from __future__ import annotations

import json

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.models import CodexSearchRequest
from evidence_rag.rag.codex_v2 import (
    CODEX_V2_VERSION,
    CodexEngineOverrideError,
    CodexTemporalRetrieverV2,
    validate_codex_engine_override,
)
from evidence_rag.rag.sources.codex.governance_v2 import (
    CODEX_RELEASE_THRESHOLDS,
    CODEX_REQUIRED_GUARDRAILS,
    CodexEvidenceStatus,
    CodexReleaseGuardrailV2,
    CodexReleaseMetricV2,
    CodexReleaseStage,
    build_codex_release_evidence_v2,
    evaluate_codex_release_v2,
)


def _install_verified_opt_in_authority(client: TestClient) -> None:
    evidence = build_codex_release_evidence_v2(
        observed_stage=CodexReleaseStage.CANARY_25,
        proposed_stage=CodexReleaseStage.OPT_IN_100,
        artifact_set_sha256="sha256:" + "1" * 64,
        golden_package_sha256="sha256:" + "2" * 64,
        component_set_sha256="sha256:" + "3" * 64,
        metrics=tuple(
            CodexReleaseMetricV2(
                name=name,
                status=CodexEvidenceStatus.AVAILABLE,
                numerator=threshold,
                denominator=1,
                value=threshold,
            )
            for name, (_direction, threshold) in CODEX_RELEASE_THRESHOLDS.items()
        ),
        guardrails=tuple(
            CodexReleaseGuardrailV2(
                name=name,
                status=CodexEvidenceStatus.AVAILABLE,
                violations=0,
            )
            for name in CODEX_REQUIRED_GUARDRAILS
        ),
        production_observation=True,
        treatment_result_available=True,
        rollback_rehearsed=True,
    )
    authority = client.app.state.runtime.source_runtime_v2.install_release_authority(
        source="codex",
        project_id="project-rag",
        evidence=evidence,
        decision=evaluate_codex_release_v2(evidence),
    )
    assert authority.stage.value == "on"


class _CountingLegacy:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, request: CodexSearchRequest) -> dict:
        self.calls += 1

        def item(
            entity_id: str,
            item_type: str,
            *,
            score: float,
            status: str | None = "completed",
            metadata: dict | None = None,
        ) -> dict:
            return {
                "entity_id": entity_id,
                "source_id": "codex-source://fixture",
                "generation_id": "generation://fixture",
                "thread_id": "thread-fixture",
                "thread_entity_id": "codex://thread/thread-fixture",
                "turn_id": "turn-fixture",
                "thread_title": "Validate parser repair",
                "item_type": item_type,
                "view_type": "summary",
                "name": entity_id,
                "role": None,
                "status": status,
                "thread_status": "completed",
                "timestamp": "2026-07-29T00:00:00Z",
                "cwd": None,
                "evidence_locator": f"codex://thread/thread-fixture/item/{entity_id}",
                "acl_ref": "public",
                "metadata": metadata or {},
                "lexical_score": score,
                "dense_score": score,
                "score": score,
                "channels": ["lexical"],
                "edges": [],
                "snippet": "observable validation evidence",
            }

        return {
            "query_id": "q-codex-fixture",
            "query": request.query,
            "resolved_scope": request.scope.model_dump(),
            "index_generation": ["generation://fixture"],
            "total": 2,
            "results": [
                item(
                    "validation",
                    "ValidationResult",
                    score=0.5,
                    metadata={"exit_code": 0},
                ),
                item("claim", "AgentMessage", score=0.5),
            ],
            "trace": {
                "duration_ms": 1.0,
                "fusion": "codex-weighted-hybrid-v2",
                "lexical_candidates": 2,
                "dense_candidates": 2,
            },
        }


def test_codex_engine_validation_and_single_legacy_execution() -> None:
    assert validate_codex_engine_override(None) == "v1"
    assert validate_codex_engine_override(" V2 ") == "v2"
    try:
        validate_codex_engine_override("latest")
    except CodexEngineOverrideError as exc:
        assert "expected v1 or v2" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid engine must fail closed")

    legacy = _CountingLegacy()
    response = CodexTemporalRetrieverV2(legacy).search(
        CodexSearchRequest(query="which validation passed?", limit=5)
    )

    assert legacy.calls == 1
    assert response["trace"]["engine"] == CODEX_V2_VERSION
    assert response["results"][0]["entity_id"] == "validation"
    assert response["results"][0]["event_status"] == "passed"
    assert response["results"][1]["negative_reasons"] == ["claim_only"]
    assert response["context"]["reasoning_included"] is False
    assert response["trace"]["release"]["decision"] == "HOLD_DEFAULT_V1"
    assert response["trace"]["release"]["old_treatment_retry"] is False


def test_codex_v2_direct_and_platform_opt_in_are_additive(
    settings, sample_repository, sample_codex_home
) -> None:
    with TestClient(create_app(settings)) as client:
        ingest = client.post(
            "/v1/ingestion/codex",
            json={
                "source": str(sample_codex_home),
                "project_path": str(sample_repository),
            },
        )
        assert ingest.status_code == 202, ingest.text
        workflow = client.get(f"/v1/ingestion/workflows/{ingest.json()['workflow_id']}").json()
        assert workflow["status"] == "completed"
        _install_verified_opt_in_authority(client)

        default = client.post(
            "/v1/codex/search",
            json={"query": "rerank retrieval", "limit": 8},
        )
        assert default.status_code == 200, default.text
        assert default.json()["trace"]["fusion"] == "codex-weighted-hybrid-v2"
        assert "engine" not in default.json()["trace"]

        first = client.post(
            "/v1/codex/search",
            headers={"X-RAG-Codex-Engine": "v2"},
            json={"query": "validate rerank retrieval", "limit": 8},
        )
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["trace"]["engine"] == CODEX_V2_VERSION
        assert payload["trace"]["release"]["default_engine"] == "v1"
        assert payload["context"]["schema_version"] == "codex-timeline-context-v2"
        assert payload["context"]["reasoning_included"] is False
        assert payload["results"]
        assert all(
            item["episode_id"].startswith("codex-v2://episode/") for item in payload["results"]
        )
        assert all("reasoning" not in item["item_type"].casefold() for item in payload["results"])
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "private chain of thought" not in serialized
        assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in serialized

        repeated = client.post(
            "/v1/codex/search",
            headers={"X-RAG-Codex-Engine": "v2"},
            json={"query": "validate rerank retrieval", "limit": 8},
        ).json()
        assert [item["episode_id"] for item in repeated["results"]] == [
            item["episode_id"] for item in payload["results"]
        ]

        platform = client.post(
            "/v1/search",
            headers={"X-RAG-Codex-Engine": "v2"},
            json={
                "query": "validate rerank retrieval",
                "sources": ["codex"],
                "limit": 8,
            },
        )
        assert platform.status_code == 200, platform.text
        global_payload = platform.json()
        assert global_payload["trace"]["codex_engine"] == "v2"
        assert global_payload["trace"]["sources"]["codex"]["engine"] == CODEX_V2_VERSION
        assert (
            global_payload["evidence_pack"]["source_contexts"]["codex"]["reasoning_included"]
            is False
        )
        assert any(item.get("episode_id") for item in global_payload["results"])

        default_platform = client.post(
            "/v1/search",
            json={"query": "rerank retrieval", "sources": ["codex"], "limit": 5},
        ).json()
        assert default_platform["trace"]["codex_engine"] == "v1"

        invalid = client.post(
            "/v1/codex/search",
            headers={"X-RAG-Codex-Engine": "future"},
            json={"query": "rerank"},
        )
        assert invalid.status_code == 422
