from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from evidence_rag.models import QueryRequest, SearchScope
from evidence_rag.rag.multisource_foundation_v2 import MultiSourceCandidateV2
from evidence_rag.rag.wiki.compiler_v1 import (
    WIKI_COMPILER_AUTHORITY_SHA256,
    build_wiki_compilation_request_v1,
)
from evidence_rag.rag.wiki.contracts_v1 import (
    WikiSourceDomainV1,
    build_source_generation_v1,
    canonical_sha256_v1,
)
from evidence_rag.rag.wiki.query_v1 import WikiIntelligentQueryV1
from evidence_rag.rag.wiki.runtime_v1 import WikiRuntimeV1
from evidence_rag.rag.wiki.store_v1 import WikiStoreV1


def _runtime(tmp_path: Path) -> WikiRuntimeV1:
    candidate = MultiSourceCandidateV2(
        candidate_id="candidate-wiki-query-generation",
        entity_id="entity-wiki-query-generation",
        retrieval_unit_id="unit-wiki-query-generation",
        parent_entity_id=None,
        source_instance="project-rag:code",
        retrieval_domain="code",
        fact_type="code.implementation",
        entity_type="code.CodeSymbol",
        task="implementation",
        title="Wiki grounded query",
        snippet="Wiki Navigator verifies raw evidence before a grounded answer.",
        locator="code://project-rag/wiki/query",
        stable_version="commit-wiki-query-v1",
        source_generation="source-code-wiki-query-v1",
        raw_or_derived="raw_fact",
        derivation="production_adapter",
        review_status="reviewed",
        fact_status="active",
        channel_scores=(("exact", 1.0),),
        calibrated_relevance=1.0,
        calibration_version="reviewed-calibration-v1",
        matched_roles=("implementation",),
        authority=1.0,
        version_alignment="exact",
        acl_ref="project:project-rag",
        token_estimate=24,
        root_provenance="production-wiki-query",
    )
    source_generations = tuple(
        build_source_generation_v1(
            source=source,
            generation_id=(
                "source-code-wiki-query-v1"
                if source is WikiSourceDomainV1.CODE
                else f"source-{source.value}-wiki-query-v1"
            ),
            watermark=f"watermark-{source.value}-wiki-query-v1",
        )
        for source in WikiSourceDomainV1
    )
    request = build_wiki_compilation_request_v1(
        request_id="wiki-query-generation-request",
        project_id="project-rag",
        generation_id="wiki-query-generation-v1",
        source_generations=source_generations,
        candidates=(candidate,),
        observed_at="2026-08-03T08:00:00Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )
    runtime = WikiRuntimeV1(store=WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path))
    result = runtime.compile_to_staging(request)
    runtime.publish_generation(
        project_id="project-rag",
        generation_id=result.manifest.generation_id,
        expected_manifest_sha256=result.manifest.content_sha256,
        reviewer_authority_sha256=canonical_sha256_v1("wiki-query-reviewer"),
        published_at="2026-08-03T08:05:00Z",
    )
    return runtime


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeClient:
    mode = "grounded"
    calls: list[dict] = []

    def __init__(self, **_: object) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, url: str, *, headers: dict, json: dict) -> _Response:
        self.calls.append({"url": url, "headers": headers, "json": json})
        document = __import__("json").loads(json["messages"][1]["content"])
        evidence = document["evidence"][0]
        citation = document["citations"][0]
        if self.mode == "malformed":
            return _Response({"choices": [{"message": {"content": "not-json"}}]})
        claim_text = (
            "Mars has a reviewed production deployment."
            if self.mode == "unsupported"
            else evidence["body"]
        )
        claims = {
            "claims": [
                {
                    "claim_id": "wiki-generated-claim-1",
                    "text": claim_text,
                    "citation_ids": [citation["citation_id"]],
                    "required_fact_ids": [evidence["fact_id"]],
                }
            ]
        }
        return _Response({"choices": [{"message": {"content": __import__("json").dumps(claims)}}]})


def _request(mode: str = "answer") -> QueryRequest:
    return QueryRequest(
        question="How does the Wiki Navigator ground its answer?",
        scope=SearchScope(project_id="project-rag"),
        mode=mode,
        include=["code"],
        max_evidence=8,
        clarification_policy="never",
    )


def test_wiki_query_generates_only_exactly_grounded_claims(
    tmp_path: Path, settings, monkeypatch
) -> None:
    runtime = _runtime(tmp_path)
    configured = replace(
        settings,
        llm_base_url="http://127.0.0.1:9999/v1",
        llm_api_key="wiki-test-secret-key",
        llm_model="reviewed-test-model",
    )
    _FakeClient.calls.clear()
    _FakeClient.mode = "grounded"
    monkeypatch.setattr("evidence_rag.rag.wiki.query_v1.httpx.Client", _FakeClient)
    response = WikiIntelligentQueryV1(settings=configured, wiki=runtime).answer(
        _request(),
        requester_acl_refs=("project:project-rag",),
    )
    assert response["answer"]["fallback_reason"] is None, response["trace"]["generator"]
    assert response["answer"]["answer_mode"] == "grounded", response["answer"]
    assert response["answer"]["citations"]
    assert response["trace"]["generator"]["fallback_reason"] is None
    assert _FakeClient.calls[0]["url"] == "http://127.0.0.1:9999/v1/chat/completions"
    assert _FakeClient.calls[0]["headers"]["Authorization"] == "Bearer wiki-test-secret-key"
    assert "wiki-test-secret-key" not in json.dumps(response)
    assert _request().question not in json.dumps(response["trace"])


def test_wiki_query_falls_back_to_retrieval_only_for_unsupported_or_malformed_generation(
    tmp_path: Path, settings, monkeypatch
) -> None:
    runtime = _runtime(tmp_path)
    configured = replace(
        settings,
        llm_base_url="http://127.0.0.1:9999/v1",
        llm_api_key="wiki-test-secret-key",
        llm_model="reviewed-test-model",
    )
    monkeypatch.setattr("evidence_rag.rag.wiki.query_v1.httpx.Client", _FakeClient)
    engine = WikiIntelligentQueryV1(settings=configured, wiki=runtime)

    _FakeClient.mode = "unsupported"
    unsupported = engine.answer(_request(), requester_acl_refs=("project:project-rag",))
    assert unsupported["answer"]["answer_mode"] == "retrieval_only"
    assert unsupported["answer"]["fallback_reason"] == "claim_citation_verification_failed"
    assert unsupported["grounded_answer"]["claims"] == []

    _FakeClient.mode = "malformed"
    malformed = engine.answer(_request(), requester_acl_refs=("project:project-rag",))
    assert malformed["answer"]["answer_mode"] == "retrieval_only"
    assert malformed["answer"]["fallback_reason"] == "generator_or_contract_failure"


def test_wiki_evidence_mode_never_invokes_generation(tmp_path: Path, settings, monkeypatch) -> None:
    runtime = _runtime(tmp_path)
    configured = replace(
        settings,
        llm_base_url="http://127.0.0.1:9999/v1",
        llm_api_key="wiki-test-secret-key",
        llm_model="reviewed-test-model",
    )

    class _BombClient:
        def __init__(self, **_: object) -> None:
            raise AssertionError("evidence mode must not construct a generator client")

    monkeypatch.setattr("evidence_rag.rag.wiki.query_v1.httpx.Client", _BombClient)
    response = WikiIntelligentQueryV1(settings=configured, wiki=runtime).answer(
        _request("evidence"),
        requester_acl_refs=("project:project-rag",),
    )
    assert response["answer"]["answer_mode"] == "retrieval_only"
    assert response["trace"]["generator"] == {
        "attempted": False,
        "availability": "NOT_REQUESTED",
        "fallback_reason": None,
    }


def test_wiki_evidence_mode_returns_explicit_refusal_for_unanswerable_query(
    tmp_path: Path, settings, monkeypatch
) -> None:
    runtime = _runtime(tmp_path)
    configured = replace(
        settings,
        llm_base_url="http://127.0.0.1:9999/v1",
        llm_api_key="wiki-test-secret-key",
        llm_model="reviewed-test-model",
    )

    class _BombClient:
        def __init__(self, **_: object) -> None:
            raise AssertionError("unanswerable evidence mode must not invoke generation")

    monkeypatch.setattr("evidence_rag.rag.wiki.query_v1.httpx.Client", _BombClient)
    request = _request("evidence").model_copy(
        update={"question": "Where is missing-identifier-9999 implemented?"}
    )

    response = WikiIntelligentQueryV1(settings=configured, wiki=runtime).answer(
        request,
        requester_acl_refs=("project:project-rag",),
    )

    assert response["answer"]["status"] == "insufficient_evidence"
    assert response["answer"]["answer_mode"] == "refusal"
    assert response["answer"]["refusal"] is True
    assert response["answer"]["refusal_reason"] is not None
    assert response["answer"]["citations"] == []
    assert response["grounded_answer"]["authority"] == "final_answer_v2"
    assert response["trace"]["generator"] == {
        "attempted": False,
        "availability": "NOT_REQUESTED",
        "fallback_reason": None,
    }
