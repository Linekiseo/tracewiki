from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.models import QueryRequest
from evidence_rag.query.intelligence import QueryUnderstandingEngine
from evidence_rag.query.interaction_v2 import ConversationSnapshot
from evidence_rag.query.provider import LLMProviderProjectRequest
from evidence_rag.rag.wiki.contracts_v1 import WikiPageFragmentV1

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "native" / "shared" / "v1"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures"
MANIFEST_PATH = CONTRACT_ROOT / "manifest.json"

_HOST_PATH = re.compile(
    r"(?:/(?:Users|home|private|tmp|var|etc|opt|root)/|(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?:^|\s)\\\\[^\\/\s]+[\\/])",
    re.IGNORECASE,
)
_SECRET = re.compile(
    r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
)
_FORBIDDEN_RESPONSE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "allowed_acl_refs",
    "prompt",
    "messages",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _manifest() -> dict[str, Any]:
    return _load(MANIFEST_PATH)


def _operation_by_id() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in _manifest()["operations"]}


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _assert_required_fields(operation: dict[str, Any], fixture: Any) -> None:
    target = fixture[0] if isinstance(fixture, list) else fixture
    assert isinstance(target, dict)
    assert set(operation["required_response_fields"]) <= set(target)


def test_native_manifest_is_a_frozen_projection_of_real_openapi() -> None:
    manifest = _manifest()
    openapi = create_app(defer_runtime=True).openapi()

    assert manifest["contract_version"] == "native-desktop-api-v1"
    assert manifest["backend"] == {
        "openapi_version": openapi["openapi"],
        "title": openapi["info"]["title"],
        "api_version": openapi["info"]["version"],
        "selected_openapi_sha256": manifest["backend"]["selected_openapi_sha256"],
    }

    selected_operations: dict[str, Any] = {}
    for operation in manifest["operations"]:
        actual = openapi["paths"][operation["path"]][operation["method"].lower()]
        assert actual["operationId"] == operation["operation_id"]
        assert _digest(actual) == operation["openapi_sha256"]
        selected_operations[operation["id"]] = actual
        if request_schema := operation.get("request_schema"):
            schema = actual["requestBody"]["content"]["application/json"]["schema"]
            assert schema == {"$ref": f"#/components/schemas/{request_schema}"}

    selected_schema_names = list(manifest["schema_sha256"])
    selected_schemas = {
        name: openapi["components"]["schemas"][name] for name in selected_schema_names
    }
    for name, expected in manifest["schema_sha256"].items():
        assert _digest(selected_schemas[name]) == expected

    selected = {
        "openapi": openapi["openapi"],
        "info": openapi["info"],
        "operations": selected_operations,
        "schemas": selected_schemas,
    }
    assert _digest(selected) == manifest["backend"]["selected_openapi_sha256"]


def test_native_request_and_wiki_fixtures_use_production_pydantic_contracts() -> None:
    query_payload = _load(FIXTURE_ROOT / "query.request.json")
    request = QueryRequest.model_validate(query_payload)
    assert request.scope.project_id == "project-rag"
    assert request.scope.allowed_acl_refs == []
    assert request.scope.enforce_acl is False
    assert "allowed_acl_refs" not in request.model_dump(mode="json")["scope"]

    provider_request = LLMProviderProjectRequest.model_validate({"project_id": "project-rag"})
    assert provider_request.model_dump() == {"project_id": "project-rag"}

    detail_payload = _load(FIXTURE_ROOT / "wiki-detail.response.json")
    detail = WikiPageFragmentV1.model_validate(detail_payload)
    pages = _load(FIXTURE_ROOT / "wiki-pages.response.json")
    assert pages["pages"] == [detail.model_dump(mode="json")]

    query_response = _load(FIXTURE_ROOT / "query.response.json")
    snapshot = ConversationSnapshot.model_validate(query_response["conversation_snapshot"])
    assert snapshot.scope_digest == query_response["conversation_snapshot"]["scope_digest"]

    actual_understanding = QueryUnderstandingEngine().local(
        request.question,
        explicit_intent=request.intent,
        requested_sources=request.include,
    )
    frozen_understanding = query_response["query_understanding"]
    assert actual_understanding.strategy == frozen_understanding["strategy"]
    assert actual_understanding.intent == frozen_understanding["intent"]
    assert (
        actual_understanding.public(include_queries=True)["content_digest"]
        == frozen_understanding["content_digest"]
    )


def test_native_fixtures_cover_operations_and_are_safe_for_desktop_decoders() -> None:
    manifest = _manifest()
    response_paths: set[Path] = set()
    for operation in manifest["operations"]:
        if fixture_name := operation.get("fixture"):
            fixture_path = CONTRACT_ROOT / fixture_name
            assert fixture_path.is_file()
            fixture = _load(fixture_path)
            _assert_required_fields(operation, fixture)
            response_paths.add(fixture_path)
        if request_fixture := operation.get("request_fixture"):
            assert (CONTRACT_ROOT / request_fixture).is_file()

    response_paths.add(CONTRACT_ROOT / manifest["errors"]["fixture"])
    for fixture_path in response_paths:
        payload = _load(fixture_path)
        serialized = json.dumps(payload, ensure_ascii=False)
        assert _HOST_PATH.search(serialized) is None
        assert _SECRET.search(serialized) is None
        for key, _ in _walk(payload):
            assert key.casefold() not in _FORBIDDEN_RESPONSE_KEYS

    provider_status = _load(FIXTURE_ROOT / "provider-public-status.response.json")
    provider_test = _load(FIXTURE_ROOT / "provider-test.response.json")
    assert provider_status["api_key_present"] is False
    assert provider_test["api_key_present"] is True
    assert "api_key" not in provider_test


def test_native_pagination_and_errors_match_openapi_constraints() -> None:
    openapi = create_app(defer_runtime=True).openapi()
    operations = _operation_by_id()

    wiki_parameters = {
        item["name"]: item["schema"]
        for item in openapi["paths"]["/v1/wiki/pages"]["get"]["parameters"]
        if item["in"] == "query"
    }
    wiki_page = operations["wiki.pages"]["pagination"]
    assert wiki_parameters["offset"]["default"] == wiki_page["offset_default"]
    assert wiki_parameters["offset"]["minimum"] == wiki_page["offset_min"]
    assert wiki_parameters["limit"]["default"] == wiki_page["limit_default"]
    assert wiki_parameters["limit"]["minimum"] == wiki_page["limit_min"]
    assert wiki_parameters["limit"]["maximum"] == wiki_page["limit_max"]

    for operation_id, path in (
        ("query.history", "/v1/query/history"),
        ("agent.audit", "/v1/codex-bridge/agents/audit"),
    ):
        parameters = {
            item["name"]: item["schema"]
            for item in openapi["paths"][path]["get"]["parameters"]
            if item["in"] == "query"
        }
        pagination = operations[operation_id]["pagination"]
        assert parameters["limit"]["default"] == pagination["limit_default"]
        assert parameters["limit"]["minimum"] == pagination["limit_min"]
        assert parameters["limit"]["maximum"] == pagination["limit_max"]

    errors = _load(FIXTURE_ROOT / "errors.response.json")
    assert {item["status"] for item in errors.values()} == {404, 422, 502}
    assert errors["provider_failure"]["body"]["detail"] == "llm_provider_test_failed"
    assert errors["query_provider_failure"]["body"]["detail"] == "answer_provider_failed"


def test_native_public_shapes_match_isolated_runtime(settings) -> None:
    query_request = _load(FIXTURE_ROOT / "query.request.json")
    query_fixture = _load(FIXTURE_ROOT / "query.response.json")
    project_fixture = _load(FIXTURE_ROOT / "project-list.response.json")[0]
    provider_fixture = _load(FIXTURE_ROOT / "provider-public-status.response.json")
    wiki_fixture = _load(FIXTURE_ROOT / "wiki-pages.response.json")
    agent_status_fixture = _load(FIXTURE_ROOT / "agent-status.response.json")
    agent_fixture = _load(FIXTURE_ROOT / "agent-list.response.json")[0]
    audit_fixture = _load(FIXTURE_ROOT / "agent-audit.response.json")
    error_fixture = _load(FIXTURE_ROOT / "errors.response.json")

    with TestClient(create_app(settings)) as client:
        projects = client.get("/v1/projects")
        assert projects.status_code == 200
        assert set(projects.json()[0]) == set(project_fixture)

        repositories = client.get(
            "/v1/repositories", params={"project_id": "project-rag"}
        )
        assert repositories.status_code == 200
        if repositories.json():
            repository_fixture = _load(FIXTURE_ROOT / "repository-list.response.json")[0]
            assert set(repository_fixture) <= set(repositories.json()[0])

        provider = client.get("/v1/ai/provider", params={"project_id": "project-rag"})
        assert provider.status_code == 200
        assert provider.json() == provider_fixture

        answer = client.post("/v1/query", json=query_request)
        assert answer.status_code == 200
        assert set(query_fixture) <= set(answer.json())
        assert set(query_fixture["answer"]) <= set(answer.json()["answer"])
        assert set(query_fixture["interaction"]) <= set(answer.json()["interaction"])

        history = client.get(
            "/v1/query/history", params={"project_id": "project-rag", "limit": 100}
        )
        assert history.status_code == 200
        assert set(history.json()) == {"contract_version", "project_id", "items"}
        assert history.json()["items"]
        assert '"question":' not in history.text
        assert "allowed_acl_refs" not in history.text

        pages = client.get(
            "/v1/wiki/pages",
            params={"project_id": "project-rag", "offset": 0, "limit": 50},
        )
        assert pages.status_code == 200
        assert set(pages.json()) == set(wiki_fixture)
        assert pages.json()["offset"] == 0
        assert pages.json()["limit"] == 50

        missing_page = client.get(
            "/v1/wiki/read",
            params={"project_id": "project-rag", "path": "/components/missing"},
        )
        assert missing_page.status_code == 404
        assert missing_page.json()["detail"] == "Wiki path not found"

        status = client.get("/v1/codex-bridge/status", params={"project_id": "project-rag"})
        assert status.status_code == 200
        assert set(status.json()) == set(agent_status_fixture)

        agents = client.get("/v1/codex-bridge/agents", params={"project_id": "project-rag"})
        assert agents.status_code == 200
        assert set(agents.json()[0]) == set(agent_fixture)

        audit = client.get(
            "/v1/codex-bridge/agents/audit",
            params={"project_id": "project-rag", "limit": 100},
        )
        assert audit.status_code == 200
        assert audit.json() == audit_fixture

        provider_test = client.post("/v1/ai/provider/test", json={"project_id": "project-rag"})
        assert provider_test.status_code == 502
        assert provider_test.json() == error_fixture["provider_failure"]["body"]

        invalid = client.get("/v1/wiki/pages")
        assert invalid.status_code == 422
        assert isinstance(invalid.json()["detail"], list)
        assert set(invalid.json()["detail"][0]) >= {"type", "loc", "msg", "input"}
