"""Lazy production facades for the five non-Code source pipelines.

The formal application database remains the authority.  The source-specific
V2 stores are disposable, process-local derived indexes created only after an
explicit V2 request.  Omitting a V2 override therefore performs no V2 I/O.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import threading
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..documents.store import DocumentStore
from ..experiments.service import ExperimentService
from ..notebooks.store import NotebookStore
from ..sources.service import RawSourceService
from ..storage import SQLiteStore
from ..workspace.service import WorkspaceService
from ..workspace.store import WorkspaceStore
from .sources.codex.runtime_v2 import CodexSourceRuntimeV2
from .sources.codex.store_v2 import CodexV2Store
from .sources.document.runtime_v2 import DocumentSourceRuntimeV2
from .sources.document.store import DocumentV2Store
from .sources.experiment.contracts_v2 import canonical_sha256_v2
from .sources.experiment.runtime_v2 import ExperimentSourceRuntimeV2
from .sources.experiment.store_v2 import ExperimentStoreV2
from .sources.notebook.runtime_v2 import NotebookSourceRuntimeV2
from .sources.notebook.store import NotebookV2Store
from .sources.workspace.runtime_v2 import WorkspaceSourceRuntimeV2
from .sources.workspace.store import WorkspaceV2Store

PRODUCTION_SOURCE_RUNTIME_REGISTRY_VERSION = "production-source-runtime-registry-v2"
SOURCE_RELEASE_ROUTER_VERSION = "five-source-release-router-v2"
_SOURCES = ("codex", "experiment", "notebook", "document", "workspace")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class SourceRouteStageV2(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    CANARY = "canary"
    ON = "on"
    STABLE = "stable"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class SourceReleaseAuthorityV2(_Frozen):
    source: str
    project_id: str
    stage: SourceRouteStageV2
    canary_percent: int = Field(default=0, ge=0, le=100)
    evaluator_version: str
    evidence_sha256: str
    decision_sha256: str
    production_observation: bool
    external_authority_sha256: str | None = None
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> SourceReleaseAuthorityV2:
        if self.source not in _SOURCES:
            raise ValueError("release authority source is not registered")
        if not self.project_id or len(self.project_id) > 256:
            raise ValueError("release authority project identity is invalid")
        digests = (self.evidence_sha256, self.decision_sha256, self.content_sha256)
        if any(_SHA256_RE.fullmatch(item) is None for item in digests):
            raise ValueError("release authority digest is invalid")
        if (
            self.external_authority_sha256 is not None
            and _SHA256_RE.fullmatch(self.external_authority_sha256) is None
        ):
            raise ValueError("external release authority digest is invalid")
        if self.stage is SourceRouteStageV2.CANARY and self.canary_percent not in {5, 25}:
            raise ValueError("canary release authority must use a reviewed percentage")
        if self.stage is not SourceRouteStageV2.CANARY and self.canary_percent:
            raise ValueError("non-canary release authority cannot carry a canary percentage")
        if self.stage is not SourceRouteStageV2.OFF and not self.production_observation:
            raise ValueError("external production evidence is required for V2 routing")
        if self.stage is SourceRouteStageV2.STABLE and self.external_authority_sha256 is None:
            raise ValueError("stable V2 requires external release authority")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("release authority digest mismatch")
        return self


class SourceRouteDecisionV2(_Frozen):
    source: str
    project_sha256: str
    request_sha256: str
    stage: SourceRouteStageV2
    requested_engine: str | None
    selected_engine: str
    response_engine: str
    execute_v1: bool
    execute_v2: bool
    shadow: bool
    canary_bucket: int = Field(ge=0, le=99)
    reason_code: str
    authority_sha256: str | None
    router_version: str = SOURCE_RELEASE_ROUTER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> SourceRouteDecisionV2:
        if self.source not in _SOURCES:
            raise ValueError("route source is not registered")
        if self.requested_engine not in {None, "v1", "v2"}:
            raise ValueError("requested source engine is invalid")
        if self.selected_engine not in {"v1", "v2"} or self.response_engine not in {
            "v1",
            "v2",
        }:
            raise ValueError("selected source engine is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", self.reason_code):
            raise ValueError("route reason code is unsafe")
        if (
            self.authority_sha256 is not None
            and _SHA256_RE.fullmatch(self.authority_sha256) is None
        ):
            raise ValueError("route authority digest is invalid")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("route decision digest mismatch")
        return self


class SourceLastKnownGoodV2(_Frozen):
    source: str
    authority_sha256: str
    generation_sha256: str
    candidate_count: int = Field(ge=0)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> SourceLastKnownGoodV2:
        if self.source not in _SOURCES:
            raise ValueError("LKG source is not registered")
        if any(
            _SHA256_RE.fullmatch(item) is None
            for item in (
                self.authority_sha256,
                self.generation_sha256,
                self.content_sha256,
            )
        ):
            raise ValueError("LKG digest is invalid")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("LKG digest mismatch")
        return self


def _generic_stage(value: object) -> tuple[SourceRouteStageV2, int]:
    normalized = str(getattr(value, "value", value)).upper()
    if normalized in {"OFFLINE", "ISOLATED_BASELINE"}:
        return SourceRouteStageV2.OFF, 0
    if normalized == "SHADOW_INTERNAL_100":
        return SourceRouteStageV2.SHADOW, 0
    if normalized == "CANARY_5":
        return SourceRouteStageV2.CANARY, 5
    if normalized == "CANARY_25":
        return SourceRouteStageV2.CANARY, 25
    if normalized == "OPT_IN_100":
        return SourceRouteStageV2.ON, 0
    if normalized == "DEFAULT_V2":
        return SourceRouteStageV2.STABLE, 0
    raise ValueError("source evaluator stage is unsupported")


def _decision_name(decision: object) -> str:
    raw = getattr(decision, "decision", getattr(decision, "action", None))
    return str(getattr(raw, "value", raw)).upper()


def build_source_release_authority_v2(
    *,
    source: str,
    project_id: str,
    evidence: BaseModel,
    decision: BaseModel,
    external_authority_sha256: str | None = None,
) -> SourceReleaseAuthorityV2:
    """Bind routing authority to an exactly reproduced source evaluator result."""

    if source == "codex":
        from .sources.codex.governance_v2 import evaluate_codex_release_v2

        expected = evaluate_codex_release_v2(evidence)  # type: ignore[arg-type]
    elif source == "experiment":
        from .sources.experiment.governance_v2 import evaluate_experiment_release_v2

        expected = evaluate_experiment_release_v2(
            evidence,  # type: ignore[arg-type]
            deployed_stage=evidence.stage,  # type: ignore[attr-defined]
        )
    elif source == "notebook":
        from .sources.notebook.release_v2 import evaluate_notebook_release_v2

        expected = evaluate_notebook_release_v2(evidence)  # type: ignore[arg-type]
    elif source == "document":
        from .sources.document.release_v2 import evaluate_document_release_v2

        expected = evaluate_document_release_v2(evidence)  # type: ignore[arg-type]
    elif source == "workspace":
        from .sources.workspace.release_v2 import evaluate_workspace_release_v2

        expected = evaluate_workspace_release_v2(evidence)  # type: ignore[arg-type]
    else:
        raise ValueError("source runtime is not registered")
    if expected != decision:
        raise ValueError("source release decision is not evaluator-verifiable")

    decision_name = _decision_name(decision)
    promoted = decision_name in {"PROMOTE", "PROMOTE_NEXT_STAGE"}
    proposed_stage = getattr(evidence, "proposed_stage", getattr(evidence, "stage", None))
    if source == "experiment" and promoted:
        proposed_stage = getattr(expected, "next_stage", None)
        if proposed_stage is None:
            raise ValueError("experiment evaluator did not identify the promoted stage")
    stage, canary_percent = _generic_stage(proposed_stage)
    stable_external_hold = (
        stage is SourceRouteStageV2.STABLE
        and decision_name in {"HOLD_DEFAULT_V1", "HOLD_DEFAULT_V1".casefold().upper()}
        and external_authority_sha256 is not None
    )
    if stage is not SourceRouteStageV2.OFF and not (promoted or stable_external_hold):
        raise ValueError("source evaluator did not authorize the requested stage")
    production_observation = bool(
        getattr(
            evidence,
            "production_observation",
            not bool(getattr(evidence, "synthetic", True))
            and getattr(evidence, "treatment_artifact_sha256", None) is not None,
        )
    )
    evidence_sha256 = canonical_sha256_v2(evidence.model_dump(mode="json"))
    decision_sha256 = canonical_sha256_v2(decision.model_dump(mode="json"))
    payload = {
        "source": source,
        "project_id": project_id,
        "stage": stage,
        "canary_percent": canary_percent,
        "evaluator_version": str(
            getattr(
                evidence,
                "evaluator_version",
                getattr(evidence, "policy_version", "unknown-source-evaluator"),
            )
        ),
        "evidence_sha256": evidence_sha256,
        "decision_sha256": decision_sha256,
        "production_observation": production_observation,
        "external_authority_sha256": external_authority_sha256,
    }
    return SourceReleaseAuthorityV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            {
                **payload,
                "stage": stage.value,
            }
        ),
    )


class ProductionSourceRuntimeRegistryV2:
    """Construct source facades lazily and own their disposable indexes."""

    def __init__(
        self,
        *,
        formal_store: SQLiteStore,
        experiments: ExperimentService,
        notebooks: NotebookStore,
        documents: DocumentStore,
        workspace_service: WorkspaceService,
        workspace_store: WorkspaceStore,
        raw_sources: RawSourceService,
    ) -> None:
        self._formal_store = formal_store
        self._experiments = experiments
        self._notebooks = notebooks
        self._documents = documents
        self._workspace_service = workspace_service
        self._workspace_store = workspace_store
        self._raw_sources = raw_sources
        self._lock = threading.RLock()
        self._source_locks = {source: threading.RLock() for source in _SOURCES}
        self._facades: dict[str, Any] = {}
        self._release_authorities: dict[tuple[str, str], SourceReleaseAuthorityV2] = {}
        self._last_known_good: dict[tuple[str, str], SourceLastKnownGoodV2] = {}
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._codex_store: CodexV2Store | None = None
        self._experiment_store: ExperimentStoreV2 | None = None
        self._active_operations = 0
        self._closed = False

    @staticmethod
    def component_class(source: str) -> type[Any] | None:
        return {
            "codex": CodexSourceRuntimeV2,
            "experiment": ExperimentSourceRuntimeV2,
            "notebook": NotebookSourceRuntimeV2,
            "document": DocumentSourceRuntimeV2,
            "workspace": WorkspaceSourceRuntimeV2,
        }.get(source)

    def get(self, source: str) -> Any:
        if source not in _SOURCES:
            raise ValueError("source runtime is not registered")
        with self._lock:
            if self._closed:
                raise RuntimeError("source runtime registry is closed")
            existing = self._facades.get(source)
            if existing is not None:
                return existing
            facade = self._build(source)
            self._facades[source] = facade
            return facade

    def install_release_authority(
        self,
        *,
        source: str,
        project_id: str,
        evidence: BaseModel,
        decision: BaseModel,
        external_authority_sha256: str | None = None,
    ) -> SourceReleaseAuthorityV2:
        """Re-run the source evaluator before installing project route authority."""

        verified = build_source_release_authority_v2(
            source=source,
            project_id=project_id,
            evidence=evidence,
            decision=decision,
            external_authority_sha256=external_authority_sha256,
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("source runtime registry is closed")
            previous = self._release_authorities.get((verified.project_id, verified.source))
            self._release_authorities[(verified.project_id, verified.source)] = verified
            if previous is None or previous.content_sha256 != verified.content_sha256:
                self._last_known_good.pop(
                    (verified.project_id, verified.source),
                    None,
                )
        return verified

    def global_v2_authorized(self, *, project_id: str, source: str) -> bool:
        """Return whether this project has authority to serve V2 source results.

        Global V2 cannot treat reviewed score calibration as source release
        authority.  Shadow and sampled canary stages are deliberately excluded:
        neither deterministically serves V2 for every global request.  The
        project-local opt-in and externally authorized stable stages do.
        """

        if source not in _SOURCES:
            return False
        with self._lock:
            if self._closed:
                return False
            authority = self._release_authorities.get((project_id, source))
        return authority is not None and authority.stage in {
            SourceRouteStageV2.ON,
            SourceRouteStageV2.STABLE,
        }

    def route(
        self,
        *,
        source: str,
        project_id: str,
        request_id: str,
        requested_engine: str | None,
    ) -> SourceRouteDecisionV2:
        if source not in _SOURCES:
            raise ValueError("source runtime is not registered")
        if requested_engine not in {None, "v1", "v2"}:
            raise ValueError("requested source engine is invalid")
        project_sha256 = canonical_sha256_v2(project_id)
        request_sha256 = canonical_sha256_v2(request_id)
        bucket = (
            int.from_bytes(
                hashlib.sha256(f"{project_id}\x1f{request_id}".encode()).digest()[:8],
                "big",
            )
            % 100
        )
        with self._lock:
            authority = self._release_authorities.get((project_id, source))
        stage = authority.stage if authority is not None else SourceRouteStageV2.OFF
        if requested_engine == "v1":
            selected = response = "v1"
            execute_v1, execute_v2, shadow = True, False, False
            reason = "explicit_v1"
        elif authority is None or stage is SourceRouteStageV2.OFF:
            selected = response = "v1"
            execute_v1, execute_v2, shadow = True, False, False
            reason = "verified_authority_unavailable"
        elif stage is SourceRouteStageV2.SHADOW:
            selected, response = "v2", "v1"
            execute_v1, execute_v2, shadow = True, True, True
            reason = "verified_shadow"
        elif stage is SourceRouteStageV2.CANARY:
            selected = "v2" if bucket < authority.canary_percent else "v1"
            response = selected
            execute_v1 = selected == "v1"
            execute_v2 = selected == "v2"
            shadow = False
            reason = "verified_canary_v2" if selected == "v2" else "verified_canary_v1"
        elif stage is SourceRouteStageV2.ON:
            selected = response = "v2" if requested_engine == "v2" else "v1"
            execute_v1 = selected == "v1"
            execute_v2 = selected == "v2"
            shadow = False
            reason = "verified_opt_in_v2" if selected == "v2" else "opt_in_default_v1"
        else:
            selected = response = "v2"
            execute_v1, execute_v2, shadow = False, True, False
            reason = "externally_authorized_stable_v2"
        payload = {
            "source": source,
            "project_sha256": project_sha256,
            "request_sha256": request_sha256,
            "stage": stage,
            "requested_engine": requested_engine,
            "selected_engine": selected,
            "response_engine": response,
            "execute_v1": execute_v1,
            "execute_v2": execute_v2,
            "shadow": shadow,
            "canary_bucket": bucket,
            "reason_code": reason,
            "authority_sha256": authority.content_sha256 if authority is not None else None,
            "router_version": SOURCE_RELEASE_ROUTER_VERSION,
        }
        return SourceRouteDecisionV2(
            **payload,
            content_sha256=canonical_sha256_v2(
                {
                    **payload,
                    "stage": stage.value,
                }
            ),
        )

    def record_v2_success(
        self,
        *,
        project_id: str,
        source: str,
        authority_sha256: str,
        generation: str,
        candidate_count: int,
    ) -> SourceLastKnownGoodV2:
        with self._lock:
            authority = self._release_authorities.get((project_id, source))
            if authority is None or authority.content_sha256 != authority_sha256:
                raise ValueError("V2 success does not match active release authority")
        generation_sha256 = (
            generation if _SHA256_RE.fullmatch(generation) else canonical_sha256_v2(generation)
        )
        payload = {
            "source": source,
            "authority_sha256": authority_sha256,
            "generation_sha256": generation_sha256,
            "candidate_count": candidate_count,
        }
        lkg = SourceLastKnownGoodV2(
            **payload,
            content_sha256=canonical_sha256_v2(payload),
        )
        with self._lock:
            self._last_known_good[(project_id, source)] = lkg
        return lkg

    def last_known_good(self, *, project_id: str, source: str) -> SourceLastKnownGoodV2 | None:
        with self._lock:
            lkg = self._last_known_good.get((project_id, source))
            authority = self._release_authorities.get((project_id, source))
            if lkg is None or authority is None or lkg.authority_sha256 != authority.content_sha256:
                return None
            return lkg

    @contextmanager
    def operation(self, source: str) -> Any:
        """Serialize one complete materialize/query/readback operation."""

        if source not in _SOURCES:
            raise ValueError("source runtime is not registered")
        with self._source_locks[source]:
            with self._lock:
                if self._closed:
                    raise RuntimeError("source runtime registry is closed")
                facade = self._facades.get(source)
                if facade is None:
                    facade = self._build(source)
                    self._facades[source] = facade
                self._active_operations += 1
            try:
                yield facade
            finally:
                with self._lock:
                    self._active_operations -= 1
                    if self._closed and self._active_operations == 0:
                        self._cleanup_locked()

    @property
    def materialized_sources(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._facades))

    @property
    def derived_root_created(self) -> bool:
        with self._lock:
            return self._temporary_directory is not None

    def close(self) -> None:
        # Reject new operations immediately.  Cleanup is deferred until the
        # last in-flight operation exits, so shutdown remains bounded even if
        # a legacy synchronous driver ignores cooperative cancellation.
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._active_operations == 0:
                self._cleanup_locked()

    def operational_snapshot(self) -> dict[str, object]:
        """Expose only aggregate route/LKG state; project identities remain private."""

        with self._lock:
            verified_lkg_count = sum(
                authority is not None and authority.content_sha256 == lkg.authority_sha256
                for key, lkg in self._last_known_good.items()
                if (authority := self._release_authorities.get(key)) is not None
            )
            stage_counts = tuple(
                (
                    stage.value,
                    sum(item.stage is stage for item in self._release_authorities.values()),
                )
                for stage in SourceRouteStageV2
            )
            return {
                "registry_version": PRODUCTION_SOURCE_RUNTIME_REGISTRY_VERSION,
                "router_version": SOURCE_RELEASE_ROUTER_VERSION,
                "status": "QUALITY_HOLD",
                "default_engine": "v1",
                "materialized_sources": tuple(sorted(self._facades)),
                "materialized_source_count": len(self._facades),
                "derived_root_created": self._temporary_directory is not None,
                "release_authority_count": len(self._release_authorities),
                "release_stage_counts": stage_counts,
                "verified_lkg_count": verified_lkg_count,
                "active_operation_count": self._active_operations,
                "closed": self._closed,
            }

    def _cleanup_locked(self) -> None:
        self._codex_store = None
        if self._experiment_store is not None:
            self._experiment_store.close()
            self._experiment_store = None
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None
        self._facades.clear()
        self._release_authorities.clear()
        self._last_known_good.clear()

    def _root(self) -> Path:
        if self._temporary_directory is None:
            self._temporary_directory = tempfile.TemporaryDirectory(
                prefix="evidence-rag-source-v2-"
            )
        return Path(self._temporary_directory.name).resolve(strict=True)

    def _build(self, source: str) -> Any:
        if source == "codex":
            root = self._root()
            store = CodexV2Store(
                root / "codex-v2.sqlite3",
                isolated_root=root,
            )
            store.initialize()
            self._codex_store = store
            return CodexSourceRuntimeV2(self._formal_store, store)

        root = self._root()
        if source == "experiment":
            store = ExperimentStoreV2(
                root / "experiment-v2.sqlite3",
                isolated_root=root,
            )
            store.initialize()
            self._experiment_store = store
            return ExperimentSourceRuntimeV2(self._experiments, store)
        if source == "notebook":
            store = NotebookV2Store(
                root / "notebook-v2.sqlite3",
                isolated_root=root,
            )
            store.initialize()
            return NotebookSourceRuntimeV2(
                notebooks=self._notebooks,
                workspace=self._workspace_service,
                raw_sources=self._raw_sources,
                index=store,
            )
        if source == "document":
            store = DocumentV2Store(
                root / "document-v2.sqlite3",
                isolated_root=root,
            )
            store.initialize()
            return DocumentSourceRuntimeV2(
                documents=self._documents,
                workspace=self._workspace_service,
                index=store,
            )
        if source == "workspace":
            store = WorkspaceV2Store(
                root / "workspace-v2.sqlite3",
                isolated_root=root,
            )
            store.initialize()
            return WorkspaceSourceRuntimeV2(
                workspace=self._workspace_store,
                index=store,
            )
        raise ValueError("source runtime is not registered")


__all__ = [
    "PRODUCTION_SOURCE_RUNTIME_REGISTRY_VERSION",
    "SOURCE_RELEASE_ROUTER_VERSION",
    "ProductionSourceRuntimeRegistryV2",
    "SourceLastKnownGoodV2",
    "SourceReleaseAuthorityV2",
    "SourceRouteDecisionV2",
    "SourceRouteStageV2",
    "build_source_release_authority_v2",
]
