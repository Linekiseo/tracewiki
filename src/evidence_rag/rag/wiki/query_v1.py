"""Explicit opt-in intelligent query facade over the Agent-Native Wiki."""

from __future__ import annotations

import json
import time
from typing import Final
from uuid import uuid4

import httpx

from ...config import Settings
from ...models import QueryRequest
from ...query.intelligence import QueryUnderstandingEngine
from ...query.provider import ResolvedLLMProvider, RuntimeLLMProviderRegistry
from ..answer_v2 import (
    AnswerClaimV2,
    AnswerModeV2,
    EvidencePackV2,
    GroundedAnswerV2,
    build_grounded_answer_v2,
)
from .contracts_v1 import WikiSourceDomainV1, canonical_sha256_v1
from .runtime_v1 import WikiRuntimeV1
from .search_v1 import WikiQueryClassV1, classify_wiki_query_v1

WIKI_QUERY_ENGINE_HEADER: Final = "X-RAG-Wiki-Engine"
WIKI_QUERY_ENGINE: Final = "wiki_v1"
WIKI_QUERY_VERSION: Final = "agent-native-wiki-intelligent-query-v1"


class WikiQueryError(ValueError):
    """Raised for invalid engine selection or generated answer authority."""


def validate_wiki_query_engine_override(value: str | None) -> str | None:
    if value is None:
        return None
    if value != WIKI_QUERY_ENGINE:
        raise WikiQueryError(f"{WIKI_QUERY_ENGINE_HEADER} must be exactly '{WIKI_QUERY_ENGINE}'")
    return value


_INTENT_CLASS = {
    "current_implementation": WikiQueryClassV1.LOCAL_DETAIL,
    "historical_implementation": WikiQueryClassV1.TEMPORAL,
    "change_trace": WikiQueryClassV1.TEMPORAL,
    "rationale": WikiQueryClassV1.MULTI_HOP,
    "experiment_validation": WikiQueryClassV1.COMPARISON,
    "claim_verification": WikiQueryClassV1.MULTI_HOP,
    "reproduction": WikiQueryClassV1.MULTI_HOP,
    "staleness_check": WikiQueryClassV1.TEMPORAL,
    "global_synthesis": WikiQueryClassV1.GLOBAL,
}


class WikiIntelligentQueryV1:
    """Navigate first, then optionally generate claims that pass exact citation checks."""

    def __init__(
        self,
        *,
        settings: Settings,
        wiki: WikiRuntimeV1,
        providers: RuntimeLLMProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.wiki = wiki
        self.providers = providers
        self.understanding = QueryUnderstandingEngine()
        self.authority_sha256 = canonical_sha256_v1(
            {
                "generator_model": settings.llm_model,
                "generator_provider_sha256": (
                    canonical_sha256_v1(settings.llm_base_url)
                    if settings.llm_base_url is not None
                    else None
                ),
                "query_version": WIKI_QUERY_VERSION,
                "wiki_runtime": wiki.authority_sha256,
            }
        )

    def answer(
        self,
        request: QueryRequest,
        *,
        requester_acl_refs: tuple[str, ...],
    ) -> dict:
        started = time.perf_counter()
        project_id = request.scope.project_id or "project-rag"
        provider = self._provider(project_id)
        understanding = self.understanding.understand(
            request.question,
            explicit_intent=request.intent,
            requested_sources=request.include,
            # Evidence-only mode must remain entirely local and must never construct
            # an external model client.  It still receives statistical intent/source
            # probabilities and an auditable retrieval plan.
            provider=provider if request.mode == "answer" else None,
            timeout_seconds=min(8.0, (request.deadline_ms or 40_000) / 1000 * 0.2),
        )
        deterministic_class = classify_wiki_query_v1(request.question)
        query_class = (
            _INTENT_CLASS.get(understanding.intent, deterministic_class)
            if understanding.strategy == "llm_structured_hybrid"
            or understanding.intent_confidence >= 0.34
            else deterministic_class
        )
        required_sources = tuple(
            sorted(
                {WikiSourceDomainV1(item) for item in request.include},
                key=lambda item: item.value,
            )
        )
        navigation_request = self.wiki.build_navigation_request(
            request_id="wiki-query-" + uuid4().hex,
            project_id=project_id,
            requester_acl_refs=requester_acl_refs,
            query=request.question,
            query_class=query_class,
            required_roles=(),
            required_sources=required_sources,
            as_of=request.as_of,
            max_searches=min(16, max(4, request.max_hops * 3 + 2)),
            max_page_reads=min(128, max(24, request.max_evidence * 4)),
            max_link_hops=min(6, request.max_hops),
            top_k_per_search=min(25, max(8, request.max_evidence)),
            token_budget=request.max_context_tokens or 8_000,
            require_raw_evidence=True,
        )
        navigation = self.wiki.navigate(navigation_request)
        pack = navigation.evidence_pack
        grounded, generation_state = self._ground(request, pack, provider=provider)
        citations = tuple(
            dict.fromkeys(citation for claim in grounded.claims for citation in claim.citation_ids)
        )
        if grounded.mode is AnswerModeV2.RETRIEVAL_ONLY and not citations:
            citations = tuple(item.citation_id for item in pack.citations)
        remediation = list(pack.decision.remediation)
        answer_text = grounded.rendered
        if not answer_text and pack.decision.unanswerable_reason is not None:
            answer_text = "证据不足，无法形成受支持结论。" + (
                " 建议：" + "；".join(remediation) if remediation else ""
            )
        response = {
            "query_id": "wiki-query://" + navigation.request_sha256[7:31],
            "engine": WIKI_QUERY_ENGINE,
            "query_contract_version": WIKI_QUERY_VERSION,
            "query_understanding": understanding.public(include_queries=True),
            "resolved_scope": {
                "project_id": project_id,
                "generation_id": navigation.generation_id,
                "as_of": request.as_of,
                "source_types": [item.value for item in required_sources],
            },
            "answer": {
                "status": pack.decision.status,
                "decision_status": pack.decision.status,
                "answer_mode": grounded.mode.value,
                "text": answer_text,
                "citations": list(citations),
                "refusal": grounded.mode is AnswerModeV2.REFUSAL,
                "refusal_reason": (
                    grounded.refusal_reason.value if grounded.refusal_reason else None
                ),
                "fallback_reason": generation_state.get("fallback_reason"),
                "remediation": remediation,
            },
            "evidence_pack": pack.model_dump(mode="json"),
            "navigation": navigation.model_dump(mode="json"),
            "grounded_answer": grounded.model_dump(mode="json"),
            "trace": {
                "engine": WIKI_QUERY_ENGINE,
                "query_sha256": pack.question_sha256,
                "generation_id": navigation.generation_id,
                "navigation_sha256": navigation.content_sha256,
                "generator": generation_state,
                "query_understanding": understanding.public(include_queries=False),
                "quality_state": "QUALITY_HOLD",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "authority_sha256": self.authority_sha256,
            },
        }
        return response

    def _ground(
        self,
        request: QueryRequest,
        pack: EvidencePackV2,
        *,
        provider: ResolvedLLMProvider | None,
    ) -> tuple[GroundedAnswerV2, dict]:
        if request.mode == "evidence":
            return build_grounded_answer_v2(pack, ()), {
                "attempted": False,
                "availability": "NOT_REQUESTED",
                "fallback_reason": None,
            }
        if provider is None:
            return build_grounded_answer_v2(pack, ()), {
                "attempted": False,
                "availability": "UNAVAILABLE",
                "fallback_reason": "generator_not_configured",
            }
        if pack.decision.unanswerable_reason is not None:
            return build_grounded_answer_v2(pack, ()), {
                "attempted": False,
                "availability": "AVAILABLE",
                "fallback_reason": "evidence_pack_unanswerable",
            }
        try:
            claims = self._generate_claims(request, pack, provider=provider)
            grounded = build_grounded_answer_v2(pack, claims)
        except Exception as error:
            return build_grounded_answer_v2(pack, ()), {
                "attempted": True,
                "availability": "UNAVAILABLE",
                "fallback_reason": "generator_or_contract_failure",
                "failure_code": type(error).__name__,
            }
        if grounded.unsupported_claim_count or grounded.mode is not AnswerModeV2.GROUNDED:
            return build_grounded_answer_v2(pack, ()), {
                "attempted": True,
                "availability": "AVAILABLE",
                "fallback_reason": "claim_citation_verification_failed",
            }
        return grounded, {
            "attempted": True,
            "availability": "AVAILABLE",
            "fallback_reason": None,
            "answer_sha256": grounded.content_sha256,
        }

    def _generate_claims(
        self,
        request: QueryRequest,
        pack: EvidencePackV2,
        *,
        provider: ResolvedLLMProvider,
    ) -> tuple[AnswerClaimV2, ...]:
        evidence = [item.model_dump(mode="json") for item in pack.rendered_evidence]
        citations = [item.model_dump(mode="json") for item in pack.citations]
        payload = {
            "model": provider.model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        'Return strict JSON {"claims":[...]}. Each claim needs claim_id, '
                        "text, citation_ids, and required_fact_ids. Use only supplied evidence; "
                        "one independently verifiable factual claim per item; never invent IDs, "
                        "paths, versions, metrics, relations, or conclusions."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": request.question,
                            "decision": pack.decision.model_dump(mode="json"),
                            "evidence": evidence,
                            "citations": citations,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
        }
        with httpx.Client(timeout=60.0, follow_redirects=False) as client:
            response = client.post(
                provider.base_url + "/chat/completions",
                headers={
                    "Authorization": f"Bearer {provider.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            envelope = response.json()
        raw = envelope["choices"][0]["message"]["content"]
        document = json.loads(raw)
        rows = document.get("claims") if isinstance(document, dict) else None
        if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
            raise WikiQueryError("generated Wiki claims are missing or exceed bounds")
        claims = tuple(AnswerClaimV2.model_validate(item) for item in rows)
        if len({item.claim_id for item in claims}) != len(claims):
            raise WikiQueryError("generated Wiki claim IDs are duplicated")
        return claims

    def _provider(self, project_id: str) -> ResolvedLLMProvider | None:
        if self.providers is not None:
            return self.providers.resolve(project_id)
        if self.settings.llm_base_url and self.settings.llm_api_key and self.settings.llm_model:
            return ResolvedLLMProvider(
                provider="openai_compatible",
                base_url=self.settings.llm_base_url.rstrip("/"),
                model=self.settings.llm_model,
                api_key=self.settings.llm_api_key,
                source="environment",
            )
        return None
