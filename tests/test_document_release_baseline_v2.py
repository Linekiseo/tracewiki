from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from evidence_rag.rag.sources.document.baseline_v1 import (
    DOCUMENT_BASELINE_ARTIFACT_FILES,
    run_document_baseline_v1,
    verify_document_baseline_artifact_v1,
)
from evidence_rag.rag.sources.document.contracts import canonical_json_bytes
from evidence_rag.rag.sources.document.release_v2 import (
    DocumentEvidenceStatus,
    DocumentReleaseAction,
    DocumentReleaseEvidence,
    DocumentReleaseGuardrail,
    DocumentReleaseMetric,
    DocumentReleaseStage,
    evaluate_document_release_v2,
)


def _run(tmp_path: Path, run_id: str = "2" * 32) -> Path:
    root = tmp_path.resolve()
    artifacts = root / "artifacts"
    work = root / "work"
    artifacts.mkdir(parents=True)
    work.mkdir(parents=True)
    output = artifacts / run_id
    result = run_document_baseline_v1(
        output_dir=output,
        artifact_root=artifacts,
        isolated_work_root=work,
    )
    assert result.status == "VERIFIED_NON_QUALIFIED"
    assert result.baseline_qualified is False
    assert result.release_action == "HOLD_DEFAULT_V1"
    assert result.retrieval_executed is False
    assert tuple(sorted(item.name for item in output.iterdir())) == tuple(
        sorted(DOCUMENT_BASELINE_ARTIFACT_FILES)
    )
    assert not tuple(work.iterdir())
    return output


def test_document_baseline_is_portable_verify_only_and_truthful(tmp_path: Path) -> None:
    output = _run(tmp_path)
    verification = verify_document_baseline_artifact_v1(output, portable=False)
    assert verification.portable is False
    copied = tmp_path / "portable" / output.name
    copied.parent.mkdir()
    shutil.copytree(output, copied)
    portable = verify_document_baseline_artifact_v1(copied)
    assert portable.portable is True
    assert portable.artifact_set_sha256 == verification.artifact_set_sha256
    metrics = json.loads((output / "metrics.json").read_text())
    metric_map = {item["metric"]: item for item in metrics["metrics"]}
    assert metric_map["retrieval_recall_at_10"]["numerator"] == 49
    assert metric_map["claim_validation_precision"]["numerator"] == 9
    release = json.loads((output / "release.json").read_text())
    assert release["decision"]["action"] == "HOLD_DEFAULT_V1"
    assert release["decision"]["default_engine"] == "v1"
    assert "claim_validation_precision" in release["decision"]["failed_metrics"]


def test_document_verifier_rejects_tamper_resign_extra_and_noncanonical(
    tmp_path: Path,
) -> None:
    output = _run(tmp_path)
    metrics_path = output / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    metrics["metrics"][0]["numerator"] = 50
    metrics_path.write_bytes(canonical_json_bytes(metrics) + b"\n")
    checksums_path = output / "checksums.json"
    checksums = json.loads(checksums_path.read_text())
    import hashlib

    checksums["checksums"]["metrics.json"] = (
        "sha256:" + hashlib.sha256(metrics_path.read_bytes()).hexdigest()
    )
    from evidence_rag.rag.sources.document.contracts import canonical_sha256

    checksums["artifact_set_sha256"] = canonical_sha256(checksums["checksums"])
    checksums_path.write_bytes(canonical_json_bytes(checksums) + b"\n")
    with pytest.raises(ValueError, match="offline recomputation"):
        verify_document_baseline_artifact_v1(output)

    output = _run(tmp_path / "extra", run_id="3" * 32)
    (output / "extra.json").write_text("{}\n")
    with pytest.raises(ValueError, match="exactly ten"):
        verify_document_baseline_artifact_v1(output)

    output = _run(tmp_path / "canonical", run_id="4" * 32)
    run_path = output / "run.json"
    run_path.write_text(json.dumps(json.loads(run_path.read_text()), indent=2))
    with pytest.raises(ValueError, match="canonical"):
        verify_document_baseline_artifact_v1(output)


def _release_evidence(
    *,
    observed: DocumentReleaseStage,
    proposed: DocumentReleaseStage,
    metric_value: float = 1.0,
    production: bool = False,
) -> DocumentReleaseEvidence:
    from evidence_rag.rag.sources.document.contracts import canonical_sha256

    metrics = (
        DocumentReleaseMetric(
            name="quality",
            status=DocumentEvidenceStatus.AVAILABLE,
            numerator=metric_value,
            denominator=1,
            value=metric_value,
            threshold=0.95,
            direction="min",
        ),
    )
    guardrails = (
        DocumentReleaseGuardrail(
            name="secret_leakage",
            status=DocumentEvidenceStatus.AVAILABLE,
            violations=0,
        ),
    )
    payload = {
        "evaluator_version": "document-release-evaluator-v2",
        "observed_stage": observed,
        "proposed_stage": proposed,
        "artifact_set_sha256": "sha256:" + "1" * 64,
        "golden_package_sha256": "sha256:" + "2" * 64,
        "retriever_version": "retriever",
        "reranker_version": "reranker",
        "context_builder_version": "context",
        "production_observation": production,
        "metrics": metrics,
        "guardrails": guardrails,
    }
    identity_payload = {
        key: (
            value
            if isinstance(value, (str, bool))
            else [item.model_dump(mode="json") for item in value]
            if isinstance(value, tuple)
            else str(value)
        )
        for key, value in payload.items()
    }
    return DocumentReleaseEvidence(
        evidence_id=(
            "docrelease-" + canonical_sha256(identity_payload).removeprefix("sha256:")[:64]
        ),
        **payload,
    )


def test_document_release_is_no_skip_fail_closed_and_side_effect_free() -> None:
    no_skip = evaluate_document_release_v2(
        _release_evidence(
            observed=DocumentReleaseStage.OFFLINE,
            proposed=DocumentReleaseStage.CANARY_5,
        )
    )
    assert no_skip.action == DocumentReleaseAction.HOLD_DEFAULT_V1
    assert no_skip.side_effect_applied is False
    failed_canary = evaluate_document_release_v2(
        _release_evidence(
            observed=DocumentReleaseStage.CANARY_5,
            proposed=DocumentReleaseStage.CANARY_25,
            metric_value=0.5,
            production=True,
        )
    )
    assert failed_canary.action == DocumentReleaseAction.ROLLBACK
    assert failed_canary.rollback_sequence == (
        "disable-document-v2-selection",
        "restore-last-known-good-generation",
        "invalidate-derived-context-cache",
        "verify-v1-health",
    )
    external_default = evaluate_document_release_v2(
        _release_evidence(
            observed=DocumentReleaseStage.OPT_IN_100,
            proposed=DocumentReleaseStage.DEFAULT_V2,
            production=True,
        )
    )
    assert external_default.action == DocumentReleaseAction.HOLD_DEFAULT_V1
    assert external_default.default_engine == "v1"
