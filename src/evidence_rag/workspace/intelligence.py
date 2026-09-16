from __future__ import annotations

from collections import Counter
from typing import Any

from ..storage import SQLiteStore, utc_now
from .service import WorkspaceError
from .store import WorkspaceStore

ACTIVE_WORK = {"backlog", "ready", "running", "review", "blocked"}
FINISHED_WORK = {"done"}
ACTIVE_ITERATIONS = {"active", "validating", "blocked"}


class ResearchIntelligenceService:
    """Derive truthful project state and next actions from persisted research evidence."""

    def __init__(self, database: SQLiteStore, workspace: WorkspaceStore) -> None:
        self.database = database
        self.workspace = workspace

    def analyze(
        self,
        project_id: str,
        *,
        topic_id: str | None = None,
        iteration_id: str | None = None,
    ) -> dict[str, Any]:
        project = self.workspace.get_project(project_id)
        if not project:
            raise WorkspaceError("project not found")

        topics = self.workspace.list_topics(project_id)
        topic = self._select_topic(topics, topic_id)
        iterations = (
            self.workspace.list_iterations(project_id, topic_id=topic["id"]) if topic else []
        )
        iteration = self._select_iteration(iterations, iteration_id)
        work_items = self.workspace.list_work_items(
            project_id,
            topic_id=topic["id"] if topic else None,
            iteration_id=iteration["id"] if iteration else None,
            limit=1000,
        )
        evidence = self._evidence(project_id, topic, iteration, iterations)
        health = self._source_health(project_id)
        work = self._work_summary(work_items)
        blockers = self._blockers(iteration, work_items, health)
        next_actions = self._next_actions(
            topic,
            iteration,
            work_items,
            evidence,
            health,
            blockers,
        )
        stage = self._stage(topic, iteration, work, evidence, health)
        score, dimensions = self._score(
            topic,
            iteration,
            work_items,
            evidence,
            health,
            blockers,
        )
        level = self._level(stage, score, blockers, evidence)
        headline = self._headline(stage, next_actions, blockers)

        return {
            "project_id": project_id,
            "scope": {
                "topic_id": topic["id"] if topic else None,
                "topic_title": topic["title"] if topic else None,
                "iteration_id": iteration["id"] if iteration else None,
                "iteration_title": iteration["title"] if iteration else None,
            },
            "stage": stage,
            "readiness": {
                "score": score,
                "level": level,
                "dimensions": dimensions,
            },
            "headline": headline,
            "work": work,
            "evidence": evidence,
            "source_health": health,
            "blockers": blockers,
            "next_actions": next_actions[:8],
            "generated_at": utc_now(),
        }

    def _select_topic(
        self, topics: list[dict[str, Any]], topic_id: str | None
    ) -> dict[str, Any] | None:
        if topic_id:
            topic = next((item for item in topics if item["id"] == topic_id), None)
            if not topic:
                raise WorkspaceError("topic not found")
            return topic
        return next((item for item in topics if item["status"] == "active"), None) or (
            topics[0] if topics else None
        )

    def _select_iteration(
        self, iterations: list[dict[str, Any]], iteration_id: str | None
    ) -> dict[str, Any] | None:
        if iteration_id:
            iteration = next(
                (item for item in iterations if item["id"] == iteration_id), None
            )
            if not iteration:
                raise WorkspaceError("iteration not found")
            return iteration
        return next(
            (item for item in iterations if item["status"] in ACTIVE_ITERATIONS), None
        ) or (iterations[0] if iterations else None)

    def _evidence(
        self,
        project_id: str,
        topic: dict[str, Any] | None,
        iteration: dict[str, Any] | None,
        iterations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        iteration_ids = [iteration["id"]] if iteration else [
            item["id"] for item in iterations
        ]
        by_source: Counter[str] = Counter()
        by_status: Counter[str] = Counter()
        total = 0
        linked_entity_ids: list[str] = []
        if iteration_ids:
            placeholders = ",".join("?" for _ in iteration_ids)
            with self.database.connection() as db:
                rows = db.execute(
                    f"""SELECT source_type, status, count(*) AS count
                        FROM iteration_links
                        WHERE project_id=? AND iteration_id IN ({placeholders})
                        GROUP BY source_type, status""",
                    [project_id, *iteration_ids],
                ).fetchall()
                linked_entity_ids = [
                    str(row["entity_id"])
                    for row in db.execute(
                        f"""SELECT DISTINCT entity_id FROM iteration_links
                            WHERE project_id=? AND iteration_id IN ({placeholders})""",
                        [project_id, *iteration_ids],
                    ).fetchall()
                ]
            for row in rows:
                count = int(row["count"])
                total += count
                by_source[str(row["source_type"])] += count
                by_status[str(row["status"])] += count

        relation_ids = list(
            dict.fromkeys(
                [
                    *([topic["id"]] if topic else []),
                    *iteration_ids,
                    *linked_entity_ids,
                ]
            )
        )
        with self.database.connection() as db:
            if relation_ids:
                placeholders = ",".join("?" for _ in relation_ids)
                relation_rows = db.execute(
                    f"""SELECT review_status, count(*) AS count
                        FROM platform_edges
                        WHERE project_id=? AND (
                          source_entity_id IN ({placeholders})
                          OR target_entity_id IN ({placeholders})
                          OR evidence_entity_id IN ({placeholders})
                        )
                        GROUP BY review_status""",
                    [project_id, *relation_ids, *relation_ids, *relation_ids],
                ).fetchall()
            else:
                relation_rows = db.execute(
                    """SELECT review_status, count(*) AS count
                       FROM platform_edges WHERE project_id=?
                       GROUP BY review_status""",
                    (project_id,),
                ).fetchall()
        relations = {str(row["review_status"]): int(row["count"]) for row in relation_rows}
        pending = relations.get("unreviewed", 0)
        confirmed = relations.get("confirmed", 0)
        contradicted = relations.get("rejected", 0)
        return {
            "total": total,
            "by_source": dict(sorted(by_source.items())),
            "by_status": dict(sorted(by_status.items())),
            "source_types": len(by_source),
            "verified": by_status.get("verified", 0) + by_status.get("confirmed", 0),
            "pending_review": pending,
            "confirmed_relations": confirmed,
            "rejected_relations": contradicted,
            "scope": "iteration" if iteration else "topic" if topic else "project",
        }

    def _source_health(self, project_id: str) -> dict[str, Any]:
        with self.database.connection() as db:
            repository_rows = db.execute(
                """SELECT status, count(*) AS count FROM repositories
                   WHERE project_id=? GROUP BY status""",
                (project_id,),
            ).fetchall()
            workflow_rows = db.execute(
                """SELECT workflow.status, count(*) AS count
                   FROM workflows workflow
                   JOIN repositories repository ON repository.id=workflow.repository_id
                   WHERE repository.project_id=?
                     AND workflow.id=(
                       SELECT latest.id FROM workflows latest
                       WHERE latest.repository_id=workflow.repository_id
                       ORDER BY latest.updated_at DESC, latest.created_at DESC
                       LIMIT 1
                     )
                   GROUP BY workflow.status""",
                (project_id,),
            ).fetchall()
            codex_sessions = db.execute(
                """SELECT count(*) AS count
                   FROM codex_threads thread
                   JOIN codex_sources source ON source.id=thread.source_id
                   WHERE thread.project_id=?
                     AND thread.generation_id=source.active_generation_id""",
                (project_id,),
            ).fetchone()["count"]
            experiment_rows = db.execute(
                """SELECT status, count(*) AS count FROM experiment_runs
                   WHERE project_id=? GROUP BY status""",
                (project_id,),
            ).fetchall()
            experiments = db.execute(
                "SELECT count(*) AS count FROM experiments WHERE project_id=?",
                (project_id,),
            ).fetchone()["count"]
            documents = db.execute(
                "SELECT count(*) AS count FROM scientific_documents WHERE project_id=?",
                (project_id,),
            ).fetchone()["count"]
            claim_rows = db.execute(
                """SELECT status, count(*) AS count FROM claims
                   WHERE project_id=? GROUP BY status""",
                (project_id,),
            ).fetchall()
            execution_rows = db.execute(
                """SELECT status, count(*) AS count FROM codex_executions
                   WHERE project_id=? GROUP BY status""",
                (project_id,),
            ).fetchall()
            pending_approvals = db.execute(
                """SELECT count(*) AS count FROM approval_requests
                   WHERE project_id=? AND status='pending'""",
                (project_id,),
            ).fetchone()["count"]
            pending_claim_matches = db.execute(
                """SELECT count(*) AS count FROM claim_match_candidates
                   WHERE project_id=? AND review_status='unreviewed'""",
                (project_id,),
            ).fetchone()["count"]
            pending_metric_matches = db.execute(
                """SELECT count(*) AS count FROM table_metric_match_candidates
                   WHERE project_id=? AND review_status='unreviewed'""",
                (project_id,),
            ).fetchone()["count"]
            drift_rows = db.execute(
                """SELECT status, count(*) AS count FROM drift_assessments
                   WHERE project_id=? GROUP BY status""",
                (project_id,),
            ).fetchall()

        repositories = self._counts(repository_rows)
        workflows = self._counts(workflow_rows)
        runs = self._counts(experiment_rows)
        claims = self._counts(claim_rows)
        executions = self._counts(execution_rows)
        drift = self._counts(drift_rows)
        return {
            "repositories": {
                "total": sum(repositories.values()),
                "ready": repositories.get("ready", 0),
                "indexing": repositories.get("indexing", 0),
                "failed": repositories.get("failed", 0),
                "states": repositories,
            },
            "indexing": {
                "running": workflows.get("running", 0) + workflows.get("queued", 0),
                "failed": workflows.get("failed", 0),
            },
            "codex": {
                "sessions": int(codex_sessions),
                "executions": sum(executions.values()),
                "running": executions.get("running", 0) + executions.get("queued", 0),
                "review": executions.get("review", 0),
                "failed": executions.get("failed", 0),
            },
            "experiments": {
                "total": int(experiments),
                "runs": sum(runs.values()),
                "completed_runs": runs.get("completed", 0) + runs.get("finished", 0),
                "failed_runs": runs.get("failed", 0),
                "states": runs,
            },
            "documents": {
                "total": int(documents),
                "claims": sum(claims.values()),
                "verified_claims": claims.get("verified", 0),
                "contradicted_claims": claims.get("contradicted", 0),
                "insufficient_claims": claims.get("insufficient_evidence", 0),
                "states": claims,
            },
            "review": {
                "pending_approvals": int(pending_approvals),
                "pending_claim_matches": int(pending_claim_matches),
                "pending_metric_matches": int(pending_metric_matches),
            },
            "drift": {
                "total": sum(drift.values()),
                "at_risk": sum(
                    count for status, count in drift.items() if status != "valid"
                ),
                "states": drift,
            },
        }

    @staticmethod
    def _counts(rows) -> dict[str, int]:
        return {str(row["status"]): int(row["count"]) for row in rows}

    @staticmethod
    def _work_summary(work_items: list[dict[str, Any]]) -> dict[str, Any]:
        states = Counter(str(item["status"]) for item in work_items)
        kinds = Counter(str(item["kind"]) for item in work_items)
        return {
            "total": len(work_items),
            "active": sum(states.get(status, 0) for status in ACTIVE_WORK),
            "finished": sum(states.get(status, 0) for status in FINISHED_WORK),
            "states": dict(sorted(states.items())),
            "kinds": dict(sorted(kinds.items())),
            "codex_assigned": sum(
                1 for item in work_items if item.get("assignee_type") == "codex"
            ),
            "without_acceptance_criteria": sum(
                1
                for item in work_items
                if item["status"] in {"ready", "running", "review"}
                and not item.get("acceptance_criteria")
            ),
        }

    def _blockers(
        self,
        iteration: dict[str, Any] | None,
        work_items: list[dict[str, Any]],
        health: dict[str, Any],
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        if iteration and iteration["status"] == "blocked":
            blockers.append(
                self._issue(
                    "iteration_blocked",
                    "当前研究迭代已阻塞",
                    iteration.get("summary") or "需要明确阻塞原因并决定继续、改线或终止。",
                    "critical",
                    "iteration",
                    iteration["id"],
                    "edit_iteration",
                    "workspace",
                )
            )
        for item in work_items:
            if item["status"] == "blocked":
                blockers.append(
                    self._issue(
                        "work_blocked",
                        item["title"],
                        item.get("summary") or item.get("objective") or "执行任务处于阻塞状态。",
                        "critical" if item.get("priority", 3) <= 2 else "high",
                        "work_item",
                        item["id"],
                        "resolve_blocker",
                        "workspace",
                    )
                )
        if health["repositories"]["failed"] or health["indexing"]["failed"]:
            blockers.append(
                self._issue(
                    "code_source_failed",
                    "代码索引存在失败",
                    (
                        f"{health['repositories']['failed']} 个仓库异常，"
                        f"{health['indexing']['failed']} 个索引任务失败。"
                    ),
                    "high",
                    "repository",
                    None,
                    "inspect_indexing",
                    "repository",
                )
            )
        if health["codex"]["failed"]:
            blockers.append(
                self._issue(
                    "codex_execution_failed",
                    "Codex 执行存在失败",
                    f"{health['codex']['failed']} 次执行需要检查失败原因或重试。",
                    "high",
                    "codex_execution",
                    None,
                    "inspect_execution",
                    "codex-bridge",
                )
            )
        if health["documents"]["contradicted_claims"]:
            blockers.append(
                self._issue(
                    "claim_contradicted",
                    "研究主张存在反证",
                    f"{health['documents']['contradicted_claims']} 条主张与当前证据冲突。",
                    "critical",
                    "claim",
                    None,
                    "review_claims",
                    "documents",
                )
            )
        if health["drift"]["at_risk"]:
            blockers.append(
                self._issue(
                    "evidence_drift",
                    "历史证据需要重新验证",
                    f"{health['drift']['at_risk']} 条证据链受版本变化影响。",
                    "high",
                    "drift_assessment",
                    None,
                    "review_drift",
                    "governance",
                )
            )
        return blockers

    def _next_actions(
        self,
        topic: dict[str, Any] | None,
        iteration: dict[str, Any] | None,
        work_items: list[dict[str, Any]],
        evidence: dict[str, Any],
        health: dict[str, Any],
        blockers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        if not topic:
            return [
                self._action(
                    "create_topic",
                    "建立研究主题",
                    "先记录研究问题，系统会自动组织后续迭代、任务与证据。",
                    "research",
                    1,
                    "create_topic",
                    None,
                    "workspace",
                )
            ]
        if not (topic.get("problem_statement") or topic.get("objective")):
            actions.append(
                self._action(
                    "define_research_question",
                    "明确当前研究问题",
                    "主题已有标题，但研究问题和目标均为空。",
                    "research",
                    1,
                    "edit_topic",
                    topic["id"],
                    "workspace",
                )
            )
        for blocker in blockers[:3]:
            actions.append(
                self._action(
                    f"resolve:{blocker['code']}:{blocker.get('target_id') or 'scope'}",
                    blocker["title"],
                    blocker["detail"],
                    "recovery",
                    1,
                    blocker["action"],
                    blocker.get("target_id"),
                    blocker["route"],
                )
            )
        if not iteration:
            actions.append(
                self._action(
                    "create_iteration",
                    "建立本轮研究迭代",
                    "当前主题还没有可执行的研究轮次。",
                    "research",
                    1,
                    "create_iteration",
                    topic["id"],
                    "workspace",
                )
            )
            return sorted(actions, key=lambda item: item["priority"])
        if not (iteration.get("goal") or iteration.get("hypothesis")):
            actions.append(
                self._action(
                    "define_iteration_goal",
                    "补充本轮目标或假设",
                    "系统需要一个判断本轮是否完成的研究目标。",
                    "research",
                    2,
                    "edit_iteration",
                    iteration["id"],
                    "workspace",
                )
            )
        if health["review"]["pending_approvals"]:
            actions.append(
                self._action(
                    "review_approvals",
                    "处理 Codex 待批准事项",
                    f"{health['review']['pending_approvals']} 项执行或状态变更等待人工确认。",
                    "review",
                    1,
                    "review_approvals",
                    None,
                    "codex-bridge",
                )
            )
        review_items = [item for item in work_items if item["status"] == "review"]
        if review_items:
            actions.append(
                self._action(
                    "review_work",
                    f"复核 {review_items[0]['title']}",
                    f"{len(review_items)} 项工作已回传结果，确认后再进入研究结论。",
                    "review",
                    1,
                    "review_work_item",
                    review_items[0]["id"],
                    "workspace",
                )
            )
        if evidence["pending_review"]:
            actions.append(
                self._action(
                    "review_relations",
                    "复核跨来源关系",
                    f"{evidence['pending_review']} 条自动推断关系尚未获得人工确认。",
                    "review",
                    2,
                    "review_relations",
                    None,
                    "bindings",
                )
            )
        active_items = [
            item for item in work_items if item["status"] in {"backlog", "ready", "running"}
        ]
        if not work_items:
            actions.append(
                self._action(
                    "create_work",
                    "拆分一个可执行任务",
                    "本轮尚无开发、实验或分析任务；只需描述目标，关联信息由系统带入。",
                    "execution",
                    2,
                    "create_work",
                    iteration["id"],
                    "workspace",
                )
            )
        elif active_items:
            item = next(
                (candidate for candidate in active_items if candidate["status"] == "running"),
                active_items[0],
            )
            actions.append(
                self._action(
                    "continue_work",
                    f"{'继续' if item['status'] == 'running' else '开始'} {item['title']}",
                    item.get("objective") or "按任务目标执行并回传可复核结果。",
                    "execution",
                    2,
                    "open_work_item",
                    item["id"],
                    "codex-bridge" if item.get("assignee_type") == "codex" else "workspace",
                )
            )
        finished_or_review = [
            item for item in work_items if item["status"] in {"done", "review"}
        ]
        if finished_or_review and evidence["total"] == 0:
            actions.append(
                self._action(
                    "link_evidence",
                    "把执行结果归入本轮证据",
                    "已有任务结果，但当前迭代尚未关联代码、会话、实验或文档证据。",
                    "evidence",
                    2,
                    "link_evidence",
                    iteration["id"],
                    "search",
                )
            )
        if evidence["total"] and evidence["source_types"] < 2:
            actions.append(
                self._action(
                    "diversify_evidence",
                    "补充另一类独立证据",
                    "当前结论仅由单一来源支撑，建议补充实验、代码或文档交叉验证。",
                    "evidence",
                    3,
                    "search_evidence",
                    iteration["id"],
                    "search",
                )
            )
        if health["documents"]["insufficient_claims"]:
            actions.append(
                self._action(
                    "validate_claims",
                    "补齐证据不足的研究主张",
                    f"{health['documents']['insufficient_claims']} 条主张尚未达到证据要求。",
                    "evidence",
                    2,
                    "review_claims",
                    None,
                    "documents",
                )
            )
        return sorted(actions, key=lambda item: item["priority"])

    @staticmethod
    def _stage(
        topic: dict[str, Any] | None,
        iteration: dict[str, Any] | None,
        work: dict[str, Any],
        evidence: dict[str, Any],
        health: dict[str, Any],
    ) -> str:
        if not topic or not (topic.get("problem_statement") or topic.get("objective")):
            return "define"
        if not iteration or not work["total"]:
            return "plan"
        if (
            iteration["status"] == "completed"
            and work["active"] == 0
            and work["finished"] > 0
            and evidence["total"] > 0
            and evidence["pending_review"] == 0
        ):
            return "complete"
        if health["review"]["pending_approvals"] or work["states"].get("review", 0):
            return "review"
        if work["states"].get("running", 0) or work["states"].get("ready", 0):
            return "execute"
        if evidence["total"] or work["finished"]:
            return "validate"
        return "plan"

    @staticmethod
    def _score(
        topic: dict[str, Any] | None,
        iteration: dict[str, Any] | None,
        work_items: list[dict[str, Any]],
        evidence: dict[str, Any],
        health: dict[str, Any],
        blockers: list[dict[str, Any]],
    ) -> tuple[int, dict[str, int]]:
        definition = 0
        if topic:
            definition += 15
            if topic.get("problem_statement") or topic.get("objective"):
                definition += 5
        if iteration:
            definition += 10
            if iteration.get("goal") or iteration.get("hypothesis"):
                definition += 5

        execution = 0
        if work_items:
            execution += 10
            if any(item["status"] in {"ready", "running", "review", "done"} for item in work_items):
                execution += 10
            if not any(item["status"] == "blocked" for item in work_items):
                execution += 5

        evidence_score = 0
        if evidence["total"]:
            evidence_score += 10
        if evidence["source_types"] >= 2:
            evidence_score += 10
        if (
            health["repositories"]["total"]
            or health["codex"]["sessions"]
            or health["experiments"]["runs"]
            or health["documents"]["total"]
        ):
            evidence_score += 5

        review = 0
        if topic and iteration:
            if not evidence["pending_review"]:
                review += 5
            if not health["review"]["pending_approvals"]:
                review += 5
            if not blockers:
                review += 5

        dimensions = {
            "definition": definition,
            "execution": execution,
            "evidence": evidence_score,
            "review": review,
        }
        return sum(dimensions.values()), dimensions

    @staticmethod
    def _level(
        stage: str,
        score: int,
        blockers: list[dict[str, Any]],
        evidence: dict[str, Any],
    ) -> str:
        if any(item["severity"] == "critical" for item in blockers):
            return "blocked"
        if stage == "define":
            return "needs_definition"
        if stage == "plan":
            return "ready_to_plan"
        if stage == "execute":
            return "executing"
        if not evidence["total"]:
            return "needs_evidence"
        if stage == "review" and score >= 70:
            return "ready_for_review"
        if stage == "complete":
            return "complete"
        return "validating"

    @staticmethod
    def _headline(
        stage: str,
        actions: list[dict[str, Any]],
        blockers: list[dict[str, Any]],
    ) -> str:
        if blockers:
            return f"{len(blockers)} 项阻塞需要处理"
        if actions:
            return actions[0]["title"]
        return {
            "define": "等待研究问题",
            "plan": "可以规划本轮工作",
            "execute": "执行正在推进",
            "validate": "正在汇总证据",
            "review": "结果等待复核",
            "complete": "本轮研究证据已闭环",
        }.get(stage, "项目状态已同步")

    @staticmethod
    def _issue(
        code: str,
        title: str,
        detail: str,
        severity: str,
        entity_type: str,
        target_id: str | None,
        action: str,
        route: str,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "title": title,
            "detail": detail,
            "severity": severity,
            "entity_type": entity_type,
            "target_id": target_id,
            "action": action,
            "route": route,
        }

    @staticmethod
    def _action(
        action_id: str,
        title: str,
        reason: str,
        kind: str,
        priority: int,
        action: str,
        target_id: str | None,
        route: str,
    ) -> dict[str, Any]:
        return {
            "id": action_id,
            "title": title,
            "reason": reason,
            "kind": kind,
            "priority": priority,
            "action": action,
            "target_id": target_id,
            "route": route,
        }
