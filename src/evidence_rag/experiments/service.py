from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from ..security import redact_secrets
from ..sources.models import SourceEventInput
from ..sources.service import RawSourceService
from ..storage import utc_now
from ..workspace.models import IterationLinkCreate, RelationCreate
from ..workspace.service import WorkspaceService
from .adapters import MLflowAdapter, MLflowAdapterError
from .models import (
    ComparisonRequest,
    ExperimentCreate,
    ExperimentUpdate,
    MLflowSyncRequest,
    RunCreate,
    RunUpdate,
)
from .store import ExperimentStore


class ExperimentError(ValueError):
    pass


class ExperimentService:
    def __init__(
        self,
        store: ExperimentStore,
        workspace: WorkspaceService,
        sources: RawSourceService | None = None,
        *,
        mlflow_allowed_http_hosts: tuple[str, ...] | None = None,
        mlflow_allowed_local_roots: tuple[Path | str, ...] | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.sources = sources
        self.mlflow_allowed_http_hosts = (
            mlflow_allowed_http_hosts
            if mlflow_allowed_http_hosts is not None
            else self._environment_values("RAG_MLFLOW_ALLOWED_HTTP_HOSTS")
        )
        self.mlflow_allowed_local_roots = (
            mlflow_allowed_local_roots
            if mlflow_allowed_local_roots is not None
            else self._environment_values("RAG_MLFLOW_ALLOWED_LOCAL_ROOTS")
        )

    def sync_mlflow(self, request: MLflowSyncRequest) -> dict[str, Any]:
        self.workspace._require_project(request.project_id)
        if self.sources is None:
            raise ExperimentError("raw source service is unavailable")
        source_token = hashlib.sha256(
            f"{request.project_id}\x1f{request.tracking_uri}".encode()
        ).hexdigest()
        source_id = f"experiment-source://mlflow/{source_token}"
        self.store.upsert_source(
            {
                "id": source_id,
                "project_id": request.project_id,
                "adapter_type": "mlflow",
                "tracking_uri": request.tracking_uri,
                "status": "syncing",
                "stats": {},
            }
        )
        try:
            adapter = MLflowAdapter(
                request.tracking_uri,
                allowed_http_hosts=self.mlflow_allowed_http_hosts,
                allowed_local_roots=self.mlflow_allowed_local_roots,
            )
            external_experiments, external_runs = adapter.load(
                request.experiment_ids, request.max_runs
            )
            mappings: dict[str, str] = {}
            for external in external_experiments:
                external_id = external["external_id"]
                experiment_id = self.store.source_mapping(source_id, external_id)
                deleted = str(external.get("lifecycle_stage") or "").casefold() == "deleted"
                if not experiment_id:
                    experiment = self.create_experiment(
                        ExperimentCreate(
                            project_id=request.project_id,
                            title=external["name"],
                            objective="Imported from MLflow tracking metadata.",
                            owner="MLflow",
                            status="archived" if deleted else "running",
                            tags=["mlflow", f"mlflow-experiment:{external_id}"],
                        )
                    )
                    experiment_id = experiment["id"]
                self.store.map_experiment(source_id, external_id, experiment_id, external)
                if deleted:
                    self.store.update_experiment(experiment_id, {"status": "deleted"})
                mappings[external_id] = experiment_id

            imported = 0
            updated = 0
            version_bound = 0
            metric_count = 0
            artifact_count = 0
            for external in external_runs:
                experiment_id = mappings.get(external["external_experiment_id"])
                if not experiment_id:
                    continue
                sanitized = self._sanitized(external)
                repository_id = request.repository_id or self.store.find_repository(
                    request.project_id, sanitized.get("repository_url")
                )
                commit_sha = sanitized.get("commit_sha")
                commit_entity_id = (
                    self.store.resolve_commit(repository_id, commit_sha)
                    if repository_id and commit_sha
                    else None
                )
                digest = hashlib.sha256(
                    f"{source_id}\x1f{sanitized['external_id']}".encode()
                ).hexdigest()
                run_id = f"run://{request.project_id}/mlflow/{digest}"
                metrics = []
                for metric in sanitized.get("metrics", []):
                    metric_token = hashlib.sha256(
                        f"{run_id}\x1f{metric['name']}\x1f{metric.get('split')}\x1f{metric.get('step')}".encode()
                    ).hexdigest()
                    metrics.append({**metric, "id": f"metric://sha256:{metric_token}"})
                artifacts = list(sanitized.get("artifacts", []))
                if sanitized.get("artifact_uri") and not artifacts:
                    artifacts.append(
                        {
                            "name": "MLflow artifact root",
                            "uri": sanitized["artifact_uri"],
                            "kind": "artifact_root",
                            "metadata": {},
                        }
                    )
                normalized_artifacts = []
                for artifact in artifacts:
                    artifact_token = hashlib.sha256(
                        f"{run_id}\x1f{artifact['name']}\x1f{artifact['uri']}".encode()
                    ).hexdigest()
                    normalized_artifacts.append(
                        {
                            **artifact,
                            "id": f"artifact://sha256:{artifact_token}",
                            "kind": artifact.get("kind", "artifact"),
                        }
                    )
                record = {
                    **sanitized,
                    "id": run_id,
                    "display_key": f"RUN-{digest[:8].upper()}",
                    "project_id": request.project_id,
                    "experiment_id": experiment_id,
                    "repository_id": repository_id,
                    "commit_entity_id": commit_entity_id,
                    "metrics": metrics,
                    "artifacts": normalized_artifacts,
                    "observed_version": self._run_version(sanitized),
                }
                run, created = self.store.upsert_imported_run(record)
                imported += int(created)
                updated += int(not created)
                version_bound += int(bool(commit_entity_id))
                metric_count += len(metrics)
                artifact_count += len(normalized_artifacts)
                raw_result = self.sources.accept(
                    SourceEventInput(
                        source_type="mlflow",
                        source_instance=request.tracking_uri,
                        event_type=(
                            "run.deleted"
                            if sanitized.get("status") == "deleted"
                            else "run.snapshot.observed"
                        ),
                        source_object_id=sanitized["external_id"],
                        source_version=self._run_version(sanitized),
                        event_time=sanitized.get("completed_at") or sanitized.get("started_at"),
                        project_id=request.project_id,
                        acl_ref=request.acl_ref,
                        source_uri=(
                            f"{request.tracking_uri}#/experiments/"
                            f"{external['external_experiment_id']}/runs/{external['external_id']}"
                        ),
                        payload=sanitized,
                        adapter_version=adapter.adapter_version,
                        schema_version="mlflow-run-v2",
                        metadata={"experiment_id": external["external_experiment_id"]},
                    )
                )
                self.sources.store.link_derivations(
                    raw_result["raw_object"]["id"],
                    [
                        run["id"],
                        *[item["id"] for item in run.get("metrics", [])],
                        *[item["id"] for item in run.get("artifacts", [])],
                    ],
                    kind="mlflow_normalization",
                    generation_id=None,
                    derivation_version=adapter.adapter_version,
                )
                if commit_entity_id:
                    self._ensure_run_commit_relation(run, commit_entity_id, commit_sha)
                self._ensure_run_metric_relations(run, run.get("metrics", []))
            stats = {
                "experiments": len(external_experiments),
                "runs": len(external_runs),
                "imported": imported,
                "updated": updated,
                "version_bound": version_bound,
                "metrics": metric_count,
                "artifacts": artifact_count,
            }
            source = self.store.upsert_source(
                {
                    "id": source_id,
                    "project_id": request.project_id,
                    "adapter_type": "mlflow",
                    "tracking_uri": request.tracking_uri,
                    "status": "ready",
                    "stats": stats,
                }
            )
            return {"source": source, "stats": stats}
        except (MLflowAdapterError, OSError, httpx.HTTPError, ValueError) as exc:
            self.store.upsert_source(
                {
                    "id": source_id,
                    "project_id": request.project_id,
                    "adapter_type": "mlflow",
                    "tracking_uri": request.tracking_uri,
                    "status": "failed",
                    "stats": {},
                    "last_error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise ExperimentError(f"MLflow sync failed: {exc}") from exc

    def _ensure_run_commit_relation(
        self, run: dict[str, Any], commit_entity_id: str, commit_sha: str | None
    ) -> None:
        existing = self.workspace.store.list_relations(
            run["project_id"], entity_id=run["id"], limit=200
        )
        if any(
            item["predicate"] == "uses" and item["target_entity_id"] == commit_entity_id
            for item in existing
        ):
            return
        self.workspace.create_relation(
            RelationCreate(
                project_id=run["project_id"],
                source_entity_id=run["id"],
                predicate="uses",
                target_entity_id=commit_entity_id,
                evidence_entity_id=run["id"],
                derivation="deterministic",
                confidence=1.0,
                review_status="confirmed",
                rule_version="mlflow-git-tag-v1",
                metadata={"commit_sha": commit_sha},
            )
        )

    def _ensure_run_metric_relations(
        self, run: dict[str, Any], metrics: list[dict[str, Any]]
    ) -> None:
        existing = self.workspace.store.list_relations(
            run["project_id"], entity_id=run["id"], limit=1000
        )
        linked = {
            item["target_entity_id"]
            for item in existing
            if item["source_entity_id"] == run["id"] and item["predicate"] == "reports"
        }
        for metric in metrics:
            if metric["id"] in linked:
                continue
            self.workspace.create_relation(
                RelationCreate(
                    project_id=run["project_id"],
                    source_entity_id=run["id"],
                    predicate="reports",
                    target_entity_id=metric["id"],
                    evidence_entity_id=metric["id"],
                    derivation="deterministic",
                    confidence=1.0,
                    review_status="confirmed",
                    rule_version="run-metric-record-v1",
                    metadata={"metric_name": metric["name"]},
                )
            )

    @staticmethod
    def _sanitized(value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(value, ensure_ascii=False)
        redacted, _findings = redact_secrets(encoded)
        return json.loads(redacted)

    @staticmethod
    def _run_version(run: dict[str, Any]) -> str:
        stable = json.dumps(run, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(stable.encode()).hexdigest()

    @staticmethod
    def _environment_values(name: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(item.strip() for item in os.getenv(name, "").split(",") if item.strip())
        )

    def create_experiment(self, request: ExperimentCreate) -> dict[str, Any]:
        self.workspace._require_project(request.project_id)
        if request.iteration_id:
            iteration = self.workspace._require_iteration(request.iteration_id)
            if iteration["project_id"] != request.project_id:
                raise ExperimentError("iteration does not belong to project")
        token = uuid4().hex
        record = request.model_dump()
        record.update(
            id=f"experiment://{request.project_id}/{token}",
            display_key=f"EXP-{token[:8].upper()}",
        )
        result = self.store.create_experiment(record)
        if request.iteration_id:
            self.workspace.link_iteration(
                IterationLinkCreate(
                    iteration_id=request.iteration_id,
                    entity_id=result["id"],
                    entity_type="Experiment",
                    source_type="experiment",
                    role="validation_plan",
                )
            )
        return result

    def update_experiment(self, experiment_id: str, request: ExperimentUpdate) -> dict[str, Any]:
        self._require_experiment(experiment_id)
        return (
            self.store.update_experiment(experiment_id, request.model_dump(exclude_none=True)) or {}
        )

    def create_run(self, request: RunCreate) -> dict[str, Any]:
        experiment = self._require_experiment(request.experiment_id)
        commit_entity_id = None
        if request.commit_sha and request.repository_id:
            commit_entity_id = self.store.resolve_commit(request.repository_id, request.commit_sha)
        token = uuid4().hex
        record = request.model_dump()
        record.update(
            id=f"run://{experiment['project_id']}/{token}",
            display_key=f"RUN-{token[:8].upper()}",
            project_id=experiment["project_id"],
            commit_entity_id=commit_entity_id,
        )
        if request.status == "completed" and not request.completed_at:
            record["completed_at"] = utc_now()
        for metric in record["metrics"]:
            metric["id"] = f"metric://{token}/{uuid4().hex}"
        for artifact in record["artifacts"]:
            artifact["id"] = f"artifact://{token}/{uuid4().hex}"
        result = self.store.create_run(record)
        if commit_entity_id:
            self.workspace.create_relation(
                RelationCreate(
                    project_id=experiment["project_id"],
                    source_entity_id=result["id"],
                    predicate="uses",
                    target_entity_id=commit_entity_id,
                    evidence_entity_id=result["id"],
                    derivation="deterministic",
                    confidence=1.0,
                    review_status="confirmed",
                    rule_version="run-commit-tag-v1",
                    metadata={
                        "commit_sha": request.commit_sha,
                        "repository_id": request.repository_id,
                    },
                )
            )
        self._ensure_run_metric_relations(result, result["metrics"])
        return result

    def update_run(self, run_id: str, request: RunUpdate) -> dict[str, Any]:
        self._require_run(run_id)
        changes = request.model_dump(exclude_none=True)
        if changes.get("status") == "completed" and not changes.get("completed_at"):
            changes["completed_at"] = utc_now()
        return self.store.update_run(run_id, changes) or {}

    def compare(self, request: ComparisonRequest) -> dict[str, Any]:
        runs = [self._require_run(run_id) for run_id in request.run_ids]
        experiment_ids = {run["experiment_id"] for run in runs}
        if len(experiment_ids) != 1:
            raise ExperimentError("all compared runs must belong to the same experiment")
        baseline_id = request.baseline_run_id or request.run_ids[0]
        baseline = next((run for run in runs if run["id"] == baseline_id), None)
        if not baseline:
            raise ExperimentError("baseline run must be included in run_ids")
        candidates = [run for run in runs if run["id"] != baseline_id]
        result = {
            "controls": self._controls(runs),
            "config_differences": self._config_differences(baseline, candidates),
            "metrics": self._metric_differences(baseline, candidates),
            "version_complete": all(run.get("commit_entity_id") for run in runs),
        }
        token = uuid4().hex
        record = {
            "id": f"comparison://{runs[0]['project_id']}/{token}",
            "display_key": f"CMP-{token[:8].upper()}",
            "project_id": runs[0]["project_id"],
            "experiment_id": runs[0]["experiment_id"],
            "name": request.name,
            "baseline_run_id": baseline_id,
            "candidate_run_ids": [run["id"] for run in candidates],
            "result": result,
        }
        saved = self.store.save_comparison(record)
        return {**saved, "runs": runs}

    def _require_experiment(self, experiment_id: str) -> dict[str, Any]:
        item = self.store.get_experiment(experiment_id)
        if not item:
            raise ExperimentError("experiment not found")
        return item

    def _require_run(self, run_id: str) -> dict[str, Any]:
        item = self.store.get_run(run_id)
        if not item:
            raise ExperimentError("experiment run not found")
        return item

    @staticmethod
    def _controls(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fields = ("dataset_id", "dataset_version", "branch")
        controls = []
        for field in fields:
            values = [run.get(field) for run in runs]
            controls.append({"field": field, "consistent": len(set(values)) == 1, "values": values})
        environments = [run.get("environment", {}) for run in runs]
        controls.append(
            {
                "field": "environment",
                "consistent": all(value == environments[0] for value in environments[1:]),
                "values": environments,
            }
        )
        return controls

    @staticmethod
    def _config_differences(
        baseline: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        keys = set(baseline["config"])
        for candidate in candidates:
            keys.update(candidate["config"])
        return [
            {
                "key": key,
                "baseline": baseline["config"].get(key),
                "candidates": [run["config"].get(key) for run in candidates],
            }
            for key in sorted(keys)
            if any(run["config"].get(key) != baseline["config"].get(key) for run in candidates)
        ]

    @staticmethod
    def _metric_differences(
        baseline: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        def key(metric: dict[str, Any]) -> tuple[str, str | None]:
            return metric["name"], metric.get("split")

        baseline_metrics = {key(metric): metric for metric in baseline["metrics"]}
        candidate_metrics: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
        for run in candidates:
            for metric in run["metrics"]:
                candidate_metrics[key(metric)].append({**metric, "run_id": run["id"]})
        rows = []
        for metric_key, base in baseline_metrics.items():
            values = candidate_metrics.get(metric_key, [])
            rows.append(
                {
                    "name": metric_key[0],
                    "split": metric_key[1],
                    "baseline": base["value"],
                    "higher_is_better": (
                        bool(base["higher_is_better"])
                        if base.get("higher_is_better") is not None
                        else None
                    ),
                    "candidates": [
                        {
                            "run_id": value["run_id"],
                            "value": value["value"],
                            "delta": value["value"] - base["value"],
                        }
                        for value in values
                    ],
                }
            )
        return rows
