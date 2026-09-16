from __future__ import annotations

from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def test_native_desktop_origin_can_preflight_without_opening_web_cors(settings):
    app = create_app(settings)
    client = TestClient(app)

    allowed = client.options(
        "/health",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "tauri://localhost"

    untrusted = client.options(
        "/health",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert untrusted.status_code == 400
    assert "access-control-allow-origin" not in untrusted.headers
