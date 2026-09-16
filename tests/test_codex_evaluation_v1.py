from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.codex_adapter import CodexSessionAdapter
from evidence_rag.rag.sources.codex.evaluation_v1 import (
    CODEX_GOLDEN_CASE_COUNT,
    EXPECTED_FALSE_VALIDATION_CATEGORIES,
    EXPECTED_NEGATIVE_ROUTES,
    EXPECTED_SLICE_COUNTS,
    CodexEvaluationError,
    CodexGoldenCase,
    CodexMetric,
    EventTruth,
    ExpectedItem,
    GoldenDataset,
    MetricStatus,
    ReviewedRetrievalRow,
    evaluate_reviewed_codex_retrieval,
    load_codex_golden_v1,
    materialize_codex_session_fixture,
    probe_codex_evaluation_foundation,
)

FIXTURE = Path(__file__).parent / "fixtures" / "codex_golden_v1"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _readdress(root: Path, relative: str) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    raw = (root / relative).read_bytes()
    entry = next(item for item in manifest["files"] if item["path"] == relative)
    entry["sha256"] = _digest(raw)
    entry["size"] = len(raw)
    hash_payload = dict(manifest)
    hash_payload.pop("package_hash")
    manifest["package_hash"] = _digest(_canonical(hash_payload))
    manifest_path.write_bytes(_canonical(manifest) + b"\n")


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "released"
    shutil.copytree(FIXTURE, target)
    return target


def _parse_materialized(settings, tmp_path: Path):
    project = tmp_path / "portable-project"
    project.mkdir(parents=True)
    codex_home = materialize_codex_session_fixture(
        tmp_path / "codex-home",
        project_root=project,
    )
    adapter = CodexSessionAdapter(
        replace(
            settings,
            allowed_local_roots=(tmp_path,),
            codex_home=codex_home,
            max_codex_item_chars=16_000,
        )
    )
    titles = adapter.read_titles(codex_home)
    parsed = []
    for path in adapter.discover(codex_home, include_archived=True, max_sessions=20):
        session = adapter.parse(
            path,
            source_root=codex_home,
            source_id="codex-source://golden-v1",
            generation_id="codex-generation://golden-v1",
            project_id="project-rag",
            project_path=project,
            acl_ref="project:project-rag",
            titles=titles,
        )
        assert session is not None
        parsed.append(session)
    return codex_home, project, parsed


def _perfect_rows(dataset: GoldenDataset) -> list[ReviewedRetrievalRow]:
    rows: list[ReviewedRetrievalRow] = []
    for case in dataset.cases:
        if case.unanswerable:
            continue
        ordered_truth = sorted(
            case.event_truth,
            key=lambda item: item.order if item.order is not None else 1_000,
        )
        item_ids = [item.item_id for item in ordered_truth]
        item_ids.extend(
            item.item_id for item in case.expected_items if item.item_id not in item_ids
        )
        judgments = {item.item_id: item for item in case.item_judgments}
        for rank, item_id in enumerate(item_ids, 1):
            authority = judgments[item_id]
            rows.append(
                ReviewedRetrievalRow(
                    dataset_id=dataset.dataset_id,
                    dataset_version=dataset.dataset_version,
                    package_hash=dataset.package_hash,
                    case_id=case.case_id,
                    rank=rank,
                    thread_id=authority.thread_id,
                    item_id=item_id,
                    locator=authority.locator,
                    reviewed=True,
                )
            )
    return rows


def test_released_dataset_has_exact_frozen_45_case_membership() -> None:
    dataset = load_codex_golden_v1()

    assert dataset.dataset_id == "codex-golden-v1"
    assert dataset.dataset_version == "v1"
    assert len(dataset.cases) == CODEX_GOLDEN_CASE_COUNT == 45
    assert Counter(case.slice for case in dataset.cases) == Counter(EXPECTED_SLICE_COUNTS)
    assert dataset.case_membership == tuple(f"codex-v1-{number:03d}" for number in range(1, 46))
    assert len(set(dataset.fixture_thread_ids)) == 8
    assert len(set(dataset.fixture_item_ids)) == 54
    assert all(case.hard_negatives for case in dataset.cases)
    assert all(case.state_labels and case.temporal_labels for case in dataset.cases)
    assert all(case.evidence_provenance for case in dataset.cases)
    assert all(case.eligible_metrics and case.item_judgments for case in dataset.cases)
    assert sum(case.unanswerable for case in dataset.cases) == 5
    assert all(
        case.refusal_expected and not case.expected_items and not case.expected_threads
        for case in dataset.cases
        if case.unanswerable
    )

    with pytest.raises(ValidationError):
        dataset.dataset_id = "changed"  # type: ignore[misc]
    payload = dataset.cases[0].model_dump(mode="json")
    payload["extra"] = True
    with pytest.raises(ValidationError):
        CodexGoldenCase.model_validate(payload)


def test_manifest_is_content_addressed_canonical_and_has_no_mutable_artifacts() -> None:
    manifest_raw = (FIXTURE / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest_raw == _canonical(manifest) + b"\n"
    assert manifest["released"] is True
    assert manifest["case_count"] == 45
    assert len(manifest["files"]) == 11
    assert len(manifest["negative_route_counts"]) == 10
    assert sum(manifest["negative_route_counts"].values()) == 45
    assert manifest["false_validation_category_counts"] == {
        "claim_only": 1,
        "failed_exit": 1,
        "invoked_no_exit": 1,
        "mentioned_only": 1,
        "target_unknown": 1,
    }
    assert manifest["item_judgment_count"] == 100
    assert manifest["eligible_metric_counts"]["context_noise_rate_at_10"] == 45

    payload = dict(manifest)
    payload.pop("package_hash")
    assert manifest["package_hash"] == _digest(_canonical(payload))
    for entry in manifest["files"]:
        raw = (FIXTURE / entry["path"]).read_bytes()
        assert entry["sha256"] == _digest(raw)
        assert entry["size"] == len(raw)
        assert not Path(entry["path"]).is_absolute()
        assert not entry["path"].endswith(("-wal", "-shm", ".pyc"))
        assert "__pycache__" not in Path(entry["path"]).parts
        if entry["path"].endswith(".jsonl"):
            assert raw.endswith(b"\n")
            for line in raw.splitlines(keepends=True):
                assert line == _canonical(json.loads(line)) + b"\n"


def test_programmatic_fixture_is_consumed_by_existing_adapter_and_all_labels_exist(
    settings, tmp_path: Path
) -> None:
    dataset = load_codex_golden_v1()
    codex_home, project, parsed = _parse_materialized(settings, tmp_path)

    assert codex_home.is_relative_to(tmp_path)
    assert project.is_relative_to(tmp_path)
    assert len(parsed) == 8
    assert sum(len(session.items) for session in parsed) == 54
    threads = {session.thread.thread_id: session for session in parsed}
    items = {item.id: item for session in parsed for item in session.items}
    assert set(threads) == set(dataset.fixture_thread_ids)
    assert set(items) == set(dataset.fixture_item_ids)

    for case in dataset.cases:
        for expected in case.expected_threads:
            assert expected.thread_id in threads
            assert threads[expected.thread_id].thread.id == expected.locator
            assert threads[expected.thread_id].thread.status == expected.status
        for expected in case.expected_items:
            assert expected.item_id in items
            assert items[expected.item_id].source_locator == expected.locator
            assert items[expected.item_id].item_type == expected.item_type
        for truth in case.event_truth:
            assert truth.item_id in items
        for negative in case.hard_negatives:
            assert negative.item_id in items
            assert items[negative.item_id].source_locator == negative.locator
        for judgment in case.item_judgments:
            assert judgment.item_id in items
            assert items[judgment.item_id].source_locator == judgment.locator

    all_content = "\n".join(item.content for item in items.values())
    assert "private reasoning is deliberately ineligible evidence" not in all_content
    assert "supporting private reasoning is excluded" not in all_content
    assert sum(session.counters["excluded_reasoning"] for session in parsed) == 2
    truncated = items[
        "codex://thread/codex-golden-truncated/turn/truncated-turn/"
        "item/ToolResult%3Atruncated-read-result"
    ]
    assert truncated.metadata["truncated"] is True
    assert truncated.content.endswith("… [TRUNCATED]")
    assert any(
        truth.truncated and truth.warning for case in dataset.cases for truth in case.event_truth
    )

    current = threads["codex-golden-current"]
    archived = threads["codex-golden-archived"]
    current_goal = next(item for item in current.items if item.item_id == "current-goal")
    archived_goal = next(item for item in archived.items if item.item_id == "archived-goal")
    assert current_goal.content == archived_goal.content
    current_validations = {
        item.item_id: item.status for item in current.items if item.item_type == "ValidationResult"
    }
    assert current_validations == {
        "validation-current-test-old": "failed",
        "validation-current-test-new": "passed",
    }
    assert archived.thread.status == "failed"
    assert not any(
        item.item_type == "FileChange" and "archived-read-only" in item.id
        for item in archived.items
    )
    assert threads["codex-golden-subagent"].thread.metadata["source"]["subagent"] is True


def test_materialization_is_deterministic_and_portable(settings, tmp_path: Path) -> None:
    copied = tmp_path / "nested" / "portable" / "fixture"
    copied.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, copied)
    assert load_codex_golden_v1(copied).package_hash == load_codex_golden_v1().package_hash

    project = tmp_path / "project"
    project.mkdir()
    first = materialize_codex_session_fixture(
        tmp_path / "home-a", project_root=project, fixture_root=copied
    )
    second = materialize_codex_session_fixture(
        tmp_path / "home-b", project_root=project, fixture_root=copied
    )
    first_files = {
        path.relative_to(first).as_posix(): path.read_bytes()
        for path in first.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second).as_posix(): path.read_bytes()
        for path in second.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files
    _parse_materialized(settings, tmp_path / "second-parse")


def test_released_programmatic_builder_rebuilds_canonical_package_exactly(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    subprocess.run(
        [sys.executable, "-B", str(root / "build_fixture.py")],
        check=True,
        cwd=root,
    )
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert load_codex_golden_v1(root).package_hash == load_codex_golden_v1().package_hash


def test_loader_rejects_hash_tamper_and_unlisted_file(tmp_path: Path) -> None:
    tampered = _copy_fixture(tmp_path)
    with (tampered / "cases.jsonl").open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(CodexEvaluationError, match="hash/size mismatch"):
        load_codex_golden_v1(tampered)

    unlisted = _copy_fixture(tmp_path / "other")
    (unlisted / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(CodexEvaluationError, match="unlisted"):
        load_codex_golden_v1(unlisted)


def test_loader_rejects_manifest_hash_mismatch_and_readdressed_benign_tamper(
    tmp_path: Path,
) -> None:
    manifest_tamper = _copy_fixture(tmp_path)
    manifest = json.loads((manifest_tamper / "manifest.json").read_bytes())
    manifest["package_hash"] = "sha256:" + ("0" * 64)
    (manifest_tamper / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
    with pytest.raises(CodexEvaluationError, match="manifest package hash mismatch"):
        load_codex_golden_v1(manifest_tamper)

    readdressed = _copy_fixture(tmp_path / "other")
    relative = "cases.jsonl"
    rows = [json.loads(line) for line in (readdressed / relative).read_bytes().splitlines()]
    rows[0]["query"] = "Which released thread owns the current parser retry goal?"
    (readdressed / relative).write_bytes(b"".join(_canonical(row) + b"\n" for row in rows))
    _readdress(readdressed, relative)
    with pytest.raises(CodexEvaluationError, match="not the pinned released"):
        load_codex_golden_v1(readdressed)


@pytest.mark.parametrize(
    "unsafe_kind",
    ["credential", "email", "payment", "absolute_path"],
)
def test_loader_rejects_secret_and_absolute_path_even_when_readdressed(
    tmp_path: Path, unsafe_kind: str
) -> None:
    root = _copy_fixture(tmp_path)
    relative = "sessions/privacy.jsonl"
    rows = [json.loads(line) for line in (root / relative).read_bytes().splitlines()]
    if unsafe_kind == "credential":
        shaped_value = "gh" + "p_" + ("A" * 24)
        rows[2]["item"]["message"] += " " + shaped_value
    elif unsafe_kind == "email":
        rows[2]["item"]["message"] += " " + "fixture-user" + "@" + "example.test"
    elif unsafe_kind == "payment":
        rows[2]["item"]["message"] += " " + ("4111" * 4)
    else:
        rows[0]["cwd"] = "/Users/" + "fixture/private"
    (root / relative).write_bytes(b"".join(_canonical(row) + b"\n" for row in rows))
    _readdress(root, relative)
    with pytest.raises(CodexEvaluationError, match="forbidden"):
        load_codex_golden_v1(root)


def test_contracts_fail_closed_on_identity_alias_and_truth_contradictions() -> None:
    dataset = load_codex_golden_v1()
    item_payload = dataset.cases[0].expected_items[0].model_dump(mode="json")
    item_payload["repository_path"] = "./src/parser.py"
    with pytest.raises(ValidationError):
        ExpectedItem.model_validate(item_payload)

    truth_payload = {
        "truth_id": "truncated-without-warning",
        "thread_id": "codex-golden-truncated",
        "item_id": (
            "codex://thread/codex-golden-truncated/turn/truncated-turn/"
            "item/ToolResult%3Atruncated-read-result"
        ),
        "kind": "tool_result",
        "truncated": True,
    }
    with pytest.raises(ValidationError, match="warning"):
        EventTruth.model_validate(truth_payload)
    truth_payload["kind"] = "reasoning"
    truth_payload["warning"] = "not eligible"
    with pytest.raises(ValidationError):
        EventTruth.model_validate(truth_payload)

    cross_thread_truth = dataset.cases[6].event_truth[0].model_dump(mode="json")
    cross_thread_truth["thread_id"] = "codex-golden-decision"
    with pytest.raises(ValidationError, match="outside its declared thread"):
        EventTruth.model_validate(cross_thread_truth)

    case_payload = dataset.cases[0].model_dump(mode="json")
    case_payload["slice"] = "unknown slice"
    with pytest.raises(ValidationError):
        CodexGoldenCase.model_validate(case_payload)

    case_payload = dataset.cases[0].model_dump(mode="json")
    case_payload["expected_items"].append(case_payload["expected_items"][0])
    with pytest.raises(ValidationError, match="duplicate positive"):
        CodexGoldenCase.model_validate(case_payload)

    case_payload = dataset.cases[0].model_dump(mode="json")
    case_payload["expected_threads"] = [
        dataset.cases[1].expected_threads[0].model_dump(mode="json")
    ]
    with pytest.raises(ValidationError, match="cross-thread"):
        CodexGoldenCase.model_validate(case_payload)

    case_payload = dataset.cases[40].model_dump(mode="json")
    case_payload["expected_threads"] = [
        dataset.cases[0].expected_threads[0].model_dump(mode="json")
    ]
    case_payload["expected_items"] = [dataset.cases[0].expected_items[0].model_dump(mode="json")]
    with pytest.raises(ValidationError, match="unanswerable"):
        CodexGoldenCase.model_validate(case_payload)

    case_payload = dataset.cases[0].model_dump(mode="json")
    positive = case_payload["expected_items"][0]
    case_payload["hard_negatives"] = [
        {
            "item_id": positive["item_id"],
            "locator": positive["locator"],
            "old_attempt": False,
            "reason": "failed_attempt",
            "thread_id": positive["thread_id"],
        }
    ]
    with pytest.raises(ValidationError, match="overlap"):
        CodexGoldenCase.model_validate(case_payload)


def test_dataset_rejects_duplicate_cases_items_and_missing_fixture_labels() -> None:
    dataset = load_codex_golden_v1()
    payload = dataset.model_dump(mode="json")
    payload["cases"][1] = json.loads(json.dumps(payload["cases"][0]))
    payload["cases"][1]["case_id"] = "codex-v1-002"
    with pytest.raises(ValidationError, match="duplicate positive item"):
        GoldenDataset.model_validate(payload)

    payload = dataset.model_dump(mode="json")
    payload["cases"][1]["case_id"] = payload["cases"][0]["case_id"]
    with pytest.raises(ValidationError, match="duplicate Golden case"):
        GoldenDataset.model_validate(payload)

    payload = dataset.model_dump(mode="json")
    missing = payload["cases"][0]["expected_items"][0]["item_id"]
    missing_index = payload["fixture_item_ids"].index(missing)
    payload["fixture_item_ids"].pop(missing_index)
    payload["fixture_item_locators"].pop(missing_index)
    with pytest.raises(ValidationError, match="absent from fixture"):
        GoldenDataset.model_validate(payload)


def test_released_eligibility_is_exact_and_cannot_be_deleted_duplicated_or_invented() -> None:
    dataset = load_codex_golden_v1()

    payload = dataset.model_dump(mode="json")
    payload["cases"][0]["eligible_metrics"].pop()
    with pytest.raises(ValidationError, match="metric eligibility is inconsistent"):
        GoldenDataset.model_validate(payload)

    case_payload = dataset.cases[0].model_dump(mode="json")
    case_payload["eligible_metrics"].append(case_payload["eligible_metrics"][0])
    with pytest.raises(ValidationError, match="duplicate eligible metric"):
        CodexGoldenCase.model_validate(case_payload)

    case_payload = dataset.cases[0].model_dump(mode="json")
    case_payload["eligible_metrics"][0] = "invented_metric"
    with pytest.raises(ValidationError):
        CodexGoldenCase.model_validate(case_payload)

    payload = dataset.model_dump(mode="json")
    positive = next(
        item for item in payload["cases"][0]["item_judgments"] if item["relevance"] == "positive"
    )
    positive["context_noise"] = True
    with pytest.raises(ValidationError, match="released authority hash mismatch"):
        GoldenDataset.model_validate(payload)


def test_released_negative_routes_and_false_validation_denominators_are_complete() -> None:
    dataset = load_codex_golden_v1()
    route_counts = Counter(
        negative.reason for case in dataset.cases for negative in case.hard_negatives
    )
    assert set(route_counts) == EXPECTED_NEGATIVE_ROUTES
    assert sum(route_counts.values()) == 45
    assert min(route_counts.values()) >= 4
    negative_item_counts = Counter(
        negative.item_id for case in dataset.cases for negative in case.hard_negatives
    )
    assert max(negative_item_counts.values()) <= 5
    assert all(len(case.hard_negatives) == 1 for case in dataset.cases)
    for case in dataset.cases:
        negative = case.hard_negatives[0]
        judgment = next(item for item in case.item_judgments if item.item_id == negative.item_id)
        assert judgment.relevance == "hard_negative"
        assert judgment.context_noise is True

    memberships = {
        negative.reason: negative for case in dataset.cases for negative in case.hard_negatives
    }
    assert (
        "/Plan%3A"
        in memberships[
            next(route for route in EXPECTED_NEGATIVE_ROUTES if route.value == "plan_only")
        ].item_id
    )
    assert (
        "current-test-"
        in memberships[
            next(
                route
                for route in EXPECTED_NEGATIVE_ROUTES
                if route.value == "same_command_multiple_statuses"
            )
        ].item_id
    )
    assert (
        "truncated-read-result"
        in memberships[
            next(
                route
                for route in EXPECTED_NEGATIVE_ROUTES
                if route.value == "truncated_tool_result"
            )
        ].item_id
    )

    false_labels = [
        (case, judgment)
        for case in dataset.cases
        for judgment in case.item_judgments
        if judgment.false_validation_category is not None
    ]
    assert {
        judgment.false_validation_category for _, judgment in false_labels
    } == EXPECTED_FALSE_VALIDATION_CATEGORIES
    assert len(false_labels) == 5
    assert all(
        CodexMetric.FALSE_VALIDATED_RATE_AT_10 in case.eligible_metrics for case, _ in false_labels
    )


def test_evaluator_computes_metrics_from_reviewed_rows_deterministically() -> None:
    dataset = load_codex_golden_v1()
    rows = _perfect_rows(dataset)

    first = evaluate_reviewed_codex_retrieval(dataset, rows)
    second = evaluate_reviewed_codex_retrieval(
        dataset,
        [row.model_dump(mode="json") for row in rows],
        case_membership=dataset.case_membership,
    )
    assert first == second
    by_name = {metric.name: metric for metric in first}
    assert set(by_name) == {
        "thread_recall_at_5",
        "episode_recall_at_5",
        "item_recall_at_10",
        "mrr_at_10",
        "goal_recall_at_10",
        "decision_recall_at_10",
        "harmful_old_attempt_rate_at_10",
        "event_order_accuracy_at_10",
        "call_result_link_accuracy_at_10",
        "patch_accuracy_at_10",
        "validation_accuracy_at_10",
        "false_validated_rate_at_10",
        "outcome_accuracy_at_10",
        "context_duplicate_rate_at_10",
        "context_noise_rate_at_10",
    }
    assert by_name["thread_recall_at_5"].value == 1.0
    assert by_name["episode_recall_at_5"].value == 1.0
    assert by_name["item_recall_at_10"].value == 1.0
    assert by_name["goal_recall_at_10"].denominator == 7
    assert by_name["decision_recall_at_10"].denominator == 7
    assert by_name["harmful_old_attempt_rate_at_10"].value == 0.0
    assert by_name["event_order_accuracy_at_10"].value == 1.0
    assert by_name["call_result_link_accuracy_at_10"].value == 1.0
    assert by_name["patch_accuracy_at_10"].value == 1.0
    assert by_name["validation_accuracy_at_10"].value == 1.0
    assert by_name["false_validated_rate_at_10"].value == 0.0
    assert by_name["outcome_accuracy_at_10"].value == 1.0
    assert by_name["context_duplicate_rate_at_10"].value == 0.0
    assert by_name["context_noise_rate_at_10"].value == 0.0
    assert all(metric.status == MetricStatus.AVAILABLE for metric in first)


def test_row_truth_forgery_is_rejected_and_false_validation_uses_released_authority() -> None:
    dataset = load_codex_golden_v1()
    rows: list[ReviewedRetrievalRow] = []
    for case in dataset.cases:
        authority = next(
            (item for item in case.item_judgments if item.false_validation_category is not None),
            None,
        )
        if authority is None:
            continue
        rows.append(
            ReviewedRetrievalRow(
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.dataset_version,
                package_hash=dataset.package_hash,
                case_id=case.case_id,
                rank=1,
                thread_id=authority.thread_id,
                item_id=authority.item_id,
                locator=authority.locator,
                reviewed=True,
            )
        )

    metrics = {item.name: item for item in evaluate_reviewed_codex_retrieval(dataset, rows)}
    false_validated = metrics["false_validated_rate_at_10"]
    assert false_validated.status == MetricStatus.AVAILABLE
    assert false_validated.numerator == 2
    assert false_validated.denominator == 5

    forged = rows[0].model_dump(mode="json")
    forged.update(
        {
            "episode_id": "codex://thread/forged/turn/forged/episode/1",
            "call_id": "forged-call",
            "patch_applied": True,
            "validation_status": "passed",
            "false_validated": False,
            "outcome_correct": True,
            "context_noise": False,
        }
    )
    with pytest.raises(CodexEvaluationError, match="invalid reviewed retrieval row"):
        evaluate_reviewed_codex_retrieval(dataset, [forged])


def test_evaluator_zero_rows_never_becomes_full_score_or_fake_available() -> None:
    dataset = load_codex_golden_v1()
    metrics = evaluate_reviewed_codex_retrieval(dataset, [])
    by_name = {metric.name: metric for metric in metrics}
    for name in (
        "thread_recall_at_5",
        "episode_recall_at_5",
        "item_recall_at_10",
        "mrr_at_10",
        "goal_recall_at_10",
        "decision_recall_at_10",
    ):
        assert by_name[name].status == MetricStatus.AVAILABLE
        assert by_name[name].value == 0.0
    for name in (
        "harmful_old_attempt_rate_at_10",
        "false_validated_rate_at_10",
        "context_duplicate_rate_at_10",
        "context_noise_rate_at_10",
    ):
        assert by_name[name].status == MetricStatus.AVAILABLE
        assert by_name[name].value == 0.0
        assert by_name[name].denominator > 0
    assert not any(metric.value == 1.0 for metric in metrics)


def test_evaluator_measures_duplicate_and_noise_from_released_authority() -> None:
    dataset = load_codex_golden_v1()
    case = dataset.cases[0]
    expected = case.expected_items[0]
    common = {
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "package_hash": dataset.package_hash,
        "case_id": dataset.cases[0].case_id,
        "thread_id": expected.thread_id,
        "item_id": expected.item_id,
        "locator": expected.locator,
        "reviewed": True,
    }
    hard_negative = case.hard_negatives[0]
    rows = [
        ReviewedRetrievalRow(rank=1, **common),
        ReviewedRetrievalRow(rank=2, **common),
        ReviewedRetrievalRow(
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            package_hash=dataset.package_hash,
            case_id=case.case_id,
            rank=3,
            thread_id=hard_negative.thread_id,
            item_id=hard_negative.item_id,
            locator=hard_negative.locator,
            reviewed=True,
        ),
    ]
    metrics = {item.name: item for item in evaluate_reviewed_codex_retrieval(dataset, rows)}
    assert metrics["context_duplicate_rate_at_10"].numerator == 1
    assert metrics["context_duplicate_rate_at_10"].denominator == 45
    assert metrics["context_noise_rate_at_10"].numerator == 1
    assert metrics["context_noise_rate_at_10"].denominator == 45
    assert metrics["patch_accuracy_at_10"].status == MetricStatus.AVAILABLE
    assert metrics["validation_accuracy_at_10"].status == MetricStatus.AVAILABLE
    assert metrics["outcome_accuracy_at_10"].status == MetricStatus.AVAILABLE


def test_evaluator_rejects_unknown_identity_rank_and_membership() -> None:
    dataset = load_codex_golden_v1()
    row = _perfect_rows(dataset)[0]

    with pytest.raises(CodexEvaluationError, match="45-case denominator"):
        evaluate_reviewed_codex_retrieval(
            dataset,
            [row],
            case_membership=dataset.case_membership[:-1],
        )
    with pytest.raises(CodexEvaluationError, match="45-case denominator"):
        evaluate_reviewed_codex_retrieval(dataset, [row], case_membership=[])
    invalid_memberships = []
    duplicate = list(dataset.case_membership)
    duplicate[-1] = duplicate[0]
    invalid_memberships.append(duplicate)
    reordered = list(dataset.case_membership)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    invalid_memberships.append(reordered)
    unknown = list(dataset.case_membership)
    unknown[-1] = "codex-v1-unknown"
    invalid_memberships.append(unknown)
    for membership in invalid_memberships:
        with pytest.raises(CodexEvaluationError, match="45-case denominator"):
            evaluate_reviewed_codex_retrieval(
                dataset,
                [row],
                case_membership=membership,
            )
    unknown_case = row.model_dump(mode="json")
    unknown_case["case_id"] = "codex-v1-unknown"
    with pytest.raises(CodexEvaluationError, match="unknown case"):
        evaluate_reviewed_codex_retrieval(dataset, [unknown_case])
    unknown_item = row.model_dump(mode="json")
    unknown_item["item_id"] = (
        "codex://thread/codex-golden-current/turn/current-turn/item/AgentMessage%3Aunknown"
    )
    unknown_item["locator"] = unknown_item["item_id"] + "#event=1"
    with pytest.raises(CodexEvaluationError, match="absent from released fixture"):
        evaluate_reviewed_codex_retrieval(dataset, [unknown_item])
    judged_ids = {item.item_id for item in dataset.cases[0].item_judgments}
    unjudged_id = next(item_id for item_id in dataset.fixture_item_ids if item_id not in judged_ids)
    unjudged = row.model_dump(mode="json")
    unjudged["item_id"] = unjudged_id
    unjudged["locator"] = dataset.fixture_locator_by_item[unjudged_id]
    unjudged["thread_id"] = unjudged_id.split("/", 4)[3]
    with pytest.raises(CodexEvaluationError, match="no released case/item judgment"):
        evaluate_reviewed_codex_retrieval(dataset, [unjudged])
    aliased_locator = row.model_dump(mode="json")
    aliased_locator["locator"] = row.item_id + "#event=999"
    with pytest.raises(CodexEvaluationError, match="locator differs"):
        evaluate_reviewed_codex_retrieval(dataset, [aliased_locator])
    duplicate_rank = row.model_copy()
    with pytest.raises(CodexEvaluationError, match="duplicate rank"):
        evaluate_reviewed_codex_retrieval(dataset, [row, duplicate_rank])


@pytest.mark.parametrize("target_kind", ["external", "internal"])
def test_loader_rejects_listed_symlink_alias_with_matching_bytes(
    tmp_path: Path,
    target_kind: str,
) -> None:
    root = _copy_fixture(tmp_path)
    listed = root / "cases.jsonl"
    raw = listed.read_bytes()
    listed.unlink()
    if target_kind == "external":
        alias_target = tmp_path / "same-cases.jsonl"
    else:
        alias_target = root / "same-cases.jsonl"
    alias_target.write_bytes(raw)
    listed.symlink_to(alias_target)

    with pytest.raises(CodexEvaluationError, match="symlink alias is forbidden"):
        load_codex_golden_v1(root)


def test_loader_rejects_symlinked_listed_path_component(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    sessions = root / "sessions"
    real_sessions = tmp_path / "same-sessions"
    shutil.move(str(sessions), real_sessions)
    sessions.symlink_to(real_sessions, target_is_directory=True)

    with pytest.raises(CodexEvaluationError, match="symlink alias is forbidden"):
        load_codex_golden_v1(root)


def test_foundation_probe_is_read_only_and_makes_no_baseline_claim() -> None:
    summary = probe_codex_evaluation_foundation()
    assert summary.case_count == 45
    assert summary.immutable_package_verified is True
    assert summary.formal_database_accessed is False
    assert summary.evaluation_run_created is False
    assert summary.baseline_metrics_run is False
    assert summary.baseline_qualified is False
    assert summary.item_judgment_count == 100
    assert summary.hard_negative_route_count == 10
    assert summary.false_validation_category_count == 5
    assert summary.release_posture == "FOUNDATION_ONLY"
