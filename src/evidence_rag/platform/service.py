from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor, wait
from typing import Any
from uuid import uuid4

from ..codex_retrieval import CodexHybridRetriever
from ..models import (
    CodexSearchRequest,
    CodexSearchScope,
    EvidenceSearchRequest,
    SearchScope,
)
from ..query.intent import SOURCE_AUTHORITY
from ..rag.codex_v2 import (
    CODEX_V2_VERSION,
    CodexTemporalRetrieverV2,
    validate_codex_engine_override,
)
from ..rag.document_v2 import (
    DOCUMENT_V2_VERSION,
    DocumentStructuredRetrieverV2,
    validate_document_engine_override,
)
from ..rag.experiment_v2 import EXPERIMENT_V2_VERSION, validate_experiment_engine_override
from ..rag.multisource import build_multisource_context, portable_locator
from ..rag.notebook_v2 import (
    NOTEBOOK_V2_VERSION,
    NotebookStructuredRetrieverV2,
    validate_notebook_engine_override,
)
from ..rag.planner_v2 import (
    build_global_context,
    build_query_plan,
    calibrate_results,
    detect_conflicts_and_staleness,
    root_provenance_key,
)
from ..rag.sources.code import (
    CodePlatformIntegration,
    CodeShadowRunner,
    CodeSourceRetrieverV1Adapter,
)
from ..rag.sources.codex.runtime_v2 import (
    CodexSourceRuntimeUnavailableV2,
    CodexSourceScopeErrorV2,
    normalize_codex_source_scope_v2,
)
from ..rag.workspace_v2 import (
    WORKSPACE_V2_VERSION,
    WorkspaceStructuredRetrieverV2,
    validate_workspace_engine_override,
)
from ..retrieval import HybridRetriever
from .models import (
    ExperimentSourceQueryRequestV2,
    ExperimentSourceQueryResponseV2,
    GlobalSearchRequest,
    NotebookQuerySpecV2,
    NotebookQueryTaskV2,
    NotebookSourceQueryRequestV2,
    NotebookSourceQueryResponseV2,
)
from .store import PlatformStore


class PlatformTypedRequestError(ValueError):
    """Stable typed-source failure that cannot expose provider diagnostics."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class PlatformService:
    _MAX_SOURCE_WORKERS = 4
    _GLOBAL_SEARCH_DEADLINE_SECONDS = 3.0
    _V2_ENGINE_BY_SOURCE = {
        "codex": CODEX_V2_VERSION,
        "experiment": EXPERIMENT_V2_VERSION,
        "notebook": NOTEBOOK_V2_VERSION,
        "document": DOCUMENT_V2_VERSION,
        "workspace": WORKSPACE_V2_VERSION,
    }

    def __init__(
        self,
        store: PlatformStore,
        code: HybridRetriever,
        codex: CodexHybridRetriever,
        event_store: Any | None = None,
        *,
        code_v1_adapter: CodeSourceRetrieverV1Adapter | None = None,
        code_integration: CodePlatformIntegration | None = None,
        code_shadow: CodeShadowRunner | None = None,
        codex_v2: CodexTemporalRetrieverV2 | None = None,
        experiment_v2: Any | None = None,
        notebook_v2: NotebookStructuredRetrieverV2 | None = None,
        document_v2: DocumentStructuredRetrieverV2 | None = None,
        workspace_v2: WorkspaceStructuredRetrieverV2 | None = None,
        source_runtime_v2: Any | None = None,
    ) -> None:
        self.store = store
        self.code = code
        self.codex = codex
        self.event_store = event_store
        resolved_code_v1_adapter = code_v1_adapter or (
            code_shadow.adapter if code_shadow is not None else None
        )
        if resolved_code_v1_adapter is not None and resolved_code_v1_adapter.legacy is not code:
            raise ValueError("Code V1 adapter must wrap the endpoint legacy retriever")
        if code_shadow is not None and code_shadow.adapter is not resolved_code_v1_adapter:
            raise ValueError("Code shadow runner must use the endpoint Code V1 adapter")
        if code_integration is not None and code_integration.legacy is not code:
            raise ValueError("Code integration must use the endpoint legacy retriever")
        self.code_v1_adapter = resolved_code_v1_adapter
        self.code_integration = code_integration
        self.code_shadow = code_shadow
        self.codex_v2 = codex_v2
        self.experiment_v2 = experiment_v2
        self.notebook_v2 = notebook_v2
        self.document_v2 = document_v2
        self.workspace_v2 = workspace_v2
        self.source_runtime_v2 = source_runtime_v2

    def query_experiment_v2(
        self,
        request: ExperimentSourceQueryRequestV2,
        *,
        allowed_acl_refs: list[str],
        enforce_acl: bool,
        engine_requested: str | None = "v2",
    ) -> dict[str, Any]:
        projected = GlobalSearchRequest(
            query=request.query,
            project_id=request.project_id,
            sources=["experiment"],
            limit=request.limit,
            experiment_ids=request.experiment_ids,
            experiment_analysis=request.analysis,
            allowed_acl_refs=allowed_acl_refs,
            enforce_acl=enforce_acl,
        )
        response = self._retrieve_source(
            source="experiment",
            request=projected,
            limit=request.limit,
            engine_requested=engine_requested,
        )
        release_route = dict(response.get("trace", {}).get("release_route") or {})
        if release_route.get("served_engine") != "v2":
            return response
        status = str(response["trace"].get("status") or "")
        if status not in {"complete", "partial"}:
            raise PlatformTypedRequestError(
                str(response["trace"].get("error_code") or "experiment_typed_query_unavailable")
            )
        return ExperimentSourceQueryResponseV2(
            task=request.analysis.task,
            results=list(response["results"]),
            context=dict(response.get("context") or {}),
            analysis=list(response.get("analysis") or []),
            selection_trace=dict(response.get("selection_trace") or {}),
            trace=dict(response["trace"]),
        ).model_dump(mode="json")

    def query_notebook_v2(
        self,
        request: NotebookSourceQueryRequestV2,
        *,
        allowed_acl_refs: list[str],
        enforce_acl: bool,
        engine_requested: str | None = "v2",
    ) -> dict[str, Any]:
        projected = GlobalSearchRequest(
            query=request.query,
            project_id=request.project_id,
            sources=["notebook"],
            limit=request.limit,
            notebook_query=request.spec,
            allowed_acl_refs=allowed_acl_refs,
            enforce_acl=enforce_acl,
        )
        try:
            response = self._retrieve_source(
                source="notebook",
                request=projected,
                limit=request.limit,
                engine_requested=engine_requested,
            )
        except Exception as error:
            safe_codes = {
                "comparison_identity_not_found",
                "comparison_scope_mismatch",
                "comparison_selector_ambiguous",
                "comparison_sides_identical",
                "comparison_spec_required",
                "comparison_task_mismatch",
                "comparison_template_mismatch",
                "comparison_version_mismatch",
                "notebook_cell_identity_ambiguous",
                "notebook_cell_identity_not_found",
                "notebook_execution_version_mismatch",
                "notebook_filter_identity_ambiguous",
                "notebook_filter_identity_not_found",
                "notebook_output_identity_ambiguous",
                "notebook_output_identity_not_found",
                "notebook_parameter_identity_not_found",
                "notebook_revision_version_mismatch",
                "notebook_template_version_mismatch",
            }
            code = str(error)
            raise PlatformTypedRequestError(
                code if code in safe_codes else "notebook_typed_query_unavailable"
            ) from error
        release_route = dict(response.get("trace", {}).get("release_route") or {})
        if release_route.get("served_engine") != "v2":
            return response
        status = str(response["trace"].get("status") or "")
        if status not in {"complete", "partial", "no_match"}:
            raise PlatformTypedRequestError(
                str(response["trace"].get("error_code") or "notebook_typed_query_unavailable")
            )
        return NotebookSourceQueryResponseV2(
            task=request.spec.task,
            results=list(response["results"]),
            context=dict(response.get("context") or {}),
            comparisons=list(response.get("comparisons") or []),
            selection_trace=dict(response.get("selection_trace") or {}),
            trace=dict(response["trace"]),
        ).model_dump(mode="json")

    @staticmethod
    def _validated_notebook_spec_v2(
        spec: NotebookQuerySpecV2 | None,
    ) -> NotebookQuerySpecV2:
        if spec is None:
            return NotebookQuerySpecV2(task=NotebookQueryTaskV2.SEARCH)
        try:
            task = NotebookQueryTaskV2(getattr(spec, "task", None))
        except (TypeError, ValueError) as error:
            raise PlatformTypedRequestError("notebook_task_invalid") from error
        try:
            payload = spec.model_dump(mode="python")
            payload["task"] = task
            return NotebookQuerySpecV2.model_validate(payload)
        except (AttributeError, ValueError) as error:
            raise PlatformTypedRequestError("notebook_typed_spec_invalid") from error

    def search(
        self,
        request: GlobalSearchRequest,
        *,
        record_event: bool = True,
        code_engine_override: str | None = None,
        codex_engine_override: str | None = None,
        experiment_engine_override: str | None = None,
        notebook_engine_override: str | None = None,
        document_engine_override: str | None = None,
        workspace_engine_override: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        deadline_seconds = (
            request.deadline_ms / 1_000
            if request.deadline_ms is not None
            else self._GLOBAL_SEARCH_DEADLINE_SECONDS
        )
        deadline_at = started + deadline_seconds
        sources = request.sources
        per_source = min(30, max(8, request.limit))
        query_plan = build_query_plan(
            query=request.query,
            intent=request.intent,
            requested_sources=list(sources),
            limit=per_source,
            max_hops=request.max_hops,
        )

        def source_limit(source: str) -> int:
            return min(
                30,
                max(4, int(query_plan["source_budgets"].get(source, per_source))),
            )

        results: list[dict[str, Any]] = []
        traces: dict[str, Any] = {}
        source_contexts: dict[str, Any] = {}
        wave_traces: list[dict[str, Any]] = []
        codex_engine = validate_codex_engine_override(codex_engine_override)
        experiment_engine = validate_experiment_engine_override(experiment_engine_override)
        notebook_engine = validate_notebook_engine_override(notebook_engine_override)
        document_engine = validate_document_engine_override(document_engine_override)
        workspace_engine = validate_workspace_engine_override(workspace_engine_override)
        engine_overrides = {
            "code": code_engine_override or "v1",
            "codex": codex_engine if codex_engine_override is not None else None,
            "experiment": (experiment_engine if experiment_engine_override is not None else None),
            "notebook": notebook_engine if notebook_engine_override is not None else None,
            "document": document_engine if document_engine_override is not None else None,
            "workspace": workspace_engine if workspace_engine_override is not None else None,
        }
        first_wave = tuple(query_plan["routed_sources"])
        fallback_wave = tuple(query_plan["fallback_sources"])
        planned_source_count = len(first_wave) + len(fallback_wave)
        executor: ThreadPoolExecutor | None = None
        first_outcomes: dict[str, dict[str, Any]] = {}
        second_outcomes: dict[str, dict[str, Any]] = {}
        if planned_source_count:
            executor = ThreadPoolExecutor(
                max_workers=min(self._MAX_SOURCE_WORKERS, planned_source_count),
                thread_name_prefix="rag-source",
            )
        try:
            first_deadline = (
                min(deadline_at, started + deadline_seconds * 0.7) if fallback_wave else deadline_at
            )
            first_outcomes, first_trace = self._run_source_wave(
                executor=executor,
                sources=first_wave,
                request=request,
                source_limit=source_limit,
                engine_overrides=engine_overrides,
                wave=1,
                wave_deadline=first_deadline,
            )
            wave_traces.append(first_trace)
            first_candidate_count = sum(
                len(outcome["results"]) for outcome in first_outcomes.values()
            )
            coverage_target = min(
                request.limit,
                max(2, min(6, len(first_wave) * 2)),
            )
            first_wave_sufficient = first_candidate_count >= coverage_target
            second_wave_sources = (
                fallback_wave
                if not first_wave_sufficient and time.perf_counter() < deadline_at
                else ()
            )
            if second_wave_sources:
                second_outcomes, second_trace = self._run_source_wave(
                    executor=executor,
                    sources=second_wave_sources,
                    request=request,
                    source_limit=source_limit,
                    engine_overrides=engine_overrides,
                    wave=2,
                    wave_deadline=deadline_at,
                )
                wave_traces.append(second_trace)
        finally:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)

        for source in (*first_wave, *fallback_wave):
            outcome = first_outcomes.get(source) or second_outcomes.get(source)
            if outcome is None:
                continue
            traces[source] = outcome["trace"]
            results.extend(outcome["results"])
            if outcome.get("context") is not None:
                source_contexts[source] = outcome["context"]

        corrective_attempts: list[dict[str, Any]] = []
        for source in fallback_wave:
            outcome = second_outcomes.get(source)
            if outcome is None:
                continue
            corrective_attempts.append(
                {
                    "round": 1,
                    "source": source,
                    "candidate_count": len(outcome["results"]),
                    "status": outcome["trace"]["status"],
                }
            )
        scopes = self.store.entity_scopes([item["entity_id"] for item in results])
        results = [
            item
            for item in results
            if self._in_scope(item, scopes.get(item["entity_id"], {}), request)
        ]
        authority_weights = SOURCE_AUTHORITY.get(request.intent or "", {})
        for item in results:
            entity_factor = self._intent_entity_factor(
                request.intent, str(item.get("entity_type") or "")
            )
            authority = authority_weights.get(item["source"], 0.7) * entity_factor
            alignment = self._version_alignment(item, request.commit)
            item["authority"] = authority
            item["version_alignment"] = alignment
        results = calibrate_results(results, intent=request.intent)
        results = self._deduplicate(results)
        results.sort(key=lambda item: item["score"], reverse=True)
        selected = self._diversify(results, request.limit)
        for item in selected:
            item["locator"] = portable_locator(item.get("locator"), str(item["entity_id"]))
        entity_ids = [item["entity_id"] for item in selected]
        if request.include_lineage and request.max_hops > 0:
            edges, relation_trace = self._expand_edges(
                entity_ids,
                request.max_hops,
                request=request,
                deadline_at=deadline_at,
            )
        else:
            edges = []
            relation_trace = {
                "edge_budget": 500,
                "max_hops_applied": 0,
                "deadline_exhausted": time.perf_counter() >= deadline_at,
                "pruned_acl": 0,
                "pruned_scope": 0,
                "pruned_version": 0,
                "pruned_review": 0,
                "pruned_confidence": 0,
                "pruned_type": 0,
            }
        selected_ids = set(entity_ids)
        domain_priority = {"platform": 3, "workspace": 2, "code": 1, "codex": 1}
        edges.sort(
            key=lambda edge: (
                domain_priority.get(str(edge.get("domain")), 0),
                int(edge["source"] in selected_ids and edge["target"] in selected_ids),
                float(edge.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        edges = edges[: max(80, request.limit * 8)]
        # Preserve confirmed one-hop relations even when the neighboring source did not
        # contain the query words.  Dropping those edges turns a multi-source evidence graph
        # back into unrelated search cards and prevents claim/run or Codex/code verification.
        related_ids = list(
            dict.fromkeys(
                endpoint
                for edge in edges
                if int(edge.get("hop") or 1) == 1
                for endpoint in (edge["source"], edge["target"])
                if endpoint not in selected_ids
            )
        )[: min(20, request.limit)]
        related_nodes = self.store.resolve_nodes(related_ids)
        related_entities = []
        for node in related_nodes:
            if node.get("type") == "Unknown":
                continue
            relation_ids = [
                edge["id"] for edge in edges if node["id"] in {edge["source"], edge["target"]}
            ]
            confidence = max(
                (
                    float(edge.get("confidence") or 0.0)
                    for edge in edges
                    if edge["id"] in relation_ids
                ),
                default=0.0,
            )
            related_entities.append(
                {
                    "entity_id": node["id"],
                    "source": node.get("domain") or "unknown",
                    "entity_type": node.get("type") or "Unknown",
                    "title": node.get("label") or node["id"],
                    "subtitle": "由已确认关系关联",
                    "snippet": "该实体通过已存储的一跳关系与检索证据相连。",
                    "locator": node.get("locator") or node["id"],
                    "version": node.get("version"),
                    "status": "related",
                    "score": round(0.25 + 0.35 * confidence, 6),
                    "channels": ["confirmed_relation"],
                    "authority": authority_weights.get(node.get("domain") or "", 0.7),
                    "version_alignment": "not_applicable",
                    "relation_only": True,
                    "relation_ids": relation_ids,
                }
            )
        for item in related_entities:
            item["locator"] = portable_locator(item.get("locator"), str(item["entity_id"]))
        conflicts = detect_conflicts_and_staleness([*selected, *related_entities])
        global_context = build_global_context(
            plan=query_plan,
            results=selected,
            source_contexts=source_contexts,
            conflicts=conflicts,
        )
        citation_map = {
            f"E{index}": {
                "entity_id": item["entity_id"],
                "source": item["source"],
                "locator": item["locator"],
                "version": item.get("version"),
            }
            for index, item in enumerate(selected, start=1)
        }
        citation_offset = len(citation_map)
        citation_map.update(
            {
                f"E{citation_offset + index}": {
                    "entity_id": item["entity_id"],
                    "source": item["source"],
                    "locator": item["locator"],
                    "version": item.get("version"),
                    "relation_only": True,
                }
                for index, item in enumerate(related_entities, start=1)
            }
        )
        context = build_multisource_context(
            project_id=request.project_id,
            results=selected,
            requested_sources=list(request.sources) or None,
        )

        def source_engine_routing(source: str) -> dict[str, str | None]:
            source_trace = traces.get(source, {})
            release_route = source_trace.get("release_route")
            release_route = release_route if isinstance(release_route, dict) else {}
            served = release_route.get("served_engine") or source_trace.get("engine_used")
            if served not in {"v1", "v2"}:
                served = None
            selected = release_route.get("selected_engine")
            if selected not in {"v1", "v2"}:
                selected = served
            return {
                "requested": engine_overrides[source],
                "selected": selected,
                "served": served,
            }

        source_engine_routes = {
            source: source_engine_routing(source)
            for source in ("codex", "experiment", "notebook", "document", "workspace")
        }
        response = {
            "query_id": f"global-query://{uuid4().hex}",
            "query": request.query,
            "project_id": request.project_id,
            "total": len(selected),
            "results": selected,
            "evidence_pack": {
                "citation_map": citation_map,
                "relations": edges,
                "related_entities": related_entities,
                "context": context.model_dump(mode="json"),
                "source_contexts": source_contexts,
                "query_plan": query_plan,
                "conflicts_and_staleness": conflicts,
                "global_context": global_context,
            },
            "trace": {
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "sources": traces,
                "fusion": "cross-source-diversified-v2",
                "fusion_version": "role-aware-normalized-fusion-v2",
                "selection_policy": "global-relevance-soft-fairness-no-source-quota",
                "score_is_calibrated_probability": False,
                # Compatibility fields report what actually served the response.
                # Requested and selected values remain available additively below.
                "codex_engine": source_engine_routes["codex"]["served"],
                "experiment_engine": source_engine_routes["experiment"]["served"],
                "notebook_engine": source_engine_routes["notebook"]["served"],
                "document_engine": source_engine_routes["document"]["served"],
                "workspace_engine": source_engine_routes["workspace"]["served"],
                "source_engine_routing": source_engine_routes,
                "intent": request.intent,
                "scope_filtered": True,
                "acl_prefiltered": request.enforce_acl,
                "relation_expansion": {
                    "edges": len(edges),
                    "related_entities": len(related_entities),
                    "max_hops": request.max_hops,
                    **relation_trace,
                },
                "planner": {
                    "version": query_plan["schema_version"],
                    "plan_digest": query_plan["content_digest"],
                    "routed_sources": query_plan["routed_sources"],
                    "fallback_sources": query_plan["fallback_sources"],
                    "route_reason": query_plan["route_reason"],
                    "source_waves": wave_traces,
                    "max_corrective_rounds": query_plan["max_corrective_rounds"],
                    "corrective_attempts": corrective_attempts,
                    "coverage": {
                        "first_wave_candidate_count": sum(
                            len(outcome["results"]) for outcome in first_outcomes.values()
                        ),
                        "target_candidate_count": coverage_target,
                        "first_wave_sufficient": first_wave_sufficient,
                        "second_wave_executed": bool(second_outcomes),
                    },
                },
                "status": (
                    "PARTIAL"
                    if any(
                        trace.get("status")
                        in {
                            "partial",
                            "timeout",
                            "unavailable",
                            "unauthorized",
                            "not_indexed",
                            "error",
                        }
                        for trace in traces.values()
                    )
                    else "COMPLETE"
                ),
                "execution": {
                    "mode": (
                        "parallel"
                        if any(wave["execution_mode"] == "parallel" for wave in wave_traces)
                        else "serial"
                    ),
                    "max_workers": min(self._MAX_SOURCE_WORKERS, planned_source_count),
                    "deadline_ms": round(deadline_seconds * 1000),
                    "deadline_exhausted": time.perf_counter() >= deadline_at
                    or any(wave["deadline_exhausted"] for wave in wave_traces),
                },
                "release": {
                    "decision": "HOLD_DEFAULT_V1",
                    "default_engines": {
                        "code": "v1",
                        "codex": "v1",
                        "experiment": "v1",
                        "notebook": "v1",
                        "document": "v1",
                        "workspace": "v1",
                    },
                    "quality_qualified": False,
                    "rollback": "omit all X-RAG-*-Engine overrides",
                },
            },
            "index_generations": sorted(
                {
                    generation
                    for trace in traces.values()
                    for generation in trace.get("index_generation", [])
                }
            ),
        }
        if record_event and self.event_store:
            self.event_store.record_query(
                request.project_id,
                request.query,
                len({item["source"] for item in selected}),
                len(selected),
                response["trace"]["duration_ms"],
            )
        return response

    def _run_source_wave(
        self,
        *,
        executor: Executor | None,
        sources: tuple[str, ...],
        request: GlobalSearchRequest,
        source_limit: Any,
        engine_overrides: dict[str, str | None],
        wave: int,
        wave_deadline: float,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        wave_started = time.perf_counter()
        execution_mode = "parallel" if len(sources) > 1 else "serial"
        if not sources or executor is None:
            return {}, {
                "wave": wave,
                "sources": list(sources),
                "execution_mode": execution_mode,
                "duration_ms": 0.0,
                "deadline_exhausted": time.perf_counter() >= wave_deadline,
            }
        futures: dict[str, Future[dict[str, Any]]] = {
            source: executor.submit(
                self._retrieve_source,
                source=source,
                request=request,
                limit=source_limit(source),
                engine_requested=engine_overrides[source],
            )
            for source in sources
        }
        remaining = max(0.0, wave_deadline - time.perf_counter())
        done, _ = wait(tuple(futures.values()), timeout=remaining)
        outcomes: dict[str, dict[str, Any]] = {}
        for source in sources:
            future = futures[source]
            if future not in done:
                future.cancel()
                outcomes[source] = self._failed_source_outcome(
                    source=source,
                    status="timeout",
                    error_code="source_deadline_exceeded",
                    engine_requested=engine_overrides[source],
                    duration_ms=(time.perf_counter() - wave_started) * 1000,
                    wave=wave,
                )
                continue
            try:
                outcome = future.result()
            except Exception as error:
                status, error_code = self._classify_source_error(error)
                outcome = self._failed_source_outcome(
                    source=source,
                    status=status,
                    error_code=error_code,
                    engine_requested=engine_overrides[source],
                    duration_ms=(time.perf_counter() - wave_started) * 1000,
                    wave=wave,
                )
            else:
                outcome["trace"]["wave"] = wave
            outcomes[source] = outcome
        return outcomes, {
            "wave": wave,
            "sources": list(sources),
            "execution_mode": execution_mode,
            "duration_ms": round((time.perf_counter() - wave_started) * 1000, 2),
            "deadline_exhausted": any(
                outcome["trace"]["status"] == "timeout" for outcome in outcomes.values()
            ),
            "source_status": {source: outcomes[source]["trace"]["status"] for source in sources},
        }

    def _retrieve_source(
        self,
        *,
        source: str,
        request: GlobalSearchRequest,
        limit: int,
        engine_requested: str | None,
        codex_scope: CodexSearchScope | None = None,
        governed_global_authority: bool = False,
        legacy_fallback: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Apply evaluator-authorized routing and fall back to V1 exactly once."""

        registry = self.source_runtime_v2
        global_v2_probe = governed_global_authority and engine_requested == "v2"
        if source == "code":
            return self._retrieve_source_engine(
                source=source,
                request=request,
                limit=limit,
                engine_requested=engine_requested or "v1",
                codex_scope=codex_scope,
            )

        def legacy() -> dict[str, Any]:
            if legacy_fallback is not None:
                return legacy_fallback()
            return self._retrieve_source_engine(
                source=source,
                request=request,
                limit=limit,
                engine_requested="v1",
                codex_scope=codex_scope,
            )

        if registry is None or not callable(getattr(registry, "route", None)):
            if global_v2_probe:
                return {
                    "source": source,
                    "results": [],
                    "context": None,
                    "analysis": [],
                    "comparisons": [],
                    "selection_trace": {},
                    "trace": {
                        "status": "unavailable",
                        "engine_used": None,
                        "index_generation": [],
                        "error_code": "verified_authority_unavailable",
                        "release_route": {
                            "stage": "off",
                            "requested_engine": "v2",
                            "selected_engine": "v1",
                            "response_engine": "v1",
                            "served_engine": "none",
                            "execute_v1": False,
                            "execute_v2": False,
                            "shadow": False,
                            "reason_code": "verified_authority_unavailable",
                            "fallback_reason": "authority_unavailable",
                            "shadow_outcome": None,
                            "authority_sha256": None,
                            "lkg_status": "UNAVAILABLE",
                            "lkg_sha256": None,
                        },
                    },
                }
            result = legacy()
            trace = dict(result.get("trace") or {})
            trace["release_route"] = {
                "stage": "off",
                "requested_engine": engine_requested,
                "selected_engine": "v1",
                "response_engine": "v1",
                "served_engine": "v1",
                "execute_v1": True,
                "execute_v2": False,
                "shadow": False,
                "reason_code": "verified_authority_unavailable",
                "fallback_reason": ("authority_unavailable" if engine_requested == "v2" else None),
                "shadow_outcome": None,
                "authority_sha256": None,
                "lkg_status": "UNAVAILABLE",
                "lkg_sha256": None,
            }
            result["trace"] = trace
            return result
        # A caller boolean can only suppress duplicate legacy work while the
        # outer global request falls back. It can never authorize V2.
        request_key = "source-request-" + _canonical_sha256(
            {
                "source": source,
                "query": request.query,
                "intent": request.intent,
                "project_id": request.project_id,
                "repository_ids": request.repository_ids,
                "thread_ids": request.thread_ids,
                "experiment_ids": request.experiment_ids,
                "document_ids": request.document_ids,
                "commit": request.commit,
                "as_of": request.as_of,
            }
        ).removeprefix("sha256:")
        route = registry.route(
            source=source,
            project_id=request.project_id,
            request_id=request_key,
            requested_engine=engine_requested,
        )

        def release_trace(
            *,
            served_engine: str,
            fallback_reason: str | None,
            shadow_outcome: str | None,
        ) -> dict[str, Any]:
            lkg = registry.last_known_good(project_id=request.project_id, source=source)
            return {
                "stage": route.stage.value,
                "requested_engine": route.requested_engine,
                "selected_engine": route.selected_engine,
                "response_engine": route.response_engine,
                "served_engine": served_engine,
                "execute_v1": route.execute_v1,
                "execute_v2": route.execute_v2,
                "shadow": route.shadow,
                "canary_bucket": route.canary_bucket,
                "reason_code": route.reason_code,
                "fallback_reason": fallback_reason,
                "shadow_outcome": shadow_outcome,
                "authority_sha256": route.authority_sha256,
                "decision_sha256": route.content_sha256,
                "lkg_status": "VERIFIED" if lkg is not None else "UNAVAILABLE",
                "lkg_sha256": lkg.content_sha256 if lkg is not None else None,
                "router_version": route.router_version,
            }

        if not route.execute_v2:
            if global_v2_probe:
                return {
                    "source": source,
                    "results": [],
                    "context": None,
                    "analysis": [],
                    "comparisons": [],
                    "selection_trace": {},
                    "trace": {
                        "status": "unavailable",
                        "engine_used": None,
                        "index_generation": [],
                        "error_code": "verified_authority_unavailable",
                        "release_route": release_trace(
                            served_engine="none",
                            fallback_reason="authority_unavailable",
                            shadow_outcome=None,
                        ),
                    },
                }
            result = legacy()
            trace = dict(result.get("trace") or {})
            trace["release_route"] = release_trace(
                served_engine="v1",
                fallback_reason=(
                    "authority_unavailable"
                    if engine_requested == "v2" and route.authority_sha256 is None
                    else None
                ),
                shadow_outcome=None,
            )
            result["trace"] = trace
            return result

        legacy_result = legacy() if route.shadow else None
        fallback_reason: str | None = None
        try:
            v2_result = self._retrieve_source_engine(
                source=source,
                request=request,
                limit=limit,
                engine_requested="v2",
                codex_scope=codex_scope,
            )
            v2_status = str(v2_result.get("trace", {}).get("status") or "")
            if v2_status in {
                "error",
                "not_indexed",
                "timeout",
                "unauthorized",
                "unavailable",
            }:
                fallback_reason = {
                    "error": "internal_v2_error",
                    "not_indexed": "source_not_indexed",
                    "timeout": "source_timeout",
                    "unauthorized": "source_unauthorized",
                    "unavailable": "source_unavailable",
                }[v2_status]
                raise RuntimeError("routed V2 unavailable")
            generations = tuple(v2_result.get("trace", {}).get("index_generation") or ())
            if not generations:
                fallback_reason = "generation_unavailable"
                raise RuntimeError("routed V2 generation unavailable")
            if route.authority_sha256 is None:
                fallback_reason = "authority_unavailable"
                raise RuntimeError("routed V2 authority unavailable")
            registry.record_v2_success(
                project_id=request.project_id,
                source=source,
                authority_sha256=route.authority_sha256,
                generation=_canonical_sha256(tuple(sorted(map(str, generations)))),
                candidate_count=len(v2_result.get("results") or ()),
            )
        except Exception as error:
            if fallback_reason is None:
                status, code = self._classify_source_error(error)
                fallback_reason = (
                    code
                    if status in {"timeout", "unauthorized", "not_indexed", "unavailable"}
                    else "internal_v2_error"
                )
            result = legacy_result if legacy_result is not None else legacy()
            trace = dict(result.get("trace") or {})
            trace["release_route"] = release_trace(
                served_engine="v1",
                fallback_reason=fallback_reason,
                shadow_outcome="v2_failed" if route.shadow else None,
            )
            result["trace"] = trace
            return result

        if route.shadow:
            if legacy_result is None:
                raise RuntimeError("shadow route is missing its V1 response")
            trace = dict(legacy_result.get("trace") or {})
            trace["release_route"] = release_trace(
                served_engine="v1",
                fallback_reason=None,
                shadow_outcome="v2_completed",
            )
            legacy_result["trace"] = trace
            return legacy_result
        trace = dict(v2_result.get("trace") or {})
        trace["release_route"] = release_trace(
            served_engine="v2",
            fallback_reason=None,
            shadow_outcome=None,
        )
        v2_result["trace"] = trace
        return v2_result

    def _retrieve_source_engine(
        self,
        *,
        source: str,
        request: GlobalSearchRequest,
        limit: int,
        engine_requested: str,
        codex_scope: CodexSearchScope | None = None,
    ) -> dict[str, Any]:
        source_started = time.perf_counter()
        context: Any | None = None
        engine_used = "v1"
        if source == "code":
            response = self._search_code(
                EvidenceSearchRequest(
                    query=request.query,
                    scope=SearchScope(
                        project_id=request.project_id,
                        repository_ids=request.repository_ids,
                        commit=request.commit,
                        allowed_acl_refs=request.allowed_acl_refs,
                        enforce_acl=request.enforce_acl,
                    ),
                    limit=limit,
                    include_edges=False,
                ),
                engine_override=None if engine_requested == "v1" else engine_requested,
            )
            source_results = [self._code(item) for item in response["results"]]
            engine_used = str(response.get("trace", {}).get("engine_selected") or engine_requested)
        elif source == "codex" and engine_requested == "v2" and self.source_runtime_v2 is not None:
            response = self._search_codex_source_v2(
                request,
                limit=limit,
                codex_scope=codex_scope,
            )
            source_results = list(response["results"])
            context = response.get("context")
            engine_used = "v2"
        elif source == "codex":
            codex_request = CodexSearchRequest(
                query=request.query,
                scope=CodexSearchScope(
                    project_id=request.project_id,
                    thread_ids=request.thread_ids,
                    date_from=request.date_from,
                    date_to=request.date_to,
                    allowed_acl_refs=request.allowed_acl_refs,
                    enforce_acl=request.enforce_acl,
                ),
                limit=limit,
                include_edges=False,
            )
            use_v2 = engine_requested == "v2" and self.codex_v2 is not None
            response = (
                self.codex_v2.search(codex_request) if use_v2 else self.codex.search(codex_request)
            )
            source_results = [self._codex(item) for item in response["results"]]
            context = response.get("context")
            engine_used = "v2" if use_v2 else "v1"
        elif (
            source == "experiment"
            and engine_requested == "v2"
            and self.source_runtime_v2 is not None
        ):
            response = self._search_experiment_source_v2(request, limit=limit)
            if (
                request.experiment_analysis is None
                and response.get("trace", {}).get("source_status") == "not_indexed"
                and self.experiment_v2 is not None
            ):
                # Manual V1 experiment records predate source publication.  They
                # retain the established typed-V2 projection, while any ready
                # governed source must use the complete production facade.
                response = self.experiment_v2.search(
                    project_id=request.project_id,
                    query=request.query,
                    experiment_ids=request.experiment_ids,
                    allowed_acl_refs=request.allowed_acl_refs,
                    enforce_acl=request.enforce_acl,
                    limit=limit,
                )
            source_results = list(response["results"])
            context = response.get("context")
            engine_used = "v2"
        elif source == "experiment" and engine_requested == "v2" and self.experiment_v2:
            if request.experiment_analysis is not None:
                raise PlatformTypedRequestError("experiment_source_runtime_required")
            response = self.experiment_v2.search(
                project_id=request.project_id,
                query=request.query,
                experiment_ids=request.experiment_ids,
                allowed_acl_refs=request.allowed_acl_refs,
                enforce_acl=request.enforce_acl,
                limit=limit,
            )
            source_results = list(response["results"])
            context = response.get("context")
            engine_used = "v2"
        elif (
            source == "notebook" and engine_requested == "v2" and self.source_runtime_v2 is not None
        ):
            notebook_spec = self._validated_notebook_spec_v2(request.notebook_query)
            with self.source_runtime_v2.operation("notebook") as facade:
                response = facade.search(
                    project_id=request.project_id,
                    query=request.query,
                    allowed_acl_refs=request.allowed_acl_refs,
                    enforce_acl=request.enforce_acl,
                    limit=limit,
                    filters=notebook_spec.filters.model_dump(mode="python"),
                    comparison=(
                        notebook_spec.comparison.model_dump(mode="python")
                        if notebook_spec.comparison is not None
                        else None
                    ),
                    task=notebook_spec.task,
                )
            source_results = [
                self._project_notebook_source_v2(item) for item in response["results"]
            ]
            context = response.get("context")
            engine_used = "v2"
        elif source == "notebook" and engine_requested == "v2" and self.notebook_v2:
            if request.notebook_query is not None:
                raise PlatformTypedRequestError("notebook_source_runtime_required")
            response = self.notebook_v2.search(
                project_id=request.project_id,
                query=request.query,
                allowed_acl_refs=request.allowed_acl_refs,
                enforce_acl=request.enforce_acl,
                limit=limit,
            )
            source_results = list(response["results"])
            context = response.get("context")
            engine_used = "v2"
        elif (
            source == "document" and engine_requested == "v2" and self.source_runtime_v2 is not None
        ):
            with self.source_runtime_v2.operation("document") as facade:
                response = facade.search(
                    project_id=request.project_id,
                    query=request.query,
                    document_ids=request.document_ids,
                    allowed_acl_refs=request.allowed_acl_refs,
                    enforce_acl=request.enforce_acl,
                    limit=limit,
                )
            source_results = [
                self._project_document_source_v2(item) for item in response["results"]
            ]
            context = self._project_document_context_v2(
                response.get("context"),
                source_results,
            )
            engine_used = "v2"
        elif source == "document" and engine_requested == "v2" and self.document_v2:
            response = self.document_v2.search(
                project_id=request.project_id,
                query=request.query,
                document_ids=request.document_ids,
                allowed_acl_refs=request.allowed_acl_refs,
                enforce_acl=request.enforce_acl,
                limit=limit,
            )
            source_results = list(response["results"])
            context = response.get("context")
            engine_used = "v2"
        elif (
            source == "workspace"
            and engine_requested == "v2"
            and self.source_runtime_v2 is not None
        ):
            with self.source_runtime_v2.operation("workspace") as facade:
                response = facade.search(
                    project_id=request.project_id,
                    query=request.query,
                    as_of=request.as_of,
                    date_from=request.date_from,
                    date_to=request.date_to,
                    allowed_acl_refs=request.allowed_acl_refs,
                    enforce_acl=request.enforce_acl,
                    limit=limit,
                )
            source_results = [
                self._project_workspace_source_v2(item) for item in response["results"]
            ]
            context = self._project_workspace_context_v2(
                response.get("context"),
                as_of=request.as_of,
                confirmed_relations=response.get("confirmed_relations"),
            )
            engine_used = "v2"
        elif source == "workspace" and engine_requested == "v2" and self.workspace_v2:
            response = self.workspace_v2.search(
                project_id=request.project_id,
                query=request.query,
                as_of=request.as_of,
                date_from=request.date_from,
                date_to=request.date_to,
                allowed_acl_refs=request.allowed_acl_refs,
                enforce_acl=request.enforce_acl,
                limit=limit,
            )
            source_results = list(response["results"])
            context = response.get("context")
            engine_used = "v2"
        else:
            source_results = self.store.structured_search(
                request.project_id,
                request.query,
                [source],
                limit,
                allowed_acl_refs=request.allowed_acl_refs,
                enforce_acl=request.enforce_acl,
            )
            response = {
                "results": source_results,
                "trace": {},
                "index_generation": [],
            }
        raw_trace = response.get("trace")
        raw_trace = dict(raw_trace) if isinstance(raw_trace, dict) else {}
        if engine_used == "v2":
            raw_trace.setdefault("engine", self._V2_ENGINE_BY_SOURCE.get(source, "v2"))
            raw_trace.setdefault(
                "release",
                {
                    "decision": "HOLD_DEFAULT_V1",
                    "default_engine": "v1",
                    "quality_qualified": False,
                    "rollback": f"omit X-RAG-{source.title()}-Engine",
                },
            )
        raw_status = str(raw_trace.get("source_status") or raw_trace.get("status") or "")
        status = self._normalize_source_status(raw_status, len(source_results))
        generations = response.get("index_generation", raw_trace.get("index_generation", []))
        if isinstance(generations, str):
            generations = [generations]
        if source == "experiment" and engine_used == "v2" and not generations:
            # Manual Experiment records predate source publication but the
            # established typed-V2 projection binds every run to an immutable
            # snapshot digest. Preserve that exact generation for release/LKG
            # verification instead of treating a valid typed response as
            # generation-less and falling back to V1.
            structured_context = response.get("context")
            context_runs = (
                structured_context.get("runs", []) if isinstance(structured_context, dict) else []
            )
            generations = sorted(
                {
                    str(item["snapshot_digest"])
                    for item in context_runs
                    if isinstance(item, dict)
                    and isinstance(item.get("snapshot_digest"), str)
                    and str(item["snapshot_digest"]).startswith("sha256:")
                }
            )
        adapter_duration = raw_trace.get("duration_ms")
        trace = {
            **raw_trace,
            "duration_ms": round((time.perf_counter() - source_started) * 1000, 2),
            "adapter_duration_ms": adapter_duration,
            "status": status,
            "candidate_count": len(source_results),
            "engine_requested": engine_requested,
            "engine_used": engine_used,
            "index_generation": list(generations or []),
            "error_code": raw_trace.get("error_code"),
        }
        return {
            "source": source,
            "results": source_results,
            "context": context,
            "analysis": response.get("analysis", []),
            "comparisons": response.get("comparisons", []),
            "selection_trace": response.get("selection_trace", {}),
            "trace": trace,
        }

    def _search_codex_source_v2(
        self,
        request: GlobalSearchRequest,
        *,
        limit: int,
        codex_scope: CodexSearchScope | None = None,
    ) -> dict[str, Any]:
        with self.source_runtime_v2.operation("codex") as facade:
            return self._search_codex_source_v2_with_facade(
                facade,
                request,
                limit=limit,
                codex_scope=codex_scope,
            )

    def _search_codex_source_v2_with_facade(
        self,
        facade: Any,
        request: GlobalSearchRequest,
        *,
        limit: int,
        codex_scope: CodexSearchScope | None = None,
    ) -> dict[str, Any]:
        scope = codex_scope or CodexSearchScope(
            project_id=request.project_id,
            thread_ids=request.thread_ids,
            date_from=request.date_from,
            date_to=request.date_to,
            allowed_acl_refs=request.allowed_acl_refs,
            enforce_acl=request.enforce_acl,
        )
        projected_project = scope.project_id or request.project_id
        if (
            projected_project != request.project_id
            or tuple(dict.fromkeys(scope.thread_ids)) != tuple(dict.fromkeys(request.thread_ids))
            or scope.date_from != request.date_from
            or scope.date_to != request.date_to
            or set(scope.allowed_acl_refs) != set(request.allowed_acl_refs)
            or scope.enforce_acl is not request.enforce_acl
        ):
            raise CodexSourceScopeErrorV2("scope_projection_mismatch")
        normalized_scope = normalize_codex_source_scope_v2(
            item_types=scope.item_types,
            statuses=scope.statuses,
            date_from=scope.date_from,
            date_to=scope.date_to,
        )
        listed: list[dict[str, Any]] = []
        page_size = 256
        offset = 0
        while True:
            page = facade.store.list_codex_threads(
                project_id=request.project_id,
                limit=page_size,
                offset=offset,
            )
            listed.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        requested = tuple(dict.fromkeys(scope.thread_ids))
        if requested:
            selected = [
                item
                for identity in requested
                for item in listed
                if identity in {str(item.get("id") or ""), str(item.get("thread_id") or "")}
            ]
            if len(selected) != len(requested):
                raise ValueError("codex_requested_thread_unavailable")
        else:
            allowed = {"public", *request.allowed_acl_refs}
            selected = [
                item
                for item in listed
                if not request.enforce_acl or str(item.get("acl_ref") or "") in allowed
            ]
        if not selected:
            return {
                "results": [],
                "context": {
                    "availability": "UNAVAILABLE",
                    "blocks": [],
                    "reasoning_included": False,
                },
                "trace": {
                    "source_status": "not_indexed",
                    "index_generation": [],
                    "runtime_version": "codex-source-runtime-v2",
                },
                "index_generation": [],
            }

        task = self._codex_task(request.query, request.intent)
        allowed_acl_refs = set(request.allowed_acl_refs)
        if not request.enforce_acl:
            allowed_acl_refs.update(str(item.get("acl_ref") or "") for item in selected)
            allowed_acl_refs.update(self._project_acl_refs(request.project_id))
            allowed_acl_refs.discard("")
        results: list[dict[str, Any]] = []
        contexts: list[dict[str, Any]] = []
        generations: list[str] = []
        watermarks: list[str] = []
        scoped_item_count = 0
        for thread in selected:
            try:
                result = facade.query_v2(
                    project_id=request.project_id,
                    allowed_acl_refs=tuple(sorted(allowed_acl_refs)),
                    thread_id=str(thread.get("thread_id") or thread.get("id") or ""),
                    query=request.query,
                    task=task,
                    final_k=min(24, max(4, limit)),
                    item_types=normalized_scope.item_types,
                    statuses=normalized_scope.statuses,
                    date_from=scope.date_from,
                    date_to=scope.date_to,
                )
            except CodexSourceRuntimeUnavailableV2 as exc:
                if exc.reason_code == "source_scope_no_match":
                    continue
                raise
            turn_status = {item.id: item.status for item in result.scoped_turns}
            authoritative_items: dict[str, Any] = {}
            for item in result.scoped_items:
                authoritative_items[item.id] = item
                authoritative_items[f"{item.item_type}:{item.item_id}"] = item
                if (
                    item.item_type == "CommandExecution"
                    and item.metadata.get("source_event_type") == "command_execution"
                    and type(item.metadata.get("exit_code")) is int
                ):
                    authoritative_items[f"CommandResult:{item.item_id}"] = item
            scoped_item_count += len(result.scoped_items)
            candidate_by_unit = {
                item.unit_id: item for item in result.pipeline.retrieval.candidates
            }
            context = result.pipeline.context
            contexts.append(context.model_dump(mode="json"))
            generations.append(result.authority.generation_id)
            watermarks.append(result.authority.watermark)
            for block in context.blocks:
                if any(item_id not in authoritative_items for item_id in block.source_item_ids):
                    raise CodexSourceScopeErrorV2("source_projection_authority_mismatch")
                source_item = authoritative_items[block.source_item_ids[0]]
                authoritative_status = source_item.status
                if authoritative_status is None:
                    authoritative_status = turn_status.get(source_item.turn_id)
                candidate = candidate_by_unit.get(block.unit_id)
                results.append(
                    {
                        "entity_id": block.source_item_ids[0],
                        "source": "codex",
                        "entity_type": str(block.role),
                        "title": str(block.role).replace("_", " ").title(),
                        "subtitle": block.state,
                        "snippet": block.text,
                        "locator": block.source_locators[0],
                        "version": block.content_sha256,
                        "status": authoritative_status,
                        "score": block.relevance_probability,
                        "channels": (
                            [str(item) for item in candidate.channels] if candidate else []
                        ),
                        "roles": [str(block.role)],
                        "content_digest": block.content_sha256,
                        "redactions": [],
                        "thread_id": result.authority.raw_thread_id,
                        "episode_id": block.episode_id,
                        "turn_id": None,
                        "evidence_role": str(block.role),
                        "event_status": block.state,
                        "negative_reasons": [],
                        "root_provenance": block.episode_id or block.unit_id,
                        "raw_or_derived": "derived_fact",
                        "fact_status": "observed",
                        "review_status": "unreviewed",
                        "metadata": {
                            "v2_unit_id": block.unit_id,
                            "source_item_ids": list(block.source_item_ids),
                            "candidate_trace_sha256": block.candidate_trace_sha256,
                            "warnings": [str(item) for item in block.warnings],
                            "runtime_version": result.runtime_version,
                            "authoritative_item_type": source_item.item_type,
                            "authoritative_status": authoritative_status,
                            "authoritative_timestamp": source_item.timestamp,
                        },
                    }
                )
        results.sort(key=lambda item: (-float(item["score"]), str(item["entity_id"])))
        results = results[:limit]
        return {
            "results": results,
            "context": {
                "availability": "AVAILABLE" if results else "UNAVAILABLE",
                "threads": contexts,
                "reasoning_included": False,
            },
            "trace": {
                "source_status": "complete" if results else "no_match",
                "index_generation": sorted(set(generations)),
                "watermarks": sorted(set(watermarks)),
                "runtime_version": "codex-source-runtime-v2",
                "thread_count": len(selected),
                "matched_thread_count": len(contexts),
                "scoped_item_count": scoped_item_count,
                "scope_filters": {
                    "item_types": list(normalized_scope.item_types),
                    "statuses": list(normalized_scope.statuses),
                    "date_from": scope.date_from,
                    "date_to": scope.date_to,
                },
            },
            "index_generation": sorted(set(generations)),
        }

    def _search_experiment_source_v2(
        self,
        request: GlobalSearchRequest,
        *,
        limit: int,
    ) -> dict[str, Any]:
        with self.source_runtime_v2.operation("experiment") as facade:
            return self._search_experiment_source_v2_with_facade(
                facade,
                request,
                limit=limit,
            )

    def _search_experiment_source_v2_with_facade(
        self,
        facade: Any,
        request: GlobalSearchRequest,
        *,
        limit: int,
    ) -> dict[str, Any]:
        sources = [
            item
            for item in facade.service.store.list_sources(request.project_id)
            if str(item.get("status") or "") == "ready"
        ]
        if not sources:
            return {
                "results": [],
                "context": {
                    "availability": "UNAVAILABLE",
                    "blocks": [],
                    "reasoning_included": False,
                },
                "trace": {
                    "source_status": "not_indexed",
                    "index_generation": [],
                    "runtime_version": "experiment-source-runtime-v2",
                },
                "index_generation": [],
            }
        if len(request.experiment_ids) > 50:
            raise ValueError("experiment_requested_scope_too_large")

        typed_analysis = request.experiment_analysis
        if typed_analysis is not None:
            # Local import avoids the Experiment package's baseline audit module
            # importing PlatformService while this module is still initializing.
            from ..rag.sources.experiment.runtime_v2 import ExperimentAnalysisSpecV2

        requested_groups: tuple[tuple[str, ...], ...] = (
            (tuple(dict.fromkeys(request.experiment_ids)),)
            if typed_analysis is not None and request.experiment_ids
            else tuple((identity,) for identity in dict.fromkeys(request.experiment_ids))
            if request.experiment_ids
            else ((),)
        )
        analysis_spec = (
            ExperimentAnalysisSpecV2(
                metric_name=typed_analysis.metric,
                baseline_run_id=typed_analysis.baseline_run_id,
                candidate_run_ids=tuple(typed_analysis.candidate_run_ids),
                run_ids=tuple(typed_analysis.run_ids),
                aggregation=typed_analysis.aggregation,
                split=typed_analysis.split,
                reproduction_roles=tuple(typed_analysis.roles),
                treatment_config_keys=frozenset(typed_analysis.treatment_config_keys),
                controlled_config_keys=frozenset(typed_analysis.controlled_config_keys),
                fail_closed=True,
            )
            if typed_analysis is not None
            else None
        )
        completed: list[Any] = []
        error_statuses: list[str] = []
        error_codes: list[str] = []
        allowed_acl_refs = set(request.allowed_acl_refs)
        if not request.enforce_acl:
            allowed_acl_refs.update(self._project_acl_refs(request.project_id))
            if facade.service.sources is not None:
                allowed_acl_refs.update(
                    str(item.get("acl_ref") or "")
                    for item in facade.service.sources.store.list_events(
                        request.project_id,
                        100_000,
                    )
                )
            allowed_acl_refs.discard("")
        for source in sources:
            for experiment_ids in requested_groups:
                try:
                    completed.append(
                        facade.query_v2(
                            project_id=request.project_id,
                            allowed_acl_refs=tuple(sorted(allowed_acl_refs)),
                            source_id=str(source["id"]),
                            query_request={
                                "task": (
                                    typed_analysis.task if typed_analysis is not None else "search"
                                ),
                                "experiment_ids": list(experiment_ids),
                                "run_ids": (
                                    typed_analysis.selected_run_ids()
                                    if typed_analysis is not None
                                    else []
                                ),
                                "limit": min(200, max(1, limit)),
                            },
                            semantic_query=request.query,
                            analysis=analysis_spec,
                            budget_tokens=min(8_000, max(512, limit * 256)),
                        )
                    )
                except Exception as error:
                    status, code = self._classify_experiment_source_failure(error)
                    error_statuses.append(status)
                    error_codes.append(code)
        if not completed:
            status = "unavailable"
            if "error" in error_statuses:
                status = "error"
            elif "timeout" in error_statuses:
                status = "timeout"
            elif error_statuses and set(error_statuses) == {"unauthorized"}:
                status = "unauthorized"
            return {
                "results": [],
                "context": {
                    "availability": "UNAVAILABLE",
                    "blocks": [],
                    "reasoning_included": False,
                },
                "trace": {
                    "source_status": status,
                    "error_code": (
                        error_codes[0]
                        if len(set(error_codes)) == 1
                        else "experiment_sources_failed"
                    ),
                    "source_error_codes": sorted(set(error_codes)),
                    "index_generation": [],
                    "runtime_version": "experiment-source-runtime-v2",
                    "source_count": 0,
                    "source_attempt_count": len(sources) * len(requested_groups),
                    "partial_source_failures": len(error_codes),
                },
                "index_generation": [],
            }

        rows: list[dict[str, Any]] = []
        contexts: list[dict[str, Any]] = []
        analyses: list[dict[str, Any]] = []
        selected_formal_run_ids: set[str] = set()
        generations: list[str] = []
        watermarks: list[str] = []
        for result in completed:
            identities = {item.pipeline_run_id: item for item in result.identities}
            snapshots = facade.v2_store.active_snapshots(
                project_id=result.authority.project_id,
                source_id=result.authority.pipeline_source_id,
                acl_ref=result.authority.acl_ref,
            )
            snapshot_by_id = {item.run_snapshot_id: item for item in snapshots}
            candidate_by_snapshot = {
                item.run_snapshot_id: item for item in result.pipeline.semantic.candidates
            }
            context_payload = result.context.model_dump(mode="json")
            context_payload.pop("acl_ref", None)
            contexts.append(context_payload)
            if typed_analysis is not None:
                analyses.append(
                    {
                        "source_id": result.authority.formal_source_id,
                        "numeric": [
                            item.model_dump(mode="json") for item in result.analysis.numeric
                        ],
                        "comparability": [
                            item.model_dump(mode="json") for item in result.analysis.comparability
                        ],
                        "comparisons": [
                            item.model_dump(mode="json") for item in result.analysis.comparisons
                        ],
                        "aggregations": [
                            item.model_dump(mode="json") for item in result.analysis.aggregations
                        ],
                        "reproduction": [
                            item.model_dump(mode="json") for item in result.analysis.reproduction
                        ],
                        "requested_reproduction_roles": list(
                            result.analysis.requested_reproduction_roles
                        ),
                        "warnings": list(result.analysis.warnings),
                    }
                )
            generations.append(result.authority.source_generation)
            watermarks.append(result.authority.watermark)
            for block in result.context.blocks:
                snapshot = snapshot_by_id.get(block.run_snapshot_id)
                if snapshot is None:
                    continue
                identity = identities.get(snapshot.run_id)
                if identity is None:
                    continue
                selected_formal_run_ids.add(identity.formal_run_id)
                candidate = candidate_by_snapshot.get(block.run_snapshot_id)
                rows.append(
                    {
                        "entity_id": identity.formal_run_id,
                        "source": "experiment",
                        "entity_type": "ExperimentRun",
                        "title": block.role.replace("_", " ").title(),
                        "subtitle": snapshot.status,
                        "snippet": block.body,
                        "locator": (
                            candidate.source_locator
                            if candidate is not None
                            else block.citations[0]
                        ),
                        "version": snapshot.content_sha256,
                        "status": snapshot.status,
                        "score": (
                            max(0.0, min(1.0, candidate.rerank_score))
                            if candidate is not None
                            else 0.0
                        ),
                        "channels": [candidate.channel] if candidate is not None else [],
                        "roles": [block.role],
                        "content_digest": block.content_sha256,
                        "redactions": [],
                        "experiment_id": identity.formal_experiment_id,
                        "root_provenance": identity.formal_run_id,
                        "raw_or_derived": "derived_fact",
                        "fact_status": "observed",
                        "review_status": "unreviewed",
                        "metadata": {
                            "experiment_id": identity.formal_experiment_id,
                            "formal_run_id": identity.formal_run_id,
                            "v2_run_snapshot_id": block.run_snapshot_id,
                            "role": block.role,
                            "runtime_version": result.runtime_version,
                        },
                    }
                )
        rows.sort(key=lambda item: (-float(item["score"]), str(item["entity_id"])))
        rows = rows[:limit]
        selection_trace = {
            "schema_version": "experiment-analysis-selection-trace-v2",
            "explicit": typed_analysis is not None,
            "task": typed_analysis.task if typed_analysis is not None else "search",
            "metric": typed_analysis.metric if typed_analysis is not None else None,
            "baseline_run_id": (
                typed_analysis.baseline_run_id if typed_analysis is not None else None
            ),
            "candidate_run_ids": (
                list(typed_analysis.candidate_run_ids) if typed_analysis is not None else []
            ),
            "run_ids": list(typed_analysis.run_ids) if typed_analysis is not None else [],
            "split": typed_analysis.split if typed_analysis is not None else None,
            "aggregation": (typed_analysis.aggregation if typed_analysis is not None else None),
            "roles": list(typed_analysis.roles) if typed_analysis is not None else [],
            "selected_run_ids": sorted(selected_formal_run_ids),
            "source_count": len(completed),
        }
        return {
            "results": rows,
            "context": {
                "availability": "AVAILABLE" if rows else "UNAVAILABLE",
                "sources": contexts,
                "reasoning_included": False,
            },
            "trace": {
                "source_status": (
                    "partial" if error_codes else ("complete" if rows else "no_match")
                ),
                "error_code": "experiment_source_partial" if error_codes else None,
                "source_error_codes": sorted(set(error_codes)),
                "index_generation": sorted(set(generations)),
                "watermarks": sorted(set(watermarks)),
                "runtime_version": "experiment-source-runtime-v2",
                "source_count": len(completed),
                "source_attempt_count": len(sources) * len(requested_groups),
                "partial_source_failures": len(error_codes),
                "selection_trace": selection_trace,
            },
            "index_generation": sorted(set(generations)),
            "analysis": analyses,
            "selection_trace": selection_trace,
        }

    @classmethod
    def _classify_experiment_source_failure(cls, error: Exception) -> tuple[str, str]:
        status, _code = cls._classify_source_error(error)
        normalized = str(error).casefold().replace("-", "_")
        typed_codes = {
            "aggregate_spec_incomplete",
            "analysis_run_outside_authorized_source",
            "analysis_run_selection_mismatch",
            "analysis_task_not_supported",
            "baseline_run_not_selected",
            "candidate_run_not_selected",
            "candidate_run_outside_authorized_source",
            "compare_spec_incomplete",
            "metric_definition_ambiguous",
            "metric_definition_not_found",
            "query_requires_clarification",
            "reproduce_spec_incomplete",
            "source_not_ready",
            "source_generation_mismatch",
            "source_watermark_mismatch",
            "typed_query_rejected",
        }
        if status == "error" and normalized in typed_codes:
            status = "unavailable"
        code = (
            normalized
            if normalized in typed_codes
            else {
                "timeout": "experiment_source_timeout",
                "unauthorized": "experiment_source_unauthorized",
                "not_indexed": "experiment_source_not_indexed",
                "unavailable": "experiment_source_unavailable",
            }.get(status, "experiment_source_query_failed")
        )
        return status, code

    @staticmethod
    def _codex_task(query: str, intent: str | None) -> str:
        normalized = f"{intent or ''} {query}".casefold()
        if any(token in normalized for token in ("fail", "error", "timeout", "失败", "错误")):
            return "failure_retry"
        if any(token in normalized for token in ("why", "decision", "tradeoff", "为何", "决策")):
            return "rationale"
        if any(token in normalized for token in ("test", "pytest", "validation", "passed", "验证")):
            return "validation"
        return "process"

    def _project_acl_refs(self, project_id: str) -> set[str]:
        try:
            with self.store.database.connection() as db:
                row = db.execute(
                    "SELECT acl_ref FROM projects WHERE id=? AND status='active'",
                    (project_id,),
                ).fetchone()
        except Exception:
            return set()
        acl_ref = str(row["acl_ref"] or "") if row is not None else ""
        return {acl_ref} if acl_ref else set()

    @staticmethod
    def _project_notebook_source_v2(item: dict[str, Any]) -> dict[str, Any]:
        projected = dict(item)
        metadata = dict(projected.get("metadata") or {})
        raw_type = str(projected.get("entity_type") or "")
        projected["entity_type"] = {
            "revision": "NotebookRun",
            "execution": "NotebookRun",
            "cell": "NotebookCell",
            "cell_execution": "NotebookCell",
            "parameter": "NotebookParameter",
            "output": "NotebookOutput",
            "error": "NotebookError",
            "symbol": "NotebookCell",
            "artifact": "NotebookOutput",
        }.get(raw_type, raw_type)
        metadata["v2_entity_type"] = raw_type
        projected["metadata"] = metadata
        return projected

    @staticmethod
    def _document_entity_type_v2(raw_type: str) -> str:
        return {
            "version": "ScientificDocument",
            "layout_block": "DocumentSection",
            "section": "DocumentSection",
            "section_summary": "DocumentSection",
            "paragraph": "DocumentSection",
            "claim": "Claim",
            "claim_candidate": "Claim",
            "table": "DocumentTable",
            "table_row": "DocumentTable",
            "table_cell_fact": "DocumentTableCell",
            "figure": "DocumentFigure",
            "formula": "DocumentFigure",
            "citation_mention": "DocumentCitation",
            "reference_work": "DocumentCitation",
            "page": "DocumentPage",
        }.get(raw_type, raw_type)

    @classmethod
    def _project_document_source_v2(cls, item: dict[str, Any]) -> dict[str, Any]:
        projected = dict(item)
        metadata = dict(projected.get("metadata") or {})
        raw_type = str(projected.get("entity_type") or "")
        projected["entity_type"] = cls._document_entity_type_v2(raw_type)
        metadata["v2_entity_type"] = raw_type
        metadata["document_id"] = str(
            metadata.get("document_id") or metadata.get("formal_document_id") or ""
        )
        metadata["parent_id"] = str(metadata.get("parent_id") or metadata["document_id"])
        projected["metadata"] = metadata
        return projected

    @classmethod
    def _project_document_context_v2(
        cls,
        context: Any,
        results: list[dict[str, Any]],
    ) -> Any:
        if not isinstance(context, dict):
            return context
        projected = dict(context)
        by_v2_id = {
            str(item.get("metadata", {}).get("v2_entity_id") or item.get("entity_id") or ""): item
            for item in results
        }
        blocks: list[dict[str, Any]] = []
        for raw in projected.get("blocks") or []:
            block = dict(raw)
            item = by_v2_id.get(str(block.get("entity_id") or ""), {})
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            block["entity_type"] = cls._document_entity_type_v2(str(block.get("entity_type") or ""))
            document_id = str(metadata.get("document_id") or "")
            block["parent_id"] = str(metadata.get("parent_id") or document_id)
            block["document_id"] = document_id
            blocks.append(block)
        parent_ids = sorted(
            {
                str(item.get("metadata", {}).get("document_id") or "")
                for item in results
                if item.get("metadata", {}).get("document_id")
            }
        )
        projected["v2_schema_version"] = projected.get("schema_version")
        projected["schema_version"] = "document-evidence-context-v2"
        projected["blocks"] = blocks
        projected["parents"] = [{"document_id": item} for item in parent_ids]
        projected["reasoning_included"] = False
        return projected

    @staticmethod
    def _workspace_entity_type_v2(raw_type: str) -> str:
        return {
            "project": "ResearchProject",
            "topic": "ResearchTopic",
            "iteration": "ResearchIteration",
            "work_item": "ResearchWorkItem",
            "acceptance_criterion": "AcceptanceCriterion",
            "acceptance_check": "AcceptanceCheck",
            "dependency": "ResearchDependency",
            "blocker": "ResearchBlocker",
            "risk": "ResearchRisk",
            "evidence_requirement": "EvidenceRequirement",
            "evidence_link": "EvidenceLink",
            "decision": "ResearchDecision",
            "outcome": "ResearchOutcome",
            "transition": "StateTransition",
            "snapshot": "WorkspaceSnapshot",
            "intelligence_run": "WorkspaceIntelligence",
            "policy_version": "PolicyVersion",
        }.get(raw_type, raw_type)

    @classmethod
    def _project_workspace_source_v2(cls, item: dict[str, Any]) -> dict[str, Any]:
        projected = dict(item)
        metadata = dict(projected.get("metadata") or {})
        raw_type = str(projected.get("entity_type") or "")
        projected["entity_type"] = cls._workspace_entity_type_v2(raw_type)
        metadata["v2_entity_type"] = raw_type
        projected["metadata"] = metadata
        return projected

    @staticmethod
    def _project_workspace_context_v2(
        context: Any,
        *,
        as_of: str | None,
        confirmed_relations: Any,
    ) -> Any:
        if not isinstance(context, dict):
            return context
        projected = dict(context)
        projected["v2_schema_version"] = projected.get("schema_version")
        projected["schema_version"] = "workspace-control-plane-context-v2"
        projected["as_of"] = as_of
        projected["temporal_state"] = "UNAVAILABLE_WITH_AUDIT_EVIDENCE" if as_of else "CURRENT"
        projected["confirmed_relations"] = (
            list(confirmed_relations) if isinstance(confirmed_relations, list) else []
        )
        projected.setdefault("evidence_links", [])
        projected.setdefault("audit_evidence", [])
        projected["reasoning_included"] = False
        return projected

    @staticmethod
    def _normalize_source_status(raw_status: str, candidate_count: int) -> str:
        normalized = raw_status.casefold().replace("-", "_")
        aliases = {
            "completed": "complete",
            "retrieved": "complete",
            "no_matching_evidence": "no_match",
            "empty": "no_match",
            "denied": "unauthorized",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized == "scope_mismatch":
            return "SCOPE_MISMATCH"
        if normalized == "acl_denied":
            return "ACL_DENIED"
        exceptional = {
            "partial",
            "timeout",
            "unavailable",
            "unauthorized",
            "not_indexed",
            "error",
        }
        if normalized in exceptional:
            return normalized
        return "complete" if candidate_count else "no_match"

    @staticmethod
    def _classify_source_error(error: Exception) -> tuple[str, str]:
        if isinstance(error, TimeoutError):
            return "timeout", "source_timeout"
        if isinstance(error, PermissionError):
            return "unauthorized", "source_unauthorized"
        normalized = str(error).casefold().replace("-", "_")
        if "not_indexed" in normalized or "not indexed" in normalized:
            return "not_indexed", "source_not_indexed"
        if "unavailable" in normalized:
            return "unavailable", "source_unavailable"
        return "error", f"source_error:{type(error).__name__}"

    @staticmethod
    def _failed_source_outcome(
        *,
        source: str,
        status: str,
        error_code: str,
        engine_requested: str | None,
        duration_ms: float,
        wave: int,
    ) -> dict[str, Any]:
        return {
            "source": source,
            "results": [],
            "context": None,
            "trace": {
                "duration_ms": round(max(0.0, duration_ms), 2),
                "adapter_duration_ms": None,
                "status": status,
                "candidate_count": 0,
                "engine_requested": engine_requested,
                "engine_used": None,
                "index_generation": [],
                "error_code": error_code,
                "wave": wave,
            },
        }

    def _submit_code_shadow(
        self,
        request: EvidenceSearchRequest,
        legacy_response: dict[str, Any],
    ) -> None:
        if self.code_shadow is None or self.code_v1_adapter is None:
            return
        try:
            profile = self.code_v1_adapter.profile_for_request(request)
            self.code_shadow.submit(request, profile, legacy_response)
        except Exception:
            # Shadow is observational only and must never alter the V1 response path.
            return

    def _search_code(
        self,
        request: EvidenceSearchRequest,
        *,
        engine_override: str | None = None,
    ) -> dict[str, Any]:
        if self.code_integration is not None:
            return self.code_integration.search(
                request,
                engine_override=engine_override,
                shadow_runner=self.code_shadow,
            )
        response = self.code.search(request)
        self._submit_code_shadow(request, response)
        return response

    @staticmethod
    def _deduplicate(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for item in results:
            identity = root_provenance_key(item)
            previous = unique.get(identity)
            if previous is None or item["score"] > previous["score"]:
                if previous:
                    item["channels"] = sorted(
                        set(previous.get("channels", [])) | set(item.get("channels", []))
                    )
                    item["deduplicated_sources"] = sorted(
                        {
                            *previous.get(
                                "deduplicated_sources", [str(previous.get("source") or "")]
                            ),
                            *item.get("deduplicated_sources", [str(item.get("source") or "")]),
                        }
                        - {""}
                    )
                unique[identity] = item
            elif previous:
                previous["channels"] = sorted(
                    set(previous.get("channels", [])) | set(item.get("channels", []))
                )
                previous["deduplicated_sources"] = sorted(
                    {
                        *previous.get("deduplicated_sources", [str(previous.get("source") or "")]),
                        *item.get("deduplicated_sources", [str(item.get("source") or "")]),
                    }
                    - {""}
                )
        return list(unique.values())

    def _expand_edges(
        self,
        entity_ids: list[str],
        max_hops: int,
        *,
        request: GlobalSearchRequest,
        deadline_at: float,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Expand only relations whose endpoints pass project, ACL, scope, version, and review."""

        visited = set(entity_ids)
        frontier = set(entity_ids)
        edges: dict[str, dict[str, Any]] = {}
        counters = {
            "pruned_acl": 0,
            "pruned_scope": 0,
            "pruned_version": 0,
            "pruned_review": 0,
            "pruned_confidence": 0,
            "pruned_type": 0,
        }
        applied_hops = 0
        deadline_exhausted = False
        for hop in range(min(4, max(0, max_hops))):
            if not frontier:
                break
            if time.perf_counter() >= deadline_at:
                deadline_exhausted = True
                break
            batch = self.store.edges_for(
                sorted(frontier),
                project_id=request.project_id,
                as_of=request.as_of,
            )
            reviewed_batch: list[dict[str, Any]] = []
            for edge in batch:
                if not str(edge.get("predicate") or "").strip():
                    counters["pruned_type"] += 1
                elif str(edge.get("review_status") or "").casefold() not in {
                    "confirmed",
                    "reviewed",
                }:
                    counters["pruned_review"] += 1
                elif float(edge.get("confidence") or 0.0) < 0.5:
                    counters["pruned_confidence"] += 1
                else:
                    reviewed_batch.append(edge)
            batch = reviewed_batch
            endpoints = {
                str(endpoint) for edge in batch for endpoint in (edge["source"], edge["target"])
            }
            acl_refs = self.store.entity_acl_refs(sorted(endpoints))
            allowed_acl_refs = set(request.allowed_acl_refs) | {"public"}
            if request.enforce_acl:
                acl_visible: list[dict[str, Any]] = []
                for edge in batch:
                    endpoint_ids = (str(edge["source"]), str(edge["target"]))
                    if any(
                        acl_refs.get(endpoint) not in allowed_acl_refs for endpoint in endpoint_ids
                    ):
                        counters["pruned_acl"] += 1
                    else:
                        acl_visible.append(edge)
                batch = acl_visible
                endpoints = {
                    str(endpoint) for edge in batch for endpoint in (edge["source"], edge["target"])
                }
            scopes = self.store.entity_scopes(sorted(endpoints))
            nodes = {str(node["id"]): node for node in self.store.resolve_nodes(sorted(endpoints))}
            next_frontier: set[str] = set()
            for edge in batch:
                if time.perf_counter() >= deadline_at:
                    deadline_exhausted = True
                    break
                endpoint_ids = (str(edge["source"]), str(edge["target"]))
                endpoint_scope_failed = False
                endpoint_version_failed = False
                for endpoint in endpoint_ids:
                    node = nodes.get(endpoint, {})
                    scope = scopes.get(endpoint, {})
                    source = self._scope_source(node, scope)
                    scope_item = {
                        "entity_id": endpoint,
                        "source": source,
                        "metadata": node.get("metadata", {}),
                    }
                    if not self._in_scope(scope_item, scope, request):
                        endpoint_scope_failed = True
                        break
                    version_item = {
                        "source": source,
                        "version": node.get("version"),
                    }
                    if self._version_alignment(version_item, request.commit) == "mismatch":
                        endpoint_version_failed = True
                        break
                if endpoint_scope_failed:
                    counters["pruned_scope"] += 1
                    continue
                if endpoint_version_failed:
                    counters["pruned_version"] += 1
                    continue
                edge_id = str(edge["id"])
                if edge_id not in edges:
                    edges[edge_id] = {**edge, "hop": hop + 1}
                for endpoint in endpoint_ids:
                    if endpoint not in visited:
                        visited.add(endpoint)
                        next_frontier.add(endpoint)
            applied_hops = hop + 1
            frontier = next_frontier
            if len(edges) >= 500:
                break
        return list(edges.values())[:500], {
            "edge_budget": 500,
            "max_hops_applied": applied_hops,
            "deadline_exhausted": deadline_exhausted,
            **counters,
        }

    @staticmethod
    def _scope_source(node: dict[str, Any], scope: dict[str, Any]) -> str:
        domain = str(node.get("domain") or "")
        normalized_domain = {
            "research": "workspace",
            "platform": "workspace",
        }.get(domain, domain)
        if normalized_domain in {
            "code",
            "codex",
            "experiment",
            "notebook",
            "document",
            "workspace",
        }:
            return normalized_domain
        if scope.get("repository_id"):
            return "code"
        if scope.get("document_id"):
            return "document"
        if scope.get("experiment_id"):
            return "experiment"
        if scope.get("thread_id"):
            return "codex"
        return "workspace"

    @staticmethod
    def _diversify(results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        """Apply a small repeat-source penalty without reserving slots for any source."""

        if limit <= 1 or len(results) <= 1:
            return results[:limit]
        selected: list[dict[str, Any]] = []
        remaining = list(results)
        source_counts: dict[str, int] = {}
        while remaining and len(selected) < limit:
            ranked = sorted(
                remaining,
                key=lambda item: (
                    -(
                        float(item.get("score") or 0.0)
                        - min(0.06, 0.015 * source_counts.get(str(item["source"]), 0))
                    ),
                    -float(item.get("score") or 0.0),
                    str(item.get("entity_id") or ""),
                ),
            )
            item = ranked[0]
            remaining.remove(item)
            source = str(item["source"])
            source_counts[source] = source_counts.get(source, 0) + 1
            explanation = item.get("fusion_explanation")
            if isinstance(explanation, dict):
                explanation["source_repeat_penalty"] = round(
                    min(0.06, 0.015 * (source_counts[source] - 1)),
                    6,
                )
                explanation["source_slot_reserved"] = False
            selected.append(item)
        selected.sort(key=lambda item: item["score"], reverse=True)
        return selected

    def resolve_scope(
        self,
        *,
        project_id: str,
        repository_ids: list[str],
        branch: str | None,
        commit: str | None,
        as_of: str | None,
    ) -> dict[str, Any]:
        return self.store.resolve_scope(project_id, repository_ids, branch, commit, as_of)

    def staleness_evidence(
        self, project_id: str, experiment_ids: list[str], document_ids: list[str]
    ) -> list[dict[str, Any]]:
        assessments = self.store.list_drift(project_id)
        if experiment_ids:
            with self.store.database.connection() as db:
                allowed_runs = {
                    row["id"]
                    for row in db.execute(
                        f"SELECT id FROM experiment_runs WHERE experiment_id IN ({','.join('?' for _ in experiment_ids)})",
                        experiment_ids,
                    ).fetchall()
                }
            assessments = [item for item in assessments if item["run_id"] in allowed_runs]
        if document_ids:
            with self.store.database.connection() as db:
                allowed_claims = {
                    row["id"]
                    for row in db.execute(
                        f"SELECT id FROM claims WHERE document_id IN ({','.join('?' for _ in document_ids)})",
                        document_ids,
                    ).fetchall()
                }
            assessments = [item for item in assessments if item["claim_id"] in allowed_claims]
        return assessments

    @staticmethod
    def _in_scope(
        item: dict[str, Any], scope: dict[str, Any], request: GlobalSearchRequest
    ) -> bool:
        if (
            request.repository_ids
            and item["source"] == "code"
            and scope.get("repository_id") not in request.repository_ids
        ):
            return False
        if (
            request.experiment_ids
            and item["source"] == "experiment"
            and (
                item.get("experiment_id")
                or item.get("metadata", {}).get("experiment_id")
                or scope.get("experiment_id")
            )
            not in request.experiment_ids
        ):
            return False
        if (
            request.document_ids
            and item["source"] == "document"
            and (
                item.get("document_id")
                or item.get("metadata", {}).get("document_id")
                or scope.get("document_id")
            )
            not in request.document_ids
        ):
            return False
        if request.thread_ids and item["source"] == "codex":
            raw_thread = (
                item.get("thread_id")
                or item.get("metadata", {}).get("thread_id")
                or scope.get("thread_id")
            )
            if raw_thread not in request.thread_ids:
                return False
        return True

    @staticmethod
    def _version_alignment(item: dict[str, Any], commit: str | None) -> str:
        if not commit or item["source"] != "code":
            return "not_applicable"
        version = str(item.get("version") or "")
        return (
            "exact"
            if version == commit or version.startswith(commit) or commit.startswith(version)
            else "mismatch"
        )

    @staticmethod
    def _intent_entity_factor(intent: str | None, entity_type: str) -> float:
        normalized = entity_type.casefold()
        if intent == "current_implementation":
            if normalized in {"commit", "diffhunk"}:
                return 0.35
            if normalized == "testresult":
                return 0.7
        if intent == "historical_implementation" and normalized in {
            "codesymbol",
            "fileversion",
        }:
            return 0.5
        if intent == "change_trace" and normalized in {"codesymbol", "fileversion"}:
            return 0.55
        return 1.0

    def lineage(
        self,
        entity_id: str,
        depth: int,
        *,
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
    ) -> dict[str, Any]:
        allowed = set(allowed_acl_refs or []) | {"public"}
        if enforce_acl and self.store.entity_acl_refs([entity_id]).get(entity_id) not in allowed:
            return {"root": entity_id, "depth": depth, "nodes": [], "edges": []}
        visited = {entity_id}
        frontier = {entity_id}
        edges: dict[str, dict[str, Any]] = {}
        for _ in range(depth):
            batch = self.store.edges_for(list(frontier))
            if enforce_acl:
                endpoints = {
                    endpoint for edge in batch for endpoint in (edge["source"], edge["target"])
                }
                acl_refs = self.store.entity_acl_refs(list(endpoints))
                batch = [
                    edge
                    for edge in batch
                    if acl_refs.get(edge["source"]) in allowed
                    and acl_refs.get(edge["target"]) in allowed
                ]
            next_frontier = set()
            for edge in batch:
                edges[edge["id"]] = edge
                for endpoint in (edge["source"], edge["target"]):
                    if endpoint not in visited:
                        visited.add(endpoint)
                        next_frontier.add(endpoint)
            frontier = next_frontier
            if not frontier:
                break
        return {
            "root": entity_id,
            "depth": depth,
            "nodes": self.store.resolve_nodes(list(visited)),
            "edges": list(edges.values()),
        }

    def neighbors(
        self,
        entity_id: str,
        cursor: int,
        limit: int,
        *,
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
    ) -> dict[str, Any]:
        allowed = set(allowed_acl_refs or []) | {"public"}
        if enforce_acl and self.store.entity_acl_refs([entity_id]).get(entity_id) not in allowed:
            return {
                "root": entity_id,
                "cursor": cursor,
                "next_cursor": cursor,
                "total": 0,
                "has_more": False,
                "nodes": [],
                "edges": [],
            }
        page = self.store.neighbor_edges_page(entity_id, cursor=cursor, limit=limit)
        edges = page["edges"]
        if enforce_acl:
            endpoints = {
                endpoint for edge in edges for endpoint in (edge["source"], edge["target"])
            }
            acl_refs = self.store.entity_acl_refs(list(endpoints))
            edges = [
                edge
                for edge in edges
                if acl_refs.get(edge["source"]) in allowed
                and acl_refs.get(edge["target"]) in allowed
            ]
        node_ids = list(
            dict.fromkeys(
                [
                    entity_id,
                    *(endpoint for edge in edges for endpoint in (edge["source"], edge["target"])),
                ]
            )
        )
        return {
            "root": entity_id,
            "cursor": page["cursor"],
            "next_cursor": page["next_cursor"],
            "total": page["total"] if not enforce_acl else None,
            "has_more": page["has_more"],
            "nodes": self.store.resolve_nodes(node_ids),
            "edges": edges,
        }

    def graph(
        self,
        project_id: str,
        domain: str,
        mode: str,
        query: str,
        limit: int,
        *,
        focus_entity_id: str = "",
        relation_types: list[str] | None = None,
        direction: str = "both",
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
    ) -> dict[str, Any]:
        if domain == "code" and mode == "relations" and focus_entity_id:
            branch = self.store.code_relation_snapshot(
                project_id,
                focus_entity_id,
                relation_types=relation_types,
                direction=direction,
                limit=limit,
            )
            nodes = branch["nodes"]
            edges = branch["edges"]
            if enforce_acl:
                allowed = set(allowed_acl_refs or []) | {"public"}
                acl_refs = self.store.entity_acl_refs([node["id"] for node in nodes])
                visible_ids = {
                    node["id"] for node in nodes if acl_refs.get(node["id"], "public") in allowed
                }
                nodes = [node for node in nodes if node["id"] in visible_ids]
                edges = [
                    edge
                    for edge in edges
                    if edge["source"] in visible_ids and edge["target"] in visible_ids
                ]
            metadata = self.store.graph_metadata(project_id, "code")
            return {
                "domain": "code",
                "mode": "relations",
                "query": query,
                "focus_entity_id": focus_entity_id,
                "nodes": nodes,
                "edges": edges,
                "metadata": {
                    **metadata,
                    "visible_nodes": len(nodes),
                    "visible_edges": len(edges),
                    "sampled": len(nodes) >= limit,
                    "relation_counts": branch["relation_counts"],
                    "direction_counts": branch["direction_counts"],
                    "direction": direction,
                    "relation_types": relation_types or [],
                },
            }
        if domain == "overview" and (mode != "semantic" or not query.strip()):
            source_names = ("code", "codex", "research")
            source_metadata = {
                source: self.store.graph_metadata(project_id, source) for source in source_names
            }
            source_totals = {
                source: source_metadata[source]["total_nodes"] for source in source_names
            }
            active_sources = [source for source in source_names if source_totals[source] > 0]
            source_limits = {source: 0 for source in source_names}
            if active_sources:
                base = max(1, min(12, limit // len(active_sources)))
                for source in active_sources:
                    source_limits[source] = min(base, source_totals[source])
                remaining = max(0, limit - sum(source_limits.values()))
                for source in active_sources:
                    available = source_totals[source] - source_limits[source]
                    addition = min(remaining, available)
                    source_limits[source] += addition
                    remaining -= addition
                    if remaining == 0:
                        break
            snapshots = [
                self.store.graph_snapshot(
                    project_id,
                    source,
                    query,
                    source_limits[source],
                    metadata=source_metadata[source],
                )
                for source in active_sources
            ]
            nodes_by_id = {node["id"]: node for snapshot in snapshots for node in snapshot["nodes"]}
            node_ids = list(nodes_by_id)[:limit]
            allowed = set(node_ids)
            edges_by_id = {
                edge["id"]: edge
                for snapshot in snapshots
                for edge in snapshot["edges"]
                if edge["source"] in allowed and edge["target"] in allowed
            }
            for edge in self.store.edges_for(node_ids):
                if edge["source"] in allowed and edge["target"] in allowed:
                    edges_by_id[edge["id"]] = edge
            nodes = [nodes_by_id[node_id] for node_id in node_ids]
            domains = {node["id"]: node["domain"] for node in nodes}
            cross_source_edges = sum(
                1
                for edge in edges_by_id.values()
                if domains.get(edge["source"]) != domains.get(edge["target"])
            )
            metadata = {
                "total_nodes": sum(item["total_nodes"] for item in source_metadata.values()),
                "total_edges": sum(item["total_edges"] for item in source_metadata.values()),
                "vector_views": sum(item["vector_views"] for item in source_metadata.values()),
                "embedding_model": next(
                    (
                        item["embedding_model"]
                        for item in source_metadata.values()
                        if item["embedding_model"]
                    ),
                    None,
                ),
            }
            snapshot = {
                "domain": "overview",
                "mode": "relations",
                "query": query,
                "nodes": nodes,
                "edges": list(edges_by_id.values())[: limit * 2],
                "metadata": {
                    **metadata,
                    "visible_nodes": len(nodes),
                    "visible_edges": min(len(edges_by_id), limit * 2),
                    "cross_source_edges": cross_source_edges,
                    "sampled": metadata["total_nodes"] > len(nodes),
                },
            }
            if not enforce_acl:
                return snapshot
            allowed_acl = set(allowed_acl_refs or []) | {"public"}
            acl_refs = self.store.entity_acl_refs(node_ids)
            visible_ids = {
                node_id for node_id in node_ids if acl_refs.get(node_id, "public") in allowed_acl
            }
            snapshot["nodes"] = [node for node in snapshot["nodes"] if node["id"] in visible_ids]
            snapshot["edges"] = [
                edge
                for edge in snapshot["edges"]
                if edge["source"] in visible_ids and edge["target"] in visible_ids
            ]
            snapshot["metadata"]["visible_nodes"] = len(snapshot["nodes"])
            snapshot["metadata"]["visible_edges"] = len(snapshot["edges"])
            return snapshot
        if mode == "semantic" and query.strip():
            if domain == "code":
                code_request = EvidenceSearchRequest(
                    query=query.strip(),
                    scope=SearchScope(
                        project_id=project_id,
                        allowed_acl_refs=list(allowed_acl_refs or []),
                        enforce_acl=enforce_acl,
                    ),
                    limit=min(limit, 30),
                    include_edges=False,
                )
                code_search = self._search_code(code_request)
                search = {
                    "results": [self._code(item) for item in code_search["results"]],
                    "total": code_search["total"],
                }
            else:
                sources = {
                    "overview": ["code", "codex", "experiment", "document"],
                    "research": ["experiment", "document"],
                }.get(domain, [domain])
                search = self.search(
                    GlobalSearchRequest(
                        query=query.strip(),
                        project_id=project_id,
                        sources=sources,
                        limit=min(limit, 30),
                        include_lineage=False,
                        allowed_acl_refs=list(allowed_acl_refs or []),
                        enforce_acl=enforce_acl,
                    ),
                    record_event=False,
                )
            nodes = [
                {
                    "id": item["entity_id"],
                    "type": item["entity_type"],
                    "label": item["title"],
                    "locator": item["locator"],
                    "domain": item["source"],
                    "version": item.get("version"),
                    "score": item["score"],
                    "channels": item.get("channels", []),
                    "repository_id": item.get("repository_id"),
                    "path": item.get("path"),
                    "language": item.get("language"),
                    "qualified_name": item.get("qualified_name") or item.get("title"),
                    "start_line": item.get("start_line"),
                    "end_line": item.get("end_line"),
                    "metadata": item.get("metadata") or {},
                }
                for item in search["results"]
            ]
            query_id = f"semantic-query://{uuid4().hex}"
            query_node = {
                "id": query_id,
                "type": "SemanticQuery",
                "label": query.strip(),
                "locator": query_id,
                "domain": "query",
                "version": self.store.graph_metadata(project_id, domain).get("embedding_model"),
                "score": 1.0,
                "channels": ["query_vector"],
            }
            entity_ids = [node["id"] for node in nodes]
            semantic_entity_ids = list(entity_ids)
            structural_neighbor_count = 0
            if domain == "code" and len(entity_ids) < limit:
                code_edge_priority = {
                    "CALLS": 0,
                    "REFERENCES": 1,
                    "IMPORTS": 2,
                    "DEFINES": 3,
                }
                seed_edges = sorted(
                    (
                        edge
                        for edge in self.store.edges_for(
                            entity_ids,
                            project_id=project_id,
                        )
                        if edge["domain"] == "code" and edge["predicate"] in code_edge_priority
                    ),
                    key=lambda edge: (
                        code_edge_priority[str(edge["predicate"])],
                        str(edge["id"]),
                    ),
                )
                selected = set(entity_ids)
                neighbor_ids: list[str] = []
                for edge in seed_edges:
                    for endpoint in (edge["source"], edge["target"]):
                        if endpoint in selected:
                            continue
                        selected.add(endpoint)
                        neighbor_ids.append(str(endpoint))
                        if len(entity_ids) + len(neighbor_ids) >= limit:
                            break
                    if len(entity_ids) + len(neighbor_ids) >= limit:
                        break
                if enforce_acl and neighbor_ids:
                    allowed = set(allowed_acl_refs or []) | {"public"}
                    acl_refs = self.store.entity_acl_refs(neighbor_ids)
                    neighbor_ids = [
                        entity_id
                        for entity_id in neighbor_ids
                        if acl_refs.get(entity_id, "public") in allowed
                    ]
                neighbor_nodes = [
                    node
                    for node in self.store.resolve_nodes(neighbor_ids)
                    if node.get("domain") == "code" and node.get("type") != "ExternalEntity"
                ]
                nodes.extend(neighbor_nodes)
                entity_ids.extend(str(node["id"]) for node in neighbor_nodes)
                structural_neighbor_count = len(neighbor_nodes)
            selected = set(entity_ids)
            relation_edges = [
                edge
                for edge in self.store.edges_for(entity_ids, project_id=project_id)
                if edge["source"] in selected and edge["target"] in selected
            ]
            vector_edges = self.code.semantic_links(semantic_entity_ids) if domain == "code" else []
            semantic_edges = [
                {
                    "id": f"{query_id}/{index}",
                    "source": query_id,
                    "predicate": "SEMANTIC_MATCH",
                    "target": node["id"],
                    "derivation": "hybrid_vector_retrieval",
                    "confidence": node["score"],
                    "review_status": "retrieved",
                    "domain": domain,
                }
                for index, node in enumerate(nodes[: len(semantic_entity_ids)])
            ]
            metadata = self.store.graph_metadata(project_id, domain)
            relation_counts: dict[str, int] = {}
            for edge in [*relation_edges, *vector_edges]:
                predicate = str(edge["predicate"])
                relation_counts[predicate] = relation_counts.get(predicate, 0) + 1
            return {
                "domain": domain,
                "mode": "semantic",
                "query": query.strip(),
                "nodes": [query_node, *nodes],
                "edges": [*semantic_edges, *vector_edges, *relation_edges],
                "metadata": {
                    **metadata,
                    "visible_nodes": len(nodes) + 1,
                    "visible_edges": (
                        len(semantic_edges) + len(vector_edges) + len(relation_edges)
                    ),
                    "sampled": search["total"] >= min(limit, 30),
                    "relation_counts": relation_counts,
                    "semantic_similarity_edges": len(vector_edges),
                    "semantic_result_nodes": len(semantic_entity_ids),
                    "structural_neighbor_nodes": structural_neighbor_count,
                },
            }
        snapshot = self.store.graph_snapshot(
            project_id,
            domain,
            query,
            limit,
            focus_entity_id=focus_entity_id,
        )
        if not enforce_acl:
            return snapshot
        allowed = set(allowed_acl_refs or []) | {"public"}
        node_ids = [node["id"] for node in snapshot["nodes"]]
        acl_refs = self.store.entity_acl_refs(node_ids)
        visible_ids = {
            node_id for node_id in node_ids if acl_refs.get(node_id, "public") in allowed
        }
        snapshot["nodes"] = [node for node in snapshot["nodes"] if node["id"] in visible_ids]
        snapshot["edges"] = [
            edge
            for edge in snapshot["edges"]
            if edge["source"] in visible_ids and edge["target"] in visible_ids
        ]
        snapshot["metadata"]["visible_nodes"] = len(snapshot["nodes"])
        snapshot["metadata"]["visible_edges"] = len(snapshot["edges"])
        return snapshot

    @staticmethod
    def _code(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": "code",
            "entity_id": item["entity_id"],
            "entity_type": item["entity_type"],
            "title": item["qualified_name"] or item["name"],
            "subtitle": item["path"],
            "snippet": item["snippet"],
            "locator": item["evidence_locator"],
            "version": item["commit"],
            "status": "indexed",
            "score": item["score"],
            "channels": item["channels"],
            "repository_id": item["repository_id"],
            "path": item["path"],
            "language": item.get("language"),
            "qualified_name": item.get("qualified_name"),
            "start_line": item["start_line"],
            "end_line": item["end_line"],
            "metadata": item.get("metadata") or {},
        }

    @staticmethod
    def _codex(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": "codex",
            "entity_id": item["entity_id"],
            "entity_type": item["item_type"],
            "title": item["name"],
            "subtitle": item["thread_title"],
            "snippet": item["snippet"],
            "locator": item["evidence_locator"],
            "version": item["timestamp"],
            "status": item["status"],
            "score": item["score"],
            "channels": item["channels"],
            "thread_id": item["thread_id"],
            "episode_id": item.get("episode_id"),
            "turn_id": item.get("turn_id"),
            "evidence_role": item.get("evidence_role"),
            "event_status": item.get("event_status"),
            "negative_reasons": item.get("negative_reasons") or [],
        }
