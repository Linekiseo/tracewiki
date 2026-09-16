from __future__ import annotations

import re
from collections import Counter
from typing import Any
from uuid import uuid4

from ..storage import SQLiteStore
from ..workspace.service import WorkspaceService
from .models import CodexComparisonCreate
from .store import CodexComparisonStore

DECISION_RE = re.compile(r"(?:decision|decided|决定|决策|选择|采用|改为)", re.I)
ALTERNATIVE_RE = re.compile(r"(?:alternative|option|方案|选项|备选|或者)", re.I)


class CodexComparisonError(ValueError):
    pass


class CodexComparisonService:
    def __init__(
        self,
        store: CodexComparisonStore,
        source: SQLiteStore,
        workspace: WorkspaceService,
    ) -> None:
        self.store = store
        self.source = source
        self.workspace = workspace

    def compare(self, request: CodexComparisonCreate) -> dict[str, Any]:
        threads = []
        for thread_id in request.thread_ids:
            thread = self.source.get_codex_thread(thread_id)
            if not thread:
                raise CodexComparisonError(f"Codex thread not found: {thread_id}")
            if thread["project_id"] != request.project_id:
                raise CodexComparisonError("all Codex threads must belong to the project")
            threads.append(thread)
        baseline_id = request.baseline_thread_id or request.thread_ids[0]
        snapshots = [self._snapshot(thread) for thread in threads]
        baseline = next(item for item in snapshots if item["thread_id"] == baseline_id)
        comparisons = [
            self._delta(baseline, candidate)
            for candidate in snapshots
            if candidate["thread_id"] != baseline_id
        ]
        token = uuid4().hex
        result = self.store.save(
            {
                "id": f"codex-comparison://{token}",
                "display_key": f"SCMP-{token[:8].upper()}",
                "project_id": request.project_id,
                "name": request.name,
                "baseline_thread_id": baseline_id,
                "thread_ids": request.thread_ids,
                "created_by": request.created_by,
                "result": {
                    "baseline": baseline,
                    "threads": snapshots,
                    "comparisons": comparisons,
                    "summary": self._summary(snapshots, comparisons),
                    "derivation": "deterministic-session-comparison-v1",
                },
            }
        )
        self.workspace._audit(
            request.project_id,
            "codex.comparison_created",
            "codex_comparison",
            result["id"],
            {"thread_ids": request.thread_ids, "baseline_thread_id": baseline_id},
            actor=request.created_by,
        )
        return result

    @staticmethod
    def _snapshot(thread: dict[str, Any]) -> dict[str, Any]:
        turns = thread["turns"]
        items = [item for turn in turns for item in turn["items"]]
        goals = [
            {"text": turn["goal"], "locator": turn["id"]} for turn in turns if turn.get("goal")
        ]
        commands = []
        paths: dict[str, list[str]] = {}
        validations = []
        changes = []
        decision_candidates = []
        alternative_candidates = []
        failures = []
        commits = set()
        for item in items:
            metadata = item.get("metadata") or {}
            locator = item["source_locator"]
            if item["item_type"] == "CommandExecution":
                commands.append(
                    {
                        "text": str(metadata.get("command") or item["content"]).splitlines()[0][
                            :500
                        ],
                        "status": item.get("status"),
                        "locator": locator,
                    }
                )
            if item["item_type"] in {"FileChange", "Patch"}:
                item_paths = CodexComparisonService._paths(metadata)
                changes.append(
                    {
                        "id": item["id"],
                        "paths": item_paths,
                        "status": item.get("status"),
                        "locator": locator,
                    }
                )
                for path in item_paths:
                    paths.setdefault(path, []).append(locator)
            if item["item_type"] == "ValidationResult":
                validations.append(
                    {
                        "command": metadata.get("command") or item["name"],
                        "status": metadata.get("status") or item.get("status"),
                        "exit_code": metadata.get("exit_code"),
                        "locator": locator,
                    }
                )
            if item.get("status") in {"failed", "error", "cancelled"}:
                failures.append(
                    {"type": item["item_type"], "text": item["content"][:500], "locator": locator}
                )
            if item["item_type"] in {"AgentMessage", "DevelopmentEpisode"}:
                for line in (part.strip() for part in item["content"].splitlines()):
                    if len(line) < 8:
                        continue
                    if DECISION_RE.search(line):
                        decision_candidates.append(
                            {
                                "text": line[:500],
                                "locator": locator,
                                "derivation": "keyword_candidate",
                            }
                        )
                    if ALTERNATIVE_RE.search(line):
                        alternative_candidates.append(
                            {
                                "text": line[:500],
                                "locator": locator,
                                "derivation": "keyword_candidate",
                            }
                        )
            for key in ("commit", "commit_sha", "head_commit"):
                value = metadata.get(key)
                if value:
                    commits.add(str(value))
        for edge in thread.get("edges", []):
            for endpoint in (edge["source_entity_id"], edge["target_entity_id"]):
                if endpoint.startswith("git://"):
                    commits.add(endpoint)
        outcome = "discussed"
        if changes:
            outcome = "executed_uncommitted"
        if commits:
            outcome = "committed"
        if any(item.get("status") == "passed" for item in validations):
            outcome = "validated" if commits else "validated_uncommitted"
        if failures and not any(item.get("status") == "passed" for item in validations):
            outcome = "failed"
        return {
            "thread_id": thread["thread_id"],
            "entity_id": thread["id"],
            "title": thread["title"],
            "cwd": thread.get("cwd"),
            "status": thread["status"],
            "started_at": thread.get("started_at"),
            "updated_at": thread.get("updated_at"),
            "outcome": outcome,
            "goals": goals,
            "decision_candidates": CodexComparisonService._unique(decision_candidates, "text"),
            "alternative_candidates": CodexComparisonService._unique(
                alternative_candidates, "text"
            ),
            "commands": commands,
            "changed_paths": [
                {"path": path, "locators": locators} for path, locators in sorted(paths.items())
            ],
            "changes": changes,
            "validations": validations,
            "failures": failures,
            "commits": sorted(commits),
            "counts": {
                "turns": len(turns),
                "items": len(items),
                "commands": len(commands),
                "changes": len(changes),
                "validations": len(validations),
                "failures": len(failures),
            },
        }

    @staticmethod
    def _paths(metadata: dict[str, Any]) -> list[str]:
        values = metadata.get("paths") or metadata.get("files") or []
        if isinstance(values, str):
            values = [values]
        path = metadata.get("path")
        if path:
            values = [*values, path]
        return sorted({str(value) for value in values if value})

    @staticmethod
    def _unique(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for item in items:
            found.setdefault(str(item[key]), item)
        return list(found.values())[:50]

    @staticmethod
    def _delta(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        def values(item: dict[str, Any], field: str, key: str) -> set[str]:
            return {str(value[key]) for value in item[field]}

        fields = {
            "changed_paths": ("changed_paths", "path"),
            "commands": ("commands", "text"),
            "validations": ("validations", "command"),
        }
        deltas = {}
        for name, (field, key) in fields.items():
            before = values(baseline, field, key)
            after = values(candidate, field, key)
            deltas[name] = {
                "shared": sorted(before & after),
                "added": sorted(after - before),
                "removed": sorted(before - after),
            }
        before_commits = set(baseline["commits"])
        after_commits = set(candidate["commits"])
        deltas["commits"] = {
            "shared": sorted(before_commits & after_commits),
            "added": sorted(after_commits - before_commits),
            "removed": sorted(before_commits - after_commits),
        }
        baseline_goal = " ".join(item["text"] for item in baseline["goals"])
        candidate_goal = " ".join(item["text"] for item in candidate["goals"])
        goal_similarity = CodexComparisonService._token_similarity(baseline_goal, candidate_goal)
        return {
            "baseline_thread_id": baseline["thread_id"],
            "candidate_thread_id": candidate["thread_id"],
            "goal_similarity": goal_similarity,
            "outcome_transition": f"{baseline['outcome']} → {candidate['outcome']}",
            "deltas": deltas,
        }

    @staticmethod
    def _token_similarity(left: str, right: str) -> float:
        pattern = re.compile(r"[A-Za-z0-9_./@-]+|[\u4e00-\u9fff]")
        left_tokens = set(pattern.findall(left.casefold()))
        right_tokens = set(pattern.findall(right.casefold()))
        if not left_tokens and not right_tokens:
            return 1.0
        return round(len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens)), 4)

    @staticmethod
    def _summary(
        snapshots: list[dict[str, Any]], comparisons: list[dict[str, Any]]
    ) -> dict[str, Any]:
        outcomes = Counter(item["outcome"] for item in snapshots)
        all_paths = Counter(path["path"] for item in snapshots for path in item["changed_paths"])
        return {
            "thread_count": len(snapshots),
            "outcomes": dict(outcomes),
            "shared_paths": sorted(path for path, count in all_paths.items() if count > 1),
            "unique_paths": sorted(path for path, count in all_paths.items() if count == 1),
            "average_goal_similarity": round(
                sum(item["goal_similarity"] for item in comparisons) / max(1, len(comparisons)),
                4,
            ),
        }
