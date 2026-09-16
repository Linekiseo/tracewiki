from __future__ import annotations

import copy
import copyreg
import gc
import io
import math
import pickle
import threading
import time
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import asdict, fields, is_dataclass, replace
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.models import EvidenceSearchRequest, SearchScope
from evidence_rag.platform.models import GlobalSearchRequest
from evidence_rag.platform.service import PlatformService
from evidence_rag.rag.sources.code import (
    CodeQueryProfile,
    CodeShadowError,
    CodeShadowRunner,
    CodeShadowStage,
    CodeSourceRetrieverV1Adapter,
)
from evidence_rag.rag.sources.code import shadow as shadow_module
from evidence_rag.rag.sources.code.shadow import (
    SHADOW_ENVELOPE_MAX_BYTES,
    SHADOW_RESULTS_MAX_ITEMS,
    SHADOW_STRUCTURE_MAX_DEPTH,
    SHADOW_STRUCTURE_MAX_NODES,
    _build_bounded_submission,
    _compare_rankings,
    _EvaluationFailure,
    _observation_identity,
    _preflight_json,
    _preflight_submission,
)
from evidence_rag.retrieval import HybridRetriever, LegacySearchResponse


class _LegacyRetriever(HybridRetriever):
    def __init__(self) -> None:
        super().__init__(store=None, embedder=None)  # type: ignore[arg-type]
        self.requests: list[EvidenceSearchRequest] = []

    def search(self, request: EvidenceSearchRequest) -> dict[str, Any]:
        self.requests.append(request)
        return self.executed(request)

    def executed(
        self,
        request: EvidenceSearchRequest,
        response: dict[str, Any] | None = None,
    ) -> LegacySearchResponse:
        payload = copy.deepcopy(response if response is not None else _legacy_response(request))
        return self._attest_response(
            request,
            payload,
            channel_entity_ids={
                "lexical": [
                    item["entity_id"]
                    for item in payload["results"]
                    if "lexical" in item["channels"]
                ],
                "dense": [
                    item["entity_id"] for item in payload["results"] if "dense" in item["channels"]
                ],
            },
        )


class _UnusedCodex:
    def search(self, request) -> dict[str, Any]:
        raise AssertionError("Codex must not run for a code-only request")


class _PlatformStore:
    def structured_search(self, *args, **kwargs) -> list[dict[str, Any]]:
        return []

    def entity_scopes(self, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {entity_id: {"repository_id": "repository://alpha"} for entity_id in entity_ids}

    def edges_for(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        return []

    def resolve_nodes(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        return []


class _SlowAdapter(CodeSourceRetrieverV1Adapter):
    def __init__(self, legacy: _LegacyRetriever, delay: float) -> None:
        super().__init__(legacy)
        self.delay = delay
        self.adapt_calls = 0

    def adapt_response(self, request, legacy_response, *, profile=None):
        self.adapt_calls += 1
        time.sleep(self.delay)
        return super().adapt_response(request, legacy_response, profile=profile)


class _ExplodingAdapter(CodeSourceRetrieverV1Adapter):
    def adapt_response(self, request, legacy_response, *, profile=None):
        raise RuntimeError("never persist query plaintext or ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")


class _InspectingAdapter(CodeSourceRetrieverV1Adapter):
    def __init__(
        self,
        legacy: _LegacyRetriever,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(legacy)
        self.entered = entered
        self.release = release
        self.read_only = False

    def adapt_response(self, request, legacy_response, *, profile=None):
        try:
            legacy_response["query"] = "mutated"
        except TypeError:
            self.read_only = True
        self.entered.set()
        self.release.wait(0.4)
        return super().adapt_response(request, legacy_response, profile=profile)


class _RaisingSubmit:
    def __init__(self, adapter: CodeSourceRetrieverV1Adapter) -> None:
        self.adapter = adapter

    def submit(self, request, profile, response) -> bool:
        raise RuntimeError("shadow dispatch failed")


_COPYREG_ADAPTER: CodeSourceRetrieverV1Adapter | None = None


def _construct_copyreg_runner() -> CodeShadowRunner:
    if _COPYREG_ADAPTER is None:
        raise RuntimeError("copyreg test adapter is unavailable")
    return CodeShadowRunner(_COPYREG_ADAPTER, timeout_ms=500.0)


class _EqualShadowRunner(CodeShadowRunner):
    hash_calls = 0
    equality_calls = 0

    def __hash__(self) -> int:
        type(self).hash_calls += 1
        return 1

    def __eq__(self, other: object) -> bool:
        type(self).equality_calls += 1
        return isinstance(other, _EqualShadowRunner)


def _request(*, commit: str | None = None, query: str = "rank implementation"):
    return EvidenceSearchRequest(
        query=query,
        scope=SearchScope(
            project_id="project-alpha",
            repository_ids=["repository://alpha"],
            commit=commit,
            allowed_acl_refs=["team:alpha"],
            enforce_acl=True,
        ),
        limit=8,
        include_edges=False,
    )


def _profile(request: EvidenceSearchRequest) -> CodeQueryProfile:
    return CodeQueryProfile(
        task="implementation",
        target_ref=request.scope.commit or "current",
    )


def _sealed_job(
    runner: CodeShadowRunner,
    request: EvidenceSearchRequest,
    response: LegacySearchResponse,
):
    profile = _profile(request)
    evidence = response.execution_evidence
    normalized = _build_bounded_submission(request, profile, response, evidence)
    state = shadow_module._runner_state(runner)
    identity = _observation_identity(
        state.telemetry_key,
        normalized,
    )
    job_id = runner._next_job_id()
    snapshot = runner._seal_submission(
        job_id,
        normalized,
        identity.profile_hash,
    )
    runner._register_probe_job(job_id)
    return job_id, snapshot


def _flip_first_byte(value: bytes) -> bytes:
    return bytes([value[0] ^ 1]) + value[1:]


def _change_last_character(value: str) -> str:
    replacement = "0" if value[-1] != "0" else "1"
    return value[:-1] + replacement


def _queued_object_graph_text(root: object) -> str:
    stack = [root]
    seen: set[int] = set()
    fragments: list[str] = []
    while stack:
        value = stack.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(value, str):
            fragments.append(value)
        elif isinstance(value, bytes):
            fragments.append(repr(value))
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, (tuple, list, set, frozenset)):
            stack.extend(value)
        elif is_dataclass(value) and not isinstance(value, type):
            stack.extend(getattr(value, item.name) for item in fields(value))
        elif callable(value):
            continue
        elif value.__class__.__module__ == "threading" and hasattr(value, "__dict__"):
            stack.extend(vars(value).values())
    return "\n".join(fragments)


class _LyingSequence(Sequence[object]):
    def __init__(self) -> None:
        self.length_calls = 0
        self.iterations = 0
        self.accesses = 0

    def __len__(self) -> int:
        self.length_calls += 1
        return 1

    def __getitem__(self, index: int) -> object:
        self.accesses += 1
        if index >= 10_000:
            raise IndexError
        return index

    def __iter__(self):
        self.iterations += 1
        for index in range(10_000):
            self.accesses += 1
            yield index


class _LyingMapping(Mapping[str, object]):
    def __init__(self) -> None:
        self.length_calls = 0
        self.iterations = 0
        self.accesses = 0

    def __len__(self) -> int:
        self.length_calls += 1
        return 1

    def __iter__(self):
        self.iterations += 1
        for index in range(10_000):
            self.accesses += 1
            yield str(index)

    def __getitem__(self, key: str) -> object:
        self.accesses += 1
        return key


class _FlippingSequence(Sequence[object]):
    def __init__(self, small_item: object) -> None:
        self.small_item = small_item
        self.length_calls = 0
        self.iterations = 0
        self.accesses = 0

    def __len__(self) -> int:
        self.length_calls += 1
        return 1

    def __getitem__(self, index: int) -> object:
        self.accesses += 1
        limit = 1 if self.iterations == 0 else 5_000
        if index >= limit:
            raise IndexError
        return self.small_item

    def __iter__(self):
        self.iterations += 1
        limit = 1 if self.iterations == 1 else 5_000
        for _ in range(limit):
            self.accesses += 1
            yield self.small_item


def _legacy_item(
    entity_id: str,
    *,
    score: float,
    lexical_score: float,
    dense_score: float,
    channels: list[str],
    commit: str,
) -> dict[str, Any]:
    name = entity_id.rsplit("/", 1)[-1]
    return {
        "entity_id": entity_id,
        "repository_id": "repository://alpha",
        "generation_id": "gen-a",
        "entity_type": "CodeSymbol",
        "view_type": "symbol.raw",
        "name": name,
        "qualified_name": f"pkg.{name}",
        "path": f"src/{name}.py",
        "language": "Python",
        "commit": commit,
        "start_line": 1,
        "end_line": 4,
        "evidence_locator": f"code://alpha@{commit}/src/{name}.py#L1-L4",
        "acl_ref": "team:alpha",
        "metadata": {},
        "lexical_score": lexical_score,
        "dense_score": dense_score,
        "score": score,
        "channels": channels,
        "edges": [],
        "snippet": (
            "def rank():\n    token = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456'\n    return token\n"
        ),
    }


def _legacy_response(request: EvidenceSearchRequest) -> dict[str, Any]:
    commit = request.scope.commit or "commit-current"
    results = [
        _legacy_item(
            "code://alpha/rank",
            score=0.9,
            lexical_score=1.0,
            dense_score=0.65,
            channels=["dense", "lexical"],
            commit=commit,
        ),
        _legacy_item(
            "code://alpha/normalize",
            score=0.8,
            lexical_score=0.8,
            dense_score=0.0,
            channels=["lexical"],
            commit=commit,
        ),
    ]
    return {
        "query_id": "q-11111111-2222-4333-8444-555555555555",
        "query": request.query,
        "resolved_scope": request.scope.model_dump(),
        "index_generation": ["gen-a"],
        "total": len(results),
        "results": results,
        "trace": {
            "duration_ms": 7.5,
            "lexical_candidates": 2,
            "dense_candidates": 3,
            "dense_matches": 1,
            "fusion": "weighted-hybrid-v2",
            "embedding_model": "local-hash-v2",
            "queried_embedding_models": ["local-hash-v2"],
        },
    }


def _platform(
    legacy: _LegacyRetriever,
    *,
    adapter: CodeSourceRetrieverV1Adapter | None = None,
    shadow: Any | None = None,
) -> PlatformService:
    return PlatformService(
        _PlatformStore(),
        legacy,
        _UnusedCodex(),
        code_v1_adapter=adapter,
        code_shadow=shadow,
    )


def _global_request(query: str = "rank implementation") -> GlobalSearchRequest:
    return GlobalSearchRequest(
        query=query,
        project_id="project-alpha",
        sources=["code"],
        limit=8,
        include_lineage=False,
        repository_ids=["repository://alpha"],
        allowed_acl_refs=["team:alpha"],
        enforce_acl=True,
    )


def _stable_platform_payload(payload: dict[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(payload)
    stable["query_id"] = "<query-id>"

    def normalize_timing(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith(("duration_ms", "latency_ms")):
                    value[key] = "<duration>"
                else:
                    normalize_timing(item)
        elif isinstance(value, list):
            for item in value:
                normalize_timing(item)

    normalize_timing(stable)
    return stable


def test_shadow_off_does_no_adapter_or_extra_retrieval_and_preserves_json() -> None:
    off_legacy = _LegacyRetriever()
    off_adapter = _SlowAdapter(off_legacy, 0.0)
    off_response = _platform(off_legacy, adapter=off_adapter).search(_global_request())

    on_legacy = _LegacyRetriever()
    on_adapter = CodeSourceRetrieverV1Adapter(on_legacy)
    runner = CodeShadowRunner(on_adapter, timeout_ms=500.0)
    on_response = _platform(on_legacy, adapter=on_adapter, shadow=runner).search(_global_request())
    assert runner.flush(1.0)

    assert len(off_legacy.requests) == 1
    assert off_adapter.adapt_calls == 0
    assert len(on_legacy.requests) == 1
    assert _stable_platform_payload(on_response) == _stable_platform_payload(off_response)
    runner.close()


def test_shadow_enabled_endpoint_is_field_for_field_legacy_compatible(
    settings,
    sample_repository,
) -> None:
    app = create_app(replace(settings, rag_code_shadow=True))
    runtime = app.state.runtime
    assert runtime.code_shadow is not None

    with TestClient(app) as client:
        workflow = client.post(
            "/v1/ingestion/repositories",
            json={"source": str(sample_repository)},
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        runner = runtime.code_shadow
        runtime.platform.code_shadow = None
        baseline = client.post(
            "/v1/search",
            json={
                "query": "HybridRanker rank",
                "sources": ["code"],
                "include_lineage": False,
            },
        ).json()
        assert runner.observations() == ()

        runtime.platform.code_shadow = runner
        shadowed = client.post(
            "/v1/search",
            json={
                "query": "HybridRanker rank",
                "sources": ["code"],
                "include_lineage": False,
            },
        ).json()
        assert runner.flush(1.0)

    assert _stable_platform_payload(shadowed) == _stable_platform_payload(baseline)
    observation = runner.observations()[-1]
    assert observation.status == "adapter_validated"
    assert observation.legacy_result_count == observation.adapter_result_count
    runner.close()


def test_platform_shadow_submission_is_non_blocking_and_never_repeats_legacy() -> None:
    legacy = _LegacyRetriever()
    adapter = _SlowAdapter(legacy, 0.15)
    runner = CodeShadowRunner(adapter, timeout_ms=500.0)
    service = _platform(legacy, adapter=adapter, shadow=runner)

    started = time.perf_counter()
    response = service.search(_global_request())
    elapsed = time.perf_counter() - started

    assert response["results"]
    assert elapsed < 0.1
    assert len(legacy.requests) == 1
    assert runner.flush(1.0)
    assert adapter.adapt_calls == 1
    assert len(legacy.requests) == 1
    runner.close()


def test_shadow_adapter_exception_and_submit_exception_cannot_change_v1_response() -> None:
    request = _request(query="secret plaintext query")
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    adapter = _ExplodingAdapter(legacy)
    runner = CodeShadowRunner(adapter, timeout_ms=500.0)

    assert runner.submit(request, _profile(request), response)
    assert runner.flush(1.0)
    observation = runner.observations()[-1]
    assert observation.status == "error"
    assert [(failure.stage, failure.error) for failure in observation.failures] == [
        (CodeShadowStage.ADAPTER, CodeShadowError.ADAPTER_FAILED)
    ]
    assert "plaintext" not in repr(asdict(observation))
    assert "ghp_" not in repr(asdict(observation))

    normal = _LegacyRetriever()
    normal_adapter = CodeSourceRetrieverV1Adapter(normal)
    service = _platform(
        normal,
        adapter=normal_adapter,
        shadow=_RaisingSubmit(normal_adapter),
    )
    visible = service.search(_global_request())
    assert visible["results"]
    assert len(normal.requests) == 1
    runner.close()


def test_shadow_timeout_drop_and_observations_are_bounded() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    adapter = _SlowAdapter(legacy, 0.2)
    runner = CodeShadowRunner(
        adapter,
        max_inflight=1,
        timeout_ms=25.0,
        max_observations=3,
    )

    assert runner.submit(request, _profile(request), response) is True
    assert runner.submit(request, _profile(request), response) is False
    assert runner.flush(0.2)
    statuses = {(item.status, item.drop_reason) for item in runner.observations()}
    assert ("timeout", "time_budget_exhausted") in statuses
    assert ("dropped", "inflight_budget_exhausted") in statuses

    runner.close()
    for _ in range(8):
        assert runner.submit(request, _profile(request), response) is False
    assert len(runner.observations()) == 3
    assert all(item.drop_reason == "runner_closed" for item in runner.observations())


def test_shadow_observation_is_redacted_and_reports_generation_version_rank_overlap() -> None:
    secret = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    request = _request(commit="commit-requested", query=f"find {secret} rank")
    raw_response = _legacy_response(request)
    raw_response["results"][1]["commit"] = "commit-wrong"
    raw_response["results"][1]["evidence_locator"] = (
        "code://alpha@commit-wrong/src/normalize.py#L1-L4"
    )
    legacy = _LegacyRetriever()
    response = legacy.executed(request, raw_response)
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    runner = CodeShadowRunner(adapter, max_observations=2, timeout_ms=500.0)

    observation = runner.probe(request, _profile(request), response)
    serialized = repr(asdict(observation))

    assert observation.status == "adapter_validated"
    assert observation.query_hash.startswith("hmac-sha256:")
    assert observation.query_id_hash.startswith("hmac-sha256:")
    assert observation.legacy_result_count == 2
    assert observation.adapter_result_count == 2
    assert observation.v2_result_count is None
    assert observation.top_k_jaccard == 1.0
    assert [delta.delta for delta in observation.rank_deltas] == [0, 0]
    assert observation.wrong_version_count == 1
    assert len(observation.generation_hashes) == 1
    assert observation.generation_hashes[0].startswith("hmac-sha256:")
    assert "gen-a" not in serialized
    assert request.query not in serialized
    assert secret not in serialized
    assert response["results"][0]["snippet"] not in serialized
    assert response["results"][0]["entity_id"] not in serialized
    assert response["results"][0]["evidence_locator"] not in serialized
    runner.close()


def test_submit_gives_shadow_a_read_only_snapshot_isolated_from_caller_mutation() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    original_entity = response["results"][0]["entity_id"]
    entered = threading.Event()
    release = threading.Event()
    adapter = _InspectingAdapter(legacy, entered, release)
    runner = CodeShadowRunner(adapter, timeout_ms=500.0)

    assert runner.submit(request, _profile(request), response)
    assert entered.wait(0.2)
    response["results"][0]["entity_id"] = "code://alpha/caller-mutated"
    response["results"][0]["snippet"] = "caller-mutated plaintext"
    release.set()
    assert runner.flush(1.0)

    observation = runner.observations()[-1]
    assert adapter.read_only is True
    assert observation.status == "adapter_validated"
    assert observation.v1_entity_hashes[0].startswith("hmac-sha256:")
    assert original_entity not in repr(asdict(observation))
    assert "caller-mutated" not in repr(asdict(observation))
    runner.close()


def test_shadow_hashes_are_stable_per_runner_and_unlinkable_across_runners() -> None:
    request = _request(query="private low entropy query")
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    first_runner = CodeShadowRunner(adapter, timeout_ms=500.0)
    second_runner = CodeShadowRunner(adapter, timeout_ms=500.0)

    first = first_runner.probe(request, _profile(request), response)
    repeated = first_runner.probe(request, _profile(request), response)
    other = second_runner.probe(request, _profile(request), response)

    assert first.query_hash == repeated.query_hash
    assert first.query_id_hash == repeated.query_id_hash
    assert first.profile_hash == repeated.profile_hash
    assert first.v1_entity_hashes == repeated.v1_entity_hashes
    assert first.generation_hashes == repeated.generation_hashes
    assert first.query_hash != other.query_hash
    assert first.query_id_hash != other.query_id_hash
    assert first.profile_hash != other.profile_hash
    assert first.v1_entity_hashes != other.v1_entity_hashes
    assert first.generation_hashes != other.generation_hashes
    assert request.query not in repr(asdict(first))
    first_state = shadow_module._runner_state(first_runner)
    second_state = shadow_module._runner_state(second_runner)
    assert first_state.telemetry_key != second_state.telemetry_key
    assert first_state.snapshot_key != second_state.snapshot_key
    assert first_state.runner_id != second_state.runner_id
    assert (
        first_state.snapshot_key,
        first_state.nonce_prefix,
    ) != (
        second_state.snapshot_key,
        second_state.nonce_prefix,
    )
    first_runner.close()
    second_runner.close()


def test_queued_snapshot_is_opaque_and_contains_no_sensitive_plaintext() -> None:
    request = _request(query="credential token query")
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    job_id, snapshot = _sealed_job(runner, request, response)
    serialized = repr(asdict(snapshot))

    forbidden = (
        request.query,
        response["query_id"],
        response["results"][0]["entity_id"],
        response["results"][0]["generation_id"],
        response["results"][0]["evidence_locator"],
        response["results"][0]["snippet"],
        "ghp_",
        "token",
        "credential",
    )
    assert all(value not in serialized for value in forbidden)
    assert len(snapshot.ciphertext) + len(snapshot.tag) <= SHADOW_ENVELOPE_MAX_BYTES + 16
    runner._open_job(job_id, snapshot)
    runner.close()


@pytest.mark.parametrize(
    "operation",
    [
        copy.copy,
        copy.deepcopy,
        pickle.dumps,
    ],
)
def test_shadow_runner_authority_cannot_be_copied_or_pickled(operation) -> None:
    legacy = _LegacyRetriever()
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )

    with pytest.raises(
        TypeError,
        match="CodeShadowRunner authority cannot be copied or serialized",
    ):
        operation(runner)
    runner.close()


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("__reduce__", ()),
        ("__reduce_ex__", (pickle.HIGHEST_PROTOCOL,)),
        ("__getstate__", ()),
        ("__setstate__", ({"timeout_ms": 500.0},)),
    ],
)
def test_shadow_runner_reduce_and_state_protocols_are_fixed_failures(
    method: str,
    args: tuple[object, ...],
) -> None:
    legacy = _LegacyRetriever()
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    with pytest.raises(
        TypeError,
        match="CodeShadowRunner authority cannot be copied or serialized",
    ):
        getattr(runner, method)(*args)
    runner.close()


def test_strict_identity_registry_ignores_equal_runner_hash_and_equality() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    _EqualShadowRunner.hash_calls = 0
    _EqualShadowRunner.equality_calls = 0
    first = _EqualShadowRunner(adapter, timeout_ms=500.0)
    second = _EqualShadowRunner(adapter, timeout_ms=500.0)

    first_state = shadow_module._runner_state(first)
    second_state = shadow_module._runner_state(second)
    assert first_state is not second_state
    assert first_state.telemetry_key != second_state.telemetry_key
    assert first_state.snapshot_key != second_state.snapshot_key
    assert first_state.condition is not second_state.condition
    assert first_state.slots is not second_state.slots
    assert shadow_module._RUNNER_STATES[id(first)].reference() is first
    assert shadow_module._RUNNER_STATES[id(second)].reference() is second

    first._next_nonce()
    assert first_state.nonce_counter == 1
    assert second_state.nonce_counter == 0
    job_id, snapshot = _sealed_job(first, request, response)
    second_state.sealed_jobs.add(job_id)
    with pytest.raises(_EvaluationFailure) as replay:
        second._open_job(job_id, snapshot)
    assert replay.value.error is CodeShadowError.SNAPSHOT_REPLAYED
    second_state.sealed_jobs.discard(job_id)
    assert first._open_job(job_id, snapshot).request == request

    assert first.submit(request, _profile(request), response)
    assert first.flush(1.0)
    assert len(first.observations()) == 1
    assert second.observations() == ()
    assert _EqualShadowRunner.hash_calls == 0
    assert _EqualShadowRunner.equality_calls == 0
    first.close()
    second.close()


def test_strict_runner_registry_supports_concurrent_registration_and_lookup() -> None:
    legacy = _LegacyRetriever()
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    runners: list[_EqualShadowRunner] = []
    failures: list[BaseException] = []
    output_lock = threading.Lock()

    def construct_and_lookup() -> None:
        try:
            runner = _EqualShadowRunner(adapter, timeout_ms=500.0)
            assert shadow_module._runner_state(runner) is shadow_module._runner_state(runner)
            with output_lock:
                runners.append(runner)
        except BaseException as error:
            with output_lock:
                failures.append(error)

    workers = [threading.Thread(target=construct_and_lookup) for _ in range(32)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert failures == []
    assert len(runners) == 32
    assert len({id(shadow_module._runner_state(runner)) for runner in runners}) == 32
    assert len({shadow_module._runner_state(runner).snapshot_key for runner in runners}) == 32
    for runner in runners:
        runner.close()


def test_runner_registry_is_weak_and_gc_removes_the_exact_entry() -> None:
    legacy = _LegacyRetriever()
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    identity = id(runner)
    reference = weakref.ref(runner)
    assert shadow_module._RUNNER_STATES[identity].reference() is runner

    del runner
    gc.collect()

    assert reference() is None
    assert identity not in shadow_module._RUNNER_STATES


def test_stale_runner_callback_and_unregistered_clone_cannot_remove_or_gain_state() -> None:
    legacy = _LegacyRetriever()
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    old = CodeShadowRunner(adapter, timeout_ms=500.0)
    current = CodeShadowRunner(adapter, timeout_ms=500.0)
    old_reference = shadow_module._RUNNER_STATES[id(old)].reference
    current_entry = shadow_module._RUNNER_STATES[id(current)]

    shadow_module._remove_runner_state(id(current), old_reference)

    assert shadow_module._RUNNER_STATES[id(current)] is current_entry
    assert shadow_module._runner_state(current) is current_entry.state
    clone = CodeShadowRunner.__new__(CodeShadowRunner)
    clone.__dict__.update(current.__dict__)
    assert id(clone) not in shadow_module._RUNNER_STATES
    with pytest.raises(RuntimeError, match="CodeShadowRunner authority is unavailable"):
        shadow_module._runner_state(clone)

    collision = CodeShadowRunner.__new__(CodeShadowRunner)
    collision_identity = id(collision)
    with shadow_module._RUNNER_STATE_LOCK:
        shadow_module._RUNNER_STATES[collision_identity] = shadow_module._RUNNER_STATES[id(old)]
    try:
        with pytest.raises(RuntimeError, match="CodeShadowRunner authority is unavailable"):
            shadow_module._register_runner_state(collision, current_entry.state)
    finally:
        with shadow_module._RUNNER_STATE_LOCK:
            shadow_module._RUNNER_STATES.pop(collision_identity, None)
    old.close()
    current.close()


def test_global_and_local_copyreg_state_restore_cannot_copy_runner_authority() -> None:
    global _COPYREG_ADAPTER

    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    runner = CodeShadowRunner(adapter, timeout_ms=500.0)
    _COPYREG_ADAPTER = adapter
    sentinel = object()
    previous = copyreg.dispatch_table.get(CodeShadowRunner, sentinel)

    def reducer(value: CodeShadowRunner):
        return (
            _construct_copyreg_runner,
            (),
            {
                "max_inflight": value.max_inflight,
                "timeout_ms": value.timeout_ms,
                "top_k": value.top_k,
            },
        )

    try:
        copyreg.pickle(CodeShadowRunner, reducer)
        payload = pickle.dumps(runner)
        with pytest.raises(
            TypeError,
            match="CodeShadowRunner authority cannot be copied or serialized",
        ):
            pickle.loads(payload)
        with pytest.raises(
            TypeError,
            match="CodeShadowRunner authority cannot be copied or serialized",
        ):
            copy.copy(runner)

        target = io.BytesIO()
        pickler = pickle.Pickler(target)
        pickler.dispatch_table = {CodeShadowRunner: reducer}
        pickler.dump(runner)
        with pytest.raises(
            TypeError,
            match="CodeShadowRunner authority cannot be copied or serialized",
        ):
            pickle.loads(target.getvalue())
    finally:
        if previous is sentinel:
            del copyreg.dispatch_table[CodeShadowRunner]
        else:
            copyreg.dispatch_table[CodeShadowRunner] = previous
        _COPYREG_ADAPTER = None

    assert runner.probe(request, _profile(request), response).status == "adapter_validated"
    runner.close()


def test_constructor_only_and_new_state_runner_clones_have_isolated_authority() -> None:
    global _COPYREG_ADAPTER

    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    runner = CodeShadowRunner(adapter, timeout_ms=500.0)
    _COPYREG_ADAPTER = adapter

    def independent_reducer(value: CodeShadowRunner):
        del value
        return _construct_copyreg_runner, ()

    try:
        target = io.BytesIO()
        pickler = pickle.Pickler(target)
        pickler.dispatch_table = {CodeShadowRunner: independent_reducer}
        pickler.dump(runner)
        independent = pickle.loads(target.getvalue())
    finally:
        _COPYREG_ADAPTER = None

    state_copied = CodeShadowRunner(adapter, timeout_ms=500.0)
    state_copied.__dict__.update(runner.__dict__)
    unregistered = CodeShadowRunner.__new__(CodeShadowRunner)
    unregistered.__dict__.update(runner.__dict__)

    original_state = shadow_module._runner_state(runner)
    independent_state = shadow_module._runner_state(independent)
    copied_state = shadow_module._runner_state(state_copied)
    assert independent_state is not original_state
    assert copied_state is not original_state
    assert independent_state.telemetry_key != original_state.telemetry_key
    assert independent_state.snapshot_key != original_state.snapshot_key
    assert independent_state.runner_id != original_state.runner_id
    assert independent_state.nonce_prefix != original_state.nonce_prefix
    assert independent_state.condition is not original_state.condition
    assert independent_state.slots is not original_state.slots

    job_id, snapshot = _sealed_job(runner, request, response)
    independent_state.sealed_jobs.add(job_id)
    with pytest.raises(_EvaluationFailure) as replay:
        independent._open_job(job_id, snapshot)
    assert replay.value.error is CodeShadowError.SNAPSHOT_REPLAYED
    original_opened = runner._open_job(job_id, snapshot)
    assert original_opened.request == request

    assert (
        runner.probe(request, _profile(request), response).query_hash
        != independent.probe(
            request,
            _profile(request),
            response,
        ).query_hash
    )
    with pytest.raises(RuntimeError, match="CodeShadowRunner authority is unavailable"):
        unregistered.observations()
    assert set(runner.__dict__) == {
        "adapter",
        "v2",
        "max_inflight",
        "timeout_ms",
        "max_observations",
        "top_k",
    }
    runner.close()
    independent.close()
    state_copied.close()


def test_actual_worker_and_timer_args_are_recursively_opaque(monkeypatch) -> None:
    request = EvidenceSearchRequest(
        query="credential token query",
        scope=SearchScope(
            project_id="project-secret",
            repository_ids=["repository://secret"],
            commit="commit-secret",
            allowed_acl_refs=["team:secret"],
            enforce_acl=True,
        ),
        limit=8,
        include_edges=False,
    )
    legacy = _LegacyRetriever()
    raw_response = _legacy_response(request)
    for item in raw_response["results"]:
        item["repository_id"] = "repository://secret"
        item["acl_ref"] = "team:secret"
        item["entity_id"] = item["entity_id"].replace("alpha", "secret")
        item["evidence_locator"] = item["evidence_locator"].replace("alpha", "secret")
    response = legacy.executed(request, raw_response)
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    captured: dict[str, object] = {}
    original_start = threading.Thread.start

    def intercept_start(thread: threading.Thread) -> None:
        if thread.name == "code-shadow":
            captured["worker"] = thread._args
        elif isinstance(thread, threading.Timer):
            captured["timer"] = thread._args
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", intercept_start)
    assert runner.submit(request, _profile(request), response)
    assert runner.flush(1.0)

    queued = _queued_object_graph_text((captured["worker"], captured["timer"]))
    forbidden = (
        request.query,
        "project-secret",
        "repository://secret",
        "commit-secret",
        "team:secret",
        response["results"][0]["entity_id"],
        response["results"][0]["generation_id"],
        response["results"][0]["evidence_locator"],
        response["results"][0]["snippet"],
        "ghp_",
    )
    assert all(value not in queued for value in forbidden)
    assert runner.pending == 0
    assert shadow_module._runner_state(runner).sealed_jobs == set()
    assert shadow_module._runner_state(runner).timers == {}
    runner.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda snapshot: replace(
            snapshot,
            ciphertext=bytes([snapshot.ciphertext[0] ^ 1]) + snapshot.ciphertext[1:],
        ),
        lambda snapshot: replace(
            snapshot,
            tag=snapshot.tag[:-1] + bytes([snapshot.tag[-1] ^ 1]),
        ),
        lambda snapshot: replace(snapshot, nonce=_flip_first_byte(snapshot.nonce)),
        lambda snapshot: replace(
            snapshot,
            profile_hash=_change_last_character(snapshot.profile_hash),
        ),
        lambda snapshot: replace(
            snapshot,
            request_execution_tag=_change_last_character(snapshot.request_execution_tag),
        ),
        lambda snapshot: replace(snapshot, evidence_version=snapshot.evidence_version + 1),
        lambda snapshot: replace(snapshot, protocol_version=snapshot.protocol_version + 1),
    ],
)
def test_aead_tamper_and_wrong_aad_fail_closed_and_consume_job(mutate) -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    state = shadow_module._runner_state(runner)
    state.nonce_prefix = b"X" + state.nonce_prefix[1:]
    job_id, snapshot = _sealed_job(runner, request, response)
    assert snapshot.nonce[0] == 0x58

    with pytest.raises(_EvaluationFailure) as caught:
        runner._open_job(job_id, mutate(snapshot))
    assert caught.value.stage is CodeShadowStage.SNAPSHOT
    assert caught.value.error is CodeShadowError.SNAPSHOT_AUTHENTICATION_FAILED

    with pytest.raises(_EvaluationFailure) as replay:
        runner._open_job(job_id, snapshot)
    assert replay.value.error is CodeShadowError.SNAPSHOT_REPLAYED
    assert shadow_module._runner_state(runner).sealed_jobs == set()
    runner.close()


def test_nonce_tamper_is_deterministic_for_every_possible_prefix_first_byte() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    state = shadow_module._runner_state(runner)

    for first_byte in range(256):
        state.nonce_prefix = bytes([first_byte]) + state.nonce_prefix[1:]
        job_id, snapshot = _sealed_job(runner, request, response)
        assert snapshot.nonce[0] == first_byte
        tampered = replace(snapshot, nonce=_flip_first_byte(snapshot.nonce))
        assert tampered.nonce != snapshot.nonce
        with pytest.raises(_EvaluationFailure) as caught:
            runner._open_job(job_id, tampered)
        assert caught.value.error is CodeShadowError.SNAPSHOT_AUTHENTICATION_FAILED

    assert state.sealed_jobs == set()
    runner.close()


def test_sealed_job_is_one_shot_and_rejects_cross_job_and_cross_runner_replay() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    runner = CodeShadowRunner(adapter, timeout_ms=500.0)
    other = CodeShadowRunner(adapter, timeout_ms=500.0)

    job_id, snapshot = _sealed_job(runner, request, response)
    opened = runner._open_job(job_id, snapshot)
    assert opened.request == request
    with pytest.raises(_EvaluationFailure) as replay:
        runner._open_job(job_id, snapshot)
    assert replay.value.error is CodeShadowError.SNAPSHOT_REPLAYED

    cross_job_id = runner._next_job_id()
    runner._register_probe_job(cross_job_id)
    with pytest.raises(_EvaluationFailure) as cross_job:
        runner._open_job(cross_job_id, snapshot)
    assert cross_job.value.error is CodeShadowError.SNAPSHOT_REPLAYED
    shadow_module._runner_state(runner).sealed_jobs.discard(cross_job_id)

    shadow_module._runner_state(other).sealed_jobs.add(job_id)
    with pytest.raises(_EvaluationFailure) as cross_runner:
        other._open_job(job_id, snapshot)
    assert cross_runner.value.error is CodeShadowError.SNAPSHOT_REPLAYED
    shadow_module._runner_state(other).sealed_jobs.discard(job_id)
    runner.close()
    other.close()


def test_wrong_aead_key_fails_with_fixed_authentication_error() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    job_id, snapshot = _sealed_job(runner, request, response)
    shadow_module._runner_state(runner).snapshot_key = AESGCM.generate_key(bit_length=256)

    with pytest.raises(_EvaluationFailure) as caught:
        runner._open_job(job_id, snapshot)
    assert caught.value.error is CodeShadowError.SNAPSHOT_AUTHENTICATION_FAILED
    runner.close()


def test_nonce_is_monotonic_per_key_and_counter_rollback_fails_closed() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    first_job, first = _sealed_job(runner, request, response)
    runner._open_job(first_job, first)
    second_job, second = _sealed_job(runner, request, response)
    runner._open_job(second_job, second)

    assert first.nonce[:4] == second.nonce[:4]
    assert (
        int.from_bytes(second.nonce[4:], "big")
        == int.from_bytes(
            first.nonce[4:],
            "big",
        )
        + 1
    )
    state = shadow_module._runner_state(runner)
    state.nonce_counter = state.last_nonce_counter
    with pytest.raises(_EvaluationFailure) as reused:
        runner._next_nonce()
    assert reused.value.error is CodeShadowError.SNAPSHOT_NONCE_REUSED
    runner.close()


def test_nonce_and_job_identity_allocation_is_thread_safe() -> None:
    legacy = _LegacyRetriever()
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    nonces: list[bytes] = []
    job_ids: list[str] = []
    output_lock = threading.Lock()

    def allocate() -> None:
        nonce = runner._next_nonce()
        job_id = runner._next_job_id()
        with output_lock:
            nonces.append(nonce)
            job_ids.append(job_id)

    workers = [threading.Thread(target=allocate) for _ in range(32)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert len(nonces) == len(set(nonces)) == 32
    assert len(job_ids) == len(set(job_ids)) == 32
    assert sorted(int.from_bytes(nonce[4:], "big") for nonce in nonces) == list(range(32))
    runner.close()


def test_oversized_submission_drops_before_identity_and_releases_admission(
    monkeypatch,
) -> None:
    request = _request(query="q" * (shadow_module.SHADOW_STRING_MAX_CHARS + 1))
    legacy = _LegacyRetriever()
    oversized = legacy.executed(request)
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        max_inflight=1,
        timeout_ms=500.0,
    )

    def identity_must_not_run(*args, **kwargs):
        raise AssertionError("identity must not process oversized input")

    monkeypatch.setattr(shadow_module, "_observation_identity", identity_must_not_run)
    assert runner.submit(request, _profile(request), oversized) is False
    observation = runner.observations()[-1]
    assert observation.drop_reason == "snapshot_oversized"
    assert observation.failures[0].error is CodeShadowError.SNAPSHOT_OVERSIZED

    monkeypatch.undo()
    valid_request = _request()
    valid_response = legacy.executed(valid_request)
    assert runner.submit(valid_request, _profile(valid_request), valid_response)
    assert runner.flush(1.0)
    assert runner.pending == 0
    assert shadow_module._runner_state(runner).sealed_jobs == set()
    assert shadow_module._runner_state(runner).timers == {}
    runner.close()


def test_dispatch_failure_and_late_timeout_cleanup_are_single_terminal(
    monkeypatch,
) -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    adapter = _SlowAdapter(legacy, 0.08)
    runner = CodeShadowRunner(
        adapter,
        max_inflight=1,
        timeout_ms=10.0,
    )
    original_start = threading.Thread.start

    def fail_worker_start(thread: threading.Thread) -> None:
        if thread.name == "code-shadow":
            raise RuntimeError("dynamic plaintext must not escape")
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_worker_start)
    assert runner.submit(request, _profile(request), response) is False
    assert runner.pending == 0
    assert shadow_module._runner_state(runner).sealed_jobs == set()
    assert shadow_module._runner_state(runner).timers == {}
    dispatch = runner.observations()[-1]
    assert dispatch.failures[0].error is CodeShadowError.DISPATCH_FAILED
    assert "dynamic plaintext" not in repr(asdict(dispatch))

    monkeypatch.undo()
    assert runner.submit(request, _profile(request), response)
    assert runner.flush(0.2)
    timeout = runner.observations()[-1]
    assert timeout.status == "timeout"
    assert timeout.failures[0].error is CodeShadowError.TIME_BUDGET_EXHAUSTED
    time.sleep(0.12)
    assert runner.pending == 0
    assert shadow_module._runner_state(runner).sealed_jobs == set()
    assert [item.status for item in runner.observations()].count("timeout") == 1

    assert runner.submit(request, _profile(request), response)
    time.sleep(0.12)
    assert runner.pending == 0
    assert shadow_module._runner_state(runner).timers == {}
    runner.close()


def test_preflight_stops_at_structural_caps_without_full_traversal() -> None:
    visits: list[int] = []
    with pytest.raises(shadow_module._SnapshotOversized):
        _preflight_json(
            list(range(shadow_module.SHADOW_CONTAINER_MAX_ITEMS + 1)),
            on_visit=visits.append,
        )
    assert visits == [1]

    nested: object = "leaf"
    for _ in range(SHADOW_STRUCTURE_MAX_DEPTH + 100):
        nested = {"nested": nested}
    visits.clear()
    with pytest.raises(shadow_module._SnapshotOversized):
        _preflight_json(nested, on_visit=visits.append)
    assert len(visits) <= SHADOW_STRUCTURE_MAX_DEPTH + 2

    visits.clear()
    with pytest.raises(shadow_module._SnapshotOversized):
        _preflight_json(
            "x" * (shadow_module.SHADOW_STRING_MAX_CHARS + 1),
            on_visit=visits.append,
        )
    assert visits == [1]


def test_result_count_cap_is_checked_before_walking_items() -> None:
    request = _request()
    profile = _profile(request)
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    response["results"] = [object()] * (SHADOW_RESULTS_MAX_ITEMS + 1)
    visits: list[int] = []

    with pytest.raises(shadow_module._SnapshotOversized):
        _preflight_submission(
            request,
            profile,
            response,
            response.execution_evidence,
            on_visit=visits.append,
        )
    assert visits == []


def test_normal_submission_has_small_injected_structure_budget_and_bounded_ciphertext() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    visits: list[int] = []
    budget = _preflight_submission(
        request,
        _profile(request),
        response,
        response.execution_evidence,
        on_visit=visits.append,
    )
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    job_id, snapshot = _sealed_job(runner, request, response)

    assert budget.nodes == visits[-1]
    assert budget.nodes < SHADOW_STRUCTURE_MAX_NODES
    assert budget.string_bytes < shadow_module.SHADOW_TOTAL_STRING_MAX_BYTES
    assert len(snapshot.ciphertext) <= SHADOW_ENVELOPE_MAX_BYTES
    runner._open_job(job_id, snapshot)
    runner.close()


@pytest.mark.parametrize("value", [_LyingSequence(), _LyingMapping()])
def test_custom_lying_containers_are_rejected_without_any_reported_length_or_iteration(
    value,
) -> None:
    with pytest.raises(shadow_module._SnapshotOversized):
        _preflight_json(value)
    assert value.length_calls == 0
    assert value.iterations == 0
    assert value.accesses == 0


def test_stateful_results_container_is_never_preflighted_or_iterated_twice(
    monkeypatch,
) -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    flipper = _FlippingSequence(response["results"][0])
    response["results"] = flipper
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        max_inflight=1,
        timeout_ms=500.0,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("unbounded identity/JSON/AES work must not run")

    monkeypatch.setattr(shadow_module, "_observation_identity", forbidden)
    monkeypatch.setattr(shadow_module, "_canonical_json_bytes", forbidden)
    assert runner.submit(request, _profile(request), response) is False
    observation = runner.observations()[-1]
    assert observation.drop_reason == "snapshot_oversized"
    assert observation.failures[0].error is CodeShadowError.SNAPSHOT_OVERSIZED
    assert flipper.length_calls == 0
    assert flipper.iterations == 0
    assert flipper.accesses == 0
    assert runner.pending == 0
    state = shadow_module._runner_state(runner)
    assert state.sealed_jobs == set()
    assert state.timers == {}

    monkeypatch.undo()
    valid = legacy.executed(request)
    assert runner.submit(request, _profile(request), valid)
    assert runner.flush(1.0)
    runner.close()


@pytest.mark.parametrize(
    "nested",
    [
        _LyingSequence(),
        _LyingMapping(),
        type("_ListSubclass", (list,), {})(["hidden"]),
        type("_DictSubclass", (dict,), {})({"hidden": "value"}),
    ],
)
def test_nested_custom_json_containers_fail_closed_without_traversal(nested) -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    response["results"][0]["metadata"] = nested
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )

    assert runner.submit(request, _profile(request), response) is False
    observation = runner.observations()[-1]
    assert observation.drop_reason == "snapshot_oversized"
    if hasattr(nested, "accesses"):
        assert nested.accesses == 0
        assert nested.iterations == 0
        assert nested.length_calls == 0
    assert runner.pending == 0
    runner.close()


@pytest.mark.parametrize("mutation", ["grow", "shrink"])
@pytest.mark.parametrize("container_kind", ["list", "dict"])
def test_exact_container_growth_or_shrink_during_snapshot_is_rejected(
    mutation: str,
    container_kind: str,
) -> None:
    if container_kind == "list":
        value: object = [{"nested": "first"}, {"nested": "second"}]

        def mutate() -> None:
            if mutation == "grow":
                value.append({"nested": "third"})  # type: ignore[union-attr]
            else:
                value.pop()  # type: ignore[union-attr]

    else:
        value = {"first": {"nested": "value"}, "second": "value"}

        def mutate() -> None:
            if mutation == "grow":
                value["third"] = "value"  # type: ignore[index]
            else:
                value.pop("second")  # type: ignore[union-attr]

    mutated = False

    def on_visit(count: int) -> None:
        nonlocal mutated
        if count == 2 and not mutated:
            mutated = True
            mutate()

    with pytest.raises(shadow_module._SnapshotChanged):
        _preflight_json(value, on_visit=on_visit)
    assert mutated is True


def test_million_item_deep_and_multibyte_inputs_stop_at_declared_caps() -> None:
    visits: list[int] = []
    with pytest.raises(shadow_module._SnapshotOversized):
        _preflight_json([None] * 1_000_000, on_visit=visits.append)
    assert visits == [1]

    nested: object = "leaf"
    for _ in range(SHADOW_STRUCTURE_MAX_DEPTH + 1_000):
        nested = {"nested": nested}
    visits.clear()
    with pytest.raises(shadow_module._SnapshotOversized):
        _preflight_json(nested, on_visit=visits.append)
    assert len(visits) <= SHADOW_STRUCTURE_MAX_DEPTH + 2

    visits.clear()
    with pytest.raises(shadow_module._SnapshotOversized):
        _preflight_json(["界" * 8_192] * 3, on_visit=visits.append)
    assert len(visits) <= 4


def test_final_json_cap_is_checked_on_normalized_tree_before_identity_or_aead(
    monkeypatch,
) -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    for item in response["results"]:
        item["snippet"] = "\x00" * 8_000
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )
    original_canonical = shadow_module._canonical_json_bytes
    normalized_calls = 0

    def assert_exact_tree(value: object) -> None:
        if type(value) is dict:
            assert all(type(key) is str for key in value)
            for item in value.values():
                assert_exact_tree(item)
        elif type(value) is list:
            for item in value:
                assert_exact_tree(item)
        else:
            assert value is None or type(value) in {str, int, float, bool}

    def checked_canonical(value: object) -> bytes:
        nonlocal normalized_calls
        normalized_calls += 1
        assert_exact_tree(value)
        return original_canonical(value)

    def identity_must_not_run(*args, **kwargs):
        raise AssertionError("identity must not run before the final byte cap")

    monkeypatch.setattr(shadow_module, "_canonical_json_bytes", checked_canonical)
    monkeypatch.setattr(shadow_module, "_observation_identity", identity_must_not_run)
    assert runner.submit(request, _profile(request), response) is False
    assert normalized_calls == 1
    observation = runner.observations()[-1]
    assert observation.failures[0].error is CodeShadowError.SNAPSHOT_OVERSIZED
    assert runner.pending == 0
    runner.close()


def test_bounded_normalized_submission_is_exact_private_tree_and_isolates_caller_mutation() -> None:
    request = _request()
    legacy = _LegacyRetriever()
    response = legacy.executed(request)
    normalized = _build_bounded_submission(
        request,
        _profile(request),
        response,
        response.execution_evidence,
    )
    entity_id = normalized.envelope["legacy_response"]["results"][0]["entity_id"]
    repositories = normalized.envelope["request"]["scope"]["repository_ids"]
    repository_snapshot = tuple(repositories)

    response["results"][0]["entity_id"] = "code://alpha/mutated"
    request.scope.repository_ids.append("repository://mutated")

    assert normalized.envelope["legacy_response"]["results"][0]["entity_id"] == entity_id
    assert tuple(normalized.envelope["request"]["scope"]["repository_ids"]) == repository_snapshot
    assert len(normalized.plaintext) <= SHADOW_ENVELOPE_MAX_BYTES
    assert not hasattr(shadow_module, "_plain_json")


def test_top_k_jaccard_deduplicates_first_rank_and_handles_empty_and_length() -> None:
    key = b"m" * 32
    duplicate = _compare_rankings(
        key,
        ("a", "a", "b"),
        ("a", "b"),
        10,
    )
    subset = _compare_rankings(
        key,
        ("a", "b", "c"),
        ("a", "b"),
        10,
    )
    both_empty = _compare_rankings(key, (), (), 10)
    one_empty = _compare_rankings(key, ("a",), (), 10)
    top_two = _compare_rankings(
        key,
        ("a", "b", "c"),
        ("a", "b", "different"),
        2,
    )

    assert duplicate[0] == 1.0
    assert [(delta.v1_rank, delta.comparison_rank) for delta in duplicate[3]] == [
        (0, 0),
        (1, 1),
    ]
    assert duplicate[4] == ("a", "b")
    assert subset[0] == round(2 / 3, 6)
    assert subset[1:3] == (round(2 / 3, 6), 1.0)
    assert both_empty[:3] == (1.0, 1.0, 1.0)
    assert one_empty[0] == 0.0
    assert top_two[0] == 1.0


@pytest.mark.parametrize(
    "invalid",
    [True, 1, "1.0", 0.0, -1.0, math.nan, math.inf, -math.inf],
)
def test_shadow_constructor_timeout_requires_exact_finite_positive_float(invalid) -> None:
    legacy = _LegacyRetriever()
    adapter = CodeSourceRetrieverV1Adapter(legacy)
    with pytest.raises(ValueError, match="exact finite positive float"):
        CodeShadowRunner(adapter, timeout_ms=invalid)


def test_shadow_constructor_accepts_smallest_positive_float() -> None:
    legacy = _LegacyRetriever()
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=math.nextafter(0.0, 1.0),
    )
    runner.close(timeout=0.0)


@pytest.mark.parametrize(
    "invalid",
    [True, 1, "1.0", -1.0, math.nan, math.inf, -math.inf],
)
@pytest.mark.parametrize("method", ["flush", "drain", "close"])
def test_shadow_wait_timeouts_require_exact_finite_nonnegative_float(
    method: str,
    invalid,
) -> None:
    legacy = _LegacyRetriever()
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )

    with pytest.raises(ValueError, match="exact finite non-negative float"):
        getattr(runner, method)(timeout=invalid)
    runner.close(timeout=0.0)


def test_shadow_wait_timeouts_accept_zero_float_at_every_entry() -> None:
    legacy = _LegacyRetriever()
    runner = CodeShadowRunner(
        CodeSourceRetrieverV1Adapter(legacy),
        timeout_ms=500.0,
    )

    assert runner.flush(0.0)
    assert runner.drain(0.0)
    assert runner.close(timeout=0.0)
