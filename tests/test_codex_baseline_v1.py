from __future__ import annotations

import builtins
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import evidence_rag.rag.sources.codex.baseline_v1 as baseline
from evidence_rag.rag.sources.codex.baseline_v1 import (
    CANONICAL_ARTIFACT_FILES,
    CODEX_BASELINE_SCHEMA_VERSION,
    CodexBaselineError,
    CodexBaselineRequest,
    CodexBaselineTrace,
    component_identities_v1,
    probe_codex_baseline_preparation_v1,
    run_codex_baseline_v1,
    verify_codex_baseline_artifact_v1,
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_bytes(b"".join(_canonical(row) + b"\n" for row in rows))


def _smoke_request(root: Path, *, seed: int = 41) -> CodexBaselineRequest:
    fixture = root / "fixture.jsonl"
    _write_jsonl(
        fixture,
        [
            {
                "type": "thread.started",
                "thread_id": "smoke-thread",
                "cwd": "${PROJECT_ROOT}",
            },
            {"type": "turn.started", "turn_id": "turn-one"},
            {
                "type": "item.completed",
                "item": {
                    "id": "goal-one",
                    "type": "user_message",
                    "message": (
                        "Implement alpha baseline retrieval without exposing "
                        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
                    ),
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "answer-one",
                    "type": "agent_message",
                    "text": "Alpha baseline retrieval is complete.",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "command-one",
                    "type": "command_execution",
                    "command": "pytest -q",
                    "aggregated_output": "2 passed",
                    "exit_code": 0,
                },
            },
            {"type": "turn.completed", "turn_id": "turn-one"},
        ],
    )
    golden = root / "smoke-cases.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "case_id": "smoke-001",
                "slice": "thread/goal location",
                "query": "alpha baseline retrieval",
                "eligible_metrics": [],
            },
            {
                "case_id": "smoke-002",
                "slice": "process trace",
                "query": "!!!",
                "eligible_metrics": [],
            },
        ],
    )
    return CodexBaselineRequest(
        execution_mode="smoke",
        authorization="SMOKE_ONLY",
        golden_path=golden,
        fixture_path=fixture,
        isolated_root=root,
        database_path=root / "database" / "baseline.sqlite3",
        raw_root=root / "materialized-raw",
        output_dir=root / "artifact",
        seed=seed,
    )


@pytest.fixture
def smoke_artifact(tmp_path: Path) -> tuple[CodexBaselineRequest, Path]:
    request = _smoke_request(tmp_path)
    result = run_codex_baseline_v1(request)
    return request, result.artifact_path


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().splitlines()]


def _released_verifier_fixture(root: Path) -> Path:
    """Build an ephemeral all-zero verifier fixture; this never runs retrieval."""

    dataset = baseline.load_codex_golden_v1()
    identities = component_identities_v1()
    runner_identity = baseline._runner_module_identity()
    cases = tuple(baseline._released_case(case) for case in dataset.cases)
    denominator_by_case = {
        case.case_id: baseline._case_denominator_contributions(case) for case in dataset.cases
    }
    component_hash = baseline._component_set_hash(identities)
    run_id = (
        "codex-xb0-"
        + baseline.hashlib.sha256(
            _canonical(
                {
                    "schema_version": baseline.CODEX_BASELINE_SCHEMA_VERSION,
                    "execution_mode": "released",
                    "dataset_id": dataset.dataset_id,
                    "dataset_version": dataset.dataset_version,
                    "package_hash": dataset.package_hash,
                    "seed": 701,
                    "case_membership": list(dataset.case_membership),
                    "component_set_hash": component_hash,
                    "production_authority_digest": (baseline.CODEX_PRODUCTION_AUTHORITY_DIGEST),
                    "runner_identity": runner_identity.model_dump(mode="json", exclude_none=True),
                    "golden_authority_digest": (baseline.CODEX_XB0_GOLDEN_AUTHORITY_DIGEST),
                    "config": baseline.CodexBaselineConfig().model_dump(mode="json"),
                }
            )
        ).hexdigest()[:32]
    )
    predictions = tuple(
        baseline.CodexPredictionRecord(
            run_id=run_id,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            package_hash=dataset.package_hash,
            case_id=case.case_id,
            query=case.query,
            eligible_metrics=case.eligible_metrics,
            denominator_contributions=denominator_by_case[case.case_id],
            results=(),
            trace=baseline.CodexBaselineTrace(
                case_id=case.case_id,
                outcome="zero_result",
                latency_ms=0.0,
                lexical_candidates=0,
                dense_candidates=0,
                dense_matches=0,
                result_count=0,
                reviewed_result_count=0,
                zero_result=True,
            ),
        )
        for case in cases
    )
    reviewed: tuple[baseline.ReviewedRetrievalRow, ...] = ()
    metrics = baseline.evaluate_reviewed_codex_retrieval(
        dataset,
        reviewed,
        case_membership=dataset.case_membership,
    )
    contributions = baseline._metric_case_contributions(dataset, reviewed)
    slices = baseline._slice_report(
        run_id=run_id,
        cases=cases,
        denominator_by_case=denominator_by_case,
        predictions=predictions,
        contributions=contributions,
    )
    output = root / "released-shaped-verifier-fixture"
    paths = baseline._ValidatedPaths(
        isolated_root=root,
        database_path=root / "unused.sqlite3",
        raw_root=root / "unused-raw",
        output_dir=output,
        derived_data=root / "unused-data",
        project_root=root / "unused-project",
        staging_dir=root / ".released-shaped-staging",
    )
    baseline._build_artifact(
        paths=paths,
        run_id=run_id,
        execution_mode="released",
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        package_hash=dataset.package_hash,
        seed=701,
        identities=identities,
        runner_identity=runner_identity,
        cases=cases,
        released_dataset=dataset,
        denominator_by_case=denominator_by_case,
        predictions=predictions,
        errors=(),
        metric_results=metrics,
        slice_report=slices,
    )
    return output


@pytest.fixture(scope="module")
def released_verifier_artifact(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    root = tmp_path_factory.mktemp("codex-xb0-released-shaped")
    artifact = _released_verifier_fixture(root)
    yield artifact
    shutil.rmtree(root)


def _readdress(artifact: Path) -> None:
    checksums = _read_json(artifact / "checksums.json")
    files: dict[str, dict[str, object]] = {}
    for name in CANONICAL_ARTIFACT_FILES[:-1]:
        raw = (artifact / name).read_bytes()
        files[name] = {"sha256": baseline._sha256(raw), "size": len(raw)}
    checksums["files"] = files
    checksums["artifact_set_hash"] = baseline._artifact_set_hash(files)
    (artifact / "checksums.json").write_bytes(_canonical(checksums) + b"\n")


def test_real_production_flat_chain_emits_ephemeral_nonqualified_artifact(
    smoke_artifact: tuple[CodexBaselineRequest, Path],
) -> None:
    request, artifact = smoke_artifact
    verification = verify_codex_baseline_artifact_v1(artifact)

    assert verification.status == "VERIFIED_SMOKE_NON_QUALIFIED"
    assert verification.retrieval_executed is False
    assert set(path.name for path in artifact.iterdir()) == set(CANONICAL_ARTIFACT_FILES)
    run = _read_json(artifact / "run.json")
    manifest = _read_json(artifact / "manifest.json")
    predictions = _read_jsonl(artifact / "predictions.jsonl")
    metrics = _read_json(artifact / "metrics.json")
    assert run["release_posture"] == "SMOKE_NON_QUALIFIED"
    assert run["baseline_qualified"] is False
    assert metrics["status"] == "SMOKE_UNAVAILABLE"
    assert metrics["metrics"] == []
    assert manifest["config"] == {
        "context_builder": "snippet-v1",
        "dense_channel": "dense-full-candidate",
        "embedding_dimensions": 384,
        "embedding_model": "local-hash-v2",
        "episode_retrieval": False,
        "event_graph": False,
        "fusion": "codex-weighted-hybrid-v2",
        "item_types": list(baseline._FIXED_ITEM_TYPES),
        "lexical_channel": "fts5",
        "ranked_candidate_multiplier": 8,
        "reranker": False,
        "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
        "thread_top_k": 5,
        "top_k": 10,
        "view_granularity": "item-per-view",
    }
    assert predictions[0]["results"]
    assert predictions[0]["trace"]["fusion"] == "codex-weighted-hybrid-v2"
    assert predictions[0]["trace"]["embedding_model"] == "local-hash-v2"
    assert all(
        item["item_type"] != "DevelopmentEpisode"
        for prediction in predictions
        for item in prediction["results"]
    )
    assert predictions[1]["trace"]["outcome"] == "zero_result"
    assert predictions[1]["results"] == []
    assert baseline._scan_artifact_files(artifact, CANONICAL_ARTIFACT_FILES[:-1]) == []
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in "\n".join(
        path.read_text(encoding="utf-8") for path in artifact.iterdir()
    )
    assert not request.database_path.exists()
    assert not request.raw_root.exists()
    assert not Path(f"{request.database_path}-wal").exists()
    assert not Path(f"{request.database_path}-shm").exists()


def test_artifact_is_portable_and_verify_only_never_calls_retrieval(
    smoke_artifact: tuple[CodexBaselineRequest, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, artifact = smoke_artifact
    copied = tmp_path / "portable-copy"
    shutil.copytree(artifact, copied)

    def forbidden_execution(*_: object, **__: object) -> object:
        raise AssertionError("verify-only executed retrieval")

    monkeypatch.setattr(baseline, "_execute_cases", forbidden_execution)
    verification = verify_codex_baseline_artifact_v1(copied)
    assert verification.portable is True
    assert verification.retrieval_executed is False


def test_released_shaped_verify_only_rebuilds_fixed_golden_and_x0_metrics(
    released_verifier_artifact: Path,
) -> None:
    verification = verify_codex_baseline_artifact_v1(released_verifier_artifact)
    assert verification.status == "VERIFIED_NON_QUALIFIED"
    assert verification.case_count == 45
    assert verification.retrieval_executed is False


@pytest.mark.parametrize("mutation", ["tamper", "delete", "extra", "symlink"])
def test_verify_rejects_tamper_delete_extra_and_symlink(
    smoke_artifact: tuple[CodexBaselineRequest, Path],
    tmp_path: Path,
    mutation: str,
) -> None:
    _, artifact = smoke_artifact
    target = tmp_path / mutation
    shutil.copytree(artifact, target)
    if mutation == "tamper":
        with (target / "predictions.jsonl").open("ab") as handle:
            handle.write(b" ")
    elif mutation == "delete":
        (target / "latency.json").unlink()
    elif mutation == "extra":
        (target / "extra.json").write_text("{}\n", encoding="utf-8")
    else:
        (target / "metrics.json").unlink()
        (target / "metrics.json").symlink_to(artifact / "metrics.json")
    with pytest.raises(CodexBaselineError):
        verify_codex_baseline_artifact_v1(target)


@pytest.mark.parametrize("field", ["metric_denominators", "component_set_hash"])
def test_verify_rejects_readdressed_cross_file_denominator_and_identity_mismatch(
    smoke_artifact: tuple[CodexBaselineRequest, Path],
    tmp_path: Path,
    field: str,
) -> None:
    _, artifact = smoke_artifact
    target = tmp_path / f"cross-file-{field}"
    shutil.copytree(artifact, target)
    manifest = _read_json(target / "manifest.json")
    if field == "metric_denominators":
        manifest[field]["mrr_at_10"] = 1
    else:
        manifest[field] = "sha256:" + ("0" * 64)
    (target / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
    _readdress(target)
    with pytest.raises(CodexBaselineError, match="denominator|component"):
        verify_codex_baseline_artifact_v1(target)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "/private/tmp/raw/evidence-rag.sqlite3",
        "/etc/passwd",
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "owner@example.com",
        "4111 1111 1111 1111",
        float("nan"),
    ],
)
def test_verify_rejects_readdressed_security_and_nonfinite_content(
    smoke_artifact: tuple[CodexBaselineRequest, Path],
    tmp_path: Path,
    unsafe_value: object,
) -> None:
    _, artifact = smoke_artifact
    target = tmp_path / f"unsafe-{len(list(tmp_path.iterdir()))}"
    shutil.copytree(artifact, target)
    run = _read_json(target / "run.json")
    run["unsafe"] = unsafe_value
    raw = json.dumps(
        run,
        allow_nan=True,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    (target / "run.json").write_bytes(raw + b"\n")
    _readdress(target)
    with pytest.raises(CodexBaselineError):
        verify_codex_baseline_artifact_v1(target)


def test_structured_security_scan_accepts_finite_long_decimal_numbers(
    tmp_path: Path,
) -> None:
    metrics = tmp_path / "metrics.json"
    slices = tmp_path / "slice_report.json"
    baseline._write_json(
        metrics,
        {
            "metrics": [
                0.058823529411764705,
                0.12345678901234567,
                1.2345678901234567e-05,
                1234567890123.5,
            ],
            "nested": {
                "available": True,
                "denominator": 17,
                "reason": None,
            },
        },
    )
    baseline._write_json(
        slices,
        {
            "slices": [
                {
                    "numerator": 1,
                    "denominator": 17,
                    "value": 1 / 17,
                },
                {
                    "numerator": 5,
                    "denominator": 17,
                    "value": 5 / 17,
                },
            ]
        },
    )

    assert baseline._PAYMENT_RE.search(metrics.read_text(encoding="utf-8"))
    assert baseline._PAYMENT_RE.search(slices.read_text(encoding="utf-8"))
    assert (
        baseline._scan_artifact_files(
            tmp_path,
            ("metrics.json", "slice_report.json"),
        )
        == []
    )


@pytest.mark.parametrize("field_name", ["query", "locator", "error"])
def test_structured_security_scan_rejects_payment_digits_in_string_fields(
    tmp_path: Path,
    field_name: str,
) -> None:
    artifact = tmp_path / f"{field_name}.json"
    baseline._write_json(
        artifact,
        {
            "nested": [
                {
                    field_name: "4111111111111111",
                    "numeric_control": 4111111111111111,
                }
            ]
        },
    )

    assert baseline._scan_artifact_files(tmp_path, (artifact.name,)) == [f"{artifact.name}:payment"]


def test_structured_security_scan_covers_jsonl_nested_values_lists_and_keys(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "nested.jsonl"
    _write_jsonl(
        artifact,
        [
            {
                "safe_numeric": [4111111111111111, 0.058823529411764705],
                "nested": [{"safe": "ordinary text"}],
            },
            {
                "nested": [
                    {
                        "4111111111111111": "unsafe payment-shaped dictionary key",
                        "contact": "owner@example.com",
                    }
                ]
            },
        ],
    )

    assert baseline._scan_artifact_files(tmp_path, (artifact.name,)) == [
        "nested.jsonl:email",
        "nested.jsonl:payment",
    ]


def test_structured_security_scan_only_exempts_declared_contract_identities(
    tmp_path: Path,
) -> None:
    payment_sha256 = "sha256:" + "a" + "4111111111111111" + ("a" * 47)
    payment_run_id = "codex-xb0-" + "a" + "4111111111111111" + ("a" * 15)
    identity = tmp_path / "identity.json"
    baseline._write_json(
        identity,
        {
            "sha256": payment_sha256,
            "run_id": payment_run_id,
            "case_id": "123e4567-e89b-12d3-a456-426614174000",
        },
    )
    assert baseline._scan_artifact_files(tmp_path, (identity.name,)) == []

    user_text = tmp_path / "user-text.json"
    baseline._write_json(
        user_text,
        {
            "query": payment_sha256,
            "locator": payment_run_id,
        },
    )
    assert baseline._scan_artifact_files(tmp_path, (user_text.name,)) == ["user-text.json:payment"]


@pytest.mark.parametrize(
    ("field_name", "unsafe_value", "finding"),
    [
        ("query", "sk-proj-abcdefghijklmnopqrstuvwxyz123456", "credential"),
        ("locator", "/private/tmp/raw/evidence-rag.sqlite3", "absolute_path"),
        ("error", "owner@example.com", "email"),
        ("query", "artifact/evaluation.sqlite3-wal", "temporary_or_generated"),
        ("locator", "cache/result.pyc", "temporary_or_generated"),
    ],
)
def test_structured_security_scan_preserves_string_security_rules(
    tmp_path: Path,
    field_name: str,
    unsafe_value: str,
    finding: str,
) -> None:
    artifact = tmp_path / f"{finding}.json"
    baseline._write_json(artifact, {"nested": [{field_name: unsafe_value}]})
    findings = baseline._scan_artifact_files(tmp_path, (artifact.name,))
    assert f"{artifact.name}:{finding}" in findings


@pytest.mark.parametrize("raw_number", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_structured_security_scan_rejects_nonfinite_json_numbers(
    tmp_path: Path,
    raw_number: str,
) -> None:
    artifact = tmp_path / "nonfinite.json"
    artifact.write_bytes(f'{{"value":{raw_number}}}\n'.encode())
    with pytest.raises(CodexBaselineError, match="invalid canonical JSON|non-finite"):
        baseline._scan_artifact_files(tmp_path, (artifact.name,))


def test_request_rejects_missing_defaults_and_released_run_without_authorization(
    tmp_path: Path,
) -> None:
    request = _smoke_request(tmp_path)
    with pytest.raises(ValidationError):
        CodexBaselineRequest(
            execution_mode="smoke",
            authorization="SMOKE_ONLY",
            golden_path=request.golden_path,
            fixture_path=request.fixture_path,
            isolated_root=request.isolated_root,
            database_path=request.database_path,
            raw_root=request.raw_root,
            output_dir=request.output_dir,
        )
    with pytest.raises(ValidationError, match="authorization"):
        request.model_copy(
            update={
                "execution_mode": "released",
                "authorization": "SMOKE_ONLY",
            }
        )


def test_preparation_probe_is_nonqualified_and_does_not_open_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_connect(*_: object, **__: object) -> object:
        raise AssertionError("preparation probe opened SQLite")

    monkeypatch.setattr("sqlite3.connect", forbidden_connect)
    prepared = probe_codex_baseline_preparation_v1()
    assert prepared.status == "PREPARED"
    assert prepared.release_posture == "NON_QUALIFIED"
    assert prepared.released_run_executed is False
    assert prepared.evaluation_run_created is False
    assert prepared.baseline_metrics_run is False
    assert prepared.baseline_qualified is False
    assert prepared.production_authority_version == baseline.CODEX_PRODUCTION_AUTHORITY_VERSION
    assert prepared.production_authority_digest == baseline.CODEX_PRODUCTION_AUTHORITY_DIGEST


@pytest.mark.parametrize("guard", ["outside", "database_reuse", "sidecar", "raw_reuse"])
def test_runner_rejects_nonisolated_or_reused_paths_before_opening_sqlite(
    tmp_path: Path,
    guard: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _smoke_request(tmp_path)
    if guard == "outside":
        request = request.model_copy(update={"database_path": tmp_path.parent / "outside.sqlite3"})
    elif guard == "database_reuse":
        request.database_path.parent.mkdir()
        request.database_path.touch()
    elif guard == "sidecar":
        request.database_path.parent.mkdir()
        Path(f"{request.database_path}-wal").touch()
    else:
        request.raw_root.mkdir()

    opened = False

    def forbidden_connect(*_: object, **__: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("path guard opened SQLite")

    monkeypatch.setattr("sqlite3.connect", forbidden_connect)
    with pytest.raises(CodexBaselineError):
        run_codex_baseline_v1(request)
    assert opened is False
    assert not request.output_dir.exists()


def test_runner_rejects_symlink_alias_and_formal_database_without_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "real-root"
    target.mkdir()
    alias = tmp_path / "alias-root"
    alias.symlink_to(target, target_is_directory=True)
    request = _smoke_request(target)
    alias_request = request.model_copy(
        update={
            "isolated_root": alias,
            "golden_path": alias / request.golden_path.name,
            "fixture_path": alias / request.fixture_path.name,
            "database_path": alias / "database" / "baseline.sqlite3",
            "raw_root": alias / "materialized-raw",
            "output_dir": alias / "artifact",
        }
    )
    with pytest.raises(CodexBaselineError, match="symlink"):
        run_codex_baseline_v1(alias_request)

    formal = Path(baseline.__file__).resolve().parents[5] / "var" / "evidence-rag.sqlite3"
    formal_request = request.model_copy(update={"database_path": formal})
    opened = False

    def forbidden_connect(*_: object, **__: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("formal SQLite was accessed")

    monkeypatch.setattr("sqlite3.connect", forbidden_connect)
    with pytest.raises(CodexBaselineError):
        run_codex_baseline_v1(formal_request)
    assert opened is False


def test_exact_component_identity_rejects_subclass_and_monkeypatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _smoke_request(tmp_path)

    class WrappedRetriever(baseline.CodexHybridRetriever):
        pass

    monkeypatch.setattr(baseline, "CodexHybridRetriever", WrappedRetriever)
    with pytest.raises(CodexBaselineError, match="wrapped|replaced|subclass"):
        run_codex_baseline_v1(request)
    assert not request.database_path.exists()
    assert not request.output_dir.exists()


def test_fixed_production_authority_rejects_preimport_retriever_injection(
    tmp_path: Path,
) -> None:
    script = """
import evidence_rag.codex_retrieval as production
Original = production.CodexHybridRetriever
class Injected(Original):
    def search(self, request):
        return super().search(request)
Injected.__module__ = Original.__module__
Injected.__qualname__ = Original.__qualname__
production.CodexHybridRetriever = Injected
import evidence_rag.rag.sources.codex.baseline_v1
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "CodexBaselineError" in result.stderr
    assert not list(tmp_path.iterdir())


def test_fixed_production_authority_is_versioned_and_not_runtime_learned() -> None:
    identities = component_identities_v1()
    assert baseline.CODEX_PRODUCTION_AUTHORITY_VERSION == ("codex-x-b0-production-authority-v5")
    assert baseline.CODEX_PRODUCTION_AUTHORITY_DIGEST == (
        "sha256:0b8a57c8668de7a575a7d09a84a0c376e6f01aa4749863db74508cd6aa440b41"
    )
    assert baseline._component_set_hash(identities) == (baseline.CODEX_PRODUCTION_AUTHORITY_DIGEST)
    assert {identity.role for identity in identities} >= {
        "adapter-class",
        "adapter-parse",
        "ingestion-class",
        "ingestion-run",
        "publication-method",
        "embedding-class",
        "embedding-embed",
        "retriever-class",
        "retriever-search",
        "store-class",
        "store-connect",
        "evaluator",
    }
    assert all(identity.source_sha256 != identity.code_sha256 for identity in identities)
    assert all(identity.globals_sha256 is not None for identity in identities)
    assert all(identity.builtins_sha256 is not None for identity in identities)
    assert all(identity.helpers_sha256 is not None for identity in identities)

    historical = baseline._frozen_historical_production_authority()
    assert baseline.CODEX_HISTORICAL_PRODUCTION_AUTHORITY_VERSION == (
        "codex-x-b0-production-authority-v1"
    )
    assert baseline.CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST == (
        "sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75"
    )
    assert baseline._component_set_hash(historical) == (
        baseline.CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST
    )
    assert all(identity.globals_sha256 is None for identity in historical)
    assert all(identity.builtins_sha256 is None for identity in historical)
    assert all(identity.helpers_sha256 is None for identity in historical)
    assert identities != historical


def test_historical_artifacts_use_frozen_authority_not_current_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _fixed_source_run_dir().resolve(strict=True)
    correction_root = run.parents[1] / "corrections"
    correction_dirs = tuple(path for path in correction_root.iterdir() if path.is_dir())
    assert len(correction_dirs) == 1
    correction = correction_dirs[0]
    snapshot = {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for directory in (run, correction)
        for path in directory.iterdir()
    }
    retrieval_module = baseline.importlib.import_module("evidence_rag.codex_retrieval")

    class InjectedRetriever(retrieval_module.CodexHybridRetriever):
        pass

    InjectedRetriever.__module__ = retrieval_module.CodexHybridRetriever.__module__
    InjectedRetriever.__qualname__ = retrieval_module.CodexHybridRetriever.__qualname__
    monkeypatch.setattr(retrieval_module, "CodexHybridRetriever", InjectedRetriever)
    with pytest.raises(CodexBaselineError):
        probe_codex_baseline_preparation_v1()

    run_verification = baseline.verify_codex_baseline_artifact_v1(run)
    correction_verification = baseline.verify_codex_baseline_correction_artifact_v1(correction)
    assert run_verification.artifact_set_hash == baseline.CODEX_XB0_SOURCE_ARTIFACT_SET_HASH
    assert correction_verification.artifact_set_hash == (
        "sha256:f5da3d0b0d9d29bed3daff0a230cd1aa131d19385c20e9784b9b7a44a089ca6d"
    )
    assert {
        str(path): (path.read_bytes(), path.stat().st_mtime_ns)
        for directory in (run, correction)
        for path in directory.iterdir()
    } == snapshot


def test_new_artifacts_bind_current_authority_v3(
    released_verifier_artifact: Path,
) -> None:
    run = _read_json(released_verifier_artifact / "run.json")
    manifest = _read_json(released_verifier_artifact / "manifest.json")
    assert run["production_authority_version"] == baseline.CODEX_PRODUCTION_AUTHORITY_VERSION
    assert run["production_authority_digest"] == baseline.CODEX_PRODUCTION_AUTHORITY_DIGEST
    assert manifest["production_authority_version"] == (baseline.CODEX_PRODUCTION_AUTHORITY_VERSION)
    assert manifest["production_authority_digest"] == (baseline.CODEX_PRODUCTION_AUTHORITY_DIGEST)
    components = manifest["components"]
    assert components == [
        identity.model_dump(mode="json", exclude_none=True)
        for identity in component_identities_v1()
    ]
    assert all(
        {"source_sha256", "code_sha256", "globals_sha256", "builtins_sha256", "helpers_sha256"}
        <= set(component)
        for component in components
    )


@pytest.mark.parametrize("copy_builtins", [False, True])
def test_current_authority_rejects_same_code_alternate_globals_and_builtins(
    monkeypatch: pytest.MonkeyPatch,
    copy_builtins: bool,
) -> None:
    retrieval_module = baseline.importlib.import_module("evidence_rag.codex_retrieval")
    original = retrieval_module.CodexHybridRetriever.search
    alternate_globals = dict(original.__globals__)
    if copy_builtins:
        alternate_globals["__builtins__"] = dict(vars(builtins))
    replacement = types.FunctionType(
        original.__code__,
        alternate_globals,
        name=original.__name__,
        argdefs=original.__defaults__,
        closure=original.__closure__,
    )
    replacement.__kwdefaults__ = original.__kwdefaults__
    replacement.__module__ = original.__module__
    replacement.__qualname__ = original.__qualname__
    monkeypatch.setattr(retrieval_module.CodexHybridRetriever, "search", replacement)
    with pytest.raises(CodexBaselineError, match="namespace|binding"):
        probe_codex_baseline_preparation_v1()


def test_current_authority_rejects_same_code_helper_with_alternate_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_module = baseline.importlib.import_module("evidence_rag.codex_adapter")
    original = adapter_module._safe_path
    replacement = types.FunctionType(
        original.__code__,
        dict(original.__globals__),
        name=original.__name__,
        argdefs=original.__defaults__,
        closure=original.__closure__,
    )
    replacement.__kwdefaults__ = original.__kwdefaults__
    replacement.__module__ = original.__module__
    replacement.__qualname__ = original.__qualname__
    monkeypatch.setattr(adapter_module, "_safe_path", replacement)
    with pytest.raises(CodexBaselineError, match="binding"):
        probe_codex_baseline_preparation_v1()


def test_current_authority_rejects_preimport_copied_builtins(
    tmp_path: Path,
) -> None:
    script = """
import builtins
import types
import evidence_rag.codex_retrieval as production
original = production.CodexHybridRetriever.search
alternate_globals = dict(original.__globals__)
alternate_globals["__builtins__"] = dict(vars(builtins))
replacement = types.FunctionType(
    original.__code__, alternate_globals, original.__name__,
    original.__defaults__, original.__closure__
)
replacement.__kwdefaults__ = original.__kwdefaults__
replacement.__module__ = original.__module__
replacement.__qualname__ = original.__qualname__
production.CodexHybridRetriever.search = replacement
import evidence_rag.rag.sources.codex.baseline_v1
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "CodexBaselineError" in result.stderr
    assert not list(tmp_path.iterdir())


def test_current_authority_allows_exact_api_cold_import_io_bombs(
    tmp_path: Path,
) -> None:
    isolated_data = tmp_path / "deferred-data"
    environment = {
        **os.environ,
        "RAG_DATA_DIR": str(isolated_data),
        "RAG_ALLOWED_LOCAL_ROOTS": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    script = (
        "import sqlite3; "
        "from pathlib import Path; "
        "sqlite3.connect = lambda *_args, **_kwargs: "
        "(_ for _ in ()).throw(AssertionError('sqlite opened during cold status')); "
        "Path.mkdir = lambda *_args, **_kwargs: "
        "(_ for _ in ()).throw(AssertionError('path created during cold status')); "
        "from fastapi.testclient import TestClient; "
        "from evidence_rag.api import app; "
        "assert app.state.runtime is None; "
        "client = TestClient(app); "
        "client.__enter__(); "
        "response = client.get('/v1/rag/status'); "
        "assert response.status_code == 200; "
        "assert app.state.runtime is None; "
        "client.__exit__(None, None, None)"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not isolated_data.exists()


def test_fixed_authority_rejects_public_export_wrapper_and_wrong_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_module = baseline.importlib.import_module("evidence_rag.codex_adapter")
    embedding_module = baseline.importlib.import_module("evidence_rag.embeddings")
    retrieval_module = baseline.importlib.import_module("evidence_rag.codex_retrieval")

    with monkeypatch.context() as context:

        class ReplacedAdapter(adapter_module.CodexSessionAdapter):
            pass

        context.setattr(adapter_module, "CodexSessionAdapter", ReplacedAdapter)
        with pytest.raises(CodexBaselineError):
            probe_codex_baseline_preparation_v1()

    with monkeypatch.context() as context:
        context.setattr(embedding_module.LocalHashEmbedding, "model_id", "wrong-version")
        with pytest.raises(CodexBaselineError):
            probe_codex_baseline_preparation_v1()

    with monkeypatch.context() as context:
        original = retrieval_module.CodexHybridRetriever.search

        def wrapped(*args: object, **kwargs: object) -> object:
            return original(*args, **kwargs)

        wrapped.__wrapped__ = original  # type: ignore[attr-defined]
        context.setattr(retrieval_module.CodexHybridRetriever, "search", wrapped)
        with pytest.raises(CodexBaselineError):
            probe_codex_baseline_preparation_v1()


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_package",
        "rogue_membership",
        "membership_order",
        "forged_317",
        "authority_query",
        "treatment_judgment",
        "denominator",
        "numerator",
        "status",
        "value",
        "harmful",
        "false_validation",
        "context_noise",
    ],
)
def test_released_verify_recomputes_fixed_authority_and_quality(
    released_verifier_artifact: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    target = tmp_path / mutation
    shutil.copytree(released_verifier_artifact, target)
    run = _read_json(target / "run.json")
    manifest = _read_json(target / "manifest.json")
    metrics = _read_json(target / "metrics.json")
    slices = _read_json(target / "slice_report.json")
    golden = _read_jsonl(target / "golden_cases.jsonl")

    def metric_row(name: str) -> dict[str, Any]:
        return next(item for item in metrics["metrics"] if item["name"] == name)

    if mutation == "unknown_package":
        manifest["dataset"]["package_hash"] = "sha256:" + ("b" * 64)
    elif mutation == "rogue_membership":
        manifest["dataset"]["case_membership"] = [
            f"rogue-{ordinal:03d}" for ordinal in range(1, 46)
        ]
    elif mutation == "membership_order":
        manifest["dataset"]["case_membership"][0:2] = reversed(
            manifest["dataset"]["case_membership"][0:2]
        )
        run["case_membership"][0:2] = reversed(run["case_membership"][0:2])
    elif mutation == "forged_317":
        target_metric = "item_recall_at_10"
        overall = metric_row(target_metric)
        overall["numerator"] = 317
        overall["value"] = 317 / overall["denominator"]
        remaining = 317
        for slice_row in slices["slices"]:
            item = next(row for row in slice_row["metrics"] if row["name"] == target_metric)
            if item["denominator"] and remaining:
                item["numerator"] = remaining
                item["value"] = remaining / item["denominator"]
                remaining = 0
            elif item["denominator"]:
                item["numerator"] = 0
                item["value"] = 0.0
    elif mutation in {"authority_query", "treatment_judgment"}:
        authority = golden[0]["authority"]
        if mutation == "authority_query":
            authority["query"] = authority["query"] + " tampered"
            golden[0]["query"] = authority["query"]
        else:
            judgment = authority["item_judgments"][0]
            judgment["context_noise"] = not judgment["context_noise"]
        golden[0]["authority_sha256"] = baseline._sha256(_canonical(authority))
    else:
        metric_name = {
            "harmful": "harmful_old_attempt_rate_at_10",
            "false_validation": "false_validated_rate_at_10",
            "context_noise": "context_noise_rate_at_10",
        }.get(mutation, "thread_recall_at_5")
        row = metric_row(metric_name)
        if mutation == "denominator":
            row["denominator"] += 1
            row["value"] = row["numerator"] / row["denominator"]
        elif mutation == "status":
            row["status"] = "UNAVAILABLE"
            row["numerator"] = None
            row["denominator"] = 0
            row["value"] = None
            row["reason"] = "tampered unavailable"
        elif mutation == "value":
            row["value"] = 0.5
        else:
            row["numerator"] += 1
            row["value"] = row["numerator"] / row["denominator"]

    (target / "run.json").write_bytes(_canonical(run) + b"\n")
    (target / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
    (target / "metrics.json").write_bytes(_canonical(metrics) + b"\n")
    (target / "slice_report.json").write_bytes(_canonical(slices) + b"\n")
    _write_jsonl(target / "golden_cases.jsonl", golden)
    _readdress(target)
    with pytest.raises(CodexBaselineError):
        verify_codex_baseline_artifact_v1(target)


def test_component_identities_and_fixed_seed_ranking_are_deterministic(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_request = _smoke_request(first_root, seed=991)
    second_request = _smoke_request(second_root, seed=991)
    first = run_codex_baseline_v1(first_request)
    second = run_codex_baseline_v1(second_request)

    assert first.run_id == second.run_id
    assert component_identities_v1() == component_identities_v1()
    first_predictions = _read_jsonl(first.artifact_path / "predictions.jsonl")
    second_predictions = _read_jsonl(second.artifact_path / "predictions.jsonl")
    for left, right in zip(first_predictions, second_predictions, strict=True):
        assert left["case_id"] == right["case_id"]
        assert left["results"] == right["results"]
        for field in (
            "outcome",
            "lexical_candidates",
            "dense_candidates",
            "dense_matches",
            "result_count",
            "reviewed_result_count",
            "zero_result",
            "fallback_used",
            "top_threads",
        ):
            assert left["trace"][field] == right["trace"][field]


def test_trace_contracts_make_error_and_unavailable_explicit_and_frozen() -> None:
    unavailable = CodexBaselineTrace(
        case_id="smoke-001",
        outcome="unavailable",
        latency_ms=0.0,
        lexical_candidates=0,
        dense_candidates=0,
        dense_matches=0,
        result_count=0,
        reviewed_result_count=0,
        zero_result=False,
        unavailable_reason="production response contract unavailable",
        error_detail_sha256="sha256:" + ("1" * 64),
    )
    error = CodexBaselineTrace(
        case_id="smoke-002",
        outcome="error",
        latency_ms=0.0,
        lexical_candidates=0,
        dense_candidates=0,
        dense_matches=0,
        result_count=0,
        reviewed_result_count=0,
        zero_result=False,
        error_type="RuntimeError",
        error_detail_sha256="sha256:" + ("2" * 64),
    )
    assert unavailable.fallback_used is False
    assert error.fallback_used is False
    with pytest.raises(ValidationError):
        unavailable.model_copy(update={"result_count": 1})
    with pytest.raises(ValidationError):
        CodexBaselineTrace(
            case_id="smoke-003",
            outcome="error",
            latency_ms=float("inf"),
            lexical_candidates=0,
            dense_candidates=0,
            dense_matches=0,
            result_count=0,
            reviewed_result_count=0,
            zero_result=False,
            error_type="RuntimeError",
            error_detail_sha256="sha256:" + ("3" * 64),
        )


def test_exact_production_retriever_exception_becomes_safe_error_trace(
    tmp_path: Path,
) -> None:
    case = baseline.CodexBaselineCase(
        case_id="smoke-error",
        slice="failure/retry",
        query="alpha baseline",
        eligible_metrics=(),
    )
    store = baseline.SQLiteStore(tmp_path / "uninitialized.sqlite3")
    retriever = baseline.CodexHybridRetriever(
        store,
        baseline.LocalHashEmbedding(384),
    )
    predictions, errors, reviewed = baseline._execute_cases(
        run_id="codex-xb0-smoke-error",
        dataset_id="codex-smoke-v1",
        dataset_version="v1",
        package_hash="sha256:" + ("1" * 64),
        cases=(case,),
        denominator_by_case={case.case_id: baseline._empty_denominator_contributions()},
        retriever=retriever,
        judgments_by_case={case.case_id: frozenset()},
    )
    assert type(retriever) is baseline.CodexHybridRetriever
    assert predictions[0].trace.outcome == "error"
    assert predictions[0].trace.zero_result is False
    assert predictions[0].trace.fallback_used is False
    assert errors[0].safe_summary == "production retriever raised an exception"
    assert reviewed == ()


def _fixed_source_run_dir() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "evals"
        / "codex"
        / "runs"
        / baseline.CODEX_XB0_SOURCE_RUN_DIRECTORY_ID
    )


def _readdress_correction(artifact: Path) -> None:
    checksums = _read_json(artifact / "checksums.json")
    files = baseline._file_records(
        artifact,
        baseline._CORRECTION_CHECKSUM_TARGET_FILES,
    )
    checksums["files"] = files
    checksums["artifact_set_hash"] = baseline._artifact_set_hash(files)
    (artifact / "checksums.json").write_bytes(_canonical(checksums) + b"\n")


@pytest.fixture(scope="module")
def correction_artifact(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    source = _fixed_source_run_dir().resolve(strict=True)
    source_snapshot = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()
    }
    root = tmp_path_factory.mktemp("codex-xb0-correction")
    request = baseline.CodexBaselineCorrectionRequest(
        authorization=baseline.CODEX_CORRECTION_AUTHORIZATION,
        source_run_dir=source,
        isolated_root=root,
        output_dir=root / "correction-artifact",
    )
    forbidden_codes = {
        baseline.run_codex_baseline_v1.__code__,
        baseline.CodexHybridRetriever.search.__code__,
        baseline.CodexIngestionService.run.__code__,
        baseline.SQLiteStore.connect.__code__,
    }

    def execution_bomb(frame: Any, event: str, argument: Any) -> Any:
        if event == "call" and frame.f_code in forbidden_codes:
            raise AssertionError("offline correction executed a forbidden production path")
        if event == "c_call" and argument is sqlite3.connect:
            raise AssertionError("offline correction opened SQLite")
        return execution_bomb

    sys.setprofile(execution_bomb)
    try:
        result = baseline.build_codex_baseline_correction_v1(request)
    finally:
        sys.setprofile(None)
    assert result.status == "CORRECTION_PREPARED"
    assert result.retrieval_executed is False
    assert result.persistent_artifact is False
    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()
    } == source_snapshot
    yield result.artifact_path
    shutil.rmtree(root)


def test_correction_contract_is_ephemeral_frozen_and_self_contained(
    correction_artifact: Path,
) -> None:
    verification = baseline.verify_codex_baseline_correction_artifact_v1(correction_artifact)
    assert verification.status == "VERIFIED_CORRECTION_NON_QUALIFIED"
    assert verification.retrieval_executed is False
    assert verification.source_run_immutable is True
    assert verification.portable is True
    assert verification.baseline_qualified is False
    assert {path.name for path in correction_artifact.iterdir()} == set(
        baseline.CORRECTION_CANONICAL_ARTIFACT_FILES
    )
    correction = _read_json(correction_artifact / "correction.json")
    manifest = _read_json(correction_artifact / "manifest.json")
    assert correction["source_run_id"] == baseline.CODEX_XB0_SOURCE_RUN_ID
    assert correction["source_artifact_set_hash"] == baseline.CODEX_XB0_SOURCE_ARTIFACT_SET_HASH
    assert manifest["source"]["files"] == baseline._FIXED_SOURCE_FILE_RECORDS
    assert manifest["evaluator"]["prefix_break"] is False
    assert manifest["evaluator"]["trust_source_reviewed_result_count"] is False
    with pytest.raises(ValidationError):
        baseline.CodexBaselineCorrectionRequest(
            authorization=baseline.CODEX_CORRECTION_AUTHORIZATION,
            source_run_dir=_fixed_source_run_dir().resolve(strict=True),
            isolated_root=correction_artifact.parent,
            output_dir=correction_artifact,
        ).model_copy(update={"authorization": "SMOKE_ONLY"})


def test_correction_complete_truth_matches_gate_authority(
    correction_artifact: Path,
) -> None:
    metrics = _read_json(correction_artifact / "corrected_metrics.json")
    by_name = {item["name"]: item for item in metrics["metrics"]}
    expected = {
        "thread_recall_at_5": (23, 40, 0.575),
        "episode_recall_at_5": (21, 40, 0.525),
        "item_recall_at_10": (26, 40, 0.65),
        "mrr_at_10": (16.2972222222, 40, 0.4074305556),
        "goal_recall_at_10": (7, 7, 1.0),
        "decision_recall_at_10": (5, 7, 5 / 7),
        "harmful_old_attempt_rate_at_10": (5, 26, 5 / 26),
        "event_order_accuracy_at_10": (1, 3, 1 / 3),
        "call_result_link_accuracy_at_10": (1, 2, 0.5),
        "patch_accuracy_at_10": (3, 4, 0.75),
        "validation_accuracy_at_10": (7, 10, 0.7),
        "false_validated_rate_at_10": (1, 5, 0.2),
        "outcome_accuracy_at_10": (1, 3, 1 / 3),
        "context_duplicate_rate_at_10": (0, 45, 0.0),
        "context_noise_rate_at_10": (8, 45, 8 / 45),
        "hard_negative_hit_rate_at_10": (8, 45, 8 / 45),
        "correct_zero_rate": (0, 5, 0.0),
        "refusal_failure_rate": (5, 5, 1.0),
    }
    assert set(by_name) == set(expected)
    for name, (numerator, denominator, value) in expected.items():
        assert by_name[name]["status"] == "AVAILABLE"
        assert by_name[name]["numerator"] == pytest.approx(numerator)
        assert by_name[name]["denominator"] == denominator
        assert by_name[name]["value"] == pytest.approx(value)
    assert metrics["reviewed_evidence_count"] == 41
    assert metrics["reviewed_rank_gap_case_count"] == 22


def test_correction_slice_ledger_reconciles_and_privacy_truth_is_available(
    correction_artifact: Path,
) -> None:
    metrics = _read_json(correction_artifact / "corrected_metrics.json")
    slices = _read_json(correction_artifact / "corrected_slice_report.json")
    overall = {item["name"]: item for item in metrics["metrics"]}
    for name, overall_row in overall.items():
        available = [
            next(row for row in item["metrics"] if row["name"] == name)
            for item in slices["slices"]
            if next(row for row in item["metrics"] if row["name"] == name)["status"] == "AVAILABLE"
        ]
        assert sum(row["denominator"] for row in available) == overall_row["denominator"]
        assert sum(row["numerator"] for row in available) == pytest.approx(overall_row["numerator"])
    privacy = next(
        item for item in slices["slices"] if item["slice"] == "unresolved/unanswerable/privacy"
    )
    privacy_metrics = {item["name"]: item for item in privacy["metrics"]}
    assert privacy_metrics["correct_zero_rate"] == {
        "name": "correct_zero_rate",
        "status": "AVAILABLE",
        "numerator": 0,
        "denominator": 5,
        "value": 0.0,
        "unit": "ratio",
        "reason": None,
    }
    assert privacy_metrics["refusal_failure_rate"]["status"] == "AVAILABLE"
    assert privacy_metrics["refusal_failure_rate"]["numerator"] == 5
    assert privacy_metrics["refusal_failure_rate"]["denominator"] == 5


def test_correction_preserves_rank_gaps_and_ignores_source_reviewed_count(
    correction_artifact: Path,
) -> None:
    manifest = _read_json(correction_artifact / "manifest.json")
    golden = _read_jsonl(correction_artifact / "golden_cases.jsonl")
    predictions = _read_jsonl(correction_artifact / "predictions.jsonl")
    dataset = baseline._rebuild_released_dataset_authority(
        manifest["source"]["dataset"],
        golden,
    )
    parsed = baseline._parse_correction_predictions(predictions)
    reviewed, prediction_by_case, count, gap_count = baseline._derive_complete_correction_evidence(
        dataset, parsed
    )
    assert count == 41
    assert gap_count == 22
    assert [row.rank for row in reviewed["codex-v1-004"]] == [1, 4]
    assert prediction_by_case["codex-v1-004"].trace.reviewed_result_count == 1
    assert [row.rank for row in reviewed["codex-v1-043"]] == [4, 9]
    assert prediction_by_case["codex-v1-043"].trace.reviewed_result_count == 0


def test_correction_hard_negative_and_refusal_reports_are_complete(
    correction_artifact: Path,
) -> None:
    hard = _read_json(correction_artifact / "hard_negative_report.json")
    refusal = _read_json(correction_artifact / "refusal_report.json")
    assert hard["case_count"] == 45
    assert hard["hit_count"] == 8
    assert [(item["case_id"], item["rank"], item["route"]) for item in hard["hits"]] == [
        ("codex-v1-004", 4, "truncated_tool_result"),
        ("codex-v1-011", 5, "failed_attempt"),
        ("codex-v1-018", 10, "archived_duplicate"),
        ("codex-v1-023", 5, "assistant_claim_without_exit"),
        ("codex-v1-026", 9, "archived_duplicate"),
        ("codex-v1-038", 6, "archived_duplicate"),
        ("codex-v1-039", 8, "subagent_same_topic"),
        ("codex-v1-043", 9, "truncated_tool_result"),
    ]
    assert sum(item["denominator"] for item in hard["routes"]) == 45
    assert sum(item["hits"] for item in hard["routes"]) == 8
    assert refusal["eligible_case_ids"] == list(baseline.CODEX_XB0_CASE_MEMBERSHIP[-5:])
    assert refusal["correct_zero_metric"]["numerator"] == 0
    assert refusal["correct_zero_metric"]["denominator"] == 5
    assert refusal["refusal_failure_metric"]["numerator"] == 5
    assert refusal["refusal_failure_metric"]["denominator"] == 5
    case_043 = next(item for item in refusal["cases"] if item["case_id"] == "codex-v1-043")
    assert case_043["hard_negative_route"] == "truncated_tool_result"
    assert case_043["hard_negative_hit"] is True
    assert case_043["hard_negative_rank"] == 9


def test_correction_artifact_is_portable_verify_only(
    correction_artifact: Path,
    tmp_path: Path,
) -> None:
    copied = tmp_path / "portable-correction"
    shutil.copytree(correction_artifact, copied)
    verification = baseline.verify_codex_baseline_correction_artifact_v1(copied)
    assert verification.status == "VERIFIED_CORRECTION_NON_QUALIFIED"
    assert verification.retrieval_executed is False
    assert verification.portable is True


@pytest.mark.parametrize(
    "mutation",
    [
        "metric",
        "slice",
        "hard_negative",
        "refusal",
        "reviewed_count",
        "later_judgment",
        "delete",
        "extra",
    ],
)
def test_correction_verifier_rejects_readdressed_tamper_delete_and_extra(
    correction_artifact: Path,
    tmp_path: Path,
    mutation: str,
) -> None:
    target = tmp_path / mutation
    shutil.copytree(correction_artifact, target)
    if mutation == "metric":
        payload = _read_json(target / "corrected_metrics.json")
        payload["metrics"][0]["numerator"] += 1
        payload["metrics"][0]["value"] = (
            payload["metrics"][0]["numerator"] / payload["metrics"][0]["denominator"]
        )
        (target / "corrected_metrics.json").write_bytes(_canonical(payload) + b"\n")
    elif mutation == "slice":
        payload = _read_json(target / "corrected_slice_report.json")
        payload["slices"][0]["metrics"][0]["numerator"] += 1
        (target / "corrected_slice_report.json").write_bytes(_canonical(payload) + b"\n")
    elif mutation == "hard_negative":
        payload = _read_json(target / "hard_negative_report.json")
        payload["hits"][0]["rank"] = 10
        (target / "hard_negative_report.json").write_bytes(_canonical(payload) + b"\n")
    elif mutation == "refusal":
        payload = _read_json(target / "refusal_report.json")
        payload["cases"][2]["hard_negative_rank"] = 8
        (target / "refusal_report.json").write_bytes(_canonical(payload) + b"\n")
    elif mutation in {"reviewed_count", "later_judgment"}:
        rows = _read_jsonl(target / "predictions.jsonl")
        if mutation == "reviewed_count":
            rows[0]["trace"]["reviewed_result_count"] = 1
        else:
            replacement = dict(rows[3]["results"][1])
            replacement["rank"] = 4
            rows[3]["results"][3] = replacement
        _write_jsonl(target / "predictions.jsonl", rows)
    elif mutation == "delete":
        (target / "refusal_report.json").unlink()
        with pytest.raises(CodexBaselineError):
            baseline.verify_codex_baseline_correction_artifact_v1(target)
        return
    else:
        (target / "extra.json").write_bytes(b"{}\n")
        with pytest.raises(CodexBaselineError):
            baseline.verify_codex_baseline_correction_artifact_v1(target)
        return
    _readdress_correction(target)
    with pytest.raises(CodexBaselineError):
        baseline.verify_codex_baseline_correction_artifact_v1(target)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "4111 1111 1111 1111",
        "owner@example.com",
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "/private/tmp/raw/evidence-rag.sqlite3",
        "artifact/evaluation.sqlite3-wal",
        "cache/result.pyc",
        float("nan"),
    ],
)
def test_correction_verifier_rejects_security_and_nonfinite_tamper(
    correction_artifact: Path,
    tmp_path: Path,
    unsafe_value: object,
) -> None:
    target = tmp_path / f"unsafe-{len(list(tmp_path.iterdir()))}"
    shutil.copytree(correction_artifact, target)
    correction = _read_json(target / "correction.json")
    correction["unsafe"] = unsafe_value
    raw = json.dumps(
        correction,
        allow_nan=True,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    (target / "correction.json").write_bytes(raw + b"\n")
    _readdress_correction(target)
    with pytest.raises(CodexBaselineError):
        baseline.verify_codex_baseline_correction_artifact_v1(target)


def test_correction_builder_rejects_non_authoritative_source_without_execution(
    tmp_path: Path,
) -> None:
    copied_parent = tmp_path / "copied-source"
    copied_parent.mkdir()
    copied_source = copied_parent / baseline.CODEX_XB0_SOURCE_RUN_DIRECTORY_ID
    shutil.copytree(_fixed_source_run_dir(), copied_source)
    with (copied_source / "metrics.json").open("ab") as handle:
        handle.write(b" ")
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    request = baseline.CodexBaselineCorrectionRequest(
        authorization=baseline.CODEX_CORRECTION_AUTHORIZATION,
        source_run_dir=copied_source,
        isolated_root=isolated,
        output_dir=isolated / "correction",
    )
    with pytest.raises(CodexBaselineError):
        baseline.build_codex_baseline_correction_v1(request)
    assert not request.output_dir.exists()
