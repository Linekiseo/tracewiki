"""M5 typed cross-source evidence graph and bounded beam traversal."""

from __future__ import annotations

from collections import defaultdict, deque
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .multisource_foundation_v2 import (
    MultiSourceCandidateV2,
    MultiSourcePlanV2,
    canonical_sha256_v2,
)

EVIDENCE_GRAPH_REGISTRY_VERSION = "multisource-edge-registry-v2"
EVIDENCE_GRAPH_TRAVERSAL_VERSION = "typed-evidence-graph-beam-v2"


class EdgeReviewStatusV2(StrEnum):
    REVIEWED = "reviewed"
    UNREVIEWED = "unreviewed"
    REJECTED = "rejected"


class EdgeFactStatusV2(StrEnum):
    VERIFIED = "verified"
    CANDIDATE = "candidate"
    CONTRADICTED = "contradicted"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class EvidenceEdgeTypeV2(_Frozen):
    predicate: str
    source_types: tuple[str, ...]
    target_types: tuple[str, ...]
    owner_domain: str
    inverse_predicate: str | None
    allowed_derivations: tuple[str, ...]
    requires_review: bool
    traversal_intents: tuple[str, ...]
    default_cost: float = Field(gt=0)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EvidenceEdgeTypeV2:
        for values in (
            self.source_types,
            self.target_types,
            self.allowed_derivations,
            self.traversal_intents,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("edge type lists must be sorted and unique")
        if any(item.endswith(".fact") for item in (*self.source_types, *self.target_types)):
            raise ValueError("edge endpoints must use typed entities, not fact envelopes")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("edge type digest mismatch")
        return self


class EvidenceEdgeRegistryV2(_Frozen):
    edge_types: tuple[EvidenceEdgeTypeV2, ...]
    registry_version: str = EVIDENCE_GRAPH_REGISTRY_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EvidenceEdgeRegistryV2:
        predicates = tuple(item.predicate for item in self.edge_types)
        if predicates != tuple(sorted(set(predicates))):
            raise ValueError("edge registry predicates must be sorted and unique")
        by_predicate = {item.predicate: item for item in self.edge_types}
        for item in self.edge_types:
            inverse = by_predicate.get(item.inverse_predicate or "")
            if inverse is not None and (
                inverse.inverse_predicate != item.predicate
                or inverse.source_types != item.target_types
                or inverse.target_types != item.source_types
                or inverse.allowed_derivations != item.allowed_derivations
                or inverse.requires_review != item.requires_review
                or inverse.traversal_intents != item.traversal_intents
            ):
                raise ValueError("registered inverse edge semantics do not match")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("edge registry digest mismatch")
        return self


class EvidenceEdgeV2(_Frozen):
    edge_id: str
    predicate: str
    source_candidate_id: str
    target_candidate_id: str
    source_fact_type: str
    source_entity_type: str
    target_fact_type: str
    target_entity_type: str
    derivation: str
    review_status: EdgeReviewStatusV2
    fact_status: EdgeFactStatusV2
    confidence: float = Field(ge=0, le=1)
    acl_ref: str
    source_version: str
    target_version: str
    evidence_locator: str
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EvidenceEdgeV2:
        payload = self.model_dump(
            mode="json",
            exclude={"edge_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if self.content_sha256 != digest or self.edge_id != "evidenceedge-" + digest.removeprefix(
            "sha256:"
        ):
            raise ValueError("edge identity mismatch")
        return self


class EvidencePathV2(_Frozen):
    path_id: str
    seed_candidate_id: str
    candidate_ids: tuple[str, ...] = Field(min_length=1)
    edge_ids: tuple[str, ...]
    predicates: tuple[str, ...]
    typed_relations: tuple[str, ...]
    matched_roles: tuple[str, ...]
    score: float
    counter_evidence: bool
    verified: bool
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EvidencePathV2:
        if (
            len(self.edge_ids) != len(self.candidate_ids) - 1
            or len(self.predicates) != len(self.edge_ids)
            or len(self.typed_relations) != len(self.edge_ids)
        ):
            raise ValueError("path nodes and edges do not align")
        payload = self.model_dump(
            mode="json",
            exclude={"path_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if self.content_sha256 != digest or self.path_id != "evidencepath-" + digest.removeprefix(
            "sha256:"
        ):
            raise ValueError("path identity mismatch")
        return self


class EvidenceTraversalTraceV2(_Frozen):
    plan_sha256: str
    registry_sha256: str
    seed_ids: tuple[str, ...]
    max_hops: int
    beam_width: int
    edge_budget: int
    expanded_nodes: int
    traversed_edges: int
    pruned_acl: int
    pruned_version: int
    pruned_type: int
    pruned_intent: int
    pruned_confidence: int
    pruned_review: int
    pruned_cycle: int
    pruned_budget: int
    accepted_paths: tuple[EvidencePathV2, ...]
    missing_roles: tuple[str, ...]
    corrective_rounds: tuple[tuple[int, tuple[str, ...]], ...]
    traversal_version: str = EVIDENCE_GRAPH_TRAVERSAL_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EvidenceTraversalTraceV2:
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("traversal trace digest mismatch")
        return self


def build_default_edge_registry_v2() -> EvidenceEdgeRegistryV2:
    specs = (
        (
            "DERIVED_FROM",
            (
                "codex.change",
                "codex.decision",
                "document.Claim",
                "document.claim",
                "document.table_cell_fact",
                "notebook.output",
                "workspace.intelligence_run",
            ),
            (
                "code.CodeSymbol",
                "code.Commit",
                "code.DiffHunk",
                "document.claim",
                "experiment.ExperimentRunV2",
                "experiment.ExperimentMetricObservationV2",
                "experiment.ExperimentRunSnapshotV2",
                "experiment.MetricObservationV2",
            ),
            "workspace",
            "DERIVES",
            ("deterministic", "human_confirmed"),
            False,
            ("historical_change", "rationale", "global_synthesis"),
        ),
        (
            "GENERATED",
            (
                "codex.change",
                "codex.outcome",
                "notebook.NotebookCellExecution",
                "notebook.NotebookOutput",
                "notebook.cell_execution",
                "notebook.output",
            ),
            (
                "document.claim",
                "document.Claim",
                "document.DocumentTable",
                "document.DocumentTableCell",
                "document.table",
                "document.table_cell_fact",
                "experiment.ExperimentArtifactVersionV2",
                "experiment.ExperimentMetricObservationV2",
            ),
            "notebook",
            "GENERATED_BY",
            ("deterministic",),
            False,
            ("experiment_validation", "reproduction"),
        ),
        (
            "INVALIDATES",
            (
                "code.DiffHunk",
                "code.TestResult",
                "document.version_diff",
                "experiment.ExperimentMetricObservationV2",
                "experiment.MetricObservationV2",
            ),
            (
                "document.claim",
                "document.Claim",
                "workspace.evidence_requirement",
                "workspace.outcome",
                "workspace.work_item",
            ),
            "document",
            "INVALIDATED_BY",
            ("human_confirmed", "deterministic_after_review"),
            True,
            ("claim_verification", "staleness_check"),
        ),
        (
            "PRODUCED",
            (
                "experiment.ExperimentRunSnapshotV2",
                "experiment.ExperimentRunV2",
                "experiment.run.surface",
                "notebook.NotebookCellExecution",
                "notebook.cell_execution",
            ),
            (
                "document.table",
                "document.table_cell_fact",
                "document.DocumentTable",
                "document.DocumentTableCell",
                "experiment.ExperimentMetricObservationV2",
                "experiment.MetricObservationV2",
                "experiment.artifact.surface",
            ),
            "experiment",
            "PRODUCED_BY",
            ("deterministic",),
            False,
            ("experiment_validation", "reproduction"),
        ),
        (
            "SUPPORTED_BY",
            ("document.Claim", "document.claim", "workspace.evidence_requirement"),
            (
                "code.CodeSymbol",
                "code.TestResult",
                "experiment.ExperimentRun",
                "experiment.ExperimentRunV2",
                "experiment.ExperimentMetricObservationV2",
                "experiment.ExperimentRunSnapshotV2",
                "experiment.MetricObservationV2",
                "experiment.run.surface",
                "notebook.NotebookCellExecution",
                "notebook.NotebookOutput",
                "notebook.cell_execution",
                "notebook.output",
            ),
            "document",
            "SUPPORTS",
            ("human_confirmed", "deterministic_after_review"),
            True,
            ("claim_verification", "staleness_check"),
        ),
        (
            "SUPPORTS",
            (
                "code.CodeSymbol",
                "code.TestResult",
                "experiment.ExperimentRun",
                "experiment.ExperimentRunV2",
                "experiment.ExperimentMetricObservationV2",
                "experiment.ExperimentRunSnapshotV2",
                "experiment.MetricObservationV2",
                "experiment.run.surface",
                "notebook.NotebookCellExecution",
                "notebook.NotebookOutput",
                "notebook.cell_execution",
                "notebook.output",
            ),
            ("document.Claim", "document.claim", "workspace.evidence_requirement"),
            "document",
            "SUPPORTED_BY",
            ("deterministic_after_review", "human_confirmed"),
            True,
            ("claim_verification", "staleness_check"),
        ),
        (
            "VALIDATED_BY",
            (
                "code.CodeSymbol",
                "code.DiffHunk",
                "codex.change",
                "workspace.ResearchWorkItem",
                "workspace.work_item",
            ),
            (
                "code.TestResult",
                "experiment.ExperimentRunV2",
                "experiment.ExperimentMetricObservationV2",
                "experiment.ExperimentRunSnapshotV2",
                "experiment.MetricObservationV2",
                "notebook.NotebookCellExecution",
                "notebook.NotebookError",
                "notebook.NotebookOutput",
                "notebook.cell_execution",
                "notebook.error",
                "notebook.output",
            ),
            "codex",
            "VALIDATES",
            ("deterministic", "human_confirmed"),
            False,
            ("current_implementation", "historical_change", "claim_verification"),
        ),
    )
    edge_types: list[EvidenceEdgeTypeV2] = []
    for (
        predicate,
        source_types,
        target_types,
        owner,
        inverse,
        derivations,
        requires_review,
        intents,
    ) in sorted(specs):
        payload = {
            "predicate": predicate,
            "source_types": tuple(sorted(source_types)),
            "target_types": tuple(sorted(target_types)),
            "owner_domain": owner,
            "inverse_predicate": inverse,
            "allowed_derivations": tuple(sorted(derivations)),
            "requires_review": requires_review,
            "traversal_intents": tuple(sorted(intents)),
            "default_cost": 1.0,
        }
        edge_types.append(
            EvidenceEdgeTypeV2(
                **payload,
                content_sha256=canonical_sha256_v2(payload),
            )
        )
    payload = {
        "edge_types": [item.model_dump(mode="json") for item in edge_types],
        "registry_version": EVIDENCE_GRAPH_REGISTRY_VERSION,
    }
    return EvidenceEdgeRegistryV2(
        edge_types=tuple(edge_types),
        content_sha256=canonical_sha256_v2(payload),
    )


def build_evidence_edge_v2(
    *,
    predicate: str,
    source: MultiSourceCandidateV2,
    target: MultiSourceCandidateV2,
    derivation: str,
    review_status: EdgeReviewStatusV2,
    fact_status: EdgeFactStatusV2,
    confidence: float,
    evidence_locator: str,
    registry: EvidenceEdgeRegistryV2 | None = None,
) -> EvidenceEdgeV2:
    active_registry = registry or build_default_edge_registry_v2()
    definition = next(
        (item for item in active_registry.edge_types if item.predicate == predicate),
        None,
    )
    if definition is None:
        raise ValueError("edge predicate is not registered")
    if (
        f"{source.retrieval_domain}.{source.entity_type}" not in definition.source_types
        or f"{target.retrieval_domain}.{target.entity_type}" not in definition.target_types
    ):
        raise ValueError("edge endpoints do not match registered typed relation")
    if derivation not in definition.allowed_derivations:
        raise ValueError("edge derivation is not registered")
    if (
        definition.requires_review
        and fact_status is EdgeFactStatusV2.VERIFIED
        and review_status is not EdgeReviewStatusV2.REVIEWED
    ):
        raise ValueError("verified relation requires reviewed authority")
    if (
        review_status is EdgeReviewStatusV2.REJECTED
        and fact_status is not EdgeFactStatusV2.CONTRADICTED
    ):
        raise ValueError("rejected relation must be contradicted")
    if source.acl_ref != target.acl_ref:
        raise ValueError("edge cannot cross ACL")
    payload = {
        "predicate": predicate,
        "source_candidate_id": source.candidate_id,
        "target_candidate_id": target.candidate_id,
        "source_fact_type": source.fact_type,
        "source_entity_type": source.entity_type,
        "target_fact_type": target.fact_type,
        "target_entity_type": target.entity_type,
        "derivation": derivation,
        "review_status": review_status,
        "fact_status": fact_status,
        "confidence": confidence,
        "acl_ref": source.acl_ref,
        "source_version": source.stable_version,
        "target_version": target.stable_version,
        "evidence_locator": evidence_locator,
    }
    normalized = EvidenceEdgeV2.model_construct(
        edge_id="pending", content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"edge_id", "content_sha256"})
    digest = canonical_sha256_v2(normalized)
    return EvidenceEdgeV2(
        edge_id="evidenceedge-" + digest.removeprefix("sha256:"),
        content_sha256=digest,
        **payload,
    )


def traverse_evidence_graph_v2(
    plan: MultiSourcePlanV2,
    candidates: tuple[MultiSourceCandidateV2, ...],
    edges: tuple[EvidenceEdgeV2, ...],
    registry: EvidenceEdgeRegistryV2,
    *,
    seed_ids: tuple[str, ...],
    max_hops: int = 3,
    beam_width: int = 8,
    edge_budget: int = 100,
    minimum_confidence: float = 0.5,
) -> EvidenceTraversalTraceV2:
    if max_hops < 1 or max_hops > 4 or beam_width < 1 or beam_width > 50:
        raise ValueError("traversal bounds are invalid")
    if edge_budget < 1 or edge_budget > 1_000:
        raise ValueError("edge budget is invalid")
    by_candidate = {item.candidate_id: item for item in candidates}
    if any(identity not in by_candidate for identity in seed_ids):
        raise ValueError("unknown traversal seed")
    edge_types = {item.predicate: item for item in registry.edge_types}
    edge_by_id = {item.edge_id: item for item in edges}
    outgoing: dict[str, list[EvidenceEdgeV2]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.source_candidate_id].append(edge)
    for values in outgoing.values():
        values.sort(key=lambda item: (-item.confidence, item.edge_id))
    counters = defaultdict(int)
    traversed = 0
    accepted: dict[str, EvidencePathV2] = {}
    queue: deque[tuple[tuple[str, ...], tuple[str, ...], float]] = deque(
        ((identity,), (), by_candidate[identity].calibrated_relevance) for identity in seed_ids
    )
    required = set(plan.required_roles)
    while queue:
        nodes, path_edges, score = queue.popleft()
        counters["expanded"] += 1
        if len(path_edges) >= max_hops:
            continue
        expansions: list[tuple[float, EvidenceEdgeV2, MultiSourceCandidateV2]] = []
        for edge in outgoing.get(nodes[-1], ()):
            if traversed >= edge_budget:
                counters["budget"] += 1
                break
            traversed += 1
            definition = edge_types.get(edge.predicate)
            target = by_candidate.get(edge.target_candidate_id)
            source = by_candidate[nodes[-1]]
            if definition is None or target is None:
                counters["type"] += 1
                continue
            if plan.intent not in definition.traversal_intents:
                counters["intent"] += 1
                continue
            if (
                f"{source.retrieval_domain}.{source.entity_type}" not in definition.source_types
                or f"{target.retrieval_domain}.{target.entity_type}" not in definition.target_types
                or edge.source_fact_type != source.fact_type
                or edge.source_entity_type != source.entity_type
                or edge.target_fact_type != target.fact_type
                or edge.target_entity_type != target.entity_type
            ):
                counters["type"] += 1
                continue
            if edge.derivation not in definition.allowed_derivations:
                counters["type"] += 1
                continue
            if edge.acl_ref not in plan.scope.acl_refs or target.acl_ref not in plan.scope.acl_refs:
                counters["acl"] += 1
                continue
            if target.version_alignment == "mismatch" or (
                edge.source_version != source.stable_version
                or edge.target_version != target.stable_version
            ):
                counters["version"] += 1
                continue
            if edge.confidence < minimum_confidence:
                counters["confidence"] += 1
                continue
            if (
                definition.requires_review and edge.review_status is not EdgeReviewStatusV2.REVIEWED
            ) or edge.review_status is EdgeReviewStatusV2.REJECTED:
                counters["review"] += 1
                continue
            if target.candidate_id in nodes:
                counters["cycle"] += 1
                continue
            new_roles = set(target.matched_roles) & required
            current_roles = {
                role for node in nodes for role in by_candidate[node].matched_roles
            } & required
            role_gain = len(new_roles - current_roles) / max(1, len(required))
            confidence_product = edge.confidence
            hop_penalty = 0.08 * (len(path_edges) + 1)
            independence = (
                0.7
                if target.root_provenance in {by_candidate[node].root_provenance for node in nodes}
                else 1.0
            )
            review_factor = 1.0 if edge.review_status is EdgeReviewStatusV2.REVIEWED else 0.8
            path_score = (
                score
                * confidence_product
                * (1.0 if target.version_alignment in {"exact", "historical_target"} else 0.85)
                * (1 + role_gain)
                * independence
                * review_factor
                - hop_penalty
            )
            expansions.append((path_score, edge, target))
        expansions.sort(key=lambda item: (-item[0], item[1].edge_id))
        for path_score, edge, target in expansions[:beam_width]:
            new_nodes = (*nodes, target.candidate_id)
            new_edges = (*path_edges, edge.edge_id)
            path_edge_values = tuple(edge_by_id[identity] for identity in new_edges)
            roles = tuple(
                role
                for role in plan.required_roles
                if any(role in by_candidate[node].matched_roles for node in new_nodes)
            )
            counter = any(by_candidate[node].counter_evidence for node in new_nodes) or (
                edge.fact_status is EdgeFactStatusV2.CONTRADICTED
            )
            verified = all(
                candidate.fact_status in {"verified", "observed", "accepted"}
                for candidate in (by_candidate[node] for node in new_nodes)
            ) and all(
                item.fact_status is EdgeFactStatusV2.VERIFIED
                and (
                    item.review_status is EdgeReviewStatusV2.REVIEWED
                    or not edge_types[item.predicate].requires_review
                )
                for item in path_edge_values
            )
            predicates = tuple(item.predicate for item in path_edge_values)
            typed_relations = tuple(
                f"{item.source_fact_type}[{item.source_entity_type}]"
                f"-{item.predicate}->"
                f"{item.target_fact_type}[{item.target_entity_type}]"
                for item in path_edge_values
            )
            payload = {
                "seed_candidate_id": new_nodes[0],
                "candidate_ids": new_nodes,
                "edge_ids": new_edges,
                "predicates": predicates,
                "typed_relations": typed_relations,
                "matched_roles": roles,
                "score": path_score,
                "counter_evidence": counter,
                "verified": verified,
            }
            normalized = EvidencePathV2.model_construct(
                path_id="pending",
                content_sha256="pending",
                **payload,
            ).model_dump(mode="json", exclude={"path_id", "content_sha256"})
            digest = canonical_sha256_v2(normalized)
            path = EvidencePathV2(
                path_id="evidencepath-" + digest.removeprefix("sha256:"),
                content_sha256=digest,
                **payload,
            )
            if roles or counter:
                accepted[path.path_id] = path
            queue.append((new_nodes, new_edges, path_score))
    covered = {role for path in accepted.values() for role in path.matched_roles} | {
        role for seed in seed_ids for role in by_candidate[seed].matched_roles
    }
    missing = tuple(role for role in plan.required_roles if role not in covered)
    corrective = tuple(
        (round_number, missing) for round_number in range(1, min(2, int(bool(missing)) * 2) + 1)
    )
    accepted_paths = tuple(
        sorted(accepted.values(), key=lambda item: (-item.score, item.path_id))[:50]
    )
    payload = {
        "plan_sha256": plan.content_sha256,
        "registry_sha256": registry.content_sha256,
        "seed_ids": seed_ids,
        "max_hops": max_hops,
        "beam_width": beam_width,
        "edge_budget": edge_budget,
        "expanded_nodes": counters["expanded"],
        "traversed_edges": traversed,
        "pruned_acl": counters["acl"],
        "pruned_version": counters["version"],
        "pruned_type": counters["type"],
        "pruned_intent": counters["intent"],
        "pruned_confidence": counters["confidence"],
        "pruned_review": counters["review"],
        "pruned_cycle": counters["cycle"],
        "pruned_budget": counters["budget"],
        "accepted_paths": accepted_paths,
        "missing_roles": missing,
        "corrective_rounds": corrective,
        "traversal_version": EVIDENCE_GRAPH_TRAVERSAL_VERSION,
    }
    normalized = EvidenceTraversalTraceV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return EvidenceTraversalTraceV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


__all__ = [
    "EVIDENCE_GRAPH_REGISTRY_VERSION",
    "EVIDENCE_GRAPH_TRAVERSAL_VERSION",
    "EdgeFactStatusV2",
    "EdgeReviewStatusV2",
    "EvidenceEdgeRegistryV2",
    "EvidenceEdgeTypeV2",
    "EvidenceEdgeV2",
    "EvidencePathV2",
    "EvidenceTraversalTraceV2",
    "build_default_edge_registry_v2",
    "build_evidence_edge_v2",
    "traverse_evidence_graph_v2",
]
