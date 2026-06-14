"""portman CLI — the single entry point for the whole lifecycle.

  portman inventory                 extract upstream + target into the DB
  portman map                       auto-link via provenance + name matching
  portman status [--json]           print the coverage summary
  portman gaps [--public] [--explain] [--reason R]   ranked port gaps + reasons
  portman batches [--public] [--out F]  group gaps into port batches / manifest
  portman report                    write reports/dashboard.md + coverage.json
  portman provenance lint           list target files missing/with-legacy headers
  portman snapshot --version REF    re-extract upstream at a git ref into the DB
  portman diff OLD NEW              upstream change report between two snapshots
  portman set STATUS --upstream ... manually set a mapping's status/owner (curated)
  portman alias A --of B            mark upstream A as covered by B's target (alias)
  portman link UP TARGET            force a link for names the matcher can't bridge
  portman trace PATH[::QUALNAME]    show the full provenance/verification record
  portman export                    write curated facts to mappings/curated.jsonl
  portman import                    load curated facts from mappings/curated.jsonl
  portman init --upstream-root ...  generate a portman.toml for a new port
  portman doctor                    validate the setup before trusting numbers
"""
from __future__ import annotations

import argparse
import sys

from .model import Status
from .commands import (
    cmd_inventory, cmd_map, cmd_status, cmd_gaps, cmd_batches, cmd_report,
    cmd_provenance, cmd_snapshot, cmd_diff, cmd_set, cmd_alias, cmd_link,
    cmd_trace, cmd_export, cmd_import, cmd_doctor, cmd_init)


def build_parser():
    p = argparse.ArgumentParser(prog="portman", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="portman.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inventory"); s.add_argument("--strict", action="store_true",
        help="fail if any file fails to parse"); s.set_defaults(func=cmd_inventory)
    sub.add_parser("map").set_defaults(func=cmd_map)
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    s.add_argument("--save", metavar="FILE", help="write current status JSON to FILE")
    s.add_argument("--fail-on-regression", dest="fail_on_regression", metavar="FILE",
                   help="exit nonzero if coverage dropped vs a saved status FILE")
    s.set_defaults(func=cmd_status)
    s = sub.add_parser("gaps"); s.add_argument("--limit", type=int, default=40)
    s.add_argument("--public", action="store_true")
    s.add_argument("--explain", action="store_true", help="why each gap isn't linked")
    s.add_argument("--reason", default="", help="filter to one gap reason")
    s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_gaps)
    s = sub.add_parser("batches"); s.add_argument("--limit", type=int, default=15)
    s.add_argument("--public", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("--out", metavar="FILE", help="write a machine-readable batch manifest")
    s.set_defaults(func=cmd_batches)
    sub.add_parser("report").set_defaults(func=cmd_report)
    s = sub.add_parser("provenance"); s.add_argument("action", choices=["lint"], nargs="?", default="lint")
    s.add_argument("--limit", type=int, default=30); s.set_defaults(func=cmd_provenance)
    s = sub.add_parser("snapshot"); s.add_argument("--version", required=True); s.set_defaults(func=cmd_snapshot)
    s = sub.add_parser("diff"); s.add_argument("old"); s.add_argument("new")
    s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_diff)
    # `aliased` is deliberately excluded: it requires a `covers` target, which set
    # cannot supply. Use the dedicated `portman alias A --of B` command instead.
    set_statuses = [x.value for x in Status if x is not Status.ALIASED]
    s = sub.add_parser("set", help="set a mapping's status (use `alias` for aliased)")
    s.add_argument("status", choices=set_statuses)
    s.add_argument("--upstream", required=True); s.add_argument("--kind", default="function")
    s.add_argument("--verification", default=""); s.add_argument("--owner", default="")
    s.add_argument("--deviation", default=""); s.add_argument("--note", default=""); s.set_defaults(func=cmd_set)
    s = sub.add_parser("alias"); s.add_argument("alias")
    s.add_argument("--of", required=True, help="primary upstream symbol path::Qualname")
    s.add_argument("--kind", default="method"); s.add_argument("--of-kind", dest="of_kind", default="")
    s.add_argument("--note", default=""); s.set_defaults(func=cmd_alias)
    s = sub.add_parser("link"); s.add_argument("upstream"); s.add_argument("target")
    s.add_argument("--note", default=""); s.set_defaults(func=cmd_link)
    s = sub.add_parser("trace"); s.add_argument("target"); s.set_defaults(func=cmd_trace)
    sub.add_parser("export").set_defaults(func=cmd_export)
    sub.add_parser("import").set_defaults(func=cmd_import)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    s = sub.add_parser("init")
    s.add_argument("--project", default="myport")
    s.add_argument("--upstream-root", dest="upstream_root", required=True)
    s.add_argument("--target-root", dest="target_root", required=True)
    s.add_argument("--upstream-adapter", dest="upstream_adapter", default="python")
    s.add_argument("--target-adapter", dest="target_adapter", default="rss")
    s.add_argument("--upstream-repo", dest="upstream_repo", default="upstream")
    s.add_argument("--target-repo", dest="target_repo", default="target")
    s.add_argument("--upstream-version", dest="upstream_version", default="HEAD")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
