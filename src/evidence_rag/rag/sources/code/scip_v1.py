"""Bounded SCIP v1 consumer and opt-in external indexer execution.

The module intentionally has no persistence dependency.  It consumes an
``index.scip`` supplied by a user or CI system, canonicalizes its locations,
and asks an injected read-only resolver to link them to already-governed
``FileVersion`` and ``CodeSymbol`` entities.  It does not materialize graph
edges; semantic edge treatment belongs to the next implementation stage.

The protobuf decoder implements only the documented SCIP subset used here and
does not require the third-party protobuf runtime.  Top-level fields are read
from a stream and nested messages are individually bounded.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import re
import secrets
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal, Protocol
from urllib.parse import unquote

try:  # pragma: no cover - exercised only on POSIX external execution.
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]


SCIP_CONSUMER_VERSION = "c5-scip-consumer-v1"
_CONTAINER_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXECUTABLE_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_LOCAL_SYMBOL_PREFIX = "local "
_CONTAINER_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:[0-9a-f]{64}$")
_SAFE_CONTAINER_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_UNSAFE_EXECUTION_PATH = re.compile(r"[\s;&|`$><!*?\\]")
_CONTAINER_RUNTIMES = frozenset({"docker", "podman"})
_HARD_DENY_EXECUTABLES = frozenset(
    {
        "bash",
        "bun",
        "cmd",
        "cmd.exe",
        "deno",
        "fish",
        "node",
        "npm",
        "npx",
        "pip",
        "pip3",
        "pnpm",
        "powershell",
        "powershell.exe",
        "pwsh",
        "python",
        "python3",
        "sh",
        "uv",
        "yarn",
        "zsh",
    }
)


class ScipStatus(StrEnum):
    """Outcome of a consumer or runner operation."""

    COMPLETE = "complete"
    PARTIAL = "partial"


class ScipOccurrenceKind(StrEnum):
    """Consumer-level occurrence classification; this is not a graph edge."""

    DEFINITION = "definition"
    REFERENCE = "reference"
    EXTERNAL = "external"


class ScipLinkStatus(StrEnum):
    """Result of linking one SCIP object to a governed entity."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    EXTERNAL = "external"
    LOCAL = "local"
    SCOPE_MISMATCH = "scope_mismatch"
    RESOLVER_UNAVAILABLE = "resolver_unavailable"


class ScipNetworkIsolation(StrEnum):
    """How an allowlisted command is prevented from reaching the network."""

    CONTAINER_NONE = "container_none"
    HOST_SANDBOX = "host_sandbox"


@dataclass(frozen=True, slots=True)
class ScipDecodeLimits:
    """Independent fail-closed protobuf and cardinality limits."""

    max_file_bytes: int = 64 * 1024 * 1024
    max_message_bytes: int = 8 * 1024 * 1024
    max_depth: int = 12
    max_fields_per_message: int = 250_000
    max_total_fields: int = 2_000_000
    max_documents: int = 100_000
    max_symbols: int = 1_000_000
    max_occurrences: int = 5_000_000
    max_string_bytes: int = 1_000_000
    max_range_value: int = 50_000_000
    max_diagnostics: int = 10_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class ScipScope:
    """Exact project, repository, generation, and access-control scope."""

    project_id: str
    repository_id: str
    generation_id: str
    acl_ref: str

    def __post_init__(self) -> None:
        for name in ("project_id", "repository_id", "generation_id", "acl_ref"):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True, order=True)
class ScipPosition:
    """One-based, half-open source position."""

    line: int
    character: int

    def __post_init__(self) -> None:
        if self.line < 1 or self.character < 1:
            raise ValueError("SCIP canonical positions must be one-based")


@dataclass(frozen=True, slots=True, order=True)
class ScipRange:
    """Canonical half-open location converted from SCIP's zero-based values."""

    start: ScipPosition
    end: ScipPosition

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("SCIP range end must not precede start")


@dataclass(frozen=True, slots=True)
class ScipRelationship:
    """Raw SymbolInformation relationship flags."""

    symbol: str
    is_reference: bool = False
    is_implementation: bool = False
    is_type_definition: bool = False
    is_definition: bool = False


@dataclass(frozen=True, slots=True)
class ScipSymbolInformation:
    """SCIP symbol metadata retained without promoting local symbols."""

    symbol: str
    relationships: tuple[ScipRelationship, ...] = ()
    kind: int = 0
    display_name: str = ""
    enclosing_symbol: str = ""
    is_external: bool = False
    is_local: bool = False


@dataclass(frozen=True, slots=True)
class ScipOccurrence:
    """One normalized and deterministically deduplicated occurrence."""

    relative_path: str
    source_range: ScipRange
    symbol: str
    symbol_roles: int
    kind: ScipOccurrenceKind
    is_local: bool


@dataclass(frozen=True, slots=True)
class ScipDocument:
    """One canonical repository-relative SCIP document."""

    relative_path: str
    language: str
    position_encoding: int
    occurrences: tuple[ScipOccurrence, ...]
    symbols: tuple[ScipSymbolInformation, ...]


@dataclass(frozen=True, slots=True)
class ScipMetadata:
    """Protocol and indexer identity decoded from Index.metadata."""

    protocol_version: int = 0
    tool_name: str = ""
    tool_version: str = ""
    tool_arguments: tuple[str, ...] = ()
    project_root: str = ""
    text_document_encoding: int = 0


@dataclass(frozen=True, slots=True)
class ScipDecodedIndex:
    """Raw bounded decoder output before repository-aware canonicalization."""

    metadata: ScipMetadata
    metadata_present: bool
    documents: tuple[_DecodedDocument, ...]
    external_symbols: tuple[ScipSymbolInformation, ...]
    content_sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class ScipRawObject:
    """RawObject-compatible lineage for the consumed SCIP artifact."""

    raw_object_id: str
    content_sha256: str
    byte_size: int
    source_name: str


@dataclass(frozen=True, slots=True)
class ScipProvenance:
    """Immutable consumer provenance, separate from relevance or edge truth."""

    derivation: Literal["scip"]
    consumer_version: str
    protocol_version: int
    indexer_name: str
    indexer_version: str
    raw_object: ScipRawObject


@dataclass(frozen=True, slots=True)
class ScipEntityRef:
    """Read-only resolver response with complete governed scope."""

    entity_id: str
    entity_type: Literal["FileVersion", "CodeSymbol"]
    project_id: str
    repository_id: str
    generation_id: str
    acl_ref: str
    relative_path: str
    scip_symbol: str = ""

    def __post_init__(self) -> None:
        if self.entity_type not in {"FileVersion", "CodeSymbol"}:
            raise ValueError("entity_type must be FileVersion or CodeSymbol")
        for name in (
            "entity_id",
            "project_id",
            "repository_id",
            "generation_id",
            "acl_ref",
            "relative_path",
        ):
            _require_text(getattr(self, name), name)


class ScipReadOnlyResolver(Protocol):
    """Injected non-mutating FileVersion/CodeSymbol lookup boundary."""

    def resolve_file(
        self,
        *,
        scope: ScipScope,
        relative_path: str,
    ) -> Sequence[ScipEntityRef]: ...

    def resolve_symbol(
        self,
        *,
        scope: ScipScope,
        symbol: str,
        relative_path: str | None,
    ) -> Sequence[ScipEntityRef]: ...


class ScipIngestionResolver:
    """Read-only adapter from one in-flight governed entity set to SCIP links.

    The adapter never guesses across paths or scopes.  A SCIP descriptor may
    resolve only when its canonical document path and decoded descriptor names
    identify exactly one already-built FileVersion/CodeSymbol entity.
    """

    resolver_version = "c5-scip-ingestion-resolver-v1"

    def __init__(self, entities: Iterable[object], *, scope: ScipScope) -> None:
        if not isinstance(scope, ScipScope):
            raise TypeError("scope must be a ScipScope")
        self.scope = scope
        files: dict[str, list[object]] = {}
        symbols: dict[str, list[object]] = {}
        for entity in tuple(entities):
            entity_type = str(getattr(entity, "entity_type", ""))
            if entity_type not in {"FileVersion", "CodeSymbol"}:
                continue
            actual_scope = (
                str(getattr(entity, "project_id", "")),
                str(getattr(entity, "repository_id", "")),
                str(getattr(entity, "generation_id", "")),
                str(getattr(entity, "acl_ref", "")),
            )
            expected_scope = (
                scope.project_id,
                scope.repository_id,
                scope.generation_id,
                scope.acl_ref,
            )
            if actual_scope != expected_scope:
                raise ScipResolverFailure("ingestion entity violated SCIP scope")
            path = str(getattr(entity, "path", "") or "")
            if not path:
                raise ScipResolverFailure("ingestion entity is missing its repository path")
            target = files if entity_type == "FileVersion" else symbols
            target.setdefault(path, []).append(entity)
        self._files = {
            path: tuple(sorted(values, key=lambda item: str(getattr(item, "id", ""))))
            for path, values in files.items()
        }
        self._symbols = {
            path: tuple(sorted(values, key=lambda item: str(getattr(item, "id", ""))))
            for path, values in symbols.items()
        }

    def resolve_file(
        self,
        *,
        scope: ScipScope,
        relative_path: str,
    ) -> Sequence[ScipEntityRef]:
        self._require_scope(scope)
        return tuple(
            self._ref(entity, relative_path=relative_path)
            for entity in self._files.get(relative_path, ())
        )

    def resolve_symbol(
        self,
        *,
        scope: ScipScope,
        symbol: str,
        relative_path: str | None,
    ) -> Sequence[ScipEntityRef]:
        self._require_scope(scope)
        if relative_path is None:
            return ()
        definition_path = _scip_descriptor_path(symbol, self._symbols)
        if definition_path is None:
            return ()
        descriptor_names = _scip_descriptor_names(symbol, definition_path)
        if not descriptor_names:
            return ()
        matches = []
        for entity in self._symbols.get(definition_path, ()):
            name = str(getattr(entity, "name", "") or "")
            qualified_name = str(getattr(entity, "qualified_name", "") or "")
            qualified_leaf = qualified_name.rsplit(".", 1)[-1] if qualified_name else ""
            if name in descriptor_names or qualified_leaf in descriptor_names:
                matches.append(entity)
        return tuple(
            self._ref(entity, relative_path=definition_path, scip_symbol=symbol)
            for entity in matches
        )

    def _require_scope(self, scope: ScipScope) -> None:
        if scope != self.scope:
            raise ScipResolverFailure("SCIP resolver call escaped its ingestion scope")

    def _ref(
        self,
        entity: object,
        *,
        relative_path: str,
        scip_symbol: str = "",
    ) -> ScipEntityRef:
        return ScipEntityRef(
            entity_id=str(getattr(entity, "id", "")),
            entity_type=str(getattr(entity, "entity_type", "")),  # type: ignore[arg-type]
            project_id=self.scope.project_id,
            repository_id=self.scope.repository_id,
            generation_id=self.scope.generation_id,
            acl_ref=self.scope.acl_ref,
            relative_path=relative_path,
            scip_symbol=scip_symbol,
        )


@dataclass(frozen=True, slots=True)
class ScipLink:
    """Deterministic resolution result; ambiguous candidates are never selected."""

    status: ScipLinkStatus
    entity: ScipEntityRef | None = None
    candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScipLinkedRelationship:
    """Relationship metadata plus governed source and target links."""

    source_symbol: str
    target_symbol: str
    relationship: ScipRelationship
    source_link: ScipLink
    target_link: ScipLink


@dataclass(frozen=True, slots=True)
class ScipLinkedDocument:
    """Canonical document and its FileVersion link."""

    document: ScipDocument
    file_link: ScipLink


@dataclass(frozen=True, slots=True)
class ScipLinkedOccurrence:
    """Canonical occurrence and its CodeSymbol target link."""

    occurrence: ScipOccurrence
    symbol_link: ScipLink


@dataclass(frozen=True, slots=True)
class ScipDiagnostic:
    """Bounded, non-secret diagnostic emitted by runner/consumer boundaries."""

    code: str
    message: str
    relative_path: str = ""
    symbol: str = ""
    candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScipFallbackTrace:
    """Explicit trace; the consumer delegates rather than running Tree-sitter."""

    attempted: bool
    engine: Literal["tree-sitter"]
    outcome: Literal["not_used", "delegated"]
    reason: str


@dataclass(frozen=True, slots=True)
class ScipConsumeResult:
    """Persistence-free output of one SCIP consumption attempt."""

    status: ScipStatus
    semantic_ready: Literal[False]
    documents: tuple[ScipLinkedDocument, ...]
    occurrences: tuple[ScipLinkedOccurrence, ...]
    external_symbols: tuple[ScipSymbolInformation, ...]
    relationships: tuple[ScipLinkedRelationship, ...]
    diagnostics: tuple[ScipDiagnostic, ...]
    provenance: ScipProvenance | None
    fallback: ScipFallbackTrace


class ScipDecodeError(ValueError):
    """Invalid, truncated, unsupported, or oversized SCIP protobuf."""


class ScipResolverFailure(RuntimeError):
    """Resolver backend or contract failure that invalidates the whole semantic result."""


class _RunnerRejection(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ScipCommandPin:
    """Exact allowlist entry for one externally managed SCIP indexer."""

    argv_template: tuple[str, ...]
    tool_name: str
    tool_version: str
    network_isolation: ScipNetworkIsolation
    executable_sha256: str = ""
    container_digest: str = ""

    def __post_init__(self) -> None:
        if (
            type(self.argv_template) is not tuple
            or not self.argv_template
            or any(type(value) is not str or not value for value in self.argv_template)
        ):
            raise ValueError("argv_template must be a non-empty argv list")
        for value in self.argv_template:
            if "\x00" in value or ("\n" in value) or ("\r" in value):
                raise ValueError("argv values must not contain NUL or line breaks")
        _require_text(self.tool_name, "tool_name")
        _require_text(self.tool_version, "tool_version")
        if self.tool_version.casefold() in {"latest", "main", "master", "head", "*"}:
            raise ValueError("tool_version must be an immutable pinned version")
        if not any(character.isdigit() for character in self.tool_version):
            raise ValueError("tool_version must contain a pinned version number")
        if not isinstance(self.network_isolation, ScipNetworkIsolation):
            raise TypeError("network_isolation must be ScipNetworkIsolation")
        if not _EXECUTABLE_DIGEST.fullmatch(self.executable_sha256):
            raise ValueError("executable_sha256 must be 64 lowercase hex characters")
        if not _CONTAINER_DIGEST.fullmatch(self.container_digest):
            raise ValueError("container_digest must be sha256:<64 lowercase hex>")
        if self.network_isolation is not ScipNetworkIsolation.CONTAINER_NONE:
            raise ValueError("SafeScipRunner permits only container_none execution")
        _validate_container_argv_template(self)


@dataclass(frozen=True, slots=True)
class ScipExecutionPolicy:
    """External execution is off unless this complete policy opts in."""

    enabled: bool = False
    allowed_pins: tuple[ScipCommandPin, ...] = ()
    timeout_seconds: float = 60.0
    output_limit_bytes: int = 1_000_000
    index_limit_bytes: int = 64 * 1024 * 1024
    cpu_seconds: int = 60
    memory_limit_bytes: int = 1_073_741_824
    container_cpus: float = 1.0
    container_pids_limit: int = 128
    cleanup_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a bool")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        for name in ("container_cpus", "cleanup_timeout_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")
        if type(self.allowed_pins) is not tuple or not all(
            isinstance(pin, ScipCommandPin) for pin in self.allowed_pins
        ):
            raise TypeError("allowed_pins must be a tuple of ScipCommandPin values")
        for name in (
            "output_limit_bytes",
            "index_limit_bytes",
            "cpu_seconds",
            "memory_limit_bytes",
            "container_pids_limit",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if len(set(self.allowed_pins)) != len(self.allowed_pins):
            raise ValueError("allowed_pins must not contain duplicates")

    @property
    def resource_description(self) -> str:
        return (
            f"wall={self.timeout_seconds:g}s,cpu={self.cpu_seconds}s,"
            f"memory={self.memory_limit_bytes}B,"
            f"container-cpus={self.container_cpus:g},"
            f"container-pids={self.container_pids_limit},"
            f"combined-output={self.output_limit_bytes}B,"
            f"index={self.index_limit_bytes}B"
        )


@dataclass(frozen=True, slots=True)
class ScipBoundedArtifact:
    """Captured external output with explicit truncation and digest."""

    name: Literal["stdout", "stderr"]
    content: bytes
    truncated: bool
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ScipExecutionOutcome:
    """Backend outcome used by the safe runner and injectable tests."""

    returncode: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    output_limited: bool = False
    index_bytes: bytes | None = None


class ScipCommandExecutor(Protocol):
    """Injectable command backend; production uses the bounded POSIX backend."""

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
        output_path: Path,
        policy: ScipExecutionPolicy,
    ) -> ScipExecutionOutcome: ...


@dataclass(frozen=True, slots=True)
class ScipRunnerResult:
    """Safe runner outcome.  Every failure is non-blocking PARTIAL."""

    status: ScipStatus
    reason: str
    tool_name: str
    tool_version: str
    index_bytes: bytes | None
    stdout: ScipBoundedArtifact
    stderr: ScipBoundedArtifact
    resource_policy: str
    network_isolation: str
    cleanup_status: Literal[
        "not_started",
        "runtime_auto_remove",
        "succeeded",
        "failed",
    ] = "not_started"


class _DiagnosticBuffer(list[ScipDiagnostic]):
    """Keep malformed input from amplifying into an unbounded diagnostic list."""

    def __init__(self, maximum: int) -> None:
        super().__init__()
        self.maximum = maximum
        self.truncated = False

    def append(self, item: ScipDiagnostic) -> None:
        if len(self) < self.maximum:
            super().append(item)
            return
        if not self.truncated:
            self.truncated = True
            self[-1] = ScipDiagnostic(
                code="diagnostic_limit",
                message="additional SCIP diagnostics were deterministically suppressed",
            )


class SafeScipRunner:
    """Run exactly one allowlisted indexer command without a shell.

    Repository installation scripts are never discovered or executed.  The
    command, version, executable/container digest, network isolation, and full
    argv template are supplied out of band by the operator.
    """

    def __init__(
        self,
        policy: ScipExecutionPolicy | None = None,
        *,
        executor: ScipCommandExecutor | None = None,
        cleanup_executor: ScipCommandExecutor | None = None,
    ) -> None:
        self.policy = policy or ScipExecutionPolicy()
        self._executor = executor or _bounded_subprocess
        self._cleanup_executor = cleanup_executor or _bounded_subprocess

    def run(
        self,
        *,
        repo_root: Path,
        pin: ScipCommandPin | None = None,
    ) -> ScipRunnerResult:
        empty_stdout = _artifact("stdout", b"", False)
        empty_stderr = _artifact("stderr", b"", False)
        if not self.policy.enabled:
            return ScipRunnerResult(
                status=ScipStatus.PARTIAL,
                reason="external_execution_disabled",
                tool_name="",
                tool_version="",
                index_bytes=None,
                stdout=empty_stdout,
                stderr=empty_stderr,
                resource_policy=self.policy.resource_description,
                network_isolation="not_started",
                cleanup_status="not_started",
            )
        selected = pin
        if selected is None and len(self.policy.allowed_pins) == 1:
            selected = self.policy.allowed_pins[0]
        if selected is None or selected not in self.policy.allowed_pins:
            return ScipRunnerResult(
                status=ScipStatus.PARTIAL,
                reason="command_not_allowlisted",
                tool_name=selected.tool_name if selected else "",
                tool_version=selected.tool_version if selected else "",
                index_bytes=None,
                stdout=empty_stdout,
                stderr=empty_stderr,
                resource_policy=self.policy.resource_description,
                network_isolation="not_started",
                cleanup_status="not_started",
            )

        try:
            root = repo_root.resolve(strict=True)
        except (OSError, RuntimeError):
            return self._failed(selected, "invalid_repository_root")
        if not root.is_dir():
            return self._failed(selected, "invalid_repository_root")

        try:
            executable = _validated_container_runtime(
                selected,
                repo_root=root,
            )
        except _RunnerRejection as error:
            return self._failed(selected, error.reason)

        with tempfile.TemporaryDirectory(prefix="evidence-rag-scip-") as temporary:
            temp_root = Path(temporary)
            output_root = temp_root / "output"
            output_root.mkdir(mode=0o700)
            index_path = output_root / "index.scip"
            container_name = f"evidence-rag-scip-{secrets.token_hex(8)}"
            try:
                argv = _render_container_argv(
                    selected,
                    executable=executable,
                    repo_root=root,
                    output_root=output_root,
                    container_name=container_name,
                    policy=self.policy,
                )
            except _RunnerRejection as error:
                return self._failed(selected, error.reason)
            env = {
                "HOME": str(temp_root),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": os.defpath,
                "NO_PROXY": "*",
                "no_proxy": "*",
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "ALL_PROXY": "",
                "PIP_NO_INDEX": "1",
                "SCIP_NETWORK": "off",
            }
            try:
                outcome = self._executor(
                    argv,
                    cwd=root,
                    env=env,
                    output_path=index_path,
                    policy=self.policy,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError):
                cleanup_status = self._cleanup_container(
                    executable=executable,
                    container_name=container_name,
                    cwd=root,
                    env=env,
                    temp_root=temp_root,
                )
                return self._failed(
                    selected,
                    "indexer_unavailable",
                    cleanup_status=cleanup_status,
                )
            if not _valid_execution_outcome(outcome):
                cleanup_status = self._cleanup_container(
                    executable=executable,
                    container_name=container_name,
                    cwd=root,
                    env=env,
                    temp_root=temp_root,
                )
                return self._failed(
                    selected,
                    "invalid_executor_outcome",
                    cleanup_status=cleanup_status,
                )

            stdout, stdout_cut = _cap_bytes(
                outcome.stdout,
                self.policy.output_limit_bytes,
            )
            remaining = max(self.policy.output_limit_bytes - len(stdout), 0)
            stderr, stderr_cut = _cap_bytes(outcome.stderr, remaining)
            captured_limited = stdout_cut or stderr_cut or outcome.output_limited
            stdout_artifact = _artifact(
                "stdout",
                stdout,
                captured_limited,
            )
            stderr_artifact = _artifact(
                "stderr",
                stderr,
                captured_limited,
            )
            if outcome.timed_out:
                cleanup_status = self._cleanup_container(
                    executable=executable,
                    container_name=container_name,
                    cwd=root,
                    env=env,
                    temp_root=temp_root,
                )
                return self._failed(
                    selected,
                    "indexer_timeout",
                    stdout=stdout_artifact,
                    stderr=stderr_artifact,
                    cleanup_status=cleanup_status,
                )
            if captured_limited:
                cleanup_status = self._cleanup_container(
                    executable=executable,
                    container_name=container_name,
                    cwd=root,
                    env=env,
                    temp_root=temp_root,
                )
                return self._failed(
                    selected,
                    "indexer_output_limit",
                    stdout=stdout_artifact,
                    stderr=stderr_artifact,
                    cleanup_status=cleanup_status,
                )
            if outcome.returncode != 0:
                cleanup_status = self._cleanup_container(
                    executable=executable,
                    container_name=container_name,
                    cwd=root,
                    env=env,
                    temp_root=temp_root,
                )
                return self._failed(
                    selected,
                    "indexer_failed",
                    stdout=stdout_artifact,
                    stderr=stderr_artifact,
                    cleanup_status=cleanup_status,
                )
            try:
                index_bytes = outcome.index_bytes
                if index_bytes is None:
                    index_bytes = _read_bounded_file(
                        index_path,
                        self.policy.index_limit_bytes,
                    )
                elif len(index_bytes) > self.policy.index_limit_bytes:
                    raise ScipDecodeError("SCIP index exceeds runner index limit")
            except (OSError, ScipDecodeError):
                return self._failed(
                    selected,
                    "index_artifact_invalid",
                    stdout=stdout_artifact,
                    stderr=stderr_artifact,
                    cleanup_status="runtime_auto_remove",
                )
            if not index_bytes:
                return self._failed(
                    selected,
                    "index_artifact_empty",
                    stdout=stdout_artifact,
                    stderr=stderr_artifact,
                    cleanup_status="runtime_auto_remove",
                )
            return ScipRunnerResult(
                status=ScipStatus.COMPLETE,
                reason="index_created",
                tool_name=selected.tool_name,
                tool_version=selected.tool_version,
                index_bytes=index_bytes,
                stdout=stdout_artifact,
                stderr=stderr_artifact,
                resource_policy=self.policy.resource_description,
                network_isolation=selected.network_isolation.value,
                cleanup_status="runtime_auto_remove",
            )

    def _cleanup_container(
        self,
        *,
        executable: str,
        container_name: str,
        cwd: Path,
        env: Mapping[str, str],
        temp_root: Path,
    ) -> Literal["succeeded", "failed"]:
        cleanup_policy = replace(
            self.policy,
            timeout_seconds=self.policy.cleanup_timeout_seconds,
            output_limit_bytes=min(self.policy.output_limit_bytes, 64 * 1024),
        )
        try:
            outcome = self._cleanup_executor(
                (executable, "rm", "-f", container_name),
                cwd=cwd,
                env=env,
                output_path=temp_root / "cleanup.no-output",
                policy=cleanup_policy,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return "failed"
        if not _valid_execution_outcome(outcome):
            return "failed"
        if outcome.timed_out or outcome.output_limited or outcome.returncode != 0:
            return "failed"
        return "succeeded"

    def _failed(
        self,
        pin: ScipCommandPin,
        reason: str,
        *,
        stdout: ScipBoundedArtifact | None = None,
        stderr: ScipBoundedArtifact | None = None,
        cleanup_status: Literal[
            "not_started",
            "runtime_auto_remove",
            "succeeded",
            "failed",
        ] = "not_started",
    ) -> ScipRunnerResult:
        return ScipRunnerResult(
            status=ScipStatus.PARTIAL,
            reason=reason,
            tool_name=pin.tool_name,
            tool_version=pin.tool_version,
            index_bytes=None,
            stdout=stdout or _artifact("stdout", b"", False),
            stderr=stderr or _artifact("stderr", b"", False),
            resource_policy=self.policy.resource_description,
            network_isolation=pin.network_isolation.value,
            cleanup_status=cleanup_status,
        )


@dataclass(slots=True)
class _DecodeBudget:
    limits: ScipDecodeLimits
    total_fields: int = 0
    documents: int = 0
    symbols: int = 0
    occurrences: int = 0

    def field(self) -> None:
        self.total_fields += 1
        if self.total_fields > self.limits.max_total_fields:
            raise ScipDecodeError("SCIP index exceeds total field limit")

    def document(self) -> None:
        self.documents += 1
        if self.documents > self.limits.max_documents:
            raise ScipDecodeError("SCIP index exceeds document limit")

    def symbol(self) -> None:
        self.symbols += 1
        if self.symbols > self.limits.max_symbols:
            raise ScipDecodeError("SCIP index exceeds symbol limit")

    def occurrence(self) -> None:
        self.occurrences += 1
        if self.occurrences > self.limits.max_occurrences:
            raise ScipDecodeError("SCIP index exceeds occurrence limit")


class _Reader(Protocol):
    @property
    def eof(self) -> bool: ...

    def read_byte(self) -> int: ...

    def read_exact(self, length: int) -> bytes: ...

    def skip(self, length: int) -> None: ...


class _StreamReader:
    def __init__(self, stream: BinaryIO, maximum: int) -> None:
        self.stream = stream
        self.maximum = maximum
        self.count = 0
        self.hasher = hashlib.sha256()
        self._lookahead: bytes | None = None

    @property
    def eof(self) -> bool:
        if self._lookahead is None:
            self._lookahead = self.stream.read(1)
        return not self._lookahead

    def read_byte(self) -> int:
        if self._lookahead is not None:
            chunk = self._lookahead
            self._lookahead = None
        else:
            chunk = self.stream.read(1)
        if not chunk:
            raise ScipDecodeError("truncated SCIP protobuf")
        self._account(chunk)
        return chunk[0]

    def read_exact(self, length: int) -> bytes:
        _validate_length(length, self.maximum)
        chunks: list[bytes] = []
        remaining = length
        if self._lookahead is not None and remaining:
            chunks.append(self._lookahead)
            remaining -= len(self._lookahead)
            self._lookahead = None
        while remaining:
            chunk = self.stream.read(min(remaining, 64 * 1024))
            if not chunk:
                raise ScipDecodeError("truncated SCIP protobuf")
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        self._account(payload)
        return payload

    def skip(self, length: int) -> None:
        _validate_length(length, self.maximum)
        remaining = length
        if self._lookahead is not None and remaining:
            chunk = self._lookahead
            self._lookahead = None
            self._account(chunk)
            remaining -= len(chunk)
        while remaining:
            chunk = self.stream.read(min(remaining, 64 * 1024))
            if not chunk:
                raise ScipDecodeError("truncated SCIP protobuf")
            self._account(chunk)
            remaining -= len(chunk)

    def _account(self, chunk: bytes) -> None:
        self.count += len(chunk)
        if self.count > self.maximum:
            raise ScipDecodeError("SCIP index exceeds file byte limit")
        self.hasher.update(chunk)


class _MemoryReader:
    def __init__(self, payload: bytes) -> None:
        self.payload = memoryview(payload)
        self.offset = 0

    @property
    def eof(self) -> bool:
        return self.offset >= len(self.payload)

    def read_byte(self) -> int:
        if self.eof:
            raise ScipDecodeError("truncated SCIP protobuf")
        value = self.payload[self.offset]
        self.offset += 1
        return value

    def read_exact(self, length: int) -> bytes:
        _validate_length(length, len(self.payload))
        end = self.offset + length
        if end > len(self.payload):
            raise ScipDecodeError("truncated SCIP protobuf")
        value = self.payload[self.offset : end].tobytes()
        self.offset = end
        return value

    def skip(self, length: int) -> None:
        _validate_length(length, len(self.payload))
        end = self.offset + length
        if end > len(self.payload):
            raise ScipDecodeError("truncated SCIP protobuf")
        self.offset = end


@dataclass(frozen=True, slots=True)
class _DecodedOccurrence:
    range_values: tuple[int, ...]
    symbol: str
    symbol_roles: int


@dataclass(frozen=True, slots=True)
class _DecodedDocument:
    relative_path: str
    occurrences: tuple[_DecodedOccurrence, ...]
    symbols: tuple[ScipSymbolInformation, ...]
    language: str
    position_encoding: int


class ScipProtobufDecoder:
    """Streaming decoder for the bounded official SCIP subset."""

    def __init__(self, limits: ScipDecodeLimits | None = None) -> None:
        self.limits = limits or ScipDecodeLimits()

    def decode_file(self, path: Path) -> ScipDecodedIndex:
        try:
            stat = path.stat()
        except OSError as error:
            raise ScipDecodeError("SCIP index is unavailable") from error
        if not path.is_file():
            raise ScipDecodeError("SCIP index is not a regular file")
        if stat.st_size > self.limits.max_file_bytes:
            raise ScipDecodeError("SCIP index exceeds file byte limit")
        with path.open("rb") as stream:
            return self.decode_stream(stream)

    def decode_bytes(self, payload: bytes) -> ScipDecodedIndex:
        if type(payload) is not bytes:
            raise TypeError("SCIP payload must be bytes")
        return self.decode_stream(io.BytesIO(payload))

    def decode_stream(self, stream: BinaryIO) -> ScipDecodedIndex:
        reader = _StreamReader(stream, self.limits.max_file_bytes)
        budget = _DecodeBudget(self.limits)
        metadata = ScipMetadata()
        metadata_seen = False
        documents: list[_DecodedDocument] = []
        external_symbols: list[ScipSymbolInformation] = []
        field_count = 0
        while not reader.eof:
            field_number, wire_type = _read_key(reader)
            field_count = self._count_field(field_count, budget)
            if field_number == 1 and wire_type == 2:
                metadata = self._parse_metadata(
                    self._read_message(reader),
                    budget,
                    depth=1,
                )
                metadata_seen = True
            elif field_number == 2 and wire_type == 2:
                budget.document()
                documents.append(
                    self._parse_document(
                        self._read_message(reader),
                        budget,
                        depth=1,
                    )
                )
            elif field_number == 3 and wire_type == 2:
                budget.symbol()
                external_symbols.append(
                    self._parse_symbol(
                        self._read_message(reader),
                        budget,
                        depth=1,
                        is_external=True,
                    )
                )
            else:
                _skip_field(
                    reader,
                    field_number,
                    wire_type,
                    self.limits,
                    budget,
                    depth=1,
                )
        if reader.count == 0:
            raise ScipDecodeError("SCIP index is empty")
        if not metadata_seen:
            metadata = ScipMetadata()
        return ScipDecodedIndex(
            metadata=metadata,
            metadata_present=metadata_seen,
            documents=tuple(documents),
            external_symbols=tuple(external_symbols),
            content_sha256=reader.hasher.hexdigest(),
            byte_size=reader.count,
        )

    def _parse_metadata(
        self,
        payload: bytes,
        budget: _DecodeBudget,
        *,
        depth: int,
    ) -> ScipMetadata:
        self._check_depth(depth)
        reader = _MemoryReader(payload)
        version = 0
        tool_name = ""
        tool_version = ""
        tool_arguments: tuple[str, ...] = ()
        project_root = ""
        encoding = 0
        field_count = 0
        while not reader.eof:
            number, wire = _read_key(reader)
            field_count = self._count_field(field_count, budget)
            if number == 1 and wire == 0:
                version = _read_bounded_int(reader, self.limits.max_range_value)
            elif number == 2 and wire == 2:
                tool_name, tool_version, tool_arguments = self._parse_tool_info(
                    self._read_message(reader),
                    budget,
                    depth=depth + 1,
                )
            elif number == 3 and wire == 2:
                project_root = self._read_string(reader)
            elif number == 4 and wire == 0:
                encoding = _read_bounded_int(reader, self.limits.max_range_value)
            else:
                _skip_field(
                    reader,
                    number,
                    wire,
                    self.limits,
                    budget,
                    depth=depth + 1,
                )
        return ScipMetadata(
            protocol_version=version,
            tool_name=tool_name,
            tool_version=tool_version,
            tool_arguments=tool_arguments,
            project_root=project_root,
            text_document_encoding=encoding,
        )

    def _parse_tool_info(
        self,
        payload: bytes,
        budget: _DecodeBudget,
        *,
        depth: int,
    ) -> tuple[str, str, tuple[str, ...]]:
        self._check_depth(depth)
        reader = _MemoryReader(payload)
        name = ""
        version = ""
        arguments: list[str] = []
        field_count = 0
        while not reader.eof:
            number, wire = _read_key(reader)
            field_count = self._count_field(field_count, budget)
            if number == 1 and wire == 2:
                name = self._read_string(reader)
            elif number == 2 and wire == 2:
                version = self._read_string(reader)
            elif number == 3 and wire == 2:
                arguments.append(self._read_string(reader))
            else:
                _skip_field(
                    reader,
                    number,
                    wire,
                    self.limits,
                    budget,
                    depth=depth + 1,
                )
        return name, version, tuple(arguments)

    def _parse_document(
        self,
        payload: bytes,
        budget: _DecodeBudget,
        *,
        depth: int,
    ) -> _DecodedDocument:
        self._check_depth(depth)
        reader = _MemoryReader(payload)
        relative_path = ""
        language = ""
        position_encoding = 0
        occurrences: list[_DecodedOccurrence] = []
        symbols: list[ScipSymbolInformation] = []
        field_count = 0
        while not reader.eof:
            number, wire = _read_key(reader)
            field_count = self._count_field(field_count, budget)
            if number == 1 and wire == 2:
                relative_path = self._read_string(reader)
            elif number == 2 and wire == 2:
                budget.occurrence()
                occurrences.append(
                    self._parse_occurrence(
                        self._read_message(reader),
                        budget,
                        depth=depth + 1,
                    )
                )
            elif number == 3 and wire == 2:
                budget.symbol()
                symbols.append(
                    self._parse_symbol(
                        self._read_message(reader),
                        budget,
                        depth=depth + 1,
                        is_external=False,
                    )
                )
            elif number == 4 and wire == 2:
                language = self._read_string(reader)
            elif number == 6 and wire == 0:
                position_encoding = _read_bounded_int(
                    reader,
                    self.limits.max_range_value,
                )
            else:
                _skip_field(
                    reader,
                    number,
                    wire,
                    self.limits,
                    budget,
                    depth=depth + 1,
                )
        return _DecodedDocument(
            relative_path=relative_path,
            occurrences=tuple(occurrences),
            symbols=tuple(symbols),
            language=language,
            position_encoding=position_encoding,
        )

    def _parse_occurrence(
        self,
        payload: bytes,
        budget: _DecodeBudget,
        *,
        depth: int,
    ) -> _DecodedOccurrence:
        self._check_depth(depth)
        reader = _MemoryReader(payload)
        legacy: list[int] = []
        typed: tuple[int, ...] | None = None
        symbol = ""
        symbol_roles = 0
        field_count = 0
        while not reader.eof:
            number, wire = _read_key(reader)
            field_count = self._count_field(field_count, budget)
            if number == 1 and wire == 2:
                legacy.extend(self._read_packed_ints(reader))
            elif number == 1 and wire == 0:
                legacy.append(_read_bounded_int(reader, self.limits.max_range_value))
            elif number == 2 and wire == 2:
                symbol = self._read_string(reader)
            elif number == 3 and wire == 0:
                symbol_roles = _read_bounded_int(reader, (1 << 31) - 1)
            elif number == 8 and wire == 2:
                candidate = self._parse_typed_range(
                    self._read_message(reader),
                    budget,
                    depth=depth + 1,
                    expected=3,
                )
                typed = self._merge_typed_range(typed, candidate)
            elif number == 9 and wire == 2:
                candidate = self._parse_typed_range(
                    self._read_message(reader),
                    budget,
                    depth=depth + 1,
                    expected=4,
                )
                typed = self._merge_typed_range(typed, candidate)
            else:
                _skip_field(
                    reader,
                    number,
                    wire,
                    self.limits,
                    budget,
                    depth=depth + 1,
                )
        legacy_tuple = tuple(legacy)
        if legacy_tuple and len(legacy_tuple) not in {3, 4}:
            raise ScipDecodeError("SCIP occurrence range must contain 3 or 4 integers")
        if typed is not None and legacy_tuple and typed != legacy_tuple:
            raise ScipDecodeError("SCIP legacy and typed occurrence ranges conflict")
        selected = typed or legacy_tuple
        if len(selected) not in {3, 4}:
            raise ScipDecodeError("SCIP occurrence is missing a supported range")
        return _DecodedOccurrence(
            range_values=selected,
            symbol=symbol,
            symbol_roles=symbol_roles,
        )

    def _parse_typed_range(
        self,
        payload: bytes,
        budget: _DecodeBudget,
        *,
        depth: int,
        expected: Literal[3, 4],
    ) -> tuple[int, ...]:
        """Decode SCIP's typed single-line (8) and multi-line (9) ranges.

        The typed messages use scalar fields 1..3 or 1..4.  Packed field 1 is
        accepted for forward-compatible producers that retain the same tuple
        representation inside the typed wrapper.
        """

        self._check_depth(depth)
        reader = _MemoryReader(payload)
        values: dict[int, int] = {}
        packed: tuple[int, ...] = ()
        field_count = 0
        while not reader.eof:
            number, wire = _read_key(reader)
            field_count = self._count_field(field_count, budget)
            if 1 <= number <= expected and wire == 0:
                if number in values:
                    raise ScipDecodeError("duplicate typed range component")
                values[number] = _read_bounded_int(reader, self.limits.max_range_value)
            elif number == 1 and wire == 2:
                if packed:
                    raise ScipDecodeError("duplicate packed typed range")
                packed = self._read_packed_ints(reader)
            else:
                _skip_field(
                    reader,
                    number,
                    wire,
                    self.limits,
                    budget,
                    depth=depth + 1,
                )
        if packed:
            if values or len(packed) != expected:
                raise ScipDecodeError("invalid packed typed range")
            return packed
        if set(values) != set(range(1, expected + 1)):
            raise ScipDecodeError("typed SCIP range is missing a component")
        return tuple(values[index] for index in range(1, expected + 1))

    def _parse_symbol(
        self,
        payload: bytes,
        budget: _DecodeBudget,
        *,
        depth: int,
        is_external: bool,
    ) -> ScipSymbolInformation:
        self._check_depth(depth)
        reader = _MemoryReader(payload)
        symbol = ""
        relationships: list[ScipRelationship] = []
        kind = 0
        display_name = ""
        enclosing_symbol = ""
        field_count = 0
        while not reader.eof:
            number, wire = _read_key(reader)
            field_count = self._count_field(field_count, budget)
            if number == 1 and wire == 2:
                symbol = self._read_string(reader)
            elif number == 4 and wire == 2:
                relationships.append(
                    self._parse_relationship(
                        self._read_message(reader),
                        budget,
                        depth=depth + 1,
                    )
                )
            elif number == 5 and wire == 0:
                kind = _read_bounded_int(reader, self.limits.max_range_value)
            elif number == 6 and wire == 2:
                display_name = self._read_string(reader)
            elif number == 8 and wire == 2:
                enclosing_symbol = self._read_string(reader)
            else:
                _skip_field(
                    reader,
                    number,
                    wire,
                    self.limits,
                    budget,
                    depth=depth + 1,
                )
        return ScipSymbolInformation(
            symbol=symbol,
            relationships=tuple(relationships),
            kind=kind,
            display_name=display_name,
            enclosing_symbol=enclosing_symbol,
            is_external=is_external,
            is_local=_is_local_symbol(symbol),
        )

    def _parse_relationship(
        self,
        payload: bytes,
        budget: _DecodeBudget,
        *,
        depth: int,
    ) -> ScipRelationship:
        self._check_depth(depth)
        reader = _MemoryReader(payload)
        symbol = ""
        flags = {2: False, 3: False, 4: False, 5: False}
        field_count = 0
        while not reader.eof:
            number, wire = _read_key(reader)
            field_count = self._count_field(field_count, budget)
            if number == 1 and wire == 2:
                symbol = self._read_string(reader)
            elif number in flags and wire == 0:
                flags[number] = bool(_read_bounded_int(reader, 1))
            else:
                _skip_field(
                    reader,
                    number,
                    wire,
                    self.limits,
                    budget,
                    depth=depth + 1,
                )
        return ScipRelationship(
            symbol=symbol,
            is_reference=flags[2],
            is_implementation=flags[3],
            is_type_definition=flags[4],
            is_definition=flags[5],
        )

    def _read_message(self, reader: _Reader) -> bytes:
        length = _read_varint(reader)
        if length > self.limits.max_message_bytes:
            raise ScipDecodeError("SCIP nested message exceeds message byte limit")
        return reader.read_exact(length)

    def _read_string(self, reader: _Reader) -> str:
        length = _read_varint(reader)
        if length > self.limits.max_string_bytes:
            raise ScipDecodeError("SCIP string exceeds string byte limit")
        payload = reader.read_exact(length)
        try:
            value = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ScipDecodeError("SCIP string is not valid UTF-8") from error
        return unicodedata.normalize("NFC", value)

    def _read_packed_ints(self, reader: _Reader) -> tuple[int, ...]:
        payload = self._read_message(reader)
        packed_reader = _MemoryReader(payload)
        values: list[int] = []
        while not packed_reader.eof:
            if len(values) >= 4:
                raise ScipDecodeError("SCIP range contains too many integers")
            values.append(_read_bounded_int(packed_reader, self.limits.max_range_value))
        return tuple(values)

    def _count_field(self, field_count: int, budget: _DecodeBudget) -> int:
        field_count += 1
        budget.field()
        if field_count > self.limits.max_fields_per_message:
            raise ScipDecodeError("SCIP message exceeds field count limit")
        return field_count

    def _check_depth(self, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise ScipDecodeError("SCIP message exceeds nesting depth limit")

    @staticmethod
    def _merge_typed_range(
        current: tuple[int, ...] | None,
        candidate: tuple[int, ...],
    ) -> tuple[int, ...]:
        if current is not None and current != candidate:
            raise ScipDecodeError("multiple typed SCIP ranges conflict")
        return candidate


class ScipConsumer:
    """Consume a supplied index first, with an optional safe runner fallback."""

    consumer_version = SCIP_CONSUMER_VERSION

    def __init__(
        self,
        *,
        decoder: ScipProtobufDecoder | None = None,
        runner: SafeScipRunner | None = None,
    ) -> None:
        self.decoder = decoder or ScipProtobufDecoder()
        self.runner = runner

    def consume(
        self,
        *,
        repo_root: Path,
        scope: ScipScope,
        resolver: ScipReadOnlyResolver | None = None,
        index_path: Path | None = None,
        runner_pin: ScipCommandPin | None = None,
        raw_object_id: str = "",
    ) -> ScipConsumeResult:
        try:
            root = repo_root.resolve(strict=True)
        except (OSError, RuntimeError):
            return _partial_result("invalid_repository_root")
        if not root.is_dir():
            return _partial_result("invalid_repository_root")

        supplied = index_path
        if supplied is None:
            default_index = root / "index.scip"
            supplied = default_index if default_index.is_file() else None
        decoded: ScipDecodedIndex
        source_name: str
        expected_tool: tuple[str, str] | None = None
        try:
            if supplied is not None:
                safe_index = supplied.resolve(strict=True)
                if not safe_index.is_file():
                    return _partial_result("index_unavailable")
                decoded = self.decoder.decode_file(safe_index)
                source_name = safe_index.name
            elif self.runner is not None:
                run = self.runner.run(repo_root=root, pin=runner_pin)
                if run.status is not ScipStatus.COMPLETE or run.index_bytes is None:
                    return _partial_result(run.reason)
                decoded = self.decoder.decode_bytes(run.index_bytes)
                source_name = "safe-runner:index.scip"
                expected_tool = (run.tool_name, run.tool_version)
            else:
                return _partial_result("index_unavailable")
        except (OSError, ScipDecodeError) as error:
            return _partial_result(
                "invalid_or_oversized_index",
                detail=_safe_error(error),
            )

        if (
            expected_tool is not None
            and (
                decoded.metadata.tool_name,
                decoded.metadata.tool_version,
            )
            != expected_tool
        ):
            return _partial_result("indexer_metadata_mismatch")
        try:
            return self._canonicalize(
                decoded,
                repo_root=root,
                scope=scope,
                resolver=resolver,
                raw_object_id=raw_object_id,
                source_name=source_name,
            )
        except ScipResolverFailure:
            return _resolver_failure_result(
                decoded,
                raw_object_id=raw_object_id,
                source_name=source_name,
            )

    def consume_bytes(
        self,
        payload: bytes,
        *,
        repo_root: Path,
        scope: ScipScope,
        resolver: ScipReadOnlyResolver | None = None,
        raw_object_id: str = "",
        source_name: str = "provided:index.scip",
    ) -> ScipConsumeResult:
        try:
            root = repo_root.resolve(strict=True)
            if not root.is_dir():
                raise OSError("not a directory")
            decoded = self.decoder.decode_bytes(payload)
        except (OSError, RuntimeError, ScipDecodeError) as error:
            return _partial_result(
                "invalid_or_oversized_index",
                detail=_safe_error(error),
            )
        try:
            return self._canonicalize(
                decoded,
                repo_root=root,
                scope=scope,
                resolver=resolver,
                raw_object_id=raw_object_id,
                source_name=source_name,
            )
        except ScipResolverFailure:
            return _resolver_failure_result(
                decoded,
                raw_object_id=raw_object_id,
                source_name=source_name,
            )

    def _canonicalize(
        self,
        decoded: ScipDecodedIndex,
        *,
        repo_root: Path,
        scope: ScipScope,
        resolver: ScipReadOnlyResolver | None,
        raw_object_id: str,
        source_name: str,
    ) -> ScipConsumeResult:
        diagnostics = _DiagnosticBuffer(self.decoder.limits.max_diagnostics)
        documents: list[ScipLinkedDocument] = []
        occurrences: list[ScipLinkedOccurrence] = []
        relationships: list[ScipLinkedRelationship] = []
        external_by_symbol = {item.symbol: item for item in decoded.external_symbols if item.symbol}
        known_internal_symbols = {
            item.symbol
            for document in decoded.documents
            for item in document.symbols
            if item.symbol and not item.is_local
        }
        document_symbol_paths: dict[str, set[str]] = {}
        normalized_documents: list[tuple[str, _DecodedDocument]] = []
        degraded = False
        if (
            not decoded.metadata_present
            or not decoded.metadata.tool_name
            or not decoded.metadata.tool_version
        ):
            diagnostics.append(
                ScipDiagnostic(
                    code="metadata_incomplete",
                    message="SCIP protocol/indexer provenance metadata is incomplete",
                )
            )
            degraded = True
        if not decoded.documents:
            diagnostics.append(
                ScipDiagnostic(
                    code="empty_index",
                    message="SCIP index contains no documents",
                )
            )
            degraded = True
        for raw_document in decoded.documents:
            try:
                relative_path = _canonical_relative_path(
                    raw_document.relative_path,
                    repo_root,
                )
            except ValueError:
                diagnostics.append(
                    ScipDiagnostic(
                        code="bad_path",
                        message="SCIP document path was rejected",
                        relative_path=_bounded_display(raw_document.relative_path),
                    )
                )
                degraded = True
                continue
            normalized_documents.append((relative_path, raw_document))
            for item in raw_document.symbols:
                if item.symbol and not item.is_local:
                    document_symbol_paths.setdefault(item.symbol, set()).add(relative_path)

        for relative_path, raw_document in sorted(
            normalized_documents,
            key=lambda item: item[0],
        ):
            file_link = _resolve_file(
                resolver,
                scope=scope,
                relative_path=relative_path,
                diagnostics=diagnostics,
            )
            if file_link.status not in {ScipLinkStatus.RESOLVED}:
                degraded = True
            canonical_occurrences: dict[
                tuple[ScipRange, str, int],
                ScipOccurrence,
            ] = {}
            for raw_occurrence in raw_document.occurrences:
                if not raw_occurrence.symbol:
                    continue
                try:
                    source_range = _canonical_range(raw_occurrence.range_values)
                except ValueError:
                    diagnostics.append(
                        ScipDiagnostic(
                            code="bad_range",
                            message="SCIP occurrence range was rejected",
                            relative_path=relative_path,
                            symbol=_bounded_display(raw_occurrence.symbol),
                        )
                    )
                    degraded = True
                    continue
                is_local = _is_local_symbol(raw_occurrence.symbol)
                is_external = raw_occurrence.symbol in external_by_symbol
                if is_external:
                    kind = ScipOccurrenceKind.EXTERNAL
                elif raw_occurrence.symbol_roles & 1:
                    kind = ScipOccurrenceKind.DEFINITION
                else:
                    kind = ScipOccurrenceKind.REFERENCE
                occurrence = ScipOccurrence(
                    relative_path=relative_path,
                    source_range=source_range,
                    symbol=raw_occurrence.symbol,
                    symbol_roles=raw_occurrence.symbol_roles,
                    kind=kind,
                    is_local=is_local,
                )
                key = (source_range, occurrence.symbol, occurrence.symbol_roles)
                if key in canonical_occurrences:
                    diagnostics.append(
                        ScipDiagnostic(
                            code="duplicate_occurrence",
                            message="duplicate SCIP occurrence was deterministically removed",
                            relative_path=relative_path,
                            symbol=_bounded_display(occurrence.symbol),
                        )
                    )
                    continue
                canonical_occurrences[key] = occurrence

            document_symbols: list[ScipSymbolInformation] = []
            for item in raw_document.symbols:
                if item.symbol:
                    document_symbols.append(item)
                    continue
                diagnostics.append(
                    ScipDiagnostic(
                        code="symbol_missing",
                        message="SCIP SymbolInformation without an identity was rejected",
                        relative_path=relative_path,
                    )
                )
                degraded = True
            document = ScipDocument(
                relative_path=relative_path,
                language=raw_document.language,
                position_encoding=raw_document.position_encoding,
                occurrences=tuple(
                    sorted(
                        canonical_occurrences.values(),
                        key=_occurrence_key,
                    )
                ),
                symbols=tuple(
                    sorted(
                        document_symbols,
                        key=_symbol_key,
                    )
                ),
            )
            documents.append(ScipLinkedDocument(document=document, file_link=file_link))
            for occurrence in document.occurrences:
                link = _resolve_occurrence_symbol(
                    resolver,
                    scope=scope,
                    occurrence=occurrence,
                    external_symbols=external_by_symbol,
                    known_internal_symbols=known_internal_symbols,
                    symbol_paths=document_symbol_paths,
                    diagnostics=diagnostics,
                )
                if link.status in {
                    ScipLinkStatus.UNRESOLVED,
                    ScipLinkStatus.AMBIGUOUS,
                    ScipLinkStatus.SCOPE_MISMATCH,
                    ScipLinkStatus.RESOLVER_UNAVAILABLE,
                }:
                    degraded = True
                occurrences.append(
                    ScipLinkedOccurrence(
                        occurrence=occurrence,
                        symbol_link=link,
                    )
                )

            for source_symbol in document.symbols:
                source_path = relative_path
                if source_symbol.is_local:
                    source_link = ScipLink(status=ScipLinkStatus.LOCAL)
                else:
                    source_link = _resolve_symbol(
                        resolver,
                        scope=scope,
                        symbol=source_symbol.symbol,
                        relative_path=source_path,
                        expected_type="CodeSymbol",
                        diagnostics=diagnostics,
                    )
                    if source_link.status not in {ScipLinkStatus.RESOLVED}:
                        degraded = True
                for relationship in source_symbol.relationships:
                    if not relationship.symbol:
                        diagnostics.append(
                            ScipDiagnostic(
                                code="relationship_symbol_missing",
                                message="SCIP relationship without a target was rejected",
                                relative_path=relative_path,
                                symbol=_bounded_display(source_symbol.symbol),
                            )
                        )
                        degraded = True
                        continue
                    if relationship.symbol in external_by_symbol:
                        target_link = ScipLink(status=ScipLinkStatus.EXTERNAL)
                        diagnostics.append(
                            ScipDiagnostic(
                                code="external_symbol",
                                message="SCIP relationship target is external",
                                relative_path=relative_path,
                                symbol=_bounded_display(relationship.symbol),
                            )
                        )
                    elif _is_local_symbol(relationship.symbol):
                        target_link = ScipLink(status=ScipLinkStatus.LOCAL)
                    else:
                        paths = sorted(document_symbol_paths.get(relationship.symbol, ()))
                        target_path = paths[0] if len(paths) == 1 else None
                        target_link = _resolve_symbol(
                            resolver,
                            scope=scope,
                            symbol=relationship.symbol,
                            relative_path=target_path,
                            expected_type="CodeSymbol",
                            diagnostics=diagnostics,
                        )
                        if target_link.status not in {ScipLinkStatus.RESOLVED}:
                            degraded = True
                    relationships.append(
                        ScipLinkedRelationship(
                            source_symbol=source_symbol.symbol,
                            target_symbol=relationship.symbol,
                            relationship=relationship,
                            source_link=source_link,
                            target_link=target_link,
                        )
                    )

        external_symbols = tuple(
            sorted(
                external_by_symbol.values(),
                key=_symbol_key,
            )
        )
        for item in external_symbols:
            diagnostics.append(
                ScipDiagnostic(
                    code="external_symbol",
                    message="SCIP external symbol retained outside governed entities",
                    symbol=_bounded_display(item.symbol),
                )
            )
        provenance = _scip_provenance(
            decoded,
            raw_object_id=raw_object_id,
            source_name=source_name,
        )
        return ScipConsumeResult(
            status=ScipStatus.PARTIAL if degraded else ScipStatus.COMPLETE,
            semantic_ready=False,
            documents=tuple(documents),
            occurrences=tuple(sorted(occurrences, key=_linked_occurrence_key)),
            external_symbols=external_symbols,
            relationships=tuple(sorted(relationships, key=_relationship_key)),
            diagnostics=tuple(sorted(diagnostics, key=_diagnostic_key)),
            provenance=provenance,
            fallback=ScipFallbackTrace(
                attempted=degraded,
                engine="tree-sitter",
                outcome="delegated" if degraded else "not_used",
                reason=(
                    "scip_partial_tree_sitter_delegated"
                    if degraded
                    else "valid_scip_index_consumed"
                ),
            ),
        )


def _resolver_candidates(value: object) -> tuple[ScipEntityRef, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ScipResolverFailure("resolver returned a non-sequence")
    return tuple(value)


def _resolve_file(
    resolver: ScipReadOnlyResolver | None,
    *,
    scope: ScipScope,
    relative_path: str,
    diagnostics: list[ScipDiagnostic],
) -> ScipLink:
    if resolver is None:
        raise ScipResolverFailure("resolver unavailable")
    try:
        raw_candidates = resolver.resolve_file(
            scope=scope,
            relative_path=relative_path,
        )
        candidates = _resolver_candidates(raw_candidates)
    except Exception as error:
        raise ScipResolverFailure("resolver backend failure") from error
    return _validate_candidates(
        candidates,
        scope=scope,
        relative_path=relative_path,
        symbol="",
        expected_type="FileVersion",
        diagnostics=diagnostics,
    )


def _resolve_symbol(
    resolver: ScipReadOnlyResolver | None,
    *,
    scope: ScipScope,
    symbol: str,
    relative_path: str | None,
    expected_type: Literal["CodeSymbol"],
    diagnostics: list[ScipDiagnostic],
) -> ScipLink:
    if resolver is None:
        raise ScipResolverFailure("resolver unavailable")
    try:
        raw_candidates = resolver.resolve_symbol(
            scope=scope,
            symbol=symbol,
            relative_path=relative_path,
        )
        candidates = _resolver_candidates(raw_candidates)
    except Exception as error:
        raise ScipResolverFailure("resolver backend failure") from error
    return _validate_candidates(
        candidates,
        scope=scope,
        relative_path=relative_path,
        symbol=symbol,
        expected_type=expected_type,
        diagnostics=diagnostics,
    )


def _resolve_occurrence_symbol(
    resolver: ScipReadOnlyResolver | None,
    *,
    scope: ScipScope,
    occurrence: ScipOccurrence,
    external_symbols: Mapping[str, ScipSymbolInformation],
    known_internal_symbols: set[str],
    symbol_paths: Mapping[str, set[str]],
    diagnostics: list[ScipDiagnostic],
) -> ScipLink:
    if occurrence.is_local:
        return ScipLink(status=ScipLinkStatus.LOCAL)
    if occurrence.symbol in external_symbols:
        diagnostics.append(
            ScipDiagnostic(
                code="external_symbol",
                message="SCIP occurrence target is external",
                relative_path=occurrence.relative_path,
                symbol=_bounded_display(occurrence.symbol),
            )
        )
        return ScipLink(status=ScipLinkStatus.EXTERNAL)
    paths = sorted(symbol_paths.get(occurrence.symbol, ()))
    target_path = paths[0] if len(paths) == 1 else occurrence.relative_path
    link = _resolve_symbol(
        resolver,
        scope=scope,
        symbol=occurrence.symbol,
        relative_path=target_path,
        expected_type="CodeSymbol",
        diagnostics=diagnostics,
    )
    if occurrence.symbol and occurrence.symbol not in known_internal_symbols:
        diagnostics.append(
            ScipDiagnostic(
                code="unresolved_symbol",
                message="SCIP symbol has no in-index definition metadata",
                relative_path=occurrence.relative_path,
                symbol=_bounded_display(occurrence.symbol),
                candidate_ids=link.candidate_ids,
            )
        )
    return link


def _validate_candidates(
    candidates: Sequence[ScipEntityRef],
    *,
    scope: ScipScope,
    relative_path: str | None,
    symbol: str,
    expected_type: Literal["FileVersion", "CodeSymbol"],
    diagnostics: list[ScipDiagnostic],
) -> ScipLink:
    valid: dict[str, ScipEntityRef] = {}
    for candidate in candidates:
        if not isinstance(candidate, ScipEntityRef):
            raise ScipResolverFailure("resolver returned an invalid entity")
        scope_matches = (
            candidate.project_id == scope.project_id
            and candidate.repository_id == scope.repository_id
            and candidate.generation_id == scope.generation_id
            and candidate.acl_ref == scope.acl_ref
        )
        path_matches = relative_path is None or candidate.relative_path == relative_path
        symbol_matches = (
            expected_type == "FileVersion"
            or not candidate.scip_symbol
            or candidate.scip_symbol == symbol
        )
        if (
            candidate.entity_type != expected_type
            or not scope_matches
            or not path_matches
            or not symbol_matches
        ):
            raise ScipResolverFailure("resolver entity violated scope contract")
        previous = valid.get(candidate.entity_id)
        if previous is not None and previous != candidate:
            raise ScipResolverFailure("resolver returned conflicting entity identities")
        valid[candidate.entity_id] = candidate
    ordered = tuple(valid[key] for key in sorted(valid))
    if not ordered:
        diagnostics.append(
            ScipDiagnostic(
                code="unresolved",
                message=f"{expected_type} resolver returned no governed candidate",
                relative_path=relative_path or "",
                symbol=_bounded_display(symbol),
            )
        )
        return ScipLink(status=ScipLinkStatus.UNRESOLVED)
    if len(ordered) > 1:
        candidate_ids = tuple(item.entity_id for item in ordered)
        diagnostics.append(
            ScipDiagnostic(
                code="ambiguous",
                message=f"{expected_type} resolver returned multiple governed candidates",
                relative_path=relative_path or "",
                symbol=_bounded_display(symbol),
                candidate_ids=candidate_ids,
            )
        )
        return ScipLink(
            status=ScipLinkStatus.AMBIGUOUS,
            candidate_ids=candidate_ids,
        )
    return ScipLink(
        status=ScipLinkStatus.RESOLVED,
        entity=ordered[0],
        candidate_ids=(ordered[0].entity_id,),
    )


def _canonical_relative_path(raw: str, repo_root: Path) -> str:
    if type(raw) is not str or not raw:
        raise ValueError("SCIP document path must be non-empty")
    if "\\" in raw or "\x00" in raw or _DRIVE_PATH.match(raw):
        raise ValueError("SCIP document path is not canonical POSIX relative path")
    if any(unicodedata.category(character).startswith("C") for character in raw):
        raise ValueError("SCIP document path contains a control character")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("SCIP document path escapes repository")
    canonical_parts = tuple(part for part in path.parts if part not in {"", "."})
    if not canonical_parts:
        raise ValueError("SCIP document path must identify a file")
    canonical = PurePosixPath(*canonical_parts).as_posix()
    candidate = (repo_root / canonical).resolve(strict=False)
    try:
        candidate.relative_to(repo_root)
    except ValueError as error:
        raise ValueError("SCIP document path follows a symlink outside repository") from error
    return canonical


def _canonical_range(values: tuple[int, ...]) -> ScipRange:
    if len(values) == 3:
        start_line, start_character, end_character = values
        end_line = start_line
    elif len(values) == 4:
        start_line, start_character, end_line, end_character = values
    else:
        raise ValueError("SCIP range must contain three or four integers")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("SCIP range values must be integers")
    if min(values) < 0:
        raise ValueError("SCIP range values must be non-negative")
    start = ScipPosition(line=start_line + 1, character=start_character + 1)
    end = ScipPosition(line=end_line + 1, character=end_character + 1)
    return ScipRange(start=start, end=end)


def _read_key(reader: _Reader) -> tuple[int, int]:
    key = _read_varint(reader)
    field_number = key >> 3
    wire_type = key & 0x07
    if field_number <= 0 or field_number > (1 << 29) - 1:
        raise ScipDecodeError("invalid protobuf field number")
    if wire_type not in {0, 1, 2, 3, 4, 5}:
        raise ScipDecodeError("invalid protobuf wire type")
    return field_number, wire_type


def _read_varint(reader: _Reader) -> int:
    result = 0
    for shift in range(0, 70, 7):
        byte = reader.read_byte()
        if shift == 63 and byte > 1:
            raise ScipDecodeError("protobuf varint overflows uint64")
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result
    raise ScipDecodeError("protobuf varint is too long")


def _read_bounded_int(reader: _Reader, maximum: int) -> int:
    value = _read_varint(reader)
    if value > maximum:
        raise ScipDecodeError("SCIP integer exceeds its bounded range")
    return value


def _skip_field(
    reader: _Reader,
    field_number: int,
    wire_type: int,
    limits: ScipDecodeLimits,
    budget: _DecodeBudget,
    *,
    depth: int,
) -> None:
    if depth > limits.max_depth:
        raise ScipDecodeError("unknown protobuf group exceeds nesting limit")
    if wire_type == 0:
        _read_varint(reader)
    elif wire_type == 1:
        reader.skip(8)
    elif wire_type == 2:
        length = _read_varint(reader)
        if length > limits.max_message_bytes:
            raise ScipDecodeError("unknown protobuf field exceeds message byte limit")
        reader.skip(length)
    elif wire_type == 5:
        reader.skip(4)
    elif wire_type == 3:
        fields = 0
        while True:
            if reader.eof:
                raise ScipDecodeError("truncated protobuf group")
            nested_number, nested_wire = _read_key(reader)
            budget.field()
            fields += 1
            if fields > limits.max_fields_per_message:
                raise ScipDecodeError("unknown protobuf group exceeds field limit")
            if nested_wire == 4:
                if nested_number != field_number:
                    raise ScipDecodeError("protobuf group end field does not match")
                break
            _skip_field(
                reader,
                nested_number,
                nested_wire,
                limits,
                budget,
                depth=depth + 1,
            )
    else:
        raise ScipDecodeError("unexpected protobuf end-group")


def _bounded_subprocess(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: Mapping[str, str],
    output_path: Path,
    policy: ScipExecutionPolicy,
) -> ScipExecutionOutcome:
    output = _CombinedOutput(policy.output_limit_bytes)

    def apply_limits() -> None:
        if resource is None:
            return
        resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds))
        resource.setrlimit(
            resource.RLIMIT_AS,
            (policy.memory_limit_bytes, policy.memory_limit_bytes),
        )

    process = subprocess.Popen(  # noqa: S603 - exact argv is operator allowlisted.
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        start_new_session=True,
        preexec_fn=apply_limits if os.name == "posix" else None,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_thread = threading.Thread(
        target=output.drain,
        args=("stdout", process.stdout),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=output.drain,
        args=("stderr", process.stderr),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + policy.timeout_seconds
    timed_out = False
    artifact_limited = False
    while process.poll() is None:
        if output.exceeded or _output_artifact_exceeds(
            output_path,
            policy.index_limit_bytes,
        ):
            artifact_limited = True
            _terminate_process_group(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate_process_group(process)
            break
        time.sleep(0.01)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    return ScipExecutionOutcome(
        returncode=process.returncode,
        stdout=output.stdout,
        stderr=output.stderr,
        timed_out=timed_out,
        output_limited=output.exceeded or artifact_limited,
    )


class _CombinedOutput:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._remaining = limit
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._lock = threading.Lock()
        self.exceeded = False

    @property
    def stdout(self) -> bytes:
        return bytes(self._stdout)

    @property
    def stderr(self) -> bytes:
        return bytes(self._stderr)

    def drain(self, name: Literal["stdout", "stderr"], stream: BinaryIO) -> None:
        target = self._stdout if name == "stdout" else self._stderr
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return
            with self._lock:
                take = min(len(chunk), self._remaining)
                target.extend(chunk[:take])
                self._remaining -= take
                if take < len(chunk):
                    self.exceeded = True


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    with suppress(OSError):
        process.kill()


def _output_artifact_exceeds(path: Path, maximum: int) -> bool:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return (
        not stat.S_ISREG(file_stat.st_mode)
        or stat.S_ISLNK(file_stat.st_mode)
        or file_stat.st_size > maximum
    )


def _sha256_file(path: Path, maximum: int) -> str:
    hasher = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            count += len(chunk)
            if count > maximum:
                raise ScipDecodeError("executable exceeds digest byte limit")
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_bounded_file(path: Path, maximum: int) -> bytes:
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise ScipDecodeError("index artifact is unavailable") from error
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or stat.S_ISLNK(file_stat.st_mode)
        or file_stat.st_size > maximum
    ):
        raise ScipDecodeError("index artifact exceeds runner limit")
    with path.open("rb") as stream:
        payload = stream.read(maximum + 1)
    if len(payload) > maximum:
        raise ScipDecodeError("index artifact exceeds runner limit")
    return payload


def _artifact(
    name: Literal["stdout", "stderr"],
    content: bytes,
    truncated: bool,
) -> ScipBoundedArtifact:
    return ScipBoundedArtifact(
        name=name,
        content=content,
        truncated=truncated,
        content_sha256=hashlib.sha256(content).hexdigest(),
    )


def _cap_bytes(value: bytes, limit: int) -> tuple[bytes, bool]:
    if limit <= 0:
        return b"", bool(value)
    return value[:limit], len(value) > limit


def _valid_execution_outcome(value: object) -> bool:
    return (
        isinstance(value, ScipExecutionOutcome)
        and (value.returncode is None or type(value.returncode) is int)
        and type(value.stdout) is bytes
        and type(value.stderr) is bytes
        and type(value.timed_out) is bool
        and type(value.output_limited) is bool
        and (value.index_bytes is None or type(value.index_bytes) is bytes)
    )


def _validate_container_argv_template(pin: ScipCommandPin) -> None:
    argv = pin.argv_template
    configured_runtime = Path(argv[0])
    runtime_name = configured_runtime.name.casefold()
    if not configured_runtime.is_absolute():
        raise ValueError("container runtime path must be absolute")
    if _UNSAFE_EXECUTION_PATH.search(str(configured_runtime)):
        raise ValueError("container runtime path contains unsafe characters")
    if runtime_name in _HARD_DENY_EXECUTABLES or runtime_name not in _CONTAINER_RUNTIMES:
        raise ValueError("hard deny permits only docker or podman container runtimes")
    if pin.tool_name != "scip-python":
        raise ValueError("tool_name must exactly identify scip-python")
    if not _SAFE_CONTAINER_VALUE.fullmatch(pin.tool_version):
        raise ValueError("tool_version contains unsupported characters")

    expected_prefix = (
        argv[0],
        "run",
        "--rm",
        "--name",
        "{container_name}",
        "--network",
        "none",
        "--read-only",
        "--cpus",
        "{cpus}",
        "--memory",
        "{memory}",
        "--pids-limit",
        "{pids}",
        "--security-opt",
        "no-new-privileges",
        "--mount",
        "type=bind,src={repo},dst=/workspace,readonly",
        "--mount",
        "type=bind,src={output},dst=/output",
    )
    if argv[: len(expected_prefix)] != expected_prefix:
        raise ValueError("container argv does not match the mandatory isolation grammar")
    if len(argv) <= len(expected_prefix):
        raise ValueError("container argv is missing its immutable image")
    image = argv[len(expected_prefix)]
    if not _CONTAINER_IMAGE.fullmatch(image):
        raise ValueError("container image must use an immutable sha256 digest")
    if image.rsplit("@", 1)[1] != pin.container_digest:
        raise ValueError("container image digest does not match the approved pin")

    command = argv[len(expected_prefix) + 1 :]
    required_command = (
        "scip-python",
        "index",
        "--output",
        "/output/index.scip",
    )
    if command[: len(required_command)] != required_command:
        raise ValueError("container command must be the fixed scip-python index grammar")
    if not command or command[-1] != "/workspace":
        raise ValueError("scip-python repository target must be /workspace")
    optional = command[len(required_command) : -1]
    if len(optional) % 2:
        raise ValueError("scip-python optional flags require bounded values")
    allowed_flags = (
        "--project-name",
        "--project-version",
        "--environment",
    )
    previous = -1
    for index in range(0, len(optional), 2):
        flag, value = optional[index : index + 2]
        try:
            order = allowed_flags.index(flag)
        except ValueError as error:
            raise ValueError("scip-python option is not in the fixed grammar") from error
        if order <= previous:
            raise ValueError("scip-python optional flags must be unique and ordered")
        if not _SAFE_CONTAINER_VALUE.fullmatch(value):
            raise ValueError("scip-python option value is unsafe")
        previous = order

    placeholders = {
        "{container_name}",
        "{cpus}",
        "{memory}",
        "{pids}",
        "{repo}",
        "{output}",
    }
    for value in argv:
        remaining = value
        for placeholder in placeholders:
            remaining = remaining.replace(placeholder, "")
        if "{" in remaining or "}" in remaining:
            raise ValueError("container argv contains an unsupported placeholder")
        if value.startswith("@") or value in {
            "-c",
            "--eval",
            "eval",
            "exec",
            "install",
        }:
            raise ValueError("container argv contains hard-denied execution semantics")


def _validated_container_runtime(
    pin: ScipCommandPin,
    *,
    repo_root: Path,
) -> str:
    configured = Path(pin.argv_template[0])
    try:
        configured_stat = configured.lstat()
    except OSError as error:
        raise _RunnerRejection("indexer_unavailable") from error
    if stat.S_ISLNK(configured_stat.st_mode) or not stat.S_ISREG(configured_stat.st_mode):
        raise _RunnerRejection("runtime_file_unsafe")
    if configured_stat.st_mode & stat.S_IWOTH:
        raise _RunnerRejection("runtime_file_unsafe")
    if not os.access(configured, os.X_OK):
        raise _RunnerRejection("runtime_file_unsafe")
    try:
        resolved = configured.resolve(strict=True)
    except OSError as error:
        raise _RunnerRejection("indexer_unavailable") from error
    if resolved != configured:
        raise _RunnerRejection("runtime_file_unsafe")
    if resolved.name.casefold() not in _CONTAINER_RUNTIMES:
        raise _RunnerRejection("runtime_kind_denied")
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise _RunnerRejection("runtime_inside_repository")
    try:
        actual_digest = _sha256_file(resolved, 512 * 1024 * 1024)
    except (OSError, ScipDecodeError) as error:
        raise _RunnerRejection("executable_digest_unavailable") from error
    if actual_digest != pin.executable_sha256:
        raise _RunnerRejection("executable_digest_mismatch")
    return str(resolved)


def _render_container_argv(
    pin: ScipCommandPin,
    *,
    executable: str,
    repo_root: Path,
    output_root: Path,
    container_name: str,
    policy: ScipExecutionPolicy,
) -> tuple[str, ...]:
    if "," in str(repo_root) or _UNSAFE_EXECUTION_PATH.search(str(repo_root)):
        raise _RunnerRejection("repository_path_unsupported")
    replacements = {
        "{container_name}": container_name,
        "{cpus}": f"{policy.container_cpus:g}",
        "{memory}": str(policy.memory_limit_bytes),
        "{pids}": str(policy.container_pids_limit),
        "{repo}": str(repo_root),
        "{output}": str(output_root),
    }
    rendered: list[str] = []
    for index, value in enumerate(pin.argv_template):
        item = executable if index == 0 else value
        for placeholder, replacement in replacements.items():
            item = item.replace(placeholder, replacement)
        rendered.append(item)
    return tuple(rendered)


def _scip_provenance(
    decoded: ScipDecodedIndex,
    *,
    raw_object_id: str,
    source_name: str,
) -> ScipProvenance:
    raw = ScipRawObject(
        raw_object_id=raw_object_id or f"raw://scip/sha256:{decoded.content_sha256}",
        content_sha256=decoded.content_sha256,
        byte_size=decoded.byte_size,
        source_name=_bounded_display(source_name),
    )
    return ScipProvenance(
        derivation="scip",
        consumer_version=SCIP_CONSUMER_VERSION,
        protocol_version=decoded.metadata.protocol_version,
        indexer_name=decoded.metadata.tool_name,
        indexer_version=decoded.metadata.tool_version,
        raw_object=raw,
    )


def _resolver_failure_result(
    decoded: ScipDecodedIndex,
    *,
    raw_object_id: str,
    source_name: str,
) -> ScipConsumeResult:
    return ScipConsumeResult(
        status=ScipStatus.PARTIAL,
        semantic_ready=False,
        documents=(),
        occurrences=(),
        external_symbols=(),
        relationships=(),
        diagnostics=(
            ScipDiagnostic(
                code="resolver_failure",
                message="SCIP resolver failed; semantic output was discarded",
            ),
        ),
        provenance=_scip_provenance(
            decoded,
            raw_object_id=raw_object_id,
            source_name=source_name,
        ),
        fallback=ScipFallbackTrace(
            attempted=True,
            engine="tree-sitter",
            outcome="delegated",
            reason="resolver_failure",
        ),
    )


def _partial_result(reason: str, *, detail: str = "") -> ScipConsumeResult:
    message = f"SCIP unavailable; Tree-sitter fallback delegated: {reason}"
    if detail:
        message = f"{message} ({detail})"
    return ScipConsumeResult(
        status=ScipStatus.PARTIAL,
        semantic_ready=False,
        documents=(),
        occurrences=(),
        external_symbols=(),
        relationships=(),
        diagnostics=(
            ScipDiagnostic(
                code=reason,
                message=message,
            ),
        ),
        provenance=None,
        fallback=ScipFallbackTrace(
            attempted=True,
            engine="tree-sitter",
            outcome="delegated",
            reason=reason,
        ),
    )


def _validate_length(length: int, maximum: int) -> None:
    if isinstance(length, bool) or not isinstance(length, int) or length < 0:
        raise ScipDecodeError("invalid protobuf length")
    if length > maximum:
        raise ScipDecodeError("protobuf length exceeds bounded input")


def _require_text(value: str, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{name} contains a control character")
    return value


def _is_local_symbol(symbol: str) -> bool:
    return symbol.startswith(_LOCAL_SYMBOL_PREFIX)


def _scip_descriptor_names(symbol: str, relative_path: str) -> frozenset[str]:
    """Extract decoded SCIP descriptors only from the exact document suffix."""

    decoded = unquote(symbol)
    marker = relative_path + "/"
    offset = decoded.find(marker)
    if offset < 0:
        return frozenset()
    descriptor = decoded[offset + len(marker) :]
    return frozenset(
        match.group(0) for match in re.finditer(r"[A-Za-z_$][A-Za-z0-9_$]*", descriptor)
    )


def _scip_descriptor_path(
    symbol: str,
    indexed_paths: Mapping[str, object],
) -> str | None:
    """Return the one exact indexed definition path encoded by a SCIP symbol."""

    decoded = unquote(symbol)
    parts = decoded.split(" ", 4)
    if len(parts) != 5:
        return None
    descriptor = parts[4]
    matches = tuple(path for path in indexed_paths if descriptor.startswith(path + "/"))
    return matches[0] if len(matches) == 1 else None


def _safe_error(error: Exception) -> str:
    return _bounded_display(str(error) or type(error).__name__, limit=160)


def _bounded_display(value: str, *, limit: int = 256) -> str:
    cleaned = "".join(
        character if not unicodedata.category(character).startswith("C") else "\ufffd"
        for character in value
    )
    return cleaned[:limit]


def _occurrence_key(
    item: ScipOccurrence,
) -> tuple[int, int, int, int, str, int]:
    return (
        item.source_range.start.line,
        item.source_range.start.character,
        item.source_range.end.line,
        item.source_range.end.character,
        item.symbol,
        item.symbol_roles,
    )


def _linked_occurrence_key(
    item: ScipLinkedOccurrence,
) -> tuple[str, int, int, int, int, str, int]:
    occurrence = item.occurrence
    return (occurrence.relative_path, *_occurrence_key(occurrence))


def _symbol_key(
    item: ScipSymbolInformation,
) -> tuple[str, str, int, bool]:
    return (item.symbol, item.display_name, item.kind, item.is_external)


def _relationship_key(
    item: ScipLinkedRelationship,
) -> tuple[str, str, bool, bool, bool, bool]:
    relation = item.relationship
    return (
        item.source_symbol,
        item.target_symbol,
        relation.is_reference,
        relation.is_implementation,
        relation.is_type_definition,
        relation.is_definition,
    )


def _diagnostic_key(
    item: ScipDiagnostic,
) -> tuple[str, str, str, tuple[str, ...], str]:
    return (
        item.code,
        item.relative_path,
        item.symbol,
        item.candidate_ids,
        item.message,
    )


__all__ = [
    "SCIP_CONSUMER_VERSION",
    "SafeScipRunner",
    "ScipBoundedArtifact",
    "ScipCommandExecutor",
    "ScipCommandPin",
    "ScipConsumeResult",
    "ScipConsumer",
    "ScipDecodeError",
    "ScipDecodeLimits",
    "ScipDecodedIndex",
    "ScipDiagnostic",
    "ScipDocument",
    "ScipEntityRef",
    "ScipExecutionOutcome",
    "ScipExecutionPolicy",
    "ScipFallbackTrace",
    "ScipIngestionResolver",
    "ScipLink",
    "ScipLinkedDocument",
    "ScipLinkedOccurrence",
    "ScipLinkedRelationship",
    "ScipLinkStatus",
    "ScipMetadata",
    "ScipNetworkIsolation",
    "ScipOccurrence",
    "ScipOccurrenceKind",
    "ScipPosition",
    "ScipProtobufDecoder",
    "ScipProvenance",
    "ScipRange",
    "ScipRawObject",
    "ScipReadOnlyResolver",
    "ScipRelationship",
    "ScipResolverFailure",
    "ScipRunnerResult",
    "ScipScope",
    "ScipStatus",
    "ScipSymbolInformation",
]
