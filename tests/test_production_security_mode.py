from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def test_production_settings_require_authentication_and_acl(settings) -> None:
    with pytest.raises(ValueError, match="RAG_API_TOKEN"):
        replace(
            settings,
            deployment_mode="production",
            api_token=None,
            enforce_acl=True,
        )
    with pytest.raises(ValueError, match="RAG_ENFORCE_ACL"):
        replace(
            settings,
            deployment_mode="production",
            api_token="production-token",
            enforce_acl=False,
        )


def test_production_access_rejects_unauthenticated_and_untrusted_acl_headers(settings) -> None:
    configured = replace(
        settings,
        deployment_mode="production",
        api_token="production-token",
        enforce_acl=True,
        trusted_acl_refs=("project:project-rag",),
    )
    app = create_app(configured)
    with TestClient(app) as client:
        unauthenticated = client.get("/v1/projects")
        untrusted = client.get(
            "/v1/projects",
            headers={
                "Authorization": "Bearer production-token",
                "X-RAG-ACL-Refs": "project:other",
            },
        )
        authenticated = client.get(
            "/v1/projects",
            headers={
                "Authorization": "Bearer production-token",
                "X-RAG-ACL-Refs": "project:project-rag",
            },
        )

    assert unauthenticated.status_code == 401
    assert untrusted.status_code == 403
    assert authenticated.status_code == 200


def test_production_mode_protects_v1_routes_without_acl_dependencies(settings) -> None:
    app = create_app(
        replace(
            settings,
            deployment_mode="production",
            api_token="production-token",
            enforce_acl=True,
            trusted_acl_refs=("project:project-rag",),
        )
    )
    with TestClient(app) as client:
        missing = client.get("/v1/stats")
        invalid = client.get(
            "/v1/codex/config",
            headers={"Authorization": "Bearer wrong"},
        )
        allowed = client.get(
            "/v1/stats",
            headers={"Authorization": "Bearer production-token"},
        )
        health = client.get("/health")

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert allowed.status_code == 200
    assert health.status_code == 200
