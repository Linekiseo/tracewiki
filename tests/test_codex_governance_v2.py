from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.rag.sources.codex.governance_v2 import (
    CODEX_RELEASE_THRESHOLDS,
    CODEX_REQUIRED_GUARDRAILS,
    CodexAppendDisposition,
    CodexCircuitStatus,
    CodexEvidenceStatus,
    CodexReleaseDecisionType,
    CodexReleaseGuardrailV2,
    CodexReleaseMetricV2,
    CodexReleaseStage,
    CodexShadowObservationV2,
    advance_and_publish_codex_append_cursor_v2,
    advance_codex_append_cursor_v2,
    aggregate_codex_shadow_v2,
    build_codex_privacy_tombstone_v2,
    build_codex_release_evidence_v2,
    evaluate_codex_circuit_v2,
    evaluate_codex_release_v2,
    execute_codex_with_fallback_v2,
    propagate_codex_privacy_tombstone_v2,
    select_codex_engine_v2,
)
from evidence_rag.rag.sources.codex.store_v2 import CodexV2PublicationError, CodexV2Store


def _cursor(content: bytes, *, previous=None):
    return advance_codex_append_cursor_v2(
        content,
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        acl_ref="project:project-a",
        previous=previous,
    )


def _metrics(*, available: bool = True):
    return tuple(
        CodexReleaseMetricV2(
            name=name,
            status=(
                CodexEvidenceStatus.AVAILABLE if available else CodexEvidenceStatus.UNAVAILABLE
            ),
            numerator=threshold if available else 0,
            denominator=1 if available else 0,
            value=threshold if available else None,
        )
        for name, (_, threshold) in CODEX_RELEASE_THRESHOLDS.items()
    )


def _guardrails(*, violations: int = 0):
    return tuple(
        CodexReleaseGuardrailV2(
            name=name,
            status=CodexEvidenceStatus.AVAILABLE,
            violations=violations,
        )
        for name in CODEX_REQUIRED_GUARDRAILS
    )


def _evidence(
    *,
    observed=CodexReleaseStage.OFFLINE,
    proposed=CodexReleaseStage.SHADOW_INTERNAL_100,
    available=True,
    violations=0,
    production=False,
    treatment=False,
):
    return build_codex_release_evidence_v2(
        observed_stage=observed,
        proposed_stage=proposed,
        artifact_set_sha256="sha256:" + ("1" * 64),
        golden_package_sha256="sha256:" + ("2" * 64),
        component_set_sha256="sha256:" + ("3" * 64),
        metrics=_metrics(available=available),
        guardrails=_guardrails(violations=violations),
        production_observation=production,
        treatment_result_available=treatment,
        rollback_rehearsed=production and treatment,
    )


def test_append_cursor_never_advances_partial_and_retains_lkg_on_replacement(
    tmp_path: Path,
) -> None:
    first = _cursor(b'{"a":1}\n{"partial":')
    assert first.published_offset == len(b'{"a":1}\n')
    partial = _cursor(b'{"a":1}\n{"partial":true', previous=first)
    assert partial.disposition is CodexAppendDisposition.PARTIAL_ONLY
    assert partial.published_offset == first.published_offset
    completed = _cursor(b'{"a":1}\n{"partial":true}\n', previous=partial)
    assert completed.disposition is CodexAppendDisposition.APPENDED
    assert completed.partial_bytes == 0
    replaced = _cursor(b'{"different":1}\n', previous=completed)
    assert replaced.disposition is CodexAppendDisposition.FULL_REINGEST_REQUIRED
    assert replaced.published_offset == 0
    assert replaced.previous_cursor_id == completed.cursor_id

    store = CodexV2Store(tmp_path / "cursor" / "codex.sqlite3", isolated_root=tmp_path)
    store.initialize()
    assert store.publish_append_cursor(first)["head_cursor_id"] == first.cursor_id
    assert store.publish_append_cursor(partial)["head_cursor_id"] == partial.cursor_id
    assert store.publish_append_cursor(completed)["head_cursor_id"] == completed.cursor_id
    held = store.publish_append_cursor(replaced)
    assert held["operation"] == "lkg_retained"
    assert held["head_cursor_id"] == completed.cursor_id
    version_changed = advance_codex_append_cursor_v2(
        b'{"a":1}\n{"partial":true}\n',
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        acl_ref="project:project-a",
        previous=completed,
        builder_version="codex-derived-builder-v3",
    )
    assert version_changed.disposition is CodexAppendDisposition.FULL_REINGEST_REQUIRED
    assert version_changed.published_offset == 0


def test_persisted_append_head_feeds_privacy_tombstone_propagation(
    tmp_path: Path,
) -> None:
    store = CodexV2Store(tmp_path / "cursor" / "codex.sqlite3", isolated_root=tmp_path)
    store.initialize()
    first, first_receipt = advance_and_publish_codex_append_cursor_v2(
        store,
        b'{"a":1}\n{"partial":',
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        acl_ref="project:project-a",
    )
    assert first_receipt["head_cursor_id"] == first.cursor_id
    completed, completed_receipt = advance_and_publish_codex_append_cursor_v2(
        store,
        b'{"a":1}\n{"partial":true}\n',
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        acl_ref="project:project-a",
    )
    assert completed.previous_cursor_id == first.cursor_id
    assert completed_receipt["head_cursor_id"] == completed.cursor_id

    tombstone = build_codex_privacy_tombstone_v2(
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        acl_ref="project:project-a",
        reason_code="privacy_request",
        source_cursor_id=completed.cursor_id,
    )
    propagated = propagate_codex_privacy_tombstone_v2(store, tombstone)
    assert propagated["source_tombstone_id"] == tombstone.tombstone_id
    assert not any(propagated["active_derived_counts"].values())
    assert (
        store.get_append_cursor_head(
            project_id="project-a",
            source_id="codex-source:a",
            generation_id="generation-a",
            acl_ref="project:project-a",
        )
        is None
    )
    with pytest.raises(CodexV2PublicationError, match="tombstoned"):
        advance_and_publish_codex_append_cursor_v2(
            store,
            b'{"a":1}\n{"partial":true}\n{"later":2}\n',
            project_id="project-a",
            source_id="codex-source:a",
            generation_id="generation-a",
            acl_ref="project:project-a",
        )


def test_stable_engine_selection_and_sanitized_shadow_aggregation() -> None:
    first = select_codex_engine_v2(
        project_id="project-a",
        request_id="request-a",
        stage=CodexReleaseStage.CANARY_25,
    )
    second = select_codex_engine_v2(
        project_id="project-a",
        request_id="request-a",
        stage=CodexReleaseStage.CANARY_25,
    )
    assert first == second
    held = select_codex_engine_v2(
        project_id="project-a",
        request_id="request-a",
        stage=CodexReleaseStage.OFFLINE,
        explicit_engine="v2",
    )
    assert held.engine == "v1"
    shadow = select_codex_engine_v2(
        project_id="project-a",
        request_id="request-a",
        stage=CodexReleaseStage.SHADOW_INTERNAL_100,
    )
    assert shadow.engine == "v1" and shadow.shadow_v2

    aggregate = aggregate_codex_shadow_v2(
        (
            CodexShadowObservationV2(
                request_key_sha256="sha256:" + ("4" * 64),
                stage=CodexReleaseStage.SHADOW_INTERNAL_100,
                v1_result_count=10,
                v2_result_count=9,
                overlap_count=8,
                v1_latency_ms=10,
                v2_latency_ms=12,
                fallback=False,
            ),
        )
    )
    assert aggregate.overlap_numerator == 8
    assert aggregate.overlap_denominator == 10
    circuit = evaluate_codex_circuit_v2(aggregate.model_copy(update={"secret_leakage": 1}))
    assert circuit.status is CodexCircuitStatus.OPEN


def test_override_authority_and_exactly_once_fallback() -> None:
    unauthorized = select_codex_engine_v2(
        project_id="project-a",
        request_id="request-a",
        stage=CodexReleaseStage.DEFAULT_V2,
        explicit_engine="v2",
    )
    assert unauthorized.engine == "v1"
    selection = select_codex_engine_v2(
        project_id="project-a",
        request_id="request-a",
        stage=CodexReleaseStage.DEFAULT_V2,
        explicit_engine="v2",
        override_authorized=True,
    )
    calls = {"v1": 0, "v2": 0}

    def v1() -> str:
        calls["v1"] += 1
        return "legacy"

    def v2() -> str:
        calls["v2"] += 1
        raise RuntimeError("sensitive detail")

    outcome = execute_codex_with_fallback_v2(selection, run_v1=v1, run_v2=v2)
    assert outcome.response == "legacy"
    assert outcome.fallback
    assert outcome.error_code == "codex-v2-execution-failed"
    assert calls == {"v1": 1, "v2": 1}


def test_release_evidence_holds_without_treatment_and_rolls_back_deployed_failure() -> None:
    held = evaluate_codex_release_v2(_evidence(available=True, production=False, treatment=False))
    assert held.decision is CodexReleaseDecisionType.HOLD_DEFAULT_V1
    assert held.default_engine == "v1"
    promoted = evaluate_codex_release_v2(_evidence(available=True, production=True, treatment=True))
    assert promoted.decision is CodexReleaseDecisionType.PROMOTE
    rolled_back = evaluate_codex_release_v2(
        _evidence(
            observed=CodexReleaseStage.CANARY_5,
            proposed=CodexReleaseStage.CANARY_25,
            available=False,
            violations=1,
            production=True,
            treatment=True,
        )
    )
    assert rolled_back.decision is CodexReleaseDecisionType.ROLLBACK
    assert rolled_back.rollback_sequence
    assert not rolled_back.side_effect_applied


def test_privacy_tombstone_contract_is_content_addressed() -> None:
    tombstone = build_codex_privacy_tombstone_v2(
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        acl_ref="project:project-a",
        reason_code="privacy_request",
    )
    assert tombstone.tombstone_id.startswith("codextomb-")
    with pytest.raises(ValueError, match="allowlisted"):
        build_codex_privacy_tombstone_v2(
            project_id="project-a",
            source_id="codex-source:a",
            generation_id="generation-a",
            acl_ref="project:project-a",
            reason_code="free form",
        )
