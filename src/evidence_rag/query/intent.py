from __future__ import annotations

import re

INTENTS = (
    "current_implementation",
    "historical_implementation",
    "change_trace",
    "rationale",
    "experiment_validation",
    "claim_verification",
    "reproduction",
    "staleness_check",
    "global_synthesis",
)


class RetrievalVocabularyExpander:
    """Add stable product vocabulary *after* algorithmic intent understanding.

    This component does not classify or decompose a question.  Its bounded aliases
    only bridge user-facing names to repository symbols for sparse recall.  Semantic
    understanding lives in ``QueryUnderstandingEngine``.
    """

    version = "retrieval-vocabulary-expansion-v1"

    def retrieval_query(self, question: str, intent: str) -> str:
        """Expand product language into stable repository retrieval vocabulary.

        The original question remains the public request.  Deterministic aliases only
        improve sparse recall for production symbols whose names differ from product
        language; they never select intent, sources, scope, or answer mode.
        """

        text = question.casefold()
        expansions: list[str] = []
        rules = (
            (
                r"智能检索|智能查询|证据检索|evidence[- ]?search",
                (
                    "UnifiedQueryService",
                    "evidence obligation",
                    "retrieval pipeline",
                    "query planner",
                ),
            ),
            (
                r"wiki|知识库|知识中枢",
                (
                    "WikiHybridSearchV1",
                    "WikiNavigatorV1",
                    "WikiRuntimeV1",
                    "wiki search navigator",
                ),
            ),
            (
                r"生产组件|生产实现|production component",
                ("runtime service implementation source adapter",),
            ),
            (r"测试|验证|test|validation", ("test validation quality gate",)),
        )
        for pattern, terms in rules:
            if re.search(pattern, text, re.I):
                expansions.extend(terms)
        if intent == "current_implementation":
            expansions.append("current implementation source runtime service")
        unique = [term for term in dict.fromkeys(expansions) if term.casefold() not in text]
        return " ".join((question, *unique)).strip()


SOURCE_AUTHORITY: dict[str, dict[str, float]] = {
    "current_implementation": {
        "code": 1.0,
        "codex": 0.55,
        "experiment": 0.65,
        "document": 0.45,
        "workspace": 0.4,
    },
    "historical_implementation": {
        "code": 1.0,
        "codex": 0.7,
        "experiment": 0.55,
        "document": 0.45,
        "workspace": 0.35,
    },
    "change_trace": {
        "code": 1.0,
        "codex": 0.9,
        "workspace": 0.6,
        "experiment": 0.5,
        "document": 0.4,
    },
    "rationale": {
        "codex": 1.0,
        "workspace": 0.85,
        "code": 0.75,
        "document": 0.65,
        "experiment": 0.5,
    },
    "experiment_validation": {
        "experiment": 1.0,
        "notebook": 0.9,
        "code": 0.75,
        "document": 0.65,
        "codex": 0.45,
        "workspace": 0.35,
    },
    "claim_verification": {
        "experiment": 1.0,
        "document": 0.9,
        "code": 0.65,
        "codex": 0.4,
        "workspace": 0.3,
    },
    "reproduction": {
        "experiment": 1.0,
        "notebook": 0.95,
        "code": 0.85,
        "codex": 0.7,
        "document": 0.55,
        "workspace": 0.35,
    },
    "staleness_check": {
        "experiment": 1.0,
        "code": 1.0,
        "document": 0.8,
        "codex": 0.45,
        "workspace": 0.3,
    },
    "global_synthesis": {
        "workspace": 0.85,
        "codex": 0.8,
        "experiment": 0.8,
        "notebook": 0.75,
        "document": 0.75,
        "code": 0.7,
    },
}


REQUIRED_ROLES: dict[str, list[str]] = {
    "current_implementation": ["current_code", "version", "tests"],
    "historical_implementation": ["historical_code", "commit"],
    "change_trace": ["diff", "commit", "development_context"],
    "rationale": ["decision_or_goal", "implementation"],
    "experiment_validation": ["run", "metric", "commit", "dataset_version"],
    "claim_verification": ["claim", "run_or_metric", "source_location"],
    "reproduction": ["run", "commit", "configuration", "dataset_version", "environment"],
    "staleness_check": [
        "claim",
        "supporting_run",
        "experiment_commit",
        "current_commit",
        "diff_or_revalidation",
    ],
    "global_synthesis": ["multiple_sources"],
}
