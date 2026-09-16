from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from .intent import INTENTS, REQUIRED_ROLES
from .interaction_v2 import assert_outbound_evidence_safe, evidence_content_findings
from .provider import ResolvedLLMProvider, provider_chat_options

QUERY_UNDERSTANDING_VERSION = "query-understanding-hybrid-v2"
QUERY_FUSION_VERSION = "bounded-multi-query-rrf-v1"
_SOURCES = ("code", "codex", "workspace", "experiment", "notebook", "document")
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/[^\s]+|[A-Za-z]:[\\/][^\s]+|\\\\[^\s]+)")
_SECRET = re.compile(
    r"(?:api[_-]?key|authorization|bearer|password|secret)\s*[:=]\s*[^\s]+",
    re.IGNORECASE,
)

_INTENT_EXAMPLES: dict[str, tuple[str, ...]] = {
    "current_implementation": (
        "当前系统是如何实现查询规划和证据检索的",
        "定位生产运行时使用的服务类和调用入口",
        "show the current implementation and runtime component",
        "最新版代码中这个接口的调用链是什么",
    ),
    "historical_implementation": (
        "旧版本当时如何实现这个功能",
        "查看这个模块在历史提交中的状态",
        "how did the previous implementation work",
        "比较早期版本和当前版本的代码",
    ),
    "change_trace": (
        "这次代码变更影响了哪些模块",
        "谁在什么时候修改了这段逻辑",
        "trace the commit diff and resulting impact",
        "从会话决策追踪到补丁和验证结果",
    ),
    "rationale": (
        "为什么选择这个技术方案",
        "解释架构决策的原因和权衡",
        "what rationale led to this implementation",
        "方案替换背后的目标和限制是什么",
    ),
    "experiment_validation": (
        "比较实验指标并判断是否优于基线",
        "这个实验结果是否有足够样本支持",
        "validate the run metrics against the baseline",
        "核对数据集版本种子和实验结果",
    ),
    "claim_verification": (
        "验证报告中的结论是否有原始证据",
        "这个主张的引用和支持实验在哪里",
        "verify the claim with citations and evidence",
        "核对论文表格中的结论是否可信",
    ),
    "reproduction": (
        "如何复现实验并得到相同结果",
        "给出运行环境配置数据版本和随机种子",
        "how can I reproduce this result exactly",
        "复现需要哪个提交命令和依赖环境",
    ),
    "staleness_check": (
        "这个结论在当前版本是否仍然有效",
        "检查文档证据是否已经过时",
        "is this claim still valid on the latest commit",
        "新版本是否使以前的实验结论失效",
    ),
    "global_synthesis": (
        "总结整个项目的主要技术路线",
        "跨代码会话实验和文档综合分析",
        "synthesize patterns across all evidence sources",
        "给出全局趋势风险和未解决问题",
    ),
}

_SOURCE_EXAMPLES: dict[str, tuple[str, ...]] = {
    "code": (
        "源码函数类模块仓库提交符号调用链",
        "implementation source code symbol commit diff",
        "查看生产代码和测试实现",
    ),
    "codex": (
        "研发会话中的决策命令补丁验证",
        "codex session turn agent message patch",
        "追踪智能体开发过程",
    ),
    "workspace": (
        "项目任务里程碑研究主题迭代",
        "workspace topic task iteration milestone",
        "查看计划状态和工作项",
    ),
    "experiment": (
        "实验运行指标基线数据集种子",
        "experiment run metric seed dataset baseline",
        "比较实验结果和统计数据",
    ),
    "notebook": (
        "notebook cell输出错误参数图表分析",
        "ipynb code cell output error execution",
        "查看笔记本中的计算过程",
    ),
    "document": (
        "文档论文报告章节主张引用图表",
        "document paper report claim citation section",
        "核对文字材料和来源位置",
    ),
}

_ROLE_QUERY_HINTS = {
    "current_code": "当前代码 实现入口 调用链",
    "historical_code": "历史代码 旧版实现",
    "version": "版本 commit generation",
    "tests": "测试 验证 通过失败",
    "commit": "commit 提交版本",
    "diff": "diff 变更影响",
    "development_context": "研发会话 决策背景",
    "decision_or_goal": "决策 目标 权衡",
    "implementation": "具体实现 生产组件",
    "run": "实验 run 状态",
    "metric": "指标 metric 分母",
    "dataset_version": "数据集 版本",
    "claim": "主张 结论",
    "run_or_metric": "实验运行 指标",
    "source_location": "原始来源 定位",
    "configuration": "配置 参数",
    "environment": "运行环境 依赖",
    "supporting_run": "支持实验 run",
    "experiment_commit": "实验对应 commit",
    "current_commit": "当前 commit",
    "diff_or_revalidation": "版本差异 重新验证",
    "multiple_sources": "跨来源 证据一致性",
}
_KNOWN_ROLES = frozenset(role for roles in REQUIRED_ROLES.values() for role in roles)


def _safe_public_text(value: str) -> str:
    return "[REDACTED_SENSITIVE_QUERY]" if evidence_content_findings(value) else value


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def _features(value: str) -> Counter[str]:
    value = _normalized(value).casefold()
    ascii_terms = re.findall(r"[a-z0-9_./:+-]{2,}", value)
    ascii_bigrams = [
        f"{left}::{right}" for left, right in zip(ascii_terms, ascii_terms[1:], strict=False)
    ]
    han = "".join(re.findall(r"[\u3400-\u9fff]", value))
    han_bigrams = [f"c2:{han[index : index + 2]}" for index in range(len(han) - 1)]
    han_trigrams = [f"c3:{han[index : index + 3]}" for index in range(len(han) - 2)]
    return Counter([*ascii_terms, *ascii_bigrams, *han_bigrams, *han_trigrams])


def _cosine(left: dict[str, float] | Counter[str], right: dict[str, float] | Counter[str]) -> float:
    numerator = sum(value * right.get(key, 0) for key, value in left.items())
    if not numerator:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


class _FrozenCentroidClassifier:
    """Small deterministic TF-IDF centroid model for offline query understanding.

    This is deliberately data-driven rather than an ordered keyword rule list.  The
    checked-in labelled utterances form the frozen training set; word and Unicode
    character n-grams provide useful Chinese/English similarity without a network or
    model download.  An external structured model remains the preferred planner.
    """

    def __init__(self, examples: dict[str, tuple[str, ...]]) -> None:
        self.labels = tuple(examples)
        documents = [_features(example) for values in examples.values() for example in values]
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(document)
        count = len(documents)
        self.idf = {
            token: math.log((count + 1) / (frequency + 1)) + 1.0
            for token, frequency in document_frequency.items()
        }
        centroids: dict[str, dict[str, float]] = {}
        for label, values in examples.items():
            aggregate: defaultdict[str, float] = defaultdict(float)
            for value in values:
                vector = self.vector(value)
                for token, weight in vector.items():
                    aggregate[token] += weight / len(values)
            centroids[label] = dict(aggregate)
        self.centroids = centroids

    def vector(self, value: str) -> dict[str, float]:
        counts = _features(value)
        return {
            token: (1.0 + math.log(frequency)) * self.idf.get(token, 1.0)
            for token, frequency in counts.items()
        }

    def probabilities(self, value: str, *, temperature: float = 0.18) -> list[tuple[str, float]]:
        vector = self.vector(value)
        similarities = {
            label: _cosine(vector, centroid) for label, centroid in self.centroids.items()
        }
        maximum = max(similarities.values(), default=0.0)
        exponentials = {
            label: math.exp((score - maximum) / temperature)
            for label, score in similarities.items()
        }
        denominator = sum(exponentials.values()) or 1.0
        return sorted(
            ((label, value / denominator) for label, value in exponentials.items()),
            key=lambda item: (-item[1], item[0]),
        )


_INTENT_CLASSIFIER = _FrozenCentroidClassifier(_INTENT_EXAMPLES)
_SOURCE_CLASSIFIER = _FrozenCentroidClassifier(_SOURCE_EXAMPLES)


def _normalized_entropy(probabilities: Iterable[float]) -> float:
    values = [value for value in probabilities if value > 0]
    if len(values) <= 1:
        return 0.0
    return -sum(value * math.log(value) for value in values) / math.log(len(values))


def _complexity(question: str, *, source_probabilities: Iterable[float]) -> float:
    clauses = len(re.findall(r"[？?。；;]|以及|并且|同时|然后|但是|对比|比较", question))
    entity_count = len(re.findall(r"[A-Za-z_][A-Za-z0-9_.:/-]{2,}", question))
    source_entropy = _normalized_entropy(source_probabilities)
    return min(1.0, 0.12 * clauses + 0.06 * entity_count + 0.55 * source_entropy)


def _mmr_subqueries(
    question: str,
    *,
    clauses: Iterable[str],
    entities: Iterable[str],
    required_roles: Iterable[str],
    limit: int = 4,
) -> tuple[str, ...]:
    normalized = _normalized(question)
    query_key = normalized.rstrip(" ，,。；;、？！?!")
    clause_values = list(
        dict.fromkeys(
            clause
            for value in clauses
            if (clause := _normalized(value)) and clause.rstrip(" ，,。；;、？！?!") != query_key
        )
    )
    candidates = [normalized]
    candidates.extend(clause for clause in clause_values if len(clause) >= 3)
    entity_values = list(entities)[:4]
    # Short, single-focus questions stay one-shot.  The local planner expands only
    # compound questions; the structured model may still decompose semantically.
    if len(clause_values) >= 2:
        for role in list(required_roles)[:4]:
            hint = _ROLE_QUERY_HINTS.get(role)
            if not hint:
                continue
            focus = " ".join(entity_values) if entity_values else normalized
            candidates.append(_normalized(f"{focus} {hint}"))
    candidates = list(dict.fromkeys(value for value in candidates if 3 <= len(value) <= 500))
    if len(candidates) <= limit:
        return tuple(candidates)
    vectors = {candidate: _INTENT_CLASSIFIER.vector(candidate) for candidate in candidates}
    query_vector = vectors[normalized]
    selected = [normalized]
    remaining = candidates[1:]
    while remaining and len(selected) < limit:
        candidate = max(
            remaining,
            key=lambda value: (
                0.72 * _cosine(vectors[value], query_vector)
                - 0.28 * max(_cosine(vectors[value], vectors[current]) for current in selected),
                -len(value),
                value,
            ),
        )
        selected.append(candidate)
        remaining.remove(candidate)
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class QueryUnderstanding:
    strategy: str
    normalized_query: str
    intent: str
    intent_confidence: float
    source_hints: tuple[str, ...]
    required_roles: tuple[str, ...]
    subqueries: tuple[str, ...]
    entities: tuple[str, ...]
    temporal_scope: str | None
    ambiguity: tuple[str, ...]
    intent_scores: tuple[tuple[str, float], ...] = ()
    source_scores: tuple[tuple[str, float], ...] = ()
    uncertainty: float = 1.0
    complexity: float = 0.0
    decomposition_strategy: str = "evidence_obligation_mmr"
    planner_model: str | None = None
    fallback_reason: str | None = None

    @property
    def content_digest(self) -> str:
        payload = json.dumps(self.public(include_queries=True), ensure_ascii=False, sort_keys=True)
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    def public(self, *, include_queries: bool) -> dict[str, Any]:
        sensitive_query = bool(evidence_content_findings(self.normalized_query))
        value: dict[str, Any] = {
            "contract_version": QUERY_UNDERSTANDING_VERSION,
            "strategy": self.strategy,
            "intent": self.intent,
            "intent_confidence": round(self.intent_confidence, 4),
            "source_hints": list(self.source_hints),
            "required_roles": list(self.required_roles),
            "entities": [] if sensitive_query else list(self.entities),
            "temporal_scope": self.temporal_scope,
            "ambiguity": list(self.ambiguity),
            "fallback_reason": self.fallback_reason,
            "intent_distribution": {
                label: round(score, 4) for label, score in self.intent_scores[:5]
            },
            "source_distribution": {label: round(score, 4) for label, score in self.source_scores},
            "algorithm": {
                "version": QUERY_UNDERSTANDING_VERSION,
                "encoder": (
                    "structured_llm_plus_tfidf_prior"
                    if self.strategy == "llm_structured_hybrid"
                    else "frozen_tfidf_word_char_ngram"
                ),
                "classifier": "calibrated_centroid_softmax",
                "uncertainty": round(self.uncertainty, 4),
                "complexity": round(self.complexity, 4),
                "decomposition": self.decomposition_strategy,
                "planner_model": self.planner_model,
            },
        }
        if include_queries:
            value["normalized_query"] = _safe_public_text(self.normalized_query)
            value["subqueries"] = [_safe_public_text(item) for item in self.subqueries]
        value["content_digest"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
        )
        return value


class QueryUnderstandingEngine:
    def local(
        self,
        question: str,
        *,
        explicit_intent: str | None,
        requested_sources: Iterable[str],
        fallback_reason: str | None = None,
    ) -> QueryUnderstanding:
        normalized = _normalized(question)
        ranked_intents = _INTENT_CLASSIFIER.probabilities(normalized)
        if explicit_intent in INTENTS:
            intent = explicit_intent
            confidence = 1.0
            ranked_intents = [
                (label, 1.0 if label == explicit_intent else 0.0) for label in INTENTS
            ]
        else:
            intent, confidence = ranked_intents[0]
        allowed_sources = tuple(
            dict.fromkeys(source for source in requested_sources if source in _SOURCES)
        )
        ranked_sources = _SOURCE_CLASSIFIER.probabilities(normalized)
        source_hints = tuple(
            source
            for source, score in ranked_sources
            if score >= 0.08 and (not allowed_sources or source in allowed_sources)
        )[:4]
        if not source_hints:
            source_hints = allowed_sources or _SOURCES
        clauses = [
            part.strip(" ，,。；;、")
            for part in re.split(
                r"(?:[？?。；;]|以及|并且|同时|然后|\band\b)", normalized, flags=re.I
            )
            if len(part.strip()) >= 3
        ]
        entities = tuple(
            dict.fromkeys(
                re.findall(
                    r"(?:[A-Za-z_][A-Za-z0-9_.:/-]{2,}|[\u3400-\u9fff]{2,}(?:模块|服务|模型|算法|接口|页面|会话|实验))",
                    normalized,
                )
            )
        )[:12]
        temporal = None
        temporal_match = re.search(
            r"(?:20\d{2}(?:[-/.年]\d{1,2})?(?:[-/.月]\d{1,2})?|当前|现在|历史|此前|最近)",
            normalized,
        )
        if temporal_match:
            temporal = temporal_match.group(0)
        uncertainty = _normalized_entropy(score for _, score in ranked_intents)
        complexity = _complexity(
            normalized, source_probabilities=(score for _, score in ranked_sources)
        )
        ambiguity: list[str] = []
        if confidence < 0.32 or uncertainty > 0.9:
            ambiguity.append("intent_low_confidence")
        if not entities:
            ambiguity.append("entity_scope_not_explicit")
        required_roles = tuple(REQUIRED_ROLES[intent])
        queries = _mmr_subqueries(
            normalized,
            clauses=clauses,
            entities=entities,
            required_roles=required_roles,
            limit=4 if complexity >= 0.35 else 3,
        )
        return QueryUnderstanding(
            strategy="local_statistical",
            normalized_query=normalized,
            intent=intent,
            intent_confidence=confidence,
            source_hints=source_hints,
            required_roles=required_roles,
            subqueries=queries,
            entities=entities,
            temporal_scope=temporal,
            ambiguity=tuple(ambiguity),
            intent_scores=tuple(ranked_intents),
            source_scores=tuple(ranked_sources),
            uncertainty=uncertainty,
            complexity=complexity,
            fallback_reason=fallback_reason,
        )

    def understand(
        self,
        question: str,
        *,
        explicit_intent: str | None,
        requested_sources: Iterable[str],
        provider: ResolvedLLMProvider | None,
        timeout_seconds: float,
    ) -> QueryUnderstanding:
        local = self.local(
            question,
            explicit_intent=explicit_intent,
            requested_sources=requested_sources,
        )
        if provider is None:
            return local
        try:
            assert_outbound_evidence_safe(question)
            if _ABSOLUTE_PATH.search(question) or _SECRET.search(question):
                raise ValueError("query_contains_sensitive_material")
            return self._llm(
                question,
                local=local,
                explicit_intent=explicit_intent,
                requested_sources=tuple(requested_sources),
                provider=provider,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            return self.local(
                question,
                explicit_intent=explicit_intent,
                requested_sources=requested_sources,
                fallback_reason="structured_model_unavailable_or_invalid",
            )

    def _llm(
        self,
        question: str,
        *,
        local: QueryUnderstanding,
        explicit_intent: str | None,
        requested_sources: tuple[str, ...],
        provider: ResolvedLLMProvider,
        timeout_seconds: float,
    ) -> QueryUnderstanding:
        allowed_sources = (
            tuple(source for source in requested_sources if source in _SOURCES) or _SOURCES
        )
        schema = {
            "intent": list(INTENTS),
            "source_hints": list(allowed_sources),
            "required_roles": "array of short evidence obligations",
            "subqueries": "1-4 standalone retrieval queries",
            "entities": "0-12 repository/domain entities",
            "temporal_scope": "string or null",
            "ambiguity": "array of unresolved ambiguities",
            "confidence": "number 0..1",
        }
        payload = {
            "model": provider.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            **provider_chat_options(provider, purpose="planning"),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a query planner for a versioned evidence RAG system. Return one JSON object only. "
                        "Decompose the question by evidence obligation. Do not answer it. Never broaden project, ACL, "
                        "repository, commit, or time scope. Use only the enumerated intent and sources. "
                        "Each subquery must be independently searchable and must not contain invented identifiers."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "explicit_intent": explicit_intent,
                            "local_prior": local.public(include_queries=False),
                            "output_contract": schema,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        with httpx.Client(timeout=max(0.5, min(timeout_seconds, 20.0))) as client:
            response = client.post(
                provider.base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        document = self._json_object(str(content))
        intent = (
            explicit_intent if explicit_intent in INTENTS else str(document.get("intent") or "")
        )
        if intent not in INTENTS:
            raise ValueError("invalid intent")
        sources = tuple(
            dict.fromkeys(
                source for source in document.get("source_hints", []) if source in allowed_sources
            )
        )[:4]
        subqueries_list: list[str] = []
        for value in document.get("subqueries", []):
            if not isinstance(value, str):
                continue
            query = _normalized(value)
            if not query or not 3 <= len(query) <= 500:
                continue
            if _SECRET.search(query) or _ABSOLUTE_PATH.search(query):
                raise ValueError("unsafe subquery")
            assert_outbound_evidence_safe(query)
            subqueries_list.append(query)
        subqueries = tuple(dict.fromkeys(subqueries_list))[:4]
        if not subqueries:
            raise ValueError("missing subqueries")
        roles = tuple(
            dict.fromkeys(
                role.strip()
                for role in document.get("required_roles", [])
                if isinstance(role, str) and role.strip() in _KNOWN_ROLES
            )
        )[:12]
        entities = tuple(
            dict.fromkeys(
                entity.strip()
                for entity in document.get("entities", [])
                if isinstance(entity, str) and 1 <= len(entity.strip()) <= 160
            )
        )[:12]
        ambiguity = tuple(
            dict.fromkeys(
                item.strip()
                for item in document.get("ambiguity", [])
                if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", item.strip())
            )
        )[:8]
        confidence = float(document.get("confidence", 0.0))
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("invalid confidence")
        temporal = document.get("temporal_scope")
        if temporal is not None and (not isinstance(temporal, str) or len(temporal) > 160):
            raise ValueError("invalid temporal scope")
        local_intents = dict(local.intent_scores)
        blended_intents = {
            label: (1.0 - confidence) * local_intents.get(label, 0.0)
            + (confidence if label == intent else 0.0)
            for label in INTENTS
        }
        intent_denominator = sum(blended_intents.values()) or 1.0
        ranked_intents = tuple(
            sorted(
                ((label, value / intent_denominator) for label, value in blended_intents.items()),
                key=lambda item: (-item[1], item[0]),
            )
        )
        local_sources = dict(local.source_scores)
        blended_sources = {
            source: local_sources.get(source, 0.0) + (0.35 if source in sources else 0.0)
            for source in allowed_sources
        }
        source_denominator = sum(blended_sources.values()) or 1.0
        ranked_sources = tuple(
            sorted(
                ((source, value / source_denominator) for source, value in blended_sources.items()),
                key=lambda item: (-item[1], item[0]),
            )
        )
        calibrated_confidence = dict(ranked_intents).get(intent, confidence)
        return QueryUnderstanding(
            strategy="llm_structured_hybrid",
            normalized_query=_normalized(question),
            intent=intent,
            intent_confidence=calibrated_confidence,
            source_hints=sources or local.source_hints,
            required_roles=roles or tuple(REQUIRED_ROLES[intent]),
            subqueries=subqueries,
            entities=entities,
            temporal_scope=temporal,
            ambiguity=ambiguity,
            intent_scores=ranked_intents,
            source_scores=ranked_sources,
            uncertainty=_normalized_entropy(score for _, score in ranked_intents),
            complexity=local.complexity,
            decomposition_strategy="constrained_llm_evidence_obligations",
            planner_model=provider.model,
        )

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
        document = json.loads(content)
        if not isinstance(document, dict):
            raise ValueError("planner output must be an object")
        return document


def fuse_search_responses(
    responses: list[dict[str, Any]],
    *,
    query_count: int,
) -> dict[str, Any]:
    if not responses:
        raise ValueError("at least one search response is required")
    if len(responses) == 1:
        response = responses[0]
        response.setdefault("trace", {})["query_fusion"] = {
            "version": QUERY_FUSION_VERSION,
            "query_count": query_count,
            "successful_queries": 1,
        }
        return response
    candidates: dict[str, dict[str, Any]] = {}
    rrf: Counter[str] = Counter()
    appearances: Counter[str] = Counter()
    for response in responses:
        for rank, item in enumerate(response.get("results", []), start=1):
            entity_id = str(item.get("entity_id") or "")
            if not entity_id:
                continue
            rrf[entity_id] += 1.0 / (60 + rank)
            appearances[entity_id] += 1
            current = candidates.get(entity_id)
            if current is None or float(item.get("score") or 0.0) > float(
                current.get("score") or 0.0
            ):
                candidates[entity_id] = dict(item)
    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            -rrf[str(item["entity_id"])],
            -appearances[str(item["entity_id"])],
            -float(item.get("score") or 0.0),
            str(item["entity_id"]),
        ),
    )
    for item in ranked:
        entity_id = str(item["entity_id"])
        metadata = dict(item.get("metadata") or {})
        metadata["multi_query_rrf"] = round(rrf[entity_id], 8)
        metadata["query_appearances"] = appearances[entity_id]
        item["metadata"] = metadata
    primary = responses[0]
    evidence_pack = dict(primary.get("evidence_pack") or {})
    for field in ("relations", "related_entities", "conflicts_and_staleness"):
        merged: list[Any] = []
        seen: set[str] = set()
        for response in responses:
            for value in (response.get("evidence_pack") or {}).get(field, []) or []:
                key = json.dumps(value, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    merged.append(value)
        evidence_pack[field] = merged
    citations: dict[str, Any] = {}
    citation_index = 1
    selected_ids = {str(item["entity_id"]) for item in ranked}
    for response in responses:
        for value in ((response.get("evidence_pack") or {}).get("citation_map") or {}).values():
            if str(value.get("entity_id") or "") not in selected_ids:
                continue
            if any(
                existing.get("entity_id") == value.get("entity_id")
                for existing in citations.values()
            ):
                continue
            citations[f"E{citation_index}"] = value
            citation_index += 1
    evidence_pack["citation_map"] = citations
    generations = list(
        dict.fromkeys(
            generation
            for response in responses
            for generation in response.get("index_generations", [])
        )
    )
    trace = dict(primary.get("trace") or {})
    trace["query_fusion"] = {
        "version": QUERY_FUSION_VERSION,
        "query_count": query_count,
        "successful_queries": len(responses),
        "unique_candidates": len(ranked),
    }
    return {
        **primary,
        "total": len(ranked),
        "results": ranked,
        "evidence_pack": evidence_pack,
        "trace": trace,
        "index_generations": generations,
    }
