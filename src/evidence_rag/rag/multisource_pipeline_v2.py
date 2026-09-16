"""Governed M5/M6 orchestration without changing the production default."""

from __future__ import annotations

import inspect
import math
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .answer_v2 import (
    AnswerClaimV2,
    EvidencePackV2,
    GroundedAnswerV2,
    build_evidence_pack_v2,
    build_grounded_answer_v2,
)
from .evidence_graph_v2 import (
    EvidenceEdgeRegistryV2,
    EvidenceEdgeV2,
    EvidenceTraversalTraceV2,
    build_default_edge_registry_v2,
    traverse_evidence_graph_v2,
)
from .global_governance_v2 import (
    GlobalRouteDecisionV2,
    GlobalRouteRequestV2,
    ReleaseStageV2,
    SanitizedGlobalTraceV2,
    inspect_untrusted_content_v2,
    route_global_request_v2,
)
from .global_switches_v2 import GlobalComponentSwitchesV2
from .multisource_foundation_v2 import (
    CapabilityRegistryV2,
    CrossSourceRerankResultV2,
    MultiSourceCandidateV2,
    MultiSourcePlanV2,
    MultiSourceScopeV2,
    RoleFusionResultV2,
    SourceCalibrationProfileV2,
    SourceExecutionStatusV2,
    SourceExecutionV2,
    fuse_multisource_candidates_v2,
    plan_multisource_query_v2,
    rerank_cross_source_candidates_v2,
)
from .sources.experiment.contracts_v2 import canonical_sha256_v2

MULTISOURCE_PIPELINE_VERSION = "governed-multisource-pipeline-v2"
_COOPERATIVE_CANCELLATION_DRAIN_SECONDS = 0.25


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class MultiSourcePipelineRequestV2(_Frozen):
    project_id: str
    request_id: str
    question: str = Field(min_length=1, max_length=4_000)
    intent: str
    scope: MultiSourceScopeV2
    requested_sources: tuple[str, ...] = ()
    context_budget_tokens: int = Field(default=6_000, ge=256, le=32_000)
    claims: tuple[AnswerClaimV2, ...] = ()

    @model_validator(mode="after")
    def _scope(self) -> MultiSourcePipelineRequestV2:
        if self.project_id != self.scope.project_id:
            raise ValueError("request project and governed scope differ")
        if self.requested_sources != tuple(dict.fromkeys(self.requested_sources)):
            raise ValueError("requested sources must be ordered and unique")
        return self


class SourceRetrievalBatchV2(_Frozen):
    source: str
    plan_sha256: str
    scope_sha256: str
    candidates: tuple[MultiSourceCandidateV2, ...]
    execution: SourceExecutionV2
    batch_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> SourceRetrievalBatchV2:
        if self.source != self.execution.source:
            raise ValueError("source batch execution mismatch")
        if any(item.retrieval_domain != self.source for item in self.candidates):
            raise ValueError("source batch contains a foreign candidate")
        if self.execution.candidate_count != len(self.candidates):
            raise ValueError("source candidate denominator mismatch")
        if self.batch_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"batch_sha256"})
        ):
            raise ValueError("source retrieval batch digest mismatch")
        return self


@runtime_checkable
class MultiSourceRetrieverV2(Protocol):
    source: str

    def retrieve(
        self,
        *,
        plan: MultiSourcePlanV2,
        scope: MultiSourceScopeV2,
        budget: int,
        required_roles: tuple[str, ...],
        corrective_round: int,
    ) -> SourceRetrievalBatchV2: ...


class SourceCancellationTokenV2:
    """Read-only cooperative cancellation signal for one pipeline execution.

    The deadline is an absolute ``time.perf_counter`` timestamp.  Adapters
    should call :meth:`cancelled`, :meth:`wait`, or :meth:`remaining_seconds`
    between bounded I/O operations.  They must not treat the token as durable
    evidence because its clock value is process-local.
    """

    __slots__ = ("_deadline", "_event", "_lock", "_reason")

    def __init__(self, *, deadline: float) -> None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise TypeError("cancellation deadline must be a finite monotonic timestamp")
        normalized = float(deadline)
        if not math.isfinite(normalized):
            raise ValueError("cancellation deadline must be a finite monotonic timestamp")
        self._deadline = normalized
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None

    @property
    def deadline(self) -> float:
        return self._deadline

    @property
    def reason(self) -> str | None:
        with self._lock:
            reason = self._reason
        if reason is not None:
            return reason
        if time.perf_counter() >= self._deadline:
            return "global_deadline_exceeded"
        return None

    def cancelled(self) -> bool:
        return self._event.is_set() or time.perf_counter() >= self._deadline

    def remaining_seconds(self) -> float:
        return max(0.0, self._deadline - time.perf_counter())

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("cancellation wait timeout must be finite")
            timeout = float(timeout)
            if not math.isfinite(timeout) or timeout < 0:
                raise ValueError("cancellation wait timeout must be finite and non-negative")
        remaining = self.remaining_seconds()
        bounded_timeout = remaining if timeout is None else min(timeout, remaining)
        signalled = self._event.wait(bounded_timeout)
        return signalled or self.cancelled()

    def _cancel(self, reason: str) -> None:
        with self._lock:
            if self._reason is None:
                self._reason = reason
        self._event.set()


@runtime_checkable
class CooperativeMultiSourceRetrieverV2(Protocol):
    source: str

    def retrieve(
        self,
        *,
        plan: MultiSourcePlanV2,
        scope: MultiSourceScopeV2,
        budget: int,
        required_roles: tuple[str, ...],
        corrective_round: int,
        cancellation: SourceCancellationTokenV2,
    ) -> SourceRetrievalBatchV2: ...


@runtime_checkable
class EvidenceEdgeProviderV2(Protocol):
    def resolve(
        self,
        *,
        plan: MultiSourcePlanV2,
        candidates: tuple[MultiSourceCandidateV2, ...],
    ) -> tuple[EvidenceEdgeV2, ...]: ...


class MultiSourcePipelineResultV2(_Frozen):
    route: GlobalRouteDecisionV2
    plan: MultiSourcePlanV2 | None
    rerank: CrossSourceRerankResultV2 | None
    fusion: RoleFusionResultV2 | None
    traversal: EvidenceTraversalTraceV2 | None
    evidence_pack: EvidencePackV2 | None
    answer: GroundedAnswerV2 | None
    trace: SanitizedGlobalTraceV2
    corrective_rounds_executed: int = Field(ge=0, le=2)
    status: str
    fallback_reason: str | None
    pipeline_version: str = MULTISOURCE_PIPELINE_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> MultiSourcePipelineResultV2:
        if self.route.execute_v2 and self.status == "completed" and self.evidence_pack is None:
            raise ValueError("completed V2 pipeline requires an evidence pack")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("pipeline result digest mismatch")
        return self


def _scope_sha256(scope: MultiSourceScopeV2) -> str:
    return canonical_sha256_v2(scope.model_dump(mode="json"))


def build_source_batch_v2(
    *,
    source: str,
    plan: MultiSourcePlanV2,
    candidates: tuple[MultiSourceCandidateV2, ...],
    execution: SourceExecutionV2,
) -> SourceRetrievalBatchV2:
    payload = {
        "source": source,
        "plan_sha256": plan.content_sha256,
        "scope_sha256": _scope_sha256(plan.scope),
        "candidates": candidates,
        "execution": execution,
    }
    normalized = SourceRetrievalBatchV2.model_construct(
        batch_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"batch_sha256"})
    return SourceRetrievalBatchV2(
        **payload,
        batch_sha256=canonical_sha256_v2(normalized),
    )


def _validate_batch(
    *,
    batch: SourceRetrievalBatchV2,
    plan: MultiSourcePlanV2,
    registry: CapabilityRegistryV2,
) -> tuple[MultiSourceCandidateV2, ...]:
    if batch.plan_sha256 != plan.content_sha256:
        raise ValueError("source batch plan mismatch")
    if batch.scope_sha256 != _scope_sha256(plan.scope):
        raise ValueError("source batch scope mismatch")
    capability = next((item for item in registry.capabilities if item.domain == batch.source), None)
    if capability is None:
        raise ValueError("source batch capability missing")
    if batch.execution.index_generation != capability.index_generation:
        raise ValueError("source batch generation mismatch")
    if batch.execution.watermark != capability.watermark:
        raise ValueError("source batch watermark mismatch")
    if any(item.source_generation != capability.index_generation for item in batch.candidates):
        raise ValueError("candidate generation mismatch")
    if any(item.acl_ref not in plan.scope.acl_refs for item in batch.candidates):
        raise ValueError("source batch contains unauthorized evidence")
    return batch.candidates


def _calibrate_candidates(
    candidates: tuple[MultiSourceCandidateV2, ...],
    profiles: Mapping[str, SourceCalibrationProfileV2],
) -> tuple[MultiSourceCandidateV2, ...]:
    calibrated: list[MultiSourceCandidateV2] = []
    for item in candidates:
        profile = profiles.get(item.retrieval_domain)
        if profile is None:
            raise ValueError(f"calibration unavailable:{item.retrieval_domain}")
        raw = max((score for _, score in item.channel_scores), default=0.0)
        calibrated.append(
            item.model_copy(
                update={
                    "calibrated_relevance": profile.probability(raw),
                    "calibration_version": profile.calibration_version,
                }
            )
        )
    return tuple(calibrated)


def _supports_cooperative_cancellation(retriever: MultiSourceRetrieverV2) -> bool:
    """Return whether ``retrieve`` explicitly accepts the optional contract."""

    try:
        parameters = inspect.signature(retriever.retrieve).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "cancellation" and parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
        for parameter in parameters
    )


def _submit_retrieval(
    executor: ThreadPoolExecutor,
    retriever: MultiSourceRetrieverV2,
    *,
    plan: MultiSourcePlanV2,
    scope: MultiSourceScopeV2,
    budget: int,
    required_roles: tuple[str, ...],
    corrective_round: int,
    cancellation: SourceCancellationTokenV2,
) -> Future[SourceRetrievalBatchV2]:
    arguments = {
        "plan": plan,
        "scope": scope,
        "budget": budget,
        "required_roles": required_roles,
        "corrective_round": corrective_round,
    }
    if _supports_cooperative_cancellation(retriever):
        arguments["cancellation"] = cancellation
    return executor.submit(retriever.retrieve, **arguments)


def _shutdown_source_executor(
    executor: ThreadPoolExecutor,
    futures: tuple[Future[SourceRetrievalBatchV2], ...],
    cancellation: SourceCancellationTokenV2,
    *,
    reason: str,
) -> None:
    """Signal running adapters and reclaim cooperative workers before return."""

    cancellation._cancel(reason)
    for future in futures:
        future.cancel()
    _, running = wait(futures, timeout=_COOPERATIVE_CANCELLATION_DRAIN_SECONDS)
    executor.shutdown(wait=not running, cancel_futures=True)


class GovernedMultiSourcePipelineV2:
    """Pure coordinator over governed source adapters and immutable results."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistryV2,
        retrievers: Mapping[str, MultiSourceRetrieverV2],
        calibrations: Mapping[str, SourceCalibrationProfileV2],
        edge_provider: EvidenceEdgeProviderV2,
        edge_registry: EvidenceEdgeRegistryV2 | None = None,
        component_switches: GlobalComponentSwitchesV2 | None = None,
        max_workers: int = 4,
        deadline_seconds: float = 3.0,
    ) -> None:
        if not 1 <= max_workers <= 8:
            raise ValueError("max_workers must be between 1 and 8")
        if not 0.05 <= deadline_seconds <= 30:
            raise ValueError("deadline_seconds must be between 0.05 and 30")
        self._registry = registry
        self._retrievers = dict(retrievers)
        self._calibrations = dict(calibrations)
        self._edge_provider = edge_provider
        self._edge_registry = edge_registry or build_default_edge_registry_v2()
        self._component_switches = component_switches or GlobalComponentSwitchesV2()
        self._max_workers = max_workers
        self._deadline_seconds = deadline_seconds

    def run(
        self,
        request: MultiSourcePipelineRequestV2,
        *,
        deployed_stage: ReleaseStageV2 = ReleaseStageV2.OFFLINE,
        explicit_v2: bool = True,
    ) -> MultiSourcePipelineResultV2:
        run_started = time.perf_counter()
        deadline_at = run_started + self._deadline_seconds
        route = route_global_request_v2(
            GlobalRouteRequestV2(
                project_id=request.project_id,
                request_id=request.request_id,
                explicit_engine="v2" if explicit_v2 else None,
            ),
            deployed_stage=deployed_stage,
        )
        if not route.execute_v2:
            return self._fallback(
                route=route,
                request=request,
                reason="v2_not_selected",
                plan=None,
            )
        rollback_blocker = self._component_switches.rollback_blocker
        if rollback_blocker is not None:
            return self._fallback(
                route=route,
                request=request,
                reason=rollback_blocker,
                plan=None,
            )
        plan = plan_multisource_query_v2(
            question=request.question,
            intent=request.intent,
            scope=request.scope,
            registry=self._registry,
            requested_sources=request.requested_sources,
            context_budget_tokens=request.context_budget_tokens,
        )
        if not plan.source_routes:
            return self._fallback(
                route=route,
                request=request,
                reason="no_capable_source",
                plan=plan,
            )
        candidates: list[MultiSourceCandidateV2] = []
        executions: dict[str, SourceExecutionV2] = {}
        filtered = 0
        budgets = dict(plan.source_budgets)
        capabilities = {item.domain: item for item in self._registry.capabilities}

        def failed_execution(
            source: str,
            *,
            status: SourceExecutionStatusV2,
            error_code: str,
            latency_ms: float = 0,
        ) -> SourceExecutionV2:
            capability = capabilities[source]
            return SourceExecutionV2(
                source=source,
                status=status,
                candidate_count=0,
                coverage=None,
                index_generation=capability.index_generation,
                watermark=capability.watermark,
                latency_ms=max(0, latency_ms),
                error_code=error_code,
            )

        executor = ThreadPoolExecutor(
            max_workers=min(self._max_workers, len(plan.source_routes)),
            thread_name_prefix="governed-rag-source",
        )
        cancellation = SourceCancellationTokenV2(deadline=deadline_at)
        submitted_futures: list[Future[SourceRetrievalBatchV2]] = []
        futures: dict[str, Future[SourceRetrievalBatchV2]] = {}
        for source in plan.source_routes:
            retriever = self._retrievers.get(source)
            if retriever is None or retriever.source != source:
                executions[source] = failed_execution(
                    source,
                    status=SourceExecutionStatusV2.UNAVAILABLE,
                    error_code="retriever_unavailable",
                )
                continue
            futures[source] = _submit_retrieval(
                executor,
                retriever,
                plan=plan,
                scope=request.scope,
                budget=budgets[source],
                required_roles=plan.required_roles,
                corrective_round=0,
                cancellation=cancellation,
            )
            submitted_futures.append(futures[source])
        done, _ = wait(
            tuple(futures.values()),
            timeout=max(0.0, deadline_at - time.perf_counter()),
        )
        for source in plan.source_routes:
            future = futures.get(source)
            if future is None:
                continue
            if future not in done:
                cancellation._cancel("global_deadline_exceeded")
                future.cancel()
                executions[source] = failed_execution(
                    source,
                    status=SourceExecutionStatusV2.TIMEOUT,
                    error_code="global_deadline_exceeded",
                    latency_ms=(time.perf_counter() - run_started) * 1000,
                )
                continue
            try:
                batch = future.result()
                batch_candidates = _validate_batch(
                    batch=batch,
                    plan=plan,
                    registry=self._registry,
                )
                safe: list[MultiSourceCandidateV2] = []
                for candidate in batch_candidates:
                    blocked, _ = inspect_untrusted_content_v2(
                        candidate.title + "\n" + candidate.snippet,
                        requester_acl_refs=plan.scope.acl_refs,
                        evidence_acl_ref=candidate.acl_ref,
                    )
                    if blocked:
                        filtered += 1
                    else:
                        safe.append(candidate)
                candidates.extend(_calibrate_candidates(tuple(safe), self._calibrations))
                executions[source] = (
                    batch.execution.model_copy(
                        update={"status": SourceExecutionStatusV2.NO_MATCHING_EVIDENCE}
                    )
                    if not batch_candidates
                    and batch.execution.status is SourceExecutionStatusV2.COMPLETE
                    else batch.execution
                )
            except PermissionError:
                executions[source] = failed_execution(
                    source=source,
                    status=SourceExecutionStatusV2.UNAUTHORIZED,
                    error_code="governed_source_unauthorized",
                )
            except TimeoutError:
                executions[source] = failed_execution(
                    source=source,
                    status=SourceExecutionStatusV2.TIMEOUT,
                    error_code="governed_source_timeout",
                )
            except ValueError as error:
                generation_mismatch = "generation mismatch" in str(error).casefold()
                executions[source] = failed_execution(
                    source=source,
                    status=(
                        SourceExecutionStatusV2.UNAVAILABLE
                        if generation_mismatch
                        else SourceExecutionStatusV2.PARTIAL
                    ),
                    error_code=(
                        "source_generation_mismatch"
                        if generation_mismatch
                        else "governed_source_error:ValueError"
                    ),
                )
            except Exception as error:
                executions[source] = failed_execution(
                    source=source,
                    status=SourceExecutionStatusV2.PARTIAL,
                    error_code=f"governed_source_error:{type(error).__name__}",
                )
        try:
            source_status = tuple(executions[source] for source in plan.source_routes)
            rerank = (
                rerank_cross_source_candidates_v2(
                    plan,
                    tuple(candidates),
                    tuple(self._calibrations.values()),
                )
                if self._component_switches.reranker
                else None
            )
            ranked_candidates = rerank.ranked if rerank is not None else tuple(candidates)
            fusion = fuse_multisource_candidates_v2(
                plan,
                ranked_candidates,
                source_status,
            )
            corrective_rounds = 0
            traversal = self._traverse(plan, fusion, ranked_candidates)
            while (
                (fusion.missing_roles or traversal.missing_roles)
                and corrective_rounds < 2
                and time.perf_counter() < deadline_at
            ):
                corrective_rounds += 1
                gained = 0
                missing_roles = tuple(
                    dict.fromkeys((*fusion.missing_roles, *traversal.missing_roles))
                )
                corrective_futures: dict[str, Future[SourceRetrievalBatchV2]] = {}
                for source in plan.source_routes:
                    retriever = self._retrievers.get(source)
                    if retriever is None:
                        continue
                    corrective_futures[source] = _submit_retrieval(
                        executor,
                        retriever,
                        plan=plan,
                        scope=request.scope,
                        budget=max(2, budgets[source] // 2),
                        required_roles=missing_roles,
                        corrective_round=corrective_rounds,
                        cancellation=cancellation,
                    )
                    submitted_futures.append(corrective_futures[source])
                corrective_done, _ = wait(
                    tuple(corrective_futures.values()),
                    timeout=max(0.0, deadline_at - time.perf_counter()),
                )
                for source in plan.source_routes:
                    future = corrective_futures.get(source)
                    if future is None:
                        continue
                    if future not in corrective_done:
                        cancellation._cancel("global_deadline_exceeded")
                        future.cancel()
                        continue
                    try:
                        batch = future.result()
                        additions = _calibrate_candidates(
                            _validate_batch(
                                batch=batch,
                                plan=plan,
                                registry=self._registry,
                            ),
                            self._calibrations,
                        )
                        known = {item.candidate_id for item in candidates}
                        for candidate in additions:
                            blocked, _ = inspect_untrusted_content_v2(
                                candidate.title + "\n" + candidate.snippet,
                                requester_acl_refs=plan.scope.acl_refs,
                                evidence_acl_ref=candidate.acl_ref,
                            )
                            if blocked:
                                filtered += 1
                            elif candidate.candidate_id not in known:
                                candidates.append(candidate)
                                known.add(candidate.candidate_id)
                                gained += 1
                    except Exception:
                        continue
                if not gained:
                    break
                rerank = (
                    rerank_cross_source_candidates_v2(
                        plan,
                        tuple(candidates),
                        tuple(self._calibrations.values()),
                    )
                    if self._component_switches.reranker
                    else None
                )
                ranked_candidates = rerank.ranked if rerank is not None else tuple(candidates)
                fusion = fuse_multisource_candidates_v2(
                    plan,
                    ranked_candidates,
                    source_status,
                )
                traversal = self._traverse(plan, fusion, ranked_candidates)
        finally:
            _shutdown_source_executor(
                executor,
                tuple(submitted_futures),
                cancellation,
                reason=(
                    "global_deadline_exceeded"
                    if time.perf_counter() >= deadline_at
                    else "pipeline_retrieval_complete"
                ),
            )
        pack = build_evidence_pack_v2(plan, fusion, traversal)
        answer = build_grounded_answer_v2(
            pack,
            request.claims if self._component_switches.generator_prompt else (),
        )
        selected_hash = canonical_sha256_v2([item.candidate_id for item in fusion.selected])
        partial_statuses = {
            SourceExecutionStatusV2.PARTIAL,
            SourceExecutionStatusV2.TIMEOUT,
            SourceExecutionStatusV2.UNAVAILABLE,
            SourceExecutionStatusV2.UNAUTHORIZED,
            SourceExecutionStatusV2.NOT_INDEXED,
        }
        pipeline_status = (
            "partial"
            if any(item.status in partial_statuses for item in source_status)
            else "completed"
        )
        trace = SanitizedGlobalTraceV2(
            trace_id=request.request_id,
            project_sha256=canonical_sha256_v2(request.project_id),
            question_sha256=plan.question_sha256,
            scope_sha256=_scope_sha256(plan.scope),
            plan_sha256=plan.content_sha256,
            route_reason=route.reason,
            source_status=tuple((item.source, item.status.value) for item in source_status),
            candidate_count=len(candidates),
            filtered_count=(
                filtered
                + len(fusion.rejected)
                + (len(rerank.rejected) if rerank is not None else 0)
            ),
            selected_ids_sha256=selected_hash,
            cache_hits=0,
            fallback_reason=None,
        )
        payload = {
            "route": route,
            "plan": plan,
            "rerank": rerank,
            "fusion": fusion,
            "traversal": traversal,
            "evidence_pack": pack,
            "answer": answer,
            "trace": trace,
            "corrective_rounds_executed": corrective_rounds,
            "status": pipeline_status,
            "fallback_reason": None,
            "pipeline_version": MULTISOURCE_PIPELINE_VERSION,
        }
        return self._result(payload)

    def _traverse(
        self,
        plan: MultiSourcePlanV2,
        fusion: RoleFusionResultV2,
        candidates: tuple[MultiSourceCandidateV2, ...],
    ) -> EvidenceTraversalTraceV2:
        edges = self._edge_provider.resolve(plan=plan, candidates=candidates)
        return traverse_evidence_graph_v2(
            plan,
            candidates,
            edges,
            self._edge_registry,
            seed_ids=tuple(item.candidate_id for item in fusion.selected),
            max_hops=4 if plan.complexity.value == "multi_hop" else 2,
        )

    def _fallback(
        self,
        *,
        route: GlobalRouteDecisionV2,
        request: MultiSourcePipelineRequestV2,
        reason: str,
        plan: MultiSourcePlanV2 | None,
    ) -> MultiSourcePipelineResultV2:
        trace = SanitizedGlobalTraceV2(
            trace_id=request.request_id,
            project_sha256=canonical_sha256_v2(request.project_id),
            question_sha256=canonical_sha256_v2(request.question),
            scope_sha256=_scope_sha256(request.scope),
            plan_sha256=plan.content_sha256 if plan else None,
            route_reason=route.reason,
            source_status=(),
            candidate_count=0,
            filtered_count=0,
            selected_ids_sha256=None,
            cache_hits=0,
            fallback_reason=reason,
        )
        return self._result(
            {
                "route": route,
                "plan": plan,
                "rerank": None,
                "fusion": None,
                "traversal": None,
                "evidence_pack": None,
                "answer": None,
                "trace": trace,
                "corrective_rounds_executed": 0,
                "status": "fallback_v1",
                "fallback_reason": reason,
                "pipeline_version": MULTISOURCE_PIPELINE_VERSION,
            }
        )

    @staticmethod
    def _result(payload: dict[str, object]) -> MultiSourcePipelineResultV2:
        normalized = MultiSourcePipelineResultV2.model_construct(
            content_sha256="pending", **payload
        ).model_dump(mode="json", exclude={"content_sha256"})
        return MultiSourcePipelineResultV2(
            **payload,
            content_sha256=canonical_sha256_v2(normalized),
        )


__all__ = [
    "MULTISOURCE_PIPELINE_VERSION",
    "CooperativeMultiSourceRetrieverV2",
    "EvidenceEdgeProviderV2",
    "GovernedMultiSourcePipelineV2",
    "MultiSourcePipelineRequestV2",
    "MultiSourcePipelineResultV2",
    "MultiSourceRetrieverV2",
    "SourceCancellationTokenV2",
    "SourceRetrievalBatchV2",
    "build_source_batch_v2",
]
