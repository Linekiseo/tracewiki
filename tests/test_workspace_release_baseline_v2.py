from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from evidence_rag.rag.sources.workspace.baseline_v1 import (
    WORKSPACE_BASELINE_ARTIFACT_FILES,
    run_workspace_baseline_v1,
    verify_workspace_baseline_artifact_v1,
)
from evidence_rag.rag.sources.workspace.contracts import (
    canonical_json_bytes,
    canonical_sha256,
)
from evidence_rag.rag.sources.workspace.release_v2 import (
    WORKSPACE_RELEASE_THRESHOLDS,
    WORKSPACE_REQUIRED_GUARDRAILS,
    WorkspaceEvidenceStatus,
    WorkspaceReleaseDecisionType,
    WorkspaceReleaseGuardrail,
    WorkspaceReleaseMetric,
    WorkspaceReleaseStage,
    build_workspace_release_evidence_v2,
    evaluate_workspace_release_v2,
)


def _run(tmp_path: Path, run_id: str = "5" * 32) -> Path:
    root = tmp_path.resolve()
    artifacts = root / "artifacts"
    work = root / "work"
    artifacts.mkdir(parents=True)
    work.mkdir(parents=True)
    output = artifacts / run_id
    result = run_workspace_baseline_v1(
        output_dir=output,
        artifact_root=artifacts,
        isolated_work_root=work,
    )
    assert result.status == "VERIFIED_NON_QUALIFIED"
    assert result.baseline_qualified is False
    assert result.release_decision == "HOLD_DEFAULT_V1"
    assert result.retrieval_executed is False
    assert tuple(sorted(item.name for item in output.iterdir())) == tuple(
        sorted(WORKSPACE_BASELINE_ARTIFACT_FILES)
    )
    assert not tuple(work.iterdir())
    return output


def test_workspace_baseline_is_portable_verify_only_and_truthful(
    tmp_path: Path,
) -> None:
    output = _run(tmp_path)
    original = verify_workspace_baseline_artifact_v1(output, portable=False)
    copied = tmp_path / "portable" / output.name
    copied.parent.mkdir()
    shutil.copytree(output, copied)
    portable = verify_workspace_baseline_artifact_v1(copied)
    assert original.artifact_set_sha256 == portable.artifact_set_sha256
    assert original.portable is False
    assert portable.portable is True
    assert portable.retrieval_executed is False
    metrics = json.loads((output / "metrics.json").read_text())
    metric_map = {item["metric"]: item for item in metrics["metrics"]}
    assert metric_map["scope_resolution_accuracy"]["numerator"] == 40
    assert metric_map["hard_negative_avoidance"]["numerator"] == 38
    assert metric_map["unsafe_mutation_rate"]["numerator"] == 0
    assert metric_map["acl_leakage_rate"]["numerator"] == 0
    release = json.loads((output / "release.json").read_text())
    assert release["decision"]["decision"] == "HOLD_DEFAULT_V1"
    assert release["decision"]["default_engine"] == "v1"
    assert release["evidence"]["production_observation"] is False


def test_workspace_verifier_rejects_tamper_resign_extra_and_noncanonical(
    tmp_path: Path,
) -> None:
    output = _run(tmp_path)
    metrics_path = output / "metrics.json"
    metrics = json.loads(metrics_path.read_text())
    metrics["metrics"][0]["numerator"] = 39
    metrics_path.write_bytes(canonical_json_bytes(metrics) + b"\n")
    checksums_path = output / "checksums.json"
    checksums = json.loads(checksums_path.read_text())
    checksums["checksums"]["metrics.json"] = (
        "sha256:" + hashlib.sha256(metrics_path.read_bytes()).hexdigest()
    )
    checksums["artifact_set_sha256"] = canonical_sha256(checksums["checksums"])
    checksums_path.write_bytes(canonical_json_bytes(checksums) + b"\n")
    with pytest.raises(ValueError, match="offline recomputation"):
        verify_workspace_baseline_artifact_v1(output)

    extra = _run(tmp_path / "extra", run_id="6" * 32)
    (extra / "extra.json").write_text("{}\n")
    with pytest.raises(ValueError, match="exactly ten"):
        verify_workspace_baseline_artifact_v1(extra)

    noncanonical = _run(tmp_path / "noncanonical", run_id="7" * 32)
    run_path = noncanonical / "run.json"
    run_path.write_text(json.dumps(json.loads(run_path.read_text()), indent=2))
    with pytest.raises(ValueError, match="canonical"):
        verify_workspace_baseline_artifact_v1(noncanonical)


def _release(
    *,
    observed: WorkspaceReleaseStage,
    proposed: WorkspaceReleaseStage,
    production: bool,
    failed: bool = False,
):
    metrics = []
    for name, (direction, threshold) in WORKSPACE_RELEASE_THRESHOLDS.items():
        value = (
            threshold - 0.1
            if failed and direction == "min"
            else threshold + 1
            if failed
            else threshold
        )
        metrics.append(
            WorkspaceReleaseMetric(
                name=name,
                status=WorkspaceEvidenceStatus.AVAILABLE,
                value=value,
                numerator=max(0.0, value),
                denominator=1,
            )
        )
    guardrails = tuple(
        WorkspaceReleaseGuardrail(
            name=name,
            status=WorkspaceEvidenceStatus.AVAILABLE,
            violations=0,
        )
        for name in WORKSPACE_REQUIRED_GUARDRAILS
    )
    return build_workspace_release_evidence_v2(
        observed_stage=observed,
        proposed_stage=proposed,
        golden_package_sha256="sha256:" + ("1" * 64),
        artifact_set_sha256="sha256:" + ("2" * 64),
        retriever_version="workspace-retriever-v2",
        reranker_version="workspace-reranker-v2",
        context_builder_version="workspace-context-builder-v2",
        metrics=tuple(metrics),
        guardrails=guardrails,
        production_observation=production,
    )


def test_workspace_release_is_no_skip_hold_and_rollback_without_side_effect() -> None:
    no_skip = evaluate_workspace_release_v2(
        _release(
            observed=WorkspaceReleaseStage.OFFLINE,
            proposed=WorkspaceReleaseStage.CANARY_5,
            production=True,
        )
    )
    assert no_skip.decision == WorkspaceReleaseDecisionType.HOLD_DEFAULT_V1
    assert "release-stage-transition" in no_skip.failed_guardrails
    no_observation = evaluate_workspace_release_v2(
        _release(
            observed=WorkspaceReleaseStage.OFFLINE,
            proposed=WorkspaceReleaseStage.ISOLATED_BASELINE,
            production=False,
        )
    )
    assert no_observation.decision == WorkspaceReleaseDecisionType.HOLD_DEFAULT_V1
    assert no_observation.default_engine == "v1"
    assert no_observation.side_effect_applied is False
    deployed_failure = evaluate_workspace_release_v2(
        _release(
            observed=WorkspaceReleaseStage.CANARY_5,
            proposed=WorkspaceReleaseStage.CANARY_25,
            production=True,
            failed=True,
        )
    )
    assert deployed_failure.decision == WorkspaceReleaseDecisionType.ROLLBACK
    assert deployed_failure.rollback_sequence == (
        "disable-workspace-v2-selection",
        "restore-last-known-good-workspace-generation",
        "invalidate-workspace-context-and-intelligence-cache",
        "verify-v1-control-plane-health",
    )
