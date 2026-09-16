from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.sources.v2.contracts import (
    REASON_POLICY,
    DigestDomain,
    OutwardDisposition,
    RawV2ContractError,
    RawV2Reason,
)
from evidence_rag.sources.v2.policy import (
    MAX_REQUESTER_ACL_REFS,
    PUBLIC_ACL_REF,
    ObjectVisibility,
    ProjectAclAuthority,
    RawV2ProjectPolicyAdapter,
    RequesterVisibilityScope,
    derive_request_visibility_scope_sha256,
)


@dataclass
class _Authority:
    projects: dict[str, dict[str, str]] = field(
        default_factory=lambda: {
            "project-a": {
                "source-public": "public",
                "source-private": "acl-alpha",
            },
            "project-b": {
                "source-public": "public",
                "source-private": "acl-alpha",
            },
        }
    )
    grants: dict[tuple[str, str], tuple[str, ...] | None] = field(
        default_factory=lambda: {
            ("project-a", "principal-full"): (
                "e\u0301quipe",
                "acl-alpha",
                "acl-alpha",
            ),
            ("project-a", "principal-public"): (),
            ("project-a", "principal-denied"): None,
            ("project-b", "principal-full"): ("acl-alpha",),
            ("project-b", "principal-public"): (),
        }
    )

    def project_exists(self, *, project_id: str) -> bool:
        return project_id in self.projects

    def resolve_object_acl_ref(self, *, project_id: str, source_acl_ref: str) -> str | None:
        return self.projects.get(project_id, {}).get(source_acl_ref)

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        return self.grants.get((project_id, principal_id))


def _adapter(authority: ProjectAclAuthority | None = None) -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=authority or _Authority())


def _assert_reason(expected: RawV2Reason, call, *args, **kwargs) -> RawV2ContractError:
    with pytest.raises(RawV2ContractError) as captured:
        call(*args, **kwargs)
    assert captured.value.reason is expected
    assert captured.value.policy == REASON_POLICY[expected]
    return captured.value


def test_request_scope_uses_a_distinct_frozen_domain_and_golden_literal() -> None:
    assert DigestDomain.REQUEST_VISIBILITY_SCOPE.value == "request-visibility-scope-v2"
    scope = _adapter().resolve_requester_scope(
        project_id="project-a",
        principal_id="principal-full",
    )
    assert scope.requester_acl_refs == ("acl-alpha", "équipe")
    assert scope.request_visibility_scope_sha256 == (
        "sha256:a23d3bcf3d2f309ee2b803295343725d63e603e7878364c8de31026daa54a6b2"
    )
    assert derive_request_visibility_scope_sha256(scope) == (
        scope.request_visibility_scope_sha256
    )

    object_visibility = _adapter().resolve_object_visibility(
        project_id="project-a",
        source_acl_ref="source-private",
    )
    assert object_visibility.visibility_partition_sha256 == (
        "sha256:adb435fdc679a253dd5e936ce389fc1c7c10b6ce5acbad66d323b1b41fe2bc92"
    )
    assert object_visibility.visibility_partition_sha256 != (
        scope.request_visibility_scope_sha256
    )


def test_visibility_values_are_frozen_computed_outputs_not_claim_fields() -> None:
    assert "visibility_partition_sha256" not in ObjectVisibility.model_fields
    assert (
        "request_visibility_scope_sha256"
        not in RequesterVisibilityScope.model_fields
    )
    with pytest.raises(ValidationError):
        ObjectVisibility.model_validate(
            {
                "project_id": "project-a",
                "acl_ref": "public",
                "visibility_partition_sha256": "sha256:" + "0" * 64,
            }
        )
    with pytest.raises(ValidationError):
        RequesterVisibilityScope.model_validate(
            {
                "project_id": "project-a",
                "requester_acl_refs": (),
                "request_visibility_scope_sha256": "sha256:" + "0" * 64,
            }
        )
    with pytest.raises(ValidationError):
        RequesterVisibilityScope(
            project_id="project-a",
            requester_acl_refs=("acl-z", "acl-a"),
        )
    scope = RequesterVisibilityScope(
        project_id="project-a",
        requester_acl_refs=("acl-a", "acl-z"),
    )
    with pytest.raises(ValidationError):
        scope.model_copy(update={"requester_acl_refs": ("acl-z", "acl-a")})
    with pytest.raises(ValidationError):
        ObjectVisibility(project_id="project-a", acl_ref="public").project_id = "project-b"  # type: ignore[misc]

    parameters = inspect.signature(
        RawV2ProjectPolicyAdapter.resolve_requester_scope
    ).parameters
    assert "requester_acl_refs" not in parameters
    assert "request_visibility_scope_sha256" not in parameters
    assert "visibility_partition_sha256" not in parameters


@pytest.mark.parametrize("project_id", ["project-a", "project-b"])
def test_same_project_public_and_private_matrix(project_id: str) -> None:
    adapter = _adapter()
    public_scope = adapter.resolve_requester_scope(
        project_id=project_id,
        principal_id="principal-public",
    )
    full_scope = adapter.resolve_requester_scope(
        project_id=project_id,
        principal_id="principal-full",
    )
    public_object = adapter.resolve_object_visibility(
        project_id=project_id,
        source_acl_ref="source-public",
    )
    private_object = adapter.resolve_object_visibility(
        project_id=project_id,
        source_acl_ref="source-private",
    )

    assert public_object.acl_ref == PUBLIC_ACL_REF
    assert public_scope.requester_acl_refs == ()
    assert (
        adapter.authorize(
            scope=public_scope,
            object_visibility=public_object,
            stored_visibility_partition_sha256=public_object.visibility_partition_sha256,
        )
        is public_object
    )
    assert (
        adapter.authorize(
            scope=full_scope,
            object_visibility=private_object,
            stored_visibility_partition_sha256=private_object.visibility_partition_sha256,
        )
        is private_object
    )
    denied = _assert_reason(
        RawV2Reason.ACL_DENIED,
        adapter.authorize,
        scope=public_scope,
        object_visibility=private_object,
        stored_visibility_partition_sha256=private_object.visibility_partition_sha256,
    )
    assert denied.policy.http_status == 404
    assert denied.policy.outward_disposition is OutwardDisposition.EVIDENCE_UNAVAILABLE


def test_equal_acl_text_is_project_scoped_for_object_and_request_visibility() -> None:
    adapter = _adapter()
    object_a = adapter.resolve_object_visibility(
        project_id="project-a", source_acl_ref="source-private"
    )
    object_b = adapter.resolve_object_visibility(
        project_id="project-b", source_acl_ref="source-private"
    )
    scope_a = adapter.resolve_requester_scope(
        project_id="project-a", principal_id="principal-public"
    )
    scope_b = adapter.resolve_requester_scope(
        project_id="project-b", principal_id="principal-public"
    )
    assert object_a.acl_ref == object_b.acl_ref == "acl-alpha"
    assert object_a.visibility_partition_sha256 != object_b.visibility_partition_sha256
    assert scope_a.requester_acl_refs == scope_b.requester_acl_refs == ()
    assert scope_a.request_visibility_scope_sha256 != scope_b.request_visibility_scope_sha256

    _assert_reason(
        RawV2Reason.PROJECT_MISMATCH,
        adapter.authorize,
        scope=scope_a,
        object_visibility=object_b,
        stored_visibility_partition_sha256=object_b.visibility_partition_sha256,
    )


def test_public_never_bypasses_cross_project_check() -> None:
    adapter = _adapter()
    scope_a = adapter.resolve_requester_scope(
        project_id="project-a", principal_id="principal-public"
    )
    public_b = adapter.resolve_object_visibility(
        project_id="project-b", source_acl_ref="source-public"
    )
    _assert_reason(
        RawV2Reason.PROJECT_MISMATCH,
        adapter.authorize,
        scope=scope_a,
        object_visibility=public_b,
        stored_visibility_partition_sha256=public_b.visibility_partition_sha256,
    )


def test_stored_partition_is_verified_before_acl_membership() -> None:
    adapter = _adapter()
    scope = adapter.resolve_requester_scope(
        project_id="project-a", principal_id="principal-full"
    )
    object_a = adapter.resolve_object_visibility(
        project_id="project-a", source_acl_ref="source-private"
    )
    copied_from_b = adapter.resolve_object_visibility(
        project_id="project-b", source_acl_ref="source-private"
    ).visibility_partition_sha256
    _assert_reason(
        RawV2Reason.VISIBILITY_MISMATCH,
        adapter.authorize,
        scope=scope,
        object_visibility=object_a,
        stored_visibility_partition_sha256=copied_from_b,
    )
    _assert_reason(
        RawV2Reason.VISIBILITY_MISMATCH,
        adapter.authorize,
        scope=scope,
        object_visibility=object_a,
        stored_visibility_partition_sha256=scope.request_visibility_scope_sha256,
    )


def test_unknown_project_and_missing_grant_fail_closed() -> None:
    adapter = _adapter()
    _assert_reason(
        RawV2Reason.PROJECT_MISMATCH,
        adapter.resolve_requester_scope,
        project_id="project-missing",
        principal_id="principal-full",
    )
    _assert_reason(
        RawV2Reason.PROJECT_MISMATCH,
        adapter.resolve_object_visibility,
        project_id="project-missing",
        source_acl_ref="source-public",
    )
    _assert_reason(
        RawV2Reason.ACL_DENIED,
        adapter.resolve_requester_scope,
        project_id="project-a",
        principal_id="principal-denied",
    )
    _assert_reason(
        RawV2Reason.ACL_DENIED,
        adapter.resolve_object_visibility,
        project_id="project-a",
        source_acl_ref="source-unregistered",
    )


def test_authority_results_are_nfc_sorted_deduplicated_and_bounded() -> None:
    authority = _Authority()
    adapter = _adapter(authority)
    scope = adapter.resolve_requester_scope(
        project_id="project-a", principal_id="principal-full"
    )
    assert scope.requester_acl_refs == ("acl-alpha", "équipe")

    authority.grants[("project-a", "principal-full")] = tuple(
        f"acl-{index:03d}" for index in range(MAX_REQUESTER_ACL_REFS + 1)
    )
    _assert_reason(
        RawV2Reason.CONTRACT_SIZE_EXCEEDED,
        adapter.resolve_requester_scope,
        project_id="project-a",
        principal_id="principal-full",
    )


@pytest.mark.parametrize(
    "grants",
    [
        ["acl-alpha"],
        ("acl-alpha", 7),
        ("acl-control\n",),
        ("x" * 241,),
    ],
)
def test_invalid_authority_grant_shapes_fail_closed(grants: object) -> None:
    authority = _Authority()
    authority.grants[("project-a", "principal-full")] = grants  # type: ignore[assignment]
    expected = (
        RawV2Reason.CONTRACT_SIZE_EXCEEDED
        if grants == ("x" * 241,)
        else RawV2Reason.CANONICALIZATION_MISMATCH
    )
    _assert_reason(
        expected,
        _adapter(authority).resolve_requester_scope,
        project_id="project-a",
        principal_id="principal-full",
    )


def test_invalid_authority_boolean_and_object_acl_fail_closed() -> None:
    authority = _Authority()
    authority.project_exists = lambda *, project_id: 1  # type: ignore[method-assign,return-value]
    _assert_reason(
        RawV2Reason.CANONICALIZATION_MISMATCH,
        _adapter(authority).resolve_requester_scope,
        project_id="project-a",
        principal_id="principal-full",
    )

    authority = _Authority()
    authority.projects["project-a"]["source-private"] = "acl-control\n"
    _assert_reason(
        RawV2Reason.CANONICALIZATION_MISMATCH,
        _adapter(authority).resolve_object_visibility,
        project_id="project-a",
        source_acl_ref="source-private",
    )


def test_adapter_satisfies_the_trusted_authority_protocol() -> None:
    authority = _Authority()
    assert isinstance(authority, ProjectAclAuthority)


def test_policy_module_is_relocatable_and_in_the_built_wheel(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    relocated_source = tmp_path / "relocated" / "src"
    shutil.copytree(repository / "src" / "evidence_rag", relocated_source / "evidence_rag")
    import_probe = (
        "from evidence_rag.sources.v2.contracts import DigestDomain;"
        "from evidence_rag.sources.v2.policy import ObjectVisibility;"
        "value=ObjectVisibility(project_id='project-a',acl_ref='public');"
        "assert DigestDomain.REQUEST_VISIBILITY_SCOPE.value=='request-visibility-scope-v2';"
        "assert value.visibility_partition_sha256.startswith('sha256:')"
    )
    relocated = subprocess.run(
        [sys.executable, "-c", f"import sys;sys.path.insert(0,{str(relocated_source)!r});{import_probe}"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert relocated.returncode == 0, relocated.stderr

    uv = shutil.which("uv")
    assert uv is not None
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    built = subprocess.run(
        [uv, "build", "--wheel", "--no-build-isolation", "--out-dir", str(wheelhouse)],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "UV_NO_PROGRESS": "1"},
    )
    assert built.returncode == 0, built.stderr
    wheels = tuple(wheelhouse.glob("*.whl"))
    assert len(wheels) == 1
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheels[0]) as archive:
        assert "evidence_rag/sources/v2/policy.py" in archive.namelist()
        archive.extractall(installed)
    wheel_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys;sys.path.insert(0,{str(installed)!r});{import_probe}",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert wheel_probe.returncode == 0, wheel_probe.stderr
