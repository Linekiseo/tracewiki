from __future__ import annotations

import pytest

from evidence_rag.rag.performance_runtime_v2 import RuntimePerformanceV2
from evidence_rag.rag.performance_v2 import PerformanceSpanV2


def _span(**overrides: object) -> PerformanceSpanV2:
    values: dict[str, object] = {
        "trace_id": "trace-" + "0" * 32,
        "span": "global_v2_execution",
        "source": None,
        "latency_ms": 12.5,
        "candidate_count": 7,
        "filtered_count": 2,
        "token_count": 128,
        "cost_units": 0.0,
        "cache_hit": False,
        "error_code": None,
        "index_generation": None,
    }
    values.update(overrides)
    return PerformanceSpanV2.model_validate(values)


def test_runtime_performance_owns_ephemeral_components_and_dashboard() -> None:
    runtime = RuntimePerformanceV2(max_spans=2)
    runtime.record(_span())
    runtime.record(_span(trace_id="trace-" + "1" * 32, latency_ms=20.0, cache_hit=True))
    runtime.record(_span(trace_id="trace-" + "2" * 32, latency_ms=30.0))

    assert runtime.vector_index.index_version == "exact-memory-vector-index-v2"
    assert len(runtime.spans()) == 2
    dashboard = runtime.dashboard(data_scale={"candidates": 14})
    assert dashboard.trace_count == 2
    assert dashboard.span_count == 2
    assert dashboard.candidate_total == 14
    assert dashboard.cache_hits == 1
    assert dashboard.hardware_profile == "repository-runtime-unqualified"
    assert runtime.clear_spans() == 2
    assert runtime.spans() == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trace_id", "raw query with spaces"),
        ("span", "unreviewed_span"),
        ("source", "/Users/private/source"),
        ("error_code", "secret=token value"),
        ("index_generation", ".."),
        ("trace_id", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"),
        ("source", "acl-team-secret"),
        ("error_code", "sk-proj-ABCDEFGHIJKLMNOP"),
        ("index_generation", "private-acl-generation"),
    ],
)
def test_runtime_performance_rejects_unbounded_or_sensitive_labels(
    field: str,
    value: str,
) -> None:
    runtime = RuntimePerformanceV2()
    with pytest.raises(ValueError):
        runtime.record(_span(**{field: value}))


def test_runtime_performance_never_claims_production_observation() -> None:
    dashboard = RuntimePerformanceV2().dashboard()
    assert dashboard.trace_count == 0
    assert dashboard.hardware_profile == "repository-runtime-unqualified"
    assert dashboard.cache_state == "in_memory_ephemeral"
    assert dashboard.calibration_availability == "UNAVAILABLE"


def test_runtime_snapshot_marks_missing_reviewed_calibration_slice_unavailable() -> None:
    runtime = RuntimePerformanceV2()
    snapshot = runtime.operational_snapshot(
        reviewed_calibration_slices=("document:claim:Claim",),
        required_calibration_slices=(
            "document:claim:Claim",
            "codex:validation:validation",
        ),
    )

    assert snapshot["status"] == "QUALITY_HOLD"
    assert snapshot["calibration_availability"] == "UNAVAILABLE"
    assert snapshot["unavailable_calibration_slice_count"] == 1
    serialized = str(snapshot).casefold()
    assert "what is the private query" not in serialized
    assert "team-secret-acl" not in serialized


@pytest.mark.parametrize(
    "kwargs",
    [
        {"active_generations": {"code": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"}},
        {"active_generations": {"query-password": "sha256:" + "0" * 64}},
        {"data_scale": {"query-password": 1}},
        {"data_scale": {"candidates": True}},
        {"hardware_profile": "private-hardware-token"},
        {"cache_state": "acl-team-secret"},
    ],
)
def test_runtime_dashboard_rejects_unreviewed_labels(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        RuntimePerformanceV2().dashboard(**kwargs)  # type: ignore[arg-type]
