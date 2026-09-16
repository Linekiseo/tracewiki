from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from evidence_rag.models import EvidenceSearchRequest, SearchScope
from evidence_rag.rag.sources.code import (
    CODE_TASK_CONTEXT_ROLE_ORDER,
    CONTEXT_BUILDER_CONTRACT_VERSION,
    CONTEXT_BUILDER_VERSION,
    CodeCandidateRole,
    CodeChannelCompleteNoMatch,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeChannelRank,
    CodeChannelScore,
    CodeContextBlock,
    CodeDerivation,
    CodeFactStatus,
    CodeRelationHop,
    CodeRelationLocator,
    CodeRelationNode,
    CodeRelationPath,
    CodeRelationType,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeSourceFusionPipeline,
    CodeSourceFusionSearchResult,
    CodeSourceFusionTrace,
    CodeSourceResult,
    CodeSourceStatus,
    CodeTask,
    CodeTaskContextBuilder,
    CodeTraversalDirection,
    CodeUncalibratedScore,
    CodeVersionAlignment,
    ContextBudget,
    ContextBuildLocatorError,
    ContextBuildRefusalError,
    ContextBuildScope,
    ContextBuildScopeError,
    ContextBuildStatus,
    ContextMetricStatus,
    ContextMissingStatus,
    ContextRefusalCode,
    ContextRole,
    ContextRoleBlock,
    RetrievalContext,
)
from evidence_rag.rag.sources.code.query_profile_v2 import (
    PROFILE_REGISTRY_VERSION,
    CodePipelineStageStatus,
    CodePipelineTraceEvent,
    CodeQueryRewrite,
    ResolvedCodeQueryProfile,
    get_code_query_profile,
)

PROJECT = "project://context-builder"
REPOSITORY = "repository-alpha"
BASE_VERSION = "a" * 40
DIRTY_VERSION = BASE_VERSION + "+dirty.manifest-7"
GENERATION = "generation://context-builder"
ACL = "acl://context-builder"
INDEX_VERSION = "index://code-v2"


def _scope(
    *,
    project_id: str = PROJECT,
    version: str = DIRTY_VERSION,
    generation: str = GENERATION,
    acl_ref: str = ACL,
    path_prefixes: tuple[str, ...] = ("src", "tests"),
) -> ContextBuildScope:
    return ContextBuildScope(
        project_id=project_id,
        repository_id=REPOSITORY,
        stable_version=version,
        source_generation=generation,
        acl_ref=acl_ref,
        index_version=INDEX_VERSION,
        watermark=version,
        allowed_path_prefixes=path_prefixes,
        allowed_acl_refs=(acl_ref,),
        enforce_acl=True,
        requested_commit=version,
        requested_branch=None,
        target_ref=version,
    )


def _node(
    name: str,
    *,
    entity_id: str | None = None,
    path: str | None = None,
    version: str = DIRTY_VERSION,
    generation: str = GENERATION,
    acl_ref: str = ACL,
    locator: str | None = None,
    line_count: int = 2,
) -> CodeRelationNode:
    source_path = path or f"src/{name}.py"
    resolved_locator = locator or f"code://{REPOSITORY}@{version}/{source_path}#L1-L{line_count}"
    return CodeRelationNode(
        entity_id=entity_id or f"entity://{name}",
        repository_id=REPOSITORY,
        stable_version=version,
        source_generation=generation,
        locator=resolved_locator,
        acl_ref=acl_ref,
    )


def _path(
    name: str,
    *,
    relation: CodeRelationType | None = None,
    direction: CodeTraversalDirection = CodeTraversalDirection.OUTGOING,
    entity_id: str | None = None,
    path: str | None = None,
    version: str = DIRTY_VERSION,
    generation: str = GENERATION,
    acl_ref: str = ACL,
    locator: str | None = None,
    line_count: int = 2,
) -> CodeRelationPath:
    terminal = _node(
        name,
        entity_id=entity_id,
        path=path,
        version=version,
        generation=generation,
        acl_ref=acl_ref,
        locator=locator,
        line_count=line_count,
    )
    if relation is None:
        return CodeRelationPath(nodes=(terminal,), edges=(), path_score=0.0)
    seed = _node(
        "seed",
        entity_id="entity://seed",
        path="src/seed.py",
        version=version,
        generation=generation,
        acl_ref=acl_ref,
    )
    stored_source, stored_target = (
        (seed, terminal) if direction is CodeTraversalDirection.OUTGOING else (terminal, seed)
    )
    edge = CodeRelationHop(
        source_entity_id=stored_source.entity_id,
        target_entity_id=stored_target.entity_id,
        source_repository_id=REPOSITORY,
        target_repository_id=REPOSITORY,
        edge_type=relation,
        direction=direction,
        hop=1,
        confidence=0.9,
        source_version=version,
        target_version=version,
        source_generation=generation,
        target_generation=generation,
        source_acl_ref=acl_ref,
        target_acl_ref=acl_ref,
        derivation=CodeDerivation.SCIP,
        review_status=CodeReviewStatus.MACHINE_CONFIRMED,
        fact_status=CodeFactStatus.MACHINE_CONFIRMED,
        locator=CodeRelationLocator(
            locator=stored_source.locator,
            owner_entity_id=stored_source.entity_id,
            repository_id=REPOSITORY,
            stable_version=version,
            source_generation=generation,
            acl_ref=acl_ref,
        ),
    )
    return CodeRelationPath(nodes=(seed, terminal), edges=(edge,), path_score=0.9)


def _candidate(
    name: str,
    rank: int,
    *,
    channel: CodeRetrievalChannel = CodeRetrievalChannel.EXACT,
    channel_rank: int | None = None,
    role: CodeCandidateRole = CodeCandidateRole.TARGET,
    entity_type: str = "CodeSymbol",
    relation: CodeRelationType | None = None,
    direction: CodeTraversalDirection = CodeTraversalDirection.OUTGOING,
    entity_id: str | None = None,
    unit_id: str | None = None,
    path: str | None = None,
    version: str = DIRTY_VERSION,
    generation: str = GENERATION,
    acl_ref: str = ACL,
    locator: str | None = None,
    alignment: CodeVersionAlignment = CodeVersionAlignment.EXACT,
    line_count: int = 2,
) -> CodeRetrievalCandidate:
    relation_path = _path(
        name,
        relation=relation,
        direction=direction,
        entity_id=entity_id,
        path=path,
        version=version,
        generation=generation,
        acl_ref=acl_ref,
        locator=locator,
        line_count=line_count,
    )
    terminal = relation_path.nodes[-1]
    if channel is CodeRetrievalChannel.GRAPH:
        channel = CodeRetrievalChannel.SPARSE
        raw_rank = rank
    else:
        raw_rank = rank if channel_rank is None else channel_rank
    return CodeRetrievalCandidate(
        entity_id=terminal.entity_id,
        retrieval_unit_id=unit_id or f"unit://{name}-{rank}",
        repository_id=REPOSITORY,
        entity_type=entity_type,
        stable_version=version,
        source_generation=generation,
        raw_channel_scores=(CodeChannelScore(channel=channel, score=1.0),),
        raw_channel_ranks=(CodeChannelRank(channel=channel, rank=raw_rank),),
        within_source_rank=rank,
        source_fused_score=1.0,
        calibrated_relevance=CodeUncalibratedScore(
            status="unavailable",
            reason="frozen context-builder fixture",
        ),
        version_alignment=alignment,
        fact_status=CodeFactStatus.OBSERVED,
        derivation=CodeDerivation.RAW_SOURCE,
        review_status=CodeReviewStatus.MACHINE_CONFIRMED,
        role=role,
        relation_path=relation_path,
        locator=terminal.locator,
        token_estimate=32,
        acl_ref=acl_ref,
    )


def _block(
    candidate: CodeRetrievalCandidate,
    content: str,
    *,
    block_id: str | None = None,
) -> CodeContextBlock:
    return CodeContextBlock(
        block_id=block_id or f"source-block://{candidate.retrieval_unit_id.rsplit('/', 1)[-1]}",
        entity_id=candidate.entity_id,
        retrieval_unit_id=candidate.retrieval_unit_id,
        repository_id=candidate.repository_id,
        role=candidate.role,
        content=content,
        stable_version=candidate.stable_version,
        source_generation=candidate.source_generation,
        relation_path=candidate.relation_path,
        locator=candidate.locator,
        token_estimate=max(1, len(content.encode("utf-8")) // 4),
        acl_ref=candidate.acl_ref,
    )


def _result(
    candidates: tuple[CodeRetrievalCandidate, ...],
    blocks: tuple[CodeContextBlock, ...] = (),
    *,
    version: str = DIRTY_VERSION,
) -> CodeSourceResult:
    observed = {score.channel for candidate in candidates for score in candidate.raw_channel_scores}
    outcomes = []
    for channel in CodeRetrievalChannel:
        if channel in observed:
            hit_count = (
                max(
                    raw_rank.rank
                    for candidate in candidates
                    for raw_rank in candidate.raw_channel_ranks
                    if raw_rank.channel is channel
                )
                + 1
            )
            outcomes.append(CodeChannelCompleteWithHits(channel=channel, hit_count=hit_count))
        elif channel in {
            CodeRetrievalChannel.EXACT,
            CodeRetrievalChannel.SPARSE,
            CodeRetrievalChannel.DENSE,
            CodeRetrievalChannel.GRAPH,
        }:
            outcomes.append(CodeChannelCompleteNoMatch(channel=channel))
        else:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason="disabled by frozen context-builder fixture",
                )
            )
    return CodeSourceResult(
        query_id="query://context-builder",
        status=CodeSourceStatus.COMPLETE,
        channel_outcomes=tuple(outcomes),
        candidates=candidates,
        context_blocks=blocks,
        index_version=INDEX_VERSION,
        watermark=version,
        latency_ms=1.0,
        errors=(),
    )


def _fusion(
    task: CodeTask,
    result: CodeSourceResult,
) -> CodeSourceFusionSearchResult:
    definition = get_code_query_profile(task)
    rewrite = CodeQueryRewrite(
        version="code-query-rewrite-v2",
        policy=definition.rewrite_policy,
        original_query="frozen query",
        rewritten_query="frozen query",
        target_identifiers=("target",),
        accepted_paths=(),
        rejected_paths=(),
        exact_fast_path=False,
    )
    profile = ResolvedCodeQueryProfile(
        definition=definition,
        rewrite=rewrite,
        contract=definition.contract(rewrite, target_ref=DIRTY_VERSION),
    )
    trace = CodeSourceFusionTrace(
        pipeline_version="code-source-fusion-pipeline-v2",
        registry_version=PROFILE_REGISTRY_VERSION,
        profile_key=definition.key,
        profile_version=definition.version,
        task=task,
        rewrite=rewrite,
        events=(
            CodePipelineTraceEvent(
                stage="result",
                status=CodePipelineStageStatus.COMPLETE,
                reason="frozen fixture",
                output_count=len(result.candidates),
            ),
        ),
        required_roles=definition.required_roles,
        missing_roles=(),
        requested_k=len(result.candidates),
        adaptive_k=len(result.candidates),
        refused=False,
    )
    return CodeSourceFusionSearchResult(result=result, trace=trace, profile=profile)


class _FrozenContextRetriever:
    retriever_version = "frozen-context-retriever-v1"

    def __init__(self, result: CodeSourceResult) -> None:
        self.result = result

    def search_with_trace(self, _request, _profile):
        return SimpleNamespace(result=self.result)


def _production_fusion(
    task: CodeTask,
    candidates: tuple[CodeRetrievalCandidate, ...],
    blocks: tuple[CodeContextBlock, ...],
    *,
    project_id: str = PROJECT,
    repository_ids: tuple[str, ...] = (REPOSITORY,),
    allowed_acl_refs: tuple[str, ...] = (ACL,),
    enforce_acl: bool = True,
) -> CodeSourceFusionSearchResult:
    result = _result(candidates, blocks)
    request = EvidenceSearchRequest(
        query=f"frozen {task.value} query",
        limit=max(1, len(candidates)),
        scope=SearchScope(
            project_id=project_id,
            repository_ids=list(repository_ids),
            commit=DIRTY_VERSION,
            allowed_acl_refs=list(allowed_acl_refs),
            enforce_acl=enforce_acl,
        ),
    )
    return CodeSourceFusionPipeline(_FrozenContextRetriever(result)).search_with_trace(
        request, task=task
    )


def _build(
    task: CodeTask,
    candidates: tuple[CodeRetrievalCandidate, ...],
    blocks: tuple[CodeContextBlock, ...],
    *,
    budget: ContextBudget | None = None,
):
    return CodeTaskContextBuilder().build(
        _production_fusion(task, candidates, blocks),
        scope=_scope(),
        budget=budget,
    )


def test_package_exports_exact_builder_contracts_and_versions() -> None:
    assert CONTEXT_BUILDER_CONTRACT_VERSION == "c7-code-task-context-v1"
    assert CONTEXT_BUILDER_VERSION == "c7-code-task-context-builder-v1"
    assert CodeTaskContextBuilder.builder_version == CONTEXT_BUILDER_VERSION
    assert set(CODE_TASK_CONTEXT_ROLE_ORDER) == {
        CodeTask.IMPLEMENTATION,
        CodeTask.BUG_LOCALIZATION,
        CodeTask.IMPACT_ANALYSIS,
        CodeTask.CHANGE_CONTEXT,
    }


@pytest.mark.parametrize(
    ("task", "expected_roles"),
    [
        (
            CodeTask.IMPLEMENTATION,
            (
                ContextRole.TARGET_SYMBOL,
                ContextRole.SIGNATURE_DOC,
                ContextRole.RELEVANT_AST_BLOCKS,
                ContextRole.REQUIRED_IMPORTS_TYPES,
                ContextRole.DIRECT_CALLERS_CALLEES,
                ContextRole.RELATED_TESTS,
                ContextRole.VERSION_LOCATOR,
            ),
        ),
        (
            CodeTask.BUG_LOCALIZATION,
            (
                ContextRole.ERROR_STACK,
                ContextRole.SUSPECT_TARGET,
                ContextRole.GRAPH_PATH,
                ContextRole.RELEVANT_BLOCK,
                ContextRole.RECENT_DIFF,
                ContextRole.FAILED_PASSED_VALIDATION,
                ContextRole.ALTERNATIVE_SUSPECTS,
            ),
        ),
        (
            CodeTask.IMPACT_ANALYSIS,
            (
                ContextRole.CHANGED_SYMBOL,
                ContextRole.INCOMING_REFERENCES_CALLS,
                ContextRole.IMPLEMENTATIONS_OVERRIDES,
                ContextRole.TESTS_COVERAGE,
                ContextRole.AFFECTED_FILES_MODULES,
                ContextRole.UNRESOLVED_UNKNOWN,
            ),
        ),
        (
            CodeTask.CHANGE_CONTEXT,
            (
                ContextRole.EDITABLE_TARGET,
                ContextRole.CONTRACT_TYPE_PARENT,
                ContextRole.CALLERS_DEPENDENCIES,
                ContextRole.NEARBY_TESTS,
                ContextRole.RECENT_DIFF,
                ContextRole.VALIDATION_REQUIREMENT,
                ContextRole.DO_NOT_EDIT_GENERATED,
            ),
        ),
    ],
)
def test_four_authorized_templates_have_frozen_role_order(
    task: CodeTask,
    expected_roles: tuple[ContextRole, ...],
) -> None:
    assert CODE_TASK_CONTEXT_ROLE_ORDER[task] == expected_roles


def test_implementation_maps_original_source_and_preserves_retrieval_truth() -> None:
    target = _candidate("target", 0, line_count=3)
    imported_type = _candidate(
        "type",
        1,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=0,
        role=CodeCandidateRole.DEPENDENCY,
        entity_type="TypeEntity",
        relation=CodeRelationType.IMPORTS,
    )
    caller = _candidate(
        "caller",
        2,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=1,
        role=CodeCandidateRole.DEPENDENCY,
        relation=CodeRelationType.CALLS,
        direction=CodeTraversalDirection.INCOMING,
    )
    test = _candidate(
        "target_test",
        3,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=2,
        role=CodeCandidateRole.TEST,
        entity_type="TestCase",
        relation=CodeRelationType.TESTS,
        path="tests/test_target.py",
    )
    target_source = 'def target(value: int) -> int:\n    """Source doc."""\n    return value + 1\n'
    output = _build(
        CodeTask.IMPLEMENTATION,
        (target, imported_type, caller, test),
        (
            _block(target, target_source),
            _block(imported_type, "class TargetType:\n    pass\n"),
            _block(caller, "def caller():\n    return target(1)\n"),
            _block(test, "def test_target():\n    assert target(1) == 2\n"),
        ),
    )

    assert isinstance(output.retrieval, RetrievalContext)
    assert output.retrieval.candidates[0].candidate.raw_channel_ranks == (
        CodeChannelRank(channel=CodeRetrievalChannel.EXACT, rank=0),
    )
    assert output.retrieval.candidates[0].candidate.locator == target.locator
    assert output.retrieval.candidates[0].candidate.relation_path == target.relation_path
    assert output.comprehension.blocks[0].source_text == target_source
    assert not hasattr(output.comprehension.blocks[0], "summary")
    fulfilled = set(output.trace.fulfilled_roles)
    assert set(CODE_TASK_CONTEXT_ROLE_ORDER[CodeTask.IMPLEMENTATION]) <= fulfilled
    assert output.trace.locator_correctness.status is ContextMetricStatus.AVAILABLE
    assert output.trace.locator_correctness.value == 1.0


def test_bug_localization_uses_only_existing_path_and_marks_absent_path_missing() -> None:
    suspect = _candidate("suspect", 0)
    test_result = _candidate(
        "failed_result",
        1,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=0,
        role=CodeCandidateRole.TEST,
        entity_type="TestResult",
        relation=CodeRelationType.FAILED_VALIDATION,
        line_count=1,
    )
    alternative = _candidate(
        "alternative",
        2,
        channel=CodeRetrievalChannel.SPARSE,
        channel_rank=0,
    )
    recent_diff = _candidate(
        "diff",
        3,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=1,
        role=CodeCandidateRole.HISTORY,
        entity_type="DiffHunk",
        relation=CodeRelationType.AFFECTS,
    )
    output = _build(
        CodeTask.BUG_LOCALIZATION,
        (suspect, test_result, alternative, recent_diff),
        (
            _block(suspect, "def suspect():\n    raise RuntimeError()\n"),
            _block(test_result, "RuntimeError: failed\n"),
            _block(alternative, "def alternative():\n    return None\n"),
            _block(recent_diff, "- old\n+ new\n"),
        ),
    )

    path_citations = [
        block.citation
        for block in output.comprehension.blocks
        if ContextRole.GRAPH_PATH in block.roles
    ]
    assert path_citations
    assert all(citation.relation_path.edges for citation in path_citations)
    assert all("relation=" in citation.path_explanation for citation in path_citations)
    assert output.retrieval.candidates[0].citation.path_explanation is None
    assert {
        ContextRole.ERROR_STACK,
        ContextRole.SUSPECT_TARGET,
        ContextRole.RECENT_DIFF,
        ContextRole.FAILED_PASSED_VALIDATION,
        ContextRole.ALTERNATIVE_SUSPECTS,
    } <= set(output.trace.fulfilled_roles)

    no_path_output = _build(
        CodeTask.BUG_LOCALIZATION,
        (suspect,),
        (_block(suspect, "def suspect():\n    return None\n"),),
    )
    graph_missing = next(
        item
        for item in no_path_output.comprehension.missing_roles
        if item.role is ContextRole.GRAPH_PATH
    )
    assert graph_missing.status is ContextMissingStatus.MISSING
    assert "no existing relation path" in graph_missing.reason


def test_impact_maps_incoming_implementation_test_and_affected_file() -> None:
    changed = _candidate("changed", 0, line_count=1)
    incoming = _candidate(
        "incoming",
        1,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=0,
        role=CodeCandidateRole.DEPENDENCY,
        relation=CodeRelationType.CALLS,
        direction=CodeTraversalDirection.INCOMING,
        line_count=1,
    )
    implementation = _candidate(
        "implementation",
        2,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=1,
        role=CodeCandidateRole.DEPENDENCY,
        relation=CodeRelationType.IMPLEMENTS,
        direction=CodeTraversalDirection.INCOMING,
        line_count=1,
    )
    test = _candidate(
        "coverage",
        3,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=2,
        role=CodeCandidateRole.TEST,
        entity_type="Coverage",
        relation=CodeRelationType.COVERS,
        path="tests/test_changed.py",
        line_count=1,
    )
    affected_file = _candidate(
        "affected",
        4,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=3,
        role=CodeCandidateRole.DEPENDENCY,
        entity_type="FileVersion",
        relation=CodeRelationType.AFFECTS,
        line_count=1,
    )
    candidates = (changed, incoming, implementation, test, affected_file)
    output = _build(
        CodeTask.IMPACT_ANALYSIS,
        candidates,
        tuple(
            _block(item, f"# exact source for {item.retrieval_unit_id}\n") for item in candidates
        ),
    )

    assert {
        ContextRole.CHANGED_SYMBOL,
        ContextRole.INCOMING_REFERENCES_CALLS,
        ContextRole.IMPLEMENTATIONS_OVERRIDES,
        ContextRole.TESTS_COVERAGE,
        ContextRole.AFFECTED_FILES_MODULES,
    } <= set(output.trace.fulfilled_roles)
    unresolved = next(
        item
        for item in output.comprehension.missing_roles
        if item.role is ContextRole.UNRESOLVED_UNKNOWN
    )
    assert unresolved.status is ContextMissingStatus.MISSING


def test_modification_maps_editable_contract_dependencies_tests_diff_and_generated() -> None:
    target = _candidate("editable", 0, line_count=1)
    parent_type = _candidate(
        "parent_type",
        1,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=0,
        role=CodeCandidateRole.DEPENDENCY,
        entity_type="TypeEntity",
        relation=CodeRelationType.TYPE_OF,
        line_count=1,
    )
    dependency = _candidate(
        "dependency",
        2,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=1,
        role=CodeCandidateRole.DEPENDENCY,
        relation=CodeRelationType.IMPORTS,
        line_count=1,
    )
    nearby_test = _candidate(
        "nearby_test",
        3,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=2,
        role=CodeCandidateRole.TEST,
        entity_type="TestCase",
        relation=CodeRelationType.TESTS,
        path="tests/test_editable.py",
        line_count=1,
    )
    diff = _candidate(
        "recent_diff",
        4,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=3,
        role=CodeCandidateRole.HISTORY,
        entity_type="DiffHunk",
        relation=CodeRelationType.AFFECTS,
        line_count=1,
    )
    generated = _candidate(
        "generated",
        5,
        channel=CodeRetrievalChannel.SPARSE,
        channel_rank=0,
        role=CodeCandidateRole.BACKGROUND,
        entity_type="GeneratedFile",
        line_count=1,
    )
    candidates = (target, parent_type, dependency, nearby_test, diff, generated)
    output = _build(
        CodeTask.CHANGE_CONTEXT,
        candidates,
        tuple(
            _block(item, f"# exact source for {item.retrieval_unit_id}\n") for item in candidates
        ),
    )

    assert set(CODE_TASK_CONTEXT_ROLE_ORDER[CodeTask.CHANGE_CONTEXT]) <= set(
        output.trace.fulfilled_roles
    )
    generated_block = next(
        block
        for block in output.comprehension.blocks
        if ContextRole.DO_NOT_EDIT_GENERATED in block.roles
    )
    assert generated_block.citation.candidate_role is CodeCandidateRole.BACKGROUND


def test_same_entity_child_sources_keep_independent_content_roles_and_citations() -> None:
    parent = "entity://shared-parent"
    first = _candidate(
        "child_one",
        0,
        entity_id=parent,
        unit_id="unit://child-one",
        locator=f"code://{REPOSITORY}@{DIRTY_VERSION}/src/shared.py#L1-L1",
    )
    second = _candidate(
        "child_two",
        1,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=0,
        role=CodeCandidateRole.TEST,
        entity_type="TestCase",
        relation=CodeRelationType.TESTS,
        entity_id=parent,
        unit_id="unit://child-two",
        path="tests/shared_test.py",
        locator=f"code://{REPOSITORY}@{DIRTY_VERSION}/tests/shared_test.py#L20-L20",
    )
    target_source = "def target(): pass\n"
    test_source = "def test_target(): pass\n"
    output = _build(
        CodeTask.IMPLEMENTATION,
        (first, second),
        (
            _block(first, target_source, block_id="source-block://first"),
            _block(second, test_source, block_id="source-block://second"),
        ),
    )

    assert len(output.retrieval.candidates) == 2
    assert len(output.comprehension.blocks) == 2
    by_unit = {block.citation.retrieval_unit_id: block for block in output.comprehension.blocks}
    assert by_unit["unit://child-one"].source_text == target_source
    assert ContextRole.RELATED_TESTS not in by_unit["unit://child-one"].roles
    assert by_unit["unit://child-two"].source_text == test_source
    assert by_unit["unit://child-two"].roles == (ContextRole.RELATED_TESTS,)
    assert output.trace.duplicate_candidate_identities == ()


def test_identical_source_across_distinct_entities_keeps_both_citations() -> None:
    first = _candidate("first", 0)
    second = _candidate(
        "second",
        1,
        channel=CodeRetrievalChannel.SPARSE,
        channel_rank=0,
        role=CodeCandidateRole.DEPENDENCY,
    )
    original = "def exact_source():\n    return 1\n"
    output = _build(
        CodeTask.IMPLEMENTATION,
        (first, second),
        (_block(first, original), _block(second, original)),
    )

    assert len(output.retrieval.candidates) == 2
    assert len(output.comprehension.blocks) == 2
    assert {block.citation.entity_id for block in output.comprehension.blocks} == {
        first.entity_id,
        second.entity_id,
    }
    assert output.trace.duplicate_candidate_identities == ()


def test_strict_result_rejects_exact_duplicate_source_block() -> None:
    target = _candidate("target", 0)
    block = _block(target, "def target(): pass\n")

    with pytest.raises(ValueError, match="IDs must be unique"):
        _result((target,), (block, block))


def test_missing_original_source_never_fabricates_a_snippet() -> None:
    target = _candidate("target", 0)
    output = _build(CodeTask.IMPLEMENTATION, (target,), ())

    assert output.comprehension.status is ContextBuildStatus.UNAVAILABLE
    assert output.comprehension.blocks == ()
    target_missing = next(
        item
        for item in output.comprehension.missing_roles
        if item.role is ContextRole.TARGET_SYMBOL
    )
    assert target_missing.status is ContextMissingStatus.UNAVAILABLE
    assert target_missing.candidate_identities == (
        output.retrieval.candidates[0].citation.source_candidate_identity,
    )
    assert output.trace.context_precision.status is ContextMetricStatus.UNAVAILABLE
    assert output.trace.context_recall.status is ContextMetricStatus.UNAVAILABLE
    assert output.trace.locator_correctness.status is ContextMetricStatus.UNAVAILABLE
    assert output.trace.role_coverage.status is ContextMetricStatus.AVAILABLE
    assert output.trace.role_coverage.value == 0.0
    assert output.trace.token_efficiency.value == 0.0


def test_budget_truncates_hard_source_only_at_full_line_and_updates_rendered_locator() -> None:
    target = _candidate(
        "unicode_target",
        0,
        locator=(f"code://{REPOSITORY}@{DIRTY_VERSION}/src/unicode_target.py#L10-L11"),
    )
    first_line = "def café():\n"
    original = first_line + '    return "你好"\n'
    output = _build(
        CodeTask.IMPLEMENTATION,
        (target,),
        (_block(target, original),),
        budget=ContextBudget(
            max_tokens=(len(first_line.encode("utf-8")) + 3) // 4,
            max_characters=len(first_line),
            per_role_block_limit=8,
        ),
    )

    block = output.comprehension.blocks[0]
    assert block.source_text == first_line
    assert block.truncated is True
    assert block.rendered_locator.endswith("#L10-L10")
    assert block.citation.locator.endswith("#L10-L11")
    assert block.source_content_identity != (
        "sha256:" + __import__("hashlib").sha256(first_line.encode()).hexdigest()
    )
    assert output.trace.budget.truncated_block_ids == (block.block_id,)


def test_budget_prunes_non_hard_blocks_and_reports_role_pruning() -> None:
    target = _candidate("target", 0)
    dependency = _candidate(
        "dependency",
        1,
        channel=CodeRetrievalChannel.GRAPH,
        channel_rank=0,
        role=CodeCandidateRole.DEPENDENCY,
        relation=CodeRelationType.IMPORTS,
    )
    target_source = "def target():\n    return 1\n"
    dependency_source = "class Dependency:\n    pass\n"
    target_tokens = (len(target_source.encode("utf-8")) + 3) // 4
    output = _build(
        CodeTask.IMPLEMENTATION,
        (target, dependency),
        (_block(target, target_source), _block(dependency, dependency_source)),
        budget=ContextBudget(
            max_tokens=target_tokens,
            max_characters=len(target_source),
            per_role_block_limit=8,
        ),
    )

    assert len(output.comprehension.blocks) == 1
    assert output.comprehension.blocks[0].citation.entity_id == target.entity_id
    assert output.trace.budget.pruned_block_ids
    missing_imports = next(
        item
        for item in output.comprehension.missing_roles
        if item.role is ContextRole.REQUIRED_IMPORTS_TYPES
    )
    assert missing_imports.status is ContextMissingStatus.PRUNED


@pytest.mark.parametrize(
    ("candidate_kwargs", "scope_kwargs", "error_type"),
    [
        ({"acl_ref": "acl://other"}, {}, ContextBuildScopeError),
        ({"generation": "generation://other"}, {}, ContextBuildScopeError),
        ({"version": "b" * 40}, {}, ContextBuildScopeError),
        (
            {"locator": (f"code://{REPOSITORY}@{'b' * 40}/src/target.py#L1-L2")},
            {},
            ContextBuildLocatorError,
        ),
        (
            {"locator": (f"code://{REPOSITORY}@{DIRTY_VERSION}//absolute.py#L1-L2")},
            {"path_prefixes": ()},
            ContextBuildLocatorError,
        ),
        (
            {"locator": (f"code://{REPOSITORY}@{DIRTY_VERSION}/src/%00bad.py#L1-L2")},
            {},
            ContextBuildLocatorError,
        ),
        (
            {"locator": (f"code://{REPOSITORY}@{DIRTY_VERSION}/%73rc/target.py#L1-L1")},
            {},
            ContextBuildLocatorError,
        ),
        (
            {"locator": (f"code://{REPOSITORY}@{DIRTY_VERSION}/src/target.py#L0-L2")},
            {},
            ContextBuildLocatorError,
        ),
        (
            {"locator": (f"code://{REPOSITORY}@{DIRTY_VERSION}/src/target.py#L1-L1&symbol=target")},
            {},
            ContextBuildLocatorError,
        ),
        (
            {"path": "private/target.py"},
            {"path_prefixes": ("src",)},
            ContextBuildScopeError,
        ),
    ],
)
def test_acl_scope_version_and_locator_mismatches_fail_closed(
    candidate_kwargs: dict[str, object],
    scope_kwargs: dict[str, object],
    error_type: type[ContextBuildScopeError],
) -> None:
    candidate = _candidate("target", 0, **candidate_kwargs)
    scope = _scope(**scope_kwargs)
    allowed_acl_refs = (candidate.acl_ref,)

    with pytest.raises(error_type):
        fusion = _production_fusion(
            CodeTask.IMPLEMENTATION,
            (candidate,),
            (),
            allowed_acl_refs=allowed_acl_refs,
        )
        CodeTaskContextBuilder().build(fusion, scope=scope)


def test_non_exact_version_alignment_fails_closed() -> None:
    candidate = _candidate(
        "target",
        0,
        alignment=CodeVersionAlignment.COMPATIBLE,
    )
    with pytest.raises(ContextBuildScopeError, match="alignment"):
        _build(
            CodeTask.IMPLEMENTATION,
            (candidate,),
            (_block(candidate, "def target():\n    pass\n"),),
        )


@pytest.mark.parametrize(
    "invalid_version",
    [
        "b" * 39,
        "b" * 65,
        "b" * 40 + "+dirty.",
        "b" * 40 + "+dirty.Manifest",
        "b" * 40 + "+dirty.manifest!",
        "b" * 40 + "+dirty.manifest+extra",
        "B" * 40,
        "dirty.manifest",
    ],
)
def test_invalid_clean_and_dirty_version_grammar_fails_closed(
    invalid_version: str,
) -> None:
    with pytest.raises(ValueError, match="stable_version"):
        _scope(version=invalid_version)


@pytest.mark.parametrize("clean_version", ["c" * 40, "d" * 64])
def test_clean_version_grammar_accepts_only_full_sha(clean_version: str) -> None:
    scope = _scope(version=clean_version)
    assert scope.stable_version == clean_version


def test_source_content_exceeding_locator_line_span_fails_closed() -> None:
    target = _candidate(
        "target",
        0,
        locator=f"code://{REPOSITORY}@{DIRTY_VERSION}/src/target.py#L10-L20",
    )
    fusion = _production_fusion(
        CodeTask.IMPLEMENTATION,
        (target,),
        (_block(target, "def target(): pass\n"),),
    )
    with pytest.raises(ContextBuildLocatorError, match="exactly equal"):
        CodeTaskContextBuilder().build(
            fusion,
            scope=_scope(),
        )


def test_unicode_nfc_path_and_one_line_locator_are_canonical() -> None:
    target = _candidate(
        "unicode",
        0,
        path="src/café.py",
        locator=f"code://{REPOSITORY}@{DIRTY_VERSION}/src/café.py#L7-L7",
    )
    output = _build(
        CodeTask.IMPLEMENTATION,
        (target,),
        (_block(target, "def café(): pass\n"),),
    )

    block = output.comprehension.blocks[0]
    assert block.citation.repository_path == "src/café.py"
    assert block.rendered_locator.endswith("#L7-L7")


def test_fusion_trace_profile_and_candidate_are_preserved_without_retrieval() -> None:
    target = _candidate("target", 0)
    fusion = _production_fusion(
        CodeTask.IMPLEMENTATION,
        (target,),
        (_block(target, "def target():\n    return 1\n"),),
    )

    output = CodeTaskContextBuilder().build(fusion, scope=_scope())

    assert output.retrieval.fusion_trace is fusion.trace
    assert output.retrieval.candidates[0].candidate is fusion.result.candidates[0]
    assert output.retrieval.candidates[0].citation.relation_path == target.relation_path
    assert output.trace.source_missing_candidate_roles == ()


def test_bare_strict_result_and_wrong_project_fail_closed() -> None:
    target = _candidate("target", 0)
    block = _block(target, "def target():\n    return 1\n")
    result = _result((target,), (block,))
    fusion = _production_fusion(CodeTask.IMPLEMENTATION, (target,), (block,))

    with pytest.raises(ContextBuildScopeError, match="production"):
        CodeTaskContextBuilder().build(
            result,
            task=CodeTask.IMPLEMENTATION,
            scope=_scope(),
        )
    with pytest.raises(ContextBuildScopeError, match="project"):
        CodeTaskContextBuilder().build(
            fusion,
            scope=_scope(project_id="project://wrong"),
        )


def test_attestation_rejects_result_content_and_authority_tampering() -> None:
    target = _candidate("target", 0)
    block = _block(target, "def target():\n    return 1\n")
    fusion = _production_fusion(CodeTask.IMPLEMENTATION, (target,), (block,))
    forged_block = block.model_copy(update={"content": "forged_body = True\n"})
    forged_result = _result((target,), (forged_block,))

    with pytest.raises(ContextBuildScopeError, match="attestation"):
        CodeTaskContextBuilder().build(
            replace(fusion, result=forged_result),
            scope=_scope(),
        )
    forged_watermark = fusion.result.model_copy(update={"watermark": "watermark://forged"})
    with pytest.raises(ContextBuildScopeError, match="attestation"):
        CodeTaskContextBuilder().build(
            replace(fusion, result=forged_watermark),
            scope=_scope(),
        )

    assert fusion.scope_attestation is not None
    published = fusion.scope_attestation.context_blocks[0]
    forged_content_identity = replace(
        published,
        content_identity="sha256:" + "0" * 64,
    )
    with pytest.raises(ContextBuildScopeError, match="attestation"):
        CodeTaskContextBuilder().build(
            replace(
                fusion,
                scope_attestation=replace(
                    fusion.scope_attestation,
                    context_blocks=(forged_content_identity,),
                ),
            ),
            scope=_scope(),
        )

    forged_metadata = block.model_copy(update={"token_estimate": block.token_estimate + 1})
    with pytest.raises(ContextBuildScopeError, match="attestation"):
        CodeTaskContextBuilder().build(
            replace(
                fusion,
                result=_result((target,), (forged_metadata,)),
            ),
            scope=_scope(),
        )

    moved_locator = f"code://{REPOSITORY}@{DIRTY_VERSION}/src/moved_target.py#L1-L2"
    moved_path = _path("target", locator=moved_locator)
    moved_candidate = target.model_copy(
        update={"relation_path": moved_path, "locator": moved_locator}
    )
    moved_block = block.model_copy(update={"relation_path": moved_path, "locator": moved_locator})
    with pytest.raises(ContextBuildScopeError, match="attestation"):
        CodeTaskContextBuilder().build(
            replace(
                fusion,
                result=_result((moved_candidate,), (moved_block,)),
            ),
            scope=_scope(),
        )

    forged_attestation = replace(
        fusion.scope_attestation,
        allowed_acl_refs=("acl://forged",),
    )
    with pytest.raises(ContextBuildScopeError, match="attestation"):
        CodeTaskContextBuilder().build(
            replace(fusion, scope_attestation=forged_attestation),
            scope=_scope(acl_ref="acl://forged"),
        )


def test_attestation_cannot_be_copied_to_another_production_result() -> None:
    first = _candidate("first", 0)
    second = _candidate("second", 0)
    first_fusion = _production_fusion(
        CodeTask.IMPLEMENTATION,
        (first,),
        (_block(first, "def first():\n    return 1\n"),),
    )
    second_fusion = _production_fusion(
        CodeTask.IMPLEMENTATION,
        (second,),
        (_block(second, "def second():\n    return 2\n"),),
    )

    with pytest.raises(ContextBuildScopeError, match="attestation"):
        CodeTaskContextBuilder().build(
            replace(
                second_fusion,
                scope_attestation=first_fusion.scope_attestation,
            ),
            scope=_scope(),
        )


def test_unknown_unsupported_and_upstream_refused_tasks_have_explicit_refusal() -> None:
    target = _candidate("target", 0)
    result = _result((target,), (_block(target, "def target():\n    pass\n"),))
    production_fusion = _production_fusion(
        CodeTask.IMPLEMENTATION,
        (target,),
        (_block(target, "def target():\n    pass\n"),),
    )
    unsupported_fusion = _production_fusion(
        CodeTask.EXACT_LOCATION,
        (target,),
        (_block(target, "def target():\n    pass\n"),),
    )

    with pytest.raises(ContextBuildRefusalError) as unknown:
        CodeTaskContextBuilder().build(
            production_fusion,
            task="not-a-task",
            scope=_scope(),
        )
    assert unknown.value.refusal.code is ContextRefusalCode.UNKNOWN_TASK

    with pytest.raises(ContextBuildRefusalError) as unsupported:
        CodeTaskContextBuilder().build(
            unsupported_fusion,
            scope=_scope(),
        )
    assert unsupported.value.refusal.code is ContextRefusalCode.UNSUPPORTED_TASK

    fusion = _fusion(CodeTask.IMPACT_ANALYSIS, result)
    refused_trace = CodeSourceFusionTrace(
        pipeline_version=fusion.trace.pipeline_version,
        registry_version=fusion.trace.registry_version,
        profile_key=fusion.trace.profile_key,
        profile_version=fusion.trace.profile_version,
        task=fusion.trace.task,
        rewrite=fusion.trace.rewrite,
        events=fusion.trace.events,
        required_roles=fusion.trace.required_roles,
        missing_roles=fusion.trace.required_roles,
        requested_k=1,
        adaptive_k=1,
        refused=True,
    )
    refused_fusion = CodeSourceFusionSearchResult(
        result=result,
        trace=refused_trace,
        profile=fusion.profile,
    )
    with pytest.raises(ContextBuildRefusalError) as upstream:
        CodeTaskContextBuilder().build(refused_fusion, scope=_scope())
    assert upstream.value.refusal.code is ContextRefusalCode.UPSTREAM_REFUSAL


def test_dirty_version_citation_path_and_metric_trace_are_exact() -> None:
    target = _candidate("target", 0)
    distractor = _candidate(
        "distractor",
        1,
        channel=CodeRetrievalChannel.SPARSE,
        channel_rank=0,
        role=CodeCandidateRole.BACKGROUND,
        entity_type="Repository",
        line_count=1,
    )
    output = _build(
        CodeTask.IMPLEMENTATION,
        (target, distractor),
        (
            _block(target, "def target():\n    return 1\n"),
            _block(distractor, "# repository background\n"),
        ),
    )

    version = output.comprehension.version
    citation = output.comprehension.blocks[0].citation
    assert version.dirty is True
    assert version.base_version == BASE_VERSION
    assert version.dirty_manifest == "manifest-7"
    assert "not equivalent to a Git commit" in version.display
    assert citation.stable_version == DIRTY_VERSION
    assert citation.source_generation == GENERATION
    assert citation.repository_path == "src/target.py"
    assert citation.locator == target.locator
    assert citation.source_candidate_identity.startswith("sha256:")
    assert output.trace.distractor_rate == 0.5
    assert output.trace.context_precision.status is ContextMetricStatus.UNAVAILABLE
    assert output.trace.context_recall.status is ContextMetricStatus.UNAVAILABLE
    assert output.trace.locator_correctness.value == 1.0
    assert output.trace.answer_utility.status is ContextMetricStatus.UNAVAILABLE


def test_builder_is_deterministic_idempotent_and_contracts_are_frozen() -> None:
    target = _candidate("target", 0)
    fusion = _production_fusion(
        CodeTask.IMPLEMENTATION,
        (target,),
        (_block(target, "def target():\n    return 1\n"),),
    )
    builder = CodeTaskContextBuilder()

    first = builder.build(
        fusion,
        scope=_scope(),
    )
    second = builder.build(
        fusion,
        scope=_scope(),
    )

    assert first == second
    assert first.canonical_json_bytes() == second.canonical_json_bytes()
    assert first.canonical_sha256() == second.canonical_sha256()
    with pytest.raises(FrozenInstanceError):
        first.comprehension.blocks = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        first.comprehension.blocks[0].source_text = "forged"  # type: ignore[misc]


def test_strict_role_block_rejects_summary_style_source_replacement() -> None:
    target = _candidate("target", 0)
    output = _build(
        CodeTask.IMPLEMENTATION,
        (target,),
        (_block(target, "def target():\n    return 1\n"),),
    )
    block = output.comprehension.blocks[0]

    summary = "summary"
    with pytest.raises(ValueError, match="content identity"):
        ContextRoleBlock(
            block_id=block.block_id,
            parent_entity_id=block.parent_entity_id,
            source_block_id=block.source_block_id,
            source_content_identity=block.source_content_identity,
            source_text=summary,
            rendered_locator=block.rendered_locator,
            roles=block.roles,
            retrieval_unit_ids=block.retrieval_unit_ids,
            citation=block.citation,
            truncated=False,
            original_character_count=len(summary),
            rendered_character_count=len(summary),
            token_estimate=(len(summary.encode("utf-8")) + 3) // 4,
            rendered_line_count=1,
        )
