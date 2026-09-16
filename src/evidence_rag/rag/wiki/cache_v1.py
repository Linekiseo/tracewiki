"""Bounded, generation-pinned cache for immutable Wiki records."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from .contracts_v1 import (
    WikiDirectoryV1,
    WikiPageFragmentV1,
    WikiRecordKindV1,
    canonical_json_bytes_v1,
)

WikiCachedRecordV1 = WikiDirectoryV1 | WikiPageFragmentV1


class WikiSnapshotCacheKeyV1(NamedTuple):
    """A cache identity that cannot cross project, generation, or ACL boundaries."""

    project_id: str
    generation_id: str
    visibility_partition: str
    logical_path: str
    record_kind: WikiRecordKindV1


class WikiSnapshotCacheStatsV1(BaseModel):
    """Observable cache state without exposing keys or record content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_entries: int = Field(ge=1)
    max_bytes: int = Field(ge=1)
    entries: int = Field(ge=0)
    bytes_used: int = Field(ge=0)
    hits: int = Field(ge=0)
    misses: int = Field(ge=0)
    inserts: int = Field(ge=0)
    evictions: int = Field(ge=0)
    oversized_rejections: int = Field(ge=0)


class WikiSnapshotCacheV1:
    """Thread-safe LRU for frozen records from immutable Wiki generations.

    The store never caches an unverified payload and never uses an active-generation
    alias as a key.  This makes publication and rollback pointer changes irrelevant to
    entries already in the cache: readers can only retrieve the exact generation and
    ACL partition they requested.
    """

    def __init__(self, *, max_entries: int = 4_096, max_bytes: int = 64 * 1024 * 1024) -> None:
        if max_entries < 1 or max_bytes < 1:
            raise ValueError("Wiki snapshot cache bounds must be positive")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._records: OrderedDict[WikiSnapshotCacheKeyV1, tuple[WikiCachedRecordV1, int]] = (
            OrderedDict()
        )
        self._bytes_used = 0
        self._hits = 0
        self._misses = 0
        self._inserts = 0
        self._evictions = 0
        self._oversized_rejections = 0
        self._lock = RLock()

    def get(self, key: WikiSnapshotCacheKeyV1) -> WikiCachedRecordV1 | None:
        with self._lock:
            entry = self._records.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._records.move_to_end(key)
            self._hits += 1
            return entry[0]

    def put(self, key: WikiSnapshotCacheKeyV1, record: WikiCachedRecordV1) -> bool:
        encoded_size = len(canonical_json_bytes_v1(record.model_dump(mode="json")))
        with self._lock:
            previous = self._records.pop(key, None)
            if previous is not None:
                self._bytes_used -= previous[1]
            if encoded_size > self._max_bytes:
                self._oversized_rejections += 1
                return False
            self._records[key] = (record, encoded_size)
            self._bytes_used += encoded_size
            self._inserts += 1
            while len(self._records) > self._max_entries or self._bytes_used > self._max_bytes:
                _, (_, evicted_size) = self._records.popitem(last=False)
                self._bytes_used -= evicted_size
                self._evictions += 1
            return key in self._records

    def stats(self) -> WikiSnapshotCacheStatsV1:
        with self._lock:
            return WikiSnapshotCacheStatsV1(
                max_entries=self._max_entries,
                max_bytes=self._max_bytes,
                entries=len(self._records),
                bytes_used=self._bytes_used,
                hits=self._hits,
                misses=self._misses,
                inserts=self._inserts,
                evictions=self._evictions,
                oversized_rejections=self._oversized_rejections,
            )
