from __future__ import annotations

from pathlib import Path
from typing import Any

from evidence_rag import storage
from evidence_rag.storage import SQLiteStore


class _ObservedConnection:
    def __init__(self) -> None:
        self.row_factory: Any = None
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


def test_regular_connection_does_not_reassert_persistent_journal_mode(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    observed = _ObservedConnection()
    monkeypatch.setattr(storage.sqlite3, "connect", lambda *_args, **_kwargs: observed)

    connection = SQLiteStore(tmp_path / "isolated.sqlite3").connect()

    assert connection is observed
    assert observed.statements == [
        "PRAGMA foreign_keys = ON",
        "PRAGMA busy_timeout = 30000",
    ]


def test_initialize_configures_wal_once_for_later_read_connections(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "isolated.sqlite3")

    store.initialize()

    with store.connection() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
