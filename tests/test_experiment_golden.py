from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.experiment.evaluation_v1 import (
    EXPECTED_SLICE_COUNTS,
    EXPERIMENT_GOLDEN_AUTHORITY_HASH,
    EXPERIMENT_GOLDEN_CASE_COUNT,
    EXPERIMENT_GOLDEN_CASES,
    EXPERIMENT_GOLDEN_PACKAGE_HASH,
    EXPERIMENT_GOLDEN_RECIPE,
    EntityKind,
    ExpectedEntity,
    ExperimentEvaluationError,
    ExperimentGoldenCase,
    _package_identity,
    build_experiment_golden_v1,
    load_experiment_golden_v1,
    verify_experiment_golden_v1,
)
from evidence_rag.rag.sources.experiment.fixture_v1 import ExperimentFixtureRecipe


def _copy_package(tmp_path: Path) -> Path:
    source = Path(__file__).parent / "fixtures" / "experiment_golden"
    target = tmp_path / "experiment-golden"
    shutil.copytree(source, target)
    return target


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode()


def _digest(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def _resign_recipe_mutation(root: Path, recipe_payload: dict[str, object]) -> None:
    recipe_canonical = _canonical(recipe_payload)
    recipe_bytes = recipe_canonical + b"\n"
    recipe_path = root / EXPERIMENT_GOLDEN_RECIPE
    recipe_path.write_bytes(recipe_bytes)
    cases_bytes = (root / EXPERIMENT_GOLDEN_CASES).read_bytes()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    recipe_file = next(
        item for item in manifest["files"] if item["path"] == EXPERIMENT_GOLDEN_RECIPE
    )
    recipe_file["sha256"] = _digest(recipe_bytes)
    recipe_file["size"] = len(recipe_bytes)
    manifest["file_digests"][EXPERIMENT_GOLDEN_RECIPE] = _digest(recipe_bytes)
    manifest["fixture_recipe_digest"] = _digest(recipe_canonical)
    package_hash = _package_identity(
        manifest,
        {
            EXPERIMENT_GOLDEN_CASES: cases_bytes,
            EXPERIMENT_GOLDEN_RECIPE: recipe_bytes,
        },
    )
    manifest["package_hash"] = package_hash
    manifest["release_record_id"] = f"release-{package_hash.removeprefix('sha256:')[:32]}"
    manifest_path.write_bytes(_canonical(manifest) + b"\n")


def test_released_experiment_golden_has_exact_frozen_membership() -> None:
    dataset = load_experiment_golden_v1()

    assert dataset.dataset_id == "experiment-golden-v1"
    assert dataset.dataset_version == "experiment-golden-v1"
    assert dataset.package_hash == EXPERIMENT_GOLDEN_PACKAGE_HASH
    assert dataset.authority_hash == EXPERIMENT_GOLDEN_AUTHORITY_HASH
    assert len(dataset.cases) == EXPERIMENT_GOLDEN_CASE_COUNT
    assert dataset.case_membership == tuple(
        f"experiment-v1-{ordinal:03d}" for ordinal in range(1, 46)
    )
    assert Counter(case.primary_slice for case in dataset.cases) == Counter(EXPECTED_SLICE_COUNTS)
    assert all(case.eligible_metrics for case in dataset.cases)
    assert all(case.hard_negatives for case in dataset.cases)
    assert all(
        case.forbidden_candidate_ids == tuple(item.entity.entity_id for item in case.hard_negatives)
        for case in dataset.cases
    )
    authority = dataset.authority_entities
    assert len(authority) == 83
    assert Counter(entity.kind for entity in authority.values()) == Counter(
        {
            EntityKind.EXPERIMENT: 1,
            EntityKind.RUN: 12,
            EntityKind.METRIC_DEFINITION: 3,
            EntityKind.METRIC_SERIES: 18,
            EntityKind.METRIC_OBSERVATION: 47,
            EntityKind.ARTIFACT: 2,
        }
    )
    assert all(
        truth.definition_id in authority
        and (
            truth.missing
            or (
                truth.series_id in authority
                and all(entity_id in authority for entity_id in truth.observation_ids)
            )
        )
        for case in dataset.cases
        for truth in case.metric_truth
    )


def test_rebuild_is_byte_stable_and_portable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_manifest = build_experiment_golden_v1(first)
    second_manifest = build_experiment_golden_v1(second)

    assert first_manifest == second_manifest
    assert first_manifest.package_hash == EXPERIMENT_GOLDEN_PACKAGE_HASH
    for name in ("manifest.json", "cases.jsonl", "fixture_recipe.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()

    relocated = tmp_path / "relocated" / "portable-copy"
    shutil.copytree(first, relocated)
    verified = verify_experiment_golden_v1(relocated)
    assert verified.package_hash == EXPERIMENT_GOLDEN_PACKAGE_HASH


@pytest.mark.parametrize(
    "mutation",
    [
        "tamper",
        "extra",
        "missing",
        "secret",
        "posix_absolute",
        "encoded_absolute",
        "windows_absolute",
        "unc_absolute",
        "nan",
    ],
)
def test_loader_fails_closed_for_package_mutations(tmp_path: Path, mutation: str) -> None:
    root = _copy_package(tmp_path)
    cases = root / "cases.jsonl"
    if mutation == "tamper":
        cases.write_bytes(cases.read_bytes() + b" ")
    elif mutation == "extra":
        (root / "undeclared.json").write_text("{}\n", encoding="utf-8")
    elif mutation == "missing":
        (root / "fixture_recipe.json").unlink()
    else:
        rows = cases.read_text(encoding="utf-8").splitlines()
        payload = json.loads(rows[0])
        payload["question"] = {
            "secret": "SECRET_SENTINEL",
            "posix_absolute": "/tmp/experiment-secret",
            "encoded_absolute": "%2Ftmp%2Fexperiment-secret",
            "windows_absolute": r"C:\Temp\experiment-secret",
            "unc_absolute": r"\\server\share\experiment-secret",
            "nan": "NaN",
        }[mutation]
        if mutation == "nan":
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).replace(
                '"NaN"', "NaN"
            )
        else:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        rows[0] = encoded
        cases.write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(ExperimentEvaluationError):
        load_experiment_golden_v1(root)


def test_loader_rejects_symlink_file_component(tmp_path: Path) -> None:
    root = _copy_package(tmp_path)
    recipe = root / "fixture_recipe.json"
    external = tmp_path / "outside.json"
    external.write_bytes(recipe.read_bytes())
    recipe.unlink()
    os.symlink(external, recipe)

    with pytest.raises(ExperimentEvaluationError, match="symlink"):
        load_experiment_golden_v1(root)


def test_strict_models_reject_cross_run_cross_experiment_and_acl_leakage() -> None:
    dataset = load_experiment_golden_v1()
    entity = dataset.cases[1].expected_entities[0]

    with pytest.raises(ValidationError):
        ExpectedEntity.model_validate(
            {
                **entity.model_dump(mode="json"),
                "locator": ("experiment://fixture/experiment-v1-main/runs/treatment-seed-22"),
            }
        )
    with pytest.raises(ValidationError):
        ExpectedEntity.model_validate(
            {
                **entity.model_dump(mode="json"),
                "experiment_id": "experiment://fixture/other",
            }
        )
    with pytest.raises(ValidationError):
        ExperimentGoldenCase.model_validate(
            {
                **dataset.cases[1].model_dump(mode="json"),
                "acl_ref": "project:other",
            }
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "cross_run",
        "cross_series",
        "cross_definition",
        "step",
        "time",
        "order",
        "locator",
    ],
)
def test_loader_rejects_resigned_metric_observation_identity_attacks(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = _copy_package(tmp_path)
    recipe = json.loads((root / EXPERIMENT_GOLDEN_RECIPE).read_bytes())
    series = recipe["runs"][0]["metrics"][0]
    point = series["points"][0]
    if mutation == "cross_run":
        point["run_id"] = recipe["runs"][1]["logical_run_id"]
    elif mutation == "cross_series":
        point["series_id"] = recipe["runs"][0]["metrics"][1]["series_id"]
    elif mutation == "cross_definition":
        point["definition_id"] = recipe["metric_definitions"][1]["definition_id"]
    elif mutation == "step":
        point["step"] = 2
    elif mutation == "time":
        point["observed_at_ms"] = series["points"][1]["observed_at_ms"]
    elif mutation == "order":
        series["points"] = list(reversed(series["points"]))
    else:
        point["locator"] = point["locator"].replace(
            "/observations/step/1",
            "/observations/step/2",
        )
    _resign_recipe_mutation(root, recipe)

    with pytest.raises(ExperimentEvaluationError, match="invalid fixture recipe"):
        load_experiment_golden_v1(root)


def test_recipe_contract_rejects_naked_cross_definition_series() -> None:
    dataset = load_experiment_golden_v1()
    payload = dataset.recipe.model_dump(mode="json")
    payload["runs"][0]["metrics"][0]["definition_id"] = payload["metric_definitions"][0][
        "definition_id"
    ]

    with pytest.raises(ValidationError):
        ExperimentFixtureRecipe.model_validate(payload)
