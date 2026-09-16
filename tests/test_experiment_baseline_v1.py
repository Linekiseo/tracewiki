from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from typing import Any

import pytest

import evidence_rag.rag.sources.experiment.baseline_v1 as baseline_module
from evidence_rag.rag.sources.experiment.baseline_v1 import (
    EXPERIMENT_BASELINE_ARTIFACT_FILES,
    EXPERIMENT_BASELINE_CURRENT_PRODUCTION_AUTHORITY_VERSION,
    EXPERIMENT_BASELINE_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST,
    EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST,
    EXPERIMENT_FOUNDATION_ELIGIBLE_DENOMINATORS,
    EXPERIMENT_FOUNDATION_LOGICAL_COUNTS,
    EXPERIMENT_FOUNDATION_RECIPE_HASH,
    EXPERIMENT_FOUNDATION_RELEASE_ID,
    EXPERIMENT_FOUNDATION_SLICE_COUNTS,
    EXPERIMENT_FOUNDATION_TASK_ID,
    EXPERIMENT_HARNESS_GATE_TASK_ID,
    ExperimentBaselineArtifactError,
    ExperimentBaselineAuthorityError,
    ExperimentBaselineCandidate,
    ExperimentBaselineGateError,
    ExperimentBaselineNotAuthorized,
    ExperimentBaselinePathError,
    ExperimentBaselinePrediction,
    ExperimentComparisonRecord,
    _build_experiment_baseline_artifact_v1,
    admit_experiment_baseline_artifact_layout,
    admit_experiment_baseline_paths,
    admit_experiment_baseline_run_paths,
    fixed_experiment_baseline_current_production_authority_v2,
    fixed_experiment_baseline_production_authority_v1,
    load_experiment_foundation_gate_v1,
    load_experiment_harness_gate_v1,
    parse_experiment_foundation_gate_v1,
    parse_experiment_harness_gate_v1,
    prepare_experiment_baseline_v1,
    run_experiment_baseline_smoke_v1,
    run_experiment_baseline_v1,
    verify_experiment_baseline_artifact_v1,
    verify_experiment_baseline_production_authority_v1,
    verify_experiment_foundation_authority_v1,
)
from evidence_rag.rag.sources.experiment.evaluation_v1 import (
    EXPERIMENT_GOLDEN_AUTHORITY_HASH,
    EXPERIMENT_GOLDEN_PACKAGE_HASH,
    load_experiment_golden_v1,
    perfect_reviewed_experiment_rows,
)
from evidence_rag.rag.sources.experiment.fixture_v1 import (
    EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST,
    EXPERIMENT_PRODUCTION_PARITY_HASH,
)

REPOSITORY = Path(__file__).resolve().parents[1]
PUBLISHED_RUN = REPOSITORY / "evals/experiment/runs/085799235df74bcdc4fc56c0c17f36e7"
FOUNDATION_GATE = (
    REPOSITORY / "docs/rag-optimization/development/reviews/"
    "11_EXPERIMENT_E0_FOUNDATION_GATE_REVIEW.md"
)
HARNESS_GATE = (
    REPOSITORY / "docs/rag-optimization/development/reviews/"
    "12_EXPERIMENT_EB0_HARNESS_GATE_REVIEW.md"
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=True,
        ).encode("utf-8")
        + b"\n"
    )


def _rewrite_json(path: Path, value: object) -> None:
    path.write_bytes(_json_bytes(value))


def _rewrite_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_bytes(b"".join(_json_bytes(value) for value in values))


def _resign(directory: Path, name: str) -> None:
    checksums_path = directory / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    payload = (directory / name).read_bytes()
    checksums["files"][name] = {
        "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    _rewrite_json(checksums_path, checksums)


def _valid_predictions() -> tuple[
    Any,
    tuple[ExperimentBaselinePrediction, ...],
    tuple[ExperimentComparisonRecord, ...],
]:
    dataset = load_experiment_golden_v1()
    reviewed_rows = perfect_reviewed_experiment_rows(dataset)
    predictions = []
    for case, row in zip(dataset.cases, reviewed_rows, strict=True):
        candidates = tuple(
            ExperimentBaselineCandidate(
                rank=candidate.rank,
                entity_id=candidate.entity_id,
                entity_kind=dataset.authority_entities[candidate.entity_id].kind,
                logical_locator=candidate.locator,
                acl_ref=candidate.acl_ref,
                matched_terms=("fixture",),
                raw_structured_fields={
                    "entity_id": candidate.entity_id,
                    "source": "experiment",
                    "entity_type": dataset.authority_entities[candidate.entity_id].kind.value,
                    "matched_terms": ["fixture"],
                },
            )
            for candidate in row.candidates
        )
        predictions.append(
            ExperimentBaselinePrediction(
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.dataset_version,
                package_hash=dataset.package_hash,
                case_id=case.case_id,
                question=case.question,
                outcome=row.outcome,
                candidates=candidates,
                candidate_count=len(candidates),
                observed_answerability=row.observed_answerability,
                observed_predicate=row.observed_predicate,
                observed_metric_truth=row.observed_metric_truth,
                observed_comparability=row.observed_comparability,
                observed_reproduction=row.observed_reproduction,
                unavailable_reason=row.unavailable_reason,
                error_reason=row.error_reason,
                latency_ms=1.0,
            )
        )
    comparisons = tuple(
        ExperimentComparisonRecord(case_id=case.case_id, attempted=False) for case in dataset.cases
    )
    return dataset, tuple(predictions), comparisons


def _build_valid_artifact(tmp_path: Path) -> Path:
    dataset, predictions, comparisons = _valid_predictions()
    output = tmp_path / "artifact"
    result = _build_experiment_baseline_artifact_v1(
        output_directory=output,
        dataset=dataset,
        predictions=predictions,
        comparisons=comparisons,
        production_authority=fixed_experiment_baseline_production_authority_v1(),
        execution_mode="TEST_FIXTURE",
        seed=20260729,
    )
    assert result.status == "NON_QUALIFIED"
    return output


def _valid_harness_gate_text() -> str:
    return textwrap.dedent(
        f"""
        # Independent Harness review
        Harness Gate 来源任务：`{EXPERIMENT_HARNESS_GATE_TASK_ID}`

        release:   {EXPERIMENT_FOUNDATION_RELEASE_ID}
        package:   {EXPERIMENT_GOLDEN_PACKAGE_HASH}
        authority: {EXPERIMENT_GOLDEN_AUTHORITY_HASH}
        recipe:    {EXPERIMENT_FOUNDATION_RECIPE_HASH}
        foundation-component: {EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST}
        harness-component: {EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST}
        parity:    {EXPERIMENT_PRODUCTION_PARITY_HASH}
        artifact:  experiment-e-b0-portable-artifact-v1

        ```
        EXPERIMENT E-B0 HARNESS PASS
        P0 findings: 0
        P1 findings: 0
        E-B0 ISOLATED PRODUCTION RUN AUTHORIZED
        E-B0 Run/metrics/artifact/qualification: NONE
        E-B0 production Run executed: false
        E1: NOT_AUTHORIZED
        ```
        """
    ).strip()


def test_latest_foundation_pass_authorizes_preparation_only() -> None:
    text = FOUNDATION_GATE.read_text(encoding="utf-8")
    decision = parse_experiment_foundation_gate_v1(text)

    assert decision.source_task_id == EXPERIMENT_FOUNDATION_TASK_ID
    assert decision.conclusion == "EXPERIMENT E0-01 FOUNDATION PASS"
    assert decision.p0_findings == decision.p1_findings == 0
    assert decision.preparation_authorization == "E-B0 HARNESS PREPARATION AUTHORIZED"
    assert decision.production_execution == "NOT_AUTHORIZED"
    assert decision.e1_status == "NOT_AUTHORIZED"
    assert decision.authority.release_record_id == EXPERIMENT_FOUNDATION_RELEASE_ID


@pytest.mark.parametrize(
    "attack",
    [
        lambda value: value.replace(
            EXPERIMENT_FOUNDATION_RELEASE_ID,
            "release-00000000000000000000000000000000",
        ),
        lambda value: value.replace(
            "P1 findings: 0\nE-B0 HARNESS PREPARATION AUTHORIZED",
            "P1 findings: 1\nE-B0 HARNESS PREPARATION AUTHORIZED",
        ),
        lambda value: (
            value + "\n# Later relevant denial\n```\nEXPERIMENT E0-01 FOUNDATION FAIL\n"
            "P0 findings: 0\nP1 findings: 0\n"
            "E-B0 HARNESS PREPARATION NOT AUTHORIZED\n```\n"
        ),
        lambda value: value.replace(
            "EXPERIMENT E0-01 FOUNDATION PASS",
            "prefix EXPERIMENT E0-01 FOUNDATION PASS suffix",
        ),
        lambda value: value.replace(
            f"Gate 来源任务：`{EXPERIMENT_FOUNDATION_TASK_ID}`",
            "Gate 来源任务：`11111111-2222-4333-8444-555555555555`",
        ),
    ],
)
def test_foundation_gate_tamper_history_and_substrings_fail(
    attack: Any,
) -> None:
    with pytest.raises(ExperimentBaselineGateError):
        parse_experiment_foundation_gate_v1(attack(FOUNDATION_GATE.read_text(encoding="utf-8")))


def test_gate_copy_is_not_authoritative(tmp_path: Path) -> None:
    copied = tmp_path / FOUNDATION_GATE.name
    copied.write_bytes(FOUNDATION_GATE.read_bytes())

    with pytest.raises(ExperimentBaselineGateError, match="copies"):
        load_experiment_foundation_gate_v1(copied)


def test_canonical_harness_gate_is_final_pass_and_ready() -> None:
    decision = load_experiment_harness_gate_v1(HARNESS_GATE)

    assert decision.harness_task_id == EXPERIMENT_HARNESS_GATE_TASK_ID
    assert decision.conclusion == "EXPERIMENT E-B0 HARNESS PASS"
    assert decision.p0_findings == 0
    assert decision.p1_findings == 0
    assert decision.run_authorization == "E-B0 ISOLATED PRODUCTION RUN AUTHORIZED"
    assert decision.authorized is True
    assert decision.production_component_hash == (
        EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST
    )
    assert decision.e1_status == "NOT_AUTHORIZED"


def test_synthetic_next_complete_harness_pass_is_the_only_authorized_branch() -> None:
    text = HARNESS_GATE.read_text(encoding="utf-8")
    decision = parse_experiment_harness_gate_v1(text + "\n\n" + _valid_harness_gate_text())

    assert decision.harness_task_id == EXPERIMENT_HARNESS_GATE_TASK_ID
    assert decision.conclusion == "EXPERIMENT E-B0 HARNESS PASS"
    assert decision.p0_findings == decision.p1_findings == 0
    assert decision.run_authorization == "E-B0 ISOLATED PRODUCTION RUN AUTHORIZED"
    assert decision.authorized is True
    assert decision.production_component_hash == (
        EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST
    )
    assert decision.e1_status == "NOT_AUTHORIZED"


@pytest.mark.parametrize(
    "task_id",
    [
        "11111111-2222-4333-8444-555555555555",
        "019fac2b-517a-79b3-8c1a-3a0625cd7ee6",
        EXPERIMENT_FOUNDATION_TASK_ID,
    ],
)
def test_harness_gate_rejects_forged_v4_v7_and_foundation_task(
    task_id: str,
) -> None:
    forged = _valid_harness_gate_text().replace(
        EXPERIMENT_HARNESS_GATE_TASK_ID,
        task_id,
    )

    with pytest.raises(ExperimentBaselineGateError):
        parse_experiment_harness_gate_v1(forged)


@pytest.mark.parametrize(
    "attack",
    [
        lambda value: value.replace(
            "P1 findings: 0\nE-B0 ISOLATED PRODUCTION RUN AUTHORIZED",
            "P1 findings: 0\nP1 findings: 0\nE-B0 ISOLATED PRODUCTION RUN AUTHORIZED",
        ),
        lambda value: value.replace(
            "E-B0 ISOLATED PRODUCTION RUN AUTHORIZED",
            "E-B0 ISOLATED PRODUCTION RUN NOT AUTHORIZED\nE-B0 ISOLATED PRODUCTION RUN AUTHORIZED",
        ),
        lambda value: value.replace(
            "release:   ",
            f"release: {EXPERIMENT_FOUNDATION_RELEASE_ID}\nrelease:   ",
        ),
        lambda value: value.replace(
            "# Independent Harness review",
            "# Independent Harness review\n\n# Independent Harness review",
        ),
        lambda value: value.replace(
            "```\nEXPERIMENT E-B0 HARNESS PASS",
            "```\nP0 findings: 0\n```\n\n```\nEXPERIMENT E-B0 HARNESS PASS",
        ),
    ],
)
def test_harness_gate_whole_section_duplicates_and_conflicts_fail(
    attack: Any,
) -> None:
    with pytest.raises(ExperimentBaselineGateError):
        parse_experiment_harness_gate_v1(attack(_valid_harness_gate_text()))


@pytest.mark.parametrize(
    "attack",
    [
        lambda value: value.replace(
            "EXPERIMENT E0-01 FOUNDATION PASS\nP0 findings: 0",
            "EXPERIMENT E0-01 FOUNDATION PASS\nEXPERIMENT E0-01 FOUNDATION PASS\nP0 findings: 0",
        ),
        lambda value: value.replace(
            "P1 findings: 0\nE-B0 HARNESS PREPARATION AUTHORIZED",
            "P1 findings: 0\nP1 findings: 0\nE-B0 HARNESS PREPARATION AUTHORIZED",
        ),
        lambda value: value.replace(
            "E-B0 HARNESS PREPARATION AUTHORIZED",
            "E-B0 HARNESS PREPARATION NOT AUTHORIZED\nE-B0 HARNESS PREPARATION AUTHORIZED",
        ),
        lambda value: value.replace(
            "release:   ",
            f"release: {EXPERIMENT_FOUNDATION_RELEASE_ID}\nrelease:   ",
        ),
    ],
)
def test_foundation_gate_whole_section_duplicates_and_conflicts_fail(
    attack: Any,
) -> None:
    with pytest.raises(ExperimentBaselineGateError):
        parse_experiment_foundation_gate_v1(attack(FOUNDATION_GATE.read_text(encoding="utf-8")))


def test_foundation_identity_is_exact_and_published_run_verifies_offline(
    tmp_path: Path,
) -> None:
    before = tuple(tmp_path.iterdir())
    foundation = verify_experiment_foundation_authority_v1()
    verification = verify_experiment_baseline_artifact_v1(PUBLISHED_RUN)
    after = tuple(tmp_path.iterdir())

    assert before == after == ()
    assert foundation.package_hash == EXPERIMENT_GOLDEN_PACKAGE_HASH
    assert foundation.authority_hash == EXPERIMENT_GOLDEN_AUTHORITY_HASH
    assert foundation.recipe_hash == EXPERIMENT_FOUNDATION_RECIPE_HASH
    assert foundation.component_hash == EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST
    assert foundation.parity_hash == EXPERIMENT_PRODUCTION_PARITY_HASH
    assert foundation.membership == tuple(
        f"experiment-v1-{ordinal:03d}" for ordinal in range(1, 46)
    )
    assert foundation.slice_counts == dict(EXPERIMENT_FOUNDATION_SLICE_COUNTS)
    assert foundation.eligible_denominators == dict(EXPERIMENT_FOUNDATION_ELIGIBLE_DENOMINATORS)
    assert foundation.logical_counts == dict(EXPERIMENT_FOUNDATION_LOGICAL_COUNTS)
    assert verification.status == "VERIFIED_NON_QUALIFIED"
    assert verification.case_count == 45
    assert verification.service_calls == 0
    assert verification.database_calls == 0
    assert verification.network_calls == 0


def test_published_production_authority_remains_frozen_after_product_evolves() -> None:
    authority = fixed_experiment_baseline_production_authority_v1()

    assert len(authority.components) == 21
    assert authority.schema_version == "experiment-e-b0-current-v1-production-authority-v2"
    assert authority.component_set_digest == EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST
    assert authority.foundation_component_set_digest == (EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST)
    assert authority.default_v1 is True
    assert authority.experiment_v2_forbidden is True
    assert authority.semantic_forbidden is True
    assert authority.reranker_forbidden is True
    assert authority.context_builder_forbidden is True
    assert authority.experiment_source_only is True
    assert authority.notebook_family_preserved is True
    assert authority.evidence_match == "token-substring"
    assert authority.comparison_contract == "controls/config/name+split/raw-delta"
    assert tuple(component.role for component in authority.components[-14:]) == (
        "experiment-compare-model",
        "experiment-compare",
        "experiment-controls",
        "experiment-config-differences",
        "experiment-metric-differences",
        "platform-search-model",
        "platform-service-class",
        "platform-service-search",
        "platform-service-in-scope",
        "platform-service-deduplicate",
        "platform-service-diversify",
        "platform-store-class",
        "platform-store-structured-search",
        "platform-store-query-terms",
    )


def test_current_production_authority_is_explicit_and_static() -> None:
    authority = fixed_experiment_baseline_current_production_authority_v2()
    by_role = {component.role: component for component in authority.components}

    assert len(authority.components) == 21
    assert authority.schema_version == EXPERIMENT_BASELINE_CURRENT_PRODUCTION_AUTHORITY_VERSION
    assert authority.component_set_digest == (
        EXPERIMENT_BASELINE_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
    )
    assert authority.component_set_digest != EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST
    assert authority.foundation_component_set_digest != EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST
    assert (
        by_role["platform-search-model"].version,
        by_role["platform-search-model"].source_sha256,
        by_role["platform-search-model"].code_sha256,
        by_role["platform-search-model"].bindings_sha256,
    ) == (
        "platform-search-model-v4",
        "sha256:61dffdcfe33df153784a641c6a9647ffef1c5b669548dfb018371986abeea64b",
        "sha256:e10e339440dcb89af6cf0e51d04b804e8e08ffbfa53546c85b01bf8f58bdcbdf",
        "sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9",
    )
    assert (
        by_role["platform-service-class"].version,
        by_role["platform-service-class"].source_sha256,
        by_role["platform-service-class"].code_sha256,
        by_role["platform-service-class"].bindings_sha256,
    ) == (
        "platform-service-current-v6",
        "sha256:5458f6252815fa6c7d5d4697da74dc18737df036bf3b411ca9bc7bd1870cceba",
        "sha256:092230756c0fb37ea19861ecf1b648b93cd2e64f99b28f6ce79d5282fd4170a0",
        "sha256:f2428c619535c7cb189549066b44fd67d47c022a944a588fef93a6e4b73e5d5a",
    )
    assert (
        by_role["experiment-service-class"].version,
        by_role["experiment-service-class"].source_sha256,
        by_role["experiment-service-class"].code_sha256,
        by_role["experiment-service-class"].bindings_sha256,
    ) == (
        "experiment-service-v3",
        "sha256:13403175f2499205fc215777d55bf0807ccb54c2c7755e28355824cdb250d98c",
        "sha256:7d9ae7d0cca8d49fc8faa2ffce884b26f2f8bebed1861c333391a77eb81283f9",
        "sha256:e5b28cd35c8b416387cb2d56d84376db49fafba4c33def4833dedcc2e60e7c41",
    )
    assert (
        by_role["platform-service-search"].version,
        by_role["platform-service-search"].bindings_sha256,
    ) == (
        "platform-service-current-v4",
        "sha256:63ab9ae7a9d346a646ae298206eed833ffbcff61a3110df22a56197fcd544e66",
    )


def test_unmodified_current_production_authority_verifier_succeeds_without_io(
    tmp_path: Path,
) -> None:
    authority = verify_experiment_baseline_production_authority_v1()

    assert authority == fixed_experiment_baseline_current_production_authority_v2()
    assert authority.component_set_digest == (
        EXPERIMENT_BASELINE_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
    )
    assert tuple(tmp_path.iterdir()) == ()


def test_current_production_smoke_stays_isolated_and_non_qualified(tmp_path: Path) -> None:
    result = run_experiment_baseline_smoke_v1(temp_root=tmp_path)

    assert result.status == "SMOKE_UNAVAILABLE"
    assert result.production_execution == "NOT_AUTHORIZED"
    assert result.released45_executed is False
    assert result.evaluation_run_created is False
    assert result.metrics_published is False
    assert result.mlflow_synced_runs == result.manual_created_runs == 12


@pytest.mark.parametrize(
    "role",
    [
        component.role
        for component in fixed_experiment_baseline_current_production_authority_v2().components
    ],
)
def test_all_21_component_replacements_fail_before_io(
    role: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = next(
        component
        for component in fixed_experiment_baseline_current_production_authority_v2().components
        if component.role == role
    )
    owner: object = importlib.import_module(identity.module)
    parts = identity.export.split(".")
    for part in parts[:-1]:
        owner = getattr(owner, part)
    original = getattr(owner, parts[-1])
    if identity.kind == "class":
        replacement = type(
            original.__name__,
            (original,),
            {"__module__": original.__module__},
        )
    else:

        def replacement(*args: object, **kwargs: object) -> object:
            return original(*args, **kwargs)

        replacement.__module__ = original.__module__
        replacement.__qualname__ = original.__qualname__
    monkeypatch.setattr(owner, parts[-1], replacement)

    with pytest.raises(ExperimentBaselineAuthorityError):
        verify_experiment_baseline_production_authority_v1()

    assert tuple(tmp_path.iterdir()) == ()


def test_exact_code_clone_and_public_alias_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = baseline_module.PlatformStore.structured_search
    clone = types.FunctionType(
        original.__code__,
        original.__globals__,
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    clone.__kwdefaults__ = original.__kwdefaults__
    clone.__module__ = original.__module__
    clone.__qualname__ = original.__qualname__
    monkeypatch.setattr(baseline_module.PlatformStore, "structured_search", clone)
    with pytest.raises(ExperimentBaselineAuthorityError):
        verify_experiment_baseline_production_authority_v1()

    monkeypatch.undo()
    original_adapter = baseline_module.MLflowAdapter

    class Alias(original_adapter):
        pass

    monkeypatch.setattr(
        baseline_module.experiment_adapters_public,
        "MLflowAdapter",
        Alias,
    )
    with pytest.raises(ExperimentBaselineAuthorityError):
        verify_experiment_baseline_production_authority_v1()


@pytest.mark.parametrize(
    ("owner", "method_name"),
    [
        (baseline_module.MLflowAdapter, "_load_files"),
        (baseline_module.ExperimentService, "sync_mlflow"),
        (baseline_module.ExperimentService, "create_experiment"),
        (baseline_module.ExperimentService, "create_run"),
        (baseline_module.ExperimentService, "compare"),
        (baseline_module.PlatformStore, "structured_search"),
    ],
)
def test_after_import_same_code_different_globals_fails(
    owner: type[object],
    method_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = getattr(owner, method_name)
    alternate_globals = dict(original.__globals__)
    alternate_globals["len"] = lambda value: 0
    alternate_globals["uuid4"] = lambda: "forged"
    clone = types.FunctionType(
        original.__code__,
        alternate_globals,
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    clone.__kwdefaults__ = original.__kwdefaults__
    clone.__module__ = original.__module__
    clone.__qualname__ = original.__qualname__
    monkeypatch.setattr(owner, method_name, clone)

    with pytest.raises(ExperimentBaselineAuthorityError):
        verify_experiment_baseline_production_authority_v1()

    assert tuple(tmp_path.iterdir()) == ()


def test_referenced_uuid4_helper_replacement_fails_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        baseline_module.experiment_service_public,
        "uuid4",
        lambda: "forged",
    )

    with pytest.raises(ExperimentBaselineAuthorityError):
        verify_experiment_baseline_production_authority_v1()

    assert tuple(tmp_path.iterdir()) == ()


def test_referenced_len_global_shadow_fails_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        baseline_module.platform_store_public,
        "len",
        builtins.len,
        raising=False,
    )

    with pytest.raises(ExperimentBaselineAuthorityError):
        verify_experiment_baseline_production_authority_v1()

    assert tuple(tmp_path.iterdir()) == ()


def test_same_code_canonical_globals_with_copied_builtins_fails_before_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = baseline_module.ExperimentService.compare
    globals_mapping = original.__globals__
    monkeypatch.setitem(globals_mapping, "__builtins__", dict(builtins.__dict__))
    clone = types.FunctionType(
        original.__code__,
        globals_mapping,
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    clone.__kwdefaults__ = original.__kwdefaults__
    clone.__module__ = original.__module__
    clone.__qualname__ = original.__qualname__
    monkeypatch.setattr(baseline_module.ExperimentService, "compare", clone)

    with pytest.raises(ExperimentBaselineAuthorityError, match="canonical builtins"):
        verify_experiment_baseline_production_authority_v1()

    assert tuple(tmp_path.iterdir()) == ()


def test_preimport_platform_wrapper_fails_in_fresh_process_without_io(
    tmp_path: Path,
) -> None:
    target = tmp_path / "fresh"
    target.mkdir()
    script = textwrap.dedent(
        """
        import pathlib
        import sys
        import evidence_rag.platform.service as public

        original = public.PlatformService.search
        def replacement(self, request, **kwargs):
            return original(self, request, **kwargs)
        replacement.__module__ = original.__module__
        replacement.__qualname__ = original.__qualname__
        public.PlatformService.search = replacement

        from evidence_rag.rag.sources.experiment.baseline_v1 import (
            ExperimentBaselineAuthorityError,
            verify_experiment_baseline_production_authority_v1,
        )
        root = pathlib.Path(sys.argv[1])
        try:
            verify_experiment_baseline_production_authority_v1()
        except ExperimentBaselineAuthorityError:
            if list(root.iterdir()):
                raise SystemExit("authority rejection created I/O")
        else:
            raise SystemExit("preimport wrapper accepted")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(target)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert tuple(target.iterdir()) == ()


@pytest.mark.parametrize(
    ("module_name", "class_name", "method_name", "class_replacement"),
    [
        (
            "evidence_rag.experiments.adapters.mlflow",
            "MLflowAdapter",
            "_load_files",
            False,
        ),
        (
            "evidence_rag.experiments.service",
            "ExperimentService",
            "compare",
            False,
        ),
        (
            "evidence_rag.experiments.service",
            "ExperimentService",
            "sync_mlflow",
            False,
        ),
        (
            "evidence_rag.experiments.service",
            "ExperimentService",
            "create_experiment",
            False,
        ),
        (
            "evidence_rag.experiments.service",
            "ExperimentService",
            "create_run",
            False,
        ),
        (
            "evidence_rag.experiments.models",
            "ExperimentCreate",
            "",
            True,
        ),
        (
            "evidence_rag.experiments.models",
            "RunCreate",
            "",
            True,
        ),
        (
            "evidence_rag.platform.store",
            "PlatformStore",
            "structured_search",
            False,
        ),
    ],
)
def test_fresh_preimport_different_globals_and_foundation7_fail_before_io(
    module_name: str,
    class_name: str,
    method_name: str,
    class_replacement: bool,
    tmp_path: Path,
) -> None:
    target = tmp_path / f"fresh-{class_name}-{method_name or 'class'}"
    target.mkdir()
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sqlite3
        import sys
        import tempfile
        import types

        module_name, class_name, method_name, class_attack, root_text = sys.argv[1:]
        module = importlib.import_module(module_name)
        owner = getattr(module, class_name)
        if class_attack == "true":
            replacement = type(
                owner.__name__,
                (owner,),
                {
                    "__module__": owner.__module__,
                    "__qualname__": owner.__qualname__,
                },
            )
            setattr(module, class_name, replacement)
        else:
            original = getattr(owner, method_name)
            alternate_globals = dict(original.__globals__)
            alternate_globals["len"] = lambda value: 0
            alternate_globals["uuid4"] = lambda: "forged"
            replacement = types.FunctionType(
                original.__code__,
                alternate_globals,
                original.__name__,
                original.__defaults__,
                original.__closure__,
            )
            replacement.__kwdefaults__ = original.__kwdefaults__
            replacement.__module__ = original.__module__
            replacement.__qualname__ = original.__qualname__
            setattr(owner, method_name, replacement)

        calls = {"sqlite": 0, "temp": 0}
        def sqlite_bomb(*args, **kwargs):
            calls["sqlite"] += 1
            raise AssertionError("SQLite reached")
        def temp_bomb(*args, **kwargs):
            calls["temp"] += 1
            raise AssertionError("temp root reached")
        sqlite3.connect = sqlite_bomb
        tempfile.mkdtemp = temp_bomb

        from evidence_rag.rag.sources.experiment.baseline_v1 import (
            ExperimentBaselineAuthorityError,
            verify_experiment_baseline_production_authority_v1,
        )

        root = pathlib.Path(root_text)
        try:
            verify_experiment_baseline_production_authority_v1()
        except ExperimentBaselineAuthorityError:
            if calls != {"sqlite": 0, "temp": 0}:
                raise SystemExit(f"I/O bomb reached: {calls}")
            if list(root.iterdir()):
                raise SystemExit("authority rejection created I/O")
        else:
            raise SystemExit("preimport replacement accepted")
        """
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            module_name,
            class_name,
            method_name,
            str(class_replacement).lower(),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert tuple(target.iterdir()) == ()


def test_canonical_harness_pass_is_readiness_only_without_path_io(
    tmp_path: Path,
) -> None:
    harness = load_experiment_harness_gate_v1(HARNESS_GATE)
    foundation = load_experiment_foundation_gate_v1(FOUNDATION_GATE)
    production = fixed_experiment_baseline_production_authority_v1()

    assert harness.authorized is True
    assert harness.foundation_authority == foundation.authority
    assert harness.production_component_hash == production.component_set_digest

    assert tuple(tmp_path.iterdir()) == ()


def test_artifact_layout_and_full_path_admission_are_read_only(
    tmp_path: Path,
) -> None:
    contract = admit_experiment_baseline_artifact_layout(EXPERIMENT_BASELINE_ARTIFACT_FILES)
    tracking = tmp_path / "tracking"
    raw_database = tmp_path / "raw-runtime" / "raw.sqlite3"
    manual_database = tmp_path / "manual-runtime" / "manual.sqlite3"
    output = tmp_path / "artifact"
    admission = admit_experiment_baseline_run_paths(
        temp_root=tmp_path,
        mlflow_tracking_root=tracking,
        raw_database_path=raw_database,
        manual_database_path=manual_database,
        output_directory=output,
    )

    assert contract.files == EXPERIMENT_BASELINE_ARTIFACT_FILES
    assert contract.checksum_targets == contract.files[:-1]
    assert admission.creates_files is False
    assert admission.raw_database_path == str(raw_database)
    assert tuple(tmp_path.iterdir()) == ()
    with pytest.raises(ExperimentBaselineArtifactError):
        admit_experiment_baseline_artifact_layout(EXPERIMENT_BASELINE_ARTIFACT_FILES[:-1])


def test_artifact_manifest_binds_final_gate_preparation_config_and_security(
    tmp_path: Path,
) -> None:
    artifact = _build_valid_artifact(tmp_path)
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    security = json.loads((artifact / "security_report.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "experiment-e-b0-artifact-manifest-v2"
    assert manifest["harness_gate_task_id"] == EXPERIMENT_HARNESS_GATE_TASK_ID
    assert manifest["harness_gate_decision_digest"] == (
        "sha256:939130c475ae005f797ccded56e36998891a55ce6b5e2574cec9180b5bf3791e"
    )
    assert manifest["preparation_digest"] == (
        "sha256:5b407c7bd9e86cce4521aeacffbe6f5f2824b88ed6799792d38fb5dd59dd4179"
    )
    assert manifest["run_uri"] == (
        f"evaluation-run://project-experiment-eb0-v1/{manifest['run_id'].removeprefix('eb0-')}"
    )
    assert manifest["execution_config"]["sources"] == ["experiment"]
    assert manifest["execution_config"]["current_v1"] is True
    assert manifest["execution_config"]["experiment_v2"] is False
    assert manifest["execution_config"]["semantic"] is False
    assert manifest["execution_config"]["reranker"] is False
    assert manifest["execution_config"]["context_builder"] is False
    assert security["schema_version"] == "experiment-e-b0-security-report-v2"
    assert security["secret_leakage"] == 0
    assert security["acl_violations"] == 0
    assert security["cross_run_locator_violations"] == 0
    assert security["formal_database_access"] == 0
    assert security["network_access"] == 0


@pytest.mark.parametrize(
    ("database_name", "output_name"),
    [
        ("evidence-rag.sqlite3", "artifact"),
        ("raw.sqlite3-wal", "artifact"),
        ("raw.sqlite3-shm", "artifact"),
        ("raw.pyc", "artifact"),
        ("raw.sqlite3", "evals"),
        ("raw.sqlite3", "experiment"),
    ],
)
def test_path_admission_rejects_formal_sidecar_and_reserved_names(
    tmp_path: Path,
    database_name: str,
    output_name: str,
) -> None:
    with pytest.raises(ExperimentBaselinePathError):
        admit_experiment_baseline_paths(
            temp_root=tmp_path,
            database_path=tmp_path / database_name,
            output_directory=tmp_path / output_name,
        )


def test_path_admission_rejects_existing_escape_relative_overlap_and_symlink(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing.sqlite3"
    existing.touch()
    for database, output in (
        (existing, tmp_path / "artifact"),
        (tmp_path.parent / "escape.sqlite3", tmp_path / "artifact"),
        (Path("relative.sqlite3"), tmp_path / "artifact"),
        (tmp_path / "nested" / "raw.sqlite3", tmp_path / "nested"),
    ):
        with pytest.raises(ExperimentBaselinePathError):
            admit_experiment_baseline_paths(
                temp_root=tmp_path,
                database_path=database,
                output_directory=output,
            )
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(ExperimentBaselinePathError, match="symlink"):
        admit_experiment_baseline_paths(
            temp_root=tmp_path,
            database_path=linked / "raw.sqlite3",
            output_directory=tmp_path / "artifact",
        )


class _PathBomb:
    def __fspath__(self) -> str:
        raise AssertionError("path must not be inspected before Harness Gate")


@pytest.mark.parametrize(
    "gate_token",
    [
        None,
        "EXPERIMENT E0-01 FOUNDATION PASS",
        "E-B0 HARNESS PREPARATION AUTHORIZED",
        "forged-harness-pass",
    ],
)
def test_future_admission_rejects_before_path_bombs(
    gate_token: str | None,
) -> None:
    with pytest.raises(ExperimentBaselineNotAuthorized, match="NOT_AUTHORIZED"):
        run_experiment_baseline_v1(
            gate_token=gate_token,
            database_path=_PathBomb(),
            output_directory=_PathBomb(),
        )


def test_published_run_replaces_preparation_smoke_and_stays_non_qualified(
    tmp_path: Path,
) -> None:
    before = tuple(tmp_path.iterdir())
    result = verify_experiment_baseline_artifact_v1(PUBLISHED_RUN)
    assert tuple(tmp_path.iterdir()) == before == ()
    assert result.status == "VERIFIED_NON_QUALIFIED"
    assert result.portable is True
    assert result.files == EXPERIMENT_BASELINE_ARTIFACT_FILES
    assert not any(path.name in EXPERIMENT_BASELINE_ARTIFACT_FILES for path in tmp_path.rglob("*"))


def test_canonical_artifact_has_ten_files_and_portable_verify_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _build_valid_artifact(tmp_path)
    copied = tmp_path / "portable-copy"
    shutil.copytree(artifact, copied)

    def bomb(*_: object, **__: object) -> object:
        raise AssertionError("verify-only called production/SQLite")

    monkeypatch.setattr(baseline_module.MLflowAdapter, "load", bomb)
    monkeypatch.setattr(baseline_module.ExperimentService, "sync_mlflow", bomb)
    monkeypatch.setattr(baseline_module.ExperimentService, "compare", bomb)
    monkeypatch.setattr(baseline_module.PlatformService, "search", bomb)

    verification = verify_experiment_baseline_artifact_v1(copied)

    assert tuple(sorted(path.name for path in copied.iterdir())) == tuple(
        sorted(EXPERIMENT_BASELINE_ARTIFACT_FILES)
    )
    assert verification.status == "VERIFIED_NON_QUALIFIED"
    assert verification.case_count == 45
    assert verification.portable is True
    assert verification.service_calls == 0
    assert verification.database_calls == 0
    assert verification.network_calls == 0


@pytest.mark.parametrize("attack", ["delete", "extra", "symlink"])
def test_artifact_rejects_delete_extra_and_symlink(
    tmp_path: Path,
    attack: str,
) -> None:
    artifact = _build_valid_artifact(tmp_path)
    if attack == "delete":
        (artifact / "metrics.json").unlink()
    elif attack == "extra":
        (artifact / "extra.json").write_text("{}\n", encoding="utf-8")
    else:
        target = tmp_path / "outside-metrics.json"
        target.write_bytes((artifact / "metrics.json").read_bytes())
        (artifact / "metrics.json").unlink()
        (artifact / "metrics.json").symlink_to(target)

    with pytest.raises(ExperimentBaselineArtifactError):
        verify_experiment_baseline_artifact_v1(artifact)


@pytest.mark.parametrize(
    "extra_name",
    ["leak.sqlite3", "leak.sqlite3-wal", "leak.sqlite3-shm", "leak.pyc"],
)
def test_artifact_rejects_database_sidecar_compiled_extras(
    tmp_path: Path,
    extra_name: str,
) -> None:
    artifact = _build_valid_artifact(tmp_path)
    (artifact / extra_name).write_bytes(b"forbidden")

    with pytest.raises(ExperimentBaselineArtifactError):
        verify_experiment_baseline_artifact_v1(artifact)


@pytest.mark.parametrize(
    "attack",
    [
        "/Users/alice/private/project",
        "/workspace/private/project",
        "owner@example.com",
        "sk-proj-0123456789abcdefghijkl",
        "4111 1111 1111 1111",
        "payment card 4111 1111 1111 1111",
        "cache.sqlite3-wal",
        "relative.db",
        "../outside",
    ],
)
def test_artifact_rejects_resigned_path_secret_email_payment_and_db_text(
    tmp_path: Path,
    attack: str,
) -> None:
    artifact = _build_valid_artifact(tmp_path)
    path = artifact / "predictions.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["candidates"][0]["raw_structured_fields"]["snippet"] = attack
    _rewrite_jsonl(path, rows)
    _resign(artifact, path.name)

    with pytest.raises(ExperimentBaselineArtifactError):
        verify_experiment_baseline_artifact_v1(artifact)


def test_artifact_rejects_resigned_nonfinite_json(
    tmp_path: Path,
) -> None:
    artifact = _build_valid_artifact(tmp_path)
    path = artifact / "latency.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cases"][0]["latency_ms"] = float("nan")
    _rewrite_json(path, payload)
    _resign(artifact, path.name)

    with pytest.raises(ExperimentBaselineArtifactError, match="non-finite"):
        verify_experiment_baseline_artifact_v1(artifact)


@pytest.mark.parametrize(
    "attack",
    [
        "golden_truth",
        "prediction_member",
        "comparison_identity",
        "latency_count",
        "error_ledger",
        "denominator",
        "component_identity",
        "run_identity",
    ],
)
def test_artifact_rejects_resigned_cross_file_member_and_denominator_attacks(
    tmp_path: Path,
    attack: str,
) -> None:
    artifact = _build_valid_artifact(tmp_path)
    if attack == "golden_truth":
        path = artifact / "golden_cases.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["question"] = "Tampered released truth."
        _rewrite_jsonl(path, rows)
    elif attack == "prediction_member":
        path = artifact / "predictions.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows.pop()
        _rewrite_jsonl(path, rows)
    elif attack == "comparison_identity":
        path = artifact / "comparison_results.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["case_id"] = "experiment-v1-002"
        _rewrite_jsonl(path, rows)
    elif attack == "latency_count":
        path = artifact / "latency.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["cases"][0]["candidate_count"] += 1
        _rewrite_json(path, payload)
    elif attack == "error_ledger":
        path = artifact / "errors.jsonl"
        _rewrite_jsonl(
            path,
            [
                {
                    "schema_version": "experiment-e-b0-error-record-v1",
                    "case_id": "experiment-v1-001",
                    "error_reason": "system_error",
                    "error": "forged",
                }
            ],
        )
    elif attack == "denominator":
        path = artifact / "metrics.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["overall"]["metrics"][0]["denominator"] += 1
        _rewrite_json(path, payload)
    elif attack == "component_identity":
        path = artifact / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["production_authority"]["components"][-1]["version"] = "forged-version"
        _rewrite_json(path, payload)
    else:
        path = artifact / "manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["run_id"] = "eb0-00000000000000000000000000000000"
        _rewrite_json(path, payload)
    _resign(artifact, path.name)

    with pytest.raises(ExperimentBaselineArtifactError):
        verify_experiment_baseline_artifact_v1(artifact)


def test_published_experiment_tree_and_package_exports_are_exact() -> None:
    import evidence_rag.rag.sources.experiment as experiment_package

    runs = REPOSITORY / "evals" / "experiment" / "runs"
    assert tuple(path.name for path in runs.iterdir() if path.is_dir()) == (
        "085799235df74bcdc4fc56c0c17f36e7",
    )
    assert tuple(sorted(path.name for path in PUBLISHED_RUN.iterdir())) == tuple(
        sorted(EXPERIMENT_BASELINE_ARTIFACT_FILES)
    )
    assert experiment_package.prepare_experiment_baseline_v1 is prepare_experiment_baseline_v1
    assert (
        experiment_package.verify_experiment_baseline_artifact_v1
        is verify_experiment_baseline_artifact_v1
    )
    assert load_experiment_foundation_gate_v1().conclusion == "EXPERIMENT E0-01 FOUNDATION PASS"
