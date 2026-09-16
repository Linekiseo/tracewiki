"""Typed, bounded, read-only traversal for the published Code V2 graph.

The graph channel is intentionally a supplement to exact/sparse/dense
retrieval.  It starts from already-authorized candidates, follows only
``graph_v2`` registry relations, and emits relation-only candidates with a
complete governed path.  Storage is kept behind a small read-only adjacency
protocol; the SQLite implementation never creates or mutates graph data.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .contracts import (
    CodeCandidateRole,
    CodeChannelRank,
    CodeChannelScore,
    CodeDerivation,
    CodeFactStatus,
    CodeRelationHop,
    CodeRelationLocator,
    CodeRelationNode,
    CodeRelationPath,
    CodeRelationType,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeTask,
    CodeTraversalDirection,
    CodeUncalibratedScore,
    CodeVersionAlignment,
)
from .graph_v2 import (
    CodeEdgeDerivationLayer,
    CodeEdgeGenerationRequirement,
    CodeEdgeVersionRequirement,
    CodeGraphEntityType,
    get_edge_spec,
    require_registered_edge_types,
    validate_edge_assertion,
)

GRAPH_RETRIEVER_VERSION = "code-typed-graph-retriever-v1"
GRAPH_FUSION_POLICY = "stable-hybrid-first-graph-supplement-v1"
HOP_DECAY = 0.72
_NOT_BUILT = frozenset({"", "not-built", "not_built", "disabled", "unavailable"})
_DIRECTION_ORDER = {direction: index for index, direction in enumerate(CodeTraversalDirection)}
_RELATION_ORDER = {relation: index for index, relation in enumerate(CodeRelationType)}


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty trimmed string")
    return value


def _bounded_int(name: str, value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _unit_float(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved < 0.0 or resolved > 1.0:
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    return resolved


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One adjacency endpoint with enough provenance to build a governed path."""

    entity_id: str
    repository_id: str
    entity_type: CodeGraphEntityType
    stable_version: str
    generation_id: str
    locator: str
    acl_ref: str
    retrieval_unit_id: str | None = None
    token_estimate: int = 0

    def __post_init__(self) -> None:
        for name in (
            "entity_id",
            "repository_id",
            "stable_version",
            "generation_id",
            "locator",
            "acl_ref",
        ):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        try:
            entity_type = CodeGraphEntityType(self.entity_type)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown graph entity type: {self.entity_type!r}") from error
        object.__setattr__(self, "entity_type", entity_type)
        if self.retrieval_unit_id is not None:
            object.__setattr__(
                self,
                "retrieval_unit_id",
                _required_text("retrieval_unit_id", self.retrieval_unit_id),
            )
            if self.retrieval_unit_id == self.entity_id:
                raise ValueError("retrieval_unit_id must differ from entity_id")
        object.__setattr__(
            self,
            "token_estimate",
            _bounded_int("token_estimate", self.token_estimate, minimum=0, maximum=10_000_000),
        )

    def semantic_identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.entity_id,
            self.repository_id,
            self.stable_version,
            self.generation_id,
            self.acl_ref,
        )

    def relation_node(self) -> CodeRelationNode:
        return CodeRelationNode(
            entity_id=self.entity_id,
            repository_id=self.repository_id,
            stable_version=self.stable_version,
            source_generation=self.generation_id,
            locator=self.locator,
            acl_ref=self.acl_ref,
        )


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One stored edge returned by an adjacency provider."""

    edge_id: str
    edge_type: CodeRelationType
    traversal_direction: CodeTraversalDirection
    source: GraphNode
    target: GraphNode
    confidence: float
    derivation_layer: CodeEdgeDerivationLayer
    derivation: CodeDerivation
    review_status: CodeReviewStatus
    fact_status: CodeFactStatus
    locator: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "edge_id", _required_text("edge_id", self.edge_id))
        object.__setattr__(self, "locator", _required_text("locator", self.locator))
        try:
            edge_type = CodeRelationType(self.edge_type)
            direction = CodeTraversalDirection(self.traversal_direction)
            layer = CodeEdgeDerivationLayer(self.derivation_layer)
            derivation = CodeDerivation(self.derivation)
            review_status = CodeReviewStatus(self.review_status)
            fact_status = CodeFactStatus(self.fact_status)
        except (TypeError, ValueError) as error:
            raise ValueError("graph edge contains an unknown typed value") from error
        object.__setattr__(self, "edge_type", edge_type)
        object.__setattr__(self, "traversal_direction", direction)
        object.__setattr__(self, "derivation_layer", layer)
        object.__setattr__(self, "derivation", derivation)
        object.__setattr__(self, "review_status", review_status)
        object.__setattr__(self, "fact_status", fact_status)
        object.__setattr__(self, "confidence", _unit_float("confidence", self.confidence))

    @property
    def next_node(self) -> GraphNode:
        if self.traversal_direction is CodeTraversalDirection.OUTGOING:
            return self.target
        return self.source


@dataclass(frozen=True, slots=True)
class GraphPublication:
    """Published graph scope resolved by a provider."""

    project_id: str
    repository_id: str
    generation_id: str
    stable_version: str
    graph_version: str
    ready: bool
    unavailable_reason: str = ""

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "generation_id",
            "stable_version",
            "graph_version",
        ):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        if not isinstance(self.ready, bool):
            raise TypeError("ready must be a bool")
        if self.unavailable_reason:
            object.__setattr__(
                self,
                "unavailable_reason",
                _required_text("unavailable_reason", self.unavailable_reason),
            )


class GraphAdjacencyProvider(Protocol):
    """Read-only storage boundary required by the graph retriever.

    ``adjacent`` must prefilter on repository, generation, direction, registered
    edge type, confidence, stable version, and ACL.  The retriever repeats every
    security/provenance check so a buggy provider still fails closed.
    """

    def publication(
        self,
        *,
        repository_id: str,
        generation_id: str,
    ) -> GraphPublication | None: ...

    def adjacent(
        self,
        *,
        node: GraphNode,
        directions: tuple[CodeTraversalDirection, ...],
        edge_types: tuple[CodeRelationType, ...],
        min_confidence: float,
        stable_version: str,
        generation_id: str,
        allowed_acl_refs: tuple[str, ...],
        limit: int,
    ) -> Sequence[GraphEdge]: ...


@dataclass(frozen=True, slots=True)
class GraphTraversalRequest:
    """Strict, bounded input for one graph-only traversal."""

    seed_candidates: tuple[CodeRetrievalCandidate, ...]
    project_id: str
    task: CodeTask
    directions: tuple[CodeTraversalDirection, ...]
    edge_types: tuple[CodeRelationType, ...]
    max_hops: int
    beam_width: int
    min_confidence: float
    target_ref: str
    stable_version: str
    generation_id: str
    allowed_acl_refs: tuple[str, ...]
    node_budget: int
    edge_budget: int
    candidate_budget: int = 50
    deadline: float | None = None

    def __post_init__(self) -> None:
        seeds = tuple(self.seed_candidates)
        if any(not isinstance(seed, CodeRetrievalCandidate) for seed in seeds):
            raise TypeError("seed_candidates must contain CodeRetrievalCandidate values")
        if len(seeds) > 10:
            raise ValueError("seed_candidates is bounded to the top 10 candidates")
        object.__setattr__(
            self,
            "seed_candidates",
            tuple(
                sorted(
                    seeds,
                    key=lambda seed: (
                        seed.within_source_rank,
                        seed.repository_id,
                        seed.locator,
                        seed.retrieval_unit_id,
                    ),
                )
            ),
        )
        try:
            task = CodeTask(self.task)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unknown Code task: {self.task!r}") from error
        object.__setattr__(self, "task", task)

        try:
            directions = tuple(CodeTraversalDirection(value) for value in self.directions)
        except (TypeError, ValueError) as error:
            raise ValueError("directions contain an unknown traversal direction") from error
        if len(directions) != len(set(directions)):
            raise ValueError("directions must not contain duplicates")
        directions = tuple(sorted(directions, key=_DIRECTION_ORDER.__getitem__))
        object.__setattr__(self, "directions", directions)

        edge_types = require_registered_edge_types(self.edge_types)
        object.__setattr__(self, "edge_types", edge_types)
        for edge_type in edge_types:
            if task not in get_edge_spec(edge_type).allowed_tasks:
                raise ValueError(f"{edge_type.value} is not allowed for task {task.value}")

        object.__setattr__(
            self,
            "max_hops",
            _bounded_int("max_hops", self.max_hops, minimum=0, maximum=4),
        )
        object.__setattr__(
            self,
            "beam_width",
            _bounded_int("beam_width", self.beam_width, minimum=1, maximum=512),
        )
        object.__setattr__(
            self,
            "node_budget",
            _bounded_int("node_budget", self.node_budget, minimum=0, maximum=10_000),
        )
        object.__setattr__(
            self,
            "edge_budget",
            _bounded_int("edge_budget", self.edge_budget, minimum=0, maximum=50_000),
        )
        object.__setattr__(
            self,
            "candidate_budget",
            _bounded_int(
                "candidate_budget",
                self.candidate_budget,
                minimum=0,
                maximum=10_000,
            ),
        )
        object.__setattr__(
            self,
            "min_confidence",
            _unit_float("min_confidence", self.min_confidence),
        )
        object.__setattr__(
            self,
            "project_id",
            _required_text("project_id", self.project_id),
        )
        object.__setattr__(
            self,
            "target_ref",
            _required_text("target_ref", self.target_ref),
        )
        object.__setattr__(
            self,
            "stable_version",
            _required_text("stable_version", self.stable_version),
        )
        if self.target_ref != self.stable_version:
            raise ValueError("graph traversal target_ref must be the exact stable version")
        if len(self.stable_version) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in self.stable_version
        ):
            raise ValueError("graph traversal requires a full lowercase commit")
        object.__setattr__(
            self,
            "generation_id",
            _required_text("generation_id", self.generation_id),
        )

        acl_refs = tuple(
            _required_text("allowed_acl_ref", value) for value in self.allowed_acl_refs
        )
        if not acl_refs:
            raise ValueError("allowed_acl_refs must be non-empty for fail-closed traversal")
        if len(acl_refs) != len(set(acl_refs)):
            raise ValueError("allowed_acl_refs must not contain duplicates")
        acl_refs = tuple(sorted(acl_refs))
        object.__setattr__(self, "allowed_acl_refs", acl_refs)

        if self.max_hops > 0 and (not directions or not edge_types):
            raise ValueError("positive-hop traversal requires directions and edge_types")
        if self.deadline is not None:
            if isinstance(self.deadline, bool) or not isinstance(self.deadline, (int, float)):
                raise TypeError("deadline must be a finite monotonic timestamp")
            deadline = float(self.deadline)
            if not math.isfinite(deadline):
                raise ValueError("deadline must be a finite monotonic timestamp")
            object.__setattr__(self, "deadline", deadline)

        allowed = set(acl_refs)
        seen_seed_keys: set[tuple[str, str]] = set()
        for seed in seeds:
            key = (seed.source_generation, seed.retrieval_unit_id)
            if key in seen_seed_keys:
                raise ValueError("seed_candidates must not contain duplicate retrieval units")
            seen_seed_keys.add(key)
            if seed.source_generation != self.generation_id:
                raise ValueError("seed generation differs from traversal generation")
            if seed.stable_version != self.stable_version:
                raise ValueError("seed stable version differs from traversal version")
            if seed.acl_ref not in allowed:
                raise ValueError("seed ACL is outside allowed_acl_refs")

    def fingerprint(self) -> str:
        payload = {
            "seeds": [seed.canonical_sha256() for seed in self.seed_candidates],
            "project_id": self.project_id,
            "task": self.task.value,
            "directions": [value.value for value in self.directions],
            "edge_types": [value.value for value in self.edge_types],
            "max_hops": self.max_hops,
            "beam_width": self.beam_width,
            "min_confidence": self.min_confidence,
            "target_ref": self.target_ref,
            "stable_version": self.stable_version,
            "generation_id": self.generation_id,
            "allowed_acl_refs": self.allowed_acl_refs,
            "node_budget": self.node_budget,
            "edge_budget": self.edge_budget,
            "candidate_budget": self.candidate_budget,
            "deadline": self.deadline,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return "graph-request:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class GraphTraversalPath:
    """One selected relation path and its complete reader-facing explanation."""

    seed_retrieval_unit_id: str
    edge_ids: tuple[str, ...]
    relation_path: CodeRelationPath
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "seed_retrieval_unit_id",
            _required_text("seed_retrieval_unit_id", self.seed_retrieval_unit_id),
        )
        edge_ids = tuple(_required_text("edge_id", value) for value in self.edge_ids)
        object.__setattr__(self, "edge_ids", edge_ids)
        object.__setattr__(
            self,
            "explanation",
            _required_text("explanation", self.explanation),
        )
        if len(edge_ids) != len(self.relation_path.edges):
            raise ValueError("edge_ids must identify every relation_path edge")
        hop_edge_ids = tuple(edge.edge_id for edge in self.relation_path.edges)
        if edge_ids != hop_edge_ids:
            raise ValueError("edge_ids must exactly match traversal-ordered relation hops")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("graph traversal paths cannot repeat a stored edge identity")


@dataclass(frozen=True, slots=True)
class GraphTraversalCandidate:
    """A graph-only Code candidate plus its typed path explanation."""

    candidate: CodeRetrievalCandidate
    path: GraphTraversalPath
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "explanation",
            _required_text("explanation", self.explanation),
        )
        if self.candidate.relation_path != self.path.relation_path:
            raise ValueError("candidate relation_path must match its graph path")
        channels = {entry.channel for entry in self.candidate.raw_channel_scores}
        if channels != {CodeRetrievalChannel.GRAPH}:
            raise ValueError("graph traversal candidates must be graph-only")

    @property
    def entity_id(self) -> str:
        return self.candidate.entity_id

    @property
    def retrieval_unit_id(self) -> str:
        return self.candidate.retrieval_unit_id

    @property
    def role(self) -> CodeCandidateRole:
        return self.candidate.role


class GraphTraversalStatus(StrEnum):
    COMPLETE = "complete"
    NO_MATCH = "no_match"
    DEADLINE = "deadline"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class GraphTraversalTrace:
    """Graph-only trace; it deliberately contains no lexical or dense scores."""

    request_id: str
    retriever_version: str
    status: GraphTraversalStatus
    generation_id: str
    stable_version: str
    publication_versions: tuple[str, ...]
    seed_count: int
    directions: tuple[CodeTraversalDirection, ...]
    edge_types: tuple[CodeRelationType, ...]
    paths: tuple[GraphTraversalPath, ...]
    nodes_examined: int
    edges_examined: int
    hops_completed: int
    rejection_counts: tuple[tuple[str, int], ...]
    latency_ms: float
    node_budget_exhausted: bool = False
    edge_budget_exhausted: bool = False
    deadline_exceeded: bool = False
    unavailable_reason: str = ""
    hop_decay: float = HOP_DECAY

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _required_text("request_id", self.request_id))
        object.__setattr__(
            self,
            "retriever_version",
            _required_text("retriever_version", self.retriever_version),
        )
        object.__setattr__(
            self,
            "generation_id",
            _required_text("generation_id", self.generation_id),
        )
        object.__setattr__(
            self,
            "stable_version",
            _required_text("stable_version", self.stable_version),
        )
        try:
            status = GraphTraversalStatus(self.status)
        except (TypeError, ValueError) as error:
            raise ValueError("unknown graph traversal status") from error
        object.__setattr__(self, "status", status)
        for name in ("seed_count", "nodes_examined", "edges_examined", "hops_completed"):
            object.__setattr__(
                self,
                name,
                _bounded_int(name, getattr(self, name), minimum=0, maximum=1_000_000),
            )
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(float(self.latency_ms))
            or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be a finite non-negative number")
        object.__setattr__(self, "latency_ms", float(self.latency_ms))
        if self.hop_decay != HOP_DECAY:
            raise ValueError(f"hop_decay must be the published constant {HOP_DECAY}")
        if self.unavailable_reason:
            object.__setattr__(
                self,
                "unavailable_reason",
                _required_text("unavailable_reason", self.unavailable_reason),
            )
        if status is GraphTraversalStatus.DEADLINE and not self.deadline_exceeded:
            raise ValueError("deadline status requires deadline_exceeded")
        if status is GraphTraversalStatus.UNAVAILABLE and not self.unavailable_reason:
            raise ValueError("unavailable status requires unavailable_reason")
        for name in (
            "node_budget_exhausted",
            "edge_budget_exhausted",
            "deadline_exceeded",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")

    @property
    def expanded_nodes(self) -> int:
        return self.nodes_examined

    @property
    def expanded_edges(self) -> int:
        return self.edges_examined

    @property
    def graph_only_recovered(self) -> int:
        return len(self.paths)


@dataclass(frozen=True, slots=True)
class GraphTraversalResult:
    """Strict graph result pairing candidates with the exact paths in its trace."""

    candidates: tuple[GraphTraversalCandidate, ...]
    trace: GraphTraversalTrace

    def __post_init__(self) -> None:
        ranks = [item.candidate.within_source_rank for item in self.candidates]
        if ranks != list(range(len(ranks))):
            raise ValueError("graph candidate ranks must be contiguous and zero-based")
        paths = tuple(item.path for item in self.candidates)
        if paths != self.trace.paths:
            raise ValueError("trace paths must exactly match returned graph candidates")
        if self.trace.status is GraphTraversalStatus.NO_MATCH and self.candidates:
            raise ValueError("no_match results cannot contain candidates")
        if self.trace.status is GraphTraversalStatus.UNAVAILABLE and self.candidates:
            raise ValueError("unavailable results cannot contain candidates")
        if self.trace.status is GraphTraversalStatus.DEADLINE and self.candidates:
            raise ValueError("deadline results cannot contain candidates")
        if self.trace.status is GraphTraversalStatus.COMPLETE and not self.candidates:
            raise ValueError("complete results require at least one graph candidate")

    @property
    def code_candidates(self) -> tuple[CodeRetrievalCandidate, ...]:
        return tuple(item.candidate for item in self.candidates)


class SQLiteGraphAdjacencyProvider:
    """Read the existing ``edges``/``entities``/publication tables in SQLite.

    A filesystem path is required so every connection can use SQLite URI
    ``mode=ro``.  ``PRAGMA query_only`` adds a second guard against accidental
    writes from future maintenance.
    """

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError("database_path must identify an existing SQLite file")
        self.database_path = path
        self._uri = f"{path.as_uri()}?mode=ro"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._uri, uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def publication(
        self,
        *,
        repository_id: str,
        generation_id: str,
    ) -> GraphPublication | None:
        repository_id = _required_text("repository_id", repository_id)
        generation_id = _required_text("generation_id", generation_id)
        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT p.project_id, p.repository_id, p.generation_id,
                       p.graph, p.status AS publication_status,
                       p.validation_json, g.commit_sha,
                       g.status AS generation_status
                FROM code_index_publications p
                JOIN index_generations g
                  ON g.id=p.generation_id AND g.repository_id=p.repository_id
                JOIN repositories r
                  ON r.id=p.repository_id AND r.project_id=p.project_id
                WHERE p.repository_id=? AND p.generation_id=?
                LIMIT 1
                """,
                (repository_id, generation_id),
            ).fetchone()
        if row is None:
            return None
        try:
            validation = json.loads(str(row["validation_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            validation = None
        capabilities = validation.get("capabilities") if isinstance(validation, dict) else None
        graph_version = str(row["graph"] or "")
        reasons: list[str] = []
        if str(row["publication_status"]) != "published":
            reasons.append("graph publication is not published")
        if str(row["generation_status"]) != "published":
            reasons.append("graph generation is not published")
        if graph_version.casefold() in _NOT_BUILT:
            reasons.append("graph publication is not built")
        if not isinstance(capabilities, dict) or capabilities.get("graph_retrieval") is not True:
            reasons.append("graph retrieval capability is not published")
        return GraphPublication(
            project_id=str(row["project_id"]),
            repository_id=str(row["repository_id"]),
            generation_id=str(row["generation_id"]),
            stable_version=str(row["commit_sha"]),
            graph_version=graph_version or "not-built",
            ready=not reasons,
            unavailable_reason="; ".join(reasons),
        )

    def adjacent(
        self,
        *,
        node: GraphNode,
        directions: tuple[CodeTraversalDirection, ...],
        edge_types: tuple[CodeRelationType, ...],
        min_confidence: float,
        stable_version: str,
        generation_id: str,
        allowed_acl_refs: tuple[str, ...],
        limit: int,
    ) -> Sequence[GraphEdge]:
        if limit <= 0 or not directions or not edge_types or not allowed_acl_refs:
            return ()
        generation_id = _required_text("generation_id", generation_id)
        stable_version = _required_text("stable_version", stable_version)
        min_confidence = _unit_float("min_confidence", min_confidence)
        edge_types = require_registered_edge_types(edge_types)
        type_marks = ",".join("?" for _ in edge_types)
        selects: list[str] = []
        values: list[object] = []
        for direction in directions:
            direction = CodeTraversalDirection(direction)
            endpoint = "source_id" if direction is CodeTraversalDirection.OUTGOING else "target_id"
            selects.append(
                f"""
                SELECT e.*, ? AS traversal_direction
                FROM edges e
                WHERE e.repository_id=? AND e.generation_id=?
                  AND e.{endpoint}=?
                  AND e.edge_type IN ({type_marks})
                  AND e.confidence>=?
                """
            )
            values.extend(
                (
                    direction.value,
                    node.repository_id,
                    generation_id,
                    node.entity_id,
                    *(edge_type.value for edge_type in edge_types),
                    min_confidence,
                )
            )
        acl_marks = ",".join("?" for _ in allowed_acl_refs)
        query = f"""
            WITH adjacent AS (
                {" UNION ALL ".join(selects)}
            )
            SELECT adjacent.id AS edge_id, adjacent.edge_type,
                   adjacent.traversal_direction, adjacent.confidence,
                   adjacent.derivation, adjacent.evidence_locator,
                   source.id AS source_id,
                   source.repository_id AS source_repository_id,
                   source.entity_type AS source_entity_type,
                   source.commit_sha AS source_version,
                   source.generation_id AS source_generation,
                   source.source_uri AS source_locator,
                   source.acl_ref AS source_acl_ref,
                   source_unit.id AS source_unit_id,
                   coalesce(source_unit.token_count, 0) AS source_token_count,
                   target.id AS target_id,
                   target.repository_id AS target_repository_id,
                   target.entity_type AS target_entity_type,
                   target.commit_sha AS target_version,
                   target.generation_id AS target_generation,
                   target.source_uri AS target_locator,
                   target.acl_ref AS target_acl_ref,
                   target_unit.id AS target_unit_id,
                   coalesce(target_unit.token_count, 0) AS target_token_count
            FROM adjacent
            JOIN entities source
              ON source.id=adjacent.source_id
             AND source.generation_id=adjacent.generation_id
             AND source.repository_id=adjacent.repository_id
            JOIN entities target
              ON target.id=adjacent.target_id
             AND target.generation_id=adjacent.generation_id
             AND target.repository_id=adjacent.repository_id
            LEFT JOIN code_retrieval_units source_unit
              ON source_unit.rowid=(
                SELECT unit.rowid
                FROM code_retrieval_units unit
                WHERE unit.entity_id=source.id
                  AND unit.repository_id=source.repository_id
                  AND unit.generation_id=source.generation_id
                  AND unit.acl_ref=source.acl_ref
                  AND unit.quality_status='ready'
                ORDER BY CASE WHEN unit.parent_unit_id IS NULL THEN 0 ELSE 1 END,
                         unit.ordinal, unit.start_byte, unit.id
                LIMIT 1
              )
            LEFT JOIN code_retrieval_units target_unit
              ON target_unit.rowid=(
                SELECT unit.rowid
                FROM code_retrieval_units unit
                WHERE unit.entity_id=target.id
                  AND unit.repository_id=target.repository_id
                  AND unit.generation_id=target.generation_id
                  AND unit.acl_ref=target.acl_ref
                  AND unit.quality_status='ready'
                ORDER BY CASE WHEN unit.parent_unit_id IS NULL THEN 0 ELSE 1 END,
                         unit.ordinal, unit.start_byte, unit.id
                LIMIT 1
              )
            WHERE source.commit_sha=? AND target.commit_sha=?
              AND source.acl_ref IN ({acl_marks})
              AND target.acl_ref IN ({acl_marks})
            ORDER BY adjacent.confidence DESC, adjacent.edge_type,
                     adjacent.id, adjacent.traversal_direction,
                     source.id, target.id
            LIMIT ?
        """
        values.extend(
            (
                stable_version,
                stable_version,
                *allowed_acl_refs,
                *allowed_acl_refs,
                limit,
            )
        )
        with closing(self._connect()) as db:
            rows = db.execute(query, values).fetchall()
        edges: list[GraphEdge] = []
        for row in rows:
            edge = _sqlite_edge(row)
            if edge is not None:
                edges.append(edge)
        return tuple(edges)


@dataclass(frozen=True, slots=True)
class _PathState:
    seed_retrieval_unit_id: str
    seed_rank: int
    nodes: tuple[GraphNode, ...]
    hops: tuple[CodeRelationHop, ...]
    edge_ids: tuple[str, ...]
    score: float

    @property
    def terminal(self) -> GraphNode:
        return self.nodes[-1]

    @property
    def semantic_nodes(self) -> frozenset[tuple[str, str, str, str, str]]:
        return frozenset(node.semantic_identity() for node in self.nodes)


class CodeTypedGraphRetriever:
    """Execute deterministic beam traversal over an authorized adjacency provider."""

    retriever_version = GRAPH_RETRIEVER_VERSION

    def __init__(
        self,
        provider: GraphAdjacencyProvider,
        *,
        clock: Callable[[], float] = time.monotonic,
        latency_clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not callable(clock) or not callable(latency_clock):
            raise TypeError("clock and latency_clock must be callable")
        self.provider = provider
        self.clock = clock
        self.latency_clock = latency_clock

    def search(self, request: GraphTraversalRequest) -> tuple[CodeRetrievalCandidate, ...]:
        return self.search_with_trace(request).code_candidates

    def traverse(self, request: GraphTraversalRequest) -> GraphTraversalResult:
        return self.search_with_trace(request)

    def search_with_trace(self, request: GraphTraversalRequest) -> GraphTraversalResult:
        started = self.latency_clock()
        if not isinstance(request, GraphTraversalRequest):
            raise TypeError("request must be a GraphTraversalRequest")
        request_id = request.fingerprint()
        if not request.seed_candidates or request.max_hops == 0 or request.candidate_budget == 0:
            return self._result(
                request,
                started=started,
                request_id=request_id,
                status=GraphTraversalStatus.NO_MATCH,
            )
        if self._deadline_reached(request):
            return self._result(
                request,
                started=started,
                request_id=request_id,
                status=GraphTraversalStatus.DEADLINE,
                deadline_exceeded=True,
            )

        repositories = tuple(sorted({seed.repository_id for seed in request.seed_candidates}))
        publications: list[GraphPublication] = []
        try:
            for repository_id in repositories:
                publication = self.provider.publication(
                    repository_id=repository_id,
                    generation_id=request.generation_id,
                )
                reason = _publication_reason(publication, request, repository_id)
                if reason:
                    return self._result(
                        request,
                        started=started,
                        request_id=request_id,
                        status=GraphTraversalStatus.UNAVAILABLE,
                        publications=publications,
                        unavailable_reason=reason,
                    )
                assert publication is not None
                publications.append(publication)
        except Exception as error:
            return self._result(
                request,
                started=started,
                request_id=request_id,
                status=GraphTraversalStatus.UNAVAILABLE,
                publications=publications,
                unavailable_reason=(f"graph publication lookup failed: {type(error).__name__}"),
            )

        frontier = tuple(_seed_state(seed) for seed in request.seed_candidates)
        discovered: set[tuple[str, str, str, str, str]] = set()
        selected_paths: dict[tuple[str, str], _PathState] = {}
        edge_identities: dict[str, tuple[object, ...]] = {}
        rejections: Counter[str] = Counter()
        edges_examined = 0
        hops_completed = 0
        node_budget_exhausted = False
        edge_budget_exhausted = False
        deadline_exceeded = False

        try:
            for hop_number in range(1, request.max_hops + 1):
                if self._deadline_reached(request):
                    deadline_exceeded = True
                    break
                expansions: list[_PathState] = []
                for state in sorted(frontier, key=_state_sort_key):
                    if edges_examined >= request.edge_budget:
                        edge_budget_exhausted = True
                        break
                    if self._deadline_reached(request):
                        deadline_exceeded = True
                        break
                    remaining = request.edge_budget - edges_examined
                    adjacent = self.provider.adjacent(
                        node=state.terminal,
                        directions=request.directions,
                        edge_types=request.edge_types,
                        min_confidence=request.min_confidence,
                        stable_version=request.stable_version,
                        generation_id=request.generation_id,
                        allowed_acl_refs=request.allowed_acl_refs,
                        limit=remaining,
                    )
                    if self._deadline_reached(request):
                        deadline_exceeded = True
                        break
                    for edge in sorted(tuple(adjacent), key=_edge_sort_key):
                        if edges_examined >= request.edge_budget:
                            edge_budget_exhausted = True
                            break
                        if self._deadline_reached(request):
                            deadline_exceeded = True
                            break
                        edges_examined += 1
                        rejection = _edge_rejection(edge, state, request)
                        if rejection:
                            rejections[rejection] += 1
                            continue
                        identity = _stored_edge_identity(edge)
                        previous_identity = edge_identities.get(edge.edge_id)
                        if previous_identity is not None and previous_identity != identity:
                            rejections["edge_identity"] += 1
                            continue
                        edge_identities[edge.edge_id] = identity
                        next_node = edge.next_node
                        if next_node.semantic_identity() in state.semantic_nodes:
                            rejections["cycle"] += 1
                            continue
                        expansions.append(_extend_state(state, edge, hop_number))
                    if deadline_exceeded:
                        break
                if deadline_exceeded:
                    break
                if edges_examined >= request.edge_budget:
                    edge_budget_exhausted = True

                next_frontier: list[_PathState] = []
                seen_terminals: set[tuple[str, str, str, str, str]] = set()
                for state in sorted(expansions, key=_state_sort_key):
                    identity = state.terminal.semantic_identity()
                    if identity in seen_terminals:
                        rejections["beam_duplicate_terminal"] += 1
                        continue
                    if identity not in discovered:
                        if len(discovered) >= request.node_budget:
                            node_budget_exhausted = True
                            rejections["node_budget"] += 1
                            continue
                        discovered.add(identity)
                    seen_terminals.add(identity)
                    next_frontier.append(state)
                    if state.terminal.retrieval_unit_id is not None:
                        key = (
                            state.terminal.generation_id,
                            state.terminal.retrieval_unit_id,
                        )
                        previous = selected_paths.get(key)
                        if previous is None or _state_sort_key(state) < _state_sort_key(previous):
                            selected_paths[key] = state
                    if len(next_frontier) >= request.beam_width:
                        if len(expansions) > len(next_frontier):
                            rejections["beam_width"] += len(expansions) - len(next_frontier)
                        break
                frontier = tuple(next_frontier)
                hops_completed = hop_number
                if not frontier or edge_budget_exhausted:
                    break
        except Exception as error:
            return self._result(
                request,
                started=started,
                request_id=request_id,
                status=GraphTraversalStatus.UNAVAILABLE,
                publications=publications,
                nodes_examined=len(discovered),
                edges_examined=edges_examined,
                hops_completed=hops_completed,
                rejection_counts=rejections,
                node_budget_exhausted=node_budget_exhausted,
                edge_budget_exhausted=edge_budget_exhausted,
                deadline_exceeded=deadline_exceeded,
                unavailable_reason=f"graph adjacency lookup failed: {type(error).__name__}",
            )

        if not deadline_exceeded and self._deadline_reached(request):
            deadline_exceeded = True
        if deadline_exceeded:
            candidates = ()
            status = GraphTraversalStatus.DEADLINE
        else:
            ranked_states = sorted(selected_paths.values(), key=_candidate_state_sort_key)
            ranked_states = ranked_states[: request.candidate_budget]
            candidates = tuple(
                _graph_candidate(state, rank) for rank, state in enumerate(ranked_states)
            )
            status = GraphTraversalStatus.COMPLETE if candidates else GraphTraversalStatus.NO_MATCH
        return self._result(
            request,
            started=started,
            request_id=request_id,
            status=status,
            publications=publications,
            candidates=candidates,
            nodes_examined=len(discovered),
            edges_examined=edges_examined,
            hops_completed=hops_completed,
            rejection_counts=rejections,
            node_budget_exhausted=node_budget_exhausted,
            edge_budget_exhausted=edge_budget_exhausted,
            deadline_exceeded=deadline_exceeded,
        )

    def _deadline_reached(self, request: GraphTraversalRequest) -> bool:
        return request.deadline is not None and self.clock() >= request.deadline

    def _result(
        self,
        request: GraphTraversalRequest,
        *,
        started: float,
        request_id: str,
        status: GraphTraversalStatus,
        publications: Sequence[GraphPublication] = (),
        candidates: tuple[GraphTraversalCandidate, ...] = (),
        nodes_examined: int = 0,
        edges_examined: int = 0,
        hops_completed: int = 0,
        rejection_counts: Counter[str] | None = None,
        node_budget_exhausted: bool = False,
        edge_budget_exhausted: bool = False,
        deadline_exceeded: bool = False,
        unavailable_reason: str = "",
    ) -> GraphTraversalResult:
        trace = GraphTraversalTrace(
            request_id=request_id,
            retriever_version=self.retriever_version,
            status=status,
            generation_id=request.generation_id,
            stable_version=request.stable_version,
            publication_versions=tuple(
                f"{item.repository_id}:{item.graph_version}"
                for item in sorted(publications, key=lambda item: item.repository_id)
            ),
            seed_count=len(request.seed_candidates),
            directions=request.directions,
            edge_types=request.edge_types,
            paths=tuple(item.path for item in candidates),
            nodes_examined=nodes_examined,
            edges_examined=edges_examined,
            hops_completed=hops_completed,
            rejection_counts=tuple(sorted((rejection_counts or {}).items())),
            latency_ms=max(0.0, (self.latency_clock() - started) * 1_000.0),
            node_budget_exhausted=node_budget_exhausted,
            edge_budget_exhausted=edge_budget_exhausted,
            deadline_exceeded=deadline_exceeded,
            unavailable_reason=unavailable_reason,
        )
        return GraphTraversalResult(candidates=candidates, trace=trace)


CodeGraphRetriever = CodeTypedGraphRetriever
CodeGraphV2Retriever = CodeTypedGraphRetriever


def fuse_graph_candidates(
    hybrid_candidates: Sequence[CodeRetrievalCandidate],
    graph_candidates: Sequence[GraphTraversalCandidate | CodeRetrievalCandidate],
    *,
    limit: int,
) -> tuple[CodeRetrievalCandidate, ...]:
    """Append/merge graph evidence without displacing existing hybrid ranks.

    Existing hybrid candidates retain their relative order, locator, relation
    role, and exact/sparse/dense ranks.  A graph hit for the same retrieval unit
    adds the graph raw signal and its governed stored-edge path while retaining
    the hybrid terminal locator; graph-only candidates fill remaining slots.
    Consequently an exact hard signal can never be displaced by graph traversal.
    """

    limit = _bounded_int("limit", limit, minimum=0, maximum=10_000)
    existing = tuple(hybrid_candidates)
    if any(not isinstance(item, CodeRetrievalCandidate) for item in existing):
        raise TypeError("hybrid_candidates must contain CodeRetrievalCandidate values")
    existing = tuple(
        sorted(
            existing,
            key=lambda item: (
                item.within_source_rank,
                item.repository_id,
                item.locator,
                item.retrieval_unit_id,
            ),
        )
    )
    existing_keys = [(item.source_generation, item.retrieval_unit_id) for item in existing]
    if len(existing_keys) != len(set(existing_keys)):
        raise ValueError("hybrid_candidates must not contain duplicate retrieval units")
    graph_values = tuple(
        item.candidate if isinstance(item, GraphTraversalCandidate) else item
        for item in graph_candidates
    )
    if any(not isinstance(item, CodeRetrievalCandidate) for item in graph_values):
        raise TypeError("graph_candidates must contain typed graph candidates")
    for item in graph_values:
        channels = {score.channel for score in item.raw_channel_scores}
        if channels != {CodeRetrievalChannel.GRAPH}:
            raise ValueError("graph_candidates must be graph-only")
        if item.relation_path.edges and any(
            edge.edge_id is None for edge in item.relation_path.edges
        ):
            raise ValueError("graph candidate relation hops require stored edge identities")
    graph_values = tuple(
        sorted(
            graph_values,
            key=lambda item: (
                item.within_source_rank,
                -item.source_fused_score,
                item.repository_id,
                item.locator,
                item.retrieval_unit_id,
            ),
        )
    )
    graph_keys = [(item.source_generation, item.retrieval_unit_id) for item in graph_values]
    graph_ranks = [item.raw_channel_ranks[0].rank for item in graph_values]
    if len(graph_keys) != len(set(graph_keys)):
        raise ValueError("graph_candidates must not contain duplicate retrieval units")
    if len(graph_ranks) != len(set(graph_ranks)):
        raise ValueError("graph_candidates must not contain duplicate graph ranks")

    graph_by_key = {(item.source_generation, item.retrieval_unit_id): item for item in graph_values}
    used_graph: set[tuple[str, str]] = set()
    merged: list[CodeRetrievalCandidate] = []
    for item in existing:
        key = (item.source_generation, item.retrieval_unit_id)
        graph = graph_by_key.get(key)
        if graph is None:
            merged.append(item)
            continue
        used_graph.add(key)
        scores = {entry.channel: entry for entry in item.raw_channel_scores}
        ranks = {entry.channel: entry for entry in item.raw_channel_ranks}
        graph_score = graph.raw_channel_scores[0]
        graph_rank = graph.raw_channel_ranks[0]
        scores.setdefault(CodeRetrievalChannel.GRAPH, graph_score)
        ranks.setdefault(CodeRetrievalChannel.GRAPH, graph_rank)
        payload = item.model_dump(mode="python", round_trip=True)
        payload.update(
            {
                "raw_channel_scores": tuple(scores.values()),
                "raw_channel_ranks": tuple(ranks.values()),
                "relation_path": _relation_path_for_merged_candidate(item, graph),
            }
        )
        merged.append(CodeRetrievalCandidate.model_validate(payload))

    merged.extend(
        item
        for item in graph_values
        if (item.source_generation, item.retrieval_unit_id) not in used_graph
    )
    selected = merged[:limit]
    result: list[CodeRetrievalCandidate] = []
    for rank, item in enumerate(selected):
        if item.within_source_rank == rank:
            result.append(item)
            continue
        payload = item.model_dump(mode="python", round_trip=True)
        payload["within_source_rank"] = rank
        result.append(CodeRetrievalCandidate.model_validate(payload))
    return tuple(result)


def _publication_reason(
    publication: GraphPublication | None,
    request: GraphTraversalRequest,
    repository_id: str,
) -> str:
    if publication is None:
        return f"published graph is missing for repository {repository_id}"
    if publication.project_id != request.project_id:
        return "graph publication project provenance does not match"
    if publication.repository_id != repository_id:
        return "graph publication repository provenance does not match"
    if publication.generation_id != request.generation_id:
        return "graph publication generation provenance does not match"
    if publication.stable_version != request.stable_version:
        return "graph publication stable version does not match"
    if not publication.ready:
        return publication.unavailable_reason or "graph publication is unavailable"
    return ""


def _seed_state(seed: CodeRetrievalCandidate) -> _PathState:
    terminal = seed.relation_path.nodes[-1]
    try:
        entity_type = CodeGraphEntityType(seed.entity_type)
    except (TypeError, ValueError):
        entity_type = CodeGraphEntityType.CODE_RETRIEVAL_UNIT
    node = GraphNode(
        entity_id=terminal.entity_id,
        repository_id=terminal.repository_id,
        entity_type=entity_type,
        stable_version=terminal.stable_version,
        generation_id=terminal.source_generation,
        locator=terminal.locator,
        acl_ref=terminal.acl_ref,
        retrieval_unit_id=seed.retrieval_unit_id,
        token_estimate=seed.token_estimate,
    )
    return _PathState(
        seed_retrieval_unit_id=seed.retrieval_unit_id,
        seed_rank=seed.within_source_rank,
        nodes=(node,),
        hops=(),
        edge_ids=(),
        score=1.0,
    )


def _edge_rejection(
    edge: GraphEdge,
    state: _PathState,
    request: GraphTraversalRequest,
) -> str:
    if not isinstance(edge, GraphEdge):
        return "provider_type"
    if edge.edge_type not in request.edge_types:
        return "edge_type"
    if edge.traversal_direction not in request.directions:
        return "direction"
    if edge.confidence < request.min_confidence:
        return "low_confidence"
    if edge.review_status not in {
        CodeReviewStatus.MACHINE_CONFIRMED,
        CodeReviewStatus.HUMAN_CONFIRMED,
    }:
        return "review"
    if request.task not in get_edge_spec(edge.edge_type).allowed_tasks:
        return "task"
    current = (
        edge.source if edge.traversal_direction is CodeTraversalDirection.OUTGOING else edge.target
    )
    if current.semantic_identity() != state.terminal.semantic_identity():
        return "disconnected"
    endpoints = (edge.source, edge.target)
    if any(node.repository_id != state.terminal.repository_id for node in endpoints):
        return "repository"
    if any(node.generation_id != request.generation_id for node in endpoints):
        return "generation"
    if any(node.stable_version != request.stable_version for node in endpoints):
        return "version"
    if any(node.acl_ref not in request.allowed_acl_refs for node in endpoints):
        return "acl"
    if edge.source.acl_ref != edge.target.acl_ref:
        return "acl_boundary"
    try:
        spec = validate_edge_assertion(
            edge.edge_type,
            source_type=edge.source.entity_type,
            target_type=edge.target.entity_type,
            derivation=edge.derivation_layer,
        )
    except ValueError:
        return "registry"
    if spec.version_requirement is CodeEdgeVersionRequirement.EXPLICIT_VERSION_TRANSITION:
        return "unsupported_version_transition"
    if spec.generation_requirement is CodeEdgeGenerationRequirement.EXPLICIT_GENERATIONS:
        return "unsupported_generation_transition"
    return ""


def _extend_state(
    state: _PathState,
    edge: GraphEdge,
    hop_number: int,
) -> _PathState:
    score = float(state.score * edge.confidence * HOP_DECAY)
    locator = CodeRelationLocator(
        locator=edge.locator,
        owner_entity_id=edge.source.entity_id,
        repository_id=edge.source.repository_id,
        stable_version=edge.source.stable_version,
        source_generation=edge.source.generation_id,
        acl_ref=edge.source.acl_ref,
    )
    relation_hop = CodeRelationHop(
        source_entity_id=edge.source.entity_id,
        target_entity_id=edge.target.entity_id,
        source_repository_id=edge.source.repository_id,
        target_repository_id=edge.target.repository_id,
        edge_type=edge.edge_type,
        direction=edge.traversal_direction,
        hop=hop_number,
        confidence=edge.confidence,
        source_version=edge.source.stable_version,
        target_version=edge.target.stable_version,
        source_generation=edge.source.generation_id,
        target_generation=edge.target.generation_id,
        source_acl_ref=edge.source.acl_ref,
        target_acl_ref=edge.target.acl_ref,
        derivation=edge.derivation,
        review_status=edge.review_status,
        fact_status=edge.fact_status,
        locator=locator,
        edge_id=edge.edge_id,
    )
    return _PathState(
        seed_retrieval_unit_id=state.seed_retrieval_unit_id,
        seed_rank=state.seed_rank,
        nodes=(*state.nodes, edge.next_node),
        hops=(*state.hops, relation_hop),
        edge_ids=(*state.edge_ids, edge.edge_id),
        score=score,
    )


def _state_sort_key(state: _PathState) -> tuple[object, ...]:
    return (
        -state.score,
        len(state.hops),
        state.seed_rank,
        state.edge_ids,
        tuple(node.entity_id for node in state.nodes),
        state.seed_retrieval_unit_id,
    )


def _candidate_state_sort_key(state: _PathState) -> tuple[object, ...]:
    return (
        -state.score,
        len(state.hops),
        state.seed_rank,
        state.terminal.repository_id,
        state.terminal.locator,
        state.terminal.retrieval_unit_id or "",
        state.edge_ids,
    )


def _edge_sort_key(edge: GraphEdge) -> tuple[object, ...]:
    return (
        -edge.confidence,
        _RELATION_ORDER[edge.edge_type],
        _DIRECTION_ORDER[edge.traversal_direction],
        edge.edge_id,
        edge.source.entity_id,
        edge.target.entity_id,
    )


def _stored_edge_identity(edge: GraphEdge) -> tuple[object, ...]:
    def node_identity(node: GraphNode) -> tuple[object, ...]:
        return (
            node.entity_id,
            node.repository_id,
            node.entity_type.value,
            node.stable_version,
            node.generation_id,
            node.locator,
            node.acl_ref,
        )

    return (
        edge.edge_type.value,
        node_identity(edge.source),
        node_identity(edge.target),
        edge.confidence,
        edge.derivation_layer.value,
        edge.derivation.value,
        edge.review_status.value,
        edge.fact_status.value,
        edge.locator,
    )


def _relation_path_for_merged_candidate(
    hybrid: CodeRetrievalCandidate,
    graph: CodeRetrievalCandidate,
) -> CodeRelationPath:
    graph_path = graph.relation_path
    if not graph_path.edges:
        return hybrid.relation_path
    hybrid_identity = (
        hybrid.entity_id,
        hybrid.retrieval_unit_id,
        hybrid.repository_id,
        hybrid.stable_version,
        hybrid.source_generation,
        hybrid.acl_ref,
    )
    graph_identity = (
        graph.entity_id,
        graph.retrieval_unit_id,
        graph.repository_id,
        graph.stable_version,
        graph.source_generation,
        graph.acl_ref,
    )
    if hybrid_identity != graph_identity:
        raise ValueError("hybrid and graph candidate provenance does not match")
    nodes = list(graph_path.nodes)
    terminal = nodes[-1]
    terminal_payload = terminal.model_dump(mode="python", round_trip=True)
    terminal_payload["locator"] = hybrid.locator
    nodes[-1] = CodeRelationNode.model_validate(terminal_payload)
    path_payload = graph_path.model_dump(mode="python", round_trip=True)
    path_payload["nodes"] = tuple(nodes)
    return CodeRelationPath.model_validate(path_payload)


def _role_for(state: _PathState) -> CodeCandidateRole:
    terminal_type = state.terminal.entity_type
    edge_type = state.hops[-1].edge_type
    if terminal_type in {
        CodeGraphEntityType.TEST_CASE,
        CodeGraphEntityType.TEST_RESULT,
        CodeGraphEntityType.COVERAGE,
        CodeGraphEntityType.VALIDATION_TARGET,
    }:
        return CodeCandidateRole.TEST
    if terminal_type in {
        CodeGraphEntityType.COMMIT,
        CodeGraphEntityType.GIT_COMMIT,
        CodeGraphEntityType.WORKTREE,
        CodeGraphEntityType.DIFF_HUNK,
        CodeGraphEntityType.CHANGE_SET,
    } or edge_type in {
        CodeRelationType.SAME_SYMBOL_AS,
        CodeRelationType.RENAMED_TO,
        CodeRelationType.MOVED_TO,
    }:
        return CodeCandidateRole.HISTORY
    if edge_type in {
        CodeRelationType.IMPORTS,
        CodeRelationType.CALLS,
        CodeRelationType.REFERENCES,
    }:
        return CodeCandidateRole.DEPENDENCY
    return CodeCandidateRole.TARGET


def _path_explanation(state: _PathState) -> str:
    hops = []
    for edge_id, edge in zip(state.edge_ids, state.hops, strict=True):
        hops.append(
            f"hop {edge.hop} {edge.direction.value} {edge.edge_type.value} "
            f"{edge.source_entity_id}->{edge.target_entity_id} "
            f"confidence={edge.confidence:.6f} edge={edge_id} "
            f"locator={edge.locator.locator}"
        )
    return (
        f"seed={state.seed_retrieval_unit_id}; "
        + "; ".join(hops)
        + f"; path_score=product(confidence)*{HOP_DECAY:.2f}^hops"
        f"={state.score:.12f}"
    )


def _graph_candidate(state: _PathState, rank: int) -> GraphTraversalCandidate:
    terminal = state.terminal
    assert terminal.retrieval_unit_id is not None
    relation_path = CodeRelationPath(
        nodes=tuple(node.relation_node() for node in state.nodes),
        edges=state.hops,
        path_score=state.score,
    )
    terminal_edge = state.hops[-1]
    role = _role_for(state)
    explanation = _path_explanation(state)
    candidate = CodeRetrievalCandidate(
        entity_id=terminal.entity_id,
        retrieval_unit_id=terminal.retrieval_unit_id,
        repository_id=terminal.repository_id,
        entity_type=terminal.entity_type.value,
        stable_version=terminal.stable_version,
        source_generation=terminal.generation_id,
        raw_channel_scores=(
            CodeChannelScore(
                channel=CodeRetrievalChannel.GRAPH,
                score=state.score,
            ),
        ),
        raw_channel_ranks=(
            CodeChannelRank(
                channel=CodeRetrievalChannel.GRAPH,
                rank=rank,
            ),
        ),
        within_source_rank=rank,
        source_fused_score=state.score,
        calibrated_relevance=CodeUncalibratedScore(
            status="unavailable",
            reason="graph path scores are deterministic relation scores, not probabilities",
        ),
        version_alignment=CodeVersionAlignment.EXACT,
        fact_status=terminal_edge.fact_status,
        derivation=terminal_edge.derivation,
        review_status=terminal_edge.review_status,
        role=role,
        relation_path=relation_path,
        locator=terminal.locator,
        token_estimate=terminal.token_estimate,
        acl_ref=terminal.acl_ref,
    )
    path = GraphTraversalPath(
        seed_retrieval_unit_id=state.seed_retrieval_unit_id,
        edge_ids=state.edge_ids,
        relation_path=relation_path,
        explanation=explanation,
    )
    return GraphTraversalCandidate(
        candidate=candidate,
        path=path,
        explanation=f"graph-only {role.value} candidate; {explanation}",
    )


def _sqlite_edge(row: sqlite3.Row) -> GraphEdge | None:
    derivation = _stored_derivation(str(row["derivation"] or ""))
    if derivation is None:
        return None
    layer, producer, review_status, fact_status = derivation
    try:
        source = GraphNode(
            entity_id=str(row["source_id"]),
            repository_id=str(row["source_repository_id"]),
            entity_type=CodeGraphEntityType(str(row["source_entity_type"])),
            stable_version=str(row["source_version"]),
            generation_id=str(row["source_generation"]),
            locator=str(row["source_locator"]),
            acl_ref=str(row["source_acl_ref"]),
            retrieval_unit_id=(
                str(row["source_unit_id"]) if row["source_unit_id"] is not None else None
            ),
            token_estimate=int(row["source_token_count"]),
        )
        target = GraphNode(
            entity_id=str(row["target_id"]),
            repository_id=str(row["target_repository_id"]),
            entity_type=CodeGraphEntityType(str(row["target_entity_type"])),
            stable_version=str(row["target_version"]),
            generation_id=str(row["target_generation"]),
            locator=str(row["target_locator"]),
            acl_ref=str(row["target_acl_ref"]),
            retrieval_unit_id=(
                str(row["target_unit_id"]) if row["target_unit_id"] is not None else None
            ),
            token_estimate=int(row["target_token_count"]),
        )
        return GraphEdge(
            edge_id=str(row["edge_id"]),
            edge_type=CodeRelationType(str(row["edge_type"])),
            traversal_direction=CodeTraversalDirection(str(row["traversal_direction"])),
            source=source,
            target=target,
            confidence=float(row["confidence"]),
            derivation_layer=layer,
            derivation=producer,
            review_status=review_status,
            fact_status=fact_status,
            locator=str(row["evidence_locator"] or row["source_locator"]),
        )
    except (TypeError, ValueError):
        return None


def _stored_derivation(
    value: str,
) -> (
    tuple[
        CodeEdgeDerivationLayer,
        CodeDerivation,
        CodeReviewStatus,
        CodeFactStatus,
    ]
    | None
):
    normalized = value.strip().casefold().replace("-", "_")
    if normalized in {"deterministic", "rule"}:
        return (
            CodeEdgeDerivationLayer.DETERMINISTIC,
            CodeDerivation.RULE,
            CodeReviewStatus.MACHINE_CONFIRMED,
            CodeFactStatus.OBSERVED,
        )
    if normalized in {"static", "static_analysis", "tree_sitter"}:
        return (
            CodeEdgeDerivationLayer.STATIC,
            CodeDerivation.TREE_SITTER,
            CodeReviewStatus.MACHINE_CONFIRMED,
            CodeFactStatus.MACHINE_CONFIRMED,
        )
    if normalized in {"scip", "lsp", "compiler", "coverage"}:
        producer = {
            "scip": CodeDerivation.SCIP,
            "lsp": CodeDerivation.LSP,
            "compiler": CodeDerivation.COMPILER,
            "coverage": CodeDerivation.COVERAGE,
        }[normalized]
        return (
            CodeEdgeDerivationLayer.STATIC,
            producer,
            CodeReviewStatus.MACHINE_CONFIRMED,
            CodeFactStatus.MACHINE_CONFIRMED,
        )
    if normalized in {"semantic", "vector"}:
        return (
            CodeEdgeDerivationLayer.SEMANTIC,
            CodeDerivation.VECTOR,
            CodeReviewStatus.UNREVIEWED,
            CodeFactStatus.INFERRED,
        )
    if normalized == "human":
        return (
            CodeEdgeDerivationLayer.HUMAN,
            CodeDerivation.HUMAN,
            CodeReviewStatus.HUMAN_CONFIRMED,
            CodeFactStatus.REPORTED,
        )
    return None


__all__ = [
    "GRAPH_FUSION_POLICY",
    "GRAPH_RETRIEVER_VERSION",
    "HOP_DECAY",
    "CodeGraphRetriever",
    "CodeGraphV2Retriever",
    "CodeTypedGraphRetriever",
    "GraphAdjacencyProvider",
    "GraphEdge",
    "GraphNode",
    "GraphPublication",
    "GraphTraversalCandidate",
    "GraphTraversalPath",
    "GraphTraversalRequest",
    "GraphTraversalResult",
    "GraphTraversalStatus",
    "GraphTraversalTrace",
    "SQLiteGraphAdjacencyProvider",
    "fuse_graph_candidates",
]
