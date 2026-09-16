from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from .models import EvaluationMetricValue

CODE_EVALUATION_RUNNER_VERSION = "code-evaluation-v2.1"
GRAPH_CHANNELS = {"graph", "typed_graph", "graph_reverse", "graph_forward"}
NON_GRAPH_RETRIEVAL_CHANNELS = {
    "dense",
    "exact",
    "history",
    "identifier",
    "lexical",
    "path",
    "sparse",
    "test",
}


def _mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if item is not None and str(item)]


def _metric(
    run_id: str,
    case_id: str | None,
    name: str,
    value: float | int | None = None,
    *,
    slice_value: dict[str, Any] | None = None,
    unavailable_reason: str | None = None,
    numerator: float | int | None = None,
    denominator: float | int | None = None,
    eligible: bool = True,
    total_cases: int | None = None,
    eligible_cases: int | None = None,
    available_cases: int | None = None,
    unavailable_cases: int | None = None,
) -> dict[str, Any]:
    if unavailable_reason:
        return EvaluationMetricValue(
            evaluation_run_id=run_id,
            case_id=case_id,
            source_domain="code",
            metric_name=name,
            slice=slice_value or {},
            status="unavailable",
            unavailable_reason=unavailable_reason,
            numerator=float(numerator) if numerator is not None else None,
            denominator=float(denominator) if denominator is not None else None,
            eligible=eligible,
            total_cases=total_cases,
            eligible_cases=eligible_cases,
            available_cases=available_cases,
            unavailable_cases=unavailable_cases,
        ).model_dump()
    numeric_value = float(value if value is not None else 0.0)
    return EvaluationMetricValue(
        evaluation_run_id=run_id,
        case_id=case_id,
        source_domain="code",
        metric_name=name,
        slice=slice_value or {},
        value=numeric_value,
        numerator=float(numerator) if numerator is not None else numeric_value,
        denominator=float(denominator) if denominator is not None else 1.0,
        eligible=eligible,
        total_cases=total_cases,
        eligible_cases=eligible_cases,
        available_cases=available_cases,
        unavailable_cases=unavailable_cases,
    ).model_dump()


def _ratio_metric(
    run_id: str,
    case_id: str,
    name: str,
    numerator: int | float,
    denominator: int | float,
    *,
    slice_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = float(numerator) / float(denominator) if denominator else 0.0
    return _metric(
        run_id,
        case_id,
        name,
        value,
        slice_value=slice_value,
        numerator=numerator,
        denominator=denominator,
    )


def _metric_lookup(
    metrics: list[dict[str, Any]], name: str, *, slice_value: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    expected_slice = slice_value or {}
    return next(
        (
            item
            for item in metrics
            if item["metric_name"] == name and item.get("slice") == expected_slice
        ),
        None,
    )


def _candidate_identity(candidate: dict[str, Any], kind: str = "preferred") -> str | None:
    entity_id = candidate.get("entity_id")
    unit_id = candidate.get("retrieval_unit_id")
    if kind == "entity":
        return f"entity:{entity_id}" if entity_id else None
    if kind == "unit":
        return f"unit:{unit_id}" if unit_id else None
    if unit_id:
        return f"unit:{unit_id}"
    return f"entity:{entity_id}" if entity_id else None


def _normalize_candidate(raw: Any, index: int) -> dict[str, Any]:
    item = _mapping(raw)
    metadata = _mapping(item.get("metadata"))
    channels_present = any(
        key in item for key in ("channels", "matched_channels", "retrieval_channels")
    )
    channels = _string_list(
        item.get("channels") or item.get("matched_channels") or item.get("retrieval_channels")
    )
    context_roles = _string_list(item.get("context_roles") or item.get("roles"))
    if item.get("context_role"):
        context_roles.append(str(item["context_role"]))
    return {
        "entity_id": item.get("entity_id") or item.get("parent_entity_id"),
        "retrieval_unit_id": item.get("retrieval_unit_id") or item.get("unit_id"),
        "entity_type": item.get("entity_type") or metadata.get("entity_type"),
        "path": item.get("path") or metadata.get("path"),
        "start_line": item.get("start_line") or metadata.get("start_line"),
        "end_line": item.get("end_line") or metadata.get("end_line"),
        "locator": (
            item.get("evidence_locator")
            or item.get("locator")
            or item.get("source_uri")
            or metadata.get("evidence_locator")
        ),
        "version": (
            item.get("version")
            or item.get("commit")
            or item.get("commit_sha")
            or metadata.get("version")
            or metadata.get("commit")
        ),
        "acl_ref": item.get("acl_ref") or metadata.get("acl_ref"),
        "validation_target_ref": (
            item.get("validation_target_ref")
            or item.get("target_ref")
            or metadata.get("validation_target_ref")
            or metadata.get("target_ref")
        ),
        "validation_status": (
            item.get("validation_status") or item.get("status") or metadata.get("validation_status")
        ),
        "channels": list(dict.fromkeys(channels)),
        "channels_present": channels_present,
        "graph_only": item.get("graph_only"),
        "context_roles": list(dict.fromkeys(context_roles)),
        "edges": item.get("edges") or [],
        "rank": int(item.get("rank") or index + 1),
        "_input_order": index,
    }


def normalize_code_response(response: Any) -> dict[str, Any]:
    payload = _mapping(response)
    raw_candidates = payload.get("candidates")
    if raw_candidates is None:
        raw_candidates = payload.get("results", [])
    candidates = [
        _normalize_candidate(raw, index)
        for index, raw in enumerate(raw_candidates if isinstance(raw_candidates, list) else [])
    ]
    candidates.sort(key=lambda item: (item["rank"], item["_input_order"]))

    raw_edges: list[Any] = []
    for key in ("relations", "edges"):
        value = payload.get(key)
        if isinstance(value, list):
            raw_edges.extend(value)
    for candidate in candidates:
        if isinstance(candidate["edges"], list):
            raw_edges.extend(candidate["edges"])

    edges: list[dict[str, str | None]] = []
    seen_edges: set[tuple[str, str, str | None]] = set()
    for raw in raw_edges:
        edge = _mapping(raw)
        source = edge.get("source") or edge.get("source_id")
        target = edge.get("target") or edge.get("target_id")
        if not source or not target:
            continue
        edge_type = edge.get("edge_type") or edge.get("predicate") or edge.get("type")
        key = (str(source), str(target), str(edge_type) if edge_type else None)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edges.append({"source": key[0], "target": key[1], "edge_type": key[2]})

    context_roles: list[str] = []
    context = payload.get("context")
    if isinstance(context, dict):
        context_roles.extend(_string_list(context.get("roles")))
        items = context.get("items")
        if isinstance(items, list):
            for raw in items:
                item = _mapping(raw)
                context_roles.extend(_string_list(item.get("roles") or item.get("context_roles")))
                if item.get("role"):
                    context_roles.append(str(item["role"]))
    elif isinstance(context, list):
        for raw in context:
            item = _mapping(raw)
            context_roles.extend(_string_list(item.get("roles")))
            if item.get("role"):
                context_roles.append(str(item["role"]))

    trace = _mapping(payload.get("trace"))
    latency = trace.get("duration_ms")
    return {
        "candidates": candidates,
        "edges": edges,
        "context_roles": list(dict.fromkeys(context_roles)),
        "trace": trace,
        "index_generation": _string_list(payload.get("index_generation")),
        "latency_ms": float(latency) if isinstance(latency, (int, float)) else None,
    }


def _alternative_groups(profile: dict[str, Any], kind: str) -> list[set[str]]:
    field = "entity_ids" if kind == "entity" else "retrieval_unit_ids"
    return [
        {str(item) for item in group.get(field, [])}
        for group in profile.get("acceptable_alternative_groups", [])
        if group.get(field)
    ]


def _required_targets(
    case: dict[str, Any],
    profile: dict[str, Any],
    judgments: list[dict[str, Any]],
    kind: str,
) -> tuple[set[str], list[set[str]]]:
    if kind == "entity":
        singles = {str(item) for item in case.get("expected_entity_ids", [])}
        field = "entity_id"
    else:
        singles = {str(item) for item in profile.get("expected_unit_ids", [])}
        field = "retrieval_unit_id"
    singles.update(
        str(item[field])
        for item in judgments
        if int(item["relevance_grade"]) == 2 and item.get(field)
    )
    groups = _alternative_groups(profile, kind)
    for group in groups:
        singles.difference_update(group)
    return singles, groups


def _required_recall(
    retrieved: set[str], singles: set[str], groups: list[set[str]]
) -> tuple[int, int]:
    numerator = len(retrieved & singles)
    numerator += sum(bool(retrieved & group) for group in groups)
    return numerator, len(singles) + len(groups)


def _group_key(candidate: dict[str, Any], profile: dict[str, Any]) -> str | None:
    entity_id = candidate.get("entity_id")
    unit_id = candidate.get("retrieval_unit_id")
    for group in profile.get("acceptable_alternative_groups", []):
        if entity_id and entity_id in group.get("entity_ids", []):
            return f"alternative:{group['group_id']}"
        if unit_id and unit_id in group.get("retrieval_unit_ids", []):
            return f"alternative:{group['group_id']}"
    return None


def _judgment_grade(
    candidate: dict[str, Any],
    *,
    judgments: list[dict[str, Any]],
    required_entities: set[str],
    required_units: set[str],
    forbidden_entities: set[str],
    profile: dict[str, Any],
) -> int:
    entity_id = candidate.get("entity_id")
    unit_id = candidate.get("retrieval_unit_id")
    if entity_id in forbidden_entities:
        return -1
    unit_grades = [
        int(item["relevance_grade"])
        for item in judgments
        if unit_id and item.get("retrieval_unit_id") == unit_id
    ]
    if unit_grades:
        return max(unit_grades)
    entity_grades = [
        int(item["relevance_grade"])
        for item in judgments
        if entity_id and item.get("entity_id") == entity_id
    ]
    if entity_grades:
        return max(entity_grades)
    if _group_key(candidate, profile):
        return 2
    if unit_id in required_units or entity_id in required_entities:
        return 2
    return 0


def _relevance_key(
    candidate: dict[str, Any],
    *,
    profile: dict[str, Any],
    required_units: set[str],
) -> str | None:
    group = _group_key(candidate, profile)
    if group:
        return group
    unit_id = candidate.get("retrieval_unit_id")
    if unit_id and unit_id in required_units:
        return f"unit:{unit_id}"
    return _candidate_identity(candidate)


def _ideal_relevance(
    *,
    case: dict[str, Any],
    profile: dict[str, Any],
    judgments: list[dict[str, Any]],
    required_entities: set[str],
    required_units: set[str],
) -> dict[str, int]:
    relevance: dict[str, int] = {}
    grouped_entity_ids = set().union(*(_alternative_groups(profile, "entity") or [set()]))
    grouped_unit_ids = set().union(*(_alternative_groups(profile, "unit") or [set()]))
    judged_entities: set[str] = set()
    judged_units: set[str] = set()
    for judgment in judgments:
        grade = int(judgment["relevance_grade"])
        entity_id = judgment.get("entity_id")
        unit_id = judgment.get("retrieval_unit_id")
        if entity_id:
            judged_entities.add(str(entity_id))
        if unit_id:
            judged_units.add(str(unit_id))
        if grade <= 0:
            continue
        pseudo_candidate = {
            "entity_id": entity_id,
            "retrieval_unit_id": unit_id,
        }
        key = _group_key(pseudo_candidate, profile)
        if not key:
            key = f"unit:{unit_id}" if unit_id else f"entity:{entity_id}"
        relevance[key] = max(relevance.get(key, 0), grade)
    for entity_id in required_entities - judged_entities - grouped_entity_ids:
        relevance[f"entity:{entity_id}"] = 2
    for unit_id in required_units - judged_units - grouped_unit_ids:
        relevance[f"unit:{unit_id}"] = 2
    for group in profile.get("acceptable_alternative_groups", []):
        relevance[f"alternative:{group['group_id']}"] = 2
    return relevance


def _dcg(grades: list[int]) -> float:
    return sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(grades, 1))


def _inferred_entity_type(entity_id: str) -> str | None:
    if "#symbol=" in entity_id:
        return "CodeSymbol"
    if entity_id.startswith("code://"):
        return "FileVersion"
    if entity_id.startswith("test-result://"):
        return "TestResult"
    if entity_id.startswith("git://"):
        return "Commit"
    if entity_id.startswith("diff://"):
        return "DiffHunk"
    return None


def _expected_type_ids(
    entity_type: str,
    required_entities: set[str],
    profile: dict[str, Any],
    entity_types: dict[str, str],
) -> set[str]:
    resolved = {
        entity_id
        for entity_id in required_entities
        if entity_types.get(entity_id) == entity_type
        or _inferred_entity_type(entity_id) == entity_type
    }
    declared = {str(item).casefold() for item in profile.get("expected_entity_types", [])}
    aliases = {
        "CodeSymbol": {"codesymbol", "symbol"},
        "FileVersion": {"fileversion", "file"},
    }[entity_type]
    declared_retrieval_types = declared & {
        "codesymbol",
        "symbol",
        "fileversion",
        "file",
    }
    if not resolved and declared & aliases and len(declared_retrieval_types) == 1:
        return set(required_entities)
    return resolved


def _locator_matches(expected: Any, candidate: dict[str, Any]) -> bool:
    if isinstance(expected, str):
        return expected == candidate.get("locator")
    if not isinstance(expected, dict):
        return False
    aliases = {
        "locator": "locator",
        "path": "path",
        "start_line": "start_line",
        "end_line": "end_line",
        "entity_id": "entity_id",
        "retrieval_unit_id": "retrieval_unit_id",
    }
    compared = False
    for expected_key, candidate_key in aliases.items():
        if expected_key not in expected:
            continue
        compared = True
        if expected[expected_key] != candidate.get(candidate_key):
            return False
    return compared


def _returned_locator_is_valid(candidate: dict[str, Any]) -> bool:
    locator = candidate.get("locator")
    if not isinstance(locator, str) or not locator.strip():
        return False
    locator = locator.strip()
    uri_locator = re.fullmatch(r"[a-z][a-z0-9+.-]*://\S+", locator) is not None
    path_line_locator = re.fullmatch(r"[^\s:]+(?:/[^\s:]*)*:\d+(?:-\d+)?", locator) is not None
    repository_line_locator = re.fullmatch(r"\S+@\S+:\S+#L\d+-L\d+", locator) is not None
    if not uri_locator and not path_line_locator and not repository_line_locator:
        return False
    start = candidate.get("start_line")
    end = candidate.get("end_line")
    if (start is None) != (end is None):
        return False
    if start is not None:
        if not isinstance(start, int) or not isinstance(end, int):
            return False
        if start < 1 or end < start:
            return False
    return True


def _identity_ref(entity_id: str | None) -> str | None:
    if not entity_id or not entity_id.startswith("code://"):
        return None
    match = re.match(r"^code://[^@]+@([^/]+)/", entity_id)
    return match.group(1) if match else None


def _candidate_matches_ref(candidate: dict[str, Any], expected_ref: str) -> bool:
    if candidate.get("version") != expected_ref:
        return False
    identity_ref = _identity_ref(candidate.get("entity_id"))
    return identity_ref is None or identity_ref == expected_ref


def _is_validation_candidate(candidate: dict[str, Any]) -> bool:
    return str(candidate.get("entity_type") or "").casefold() == "testresult" or str(
        candidate.get("entity_id") or ""
    ).startswith("test-result://")


def _candidate_acl(candidate: dict[str, Any], entity_acl_refs: dict[str, str]) -> str | None:
    return candidate.get("acl_ref") or entity_acl_refs.get(str(candidate.get("entity_id") or ""))


def evaluate_code_case(
    run_id: str,
    case: dict[str, Any],
    response: Any,
    *,
    limit: int,
    entity_types: dict[str, str] | None = None,
    entity_acl_refs: dict[str, str] | None = None,
    allowed_acl_refs: list[str] | None = None,
    enforce_acl: bool = False,
    measured_latency_ms: float | None = None,
) -> dict[str, Any]:
    normalized = normalize_code_response(response)
    candidates = normalized["candidates"][:limit]
    profile = case.get("code_profile") or {}
    judgments = list(case.get("candidate_judgments") or [])
    required_entities, entity_groups = _required_targets(case, profile, judgments, "entity")
    required_units, unit_groups = _required_targets(case, profile, judgments, "unit")
    forbidden_entities = {str(item) for item in case.get("forbidden_entity_ids", [])}
    forbidden_entities.update(
        str(item["entity_id"])
        for item in judgments
        if int(item["relevance_grade"]) < 0 and item.get("entity_id")
    )
    harmful_units = {
        str(item["retrieval_unit_id"])
        for item in judgments
        if int(item["relevance_grade"]) < 0 and item.get("retrieval_unit_id")
    }
    resolved_entity_types = entity_types or {}
    resolved_acl_refs = entity_acl_refs or {}
    metrics: list[dict[str, Any]] = []
    case_id = case["id"]

    entity_ids_by_k = {
        k: {str(item["entity_id"]) for item in candidates[:k] if item.get("entity_id")}
        for k in (1, 5, 10, 20)
    }
    unit_ids_by_k = {
        k: {
            str(item["retrieval_unit_id"])
            for item in candidates[:k]
            if item.get("retrieval_unit_id")
        }
        for k in (1, 5, 10, 20)
    }
    unit_capable = normalized["trace"].get("retrieval_unit_capable") is True or any(
        item.get("retrieval_unit_id") for item in candidates
    )
    for k in (1, 5, 10, 20):
        entity_numerator, entity_denominator = _required_recall(
            entity_ids_by_k[k], required_entities, entity_groups
        )
        if entity_denominator:
            metrics.append(
                _ratio_metric(
                    run_id,
                    case_id,
                    f"entity_recall@{k}",
                    entity_numerator,
                    entity_denominator,
                )
            )
        else:
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    f"entity_recall@{k}",
                    unavailable_reason="case has no required entity annotation",
                    eligible=False,
                )
            )
        unit_numerator, unit_denominator = _required_recall(
            unit_ids_by_k[k], required_units, unit_groups
        )
        if not unit_denominator:
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    f"unit_recall@{k}",
                    unavailable_reason="case has no required retrieval-unit annotation",
                    eligible=False,
                )
            )
        elif not unit_capable:
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    f"unit_recall@{k}",
                    unavailable_reason=(
                        "retriever does not expose a retrieval-unit capability contract"
                    ),
                    numerator=unit_numerator,
                    denominator=unit_denominator,
                )
            )
        else:
            metrics.append(
                _ratio_metric(
                    run_id,
                    case_id,
                    f"unit_recall@{k}",
                    unit_numerator,
                    unit_denominator,
                )
            )

    for metric_name, entity_type in (
        ("file_recall@10", "FileVersion"),
        ("symbol_recall@10", "CodeSymbol"),
    ):
        expected_type_ids = _expected_type_ids(
            entity_type, required_entities, profile, resolved_entity_types
        )
        if expected_type_ids:
            numerator = len(expected_type_ids & entity_ids_by_k[10])
            metrics.append(
                _ratio_metric(
                    run_id,
                    case_id,
                    metric_name,
                    numerator,
                    len(expected_type_ids),
                )
            )
        else:
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    metric_name,
                    unavailable_reason=f"case has no required {entity_type} expectation",
                    eligible=False,
                )
            )

    ideal_relevance = _ideal_relevance(
        case=case,
        profile=profile,
        judgments=judgments,
        required_entities=required_entities,
        required_units=required_units,
    )
    seen_relevance: set[str] = set()
    ranked_grades: list[int] = []
    first_relevant_rank: int | None = None
    relevant_target_keys_at_10: set[str] = set()
    for rank, candidate in enumerate(candidates[:10], 1):
        grade = _judgment_grade(
            candidate,
            judgments=judgments,
            required_entities=required_entities,
            required_units=required_units,
            forbidden_entities=forbidden_entities,
            profile=profile,
        )
        relevance_key = _relevance_key(candidate, profile=profile, required_units=required_units)
        if grade > 0 and relevance_key and relevance_key in seen_relevance:
            grade = 0
        elif grade > 0 and relevance_key:
            seen_relevance.add(relevance_key)
            relevant_target_keys_at_10.add(relevance_key)
        ranked_grades.append(max(0, grade))
        if grade > 0 and first_relevant_rank is None:
            first_relevant_rank = rank
    if ideal_relevance:
        reciprocal_rank = 1.0 / first_relevant_rank if first_relevant_rank else 0.0
        metrics.append(_metric(run_id, case_id, "mrr@10", reciprocal_rank))
        ideal_grades = sorted(ideal_relevance.values(), reverse=True)[:10]
        ideal_dcg = _dcg(ideal_grades)
        ndcg = _dcg(ranked_grades) / ideal_dcg if ideal_dcg else 0.0
        metrics.append(_metric(run_id, case_id, "ndcg@10", ndcg))
    else:
        reason = "case has no positive relevance annotation"
        metrics.append(
            _metric(run_id, case_id, "mrr@10", unavailable_reason=reason, eligible=False)
        )
        metrics.append(
            _metric(run_id, case_id, "ndcg@10", unavailable_reason=reason, eligible=False)
        )

    has_harmful_annotation = bool(forbidden_entities or harmful_units)
    harmful_candidates = [
        item
        for item in candidates[:10]
        if item.get("entity_id") in forbidden_entities
        or item.get("retrieval_unit_id") in harmful_units
        or _judgment_grade(
            item,
            judgments=judgments,
            required_entities=required_entities,
            required_units=required_units,
            forbidden_entities=forbidden_entities,
            profile=profile,
        )
        < 0
    ]
    if has_harmful_annotation:
        metrics.append(
            _metric(
                run_id,
                case_id,
                "hard_negative_error@10",
                float(bool(harmful_candidates)),
                numerator=int(bool(harmful_candidates)),
                denominator=1,
            )
        )
        metrics.append(
            _ratio_metric(
                run_id,
                case_id,
                "harmful_candidate_rate@10",
                len(harmful_candidates),
                len(candidates[:10]),
            )
        )
    else:
        reason = "case has no forbidden or negative relevance annotation"
        metrics.append(
            _metric(
                run_id,
                case_id,
                "hard_negative_error@10",
                unavailable_reason=reason,
                eligible=False,
            )
        )
        metrics.append(
            _metric(
                run_id,
                case_id,
                "harmful_candidate_rate@10",
                unavailable_reason=reason,
                eligible=False,
            )
        )

    allowed = set(allowed_acl_refs or []) | {"public"}
    unauthorized_candidates: list[dict[str, Any]] = []
    unknown_acl_candidates: list[dict[str, Any]] = []
    if enforce_acl:
        for candidate in candidates[:10]:
            acl_ref = _candidate_acl(candidate, resolved_acl_refs)
            if not acl_ref:
                unknown_acl_candidates.append(candidate)
            elif acl_ref not in allowed:
                unauthorized_candidates.append(candidate)
        if unknown_acl_candidates:
            reason = "one or more returned candidates have no resolvable ACL"
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "unauthorized_leakage_count@10",
                    unavailable_reason=reason,
                    numerator=len(unauthorized_candidates),
                    denominator=1,
                )
            )
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "unauthorized_leakage_rate@10",
                    unavailable_reason=reason,
                    numerator=len(unauthorized_candidates),
                    denominator=len(candidates[:10]),
                )
            )
        else:
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "unauthorized_leakage_count@10",
                    len(unauthorized_candidates),
                    numerator=len(unauthorized_candidates),
                    denominator=1,
                )
            )
            metrics.append(
                _ratio_metric(
                    run_id,
                    case_id,
                    "unauthorized_leakage_rate@10",
                    len(unauthorized_candidates),
                    len(candidates[:10]),
                )
            )
    else:
        reason = "ACL enforcement was not enabled for this case"
        metrics.append(
            _metric(
                run_id,
                case_id,
                "unauthorized_leakage_count@10",
                unavailable_reason=reason,
                eligible=False,
            )
        )
        metrics.append(
            _metric(
                run_id,
                case_id,
                "unauthorized_leakage_rate@10",
                unavailable_reason=reason,
                eligible=False,
            )
        )

    typed_paths = profile.get("required_paths", [])
    actual_edges = {
        (str(item["source"]), str(item["target"]), item.get("edge_type"))
        for item in normalized["edges"]
    }
    if enforce_acl and actual_edges:
        candidate_acl_map = {
            str(item["entity_id"]): _candidate_acl(item, resolved_acl_refs)
            for item in candidates
            if item.get("entity_id")
        }
        candidate_acl_map.update(resolved_acl_refs)
        actual_edges = {
            edge
            for edge in actual_edges
            if candidate_acl_map.get(edge[0]) in allowed
            and candidate_acl_map.get(edge[1]) in allowed
        }
    if typed_paths:
        recalled_paths = 0
        required_edges: set[tuple[str, str, str]] = set()
        for path in typed_paths:
            steps = {
                (str(edge["source"]), str(edge["target"]), str(edge["edge_type"]))
                for edge in path["edges"]
            }
            required_edges.update(steps)
            recalled_paths += int(steps <= actual_edges)
        metrics.append(
            _ratio_metric(
                run_id,
                case_id,
                "required_path_recall",
                recalled_paths,
                len(typed_paths),
            )
        )
        matched_actual_edges = {actual for actual in actual_edges if actual in required_edges}
        metrics.append(
            _ratio_metric(
                run_id,
                case_id,
                "required_path_precision",
                len(matched_actual_edges),
                len(actual_edges),
                slice_value={"denominator": "all_returned_edges"},
            )
        )
    else:
        reason = "case has no typed required path annotation"
        metrics.append(
            _metric(
                run_id,
                case_id,
                "required_path_recall",
                unavailable_reason=reason,
                eligible=False,
            )
        )
        metrics.append(
            _metric(
                run_id,
                case_id,
                "required_path_precision",
                unavailable_reason=reason,
                eligible=False,
                slice_value={"denominator": "all_returned_edges"},
            )
        )

    if ideal_relevance:
        has_channel_attribution = any(
            item["channels_present"] or item.get("graph_only") is not None
            for item in candidates[:10]
        )
        if has_channel_attribution:
            channel_exclusive: set[str] = set()
            for candidate in candidates[:10]:
                grade = _judgment_grade(
                    candidate,
                    judgments=judgments,
                    required_entities=required_entities,
                    required_units=required_units,
                    forbidden_entities=forbidden_entities,
                    profile=profile,
                )
                if grade <= 0:
                    continue
                channels = {str(item).casefold() for item in candidate["channels"]}
                graph_only = (
                    bool(candidate["graph_only"])
                    if candidate.get("graph_only") is not None
                    else bool(channels & GRAPH_CHANNELS)
                    and not bool(channels & NON_GRAPH_RETRIEVAL_CHANNELS)
                )
                key = _relevance_key(candidate, profile=profile, required_units=required_units)
                if graph_only and key:
                    channel_exclusive.add(key)
            metrics.append(
                _ratio_metric(
                    run_id,
                    case_id,
                    "channel_exclusive_graph_relevant_rate@10",
                    len(channel_exclusive & set(ideal_relevance)),
                    len(ideal_relevance),
                )
            )
        else:
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "channel_exclusive_graph_relevant_rate@10",
                    unavailable_reason=("retriever did not expose candidate channel attribution"),
                )
            )
        metrics.append(
            _metric(
                run_id,
                case_id,
                "graph_only_recovery@10",
                unavailable_reason=("paired graph-on/graph-off runs are required for recovery"),
                numerator=0,
                denominator=len(ideal_relevance),
            )
        )
    else:
        reason = "case has no positive relevance annotation"
        metrics.append(
            _metric(
                run_id,
                case_id,
                "channel_exclusive_graph_relevant_rate@10",
                unavailable_reason=reason,
                eligible=False,
            )
        )
        metrics.append(
            _metric(
                run_id,
                case_id,
                "graph_only_recovery@10",
                unavailable_reason=reason,
                eligible=False,
            )
        )

    expected_ref = profile.get("expected_ref") or case.get("required_version")
    if expected_ref:
        version_candidates = candidates[:10]
        if not version_candidates:
            reason = "no retrieved candidate exposes version evidence"
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "missing_version_rate",
                    unavailable_reason=reason,
                    denominator=0,
                )
            )
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "wrong_version_rate",
                    unavailable_reason=reason,
                    denominator=0,
                )
            )
        else:
            missing_versions = sum(not item.get("version") for item in version_candidates)
            metrics.append(
                _ratio_metric(
                    run_id,
                    case_id,
                    "missing_version_rate",
                    missing_versions,
                    len(version_candidates),
                )
            )
            wrong_versions = sum(
                bool(item.get("version")) and not _candidate_matches_ref(item, expected_ref)
                for item in version_candidates
            )
            if missing_versions:
                metrics.append(
                    _metric(
                        run_id,
                        case_id,
                        "wrong_version_rate",
                        unavailable_reason=(
                            "one or more candidates lack version; wrong-version rate "
                            "cannot exclude them"
                        ),
                        numerator=wrong_versions,
                        denominator=len(version_candidates),
                    )
                )
            else:
                metrics.append(
                    _ratio_metric(
                        run_id,
                        case_id,
                        "wrong_version_rate",
                        wrong_versions,
                        len(version_candidates),
                    )
                )

        exact_retrieved = {
            str(item["entity_id"])
            for item in candidates[:10]
            if item.get("entity_id") and _candidate_matches_ref(item, expected_ref)
        }
        exact_numerator, exact_denominator = _required_recall(
            exact_retrieved, required_entities, entity_groups
        )
        if "+dirty." in str(expected_ref):
            if exact_denominator:
                metrics.append(
                    _ratio_metric(
                        run_id,
                        case_id,
                        "dirty_snapshot_accuracy",
                        exact_numerator,
                        exact_denominator,
                    )
                )
            else:
                metrics.append(
                    _metric(
                        run_id,
                        case_id,
                        "dirty_snapshot_accuracy",
                        unavailable_reason=("dirty-ref case has no required entity denominator"),
                    )
                )
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "exact_commit_accuracy",
                    unavailable_reason="expected ref is a dirty snapshot, not an exact commit",
                    eligible=False,
                )
            )
        else:
            if exact_denominator:
                metrics.append(
                    _ratio_metric(
                        run_id,
                        case_id,
                        "exact_commit_accuracy",
                        exact_numerator,
                        exact_denominator,
                    )
                )
            else:
                metrics.append(
                    _metric(
                        run_id,
                        case_id,
                        "exact_commit_accuracy",
                        unavailable_reason=("exact-commit case has no required entity denominator"),
                    )
                )
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "dirty_snapshot_accuracy",
                    unavailable_reason="expected ref is a clean commit",
                    eligible=False,
                )
            )
    else:
        reason = "case has no expected version or ref"
        for name in (
            "missing_version_rate",
            "wrong_version_rate",
            "exact_commit_accuracy",
            "dirty_snapshot_accuracy",
        ):
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    name,
                    unavailable_reason=reason,
                    eligible=False,
                )
            )

    validation_applicable = (
        "TestResult" in profile.get("expected_entity_types", [])
        or "validation" in profile.get("expected_context_roles", [])
        or any(item.get("necessity_role") == "validation" for item in judgments)
    )
    validation_candidates = [item for item in candidates[:10] if _is_validation_candidate(item)]
    if validation_applicable:
        if not validation_candidates:
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "false_validation_rate",
                    unavailable_reason=(
                        "no validation candidate exposes an exact target-ref binding"
                    ),
                    denominator=0,
                )
            )
        elif any(not item.get("validation_target_ref") for item in validation_candidates):
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "false_validation_rate",
                    unavailable_reason=(
                        "one or more validation candidates lack an exact target-ref binding"
                    ),
                    denominator=len(validation_candidates),
                )
            )
        elif not expected_ref:
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "false_validation_rate",
                    unavailable_reason="validation case has no expected target ref",
                    denominator=len(validation_candidates),
                )
            )
        else:
            false_validations = sum(
                item.get("validation_target_ref") != expected_ref for item in validation_candidates
            )
            metrics.append(
                _ratio_metric(
                    run_id,
                    case_id,
                    "false_validation_rate",
                    false_validations,
                    len(validation_candidates),
                )
            )
    else:
        metrics.append(
            _metric(
                run_id,
                case_id,
                "false_validation_rate",
                unavailable_reason="case does not require validation evidence",
                eligible=False,
            )
        )

    expected_locators = list(profile.get("expected_locators") or [])
    if expected_locators:
        locator_hits = sum(
            any(_locator_matches(expected, candidate) for candidate in candidates[:10])
            for expected in expected_locators
        )
        metrics.append(
            _ratio_metric(
                run_id,
                case_id,
                "expected_locator_recall@10",
                locator_hits,
                len(expected_locators),
            )
        )
    else:
        metrics.append(
            _metric(
                run_id,
                case_id,
                "expected_locator_recall@10",
                unavailable_reason="case has no expected locator annotation",
                eligible=False,
            )
        )
    if candidates[:10]:
        valid_locators = sum(_returned_locator_is_valid(candidate) for candidate in candidates[:10])
        metrics.append(
            _ratio_metric(
                run_id,
                case_id,
                "returned_locator_validity@10",
                valid_locators,
                len(candidates[:10]),
            )
        )
    else:
        metrics.append(
            _metric(
                run_id,
                case_id,
                "returned_locator_validity@10",
                unavailable_reason="no returned candidate locator to validate",
                eligible=False,
            )
        )

    expected_roles = {str(item) for item in profile.get("expected_context_roles", [])}
    returned_roles = set(normalized["context_roles"])
    for candidate in candidates[:10]:
        returned_roles.update(candidate["context_roles"])
    if expected_roles:
        metrics.append(
            _ratio_metric(
                run_id,
                case_id,
                "context_role_coverage@10",
                len(expected_roles & returned_roles),
                len(expected_roles),
            )
        )
    else:
        metrics.append(
            _metric(
                run_id,
                case_id,
                "context_role_coverage@10",
                unavailable_reason="case has no expected context-role annotation",
                eligible=False,
            )
        )

    entity_identities = [
        identity
        for identity in (_candidate_identity(item, "entity") for item in candidates[:10])
        if identity
    ]
    entity_duplicates = len(entity_identities) - len(set(entity_identities))
    metrics.append(
        _ratio_metric(
            run_id,
            case_id,
            "entity_duplicate_rate@10",
            entity_duplicates,
            len(entity_identities),
        )
    )
    unit_identities = [
        identity
        for identity in (_candidate_identity(item, "unit") for item in candidates[:10])
        if identity
    ]
    if unit_capable:
        unit_duplicates = len(unit_identities) - len(set(unit_identities))
        metrics.append(
            _ratio_metric(
                run_id,
                case_id,
                "unit_duplicate_rate@10",
                unit_duplicates,
                len(unit_identities),
            )
        )
    else:
        metrics.append(
            _metric(
                run_id,
                case_id,
                "unit_duplicate_rate@10",
                unavailable_reason=(
                    "retriever does not expose a retrieval-unit capability contract"
                ),
                eligible=False,
            )
        )

    latency_ms = normalized["latency_ms"]
    if latency_ms is None:
        latency_ms = measured_latency_ms
    if latency_ms is None:
        metrics.append(
            _metric(
                run_id,
                case_id,
                "latency_ms",
                unavailable_reason="runner and retriever did not expose latency",
            )
        )
    else:
        metrics.append(_metric(run_id, case_id, "latency_ms", latency_ms))
    metrics.append(_metric(run_id, case_id, "candidate_count", len(candidates)))

    returned_channel_counts: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        for channel in set(candidate["channels"]):
            returned_channel_counts[channel] += 1
    for channel, count in sorted(returned_channel_counts.items()):
        metrics.append(
            _metric(
                run_id,
                case_id,
                "candidate_count_by_channel",
                count,
                slice_value={"channel": channel, "stage": "returned"},
            )
        )
    for key, value in sorted(normalized["trace"].items()):
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (key.endswith("_candidates") or key.endswith("_matches"))
        ):
            metrics.append(
                _metric(
                    run_id,
                    case_id,
                    "candidate_count_by_channel",
                    value,
                    slice_value={"channel": key, "stage": "retriever"},
                )
            )

    index_size = normalized["trace"].get("index_size_bytes")
    if isinstance(index_size, (int, float)) and not isinstance(index_size, bool):
        metrics.append(_metric(run_id, case_id, "index_size_bytes", index_size))
    else:
        metrics.append(
            _metric(
                run_id,
                case_id,
                "index_size_bytes",
                unavailable_reason="retriever trace does not expose code index size",
            )
        )

    harmful_metric = _metric_lookup(metrics, "hard_negative_error@10")
    unauthorized_metric = _metric_lookup(metrics, "unauthorized_leakage_count@10")
    entity_recall = _metric_lookup(metrics, "entity_recall@10")
    path_recall = _metric_lookup(metrics, "required_path_recall")
    wrong_version = _metric_lookup(metrics, "wrong_version_rate")
    missing_version = _metric_lookup(metrics, "missing_version_rate")
    locator_recall = _metric_lookup(metrics, "expected_locator_recall@10")
    context_coverage = _metric_lookup(metrics, "context_role_coverage@10")
    false_validation = _metric_lookup(metrics, "false_validation_rate")

    no_harmful = (
        harmful_metric is None
        or not harmful_metric["eligible"]
        or (harmful_metric["status"] == "available" and float(harmful_metric["value"]) == 0.0)
    )
    no_unauthorized = (
        unauthorized_metric is None
        or not unauthorized_metric["eligible"]
        or (
            unauthorized_metric["status"] == "available"
            and float(unauthorized_metric["value"]) == 0.0
        )
    )
    answer_mode = str(profile.get("expected_answer_mode") or "direct")
    if answer_mode == "direct":
        gates: list[bool] = []
        for metric, applies, zero_is_good in (
            (entity_recall, bool(required_entities or entity_groups), False),
            (path_recall, bool(typed_paths), False),
            (wrong_version, bool(expected_ref), True),
            (missing_version, bool(expected_ref), True),
            (locator_recall, bool(expected_locators), False),
            (context_coverage, bool(expected_roles), False),
            (false_validation, validation_applicable, True),
        ):
            if not applies:
                continue
            if not metric or metric["status"] != "available":
                gates.append(False)
            elif zero_is_good:
                gates.append(float(metric["value"]) == 0.0)
            else:
                gates.append(float(metric["value"]) == 1.0)
        passed = bool(gates) and all(gates) and no_harmful and no_unauthorized
    elif answer_mode in {"refuse", "clarify"}:
        passed = not candidates and no_harmful and no_unauthorized
    else:
        missing_roles = bool(expected_roles - returned_roles)
        missing_validation = validation_applicable and not validation_candidates
        passed = (
            (not candidates or missing_roles or missing_validation)
            and no_harmful
            and no_unauthorized
        )
    metrics.append(
        _metric(
            run_id,
            case_id,
            "answer_mode_accuracy",
            float(passed),
            numerator=int(passed),
            denominator=1,
            slice_value={"expected_answer_mode": answer_mode},
        )
    )

    legacy_projection = {
        "source_recall": 1.0,
        "entity_recall": (
            float(entity_recall["value"])
            if entity_recall and entity_recall["status"] == "available"
            else 1.0
        ),
        "citation_completeness": (
            float(locator_recall["value"])
            if locator_recall and locator_recall["status"] == "available"
            else 1.0
        ),
        "version_accuracy": (
            1.0 - float(wrong_version["value"])
            if wrong_version and wrong_version["status"] == "available"
            else (0.0 if expected_ref else 1.0)
        ),
        "evidence_path_recall": (
            float(path_recall["value"])
            if path_recall and path_recall["status"] == "available"
            else 1.0
        ),
        "commit_accuracy": (
            1.0 - float(wrong_version["value"])
            if wrong_version and wrong_version["status"] == "available"
            else 1.0
        ),
        "wrong_version_rate": (
            float(wrong_version["value"])
            if wrong_version and wrong_version["status"] == "available"
            else (1.0 if expected_ref else 0.0)
        ),
        "unauthorized_leakage": (
            float(unauthorized_metric["value"])
            if unauthorized_metric and unauthorized_metric["status"] == "available"
            else 0.0
        ),
    }
    result_entity_ids = list(
        dict.fromkeys(str(item["entity_id"]) for item in candidates if item.get("entity_id"))
    )
    return {
        "passed": passed,
        "metrics": metrics,
        "legacy_projection": legacy_projection,
        "result_entity_ids": result_entity_ids,
        "detail": {
            "runner_version": CODE_EVALUATION_RUNNER_VERSION,
            "authoritative_metric_contract": "evaluation_metric_values",
            "legacy_fields": "compatibility_projection",
            "expected_answer_mode": answer_mode,
            "answer_mode_scoring_basis": "direct_retrieval_outcome",
            "required_entity_denominator": len(required_entities) + len(entity_groups),
            "required_unit_denominator": len(required_units) + len(unit_groups),
            "ideal_relevant_target_keys": sorted(ideal_relevance),
            "relevant_target_keys_at_10": sorted(relevant_target_keys_at_10),
            "retrieval_unit_ids": [
                item["retrieval_unit_id"] for item in candidates if item.get("retrieval_unit_id")
            ],
            "candidate_count": len(candidates),
            "unauthorized_entity_ids": [
                item["entity_id"] for item in unauthorized_candidates if item.get("entity_id")
            ],
            "unknown_acl_entity_ids": [
                item["entity_id"] for item in unknown_acl_candidates if item.get("entity_id")
            ],
            "index_generation": normalized["index_generation"],
            "trace": normalized["trace"],
        },
    }


def apply_paired_graph_recovery(
    metrics: list[dict[str, Any]],
    *,
    run_id: str,
    case_id: str,
    recovered_target_keys: set[str],
    ideal_target_keys: set[str],
) -> None:
    replacement = _ratio_metric(
        run_id,
        case_id,
        "graph_only_recovery@10",
        len(recovered_target_keys & ideal_target_keys),
        len(ideal_target_keys),
    )
    for index, metric in enumerate(metrics):
        if metric["metric_name"] == "graph_only_recovery@10" and not metric["slice"]:
            metrics[index] = replacement
            return
    metrics.append(replacement)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def aggregate_code_metrics(
    run_id: str,
    case_metrics: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    aggregates: list[dict[str, Any]] = []
    slice_groups: list[tuple[dict[str, Any], list[list[dict[str, Any]]]]] = [
        ({"scope": "overall"}, [metrics for _, metrics in case_metrics])
    ]
    tasks = sorted(
        {str(case["code_profile"]["task"]) for case, _ in case_metrics if case.get("code_profile")}
    )
    for task in tasks:
        slice_groups.append(
            (
                {"task": task},
                [
                    metrics
                    for case, metrics in case_metrics
                    if case.get("code_profile", {}).get("task") == task
                ],
            )
        )
    tags = sorted({str(tag) for case, _ in case_metrics for tag in case.get("tags", [])})
    for tag in tags:
        slice_groups.append(
            (
                {"tag": tag},
                [metrics for case, metrics in case_metrics if tag in case.get("tags", [])],
            )
        )

    for slice_value, metric_lists in slice_groups:
        grouped: dict[tuple[str, tuple[tuple[str, Any], ...]], list[dict[str, Any]]] = defaultdict(
            list
        )
        for metrics in metric_lists:
            for item in metrics:
                metric_slice = tuple(sorted((item.get("slice") or {}).items()))
                grouped[(item["metric_name"], metric_slice)].append(item)
        total_cases = len(metric_lists)
        for (name, metric_slice), items in sorted(grouped.items()):
            combined_slice = {**slice_value, **dict(metric_slice)}
            eligible_items = [item for item in items if item.get("eligible", True)]
            available_items = [item for item in eligible_items if item["status"] == "available"]
            unavailable_count = len(eligible_items) - len(available_items)
            if available_items:
                numerators = [item.get("numerator") for item in available_items]
                denominators = [item.get("denominator") for item in available_items]
                if all(value is not None for value in [*numerators, *denominators]):
                    numerator = sum(float(value) for value in numerators)
                    denominator = sum(float(value) for value in denominators)
                    value = (
                        numerator / denominator
                        if denominator
                        else sum(float(item["value"]) for item in available_items)
                        / len(available_items)
                    )
                else:
                    numerator = sum(float(item["value"]) for item in available_items)
                    denominator = len(available_items)
                    value = numerator / denominator
                aggregates.append(
                    _metric(
                        run_id,
                        None,
                        name,
                        value,
                        slice_value=combined_slice,
                        numerator=numerator,
                        denominator=denominator,
                        total_cases=total_cases,
                        eligible_cases=len(eligible_items),
                        available_cases=len(available_items),
                        unavailable_cases=unavailable_count,
                    )
                )
            else:
                aggregates.append(
                    _metric(
                        run_id,
                        None,
                        name,
                        slice_value=combined_slice,
                        unavailable_reason=(
                            "metric unavailable for every eligible case in this slice"
                            if eligible_items
                            else "metric is not applicable to this slice"
                        ),
                        eligible=bool(eligible_items),
                        numerator=0,
                        denominator=0,
                        total_cases=total_cases,
                        eligible_cases=len(eligible_items),
                        available_cases=0,
                        unavailable_cases=unavailable_count,
                    )
                )

        latency_values = [
            float(item["value"])
            for metrics in metric_lists
            for item in metrics
            if item["metric_name"] == "latency_ms"
            and not item.get("slice")
            and item["status"] == "available"
        ]
        for name, fraction in (("latency_p50_ms", 0.50), ("latency_p95_ms", 0.95)):
            if latency_values:
                value = _percentile(latency_values, fraction)
                aggregates.append(
                    _metric(
                        run_id,
                        None,
                        name,
                        value,
                        slice_value=slice_value,
                        numerator=value,
                        denominator=1,
                        total_cases=total_cases,
                        eligible_cases=total_cases,
                        available_cases=len(latency_values),
                        unavailable_cases=total_cases - len(latency_values),
                    )
                )
            else:
                aggregates.append(
                    _metric(
                        run_id,
                        None,
                        name,
                        slice_value=slice_value,
                        unavailable_reason="no case in this slice exposes latency",
                        numerator=0,
                        denominator=0,
                        total_cases=total_cases,
                        eligible_cases=total_cases,
                        available_cases=0,
                        unavailable_cases=total_cases,
                    )
                )
    return aggregates
