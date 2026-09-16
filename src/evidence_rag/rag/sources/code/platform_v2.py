"""Opt-in Code V2 routing shared by direct and Platform search endpoints."""

from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ....config import Settings
from ....models import EvidenceSearchRequest, SearchScope
from ....retrieval import HybridRetriever
from ....storage import SQLiteStore
from .context_builder_v2 import (
    CONTEXT_BUILDER_VERSION,
    CodeTaskContextBuilder,
    ContextBuildScope,
)
from .contracts import (
    CodeQueryProfile,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeSourceResult,
    CodeSourceStatus,
)
from .graph_retrieval_v2 import _stored_derivation
from .graph_v2 import CodeGraphEntityType, validate_edge_assertion
from .query_profile_v2 import (
    PIPELINE_VERSION,
    CodeSourceFusionPipeline,
    CodeSourceFusionSearchResult,
    _verify_code_source_scope_attestation,
)
from .shadow import CodeShadowRunner
from .v1_adapter import CodeSourceRetrieverV1Adapter

CODE_PLATFORM_INTEGRATION_VERSION = "c7-code-platform-integration-v1"
CODE_ENGINE_HEADER = "X-RAG-Code-Engine"
_VALID_ENGINES = frozenset({"v1", "v2"})
_FALLBACK_COMPONENT = "CodeSourceFusionPipeline"


class CodeEngineOverrideError(ValueError):
    """The request supplied an unsupported Code engine override."""


class _CodeV2Fallback(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _RoutingDecision:
    engine_requested: str
    engine_selected: str
    selection_reason: str
    canary_bucket: int | None


class _GovernedShadowRetriever:
    """Give the existing shadow runner the same governed production V2 pipeline."""

    def __init__(self, integration: CodePlatformIntegration) -> None:
        self.integration = integration

    def search(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> CodeSourceResult:
        governed = self.integration._governed_request(request)
        if governed is None:
            raise RuntimeError("Code V2 governed scope is unavailable")
        return self.integration.v2_pipeline.search(governed, profile)


class CodePlatformIntegration:
    """Route one Code request without changing the established V1 response schema."""

    integration_version = CODE_PLATFORM_INTEGRATION_VERSION

    def __init__(
        self,
        *,
        settings: Settings,
        store: SQLiteStore,
        legacy: HybridRetriever,
        v1_adapter: CodeSourceRetrieverV1Adapter,
        v2_pipeline: CodeSourceFusionPipeline,
        timeout_ms: float = 250.0,
    ) -> None:
        if v1_adapter.legacy is not legacy:
            raise ValueError("Code V1 adapter must wrap the integration legacy retriever")
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, (int, float))
            or timeout_ms <= 0
        ):
            raise ValueError("timeout_ms must be a positive number")
        self.settings = settings
        self.store = store
        self.legacy = legacy
        self.v1_adapter = v1_adapter
        self.v2_pipeline = v2_pipeline
        self.timeout_ms = float(timeout_ms)
        self.shadow_v2 = _GovernedShadowRetriever(self)

    @staticmethod
    def validate_override(engine_override: str | None) -> str | None:
        if engine_override is None:
            return None
        if (
            type(engine_override) is not str
            or engine_override not in _VALID_ENGINES
            or not engine_override.isascii()
        ):
            raise CodeEngineOverrideError(f"{CODE_ENGINE_HEADER} must be exactly 'v1' or 'v2'")
        return engine_override

    @staticmethod
    def canary_bucket(request: EvidenceSearchRequest) -> int:
        identity = {
            "commit": unicodedata.normalize("NFC", request.scope.commit or ""),
            "project_id": unicodedata.normalize("NFC", request.scope.project_id or ""),
            "query": unicodedata.normalize("NFC", request.query),
            "repository_ids": sorted(
                {
                    unicodedata.normalize("NFC", repository_id)
                    for repository_id in request.scope.repository_ids
                }
            ),
        }
        payload = json.dumps(
            identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % 100

    def search(
        self,
        request: EvidenceSearchRequest,
        *,
        engine_override: str | None = None,
        shadow_runner: CodeShadowRunner | None = None,
    ) -> dict[str, Any]:
        override = self.validate_override(engine_override)
        decision = self._decision(request, override)

        # Shadow mode is observational: V1 remains the only user response.
        if shadow_runner is not None:
            legacy_response = self.legacy.search(request)
            self._submit_shadow(shadow_runner, request, legacy_response)
            legacy_response = self._without_projected_edges(legacy_response)
            return self._with_routing_trace(
                legacy_response,
                decision=_RoutingDecision(
                    engine_requested=decision.engine_requested,
                    engine_selected="v1",
                    selection_reason=decision.selection_reason,
                    canary_bucket=decision.canary_bucket,
                ),
                fallback=self._fallback_trace("not_used"),
                shadow_status="enabled",
            )

        if decision.engine_selected == "v1":
            legacy_response = self._without_projected_edges(self.legacy.search(request))
            return self._with_routing_trace(
                legacy_response,
                decision=decision,
                fallback=self._fallback_trace("not_used"),
                shadow_status="enabled" if self.settings.rag_code_shadow else "disabled",
            )

        try:
            return self._search_v2(request, decision)
        except _CodeV2Fallback as fallback:
            # One and only one legacy execution follows a failed V2 attempt.
            legacy_response = self._without_projected_edges(self.legacy.search(request))
            fallback_decision = _RoutingDecision(
                engine_requested=decision.engine_requested,
                engine_selected="v1",
                selection_reason="fallback",
                canary_bucket=decision.canary_bucket,
            )
            return self._with_routing_trace(
                legacy_response,
                decision=fallback_decision,
                fallback=self._fallback_trace("succeeded", reason=fallback.reason),
                shadow_status="disabled",
                code_v2={
                    "status": "unavailable",
                    "component": _FALLBACK_COMPONENT,
                    "component_version": getattr(
                        self.v2_pipeline, "pipeline_version", PIPELINE_VERSION
                    ),
                    "context": self._context_trace("unavailable", "v2_unavailable"),
                },
            )

    def _decision(
        self,
        request: EvidenceSearchRequest,
        override: str | None,
    ) -> _RoutingDecision:
        if override is not None:
            return _RoutingDecision(override, override, "header", None)
        requested = self.settings.rag_code_engine
        if requested == "v2":
            return _RoutingDecision("v2", "v2", "setting", None)
        bucket = self.canary_bucket(request)
        selected = "v2" if bucket < self.settings.rag_code_canary_percent else "v1"
        reason = "canary" if self.settings.rag_code_canary_percent else "default"
        return _RoutingDecision("v1", selected, reason, bucket)

    def _search_v2(
        self,
        request: EvidenceSearchRequest,
        decision: _RoutingDecision,
    ) -> dict[str, Any]:
        governed = self._governed_request(request)
        if governed is None:
            raise _CodeV2Fallback("scope_unavailable")
        started = time.monotonic()
        deadline = started + self.timeout_ms / 1000.0
        try:
            fusion = self.v2_pipeline.search_with_trace(governed, deadline=deadline)
        except Exception as error:
            del error
            raise _CodeV2Fallback("v2_error") from None
        if time.monotonic() > deadline or fusion.result.status is CodeSourceStatus.TIMEOUT:
            raise _CodeV2Fallback("v2_timeout")
        if fusion.result.status is CodeSourceStatus.UNAVAILABLE:
            raise _CodeV2Fallback("v2_unavailable")
        try:
            attestation = _verify_code_source_scope_attestation(fusion)
            results, edge_projection = self._project_results(governed, fusion, attestation)
        except Exception as error:
            del error
            raise _CodeV2Fallback("attestation_or_projection_unavailable") from None

        context = self._structured_context_trace(fusion, attestation)
        trace = {
            "duration_ms": round((time.monotonic() - started) * 1000.0, 2),
            "lexical_candidates": self._channel_hits(fusion.result, "sparse"),
            "dense_candidates": 0,
            "dense_matches": 0,
            "fusion": "code-source-fusion-v2",
            "embedding_model": None,
            "queried_embedding_models": [],
            "code_v2": {
                "status": fusion.result.status.value,
                "component": _FALLBACK_COMPONENT,
                "component_version": fusion.trace.pipeline_version,
                "profile": {
                    "key": fusion.trace.profile_key,
                    "version": fusion.trace.profile_version,
                    "task": fusion.trace.task.value,
                },
                "scope_attestation": "verified",
                "edge_projection": edge_projection,
                "context": context,
            },
        }
        response = {
            "query_id": fusion.result.query_id,
            "query": request.query,
            "resolved_scope": request.scope.model_dump(),
            "index_generation": sorted(
                {candidate.source_generation for candidate in fusion.result.candidates}
            ),
            "total": len(results),
            "results": results,
            "trace": trace,
        }
        return self._with_routing_trace(
            response,
            decision=decision,
            fallback=self._fallback_trace("not_used"),
            shadow_status="disabled",
        )

    def _governed_request(
        self,
        request: EvidenceSearchRequest,
    ) -> EvidenceSearchRequest | None:
        requested_ids = tuple(request.scope.repository_ids)
        commit = request.scope.commit
        if (
            len(requested_ids) != 1
            or not request.scope.project_id
            or request.scope.branch is not None
            or commit is None
            or len(commit) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in commit)
        ):
            return None

        repository_id = requested_ids[0]
        project_id = request.scope.project_id
        repositories = self.store.list_repositories()
        requested = [
            row
            for row in repositories
            if str(row["id"]) == repository_id and str(row["project_id"]) == project_id
        ]
        if len(requested) != 1:
            return None
        repository = requested[0]

        supplied_acl_refs = set(request.scope.allowed_acl_refs)
        repository_acl_ref = str(repository["acl_ref"])
        if request.scope.enforce_acl and (
            repository_acl_ref != "public" and repository_acl_ref not in supplied_acl_refs
        ):
            return None
        active = self.store.active_code_generations(
            project_id=project_id,
            repository_ids=[repository_id],
        )
        generation_id = active.get(repository_id)
        if (
            len(active) != 1
            or not generation_id
            or str(repository.get("active_generation_id") or "") != generation_id
            or str(repository.get("head_commit") or "") != commit
            or str(repository.get("status") or "") != "ready"
        ):
            return None
        with self.store.connection() as database:
            generation = database.execute(
                """
                SELECT commit_sha, status
                FROM index_generations
                WHERE id=? AND repository_id=?
                """,
                (generation_id, repository_id),
            ).fetchone()
        if (
            generation is None
            or str(generation["commit_sha"]) != commit
            or str(generation["status"]) != "published"
        ):
            return None
        publication = self.store.get_code_index_publication(
            generation_id,
            repository_id=repository_id,
            active_only=True,
        )
        if (
            publication is None
            or str(publication.get("project_id") or "") != project_id
            or str(publication.get("repository_id") or "") != repository_id
            or str(publication.get("generation_id") or "") != generation_id
            or str(publication.get("status") or "") != "published"
            or str(publication.get("sparse") or "").casefold()
            in {"", "not-built", "not_built", "disabled", "unavailable"}
        ):
            return None
        validation = publication.get("validation")
        if isinstance(validation, Mapping):
            capabilities = validation.get("capabilities")
            if isinstance(capabilities, Mapping) and capabilities.get("sparse_retrieval") is False:
                return None
        if not repository_acl_ref:
            return None
        allowed_acl_refs = tuple(
            sorted(
                {"public"}
                | supplied_acl_refs
                | ({repository_acl_ref} if not request.scope.enforce_acl else set())
            )
        )
        scope_payload = request.scope.model_dump()
        scope_payload.update(
            {
                "project_id": project_id,
                "repository_ids": [repository_id],
                "allowed_acl_refs": list(allowed_acl_refs),
                "enforce_acl": True,
            }
        )
        return EvidenceSearchRequest(
            query=request.query,
            scope=SearchScope(**scope_payload),
            limit=request.limit,
            include_edges=request.include_edges,
        )

    def _project_results(
        self,
        request: EvidenceSearchRequest,
        fusion: CodeSourceFusionSearchResult,
        attestation: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, int | str]]:
        if (
            request.scope.project_id != attestation.project_id
            or request.scope.commit != attestation.requested_commit
            or request.scope.commit != attestation.target_ref
        ):
            raise ValueError("edge projection scope conflicts with the attested request")
        allowed_acl_refs = set(attestation.allowed_acl_refs)
        repository_ids = set(attestation.repository_ids)
        results: list[dict[str, Any]] = []
        seen_edge_ids: set[str] = set()
        projected_edge_count = 0
        deduplicated_edge_count = 0
        for candidate in fusion.result.candidates:
            self._verify_candidate_scope(candidate, request, repository_ids, allowed_acl_refs)
            unit = self.store.load_code_unit(
                candidate.retrieval_unit_id,
                project_id=attestation.project_id,
                repository_id=candidate.repository_id,
                generation_id=candidate.source_generation,
                allowed_acl_refs=attestation.allowed_acl_refs,
                active_only=False,
            )
            if unit is None:
                raise ValueError("attested retrieval unit is unavailable")
            self._verify_unit(candidate, unit, attestation.project_id)
            metadata = unit.get("metadata")
            if not isinstance(metadata, Mapping):
                metadata = {}
            qualified_name = str(unit.get("qualified_name") or "")
            path = str(unit.get("path") or "")
            name = str(metadata.get("name") or "")
            if not name:
                name = (
                    qualified_name.rsplit(".", 1)[-1] if qualified_name else path.rsplit("/", 1)[-1]
                )
            sparse_score = next(
                (
                    float(score.score)
                    for score in candidate.raw_channel_scores
                    if score.channel.value == "sparse"
                ),
                0.0,
            )
            edges, duplicates = self._project_candidate_edges(
                request,
                candidate,
                attestation,
                seen_edge_ids,
            )
            projected_edge_count += len(edges)
            deduplicated_edge_count += duplicates
            results.append(
                {
                    "entity_id": candidate.entity_id,
                    "repository_id": candidate.repository_id,
                    "generation_id": candidate.source_generation,
                    "entity_type": candidate.entity_type,
                    "view_type": str(unit.get("unit_type") or ""),
                    "name": name,
                    "qualified_name": qualified_name or None,
                    "path": path,
                    "language": str(unit.get("language") or ""),
                    "commit": candidate.stable_version,
                    "start_line": unit.get("start_line"),
                    "end_line": unit.get("end_line"),
                    "evidence_locator": candidate.locator,
                    "acl_ref": candidate.acl_ref,
                    "metadata": dict(metadata),
                    "lexical_score": sparse_score,
                    "dense_score": 0.0,
                    "score": round(float(candidate.source_fused_score), 6),
                    "channels": [score.channel.value for score in candidate.raw_channel_scores],
                    "edges": edges,
                    "snippet": self._snippet(str(unit.get("content") or "")),
                }
            )
        edge_status = (
            "disabled"
            if not request.include_edges
            else ("complete" if projected_edge_count else "no_match")
        )
        return results, {
            "status": edge_status,
            "projected_edge_count": min(200, projected_edge_count),
            "deduplicated_edge_count": min(200, deduplicated_edge_count),
        }

    def _project_candidate_edges(
        self,
        request: EvidenceSearchRequest,
        candidate: CodeRetrievalCandidate,
        attestation: Any,
        seen_edge_ids: set[str],
    ) -> tuple[list[dict[str, Any]], int]:
        channels = {score.channel for score in candidate.raw_channel_scores}
        if not request.include_edges or CodeRetrievalChannel.GRAPH not in channels:
            return [], 0
        hops = candidate.relation_path.edges
        if not hops:
            return [], 0
        edge_ids = tuple(edge.edge_id for edge in hops)
        if any(edge_id is None for edge_id in edge_ids):
            raise ValueError("graph relation path is missing a stored edge identity")
        resolved_ids = tuple(str(edge_id) for edge_id in edge_ids)
        if len(resolved_ids) != len(set(resolved_ids)):
            raise ValueError("graph relation path repeats a stored edge identity")
        stored = self._load_stored_graph_edges(candidate, attestation.project_id, resolved_ids)
        projected: list[dict[str, Any]] = []
        duplicates = 0
        for edge in hops:
            assert edge.edge_id is not None
            row = stored.get(edge.edge_id)
            if row is None:
                raise ValueError("attested graph edge is unavailable")
            self._verify_stored_graph_edge(edge, row, candidate, request, attestation)
            if edge.edge_id in seen_edge_ids:
                duplicates += 1
                continue
            seen_edge_ids.add(edge.edge_id)
            projected.append(
                {
                    "id": edge.edge_id,
                    "project_id": attestation.project_id,
                    "repository_id": candidate.repository_id,
                    "ref": attestation.target_ref,
                    "commit": candidate.stable_version,
                    "generation_id": candidate.source_generation,
                    "source_id": edge.source_entity_id,
                    "target_id": edge.target_entity_id,
                    "edge_type": edge.edge_type.value,
                    "direction": edge.direction.value,
                    "hop": edge.hop,
                    "confidence": edge.confidence,
                    "derivation": edge.derivation.value,
                    "review_status": edge.review_status.value,
                    "fact_status": edge.fact_status.value,
                    "evidence_locator": edge.locator.locator,
                    "source_version": edge.source_version,
                    "target_version": edge.target_version,
                    "source_generation": edge.source_generation,
                    "target_generation": edge.target_generation,
                    "source_acl_ref": edge.source_acl_ref,
                    "target_acl_ref": edge.target_acl_ref,
                }
            )
        return projected, duplicates

    def _load_stored_graph_edges(
        self,
        candidate: CodeRetrievalCandidate,
        project_id: str,
        edge_ids: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        marks = ",".join("?" for _ in edge_ids)
        with self.store.connection() as database:
            rows = database.execute(
                f"""
                SELECT edge.id AS edge_id, edge.repository_id, edge.generation_id,
                       edge.edge_type, edge.derivation, edge.confidence,
                       edge.evidence_locator, repository.project_id,
                       repository.active_generation_id,
                       generation.commit_sha AS generation_commit,
                       generation.status AS generation_status,
                       source.id AS source_id,
                       source.project_id AS source_project_id,
                       source.repository_id AS source_repository_id,
                       source.generation_id AS source_generation,
                       source.entity_type AS source_entity_type,
                       source.commit_sha AS source_version,
                       source.source_uri AS source_locator,
                       source.acl_ref AS source_acl_ref,
                       target.id AS target_id,
                       target.project_id AS target_project_id,
                       target.repository_id AS target_repository_id,
                       target.generation_id AS target_generation,
                       target.entity_type AS target_entity_type,
                       target.commit_sha AS target_version,
                       target.source_uri AS target_locator,
                       target.acl_ref AS target_acl_ref
                  FROM edges edge
                  JOIN repositories repository
                    ON repository.id=edge.repository_id
                  JOIN index_generations generation
                    ON generation.id=edge.generation_id
                   AND generation.repository_id=edge.repository_id
                  JOIN entities source
                    ON source.id=edge.source_id
                   AND source.repository_id=edge.repository_id
                   AND source.generation_id=edge.generation_id
                  JOIN entities target
                    ON target.id=edge.target_id
                   AND target.repository_id=edge.repository_id
                   AND target.generation_id=edge.generation_id
                 WHERE edge.repository_id=? AND edge.generation_id=?
                   AND repository.project_id=? AND edge.id IN ({marks})
                 ORDER BY edge.id
                """,
                (
                    candidate.repository_id,
                    candidate.source_generation,
                    project_id,
                    *edge_ids,
                ),
            ).fetchall()
        values = {str(row["edge_id"]): dict(row) for row in rows}
        if len(values) != len(edge_ids):
            raise ValueError("stored graph edge identity set does not match the relation path")
        return values

    @staticmethod
    def _verify_stored_graph_edge(
        edge: Any,
        row: Mapping[str, Any],
        candidate: CodeRetrievalCandidate,
        request: EvidenceSearchRequest,
        attestation: Any,
    ) -> None:
        governance = _stored_derivation(str(row.get("derivation") or ""))
        if governance is None:
            raise ValueError("stored graph edge derivation is unsupported")
        layer, derivation, review_status, fact_status = governance
        if review_status not in {
            CodeReviewStatus.MACHINE_CONFIRMED,
            CodeReviewStatus.HUMAN_CONFIRMED,
        }:
            raise ValueError("stored graph edge is not reviewed")
        validate_edge_assertion(
            edge.edge_type,
            source_type=CodeGraphEntityType(str(row["source_entity_type"])),
            target_type=CodeGraphEntityType(str(row["target_entity_type"])),
            derivation=layer,
        )
        stored_locator = str(row.get("evidence_locator") or row.get("source_locator") or "")
        expected = (
            edge.edge_id,
            edge.source_entity_id,
            edge.target_entity_id,
            edge.source_repository_id,
            edge.target_repository_id,
            edge.edge_type.value,
            edge.confidence,
            edge.source_version,
            edge.target_version,
            edge.source_generation,
            edge.target_generation,
            edge.source_acl_ref,
            edge.target_acl_ref,
            edge.derivation,
            edge.review_status,
            edge.fact_status,
            edge.locator.locator,
        )
        actual = (
            str(row["edge_id"]),
            str(row["source_id"]),
            str(row["target_id"]),
            str(row["source_repository_id"]),
            str(row["target_repository_id"]),
            str(row["edge_type"]),
            float(row["confidence"]),
            str(row["source_version"]),
            str(row["target_version"]),
            str(row["source_generation"]),
            str(row["target_generation"]),
            str(row["source_acl_ref"]),
            str(row["target_acl_ref"]),
            derivation,
            review_status,
            fact_status,
            stored_locator,
        )
        if actual != expected:
            raise ValueError("stored graph edge conflicts with attested relation provenance")
        commit = request.scope.commit
        if (
            str(row["project_id"]) != attestation.project_id
            or str(row["source_project_id"]) != attestation.project_id
            or str(row["target_project_id"]) != attestation.project_id
            or str(row["repository_id"]) != candidate.repository_id
            or str(row["generation_id"]) != candidate.source_generation
            or str(row["active_generation_id"]) != candidate.source_generation
            or str(row["generation_commit"]) != candidate.stable_version
            or str(row["generation_status"]) != "published"
            or candidate.repository_id not in attestation.repository_ids
            or candidate.source_generation not in attestation.source_generations
            or candidate.stable_version not in attestation.stable_versions
            or edge.source_acl_ref not in attestation.allowed_acl_refs
            or edge.target_acl_ref not in attestation.allowed_acl_refs
            or commit != candidate.stable_version
            or attestation.target_ref != commit
        ):
            raise ValueError("stored graph edge is outside the governed publication scope")

    @staticmethod
    def _verify_candidate_scope(
        candidate: CodeRetrievalCandidate,
        request: EvidenceSearchRequest,
        repository_ids: set[str],
        allowed_acl_refs: set[str],
    ) -> None:
        if (
            candidate.repository_id not in repository_ids
            or candidate.repository_id not in request.scope.repository_ids
            or candidate.acl_ref not in allowed_acl_refs
            or candidate.acl_ref not in request.scope.allowed_acl_refs
        ):
            raise ValueError("candidate is outside the attested request scope")
        if request.scope.commit and candidate.stable_version != request.scope.commit:
            raise ValueError("candidate commit conflicts with the requested commit")

    @staticmethod
    def _verify_unit(
        candidate: CodeRetrievalCandidate,
        unit: Mapping[str, Any],
        project_id: str,
    ) -> None:
        actual = (
            str(unit.get("id") or ""),
            str(unit.get("entity_id") or ""),
            str(unit.get("repository_id") or ""),
            str(unit.get("generation_id") or ""),
            str(unit.get("project_id") or ""),
            str(unit.get("acl_ref") or ""),
        )
        expected = (
            candidate.retrieval_unit_id,
            candidate.entity_id,
            candidate.repository_id,
            candidate.source_generation,
            project_id,
            candidate.acl_ref,
        )
        if actual != expected:
            raise ValueError("stored retrieval unit conflicts with attested candidate provenance")

    def _structured_context_trace(
        self,
        fusion: CodeSourceFusionSearchResult,
        attestation: Any,
    ) -> dict[str, Any]:
        if self.settings.rag_code_context != "structured-v2":
            return self._context_trace("disabled", "snippet_v1")
        candidates = fusion.result.candidates
        candidate_acls = {candidate.acl_ref for candidate in candidates}
        if (
            len(attestation.repository_ids) != 1
            or len(attestation.stable_versions) != 1
            or len(attestation.source_generations) != 1
            or len(candidate_acls) != 1
        ):
            return self._context_trace("unavailable", "scope_unavailable")
        try:
            scope = ContextBuildScope(
                project_id=attestation.project_id,
                repository_id=attestation.repository_ids[0],
                stable_version=attestation.stable_versions[0],
                source_generation=attestation.source_generations[0],
                acl_ref=next(iter(candidate_acls)),
                index_version=attestation.index_version,
                watermark=attestation.watermark,
                allowed_path_prefixes=attestation.requested_paths,
                allowed_acl_refs=attestation.allowed_acl_refs,
                enforce_acl=attestation.enforce_acl,
                requested_commit=attestation.requested_commit,
                requested_branch=attestation.requested_branch,
                target_ref=attestation.target_ref,
            )
            built = CodeTaskContextBuilder().build(fusion, scope=scope)
        except Exception as error:
            del error
            return self._context_trace("unavailable", "builder_unavailable")
        return {
            "status": built.comprehension.status.value,
            "builder_version": CONTEXT_BUILDER_VERSION,
            "selected_source_blocks": built.trace.selected_source_blocks,
            "fulfilled_roles": [role.value for role in built.trace.fulfilled_roles],
            "missing_roles": [role.value for role in built.trace.missing_roles],
        }

    @staticmethod
    def _context_trace(status: str, reason: str) -> dict[str, Any]:
        return {
            "status": status,
            "builder_version": CONTEXT_BUILDER_VERSION,
            "reason": reason,
        }

    @staticmethod
    def _channel_hits(result: CodeSourceResult, channel: str) -> int:
        for outcome in result.channel_outcomes:
            if outcome.channel.value == channel:
                return int(getattr(outcome, "hit_count", 0))
        return 0

    @staticmethod
    def _snippet(content: str, size: int = 700) -> str:
        if len(content) <= size:
            return content
        return content[:size] + "…"

    @staticmethod
    def _without_projected_edges(response: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(response)
        results: list[Any] = []
        for item in payload.get("results") or []:
            if not isinstance(item, Mapping):
                raise ValueError("legacy Code result must be a mapping")
            projected = dict(item)
            projected["edges"] = []
            results.append(projected)
        payload["results"] = results
        return payload

    def _submit_shadow(
        self,
        runner: CodeShadowRunner,
        request: EvidenceSearchRequest,
        legacy_response: Mapping[str, Any],
    ) -> str:
        try:
            profile = self.v1_adapter.profile_for_request(request)
            return "submitted" if runner.submit(request, profile, legacy_response) else "dropped"
        except Exception:
            return "error"

    @staticmethod
    def _fallback_trace(status: str, *, reason: str = "none") -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "component": _FALLBACK_COMPONENT,
            "component_version": PIPELINE_VERSION,
        }

    def _with_routing_trace(
        self,
        response: Mapping[str, Any],
        *,
        decision: _RoutingDecision,
        fallback: Mapping[str, Any],
        shadow_status: str,
        code_v2: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(response)
        trace = dict(payload.get("trace") or {})
        trace["routing"] = {
            "integration_version": CODE_PLATFORM_INTEGRATION_VERSION,
            "engine_requested": decision.engine_requested,
            "engine_selected": decision.engine_selected,
            "selection_reason": decision.selection_reason,
            "canary_percent": self.settings.rag_code_canary_percent,
            "canary_bucket": decision.canary_bucket,
            "fallback": dict(fallback),
            "shadow": {"status": shadow_status},
        }
        if code_v2 is not None:
            trace["code_v2"] = dict(code_v2)
        payload["trace"] = trace
        return payload


__all__ = [
    "CODE_ENGINE_HEADER",
    "CODE_PLATFORM_INTEGRATION_VERSION",
    "CodeEngineOverrideError",
    "CodePlatformIntegration",
]
