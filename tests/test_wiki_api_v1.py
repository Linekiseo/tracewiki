from __future__ import annotations

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
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


def _compilation_request():
    generations = tuple(
        build_source_generation_v1(
            source=source,
            generation_id=f"source-{source.value}-api-v1",
            watermark=f"watermark-{source.value}-api-v1",
        )
        for source in WikiSourceDomainV1
    )
    candidate = MultiSourceCandidateV2(
        candidate_id="candidate-code-api",
        entity_id="entity-code-api",
        retrieval_unit_id="unit-code-api",
        parent_entity_id=None,
        source_instance="project-rag:code",
        retrieval_domain="code",
        fact_type="code.implementation",
        entity_type="code.CodeSymbol",
        task="implementation",
        title="Agent-native Wiki API",
        snippet="Reviewed Wiki API and Navigator implementation evidence.",
        locator="code://project-rag/symbol/wiki-api",
        stable_version="commit-api-v1",
        source_generation="source-code-api-v1",
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
        token_estimate=32,
        root_provenance="production-code-api",
    )
    return build_wiki_compilation_request_v1(
        request_id="wiki-api-compile-1",
        project_id="project-rag",
        generation_id="wiki-api-generation-1",
        source_generations=generations,
        candidates=(candidate,),
        observed_at="2026-08-03T06:00:00Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )


def test_wiki_api_compiles_publishes_searches_reads_and_navigates(settings) -> None:
    with TestClient(create_app(settings)) as client:
        request = _compilation_request()
        compiled = client.post("/v1/wiki/compile", json=request.model_dump(mode="json"))
        assert compiled.status_code == 202, compiled.text
        manifest = compiled.json()["manifest"]
        published = client.post(
            "/v1/wiki/publish",
            json={
                "project_id": "project-rag",
                "generation_id": manifest["generation_id"],
                "expected_manifest_sha256": manifest["content_sha256"],
                "reviewer_authority_sha256": canonical_sha256_v1("wiki-api-reviewer"),
            },
        )
        assert published.status_code == 200, published.text
        status = client.get("/v1/wiki/status?project_id=project-rag")
        assert status.status_code == 200
        assert status.json()["availability"] == "AVAILABLE"
        assert status.json()["default_engine"] == "v1"
        search = client.post(
            "/v1/wiki/search",
            json={
                "project_id": "project-rag",
                "query": "Wiki API Navigator implementation",
                "required_sources": ["code"],
                "required_roles": ["implementation"],
            },
        )
        assert search.status_code == 200, search.text
        hit = search.json()["hits"][0]
        read = client.get(
            "/v1/wiki/read",
            params={"project_id": "project-rag", "path": hit["logical_path"]},
        )
        assert read.status_code == 200
        assert read.json()["logical_path"] == hit["logical_path"]
        navigation = client.post(
            "/v1/wiki/navigate",
            json={
                "project_id": "project-rag",
                "query": "Wiki API implementation",
                "required_sources": ["code"],
                "required_roles": ["implementation"],
            },
        )
        assert navigation.status_code == 200, navigation.text
        assert navigation.json()["stop_reason"] == "evidence_complete"
        assert navigation.json()["raw_verify_count"] >= 1
        pages = client.get("/v1/wiki/pages?project_id=project-rag")
        assert pages.status_code == 200
        assert pages.json()["total"] > 0
        assert pages.json()["offset"] == 0
        filtered = client.get(
            "/v1/wiki/pages",
            params={
                "project_id": "project-rag",
                "q": "Navigator implementation",
                "page_type": "component",
                "source": "code",
                "role": "implementation",
                "limit": 1,
            },
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] >= 1
        assert len(filtered.json()["pages"]) == 1
        page = filtered.json()["pages"][0]
        evidence = client.get(
            "/v1/wiki/evidence",
            params={
                "project_id": "project-rag",
                "page_path": page["logical_path"],
                "source_ref_id": page["source_refs"][0]["source_ref_id"],
            },
        )
        assert evidence.status_code == 200, evidence.text
        assert evidence.json()["page_path"] == page["logical_path"]
        followed = client.get(
            "/v1/wiki/follow",
            params={"project_id": "project-rag", "path": page["logical_path"]},
        )
        assert followed.status_code == 200, followed.text
        assert followed.json()["source_path"] == page["logical_path"]


def test_wiki_api_does_not_expose_unknown_projects(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/wiki/status?project_id=unknown-project")
        assert response.status_code == 404


def test_unified_query_explicitly_opts_into_wiki_without_changing_default_v1(settings) -> None:
    with TestClient(create_app(settings)) as client:
        request = _compilation_request()
        compiled = client.post("/v1/wiki/compile", json=request.model_dump(mode="json"))
        manifest = compiled.json()["manifest"]
        client.post(
            "/v1/wiki/publish",
            json={
                "project_id": "project-rag",
                "generation_id": manifest["generation_id"],
                "expected_manifest_sha256": manifest["content_sha256"],
                "reviewer_authority_sha256": canonical_sha256_v1("wiki-query-reviewer"),
            },
        )
        payload = {
            "question": "Wiki API Navigator implementation",
            "scope": {"project_id": "project-rag"},
            "mode": "evidence",
            "include": ["code"],
            "clarification_policy": "never",
        }
        response = client.post(
            "/v1/query",
            headers={"X-RAG-Wiki-Engine": "wiki_v1"},
            json=payload,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["engine"] == "wiki_v1"
        assert body["answer"]["answer_mode"] == "retrieval_only"
        assert body["navigation"]["raw_verify_count"] >= 1
        assert body["trace"]["quality_state"] == "QUALITY_HOLD"
        assert payload["question"] not in str(body["trace"])

        invalid = client.post(
            "/v1/query",
            headers={"X-RAG-Wiki-Engine": "v2"},
            json=payload,
        )
        assert invalid.status_code == 422
        conflict = client.post(
            "/v1/query",
            headers={"X-RAG-Wiki-Engine": "wiki_v1", "X-RAG-Code-Engine": "v1"},
            json=payload,
        )
        assert conflict.status_code == 422
