from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import yaml

from evidence_rag.config import Settings
from evidence_rag.evaluation.models import (
    CodeEvaluationCaseProfile,
    CodeEvaluationRunRequest,
    EvaluationCandidateJudgment,
    EvaluationCaseCreate,
)
from evidence_rag.models import RepositoryIngestRequest
from evidence_rag.parser import CodeParser
from evidence_rag.runtime import create_runtime

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = REPOSITORY_ROOT / "evals" / "code"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "code_golden"
MANIFEST_PATH = EVAL_ROOT / "code-golden-v1.manifest.json"
CASES_PATH = EVAL_ROOT / "code-golden-v1.cases.yaml"
CONTRACT_PATH = EVAL_ROOT / "annotation-contract-v1.schema.json"
FIXTURE_MANIFEST_PATH = FIXTURE_ROOT / "fixture_manifest.json"
V2_MANIFEST_PATH = EVAL_ROOT / "code-golden-v2.manifest.json"
V2_CASES_PATH = EVAL_ROOT / "code-golden-v2.cases.yaml"
V2_SOURCE_MANIFEST_PATH = FIXTURE_ROOT / "v2" / "source-manifest.json"
V2_ADAPTER_PATH = EVAL_ROOT / "code_golden_v2.py"
V2_MATERIALIZER_PATH = FIXTURE_ROOT / "v2" / "materializer.py"

EXPECTED_TASK_COUNTS = {
    "exact_location": 7,
    "implementation": 7,
    "call_reference_path": 7,
    "bug_localization": 7,
    "impact_analysis": 7,
    "change_context": 5,
    "historical_version": 5,
    "test_validation": 5,
}
REQUIRED_SLICES = {
    "chinese",
    "english",
    "natural_language_to_code",
    "identifier_heavy",
    "low_lexical_overlap",
    "same_name_hard_negative",
    "wrong_version",
    "generated_vendor",
    "unanswerable",
    "acl_forbidden",
    "dirty_current",
    "graph_required",
    "graph_harmful",
}


def _load_module(name: str, path: Path) -> Any:
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _v2_adapter() -> Any:
    return _load_module("code_golden_v2_adapter", V2_ADAPTER_PATH)


def _v2_materializer() -> Any:
    return _load_module("code_golden_v2_materializer", V2_MATERIALIZER_PATH)


def _v2_dataset() -> dict[str, Any]:
    loaded = yaml.safe_load(V2_CASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset() -> dict[str, Any]:
    loaded = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_manifest_versions_files_and_hashes_are_pinned() -> None:
    manifest = _json(MANIFEST_PATH)
    fixture = _json(FIXTURE_MANIFEST_PATH)

    assert manifest["dataset_version"] == "code-golden-v1"
    assert manifest["immutable"] is True
    assert manifest["status"] == "annotated"
    assert "new dataset version" in manifest["revision_policy"]
    assert manifest["sha256"] == {
        "cases": _sha256(CASES_PATH),
        "annotation_contract": _sha256(CONTRACT_PATH),
        "fixture_manifest": _sha256(FIXTURE_MANIFEST_PATH),
    }
    assert fixture["schema_version"] == "code-golden-fixture-manifest-v1"
    assert fixture["content_digests"] == {
        "repository_sha256": _tree_digest(FIXTURE_ROOT / fixture["repository_root"]),
        "history_sha256": _tree_digest(FIXTURE_ROOT / fixture["history_root"]),
    }


def test_case_distribution_smoke_subset_and_required_slices() -> None:
    manifest = _json(MANIFEST_PATH)
    cases = _dataset()["cases"]
    case_ids = [case["case_id"] for case in cases]
    smoke_ids = [case["case_id"] for case in cases if case["smoke"]]
    tasks = Counter(case["task"] for case in cases)
    slices = Counter(slice_name for case in cases for slice_name in case["slices"])

    assert len(cases) == 50
    assert len(case_ids) == len(set(case_ids))
    assert case_ids == [f"code-golden-v1-{index:03d}" for index in range(1, 51)]
    assert tasks == EXPECTED_TASK_COUNTS
    assert manifest["task_distribution"] == EXPECTED_TASK_COUNTS
    assert len(smoke_ids) == 15
    assert smoke_ids == manifest["smoke_case_ids"]
    assert set(manifest["required_slices"]) == REQUIRED_SLICES
    assert set(slices) >= REQUIRED_SLICES
    assert manifest["slice_counts"] == {
        slice_name: slices[slice_name] for slice_name in manifest["required_slices"]
    }


def test_every_case_matches_the_annotation_contract_shape() -> None:
    contract = _json(CONTRACT_PATH)
    cases = _dataset()["cases"]
    required = set(contract["required"])
    allowed = set(contract["properties"])
    task_values = set(contract["properties"]["task"]["enum"])
    language_values = set(contract["properties"]["query_language"]["enum"])
    answer_modes = set(contract["properties"]["expected_answer_mode"]["enum"])
    answerability_values = set(contract["properties"]["answerability"]["enum"])
    review_values = set(contract["properties"]["review"]["properties"]["status"]["enum"])
    case_id_pattern = re.compile(contract["properties"]["case_id"]["pattern"])
    unit_id_pattern = re.compile(contract["properties"]["expected_unit_ids"]["items"]["pattern"])

    assert contract["additionalProperties"] is False
    for case in cases:
        assert set(case) == required
        assert set(case) <= allowed
        assert case_id_pattern.fullmatch(case["case_id"])
        assert case["dataset_version"] == "code-golden-v1"
        assert case["task"] in task_values
        assert case["query"].strip()
        assert case["query_language"] in language_values
        assert case["code_languages"]
        assert len(case["code_languages"]) == len(set(case["code_languages"]))
        assert case["expected_answer_mode"] in answer_modes
        assert case["answerability"] in answerability_values
        assert case["review"]["status"] in review_values
        assert set(case["query_profile"]) == {
            "style",
            "repository_id",
            "scope_ref",
            "acl_refs",
            "graph_requirement",
        }
        assert case["query_profile"]["repository_id"] == ("repo://fixture/code-golden-shop")
        assert case["query_profile"]["scope_ref"]
        assert case["expected_ref"]
        assert case["query_profile"]["acl_refs"]
        assert len(case["slices"]) == len(set(case["slices"]))
        assert all(unit_id_pattern.fullmatch(unit_id) for unit_id in case["expected_unit_ids"])


def test_entities_units_locators_and_candidate_judgments_resolve() -> None:
    fixture = _json(FIXTURE_MANIFEST_PATH)
    cases = _dataset()["cases"]
    entities = {entity["id"]: entity for entity in fixture["entity_catalog"]}
    units = {unit["id"]: unit for unit in fixture["unit_catalog"]}
    all_grades: set[int] = set()
    hard_negative_count = 0

    assert len(entities) == len(fixture["entity_catalog"])
    assert len(units) == len(fixture["unit_catalog"])
    assert all(unit["entity_id"] in entities for unit in units.values())

    for case in cases:
        assert set(case["expected_entity_ids"]) <= set(entities)
        assert set(case["expected_unit_ids"]) <= set(units)
        assert set(case["forbidden_entity_ids"]) <= set(entities)
        expected_types = {
            entities[entity_id]["entity_type"] for entity_id in case["expected_entity_ids"]
        }
        assert expected_types <= set(case["expected_entity_types"])
        if case["answerability"] == "answerable":
            assert case["expected_entity_ids"]
            assert case["expected_unit_ids"]
            assert case["expected_answer_mode"] == "direct"
        for unit_id in case["expected_unit_ids"]:
            assert units[unit_id]["entity_id"] in case["expected_entity_ids"]

        for locator in case["expected_locators"]:
            entity = entities[locator["entity_id"]]
            assert locator["entity_id"] in case["expected_entity_ids"]
            assert locator["path"] == entity["path"]
            if "start_line" in locator:
                assert locator["start_line"] == entity["locator"]["start_line"]
                assert locator["end_line"] == entity["locator"]["end_line"]
                assert locator["start_line"] <= locator["end_line"]

        assert case["candidate_judgments"]
        for judgment in case["candidate_judgments"]:
            assert judgment["entity_id"] in entities
            unit_id = judgment["retrieval_unit_id"]
            if unit_id is not None:
                assert unit_id in units
                assert units[unit_id]["entity_id"] == judgment["entity_id"]
            assert judgment["relevance_grade"] in {-1, 0, 1, 2}
            assert judgment["note"].strip()
            all_grades.add(judgment["relevance_grade"])
            hard_negative_count += judgment["relevance_grade"] == -1

    manifest = _json(MANIFEST_PATH)
    assert all_grades == {-1, 0, 1, 2}
    assert hard_negative_count == manifest["counts"]["hard_negative_judgments"]
    assert hard_negative_count >= 15


def test_required_paths_match_catalog_edges_with_direction() -> None:
    fixture = _json(FIXTURE_MANIFEST_PATH)
    cases = _dataset()["cases"]
    catalog_edges = {
        (edge["source"], edge["edge_type"], edge["target"]) for edge in fixture["edge_catalog"]
    }

    for case in cases:
        observed_types: set[str] = set()
        for path in case["required_paths"]:
            assert len(path["nodes"]) == len(path["edges"]) + 1
            for index, edge in enumerate(path["edges"]):
                assert edge["direction"] in {"outgoing", "incoming"}
                observed_types.add(edge["edge_type"])
                if edge["direction"] == "outgoing":
                    assert edge["source"] == path["nodes"][index]
                    assert edge["target"] == path["nodes"][index + 1]
                    catalog_key = (
                        edge["source"],
                        edge["edge_type"],
                        edge["target"],
                    )
                else:
                    assert edge["target"] == path["nodes"][index]
                    assert edge["source"] == path["nodes"][index + 1]
                    catalog_key = (
                        edge["source"],
                        edge["edge_type"],
                        edge["target"],
                    )
                assert catalog_key in catalog_edges
        assert observed_types == set(case["required_edge_types"])
        if "graph_required" in case["slices"]:
            assert case["required_paths"]
            assert case["query_profile"]["graph_requirement"] == "required"
        if "graph_harmful" in case["slices"]:
            assert case["query_profile"]["graph_requirement"] == "harmful_if_expanded"


def test_unanswerable_acl_review_and_disagreement_guards() -> None:
    manifest = _json(MANIFEST_PATH)
    cases = _dataset()["cases"]
    non_answerable = [
        case
        for case in cases
        if case["answerability"] in {"unanswerable", "ambiguous", "forbidden"}
    ]
    delayed_or_second = [
        case
        for case in cases
        if case["review"]["status"] in {"delayed_reviewed", "second_reviewed"}
    ]
    declared_disagreements = {item["id"] for item in manifest["review"]["disagreements"]}

    assert len(non_answerable) >= 5
    assert len(non_answerable) == (manifest["counts"]["unanswerable_ambiguous_or_acl_cases"])
    assert all(case["expected_answer_mode"] != "direct" for case in non_answerable)
    assert all(
        not case["expected_entity_ids"] for case in cases if case["answerability"] == "forbidden"
    )
    assert all(case["forbidden_entity_ids"] for case in cases if "acl_forbidden" in case["slices"])
    assert len(delayed_or_second) >= 10
    assert len(delayed_or_second) == (manifest["counts"]["delayed_or_second_reviewed_cases"])
    assert (
        len(delayed_or_second) / len(cases)
        >= manifest["annotation_policy"]["minimum_review_fraction"]
    )
    assert {
        case["review"]["disagreement_id"]
        for case in cases
        if case["review"]["disagreement_id"] is not None
    } == declared_disagreements


def test_fixture_is_multilingual_historical_and_not_pytest_collectable() -> None:
    fixture = _json(FIXTURE_MANIFEST_PATH)
    repository_root = FIXTURE_ROOT / fixture["repository_root"]

    assert fixture["dirty"] is True
    assert len(fixture["commits"]) >= 3
    assert "refs/heads/release-v1" in fixture["refs"]
    assert "refs/tags/v1.0.0" in fixture["refs"]
    assert fixture["generated_roots"] == ["generated"]
    assert fixture["vendor_roots"] == ["vendor"]
    assert {item["status"] for item in fixture["validation_artifacts"]} == {
        "failed",
        "passed",
    }
    assert (repository_root / "src" / "checkout" / "pricing.py").is_file()
    assert (repository_root / "web" / "checkout.ts").is_file()
    assert (repository_root / "vendor" / "payment_sdk.js").is_file()
    assert (
        FIXTURE_ROOT / "history" / "commits" / "1111111111111111111111111111111111111111"
    ).is_dir()
    assert (
        FIXTURE_ROOT / "history" / "commits" / "3333333333333333333333333333333333333333"
    ).is_dir()

    collectable_python = [
        path
        for path in repository_root.rglob("*.py")
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    ]
    assert collectable_python == []
    pricing_cases = (repository_root / "tests" / "pricing_cases.py").read_text(encoding="utf-8")
    assert "def test_gold_customer_discount" in pricing_cases
    assert "def test_rejects_unknown_currency" in pricing_cases

    pricing = (repository_root / "src" / "checkout" / "pricing.py").read_text(encoding="utf-8")
    gateway = (repository_root / "src" / "payments" / "gateway.py").read_text(encoding="utf-8")
    checkout_ts = (repository_root / "web" / "checkout.ts").read_text(encoding="utf-8")
    assert "def format_line" in pricing
    assert gateway.count("def charge(") == 3
    assert "formatMoney as displayMoney" in checkout_ts


def test_c0_01_field_mapping_is_complete_without_baseline_results() -> None:
    manifest = _json(MANIFEST_PATH)
    integration = manifest["c0_01_integration"]

    assert set(integration) == {
        "evaluation_cases",
        "evaluation_case_profiles",
        "evaluation_candidate_judgments",
        "required_path_extension",
    }
    assert set(integration["evaluation_case_profiles"]) >= {
        "source_domain",
        "task",
        "query_profile_json",
        "expected_unit_ids_json",
        "expected_entity_types_json",
        "expected_locators_json",
        "required_edge_types_json",
        "expected_context_roles_json",
        "expected_ref",
        "expected_answer_mode",
    }
    assert set(integration["evaluation_candidate_judgments"]) == {
        "case_id",
        "entity_id",
        "retrieval_unit_id",
        "relevance_grade",
        "necessity_role",
        "note",
    }
    serialized = json.dumps(manifest, sort_keys=True).lower()
    assert "no baseline or treatment result is included" in serialized
    assert "pass_rate" not in serialized
    assert "latency_ms" not in serialized


def test_every_case_adapts_to_the_live_c0_01_models() -> None:
    cases = _dataset()["cases"]

    for case in cases:
        query_profile = case["query_profile"]
        code_profile = CodeEvaluationCaseProfile(
            task=case["task"],
            query_profile={
                "repository_ids": [query_profile["repository_id"]],
                "commit": query_profile["scope_ref"],
                "languages": [
                    language for language in case["code_languages"] if language != "text"
                ],
                "style": query_profile["style"],
                "acl_refs": query_profile["acl_refs"],
                "graph_requirement": query_profile["graph_requirement"],
            },
            expected_unit_ids=case["expected_unit_ids"],
            expected_entity_types=case["expected_entity_types"],
            expected_locators=case["expected_locators"],
            required_edge_types=case["required_edge_types"],
            expected_context_roles=case["expected_context_roles"],
            expected_ref=case["expected_ref"],
            expected_answer_mode=case["expected_answer_mode"],
        )
        request = EvaluationCaseCreate(
            project_id="project-code-golden",
            name=case["case_id"],
            question=case["query"],
            expected_sources=["code"],
            expected_entity_ids=case["expected_entity_ids"],
            expected_paths=[required_path["nodes"] for required_path in case["required_paths"]],
            expected_commit_ids=[
                entity_id
                for entity_id in case["expected_entity_ids"]
                if entity_id.startswith("git://")
            ],
            forbidden_entity_ids=case["forbidden_entity_ids"],
            required_version=case["expected_ref"],
            tags=[case["task"], *case["slices"]],
            code_profile=code_profile,
            candidate_judgments=[
                EvaluationCandidateJudgment(**judgment) for judgment in case["candidate_judgments"]
            ],
        )
        payload = request.model_dump()

        assert payload["code_profile"]["source_domain"] == "code"
        assert payload["code_profile"]["query_profile"]["repository_ids"] == [
            "repo://fixture/code-golden-shop"
        ]
        assert (
            payload["code_profile"]["query_profile"]["commit"]
            == (case["query_profile"]["scope_ref"])
        )
        assert payload["expected_paths"] == [
            required_path["nodes"] for required_path in case["required_paths"]
        ]


@pytest.fixture(scope="module")
def code_golden_v2_materialization(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    materializer = _v2_materializer()
    root = tmp_path_factory.mktemp("code-golden-v2-materialization")
    sources = materializer.materialize_all(root / "source", REPOSITORY_ROOT)
    return {
        "materializer": materializer,
        "root": root,
        "sources": sources,
    }


@pytest.fixture(scope="module")
def code_golden_v2_ingest(
    tmp_path_factory: pytest.TempPathFactory,
    code_golden_v2_materialization: dict[str, Any],
) -> dict[str, Any]:
    runtime_root = tmp_path_factory.mktemp("code-golden-v2-ingest")
    settings = Settings(
        data_dir=runtime_root / "data",
        database_path=runtime_root / "data" / "evidence-rag.sqlite3",
        repository_cache=runtime_root / "repository-cache",
        web_dir=REPOSITORY_ROOT / "web",
        allowed_local_roots=(
            code_golden_v2_materialization["root"],
            runtime_root,
        ),
        project_root=REPOSITORY_ROOT,
    )
    runtime = create_runtime(settings)
    snapshots: dict[str, Any] = {}
    workflows: dict[str, dict[str, Any]] = {}
    for source_class, source in code_golden_v2_materialization["sources"].items():
        snapshot = runtime.ingestion.resolver.resolve(
            source.path,
            project_id="project-rag",
            acl_ref="acl://code-golden-v2/public",
        )
        snapshots[source_class] = snapshot
        request = RepositoryIngestRequest(
            source=source.path,
            project_id="project-rag",
            acl_ref="acl://code-golden-v2/public",
            ignore=["generated", "vendor"],
            history_depth=3,
        )
        workflow_id = runtime.ingestion.enqueue(request)
        runtime.ingestion.run(workflow_id, request)
        with runtime.store.connection() as database:
            row = database.execute(
                "SELECT status, error, counters_json FROM workflows WHERE id=?",
                (workflow_id,),
            ).fetchone()
        assert row is not None
        workflows[source_class] = {
            "status": row["status"],
            "error": row["error"],
            "counters": json.loads(row["counters_json"]),
        }
    return {
        **code_golden_v2_materialization,
        "runtime": runtime,
        "settings": settings,
        "snapshots": snapshots,
        "workflows": workflows,
    }


def _git_text(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_code_golden_v2_static_package_release_and_review_provenance() -> None:
    adapter = _v2_adapter()
    summary = adapter.validate_static_package(REPOSITORY_ROOT)
    manifest = _json(V2_MANIFEST_PATH)
    cases = _v2_dataset()["cases"]

    assert summary == {
        "package_hash": manifest["package_hash"],
        "release_record_id": "code-golden-v2-release-001",
        "case_count": 50,
        "smoke_count": 15,
        "source_classes": [
            "controlled_multilingual",
            "current_project_snapshot",
            "historical_error",
        ],
        "eligible_cases": 33,
        "ineligible_cases": 17,
    }
    assert manifest["immutable"] is True
    assert manifest["supersedes"]["dataset_version"] == "code-golden-v1"
    assert Counter(case["task"] for case in cases) == EXPECTED_TASK_COUNTS
    assert sum(case["smoke"] for case in cases) == 15
    assert {case["source_class"] for case in cases} == {
        "current_project_snapshot",
        "controlled_multilingual",
        "historical_error",
    }
    assert all(
        record["reviewed_package_hash"] == manifest["package_hash"]
        for record in adapter.verify_review_provenance(REPOSITORY_ROOT)
    )


def test_code_golden_v2_same_version_digest_drift_fails_closed(
    tmp_path: Path,
) -> None:
    adapter = _v2_adapter()
    drift_root = tmp_path / "drift-package"
    drift_eval = drift_root / "evals" / "code"
    drift_fixture = drift_root / "tests" / "fixtures" / "code_golden" / "v2"
    drift_eval.mkdir(parents=True)
    for name in (
        "code-golden-v2.manifest.json",
        "code-golden-v2.cases.yaml",
        "annotation-contract-v2.schema.json",
        "code-golden-v2.unit-annotations.pending.yaml",
        "code-golden-releases.jsonl",
        "code-golden-v2.provenance.jsonl",
    ):
        shutil.copy2(EVAL_ROOT / name, drift_eval / name)
    shutil.copytree(FIXTURE_ROOT / "v2", drift_fixture)
    cases_path = drift_eval / "code-golden-v2.cases.yaml"
    cases_path.write_text(
        cases_path.read_text(encoding="utf-8") + "\n# same-version drift\n",
        encoding="utf-8",
    )

    with pytest.raises(adapter.GoldenPackageError, match="same-version package digest drift"):
        adapter.verify_release(drift_root)


def test_code_golden_v2_has_no_unit_treatment_identity() -> None:
    dataset_text = V2_CASES_PATH.read_text(encoding="utf-8").casefold()
    dataset = _v2_dataset()
    pending = yaml.safe_load(
        (EVAL_ROOT / "code-golden-v2.unit-annotations.pending.yaml").read_text(encoding="utf-8")
    )

    assert "expected_unit_ids" not in dataset_text
    assert "retrieval_unit_id" not in dataset_text
    assert "unit://" not in dataset_text
    assert all(
        "unit" not in judgment["target_key"].casefold()
        for case in dataset["cases"]
        for judgment in case["candidate_judgments"]
    )
    assert pending["status"] == "pending_c2"
    assert pending["builder_version"] is None
    assert pending["required_builder_version"] == "c2-code-retrieval-unit-builder-v1"
    assert pending["annotations"] == []


def test_code_golden_v2_adapter_matches_live_c0_01_contract() -> None:
    adapter = _v2_adapter()
    manifest = _json(V2_MANIFEST_PATH)
    source_manifest = _json(V2_SOURCE_MANIFEST_PATH)
    cases = _v2_dataset()["cases"]
    eligible_ids: list[str] = []

    for case in cases:
        if case["treatment_eligibility"]["status"] == "ineligible":
            with pytest.raises(adapter.GoldenPackageError, match="machine-ineligible"):
                adapter.expand_case(
                    case,
                    manifest=manifest,
                    source_manifest=source_manifest,
                )
            disabled = adapter.expand_case(
                case,
                manifest=manifest,
                source_manifest=source_manifest,
                allow_ineligible=True,
            )
            assert disabled["enabled"] is False
            continue

        expanded = adapter.expand_case(
            case,
            manifest=manifest,
            source_manifest=source_manifest,
        )
        metadata = expanded.pop("_code_golden_v2")
        request = EvaluationCaseCreate.model_validate(expanded)
        payload = request.model_dump()
        profile = payload["code_profile"]
        eligible_ids.append(case["case_id"])

        assert metadata["treatment_eligibility"]["status"] == "eligible"
        assert profile["dataset_id"] == "code-golden"
        assert profile["dataset_version"] == "code-golden-v2"
        assert profile["dataset_package_hash"] == manifest["package_hash"]
        assert profile["expected_unit_ids"] == []
        assert payload["expected_paths"] == [path["nodes"] for path in profile["required_paths"]]
        assert profile["required_edge_types"] == sorted(
            {edge["edge_type"] for path in profile["required_paths"] for edge in path["edges"]}
        )
        assert all(
            judgment["retrieval_unit_id"] is None for judgment in payload["candidate_judgments"]
        )

    run_request = CodeEvaluationRunRequest(
        project_id="project-code-golden-v2",
        dataset_id=manifest["dataset_id"],
        dataset_version=manifest["dataset_version"],
        package_hash=manifest["package_hash"],
        case_ids=eligible_ids,
        repository_ids=[source["repository_id"] for source in source_manifest["source_classes"]],
        graph_candidate_enabled=False,
    )
    assert run_request.package_hash == manifest["package_hash"]
    assert len(run_request.case_ids) == 33


def test_code_golden_v2_materializer_is_deterministic_and_uses_real_git(
    tmp_path: Path,
    code_golden_v2_materialization: dict[str, Any],
) -> None:
    materializer = code_golden_v2_materialization["materializer"]
    first_sources = code_golden_v2_materialization["sources"]
    second_sources = materializer.materialize_all(tmp_path / "second", REPOSITORY_ROOT)

    def stable_descriptor(source: Any) -> dict[str, Any]:
        descriptor = source.to_dict()
        descriptor.pop("path")
        return descriptor

    assert {key: stable_descriptor(value) for key, value in first_sources.items()} == {
        key: stable_descriptor(value) for key, value in second_sources.items()
    }
    assert set(first_sources) == {
        "current_project_snapshot",
        "controlled_multilingual",
        "historical_error",
    }

    history = first_sources["historical_error"]
    history_root = Path(history.path)
    assert len(history.commits) == 3
    assert len(set(history.commits)) == 3
    assert all(re.fullmatch(r"[0-9a-f]{40}", commit) for commit in history.commits)
    assert all(
        _git_text(history_root, "cat-file", "-t", commit) == "commit" for commit in history.commits
    )
    assert (
        _git_text(history_root, "rev-parse", "refs/heads/release-v1")
        == (history.branches["release-v1"])
    )
    assert (
        _git_text(history_root, "rev-parse", "refs/tags/v1.0.0^{commit}")
        == (history.tags["v1.0.0"])
    )
    assert history.dirty is True
    assert history.resolved_ref.startswith(f"{history.base_commit}+dirty.")
    assert {observation.observed_status for observation in history.validation_observations} == {
        "passed",
        "failed",
    }
    assert all(
        observation.observed_status == observation.expected_status
        and observation.observed_exit_code == observation.expected_exit_code
        for observation in history.validation_observations
    )


def test_code_golden_v2_resolver_and_isolated_ingest_cover_three_sources(
    code_golden_v2_ingest: dict[str, Any],
) -> None:
    source_manifest = _json(V2_SOURCE_MANIFEST_PATH)
    sources = code_golden_v2_ingest["sources"]
    snapshots = code_golden_v2_ingest["snapshots"]
    workflows = code_golden_v2_ingest["workflows"]
    runtime = code_golden_v2_ingest["runtime"]

    assert all(workflow["status"] == "completed" for workflow in workflows.values())
    assert all(workflow["error"] is None for workflow in workflows.values())
    assert workflows["current_project_snapshot"]["counters"]["files"] == 3
    assert workflows["controlled_multilingual"]["counters"]["files"] >= 8
    assert workflows["historical_error"]["counters"]["commits"] == 3

    for source_class, source in sources.items():
        snapshot = snapshots[source_class]
        assert snapshot.id == source.repository_id
        assert snapshot.base_commit == source.base_commit
        assert snapshot.head_commit == source.resolved_ref
        assert snapshot.dirty is source.dirty
        with runtime.store.connection() as database:
            repository = database.execute(
                "SELECT id, head_commit, source_url, status FROM repositories WHERE id=?",
                (source.repository_id,),
            ).fetchone()
        assert repository is not None
        assert repository["id"] == source.repository_id
        assert repository["head_commit"] == source.resolved_ref
        assert repository["source_url"] == source.remote_url
        assert repository["status"] == "ready"

    assert {source["repository_id"] for source in source_manifest["source_classes"]} == {
        source.repository_id for source in sources.values()
    }


def test_code_golden_v2_refs_entities_locators_and_paths_resolve(
    code_golden_v2_ingest: dict[str, Any],
) -> None:
    source_manifest = _json(V2_SOURCE_MANIFEST_PATH)
    runtime = code_golden_v2_ingest["runtime"]
    sources = code_golden_v2_ingest["sources"]
    source_by_repository = {source.repository_id: source for source in sources.values()}
    active_ref_keys = {
        "project_fixed_head",
        "controlled_main",
        "history_dirty",
    }
    parser = CodeParser()

    for ref in source_manifest["refs"].values():
        source = source_by_repository[ref["repository_id"]]
        source_root = Path(source.path)
        if ref["kind"] == "dirty":
            assert ref["resolved_ref"] == source.resolved_ref
            assert ref["base_sha"] == source.base_commit
            continue
        sha = ref["sha"]
        assert _git_text(source_root, "cat-file", "-t", sha) == "commit"
        if ref["kind"] == "branch":
            assert _git_text(source_root, "rev-parse", f"refs/heads/{ref['name']}") == sha
        if ref["kind"] == "tag":
            assert _git_text(source_root, "rev-parse", f"refs/tags/{ref['name']}^{{commit}}") == sha

    for target in source_manifest["entities"].values():
        source = source_by_repository[target["repository_id"]]
        ref = source_manifest["refs"][target["ref_key"]]
        span = target["span"]
        if target["ref_key"] in active_ref_keys and target["treatment_status"] == "available":
            observed = runtime.store.get_entity(target["entity_id"])
            assert observed is not None
            assert observed["path"] == target["path"]
            assert observed["qualified_name"] == target["qualified_name"]
            assert observed["start_line"] == span["start_line"]
            assert observed["end_line"] == span["end_line"]
            assert observed["commit_sha"] == (ref.get("resolved_ref") or ref.get("sha"))
            continue

        if target["path"].startswith(("generated/", "vendor/")):
            assert runtime.store.get_entity(target["entity_id"]) is None
            content = (Path(source.path) / target["path"]).read_text(encoding="utf-8")
            parsed = parser.parse(target["path"], content)
            symbol = next(
                item for item in parsed.symbols if item.qualified_name == target["qualified_name"]
            )
        else:
            historical = runtime.code_history.read_file(
                target["repository_id"],
                ref["sha"],
                target["path"],
            )
            symbol = next(
                item
                for item in historical["symbols"]
                if item["qualified_name"] == target["qualified_name"]
            )
        assert (
            symbol.start_line == span["start_line"]
            if hasattr(symbol, "start_line")
            else (symbol["start_line"] == span["start_line"])
        )
        assert (
            symbol.end_line == span["end_line"]
            if hasattr(symbol, "end_line")
            else (symbol["end_line"] == span["end_line"])
        )
        if target["treatment_status"] == "unavailable":
            assert target["unavailable_reason"]

    entities_by_id = {
        target["entity_id"]: target for target in source_manifest["entities"].values()
    }
    for path in source_manifest["paths"].values():
        for edge in path["edges"]:
            if path["availability"] == "available":
                with runtime.store.connection() as database:
                    observed = database.execute(
                        """SELECT 1 FROM edges
                           WHERE source_id=? AND edge_type=? AND target_id=?""",
                        (edge["source"], edge["edge_type"], edge["target"]),
                    ).fetchone()
                assert observed is not None
                continue

            assert path["unavailable_reason"]
            source_target = entities_by_id[edge["source"]]
            target_target = entities_by_id[edge["target"]]
            ref = source_manifest["refs"][source_target["ref_key"]]
            historical = runtime.code_history.read_file(
                source_target["repository_id"],
                ref["sha"],
                source_target["path"],
            )
            parsed = parser.parse(source_target["path"], historical["content"])
            source_symbol = next(
                item
                for item in parsed.symbols
                if item.qualified_name == source_target["qualified_name"]
            )
            assert target_target["qualified_name"].rsplit(".", 1)[-1] in source_symbol.calls


def test_code_golden_v2_validation_targets_are_observed_not_asserted(
    code_golden_v2_materialization: dict[str, Any],
) -> None:
    source_manifest = _json(V2_SOURCE_MANIFEST_PATH)
    history = code_golden_v2_materialization["sources"]["historical_error"]
    observations = {
        observation.validation_id: observation for observation in history.validation_observations
    }
    expected_observation_ids = {
        "history_release_failing": "historical-release-failing",
        "history_main_passing": "historical-main-passing",
        "history_dirty_failing": "historical-dirty-failing",
    }

    for target_key, observation_id in expected_observation_ids.items():
        target = source_manifest["validation_targets"][target_key]
        observation = observations[observation_id]
        assert observation.repository_id == target["repository_id"]
        assert observation.ref_sha == (
            source_manifest["refs"][target["ref_key"]].get("resolved_ref")
            or source_manifest["refs"][target["ref_key"]]["sha"]
        )
        assert observation.script_path == target["path"]
        assert observation.observed_status == target["expected_status"]
        assert observation.observed_exit_code == target["expected_exit_code"]
        assert target["materialization_status"] == "available"
        assert target["treatment_status"] == "unavailable"
        assert target["unavailable_reason"]


def test_code_golden_v2_leakage_scanner_rejects_metadata(
    tmp_path: Path,
    code_golden_v2_materialization: dict[str, Any],
) -> None:
    adapter = _v2_adapter()
    source_manifest = _json(V2_SOURCE_MANIFEST_PATH)
    literals = source_manifest["leakage_policy"]["ingest_content_forbidden_literals"]
    sources = code_golden_v2_materialization["sources"]

    for source_class, source in sources.items():
        findings = adapter.scan_ingest_root(
            Path(source.path),
            forbidden_literals=literals,
            reject_fixture_metadata_paths=source_class
            in {"controlled_multilingual", "historical_error"},
        )
        assert findings == []

    for source_class in ("controlled_multilingual", "historical_error"):
        source_root = Path(sources[source_class].path)
        assert not (source_root / "README.md").exists()
        assert not any(
            path.name.startswith("test_") or path.name.endswith("_test.py")
            for path in source_root.rglob("*.py")
        )

    leaking_root = tmp_path / "leaking-source"
    leaking_root.mkdir()
    (leaking_root / "README.md").write_text(
        "code-golden-v2 package_hash answerability\n",
        encoding="utf-8",
    )
    findings = adapter.scan_ingest_root(
        leaking_root,
        forbidden_literals=literals,
        reject_fixture_metadata_paths=True,
    )
    assert "path:README.md" in findings
    assert any(finding.startswith("literal:code-golden-v2:") for finding in findings)
