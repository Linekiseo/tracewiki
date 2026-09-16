"""Read-only Notebook retrieval, execution-state projection, and context building."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any, Literal

from ..notebooks.store import NotebookStore
from ..security import redact_secrets
from .multisource import portable_locator

NOTEBOOK_ENGINE_HEADER = "X-RAG-Notebook-Engine"
NOTEBOOK_V2_VERSION = "notebook-execution-retrieval-v2"
NOTEBOOK_CONTEXT_VERSION = "notebook-execution-context-v2"

_ENGINE_VALUES = frozenset({"v1", "v2"})
_TOKEN_RE = re.compile(r"[\w@./:-]+", re.UNICODE)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ABS_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'`(])(?:/(?:Users|home|private|tmp|var|etc|opt)/|[a-z]:[\\/]|"
    r"\\\\[^\\/\s]+[\\/][^\\/\s]+)"
)
_STOP = frozenset(
    {
        "a",
        "and",
        "cell",
        "in",
        "notebook",
        "of",
        "output",
        "run",
        "show",
        "the",
        "to",
        "代码",
        "单元",
        "笔记本",
        "输出",
    }
)


class NotebookEngineOverrideError(ValueError):
    """Raised for an unsupported Notebook engine override."""


def validate_notebook_engine_override(value: str | None) -> Literal["v1", "v2"]:
    normalized = (value or "v1").strip().lower()
    if normalized not in _ENGINE_VALUES:
        raise NotebookEngineOverrideError(f"invalid {NOTEBOOK_ENGINE_HEADER}; expected v1 or v2")
    return normalized  # type: ignore[return-value]


def _digest(*values: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _tokens(value: Any) -> tuple[str, ...]:
    return tuple(
        token
        for token in (match.casefold() for match in _TOKEN_RE.findall(str(value or "")))
        if len(token) > 1 and token not in _STOP
    )


def _safe_text(value: Any, limit: int = 1_200) -> tuple[str, tuple[str, ...]]:
    raw = str(value or "")
    redacted, findings = redact_secrets(raw)
    if _ABS_PATH_RE.search(redacted):
        findings = [*findings, "absolute_path"]
        redacted = _ABS_PATH_RE.sub(" [REDACTED_PATH] ", redacted)
    cleaned = _CONTROL_RE.sub(" ", redacted).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: max(1, limit - 1)].rstrip() + "…"
    return cleaned, tuple(sorted(set(findings)))


def _task(query: str) -> str:
    lowered = query.casefold()
    for task, markers in (
        ("error", ("error", "exception", "failed", "traceback", "错误", "失败")),
        ("parameter", ("parameter", "config", "seed", "参数", "配置")),
        ("compare", ("compare", "diff", "changed", "对比", "变化")),
        ("reproduce", ("reproduce", "rerun", "复现", "重跑")),
        ("output", ("output", "result", "value", "输出", "结果")),
        ("code", ("code", "function", "variable", "代码", "函数", "变量")),
    ):
        if any(marker in lowered for marker in markers):
            return task
    return "locate"


def _python_symbols(source: str) -> tuple[set[str], set[str], str | None]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set(), set(), "parse_unavailable"
    defined: set[str] = set()
    read: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Param)):
                defined.add(node.id)
            elif isinstance(node.ctx, ast.Load):
                read.add(node.id)
    return defined, read - defined, None


class NotebookStructuredRetrieverV2:
    """Derive typed Notebook units without executing notebook content."""

    def __init__(self, store: NotebookStore) -> None:
        self.store = store

    def search(
        self,
        *,
        project_id: str,
        query: str,
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        task = _task(query)
        query_tokens = set(_tokens(query))
        allowed = set(allowed_acl_refs or ()) | {"public"}
        units: list[dict[str, Any]] = []
        run_contexts: list[dict[str, Any]] = []
        generations: set[str] = set()
        denied_runs = 0
        for summary in self.store.list_runs(project_id):
            run = self.store.get_run(str(summary["id"]))
            if run is None:
                continue
            acl_ref = str(run.get("acl_ref") or "")
            if enforce_acl and acl_ref not in allowed:
                denied_runs += 1
                continue
            generations.add(str(run.get("content_hash") or ""))
            run_units, context = self._run_units(run, task, query_tokens)
            units.extend(run_units)
            run_contexts.append(context)
        units = [item for item in units if item["score"] > 0 or not query_tokens]
        units.sort(
            key=lambda item: (
                -float(item["score"]),
                str(item["entity_type"]),
                str(item["entity_id"]),
            )
        )
        selected = units[: min(100, max(1, limit))]
        selected_ids = {str(item["entity_id"]) for item in selected}
        dependencies = [
            edge
            for context in run_contexts
            for edge in context["dependencies"]
            if edge["source"] in selected_ids or edge["target"] in selected_ids
        ][: max(20, limit * 3)]
        context = {
            "schema_version": NOTEBOOK_CONTEXT_VERSION,
            "task": task,
            "blocks": [
                {
                    "entity_id": item["entity_id"],
                    "entity_type": item["entity_type"],
                    "locator": item["locator"],
                    "execution_state": item["execution_state"],
                    "roles": item["roles"],
                    "content_digest": item["content_digest"],
                    "text": item["snippet"],
                }
                for item in selected
            ],
            "dependencies": dependencies,
            "comparisons": self._comparisons(run_contexts) if task == "compare" else [],
            "stale_runs": [context["run_id"] for context in run_contexts if context["stale"]],
            "missing": [] if selected else ["authorized_notebook_evidence"],
            "reasoning_included": False,
        }
        return {
            "results": selected,
            "context": context,
            "trace": {
                "engine": NOTEBOOK_V2_VERSION,
                "task": task,
                "candidate_count": len(units),
                "selected_count": len(selected),
                "denied_runs": denied_runs,
                "index_generation": sorted(value for value in generations if value),
                "release": {
                    "decision": "HOLD_DEFAULT_V1",
                    "default_engine": "v1",
                    "quality_qualified": False,
                    "rollback": f"omit {NOTEBOOK_ENGINE_HEADER} or set it to v1",
                },
                "reasoning_included": False,
            },
        }

    def _run_units(
        self, run: dict[str, Any], task: str, query_tokens: set[str]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        run_id = str(run["id"])
        template_id = str(run["template_id"])
        source_uri = portable_locator(run.get("source_uri"), template_id)
        units: list[dict[str, Any]] = []
        dependencies: list[dict[str, Any]] = []
        symbols: dict[str, str] = {}
        previous_execution: int | None = None
        stale = False
        parameters = run.get("parameters") or {}
        run_text = " ".join(
            (
                str(run.get("template_name") or ""),
                str(run.get("version") or ""),
                str(run.get("status") or ""),
                json.dumps(parameters, sort_keys=True, ensure_ascii=False),
            )
        )
        units.append(
            self._unit(
                entity_id=run_id,
                entity_type="NotebookRun",
                title=str(run.get("template_name") or run_id),
                text=run_text,
                locator=f"notebook://{template_id}/run/{run_id}",
                version=str(run.get("version") or ""),
                status=str(run.get("status") or "unknown"),
                execution_state="completed"
                if str(run.get("status")) == "completed"
                else "incomplete",
                roles=("template", "run", "parameter") if parameters else ("template", "run"),
                task=task,
                query_tokens=query_tokens,
                metadata={"template_id": template_id, "source_uri": source_uri},
            )
        )
        for key, value in sorted(parameters.items()):
            parameter_id = f"{run_id}/parameter/{_digest(str(key))[-16:]}"
            units.append(
                self._unit(
                    entity_id=parameter_id,
                    entity_type="NotebookParameter",
                    title=str(key),
                    text=f"{key}={value}",
                    locator=f"notebook://{template_id}/run/{run_id}/parameter/{key}",
                    version=str(run.get("version") or ""),
                    status="observed",
                    execution_state="observed",
                    roles=("parameter",),
                    task=task,
                    query_tokens=query_tokens,
                    metadata={"run_id": run_id, "parameter": str(key)},
                )
            )
        for cell in run.get("cells", []):
            cell_id = str(cell["id"])
            source = str(cell.get("source") or "")
            count = cell.get("execution_count")
            outputs = list(cell.get("outputs") or [])
            if isinstance(count, int):
                if previous_execution is not None and count < previous_execution:
                    stale = True
                previous_execution = count
            if cell.get("cell_type") == "code" and count is not None and not outputs:
                stale = True
            defined, reads, parse_issue = (
                _python_symbols(source)
                if str(run.get("language") or "").casefold() in {"python", "python3", ""}
                and cell.get("cell_type") == "code"
                else (set(), set(), None)
            )
            for name in sorted(reads):
                parent = symbols.get(name)
                if parent:
                    dependencies.append(
                        {
                            "source": parent,
                            "target": cell_id,
                            "symbol": name,
                            "kind": "observable_def_use",
                        }
                    )
            for name in defined:
                symbols[name] = cell_id
            execution_state = (
                "not_executed"
                if count is None
                else "error"
                if any(output.get("output_type") == "error" for output in outputs)
                else "executed"
            )
            units.append(
                self._unit(
                    entity_id=cell_id,
                    entity_type="NotebookCell",
                    title=f"Cell {cell.get('ordinal', 0)}",
                    text=source,
                    locator=f"notebook://{template_id}/run/{run_id}/cell/{cell.get('ordinal', 0)}",
                    version=str(run.get("version") or ""),
                    status=execution_state,
                    execution_state=execution_state,
                    roles=("code",) if cell.get("cell_type") == "code" else ("narrative",),
                    task=task,
                    query_tokens=query_tokens,
                    metadata={
                        "run_id": run_id,
                        "ordinal": cell.get("ordinal"),
                        "source_hash": cell.get("source_hash"),
                        "defined": sorted(defined),
                        "read": sorted(reads),
                        "diagnostic": parse_issue,
                    },
                )
            )
            for output in outputs:
                output_id = str(output["id"])
                is_error = output.get("output_type") == "error"
                text = " ".join(
                    str(value or "")
                    for value in (
                        output.get("error_name"),
                        output.get("error_value"),
                        output.get("text_content"),
                    )
                ).strip()
                units.append(
                    self._unit(
                        entity_id=output_id,
                        entity_type="NotebookError" if is_error else "NotebookOutput",
                        title=str(output.get("error_name") or f"Output {output.get('ordinal', 0)}"),
                        text=text,
                        locator=(
                            f"notebook://{template_id}/run/{run_id}/cell/"
                            f"{cell.get('ordinal', 0)}/output/{output.get('ordinal', 0)}"
                        ),
                        version=str(run.get("version") or ""),
                        status="failed" if is_error else "observed",
                        execution_state="error" if is_error else "observed",
                        roles=("error",) if is_error else ("output",),
                        task=task,
                        query_tokens=query_tokens,
                        metadata={
                            "run_id": run_id,
                            "cell_id": cell_id,
                            "data_types": output.get("data_types") or [],
                        },
                    )
                )
        return units, {
            "run_id": run_id,
            "template_id": template_id,
            "version": str(run.get("version") or ""),
            "content_hash": str(run.get("content_hash") or ""),
            "cells": {
                str(cell["id"]): str(cell.get("source_hash") or "") for cell in run.get("cells", [])
            },
            "dependencies": dependencies,
            "stale": stale,
        }

    @staticmethod
    def _unit(
        *,
        entity_id: str,
        entity_type: str,
        title: str,
        text: str,
        locator: str,
        version: str,
        status: str,
        execution_state: str,
        roles: tuple[str, ...],
        task: str,
        query_tokens: set[str],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        safe_title, title_findings = _safe_text(title, 240)
        safe_text, text_findings = _safe_text(text)
        unit_tokens = set(_tokens(f"{safe_title} {safe_text} {entity_type} {' '.join(roles)}"))
        overlap = len(query_tokens & unit_tokens)
        coverage = overlap / max(1, len(query_tokens))
        exact = int(bool(query_tokens) and " ".join(sorted(query_tokens)) in safe_text.casefold())
        task_boost = 0.18 if task in roles else 0.0
        score = min(
            1.0, 0.08 + coverage * 0.62 + min(overlap, 4) * 0.04 + exact * 0.08 + task_boost
        )
        content_digest = _digest(
            entity_id, version, safe_text, json.dumps(metadata, sort_keys=True)
        )
        return {
            "entity_id": entity_id,
            "source": "notebook",
            "entity_type": entity_type,
            "title": safe_title,
            "subtitle": f"{execution_state} · {version or 'unversioned'}",
            "snippet": safe_text,
            "locator": portable_locator(locator, entity_id),
            "version": version or None,
            "status": status,
            "score": round(score, 6),
            "channels": sorted({"notebook_exact" if exact else "notebook_lexical", *roles}),
            "execution_state": execution_state,
            "roles": list(roles),
            "content_digest": content_digest,
            "redactions": sorted(set(title_findings) | set(text_findings)),
            "metadata": metadata,
        }

    @staticmethod
    def _comparisons(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_template: dict[str, list[dict[str, Any]]] = {}
        for context in contexts:
            by_template.setdefault(context["template_id"], []).append(context)
        comparisons: list[dict[str, Any]] = []
        for template_id, runs in sorted(by_template.items()):
            if len(runs) < 2:
                continue
            baseline, candidate = sorted(runs, key=lambda item: (item["version"], item["run_id"]))[
                -2:
            ]
            shared = set(baseline["cells"]) & set(candidate["cells"])
            changed = sorted(
                cell_id
                for cell_id in shared
                if baseline["cells"][cell_id] != candidate["cells"][cell_id]
            )
            comparisons.append(
                {
                    "template_id": template_id,
                    "baseline_run_id": baseline["run_id"],
                    "candidate_run_id": candidate["run_id"],
                    "changed_cell_ids": changed,
                    "state": "AVAILABLE" if shared else "UNAVAILABLE",
                }
            )
        return comparisons
