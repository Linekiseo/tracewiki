from __future__ import annotations

from evidence_rag.rag.answer_v2 import (
    AnswerAuthorityV2,
    AnswerClaimV2,
    AnswerModeV2,
    build_evidence_pack_v2,
    build_grounded_answer_v2,
    normalize_evidence_fact_v2,
    render_source_evidence_v2,
)
from evidence_rag.rag.evidence_graph_v2 import (
    build_default_edge_registry_v2,
    traverse_evidence_graph_v2,
)
from evidence_rag.rag.multisource_foundation_v2 import (
    MultiSourceCandidateV2,
    MultiSourceScopeV2,
    RoleFusionResultV2,
    SourceExecutionStatusV2,
    SourceExecutionV2,
    build_default_capability_registry_v2,
    canonical_sha256_v2,
    plan_multisource_query_v2,
)


def _plan():
    domains = ("code", "codex", "experiment", "notebook", "document", "workspace")
    registry = build_default_capability_registry_v2(
        generations={domain: f"{domain}-g1" for domain in domains},
        watermarks={domain: f"{domain}-w1" for domain in domains},
    )
    return plan_multisource_query_v2(
        question="Where is parser implemented?",
        intent="current_implementation",
        scope=MultiSourceScopeV2(project_id="project-1", acl_refs=("acl-1",)),
        registry=registry,
        requested_sources=("code",),
        context_budget_tokens=1000,
    )


def _candidate():
    return MultiSourceCandidateV2(
        candidate_id="code-parser",
        entity_id="code-parser",
        retrieval_unit_id="unit-code-parser",
        parent_entity_id=None,
        source_instance="code-1",
        retrieval_domain="code",
        fact_type="code.fact",
        entity_type="CodeSymbol",
        task="implementation",
        title="Parser implementation",
        snippet="Parser implementation validates syntax",
        locator="code://repo/parser.py#L1",
        stable_version="commit-a",
        source_generation="code-g1",
        raw_or_derived="raw_fact",
        derivation="source_record",
        review_status="reviewed",
        fact_status="verified",
        channel_scores=(("exact", 1.0),),
        calibrated_relevance=1,
        calibration_version="code-cal-v2",
        matched_roles=("current_symbol", "current_version", "validation"),
        authority=1,
        version_alignment="exact",
        acl_ref="acl-1",
        token_estimate=100,
        root_provenance="parser-root",
    )


def _fusion(plan, candidate, *, status=SourceExecutionStatusV2.COMPLETE):
    payload = {
        "selected": (candidate,),
        "satisfied_roles": plan.required_roles
        if status is SourceExecutionStatusV2.COMPLETE
        else (),
        "missing_roles": () if status is SourceExecutionStatusV2.COMPLETE else plan.required_roles,
        "source_status": (
            SourceExecutionV2(
                source="code",
                status=status,
                candidate_count=1 if status is SourceExecutionStatusV2.COMPLETE else 0,
                coverage=1 if status is SourceExecutionStatusV2.COMPLETE else None,
                index_generation="code-g1",
                watermark="code-w1",
                latency_ms=10,
            ),
        ),
        "rejected": (),
        "total_tokens": 100,
        "budget_tokens": 1000,
        "fusion_version": "multisource-role-fusion-v2",
    }
    constructed = RoleFusionResultV2.model_construct(content_sha256="pending", **payload)
    return RoleFusionResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            constructed.model_dump(mode="json", exclude={"content_sha256"})
        ),
    )


def test_evidence_pack_and_claim_citation_verification() -> None:
    plan = _plan()
    candidate = _candidate()
    traversal = traverse_evidence_graph_v2(
        plan,
        (candidate,),
        (),
        build_default_edge_registry_v2(),
        seed_ids=("code-parser",),
    )
    pack = build_evidence_pack_v2(plan, _fusion(plan, candidate), traversal)
    fact_id = pack.included_fact_ids[0]
    answer = build_grounded_answer_v2(
        pack,
        (
            AnswerClaimV2(
                claim_id="claim-1",
                text="Parser implementation validates syntax",
                citation_ids=("E1",),
                required_fact_ids=(fact_id,),
            ),
        ),
    )
    assert answer.mode == AnswerModeV2.GROUNDED
    assert answer.authority == AnswerAuthorityV2.FINAL_ANSWER
    assert answer.unsupported_claim_count == 0
    assert answer.citation_precision_numerator == 1
    assert pack.rendered_evidence[0].heading == "Code · CodeSymbol"


def test_unsupported_claim_is_not_rendered() -> None:
    plan = _plan()
    candidate = _candidate()
    traversal = traverse_evidence_graph_v2(
        plan,
        (candidate,),
        (),
        build_default_edge_registry_v2(),
        seed_ids=("code-parser",),
    )
    pack = build_evidence_pack_v2(plan, _fusion(plan, candidate), traversal)
    answer = build_grounded_answer_v2(
        pack,
        (
            AnswerClaimV2(
                claim_id="claim-1",
                text="Parser guarantees quantum optimization",
                citation_ids=("E1",),
                required_fact_ids=pack.included_fact_ids,
            ),
        ),
    )
    assert answer.mode == AnswerModeV2.RETRIEVAL_ONLY
    assert answer.rendered == ""
    assert answer.refusal_reason is None
    assert answer.unsupported_claim_count == 1


def test_timeout_is_not_misreported_as_no_evidence() -> None:
    plan = _plan()
    candidate = _candidate()
    traversal = traverse_evidence_graph_v2(
        plan,
        (candidate,),
        (),
        build_default_edge_registry_v2(),
        seed_ids=("code-parser",),
    )
    pack = build_evidence_pack_v2(
        plan,
        _fusion(plan, candidate, status=SourceExecutionStatusV2.TIMEOUT),
        traversal,
    )
    assert pack.decision.status == "source_unavailable"
    assert pack.decision.unanswerable_reason == "timeout"
    assert pack.decision.answer_mode == AnswerModeV2.RETRIEVAL_ONLY
    answer = build_grounded_answer_v2(
        pack,
        (
            AnswerClaimV2(
                claim_id="claim-1",
                text="Parser implementation validates syntax",
                citation_ids=("E1",),
                required_fact_ids=pack.included_fact_ids,
            ),
        ),
    )
    assert answer.mode == AnswerModeV2.REFUSAL
    assert answer.authority == AnswerAuthorityV2.FINAL_ANSWER
    assert answer.refusal_reason == "timeout"


def test_unanswerable_pack_refuses_even_without_generated_claims() -> None:
    plan = _plan()
    candidate = _candidate()
    traversal = traverse_evidence_graph_v2(
        plan,
        (candidate,),
        (),
        build_default_edge_registry_v2(),
        seed_ids=("code-parser",),
    )
    pack = build_evidence_pack_v2(
        plan,
        _fusion(plan, candidate, status=SourceExecutionStatusV2.TIMEOUT),
        traversal,
    )

    answer = build_grounded_answer_v2(pack, ())

    assert answer.mode == AnswerModeV2.REFUSAL
    assert answer.authority == AnswerAuthorityV2.FINAL_ANSWER
    assert answer.rendered.startswith("Insufficient evidence.")
    assert answer.claims == ()
    assert answer.refusal_reason == "timeout"


def test_empty_claims_are_a_retrieval_only_handoff_not_a_fake_answer() -> None:
    plan = _plan()
    candidate = _candidate()
    traversal = traverse_evidence_graph_v2(
        plan,
        (candidate,),
        (),
        build_default_edge_registry_v2(),
        seed_ids=("code-parser",),
    )
    pack = build_evidence_pack_v2(plan, _fusion(plan, candidate), traversal)
    answer = build_grounded_answer_v2(pack, ())
    assert answer.mode == AnswerModeV2.RETRIEVAL_ONLY
    assert answer.authority == AnswerAuthorityV2.RETRIEVAL_STAGE
    assert answer.rendered == ""
    assert answer.claims == ()
    assert answer.refusal_reason is None


def test_unknown_citation_fails_closed_to_retrieval_only() -> None:
    plan = _plan()
    candidate = _candidate()
    traversal = traverse_evidence_graph_v2(
        plan,
        (candidate,),
        (),
        build_default_edge_registry_v2(),
        seed_ids=("code-parser",),
    )
    pack = build_evidence_pack_v2(plan, _fusion(plan, candidate), traversal)
    answer = build_grounded_answer_v2(
        pack,
        (
            AnswerClaimV2(
                claim_id="claim-1",
                text="Parser implementation validates syntax",
                citation_ids=("E999",),
                required_fact_ids=pack.included_fact_ids,
            ),
        ),
    )
    assert answer.mode == AnswerModeV2.RETRIEVAL_ONLY
    assert answer.verifications[0].reason == "citation_unknown"
    assert answer.verifications[0].invalid_citation_ids == ("E999",)


def test_unreviewed_derived_fact_cannot_become_claim_authority() -> None:
    plan = _plan()
    candidate = _candidate().model_copy(
        update={
            "raw_or_derived": "derived_fact",
            "derivation": "model_projection",
            "review_status": "unreviewed",
        }
    )
    traversal = traverse_evidence_graph_v2(
        plan,
        (candidate,),
        (),
        build_default_edge_registry_v2(),
        seed_ids=("code-parser",),
    )
    pack = build_evidence_pack_v2(plan, _fusion(plan, candidate), traversal)
    answer = build_grounded_answer_v2(
        pack,
        (
            AnswerClaimV2(
                claim_id="claim-1",
                text="Parser implementation validates syntax",
                citation_ids=("E1",),
                required_fact_ids=pack.included_fact_ids,
            ),
        ),
    )
    assert answer.mode == AnswerModeV2.RETRIEVAL_ONLY
    assert answer.verifications[0].reason == "fact_not_claim_authority"


def test_source_renderers_preserve_each_typed_entity_semantics() -> None:
    entity_types = {
        "code": "CodeSymbol",
        "codex": "episode",
        "experiment": "ExperimentMetricObservationV2",
        "notebook": "cell",
        "document": "claim",
        "workspace": "work_item",
    }
    headings = {}
    for source, entity_type in entity_types.items():
        candidate = _candidate().model_copy(
            update={
                "candidate_id": source,
                "entity_id": source,
                "retrieval_unit_id": f"unit-{source}",
                "source_instance": f"{source}-1",
                "retrieval_domain": source,
                "fact_type": f"{source}.fact",
                "entity_type": entity_type,
                "locator": f"{source}://typed-entity",
                "root_provenance": source,
            }
        )
        rendered = render_source_evidence_v2(normalize_evidence_fact_v2(candidate))
        headings[source] = rendered.heading
        assert entity_type in rendered.heading
        assert rendered.locator == f"{source}://typed-entity"
    assert tuple(headings) == (
        "code",
        "codex",
        "experiment",
        "notebook",
        "document",
        "workspace",
    )
