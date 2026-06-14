"""Core data model for the port-management framework.

Everything is language-agnostic. Upstream and target items are both represented
as `Symbol` records; the relationship between them is a `Mapping`. A `Deviation`
documents an intentional difference.

These dataclasses are the single in-memory representation; persistence lives in
`db.py` and the on-disk schema mirrors these fields 1:1.
"""
from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional


class SymbolKind(str, enum.Enum):
    """The granularity levels we track. Files are symbols too, so a single table
    can answer both 'which files are ported' and 'which methods are ported'."""
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    CONSTANT = "constant"
    TYPE = "type"
    TEST = "test"


class Side(str, enum.Enum):
    """Which tree a symbol/inventory belongs to."""
    UPSTREAM = "upstream"
    TARGET = "target"


class Status(str, enum.Enum):
    """Implementation status of a mapped item. Ordered worst -> best so progress
    can be scored numerically (see WEIGHT)."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    PARTIAL = "partial"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    DIVERGED = "diverged"
    DEPRECATED = "deprecated"
    ALIASED = "aliased"        # covered by another symbol's target (alias/wrapper)


# Numeric weight used for progress scoring. DIVERGED/DEPRECATED/ALIASED are
# intentional end-states and are excluded from the "to-do" denominator.
WEIGHT = {
    Status.NOT_STARTED: 0.0,
    Status.IN_PROGRESS: 0.25,
    Status.PARTIAL: 0.5,
    Status.IMPLEMENTED: 0.85,
    Status.VERIFIED: 1.0,
    Status.DIVERGED: 1.0,      # intentional + documented => counts as done
    Status.DEPRECATED: 1.0,    # intentionally not ported => counts as done
    Status.ALIASED: 1.0,       # covered via an alias/wrapper => counts as done
}


class Verification(str, enum.Enum):
    """Independent axis from Status: *how* we know the behavior matches."""
    NONE = "none"
    SIGNATURE = "signature"        # API shape compared
    GOLDEN = "golden"              # golden/snapshot outputs match
    DIFFERENTIAL = "differential"  # ran both, compared outputs
    FUZZ = "fuzz"                  # property/fuzz tested against upstream
    PORTED_TESTS = "ported_tests"  # upstream test suite ported and green


class Confidence(str, enum.Enum):
    """How a mapping link was established — independent of Status/Verification."""
    AUTO = "auto"            # machine-proposed by the matcher
    MANUAL = "manual"        # a human set/forced it (curated, persisted to JSONL)
    REVIEW = "review"        # a second human signed off
    CONFIG = "config"        # derived from [mapping.symbol_links] (re-applied each map)
    AMBIGUOUS = "ambiguous"  # only a name-collision match; not a real link


# confidence levels that are human/forced decisions (locked against the auto-mapper
# and, except CONFIG, exported to curated.jsonl)
LOCKED_CONFIDENCE = (Confidence.MANUAL.value, Confidence.REVIEW.value, Confidence.CONFIG.value)


def symbol_id(repo: str, path: str, qualname: str, kind: str) -> str:
    """Stable identity for a symbol across versions. Deliberately excludes line
    numbers so a symbol keeps its id when it moves within a file."""
    raw = f"{repo}::{path}::{qualname}::{kind}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


@dataclass
class Symbol:
    """One upstream OR target item at any granularity."""
    side: str                      # "upstream" | "target"
    repo: str                      # logical repo name, e.g. "tinygrad"
    path: str                      # repo-relative path, e.g. "tinygrad/dtype.py"
    qualname: str                  # dotted name, e.g. "DType.itemsize" ("" for files)
    kind: str                      # SymbolKind value
    signature: str = ""            # normalized signature, "" if n/a
    lineno: int = 0
    end_lineno: int = 0
    version: str = ""              # upstream commit/release this was extracted at
    sig_hash: str = ""             # hash of normalized signature (cheap diff)
    body_hash: str = ""            # hash of source text (behavior-change signal)
    is_public: bool = True         # leading underscore => internal
    sid: str = ""

    def __post_init__(self):
        if not self.sid:
            self.sid = symbol_id(self.repo, self.path, self.qualname, self.kind)
        if self.qualname:
            leaf = self.qualname.rsplit(".", 1)[-1]
            self.is_public = not leaf.startswith("_")

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class Mapping:
    """Links an upstream symbol id to a target symbol id (or none yet)."""
    upstream_sid: str
    target_sid: Optional[str] = None
    status: str = Status.NOT_STARTED.value
    verification: str = Verification.NONE.value
    owner: str = ""
    reviewer: str = ""
    deviation_id: Optional[str] = None
    note: str = ""
    # when status == aliased: the *primary* upstream_sid whose target implementation
    # also covers this symbol (an alias / private-forwarder / public wrapper).
    covers: str = ""
    # provenance the target file declared about itself, for audit
    declared_upstream_path: str = ""
    declared_upstream_version: str = ""
    confidence: str = Confidence.AUTO.value
    updated_at: str = ""

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class Deviation:
    """An intentional, documented difference from upstream."""
    did: str
    upstream_sid: str
    title: str
    rationale: str
    kind: str = "behavioral"       # behavioral|api|omission|addition|perf|platform
    approved_by: str = ""
    upstream_version: str = ""
    created_at: str = ""

    def to_row(self) -> dict:
        return asdict(self)
