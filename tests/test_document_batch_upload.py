from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from evidence_rag.api import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_document_batch_upload_reports_each_file(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/documents/upload-batch",
            data={
                "version": "v2",
                "authors": "Research Team, Reviewer",
                "tags": "evaluation, batch",
                "extract_claims": "true",
                "manifest": json.dumps(
                    [
                        {"title": "Ablation report", "version": "v2.1"},
                        {"title": "Limitations note"},
                        {"title": "Unsupported archive"},
                    ]
                ),
            },
            files=[
                (
                    "files",
                    (
                        "ablation.md",
                        b"# Results\n\nClaim: Accuracy reaches 91.4% on the held-out set.",
                        "text/markdown",
                    ),
                ),
                (
                    "files",
                    (
                        "limitations.txt",
                        b"Conclusion: Latency increases under the larger workload.",
                        "text/plain",
                    ),
                ),
                ("files", ("archive.zip", b"not-a-document", "application/zip")),
            ],
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["total"] == 3
        assert payload["succeeded"] == 2
        assert payload["failed"] == 1
        assert [item["status"] for item in payload["items"]] == [
            "ingested",
            "ingested",
            "failed",
        ]
        assert payload["items"][0]["document"]["title"] == "Ablation report"
        assert payload["items"][0]["document"]["version"] == "v2.1"
        assert payload["items"][0]["claim_count"] == 1
        assert payload["items"][1]["document"]["authors"] == ["Research Team", "Reviewer"]
        assert payload["items"][2]["error"] == "unsupported document format: .zip"

        content_response = client.get(
            "/v1/documents/content",
            params={"document_id": payload["items"][0]["document"]["id"]},
        )
        assert content_response.status_code == 200
        assert content_response.headers["content-type"].startswith("text/markdown")
        assert content_response.content.startswith(b"# Results")


def test_document_batch_upload_accepts_more_than_fifty_files(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/documents/upload-batch",
            files=[("files", (f"note-{index}.txt", b"text", "text/plain")) for index in range(51)],
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["total"] == 51
        assert payload["succeeded"] == 51
        assert payload["failed"] == 0


def test_local_document_import_has_no_file_size_gate() -> None:
    sources = "\n".join(
        [
            (ROOT / "src/evidence_rag/documents/router.py").read_text(),
            (ROOT / "src/evidence_rag/documents/service.py").read_text(),
        ]
    )

    assert "50_000_000" not in sources
    assert "document exceeds 50 MB limit" not in sources
