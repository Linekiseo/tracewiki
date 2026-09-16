"""Context assembly for an already filtered Notebook retrieval result."""

from __future__ import annotations

from .cell_matcher import NotebookComparisonResult
from .context_builder import (
    _REQUIRED_SECTIONS,
    NOTEBOOK_CONTEXT_BUILDER_VERSION,
    NotebookContextAvailability,
    NotebookContextBundle,
    NotebookContextMetrics,
    _candidate_block,
    _comparison_blocks,
)
from .contracts import canonical_sha256
from .retriever import (
    NotebookQueryTask,
    NotebookSearchResult,
    NotebookSearchScope,
)


def build_notebook_context_from_search_v2(
    *,
    scope: NotebookSearchScope,
    search: NotebookSearchResult,
    max_chars: int = 12_000,
    comparison: NotebookComparisonResult | None = None,
) -> NotebookContextBundle:
    """Build context from the exact filter-before-rank candidate set."""

    if not 256 <= max_chars <= 100_000:
        raise ValueError("Notebook context budget must be between 256 and 100000 chars")
    if comparison is not None and (
        comparison.project_id != scope.project_id
        or comparison.acl_ref not in scope.allowed_acl_refs
        or (scope.generation_id is not None and comparison.generation_id != scope.generation_id)
    ):
        raise ValueError("Notebook comparison is outside governed context scope")
    candidates = list(search.candidates)
    if search.trace.task == NotebookQueryTask.COMPARE and comparison is not None:
        candidates = [*_comparison_blocks(comparison), *candidates]

    blocks = []
    citations = []
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
    missing = tuple(
        f"missing:{section}"
        for section in _REQUIRED_SECTIONS[search.trace.task]
        if section not in present_sections
    )
    availability = (
        NotebookContextAvailability.UNAVAILABLE
        if not blocks
        else NotebookContextAvailability.PROVISIONAL
        if missing
        else NotebookContextAvailability.AVAILABLE
    )
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


__all__ = ["build_notebook_context_from_search_v2"]
