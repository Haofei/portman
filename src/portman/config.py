"""Load portman.toml. TOML (stdlib tomllib) is used instead of YAML so the
framework has zero third-party dependencies."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SideCfg:
    repo: str
    root: Path          # absolute path to the source tree to scan
    adapter: str
    version: str = ""   # git ref / release tag of the pinned baseline
    exclude: tuple[str, ...] = ()


@dataclass
class Config:
    project: str
    upstream: SideCfg
    target: SideCfg
    db_path: Path
    reports_dir: Path
    generic_adapters: dict = field(default_factory=dict)
    # foundational-path bonuses for gap risk ranking — config, not hard-coded,
    # so the framework is library-agnostic.
    risk_high: tuple[str, ...] = ()
    risk_medium: tuple[str, ...] = ()
    root: Path = Path(".")

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = tomllib.loads(path.read_text())
        root = path.parent.resolve()

        def side(d: dict) -> SideCfg:
            return SideCfg(
                repo=d["repo"],
                root=(root / d["root"]).resolve() if not Path(d["root"]).is_absolute()
                     else Path(d["root"]),
                adapter=d["adapter"],
                version=d.get("version", ""),
                exclude=tuple(d.get("exclude", [])))

        risk = data.get("risk", {})
        return cls(
            project=data["project"],
            upstream=side(data["upstream"]),
            target=side(data["target"]),
            db_path=(root / data.get("db", "mappings/port.db")),
            reports_dir=(root / data.get("reports", "reports")),
            generic_adapters=data.get("adapters", {}),
            risk_high=tuple(risk.get("high", [])),
            risk_medium=tuple(risk.get("medium", [])),
            root=root)
