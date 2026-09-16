"""Default-off raw V2 dual-write and shadow control seam.

The module is transport and storage neutral.  It does not mount application
runtime, open a database, or make a non-default policy authoritative.  A
caller may load non-default policy bytes only when an out-of-band expected
digest and reviewed revision are already known.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from .contracts import (
    RawV2ContractError,
    RawV2Reason,
    SourceDomain,
    canonical_json_bytes,
    exact_bytes_sha256,
    normalize_portable_id,
    strict_json_loads,
    validate_sha256_digest,
)

RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION = "raw-v2-runtime-control-policy-v1"
RAW_V2_RUNTIME_CONTROL_VERSION = "raw-v2-runtime-control-v1"
RAW_V2_RUNTIME_OBSERVATION_SCHEMA_VERSION = "raw-v2-runtime-observation-v1"

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_POLICY_KEYS = frozenset(("reviewed_revision", "rules", "schema_version"))
_RULE_KEYS = frozenset(("intent", "project_id", "read_mode", "source_domain", "write_mode"))
_MAX_POLICY_BYTES = 64 * 1024
_MAX_SCOPE_RULES = 256
_POLICY_AUTHORITY = object()


class RawV2RuntimeControlErrorCode(StrEnum):
    POLICY_NOT_CANONICAL = "policy_not_canonical"
    POLICY_DIGEST_MISMATCH = "policy_digest_mismatch"
    REVIEWED_REVISION_MISMATCH = "reviewed_revision_mismatch"
    POLICY_SCHEMA_MISMATCH = "policy_schema_mismatch"
    POLICY_SCOPE_INVALID = "policy_scope_invalid"
    POLICY_SCOPE_DUPLICATE = "policy_scope_duplicate"
    POLICY_MODE_INVALID = "policy_mode_invalid"
    CUTOVER_NOT_AUTHORIZED = "cutover_not_authorized"
    EXECUTION_CONTRACT_INVALID = "execution_contract_invalid"


class RawV2RuntimeControlError(ValueError):
    """Typed control-plane rejection without policy or payload echo."""

    def __init__(self, code: RawV2RuntimeControlErrorCode) -> None:
        if type(code) is not RawV2RuntimeControlErrorCode:
            raise TypeError("code must be an exact RawV2RuntimeControlErrorCode")
        self.code = code
        super().__init__(code.value)


def _fail(code: RawV2RuntimeControlErrorCode) -> None:
    raise RawV2RuntimeControlError(code)


class RawV2WriteMode(StrEnum):
    V1_ONLY = "v1-only"
    DUAL_WRITE_DARK = "dual-write-dark"


class RawV2ReadMode(StrEnum):
    V1 = "v1"
    V2_SHADOW = "v2-shadow"
    V2_REQUIRED = "v2-required"


class RawV2Operation(StrEnum):
    WRITE = "write"
    READ = "read"


class RawV2ObservationKind(StrEnum):
    V1_ONLY = "v1_only"
    DUAL_WRITE_MATCH = "dual_write_match"
    DUAL_WRITE_MISMATCH = "dual_write_mismatch"
    V2_WRITE_FAILED = "v2_write_failed"
    SHADOW_MATCH = "shadow_match"
    SHADOW_MISMATCH = "shadow_mismatch"
    V2_READ_FAILED = "v2_read_failed"


class RawV2MismatchCode(StrEnum):
    ACL_PARTITION = "acl_partition_mismatch"
    BINDING_AUTHORITY = "binding_authority_mismatch"
    CONTENT_DIGEST = "content_digest_mismatch"
    OBJECT_AUTHORITY = "object_authority_mismatch"
    REASON = "reason_mismatch"
    SOURCE_VERSION = "source_version_mismatch"
    STATE = "state_mismatch"


class RawV2Severity(StrEnum):
    INFO = "INFO"
    P0 = "P0"
    P1 = "P1"


class RawV2Qualification(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    PASS = "PASS"
    BLOCKED = "BLOCKED"


class RawV2AuthorityState(StrEnum):
    ACTIVE = "active"
    REFERENCE_ONLY = "reference_only"
    QUARANTINED = "quarantined"
    TOMBSTONED = "tombstoned"
    CORRUPT = "corrupt"
    UNAVAILABLE = "unavailable"


def _domain_digest(domain: str, value: object, *, allow_none: bool = False) -> str:
    preimage = domain.encode("ascii") + b"\x00" + canonical_json_bytes(value, allow_none=allow_none)
    return "sha256:" + hashlib.sha256(preimage).hexdigest()


def _canonical_scope_text(value: str, *, label: str) -> str:
    try:
        normalized = normalize_portable_id(value, label=label)
    except RawV2ContractError:
        _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
    if "*" in normalized:
        _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
    return normalized


@dataclass(frozen=True, slots=True)
class RawV2RuntimeScope:
    project_id: str
    source_domain: SourceDomain
    intent: str

    def __post_init__(self) -> None:
        if type(self.source_domain) is not SourceDomain:
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
        object.__setattr__(
            self,
            "project_id",
            _canonical_scope_text(self.project_id, label="runtime project ID"),
        )
        object.__setattr__(
            self,
            "intent",
            _canonical_scope_text(self.intent, label="runtime intent"),
        )

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.project_id, self.source_domain.value, self.intent)

    def canonical_value(self) -> dict[str, str]:
        return {
            "intent": self.intent,
            "project_id": self.project_id,
            "source_domain": self.source_domain.value,
        }

    @property
    def scope_sha256(self) -> str:
        return _domain_digest("raw-v2-runtime-scope-v1", self.canonical_value())


@dataclass(frozen=True, slots=True)
class RawV2ExecutionModes:
    write_mode: RawV2WriteMode
    read_mode: RawV2ReadMode

    def __post_init__(self) -> None:
        if type(self.write_mode) is not RawV2WriteMode:
            _fail(RawV2RuntimeControlErrorCode.POLICY_MODE_INVALID)
        if type(self.read_mode) is not RawV2ReadMode:
            _fail(RawV2RuntimeControlErrorCode.POLICY_MODE_INVALID)
        if self.read_mode is RawV2ReadMode.V2_REQUIRED:
            _fail(RawV2RuntimeControlErrorCode.CUTOVER_NOT_AUTHORIZED)


_DEFAULT_MODES = RawV2ExecutionModes(
    write_mode=RawV2WriteMode.V1_ONLY,
    read_mode=RawV2ReadMode.V1,
)


@dataclass(frozen=True, slots=True)
class RawV2ScopeRule:
    scope: RawV2RuntimeScope
    modes: RawV2ExecutionModes

    def __post_init__(self) -> None:
        if type(self.scope) is not RawV2RuntimeScope:
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
        if type(self.modes) is not RawV2ExecutionModes:
            _fail(RawV2RuntimeControlErrorCode.POLICY_MODE_INVALID)
        if self.modes == _DEFAULT_MODES:
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.scope.key

    def canonical_value(self) -> dict[str, str]:
        return {
            **self.scope.canonical_value(),
            "read_mode": self.modes.read_mode.value,
            "write_mode": self.modes.write_mode.value,
        }


@dataclass(frozen=True, slots=True, init=False)
class RawV2RuntimePolicy:
    reviewed_revision: str | None
    rules: tuple[RawV2ScopeRule, ...]
    content_sha256: str

    def __init__(
        self,
        *,
        _authority: object,
        reviewed_revision: str | None,
        rules: tuple[RawV2ScopeRule, ...],
        content_sha256: str,
    ) -> None:
        if _authority is not _POLICY_AUTHORITY:
            raise TypeError("runtime policy must be created by a reviewed loader or default()")
        if type(rules) is not tuple or any(type(rule) is not RawV2ScopeRule for rule in rules):
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCHEMA_MISMATCH)
        if len(rules) > _MAX_SCOPE_RULES:
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
        keys = tuple(rule.key for rule in rules)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
        if rules:
            if (
                type(reviewed_revision) is not str
                or _REVISION_RE.fullmatch(reviewed_revision) is None
            ):
                _fail(RawV2RuntimeControlErrorCode.REVIEWED_REVISION_MISMATCH)
        elif reviewed_revision is not None:
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
        try:
            validate_sha256_digest(content_sha256)
        except RawV2ContractError:
            _fail(RawV2RuntimeControlErrorCode.POLICY_DIGEST_MISMATCH)
        object.__setattr__(self, "reviewed_revision", reviewed_revision)
        object.__setattr__(self, "rules", rules)
        object.__setattr__(self, "content_sha256", content_sha256)

    @classmethod
    def default(cls) -> RawV2RuntimePolicy:
        value = {
            "reviewed_revision": None,
            "rules": [],
            "schema_version": RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION,
        }
        return cls(
            _authority=_POLICY_AUTHORITY,
            reviewed_revision=None,
            rules=(),
            content_sha256=exact_bytes_sha256(canonical_json_bytes(value, allow_none=True)),
        )

    def modes_for(self, scope: RawV2RuntimeScope) -> RawV2ExecutionModes:
        if type(scope) is not RawV2RuntimeScope:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        for rule in self.rules:
            if rule.key == scope.key:
                return rule.modes
        return _DEFAULT_MODES


def _exact_mapping(value: object, keys: frozenset[str]) -> Mapping[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        _fail(RawV2RuntimeControlErrorCode.POLICY_SCHEMA_MISMATCH)
    return value


def _read_mode(value: object) -> RawV2ReadMode:
    if type(value) is not str:
        _fail(RawV2RuntimeControlErrorCode.POLICY_MODE_INVALID)
    try:
        mode = RawV2ReadMode(value)
    except ValueError:
        _fail(RawV2RuntimeControlErrorCode.POLICY_MODE_INVALID)
    if mode is RawV2ReadMode.V2_REQUIRED:
        _fail(RawV2RuntimeControlErrorCode.CUTOVER_NOT_AUTHORIZED)
    return mode


def _write_mode(value: object) -> RawV2WriteMode:
    if type(value) is not str:
        _fail(RawV2RuntimeControlErrorCode.POLICY_MODE_INVALID)
    try:
        return RawV2WriteMode(value)
    except ValueError:
        _fail(RawV2RuntimeControlErrorCode.POLICY_MODE_INVALID)


def load_reviewed_runtime_policy(
    payload: bytes,
    *,
    expected_content_sha256: str,
    expected_revision: str,
) -> RawV2RuntimePolicy:
    """Load exact canonical policy bytes bound to trusted external expectations."""

    if type(payload) is not bytes or not payload or len(payload) > _MAX_POLICY_BYTES:
        _fail(RawV2RuntimeControlErrorCode.POLICY_NOT_CANONICAL)
    try:
        validate_sha256_digest(expected_content_sha256)
    except RawV2ContractError:
        _fail(RawV2RuntimeControlErrorCode.POLICY_DIGEST_MISMATCH)
    if exact_bytes_sha256(payload) != expected_content_sha256:
        _fail(RawV2RuntimeControlErrorCode.POLICY_DIGEST_MISMATCH)
    if type(expected_revision) is not str or _REVISION_RE.fullmatch(expected_revision) is None:
        _fail(RawV2RuntimeControlErrorCode.REVIEWED_REVISION_MISMATCH)
    try:
        parsed = strict_json_loads(payload)
        if canonical_json_bytes(parsed) != payload:
            _fail(RawV2RuntimeControlErrorCode.POLICY_NOT_CANONICAL)
    except RawV2RuntimeControlError:
        raise
    except RawV2ContractError:
        _fail(RawV2RuntimeControlErrorCode.POLICY_NOT_CANONICAL)
    root = _exact_mapping(parsed, _POLICY_KEYS)
    if root["schema_version"] != RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION:
        _fail(RawV2RuntimeControlErrorCode.POLICY_SCHEMA_MISMATCH)
    reviewed_revision = root["reviewed_revision"]
    if (
        type(reviewed_revision) is not str
        or _REVISION_RE.fullmatch(reviewed_revision) is None
        or reviewed_revision != expected_revision
    ):
        _fail(RawV2RuntimeControlErrorCode.REVIEWED_REVISION_MISMATCH)
    raw_rules = root["rules"]
    if type(raw_rules) is not list or not raw_rules or len(raw_rules) > _MAX_SCOPE_RULES:
        _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)

    rules: list[RawV2ScopeRule] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_rule in raw_rules:
        rule_value = _exact_mapping(raw_rule, _RULE_KEYS)
        source_value = rule_value["source_domain"]
        if type(source_value) is not str:
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
        try:
            source_domain = SourceDomain(source_value)
        except ValueError:
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
        try:
            scope = RawV2RuntimeScope(
                project_id=rule_value["project_id"],
                source_domain=source_domain,
                intent=rule_value["intent"],
            )
        except (RawV2ContractError, TypeError):
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
        if scope.key in seen:
            _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_DUPLICATE)
        seen.add(scope.key)
        rules.append(
            RawV2ScopeRule(
                scope=scope,
                modes=RawV2ExecutionModes(
                    write_mode=_write_mode(rule_value["write_mode"]),
                    read_mode=_read_mode(rule_value["read_mode"]),
                ),
            )
        )
    if tuple(rule.key for rule in rules) != tuple(sorted(rule.key for rule in rules)):
        _fail(RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID)
    return RawV2RuntimePolicy(
        _authority=_POLICY_AUTHORITY,
        reviewed_revision=reviewed_revision,
        rules=tuple(rules),
        content_sha256=expected_content_sha256,
    )


@dataclass(frozen=True, slots=True)
class RawV2ShadowProjection:
    object_authority_sha256: str | None
    binding_authority_sha256: str | None
    raw_content_sha256: str | None
    state: RawV2AuthorityState
    source_version_sha256: str | None
    acl_partition_sha256: str | None
    reason: RawV2Reason | None

    def __post_init__(self) -> None:
        if type(self.state) is not RawV2AuthorityState:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        if self.reason is not None and type(self.reason) is not RawV2Reason:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        for value in (
            self.object_authority_sha256,
            self.binding_authority_sha256,
            self.raw_content_sha256,
            self.source_version_sha256,
            self.acl_partition_sha256,
        ):
            if value is None:
                continue
            try:
                validate_sha256_digest(value)
            except RawV2ContractError:
                _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)

    def canonical_value(self) -> dict[str, object]:
        return {
            "acl_partition_sha256": self.acl_partition_sha256,
            "binding_authority_sha256": self.binding_authority_sha256,
            "object_authority_sha256": self.object_authority_sha256,
            "raw_content_sha256": self.raw_content_sha256,
            "reason": None if self.reason is None else self.reason.value,
            "source_version_sha256": self.source_version_sha256,
            "state": self.state.value,
        }

    @property
    def projection_sha256(self) -> str:
        return _domain_digest(
            "raw-v2-shadow-projection-v1",
            self.canonical_value(),
            allow_none=True,
        )


_MISMATCH_FIELDS: tuple[tuple[str, RawV2MismatchCode], ...] = (
    ("acl_partition_sha256", RawV2MismatchCode.ACL_PARTITION),
    ("binding_authority_sha256", RawV2MismatchCode.BINDING_AUTHORITY),
    ("raw_content_sha256", RawV2MismatchCode.CONTENT_DIGEST),
    ("object_authority_sha256", RawV2MismatchCode.OBJECT_AUTHORITY),
    ("reason", RawV2MismatchCode.REASON),
    ("source_version_sha256", RawV2MismatchCode.SOURCE_VERSION),
    ("state", RawV2MismatchCode.STATE),
)
_P0_MISMATCHES = frozenset(
    (
        RawV2MismatchCode.ACL_PARTITION,
        RawV2MismatchCode.SOURCE_VERSION,
        RawV2MismatchCode.STATE,
    )
)


def _compare(
    v1: RawV2ShadowProjection,
    v2: RawV2ShadowProjection,
) -> tuple[RawV2MismatchCode, ...]:
    mismatches = [
        code
        for field_name, code in _MISMATCH_FIELDS
        if getattr(v1, field_name) != getattr(v2, field_name)
    ]
    return tuple(sorted(mismatches, key=lambda item: item.value))


@dataclass(frozen=True, slots=True)
class RawV2RuntimeObservation:
    operation: RawV2Operation
    kind: RawV2ObservationKind
    policy_sha256: str
    scope_sha256: str
    v1_projection_sha256: str | None
    v2_projection_sha256: str | None
    mismatch_codes: tuple[RawV2MismatchCode, ...]
    severity: RawV2Severity
    qualification: RawV2Qualification

    def __post_init__(self) -> None:
        if type(self.operation) is not RawV2Operation:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        if type(self.kind) is not RawV2ObservationKind:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        if type(self.mismatch_codes) is not tuple or any(
            type(code) is not RawV2MismatchCode for code in self.mismatch_codes
        ):
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        if self.mismatch_codes != tuple(
            sorted(set(self.mismatch_codes), key=lambda item: item.value)
        ):
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        if type(self.severity) is not RawV2Severity:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        if type(self.qualification) is not RawV2Qualification:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        for value in (
            self.policy_sha256,
            self.scope_sha256,
            self.v1_projection_sha256,
            self.v2_projection_sha256,
        ):
            if value is None:
                continue
            try:
                validate_sha256_digest(value)
            except RawV2ContractError:
                _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        self._validate_shape()

    def _validate_shape(self) -> None:
        if self.kind is RawV2ObservationKind.V1_ONLY:
            if (
                self.v1_projection_sha256 is not None
                or self.v2_projection_sha256 is not None
                or self.mismatch_codes
                or self.severity is not RawV2Severity.INFO
                or self.qualification is not RawV2Qualification.NOT_EVALUATED
            ):
                _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
            return
        if self.kind in (
            RawV2ObservationKind.V2_WRITE_FAILED,
            RawV2ObservationKind.V2_READ_FAILED,
        ):
            if (
                self.mismatch_codes
                or self.severity is not RawV2Severity.P1
                or self.qualification is not RawV2Qualification.BLOCKED
            ):
                _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
            return
        if self.v1_projection_sha256 is None or self.v2_projection_sha256 is None:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        if self.kind in (
            RawV2ObservationKind.DUAL_WRITE_MATCH,
            RawV2ObservationKind.SHADOW_MATCH,
        ):
            if (
                self.mismatch_codes
                or self.severity is not RawV2Severity.INFO
                or self.qualification is not RawV2Qualification.PASS
            ):
                _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
            return
        if (
            not self.mismatch_codes
            or self.severity not in (RawV2Severity.P0, RawV2Severity.P1)
            or self.qualification is not RawV2Qualification.BLOCKED
        ):
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)

    def canonical_value(self) -> dict[str, object]:
        return {
            "control_version": RAW_V2_RUNTIME_CONTROL_VERSION,
            "kind": self.kind.value,
            "mismatch_codes": [code.value for code in self.mismatch_codes],
            "operation": self.operation.value,
            "policy_sha256": self.policy_sha256,
            "qualification": self.qualification.value,
            "schema_version": RAW_V2_RUNTIME_OBSERVATION_SCHEMA_VERSION,
            "scope_sha256": self.scope_sha256,
            "severity": self.severity.value,
            "v1_projection_sha256": self.v1_projection_sha256,
            "v2_projection_sha256": self.v2_projection_sha256,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_value(), allow_none=True)


@dataclass(frozen=True, slots=True)
class RawV2RuntimeResult[T]:
    product_result: T
    observation: RawV2RuntimeObservation

    def __post_init__(self) -> None:
        if type(self.observation) is not RawV2RuntimeObservation:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)


def _require_callable(value: object) -> None:
    if not callable(value):
        _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)


def _v1_only_observation(
    *,
    operation: RawV2Operation,
    policy: RawV2RuntimePolicy,
    scope: RawV2RuntimeScope,
) -> RawV2RuntimeObservation:
    return RawV2RuntimeObservation(
        operation=operation,
        kind=RawV2ObservationKind.V1_ONLY,
        policy_sha256=policy.content_sha256,
        scope_sha256=scope.scope_sha256,
        v1_projection_sha256=None,
        v2_projection_sha256=None,
        mismatch_codes=(),
        severity=RawV2Severity.INFO,
        qualification=RawV2Qualification.NOT_EVALUATED,
    )


def _failure_observation(
    *,
    operation: RawV2Operation,
    policy: RawV2RuntimePolicy,
    scope: RawV2RuntimeScope,
    kind: RawV2ObservationKind,
) -> RawV2RuntimeObservation:
    return RawV2RuntimeObservation(
        operation=operation,
        kind=kind,
        policy_sha256=policy.content_sha256,
        scope_sha256=scope.scope_sha256,
        v1_projection_sha256=None,
        v2_projection_sha256=None,
        mismatch_codes=(),
        severity=RawV2Severity.P1,
        qualification=RawV2Qualification.BLOCKED,
    )


def _comparison_observation(
    *,
    operation: RawV2Operation,
    policy: RawV2RuntimePolicy,
    scope: RawV2RuntimeScope,
    v1_projection: RawV2ShadowProjection,
    v2_projection: RawV2ShadowProjection,
    match_kind: RawV2ObservationKind,
    mismatch_kind: RawV2ObservationKind,
) -> RawV2RuntimeObservation:
    if (
        type(v1_projection) is not RawV2ShadowProjection
        or type(v2_projection) is not RawV2ShadowProjection
    ):
        _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
    mismatches = _compare(v1_projection, v2_projection)
    if mismatches:
        severity = (
            RawV2Severity.P0
            if any(code in _P0_MISMATCHES for code in mismatches)
            else RawV2Severity.P1
        )
        kind = mismatch_kind
        qualification = RawV2Qualification.BLOCKED
    else:
        severity = RawV2Severity.INFO
        kind = match_kind
        qualification = RawV2Qualification.PASS
    return RawV2RuntimeObservation(
        operation=operation,
        kind=kind,
        policy_sha256=policy.content_sha256,
        scope_sha256=scope.scope_sha256,
        v1_projection_sha256=v1_projection.projection_sha256,
        v2_projection_sha256=v2_projection.projection_sha256,
        mismatch_codes=mismatches,
        severity=severity,
        qualification=qualification,
    )


@dataclass(frozen=True, slots=True, init=False)
class RawV2RuntimeControl:
    """Execute a pre-resolved default-off dual-write/shadow policy."""

    _policy: RawV2RuntimePolicy

    def __init__(self, policy: RawV2RuntimePolicy) -> None:
        if type(policy) is not RawV2RuntimePolicy:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        object.__setattr__(self, "_policy", policy)

    @property
    def policy(self) -> RawV2RuntimePolicy:
        return self._policy

    def execute_write[T](
        self,
        *,
        scope: RawV2RuntimeScope,
        v1: Callable[[], T],
        v2: Callable[[], object],
        project_v1: Callable[[T], RawV2ShadowProjection],
        project_v2: Callable[[object], RawV2ShadowProjection],
    ) -> RawV2RuntimeResult[T]:
        self._validate_execution(scope, v1, v2, project_v1, project_v2)
        modes = self._policy.modes_for(scope)
        product_result = v1()
        if modes.write_mode is RawV2WriteMode.V1_ONLY:
            return RawV2RuntimeResult(
                product_result=product_result,
                observation=_v1_only_observation(
                    operation=RawV2Operation.WRITE,
                    policy=self._policy,
                    scope=scope,
                ),
            )
        try:
            v2_result = v2()
            v1_projection = project_v1(product_result)
            v2_projection = project_v2(v2_result)
            observation = _comparison_observation(
                operation=RawV2Operation.WRITE,
                policy=self._policy,
                scope=scope,
                v1_projection=v1_projection,
                v2_projection=v2_projection,
                match_kind=RawV2ObservationKind.DUAL_WRITE_MATCH,
                mismatch_kind=RawV2ObservationKind.DUAL_WRITE_MISMATCH,
            )
        except Exception:
            observation = _failure_observation(
                operation=RawV2Operation.WRITE,
                policy=self._policy,
                scope=scope,
                kind=RawV2ObservationKind.V2_WRITE_FAILED,
            )
        return RawV2RuntimeResult(product_result=product_result, observation=observation)

    def execute_read[T](
        self,
        *,
        scope: RawV2RuntimeScope,
        v1: Callable[[], T],
        v2: Callable[[], object],
        project_v1: Callable[[T], RawV2ShadowProjection],
        project_v2: Callable[[object], RawV2ShadowProjection],
    ) -> RawV2RuntimeResult[T]:
        self._validate_execution(scope, v1, v2, project_v1, project_v2)
        modes = self._policy.modes_for(scope)
        product_result = v1()
        if modes.read_mode is RawV2ReadMode.V1:
            return RawV2RuntimeResult(
                product_result=product_result,
                observation=_v1_only_observation(
                    operation=RawV2Operation.READ,
                    policy=self._policy,
                    scope=scope,
                ),
            )
        try:
            v2_result = v2()
            v1_projection = project_v1(product_result)
            v2_projection = project_v2(v2_result)
            observation = _comparison_observation(
                operation=RawV2Operation.READ,
                policy=self._policy,
                scope=scope,
                v1_projection=v1_projection,
                v2_projection=v2_projection,
                match_kind=RawV2ObservationKind.SHADOW_MATCH,
                mismatch_kind=RawV2ObservationKind.SHADOW_MISMATCH,
            )
        except Exception:
            observation = _failure_observation(
                operation=RawV2Operation.READ,
                policy=self._policy,
                scope=scope,
                kind=RawV2ObservationKind.V2_READ_FAILED,
            )
        return RawV2RuntimeResult(product_result=product_result, observation=observation)

    @staticmethod
    def _validate_execution(scope: object, *callables: object) -> None:
        if type(scope) is not RawV2RuntimeScope:
            _fail(RawV2RuntimeControlErrorCode.EXECUTION_CONTRACT_INVALID)
        for value in callables:
            _require_callable(value)


__all__ = [
    "RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION",
    "RAW_V2_RUNTIME_CONTROL_VERSION",
    "RAW_V2_RUNTIME_OBSERVATION_SCHEMA_VERSION",
    "RawV2AuthorityState",
    "RawV2ExecutionModes",
    "RawV2MismatchCode",
    "RawV2ObservationKind",
    "RawV2Operation",
    "RawV2Qualification",
    "RawV2ReadMode",
    "RawV2RuntimeControl",
    "RawV2RuntimeControlError",
    "RawV2RuntimeControlErrorCode",
    "RawV2RuntimeObservation",
    "RawV2RuntimePolicy",
    "RawV2RuntimeResult",
    "RawV2RuntimeScope",
    "RawV2ScopeRule",
    "RawV2Severity",
    "RawV2ShadowProjection",
    "RawV2WriteMode",
    "load_reviewed_runtime_policy",
]
