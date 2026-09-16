"""Task-specific Codex context and normalized comparison projection."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_sha256
from .retrieval_v2 import (
    CodexCandidateNecessity,
    CodexQueryTask,
    CodexRetrievalCandidateV2,
    CodexRetrievalResultV2,
)
from .units_v2 import (
    CodexEvidenceLevel,
    CodexRetrievalPublicationV2,
    CodexRetrievalUnitV2,
    CodexUnitRole,
)

CODEX_CONTEXT_CONTRACT_VERSION = "codex-task-context-contract-v2"
CODEX_CONTEXT_BUILDER_VERSION = "codex-task-context-builder-v2"
CODEX_COMPARISON_VERSION = "codex-normalized-comparison-v2"


class _FrozenContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CodexContextStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class CodexContextWarning(StrEnum):
    CANDIDATE_ONLY = "candidate_only"
    UNVERIFIED_STATE = "unverified_state"
    FAILED_OR_UNKNOWN = "failed_or_unknown"
    BUDGET_TRUNCATED = "budget_truncated"
    MISSING_OBSERVABLE_EVIDENCE = "missing_observable_evidence"
    RATIONALE_IS_OBSERVABLE_ONLY = "rationale_is_observable_only"
    TARGET_UNKNOWN = "target_unknown"
    EVENT_ORDER_GAP = "event_order_gap"


class CodexContextBlockV2(_FrozenContext):
    block_id: str
    citation_id: str
    unit_id: str
    role: CodexUnitRole
    state: str
    evidence_level: CodexEvidenceLevel
    episode_id: str | None
    text: str = Field(min_length=1, max_length=16_384)
    source_item_ids: tuple[str, ...] = Field(min_length=1)
    source_item_sha256s: tuple[str, ...] = Field(min_length=1)
    source_locators: tuple[str, ...] = Field(min_length=1)
    command_argv: tuple[str, ...]
    targets: tuple[str, ...]
    tool_names: tuple[str, ...]
    ordinal: int = Field(ge=0)
    candidate_trace_sha256: str
    relevance_probability: float = Field(ge=0, le=1)
    necessity: CodexCandidateNecessity
    graph_path: tuple[str, ...]
    warnings: tuple[CodexContextWarning, ...]
    truncated: bool
    content_sha256: str
    contract_version: str = CODEX_CONTEXT_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexContextBlockV2:
        if self.warnings != tuple(sorted(set(self.warnings), key=str)):
            raise ValueError("context warnings must be sorted and unique")
        content = canonical_sha256(
            self.model_dump(
                mode="json",
                exclude={"block_id", "citation_id", "content_sha256"},
            )
        )
        expected_block = "codexblock-" + canonical_sha256(
            {"unit_id": self.unit_id, "content_sha256": content}
        ).removeprefix("sha256:")
        expected_citation = "codexcite-" + canonical_sha256(
            {
                "unit_id": self.unit_id,
                "source_locators": self.source_locators,
                "content_sha256": content,
            }
        ).removeprefix("sha256:")
        if (
            self.content_sha256 != content
            or self.block_id != expected_block
            or self.citation_id != expected_citation
        ):
            raise ValueError("context block identity mismatch")
        return self


class CodexTaskContextV2(_FrozenContext):
    task: CodexQueryTask
    status: CodexContextStatus
    title: str
    blocks: tuple[CodexContextBlockV2, ...]
    warnings: tuple[CodexContextWarning, ...]
    missing_roles: tuple[CodexUnitRole, ...]
    selected_chars: int = Field(ge=0)
    budget_chars: int = Field(ge=256, le=262_144)
    candidate_count: int = Field(ge=0)
    selected_candidate_count: int = Field(ge=0)
    dropped_candidate_count: int = Field(ge=0)
    citation_coverage: float = Field(ge=0, le=1)
    event_order_valid: bool
    retrieval_result_sha256: str
    publication_sha256: str
    reasoning_included: bool = False
    context_sha256: str
    builder_version: str = CODEX_CONTEXT_BUILDER_VERSION
    contract_version: str = CODEX_CONTEXT_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexTaskContextV2:
        if self.reasoning_included:
            raise ValueError("hidden reasoning cannot enter Codex context")
        if self.selected_chars != sum(len(item.text) for item in self.blocks):
            raise ValueError("selected context character count mismatch")
        if self.selected_chars > self.budget_chars:
            raise ValueError("context exceeds its deterministic budget")
        if self.selected_candidate_count != len(self.blocks):
            raise ValueError("selected context candidate count mismatch")
        if self.selected_candidate_count + self.dropped_candidate_count != self.candidate_count:
            raise ValueError("context candidate accounting mismatch")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"context_sha256"}))
        if self.context_sha256 != expected:
            raise ValueError("context identity mismatch")
        return self


class CodexComparisonEntryV2(_FrozenContext):
    label: str
    context_sha256: str
    status: CodexContextStatus
    block_count: int = Field(ge=0)
    episode_ids: tuple[str, ...]
    observed_roles: tuple[CodexUnitRole, ...]
    citation_ids: tuple[str, ...]
    goal_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    changed_targets: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]
    validation_states: tuple[str, ...]
    outcome_states: tuple[str, ...]
    open_issue_count: int = Field(ge=0)


class CodexComparisonProjectionV2(_FrozenContext):
    entries: tuple[CodexComparisonEntryV2, ...] = Field(min_length=2)
    shared_roles: tuple[CodexUnitRole, ...]
    divergent_roles: tuple[CodexUnitRole, ...]
    projection_sha256: str
    version: str = CODEX_COMPARISON_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexComparisonProjectionV2:
        labels = tuple(item.label for item in self.entries)
        if labels != tuple(sorted(set(labels))):
            raise ValueError("comparison labels must be sorted and unique")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"projection_sha256"}))
        if self.projection_sha256 != expected:
            raise ValueError("comparison projection identity mismatch")
        return self


_TASK_TITLES = {
    CodexQueryTask.PROCESS: "Observable implementation process",
    CodexQueryTask.RATIONALE: "Observable decisions and alternatives",
    CodexQueryTask.VALIDATION: "Observable validation evidence",
    CodexQueryTask.FAILURE_RETRY: "Observable failure and retry history",
}
_REQUIRED_ROLES = {
    CodexQueryTask.PROCESS: {
        CodexUnitRole.GOAL,
        CodexUnitRole.ACTION,
        CodexUnitRole.OUTCOME,
    },
    CodexQueryTask.RATIONALE: {
        CodexUnitRole.GOAL,
        CodexUnitRole.DECISION,
    },
    CodexQueryTask.VALIDATION: {
        CodexUnitRole.VALIDATION,
    },
    CodexQueryTask.FAILURE_RETRY: {
        CodexUnitRole.FAILURE,
        CodexUnitRole.ACTION,
    },
}


def _line_safe_prefix(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    if limit < 2:
        return "", True
    prefix = value[: limit - 1]
    newline = prefix.rfind("\n")
    if newline >= max(0, len(prefix) // 2):
        prefix = prefix[:newline]
    else:
        space = prefix.rfind(" ")
        if space >= max(0, len(prefix) // 2):
            prefix = prefix[:space]
    return prefix.rstrip() + "…", True


def _block(
    unit: CodexRetrievalUnitV2,
    candidate: CodexRetrievalCandidateV2,
    *,
    text: str,
    truncated: bool,
) -> CodexContextBlockV2:
    warnings: set[CodexContextWarning] = set()
    if unit.evidence_level is CodexEvidenceLevel.CANDIDATE_ONLY:
        warnings.add(CodexContextWarning.CANDIDATE_ONLY)
    if unit.state in {"unknown", "unlinked", "claim_only", "plan_only"}:
        warnings.add(CodexContextWarning.UNVERIFIED_STATE)
    if unit.state == "target_unknown":
        warnings.add(CodexContextWarning.TARGET_UNKNOWN)
    if unit.role is CodexUnitRole.FAILURE or unit.state in {
        "failed",
        "timeout",
        "cancelled",
    }:
        warnings.add(CodexContextWarning.FAILED_OR_UNKNOWN)
    if truncated:
        warnings.add(CodexContextWarning.BUDGET_TRUNCATED)
    if "candidate_only" in candidate.hard_negative_reasons:
        warnings.add(CodexContextWarning.CANDIDATE_ONLY)
    payload: dict[str, Any] = {
        "unit_id": unit.unit_id,
        "role": unit.role,
        "state": unit.state,
        "evidence_level": unit.evidence_level,
        "episode_id": unit.episode_id,
        "text": text,
        "source_item_ids": unit.source_item_ids,
        "source_item_sha256s": unit.source_item_sha256s,
        "source_locators": unit.source_locators,
        "command_argv": unit.command_argv,
        "targets": unit.targets,
        "tool_names": unit.tool_names,
        "ordinal": unit.ordinal,
        "candidate_trace_sha256": candidate.trace_sha256,
        "relevance_probability": candidate.relevance_probability,
        "necessity": candidate.necessity,
        "graph_path": candidate.graph_path,
        "warnings": tuple(sorted(warnings, key=str)),
        "truncated": truncated,
        "contract_version": CODEX_CONTEXT_CONTRACT_VERSION,
    }
    content = canonical_sha256(payload)
    return CodexContextBlockV2(
        block_id="codexblock-"
        + canonical_sha256({"unit_id": unit.unit_id, "content_sha256": content}).removeprefix(
            "sha256:"
        ),
        citation_id="codexcite-"
        + canonical_sha256(
            {
                "unit_id": unit.unit_id,
                "source_locators": unit.source_locators,
                "content_sha256": content,
            }
        ).removeprefix("sha256:"),
        content_sha256=content,
        **payload,
    )


def build_codex_task_context_v2(
    publication: CodexRetrievalPublicationV2,
    retrieval: CodexRetrievalResultV2,
    *,
    budget_chars: int = 12_000,
) -> CodexTaskContextV2:
    """Pack deterministic context from selected units and stable source citations."""

    publication = CodexRetrievalPublicationV2.model_validate(
        publication.model_dump(mode="python", round_trip=True)
    )
    retrieval = CodexRetrievalResultV2.model_validate(
        retrieval.model_dump(mode="python", round_trip=True)
    )
    if retrieval.publication_sha256 != publication.publication_sha256:
        raise ValueError("retrieval result is not bound to this publication")
    if budget_chars < 256 or budget_chars > 262_144:
        raise ValueError("context budget is outside the frozen bound")
    unit_by_id = {item.unit_id: item for item in publication.units}
    eligible: list[tuple[CodexRetrievalUnitV2, CodexRetrievalCandidateV2]] = []
    seen_units: set[str] = set()
    for candidate in retrieval.candidates:
        unit = unit_by_id.get(candidate.unit_id)
        if unit is None:
            raise ValueError("retrieval candidate references an unknown unit")
        if unit.role in {CodexUnitRole.THREAD, CodexUnitRole.EPISODE}:
            continue
        if unit.unit_id in seen_units:
            raise ValueError("retrieval result repeats a selected unit")
        seen_units.add(unit.unit_id)
        eligible.append((unit, candidate))
    eligible.sort(key=lambda pair: (pair[0].ordinal, pair[0].unit_id))
    blocks: list[CodexContextBlockV2] = []
    remaining = budget_chars
    for unit, candidate in eligible:
        if remaining < 32:
            break
        text, truncated = _line_safe_prefix(unit.search_text, remaining)
        if not text:
            break
        blocks.append(
            _block(
                unit,
                candidate,
                text=text,
                truncated=truncated,
            )
        )
        remaining -= len(text)
        if truncated:
            break
    observed_roles = {item.role for item in blocks}
    missing = tuple(sorted(_REQUIRED_ROLES[retrieval.task] - observed_roles, key=str))
    warnings = {warning for block in blocks for warning in block.warnings}
    if not blocks:
        warnings.add(CodexContextWarning.MISSING_OBSERVABLE_EVIDENCE)
    dropped = len(eligible) - len(blocks)
    if dropped:
        warnings.add(CodexContextWarning.BUDGET_TRUNCATED)
    event_order_valid = tuple(item.ordinal for item in blocks) == tuple(
        sorted(item.ordinal for item in blocks)
    )
    if not event_order_valid:
        warnings.add(CodexContextWarning.EVENT_ORDER_GAP)
    if retrieval.task is CodexQueryTask.RATIONALE:
        warnings.add(CodexContextWarning.RATIONALE_IS_OBSERVABLE_ONLY)
    if not blocks:
        status = CodexContextStatus.UNAVAILABLE
    elif missing or any(
        warning
        in {
            CodexContextWarning.CANDIDATE_ONLY,
            CodexContextWarning.UNVERIFIED_STATE,
            CodexContextWarning.BUDGET_TRUNCATED,
        }
        for warning in warnings
    ):
        status = CodexContextStatus.PROVISIONAL
    else:
        status = CodexContextStatus.AVAILABLE
    values = {
        "task": retrieval.task,
        "status": status,
        "title": _TASK_TITLES[retrieval.task],
        "blocks": tuple(blocks),
        "warnings": tuple(sorted(warnings, key=str)),
        "missing_roles": missing,
        "selected_chars": sum(len(item.text) for item in blocks),
        "budget_chars": budget_chars,
        "candidate_count": len(eligible),
        "selected_candidate_count": len(blocks),
        "dropped_candidate_count": dropped,
        "citation_coverage": (len(blocks) / len(eligible) if eligible else 0.0),
        "event_order_valid": event_order_valid,
        "retrieval_result_sha256": retrieval.result_sha256,
        "publication_sha256": publication.publication_sha256,
        "reasoning_included": False,
        "builder_version": CODEX_CONTEXT_BUILDER_VERSION,
        "contract_version": CODEX_CONTEXT_CONTRACT_VERSION,
    }
    return CodexTaskContextV2(
        **values,
        context_sha256=canonical_sha256(
            {
                key: (
                    [item.model_dump(mode="json") for item in value]
                    if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
                    else value
                )
                for key, value in values.items()
            }
        ),
    )


def project_codex_comparison_v2(
    contexts: dict[str, CodexTaskContextV2],
) -> CodexComparisonProjectionV2:
    if len(contexts) < 2:
        raise ValueError("comparison requires at least two independently built contexts")
    entries: list[CodexComparisonEntryV2] = []
    role_sets: list[set[CodexUnitRole]] = []
    for label, context in sorted(contexts.items()):
        if not label or label != label.strip() or "/" in label or "\\" in label:
            raise ValueError("comparison label must be opaque and portable")
        roles = {item.role for item in context.blocks}
        role_sets.append(roles)
        entries.append(
            CodexComparisonEntryV2(
                label=label,
                context_sha256=context.context_sha256,
                status=context.status,
                block_count=len(context.blocks),
                episode_ids=tuple(
                    sorted(
                        {item.episode_id for item in context.blocks if item.episode_id is not None}
                    )
                ),
                observed_roles=tuple(sorted(roles, key=str)),
                citation_ids=tuple(item.citation_id for item in context.blocks),
                goal_count=sum(item.role is CodexUnitRole.GOAL for item in context.blocks),
                decision_count=sum(item.role is CodexUnitRole.DECISION for item in context.blocks),
                changed_targets=tuple(
                    sorted(
                        {
                            target
                            for item in context.blocks
                            if item.role is CodexUnitRole.CHANGE
                            for target in item.targets
                        }
                    )
                ),
                commands=tuple(
                    dict.fromkeys(item.command_argv for item in context.blocks if item.command_argv)
                ),
                validation_states=tuple(
                    item.state for item in context.blocks if item.role is CodexUnitRole.VALIDATION
                ),
                outcome_states=tuple(
                    item.state
                    for item in context.blocks
                    if item.role in {CodexUnitRole.OUTCOME, CodexUnitRole.FAILURE}
                ),
                open_issue_count=sum(
                    item.role is CodexUnitRole.FAILURE
                    or item.state
                    in {
                        "failed",
                        "timeout",
                        "cancelled",
                        "unknown",
                        "unlinked",
                        "target_unknown",
                    }
                    for item in context.blocks
                ),
            )
        )
    shared = set.intersection(*role_sets) if role_sets else set()
    union = set.union(*role_sets) if role_sets else set()
    values = {
        "entries": tuple(entries),
        "shared_roles": tuple(sorted(shared, key=str)),
        "divergent_roles": tuple(sorted(union - shared, key=str)),
        "version": CODEX_COMPARISON_VERSION,
    }
    return CodexComparisonProjectionV2(
        **values,
        projection_sha256=canonical_sha256(
            {
                key: (
                    [item.model_dump(mode="json") for item in value] if key == "entries" else value
                )
                for key, value in values.items()
            }
        ),
    )


__all__ = [
    "CODEX_COMPARISON_VERSION",
    "CODEX_CONTEXT_BUILDER_VERSION",
    "CODEX_CONTEXT_CONTRACT_VERSION",
    "CodexComparisonEntryV2",
    "CodexComparisonProjectionV2",
    "CodexContextBlockV2",
    "CodexContextStatus",
    "CodexContextWarning",
    "CodexTaskContextV2",
    "build_codex_task_context_v2",
    "project_codex_comparison_v2",
]
