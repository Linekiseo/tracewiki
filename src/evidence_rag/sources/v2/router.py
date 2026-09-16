"""Transport-neutral raw V2 public projection router.

This module does not mount an application router or read storage.  It is the
explicit record-to-wire seam used by a later reviewed V2 service integration.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import RawV2ContractError, RawV2Reason, canonical_json_bytes
from .models import (
    RawV2CreatePublicResponse,
    RawV2EventListPublicResponse,
    RawV2EventListRequest,
    RawV2EventPublic,
    RawV2ObjectKey,
    RawV2ObjectListPublicResponse,
    RawV2ObjectListRequest,
    RawV2ObjectPublic,
    RawV2ProjectScope,
    RawV2PublicError,
    RawV2SelectedReadPublicResponse,
    RawV2TombstonePublicResponse,
)
from .store import RawEventRecord, RawObjectRecord, RawTombstoneRecord

RAW_V2_PUBLIC_PROJECTION_VERSION = "raw-v2-public-projection-v1"

type PublicBody = (
    RawV2CreatePublicResponse
    | RawV2ObjectListPublicResponse
    | RawV2EventListPublicResponse
    | RawV2ObjectPublic
    | RawV2TombstonePublicResponse
    | RawV2SelectedReadPublicResponse
    | RawV2PublicError
)
_PUBLIC_BODY_TYPES = (
    RawV2CreatePublicResponse,
    RawV2ObjectListPublicResponse,
    RawV2EventListPublicResponse,
    RawV2ObjectPublic,
    RawV2TombstonePublicResponse,
    RawV2SelectedReadPublicResponse,
    RawV2PublicError,
)


@dataclass(frozen=True, slots=True)
class RawV2PublicResult:
    """HTTP transport metadata plus one exact reviewed public body."""

    status_code: int
    body: PublicBody

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise TypeError("status_code must be an exact HTTP integer")
        if type(self.body) not in _PUBLIC_BODY_TYPES:
            raise TypeError("body must be an exact reviewed raw V2 public model")

    def body_bytes(self) -> bytes:
        return canonical_json_bytes(self.body.model_dump(mode="json"), allow_none=True)


def _require_exact(value: object, expected: type[object], *, label: str) -> None:
    if type(value) is not expected:
        raise TypeError(f"{label} must be exact {expected.__name__}")


class RawV2PublicProjectionRouter:
    """Project-checked explicit projection for raw V2 endpoint results."""

    def create(
        self,
        *,
        scope: RawV2ProjectScope,
        raw_object: RawObjectRecord,
        event: RawEventRecord,
    ) -> RawV2PublicResult:
        _require_exact(scope, RawV2ProjectScope, label="scope")
        _require_exact(raw_object, RawObjectRecord, label="raw_object")
        _require_exact(event, RawEventRecord, label="event")
        if raw_object.project_id != scope.project_id or event.project_id != scope.project_id:
            return self._unavailable()
        if (
            event.raw_object_id != raw_object.raw_object_id
            or event.raw_content_sha256 != raw_object.raw_content_sha256
        ):
            return self._conflict()
        body = RawV2CreatePublicResponse(
            raw_object=self._object(raw_object),
            event=self._event(event),
            duplicate=not event.created,
        )
        return RawV2PublicResult(status_code=202, body=body)

    def list_objects(
        self,
        *,
        request: RawV2ObjectListRequest,
        records: tuple[RawObjectRecord, ...],
    ) -> RawV2PublicResult:
        _require_exact(request, RawV2ObjectListRequest, label="request")
        if type(records) is not tuple:
            raise TypeError("records must be an exact tuple")
        if len(records) > request.limit:
            raise ValueError("records exceed the reviewed request limit")
        projected: list[RawV2ObjectPublic] = []
        for record in records:
            _require_exact(record, RawObjectRecord, label="object record")
            if record.project_id != request.project_id:
                return self._unavailable()
            if request.state is not None and record.state != request.state:
                raise ValueError("record does not match the reviewed state filter")
            projected.append(self._object(record))
        return RawV2PublicResult(
            status_code=200,
            body=RawV2ObjectListPublicResponse(items=tuple(projected)),
        )

    def list_events(
        self,
        *,
        request: RawV2EventListRequest,
        records: tuple[RawEventRecord, ...],
    ) -> RawV2PublicResult:
        _require_exact(request, RawV2EventListRequest, label="request")
        if type(records) is not tuple:
            raise TypeError("records must be an exact tuple")
        if len(records) > request.limit:
            raise ValueError("records exceed the reviewed request limit")
        projected: list[RawV2EventPublic] = []
        for record in records:
            _require_exact(record, RawEventRecord, label="event record")
            if record.project_id != request.project_id:
                return self._unavailable()
            projected.append(self._event(record))
        return RawV2PublicResult(
            status_code=200,
            body=RawV2EventListPublicResponse(items=tuple(projected)),
        )

    def by_id(
        self,
        *,
        request: RawV2ObjectKey,
        record: RawObjectRecord | None,
    ) -> RawV2PublicResult:
        _require_exact(request, RawV2ObjectKey, label="request")
        if record is None:
            return self._unavailable()
        _require_exact(record, RawObjectRecord, label="record")
        if record.project_id != request.project_id or record.raw_object_id != request.raw_object_id:
            return self._unavailable()
        return RawV2PublicResult(status_code=200, body=self._object(record))

    def tombstone(
        self,
        *,
        request: RawV2ObjectKey,
        record: RawTombstoneRecord | None,
    ) -> RawV2PublicResult:
        _require_exact(request, RawV2ObjectKey, label="request")
        if record is None:
            return self._unavailable()
        _require_exact(record, RawTombstoneRecord, label="record")
        if record.project_id != request.project_id or record.raw_object_id != request.raw_object_id:
            return self._unavailable()
        body = RawV2TombstonePublicResponse(
            raw_object_id=record.raw_object_id,
            event_id=record.event_id,
            state="tombstoned",
            tombstoned_at=record.tombstoned_at,
            invalidated_binding_count=record.invalidated_binding_count,
            blocked_entity_count=record.blocked_entity_count,
            duplicate=not record.created,
        )
        return RawV2PublicResult(status_code=200, body=body)

    def error(self, error: RawV2ContractError) -> RawV2PublicResult:
        _require_exact(error, RawV2ContractError, label="error")
        return RawV2PublicResult(
            status_code=error.policy.http_status,
            body=RawV2PublicError(code=error.policy.outward_disposition),
        )

    def selected_read(
        self,
        *,
        response: RawV2SelectedReadPublicResponse,
    ) -> RawV2PublicResult:
        _require_exact(response, RawV2SelectedReadPublicResponse, label="response")
        return RawV2PublicResult(status_code=200, body=response)

    def _unavailable(self) -> RawV2PublicResult:
        return self.error(RawV2ContractError(RawV2Reason.PROJECT_MISMATCH))

    def _conflict(self) -> RawV2PublicResult:
        return self.error(RawV2ContractError(RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT))

    @staticmethod
    def _object(record: RawObjectRecord) -> RawV2ObjectPublic:
        return RawV2ObjectPublic(
            raw_object_id=record.raw_object_id,
            raw_content_sha256=record.raw_content_sha256,
            byte_length=record.byte_length,
            state=record.state,
        )

    @staticmethod
    def _event(record: RawEventRecord) -> RawV2EventPublic:
        return RawV2EventPublic(
            event_id=record.event_id,
            raw_object_id=record.raw_object_id,
            raw_content_sha256=record.raw_content_sha256,
            status=record.status,
        )


__all__ = [
    "RAW_V2_PUBLIC_PROJECTION_VERSION",
    "RawV2PublicProjectionRouter",
    "RawV2PublicResult",
]
