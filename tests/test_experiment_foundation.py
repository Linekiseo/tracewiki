from __future__ import annotations

import importlib.machinery
import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from evidence_rag.rag.sources.experiment.evaluation_v1 import (
    EXPERIMENT_GOLDEN_PACKAGE_HASH,
    load_experiment_golden_v1,
    prepare_experiment_evaluation_v1,
)
from evidence_rag.rag.sources.experiment.fixture_v1 import (
    EXPERIMENT_CURRENT_PRODUCTION_AUTHORITY_VERSION,
    EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST,
    EXPERIMENT_CURRENT_PRODUCTION_PARITY_HASH,
    EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST,
    ExperimentFixtureError,
    FixtureMetricDirection,
    FixtureRunStatus,
    FixtureVerificationState,
    _portable_module_origin,
    build_production_lane_parity,
    deterministic_experiment_recipe,
    experiment_adapters_public,
    experiment_service_public,
    verify_production_component_authority,
)


def test_recipe_has_frozen_logical_counts_and_hard_conditions() -> None:
    recipe = deterministic_experiment_recipe()

    assert recipe.logical_counts == {
        "experiments": 1,
        "runs": 12,
        "metric_definitions": 3,
        "metric_series": 18,
        "metric_observations": 47,
        "artifacts": 2,
    }
    assert tuple(run.seed for run in recipe.runs if run.group == "baseline") == (11, 22, 33)
    assert tuple(run.seed for run in recipe.runs if run.group == "treatment") == (11, 22, 33)
    assert sum(run.status == FixtureRunStatus.FAILED for run in recipe.runs) == 1
    assert sum(run.status == FixtureRunStatus.RUNNING for run in recipe.runs) == 1
    assert {type(run.config["workers"]) for run in recipe.runs} == {int, str}
    assert any(
        metric.direction == FixtureMetricDirection.UNKNOWN
        for run in recipe.runs
        for metric in run.metrics
    )
    assert any(len(metric.points) == 3 for run in recipe.runs for metric in run.metrics)
    assert {artifact.verification_state for run in recipe.runs for artifact in run.artifacts} == {
        FixtureVerificationState.VERIFIED_CHECKSUM,
        FixtureVerificationState.LOCATED_UNVERIFIED,
    }
    assert tuple(definition.definition_id for definition in recipe.metric_definitions) == (
        "metric-definition://fixture/latency_ms",
        "metric-definition://fixture/mystery_score",
        "metric-definition://fixture/ndcg_at_10",
    )
    series = [metric for run in recipe.runs for metric in run.metrics]
    points = [point for metric in series for point in metric.points]
    assert len({metric.series_id for metric in series}) == 18
    assert len({point.observation_id for point in points}) == 47
    assert (
        recipe.production_authority.component_set_digest
        == EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST
    )
    assert all(point.run_id == metric.run_id for metric in series for point in metric.points)
    assert all(
        point.definition_id == metric.definition_id
        and point.series_id == metric.series_id
        and point.locator.startswith(metric.locator + "/observations/step/")
        for metric in series
        for point in metric.points
    )


def test_real_mlflow_and_manual_production_lanes_have_frozen_parity(
    tmp_path: Path,
) -> None:
    parity = build_production_lane_parity(tmp_path)

    assert parity.parity is True
    assert parity.differences == ()
    assert parity.parity_hash == EXPERIMENT_CURRENT_PRODUCTION_PARITY_HASH
    assert parity.mlflow.parity_digest == parity.manual.parity_digest
    assert parity.mlflow.production_evidence_digest != parity.manual.production_evidence_digest
    assert (
        parity.production_authority.component_set_digest
        == EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
    )
    assert tuple(component.role for component in parity.production_authority.components) == (
        "mlflow-adapter-class",
        "experiment-service-class",
        "experiment-sync-mlflow",
        "experiment-create-experiment",
        "experiment-create-run",
        "experiment-create-model",
        "run-create-model",
    )
    assert parity.mlflow.production_authority == parity.production_authority
    assert parity.manual.production_authority == parity.production_authority
    assert "filestore_latest_metric_only" not in parity.mlflow.capability_gaps
    assert "manual_service_unknown_direction_unrepresentable_v1" in (parity.manual.capability_gaps)
    assert list(tmp_path.rglob("*.sqlite3"))
    assert all(path.is_relative_to(tmp_path) for path in tmp_path.rglob("*.sqlite3"))


@pytest.mark.parametrize(
    "attack",
    ["method_wrapper", "exact_code_clone", "public_export", "wrong_digest"],
)
def test_production_authority_attacks_fail_before_temp_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    if attack == "method_wrapper":
        original = experiment_service_public.ExperimentService.sync_mlflow

        def replacement(self, request):
            return original(self, request)

        monkeypatch.setattr(
            experiment_service_public.ExperimentService,
            "sync_mlflow",
            replacement,
        )
    elif attack == "exact_code_clone":
        original = experiment_service_public.ExperimentService.sync_mlflow
        replacement = types.FunctionType(
            original.__code__,
            original.__globals__,
            original.__name__,
            original.__defaults__,
            original.__closure__,
        )
        replacement.__kwdefaults__ = original.__kwdefaults__
        replacement.__module__ = original.__module__
        replacement.__qualname__ = original.__qualname__
        monkeypatch.setattr(
            experiment_service_public.ExperimentService,
            "sync_mlflow",
            replacement,
        )
    elif attack == "public_export":
        original = experiment_adapters_public.MLflowAdapter

        class Replacement(original):
            pass

        monkeypatch.setattr(
            experiment_adapters_public,
            "MLflowAdapter",
            Replacement,
        )
    else:
        monkeypatch.setattr(
            experiment_adapters_public.MLflowAdapter,
            "adapter_version",
            "forged-version",
        )

    with pytest.raises(ExperimentFixtureError):
        build_production_lane_parity(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_preimport_component_injection_fails_in_fresh_process_without_io(
    tmp_path: Path,
) -> None:
    target = tmp_path / "fresh-process"
    target.mkdir()
    script = textwrap.dedent(
        """
        import pathlib
        import sys
        import evidence_rag.experiments.adapters as public
        import evidence_rag.experiments.adapters.mlflow as defining
        import evidence_rag.experiments.service as service

        original = defining.MLflowAdapter
        class InjectedAdapter(original):
            pass
        defining.MLflowAdapter = InjectedAdapter
        public.MLflowAdapter = InjectedAdapter
        service.MLflowAdapter = InjectedAdapter

        from evidence_rag.rag.sources.experiment.fixture_v1 import (
            ExperimentFixtureError,
            build_production_lane_parity,
        )

        root = pathlib.Path(sys.argv[1])
        try:
            build_production_lane_parity(root)
        except ExperimentFixtureError:
            if list(root.iterdir()):
                raise SystemExit("authority failure created temp side effects")
        else:
            raise SystemExit("pre-import injection was accepted")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(target)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert list(target.iterdir()) == []


def test_fixed_component_authority_is_not_learned_from_runtime() -> None:
    authority = verify_production_component_authority()
    mlflow = next(
        component for component in authority.components if component.role == "mlflow-adapter-class"
    )
    service = next(
        component
        for component in authority.components
        if component.role == "experiment-service-class"
    )

    assert authority.schema_version == EXPERIMENT_CURRENT_PRODUCTION_AUTHORITY_VERSION
    assert authority.component_set_digest == EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
    assert (
        mlflow.version,
        mlflow.source_sha256,
        mlflow.code_sha256,
        mlflow.bindings_sha256,
    ) == (
        "mlflow-tracking-v2",
        "sha256:20e299abfe1ade4e85637e8b6da478a9363ad5f1f8cc65732172ac352b7eb6b5",
        "sha256:256632ae82d0333729aeac680b2d2afeb558db7824ebb77e4905c461723ebe36",
        "sha256:b94d1f49edf7fa94824f88f92ce5d288811bb208a2997055f4501e2075cb5d18",
    )
    assert (
        service.version,
        service.source_sha256,
        service.code_sha256,
        service.bindings_sha256,
    ) == (
        "experiment-service-v3",
        "sha256:13403175f2499205fc215777d55bf0807ccb54c2c7755e28355824cdb250d98c",
        "sha256:7d9ae7d0cca8d49fc8faa2ffce884b26f2f8bebed1861c333391a77eb81283f9",
        "sha256:e5b28cd35c8b416387cb2d56d84376db49fafba4c33def4833dedcc2e60e7c41",
    )
    assert all(
        component.source_sha256 != component.code_sha256 for component in authority.components
    )


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("/checkout/.venv/lib/python/site-packages/httpx/__init__.py", "<module-root>/httpx/__init__.py"),
        (r"C:\checkout\httpx\__init__.py", "<module-root>/httpx/__init__.py"),
        ("/runtime/extensions/httpx.abi3.so", "<module-file>/httpx.abi3.so"),
        ("frozen", "frozen"),
    ],
)
def test_module_authority_origin_is_checkout_portable(origin: str, expected: str) -> None:
    module = types.ModuleType("httpx")
    module.__spec__ = importlib.machinery.ModuleSpec("httpx", loader=None, origin=origin)

    assert _portable_module_origin(module) == expected


def test_released_package_is_portable_and_contains_no_host_path_or_secret() -> None:
    dataset = load_experiment_golden_v1()
    package = Path(__file__).parent / "fixtures" / "experiment_golden"

    assert dataset.package_hash == EXPERIMENT_GOLDEN_PACKAGE_HASH
    for path in package.iterdir():
        payload = path.read_text(encoding="utf-8")
        assert "/Users/" not in payload
        assert r"C:\\" not in payload
        assert "\\\\server\\" not in payload
        assert "SECRET_SENTINEL" not in payload
        assert "@" not in payload
        if path.suffix == ".json":
            json.loads(payload)


def test_evaluation_preparation_has_no_run_metric_artifact_or_qualification() -> None:
    preparation = prepare_experiment_evaluation_v1()

    assert preparation.foundation_status == "E0-01 IMPLEMENTED / AWAITING FOUNDATION GATE"
    assert preparation.case_count == 45
    assert preparation.evaluation_run_created is False
    assert preparation.baseline_run_created is False
    assert preparation.metrics_published is False
    assert preparation.qualification_created is False


def test_foundation_preserves_the_single_published_baseline_run() -> None:
    repository = Path(__file__).resolve().parents[1]
    runs = repository / "evals" / "experiment" / "runs"
    assert tuple(path.name for path in runs.iterdir() if path.is_dir()) == (
        "085799235df74bcdc4fc56c0c17f36e7",
    )
