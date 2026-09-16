"""Fail-closed, verify-only release admission for the complete RAG product.

This module deliberately does not discover artifacts under ``evals`` and does
not run retrieval, adapters, databases, benchmarks, or production replay.  It
accepts only explicit immutable evidence references and records the current
reviewed production-code authority before evaluating release eligibility.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib
import inspect
import json
import math
import re
import stat
import types
import unicodedata
import urllib.parse
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from evidence_rag.code_identity_v1 import (
    CodeIdentityError,
    portable_code_payload,
    portable_constant_payload,
    portable_function_payload,
    portable_stdlib_object_identity,
)

from .sources.experiment.contracts_v2 import canonical_json_bytes_v2, canonical_sha256_v2

RELEASE_ADMISSION_VERSION = "rag-release-admission-v2"
PRODUCTION_AUTHORITY_VERSION = "rag-production-authority-v4"
RELEASE_ADMISSION_PACKAGE_VERSION = "rag-release-admission-package-v2"
REVIEWED_RELEASE_EVIDENCE_AUTHORITY_VERSION = "rag-reviewed-release-evidence-authority-v2"
DEFAULT_ENGINE = "v1"

_SOURCE_NAMES = ("code", "codex", "experiment", "notebook", "document", "workspace")
_GATE_NAMES = ("multisource", "security", "performance", "rollback")
_PACKAGE_FILES = (
    "manifest.json",
    "authority.json",
    "sources.json",
    "gates.json",
    "admission.json",
    "security.json",
    "checksums.json",
)
_CHECKSUM_TARGETS = _PACKAGE_FILES[:-1]
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_REASON_RE = re.compile(r"[a-z][a-z0-9_-]{0,127}")
_ARTIFACT_ID_RE = re.compile(r"[0-9a-f]{32}")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"(?i:bearer\s+[A-Za-z0-9._~+/-]{8,})|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret|credential|"
    r"private[_-]?key)\s*[:=]\s*[^\s,;\"']{6,})|"
    r"(?i:^(?:password|secret|credential)$))"
)
_SECRET_KEY_RE = re.compile(
    r"(?i)^(?:api[_-]?key|access[_-]?token|password|secret|credential|"
    r"private[_-]?key|authorization|acl|acl_refs|query|prompt)$"
)
_POSIX_ABSOLUTE_RE = re.compile(r"(?:^|[\s\"'(=])/(?!/)[^\s\"']+")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?:^|[\s\"'(=])[a-z]:[\\/][^\s\"']*")
_UNC_RE = re.compile(
    r"(?i)(?:^|[\s\"'(=])(?:(?:\\{2,}|/{2,})[.?\\/]*"
    r"[^\\/\s]+[\\/]+[^\\/\s]+)"
)
_FORBIDDEN_FILE_RE = re.compile(
    r"(?i)(?:[^/\\\s\"']+\.(?:db|sqlite|sqlite3)(?:-(?:wal|shm))?|"
    r"[^/\\\s\"']+-(?:wal|shm)|[^/\\\s\"']+\.pyc)(?:$|[/\\\s\"'])"
)
_ALLOWED_ARTIFACT_URIS: Mapping[str, tuple[str, ...]] = {
    "code": ("evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061",),
    "codex": ("evaluation-correction://project-codex-xb0-v1/1ccecfa1c9667d0f716c709d1175d570",),
    "experiment": ("evaluation-run://project-experiment-eb0-v1/085799235df74bcdc4fc56c0c17f36e7",),
    "notebook": ("evaluation-run://project-notebook-nb0-v1/b9278f79dc7d42a8a61c25c45b1d7d3e",),
    "document": ("evaluation-run://project-document-db0-v1/b6c5c5e8f21446d5a0038d23ffed5acf",),
    "workspace": ("evaluation-run://project-workspace-wb0-v1/363181f5d05847a0aa98fbbee83c4373",),
}
_ALLOWED_VERIFIERS: Mapping[str, tuple[str, ...]] = {
    "code": ("code-release-verify-only-v2",),
    "codex": (
        "codex-baseline-verify-only-v1",
        "codex-correction-verify-only-v1",
    ),
    "experiment": ("experiment-baseline-verify-only-v1",),
    "notebook": ("notebook-baseline-verify-only-v1",),
    "document": ("document-baseline-verify-only-v1",),
    "workspace": ("workspace-baseline-verify-only-v1",),
}
_VERIFIER_AUTHORITIES: Mapping[str, str] = {
    verifier_id: canonical_sha256_v2(
        {"authority": "reviewed-release-verifier-v2", "verifier_id": verifier_id}
    )
    for verifier_ids in _ALLOWED_VERIFIERS.values()
    for verifier_id in verifier_ids
}
_GATE_AUTHORITIES: Mapping[str, tuple[str, str, str]] = {
    gate: (
        f"rag-{gate}-release-evidence-v2",
        f"rag-{gate}-release-verify-only-v2",
        canonical_sha256_v2(
            {
                "authority": "reviewed-release-gate-verifier-v2",
                "gate": gate,
                "verifier_id": f"rag-{gate}-release-verify-only-v2",
            }
        ),
    )
    for gate in _GATE_NAMES
}
# This is the only promotion trust root.  Rows may be added only through a
# reviewed code change containing exact immutable identities.  The current
# repository has no production-reviewed source or gate evidence authority.
_FIXED_REVIEWED_SOURCE_EVIDENCE_ROWS: tuple[dict[str, str], ...] = ()
_FIXED_REVIEWED_GATE_EVIDENCE_ROWS: tuple[dict[str, str], ...] = ()


class ReleaseAdmissionError(ValueError):
    """Base error for release-admission contract failures."""


class ProductionAuthorityError(ReleaseAdmissionError):
    """Raised when reviewed production authority is stale or unavailable."""


class ProductionAuthorityUnavailableError(ProductionAuthorityError):
    """Raised when an authority binding cannot be represented exactly."""


class ReleaseAdmissionPackageError(ReleaseAdmissionError):
    """Raised when a portable admission package is malformed or unsafe."""


class AuthorityFreshness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class EvidenceAvailability(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class ReleaseDecision(StrEnum):
    QUALITY_HOLD = "QUALITY_HOLD"
    PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ProductionComponentAuthorityV2(_Frozen):
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    role: str
    kind: Literal["class", "method", "function"]
    module: str
    public_module: str
    export: str
    qualname: str
    version: str
    source_sha256: str
    code_sha256: str
    bindings_sha256: str

    @model_validator(mode="after")
    def _portable(self) -> Self:
        text_fields = (
            self.role,
            self.module,
            self.public_module,
            self.export,
            self.qualname,
            self.version,
        )
        if any(not value or value != value.strip() for value in text_fields):
            raise ValueError("production component identity is not canonical")
        if any(
            _SHA256_RE.fullmatch(value) is None
            for value in (self.source_sha256, self.code_sha256, self.bindings_sha256)
        ):
            raise ValueError("production component digest is invalid")
        return self


# These rows are reviewed source authority, not values learned from the objects
# imported at runtime.
_FIXED_AUTHORITY_ROWS: tuple[dict[str, str], ...] = (
    {
        "source": "code",
        "role": "code-platform-search",
        "kind": "method",
        "module": "evidence_rag.rag.sources.code.platform_v2",
        "public_module": "evidence_rag.rag.sources.code.platform_v2",
        "export": "CodePlatformIntegration.search",
        "qualname": "CodePlatformIntegration.search",
        "version": "code-platform-integration-v3",
        "source_sha256": "sha256:d4d34c6e12b3d5ab545e089d8610b1ed5d33deaca7328dc94f804ab9cec43922",
        "code_sha256": "sha256:8f49287e7bd15c0be52615d80254f989f3781714f794114014dc4a1e12d55608",
        "bindings_sha256": "sha256:42350226faa803b79fddf4f148ec2e748bcb62ce727a126d22ee75bc36261ba0",
    },
    {
        "source": "codex",
        "role": "codex-source-runtime-execute",
        "kind": "method",
        "module": "evidence_rag.rag.sources.codex.runtime_v2",
        "public_module": "evidence_rag.rag.sources.codex.runtime_v2",
        "export": "CodexSourceRuntimeV2.execute",
        "qualname": "CodexSourceRuntimeV2.execute",
        "version": "codex-source-runtime-v2",
        "source_sha256": "sha256:13883d200a603c270187d4cbb64113fedb29b1d55480af49dc70123518f812ce",
        "code_sha256": "sha256:34fe9567d3322b0a4770d1d256f33f146bc3f14b0b2a2f1bc360c83363731df0",
        "bindings_sha256": "sha256:f6b9cf33fc7272835cc6f1aea44c41bdde7cabaed45eec258bdbc544410d259e",
    },
    {
        "source": "experiment",
        "role": "experiment-source-runtime-execute",
        "kind": "method",
        "module": "evidence_rag.rag.sources.experiment.runtime_v2",
        "public_module": "evidence_rag.rag.sources.experiment.runtime_v2",
        "export": "ExperimentSourceRuntimeV2.execute",
        "qualname": "ExperimentSourceRuntimeV2.execute",
        "version": "experiment-source-runtime-v2",
        "source_sha256": "sha256:05ae3617b5b8801302b92a8a67a990d6608174fef484f4ec7ba022ed92dc870c",
        "code_sha256": "sha256:03f7c42ed0ee62f7efc281c7b9967553ee1061f48de21af46342d4951f50baec",
        "bindings_sha256": "sha256:f4619a281d7a05b3c9935c551f5d478cef0c618fb373c7dcec4143aea5ba12a5",
    },
    {
        "source": "notebook",
        "role": "notebook-source-runtime-search",
        "kind": "method",
        "module": "evidence_rag.rag.sources.notebook.runtime_v2",
        "public_module": "evidence_rag.rag.sources.notebook.runtime_v2",
        "export": "NotebookSourceRuntimeV2.search",
        "qualname": "NotebookSourceRuntimeV2.search",
        "version": "notebook-source-runtime-v3",
        "source_sha256": "sha256:94c40854cd96026746ad34bbc2fd930992861cbc64e40f5d86fafa9aa45a133d",
        "code_sha256": "sha256:e96738e9a33ef4b2f78e28fc1bfa8bbc17686a6364d1d9d8ac226ad4f1c43c84",
        "bindings_sha256": "sha256:6e0da5a5030dab0a01e5e125b559ced246e86d1c599cf44ea33b8fd85a49a09f",
    },
    {
        "source": "document",
        "role": "document-source-runtime-search",
        "kind": "method",
        "module": "evidence_rag.rag.sources.document.runtime_v2",
        "public_module": "evidence_rag.rag.sources.document.runtime_v2",
        "export": "DocumentSourceRuntimeV2.search",
        "qualname": "DocumentSourceRuntimeV2.search",
        "version": "document-source-runtime-v3",
        "source_sha256": "sha256:bcb460992cedcf43bccd85596ce3bdc6c7c419af67d3fa3fecdedcbd0ed5d861",
        "code_sha256": "sha256:2e8be739106e7f8fc11054d1b02eff75353d5e1633f18b30f168273b08adc265",
        "bindings_sha256": "sha256:8194acfbd3d4aedd1a5db04e166cf0ee29ba73f12411aca49e565c420829cd5f",
    },
    {
        "source": "workspace",
        "role": "workspace-source-runtime-search",
        "kind": "method",
        "module": "evidence_rag.rag.sources.workspace.runtime_v2",
        "public_module": "evidence_rag.rag.sources.workspace.runtime_v2",
        "export": "WorkspaceSourceRuntimeV2.search",
        "qualname": "WorkspaceSourceRuntimeV2.search",
        "version": "workspace-source-runtime-v3",
        "source_sha256": "sha256:0524a697c4b828c04255a74d8919cd06cd66aaa8e1689afba5a2f3df6c29014a",
        "code_sha256": "sha256:dffa4ef648c572c8ade05614a458e1175ef640d02b9001bac704be88427fcacf",
        "bindings_sha256": "sha256:e6f0cd12f777ce5151d1f5c6598871d6e7eb922a0badedcd55cdf5abbf767536",
    },
)


def _fixed_components() -> tuple[ProductionComponentAuthorityV2, ...]:
    return tuple(
        ProductionComponentAuthorityV2.model_validate(row) for row in _FIXED_AUTHORITY_ROWS
    )


class ProductionAuthorityRegistryV2(_Frozen):
    registry_version: Literal["rag-production-authority-v4"] = PRODUCTION_AUTHORITY_VERSION
    components: tuple[ProductionComponentAuthorityV2, ...]
    component_set_sha256: str
    content_sha256: str

    @model_validator(mode="after")
    def _fixed_reviewed_registry(self) -> Self:
        fixed = _fixed_components()
        if self.components != fixed:
            raise ValueError("production authority registry differs from reviewed constants")
        expected_set = canonical_sha256_v2([item.model_dump(mode="json") for item in fixed])
        if self.component_set_sha256 != expected_set:
            raise ValueError("production authority component-set digest mismatch")
        expected_content = canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )
        if self.content_sha256 != expected_content:
            raise ValueError("production authority registry digest mismatch")
        return self


class SourceAuthorityFreshnessV2(_Frozen):
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    status: AuthorityFreshness
    reason_code: str
    expected_component_sha256: str
    observed_component_sha256: str | None = None

    @model_validator(mode="after")
    def _safe(self) -> Self:
        if _SAFE_REASON_RE.fullmatch(self.reason_code) is None:
            raise ValueError("authority reason code is not portable")
        if _SHA256_RE.fullmatch(self.expected_component_sha256) is None:
            raise ValueError("expected authority digest is invalid")
        if (
            self.observed_component_sha256 is not None
            and _SHA256_RE.fullmatch(self.observed_component_sha256) is None
        ):
            raise ValueError("observed authority digest is invalid")
        if self.status is AuthorityFreshness.CURRENT and (
            self.observed_component_sha256 != self.expected_component_sha256
        ):
            raise ValueError("CURRENT authority must match reviewed digest")
        return self


class ProductionAuthorityReportV2(_Frozen):
    registry: ProductionAuthorityRegistryV2
    sources: tuple[SourceAuthorityFreshnessV2, ...]
    all_current: bool
    report_version: Literal["rag-production-authority-report-v2"] = (
        "rag-production-authority-report-v2"
    )
    content_sha256: str

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if tuple(row.source for row in self.sources) != _SOURCE_NAMES:
            raise ValueError("authority report source membership/order mismatch")
        if self.all_current != all(
            item.status is AuthorityFreshness.CURRENT for item in self.sources
        ):
            raise ValueError("authority report current-state mismatch")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("authority report digest mismatch")
        return self


class ImmutableVerifierResultV2(_Frozen):
    verifier_id: str
    verifier_authority_sha256: str
    status: VerificationStatus
    artifact_set_sha256: str
    portable: bool
    verify_only: bool
    retrieval_executed: bool
    database_accessed: bool
    network_accessed: bool
    quality_qualified: bool
    production_observation: bool
    evidence_origin: Literal["CALLER_TEST_ONLY"] = "CALLER_TEST_ONLY"
    authority_attestation_sha256: None = None
    result_sha256: str

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        expected_authority = _VERIFIER_AUTHORITIES.get(self.verifier_id)
        if expected_authority is None or self.verifier_authority_sha256 != expected_authority:
            raise ValueError("source artifact verifier authority is not reviewed")
        if (
            _SHA256_RE.fullmatch(self.artifact_set_sha256) is None
            or _SHA256_RE.fullmatch(self.verifier_authority_sha256) is None
            or _SHA256_RE.fullmatch(self.result_sha256) is None
        ):
            raise ValueError("verifier result digest is invalid")
        if self.status is VerificationStatus.VERIFIED and not (
            self.portable
            and self.verify_only
            and not self.retrieval_executed
            and not self.database_accessed
            and not self.network_accessed
        ):
            raise ValueError("VERIFIED artifact must be portable and verify-only")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("verifier result digest mismatch")
        return self


class ImmutableSourceArtifactReferenceV2(_Frozen):
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    artifact_uri: str
    artifact_set_sha256: str
    source_authority_sha256: str
    verifier: ImmutableVerifierResultV2

    @model_validator(mode="after")
    def _allowlisted(self) -> Self:
        if self.artifact_uri not in _ALLOWED_ARTIFACT_URIS[self.source]:
            raise ValueError("source artifact URI is not explicitly allowlisted")
        artifact_id = self.artifact_uri.rsplit("/", 1)[-1]
        if _ARTIFACT_ID_RE.fullmatch(artifact_id) is None:
            raise ValueError("source artifact URI has a noncanonical immutable identity")
        if (
            _SHA256_RE.fullmatch(self.artifact_set_sha256) is None
            or _SHA256_RE.fullmatch(self.source_authority_sha256) is None
        ):
            raise ValueError("source artifact reference digest is invalid")
        if self.verifier.verifier_id not in _ALLOWED_VERIFIERS[self.source]:
            raise ValueError("source artifact verifier is not allowlisted")
        if self.verifier.artifact_set_sha256 != self.artifact_set_sha256:
            raise ValueError("source artifact/verifier set digest mismatch")
        return self


class ReleaseGateEvidenceV2(_Frozen):
    gate: Literal["multisource", "security", "performance", "rollback"]
    evidence_kind: str
    verifier_id: str
    verifier_authority_sha256: str
    status: VerificationStatus
    evidence_uri: str | None
    evidence_sha256: str | None
    qualified: bool
    production_observation: bool
    evidence_origin: Literal["CALLER_TEST_ONLY"] = "CALLER_TEST_ONLY"
    authority_attestation_sha256: None = None
    reason_code: str
    result_sha256: str

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        evidence_kind, verifier_id, verifier_authority = _GATE_AUTHORITIES[self.gate]
        if (
            self.evidence_kind != evidence_kind
            or self.verifier_id != verifier_id
            or self.verifier_authority_sha256 != verifier_authority
        ):
            raise ValueError("release-gate evidence authority is not reviewed")
        if _SAFE_REASON_RE.fullmatch(self.reason_code) is None:
            raise ValueError("release-gate reason code is not portable")
        if self.status is VerificationStatus.VERIFIED:
            if self.evidence_uri is None or self.evidence_sha256 is None:
                raise ValueError("VERIFIED gate requires immutable evidence identity")
            expected_prefix = f"evaluation-evidence://rag-release/{self.gate}/"
            if not self.evidence_uri.startswith(expected_prefix):
                raise ValueError("release-gate evidence URI is not allowlisted")
            evidence_id = self.evidence_uri.removeprefix(expected_prefix)
            if _ARTIFACT_ID_RE.fullmatch(evidence_id) is None:
                raise ValueError("release-gate evidence URI identity is not canonical")
            if _SHA256_RE.fullmatch(self.evidence_sha256) is None:
                raise ValueError("release-gate evidence digest is invalid")
        elif self.evidence_uri is not None or self.evidence_sha256 is not None:
            raise ValueError("unverified release gate cannot carry evidence identity")
        if _SHA256_RE.fullmatch(self.result_sha256) is None:
            raise ValueError("release-gate result digest is invalid")
        expected_result = canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"result_sha256"})
        )
        if self.result_sha256 != expected_result:
            raise ValueError("release-gate result digest mismatch")
        return self


class ReviewedSourceEvidenceAuthorityV2(_Frozen):
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    artifact_uri: str
    artifact_set_sha256: str
    source_authority_sha256: str
    verifier_id: str
    verifier_authority_sha256: str
    verifier_result_sha256: str
    authority_subject_sha256: str
    parent_authority_sha256: str
    authority_attestation_sha256: str

    @model_validator(mode="after")
    def _attestation_binding(self) -> Self:
        if any(
            _SHA256_RE.fullmatch(value) is None
            for value in (
                self.artifact_set_sha256,
                self.source_authority_sha256,
                self.verifier_authority_sha256,
                self.verifier_result_sha256,
                self.authority_subject_sha256,
                self.parent_authority_sha256,
                self.authority_attestation_sha256,
            )
        ):
            raise ValueError("reviewed source authority binding digest is invalid")
        if (
            self.authority_subject_sha256 != self.verifier_result_sha256
            or self.parent_authority_sha256 != self.source_authority_sha256
        ):
            raise ValueError("reviewed source attestation subject/parent binding mismatch")
        expected_attestation = canonical_sha256_v2(
            {
                "authority": "reviewed-source-release-attestation-v2",
                "source": self.source,
                "artifact_uri": self.artifact_uri,
                "artifact_set_sha256": self.artifact_set_sha256,
                "verifier_id": self.verifier_id,
                "verifier_authority_sha256": self.verifier_authority_sha256,
                "authority_subject_sha256": self.authority_subject_sha256,
                "parent_authority_sha256": self.parent_authority_sha256,
            }
        )
        if self.authority_attestation_sha256 != expected_attestation:
            raise ValueError("reviewed source authority attestation digest mismatch")
        return self


class ReviewedGateEvidenceAuthorityV2(_Frozen):
    gate: Literal["multisource", "security", "performance", "rollback"]
    evidence_kind: str
    evidence_uri: str
    evidence_sha256: str
    verifier_id: str
    verifier_authority_sha256: str
    result_sha256: str
    authority_subject_sha256: str
    parent_authority_sha256: str
    authority_attestation_sha256: str

    @model_validator(mode="after")
    def _attestation_binding(self) -> Self:
        if any(
            _SHA256_RE.fullmatch(value) is None
            for value in (
                self.evidence_sha256,
                self.verifier_authority_sha256,
                self.result_sha256,
                self.authority_subject_sha256,
                self.parent_authority_sha256,
                self.authority_attestation_sha256,
            )
        ):
            raise ValueError("reviewed gate authority binding digest is invalid")
        if (
            self.authority_subject_sha256 != self.result_sha256
            or self.parent_authority_sha256 != self.verifier_authority_sha256
        ):
            raise ValueError("reviewed gate attestation subject/parent binding mismatch")
        expected_attestation = canonical_sha256_v2(
            {
                "authority": "reviewed-gate-release-attestation-v2",
                "gate": self.gate,
                "evidence_kind": self.evidence_kind,
                "evidence_uri": self.evidence_uri,
                "evidence_sha256": self.evidence_sha256,
                "verifier_id": self.verifier_id,
                "verifier_authority_sha256": self.verifier_authority_sha256,
                "authority_subject_sha256": self.authority_subject_sha256,
                "parent_authority_sha256": self.parent_authority_sha256,
            }
        )
        if self.authority_attestation_sha256 != expected_attestation:
            raise ValueError("reviewed gate authority attestation digest mismatch")
        return self


class ReviewedReleaseEvidenceAuthorityRegistryV2(_Frozen):
    registry_version: Literal["rag-reviewed-release-evidence-authority-v2"] = (
        REVIEWED_RELEASE_EVIDENCE_AUTHORITY_VERSION
    )
    sources: tuple[ReviewedSourceEvidenceAuthorityV2, ...]
    gates: tuple[ReviewedGateEvidenceAuthorityV2, ...]
    content_sha256: str

    @model_validator(mode="after")
    def _fixed_reviewed_authority(self) -> Self:
        fixed_sources = tuple(
            ReviewedSourceEvidenceAuthorityV2.model_validate(row)
            for row in _FIXED_REVIEWED_SOURCE_EVIDENCE_ROWS
        )
        fixed_gates = tuple(
            ReviewedGateEvidenceAuthorityV2.model_validate(row)
            for row in _FIXED_REVIEWED_GATE_EVIDENCE_ROWS
        )
        if self.sources != fixed_sources or self.gates != fixed_gates:
            raise ValueError("reviewed release evidence authority is not fixed")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("reviewed release evidence authority digest mismatch")
        return self


class SourceAdmissionStateV2(_Frozen):
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    availability: EvidenceAvailability
    authority_status: AuthorityFreshness
    artifact_uri: str | None
    artifact_set_sha256: str | None
    verifier_id: str | None
    quality_qualified: bool
    production_observation: bool
    reason_code: str

    @model_validator(mode="after")
    def _safe(self) -> Self:
        if _SAFE_REASON_RE.fullmatch(self.reason_code) is None:
            raise ValueError("source admission reason code is not portable")
        if self.availability is EvidenceAvailability.CURRENT and not all(
            (self.artifact_uri, self.artifact_set_sha256, self.verifier_id)
        ):
            raise ValueError("CURRENT source admission requires artifact identity")
        return self


class ReleaseAdmissionEnvelopeV2(_Frozen):
    authority_registry_sha256: str
    authority_report_sha256: str
    reviewed_evidence_authority_sha256: str
    sources: tuple[SourceAdmissionStateV2, ...]
    gates: tuple[ReleaseGateEvidenceV2, ...]
    decision: ReleaseDecision
    default_engine: Literal["v1"] = DEFAULT_ENGINE
    quality_hold: bool
    promotion_performed: Literal[False] = False
    authority_attestation_sha256: str | None = None
    blockers: tuple[str, ...]
    admission_version: Literal["rag-release-admission-v2"] = RELEASE_ADMISSION_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if tuple(row.source for row in self.sources) != _SOURCE_NAMES:
            raise ValueError("release admission source membership/order mismatch")
        if tuple(row.gate for row in self.gates) != _GATE_NAMES:
            raise ValueError("release admission gate membership/order mismatch")
        source_ready = all(
            item.availability is EvidenceAvailability.CURRENT
            and item.authority_status is AuthorityFreshness.CURRENT
            and item.quality_qualified
            and item.production_observation
            for item in self.sources
        )
        gates_ready = all(
            item.status is VerificationStatus.VERIFIED
            and item.qualified
            and item.production_observation
            for item in self.gates
        )
        eligible = source_ready and gates_ready
        if (
            self.authority_attestation_sha256 is not None
            and _SHA256_RE.fullmatch(self.authority_attestation_sha256) is None
        ):
            raise ValueError("release authority attestation is invalid")
        if eligible and self.authority_attestation_sha256 is None:
            raise ValueError("promotion eligibility requires authority attestation")
        if (self.decision is ReleaseDecision.PROMOTION_ELIGIBLE) != eligible:
            raise ValueError("release decision does not match fail-closed gates")
        if self.quality_hold != (not eligible):
            raise ValueError("release quality-hold flag mismatch")
        if eligible != (not self.blockers):
            raise ValueError("release blockers do not match eligibility")
        if tuple(sorted(set(self.blockers))) != self.blockers:
            raise ValueError("release blockers must be sorted and unique")
        if (
            _SHA256_RE.fullmatch(self.authority_registry_sha256) is None
            or _SHA256_RE.fullmatch(self.authority_report_sha256) is None
            or _SHA256_RE.fullmatch(self.reviewed_evidence_authority_sha256) is None
        ):
            raise ValueError("release authority digest is invalid")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("release admission envelope digest mismatch")
        return self


class ReleaseAdmissionPackageManifestV2(_Frozen):
    package_version: Literal["rag-release-admission-package-v2"] = RELEASE_ADMISSION_PACKAGE_VERSION
    files: tuple[str, ...] = _PACKAGE_FILES
    checksum_targets: tuple[str, ...] = _CHECKSUM_TARGETS
    authority_registry_sha256: str
    authority_report_sha256: str
    reviewed_evidence_authority_sha256: str
    source_inputs_sha256: str
    gate_inputs_sha256: str
    admission_sha256: str
    default_engine: Literal["v1"] = DEFAULT_ENGINE
    retrieval_executed: Literal[False] = False
    database_accessed: Literal[False] = False
    network_accessed: Literal[False] = False
    verify_only: Literal[True] = True
    exact_files: Literal[True] = True
    portable: Literal[True] = True
    authority_attested: Literal[False] = False

    @model_validator(mode="after")
    def _fixed(self) -> Self:
        if self.files != _PACKAGE_FILES or self.checksum_targets != _CHECKSUM_TARGETS:
            raise ValueError("release admission package membership mismatch")
        if any(
            _SHA256_RE.fullmatch(value) is None
            for value in (
                self.source_inputs_sha256,
                self.gate_inputs_sha256,
                self.reviewed_evidence_authority_sha256,
                self.admission_sha256,
            )
        ):
            raise ValueError("release admission package input digest is invalid")
        return self


class ReleaseAdmissionSecurityV2(_Frozen):
    status: Literal["PASS"] = "PASS"
    scanned_files: tuple[str, ...] = _CHECKSUM_TARGETS
    finding_count: Literal[0] = 0
    symlink_count: Literal[0] = 0
    absolute_path_count: Literal[0] = 0
    secret_count: Literal[0] = 0
    database_sidecar_count: Literal[0] = 0
    bytecode_count: Literal[0] = 0
    nonfinite_count: Literal[0] = 0


class ReleaseAdmissionPackageVerificationV2(_Frozen):
    status: Literal["VERIFIED_QUALITY_HOLD"]
    artifact_set_sha256: str
    authority_registry_sha256: str
    authority_attestation_sha256: None = None
    authority_attested: Literal[False] = False
    admission_sha256: str
    decision: Literal[ReleaseDecision.QUALITY_HOLD] = ReleaseDecision.QUALITY_HOLD
    exact_files: Literal[True] = True
    portable: Literal[True] = True
    verify_only: Literal[True] = True
    retrieval_executed: Literal[False] = False
    database_accessed: Literal[False] = False
    network_accessed: Literal[False] = False


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _source_digest(value: object) -> str:
    return _sha256_bytes(inspect.getsource(value).encode("utf-8"))


def _constant_payload(value: object) -> object:
    return portable_constant_payload(value)


def _code_payload(code: types.CodeType) -> dict[str, object]:
    return portable_code_payload(code)


def _function_code_digest(value: types.FunctionType) -> str:
    try:
        return canonical_sha256_v2(portable_function_payload(value))
    except CodeIdentityError as exc:
        raise ProductionAuthorityError(str(exc)) from exc


def _class_callable_code_payload(raw: object) -> dict[str, str] | None:
    kind = "function"
    target = raw
    if isinstance(raw, staticmethod):
        kind = "staticmethod"
        target = raw.__func__
    elif isinstance(raw, classmethod):
        kind = "classmethod"
        target = raw.__func__
    if isinstance(target, types.FunctionType):
        return {"kind": kind, "code": _function_code_digest(target)}
    if kind != "function" and target is types.GenericAlias:
        return {"kind": kind, "generic_alias": "types.GenericAlias"}
    if kind != "function":
        raise ProductionAuthorityUnavailableError(
            "production class descriptor target is not exactly supported"
        )
    return None


def _class_code_digest(value: type[object]) -> str:
    members: dict[str, object] = {}
    class_source_file = inspect.getsourcefile(value)
    for name, raw in sorted(vars(value).items()):
        callable_payload = _class_callable_code_payload(raw)
        callable_value = raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
        if callable_payload is not None and (
            "generic_alias" in callable_payload
            or (
                isinstance(callable_value, types.FunctionType)
                and inspect.getsourcefile(callable_value) == class_source_file
            )
        ):
            members[name] = callable_payload
        elif isinstance(raw, property):
            members[name] = {
                "kind": "property",
                "get": (
                    _function_code_digest(raw.fget)
                    if raw.fget and inspect.getsourcefile(raw.fget) == class_source_file
                    else None
                ),
                "set": (
                    _function_code_digest(raw.fset)
                    if raw.fset and inspect.getsourcefile(raw.fset) == class_source_file
                    else None
                ),
                "delete": (
                    _function_code_digest(raw.fdel)
                    if raw.fdel and inspect.getsourcefile(raw.fdel) == class_source_file
                    else None
                ),
            }
    return canonical_sha256_v2(members)


def _runtime_code_digest(value: object, kind: str) -> str:
    if kind == "class" and isinstance(value, type):
        return _class_code_digest(value)
    if kind in {"method", "function"} and isinstance(value, types.FunctionType):
        return _function_code_digest(value)
    raise ProductionAuthorityError("production component kind/runtime mismatch")


def _safe_runtime_reference(value: object, *, depth: int = 0) -> object:
    if depth > 3:
        raise ProductionAuthorityUnavailableError(
            "production binding exceeds the exact authority depth"
        )
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"nonfinite": True}
    if isinstance(value, bytes):
        return {"bytes_sha256": _sha256_bytes(value)}
    if isinstance(value, tuple):
        return {"tuple": [_safe_runtime_reference(item, depth=depth + 1) for item in value]}
    if isinstance(value, frozenset):
        rows = [_safe_runtime_reference(item, depth=depth + 1) for item in value]
        return {"frozenset": sorted(rows, key=lambda item: canonical_json_bytes_v2(item))}
    if isinstance(value, Mapping):
        rows = [
            (
                _safe_runtime_reference(key, depth=depth + 1),
                _safe_runtime_reference(item, depth=depth + 1),
            )
            for key, item in value.items()
        ]
        rows.sort(key=lambda item: canonical_json_bytes_v2(item[0]))
        return {"mapping": rows}
    if isinstance(value, types.ModuleType):
        return {"module": value.__name__}
    stdlib_identity = portable_stdlib_object_identity(value)
    if stdlib_identity is not None:
        payload: dict[str, object] = {
            "stdlib": stdlib_identity,
            "kind": f"{type(value).__module__}.{type(value).__qualname__}",
        }
        if isinstance(value, type):
            for name, raw in vars(value).items():
                if (
                    name == "__class_getitem__"
                    and isinstance(raw, (staticmethod, classmethod))
                    and raw.__func__ is not types.GenericAlias
                ):
                    raise ProductionAuthorityUnavailableError(
                        "production stdlib generic-alias descriptor is not exactly supported"
                    )
            payload["descriptor_kinds"] = [
                {
                    "name": name,
                    "kind": "staticmethod" if isinstance(raw, staticmethod) else "classmethod",
                    "target": (
                        "types.GenericAlias"
                        if raw.__func__ is types.GenericAlias
                        else f"{getattr(raw.__func__, '__module__', None)}."
                        f"{getattr(raw.__func__, '__qualname__', None)}"
                    ),
                }
                for name, raw in sorted(vars(value).items())
                if isinstance(raw, (staticmethod, classmethod))
                and raw.__func__ is types.GenericAlias
            ]
        return payload
    if isinstance(value, types.FunctionType):
        module_name = value.__module__
        if not module_name:
            raise ProductionAuthorityUnavailableError(
                "production function binding has no canonical module"
            )
        module = importlib.import_module(module_name)
        canonical: object = module
        for part in value.__qualname__.split("."):
            if part == "<locals>":
                raise ProductionAuthorityUnavailableError(
                    "local production function binding is unsupported"
                )
            canonical = getattr(canonical, part)
        if canonical is not value:
            raise ProductionAuthorityError("production function binding was replaced")
        if value.__globals__ is not vars(module):
            raise ProductionAuthorityError("production function binding uses noncanonical globals")
        return {
            "function": f"{value.__module__}.{value.__qualname__}",
            "source": _source_digest(value),
            "code": _function_code_digest(value),
            "defaults": _safe_runtime_reference(value.__defaults__, depth=depth + 1),
            "kwdefaults": _safe_runtime_reference(value.__kwdefaults__, depth=depth + 1),
        }
    if isinstance(value, types.BuiltinFunctionType):
        module_name = getattr(value, "__module__", None)
        qualname = getattr(value, "__qualname__", None)
        name = getattr(value, "__name__", None)
        if not module_name or not qualname or not name:
            raise ProductionAuthorityUnavailableError(
                "builtin production binding has no canonical identity"
            )
        module = importlib.import_module(module_name)
        if getattr(module, name, None) is not value:
            raise ProductionAuthorityError("builtin production binding was replaced")
        return {
            "builtin_function": f"{module_name}.{qualname}",
            "name": name,
        }
    if isinstance(value, type):
        module_name = value.__module__
        qualname = value.__qualname__
        module = importlib.import_module(module_name)
        canonical: object = module
        for part in qualname.split("."):
            if part == "<locals>":
                raise ProductionAuthorityUnavailableError(
                    "local production class binding is unsupported"
                )
            canonical = getattr(canonical, part)
        if canonical is not value:
            raise ProductionAuthorityError("production class binding was replaced")
        if module_name == "builtins":
            return {"builtin_type": f"{module_name}.{qualname}"}
        if module_name in {"datetime", "zoneinfo"}:
            return {
                "extension_type": f"{module_name}.{qualname}",
                "metaclass": f"{type(value).__module__}.{type(value).__qualname__}",
                "bases": [f"{base.__module__}.{base.__qualname__}" for base in value.__bases__],
                "basicsize": value.__basicsize__,
                "itemsize": value.__itemsize__,
                "flags": value.__flags__,
                "members": [
                    (
                        name,
                        f"{type(member).__module__}.{type(member).__qualname__}",
                    )
                    for name, member in sorted(vars(value).items())
                ],
            }
        return {
            "class": f"{value.__module__}.{value.__qualname__}",
            "source": _source_digest(value),
            "code": _class_code_digest(value),
        }
    value_type = type(value)
    if value_type.__module__ == "datetime" and value_type.__qualname__ == "timezone":
        return {
            "datetime_timezone": repr(value),
            "type": "datetime.timezone",
        }
    raise ProductionAuthorityUnavailableError("production binding type is not exactly supported")


def _function_binding_payload(value: types.FunctionType) -> dict[str, object]:
    bindings: list[dict[str, object]] = []
    for name in sorted(set(value.__code__.co_names)):
        if name in value.__globals__:
            bindings.append(
                {
                    "name": name,
                    "scope": "global",
                    "binding": _safe_runtime_reference(value.__globals__[name]),
                }
            )
        elif hasattr(builtins, name):
            bindings.append(
                {
                    "name": name,
                    "scope": "builtin",
                    "binding": _safe_runtime_reference(getattr(builtins, name)),
                }
            )
        else:
            bindings.append({"name": name, "scope": "unbound"})
    closure: list[dict[str, object]] = []
    if value.__closure__:
        for name, cell in zip(value.__code__.co_freevars, value.__closure__, strict=True):
            try:
                binding = _safe_runtime_reference(cell.cell_contents)
            except ValueError:
                binding = {"empty": True}
            closure.append({"name": name, "binding": binding})
    return {
        "callable_type": f"{type(value).__module__}.{type(value).__qualname__}",
        "module": value.__module__,
        "qualname": value.__qualname__,
        "co_names": list(value.__code__.co_names),
        "defaults": _safe_runtime_reference(value.__defaults__),
        "kwdefaults": _safe_runtime_reference(value.__kwdefaults__),
        "globals": bindings,
        "closure": closure,
    }


def _runtime_binding_digest(value: object, kind: str) -> str:
    if kind in {"method", "function"} and isinstance(value, types.FunctionType):
        return canonical_sha256_v2(_function_binding_payload(value))
    if kind == "class" and isinstance(value, type):
        rows: list[dict[str, object]] = []
        for name, raw in sorted(vars(value).items()):
            descriptor_kind = "function"
            function = raw
            if isinstance(raw, staticmethod):
                descriptor_kind = "staticmethod"
                function = raw.__func__
            elif isinstance(raw, classmethod):
                descriptor_kind = "classmethod"
                function = raw.__func__
            if isinstance(function, types.FunctionType):
                rows.append(
                    {
                        "name": name,
                        "kind": descriptor_kind,
                        "binding": _function_binding_payload(function),
                    }
                )
            elif descriptor_kind != "function" and function is not types.GenericAlias:
                raise ProductionAuthorityUnavailableError(
                    "production class descriptor target is not exactly supported"
                )
        return canonical_sha256_v2(rows)
    raise ProductionAuthorityError("production component binding kind mismatch")


def _resolve_export(module_name: str, export: str) -> tuple[types.ModuleType, object]:
    module = importlib.import_module(module_name)
    value: object = module
    for part in export.split("."):
        value = getattr(value, part)
    return module, value


def _observed_component(
    expected: ProductionComponentAuthorityV2,
) -> ProductionComponentAuthorityV2:
    module, value = _resolve_export(expected.public_module, expected.export)
    canonical_module = importlib.import_module(expected.module)
    canonical: object = canonical_module
    for part in expected.export.split("."):
        canonical = getattr(canonical, part)
    if module is not canonical_module or value is not canonical:
        raise ProductionAuthorityError("production public export binding was replaced")
    if getattr(value, "__module__", None) != expected.module:
        raise ProductionAuthorityError("production component module was replaced")
    if getattr(value, "__qualname__", None) != expected.qualname:
        raise ProductionAuthorityError("production component qualname was replaced")
    if isinstance(value, types.FunctionType) and value.__globals__ is not vars(canonical_module):
        raise ProductionAuthorityError("production component globals were replaced")
    return ProductionComponentAuthorityV2(
        source=expected.source,
        role=expected.role,
        kind=expected.kind,
        module=expected.module,
        public_module=expected.public_module,
        export=expected.export,
        qualname=expected.qualname,
        version=expected.version,
        source_sha256=_source_digest(value),
        code_sha256=_runtime_code_digest(value, expected.kind),
        bindings_sha256=_runtime_binding_digest(value, expected.kind),
    )


def fixed_production_authority_registry_v2() -> ProductionAuthorityRegistryV2:
    components = _fixed_components()
    payload = {
        "registry_version": PRODUCTION_AUTHORITY_VERSION,
        "components": components,
        "component_set_sha256": canonical_sha256_v2(
            [item.model_dump(mode="json") for item in components]
        ),
    }
    return ProductionAuthorityRegistryV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            ProductionAuthorityRegistryV2.model_construct(
                content_sha256="pending", **payload
            ).model_dump(mode="json", exclude={"content_sha256"})
        ),
    )


def inspect_current_production_authority_v2() -> ProductionAuthorityReportV2:
    registry = fixed_production_authority_registry_v2()
    rows: list[SourceAuthorityFreshnessV2] = []
    for expected in registry.components:
        expected_sha = canonical_sha256_v2(expected.model_dump(mode="json"))
        try:
            observed = _observed_component(expected)
            observed_sha = canonical_sha256_v2(observed.model_dump(mode="json"))
            current = observed == expected
            rows.append(
                SourceAuthorityFreshnessV2(
                    source=expected.source,
                    status=(AuthorityFreshness.CURRENT if current else AuthorityFreshness.STALE),
                    reason_code="current" if current else "reviewed-digest-mismatch",
                    expected_component_sha256=expected_sha,
                    observed_component_sha256=observed_sha,
                )
            )
        except (
            AttributeError,
            ImportError,
            ModuleNotFoundError,
            ProductionAuthorityUnavailableError,
        ):
            rows.append(
                SourceAuthorityFreshnessV2(
                    source=expected.source,
                    status=AuthorityFreshness.UNAVAILABLE,
                    reason_code="component-unavailable",
                    expected_component_sha256=expected_sha,
                )
            )
        except (OSError, TypeError, ProductionAuthorityError):
            rows.append(
                SourceAuthorityFreshnessV2(
                    source=expected.source,
                    status=AuthorityFreshness.STALE,
                    reason_code="authority-invalid",
                    expected_component_sha256=expected_sha,
                )
            )
    payload = {
        "registry": registry,
        "sources": tuple(rows),
        "all_current": all(row.status is AuthorityFreshness.CURRENT for row in rows),
        "report_version": "rag-production-authority-report-v2",
    }
    return ProductionAuthorityReportV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            ProductionAuthorityReportV2.model_construct(
                content_sha256="pending", **payload
            ).model_dump(mode="json", exclude={"content_sha256"})
        ),
    )


def verify_current_production_authority_v2() -> ProductionAuthorityReportV2:
    report = inspect_current_production_authority_v2()
    if not report.all_current:
        raise ProductionAuthorityError("reviewed production authority is stale or unavailable")
    return report


def build_verifier_result_v2(
    *,
    verifier_id: str,
    status: VerificationStatus,
    artifact_set_sha256: str,
    portable: bool,
    verify_only: bool,
    retrieval_executed: bool,
    database_accessed: bool,
    network_accessed: bool,
    quality_qualified: bool,
    production_observation: bool,
) -> ImmutableVerifierResultV2:
    verifier_authority = _VERIFIER_AUTHORITIES.get(verifier_id)
    if verifier_authority is None:
        raise ReleaseAdmissionError("source artifact verifier is not reviewed")
    payload = {
        "verifier_id": verifier_id,
        "verifier_authority_sha256": verifier_authority,
        "status": status,
        "artifact_set_sha256": artifact_set_sha256,
        "portable": portable,
        "verify_only": verify_only,
        "retrieval_executed": retrieval_executed,
        "database_accessed": database_accessed,
        "network_accessed": network_accessed,
        "quality_qualified": quality_qualified,
        "production_observation": production_observation,
        "evidence_origin": "CALLER_TEST_ONLY",
        "authority_attestation_sha256": None,
    }
    return ImmutableVerifierResultV2(
        **payload,
        result_sha256=canonical_sha256_v2(payload),
    )


def build_gate_evidence_v2(
    *,
    gate: Literal["multisource", "security", "performance", "rollback"],
    status: VerificationStatus,
    evidence_uri: str | None,
    evidence_sha256: str | None,
    qualified: bool,
    production_observation: bool,
    reason_code: str,
) -> ReleaseGateEvidenceV2:
    evidence_kind, verifier_id, verifier_authority = _GATE_AUTHORITIES[gate]
    payload = {
        "gate": gate,
        "evidence_kind": evidence_kind,
        "verifier_id": verifier_id,
        "verifier_authority_sha256": verifier_authority,
        "status": status,
        "evidence_uri": evidence_uri,
        "evidence_sha256": evidence_sha256,
        "qualified": qualified,
        "production_observation": production_observation,
        "evidence_origin": "CALLER_TEST_ONLY",
        "authority_attestation_sha256": None,
        "reason_code": reason_code,
    }
    return ReleaseGateEvidenceV2(
        **payload,
        result_sha256=canonical_sha256_v2(payload),
    )


def _unavailable_gate_evidence_v2() -> tuple[ReleaseGateEvidenceV2, ...]:
    return tuple(
        build_gate_evidence_v2(
            gate=gate,
            status=VerificationStatus.UNAVAILABLE,
            evidence_uri=None,
            evidence_sha256=None,
            qualified=False,
            production_observation=False,
            reason_code="production-evidence-unavailable",
        )
        for gate in _GATE_NAMES
    )


def _canonical_source_inputs(
    artifacts: Sequence[ImmutableSourceArtifactReferenceV2],
) -> tuple[ImmutableSourceArtifactReferenceV2, ...]:
    validated = tuple(
        ImmutableSourceArtifactReferenceV2.model_validate(item.model_dump(mode="json"))
        for item in artifacts
    )
    artifact_by_source = {item.source: item for item in validated}
    if len(artifact_by_source) != len(validated):
        raise ReleaseAdmissionError("duplicate source artifact reference")
    unexpected = set(artifact_by_source).difference(_SOURCE_NAMES)
    if unexpected:
        raise ReleaseAdmissionError("unknown source artifact reference")
    ordered = tuple(
        artifact_by_source[source] for source in _SOURCE_NAMES if source in artifact_by_source
    )
    if validated != ordered:
        raise ReleaseAdmissionError("source artifact references are not in canonical order")
    return ordered


def _canonical_gate_inputs(
    gates: Sequence[ReleaseGateEvidenceV2],
) -> tuple[ReleaseGateEvidenceV2, ...]:
    validated = tuple(
        ReleaseGateEvidenceV2.model_validate(item.model_dump(mode="json")) for item in gates
    )
    gate_by_name = {item.gate: item for item in validated}
    if len(gate_by_name) != len(validated):
        raise ReleaseAdmissionError("duplicate release-gate evidence")
    if set(gate_by_name) != set(_GATE_NAMES):
        raise ReleaseAdmissionError("release-gate evidence membership mismatch")
    ordered = tuple(gate_by_name[name] for name in _GATE_NAMES)
    if validated != ordered:
        raise ReleaseAdmissionError("release-gate evidence is not in canonical order")
    return ordered


def fixed_reviewed_release_evidence_authority_registry_v2() -> (
    ReviewedReleaseEvidenceAuthorityRegistryV2
):
    payload = {
        "registry_version": REVIEWED_RELEASE_EVIDENCE_AUTHORITY_VERSION,
        "sources": tuple(
            ReviewedSourceEvidenceAuthorityV2.model_validate(row)
            for row in _FIXED_REVIEWED_SOURCE_EVIDENCE_ROWS
        ),
        "gates": tuple(
            ReviewedGateEvidenceAuthorityV2.model_validate(row)
            for row in _FIXED_REVIEWED_GATE_EVIDENCE_ROWS
        ),
    }
    return ReviewedReleaseEvidenceAuthorityRegistryV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            ReviewedReleaseEvidenceAuthorityRegistryV2.model_construct(
                content_sha256="pending",
                **payload,
            ).model_dump(mode="json", exclude={"content_sha256"})
        ),
    )


def _source_matches_reviewed_authority(
    reference: ImmutableSourceArtifactReferenceV2,
    registry: ReviewedReleaseEvidenceAuthorityRegistryV2,
) -> bool:
    return any(
        (
            row.source == reference.source
            and row.artifact_uri == reference.artifact_uri
            and row.artifact_set_sha256 == reference.artifact_set_sha256
            and row.source_authority_sha256 == reference.source_authority_sha256
            and row.verifier_id == reference.verifier.verifier_id
            and row.verifier_authority_sha256 == reference.verifier.verifier_authority_sha256
            and row.verifier_result_sha256 == reference.verifier.result_sha256
            and row.authority_subject_sha256 == reference.verifier.result_sha256
            and row.parent_authority_sha256 == reference.source_authority_sha256
            and row.authority_attestation_sha256 == reference.verifier.authority_attestation_sha256
        )
        for row in registry.sources
    )


def _gate_matches_reviewed_authority(
    evidence: ReleaseGateEvidenceV2,
    registry: ReviewedReleaseEvidenceAuthorityRegistryV2,
) -> bool:
    return any(
        (
            row.gate == evidence.gate
            and row.evidence_kind == evidence.evidence_kind
            and row.evidence_uri == evidence.evidence_uri
            and row.evidence_sha256 == evidence.evidence_sha256
            and row.verifier_id == evidence.verifier_id
            and row.verifier_authority_sha256 == evidence.verifier_authority_sha256
            and row.result_sha256 == evidence.result_sha256
            and row.authority_subject_sha256 == evidence.result_sha256
            and row.parent_authority_sha256 == evidence.verifier_authority_sha256
            and row.authority_attestation_sha256 == evidence.authority_attestation_sha256
        )
        for row in registry.gates
    )


def _source_states(
    *,
    authority: ProductionAuthorityReportV2,
    artifacts: Sequence[ImmutableSourceArtifactReferenceV2],
    reviewed_registry: ReviewedReleaseEvidenceAuthorityRegistryV2,
) -> tuple[SourceAdmissionStateV2, ...]:
    ordered_artifacts = _canonical_source_inputs(artifacts)
    artifact_by_source = {item.source: item for item in ordered_artifacts}
    authority_by_source = {item.source: item for item in authority.sources}
    component_by_source = {item.source: item for item in authority.registry.components}
    rows: list[SourceAdmissionStateV2] = []
    for source in _SOURCE_NAMES:
        current = authority_by_source[source]
        reference = artifact_by_source.get(source)
        if current.status is not AuthorityFreshness.CURRENT:
            rows.append(
                SourceAdmissionStateV2(
                    source=source,
                    availability=(
                        EvidenceAvailability.STALE
                        if current.status is AuthorityFreshness.STALE
                        else EvidenceAvailability.UNAVAILABLE
                    ),
                    authority_status=current.status,
                    artifact_uri=None,
                    artifact_set_sha256=None,
                    verifier_id=None,
                    quality_qualified=False,
                    production_observation=False,
                    reason_code="authority-not-current",
                )
            )
            continue
        if reference is None:
            rows.append(
                SourceAdmissionStateV2(
                    source=source,
                    availability=EvidenceAvailability.UNAVAILABLE,
                    authority_status=current.status,
                    artifact_uri=None,
                    artifact_set_sha256=None,
                    verifier_id=None,
                    quality_qualified=False,
                    production_observation=False,
                    reason_code="artifact-unavailable",
                )
            )
            continue
        expected_authority_sha = canonical_sha256_v2(
            component_by_source[source].model_dump(mode="json")
        )
        if reference.source_authority_sha256 != expected_authority_sha:
            rows.append(
                SourceAdmissionStateV2(
                    source=source,
                    availability=EvidenceAvailability.STALE,
                    authority_status=current.status,
                    artifact_uri=reference.artifact_uri,
                    artifact_set_sha256=reference.artifact_set_sha256,
                    verifier_id=reference.verifier.verifier_id,
                    quality_qualified=False,
                    production_observation=False,
                    reason_code="artifact-authority-stale",
                )
            )
            continue
        if reference.verifier.status is not VerificationStatus.VERIFIED:
            rows.append(
                SourceAdmissionStateV2(
                    source=source,
                    availability=(
                        EvidenceAvailability.STALE
                        if reference.verifier.status is VerificationStatus.STALE
                        else EvidenceAvailability.UNAVAILABLE
                    ),
                    authority_status=current.status,
                    artifact_uri=reference.artifact_uri,
                    artifact_set_sha256=reference.artifact_set_sha256,
                    verifier_id=reference.verifier.verifier_id,
                    quality_qualified=False,
                    production_observation=False,
                    reason_code="artifact-not-verified",
                )
            )
            continue
        if not _source_matches_reviewed_authority(reference, reviewed_registry):
            rows.append(
                SourceAdmissionStateV2(
                    source=source,
                    availability=EvidenceAvailability.UNAVAILABLE,
                    authority_status=current.status,
                    artifact_uri=reference.artifact_uri,
                    artifact_set_sha256=reference.artifact_set_sha256,
                    verifier_id=reference.verifier.verifier_id,
                    quality_qualified=False,
                    production_observation=False,
                    reason_code="reviewed-release-authority-unavailable",
                )
            )
            continue
        rows.append(
            SourceAdmissionStateV2(
                source=source,
                availability=EvidenceAvailability.CURRENT,
                authority_status=current.status,
                artifact_uri=reference.artifact_uri,
                artifact_set_sha256=reference.artifact_set_sha256,
                verifier_id=reference.verifier.verifier_id,
                quality_qualified=reference.verifier.quality_qualified,
                production_observation=reference.verifier.production_observation,
                reason_code=(
                    "qualified-current"
                    if reference.verifier.quality_qualified
                    and reference.verifier.production_observation
                    else "quality-or-observation-hold"
                ),
            )
        )
    return tuple(rows)


def evaluate_release_admission_v2(
    *,
    authority: ProductionAuthorityReportV2,
    artifacts: Sequence[ImmutableSourceArtifactReferenceV2],
    gates: Sequence[ReleaseGateEvidenceV2],
) -> ReleaseAdmissionEnvelopeV2:
    fixed_registry = fixed_production_authority_registry_v2()
    if authority.registry != fixed_registry:
        raise ReleaseAdmissionError("release authority registry is not the reviewed registry")
    runtime_authority = inspect_current_production_authority_v2()
    if authority != runtime_authority:
        raise ProductionAuthorityError("release authority report is not the current runtime report")
    reviewed_registry = fixed_reviewed_release_evidence_authority_registry_v2()
    ordered_artifacts = _canonical_source_inputs(artifacts)
    raw_gates = _canonical_gate_inputs(gates)
    sources = _source_states(
        authority=authority,
        artifacts=ordered_artifacts,
        reviewed_registry=reviewed_registry,
    )
    ordered_gates = tuple(
        item
        if _gate_matches_reviewed_authority(item, reviewed_registry)
        else build_gate_evidence_v2(
            gate=item.gate,
            status=VerificationStatus.UNAVAILABLE,
            evidence_uri=None,
            evidence_sha256=None,
            qualified=False,
            production_observation=False,
            reason_code="reviewed-release-authority-unavailable",
        )
        for item in raw_gates
    )
    blockers: set[str] = set()
    reviewed_authority_ready = (
        len(reviewed_registry.sources) == len(_SOURCE_NAMES)
        and len(reviewed_registry.gates) == len(_GATE_NAMES)
        and all(
            _source_matches_reviewed_authority(item, reviewed_registry)
            for item in ordered_artifacts
        )
        and len(ordered_artifacts) == len(_SOURCE_NAMES)
        and all(_gate_matches_reviewed_authority(item, reviewed_registry) for item in raw_gates)
    )
    if not reviewed_authority_ready:
        blockers.add("reviewed-release-authority-unavailable")
    for item in sources:
        if item.availability is not EvidenceAvailability.CURRENT:
            blockers.add(f"source-{item.source}-{item.availability.value.lower()}")
        if not item.quality_qualified:
            blockers.add(f"source-{item.source}-quality")
        if not item.production_observation:
            blockers.add(f"source-{item.source}-production-observation")
    for item in ordered_gates:
        if item.status is not VerificationStatus.VERIFIED:
            blockers.add(f"gate-{item.gate}-{item.status.value.lower()}")
        if not item.qualified:
            blockers.add(f"gate-{item.gate}-quality")
        if not item.production_observation:
            blockers.add(f"gate-{item.gate}-production-observation")
    ordered_blockers = tuple(sorted(blockers))
    # The fixed reviewed registry is the sole source of authority
    # attestations.  Caller-built verifier/gate objects always have a null
    # attestation and therefore cannot become promotion evidence.
    authority_attestation_sha256: str | None = None
    if reviewed_authority_ready:
        authority_attestation_sha256 = canonical_sha256_v2(
            {
                "authority": REVIEWED_RELEASE_EVIDENCE_AUTHORITY_VERSION,
                "registry_sha256": reviewed_registry.content_sha256,
                "source_attestations": [
                    row.authority_attestation_sha256 for row in reviewed_registry.sources
                ],
                "gate_attestations": [
                    row.authority_attestation_sha256 for row in reviewed_registry.gates
                ],
            }
        )
    eligible = not ordered_blockers and authority_attestation_sha256 is not None
    payload = {
        "authority_registry_sha256": authority.registry.content_sha256,
        "authority_report_sha256": authority.content_sha256,
        "reviewed_evidence_authority_sha256": reviewed_registry.content_sha256,
        "sources": sources,
        "gates": ordered_gates,
        "decision": (
            ReleaseDecision.PROMOTION_ELIGIBLE if eligible else ReleaseDecision.QUALITY_HOLD
        ),
        "default_engine": DEFAULT_ENGINE,
        "quality_hold": not eligible,
        "promotion_performed": False,
        "authority_attestation_sha256": authority_attestation_sha256,
        "blockers": ordered_blockers,
        "admission_version": RELEASE_ADMISSION_VERSION,
    }
    return ReleaseAdmissionEnvelopeV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            ReleaseAdmissionEnvelopeV2.model_construct(
                content_sha256="pending", **payload
            ).model_dump(mode="json", exclude={"content_sha256"})
        ),
    )


def build_current_repository_quality_hold_v2() -> ReleaseAdmissionEnvelopeV2:
    """Return current repository truth without reading artifacts or running evidence."""

    authority = inspect_current_production_authority_v2()
    gates = _unavailable_gate_evidence_v2()
    return evaluate_release_admission_v2(authority=authority, artifacts=(), gates=gates)


def _strict_json_loads(raw: bytes) -> object:
    def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in rows:
            if key in result:
                raise ReleaseAdmissionPackageError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ReleaseAdmissionPackageError(f"nonfinite JSON number: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseAdmissionPackageError("invalid JSON payload") from exc


def _canonical_file_bytes(value: object) -> bytes:
    return canonical_json_bytes_v2(value) + b"\n"


def _iter_strings(value: object) -> Sequence[str]:
    rows: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            rows.append(item)
        elif isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ReleaseAdmissionPackageError("canonical JSON object key must be a string")
                rows.append(key)
                visit(child)
        elif isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray, str)):
            for child in item:
                visit(child)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ReleaseAdmissionPackageError("nonfinite artifact value")

    visit(value)
    return tuple(rows)


def _decoded_variants(value: str) -> tuple[str, ...]:
    rows: list[str] = []
    current = unicodedata.normalize("NFKC", value)
    for _ in range(6):
        if current in rows:
            break
        rows.append(current)
        current = unicodedata.normalize("NFKC", urllib.parse.unquote(current))
    return tuple(rows)


def _security_findings(payloads: Mapping[str, object]) -> tuple[str, ...]:
    findings: set[str] = set()
    for filename, payload in payloads.items():
        for value in _iter_strings(payload):
            for variant in _decoded_variants(value):
                if _CONTROL_RE.search(variant):
                    findings.add(f"{filename}:control-character")
                if _SECRET_KEY_RE.fullmatch(variant) or _SECRET_RE.search(variant):
                    findings.add(f"{filename}:secret")
                if (
                    _POSIX_ABSOLUTE_RE.search(variant)
                    or _WINDOWS_ABSOLUTE_RE.search(variant)
                    or _UNC_RE.search(variant)
                ):
                    findings.add(f"{filename}:absolute-path")
                if _FORBIDDEN_FILE_RE.search(variant):
                    findings.add(f"{filename}:database-sidecar-or-bytecode")
    return tuple(sorted(findings))


def _validate_output_path(output_dir: Path, artifact_root: Path) -> None:
    if not output_dir.is_absolute() or not artifact_root.is_absolute():
        raise ReleaseAdmissionPackageError("release admission paths must be absolute")
    if artifact_root.is_symlink():
        raise ReleaseAdmissionPackageError("release admission root cannot be a symlink")
    root = artifact_root.resolve(strict=True)
    probe = artifact_root
    while probe != probe.parent:
        if probe.is_symlink():
            raise ReleaseAdmissionPackageError("release admission root traverses a symlink")
        probe = probe.parent
    try:
        relative_parent = output_dir.parent.relative_to(artifact_root)
    except ValueError as exc:
        raise ReleaseAdmissionPackageError(
            "release admission output escapes artifact root"
        ) from exc
    probe = artifact_root
    for part in relative_parent.parts:
        probe /= part
        if probe.is_symlink():
            raise ReleaseAdmissionPackageError("release admission output traverses a symlink")
    parent = output_dir.parent.resolve(strict=True)
    if not parent.is_relative_to(root):
        raise ReleaseAdmissionPackageError("release admission output escapes artifact root")
    if output_dir.exists():
        raise ReleaseAdmissionPackageError("release admission output must be new")


def write_release_admission_package_v2(
    *,
    output_dir: Path,
    artifact_root: Path,
    authority: ProductionAuthorityReportV2,
    admission: ReleaseAdmissionEnvelopeV2,
    artifacts: Sequence[ImmutableSourceArtifactReferenceV2] = (),
    gates: Sequence[ReleaseGateEvidenceV2] | None = None,
    trusted_artifacts: Sequence[ImmutableSourceArtifactReferenceV2] | None = None,
    trusted_gates: Sequence[ReleaseGateEvidenceV2] | None = None,
) -> Path:
    if trusted_artifacts is not None or trusted_gates is not None:
        raise ReleaseAdmissionPackageError(
            "caller-supplied trusted evidence is forbidden; use the canonical control plane"
        )
    current = inspect_current_production_authority_v2()
    if authority != current:
        raise ProductionAuthorityError("package authority report is not current")
    source_inputs = _canonical_source_inputs(artifacts)
    gate_inputs = _canonical_gate_inputs(
        _unavailable_gate_evidence_v2() if gates is None else gates
    )
    recomputed = evaluate_release_admission_v2(
        authority=authority,
        artifacts=source_inputs,
        gates=gate_inputs,
    )
    if admission != recomputed:
        raise ReleaseAdmissionPackageError(
            "admission envelope was not derived from package evidence inputs"
        )
    if admission.decision is ReleaseDecision.PROMOTION_ELIGIBLE:
        raise ReleaseAdmissionPackageError(
            "legacy package writer cannot package promotion authority"
        )
    _validate_output_path(output_dir, artifact_root)
    source_payload = [item.model_dump(mode="json") for item in source_inputs]
    gate_payload = [item.model_dump(mode="json") for item in gate_inputs]
    manifest = ReleaseAdmissionPackageManifestV2(
        authority_registry_sha256=authority.registry.content_sha256,
        authority_report_sha256=authority.content_sha256,
        reviewed_evidence_authority_sha256=(
            fixed_reviewed_release_evidence_authority_registry_v2().content_sha256
        ),
        source_inputs_sha256=canonical_sha256_v2(source_payload),
        gate_inputs_sha256=canonical_sha256_v2(gate_payload),
        admission_sha256=admission.content_sha256,
    )
    security = ReleaseAdmissionSecurityV2()
    payloads: dict[str, object] = {
        "manifest.json": manifest.model_dump(mode="json"),
        "authority.json": authority.model_dump(mode="json"),
        "sources.json": source_payload,
        "gates.json": gate_payload,
        "admission.json": admission.model_dump(mode="json"),
        "security.json": security.model_dump(mode="json"),
    }
    findings = _security_findings(payloads)
    if findings:
        raise ReleaseAdmissionPackageError("release admission package contains unsafe content")
    output_dir.mkdir(mode=0o700)
    try:
        for filename in _CHECKSUM_TARGETS:
            (output_dir / filename).write_bytes(_canonical_file_bytes(payloads[filename]))
        checksums = {
            filename: _sha256_bytes((output_dir / filename).read_bytes())
            for filename in _CHECKSUM_TARGETS
        }
        checksum_payload = {
            "checksums": checksums,
            "artifact_set_sha256": canonical_sha256_v2(checksums),
        }
        (output_dir / "checksums.json").write_bytes(_canonical_file_bytes(checksum_payload))
        verify_release_admission_package_v2(
            output_dir,
        )
    except Exception:
        for path in output_dir.iterdir():
            path.unlink()
        output_dir.rmdir()
        raise
    return output_dir


def verify_release_admission_package_v2(
    artifact_dir: Path,
    *,
    trusted_artifacts: Sequence[ImmutableSourceArtifactReferenceV2] | None = None,
    trusted_gates: Sequence[ReleaseGateEvidenceV2] | None = None,
) -> ReleaseAdmissionPackageVerificationV2:
    if trusted_artifacts is not None or trusted_gates is not None:
        raise ReleaseAdmissionPackageError(
            "caller-supplied trusted evidence is forbidden; use the canonical control plane"
        )
    current = inspect_current_production_authority_v2()
    if not artifact_dir.is_absolute() or not artifact_dir.is_dir():
        raise ReleaseAdmissionPackageError("artifact path must be an absolute directory")
    probe = Path(artifact_dir.anchor)
    for part in artifact_dir.parts[1:]:
        probe /= part
        if probe.is_symlink():
            raise ReleaseAdmissionPackageError("artifact path cannot traverse a symlink")
    names = tuple(sorted(path.name for path in artifact_dir.iterdir()))
    if names != tuple(sorted(_PACKAGE_FILES)):
        raise ReleaseAdmissionPackageError("artifact file membership mismatch")
    raw_files: dict[str, bytes] = {}
    payloads: dict[str, object] = {}
    for filename in _PACKAGE_FILES:
        path = artifact_dir / filename
        if not stat.S_ISREG(path.lstat().st_mode) or path.is_symlink():
            raise ReleaseAdmissionPackageError("artifact members must be regular nonsymlink files")
        raw = path.read_bytes()
        payload = _strict_json_loads(raw)
        if _canonical_file_bytes(payload) != raw:
            raise ReleaseAdmissionPackageError("artifact JSON is not canonical")
        raw_files[filename] = raw
        payloads[filename] = payload
    findings = _security_findings({filename: payloads[filename] for filename in _CHECKSUM_TARGETS})
    if findings:
        raise ReleaseAdmissionPackageError("artifact security scan failed")
    checksums_payload = payloads["checksums.json"]
    if not isinstance(checksums_payload, Mapping):
        raise ReleaseAdmissionPackageError("checksums payload is invalid")
    checksums = checksums_payload.get("checksums")
    if not isinstance(checksums, Mapping) or set(checksums) != set(_CHECKSUM_TARGETS):
        raise ReleaseAdmissionPackageError("checksum target membership mismatch")
    expected_checksums = {
        filename: _sha256_bytes(raw_files[filename]) for filename in _CHECKSUM_TARGETS
    }
    if dict(checksums) != expected_checksums:
        raise ReleaseAdmissionPackageError("artifact checksum mismatch")
    artifact_set_sha = canonical_sha256_v2(expected_checksums)
    if checksums_payload.get("artifact_set_sha256") != artifact_set_sha:
        raise ReleaseAdmissionPackageError("artifact-set digest mismatch")
    try:
        manifest = ReleaseAdmissionPackageManifestV2.model_validate(payloads["manifest.json"])
        authority = ProductionAuthorityReportV2.model_validate(payloads["authority.json"])
        sources_payload = payloads["sources.json"]
        gates_payload = payloads["gates.json"]
        if not isinstance(sources_payload, list) or not isinstance(gates_payload, list):
            raise ValueError("source/gate payload must be arrays")
        source_inputs = _canonical_source_inputs(
            tuple(
                ImmutableSourceArtifactReferenceV2.model_validate(item) for item in sources_payload
            )
        )
        gate_inputs = _canonical_gate_inputs(
            tuple(ReleaseGateEvidenceV2.model_validate(item) for item in gates_payload)
        )
        admission = ReleaseAdmissionEnvelopeV2.model_validate(payloads["admission.json"])
        security = ReleaseAdmissionSecurityV2.model_validate(payloads["security.json"])
    except (TypeError, ValueError) as exc:
        raise ReleaseAdmissionPackageError("artifact contract validation failed") from exc
    if authority != current:
        raise ProductionAuthorityError("artifact authority is stale")
    try:
        recomputed = evaluate_release_admission_v2(
            authority=authority,
            artifacts=source_inputs,
            gates=gate_inputs,
        )
    except (ProductionAuthorityError, ReleaseAdmissionError, ValueError) as exc:
        raise ReleaseAdmissionPackageError(
            "artifact admission inputs could not be re-evaluated"
        ) from exc
    if admission != recomputed:
        raise ReleaseAdmissionPackageError("artifact admission differs from re-evaluation")
    source_payload = [item.model_dump(mode="json") for item in source_inputs]
    gate_payload = [item.model_dump(mode="json") for item in gate_inputs]
    if (
        manifest.authority_registry_sha256 != authority.registry.content_sha256
        or manifest.authority_report_sha256 != authority.content_sha256
        or manifest.reviewed_evidence_authority_sha256
        != fixed_reviewed_release_evidence_authority_registry_v2().content_sha256
        or manifest.source_inputs_sha256 != canonical_sha256_v2(source_payload)
        or manifest.gate_inputs_sha256 != canonical_sha256_v2(gate_payload)
        or manifest.admission_sha256 != admission.content_sha256
        or admission.authority_registry_sha256 != authority.registry.content_sha256
        or admission.authority_report_sha256 != authority.content_sha256
        or admission.reviewed_evidence_authority_sha256
        != fixed_reviewed_release_evidence_authority_registry_v2().content_sha256
    ):
        raise ReleaseAdmissionPackageError("artifact cross-file authority mismatch")
    if security != ReleaseAdmissionSecurityV2():
        raise ReleaseAdmissionPackageError("artifact security report mismatch")
    if admission.decision is ReleaseDecision.PROMOTION_ELIGIBLE:
        raise ReleaseAdmissionPackageError("legacy package cannot carry promotion authority")
    return ReleaseAdmissionPackageVerificationV2(
        status="VERIFIED_QUALITY_HOLD",
        artifact_set_sha256=artifact_set_sha,
        authority_registry_sha256=authority.registry.content_sha256,
        admission_sha256=admission.content_sha256,
        decision=ReleaseDecision.QUALITY_HOLD,
    )


__all__ = [
    "AuthorityFreshness",
    "EvidenceAvailability",
    "ImmutableSourceArtifactReferenceV2",
    "ImmutableVerifierResultV2",
    "ProductionAuthorityError",
    "ProductionAuthorityRegistryV2",
    "ProductionAuthorityReportV2",
    "ProductionComponentAuthorityV2",
    "ReleaseAdmissionEnvelopeV2",
    "ReleaseAdmissionError",
    "ReleaseAdmissionPackageError",
    "ReleaseAdmissionPackageVerificationV2",
    "ReleaseDecision",
    "ReleaseGateEvidenceV2",
    "ReviewedGateEvidenceAuthorityV2",
    "ReviewedReleaseEvidenceAuthorityRegistryV2",
    "ReviewedSourceEvidenceAuthorityV2",
    "SourceAdmissionStateV2",
    "VerificationStatus",
    "build_current_repository_quality_hold_v2",
    "build_gate_evidence_v2",
    "build_verifier_result_v2",
    "evaluate_release_admission_v2",
    "fixed_production_authority_registry_v2",
    "fixed_reviewed_release_evidence_authority_registry_v2",
    "inspect_current_production_authority_v2",
    "verify_current_production_authority_v2",
    "verify_release_admission_package_v2",
    "write_release_admission_package_v2",
]
