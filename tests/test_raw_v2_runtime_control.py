from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from evidence_rag.sources.v2.contracts import (
    RawV2Reason,
    SourceDomain,
    canonical_json_bytes,
    exact_bytes_sha256,
)
from evidence_rag.sources.v2.runtime_control import (
    RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION,
    RawV2AuthorityState,
    RawV2MismatchCode,
    RawV2ObservationKind,
    RawV2Qualification,
    RawV2ReadMode,
    RawV2RuntimeControl,
    RawV2RuntimeControlError,
    RawV2RuntimeControlErrorCode,
    RawV2RuntimePolicy,
    RawV2RuntimeScope,
    RawV2Severity,
    RawV2ShadowProjection,
    RawV2WriteMode,
    load_reviewed_runtime_policy,
)

REVISION = "ae5f4d508da9c0dbca5082c28ebad4e9071150c8"


def _sha(label: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _scope(
    *,
    project_id: str = "project-alpha",
    source_domain: SourceDomain = SourceDomain.CODE,
    intent: str = "ingest",
) -> RawV2RuntimeScope:
    return RawV2RuntimeScope(
        project_id=project_id,
        source_domain=source_domain,
        intent=intent,
    )


def _rule(
    *,
    project_id: str = "project-alpha",
    source_domain: str = "code",
    intent: str = "ingest",
    write_mode: str = "dual-write-dark",
    read_mode: str = "v2-shadow",
) -> dict[str, str]:
    return {
        "intent": intent,
        "project_id": project_id,
        "read_mode": read_mode,
        "source_domain": source_domain,
        "write_mode": write_mode,
    }


def _policy_bytes(*rules: dict[str, str], revision: str = REVISION) -> bytes:
    return canonical_json_bytes(
        {
            "reviewed_revision": revision,
            "rules": list(rules),
            "schema_version": RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION,
        }
    )


def _reviewed_policy(*rules: dict[str, str]) -> RawV2RuntimePolicy:
    payload = _policy_bytes(*rules)
    return load_reviewed_runtime_policy(
        payload,
        expected_content_sha256=exact_bytes_sha256(payload),
        expected_revision=REVISION,
    )


def _projection(**changes: Any) -> RawV2ShadowProjection:
    values: dict[str, Any] = {
        "object_authority_sha256": _sha("object"),
        "binding_authority_sha256": _sha("binding"),
        "raw_content_sha256": _sha("content"),
        "state": RawV2AuthorityState.ACTIVE,
        "source_version_sha256": _sha("version"),
        "acl_partition_sha256": _sha("acl"),
        "reason": None,
    }
    values.update(changes)
    return RawV2ShadowProjection(**values)


def test_default_policy_is_v1_only_and_execution_has_no_request_mode() -> None:
    policy = RawV2RuntimePolicy.default()
    scope = _scope()
    modes = policy.modes_for(scope)
    assert modes.write_mode is RawV2WriteMode.V1_ONLY
    assert modes.read_mode is RawV2ReadMode.V1
    assert policy.rules == ()
    assert "mode" not in inspect.signature(RawV2RuntimeControl.execute_write).parameters
    assert "mode" not in inspect.signature(RawV2RuntimeControl.execute_read).parameters

    sentinel = object()
    calls: list[str] = []
    control = RawV2RuntimeControl(policy)
    result = control.execute_write(
        scope=scope,
        v1=lambda: calls.append("v1") or sentinel,
        v2=lambda: calls.append("v2") or object(),
        project_v1=lambda _value: calls.append("project-v1") or _projection(),
        project_v2=lambda _value: calls.append("project-v2") or _projection(),
    )
    assert result.product_result is sentinel
    assert calls == ["v1"]
    assert result.observation.kind is RawV2ObservationKind.V1_ONLY
    assert result.observation.qualification is RawV2Qualification.NOT_EVALUATED

    read_result = control.execute_read(
        scope=scope,
        v1=lambda: calls.append("read-v1") or sentinel,
        v2=lambda: calls.append("read-v2") or object(),
        project_v1=lambda _value: calls.append("read-project-v1") or _projection(),
        project_v2=lambda _value: calls.append("read-project-v2") or _projection(),
    )
    assert read_result.product_result is sentinel
    assert calls == ["v1", "read-v1"]
    assert read_result.observation.kind is RawV2ObservationKind.V1_ONLY


def test_reviewed_policy_requires_exact_canonical_digest_revision_and_schema() -> None:
    payload = _policy_bytes(_rule())
    policy = load_reviewed_runtime_policy(
        payload,
        expected_content_sha256=exact_bytes_sha256(payload),
        expected_revision=REVISION,
    )
    modes = policy.modes_for(_scope())
    assert modes.write_mode is RawV2WriteMode.DUAL_WRITE_DARK
    assert modes.read_mode is RawV2ReadMode.V2_SHADOW
    assert policy.reviewed_revision == REVISION
    assert policy.content_sha256 == exact_bytes_sha256(payload)

    with pytest.raises(RawV2RuntimeControlError) as digest_error:
        load_reviewed_runtime_policy(
            payload,
            expected_content_sha256=_sha("wrong"),
            expected_revision=REVISION,
        )
    assert digest_error.value.code is RawV2RuntimeControlErrorCode.POLICY_DIGEST_MISMATCH

    with pytest.raises(RawV2RuntimeControlError) as revision_error:
        load_reviewed_runtime_policy(
            payload,
            expected_content_sha256=exact_bytes_sha256(payload),
            expected_revision="0" * 40,
        )
    assert revision_error.value.code is RawV2RuntimeControlErrorCode.REVIEWED_REVISION_MISMATCH

    bad_schema = canonical_json_bytes(
        {
            "reviewed_revision": REVISION,
            "rules": [_rule()],
            "schema_version": "raw-v2-runtime-control-policy-v0",
        }
    )
    with pytest.raises(RawV2RuntimeControlError) as schema_error:
        load_reviewed_runtime_policy(
            bad_schema,
            expected_content_sha256=exact_bytes_sha256(bad_schema),
            expected_revision=REVISION,
        )
    assert schema_error.value.code is RawV2RuntimeControlErrorCode.POLICY_SCHEMA_MISMATCH


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            b'{"reviewed_revision":"'
            + REVISION.encode()
            + b'","rules":[],"rules":[],"schema_version":"raw-v2-runtime-control-policy-v1"}',
            RawV2RuntimeControlErrorCode.POLICY_NOT_CANONICAL,
        ),
        (
            _policy_bytes(_rule()) + b"\n",
            RawV2RuntimeControlErrorCode.POLICY_NOT_CANONICAL,
        ),
        (
            canonical_json_bytes(
                {
                    "extra": True,
                    "reviewed_revision": REVISION,
                    "rules": [_rule()],
                    "schema_version": RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION,
                }
            ),
            RawV2RuntimeControlErrorCode.POLICY_SCHEMA_MISMATCH,
        ),
        (
            _policy_bytes(_rule(project_id="project-*")),
            RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID,
        ),
        (
            _policy_bytes(_rule(), _rule()),
            RawV2RuntimeControlErrorCode.POLICY_SCOPE_DUPLICATE,
        ),
        (
            _policy_bytes(_rule(read_mode="v2-required")),
            RawV2RuntimeControlErrorCode.CUTOVER_NOT_AUTHORIZED,
        ),
        (
            _policy_bytes(_rule(write_mode="v1-only", read_mode="v1")),
            RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID,
        ),
    ],
)
def test_policy_rejects_noncanonical_unreviewed_or_ineffective_scope(
    payload: bytes,
    code: RawV2RuntimeControlErrorCode,
) -> None:
    with pytest.raises(RawV2RuntimeControlError) as captured:
        load_reviewed_runtime_policy(
            payload,
            expected_content_sha256=exact_bytes_sha256(payload),
            expected_revision=REVISION,
        )
    assert captured.value.code is code


def test_policy_scope_order_and_matching_are_exact() -> None:
    first = _rule(project_id="project-alpha", source_domain="code", intent="ingest")
    second = _rule(
        project_id="project-beta",
        source_domain="document",
        intent="query",
        write_mode="v1-only",
    )
    policy = _reviewed_policy(first, second)
    assert policy.modes_for(_scope()).write_mode is RawV2WriteMode.DUAL_WRITE_DARK
    assert (
        policy.modes_for(
            _scope(
                project_id="project-beta",
                source_domain=SourceDomain.DOCUMENT,
                intent="query",
            )
        ).read_mode
        is RawV2ReadMode.V2_SHADOW
    )
    for miss in (
        _scope(project_id="project-alpha-child"),
        _scope(source_domain=SourceDomain.CODEX),
        _scope(intent="ingest-more"),
    ):
        assert policy.modes_for(miss).write_mode is RawV2WriteMode.V1_ONLY
        assert policy.modes_for(miss).read_mode is RawV2ReadMode.V1

    reversed_payload = _policy_bytes(second, first)
    with pytest.raises(RawV2RuntimeControlError) as captured:
        load_reviewed_runtime_policy(
            reversed_payload,
            expected_content_sha256=exact_bytes_sha256(reversed_payload),
            expected_revision=REVISION,
        )
    assert captured.value.code is RawV2RuntimeControlErrorCode.POLICY_SCOPE_INVALID


def test_dual_write_calls_v1_then_v2_once_and_preserves_v1_identity() -> None:
    control = RawV2RuntimeControl(_reviewed_policy(_rule(read_mode="v1")))
    sentinel = {"legacy": "unchanged"}
    calls: list[str] = []
    result = control.execute_write(
        scope=_scope(),
        v1=lambda: calls.append("v1") or sentinel,
        v2=lambda: calls.append("v2") or {"v2": "internal"},
        project_v1=lambda _value: calls.append("project-v1") or _projection(),
        project_v2=lambda _value: calls.append("project-v2") or _projection(),
    )
    assert result.product_result is sentinel
    assert calls == ["v1", "v2", "project-v1", "project-v2"]
    assert result.observation.kind is RawV2ObservationKind.DUAL_WRITE_MATCH
    assert result.observation.qualification is RawV2Qualification.PASS
    assert result.observation.mismatch_codes == ()


def test_dual_write_uses_the_same_typed_mismatch_contract() -> None:
    control = RawV2RuntimeControl(_reviewed_policy(_rule(read_mode="v1")))
    sentinel = object()
    result = control.execute_write(
        scope=_scope(),
        v1=lambda: sentinel,
        v2=lambda: object(),
        project_v1=lambda _value: _projection(),
        project_v2=lambda _value: _projection(
            acl_partition_sha256=_sha("different-acl"),
            raw_content_sha256=_sha("different-content"),
        ),
    )
    assert result.product_result is sentinel
    assert result.observation.kind is RawV2ObservationKind.DUAL_WRITE_MISMATCH
    assert result.observation.mismatch_codes == (
        RawV2MismatchCode.ACL_PARTITION,
        RawV2MismatchCode.CONTENT_DIGEST,
    )
    assert result.observation.severity is RawV2Severity.P0
    assert result.observation.qualification is RawV2Qualification.BLOCKED


def test_v1_failure_propagates_before_v2_and_v2_failure_is_a_visible_blocker() -> None:
    control = RawV2RuntimeControl(_reviewed_policy(_rule(read_mode="v1")))
    calls: list[str] = []

    def fail_v1() -> object:
        calls.append("v1")
        raise LookupError("legacy failure")

    with pytest.raises(LookupError, match="legacy failure"):
        control.execute_write(
            scope=_scope(),
            v1=fail_v1,
            v2=lambda: calls.append("v2"),
            project_v1=lambda _value: _projection(),
            project_v2=lambda _value: _projection(),
        )
    assert calls == ["v1"]

    sentinel = object()

    def fail_v2() -> object:
        calls.append("v2")
        raise RuntimeError("secret exception /private/path source://uri")

    private_control = RawV2RuntimeControl(
        _reviewed_policy(_rule(project_id="secret-project", read_mode="v1"))
    )
    result = private_control.execute_write(
        scope=_scope(project_id="secret-project"),
        v1=lambda: calls.append("v1-success") or sentinel,
        v2=fail_v2,
        project_v1=lambda _value: _projection(),
        project_v2=lambda _value: _projection(),
    )
    assert result.product_result is sentinel
    assert calls == ["v1", "v1-success", "v2"]
    assert result.observation.kind is RawV2ObservationKind.V2_WRITE_FAILED
    assert result.observation.qualification is RawV2Qualification.BLOCKED
    assert result.observation.severity is RawV2Severity.P1
    serialized = result.observation.canonical_bytes()
    for secret in (b"secret-project", b"secret exception", b"/private/path", b"source://uri"):
        assert secret not in serialized


def test_shadow_returns_exact_v1_result_and_reports_match() -> None:
    control = RawV2RuntimeControl(_reviewed_policy(_rule(write_mode="v1-only")))
    sentinel = ["legacy", "response"]
    calls: list[str] = []
    result = control.execute_read(
        scope=_scope(),
        v1=lambda: calls.append("v1") or sentinel,
        v2=lambda: calls.append("v2") or ("v2", "response"),
        project_v1=lambda _value: calls.append("project-v1") or _projection(),
        project_v2=lambda _value: calls.append("project-v2") or _projection(),
    )
    assert result.product_result is sentinel
    assert calls == ["v1", "v2", "project-v1", "project-v2"]
    assert result.observation.kind is RawV2ObservationKind.SHADOW_MATCH
    assert result.observation.qualification is RawV2Qualification.PASS
    assert result.observation.severity is RawV2Severity.INFO


@pytest.mark.parametrize(
    ("change", "expected_code", "expected_severity"),
    [
        (
            {"object_authority_sha256": _sha("other-object")},
            RawV2MismatchCode.OBJECT_AUTHORITY,
            RawV2Severity.P1,
        ),
        (
            {"binding_authority_sha256": _sha("other-binding")},
            RawV2MismatchCode.BINDING_AUTHORITY,
            RawV2Severity.P1,
        ),
        (
            {"raw_content_sha256": _sha("other-content")},
            RawV2MismatchCode.CONTENT_DIGEST,
            RawV2Severity.P1,
        ),
        (
            {"state": RawV2AuthorityState.TOMBSTONED},
            RawV2MismatchCode.STATE,
            RawV2Severity.P0,
        ),
        (
            {"source_version_sha256": _sha("other-version")},
            RawV2MismatchCode.SOURCE_VERSION,
            RawV2Severity.P0,
        ),
        (
            {"acl_partition_sha256": _sha("other-acl")},
            RawV2MismatchCode.ACL_PARTITION,
            RawV2Severity.P0,
        ),
        (
            {"reason": RawV2Reason.BINDING_NOT_FOUND},
            RawV2MismatchCode.REASON,
            RawV2Severity.P1,
        ),
    ],
)
def test_shadow_mismatch_codes_and_severity_are_frozen(
    change: dict[str, object],
    expected_code: RawV2MismatchCode,
    expected_severity: RawV2Severity,
) -> None:
    control = RawV2RuntimeControl(_reviewed_policy(_rule(write_mode="v1-only")))
    result = control.execute_read(
        scope=_scope(),
        v1=lambda: object(),
        v2=lambda: object(),
        project_v1=lambda _value: _projection(),
        project_v2=lambda _value: _projection(**change),
    )
    assert result.observation.kind is RawV2ObservationKind.SHADOW_MISMATCH
    assert result.observation.mismatch_codes == (expected_code,)
    assert result.observation.severity is expected_severity
    assert result.observation.qualification is RawV2Qualification.BLOCKED


def test_multiple_mismatches_are_sorted_and_p0_dominates() -> None:
    control = RawV2RuntimeControl(_reviewed_policy(_rule(write_mode="v1-only")))
    result = control.execute_read(
        scope=_scope(),
        v1=lambda: object(),
        v2=lambda: object(),
        project_v1=lambda _value: _projection(),
        project_v2=lambda _value: _projection(
            state=RawV2AuthorityState.CORRUPT,
            object_authority_sha256=_sha("other-object"),
            reason=RawV2Reason.BLOB_CORRUPT,
        ),
    )
    assert result.observation.mismatch_codes == tuple(
        sorted(
            (
                RawV2MismatchCode.OBJECT_AUTHORITY,
                RawV2MismatchCode.REASON,
                RawV2MismatchCode.STATE,
            ),
            key=lambda item: item.value,
        )
    )
    assert result.observation.severity is RawV2Severity.P0


def test_shadow_v2_or_projection_failure_preserves_v1_and_hides_error() -> None:
    control = RawV2RuntimeControl(
        _reviewed_policy(_rule(project_id="private-project", write_mode="v1-only"))
    )
    sentinel = object()

    def failure() -> object:
        raise RuntimeError("private-project acl://secret /absolute/path")

    for failing_v2, failing_projection in (
        (failure, lambda _value: _projection()),
        (object, failure),
    ):
        result = control.execute_read(
            scope=_scope(project_id="private-project"),
            v1=lambda: sentinel,
            v2=failing_v2,
            project_v1=lambda _value: _projection(),
            project_v2=failing_projection,
        )
        assert result.product_result is sentinel
        assert result.observation.kind is RawV2ObservationKind.V2_READ_FAILED
        assert result.observation.qualification is RawV2Qualification.BLOCKED
        payload = result.observation.canonical_bytes()
        for secret in (b"private-project", b"acl://secret", b"/absolute/path"):
            assert secret not in payload


def test_observation_is_canonical_bounded_and_contains_only_reviewed_fields() -> None:
    control = RawV2RuntimeControl(
        _reviewed_policy(_rule(project_id="private-project", write_mode="v1-only"))
    )
    result = control.execute_read(
        scope=_scope(project_id="private-project"),
        v1=lambda: object(),
        v2=lambda: object(),
        project_v1=lambda _value: _projection(),
        project_v2=lambda _value: _projection(),
    )
    value = result.observation.canonical_value()
    assert set(value) == {
        "control_version",
        "kind",
        "mismatch_codes",
        "operation",
        "policy_sha256",
        "qualification",
        "schema_version",
        "scope_sha256",
        "severity",
        "v1_projection_sha256",
        "v2_projection_sha256",
    }
    assert result.observation.canonical_bytes() == canonical_json_bytes(value, allow_none=True)
    assert len(result.observation.canonical_bytes()) < 2048
    assert b"private-project" not in result.observation.canonical_bytes()


def test_policy_scope_projection_and_result_are_frozen() -> None:
    scope = _scope()
    policy = _reviewed_policy(_rule())
    projection = _projection()
    control = RawV2RuntimeControl(policy)
    result = control.execute_read(
        scope=scope,
        v1=lambda: object(),
        v2=lambda: object(),
        project_v1=lambda _value: projection,
        project_v2=lambda _value: projection,
    )
    for value, field, replacement in (
        (scope, "intent", "other"),
        (policy, "reviewed_revision", "0" * 40),
        (projection, "state", RawV2AuthorityState.CORRUPT),
        (result.observation, "severity", RawV2Severity.P0),
        (control, "_policy", RawV2RuntimePolicy.default()),
    ):
        with pytest.raises((FrozenInstanceError, AttributeError)):
            setattr(value, field, replacement)
    assert replace(scope, intent="other").intent == "other"

    with pytest.raises(TypeError, match="reviewed loader"):
        RawV2RuntimePolicy(
            _authority=object(),
            reviewed_revision=REVISION,
            rules=policy.rules,
            content_sha256=policy.content_sha256,
        )
