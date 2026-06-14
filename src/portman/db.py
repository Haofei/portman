"""SQLite mapping database.

Design choice: SQLite is the *queryable* store; it is rebuildable at any time
from (a) the upstream/target source trees and (b) the human-curated facts
exported to JSONL (`mappings/curated.jsonl`). The curated JSONL is the
git-tracked source of truth for the things humans decide — ownership, manual
links, statuses, deviations. Inventory tables are derived and need not be
committed. This keeps merge conflicts to the small curated file, not the
thousands of auto-extracted symbols.

Tables
  symbols      every upstream and target item (derived)
  mappings     upstream_sid -> target_sid + status/verification/owner (curated)
  deviations   intentional differences (curated)
  snapshots    progress totals per run, for historical trend lines (append-only)
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import fields
from pathlib import Path

from .model import Symbol, Mapping, Deviation

#: mappings table columns — the single source of truth is the Mapping dataclass,
#: so adding a field flows into INSERT/UPSERT automatically (the CREATE TABLE and
#: a migration still need the column, but the verbose SQL no longer duplicates it).
_MAPPING_COLS = tuple(f.name for f in fields(Mapping))

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
  sid TEXT, side TEXT, repo TEXT, path TEXT, qualname TEXT, kind TEXT,
  signature TEXT, lineno INT, end_lineno INT, version TEXT,
  sig_hash TEXT, body_hash TEXT, is_public INT,
  PRIMARY KEY (sid, version, side)
);
CREATE INDEX IF NOT EXISTS ix_sym_side ON symbols(side, version);
CREATE INDEX IF NOT EXISTS ix_sym_path ON symbols(side, path);

CREATE TABLE IF NOT EXISTS mappings (
  upstream_sid TEXT PRIMARY KEY,
  target_sid TEXT, status TEXT, verification TEXT, owner TEXT, reviewer TEXT,
  deviation_id TEXT, note TEXT, covers TEXT, declared_upstream_path TEXT,
  declared_upstream_version TEXT, confidence TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS deviations (
  did TEXT PRIMARY KEY, upstream_sid TEXT, title TEXT, rationale TEXT,
  kind TEXT, approved_by TEXT, upstream_version TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
  ts TEXT, upstream_version TEXT, metric TEXT, value REAL
);

CREATE TABLE IF NOT EXISTS parse_errors (
  side TEXT, version TEXT, path TEXT, error TEXT,
  PRIMARY KEY (side, version, path)
);

-- Lets `diff`/CI refer to a snapshot by a human ref (tag/branch) even though
-- symbols are stored under the resolved commit SHA.
CREATE TABLE IF NOT EXISTS version_aliases (
  ref TEXT PRIMARY KEY, sha TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        # additive column migrations for DBs created before a column existed
        cols = {r["name"] for r in self.c.execute("PRAGMA table_info(mappings)")}
        if "covers" not in cols:
            self.c.execute("ALTER TABLE mappings ADD COLUMN covers TEXT DEFAULT ''")
            self.c.commit()

    # ---- inventory (derived) -------------------------------------------------
    def replace_symbols(self, side: str, version: str, syms: list[Symbol]):
        self.c.execute("DELETE FROM symbols WHERE side=? AND version=?", (side, version))
        self.c.executemany(
            "INSERT OR REPLACE INTO symbols VALUES "
            "(:sid,:side,:repo,:path,:qualname,:kind,:signature,:lineno,"
            ":end_lineno,:version,:sig_hash,:body_hash,:is_public)",
            [{**s.to_row(), "is_public": int(s.is_public)} for s in syms])
        self.c.commit()

    def replace_parse_errors(self, side: str, version: str, errors: list[dict]):
        self.c.execute("DELETE FROM parse_errors WHERE side=? AND version=?", (side, version))
        self.c.executemany("INSERT OR REPLACE INTO parse_errors VALUES (?,?,?,?)",
                           [(side, version, e["path"], e["error"]) for e in errors])
        self.c.commit()

    def parse_errors(self, side: str, version: str) -> list[sqlite3.Row]:
        return self.c.execute("SELECT * FROM parse_errors WHERE side=? AND version=?",
                              (side, version)).fetchall()

    # ---- version aliases (ref/tag -> resolved sha) --------------------------
    def set_version_alias(self, ref: str, sha: str):
        if ref and ref != sha:
            self.c.execute("INSERT OR REPLACE INTO version_aliases VALUES (?,?)", (ref, sha))
            self.c.commit()

    def resolve_version(self, v: str) -> str:
        """Map a tag/branch ref to the sha its symbols are stored under; pass
        through an unknown value unchanged (it may already be a sha)."""
        row = self.c.execute("SELECT sha FROM version_aliases WHERE ref=?", (v,)).fetchone()
        return row["sha"] if row else v

    def has_version(self, side: str, version: str) -> bool:
        return self.c.execute(
            "SELECT 1 FROM symbols WHERE side=? AND version=? LIMIT 1",
            (side, version)).fetchone() is not None

    def duplicate_targets(self) -> list[sqlite3.Row]:
        """Target symbols claimed by more than one *primary* mapping — a 1:1
        violation. Mappings with status='aliased' are intentional secondary
        coverers (alias/wrapper) and are excluded."""
        return self.c.execute(
            "SELECT target_sid, COUNT(*) n FROM mappings "
            "WHERE target_sid IS NOT NULL AND status!='aliased' "
            "GROUP BY target_sid HAVING n>1 ORDER BY n DESC").fetchall()

    def symbols(self, side: str, version: str, kind: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM symbols WHERE side=? AND version=?"
        a: list = [side, version]
        if kind:
            q += " AND kind=?"; a.append(kind)
        return self.c.execute(q, a).fetchall()

    # ---- curated facts -------------------------------------------------------
    def upsert_mapping(self, m: Mapping):
        m.updated_at = m.updated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cols = ",".join(_MAPPING_COLS)
        placeholders = ",".join(f":{c}" for c in _MAPPING_COLS)
        updates = ",".join(f"{c}=excluded.{c}" for c in _MAPPING_COLS if c != "upstream_sid")
        self.c.execute(
            f"INSERT INTO mappings ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(upstream_sid) DO UPDATE SET {updates}",
            m.to_row())
        self.c.commit()

    def clear_config_links(self):
        """Drop config-derived ([mapping.symbol_links]) mappings so they can be
        re-applied fresh — removing a link from config removes it from the DB."""
        self.c.execute("DELETE FROM mappings WHERE confidence='config'")
        self.c.commit()

    def mapping(self, upstream_sid: str) -> sqlite3.Row | None:
        return self.c.execute("SELECT * FROM mappings WHERE upstream_sid=?",
                              (upstream_sid,)).fetchone()

    def mappings(self) -> list[sqlite3.Row]:
        return self.c.execute("SELECT * FROM mappings").fetchall()

    def mapping_index(self) -> dict[str, sqlite3.Row]:
        """All mappings keyed by upstream_sid — the read model's per-symbol lookup
        in one query (instead of N `mapping()` calls or repeated comprehensions)."""
        return {m["upstream_sid"]: m for m in self.mappings()}

    def upsert_deviation(self, d: Deviation):
        self.c.execute(
            "INSERT OR REPLACE INTO deviations VALUES "
            "(:did,:upstream_sid,:title,:rationale,:kind,:approved_by,"
            ":upstream_version,:created_at)", d.to_row())
        self.c.commit()

    def deviations(self) -> list[sqlite3.Row]:
        return self.c.execute("SELECT * FROM deviations").fetchall()

    # ---- history -------------------------------------------------------------
    def add_snapshot(self, version: str, metrics: dict[str, float]):
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.c.executemany("INSERT INTO snapshots VALUES (?,?,?,?)",
                           [(ts, version, k, v) for k, v in metrics.items()])
        self.c.commit()

    def history(self, metric: str) -> list[sqlite3.Row]:
        return self.c.execute(
            "SELECT ts,upstream_version,value FROM snapshots WHERE metric=? ORDER BY ts",
            (metric,)).fetchall()

    # ---- curated JSONL round-trip (git source of truth) ---------------------
    def export_curated(self, path: Path):
        # Only HUMAN-owned facts belong in the git source of truth. Ambiguous/auto
        # rows carry an auto-generated note, so note alone must not qualify a row.
        rows = [dict(r) for r in self.mappings()
                if r["confidence"] in ("manual", "review") or r["owner"] or r["deviation_id"]]
        with path.open("w") as f:
            f.write("# curated mappings — human-owned facts; auto links are not stored here\n")
            for r in sorted(rows, key=lambda r: r["upstream_sid"]):
                f.write(json.dumps({"type": "mapping", **r}, sort_keys=True) + "\n")
            for r in self.deviations():
                f.write(json.dumps({"type": "deviation", **dict(r)}, sort_keys=True) + "\n")

    def import_curated(self, path: Path):
        if not path.exists():
            return
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            obj = json.loads(line)
            t = obj.pop("type")
            if t == "mapping":
                self.upsert_mapping(Mapping(**obj))
            elif t == "deviation":
                self.upsert_deviation(Deviation(**obj))
