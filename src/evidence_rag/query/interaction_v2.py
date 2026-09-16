"""Deterministic, persistence-free interaction and answer-grounding contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SourceName = Literal["code", "codex", "workspace", "experiment", "notebook", "document"]
ALL_SOURCES: tuple[SourceName, ...] = (
    "code",
    "codex",
    "experiment",
    "notebook",
    "document",
    "workspace",
)

INTERACTION_CONTRACT_VERSION = "query-interaction-v2"
CLAIM_VERIFIER_VERSION = "query-claim-citation-verifier-v2"
TRACE_SANITIZER_VERSION = "query-trace-sanitizer-v2"

_AMBIGUOUS_REFERENCE_RE = re.compile(
    r"(?:这个|那个|它|其|上述|前述|刚才|上一个|上一轮|那次|旧版本|之前的版本|"
    r"\b(?:it|that|this|those|previous|former|old version|above)\b)",
    re.IGNORECASE,
)
_LOW_INFORMATION_RE = re.compile(
    r"^(?:为什么|怎么样|然后呢|还有呢|继续|why|how|continue)[？?]?$", re.I
)
_CITATION_RE = re.compile(r"\[(E\d+)\]")
_CLAIM_END_RE = re.compile(r"[。！？.!?]")
_WORD_RE = re.compile(r"[A-Za-z0-9_.@/+:-]{2,}|[\u3400-\u9fff]+")
_NUMBER_UNIT_RE = re.compile(
    r"(?<![\w.])([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    r"\s*(%|percent|ms|msec|s|sec|seconds?|kg|g|kb|mb|gb|tb|hz|khz|mhz|ghz|x)?",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"(?:\b(?:not|no|never|without|neither|nor|cannot|can't|didn't|failed to)\b|"
    r"不|未|没有|并非|不能|无法|禁止)",
    re.IGNORECASE,
)
_POSITIVE_STATUS_RE = re.compile(
    r"(?:\b(?:passed|completed|succeeded|successful|applied|accepted)\b|"
    r"通过|完成|成功|已应用|已接受)",
    re.IGNORECASE,
)
_NEGATIVE_STATUS_RE = re.compile(
    r"(?:\b(?:failed|failure|rejected|timeout|timed out|cancelled|canceled)\b|"
    r"失败|拒绝|超时|取消)",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S{8,}))"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'`(=:\[])(?:"
    r"/(?:Users|home|private|tmp|var|etc|opt|root)(?:/|\\)"
    r"|[a-z]:[\\/](?:[^\\/\s]+[\\/]?)+"
    r"|file:///?(?:[a-z]:[\\/]|/)"
    r")"
)
_UNC_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'`(=\[])(?:"
    r"\\\\[^\\/\s]+[\\/][^\\/\s]+"
    r"|//[^/\s]+/[^/\s]+"
    r")"
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?is)(?:"
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|system|developer)"
    r"\s+(?:instructions?|messages?|prompts?)"
    r"|(?:override|bypass|disregard|forget)\s+(?:the\s+)?(?:system|developer|previous|prior)"
    r"\s+(?:instructions?|messages?|prompts?)"
    r"|(?:reveal|print|show|repeat)\s+(?:the\s+)?(?:system|developer)\s+(?:prompt|message)"
    r"|you\s+are\s+(?:now\s+)?(?:chatgpt|an?\s+assistant)"
    r"|jailbreak"
    r"|忽略(?:所有|任何|之前|前述|上述|系统|开发者)?.{0,12}(?:指令|提示|消息)"
    r"|(?:覆盖|绕过|无视|泄露|显示|打印).{0,12}(?:系统|开发者).{0,8}(?:指令|提示|消息)"
    r"|越狱"
    r")"
)
_SENSITIVE_TRACE_KEYS = {
    "authorization",
    "api_key",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "prompt",
    "messages",
    "query",
    "question",
    "raw_query",
    "rewritten_query",
}
_WORD_STOP = {
    "the",
    "and",
    "are",
    "was",
    "were",
    "with",
    "from",
    "that",
    "this",
    "for",
    "into",
    "has",
    "have",
    "had",
    "its",
    "but",
    "not",
    "can",
    "will",
    "may",
    "is",
    "a",
    "an",
    "of",
    "to",
    "in",
    "on",
    "at",
}


class InteractionState(StrEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    QUERY_RESOLVED = "query_resolved"
    NEEDS_CLARIFICATION = "needs_clarification"
    SCOPE_SNAPSHOTTED = "scope_snapshotted"
    PLANNED = "planned"
    RETRIEVING = "retrieving"
    EVIDENCE_VERIFIED = "evidence_verified"
    CONTEXT_PACKED = "context_packed"
    ANSWER_MODE_SELECTED = "answer_mode_selected"
    GENERATING = "generating"
    CLAIM_VERIFYING = "claim_verifying"
    COMPLETE = "complete"
    PARTIAL = "partial"
    REFUSED = "refused"
    RETRIEVAL_ONLY = "retrieval_only"


class AnswerMode(StrEnum):
    GROUNDED_GENERATION = "grounded_generation"
    RETRIEVAL_ONLY = "retrieval_only"
    PARTIAL = "partial"
    REFUSAL = "refusal"


class SourceExecutionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    NOT_INDEXED = "not_indexed"
    NO_MATCHING_EVIDENCE = "no_matching_evidence"


class ConversationSnapshot(BaseModel):
    """A client-carried snapshot; no server-side conversation persistence is implied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["conversation-snapshot-v1"] = "conversation-snapshot-v1"
    conversation_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    last_turn_id: str | None = Field(default=None, max_length=128)
    project_id: str
    repository_ids: tuple[str, ...] = ()
    commit: str | None = None
    as_of: str | None = None
    source_types: tuple[SourceName, ...] = ()
    intent: str | None = None
    citation_ids: tuple[str, ...] = ()
    index_generations: tuple[str, ...] = ()
    scope_digest: str


@dataclass(frozen=True, slots=True)
class InteractionMachine:
    state: InteractionState
    history: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnswerabilityDecision:
    mode: AnswerMode
    final_state: InteractionState
    reason: str
    llm_allowed: bool


@dataclass(frozen=True, slots=True)
class ClaimCheck:
    claim_index: int
    citation_ids: tuple[str, ...]
    supported: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CitationValidation:
    supported: bool
    checks: tuple[ClaimCheck, ...]
    claim_count: int
    supported_claim_count: int

    def trace_summary(self) -> dict[str, Any]:
        reason_counts: dict[str, int] = {}
        for check in self.checks:
            for reason in check.reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        return {
            "verifier_version": CLAIM_VERIFIER_VERSION,
            "supported": self.supported,
            "claim_count": self.claim_count,
            "supported_claim_count": self.supported_claim_count,
            "reason_counts": dict(sorted(reason_counts.items())),
        }


class UnsafeEvidenceError(ValueError):
    """Raised before a remote model call when outbound evidence is unsafe."""


_ALLOWED_TRANSITIONS: dict[InteractionState, frozenset[InteractionState]] = {
    InteractionState.RECEIVED: frozenset({InteractionState.VALIDATED}),
    InteractionState.VALIDATED: frozenset(
        {InteractionState.QUERY_RESOLVED, InteractionState.NEEDS_CLARIFICATION}
    ),
    InteractionState.QUERY_RESOLVED: frozenset({InteractionState.SCOPE_SNAPSHOTTED}),
    InteractionState.SCOPE_SNAPSHOTTED: frozenset({InteractionState.PLANNED}),
    InteractionState.PLANNED: frozenset({InteractionState.RETRIEVING}),
    InteractionState.RETRIEVING: frozenset(
        {
            InteractionState.EVIDENCE_VERIFIED,
            InteractionState.PARTIAL,
            InteractionState.REFUSED,
        }
    ),
    InteractionState.EVIDENCE_VERIFIED: frozenset({InteractionState.CONTEXT_PACKED}),
    InteractionState.CONTEXT_PACKED: frozenset({InteractionState.ANSWER_MODE_SELECTED}),
    InteractionState.ANSWER_MODE_SELECTED: frozenset(
        {
            InteractionState.GENERATING,
            InteractionState.PARTIAL,
            InteractionState.REFUSED,
            InteractionState.RETRIEVAL_ONLY,
        }
    ),
    InteractionState.GENERATING: frozenset(
        {InteractionState.CLAIM_VERIFYING, InteractionState.RETRIEVAL_ONLY}
    ),
    InteractionState.CLAIM_VERIFYING: frozenset(
        {InteractionState.COMPLETE, InteractionState.RETRIEVAL_ONLY}
    ),
}


def begin_interaction() -> InteractionMachine:
    return InteractionMachine(
        state=InteractionState.RECEIVED,
        history=(InteractionState.RECEIVED.value,),
    )


def transition(
    machine: InteractionMachine,
    target: InteractionState,
) -> InteractionMachine:
    allowed = _ALLOWED_TRANSITIONS.get(machine.state, frozenset())
    if target not in allowed:
        raise ValueError(f"invalid interaction transition: {machine.state.value}->{target.value}")
    return InteractionMachine(state=target, history=(*machine.history, target.value))


def clarification_for_request(
    *,
    question: str,
    policy: str | None,
    conversation_id: str | None,
    client_turn_id: str | None,
    parent_turn_id: str | None,
    context_revision: int | None,
    snapshot: ConversationSnapshot | None,
    explicit_commit: str | None,
    explicit_as_of: str | None,
) -> dict[str, Any] | None:
    """Return a deterministic clarification without retaining or echoing the raw question."""

    snapshot_issue: str | None = None
    if snapshot is not None:
        if conversation_id is None or snapshot.conversation_id != conversation_id:
            snapshot_issue = "conversation_snapshot_mismatch"
        elif context_revision is None or snapshot.revision != context_revision:
            snapshot_issue = "context_revision_mismatch"
        elif parent_turn_id and snapshot.last_turn_id != parent_turn_id:
            snapshot_issue = "parent_turn_mismatch"
    elif context_revision not in {None, 0} or parent_turn_id is not None:
        snapshot_issue = "conversation_snapshot_required"
    if snapshot_issue:
        return {
            "needed": True,
            "reason": snapshot_issue,
            "questions": [
                "The referenced conversation context is unavailable or stale. "
                "Please resend the latest ConversationSnapshot or restate the target scope/version."
            ],
            "resume_required": True,
        }

    normalized_policy = policy or "auto"
    if normalized_policy == "never":
        return None
    has_reusable_context = (
        snapshot is not None
        and snapshot.conversation_id == conversation_id
        and snapshot.revision == context_revision
    )
    version_is_implicit = (
        _AMBIGUOUS_REFERENCE_RE.search(question) is not None
        and explicit_commit is None
        and explicit_as_of is None
        and not has_reusable_context
    )
    low_information = normalized_policy == "always" and _LOW_INFORMATION_RE.fullmatch(
        question.strip()
    )
    if not version_is_implicit and not low_information:
        return None
    return {
        "needed": True,
        "reason": (
            "ambiguous_scope_or_version" if version_is_implicit else "underspecified_follow_up"
        ),
        "questions": ["Which project entity and version or time should this question apply to?"],
        "resume_required": True,
        "client_turn_id": client_turn_id,
    }


def normalize_source_status(
    search: Mapping[str, Any],
    requested_sources: Sequence[str],
) -> dict[str, dict[str, Any]]:
    trace = search.get("trace") if isinstance(search.get("trace"), Mapping) else {}
    source_traces = trace.get("sources") if isinstance(trace.get("sources"), Mapping) else {}
    results = search.get("results") if isinstance(search.get("results"), list) else []
    counts: dict[str, int] = {}
    for item in results:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source") or "")
        if source:
            counts[source] = counts.get(source, 0) + 1
    expected = tuple(dict.fromkeys(requested_sources or ALL_SOURCES))
    normalized: dict[str, dict[str, Any]] = {}
    for source in expected:
        raw = source_traces.get(source)
        raw = raw if isinstance(raw, Mapping) else {}
        raw_status = str(raw.get("status") or "").casefold()
        status = _normalize_source_status_value(raw_status)
        if not raw_status:
            status = (
                SourceExecutionStatus.COMPLETE
                if source in source_traces or counts.get(source, 0)
                else SourceExecutionStatus.NO_MATCHING_EVIDENCE
            )
        normalized[source] = {
            "status": status.value,
            "reason": _source_reason(status),
            "latency_ms": _number_or_none(raw.get("latency_ms") or raw.get("duration_ms")),
            "candidate_count": counts.get(source, 0),
            "index_generation": raw.get("index_generation"),
            "watermark": raw.get("watermark"),
        }
    return normalized


def source_status_for_exception(
    requested_sources: Sequence[str],
    error: BaseException,
    *,
    latency_ms: float,
) -> dict[str, dict[str, Any]]:
    error_name = error.__class__.__name__.casefold()
    timed_out = isinstance(error, TimeoutError) or "timeout" in error_name
    if isinstance(error, OSError) and "timeout" in str(error).casefold():
        timed_out = True
    status = SourceExecutionStatus.TIMEOUT if timed_out else SourceExecutionStatus.UNAVAILABLE
    return {
        source: {
            "status": status.value,
            "reason": _source_reason(status),
            "latency_ms": round(latency_ms, 2),
            "candidate_count": 0,
            "index_generation": None,
            "watermark": None,
        }
        for source in dict.fromkeys(requested_sources or ALL_SOURCES)
    }


def has_source_failure(source_status: Mapping[str, Mapping[str, Any]]) -> bool:
    failing = {
        SourceExecutionStatus.PARTIAL.value,
        SourceExecutionStatus.TIMEOUT.value,
        SourceExecutionStatus.UNAVAILABLE.value,
        SourceExecutionStatus.UNAUTHORIZED.value,
        SourceExecutionStatus.NOT_INDEXED.value,
    }
    return any(str(item.get("status")) in failing for item in source_status.values())


def select_answer_mode(
    *,
    request_mode: str,
    answer_format: str | None,
    llm_configured: bool,
    pack: Mapping[str, Any],
    source_status: Mapping[str, Mapping[str, Any]],
    deadline_exceeded: bool,
    requested_sources: Sequence[str],
    repository_ids: Sequence[str],
    target_commit: str | None,
) -> AnswerabilityDecision:
    decision = pack.get("decision") if isinstance(pack.get("decision"), Mapping) else {}
    decision_status = str(decision.get("status") or "insufficient_evidence")
    citation_map = pack.get("citation_map") if isinstance(pack.get("citation_map"), Mapping) else {}
    has_evidence = bool(citation_map)
    safety = pack.get("evidence_safety") if isinstance(pack.get("evidence_safety"), Mapping) else {}
    if safety.get("generation_blocked"):
        if has_evidence:
            return AnswerabilityDecision(
                AnswerMode.RETRIEVAL_ONLY,
                InteractionState.RETRIEVAL_ONLY,
                "unsafe_evidence_removed",
                False,
            )
        return AnswerabilityDecision(
            AnswerMode.REFUSAL,
            InteractionState.REFUSED,
            "no_safe_evidence",
            False,
        )
    trusted, trust_reason = evidence_scope_is_trusted(
        pack,
        requested_sources=requested_sources,
        repository_ids=repository_ids,
        target_commit=target_commit,
    )
    if decision_status == "potentially_stale":
        return AnswerabilityDecision(
            AnswerMode.REFUSAL,
            InteractionState.REFUSED,
            "potentially_stale",
            False,
        )
    if not trusted:
        return AnswerabilityDecision(
            AnswerMode.REFUSAL,
            InteractionState.REFUSED,
            trust_reason,
            False,
        )
    source_values = {str(item.get("status")) for item in source_status.values()}
    if SourceExecutionStatus.UNAUTHORIZED.value in source_values:
        return AnswerabilityDecision(
            AnswerMode.REFUSAL,
            InteractionState.REFUSED,
            "source_unauthorized",
            False,
        )
    if deadline_exceeded or has_source_failure(source_status):
        if has_evidence:
            return AnswerabilityDecision(
                AnswerMode.PARTIAL,
                InteractionState.PARTIAL,
                "deadline_exceeded" if deadline_exceeded else "source_partial",
                False,
            )
        return AnswerabilityDecision(
            AnswerMode.REFUSAL,
            InteractionState.REFUSED,
            "deadline_exceeded" if deadline_exceeded else "source_unavailable",
            False,
        )
    if decision_status in {"insufficient_evidence", "source_unavailable", "unauthorized"}:
        if has_evidence and decision_status == "insufficient_evidence":
            return AnswerabilityDecision(
                AnswerMode.RETRIEVAL_ONLY,
                InteractionState.RETRIEVAL_ONLY,
                "unverified_or_incomplete_evidence",
                False,
            )
        return AnswerabilityDecision(
            AnswerMode.REFUSAL,
            InteractionState.REFUSED,
            decision_status,
            False,
        )
    if request_mode == "evidence" or answer_format == "evidence_only" or not llm_configured:
        return AnswerabilityDecision(
            AnswerMode.RETRIEVAL_ONLY,
            InteractionState.RETRIEVAL_ONLY,
            "evidence_requested" if request_mode == "evidence" else "generation_unavailable",
            False,
        )
    return AnswerabilityDecision(
        AnswerMode.GROUNDED_GENERATION,
        InteractionState.COMPLETE,
        "grounded_generation_allowed",
        True,
    )


def evidence_scope_is_trusted(
    pack: Mapping[str, Any],
    *,
    requested_sources: Sequence[str],
    repository_ids: Sequence[str],
    target_commit: str | None,
) -> tuple[bool, str]:
    allowed_sources = set(requested_sources)
    allowed_repositories = set(repository_ids)
    items = [
        *list(pack.get("verified_facts") or ()),
        *list(pack.get("supporting_evidence") or ()),
    ]
    for item in items:
        if not isinstance(item, Mapping):
            continue
        # Relation-expanded nodes are navigation hints, never answer authority.
        # Their version alignment may legitimately be ``not_applicable``; they
        # must not downgrade an otherwise exact direct evidence set.
        if item.get("relation_only"):
            continue
        source = str(item.get("source") or "")
        if allowed_sources and source not in allowed_sources:
            return False, "source_scope_mismatch"
        repository_id = item.get("repository_id")
        if allowed_repositories and repository_id and repository_id not in allowed_repositories:
            return False, "repository_scope_mismatch"
        alignment = str(item.get("version_alignment") or "").casefold()
        if alignment in {"mismatch", "unknown"}:
            return False, "version_scope_untrusted"
        if source == "code" and target_commit:
            if alignment not in {"exact", "compatible", "historical_target"}:
                return False, "code_version_untrusted"
            if not item.get("version"):
                return False, "code_version_missing"
    return True, "scope_trusted"


def validate_claim_citations(
    text: str,
    pack: Mapping[str, Any],
    *,
    requested_sources: Sequence[str] = (),
    repository_ids: Sequence[str] = (),
    target_commit: str | None = None,
) -> CitationValidation:
    claims = split_claims(text)
    citation_map = pack.get("citation_map") if isinstance(pack.get("citation_map"), Mapping) else {}
    evidence_by_entity = {
        str(item.get("entity_id")): item
        for item in [
            *list(pack.get("verified_facts") or ()),
            *list(pack.get("supporting_evidence") or ()),
            *list(pack.get("counter_evidence") or ()),
        ]
        if isinstance(item, Mapping) and item.get("entity_id")
    }
    checks: list[ClaimCheck] = []
    for index, claim in enumerate(claims):
        citation_ids = tuple(dict.fromkeys(_CITATION_RE.findall(claim)))
        reasons: list[str] = []
        if not citation_ids:
            reasons.append("citation_missing")
        invalid_ids = [citation for citation in citation_ids if citation not in citation_map]
        if invalid_ids:
            reasons.append("citation_unknown")
        candidates: list[Mapping[str, Any]] = []
        for citation_id in citation_ids:
            citation = citation_map.get(citation_id)
            if not isinstance(citation, Mapping):
                continue
            entity_id = str(citation.get("entity_id") or "")
            item = evidence_by_entity.get(entity_id)
            if item is None:
                reasons.append("citation_evidence_missing")
                continue
            if citation.get("version") != item.get("version"):
                reasons.append("citation_version_mismatch")
                continue
            scope_ok, scope_reason = _claim_scope_is_supported(
                item,
                requested_sources=requested_sources,
                repository_ids=repository_ids,
                target_commit=target_commit,
            )
            if not scope_ok:
                reasons.append(scope_reason)
                continue
            candidates.append(item)
        if candidates and not any(_claim_is_entailed(claim, item) for item in candidates):
            reasons.append("claim_not_supported")
        checks.append(
            ClaimCheck(
                claim_index=index,
                citation_ids=citation_ids,
                supported=not reasons and bool(candidates),
                reasons=tuple(dict.fromkeys(reasons)),
            )
        )
    supported_count = sum(check.supported for check in checks)
    return CitationValidation(
        supported=bool(checks) and supported_count == len(checks),
        checks=tuple(checks),
        claim_count=len(checks),
        supported_claim_count=supported_count,
    )


def repair_generated_text(text: str, validation: CitationValidation) -> str:
    claims = split_claims(text)
    supported = {check.claim_index for check in validation.checks if check.supported}
    return "\n".join(claim for index, claim in enumerate(claims) if index in supported).strip()


def filter_unsafe_evidence(
    pack: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove unsafe full evidence records and block free generation for this turn."""

    filtered = deepcopy(dict(pack))
    groups = ("verified_facts", "supporting_evidence", "counter_evidence")
    unsafe_entities: set[str] = set()
    reason_counts: dict[str, int] = {}
    for group in groups:
        safe_items: list[dict[str, Any]] = []
        for raw_item in filtered.get(group) or []:
            item = dict(raw_item)
            reasons = evidence_content_findings(item)
            if not reasons:
                safe_items.append(item)
                continue
            unsafe_entities.add(str(item.get("entity_id") or ""))
            for reason in reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        filtered[group] = safe_items

    if unsafe_entities:
        filtered["related_evidence"] = [
            item
            for item in filtered.get("related_evidence") or []
            if str(item.get("entity_id") or "") not in unsafe_entities
        ]
        filtered["citation_map"] = {
            citation_id: citation
            for citation_id, citation in (filtered.get("citation_map") or {}).items()
            if str(citation.get("entity_id") or "") not in unsafe_entities
        }
        _filter_comprehension_context(filtered, unsafe_entities)
        missing = list(filtered.get("missing_evidence") or [])
        missing.append(
            {
                "role": "safe_generation_context",
                "reason": "Unsafe evidence was removed before generation.",
            }
        )
        filtered["missing_evidence"] = missing[:6]
        role_coverage = dict(filtered.get("role_coverage") or {})
        role_coverage["missing"] = list(
            dict.fromkeys([*(role_coverage.get("missing") or []), "safe_generation_context"])
        )
        filtered["role_coverage"] = role_coverage
        decision = dict(filtered.get("decision") or {})
        decision["status"] = (
            "partially_supported" if filtered.get("citation_map") else "insufficient_evidence"
        )
        decision["safety_degraded"] = True
        filtered["decision"] = decision

    report = {
        "policy_version": "outbound-evidence-safety-v1",
        "checked_full_string": True,
        "removed_evidence_count": len(unsafe_entities),
        "reason_counts": dict(sorted(reason_counts.items())),
        "generation_blocked": bool(unsafe_entities),
    }
    filtered["evidence_safety"] = report
    return filtered, report


def evidence_content_findings(value: Any) -> tuple[str, ...]:
    """Scan the complete serialized evidence value without truncation."""

    rendered = value if isinstance(value, str) else "\n".join(_iter_string_values(value))
    findings: list[str] = []
    if _SECRET_RE.search(rendered):
        findings.append("secret_material")
    if _ABSOLUTE_PATH_RE.search(rendered) or _UNC_PATH_RE.search(rendered):
        findings.append("absolute_or_unc_path")
    if _PROMPT_INJECTION_RE.search(rendered):
        findings.append("prompt_injection")
    return tuple(findings)


def assert_outbound_evidence_safe(value: str) -> None:
    findings = evidence_content_findings(value)
    if findings:
        raise UnsafeEvidenceError(
            "outbound evidence rejected by safety policy: " + ",".join(findings)
        )


def split_claims(text: str) -> tuple[str, ...]:
    claims: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cursor = 0
        for match in _CLAIM_END_RE.finditer(line):
            end = match.end()
            citation_tail = re.match(r"(?:\s*\[E\d+\])+", line[end:])
            if citation_tail:
                end += citation_tail.end()
            claim = line[cursor:end].strip()
            if claim and _CITATION_RE.fullmatch(claim) is None:
                claims.append(claim)
            cursor = end
        remainder = line[cursor:].strip()
        if remainder and _CITATION_RE.fullmatch(remainder) is None:
            claims.append(remainder)
    return tuple(claims)


def sanitize_trace(value: Any) -> Any:
    """Remove raw questions, ACL material, credentials and secrets from nested trace data."""

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.casefold()
            if (
                normalized in _SENSITIVE_TRACE_KEYS
                or "question" in normalized
                or "acl" in normalized
                or "secret" in normalized
            ):
                continue
            sanitized[key] = sanitize_trace(raw_value)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_trace(item) for item in value]
    if isinstance(value, str):
        return _SECRET_RE.sub("[REDACTED]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def build_conversation_snapshot(
    *,
    conversation_id: str,
    revision: int,
    last_turn_id: str | None,
    resolved_scope: Mapping[str, Any],
    source_types: Sequence[str],
    intent: str | None,
    citation_ids: Sequence[str],
    index_generations: Sequence[str],
) -> ConversationSnapshot:
    scope_payload = {
        "project_id": str(resolved_scope.get("project_id") or "project-rag"),
        "repository_ids": tuple(str(item) for item in resolved_scope.get("repository_ids") or ()),
        "commit": _optional_string(resolved_scope.get("commit")),
        "as_of": _optional_string(resolved_scope.get("as_of")),
        "source_types": tuple(source_types),
    }
    scope_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(scope_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    return ConversationSnapshot(
        conversation_id=conversation_id,
        revision=revision,
        last_turn_id=last_turn_id,
        project_id=scope_payload["project_id"],
        repository_ids=scope_payload["repository_ids"],
        commit=scope_payload["commit"],
        as_of=scope_payload["as_of"],
        source_types=scope_payload["source_types"],
        intent=intent,
        citation_ids=tuple(dict.fromkeys(citation_ids)),
        index_generations=tuple(dict.fromkeys(index_generations)),
        scope_digest=scope_digest,
    )


def _claim_scope_is_supported(
    item: Mapping[str, Any],
    *,
    requested_sources: Sequence[str],
    repository_ids: Sequence[str],
    target_commit: str | None,
) -> tuple[bool, str]:
    if item.get("relation_only"):
        return False, "relation_only_not_claim_authority"
    status = str(item.get("status") or "").casefold()
    fact_status = str(item.get("fact_status") or "").casefold()
    if status in {"contradicted", "failed", "rejected"} or fact_status in {
        "contradicted",
        "rejected",
        "invalid",
    }:
        return False, "contradicted_or_rejected_evidence"
    derivation = str(item.get("derivation") or "").casefold()
    review_status = str(item.get("review_status") or "").casefold()
    if derivation in {"llm_inferred", "rule_derived", "heuristic"} and review_status not in {
        "reviewed",
        "confirmed",
        "accepted",
    }:
        return False, "unreviewed_derived_evidence"
    source = str(item.get("source") or "")
    if requested_sources and source not in set(requested_sources):
        return False, "source_scope_mismatch"
    repository_id = item.get("repository_id")
    if repository_ids and repository_id and repository_id not in set(repository_ids):
        return False, "repository_scope_mismatch"
    alignment = str(item.get("version_alignment") or "").casefold()
    if alignment in {"mismatch", "unknown"}:
        return False, "version_scope_untrusted"
    if source == "code" and target_commit:
        if alignment not in {"exact", "compatible", "historical_target"}:
            return False, "code_version_untrusted"
        if not item.get("version"):
            return False, "code_version_missing"
    return True, "scope_supported"


def _claim_is_entailed(claim: str, item: Mapping[str, Any]) -> bool:
    normalized_claim = _CITATION_RE.sub("", claim)
    claim_tokens = _meaningful_tokens(normalized_claim)
    evidence = f"{item.get('title') or ''} {item.get('snippet') or ''}"
    evidence_tokens = _meaningful_tokens(evidence)
    if not claim_tokens or not evidence_tokens:
        return False
    if not _numeric_facts_are_preserved(normalized_claim, evidence):
        return False
    if not _polarity_is_compatible(normalized_claim, evidence):
        return False
    strong_entities = {
        token.casefold()
        for token in _WORD_RE.findall(normalized_claim)
        if len(token) >= 3 and any(marker in token for marker in ("_", ".", "/", "@"))
    }
    evidence_folded = evidence.casefold()
    if any(entity not in evidence_folded for entity in strong_entities):
        return False
    overlap = claim_tokens & evidence_tokens
    required = max(1, math.ceil(min(len(claim_tokens), 6) * 0.67))
    return len(overlap) >= required


def _numeric_facts_are_preserved(claim: str, evidence: str) -> bool:
    claim_values = _number_units(claim)
    if not claim_values:
        return True
    evidence_values = _number_units(evidence)
    for value, unit in claim_values:
        if unit:
            if (value, unit) not in evidence_values:
                return False
        elif not any(candidate == value for candidate, _candidate_unit in evidence_values):
            return False
    return True


def _number_units(value: str) -> set[tuple[Decimal, str]]:
    units = {
        "percent": "%",
        "msec": "ms",
        "sec": "s",
        "second": "s",
        "seconds": "s",
    }
    found: set[tuple[Decimal, str]] = set()
    for number, raw_unit in _NUMBER_UNIT_RE.findall(value):
        try:
            normalized = Decimal(number).normalize()
        except InvalidOperation:
            continue
        unit = units.get(raw_unit.casefold(), raw_unit.casefold())
        found.add((normalized, unit))
    return found


def _polarity_is_compatible(claim: str, evidence: str) -> bool:
    claim_positive = bool(_POSITIVE_STATUS_RE.search(claim))
    claim_negative = bool(_NEGATIVE_STATUS_RE.search(claim))
    evidence_positive = bool(_POSITIVE_STATUS_RE.search(evidence))
    evidence_negative = bool(_NEGATIVE_STATUS_RE.search(evidence))
    if claim_positive and evidence_negative and not evidence_positive:
        return False
    if claim_negative and evidence_positive and not evidence_negative:
        return False
    claim_negated = bool(_NEGATION_RE.search(claim))
    evidence_negated = bool(_NEGATION_RE.search(evidence))
    return claim_negated == evidence_negated or (
        (claim_positive and evidence_positive) or (claim_negative and evidence_negative)
    )


def _meaningful_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for raw_match in _WORD_RE.findall(value.casefold()):
        match = raw_match.strip("._@/+:-")
        if not match:
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", match):
            if len(match) == 1:
                tokens.add(match)
            else:
                tokens.update(match[index : index + 2] for index in range(len(match) - 1))
        elif match not in _WORD_STOP and not re.fullmatch(r"e\d+", match):
            tokens.add(match)
    return tokens


def _normalize_source_status_value(value: str) -> SourceExecutionStatus:
    aliases = {
        "ok": SourceExecutionStatus.COMPLETE,
        "success": SourceExecutionStatus.COMPLETE,
        "completed": SourceExecutionStatus.COMPLETE,
        "complete": SourceExecutionStatus.COMPLETE,
        "partial": SourceExecutionStatus.PARTIAL,
        "timeout": SourceExecutionStatus.TIMEOUT,
        "unavailable": SourceExecutionStatus.UNAVAILABLE,
        "failed": SourceExecutionStatus.UNAVAILABLE,
        "error": SourceExecutionStatus.UNAVAILABLE,
        "unauthorized": SourceExecutionStatus.UNAUTHORIZED,
        "scope_mismatch": SourceExecutionStatus.UNAUTHORIZED,
        "acl_denied": SourceExecutionStatus.UNAUTHORIZED,
        "not_indexed": SourceExecutionStatus.NOT_INDEXED,
        "no_match": SourceExecutionStatus.NO_MATCHING_EVIDENCE,
        "no_matching_evidence": SourceExecutionStatus.NO_MATCHING_EVIDENCE,
    }
    return aliases.get(value, SourceExecutionStatus.COMPLETE)


def _source_reason(status: SourceExecutionStatus) -> str | None:
    return {
        SourceExecutionStatus.COMPLETE: None,
        SourceExecutionStatus.PARTIAL: "source_partial",
        SourceExecutionStatus.TIMEOUT: "source_timeout",
        SourceExecutionStatus.UNAVAILABLE: "source_unavailable",
        SourceExecutionStatus.UNAUTHORIZED: "source_unauthorized",
        SourceExecutionStatus.NOT_INDEXED: "source_not_indexed",
        SourceExecutionStatus.NO_MATCHING_EVIDENCE: "no_matching_evidence",
    }[status]


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 2)
    return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _iter_string_values(value: Any) -> tuple[str, ...]:
    values: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            values.append(item)
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(values)


def _filter_comprehension_context(
    pack: dict[str, Any],
    unsafe_entities: set[str],
) -> None:
    context = pack.get("comprehension_context")
    if not isinstance(context, Mapping):
        return
    cleaned = dict(context)
    evidence = [
        dict(item)
        for item in cleaned.get("evidence") or []
        if str(item.get("entity_id") or "") not in unsafe_entities
    ]
    cleaned["evidence"] = evidence
    cleaned["rendered"] = "\n".join(
        f"[{item.get('citation_id')}] {item.get('role')} {item.get('source')}/"
        f"{item.get('entity_type')}: {item.get('statement')}"
        for item in evidence
    )
    digest_payload = dict(cleaned)
    digest_payload.pop("content_digest", None)
    cleaned["content_digest"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                digest_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    pack["comprehension_context"] = cleaned


__all__ = [
    "ALL_SOURCES",
    "CLAIM_VERIFIER_VERSION",
    "INTERACTION_CONTRACT_VERSION",
    "TRACE_SANITIZER_VERSION",
    "AnswerMode",
    "AnswerabilityDecision",
    "CitationValidation",
    "ClaimCheck",
    "ConversationSnapshot",
    "InteractionMachine",
    "InteractionState",
    "SourceExecutionStatus",
    "UnsafeEvidenceError",
    "assert_outbound_evidence_safe",
    "begin_interaction",
    "build_conversation_snapshot",
    "clarification_for_request",
    "evidence_content_findings",
    "evidence_scope_is_trusted",
    "filter_unsafe_evidence",
    "has_source_failure",
    "normalize_source_status",
    "repair_generated_text",
    "sanitize_trace",
    "select_answer_mode",
    "source_status_for_exception",
    "split_claims",
    "transition",
    "validate_claim_citations",
]
