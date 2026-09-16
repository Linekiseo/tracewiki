"""Released 40-case Workspace Golden and reviewed-row evaluator."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    WorkspaceAuthority,
    WorkspaceEntity,
    WorkspaceEntityType,
    WorkspacePublication,
    canonical_sha256,
)
from .fixture_v1 import (
    WORKSPACE_FIXTURE_OBSERVED_AT,
    WORKSPACE_GOLDEN_DATASET_ID,
    WORKSPACE_GOLDEN_DATASET_VERSION,
    WorkspaceFixtureBundle,
    build_workspace_fixture_v1,
)

WORKSPACE_GOLDEN_CASE_COUNT = 40
WORKSPACE_GOLDEN_PACKAGE_SHA256 = (
    "sha256:f1849d314a8f1b37d5eb7808e18cd4348f33782b47bb4719a76da318d90be4b5"
)
WORKSPACE_GOLDEN_AUTHORITY_SHA256 = (
    "sha256:63a29f4a357d4ca9870e8f807c9f26e3f5a8c164c17d293bf2b2ae2ae4a4f2f2"
)


class WorkspaceGoldenError(ValueError):
    """Raised when Workspace Golden authority or reviewed rows are invalid."""


class WorkspaceGoldenSlice(StrEnum):
    EXACT = "exact_ambiguity"
    CURRENT = "current_state"
    WORK = "work_acceptance"
    BLOCKER = "blocker_dependency_overdue"
    COVERAGE = "evidence_coverage"
    INTELLIGENCE = "next_action_intelligence"
    DECISION = "decision_rationale"
    TEMPORAL = "as_of_audit_compare"


EXPECTED_WORKSPACE_SLICE_COUNTS = {
    WorkspaceGoldenSlice.EXACT: 5,
    WorkspaceGoldenSlice.CURRENT: 6,
    WorkspaceGoldenSlice.WORK: 6,
    WorkspaceGoldenSlice.BLOCKER: 6,
    WorkspaceGoldenSlice.COVERAGE: 6,
    WorkspaceGoldenSlice.INTELLIGENCE: 5,
    WorkspaceGoldenSlice.DECISION: 3,
    WorkspaceGoldenSlice.TEMPORAL: 3,
}


class WorkspaceMetric(StrEnum):
    SCOPE_RESOLUTION_ACCURACY = "scope_resolution_accuracy"
    EXACT_ENTITY_ACCURACY = "exact_entity_accuracy"
    CURRENT_STATE_ACCURACY = "current_state_accuracy"
    TEMPORAL_ACCURACY = "temporal_accuracy"
    WORK_ITEM_RECALL_AT_5 = "work_item_recall_at_5"
    GRAPH_PATH_RECALL = "graph_path_recall"
    DUPLICATE_DISAMBIGUATION_ACCURACY = "duplicate_disambiguation_accuracy"
    STATUS_ACCURACY = "status_accuracy"
    OVERDUE_ACCURACY = "overdue_accuracy"
    BLOCKER_PRECISION = "blocker_precision"
    DEPENDENCY_PATH_ACCURACY = "dependency_path_accuracy"
    ACCEPTANCE_GATE_ACCURACY = "acceptance_gate_accuracy"
    EVIDENCE_COVERAGE_PRECISION = "evidence_coverage_precision"
    MISSING_ROLE_ACCURACY = "missing_role_accuracy"
    NEXT_ACTION_RULE_ACCURACY = "next_action_rule_accuracy"
    AUTHORITY_CLASSIFICATION_ACCURACY = "authority_classification_accuracy"
    HARD_NEGATIVE_AVOIDANCE = "hard_negative_avoidance"
    MRR = "mrr"
    NDCG_AT_10 = "ndcg_at_10"
    UNSAFE_MUTATION_RATE = "unsafe_mutation_rate"
    ACL_LEAKAGE_RATE = "acl_leakage_rate"


class WorkspaceMetricStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class WorkspaceReviewAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class _FrozenEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class WorkspaceGoldenCase(_FrozenEvaluation):
    case_id: str
    slice: WorkspaceGoldenSlice
    query: str
    expected_entity_ids: tuple[str, ...]
    expected_entity_types: tuple[WorkspaceEntityType, ...]
    expected_authorities: tuple[WorkspaceAuthority, ...]
    hard_negative_entity_ids: tuple[str, ...] = ()
    eligible_metrics: tuple[WorkspaceMetric, ...]
    required_edge_types: tuple[str, ...] = ()
    include_history: bool = False
    as_of: str | None = None
    expected_empty: bool = False

    @model_validator(mode="after")
    def _truth(self) -> WorkspaceGoldenCase:
        if len(set(self.expected_entity_ids)) != len(self.expected_entity_ids):
            raise ValueError("Workspace positive membership must be unique")
        if len(set(self.hard_negative_entity_ids)) != len(self.hard_negative_entity_ids):
            raise ValueError("Workspace hard-negative membership must be unique")
        if set(self.expected_entity_ids) & set(self.hard_negative_entity_ids):
            raise ValueError("Workspace positives and hard negatives overlap")
        if self.expected_empty != (not self.expected_entity_ids):
            raise ValueError("Workspace expected_empty does not match positives")
        if len(set(self.eligible_metrics)) != len(self.eligible_metrics):
            raise ValueError("Workspace eligible metric membership must be unique")
        return self


class WorkspaceGoldenDataset(_FrozenEvaluation):
    dataset_id: str
    dataset_version: str
    package_sha256: str
    authority_sha256: str
    fixture_recipe_sha256: str
    publication: WorkspacePublication
    cases: tuple[WorkspaceGoldenCase, ...]

    def package_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"package_sha256", "authority_sha256"},
        )

    def entity_map(self) -> dict[str, WorkspaceEntity]:
        return {item.entity_id: item for item in self.publication.entities}

    @model_validator(mode="after")
    def _authority(self) -> WorkspaceGoldenDataset:
        if (
            self.dataset_id != WORKSPACE_GOLDEN_DATASET_ID
            or self.dataset_version != WORKSPACE_GOLDEN_DATASET_VERSION
        ):
            raise ValueError("unexpected Workspace Golden identity")
        expected_ids = tuple(f"workspace-v1-{index:03d}" for index in range(1, 41))
        if len(self.cases) != 40 or tuple(item.case_id for item in self.cases) != expected_ids:
            raise ValueError("Workspace Golden must contain canonical ordered 40 cases")
        if Counter(item.slice for item in self.cases) != Counter(EXPECTED_WORKSPACE_SLICE_COUNTS):
            raise ValueError("Workspace Golden slice counts are not canonical")
        entities = self.entity_map()
        for case in self.cases:
            expected = [entities.get(item) for item in case.expected_entity_ids]
            hard = [entities.get(item) for item in case.hard_negative_entity_ids]
            if any(item is None for item in (*expected, *hard)):
                raise ValueError("Workspace case references unknown entity authority")
            if case.expected_entity_ids:
                if set(case.expected_entity_types) != {
                    item.entity_type for item in expected if item is not None
                }:
                    raise ValueError("Workspace expected type truth mismatch")
                if set(case.expected_authorities) != {
                    item.authority for item in expected if item is not None
                }:
                    raise ValueError("Workspace authority truth mismatch")
        expected_package = canonical_sha256(self.package_payload())
        if self.package_sha256 != expected_package:
            raise ValueError("Workspace Golden package digest mismatch")
        expected_authority = canonical_sha256(
            {
                "dataset_id": self.dataset_id,
                "dataset_version": self.dataset_version,
                "package_sha256": self.package_sha256,
                "case_membership": expected_ids,
                "slice_counts": {
                    key.value: value for key, value in EXPECTED_WORKSPACE_SLICE_COUNTS.items()
                },
                "publication_sha256": self.publication.publication_sha256,
                "entity_ids": sorted(entities),
            }
        )
        if self.authority_sha256 != expected_authority:
            raise ValueError("Workspace Golden authority digest mismatch")
        if WORKSPACE_GOLDEN_PACKAGE_SHA256 and (
            self.package_sha256 != WORKSPACE_GOLDEN_PACKAGE_SHA256
        ):
            raise ValueError("Workspace Golden differs from released package")
        if WORKSPACE_GOLDEN_AUTHORITY_SHA256 and (
            self.authority_sha256 != WORKSPACE_GOLDEN_AUTHORITY_SHA256
        ):
            raise ValueError("Workspace Golden differs from released authority")
        return self


class WorkspaceReviewedCandidate(_FrozenEvaluation):
    entity_id: str
    locator: str


class WorkspaceReviewedRow(_FrozenEvaluation):
    case_id: str
    availability: WorkspaceReviewAvailability
    candidates: tuple[WorkspaceReviewedCandidate, ...] = Field(default=(), max_length=10)
    diagnostic: str | None = None

    @model_validator(mode="after")
    def _review(self) -> WorkspaceReviewedRow:
        if self.availability != WorkspaceReviewAvailability.AVAILABLE and self.candidates:
            raise ValueError("unavailable Workspace row cannot have candidates")
        if len({item.entity_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("Workspace reviewed candidates must be unique")
        return self


class WorkspaceMetricResult(_FrozenEvaluation):
    metric: WorkspaceMetric
    status: WorkspaceMetricStatus
    numerator: float
    eligible_denominator: int
    evaluated_denominator: int
    unavailable_count: int
    value: float | None


class WorkspaceSliceResult(_FrozenEvaluation):
    slice: WorkspaceGoldenSlice
    status: WorkspaceMetricStatus
    numerator: int
    eligible_denominator: int
    evaluated_denominator: int
    unavailable_count: int
    value: float | None


class WorkspaceEvaluationReport(_FrozenEvaluation):
    dataset_id: str
    dataset_version: str
    package_sha256: str
    authority_sha256: str
    case_count: int
    reviewed_row_count: int
    hard_negative_case_count: int
    metrics: tuple[WorkspaceMetricResult, ...]
    slices: tuple[WorkspaceSliceResult, ...]


def _entity(bundle: WorkspaceFixtureBundle, stable_id: str) -> WorkspaceEntity:
    return next(item for item in bundle.publication.entities if item.stable_id == stable_id)


def _case(
    index: int,
    slice_: WorkspaceGoldenSlice,
    query: str,
    expected: tuple[WorkspaceEntity, ...],
    metrics: tuple[WorkspaceMetric, ...],
    *,
    hard: tuple[WorkspaceEntity, ...] = (),
    edges: tuple[str, ...] = (),
    include_history: bool = False,
    as_of: str | None = None,
) -> WorkspaceGoldenCase:
    global_metrics = (
        WorkspaceMetric.SCOPE_RESOLUTION_ACCURACY,
        WorkspaceMetric.HARD_NEGATIVE_AVOIDANCE,
        WorkspaceMetric.MRR,
        WorkspaceMetric.NDCG_AT_10,
        WorkspaceMetric.AUTHORITY_CLASSIFICATION_ACCURACY,
        WorkspaceMetric.UNSAFE_MUTATION_RATE,
        WorkspaceMetric.ACL_LEAKAGE_RATE,
    )
    return WorkspaceGoldenCase(
        case_id=f"workspace-v1-{index:03d}",
        slice=slice_,
        query=query,
        expected_entity_ids=tuple(item.entity_id for item in expected),
        expected_entity_types=tuple(sorted({item.entity_type for item in expected})),
        expected_authorities=tuple(sorted({item.authority for item in expected})),
        hard_negative_entity_ids=tuple(item.entity_id for item in hard),
        eligible_metrics=tuple(dict.fromkeys((*global_metrics, *metrics))),
        required_edge_types=edges,
        include_history=include_history,
        as_of=as_of,
        expected_empty=not expected,
    )


def _build_cases(bundle: WorkspaceFixtureBundle) -> tuple[WorkspaceGoldenCase, ...]:
    def e(stable_id: str) -> WorkspaceEntity:
        return _entity(bundle, stable_id)

    exact = (WorkspaceMetric.EXACT_ENTITY_ACCURACY,)
    current = (
        WorkspaceMetric.CURRENT_STATE_ACCURACY,
        WorkspaceMetric.STATUS_ACCURACY,
    )
    work = (
        WorkspaceMetric.WORK_ITEM_RECALL_AT_5,
        WorkspaceMetric.ACCEPTANCE_GATE_ACCURACY,
    )
    blocker = (
        WorkspaceMetric.BLOCKER_PRECISION,
        WorkspaceMetric.DEPENDENCY_PATH_ACCURACY,
        WorkspaceMetric.GRAPH_PATH_RECALL,
        WorkspaceMetric.OVERDUE_ACCURACY,
    )
    coverage = (
        WorkspaceMetric.EVIDENCE_COVERAGE_PRECISION,
        WorkspaceMetric.MISSING_ROLE_ACCURACY,
    )
    intelligence = (
        WorkspaceMetric.NEXT_ACTION_RULE_ACCURACY,
        WorkspaceMetric.CURRENT_STATE_ACCURACY,
    )
    decision = (
        WorkspaceMetric.EXACT_ENTITY_ACCURACY,
        WorkspaceMetric.GRAPH_PATH_RECALL,
    )
    temporal = (
        WorkspaceMetric.TEMPORAL_ACCURACY,
        WorkspaceMetric.GRAPH_PATH_RECALL,
    )
    cases = (
        _case(
            1, WorkspaceGoldenSlice.EXACT, "Open PROJECT-RAG", (e("project-rag-control"),), exact
        ),
        _case(2, WorkspaceGoldenSlice.EXACT, "Open TOPIC-ACTIVE", (e("topic-rag-active"),), exact),
        _case(
            3,
            WorkspaceGoldenSlice.EXACT,
            "List all topics named RAG system design including archived history",
            (e("topic-rag-active"), e("topic-rag-archived")),
            (*exact, WorkspaceMetric.DUPLICATE_DISAMBIGUATION_ACCURACY),
            include_history=True,
        ),
        _case(4, WorkspaceGoldenSlice.EXACT, "Open ITER-BETA", (e("iteration-beta"),), exact),
        _case(5, WorkspaceGoldenSlice.EXACT, "Open TASK-0003", (e("work-03"),), exact),
        _case(
            6,
            WorkspaceGoldenSlice.CURRENT,
            "Current active iteration for typed control-plane foundation",
            (e("iteration-alpha"),),
            current,
            hard=(e("iteration-old"),),
        ),
        _case(
            7,
            WorkspaceGoldenSlice.CURRENT,
            "Which iteration is validating release?",
            (e("iteration-beta"),),
            current,
        ),
        _case(
            8,
            WorkspaceGoldenSlice.CURRENT,
            "Current running structured views task",
            (e("work-01"),),
            (*current, WorkspaceMetric.WORK_ITEM_RECALL_AT_5),
        ),
        _case(
            9,
            WorkspaceGoldenSlice.CURRENT,
            "Current review retrieval contract task",
            (e("work-02"),),
            (*current, WorkspaceMetric.WORK_ITEM_RECALL_AT_5),
        ),
        _case(
            10,
            WorkspaceGoldenSlice.CURRENT,
            "Current blocked ACL task",
            (e("work-03"),),
            (*current, WorkspaceMetric.WORK_ITEM_RECALL_AT_5),
        ),
        _case(
            11,
            WorkspaceGoldenSlice.CURRENT,
            "Who owns ITER-ALPHA and when is it due?",
            (e("iteration-alpha"),),
            current,
        ),
        _case(
            12,
            WorkspaceGoldenSlice.WORK,
            "Acceptance criterion for TASK-0004",
            (e("criterion-04"),),
            work,
        ),
        _case(
            13,
            WorkspaceGoldenSlice.WORK,
            "Acceptance check result for TASK-0004",
            (e("check-04"),),
            work,
        ),
        _case(
            14,
            WorkspaceGoldenSlice.WORK,
            "Can TASK-0004 enter done? show criterion and failing check",
            (e("work-04"), e("criterion-04"), e("check-04")),
            work,
            edges=("has_acceptance", "checks"),
        ),
        _case(
            15,
            WorkspaceGoldenSlice.WORK,
            "Show passed acceptance for TASK-0013",
            (e("work-13"), e("criterion-13"), e("check-13")),
            work,
            edges=("has_acceptance", "checks"),
        ),
        _case(
            16,
            WorkspaceGoldenSlice.WORK,
            "Work item awaiting review TASK-0002",
            (e("work-02"),),
            work,
        ),
        _case(17, WorkspaceGoldenSlice.WORK, "Ready temporal snapshot task", (e("work-06"),), work),
        _case(
            18,
            WorkspaceGoldenSlice.BLOCKER,
            "Why is ACL scope blocked?",
            (e("blocker-acl"),),
            blocker,
            edges=("blocked_by",),
        ),
        _case(
            19,
            WorkspaceGoldenSlice.BLOCKER,
            "Dependency path from TASK-0001 to TASK-0003",
            (e("work-01"), e("dependency-01"), e("work-03")),
            blocker,
            edges=("depends_on",),
        ),
        _case(
            20, WorkspaceGoldenSlice.BLOCKER, "Which ACL task is overdue?", (e("work-03"),), blocker
        ),
        _case(
            21,
            WorkspaceGoldenSlice.BLOCKER,
            "Current stale evidence blocker",
            (e("blocker-stale"),),
            blocker,
        ),
        _case(
            22,
            WorkspaceGoldenSlice.BLOCKER,
            "Historical resolved fixture parity blocker",
            (e("blocker-resolved"),),
            blocker,
            include_history=True,
        ),
        _case(
            23,
            WorkspaceGoldenSlice.BLOCKER,
            "Critical default switch risk",
            (e("hazard-default-switch"),),
            blocker,
        ),
        _case(
            24,
            WorkspaceGoldenSlice.COVERAGE,
            "Implementation evidence requirement",
            (e("requirement-implementation"),),
            coverage,
        ),
        _case(
            25,
            WorkspaceGoldenSlice.COVERAGE,
            "Evidence coverage: accepted independent implementation and validation links",
            (e("evidence-link-01"), e("evidence-link-02")),
            coverage,
            hard=(e("evidence-link-09"),),
        ),
        _case(
            26,
            WorkspaceGoldenSlice.COVERAGE,
            "Why is reproduction evidence missing or stale?",
            (e("requirement-reproduction"), e("evidence-link-04")),
            coverage,
        ),
        _case(
            27,
            WorkspaceGoldenSlice.COVERAGE,
            "Reviewed publication evidence",
            (e("requirement-publication"), e("evidence-link-03")),
            coverage,
            hard=(e("evidence-link-10"),),
        ),
        _case(
            28,
            WorkspaceGoldenSlice.COVERAGE,
            "Release quality and rollback evidence coverage",
            (e("requirement-release"), e("evidence-link-07"), e("evidence-link-08")),
            coverage,
        ),
        _case(
            29,
            WorkspaceGoldenSlice.COVERAGE,
            "Security requirement and authorized code-security evidence, not code-private",
            (e("requirement-security"), e("evidence-link-06")),
            coverage,
            hard=(e("evidence-link-12"),),
        ),
        _case(
            30,
            WorkspaceGoldenSlice.INTELLIGENCE,
            "Current workspace intelligence",
            (
                next(
                    item
                    for item in bundle.publication.entities
                    if item.entity_type == WorkspaceEntityType.INTELLIGENCE_RUN and item.current
                ),
            ),
            intelligence,
            hard=(e("intelligence-expired"),),
        ),
        _case(
            31,
            WorkspaceGoldenSlice.INTELLIGENCE,
            "Next action for blockers BLOCK-ACL and BLOCK-STALE",
            (e("blocker-acl"), e("blocker-stale")),
            intelligence,
            hard=(e("intelligence-expired"),),
        ),
        _case(
            32,
            WorkspaceGoldenSlice.INTELLIGENCE,
            "Open INTEL-EXPIRED as current advice",
            (),
            intelligence,
            hard=(e("intelligence-expired"),),
        ),
        _case(
            33,
            WorkspaceGoldenSlice.INTELLIGENCE,
            "Explain current readiness from its snapshot",
            (
                next(
                    item
                    for item in bundle.publication.entities
                    if item.entity_type == WorkspaceEntityType.INTELLIGENCE_RUN and item.current
                ),
                next(
                    item
                    for item in bundle.publication.entities
                    if item.entity_type == WorkspaceEntityType.SNAPSHOT
                ),
            ),
            intelligence,
            edges=("derived_from",),
        ),
        _case(
            34,
            WorkspaceGoldenSlice.INTELLIGENCE,
            "High progress iteration with missing reproduction evidence",
            (e("iteration-alpha"), e("requirement-reproduction")),
            intelligence,
        ),
        _case(
            35,
            WorkspaceGoldenSlice.DECISION,
            "Why keep V1 as default?",
            (e("decision-default-engine"),),
            decision,
        ),
        _case(
            36,
            WorkspaceGoldenSlice.DECISION,
            "Why use source-local calibrated fusion?",
            (e("decision-source-local"),),
            decision,
        ),
        _case(
            37,
            WorkspaceGoldenSlice.DECISION,
            "Compare current fusion decision with superseded raw-score decision",
            (e("decision-source-local"), e("decision-old-scoring")),
            decision,
            edges=("supersedes",),
            include_history=True,
        ),
        _case(
            38,
            WorkspaceGoldenSlice.TEMPORAL,
            "Open TRANS-0001 and show who transitioned TASK-0001 from backlog",
            (e("transition-01"),),
            temporal,
            as_of=WORKSPACE_FIXTURE_OBSERVED_AT,
        ),
        _case(
            39,
            WorkspaceGoldenSlice.TEMPORAL,
            "Workspace authoritative snapshot at 2026-07-29",
            (
                next(
                    item
                    for item in bundle.publication.entities
                    if item.entity_type == WorkspaceEntityType.SNAPSHOT
                ),
            ),
            temporal,
            as_of=WORKSPACE_FIXTURE_OBSERVED_AT,
        ),
        _case(
            40,
            WorkspaceGoldenSlice.TEMPORAL,
            "Compare ITER-ALPHA and ITER-BETA",
            (e("iteration-alpha"), e("iteration-beta")),
            temporal,
        ),
    )
    return cases


def build_workspace_golden_v1() -> WorkspaceGoldenDataset:
    bundle = build_workspace_fixture_v1()
    cases = _build_cases(bundle)
    base = {
        "dataset_id": WORKSPACE_GOLDEN_DATASET_ID,
        "dataset_version": WORKSPACE_GOLDEN_DATASET_VERSION,
        "fixture_recipe_sha256": bundle.recipe_sha256,
        "publication": bundle.publication.model_dump(mode="json"),
        "cases": [item.model_dump(mode="json") for item in cases],
    }
    package = canonical_sha256(base)
    authority = canonical_sha256(
        {
            "dataset_id": WORKSPACE_GOLDEN_DATASET_ID,
            "dataset_version": WORKSPACE_GOLDEN_DATASET_VERSION,
            "package_sha256": package,
            "case_membership": [item.case_id for item in cases],
            "slice_counts": {
                key.value: value for key, value in EXPECTED_WORKSPACE_SLICE_COUNTS.items()
            },
            "publication_sha256": bundle.publication.publication_sha256,
            "entity_ids": sorted(item.entity_id for item in bundle.publication.entities),
        }
    )
    return WorkspaceGoldenDataset(
        **base,
        package_sha256=package,
        authority_sha256=authority,
    )


def _metric_result(
    metric: WorkspaceMetric,
    numerator: float,
    eligible: int,
    evaluated: int,
) -> WorkspaceMetricResult:
    unavailable = eligible - evaluated
    if eligible == 0:
        status = WorkspaceMetricStatus.UNAVAILABLE
        value = None
    elif evaluated < eligible:
        status = (
            WorkspaceMetricStatus.UNAVAILABLE
            if evaluated == 0
            else WorkspaceMetricStatus.PROVISIONAL
        )
        value = None if evaluated == 0 else numerator / evaluated
    else:
        status = WorkspaceMetricStatus.AVAILABLE
        value = numerator / eligible
    return WorkspaceMetricResult(
        metric=metric,
        status=status,
        numerator=numerator,
        eligible_denominator=eligible,
        evaluated_denominator=evaluated,
        unavailable_count=unavailable,
        value=value,
    )


def evaluate_reviewed_workspace_retrieval(
    reviewed_rows: tuple[WorkspaceReviewedRow, ...],
    *,
    dataset: WorkspaceGoldenDataset | None = None,
    case_membership: tuple[str, ...] | None = None,
) -> WorkspaceEvaluationReport:
    dataset = dataset or build_workspace_golden_v1()
    expected_membership = tuple(item.case_id for item in dataset.cases)
    if case_membership is not None and case_membership != expected_membership:
        raise WorkspaceGoldenError("Workspace evaluation membership is not exact released 40")
    if tuple(item.case_id for item in reviewed_rows) != expected_membership:
        raise WorkspaceGoldenError("Workspace reviewed rows are incomplete or reordered")
    cases = {item.case_id: item for item in dataset.cases}
    entities = dataset.entity_map()
    metric_eligible: Counter[WorkspaceMetric] = Counter()
    metric_evaluated: Counter[WorkspaceMetric] = Counter()
    metric_numerator: defaultdict[WorkspaceMetric, float] = defaultdict(float)
    slice_eligible: Counter[WorkspaceGoldenSlice] = Counter()
    slice_evaluated: Counter[WorkspaceGoldenSlice] = Counter()
    slice_numerator: Counter[WorkspaceGoldenSlice] = Counter()
    for row in reviewed_rows:
        case = cases[row.case_id]
        slice_eligible[case.slice] += 1
        for metric in case.eligible_metrics:
            metric_eligible[metric] += 1
        if row.availability != WorkspaceReviewAvailability.AVAILABLE:
            continue
        slice_evaluated[case.slice] += 1
        candidate_entities: list[WorkspaceEntity] = []
        for reviewed in row.candidates:
            entity = entities.get(reviewed.entity_id)
            if entity is None or entity.locator != reviewed.locator:
                raise WorkspaceGoldenError(
                    "Workspace reviewed candidate is outside released authority"
                )
            candidate_entities.append(entity)
        candidate_ids = tuple(item.entity_id for item in candidate_entities)
        positions = [
            candidate_ids.index(item) + 1
            for item in case.expected_entity_ids
            if item in candidate_ids
        ]
        full_hit = (
            not candidate_ids
            if case.expected_empty
            else set(case.expected_entity_ids).issubset(candidate_ids)
        )
        hard_safe = not set(case.hard_negative_entity_ids) & set(candidate_ids)
        authority_correct = not candidate_entities or {
            item.authority
            for item in candidate_entities
            if item.entity_id in case.expected_entity_ids
        } == set(case.expected_authorities)
        unsafe_mutation = any(
            bool(item.metadata.get("authoritative_state_mutated")) for item in candidate_entities
        )
        acl_leakage = any(
            item.entity_type == WorkspaceEntityType.EVIDENCE_LINK
            and item.metadata.get("external_acl_ref") != item.scope.acl_ref
            for item in candidate_entities
        )
        if full_hit:
            slice_numerator[case.slice] += 1
        for metric in case.eligible_metrics:
            metric_evaluated[metric] += 1
            success = 0.0
            if metric in {
                WorkspaceMetric.SCOPE_RESOLUTION_ACCURACY,
                WorkspaceMetric.EXACT_ENTITY_ACCURACY,
                WorkspaceMetric.CURRENT_STATE_ACCURACY,
                WorkspaceMetric.TEMPORAL_ACCURACY,
                WorkspaceMetric.WORK_ITEM_RECALL_AT_5,
                WorkspaceMetric.GRAPH_PATH_RECALL,
                WorkspaceMetric.DUPLICATE_DISAMBIGUATION_ACCURACY,
                WorkspaceMetric.STATUS_ACCURACY,
                WorkspaceMetric.OVERDUE_ACCURACY,
                WorkspaceMetric.BLOCKER_PRECISION,
                WorkspaceMetric.DEPENDENCY_PATH_ACCURACY,
                WorkspaceMetric.ACCEPTANCE_GATE_ACCURACY,
                WorkspaceMetric.EVIDENCE_COVERAGE_PRECISION,
                WorkspaceMetric.MISSING_ROLE_ACCURACY,
                WorkspaceMetric.NEXT_ACTION_RULE_ACCURACY,
            }:
                success = float(full_hit)
            elif metric == WorkspaceMetric.AUTHORITY_CLASSIFICATION_ACCURACY:
                success = float(full_hit and authority_correct)
            elif metric == WorkspaceMetric.HARD_NEGATIVE_AVOIDANCE:
                success = float(hard_safe)
            elif metric == WorkspaceMetric.UNSAFE_MUTATION_RATE:
                success = float(unsafe_mutation)
            elif metric == WorkspaceMetric.ACL_LEAKAGE_RATE:
                success = float(acl_leakage)
            elif metric == WorkspaceMetric.MRR:
                success = 0.0 if not positions else 1.0 / min(positions)
            elif metric == WorkspaceMetric.NDCG_AT_10 and positions:
                dcg = sum(1.0 / math.log2(position + 1) for position in positions)
                ideal = sum(
                    1.0 / math.log2(position + 1)
                    for position in range(1, len(case.expected_entity_ids) + 1)
                )
                success = dcg / ideal
            metric_numerator[metric] += success
    metrics = tuple(
        _metric_result(
            metric,
            metric_numerator[metric],
            metric_eligible[metric],
            metric_evaluated[metric],
        )
        for metric in WorkspaceMetric
        if metric_eligible[metric]
    )
    slices = tuple(
        WorkspaceSliceResult(
            slice=slice_,
            status=(
                WorkspaceMetricStatus.AVAILABLE
                if slice_evaluated[slice_] == slice_eligible[slice_]
                else WorkspaceMetricStatus.UNAVAILABLE
                if not slice_evaluated[slice_]
                else WorkspaceMetricStatus.PROVISIONAL
            ),
            numerator=slice_numerator[slice_],
            eligible_denominator=slice_eligible[slice_],
            evaluated_denominator=slice_evaluated[slice_],
            unavailable_count=slice_eligible[slice_] - slice_evaluated[slice_],
            value=(
                slice_numerator[slice_] / slice_evaluated[slice_]
                if slice_evaluated[slice_]
                else None
            ),
        )
        for slice_ in WorkspaceGoldenSlice
    )
    return WorkspaceEvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        package_sha256=dataset.package_sha256,
        authority_sha256=dataset.authority_sha256,
        case_count=len(dataset.cases),
        reviewed_row_count=len(reviewed_rows),
        hard_negative_case_count=sum(bool(item.hard_negative_entity_ids) for item in dataset.cases),
        metrics=metrics,
        slices=slices,
    )


__all__ = [
    "EXPECTED_WORKSPACE_SLICE_COUNTS",
    "WORKSPACE_GOLDEN_AUTHORITY_SHA256",
    "WORKSPACE_GOLDEN_CASE_COUNT",
    "WORKSPACE_GOLDEN_PACKAGE_SHA256",
    "WorkspaceEvaluationReport",
    "WorkspaceGoldenCase",
    "WorkspaceGoldenDataset",
    "WorkspaceGoldenError",
    "WorkspaceGoldenSlice",
    "WorkspaceMetric",
    "WorkspaceMetricResult",
    "WorkspaceMetricStatus",
    "WorkspaceReviewAvailability",
    "WorkspaceReviewedCandidate",
    "WorkspaceReviewedRow",
    "build_workspace_golden_v1",
    "evaluate_reviewed_workspace_retrieval",
]
