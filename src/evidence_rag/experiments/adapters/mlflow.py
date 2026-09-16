from __future__ import annotations

import ipaddress
import mimetypes
import os
import socket
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
import yaml


class MLflowAdapterError(ValueError):
    pass


class MLflowAdapter:
    adapter_version = "mlflow-tracking-v2"

    def __init__(
        self,
        tracking_uri: str,
        *,
        timeout: float = 30.0,
        allowed_http_hosts: Iterable[str] = (),
        allowed_local_roots: Iterable[Path | str] = (),
    ) -> None:
        self.tracking_uri = tracking_uri.rstrip("/")
        parsed = urlparse(tracking_uri)
        self.is_http = parsed.scheme in {"http", "https"}
        self.timeout = timeout
        self._allowed_http_hosts = frozenset(
            item.strip().casefold() for item in allowed_http_hosts if item.strip()
        )
        self._allowed_local_roots = tuple(allowed_local_roots)
        if self.is_http:
            self._validate_http_target(self.tracking_uri)
            self.root = None
        else:
            if parsed.scheme not in {"", "file"}:
                raise MLflowAdapterError("MLflow tracking URI scheme is not allowed")
            if parsed.scheme == "file" and (parsed.netloc or parsed.query or parsed.fragment):
                raise MLflowAdapterError("MLflow FileStore URI must be a local absolute path")
            value = unquote(parsed.path) if parsed.scheme == "file" else tracking_uri
            self.root = self._validate_local_root(value)

    def load(
        self, experiment_ids: list[str], max_runs: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self.is_http:
            return self._load_rest(experiment_ids, max_runs)
        return self._load_files(experiment_ids, max_runs)

    def _load_files(
        self, selected: list[str], max_runs: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        assert self.root is not None
        experiments = []
        runs = []
        selected_set = set(selected)
        for directory in sorted(self.root.iterdir()):
            self._validate_local_path(directory)
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            meta = self._yaml(directory / "meta.yaml")
            external_id = str(meta.get("experiment_id") or directory.name)
            if selected_set and external_id not in selected_set:
                continue
            experiment = {
                "external_id": external_id,
                "name": str(meta.get("name") or f"MLflow experiment {external_id}"),
                "lifecycle_stage": str(meta.get("lifecycle_stage") or "active"),
                "artifact_location": meta.get("artifact_location"),
                "tags": {},
            }
            experiments.append(experiment)
            for run_dir in sorted(directory.iterdir()):
                if len(runs) >= max_runs:
                    break
                self._validate_local_path(run_dir)
                run_meta = run_dir / "meta.yaml"
                self._validate_local_path(run_meta, allow_missing=True)
                if not run_dir.is_dir() or not run_meta.is_file():
                    continue
                run = self._file_run(external_id, run_dir)
                if run:
                    runs.append(run)
            if len(runs) >= max_runs:
                break
        return experiments, runs

    def _file_run(self, experiment_id: str, directory: Path) -> dict[str, Any] | None:
        self._validate_local_path(directory)
        meta = self._yaml(directory / "meta.yaml")
        run_id = str(meta.get("run_id") or meta.get("run_uuid") or directory.name)
        if not run_id:
            return None
        tags = self._key_values(directory / "tags")
        params = self._key_values(directory / "params")
        metrics = []
        metrics_dir = directory / "metrics"
        self._validate_local_path(metrics_dir, allow_missing=True)
        if metrics_dir.is_dir():
            for path in sorted(item for item in metrics_dir.rglob("*") if item.is_file()):
                self._validate_local_path(path)
                for observation in self._metric_history(path):
                    metrics.append(
                        {
                            "name": path.relative_to(metrics_dir).as_posix(),
                            **observation,
                        }
                    )
        artifacts = []
        artifact_dir = directory / "artifacts"
        self._validate_local_path(artifact_dir, allow_missing=True)
        if artifact_dir.is_dir():
            for path in sorted(item for item in artifact_dir.rglob("*") if item.is_file()):
                self._validate_local_path(path)
                artifacts.append(
                    {
                        "name": path.relative_to(artifact_dir).as_posix(),
                        "uri": path.as_uri(),
                        "kind": "artifact",
                        "media_type": mimetypes.guess_type(path.name)[0],
                        "metadata": {"size": path.stat().st_size},
                    }
                )
        return self._normalize_run(
            experiment_id,
            {
                "run_id": run_id,
                "run_name": tags.get("mlflow.runName") or meta.get("run_name"),
                "status": meta.get("status"),
                "lifecycle_stage": meta.get("lifecycle_stage"),
                "start_time": meta.get("start_time"),
                "end_time": meta.get("end_time"),
                "artifact_uri": meta.get("artifact_uri"),
                "params": params,
                "metrics": metrics,
                "tags": tags,
                "artifacts": artifacts,
            },
        )

    def _load_rest(
        self, selected: list[str], max_runs: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with httpx.Client(
            base_url=self.tracking_uri,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = self._request(
                client,
                "POST",
                "/api/2.0/mlflow/experiments/search",
                json={"max_results": 1000},
            )
            raw_experiments = response.json().get("experiments", [])
            selected_set = set(selected)
            experiments = [
                {
                    "external_id": str(item["experiment_id"]),
                    "name": item.get("name") or f"MLflow experiment {item['experiment_id']}",
                    "lifecycle_stage": item.get("lifecycle_stage", "active"),
                    "artifact_location": item.get("artifact_location"),
                    "tags": self._pairs(item.get("tags", [])),
                }
                for item in raw_experiments
                if not selected_set or str(item["experiment_id"]) in selected_set
            ]
            runs = []
            ids = [item["external_id"] for item in experiments]
            token = None
            while ids and len(runs) < max_runs:
                payload: dict[str, Any] = {
                    "experiment_ids": ids,
                    "max_results": min(1000, max_runs - len(runs)),
                    "order_by": ["attributes.start_time DESC"],
                }
                if token:
                    payload["page_token"] = token
                response = self._request(
                    client,
                    "POST",
                    "/api/2.0/mlflow/runs/search",
                    json=payload,
                )
                data = response.json()
                for item in data.get("runs", []):
                    info = item.get("info", {})
                    summary_metrics = item.get("data", {}).get("metrics", [])
                    record = {
                        "run_id": info.get("run_id"),
                        "run_name": info.get("run_name"),
                        "status": info.get("status"),
                        "lifecycle_stage": info.get("lifecycle_stage"),
                        "start_time": info.get("start_time"),
                        "end_time": info.get("end_time"),
                        "artifact_uri": info.get("artifact_uri"),
                        "params": self._pairs(item.get("data", {}).get("params", [])),
                        "metrics": self._rest_metric_history(
                            client,
                            str(info.get("run_id") or ""),
                            summary_metrics,
                        ),
                        "tags": self._pairs(item.get("data", {}).get("tags", [])),
                        "artifacts": [],
                    }
                    runs.append(self._normalize_run(str(info.get("experiment_id")), record))
                token = data.get("next_page_token")
                if not token:
                    break
        return experiments, runs

    def _normalize_run(self, experiment_id: str, item: dict[str, Any]) -> dict[str, Any]:
        tags = item.get("tags", {})
        lifecycle_stage = str(item.get("lifecycle_stage") or "active").casefold()
        status = "deleted" if lifecycle_stage == "deleted" else self._status(item.get("status"))
        return {
            "external_id": str(item["run_id"]),
            "external_experiment_id": experiment_id,
            "name": str(item.get("run_name") or tags.get("mlflow.runName") or item["run_id"]),
            "status": status,
            "repository_url": tags.get("mlflow.source.git.repoURL") or tags.get("git.repository"),
            "commit_sha": tags.get("mlflow.source.git.commit") or tags.get("git.commit"),
            "branch": tags.get("git.branch"),
            "dataset_id": tags.get("dataset_id") or tags.get("dataset.name"),
            "dataset_version": tags.get("dataset_version") or tags.get("dataset.digest"),
            "config": item.get("params", {}),
            "environment": {
                key: value
                for key, value in tags.items()
                if key.startswith(("environment.", "container.", "hardware."))
            },
            "command": tags.get("mlflow.project.entryPoint")
            or tags.get("mlflow.source.name")
            or "",
            "started_at": self._time(item.get("start_time")),
            "completed_at": self._time(item.get("end_time")),
            "metrics": [
                {
                    "name": metric["name"],
                    "value": float(metric["value"]),
                    "step": metric.get("step"),
                    "observed_at": self._time(metric.get("timestamp")),
                    "unit": None,
                    "split": None,
                    "higher_is_better": None,
                }
                for metric in item.get("metrics", [])
                if metric.get("name") and metric.get("value") is not None
            ],
            "artifacts": item.get("artifacts", []),
            "tags": {str(key): str(value) for key, value in tags.items()},
            "artifact_uri": item.get("artifact_uri"),
            "lifecycle_stage": lifecycle_stage,
        }

    def _yaml(self, path: Path) -> dict[str, Any]:
        self._validate_local_path(path, allow_missing=True)
        if not path.is_file():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _key_values(self, directory: Path) -> dict[str, str]:
        self._validate_local_path(directory, allow_missing=True)
        if not directory.is_dir():
            return {}
        values: dict[str, str] = {}
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            self._validate_local_path(path)
            values[path.relative_to(directory).as_posix()] = path.read_text(
                encoding="utf-8"
            ).strip()
        return values

    @staticmethod
    def _metric_history(path: Path) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) < 2:
                continue
            try:
                observations.append(
                    {
                        "timestamp": int(fields[0]),
                        "value": float(fields[1]),
                        "step": int(fields[2]) if len(fields) > 2 else None,
                    }
                )
            except ValueError:
                continue
        return observations

    def _rest_metric_history(
        self,
        client: httpx.Client,
        run_id: str,
        summary_metrics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not run_id:
            raise MLflowAdapterError("MLflow run is missing run_id")
        observations: list[dict[str, Any]] = []
        keys = sorted(
            {
                str(metric.get("key"))
                for metric in summary_metrics
                if metric.get("key") not in (None, "")
            }
        )
        for key in keys:
            token: str | None = None
            found = 0
            while True:
                params: dict[str, Any] = {"run_id": run_id, "metric_key": key}
                if token:
                    params["page_token"] = token
                response = self._request(
                    client,
                    "GET",
                    "/api/2.0/mlflow/metrics/get-history",
                    params=params,
                )
                data = response.json()
                history = data.get("metrics", [])
                for metric in history:
                    if metric.get("value") is None:
                        continue
                    observations.append(
                        {
                            "name": str(metric.get("key") or key),
                            "value": metric["value"],
                            "step": metric.get("step"),
                            "timestamp": metric.get("timestamp"),
                        }
                    )
                    found += 1
                token = data.get("next_page_token")
                if not token:
                    break
            if found == 0:
                raise MLflowAdapterError(
                    f"MLflow metric history unavailable for run={run_id!r}, metric={key!r}"
                )
        return observations

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        self._validate_http_target(self.tracking_uri)
        response = client.request(method, path, **kwargs)
        if response.is_redirect:
            raise MLflowAdapterError("MLflow redirects are forbidden")
        response.raise_for_status()
        return response

    def _validate_http_target(self, value: str) -> None:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise MLflowAdapterError("MLflow HTTP tracking URI is invalid")
        host = parsed.hostname.encode("idna").decode("ascii").casefold()
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise MLflowAdapterError("MLflow HTTP tracking URI port is invalid") from exc
        candidates = {
            host,
            f"{host}:{port}",
            f"{parsed.scheme}://{host}",
            f"{parsed.scheme}://{host}:{port}",
        }
        if not self._allowed_http_hosts.intersection(candidates):
            raise MLflowAdapterError("MLflow HTTP host is not explicitly allowed")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if not literal.is_global:
                raise MLflowAdapterError("MLflow HTTP host resolves to a non-public address")
            return
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(
                    host,
                    port,
                    type=socket.SOCK_STREAM,
                )
            }
        except OSError as exc:
            raise MLflowAdapterError("MLflow HTTP host resolution failed") from exc
        if not addresses:
            raise MLflowAdapterError("MLflow HTTP host did not resolve")
        for address in addresses:
            try:
                resolved = ipaddress.ip_address(address)
            except ValueError as exc:
                raise MLflowAdapterError("MLflow HTTP host resolution was invalid") from exc
            if not resolved.is_global:
                raise MLflowAdapterError("MLflow HTTP host resolves to a non-public address")

    def _validate_local_root(self, value: str) -> Path:
        if not self._allowed_local_roots:
            raise MLflowAdapterError("MLflow local FileStore roots are not configured")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise MLflowAdapterError("MLflow FileStore path must be absolute")
        candidate_lexical = Path(os.path.abspath(candidate))
        for configured in self._allowed_local_roots:
            root_lexical = Path(os.path.abspath(Path(configured).expanduser()))
            if not root_lexical.is_absolute() or root_lexical.is_symlink():
                continue
            try:
                candidate_lexical.relative_to(root_lexical)
            except ValueError:
                continue
            try:
                root = root_lexical.resolve(strict=True)
                resolved = candidate_lexical.resolve(strict=True)
                if root != root_lexical:
                    continue
                resolved.relative_to(root)
            except (FileNotFoundError, OSError, ValueError):
                continue
            if not resolved.is_dir():
                raise MLflowAdapterError(f"MLflow FileStore not found: {resolved}")
            self.root = root
            self._validate_local_path(candidate_lexical)
            return resolved
        raise MLflowAdapterError("MLflow FileStore is outside explicitly allowed roots")

    def _validate_local_path(self, path: Path, *, allow_missing: bool = False) -> None:
        root = getattr(self, "root", None)
        if root is None:
            return
        lexical = Path(os.path.abspath(path))
        try:
            relative = lexical.relative_to(root)
        except ValueError as exc:
            raise MLflowAdapterError("MLflow FileStore path escaped its allowed root") from exc
        current = root
        for part in relative.parts:
            current = current / part
            if not current.exists() and allow_missing:
                return
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                if allow_missing:
                    return
                raise MLflowAdapterError("MLflow FileStore path disappeared") from None
            if current.is_symlink():
                raise MLflowAdapterError("MLflow FileStore symlinks are forbidden")
            if not metadata:
                raise MLflowAdapterError("MLflow FileStore path is invalid")
        try:
            resolved = lexical.resolve(strict=not allow_missing)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            if allow_missing and not lexical.exists():
                return
            raise MLflowAdapterError("MLflow FileStore path escaped its allowed root") from exc

    @staticmethod
    def _pairs(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            str(item.get("key")): item.get("value") for item in items if item.get("key") is not None
        }

    @staticmethod
    def _time(value: Any) -> str | None:
        if value in (None, ""):
            return None
        try:
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000
            return datetime.fromtimestamp(number, tz=UTC).isoformat()
        except (TypeError, ValueError, OSError):
            return str(value)

    @staticmethod
    def _status(value: Any) -> str:
        mapping = {
            "1": "running",
            "2": "running",
            "3": "completed",
            "4": "failed",
            "5": "cancelled",
            "RUNNING": "running",
            "SCHEDULED": "queued",
            "FINISHED": "completed",
            "FAILED": "failed",
            "KILLED": "cancelled",
        }
        return mapping.get(str(value).upper(), "completed")
