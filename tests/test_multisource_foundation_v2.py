from __future__ import annotations

from evidence_rag.rag.multisource_foundation_v2 import (
    CalibrationObservationV2,
    MultiSourceCandidateV2,
    MultiSourceScopeV2,
    SourceExecutionStatusV2,
    SourceExecutionV2,
    build_calibration_dashboard_v2,
    build_default_capability_registry_v2,
    build_multisource_golden_v2,
    fit_source_calibration_v2,
    fuse_multisource_candidates_v2,
    plan_multisource_query_v2,
    rerank_cross_source_candidates_v2,
)


def _registry():
    return build_default_capability_registry_v2(
        generations={
            source: f"{source}-generation-1"
            for source in ("code", "codex", "experiment", "notebook", "document", "workspace")
        },
        watermarks={
            source: f"{source}-watermark-1"
            for source in ("code", "codex", "experiment", "notebook", "document", "workspace")
        },
    )


def test_released_multisource_golden_has_exact_60_and_frozen_slices() -> None:
    golden = build_multisource_golden_v2()
    assert len(golden.cases) == 60
    assert dict(golden.slice_counts) == {
        "code_codex": 8,
        "experiment_document": 8,
        "experiment_notebook": 7,
        "code_experiment": 6,
        "notebook_code_codex": 5,
        "workspace_evidence": 6,
        "three_source_chain": 6,
        "four_to_six_source_chain": 4,
        "version_conflict_stale": 4,
        "unanswerable_source_failure": 3,
        "acl_sensitive": 3,
    }


def test_planner_is_capability_bound_replayable_and_scope_preserving() -> None:
    scope = MultiSourceScopeV2(
        project_id="project-1",
        acl_refs=("acl-1",),
        target_version="version-1",
    )
    first = plan_multisource_query_v2(
        question="Is the reported improvement valid?",
        intent="claim_verification",
        scope=scope,
        registry=_registry(),
    )
    second = plan_multisource_query_v2(
        question="Is the reported improvement valid?",
        intent="claim_verification",
        scope=scope,
        registry=_registry(),
    )
    assert first == second
    assert first.scope.acl_refs == ("acl-1",)
    assert first.source_routes == ("document", "experiment", "code", "notebook")
    assert tuple((item.source, item.task) for item in first.source_contracts) == (
        ("document", "claim"),
        ("experiment", "aggregate"),
        ("code", "test_validation"),
        ("notebook", "output"),
    )
    assert tuple(item.task for item in first.subquestions) == (
        "claim",
        "aggregate",
        "test_validation",
        "output",
    )


def test_explicit_supplemental_sources_receive_governed_tasks() -> None:
    plan = plan_multisource_query_v2(
        question="How is the current implementation organized?",
        intent="current_implementation",
        scope=MultiSourceScopeV2(project_id="project-1", acl_refs=("acl-1",)),
        registry=_registry(),
        requested_sources=("code", "codex", "workspace", "document"),
    )

    assert tuple((item.source, item.task) for item in plan.source_contracts) == (
        ("code", "implementation"),
        ("codex", "validation"),
        ("workspace", "current"),
        ("document", "locate"),
    )


def test_capabilities_publish_real_entity_types_not_fact_placeholders() -> None:
    registry = _registry()
    by_source = {item.domain: item for item in registry.capabilities}
    assert "implementation" in by_source["code"].supported_tasks
    assert "CodeSymbol" in by_source["code"].entity_types
    assert "episode" in by_source["codex"].entity_types
    assert "cell" in by_source["notebook"].entity_types
    assert "claim" in by_source["document"].entity_types
    assert "work_item" in by_source["workspace"].entity_types
    assert all(
        f"{source}.fact" not in capability.entity_types for source, capability in by_source.items()
    )


def test_unknown_intent_is_explicitly_normalized_to_global_synthesis() -> None:
    plan = plan_multisource_query_v2(
        question="Collect all relevant evidence.",
        intent="made_up_intent",
        scope=MultiSourceScopeV2(project_id="project-1", acl_refs=("acl-1",)),
        registry=_registry(),
        requested_sources=("code", "document"),
    )
    assert plan.intent == "global_synthesis"
    assert tuple((item.source, item.task) for item in plan.source_contracts) == (
        ("code", "implementation"),
        ("document", "locate"),
    )
    assert plan.planning_gaps == ("intent:made_up_intent:fallback_global_synthesis",)


def test_calibration_uses_reviewed_source_rows_not_cross_source_raw_scores() -> None:
    rows = tuple(
        CalibrationObservationV2(source="code", raw_score=score, relevant=relevant)
        for score, relevant in (
            (0.1, False),
            (0.2, False),
            (0.4, False),
            (0.6, True),
            (0.8, True),
            (0.9, True),
        )
    )
    profile = fit_source_calibration_v2("code", rows, bins=3)
    assert profile.sample_count == 6
    assert 0 <= profile.ece <= 1
    assert 0 <= profile.brier <= 1
    assert profile.probability(0.9) >= profile.probability(0.1)


def test_calibration_dashboard_does_not_invent_missing_reviewed_profiles() -> None:
    rows = tuple(
        CalibrationObservationV2(
            source="code",
            task="implementation",
            entity_type="CodeSymbol",
            raw_score=score,
            relevant=relevant,
            review_status="reviewed",
        )
        for score, relevant in ((0.1, False), (0.9, True))
    )
    profile = fit_source_calibration_v2(
        "code",
        rows,
        task="implementation",
        entity_type="CodeSymbol",
        bins=2,
    )
    dashboard = build_calibration_dashboard_v2(
        (profile,),
        required_slices=(
            ("code", "implementation", "CodeSymbol"),
            ("document", "claim", "claim"),
        ),
    )
    reviewed, unavailable = dashboard.rows
    assert reviewed.status == "reviewed"
    assert reviewed.sample_count == 2
    assert unavailable.status == "unavailable"
    assert unavailable.sample_count == 0
    assert unavailable.ece is None
    assert unavailable.profile_sha256 is None


def test_unreviewed_rows_cannot_create_a_reviewed_calibration_profile() -> None:
    rows = (
        CalibrationObservationV2(
            source="code",
            raw_score=0.1,
            relevant=False,
            review_status="unreviewed",
        ),
        CalibrationObservationV2(
            source="code",
            raw_score=0.9,
            relevant=True,
            review_status="unreviewed",
        ),
    )
    try:
        fit_source_calibration_v2("code", rows, bins=2)
    except ValueError as error:
        assert "reviewed rows" in str(error)
    else:
        raise AssertionError("unreviewed rows were promoted to a reviewed calibration")


def test_role_fusion_rejects_acl_wrong_version_and_duplicate_root() -> None:
    plan = plan_multisource_query_v2(
        question="Why did the implementation change?",
        intent="historical_change",
        scope=MultiSourceScopeV2(project_id="project-1", acl_refs=("acl-1",)),
        registry=_registry(),
    )
    roles = plan.required_roles
    tasks = {item.source: item.task for item in plan.source_contracts}
    entity_types = {
        "code": "CodeSymbol",
        "codex": "goal",
        "workspace": "work_item",
    }

    def candidate(
        identity: str,
        source: str,
        role: str,
        *,
        acl: str = "acl-1",
        version: str = "exact",
        root: str | None = None,
        entity_type: str | None = None,
    ) -> MultiSourceCandidateV2:
        return MultiSourceCandidateV2(
            candidate_id=identity,
            entity_id=identity,
            retrieval_unit_id=f"unit-{identity}",
            parent_entity_id=None,
            source_instance=f"{source}-1",
            retrieval_domain=source,
            fact_type=f"{source}.fact",
            entity_type=entity_type or entity_types[source],
            task=tasks[source],
            title=identity,
            snippet="bounded evidence",
            locator=f"{source}://{identity}",
            stable_version="v1",
            source_generation=f"{source}-generation-1",
            raw_or_derived="raw_fact",
            derivation="source_record",
            review_status="reviewed",
            fact_status="verified",
            channel_scores=(("exact", 1.0),),
            calibrated_relevance=0.9,
            calibration_version=f"{source}-calibration-v2",
            matched_roles=(role,),
            authority=1.0,
            version_alignment=version,
            acl_ref=acl,
            token_estimate=100,
            root_provenance=root or identity,
        )

    candidates = (
        candidate("goal", "codex", roles[0], root="root-a"),
        candidate("same-root", "workspace", roles[1], root="root-a"),
        candidate("patch", "code", roles[2]),
        candidate("commit", "code", roles[3]),
        candidate("denied", "code", roles[0], acl="acl-hidden"),
        candidate("old", "code", roles[0], version="mismatch"),
        candidate("collapsed", "code", roles[0], entity_type="code.fact"),
    )
    status = tuple(
        SourceExecutionV2(
            source=source,
            status=SourceExecutionStatusV2.COMPLETE,
            candidate_count=10,
            coverage=1,
            index_generation=f"{source}-generation-1",
            watermark=f"{source}-watermark-1",
            latency_ms=1,
        )
        for source in plan.source_routes
    )
    result = fuse_multisource_candidates_v2(plan, candidates, status)
    assert all(item.acl_ref == "acl-1" for item in result.selected)
    assert all(item.version_alignment != "mismatch" for item in result.selected)
    assert ("denied", "acl_denied") in result.rejected
    assert ("old", "wrong_version") in result.rejected
    assert ("collapsed", "entity_type_unsupported") not in result.rejected
    assert any(item.candidate_id == "collapsed" for item in result.selected)
    assert sum(item.root_provenance == "root-a" for item in result.selected) == 1


def test_strict_cross_source_rerank_requires_typed_candidate_and_reviewed_profile() -> None:
    plan = plan_multisource_query_v2(
        question="Is the reported metric supported?",
        intent="claim_verification",
        scope=MultiSourceScopeV2(project_id="project-1", acl_refs=("acl-1",)),
        registry=_registry(),
        requested_sources=("document",),
    )
    profile = fit_source_calibration_v2(
        "document",
        (
            CalibrationObservationV2(
                source="document",
                task="claim",
                entity_type="claim",
                raw_score=0.1,
                relevant=False,
            ),
            CalibrationObservationV2(
                source="document",
                task="claim",
                entity_type="claim",
                raw_score=0.9,
                relevant=True,
            ),
        ),
        task="claim",
        entity_type="claim",
        bins=2,
    )

    def candidate(
        identity: str,
        entity_type: str,
        *,
        calibrated_relevance: float | None = None,
    ) -> MultiSourceCandidateV2:
        return MultiSourceCandidateV2(
            candidate_id=identity,
            entity_id=identity,
            retrieval_unit_id=f"unit-{identity}",
            parent_entity_id=None,
            source_instance="document-1",
            retrieval_domain="document",
            fact_type="document.fact",
            entity_type=entity_type,
            task="claim",
            title=identity,
            snippet="reported metric evidence",
            locator=f"document://{identity}",
            stable_version="v1",
            source_generation="document-generation-1",
            raw_or_derived="raw_fact",
            derivation="source_record",
            review_status="reviewed",
            fact_status="verified",
            channel_scores=(("exact", 0.9),),
            calibrated_relevance=(
                profile.probability(0.9) if calibrated_relevance is None else calibrated_relevance
            ),
            calibration_version=profile.calibration_version,
            matched_roles=("claim",),
            authority=1.0,
            version_alignment="exact",
            acl_ref="acl-1",
            token_estimate=20,
            root_provenance=identity,
        )

    result = rerank_cross_source_candidates_v2(
        plan,
        (
            candidate("typed", "claim"),
            candidate("collapsed", "document.fact"),
            candidate("uncalibrated", "claim", calibrated_relevance=0.9),
        ),
        (profile,),
    )
    assert tuple(item.candidate_id for item in result.ranked) == ("typed",)
    assert ("collapsed", "reviewed_calibration_missing") in result.rejected
    assert ("uncalibrated", "calibrated_relevance_mismatch") in result.rejected
    assert result.decisions[0].task == "claim"
