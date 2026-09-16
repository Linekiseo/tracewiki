from __future__ import annotations

import time
from statistics import mean
from typing import Any
from uuid import uuid4

from ..models import EvidenceSearchRequest, SearchScope
from ..platform.models import GlobalSearchRequest
from ..platform.service import PlatformService
from ..workspace.service import WorkspaceService
from .code import (
    CODE_EVALUATION_RUNNER_VERSION,
    aggregate_code_metrics,
    apply_paired_graph_recovery,
    evaluate_code_case,
)
from .models import CodeEvaluationRunRequest, EvaluationCaseCreate, EvaluationRunRequest
from .store import EvaluationStore


class EvaluationError(ValueError):
    pass


class EvaluationService:
    def __init__(
        self, store: EvaluationStore, platform: PlatformService, workspace: WorkspaceService
    ) -> None:
        self.store = store
        self.platform = platform
        self.workspace = workspace

    def create_case(self, request: EvaluationCaseCreate) -> dict[str, Any]:
        self.workspace._require_project(request.project_id)
        record = request.model_dump()
        profile = record.get("code_profile")
        if profile:
            typed_paths = profile.get("required_paths") or []
            legacy_paths = record.get("expected_paths") or []
            if legacy_paths and not typed_paths:
                raise EvaluationError(
                    "code cases with expected_paths require rich typed required_paths"
                )
            typed_nodes = [path["nodes"] for path in typed_paths]
            if legacy_paths and legacy_paths != typed_nodes:
                raise EvaluationError(
                    "expected_paths must exactly match required_paths node traversal"
                )
            if typed_paths and not legacy_paths:
                record["expected_paths"] = typed_nodes
            for dimension in ("entity_ids", "retrieval_unit_ids"):
                seen: set[str] = set()
                for group in profile.get("acceptable_alternative_groups") or []:
                    members = {str(item) for item in group.get(dimension) or []}
                    overlap = seen & members
                    if overlap:
                        raise EvaluationError(
                            "acceptable alternative groups overlap for "
                            f"{dimension}: {', '.join(sorted(overlap))}"
                        )
                    seen.update(members)
        token = uuid4().hex
        return self.store.create_case(
            {
                **record,
                "id": f"evaluation-case://{request.project_id}/{token}",
                "display_key": f"GQ-{token[:8].upper()}",
            }
        )

    def run(self, request: EvaluationRunRequest) -> dict[str, Any]:
        cases = self.store.list_cases(
            request.project_id, enabled_only=True, case_ids=request.case_ids or None
        )
        if not cases:
            raise EvaluationError("no enabled evaluation cases")
        token = uuid4().hex
        run_id = f"evaluation-run://{request.project_id}/{token}"
        display_key = f"EVAL-{token[:8].upper()}"
        self.store.create_run(run_id, display_key, request.project_id, len(cases))
        try:
            return self._run_global_cases(request, cases, run_id)
        except Exception as exc:
            self.store.fail_run(run_id, exc)
            if isinstance(exc, EvaluationError):
                raise
            raise EvaluationError(f"evaluation runner failed: {type(exc).__name__}: {exc}") from exc

    def _run_global_cases(
        self,
        request: EvaluationRunRequest,
        cases: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        scores = []
        for case in cases:
            result = self.platform.search(
                GlobalSearchRequest(
                    query=case["question"],
                    project_id=request.project_id,
                    sources=list(case["expected_sources"]),
                    limit=request.limit_per_query,
                    include_lineage=True,
                ),
                record_event=False,
            )
            related = result["evidence_pack"].get("related_entities", [])
            evidence_items = [*result["results"], *related]
            result_sources = {item["source"] for item in evidence_items}
            direct_result_ids = [item["entity_id"] for item in result["results"]]
            result_ids = list(dict.fromkeys(item["entity_id"] for item in evidence_items))
            expected_sources = set(case["expected_sources"])
            expected_ids = set(case["expected_entity_ids"])
            source_recall = (
                len(expected_sources & result_sources) / len(expected_sources)
                if expected_sources
                else 1.0
            )
            entity_recall = (
                len(expected_ids & set(result_ids)) / len(expected_ids) if expected_ids else 1.0
            )
            citation_count = len(result["evidence_pack"]["citation_map"])
            citation_completeness = min(1.0, citation_count / max(1, len(result_ids)))
            if case["required_version"]:
                comparable = [item for item in evidence_items if item.get("version")]
                wrong_versions = [
                    item for item in comparable if item.get("version") != case["required_version"]
                ]
                wrong_version_rate = len(wrong_versions) / max(1, len(comparable))
                version_accuracy = 1.0 - wrong_version_rate if comparable else 0.0
            else:
                version_accuracy = 1.0
                wrong_version_rate = 0.0
            relation_pairs = {
                (edge["source"], edge["target"]) for edge in result["evidence_pack"]["relations"]
            }
            recalled_paths = 0
            for path in case["expected_paths"]:
                if not path:
                    recalled_paths += 1
                    continue
                if len(path) == 1:
                    recalled_paths += int(path[0] in result_ids)
                    continue
                recalled_paths += int(
                    all(
                        (left, right) in relation_pairs or (right, left) in relation_pairs
                        for left, right in zip(path, path[1:], strict=False)
                    )
                )
            evidence_path_recall = (
                recalled_paths / len(case["expected_paths"]) if case["expected_paths"] else 1.0
            )
            expected_commits = set(case["expected_commit_ids"])
            commit_accuracy = (
                len(expected_commits & set(result_ids)) / len(expected_commits)
                if expected_commits
                else 1.0
            )
            leaked = set(case["forbidden_entity_ids"]) & set(result_ids)
            unauthorized_leakage = len(leaked) / max(1, len(case["forbidden_entity_ids"]))
            passed = (
                min(
                    source_recall,
                    entity_recall,
                    citation_completeness,
                    version_accuracy,
                    evidence_path_recall,
                    commit_accuracy,
                )
                >= 1.0
                and unauthorized_leakage == 0.0
            )
            score = {
                "id": f"evaluation-result://{uuid4().hex}",
                "evaluation_run_id": run_id,
                "case_id": case["id"],
                "passed": passed,
                "source_recall": source_recall,
                "entity_recall": entity_recall,
                "citation_completeness": citation_completeness,
                "version_accuracy": version_accuracy,
                "evidence_path_recall": evidence_path_recall,
                "commit_accuracy": commit_accuracy,
                "wrong_version_rate": wrong_version_rate,
                "unauthorized_leakage": unauthorized_leakage,
                "latency_ms": result["trace"]["duration_ms"],
                "result_entity_ids": result_ids,
                "detail": {
                    "result_sources": sorted(result_sources),
                    "direct_result_entity_ids": direct_result_ids,
                    "relation_expanded_entity_ids": [item["entity_id"] for item in related],
                    "leaked_entity_ids": sorted(leaked),
                    "recalled_paths": recalled_paths,
                },
            }
            self.store.add_result(score)
            scores.append(score)
        summary = {
            "passed": sum(item["passed"] for item in scores),
            "failed": sum(not item["passed"] for item in scores),
            "pass_rate": mean(float(item["passed"]) for item in scores),
            "source_recall": mean(item["source_recall"] for item in scores),
            "entity_recall": mean(item["entity_recall"] for item in scores),
            "citation_completeness": mean(item["citation_completeness"] for item in scores),
            "version_accuracy": mean(item["version_accuracy"] for item in scores),
            "evidence_path_recall": mean(item["evidence_path_recall"] for item in scores),
            "commit_accuracy": mean(item["commit_accuracy"] for item in scores),
            "wrong_version_rate": mean(item["wrong_version_rate"] for item in scores),
            "unauthorized_leakage": mean(item["unauthorized_leakage"] for item in scores),
            "latency_ms": mean(item["latency_ms"] for item in scores),
        }
        self.store.complete_run(run_id, summary)
        return self.store.get_run(run_id) or {}

    @staticmethod
    def _response_entity_ids(response: Any) -> list[str]:
        payload = response if isinstance(response, dict) else {}
        entity_ids: list[str] = []
        for field in ("results", "candidates"):
            values = payload.get(field)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                entity_id = item.get("entity_id") or item.get("parent_entity_id")
                if entity_id:
                    entity_ids.append(str(entity_id))
                for edge in item.get("edges") or []:
                    if isinstance(edge, dict):
                        entity_ids.extend(
                            str(value)
                            for value in (
                                edge.get("source") or edge.get("source_id"),
                                edge.get("target") or edge.get("target_id"),
                            )
                            if value
                        )
        for field in ("relations", "edges"):
            values = payload.get(field)
            if not isinstance(values, list):
                continue
            for edge in values:
                if isinstance(edge, dict):
                    entity_ids.extend(
                        str(value)
                        for value in (
                            edge.get("source") or edge.get("source_id"),
                            edge.get("target") or edge.get("target_id"),
                        )
                        if value
                    )
        return list(dict.fromkeys(entity_ids))

    @staticmethod
    def _validate_dataset_membership(
        request: CodeEvaluationRunRequest,
        enabled_cases: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        dataset_cases = [
            case
            for case in enabled_cases
            if (case.get("code_profile") or {}).get("dataset_id") == request.dataset_id
            and (case.get("code_profile") or {}).get("dataset_version") == request.dataset_version
            and (case.get("code_profile") or {}).get("dataset_package_hash") == request.package_hash
        ]
        requested_ids = set(request.case_ids)
        dataset_ids = {str(case["id"]) for case in dataset_cases}
        if requested_ids != dataset_ids:
            missing = sorted(dataset_ids - requested_ids)
            unknown = sorted(requested_ids - dataset_ids)
            detail = []
            if missing:
                detail.append("missing dataset cases: " + ", ".join(missing[:5]))
            if unknown:
                detail.append("unknown or mismatched cases: " + ", ".join(unknown[:5]))
            raise EvaluationError(
                "case_ids must exactly equal enabled dataset membership"
                + (": " + "; ".join(detail) if detail else "")
            )
        if not dataset_cases:
            raise EvaluationError("no enabled cases match the requested dataset package")
        return sorted(dataset_cases, key=lambda case: str(case["id"]))

    def _search_code(
        self,
        request: EvidenceSearchRequest,
        *,
        graph_candidate_enabled: bool,
    ) -> tuple[Any, bool]:
        evaluation_search = getattr(self.platform.code, "search_evaluation", None)
        if callable(evaluation_search):
            return (
                evaluation_search(request, graph_candidate_enabled=graph_candidate_enabled),
                True,
            )
        if graph_candidate_enabled:
            raise EvaluationError(
                "retriever does not expose an explicit graph-candidate evaluation toggle"
            )
        return self.platform.code.search(request), False

    def run_code(
        self,
        request: CodeEvaluationRunRequest,
        *,
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
    ) -> dict[str, Any]:
        """Run Code Evaluation V2 against the Code Retriever directly."""

        self.workspace._require_project(request.project_id)
        enabled_cases = self.store.list_cases(
            request.project_id,
            enabled_only=True,
        )
        cases = self._validate_dataset_membership(request, enabled_cases)
        invalid_cases = [
            case["display_key"]
            for case in cases
            if not case.get("code_profile") or case["code_profile"].get("source_domain") != "code"
        ]
        if invalid_cases:
            raise EvaluationError(
                "code evaluation requires a code_profile for every case: "
                + ", ".join(invalid_cases[:5])
            )
        caller_acl_refs = {str(item) for item in allowed_acl_refs or []}
        case_scope_contracts: dict[str, dict[str, Any]] = {}
        snapshot_repository_ids = set(request.repository_ids)
        for case in cases:
            profile = case["code_profile"]
            query_profile = profile.get("query_profile") or {}
            profile_acl_refs = {str(item) for item in query_profile.get("acl_refs") or []}
            if enforce_acl:
                effective_acl_refs = (
                    profile_acl_refs & caller_acl_refs if profile_acl_refs else caller_acl_refs
                )
            else:
                effective_acl_refs = profile_acl_refs
            repository_ids = list(query_profile.get("repository_ids") or [])
            if query_profile.get("repository_id"):
                repository_ids.append(str(query_profile["repository_id"]))
            repository_ids = list(dict.fromkeys(repository_ids or request.repository_ids))
            snapshot_repository_ids.update(repository_ids)
            case_scope_contracts[str(case["id"])] = {
                "repository_ids": repository_ids,
                "commit": (
                    query_profile.get("commit")
                    or query_profile.get("ref")
                    or query_profile.get("scope_ref")
                    or request.commit
                    or profile.get("expected_ref")
                    or case.get("required_version")
                ),
                "languages": list(
                    query_profile.get("languages") or query_profile.get("code_languages") or []
                ),
                "entity_types": list(query_profile.get("entity_types") or []),
                "allowed_acl_refs": sorted(effective_acl_refs),
                "enforce_acl": enforce_acl or bool(profile_acl_refs),
            }
        requested_membership = sorted(request.case_ids)
        baseline: dict[str, Any] | None = None
        if request.paired_graph_off_run_id:
            baseline = self.store.get_run(request.paired_graph_off_run_id)
            if not baseline:
                raise EvaluationError("paired graph-off run not found")
            if baseline["status"] != "completed" or baseline["source_domain"] != "code":
                raise EvaluationError("paired graph-off run must be a completed Code run")
            if bool(baseline.get("graph_candidate_enabled")):
                raise EvaluationError("paired baseline is not graph-off")
            if (
                baseline.get("dataset_id"),
                baseline.get("dataset_version"),
                baseline.get("dataset_package_hash"),
            ) != (request.dataset_id, request.dataset_version, request.package_hash):
                raise EvaluationError("paired run dataset identity does not match")
            if sorted(baseline.get("snapshot", {}).get("case_ids") or []) != requested_membership:
                raise EvaluationError("paired run case membership does not match")

        token = uuid4().hex
        run_id = f"evaluation-run://{request.project_id}/{token}"
        display_key = f"CODE-EVAL-{token[:8].upper()}"
        evaluator_config = {
            "runner_version": CODE_EVALUATION_RUNNER_VERSION,
            "limit_per_query": request.limit_per_query,
            "repository_ids": sorted(snapshot_repository_ids),
            "commit": request.commit,
            "case_scopes": [
                {"case_id": case_id, **scope}
                for case_id, scope in sorted(case_scope_contracts.items())
            ],
            "graph_candidate_enabled": request.graph_candidate_enabled,
            "paired_graph_off_run_id": request.paired_graph_off_run_id,
            "declared": request.config,
        }
        try:
            snapshot = self.store.code_snapshot(
                request.project_id,
                sorted(snapshot_repository_ids) or None,
                dataset_id=request.dataset_id,
                dataset_version=request.dataset_version,
                dataset_package_hash=request.package_hash,
                case_ids=requested_membership,
                evaluator_config=evaluator_config,
            )
        except RuntimeError as exc:
            raise EvaluationError(f"unable to freeze Code evaluation inputs: {exc}") from exc
        persisted_config = {
            "runner_version": CODE_EVALUATION_RUNNER_VERSION,
            "request": request.model_dump(),
            "declared": request.config,
            "observed_retriever": {},
        }
        self.store.create_run(
            run_id,
            display_key,
            request.project_id,
            len(cases),
            source_domain="code",
            runner_version=CODE_EVALUATION_RUNNER_VERSION,
            dataset_id=request.dataset_id,
            dataset_version=request.dataset_version,
            dataset_package_hash=request.package_hash,
            graph_candidate_enabled=request.graph_candidate_enabled,
            paired_graph_off_run_id=request.paired_graph_off_run_id,
            snapshot=snapshot,
            config=persisted_config,
        )

        scores: list[dict[str, Any]] = []
        evaluations: list[tuple[dict[str, Any], dict[str, Any]]] = []
        observed_config: dict[str, set[str]] = {}
        observed_generations: set[str] = set()
        toggle_observations: set[bool] = set()
        try:
            expected_entity_ids = list(
                dict.fromkeys(
                    entity_id for case in cases for entity_id in case.get("expected_entity_ids", [])
                )
            )
            entity_types = self.store.entity_types(expected_entity_ids)
            for case in cases:
                case_scope = case_scope_contracts[str(case["id"])]
                search_request = EvidenceSearchRequest(
                    query=case["question"],
                    scope=SearchScope(
                        project_id=request.project_id,
                        **case_scope,
                    ),
                    limit=request.limit_per_query,
                    include_edges=True,
                )
                started = time.perf_counter()
                response, explicit_toggle = self._search_code(
                    search_request,
                    graph_candidate_enabled=request.graph_candidate_enabled,
                )
                measured_latency_ms = (time.perf_counter() - started) * 1000
                entity_acl_refs = self.store.entity_acl_refs(self._response_entity_ids(response))
                evaluated = evaluate_code_case(
                    run_id,
                    case,
                    response,
                    limit=request.limit_per_query,
                    entity_types=entity_types,
                    entity_acl_refs=entity_acl_refs,
                    allowed_acl_refs=case_scope["allowed_acl_refs"],
                    enforce_acl=case_scope["enforce_acl"],
                    measured_latency_ms=measured_latency_ms,
                )
                if explicit_toggle:
                    toggle_observations.add(request.graph_candidate_enabled)
                legacy = evaluated["legacy_projection"]
                latency_metric = next(
                    item
                    for item in evaluated["metrics"]
                    if item["metric_name"] == "latency_ms" and not item["slice"]
                )
                latency_ms = (
                    float(latency_metric["value"])
                    if latency_metric["status"] == "available"
                    else measured_latency_ms
                )
                score = {
                    "id": f"evaluation-result://{uuid4().hex}",
                    "evaluation_run_id": run_id,
                    "case_id": case["id"],
                    "passed": evaluated["passed"],
                    **legacy,
                    "latency_ms": latency_ms,
                    "result_entity_ids": evaluated["result_entity_ids"],
                    "detail": evaluated["detail"],
                }
                scores.append(score)
                evaluations.append((case, evaluated))

                trace = evaluated["detail"]["trace"]
                for key, value in trace.items():
                    if value is None or isinstance(value, (dict, list)):
                        continue
                    if any(
                        token in key.casefold()
                        for token in ("model", "profile", "fusion", "reranker", "version")
                    ):
                        observed_config.setdefault(key, set()).add(str(value))
                observed_generations.update(evaluated["detail"]["index_generation"])

            observed_retriever: dict[str, Any] = {
                key: sorted(values) for key, values in sorted(observed_config.items())
            }
            if toggle_observations:
                if toggle_observations != {request.graph_candidate_enabled}:
                    raise EvaluationError("retriever graph toggle observation was inconsistent")
                observed_retriever["graph_candidate_enabled"] = request.graph_candidate_enabled
            else:
                observed_retriever["graph_candidate_enabled"] = "unavailable"
            snapshot = self.store.finalize_code_snapshot(
                snapshot,
                observed_index_generations=sorted(observed_generations),
                observed_retriever=observed_retriever,
            )
            persisted_config["observed_retriever"] = observed_retriever

            if baseline:
                baseline_observed = (
                    baseline.get("snapshot", {})
                    .get("observed_retriever", {})
                    .get("graph_candidate_enabled")
                )
                if baseline_observed is not False:
                    raise EvaluationError("paired baseline lacks an observed graph-off capability")
                if observed_retriever.get("graph_candidate_enabled") is not True:
                    raise EvaluationError("paired candidate lacks an observed graph-on capability")
                if baseline.get("snapshot", {}).get("comparison_fingerprint") != snapshot.get(
                    "comparison_fingerprint"
                ):
                    raise EvaluationError(
                        "paired graph runs do not share the same immutable snapshot/config"
                    )
                baseline_results = {
                    str(item["case_id"]): item for item in baseline.get("results") or []
                }
                for case, evaluated in evaluations:
                    baseline_result = baseline_results.get(str(case["id"]))
                    if not baseline_result:
                        raise EvaluationError("paired graph-off run is missing a case result")
                    current_keys = set(evaluated["detail"].get("relevant_target_keys_at_10") or [])
                    baseline_keys = set(
                        baseline_result.get("detail", {}).get("relevant_target_keys_at_10") or []
                    )
                    apply_paired_graph_recovery(
                        evaluated["metrics"],
                        run_id=run_id,
                        case_id=case["id"],
                        recovered_target_keys=current_keys - baseline_keys,
                        ideal_target_keys=set(
                            evaluated["detail"].get("ideal_relevant_target_keys") or []
                        ),
                    )

            case_metric_groups = [(case, evaluated["metrics"]) for case, evaluated in evaluations]
            aggregate_metrics = aggregate_code_metrics(run_id, case_metric_groups)
            self.store.update_run_context(run_id, snapshot=snapshot, config=persisted_config)
            for score, (_, evaluated) in zip(scores, evaluations, strict=True):
                self.store.add_result(score)
                self.store.add_metric_values(evaluated["metrics"])
            self.store.add_metric_values(aggregate_metrics)
            overall_metrics = {
                item["metric_name"]: {
                    "value": item["value"],
                    "numerator": item.get("numerator"),
                    "denominator": item.get("denominator"),
                    "total_cases": item.get("total_cases"),
                    "eligible_cases": item.get("eligible_cases"),
                    "available_cases": item.get("available_cases"),
                    "unavailable_cases": item.get("unavailable_cases"),
                }
                for item in aggregate_metrics
                if item["status"] == "available" and item["slice"] == {"scope": "overall"}
            }
            unavailable_metrics = sorted(
                item["metric_name"]
                for item in aggregate_metrics
                if item["status"] == "unavailable" and item["slice"] == {"scope": "overall"}
            )
            summary = {
                "passed": sum(item["passed"] for item in scores),
                "failed": sum(not item["passed"] for item in scores),
                "pass_rate": mean(float(item["passed"]) for item in scores),
                "source_domain": "code",
                "runner_version": CODE_EVALUATION_RUNNER_VERSION,
                "metrics": overall_metrics,
                "unavailable_metrics": unavailable_metrics,
            }
            self.store.complete_run(run_id, summary)
            return self.store.get_run(run_id) or {}
        except Exception as exc:
            self.store.fail_run(run_id, exc)
            if isinstance(exc, EvaluationError):
                raise
            raise EvaluationError(
                f"code evaluation runner failed: {type(exc).__name__}: {exc}"
            ) from exc
