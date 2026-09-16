"""Typed, non-executing parameter parser for tagged Notebook cells."""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass
from typing import Any

from .contracts import NotebookParameterType, canonical_json_bytes, canonical_sha256

NOTEBOOK_PARAMETER_PARSER_VERSION = "notebook-parameter-parser-v2"


@dataclass(frozen=True, slots=True)
class ParsedNotebookParameter:
    name: str
    value_type: NotebookParameterType
    canonical_value: str
    value_sha256: str


@dataclass(frozen=True, slots=True)
class NotebookParameterParseResult:
    parameters: tuple[ParsedNotebookParameter, ...]
    diagnostics: tuple[str, ...]


def _literal(value: Any) -> tuple[NotebookParameterType, str]:
    if value is None:
        kind = NotebookParameterType.NULL
    elif type(value) is bool:
        kind = NotebookParameterType.BOOLEAN
    elif type(value) is int:
        kind = NotebookParameterType.INTEGER
    elif type(value) is float:
        if not math.isfinite(value):
            return NotebookParameterType.UNRESOLVED, "<unresolved>"
        kind = NotebookParameterType.FLOAT
    elif isinstance(value, str):
        kind = NotebookParameterType.STRING
    elif isinstance(value, list):
        kind = NotebookParameterType.LIST
    elif isinstance(value, dict) and all(isinstance(key, str) for key in value):
        kind = NotebookParameterType.OBJECT
    else:
        return NotebookParameterType.UNRESOLVED, "<unresolved>"
    return kind, canonical_json_bytes(value).decode("utf-8")


def parse_notebook_parameters_v2(source: str) -> NotebookParameterParseResult:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return NotebookParameterParseResult((), ("parameter-syntax-unavailable",))
    parsed: list[ParsedNotebookParameter] = []
    diagnostics: set[str] = set()
    observed_names: set[str] = set()
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            diagnostics.add("parameter-target-unsupported")
            continue
        name = targets[0].id
        if name in observed_names:
            diagnostics.add("parameter-duplicate-name")
            continue
        observed_names.add(name)
        expression = statement.value
        if expression is None:
            value_type, canonical_value = NotebookParameterType.UNRESOLVED, "<unresolved>"
        else:
            try:
                literal = ast.literal_eval(expression)
            except (ValueError, TypeError):
                value_type, canonical_value = (
                    NotebookParameterType.UNRESOLVED,
                    "<unresolved>",
                )
            else:
                value_type, canonical_value = _literal(literal)
        value_sha256 = canonical_sha256(
            {
                "parser_version": NOTEBOOK_PARAMETER_PARSER_VERSION,
                "name": name,
                "type": value_type,
                "value": canonical_value,
            }
        )
        parsed.append(
            ParsedNotebookParameter(
                name=name,
                value_type=value_type,
                canonical_value=canonical_value,
                value_sha256=value_sha256,
            )
        )
    return NotebookParameterParseResult(tuple(parsed), tuple(sorted(diagnostics)))


__all__ = [
    "NOTEBOOK_PARAMETER_PARSER_VERSION",
    "NotebookParameterParseResult",
    "ParsedNotebookParameter",
    "parse_notebook_parameters_v2",
]
