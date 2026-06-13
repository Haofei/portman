"""Python adapter — exact symbol extraction via the `ast` module.

Captures files, classes, functions, methods, module-level constants, and
TypeAlias/Assign-based types, with normalized signatures so signature drift is
detectable across upstream versions."""
from __future__ import annotations

import ast
from pathlib import Path

from ..model import Symbol, SymbolKind
from .base import Adapter, h


def _sig(node) -> str:
    """Normalize a function signature to a stable string: names + defaults arity
    + annotations (as source text). Whitespace-insensitive."""
    a = node.args
    parts: list[str] = []
    for arg in a.posonlyargs:
        parts.append(_arg(arg))
    if a.posonlyargs:
        parts.append("/")
    for arg in a.args:
        parts.append(_arg(arg))
    if a.vararg:
        parts.append("*" + _arg(a.vararg))
    elif a.kwonlyargs:
        parts.append("*")
    for arg in a.kwonlyargs:
        parts.append(_arg(arg))
    if a.kwarg:
        parts.append("**" + _arg(a.kwarg))
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    ndef = len(a.defaults) + len([d for d in a.kw_defaults if d is not None])
    return f"({', '.join(parts)}){ret} [defaults={ndef}]"


def _arg(arg) -> str:
    ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
    return f"{arg.arg}{ann}"


class PythonAdapter(Adapter):
    name = "python"
    patterns = ("*.py",)

    def extract_file(self, root, file, side, repo, version):
        rel = file.relative_to(root).as_posix()
        src = file.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src, filename=rel)
        is_test = rel.startswith("test") or "/test" in rel or Path(rel).name.startswith("test_")
        out: list[Symbol] = [Symbol(
            side=side, repo=repo, path=rel, qualname="",
            kind=SymbolKind.TEST.value if is_test else SymbolKind.FILE.value,
            version=version, body_hash=h(src))]

        def visit(node, prefix: str):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qn = f"{prefix}{child.name}"
                    kind = SymbolKind.METHOD if prefix else SymbolKind.FUNCTION
                    if is_test and child.name.startswith("test"):
                        kind = SymbolKind.TEST
                    sig = _sig(child)
                    out.append(Symbol(
                        side=side, repo=repo, path=rel, qualname=qn,
                        kind=kind.value, signature=sig, lineno=child.lineno,
                        end_lineno=getattr(child, "end_lineno", child.lineno),
                        version=version, sig_hash=h(sig),
                        body_hash=h(ast.unparse(child))))
                elif isinstance(child, ast.ClassDef):
                    qn = f"{prefix}{child.name}"
                    bases = ", ".join(ast.unparse(b) for b in child.bases)
                    out.append(Symbol(
                        side=side, repo=repo, path=rel, qualname=qn,
                        kind=SymbolKind.CLASS.value, signature=f"({bases})",
                        lineno=child.lineno,
                        end_lineno=getattr(child, "end_lineno", child.lineno),
                        version=version, sig_hash=h(bases),
                        body_hash=h(ast.unparse(child))))
                    visit(child, f"{qn}.")
                elif isinstance(child, ast.Assign) and not prefix:
                    for t in child.targets:
                        if isinstance(t, ast.Name) and t.id.isupper():
                            out.append(Symbol(
                                side=side, repo=repo, path=rel, qualname=t.id,
                                kind=SymbolKind.CONSTANT.value, lineno=child.lineno,
                                version=version, body_hash=h(ast.unparse(child))))
                elif isinstance(child, (ast.AnnAssign,)) and not prefix:
                    if isinstance(child.target, ast.Name):
                        out.append(Symbol(
                            side=side, repo=repo, path=rel,
                            qualname=child.target.id, kind=SymbolKind.TYPE.value,
                            lineno=child.lineno, version=version,
                            body_hash=h(ast.unparse(child))))

        visit(tree, "")
        return out
