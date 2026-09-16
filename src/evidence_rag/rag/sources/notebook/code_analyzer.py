"""Static Python cell analysis with explicit uncertainty."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass

NOTEBOOK_CODE_ANALYZER_VERSION = "notebook-code-analyzer-v2"

_MAGIC_RE = re.compile(r"(?m)^\s*(?:!|%%?|get_ipython\s*\()")
_DYNAMIC_CALLS = frozenset({"eval", "exec", "compile", "__import__"})
_EXTERNAL_CALLS = frozenset(
    {
        "open",
        "read_csv",
        "read_json",
        "read_parquet",
        "read_sql",
        "load",
    }
)


@dataclass(frozen=True, slots=True)
class NotebookCodeAnalysis:
    parser_available: bool
    definitions: tuple[str, ...]
    reads: tuple[str, ...]
    imports: tuple[str, ...]
    calls: tuple[str, ...]
    external_dependency_refs: tuple[str, ...]
    diagnostics: tuple[str, ...]


def _call_name(node: ast.Call) -> str | None:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _portable_ast_dump(value: object) -> str:
    """Render the stable, empty-field-free AST form used by Python 3.13.

    Python 3.12's :func:`ast.dump` includes empty list fields such as
    ``keywords=[]`` while Python 3.13 omits them by default.  External
    dependency identities are persisted evidence, so they must not inherit
    that interpreter-minor formatting difference.
    """

    if isinstance(value, ast.AST):
        fields = (
            f"{name}={_portable_ast_dump(item)}"
            for name, item in ast.iter_fields(value)
            if item is not None and item != [] and item != ()
        )
        return f"{type(value).__name__}({', '.join(fields)})"
    if isinstance(value, list):
        return f"[{', '.join(_portable_ast_dump(item) for item in value)}]"
    if isinstance(value, tuple):
        body = ", ".join(_portable_ast_dump(item) for item in value)
        return f"({body}{',' if len(value) == 1 else ''})"
    return repr(value)


def _external_ref(node: ast.Call) -> str:
    payload = _portable_ast_dump(node)
    return "external-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def analyze_notebook_code_v2(source: str, *, language: str | None) -> NotebookCodeAnalysis:
    normalized_language = (language or "").casefold()
    if normalized_language not in {"", "python", "python3"}:
        return NotebookCodeAnalysis(False, (), (), (), (), (), ("language-unsupported",))
    diagnostics: set[str] = set()
    if _MAGIC_RE.search(source):
        diagnostics.add("magic-or-shell-unresolved")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return NotebookCodeAnalysis(
            False,
            (),
            (),
            (),
            (),
            (),
            tuple(sorted({*diagnostics, "python-parse-unavailable"})),
        )
    definitions: set[str] = set()
    reads: set[str] = set()
    imports: set[str] = set()
    calls: set[str] = set()
    external: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions.add(node.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Param)):
                definitions.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                reads.add(node.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".", 1)[0]
                definitions.add(name)
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                name = alias.asname or alias.name
                definitions.add(name)
                imports.add(f"{module}.{alias.name}".strip("."))
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name:
                calls.add(name)
                if name in _DYNAMIC_CALLS:
                    diagnostics.add("dynamic-execution-unresolved")
                if name in _EXTERNAL_CALLS:
                    external.add(_external_ref(node))
    return NotebookCodeAnalysis(
        True,
        tuple(sorted(definitions)),
        tuple(sorted(reads - definitions)),
        tuple(sorted(imports)),
        tuple(sorted(calls)),
        tuple(sorted(external)),
        tuple(sorted(diagnostics)),
    )


__all__ = [
    "NOTEBOOK_CODE_ANALYZER_VERSION",
    "NotebookCodeAnalysis",
    "analyze_notebook_code_v2",
]
