"""Experiment E4 semantic surfaces, deterministic reranking, and context."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .analysis_v2 import (
    ExperimentAggregationResultV2,
    ExperimentComparabilityResultV2,
    ExperimentComparisonV2,
    ExperimentReproductionResultV2,
)
from .contracts_v2 import ExperimentRunSnapshotV2, canonical_sha256_v2
from .query_v2 import ExperimentQueryTaskV2, ExperimentQueryV2

EXPERIMENT_SURFACE_BUILDER_VERSION = "experiment-surface-builder-v2"
EXPERIMENT_SEMANTIC_PROFILE_VERSION = "experiment-local-semantic-profile-v2"
EXPERIMENT_RERANKER_VERSION = "experiment-structured-tail-reranker-v2"
EXPERIMENT_CONTEXT_BUILDER_VERSION = "experiment-context-builder-v2"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_@.+-]+|[\u3400-\u9fff]+")
_PRIVATE_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S{8,})|"
    r"(?:^|[\s\"'(])/(?:Users|home|private|tmp|var|etc|root|Volumes)(?:/|\b)|"
    r"(?i:(?:^|[\s\"'(])[A-Z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+))"
)


class ExperimentSurfaceTypeV2(StrEnum):
    EXPERIMENT = "experiment.surface"
    RUN = "run.surface"
    METRIC_DEFINITION = "metric.definition"
    CONFIG = "config.surface"
    DATASET = "dataset.surface"
    ARTIFACT = "artifact.surface"
    COMPARISON = "comparison.surface"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ExperimentSurfaceUnitV2(_Frozen):
    unit_id: str
    unit_type: ExperimentSurfaceTypeV2
    project_id: str
    source_id: str
    generation_id: str
    acl_ref: str
    run_snapshot_id: str
    source_identity: str
    title: str
    text: str
    aliases: tuple[str, ...]
    negative_features: tuple[str, ...]
    raw_locator: str
    content_sha256: str
    builder_version: str = EXPERIMENT_SURFACE_BUILDER_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentSurfaceUnitV2:
        if _PRIVATE_RE.search(self.title) or _PRIVATE_RE.search(self.text):
            raise ValueError("surface contains private material")
        if self.aliases != tuple(sorted(set(self.aliases))):
            raise ValueError("surface aliases must be sorted and unique")
        payload = self.model_dump(
            mode="json",
            exclude={"unit_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if (
            self.content_sha256 != digest
            or self.unit_id != "experimentsurface-" + digest.removeprefix("sha256:")
        ):
            raise ValueError("surface identity mismatch")
        return self


class ExperimentSemanticCandidateV2(_Frozen):
    unit_id: str
    run_snapshot_id: str
    unit_type: ExperimentSurfaceTypeV2
    pinned_structured: bool
    sparse_score: float = Field(ge=0, le=1)
    alias_score: float = Field(ge=0, le=1)
    dense_score: float | None = Field(default=None, ge=0, le=1)
    rerank_score: float
    rank: int = Field(ge=1)
    channel: Literal["exact", "structured", "alias", "sparse", "dense"]
    negative_features: tuple[str, ...]
    source_locator: str


class ExperimentSemanticResultV2(_Frozen):
    query_sha256: str
    candidates: tuple[ExperimentSemanticCandidateV2, ...]
    pinned_count: int
    semantic_tail_count: int
    embedding_profile: str | None
    fallback_reason: str | None
    profile_version: str = EXPERIMENT_SEMANTIC_PROFILE_VERSION
    reranker_version: str = EXPERIMENT_RERANKER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentSemanticResultV2:
        if self.pinned_count != sum(item.pinned_structured for item in self.candidates):
            raise ValueError("pinned count is inconsistent")
        if self.semantic_tail_count != len(self.candidates) - self.pinned_count:
            raise ValueError("semantic tail count is inconsistent")
        if tuple(item.rank for item in self.candidates) != tuple(
            range(1, len(self.candidates) + 1)
        ):
            raise ValueError("candidate ranks are not contiguous")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("semantic result digest mismatch")
        return self


class ExperimentContextBlockV2(_Frozen):
    block_id: str
    role: Literal[
        "identity",
        "conditions",
        "observations",
        "computed",
        "comparability",
        "reproduction",
        "missing",
    ]
    run_snapshot_id: str
    body: str
    citations: tuple[str, ...]
    token_estimate: int = Field(ge=1)
    truncated: bool
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentContextBlockV2:
        if not self.citations:
            raise ValueError("context block requires citations")
        if _PRIVATE_RE.search(self.body):
            raise ValueError("context block contains private material")
        payload = self.model_dump(
            mode="json",
            exclude={"block_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if (
            self.content_sha256 != digest
            or self.block_id != "experimentcontextblock-" + digest.removeprefix("sha256:")
        ):
            raise ValueError("context block identity mismatch")
        return self


class ExperimentContextV2(_Frozen):
    task: ExperimentQueryTaskV2
    project_id: str
    source_id: str
    generation_id: str
    acl_ref: str
    blocks: tuple[ExperimentContextBlockV2, ...]
    included_block_ids: tuple[str, ...]
    dropped_block_ids: tuple[str, ...]
    citation_count: int
    role_coverage: tuple[str, ...]
    budget_tokens: int
    used_tokens: int
    warnings: tuple[str, ...]
    builder_version: str = EXPERIMENT_CONTEXT_BUILDER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentContextV2:
        if self.included_block_ids != tuple(item.block_id for item in self.blocks):
            raise ValueError("included context IDs do not match blocks")
        if self.used_tokens != sum(item.token_estimate for item in self.blocks):
            raise ValueError("context token accounting is inconsistent")
        if self.used_tokens > self.budget_tokens:
            raise ValueError("context exceeds budget")
        if self.citation_count != sum(len(item.citations) for item in self.blocks):
            raise ValueError("context citation count is inconsistent")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("context digest mismatch")
        return self


def _safe_surface_text(value: object, *, limit: int = 1_000) -> str:
    text = " ".join(str(value).split())
    if not text or len(text) > limit or _PRIVATE_RE.search(text):
        raise ValueError("surface text is unsafe")
    return text


def _unit(
    snapshot: ExperimentRunSnapshotV2,
    unit_type: ExperimentSurfaceTypeV2,
    source_identity: str,
    title: str,
    text: str,
    *,
    aliases: tuple[str, ...] = (),
    negative_features: tuple[str, ...] = (),
) -> ExperimentSurfaceUnitV2:
    payload = {
        "unit_type": unit_type,
        "project_id": snapshot.project_id,
        "source_id": snapshot.source_id,
        "generation_id": snapshot.generation_id,
        "acl_ref": snapshot.acl_ref,
        "run_snapshot_id": snapshot.run_snapshot_id,
        "source_identity": source_identity,
        "title": _safe_surface_text(title, limit=300),
        "text": _safe_surface_text(text),
        "aliases": tuple(sorted(set(aliases))),
        "negative_features": tuple(sorted(set(negative_features))),
        "raw_locator": f"experiment-v2://snapshot/{snapshot.run_snapshot_id}",
        "builder_version": EXPERIMENT_SURFACE_BUILDER_VERSION,
    }
    digest = canonical_sha256_v2(
        {
            key: value.value if isinstance(value, StrEnum) else value
            for key, value in payload.items()
        }
    )
    return ExperimentSurfaceUnitV2(
        unit_id="experimentsurface-" + digest.removeprefix("sha256:"),
        content_sha256=digest,
        **payload,
    )


def build_experiment_surfaces_v2(
    snapshot: ExperimentRunSnapshotV2,
    *,
    experiment_title: str,
    objective: str | None = None,
    hypothesis: str | None = None,
    run_name: str | None = None,
    tags: tuple[str, ...] = (),
) -> tuple[ExperimentSurfaceUnitV2, ...]:
    """Build search surfaces without deriving any numeric truth from text."""

    units: list[ExperimentSurfaceUnitV2] = []
    experiment_text = " ".join(
        part
        for part in (
            experiment_title,
            objective or "",
            hypothesis or "",
            " ".join(sorted(set(tags))),
        )
        if part
    )
    units.append(
        _unit(
            snapshot,
            ExperimentSurfaceTypeV2.EXPERIMENT,
            snapshot.experiment_id,
            experiment_title,
            experiment_text,
            aliases=tags,
        )
    )
    negative = []
    if snapshot.status != "completed":
        negative.append("status_not_completed")
    if snapshot.dataset.completeness != "complete":
        negative.append("dataset_incomplete")
    units.append(
        _unit(
            snapshot,
            ExperimentSurfaceTypeV2.RUN,
            snapshot.run_id,
            run_name or snapshot.run_id,
            " ".join(
                part
                for part in (
                    run_name or snapshot.run_id,
                    f"status {snapshot.status}",
                    f"dataset {snapshot.dataset.dataset_id or 'missing'}",
                    f"version {snapshot.dataset.version or 'missing'}",
                )
                if part
            ),
            negative_features=tuple(negative),
        )
    )
    for definition in snapshot.definitions:
        units.append(
            _unit(
                snapshot,
                ExperimentSurfaceTypeV2.METRIC_DEFINITION,
                definition.definition_id,
                definition.canonical_name,
                " ".join(
                    (
                        definition.canonical_name,
                        definition.description,
                        f"unit {definition.unit or 'unknown'}",
                        f"direction {definition.direction.value}",
                    )
                ),
                aliases=definition.aliases,
                negative_features=("direction_unknown",)
                if definition.direction.value == "unknown"
                else (),
            )
        )
    config_keys = tuple(key for key, _ in snapshot.config.values)
    if config_keys:
        units.append(
            _unit(
                snapshot,
                ExperimentSurfaceTypeV2.CONFIG,
                snapshot.config.config_snapshot_id,
                "configuration",
                "configuration keys " + " ".join(config_keys),
            )
        )
    units.append(
        _unit(
            snapshot,
            ExperimentSurfaceTypeV2.DATASET,
            snapshot.dataset.dataset_version_id,
            snapshot.dataset.dataset_id or "dataset missing",
            " ".join(
                (
                    f"dataset {snapshot.dataset.dataset_id or 'missing'}",
                    f"version {snapshot.dataset.version or 'missing'}",
                    f"split {snapshot.dataset.split or 'unspecified'}",
                )
            ),
            negative_features=("dataset_incomplete",)
            if snapshot.dataset.completeness != "complete"
            else (),
        )
    )
    for artifact in snapshot.artifacts:
        units.append(
            _unit(
                snapshot,
                ExperimentSurfaceTypeV2.ARTIFACT,
                artifact.artifact_version_id,
                artifact.name,
                f"{artifact.kind} artifact {artifact.name} "
                f"state {artifact.verification_state.value}",
                negative_features=("artifact_unverified",)
                if artifact.verification_state.value != "present_verified"
                else (),
            )
        )
    return tuple(sorted(units, key=lambda item: item.unit_id))


def surface_records_v2(
    units: tuple[ExperimentSurfaceUnitV2, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "unit_id": item.unit_id,
            "run_snapshot_id": item.run_snapshot_id,
            "unit_type": item.unit_type.value,
            "content_sha256": item.content_sha256,
            "payload": item.model_dump(mode="json"),
        }
        for item in units
    )


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text)}


def retrieve_experiment_surfaces_v2(
    query: ExperimentQueryV2,
    snapshots: tuple[ExperimentRunSnapshotV2, ...],
    units: tuple[ExperimentSurfaceUnitV2, ...],
    *,
    semantic_query: str,
    structured_snapshot_ids: tuple[str, ...] = (),
    limit: int | None = None,
    dense_profile: str | None = None,
) -> ExperimentSemanticResultV2:
    """Pin exact/structured candidates and rerank only the semantic tail."""

    if any(
        item.project_id != query.project_id
        or item.source_id != query.source_id
        or item.acl_ref != query.acl_ref
        for item in snapshots
    ):
        raise ValueError("retrieval snapshots cross query scope")
    by_snapshot = {item.run_snapshot_id: item for item in snapshots}
    if any(
        item.run_snapshot_id not in by_snapshot
        or item.project_id != query.project_id
        or item.source_id != query.source_id
        or item.acl_ref != query.acl_ref
        or item.generation_id != by_snapshot[item.run_snapshot_id].generation_id
        for item in units
    ):
        raise ValueError("surface unit authority mismatch")
    structured = set(structured_snapshot_ids)
    if not structured.issubset(by_snapshot):
        raise ValueError("structured candidate is not in governed snapshots")
    requested = _tokens(semantic_query)
    scored: list[tuple[bool, float, str, ExperimentSurfaceUnitV2, float, float]] = []
    for unit in units:
        surface_tokens = _tokens(f"{unit.title} {unit.text}")
        alias_tokens = _tokens(" ".join(unit.aliases))
        sparse = len(requested & surface_tokens) / max(1, len(requested | surface_tokens))
        alias = len(requested & alias_tokens) / max(1, len(requested)) if alias_tokens else 0
        pinned = unit.run_snapshot_id in structured
        snapshot = by_snapshot[unit.run_snapshot_id]
        penalty = 0.12 * len(unit.negative_features)
        task_bonus = (
            0.2
            if (
                query.task is ExperimentQueryTaskV2.REPRODUCE
                and unit.unit_type
                in {
                    ExperimentSurfaceTypeV2.CONFIG,
                    ExperimentSurfaceTypeV2.DATASET,
                    ExperimentSurfaceTypeV2.ARTIFACT,
                }
            )
            or (
                query.task
                in {
                    ExperimentQueryTaskV2.BEST,
                    ExperimentQueryTaskV2.COMPARE,
                    ExperimentQueryTaskV2.TREND,
                }
                and unit.unit_type is ExperimentSurfaceTypeV2.METRIC_DEFINITION
            )
            else 0
        )
        completeness = (
            int(snapshot.dataset.completeness == "complete")
            + int(bool(snapshot.environment.values))
            + int(bool(snapshot.config.values))
        ) / 3
        score = 2.0 * int(pinned) + max(sparse, alias) + task_bonus + 0.1 * completeness - penalty
        scored.append((pinned, score, unit.unit_id, unit, sparse, alias))
    scored.sort(key=lambda row: (not row[0], -row[1], row[2]))
    bounded = scored[: min(limit or query.limit, 200)]
    candidates = tuple(
        ExperimentSemanticCandidateV2(
            unit_id=row[3].unit_id,
            run_snapshot_id=row[3].run_snapshot_id,
            unit_type=row[3].unit_type,
            pinned_structured=row[0],
            sparse_score=row[4],
            alias_score=row[5],
            dense_score=None,
            rerank_score=row[1],
            rank=index,
            channel=row[3].unit_type
            and (  # retain inferred Literal
                "exact"
                if row[0]
                and (
                    row[3].source_identity in query.run_ids
                    or row[3].source_identity in query.experiment_ids
                )
                else "structured"
                if row[0]
                else "alias"
                if row[5] >= row[4] and row[5] > 0
                else "sparse"
            ),
            negative_features=row[3].negative_features,
            source_locator=row[3].raw_locator,
        )
        for index, row in enumerate(bounded, start=1)
    )
    payload = {
        "query_sha256": canonical_sha256_v2(query.model_dump(mode="json")),
        "candidates": candidates,
        "pinned_count": sum(item.pinned_structured for item in candidates),
        "semantic_tail_count": sum(not item.pinned_structured for item in candidates),
        "embedding_profile": dense_profile,
        "fallback_reason": "dense_provider_unavailable" if dense_profile else None,
        "profile_version": EXPERIMENT_SEMANTIC_PROFILE_VERSION,
        "reranker_version": EXPERIMENT_RERANKER_VERSION,
    }
    json_payload = {
        **payload,
        "candidates": [item.model_dump(mode="json") for item in candidates],
    }
    return ExperimentSemanticResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(json_payload),
    )


def _context_block(
    role: str,
    snapshot: ExperimentRunSnapshotV2,
    body: str,
    citations: tuple[str, ...],
    *,
    max_chars: int,
) -> ExperimentContextBlockV2:
    normalized = " ".join(body.split())
    truncated = len(normalized) > max_chars
    rendered = normalized[:max_chars].rstrip() if truncated else normalized
    token_estimate = max(1, (len(rendered) + 3) // 4)
    payload = {
        "role": role,
        "run_snapshot_id": snapshot.run_snapshot_id,
        "body": rendered,
        "citations": citations,
        "token_estimate": token_estimate,
        "truncated": truncated,
    }
    digest = canonical_sha256_v2(payload)
    return ExperimentContextBlockV2(
        block_id="experimentcontextblock-" + digest.removeprefix("sha256:"),
        content_sha256=digest,
        **payload,  # type: ignore[arg-type]
    )


def build_experiment_context_v2(
    query: ExperimentQueryV2,
    snapshots: tuple[ExperimentRunSnapshotV2, ...],
    *,
    comparisons: tuple[ExperimentComparisonV2, ...] = (),
    aggregations: tuple[ExperimentAggregationResultV2, ...] = (),
    comparability: tuple[ExperimentComparabilityResultV2, ...] = (),
    reproduction: tuple[ExperimentReproductionResultV2, ...] = (),
    budget_tokens: int = 1_500,
) -> ExperimentContextV2:
    """Pack raw identities and precomputed facts; it performs no hidden math."""

    if budget_tokens < 64 or budget_tokens > 20_000:
        raise ValueError("context budget must be within 64..20000 tokens")
    if not snapshots:
        raise ValueError("context requires at least one governed snapshot")
    if any(
        item.project_id != query.project_id
        or item.source_id != query.source_id
        or item.acl_ref != query.acl_ref
        for item in snapshots
    ):
        raise ValueError("context snapshots cross query scope")
    by_id = {item.run_snapshot_id: item for item in snapshots}
    proposals: list[ExperimentContextBlockV2] = []
    max_chars = max(160, budget_tokens * 2)
    for snapshot in sorted(snapshots, key=lambda item: (item.observed_at, item.run_id)):
        snapshot_locator = f"experiment-v2://snapshot/{snapshot.run_snapshot_id}"
        proposals.append(
            _context_block(
                "identity",
                snapshot,
                f"run {snapshot.run_id}; experiment {snapshot.experiment_id}; "
                f"status {snapshot.status}; generation {snapshot.generation_id}",
                (snapshot_locator,),
                max_chars=max_chars,
            )
        )
        proposals.append(
            _context_block(
                "conditions",
                snapshot,
                f"dataset {snapshot.dataset.dataset_id or 'missing'} "
                f"version {snapshot.dataset.version or 'missing'}; "
                f"commit {'present' if snapshot.commit_sha else 'missing'}; "
                f"config {snapshot.config.config_snapshot_id}; "
                f"environment {snapshot.environment.environment_snapshot_id}",
                (
                    snapshot_locator + "/dataset",
                    snapshot_locator + "/config",
                    snapshot_locator + "/environment",
                ),
                max_chars=max_chars,
            )
        )
        valid = [item for item in snapshot.observations if item.valid]
        if valid:
            body = "; ".join(
                f"{item.raw_name}={item.raw_value} "
                f"{item.raw_unit or item.canonical_unit or 'unitless'} "
                f"split={item.split or 'unspecified'} step={item.step}"
                for item in valid
            )
            proposals.append(
                _context_block(
                    "observations",
                    snapshot,
                    body,
                    tuple(item.source_locator for item in valid),
                    max_chars=max_chars,
                )
            )
    for item in comparisons:
        snapshot = by_id.get(item.comparability.candidate_snapshot_id)
        if snapshot is None:
            raise ValueError("comparison is not bound to context snapshot")
        proposals.append(
            _context_block(
                "computed",
                snapshot,
                f"comparison {item.status}; baseline={item.baseline_value}; "
                f"candidate={item.candidate_value}; delta={item.delta}; "
                f"relative_delta={item.relative_delta}; improvement={item.improvement}; "
                f"reason={item.reason or 'none'}",
                (f"experiment-v2://comparison/{item.content_sha256.removeprefix('sha256:')}",),
                max_chars=max_chars,
            )
        )
    for item in aggregations:
        snapshot = next(
            (candidate for candidate in snapshots if candidate.run_id in item.included_run_ids),
            snapshots[0],
        )
        proposals.append(
            _context_block(
                "computed",
                snapshot,
                f"aggregation {item.function}; status={item.status}; n={item.n}; "
                f"value={item.value}; mean={item.mean}; median={item.median}; "
                f"std={item.std}; reason={item.reason or 'none'}",
                tuple(
                    f"experiment-v2://observation/{identity}" for identity in item.observation_ids
                )
                or (f"experiment-v2://group/{item.run_group_id}",),
                max_chars=max_chars,
            )
        )
    for item in comparability:
        snapshot = by_id.get(item.candidate_snapshot_id)
        if snapshot is None:
            raise ValueError("comparability result is not bound to context snapshot")
        proposals.append(
            _context_block(
                "comparability",
                snapshot,
                f"decision={item.decision.value}; differences="
                + ",".join(difference.field for difference in item.differences)
                + "; missing="
                + ",".join(item.missing_fields),
                tuple(difference.candidate_locator for difference in item.differences)
                or (f"experiment-v2://snapshot/{snapshot.run_snapshot_id}",),
                max_chars=max_chars,
            )
        )
    for item in reproduction:
        snapshot = by_id.get(item.run_snapshot_id)
        if snapshot is None:
            raise ValueError("reproduction result is not bound to context snapshot")
        missing = tuple(role.role for role in item.roles if role.state.value != "present_verified")
        proposals.append(
            _context_block(
                "reproduction" if not missing else "missing",
                snapshot,
                f"reproduction={item.status}; missing={','.join(missing) or 'none'}",
                tuple(evidence for role in item.roles for evidence in role.evidence_ids)
                or (f"experiment-v2://snapshot/{snapshot.run_snapshot_id}",),
                max_chars=max_chars,
            )
        )
    included: list[ExperimentContextBlockV2] = []
    dropped: list[str] = []
    used = 0
    role_priority = {
        "identity": 0,
        "comparability": 1,
        "computed": 2,
        "observations": 3,
        "conditions": 4,
        "reproduction": 5,
        "missing": 5,
    }
    for block in sorted(
        proposals,
        key=lambda item: (
            role_priority[item.role],
            by_id[item.run_snapshot_id].observed_at,
            item.block_id,
        ),
    ):
        if used + block.token_estimate > budget_tokens:
            dropped.append(block.block_id)
            continue
        included.append(block)
        used += block.token_estimate
    warnings: list[str] = []
    if dropped:
        warnings.append("budget_truncated")
    if not any(item.role == "observations" for item in included):
        warnings.append("observations_unavailable")
    payload = {
        "task": query.task,
        "project_id": query.project_id,
        "source_id": query.source_id,
        "generation_id": snapshots[0].generation_id,
        "acl_ref": query.acl_ref,
        "blocks": tuple(included),
        "included_block_ids": tuple(item.block_id for item in included),
        "dropped_block_ids": tuple(dropped),
        "citation_count": sum(len(item.citations) for item in included),
        "role_coverage": tuple(sorted({item.role for item in included})),
        "budget_tokens": budget_tokens,
        "used_tokens": used,
        "warnings": tuple(warnings),
        "builder_version": EXPERIMENT_CONTEXT_BUILDER_VERSION,
    }
    json_payload = {
        **payload,
        "task": query.task.value,
        "blocks": [item.model_dump(mode="json") for item in included],
    }
    return ExperimentContextV2(
        **payload,
        content_sha256=canonical_sha256_v2(json_payload),
    )


__all__ = [
    "EXPERIMENT_CONTEXT_BUILDER_VERSION",
    "EXPERIMENT_RERANKER_VERSION",
    "EXPERIMENT_SEMANTIC_PROFILE_VERSION",
    "EXPERIMENT_SURFACE_BUILDER_VERSION",
    "ExperimentContextBlockV2",
    "ExperimentContextV2",
    "ExperimentSemanticCandidateV2",
    "ExperimentSemanticResultV2",
    "ExperimentSurfaceTypeV2",
    "ExperimentSurfaceUnitV2",
    "build_experiment_context_v2",
    "build_experiment_surfaces_v2",
    "retrieve_experiment_surfaces_v2",
    "surface_records_v2",
]
