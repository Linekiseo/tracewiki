"""Project-scoped ACL and visibility policy contract for raw V2 authority.

The adapter consumes trusted server-side policy results and emits computed-only
object/request visibility values.  It has no storage, HTTP, identity-provider,
runtime, or configuration side effects.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from .contracts import (
    DigestDomain,
    H,
    PortableId,
    RawV2ContractError,
    RawV2Reason,
    VisibilityPartitionPayload,
    derive_visibility_partition_sha256,
    normalize_portable_id,
    validate_sha256_digest,
)

ACL_VISIBILITY_POLICY_VERSION = "raw-v2-acl-visibility-policy-v1"
PUBLIC_ACL_REF = "public"
MAX_REQUESTER_ACL_REFS = 64


def _fail(reason: RawV2Reason, detail: str) -> None:
    raise RawV2ContractError(reason, detail)


class _FrozenPolicyValue(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(
            mode="python",
            round_trip=True,
            exclude_computed_fields=True,
        )
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class ObjectVisibility(_FrozenPolicyValue):
    """One logical object's project + single canonical ACL partition."""

    project_id: PortableId
    acl_ref: PortableId

    @computed_field(return_type=str)
    @property
    def visibility_partition_sha256(self) -> str:
        return derive_visibility_partition_sha256(
            VisibilityPartitionPayload(project_id=self.project_id, acl_ref=self.acl_ref)
        )


class RequesterVisibilityScope(_FrozenPolicyValue):
    """Trusted requester grants for exactly one project.

    Empty grants represent public-only access.  Missing project authorization is
    represented by the authority returning ``None`` and never creates this value.
    """

    project_id: PortableId
    requester_acl_refs: tuple[PortableId, ...]

    @model_validator(mode="after")
    def _canonical_acl_set(self) -> RequesterVisibilityScope:
        if len(self.requester_acl_refs) > MAX_REQUESTER_ACL_REFS:
            raise ValueError("requester ACL set exceeds the frozen cardinality limit")
        if self.requester_acl_refs != tuple(sorted(set(self.requester_acl_refs))):
            raise ValueError("requester ACL refs must be NFC-normalized, sorted, and unique")
        return self

    @computed_field(return_type=str)
    @property
    def request_visibility_scope_sha256(self) -> str:
        return derive_request_visibility_scope_sha256(self)


def derive_request_visibility_scope_sha256(scope: RequesterVisibilityScope) -> str:
    """Bind one project and its canonical requester ACL set under a distinct domain."""

    if type(scope) is not RequesterVisibilityScope:
        _fail(
            RawV2Reason.CANONICALIZATION_MISMATCH,
            "request visibility scope is not a frozen policy value",
        )
    return H(
        DigestDomain.REQUEST_VISIBILITY_SCOPE,
        [scope.project_id, list(scope.requester_acl_refs)],
    )


@runtime_checkable
class ProjectAclAuthority(Protocol):
    """Trusted server policy boundary consumed by the pure raw V2 adapter."""

    def project_exists(self, *, project_id: str) -> bool: ...

    def resolve_object_acl_ref(
        self,
        *,
        project_id: str,
        source_acl_ref: str,
    ) -> str | None: ...

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None: ...


class RawV2ProjectPolicyAdapter:
    """Normalize trusted policy results and fail closed on visibility mismatch."""

    def __init__(self, *, authority: ProjectAclAuthority) -> None:
        if not isinstance(authority, ProjectAclAuthority):
            raise TypeError("authority must implement ProjectAclAuthority")
        self._authority = authority

    @staticmethod
    def _identifier(value: str, *, label: str) -> str:
        return normalize_portable_id(value, label=label)

    def _require_project(self, project_id: str) -> None:
        exists = self._authority.project_exists(project_id=project_id)
        if type(exists) is not bool:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "project authority result must be exact bool",
            )
        if not exists:
            _fail(RawV2Reason.PROJECT_MISMATCH, "project is outside policy authority")

    def resolve_object_visibility(
        self,
        *,
        project_id: str,
        source_acl_ref: str,
    ) -> ObjectVisibility:
        """Resolve source ACL input through server policy and compute object partition."""

        canonical_project = self._identifier(project_id, label="project ID")
        canonical_source_acl = self._identifier(source_acl_ref, label="source ACL reference")
        self._require_project(canonical_project)
        resolved = self._authority.resolve_object_acl_ref(
            project_id=canonical_project,
            source_acl_ref=canonical_source_acl,
        )
        if resolved is None:
            _fail(RawV2Reason.ACL_DENIED, "object ACL has no trusted project mapping")
        canonical_acl = self._identifier(resolved, label="canonical object ACL reference")
        return ObjectVisibility(project_id=canonical_project, acl_ref=canonical_acl)

    def resolve_requester_scope(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> RequesterVisibilityScope:
        """Resolve trusted principal grants; callers cannot provide grants or digests."""

        canonical_project = self._identifier(project_id, label="project ID")
        canonical_principal = self._identifier(principal_id, label="principal ID")
        self._require_project(canonical_project)
        raw_grants = self._authority.resolve_requester_acl_refs(
            project_id=canonical_project,
            principal_id=canonical_principal,
        )
        if raw_grants is None:
            _fail(RawV2Reason.ACL_DENIED, "principal has no trusted project grant")
        if type(raw_grants) is not tuple:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "requester ACL authority result must be exact tuple",
            )
        canonical_grants = tuple(
            sorted(
                {
                    self._identifier(item, label="requester ACL reference")
                    for item in raw_grants
                }
            )
        )
        if len(canonical_grants) > MAX_REQUESTER_ACL_REFS:
            _fail(
                RawV2Reason.CONTRACT_SIZE_EXCEEDED,
                f"requester ACL set exceeds {MAX_REQUESTER_ACL_REFS} unique refs",
            )
        return RequesterVisibilityScope(
            project_id=canonical_project,
            requester_acl_refs=canonical_grants,
        )

    def authorize(
        self,
        *,
        scope: RequesterVisibilityScope,
        object_visibility: ObjectVisibility,
        stored_visibility_partition_sha256: str,
    ) -> ObjectVisibility:
        """Authorize one object after exact project and stored-partition verification."""

        if type(scope) is not RequesterVisibilityScope:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "request visibility scope is not a frozen policy value",
            )
        if type(object_visibility) is not ObjectVisibility:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "object visibility is not a frozen policy value",
            )
        if scope.project_id != object_visibility.project_id:
            _fail(RawV2Reason.PROJECT_MISMATCH, "request and object projects differ")
        stored_partition = validate_sha256_digest(stored_visibility_partition_sha256)
        if stored_partition != object_visibility.visibility_partition_sha256:
            _fail(
                RawV2Reason.VISIBILITY_MISMATCH,
                "stored object partition does not match project and object ACL",
            )
        if (
            object_visibility.acl_ref != PUBLIC_ACL_REF
            and object_visibility.acl_ref not in scope.requester_acl_refs
        ):
            _fail(RawV2Reason.ACL_DENIED, "object ACL is not in trusted requester grants")
        return object_visibility


__all__ = [
    "ACL_VISIBILITY_POLICY_VERSION",
    "MAX_REQUESTER_ACL_REFS",
    "PUBLIC_ACL_REF",
    "ObjectVisibility",
    "ProjectAclAuthority",
    "RawV2ProjectPolicyAdapter",
    "RequesterVisibilityScope",
    "derive_request_visibility_scope_sha256",
]
