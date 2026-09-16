from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.code import (
    ARTIFACT_ORDER,
    CB0_QUALIFIED_RUN_ID,
    CB6_PROVISIONAL_RUN_ID,
    GATE_REQUIREMENTS,
    GUARDRAIL_ORDER,
    MINIMUM_STAGE_SAMPLES,
    RELEASE_STAGE_ORDER,
    REQUIRED_ARTIFACT_DISPOSITION,
    ROLLBACK_ACTIONS,
    ArtifactDisposition,
    ArtifactVersionExpectation,
    EvidenceClass,
    EvidenceSource,
    EvidenceStatus,
    GuardrailName,
    GuardrailStatus,
    MetricUnit,
    ReleaseArtifact,
    ReleaseArtifactKind,
    ReleaseCostEvidence,
    ReleaseDecisionStatus,
    ReleaseEvidence,
    ReleaseGuardrail,
    ReleaseLatencyEvidence,
    ReleaseMetric,
    ReleaseMetricName,
    ReleasePolicy,
    ReleaseStage,
    ReleaseVersions,
    RollbackAction,
    SanitizedShadowCounters,
    ThresholdOperator,
    aggregate_shadow_evidence,
    create_test_attestation,
    current_release_evidence,
    evaluate_release,
)
from evidence_rag.rag.sources.code import (
    TestHMACSHA256Verifier as HMACVerifier,
)

NOW = datetime(2026, 7, 29, 1, 0, tzinfo=UTC)
VERIFIER = HMACVerifier(
    verifier_id="test-release-verifier",
    verifier_version="test-release-verifier-v1",
    key=b"c7-03-test-verifier-key-material-0001",
)
VERSIONS = ReleaseVersions(
    code_golden_version="code-golden-test-v1",
    evaluation_profile_version="release-profile-test-v1",
    builder_version="builder-test-v1",
    index_version="index-test-v1",
    model_version="model-test-v1",
    scip_version="scip-test-v1",
)
ARTIFACT_VERSIONS = {kind: f"{kind.value}-test-v1" for kind in ARTIFACT_ORDER}
POLICY = ReleasePolicy(
    expected_versions=VERSIONS,
    artifact_versions=tuple(
        ArtifactVersionExpectation(
            kind=kind,
            artifact_version=ARTIFACT_VERSIONS[kind],
        )
        for kind in ARTIFACT_ORDER
    ),
    metric_artifact_version="metric-artifact-test-v1",
    guardrail_artifact_version="guardrail-artifact-test-v1",
    latency_artifact_version="metric-artifact-test-v1",
    cost_artifact_version="cost-artifact-test-v1",
    shadow_artifact_version="shadow-artifact-test-v1",
    release_artifact_version="release-snapshot-test-v1",
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _source(
    label: str,
    version: str,
    *,
    stage: ReleaseStage,
    parent_digest: str | None,
) -> EvidenceSource:
    issuance_label = f"{label}:{stage.value}:{parent_digest or 'root'}"
    return EvidenceSource(
        release_stage=stage,
        parent_evidence_digest=parent_digest,
        artifact_id=f"test://release/{label}",
        artifact_version=version,
        artifact_digest=_digest(issuance_label),
    )


def _sign(component: Any) -> Any:
    assert component.source is not None
    unsigned = component.model_copy(update={"attestation": None})
    return unsigned.model_copy(
        update={
            "attestation": create_test_attestation(
                subject_digest=unsigned.attested_digest(),
                source=unsigned.source,
                verifier=VERIFIER,
                release_stage=unsigned.release_stage,
                parent_evidence_digest=unsigned.parent_evidence_digest,
            )
        }
    )


_BOUNDARY_COUNTS = {
    ReleaseMetricName.SYMBOL_RECALL_AT_10: (85.0, 100),
    ReleaseMetricName.FILE_RECALL_AT_10: (90.0, 100),
    ReleaseMetricName.MRR_AT_10: (21.0, 30),
    ReleaseMetricName.REQUIRED_PATH_RECALL: (75.0, 100),
    ReleaseMetricName.EXACT_COMMIT_ACCURACY: (95.0, 100),
    ReleaseMetricName.WRONG_VERSION_RATE: (5.0, 250),
    ReleaseMetricName.FALSE_VALIDATION_RATE: (0.0, 120),
    ReleaseMetricName.LOCATOR_ACCURACY: (245.0, 250),
    ReleaseMetricName.HARMFUL_CANDIDATE_RATE_AT_10: (5.0, 100),
    ReleaseMetricName.LATENCY_P95_MS: (1_500.0, 33),
    ReleaseMetricName.UNAUTHORIZED_LEAKAGE_RATE: (0.0, 224),
    ReleaseMetricName.SECRET_LEAKAGE_RATE: (0.0, 224),
}


def _metric(
    name: ReleaseMetricName,
    *,
    stage: ReleaseStage,
    parent_digest: str | None,
) -> ReleaseMetric:
    requirement = next(item for item in GATE_REQUIREMENTS if item.name is name)
    numerator, denominator = _BOUNDARY_COUNTS[name]
    value = numerator / denominator if requirement.unit is MetricUnit.RATIO else numerator
    return _sign(
        ReleaseMetric(
            release_stage=stage,
            parent_evidence_digest=parent_digest,
            name=name,
            value=value,
            status=EvidenceStatus.AVAILABLE,
            numerator=numerator,
            denominator=denominator,
            unit=requirement.unit,
            source=_source(
                f"metric-{name.value}",
                POLICY.metric_artifact_version,
                stage=stage,
                parent_digest=parent_digest,
            ),
        )
    )


def _guardrail(
    name: GuardrailName,
    sample_count: int,
    *,
    stage: ReleaseStage,
    parent_digest: str | None,
) -> ReleaseGuardrail:
    return _sign(
        ReleaseGuardrail(
            release_stage=stage,
            parent_evidence_digest=parent_digest,
            name=name,
            status=GuardrailStatus.PASS,
            numerator=sample_count,
            denominator=sample_count,
            source=_source(
                f"guardrail-{name.value}-{sample_count}",
                POLICY.guardrail_artifact_version,
                stage=stage,
                parent_digest=parent_digest,
            ),
        )
    )


def _artifact(
    kind: ReleaseArtifactKind,
    *,
    stage: ReleaseStage,
    parent_digest: str | None,
) -> ReleaseArtifact:
    return _sign(
        ReleaseArtifact(
            release_stage=stage,
            parent_evidence_digest=parent_digest,
            kind=kind,
            artifact_id=f"test://release-artifact/{kind.value}",
            evidence_status=EvidenceStatus.AVAILABLE,
            disposition=REQUIRED_ARTIFACT_DISPOSITION[kind],
            source=_source(
                f"artifact-{kind.value}",
                ARTIFACT_VERSIONS[kind],
                stage=stage,
                parent_digest=parent_digest,
            ),
            reason="synthetic_test_fixture_only",
        )
    )


def _snapshot(
    stage: ReleaseStage,
    *,
    previous: ReleaseEvidence | None = None,
) -> ReleaseEvidence:
    parent_digest = None if previous is None else previous.canonical_sha256()
    sample_count = MINIMUM_STAGE_SAMPLES[stage]
    metrics = tuple(
        _metric(name, stage=stage, parent_digest=parent_digest) for name in ReleaseMetricName
    )
    latency_metric = next(
        metric for metric in metrics if metric.name is ReleaseMetricName.LATENCY_P95_MS
    )
    latency = _sign(
        ReleaseLatencyEvidence(
            release_stage=stage,
            parent_evidence_digest=parent_digest,
            status=EvidenceStatus.AVAILABLE,
            sample_count=latency_metric.denominator,
            p50_ms=500.0,
            p95_ms=latency_metric.value,
            p99_ms=1_600.0,
            source=latency_metric.source,
        )
    )
    cost = _sign(
        ReleaseCostEvidence(
            release_stage=stage,
            parent_evidence_digest=parent_digest,
            status=EvidenceStatus.AVAILABLE,
            index_bytes=1_000_000,
            ingest_time_ms=1_000.0,
            source=_source(
                "cost",
                POLICY.cost_artifact_version,
                stage=stage,
                parent_digest=parent_digest,
            ),
        )
    )
    shadow = aggregate_shadow_evidence(
        (
            SanitizedShadowCounters(
                sample_count=sample_count,
                latency_ms=(100.0,) * sample_count,
                error_count=0,
                exception_count=0,
                fallback_count=0,
                compatibility_failure_count=0,
                security_violation_count=0,
                unauthorized_leakage_count=0,
                secret_leakage_count=0,
                model_unavailable_count=0,
                semantic_fallback_count=0,
            ),
        ),
        release_stage=stage,
        parent_evidence_digest=parent_digest,
        source=_source(
            f"shadow-{stage.value}",
            POLICY.shadow_artifact_version,
            stage=stage,
            parent_digest=parent_digest,
        ),
    )
    shadow = _sign(shadow)
    unsigned = ReleaseEvidence(
        release_stage=stage,
        parent_evidence_digest=parent_digest,
        evidence_class=EvidenceClass.TEST,
        stage=stage,
        generated_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        previous_evidence_digest=parent_digest,
        versions=VERSIONS,
        sample_count=sample_count,
        metrics=metrics,
        guardrails=tuple(
            _guardrail(
                name,
                sample_count,
                stage=stage,
                parent_digest=parent_digest,
            )
            for name in GUARDRAIL_ORDER
        ),
        artifacts=tuple(
            _artifact(kind, stage=stage, parent_digest=parent_digest) for kind in ARTIFACT_ORDER
        ),
        latency=latency,
        cost=cost,
        shadow=shadow,
        source=_source(
            f"snapshot-{stage.value}",
            POLICY.release_artifact_version,
            stage=stage,
            parent_digest=parent_digest,
        ),
    )
    return _sign(unsigned)


def _replace_metric(
    evidence: ReleaseEvidence,
    replacement: ReleaseMetric,
) -> ReleaseEvidence:
    metrics = tuple(
        replacement if metric.name is replacement.name else metric for metric in evidence.metrics
    )
    return _sign(evidence.model_copy(update={"metrics": metrics}))


def _decision(
    evidence: ReleaseEvidence,
    *,
    prior: tuple[ReleaseEvidence, ...] = (),
    deployed_stage: ReleaseStage | str | None = None,
    allow_test_evidence: bool = True,
    policy: ReleasePolicy | None = POLICY,
    verification_timed_out: bool = False,
):
    return evaluate_release(
        evidence,
        deployed_stage=evidence.stage if deployed_stage is None else deployed_stage,
        policy=policy,
        verifiers=(VERIFIER,),
        prior_evidence=prior,
        evaluated_at=NOW,
        allow_test_evidence=allow_test_evidence,
        verification_timed_out=verification_timed_out,
    )


def test_current_reviewed_truth_is_hold_default_v1_and_never_quality_pass() -> None:
    evidence = current_release_evidence(observed_at=NOW)
    policy = ReleasePolicy(
        expected_versions=evidence.versions,
        artifact_versions=POLICY.artifact_versions,
        metric_artifact_version=POLICY.metric_artifact_version,
        guardrail_artifact_version=POLICY.guardrail_artifact_version,
        latency_artifact_version=POLICY.latency_artifact_version,
        cost_artifact_version=POLICY.cost_artifact_version,
        shadow_artifact_version=POLICY.shadow_artifact_version,
        release_artifact_version=POLICY.release_artifact_version,
    )

    decision = evaluate_release(
        evidence,
        deployed_stage=ReleaseStage.OFFLINE,
        policy=policy,
        evaluated_at=NOW,
    )
    no_policy = evaluate_release(
        evidence,
        deployed_stage=ReleaseStage.OFFLINE,
        evaluated_at=NOW,
    )

    assert decision.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert decision.runtime_engine == "v1"
    assert decision.configuration_changed is False
    assert decision.engineering_pass_is_release_quality is False
    assert decision.release_quality_passed is False
    assert no_policy.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert "release_policy:missing" in no_policy.blockers
    assert "metric:symbol_recall_at_10:missing" in decision.blockers
    assert "guardrail:acl_security_suite:missing" in decision.blockers
    assert "artifact:c_b1:disposition_not_qualified" in decision.blockers
    assert "artifact:c_b5:disposition_not_qualified" in decision.blockers
    assert "artifact:c_b6:disposition_provisional_not_qualified" in decision.blockers
    artifacts = {artifact.kind: artifact for artifact in evidence.artifacts}
    assert artifacts[ReleaseArtifactKind.C_B0].artifact_id == CB0_QUALIFIED_RUN_ID
    assert artifacts[ReleaseArtifactKind.C_B0].disposition is ArtifactDisposition.QUALIFIED
    assert artifacts[ReleaseArtifactKind.C_B6].artifact_id == CB6_PROVISIONAL_RUN_ID
    assert (
        artifacts[ReleaseArtifactKind.C_B6].disposition
        is ArtifactDisposition.PROVISIONAL_NOT_QUALIFIED
    )
    assert artifacts[ReleaseArtifactKind.C_B6].evidence_status is EvidenceStatus.UNAVAILABLE
    assert all(
        artifact.evidence_status is EvidenceStatus.UNAVAILABLE for artifact in evidence.artifacts
    )


def test_complete_signed_test_fixture_promotes_only_through_every_stage() -> None:
    chain: list[ReleaseEvidence] = []
    for index, stage in enumerate(RELEASE_STAGE_ORDER):
        evidence = _snapshot(stage, previous=chain[-1] if chain else None)
        parent_digest = None if not chain else chain[-1].canonical_sha256()
        children = (
            *evidence.metrics,
            *evidence.guardrails,
            *evidence.artifacts,
            evidence.latency,
            evidence.cost,
            evidence.shadow,
        )
        assert all(child is not None for child in children)
        for child in children:
            assert child is not None
            assert child.release_stage is stage
            assert child.parent_evidence_digest == parent_digest
            assert child.source is not None
            assert child.source.release_stage is stage
            assert child.source.parent_evidence_digest == parent_digest
            assert child.attestation is not None
            assert child.attestation.release_stage is stage
            assert child.attestation.parent_evidence_digest == parent_digest
        decision = _decision(evidence, prior=tuple(chain))
        assert decision.status is ReleaseDecisionStatus.PROMOTION_APPROVED
        assert decision.evidence_stage is stage
        assert decision.trusted_deployed_stage is stage
        assert decision.next_stage == (
            RELEASE_STAGE_ORDER[index + 1] if index + 1 < len(RELEASE_STAGE_ORDER) else None
        )
        assert decision.runtime_engine == "v1"
        assert decision.configuration_changed is False
        chain.append(evidence)


def test_trusted_deployed_stage_authority_is_required_and_invalid_is_rollback() -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)

    with pytest.raises(TypeError, match="deployed_stage"):
        evaluate_release(  # type: ignore[call-arg]
            evidence,
            policy=POLICY,
            evaluated_at=NOW,
        )

    invalid = evaluate_release(
        evidence,
        deployed_stage="INVALID_STAGE",
        policy=POLICY,
        evaluated_at=NOW,
    )
    assert invalid.status is ReleaseDecisionStatus.ROLLBACK_REQUIRED
    assert invalid.trusted_deployed_stage is None
    assert invalid.blockers == ("trusted_deployed_stage:invalid",)


@pytest.mark.parametrize(
    ("deployed_stage", "expected_status"),
    [
        (ReleaseStage.OFFLINE, ReleaseDecisionStatus.HOLD_DEFAULT_V1),
        (
            ReleaseStage.SHADOW_INTERNAL_100,
            ReleaseDecisionStatus.HOLD_DEFAULT_V1,
        ),
        (ReleaseStage.CANARY_5, ReleaseDecisionStatus.ROLLBACK_REQUIRED),
        (ReleaseStage.CANARY_25, ReleaseDecisionStatus.ROLLBACK_REQUIRED),
        (ReleaseStage.OPT_IN_100, ReleaseDecisionStatus.ROLLBACK_REQUIRED),
        (ReleaseStage.DEFAULT_V2, ReleaseDecisionStatus.ROLLBACK_REQUIRED),
    ],
)
def test_invalid_candidate_stage_severity_comes_only_from_trusted_authority(
    deployed_stage: ReleaseStage,
    expected_status: ReleaseDecisionStatus,
) -> None:
    invalid = _snapshot(ReleaseStage.OFFLINE).model_dump(mode="python")
    invalid["stage"] = "INVALID_STAGE"

    decision = evaluate_release(
        invalid,
        deployed_stage=deployed_stage,
        policy=POLICY,
        evaluated_at=NOW,
    )

    assert decision.status is expected_status
    assert decision.trusted_deployed_stage is deployed_stage
    assert decision.blockers == ("release_contract:invalid_or_tampered",)


def test_canary_authority_makes_candidate_downgrade_and_top_tamper_rollback() -> None:
    offline = _snapshot(ReleaseStage.OFFLINE)
    downgraded = _decision(
        offline,
        deployed_stage=ReleaseStage.CANARY_5,
    )
    assert downgraded.status is ReleaseDecisionStatus.ROLLBACK_REQUIRED
    assert "trusted_deployed_stage:candidate_stage_mismatch" in downgraded.blockers

    shadow = _snapshot(ReleaseStage.SHADOW_INTERNAL_100, previous=offline)
    canary = _snapshot(ReleaseStage.CANARY_5, previous=shadow)
    assert canary.attestation is not None
    bad_top_attestation = canary.attestation.model_copy(
        update={"signature": "hmac-sha256:" + ("0" * 64)}
    )
    tampered = canary.model_copy(update={"attestation": bad_top_attestation})
    decision = _decision(tampered, prior=(offline, shadow))

    assert decision.status is ReleaseDecisionStatus.ROLLBACK_REQUIRED
    assert "release_snapshot:signature_invalid" in decision.blockers


def test_synthetic_fixture_requires_explicit_test_marker_and_test_verifier() -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)

    unauthorized = _decision(evidence, allow_test_evidence=False)
    assert unauthorized.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert "test_evidence:not_authorized" in unauthorized.blockers

    no_verifier = evaluate_release(
        evidence,
        deployed_stage=evidence.stage,
        policy=POLICY,
        evaluated_at=NOW,
        allow_test_evidence=True,
    )
    assert no_verifier.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert "release_snapshot:verifier_unavailable" in no_verifier.blockers

    no_evaluation_time = evaluate_release(
        evidence,
        deployed_stage=evidence.stage,
        policy=POLICY,
        verifiers=(VERIFIER,),
        allow_test_evidence=True,
    )
    assert no_evaluation_time.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert no_evaluation_time.blockers == ("evaluated_at:missing",)

    production_claim = _sign(
        evidence.model_copy(update={"evidence_class": EvidenceClass.PRODUCTION})
    )
    forbidden = _decision(production_claim)
    assert "release_snapshot:test_verifier_forbidden_in_production" in (forbidden.blockers)


def test_all_exact_gate_boundaries_are_accepted() -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)

    decision = _decision(evidence)

    assert decision.status is ReleaseDecisionStatus.PROMOTION_APPROVED
    assert {metric.name: metric.value for metric in evidence.metrics} == {
        ReleaseMetricName.SYMBOL_RECALL_AT_10: 0.85,
        ReleaseMetricName.FILE_RECALL_AT_10: 0.9,
        ReleaseMetricName.MRR_AT_10: 0.7,
        ReleaseMetricName.REQUIRED_PATH_RECALL: 0.75,
        ReleaseMetricName.EXACT_COMMIT_ACCURACY: 0.95,
        ReleaseMetricName.WRONG_VERSION_RATE: 0.02,
        ReleaseMetricName.FALSE_VALIDATION_RATE: 0.0,
        ReleaseMetricName.LOCATOR_ACCURACY: 0.98,
        ReleaseMetricName.HARMFUL_CANDIDATE_RATE_AT_10: 0.05,
        ReleaseMetricName.LATENCY_P95_MS: 1_500.0,
        ReleaseMetricName.UNAUTHORIZED_LEAKAGE_RATE: 0.0,
        ReleaseMetricName.SECRET_LEAKAGE_RATE: 0.0,
    }


@pytest.mark.parametrize("requirement", GATE_REQUIREMENTS, ids=lambda item: item.name.value)
def test_each_gate_rejects_the_first_value_beyond_its_boundary(
    requirement,
) -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)
    metric = next(item for item in evidence.metrics if item.name is requirement.name)
    if requirement.unit is MetricUnit.MILLISECONDS:
        failed = metric.model_copy(update={"value": 1_500.01, "numerator": 1_500.01})
    elif requirement.operator is ThresholdOperator.GREATER_THAN_OR_EQUAL:
        failed = metric.model_copy(
            update={
                "numerator": metric.numerator - 1,
                "value": (metric.numerator - 1) / metric.denominator,
            }
        )
    else:
        failed = metric.model_copy(
            update={
                "numerator": metric.numerator + 1,
                "value": (metric.numerator + 1) / metric.denominator,
            }
        )
    failed = _sign(failed)
    evidence = _replace_metric(evidence, failed)

    decision = _decision(evidence)

    assert decision.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert f"metric:{requirement.name.value}:threshold_failed" in decision.blockers


@pytest.mark.parametrize(
    "status",
    [EvidenceStatus.PROVISIONAL, EvidenceStatus.UNAVAILABLE],
)
def test_provisional_and_unavailable_metrics_cannot_promote(
    status: EvidenceStatus,
) -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)
    original = evidence.metrics[0]
    update: dict[str, Any] = {"status": status}
    if status is EvidenceStatus.UNAVAILABLE:
        update.update(value=None, numerator=0.0, denominator=0)
    replacement = _sign(original.model_copy(update=update))
    evidence = _replace_metric(evidence, replacement)

    decision = _decision(evidence)

    assert f"metric:{original.name.value}:status_{status.value.lower()}" in decision.blockers


def test_denominator_minimum_and_contract_count_contradictions_fail_closed() -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)
    file_metric = next(
        metric for metric in evidence.metrics if metric.name is ReleaseMetricName.FILE_RECALL_AT_10
    )
    small = _sign(
        file_metric.model_copy(update={"numerator": 29.0, "denominator": 32, "value": 29 / 32})
    )
    decision = _decision(_replace_metric(evidence, small))
    assert "metric:file_recall_at_10:denominator_below_minimum" in decision.blockers

    payload = evidence.model_dump(mode="python")
    payload["metrics"][0].update(
        status=EvidenceStatus.AVAILABLE,
        value=None,
        numerator=0.0,
        denominator=0,
    )
    invalid = evaluate_release(
        payload,
        deployed_stage=ReleaseStage.OFFLINE,
        policy=POLICY,
    )
    assert invalid.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert invalid.blockers == ("release_contract:invalid_or_tampered",)


def test_digest_signature_and_summary_tampering_all_fail_closed() -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)
    metric = evidence.metrics[0]
    tampered_source = metric.source.model_copy(
        update={"artifact_digest": _digest("different-artifact")}
    )
    source_tamper = _replace_metric(
        evidence,
        metric.model_copy(update={"source": tampered_source}),
    )
    source_decision = _decision(source_tamper)
    assert "metric:symbol_recall_at_10:attestation_binding_mismatch" in (source_decision.blockers)

    assert metric.attestation is not None
    bad_attestation = metric.attestation.model_copy(
        update={"signature": "hmac-sha256:" + ("0" * 64)}
    )
    signature_tamper = _replace_metric(
        evidence,
        metric.model_copy(update={"attestation": bad_attestation}),
    )
    signature_decision = _decision(signature_tamper)
    assert "metric:symbol_recall_at_10:signature_invalid" in (signature_decision.blockers)

    summary_tamper = evidence.model_dump(mode="python")
    summary_tamper["summary"] = {"quality": "PASS"}
    summary_decision = evaluate_release(
        summary_tamper,
        deployed_stage=ReleaseStage.OFFLINE,
        policy=POLICY,
    )
    assert summary_decision.blockers == ("release_contract:invalid_or_tampered",)


@pytest.mark.parametrize(
    ("child_kind", "expected_blocker"),
    [
        (
            "metric",
            "metric:symbol_recall_at_10:component_stage_parent_mismatch",
        ),
        (
            "guardrail",
            "guardrail:exact_identifier_non_regression:component_stage_parent_mismatch",
        ),
        (
            "artifact",
            "artifact:code_golden:component_stage_parent_mismatch",
        ),
        ("latency", "latency_summary:component_stage_parent_mismatch"),
        ("cost", "cost_summary:component_stage_parent_mismatch"),
        ("shadow", "shadow_evidence:component_stage_parent_mismatch"),
    ],
)
def test_cross_stage_child_copy_fails_even_when_top_envelope_is_resigned(
    child_kind: str,
    expected_blocker: str,
) -> None:
    offline = _snapshot(ReleaseStage.OFFLINE)
    shadow = _snapshot(ReleaseStage.SHADOW_INTERNAL_100, previous=offline)
    update: dict[str, Any]
    if child_kind == "metric":
        update = {"metrics": (offline.metrics[0], *shadow.metrics[1:])}
    elif child_kind == "guardrail":
        update = {"guardrails": (offline.guardrails[0], *shadow.guardrails[1:])}
    elif child_kind == "artifact":
        update = {"artifacts": (offline.artifacts[0], *shadow.artifacts[1:])}
    elif child_kind == "latency":
        update = {"latency": offline.latency}
    elif child_kind == "cost":
        update = {"cost": offline.cost}
    else:
        update = {"shadow": offline.shadow}
    copied = _sign(shadow.model_copy(update=update))

    decision = _decision(copied, prior=(offline,))

    assert decision.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert expected_blocker in decision.blockers


def test_no_stage_skip_and_stale_profile_or_artifact_version() -> None:
    offline = _snapshot(ReleaseStage.OFFLINE)
    skipped = _snapshot(ReleaseStage.CANARY_5, previous=offline)

    skip_decision = _decision(skipped, prior=(offline,))

    assert skip_decision.status is ReleaseDecisionStatus.ROLLBACK_REQUIRED
    assert "previous_stage:chain_missing_or_skipped" in skip_decision.blockers

    stale_versions = VERSIONS.model_copy(update={"model_version": "stale-model-v0"})
    stale = _sign(offline.model_copy(update={"versions": stale_versions}))
    stale_decision = _decision(stale)
    assert "release_versions:stale_or_mismatched" in stale_decision.blockers

    artifact = offline.artifacts[0]
    assert artifact.source is not None
    old_source = artifact.source.model_copy(update={"artifact_version": "code-golden-old-v0"})
    old_artifact = _sign(artifact.model_copy(update={"source": old_source}))
    old_artifacts = (old_artifact, *offline.artifacts[1:])
    old_evidence = _sign(offline.model_copy(update={"artifacts": old_artifacts}))
    old_decision = _decision(old_evidence)
    assert "artifact:code_golden:artifact_version_mismatch" in old_decision.blockers


def test_missing_unknown_or_failed_guardrail_blocks_promotion() -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)
    missing = _sign(evidence.model_copy(update={"guardrails": evidence.guardrails[:-1]}))
    missing_decision = _decision(missing)
    assert "guardrail:acl_security_suite:missing" in missing_decision.blockers

    original = next(
        item
        for item in evidence.guardrails
        if item.name is GuardrailName.MODEL_UNAVAILABLE_FALLBACK
    )
    failed = _sign(
        original.model_copy(
            update={
                "status": GuardrailStatus.FAIL,
                "numerator": original.numerator - 1,
            }
        )
    )
    guardrails = tuple(failed if item.name is failed.name else item for item in evidence.guardrails)
    failed_evidence = _sign(evidence.model_copy(update={"guardrails": guardrails}))
    failed_decision = _decision(failed_evidence)
    assert "guardrail:model_unavailable_fallback:status_fail" in (failed_decision.blockers)

    unknown = _sign(
        original.model_copy(
            update={
                "status": GuardrailStatus.UNKNOWN,
                "numerator": 0,
                "denominator": 0,
            }
        )
    )
    unknown_guardrails = tuple(
        unknown if item.name is unknown.name else item for item in evidence.guardrails
    )
    unknown_evidence = _sign(evidence.model_copy(update={"guardrails": unknown_guardrails}))
    unknown_decision = _decision(unknown_evidence)
    assert "guardrail:model_unavailable_fallback:status_unknown" in (unknown_decision.blockers)


def test_security_leakage_timeout_and_canary_failure_require_fixed_rollback() -> None:
    offline = _snapshot(ReleaseStage.OFFLINE)
    shadow = _snapshot(ReleaseStage.SHADOW_INTERNAL_100, previous=offline)
    canary = _snapshot(ReleaseStage.CANARY_5, previous=shadow)
    metric = next(
        item for item in canary.metrics if item.name is ReleaseMetricName.UNAUTHORIZED_LEAKAGE_RATE
    )
    leakage = _sign(metric.model_copy(update={"numerator": 1.0, "value": 1 / 224}))
    leaking_canary = _replace_metric(canary, leakage)

    decision = _decision(leaking_canary, prior=(offline, shadow))

    assert decision.status is ReleaseDecisionStatus.ROLLBACK_REQUIRED
    assert "metric:unauthorized_leakage_rate:threshold_failed" in decision.blockers
    assert decision.rollback_plan.actions == ROLLBACK_ACTIONS
    assert decision.rollback_plan.actions == (
        RollbackAction.SET_ENGINE_V1,
        RollbackAction.DISABLE_RERANKER,
        RollbackAction.DISABLE_GRAPH,
        RollbackAction.SWITCH_EMBEDDING_PROFILE,
        RollbackAction.SWITCH_V2_PUBLICATION_POINTER,
        RollbackAction.PRESERVE_EVIDENCE,
        RollbackAction.PRESERVE_STABLE_ENTITY_GENERATION,
    )

    timeout = _decision(
        canary,
        prior=(offline, shadow),
        verification_timed_out=True,
    )
    assert timeout.status is ReleaseDecisionStatus.ROLLBACK_REQUIRED
    assert "release_verification:timeout" in timeout.blockers


def test_attestation_verifier_timeout_is_a_fail_closed_hold() -> None:
    class TimeoutVerifier:
        verifier_id = VERIFIER.verifier_id
        verifier_version = VERIFIER.verifier_version
        test_only = True

        def verify(self, _attestation) -> bool:
            raise TimeoutError

    evidence = _snapshot(ReleaseStage.OFFLINE)

    decision = evaluate_release(
        evidence,
        deployed_stage=evidence.stage,
        policy=POLICY,
        verifiers=(TimeoutVerifier(),),
        evaluated_at=NOW,
        allow_test_evidence=True,
    )

    assert decision.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert "release_snapshot:verifier_timeout" in decision.blockers


def test_shadow_aggregation_is_bounded_deterministic_and_denominator_honest() -> None:
    empty = aggregate_shadow_evidence(
        (),
        release_stage=ReleaseStage.OFFLINE,
        parent_evidence_digest=None,
    )
    assert empty.total_samples == 0
    assert empty.latency_status is EvidenceStatus.UNAVAILABLE
    assert empty.error_rate.status is EvidenceStatus.UNAVAILABLE
    assert empty.error_rate.denominator == 0

    complete = SanitizedShadowCounters(
        sample_count=2,
        latency_ms=(20.0, 10.0),
        error_count=0,
        exception_count=0,
        fallback_count=1,
        compatibility_failure_count=0,
        security_violation_count=0,
        unauthorized_leakage_count=0,
        secret_leakage_count=0,
        model_unavailable_count=1,
        semantic_fallback_count=1,
    )
    partial = SanitizedShadowCounters(
        sample_count=3,
        latency_ms=(30.0,),
        error_count=None,
        exception_count=None,
        fallback_count=0,
        compatibility_failure_count=None,
        security_violation_count=None,
        unauthorized_leakage_count=None,
        secret_leakage_count=None,
        model_unavailable_count=0,
        semantic_fallback_count=0,
    )
    source = _source(
        "shadow-partial",
        POLICY.shadow_artifact_version,
        stage=ReleaseStage.OFFLINE,
        parent_digest=None,
    )
    first = aggregate_shadow_evidence(
        (complete, partial),
        release_stage=ReleaseStage.OFFLINE,
        parent_evidence_digest=None,
        source=source,
    )
    second = aggregate_shadow_evidence(
        (partial, complete),
        release_stage=ReleaseStage.OFFLINE,
        parent_evidence_digest=None,
        source=source,
    )

    assert first == second
    assert first.canonical_sha256() == second.canonical_sha256()
    assert first.total_samples == 5
    assert first.latency_samples == 3
    assert first.latency_status is EvidenceStatus.PROVISIONAL
    assert first.error_rate.status is EvidenceStatus.PROVISIONAL
    assert first.error_rate.denominator == 2
    assert first.fallback_rate.status is EvidenceStatus.AVAILABLE
    assert first.fallback_rate.denominator == 5
    assert first.fallback_rate.numerator == 1
    assert first.model_unavailable_rate.numerator == 1
    assert first.semantic_fallback_rate.numerator == 1

    with pytest.raises(ValidationError, match="query"):
        SanitizedShadowCounters.model_validate({"sample_count": 1, "query": "do not retain me"})
    with pytest.raises(ValueError, match="sample bound"):
        aggregate_shadow_evidence(
            (SanitizedShadowCounters(sample_count=10_001),),
            release_stage=ReleaseStage.OFFLINE,
            parent_evidence_digest=None,
        )


def test_contracts_are_frozen_portable_secret_safe_and_canonical() -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)
    first = evidence.canonical_sha256()
    second = ReleaseEvidence.model_validate(
        evidence.model_dump(mode="python", round_trip=True)
    ).canonical_sha256()
    assert first == second
    assert first.startswith("sha256:")
    assert b"test_fixture_only" in evidence.canonical_json_bytes()
    assert b"/Users/" not in evidence.canonical_json_bytes()
    assert b"acl_ref" not in evidence.canonical_json_bytes()

    with pytest.raises(ValidationError, match="frozen"):
        evidence.sample_count = 999  # type: ignore[misc]
    with pytest.raises(ValidationError, match="absolute paths"):
        _source(
            "/Users/example/private.json",
            "artifact-v1",
            stage=ReleaseStage.OFFLINE,
            parent_digest=None,
        )
    with pytest.raises(ValidationError, match="secret material"):
        ReleaseArtifact(
            release_stage=ReleaseStage.OFFLINE,
            parent_evidence_digest=None,
            kind=ReleaseArtifactKind.SECURITY_REPORT,
            artifact_id="test://security/report",
            evidence_status=EvidenceStatus.UNAVAILABLE,
            disposition=ArtifactDisposition.UNAVAILABLE,
            reason="api_key=supersecretcredential",
        )
    with pytest.raises(ValidationError, match="personal data"):
        ReleaseArtifact(
            release_stage=ReleaseStage.OFFLINE,
            parent_evidence_digest=None,
            kind=ReleaseArtifactKind.SECURITY_REPORT,
            artifact_id="test://security/report",
            evidence_status=EvidenceStatus.UNAVAILABLE,
            disposition=ArtifactDisposition.UNAVAILABLE,
            reason="owner=person@example.invalid",
        )


def test_rollback_plan_rejects_reordering_and_evaluator_never_executes_changes() -> None:
    evidence = _snapshot(ReleaseStage.OFFLINE)
    payload = evidence.model_dump(mode="python")
    payload["rollback_plan"]["actions"] = list(reversed(ROLLBACK_ACTIONS))

    decision = evaluate_release(
        payload,
        deployed_stage=ReleaseStage.OFFLINE,
        policy=POLICY,
    )

    assert decision.status is ReleaseDecisionStatus.HOLD_DEFAULT_V1
    assert decision.runtime_engine == "v1"
    assert decision.configuration_changed is False
    assert decision.blockers == ("release_contract:invalid_or_tampered",)
