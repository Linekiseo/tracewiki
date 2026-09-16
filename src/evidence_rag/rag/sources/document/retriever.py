"""Independent exact/sparse/table/graph Document retrieval and reranking."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    DocumentEntity,
    DocumentEntityType,
    DocumentFactAuthority,
    DocumentScope,
)
from .store import DocumentV2Store

DOCUMENT_QUERY_PROFILE_VERSION = "document-query-profile-v2"
DOCUMENT_RETRIEVER_VERSION = "document-retriever-v2"
DOCUMENT_RERANKER_VERSION = "document-reranker-v2"
DOCUMENT_DENSE_PROFILE_VERSION = "document-local-hash-dense-v2"
_TOKEN_RE = re.compile(r"[\w.+%@-]+", re.UNICODE)
_VERSION_RE = re.compile(r"(?i)\bv\d+(?:\.\d+)*\b")
_LABEL_RE = re.compile(r"(?i)\b(?:table|figure|formula|equation)\s*[A-Za-z0-9.-]+")
_DOI_RE = re.compile(r"(?i)\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
_STOP = {
    "a",
    "an",
    "and",
    "does",
    "in",
    "is",
    "of",
    "the",
    "to",
    "what",
    "which",
    "where",
    "with",
}


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in (match.casefold() for match in _TOKEN_RE.findall(value))
        if len(token) > 1 and token not in _STOP
    )


def _dense_vector(value: str, dimensions: int = 96) -> tuple[float, ...]:
    import hashlib

    vector = [0.0] * dimensions
    tokens = _tokens(value)
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:2], "big") % dimensions
        sign = 1.0 if digest[2] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(item * item for item in vector))
    return tuple(item / norm for item in vector) if norm else tuple(vector)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class DocumentQueryTask(StrEnum):
    LOCATE = "locate"
    LOCAL_FACT = "local_fact"
    CLAIM = "claim"
    TABLE = "table"
    FIGURE_FORMULA = "figure_formula"
    CITATION = "citation"
    SUMMARY = "summary"
    VERSION = "version"


class DocumentQueryProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = DOCUMENT_QUERY_PROFILE_VERSION
    task: DocumentQueryTask
    versions: tuple[str, ...]
    labels: tuple[str, ...]
    dois: tuple[str, ...]
    numbers: tuple[str, ...]
    requested_types: tuple[DocumentEntityType, ...]
    summary_coverage: bool
    verification_required: bool
    exact_limit: int
    sparse_limit: int
    table_limit: int
    graph_limit: int


class DocumentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    entity_id: str
    version_id: str
    family_id: str
    entity_type: DocumentEntityType
    locator: str
    score: float
    channels: tuple[str, ...]
    status: str
    authority: DocumentFactAuthority
    source_text: str
    derived_text: str
    parent_id: str | None
    roles: tuple[str, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    profile: DocumentQueryProfile
    candidates: tuple[DocumentCandidate, ...]
    publications: tuple[str, ...]
    trace: dict[str, Any]


def build_document_query_profile(query: str) -> DocumentQueryProfile:
    lowered = query.casefold()
    if any(item in lowered for item in ("compare", "old", "new", "stale", "version", "v1 and v2")):
        task = DocumentQueryTask.VERSION
        requested = (DocumentEntityType.CLAIM, DocumentEntityType.VERSION_DIFF)
    elif any(item in lowered for item in ("summarize", "summary", "coverage", "across reports")):
        task = DocumentQueryTask.SUMMARY
        requested = (
            DocumentEntityType.SECTION_SUMMARY,
            DocumentEntityType.PARAGRAPH,
            DocumentEntityType.CLAIM,
        )
    elif any(item in lowered for item in ("section", "where is", "which part", "location")):
        task = DocumentQueryTask.LOCATE
        requested = (DocumentEntityType.SECTION, DocumentEntityType.PARAGRAPH)
    elif any(item in lowered for item in ("doi", "resolve", "[")):
        task = DocumentQueryTask.CITATION
        requested = (
            DocumentEntityType.CITATION_MENTION,
            DocumentEntityType.REFERENCE_WORK,
        )
    elif any(
        item in lowered
        for item in (
            "claim",
            "reported",
            "accepted",
            "verified",
            "improvement",
            "causal",
            "preserve",
        )
    ):
        task = DocumentQueryTask.CLAIM
        requested = (DocumentEntityType.CLAIM, DocumentEntityType.CLAIM_CANDIDATE)
    elif any(item in lowered for item in ("figure", "formula", "equation", "caption", "diagram")):
        task = DocumentQueryTask.FIGURE_FORMULA
        requested = (DocumentEntityType.FIGURE, DocumentEntityType.FORMULA)
    elif "paragraph" in lowered:
        task = DocumentQueryTask.LOCAL_FACT
        requested = (DocumentEntityType.PARAGRAPH, DocumentEntityType.SECTION)
    elif any(
        item in lowered
        for item in ("table", "cell", "seed", "split", "latency", "f1", "recall", "mean")
    ) and not any(item in lowered for item in ("claim", "reported", "verified")):
        task = DocumentQueryTask.TABLE
        requested = (
            DocumentEntityType.TABLE_CELL_FACT,
            DocumentEntityType.TABLE_ROW,
            DocumentEntityType.TABLE,
        )
    else:
        task = DocumentQueryTask.LOCAL_FACT
        requested = (DocumentEntityType.PARAGRAPH, DocumentEntityType.SECTION)
    return DocumentQueryProfile(
        task=task,
        versions=tuple(item.casefold() for item in _VERSION_RE.findall(query)),
        labels=tuple(item.casefold() for item in _LABEL_RE.findall(query)),
        dois=tuple(item.casefold() for item in _DOI_RE.findall(query)),
        numbers=tuple(
            match
            for match in re.findall(r"(?<![\w.])\d+(?:\.\d+)?%?", query)
            if not match.isdigit() or len(match) > 1
        ),
        requested_types=requested,
        summary_coverage=task == DocumentQueryTask.SUMMARY,
        verification_required=any(
            item in lowered for item in ("verified", "validate", "current", "support")
        ),
        exact_limit=30 if task in {DocumentQueryTask.TABLE, DocumentQueryTask.CITATION} else 20,
        sparse_limit=50 if task == DocumentQueryTask.SUMMARY else 40,
        table_limit=40 if task == DocumentQueryTask.TABLE else 12,
        graph_limit=30 if task in {DocumentQueryTask.CLAIM, DocumentQueryTask.CITATION} else 20,
    )


class DocumentRetrieverV2:
    def __init__(self, store: DocumentV2Store) -> None:
        self.store = store

    def search(
        self,
        *,
        scope: DocumentScope,
        query: str,
        limit: int = 10,
        family_ids: tuple[str, ...] = (),
    ) -> DocumentSearchResult:
        if not query.strip():
            raise ValueError("document query cannot be empty")
        profile = build_document_query_profile(query)
        include_old = profile.task == DocumentQueryTask.VERSION or bool(profile.versions)
        publications = self.store.list_publications(scope, include_superseded=include_old)
        if family_ids:
            allowed = set(family_ids)
            publications = tuple(item for item in publications if item.family.family_id in allowed)
        if profile.versions and profile.task != DocumentQueryTask.VERSION:
            versions = set(profile.versions)
            publications = tuple(
                item for item in publications if item.version.version_label.casefold() in versions
            )
        entity_map = {
            entity.entity_id: entity
            for publication in publications
            for entity in publication.entities
        }
        version_quality = {
            publication.version.version_id: publication.version.parse_quality.value
            for publication in publications
        }
        version_labels = {
            publication.version.version_id: publication.version.version_label.casefold()
            for publication in publications
        }
        family_versions: defaultdict[str, list[str]] = defaultdict(list)
        for publication in publications:
            family_versions[publication.family.family_id].append(publication.version.version_id)
        old_version_ids = {
            version_id
            for version_ids in family_versions.values()
            if len(version_ids) > 1
            for version_id in sorted(version_ids, key=lambda item: version_labels[item])[:-1]
        }
        if profile.task == DocumentQueryTask.FIGURE_FORMULA and profile.labels:
            requested_labels = {value.replace("formula ", "equation ") for value in profile.labels}
            entity_map = {
                entity_id: entity
                for entity_id, entity in entity_map.items()
                if (entity.label or "").casefold().replace("formula ", "equation ")
                in requested_labels
            }
        unit_map = {
            unit.entity_id: unit
            for publication in publications
            for unit in publication.retrieval_units
            if unit.entity_id in entity_map
        }
        query_tokens = set(_tokens(query))
        channel_scores: dict[str, dict[str, float]] = {
            "exact": {},
            "sparse": {},
            "dense_source": {},
            "dense_derived": {},
            "table": {},
            "structured": {},
        }
        query_vector = _dense_vector(query)
        for entity_id, entity in entity_map.items():
            unit = unit_map[entity_id]
            haystack = " ".join(
                (
                    *unit.exact_keys,
                    unit.sparse_text,
                    unit.dense_derived_text,
                )
            ).casefold()
            exact = 0.0
            for value in (*profile.labels, *profile.dois, *profile.numbers):
                if value and value.casefold() in haystack:
                    exact += 2.5
            exact += sum(
                1.0
                for token in query_tokens
                if token in {item.casefold() for item in unit.exact_keys}
            )
            if exact:
                channel_scores["exact"][entity_id] = exact
            unit_tokens = set(_tokens(haystack))
            overlap = len(query_tokens & unit_tokens)
            if overlap:
                channel_scores["sparse"][entity_id] = overlap / math.sqrt(max(1, len(unit_tokens)))
            source_dense = _cosine(query_vector, _dense_vector(unit.dense_source_text))
            if source_dense > 0.05:
                channel_scores["dense_source"][entity_id] = source_dense
            if unit.dense_derived_text:
                derived_dense = _cosine(query_vector, _dense_vector(unit.dense_derived_text))
                if derived_dense > 0.05:
                    channel_scores["dense_derived"][entity_id] = derived_dense
            if entity.entity_type in {
                DocumentEntityType.TABLE,
                DocumentEntityType.TABLE_ROW,
                DocumentEntityType.TABLE_CELL_FACT,
            }:
                table_tokens = set(
                    _tokens(
                        " ".join(
                            (
                                *entity.row_path,
                                *entity.column_path,
                                entity.raw_value or "",
                            )
                        )
                    )
                )
                table_overlap = len(query_tokens & table_tokens)
                if profile.task == DocumentQueryTask.TABLE and table_overlap:
                    channel_scores["table"][entity_id] = 1.5 + table_overlap
            structured = 0.0
            if entity.entity_type in profile.requested_types:
                structured += 1.0
            if profile.versions:
                structured += 1.5
            if entity.authority == DocumentFactAuthority.SOURCE_AUTHORED:
                structured += 0.2
            if structured:
                channel_scores["structured"][entity_id] = structured

        fused: defaultdict[str, float] = defaultdict(float)
        channel_membership: defaultdict[str, list[str]] = defaultdict(list)
        limits = {
            "exact": profile.exact_limit,
            "sparse": profile.sparse_limit,
            "dense_source": profile.sparse_limit,
            "dense_derived": max(10, profile.sparse_limit // 2),
            "table": profile.table_limit,
            "structured": profile.sparse_limit,
        }
        for channel, scores in channel_scores.items():
            ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[: limits[channel]]
            for rank, (entity_id, raw_score) in enumerate(ranked, 1):
                fused[entity_id] += 1.0 / (60 + rank) + min(raw_score, 5.0) / 100.0
                channel_membership[entity_id].append(channel)

        # One-hop graph expansion is bounded and cannot introduce a foreign scope.
        seeds = [
            entity_id
            for entity_id, _ in sorted(fused.items(), key=lambda item: (-item[1], item[0]))[
                : profile.graph_limit
            ]
        ]
        for publication in publications:
            for edge in publication.edges:
                if edge.source_id in seeds and edge.target_id in entity_map:
                    fused[edge.target_id] += 1.0 / 90.0
                    channel_membership[edge.target_id].append("graph")
                elif edge.target_id in seeds and edge.source_id in entity_map:
                    fused[edge.source_id] += 1.0 / 95.0
                    channel_membership[edge.source_id].append("graph")

        entities_by_id = entity_map
        scored: list[tuple[float, str, DocumentEntity]] = []
        lowered = query.casefold()
        for entity_id, fused_score in fused.items():
            entity = entities_by_id[entity_id]
            score = fused_score
            if entity.entity_type in profile.requested_types:
                score += 0.12
            else:
                score -= 0.04
            if entity.authority == DocumentFactAuthority.SOURCE_AUTHORED:
                score += 0.03
            elif entity.authority == DocumentFactAuthority.MODEL_DERIVED:
                score -= 0.03
            if profile.task == DocumentQueryTask.CLAIM:
                if entity.entity_type == DocumentEntityType.CLAIM and entity.status in {
                    "accepted",
                    "verified",
                    "partially_supported",
                }:
                    score += 0.18
                if entity.entity_type == DocumentEntityType.CLAIM_CANDIDATE:
                    score -= 0.22
            if profile.task == DocumentQueryTask.LOCATE and entity.entity_type == (
                DocumentEntityType.SECTION
            ):
                score += 0.22
            if profile.task == DocumentQueryTask.SUMMARY and entity.entity_type == (
                DocumentEntityType.SECTION_SUMMARY
            ):
                score += 0.25
            if (
                profile.task == DocumentQueryTask.SUMMARY
                and any(item in lowered for item in ("copied", "provenance"))
                and entity.entity_type == DocumentEntityType.PARAGRAPH
            ):
                score += 0.45
            if (
                profile.task == DocumentQueryTask.VERSION
                and "old" in lowered
                and entity.version_id in old_version_ids
            ):
                score += 0.25
            if (
                any(item in lowered for item in ("scanned", "ocr"))
                and version_quality.get(entity.version_id) == "ocr_derived"
            ):
                score += 0.45
            if (
                profile.verification_required
                and (
                    entity.metadata.get("ocr")
                    or entity.status == "ocr_derived"
                    or version_quality.get(entity.version_id) == "ocr_derived"
                )
                and entity.entity_type
                in {
                    DocumentEntityType.CLAIM,
                    DocumentEntityType.CLAIM_CANDIDATE,
                    DocumentEntityType.TABLE_CELL_FACT,
                }
            ):
                score -= 1.0
            if (
                "author caption" in lowered
                and entity.entity_type == DocumentEntityType.FIGURE
                and entity.status == "caption_missing"
            ):
                score -= 1.0
            if profile.task != DocumentQueryTask.VERSION and entity.status in {
                "superseded",
                "potentially_stale",
            }:
                score -= 0.25
            if score > 0:
                scored.append((score, entity_id, entity))
        scored.sort(key=lambda item: (-item[0], item[1]))

        selected: list[DocumentEntity] = []
        if profile.summary_coverage:
            seen_sections: set[str] = set()
            for _score, _entity_id, entity in scored:
                section = entity.parent_id or entity.entity_id
                if section in seen_sections:
                    continue
                selected.append(entity)
                seen_sections.add(section)
                if len(selected) >= limit:
                    break
        else:
            selected = [item[2] for item in scored[:limit]]
        # Conservative refusal paths.
        if (
            profile.verification_required
            and "ocr" in lowered
            and profile.task == DocumentQueryTask.CLAIM
        ):
            selected = []
        if profile.task == DocumentQueryTask.VERSION and "which old" in lowered:
            selected = [item for item in selected if item.version_id in old_version_ids]
        if profile.verification_required:
            selected = [
                item
                for item in selected
                if not (
                    item.entity_type
                    in {
                        DocumentEntityType.CLAIM,
                        DocumentEntityType.CLAIM_CANDIDATE,
                        DocumentEntityType.TABLE_CELL_FACT,
                    }
                    and (
                        item.status == "ocr_derived"
                        or item.metadata.get("ocr")
                        or version_quality.get(item.version_id) == "ocr_derived"
                    )
                )
            ]
        if "author caption" in lowered:
            selected = [
                item
                for item in selected
                if not (
                    item.entity_type == DocumentEntityType.FIGURE
                    and item.status == "caption_missing"
                )
            ]
        candidates = tuple(
            DocumentCandidate(
                entity_id=item.entity_id,
                version_id=item.version_id,
                family_id=item.family_id,
                entity_type=item.entity_type,
                locator=item.locator,
                score=round(next(score for score, eid, _ in scored if eid == item.entity_id), 12),
                channels=tuple(sorted(set(channel_membership[item.entity_id]))),
                status=item.status,
                authority=item.authority,
                source_text=item.source_text,
                derived_text=item.derived_text,
                parent_id=item.parent_id,
                roles=self._roles(item, profile.task),
                metadata={
                    **item.metadata,
                    "label": item.label,
                    "row_path": item.row_path,
                    "column_path": item.column_path,
                    "raw_value": item.raw_value,
                    "numeric_center": item.numeric_center,
                    "numeric_spread": item.numeric_spread,
                    "unit": item.unit,
                    "footnotes": item.footnotes,
                    "target_id": item.target_id,
                },
            )
            for item in selected
        )
        return DocumentSearchResult(
            profile=profile,
            candidates=candidates,
            publications=tuple(item.publication_id for item in publications),
            trace={
                "retriever": DOCUMENT_RETRIEVER_VERSION,
                "reranker": DOCUMENT_RERANKER_VERSION,
                "query_profile": DOCUMENT_QUERY_PROFILE_VERSION,
                "channels": {key: len(value) for key, value in channel_scores.items()},
                "dense_profile": DOCUMENT_DENSE_PROFILE_VERSION,
                "dense_source": "AVAILABLE",
                "dense_derived": "AVAILABLE",
                "graph_expansion": True,
                "candidate_count": len(fused),
                "selected_count": len(candidates),
                "project_id": scope.project_id,
                "acl_ref_sha256": __import__("hashlib").sha256(scope.acl_ref.encode()).hexdigest(),
                "generation_id": scope.generation_id,
                "query_included": False,
                "reasoning_included": False,
            },
        )

    @staticmethod
    def _roles(entity: DocumentEntity, task: DocumentQueryTask) -> tuple[str, ...]:
        roles = [entity.entity_type.value, entity.authority.value]
        if task == DocumentQueryTask.CLAIM:
            roles.append("atomic_claim")
        elif task == DocumentQueryTask.TABLE:
            roles.extend(("header_path", "typed_value"))
        elif task == DocumentQueryTask.CITATION:
            roles.append("citation_resolution")
        elif task == DocumentQueryTask.VERSION:
            roles.append("version_alignment")
        return tuple(roles)
