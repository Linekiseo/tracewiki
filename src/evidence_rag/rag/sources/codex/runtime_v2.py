"""Production-store facade for the complete Codex source pipeline.

The facade deliberately reads the application's active Codex generation and
does not consult evaluation data.  V1 remains the default; an explicit V2
request either returns a source-authenticated pipeline result or fails closed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ....models import CodexItemRecord, CodexTurnRecord
from .contracts import canonical_sha256
from .pipeline_v2 import (
    CODEX_PIPELINE_COMPONENT_SET_SHA256,
    CodexPipelineQueryResultV2,
    build_codex_thread_pipeline_v2,
    publish_codex_thread_pipeline_v2,
    query_codex_thread_pipeline_v2,
)
from .retrieval_v2 import CodexCalibrationArtifactV2, CodexCandidateChannel, CodexQueryTask
from .store_v2 import CodexV2Store, CodexV2StoreError

CODEX_SOURCE_RUNTIME_VERSION = "codex-source-runtime-v2"


class CodexProductionStoreV2(Protocol):
    """The read surface already implemented by the formal SQLite store."""

    def get_codex_thread(self, thread_identifier: str) -> dict[str, Any] | None: ...

    def list_codex_sources(self) -> list[dict[str, Any]]: ...

    def connection(self) -> Any: ...


class CodexSourceRuntimeErrorV2(ValueError):
    """Base error whose message is always a bounded reason code."""


class CodexSourceRuntimeUnavailableV2(CodexSourceRuntimeErrorV2):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class CodexSourceScopeErrorV2(CodexSourceRuntimeErrorV2):
    """The caller supplied a scope that cannot be compared safely."""


@dataclass(frozen=True, slots=True)
class CodexSourceScopeV2:
    item_types: tuple[str, ...]
    statuses: tuple[str, ...]
    date_from: datetime | None
    date_to: datetime | None


@dataclass(frozen=True, slots=True)
class CodexSourceAuthorityV2:
    project_id: str
    source_id: str
    raw_thread_id: str
    thread_record_id: str
    acl_ref: str
    generation_id: str
    watermark: str
    turn_count: int
    item_count: int


@dataclass(frozen=True, slots=True)
class CodexSourceRuntimeResultV2:
    authority: CodexSourceAuthorityV2
    pipeline: CodexPipelineQueryResultV2
    scoped_turns: tuple[CodexTurnRecord, ...]
    scoped_items: tuple[CodexItemRecord, ...]
    selected_engine: str = "v2"
    runtime_version: str = CODEX_SOURCE_RUNTIME_VERSION


@dataclass(frozen=True, slots=True)
class _LoadedCodexSource:
    authority: CodexSourceAuthorityV2
    turns: tuple[CodexTurnRecord, ...]
    items: tuple[CodexItemRecord, ...]
    source_snapshot_sha256: str


def _authorized(acl_ref: str, allowed_acl_refs: Iterable[str]) -> bool:
    return acl_ref == "public" or acl_ref in set(allowed_acl_refs)


def _aware_datetime(value: str | None, label: str) -> datetime | None:
    if value is None:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise CodexSourceScopeErrorV2(f"{label}_invalid")
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CodexSourceScopeErrorV2(f"{label}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CodexSourceScopeErrorV2(f"{label}_timezone_required")
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise CodexSourceScopeErrorV2(f"{label}_invalid") from exc


def normalize_codex_source_scope_v2(
    *,
    item_types: Iterable[str] = (),
    statuses: Iterable[str] = (),
    date_from: str | None = None,
    date_to: str | None = None,
) -> CodexSourceScopeV2:
    normalized_types = tuple(dict.fromkeys(item_types))
    normalized_statuses = tuple(dict.fromkeys(statuses))
    if any(
        type(value) is not str or not value or value != value.strip() for value in normalized_types
    ):
        raise CodexSourceScopeErrorV2("item_types_invalid")
    if any(
        type(value) is not str or not value or value != value.strip()
        for value in normalized_statuses
    ):
        raise CodexSourceScopeErrorV2("statuses_invalid")
    lower = _aware_datetime(date_from, "date_from")
    upper = _aware_datetime(date_to, "date_to")
    if lower is not None and upper is not None and lower > upper:
        raise CodexSourceScopeErrorV2("date_range_invalid")
    return CodexSourceScopeV2(
        item_types=normalized_types,
        statuses=normalized_statuses,
        date_from=lower,
        date_to=upper,
    )


def _scope_items(
    raw_items: tuple[dict[str, Any], ...],
    raw_turns: tuple[dict[str, Any], ...],
    scope: CodexSourceScopeV2,
) -> tuple[dict[str, Any], ...]:
    turn_status = {str(turn.get("id") or ""): turn.get("status") for turn in raw_turns}
    allowed_types = set(scope.item_types)
    allowed_statuses = set(scope.statuses)
    selected: list[dict[str, Any]] = []
    for item in raw_items:
        if allowed_types and item.get("item_type") not in allowed_types:
            continue
        effective_status = item.get("status")
        if effective_status is None:
            effective_status = turn_status.get(str(item.get("turn_id") or ""))
        if allowed_statuses and effective_status not in allowed_statuses:
            continue
        if scope.date_from is not None or scope.date_to is not None:
            timestamp = item.get("timestamp")
            if timestamp is None:
                continue
            observed_at = _aware_datetime(timestamp, "source_item_timestamp")
            if observed_at is None:
                continue
            if scope.date_from is not None and observed_at < scope.date_from:
                continue
            if scope.date_to is not None and observed_at > scope.date_to:
                continue
        selected.append(item)
    return tuple(selected)


class CodexSourceRuntimeV2:
    """Run the existing adapter→event→episode→retrieval→context chain."""

    def __init__(
        self,
        store: CodexProductionStoreV2,
        derived_store: CodexV2Store | None = None,
    ) -> None:
        self.store = store
        self.derived_store = derived_store
        self._known_authorities: dict[str, CodexSourceAuthorityV2] = {}

    def execute(
        self,
        *,
        engine: str | None,
        legacy: Callable[[], object] | None,
        project_id: str,
        allowed_acl_refs: Iterable[str],
        thread_id: str,
        query: str,
        task: CodexQueryTask | str,
        expected_generation_id: str | None = None,
        expected_watermark: str | None = None,
        final_k: int = 12,
        budget_chars: int = 12_000,
        enabled_channels: frozenset[CodexCandidateChannel] | None = None,
        calibration: CodexCalibrationArtifactV2 | None = None,
        item_types: Iterable[str] = (),
        statuses: Iterable[str] = (),
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> object:
        """Keep the legacy return untouched unless V2 is explicitly selected."""

        selected = "v1" if engine is None else engine
        if selected not in {"v1", "v2"}:
            raise CodexSourceRuntimeErrorV2("invalid_engine")
        if selected == "v1":
            if legacy is None:
                raise CodexSourceRuntimeErrorV2("legacy_callable_required")
            return legacy()
        return self.query_v2(
            project_id=project_id,
            allowed_acl_refs=allowed_acl_refs,
            thread_id=thread_id,
            query=query,
            task=task,
            expected_generation_id=expected_generation_id,
            expected_watermark=expected_watermark,
            final_k=final_k,
            budget_chars=budget_chars,
            enabled_channels=enabled_channels,
            calibration=calibration,
            item_types=item_types,
            statuses=statuses,
            date_from=date_from,
            date_to=date_to,
        )

    def query_v2(
        self,
        *,
        project_id: str,
        allowed_acl_refs: Iterable[str],
        thread_id: str,
        query: str,
        task: CodexQueryTask | str,
        expected_generation_id: str | None = None,
        expected_watermark: str | None = None,
        final_k: int = 12,
        budget_chars: int = 12_000,
        enabled_channels: frozenset[CodexCandidateChannel] | None = None,
        calibration: CodexCalibrationArtifactV2 | None = None,
        item_types: Iterable[str] = (),
        statuses: Iterable[str] = (),
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> CodexSourceRuntimeResultV2:
        source_scope = normalize_codex_source_scope_v2(
            item_types=item_types,
            statuses=statuses,
            date_from=date_from,
            date_to=date_to,
        )
        allowed = tuple(dict.fromkeys(allowed_acl_refs))
        loaded = self._load(
            project_id=project_id,
            allowed_acl_refs=allowed,
            thread_id=thread_id,
            expected_generation_id=expected_generation_id,
            expected_watermark=expected_watermark,
            source_scope=source_scope,
        )
        for identity in (
            thread_id,
            loaded.authority.raw_thread_id,
            loaded.authority.thread_record_id,
        ):
            if identity:
                self._known_authorities[identity] = loaded.authority
        try:
            bundle = (
                self.derived_store.lookup_pipeline_bundle(
                    project_id=loaded.authority.project_id,
                    source_id=loaded.authority.source_id,
                    generation_id=loaded.authority.generation_id,
                    thread_id=loaded.authority.thread_record_id,
                    acl_ref=loaded.authority.acl_ref,
                    authority_watermark=loaded.authority.watermark,
                    source_snapshot_sha256=loaded.source_snapshot_sha256,
                    component_set_sha256=CODEX_PIPELINE_COMPONENT_SET_SHA256,
                )
                if self.derived_store is not None
                else None
            )
            if bundle is None:
                bundle = build_codex_thread_pipeline_v2(
                    loaded.items,
                    project_id=loaded.authority.project_id,
                    thread_id=loaded.authority.thread_record_id,
                    turns=loaded.turns,
                )
            if (
                bundle.publication.source_id != loaded.authority.source_id
                or bundle.publication.generation_id != loaded.authority.generation_id
                or bundle.publication.acl_ref != loaded.authority.acl_ref
            ):
                raise CodexSourceRuntimeUnavailableV2("pipeline_authority_mismatch")
            if self.derived_store is not None:
                publish_codex_thread_pipeline_v2(
                    self.derived_store,
                    bundle,
                    authority_watermark=loaded.authority.watermark,
                    source_snapshot_sha256=loaded.source_snapshot_sha256,
                )
            result = query_codex_thread_pipeline_v2(
                bundle,
                query,
                task=task,
                final_k=final_k,
                budget_chars=budget_chars,
                enabled_channels=enabled_channels,
                calibration=calibration,
                store=self.derived_store,
            )
        except CodexSourceRuntimeUnavailableV2:
            raise
        except (TypeError, ValueError, CodexV2StoreError) as exc:
            raise CodexSourceRuntimeUnavailableV2("pipeline_rejected_source") from exc

        try:
            current = self._load(
                project_id=project_id,
                allowed_acl_refs=allowed,
                thread_id=thread_id,
                expected_generation_id=loaded.authority.generation_id,
                expected_watermark=loaded.authority.watermark,
                source_scope=source_scope,
            )
        except (CodexSourceRuntimeUnavailableV2, CodexSourceScopeErrorV2):
            self._rollback_derived(loaded.authority)
            raise
        if current.authority != loaded.authority:
            self._rollback_derived(loaded.authority)
            raise CodexSourceRuntimeUnavailableV2("source_changed_during_query")
        return CodexSourceRuntimeResultV2(
            authority=loaded.authority,
            pipeline=result,
            scoped_turns=loaded.turns,
            scoped_items=loaded.items,
        )

    def _load(
        self,
        *,
        project_id: str,
        allowed_acl_refs: tuple[str, ...],
        thread_id: str,
        expected_generation_id: str | None,
        expected_watermark: str | None,
        source_scope: CodexSourceScopeV2,
    ) -> _LoadedCodexSource:
        known = self._known_authorities.get(thread_id)
        if known is not None and known.project_id != project_id:
            known = None
        project = self._project(project_id)
        if project is None:
            self._tombstone_known(known, reason="source_deleted")
            raise CodexSourceRuntimeUnavailableV2("project_not_found")
        if str(project.get("status") or "") != "active":
            self._tombstone_known(known, reason="acl_revoked")
            raise CodexSourceRuntimeUnavailableV2("project_inactive")
        if not _authorized(str(project.get("acl_ref") or ""), allowed_acl_refs):
            if known is not None and str(project.get("acl_ref") or "") != known.acl_ref:
                self._tombstone_known(known, reason="acl_revoked")
            raise CodexSourceRuntimeUnavailableV2("project_acl_denied")

        thread = self.store.get_codex_thread(thread_id)
        if thread is None:
            known_source = (
                next(
                    (
                        item
                        for item in self.store.list_codex_sources()
                        if known is not None and item.get("id") == known.source_id
                    ),
                    None,
                )
                if known is not None
                else None
            )
            if known is not None and known_source is not None:
                current_acl_ref = str(known_source.get("acl_ref") or "")
                current_generation_id = str(known_source.get("active_generation_id") or "")
                current_status = str(known_source.get("status") or "")
                if current_acl_ref != known.acl_ref:
                    self._tombstone_known(known, reason="acl_revoked")
                elif current_generation_id != known.generation_id:
                    self._rollback_derived(known)
                elif current_status in {"deleted", "tombstoned"}:
                    self._tombstone_known(known, reason="source_deleted")
                else:
                    self._tombstone_known(known, reason="source_deleted")
            else:
                self._tombstone_known(known, reason="source_deleted")
            raise CodexSourceRuntimeUnavailableV2("thread_not_found")
        if thread.get("project_id") != project_id:
            raise CodexSourceRuntimeUnavailableV2("thread_project_mismatch")
        source_id = str(thread.get("source_id") or "")
        sources = [item for item in self.store.list_codex_sources() if item.get("id") == source_id]
        if len(sources) != 1:
            self._tombstone_known(known, reason="source_deleted")
            raise CodexSourceRuntimeUnavailableV2("source_not_found")
        source = sources[0]
        generation_id = str(source.get("active_generation_id") or "")
        acl_ref = str(source.get("acl_ref") or "")
        source_status = str(source.get("status") or "")
        if source.get("project_id") != project_id or source_status != "ready" or not generation_id:
            if known is not None:
                if source_status in {"deleted", "tombstoned"}:
                    self._tombstone_known(known, reason="source_deleted")
                elif source_status in {"acl_revoked", "revoked"}:
                    self._tombstone_known(known, reason="acl_revoked")
                else:
                    self._rollback_derived(known)
            raise CodexSourceRuntimeUnavailableV2("source_not_ready")
        if not _authorized(acl_ref, allowed_acl_refs):
            if known is not None and acl_ref != known.acl_ref:
                self._tombstone_known(known, reason="acl_revoked")
            raise CodexSourceRuntimeUnavailableV2("source_acl_denied")
        if (
            thread.get("generation_id") != generation_id
            or thread.get("acl_ref") != acl_ref
            or not self._generation_is_published(source_id, generation_id)
        ):
            if known is not None:
                if thread.get("acl_ref") != known.acl_ref or acl_ref != known.acl_ref:
                    self._tombstone_known(known, reason="acl_revoked")
                elif generation_id != known.generation_id:
                    self._rollback_derived(known)
            raise CodexSourceRuntimeUnavailableV2("active_generation_mismatch")

        raw_turns = tuple(thread.get("turns") or ())
        raw_items = tuple(item for turn in raw_turns for item in tuple(turn.get("items") or ()))
        if not raw_items:
            raise CodexSourceRuntimeUnavailableV2("source_items_unavailable")
        thread_record_id = str(thread.get("id") or "")
        turn_ids = {str(item.get("id") or "") for item in raw_turns}
        if (
            not thread_record_id
            or len(turn_ids) != len(raw_turns)
            or any(
                item.get("thread_id") != thread_record_id
                or item.get("source_id") != source_id
                or item.get("generation_id") != generation_id
                for item in raw_turns
            )
            or any(
                item.get("thread_id") != thread_record_id
                or item.get("turn_id") not in turn_ids
                or item.get("source_id") != source_id
                or item.get("generation_id") != generation_id
                or item.get("acl_ref") != acl_ref
                for item in raw_items
            )
        ):
            raise CodexSourceRuntimeUnavailableV2("record_scope_mismatch")
        if self._has_blocked_items(tuple(str(item["id"]) for item in raw_items)):
            self._tombstone_derived(
                project_id=project_id,
                source_id=source_id,
                generation_id=generation_id,
                acl_ref=acl_ref,
                reason="privacy_request",
            )
            raise CodexSourceRuntimeUnavailableV2("source_contains_tombstoned_items")
        scoped_raw_items = _scope_items(raw_items, raw_turns, source_scope)
        if not scoped_raw_items:
            raise CodexSourceRuntimeUnavailableV2("source_scope_no_match")
        scoped_turn_ids = {str(item.get("turn_id") or "") for item in scoped_raw_items}
        scoped_raw_turns = tuple(
            item for item in raw_turns if str(item.get("id") or "") in scoped_turn_ids
        )

        source_hash = str(thread.get("source_hash") or "")
        if not source_hash:
            raise CodexSourceRuntimeUnavailableV2("source_watermark_unavailable")
        watermark = canonical_sha256(
            {
                "project_id": project_id,
                "source_id": source_id,
                "generation_id": generation_id,
                "thread_record_id": thread_record_id,
                "source_hash": source_hash,
            }
        )
        if expected_generation_id is not None and expected_generation_id != generation_id:
            raise CodexSourceRuntimeUnavailableV2("expected_generation_mismatch")
        if expected_watermark is not None and expected_watermark != watermark:
            raise CodexSourceRuntimeUnavailableV2("expected_watermark_mismatch")

        turns = tuple(
            CodexTurnRecord(
                **{
                    name: item[name]
                    for name in CodexTurnRecord.__dataclass_fields__
                    if name in item
                }
            )
            for item in scoped_raw_turns
        )
        items = tuple(
            CodexItemRecord(
                **{
                    name: item[name]
                    for name in CodexItemRecord.__dataclass_fields__
                    if name in item
                }
            )
            for item in scoped_raw_items
        )
        authority = CodexSourceAuthorityV2(
            project_id=project_id,
            source_id=source_id,
            raw_thread_id=str(thread.get("thread_id") or ""),
            thread_record_id=thread_record_id,
            acl_ref=acl_ref,
            generation_id=generation_id,
            watermark=watermark,
            turn_count=len(turns),
            item_count=len(items),
        )
        source_snapshot_sha256 = canonical_sha256(
            {
                "authority": asdict(authority),
                "turns": tuple(asdict(item) for item in turns),
                "items": tuple(asdict(item) for item in items),
            }
        )
        return _LoadedCodexSource(
            authority=authority,
            turns=turns,
            items=items,
            source_snapshot_sha256=source_snapshot_sha256,
        )

    def _rollback_derived(self, authority: CodexSourceAuthorityV2) -> None:
        if self.derived_store is None:
            return
        try:
            self.derived_store.rollback(
                project_id=authority.project_id,
                source_id=authority.source_id,
                generation_id=authority.generation_id,
            )
        except CodexV2StoreError:
            return

    def _tombstone_known(
        self,
        authority: CodexSourceAuthorityV2 | None,
        *,
        reason: str,
    ) -> None:
        if authority is None:
            return
        try:
            self._tombstone_derived(
                project_id=authority.project_id,
                source_id=authority.source_id,
                generation_id=authority.generation_id,
                acl_ref=authority.acl_ref,
                reason=reason,
            )
        except CodexV2StoreError:
            return

    def _tombstone_derived(
        self,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        acl_ref: str,
        reason: str,
    ) -> None:
        if self.derived_store is None:
            return
        self.derived_store.tombstone(
            project_id=project_id,
            source_id=source_id,
            generation_id=generation_id,
            acl_ref=acl_ref,
            reason=reason,
        )

    def _project(self, project_id: str) -> dict[str, Any] | None:
        with self.store.connection() as db:
            row = db.execute(
                "SELECT id, acl_ref, status FROM projects WHERE id=?",
                (project_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _generation_is_published(self, source_id: str, generation_id: str) -> bool:
        with self.store.connection() as db:
            row = db.execute(
                """SELECT status FROM codex_generations
                   WHERE id=? AND source_id=?""",
                (generation_id, source_id),
            ).fetchone()
        return row is not None and row["status"] == "published"

    def _has_blocked_items(self, item_ids: tuple[str, ...]) -> bool:
        with self.store.connection() as db:
            for offset in range(0, len(item_ids), 500):
                chunk = item_ids[offset : offset + 500]
                marks = ",".join("?" for _ in chunk)
                if db.execute(
                    f"SELECT 1 FROM blocked_entities WHERE entity_id IN ({marks}) LIMIT 1",
                    chunk,
                ).fetchone():
                    return True
        return False


__all__ = [
    "CODEX_SOURCE_RUNTIME_VERSION",
    "CodexProductionStoreV2",
    "CodexSourceAuthorityV2",
    "CodexSourceRuntimeErrorV2",
    "CodexSourceRuntimeResultV2",
    "CodexSourceScopeErrorV2",
    "CodexSourceScopeV2",
    "CodexSourceRuntimeUnavailableV2",
    "CodexSourceRuntimeV2",
    "normalize_codex_source_scope_v2",
]
