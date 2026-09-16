from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.models import EvidenceSearchRequest, SearchScope
from evidence_rag.rag.sources.code import (
    CodeChannelCompleteNoMatch,
    CodeChannelCompletePruned,
    CodeChannelError,
    CodeChannelOutcomeStatus,
    CodeExactSparseRetriever,
    CodeLocatorKind,
    CodeQueryProfile,
    CodeRetrievalBudget,
    CodeRetrievalChannel,
    CodeSourceResult,
    CodeSourceStatus,
)
from evidence_rag.storage import SQLiteStore

ACTIVE_SHA = "a" * 40
OLD_SHA = "b" * 40
BETA_SHA = "c" * 40
OTHER_SHA = "d" * 40


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "code-retrieval-v2.sqlite3")
    store.initialize()
    return store


def _seed_generation(
    store: SQLiteStore,
    *,
    repository_id: str = "repository://alpha",
    project_id: str = "project://alpha",
    generation_id: str = "generation://alpha-active",
    commit_sha: str = ACTIVE_SHA,
    acl_ref: str = "acl://alpha",
    active: bool = True,
    generation_status: str = "published",
    publication: bool = True,
    publication_status: str = "published",
    sparse: str = "fts5-code-v2",
) -> None:
    with store.transaction() as db:
        db.execute(
            """
            INSERT INTO repositories(
                id, project_id, name, source_type, local_path, default_branch,
                head_commit, acl_ref, status, active_generation_id, created_at, updated_at
            ) VALUES (?, ?, ?, 'local', ?, 'main', ?, ?, 'ready', ?, 'now', 'now')
            ON CONFLICT(id) DO UPDATE SET
                project_id=excluded.project_id,
                head_commit=CASE
                    WHEN excluded.active_generation_id IS NULL
                    THEN repositories.head_commit
                    ELSE excluded.head_commit
                END,
                active_generation_id=CASE
                    WHEN excluded.active_generation_id IS NULL
                    THEN repositories.active_generation_id
                    ELSE excluded.active_generation_id
                END
            """,
            (
                repository_id,
                project_id,
                repository_id,
                f"/tmp/{repository_id.rsplit('/', 1)[-1]}",
                commit_sha,
                acl_ref,
                generation_id if active else None,
            ),
        )
        db.execute(
            """
            INSERT INTO index_generations(
                id, repository_id, commit_sha, status, started_at, completed_at
            ) VALUES (?, ?, ?, ?, 'now', 'now')
            """,
            (generation_id, repository_id, commit_sha, generation_status),
        )
    if publication:
        store.upsert_code_index_publication(
            {
                "generation_id": generation_id,
                "repository_id": repository_id,
                "project_id": project_id,
                "builder": "ast-v2-test",
                "sparse": sparse,
                "embedding": "not-built",
                "graph": "not-built",
                "status": publication_status,
                "validation": {
                    "capabilities": {
                        "ast_unit_builder": True,
                        "unit_store": True,
                        "fts_row_integrity": True,
                        "sparse_retrieval": sparse != "not-built",
                        "dense_retrieval": False,
                        "graph_retrieval": False,
                    }
                },
            }
        )


def _seed_unit(
    store: SQLiteStore,
    *,
    unit_id: str,
    entity_id: str,
    path: str,
    qualified_name: str,
    identifiers: list[str],
    body: str,
    repository_id: str = "repository://alpha",
    project_id: str = "project://alpha",
    generation_id: str = "generation://alpha-active",
    commit_sha: str = ACTIVE_SHA,
    acl_ref: str = "acl://alpha",
    signature: str = "",
    doc: str = "",
    start_line: int = 1,
    end_line: int = 4,
    entity_type: str = "FileVersion",
    unit_type: str = "symbol.ast_block",
    ast_node_type: str = "function_definition",
    ordinal: int = 0,
    structural_path: str = "module/function_definition[0]",
) -> None:
    with store.transaction() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO entities(
                id, repository_id, generation_id, project_id, entity_type, name,
                qualified_name, path, language, commit_sha, content_hash, source_uri,
                acl_ref, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'python', ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                repository_id,
                generation_id,
                project_id,
                entity_type,
                qualified_name or path,
                qualified_name or path,
                path,
                commit_sha,
                f"entity-hash-{entity_id}-{generation_id}",
                f"code://{repository_id}@{commit_sha}/{path}",
                acl_ref,
                body,
            ),
        )
    store.insert_code_units(
        [
            {
                "id": unit_id,
                "entity_id": entity_id,
                "parent_unit_id": None,
                "repository_id": repository_id,
                "generation_id": generation_id,
                "project_id": project_id,
                "unit_type": unit_type,
                "ast_node_type": ast_node_type,
                "ordinal": ordinal,
                "language": "python",
                "path": path,
                "qualified_name": qualified_name,
                "signature": signature,
                "identifiers": identifiers,
                "doc": doc,
                "body": body,
                "content": body,
                "context_ref": {
                    "parent_entity_id": entity_id,
                    "structural_path": structural_path,
                    "source_range": {
                        "start_line": start_line,
                        "end_line": end_line,
                        "start_byte": 0,
                        "end_byte": len(body.encode()),
                    },
                },
                "start_line": start_line,
                "end_line": end_line,
                "start_byte": 0,
                "end_byte": len(body.encode()),
                "token_count": max(1, (len(body.encode()) + 3) // 4),
                "content_hash": f"unit-hash-{unit_id}-{generation_id}",
                "builder_version": "ast-v2-test",
                "quality_status": "complete",
                "acl_ref": acl_ref,
                "metadata": {
                    "ref": commit_sha,
                    "parser_version": "tree-sitter-test",
                    "source_lineage": {
                        "source_uri": f"code://{repository_id}@{commit_sha}/{path}",
                        "ref": commit_sha,
                    },
                },
            }
        ]
    )


def _request(
    query: str,
    *,
    project_id: str = "project://alpha",
    repository_ids: list[str] | None = None,
    commit: str | None = None,
    branch: str | None = None,
    allowed_acl_refs: list[str] | None = None,
    enforce_acl: bool = True,
    limit: int = 10,
) -> EvidenceSearchRequest:
    return EvidenceSearchRequest(
        query=query,
        limit=limit,
        scope=SearchScope(
            project_id=project_id,
            repository_ids=repository_ids or ["repository://alpha"],
            commit=commit,
            branch=branch,
            allowed_acl_refs=allowed_acl_refs or ["acl://alpha"],
            enforce_acl=enforce_acl,
        ),
    )


def _profile(
    *,
    target_identifiers: list[str] | None = None,
    target_paths: list[str] | None = None,
    target_ref: str = "current",
    exact_candidates: int = 20,
    sparse_candidates: int = 40,
    total_candidates: int = 50,
) -> CodeQueryProfile:
    return CodeQueryProfile(
        task="exact_location",
        target_identifiers=target_identifiers or [],
        target_paths=target_paths or [],
        target_ref=target_ref,
        budget=CodeRetrievalBudget(
            total_candidates=total_candidates,
            exact_candidates=exact_candidates,
            sparse_candidates=sparse_candidates,
            dense_candidates=0,
            context_token_budget=0,
        ),
    )


def _seed_primary(store: SQLiteStore) -> None:
    _seed_generation(store)
    _seed_unit(
        store,
        unit_id="unit://alpha/quote",
        entity_id="entity://alpha/pricing",
        path="src/checkout/pricing.py",
        qualified_name="checkout.PriceEngine.quote",
        identifiers=["PriceEngine", "quote", "subtotal_cents"],
        signature="def quote(self, subtotal_cents: int) -> str",
        doc="Return a checkout quote.",
        body="def quote(self, subtotal_cents):\n    return str(subtotal_cents)\n",
        start_line=10,
        end_line=12,
    )


def _candidate_ids(result: CodeSourceResult) -> list[str]:
    return [candidate.retrieval_unit_id for candidate in result.candidates]


@pytest.mark.parametrize(
    ("query", "profile"),
    [
        (
            f"code://repository://alpha@{ACTIVE_SHA}/src/checkout/pricing.py#L10-L12",
            None,
        ),
        ("checkout.PriceEngine.quote", None),
        ("src/checkout/pricing.py", None),
        ("quote", None),
        ("pricing.py", None),
        (
            'File "src/checkout/pricing.py", line 10, in quote',
            None,
        ),
    ],
)
def test_exact_locator_variants_return_strict_provenance(
    tmp_path: Path,
    query: str,
    profile: CodeQueryProfile | None,
) -> None:
    store = _store(tmp_path)
    _seed_primary(store)

    response = CodeExactSparseRetriever(store).search_with_trace(_request(query), profile)

    assert response.result.status is CodeSourceStatus.COMPLETE
    assert response.result.candidates
    candidate = response.result.candidates[0]
    assert candidate.retrieval_unit_id == "unit://alpha/quote"
    assert candidate.entity_id == "entity://alpha/pricing"
    assert candidate.repository_id == "repository://alpha"
    assert candidate.source_generation == "generation://alpha-active"
    assert candidate.stable_version == ACTIVE_SHA
    assert candidate.acl_ref == "acl://alpha"
    assert candidate.locator.endswith("#L10-L12")
    assert candidate.relation_path.nodes[-1].locator == candidate.locator
    assert CodeRetrievalChannel.EXACT in {score.channel for score in candidate.raw_channel_scores}
    assert response.trace.locators
    assert type(response.result).model_validate_json(response.result.model_dump_json()) == (
        response.result
    )


def test_test_selector_error_code_branch_and_tag_availability(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_primary(store)
    _seed_unit(
        store,
        unit_id="unit://alpha/test-quote",
        entity_id="entity://alpha/test-pricing",
        path="tests/test_pricing.py",
        qualified_name="tests.test_pricing.TestPrice.test_quote",
        identifiers=["TestPrice", "test_quote", "E11000"],
        signature="def test_quote() -> None",
        body="def test_quote():\n    raise RuntimeError('E11000')\n",
        start_line=20,
        end_line=22,
    )
    retriever = CodeExactSparseRetriever(store)

    selector = retriever.search_with_trace(_request("tests/test_pricing.py::TestPrice::test_quote"))
    assert _candidate_ids(selector.result)[0] == "unit://alpha/test-quote"
    assert {locator.kind for locator in selector.trace.locators} >= {
        CodeLocatorKind.TEST_SELECTOR,
        CodeLocatorKind.EXACT_PATH,
    }

    error = retriever.search_with_trace(_request("E11000"))
    assert "unit://alpha/test-quote" in _candidate_ids(error.result)
    assert CodeLocatorKind.ERROR_CODE in {locator.kind for locator in error.trace.locators}

    branch = retriever.search(
        _request("quote", branch="main"),
        _profile(target_identifiers=["quote"]),
    )
    assert _candidate_ids(branch)[0] == "unit://alpha/quote"
    assert branch.candidates[0].version_alignment.value == "exact"

    tag = retriever.search_with_trace(
        _request("tag:v1.0 quote"),
        _profile(target_identifiers=["quote"]),
    )
    assert tag.result.status is CodeSourceStatus.UNAVAILABLE
    assert not tag.result.candidates
    assert "Tag resolution is unavailable" in tag.trace.unavailable_reason
    assert any(
        locator.kind is CodeLocatorKind.TAG and not locator.available
        for locator in tag.trace.locators
    )


def test_sparse_normalization_and_matched_fields_use_existing_fts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_generation(store)
    _seed_unit(
        store,
        unit_id="unit://alpha/http-error",
        entity_id="entity://alpha/http-error",
        path="src/http/server.py",
        qualified_name="http.HTTPServerError.handle",
        identifiers=["HTTPServerError", "E11000", "left", "right"],
        signature="def handle(error: HTTPServerError) -> None",
        doc="中文检索错误处理。",
        body="if left == right:\n    raise HTTPServerError('E11000')\n",
    )
    query = "HTTPServerError 中文检索错误 E11000 =="

    response = CodeExactSparseRetriever(store).search_with_trace(_request(query))

    tokens = response.trace.normalized_tokens
    assert "HTTPServerError" in tokens
    assert {"http", "server", "error"} <= set(tokens)
    assert {"中文检索错误", "中文", "文检", "检索", "索错", "错误"} <= set(tokens)
    assert {"E11000", "e11000", "11000", "==", "eq", "__eq__", "equality"} <= set(tokens)
    sparse_trace = next(
        trace
        for trace in response.trace.channel_hits
        if trace.channel is CodeRetrievalChannel.SPARSE
        and trace.retrieval_unit_id == "unit://alpha/http-error"
    )
    assert {"qualified_name", "signature", "identifiers", "doc", "body"} <= set(
        sparse_trace.matched_fields
    )
    assert "field weights remain store-owned" in sparse_trace.explanation


def test_same_name_exact_keeps_path_hard_negatives_and_never_claims_unique_resolution(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_generation(store)
    for suffix, path in (
        ("checkout", "src/checkout/formatting.py"),
        ("admin", "src/admin/formatting.py"),
        ("tax", "src/tax/formatting.py"),
    ):
        _seed_unit(
            store,
            unit_id=f"unit://alpha/format-line-{suffix}",
            entity_id=f"entity://alpha/format-line-{suffix}",
            path=path,
            qualified_name=f"{suffix}.format_line",
            identifiers=["format_line", "label"],
            signature="def format_line(label: str) -> str",
            body="def format_line(label):\n    return label\n",
        )

    response = CodeExactSparseRetriever(store).search_with_trace(_request("format_line"))

    exact_traces = [
        trace
        for trace in response.trace.channel_hits
        if trace.channel is CodeRetrievalChannel.EXACT
    ]
    assert len(exact_traces) == 3
    assert {trace.locator for trace in exact_traces} == {
        "src/admin/formatting.py",
        "src/checkout/formatting.py",
        "src/tax/formatting.py",
    }
    assert all("without silent selection" in trace.explanation for trace in exact_traces)
    assert all("src/admin/formatting.py" in trace.explanation for trace in exact_traces)
    assert [trace.raw_rank for trace in exact_traces] == [0, 1, 2]


def test_acl_project_active_generation_and_explicit_version_are_isolated(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_generation(store)
    _seed_generation(
        store,
        generation_id="generation://alpha-old",
        commit_sha=OLD_SHA,
        active=False,
    )
    _seed_generation(
        store,
        repository_id="repository://beta",
        generation_id="generation://beta-active",
        commit_sha=BETA_SHA,
        acl_ref="acl://beta",
    )
    _seed_generation(
        store,
        repository_id="repository://other",
        project_id="project://other",
        generation_id="generation://other-active",
        commit_sha=OTHER_SHA,
        acl_ref="acl://other",
    )
    for unit_id, entity_id, repository_id, project_id, generation_id, sha, acl, marker in (
        (
            "unit://alpha/current",
            "entity://alpha/current",
            "repository://alpha",
            "project://alpha",
            "generation://alpha-active",
            ACTIVE_SHA,
            "acl://alpha",
            "current_marker",
        ),
        (
            "unit://alpha/old",
            "entity://alpha/old",
            "repository://alpha",
            "project://alpha",
            "generation://alpha-old",
            OLD_SHA,
            "acl://alpha",
            "old_marker",
        ),
        (
            "unit://beta/private",
            "entity://beta/private",
            "repository://beta",
            "project://alpha",
            "generation://beta-active",
            BETA_SHA,
            "acl://beta",
            "private_marker",
        ),
        (
            "unit://other/project",
            "entity://other/project",
            "repository://other",
            "project://other",
            "generation://other-active",
            OTHER_SHA,
            "acl://other",
            "other_marker",
        ),
    ):
        _seed_unit(
            store,
            unit_id=unit_id,
            entity_id=entity_id,
            path=f"src/{marker}.py",
            qualified_name=marker,
            identifiers=[marker],
            body=f"def {marker}(): pass\n",
            repository_id=repository_id,
            project_id=project_id,
            generation_id=generation_id,
            commit_sha=sha,
            acl_ref=acl,
        )
    retriever = CodeExactSparseRetriever(store)

    active = retriever.search(_request("current_marker"))
    assert _candidate_ids(active) == ["unit://alpha/current"]
    assert {candidate.source_generation for candidate in active.candidates} == {
        "generation://alpha-active"
    }

    old = retriever.search(
        _request("old_marker", commit=OLD_SHA),
        _profile(target_ref=OLD_SHA),
    )
    assert _candidate_ids(old) == ["unit://alpha/old"]
    assert old.candidates[0].stable_version == OLD_SHA
    assert old.candidates[0].version_alignment.value == "exact"

    short_sha = retriever.search(
        _request("old_marker", commit=OLD_SHA[:12]),
        _profile(target_ref=OLD_SHA[:12]),
    )
    assert _candidate_ids(short_sha) == ["unit://alpha/old"]

    denied = retriever.search(
        _request(
            "current_marker",
            allowed_acl_refs=["acl://beta"],
        )
    )
    assert denied.candidates == ()
    assert denied.status is CodeSourceStatus.COMPLETE

    beta = retriever.search(
        _request(
            "private_marker",
            repository_ids=["repository://beta"],
            allowed_acl_refs=["acl://beta"],
        )
    )
    assert _candidate_ids(beta) == ["unit://beta/private"]


def test_weighted_rrf_top_k_complete_pruned_and_determinism(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_generation(store)
    _seed_unit(
        store,
        unit_id="unit://alpha/exact",
        entity_id="entity://alpha/exact",
        path="src/exact_target.py",
        qualified_name="exact_target",
        identifiers=["exact_target"],
        body="def exact_target():\n    return 1\n",
    )
    _seed_unit(
        store,
        unit_id="unit://alpha/sparse",
        entity_id="entity://alpha/sparse",
        path="src/lexical_candidate.py",
        qualified_name="lexical_candidate",
        identifiers=["sparseonly"],
        body="def lexical_candidate():\n    return sparseonly\n",
    )
    request = _request("sparseonly", limit=1)
    profile = _profile(target_paths=["src/exact_target.py"])
    retriever = CodeExactSparseRetriever(store)

    first = retriever.search_with_trace(request, profile)
    second = retriever.search_with_trace(request, profile)

    assert _candidate_ids(first.result) == ["unit://alpha/exact"]
    assert first.result.candidates[0].raw_channel_ranks[0].channel is CodeRetrievalChannel.EXACT
    sparse_outcome = next(
        outcome
        for outcome in first.result.channel_outcomes
        if outcome.channel is CodeRetrievalChannel.SPARSE
    )
    assert isinstance(sparse_outcome, CodeChannelCompletePruned)
    assert sparse_outcome.hit_count == 1
    assert first.trace.fusion_policy == ("deterministic-weighted-rrf-v1:k=60:exact=2:sparse=1")
    assert first.result.candidates[0].model_dump(mode="json") == second.result.candidates[
        0
    ].model_dump(mode="json")
    assert first.trace == second.trace
    assert first.result.query_id == second.result.query_id


def test_entity_diversification_prefers_real_canonical_units_before_children(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_generation(store)
    primary_entity = "entity://alpha/primary-symbol"
    _seed_unit(
        store,
        unit_id="unit://alpha/primary-definition",
        entity_id=primary_entity,
        entity_type="CodeSymbol",
        path="src/primary.py",
        qualified_name="pkg.Primary.target_symbol",
        identifiers=["Primary", "target_symbol"],
        signature="def target_symbol(value: int) -> int",
        body="def target_symbol(value):\n    return value\n",
        start_line=10,
        end_line=80,
        unit_type="symbol.ast_block",
        ast_node_type="function_definition",
        structural_path="module/function_definition[0]",
    )
    _seed_unit(
        store,
        unit_id="unit://alpha/primary-branch",
        entity_id=primary_entity,
        entity_type="CodeSymbol",
        path="src/primary.py",
        qualified_name="pkg.Primary.target_symbol",
        identifiers=["Primary", "target_symbol"],
        body="if target_symbol:\n    return target_symbol\n",
        start_line=20,
        end_line=30,
        unit_type="branch.ast_block",
        ast_node_type="if_statement",
        ordinal=1,
        structural_path="module/function_definition[0]/if_statement[0]",
    )
    _seed_unit(
        store,
        unit_id="unit://alpha/primary-loop",
        entity_id=primary_entity,
        entity_type="CodeSymbol",
        path="src/primary.py",
        qualified_name="pkg.Primary.target_symbol",
        identifiers=["Primary", "target_symbol"],
        body="for item in target_symbol:\n    yield item\n",
        start_line=40,
        end_line=60,
        unit_type="loop.ast_block",
        ast_node_type="for_statement",
        ordinal=2,
        structural_path="module/function_definition[0]/for_statement[0]",
    )
    for index in range(8):
        _seed_unit(
            store,
            unit_id=f"unit://alpha/related-{index}",
            entity_id=f"entity://alpha/related-{index}",
            entity_type="CodeSymbol",
            path=f"src/related_{index}.py",
            qualified_name=f"pkg.Related{index}.candidate",
            identifiers=["target_symbol", f"related_{index}"],
            body=f"def related_{index}():\n    return target_symbol\n",
            start_line=100 + index * 10,
            end_line=104 + index * 10,
        )
    file_entity = "entity://alpha/file-version"
    _seed_unit(
        store,
        unit_id="unit://alpha/file-surface",
        entity_id=file_entity,
        entity_type="FileVersion",
        path="src/file_surface.py",
        qualified_name="src.file_surface",
        identifiers=["target_symbol"],
        body="target_symbol = 'surface'\n",
        start_line=1,
        end_line=200,
        unit_type="file.surface",
        ast_node_type="module",
        structural_path="module",
    )
    _seed_unit(
        store,
        unit_id="unit://alpha/file-child",
        entity_id=file_entity,
        entity_type="FileVersion",
        path="src/file_surface.py",
        qualified_name="src.file_surface",
        identifiers=["target_symbol"],
        body="if target_symbol:\n    pass\n",
        start_line=25,
        end_line=27,
        unit_type="branch.ast_block",
        ast_node_type="if_statement",
        ordinal=1,
        structural_path="module/if_statement[0]",
    )
    retriever = CodeExactSparseRetriever(store)

    top_ten = retriever.search_with_trace(_request("target_symbol", limit=10))
    repeated = retriever.search_with_trace(_request("target_symbol", limit=10))

    assert len(top_ten.result.candidates) == 10
    assert len({candidate.entity_id for candidate in top_ten.result.candidates}) == 10
    assert top_ten.result.candidates[0].retrieval_unit_id == "unit://alpha/file-surface"
    primary = next(
        candidate
        for candidate in top_ten.result.candidates
        if candidate.entity_id == primary_entity
    )
    assert primary.retrieval_unit_id == "unit://alpha/primary-definition"
    assert primary.locator.endswith("/src/primary.py#L10-L80")
    assert "unit://alpha/primary-branch" not in _candidate_ids(top_ten.result)
    assert "unit://alpha/primary-loop" not in _candidate_ids(top_ten.result)
    assert top_ten.result.model_dump(
        mode="json", exclude={"latency_ms"}
    ) == repeated.result.model_dump(mode="json", exclude={"latency_ms"})
    assert top_ten.trace == repeated.trace

    definition_trace = next(
        trace
        for trace in top_ten.trace.channel_hits
        if trace.channel is CodeRetrievalChannel.EXACT
        and trace.retrieval_unit_id == "unit://alpha/primary-definition"
    )
    branch_trace = next(
        trace
        for trace in top_ten.trace.channel_hits
        if trace.channel is CodeRetrievalChannel.EXACT
        and trace.retrieval_unit_id == "unit://alpha/primary-branch"
    )
    file_trace = next(
        trace
        for trace in top_ten.trace.channel_hits
        if trace.channel is CodeRetrievalChannel.EXACT
        and trace.retrieval_unit_id == "unit://alpha/file-surface"
    )
    assert definition_trace.representative
    assert definition_trace.diversified_rank is not None
    assert definition_trace.original_fused_rank is not None
    assert "canonical CodeSymbol" in definition_trace.diversification_reason
    assert not branch_trace.representative
    assert branch_trace.diversified_rank is None
    assert branch_trace.raw_rank >= 0
    assert "deferred by top_k" in branch_trace.diversification_reason
    assert file_trace.representative
    assert "canonical FileVersion file.surface" in file_trace.diversification_reason

    expanded = retriever.search_with_trace(_request("target_symbol", limit=13))
    assert len({candidate.entity_id for candidate in expanded.result.candidates[:10]}) == 10
    assert {candidate.retrieval_unit_id for candidate in expanded.result.candidates[10:]} == {
        "unit://alpha/file-child",
        "unit://alpha/primary-branch",
        "unit://alpha/primary-loop",
    }
    assert all(
        "appended only after" in trace.diversification_reason
        for trace in expanded.trace.channel_hits
        if trace.channel is CodeRetrievalChannel.EXACT
        and trace.retrieval_unit_id
        in {
            "unit://alpha/file-child",
            "unit://alpha/primary-branch",
            "unit://alpha/primary-loop",
        }
    )

    qualified = retriever.search(_request("pkg.Primary.target_symbol", limit=3))
    uri = retriever.search(
        _request(
            f"code://repository://alpha@{ACTIVE_SHA}/src/primary.py",
            limit=3,
        )
    )
    assert _candidate_ids(qualified)[0] == "unit://alpha/primary-definition"
    assert _candidate_ids(uri)[0] == "unit://alpha/primary-definition"


@pytest.mark.parametrize(
    ("publication", "status", "sparse", "reason"),
    [
        (False, "published", "fts5-code-v2", "missing"),
        (True, "building", "fts5-code-v2", "not published"),
        (True, "published", "not-built", "not built"),
    ],
)
def test_publication_missing_building_or_not_built_fails_closed(
    tmp_path: Path,
    publication: bool,
    status: str,
    sparse: str,
    reason: str,
) -> None:
    store = _store(tmp_path)
    _seed_generation(
        store,
        publication=publication,
        publication_status=status,
        sparse=sparse,
    )
    _seed_unit(
        store,
        unit_id="unit://alpha/unreachable",
        entity_id="entity://alpha/unreachable",
        path="src/unreachable.py",
        qualified_name="unreachable",
        identifiers=["unreachable"],
        body="def unreachable(): pass\n",
    )

    response = CodeExactSparseRetriever(store).search_with_trace(_request("unreachable"))

    assert response.result.status is CodeSourceStatus.UNAVAILABLE
    assert response.result.candidates == ()
    assert reason in response.trace.unavailable_reason.casefold()
    assert {
        outcome.status
        for outcome in response.result.channel_outcomes
        if outcome.channel in {CodeRetrievalChannel.EXACT, CodeRetrievalChannel.SPARSE}
    } == {CodeChannelOutcomeStatus.UNAVAILABLE}
    assert {error.kind.value for error in response.result.errors} == {"unavailable"}


def test_complete_no_match_and_sparse_store_error_are_contract_states(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_primary(store)
    retriever = CodeExactSparseRetriever(store)

    no_match = retriever.search(_request("NoSuchIdentifier"))
    assert no_match.status is CodeSourceStatus.COMPLETE
    assert no_match.candidates == ()
    assert all(
        isinstance(outcome, CodeChannelCompleteNoMatch) for outcome in no_match.channel_outcomes[:2]
    )

    with store.connection() as db:
        db.execute("DROP TABLE code_retrieval_units_fts")
    partial = retriever.search(
        _request("=="),
        _profile(target_paths=["src/checkout/pricing.py"]),
    )
    assert partial.status is CodeSourceStatus.PARTIAL
    assert _candidate_ids(partial) == ["unit://alpha/quote"]
    sparse_outcome = next(
        outcome
        for outcome in partial.channel_outcomes
        if outcome.channel is CodeRetrievalChannel.SPARSE
    )
    assert isinstance(sparse_outcome, CodeChannelError)
    assert len(partial.errors) == 1
    assert partial.errors[0].channel is CodeRetrievalChannel.SPARSE
    assert partial.errors[0].kind.value == "error"


def test_result_contract_round_trip_preserves_raw_ranks_locator_and_provenance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_primary(store)

    result = CodeExactSparseRetriever(store).search(
        _request("quote"),
        _profile(target_identifiers=["quote"]),
    )
    round_tripped = CodeSourceResult.model_validate_json(result.model_dump_json())

    assert round_tripped == result
    assert round_tripped.canonical_sha256() == result.canonical_sha256()
    candidate = round_tripped.candidates[0]
    assert {rank.channel for rank in candidate.raw_channel_ranks} == {
        CodeRetrievalChannel.EXACT,
        CodeRetrievalChannel.SPARSE,
    }
    terminal = candidate.relation_path.nodes[-1]
    assert (
        terminal.entity_id,
        terminal.repository_id,
        terminal.stable_version,
        terminal.source_generation,
        terminal.locator,
        terminal.acl_ref,
    ) == (
        candidate.entity_id,
        candidate.repository_id,
        candidate.stable_version,
        candidate.source_generation,
        candidate.locator,
        candidate.acl_ref,
    )
