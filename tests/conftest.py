from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    return Settings(
        data_dir=data,
        database_path=data / "test.sqlite3",
        repository_cache=data / "repositories",
        web_dir=Path(__file__).resolve().parents[1] / "web",
        allowed_local_roots=(tmp_path,),
        max_file_bytes=200_000,
        embedding_dimensions=96,
    )


@pytest.fixture
def sample_repository(tmp_path: Path) -> Path:
    root = tmp_path / "sample-code"
    source = root / "src"
    source.mkdir(parents=True)
    (source / "ranking.py").write_text(
        '''"""Ranking primitives."""

def normalize_score(value: float) -> float:
    """Clamp a relevance score to the public range."""
    return max(0.0, min(1.0, value))


class HybridRanker:
    def rank(self, lexical_score: float, dense_score: float) -> float:
        combined = lexical_score * 0.55 + dense_score * 0.45
        return normalize_score(combined)
''',
        encoding="utf-8",
    )
    (source / "service.py").write_text(
        """from .ranking import HybridRanker

class SearchService:
    def __init__(self):
        self.ranker = HybridRanker()

    def search(self, lexical_score: float, dense_score: float) -> float:
        return self.ranker.rank(lexical_score, dense_score)
""",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Sample code\n\nHybrid lexical and dense evidence retrieval.\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def sample_codex_home(tmp_path: Path, sample_repository: Path) -> Path:
    import json

    codex_home = tmp_path / ".codex"
    sessions = codex_home / "sessions" / "2026" / "07" / "23"
    sessions.mkdir(parents=True)
    thread_id = "11111111-2222-4333-8444-555555555555"
    rows = [
        {
            "type": "session_meta",
            "timestamp": "2026-07-23T08:00:00Z",
            "payload": {
                "id": thread_id,
                "cwd": str(sample_repository),
                "originator": "codex_cli_rs",
                "cli_version": "0.200.0",
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-07-23T08:00:01Z",
            "payload": {"type": "task_started", "turn_id": "turn-a"},
        },
        {
            "type": "event_msg",
            "timestamp": "2026-07-23T08:00:02Z",
            "payload": {
                "type": "user_message",
                "id": "goal-1",
                "message": (
                    "Implement rerank retrieval and never expose "
                    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
                ),
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-07-23T08:00:03Z",
            "payload": {"type": "reasoning", "summary": "private chain of thought"},
        },
        {
            "type": "response_item",
            "timestamp": "2026-07-23T08:00:04Z",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-1",
                "arguments": json.dumps({"cmd": "rg -n rerank src"}),
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-07-23T08:00:05Z",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "src/retrieval.py:42:def rerank",
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-07-23T08:00:06Z",
            "payload": {
                "type": "patch_apply_end",
                "call_id": "patch-1",
                "success": True,
                "changes": {"src/retrieval.py": {"kind": "update"}},
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-07-23T08:00:07Z",
            "payload": {
                "type": "agent_message",
                "id": "answer-1",
                "message": "Rerank retrieval is implemented and tests pass.",
            },
        },
        {
            "type": "event_msg",
            "timestamp": "2026-07-23T08:00:08Z",
            "payload": {"type": "task_complete", "turn_id": "turn-a"},
        },
    ]
    rollout = sessions / f"rollout-2026-07-23T08-00-00-{thread_id}.jsonl"
    rollout.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    (codex_home / "session_index.jsonl").write_text(
        json.dumps(
            {
                "id": thread_id,
                "thread_name": "Implement rerank retrieval",
                "updated_at": "2026-07-23T08:00:08Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return codex_home
