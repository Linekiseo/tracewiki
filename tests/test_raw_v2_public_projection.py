from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest
from pydantic import ValidationError

from evidence_rag.sources.v2.contracts import (
    REASON_POLICY,
    RawV2ContractError,
    RawV2Reason,
)
from evidence_rag.sources.v2.models import (
    RawV2CreatePublicResponse,
    RawV2EventListRequest,
    RawV2EventPublic,
    RawV2ObjectKey,
    RawV2ObjectListRequest,
    RawV2ObjectPublic,
    RawV2ProjectScope,
    RawV2PublicError,
    RawV2TombstonePublicResponse,
)
from evidence_rag.sources.v2.router import (
    RAW_V2_PUBLIC_PROJECTION_VERSION,
    RawV2PublicProjectionRouter,
    RawV2PublicResult,
)
from evidence_rag.sources.v2.store import (
    RawEventRecord,
    RawObjectRecord,
    RawTombstoneRecord,
)

PROJECT = "project-public"
PEER_PROJECT = "project-peer"
RAW_ID = "raw-v2:" + "1" * 64
PEER_RAW_ID = "raw-v2:" + "2" * 64
EVENT_ID = "source-event-v2:" + "3" * 64
TOMBSTONE_EVENT_ID = "source-event-v2:" + "4" * 64
DIGEST = "sha256:" + "5" * 64
LOGICAL_DIGEST = "sha256:" + "6" * 64
VISIBILITY_DIGEST = "sha256:" + "7" * 64
IDEMPOTENCY_DIGEST = "sha256:" + "8" * 64
TOMBSTONED_AT = "2026-09-01T00:00:00.000000Z"

FIELD_SETS = {
    "by_id": ["byte_length", "raw_content_sha256", "raw_object_id", "state"],
    "create": [
        "duplicate",
        "event",
        "event.event_id",
        "event.raw_content_sha256",
        "event.raw_object_id",
        "event.status",
        "raw_object",
        "raw_object.byte_length",
        "raw_object.raw_content_sha256",
        "raw_object.raw_object_id",
        "raw_object.state",
    ],
    "error": ["code"],
    "event_list": [
        "items",
        "items[].event_id",
        "items[].raw_content_sha256",
        "items[].raw_object_id",
        "items[].status",
    ],
    "raw_list": [
        "items",
        "items[].byte_length",
        "items[].raw_content_sha256",
        "items[].raw_object_id",
        "items[].state",
    ],
    "tombstone": [
        "blocked_entity_count",
        "duplicate",
        "event_id",
        "invalidated_binding_count",
        "raw_object_id",
        "state",
        "tombstoned_at",
    ],
}
FIELD_SET_DIGEST = "sha256:0ea5cfd37f5a69b380e7b64b01d4a6e0094c41715c3112e645c70d79958e9e3c"
FORBIDDEN_NAMES = {
    "acl",
    "acl_ref",
    "actor",
    "idempotency_sha256",
    "logical_identity_sha256",
    "metadata",
    "metadata_json",
    "path",
    "payload_ref",
    "project_id",
    "reason",
    "secret",
    "source_uri",
    "storage_key",
    "storage_path",
    "trace_id",
    "visibility_partition_sha256",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _object_record(
    *,
    project_id: str = PROJECT,
    raw_object_id: str = RAW_ID,
    state: str = "active",
    created: bool = True,
    raw_content_sha256: str | None = DIGEST,
    byte_length: int | None = 7,
) -> RawObjectRecord:
    return RawObjectRecord(
        raw_object_id=raw_object_id,
        logical_identity_sha256=LOGICAL_DIGEST,
        project_id=project_id,
        visibility_partition_sha256=VISIBILITY_DIGEST,
        raw_content_sha256=raw_content_sha256,
        byte_length=byte_length,
        state=state,
        created=created,
    )


def _event_record(
    *,
    project_id: str = PROJECT,
    raw_object_id: str | None = RAW_ID,
    status: str = "persisted",
    created: bool = True,
    raw_content_sha256: str | None = DIGEST,
) -> RawEventRecord:
    return RawEventRecord(
        event_id=EVENT_ID,
        idempotency_sha256=IDEMPOTENCY_DIGEST,
        project_id=project_id,
        visibility_partition_sha256=VISIBILITY_DIGEST,
        raw_object_id=raw_object_id,
        raw_content_sha256=raw_content_sha256,
        status=status,
        created=created,
    )


def _tombstone_record(
    *,
    project_id: str = PROJECT,
    raw_object_id: str = RAW_ID,
    created: bool = True,
) -> RawTombstoneRecord:
    return RawTombstoneRecord(
        raw_object_id=raw_object_id,
        project_id=project_id,
        event_id=TOMBSTONE_EVENT_ID,
        tombstoned_at=TOMBSTONED_AT,
        invalidated_binding_count=3,
        blocked_entity_count=2,
        created=created,
    )


def _body(result: RawV2PublicResult) -> object:
    return json.loads(result.body_bytes())


def _field_paths(value: object, *, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in sorted(value.items()):
            current = f"{prefix}.{key}" if prefix else key
            paths.append(current)
            paths.extend(_field_paths(item, prefix=current))
    elif isinstance(value, list) and value:
        item_prefix = f"{prefix}[]"
        paths.extend(_field_paths(value[0], prefix=item_prefix))
    return paths


def _assert_no_forbidden_names(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.lower()
            assert lowered not in FORBIDDEN_NAMES
            assert not any(token in lowered for token in ("path", "uri", "secret", "acl"))
            _assert_no_forbidden_names(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_names(item)


def test_projection_version_and_frozen_field_set_digest() -> None:
    assert RAW_V2_PUBLIC_PROJECTION_VERSION == "raw-v2-public-projection-v1"
    assert _sha256(_canonical_bytes(FIELD_SETS)) == FIELD_SET_DIGEST


def test_create_projects_exact_allowlist_and_duplicate_semantics() -> None:
    router = RawV2PublicProjectionRouter()
    created = router.create(
        scope=RawV2ProjectScope(project_id=PROJECT),
        raw_object=_object_record(created=True),
        event=_event_record(created=True),
    )
    duplicate = router.create(
        scope=RawV2ProjectScope(project_id=PROJECT),
        raw_object=_object_record(created=False),
        event=_event_record(created=False),
    )

    assert created.status_code == 202
    assert _body(created) == {
        "duplicate": False,
        "event": {
            "event_id": EVENT_ID,
            "raw_content_sha256": DIGEST,
            "raw_object_id": RAW_ID,
            "status": "persisted",
        },
        "raw_object": {
            "byte_length": 7,
            "raw_content_sha256": DIGEST,
            "raw_object_id": RAW_ID,
            "state": "active",
        },
    }
    assert _body(duplicate)["duplicate"] is True  # type: ignore[index]
    assert set(_field_paths(_body(created))) == set(FIELD_SETS["create"])


def test_list_and_by_id_project_exact_public_fields_only() -> None:
    router = RawV2PublicProjectionRouter()
    raw_list = router.list_objects(
        request=RawV2ObjectListRequest(project_id=PROJECT, limit=25),
        records=(
            _object_record(),
            _object_record(
                raw_object_id=PEER_RAW_ID,
                state="reference_only",
                raw_content_sha256=None,
                byte_length=None,
            ),
        ),
    )
    event_list = router.list_events(
        request=RawV2EventListRequest(project_id=PROJECT, limit=25),
        records=(_event_record(),),
    )
    by_id = router.by_id(
        request=RawV2ObjectKey(project_id=PROJECT, raw_object_id=RAW_ID),
        record=_object_record(),
    )

    assert raw_list.status_code == event_list.status_code == by_id.status_code == 200
    assert set(_field_paths(_body(raw_list))) == set(FIELD_SETS["raw_list"])
    assert set(_field_paths(_body(event_list))) == set(FIELD_SETS["event_list"])
    assert set(_field_paths(_body(by_id))) == set(FIELD_SETS["by_id"])
    for result in (raw_list, event_list, by_id):
        _assert_no_forbidden_names(_body(result))


def test_tombstone_projects_exact_receipt_and_duplicate() -> None:
    router = RawV2PublicProjectionRouter()
    request = RawV2ObjectKey(project_id=PROJECT, raw_object_id=RAW_ID)
    created = router.tombstone(request=request, record=_tombstone_record(created=True))
    duplicate = router.tombstone(request=request, record=_tombstone_record(created=False))

    assert created.status_code == 200
    assert _body(created) == {
        "blocked_entity_count": 2,
        "duplicate": False,
        "event_id": TOMBSTONE_EVENT_ID,
        "invalidated_binding_count": 3,
        "raw_object_id": RAW_ID,
        "state": "tombstoned",
        "tombstoned_at": TOMBSTONED_AT,
    }
    assert _body(duplicate)["duplicate"] is True  # type: ignore[index]
    assert set(_field_paths(_body(created))) == set(FIELD_SETS["tombstone"])


def test_private_internal_authority_never_serializes() -> None:
    hostile_project = "project-private-path-ref-uri-secret-acl"
    router = RawV2PublicProjectionRouter()
    result = router.create(
        scope=RawV2ProjectScope(project_id=hostile_project),
        raw_object=_object_record(project_id=hostile_project),
        event=_event_record(project_id=hostile_project),
    )
    raw = result.body_bytes()

    for private_value in (
        hostile_project,
        LOGICAL_DIGEST,
        VISIBILITY_DIGEST,
        IDEMPOTENCY_DIGEST,
        b"/private/blob/root",
        b"payload-ref://internal",
        b"file:///private/source",
        b"https://internal/source",
        b"SECRET_TOKEN",
    ):
        needle = private_value if isinstance(private_value, bytes) else private_value.encode()
        assert needle not in raw
    _assert_no_forbidden_names(_body(result))


@pytest.mark.parametrize(
    "reason",
    [
        RawV2Reason.BINDING_NOT_FOUND,
        RawV2Reason.PROJECT_MISMATCH,
        RawV2Reason.ACL_DENIED,
        RawV2Reason.VISIBILITY_MISMATCH,
    ],
)
def test_hidden_existence_errors_are_byte_identical(reason: RawV2Reason) -> None:
    router = RawV2PublicProjectionRouter()
    missing = router.by_id(
        request=RawV2ObjectKey(project_id=PROJECT, raw_object_id=RAW_ID),
        record=None,
    )
    hidden = router.error(RawV2ContractError(reason, "/private SECRET acl:peer"))

    assert hidden.status_code == missing.status_code == 404
    assert hidden.body_bytes() == missing.body_bytes() == b'{"code":"evidence_unavailable"}'
    assert _field_paths(_body(hidden)) == FIELD_SETS["error"]


def test_by_id_tombstone_and_lists_do_not_serialize_peer_project() -> None:
    router = RawV2PublicProjectionRouter()
    key = RawV2ObjectKey(project_id=PROJECT, raw_object_id=RAW_ID)
    peer_object = _object_record(project_id=PEER_PROJECT)
    peer_tombstone = _tombstone_record(project_id=PEER_PROJECT)

    results = (
        router.by_id(request=key, record=peer_object),
        router.tombstone(request=key, record=peer_tombstone),
        router.list_objects(
            request=RawV2ObjectListRequest(project_id=PROJECT, limit=10),
            records=(peer_object,),
        ),
        router.list_events(
            request=RawV2EventListRequest(project_id=PROJECT, limit=10),
            records=(_event_record(project_id=PEER_PROJECT),),
        ),
    )

    for result in results:
        assert result.status_code == 404
        assert result.body_bytes() == b'{"code":"evidence_unavailable"}'
        assert PEER_PROJECT.encode() not in result.body_bytes()


def test_create_relationship_conflict_never_serializes_internal_record() -> None:
    router = RawV2PublicProjectionRouter()
    result = router.create(
        scope=RawV2ProjectScope(project_id=PROJECT),
        raw_object=_object_record(),
        event=_event_record(raw_object_id=PEER_RAW_ID),
    )

    assert result.status_code == REASON_POLICY[
        RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT
    ].http_status
    assert _body(result) == {"code": "conflict"}
    assert RAW_ID.encode() not in result.body_bytes()
    assert PEER_RAW_ID.encode() not in result.body_bytes()


def test_every_reason_uses_frozen_policy_without_reason_or_detail() -> None:
    router = RawV2PublicProjectionRouter()
    for reason in RawV2Reason:
        detail = f"private-detail-{reason.value}-/tmp/secret"
        result = router.error(RawV2ContractError(reason, detail))
        assert result.status_code == REASON_POLICY[reason].http_status
        assert _body(result) == {"code": REASON_POLICY[reason].outward_disposition.value}
        assert detail.encode() not in result.body_bytes()
        if reason.value != REASON_POLICY[reason].outward_disposition.value:
            assert reason.value.encode() not in result.body_bytes()


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        (
            "create",
            {
                "scope": RawV2ProjectScope(project_id=PROJECT),
                "raw_object": {"storage_path": "/private", "acl_ref": "peer"},
                "event": _event_record(),
            },
        ),
        (
            "list_objects",
            {
                "request": RawV2ObjectListRequest(project_id=PROJECT, limit=10),
                "records": ({"source_uri": "file:///private"},),
            },
        ),
        (
            "by_id",
            {
                "request": RawV2ObjectKey(project_id=PROJECT, raw_object_id=RAW_ID),
                "record": {"id": RAW_ID, "payload_ref": "secret"},
            },
        ),
        (
            "tombstone",
            {
                "request": RawV2ObjectKey(project_id=PROJECT, raw_object_id=RAW_ID),
                "record": {"id": RAW_ID, "metadata": {"secret": "token"}},
            },
        ),
    ],
)
def test_direct_dict_or_row_like_input_is_rejected(method: str, kwargs: dict[str, Any]) -> None:
    router = RawV2PublicProjectionRouter()
    with pytest.raises(TypeError):
        cast(Any, getattr(router, method))(**kwargs)


def test_public_models_are_strict_extra_forbid_and_frozen() -> None:
    public = RawV2ObjectPublic(
        raw_object_id=RAW_ID,
        raw_content_sha256=DIGEST,
        byte_length=7,
        state="active",
    )
    with pytest.raises(ValidationError):
        RawV2ObjectPublic.model_validate({**public.model_dump(), "storage_path": "/private"})
    with pytest.raises(ValidationError):
        public.state = "tombstoned"

    result = RawV2PublicResult(status_code=200, body=public)
    with pytest.raises(FrozenInstanceError):
        result.status_code = 500


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RawV2ProjectScope(project_id=""),
        lambda: RawV2ObjectKey(project_id=PROJECT, raw_object_id="raw-v2:not-canonical"),
        lambda: RawV2ObjectListRequest(project_id=PROJECT, limit=0),
        lambda: RawV2EventListRequest(project_id=PROJECT, limit=1001),
        lambda: RawV2ObjectPublic(
            raw_object_id=RAW_ID,
            raw_content_sha256="sha256:UPPER",
            byte_length=7,
            state="active",
        ),
        lambda: RawV2ObjectPublic(
            raw_object_id=RAW_ID,
            raw_content_sha256=None,
            byte_length=None,
            state="active",
        ),
        lambda: RawV2ObjectPublic(
            raw_object_id=RAW_ID,
            raw_content_sha256=DIGEST,
            byte_length=7,
            state="unknown",
        ),
        lambda: RawV2EventPublic(
            event_id=EVENT_ID,
            raw_object_id=RAW_ID,
            raw_content_sha256=DIGEST,
            status="unknown",
        ),
        lambda: RawV2TombstonePublicResponse(
            raw_object_id=RAW_ID,
            event_id=TOMBSTONE_EVENT_ID,
            state="tombstoned",
            tombstoned_at="2026-09-01T00:00:00Z",
            invalidated_binding_count=-1,
            blocked_entity_count=0,
            duplicate=False,
        ),
        lambda: RawV2PublicError(code="acl_denied"),
    ],
)
def test_invalid_public_shapes_fail_before_serialization(factory: Any) -> None:
    with pytest.raises((RawV2ContractError, ValidationError)):
        factory()


def test_public_response_models_reject_unreviewed_nested_fields() -> None:
    raw = RawV2ObjectPublic(
        raw_object_id=RAW_ID,
        raw_content_sha256=DIGEST,
        byte_length=7,
        state="active",
    )
    event = RawV2EventPublic(
        event_id=EVENT_ID,
        raw_object_id=RAW_ID,
        raw_content_sha256=DIGEST,
        status="persisted",
    )
    response = RawV2CreatePublicResponse(raw_object=raw, event=event, duplicate=False)

    with pytest.raises(ValidationError):
        RawV2CreatePublicResponse.model_validate(
            {**response.model_dump(), "metadata": {"secret": "value"}}
        )
