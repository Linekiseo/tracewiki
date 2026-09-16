from __future__ import annotations

from typing import Any

from evidence_rag.rag.sources.document.contracts import canonical_sha256
from evidence_rag.rag.sources.document.release_v2 import (
    DocumentEvidenceStatus,
    DocumentReleaseEvidence,
    DocumentReleaseGuardrail,
    DocumentReleaseMetric,
    DocumentReleaseStage,
    evaluate_document_release_v2,
)
from evidence_rag.rag.sources.experiment.governance_v2 import (
    EvidenceAvailabilityV2,
    ExperimentReleaseMetricV2,
    ExperimentReleaseStageV2,
    build_experiment_release_evidence_v2,
    evaluate_experiment_release_v2,
)
from evidence_rag.rag.sources.workspace.release_v2 import (
    WORKSPACE_RELEASE_THRESHOLDS,
    WORKSPACE_REQUIRED_GUARDRAILS,
    WorkspaceEvidenceStatus,
    WorkspaceReleaseGuardrail,
    WorkspaceReleaseMetric,
    WorkspaceReleaseStage,
    build_workspace_release_evidence_v2,
    evaluate_workspace_release_v2,
)


def _digest(label: str) -> str:
    return canonical_sha256({"isolated_test_release_evidence": label})


def install_document_v2_authority(runtime: Any, *, project_id: str = "project-rag") -> None:
    values = {
        "evaluator_version": "document-release-evaluator-v2",
        "observed_stage": DocumentReleaseStage.CANARY_25,
        "proposed_stage": DocumentReleaseStage.OPT_IN_100,
        "artifact_set_sha256": _digest("document-artifact-set"),
        "golden_package_sha256": _digest("document-golden-package"),
        "retriever_version": "document-source-runtime-v2-test",
        "reranker_version": "document-source-reranker-v2-test",
        "context_builder_version": "document-context-builder-v2-test",
        "production_observation": True,
        "metrics": (
            DocumentReleaseMetric(
                name="isolated_test_quality",
                status=DocumentEvidenceStatus.AVAILABLE,
                numerator=1,
                denominator=1,
                value=1,
                threshold=1,
                direction="min",
            ),
        ),
        "guardrails": (
            DocumentReleaseGuardrail(
                name="isolated_test_secret_leakage",
                status=DocumentEvidenceStatus.AVAILABLE,
                violations=0,
            ),
        ),
    }
    identity_payload = DocumentReleaseEvidence.model_construct(
        evidence_id="pending",
        **values,
    ).model_dump(mode="json", exclude={"evidence_id"})
    evidence = DocumentReleaseEvidence(
        evidence_id="docrelease-" + canonical_sha256(identity_payload).removeprefix("sha256:")[:64],
        **values,
    )
    decision = evaluate_document_release_v2(evidence)
    runtime.source_runtime_v2.install_release_authority(
        source="document",
        project_id=project_id,
        evidence=evidence,
        decision=decision,
    )


def install_workspace_v2_authority(runtime: Any, *, project_id: str = "project-rag") -> None:
    metrics = tuple(
        WorkspaceReleaseMetric(
            name=name,
            status=WorkspaceEvidenceStatus.AVAILABLE,
            value=(threshold if direction == "min" else 0.0),
            numerator=(threshold if direction == "min" else 0.0),
            denominator=1,
        )
        for name, (direction, threshold) in WORKSPACE_RELEASE_THRESHOLDS.items()
    )
    guardrails = tuple(
        WorkspaceReleaseGuardrail(
            name=name,
            status=WorkspaceEvidenceStatus.AVAILABLE,
            violations=0,
        )
        for name in WORKSPACE_REQUIRED_GUARDRAILS
    )
    evidence = build_workspace_release_evidence_v2(
        observed_stage=WorkspaceReleaseStage.CANARY_25,
        proposed_stage=WorkspaceReleaseStage.OPT_IN_100,
        golden_package_sha256=_digest("workspace-golden-package"),
        artifact_set_sha256=_digest("workspace-artifact-set"),
        retriever_version="workspace-source-runtime-v2-test",
        reranker_version="workspace-source-reranker-v2-test",
        context_builder_version="workspace-context-builder-v2-test",
        metrics=metrics,
        guardrails=guardrails,
        production_observation=True,
    )
    decision = evaluate_workspace_release_v2(evidence)
    runtime.source_runtime_v2.install_release_authority(
        source="workspace",
        project_id=project_id,
        evidence=evidence,
        decision=decision,
    )


def install_experiment_v2_authority(runtime: Any, *, project_id: str = "project-rag") -> None:
    evidence = build_experiment_release_evidence_v2(
        project_id=project_id,
        stage=ExperimentReleaseStageV2.CANARY_25,
        previous_evidence_sha256=_digest("experiment-previous-evidence"),
        baseline_artifact_sha256=_digest("experiment-baseline-artifact"),
        treatment_artifact_sha256=_digest("experiment-treatment-artifact"),
        metrics=(
            ExperimentReleaseMetricV2(
                name="isolated_test_accuracy",
                numerator=1,
                denominator=1,
                value=1,
                threshold=1,
                comparison="gte",
                availability=EvidenceAvailabilityV2.AVAILABLE,
                source_evidence_sha256=_digest("experiment-metric-evidence"),
            ),
        ),
        secret_leakage_count=0,
        acl_leakage_count=0,
        truth_drift_count=0,
        fallback_count=0,
        p95_latency_ms=1,
        storage_bytes=1,
        rollback_rehearsed=True,
        synthetic=False,
        production_observation=True,
    )
    decision = evaluate_experiment_release_v2(
        evidence,
        deployed_stage=ExperimentReleaseStageV2.CANARY_25,
    )
    runtime.source_runtime_v2.install_release_authority(
        source="experiment",
        project_id=project_id,
        evidence=evidence,
        decision=decision,
    )
