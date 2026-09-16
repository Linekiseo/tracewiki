import json
import os
from dataclasses import replace

from fastapi.testclient import TestClient

from evidence_rag import codex_discovery
from evidence_rag.api import create_app
from evidence_rag.models import CodexIngestRequest, CodexSearchRequest
from evidence_rag.runtime import create_runtime
from evidence_rag.security import redact_secrets


def _write_rollout(path, *, thread_id: str, cwd: str, subagent: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    source = {"subagent": True} if subagent else "cli"
    rows = [
        {
            "type": "session_meta",
            "timestamp": "2026-08-01T00:00:00Z",
            "payload": {"id": thread_id, "cwd": cwd, "source": source},
        },
        {
            "type": "event_msg",
            "timestamp": "2026-08-01T00:00:01Z",
            "payload": {"type": "task_started", "turn_id": f"{thread_id}-turn"},
        },
        {
            "type": "event_msg",
            "timestamp": "2026-08-01T00:00:02Z",
            "payload": {
                "type": "user_message",
                "id": f"{thread_id}-goal",
                "message": f"Goal for {thread_id}",
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-08-01T00:00:03Z",
            "payload": {"type": "task_complete", "turn_id": f"{thread_id}-turn"},
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_codex_rollout_becomes_scoped_searchable_evidence(
    settings, sample_repository, sample_codex_home
) -> None:
    runtime = create_runtime(settings)
    request = CodexIngestRequest(source=str(sample_codex_home), project_path=str(sample_repository))
    workflow_id = runtime.codex_ingestion.enqueue(request)
    runtime.codex_ingestion.run(workflow_id, request)

    workflow = runtime.store.get_workflow(workflow_id)
    assert workflow is not None
    assert workflow["status"] == "completed"
    assert workflow["counters"]["sessions"] == 1
    assert workflow["counters"]["turns"] == 1
    assert workflow["counters"]["commands"] == 1
    assert workflow["counters"]["file_changes"] == 1
    assert workflow["counters"]["excluded_reasoning"] == 1
    assert workflow["counters"]["redacted_items"] == 1

    sessions = runtime.store.list_codex_threads(project_id="project-rag")
    assert len(sessions) == 1
    assert sessions[0]["title"] == "Implement rerank retrieval"
    assert sessions[0]["turn_count"] == 1
    assert sessions[0]["id"].startswith("codex://thread/")

    detail = runtime.store.get_codex_thread(sessions[0]["thread_id"])
    assert detail is not None
    content = "\n".join(item["content"] for turn in detail["turns"] for item in turn["items"])
    assert "private chain of thought" not in content
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in content
    assert "[REDACTED:github_token]" in content
    assert any(edge["edge_type"] == "OUTPUT_OF" for edge in detail["edges"])

    result = runtime.codex_retriever.search(CodexSearchRequest(query="rerank retrieval", limit=5))
    assert result["results"]
    assert result["trace"]["fusion"] == "codex-weighted-hybrid-v2"
    assert result["trace"]["dense_matches"] <= result["trace"]["dense_candidates"]
    assert any(item["item_type"] == "DevelopmentEpisode" for item in result["results"])
    assert all(item["evidence_locator"].startswith("codex://") for item in result["results"])


def test_codex_session_ingestion_and_search_api(
    settings, sample_repository, sample_codex_home
) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/ingestion/codex",
            json={
                "source": str(sample_codex_home),
                "project_path": str(sample_repository),
            },
        )
        assert response.status_code == 202
        workflow = client.get(f"/v1/ingestion/workflows/{response.json()['workflow_id']}").json()
        assert workflow["status"] == "completed"

        sessions = client.get("/v1/codex/sessions").json()
        assert len(sessions) == 1
        detail = client.get(f"/v1/codex/sessions/{sessions[0]['thread_id']}")
        assert detail.status_code == 200
        assert detail.json()["turns"][0]["items"]
        timeline = client.get(f"/v1/codex/sessions/{sessions[0]['thread_id']}/timeline")
        assert timeline.status_code == 200
        assert timeline.json()["turn_count"] == 1
        assert timeline.json()["turns"][0]["goal"]
        assert timeline.json()["turns"][0]["operations"] == []
        assert "items" not in timeline.json()["turns"][0]
        turn_audit = client.get(
            f"/v1/codex/sessions/{sessions[0]['thread_id']}/turn-audit",
            params={"turn_id": timeline.json()["turns"][0]["id"]},
        )
        assert turn_audit.status_code == 200
        assert turn_audit.json()["operations"]
        assert turn_audit.json()["operations"][0]["locator"].startswith("codex://")

        search = client.post(
            "/v1/codex/search",
            json={"query": "src/retrieval.py", "include_edges": True},
        )
        assert search.status_code == 200
        assert search.json()["results"]
        assert client.get("/v1/codex/stats").json()["sessions"] == 1
        graph = client.get("/v1/graph", params={"domain": "codex", "limit": 24}).json()
        assert {"CodexThread", "CodexTurn"} <= {node["type"] for node in graph["nodes"]}
        assert {"HAS_TURN", "HAS_ITEM"} <= {edge["predicate"] for edge in graph["edges"]}
        file_change = next(node for node in graph["nodes"] if node["type"] == "FileChange")
        assert file_change["path"] == "src/retrieval.py"
        assert file_change["label"] == "src/retrieval.py"
        focused_graph = client.get(
            "/v1/graph",
            params={
                "project_id": sessions[0]["project_id"],
                "domain": "codex",
                "query": sessions[0]["thread_id"],
                "limit": 96,
            },
        )
        assert focused_graph.status_code == 200
        focused_nodes = focused_graph.json()["nodes"]
        assert [node["id"] for node in focused_nodes if node["type"] == "CodexThread"] == [
            sessions[0]["id"]
        ]
        assert graph["metadata"]["vector_views"] > 0


def test_codex_timeline_returns_bounded_turn_windows(settings, tmp_path) -> None:
    stream = tmp_path / "paged-session.jsonl"
    rows: list[dict] = [
        {"type": "thread.started", "thread_id": "paged-thread", "cwd": str(tmp_path)}
    ]
    for ordinal in range(1, 31):
        turn_id = f"paged-turn-{ordinal:02d}"
        rows.extend(
            [
                {"type": "turn.started", "turn_id": turn_id},
                {
                    "type": "item.completed",
                    "item": {
                        "id": f"goal-{ordinal:02d}",
                        "type": "user_message",
                        "message": f"Goal {ordinal:02d}",
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": f"command-{ordinal:02d}",
                        "type": "command_execution",
                        "command": f"pytest tests/test_{ordinal:02d}.py",
                        "aggregated_output": "1 passed",
                        "exit_code": 0,
                    },
                },
                {"type": "turn.completed", "turn_id": turn_id},
            ]
        )
    stream.write_text("\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/ingestion/codex",
            json={"source": str(stream), "project_path": str(tmp_path), "max_sessions": 1},
        )
        assert response.status_code == 202
        first = client.get(
            "/v1/codex/sessions/paged-thread/timeline",
            params={"turn_offset": 0, "turn_limit": 8},
        )
        assert first.status_code == 200
        first_payload = first.json()
        assert first_payload["turn_count"] == 30
        assert first_payload["visible_turn_count"] == 8
        assert first_payload["has_more_turns"] is True
        assert [turn["ordinal"] for turn in first_payload["turns"]] == list(range(23, 31))

        older = client.get(
            "/v1/codex/sessions/paged-thread/timeline",
            params={"turn_offset": 24, "turn_limit": 8},
        ).json()
        assert older["visible_turn_count"] == 6
        assert older["has_more_turns"] is False
        assert [turn["ordinal"] for turn in older["turns"]] == list(range(1, 7))

        focused_turn_id = older["turns"][4]["id"]
        focused_graph = client.get(
            "/v1/graph",
            params={
                "project_id": "project-rag",
                "domain": "codex",
                "query": "paged-thread",
                "focus_entity_id": focused_turn_id,
                "limit": 96,
            },
        )
        assert focused_graph.status_code == 200
        graph_nodes = focused_graph.json()["nodes"]
        graph_turns = [node for node in graph_nodes if node["type"] == "CodexTurn"]
        assert focused_turn_id in {node["id"] for node in graph_turns}
        assert len(graph_turns) <= 12
        assert len(graph_nodes) <= 96


def test_project_session_sync_discovers_and_loads_new_sessions(
    settings, sample_repository, sample_codex_home
) -> None:
    first_thread_id = "11111111-2222-4333-8444-555555555555"
    second_thread_id = "66666666-7777-4888-8999-000000000000"

    with TestClient(create_app(settings)) as client:
        initial = client.post(
            "/v1/ingestion/codex",
            json={
                "source": str(sample_codex_home),
                "project_path": str(sample_repository),
            },
        )
        assert initial.status_code == 202
        assert (
            len(
                client.get(
                    "/v1/codex/sessions",
                    params={"project_id": "project-rag"},
                ).json()
            )
            == 1
        )

        first_rollout = next((sample_codex_home / "sessions").rglob("*.jsonl"))
        second_rollout = first_rollout.with_name(
            f"rollout-2026-07-23T09-00-00-{second_thread_id}.jsonl"
        )
        second_rollout.write_text(
            first_rollout.read_text(encoding="utf-8")
            .replace(first_thread_id, second_thread_id)
            .replace("Implement rerank retrieval", "Audit retrieval evidence"),
            encoding="utf-8",
        )
        with (sample_codex_home / "session_index.jsonl").open(
            "a",
            encoding="utf-8",
        ) as index:
            index.write(
                json.dumps(
                    {
                        "id": second_thread_id,
                        "thread_name": "Audit retrieval evidence",
                        "updated_at": "2026-07-23T09:00:08Z",
                    }
                )
                + "\n"
            )

        pending = client.get("/v1/codex/projects/project-rag/sync-status")
        assert pending.status_code == 200
        assert pending.json()["discovered_sessions"] == 2
        assert pending.json()["indexed_sessions"] == 1
        assert pending.json()["new_sessions"] == 1
        assert pending.json()["needs_sync"] is True

        sync = client.post(
            "/v1/codex/projects/project-rag/sync",
            json={"force": False},
        )
        assert sync.status_code == 202
        assert sync.json()["status"] == "queued"
        workflow = client.get(f"/v1/ingestion/workflows/{sync.json()['workflow_id']}").json()
        assert workflow["status"] == "completed"
        assert workflow["counters"]["sessions"] == 2

        sessions = client.get(
            "/v1/codex/sessions",
            params={"project_id": "project-rag"},
        ).json()
        assert {item["thread_id"] for item in sessions} == {
            first_thread_id,
            second_thread_id,
        }
        current = client.get("/v1/codex/projects/project-rag/sync-status").json()
        assert current["indexed_sessions"] == 2
        assert current["new_sessions"] == 0
        assert current["needs_sync"] is False


def test_empty_codex_resync_preserves_last_published_generation(
    settings, sample_repository, sample_codex_home
) -> None:
    runtime = create_runtime(settings)
    request = CodexIngestRequest(
        source=str(sample_codex_home),
        project_path=str(sample_repository),
    )
    first_id = runtime.codex_ingestion.enqueue(request)
    runtime.codex_ingestion.run(first_id, request)
    before = runtime.store.list_codex_threads(project_id="project-rag")
    assert len(before) == 1

    for rollout in (sample_codex_home / "sessions").rglob("*.jsonl"):
        rollout.unlink()
    second_id = runtime.codex_ingestion.enqueue(request)
    runtime.codex_ingestion.run(second_id, request)

    failed = runtime.store.get_workflow(second_id)
    assert failed is not None
    assert failed["status"] == "failed"
    assert "previously published generation was preserved" in failed["error"]
    after = runtime.store.list_codex_threads(project_id="project-rag")
    assert [item["thread_id"] for item in after] == [item["thread_id"] for item in before]
    assert runtime.store.list_codex_sources()[0]["status"] == "ready"


def test_secret_redaction_removes_complete_private_key_and_api_key() -> None:
    content = (
        "-----BEGIN PRIVATE KEY-----\nprivate-key-body\n-----END PRIVATE KEY-----\n"
        "api_key=sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    )
    redacted, findings = redact_secrets(content)
    assert "private-key-body" not in redacted
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert {"private_key", "openai_api_key"} <= set(findings)


def test_project_discovery_is_not_crowded_out_and_scans_late_binding_metadata(
    settings, tmp_path
) -> None:
    codex_home = tmp_path / "discovery-home"
    sessions = codex_home / "sessions"
    project = tmp_path / "target-project"
    other = tmp_path / "other-project"
    project.mkdir()
    other.mkdir()

    for index in range(25):
        path = sessions / f"unrelated-{index:02d}.jsonl"
        _write_rollout(path, thread_id=f"unrelated-{index:02d}", cwd=str(other))
        os.utime(path, (2_000 + index, 2_000 + index))

    target = sessions / "target-late-metadata.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = [
        json.dumps({"type": "noise", "payload": {"value": index}}) + "\n" for index in range(170)
    ]
    target.write_text(
        "".join(prefix)
        + json.dumps(
            {
                "type": "session_meta",
                "timestamp": "2026-08-01T00:00:00Z",
                "payload": {"id": "target-late", "cwd": str(project), "source": "cli"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(target, (1_000, 1_000))
    configured = replace(settings, codex_home=codex_home, project_root=project)

    with TestClient(create_app(configured)) as client:
        status = client.get(
            "/v1/codex/projects/project-rag/sync-status",
            params={"max_sessions": 1},
        ).json()

    assert status["discovered_sessions"] == 1
    assert status["indexable_sessions"] == 1
    assert status["discovery"]["candidate_sessions"] == 26
    assert status["discovery"]["excluded_other_project_sessions"] == 25
    assert status["discovery"]["candidate_limit_applied"] is False
    assert status["discovery"]["complete"] is True


def test_project_match_only_decodes_binding_records_and_stops_at_session_authority(
    tmp_path, monkeypatch
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    target = tmp_path / "late-target.jsonl"
    target.write_text(
        "".join(
            json.dumps({"type": "event_msg", "payload": {"message": str(index)}}) + "\n"
            for index in range(10_000)
        )
        + json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "late-target", "cwd": str(project)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    original_loads = codex_discovery.json.loads
    decoded_rows = 0

    def counted_loads(value):
        nonlocal decoded_rows
        decoded_rows += 1
        return original_loads(value)

    monkeypatch.setattr(codex_discovery.json, "loads", counted_loads)
    assert codex_discovery.match_codex_session_project(target, project) == (
        True,
        "matched",
    )
    assert decoded_rows == 1

    unrelated = tmp_path / "unrelated.jsonl"
    unrelated.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "unrelated", "cwd": str(other)},
            }
        )
        + "\n"
        + "".join(
            json.dumps({"type": "event_msg", "payload": {"message": str(index)}}) + "\n"
            for index in range(10_000)
        ),
        encoding="utf-8",
    )
    decoded_rows = 0
    assert codex_discovery.match_codex_session_project(unrelated, project) == (
        False,
        "different_project",
    )
    assert decoded_rows == 1


def test_session_visibility_filters_before_pagination_and_incomplete_scan_blocks_publish(
    settings, tmp_path
) -> None:
    codex_home = tmp_path / "visibility-home"
    sessions = codex_home / "sessions"
    project = tmp_path / "visibility-project"
    project.mkdir()
    normal = sessions / "normal.jsonl"
    subagent = sessions / "subagent.jsonl"
    _write_rollout(normal, thread_id="normal-thread", cwd=str(project))
    _write_rollout(
        subagent,
        thread_id="subagent-thread",
        cwd=str(project),
        subagent=True,
    )
    os.utime(normal, (1_000, 1_000))
    os.utime(subagent, (2_000, 2_000))
    configured = replace(settings, codex_home=codex_home, project_root=project)

    with TestClient(create_app(configured)) as client:
        ingested = client.post(
            "/v1/ingestion/codex",
            json={"source": str(codex_home), "project_path": str(project)},
        ).json()
        workflow = client.get(f"/v1/ingestion/workflows/{ingested['workflow_id']}").json()
        assert workflow["status"] == "completed"

        visible = client.get(
            "/v1/codex/sessions", params={"project_id": "project-rag", "limit": 1}
        ).json()
        assert [item["thread_id"] for item in visible] == ["normal-thread"]
        all_sessions = client.get(
            "/v1/codex/sessions",
            params={"project_id": "project-rag", "limit": 2, "include_subagents": True},
        ).json()
        assert {item["thread_id"] for item in all_sessions} == {
            "normal-thread",
            "subagent-thread",
        }

        unsafe = sessions / "unsafe-link.jsonl"
        unsafe.symlink_to(normal)
        status = client.get("/v1/codex/projects/project-rag/sync-status").json()
        assert status["discovery"]["complete"] is False
        assert status["discovery"]["visibility_state"] == "partial_fail_closed"
        assert status["sync_blocked_reason"] == "discovery_incomplete"
        assert status["needs_sync"] is False

        sync = client.post(
            "/v1/codex/projects/project-rag/sync",
            json={"force": True},
        ).json()
        failed = client.get(f"/v1/ingestion/workflows/{sync['workflow_id']}").json()
        assert failed["status"] == "failed"
        assert "refusing to publish a partial generation" in failed["error"]
        preserved = client.get(
            "/v1/codex/sessions",
            params={"project_id": "project-rag", "include_subagents": True},
        ).json()
        assert {item["thread_id"] for item in preserved} == {
            "normal-thread",
            "subagent-thread",
        }


def test_public_noninteractive_jsonl_is_normalized(settings, tmp_path) -> None:
    stream = tmp_path / "codex-events.jsonl"
    rows = [
        {"type": "thread.started", "thread_id": "public-thread", "cwd": str(tmp_path)},
        {"type": "turn.started", "turn_id": "public-turn"},
        {
            "type": "item.completed",
            "item": {
                "id": "command-1",
                "type": "command_execution",
                "command": "pytest -q",
                "aggregated_output": "11 passed",
                "exit_code": 0,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "change-1",
                "type": "file_change",
                "changes": [{"path": "src/ranker.py", "kind": "update"}],
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "answer-1",
                "type": "agent_message",
                "text": "Ranking tests pass.",
            },
        },
        {"type": "turn.completed", "turn_id": "public-turn"},
    ]
    stream.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    runtime = create_runtime(settings)
    request = CodexIngestRequest(source=str(stream), project_path=str(tmp_path), max_sessions=1)
    workflow_id = runtime.codex_ingestion.enqueue(request)
    runtime.codex_ingestion.run(workflow_id, request)

    workflow = runtime.store.get_workflow(workflow_id)
    assert workflow["status"] == "completed"
    assert workflow["counters"]["sessions"] == 1
    assert workflow["counters"]["commands"] == 1
    assert workflow["counters"]["file_changes"] == 1
    assert workflow["counters"]["validations"] == 1
    detail = runtime.store.get_codex_thread("public-thread")
    assert detail is not None
    assert detail["metadata"]["format"] == "public-jsonl"
    validation = next(
        item
        for turn in detail["turns"]
        for item in turn["items"]
        if item["item_type"] == "ValidationResult"
    )
    assert validation["status"] == "passed"
    assert validation["metadata"]["exit_code"] == 0
    assert any(edge["edge_type"] == "VALIDATED_BY" for edge in detail["edges"])
    timeline_summary = runtime.store.get_codex_thread_timeline("public-thread")
    assert timeline_summary is not None
    assert timeline_summary["turns"][0]["operations"] == []
    timeline = runtime.store.get_codex_thread_timeline(
        "public-thread",
        detail_turn_id=timeline_summary["turns"][0]["id"],
    )
    assert timeline is not None
    turn = timeline["turns"][0]
    command = next(item for item in turn["operations"] if item["kind"] == "validation")
    assert command["command"] == "pytest -q"
    assert command["output"] == "11 passed"
    assert command["exit_code"] == 0
    assert command["status"] == "passed"
    assert command["locator"].startswith("codex://")
    assert turn["file_changes"][0]["path"] == "src/ranker.py"
    assert turn["file_changes"][0]["locator"].startswith("codex://")


def test_codex_transport_noise_is_removed_from_indexed_items(settings, tmp_path) -> None:
    stream = tmp_path / "noisy-codex-events.jsonl"
    context = (
        '<in-app-browser-context source="ambient-ui-state">\n'
        "Current URL: http://127.0.0.1:8000/#codex\n"
        "</in-app-browser-context>\n\n"
    )
    rows = [
        {"type": "thread.started", "thread_id": "clean-thread", "cwd": str(tmp_path)},
        {"type": "turn.started", "turn_id": "clean-turn"},
        {
            "type": "item.completed",
            "item": {
                "id": "goal-1",
                "type": "user_message",
                "message": (
                    context
                    + "## My request for Codex:\n"
                    + "优化\u200b Codex 会话解析\x1b[31m。\x1b[0m"
                ),
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "interrupt-1",
                "type": "custom_tool_call",
                "name": "write_stdin",
                "call_id": "call-interrupt",
                "input": json.dumps(
                    {
                        "session_id": 6902,
                        "chars": "\u0003",
                        "yield_time_ms": 1000,
                        "max_output_tokens": 5000,
                    }
                ),
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "poll-1",
                "type": "custom_tool_call",
                "name": "write_stdin",
                "call_id": "call-poll",
                "input": json.dumps(
                    {
                        "session_id": 6902,
                        "chars": "",
                        "yield_time_ms": 5000,
                    }
                ),
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "tool-1",
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "call-1",
                "input": json.dumps({"value": "const useful = true;"}),
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "command-1",
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "call-2",
                "input": json.dumps(
                    {
                        "value": (
                            'const r = await tools.exec_command({"cmd":"pytest -q"}); '
                            "text(r.output);"
                        )
                    }
                ),
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "result-1",
                "type": "custom_tool_call_output",
                "call_id": "call-1",
                "output": (
                    json.dumps(
                        {
                            "i": 0,
                            "output": (
                                "Script completed\n"
                                "Wall time 0.1 seconds\n"
                                "Output:\n\n"
                                "useful result citeturn0search0 [wordlim: 200]"
                            ),
                        }
                    )
                    + "\n"
                    + json.dumps(
                        {
                            "i": 1,
                            "output": ("Original token count: 2\nOutput:\nsecond result"),
                        }
                    )
                ),
            },
        },
        {"type": "turn.completed", "turn_id": "clean-turn"},
    ]
    stream.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    runtime = create_runtime(settings)
    request = CodexIngestRequest(source=str(stream), project_path=str(tmp_path), max_sessions=1)
    workflow_id = runtime.codex_ingestion.enqueue(request)
    runtime.codex_ingestion.run(workflow_id, request)

    detail = runtime.store.get_codex_thread("clean-thread")
    assert detail is not None
    items = [item for turn in detail["turns"] for item in turn["items"]]
    goal = next(item for item in items if item["item_type"] == "UserGoal")
    tool = next(item for item in items if item["item_type"] == "ToolCall")
    command = next(
        item
        for item in items
        if item["item_type"] == "CommandExecution" and item["content"] == "pytest -q"
    )
    command_summaries = {
        item["content"] for item in items if item["item_type"] == "CommandExecution"
    }
    result = next(item for item in items if item["item_type"] == "ToolResult")
    assert goal["content"] == "优化 Codex 会话解析。"
    assert tool["content"] == "const useful = true;"
    assert command["content"] == "pytest -q"
    assert "中止后台命令" in command_summaries
    assert "等待后台命令继续输出" in command_summaries
    assert all("session_id" not in content for content in command_summaries)
    assert result["content"] == "useful result\nsecond result"
    assert detail["metadata"]["cleaning_version"] == "codex-clean-text-v3"


def test_codex_session_comparison_is_persisted_and_evidence_backed(settings, tmp_path) -> None:
    source = tmp_path / "comparison-sessions"
    source.mkdir()

    def write_session(name: str, thread_id: str, path: str, command: str, exit_code: int) -> None:
        rows = [
            {"type": "thread.started", "thread_id": thread_id},
            {"type": "turn.started", "turn_id": f"{thread_id}-turn"},
            {
                "type": "item.completed",
                "item": {
                    "id": f"{thread_id}-goal",
                    "type": "user_message",
                    "message": "实现并验证 rerank 缓存方案",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": f"{thread_id}-command",
                    "type": "command_execution",
                    "command": command,
                    "aggregated_output": "completed",
                    "exit_code": exit_code,
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": f"{thread_id}-change",
                    "type": "file_change",
                    "changes": [{"path": path, "kind": "update"}],
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": f"{thread_id}-answer",
                    "type": "agent_message",
                    "text": "决定采用 query-aware cache 方案。",
                },
            },
            {"type": "turn.completed", "turn_id": f"{thread_id}-turn"},
        ]
        (source / name).write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

    write_session("baseline.jsonl", "baseline-thread", "src/cache.py", "pytest -q", 0)
    write_session("candidate.jsonl", "candidate-thread", "src/ranker.py", "ruff check .", 0)

    with TestClient(create_app(settings)) as client:
        workflow = client.post(
            "/v1/ingestion/codex", json={"source": str(source), "max_sessions": 10}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        sessions = client.get("/v1/codex/sessions").json()
        assert {item["thread_id"] for item in sessions} == {
            "baseline-thread",
            "candidate-thread",
        }
        comparison = client.post(
            "/v1/codex/comparisons",
            json={
                "thread_ids": ["baseline-thread", "candidate-thread"],
                "baseline_thread_id": "baseline-thread",
                "name": "Cache implementation alternatives",
            },
        )
        assert comparison.status_code == 201
        payload = comparison.json()
        assert payload["result"]["summary"]["thread_count"] == 2
        delta = payload["result"]["comparisons"][0]
        assert delta["deltas"]["changed_paths"]["removed"] == ["src/cache.py"]
        assert delta["deltas"]["changed_paths"]["added"] == ["src/ranker.py"]
        assert payload["result"]["threads"][0]["validations"][0]["exit_code"] == 0
        assert payload["result"]["threads"][0]["decision_candidates"]
        stored = client.get("/v1/codex/comparisons").json()
        assert stored[0]["id"] == payload["id"]
