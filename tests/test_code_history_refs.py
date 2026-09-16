from __future__ import annotations

import sqlite3
import subprocess

from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def _git(root, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_refs_historical_files_and_compare_do_not_change_worktree(settings, tmp_path) -> None:
    root = tmp_path / "versioned-repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "history@example.com")
    _git(root, "config", "user.name", "History Test")
    (root / "analysis.py").write_text("def metric():\n    return 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial analysis")
    main_sha = _git(root, "rev-parse", "HEAD")

    _git(root, "switch", "-c", "experiment")
    _git(root, "mv", "analysis.py", "metrics.py")
    (root / "metrics.py").write_text("def metric():\n    return 2\n", encoding="utf-8")
    (root / "result.bin").write_bytes(b"\x00\x01\x02")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "revise metric on experiment branch")
    experiment_sha = _git(root, "rev-parse", "HEAD")
    _git(root, "switch", "main")
    status_before = _git(root, "status", "--porcelain=v1")

    with TestClient(create_app(settings)) as client:
        workflow = client.post(
            "/v1/ingestion/repositories",
            json={"source": str(root), "history_depth": 20},
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        repository = client.get("/v1/repositories").json()[0]
        repository_id = repository["id"]

        refs = client.get("/v1/code/refs", params={"repository_id": repository_id}).json()
        assert refs["head"]["sha"] == main_sha
        assert refs["default_branch"] == "main"
        assert {item["name"] for item in refs["branches"]} >= {"main", "experiment"}

        files = client.get(
            "/v1/code/files",
            params={"repository_id": repository_id, "ref": "experiment"},
        ).json()
        paths = {item["path"] for item in files}
        assert {"metrics.py", "result.bin"} <= paths
        historical = client.get(
            "/v1/code/file",
            params={
                "repository_id": repository_id,
                "ref": "experiment",
                "path": "metrics.py",
            },
        ).json()
        assert historical["commit_sha"] == experiment_sha
        assert historical["historical"] is True
        assert "return 2" in historical["content"]
        binary = client.get(
            "/v1/code/file",
            params={
                "repository_id": repository_id,
                "ref": "experiment",
                "path": "result.bin",
            },
        ).json()
        assert binary["binary"] is True
        assert binary["content"] is None

        comparison = client.get(
            "/v1/code/compare",
            params={
                "repository_id": repository_id,
                "base_ref": "main",
                "target_ref": "experiment",
            },
        ).json()
        assert comparison["base_sha"] == main_sha
        assert comparison["target_sha"] == experiment_sha
        statuses = {item["status"][0] for item in comparison["changed_files"]}
        assert statuses >= {"A"}

        assert (
            client.get(
                "/v1/code/file",
                params={
                    "repository_id": repository_id,
                    "ref": "experiment",
                    "path": "../.git/config",
                },
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/v1/code/files",
                params={"repository_id": repository_id, "ref": "--all"},
            ).status_code
            == 422
        )

    assert _git(root, "branch", "--show-current") == "main"
    assert _git(root, "status", "--porcelain=v1") == status_before


def test_history_sync_repairs_legacy_head_default_branch(settings, tmp_path) -> None:
    root = tmp_path / "legacy-default-branch"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "history@example.com")
    _git(root, "config", "user.name", "History Test")
    (root / "README.md").write_text("# versioned\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")

    with TestClient(create_app(settings)) as client:
        workflow = client.post("/v1/ingestion/repositories", json={"source": str(root)}).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        repository_id = client.get("/v1/repositories").json()[0]["id"]
        with sqlite3.connect(settings.database_path) as db:
            db.execute(
                "UPDATE repositories SET default_branch='HEAD' WHERE id=?",
                (repository_id,),
            )
            db.execute(
                """INSERT INTO git_branches
                   (id, repository_id, name, head_sha, is_default, observed_at)
                   VALUES ('branch://legacy-origin', ?, 'origin', ?, 0, '2026-07-24T00:00:00Z')""",
                (repository_id, _git(root, "rev-parse", "HEAD")),
            )

        result = client.post(
            "/v1/code/history/sync",
            json={
                "repository_id": repository_id,
                "depth": 20,
                "include_diffs": False,
                "fetch_remote": False,
            },
        )
        assert result.status_code == 200
        refs = client.get("/v1/code/refs", params={"repository_id": repository_id}).json()
        assert refs["default_branch"] == "main"
        assert "origin" not in {item["name"] for item in refs["branches"]}
        assert (
            next(item for item in refs["branches"] if item["name"] == "main")["is_default"] is True
        )


def test_current_query_uses_dirty_worktree_generation_instead_of_clean_branch(
    settings, tmp_path
) -> None:
    root = tmp_path / "dirty-repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "history@example.com")
    _git(root, "config", "user.name", "History Test")
    source = root / "ranking.py"
    source.write_text("def rank_score():\n    return 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial rank score")
    source.write_text("def rank_score():\n    return 2\n", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        workflow = client.post("/v1/ingestion/repositories", json={"source": str(root)}).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        repository = client.get("/v1/repositories").json()[0]
        assert "+dirty." in repository["head_commit"]

        answer = client.post(
            "/v1/query",
            json={
                "question": "How is rank_score currently implemented?",
                "intent": "current_implementation",
                "mode": "evidence",
            },
        ).json()
        assert answer["resolved_scope"]["commit"] == repository["head_commit"]
        missing = {item["role"] for item in answer["evidence_pack"]["missing_evidence"]}
        assert "current_code" not in missing
        assert "tests" in missing
