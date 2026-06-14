"""portman command implementations, grouped by concern. cli.py imports the
cmd_* from here; each lives in a focused submodule."""
from ._shared import _cfg, _db, _ctx
from .lifecycle import cmd_inventory, cmd_map, cmd_snapshot, cmd_diff
from .analysis import cmd_status, cmd_gaps, cmd_batches, cmd_report, cmd_provenance
from .curation import cmd_set, cmd_alias, cmd_link, cmd_trace, cmd_export, cmd_import
from .meta import cmd_init, cmd_doctor

__all__ = [
    "cmd_inventory", "cmd_map", "cmd_snapshot", "cmd_diff",
    "cmd_status", "cmd_gaps", "cmd_batches", "cmd_report", "cmd_provenance",
    "cmd_set", "cmd_alias", "cmd_link", "cmd_trace", "cmd_export", "cmd_import",
    "cmd_init", "cmd_doctor", "_cfg", "_db", "_ctx",
]
