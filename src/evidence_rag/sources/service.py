from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..security import secret_findings
from ..storage import utc_now
from .models import SourceEventInput
from .store import RawSourceStore


class RawSourceService:
    def __init__(self, store: RawSourceStore, root: Path) -> None:
        self.store = store
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def accept(self, request: SourceEventInput) -> dict[str, Any]:
        observed = utc_now()
        payload_bytes = self._encode_payload(request.payload, request.media_type)
        digest = (
            hashlib.sha256(payload_bytes).hexdigest()
            if payload_bytes is not None
            else hashlib.sha256((request.payload_ref or "").encode()).hexdigest()
        )
        content_hash = f"sha256:{digest}"
        state = "active"
        findings: list[str] = []
        if payload_bytes is not None and request.media_type.startswith(
            ("text/", "application/json")
        ):
            findings = secret_findings(payload_bytes.decode("utf-8", errors="replace"))
            if findings:
                state = "quarantined"
        raw = self.persist_bytes(
            project_id=request.project_id,
            source_type=request.source_type,
            source_instance=request.source_instance,
            source_object_id=request.source_object_id,
            source_version=request.source_version,
            payload=payload_bytes,
            source_uri=request.source_uri,
            media_type=request.media_type,
            acl_ref=request.acl_ref,
            adapter_version=request.adapter_version,
            schema_version=request.schema_version,
            state=state,
            metadata={**request.metadata, "secret_findings": findings},
            content_hash=content_hash,
        )
        event_id = request.event_id or f"evt-{uuid4()}"
        idempotency = hashlib.sha256(
            "\x1f".join(
                [
                    request.source_instance,
                    request.source_object_id,
                    request.source_version,
                    request.event_type,
                ]
            ).encode()
        ).hexdigest()
        event, created = self.store.create_event(
            {
                "event_id": event_id,
                "idempotency_key": idempotency,
                "source_type": request.source_type,
                "source_instance": request.source_instance,
                "event_type": request.event_type,
                "source_object_id": request.source_object_id,
                "source_version": request.source_version,
                "event_time": request.event_time,
                "observed_at": observed,
                "project_id": request.project_id,
                "acl_ref": request.acl_ref,
                "content_hash": content_hash,
                "raw_object_id": raw["id"],
                "payload_ref": request.payload_ref or raw.get("storage_path"),
                "trace_id": request.trace_id or uuid4().hex,
                "schema_version": request.schema_version,
                "status": "quarantined" if state == "quarantined" else "persisted",
                "metadata": request.metadata,
            }
        )
        return {"event": event, "raw_object": raw, "duplicate": not created}

    def persist_bytes(
        self,
        *,
        project_id: str,
        source_type: str,
        source_instance: str,
        source_object_id: str,
        source_version: str,
        payload: bytes | None,
        source_uri: str | None,
        media_type: str,
        acl_ref: str,
        adapter_version: str,
        schema_version: str,
        state: str = "active",
        metadata: dict[str, Any] | None = None,
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        digest = (
            content_hash or f"sha256:{hashlib.sha256(payload or b'').hexdigest()}"
        ).removeprefix("sha256:")
        identity_digest = hashlib.sha256(
            "\x1f".join([source_instance, source_object_id, source_version, digest]).encode("utf-8")
        ).hexdigest()
        raw_id = f"raw://sha256:{identity_digest}"
        storage_path = None
        # Quarantined content is deliberately not duplicated without a configured encrypted store.
        if payload is not None and state == "active":
            target = self.root / digest[:2] / digest[2:4] / digest
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temporary = target.with_suffix(f".{uuid4().hex}.tmp")
                temporary.write_bytes(payload)
                temporary.replace(target)
            storage_path = str(target)
        return self.store.put_object(
            {
                "id": raw_id,
                "project_id": project_id,
                "source_type": source_type,
                "source_instance": source_instance,
                "source_object_id": source_object_id,
                "source_version": source_version,
                "source_uri": source_uri,
                "content_hash": f"sha256:{digest}",
                "media_type": media_type,
                "byte_length": len(payload or b""),
                "storage_path": storage_path,
                "acl_ref": acl_ref,
                "state": state,
                "adapter_version": adapter_version,
                "schema_version": schema_version,
                "metadata": metadata or {},
                "observed_at": utc_now(),
            }
        )

    @staticmethod
    def _encode_payload(payload: Any | None, media_type: str) -> bytes | None:
        if payload is None:
            return None
        if isinstance(payload, str):
            return payload.encode("utf-8")
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload)
        if media_type != "application/json":
            raise ValueError("structured payloads require application/json")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
