"""Task-specific, citation-stable Notebook context assembly."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .cell_matcher import NotebookComparisonResult
from .contracts import canonical_sha256
from .retriever import (
    NotebookCandidate,
    NotebookQueryTask,
    NotebookRetrieverV2,
    NotebookSearchScope,
    NotebookSearchTrace,
)
from .store import NotebookV2Store

NOTEBOOK_CONTEXT_BUILDER_VERSION = "notebook-context-builder-v2"


class NotebookContextAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class _FrozenContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class NotebookContextCitation(_FrozenContext):
    citation_id: str
    entity_id: str
    unit_id: str
    locator: str
    content_sha256: str


class NotebookContextBlock(_FrozenContext):
    block_id: str
    section: str
    entity_id: str
    unit_type: str
    locator: str
    content: str
    content_sha256: str
    citation_id: str
    score: float
    truncated: bool
    stale: bool
    unexecuted: bool
    metric_confirmed: bool | None


class NotebookContextMetrics(_FrozenContext):
    retrieved_candidates: int = Field(ge=0)
    selected_blocks: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    used_chars: int = Field(ge=0)
    budget_chars: int = Field(ge=1)
    truncated_blocks: int = Field(ge=0)
    missing_required_sections: int = Field(ge=0)


class NotebookContextBundle(_FrozenContext):
    context_id: str
    builder_version: str = NOTEBOOK_CONTEXT_BUILDER_VERSION
    project_id: str
    allowed_acl_refs: tuple[str, ...]
    generation_id: str | None
    task: NotebookQueryTask
    query_sha256: str
    availability: NotebookContextAvailability
    blocks: tuple[NotebookContextBlock, ...]
    citations: tuple[NotebookContextCitation, ...]
    missing_evidence: tuple[str, ...]
    metrics: NotebookContextMetrics
    retrieval_trace: NotebookSearchTrace
    reasoning_included: bool = False

    @model_validator(mode="after")
    def _identity(self) -> NotebookContextBundle:
        if self.reasoning_included:
            raise ValueError("Notebook context cannot include hidden reasoning")
        citation_ids = {item.citation_id for item in self.citations}
        if citation_ids != {item.citation_id for item in self.blocks}:
            raise ValueError("every context block must have one stable citation")
        payload = self.model_dump(mode="json", exclude={"context_id"})
        expected = "nbcontext-" + canonical_sha256(payload).removeprefix("sha256:")
        if self.context_id != expected:
            raise ValueError("Notebook context id mismatch")
        return self


_REQUIRED_SECTIONS: dict[NotebookQueryTask, tuple[str, ...]] = {
    NotebookQueryTask.SEARCH: ("target",),
    NotebookQueryTask.CODE: ("target_code", "execution_state"),
    NotebookQueryTask.PARAMETER: ("parameter_snapshot",),
    NotebookQueryTask.OUTPUT: ("matched_output", "producer_state"),
    NotebookQueryTask.ERROR: ("failed_output", "execution_state"),
    NotebookQueryTask.LINEAGE: ("lineage_cell",),
    NotebookQueryTask.COMPARE: ("comparison_summary",),
    NotebookQueryTask.REPRODUCTION: ("revision_or_execution", "parameter_snapshot"),
}


def _section(task: NotebookQueryTask, candidate: NotebookCandidate) -> str:
    unit_type = candidate.unit_type
    if task == NotebookQueryTask.ERROR:
        return {
            "error": "failed_output",
            "parameter": "parameter_snapshot",
            "cell": "dependency_cell",
            "cell_execution": "execution_state",
            "execution": "final_status",
        }.get(unit_type, "supporting_evidence")
    if task == NotebookQueryTask.OUTPUT:
        return {
            "output": "matched_output",
            "error": "matched_output",
            "cell_execution": "producer_state",
            "cell": "producer_cell",
            "parameter": "parameter_snapshot",
        }.get(unit_type, "supporting_evidence")
    if task == NotebookQueryTask.CODE:
        return {
            "cell": "target_code",
            "cell_execution": "execution_state",
            "output": "observed_output",
            "error": "state_warning",
        }.get(unit_type, "supporting_evidence")
    if task == NotebookQueryTask.PARAMETER:
        return "parameter_snapshot" if unit_type == "parameter" else "parameter_context"
    if task == NotebookQueryTask.LINEAGE:
        return "lineage_cell" if unit_type == "cell" else "lineage_support"
    if task == NotebookQueryTask.REPRODUCTION:
        return {
            "revision": "revision_or_execution",
            "execution": "revision_or_execution",
            "parameter": "parameter_snapshot",
            "error": "missing_dependency_warning",
            "cell": "code_dependency",
        }.get(unit_type, "supporting_evidence")
    if task == NotebookQueryTask.COMPARE:
        return {
            "comparison": "comparison_summary",
            "comparison_match": "matched_cells",
            "parameter_change": "parameter_diff",
        }.get(unit_type, "comparison_candidate")
    return "target"


def _line_safe_truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    if limit <= 1:
        return "…"[:limit], True
    marker = "\n…[truncated]"
    if limit <= len(marker):
        return value[: limit - 1] + "…", True
    prefix = value[: limit - len(marker)]
    newline = prefix.rfind("\n")
    if newline > 0:
        prefix = prefix[:newline]
    return prefix.rstrip() + marker, True


def _citation_id(unit_id: str, content_sha256: str) -> str:
    return "nbcite-" + hashlib.sha256(f"{unit_id}\x1f{content_sha256}".encode()).hexdigest()


def _candidate_block(
    *,
    task: NotebookQueryTask,
    candidate: NotebookCandidate,
    remaining: int,
) -> tuple[NotebookContextBlock | None, NotebookContextCitation | None]:
    if remaining <= 0:
        return None, None
    content, truncated = _line_safe_truncate(candidate.content, remaining)
    if not content:
        return None, None
    content_sha256 = canonical_sha256(
        {
            "source_content_sha256": candidate.content_sha256,
            "content": content,
            "truncated": truncated,
        }
    )
    citation_id = _citation_id(candidate.unit_id, content_sha256)
    section = _section(task, candidate)
    block_id = (
        "nbblock-"
        + hashlib.sha256(
            f"{section}\x1f{candidate.unit_id}\x1f{content_sha256}".encode()
        ).hexdigest()
    )
    block = NotebookContextBlock(
        block_id=block_id,
        section=section,
        entity_id=candidate.entity_id,
        unit_type=candidate.unit_type,
        locator=candidate.locator,
        content=content,
        content_sha256=content_sha256,
        citation_id=citation_id,
        score=candidate.score,
        truncated=truncated,
        stale=candidate.stale,
        unexecuted=candidate.unexecuted,
        metric_confirmed=(False if candidate.unit_type in {"output", "error"} else None),
    )
    citation = NotebookContextCitation(
        citation_id=citation_id,
        entity_id=candidate.entity_id,
        unit_id=candidate.unit_id,
        locator=candidate.locator,
        content_sha256=content_sha256,
    )
    return block, citation


def _comparison_blocks(
    comparison: NotebookComparisonResult,
) -> tuple[NotebookCandidate, ...]:
    summary = (
        f"comparison {comparison.baseline_revision_id} -> "
        f"{comparison.candidate_revision_id}; status "
        f"{comparison.baseline_status} -> {comparison.candidate_status}; "
        f"matches {len(comparison.matches)}; ambiguous "
        f"{comparison.ambiguous_pair_count}"
    )
    values: list[tuple[str, str, str, str]] = [
        (
            comparison.comparison_id,
            "comparison",
            comparison.locator,
            summary,
        )
    ]
    for match in comparison.matches:
        locator = match.candidate_locator or match.baseline_locator
        if locator is None:
            continue
        entity_id = (
            match.candidate_cell_version_id
            or match.baseline_cell_version_id
            or comparison.comparison_id
        )
        values.append(
            (
                entity_id,
                "comparison_match",
                locator,
                "cell change "
                + ",".join(str(item) for item in match.change_types)
                + f"; method {match.method}; confidence {match.confidence}",
            )
        )
    for parameter in comparison.parameter_changes:
        if parameter.change == "unchanged":
            continue
        values.append(
            (
                f"{comparison.comparison_id}-{parameter.name}",
                "parameter_change",
                comparison.locator,
                f"parameter {parameter.name} change {parameter.change}; "
                f"type {parameter.baseline_type or 'missing'} -> "
                f"{parameter.candidate_type or 'missing'}",
            )
        )
    return tuple(
        NotebookCandidate(
            unit_id="nbcompareunit-"
            + hashlib.sha256(f"{entity_id}\x1f{content}".encode()).hexdigest(),
            entity_id=entity_id,
            unit_type=unit_type,
            profile="comparison-v2",
            revision_id=comparison.candidate_revision_id,
            execution_id=comparison.candidate_execution_id,
            locator=locator,
            content=content,
            content_sha256=canonical_sha256(content),
            score=10.0 if unit_type == "comparison" else 5.0,
            channels=(),
            reasons=("cell-matcher",),
            stale=False,
            unexecuted=False,
        )
        for entity_id, unit_type, locator, content in values
    )


class NotebookContextBuilderV2:
    def __init__(self, store: NotebookV2Store) -> None:
        self.store = store
        self.retriever = NotebookRetrieverV2(store)

    def build(
        self,
        *,
        scope: NotebookSearchScope,
        query: str,
        max_chars: int = 12_000,
        limit: int | None = None,
        comparison: NotebookComparisonResult | None = None,
    ) -> NotebookContextBundle:
        if not 256 <= max_chars <= 100_000:
            raise ValueError("Notebook context budget must be between 256 and 100000 chars")
        search = self.retriever.search(scope=scope, query=query, limit=limit)
        if comparison is not None and (
            comparison.project_id != scope.project_id
            or comparison.acl_ref not in scope.allowed_acl_refs
            or (scope.generation_id is not None and comparison.generation_id != scope.generation_id)
        ):
            raise ValueError("Notebook comparison is outside governed context scope")
        candidates = list(search.candidates)
        if search.trace.task == NotebookQueryTask.COMPARE and comparison is not None:
            candidates = [*_comparison_blocks(comparison), *candidates]

        blocks: list[NotebookContextBlock] = []
        citations: list[NotebookContextCitation] = []
        seen_entities: set[tuple[str, str]] = set()
        remaining = max_chars
        for candidate in candidates:
            identity = (candidate.entity_id, candidate.content_sha256)
            if identity in seen_entities:
                continue
            block, citation = _candidate_block(
                task=search.trace.task,
                candidate=candidate,
                remaining=remaining,
            )
            if block is None or citation is None:
                break
            blocks.append(block)
            citations.append(citation)
            seen_entities.add(identity)
            remaining -= len(block.content)
            if block.truncated:
                break

        present_sections = {item.section for item in blocks}
        required = _REQUIRED_SECTIONS[search.trace.task]
        missing = tuple(
            f"missing:{section}" for section in required if section not in present_sections
        )
        if not blocks:
            availability = NotebookContextAvailability.UNAVAILABLE
        elif missing:
            availability = NotebookContextAvailability.PROVISIONAL
        else:
            availability = NotebookContextAvailability.AVAILABLE
        metrics = NotebookContextMetrics(
            retrieved_candidates=len(search.candidates),
            selected_blocks=len(blocks),
            citation_count=len(citations),
            used_chars=sum(len(item.content) for item in blocks),
            budget_chars=max_chars,
            truncated_blocks=sum(item.truncated for item in blocks),
            missing_required_sections=len(missing),
        )
        values = {
            "builder_version": NOTEBOOK_CONTEXT_BUILDER_VERSION,
            "project_id": scope.project_id,
            "allowed_acl_refs": scope.allowed_acl_refs,
            "generation_id": scope.generation_id,
            "task": search.trace.task,
            "query_sha256": search.trace.query_sha256,
            "availability": availability,
            "blocks": tuple(blocks),
            "citations": tuple(citations),
            "missing_evidence": missing,
            "metrics": metrics,
            "retrieval_trace": search.trace,
        }
        draft = NotebookContextBundle.model_construct(
            context_id="nbcontext-" + ("0" * 64),
            **values,
        )
        context_id = "nbcontext-" + canonical_sha256(
            draft.model_dump(mode="json", exclude={"context_id"})
        ).removeprefix("sha256:")
        return NotebookContextBundle(context_id=context_id, **values)


__all__ = [
    "NOTEBOOK_CONTEXT_BUILDER_VERSION",
    "NotebookContextAvailability",
    "NotebookContextBlock",
    "NotebookContextBuilderV2",
    "NotebookContextBundle",
    "NotebookContextCitation",
    "NotebookContextMetrics",
]
