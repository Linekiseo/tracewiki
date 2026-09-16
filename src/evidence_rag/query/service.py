from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from ..config import Settings
from ..models import QueryRequest
from ..platform.models import GlobalSearchRequest
from ..platform.service import PlatformService
from ..rag.planner_v2 import build_query_plan
from .intelligence import (
    QUERY_UNDERSTANDING_VERSION,
    QueryUnderstandingEngine,
    fuse_search_responses,
)
from .intent import REQUIRED_ROLES, RetrievalVocabularyExpander
from .interaction_v2 import (
    INTERACTION_CONTRACT_VERSION,
    TRACE_SANITIZER_VERSION,
    AnswerabilityDecision,
    AnswerMode,
    InteractionMachine,
    InteractionState,
    assert_outbound_evidence_safe,
    begin_interaction,
    build_conversation_snapshot,
    clarification_for_request,
    evidence_content_findings,
    filter_unsafe_evidence,
    normalize_source_status,
    repair_generated_text,
    sanitize_trace,
    select_answer_mode,
    source_status_for_exception,
    transition,
    validate_claim_citations,
)
from .provider import (
    ResolvedLLMProvider,
    RuntimeLLMProviderRegistry,
    provider_chat_options,
)

if TYPE_CHECKING:
    from ..rag.multisource_runtime_v2 import MultiSourceRuntimeV2
    from ..workspace.service import WorkspaceService

EXPLICIT_SOURCE_PATTERNS = {
    "source_code": re.compile(r"代码|源码|(?:^|\W)code(?:$|\W)|symbol", re.IGNORECASE),
    "source_codex": re.compile(r"codex|研发会话|开发会话", re.IGNORECASE),
    "source_experiment": re.compile(
        r"实验|指标|(?:^|\W)experiment(?:$|\W)|(?:^|\W)metric(?:$|\W)",
        re.IGNORECASE,
    ),
    "source_notebook": re.compile(
        r"笔记本|notebook|(?:^|\W)ipynb(?:$|\W)|代码单元|cell",
        re.IGNORECASE,
    ),
    "source_document": re.compile(
        r"文档|论文|报告|(?:^|\W)document(?:$|\W)|(?:^|\W)claim(?:$|\W)",
        re.IGNORECASE,
    ),
    "source_workspace": re.compile(r"研究主题|研究迭代|工作任务|workspace", re.IGNORECASE),
}

QUERY_PLAN_PREVIEW_VERSION = "query-plan-preview-v1"
QUERY_HISTORY_VERSION = "query-history-v1"
_ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s\"'])(?:/(?!/)|[A-Za-z]:[\\/]|~[/\\])")


class UnifiedQueryService:
    def __init__(
        self,
        settings: Settings,
        platform: PlatformService,
        *,
        global_v2: MultiSourceRuntimeV2 | None = None,
        workspace: WorkspaceService | None = None,
        providers: RuntimeLLMProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.platform = platform
        self.global_v2 = global_v2
        self.workspace = workspace
        self.vocabulary = RetrievalVocabularyExpander()
        self.providers = providers
        self.understanding = QueryUnderstandingEngine()

    def preview(self, request: QueryRequest) -> dict[str, Any]:
        """Build a deterministic plan without resolving indexes or retrieving evidence."""

        requested_sources = list(request.include or request.scope.source_types)
        understanding = self.understanding.local(
            request.question,
            explicit_intent=request.intent,
            requested_sources=requested_sources,
        )
        intent = understanding.intent
        intent_signals = [
            f"algorithm:{understanding.strategy}",
            f"confidence:{understanding.intent_confidence:.4f}",
        ]
        per_source_limit = min(30, max(8, request.max_evidence))
        plan = build_query_plan(
            query=request.question,
            intent=intent,
            requested_sources=requested_sources,
            limit=per_source_limit,
            max_hops=request.max_hops,
        )
        first_wave = list(plan["routed_sources"])
        fallback_wave = list(plan["fallback_sources"])
        source_waves = []
        for wave, sources in ((1, first_wave), (2, fallback_wave)):
            if not sources:
                continue
            source_waves.append(
                {
                    "wave": wave,
                    "execution_mode": "parallel",
                    "sources": sources,
                    "budgets": {source: int(plan["source_budgets"][source]) for source in sources},
                }
            )
        planned_sources = list(dict.fromkeys((*first_wave, *fallback_wave)))
        resolved_scope = self._safe_scope(
            {
                "project_id": request.scope.project_id or "project-rag",
                "repository_ids": request.scope.repository_ids,
                "branch": request.scope.branch,
                "commit": request.scope.commit,
                "as_of": request.as_of,
                "source_types": planned_sources,
            }
        )
        return {
            "contract_version": QUERY_PLAN_PREVIEW_VERSION,
            "project_id": resolved_scope["project_id"],
            "resolved_scope": resolved_scope,
            "intent": intent,
            "intent_signals": intent_signals,
            "query_understanding": understanding.public(include_queries=True),
            "source_waves": source_waves,
            "required_roles": list(plan["required_roles"]),
            "budgets": {
                "max_evidence": request.max_evidence,
                "max_context_tokens": request.max_context_tokens,
                "deadline_ms": request.deadline_ms,
                "per_source": dict(plan["source_budgets"]),
            },
            "generation": {
                "requested": request.mode == "answer",
                "policy": "grounded_only" if request.mode == "answer" else "disabled",
                "answer_format": request.answer_format or "concise",
            },
            "plan_digest": plan["content_digest"],
            "retrieval_performed": False,
        }

    def history(self, project_id: str, limit: int = 100) -> dict[str, Any]:
        items = self.workspace.list_query_history(project_id, limit) if self.workspace else []
        return {
            "contract_version": QUERY_HISTORY_VERSION,
            "project_id": project_id,
            "items": [
                {
                    "id": item["id"],
                    "created_at": item["created_at"],
                    **dict(item.get("detail") or {}),
                }
                for item in items
            ],
        }

    def answer(
        self,
        request: QueryRequest,
        *,
        code_engine_override: str | None = None,
        codex_engine_override: str | None = None,
        experiment_engine_override: str | None = None,
        notebook_engine_override: str | None = None,
        document_engine_override: str | None = None,
        workspace_engine_override: str | None = None,
        global_engine_override: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        trace_id = uuid4().hex
        deadline_at = (
            started + request.deadline_ms / 1000 if request.deadline_ms is not None else None
        )
        machine = transition(begin_interaction(), InteractionState.VALIDATED)
        clarification = clarification_for_request(
            question=request.question,
            policy=request.clarification_policy,
            conversation_id=request.conversation_id,
            client_turn_id=request.client_turn_id,
            parent_turn_id=request.parent_turn_id,
            context_revision=request.context_revision,
            snapshot=request.conversation_snapshot,
            explicit_commit=request.scope.commit,
            explicit_as_of=request.as_of,
        )
        if clarification is not None:
            machine = transition(machine, InteractionState.NEEDS_CLARIFICATION)
            response = self._clarification_response(
                request, trace_id, machine, clarification, started
            )
            self._record_history(request, response, intent=request.intent)
            return response

        intent_started = time.perf_counter()
        project_id = request.scope.project_id or "project-rag"
        sources = request.include or request.scope.source_types
        provider = self._provider(project_id)
        understanding_timeout = 8.0
        if request.deadline_ms is not None:
            understanding_timeout = max(0.5, min(8.0, request.deadline_ms / 1000 * 0.2))
        understanding = self.understanding.understand(
            request.question,
            explicit_intent=request.intent,
            requested_sources=sources,
            provider=provider,
            timeout_seconds=understanding_timeout,
        )
        intent = understanding.intent
        intent_signals = [
            f"algorithm:{understanding.strategy}",
            f"confidence:{understanding.intent_confidence:.4f}",
        ]
        spans = [
            self._span(
                "QUERY_UNDERSTANDING",
                intent_started,
                {
                    "strategy": understanding.strategy,
                    "intent": intent,
                    "subquery_count": len(understanding.subqueries),
                    "provider_configured": provider is not None,
                },
            )
        ]
        machine = transition(machine, InteractionState.QUERY_RESOLVED)
        scope_started = time.perf_counter()
        resolved_scope = self.platform.resolve_scope(
            project_id=project_id,
            repository_ids=request.scope.repository_ids,
            branch=request.scope.branch,
            commit=request.scope.commit,
            as_of=request.as_of,
        )
        spans.append(self._span("SCOPE_RESOLUTION", scope_started, resolved_scope))
        machine = transition(machine, InteractionState.SCOPE_SNAPSHOTTED)
        machine = transition(machine, InteractionState.PLANNED)
        machine = transition(machine, InteractionState.RETRIEVING)
        search_started = time.perf_counter()
        search_failed = False
        try:
            query_limit = (
                1 if request.deadline_ms is not None and request.deadline_ms < 10_000 else 3
            )
            retrieval_queries = list(
                dict.fromkeys(
                    self.vocabulary.retrieval_query(query, intent)
                    for query in understanding.subqueries[:query_limit]
                )
            )
            searches: list[dict[str, Any]] = []
            # Execute expansions first and the user's primary query last so operational
            # traces and legacy observers retain the original request as their final
            # visible search.  Insert responses at the front to keep fusion's primary
            # response equal to the original query.
            for retrieval_query in reversed(retrieval_queries):
                if deadline_at is not None and time.perf_counter() >= deadline_at:
                    break
                query_index = retrieval_queries.index(retrieval_query)
                global_request = GlobalSearchRequest(
                    query=retrieval_query,
                    project_id=project_id,
                    sources=sources,
                    limit=min(100, max(request.max_evidence * 4, 20)),
                    include_lineage=True,
                    max_hops=request.max_hops,
                    repository_ids=request.scope.repository_ids,
                    commit=resolved_scope.get("commit") or request.scope.commit,
                    thread_ids=request.scope.thread_ids,
                    experiment_ids=request.scope.experiment_ids,
                    document_ids=request.scope.document_ids,
                    date_from=request.scope.date_from,
                    date_to=request.scope.date_to,
                    as_of=request.as_of,
                    intent=intent,
                    deadline_ms=(
                        min(
                            30_000,
                            max(100, int(request.deadline_ms * 0.8 / len(retrieval_queries))),
                        )
                        if request.deadline_ms is not None
                        else None
                    ),
                    allowed_acl_refs=request.scope.allowed_acl_refs,
                    enforce_acl=request.scope.enforce_acl,
                )

                def legacy_search(
                    current_request: GlobalSearchRequest = global_request,
                ) -> dict[str, Any]:
                    return self.platform.search(
                        current_request,
                        code_engine_override=code_engine_override,
                        codex_engine_override=codex_engine_override,
                        experiment_engine_override=experiment_engine_override,
                        notebook_engine_override=notebook_engine_override,
                        document_engine_override=document_engine_override,
                        workspace_engine_override=workspace_engine_override,
                    )

                search_response = (
                    self.global_v2.search(
                        global_request,
                        engine_override=global_engine_override,
                        fallback=legacy_search,
                        intent=intent,
                        request_id=f"interaction-{trace_id}-{query_index + 1}",
                        context_budget_tokens=request.max_context_tokens,
                    )
                    if self.global_v2 is not None
                    else legacy_search()
                )
                searches.insert(0, search_response)
            search = fuse_search_responses(searches, query_count=len(retrieval_queries))
            source_status = normalize_source_status(search, sources)
        except Exception as error:
            search_failed = True
            source_status = source_status_for_exception(
                sources,
                error,
                latency_ms=(time.perf_counter() - search_started) * 1000,
            )
            search = self._failed_search(trace_id)
        spans.append(
            self._span(
                "SOURCE_RETRIEVAL_AND_FUSION",
                search_started,
                {
                    "candidates": search["total"],
                    "sources": list(source_status),
                    "execution_model": "platform_managed",
                    "query_understanding": understanding.strategy,
                    "query_count": len(understanding.subqueries),
                    "status": "partial" if search_failed else "complete",
                },
            )
        )
        pack_started = time.perf_counter()
        evidence_pack = self._pack(
            request,
            intent,
            intent_signals,
            resolved_scope,
            search,
            trace_id,
            source_status=source_status,
            allow_supplemental_lookup=not search_failed,
            additional_required_roles=understanding.required_roles,
        )
        evidence_pack, evidence_safety = filter_unsafe_evidence(evidence_pack)
        # Safety filtering may remove complete evidence records and add a required gap.
        # Rebuild the public organization from that post-filter authority so no cluster,
        # relation or obligation can retain an unsafe entity by identifier.
        evidence_pack["knowledge_organization"] = self._knowledge_organization(evidence_pack)
        machine = transition(machine, InteractionState.EVIDENCE_VERIFIED)
        spans.append(
            self._span(
                "EVIDENCE_PACK",
                pack_started,
                {
                    "primary": len(evidence_pack["verified_facts"]),
                    "supporting": len(evidence_pack["supporting_evidence"]),
                    "counter": len(evidence_pack["counter_evidence"]),
                    "missing": len(evidence_pack["missing_evidence"]),
                },
            )
        )
        machine = transition(machine, InteractionState.CONTEXT_PACKED)
        machine = transition(machine, InteractionState.ANSWER_MODE_SELECTED)
        deadline_exceeded = deadline_at is not None and time.perf_counter() >= deadline_at
        answerability = select_answer_mode(
            request_mode=request.mode,
            answer_format=request.answer_format,
            llm_configured=provider is not None,
            pack=evidence_pack,
            source_status=source_status,
            deadline_exceeded=deadline_exceeded,
            requested_sources=sources,
            repository_ids=request.scope.repository_ids,
            target_commit=resolved_scope.get("commit") or request.scope.commit,
        )
        answer, machine, generation_trace = self._answer(
            request,
            evidence_pack,
            answerability,
            machine,
            deadline_at=deadline_at,
        )
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        platform_trace = sanitize_trace(search.get("trace", {}))
        planner_trace = (
            platform_trace.get("planner") if isinstance(platform_trace.get("planner"), dict) else {}
        )
        corrective_rounds = len(planner_trace.get("corrective_attempts") or [])
        reason_code = answer.get("fallback_reason") or answerability.reason
        answer_mode = answer.get("answer_mode", answerability.mode.value)
        role_coverage = evidence_pack.get("role_coverage") or {}
        required_roles = list(
            dict.fromkeys(
                [
                    *list(role_coverage.get("satisfied") or []),
                    *list(role_coverage.get("missing") or []),
                ]
            )
        )
        next_actions = self._next_actions(
            source_status=source_status,
            missing_roles=list(role_coverage.get("missing") or []),
            answer_mode=str(answer_mode),
        )
        answer["next_actions"] = next_actions
        evidence_pack["next_actions"] = next_actions
        evidence_pack["trace"] = {
            "trace_id": trace_id,
            "query_id": evidence_pack["query_id"],
            "index_generations": search.get("index_generations", []),
            "query_understanding_version": QUERY_UNDERSTANDING_VERSION,
            "vocabulary_expansion_version": self.vocabulary.version,
            "fusion": platform_trace.get("fusion"),
            "sources": platform_trace.get("sources", {}),
            "planner": platform_trace.get("planner"),
            "platform": platform_trace,
            "source_status": sanitize_trace(source_status),
            "evidence_safety": evidence_safety,
            "duration_ms": elapsed,
            "spans": sanitize_trace(spans),
            "interaction_states": list(machine.history),
            "generation": generation_trace,
            "corrective_rounds": corrective_rounds,
            "sanitizer_version": TRACE_SANITIZER_VERSION,
        }
        consistency_watermark = {
            "index_generations": search.get("index_generations", []),
            "sources": {
                source: item.get("watermark")
                for source, item in source_status.items()
                if item.get("watermark") is not None
            },
        }
        snapshot = None
        response_revision = request.context_revision
        if request.conversation_id:
            response_revision = (request.context_revision or 0) + 1
            snapshot = build_conversation_snapshot(
                conversation_id=request.conversation_id,
                revision=response_revision,
                last_turn_id=request.client_turn_id,
                resolved_scope=resolved_scope,
                source_types=sources,
                intent=intent,
                citation_ids=answer.get("citations", []),
                index_generations=search.get("index_generations", []),
            )
        response = {
            "query_id": evidence_pack["query_id"],
            "resolved_scope": resolved_scope,
            "answer": answer,
            "evidence_pack": evidence_pack,
            "trace_id": trace_id,
            "state": machine.state.value,
            "interaction_id": request.client_turn_id,
            "parent_interaction_id": request.parent_turn_id,
            "interaction_state": machine.state.value,
            "reason_code": reason_code,
            "answer_mode": answer_mode,
            "required_roles": required_roles,
            "satisfied_roles": list(role_coverage.get("satisfied") or []),
            "missing_roles": list(role_coverage.get("missing") or []),
            "corrective_rounds": corrective_rounds,
            "consistency_watermark": consistency_watermark,
            "claim_verification": answer.get("claim_verification"),
            "fallback": generation_trace.get("fallback") or answer.get("fallback_reason"),
            "next_actions": next_actions,
            "source_status": source_status,
            "conversation_id": request.conversation_id,
            "client_turn_id": request.client_turn_id,
            "parent_turn_id": request.parent_turn_id,
            "context_revision": response_revision,
            "conversation_snapshot": (
                snapshot.model_dump(mode="json") if snapshot is not None else None
            ),
            "query_understanding": understanding.public(include_queries=True),
            "llm_provider": (
                {key: value for key, value in provider.public().items() if key != "base_url"}
                if provider is not None
                else {
                    "contract_version": "llm-provider-runtime-v1",
                    "configured": False,
                    "provider": "openai_compatible",
                }
            ),
            "interaction": {
                "contract_version": INTERACTION_CONTRACT_VERSION,
                "state": machine.state.value,
                "transitions": list(machine.history),
                "answer_mode": answer_mode,
                "reason": reason_code,
                "deadline_exceeded": deadline_exceeded,
                "clarification": None,
                "next_actions": next_actions,
            },
        }
        self._record_history(request, response, intent=intent)
        return response

    def _record_history(
        self,
        request: QueryRequest,
        response: dict[str, Any],
        *,
        intent: str | None,
    ) -> None:
        if self.workspace is None:
            return
        project_id = request.scope.project_id or "project-rag"
        citation_map = response.get("evidence_pack", {}).get("citation_map") or {}
        if not isinstance(citation_map, dict):
            citation_map = {}
        entity_ids: list[str] = []
        evidence_sources: list[str] = []
        for value in citation_map.values():
            if not isinstance(value, dict):
                continue
            entity_id = self._safe_identifier(value.get("entity_id"))
            if entity_id:
                entity_ids.append(entity_id)
            source = str(value.get("source") or "")
            if source in EXPLICIT_SOURCE_PATTERNS:
                evidence_sources.append(source)
        detail = {
            "query_id": self._safe_identifier(response.get("query_id")),
            "trace_id": self._safe_identifier(response.get("trace_id")),
            "question_digest": "sha256:"
            + hashlib.sha256(request.question.encode("utf-8")).hexdigest(),
            "intent": intent,
            "resolved_scope": self._safe_scope(response.get("resolved_scope") or {}),
            "interaction_state": response.get("interaction_state"),
            "answer_mode": response.get("answer_mode"),
            "reason_code": self._safe_scalar(response.get("reason_code")),
            "source_status": self._safe_source_status(response.get("source_status") or {}),
            "evidence": {
                "citation_ids": [self._safe_identifier(key) for key in citation_map],
                "sources": list(dict.fromkeys(evidence_sources)),
                "entity_ids": list(dict.fromkeys(entity_ids)),
                "count": len(citation_map),
            },
            "consistency_watermark": {
                "index_generations": [
                    self._safe_identifier(value)
                    for value in (
                        response.get("consistency_watermark", {}).get("index_generations") or []
                    )
                ]
            },
        }
        try:
            self.workspace.record_query_history(
                project_id,
                query_id=str(detail["query_id"] or "query-interaction://unknown"),
                trace_id=str(detail["trace_id"] or ""),
                detail=detail,
                clarification=response.get("interaction_state")
                == InteractionState.NEEDS_CLARIFICATION.value,
            )
        except Exception:
            # Query history is an additive review surface and must not change the
            # established answer contract when its persistence backend is unavailable.
            return

    @classmethod
    def _safe_scope(cls, scope: dict[str, Any]) -> dict[str, Any]:
        repositories = scope.get("repository_ids")
        if not repositories and isinstance(scope.get("repositories"), list):
            repositories = [
                item.get("id") for item in scope["repositories"] if isinstance(item, dict)
            ]
        return {
            "project_id": cls._safe_identifier(scope.get("project_id")) or "project-rag",
            "repository_ids": [cls._safe_identifier(value) for value in (repositories or [])],
            "branch": cls._safe_scalar(scope.get("branch")),
            "commit": cls._safe_scalar(scope.get("commit")),
            "as_of": cls._safe_scalar(scope.get("as_of")),
            "source_types": [
                value
                for value in (scope.get("source_types") or [])
                if value in EXPLICIT_SOURCE_PATTERNS
            ],
        }

    @classmethod
    def _safe_source_status(cls, value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for source, item in value.items():
            if source not in EXPLICIT_SOURCE_PATTERNS or not isinstance(item, dict):
                continue
            result[source] = {
                key: cls._safe_scalar(item.get(key))
                for key in ("status", "reason_code", "engine")
                if item.get(key) is not None
            }
        return result

    @staticmethod
    def _safe_scalar(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        text = str(sanitize_trace(str(value)))
        if text != str(value) or _ABSOLUTE_PATH_RE.search(text):
            return "redacted:sha256:" + hashlib.sha256(str(value).encode()).hexdigest()
        return text[:512]

    @classmethod
    def _safe_identifier(cls, value: Any) -> str:
        safe = cls._safe_scalar(value)
        return "" if safe is None else str(safe)

    def _clarification_response(
        self,
        request: QueryRequest,
        trace_id: str,
        machine: InteractionMachine,
        clarification: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        query_id = f"query-interaction://{trace_id}"
        resolved_scope = {
            "project_id": request.scope.project_id or "project-rag",
            "repository_ids": list(request.scope.repository_ids),
            "commit": request.scope.commit,
            "as_of": request.as_of,
        }
        trace = {
            "trace_id": trace_id,
            "query_id": query_id,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "interaction_states": list(machine.history),
            "spans": [],
            "sanitizer_version": TRACE_SANITIZER_VERSION,
        }
        evidence_pack = {
            "query_id": query_id,
            "intent": request.intent,
            "resolved_scope": resolved_scope,
            "verified_facts": [],
            "supporting_evidence": [],
            "counter_evidence": [],
            "missing_evidence": [
                {
                    "role": "query_scope",
                    "reason": "The query scope or version requires clarification.",
                }
            ],
            "citation_map": {},
            "source_status": {},
            "decision": {
                "status": "query_underspecified",
                "confidence": None,
                "confidence_status": "unavailable",
                "confidence_reason": "query_scope_unresolved",
                "grounding_coverage": {"required": 1, "satisfied": 0, "ratio": 0.0},
                "rule_version": "evidence-decision-v4",
            },
            "trace": trace,
        }
        answer = {
            "status": InteractionState.NEEDS_CLARIFICATION.value,
            "decision_status": "query_underspecified",
            "answer_mode": InteractionState.NEEDS_CLARIFICATION.value,
            "text": clarification["questions"][0],
            "citations": [],
            "applicable_scope": resolved_scope,
            "refusal": False,
            "refusal_reason": None,
            "clarification": clarification,
            "next_actions": ["补充明确的项目实体、版本或时间范围后继续查询"],
        }
        next_actions = list(answer["next_actions"])
        return {
            "query_id": query_id,
            "resolved_scope": resolved_scope,
            "answer": answer,
            "evidence_pack": evidence_pack,
            "trace_id": trace_id,
            "state": machine.state.value,
            "interaction_id": request.client_turn_id,
            "parent_interaction_id": request.parent_turn_id,
            "interaction_state": machine.state.value,
            "reason_code": clarification["reason"],
            "answer_mode": InteractionState.NEEDS_CLARIFICATION.value,
            "required_roles": ["query_scope"],
            "satisfied_roles": [],
            "missing_roles": ["query_scope"],
            "corrective_rounds": 0,
            "consistency_watermark": {"index_generations": [], "sources": {}},
            "claim_verification": None,
            "fallback": None,
            "next_actions": next_actions,
            "source_status": {},
            "conversation_id": request.conversation_id,
            "client_turn_id": request.client_turn_id,
            "parent_turn_id": request.parent_turn_id,
            "context_revision": request.context_revision,
            "conversation_snapshot": (
                request.conversation_snapshot.model_dump(mode="json")
                if request.conversation_snapshot is not None
                else None
            ),
            "interaction": {
                "contract_version": INTERACTION_CONTRACT_VERSION,
                "state": machine.state.value,
                "transitions": list(machine.history),
                "answer_mode": InteractionState.NEEDS_CLARIFICATION.value,
                "reason": clarification["reason"],
                "deadline_exceeded": False,
                "clarification": clarification,
                "next_actions": next_actions,
            },
        }

    @staticmethod
    def _failed_search(trace_id: str) -> dict[str, Any]:
        return {
            "query_id": f"global-query://{trace_id}",
            "total": 0,
            "results": [],
            "evidence_pack": {
                "citation_map": {},
                "relations": [],
                "related_entities": [],
                "context": None,
                "source_contexts": {},
                "query_plan": None,
                "conflicts_and_staleness": [],
                "global_context": None,
            },
            "trace": {
                "sources": {},
                "fusion": "retrieval_unavailable",
                "planner": None,
            },
            "index_generations": [],
        }

    def _pack(
        self,
        request: QueryRequest,
        intent: str,
        intent_signals: list[str],
        resolved_scope: dict[str, Any],
        search: dict[str, Any],
        trace_id: str,
        *,
        source_status: dict[str, dict[str, Any]],
        allow_supplemental_lookup: bool = True,
        additional_required_roles: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        results = search["results"]
        relations = search["evidence_pack"].get("relations", [])
        confirmed_relation_ids = {
            str(edge.get("id")) for edge in relations if edge.get("review_status") == "confirmed"
        }
        related_results = [
            item
            for item in search["evidence_pack"].get("related_entities", [])
            if confirmed_relation_ids.intersection(item.get("relation_ids", []))
        ]
        expanded_results = [*results, *related_results]
        primary: list[dict[str, Any]] = []
        supporting: list[dict[str, Any]] = []
        counter: list[dict[str, Any]] = []
        inferred: list[dict[str, Any]] = []
        version_differences: list[dict[str, Any]] = []
        inferred_edges = {
            edge["id"]
            for edge in relations
            if edge.get("derivation") in {"llm_inferred", "rule_derived"}
            and edge.get("review_status") != "confirmed"
        }
        for item in expanded_results:
            normalized = self._evidence(item)
            connected_relations = [
                edge
                for edge in relations
                if item["entity_id"] in {edge.get("source"), edge.get("target")}
            ]
            normalized["obligation_roles"] = sorted(
                self._roles([item], resolved_scope, connected_relations, [])
            )
            status = str(item.get("status") or "").casefold()
            if status in {"contradicted", "failed", "rejected"}:
                counter.append(normalized)
            elif item.get("relation_only") and confirmed_relation_ids.intersection(
                item.get("relation_ids", [])
            ):
                # The related entity is not promoted as a verified domain fact.
                # It is primary evidence only for the explicitly reviewed
                # relationship whose existence the query pack is evaluating.
                normalized["fact_status"] = "accepted"
                normalized["review_status"] = "confirmed"
                normalized["derivation"] = "confirmed_relation"
                primary.append(normalized)
            elif self._is_verified_fact(item):
                primary.append(normalized)
            else:
                supporting.append(normalized)
        for edge in relations:
            if edge["id"] in inferred_edges:
                inferred.append(edge)
        if intent == "staleness_check" and allow_supplemental_lookup:
            version_differences = self.platform.staleness_evidence(
                request.scope.project_id or "project-rag",
                request.scope.experiment_ids,
                request.scope.document_ids,
            )
        roles = self._roles(expanded_results, resolved_scope, relations, version_differences)
        required_roles = list(
            dict.fromkeys(
                [
                    *REQUIRED_ROLES[intent],
                    *additional_required_roles,
                    *self._explicit_source_roles(request.question),
                ]
            )
        )
        missing = [
            {
                "role": role,
                "reason_code": "required_role_not_satisfied",
                "reason": f"当前授权范围内没有满足“{self._role_label(role)}”的可核验证据。",
            }
            for role in required_roles
            if role not in roles
        ]
        primary = self._select_obligation_balanced_evidence(
            primary,
            required_roles=required_roles,
            intent=intent,
            limit=8,
        )
        relation_support = [
            item for item in supporting if "confirmed_relation" in item.get("channels", [])
        ]
        regular_support = self._select_obligation_balanced_evidence(
            [item for item in supporting if "confirmed_relation" not in item.get("channels", [])],
            required_roles=required_roles,
            intent=intent,
            limit=8,
        )
        supporting = [*regular_support[:8], *relation_support[:4]][:10]
        counter = counter[:5]
        inferred = inferred[:4]
        version_differences = version_differences[:8]
        selected_ids = {item["entity_id"] for item in [*primary, *supporting, *counter]}
        citation_map = {
            citation_id: value
            for citation_id, value in search["evidence_pack"]["citation_map"].items()
            if value["entity_id"] in selected_ids
        }
        decision = self._decision(
            intent,
            primary,
            counter,
            missing,
            version_differences,
            search["evidence_pack"].get("conflicts_and_staleness", []),
            required_role_count=len(required_roles),
        )
        raw_global_context = search["evidence_pack"].get("global_context")
        global_context = (
            dict(raw_global_context) if isinstance(raw_global_context, dict) else raw_global_context
        )
        if isinstance(global_context, dict):
            # QueryService is the only final-answer authority on this endpoint.
            # Upstream retrieval may expose a content-addressed handoff, never a
            # rendered answer or claim set.
            global_context["answer"] = None
            global_context["final_answer_authority"] = "query_service"
        retrieval_context = {
            "schema_version": "retrieval-context-bundle-v1",
            "multisource": search["evidence_pack"].get("context"),
            "source_contexts": search["evidence_pack"].get("source_contexts", {}),
            "query_plan": search["evidence_pack"].get("query_plan"),
            "global_context": global_context,
            "conflicts_and_staleness": search["evidence_pack"].get("conflicts_and_staleness", []),
            "result_count": search["total"],
            "reasoning_included": False,
        }
        comprehension_context = self._comprehension_context(
            primary=primary,
            supporting=supporting,
            counter=counter,
            missing=missing[:5],
            citation_map=citation_map,
            budget_chars=(
                request.max_context_tokens * 4 if request.max_context_tokens is not None else 12_000
            ),
        )
        pack = {
            "query_id": search["query_id"],
            "question": request.question,
            "intent": intent,
            "intent_signals": intent_signals,
            "resolved_scope": resolved_scope,
            "verified_facts": primary,
            "supporting_evidence": supporting,
            "counter_evidence": counter,
            "related_evidence": [
                item
                for item in [*primary, *supporting]
                if "confirmed_relation" in item.get("channels", [])
            ],
            "version_differences": version_differences,
            "inferred_relations": inferred,
            "missing_evidence": missing[:5],
            "citation_map": citation_map,
            "relations": relations,
            "query_plan": search["evidence_pack"].get("query_plan"),
            "conflicts_and_staleness": search["evidence_pack"].get("conflicts_and_staleness", []),
            "retrieval_context": retrieval_context,
            "comprehension_context": comprehension_context,
            "decision": decision,
            "source_status": source_status,
            "role_coverage": {
                "required": required_roles,
                "satisfied": sorted(roles.intersection(required_roles)),
                "missing": [item["role"] for item in missing[:5]],
                "stale": (
                    sorted(
                        {
                            role
                            for role in required_roles
                            if any(
                                item.get("version_alignment") in {"mismatch", "unknown"}
                                for item in expanded_results
                            )
                        }
                    )
                    if required_roles
                    else []
                ),
            },
            "trace": {"trace_id": trace_id},
        }
        pack["knowledge_organization"] = self._knowledge_organization(pack)
        return pack

    def _answer(
        self,
        request: QueryRequest,
        pack: dict[str, Any],
        answerability: AnswerabilityDecision,
        machine: InteractionMachine,
        *,
        deadline_at: float | None,
    ) -> tuple[dict[str, Any], InteractionMachine, dict[str, Any]]:
        generation_trace: dict[str, Any] = {
            "attempted": False,
            "fallback": None,
            "citation_verification": None,
        }
        if answerability.mode is AnswerMode.REFUSAL:
            answer = self._deterministic_answer(pack)
            answer.update(
                {
                    "status": (answer["status"] if request.mode == "evidence" else "refused"),
                    "answer_mode": AnswerMode.REFUSAL.value,
                    "refusal": True,
                    "refusal_reason": answerability.reason,
                }
            )
            return (
                answer,
                transition(machine, InteractionState.REFUSED),
                generation_trace,
            )
        if answerability.mode is AnswerMode.PARTIAL:
            answer = self._deterministic_answer(pack)
            answer.update(
                {
                    "status": "partial",
                    "answer_mode": AnswerMode.PARTIAL.value,
                    "partial": True,
                    "refusal": False,
                    "refusal_reason": None,
                }
            )
            return (
                answer,
                transition(machine, InteractionState.PARTIAL),
                generation_trace,
            )
        if answerability.mode is AnswerMode.RETRIEVAL_ONLY:
            answer = self._deterministic_answer(pack)
            answer.update(
                {
                    "status": "retrieval_only",
                    "answer_mode": AnswerMode.RETRIEVAL_ONLY.value,
                    "refusal": False,
                    "refusal_reason": None,
                }
            )
            return (
                answer,
                transition(machine, InteractionState.RETRIEVAL_ONLY),
                generation_trace,
            )

        machine = transition(machine, InteractionState.GENERATING)
        generation_trace["attempted"] = True
        generated_started = time.perf_counter()
        try:
            text = self._generate(request, pack, deadline_at=deadline_at)
        except Exception as error:
            generation_trace.update(
                {
                    "duration_ms": round((time.perf_counter() - generated_started) * 1000, 2),
                    "fallback": "generator_failure_to_retrieval_only",
                    "error_type": type(error).__name__,
                    "error_code": (
                        str(error)
                        if re.fullmatch(r"llm_[a-z0-9_]{1,100}", str(error))
                        else "provider_or_transport_failure"
                    ),
                }
            )
            answer = self._deterministic_answer(pack)
            answer.update(
                {
                    "status": "retrieval_only",
                    "answer_mode": AnswerMode.RETRIEVAL_ONLY.value,
                    "fallback_reason": "generator_failure",
                    "refusal": False,
                    "refusal_reason": None,
                }
            )
            return (
                answer,
                transition(machine, InteractionState.RETRIEVAL_ONLY),
                generation_trace,
            )

        generation_trace["duration_ms"] = round((time.perf_counter() - generated_started) * 1000, 2)
        machine = transition(machine, InteractionState.CLAIM_VERIFYING)
        validation = validate_claim_citations(
            text,
            pack,
            requested_sources=request.include or request.scope.source_types,
            repository_ids=request.scope.repository_ids,
            target_commit=pack["resolved_scope"].get("commit") or request.scope.commit,
        )
        repair_applied = False
        if not validation.supported:
            repaired = repair_generated_text(text, validation)
            repair_applied = True
            if repaired:
                repaired_validation = validate_claim_citations(
                    repaired,
                    pack,
                    requested_sources=request.include or request.scope.source_types,
                    repository_ids=request.scope.repository_ids,
                    target_commit=pack["resolved_scope"].get("commit") or request.scope.commit,
                )
                if repaired_validation.supported:
                    text = repaired
                    validation = repaired_validation
        generation_trace["citation_verification"] = {
            **validation.trace_summary(),
            "repair_applied": repair_applied,
        }
        if not validation.supported:
            generation_trace["fallback"] = "citation_failure_to_retrieval_only"
            answer = self._deterministic_answer(pack)
            answer.update(
                {
                    "status": "retrieval_only",
                    "answer_mode": AnswerMode.RETRIEVAL_ONLY.value,
                    "fallback_reason": "citation_verification_failed",
                    "claim_verification": validation.trace_summary(),
                    "refusal": False,
                    "refusal_reason": None,
                }
            )
            return (
                answer,
                transition(machine, InteractionState.RETRIEVAL_ONLY),
                generation_trace,
            )

        referenced = sorted(
            {item for item in re.findall(r"\[(E\d+)\]", text) if item in pack["citation_map"]},
            key=lambda item: int(item[1:]),
        )
        return (
            {
                "status": pack["decision"]["status"],
                "decision_status": pack["decision"]["status"],
                "answer_mode": AnswerMode.GROUNDED_GENERATION.value,
                "text": text,
                "citations": referenced,
                "claim_verification": validation.trace_summary(),
                "applicable_scope": pack["resolved_scope"],
                "refusal": False,
                "refusal_reason": None,
            },
            transition(machine, InteractionState.COMPLETE),
            generation_trace,
        )

    def _generate(
        self,
        request: QueryRequest,
        pack: dict[str, Any],
        *,
        deadline_at: float | None,
    ) -> str:
        provider = self._provider(request.scope.project_id or "project-rag")
        if provider is None:
            raise RuntimeError("llm_provider_not_configured")
        blocks = []
        by_entity = {
            item["entity_id"]: item
            for item in [
                *pack["verified_facts"],
                *pack["supporting_evidence"],
                *pack["counter_evidence"],
            ]
        }
        context_char_budget = (request.max_context_tokens or 12_000) * 4
        used_chars = 0
        for citation_id, citation in pack["citation_map"].items():
            item = by_entity.get(citation["entity_id"], {})
            block = (
                f"[{citation_id}] {citation['locator']}\n"
                f"source={citation['source']} version={citation.get('version')}\n"
                f"{item.get('snippet', '')}"
            )
            if blocks and used_chars + len(block) > context_char_budget:
                break
            blocks.append(block[: max(0, context_char_budget - used_chars)])
            used_chars += len(blocks[-1])
        system = (
            "Select grounded claims from the supplied Evidence Pack and return one JSON object "
            "with a claims array. Every claim is {text, citation_ids}. The text must be a concise "
            "contiguous verbatim excerpt copied from exactly one cited title or snippet. Each "
            "citation_ids array must therefore contain exactly one supplied E# ID. Do not "
            "put citation markers inside text. Never invent paths, commits, run IDs, metrics, "
            "versions, or document locations. Omit any claim that cannot be supported directly."
        )
        format_instruction = {
            "concise": "Use a concise answer.",
            "detailed": "Use a detailed answer while keeping one claim per line.",
            "evidence_only": "Return only cited evidence statements.",
        }.get(request.answer_format or "", "")
        payload = {
            "model": provider.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            **provider_chat_options(provider, purpose="generation"),
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        f"Question:\n{request.question}\n\nDecision:\n{pack['decision']}\n\n"
                        f"Missing:\n{pack['missing_evidence']}\n\nEvidence:\n"
                        + "\n\n".join(blocks)
                        + "\n\nOutput contract:\n"
                        + '{"claims":[{"text":"extractive supported statement",'
                        + '"citation_ids":["E1"]}]}'
                        + (f"\n\nStyle:\n{format_instruction}" if format_instruction else "")
                    ),
                },
            ],
        }
        assert_outbound_evidence_safe(request.question)
        assert_outbound_evidence_safe("\n\n".join(blocks))
        timeout_seconds = 60.0
        if deadline_at is not None:
            timeout_seconds = min(timeout_seconds, deadline_at - time.perf_counter())
            if timeout_seconds <= 0:
                raise TimeoutError("query deadline exceeded before generation")
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                provider.base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"].get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("llm_provider_empty_generation")
            return self._render_grounded_claims(content, pack)

    @staticmethod
    def _render_grounded_claims(content: str, pack: dict[str, Any]) -> str:
        """Turn a bounded model selection into deterministic cited claim lines.

        Natural-language citation placement is too provider-sensitive to be a
        security boundary.  The model therefore selects claim text and evidence
        IDs in JSON; the service validates membership and owns final rendering
        before the existing entailment verifier runs.
        """

        document = json.loads(content)
        if not isinstance(document, dict) or set(document) != {"claims"}:
            raise ValueError("llm_generation_contract_mismatch")
        raw_claims = document.get("claims")
        if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= 12:
            raise ValueError("llm_generation_claim_count_invalid")
        citation_map = pack.get("citation_map")
        if not isinstance(citation_map, dict):
            raise ValueError("llm_generation_citation_authority_missing")
        evidence_by_entity = {
            str(item.get("entity_id")): item
            for item in [
                *list(pack.get("verified_facts") or ()),
                *list(pack.get("supporting_evidence") or ()),
                *list(pack.get("counter_evidence") or ()),
            ]
            if isinstance(item, dict) and item.get("entity_id")
        }

        rendered: list[str] = []
        total_chars = 0
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict) or set(raw_claim) != {
                "text",
                "citation_ids",
            }:
                raise ValueError("llm_generation_claim_shape_invalid")
            text = raw_claim.get("text")
            citation_ids = raw_claim.get("citation_ids")
            if not isinstance(text, str) or not isinstance(citation_ids, list):
                raise ValueError("llm_generation_claim_type_invalid")
            text = " ".join(text.split()).strip()
            text = re.sub(r"\s*\[E\d+\]\s*", " ", text).strip()
            if not 3 <= len(text) <= 600 or evidence_content_findings({"snippet": text}):
                raise ValueError("llm_generation_claim_text_invalid")
            normalized_ids = tuple(
                dict.fromkeys(item for item in citation_ids if isinstance(item, str))
            )
            if (
                len(normalized_ids) != 1
                or len(normalized_ids) != len(citation_ids)
                or any(item not in citation_map for item in normalized_ids)
            ):
                raise ValueError("llm_generation_citation_membership_invalid")
            citation = citation_map[normalized_ids[0]]
            if not isinstance(citation, dict):
                raise ValueError("llm_generation_citation_authority_missing")
            evidence = evidence_by_entity.get(str(citation.get("entity_id") or ""))
            if evidence is None:
                raise ValueError("llm_generation_citation_authority_missing")
            authority_text = " ".join(
                f"{evidence.get('title') or ''} {evidence.get('snippet') or ''}".split()
            )
            if text.casefold() not in authority_text.casefold():
                raise ValueError("llm_generation_excerpt_not_in_evidence")
            sentences = tuple(
                segment.strip().rstrip("。！？.!?").rstrip()
                for segment in re.split(r"(?<=[。！？!?])\s+|(?<=\.)\s+", text)
                if segment.strip()
            )
            if not sentences or any(len(sentence) < 3 for sentence in sentences):
                raise ValueError("llm_generation_claim_text_invalid")
            for sentence in sentences:
                line = f"{sentence} [{normalized_ids[0]}]."
                total_chars += len(line)
                if total_chars > 6_000:
                    raise ValueError("llm_generation_output_too_large")
                rendered.append(line)
        return "\n".join(rendered)

    def _roles(
        self,
        results: list[dict[str, Any]],
        scope: dict[str, Any],
        relations: list[dict[str, Any]],
        version_differences: list[dict[str, Any]],
    ) -> set[str]:
        roles: set[str] = set()
        sources = {item["source"] for item in results}
        roles.update(f"source_{source}" for source in sources)
        types = {str(item["entity_type"]).casefold() for item in results}
        code_items = [item for item in results if item["source"] == "code"]
        implementation_types = {
            "codesymbol",
            "fileversion",
            "function",
            "class",
            "method",
            "module",
            "searchview",
        }
        history_types = {"commit", "diffhunk", "testresult"}
        if any(
            str(item["entity_type"]).casefold() in implementation_types
            and item.get("version_alignment") != "mismatch"
            for item in code_items
        ):
            roles.update({"current_code", "implementation"})
        if any(str(item["entity_type"]).casefold() in history_types for item in code_items):
            roles.add("historical_code")
        if any(item.get("version") for item in code_items):
            roles.update({"version", "commit"})
        if scope.get("commit"):
            roles.update({"version", "current_commit"})
        if {"testresult", "validationresult"} & types or any(
            self._is_test_source_evidence(item) for item in code_items
        ):
            roles.add("tests")
        if "diffhunk" in types:
            roles.update({"diff", "diff_or_revalidation"})
        if "codex" in sources:
            roles.add("development_context")
        if {
            "usergoal",
            "agentmessage",
            "developmenteepisode",
            "developmentepisode",
            "summary",
            "codexturn",
        } & types:
            roles.add("decision_or_goal")
        if {"experimentrun", "experimentrunv2"} & types:
            roles.update({"run", "run_or_metric", "supporting_run"})
        if {"metric", "metricobservationv2"} & types:
            roles.update({"metric", "run_or_metric"})
        if "claim" in types:
            roles.add("claim")
        if any(item["source"] == "document" and item.get("locator") for item in results):
            roles.add("source_location")
        if any(
            item.get("dataset_id") or "dataset" in item.get("snippet", "").casefold()
            for item in results
        ):
            roles.add("dataset_version")
        if any(
            "config=" in item.get("snippet", "").casefold()
            or "parameter" in item.get("snippet", "").casefold()
            for item in results
        ):
            roles.add("configuration")
        if any("environment" in item.get("snippet", "").casefold() for item in results):
            roles.add("environment")
        if any(edge.get("predicate") == "uses" for edge in relations):
            roles.add("experiment_commit")
        if version_differences:
            roles.add("diff_or_revalidation")
        if len(sources) >= 2:
            roles.add("multiple_sources")
        return roles

    @staticmethod
    def _is_test_source_evidence(item: dict[str, Any]) -> bool:
        searchable = " ".join(
            str(item.get(key) or "") for key in ("path", "title", "locator", "entity_type")
        ).casefold()
        return any(
            marker in searchable
            for marker in (
                "tests/",
                "/test_",
                "\\test_",
                "pytest",
                "validationresult",
                "testresult",
                "quality_gate",
            )
        )

    @classmethod
    def _is_production_implementation_evidence(cls, item: dict[str, Any]) -> bool:
        """Identify executable implementation evidence, excluding docs and tests."""

        if str(item.get("source") or "").casefold() != "code":
            return False
        if cls._is_test_source_evidence(item):
            return False
        entity_type = str(item.get("entity_type") or "").casefold()
        if entity_type not in {
            "codesymbol",
            "fileversion",
            "function",
            "class",
            "method",
            "module",
            "searchview",
        }:
            return False
        searchable = (
            " ".join(str(item.get(key) or "") for key in ("path", "locator", "title"))
            .replace("\\", "/")
            .casefold()
        )
        if any(
            marker in searchable
            for marker in (
                "/docs/",
                "docs/",
                "/reviews/",
                "/evals/",
                ".md#",
                ".rst#",
                ".txt#",
            )
        ):
            return False
        return any(
            marker in searchable
            for marker in (
                "src/",
                "frontend/src/",
                "plugins/",
                ".py#",
                ".ts#",
                ".tsx#",
                ".rs#",
            )
        )

    @staticmethod
    def _is_documentation_code_evidence(item: dict[str, Any]) -> bool:
        if str(item.get("source") or "").casefold() != "code":
            return False
        searchable = (
            " ".join(str(item.get(key) or "") for key in ("path", "locator", "title"))
            .replace("\\", "/")
            .casefold()
        )
        return any(
            marker in searchable
            for marker in (
                "/docs/",
                "docs/",
                ".md#",
                ".rst#",
                ".txt#",
            )
        )

    @classmethod
    def _evidence_priority(cls, item: dict[str, Any], intent: str) -> tuple[Any, ...]:
        """Prefer reviewable domain evidence over protocol or graph echoes."""

        source = str(item.get("source") or "").casefold()
        entity_type = str(item.get("entity_type") or "").casefold()
        noisy_protocol = entity_type in {
            "toolresult",
            "toolcall",
            "commandexecution",
            "rawmessage",
        }
        source_rank = {
            "code": 0,
            "experiment": 1,
            "notebook": 1,
            "document": 2,
            "workspace": 2,
            "codex": 3,
        }.get(source, 4)
        if intent in {"change_tracking", "decision_trace"} and source == "codex":
            source_rank = 0
        production_rank = (
            0
            if cls._is_production_implementation_evidence(item)
            else (1 if source == "code" else 2)
        )
        title = str(item.get("title") or "")
        leaf_symbol = title.rsplit(".", 1)[-1]
        private_rank = 1 if leaf_symbol.startswith("_") and not leaf_symbol.startswith("__") else 0
        # Tests are a required supporting obligation for current implementation
        # queries, but they must not displace the implementation itself from the
        # answer lead. Validation/reproduction intents invert that preference.
        is_test = cls._is_test_source_evidence(item)
        test_rank = (
            (0 if is_test else 1)
            if intent in {"experiment_validation", "reproduction"}
            else (1 if is_test else 0)
        )
        return (
            1 if item.get("relation_only") else 0,
            1 if noisy_protocol else 0,
            source_rank,
            production_rank
            if intent in {"current_implementation", "historical_implementation"}
            else 0,
            private_rank if intent == "current_implementation" else 0,
            test_rank,
            -float(item.get("score") or 0.0),
            str(item.get("entity_id") or ""),
        )

    @classmethod
    def _select_obligation_balanced_evidence(
        cls,
        items: list[dict[str, Any]],
        *,
        required_roles: list[str],
        intent: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Select a compact pack that represents each obligation without label echo.

        A test symbol is still code and can therefore carry ``current_code`` and
        ``version`` roles.  Using it as the representative for every role produces
        a formally complete but practically misleading answer.  Role-specific
        preference keeps production implementation and test evidence distinct,
        while the final fill preserves the normal evidence ordering.
        """

        eligible_items = items
        if intent in {"current_implementation", "historical_implementation"}:
            implementation_items = [
                item for item in items if not cls._is_documentation_code_evidence(item)
            ]
            if implementation_items:
                eligible_items = implementation_items
        ordered = sorted(
            eligible_items,
            key=lambda item: cls._evidence_priority(item, intent),
        )
        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()

        for role in required_roles:
            candidates = [
                item
                for item in ordered
                if role in set(item.get("obligation_roles") or ())
                and str(item.get("entity_id") or "") not in selected_ids
            ]
            if not candidates:
                continue

            def role_key(item: dict[str, Any], obligation_role: str = role) -> tuple[Any, ...]:
                is_test = cls._is_test_source_evidence(item)
                if obligation_role == "tests":
                    role_rank = 0 if is_test else 1
                elif obligation_role in {
                    "current_code",
                    "implementation",
                    "version",
                    "commit",
                }:
                    if cls._is_production_implementation_evidence(item):
                        role_rank = 0
                    else:
                        role_rank = 2 if is_test else 1
                else:
                    role_rank = 0
                return (role_rank, *cls._evidence_priority(item, intent))

            chosen = min(candidates, key=role_key)
            selected.append(chosen)
            selected_ids.add(str(chosen.get("entity_id") or ""))
            if len(selected) >= limit:
                return selected

        for item in ordered:
            entity_id = str(item.get("entity_id") or "")
            if entity_id in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(entity_id)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _role_label(role: str) -> str:
        return {
            "current_code": "当前代码",
            "version": "明确版本",
            "tests": "测试证据",
            "safe_generation_context": "安全生成上下文",
            "implementation": "实现证据",
            "development_context": "研发上下文",
            "multiple_sources": "多来源交叉证据",
        }.get(role, role)

    def _deterministic_answer(self, pack: dict[str, Any]) -> dict[str, Any]:
        """Build a useful grounded analysis when no generative model is configured."""

        citation_by_entity = {
            value["entity_id"]: citation_id for citation_id, value in pack["citation_map"].items()
        }
        status = pack["decision"]["status"]
        status_labels = {
            "supported": "现有证据支持",
            "contradicted": "现有证据存在反驳",
            "potentially_stale": "结论可能已经过时",
            "insufficient_evidence": "证据不足，暂不能下结论",
        }
        role_labels = {
            "current_code": "当前代码",
            "historical_code": "历史代码",
            "implementation": "实现证据",
            "version": "版本定位",
            "commit": "提交版本",
            "tests": "测试证据",
            "diff": "代码差异",
            "development_context": "开发上下文",
            "decision_or_goal": "研发目标或决策",
            "run": "实验运行",
            "run_or_metric": "实验运行或指标",
            "metric": "实验指标",
            "dataset_version": "数据集版本",
            "claim": "文档主张",
            "source_location": "原始出处",
            "configuration": "运行配置",
            "environment": "运行环境",
            "supporting_run": "支撑实验",
            "experiment_commit": "实验与提交的绑定",
            "current_commit": "当前提交",
            "diff_or_revalidation": "差异或重新验证",
            "multiple_sources": "至少两类来源",
            "source_code": "代码证据",
            "source_codex": "Codex 会话证据",
            "source_experiment": "实验与指标证据",
            "source_notebook": "Notebook 单元与输出证据",
            "source_document": "文档或主张证据",
            "source_workspace": "科研任务与迭代证据",
        }

        def finding(item: dict[str, Any]) -> dict[str, Any]:
            citation = citation_by_entity.get(item["entity_id"])
            snippet = re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip()
            if len(snippet) > 180:
                snippet = snippet[:177].rstrip() + "…"
            statement = str(item.get("title") or item["entity_id"])
            if snippet and snippet.casefold() not in statement.casefold():
                statement += f"：{snippet}"
            return {
                "entity_id": item["entity_id"],
                "source": item["source"],
                "statement": statement,
                "citation": citation,
                "version": item.get("version"),
                "relation_only": bool(
                    item.get("relation_only") or "confirmed_relation" in item.get("channels", [])
                ),
            }

        # A relation-expanded item can satisfy an obligation and therefore land in
        # ``verified_facts`` while a stronger directly retrieved item is kept as
        # supporting evidence. Rank the two positive buckets together, then retain
        # at least one honest representative for each required obligation. This
        # prevents a high-scoring test suite from being presented as the production
        # implementation while still keeping test evidence in the answer.
        answer_items = self._select_obligation_balanced_evidence(
            [*pack["verified_facts"], *pack["supporting_evidence"]],
            required_roles=list(pack.get("role_coverage", {}).get("required") or ()),
            intent=str(pack.get("intent") or "global_synthesis"),
            limit=6,
        )
        findings = [finding(item) for item in answer_items]
        counter = [finding(item) for item in pack["counter_evidence"][:2]]
        referenced = [item["citation"] for item in [*findings, *counter] if item.get("citation")]
        source_counts = defaultdict(int)
        for item in [*pack["verified_facts"], *pack["supporting_evidence"]]:
            source_counts[item["source"]] += 1
        versions = list(
            dict.fromkeys(
                str(item["version"])
                for item in [*pack["verified_facts"], *pack["supporting_evidence"]]
                if item.get("version")
            )
        )[:5]
        missing_roles = [item["role"] for item in pack["missing_evidence"]]
        coverage = pack["decision"]["grounding_coverage"]
        sentences = [
            f"{status_labels.get(status, status)}"
            f"（证据角色覆盖 {coverage['satisfied']}/{coverage['required']}；"
            "未经过可信校准，不输出概率置信度）。"
        ]
        if findings:
            rendered = []
            for item in findings:
                suffix = f" [{item['citation']}]" if item.get("citation") else ""
                rendered.append(item["statement"] + suffix)
            sentences.append("主要依据：" + "；".join(rendered) + "。")
        if counter:
            rendered = []
            for item in counter:
                suffix = f" [{item['citation']}]" if item.get("citation") else ""
                rendered.append(item["statement"] + suffix)
            sentences.append("反证或失败证据：" + "；".join(rendered) + "。")
        if versions:
            sentences.append("适用版本：" + "、".join(versions) + "。")
        if missing_roles:
            sentences.append(
                "仍缺少：" + "、".join(role_labels.get(role, role) for role in missing_roles) + "。"
            )
        if pack.get("conflicts_and_staleness"):
            sentences.append(
                f"检测到 {len(pack['conflicts_and_staleness'])} 项冲突、失败状态或版本差异，"
                "需按证据定位复核。"
            )
        analysis = {
            "findings": findings,
            "counter_findings": counter,
            "source_coverage": dict(sorted(source_counts.items())),
            "versions": versions,
            "missing_roles": missing_roles,
            "relation_paths": pack["relations"][:12],
            "conflicts_and_staleness": pack.get("conflicts_and_staleness", [])[:12],
            "query_plan": pack.get("query_plan"),
        }
        return {
            "status": "retrieval_only" if referenced else "insufficient_evidence",
            "decision_status": status,
            "text": "".join(sentences),
            "citations": list(dict.fromkeys(referenced)),
            "analysis": analysis,
            "applicable_scope": pack["resolved_scope"],
            "refusal": status == "insufficient_evidence",
            "refusal_reason": (
                "required authorized evidence is missing"
                if status == "insufficient_evidence"
                else None
            ),
        }

    @staticmethod
    def _decision(
        intent,
        primary,
        counter,
        missing,
        diffs,
        conflicts,
        *,
        required_role_count: int,
    ) -> dict[str, Any]:
        if counter or any(item.get("kind") == "cross_source_difference" for item in conflicts):
            status = "contradicted"
        elif intent == "staleness_check" and any(
            item.get("status") == "potentially_stale" for item in diffs
        ):
            status = "potentially_stale"
        elif missing:
            status = "insufficient_evidence"
        elif primary:
            status = "supported"
        else:
            status = "insufficient_evidence"
        coverage = 1.0 - len(missing) / max(1, required_role_count)
        return {
            "status": status,
            "confidence": None,
            "confidence_status": "unavailable",
            "confidence_reason": "reviewed_calibration_unavailable",
            "grounding_coverage": {
                "required": required_role_count,
                "satisfied": max(0, required_role_count - len(missing)),
                "ratio": round(max(0.0, min(1.0, coverage)), 3),
            },
            "rule_version": "evidence-decision-v4",
        }

    @staticmethod
    def _explicit_source_roles(question: str) -> list[str]:
        return [
            role for role, pattern in EXPLICIT_SOURCE_PATTERNS.items() if pattern.search(question)
        ]

    @staticmethod
    def _comprehension_context(
        *,
        primary: list[dict[str, Any]],
        supporting: list[dict[str, Any]],
        counter: list[dict[str, Any]],
        missing: list[dict[str, Any]],
        citation_map: dict[str, dict[str, Any]],
        budget_chars: int = 12_000,
    ) -> dict[str, Any]:
        citation_by_entity = {
            value["entity_id"]: citation_id for citation_id, value in citation_map.items()
        }

        def blocks(role: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            projected = []
            for item in items:
                citation_id = citation_by_entity.get(item["entity_id"])
                if not citation_id:
                    continue
                statement = " ".join(str(item.get("snippet") or "").split())[:600]
                projected.append(
                    {
                        "citation_id": citation_id,
                        "role": role,
                        "entity_id": item["entity_id"],
                        "source": item["source"],
                        "entity_type": item["entity_type"],
                        "statement": statement,
                        "locator": item["locator"],
                        "version": item.get("version"),
                    }
                )
            return projected

        selected = [
            *blocks("verified", primary),
            *blocks("supporting", supporting),
            *blocks("counter", counter),
        ]
        rendered = "\n".join(
            f"[{item['citation_id']}] {item['role']} {item['source']}/"
            f"{item['entity_type']}: {item['statement']}"
            for item in selected
        )[:budget_chars]
        payload = {
            "schema_version": "comprehension-context-v1",
            "evidence": selected,
            "missing": missing,
            "rendered": rendered,
            "reasoning_included": False,
            "derived_only_from_selected_evidence": True,
        }
        payload["content_digest"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        return payload

    @staticmethod
    def _evidence_cluster_role(item: dict[str, Any]) -> str:
        """Classify selected evidence by reviewable subject, not query obligation.

        Obligation roles answer whether a query is sufficiently supported.  They are
        intentionally broad (for example ``current_code``) and therefore make poor
        knowledge-navigation buckets.  This classifier only uses already-public,
        selected evidence metadata and keeps the projection deterministic.
        """

        source = str(item.get("source") or "").casefold()
        entity_type = str(item.get("entity_type") or "").casefold()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        searchable = " ".join(
            str(value or "")
            for value in (
                item.get("path"),
                item.get("title"),
                item.get("subtitle"),
                item.get("locator"),
                entity_type,
                metadata.get("kind"),
                metadata.get("symbol"),
            )
        ).casefold()

        if source == "codex":
            return "implementation_history"
        if source == "experiment":
            return "experimental_results"
        if source == "notebook":
            return "analysis_reproduction"
        if source == "workspace":
            return "project_context"

        if any(marker in searchable for marker in ("security", "acl", "scope", "auth")):
            return "governance_security"
        if any(
            marker in searchable
            for marker in ("test_", "/test", "tests/", "pytest", "validation", "quality_gate")
        ):
            return "validation_quality"
        if any(
            marker in searchable
            for marker in ("src-tauri", "tauri", "cargo.toml", "desktop", "windows", "macos")
        ):
            return "desktop_delivery"
        if any(
            marker in searchable
            for marker in ("/wiki/", "wiki_", "wikiworkbench", "knowledge page", "knowledge_page")
        ):
            return "wiki_organization"
        if any(
            marker in searchable
            for marker in (
                "/query/",
                "retrieval",
                "retriever",
                "rerank",
                "embedding",
                "fusion",
                "query planner",
                "query_",
            )
        ):
            return "retrieval_pipeline"
        if any(
            marker in searchable
            for marker in ("frontend/", ".tsx", ".css", "workbench", "interaction", "ui ")
        ):
            return "product_interaction"
        if any(
            marker in searchable
            for marker in ("router.py", "api.py", "runtime.py", "platform", "service.py")
        ):
            return "service_integration"
        if any(
            marker in searchable
            for marker in ("docs/", "architecture", "design", "roadmap", "readme")
        ):
            return "architecture_design"
        if source == "document":
            return "documented_knowledge"
        if "relation" in entity_type or "edge" in entity_type:
            return "relation_graph"
        return "current_implementation"

    @staticmethod
    def _knowledge_organization(pack: dict[str, Any]) -> dict[str, Any]:
        """Project an evidence pack into a deterministic human-review structure.

        This is deliberately not a generated summary.  Every cluster, obligation,
        relation and risk points back to the already selected evidence pack so that
        clients never need to infer a second, inconsistent organization model.
        """

        primary = list(pack.get("verified_facts") or [])
        supporting = list(pack.get("supporting_evidence") or [])
        counter = list(pack.get("counter_evidence") or [])
        selected = [*primary, *supporting, *counter]
        selected_by_id = {str(item["entity_id"]): item for item in selected}
        citation_by_entity = {
            str(value["entity_id"]): citation_id
            for citation_id, value in (pack.get("citation_map") or {}).items()
        }
        role_coverage = dict(pack.get("role_coverage") or {})
        satisfied = set(role_coverage.get("satisfied") or [])
        missing = set(role_coverage.get("missing") or [])
        required = list(
            dict.fromkeys(
                [
                    *list(role_coverage.get("required") or []),
                    *sorted(satisfied),
                    *sorted(missing),
                ]
            )
        )

        relation_paths = []
        for edge in pack.get("relations") or []:
            source_id = str(edge.get("source") or "")
            target_id = str(edge.get("target") or "")
            if source_id not in selected_by_id or target_id not in selected_by_id:
                continue
            relation_paths.append(
                {
                    "relation_id": str(edge.get("id") or ""),
                    "source_entity_id": source_id,
                    "target_entity_id": target_id,
                    "predicate": str(edge.get("predicate") or "related_to"),
                    "domain": str(edge.get("domain") or "unknown"),
                    "review_status": edge.get("review_status"),
                    "derivation": edge.get("derivation"),
                }
            )
        relation_paths = relation_paths[:20]

        obligations = []
        for role in required:
            role_items = [
                item for item in selected if role in set(item.get("obligation_roles") or ())
            ]
            basis = "direct_evidence"
            if not role_items and role == "multiple_sources" and role in satisfied:
                role_items = selected
                basis = "cross_source_coverage"
            elif not role_items and role in {"experiment_commit", "diff_or_revalidation"}:
                relation_ids = {item["relation_id"] for item in relation_paths}
                role_items = [
                    item
                    for item in selected
                    if relation_ids.intersection(item.get("relation_ids") or ())
                ]
                basis = "confirmed_relation"
            elif not role_items and role in satisfied:
                basis = "resolved_scope"
            obligations.append(
                {
                    "role": role,
                    "status": (
                        "satisfied"
                        if role in satisfied
                        else "missing"
                        if role in missing
                        else "unknown"
                    ),
                    "basis": basis,
                    "evidence_entity_ids": [str(item["entity_id"]) for item in role_items],
                    "citation_ids": [
                        citation_by_entity[str(item["entity_id"])]
                        for item in role_items
                        if str(item["entity_id"]) in citation_by_entity
                    ],
                    "sources": sorted({str(item["source"]) for item in role_items}),
                }
            )

        counter_ids = {str(item["entity_id"]) for item in counter}
        clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in selected:
            if str(item["entity_id"]) in counter_ids:
                continue
            clusters[UnifiedQueryService._evidence_cluster_role(item)].append(item)
        evidence_clusters = [
            {
                "cluster_id": "cluster-" + hashlib.sha256(role.encode()).hexdigest()[:16],
                "role": role,
                "entity_ids": [str(item["entity_id"]) for item in items],
                "citation_ids": [
                    citation_by_entity[str(item["entity_id"])]
                    for item in items
                    if str(item["entity_id"]) in citation_by_entity
                ],
                "sources": sorted({str(item["source"]) for item in items}),
                "obligation_roles": sorted(
                    {
                        str(obligation_role)
                        for item in items
                        for obligation_role in item.get("obligation_roles") or ()
                    }
                ),
                "verified_count": sum(item in primary for item in items),
                "supporting_count": sum(item in supporting for item in items),
            }
            for role, items in sorted(clusters.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        ]

        risks = [
            {
                "kind": "counter_evidence",
                "entity_ids": [str(item["entity_id"])],
                "sources": [str(item["source"])],
                "status": str(item.get("status") or "counter"),
                "reason": "selected_counter_evidence",
            }
            for item in counter
        ]
        risks.extend(
            {
                "kind": str(item.get("kind") or "conflict_or_staleness"),
                "entity_ids": [str(value) for value in item.get("entity_ids") or ()],
                "sources": [str(value) for value in item.get("sources") or ()],
                "status": str(item.get("status") or "REVIEW_REQUIRED"),
                "reason": str(item.get("reason") or "review_required"),
            }
            for item in pack.get("conflicts_and_staleness") or ()
        )
        risks.extend(
            {
                "kind": "version_difference",
                "entity_ids": [str(item.get("entity_id"))] if item.get("entity_id") else [],
                "sources": [str(item.get("source"))] if item.get("source") else [],
                "status": str(item.get("status") or "REVIEW_REQUIRED"),
                "reason": str(item.get("reason") or "version_difference"),
            }
            for item in pack.get("version_differences") or ()
        )
        decision_status = str((pack.get("decision") or {}).get("status") or "insufficient_evidence")
        payload = {
            "schema_version": "query-knowledge-organization-v1",
            "question_digest": "sha256:"
            + hashlib.sha256(str(pack.get("question") or "").encode()).hexdigest(),
            "decision": {
                "status": decision_status,
                "complete": bool(
                    decision_status == "supported" and selected and not missing and not risks
                ),
            },
            "obligations": obligations,
            "evidence_clusters": evidence_clusters,
            "relation_paths": relation_paths,
            "risks": risks,
            "gaps": list(pack.get("missing_evidence") or []),
            "selected_evidence_count": len(selected),
            "counter_evidence_count": len(counter),
            "reasoning_included": False,
            "derived_only_from_evidence_pack": True,
        }
        payload["content_digest"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        return payload

    @staticmethod
    def _evidence(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": item["entity_id"],
            "source": item["source"],
            "entity_type": item["entity_type"],
            "title": item["title"],
            "snippet": item["snippet"],
            "locator": item["locator"],
            "version": item.get("version"),
            "status": item.get("status"),
            "fact_status": item.get("fact_status"),
            "review_status": item.get("review_status"),
            "derivation": item.get("derivation"),
            "authority": item.get("authority"),
            "version_alignment": item.get("version_alignment"),
            "score": item["score"],
            "channels": item.get("channels", []),
            "repository_id": item.get("repository_id"),
            "path": item.get("path"),
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
            "thread_id": item.get("thread_id"),
            "subtitle": item.get("subtitle"),
            "relation_only": item.get("relation_only", False),
            "relation_ids": item.get("relation_ids", []),
            "roles": item.get("roles", []),
            "content_digest": item.get("content_digest"),
            "fusion_explanation": item.get("fusion_explanation"),
            "metadata": item.get("metadata", {}),
        }

    @staticmethod
    def _is_verified_fact(item: dict[str, Any]) -> bool:
        """Only explicit reviewed facts may enter the verified-facts partition.

        Source authority and retrieval score describe where a candidate came from and how
        it ranked; neither proves that the underlying claim was reviewed.  Missing fact or
        review state therefore remains supporting/observed evidence.
        """

        fact_status = str(item.get("fact_status") or "").casefold()
        review_status = str(item.get("review_status") or "").casefold()
        derivation = str(item.get("derivation") or "").casefold()
        return (
            not item.get("relation_only")
            and fact_status in {"verified", "accepted"}
            and review_status in {"reviewed", "confirmed", "accepted"}
            and derivation not in {"llm_inferred", "rule_derived", "heuristic"}
            and float(item.get("authority") or 0.0) >= 0.8
            and str(item.get("version_alignment") or "").casefold() not in {"mismatch", "unknown"}
        )

    def _provider(self, project_id: str) -> ResolvedLLMProvider | None:
        if self.providers is not None:
            return self.providers.resolve(project_id)
        if self.settings.llm_base_url and self.settings.llm_api_key and self.settings.llm_model:
            return ResolvedLLMProvider(
                provider="openai_compatible",
                base_url=self.settings.llm_base_url.rstrip("/"),
                model=self.settings.llm_model,
                api_key=self.settings.llm_api_key,
                source="environment",
            )
        return None

    def _llm_configured(self, project_id: str = "project-rag") -> bool:
        return self._provider(project_id) is not None

    @staticmethod
    def _next_actions(
        *,
        source_status: dict[str, dict[str, Any]],
        missing_roles: list[str],
        answer_mode: str,
    ) -> list[str]:
        actions: list[str] = []
        statuses = {str(item.get("status") or "") for item in source_status.values()}
        if "unauthorized" in statuses:
            actions.append("申请所需来源权限后重试")
        if "not_indexed" in statuses:
            actions.append("同步未索引来源后重试")
        if statuses.intersection({"timeout", "unavailable", "partial"}):
            actions.append("检查来源状态并在可用后重试")
        if missing_roles:
            actions.append("补充缺失角色证据：" + "、".join(missing_roles[:3]))
        if answer_mode in {"refusal", "partial"}:
            actions.append("缩小查询范围或补充明确版本与时间")
        elif answer_mode == "retrieval_only":
            actions.append("核对引用证据后再决定是否生成结论")
        else:
            actions.append("继续追问时保留当前交互链")
        return list(dict.fromkeys(actions))[:4]

    @staticmethod
    def _span(name: str, started: float, attributes: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": name,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "attributes": attributes,
        }
