"""Version-exact TestResult and Test-to-Symbol evidence treatment.

This module is deliberately runtime and persistence independent.  It consumes
already observed or reported test facts, aligns them to one exact commit or
worktree snapshot, and emits only relations accepted by the C4 graph registry.
Weak static and name/path evidence remains reviewable candidate material and
never becomes a graph assertion.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol, Self, runtime_checkable

from ....models import EvidenceSearchRequest
from .contracts import (
    CodeCandidateRole,
    CodeChannelRank,
    CodeChannelScore,
    CodeDerivation,
    CodeFactStatus,
    CodeRelationType,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
)
from .graph_v2 import (
    CodeEdgeDerivationLayer,
    CodeGraphEntityType,
    validate_edge_assertion,
)
from .query_profile_v2 import CodeOptionalHookResult, CodeOptionalHookStatus

TEST_VALIDATION_CONTRACT_VERSION = "c6-test-validation-v2"
TEST_VALIDATION_BINDER_VERSION = "c6-test-validation-binder-v2"

_FULL_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIRTY_VERSION = re.compile(
    r"^(?P<base>[0-9a-f]{40}(?:[0-9a-f]{24})?)"
    r"\+dirty\.(?P<manifest>[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?)$"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class TestValidationError(ValueError):
    """Base fail-closed Test/Validation-to-Symbol contract error."""


class TestValidationScopeError(TestValidationError):
    """An internally contradictory target or evidence scope was supplied."""


class TestObservation(StrEnum):
    OBSERVED = "observed"
    REPORTED = "reported"


class TestExecutionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    UNKNOWN = "unknown"


class TestStatusConsistency(StrEnum):
    CONSISTENT = "consistent"
    MISSING_EXIT_CODE = "missing_exit_code"
    STATUS_EXIT_CONTRADICTION = "status_exit_contradiction"
    REPORTED_OBSERVED_CONTRADICTION = "reported_observed_contradiction"
    REPORTED_ONLY = "reported_only"


class TestSymbolSignal(StrEnum):
    COVERAGE_FUNCTION = "coverage_function"
    COVERAGE_LINE = "coverage_line"
    EXACT_SELECTOR = "exact_selector"
    EXACT_TARGET = "exact_target"
    SCIP = "scip"
    IMPORT = "import"
    CALL = "call"
    PATH_NAME_HEURISTIC = "path_name_heuristic"


_SIGNAL_RANK = {
    TestSymbolSignal.COVERAGE_FUNCTION: 0,
    TestSymbolSignal.COVERAGE_LINE: 1,
    TestSymbolSignal.EXACT_SELECTOR: 2,
    TestSymbolSignal.EXACT_TARGET: 3,
    TestSymbolSignal.SCIP: 4,
    TestSymbolSignal.IMPORT: 5,
    TestSymbolSignal.CALL: 6,
    TestSymbolSignal.PATH_NAME_HEURISTIC: 7,
}


class TestSymbolBindingStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANDIDATE = "candidate"


class ValidationTreatmentStatus(StrEnum):
    VALIDATED = "validated"
    FAILED_VALIDATION = "failed_validation"
    OBSERVATION_ONLY = "observation_only"
    HISTORICAL_VALIDATION = "historical_validation"
    MISSING_CONTEXT = "missing_context"
    REJECTED = "rejected"


class ValidationDiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationDiagnosticCode(StrEnum):
    NO_TEST_RESULT = "no_test_result"
    REPORTED_ONLY = "reported_only"
    MISSING_EXIT_CODE = "missing_exit_code"
    STATUS_EXIT_CONTRADICTION = "status_exit_contradiction"
    REPORTED_OBSERVED_CONTRADICTION = "reported_observed_contradiction"
    HISTORICAL_TEST_RESULT = "historical_test_result"
    SCOPE_MISMATCH = "scope_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    GENERATION_MISMATCH = "generation_mismatch"
    DIRTY_MANIFEST_MISMATCH = "dirty_manifest_mismatch"
    TARGET_IDENTITY_MISMATCH = "target_identity_mismatch"
    TEST_RESULT_ID_MISMATCH = "test_result_id_mismatch"
    SELECTOR_MISMATCH = "selector_mismatch"
    COVERAGE_ARTIFACT_MISMATCH = "coverage_artifact_mismatch"


class TestValidationEdgeRole(StrEnum):
    HAS_TEST_RESULT = "has_test_result"
    VALIDATION_SUCCESS = "validation_success"
    VALIDATION_FAILURE = "validation_failure"
    COVERAGE_OBSERVATION = "coverage_observation"
    EXACT_TEST_TARGET = "exact_test_target"


class _CanonicalContract:
    def canonical_json_bytes(self) -> bytes:
        return _canonical_json(asdict(self))

    def canonical_sha256(self) -> str:
        return _sha256(self.canonical_json_bytes())


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes | str | object) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = _canonical_json(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip() or _CONTROL.search(value):
        raise ValueError(f"{field_name} must be non-empty, trimmed, and control-free")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _full_sha(value: object, field_name: str) -> str:
    sha = _text(value, field_name).lower()
    if not _FULL_SHA.fullmatch(sha):
        raise ValueError(f"{field_name} must be a full Git commit SHA")
    return sha


def _safe_path(value: object, field_name: str = "path") -> str:
    path = _text(value, field_name)
    if "\\" in path:
        raise ValueError(f"{field_name} must use POSIX separators")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError(f"{field_name} must be repository relative")
    normalized = candidate.as_posix()
    if normalized in {"", "."} or normalized.startswith("./"):
        raise ValueError(f"{field_name} must identify a repository-relative path")
    return normalized


def _ordered_unique_text(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return tuple(sorted(normalized))


def _positive_lines(values: Iterable[int]) -> tuple[int, ...]:
    normalized = tuple(values)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in normalized
    ):
        raise ValueError("covered_lines must contain positive integers")
    if len(normalized) != len(set(normalized)):
        raise ValueError("covered_lines must be unique")
    return tuple(sorted(normalized))


def _unit_float(value: object, field_name: str) -> float:
    if type(value) is not float or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be an exact float in [0, 1]")
    return value


@dataclass(frozen=True, slots=True)
class RawObjectRef(_CanonicalContract):
    """Secret-free immutable reference to raw stdout, stderr, or coverage bytes."""

    raw_object_id: str
    content_sha256: str
    byte_size: int
    source_name: str
    media_type: str = "application/octet-stream"
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "raw_object_id",
            "source_name",
            "media_type",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.content_sha256, str) or not _SHA256.fullmatch(self.content_sha256):
            raise ValueError("content_sha256 must be a canonical SHA-256 identity")
        if (
            isinstance(self.byte_size, bool)
            or not isinstance(self.byte_size, int)
            or self.byte_size < 0
        ):
            raise ValueError("byte_size must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class TestTargetVersion(_CanonicalContract):
    """One exact validation target watermark, including dirty worktree identity."""

    project_id: str
    repository_id: str
    commit_sha: str
    stable_version: str
    generation_id: str
    acl_ref: str
    target_id: str
    target_type: CodeGraphEntityType
    dirty_manifest_sha256: str | None = None
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "stable_version",
            "generation_id",
            "acl_ref",
            "target_id",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "commit_sha", _full_sha(self.commit_sha, "commit_sha"))
        allowed = {
            CodeGraphEntityType.COMMIT,
            CodeGraphEntityType.GIT_COMMIT,
            CodeGraphEntityType.WORKTREE,
        }
        if self.target_type not in allowed:
            raise ValueError("validation target must be Commit, GitCommit, or Worktree")
        dirty = _DIRTY_VERSION.fullmatch(self.stable_version)
        if dirty is None:
            if self.stable_version != self.commit_sha:
                raise ValueError("clean target stable_version must equal commit_sha")
            if self.dirty_manifest_sha256 is not None:
                raise ValueError("clean target cannot carry a dirty manifest")
        else:
            if self.target_type is not CodeGraphEntityType.WORKTREE:
                raise ValueError("dirty target must be a Worktree")
            if dirty.group("base") != self.commit_sha:
                raise ValueError("dirty target base SHA must equal commit_sha")
            if self.dirty_manifest_sha256 is None or not _SHA256.fullmatch(
                self.dirty_manifest_sha256
            ):
                raise ValueError("dirty target requires a canonical dirty manifest SHA-256")
            manifest = dirty.group("manifest")
            digest = self.dirty_manifest_sha256.removeprefix("sha256:")
            if manifest not in {digest, self.dirty_manifest_sha256}:
                raise ValueError("stable_version dirty identity must equal dirty manifest")
        scheme = {
            CodeGraphEntityType.COMMIT: "commit",
            CodeGraphEntityType.GIT_COMMIT: "git-commit",
            CodeGraphEntityType.WORKTREE: "worktree",
        }[self.target_type]
        expected_target_id = f"{scheme}://{self.repository_id}@{self.stable_version}"
        if self.target_id != expected_target_id:
            raise ValueError("target_id must identify the exact repository target version")

    @property
    def dirty(self) -> bool:
        return self.dirty_manifest_sha256 is not None

    @classmethod
    def commit(
        cls,
        *,
        project_id: str,
        repository_id: str,
        commit_sha: str,
        generation_id: str,
        acl_ref: str,
        git_commit: bool = False,
    ) -> Self:
        sha = _full_sha(commit_sha, "commit_sha")
        target_type = CodeGraphEntityType.GIT_COMMIT if git_commit else CodeGraphEntityType.COMMIT
        scheme = "git-commit" if git_commit else "commit"
        return cls(
            project_id=project_id,
            repository_id=repository_id,
            commit_sha=sha,
            stable_version=sha,
            generation_id=generation_id,
            acl_ref=acl_ref,
            target_id=f"{scheme}://{repository_id}@{sha}",
            target_type=target_type,
        )

    @classmethod
    def worktree(
        cls,
        *,
        project_id: str,
        repository_id: str,
        commit_sha: str,
        generation_id: str,
        acl_ref: str,
        dirty_manifest_sha256: str | None = None,
    ) -> Self:
        sha = _full_sha(commit_sha, "commit_sha")
        stable_version = sha
        if dirty_manifest_sha256 is not None:
            if not _SHA256.fullmatch(dirty_manifest_sha256):
                raise ValueError("dirty_manifest_sha256 must be a canonical SHA-256 identity")
            stable_version += "+dirty." + dirty_manifest_sha256.removeprefix("sha256:")
        return cls(
            project_id=project_id,
            repository_id=repository_id,
            commit_sha=sha,
            stable_version=stable_version,
            generation_id=generation_id,
            acl_ref=acl_ref,
            target_id=f"worktree://{repository_id}@{stable_version}",
            target_type=CodeGraphEntityType.WORKTREE,
            dirty_manifest_sha256=dirty_manifest_sha256,
        )


def _status_consistency(
    *,
    observation: TestObservation,
    status: TestExecutionStatus,
    exit_code: int | None,
    reported_status: TestExecutionStatus | None,
) -> TestStatusConsistency:
    if observation is TestObservation.REPORTED:
        return TestStatusConsistency.REPORTED_ONLY
    if exit_code is None:
        return TestStatusConsistency.MISSING_EXIT_CODE
    contradictory = (status is TestExecutionStatus.PASSED and exit_code != 0) or (
        status in {TestExecutionStatus.FAILED, TestExecutionStatus.ERROR} and exit_code == 0
    )
    if contradictory:
        return TestStatusConsistency.STATUS_EXIT_CONTRADICTION
    if reported_status is not None and reported_status is not status:
        return TestStatusConsistency.REPORTED_OBSERVED_CONTRADICTION
    return TestStatusConsistency.CONSISTENT


@dataclass(frozen=True, slots=True)
class TestResultV2(_CanonicalContract):
    """Observed or reported test fact bound to one immutable target watermark."""

    test_result_id: str
    target: TestTargetVersion
    command: tuple[str, ...]
    environment_ref: str
    status: TestExecutionStatus
    observation: TestObservation
    framework: str
    framework_parser_version: str
    evidence_locator: str
    selector: str | None = None
    test_cases: tuple[str, ...] = ()
    exit_code: int | None = None
    duration_ms: int | None = None
    stdout_ref: RawObjectRef | None = None
    stderr_ref: RawObjectRef | None = None
    coverage_artifact_ref: RawObjectRef | None = None
    reported_status: TestExecutionStatus | None = None
    observed_at: str | None = None
    source: str = "test-runner"
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION
    status_consistency: TestStatusConsistency = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "test_result_id",
            "environment_ref",
            "framework",
            "framework_parser_version",
            "evidence_locator",
            "source",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.target, TestTargetVersion):
            raise TypeError("target must be a TestTargetVersion")
        if isinstance(self.command, (str, bytes)):
            raise TypeError("command must be a tuple of arguments")
        command = tuple(_text(value, "command argument") for value in self.command)
        if not command:
            raise ValueError("command must contain at least one argument")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "selector", _optional_text(self.selector, "selector"))
        if isinstance(self.test_cases, (str, bytes)):
            raise TypeError("test_cases must be a tuple of selectors")
        object.__setattr__(
            self,
            "test_cases",
            _ordered_unique_text(self.test_cases, "test_case"),
        )
        object.__setattr__(
            self,
            "observed_at",
            _optional_text(self.observed_at, "observed_at"),
        )
        if not isinstance(self.status, TestExecutionStatus):
            raise TypeError("status must be a TestExecutionStatus")
        if not isinstance(self.observation, TestObservation):
            raise TypeError("observation must be a TestObservation")
        if self.reported_status is not None and not isinstance(
            self.reported_status,
            TestExecutionStatus,
        ):
            raise TypeError("reported_status must be a TestExecutionStatus or None")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise TypeError("exit_code must be an integer or None")
        if self.duration_ms is not None and (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or self.duration_ms < 0
        ):
            raise ValueError("duration_ms must be a non-negative integer or None")
        for name in ("stdout_ref", "stderr_ref", "coverage_artifact_ref"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, RawObjectRef):
                raise TypeError(f"{name} must be a RawObjectRef or None")
        object.__setattr__(
            self,
            "status_consistency",
            _status_consistency(
                observation=self.observation,
                status=self.status,
                exit_code=self.exit_code,
                reported_status=self.reported_status,
            ),
        )

    @classmethod
    def create(
        cls,
        *,
        target: TestTargetVersion,
        command: Iterable[str],
        environment_ref: str,
        status: TestExecutionStatus,
        observation: TestObservation,
        framework: str,
        framework_parser_version: str,
        evidence_locator: str,
        selector: str | None = None,
        test_cases: Iterable[str] = (),
        exit_code: int | None = None,
        duration_ms: int | None = None,
        stdout_ref: RawObjectRef | None = None,
        stderr_ref: RawObjectRef | None = None,
        coverage_artifact_ref: RawObjectRef | None = None,
        reported_status: TestExecutionStatus | None = None,
        observed_at: str | None = None,
        source: str = "test-runner",
        test_result_id: str | None = None,
    ) -> Self:
        command_values = tuple(command)
        case_values = tuple(test_cases)
        identity = test_result_id or (
            "test-result://sha256/"
            + _sha256(
                {
                    "command": command_values,
                    "coverage_artifact_ref": (
                        asdict(coverage_artifact_ref) if coverage_artifact_ref is not None else None
                    ),
                    "duration_ms": duration_ms,
                    "environment_ref": environment_ref,
                    "evidence_locator": evidence_locator,
                    "exit_code": exit_code,
                    "framework": framework,
                    "framework_parser_version": framework_parser_version,
                    "observation": observation.value,
                    "observed_at": observed_at,
                    "reported_status": (
                        reported_status.value if reported_status is not None else None
                    ),
                    "selector": selector,
                    "source": source,
                    "status": status.value,
                    "stderr_ref": asdict(stderr_ref) if stderr_ref is not None else None,
                    "stdout_ref": asdict(stdout_ref) if stdout_ref is not None else None,
                    "target": asdict(target),
                    "test_cases": sorted(case_values),
                }
            ).removeprefix("sha256:")
        )
        return cls(
            test_result_id=identity,
            target=target,
            command=command_values,
            environment_ref=environment_ref,
            status=status,
            observation=observation,
            framework=framework,
            framework_parser_version=framework_parser_version,
            evidence_locator=evidence_locator,
            selector=selector,
            test_cases=case_values,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout_ref=stdout_ref,
            stderr_ref=stderr_ref,
            coverage_artifact_ref=coverage_artifact_ref,
            reported_status=reported_status,
            observed_at=observed_at,
            source=source,
        )


@dataclass(frozen=True, slots=True)
class TestSymbolVersion(_CanonicalContract):
    """Exact symbol or file version eligible for TESTS/COVERS evidence."""

    target: TestTargetVersion
    symbol_version_id: str
    entity_id: str
    entity_type: CodeGraphEntityType
    path: str
    evidence_locator: str
    symbol_name: str = ""
    qualified_name: str = ""
    start_line: int | None = None
    end_line: int | None = None
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.target, TestTargetVersion):
            raise TypeError("target must be a TestTargetVersion")
        for name in (
            "symbol_version_id",
            "entity_id",
            "evidence_locator",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "path", _safe_path(self.path))
        for name in ("symbol_name", "qualified_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or _CONTROL.search(value):
                raise ValueError(f"{name} must be a control-free string")
        if self.entity_type not in {
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.FILE_VERSION,
        }:
            raise ValueError("test symbol target must be CodeSymbol or FileVersion")
        if self.entity_type is CodeGraphEntityType.CODE_SYMBOL and (
            not self.symbol_name or not self.qualified_name
        ):
            raise ValueError("CodeSymbol targets require symbol_name and qualified_name")
        if self.entity_type is CodeGraphEntityType.FILE_VERSION and (
            self.symbol_name or self.qualified_name
        ):
            raise ValueError("FileVersion targets cannot masquerade as symbols")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("symbol start/end lines must both be present or absent")
        if self.start_line is not None and (
            self.start_line < 1 or self.end_line is None or self.end_line < self.start_line
        ):
            raise ValueError("symbol version line span must be one-based and ordered")


@dataclass(frozen=True, slots=True)
class CoverageEvidence(_CanonicalContract):
    """Observed line/function coverage with raw artifact provenance."""

    coverage_id: str
    test_result_id: str
    target: TestTargetVersion
    symbol: TestSymbolVersion
    artifact_ref: RawObjectRef
    parser_version: str
    evidence_locator: str
    covered_lines: tuple[int, ...] = ()
    function_name: str | None = None
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "coverage_id",
            "test_result_id",
            "parser_version",
            "evidence_locator",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.target, TestTargetVersion):
            raise TypeError("target must be a TestTargetVersion")
        if not isinstance(self.symbol, TestSymbolVersion):
            raise TypeError("symbol must be a TestSymbolVersion")
        if self.symbol.target != self.target:
            raise TestValidationScopeError("coverage symbol scope conflicts with target")
        if not isinstance(self.artifact_ref, RawObjectRef):
            raise TypeError("artifact_ref must be a RawObjectRef")
        object.__setattr__(self, "covered_lines", _positive_lines(self.covered_lines))
        object.__setattr__(
            self,
            "function_name",
            _optional_text(self.function_name, "function_name"),
        )
        if not self.covered_lines and self.function_name is None:
            raise ValueError("coverage requires covered lines or an exact function name")
        if (
            self.covered_lines
            and self.symbol.start_line is not None
            and any(
                line < self.symbol.start_line
                or self.symbol.end_line is None
                or line > self.symbol.end_line
                for line in self.covered_lines
            )
        ):
            raise ValueError("covered lines must fall within the exact symbol interval")

    @property
    def signal(self) -> TestSymbolSignal:
        if self.function_name is not None:
            return TestSymbolSignal.COVERAGE_FUNCTION
        return TestSymbolSignal.COVERAGE_LINE


@dataclass(frozen=True, slots=True)
class TestSymbolEvidence(_CanonicalContract):
    """Selector/static/heuristic evidence proposed for one Test-to-Symbol binding."""

    evidence_id: str
    test_result_id: str
    target: TestTargetVersion
    test_case_id: str
    symbol: TestSymbolVersion
    signal: TestSymbolSignal
    confidence: float
    parser_version: str
    evidence_locator: str
    selector: str | None = None
    declared_target_symbol_version_id: str | None = None
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "test_result_id",
            "test_case_id",
            "parser_version",
            "evidence_locator",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.target, TestTargetVersion):
            raise TypeError("target must be a TestTargetVersion")
        if not isinstance(self.symbol, TestSymbolVersion):
            raise TypeError("symbol must be a TestSymbolVersion")
        if self.symbol.target != self.target:
            raise TestValidationScopeError("test symbol scope conflicts with target")
        if not isinstance(self.signal, TestSymbolSignal):
            raise TypeError("signal must be a TestSymbolSignal")
        if self.signal in {
            TestSymbolSignal.COVERAGE_FUNCTION,
            TestSymbolSignal.COVERAGE_LINE,
        }:
            raise ValueError("coverage signals require CoverageEvidence")
        _unit_float(self.confidence, "confidence")
        object.__setattr__(self, "selector", _optional_text(self.selector, "selector"))
        object.__setattr__(
            self,
            "declared_target_symbol_version_id",
            _optional_text(
                self.declared_target_symbol_version_id,
                "declared_target_symbol_version_id",
            ),
        )
        if self.signal is TestSymbolSignal.EXACT_SELECTOR and self.selector is None:
            raise ValueError("exact selector evidence requires selector")
        if (
            self.signal is TestSymbolSignal.EXACT_TARGET
            and self.declared_target_symbol_version_id != self.symbol.symbol_version_id
        ):
            raise ValueError("exact target evidence must name the exact symbol_version_id")


@dataclass(frozen=True, slots=True)
class TestSymbolBinding(_CanonicalContract):
    binding_id: str
    target: TestTargetVersion
    test_result_id: str
    test_case_id: str | None
    symbol: TestSymbolVersion
    evidence_id: str
    signal: TestSymbolSignal
    rank: int
    status: TestSymbolBindingStatus
    relation_type: CodeRelationType
    confidence: float
    review_required: bool
    evidence_locator: str
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "binding_id",
            "test_result_id",
            "evidence_id",
            "evidence_locator",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "test_case_id",
            _optional_text(self.test_case_id, "test_case_id"),
        )
        if self.symbol.target != self.target:
            raise TestValidationScopeError("binding symbol scope conflicts with target")
        if not isinstance(self.signal, TestSymbolSignal):
            raise TypeError("signal must be a TestSymbolSignal")
        if self.rank != _SIGNAL_RANK[self.signal]:
            raise ValueError("binding rank must equal the frozen evidence priority")
        if not isinstance(self.status, TestSymbolBindingStatus):
            raise TypeError("status must be a TestSymbolBindingStatus")
        confirmed = self.signal in {
            TestSymbolSignal.COVERAGE_FUNCTION,
            TestSymbolSignal.COVERAGE_LINE,
            TestSymbolSignal.EXACT_SELECTOR,
            TestSymbolSignal.EXACT_TARGET,
        }
        expected_status = (
            TestSymbolBindingStatus.CONFIRMED if confirmed else TestSymbolBindingStatus.CANDIDATE
        )
        if self.status is not expected_status or self.review_required is confirmed:
            raise ValueError("binding review/status conflicts with evidence strength")
        expected_relation = (
            CodeRelationType.COVERS
            if self.signal
            in {
                TestSymbolSignal.COVERAGE_FUNCTION,
                TestSymbolSignal.COVERAGE_LINE,
            }
            else CodeRelationType.TESTS
        )
        if self.relation_type is not expected_relation:
            raise ValueError("binding relation conflicts with evidence signal")
        if (
            self.signal is TestSymbolSignal.PATH_NAME_HEURISTIC
            and self.relation_type is CodeRelationType.COVERS
        ):
            raise ValueError("heuristic evidence can never claim COVERS")
        _unit_float(self.confidence, "confidence")


@dataclass(frozen=True, slots=True)
class TestValidationEdge(_CanonicalContract):
    edge_id: str
    target: TestTargetVersion
    relation_type: CodeRelationType
    source_id: str
    source_type: CodeGraphEntityType
    target_id: str
    target_type: CodeGraphEntityType
    role: TestValidationEdgeRole
    evidence_id: str
    evidence_locator: str
    confidence: float = 1.0
    derivation: CodeEdgeDerivationLayer = CodeEdgeDerivationLayer.DETERMINISTIC
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "edge_id",
            "source_id",
            "target_id",
            "evidence_id",
            "evidence_locator",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.target, TestTargetVersion):
            raise TypeError("target must be a TestTargetVersion")
        if not isinstance(self.relation_type, CodeRelationType):
            raise TypeError("relation_type must be a registered CodeRelationType")
        if not isinstance(self.source_type, CodeGraphEntityType) or not isinstance(
            self.target_type,
            CodeGraphEntityType,
        ):
            raise TypeError("edge endpoint types must be CodeGraphEntityType values")
        if not isinstance(self.role, TestValidationEdgeRole):
            raise TypeError("role must be a TestValidationEdgeRole")
        _unit_float(self.confidence, "confidence")
        validate_edge_assertion(
            self.relation_type,
            source_type=self.source_type,
            target_type=self.target_type,
            derivation=self.derivation,
        )
        expected = {
            TestValidationEdgeRole.HAS_TEST_RESULT: CodeRelationType.CONTAINS,
            TestValidationEdgeRole.VALIDATION_SUCCESS: CodeRelationType.VALIDATED_BY,
            TestValidationEdgeRole.VALIDATION_FAILURE: CodeRelationType.FAILED_VALIDATION,
            TestValidationEdgeRole.COVERAGE_OBSERVATION: CodeRelationType.COVERS,
            TestValidationEdgeRole.EXACT_TEST_TARGET: CodeRelationType.TESTS,
        }[self.role]
        if self.relation_type is not expected:
            raise ValueError("edge role conflicts with registered relation type")
        if self.role is TestValidationEdgeRole.HAS_TEST_RESULT and (
            self.source_type is not self.target.target_type
            or self.target_type is not CodeGraphEntityType.TEST_RESULT
        ):
            raise ValueError("has_test_result must be target CONTAINS TestResult")
        if self.role in {
            TestValidationEdgeRole.VALIDATION_SUCCESS,
            TestValidationEdgeRole.VALIDATION_FAILURE,
        } and (
            self.source_type is not self.target.target_type
            or self.target_type is not CodeGraphEntityType.TEST_RESULT
        ):
            raise ValueError("validation status edges must bind target to TestResult")


@dataclass(frozen=True, slots=True)
class ValidationDiagnostic(_CanonicalContract):
    diagnostic_id: str
    target: TestTargetVersion
    code: ValidationDiagnosticCode
    severity: ValidationDiagnosticSeverity
    message: str
    evidence_ids: tuple[str, ...] = ()
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "diagnostic_id",
            "message",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.target, TestTargetVersion):
            raise TypeError("target must be a TestTargetVersion")
        if not isinstance(self.code, ValidationDiagnosticCode):
            raise TypeError("code must be a ValidationDiagnosticCode")
        if not isinstance(self.severity, ValidationDiagnosticSeverity):
            raise TypeError("severity must be a ValidationDiagnosticSeverity")
        object.__setattr__(
            self,
            "evidence_ids",
            _ordered_unique_text(self.evidence_ids, "evidence_id"),
        )


@dataclass(frozen=True, slots=True)
class TestValidationTrace(_CanonicalContract):
    target: TestTargetVersion
    status: ValidationTreatmentStatus
    test_results_seen: int
    coverage_evidence_seen: int
    symbol_evidence_seen: int
    confirmed_bindings: int
    candidate_bindings: int
    registered_edges: int
    diagnostics: int
    canonical_input_sha256: str
    binder_version: str = TEST_VALIDATION_BINDER_VERSION
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.target, TestTargetVersion):
            raise TypeError("target must be a TestTargetVersion")
        if not isinstance(self.status, ValidationTreatmentStatus):
            raise TypeError("status must be a ValidationTreatmentStatus")
        for name in (
            "test_results_seen",
            "coverage_evidence_seen",
            "symbol_evidence_seen",
            "confirmed_bindings",
            "candidate_bindings",
            "registered_edges",
            "diagnostics",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not _SHA256.fullmatch(self.canonical_input_sha256):
            raise ValueError("canonical_input_sha256 must be canonical")
        for name in ("binder_version", "contract_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class ValidationTreatment(_CanonicalContract):
    status: ValidationTreatmentStatus
    target: TestTargetVersion
    test_result: TestResultV2 | None
    bindings: tuple[TestSymbolBinding, ...]
    edges: tuple[TestValidationEdge, ...]
    diagnostics: tuple[ValidationDiagnostic, ...]
    trace: TestValidationTrace
    contract_version: str = TEST_VALIDATION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.status, ValidationTreatmentStatus):
            raise TypeError("status must be a ValidationTreatmentStatus")
        if not isinstance(self.target, TestTargetVersion):
            raise TypeError("target must be a TestTargetVersion")
        for name in ("bindings", "edges", "diagnostics"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(
            self,
            "contract_version",
            _text(self.contract_version, "contract_version"),
        )
        for values, field_name, key in (
            (self.bindings, "bindings", lambda item: item.binding_id),
            (self.edges, "edges", lambda item: item.edge_id),
            (self.diagnostics, "diagnostics", lambda item: item.diagnostic_id),
        ):
            identities = tuple(key(item) for item in values)
            if len(identities) != len(set(identities)):
                raise ValueError(f"{field_name} cannot contain duplicates")
        if self.trace.target != self.target or self.trace.status is not self.status:
            raise ValueError("treatment trace conflicts with target/status")
        if any(item.target != self.target for item in self.bindings):
            raise TestValidationScopeError("binding scope conflicts with treatment")
        if any(item.target != self.target for item in self.edges):
            raise TestValidationScopeError("edge scope conflicts with treatment")
        if any(item.target != self.target for item in self.diagnostics):
            raise TestValidationScopeError("diagnostic scope conflicts with treatment")
        confirmed = sum(item.status is TestSymbolBindingStatus.CONFIRMED for item in self.bindings)
        candidates = sum(item.status is TestSymbolBindingStatus.CANDIDATE for item in self.bindings)
        if (
            self.trace.confirmed_bindings != confirmed
            or self.trace.candidate_bindings != candidates
            or self.trace.registered_edges != len(self.edges)
            or self.trace.diagnostics != len(self.diagnostics)
        ):
            raise ValueError("treatment accounting conflicts with trace")
        if (
            self.status
            in {
                ValidationTreatmentStatus.MISSING_CONTEXT,
                ValidationTreatmentStatus.REJECTED,
            }
            and self.test_result is not None
        ):
            raise ValueError("missing/rejected treatment cannot retain a test result")
        if self.status is ValidationTreatmentStatus.HISTORICAL_VALIDATION and (
            self.test_result is None or self.test_result.target == self.target
        ):
            raise ValueError("historical treatment requires a different target version")


def _diagnostic(
    target: TestTargetVersion,
    *,
    code: ValidationDiagnosticCode,
    severity: ValidationDiagnosticSeverity,
    message: str,
    evidence_ids: Iterable[str] = (),
) -> ValidationDiagnostic:
    identifiers = tuple(sorted(set(evidence_ids)))
    payload = {
        "code": code.value,
        "evidence_ids": identifiers,
        "message": message,
        "target": asdict(target),
    }
    return ValidationDiagnostic(
        diagnostic_id=(
            "test-validation-diagnostic://sha256/" + _sha256(payload).removeprefix("sha256:")
        ),
        target=target,
        code=code,
        severity=severity,
        message=message,
        evidence_ids=identifiers,
    )


def _target_mismatch(
    expected: TestTargetVersion,
    actual: TestTargetVersion,
    *,
    historical_allowed: bool,
) -> ValidationDiagnosticCode | None:
    if (
        expected.project_id,
        expected.repository_id,
        expected.acl_ref,
    ) != (
        actual.project_id,
        actual.repository_id,
        actual.acl_ref,
    ):
        return ValidationDiagnosticCode.SCOPE_MISMATCH
    if expected.stable_version != actual.stable_version:
        if expected.commit_sha == actual.commit_sha and expected.dirty and actual.dirty:
            return ValidationDiagnosticCode.DIRTY_MANIFEST_MISMATCH
        if historical_allowed:
            return ValidationDiagnosticCode.HISTORICAL_TEST_RESULT
        return ValidationDiagnosticCode.VERSION_MISMATCH
    if expected.commit_sha != actual.commit_sha:
        return ValidationDiagnosticCode.VERSION_MISMATCH
    if expected.generation_id != actual.generation_id:
        return ValidationDiagnosticCode.GENERATION_MISMATCH
    if expected.target_id != actual.target_id or expected.target_type is not actual.target_type:
        return ValidationDiagnosticCode.TARGET_IDENTITY_MISMATCH
    if expected.dirty_manifest_sha256 != actual.dirty_manifest_sha256:
        return ValidationDiagnosticCode.DIRTY_MANIFEST_MISMATCH
    return None


def _mismatch_message(code: ValidationDiagnosticCode) -> str:
    return {
        ValidationDiagnosticCode.SCOPE_MISMATCH: (
            "test evidence project/repository/ACL conflicts with requested target"
        ),
        ValidationDiagnosticCode.VERSION_MISMATCH: (
            "test evidence is not bound to the exact requested stable version"
        ),
        ValidationDiagnosticCode.GENERATION_MISMATCH: (
            "test evidence generation conflicts with requested target"
        ),
        ValidationDiagnosticCode.DIRTY_MANIFEST_MISMATCH: (
            "test evidence dirty manifest conflicts with requested worktree"
        ),
        ValidationDiagnosticCode.TARGET_IDENTITY_MISMATCH: (
            "test evidence commit/worktree identity conflicts with requested target"
        ),
        ValidationDiagnosticCode.HISTORICAL_TEST_RESULT: (
            "test result belongs to an older exact target and is historical only"
        ),
    }[code]


def _edge(
    *,
    target: TestTargetVersion,
    relation_type: CodeRelationType,
    source_id: str,
    source_type: CodeGraphEntityType,
    target_id: str,
    target_type: CodeGraphEntityType,
    role: TestValidationEdgeRole,
    evidence_id: str,
    evidence_locator: str,
    confidence: float = 1.0,
) -> TestValidationEdge:
    payload = {
        "evidence_id": evidence_id,
        "relation_type": relation_type.value,
        "role": role.value,
        "source_id": source_id,
        "stable_version": target.stable_version,
        "target_id": target_id,
    }
    return TestValidationEdge(
        edge_id="test-validation-edge://sha256/" + _sha256(payload).removeprefix("sha256:"),
        target=target,
        relation_type=relation_type,
        source_id=source_id,
        source_type=source_type,
        target_id=target_id,
        target_type=target_type,
        role=role,
        evidence_id=evidence_id,
        evidence_locator=evidence_locator,
        confidence=confidence,
    )


def _containment_edge(
    target: TestTargetVersion,
    result: TestResultV2,
) -> TestValidationEdge:
    return _edge(
        target=target,
        relation_type=CodeRelationType.CONTAINS,
        source_id=target.target_id,
        source_type=target.target_type,
        target_id=result.test_result_id,
        target_type=CodeGraphEntityType.TEST_RESULT,
        role=TestValidationEdgeRole.HAS_TEST_RESULT,
        evidence_id=result.test_result_id,
        evidence_locator=result.evidence_locator,
    )


def _validation_relation(result: TestResultV2) -> CodeRelationType | None:
    if result.status_consistency is not TestStatusConsistency.CONSISTENT:
        return None
    if result.status in {TestExecutionStatus.FAILED, TestExecutionStatus.ERROR} or (
        result.exit_code is not None and result.exit_code != 0
    ):
        return CodeRelationType.FAILED_VALIDATION
    if result.status is TestExecutionStatus.PASSED and result.exit_code == 0:
        return CodeRelationType.VALIDATED_BY
    return None


def _status_diagnostics(
    target: TestTargetVersion,
    result: TestResultV2,
) -> tuple[ValidationDiagnostic, ...]:
    diagnostics: list[ValidationDiagnostic] = []
    if result.observation is TestObservation.REPORTED:
        diagnostics.append(
            _diagnostic(
                target,
                code=ValidationDiagnosticCode.REPORTED_ONLY,
                severity=ValidationDiagnosticSeverity.WARNING,
                message="reported-only test result is observation, not validation proof",
                evidence_ids=(result.test_result_id,),
            )
        )
    if result.exit_code is None:
        diagnostics.append(
            _diagnostic(
                target,
                code=ValidationDiagnosticCode.MISSING_EXIT_CODE,
                severity=ValidationDiagnosticSeverity.WARNING,
                message="test result has no observed exit code",
                evidence_ids=(result.test_result_id,),
            )
        )
    contradictory = (
        result.status is TestExecutionStatus.PASSED
        and result.exit_code is not None
        and result.exit_code != 0
    ) or (
        result.status in {TestExecutionStatus.FAILED, TestExecutionStatus.ERROR}
        and result.exit_code == 0
    )
    if contradictory:
        diagnostics.append(
            _diagnostic(
                target,
                code=ValidationDiagnosticCode.STATUS_EXIT_CONTRADICTION,
                severity=ValidationDiagnosticSeverity.ERROR,
                message="test status contradicts its observed exit code; success is forbidden",
                evidence_ids=(result.test_result_id,),
            )
        )
    if result.reported_status is not None and result.reported_status is not result.status:
        diagnostics.append(
            _diagnostic(
                target,
                code=ValidationDiagnosticCode.REPORTED_OBSERVED_CONTRADICTION,
                severity=ValidationDiagnosticSeverity.WARNING,
                message="reported status contradicts parsed test status",
                evidence_ids=(result.test_result_id,),
            )
        )
    return tuple(diagnostics)


def _binding(
    *,
    target: TestTargetVersion,
    result: TestResultV2,
    symbol: TestSymbolVersion,
    evidence_id: str,
    test_case_id: str | None,
    signal: TestSymbolSignal,
    confidence: float,
    evidence_locator: str,
) -> TestSymbolBinding:
    confirmed = signal in {
        TestSymbolSignal.COVERAGE_FUNCTION,
        TestSymbolSignal.COVERAGE_LINE,
        TestSymbolSignal.EXACT_SELECTOR,
        TestSymbolSignal.EXACT_TARGET,
    }
    relation = (
        CodeRelationType.COVERS
        if signal
        in {
            TestSymbolSignal.COVERAGE_FUNCTION,
            TestSymbolSignal.COVERAGE_LINE,
        }
        else CodeRelationType.TESTS
    )
    payload = {
        "evidence_id": evidence_id,
        "relation_type": relation.value,
        "signal": signal.value,
        "symbol_version_id": symbol.symbol_version_id,
        "test_case_id": test_case_id,
        "test_result_id": result.test_result_id,
    }
    return TestSymbolBinding(
        binding_id="test-symbol-binding://sha256/" + _sha256(payload).removeprefix("sha256:"),
        target=target,
        test_result_id=result.test_result_id,
        test_case_id=test_case_id,
        symbol=symbol,
        evidence_id=evidence_id,
        signal=signal,
        rank=_SIGNAL_RANK[signal],
        status=(
            TestSymbolBindingStatus.CONFIRMED if confirmed else TestSymbolBindingStatus.CANDIDATE
        ),
        relation_type=relation,
        confidence=confidence,
        review_required=not confirmed,
        evidence_locator=evidence_locator,
    )


def _dedupe_sorted(
    values: Iterable[Any],
    *,
    identity: str,
    sort_key: Any,
) -> tuple[Any, ...]:
    by_id: dict[str, Any] = {}
    for value in values:
        item_id = getattr(value, identity)
        previous = by_id.get(item_id)
        if previous is not None and previous != value:
            raise TestValidationScopeError(f"duplicate {identity} has conflicting frozen evidence")
        by_id[item_id] = value
    return tuple(sorted(by_id.values(), key=sort_key))


class TestValidationBinder:
    """Treat one TestResult against one exact target and rank symbol evidence."""

    binder_version = TEST_VALIDATION_BINDER_VERSION

    def bind(
        self,
        result: TestResultV2 | None,
        *,
        current_target: TestTargetVersion,
        coverage_evidence: Iterable[CoverageEvidence] = (),
        symbol_evidence: Iterable[TestSymbolEvidence] = (),
    ) -> ValidationTreatment:
        if not isinstance(current_target, TestTargetVersion):
            raise TypeError("current_target must be a TestTargetVersion")
        if result is not None and not isinstance(result, TestResultV2):
            raise TypeError("result must be a TestResultV2 or None")
        coverage_inputs = tuple(coverage_evidence)
        symbol_inputs = tuple(symbol_evidence)
        if any(not isinstance(item, CoverageEvidence) for item in coverage_inputs):
            raise TypeError("coverage_evidence must contain CoverageEvidence")
        if any(not isinstance(item, TestSymbolEvidence) for item in symbol_inputs):
            raise TypeError("symbol_evidence must contain TestSymbolEvidence")
        coverage_values = _dedupe_sorted(
            coverage_inputs,
            identity="coverage_id",
            sort_key=lambda item: item.coverage_id,
        )
        symbol_values = _dedupe_sorted(
            symbol_inputs,
            identity="evidence_id",
            sort_key=lambda item: item.evidence_id,
        )
        if result is None:
            diagnostic = _diagnostic(
                current_target,
                code=ValidationDiagnosticCode.NO_TEST_RESULT,
                severity=ValidationDiagnosticSeverity.WARNING,
                message="no TestResult is available for this exact target",
            )
            return self._treatment(
                status=ValidationTreatmentStatus.MISSING_CONTEXT,
                current_target=current_target,
                result=None,
                coverage_values=coverage_values,
                symbol_values=symbol_values,
                bindings=(),
                edges=(),
                diagnostics=(diagnostic,),
            )

        mismatch = _target_mismatch(
            current_target,
            result.target,
            historical_allowed=True,
        )
        if mismatch is not None:
            diagnostic = _diagnostic(
                current_target,
                code=mismatch,
                severity=(
                    ValidationDiagnosticSeverity.INFO
                    if mismatch is ValidationDiagnosticCode.HISTORICAL_TEST_RESULT
                    else ValidationDiagnosticSeverity.ERROR
                ),
                message=_mismatch_message(mismatch),
                evidence_ids=(
                    (result.test_result_id,)
                    if mismatch is ValidationDiagnosticCode.HISTORICAL_TEST_RESULT
                    else ()
                ),
            )
            historical = mismatch is ValidationDiagnosticCode.HISTORICAL_TEST_RESULT
            return self._treatment(
                status=(
                    ValidationTreatmentStatus.HISTORICAL_VALIDATION
                    if historical
                    else ValidationTreatmentStatus.REJECTED
                ),
                current_target=current_target,
                result=result if historical else None,
                observed_result=result,
                coverage_values=coverage_values,
                symbol_values=symbol_values,
                bindings=(),
                edges=(),
                diagnostics=(diagnostic,),
            )

        diagnostics = list(_status_diagnostics(current_target, result))
        edges: list[TestValidationEdge] = [_containment_edge(current_target, result)]
        relation = _validation_relation(result)
        if relation is not None:
            role = (
                TestValidationEdgeRole.VALIDATION_SUCCESS
                if relation is CodeRelationType.VALIDATED_BY
                else TestValidationEdgeRole.VALIDATION_FAILURE
            )
            edges.append(
                _edge(
                    target=current_target,
                    relation_type=relation,
                    source_id=current_target.target_id,
                    source_type=current_target.target_type,
                    target_id=result.test_result_id,
                    target_type=CodeGraphEntityType.TEST_RESULT,
                    role=role,
                    evidence_id=result.test_result_id,
                    evidence_locator=result.evidence_locator,
                )
            )

        bindings: list[TestSymbolBinding] = []
        for coverage in coverage_values:
            evidence_mismatch = _target_mismatch(
                current_target,
                coverage.target,
                historical_allowed=False,
            )
            if evidence_mismatch is not None:
                diagnostics.append(
                    _diagnostic(
                        current_target,
                        code=evidence_mismatch,
                        severity=ValidationDiagnosticSeverity.ERROR,
                        message=_mismatch_message(evidence_mismatch),
                        evidence_ids=(coverage.coverage_id,),
                    )
                )
                continue
            if coverage.test_result_id != result.test_result_id:
                diagnostics.append(
                    _diagnostic(
                        current_target,
                        code=ValidationDiagnosticCode.TEST_RESULT_ID_MISMATCH,
                        severity=ValidationDiagnosticSeverity.ERROR,
                        message="coverage belongs to a different TestResult",
                        evidence_ids=(coverage.coverage_id,),
                    )
                )
                continue
            if (
                result.coverage_artifact_ref is not None
                and coverage.artifact_ref != result.coverage_artifact_ref
            ):
                diagnostics.append(
                    _diagnostic(
                        current_target,
                        code=ValidationDiagnosticCode.COVERAGE_ARTIFACT_MISMATCH,
                        severity=ValidationDiagnosticSeverity.ERROR,
                        message="coverage artifact conflicts with TestResult provenance",
                        evidence_ids=(coverage.coverage_id,),
                    )
                )
                continue
            binding = _binding(
                target=current_target,
                result=result,
                symbol=coverage.symbol,
                evidence_id=coverage.coverage_id,
                test_case_id=None,
                signal=coverage.signal,
                confidence=1.0,
                evidence_locator=coverage.evidence_locator,
            )
            bindings.append(binding)
            edges.append(
                _edge(
                    target=current_target,
                    relation_type=CodeRelationType.COVERS,
                    source_id=coverage.coverage_id,
                    source_type=CodeGraphEntityType.COVERAGE,
                    target_id=coverage.symbol.symbol_version_id,
                    target_type=coverage.symbol.entity_type,
                    role=TestValidationEdgeRole.COVERAGE_OBSERVATION,
                    evidence_id=coverage.coverage_id,
                    evidence_locator=coverage.evidence_locator,
                )
            )

        selectors = {value for value in (result.selector, *result.test_cases) if value is not None}
        for evidence in symbol_values:
            evidence_mismatch = _target_mismatch(
                current_target,
                evidence.target,
                historical_allowed=False,
            )
            if evidence_mismatch is not None:
                diagnostics.append(
                    _diagnostic(
                        current_target,
                        code=evidence_mismatch,
                        severity=ValidationDiagnosticSeverity.ERROR,
                        message=_mismatch_message(evidence_mismatch),
                        evidence_ids=(evidence.evidence_id,),
                    )
                )
                continue
            if evidence.test_result_id != result.test_result_id:
                diagnostics.append(
                    _diagnostic(
                        current_target,
                        code=ValidationDiagnosticCode.TEST_RESULT_ID_MISMATCH,
                        severity=ValidationDiagnosticSeverity.ERROR,
                        message="symbol evidence belongs to a different TestResult",
                        evidence_ids=(evidence.evidence_id,),
                    )
                )
                continue
            if (
                evidence.signal is TestSymbolSignal.EXACT_SELECTOR
                and evidence.selector not in selectors
            ):
                diagnostics.append(
                    _diagnostic(
                        current_target,
                        code=ValidationDiagnosticCode.SELECTOR_MISMATCH,
                        severity=ValidationDiagnosticSeverity.ERROR,
                        message="exact selector evidence is absent from TestResult selectors",
                        evidence_ids=(evidence.evidence_id,),
                    )
                )
                continue
            binding = _binding(
                target=current_target,
                result=result,
                symbol=evidence.symbol,
                evidence_id=evidence.evidence_id,
                test_case_id=evidence.test_case_id,
                signal=evidence.signal,
                confidence=evidence.confidence,
                evidence_locator=evidence.evidence_locator,
            )
            bindings.append(binding)
            if binding.status is TestSymbolBindingStatus.CONFIRMED:
                edges.append(
                    _edge(
                        target=current_target,
                        relation_type=CodeRelationType.TESTS,
                        source_id=evidence.test_case_id,
                        source_type=CodeGraphEntityType.TEST_CASE,
                        target_id=evidence.symbol.symbol_version_id,
                        target_type=evidence.symbol.entity_type,
                        role=TestValidationEdgeRole.EXACT_TEST_TARGET,
                        evidence_id=evidence.evidence_id,
                        evidence_locator=evidence.evidence_locator,
                        confidence=evidence.confidence,
                    )
                )

        binding_values = _dedupe_sorted(
            bindings,
            identity="binding_id",
            sort_key=lambda item: (
                item.rank,
                0 if item.status is TestSymbolBindingStatus.CONFIRMED else 1,
                -item.confidence,
                item.symbol.symbol_version_id,
                item.binding_id,
            ),
        )
        edge_values = _dedupe_sorted(
            edges,
            identity="edge_id",
            sort_key=lambda item: (
                item.relation_type.value,
                item.source_id,
                item.target_id,
                item.edge_id,
            ),
        )
        diagnostic_values = _dedupe_sorted(
            diagnostics,
            identity="diagnostic_id",
            sort_key=lambda item: (item.code.value, item.diagnostic_id),
        )
        status = {
            CodeRelationType.VALIDATED_BY: ValidationTreatmentStatus.VALIDATED,
            CodeRelationType.FAILED_VALIDATION: (ValidationTreatmentStatus.FAILED_VALIDATION),
            None: ValidationTreatmentStatus.OBSERVATION_ONLY,
        }[relation]
        return self._treatment(
            status=status,
            current_target=current_target,
            result=result,
            coverage_values=coverage_values,
            symbol_values=symbol_values,
            bindings=binding_values,
            edges=edge_values,
            diagnostics=diagnostic_values,
        )

    def treat(
        self,
        result: TestResultV2 | None,
        *,
        current_target: TestTargetVersion,
        coverage_evidence: Iterable[CoverageEvidence] = (),
        symbol_evidence: Iterable[TestSymbolEvidence] = (),
    ) -> ValidationTreatment:
        """Compatibility spelling for the treatment-oriented contract."""

        return self.bind(
            result,
            current_target=current_target,
            coverage_evidence=coverage_evidence,
            symbol_evidence=symbol_evidence,
        )

    def _treatment(
        self,
        *,
        status: ValidationTreatmentStatus,
        current_target: TestTargetVersion,
        result: TestResultV2 | None,
        observed_result: TestResultV2 | None = None,
        coverage_values: tuple[CoverageEvidence, ...],
        symbol_values: tuple[TestSymbolEvidence, ...],
        bindings: tuple[TestSymbolBinding, ...],
        edges: tuple[TestValidationEdge, ...],
        diagnostics: tuple[ValidationDiagnostic, ...],
    ) -> ValidationTreatment:
        input_result = observed_result if observed_result is not None else result
        canonical_input_sha256 = _sha256(
            {
                "binder_version": self.binder_version,
                "coverage": [asdict(item) for item in coverage_values],
                "current_target": asdict(current_target),
                "result": asdict(input_result) if input_result is not None else None,
                "symbol_evidence": [asdict(item) for item in symbol_values],
            }
        )
        trace = TestValidationTrace(
            target=current_target,
            status=status,
            test_results_seen=1 if input_result is not None else 0,
            coverage_evidence_seen=len(coverage_values),
            symbol_evidence_seen=len(symbol_values),
            confirmed_bindings=sum(
                item.status is TestSymbolBindingStatus.CONFIRMED for item in bindings
            ),
            candidate_bindings=sum(
                item.status is TestSymbolBindingStatus.CANDIDATE for item in bindings
            ),
            registered_edges=len(edges),
            diagnostics=len(diagnostics),
            canonical_input_sha256=canonical_input_sha256,
        )
        return ValidationTreatment(
            status=status,
            target=current_target,
            test_result=result,
            bindings=bindings,
            edges=edges,
            diagnostics=diagnostics,
            trace=trace,
        )


TestValidationMapper = TestValidationBinder


@runtime_checkable
class CodeTestValidationHookStore(Protocol):
    """Production read surface for exact current-generation test expansion."""

    def connection(self) -> Any: ...

    def load_code_unit(
        self,
        unit_id: str,
        **scope: Any,
    ) -> dict[str, Any] | None: ...


class CodeTestValidationExpansionHookV2:
    """Treat explicit test-to-unit metadata before exposing the TEST channel."""

    hook_version = "c6-production-test-validation-hook-v1"

    def __init__(
        self,
        store: CodeTestValidationHookStore,
        *,
        binder: TestValidationBinder | None = None,
    ) -> None:
        if not isinstance(store, CodeTestValidationHookStore):
            raise TypeError("store must implement CodeTestValidationHookStore")
        self.store = store
        self.binder = binder or TestValidationBinder()
        self._lkg: dict[
            tuple[Any, ...],
            tuple[tuple[str, str, CodeFactStatus], ...],
        ] = {}
        self._lock = threading.RLock()

    def expand(
        self,
        *,
        request: EvidenceSearchRequest,
        profile: Any,
        seeds: tuple[CodeRetrievalCandidate, ...],
        limit: int,
        deadline: float | None,
    ) -> CodeOptionalHookResult:
        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("test hook limit must be a positive integer")
        if deadline is not None and time.monotonic() >= deadline:
            return self._result(
                CodeOptionalHookStatus.TIMEOUT,
                reason="test hook deadline reached before exact scope resolution",
            )
        try:
            scope = self._scope(request, profile, seeds)
            seed_rows = self._seed_rows(scope, seeds)
        except (TestValidationError, TypeError, ValueError) as error:
            return self._result(
                CodeOptionalHookStatus.UNAVAILABLE,
                reason=f"test-validation scope unavailable: {type(error).__name__}",
            )

        key = (
            scope["project_id"],
            scope["repository_id"],
            scope["commit_sha"],
            scope["generation_id"],
            scope["acl_ref"],
            request.query,
            tuple(sorted((seed.entity_id, seed.retrieval_unit_id) for seed in seeds)),
        )
        try:
            selected = self._select(
                scope,
                seed_rows,
                limit=limit,
                deadline=deadline,
            )
        except TimeoutError:
            return self._result(
                CodeOptionalHookStatus.TIMEOUT,
                reason="test-validation treatment exceeded the governed deadline",
            )
        except Exception as error:
            del error
            with self._lock:
                cached = self._lkg.get(key)
            if cached is None:
                return self._result(
                    CodeOptionalHookStatus.UNAVAILABLE,
                    reason="test-validation expansion failed without an exact same-scope LKG",
                )
            by_identity = {
                (candidate.entity_id, candidate.retrieval_unit_id): candidate
                for candidate, _ in seed_rows
            }
            selected = tuple(
                (by_identity[(entity_id, unit_id)], fact_status)
                for entity_id, unit_id, fact_status in cached
                if (entity_id, unit_id) in by_identity
            )
            return self._from_candidates(
                selected,
                reason="exact same-scope test-validation LKG reused after source failure",
            )

        identities = tuple(
            sorted(
                (
                    candidate.entity_id,
                    candidate.retrieval_unit_id,
                    fact_status,
                )
                for candidate, fact_status in selected
            )
        )
        with self._lock:
            self._lkg[key] = identities
        return self._from_candidates(selected)

    def _scope(
        self,
        request: EvidenceSearchRequest,
        profile: Any,
        seeds: tuple[CodeRetrievalCandidate, ...],
    ) -> dict[str, str]:
        repositories = tuple(request.scope.repository_ids)
        commit = request.scope.commit
        project_id = request.scope.project_id
        target_ref = str(getattr(profile, "target_ref", "") or "")
        if (
            len(repositories) != 1
            or not project_id
            or commit is None
            or _FULL_SHA.fullmatch(commit) is None
            or target_ref != commit
            or request.scope.enforce_acl is not True
        ):
            raise TestValidationScopeError(
                "test validation requires one governed exact commit scope"
            )
        repository_id = repositories[0]
        with self.store.connection() as database:
            repository = database.execute(
                """
                SELECT project_id, id, active_generation_id, head_commit, acl_ref, status
                FROM repositories
                WHERE id=? AND project_id=?
                """,
                (repository_id, project_id),
            ).fetchone()
            if repository is None:
                raise TestValidationScopeError("test repository is unavailable")
            generation_id = str(repository["active_generation_id"] or "")
            generation = database.execute(
                """
                SELECT commit_sha, status
                FROM index_generations
                WHERE id=? AND repository_id=?
                """,
                (generation_id, repository_id),
            ).fetchone()
        acl_ref = str(repository["acl_ref"] or "")
        if (
            str(repository["head_commit"]) != commit
            or str(repository["status"]) != "ready"
            or generation is None
            or str(generation["commit_sha"]) != commit
            or str(generation["status"]) != "published"
            or not acl_ref
            or acl_ref not in set(request.scope.allowed_acl_refs)
        ):
            raise TestValidationScopeError("test repository/generation/ACL watermark mismatch")
        if any(
            (
                seed.repository_id,
                seed.stable_version,
                seed.source_generation,
                seed.acl_ref,
            )
            != (repository_id, commit, generation_id, acl_ref)
            for seed in seeds
        ):
            raise TestValidationScopeError("test seeds conflict with the governed watermark")
        return {
            "project_id": project_id,
            "repository_id": repository_id,
            "commit_sha": commit,
            "generation_id": generation_id,
            "acl_ref": acl_ref,
        }

    def _seed_rows(
        self,
        scope: Mapping[str, str],
        seeds: tuple[CodeRetrievalCandidate, ...],
    ) -> tuple[tuple[CodeRetrievalCandidate, dict[str, Any]], ...]:
        values = []
        for seed in seeds:
            row = self.store.load_code_unit(
                seed.retrieval_unit_id,
                project_id=scope["project_id"],
                repository_id=scope["repository_id"],
                generation_id=scope["generation_id"],
                allowed_acl_refs=(scope["acl_ref"],),
                active_only=True,
            )
            if row is None:
                raise TestValidationScopeError("test seed retrieval unit is unavailable")
            actual = (
                str(row.get("entity_id") or ""),
                str(row.get("repository_id") or ""),
                str(row.get("generation_id") or ""),
                str(row.get("acl_ref") or ""),
            )
            expected = (
                seed.entity_id,
                seed.repository_id,
                seed.source_generation,
                seed.acl_ref,
            )
            if actual != expected:
                raise TestValidationScopeError("test seed retrieval unit provenance mismatch")
            values.append((seed, row))
        return tuple(values)

    def _select(
        self,
        scope: Mapping[str, str],
        seed_rows: tuple[tuple[CodeRetrievalCandidate, dict[str, Any]], ...],
        *,
        limit: int,
        deadline: float | None,
    ) -> tuple[tuple[CodeRetrievalCandidate, CodeFactStatus], ...]:
        target = TestTargetVersion.commit(
            project_id=scope["project_id"],
            repository_id=scope["repository_id"],
            commit_sha=scope["commit_sha"],
            generation_id=scope["generation_id"],
            acl_ref=scope["acl_ref"],
        )
        with self.store.connection() as database:
            rows = database.execute(
                """
                SELECT *
                FROM test_results
                WHERE project_id=? AND repository_id=? AND commit_sha=? AND acl_ref=?
                ORDER BY observed_at DESC, id
                LIMIT ?
                """,
                (
                    scope["project_id"],
                    scope["repository_id"],
                    scope["commit_sha"],
                    scope["acl_ref"],
                    min(64, max(limit * 4, limit)),
                ),
            ).fetchall()

        selected: dict[
            tuple[str, str],
            tuple[CodeRetrievalCandidate, CodeFactStatus],
        ] = {}
        for row in rows:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("test-validation treatment deadline exceeded")
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            if not isinstance(metadata, dict):
                continue
            if metadata.get("generation_id") != scope["generation_id"]:
                continue
            matched = tuple(
                (candidate, unit)
                for candidate, unit in seed_rows
                if _test_metadata_matches(metadata, candidate, unit)
            )
            if not matched:
                continue
            result = _legacy_test_result(row, metadata, target)
            if result is None:
                continue
            for candidate, unit in matched:
                symbol = _test_symbol_version(target, candidate, unit)
                evidence_locator = str(metadata.get("evidence_locator") or result.evidence_locator)
                evidence = TestSymbolEvidence(
                    evidence_id=(
                        "test-symbol-evidence://sha256/"
                        + hashlib.sha256(
                            "\x1f".join(
                                (
                                    result.test_result_id,
                                    symbol.symbol_version_id,
                                    evidence_locator,
                                )
                            ).encode()
                        ).hexdigest()
                    ),
                    test_result_id=result.test_result_id,
                    target=target,
                    test_case_id=str(
                        metadata.get("test_case_id")
                        or f"test-case://{result.test_result_id.rsplit('/', 1)[-1]}"
                    ),
                    symbol=symbol,
                    signal=TestSymbolSignal.EXACT_TARGET,
                    confidence=1.0,
                    parser_version="test-result-metadata-v1",
                    evidence_locator=evidence_locator,
                    declared_target_symbol_version_id=symbol.symbol_version_id,
                )
                treatment = self.binder.bind(
                    result,
                    current_target=target,
                    symbol_evidence=(evidence,),
                )
                if not any(
                    binding.status is TestSymbolBindingStatus.CONFIRMED
                    for binding in treatment.bindings
                ):
                    continue
                if treatment.status is ValidationTreatmentStatus.VALIDATED:
                    fact_status = CodeFactStatus.MACHINE_CONFIRMED
                elif treatment.status is ValidationTreatmentStatus.FAILED_VALIDATION:
                    fact_status = CodeFactStatus.FAILED_VALIDATION
                else:
                    continue
                identity = (candidate.entity_id, candidate.retrieval_unit_id)
                previous = selected.get(identity)
                if previous is None or fact_status is CodeFactStatus.FAILED_VALIDATION:
                    selected[identity] = (candidate, fact_status)
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break
        return tuple(selected[key] for key in sorted(selected))

    def _from_candidates(
        self,
        candidates: Sequence[tuple[CodeRetrievalCandidate, CodeFactStatus]],
        *,
        reason: str = "",
    ) -> CodeOptionalHookResult:
        expanded = tuple(
            _test_hook_candidate(candidate, fact_status, rank)
            for rank, (candidate, fact_status) in enumerate(candidates)
        )
        return self._result(
            (
                CodeOptionalHookStatus.COMPLETE
                if expanded
                else CodeOptionalHookStatus.COMPLETE_NO_MATCH
            ),
            candidates=expanded,
            reason=reason,
        )

    def _result(
        self,
        status: CodeOptionalHookStatus,
        *,
        candidates: tuple[CodeRetrievalCandidate, ...] = (),
        reason: str = "",
    ) -> CodeOptionalHookResult:
        return CodeOptionalHookResult(
            channel=CodeRetrievalChannel.TEST,
            status=status,
            candidates=candidates,
            hook_version=self.hook_version,
            reason=reason,
        )


def _test_metadata_values(metadata: Mapping[str, Any], key: str) -> frozenset[str]:
    values = metadata.get(key, ())
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        return frozenset()
    return frozenset(
        value for value in values if isinstance(value, str) and value and value == value.strip()
    )


def _test_metadata_matches(
    metadata: Mapping[str, Any],
    candidate: CodeRetrievalCandidate,
    unit: Mapping[str, Any],
) -> bool:
    unit_ids = _test_metadata_values(metadata, "target_retrieval_unit_ids")
    entity_ids = _test_metadata_values(metadata, "target_entity_ids")
    paths = _test_metadata_values(metadata, "target_paths")
    symbols = _test_metadata_values(metadata, "target_symbols")
    if not any((unit_ids, entity_ids, paths, symbols)):
        return False
    qualified_name = str(unit.get("qualified_name") or "")
    symbol_name = qualified_name.rsplit(".", 1)[-1] if qualified_name else ""
    return (
        candidate.retrieval_unit_id in unit_ids
        or candidate.entity_id in entity_ids
        or str(unit.get("path") or "") in paths
        or qualified_name in symbols
        or symbol_name in symbols
    )


def _legacy_test_status(value: object) -> TestExecutionStatus:
    return {
        "passed": TestExecutionStatus.PASSED,
        "failed": TestExecutionStatus.FAILED,
        "error": TestExecutionStatus.ERROR,
    }.get(str(value).casefold(), TestExecutionStatus.UNKNOWN)


def _legacy_test_result(
    row: Any,
    metadata: Mapping[str, Any],
    target: TestTargetVersion,
) -> TestResultV2 | None:
    command = str(row["command"] or "")
    if not command or command != command.strip():
        return None
    observation = (
        TestObservation.OBSERVED
        if metadata.get("observation") == TestObservation.OBSERVED.value
        else TestObservation.REPORTED
    )
    raw_duration = row["duration_ms"]
    duration_ms = (
        int(raw_duration)
        if isinstance(raw_duration, (int, float))
        and not isinstance(raw_duration, bool)
        and float(raw_duration).is_integer()
        else None
    )
    reported_status = metadata.get("reported_status")
    return TestResultV2.create(
        target=target,
        command=(command,),
        environment_ref=str(metadata.get("environment_ref") or "environment:unspecified"),
        status=_legacy_test_status(row["status"]),
        observation=observation,
        framework=str(row["framework"] or "unknown"),
        framework_parser_version=str(
            metadata.get("framework_parser_version") or "legacy-test-result-v1"
        ),
        evidence_locator=str(metadata.get("evidence_locator") or row["id"]),
        selector=(
            str(metadata["selector"])
            if isinstance(metadata.get("selector"), str) and metadata["selector"].strip()
            else None
        ),
        exit_code=row["exit_code"],
        duration_ms=duration_ms,
        reported_status=(
            _legacy_test_status(reported_status) if reported_status is not None else None
        ),
        observed_at=str(row["observed_at"]),
        source=str(metadata.get("source") or "code-history-v1"),
        test_result_id=str(row["id"]),
    )


def _test_symbol_version(
    target: TestTargetVersion,
    candidate: CodeRetrievalCandidate,
    unit: Mapping[str, Any],
) -> TestSymbolVersion:
    unit_type = str(unit.get("unit_type") or "")
    qualified_name = str(unit.get("qualified_name") or "")
    is_symbol = unit_type == "symbol.ast_block" and bool(qualified_name)
    symbol_version_id = (
        "test-symbol-version://sha256/"
        + hashlib.sha256(
            "\x1f".join(
                (
                    candidate.entity_id,
                    candidate.retrieval_unit_id,
                    target.stable_version,
                    target.generation_id,
                )
            ).encode()
        ).hexdigest()
    )
    return TestSymbolVersion(
        target=target,
        symbol_version_id=symbol_version_id,
        entity_id=candidate.entity_id,
        entity_type=(
            CodeGraphEntityType.CODE_SYMBOL if is_symbol else CodeGraphEntityType.FILE_VERSION
        ),
        path=str(unit.get("path") or ""),
        evidence_locator=candidate.locator,
        symbol_name=qualified_name.rsplit(".", 1)[-1] if is_symbol else "",
        qualified_name=qualified_name if is_symbol else "",
        start_line=int(unit.get("start_line") or 1),
        end_line=int(unit.get("end_line") or unit.get("start_line") or 1),
    )


def _test_hook_candidate(
    candidate: CodeRetrievalCandidate,
    fact_status: CodeFactStatus,
    rank: int,
) -> CodeRetrievalCandidate:
    payload = candidate.model_dump(mode="python", round_trip=True)
    scores = [
        item
        for item in candidate.raw_channel_scores
        if item.channel is not CodeRetrievalChannel.TEST
    ]
    ranks = [
        item
        for item in candidate.raw_channel_ranks
        if item.channel is not CodeRetrievalChannel.TEST
    ]
    scores.append(
        CodeChannelScore(
            channel=CodeRetrievalChannel.TEST,
            score=1.0 / (rank + 1),
        )
    )
    ranks.append(CodeChannelRank(channel=CodeRetrievalChannel.TEST, rank=rank))
    payload.update(
        raw_channel_scores=tuple(scores),
        raw_channel_ranks=tuple(ranks),
        within_source_rank=rank,
        source_fused_score=max(candidate.source_fused_score, 1.0 / (rank + 1)),
        role=CodeCandidateRole.TEST,
        fact_status=fact_status,
        derivation=CodeDerivation.RULE,
    )
    return CodeRetrievalCandidate.model_validate(payload)


__all__ = [
    "TEST_VALIDATION_BINDER_VERSION",
    "TEST_VALIDATION_CONTRACT_VERSION",
    "CoverageEvidence",
    "CodeTestValidationExpansionHookV2",
    "CodeTestValidationHookStore",
    "RawObjectRef",
    "TestExecutionStatus",
    "TestObservation",
    "TestResultV2",
    "TestStatusConsistency",
    "TestSymbolBinding",
    "TestSymbolBindingStatus",
    "TestSymbolEvidence",
    "TestSymbolSignal",
    "TestSymbolVersion",
    "TestTargetVersion",
    "TestValidationBinder",
    "TestValidationEdge",
    "TestValidationEdgeRole",
    "TestValidationError",
    "TestValidationMapper",
    "TestValidationScopeError",
    "TestValidationTrace",
    "ValidationDiagnostic",
    "ValidationDiagnosticCode",
    "ValidationDiagnosticSeverity",
    "ValidationTreatment",
    "ValidationTreatmentStatus",
]
