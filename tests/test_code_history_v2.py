from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evidence_rag.parser import CodeParser
from evidence_rag.rag.sources.code.contracts import CodeRelationType
from evidence_rag.rag.sources.code.history_v2 import (
    HISTORY_SCHEMA_VERSION,
    GitRenameHint,
    HistoricalFileStatus,
    HistoricalMaterializer,
    HistoryMaterializationPolicy,
    HistoryMaterializationRequest,
    HistoryMaterializationStatus,
    HistoryNamespace,
    HistoryNamespaceKind,
    HistoryPinReason,
    HistoryPublicationError,
    HistoryScopeError,
    InMemoryHistoricalPublicationStore,
    LineageRelationType,
    LineageStatus,
    SQLiteHistoricalPublicationStore,
    SymbolLineageRequest,
    SymbolLineageResolver,
)
from evidence_rag.rag.sources.code.unit_builder import BUILDER_VERSION, CodeUnitBuilder


@dataclass(frozen=True, slots=True)
class GitFixture:
    root: Path
    first: str
    second: str
    third: str


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


@pytest.fixture
def history_repo(tmp_path: Path) -> GitFixture:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "History Test")
    _git(root, "config", "user.email", "history@example.invalid")

    _write(
        root / "src/calc.py",
        """\
def total(value: int) -> int:
    return value + 1


def helper(value: int) -> int:
    return value * 2
""",
    )
    _write(root / "assets/blob.py", b"\x00\x01not-source")
    _write(root / "notes.txt", "not a supported Code language\n")
    _write(root / "src/large.py", "def large():\n    return '" + ("x" * 512) + "'\n")
    _git(root, "add", "--", "src/calc.py", "assets/blob.py", "notes.txt", "src/large.py")
    _git(root, "commit", "-q", "-m", "first")
    first = _git(root, "rev-parse", "HEAD")
    _git(root, "tag", "history-v1", first)

    _write(
        root / "src/calc.py",
        """\
def compute_total(value: int) -> int:
    return value + 1


def helper(value: int) -> int:
    return value * 2
""",
    )
    _git(root, "add", "--", "src/calc.py")
    _git(root, "commit", "-q", "-m", "rename symbol")
    second = _git(root, "rev-parse", "HEAD")
    _git(root, "tag", "history-v2", second)

    (root / "lib").mkdir()
    _git(root, "mv", "src/calc.py", "lib/calc.py")
    _git(root, "commit", "-q", "-m", "move file")
    third = _git(root, "rev-parse", "HEAD")
    _git(root, "tag", "history-v3", third)

    # The worktree is deliberately dirty after all immutable commits exist.
    _write(
        root / "lib/calc.py",
        """\
def compute_total(value: int) -> int:
    return 999


def helper(value: int) -> int:
    return value * 2
""",
    )
    return GitFixture(root=root, first=first, second=second, third=third)


def _request(
    fixture: GitFixture,
    ref: str,
    generation: str,
    *,
    commit_sha: str | None = None,
    paths: tuple[str, ...] = ("src/calc.py",),
    policy: HistoryMaterializationPolicy | None = None,
    pin_reasons: tuple[HistoryPinReason, ...] = (),
) -> HistoryMaterializationRequest:
    return HistoryMaterializationRequest(
        project_id="project-history",
        repository_id="repo-history",
        repository_root=fixture.root,
        ref=ref,
        commit_sha=commit_sha,
        generation_id=generation,
        acl_ref="acl:engineering",
        paths=paths,
        policy=policy or HistoryMaterializationPolicy(),
        pin_reasons=pin_reasons,
    )


def _exact_query(publication) -> dict[str, str]:
    return {
        "project_id": publication.project_id,
        "repository_id": publication.repository_id,
        "commit_sha": publication.commit_sha,
        "generation_id": publication.generation_id,
        "acl_ref": publication.acl_ref,
        "profile": publication.profile,
        "schema_version": publication.schema_version,
        "builder_version": publication.builder_version,
        "selection_identity": publication.selection_identity,
    }


def _symbols(publication):
    return tuple(
        item
        for item in publication.units
        if item.unit_type == "symbol.ast_block" and item.symbol_name
    )


def test_exact_ref_materialization_reads_commit_not_dirty_worktree(
    history_repo: GitFixture,
) -> None:
    store = InMemoryHistoricalPublicationStore()
    materializer = HistoricalMaterializer(store)
    old = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-history-v1",
            commit_sha=history_repo.first,
        )
    )
    current_clean = materializer.materialize(
        _request(
            history_repo,
            "history-v3",
            "generation-history-v3",
            commit_sha=history_repo.third,
            paths=("lib/calc.py",),
        )
    )

    assert old.commit_sha == history_repo.first
    assert old.trace.resolved_commit_sha == history_repo.first
    assert old.namespace.kind is HistoryNamespaceKind.HISTORY
    assert old.namespace.name == f"code/history/repo-history/{history_repo.first}"
    assert old.status is HistoryMaterializationStatus.COMPLETE
    assert old.files[0].content is not None
    assert "def total" in old.files[0].content
    assert "compute_total" not in old.files[0].content
    assert "return 999" not in old.files[0].content
    assert current_clean.files[0].content is not None
    assert "compute_total" in current_clean.files[0].content
    assert "return 999" not in current_clean.files[0].content
    assert all(item.commit_sha == history_repo.first for item in old.units)
    assert all(f"@{history_repo.first}/" in item.locator for item in old.units)
    assert all(item.generation_id == "generation-history-v1" for item in old.units)
    assert all(item.acl_ref == "acl:engineering" for item in old.units)
    assert (
        old.exact_units(
            project_id="project-history",
            repository_id="repo-history",
            commit_sha=history_repo.first,
            generation_id="generation-history-v1",
            acl_ref="acl:engineering",
        )
        == old.units
    )

    clean_current = HistoryNamespace.current(
        project_id="project-history",
        repository_id="repo-history",
        stable_version=history_repo.third,
        generation_id="active-clean",
        acl_ref="acl:engineering",
        commit_sha=history_repo.third,
    )
    dirty_current = HistoryNamespace.current(
        project_id="project-history",
        repository_id="repo-history",
        stable_version=f"{history_repo.third}+dirty.manifest123",
        generation_id="active-dirty",
        acl_ref="acl:engineering",
        commit_sha=history_repo.third,
        dirty=True,
    )
    assert clean_current.name != dirty_current.name != old.namespace.name
    assert clean_current.dirty is False
    assert dirty_current.dirty is True
    with pytest.raises(FrozenInstanceError):
        old.commit_sha = history_repo.third  # type: ignore[misc]


def test_current_namespace_requires_exact_clean_or_dirty_base_sha(
    history_repo: GitFixture,
) -> None:
    clean = HistoryNamespace.current(
        project_id="project-history",
        repository_id="repo-history",
        stable_version=history_repo.third,
        generation_id="generation-current-clean",
        acl_ref="acl:engineering",
        commit_sha=history_repo.third,
    )
    dirty = HistoryNamespace.current(
        project_id="project-history",
        repository_id="repo-history",
        stable_version=f"{history_repo.third}+dirty.manifest_123",
        generation_id="generation-current-dirty",
        acl_ref="acl:engineering",
        commit_sha=history_repo.third,
        dirty=True,
    )
    assert clean.stable_version == clean.commit_sha
    assert dirty.stable_version.startswith(f"{dirty.commit_sha}+dirty.")

    with pytest.raises(ValueError, match="stable_version must equal commit_sha"):
        HistoryNamespace.current(
            project_id="project-history",
            repository_id="repo-history",
            stable_version=history_repo.first,
            generation_id="generation-current-wrong-clean",
            acl_ref="acl:engineering",
            commit_sha=history_repo.third,
        )
    with pytest.raises(ValueError, match="base SHA must equal commit_sha"):
        HistoryNamespace.current(
            project_id="project-history",
            repository_id="repo-history",
            stable_version=f"{history_repo.first}+dirty.manifest123",
            generation_id="generation-current-wrong-base",
            acl_ref="acl:engineering",
            commit_sha=history_repo.third,
            dirty=True,
        )
    for malformed in (
        f"{history_repo.third}+dirty.",
        f"{history_repo.third}+dirty..manifest",
        f"{history_repo.third}+dirty.Manifest",
        f"{history_repo.third}+dirty.manifest/",
        f"{history_repo.third}+dirty.-manifest",
    ):
        with pytest.raises(ValueError, match="manifest_identity"):
            HistoryNamespace.current(
                project_id="project-history",
                repository_id="repo-history",
                stable_version=malformed,
                generation_id="generation-current-malformed",
                acl_ref="acl:engineering",
                commit_sha=history_repo.third,
                dirty=True,
            )


def test_ref_path_and_expected_sha_validation_fail_closed(history_repo: GitFixture) -> None:
    materializer = HistoricalMaterializer(InMemoryHistoricalPublicationStore())
    assert (
        materializer.resolve_ref(
            _request(history_repo, "history-v2", "generation-v2", commit_sha=history_repo.second)
        )
        == history_repo.second
    )

    with pytest.raises(ValueError, match="explicit clean ref"):
        _request(history_repo, "HEAD", "generation-head")
    with pytest.raises(ValueError, match="safe repository-relative"):
        _request(history_repo, "history-v1", "generation-v1", paths=("../secret.py",))
    with pytest.raises(HistoryScopeError, match="different commit SHA"):
        materializer.materialize(
            _request(
                history_repo,
                "history-v1",
                "generation-wrong",
                commit_sha=history_repo.second,
            )
        )


class CountingParser(CodeParser):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def parse(self, relative_path: str, content: str, blob_hash: str | None = None):
        self.calls += 1
        return super().parse(relative_path, content, blob_hash)


class CountingBuilder(CodeUnitBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def build(self, source, *, context=None):
        self.calls += 1
        return super().build(source, context=context)


def test_materialization_cache_reuses_parser_and_builder_and_rebinds_ref(
    history_repo: GitFixture,
) -> None:
    parser = CountingParser()
    builder = CountingBuilder()
    store = InMemoryHistoricalPublicationStore()
    materializer = HistoricalMaterializer(store, parser=parser, builder=builder)
    first = materializer.materialize(_request(history_repo, "history-v1", "generation-cache"))
    second = materializer.materialize(
        _request(history_repo, history_repo.first, "generation-cache")
    )

    assert parser.calls == 1
    assert builder.calls == 1
    assert first.content_key == second.content_key
    assert first.publication_hash == second.publication_hash
    assert tuple(item.unit_id for item in first.units) == tuple(
        item.unit_id for item in second.units
    )
    assert second.requested_ref == history_repo.first
    assert all(item.requested_ref == history_repo.first for item in second.units)
    assert second.trace.cache_hit is True
    assert second.trace.status is HistoryMaterializationStatus.CACHE_HIT
    assert store.publication_count() == 1


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_cache_key_is_scoped_by_paths_and_materialization_policy(
    history_repo: GitFixture,
    tmp_path: Path,
    store_kind: str,
) -> None:
    store = (
        InMemoryHistoricalPublicationStore()
        if store_kind == "memory"
        else SQLiteHistoricalPublicationStore(tmp_path / "selection.sqlite3")
    )
    materializer = HistoricalMaterializer(store)

    first_path = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-selection-path",
            paths=("src/calc.py",),
        )
    )
    second_path = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-selection-path",
            paths=("src/large.py",),
        )
    )
    assert first_path.content_key != second_path.content_key
    assert {item.path for item in first_path.files} == {"src/calc.py"}
    assert {item.path for item in second_path.files} == {"src/large.py"}

    missing_first = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-selection-missing",
            paths=("src/missing.py",),
        )
    )
    normal_after_missing = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-selection-missing",
            paths=("src/calc.py",),
        )
    )
    assert missing_first.status is HistoryMaterializationStatus.EMPTY
    assert normal_after_missing.status is HistoryMaterializationStatus.COMPLETE
    assert missing_first.content_key != normal_after_missing.content_key
    assert normal_after_missing.files[0].content is not None
    assert "def total" in normal_after_missing.files[0].content

    oversize_first = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-selection-policy",
            paths=("src/large.py",),
            policy=HistoryMaterializationPolicy(max_file_bytes=100),
        )
    )
    normal_after_oversize = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-selection-policy",
            paths=("src/large.py",),
            policy=HistoryMaterializationPolicy(max_file_bytes=1_000),
        )
    )
    assert oversize_first.files[0].status is HistoricalFileStatus.SKIPPED_OVERSIZE
    assert normal_after_oversize.files[0].status is HistoricalFileStatus.MATERIALIZED
    assert oversize_first.content_key != normal_after_oversize.content_key
    assert oversize_first.selection_identity != normal_after_oversize.selection_identity

    ordered = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-selection-order",
            paths=("src/large.py", "src/calc.py"),
        )
    )
    reversed_order = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-selection-order",
            paths=("src/calc.py", "src/large.py"),
        )
    )
    assert ordered.content_key == reversed_order.content_key
    assert reversed_order.trace.cache_hit is True
    if isinstance(store, SQLiteHistoricalPublicationStore):
        store.close()


def test_binary_oversize_and_unsupported_files_are_honest_skips(
    history_repo: GitFixture,
) -> None:
    policy = HistoryMaterializationPolicy(max_file_bytes=100)
    publication = HistoricalMaterializer(InMemoryHistoricalPublicationStore()).materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-skips",
            paths=("assets/blob.py", "notes.txt", "src/large.py"),
            policy=policy,
        )
    )

    status_by_path = {item.path: item.status for item in publication.files}
    assert status_by_path == {
        "assets/blob.py": HistoricalFileStatus.SKIPPED_BINARY,
        "notes.txt": HistoricalFileStatus.SKIPPED_UNSUPPORTED,
        "src/large.py": HistoricalFileStatus.SKIPPED_OVERSIZE,
    }
    assert publication.status is HistoryMaterializationStatus.EMPTY
    assert publication.units == ()
    assert all(item.content is None for item in publication.files)
    assert publication.trace.files_seen == 3
    assert publication.trace.files_skipped == 3


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_atomic_publication_rollback_leaves_no_partial_artifacts(
    history_repo: GitFixture,
    tmp_path: Path,
    store_kind: str,
) -> None:
    baseline = HistoricalMaterializer(InMemoryHistoricalPublicationStore()).materialize(
        _request(history_repo, "history-v1", "generation-atomic")
    )

    def fail_after_commit_fact(stage: str) -> None:
        if stage == "after_commit_fact":
            raise RuntimeError("injected publication failure")

    if store_kind == "memory":
        store = InMemoryHistoricalPublicationStore(failpoint=fail_after_commit_fact)
        expected = RuntimeError
    else:
        store = SQLiteHistoricalPublicationStore(
            tmp_path / "atomic.sqlite3",
            failpoint=fail_after_commit_fact,
        )
        expected = HistoryPublicationError
    with pytest.raises(expected, match="failure|failed"):
        store.publish(baseline)
    assert store.publication_count() == 0
    assert store.commit_fact_count() == 0
    assert not store.has_commit_fact(baseline.repository_id, baseline.commit_sha)
    if isinstance(store, SQLiteHistoricalPublicationStore):
        store.close()


def test_sqlite_store_round_trip_exact_version_and_tmp_only(
    history_repo: GitFixture,
    tmp_path: Path,
) -> None:
    database = tmp_path / "history.sqlite3"
    store = SQLiteHistoricalPublicationStore(database)
    publication = HistoricalMaterializer(store).materialize(
        _request(history_repo, "history-v2", "generation-sqlite")
    )
    store.close()

    reopened = SQLiteHistoricalPublicationStore(database)
    loaded = reopened.get_exact(**_exact_query(publication))
    assert loaded == publication
    assert reopened.publication_count() == 1
    assert reopened.commit_fact_count() == 1
    assert reopened.has_commit_fact("repo-history", history_repo.second)
    reopened.close()
    with pytest.raises(ValueError, match="temporary paths"):
        SQLiteHistoricalPublicationStore(Path.cwd() / "not-allowed.sqlite3")


class FakeClock:
    def __init__(self) -> None:
        self.value = 1.0

    def __call__(self) -> float:
        return self.value

    def iso(self) -> str:
        return datetime.fromtimestamp(self.value, tz=UTC).isoformat()


def test_ttl_lru_evicts_only_derived_history_and_retains_commit_facts(
    history_repo: GitFixture,
) -> None:
    clock = FakeClock()
    store = InMemoryHistoricalPublicationStore(clock=clock)
    materializer = HistoricalMaterializer(store, clock=clock.iso)
    publications = []
    for ref, generation in (
        ("history-v1", "generation-evict-1"),
        ("history-v2", "generation-evict-2"),
        ("history-v3", "generation-evict-3"),
    ):
        path = ("lib/calc.py",) if ref == "history-v3" else ("src/calc.py",)
        publications.append(
            materializer.materialize(_request(history_repo, ref, generation, paths=path))
        )
        clock.value += 10

    result = store.evict(
        HistoryMaterializationPolicy(
            ttl_seconds=None,
            max_historical_publications=1,
            retain_hot_publications=False,
        )
    )
    assert result.evicted_content_keys == tuple(
        sorted(item.content_key for item in publications[:2])
    )
    assert result.retained_content_keys == (publications[2].content_key,)
    assert result.publication_count == 1
    assert result.commit_fact_count == 3
    assert all(
        store.has_commit_fact("repo-history", sha)
        for sha in (history_repo.first, history_repo.second, history_repo.third)
    )

    clock.value = 100
    expired = store.evict(
        HistoryMaterializationPolicy(
            ttl_seconds=5,
            max_historical_publications=10,
            retain_hot_publications=False,
        ),
        now=clock.value,
    )
    assert expired.publication_count == 0
    assert expired.commit_fact_count == 3


def test_explicit_referenced_and_hot_pins_can_be_retained(
    history_repo: GitFixture,
) -> None:
    clock = FakeClock()
    store = InMemoryHistoricalPublicationStore(clock=clock)
    materializer = HistoricalMaterializer(store, clock=clock.iso)
    pinned = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-pinned",
            pin_reasons=(HistoryPinReason.EXPLICIT, HistoryPinReason.REFERENCED),
        )
    )
    clock.value += 10
    other = materializer.materialize(_request(history_repo, "history-v2", "generation-unpinned"))
    store.get_exact(**_exact_query(other))
    store.get_exact(**_exact_query(other))

    result = store.evict(
        HistoryMaterializationPolicy(
            ttl_seconds=0,
            max_historical_publications=1,
            retain_hot_publications=True,
        ),
        now=100,
    )
    assert set(result.retained_content_keys) == {pinned.content_key, other.content_key}
    assert result.commit_fact_count == 2


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_cache_hit_pin_upgrade_is_monotonic_atomic_and_retained(
    history_repo: GitFixture,
    tmp_path: Path,
    store_kind: str,
) -> None:
    clock = FakeClock()
    store = (
        InMemoryHistoricalPublicationStore(clock=clock)
        if store_kind == "memory"
        else SQLiteHistoricalPublicationStore(tmp_path / "pin-union.sqlite3", clock=clock)
    )
    materializer = HistoricalMaterializer(store, clock=clock.iso)
    base = materializer.materialize(_request(history_repo, "history-v1", "generation-pin-union"))
    assert base.pin_reasons == ()

    referenced = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-pin-union",
            pin_reasons=(HistoryPinReason.REFERENCED,),
        )
    )
    assert referenced.trace.cache_hit is True
    assert referenced.pin_reasons == (HistoryPinReason.REFERENCED,)

    explicit = materializer.materialize(
        _request(
            history_repo,
            "history-v1",
            "generation-pin-union",
            pin_reasons=(HistoryPinReason.EXPLICIT,),
        )
    )
    assert set(explicit.pin_reasons) == {
        HistoryPinReason.EXPLICIT,
        HistoryPinReason.REFERENCED,
    }
    repeated = store.publish(replace(explicit, pin_reasons=(HistoryPinReason.REFERENCED,)))
    assert repeated.pin_reasons == explicit.pin_reasons
    assert store.publication_count() == 1
    assert store.commit_fact_count() == 1

    evicted = store.evict(
        HistoryMaterializationPolicy(
            ttl_seconds=0,
            max_historical_publications=1,
            retain_hot_publications=False,
        ),
        now=100,
    )
    assert evicted.evicted_content_keys == ()
    assert evicted.retained_content_keys == (base.content_key,)
    assert store.has_commit_fact("repo-history", history_repo.first)
    if isinstance(store, SQLiteHistoricalPublicationStore):
        store.close()


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_pin_upgrade_rollback_does_not_partially_persist(
    history_repo: GitFixture,
    tmp_path: Path,
    store_kind: str,
) -> None:
    store = (
        InMemoryHistoricalPublicationStore()
        if store_kind == "memory"
        else SQLiteHistoricalPublicationStore(tmp_path / "pin-rollback.sqlite3")
    )
    publication = HistoricalMaterializer(store).materialize(
        _request(history_repo, "history-v1", "generation-pin-rollback")
    )

    def fail_pin_union(stage: str) -> None:
        if stage == "after_pin_union":
            raise RuntimeError("injected pin union failure")

    store.failpoint = fail_pin_union
    expected = RuntimeError if store_kind == "memory" else HistoryPublicationError
    with pytest.raises(expected, match="failure|failed"):
        store.publish(
            replace(
                publication,
                pin_reasons=(HistoryPinReason.REFERENCED,),
            )
        )
    store.failpoint = None
    loaded = store.get_exact(**_exact_query(publication))
    assert loaded is not None
    assert loaded.pin_reasons == ()
    assert store.publication_count() == 1
    assert store.commit_fact_count() == 1
    assert store.has_commit_fact("repo-history", history_repo.first)
    if isinstance(store, SQLiteHistoricalPublicationStore):
        store.close()


def test_exact_query_wrong_version_acl_repository_and_project_fail_closed(
    history_repo: GitFixture,
) -> None:
    store = InMemoryHistoricalPublicationStore()
    publication = HistoricalMaterializer(store).materialize(
        _request(history_repo, "history-v1", "generation-scope")
    )
    scope = _exact_query(publication)
    assert store.get_exact(**scope) == publication

    for field, value in (
        ("project_id", "project-other"),
        ("repository_id", "repo-other"),
        ("commit_sha", history_repo.second),
        ("generation_id", "generation-other"),
        ("acl_ref", "acl:other"),
        ("schema_version", "schema-other"),
        ("builder_version", "builder-other"),
        ("selection_identity", "sha256:" + ("0" * 64)),
    ):
        wrong = {**scope, field: value}
        assert store.get_exact(**wrong) is None
    with pytest.raises(HistoryScopeError):
        publication.exact_units(
            project_id="project-history",
            repository_id="repo-history",
            commit_sha=history_repo.second,
            generation_id="generation-scope",
            acl_ref="acl:engineering",
        )


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_content_key_is_not_an_acl_capability_and_denied_reads_do_not_make_hot(
    history_repo: GitFixture,
    tmp_path: Path,
    store_kind: str,
) -> None:
    clock = FakeClock()
    store = (
        InMemoryHistoricalPublicationStore(clock=clock)
        if store_kind == "memory"
        else SQLiteHistoricalPublicationStore(tmp_path / "acl-hot.sqlite3", clock=clock)
    )
    assert not hasattr(store, "get_publication")
    assert not hasattr(store, "get")
    materializer = HistoricalMaterializer(store, clock=clock.iso)
    first = materializer.materialize(_request(history_repo, "history-v1", "generation-acl-hot-1"))
    clock.value += 10
    second = materializer.materialize(_request(history_repo, "history-v2", "generation-acl-hot-2"))

    wrong_acl = {**_exact_query(first), "acl_ref": "acl:unauthorized"}
    assert store.get_exact(**wrong_acl) is None
    with pytest.raises(TypeError):
        store.get_exact(  # type: ignore[call-arg]
            content_key=first.content_key,
            **wrong_acl,
        )

    result = store.evict(
        HistoryMaterializationPolicy(
            ttl_seconds=None,
            max_historical_publications=1,
            hot_access_threshold=2,
            retain_hot_publications=True,
        )
    )
    assert result.evicted_content_keys == (first.content_key,)
    assert result.retained_content_keys == (second.content_key,)
    assert result.commit_fact_count == 2
    if isinstance(store, SQLiteHistoricalPublicationStore):
        store.close()


def _lineage_request(
    source,
    target,
    *,
    candidate_threshold: float = 0.3,
    confirmation_threshold: float = 0.6,
) -> SymbolLineageRequest:
    return SymbolLineageRequest(
        project_id=source.project_id,
        repository_id=source.repository_id,
        source_commit_sha=source.commit_sha,
        target_commit_sha=target.commit_sha,
        source_generation_id=source.generation_id,
        target_generation_id=target.generation_id,
        acl_ref=source.acl_ref,
        candidate_threshold=candidate_threshold,
        confirmation_threshold=confirmation_threshold,
    )


def test_symbol_lineage_exact_rename_move_is_deterministic(
    history_repo: GitFixture,
) -> None:
    store = InMemoryHistoricalPublicationStore()
    materializer = HistoricalMaterializer(store)
    first = materializer.materialize(_request(history_repo, "history-v1", "generation-lineage-1"))
    second = materializer.materialize(_request(history_repo, "history-v2", "generation-lineage-2"))
    third_request = _request(
        history_repo,
        "history-v3",
        "generation-lineage-3",
        paths=("lib/calc.py",),
    )
    third = materializer.materialize(third_request)
    resolver = SymbolLineageResolver()

    rename = resolver.resolve(
        _lineage_request(first, second),
        _symbols(first),
        _symbols(second),
    )
    repeated = resolver.resolve(
        _lineage_request(first, second),
        tuple(reversed(_symbols(first))),
        tuple(reversed(_symbols(second))),
    )
    assert rename == repeated
    relations = {item.relation_type for item in rename.lineages}
    assert CodeRelationType.SAME_SYMBOL_AS in relations
    assert CodeRelationType.RENAMED_TO in relations
    assert all(item.status is LineageStatus.CONFIRMED for item in rename.lineages)
    assert all(item.review_required is False for item in rename.lineages)
    assert all(item.explanation_trace for item in rename.lineages)

    hints = materializer.git_rename_hints(
        request=third_request,
        source_commit_sha=history_repo.second,
        target_commit_sha=history_repo.third,
        source_generation_id=second.generation_id,
        target_generation_id=third.generation_id,
    )
    assert hints == (
        GitRenameHint(
            project_id="project-history",
            repository_id="repo-history",
            source_commit_sha=history_repo.second,
            target_commit_sha=history_repo.third,
            source_generation_id=second.generation_id,
            target_generation_id=third.generation_id,
            acl_ref="acl:engineering",
            old_path="src/calc.py",
            new_path="lib/calc.py",
            similarity=1.0,
            evidence_locator=(
                f"git-diff://repo-history/{history_repo.second}..{history_repo.third}"
            ),
        ),
    )
    moved = resolver.resolve(
        _lineage_request(second, third),
        _symbols(second),
        _symbols(third),
        rename_hints=hints,
    )
    assert CodeRelationType.MOVED_TO in {item.relation_type for item in moved.lineages}
    assert all(
        item.source_commit_sha == history_repo.second
        and item.target_commit_sha == history_repo.third
        for item in moved.lineages
    )


def test_split_merge_remain_candidates_and_cross_scope_fails_closed(
    history_repo: GitFixture,
) -> None:
    materializer = HistoricalMaterializer(InMemoryHistoricalPublicationStore())
    first = materializer.materialize(_request(history_repo, "history-v1", "generation-candidate-1"))
    second = materializer.materialize(
        _request(history_repo, "history-v2", "generation-candidate-2")
    )
    source_total = next(item for item in _symbols(first) if item.symbol_name == "total")
    target_total = next(item for item in _symbols(second) if item.symbol_name == "compute_total")
    duplicate = replace(
        target_total,
        unit_id=target_total.unit_id + "-variant",
        symbol_name="compute_total_variant",
        qualified_name=target_total.qualified_name + "_variant",
        signature=(
            target_total.signature.replace("compute_total", "compute_total_variant")
            if target_total.signature
            else None
        ),
        body=target_total.body.replace("compute_total", "compute_total_variant"),
        locator=target_total.locator + "-variant",
    )
    resolver = SymbolLineageResolver()
    result = resolver.resolve(
        _lineage_request(first, second),
        (source_total,),
        (target_total, duplicate),
    )
    split_candidates = [
        item for item in result.candidates if item.relation_type is LineageRelationType.SPLIT_INTO
    ]
    assert split_candidates
    assert all(item.review_required for item in split_candidates)
    assert all(item.status is LineageStatus.REVIEW_REQUIRED for item in split_candidates)
    assert all(
        item.relation_type
        in {
            CodeRelationType.SAME_SYMBOL_AS,
            CodeRelationType.RENAMED_TO,
            CodeRelationType.MOVED_TO,
        }
        for item in result.lineages
    )

    wrong_repository = replace(
        source_total,
        repository_id="repo-other",
        provenance=replace(source_total.provenance, repository_id="repo-other"),
    )
    with pytest.raises(HistoryScopeError, match="scope/version/ACL"):
        resolver.resolve(
            _lineage_request(first, second),
            (wrong_repository,),
            (target_total,),
        )
    wrong_acl_hint = GitRenameHint(
        project_id="project-history",
        repository_id="repo-history",
        source_commit_sha=first.commit_sha,
        target_commit_sha=second.commit_sha,
        source_generation_id=first.generation_id,
        target_generation_id=second.generation_id,
        acl_ref="acl:other",
        old_path=source_total.file_path,
        new_path=target_total.file_path,
        similarity=1.0,
        evidence_locator="git-diff://wrong-acl",
    )
    with pytest.raises(HistoryScopeError, match="rename hint"):
        resolver.resolve(
            _lineage_request(first, second),
            (source_total,),
            (target_total,),
            rename_hints=(wrong_acl_hint,),
        )


def test_content_address_includes_exact_generation_schema_and_builder(
    history_repo: GitFixture,
) -> None:
    store = InMemoryHistoricalPublicationStore()
    materializer = HistoricalMaterializer(store)
    first = materializer.materialize(_request(history_repo, "history-v1", "generation-key-a"))
    second = materializer.materialize(_request(history_repo, "history-v1", "generation-key-b"))
    assert first.content_key != second.content_key
    assert first.schema_version == second.schema_version == HISTORY_SCHEMA_VERSION
    assert first.builder_version == second.builder_version == BUILDER_VERSION
    assert store.publication_count() == 2
