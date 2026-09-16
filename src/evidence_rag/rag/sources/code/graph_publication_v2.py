"""Governed publication of the typed Code graph built by production ingestion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import CodeRelationType
from .graph_retrieval_v2 import CodeTypedGraphRetriever

CODE_GRAPH_PUBLICATION_VERSION = "code-ast-v2-typed-graph-publication-v1"


class CodeGraphPublicationStore(Protocol):
    def connection(self) -> Any: ...

    def get_code_index_publication(
        self,
        generation_id: str,
        *,
        repository_id: str | None = None,
        active_only: bool = False,
    ) -> dict[str, Any] | None: ...

    def upsert_code_index_publication(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CodeGraphPublicationResult:
    repository_id: str
    generation_id: str
    edge_count: int
    registered_edge_count: int
    excluded_edge_types: tuple[tuple[str, int], ...]
    publication: dict[str, Any]


class CodeGraphProfilePublisher:
    """Validate legacy typed edges before advertising graph retrieval readiness."""

    publisher_version = CODE_GRAPH_PUBLICATION_VERSION

    def __init__(self, store: CodeGraphPublicationStore) -> None:
        self.store = store

    def publish(
        self,
        *,
        project_id: str,
        repository_id: str,
        generation_id: str,
    ) -> CodeGraphPublicationResult:
        current = self.store.get_code_index_publication(
            generation_id,
            repository_id=repository_id,
            active_only=True,
        )
        if (
            current is None
            or current.get("status") != "published"
            or current.get("project_id") != project_id
            or current.get("repository_id") != repository_id
            or current.get("generation_id") != generation_id
        ):
            raise ValueError("graph publication requires the active governed Code generation")

        registered = tuple(sorted(item.value for item in CodeRelationType))
        marks = ",".join("?" for _ in registered)
        with self.store.connection() as database:
            generation = database.execute(
                """
                SELECT g.commit_sha, g.status, r.project_id, r.active_generation_id
                FROM index_generations g
                JOIN repositories r ON r.id=g.repository_id
                WHERE g.id=? AND g.repository_id=?
                """,
                (generation_id, repository_id),
            ).fetchone()
            if (
                generation is None
                or str(generation["status"]) != "published"
                or str(generation["project_id"]) != project_id
                or str(generation["active_generation_id"]) != generation_id
            ):
                raise ValueError("graph publication generation is not active and published")

            invalid_endpoints = int(
                database.execute(
                    f"""
                    SELECT count(*)
                    FROM edges edge
                    LEFT JOIN entities source
                      ON source.id=edge.source_id
                     AND source.repository_id=edge.repository_id
                     AND source.generation_id=edge.generation_id
                    LEFT JOIN entities target
                      ON target.id=edge.target_id
                     AND target.repository_id=edge.repository_id
                     AND target.generation_id=edge.generation_id
                    WHERE edge.repository_id=? AND edge.generation_id=?
                      AND edge.edge_type IN ({marks})
                      AND (
                        source.id IS NULL OR target.id IS NULL
                        OR source.project_id != ?
                        OR target.project_id != ?
                        OR source.commit_sha != ?
                        OR target.commit_sha != ?
                        OR source.acl_ref != target.acl_ref
                      )
                    """,
                    (
                        repository_id,
                        generation_id,
                        *registered,
                        project_id,
                        project_id,
                        str(generation["commit_sha"]),
                        str(generation["commit_sha"]),
                    ),
                ).fetchone()[0]
            )
            if invalid_endpoints:
                raise ValueError("typed graph contains cross-scope, version, or ACL endpoints")

            edge_count = int(
                database.execute(
                    "SELECT count(*) FROM edges WHERE repository_id=? AND generation_id=?",
                    (repository_id, generation_id),
                ).fetchone()[0]
            )
            registered_edge_count = int(
                database.execute(
                    f"""
                    SELECT count(*) FROM edges
                    WHERE repository_id=? AND generation_id=?
                      AND edge_type IN ({marks})
                    """,
                    (repository_id, generation_id, *registered),
                ).fetchone()[0]
            )
            excluded = tuple(
                (str(row["edge_type"]), int(row["edge_count"]))
                for row in database.execute(
                    f"""
                    SELECT edge_type, count(*) AS edge_count
                    FROM edges
                    WHERE repository_id=? AND generation_id=?
                      AND edge_type NOT IN ({marks})
                    GROUP BY edge_type
                    ORDER BY edge_type
                    """,
                    (repository_id, generation_id, *registered),
                ).fetchall()
            )

        validation = dict(current.get("validation") or {})
        capabilities = dict(validation.get("capabilities") or {})
        capabilities["graph_retrieval"] = True
        validation["capabilities"] = capabilities
        validation["graph_publication"] = {
            "schema_version": CODE_GRAPH_PUBLICATION_VERSION,
            "retriever_class": CodeTypedGraphRetriever.__name__,
            "retriever_version": CodeTypedGraphRetriever.retriever_version,
            "source": "production-ingestion-edges",
            "edge_count": edge_count,
            "registered_edge_count": registered_edge_count,
            "excluded_unregistered_edge_types": dict(excluded),
            "registry_closed": True,
            "endpoint_project_version_acl_integrity": True,
        }
        publication = self.store.upsert_code_index_publication(
            {
                "generation_id": generation_id,
                "repository_id": repository_id,
                "project_id": project_id,
                "builder": current["builder"],
                "sparse": current["sparse"],
                "embedding": current["embedding"],
                "graph": CODE_GRAPH_PUBLICATION_VERSION,
                "status": "published",
                "validation": validation,
            }
        )
        return CodeGraphPublicationResult(
            repository_id=repository_id,
            generation_id=generation_id,
            edge_count=edge_count,
            registered_edge_count=registered_edge_count,
            excluded_edge_types=excluded,
            publication=publication,
        )


__all__ = [
    "CODE_GRAPH_PUBLICATION_VERSION",
    "CodeGraphProfilePublisher",
    "CodeGraphPublicationResult",
    "CodeGraphPublicationStore",
]
