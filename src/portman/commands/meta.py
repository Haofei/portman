"""Meta commands: init (generate config) and doctor (health checks)."""
from __future__ import annotations

from pathlib import Path

from ._shared import _cfg
from .. import health


TOML_TEMPLATE = '''\
project = "{project}"
db = "mappings/port.db"
reports = "reports"

[upstream]
repo = "{up_repo}"
root = "{up_root}"
adapter = "{up_adapter}"
version = "{up_version}"
exclude = ["__pycache__"]

[target]
repo = "{tg_repo}"
root = "{tg_root}"
adapter = "{tg_adapter}"
version = "working"
exclude = ["__pycache__"]

# Foundational-path bonuses for gap risk ranking (library-specific, optional).
[risk]
high = []
medium = []
'''


def cmd_init(args):
    out = Path(args.config)
    if out.exists() and not args.force:
        print(f"refusing to overwrite existing {out} (use --force)"); return 1
    out.write_text(TOML_TEMPLATE.format(
        project=args.project, up_repo=args.upstream_repo, up_root=args.upstream_root,
        up_adapter=args.upstream_adapter, up_version=args.upstream_version,
        tg_repo=args.target_repo, tg_root=args.target_root, tg_adapter=args.target_adapter))
    print(f"wrote {out}. Next: portman --config {out} doctor && make all")


def cmd_doctor(args):
    """Validate that the setup is sane before trusting any numbers."""
    cfg = _cfg(args)
    checks = health.run_checks(cfg)
    fails = sum(1 for lv, *_ in checks if lv == health.FAIL)
    for lv, name, detail in checks:
        print(f"  {health.MARK[lv]} [{lv}] {name}" + (f" — {detail}" if detail else ""))
    print(f"\n{len(checks)} checks, {fails} failing")
    return 1 if fails else 0
