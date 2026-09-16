from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DATASET_ID = "code-golden"
DATASET_VERSION = "code-golden-v2"
SELF_HASH_SENTINEL = "sha256:SELF"
EXCLUDED_FIXTURE_NAMES = {".DS_Store"}
EXCLUDED_FIXTURE_SUFFIXES = {".pyc", ".pyo"}
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
FORBIDDEN_BASELINE_MARKERS = (
    "expected_unit_ids",
    "retrieval_unit_id",
    "unit://",
)


class GoldenPackageError(ValueError):
    pass


def package_paths(repository_root: Path) -> dict[str, Path]:
    root = repository_root.resolve()
    return {
        "manifest": root / "evals/code/code-golden-v2.manifest.json",
        "cases": root / "evals/code/code-golden-v2.cases.yaml",
        "annotation_contract": root / "evals/code/annotation-contract-v2.schema.json",
        "unit_pending": root / "evals/code/code-golden-v2.unit-annotations.pending.yaml",
        "fixture_root": root / "tests/fixtures/code_golden/v2",
        "source_manifest": root / "tests/fixtures/code_golden/v2/source-manifest.json",
        "release_registry": root / "evals/code/code-golden-releases.jsonl",
        "review_provenance": root / "evals/code/code-golden-v2.provenance.jsonl",
    }


def load_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise GoldenPackageError(f"expected a JSON object: {path}")
    return loaded


def load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise GoldenPackageError(f"expected a YAML mapping: {path}")
    return loaded


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GoldenPackageError(f"invalid JSONL record at {path}:{line_number}") from exc
        if not isinstance(record, dict):
            raise GoldenPackageError(f"JSONL record is not an object at {path}:{line_number}")
        records.append(record)
    return records


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalized_manifest_digest(path: Path) -> str:
    manifest = load_json(path)
    manifest["package_hash"] = SELF_HASH_SENTINEL
    return sha256_bytes(canonical_json_bytes(manifest))


def fixture_tree_digest(root: Path) -> str:
    entries: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if "__pycache__" in path.parts:
            continue
        if path.name in EXCLUDED_FIXTURE_NAMES or path.suffix in EXCLUDED_FIXTURE_SUFFIXES:
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_digest(path),
            }
        )
    return sha256_bytes(canonical_json_bytes(entries))


def component_digests(repository_root: Path) -> dict[str, str]:
    paths = package_paths(repository_root)
    return {
        "manifest": normalized_manifest_digest(paths["manifest"]),
        "cases": file_digest(paths["cases"]),
        "annotation_contract": file_digest(paths["annotation_contract"]),
        "unit_pending": file_digest(paths["unit_pending"]),
        "fixture_tree": fixture_tree_digest(paths["fixture_root"]),
    }


def compute_package_hash(repository_root: Path) -> str:
    payload = {
        "algorithm": "code-golden-canonical-package-v2",
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "components": component_digests(repository_root),
    }
    return sha256_bytes(canonical_json_bytes(payload))


def record_hash(record: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in record.items() if key != "record_hash"}
    return sha256_bytes(canonical_json_bytes(unsigned))


def verify_append_only_chain(records: list[dict[str, Any]], *, ledger_name: str) -> None:
    previous: str | None = None
    seen_ids: set[str] = set()
    for index, record in enumerate(records, 1):
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise GoldenPackageError(f"{ledger_name} record {index} has no record_id")
        if record_id in seen_ids:
            raise GoldenPackageError(f"{ledger_name} has duplicate record_id: {record_id}")
        seen_ids.add(record_id)
        if record.get("sequence") != index:
            raise GoldenPackageError(f"{ledger_name} sequence is not append-only at {record_id}")
        if record.get("previous_record_hash") != previous:
            raise GoldenPackageError(f"{ledger_name} chain break at {record_id}")
        observed_hash = record_hash(record)
        if record.get("record_hash") != observed_hash:
            raise GoldenPackageError(f"{ledger_name} record hash mismatch at {record_id}")
        previous = observed_hash


def verify_release(repository_root: Path) -> dict[str, Any]:
    paths = package_paths(repository_root)
    manifest = load_json(paths["manifest"])
    computed = compute_package_hash(repository_root)
    if manifest.get("dataset_id") != DATASET_ID:
        raise GoldenPackageError("manifest dataset_id mismatch")
    if manifest.get("dataset_version") != DATASET_VERSION:
        raise GoldenPackageError("manifest dataset_version mismatch")
    if manifest.get("package_hash") != computed:
        raise GoldenPackageError(
            "same-version package digest drift: "
            f"manifest={manifest.get('package_hash')} computed={computed}"
        )

    records = load_jsonl(paths["release_registry"])
    verify_append_only_chain(records, ledger_name="release registry")
    matching = [
        record
        for record in records
        if record.get("dataset_id") == DATASET_ID
        and record.get("dataset_version") == DATASET_VERSION
    ]
    if len(matching) != 1:
        raise GoldenPackageError(
            "release registry must contain exactly one record for this dataset version"
        )
    release = matching[0]
    if release.get("package_hash") != computed:
        raise GoldenPackageError("release registry package hash mismatch")
    if release.get("component_digests") != component_digests(repository_root):
        raise GoldenPackageError("release registry component digests mismatch")
    return release


def verify_review_provenance(repository_root: Path) -> list[dict[str, Any]]:
    paths = package_paths(repository_root)
    package_hash = compute_package_hash(repository_root)
    records = load_jsonl(paths["review_provenance"])
    verify_append_only_chain(records, ledger_name="review provenance")
    required = {
        "record_id",
        "sequence",
        "previous_record_hash",
        "reviewer_identity",
        "reviewed_at",
        "reviewed_package_hash",
        "case_ids",
        "decision",
        "resolution",
        "evidence",
        "record_hash",
    }
    for record in records:
        if set(record) != required:
            raise GoldenPackageError(
                f"review record has unexpected shape: {record.get('record_id')}"
            )
        identity = record["reviewer_identity"]
        if not isinstance(identity, dict) or not identity.get("scheme") or not identity.get("id"):
            raise GoldenPackageError("reviewer identity must be structured and non-empty")
        if record["reviewed_package_hash"] != package_hash:
            raise GoldenPackageError("review record does not bind the released package hash")
        if not record["reviewed_at"] or not record["resolution"]:
            raise GoldenPackageError("review record requires time and resolution")
    return records


def _ref_value(ref: dict[str, Any]) -> str:
    return str(ref.get("resolved_ref") or ref.get("sha"))


def expand_case(
    case: dict[str, Any],
    *,
    manifest: dict[str, Any],
    source_manifest: dict[str, Any],
    allow_ineligible: bool = False,
) -> dict[str, Any]:
    eligibility = case["treatment_eligibility"]
    if eligibility["status"] != "eligible" and not allow_ineligible:
        raise GoldenPackageError(
            f"{case['case_id']} is machine-ineligible: {eligibility['reason']}"
        )

    entities = source_manifest["entities"]
    validations = source_manifest["validation_targets"]
    paths = source_manifest["paths"]
    refs = source_manifest["refs"]

    expected_entities = [entities[key] for key in case["expected_target_keys"]]
    forbidden_entities = [entities[key] for key in case["forbidden_target_keys"]]
    expanded_paths = [deepcopy(paths[key]) for key in case["required_path_keys"]]
    for path in expanded_paths:
        path.pop("availability", None)
        path.pop("unavailable_reason", None)
    expected_ref = _ref_value(refs[case["query_profile"]["ref_key"]])

    required_edge_types = sorted(
        {edge["edge_type"] for path in expanded_paths for edge in path["edges"]}
    )
    locators = [
        {
            "entity_id": target["entity_id"],
            "repository_id": target["repository_id"],
            "ref": _ref_value(refs[target["ref_key"]]),
            "path": target["path"],
            "start_line": target["span"]["start_line"],
            "end_line": target["span"]["end_line"],
        }
        for target in expected_entities
    ]
    candidate_judgments = [
        {
            "entity_id": entities[judgment["target_key"]]["entity_id"],
            "retrieval_unit_id": None,
            "relevance_grade": judgment["relevance_grade"],
            "necessity_role": judgment["necessity_role"],
            "note": judgment["note"],
        }
        for judgment in case["candidate_judgments"]
    ]
    validation_targets = [deepcopy(validations[key]) for key in case["validation_target_keys"]]
    return {
        "project_id": "project-code-golden-v2",
        "name": case["case_id"],
        "question": case["query"],
        "expected_sources": ["code"],
        "expected_entity_ids": [target["entity_id"] for target in expected_entities],
        "expected_paths": [path["nodes"] for path in expanded_paths],
        "expected_commit_ids": [],
        "forbidden_entity_ids": [target["entity_id"] for target in forbidden_entities],
        "required_version": expected_ref,
        "tags": [case["task"], *case["slices"]],
        "enabled": eligibility["status"] == "eligible",
        "code_profile": {
            "source_domain": "code",
            "task": case["task"],
            "query_profile": {
                "repository_ids": [case["query_profile"]["repository_id"]],
                "commit": expected_ref,
                "languages": [
                    language for language in case["code_languages"] if language != "text"
                ],
                "style": case["query_profile"]["style"],
                "acl_refs": case["query_profile"]["acl_refs"],
                "graph_requirement": case["query_profile"]["graph_requirement"],
            },
            "dataset_id": manifest["dataset_id"],
            "dataset_version": manifest["dataset_version"],
            "dataset_package_hash": manifest["package_hash"],
            "expected_unit_ids": [],
            "expected_entity_types": sorted(
                {target["entity_type"] for target in expected_entities}
            ),
            "expected_locators": locators,
            "required_paths": expanded_paths,
            "required_edge_types": required_edge_types,
            "acceptable_alternative_groups": [],
            "expected_context_roles": case["expected_context_roles"],
            "expected_ref": expected_ref,
            "expected_answer_mode": case["expected_answer_mode"],
        },
        "candidate_judgments": candidate_judgments,
        "_code_golden_v2": {
            "answerability": case["answerability"],
            "source_class": case["source_class"],
            "treatment_eligibility": deepcopy(eligibility),
            "validation_targets": validation_targets,
        },
    }


def scan_ingest_root(
    root: Path,
    *,
    forbidden_literals: list[str],
    reject_fixture_metadata_paths: bool,
) -> list[str]:
    findings: list[str] = []
    forbidden_names = {
        "README.md",
        "fixture_manifest.json",
        "source-manifest.json",
        "code-golden-v2.cases.yaml",
        "annotation-contract-v2.schema.json",
    }
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if ".git" in path.parts:
            continue
        relative = path.relative_to(root).as_posix()
        if reject_fixture_metadata_paths and path.name in forbidden_names:
            findings.append(f"path:{relative}")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for literal in forbidden_literals:
            if literal in content:
                findings.append(f"literal:{literal}:{relative}")
    return findings


def validate_static_package(repository_root: Path) -> dict[str, Any]:
    paths = package_paths(repository_root)
    manifest = load_json(paths["manifest"])
    dataset = load_yaml(paths["cases"])
    contract = load_json(paths["annotation_contract"])
    source_manifest = load_json(paths["source_manifest"])
    pending = load_yaml(paths["unit_pending"])
    cases = dataset["cases"]

    if dataset["dataset_id"] != DATASET_ID or dataset["dataset_version"] != DATASET_VERSION:
        raise GoldenPackageError("cases dataset identity mismatch")
    if len(cases) != 50 or len({case["case_id"] for case in cases}) != 50:
        raise GoldenPackageError("v2 requires exactly 50 unique cases")
    expected_case_ids = [f"code-golden-v2-{index:03d}" for index in range(1, 51)]
    if [case["case_id"] for case in cases] != expected_case_ids:
        raise GoldenPackageError("case IDs are not the canonical v2 sequence")
    if Counter(case["task"] for case in cases) != Counter(EXPECTED_TASK_COUNTS):
        raise GoldenPackageError("task distribution mismatch")
    if sum(bool(case["smoke"]) for case in cases) != 15:
        raise GoldenPackageError("v2 requires exactly 15 smoke cases")
    if {case["source_class"] for case in cases} != {
        "current_project_snapshot",
        "controlled_multilingual",
        "historical_error",
    }:
        raise GoldenPackageError("all three source classes must be represented")

    required_fields = set(contract["required"])
    allowed_fields = set(contract["properties"])
    entities = source_manifest["entities"]
    paths_catalog = source_manifest["paths"]
    validations = source_manifest["validation_targets"]
    refs = source_manifest["refs"]
    source_classes = {
        source["source_class"]: source for source in source_manifest["source_classes"]
    }
    review_ids: set[str] = set()
    for case in cases:
        if set(case) != required_fields or not set(case) <= allowed_fields:
            raise GoldenPackageError(f"case shape mismatch: {case['case_id']}")
        if case["source_class"] not in source_classes:
            raise GoldenPackageError(f"unknown source class: {case['case_id']}")
        profile = case["query_profile"]
        source = source_classes[case["source_class"]]
        if profile["repository_id"] != source["repository_id"]:
            raise GoldenPackageError(f"case repository/source mismatch: {case['case_id']}")
        if profile["ref_key"] not in refs:
            raise GoldenPackageError(f"unknown ref key: {case['case_id']}")
        if refs[profile["ref_key"]]["repository_id"] != profile["repository_id"]:
            raise GoldenPackageError(f"case ref/repository mismatch: {case['case_id']}")
        for key in case["expected_target_keys"] + case["forbidden_target_keys"]:
            if key not in entities:
                raise GoldenPackageError(f"unknown entity target {key}: {case['case_id']}")
            if entities[key]["repository_id"] != profile["repository_id"]:
                raise GoldenPackageError(f"cross-repository entity target: {case['case_id']}")
        for key in case["validation_target_keys"]:
            if key not in validations:
                raise GoldenPackageError(f"unknown validation target {key}: {case['case_id']}")
        for key in case["required_path_keys"]:
            if key not in paths_catalog:
                raise GoldenPackageError(f"unknown path target {key}: {case['case_id']}")
            path = paths_catalog[key]
            if len(path["nodes"]) != len(path["edges"]) + 1:
                raise GoldenPackageError(f"invalid rich path length: {key}")
            if path["availability"] == "unavailable" and not path.get("unavailable_reason"):
                raise GoldenPackageError(f"unavailable path lacks reason: {key}")
            for index, edge in enumerate(path["edges"]):
                left, right = path["nodes"][index : index + 2]
                expected = (left, right) if edge["direction"] == "outgoing" else (right, left)
                if (edge["source"], edge["target"]) != expected:
                    raise GoldenPackageError(f"rich path direction mismatch: {key}")
        for judgment in case["candidate_judgments"]:
            if judgment["target_key"] not in entities:
                raise GoldenPackageError(f"candidate target does not resolve: {case['case_id']}")
        eligibility = case["treatment_eligibility"]
        if eligibility["status"] == "ineligible" and not eligibility["reason"]:
            raise GoldenPackageError(f"ineligible case lacks reason: {case['case_id']}")
        review_ids.update(case["review"]["record_ids"])

    serialized_cases = paths["cases"].read_text(encoding="utf-8").casefold()
    for marker in FORBIDDEN_BASELINE_MARKERS:
        if marker.casefold() in serialized_cases:
            raise GoldenPackageError(f"Unit treatment marker leaked into v2 cases: {marker}")
    if pending["status"] != "pending_c2":
        raise GoldenPackageError("Unit annotation layer must remain pending_c2")
    if pending["builder_version"] is not None or pending["annotations"]:
        raise GoldenPackageError("pending Unit layer must have no builder or annotations")

    release = verify_release(repository_root)
    provenance = verify_review_provenance(repository_root)
    provenance_ids = {record["record_id"] for record in provenance}
    if review_ids != provenance_ids:
        raise GoldenPackageError("case review references do not match provenance records")
    return {
        "package_hash": manifest["package_hash"],
        "release_record_id": release["record_id"],
        "case_count": len(cases),
        "smoke_count": sum(bool(case["smoke"]) for case in cases),
        "source_classes": sorted(source_classes),
        "eligible_cases": sum(
            case["treatment_eligibility"]["status"] == "eligible" for case in cases
        ),
        "ineligible_cases": sum(
            case["treatment_eligibility"]["status"] == "ineligible" for case in cases
        ),
    }
