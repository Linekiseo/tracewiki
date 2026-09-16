from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.rag.multisource_foundation_v2 import MultiSourceCandidateV2
from evidence_rag.rag.wiki.builder_v1 import (
    WikiPatchActionV1,
    WikiPatchOriginV1,
    build_wiki_builder_patch_v1,
    build_wiki_patch_operation_v1,
)
from evidence_rag.rag.wiki.compiler_v1 import (
    WIKI_COMPILER_AUTHORITY_SHA256,
    build_wiki_compilation_request_v1,
)
from evidence_rag.rag.wiki.contracts_v1 import (
    WikiPageFragmentV1,
    WikiSourceDomainV1,
    build_source_generation_v1,
    build_wiki_page_fragment_v1,
    canonical_sha256_v1,
)


def _mcp(client: TestClient, method: str, params: dict | None = None) -> dict:
    response = client.post(
        "/mcp",
        headers={"Authorization": "Bearer wiki-agent-token"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            **({"params": params} if params is not None else {}),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _call(client: TestClient, name: str, arguments: dict) -> dict:
    response = _mcp(
        client,
        "tools/call",
        {"name": name, "arguments": arguments},
    )["result"]
    assert response["isError"] is False, response
    return response["structuredContent"]


def _request():
    generations = tuple(
        build_source_generation_v1(
            source=source,
            generation_id=f"source-{source.value}-mcp-v1",
            watermark=f"watermark-{source.value}-mcp-v1",
        )
        for source in WikiSourceDomainV1
    )
    candidates = (
        MultiSourceCandidateV2(
            candidate_id="candidate-code-mcp",
            entity_id="entity-code-mcp",
            retrieval_unit_id="unit-code-mcp",
            parent_entity_id=None,
            source_instance="project-rag:code",
            retrieval_domain="code",
            fact_type="code.implementation",
            entity_type="code.CodeSymbol",
            task="implementation",
            title="Governed Wiki MCP navigation",
            snippet="MCP tools search, follow and verify exact raw Wiki evidence.",
            locator="code://project-rag/symbol/wiki-mcp",
            stable_version="commit-mcp-v1",
            source_generation="source-code-mcp-v1",
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
            root_provenance="production-code-mcp",
        ),
        MultiSourceCandidateV2(
            candidate_id="candidate-document-mcp",
            entity_id="entity-document-mcp",
            retrieval_unit_id="unit-document-mcp",
            parent_entity_id=None,
            source_instance="project-rag:document",
            retrieval_domain="document",
            fact_type="document.requirement",
            entity_type="document.Section",
            task="requirement",
            title="Agent evidence requirement",
            snippet="Agent answers must bind citations to verified raw evidence.",
            locator="document://project-rag/wiki-agent-requirement",
            stable_version="document-mcp-v1",
            source_generation="source-document-mcp-v1",
            raw_or_derived="raw_fact",
            derivation="production_adapter",
            review_status="reviewed",
            fact_status="active",
            channel_scores=(("exact", 1.0),),
            calibrated_relevance=0.97,
            calibration_version="reviewed-calibration-v1",
            matched_roles=("requirement",),
            authority=1.0,
            version_alignment="exact",
            acl_ref="project:project-rag",
            token_estimate=24,
            root_provenance="production-document-mcp",
        ),
    )
    return build_wiki_compilation_request_v1(
        request_id="wiki-mcp-compile-1",
        project_id="project-rag",
        generation_id="wiki-mcp-generation-1",
        source_generations=generations,
        candidates=candidates,
        observed_at="2026-08-03T08:00:00Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )


def _publish(client: TestClient) -> dict:
    request = _request()
    compiled = client.post("/v1/wiki/compile", json=request.model_dump(mode="json"))
    assert compiled.status_code == 202, compiled.text
    manifest = compiled.json()["manifest"]
    published = client.post(
        "/v1/wiki/publish",
        json={
            "project_id": "project-rag",
            "generation_id": manifest["generation_id"],
            "expected_manifest_sha256": manifest["content_sha256"],
            "reviewer_authority_sha256": canonical_sha256_v1("wiki-mcp-reviewer"),
        },
    )
    assert published.status_code == 200, published.text
    return manifest


def test_wiki_mcp_tools_are_acl_pinned_and_verify_raw_evidence(settings) -> None:
    configured = replace(settings, mcp_token="wiki-agent-token")
    with TestClient(create_app(configured)) as client:
        _publish(client)
        initialized = _mcp(client, "initialize", {"clientInfo": {"name": "Codex"}})
        assert "wiki_navigate" in initialized["result"]["instructions"]
        tools = _mcp(client, "tools/list")["result"]["tools"]
        names = {item["name"] for item in tools}
        assert {
            "wiki_navigation_status",
            "wiki_search",
            "wiki_read",
            "wiki_follow",
            "wiki_navigate",
            "evidence_read",
            "wiki_propose_patch",
        } <= names
        proposal = next(item for item in tools if item["name"] == "wiki_propose_patch")
        assert proposal["annotations"]["readOnlyHint"] is False
        assert proposal["inputSchema"]["properties"]["patch"]["type"] == "object"
        navigator_tool = next(item for item in tools if item["name"] == "wiki_navigate")
        assert navigator_tool["inputSchema"]["properties"]["max_raw_reads"]["maximum"] == 128
        assert navigator_tool["inputSchema"]["properties"]["retrieval_deadline_ms"]["minimum"] == 50

        status = _call(client, "wiki_navigation_status", {})
        assert status["availability"] == "AVAILABLE"
        assert status["quality_state"] == "QUALITY_HOLD"
        search = _call(
            client,
            "wiki_search",
            {
                "query": "MCP navigation exact raw evidence",
                "required_sources": ["code"],
                "required_roles": ["implementation"],
            },
        )
        assert search["hits"]
        page = _call(client, "wiki_read", {"path": search["hits"][0]["logical_path"]})
        assert page["scope"]["acl_refs"] == ["project:project-rag"]
        raw = _call(
            client,
            "evidence_read",
            {
                "page_path": page["logical_path"],
                "source_ref_id": page["source_refs"][0]["source_ref_id"],
            },
        )
        assert raw["evidence"]["locator"] == page["source_refs"][0]["locator"]
        navigation = _call(
            client,
            "wiki_navigate",
            {
                "query": "Wiki MCP implementation",
                "required_sources": ["code"],
                "required_roles": ["implementation"],
                "max_raw_reads": 4,
                "empty_search_patience": 2,
                "retrieval_deadline_ms": 5_000,
            },
        )
        assert navigation["stop_reason"] == "evidence_complete"
        assert navigation["raw_verify_count"] >= 1
        assert navigation["raw_read_count"] <= 4
        assert navigation["retrieval_deadline_ms"] == 5_000

        pages = client.get("/v1/wiki/pages?project_id=project-rag").json()["pages"]
        linked = next((item for item in pages if item["links"]), None)
        if linked is not None:
            followed = _call(client, "wiki_follow", {"path": linked["logical_path"]})
            assert followed["source_path"] == linked["logical_path"]
        resources = _mcp(client, "resources/list")["result"]["resources"]
        wiki_resource = next(item for item in resources if item["uri"].startswith("wiki://"))
        resource = _mcp(client, "resources/read", {"uri": wiki_resource["uri"]})
        assert resource["result"]["contents"][0]["mimeType"] == "application/json"

        blocked = _mcp(
            client,
            "tools/call",
            {
                "name": "wiki_search",
                "arguments": {"project_id": "project-other", "query": "secret"},
            },
        )["result"]
        assert blocked["isError"] is True


def test_agent_patch_tool_accepts_only_unreviewed_content_addressed_proposals(settings) -> None:
    configured = replace(settings, mcp_token="wiki-agent-token")
    with TestClient(create_app(configured)) as client:
        manifest = _publish(client)
        pages = client.get("/v1/wiki/pages?project_id=project-rag").json()["pages"]
        page = WikiPageFragmentV1.model_validate(pages[0])
        after = build_wiki_page_fragment_v1(
            **page.model_dump(mode="python", exclude={"content_sha256", "summary"}),
            summary=page.summary + " Agent-proposed clarification awaiting human review.",
        )
        operation = build_wiki_patch_operation_v1(
            action=WikiPatchActionV1.UPSERT_PAGE,
            visibility_partition=page.scope.visibility_partition,
            logical_path=page.logical_path,
            before_sha256=page.content_sha256,
            after_page=after,
        )
        unreviewed = build_wiki_builder_patch_v1(
            base_manifest_sha256=manifest["content_sha256"],
            operations=(operation,),
            affected_query_ids=("affected-agent-query",),
            guard_query_ids=("guard-agent-query",),
            trigger_error_entry_ids=(),
            origin=WikiPatchOriginV1.AGENT_UNREVIEWED,
            reviewed=False,
            reviewer_authority_sha256=None,
        )
        accepted = _call(
            client,
            "wiki_propose_patch",
            {
                "patch": unreviewed.model_dump(mode="json"),
                "idempotency_key": "wiki-agent-proposal-001",
            },
        )
        assert accepted["state"] == "staged_for_human_review"
        assert accepted["production_authorized"] is False
        repeated = _call(
            client,
            "wiki_propose_patch",
            {
                "patch": unreviewed.model_dump(mode="json"),
                "idempotency_key": "wiki-agent-proposal-001",
            },
        )
        assert repeated == accepted

        reviewed = build_wiki_builder_patch_v1(
            base_manifest_sha256=manifest["content_sha256"],
            operations=(operation,),
            affected_query_ids=("affected-agent-query",),
            guard_query_ids=("guard-agent-query",),
            trigger_error_entry_ids=(),
            origin=WikiPatchOriginV1.AGENT_REVIEWED,
            reviewed=True,
            reviewer_authority_sha256=canonical_sha256_v1("fake-agent-reviewer"),
        )
        rejected = _mcp(
            client,
            "tools/call",
            {
                "name": "wiki_propose_patch",
                "arguments": {
                    "patch": reviewed.model_dump(mode="json"),
                    "idempotency_key": "wiki-agent-proposal-002",
                },
            },
        )["result"]
        assert rejected["isError"] is True
        assert "unreviewed" in rejected["content"][0]["text"].lower()
