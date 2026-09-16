"""Deterministic Notebook source-local reranker."""

from __future__ import annotations

from dataclasses import dataclass

NOTEBOOK_RERANKER_VERSION = "notebook-reranker-v2"


@dataclass(frozen=True, slots=True)
class NotebookRerankFeatures:
    exact_match: bool
    token_overlap: float
    task_match: bool
    task_priority: float
    metadata_overlap: float
    graph_distance: int | None
    current_revision: bool
    stale: bool
    unexecuted: bool
    markdown_only: bool
    metric_unconfirmed: bool
    task: str


@dataclass(frozen=True, slots=True)
class NotebookRerankResult:
    score: float
    reasons: tuple[str, ...]


def rerank_notebook_candidate_v2(
    features: NotebookRerankFeatures,
) -> NotebookRerankResult:
    score = 0.0
    reasons: list[str] = []
    if features.exact_match:
        score += 3.0
        reasons.append("exact")
    if features.token_overlap > 0:
        overlap = min(1.0, max(0.0, features.token_overlap))
        score += 2.0 * overlap
        reasons.append("lexical")
    if features.task_match:
        score += 2.5 * min(1.0, max(0.0, features.task_priority))
        reasons.append("task-profile")
    if features.metadata_overlap > 0:
        score += 1.5 * min(1.0, max(0.0, features.metadata_overlap))
        reasons.append("governed-metadata")
    if features.graph_distance is not None:
        score += 1.0 / (1 + features.graph_distance)
        reasons.append("dependency-graph")
    if features.current_revision:
        score += 0.25
        reasons.append("current-revision")
    if features.stale:
        if features.task in {"error", "compare", "reproduction"}:
            score += 0.2
            reasons.append("stale-explicit")
        else:
            score -= 2.0
            reasons.append("stale-penalty")
    if features.unexecuted:
        if features.task in {"code", "compare"}:
            score -= 0.25
        else:
            score -= 1.0
        reasons.append("unexecuted")
    if features.markdown_only and features.task in {"code", "lineage", "parameter"}:
        score -= 0.75
        reasons.append("markdown-hard-negative")
    if features.metric_unconfirmed:
        score -= 0.5
        reasons.append("metric-unconfirmed")
    return NotebookRerankResult(round(score, 12), tuple(reasons))


__all__ = [
    "NOTEBOOK_RERANKER_VERSION",
    "NotebookRerankFeatures",
    "NotebookRerankResult",
    "rerank_notebook_candidate_v2",
]
