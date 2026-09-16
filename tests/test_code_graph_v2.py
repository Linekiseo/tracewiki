from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.code import (
    CODE_EDGE_REGISTRY,
    CODE_EDGE_SPECS,
    EDGE_REGISTRY,
    CodeEdgeDerivationLayer,
    CodeEdgeDirection,
    CodeEdgeGenerationRequirement,
    CodeEdgeInverse,
    CodeEdgeOwner,
    CodeEdgeReviewRequirement,
    CodeEdgeSpec,
    CodeEdgeVersionRequirement,
    CodeGraphEntity,
    CodeGraphEntityType,
    CodeRelationType,
    CodeSymbolKind,
    CodeUnresolvedDiagnostic,
    CodeUnresolvedDiagnostics,
    get_edge_spec,
    registered_edge_type,
    require_registered_edge_types,
    summarize_unresolved_diagnostics,
    validate_edge_assertion,
)


def _diagnostic(
    raw_target: str,
    *,
    source_id: str = "code://repo@abc/src/service.py#symbol=Service.run",
    relation: str = "CALLS",
    reason: str = "no_candidate",
    language: str = "python",
    module: str = "service",
    candidates: tuple[str, ...] = (),
) -> CodeUnresolvedDiagnostic:
    return CodeUnresolvedDiagnostic(
        source=CodeGraphEntity(
            entity_id=source_id,
            entity_type="CodeSymbol",
            symbol_kind="method",
        ),
        raw_target=raw_target,
        relation=relation,
        reason=reason,
        candidate_ids=candidates,
        parser_version="tree-sitter-python-v1",
        resolver_version="code-static-resolver-v1",
        language=language,
        module=module,
    )


def test_edge_registry_is_complete_immutable_serializable_and_canonical() -> None:
    assert EDGE_REGISTRY is CODE_EDGE_REGISTRY
    assert tuple(CODE_EDGE_REGISTRY) == tuple(CodeRelationType)
    assert tuple(CODE_EDGE_REGISTRY.values()) == CODE_EDGE_SPECS
    assert {spec.edge_type for spec in CODE_EDGE_SPECS} == set(CodeRelationType)

    for edge_type, spec in CODE_EDGE_REGISTRY.items():
        assert edge_type is spec.edge_type
        assert spec.source_types
        assert spec.target_types
        assert spec.derivations
        assert spec.allowed_tasks
        assert spec.confidence_meaning
        assert 1 <= spec.default_traversal_cost <= 10
        assert CodeEdgeSpec.model_validate_json(spec.model_dump_json()) == spec
        assert len(spec.canonical_sha256()) == 64

    with pytest.raises(TypeError):
        CODE_EDGE_REGISTRY[CodeRelationType.CALLS] = get_edge_spec("REFERENCES")  # type: ignore[index]


def test_unknown_edges_fail_closed_before_a_traversal_whitelist() -> None:
    assert registered_edge_type("CALLS") is CodeRelationType.CALLS
    assert require_registered_edge_types(["TESTS", "CALLS"]) == (
        CodeRelationType.CALLS,
        CodeRelationType.TESTS,
    )

    with pytest.raises(ValueError, match="unregistered Code edge type"):
        registered_edge_type("DEPENDS_ON")
    with pytest.raises(ValueError, match="unregistered Code edge type"):
        get_edge_spec("calls")
    with pytest.raises(ValueError, match="duplicates"):
        require_registered_edge_types(["CALLS", CodeRelationType.CALLS])
    with pytest.raises(ValidationError, match="relation"):
        _diagnostic("target").model_copy(update={"relation": "free_edge"})


def test_inverse_owner_direction_and_version_generation_semantics_are_explicit() -> None:
    calls = get_edge_spec(CodeRelationType.CALLS)
    assert calls.direction is CodeEdgeDirection.FORWARD
    assert calls.inverse is CodeEdgeInverse.CALLED_BY
    assert calls.owner is CodeEdgeOwner.SOURCE
    assert calls.version_requirement is CodeEdgeVersionRequirement.SAME_STABLE_VERSION
    assert calls.generation_requirement is CodeEdgeGenerationRequirement.SAME_GENERATION

    lineage = get_edge_spec("SAME_SYMBOL_AS")
    assert lineage.direction is CodeEdgeDirection.BIDIRECTIONAL
    assert lineage.inverse is CodeEdgeInverse.SAME_SYMBOL_AS
    assert lineage.owner is CodeEdgeOwner.BOTH
    assert lineage.version_requirement is CodeEdgeVersionRequirement.EXPLICIT_VERSION_TRANSITION
    assert lineage.generation_requirement is CodeEdgeGenerationRequirement.EXPLICIT_GENERATIONS
    assert lineage.review_requirement is CodeEdgeReviewRequirement.SEMANTIC_CANDIDATES

    validation = get_edge_spec("FAILED_VALIDATION")
    assert validation.inverse is CodeEdgeInverse.HAS_FAILED_VALIDATION
    assert validation.version_requirement is CodeEdgeVersionRequirement.EXACT_TARGET_VERSION
    assert validation.generation_requirement is CodeEdgeGenerationRequirement.TARGET_ALIGNED
    assert "must not be treated as validation success" in validation.confidence_meaning


def test_derivation_layers_are_typed_and_semantic_edges_cannot_claim_calls() -> None:
    calls = get_edge_spec("CALLS")
    assert calls.derivations == (
        CodeEdgeDerivationLayer.STATIC,
        CodeEdgeDerivationLayer.HUMAN,
    )
    assert calls.require_derivation("static") is CodeEdgeDerivationLayer.STATIC
    with pytest.raises(ValueError, match="not allowed for CALLS"):
        calls.require_derivation("semantic")
    with pytest.raises(ValueError, match="not allowed for CALLS"):
        validate_edge_assertion(
            "CALLS",
            source_type="CodeSymbol",
            target_type="CodeSymbol",
            derivation="semantic",
        )

    payload = calls.model_dump(mode="python")
    payload["derivations"] = ["static", "semantic", "human"]
    payload["review_requirement"] = "semantic_candidates"
    with pytest.raises(ValidationError, match="semantic derivation cannot assert a CALLS"):
        CodeEdgeSpec.model_validate(payload)

    affects = validate_edge_assertion(
        "AFFECTS",
        source_type="DiffHunk",
        target_type="CodeSymbol",
        derivation="semantic",
    )
    assert affects.review_requirement is CodeEdgeReviewRequirement.SEMANTIC_CANDIDATES


def test_endpoint_types_are_closed_and_local_variables_are_not_entities() -> None:
    assert validate_edge_assertion(
        "TYPE_OF",
        source_type=CodeGraphEntityType.CODE_SYMBOL,
        target_type=CodeGraphEntityType.TYPE_ENTITY,
        derivation="static",
    ) is get_edge_spec("TYPE_OF")
    assert not get_edge_spec("CALLS").accepts_endpoints("LocalVariable", "CodeSymbol")

    with pytest.raises(ValidationError, match="entity_type"):
        CodeGraphEntity(
            entity_id="code://repo@abc/src/service.py#local=result",
            entity_type="LocalVariable",
        )
    with pytest.raises(ValidationError, match="symbol_kind"):
        CodeGraphEntity(
            entity_id="code://repo@abc/src/service.py#local=result",
            entity_type="CodeSymbol",
            symbol_kind="local_variable",
        )
    with pytest.raises(ValueError, match="endpoint types"):
        validate_edge_assertion(
            "CALLS",
            source_type="local_variable",
            target_type="CodeSymbol",
            derivation="static",
        )


def test_diagnostic_sampling_is_bounded_while_aggregates_count_every_observation() -> None:
    repeated = _diagnostic("missing")
    records = [
        repeated,
        repeated,
        _diagnostic("alpha"),
        _diagnostic("beta", reason="ambiguous", candidates=("entity://b", "entity://a")),
        _diagnostic("gamma", reason="dynamic"),
        _diagnostic("delta", reason="limit"),
        _diagnostic(
            "external.package",
            relation="IMPORTS",
            reason="external",
            module="service.imports",
        ),
        _diagnostic(
            "external.other",
            source_id="code://repo@abc/src/other.py#symbol=load",
            relation="IMPORTS",
            reason="external",
            module="other",
        ),
    ]

    summary = summarize_unresolved_diagnostics(
        records,
        detail_limit_per_source_relation=2,
    )

    assert summary.total_count == len(records)
    assert sum(item.count for item in summary.aggregates) == len(records)
    assert summary.count(relation="CALLS") == 6
    assert summary.count(reason="external", language="python") == 2
    assert summary.count(module="service.imports") == 1
    assert len(summary.details) == 4
    detail_buckets = Counter((item.source.entity_id, item.relation) for item in summary.details)
    assert max(detail_buckets.values()) == 2
    assert summary.by_language_module() == tuple(
        sorted(summary.by_language_module(), key=lambda item: (item.language, item.module))
    )

    no_details = summarize_unresolved_diagnostics(
        records,
        detail_limit_per_source_relation=0,
    )
    assert no_details.details == ()
    assert no_details.total_count == len(records)
    assert no_details.aggregates == summary.aggregates


def test_diagnostic_sampling_and_serialization_are_order_independent() -> None:
    records = [
        _diagnostic(
            f"target_{index}",
            reason=("ambiguous" if index % 2 else "no_candidate"),
            candidates=(f"entity://{index + 1}", f"entity://{index}"),
        )
        for index in range(12)
    ]
    forward = summarize_unresolved_diagnostics(
        records,
        detail_limit_per_source_relation=3,
    )
    reverse = summarize_unresolved_diagnostics(
        reversed(records),
        detail_limit_per_source_relation=3,
    )

    assert forward == reverse
    assert forward.canonical_json_bytes() == reverse.canonical_json_bytes()
    assert CodeUnresolvedDiagnostics.model_validate_json(forward.model_dump_json()) == forward
    assert all(item.candidate_ids == tuple(sorted(item.candidate_ids)) for item in forward.details)


def test_graph_models_are_frozen_and_revalidated_on_copy() -> None:
    entity = CodeGraphEntity(
        entity_id="code://repo@abc/src/service.py#symbol=Service",
        entity_type="CodeSymbol",
        symbol_kind=CodeSymbolKind.CLASS,
    )
    diagnostic = _diagnostic("missing")
    summary = summarize_unresolved_diagnostics([diagnostic])

    with pytest.raises(ValidationError, match="frozen"):
        entity.entity_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        summary.total_count = 2  # type: ignore[misc]
    with pytest.raises(ValidationError, match="uncapped aggregate count"):
        summary.model_copy(update={"total_count": 2})
