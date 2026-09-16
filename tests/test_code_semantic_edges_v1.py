from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace

import pytest

from evidence_rag.rag.sources.code.contracts import (
    CodeDerivation,
    CodeFactStatus,
    CodeRelationType,
    CodeReviewStatus,
)
from evidence_rag.rag.sources.code.graph_v2 import CodeGraphEntityType
from evidence_rag.rag.sources.code.scip_v1 import (
    SCIP_CONSUMER_VERSION,
    ScipConsumeResult,
    ScipDiagnostic,
    ScipDocument,
    ScipEntityRef,
    ScipFallbackTrace,
    ScipLink,
    ScipLinkedDocument,
    ScipLinkedOccurrence,
    ScipLinkedRelationship,
    ScipLinkStatus,
    ScipOccurrence,
    ScipOccurrenceKind,
    ScipPosition,
    ScipProvenance,
    ScipRange,
    ScipRawObject,
    ScipRelationship,
    ScipScope,
    ScipStatus,
)
from evidence_rag.rag.sources.code.semantic_edges_v1 import (
    SCIP_EXPLICIT_CALL_ROLE,
    SCIP_EXPLICIT_OVERRIDE_ROLE,
    SEMANTIC_EDGE_LABEL_PROTOCOL_VERSION,
    ConservativeSemanticEdge,
    SemanticEdgeDiagnosticCode,
    SemanticEdgeEndpoint,
    SemanticEdgeEvaluationStatus,
    SemanticEdgePrecisionLabel,
    SemanticEdgePrecisionVerdict,
    SemanticEdgeScope,
    SemanticEdgeSupportStatus,
    SemanticEdgeTreatmentResult,
    SemanticEdgeTreatmentStatus,
    SemanticScipStatus,
    TreeSitterConservativeEdge,
    evaluate_semantic_edges,
    sample_semantic_edges,
    treat_python_semantic_edges,
)

CALLER = "scip-python python example 1.0.0 src/app.py/Caller#run()."
CALLEE = "scip-python python example 1.0.0 src/app.py/helper()."
OTHER = "scip-python python example 1.0.0 src/app.py/other()."
EXTERNAL = "scip-python python requests 2.32.0 requests/api.py/get()."
LOCAL = "local 0"


def _scope(**updates: str) -> SemanticEdgeScope:
    values = {
        "project_id": "project",
        "repository_id": "repo",
        "generation_id": "generation",
        "stable_version": "commit-abc",
        "acl_ref": "project:project",
    }
    values.update(updates)
    return SemanticEdgeScope(**values)


def _scip_scope(scope: SemanticEdgeScope) -> ScipScope:
    return ScipScope(
        project_id=scope.project_id,
        repository_id=scope.repository_id,
        generation_id=scope.generation_id,
        acl_ref=scope.acl_ref,
    )


def _entity(
    entity_id: str,
    entity_type: str,
    *,
    scope: SemanticEdgeScope,
    symbol: str = "",
    path: str = "src/app.py",
    **updates: str,
) -> ScipEntityRef:
    values = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "project_id": scope.project_id,
        "repository_id": scope.repository_id,
        "generation_id": scope.generation_id,
        "acl_ref": scope.acl_ref,
        "relative_path": path,
        "scip_symbol": symbol,
    }
    values.update(updates)
    return ScipEntityRef(**values)  # type: ignore[arg-type]


def _link(entity: ScipEntityRef) -> ScipLink:
    return ScipLink(status=ScipLinkStatus.RESOLVED, entity=entity)


def _occurrence(
    symbol: str,
    *,
    roles: int = 0,
    kind: ScipOccurrenceKind = ScipOccurrenceKind.REFERENCE,
    link: ScipLink | None = None,
    path: str = "src/app.py",
    line: int = 4,
) -> ScipLinkedOccurrence:
    return ScipLinkedOccurrence(
        occurrence=ScipOccurrence(
            relative_path=path,
            source_range=ScipRange(
                start=ScipPosition(line, 5),
                end=ScipPosition(line, 11),
            ),
            symbol=symbol,
            symbol_roles=roles,
            kind=kind,
            is_local=symbol.startswith("local "),
        ),
        symbol_link=link or ScipLink(status=ScipLinkStatus.UNRESOLVED),
    )


def _relationship(
    *,
    scope: SemanticEdgeScope,
    source_symbol: str = CALLER,
    target_symbol: str = CALLEE,
    source_id: str = "symbol:caller",
    target_id: str = "symbol:callee",
    reference: bool = False,
    implementation: bool = False,
    definition: bool = False,
    source_updates: dict[str, str] | None = None,
    target_updates: dict[str, str] | None = None,
    source_link: ScipLink | None = None,
    target_link: ScipLink | None = None,
) -> ScipLinkedRelationship:
    source = _entity(
        source_id,
        "CodeSymbol",
        scope=scope,
        symbol=source_symbol,
        **(source_updates or {}),
    )
    target = _entity(
        target_id,
        "CodeSymbol",
        scope=scope,
        symbol=target_symbol,
        **(target_updates or {}),
    )
    return ScipLinkedRelationship(
        source_symbol=source_symbol,
        target_symbol=target_symbol,
        relationship=ScipRelationship(
            symbol=target_symbol,
            is_reference=reference,
            is_implementation=implementation,
            is_definition=definition,
        ),
        source_link=source_link or _link(source),
        target_link=target_link or _link(target),
    )


def _result(
    *,
    scope: SemanticEdgeScope,
    status: ScipStatus = ScipStatus.COMPLETE,
    language: str = "python",
    occurrences: tuple[ScipLinkedOccurrence, ...] = (),
    relationships: tuple[ScipLinkedRelationship, ...] = (),
    diagnostics: tuple[ScipDiagnostic, ...] = (),
    provenance: bool = True,
    include_document: bool = True,
) -> ScipConsumeResult:
    file_entity = _entity("file:app", "FileVersion", scope=scope)
    document = ScipDocument(
        relative_path="src/app.py",
        language=language,
        position_encoding=1,
        occurrences=tuple(item.occurrence for item in occurrences),
        symbols=(),
    )
    return ScipConsumeResult(
        status=status,
        semantic_ready=False,
        documents=(
            (ScipLinkedDocument(document=document, file_link=_link(file_entity)),)
            if include_document
            else ()
        ),
        occurrences=occurrences,
        external_symbols=(),
        relationships=relationships,
        diagnostics=diagnostics,
        provenance=(
            ScipProvenance(
                derivation="scip",
                consumer_version=SCIP_CONSUMER_VERSION,
                protocol_version=1,
                indexer_name="scip-python",
                indexer_version="0.6.8",
                raw_object=ScipRawObject(
                    raw_object_id="raw:scip",
                    content_sha256=hashlib.sha256(b"index.scip").hexdigest(),
                    byte_size=10,
                    source_name="index.scip",
                ),
            )
            if provenance
            else None
        ),
        fallback=ScipFallbackTrace(
            attempted=status is ScipStatus.PARTIAL,
            engine="tree-sitter",
            outcome="delegated" if status is ScipStatus.PARTIAL else "not_used",
            reason="fixture",
        ),
    )


def _endpoint(
    entity_id: str,
    entity_type: CodeGraphEntityType,
    *,
    scope: SemanticEdgeScope,
    locator: str | None = None,
    **updates: str,
) -> SemanticEdgeEndpoint:
    values = {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "project_id": scope.project_id,
        "repository_id": scope.repository_id,
        "generation_id": scope.generation_id,
        "stable_version": scope.stable_version,
        "acl_ref": scope.acl_ref,
        "locator": (
            locator
            if locator is not None
            else f"code://repo@commit-abc/src/app.py#entity={entity_id}"
        ),
    }
    values.update(updates)
    return SemanticEdgeEndpoint(**values)  # type: ignore[arg-type]


def _tree_edge(
    *,
    scope: SemanticEdgeScope,
    edge_type: CodeRelationType = CodeRelationType.REFERENCES,
    source: SemanticEdgeEndpoint | None = None,
    target: SemanticEdgeEndpoint | None = None,
    confidence: float = 0.95,
    evidence_locators: tuple[str, ...] = ("code://repo@commit-abc/src/app.py#L4C5-L4C11",),
) -> TreeSitterConservativeEdge:
    return TreeSitterConservativeEdge(
        edge_type=edge_type,
        source=source or _endpoint("file:app", CodeGraphEntityType.FILE_VERSION, scope=scope),
        target=target or _endpoint("symbol:callee", CodeGraphEntityType.CODE_SYMBOL, scope=scope),
        confidence=confidence,
        evidence_locators=evidence_locators,
        parser_version="tree-sitter-python-v1",
        resolver_version="conservative-resolver-v1",
    )


def _edge_types(result: SemanticEdgeTreatmentResult) -> list[CodeRelationType]:
    return [edge.edge_type for edge in result.edges]


def test_reliable_non_definition_occurrence_maps_to_references() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    result = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(_occurrence(CALLEE, link=_link(target)),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert result.status is SemanticEdgeTreatmentStatus.COMPLETE
    assert result.support_status is SemanticEdgeSupportStatus.SUPPORTED
    assert result.semantic_ready is True
    assert _edge_types(result) == [CodeRelationType.REFERENCES]
    edge = result.edges[0]
    assert edge.source.entity_type is CodeGraphEntityType.FILE_VERSION
    assert edge.target.entity_type is CodeGraphEntityType.CODE_SYMBOL
    assert edge.derivations == (CodeDerivation.SCIP,)
    assert edge.confidence == 1.0
    assert edge.review_status is CodeReviewStatus.MACHINE_CONFIRMED
    assert edge.fact_status is CodeFactStatus.MACHINE_CONFIRMED


def test_only_explicit_call_role_plus_one_enclosing_relationship_maps_calls() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    result = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(
                _occurrence(
                    CALLEE,
                    roles=SCIP_EXPLICIT_CALL_ROLE,
                    link=_link(target),
                ),
            ),
            relationships=(_relationship(scope=scope, reference=True),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert _edge_types(result) == [CodeRelationType.CALLS]
    assert result.edges[0].source.entity_id == "symbol:caller"
    assert result.edges[0].target.entity_id == "symbol:callee"


def test_ordinary_reference_and_definition_never_impersonate_calls() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    ordinary = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(_occurrence(CALLEE, link=_link(target)),),
            relationships=(_relationship(scope=scope, reference=True),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )
    definition = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(
                _occurrence(
                    CALLEE,
                    roles=SCIP_EXPLICIT_CALL_ROLE | 1,
                    kind=ScipOccurrenceKind.DEFINITION,
                    link=_link(target),
                ),
            ),
            relationships=(_relationship(scope=scope, reference=True),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert CodeRelationType.CALLS not in _edge_types(ordinary)
    assert CodeRelationType.CALLS not in _edge_types(definition)
    assert _edge_types(ordinary) == [
        CodeRelationType.REFERENCES,
        CodeRelationType.REFERENCES,
    ]
    assert not definition.edges


def test_call_role_without_unique_enclosing_symbol_falls_back_to_reference() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    missing = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(
                _occurrence(
                    CALLEE,
                    roles=SCIP_EXPLICIT_CALL_ROLE,
                    link=_link(target),
                ),
            ),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )
    ambiguous = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(
                _occurrence(
                    CALLEE,
                    roles=SCIP_EXPLICIT_CALL_ROLE,
                    link=_link(target),
                ),
            ),
            relationships=(
                _relationship(scope=scope, reference=True),
                _relationship(
                    scope=scope,
                    reference=True,
                    source_symbol=OTHER,
                    source_id="symbol:other",
                ),
            ),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert _edge_types(missing) == [CodeRelationType.REFERENCES]
    assert {item.code for item in missing.diagnostics} >= {
        SemanticEdgeDiagnosticCode.CALL_EVIDENCE_MISSING
    }
    assert CodeRelationType.CALLS not in _edge_types(ambiguous)
    assert {item.code for item in ambiguous.diagnostics} >= {
        SemanticEdgeDiagnosticCode.CALL_EVIDENCE_AMBIGUOUS
    }


def test_implementation_and_explicit_override_evidence_are_distinct() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    relationship_only = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            relationships=(_relationship(scope=scope, implementation=True),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )
    implementation = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(_occurrence(CALLEE, link=_link(target)),),
            relationships=(_relationship(scope=scope, implementation=True),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )
    override = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(
                _occurrence(
                    CALLEE,
                    roles=SCIP_EXPLICIT_OVERRIDE_ROLE,
                    link=_link(target),
                ),
            ),
            relationships=(_relationship(scope=scope, implementation=True),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert not relationship_only.edges
    assert SemanticEdgeDiagnosticCode.RELATIONSHIP_EVIDENCE_MISSING in {
        item.code for item in relationship_only.diagnostics
    }
    assert CodeRelationType.IMPLEMENTS in _edge_types(implementation)
    assert CodeRelationType.OVERRIDES not in _edge_types(implementation)
    assert CodeRelationType.IMPLEMENTS in _edge_types(override)
    assert CodeRelationType.OVERRIDES in _edge_types(override)
    assert CodeRelationType.CALLS not in _edge_types(override)
    override_edge = next(
        edge for edge in override.edges if edge.edge_type is CodeRelationType.OVERRIDES
    )
    assert len(override_edge.evidence_locators) == 2


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (ScipLinkStatus.UNRESOLVED, ScipOccurrenceKind.REFERENCE),
        (ScipLinkStatus.AMBIGUOUS, ScipOccurrenceKind.REFERENCE),
        (ScipLinkStatus.EXTERNAL, ScipOccurrenceKind.EXTERNAL),
        (ScipLinkStatus.LOCAL, ScipOccurrenceKind.REFERENCE),
    ],
)
def test_non_resolved_occurrence_never_authorizes_semantic_relationship_edges(
    status: ScipLinkStatus,
    kind: ScipOccurrenceKind,
) -> None:
    scope = _scope()
    candidate_ids = ("symbol:a", "symbol:b") if status is ScipLinkStatus.AMBIGUOUS else ()
    linked_occurrence = _occurrence(
        LOCAL if status is ScipLinkStatus.LOCAL else CALLEE,
        roles=SCIP_EXPLICIT_CALL_ROLE | SCIP_EXPLICIT_OVERRIDE_ROLE,
        kind=kind,
        link=ScipLink(status=status, candidate_ids=candidate_ids),
    )
    relationship = _relationship(
        scope=scope,
        target_symbol=LOCAL if status is ScipLinkStatus.LOCAL else CALLEE,
        reference=True,
        implementation=True,
    )
    result = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            status=ScipStatus.PARTIAL,
            occurrences=(linked_occurrence,),
            relationships=(relationship,),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert not {
        CodeRelationType.CALLS,
        CodeRelationType.IMPLEMENTS,
        CodeRelationType.OVERRIDES,
    }.intersection(_edge_types(result))
    assert result.semantic_ready is False
    assert (
        SemanticEdgeDiagnosticCode.RELATIONSHIP_EVIDENCE_MISSING
        in {item.code for item in result.diagnostics}
        or status is ScipLinkStatus.LOCAL
    )


def test_unresolved_enclosing_source_or_target_mismatch_cannot_authorize_override() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    occurrence = _occurrence(
        CALLEE,
        roles=SCIP_EXPLICIT_OVERRIDE_ROLE,
        link=_link(target),
    )
    missing_source = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            status=ScipStatus.PARTIAL,
            occurrences=(occurrence,),
            relationships=(
                _relationship(
                    scope=scope,
                    implementation=True,
                    source_link=ScipLink(status=ScipLinkStatus.UNRESOLVED),
                ),
            ),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )
    mismatched_target = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(occurrence,),
            relationships=(
                _relationship(
                    scope=scope,
                    implementation=True,
                    target_id="symbol:other",
                ),
            ),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    for result in (missing_source, mismatched_target):
        assert CodeRelationType.OVERRIDES not in _edge_types(result)
        assert CodeRelationType.IMPLEMENTS not in _edge_types(result)
        assert _edge_types(result) == [CodeRelationType.REFERENCES]


@pytest.mark.parametrize(
    ("status", "kind", "symbol"),
    [
        ("external", ScipOccurrenceKind.EXTERNAL, EXTERNAL),
        ("local", ScipOccurrenceKind.REFERENCE, LOCAL),
        ("unresolved", ScipOccurrenceKind.REFERENCE, CALLEE),
        ("ambiguous", ScipOccurrenceKind.REFERENCE, CALLEE),
    ],
)
def test_external_local_unresolved_and_ambiguous_do_not_become_internal_edges(
    status: str,
    kind: ScipOccurrenceKind,
    symbol: str,
) -> None:
    scope = _scope()
    link = ScipLink(
        status=ScipLinkStatus(status),
        candidate_ids=("symbol:a", "symbol:b") if status == "ambiguous" else (),
    )
    result = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            status=ScipStatus.PARTIAL,
            occurrences=(_occurrence(symbol, kind=kind, link=link),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert not result.edges
    assert result.semantic_ready is False
    expected = {
        "external": SemanticEdgeDiagnosticCode.LINK_EXTERNAL,
        "local": SemanticEdgeDiagnosticCode.LINK_LOCAL,
        "unresolved": SemanticEdgeDiagnosticCode.LINK_UNRESOLVED,
        "ambiguous": SemanticEdgeDiagnosticCode.LINK_AMBIGUOUS,
    }[status]
    assert expected in {item.code for item in result.diagnostics}


def test_external_or_local_classification_wins_over_a_forged_resolved_link() -> None:
    scope = _scope()
    forged = _link(_entity("symbol:forged", "CodeSymbol", scope=scope, symbol=EXTERNAL))
    result = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(
                _occurrence(EXTERNAL, kind=ScipOccurrenceKind.EXTERNAL, link=forged),
                _occurrence(LOCAL, link=forged, line=8),
            ),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )
    assert not result.edges
    assert {item.code for item in result.diagnostics} >= {
        SemanticEdgeDiagnosticCode.LINK_EXTERNAL,
        SemanticEdgeDiagnosticCode.LINK_LOCAL,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "other-project"),
        ("repository_id", "other-repo"),
        ("generation_id", "other-generation"),
        ("acl_ref", "project:other"),
    ],
)
def test_scip_endpoint_scope_mismatch_fails_closed(field: str, value: str) -> None:
    scope = _scope()
    target = _entity(
        "symbol:callee",
        "CodeSymbol",
        scope=scope,
        symbol=CALLEE,
        **{field: value},
    )
    result = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(_occurrence(CALLEE, link=_link(target)),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert not result.edges
    assert SemanticEdgeDiagnosticCode.ENDPOINT_SCOPE_MISMATCH in {
        item.code for item in result.diagnostics
    }


@pytest.mark.parametrize(
    ("updates", "code"),
    [
        (
            {"acl_ref": "project:other"},
            SemanticEdgeDiagnosticCode.ENDPOINT_SCOPE_MISMATCH,
        ),
        (
            {"generation_id": "other-generation"},
            SemanticEdgeDiagnosticCode.ENDPOINT_SCOPE_MISMATCH,
        ),
        (
            {"stable_version": "other-version"},
            SemanticEdgeDiagnosticCode.ENDPOINT_VERSION_MISMATCH,
        ),
    ],
)
def test_tree_endpoint_acl_generation_and_version_fail_closed(
    updates: dict[str, str],
    code: SemanticEdgeDiagnosticCode,
) -> None:
    scope = _scope()
    bad_source = _endpoint(
        "file:app",
        CodeGraphEntityType.FILE_VERSION,
        scope=scope,
        **updates,
    )
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(_tree_edge(scope=scope, source=bad_source),),
        scope=scope,
    )

    assert not result.edges
    assert code in {item.code for item in result.diagnostics}


def test_endpoint_type_registry_and_locator_fail_closed() -> None:
    scope = _scope()
    bad_type = _tree_edge(
        scope=scope,
        edge_type=CodeRelationType.CALLS,
        source=_endpoint(
            "file:app",
            CodeGraphEntityType.FILE_VERSION,
            scope=scope,
        ),
    )
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(bad_type,),
        scope=scope,
    )
    assert not result.edges
    assert SemanticEdgeDiagnosticCode.EDGE_REGISTRY_REJECTED in {
        item.code for item in result.diagnostics
    }

    with pytest.raises(ValueError, match="locator"):
        _endpoint(
            "symbol:callee",
            CodeGraphEntityType.CODE_SYMBOL,
            scope=scope,
            locator="",
        )
    with pytest.raises(ValueError, match="control"):
        _endpoint(
            "symbol:callee",
            CodeGraphEntityType.CODE_SYMBOL,
            scope=scope,
            locator="code://repo/path\nsecret",
        )


def test_low_confidence_tree_edge_is_diagnostic_not_graph_noise() -> None:
    scope = _scope()
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(_tree_edge(scope=scope, confidence=0.79),),
        scope=scope,
    )
    assert not result.edges
    assert SemanticEdgeDiagnosticCode.LOW_CONFIDENCE in {item.code for item in result.diagnostics}


def test_same_canonical_edge_merges_provenance_locators_without_confidence_inflation() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    scip = _result(
        scope=scope,
        occurrences=(_occurrence(CALLEE, link=_link(target)),),
    )
    tree = _tree_edge(
        scope=scope,
        confidence=0.9,
        evidence_locators=("code://repo@commit-abc/src/app.py#tree-ref",),
    )
    result = treat_python_semantic_edges(
        scip_result=scip,
        tree_sitter_edges=(tree,),
        scope=scope,
    )

    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.derivations == (CodeDerivation.SCIP, CodeDerivation.TREE_SITTER)
    assert len(edge.provenances) == 2
    assert len(edge.evidence_locators) == 2
    assert edge.confidence == 1.0
    assert result.trace.tree_sitter_retained_count == 1
    assert result.trace.scip_retained_count == 1


def test_conflicting_scip_call_does_not_overwrite_tree_sitter_reference() -> None:
    scope = _scope()
    source = _endpoint("symbol:caller", CodeGraphEntityType.CODE_SYMBOL, scope=scope)
    target_endpoint = _endpoint(
        "symbol:callee",
        CodeGraphEntityType.CODE_SYMBOL,
        scope=scope,
    )
    tree = _tree_edge(
        scope=scope,
        edge_type=CodeRelationType.REFERENCES,
        source=source,
        target=target_endpoint,
    )
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    scip = _result(
        scope=scope,
        occurrences=(
            _occurrence(
                CALLEE,
                roles=SCIP_EXPLICIT_CALL_ROLE,
                link=_link(target),
            ),
        ),
        relationships=(_relationship(scope=scope, reference=True),),
    )
    result = treat_python_semantic_edges(
        scip_result=scip,
        tree_sitter_edges=(tree,),
        scope=scope,
    )

    assert _edge_types(result) == [CodeRelationType.REFERENCES]
    assert result.edges[0].derivations == (CodeDerivation.TREE_SITTER,)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].retained_tree_edge_ids == (result.edges[0].edge_id,)
    assert SemanticEdgeDiagnosticCode.TREE_SITTER_SCIP_CONFLICT in {
        item.code for item in result.diagnostics
    }
    assert result.semantic_ready is False


def test_order_independent_hash_and_idempotent_treatment() -> None:
    scope = _scope()
    callee = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    other = _entity("symbol:other", "CodeSymbol", scope=scope, symbol=OTHER)
    occurrences = (
        _occurrence(CALLEE, link=_link(callee), line=4),
        _occurrence(OTHER, link=_link(other), line=8),
    )
    forward = treat_python_semantic_edges(
        scip_result=_result(scope=scope, occurrences=occurrences),
        tree_sitter_edges=(),
        scope=scope,
    )
    reverse = treat_python_semantic_edges(
        scip_result=_result(scope=scope, occurrences=tuple(reversed(occurrences))),
        tree_sitter_edges=(),
        scope=scope,
    )
    repeated = treat_python_semantic_edges(
        scip_result=_result(scope=scope, occurrences=occurrences),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert forward == reverse == repeated
    assert forward.result_hash == reverse.result_hash
    assert [edge.edge_id for edge in forward.edges] == [edge.edge_id for edge in reverse.edges]
    with pytest.raises(FrozenInstanceError):
        forward.semantic_ready = False  # type: ignore[misc]


def test_scip_unavailable_and_partial_preserve_tree_sitter_fallback_truthfully() -> None:
    scope = _scope()
    tree = _tree_edge(scope=scope)
    unavailable = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(tree,),
        scope=scope,
    )
    partial = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            status=ScipStatus.PARTIAL,
            diagnostics=(ScipDiagnostic(code="unresolved", message="one unresolved symbol"),),
        ),
        tree_sitter_edges=(tree,),
        scope=scope,
    )

    assert unavailable.status is SemanticEdgeTreatmentStatus.PARTIAL
    assert unavailable.trace.scip_status is SemanticScipStatus.UNAVAILABLE
    assert unavailable.trace.fallback_used is True
    assert unavailable.trace.reason == "scip_unavailable_tree_sitter_preserved"
    assert unavailable.semantic_ready is False
    assert _edge_types(unavailable) == [CodeRelationType.REFERENCES]

    assert partial.trace.scip_status is SemanticScipStatus.PARTIAL
    assert partial.trace.scip_diagnostic_codes == ("unresolved",)
    assert partial.trace.fallback_used is True
    assert partial.trace.reason == "scip_partial_tree_sitter_preserved"
    assert partial.semantic_ready is False


def test_partial_scip_is_semantic_ready_only_when_a_legal_scip_edge_survives() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    valid = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            status=ScipStatus.PARTIAL,
            occurrences=(_occurrence(CALLEE, link=_link(target)),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )
    invalid = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            status=ScipStatus.PARTIAL,
            occurrences=(_occurrence(CALLEE),),
        ),
        tree_sitter_edges=(),
        scope=scope,
    )

    assert valid.semantic_ready is True
    assert valid.trace.scip_retained_count == 1
    assert invalid.semantic_ready is False
    assert invalid.trace.scip_retained_count == 0


@pytest.mark.parametrize("language", ["javascript", "js", "typescript", "ts", "tsx"])
def test_javascript_and_typescript_are_explicitly_unavailable(language: str) -> None:
    scope = _scope()
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(_tree_edge(scope=scope),),
        scope=scope,
        language=language,
    )

    assert result.status is SemanticEdgeTreatmentStatus.UNAVAILABLE
    assert result.support_status is SemanticEdgeSupportStatus.UNAVAILABLE
    assert not result.edges
    assert result.trace.tree_sitter_input_count == 1
    assert result.trace.tree_sitter_retained_count == 0
    assert result.trace.fallback_used is False
    assert SemanticEdgeDiagnosticCode.LANGUAGE_UNAVAILABLE in {
        item.code for item in result.diagnostics
    }


def test_scip_document_language_cannot_be_mislabeled_as_python() -> None:
    scope = _scope()
    result = treat_python_semantic_edges(
        scip_result=_result(scope=scope, language="typescript"),
        tree_sitter_edges=(),
        scope=scope,
        language="python",
    )
    assert result.support_status is SemanticEdgeSupportStatus.UNAVAILABLE
    assert result.language == "typescript"


def test_missing_scip_provenance_rejects_linked_semantic_output() -> None:
    scope = _scope()
    target = _entity("symbol:callee", "CodeSymbol", scope=scope, symbol=CALLEE)
    result = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            occurrences=(_occurrence(CALLEE, link=_link(target)),),
            provenance=False,
        ),
        tree_sitter_edges=(),
        scope=scope,
    )
    assert not result.edges
    assert result.semantic_ready is False
    assert SemanticEdgeDiagnosticCode.SCIP_PROVENANCE_MISSING in {
        item.code for item in result.diagnostics
    }


def test_diagnostic_sampling_keeps_exact_counts() -> None:
    scope = _scope()
    occurrences = tuple(
        _occurrence(
            f"local {index}",
            link=ScipLink(status=ScipLinkStatus.LOCAL),
            line=index + 1,
        )
        for index in range(6)
    )
    result = treat_python_semantic_edges(
        scip_result=_result(
            scope=scope,
            status=ScipStatus.PARTIAL,
            occurrences=occurrences,
        ),
        tree_sitter_edges=(),
        scope=scope,
        diagnostic_limit=2,
    )
    counts = {item.code: item.count for item in result.diagnostic_counts}
    assert counts[SemanticEdgeDiagnosticCode.LINK_LOCAL] == 6
    assert len(result.diagnostics) == 2
    assert result.diagnostics_truncated == 5


def test_evaluation_is_unavailable_without_real_labels_and_does_not_fake_200() -> None:
    scope = _scope()
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(_tree_edge(scope=scope),),
        scope=scope,
    )
    evaluation = evaluate_semantic_edges(
        result,
        eligible_edge_count=2,
        unresolved_before=10,
        unresolved_after=7,
    )

    assert evaluation.status is SemanticEdgeEvaluationStatus.UNAVAILABLE
    assert evaluation.reason == "quality_labels_unavailable"
    assert evaluation.sample_size_required == 1
    assert evaluation.sample_size_target == 200
    assert evaluation.labeled_sample_count == 0
    assert evaluation.precision is None
    assert evaluation.edge_precision is None
    assert evaluation.coverage is None
    assert evaluation.unresolved_reduction_count is None
    assert evaluation.unresolved_reduction_rate is None
    assert evaluation.unresolved_reduction is None
    assert evaluation.graph_noise_count is None
    assert evaluation.graph_noise_rate is None
    assert evaluation.harmful_count is None
    assert evaluation.harmful_rate is None
    assert evaluation.structural_counts.legal_edge_count == 1
    assert evaluation.structural_counts.conflict_count == 0
    assert evaluation.structural_counts.eligible_edge_count == 2
    assert evaluation.structural_counts.unresolved_before == 10
    assert evaluation.structural_counts.unresolved_after == 7
    assert evaluation.structural_counts.unresolved_delta == -3
    assert evaluation.structural_counts.classification == "non_quality_diagnostics"


def test_evaluation_protocol_reports_precision_coverage_unresolved_and_noise() -> None:
    scope = _scope()
    source = _endpoint("file:app", CodeGraphEntityType.FILE_VERSION, scope=scope)
    first = _tree_edge(scope=scope, source=source)
    second = _tree_edge(
        scope=scope,
        source=source,
        target=_endpoint(
            "symbol:other",
            CodeGraphEntityType.CODE_SYMBOL,
            scope=scope,
        ),
        evidence_locators=("code://repo@commit-abc/src/app.py#L8C5-L8C10",),
    )
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(first, second),
        scope=scope,
    )
    sample = sample_semantic_edges(result)
    labels = (
        SemanticEdgePrecisionLabel(
            edge_id=sample[0].edge_id,
            verdict=SemanticEdgePrecisionVerdict.CORRECT,
            reviewer="reviewer-1",
        ),
        SemanticEdgePrecisionLabel(
            edge_id=sample[1].edge_id,
            verdict=SemanticEdgePrecisionVerdict.GRAPH_NOISE,
            reviewer="reviewer-1",
        ),
    )
    evaluation = evaluate_semantic_edges(
        result,
        labels=reversed(labels),
        eligible_edge_count=4,
        unresolved_before=8,
        unresolved_after=6,
    )

    assert evaluation.status is SemanticEdgeEvaluationStatus.PROVISIONAL
    assert evaluation.reason == "all_fixture_edges_labeled_below_200_provisional"
    assert evaluation.protocol_version == SEMANTIC_EDGE_LABEL_PROTOCOL_VERSION
    assert evaluation.sample_count == 2
    assert evaluation.precision == 0.5
    assert evaluation.correct_count == 1
    assert evaluation.graph_noise_count == 1
    assert evaluation.harmful_count == 1
    assert evaluation.coverage == 0.25
    assert evaluation.unresolved_reduction_count == 2
    assert evaluation.unresolved_reduction_rate == 0.25


def test_partial_real_labels_are_provisional_and_never_pass_the_200_gate() -> None:
    scope = _scope()
    source = _endpoint("file:app", CodeGraphEntityType.FILE_VERSION, scope=scope)
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(
            _tree_edge(scope=scope, source=source),
            _tree_edge(
                scope=scope,
                source=source,
                target=_endpoint(
                    "symbol:other",
                    CodeGraphEntityType.CODE_SYMBOL,
                    scope=scope,
                ),
                evidence_locators=("code://repo@commit-abc/src/app.py#L8C5-L8C10",),
            ),
        ),
        scope=scope,
    )
    label = SemanticEdgePrecisionLabel(
        edge_id=sample_semantic_edges(result)[0].edge_id,
        verdict=SemanticEdgePrecisionVerdict.CORRECT,
        reviewer="reviewer-1",
    )
    evaluation = evaluate_semantic_edges(
        result,
        labels=(label,),
        eligible_edge_count=4,
        unresolved_before=8,
        unresolved_after=6,
    )

    assert evaluation.status is SemanticEdgeEvaluationStatus.PROVISIONAL
    assert evaluation.reason == "partial_real_label_sample_provisional"
    assert evaluation.sample_count == 1
    assert evaluation.precision == 1.0
    assert evaluation.coverage == 0.25
    assert evaluation.graph_noise_count == 0
    assert evaluation.harmful_count == 0


def test_two_hundred_real_labels_make_quality_metrics_available() -> None:
    scope = _scope()
    source = _endpoint("file:app", CodeGraphEntityType.FILE_VERSION, scope=scope)
    tree_edges = tuple(
        _tree_edge(
            scope=scope,
            source=source,
            target=_endpoint(
                f"symbol:target-{index}",
                CodeGraphEntityType.CODE_SYMBOL,
                scope=scope,
            ),
            evidence_locators=(f"code://repo@commit-abc/src/app.py#L{index + 1}C1-L{index + 1}C2",),
        )
        for index in range(200)
    )
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=tree_edges,
        scope=scope,
    )
    labels = tuple(
        SemanticEdgePrecisionLabel(
            edge_id=edge.edge_id,
            verdict=SemanticEdgePrecisionVerdict.CORRECT,
            reviewer="reviewer-1",
        )
        for edge in sample_semantic_edges(result)
    )
    evaluation = evaluate_semantic_edges(
        result,
        labels=labels,
        eligible_edge_count=200,
    )

    assert evaluation.status is SemanticEdgeEvaluationStatus.AVAILABLE
    assert evaluation.reason == "minimum_200_label_quality_sample_complete"
    assert evaluation.sample_count == 200
    assert evaluation.precision == 1.0
    assert evaluation.coverage == 1.0
    assert evaluation.graph_noise_count == 0
    assert evaluation.harmful_count == 0


def test_uncertain_or_incomplete_labels_keep_precision_unavailable() -> None:
    scope = _scope()
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(_tree_edge(scope=scope),),
        scope=scope,
    )
    uncertain = evaluate_semantic_edges(
        result,
        labels=(
            SemanticEdgePrecisionLabel(
                edge_id=result.edges[0].edge_id,
                verdict=SemanticEdgePrecisionVerdict.UNCERTAIN,
                reviewer="reviewer-1",
            ),
        ),
    )
    assert uncertain.status is SemanticEdgeEvaluationStatus.UNAVAILABLE
    assert uncertain.precision is None
    assert uncertain.uncertain_count == 1
    assert uncertain.graph_noise_count is None
    assert uncertain.harmful_count is None


def test_evaluation_rejects_fake_unknown_duplicate_or_wrong_protocol_labels() -> None:
    scope = _scope()
    result = treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=(_tree_edge(scope=scope),),
        scope=scope,
    )
    label = SemanticEdgePrecisionLabel(
        edge_id=result.edges[0].edge_id,
        verdict=SemanticEdgePrecisionVerdict.CORRECT,
        reviewer="reviewer-1",
    )
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_semantic_edges(result, labels=(label, label))
    with pytest.raises(ValueError, match="unknown edge"):
        evaluate_semantic_edges(
            result,
            labels=(replace(label, edge_id="code-edge-v1:unknown"),),
        )
    with pytest.raises(ValueError, match="protocol"):
        replace(label, protocol_version="invented-v2")


def test_public_package_lazy_exports_semantic_treatment() -> None:
    from evidence_rag.rag.sources import code

    assert code.SemanticEdgeScope is SemanticEdgeScope
    assert code.ConservativeSemanticEdge is ConservativeSemanticEdge
    assert code.treat_python_semantic_edges is treat_python_semantic_edges
