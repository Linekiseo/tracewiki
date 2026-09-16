from __future__ import annotations

from threading import Lock
from typing import Any


class _SerializedConnection:
    """Delegate a connection while serializing its close with future opens."""

    __slots__ = ("_connection", "_lifecycle_lock")

    def __init__(self, connection: Any, lifecycle_lock: Lock) -> None:
        self._connection = connection
        self._lifecycle_lock = lifecycle_lock

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def close(self) -> None:
        with self._lifecycle_lock:
            self._connection.close()


def serialize_sqlite_connection_lifecycle(store: Any) -> None:
    """Prevent concurrent SQLite open/close operations for one runtime store.

    Python 3.13's SQLite build can deadlock in its reusable-file-descriptor
    mutex when a WAL connection opens while another request closes a
    connection.  Codex publication produces many short transactions while the
    UI polls status and graph endpoints.  Serializing only connection open and
    close avoids that deadlock without serializing queries or transactions.
    """

    if getattr(store, "_serialized_connection_lifecycle", False):
        return

    lifecycle_lock = Lock()
    original_connect = store.connect

    def connect() -> _SerializedConnection:
        with lifecycle_lock:
            connection = original_connect()
        return _SerializedConnection(connection, lifecycle_lock)

    store.connect = connect
    store._serialized_connection_lifecycle = True
