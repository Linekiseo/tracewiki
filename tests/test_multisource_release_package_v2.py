from __future__ import annotations

import hashlib
import json
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest

from evidence_rag.rag.global_governance_v2 import (
    ReleaseStageV2,
    SourceReleaseTruthV2,
    build_rollback_rehearsal_v2,
    build_security_matrix_v2,
    evaluate_global_release_v2,
    evaluate_security_matrix_v2,
)
from evidence_rag.rag.multisource_evaluation_v2 import (
    CaseExecutionStateV2,
    build_multisource_bundle_v2,
    build_reviewed_case_v2,
    evaluate_multisource_release_v2,
)
from evidence_rag.rag.multisource_foundation_v2 import (
    MULTISOURCE_DOMAINS,
    build_multisource_golden_v2,
)
from evidence_rag.rag.multisource_release_package_v2 import (
    MULTISOURCE_RELEASE_PACKAGE_FILES,
    MultiSourceReleasePackageError,
    build_source_artifact_reference_v2,
    verify_multisource_release_package_v2,
    write_multisource_release_package_v2,
)
from evidence_rag.rag.performance_v2 import build_performance_dashboard_v2
from evidence_rag.rag.sources.experiment.contracts_v2 import (
    canonical_json_bytes_v2,
    canonical_sha256_v2,
)

_AUTHORITY_SHA256 = "sha256:" + "a1" * 32
_SOURCE_RUNS = {
    "code": "evaluation-run://project-code-cb0-v1/" + "01" * 16,
    "codex": "evaluation-correction://project-codex-xb0-v1/" + "02" * 16,
    "experiment": "evaluation-run://project-experiment-eb0-v1/" + "03" * 16,
    "notebook": "evaluation-run://project-notebook-nb0-v1/" + "04" * 16,
    "document": "evaluation-run://project-document-db0-v1/" + "05" * 16,
    "workspace": "evaluation-run://project-workspace-wb0-v1/" + "06" * 16,
}


def _quality_hold_bundle():
    golden = build_multisource_golden_v2()
    rows = tuple(
        build_reviewed_case_v2(
            case_id=item.case_id,
            execution_state=CaseExecutionStateV2.UNAVAILABLE,
            failure_reason="production_observation_unavailable",
        )
        for item in golden.cases
    )
    evaluation = evaluate_multisource_release_v2(golden, rows)
    security = evaluate_security_matrix_v2(build_security_matrix_v2())
    performance = build_performance_dashboard_v2(
        (),
        active_generations={},
        data_scale={"multisource_cases": 60},
        hardware_profile="isolated-test",
        concurrency=1,
        cache_state="cold",
    )
    source_truth = tuple(
        SourceReleaseTruthV2(
            source=source,
            engineering_complete=True,
            quality_qualified=False,
            disposition="QUALITY_HOLD",
            evidence_uri=None,
            evidence_sha256=None,
        )
        for source in MULTISOURCE_DOMAINS
    )
    release = evaluate_global_release_v2(
        evaluated_stage=ReleaseStageV2.OFFLINE,
        deployed_stage=ReleaseStageV2.OFFLINE,
        source_truth=source_truth,
        multisource_golden_sha256=golden.content_sha256,
        multisource_quality_available=False,
        multisource_quality_qualified=False,
        security_report=security,
        performance_dashboard=performance,
        rollback_rehearsal=build_rollback_rehearsal_v2(ReleaseStageV2.OFFLINE),
    )
    return build_multisource_bundle_v2(
        golden=golden,
        evaluation=evaluation,
        security=security,
        performance=performance,
        release=release,
        retrieval_executed=False,
        production_observation=False,
    )


def _source_references():
    return tuple(
        build_source_artifact_reference_v2(
            source=source,
            artifact_uri=_SOURCE_RUNS[source],
            artifact_set_sha256="sha256:" + f"{index:02x}" * 32,
            verifier_version=f"{source}-portable-verify-v1",
            quality_qualified=False,
        )
        for index, source in enumerate(MULTISOURCE_DOMAINS, start=16)
    )


def _write_package(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    output = tmp_path / "release-package"
    result = write_multisource_release_package_v2(
        output_dir=output,
        bundle=_quality_hold_bundle(),
        source_artifacts=_source_references(),
        authority_sha256=_AUTHORITY_SHA256,
    )
    assert result.status == "VERIFIED_QUALITY_HOLD_NON_QUALIFIED"
    assert result.qualified is False
    return output


def _canonical_file(payload: object) -> bytes:
    return canonical_json_bytes_v2(payload) + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _load(path: Path) -> object:
    return json.loads(path.read_bytes())


def _resign_checksums(package: Path) -> None:
    targets = MULTISOURCE_RELEASE_PACKAGE_FILES[:-1]
    checksums = tuple(
        (filename, _sha256_bytes((package / filename).read_bytes())) for filename in targets
    )
    payload = {
        "package_version": "multisource-release-audit-package-v1",
        "checksums": checksums,
        "package_set_sha256": canonical_sha256_v2(dict(checksums)),
    }
    (package / "checksums.json").write_bytes(_canonical_file(payload))


def _replace_payload(package: Path, filename: str, payload: object) -> None:
    (package / filename).write_bytes(_canonical_file(payload))
    _resign_checksums(package)


def _refresh_content_sha256(payload: dict[str, object]) -> None:
    payload["content_sha256"] = canonical_sha256_v2(
        {key: value for key, value in payload.items() if key != "content_sha256"}
    )


def test_quality_hold_package_is_portable_exact_files_and_never_authoritative(
    tmp_path: Path,
) -> None:
    package = _write_package(tmp_path)
    assert tuple(sorted(path.name for path in package.iterdir())) == tuple(
        sorted(MULTISOURCE_RELEASE_PACKAGE_FILES)
    )
    original = verify_multisource_release_package_v2(package)
    assert original.qualified is False
    assert original.verify_only is True
    assert original.retrieval_executed is False
    assert original.database_accessed is False
    assert original.network_accessed is False
    assert original.exact_files is True
    assert original.portable is True
    assert original.test_only is True
    assert original.authority_attested is False
    assert original.authority_attestation_sha256 is None
    assert original.caller_integrity_sha256 == _AUTHORITY_SHA256
    qualification = _load(package / "qualification.json")
    assert isinstance(qualification, dict)
    assert qualification["status"] == "QUALITY_HOLD_NON_QUALIFIED"
    assert qualification["qualified"] is False
    assert qualification["audit_only"] is True

    copied = tmp_path / "copied-anywhere"
    shutil.copytree(package, copied)
    assert verify_multisource_release_package_v2(copied) == original


def test_verifier_does_not_call_database_network_or_retrieval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_package(tmp_path)

    def bomb(*_args, **_kwargs):
        raise AssertionError("verify-only package attempted an external operation")

    monkeypatch.setattr(sqlite3, "connect", bomb)
    monkeypatch.setattr(socket, "socket", bomb)
    assert verify_multisource_release_package_v2(package).verify_only is True


@pytest.mark.parametrize("attack", ["extra", "delete", "tamper", "member_symlink"])
def test_exact_membership_checksums_and_regular_files_fail_closed(
    tmp_path: Path,
    attack: str,
) -> None:
    package = _write_package(tmp_path)
    if attack == "extra":
        (package / "extra.json").write_text("{}\n", encoding="utf-8")
    elif attack == "delete":
        (package / "manifest.json").unlink()
    elif attack == "tamper":
        payload = _load(package / "manifest.json")
        assert isinstance(payload, dict)
        payload["audit_only"] = False
        (package / "manifest.json").write_bytes(_canonical_file(payload))
    else:
        target = tmp_path / "alias.json"
        target.write_bytes((package / "manifest.json").read_bytes())
        (package / "manifest.json").unlink()
        (package / "manifest.json").symlink_to(target)
    with pytest.raises((ValueError, MultiSourceReleasePackageError)):
        verify_multisource_release_package_v2(package)


def test_symlink_package_root_is_rejected(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    alias = tmp_path / "package-alias"
    alias.symlink_to(package, target_is_directory=True)
    with pytest.raises(MultiSourceReleasePackageError, match="real directory"):
        verify_multisource_release_package_v2(alias)


@pytest.mark.parametrize(
    "unsafe",
    [
        "/Users/operator/private/result.json",
        r"\\server\share\result.json",
        "//server/share/result.json",
        "%255C%255Cserver%255Cshare%255Cresult.json",
        "password=correct-horse-battery",
        "owner@example.test",
        "formal.sqlite3",
        "formal.sqlite3-wal",
        "formal.sqlite3-shm",
        "cache.pyc",
    ],
)
def test_resigned_unsafe_strings_are_rejected(tmp_path: Path, unsafe: str) -> None:
    package = _write_package(tmp_path)
    qualification = _load(package / "qualification.json")
    assert isinstance(qualification, dict)
    qualification["blockers"] = sorted(
        [*qualification["blockers"], unsafe]  # type: ignore[index]
    )
    _refresh_content_sha256(qualification)
    _replace_payload(package, "qualification.json", qualification)
    with pytest.raises(MultiSourceReleasePackageError):
        verify_multisource_release_package_v2(package)


def test_nonfinite_duplicate_json_and_noncanonical_json_are_rejected(
    tmp_path: Path,
) -> None:
    package = _write_package(tmp_path)
    manifest = (package / "manifest.json").read_text(encoding="utf-8").rstrip()
    (package / "manifest.json").write_text(
        manifest[:-1] + ',"x":NaN}\n',
        encoding="utf-8",
    )
    _resign_checksums(package)
    with pytest.raises(MultiSourceReleasePackageError):
        verify_multisource_release_package_v2(package)

    package = _write_package(tmp_path / "second")
    manifest = _load(package / "manifest.json")
    assert isinstance(manifest, dict)
    raw = json.dumps(manifest, indent=2, ensure_ascii=False).encode() + b"\n"
    (package / "manifest.json").write_bytes(raw)
    _resign_checksums(package)
    with pytest.raises(MultiSourceReleasePackageError, match="not canonical"):
        verify_multisource_release_package_v2(package)

    package = _write_package(tmp_path / "third")
    raw = (package / "manifest.json").read_text(encoding="utf-8").rstrip()
    (package / "manifest.json").write_text(
        raw[:-1] + ',"qualified":false}\n',
        encoding="utf-8",
    )
    _resign_checksums(package)
    with pytest.raises(MultiSourceReleasePackageError, match="duplicate"):
        verify_multisource_release_package_v2(package)


def test_resigned_cross_file_authority_mismatch_is_rejected(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    authorities = _load(package / "authorities.json")
    assert isinstance(authorities, dict)
    authorities["evaluation_sha256"] = "sha256:" + "ff" * 32
    _refresh_content_sha256(authorities)
    _replace_payload(package, "authorities.json", authorities)
    with pytest.raises(MultiSourceReleasePackageError, match="cross-file"):
        verify_multisource_release_package_v2(package)


def test_caller_cannot_upgrade_integrity_digest_into_authority_attestation(
    tmp_path: Path,
) -> None:
    package = _write_package(tmp_path)
    authorities = _load(package / "authorities.json")
    assert isinstance(authorities, dict)
    authorities["authority_attestation_sha256"] = "sha256:" + "ab" * 32
    authorities["authority_attested"] = True
    _refresh_content_sha256(authorities)
    _replace_payload(package, "authorities.json", authorities)
    with pytest.raises(ValueError):
        verify_multisource_release_package_v2(package)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qualified", True),
        ("audit_only", False),
        ("status", "QUALIFIED"),
        ("release_decision", "PROMOTE"),
        ("default_engine", "v2"),
        ("evaluation_qualified", True),
        ("qualified", 0),
    ],
)
def test_resigned_qualification_can_never_be_promoted(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    package = _write_package(tmp_path)
    qualification = _load(package / "qualification.json")
    assert isinstance(qualification, dict)
    qualification[field] = value
    _refresh_content_sha256(qualification)
    _replace_payload(package, "qualification.json", qualification)
    with pytest.raises(ValueError):
        verify_multisource_release_package_v2(package)


def test_source_references_are_explicit_allowlisted_and_not_dereferenced(
    tmp_path: Path,
) -> None:
    references = list(_source_references())
    payload = references[0].model_dump(mode="json")
    payload["artifact_uri"] = "file:///tmp/evals"
    payload["content_sha256"] = canonical_sha256_v2(
        {key: value for key, value in payload.items() if key != "content_sha256"}
    )
    with pytest.raises(ValueError, match="allowlisted"):
        type(references[0]).model_validate(payload)

    with pytest.raises(ValueError, match="six-source order"):
        write_multisource_release_package_v2(
            output_dir=tmp_path / "bad-order",
            bundle=_quality_hold_bundle(),
            source_artifacts=tuple(reversed(references)),
            authority_sha256=_AUTHORITY_SHA256,
        )
    assert not (tmp_path / "bad-order").exists()


def test_resigned_source_artifact_and_manifest_cross_file_mismatch_fail(
    tmp_path: Path,
) -> None:
    package = _write_package(tmp_path)
    source_artifacts = _load(package / "source_artifacts.json")
    assert isinstance(source_artifacts, dict)
    refs = source_artifacts["references"]
    assert isinstance(refs, list)
    first = refs[0]
    assert isinstance(first, dict)
    first["artifact_set_sha256"] = "sha256:" + "ee" * 32
    _refresh_content_sha256(first)
    _refresh_content_sha256(source_artifacts)
    _replace_payload(package, "source_artifacts.json", source_artifacts)
    with pytest.raises(MultiSourceReleasePackageError, match="cross-file"):
        verify_multisource_release_package_v2(package)


def test_output_must_be_new_and_quality_hold_truth_is_required(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    with pytest.raises(MultiSourceReleasePackageError, match="new"):
        write_multisource_release_package_v2(
            output_dir=package,
            bundle=_quality_hold_bundle(),
            source_artifacts=_source_references(),
            authority_sha256=_AUTHORITY_SHA256,
        )
