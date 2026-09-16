"""Read-only Codex episode, temporal rerank, and context projection.

The V2 path deliberately wraps the current production retriever.  It adds no
tables, writers, background jobs, or default switch; omitting the opt-in header
continues to execute the V1 path exactly once.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Literal

from ..codex_retrieval import CodexHybridRetriever
from ..models import CodexSearchRequest
from ..security import redact_secrets
from .multisource import portable_locator

CODEX_ENGINE_HEADER = "X-RAG-Codex-Engine"
CODEX_V2_VERSION = "codex-episode-temporal-v2"
CODEX_EPISODE_VERSION = "codex-episode-v2"
CODEX_CONTEXT_VERSION = "codex-timeline-context-v2"

_ENGINE_VALUES = frozenset({"v1", "v2"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_POSIX_ABSOLUTE = re.compile(
    r"(?<![\w:])/(?:Users|home|private|tmp|var|etc|opt)/[^\s\"'`<>]{1,500}",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?<![\w])[a-z]:[\\/][^\s\"'`<>]{1,500}")
_TASK_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("validation", ("test", "pytest", "validation", "validate", "passed", "failed", "验证")),
    ("failure", ("failure", "failed", "error", "timeout", "rejected", "失败", "错误")),
    ("change", ("change", "patch", "implement", "modified", "file", "修改", "实现")),
    ("rationale", ("why", "decision", "rationale", "choose", "tradeoff", "为何", "决策")),
    ("history", ("history", "previous", "archived", "old attempt", "compare", "历史", "对比")),
)
_TASK_TYPE_BOOSTS: dict[str, dict[str, float]] = {
    "validation": {
        "ValidationResult": 0.28,
        "CommandExecution": 0.22,
        "ToolResult": 0.12,
    },
    "failure": {
        "ValidationResult": 0.24,
        "ToolResult": 0.20,
        "CommandExecution": 0.18,
        "Patch": 0.08,
    },
    "change": {
        "FileChange": 0.28,
        "Patch": 0.26,
        "ValidationResult": 0.12,
        "CommandExecution": 0.08,
    },
    "rationale": {
        "UserGoal": 0.20,
        "AgentMessage": 0.16,
        "Plan": 0.10,
        "FileChange": 0.06,
    },
    "history": {
        "DevelopmentEpisode": 0.20,
        "UserGoal": 0.12,
        "FileChange": 0.10,
        "ValidationResult": 0.08,
    },
    "search": {
        "DevelopmentEpisode": 0.10,
        "UserGoal": 0.08,
        "FileChange": 0.08,
        "ValidationResult": 0.08,
    },
}
_RELEASE = {
    "stage": "OPT_IN",
    "default_engine": "v1",
    "decision": "HOLD_DEFAULT_V1",
    "quality_qualified": False,
    "schema_migration": False,
    "old_treatment_retry": False,
    "rollback": "omit X-RAG-Codex-Engine or set it to v1",
}


class CodexEngineOverrideError(ValueError):
    """Raised when a request asks for an unknown Codex engine."""


def validate_codex_engine_override(value: str | None) -> Literal["v1", "v2"]:
    normalized = (value or "v1").strip().lower()
    if normalized not in _ENGINE_VALUES:
        raise CodexEngineOverrideError(f"invalid {CODEX_ENGINE_HEADER}; expected v1 or v2")
    return normalized  # type: ignore[return-value]


def _digest(*values: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _task(query: str) -> str:
    normalized = " ".join(query.casefold().split())
    for task, markers in _TASK_MARKERS:
        if any(marker in normalized for marker in markers):
            return task
    return "search"


def _safe_text(value: Any, limit: int = 900) -> tuple[str, tuple[str, ...], bool]:
    redacted, findings = redact_secrets(str(value or ""))
    redacted = _POSIX_ABSOLUTE.sub("[REDACTED:absolute_path]", redacted)
    redacted = _WINDOWS_ABSOLUTE.sub("[REDACTED:absolute_path]", redacted)
    cleaned = " ".join(_CONTROL.sub(" ", redacted).split())
    if len(cleaned) <= limit:
        return cleaned, tuple(sorted(findings)), False
    return cleaned[: limit - 1].rstrip() + "…", tuple(sorted(findings)), True


def _nested_exit_codes(value: Any, *, depth: int = 0) -> tuple[int, ...]:
    if depth > 4:
        return ()
    if isinstance(value, dict):
        found: list[int] = []
        for key, child in list(value.items())[:80]:
            normalized = str(key).casefold()
            if normalized in {"exit", "exit_code", "returncode", "return_code"}:
                if isinstance(child, int) and not isinstance(child, bool):
                    found.append(child)
                continue
            found.extend(_nested_exit_codes(child, depth=depth + 1))
        return tuple(found)
    if isinstance(value, (list, tuple)):
        return tuple(
            code
            for child in list(value)[:80]
            for code in _nested_exit_codes(child, depth=depth + 1)
        )
    return ()


def _event_status(item: dict[str, Any]) -> str:
    explicit = str(item.get("status") or "").strip().casefold()
    exits = set(_nested_exit_codes(item.get("metadata") or {}))
    if len(exits) == 1:
        return "passed" if next(iter(exits)) == 0 else "failed"
    if len(exits) > 1:
        return "ambiguous"
    if explicit in {
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "running",
        "proposed",
        "applied",
        "passed",
    }:
        return explicit
    return "observed" if item.get("timestamp") else "unknown"


def _evidence_role(item_type: str) -> str:
    return {
        "UserGoal": "goal",
        "Plan": "proposal",
        "CommandExecution": "action",
        "ToolCall": "action",
        "ToolResult": "result",
        "Patch": "change",
        "FileChange": "change",
        "ValidationResult": "validation",
        "AgentMessage": "reported_outcome",
        "DevelopmentEpisode": "episode",
    }.get(item_type, "context")


def _negative_reasons(item: dict[str, Any], *, task: str, truncated: bool) -> tuple[str, ...]:
    item_type = str(item.get("item_type") or "")
    status = _event_status(item)
    metadata = item.get("metadata") or {}
    metadata_text = str(metadata).casefold()
    reasons: list[str] = []
    if item_type == "AgentMessage":
        reasons.append("claim_only")
    if item_type == "Plan" and task not in {"rationale", "history"}:
        reasons.append("plan_only")
    if status in {"failed", "timeout", "cancelled"} and task not in {"failure", "history"}:
        reasons.append("non_current_outcome")
    if str(item.get("thread_status") or "").casefold() in {"failed", "archived"}:
        reasons.append("historical_or_failed_thread")
    if truncated or "truncat" in metadata_text:
        reasons.append("truncated_evidence")
    if "subagent" in metadata_text:
        reasons.append("supporting_subagent")
    return tuple(dict.fromkeys(reasons))


class CodexTemporalRetrieverV2:
    """Task-aware projection over one bounded execution of the V1 retriever."""

    def __init__(self, legacy: CodexHybridRetriever) -> None:
        self.legacy = legacy

    def search(self, request: CodexSearchRequest) -> dict[str, Any]:
        expanded_limit = min(50, max(request.limit * 4, 20))
        legacy_request = request.model_copy(update={"limit": expanded_limit, "include_edges": True})
        legacy = self.legacy.search(legacy_request)
        task = _task(request.query)
        candidates: list[dict[str, Any]] = []
        raw_results = list(legacy.get("results") or [])
        recency_order = {
            entity_id: rank
            for rank, entity_id in enumerate(
                str(item["entity_id"])
                for item in sorted(
                    raw_results,
                    key=lambda value: (
                        str(value.get("timestamp") or ""),
                        str(value.get("entity_id") or ""),
                    ),
                    reverse=True,
                )
            )
        }
        for item in raw_results:
            item_type = str(item.get("item_type") or "")
            if "reasoning" in item_type.casefold():
                continue
            snippet, redactions, truncated = _safe_text(item.get("snippet"))
            projected = dict(item)
            projected["snippet"] = snippet
            projected["evidence_locator"] = portable_locator(
                item.get("evidence_locator"), str(item.get("entity_id") or "")
            )
            projected["episode_id"] = (
                "codex-v2://episode/"
                + _digest(
                    str(item.get("thread_entity_id") or item.get("thread_id") or ""),
                    str(item.get("turn_id") or "thread"),
                    CODEX_EPISODE_VERSION,
                ).split(":", 1)[1]
            )
            projected["evidence_role"] = _evidence_role(item_type)
            projected["event_status"] = _event_status(item)
            projected["redactions"] = list(redactions)
            projected["truncated"] = truncated
            negatives = _negative_reasons(projected, task=task, truncated=truncated)
            projected["negative_reasons"] = list(negatives)
            type_boost = _TASK_TYPE_BOOSTS[task].get(item_type, 0.0)
            status_boost = (
                0.08
                if projected["event_status"] == "passed" and task == "validation"
                else 0.06
                if projected["event_status"] == "failed" and task == "failure"
                else 0.0
            )
            recency = 0.04 / (1 + recency_order[str(item["entity_id"])])
            penalty = min(0.30, 0.06 * len(negatives))
            projected["legacy_score"] = float(item.get("score") or 0.0)
            projected["score"] = round(
                max(
                    0.0,
                    min(
                        0.999,
                        projected["legacy_score"] + type_boost + status_boost + recency - penalty,
                    ),
                ),
                6,
            )
            projected["channels"] = sorted(
                set(projected.get("channels") or [])
                | {"codex_episode_v2", "codex_temporal_rerank_v2"}
            )
            candidates.append(projected)

        candidates.sort(
            key=lambda item: (
                float(item["score"]),
                str(item.get("timestamp") or ""),
                str(item["entity_id"]),
            ),
            reverse=True,
        )
        results = candidates[: request.limit]
        timeline = [
            {
                "entity_id": item["entity_id"],
                "episode_id": item["episode_id"],
                "thread_id": item.get("thread_id"),
                "turn_id": item.get("turn_id"),
                "timestamp": item.get("timestamp"),
                "item_type": item.get("item_type"),
                "evidence_role": item["evidence_role"],
                "event_status": item["event_status"],
                "snippet": item["snippet"],
                "locator": item["evidence_locator"],
                "negative_reasons": item["negative_reasons"],
            }
            for item in sorted(
                results,
                key=lambda value: (
                    str(value.get("timestamp") or ""),
                    str(value.get("entity_id") or ""),
                ),
            )
        ]
        thread_counts = Counter(str(item.get("thread_id") or "") for item in results)
        status_counts = Counter(item["event_status"] for item in results)
        context = {
            "schema_version": CODEX_CONTEXT_VERSION,
            "task": task,
            "episodes": sorted({item["episode_id"] for item in results}),
            "timeline": timeline,
            "comparison": {
                "thread_count": len(thread_counts),
                "threads": [
                    {"thread_id": key, "selected_events": value}
                    for key, value in sorted(thread_counts.items())
                    if key
                ],
                "event_status_counts": dict(sorted(status_counts.items())),
            },
            "missing": ["observable_evidence"] if not timeline else [],
            "reasoning_included": False,
            "retrieval_context_separated": True,
            "content_digest": _digest(
                CODEX_CONTEXT_VERSION,
                task,
                *(str(item["entity_id"]) for item in timeline),
            ),
        }
        trace = {
            **dict(legacy.get("trace") or {}),
            "engine": CODEX_V2_VERSION,
            "base_engine": "codex-weighted-hybrid-v2",
            "task": task,
            "candidate_count": len(candidates),
            "episode_count": len(context["episodes"]),
            "temporal_reranker": "codex-task-temporal-rerank-v2",
            "context_builder": CODEX_CONTEXT_VERSION,
            "reasoning_excluded": True,
            "release": dict(_RELEASE),
        }
        return {
            **legacy,
            "total": len(results),
            "results": results,
            "context": context,
            "trace": trace,
        }


__all__ = [
    "CODEX_CONTEXT_VERSION",
    "CODEX_ENGINE_HEADER",
    "CODEX_EPISODE_VERSION",
    "CODEX_V2_VERSION",
    "CodexEngineOverrideError",
    "CodexTemporalRetrieverV2",
    "validate_codex_engine_override",
]
