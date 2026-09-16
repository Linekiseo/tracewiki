from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from evidence_rag.rag.sources.workspace import (
    WORKSPACE_SOURCE_DEFAULT_ENGINE,
    WorkspaceSourceRuntimeError,
    WorkspaceSourceRuntimeV2,
)
from evidence_rag.rag.sources.workspace.state_v2 import parse_workspace_timestamp
from evidence_rag.rag.sources.workspace.store import WorkspaceV2Store
from evidence_rag.storage import SQLiteStore
from evidence_rag.workspace.models import (
    IterationCreate,
    IterationLinkCreate,
    ProjectCreate,
    RelationCreate,
    TopicCreate,
    WorkItemCreate,
    WorkItemTransition,
    WorkItemUpdate,
)
from evidence_rag.workspace.service import WorkspaceService
from evidence_rag.workspace.store import WorkspaceStore


def _runtime(tmp_path: Path) -> WorkspaceSourceRuntimeV2:
    database = SQLiteStore(tmp_path / "formal.sqlite3")
    database.initialize()
    store = WorkspaceStore(database)
    store.initialize()
    service = WorkspaceService(store)
    project = service.create_project(
        ProjectCreate(
            id="project-workspace-runtime",
            name="Workspace runtime",
            acl_ref="project:workspace-runtime",
            settings={"timezone": "Asia/Shanghai"},
        )
    )
    topic = service.create_topic(
        TopicCreate(
            project_id=project["id"],
            title="Runtime facades",
            objective="Connect authoritative source state",
            status="active",
        )
    )
    iteration = service.create_iteration(
        IterationCreate(
            project_id=project["id"],
            topic_id=topic["id"],
            title="Production integration",
            goal="Exercise planning, evidence, state, and retrieval",
            status="active",
        )
    )
    prerequisite = service.create_work_item(
        WorkItemCreate(
            project_id=project["id"],
            topic_id=topic["id"],
            iteration_id=iteration["id"],
            title="Publish prerequisite",
            objective="Make dependency available",
            status="done",
            acceptance_criteria=[],
        )
    )
    work = service.create_work_item(
        WorkItemCreate(
            project_id=project["id"],
            topic_id=topic["id"],
            iteration_id=iteration["id"],
            title="Wire Workspace runtime",
            objective="Expose authoritative current state",
            acceptance_criteria=["Runtime returns governed evidence"],
            status="ready",
            due_at=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        )
    )
    work = service.transition_work_item(
        work["id"],
        WorkItemTransition(expected_version=work["version"], status="running"),
    )
    service.create_relation(
        RelationCreate(
            project_id=project["id"],
            source_entity_id=work["id"],
            predicate="depends_on",
            target_entity_id=prerequisite["id"],
            derivation="human_confirmed",
            confidence=1.0,
            review_status="confirmed",
        )
    )
    observed = datetime.now(UTC).isoformat()
    service.link_iteration(
        IterationLinkCreate(
            iteration_id=iteration["id"],
            entity_id=project["id"],
            entity_type="project",
            source_type="workspace",
            role="validation",
            status="linked",
            metadata={
                "requirement_id": "runtime-validation",
                "requirement_label": "Runtime validation evidence",
                "requirement_purpose": "Prove the production facade uses authoritative state",
                "required_roles": ["validation"],
                "allowed_source_domains": ["workspace"],
                "min_count": 1,
                "min_independence_groups": 1,
                "max_age_days": 30,
                "external_version": "1",
                "external_generation": "formal-project-v1",
                "pinned_generation": "formal-project-v1",
                "external_acl_ref": "project:workspace-runtime",
                "observed_at": observed,
                "independence_group": "workspace-control-plane",
                "reviewed": True,
                "fresh": True,
                "link_status": "accepted",
            },
        )
    )
    index_root = tmp_path / "workspace-index"
    index_root.mkdir()
    return WorkspaceSourceRuntimeV2(
        workspace=store,
        index=WorkspaceV2Store(
            index_root / "workspace-runtime.sqlite3",
            isolated_root=index_root,
        ),
    )


def test_workspace_runtime_builds_state_planning_evidence_intelligence_and_context(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    result = runtime.search(
        project_id="project-workspace-runtime",
        query="What is the current next action and readiness?",
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert result["results"]
    assert WORKSPACE_SOURCE_DEFAULT_ENGINE == "v1"
    assert result["trace"]["explicit_v2_required"] is True
    assert result["trace"]["fallback_used"] is False
    assert result["trace"]["read_only"] is True
    assert result["trace"]["mutation_applied"] is False
    assert result["intelligence"]["policy_version"]
    assert result["intelligence"]["next_actions"]
    assert result["context"]["blocks"]
    assert result["context"]["mutation_applied"] is False
    assert all("fixture" not in item["locator"] for item in result["results"])

    coverage = runtime.search(
        project_id="project-workspace-runtime",
        query="Show evidence coverage validation requirement",
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert coverage["coverage"]
    assert coverage["coverage"][0]["satisfied"] is True
    assert any(
        item["entity_type"] in {"evidence_requirement", "evidence_link"}
        for item in coverage["results"]
    )


def test_workspace_runtime_acl_and_unavailable_history_fail_closed(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    denied = runtime.search(
        project_id="project-workspace-runtime",
        query="current status",
        allowed_acl_refs=["project:other"],
        enforce_acl=True,
    )
    assert denied["results"] == []
    assert denied["trace"]["source_status"] == "acl_denied"

    historical = runtime.search(
        project_id="project-workspace-runtime",
        query="status as of yesterday",
        as_of=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert historical["results"] == []
    assert historical["trace"]["source_status"] == "partial"
    assert historical["trace"]["error_code"] == "temporal_projection_unavailable"
    assert historical["context"]["missing"] == ["historical_snapshot_unavailable"]


def test_workspace_runtime_projects_audited_status_as_of_without_current_leak(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    work = next(
        item
        for item in runtime.workspace.list_work_items(
            "project-workspace-runtime",
            limit=100,
        )
        if item["title"] == "Wire Workspace runtime"
    )
    audits = runtime.workspace.list_audits("project-workspace-runtime", limit=100)
    created_at = parse_workspace_timestamp(
        next(
            item
            for item in audits
            if item["action"] == "work_item.created" and item["resource_id"] == work["id"]
        )["created_at"],
        label="created_at",
    )
    transition_at = parse_workspace_timestamp(
        next(
            item
            for item in audits
            if item["action"] == "work_item.transitioned" and item["resource_id"] == work["id"]
        )["created_at"],
        label="transition_at",
    )
    cutoff = created_at + (transition_at - created_at) / 2

    historical = runtime.search(
        project_id="project-workspace-runtime",
        query=f"{work['display_key']} status:ready history",
        as_of=cutoff.isoformat(),
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    historical_work = next(
        item for item in historical["results"] if item["entity_id"] == work["id"]
    )
    assert historical_work["status"] == "ready"
    assert historical_work["version"] == "1"
    assert historical_work["locator"].endswith("?version=1")
    assert historical["trace"]["historical_projection"]["applied_transition_ids"] == []
    assert historical["trace"]["historical_projection"]["exact"] is True
    assert historical["trace"]["historical_projection"]["diagnostics"] == []
    assert (
        historical["trace"]["temporal_scope"]["projection"] == "formal_rows_plus_audit_transitions"
    )

    next_audit_at = min(
        parse_workspace_timestamp(item["created_at"], label="next_audit_at")
        for item in audits
        if parse_workspace_timestamp(item["created_at"], label="next_audit_at") > transition_at
    )
    transition_audit_id = next(
        item["id"]
        for item in audits
        if item["action"] == "work_item.transitioned" and item["resource_id"] == work["id"]
    )
    after_transition = runtime.search(
        project_id="project-workspace-runtime",
        query=f"{work['display_key']} status:running history",
        as_of=(transition_at + (next_audit_at - transition_at) / 2).isoformat(),
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    projected_work = next(
        item for item in after_transition["results"] if item["entity_id"] == work["id"]
    )
    assert projected_work["status"] == "running"
    assert after_transition["trace"]["historical_projection"]["applied_transition_ids"] == [
        transition_audit_id
    ]

    current = runtime.search(
        project_id="project-workspace-runtime",
        query=f"{work['display_key']} status:running",
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    current_work = next(item for item in current["results"] if item["entity_id"] == work["id"])
    assert current_work["status"] == "running"
    assert current_work["version"] == "2"
    assert current_work["locator"].endswith("?version=2")
    assert historical["index_generation"] == current["index_generation"]
    assert historical["trace"]["source_generation"] == current["index_generation"][0]
    assert historical["watermark"] == current["watermark"]


def test_workspace_runtime_date_window_is_inclusive_and_normalized(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    transition = next(
        item
        for item in runtime.workspace.list_audits("project-workspace-runtime", limit=100)
        if item["action"] == "work_item.transitioned"
    )
    instant = parse_workspace_timestamp(transition["created_at"], label="transition_at")
    equivalent = instant.astimezone(timezone(timedelta(hours=8))).isoformat()
    result = runtime.search(
        project_id="project-workspace-runtime",
        query="transition history ready running",
        date_from=equivalent,
        date_to=instant.isoformat().replace("+00:00", "Z"),
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert [(item["entity_id"], item["entity_type"]) for item in result["results"]] == [
        (transition["id"], "transition")
    ]
    normalized = instant.isoformat().replace("+00:00", "Z")
    assert result["trace"]["temporal_filter"] == {
        "as_of": None,
        "date_from": normalized,
        "date_to": normalized,
        "bounds": "inclusive",
        "field": "entity.effective_at",
    }
    assert result["trace"]["scope_filters"]["date_from"] == equivalent


def test_workspace_runtime_date_window_outside_provable_history_fails_closed(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    project = runtime.workspace.get_project("project-workspace-runtime")
    assert project is not None
    before_creation = parse_workspace_timestamp(
        project["created_at"],
        label="project_created_at",
    ) - timedelta(microseconds=1)
    result = runtime.search(
        project_id="project-workspace-runtime",
        query="history",
        date_to=before_creation.isoformat(),
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert result["results"] == []
    assert result["trace"]["source_status"] == "partial"
    assert result["trace"]["error_code"] == "temporal_scope_unavailable"
    assert result["context"]["missing"] == ["temporal_range_outside_provable_history"]


@pytest.mark.parametrize(
    ("scope", "reason"),
    [
        ({"as_of": "2026-07-30T00:00:00"}, "as_of_timezone_required"),
        ({"date_from": " 2026-07-30T00:00:00Z"}, "date_from_invalid"),
        ({"date_to": "not-a-date"}, "date_to_invalid"),
        (
            {
                "date_from": "2026-07-31T00:00:00Z",
                "date_to": "2026-07-30T00:00:00Z",
            },
            "date_range_invalid",
        ),
    ],
)
def test_workspace_runtime_invalid_temporal_scope_fails_before_source_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, str],
    reason: str,
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(
        runtime.workspace,
        "get_project",
        lambda *_args, **_kwargs: pytest.fail("invalid scope must fail before source I/O"),
    )
    with pytest.raises(WorkspaceSourceRuntimeError, match=f"^{reason}$"):
        runtime.search(
            project_id="project-workspace-runtime",
            query="history",
            **scope,
        )


def test_workspace_runtime_unprovable_field_history_fails_closed(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    before = runtime.search(
        project_id="project-workspace-runtime",
        query="current status",
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    work = next(
        item
        for item in runtime.workspace.list_work_items(
            "project-workspace-runtime",
            limit=100,
        )
        if item["title"] == "Wire Workspace runtime"
    )
    WorkspaceService(runtime.workspace).update_work_item(
        work["id"],
        WorkItemUpdate(
            expected_version=work["version"],
            title="A later title that cannot be reversed",
        ),
    )
    historical = runtime.search(
        project_id="project-workspace-runtime",
        query="history",
        as_of=before["watermark"],
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert historical["results"] == []
    assert historical["trace"]["source_status"] == "partial"
    assert historical["trace"]["error_code"] == "temporal_projection_unavailable"
    assert historical["context"]["missing"] == ["historical_snapshot_unavailable"]
    assert historical["trace"]["diagnostics"][0].startswith("unreconstructable_audit:")


def test_workspace_runtime_only_projects_current_confirmed_relations(tmp_path: Path) -> None:
    now = datetime.now(UTC)

    future_root = tmp_path / "future"
    future_root.mkdir()
    future_runtime = _runtime(future_root)
    relation = future_runtime.workspace.list_relations("project-workspace-runtime")[0]
    with future_runtime.workspace.database.transaction() as db:
        db.execute(
            "UPDATE platform_edges SET valid_from=?, valid_to=NULL WHERE id=?",
            ((now + timedelta(days=1)).isoformat(), relation["id"]),
        )
    future = future_runtime.search(
        project_id="project-workspace-runtime",
        query="current dependency",
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert future["confirmed_relations"] == []
    assert "inactive_relation_pruned" in future["trace"]["diagnostics"]

    expired_root = tmp_path / "expired"
    expired_root.mkdir()
    expired_runtime = _runtime(expired_root)
    relation = expired_runtime.workspace.list_relations("project-workspace-runtime")[0]
    with expired_runtime.workspace.database.transaction() as db:
        db.execute(
            "UPDATE platform_edges SET valid_from=?, valid_to=? WHERE id=?",
            (
                (now - timedelta(days=2)).isoformat(),
                (now - timedelta(days=1)).isoformat(),
                relation["id"],
            ),
        )
    expired = expired_runtime.search(
        project_id="project-workspace-runtime",
        query="current dependency",
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert expired["confirmed_relations"] == []

    protected = "/Users/private/relation-time"
    malformed_root = tmp_path / "malformed"
    malformed_root.mkdir()
    malformed_runtime = _runtime(malformed_root)
    relation = malformed_runtime.workspace.list_relations("project-workspace-runtime")[0]
    with malformed_runtime.workspace.database.transaction() as db:
        db.execute(
            "UPDATE platform_edges SET valid_from=?, valid_to=NULL WHERE id=?",
            (protected, relation["id"]),
        )
    malformed = malformed_runtime.search(
        project_id="project-workspace-runtime",
        query="current dependency",
        allowed_acl_refs=["project:workspace-runtime"],
        enforce_acl=True,
    )
    assert malformed["confirmed_relations"] == []
    assert "invalid_relation_temporal" in malformed["trace"]["diagnostics"]
    assert protected not in str(malformed)
