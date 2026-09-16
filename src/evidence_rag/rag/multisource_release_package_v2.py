"""Portable, verify-only audit package for the multi-source release bundle.

This module deliberately packages explicit source artifact references rather
than walking an ``evals`` tree.  The first package version is audit-only: a
valid package proves the current QUALITY_HOLD evidence is internally
consistent, but it can never qualify or promote the V2 release.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .multisource_evaluation_v2 import (
    MULTISOURCE_EVIDENCE_BUNDLE_VERSION,
    MultiSourceEvidenceBundleV2,
    serialize_multisource_bundle_v2,
)
from .multisource_foundation_v2 import MULTISOURCE_DOMAINS
from .sources.experiment.contracts_v2 import (
    canonical_json_bytes_v2,
    canonical_sha256_v2,
)

MULTISOURCE_RELEASE_PACKAGE_VERSION = "multisource-release-audit-package-v1"
MULTISOURCE_RELEASE_PACKAGE_VERIFY_VERSION = "multisource-release-audit-package-verify-v1"
MULTISOURCE_RELEASE_PACKAGE_FILES = (
    "manifest.json",
    "bundle.json",
    "source_artifacts.json",
    "authorities.json",
    "qualification.json",
    "checksums.json",
)
_CHECKSUM_TARGETS = MULTISOURCE_RELEASE_PACKAGE_FILES[:-1]
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SHA256_RE = re.compile(_SHA256_PATTERN)
_SAFE_VERSION_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|credential|"
    r"private[_-]?key)\s*[:=]\s*[^\s\"']{8,}))"
)
_SECRET_KEY_RE = re.compile(
    r"(?i)^(?:api[_-]?key|access[_-]?token|password|credential|private[_-]?key)$"
)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_POSIX_ABSOLUTE_RE = re.compile(
    r"(?:^|[\s\"'(=])/(?!/)(?:Users|home|private|tmp|var|opt|etc|root|mnt|Volumes)"
    r"(?:/|$)"
)
_GENERIC_POSIX_ABSOLUTE_RE = re.compile(r"(?:^|[\s\"'(=])/(?!/)[^\s\"']+")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?:^|[\s\"'(=])[a-z]:[\\/][^\s\"']*")
_UNC_RE = re.compile(
    r"(?i)(?:^|[\s\"'(=])(?:\\\\[.?\\]*[^\\/\s]+[\\/][^\\/\s]+|"
    r"//[.?/]*[^/\s]+/[^/\s]+)"
)
_FORBIDDEN_FILE_RE = re.compile(
    r"(?i)(?:\.sqlite3(?:-wal|-shm)?|(?:^|[/\\])[^/\\\s]+-(?:wal|shm)"
    r"(?:$|[/\\\s])|\.pyc(?:$|[\s\"']))"
)
_SOURCE_URI_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "code": re.compile(r"^evaluation-run://project-code-[a-z0-9][a-z0-9-]*/[0-9a-f]{32}$"),
    "codex": re.compile(
        r"^evaluation-(?:run|correction)://project-codex-[a-z0-9][a-z0-9-]*/"
        r"[0-9a-f]{32}$"
    ),
    "experiment": re.compile(
        r"^evaluation-run://project-experiment-[a-z0-9][a-z0-9-]*/[0-9a-f]{32}$"
    ),
    "notebook": re.compile(r"^evaluation-run://project-notebook-[a-z0-9][a-z0-9-]*/[0-9a-f]{32}$"),
    "document": re.compile(r"^evaluation-run://project-document-[a-z0-9][a-z0-9-]*/[0-9a-f]{32}$"),
    "workspace": re.compile(
        r"^evaluation-run://project-workspace-[a-z0-9][a-z0-9-]*/[0-9a-f]{32}$"
    ),
}


class MultiSourceReleasePackageError(ValueError):
    """Raised when an outer release audit package is unsafe or inconsistent."""


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class SourceArtifactReferenceV2(_Frozen):
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    artifact_uri: str
    artifact_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_version: str
    quality_qualified: bool
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> SourceArtifactReferenceV2:
        if _SOURCE_URI_PATTERNS[self.source].fullmatch(self.artifact_uri) is None:
            raise ValueError("source artifact URI is not allowlisted")
        if (
            self.verifier_version != self.verifier_version.strip()
            or _SAFE_VERSION_RE.fullmatch(self.verifier_version) is None
        ):
            raise ValueError("source artifact verifier version is not canonical")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("source artifact reference digest mismatch")
        return self


class SourceArtifactReferenceSetV2(_Frozen):
    references: tuple[SourceArtifactReferenceV2, ...] = Field(min_length=6, max_length=6)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> SourceArtifactReferenceSetV2:
        if tuple(item.source for item in self.references) != MULTISOURCE_DOMAINS:
            raise ValueError("source artifact references require canonical six-source order")
        if len({item.artifact_uri for item in self.references}) != len(self.references):
            raise ValueError("source artifact references must be unique")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("source artifact reference set digest mismatch")
        return self


class MultiSourcePackageAuthoritiesV2(_Frozen):
    bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    golden_sha256: str = Field(pattern=_SHA256_PATTERN)
    evaluation_sha256: str = Field(pattern=_SHA256_PATTERN)
    security_sha256: str = Field(pattern=_SHA256_PATTERN)
    performance_sha256: str = Field(pattern=_SHA256_PATTERN)
    release_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_artifacts_sha256: str = Field(pattern=_SHA256_PATTERN)
    caller_integrity_sha256: str = Field(pattern=_SHA256_PATTERN)
    authority_attestation_sha256: None = None
    authority_attested: Literal[False] = False
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> MultiSourcePackageAuthoritiesV2:
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("multi-source package authority digest mismatch")
        return self


class MultiSourcePackageQualificationV2(_Frozen):
    status: Literal["QUALITY_HOLD_NON_QUALIFIED"] = "QUALITY_HOLD_NON_QUALIFIED"
    release_decision: Literal["HOLD_DEFAULT_V1"] = "HOLD_DEFAULT_V1"
    default_engine: Literal["v1"] = "v1"
    evaluation_qualified: Literal[False] = False
    qualified: Literal[False] = False
    audit_only: Literal[True] = True
    release_sha256: str = Field(pattern=_SHA256_PATTERN)
    blockers: tuple[str, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> MultiSourcePackageQualificationV2:
        if self.blockers != tuple(sorted(set(self.blockers))):
            raise ValueError("qualification blockers must be sorted and unique")
        if "outer_package_audit_only" not in self.blockers:
            raise ValueError("outer package must remain audit-only")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("multi-source package qualification digest mismatch")
        return self


class MultiSourceReleasePackageManifestV2(_Frozen):
    package_version: Literal["multisource-release-audit-package-v1"] = (
        MULTISOURCE_RELEASE_PACKAGE_VERSION
    )
    bundle_version: Literal["multisource-evidence-bundle-v2"] = MULTISOURCE_EVIDENCE_BUNDLE_VERSION
    files: tuple[str, ...] = MULTISOURCE_RELEASE_PACKAGE_FILES
    bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    authorities_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_artifacts_sha256: str = Field(pattern=_SHA256_PATTERN)
    qualification_sha256: str = Field(pattern=_SHA256_PATTERN)
    default_engine: Literal["v1"] = "v1"
    audit_only: Literal[True] = True
    test_only: Literal[True] = True
    exact_files: Literal[True] = True
    portable: Literal[True] = True
    authority_attested: Literal[False] = False
    qualified: Literal[False] = False
    source_artifacts_embedded: Literal[False] = False
    retrieval_executed: Literal[False] = False
    database_accessed: Literal[False] = False
    network_accessed: Literal[False] = False
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> MultiSourceReleasePackageManifestV2:
        if self.files != MULTISOURCE_RELEASE_PACKAGE_FILES:
            raise ValueError("release package file membership mismatch")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("release package manifest digest mismatch")
        return self


class MultiSourceReleasePackageChecksumsV2(_Frozen):
    package_version: Literal["multisource-release-audit-package-v1"] = (
        MULTISOURCE_RELEASE_PACKAGE_VERSION
    )
    checksums: tuple[tuple[str, str], ...]
    package_set_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> MultiSourceReleasePackageChecksumsV2:
        expected_names = _CHECKSUM_TARGETS
        if tuple(name for name, _ in self.checksums) != expected_names:
            raise ValueError("release package checksum membership mismatch")
        if any(_SHA256_RE.fullmatch(value) is None for _, value in self.checksums):
            raise ValueError("release package checksum is malformed")
        if self.package_set_sha256 != canonical_sha256_v2(dict(self.checksums)):
            raise ValueError("release package set digest mismatch")
        return self


class MultiSourceReleasePackageVerificationV2(_Frozen):
    status: Literal["VERIFIED_QUALITY_HOLD_NON_QUALIFIED"] = "VERIFIED_QUALITY_HOLD_NON_QUALIFIED"
    package_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    caller_integrity_sha256: str = Field(pattern=_SHA256_PATTERN)
    authority_attestation_sha256: None = None
    authority_attested: Literal[False] = False
    source_artifact_count: Literal[6] = 6
    qualified: Literal[False] = False
    test_only: Literal[True] = True
    exact_files: Literal[True] = True
    portable: Literal[True] = True
    verify_only: Literal[True] = True
    retrieval_executed: Literal[False] = False
    database_accessed: Literal[False] = False
    network_accessed: Literal[False] = False
    verifier_version: Literal["multisource-release-audit-package-verify-v1"] = (
        MULTISOURCE_RELEASE_PACKAGE_VERIFY_VERSION
    )


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_file_bytes(value: object) -> bytes:
    return canonical_json_bytes_v2(value) + b"\n"


def _strict_json_loads(value: bytes) -> object:
    def reject_duplicate(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, child in pairs:
            if key in result:
                raise MultiSourceReleasePackageError("duplicate JSON key")
            result[key] = child
        return result

    def reject_constant(_: str) -> object:
        raise MultiSourceReleasePackageError("non-finite JSON number")

    try:
        return json.loads(
            value,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MultiSourceReleasePackageError("release package contains invalid JSON") from error


def _decoded_views(value: str) -> tuple[str, ...]:
    views: list[str] = []
    current = unicodedata.normalize("NFKC", value)
    for _ in range(5):
        if current in views:
            break
        views.append(current)
        current = unicodedata.normalize("NFKC", unquote(current))
    return tuple(views)


def _scan_security(value: object, *, path: str = "$") -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise MultiSourceReleasePackageError("non-finite package value")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise MultiSourceReleasePackageError("package keys must be strings")
            if _SECRET_KEY_RE.fullmatch(key):
                raise MultiSourceReleasePackageError("secret-bearing package key")
            _scan_security(key, path=f"{path}.<key>")
            _scan_security(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _scan_security(child, path=f"{path}[{index}]")
        return
    if not isinstance(value, str):
        raise MultiSourceReleasePackageError("unsupported package value")
    if _CONTROL_RE.search(value):
        raise MultiSourceReleasePackageError("control character in package")
    for view in _decoded_views(value):
        if _SECRET_RE.search(view) or _EMAIL_RE.search(view):
            raise MultiSourceReleasePackageError("secret or direct contact data in package")
        if (
            _POSIX_ABSOLUTE_RE.search(view)
            or _GENERIC_POSIX_ABSOLUTE_RE.search(view)
            or _WINDOWS_ABSOLUTE_RE.search(view)
            or _UNC_RE.search(view)
        ):
            raise MultiSourceReleasePackageError("absolute or UNC path in package")
        if _FORBIDDEN_FILE_RE.search(view):
            raise MultiSourceReleasePackageError("database, sidecar, or bytecode in package")


def build_source_artifact_reference_v2(
    *,
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"],
    artifact_uri: str,
    artifact_set_sha256: str,
    verifier_version: str,
    quality_qualified: bool,
) -> SourceArtifactReferenceV2:
    payload = {
        "source": source,
        "artifact_uri": artifact_uri,
        "artifact_set_sha256": artifact_set_sha256,
        "verifier_version": verifier_version,
        "quality_qualified": quality_qualified,
    }
    return SourceArtifactReferenceV2(
        **payload,
        content_sha256=canonical_sha256_v2(payload),
    )


def _build_reference_set(
    references: tuple[SourceArtifactReferenceV2, ...],
) -> SourceArtifactReferenceSetV2:
    payload = {"references": references}
    return SourceArtifactReferenceSetV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            SourceArtifactReferenceSetV2.model_construct(
                **payload, content_sha256="pending"
            ).model_dump(mode="json", exclude={"content_sha256"})
        ),
    )


def _bind_bundle(
    bundle: MultiSourceEvidenceBundleV2,
    references: SourceArtifactReferenceSetV2,
    caller_integrity_sha256: str,
) -> tuple[
    MultiSourcePackageAuthoritiesV2,
    MultiSourcePackageQualificationV2,
    MultiSourceReleasePackageManifestV2,
]:
    if _SHA256_RE.fullmatch(caller_integrity_sha256) is None:
        raise MultiSourceReleasePackageError("outer caller integrity digest is malformed")
    if (
        bundle.release.decision != "HOLD_DEFAULT_V1"
        or bundle.default_engine != "v1"
        or bundle.release.default_engine != "v1"
        or bundle.evaluation.qualified
    ):
        raise MultiSourceReleasePackageError(
            "outer package v1 only represents QUALITY_HOLD evidence"
        )
    source_truth = {item.source: item for item in bundle.release.source_truth}
    for reference in references.references:
        truth = source_truth[reference.source]
        if reference.quality_qualified != truth.quality_qualified:
            raise MultiSourceReleasePackageError("source qualification truth mismatch")
        if truth.evidence_uri is not None and reference.artifact_uri != truth.evidence_uri:
            raise MultiSourceReleasePackageError("source artifact URI truth mismatch")
        if (
            truth.evidence_sha256 is not None
            and reference.artifact_set_sha256 != truth.evidence_sha256
        ):
            raise MultiSourceReleasePackageError("source artifact digest truth mismatch")

    authority_payload = {
        "bundle_sha256": bundle.content_sha256,
        "golden_sha256": bundle.golden.content_sha256,
        "evaluation_sha256": bundle.evaluation.content_sha256,
        "security_sha256": bundle.security.content_sha256,
        "performance_sha256": bundle.performance.content_sha256,
        "release_sha256": bundle.release.content_sha256,
        "source_artifacts_sha256": references.content_sha256,
        # This value is intentionally integrity-only.  The legacy audit
        # package has no production authority attestation and cannot acquire
        # one by calling this writer.
        "caller_integrity_sha256": caller_integrity_sha256,
        "authority_attestation_sha256": None,
        "authority_attested": False,
    }
    authorities = MultiSourcePackageAuthoritiesV2(
        **authority_payload,
        content_sha256=canonical_sha256_v2(authority_payload),
    )
    blockers = tuple(sorted({*bundle.release.blockers, "outer_package_audit_only"}))
    qualification_payload = {
        "status": "QUALITY_HOLD_NON_QUALIFIED",
        "release_decision": "HOLD_DEFAULT_V1",
        "default_engine": "v1",
        "evaluation_qualified": False,
        "qualified": False,
        "audit_only": True,
        "release_sha256": bundle.release.content_sha256,
        "blockers": blockers,
    }
    qualification = MultiSourcePackageQualificationV2(
        **qualification_payload,
        content_sha256=canonical_sha256_v2(qualification_payload),
    )
    manifest_payload = {
        "package_version": MULTISOURCE_RELEASE_PACKAGE_VERSION,
        "bundle_version": MULTISOURCE_EVIDENCE_BUNDLE_VERSION,
        "files": MULTISOURCE_RELEASE_PACKAGE_FILES,
        "bundle_sha256": bundle.content_sha256,
        "authorities_sha256": authorities.content_sha256,
        "source_artifacts_sha256": references.content_sha256,
        "qualification_sha256": qualification.content_sha256,
        "default_engine": "v1",
        "audit_only": True,
        "test_only": True,
        "exact_files": True,
        "portable": True,
        "authority_attested": False,
        "qualified": False,
        "source_artifacts_embedded": False,
        "retrieval_executed": False,
        "database_accessed": False,
        "network_accessed": False,
    }
    manifest = MultiSourceReleasePackageManifestV2(
        **manifest_payload,
        content_sha256=canonical_sha256_v2(manifest_payload),
    )
    return authorities, qualification, manifest


def _payloads_for_package(
    *,
    bundle: MultiSourceEvidenceBundleV2,
    references: tuple[SourceArtifactReferenceV2, ...],
    caller_integrity_sha256: str,
) -> dict[str, object]:
    canonical_bundle = MultiSourceEvidenceBundleV2.model_validate(bundle.model_dump(mode="json"))
    if serialize_multisource_bundle_v2(canonical_bundle) != canonical_json_bytes_v2(
        bundle.model_dump(mode="json")
    ):
        raise MultiSourceReleasePackageError("bundle is not canonical")
    reference_set = _build_reference_set(references)
    authorities, qualification, manifest = _bind_bundle(
        canonical_bundle,
        reference_set,
        caller_integrity_sha256,
    )
    payloads: dict[str, object] = {
        "manifest.json": manifest.model_dump(mode="json"),
        "bundle.json": canonical_bundle.model_dump(mode="json"),
        "source_artifacts.json": reference_set.model_dump(mode="json"),
        "authorities.json": authorities.model_dump(mode="json"),
        "qualification.json": qualification.model_dump(mode="json"),
    }
    _scan_security(payloads)
    return payloads


def write_multisource_release_package_v2(
    *,
    output_dir: Path,
    bundle: MultiSourceEvidenceBundleV2,
    source_artifacts: tuple[SourceArtifactReferenceV2, ...],
    authority_sha256: str,
) -> MultiSourceReleasePackageVerificationV2:
    """Write one exact test-only audit package without dereferencing artifacts.

    ``authority_sha256`` is a retained legacy parameter name.  It is recorded
    only as caller-provided integrity metadata and never as an authority
    attestation.
    """

    if not output_dir.is_absolute():
        raise MultiSourceReleasePackageError("package output must be an absolute path")
    if output_dir.exists() or output_dir.is_symlink():
        raise MultiSourceReleasePackageError("package output must be new")
    parent = output_dir.parent
    if not parent.is_dir() or parent.is_symlink():
        raise MultiSourceReleasePackageError("package parent must be a real directory")
    payloads = _payloads_for_package(
        bundle=bundle,
        references=source_artifacts,
        caller_integrity_sha256=authority_sha256,
    )
    created = False
    try:
        output_dir.mkdir(mode=0o700)
        created = True
        for filename in _CHECKSUM_TARGETS:
            (output_dir / filename).write_bytes(_canonical_file_bytes(payloads[filename]))
        checksums = tuple(
            (
                filename,
                _sha256_bytes((output_dir / filename).read_bytes()),
            )
            for filename in _CHECKSUM_TARGETS
        )
        checksum_payload = MultiSourceReleasePackageChecksumsV2(
            package_version=MULTISOURCE_RELEASE_PACKAGE_VERSION,
            checksums=checksums,
            package_set_sha256=canonical_sha256_v2(dict(checksums)),
        )
        (output_dir / "checksums.json").write_bytes(
            _canonical_file_bytes(checksum_payload.model_dump(mode="json"))
        )
        return verify_multisource_release_package_v2(output_dir)
    except Exception:
        if created:
            for filename in MULTISOURCE_RELEASE_PACKAGE_FILES:
                path = output_dir / filename
                if path.exists() or path.is_symlink():
                    path.unlink()
            output_dir.rmdir()
        raise


def _load_exact_payloads(package_dir: Path) -> dict[str, object]:
    if not package_dir.is_absolute() or not package_dir.is_dir() or package_dir.is_symlink():
        raise MultiSourceReleasePackageError("package directory must be an absolute real directory")
    entries = tuple(package_dir.iterdir())
    if tuple(sorted(path.name for path in entries)) != tuple(
        sorted(MULTISOURCE_RELEASE_PACKAGE_FILES)
    ):
        raise MultiSourceReleasePackageError("package must contain exact canonical file membership")
    payloads: dict[str, object] = {}
    for filename in MULTISOURCE_RELEASE_PACKAGE_FILES:
        path = package_dir / filename
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise MultiSourceReleasePackageError("package entries must be regular files")
        raw = path.read_bytes()
        payload = _strict_json_loads(raw)
        if raw != _canonical_file_bytes(payload):
            raise MultiSourceReleasePackageError("package JSON is not canonical")
        payloads[filename] = payload
    return payloads


def verify_multisource_release_package_v2(
    package_dir: Path,
) -> MultiSourceReleasePackageVerificationV2:
    """Verify exact canonical files using only portable local JSON bytes."""

    payloads = _load_exact_payloads(package_dir)
    checksums = MultiSourceReleasePackageChecksumsV2.model_validate(payloads["checksums.json"])
    if (package_dir / "checksums.json").read_bytes() != _canonical_file_bytes(
        checksums.model_dump(mode="json")
    ):
        raise MultiSourceReleasePackageError("checksum contract is not type-canonical")
    for filename, expected in checksums.checksums:
        observed = _sha256_bytes((package_dir / filename).read_bytes())
        if observed != expected:
            raise MultiSourceReleasePackageError("release package checksum mismatch")
    if checksums.package_set_sha256 != canonical_sha256_v2(dict(checksums.checksums)):
        raise MultiSourceReleasePackageError("release package set digest mismatch")

    _scan_security(
        {name: payload for name, payload in payloads.items() if name != "checksums.json"}
    )
    bundle = MultiSourceEvidenceBundleV2.model_validate(payloads["bundle.json"])
    if (package_dir / "bundle.json").read_bytes() != serialize_multisource_bundle_v2(
        bundle
    ) + b"\n":
        raise MultiSourceReleasePackageError("bundle file is not canonical")
    references = SourceArtifactReferenceSetV2.model_validate(payloads["source_artifacts.json"])
    authorities = MultiSourcePackageAuthoritiesV2.model_validate(payloads["authorities.json"])
    qualification = MultiSourcePackageQualificationV2.model_validate(payloads["qualification.json"])
    manifest = MultiSourceReleasePackageManifestV2.model_validate(payloads["manifest.json"])
    typed_contracts: tuple[tuple[str, BaseModel], ...] = (
        ("source_artifacts.json", references),
        ("authorities.json", authorities),
        ("qualification.json", qualification),
        ("manifest.json", manifest),
    )
    for filename, contract in typed_contracts:
        if (package_dir / filename).read_bytes() != _canonical_file_bytes(
            contract.model_dump(mode="json")
        ):
            raise MultiSourceReleasePackageError("release package contract is not type-canonical")
    expected_authorities, expected_qualification, expected_manifest = _bind_bundle(
        bundle,
        references,
        authorities.caller_integrity_sha256,
    )
    if (
        authorities != expected_authorities
        or qualification != expected_qualification
        or manifest != expected_manifest
    ):
        raise MultiSourceReleasePackageError("release package cross-file mismatch")
    return MultiSourceReleasePackageVerificationV2(
        package_set_sha256=checksums.package_set_sha256,
        bundle_sha256=bundle.content_sha256,
        caller_integrity_sha256=authorities.caller_integrity_sha256,
    )


__all__ = [
    "MULTISOURCE_RELEASE_PACKAGE_FILES",
    "MULTISOURCE_RELEASE_PACKAGE_VERIFY_VERSION",
    "MULTISOURCE_RELEASE_PACKAGE_VERSION",
    "MultiSourcePackageAuthoritiesV2",
    "MultiSourcePackageQualificationV2",
    "MultiSourceReleasePackageChecksumsV2",
    "MultiSourceReleasePackageError",
    "MultiSourceReleasePackageManifestV2",
    "MultiSourceReleasePackageVerificationV2",
    "SourceArtifactReferenceSetV2",
    "SourceArtifactReferenceV2",
    "build_source_artifact_reference_v2",
    "verify_multisource_release_package_v2",
    "write_multisource_release_package_v2",
]
