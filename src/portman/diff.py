"""Upstream change tracking + API comparison.

`upstream_diff` compares two extracted upstream inventories (old baseline vs new
release) and classifies every change: added / removed / moved / signature-changed
/ body-changed. It works purely on stored Symbol rows so it is language-agnostic.

`upgrade_report` joins that diff with the current mapping state to answer
'what does this new upstream release mean for our port?' — i.e. new gaps,
ported symbols whose upstream signature changed (re-verify), and symbols upstream
removed that we still carry (candidate deprecations)."""
from __future__ import annotations

from .db import DB


def _key(s) -> tuple:
    # identity that survives line moves but not renames/path moves
    return (s["path"], s["qualname"], s["kind"])


def upstream_diff(db: DB, old_v: str, new_v: str) -> dict:
    old = {_key(s): s for s in db.symbols("upstream", old_v)}
    new = {_key(s): s for s in db.symbols("upstream", new_v)}
    old_keys, new_keys = set(old), set(new)

    added = [k for k in new_keys - old_keys]
    removed = [k for k in old_keys - new_keys]

    # detect moves: same (qualname, kind) different path, where path side is unique
    def by_qual(keys, src):
        d = {}
        for k in keys:
            d.setdefault((k[1], k[2]), []).append(k)
        return d
    add_q = by_qual(added, new)
    rem_q = by_qual(removed, old)
    moved = []
    for q, aks in list(add_q.items()):
        if len(aks) == 1 and q in rem_q and len(rem_q[q]) == 1:
            moved.append({"from": rem_q[q][0][0], "to": aks[0][0], "qualname": q[0], "kind": q[1]})
            added.remove(aks[0]); removed.remove(rem_q[q][0])

    sig_changed, body_changed = [], []
    for k in old_keys & new_keys:
        o, n = old[k], new[k]
        if o["sig_hash"] != n["sig_hash"] and (o["sig_hash"] or n["sig_hash"]):
            sig_changed.append({"path": k[0], "qualname": k[1], "kind": k[2],
                                "old": o["signature"], "new": n["signature"]})
        elif o["body_hash"] != n["body_hash"]:
            body_changed.append({"path": k[0], "qualname": k[1], "kind": k[2]})

    def fmt(keys):
        return [{"path": k[0], "qualname": k[1] or "<file>", "kind": k[2]} for k in keys]

    return {
        "old_version": old_v, "new_version": new_v,
        "added": fmt(added), "removed": fmt(removed), "moved": moved,
        "signature_changed": sig_changed, "body_changed": body_changed,
        "summary": {"added": len(added), "removed": len(removed), "moved": len(moved),
                    "signature_changed": len(sig_changed), "body_changed": len(body_changed)},
    }


def upgrade_report(db: DB, old_v: str, new_v: str) -> dict:
    """Cross the upstream diff with our current port state."""
    d = upstream_diff(db, old_v, new_v)
    old = {_key(s): s for s in db.symbols("upstream", old_v)}

    # ported symbols whose upstream signature/body changed => need re-verification
    needs_reverify = []
    for change in d["signature_changed"] + d["body_changed"]:
        k = (change["path"], change["qualname"] if change["qualname"] != "<file>" else "", change["kind"])
        s = old.get(k)
        if not s:
            continue
        m = db.mapping(s["sid"])
        if m and m["status"] in ("implemented", "verified"):
            needs_reverify.append({**change, "current_status": m["status"],
                                   "owner": m["owner"]})

    # removed-upstream symbols we still implement => candidate deprecations
    candidate_deprecations = []
    for r in d["removed"]:
        k = (r["path"], r["qualname"] if r["qualname"] != "<file>" else "", r["kind"])
        s = old.get(k)
        if s:
            m = db.mapping(s["sid"])
            if m and m["target_sid"]:
                candidate_deprecations.append(r)

    d["new_work"] = d["added"]                      # brand-new upstream surface to port
    d["needs_reverify"] = needs_reverify
    d["candidate_deprecations"] = candidate_deprecations
    return d
