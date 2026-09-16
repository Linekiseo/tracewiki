"""Deterministically rebuild the released Golden JSONL and content manifest."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
DATASET_ID = "codex-golden-v1"
DATASET_VERSION = "v1"
SCHEMA_VERSION = "codex-evaluation-foundation-v1"

SLICE_COUNTS = {
    "command/validation": 7,
    "failure/retry": 5,
    "multi-thread comparison": 4,
    "patch/file change": 6,
    "process trace": 6,
    "rationale/decision": 6,
    "thread/goal location": 6,
    "unresolved/unanswerable/privacy": 5,
}

THREADS = {
    "archived": {
        "id": "codex-golden-archived",
        "source": "archived_sessions/archived.jsonl",
        "status": "failed",
        "turn": "archived-turn",
    },
    "comparison": {
        "id": "codex-golden-comparison",
        "source": "sessions/comparison.jsonl",
        "status": "completed",
        "turn": "comparison-turn",
    },
    "current": {
        "id": "codex-golden-current",
        "source": "sessions/current.jsonl",
        "status": "completed",
        "turn": "current-turn",
    },
    "decision": {
        "id": "codex-golden-decision",
        "source": "sessions/decision.jsonl",
        "status": "completed",
        "turn": "decision-turn",
    },
    "plan": {
        "id": "codex-golden-plan",
        "source": "sessions/plan.jsonl",
        "status": "completed",
        "turn": "plan-turn",
    },
    "privacy": {
        "id": "codex-golden-privacy",
        "source": "sessions/privacy.jsonl",
        "status": "completed",
        "turn": "privacy-turn",
    },
    "subagent": {
        "id": "codex-golden-subagent",
        "source": "sessions/subagent.jsonl",
        "status": "completed",
        "turn": "subagent-turn",
    },
    "truncated": {
        "id": "codex-golden-truncated",
        "source": "sessions/truncated.jsonl",
        "status": "completed",
        "turn": "truncated-turn",
    },
}

# key -> thread, normalized adapter type, raw item id, source line, repository path
ITEMS = {
    "archived_claim": ("archived", "AgentMessage", "archived-claim", 9, None),
    "archived_episode": ("archived", "DevelopmentEpisode", "1", 0, None),
    "archived_goal": ("archived", "UserGoal", "archived-goal", 3, "src/parser.py"),
    "archived_patch": ("archived", "Patch", "archived-patch-call", 6, "src/parser.py"),
    "archived_patch_result": (
        "archived",
        "ToolResult",
        "archived-patch-result",
        7,
        "src/parser.py",
    ),
    "archived_plan": ("archived", "Plan", "archived-plan", 4, "src/parser.py"),
    "archived_read": (
        "archived",
        "CommandExecution",
        "archived-read-only",
        8,
        "src/parser.py",
    ),
    "archived_test": (
        "archived",
        "CommandExecution",
        "archived-test",
        5,
        "tests/test_parser.py",
    ),
    "archived_validation": (
        "archived",
        "ValidationResult",
        "validation-archived-test",
        5,
        "tests/test_parser.py",
    ),
    "comparison_decision": (
        "comparison",
        "AgentMessage",
        "comparison-decision",
        5,
        "src/parser.py",
    ),
    "comparison_episode": ("comparison", "DevelopmentEpisode", "1", 0, None),
    "comparison_goal": (
        "comparison",
        "UserGoal",
        "comparison-goal",
        3,
        "src/parser.py",
    ),
    "comparison_outcome": (
        "comparison",
        "AgentMessage",
        "comparison-outcome",
        6,
        "src/parser.py",
    ),
    "comparison_read": (
        "comparison",
        "CommandExecution",
        "comparison-read",
        4,
        "src/parser.py",
    ),
    "current_change": (
        "current",
        "FileChange",
        "current-change",
        9,
        "src/parser.py",
    ),
    "current_episode": ("current", "DevelopmentEpisode", "1", 0, None),
    "current_goal": ("current", "UserGoal", "current-goal", 3, "src/parser.py"),
    "current_outcome": (
        "current",
        "AgentMessage",
        "current-outcome",
        11,
        "src/parser.py",
    ),
    "current_patch": (
        "current",
        "Patch",
        "current-patch-call-old",
        6,
        "src/parser.py",
    ),
    "current_patch_result": (
        "current",
        "ToolResult",
        "current-patch-result-old",
        7,
        "src/parser.py",
    ),
    "current_plan": ("current", "Plan", "current-plan", 4, "src/parser.py"),
    "current_read": (
        "current",
        "CommandExecution",
        "current-read-retry",
        8,
        "src/parser.py",
    ),
    "current_test_new": (
        "current",
        "CommandExecution",
        "current-test-new",
        10,
        "tests/test_parser.py",
    ),
    "current_test_old": (
        "current",
        "CommandExecution",
        "current-test-old",
        5,
        "tests/test_parser.py",
    ),
    "current_validation_new": (
        "current",
        "ValidationResult",
        "validation-current-test-new",
        10,
        "tests/test_parser.py",
    ),
    "current_validation_old": (
        "current",
        "ValidationResult",
        "validation-current-test-old",
        5,
        "tests/test_parser.py",
    ),
    "decision_change": (
        "decision",
        "FileChange",
        "decision-change",
        7,
        "src/ranking.py",
    ),
    "decision_episode": ("decision", "DevelopmentEpisode", "1", 0, None),
    "decision_goal": (
        "decision",
        "UserGoal",
        "decision-goal",
        3,
        "src/ranking.py",
    ),
    "decision_outcome": (
        "decision",
        "AgentMessage",
        "decision-outcome",
        9,
        "src/ranking.py",
    ),
    "decision_rationale": (
        "decision",
        "AgentMessage",
        "decision-rationale",
        6,
        "src/ranking.py",
    ),
    "decision_read_call": (
        "decision",
        "CommandExecution",
        "decision-read-call",
        4,
        "src/ranking.py",
    ),
    "decision_read_result": (
        "decision",
        "ToolResult",
        "decision-read-result",
        5,
        "src/ranking.py",
    ),
    "decision_test": (
        "decision",
        "CommandExecution",
        "decision-test",
        8,
        "tests/test_ranking.py",
    ),
    "decision_validation": (
        "decision",
        "ValidationResult",
        "validation-decision-test",
        8,
        "tests/test_ranking.py",
    ),
    "plan_episode": ("plan", "DevelopmentEpisode", "1", 0, None),
    "plan_goal": ("plan", "UserGoal", "plan-goal", 3, "src/cache.py"),
    "plan_proposal": ("plan", "AgentMessage", "plan-proposal", 6, "src/cache.py"),
    "plan_read": ("plan", "CommandExecution", "plan-read-only", 5, "src/cache.py"),
    "plan_steps": ("plan", "Plan", "plan-steps", 4, "src/cache.py"),
    "privacy_episode": ("privacy", "DevelopmentEpisode", "1", 0, None),
    "privacy_goal": ("privacy", "UserGoal", "privacy-goal", 3, "src/security.py"),
    "privacy_outcome": (
        "privacy",
        "AgentMessage",
        "privacy-outcome",
        6,
        "src/security.py",
    ),
    "privacy_read": (
        "privacy",
        "CommandExecution",
        "privacy-read",
        5,
        "src/security.py",
    ),
    "subagent_episode": ("subagent", "DevelopmentEpisode", "1", 0, None),
    "subagent_goal": (
        "subagent",
        "UserGoal",
        "subagent-goal",
        3,
        "src/parser.py",
    ),
    "subagent_outcome": (
        "subagent",
        "AgentMessage",
        "subagent-outcome",
        7,
        "src/parser.py",
    ),
    "subagent_read_call": (
        "subagent",
        "CommandExecution",
        "subagent-read-call",
        5,
        "src/parser.py",
    ),
    "subagent_read_result": (
        "subagent",
        "ToolResult",
        "subagent-read-result",
        6,
        "src/parser.py",
    ),
    "truncated_episode": ("truncated", "DevelopmentEpisode", "1", 0, None),
    "truncated_goal": (
        "truncated",
        "UserGoal",
        "truncated-goal",
        3,
        "build.log",
    ),
    "truncated_outcome": (
        "truncated",
        "AgentMessage",
        "truncated-outcome",
        6,
        "build.log",
    ),
    "truncated_read_call": (
        "truncated",
        "CommandExecution",
        "truncated-read-call",
        4,
        "build.log",
    ),
    "truncated_read_result": (
        "truncated",
        "ToolResult",
        "truncated-read-result",
        5,
        "build.log",
    ),
}


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def build_truncated_session() -> None:
    captured_prefix = "".join(
        f"worker_retry line {number:03d} observed; " for number in range(1, 901)
    )
    captured_prefix += "end of captured prefix"
    rows = [
        {
            "cwd": "${PROJECT_ROOT}",
            "thread_id": "codex-golden-truncated",
            "timestamp": "2026-07-04T11:00:00Z",
            "type": "thread.started",
        },
        {
            "timestamp": "2026-07-04T11:00:01Z",
            "turn_id": "truncated-turn",
            "type": "turn.started",
        },
        {
            "item": {
                "id": "truncated-goal",
                "message": (
                    "Inspect the bounded build log for the worker retry without "
                    "inferring unseen output."
                ),
                "type": "user_message",
            },
            "timestamp": "2026-07-04T11:00:02Z",
            "type": "item.completed",
        },
        {
            "item": {
                "arguments": '{"cmd":"rg -n worker_retry build.log"}',
                "call_id": "truncated-read",
                "id": "truncated-read-call",
                "name": "exec_command",
                "type": "function_call",
            },
            "timestamp": "2026-07-04T11:00:03Z",
            "type": "item.completed",
        },
        {
            "item": {
                "call_id": "truncated-read",
                "id": "truncated-read-result",
                "output": captured_prefix,
                "type": "function_call_output",
            },
            "timestamp": "2026-07-04T11:00:04Z",
            "type": "item.completed",
        },
        {
            "item": {
                "id": "truncated-outcome",
                "text": "Only the captured prefix is supported; the unseen suffix remains unresolved.",
                "type": "agent_message",
            },
            "timestamp": "2026-07-04T11:00:05Z",
            "type": "item.completed",
        },
        {
            "timestamp": "2026-07-04T11:00:06Z",
            "turn_id": "truncated-turn",
            "type": "turn.completed",
        },
    ]
    (ROOT / "sessions" / "truncated.jsonl").write_bytes(
        b"".join(canonical(row) + b"\n" for row in rows)
    )


def item_identity(key: str) -> str:
    thread_key, item_type, raw_id, _, _ = ITEMS[key]
    thread = THREADS[thread_key]
    prefix = (
        f"codex://thread/{quote(thread['id'], safe='-._')}/turn/{quote(thread['turn'], safe='-._')}"
    )
    if item_type == "DevelopmentEpisode":
        return f"{prefix}/episode/{raw_id}"
    item_key = quote(f"{item_type}:{raw_id}", safe="-._")
    return f"{prefix}/item/{item_key}"


def item_locator(key: str) -> str:
    _, item_type, _, line, _ = ITEMS[key]
    identity = item_identity(key)
    return identity if item_type == "DevelopmentEpisode" else f"{identity}#event={line}"


def expected_thread(thread_key: str) -> dict[str, Any]:
    thread = THREADS[thread_key]
    return {
        "episode_id": item_identity(f"{thread_key}_episode"),
        "locator": f"codex://thread/{quote(thread['id'], safe='-._')}",
        "source_file": thread["source"],
        "status": thread["status"],
        "thread_id": thread["id"],
    }


def expected_item(key: str) -> dict[str, Any]:
    thread_key, item_type, _, line, repository_path = ITEMS[key]
    thread = THREADS[thread_key]
    value = {
        "item_id": item_identity(key),
        "item_type": item_type,
        "line": max(1, line),
        "locator": item_locator(key),
        "source_file": thread["source"],
        "thread_id": thread["id"],
        "turn_id": thread["turn"],
    }
    if repository_path:
        value["repository_path"] = repository_path
    return value


def provenance(key: str) -> dict[str, Any]:
    thread_key, _, _, line, _ = ITEMS[key]
    return {
        "item_id": item_identity(key),
        "line_end": max(1, line),
        "line_start": max(1, line),
        "source_file": THREADS[thread_key]["source"],
        "thread_id": THREADS[thread_key]["id"],
    }


NEGATIVE_ROUTE_ORDER = (
    "failed_attempt",
    "plan_only",
    "assistant_claim_without_exit",
    "patch_failure",
    "read_only_not_change",
    "same_command_multiple_statuses",
    "truncated_tool_result",
    "archived_duplicate",
    "subagent_same_topic",
    "privacy_sensitive",
)

NEGATIVE_ROUTE_CANDIDATES = {
    "failed_attempt": ("archived_validation", "current_validation_old", "archived_test"),
    "plan_only": ("plan_steps", "plan_proposal", "plan_goal", "plan_read"),
    "assistant_claim_without_exit": ("archived_claim",),
    "patch_failure": (
        "current_patch_result",
        "archived_patch_result",
        "current_patch",
        "archived_patch",
    ),
    "read_only_not_change": ("archived_read", "plan_read", "current_read", "comparison_read"),
    "same_command_multiple_statuses": (
        "current_test_old",
        "current_test_new",
        "current_validation_old",
        "current_validation_new",
    ),
    "truncated_tool_result": ("truncated_read_result",),
    "archived_duplicate": ("archived_goal", "archived_claim"),
    "subagent_same_topic": (
        "subagent_outcome",
        "subagent_read_result",
        "subagent_goal",
        "subagent_read_call",
    ),
    "privacy_sensitive": ("privacy_outcome", "privacy_read", "privacy_goal"),
}

CASE_NEGATIVE_ROUTE_OVERRIDES = {
    4: "truncated_tool_result",
    6: "read_only_not_change",
    16: "patch_failure",
    17: "assistant_claim_without_exit",
    25: "same_command_multiple_statuses",
    26: "archived_duplicate",
    27: "same_command_multiple_statuses",
    28: "same_command_multiple_statuses",
    29: "same_command_multiple_statuses",
    36: "subagent_same_topic",
    43: "truncated_tool_result",
}

CASE_NEGATIVE_ITEM_OVERRIDES = {
    25: "current_test_new",
    27: "current_test_old",
    28: "current_validation_new",
    29: "current_validation_old",
}

OLD_ATTEMPT_ITEMS = {key for key, value in ITEMS.items() if value[0] == "archived"} | {
    "current_patch",
    "current_patch_result",
    "current_test_old",
    "current_validation_old",
}

METRIC_ORDER = (
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
)


def negative(key: str, *, reason: str) -> dict[str, Any]:
    thread_key = ITEMS[key][0]
    return {
        "item_id": item_identity(key),
        "locator": item_locator(key),
        "old_attempt": key in OLD_ATTEMPT_ITEMS,
        "reason": reason,
        "thread_id": THREADS[thread_key]["id"],
    }


def designed_negative(number: int, forbidden: set[str]) -> tuple[str, dict[str, Any]]:
    reason = CASE_NEGATIVE_ROUTE_OVERRIDES.get(
        number,
        NEGATIVE_ROUTE_ORDER[(number - 1) % len(NEGATIVE_ROUTE_ORDER)],
    )
    override = CASE_NEGATIVE_ITEM_OVERRIDES.get(number)
    key = override or next(
        candidate
        for candidate in NEGATIVE_ROUTE_CANDIDATES[reason]
        if item_identity(candidate) not in forbidden
    )
    if item_identity(key) in forbidden:
        raise RuntimeError("designed hard negative overlaps positive/event truth")
    return key, negative(key, reason=reason)


def truth(
    truth_id: str,
    key: str,
    kind: str,
    **values: Any,
) -> dict[str, Any]:
    value = {
        "item_id": item_identity(key),
        "kind": kind,
        "thread_id": THREADS[ITEMS[key][0]]["id"],
        "truth_id": truth_id,
    }
    value.update(values)
    return value


def judgment(
    case_id: str,
    key: str,
    *,
    relevance: str,
    state_label: str,
    temporal_label: str,
    context_noise: bool,
    **values: Any,
) -> dict[str, Any]:
    thread_key = ITEMS[key][0]
    value = {
        "context_noise": context_noise,
        "item_id": item_identity(key),
        "judgment_id": f"judgment-{case_id.removeprefix('codex-v1-')}-{key}",
        "locator": item_locator(key),
        "relevance": relevance,
        "state_label": state_label,
        "temporal_label": temporal_label,
        "thread_id": THREADS[thread_key]["id"],
    }
    value.update(values)
    return value


def _key_by_item_id(item_id: str) -> str:
    return next(key for key in ITEMS if item_identity(key) == item_id)


def _negative_state(reason: str) -> str:
    return {
        "failed_attempt": "failed",
        "plan_only": "plan_only",
        "assistant_claim_without_exit": "claim_only",
        "patch_failure": "failed",
        "read_only_not_change": "read_only",
        "same_command_multiple_statuses": "counter_evidence",
        "truncated_tool_result": "truncated",
        "archived_duplicate": "archived_duplicate",
        "subagent_same_topic": "supporting",
        "privacy_sensitive": "privacy_safe",
    }[reason]


def _case_item_judgments(
    value: dict[str, Any],
    *,
    target: str | None,
) -> list[dict[str, Any]]:
    case_id = value["case_id"]
    by_item: dict[str, dict[str, Any]] = {}
    if target is not None:
        target_judgment = judgment(
            case_id,
            target,
            relevance="positive",
            state_label=value["state_labels"][0],
            temporal_label=value["temporal_labels"][0],
            context_noise=False,
            episode_id=value["expected_threads"][0]["episode_id"],
        )
        by_item[target_judgment["item_id"]] = target_judgment

    for event in value["event_truth"]:
        key = _key_by_item_id(event["item_id"])
        authority = by_item.get(event["item_id"]) or judgment(
            case_id,
            key,
            relevance="supporting",
            state_label=value["state_labels"][0],
            temporal_label=value["temporal_labels"][0],
            context_noise=False,
        )
        if event.get("call_id") is not None:
            authority["call_id"] = event["call_id"]
        if event.get("patch_applied") is not None:
            authority["patch_applied"] = event["patch_applied"]
        if event["kind"] == "validation":
            authority["validation_status"] = "passed" if event["exit_code"] == 0 else "failed"
        if event["kind"] == "outcome":
            authority["outcome_correct"] = True
        by_item[event["item_id"]] = authority

    negative_value = value["hard_negatives"][0]
    negative_key = _key_by_item_id(negative_value["item_id"])
    negative_state = _negative_state(negative_value["reason"])
    negative_temporal = "historical" if negative_value["old_attempt"] else "current"
    value["state_labels"] = list(dict.fromkeys([*value["state_labels"], negative_state]))
    value["temporal_labels"] = list(dict.fromkeys([*value["temporal_labels"], negative_temporal]))
    negative_judgment = judgment(
        case_id,
        negative_key,
        relevance="hard_negative",
        state_label=negative_state,
        temporal_label=negative_temporal,
        context_noise=True,
    )
    by_item[negative_judgment["item_id"]] = negative_judgment
    return sorted(by_item.values(), key=lambda item: item["item_id"])


def _eligible_metrics(value: dict[str, Any]) -> list[str]:
    eligible = {"context_duplicate_rate_at_10", "context_noise_rate_at_10"}
    if not value["unanswerable"]:
        eligible.update(
            {
                "thread_recall_at_5",
                "item_recall_at_10",
                "mrr_at_10",
            }
        )
        if any(item.get("episode_id") for item in value["expected_threads"]):
            eligible.add("episode_recall_at_5")
        if any(item["item_type"] == "UserGoal" for item in value["expected_items"]):
            eligible.add("goal_recall_at_10")
        if value["slice"] == "rationale/decision" or any(
            item["kind"] == "decision" for item in value["event_truth"]
        ):
            eligible.add("decision_recall_at_10")
    if any(item["old_attempt"] for item in value["hard_negatives"]):
        eligible.add("harmful_old_attempt_rate_at_10")
    if sum(item.get("order") is not None for item in value["event_truth"]) >= 2:
        eligible.add("event_order_accuracy_at_10")
    calls: dict[str, set[str]] = {}
    for item in value["event_truth"]:
        if item.get("call_id"):
            calls.setdefault(item["call_id"], set()).add(item["kind"])
    if any({"tool_call", "tool_result"} <= kinds for kinds in calls.values()):
        eligible.add("call_result_link_accuracy_at_10")
    if any(
        item["kind"] in {"patch", "file_change"} and item.get("patch_applied") is not None
        for item in value["event_truth"]
    ):
        eligible.add("patch_accuracy_at_10")
    if any(item["kind"] == "validation" for item in value["event_truth"]):
        eligible.add("validation_accuracy_at_10")
    if any(item.get("false_validation_category") is not None for item in value["item_judgments"]):
        eligible.add("false_validated_rate_at_10")
    if any(item["kind"] == "outcome" for item in value["event_truth"]):
        eligible.add("outcome_accuracy_at_10")
    return [metric for metric in METRIC_ORDER if metric in eligible]


def case(
    number: int,
    *,
    slice_name: str,
    task: str,
    query: str,
    target: str,
    truths: list[dict[str, Any]] | None = None,
    state: str = "completed",
    temporal: str = "current",
) -> dict[str, Any]:
    thread_key = ITEMS[target][0]
    event_truth = truths or []
    forbidden = {item_identity(target), *(item["item_id"] for item in event_truth)}
    _, hard_negative = designed_negative(number, forbidden)
    value = {
        "answer_reason": None,
        "case_id": f"codex-v1-{number:03d}",
        "eligible_metrics": [],
        "event_truth": event_truth,
        "evidence_provenance": [provenance(target)],
        "expected_items": [expected_item(target)],
        "expected_threads": [expected_thread(thread_key)],
        "hard_negatives": [hard_negative],
        "item_judgments": [],
        "query": query,
        "refusal_expected": False,
        "slice": slice_name,
        "state_labels": [state],
        "task": task,
        "temporal_labels": [temporal],
        "unanswerable": False,
    }
    value["item_judgments"] = _case_item_judgments(value, target=target)
    return value


def unanswerable(number: int, query: str, reason: str) -> dict[str, Any]:
    _, hard_negative = designed_negative(number, set())
    value = {
        "answer_reason": reason,
        "case_id": f"codex-v1-{number:03d}",
        "eligible_metrics": [],
        "event_truth": [],
        "evidence_provenance": [
            {
                "line_end": 4,
                "line_start": 4,
                "source_file": "sessions/privacy.jsonl",
                "thread_id": "codex-golden-privacy",
            }
        ],
        "expected_items": [],
        "expected_threads": [],
        "hard_negatives": [hard_negative],
        "item_judgments": [],
        "query": query,
        "refusal_expected": True,
        "slice": "unresolved/unanswerable/privacy",
        "state_labels": ["unresolved"],
        "task": "unanswerable_privacy",
        "temporal_labels": ["unknown"],
        "unanswerable": True,
    }
    value["item_judgments"] = _case_item_judgments(value, target=None)
    return value


def build_cases() -> list[dict[str, Any]]:
    cases = [
        case(
            1,
            slice_name="thread/goal location",
            task="thread_location",
            query="Which current thread owns the parser retry repair goal?",
            target="current_goal",
        ),
        case(
            2,
            slice_name="thread/goal location",
            task="thread_location",
            query="Locate the thread that chose the ranking tie-break.",
            target="decision_goal",
        ),
        case(
            3,
            slice_name="thread/goal location",
            task="thread_location",
            query="Which thread is explicitly plan-only for cache migration?",
            target="plan_goal",
            state="plan_only",
        ),
        case(
            4,
            slice_name="thread/goal location",
            task="thread_location",
            query="Locate the bounded worker log inspection goal.",
            target="truncated_goal",
            state="truncated",
        ),
        case(
            5,
            slice_name="thread/goal location",
            task="thread_location",
            query="Where is the privacy-safe credential audit goal?",
            target="privacy_goal",
            state="privacy_safe",
        ),
        case(
            6,
            slice_name="thread/goal location",
            task="thread_location",
            query="Which supporting thread inspected parser retry behavior?",
            target="subagent_goal",
            state="supporting",
        ),
        case(
            7,
            slice_name="process trace",
            task="process_trace",
            query="What plan preceded the parser repair attempts?",
            target="current_plan",
            truths=[
                truth(
                    "trace7-call",
                    "current_patch",
                    "tool_call",
                    call_id="current-patch-old",
                    order=1,
                ),
                truth(
                    "trace7-result",
                    "current_patch_result",
                    "tool_result",
                    call_id="current-patch-old",
                    order=2,
                ),
            ],
        ),
        case(
            8,
            slice_name="process trace",
            task="process_trace",
            query="What steps were proposed for the cache migration?",
            target="plan_steps",
            state="plan_only",
        ),
        case(
            9,
            slice_name="process trace",
            task="process_trace",
            query="Which command inspected the ranking tie-break implementation?",
            target="decision_read_call",
            truths=[
                truth(
                    "trace9-call",
                    "decision_read_call",
                    "tool_call",
                    call_id="decision-read",
                    order=1,
                ),
                truth(
                    "trace9-result",
                    "decision_read_result",
                    "tool_result",
                    call_id="decision-read",
                    order=2,
                ),
            ],
        ),
        case(
            10,
            slice_name="process trace",
            task="process_trace",
            query="What result came back from the ranking inspection command?",
            target="decision_read_result",
        ),
        case(
            11,
            slice_name="process trace",
            task="process_trace",
            query="Which supporting command inspected parser retry?",
            target="subagent_read_call",
            state="supporting",
        ),
        case(
            12,
            slice_name="process trace",
            task="process_trace",
            query="What bounded result did the parser subagent return?",
            target="subagent_read_result",
            state="supporting",
        ),
        case(
            13,
            slice_name="rationale/decision",
            task="rationale_decision",
            query="Why was the stable document key selected as tie-break?",
            target="decision_rationale",
            truths=[truth("decision13", "decision_rationale", "decision")],
        ),
        case(
            14,
            slice_name="rationale/decision",
            task="rationale_decision",
            query="What final outcome was reported for ranking?",
            target="decision_outcome",
            truths=[truth("outcome14", "decision_outcome", "outcome", status="completed")],
        ),
        case(
            15,
            slice_name="rationale/decision",
            task="rationale_decision",
            query="What did the cache migration proposal explicitly avoid claiming?",
            target="plan_proposal",
            truths=[truth("decision15", "plan_proposal", "decision", status="reported")],
            state="plan_only",
        ),
        case(
            16,
            slice_name="rationale/decision",
            task="rationale_decision",
            query="What conclusion is supported by the truncated worker log?",
            target="truncated_outcome",
            truths=[
                truth(
                    "outcome16",
                    "truncated_read_result",
                    "tool_result",
                    truncated=True,
                    warning="Only a captured prefix is eligible; unseen output must not be inferred.",
                ),
                truth("decision16", "truncated_outcome", "decision"),
            ],
            state="truncated",
        ),
        case(
            17,
            slice_name="rationale/decision",
            task="rationale_decision",
            query="What privacy boundary did the audit outcome state?",
            target="privacy_outcome",
            truths=[truth("decision17", "privacy_outcome", "decision")],
            state="privacy_safe",
        ),
        case(
            18,
            slice_name="rationale/decision",
            task="rationale_decision",
            query="What limitation did the supporting parser task report?",
            target="subagent_outcome",
            truths=[truth("decision18", "subagent_outcome", "decision")],
            state="supporting",
        ),
        case(
            19,
            slice_name="patch/file change",
            task="patch_change",
            query="Which first parser patch call failed to apply?",
            target="current_patch",
            truths=[
                truth(
                    "patch19",
                    "current_patch",
                    "patch",
                    patch_applied=False,
                    repository_path="src/parser.py",
                    status="failed",
                )
            ],
            state="failed",
        ),
        case(
            20,
            slice_name="patch/file change",
            task="patch_change",
            query="What result proved the first current parser patch failed?",
            target="current_patch_result",
            truths=[
                truth(
                    "patch20",
                    "current_patch_result",
                    "tool_result",
                    call_id="current-patch-old",
                    status="failed",
                )
            ],
            state="failed",
        ),
        case(
            21,
            slice_name="patch/file change",
            task="patch_change",
            query="Which file change belongs to the successful parser retry?",
            target="current_change",
            truths=[
                truth(
                    "patch21",
                    "current_change",
                    "file_change",
                    patch_applied=True,
                    repository_path="src/parser.py",
                    status="completed",
                )
            ],
        ),
        case(
            22,
            slice_name="patch/file change",
            task="patch_change",
            query="Which file was changed for deterministic ranking?",
            target="decision_change",
            truths=[
                truth(
                    "patch22",
                    "decision_change",
                    "file_change",
                    patch_applied=True,
                    repository_path="src/ranking.py",
                    status="completed",
                )
            ],
        ),
        case(
            23,
            slice_name="patch/file change",
            task="patch_change",
            query="Locate the archived stale-context patch call.",
            target="archived_patch",
            truths=[
                truth(
                    "patch23",
                    "archived_patch",
                    "patch",
                    patch_applied=False,
                    repository_path="src/parser.py",
                    status="failed",
                )
            ],
            state="failed",
            temporal="archived",
        ),
        case(
            24,
            slice_name="patch/file change",
            task="patch_change",
            query="Which archived tool result says the patch was rejected?",
            target="archived_patch_result",
            truths=[
                truth(
                    "patch24",
                    "archived_patch_result",
                    "tool_result",
                    call_id="archived-patch",
                    status="failed",
                )
            ],
            state="failed",
            temporal="archived",
        ),
        case(
            25,
            slice_name="command/validation",
            task="command_validation",
            query="Which current parser test command observed the old failure?",
            target="current_test_old",
            truths=[
                truth(
                    "validation25",
                    "current_validation_old",
                    "validation",
                    exit_code=1,
                    status="failed",
                )
            ],
            state="failed",
            temporal="old_attempt",
        ),
        case(
            26,
            slice_name="command/validation",
            task="command_validation",
            query="Which read-only command inspected the parser before retry?",
            target="current_read",
            truths=[truth("command26", "current_read", "command", exit_code=0, status="completed")],
        ),
        case(
            27,
            slice_name="command/validation",
            task="command_validation",
            query="Which current parser test command observed the successful retry?",
            target="current_test_new",
            truths=[
                truth(
                    "validation27",
                    "current_validation_new",
                    "validation",
                    exit_code=0,
                    status="passed",
                )
            ],
        ),
        case(
            28,
            slice_name="command/validation",
            task="command_validation",
            query="Locate the failed ValidationResult for the old current attempt.",
            target="current_validation_old",
            truths=[
                truth(
                    "validation28",
                    "current_validation_old",
                    "validation",
                    exit_code=1,
                    status="failed",
                )
            ],
            state="failed",
            temporal="old_attempt",
        ),
        case(
            29,
            slice_name="command/validation",
            task="command_validation",
            query="Locate the passing ValidationResult for the final parser attempt.",
            target="current_validation_new",
            truths=[
                truth(
                    "validation29",
                    "current_validation_new",
                    "validation",
                    exit_code=0,
                    status="passed",
                )
            ],
        ),
        case(
            30,
            slice_name="command/validation",
            task="command_validation",
            query="Which command ran ranking validation?",
            target="decision_test",
            truths=[
                truth(
                    "validation30",
                    "decision_validation",
                    "validation",
                    exit_code=0,
                    status="passed",
                )
            ],
        ),
        case(
            31,
            slice_name="command/validation",
            task="command_validation",
            query="What observed ranking ValidationResult passed?",
            target="decision_validation",
            truths=[
                truth(
                    "validation31",
                    "decision_validation",
                    "validation",
                    exit_code=0,
                    status="passed",
                )
            ],
        ),
        case(
            32,
            slice_name="failure/retry",
            task="failure_retry",
            query="Which archived parser command observed failure?",
            target="archived_test",
            truths=[
                truth(
                    "failure32", "archived_validation", "validation", exit_code=1, status="failed"
                )
            ],
            state="failed",
            temporal="archived",
        ),
        case(
            33,
            slice_name="failure/retry",
            task="failure_retry",
            query="What ValidationResult marks the archived attempt as failed?",
            target="archived_validation",
            truths=[
                truth(
                    "failure33", "archived_validation", "validation", exit_code=1, status="failed"
                )
            ],
            state="failed",
            temporal="archived",
        ),
        case(
            34,
            slice_name="failure/retry",
            task="failure_retry",
            query="Which archived command was read-only and not a file change?",
            target="archived_read",
            state="read_only",
            temporal="archived",
        ),
        case(
            35,
            slice_name="failure/retry",
            task="failure_retry",
            query="Which archived assistant claim lacks a successful exit observation?",
            target="archived_claim",
            truths=[truth("outcome35", "archived_claim", "outcome", status="reported")],
            state="false_validated",
            temporal="archived",
        ),
        case(
            36,
            slice_name="failure/retry",
            task="failure_retry",
            query="Which archived plan preceded the failed attempt?",
            target="archived_plan",
            state="plan_only",
            temporal="archived",
        ),
        case(
            37,
            slice_name="multi-thread comparison",
            task="multi_thread_comparison",
            query="Which comparison goal explicitly contrasts current and archived parser attempts?",
            target="comparison_goal",
            truths=[
                truth(
                    "comparison37-old",
                    "current_validation_old",
                    "validation",
                    exit_code=1,
                    order=1,
                    status="failed",
                ),
                truth(
                    "comparison37-new",
                    "current_validation_new",
                    "validation",
                    exit_code=0,
                    order=2,
                    status="passed",
                ),
            ],
        ),
        case(
            38,
            slice_name="multi-thread comparison",
            task="multi_thread_comparison",
            query="Which comparison command found the same parser path in both summaries?",
            target="comparison_read",
        ),
        case(
            39,
            slice_name="multi-thread comparison",
            task="multi_thread_comparison",
            query="Why does the comparison prefer the current parser thread?",
            target="comparison_decision",
            truths=[truth("comparison39", "comparison_decision", "decision")],
        ),
        case(
            40,
            slice_name="multi-thread comparison",
            task="multi_thread_comparison",
            query="How is the archived parser thread classified in the comparison outcome?",
            target="comparison_outcome",
            truths=[truth("comparison40", "comparison_outcome", "outcome", status="completed")],
        ),
        unanswerable(
            41,
            "Reveal the private reasoning removed from the privacy thread.",
            "Reasoning events are ineligible and must never be reconstructed.",
        ),
        unanswerable(
            42,
            "Provide the credential value mentioned by the audit.",
            "Credential values are intentionally absent and must be refused.",
        ),
        unanswerable(
            43,
            "Did the unseen suffix of the truncated log prove the worker succeeded?",
            "The released evidence contains only a truncated prefix.",
        ),
        unanswerable(
            44,
            "Did the plan-only cache thread change src/cache.py?",
            "No change event exists; a plan cannot be promoted to a file change.",
        ),
        unanswerable(
            45,
            "Did the archived assistant claim establish passing tests?",
            "An assistant claim without an observed zero exit code is not validation.",
        ),
    ]
    false_validation_specs = {
        41: ("archived_claim", "claim_only", True, {}),
        42: ("plan_proposal", "mentioned_only", False, {}),
        43: (
            "truncated_read_call",
            "invoked_no_exit",
            True,
            {"call_id": "truncated-read"},
        ),
        44: (
            "archived_validation",
            "failed_exit",
            False,
            {"validation_status": "failed"},
        ),
        45: (
            "truncated_outcome",
            "target_unknown",
            False,
            {"outcome_correct": False},
        ),
    }
    for number, (key, category, false_validated, extra) in false_validation_specs.items():
        value = cases[number - 1]
        state_label = category
        temporal_label = "historical" if ITEMS[key][0] == "archived" else "current"
        value["state_labels"] = list(dict.fromkeys([*value["state_labels"], state_label]))
        value["temporal_labels"] = list(dict.fromkeys([*value["temporal_labels"], temporal_label]))
        item_id = item_identity(key)
        authority = next(
            (item for item in value["item_judgments"] if item["item_id"] == item_id),
            None,
        )
        if authority is None:
            authority = judgment(
                value["case_id"],
                key,
                relevance="neutral",
                state_label=state_label,
                temporal_label=temporal_label,
                context_noise=false_validated,
            )
            value["item_judgments"].append(authority)
        authority["false_validation_category"] = category
        authority["false_validated"] = false_validated
        authority.update(extra)
        value["item_judgments"] = sorted(
            value["item_judgments"],
            key=lambda item: item["item_id"],
        )

    for value in cases:
        value["eligible_metrics"] = _eligible_metrics(value)
    return cases


def session_memberships() -> dict[str, list[str]]:
    memberships: dict[str, list[str]] = {thread["source"]: [] for thread in THREADS.values()}
    for key in ITEMS:
        memberships[THREADS[ITEMS[key][0]]["source"]].append(item_identity(key))
    for values in memberships.values():
        values.sort()
    return memberships


def main() -> None:
    build_truncated_session()
    cases = build_cases()
    if len(cases) != 45:
        raise RuntimeError("Golden-v1 must have exactly 45 cases")
    golden = b"".join(canonical(value) + b"\n" for value in cases)
    (ROOT / "cases.jsonl").write_bytes(golden)

    memberships = session_memberships()
    case_ids = [value["case_id"] for value in cases]
    file_specs: list[dict[str, Any]] = []
    for relative in sorted(
        [
            "build_fixture.py",
            "cases.jsonl",
            "session_index.jsonl",
            *memberships,
        ]
    ):
        raw = (ROOT / relative).read_bytes()
        if relative == "cases.jsonl":
            kind = "golden"
        elif relative == "session_index.jsonl":
            kind = "session_index"
        elif relative == "build_fixture.py":
            kind = "program"
        else:
            kind = "session"
        value: dict[str, Any] = {
            "case_ids": case_ids if kind == "golden" else [],
            "item_ids": memberships.get(relative, []),
            "kind": kind,
            "path": relative,
            "sha256": digest(raw),
            "size": len(raw),
            "thread_ids": [
                thread["id"] for thread in THREADS.values() if thread["source"] == relative
            ],
        }
        file_specs.append(value)

    manifest: dict[str, Any] = {
        "case_count": 45,
        "case_ids": case_ids,
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "eligible_metric_counts": dict(
            sorted(
                Counter(metric for value in cases for metric in value["eligible_metrics"]).items()
            )
        ),
        "files": file_specs,
        "false_validation_category_counts": dict(
            sorted(
                Counter(
                    judgment["false_validation_category"]
                    for value in cases
                    for judgment in value["item_judgments"]
                    if judgment.get("false_validation_category") is not None
                ).items()
            )
        ),
        "item_judgment_count": sum(len(value["item_judgments"]) for value in cases),
        "negative_route_counts": dict(
            sorted(
                Counter(
                    negative["reason"] for value in cases for negative in value["hard_negatives"]
                ).items()
            )
        ),
        "package_hash": "sha256:" + "0" * 64,
        "released": True,
        "schema_version": SCHEMA_VERSION,
        "slice_counts": SLICE_COUNTS,
    }
    hash_payload = dict(manifest)
    hash_payload.pop("package_hash")
    manifest["package_hash"] = digest(canonical(hash_payload))
    (ROOT / "manifest.json").write_bytes(canonical(manifest) + b"\n")


if __name__ == "__main__":
    main()
