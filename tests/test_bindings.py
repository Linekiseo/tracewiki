from dataclasses import replace

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.bindings.router import TRUSTED_BINDING_REVIEWER
from evidence_rag.bindings.service import BindingService


def test_codex_file_change_binding_review(settings, sample_repository, sample_codex_home) -> None:
    (sample_repository / "src" / "retrieval.py").write_text(
        """def rerank(results: list[float]) -> list[float]:
    return sorted(results, reverse=True)
""",
        encoding="utf-8",
    )
    settings = replace(settings, codex_home=sample_codex_home)
    with TestClient(create_app(settings)) as client:
        repository_workflow = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{repository_workflow['workflow_id']}").json()[
                "status"
            ]
            == "completed"
        )

        codex_workflow = client.post(
            "/v1/ingestion/codex",
            json={
                "source": str(sample_codex_home),
                "project_path": str(sample_repository),
            },
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{codex_workflow['workflow_id']}").json()["status"]
            == "completed"
        )

        scan = client.post("/v1/bindings/scan", json={})
        assert scan.status_code == 200
        assert scan.json()["counts"]["candidates"] == 1

        candidates = client.get(
            "/v1/bindings/candidates", params={"review_status": "unreviewed"}
        ).json()
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["changed_path"] == "src/retrieval.py"
        assert candidate["target_path"] == "src/retrieval.py"
        assert candidate["derivation"] == "path_exact"
        assert candidate["confidence"] == 0.98
        assert candidate["target_symbol_ids"]
        assert candidate["signals"]["commit_context_only"] is True

        detail = client.get("/v1/bindings/by-id", params={"binding_id": candidate["id"]}).json()
        assert "src/retrieval.py" in detail["source"]["content"]
        assert "def rerank" in detail["target"]["content"]

        reviewed = client.post(
            "/v1/bindings/review",
            params={"binding_id": candidate["id"]},
            json={
                "decision": "confirmed",
                "reviewer": "test-reviewer",
                "note": "Path and repository workspace match.",
            },
        )
        assert reviewed.status_code == 200
        result = reviewed.json()
        assert result["binding"]["review_status"] == "confirmed"
        assert result["binding"]["reviewed_by"] == TRUSTED_BINDING_REVIEWER
        assert result["binding"]["reviewed_by"] != "test-reviewer"
        assert result["relation"]["derivation"] == "human_confirmed"
        assert result["relation"]["predicate"] == "changed_path_maps_to"

        stats = client.get("/v1/bindings/stats").json()
        assert stats == {
            "total": 1,
            "pending": 0,
            "confirmed": 1,
            "rejected": 0,
            "high_confidence": 0,
        }

        duplicate_review = client.post(
            "/v1/bindings/review",
            params={"binding_id": candidate["id"]},
            json={"decision": "rejected"},
        )
        assert duplicate_review.status_code == 409

        background = client.post("/v1/bindings/scan/background", json={})
        assert background.status_code == 202
        assert background.json()["status"] == "running"
        background_status = client.get(
            "/v1/bindings/scan/status",
            params={"scan_id": background.json()["scan_id"]},
        )
        assert background_status.status_code == 200
        assert background_status.json()["status"] == "completed"
        assert background_status.json()["counts"]["source_items_processed"] == 1


def test_completed_scan_atomically_prunes_stale_unreviewed_candidates(
    settings, sample_repository, sample_codex_home
) -> None:
    (sample_repository / "src" / "retrieval.py").write_text(
        "def rerank(results):\n    return list(reversed(results))\n",
        encoding="utf-8",
    )
    settings = replace(settings, codex_home=sample_codex_home)
    with TestClient(create_app(settings)) as client:
        repository = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{repository['workflow_id']}").json()["status"]
            == "completed"
        )
        codex = client.post(
            "/v1/ingestion/codex",
            json={"source": str(sample_codex_home), "project_path": str(sample_repository)},
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{codex['workflow_id']}").json()["status"]
            == "completed"
        )
        assert client.post("/v1/bindings/scan", json={}).json()["counts"]["candidates"] == 1
        runtime = client.app.state.runtime
        original = runtime.bindings.store.list_candidates("project-rag", limit=1)[0]
        runtime.bindings.store.upsert_candidate(
            {
                **original,
                "id": "binding://stale-unreviewed",
                "changed_path": "patch-prose/n+",
            }
        )
        assert runtime.bindings.store.stats("project-rag")["pending"] == 2

        rescanned = client.post("/v1/bindings/scan", json={})
        assert rescanned.status_code == 200
        assert rescanned.json()["counts"]["candidates"] == 1
        current = runtime.bindings.store.list_candidates("project-rag", limit=10)
        assert [item["changed_path"] for item in current] == ["src/retrieval.py"]


def test_bulk_binding_review_is_atomic_and_confirms_exact_pending_snapshot(
    settings, sample_repository, sample_codex_home
) -> None:
    (sample_repository / "src" / "retrieval.py").write_text(
        "def rerank(results):\n    return list(reversed(results))\n",
        encoding="utf-8",
    )
    settings = replace(settings, codex_home=sample_codex_home)
    with TestClient(create_app(settings)) as client:
        repository = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{repository['workflow_id']}").json()["status"]
            == "completed"
        )
        codex = client.post(
            "/v1/ingestion/codex",
            json={
                "source": str(sample_codex_home),
                "project_path": str(sample_repository),
            },
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{codex['workflow_id']}").json()["status"]
            == "completed"
        )
        assert client.post("/v1/bindings/scan", json={}).json()["counts"]["candidates"] == 1
        runtime = client.app.state.runtime
        original = runtime.bindings.store.list_candidates("project-rag", limit=1)[0]
        runtime.bindings.store.upsert_candidate(
            {
                **original,
                "id": "binding://duplicate-logical-relation",
                "changed_path": f"./{original['changed_path']}",
            }
        )
        assert client.get("/v1/bindings/stats").json()["pending"] == 2

        relations_before = client.get(
            "/v1/relations", params={"project_id": "project-rag", "limit": 500}
        ).json()
        stale_snapshot = client.post(
            "/v1/bindings/review-all",
            json={
                "project_id": "project-rag",
                "decision": "confirmed",
                "expected_pending": 3,
            },
        )
        assert stale_snapshot.status_code == 409
        assert client.get("/v1/bindings/stats").json()["pending"] == 2
        assert (
            client.get("/v1/relations", params={"project_id": "project-rag", "limit": 500}).json()
            == relations_before
        )

        reviewed = client.post(
            "/v1/bindings/review-all",
            json={
                "project_id": "project-rag",
                "decision": "confirmed",
                "expected_pending": 2,
                "note": "reviewed as one immutable queue",
            },
        )
        assert reviewed.status_code == 200
        assert reviewed.json() == {
            "project_id": "project-rag",
            "decision": "confirmed",
            "reviewed": 2,
            "relations_created": 1,
            "remaining_pending": 0,
        }
        stats = client.get("/v1/bindings/stats").json()
        assert stats["pending"] == 0
        assert stats["confirmed"] == 2
        relations = client.get(
            "/v1/relations", params={"project_id": "project-rag", "limit": 500}
        ).json()
        assert len(relations) == len(relations_before) + 1
        bulk_relation = next(
            relation for relation in relations if relation["metadata"].get("bulk_review")
        )
        assert bulk_relation["review_status"] == "confirmed"
        assert bulk_relation["reviewed_by"] == TRUSTED_BINDING_REVIEWER
        assert len(bulk_relation["metadata"]["binding_ids"]) == 2


def test_bulk_binding_review_reuses_an_already_confirmed_logical_relation(
    settings, sample_repository, sample_codex_home
) -> None:
    (sample_repository / "src" / "retrieval.py").write_text(
        "def rerank(results):\n    return list(reversed(results))\n",
        encoding="utf-8",
    )
    settings = replace(settings, codex_home=sample_codex_home)
    with TestClient(create_app(settings)) as client:
        repository = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{repository['workflow_id']}").json()["status"]
            == "completed"
        )
        codex = client.post(
            "/v1/ingestion/codex",
            json={"source": str(sample_codex_home), "project_path": str(sample_repository)},
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{codex['workflow_id']}").json()["status"]
            == "completed"
        )
        assert client.post("/v1/bindings/scan", json={}).json()["counts"]["candidates"] == 1
        runtime = client.app.state.runtime
        original = runtime.bindings.store.list_candidates("project-rag", limit=1)[0]
        runtime.bindings.store.upsert_candidate(
            {
                **original,
                "id": "binding://duplicate-after-single-review",
                "changed_path": f"./{original['changed_path']}",
            }
        )

        single = client.post(
            "/v1/bindings/review",
            params={"binding_id": original["id"]},
            json={"decision": "confirmed"},
        )
        assert single.status_code == 200
        relation_id = single.json()["relation"]["id"]

        reviewed = client.post(
            "/v1/bindings/review-all",
            json={"project_id": "project-rag", "expected_pending": 1},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["reviewed"] == 1
        assert reviewed.json()["relations_created"] == 0
        relation = next(
            item
            for item in client.get("/v1/relations", params={"limit": 500}).json()
            if item["id"] == relation_id
        )
        assert relation["review_status"] == "confirmed"
        assert relation["reviewed_by"] == TRUSTED_BINDING_REVIEWER
        assert len(relation["metadata"]["binding_ids"]) == 2


def test_binding_path_resolution_respects_workspace_and_absolute_repository(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    repositories = [
        {"id": "repo-first", "local_path": str(first)},
        {"id": "repo-second", "local_path": str(second)},
    ]
    file_indexes = {
        repository["id"]: {
            "exact": {
                "src/shared.py": {
                    "id": f"file-{repository['id']}",
                    "path": "src/shared.py",
                }
            },
            "files": [],
        }
        for repository in repositories
    }
    service = BindingService(store=None, workspace=None)  # type: ignore[arg-type]
    change = {"cwd": str(first)}

    relative = service._matches(change, "src/shared.py", repositories, file_indexes)
    assert [item[0]["id"] for item in relative] == ["repo-first"]

    absolute = service._matches(
        change,
        str(second / "src" / "shared.py"),
        repositories,
        file_indexes,
    )
    assert [item[0]["id"] for item in absolute] == ["repo-second"]

    indexed = service._file_index(
        [
            {"id": "nested", "path": "packages/api/src/retrieval.py"},
            {"id": "other", "path": "docs/readme.md"},
        ]
    )
    unique_suffix = service._matches(
        {"cwd": str(first)},
        "src/retrieval.py",
        [repositories[0]],
        {repositories[0]["id"]: indexed},
    )
    assert unique_suffix[0][1]["id"] == "nested"
    assert unique_suffix[0][2] == "path_suffix_unique"


def test_commit_binding_skips_history_without_observable_patch_truth(tmp_path) -> None:
    class NoHistoryStore:
        def resolve_history_commit(self, repository_id: str, sha: str):
            return None

        def history_candidates(self, repository_id: str, path: str):
            raise AssertionError("history lookup must not run without patch truth")

    service = BindingService(store=NoHistoryStore(), workspace=None)  # type: ignore[arg-type]
    repository = {
        "id": "repo-one",
        "name": "one",
        "local_path": str(tmp_path),
        "active_generation_id": "generation-one",
    }
    change = {
        "content": "Changed src/retrieval.py while reviewing the current implementation.",
        "metadata": {},
        "cwd": str(tmp_path),
    }

    assert service._commit_matches(change, "src/retrieval.py", [repository]) == []


def test_commit_binding_reuses_history_patch_and_commit_authority_caches(tmp_path) -> None:
    class CountingStore:
        history_calls = 0
        commit_calls = 0

        def history_candidates(self, repository_id: str, path: str):
            self.history_calls += 1
            return [
                {
                    "id": "hunk-one",
                    "patch": "@@ -1 +1 @@\n-old\n+new",
                    "patch_hash": "sha256:hunk",
                    "commit_sha": "a" * 40,
                    "committed_at": "2026-08-03T00:00:00+00:00",
                }
            ]

        def resolve_history_commit(self, repository_id: str, sha: str):
            self.commit_calls += 1
            return {"id": "commit-one", "sha": "a" * 40}

    store = CountingStore()
    service = BindingService(store=store, workspace=None)  # type: ignore[arg-type]
    repository = {
        "id": "repo-one",
        "name": "one",
        "local_path": str(tmp_path),
        "active_generation_id": "generation-one",
    }
    change = {
        "content": "@@ -1 +1 @@\n-old\n+new",
        "metadata": {},
        "cwd": str(tmp_path),
        "timestamp": "2026-08-03T00:00:00+00:00",
    }
    history_cache = {}
    commit_cache = {}
    patch_line_cache = {}

    first = service._commit_matches(
        change,
        "src/retrieval.py",
        [repository],
        history_cache=history_cache,
        commit_cache=commit_cache,
        patch_line_cache=patch_line_cache,
    )
    second = service._commit_matches(
        change,
        "src/retrieval.py",
        [repository],
        history_cache=history_cache,
        commit_cache=commit_cache,
        patch_line_cache=patch_line_cache,
    )

    assert first == second
    assert first[0]["derivation"] == "patch_diff_exact"
    assert store.history_calls == 1
    assert store.commit_calls == 1


def test_binding_paths_prefer_authoritative_filechange_payload_over_lossy_metadata() -> None:
    service = BindingService(store=None, workspace=None)  # type: ignore[arg-type]
    change = {
        "item_type": "FileChange",
        "content": '{"/workspace/rag/src/query.py":{"type":"update","unified_diff":"@@"}}',
        "metadata": {
            "paths": [
                "/workspace/rag/src/query.py",
                "@@/n",
                "n+/n+",
                "裁决/n+/n+",
            ]
        },
    }

    assert service._raw_change_paths(change) == ["/workspace/rag/src/query.py"]
    assert service._change_paths(change) == ["/workspace/rag/src/query.py"]


def test_binding_paths_fail_closed_for_patch_prose_and_keep_real_patch_headers() -> None:
    service = BindingService(store=None, workspace=None)  # type: ignore[arg-type]
    change = {
        "item_type": "Patch",
        "content": """*** Begin Patch
*** Update File: src/query/service.py
@@ -1 +1 @@
-old
+new
*** End Patch
""",
        "metadata": {"paths": ["@@/n", "text/n+P0", "src/not-used.py"]},
    }

    assert service._change_paths(change) == ["src/query/service.py"]
    for invalid in ("@@/n", "n+/n+", "裁决/n+/n+", "https://host/file.py", "../x.py"):
        assert service._plausible_path(invalid) is False
