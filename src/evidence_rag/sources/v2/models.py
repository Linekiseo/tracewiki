"""Strict public response models for the raw V2 projection boundary."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from .contracts import (
    OutwardDisposition,
    PortableId,
    SourceDomain,
    exact_bytes_sha256,
    validate_locator_id,
    validate_raw_object_id,
    validate_sha256_digest,
    validate_source_event_id,
    verify_canonical_timestamp,
)

RawObjectId = Annotated[StrictStr, AfterValidator(validate_raw_object_id)]
SourceEventId = Annotated[StrictStr, AfterValidator(validate_source_event_id)]
LocatorId = Annotated[StrictStr, AfterValidator(validate_locator_id)]
Digest = Annotated[StrictStr, AfterValidator(validate_sha256_digest)]
CanonicalTime = Annotated[StrictStr, AfterValidator(verify_canonical_timestamp)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
ListLimit = Annotated[StrictInt, Field(ge=1, le=1_000)]
RawObjectState = Literal["active", "reference_only", "quarantined", "tombstoned", "corrupt"]
RawEventStatus = Literal["persisted", "reference_only", "quarantined", "rejected", "tombstone"]


class _FrozenPublicModel(BaseModel):
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
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class RawV2ProjectScope(_FrozenPublicModel):
    """Mandatory project scope supplied by the authenticated service boundary."""

    project_id: PortableId


class RawV2ObjectKey(RawV2ProjectScope):
    """Mandatory project/object identity for by-id and tombstone operations."""

    raw_object_id: RawObjectId


class RawV2ObjectListRequest(RawV2ProjectScope):
    state: RawObjectState | None = None
    limit: ListLimit = 100


class RawV2EventListRequest(RawV2ProjectScope):
    limit: ListLimit = 100


class RawV2ObjectPublic(_FrozenPublicModel):
    raw_object_id: RawObjectId
    raw_content_sha256: Digest | None
    byte_length: NonNegativeInt | None
    state: RawObjectState

    @model_validator(mode="after")
    def _content_shape(self) -> RawV2ObjectPublic:
        has_digest = self.raw_content_sha256 is not None
        has_length = self.byte_length is not None
        if has_digest != has_length:
            raise ValueError("public content digest and byte length must appear together")
        if self.state == "active" and not has_digest:
            raise ValueError("active public object requires verified content")
        if self.state == "reference_only" and has_digest:
            raise ValueError("reference-only public object cannot claim content")
        return self


class RawV2EventPublic(_FrozenPublicModel):
    event_id: SourceEventId
    raw_object_id: RawObjectId | None
    raw_content_sha256: Digest | None
    status: RawEventStatus

    @model_validator(mode="after")
    def _event_shape(self) -> RawV2EventPublic:
        if self.status in {"persisted", "quarantined"} and self.raw_content_sha256 is None:
            raise ValueError("persisted public event requires verified content")
        if self.status == "reference_only" and self.raw_content_sha256 is not None:
            raise ValueError("reference-only public event cannot claim content")
        if self.status != "rejected" and self.raw_object_id is None:
            raise ValueError("non-rejected public event requires a raw object")
        return self


class RawV2CreatePublicResponse(_FrozenPublicModel):
    raw_object: RawV2ObjectPublic
    event: RawV2EventPublic
    duplicate: StrictBool


class RawV2ObjectListPublicResponse(_FrozenPublicModel):
    items: tuple[RawV2ObjectPublic, ...]


class RawV2EventListPublicResponse(_FrozenPublicModel):
    items: tuple[RawV2EventPublic, ...]


class RawV2TombstonePublicResponse(_FrozenPublicModel):
    raw_object_id: RawObjectId
    event_id: SourceEventId
    state: Literal["tombstoned"]
    tombstoned_at: CanonicalTime
    invalidated_binding_count: NonNegativeInt
    blocked_entity_count: NonNegativeInt
    duplicate: StrictBool


class RawV2PublicError(_FrozenPublicModel):
    code: OutwardDisposition


class RawV2SelectedReadPublicResponse(_FrozenPublicModel):
    """Byte-exact selected evidence after trusted privacy and audit processing."""

    locator_id: LocatorId
    binding_sha256: Digest
    raw_object_id: RawObjectId
    source_domain: SourceDomain
    source_type: PortableId
    source_version: PortableId
    stable_version: PortableId
    generation_id: PortableId
    media_type: StrictStr
    raw_content_sha256: Digest
    selector_kind: PortableId
    selector_sha256: Digest
    selected_content_sha256: Digest
    selected_byte_length: NonNegativeInt
    selected_base64: StrictStr
    evidence_state: Literal["OBSERVED"]
    audit_sha256: Digest

    @model_validator(mode="after")
    def _selected_bytes_shape(self) -> RawV2SelectedReadPublicResponse:
        try:
            encoded = self.selected_base64.encode("ascii")
            selected = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ValueError("selected_base64 must be strict base64") from error
        if base64.b64encode(selected) != encoded:
            raise ValueError("selected_base64 must be canonical")
        if len(selected) != self.selected_byte_length:
            raise ValueError("selected_base64 length differs from selected_byte_length")
        if exact_bytes_sha256(selected) != self.selected_content_sha256:
            raise ValueError("selected_base64 differs from selected_content_sha256")
        return self


__all__ = [
    "RawV2CreatePublicResponse",
    "RawV2EventListPublicResponse",
    "RawV2EventListRequest",
    "RawV2EventPublic",
    "RawV2ObjectKey",
    "RawV2ObjectListPublicResponse",
    "RawV2ObjectListRequest",
    "RawV2ObjectPublic",
    "RawV2ProjectScope",
    "RawV2PublicError",
    "RawV2SelectedReadPublicResponse",
    "RawV2TombstonePublicResponse",
]
