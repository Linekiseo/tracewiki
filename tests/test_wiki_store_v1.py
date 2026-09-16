from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from evidence_rag.rag.wiki.contracts_v1 import (
    SourceGenerationV1,
    WikiFactAuthorityV1,
    WikiFactStatusV1,
    WikiGenerationStatusV1,
    WikiPageFragmentV1,
    WikiPageTypeV1,
    WikiRecordIndexV1,
    WikiRecordKindV1,
    WikiScopeV1,
    WikiSourceDomainV1,
    build_source_generation_v1,
    build_wiki_directory_v1,
    build_wiki_fact_v1,
    build_wiki_generation_manifest_v1,
    build_wiki_page_fragment_v1,
    build_wiki_source_ref_v1,
    canonical_sha256_v1,
    visibility_partition_v1,
)
from evidence_rag.rag.wiki.paths_v1 import (
    WIKI_INTENT_DIRECTORIES,
    wiki_record_physical_key_v1,
)
from evidence_rag.rag.wiki.store_v1 import (
    WikiPublicationError,
    WikiStorePathError,
    WikiStoreV1,
    verify_wiki_snapshot_v1,
)


def _generations(version: int) -> tuple[SourceGenerationV1, ...]:
    return tuple(
        build_source_generation_v1(
            source=source,
            generation_id=f"source-{source.value}-generation-{version}",
            watermark=f"source-{source.value}-watermark-{version}",
        )
        for source in WikiSourceDomainV1
    )


def _scope(version: int, *, acl_refs: tuple[str, ...] = ("team-a",)) -> WikiScopeV1:
    return WikiScopeV1(
        project_id="project-wiki-store-v1",
        visibility_partition=visibility_partition_v1(acl_refs),
        acl_refs=acl_refs,
        source_generations=_generations(version),
        as_of=f"2026-08-0{version}T00:00:00Z",
    )


def _page(path: str, page_type: WikiPageTypeV1, scope: WikiScopeV1, version: int):
    source_ref = build_wiki_source_ref_v1(
        source_ref_id=f"source-ref-code-{page_type.value}-{version}",
        source=WikiSourceDomainV1.CODE,
        entity_type="CodeSymbol",
        entity_id=f"code-{page_type.value}-{version}",
        locator=f"code://project-wiki-store-v1/symbol/{page_type.value}-{version}",
        generation_id=f"source-code-generation-{version}",
        watermark=f"source-code-watermark-{version}",
        acl_refs=scope.acl_refs,
        observed_at=f"2026-08-0{version}T00:00:00Z",
        raw_content_sha256=canonical_sha256_v1({"page": path, "version": version}),
    )
    fact = build_wiki_fact_v1(
        fact_id=f"fact-{page_type.value}-{version}",
        subject_path=path,
        predicate="describes.version",
        object_text=f"Reviewed Wiki Store generation {version}.",
        source_ref_ids=(source_ref.source_ref_id,),
        authority=WikiFactAuthorityV1.RAW_OBSERVED,
        status=WikiFactStatusV1.ACTIVE,
        confidence=1.0,
        valid_from=f"2026-08-0{version}T00:00:00Z",
        valid_to=None,
    )
    return build_wiki_page_fragment_v1(
        fragment_id=f"fragment-{page_type.value}-{version}",
        logical_path=path,
        page_type=page_type,
        title=f"{page_type.value.title()} generation {version}",
        summary=f"Reviewed {page_type.value} page for atomic generation {version}.",
        aliases=(),
        tags=("store",),
        scope=scope,
        source_refs=(source_ref,),
        facts=(fact,),
        links=(),
        evidence_roles=("implementation",),
    )


def _snapshot(version: int):
    scope = _scope(version)
    generation_id = f"wiki-generation-{version}"
    capability_path = "/capabilities/evidence-navigation-a4c198e7"
    component_path = "/components/wiki-store-c881f713"
    pages = (
        _page(capability_path, WikiPageTypeV1.CAPABILITY, scope, version),
        _page(component_path, WikiPageTypeV1.COMPONENT, scope, version),
    )
    directories = tuple(
        build_wiki_directory_v1(
            directory_id=f"directory-{intent}-{version}",
            logical_path=f"/{intent}",
            scope=scope,
            child_paths=(),
            page_paths=tuple(
                path for path in (capability_path, component_path) if path.startswith(f"/{intent}/")
            ),
        )
        for intent in WIKI_INTENT_DIRECTORIES
    )
    records = tuple(
        sorted(
            (*directories, *pages),
            key=lambda item: (
                item.scope.visibility_partition,
                item.logical_path,
                "directory" if item in directories else "page_fragment",
            ),
        )
    )
    indexes = []
    for record in records:
        kind = (
            WikiRecordKindV1.DIRECTORY if record in directories else WikiRecordKindV1.PAGE_FRAGMENT
        )
        indexes.append(
            WikiRecordIndexV1(
                logical_path=record.logical_path,
                record_kind=kind,
                visibility_partition=scope.visibility_partition,
                record_sha256=record.content_sha256,
                physical_key=wiki_record_physical_key_v1(
                    project_id=scope.project_id,
                    generation_id=generation_id,
                    visibility_partition=scope.visibility_partition,
                    logical_path=record.logical_path,
                    record_kind=kind.value,
                ),
            )
        )
    manifest = build_wiki_generation_manifest_v1(
        snapshot_id=f"snapshot-wiki-store-{version}",
        project_id=scope.project_id,
        generation_id=generation_id,
        status=WikiGenerationStatusV1.VERIFIED,
        source_generations=scope.source_generations,
        visibility_partitions=(scope.visibility_partition,),
        records=tuple(indexes),
        root_paths=tuple(f"/{intent}" for intent in WIKI_INTENT_DIRECTORIES),
        compiler_authority_sha256=canonical_sha256_v1("wiki-store-test-compiler-v1"),
        created_at=f"2026-08-0{version}T00:00:00Z",
    )
    return manifest, records


def test_store_rejects_formal_sidecar_escape_and_symlink_paths(tmp_path: Path) -> None:
    with pytest.raises(WikiStorePathError, match="formal service database"):
        WikiStoreV1(tmp_path / "evidence-rag.sqlite3", isolated_root=tmp_path)
    with pytest.raises(WikiStorePathError, match="inside"):
        WikiStoreV1(tmp_path.parent / "outside.sqlite3", isolated_root=tmp_path)
    sidecar_path = tmp_path / "wiki.sqlite3"
    Path(str(sidecar_path) + "-wal").write_text("forbidden")
    with pytest.raises(WikiStorePathError, match="sidecars"):
        WikiStoreV1(sidecar_path, isolated_root=tmp_path)
    Path(str(sidecar_path) + "-wal").unlink()
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(WikiStorePathError, match="symlink"):
        WikiStoreV1(alias / "wiki.sqlite3", isolated_root=tmp_path)


def test_stage_publish_and_read_are_generation_and_visibility_consistent(tmp_path: Path) -> None:
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    manifest, records = _snapshot(1)
    store.stage_generation(manifest, records)
    assert store.active_manifest(manifest.project_id) is None
    store.publish_generation(
        project_id=manifest.project_id,
        generation_id=manifest.generation_id,
        expected_manifest_sha256=manifest.content_sha256,
        published_at="2026-08-03T01:00:00Z",
    )
    assert store.active_manifest(manifest.project_id) == manifest
    page = store.read_record(
        project_id=manifest.project_id,
        requester_acl_refs=("team-a",),
        logical_path="/capabilities/evidence-navigation-a4c198e7",
        record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
    )
    assert page is not None and page.scope.source_generations == manifest.source_generations
    assert (
        store.read_record(
            project_id=manifest.project_id,
            requester_acl_refs=("team-b",),
            logical_path="/capabilities/evidence-navigation-a4c198e7",
            record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
        )
        is None
    )
    assert not Path(str(store.path) + "-wal").exists()
    assert not Path(str(store.path) + "-shm").exists()


def test_snapshot_cache_is_bounded_and_partitioned_by_generation_and_acl(tmp_path: Path) -> None:
    store = WikiStoreV1(
        tmp_path / "wiki.sqlite3",
        isolated_root=tmp_path,
        cache_max_entries=8,
        cache_max_bytes=256_000,
    )
    first_manifest, first_records = _snapshot(1)
    store.stage_generation(first_manifest, first_records)
    store.publish_generation(
        project_id=first_manifest.project_id,
        generation_id=first_manifest.generation_id,
        expected_manifest_sha256=first_manifest.content_sha256,
        published_at="2026-08-03T01:00:00Z",
    )
    path = "/capabilities/evidence-navigation-a4c198e7"
    first = store.read_record(
        project_id=first_manifest.project_id,
        requester_acl_refs=("team-a",),
        logical_path=path,
        record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
    )
    assert first is not None
    assert store.cache_stats().misses == 1
    assert (
        store.read_record(
            project_id=first_manifest.project_id,
            requester_acl_refs=("team-a",),
            logical_path=path,
            record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
        )
        == first
    )
    assert store.cache_stats().hits == 1
    assert (
        store.read_record(
            project_id=first_manifest.project_id,
            requester_acl_refs=("team-b",),
            logical_path=path,
            record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
        )
        is None
    )
    assert store.cache_stats().misses == 2

    second_manifest, second_records = _snapshot(2)
    store.stage_generation(second_manifest, second_records)
    store.publish_generation(
        project_id=second_manifest.project_id,
        generation_id=second_manifest.generation_id,
        expected_manifest_sha256=second_manifest.content_sha256,
        published_at="2026-08-03T02:00:00Z",
    )
    second = store.read_record(
        project_id=second_manifest.project_id,
        requester_acl_refs=("team-a",),
        logical_path=path,
        record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
    )
    assert second is not None and second != first
    assert second.scope.source_generations == second_manifest.source_generations
    assert (
        store.read_record(
            project_id=first_manifest.project_id,
            requester_acl_refs=("team-a",),
            logical_path=path,
            record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
            generation_id=first_manifest.generation_id,
        )
        == first
    )
    stats = store.cache_stats()
    assert stats.entries == 2
    assert stats.bytes_used <= stats.max_bytes

    one_entry_store = WikiStoreV1(
        tmp_path / "one-entry.sqlite3",
        isolated_root=tmp_path,
        cache_max_entries=1,
        cache_max_bytes=256_000,
    )
    one_entry_store.stage_generation(first_manifest, first_records)
    pages = tuple(record for record in first_records if isinstance(record, WikiPageFragmentV1))
    one_entry_store.read_pages(
        project_id=first_manifest.project_id,
        requester_acl_refs=("team-a",),
        logical_paths=tuple(page.logical_path for page in pages),
        generation_id=first_manifest.generation_id,
    )
    bounded = one_entry_store.cache_stats()
    assert bounded.entries == 1
    assert bounded.evictions == 1


def test_concurrent_publish_and_generation_pinned_reads_never_mix_snapshots(
    tmp_path: Path,
) -> None:
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    first_manifest, first_records = _snapshot(1)
    second_manifest, second_records = _snapshot(2)
    store.stage_generation(first_manifest, first_records)
    store.publish_generation(
        project_id=first_manifest.project_id,
        generation_id=first_manifest.generation_id,
        expected_manifest_sha256=first_manifest.content_sha256,
        published_at="2026-08-03T01:00:00Z",
    )
    store.stage_generation(second_manifest, second_records)
    paths = (
        "/capabilities/evidence-navigation-a4c198e7",
        "/components/wiki-store-c881f713",
    )
    barrier = Barrier(9)

    def read_snapshots() -> tuple[str, ...]:
        barrier.wait()
        observed: list[str] = []
        for _ in range(100):
            manifest = store.active_manifest(first_manifest.project_id)
            assert manifest is not None
            pages = store.read_pages(
                project_id=manifest.project_id,
                requester_acl_refs=("team-a",),
                logical_paths=paths,
                generation_id=manifest.generation_id,
            )
            assert len(pages) == 2
            assert all(
                page.scope.source_generations == manifest.source_generations for page in pages
            )
            observed.append(manifest.generation_id)
        return tuple(observed)

    def publish_second() -> None:
        barrier.wait()
        store.publish_generation(
            project_id=second_manifest.project_id,
            generation_id=second_manifest.generation_id,
            expected_manifest_sha256=second_manifest.content_sha256,
            published_at="2026-08-03T02:00:00Z",
        )

    with ThreadPoolExecutor(max_workers=9) as executor:
        readers = [executor.submit(read_snapshots) for _ in range(8)]
        writer = executor.submit(publish_second)
        writer.result()
        observed = tuple(generation for task in readers for generation in task.result())
    assert set(observed).issubset({first_manifest.generation_id, second_manifest.generation_id})
    assert store.active_manifest(first_manifest.project_id) == second_manifest
    assert not Path(str(store.path) + "-wal").exists()
    assert not Path(str(store.path) + "-shm").exists()


def test_failed_stage_cannot_change_active_generation(tmp_path: Path) -> None:
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    first_manifest, first_records = _snapshot(1)
    store.stage_generation(first_manifest, first_records)
    store.publish_generation(
        project_id=first_manifest.project_id,
        generation_id=first_manifest.generation_id,
        expected_manifest_sha256=first_manifest.content_sha256,
        published_at="2026-08-03T01:00:00Z",
    )
    second_manifest, second_records = _snapshot(2)
    with pytest.raises(WikiPublicationError, match="membership"):
        store.stage_generation(second_manifest, second_records[:-1])
    assert store.active_manifest(first_manifest.project_id) == first_manifest
    assert store.generation_count() == 1


def test_fts_candidates_are_acl_scoped_and_verified_against_page_authority(
    tmp_path: Path,
) -> None:
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    manifest, records = _snapshot(1)
    store.stage_generation(manifest, records)
    paths = store.search_page_paths_fts(
        project_id=manifest.project_id,
        requester_acl_refs=("team-a",),
        query="atomic generation implementation",
        generation_id=manifest.generation_id,
    )
    assert paths == (
        "/capabilities/evidence-navigation-a4c198e7",
        "/components/wiki-store-c881f713",
    )
    assert (
        store.search_page_paths_fts(
            project_id=manifest.project_id,
            requester_acl_refs=("team-b",),
            query="atomic generation implementation",
            generation_id=manifest.generation_id,
        )
        == ()
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE wiki_page_fts_v1 SET title='tampered' WHERE generation_id=?",
            (manifest.generation_id,),
        )
    with pytest.raises(WikiPublicationError, match="FTS index"):
        store.verify_generation(
            project_id=manifest.project_id,
            generation_id=manifest.generation_id,
            expected_manifest_sha256=manifest.content_sha256,
        )


def test_portable_snapshot_verifies_from_copy_without_database(tmp_path: Path) -> None:
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    manifest, records = _snapshot(1)
    store.stage_generation(manifest, records)
    store.publish_generation(
        project_id=manifest.project_id,
        generation_id=manifest.generation_id,
        expected_manifest_sha256=manifest.content_sha256,
        published_at="2026-08-03T01:00:00Z",
    )
    package = tmp_path / "snapshot"
    verification = store.export_snapshot(
        project_id=manifest.project_id,
        generation_id=manifest.generation_id,
        output_dir=package,
    )
    assert verification.record_count == len(records)
    assert verification.retrieval_executed is False
    copied = tmp_path / "copied-snapshot"
    shutil.copytree(package, copied)
    store.path.unlink()
    copied_verification = verify_wiki_snapshot_v1(copied)
    assert copied_verification == verification
    assert copied_verification.database_opened is False


def test_portable_snapshot_fails_closed_on_tamper_delete_extra_and_symlink(tmp_path: Path) -> None:
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    manifest, records = _snapshot(1)
    store.stage_generation(manifest, records)
    package = tmp_path / "snapshot"
    store.export_snapshot(
        project_id=manifest.project_id,
        generation_id=manifest.generation_id,
        output_dir=package,
    )
    tampered = tmp_path / "tampered"
    shutil.copytree(package, tampered)
    with (tampered / "records.jsonl").open("ab") as handle:
        handle.write(b"{}\n")
    with pytest.raises(WikiPublicationError, match="checksum"):
        verify_wiki_snapshot_v1(tampered)
    missing = tmp_path / "missing"
    shutil.copytree(package, missing)
    (missing / "snapshot.json").unlink()
    with pytest.raises(WikiPublicationError, match="membership"):
        verify_wiki_snapshot_v1(missing)
    extra = tmp_path / "extra"
    shutil.copytree(package, extra)
    (extra / "database.sqlite3").write_bytes(b"not allowed")
    with pytest.raises(WikiPublicationError, match="membership"):
        verify_wiki_snapshot_v1(extra)
    linked = tmp_path / "linked"
    shutil.copytree(package, linked)
    (linked / "snapshot.json").unlink()
    (linked / "snapshot.json").symlink_to(package / "snapshot.json")
    with pytest.raises(WikiPublicationError, match="regular files"):
        verify_wiki_snapshot_v1(linked)
