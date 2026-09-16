from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from .models import EvaluationCaseCreate
from .store import EvaluationStore

DATASET_ID = "code-golden"
DATASET_VERSION = "code-golden-v2"
PACKAGE_HASH = "sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61"
RELEASE_RECORD_ID = "code-golden-v2-release-001"
PROJECT_ID = "project-code-golden-v2"
ACL_REF = "acl://code-golden-v2/public"
EXPECTED_CASE_COUNT = 50
EXPECTED_ELIGIBLE_COUNT = 33
EXPECTED_INELIGIBLE_COUNT = 17


class GoldenLoaderError(ValueError):
    """The released Golden package cannot be used without changing its identity."""


@dataclass(frozen=True, slots=True)
class GoldenCase:
    canonical_id: str
    request: EvaluationCaseCreate
    source_class: str
    query_language: str
    query_style: str
    slices: tuple[str, ...]
    smoke: bool
    answerability: str
    treatment_status: str
    unavailable_reason: str | None
    validation_targets: tuple[dict[str, Any], ...]

    @property
    def eligible(self) -> bool:
        return self.treatment_status == "eligible"


@dataclass(frozen=True, slots=True)
class GoldenPackage:
    repository_root: Path
    package_hash: str
    release_record_id: str
    manifest: dict[str, Any]
    source_manifest: dict[str, Any]
    validation_summary: dict[str, Any]
    cases: tuple[GoldenCase, ...]

    @property
    def eligible_cases(self) -> tuple[GoldenCase, ...]:
        return tuple(case for case in self.cases if case.eligible)

    @property
    def ineligible_cases(self) -> tuple[GoldenCase, ...]:
        return tuple(case for case in self.cases if not case.eligible)

    @property
    def membership_hash(self) -> str:
        members = [case.canonical_id for case in self.eligible_cases]
        encoded = json.dumps(
            members,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LoadedGolden:
    package: GoldenPackage
    case_ids: tuple[str, ...]
    canonical_case_ids: tuple[str, ...]


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_module(name: str, path: Path) -> ModuleType:
    cached = sys.modules.get(name)
    if (
        cached is not None
        and Path(str(getattr(cached, "__file__", ""))).resolve() == path.resolve()
    ):
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise GoldenLoaderError(f"unable to load released package component: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    finally:
        sys.dont_write_bytecode = previous
    return module


def golden_adapter(root: Path) -> ModuleType:
    path = root.resolve() / "evals" / "code" / "code_golden_v2.py"
    if not path.is_file():
        raise GoldenLoaderError("Code Golden v2 adapter is missing")
    return _load_module("evidence_rag_code_golden_v2_adapter", path)


def golden_materializer(root: Path) -> ModuleType:
    path = root.resolve() / "tests" / "fixtures" / "code_golden" / "v2" / "materializer.py"
    if not path.is_file():
        raise GoldenLoaderError("Code Golden v2 materializer is missing")
    return _load_module("evidence_rag_code_golden_v2_materializer", path)


def _require_exact_identity(
    validation_summary: dict[str, Any],
    manifest: dict[str, Any],
    release: dict[str, Any],
) -> None:
    observed = (
        manifest.get("dataset_id"),
        manifest.get("dataset_version"),
        manifest.get("package_hash"),
        release.get("record_id"),
    )
    expected = (DATASET_ID, DATASET_VERSION, PACKAGE_HASH, RELEASE_RECORD_ID)
    if observed != expected:
        raise GoldenLoaderError(
            "released Code Golden v2 identity differs from the C0-03 frozen anchor"
        )
    if validation_summary != {
        "package_hash": PACKAGE_HASH,
        "release_record_id": RELEASE_RECORD_ID,
        "case_count": EXPECTED_CASE_COUNT,
        "smoke_count": 15,
        "source_classes": [
            "controlled_multilingual",
            "current_project_snapshot",
            "historical_error",
        ],
        "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
        "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
    }:
        raise GoldenLoaderError("Code Golden v2 validation summary does not match the release")


def validate_golden_package(root: Path | None = None) -> GoldenPackage:
    resolved_root = (root or repository_root()).resolve()
    adapter = golden_adapter(resolved_root)
    try:
        validation_summary = adapter.validate_static_package(resolved_root)
        release = adapter.verify_release(resolved_root)
        adapter.verify_review_provenance(resolved_root)
        paths = adapter.package_paths(resolved_root)
        manifest = adapter.load_json(paths["manifest"])
        source_manifest = adapter.load_json(paths["source_manifest"])
        dataset = adapter.load_yaml(paths["cases"])
    except Exception as exc:
        if isinstance(exc, GoldenLoaderError):
            raise
        raise GoldenLoaderError(f"Code Golden v2 failed closed: {exc}") from exc
    _require_exact_identity(validation_summary, manifest, release)

    expanded_cases: list[GoldenCase] = []
    for raw_case in dataset.get("cases") or []:
        try:
            expanded = adapter.expand_case(
                raw_case,
                manifest=manifest,
                source_manifest=source_manifest,
                allow_ineligible=True,
            )
            metadata = expanded.pop("_code_golden_v2")
            request = EvaluationCaseCreate.model_validate(expanded)
        except Exception as exc:
            raise GoldenLoaderError(
                f"unable to expand canonical case {raw_case.get('case_id')}: {exc}"
            ) from exc
        eligibility = metadata["treatment_eligibility"]
        expanded_cases.append(
            GoldenCase(
                canonical_id=str(raw_case["case_id"]),
                request=request,
                source_class=str(metadata["source_class"]),
                query_language=str(raw_case["query_language"]),
                query_style=str(raw_case["query_profile"]["style"]),
                slices=tuple(str(item) for item in raw_case["slices"]),
                smoke=bool(raw_case["smoke"]),
                answerability=str(metadata["answerability"]),
                treatment_status=str(eligibility["status"]),
                unavailable_reason=(
                    str(eligibility["reason"]) if eligibility.get("reason") else None
                ),
                validation_targets=tuple(metadata["validation_targets"]),
            )
        )

    canonical_ids = [case.canonical_id for case in expanded_cases]
    expected_ids = [f"code-golden-v2-{index:03d}" for index in range(1, 51)]
    eligible_count = sum(case.eligible for case in expanded_cases)
    if (
        canonical_ids != expected_ids
        or len(expanded_cases) != EXPECTED_CASE_COUNT
        or eligible_count != EXPECTED_ELIGIBLE_COUNT
        or len(expanded_cases) - eligible_count != EXPECTED_INELIGIBLE_COUNT
    ):
        raise GoldenLoaderError("expanded Golden membership differs from the released package")

    return GoldenPackage(
        repository_root=resolved_root,
        package_hash=PACKAGE_HASH,
        release_record_id=RELEASE_RECORD_ID,
        manifest=manifest,
        source_manifest=source_manifest,
        validation_summary=validation_summary,
        cases=tuple(expanded_cases),
    )


def evaluation_case_id(canonical_id: str) -> str:
    return f"evaluation-case://{PROJECT_ID}/{canonical_id}"


def _persist_record(case: GoldenCase) -> dict[str, Any]:
    payload = case.request.model_dump()
    return {
        **payload,
        "id": evaluation_case_id(case.canonical_id),
        "display_key": case.canonical_id.upper(),
    }


def _normalized_judgments(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        values,
        key=lambda item: (
            str(item.get("entity_id") or ""),
            str(item.get("retrieval_unit_id") or ""),
        ),
    )


def load_golden_cases(store: EvaluationStore, package: GoldenPackage) -> LoadedGolden:
    existing = [
        case
        for case in store.list_cases(PROJECT_ID)
        if (case.get("code_profile") or {}).get("dataset_id") == DATASET_ID
        and (case.get("code_profile") or {}).get("dataset_version") == DATASET_VERSION
    ]
    if existing:
        raise GoldenLoaderError("Code Golden v2 is already loaded; immutable load is single-use")

    for case in package.cases:
        store.create_case(_persist_record(case))

    loaded = store.list_cases(PROJECT_ID)
    by_name = {str(case["name"]): case for case in loaded}
    if set(by_name) != {case.canonical_id for case in package.cases}:
        raise GoldenLoaderError("persisted Golden case names do not match canonical membership")

    eligible_ids: list[str] = []
    canonical_eligible_ids: list[str] = []
    for case in package.cases:
        observed = by_name[case.canonical_id]
        expected_payload = case.request.model_dump()
        for field in (
            "project_id",
            "name",
            "question",
            "expected_sources",
            "expected_entity_ids",
            "expected_paths",
            "expected_commit_ids",
            "forbidden_entity_ids",
            "required_version",
            "tags",
            "enabled",
            "code_profile",
            "candidate_judgments",
        ):
            observed_value = observed[field]
            expected_value = expected_payload[field]
            if field == "candidate_judgments":
                observed_value = _normalized_judgments(observed_value)
                expected_value = _normalized_judgments(expected_value)
            if observed_value != expected_value:
                raise GoldenLoaderError(
                    f"persisted Golden payload drift for {case.canonical_id}: {field}"
                )
        if case.eligible:
            eligible_ids.append(str(observed["id"]))
            canonical_eligible_ids.append(case.canonical_id)

    if (
        len(loaded) != EXPECTED_CASE_COUNT
        or len(eligible_ids) != EXPECTED_ELIGIBLE_COUNT
        or sum(not bool(case["enabled"]) for case in loaded) != EXPECTED_INELIGIBLE_COUNT
    ):
        raise GoldenLoaderError("persisted Golden eligibility does not match 50/33/17")

    return LoadedGolden(
        package=package,
        case_ids=tuple(sorted(eligible_ids)),
        canonical_case_ids=tuple(sorted(canonical_eligible_ids)),
    )
