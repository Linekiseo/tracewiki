from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from evidence_rag.sqlite_lifecycle import serialize_sqlite_connection_lifecycle
from evidence_rag.storage import SQLiteStore


def test_connection_open_waits_for_concurrent_close(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    closing = threading.Event()
    allow_close = threading.Event()
    connected: list[_LifecycleConnection] = []

    class _LifecycleConnection:
        row_factory: Any = None

        def execute(self, _statement: str) -> None:
            return None

        def close(self) -> None:
            closing.set()
            assert allow_close.wait(timeout=2)

    def fake_connect(*_args: Any, **_kwargs: Any) -> _LifecycleConnection:
        connection = _LifecycleConnection()
        connected.append(connection)
        return connection

    monkeypatch.setattr("evidence_rag.storage.sqlite3.connect", fake_connect)
    store = SQLiteStore(tmp_path / "isolated.sqlite3")
    serialize_sqlite_connection_lifecycle(store)

    def use_first() -> None:
        with store.connection():
            pass

    first = threading.Thread(target=use_first)
    first.start()
    assert closing.wait(timeout=2)

    opened = threading.Event()

    def use_second() -> None:
        with store.connection():
            opened.set()

    second = threading.Thread(target=use_second)
    second.start()
    assert not opened.wait(timeout=0.05)
    assert len(connected) == 1

    allow_close.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert opened.is_set()
    assert len(connected) == 2
