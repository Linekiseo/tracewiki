from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.query.intelligence import (
    QueryUnderstandingEngine,
    fuse_search_responses,
)
from evidence_rag.query.provider import (
    LLMProviderConfiguration,
    ResolvedLLMProvider,
    RuntimeLLMProviderRegistry,
    provider_chat_options,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class _Client:
    payload: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []

    def __init__(self, **_: Any) -> None:
        pass

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _Response(self.payload)


def _provider() -> ResolvedLLMProvider:
    return ResolvedLLMProvider(
        provider="openai_compatible",
        base_url="https://llm.example/v1",
        model="query-planner",
        api_key="private-test-key",
        source="desktop_runtime",
    )


def _search(query_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "total": len(results),
        "results": results,
        "evidence_pack": {
            "citation_map": {
                f"E{index}": {
                    "entity_id": item["entity_id"],
                    "source": item["source"],
                    "locator": item["locator"],
                    "version": item.get("version"),
                }
                for index, item in enumerate(results, start=1)
            },
            "relations": [],
            "related_entities": [],
            "conflicts_and_staleness": [],
        },
        "trace": {"sources": {}, "fusion": "source-fusion"},
        "index_generations": [f"generation-{query_id}"],
    }


def test_local_query_understanding_uses_statistical_prototypes_and_bounded_decomposition() -> None:
    result = QueryUnderstandingEngine().local(
        "比较三个实验的指标，同时解释代码变更为何影响复现",
        explicit_intent=None,
        requested_sources=("experiment", "code", "codex"),
    )

    assert result.strategy == "local_statistical"
    assert result.intent in {
        "experiment_validation",
        "reproduction",
        "rationale",
        "change_trace",
        "global_synthesis",
    }
    assert 0.0 <= result.intent_confidence <= 1.0
    assert 2 <= len(result.subqueries) <= 4
    assert set(result.source_hints).issubset({"experiment", "code", "codex"})
    assert result.content_digest.startswith("sha256:")
    assert sum(score for _, score in result.intent_scores) == pytest.approx(1.0)
    assert sum(score for _, score in result.source_scores) == pytest.approx(1.0)
    assert 0.0 <= result.uncertainty <= 1.0
    assert result.complexity > 0.0
    public = result.public(include_queries=True)
    assert public["algorithm"]["encoder"] == "frozen_tfidf_word_char_ngram"
    assert public["algorithm"]["classifier"] == "calibrated_centroid_softmax"
    assert len(public["intent_distribution"]) >= 3


def test_local_classifier_is_probability_ranked_not_first_matching_rule() -> None:
    result = QueryUnderstandingEngine().local(
        "请把分散在实现、研发记录和实验里的依据综合起来，指出尚未闭合的风险",
        explicit_intent=None,
        requested_sources=("code", "codex", "experiment", "workspace"),
    )

    assert result.intent == max(result.intent_scores, key=lambda item: item[1])[0]
    assert result.intent_scores[0][1] < 1.0
    assert result.uncertainty > 0.0
    assert set(dict(result.source_scores)) == {
        "code",
        "codex",
        "workspace",
        "experiment",
        "notebook",
        "document",
    }


def test_structured_llm_plan_is_schema_bounded_and_cannot_broaden_sources(monkeypatch) -> None:
    document = {
        "intent": "reproduction",
        "source_hints": ["code", "experiment", "document"],
        "required_roles": ["run", "commit", "invented_role"],
        "subqueries": ["实验运行与数据版本", "代码提交和环境配置"],
        "entities": ["ExperimentService", "run-7"],
        "temporal_scope": "current",
        "ambiguity": ["missing_dataset"],
        "confidence": 0.91,
    }
    _Client.payload = {
        "choices": [{"message": {"content": json.dumps(document, ensure_ascii=False)}}]
    }
    _Client.calls.clear()
    monkeypatch.setattr("evidence_rag.query.intelligence.httpx.Client", _Client)

    result = QueryUnderstandingEngine().understand(
        "如何复现实验并核对对应代码？",
        explicit_intent=None,
        requested_sources=("code", "experiment"),
        provider=_provider(),
        timeout_seconds=3,
    )

    assert result.strategy == "llm_structured_hybrid"
    assert result.intent == "reproduction"
    assert result.source_hints == ("code", "experiment")
    assert "invented_role" not in result.required_roles
    assert result.subqueries == ("实验运行与数据版本", "代码提交和环境配置")
    assert "private-test-key" not in json.dumps(result.public(include_queries=True))
    assert result.public(include_queries=True)["algorithm"] == {
        "version": "query-understanding-hybrid-v2",
        "encoder": "structured_llm_plus_tfidf_prior",
        "classifier": "calibrated_centroid_softmax",
        "uncertainty": round(result.uncertainty, 4),
        "complexity": round(result.complexity, 4),
        "decomposition": "constrained_llm_evidence_obligations",
        "planner_model": "query-planner",
    }
    assert _Client.calls[0]["headers"]["Authorization"] == "Bearer private-test-key"


def test_invalid_or_sensitive_model_path_falls_back_without_external_call(monkeypatch) -> None:
    class _Bomb:
        def __init__(self, **_: Any) -> None:
            raise AssertionError("sensitive query must stay local")

    monkeypatch.setattr("evidence_rag.query.intelligence.httpx.Client", _Bomb)
    result = QueryUnderstandingEngine().understand(
        "检查 /Users/private/key.txt 中的 api_key=do-not-send",
        explicit_intent=None,
        requested_sources=("code",),
        provider=_provider(),
        timeout_seconds=3,
    )

    assert result.strategy == "local_statistical"
    assert result.fallback_reason == "structured_model_unavailable_or_invalid"


def test_multi_query_rrf_rewards_cross_query_evidence_and_rebuilds_citations() -> None:
    common = {
        "entity_id": "code://common",
        "source": "code",
        "locator": "code://repo/common.py#L1",
        "score": 0.6,
    }
    first_only = {
        "entity_id": "code://first",
        "source": "code",
        "locator": "code://repo/first.py#L1",
        "score": 0.99,
    }
    second_only = {
        "entity_id": "document://second",
        "source": "document",
        "locator": "document://second#claim=1",
        "score": 0.8,
    }

    fused = fuse_search_responses(
        [
            _search("one", [first_only, common]),
            _search("two", [second_only, common]),
        ],
        query_count=2,
    )

    assert fused["results"][0]["entity_id"] == "code://common"
    assert fused["results"][0]["metadata"]["query_appearances"] == 2
    assert len(fused["evidence_pack"]["citation_map"]) == 3
    assert fused["trace"]["query_fusion"]["successful_queries"] == 2


def test_runtime_provider_registry_is_ephemeral_and_never_exposes_secret(settings) -> None:
    registry = RuntimeLLMProviderRegistry(settings)
    assert registry.status("project-rag")["configured"] is False

    public = registry.configure(
        LLMProviderConfiguration(
            project_id="project-rag",
            base_url="https://llm.example/v1/",
            model="planner-model",
            api_key="super-private-key",
        )
    )

    assert public["configured"] is True
    assert public["base_url"] == "https://llm.example/v1"
    assert "super-private-key" not in repr(public)
    assert registry.resolve("project-rag").api_key == "super-private-key"  # type: ignore[union-attr]
    assert registry.clear("project-rag")["configured"] is False


def test_provider_health_reserves_reasoning_budget_and_returns_no_secret(
    settings, monkeypatch
) -> None:
    _Client.payload = {
        "choices": [
            {
                "message": {
                    "content": "READY",
                    "reasoning_content": "private provider reasoning",
                },
                "finish_reason": "stop",
            }
        ]
    }
    _Client.calls.clear()
    monkeypatch.setattr("evidence_rag.query.provider.httpx.Client", _Client)
    registry = RuntimeLLMProviderRegistry(settings)
    registry.configure(
        LLMProviderConfiguration(
            project_id="project-rag",
            base_url="https://llm.example/v1",
            model="reasoning-model",
            api_key="health-secret",
        )
    )

    result = registry.test("project-rag")

    assert result["health"] == "ready"
    assert result["tested"] is True
    assert "health-secret" not in repr(result)
    assert _Client.calls[0]["json"]["max_tokens"] >= 128
    assert _Client.calls[0]["json"]["messages"][0]["content"].startswith("Return exactly")


def test_deepseek_v4_uses_bounded_non_thinking_profiles_without_affecting_generic() -> None:
    deepseek = ResolvedLLMProvider(
        provider="openai_compatible",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_key="private-key",
        source="desktop_runtime",
    )

    assert provider_chat_options(deepseek, purpose="health") == {
        "max_tokens": 128,
        "thinking": {"type": "disabled"},
    }
    assert provider_chat_options(deepseek, purpose="planning") == {
        "max_tokens": 1_200,
        "thinking": {"type": "disabled"},
    }
    assert provider_chat_options(deepseek, purpose="generation") == {
        "max_tokens": 1_600,
        "thinking": {"type": "disabled"},
    }
    assert provider_chat_options(_provider(), purpose="generation") == {
        "max_tokens": 1_600
    }


def test_remote_provider_requires_https_and_environment_fallback_is_supported(settings) -> None:
    with pytest.raises(ValueError):
        LLMProviderConfiguration(
            project_id="project-rag",
            base_url="http://llm.example/v1",
            model="planner",
            api_key="key",
        )

    configured = replace(
        settings,
        llm_base_url="https://llm.example/v1",
        llm_model="planner",
        llm_api_key="environment-secret",
    )
    resolved = RuntimeLLMProviderRegistry(configured).resolve("project-rag")
    assert resolved is not None
    assert resolved.source == "environment"
    assert resolved.public()["secret_storage"] == "process_environment"


def test_provider_api_registers_only_process_memory_and_never_returns_key(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.put(
            "/v1/ai/provider",
            json={
                "project_id": "project-rag",
                "provider": "openai_compatible",
                "base_url": "https://llm.example/v1",
                "model": "planner",
                "api_key": "never-return-this-key",
            },
        )
        assert response.status_code == 200
        assert response.json()["configured"] is True
        assert "never-return-this-key" not in response.text

        status = client.get("/v1/ai/provider", params={"project_id": "project-rag"})
        assert status.status_code == 200
        assert status.json()["model"] == "planner"
        assert "never-return-this-key" not in status.text

        cleared = client.request(
            "DELETE",
            "/v1/ai/provider",
            json={"project_id": "project-rag"},
        )
        assert cleared.status_code == 200
        assert cleared.json()["configured"] is False
