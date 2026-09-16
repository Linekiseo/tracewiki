"""Deterministic multi-source planning, calibration, conflict, and context utilities."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any

from ..query.intent import REQUIRED_ROLES, SOURCE_AUTHORITY

PLANNER_VERSION = "multisource-query-planner-v2"
FUSION_VERSION = "role-aware-normalized-fusion-v2"
GLOBAL_CONTEXT_VERSION = "global-comprehension-context-v2"

SOURCES = ("code", "codex", "experiment", "notebook", "document", "workspace")
_SOURCE_MARKERS: dict[str, tuple[str, ...]] = {
    "code": ("code", "source", "symbol", "implementation", "代码", "源码", "实现"),
    "codex": ("codex", "session", "decision", "rationale", "会话", "决策", "原因"),
    "experiment": ("experiment", "run", "metric", "baseline", "实验", "运行", "指标"),
    "notebook": ("notebook", "ipynb", "cell", "output", "笔记本", "单元", "输出"),
    "document": (
        "document",
        "claim",
        "paper",
        "report",
        "table",
        "conclusion",
        "文档",
        "论断",
        "报告",
        "结论",
    ),
    "workspace": ("workspace", "topic", "iteration", "task", "blocker", "任务", "迭代", "阻塞"),
}
_INTENT_ORDER: dict[str, tuple[str, ...]] = {
    "current_implementation": ("code", "codex", "workspace", "document", "experiment", "notebook"),
    "historical_implementation": (
        "code",
        "codex",
        "document",
        "workspace",
        "experiment",
        "notebook",
    ),
    "change_trace": ("code", "codex", "workspace", "experiment", "document", "notebook"),
    "rationale": ("codex", "workspace", "code", "document", "experiment", "notebook"),
    "experiment_validation": (
        "experiment",
        "notebook",
        "document",
        "code",
        "codex",
        "workspace",
    ),
    "claim_verification": (
        "experiment",
        "document",
        "notebook",
        "code",
        "codex",
        "workspace",
    ),
    "reproduction": ("experiment", "notebook", "code", "codex", "document", "workspace"),
    "staleness_check": ("document", "experiment", "code", "workspace", "codex", "notebook"),
    "global_synthesis": SOURCES,
}
_SOURCE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "code": ("implementation", "version", "diff", "tests"),
    "codex": ("development_context", "decision_or_goal", "validation"),
    "experiment": ("run", "metric", "dataset_version", "configuration", "environment"),
    "notebook": ("parameter", "code", "output", "error", "reproduction"),
    "document": ("claim", "source_location", "citation", "table", "counter_evidence"),
    "workspace": ("current_state", "owner", "blocker", "evidence_coverage", "audit"),
}
_COUNTER_STATUSES = frozenset(
    {"contradicted", "failed", "rejected", "blocked", "cancelled", "potentially_stale"}
)


def _digest(payload: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
    )


def build_query_plan(
    *,
    query: str,
    intent: str | None,
    requested_sources: list[str] | None,
    limit: int,
    max_hops: int,
) -> dict[str, Any]:
    resolved_intent = intent or "global_synthesis"
    requested = tuple(dict.fromkeys(requested_sources or ()))
    order = _INTENT_ORDER.get(resolved_intent, SOURCES)
    explicit_matches = {
        source
        for source, markers in _SOURCE_MARKERS.items()
        if any(marker in query.casefold() for marker in markers)
    }
    explicit = tuple(source for source in order if source in explicit_matches)
    if requested:
        routed = requested
        fallback: tuple[str, ...] = ()
        route_reason = "explicit_request"
    elif explicit:
        routed = explicit[:3]
        fallback = tuple(source for source in order if source not in routed)[:2]
        route_reason = "deterministic_query_markers"
    else:
        routed = order[:2]
        fallback = order[2:4]
        route_reason = "intent_default"
    planned_sources = tuple(dict.fromkeys((*routed, *fallback)))
    total_budget = min(100, max(1, int(limit)))
    leading = max(1, min(30, total_budget))
    budgets = {
        source: max(4, leading if index < 2 else max(4, leading // 2))
        for index, source in enumerate(planned_sources)
    }
    required_roles = tuple(REQUIRED_ROLES.get(resolved_intent, ()))
    subquestions = {
        source: _subquestion(query, source, required_roles) for source in planned_sources
    }
    plan = {
        "schema_version": PLANNER_VERSION,
        "intent": resolved_intent,
        "requested_sources": list(requested),
        "explicit_sources": list(explicit),
        "routed_sources": list(routed),
        "fallback_sources": list(fallback),
        "route_reason": route_reason,
        "required_roles": list(required_roles),
        "source_budgets": budgets,
        "subquestions": subquestions,
        "capabilities": {source: list(_SOURCE_CAPABILITIES[source]) for source in planned_sources},
        "max_hops": min(4, max(0, int(max_hops))),
        "max_source_waves": 2,
        "max_corrective_rounds": 2,
        "reasoning_included": False,
    }
    plan["content_digest"] = _digest(plan)
    return plan


def _subquestion(query: str, source: str, roles: tuple[str, ...]) -> str:
    capabilities = set(_SOURCE_CAPABILITIES[source])
    relevant = [role for role in roles if role in capabilities]
    suffix = ", ".join(relevant or _SOURCE_CAPABILITIES[source][:2])
    return f"{query.strip()} [source={source}; required={suffix}]"[:2_000]


def calibrate_results(results: list[dict[str, Any]], *, intent: str | None) -> list[dict[str, Any]]:
    """Normalize within a source without claiming a fitted relevance probability."""

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        by_source[str(item.get("source") or "unknown")].append(item)
    authority_weights = SOURCE_AUTHORITY.get(intent or "", {})
    calibrated: list[dict[str, Any]] = []
    for source, items in by_source.items():
        ordered = sorted(
            items,
            key=lambda item: (-float(item.get("score") or 0.0), str(item.get("entity_id"))),
        )
        raw_values = [float(item.get("score") or 0.0) for item in ordered]
        low = min(raw_values, default=0.0)
        high = max(raw_values, default=0.0)
        for rank, item in enumerate(ordered, start=1):
            raw = float(item.get("score") or 0.0)
            local_normalized = (
                (raw - low) / (high - low) if high > low else (1.0 if raw > 0 else 0.0)
            )
            rank_percentile = 1.0 - (rank - 1) / max(1, len(ordered))
            authority = float(item.get("authority") or authority_weights.get(source, 0.7))
            status = str(item.get("status") or "").casefold()
            status_factor = 0.65 if status in _COUNTER_STATUSES else 1.0
            version_factor = 0.35 if item.get("version_alignment") == "mismatch" else 1.0
            score = (
                (
                    0.52 * rank_percentile
                    + 0.28 * local_normalized
                    + 0.20 * max(0.0, min(1.0, authority))
                )
                * status_factor
                * version_factor
            )
            item["local_score"] = raw
            item["score"] = round(max(0.0, min(1.0, score)), 6)
            item["fusion_explanation"] = {
                "source": source,
                "source_rank": rank,
                "source_count": len(ordered),
                "score_kind": "within_source_normalized_heuristic",
                "score_is_calibrated_probability": False,
                "local_normalized": round(local_normalized, 6),
                "rank_percentile": round(rank_percentile, 6),
                "authority": round(authority, 6),
                "status_factor": status_factor,
                "version_factor": version_factor,
                "raw_scores_cross_source_added": False,
            }
            calibrated.append(item)
    return calibrated


def root_provenance_key(item: dict[str, Any]) -> tuple[str, str, str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    root = (
        item.get("root_provenance")
        or metadata.get("root_provenance")
        or metadata.get("root_entity_id")
        or metadata.get("source_entity_id")
        or item.get("content_digest")
        or item.get("entity_id")
    )
    return (
        str(metadata.get("root_provenance_namespace") or "global"),
        str(root or ""),
        str(item.get("version") or ""),
    )


def detect_conflicts_and_staleness(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in results:
        status = str(item.get("status") or "").casefold()
        if status in _COUNTER_STATUSES or item.get("version_alignment") == "mismatch":
            findings.append(
                {
                    "kind": "counter_or_stale",
                    "entity_ids": [item["entity_id"]],
                    "sources": [item["source"]],
                    "status": status or "version_mismatch",
                    "reason": (
                        "version_mismatch"
                        if item.get("version_alignment") == "mismatch"
                        else f"observable_status:{status}"
                    ),
                }
            )
    by_title: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        title = re.sub(r"\W+", " ", str(item.get("title") or "").casefold()).strip()
        if title:
            by_title[title].append(item)
    for title, items in by_title.items():
        sources = {str(item.get("source")) for item in items}
        statuses = {str(item.get("status") or "").casefold() for item in items}
        versions = {str(item.get("version")) for item in items if item.get("version")}
        if len(sources) >= 2 and (len(statuses) > 1 or len(versions) > 1):
            findings.append(
                {
                    "kind": "cross_source_difference",
                    "entity_ids": sorted(str(item["entity_id"]) for item in items),
                    "sources": sorted(sources),
                    "status": "REVIEW_REQUIRED",
                    "reason": f"same_title_different_status_or_version:{title[:120]}",
                }
            )
    findings.sort(key=lambda item: (item["kind"], item["reason"], item["entity_ids"]))
    return findings[:50]


def build_global_context(
    *,
    plan: dict[str, Any],
    results: list[dict[str, Any]],
    source_contexts: dict[str, Any],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    sections = []
    for source in plan["routed_sources"]:
        items = [item for item in results if item.get("source") == source]
        sections.append(
            {
                "source": source,
                "available": bool(items),
                "count": len(items),
                "entity_ids": [item["entity_id"] for item in items],
                "roles": sorted(
                    {
                        str(role)
                        for item in items
                        for role in (
                            item.get("roles")
                            if isinstance(item.get("roles"), list)
                            else item.get("channels", [])
                        )
                    }
                ),
                "specialized_context": source_contexts.get(source),
            }
        )
    role_coverage = Counter(
        str(role)
        for item in results
        for role in (
            item.get("roles") if isinstance(item.get("roles"), list) else item.get("channels", [])
        )
    )
    payload = {
        "schema_version": GLOBAL_CONTEXT_VERSION,
        "plan_digest": plan["content_digest"],
        "sections": sections,
        "role_coverage": dict(sorted(role_coverage.items())),
        "conflicts_and_staleness": conflicts,
        "missing_sources": [section["source"] for section in sections if not section["available"]],
        "reasoning_included": False,
        "derived_only_from_selected_evidence": True,
    }
    payload["content_digest"] = _digest(payload)
    return payload
