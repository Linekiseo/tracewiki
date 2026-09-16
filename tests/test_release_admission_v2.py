from __future__ import annotations

import hashlib
import json
import shutil
import types
from pathlib import Path

import pytest

import evidence_rag.rag.release_admission_v2 as admission_module
from evidence_rag.rag.release_admission_v2 import (
    AuthorityFreshness,
    EvidenceAvailability,
    ImmutableSourceArtifactReferenceV2,
    ProductionAuthorityError,
    ReleaseAdmissionPackageError,
    ReleaseDecision,
    ReleaseGateEvidenceV2,
    ReviewedGateEvidenceAuthorityV2,
    ReviewedSourceEvidenceAuthorityV2,
    VerificationStatus,
    build_current_repository_quality_hold_v2,
    build_gate_evidence_v2,
    build_verifier_result_v2,
    evaluate_release_admission_v2,
    fixed_production_authority_registry_v2,
    fixed_reviewed_release_evidence_authority_registry_v2,
    inspect_current_production_authority_v2,
    verify_current_production_authority_v2,
    verify_release_admission_package_v2,
    write_release_admission_package_v2,
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _resign(directory: Path, filename: str) -> None:
    path = directory / filename
    path.write_bytes(_json_bytes(json.loads(path.read_bytes())))
    checksums_path = directory / "checksums.json"
    payload = json.loads(checksums_path.read_bytes())
    payload["checksums"][filename] = _sha(path.read_bytes())
    payload["artifact_set_sha256"] = admission_module.canonical_sha256_v2(payload["checksums"])
    checksums_path.write_bytes(_json_bytes(payload))


def _ready_artifacts():
    authority = inspect_current_production_authority_v2()
    component_by_source = {
        component.source: component for component in authority.registry.components
    }
    verifier_by_source = {
        "code": "code-release-verify-only-v2",
        "codex": "codex-correction-verify-only-v1",
        "experiment": "experiment-baseline-verify-only-v1",
        "notebook": "notebook-baseline-verify-only-v1",
        "document": "document-baseline-verify-only-v1",
        "workspace": "workspace-baseline-verify-only-v1",
    }
    artifact_uri_by_source = {
        source: admission_module._ALLOWED_ARTIFACT_URIS[source][0]
        for source in (
            "code",
            "codex",
            "experiment",
            "notebook",
            "document",
            "workspace",
        )
    }
    return authority, tuple(
        ImmutableSourceArtifactReferenceV2(
            source=source,
            artifact_uri=artifact_uri_by_source[source],
            artifact_set_sha256="sha256:" + f"{index + 101:064x}",
            source_authority_sha256=admission_module.canonical_sha256_v2(
                component_by_source[source].model_dump(mode="json")
            ),
            verifier=build_verifier_result_v2(
                verifier_id=verifier_by_source[source],
                status=VerificationStatus.VERIFIED,
                artifact_set_sha256="sha256:" + f"{index + 101:064x}",
                portable=True,
                verify_only=True,
                retrieval_executed=False,
                database_accessed=False,
                network_accessed=False,
                quality_qualified=True,
                production_observation=True,
            ),
        )
        for index, source in enumerate(
            ("code", "codex", "experiment", "notebook", "document", "workspace"),
            start=1,
        )
    )


def _ready_gates() -> tuple[ReleaseGateEvidenceV2, ...]:
    return tuple(
        build_gate_evidence_v2(
            gate=gate,
            status=VerificationStatus.VERIFIED,
            evidence_uri=f"evaluation-evidence://rag-release/{gate}/{index:032x}",
            evidence_sha256="sha256:" + f"{index + 501:064x}",
            qualified=True,
            production_observation=True,
            reason_code="qualified-current",
        )
        for index, gate in enumerate(
            ("multisource", "security", "performance", "rollback"), start=1
        )
    )


def _quality_hold_package(tmp_path: Path, name: str = "release-admission") -> Path:
    authority = inspect_current_production_authority_v2()
    admission = build_current_repository_quality_hold_v2()
    return write_release_admission_package_v2(
        output_dir=tmp_path / name,
        artifact_root=tmp_path,
        authority=authority,
        admission=admission,
    )


def _promotion_package(tmp_path: Path):
    authority, artifacts = _ready_artifacts()
    gates = _ready_gates()
    admission = evaluate_release_admission_v2(
        authority=authority,
        artifacts=artifacts,
        gates=gates,
    )
    package = write_release_admission_package_v2(
        output_dir=tmp_path / "promotion-admission",
        artifact_root=tmp_path,
        authority=authority,
        admission=admission,
        artifacts=artifacts,
        gates=gates,
    )
    return package, artifacts, gates


def test_fixed_authority_registry_is_current_and_not_runtime_learned() -> None:
    registry = fixed_production_authority_registry_v2()
    report = inspect_current_production_authority_v2()
    assert registry.registry_version == "rag-production-authority-v4"
    assert tuple(component.source for component in registry.components) == (
        "code",
        "codex",
        "experiment",
        "notebook",
        "document",
        "workspace",
    )
    assert all("0" * 64 not in component.source_sha256 for component in registry.components)
    assert report.all_current is True
    assert all(
        row.status is AuthorityFreshness.CURRENT and row.reason_code == "current"
        for row in report.sources
    )
    assert verify_current_production_authority_v2() == report


def test_workspace_authority_handles_descriptor_kinds_and_generic_alias_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tempfile_module = admission_module.importlib.import_module("tempfile")
    temporary_directory = tempfile_module.TemporaryDirectory
    generic_alias_descriptor = vars(temporary_directory)["__class_getitem__"]
    assert isinstance(generic_alias_descriptor, classmethod)
    assert generic_alias_descriptor.__func__ is types.GenericAlias
    assert admission_module._class_callable_code_payload(generic_alias_descriptor) == {
        "kind": "classmethod",
        "generic_alias": "types.GenericAlias",
    }

    with monkeypatch.context() as patch:
        patch.setattr(
            temporary_directory,
            "__class_getitem__",
            staticmethod(types.GenericAlias),
        )
        changed_kind = inspect_current_production_authority_v2().sources[-1]
        assert changed_kind.status is AuthorityFreshness.STALE
        assert changed_kind.reason_code == "reviewed-digest-mismatch"

    with monkeypatch.context() as patch:
        patch.setattr(temporary_directory, "__class_getitem__", classmethod(object()))
        unsupported = inspect_current_production_authority_v2().sources[-1]
        assert unsupported.status is AuthorityFreshness.UNAVAILABLE
        assert unsupported.reason_code == "component-unavailable"


def test_authority_rejects_export_replacement_and_same_code_different_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previously_current = inspect_current_production_authority_v2()
    module = admission_module.importlib.import_module("evidence_rag.rag.sources.code.platform_v2")
    original = module.CodePlatformIntegration.search
    globals_copy = dict(original.__globals__)
    referenced = next(name for name in original.__code__.co_names if name in globals_copy)
    globals_copy[referenced] = object()
    clone = types.FunctionType(
        original.__code__,
        globals_copy,
        name=original.__name__,
        argdefs=original.__defaults__,
        closure=original.__closure__,
    )
    clone.__module__ = original.__module__
    clone.__qualname__ = original.__qualname__
    clone.__kwdefaults__ = original.__kwdefaults__
    monkeypatch.setattr(module.CodePlatformIntegration, "search", clone)

    report = inspect_current_production_authority_v2()
    code = report.sources[0]
    assert code.status is AuthorityFreshness.STALE
    assert code.reason_code == "authority-invalid"
    with pytest.raises(ProductionAuthorityError, match="stale"):
        verify_current_production_authority_v2()
    unavailable_gates = tuple(
        build_gate_evidence_v2(
            gate=gate,
            status=VerificationStatus.UNAVAILABLE,
            evidence_uri=None,
            evidence_sha256=None,
            qualified=False,
            production_observation=False,
            reason_code="production-evidence-unavailable",
        )
        for gate in ("multisource", "security", "performance", "rollback")
    )
    with pytest.raises(ProductionAuthorityError, match="current runtime"):
        evaluate_release_admission_v2(
            authority=previously_current,
            artifacts=(),
            gates=unavailable_gates,
        )


def test_authority_binds_builtin_identity_and_unsupported_binding_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_max = admission_module.builtins.max
    monkeypatch.setattr(admission_module.builtins, "max", admission_module.builtins.min)
    builtin_report = inspect_current_production_authority_v2()
    monkeypatch.setattr(admission_module.builtins, "max", original_max)
    workspace = builtin_report.sources[-1]
    assert builtin_report.all_current is False
    assert workspace.status is not AuthorityFreshness.CURRENT

    module = admission_module.importlib.import_module(
        "evidence_rag.rag.sources.workspace.runtime_v2"
    )
    monkeypatch.setitem(module.WorkspaceSourceRuntimeV2.search.__globals__, "UTC", object())
    unavailable_report = inspect_current_production_authority_v2()
    assert unavailable_report.all_current is False
    assert unavailable_report.sources[-1].status is AuthorityFreshness.UNAVAILABLE
    assert unavailable_report.sources[-1].reason_code == "component-unavailable"


def test_workspace_authority_rejects_helper_wrapper_and_subclass_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = admission_module.importlib.import_module(
        "evidence_rag.rag.sources.workspace.runtime_v2"
    )
    original_helper = module._snapshot_generation

    def helper_wrapper(*args: object, **kwargs: object) -> object:
        return original_helper(*args, **kwargs)

    helper_wrapper.__module__ = original_helper.__module__
    helper_wrapper.__qualname__ = original_helper.__qualname__
    with monkeypatch.context() as patch:
        patch.setattr(module, "_snapshot_generation", helper_wrapper)
        wrapped = inspect_current_production_authority_v2().sources[-1]
        assert wrapped.status is not AuthorityFreshness.CURRENT

    helper_globals_copy = dict(original_helper.__globals__)
    helper_copy = types.FunctionType(
        original_helper.__code__,
        helper_globals_copy,
        name=original_helper.__name__,
        argdefs=original_helper.__defaults__,
        closure=original_helper.__closure__,
    )
    helper_copy.__module__ = original_helper.__module__
    helper_copy.__qualname__ = original_helper.__qualname__
    helper_copy.__kwdefaults__ = original_helper.__kwdefaults__
    with monkeypatch.context() as patch:
        patch.setattr(module, "_snapshot_generation", helper_copy)
        copied = inspect_current_production_authority_v2().sources[-1]
        assert copied.status is AuthorityFreshness.STALE
        assert copied.reason_code == "authority-invalid"

    original_retriever = module.WorkspaceRetrieverV2

    class RetrieverSubclass(original_retriever):
        pass

    RetrieverSubclass.__module__ = original_retriever.__module__
    RetrieverSubclass.__qualname__ = original_retriever.__qualname__
    with monkeypatch.context() as patch:
        patch.setattr(module, "WorkspaceRetrieverV2", RetrieverSubclass)
        subclassed = inspect_current_production_authority_v2().sources[-1]
        assert subclassed.status is not AuthorityFreshness.CURRENT


def test_current_repository_truth_is_quality_hold_without_quality_generation() -> None:
    result = build_current_repository_quality_hold_v2()
    assert result.default_engine == "v1"
    assert result.decision is ReleaseDecision.QUALITY_HOLD
    assert result.quality_hold is True
    assert result.promotion_performed is False
    assert all(row.availability is not EvidenceAvailability.CURRENT for row in result.sources)
    assert all(row.quality_qualified is False for row in result.sources)
    assert all(row.production_observation is False for row in result.sources)
    assert all(gate.status is VerificationStatus.UNAVAILABLE for gate in result.gates)


def test_promotion_requires_every_source_and_global_gate() -> None:
    authority, artifacts = _ready_artifacts()
    gates = _ready_gates()
    eligible = evaluate_release_admission_v2(
        authority=authority,
        artifacts=artifacts,
        gates=gates,
    )
    assert eligible.decision is ReleaseDecision.QUALITY_HOLD
    assert eligible.quality_hold is True
    assert "reviewed-release-authority-unavailable" in eligible.blockers
    assert all(row.availability is not EvidenceAvailability.CURRENT for row in eligible.sources)
    assert all(gate.status is VerificationStatus.UNAVAILABLE for gate in eligible.gates)
    assert eligible.default_engine == "v1"
    assert eligible.promotion_performed is False

    stale_reference = artifacts[0].model_copy(
        update={"source_authority_sha256": "sha256:" + "f" * 64}
    )
    held = evaluate_release_admission_v2(
        authority=authority,
        artifacts=(stale_reference, *artifacts[1:]),
        gates=gates,
    )
    assert held.decision is ReleaseDecision.QUALITY_HOLD
    assert held.sources[0].availability is EvidenceAvailability.STALE
    assert "source-code-stale" in held.blockers

    held_gate = build_gate_evidence_v2(
        gate="rollback",
        status=VerificationStatus.VERIFIED,
        evidence_uri=gates[-1].evidence_uri,
        evidence_sha256=gates[-1].evidence_sha256,
        qualified=False,
        production_observation=False,
        reason_code="rollback-unavailable",
    )
    held = evaluate_release_admission_v2(
        authority=authority,
        artifacts=artifacts,
        gates=(*gates[:-1], held_gate),
    )
    assert held.decision is ReleaseDecision.QUALITY_HOLD
    assert "gate-rollback-quality" in held.blockers
    assert "gate-rollback-production-observation" in held.blockers


def test_artifact_references_are_explicitly_allowlisted_and_cross_bound() -> None:
    _, artifacts = _ready_artifacts()
    payload = artifacts[0].model_dump(mode="json")
    payload["artifact_uri"] = "/Users/operator/evals/code/run"
    with pytest.raises(ValueError, match="allowlisted"):
        ImmutableSourceArtifactReferenceV2.model_validate(payload)

    payload = artifacts[0].model_dump(mode="json")
    payload["artifact_uri"] = (
        "evaluation-run://project-workspace-baseline-v1/00000000000000000000000000000001"
    )
    with pytest.raises(ValueError, match="allowlisted"):
        ImmutableSourceArtifactReferenceV2.model_validate(payload)

    payload = artifacts[0].model_dump(mode="json")
    payload["verifier"]["artifact_set_sha256"] = "sha256:" + "e" * 64
    payload["verifier"]["result_sha256"] = admission_module.canonical_sha256_v2(
        {key: value for key, value in payload["verifier"].items() if key != "result_sha256"}
    )
    with pytest.raises(ValueError, match="set digest mismatch"):
        ImmutableSourceArtifactReferenceV2.model_validate(payload)


def test_package_is_canonical_portable_exact_files_and_copy_verifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _quality_hold_package(tmp_path)
    copied = tmp_path / "copied-package"
    shutil.copytree(package, copied)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("release admission verification executed a forbidden subsystem")

    monkeypatch.setattr("sqlite3.connect", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    result = verify_release_admission_package_v2(package)
    assert result.status == "VERIFIED_QUALITY_HOLD"
    assert result.decision is ReleaseDecision.QUALITY_HOLD
    assert result.portable is True
    assert result.exact_files is True
    assert result.authority_attested is False
    assert result.authority_attestation_sha256 is None
    assert result.verify_only is True
    assert result.retrieval_executed is False
    assert result.database_accessed is False
    assert result.network_accessed is False
    assert tuple(sorted(path.name for path in package.iterdir())) == tuple(
        sorted(admission_module._PACKAGE_FILES)
    )
    assert verify_release_admission_package_v2(copied) == result


def test_caller_supplied_trust_is_rejected_not_treated_as_authority(
    tmp_path: Path,
) -> None:
    package, artifacts, gates = _promotion_package(tmp_path)
    standalone = verify_release_admission_package_v2(package)
    assert standalone.status == "VERIFIED_QUALITY_HOLD"
    assert standalone.decision is ReleaseDecision.QUALITY_HOLD

    with pytest.raises(ReleaseAdmissionPackageError, match="caller-supplied"):
        verify_release_admission_package_v2(
            package,
            trusted_artifacts=artifacts,
            trusted_gates=gates,
        )

    wrong_artifacts = (
        artifacts[0].model_copy(update={"artifact_set_sha256": "sha256:" + "f" * 64}),
        *artifacts[1:],
    )
    with pytest.raises(ReleaseAdmissionPackageError, match="caller-supplied"):
        verify_release_admission_package_v2(
            package,
            trusted_artifacts=wrong_artifacts,
            trusted_gates=gates,
        )


def test_reviewed_release_evidence_authority_is_fixed_empty_and_not_caller_learned() -> None:
    registry = fixed_reviewed_release_evidence_authority_registry_v2()
    assert registry.sources == ()
    assert registry.gates == ()
    assert admission_module._ALLOWED_ARTIFACT_URIS == {
        "code": ("evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061",),
        "codex": ("evaluation-correction://project-codex-xb0-v1/1ccecfa1c9667d0f716c709d1175d570",),
        "experiment": (
            "evaluation-run://project-experiment-eb0-v1/085799235df74bcdc4fc56c0c17f36e7",
        ),
        "notebook": ("evaluation-run://project-notebook-nb0-v1/b9278f79dc7d42a8a61c25c45b1d7d3e",),
        "document": ("evaluation-run://project-document-db0-v1/b6c5c5e8f21446d5a0038d23ffed5acf",),
        "workspace": (
            "evaluation-run://project-workspace-wb0-v1/363181f5d05847a0aa98fbbee83c4373",
        ),
    }

    authority, artifacts = _ready_artifacts()
    gates = _ready_gates()
    caller_self_signed = evaluate_release_admission_v2(
        authority=authority,
        artifacts=artifacts,
        gates=gates,
    )
    assert caller_self_signed.decision is ReleaseDecision.QUALITY_HOLD
    assert caller_self_signed.default_engine == "v1"
    assert "reviewed-release-authority-unavailable" in caller_self_signed.blockers


def test_reviewed_attestation_is_bound_to_subject_parent_and_evidence_identity() -> None:
    source_payload = {
        "source": "code",
        "artifact_uri": admission_module._ALLOWED_ARTIFACT_URIS["code"][0],
        "artifact_set_sha256": "sha256:" + "1" * 64,
        "source_authority_sha256": "sha256:" + "2" * 64,
        "verifier_id": "code-release-verify-only-v2",
        "verifier_authority_sha256": admission_module._VERIFIER_AUTHORITIES[
            "code-release-verify-only-v2"
        ],
        "verifier_result_sha256": "sha256:" + "3" * 64,
        "authority_subject_sha256": "sha256:" + "3" * 64,
        "parent_authority_sha256": "sha256:" + "2" * 64,
    }
    source_payload["authority_attestation_sha256"] = admission_module.canonical_sha256_v2(
        {
            "authority": "reviewed-source-release-attestation-v2",
            "source": source_payload["source"],
            "artifact_uri": source_payload["artifact_uri"],
            "artifact_set_sha256": source_payload["artifact_set_sha256"],
            "verifier_id": source_payload["verifier_id"],
            "verifier_authority_sha256": source_payload["verifier_authority_sha256"],
            "authority_subject_sha256": source_payload["authority_subject_sha256"],
            "parent_authority_sha256": source_payload["parent_authority_sha256"],
        }
    )
    ReviewedSourceEvidenceAuthorityV2.model_validate(source_payload)
    rebound_source = {**source_payload, "artifact_set_sha256": "sha256:" + "4" * 64}
    with pytest.raises(ValueError, match="attestation digest"):
        ReviewedSourceEvidenceAuthorityV2.model_validate(rebound_source)

    gate_payload = {
        "gate": "security",
        "evidence_kind": "rag-security-release-evidence-v2",
        "evidence_uri": "evaluation-evidence://rag-release/security/" + "1" * 32,
        "evidence_sha256": "sha256:" + "5" * 64,
        "verifier_id": "rag-security-release-verify-only-v2",
        "verifier_authority_sha256": "sha256:" + "6" * 64,
        "result_sha256": "sha256:" + "7" * 64,
        "authority_subject_sha256": "sha256:" + "7" * 64,
        "parent_authority_sha256": "sha256:" + "6" * 64,
    }
    gate_payload["authority_attestation_sha256"] = admission_module.canonical_sha256_v2(
        {
            "authority": "reviewed-gate-release-attestation-v2",
            "gate": gate_payload["gate"],
            "evidence_kind": gate_payload["evidence_kind"],
            "evidence_uri": gate_payload["evidence_uri"],
            "evidence_sha256": gate_payload["evidence_sha256"],
            "verifier_id": gate_payload["verifier_id"],
            "verifier_authority_sha256": gate_payload["verifier_authority_sha256"],
            "authority_subject_sha256": gate_payload["authority_subject_sha256"],
            "parent_authority_sha256": gate_payload["parent_authority_sha256"],
        }
    )
    ReviewedGateEvidenceAuthorityV2.model_validate(gate_payload)
    rebound_gate = {
        **gate_payload,
        "evidence_uri": "evaluation-evidence://rag-release/security/" + "2" * 32,
    }
    with pytest.raises(ValueError, match="attestation digest"):
        ReviewedGateEvidenceAuthorityV2.model_validate(rebound_gate)


def test_outer_quality_hold_cannot_be_resigned_as_promotion(
    tmp_path: Path,
) -> None:
    package, artifacts, gates = _promotion_package(tmp_path)
    admission = json.loads((package / "admission.json").read_bytes())
    admission["sources"] = [
        {
            "source": item.source,
            "availability": "CURRENT",
            "authority_status": "CURRENT",
            "artifact_uri": item.artifact_uri,
            "artifact_set_sha256": item.artifact_set_sha256,
            "verifier_id": item.verifier.verifier_id,
            "quality_qualified": True,
            "production_observation": True,
            "reason_code": "qualified-current",
        }
        for item in artifacts
    ]
    admission["gates"] = [item.model_dump(mode="json") for item in gates]
    admission["decision"] = "PROMOTION_ELIGIBLE"
    admission["quality_hold"] = False
    admission["blockers"] = []
    admission["content_sha256"] = admission_module.canonical_sha256_v2(
        {key: value for key, value in admission.items() if key != "content_sha256"}
    )
    (package / "admission.json").write_bytes(_json_bytes(admission))
    _resign(package, "admission.json")

    manifest = json.loads((package / "manifest.json").read_bytes())
    manifest["admission_sha256"] = admission["content_sha256"]
    (package / "manifest.json").write_bytes(_json_bytes(manifest))
    _resign(package, "manifest.json")

    with pytest.raises(ReleaseAdmissionPackageError, match="contract|re-evaluation"):
        verify_release_admission_package_v2(package)


def test_package_recomputes_raw_inputs_and_rejects_self_reported_state(
    tmp_path: Path,
) -> None:
    package = _quality_hold_package(tmp_path)
    sources = json.loads((package / "sources.json").read_bytes())
    assert sources == []

    admission = json.loads((package / "admission.json").read_bytes())
    admission["sources"][0]["availability"] = "CURRENT"
    admission["sources"][0]["artifact_uri"] = admission_module._ALLOWED_ARTIFACT_URIS["code"][0]
    admission["sources"][0]["artifact_set_sha256"] = "sha256:" + "1" * 64
    admission["sources"][0]["verifier_id"] = "code-release-verify-only-v2"
    admission["sources"][0]["quality_qualified"] = True
    admission["sources"][0]["production_observation"] = True
    admission["sources"][0]["reason_code"] = "qualified-current"
    unsigned = {key: value for key, value in admission.items() if key != "content_sha256"}
    admission["content_sha256"] = admission_module.canonical_sha256_v2(unsigned)
    (package / "admission.json").write_bytes(_json_bytes(admission))
    _resign(package, "admission.json")
    with pytest.raises(ReleaseAdmissionPackageError):
        verify_release_admission_package_v2(package)


def test_source_and_gate_authorities_reject_forged_uri_verifier_and_result() -> None:
    _, artifacts = _ready_artifacts()
    payload = artifacts[0].model_dump(mode="json")
    payload["artifact_uri"] = (
        "evaluation-run://project-code-golden-v2/ffffffffffffffffffffffffffffffff"
    )
    with pytest.raises(ValueError, match="allowlisted"):
        ImmutableSourceArtifactReferenceV2.model_validate(payload)

    payload = artifacts[0].model_dump(mode="json")
    payload["verifier"]["verifier_id"] = "forged-verify-only-v2"
    payload["verifier"]["result_sha256"] = admission_module.canonical_sha256_v2(
        {key: value for key, value in payload["verifier"].items() if key != "result_sha256"}
    )
    with pytest.raises(ValueError, match="authority"):
        ImmutableSourceArtifactReferenceV2.model_validate(payload)

    gate_payload = _ready_gates()[0].model_dump(mode="json")
    gate_payload["qualified"] = False
    with pytest.raises(ValueError, match="result digest"):
        ReleaseGateEvidenceV2.model_validate(gate_payload)


@pytest.mark.parametrize(
    "attack",
    ("tamper", "delete", "extra", "symlink", "secret", "absolute", "database", "nonfinite"),
)
def test_package_rejects_tamper_security_and_membership_attacks(
    tmp_path: Path,
    attack: str,
) -> None:
    target = _quality_hold_package(tmp_path, f"attack-{attack}")
    if attack == "tamper":
        payload = json.loads((target / "admission.json").read_bytes())
        payload["default_engine"] = "v2"
        (target / "admission.json").write_bytes(_json_bytes(payload))
    elif attack == "delete":
        (target / "security.json").unlink()
    elif attack == "extra":
        (target / "evaluation.sqlite3-wal").write_bytes(b"not-a-database")
    elif attack == "symlink":
        path = target / "security.json"
        alias = tmp_path / "security-alias.json"
        alias.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(alias)
    elif attack in {"secret", "absolute", "database"}:
        payload = json.loads((target / "gates.json").read_bytes())
        payload[0]["injected"] = {
            "secret": "password=top-secret-value",
            "absolute": "/Users/operator/private",
            "database": "evaluation.sqlite3-shm",
        }[attack]
        (target / "gates.json").write_bytes(_json_bytes(payload))
        _resign(target, "gates.json")
    else:
        raw = (target / "gates.json").read_text(encoding="utf-8")
        raw = raw.replace('"qualified":false', '"qualified":NaN', 1)
        (target / "gates.json").write_text(raw, encoding="utf-8")

    with pytest.raises((ReleaseAdmissionPackageError, ProductionAuthorityError)):
        verify_release_admission_package_v2(target)


@pytest.mark.parametrize(
    "unsafe",
    (
        "/srv/private/result.json",
        "/opt/data/cache",
        r"C:\\private\\result.json",
        r"\\server\\share\\result.json",
        "//server/share/result.json",
        "%255C%255Cserver%255Cshare%255Cresult.json",
        "formal.db",
        "formal.sqlite",
        "formal.sqlite3",
        "formal.sqlite3-wal",
        "formal.sqlite3-shm",
        "cache.pyc",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "sk-proj-ABCDEFGHIJKLMNOPQRSTUV",
        "AKIA1234567890ABCDEF",
        "eyJabcdefghijk.abcdefghijkl.abcdefghijkl",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "password",
    ),
)
def test_canonical_json_scanner_rejects_paths_databases_and_single_token_secrets(
    tmp_path: Path,
    unsafe: str,
) -> None:
    package = _quality_hold_package(tmp_path)
    payload = json.loads((package / "gates.json").read_bytes())
    payload[0]["injected"] = unsafe
    (package / "gates.json").write_bytes(_json_bytes(payload))
    _resign(package, "gates.json")
    with pytest.raises(ReleaseAdmissionPackageError, match="security"):
        verify_release_admission_package_v2(package)


def test_canonical_json_scanner_checks_keys_but_not_numeric_or_boolean_values() -> None:
    assert (
        admission_module._security_findings(
            {
                "safe.json": {
                    "large_numeric": 4111111111111111,
                    "finite": 0.00000000000000001,
                    "enabled": True,
                    "uri": "evaluation-run://project-code-golden-v2/"
                    "5a92eafdff5d49e6aae8bb55fdc14061",
                    "sha256": "sha256:" + "a" * 64,
                    "uuid": "019fac2b-517a-79b3-8c1a-3a0625cd7ee5",
                }
            }
        )
        == ()
    )
    assert admission_module._security_findings({"unsafe.json": {"query": "benign"}}) == (
        "unsafe.json:secret",
    )


def test_writer_runs_security_scan_before_creating_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = inspect_current_production_authority_v2()
    admission = build_current_repository_quality_hold_v2()
    original = admission_module.ReleaseAdmissionSecurityV2.model_dump

    def injected(self, *args, **kwargs):
        payload = original(self, *args, **kwargs)
        payload["note"] = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        return payload

    monkeypatch.setattr(
        admission_module.ReleaseAdmissionSecurityV2,
        "model_dump",
        injected,
    )
    output = tmp_path / "unsafe-write"
    with pytest.raises(ReleaseAdmissionPackageError, match="unsafe"):
        write_release_admission_package_v2(
            output_dir=output,
            artifact_root=tmp_path,
            authority=authority,
            admission=admission,
        )
    assert output.exists() is False


def test_stale_authority_stops_package_before_output_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = inspect_current_production_authority_v2()
    admission = build_current_repository_quality_hold_v2()
    module = admission_module.importlib.import_module(
        "evidence_rag.rag.sources.document.runtime_v2"
    )
    original = module.DocumentSourceRuntimeV2.search

    def replacement(*args: object, **kwargs: object) -> object:
        return original(*args, **kwargs)

    monkeypatch.setattr(module.DocumentSourceRuntimeV2, "search", replacement)
    output = tmp_path / "must-not-exist"
    with pytest.raises(ProductionAuthorityError, match="not current"):
        write_release_admission_package_v2(
            output_dir=output,
            artifact_root=tmp_path,
            authority=authority,
            admission=admission,
        )
    assert output.exists() is False


def test_package_rejects_symlink_output_root(tmp_path: Path) -> None:
    authority = inspect_current_production_authority_v2()
    admission = build_current_repository_quality_hold_v2()
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(ReleaseAdmissionPackageError, match="symlink"):
        write_release_admission_package_v2(
            output_dir=linked / "artifact",
            artifact_root=linked,
            authority=authority,
            admission=admission,
        )
