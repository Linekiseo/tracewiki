"""Independent exact/sparse/graph Notebook Retriever V2."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ....platform.models import NotebookQueryTaskV2
from .contracts import NotebookPublication, NotebookRetrievalUnit
from .reranker import (
    NOTEBOOK_RERANKER_VERSION,
    NotebookRerankFeatures,
    rerank_notebook_candidate_v2,
)
from .store import NotebookV2Store

NOTEBOOK_RETRIEVER_VERSION = "notebook-retriever-v2"
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*|[\u3400-\u9fff]{2,}")
_TOKEN_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "cell",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "locate",
    "notebook",
    "of",
    "or",
    "output",
    "revision",
    "run",
    "show",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "what",
    "where",
    "which",
    "with",
}
_TOKEN_ALIASES = {
    "added": "add",
    "calculated": "calculate",
    "calculates": "calculate",
    "completed": "complete",
    "computed": "compute",
    "configured": "config",
    "defines": "define",
    "defined": "define",
    "executed": "execute",
    "executions": "execute",
    "failed": "fail",
    "failure": "fail",
    "parameters": "parameter",
    "produced": "produce",
    "producing": "produce",
    "recover": "success",
    "recovery": "success",
    "records": "record",
    "revisions": "revision",
    "runs": "run",
    "succeeded": "success",
    "successful": "success",
}


NotebookQueryTask = NotebookQueryTaskV2


class NotebookChannel(StrEnum):
    EXACT = "exact"
    STRUCTURED = "structured"
    SPARSE = "sparse"
    GRAPH = "graph"
    DENSE = "dense"


class _FrozenSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class NotebookSearchScope(_FrozenSearch):
    project_id: str
    allowed_acl_refs: tuple[str, ...]
    generation_id: str | None = None

    @model_validator(mode="after")
    def _scope(self) -> NotebookSearchScope:
        if not self.project_id or not self.allowed_acl_refs:
            raise ValueError("Notebook search requires project and non-empty ACL authority")
        if len(set(self.allowed_acl_refs)) != len(self.allowed_acl_refs):
            raise ValueError("ACL authority must be unique")
        return self


class NotebookFilterError(ValueError):
    """A bounded fail-closed error for an exact Notebook filter."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class NotebookSearchFiltersV2(_FrozenSearch):
    template_ids: tuple[str, ...] = ()
    revision_ids: tuple[str, ...] = ()
    execution_ids: tuple[str, ...] = ()
    cell_ids: tuple[str, ...] = ()
    statuses: tuple[Literal["completed", "failed", "cancelled", "unknown"], ...] = ()
    parameter_names: tuple[str, ...] = ()
    parameter_ids: tuple[str, ...] = ()
    output_ids: tuple[str, ...] = ()
    output_types: tuple[
        Literal["stream", "display", "execute_result", "error", "binary_omitted"], ...
    ] = ()

    @model_validator(mode="after")
    def _canonical(self) -> NotebookSearchFiltersV2:
        for values, label, maximum in (
            (self.template_ids, "template IDs", 50),
            (self.revision_ids, "revision IDs", 50),
            (self.execution_ids, "execution IDs", 50),
            (self.cell_ids, "cell IDs", 100),
            (self.statuses, "statuses", 4),
            (self.parameter_names, "parameter names", 50),
            (self.parameter_ids, "parameter IDs", 100),
            (self.output_ids, "output IDs", 100),
            (self.output_types, "output types", 5),
        ):
            if len(values) > maximum:
                raise ValueError(f"too many Notebook {label}")
            if values != tuple(sorted(set(values))):
                raise ValueError(f"Notebook {label} must be sorted and unique")
        return self


class NotebookFilterSelectionTraceV2(_FrozenSearch):
    schema_version: str = "notebook-filter-selection-trace-v2"
    filter_before_rank: bool = True
    requested_filters: dict[str, tuple[str, ...]]
    publication_count_before_filter: int = Field(ge=0)
    publication_count_after_filter: int = Field(ge=0)
    unit_count_before_filter: int = Field(ge=0)
    unit_count_after_filter: int = Field(ge=0)
    selected_template_ids: tuple[str, ...]
    selected_revision_ids: tuple[str, ...]
    selected_execution_ids: tuple[str, ...]


class NotebookQueryProfile(_FrozenSearch):
    task: NotebookQueryTask
    tokens: tuple[str, ...]
    preferred_unit_types: tuple[str, ...]
    adaptive_limit: int = Field(ge=1, le=100)
    abstention_reason: str | None = None
    query_sha256: str


class NotebookCandidate(_FrozenSearch):
    unit_id: str
    entity_id: str
    unit_type: str
    profile: str
    revision_id: str
    execution_id: str | None
    locator: str
    content: str
    content_sha256: str
    score: float
    channels: tuple[NotebookChannel, ...]
    reasons: tuple[str, ...]
    stale: bool
    unexecuted: bool


class NotebookChannelTrace(_FrozenSearch):
    channel: NotebookChannel
    status: str
    candidate_count: int


class NotebookSearchTrace(_FrozenSearch):
    retriever_version: str
    reranker_version: str
    query_sha256: str
    task: NotebookQueryTask
    publication_count: int
    unit_count: int
    selected_count: int
    channels: tuple[NotebookChannelTrace, ...]
    selection_trace: NotebookFilterSelectionTraceV2
    abstention_reason: str | None = None
    reasoning_included: bool = False


class NotebookSearchResult(_FrozenSearch):
    candidates: tuple[NotebookCandidate, ...] = Field(max_length=100)
    trace: NotebookSearchTrace


def _tokens(value: str) -> tuple[str, ...]:
    tokens: set[str] = set()
    for match in _TOKEN_RE.finditer(value):
        raw = match.group(0).casefold()
        parts = (raw, *re.split(r"[_.-]+", raw))
        for part in parts:
            normalized = _TOKEN_ALIASES.get(part, part)
            if len(normalized) > 1 and normalized not in _TOKEN_STOP_WORDS:
                tokens.add(normalized)
    return tuple(sorted(tokens))


def profile_notebook_query_v2(
    query: str,
    *,
    task_override: NotebookQueryTask | None = None,
) -> NotebookQueryProfile:
    lowered = query.casefold()
    task_markers = (
        (NotebookQueryTask.ERROR, ("error", "exception", "failed", "retry", "错误", "失败")),
        (NotebookQueryTask.COMPARE, ("compare", "changed", "moved", "revision", "对比", "变化")),
        (
            NotebookQueryTask.REPRODUCTION,
            ("reproduce", "reproduction", "rerun", "environment", "复现", "重跑"),
        ),
        (
            NotebookQueryTask.LINEAGE,
            (
                "lineage",
                "producer",
                "depends",
                "dependency",
                "dataflow",
                "trace",
                "依赖",
                "血缘",
            ),
        ),
        (NotebookQueryTask.PARAMETER, ("parameter", "config", "seed", "参数", "配置")),
        (NotebookQueryTask.OUTPUT, ("output", "result", "table", "figure", "输出", "结果")),
        (NotebookQueryTask.CODE, ("code", "function", "variable", "define", "代码", "函数")),
    )
    task = NotebookQueryTask.SEARCH
    for candidate, markers in task_markers:
        if any(marker in lowered for marker in markers):
            task = candidate
            break
    if task_override is not None:
        task = task_override
    preferred = {
        NotebookQueryTask.SEARCH: ("cell", "execution", "revision"),
        NotebookQueryTask.CODE: ("cell", "cell_execution"),
        NotebookQueryTask.PARAMETER: ("parameter", "cell"),
        NotebookQueryTask.OUTPUT: ("output", "cell_execution", "cell"),
        NotebookQueryTask.ERROR: ("error", "execution", "cell_execution", "cell"),
        NotebookQueryTask.LINEAGE: ("cell", "parameter", "cell_execution"),
        NotebookQueryTask.COMPARE: ("revision", "cell", "execution"),
        NotebookQueryTask.REPRODUCTION: ("revision", "execution", "parameter", "error"),
    }[task]
    adaptive_limit = {
        NotebookQueryTask.SEARCH: 8,
        NotebookQueryTask.CODE: 12,
        NotebookQueryTask.PARAMETER: 8,
        NotebookQueryTask.OUTPUT: 10,
        NotebookQueryTask.ERROR: 12,
        NotebookQueryTask.LINEAGE: 14,
        NotebookQueryTask.COMPARE: 16,
        NotebookQueryTask.REPRODUCTION: 16,
    }[task]
    unsafe_disclosure = any(
        marker in lowered
        for marker in (
            "reveal the private",
            "absolute input path",
            "show secret",
            "show credential",
            "leak token",
            "泄露密钥",
        )
    )
    unconfirmed_metric = "confirmed" in lowered and "experiment" in lowered and "metric" in lowered
    return NotebookQueryProfile(
        task=task,
        tokens=_tokens(query),
        preferred_unit_types=preferred,
        adaptive_limit=adaptive_limit,
        abstention_reason=(
            "unsafe-disclosure-request"
            if unsafe_disclosure
            else "notebook-metric-binding-unconfirmed"
            if unconfirmed_metric
            else None
        ),
        query_sha256="sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest(),
    )


def _current_revisions(publications: tuple[NotebookPublication, ...]) -> set[str]:
    by_template: dict[str, list[NotebookPublication]] = defaultdict(list)
    for publication in publications:
        by_template[publication.template.template_id].append(publication)
    return {
        max(
            items,
            key=lambda item: (
                item.revision.source_version,
                item.execution.execution_key,
                item.publication_id,
            ),
        ).revision.revision_id
        for items in by_template.values()
    }


def _state_maps(
    publications: tuple[NotebookPublication, ...],
) -> tuple[dict[str, bool], dict[str, bool]]:
    stale: dict[str, bool] = {}
    unexecuted: dict[str, bool] = {}
    for publication in publications:
        artifacts_by_cell = defaultdict(list)
        for artifact in publication.artifacts:
            artifacts_by_cell[artifact.cell_execution_id].append(artifact.artifact_id)
        for observed in publication.cell_executions:
            stale[observed.cell_execution_id] = observed.stale
            unexecuted[observed.cell_execution_id] = observed.state == "not_executed"
            for artifact_id in artifacts_by_cell[observed.cell_execution_id]:
                stale[artifact_id] = observed.stale
                unexecuted[artifact_id] = observed.state == "not_executed"
        for cell in publication.cell_versions:
            matching = [
                item
                for item in publication.cell_executions
                if item.cell_version_id == cell.cell_version_id
            ]
            if matching:
                stale[cell.cell_version_id] = stale.get(cell.cell_version_id, False) or any(
                    item.stale for item in matching
                )
                unexecuted[cell.cell_version_id] = unexecuted.get(
                    cell.cell_version_id, True
                ) and all(item.state == "not_executed" for item in matching)
    return stale, unexecuted


def filter_notebook_publications_v2(
    publications: tuple[NotebookPublication, ...],
    filters: NotebookSearchFiltersV2,
) -> tuple[NotebookPublication, ...]:
    """Apply exact publication/version filters before any candidate scoring."""

    selected = publications
    for attribute, requested, label in (
        ("template.template_id", filters.template_ids, "template"),
        ("revision.revision_id", filters.revision_ids, "revision"),
        ("execution.execution_id", filters.execution_ids, "execution"),
    ):
        if not requested:
            continue

        def value(
            publication: NotebookPublication,
            path: str = attribute,
        ) -> str:
            cursor: object = publication
            for part in path.split("."):
                cursor = getattr(cursor, part)
            return str(cursor)

        available_all = {value(publication) for publication in publications}
        missing = set(requested) - available_all
        if missing:
            raise NotebookFilterError("notebook_filter_identity_not_found")
        narrowed = tuple(
            publication for publication in selected if value(publication) in set(requested)
        )
        if not narrowed:
            raise NotebookFilterError(f"notebook_{label}_version_mismatch")
        selected = narrowed
    if filters.statuses:
        selected = tuple(
            publication
            for publication in selected
            if str(publication.execution.status) in set(filters.statuses)
        )
    return selected


def _filtered_units(
    publications: tuple[NotebookPublication, ...],
    filters: NotebookSearchFiltersV2,
) -> tuple[tuple[NotebookRetrievalUnit, ...], dict[str, NotebookPublication]]:
    units_by_id: dict[str, NotebookRetrievalUnit] = {}
    publication_by_unit: dict[str, NotebookPublication] = {}
    cell_entities: set[str] | None = None
    if filters.cell_ids:
        cell_entities = set()
        for requested in filters.cell_ids:
            matches = [
                (publication, cell)
                for publication in publications
                for cell in publication.cell_versions
                if requested
                in {
                    cell.cell_version_id,
                    cell.stable_cell_id,
                    cell.native_cell_id,
                }
            ]
            if not matches:
                raise NotebookFilterError("notebook_cell_identity_not_found")
            if len(matches) > 1:
                raise NotebookFilterError("notebook_cell_identity_ambiguous")
            publication, cell = matches[0]
            cell_entities.add(cell.cell_version_id)
            executions = {
                item.cell_execution_id
                for item in publication.cell_executions
                if item.cell_version_id == cell.cell_version_id
            }
            cell_entities.update(executions)
            cell_entities.update(
                item.parameter_id
                for item in publication.parameters
                if item.cell_version_id == cell.cell_version_id
            )
            cell_entities.update(
                item.artifact_id
                for item in publication.artifacts
                if item.cell_execution_id in executions
            )
    parameter_entities: set[str] | None = None
    if filters.parameter_names:
        parameter_entities = {
            item.parameter_id
            for publication in publications
            for item in publication.parameters
            if item.name in set(filters.parameter_names)
        }
    parameter_id_entities: set[str] | None = None
    if filters.parameter_ids:
        available_parameter_ids = {
            item.parameter_id for publication in publications for item in publication.parameters
        }
        if not set(filters.parameter_ids) <= available_parameter_ids:
            raise NotebookFilterError("notebook_parameter_identity_not_found")
        parameter_id_entities = set(filters.parameter_ids)
    output_entities: set[str] | None = None
    if filters.output_types:
        output_entities = {
            item.artifact_id
            for publication in publications
            for item in publication.artifacts
            if str(item.artifact_type) in set(filters.output_types)
        }
    output_id_entities: set[str] | None = None
    if filters.output_ids:
        available_output_ids = {
            item.artifact_id for publication in publications for item in publication.artifacts
        }
        if not set(filters.output_ids) <= available_output_ids:
            raise NotebookFilterError("notebook_output_identity_not_found")
        output_id_entities = set(filters.output_ids)
    eligible_sets = [
        values
        for values in (
            cell_entities,
            parameter_entities,
            parameter_id_entities,
            output_entities,
            output_id_entities,
        )
        if values is not None
    ]
    eligible_entities = set.intersection(*eligible_sets) if eligible_sets else None
    for publication in publications:
        for unit in publication.retrieval_units:
            if eligible_entities is not None and unit.entity_id not in eligible_entities:
                continue
            units_by_id.setdefault(unit.unit_id, unit)
            publication_by_unit.setdefault(unit.unit_id, publication)
    return tuple(units_by_id.values()), publication_by_unit


class NotebookRetrieverV2:
    def __init__(self, store: NotebookV2Store) -> None:
        self.store = store

    def search(
        self,
        *,
        scope: NotebookSearchScope,
        query: str,
        limit: int | None = None,
        filters: NotebookSearchFiltersV2 | None = None,
        task_override: NotebookQueryTask | None = None,
    ) -> NotebookSearchResult:
        resolved_filters = filters or NotebookSearchFiltersV2()
        profile = profile_notebook_query_v2(query, task_override=task_override)
        effective_limit = profile.adaptive_limit if limit is None else limit
        if not 1 <= effective_limit <= 100:
            raise ValueError("Notebook search limit must be between 1 and 100")
        all_publications = self.store.list_publications(
            project_id=scope.project_id,
            allowed_acl_refs=scope.allowed_acl_refs,
            generation_id=scope.generation_id,
        )
        publications = filter_notebook_publications_v2(all_publications, resolved_filters)
        unfiltered_unit_count = sum(
            len(publication.retrieval_units) for publication in all_publications
        )
        filtered_units, publication_by_unit = _filtered_units(publications, resolved_filters)
        units_by_id: dict[str, NotebookRetrievalUnit] = {
            unit.unit_id: unit for unit in filtered_units
        }
        edges = []
        for publication in publications:
            edges.extend(publication.edges)
        current_revisions = _current_revisions(publications)
        stale_by_entity, unexecuted_by_entity = _state_maps(publications)
        query_tokens = set(profile.tokens)
        graph_distance: dict[str, int] = {}
        preliminary: dict[str, tuple[bool, float, bool]] = {}
        task_priority_by_entity: dict[str, float] = {}
        metadata_overlap_by_entity: dict[str, float] = {}
        exact_count = sparse_count = structured_count = 0
        for unit in units_by_id.values():
            content_tokens = set(_tokens(unit.content))
            exact = bool(query_tokens & content_tokens)
            overlap = (
                len(query_tokens & content_tokens) / len(query_tokens) if query_tokens else 0.0
            )
            task_match = unit.unit_type in profile.preferred_unit_types
            task_priority = (
                1.0 - 0.2 * profile.preferred_unit_types.index(unit.unit_type)
                if task_match
                else 0.0
            )
            publication = publication_by_unit[unit.unit_id]
            metadata_tokens = set(
                _tokens(
                    " ".join(
                        (
                            publication.template.name,
                            publication.template.source_key,
                            publication.revision.source_version,
                            publication.execution.execution_key,
                            str(publication.execution.status),
                        )
                    )
                )
            )
            metadata_overlap = (
                len(query_tokens & metadata_tokens) / len(query_tokens) if query_tokens else 0.0
            )
            if exact:
                exact_count += 1
            if overlap:
                sparse_count += 1
            if task_match:
                structured_count += 1
            preliminary[unit.entity_id] = (
                preliminary.get(unit.entity_id, (False, 0.0, False))[0] or exact,
                max(preliminary.get(unit.entity_id, (False, 0.0, False))[1], overlap),
                preliminary.get(unit.entity_id, (False, 0.0, False))[2] or task_match,
            )
            task_priority_by_entity[unit.entity_id] = max(
                task_priority_by_entity.get(unit.entity_id, 0.0),
                task_priority,
            )
            metadata_overlap_by_entity[unit.entity_id] = max(
                metadata_overlap_by_entity.get(unit.entity_id, 0.0),
                metadata_overlap,
            )
        seeds = {
            entity_id
            for entity_id, (exact, overlap, task_match) in preliminary.items()
            if exact or overlap >= 0.5 or (not query_tokens and task_match)
        }
        for edge in edges:
            if edge.source_id in seeds:
                graph_distance[edge.target_id] = min(graph_distance.get(edge.target_id, 99), 1)
            if edge.target_id in seeds:
                graph_distance[edge.source_id] = min(graph_distance.get(edge.source_id, 99), 1)

        candidates: list[NotebookCandidate] = []
        if profile.abstention_reason is not None:
            candidates = []
        else:
            candidates = self._rank_candidates(
                units=tuple(units_by_id.values()),
                profile=profile,
                preliminary=preliminary,
                task_priority_by_entity=task_priority_by_entity,
                metadata_overlap_by_entity=metadata_overlap_by_entity,
                graph_distance=graph_distance,
                current_revisions=current_revisions,
                stale_by_entity=stale_by_entity,
                unexecuted_by_entity=unexecuted_by_entity,
                query_tokens=query_tokens,
            )
        candidates.sort(
            key=lambda item: (
                -item.score,
                item.unit_type,
                item.entity_id,
                item.unit_id,
            )
        )
        selected = tuple(candidates[:effective_limit])
        channel_counts = Counter(
            channel for candidate in candidates for channel in candidate.channels
        )
        traces = tuple(
            NotebookChannelTrace(
                channel=channel,
                status=(
                    "UNAVAILABLE"
                    if channel == NotebookChannel.DENSE
                    else "COMPLETE_WITH_HITS"
                    if channel_counts[channel]
                    else "COMPLETE_NO_MATCH"
                ),
                candidate_count=channel_counts[channel],
            )
            for channel in NotebookChannel
        )
        return NotebookSearchResult(
            candidates=selected,
            trace=NotebookSearchTrace(
                retriever_version=NOTEBOOK_RETRIEVER_VERSION,
                reranker_version=NOTEBOOK_RERANKER_VERSION,
                query_sha256=profile.query_sha256,
                task=profile.task,
                publication_count=len(publications),
                unit_count=len(units_by_id),
                selected_count=len(selected),
                channels=traces,
                selection_trace=NotebookFilterSelectionTraceV2(
                    requested_filters={
                        "template_ids": resolved_filters.template_ids,
                        "revision_ids": resolved_filters.revision_ids,
                        "execution_ids": resolved_filters.execution_ids,
                        "cell_ids": resolved_filters.cell_ids,
                        "statuses": resolved_filters.statuses,
                        "parameter_names": resolved_filters.parameter_names,
                        "parameter_ids": resolved_filters.parameter_ids,
                        "output_ids": resolved_filters.output_ids,
                        "output_types": resolved_filters.output_types,
                    },
                    publication_count_before_filter=len(all_publications),
                    publication_count_after_filter=len(publications),
                    unit_count_before_filter=unfiltered_unit_count,
                    unit_count_after_filter=len(units_by_id),
                    selected_template_ids=tuple(
                        sorted({item.template.template_id for item in publications})
                    ),
                    selected_revision_ids=tuple(
                        sorted({item.revision.revision_id for item in publications})
                    ),
                    selected_execution_ids=tuple(
                        sorted({item.execution.execution_id for item in publications})
                    ),
                ),
                abstention_reason=profile.abstention_reason,
            ),
        )

    def _rank_candidates(
        self,
        *,
        units: tuple[NotebookRetrievalUnit, ...],
        profile: NotebookQueryProfile,
        preliminary: dict[str, tuple[bool, float, bool]],
        task_priority_by_entity: dict[str, float],
        metadata_overlap_by_entity: dict[str, float],
        graph_distance: dict[str, int],
        current_revisions: set[str],
        stale_by_entity: dict[str, bool],
        unexecuted_by_entity: dict[str, bool],
        query_tokens: set[str],
    ) -> list[NotebookCandidate]:
        candidates: list[NotebookCandidate] = []
        for unit in units:
            exact, overlap, task_match = preliminary.get(unit.entity_id, (False, 0.0, False))
            distance = graph_distance.get(unit.entity_id)
            if not (exact or overlap or distance is not None or (not query_tokens and task_match)):
                continue
            stale = stale_by_entity.get(unit.entity_id, False)
            unexecuted = unexecuted_by_entity.get(unit.entity_id, False)
            features = NotebookRerankFeatures(
                exact_match=exact,
                token_overlap=overlap,
                task_match=task_match,
                task_priority=task_priority_by_entity.get(unit.entity_id, 0.0),
                metadata_overlap=metadata_overlap_by_entity.get(unit.entity_id, 0.0),
                graph_distance=distance,
                current_revision=unit.revision_id in current_revisions,
                stale=stale,
                unexecuted=unexecuted,
                markdown_only=unit.profile == "markdown-v2",
                metric_unconfirmed="metric_unconfirmed" in unit.content,
                task=profile.task,
            )
            reranked = rerank_notebook_candidate_v2(features)
            if reranked.score <= 0:
                continue
            channels = tuple(
                channel
                for channel, active in (
                    (NotebookChannel.EXACT, exact),
                    (NotebookChannel.STRUCTURED, task_match),
                    (NotebookChannel.SPARSE, overlap > 0),
                    (NotebookChannel.GRAPH, distance is not None),
                )
                if active
            )
            candidates.append(
                NotebookCandidate(
                    unit_id=unit.unit_id,
                    entity_id=unit.entity_id,
                    unit_type=unit.unit_type,
                    profile=unit.profile,
                    revision_id=unit.revision_id,
                    execution_id=unit.execution_id,
                    locator=unit.locator,
                    content=unit.content,
                    content_sha256=unit.content_sha256,
                    score=reranked.score,
                    channels=channels,
                    reasons=reranked.reasons,
                    stale=stale,
                    unexecuted=unexecuted,
                )
            )
        return candidates


__all__ = [
    "NOTEBOOK_RETRIEVER_VERSION",
    "NotebookCandidate",
    "NotebookChannel",
    "NotebookChannelTrace",
    "NotebookFilterError",
    "NotebookFilterSelectionTraceV2",
    "NotebookQueryProfile",
    "NotebookQueryTask",
    "NotebookRetrieverV2",
    "NotebookSearchFiltersV2",
    "NotebookSearchResult",
    "NotebookSearchScope",
    "NotebookSearchTrace",
    "filter_notebook_publications_v2",
    "profile_notebook_query_v2",
]
