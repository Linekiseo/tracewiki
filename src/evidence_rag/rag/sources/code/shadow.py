"""Bounded, non-blocking shadow validation for Code Source retrieval."""

from __future__ import annotations

import hmac
import json
import math
import secrets
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ....models import EvidenceSearchRequest, SearchScope
from ....retrieval import (
    LegacyChannelExecutionEvidence,
    LegacySearchExecutionEvidence,
    LegacySearchResponse,
)
from .contracts import (
    CodeQueryProfile,
    CodeSourceResult,
    CodeVersionAlignment,
)
from .v1_adapter import CodeSourceRetrieverV1Adapter

SHADOW_ENVELOPE_MAX_BYTES = 64 * 1024
SHADOW_STRUCTURE_MAX_DEPTH = 12
SHADOW_STRUCTURE_MAX_NODES = 4_096
SHADOW_CONTAINER_MAX_ITEMS = 256
SHADOW_RESULTS_MAX_ITEMS = 50
SHADOW_STRING_MAX_CHARS = 8_192
SHADOW_TOTAL_STRING_MAX_CHARS = 48 * 1024
SHADOW_TOTAL_STRING_MAX_BYTES = 48 * 1024

_PROTOCOL_VERSION = 2
_EVIDENCE_VERSION = 1
_AES_GCM_TAG_BYTES = 16
_NONCE_COUNTER_MAX = (1 << 64) - 1


class CodeSourceRetrieverProtocol(Protocol):
    """Future V2 seam; C1-02 intentionally provides no V2 implementation."""

    def search(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
    ) -> CodeSourceResult: ...


class CodeShadowStage(StrEnum):
    SNAPSHOT = "snapshot"
    DISPATCH = "dispatch"
    ADAPTER = "adapter"
    V2 = "v2"
    RUNNER = "runner"
    TIMEOUT = "timeout"
    PROBE = "probe"


class CodeShadowError(StrEnum):
    SNAPSHOT_FAILED = "snapshot_failed"
    SNAPSHOT_OVERSIZED = "snapshot_oversized"
    SNAPSHOT_AUTHENTICATION_FAILED = "snapshot_authentication_failed"
    SNAPSHOT_REPLAYED = "snapshot_replayed"
    SNAPSHOT_NONCE_REUSED = "snapshot_nonce_reused"
    DISPATCH_FAILED = "dispatch_failed"
    ADAPTER_FAILED = "adapter_failed"
    V2_FAILED = "v2_failed"
    V2_INVALID_RESULT = "v2_invalid_result"
    RUNNER_FAILED = "runner_failed"
    TIME_BUDGET_EXHAUSTED = "time_budget_exhausted"
    PROBE_FAILED = "probe_failed"


@dataclass(frozen=True, slots=True)
class CodeShadowFailure:
    """A fixed, non-dynamic failure code safe for redacted telemetry."""

    stage: CodeShadowStage
    error: CodeShadowError


@dataclass(frozen=True, slots=True)
class CodeShadowRankDelta:
    entity_hash: str
    v1_rank: int
    comparison_rank: int
    delta: int


@dataclass(frozen=True, slots=True)
class CodeShadowObservation:
    """Redacted in-memory telemetry; no query or evidence content is retained."""

    query_hash: str
    query_id_hash: str
    profile_hash: str
    status: str
    comparison_engine: str
    v1_entity_hashes: tuple[str, ...]
    comparison_entity_hashes: tuple[str, ...]
    top_k_jaccard: float
    v1_top_k_recall: float
    comparison_top_k_recall: float
    rank_deltas: tuple[CodeShadowRankDelta, ...]
    legacy_result_count: int
    adapter_result_count: int
    adapter_unique_result_count: int
    v2_result_count: int | None
    v2_unique_result_count: int | None
    legacy_latency_ms: float
    adapter_latency_ms: float
    v2_latency_ms: float | None
    total_latency_ms: float
    wrong_version_count: int
    v2_wrong_version_count: int | None
    generation_hashes: tuple[str, ...]
    failures: tuple[CodeShadowFailure, ...]
    drop_reason: str


@dataclass(slots=True)
class _ShadowJob:
    job_id: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class _ObservationIdentity:
    query_hash: str
    query_id_hash: str
    profile_hash: str
    legacy_result_count: int


@dataclass(frozen=True, slots=True)
class _SealedJobEnvelope:
    """Opaque queued state; every sensitive worker input is inside AES-GCM ciphertext."""

    protocol_version: int
    runner_id: str
    job_id: str
    request_execution_tag: str
    profile_hash: str
    evidence_version: int
    nonce: bytes
    ciphertext: bytes
    tag: bytes


@dataclass(frozen=True, slots=True)
class _OpenedJobEnvelope:
    request: EvidenceSearchRequest
    profile: CodeQueryProfile
    legacy_response: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _NormalizedSubmission:
    """Private bounded snapshot; no caller-owned container remains reachable."""

    envelope: dict[str, Any]
    plaintext: bytes
    budget: _StructureBudget
    request_execution_tag: str
    evidence_version: int


@dataclass(frozen=True, slots=True)
class _EvaluationFailure(Exception):
    stage: CodeShadowStage
    error: CodeShadowError


@dataclass(slots=True)
class _StructureBudget:
    nodes: int = 0
    string_chars: int = 0
    string_bytes: int = 0


@dataclass(slots=True)
class _RunnerState:
    """Authority and mutable state bound to one live runner object identity."""

    telemetry_key: bytes
    snapshot_key: bytes
    runner_id: str
    nonce_prefix: bytes
    authority_lock: threading.Lock
    slots: threading.BoundedSemaphore
    observations: deque[CodeShadowObservation]
    condition: threading.Condition
    sealed_jobs: set[str]
    timers: dict[str, threading.Timer]
    nonce_counter: int = 0
    last_nonce_counter: int = -1
    job_counter: int = 0
    pending: int = 0
    closed: bool = False


@dataclass(frozen=True, slots=True)
class _RunnerStateEntry:
    reference: weakref.ReferenceType[object]
    state: _RunnerState


_RUNNER_STATE_LOCK = threading.RLock()
_RUNNER_STATES: dict[int, _RunnerStateEntry] = {}
_RUNNER_AUTHORITY_UNAVAILABLE = "CodeShadowRunner authority is unavailable"
# This boundary defeats public copy/serialization protocols; it is not a sandbox
# against hostile code that deliberately imports module-private in-process state.


def _remove_runner_state(
    identity: int,
    callback_reference: weakref.ReferenceType[object],
) -> None:
    """Remove only the registration that installed this exact weakref callback."""

    with _RUNNER_STATE_LOCK:
        entry = _RUNNER_STATES.get(identity)
        if entry is not None and entry.reference is callback_reference:
            _RUNNER_STATES.pop(identity, None)


def _register_runner_state(runner: CodeShadowRunner, state: _RunnerState) -> None:
    identity = id(runner)

    def remove(callback_reference: weakref.ReferenceType[object]) -> None:
        _remove_runner_state(identity, callback_reference)

    reference = weakref.ref(runner, remove)
    entry = _RunnerStateEntry(reference=reference, state=state)
    with _RUNNER_STATE_LOCK:
        current = _RUNNER_STATES.get(identity)
        if current is not None and current.reference() is not None:
            raise RuntimeError(_RUNNER_AUTHORITY_UNAVAILABLE)
        _RUNNER_STATES[identity] = entry


def _runner_state(runner: object) -> _RunnerState:
    """Resolve state by real identity; copying public instance state grants no authority."""

    identity = id(runner)
    with _RUNNER_STATE_LOCK:
        entry = _RUNNER_STATES.get(identity)
        registered = None if entry is None else entry.reference()
        if entry is not None and registered is None:
            _RUNNER_STATES.pop(identity, None)
    if entry is None or registered is not runner:
        raise RuntimeError(_RUNNER_AUTHORITY_UNAVAILABLE)
    return entry.state


class _SnapshotOversized(ValueError):
    pass


class _ReadOnlyLegacyResponse(Mapping[str, Any]):
    """Transient deep-read-only execution view with separately retained evidence."""

    __slots__ = ("_payload", "_execution_evidence")

    def __init__(
        self,
        payload: Mapping[str, Any],
        execution_evidence: LegacySearchExecutionEvidence,
    ) -> None:
        self._payload = payload
        self._execution_evidence = execution_evidence

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    @property
    def execution_evidence(self) -> LegacySearchExecutionEvidence:
        return self._execution_evidence


class CodeShadowRunner:
    """Validate adapter/V2 results outside the request path with hard admission bounds."""

    _AUTHORITY_COPY_ERROR = "CodeShadowRunner authority cannot be copied or serialized"

    def __init__(
        self,
        adapter: CodeSourceRetrieverV1Adapter,
        v2: CodeSourceRetrieverProtocol | None = None,
        *,
        max_inflight: int = 4,
        timeout_ms: float = 150.0,
        max_observations: int = 256,
        top_k: int = 10,
    ) -> None:
        if type(max_inflight) is not int or max_inflight < 1:
            raise ValueError("max_inflight must be a positive integer")
        _validate_timeout(timeout_ms, positive=True, field="timeout_ms")
        if type(max_observations) is not int or max_observations < 1:
            raise ValueError("max_observations must be a positive integer")
        if type(top_k) is not int or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        self.adapter = adapter
        self.v2 = v2
        self.max_inflight = max_inflight
        self.timeout_ms = timeout_ms
        self.max_observations = max_observations
        self.top_k = top_k
        state = _RunnerState(
            telemetry_key=secrets.token_bytes(32),
            snapshot_key=AESGCM.generate_key(bit_length=256),
            runner_id=secrets.token_hex(16),
            nonce_prefix=secrets.token_bytes(4),
            authority_lock=threading.Lock(),
            slots=threading.BoundedSemaphore(max_inflight),
            observations=deque(maxlen=max_observations),
            condition=threading.Condition(),
            sealed_jobs=set(),
            timers={},
        )
        _register_runner_state(self, state)

    def __copy__(self) -> CodeShadowRunner:
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __deepcopy__(self, memo: dict[int, object]) -> CodeShadowRunner:
        del memo
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __reduce__(self) -> object:
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __getstate__(self) -> object:
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __setstate__(self, state: object) -> None:
        del state
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def _state(self) -> _RunnerState:
        return _runner_state(self)

    def submit(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        legacy_response: Mapping[str, Any],
    ) -> bool:
        """Bound, seal, and admit shadow work without waiting for adaptation or V2."""

        state = self._state()
        with state.condition:
            closed = state.closed
        if closed:
            self._append_observation(
                _empty_observation(
                    self._control_identity(b"runner-closed", legacy_response),
                    status="dropped",
                    drop_reason="runner_closed",
                )
            )
            return False
        if not state.slots.acquire(blocking=False):
            self._append_observation(
                _empty_observation(
                    self._control_identity(b"inflight-budget", legacy_response),
                    status="dropped",
                    drop_reason="inflight_budget_exhausted",
                )
            )
            return False

        try:
            evidence = _execution_evidence(legacy_response)
            normalized = _build_bounded_submission(
                request,
                profile,
                legacy_response,
                evidence,
            )
            identity = _observation_identity(
                state.telemetry_key,
                normalized,
            )
            job_id = self._next_job_id()
            response_snapshot = self._seal_submission(
                job_id,
                normalized,
                identity.profile_hash,
            )
        except _SnapshotOversized:
            state.slots.release()
            self._append_observation(
                _empty_observation(
                    self._control_identity(b"snapshot-oversized", legacy_response),
                    status="dropped",
                    failures=(
                        CodeShadowFailure(
                            CodeShadowStage.SNAPSHOT,
                            CodeShadowError.SNAPSHOT_OVERSIZED,
                        ),
                    ),
                    drop_reason="snapshot_oversized",
                )
            )
            return False
        except _EvaluationFailure as failure:
            state.slots.release()
            self._append_observation(
                _empty_observation(
                    self._control_identity(b"snapshot-failed", legacy_response),
                    status="error",
                    failures=(CodeShadowFailure(failure.stage, failure.error),),
                    drop_reason="snapshot_failed",
                )
            )
            return False
        except BaseException:
            state.slots.release()
            self._append_observation(
                _empty_observation(
                    self._control_identity(b"snapshot-failed", legacy_response),
                    status="error",
                    failures=(
                        CodeShadowFailure(
                            CodeShadowStage.SNAPSHOT,
                            CodeShadowError.SNAPSHOT_FAILED,
                        ),
                    ),
                    drop_reason="snapshot_failed",
                )
            )
            return False

        job = _ShadowJob(job_id=job_id)
        with state.condition:
            if state.closed:
                state.slots.release()
                self._append_observation(
                    _empty_observation(
                        identity,
                        status="dropped",
                        drop_reason="runner_closed",
                    )
                )
                return False
            state.sealed_jobs.add(job_id)
            state.pending += 1

        timer: threading.Timer | None = None
        try:
            timer = threading.Timer(
                timeout_ms_to_seconds(self.timeout_ms),
                self._expire_job,
                args=(job, identity),
            )
            timer.daemon = True
            with state.condition:
                state.timers[job_id] = timer
            worker = threading.Thread(
                target=self._run_job,
                args=(job, identity, response_snapshot),
                name="code-shadow",
                daemon=True,
            )
            timer.start()
            worker.start()
        except BaseException:
            self._cancel_timer(job_id)
            self._finish_job(
                job,
                _empty_observation(
                    identity,
                    status="error",
                    failures=(
                        CodeShadowFailure(
                            CodeShadowStage.DISPATCH,
                            CodeShadowError.DISPATCH_FAILED,
                        ),
                    ),
                    drop_reason="dispatch_failed",
                ),
            )
            state.slots.release()
            return False
        return True

    def probe(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        legacy_response: Mapping[str, Any],
    ) -> CodeShadowObservation:
        """Synchronously evaluate one one-shot envelope for deterministic tests."""

        state = self._state()
        started = time.perf_counter()
        try:
            evidence = _execution_evidence(legacy_response)
            normalized = _build_bounded_submission(
                request,
                profile,
                legacy_response,
                evidence,
            )
            identity = _observation_identity(
                state.telemetry_key,
                normalized,
            )
            job_id = self._next_job_id()
            snapshot = self._seal_submission(
                job_id,
                normalized,
                identity.profile_hash,
            )
            self._register_probe_job(job_id)
            opened = self._open_job(job_id, snapshot)
            return self._evaluate(
                identity,
                opened.request,
                opened.profile,
                opened.legacy_response,
                started=started,
            )
        except _SnapshotOversized:
            return _empty_observation(
                self._control_identity(b"snapshot-oversized", legacy_response),
                status="dropped",
                total_latency_ms=_elapsed_ms(started),
                failures=(
                    CodeShadowFailure(
                        CodeShadowStage.SNAPSHOT,
                        CodeShadowError.SNAPSHOT_OVERSIZED,
                    ),
                ),
                drop_reason="snapshot_oversized",
            )
        except _EvaluationFailure as failure:
            return _empty_observation(
                self._control_identity(b"probe-failed", legacy_response),
                status="error",
                total_latency_ms=_elapsed_ms(started),
                failures=(CodeShadowFailure(failure.stage, failure.error),),
                drop_reason="probe_failed",
            )
        except BaseException:
            return _empty_observation(
                self._control_identity(b"probe-failed", legacy_response),
                status="error",
                total_latency_ms=_elapsed_ms(started),
                failures=(
                    CodeShadowFailure(
                        CodeShadowStage.PROBE,
                        CodeShadowError.PROBE_FAILED,
                    ),
                ),
                drop_reason="probe_failed",
            )

    def observations(self) -> tuple[CodeShadowObservation, ...]:
        state = self._state()
        with state.condition:
            return tuple(state.observations)

    @property
    def pending(self) -> int:
        state = self._state()
        with state.condition:
            return state.pending

    def flush(self, timeout: float = 1.0) -> bool:
        """Wait only for accepted jobs to reach success/error/timeout observation."""

        _validate_timeout(timeout, positive=False, field="timeout")
        state = self._state()
        deadline = time.monotonic() + timeout
        with state.condition:
            while state.pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                state.condition.wait(remaining)
            return True

    def drain(self, timeout: float = 1.0) -> bool:
        _validate_timeout(timeout, positive=False, field="timeout")
        return self.flush(timeout)

    def close(self, *, wait: bool = False, timeout: float = 1.0) -> bool:
        _validate_timeout(timeout, positive=False, field="timeout")
        state = self._state()
        with state.condition:
            state.closed = True
        return self.flush(timeout) if wait else True

    def _next_job_id(self) -> str:
        state = self._state()
        with state.authority_lock:
            if state.job_counter > _NONCE_COUNTER_MAX:
                raise _EvaluationFailure(
                    CodeShadowStage.SNAPSHOT,
                    CodeShadowError.SNAPSHOT_FAILED,
                )
            counter = state.job_counter
            state.job_counter += 1
        return _keyed_hash(
            state.telemetry_key,
            b"job",
            counter.to_bytes(8, "big"),
        )

    def _next_nonce(self) -> bytes:
        state = self._state()
        with state.authority_lock:
            counter = state.nonce_counter
            if counter > _NONCE_COUNTER_MAX or counter <= state.last_nonce_counter:
                raise _EvaluationFailure(
                    CodeShadowStage.SNAPSHOT,
                    CodeShadowError.SNAPSHOT_NONCE_REUSED,
                )
            state.last_nonce_counter = counter
            state.nonce_counter += 1
            return state.nonce_prefix + counter.to_bytes(8, "big")

    def _seal_submission(
        self,
        job_id: str,
        normalized: _NormalizedSubmission,
        profile_hash: str,
    ) -> _SealedJobEnvelope:
        state = self._state()
        nonce = self._next_nonce()
        snapshot = _SealedJobEnvelope(
            protocol_version=_PROTOCOL_VERSION,
            runner_id=state.runner_id,
            job_id=job_id,
            request_execution_tag=normalized.request_execution_tag,
            profile_hash=profile_hash,
            evidence_version=normalized.evidence_version,
            nonce=nonce,
            ciphertext=b"",
            tag=b"",
        )
        aad = _snapshot_aad(snapshot)
        sealed = AESGCM(state.snapshot_key).encrypt(nonce, normalized.plaintext, aad)
        return _SealedJobEnvelope(
            protocol_version=snapshot.protocol_version,
            runner_id=snapshot.runner_id,
            job_id=snapshot.job_id,
            request_execution_tag=snapshot.request_execution_tag,
            profile_hash=snapshot.profile_hash,
            evidence_version=snapshot.evidence_version,
            nonce=snapshot.nonce,
            ciphertext=sealed[:-_AES_GCM_TAG_BYTES],
            tag=sealed[-_AES_GCM_TAG_BYTES:],
        )

    def _register_probe_job(self, job_id: str) -> None:
        state = self._state()
        with state.condition:
            if len(state.sealed_jobs) >= self.max_inflight + 1:
                raise _EvaluationFailure(
                    CodeShadowStage.SNAPSHOT,
                    CodeShadowError.SNAPSHOT_FAILED,
                )
            state.sealed_jobs.add(job_id)

    def _open_job(
        self,
        job_id: str,
        snapshot: _SealedJobEnvelope,
    ) -> _OpenedJobEnvelope:
        state = self._state()
        with state.condition:
            if (
                snapshot.runner_id != state.runner_id
                or snapshot.job_id != job_id
                or job_id not in state.sealed_jobs
            ):
                raise _EvaluationFailure(
                    CodeShadowStage.SNAPSHOT,
                    CodeShadowError.SNAPSHOT_REPLAYED,
                )
            state.sealed_jobs.remove(job_id)
        try:
            plaintext = AESGCM(state.snapshot_key).decrypt(
                snapshot.nonce,
                snapshot.ciphertext + snapshot.tag,
                _snapshot_aad(snapshot),
            )
        except InvalidTag:
            raise _EvaluationFailure(
                CodeShadowStage.SNAPSHOT,
                CodeShadowError.SNAPSHOT_AUTHENTICATION_FAILED,
            ) from None
        except BaseException:
            raise _EvaluationFailure(
                CodeShadowStage.SNAPSHOT,
                CodeShadowError.SNAPSHOT_FAILED,
            ) from None
        return self._decode_opened_job(snapshot, plaintext)

    def _decode_opened_job(
        self,
        snapshot: _SealedJobEnvelope,
        plaintext: bytes,
    ) -> _OpenedJobEnvelope:
        state = self._state()
        try:
            envelope = json.loads(plaintext)
            if not isinstance(envelope, dict):
                raise TypeError
            if envelope.get("protocol_version") != _PROTOCOL_VERSION:
                raise ValueError
            request = _request_from_payload(envelope["request"])
            profile = CodeQueryProfile.model_validate(envelope["profile"])
            evidence_value = envelope.get("execution_evidence")
            evidence = (
                _evidence_from_payload(evidence_value) if evidence_value is not None else None
            )
            legacy_payload = _freeze_json(envelope["legacy_response"])
            if not isinstance(legacy_payload, Mapping):
                raise TypeError
            profile_hash = _keyed_hash(
                state.telemetry_key,
                b"profile",
                profile.canonical_json_bytes(),
            )
            request_execution_tag = (
                evidence.request_tag.hex()
                if evidence is not None
                else _keyed_hash(state.telemetry_key, b"missing-evidence", b"")
            )
            evidence_version = _EVIDENCE_VERSION if evidence is not None else 0
            if not hmac.compare_digest(profile_hash, snapshot.profile_hash):
                raise ValueError
            if not hmac.compare_digest(
                request_execution_tag,
                snapshot.request_execution_tag,
            ):
                raise ValueError
            if evidence_version != snapshot.evidence_version:
                raise ValueError
            if evidence is None:
                legacy_response: Mapping[str, Any] = legacy_payload
            else:
                legacy_response = _ReadOnlyLegacyResponse(legacy_payload, evidence)
            return _OpenedJobEnvelope(
                request=request,
                profile=profile,
                legacy_response=legacy_response,
            )
        except BaseException:
            raise _EvaluationFailure(
                CodeShadowStage.SNAPSHOT,
                CodeShadowError.SNAPSHOT_AUTHENTICATION_FAILED,
            ) from None

    def _run_job(
        self,
        job: _ShadowJob,
        identity: _ObservationIdentity,
        response_snapshot: _SealedJobEnvelope,
    ) -> None:
        state = self._state()
        started = time.perf_counter()
        try:
            opened = self._open_job(job.job_id, response_snapshot)
            observation = self._evaluate(
                identity,
                opened.request,
                opened.profile,
                opened.legacy_response,
                started=started,
            )
        except _EvaluationFailure as failure:
            observation = _empty_observation(
                identity,
                status="error",
                total_latency_ms=_elapsed_ms(started),
                failures=(CodeShadowFailure(failure.stage, failure.error),),
                drop_reason="shadow_failed",
            )
        except BaseException:
            observation = _empty_observation(
                identity,
                status="error",
                total_latency_ms=_elapsed_ms(started),
                failures=(
                    CodeShadowFailure(
                        CodeShadowStage.RUNNER,
                        CodeShadowError.RUNNER_FAILED,
                    ),
                ),
                drop_reason="shadow_failed",
            )
        if self._finish_job(job, observation):
            self._cancel_timer(job.job_id)
        # A timed-out daemon may finish later; admission remains bounded until it does.
        state.slots.release()

    def _evaluate(
        self,
        identity: _ObservationIdentity,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        legacy_response: Mapping[str, Any],
        *,
        started: float,
    ) -> CodeShadowObservation:
        state = self._state()
        adapter_started = time.perf_counter()
        try:
            adapted = self.adapter.adapt_response(request, legacy_response, profile=profile)
        except BaseException:
            raise _EvaluationFailure(
                CodeShadowStage.ADAPTER,
                CodeShadowError.ADAPTER_FAILED,
            ) from None
        adapter_latency_ms = _elapsed_ms(adapter_started)
        legacy_ids = _legacy_entity_ids(legacy_response)
        v1_ids = tuple(candidate.entity_id for candidate in adapted.candidates)

        v2_result: CodeSourceResult | None = None
        v2_latency_ms: float | None = None
        if self.v2 is None:
            comparison_engine = "adapter-validation"
            comparison_ids = legacy_ids
            status = "adapter_validated"
        else:
            comparison_engine = "v2"
            v2_started = time.perf_counter()
            try:
                v2_result = self.v2.search(_copy_request(request), profile)
            except BaseException:
                raise _EvaluationFailure(
                    CodeShadowStage.V2,
                    CodeShadowError.V2_FAILED,
                ) from None
            v2_latency_ms = _elapsed_ms(v2_started)
            if not isinstance(v2_result, CodeSourceResult):
                raise _EvaluationFailure(
                    CodeShadowStage.V2,
                    CodeShadowError.V2_INVALID_RESULT,
                )
            comparison_ids = tuple(candidate.entity_id for candidate in v2_result.candidates)
            status = "compared"

        (
            jaccard,
            v1_recall,
            comparison_recall,
            deltas,
            v1_unique,
            comparison_unique,
        ) = _compare_rankings(
            state.telemetry_key,
            v1_ids,
            comparison_ids,
            self.top_k,
        )
        v1_wrong_version = sum(
            candidate.version_alignment is CodeVersionAlignment.MISMATCH
            for candidate in adapted.candidates
        )
        v2_wrong_version = (
            None
            if v2_result is None
            else sum(
                candidate.version_alignment is CodeVersionAlignment.MISMATCH
                for candidate in v2_result.candidates
            )
        )
        generation_hashes = {
            _keyed_hash(
                state.telemetry_key,
                b"generation",
                candidate.source_generation.encode("utf-8"),
            )
            for candidate in adapted.candidates[: self.top_k]
        }
        if v2_result is not None:
            generation_hashes.update(
                _keyed_hash(
                    state.telemetry_key,
                    b"generation",
                    candidate.source_generation.encode("utf-8"),
                )
                for candidate in v2_result.candidates[: self.top_k]
            )
        return CodeShadowObservation(
            query_hash=identity.query_hash,
            query_id_hash=_keyed_hash(
                state.telemetry_key,
                b"query-id",
                adapted.query_id.encode("utf-8"),
            ),
            profile_hash=identity.profile_hash,
            status=status,
            comparison_engine=comparison_engine,
            v1_entity_hashes=tuple(
                _keyed_hash(
                    state.telemetry_key,
                    b"entity",
                    entity_id.encode("utf-8"),
                )
                for entity_id in v1_unique[: self.top_k]
            ),
            comparison_entity_hashes=tuple(
                _keyed_hash(
                    state.telemetry_key,
                    b"entity",
                    entity_id.encode("utf-8"),
                )
                for entity_id in comparison_unique[: self.top_k]
            ),
            top_k_jaccard=jaccard,
            v1_top_k_recall=v1_recall,
            comparison_top_k_recall=comparison_recall,
            rank_deltas=deltas,
            legacy_result_count=len(legacy_ids),
            adapter_result_count=len(v1_ids),
            adapter_unique_result_count=len(v1_unique),
            v2_result_count=None if v2_result is None else len(comparison_ids),
            v2_unique_result_count=None if v2_result is None else len(comparison_unique),
            legacy_latency_ms=adapted.latency_ms,
            adapter_latency_ms=adapter_latency_ms,
            v2_latency_ms=v2_latency_ms,
            total_latency_ms=_elapsed_ms(started),
            wrong_version_count=v1_wrong_version,
            v2_wrong_version_count=v2_wrong_version,
            generation_hashes=tuple(sorted(generation_hashes)),
            failures=(),
            drop_reason="none",
        )

    def _expire_job(
        self,
        job: _ShadowJob,
        identity: _ObservationIdentity,
    ) -> None:
        self._finish_job(
            job,
            _empty_observation(
                identity,
                status="timeout",
                total_latency_ms=self.timeout_ms,
                failures=(
                    CodeShadowFailure(
                        CodeShadowStage.TIMEOUT,
                        CodeShadowError.TIME_BUDGET_EXHAUSTED,
                    ),
                ),
                drop_reason="time_budget_exhausted",
            ),
        )
        self._drop_timer(job.job_id)

    def _finish_job(
        self,
        job: _ShadowJob,
        observation: CodeShadowObservation,
    ) -> bool:
        state = self._state()
        with job.lock:
            if job.terminal:
                return False
            job.terminal = True
        with state.condition:
            state.sealed_jobs.discard(job.job_id)
            if state.pending <= 0:
                state.condition.notify_all()
                return False
            state.observations.append(observation)
            state.pending -= 1
            state.condition.notify_all()
        return True

    def _append_observation(self, observation: CodeShadowObservation) -> None:
        state = self._state()
        with state.condition:
            state.observations.append(observation)
            state.condition.notify_all()

    def _cancel_timer(self, job_id: str) -> None:
        state = self._state()
        with state.condition:
            timer = state.timers.pop(job_id, None)
        if timer is not None:
            timer.cancel()

    def _drop_timer(self, job_id: str) -> None:
        state = self._state()
        with state.condition:
            state.timers.pop(job_id, None)

    def _control_identity(
        self,
        reason: bytes,
        legacy_response: object,
    ) -> _ObservationIdentity:
        state = self._state()
        result_count = _bounded_result_count(legacy_response)
        return _ObservationIdentity(
            query_hash=_keyed_hash(state.telemetry_key, b"control-query", reason),
            query_id_hash=_keyed_hash(state.telemetry_key, b"control-query-id", reason),
            profile_hash=_keyed_hash(state.telemetry_key, b"control-profile", reason),
            legacy_result_count=result_count,
        )


def timeout_ms_to_seconds(timeout_ms: float) -> float:
    return timeout_ms / 1_000.0


def _validate_timeout(value: object, *, positive: bool, field: str) -> None:
    if type(value) is not float or not math.isfinite(value):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{field} must be an exact finite {qualifier} float")
    if (positive and value <= 0.0) or (not positive and value < 0.0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{field} must be an exact finite {qualifier} float")


def _copy_request(request: EvidenceSearchRequest) -> EvidenceSearchRequest:
    if not isinstance(request, EvidenceSearchRequest):
        raise TypeError("request must be an EvidenceSearchRequest")
    scope = SearchScope(
        **request.scope.model_dump(mode="python"),
        allowed_acl_refs=list(request.scope.allowed_acl_refs),
        enforce_acl=request.scope.enforce_acl,
    )
    return EvidenceSearchRequest(
        query=request.query,
        scope=scope,
        limit=request.limit,
        include_edges=request.include_edges,
    )


def _request_from_payload(value: object) -> EvidenceSearchRequest:
    if not isinstance(value, dict) or set(value) != {
        "query",
        "scope",
        "limit",
        "include_edges",
    }:
        raise TypeError
    scope_value = value["scope"]
    if not isinstance(scope_value, dict):
        raise TypeError
    scope_payload = dict(scope_value)
    allowed_acl_refs = scope_payload.pop("allowed_acl_refs")
    enforce_acl = scope_payload.pop("enforce_acl")
    scope = SearchScope(
        **scope_payload,
        allowed_acl_refs=allowed_acl_refs,
        enforce_acl=enforce_acl,
    )
    return EvidenceSearchRequest(
        query=value["query"],
        scope=scope,
        limit=value["limit"],
        include_edges=value["include_edges"],
    )


def _execution_evidence(
    legacy_response: Mapping[str, Any],
) -> LegacySearchExecutionEvidence | None:
    if type(legacy_response) is not LegacySearchResponse:
        return None
    evidence = legacy_response.execution_evidence
    return evidence if isinstance(evidence, LegacySearchExecutionEvidence) else None


def _evidence_from_payload(value: object) -> LegacySearchExecutionEvidence:
    if not isinstance(value, dict) or value.get("version") != _EVIDENCE_VERSION:
        raise TypeError
    if set(value) != {
        "version",
        "request_tag",
        "response_tag",
        "channels",
        "attestation",
    }:
        raise TypeError
    channels_value = value["channels"]
    if not isinstance(channels_value, list) or len(channels_value) != 2:
        raise TypeError
    channels: list[LegacyChannelExecutionEvidence] = []
    for channel_value in channels_value:
        if not isinstance(channel_value, dict) or set(channel_value) != {
            "channel",
            "hit_count",
            "returned_ranks",
        }:
            raise TypeError
        ranks_value = channel_value["returned_ranks"]
        if not isinstance(ranks_value, list):
            raise TypeError
        ranks: list[tuple[str, int]] = []
        for item in ranks_value:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not int
            ):
                raise TypeError
            ranks.append((item[0], item[1]))
        if type(channel_value["channel"]) is not str or type(channel_value["hit_count"]) is not int:
            raise TypeError
        channels.append(
            LegacyChannelExecutionEvidence(
                channel=channel_value["channel"],
                hit_count=channel_value["hit_count"],
                returned_ranks=tuple(ranks),
            )
        )
    request_tag = _decode_tag(value["request_tag"])
    response_tag = _decode_tag(value["response_tag"])
    attestation = _decode_tag(value["attestation"])
    return LegacySearchExecutionEvidence(
        request_tag=request_tag,
        response_tag=response_tag,
        channels=tuple(channels),
        attestation=attestation,
    )


def _decode_tag(value: object) -> bytes:
    if type(value) is not str or len(value) != 64:
        raise TypeError
    decoded = bytes.fromhex(value)
    if len(decoded) != 32:
        raise TypeError
    return decoded


def _snapshot_aad(snapshot: _SealedJobEnvelope) -> bytes:
    return _canonical_json_bytes(
        {
            "protocol_version": snapshot.protocol_version,
            "runner_id": snapshot.runner_id,
            "job_id": snapshot.job_id,
            "request_execution_tag": snapshot.request_execution_tag,
            "profile_hash": snapshot.profile_hash,
            "evidence_version": snapshot.evidence_version,
        }
    )


def _preflight_submission(
    request: EvidenceSearchRequest,
    profile: CodeQueryProfile,
    legacy_response: Mapping[str, Any],
    evidence: LegacySearchExecutionEvidence | None,
    *,
    on_visit: Callable[[int], None] | None = None,
) -> _StructureBudget:
    return _build_bounded_submission(
        request,
        profile,
        legacy_response,
        evidence,
        on_visit=on_visit,
    ).budget


def _preflight_json(
    value: object,
    *,
    budget: _StructureBudget | None = None,
    on_visit: Callable[[int], None] | None = None,
) -> _StructureBudget:
    normalizer = _BoundedNormalizer(budget=budget, on_visit=on_visit)
    normalizer.normalize_json(value, depth=0, trusted=False)
    return normalizer.budget


class _SnapshotChanged(ValueError):
    """Caller-owned exact container changed while its one-pass snapshot was taken."""


class _BoundedNormalizer:
    """Single-pass copier for exact builtins with deterministic structural ceilings."""

    def __init__(
        self,
        *,
        budget: _StructureBudget | None = None,
        on_visit: Callable[[int], None] | None = None,
    ) -> None:
        self.budget = budget or _StructureBudget()
        self._on_visit = on_visit

    def _visit(self, depth: int) -> None:
        if self.budget.nodes >= SHADOW_STRUCTURE_MAX_NODES or depth > SHADOW_STRUCTURE_MAX_DEPTH:
            raise _SnapshotOversized
        self.budget.nodes += 1
        if self._on_visit is not None:
            self._on_visit(self.budget.nodes)

    def _require_child_capacity(self, depth: int) -> None:
        if (
            self.budget.nodes >= SHADOW_STRUCTURE_MAX_NODES
            or depth + 1 > SHADOW_STRUCTURE_MAX_DEPTH
        ):
            raise _SnapshotOversized

    def normalize_json(
        self,
        value: object,
        *,
        depth: int,
        trusted: bool,
        max_items: int = SHADOW_CONTAINER_MAX_ITEMS,
    ) -> object:
        self._visit(depth)
        if type(value) is dict:
            return self._normalize_dict(
                value,
                depth=depth,
                trusted=trusted,
                max_items=max_items,
            )
        if type(value) is list:
            return self._normalize_list(
                value,
                depth=depth,
                trusted=trusted,
                max_items=max_items,
            )
        if trusted and type(value) is tuple:
            return self._normalize_tuple(value, depth=depth, max_items=max_items)
        if trusted and isinstance(value, StrEnum):
            enum_value = value.value
            if type(enum_value) is not str:
                raise TypeError
            _account_string(enum_value, self.budget)
            return enum_value
        if type(value) is str:
            _account_string(value, self.budget)
            return value
        if value is None or type(value) in {int, bool}:
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise TypeError
            return value
        if isinstance(value, (Mapping, Sequence, Iterator)):
            raise _SnapshotOversized
        raise TypeError

    def normalize_legacy_response(
        self,
        value: LegacySearchResponse,
        *,
        depth: int,
    ) -> dict[str, Any]:
        self._visit(depth)
        return self._normalize_dict(
            value,
            depth=depth,
            trusted=False,
            max_items=SHADOW_CONTAINER_MAX_ITEMS,
            legacy_root=True,
        )

    def normalize_evidence(
        self,
        evidence: LegacySearchExecutionEvidence,
        *,
        depth: int,
    ) -> dict[str, Any]:
        if type(evidence) is not LegacySearchExecutionEvidence:
            raise TypeError
        channels = evidence.channels
        if type(channels) is not tuple or tuple.__len__(channels) != 2:
            raise TypeError
        raw_channels: list[dict[str, Any]] = []
        for channel_index in range(2):
            channel = tuple.__getitem__(channels, channel_index)
            if type(channel) is not LegacyChannelExecutionEvidence:
                raise TypeError
            ranks = channel.returned_ranks
            if type(ranks) is not tuple:
                raise TypeError
            rank_count = tuple.__len__(ranks)
            if rank_count > SHADOW_RESULTS_MAX_ITEMS:
                raise _SnapshotOversized
            raw_ranks: list[list[object]] = []
            for rank_index in range(rank_count):
                entry = tuple.__getitem__(ranks, rank_index)
                if type(entry) is not tuple or tuple.__len__(entry) != 2:
                    raise TypeError
                raw_ranks.append(
                    [
                        tuple.__getitem__(entry, 0),
                        tuple.__getitem__(entry, 1),
                    ]
                )
            raw_channels.append(
                {
                    "channel": channel.channel,
                    "hit_count": channel.hit_count,
                    "returned_ranks": raw_ranks,
                }
            )
        raw = {
            "version": _EVIDENCE_VERSION,
            "request_tag": _encode_evidence_tag(evidence.request_tag),
            "response_tag": _encode_evidence_tag(evidence.response_tag),
            "channels": raw_channels,
            "attestation": _encode_evidence_tag(evidence.attestation),
        }
        normalized = self.normalize_json(raw, depth=depth, trusted=False)
        if type(normalized) is not dict:
            raise TypeError
        return normalized

    def _normalize_dict(
        self,
        value: dict[str, Any],
        *,
        depth: int,
        trusted: bool,
        max_items: int,
        legacy_root: bool = False,
    ) -> dict[str, Any]:
        if not legacy_root and type(value) is not dict:
            raise TypeError
        if legacy_root and type(value) is not LegacySearchResponse:
            raise TypeError
        captured_length = dict.__len__(value)
        if captured_length > max_items:
            raise _SnapshotOversized
        iterator = iter(dict.items(value))
        normalized: dict[str, Any] = {}
        try:
            for _ in range(captured_length):
                self._require_child_capacity(depth)
                key, item = next(iterator)
                if type(key) is not str:
                    raise TypeError
                _account_string(key, self.budget)
                item_limit = (
                    SHADOW_RESULTS_MAX_ITEMS
                    if legacy_root and key == "results"
                    else SHADOW_CONTAINER_MAX_ITEMS
                )
                normalized[key] = self.normalize_json(
                    item,
                    depth=depth + 1,
                    trusted=trusted,
                    max_items=item_limit,
                )
            try:
                next(iterator)
            except StopIteration:
                pass
            else:
                raise _SnapshotChanged
        except (RuntimeError, StopIteration):
            raise _SnapshotChanged from None
        if dict.__len__(value) != captured_length:
            raise _SnapshotChanged
        return normalized

    def _normalize_list(
        self,
        value: list[object],
        *,
        depth: int,
        trusted: bool,
        max_items: int,
    ) -> list[object]:
        if type(value) is not list:
            raise TypeError
        captured_length = list.__len__(value)
        if captured_length > max_items:
            raise _SnapshotOversized
        normalized: list[object] = []
        try:
            for index in range(captured_length):
                self._require_child_capacity(depth)
                normalized.append(
                    self.normalize_json(
                        list.__getitem__(value, index),
                        depth=depth + 1,
                        trusted=trusted,
                    )
                )
        except (IndexError, RuntimeError):
            raise _SnapshotChanged from None
        if list.__len__(value) != captured_length:
            raise _SnapshotChanged
        return normalized

    def _normalize_tuple(
        self,
        value: tuple[object, ...],
        *,
        depth: int,
        max_items: int,
    ) -> list[object]:
        captured_length = tuple.__len__(value)
        if captured_length > max_items:
            raise _SnapshotOversized
        normalized: list[object] = []
        for index in range(captured_length):
            self._require_child_capacity(depth)
            normalized.append(
                self.normalize_json(
                    tuple.__getitem__(value, index),
                    depth=depth + 1,
                    trusted=True,
                )
            )
        return normalized


def _build_bounded_submission(
    request: EvidenceSearchRequest,
    profile: CodeQueryProfile,
    legacy_response: Mapping[str, Any],
    evidence: LegacySearchExecutionEvidence | None,
    *,
    on_visit: Callable[[int], None] | None = None,
) -> _NormalizedSubmission:
    """Build the sole private snapshot before identity, JSON, or AEAD processing."""

    if type(request) is not EvidenceSearchRequest or type(request.scope) is not SearchScope:
        raise TypeError
    if type(profile) is not CodeQueryProfile:
        raise TypeError
    if type(legacy_response) is not LegacySearchResponse:
        if isinstance(legacy_response, (Mapping, Sequence, Iterator)):
            raise _SnapshotOversized
        raise TypeError
    if dict.__len__(legacy_response) > SHADOW_CONTAINER_MAX_ITEMS:
        raise _SnapshotOversized
    results = dict.get(legacy_response, "results")
    if type(results) is not list:
        if isinstance(results, (Mapping, Sequence, Iterator)):
            raise _SnapshotOversized
        raise TypeError
    if list.__len__(results) > SHADOW_RESULTS_MAX_ITEMS:
        raise _SnapshotOversized
    actual_evidence = legacy_response.execution_evidence
    if (
        type(actual_evidence) is not LegacySearchExecutionEvidence
        or evidence is not actual_evidence
    ):
        raise TypeError
    if type(actual_evidence.channels) is not tuple:
        raise TypeError
    if tuple.__len__(actual_evidence.channels) != 2:
        raise TypeError
    for channel_index in range(2):
        channel = tuple.__getitem__(actual_evidence.channels, channel_index)
        if (
            type(channel) is not LegacyChannelExecutionEvidence
            or type(channel.returned_ranks) is not tuple
        ):
            raise TypeError
        if tuple.__len__(channel.returned_ranks) > SHADOW_RESULTS_MAX_ITEMS:
            raise _SnapshotOversized

    scope = request.scope
    request_view = {
        "query": request.query,
        "scope": {
            "project_id": scope.project_id,
            "repository_ids": scope.repository_ids,
            "commit": scope.commit,
            "languages": scope.languages,
            "entity_types": scope.entity_types,
            "branch": scope.branch,
            "experiment_ids": scope.experiment_ids,
            "document_ids": scope.document_ids,
            "thread_ids": scope.thread_ids,
            "date_from": scope.date_from,
            "date_to": scope.date_to,
            "source_types": scope.source_types,
            "allowed_acl_refs": scope.allowed_acl_refs,
            "enforce_acl": scope.enforce_acl,
        },
        "limit": request.limit,
        "include_edges": request.include_edges,
    }
    budget = profile.budget
    profile_view = {
        "profile_version": profile.profile_version,
        "task": profile.task,
        "target_identifiers": profile.target_identifiers,
        "target_paths": profile.target_paths,
        "target_ref": profile.target_ref,
        "directions": profile.directions,
        "edge_types": profile.edge_types,
        "max_hops": profile.max_hops,
        "require_tests": profile.require_tests,
        "include_history": profile.include_history,
        "budget": {
            "total_candidates": budget.total_candidates,
            "exact_candidates": budget.exact_candidates,
            "sparse_candidates": budget.sparse_candidates,
            "dense_candidates": budget.dense_candidates,
            "graph_candidates": budget.graph_candidates,
            "history_candidates": budget.history_candidates,
            "test_candidates": budget.test_candidates,
            "graph_node_budget": budget.graph_node_budget,
            "graph_edge_budget": budget.graph_edge_budget,
            "context_token_budget": budget.context_token_budget,
        },
    }

    normalizer = _BoundedNormalizer(on_visit=on_visit)
    normalizer._visit(0)
    envelope: dict[str, Any] = {}
    for key in (
        "protocol_version",
        "request",
        "profile",
        "legacy_response",
        "execution_evidence",
    ):
        _account_string(key, normalizer.budget)
    envelope["protocol_version"] = normalizer.normalize_json(
        _PROTOCOL_VERSION,
        depth=1,
        trusted=False,
    )
    envelope["request"] = normalizer.normalize_json(
        request_view,
        depth=1,
        trusted=True,
    )
    envelope["profile"] = normalizer.normalize_json(
        profile_view,
        depth=1,
        trusted=True,
    )
    envelope["legacy_response"] = normalizer.normalize_legacy_response(
        legacy_response,
        depth=1,
    )
    envelope["execution_evidence"] = normalizer.normalize_evidence(
        actual_evidence,
        depth=1,
    )
    plaintext = _canonical_json_bytes(envelope)
    if len(plaintext) > SHADOW_ENVELOPE_MAX_BYTES:
        raise _SnapshotOversized
    return _NormalizedSubmission(
        envelope=envelope,
        plaintext=plaintext,
        budget=normalizer.budget,
        request_execution_tag=actual_evidence.request_tag.hex(),
        evidence_version=_EVIDENCE_VERSION,
    )


def _encode_evidence_tag(value: object) -> str:
    if type(value) is not bytes or len(value) != 32:
        raise TypeError
    return value.hex()


def _account_string(value: str, budget: _StructureBudget) -> None:
    if len(value) > SHADOW_STRING_MAX_CHARS:
        raise _SnapshotOversized
    budget.string_chars += len(value)
    if budget.string_chars > SHADOW_TOTAL_STRING_MAX_CHARS:
        raise _SnapshotOversized
    budget.string_bytes += len(value.encode("utf-8"))
    if budget.string_bytes > SHADOW_TOTAL_STRING_MAX_BYTES:
        raise _SnapshotOversized


def _freeze_json(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _observation_identity(
    key: bytes,
    normalized: _NormalizedSubmission,
) -> _ObservationIdentity:
    request = normalized.envelope["request"]
    profile = normalized.envelope["profile"]
    legacy_response = normalized.envelope["legacy_response"]
    if type(request) is not dict or type(profile) is not dict:
        raise TypeError
    if type(legacy_response) is not dict:
        raise TypeError
    query = request.get("query")
    query_id = legacy_response.get("query_id")
    results = legacy_response.get("results")
    if type(query) is not str or type(results) is not list:
        raise TypeError
    return _ObservationIdentity(
        query_hash=_keyed_hash(key, b"query", query.encode("utf-8")),
        query_id_hash=_keyed_hash(
            key,
            b"query-id",
            (query_id if type(query_id) is str else "").encode("utf-8"),
        ),
        profile_hash=_keyed_hash(key, b"profile", _canonical_json_bytes(profile)),
        legacy_result_count=list.__len__(results),
    )


def _bounded_result_count(value: object) -> int:
    if type(value) is not LegacySearchResponse:
        return 0
    results = dict.get(value, "results")
    if type(results) is not list:
        return 0
    return min(list.__len__(results), SHADOW_RESULTS_MAX_ITEMS + 1)


def _legacy_entity_ids(legacy_response: Mapping[str, Any]) -> tuple[str, ...]:
    results = legacy_response["results"]
    if isinstance(results, (str, bytes)) or not isinstance(results, Sequence):
        raise TypeError
    entity_ids = []
    for item in results:
        if not isinstance(item, Mapping) or type(item.get("entity_id")) is not str:
            raise TypeError
        entity_ids.append(item["entity_id"])
    return tuple(entity_ids)


def _deduplicate_first(entity_ids: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(entity_ids))


def _compare_rankings(
    key: bytes,
    v1_ids: tuple[str, ...],
    comparison_ids: tuple[str, ...],
    top_k: int,
) -> tuple[
    float,
    float,
    float,
    tuple[CodeShadowRankDelta, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    v1_unique = _deduplicate_first(v1_ids)
    comparison_unique = _deduplicate_first(comparison_ids)
    v1_top = v1_unique[:top_k]
    comparison_top = comparison_unique[:top_k]
    v1_set = set(v1_top)
    comparison_set = set(comparison_top)
    intersection = v1_set & comparison_set
    union = v1_set | comparison_set
    jaccard = 1.0 if not union else len(intersection) / float(len(union))
    v1_recall = 1.0 if not v1_set else len(intersection) / float(len(v1_set))
    comparison_recall = (
        1.0 if not comparison_set else len(intersection) / float(len(comparison_set))
    )
    v1_ranks = {entity_id: rank for rank, entity_id in enumerate(v1_top)}
    comparison_ranks = {entity_id: rank for rank, entity_id in enumerate(comparison_top)}
    deltas = tuple(
        CodeShadowRankDelta(
            entity_hash=_keyed_hash(
                key,
                b"entity",
                entity_id.encode("utf-8"),
            ),
            v1_rank=v1_ranks[entity_id],
            comparison_rank=comparison_ranks[entity_id],
            delta=comparison_ranks[entity_id] - v1_ranks[entity_id],
        )
        for entity_id in v1_top
        if entity_id in comparison_ranks
    )
    return (
        round(jaccard, 6),
        round(v1_recall, 6),
        round(comparison_recall, 6),
        deltas,
        v1_unique,
        comparison_unique,
    )


def _empty_observation(
    identity: _ObservationIdentity,
    *,
    status: str,
    drop_reason: str,
    total_latency_ms: float = 0.0,
    failures: tuple[CodeShadowFailure, ...] = (),
) -> CodeShadowObservation:
    return CodeShadowObservation(
        query_hash=identity.query_hash,
        query_id_hash=identity.query_id_hash,
        profile_hash=identity.profile_hash,
        status=status,
        comparison_engine="adapter-validation",
        v1_entity_hashes=(),
        comparison_entity_hashes=(),
        top_k_jaccard=0.0,
        v1_top_k_recall=0.0,
        comparison_top_k_recall=0.0,
        rank_deltas=(),
        legacy_result_count=identity.legacy_result_count,
        adapter_result_count=0,
        adapter_unique_result_count=0,
        v2_result_count=None,
        v2_unique_result_count=None,
        legacy_latency_ms=0.0,
        adapter_latency_ms=0.0,
        v2_latency_ms=None,
        total_latency_ms=total_latency_ms,
        wrong_version_count=0,
        v2_wrong_version_count=None,
        generation_hashes=(),
        failures=failures,
        drop_reason=drop_reason,
    )


def _keyed_hash(key: bytes, namespace: bytes, payload: bytes) -> str:
    digest = hmac.digest(key, namespace + b"\0" + payload, "sha256")
    return f"hmac-sha256:{digest.hex()}"


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1_000.0, 3)
