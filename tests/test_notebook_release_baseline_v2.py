from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from evidence_rag.rag.sources.notebook import (
    NOTEBOOK_BASELINE_ARTIFACT_FILES,
    NOTEBOOK_GOLDEN_PACKAGE_SHA256,
    NOTEBOOK_RELEASE_THRESHOLDS,
    NotebookEvidenceStatus,
    NotebookReleaseDecisionType,
    NotebookReleaseGuardrail,
    NotebookReleaseMetric,
    NotebookReleaseStage,
    NotebookRetrieverV2,
    build_notebook_release_evidence_v2,
    evaluate_notebook_release_v2,
    run_notebook_baseline_v1,
    verify_notebook_baseline_artifact_v1,
)
from evidence_rag.rag.sources.notebook.context_builder import (
    NOTEBOOK_CONTEXT_BUILDER_VERSION,
)
from evidence_rag.rag.sources.notebook.reranker import NOTEBOOK_RERANKER_VERSION
from evidence_rag.rag.sources.notebook.retriever import NOTEBOOK_RETRIEVER_VERSION


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _resign(directory: Path) -> None:
    targets = NOTEBOOK_BASELINE_ARTIFACT_FILES[:-1]
    checksums = {filename: _sha256((directory / filename).read_bytes()) for filename in targets}
    payload = {
        "artifact_version": "notebook-portable-artifact-v1",
        "checksums": checksums,
        "artifact_set_sha256": "sha256:"
        + hashlib.sha256(
            json.dumps(
                checksums,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest(),
    }
    (directory / "checksums.json").write_bytes(_canonical(payload))


def _release_metrics(value: float) -> tuple[NotebookReleaseMetric, ...]:
    return tuple(
        NotebookReleaseMetric(
            name=name,
            status=NotebookEvidenceStatus.AVAILABLE,
            value=value if direction == "min" else 100.0,
            numerator=1,
            denominator=1,
        )
        for name, (direction, _) in NOTEBOOK_RELEASE_THRESHOLDS.items()
    )


def _guardrails() -> tuple[NotebookReleaseGuardrail, ...]:
    return tuple(
        NotebookReleaseGuardrail(
            name=name,
            status=NotebookEvidenceStatus.AVAILABLE,
            violations=0,
        )
        for name in (
            "secret_leakage",
            "unauthorized_leakage",
            "reasoning_leakage",
        )
    )


def test_notebook_release_evaluator_holds_and_rolls_back_without_side_effects() -> None:
    evidence = build_notebook_release_evidence_v2(
        observed_stage=NotebookReleaseStage.OFFLINE,
        proposed_stage=NotebookReleaseStage.ISOLATED_BASELINE,
        golden_package_sha256=NOTEBOOK_GOLDEN_PACKAGE_SHA256,
        artifact_set_sha256="sha256:" + ("1" * 64),
        retriever_version=NOTEBOOK_RETRIEVER_VERSION,
        reranker_version=NOTEBOOK_RERANKER_VERSION,
        context_builder_version=NOTEBOOK_CONTEXT_BUILDER_VERSION,
        metrics=_release_metrics(0.0),
        guardrails=_guardrails(),
    )
    decision = evaluate_notebook_release_v2(evidence)
    assert decision.decision == NotebookReleaseDecisionType.HOLD_DEFAULT_V1
    assert decision.default_engine == "v1"
    assert decision.side_effect_applied is False
    assert decision.failed_metrics

    deployed = build_notebook_release_evidence_v2(
        observed_stage=NotebookReleaseStage.CANARY_5,
        proposed_stage=NotebookReleaseStage.CANARY_25,
        golden_package_sha256=NOTEBOOK_GOLDEN_PACKAGE_SHA256,
        artifact_set_sha256="sha256:" + ("2" * 64),
        retriever_version=NOTEBOOK_RETRIEVER_VERSION,
        reranker_version=NOTEBOOK_RERANKER_VERSION,
        context_builder_version=NOTEBOOK_CONTEXT_BUILDER_VERSION,
        metrics=_release_metrics(0.0),
        guardrails=_guardrails(),
        production_observation=True,
    )
    assert evaluate_notebook_release_v2(deployed).decision == NotebookReleaseDecisionType.ROLLBACK

    skipped = build_notebook_release_evidence_v2(
        observed_stage=NotebookReleaseStage.OFFLINE,
        proposed_stage=NotebookReleaseStage.CANARY_5,
        golden_package_sha256=NOTEBOOK_GOLDEN_PACKAGE_SHA256,
        artifact_set_sha256="sha256:" + ("3" * 64),
        retriever_version=NOTEBOOK_RETRIEVER_VERSION,
        reranker_version=NOTEBOOK_RERANKER_VERSION,
        context_builder_version=NOTEBOOK_CONTEXT_BUILDER_VERSION,
        metrics=_release_metrics(1.0),
        guardrails=_guardrails(),
    )
    assert "release-stage-transition" in evaluate_notebook_release_v2(skipped).failed_guardrails


def test_notebook_baseline_is_portable_recomputed_and_non_qualified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    output = artifact_root / ("1" * 32)
    verification = run_notebook_baseline_v1(
        output_dir=output,
        artifact_root=artifact_root,
        isolated_work_root=tmp_path / "work",
    )
    assert verification.status == "VERIFIED_NON_QUALIFIED"
    assert verification.case_count == 40
    assert verification.baseline_qualified is False
    assert verification.release_decision == "HOLD_DEFAULT_V1"
    assert sorted(path.name for path in output.iterdir()) == sorted(
        NOTEBOOK_BASELINE_ARTIFACT_FILES
    )
    assert not any(
        path.name.endswith((".sqlite3", "-wal", "-shm", ".pyc")) for path in output.iterdir()
    )

    copied = tmp_path / "portable-copy"
    shutil.copytree(output, copied)
    monkeypatch.setattr(
        NotebookRetrieverV2,
        "search",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("verify-only attempted retrieval")
        ),
    )
    portable = verify_notebook_baseline_artifact_v1(copied)
    assert portable.portable is True
    assert portable.retrieval_executed is False
    metrics = json.loads((copied / "metrics.json").read_text())
    metric_map = {item["metric"]: item for item in metrics["metrics"]}
    assert metric_map["cell_recall_at_10"]["value"] >= 0.90
    assert metric_map["producer_output_path_recall"]["value"] < 0.95
    release = json.loads((copied / "release.json").read_text())
    assert release["decision"]["decision"] == "HOLD_DEFAULT_V1"

    tampered = tmp_path / "tampered"
    shutil.copytree(output, tampered)
    tampered_metrics = json.loads((tampered / "metrics.json").read_text())
    tampered_metrics["metrics"][0]["numerator"] = 999
    (tampered / "metrics.json").write_bytes(_canonical(tampered_metrics))
    _resign(tampered)
    with pytest.raises(ValueError, match="metrics"):
        verify_notebook_baseline_artifact_v1(tampered)

    extra = tmp_path / "extra"
    shutil.copytree(output, extra)
    (extra / "rogue.json").write_text("{}")
    with pytest.raises(ValueError, match="exactly ten"):
        verify_notebook_baseline_artifact_v1(extra)
