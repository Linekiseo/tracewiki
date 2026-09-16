"""M6 evidence normalization, adaptive packing, and grounded-answer verification."""

from __future__ import annotations

import re
from collections import Counter
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence_graph_v2 import EvidenceTraversalTraceV2
from .multisource_foundation_v2 import (
    MultiSourceCandidateV2,
    MultiSourceComplexityV2,
    MultiSourcePlanV2,
    RoleFusionResultV2,
    SourceExecutionStatusV2,
    canonical_sha256_v2,
)

EVIDENCE_FACT_VERSION = "multisource-evidence-fact-v2"
EVIDENCE_PACK_VERSION = "multisource-evidence-pack-v2"
GROUNDING_VERIFIER_VERSION = "claim-citation-verifier-v2"
GROUNDED_ANSWER_VERSION = "grounded-answer-contract-v2"
SOURCE_RENDERER_VERSION = "source-specific-evidence-renderer-v2"

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_@.+-]+|[\u3400-\u9fff]+")
_SECRET_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S{8,}))"
)


class EvidenceFactStatusV2(StrEnum):
    VERIFIED = "verified"
    REPORTED = "reported"
    OBSERVED = "observed"
    INFERRED = "inferred"


class AnswerModeV2(StrEnum):
    DETERMINISTIC = "deterministic"
    RETRIEVAL_ONLY = "retrieval_only"
    GROUNDED = "grounded"
    REFUSAL = "refusal"


class AnswerAuthorityV2(StrEnum):
    RETRIEVAL_STAGE = "retrieval_stage"
    FINAL_ANSWER = "final_answer_v2"


class UnanswerableReasonV2(StrEnum):
    EXPLICIT_UNANSWERABLE = "explicit_unanswerable"
    SOURCE_UNAVAILABLE = "source_unavailable"
    UNAUTHORIZED = "unauthorized"
    NOT_INDEXED = "not_indexed"
    TIMEOUT = "timeout"
    MISSING_REQUIRED_ROLE = "missing_required_role"
    CONFLICT = "conflict"
    INCOMPARABLE = "incomparable"
    INSUFFICIENT_METADATA = "insufficient_metadata"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class EvidenceFactV2(_Frozen):
    fact_id: str
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    entity_id: str
    fact_type: str
    entity_type: str
    retrieval_unit_id: str
    status: EvidenceFactStatusV2
    text: str
    locator: str
    stable_version: str
    source_generation: str
    raw_or_derived: Literal["raw_fact", "derived_fact"]
    derivation: str
    review_status: str
    fact_status: str
    matched_roles: tuple[str, ...]
    root_provenance: str
    counter_evidence: bool
    token_estimate: int = Field(ge=1)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EvidenceFactV2:
        if not self.fact_type.startswith(f"{self.source}."):
            raise ValueError("evidence fact envelope does not match source")
        if _SECRET_RE.search(self.text) or _SECRET_RE.search(self.locator):
            raise ValueError("evidence fact contains secret material")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("evidence fact digest mismatch")
        return self


class EvidenceCitationV2(_Frozen):
    citation_id: str
    fact_id: str
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    fact_type: str
    entity_type: str
    locator: str
    stable_version: str
    content_sha256: str


class SourceRenderedEvidenceV2(_Frozen):
    fact_id: str
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    entity_type: str
    heading: str
    body: str
    locator: str
    stable_version: str
    renderer_version: str = SOURCE_RENDERER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> SourceRenderedEvidenceV2:
        if _SECRET_RE.search(self.heading) or _SECRET_RE.search(self.body):
            raise ValueError("rendered evidence contains secret material")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("rendered evidence digest mismatch")
        return self


class EvidencePackDecisionV2(_Frozen):
    status: Literal[
        "supported",
        "partially_supported",
        "contradicted",
        "potentially_stale",
        "insufficient_evidence",
        "source_unavailable",
        "unauthorized",
    ]
    answer_mode: AnswerModeV2
    unanswerable_reason: UnanswerableReasonV2 | None
    missing_roles: tuple[str, ...]
    remediation: tuple[str, ...]
    next_authority: Literal["build_grounded_answer_v2"] = "build_grounded_answer_v2"

    @model_validator(mode="after")
    def _retrieval_authority(self) -> EvidencePackDecisionV2:
        if self.answer_mode is not AnswerModeV2.RETRIEVAL_ONLY:
            raise ValueError("retrieval stage cannot claim final answer authority")
        return self


class EvidencePackV2(_Frozen):
    question_sha256: str
    project_id: str
    plan_sha256: str
    source_status: tuple[tuple[str, str, str | None, str | None], ...]
    verified_facts: tuple[EvidenceFactV2, ...]
    reported_facts: tuple[EvidenceFactV2, ...]
    observed_facts: tuple[EvidenceFactV2, ...]
    inferred_facts: tuple[EvidenceFactV2, ...]
    counter_evidence: tuple[str, ...]
    qualifiers: tuple[str, ...]
    version_differences: tuple[str, ...]
    path_ids: tuple[str, ...]
    satisfied_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    stale_roles: tuple[str, ...]
    unauthorized_roles: tuple[str, ...]
    typed_relation_context: tuple[str, ...]
    rendered_evidence: tuple[SourceRenderedEvidenceV2, ...]
    citations: tuple[EvidenceCitationV2, ...]
    decision: EvidencePackDecisionV2
    included_fact_ids: tuple[str, ...]
    dropped_fact_ids: tuple[str, ...]
    token_budget: int
    used_tokens: int
    pack_version: str = EVIDENCE_PACK_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EvidencePackV2:
        facts = (
            *self.verified_facts,
            *self.reported_facts,
            *self.observed_facts,
            *self.inferred_facts,
        )
        if self.included_fact_ids != tuple(item.fact_id for item in facts):
            raise ValueError("evidence pack fact membership mismatch")
        if self.used_tokens != sum(item.token_estimate for item in facts):
            raise ValueError("evidence pack token accounting mismatch")
        if self.used_tokens > self.token_budget:
            raise ValueError("evidence pack exceeds budget")
        if {item.fact_id for item in self.citations} != set(self.included_fact_ids):
            raise ValueError("evidence pack citations are incomplete")
        if len({item.citation_id for item in self.citations}) != len(self.citations):
            raise ValueError("evidence pack citation IDs must be unique")
        by_fact = {item.fact_id: item for item in facts}
        for citation in self.citations:
            fact = by_fact[citation.fact_id]
            if (
                citation.source != fact.source
                or citation.fact_type != fact.fact_type
                or citation.entity_type != fact.entity_type
                or citation.locator != fact.locator
                or citation.stable_version != fact.stable_version
                or citation.content_sha256 != fact.content_sha256
            ):
                raise ValueError("citation does not exactly bind its typed evidence fact")
        if tuple(item.fact_id for item in self.rendered_evidence) != self.included_fact_ids:
            raise ValueError("source-specific rendered evidence membership mismatch")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("evidence pack digest mismatch")
        return self


class AnswerClaimV2(_Frozen):
    claim_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=4_000)
    citation_ids: tuple[str, ...] = Field(min_length=1)
    required_fact_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _canonical(self) -> AnswerClaimV2:
        if self.citation_ids != tuple(dict.fromkeys(self.citation_ids)):
            raise ValueError("claim citation IDs must be ordered and unique")
        if self.required_fact_ids != tuple(dict.fromkeys(self.required_fact_ids)):
            raise ValueError("claim required fact IDs must be ordered and unique")
        if _CONTROL_RE.search(self.text) or _SECRET_RE.search(self.text):
            raise ValueError("claim text is unsafe")
        return self


class ClaimCitationVerificationV2(_Frozen):
    claim_id: str
    supported: bool
    cited_fact_ids: tuple[str, ...]
    invalid_citation_ids: tuple[str, ...]
    missing_fact_ids: tuple[str, ...]
    unsupported_terms: tuple[str, ...]
    reason: str | None


class GroundedAnswerV2(_Frozen):
    authority: AnswerAuthorityV2
    mode: AnswerModeV2
    rendered: str
    claims: tuple[AnswerClaimV2, ...]
    verifications: tuple[ClaimCitationVerificationV2, ...]
    unsupported_claim_count: int
    citation_precision_numerator: int
    citation_precision_denominator: int
    citation_completeness_numerator: int
    citation_completeness_denominator: int
    refusal_reason: UnanswerableReasonV2 | None
    verifier_version: str = GROUNDING_VERIFIER_VERSION
    answer_version: str = GROUNDED_ANSWER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> GroundedAnswerV2:
        if self.unsupported_claim_count != sum(not item.supported for item in self.verifications):
            raise ValueError("unsupported claim count mismatch")
        if self.mode is AnswerModeV2.REFUSAL and self.refusal_reason is None:
            raise ValueError("refusal requires a reason")
        if self.mode is AnswerModeV2.RETRIEVAL_ONLY and (
            self.rendered or self.refusal_reason is not None
        ):
            raise ValueError("retrieval-only handoff cannot render a final answer or refusal")
        if self.authority is AnswerAuthorityV2.RETRIEVAL_STAGE and (
            self.mode is not AnswerModeV2.RETRIEVAL_ONLY or self.claims or self.verifications
        ):
            raise ValueError("retrieval stage cannot own answer claims")
        if (
            self.authority is AnswerAuthorityV2.FINAL_ANSWER
            and self.mode is AnswerModeV2.GROUNDED
            and not self.rendered
        ):
            raise ValueError("grounded final answer must render supported claims")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("grounded answer digest mismatch")
        return self


def normalize_evidence_fact_v2(candidate: MultiSourceCandidateV2) -> EvidenceFactV2:
    status = (
        EvidenceFactStatusV2.VERIFIED
        if candidate.fact_status in {"verified", "accepted"}
        else EvidenceFactStatusV2.OBSERVED
        if candidate.fact_status in {"observed", "completed", "failed"}
        else EvidenceFactStatusV2.REPORTED
        if candidate.raw_or_derived == "raw_fact"
        else EvidenceFactStatusV2.INFERRED
    )
    text = _CONTROL_RE.sub(" ", f"{candidate.title}. {candidate.snippet}").strip()
    if len(text) > 2_000:
        text = text[:1_999].rstrip() + "…"
    payload = {
        "fact_id": "evidencefact-" + candidate.candidate_id,
        "source": candidate.retrieval_domain,
        "entity_id": candidate.entity_id,
        "fact_type": candidate.fact_type,
        "entity_type": candidate.entity_type,
        "retrieval_unit_id": candidate.retrieval_unit_id,
        "status": status,
        "text": text,
        "locator": candidate.locator,
        "stable_version": candidate.stable_version,
        "source_generation": candidate.source_generation,
        "raw_or_derived": candidate.raw_or_derived,
        "derivation": candidate.derivation,
        "review_status": candidate.review_status,
        "fact_status": candidate.fact_status,
        "matched_roles": candidate.matched_roles,
        "root_provenance": candidate.root_provenance,
        "counter_evidence": candidate.counter_evidence,
        "token_estimate": candidate.token_estimate,
    }
    normalized = EvidenceFactV2.model_construct(content_sha256="pending", **payload).model_dump(
        mode="json", exclude={"content_sha256"}
    )
    return EvidenceFactV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def render_source_evidence_v2(fact: EvidenceFactV2) -> SourceRenderedEvidenceV2:
    """Render a typed fact without turning retrieval output into an answer."""

    heading, body = {
        "code": (
            f"Code · {fact.entity_type}",
            f"{fact.text}\nLocation: {fact.locator}\nVersion: {fact.stable_version}",
        ),
        "codex": (
            f"Codex timeline · {fact.entity_type}",
            f"{fact.text}\nEvent locator: {fact.locator}",
        ),
        "experiment": (
            f"Experiment · {fact.entity_type}",
            f"{fact.text}\nRun/version: {fact.stable_version}\nLocator: {fact.locator}",
        ),
        "notebook": (
            f"Notebook flow · {fact.entity_type}",
            f"{fact.text}\nCell/output locator: {fact.locator}",
        ),
        "document": (
            f"Document · {fact.entity_type}",
            f"{fact.text}\nSource span: {fact.locator}",
        ),
        "workspace": (
            (
                "Workspace derived"
                if fact.raw_or_derived == "derived_fact"
                or fact.entity_type in {"intelligence_run", "IntelligenceRun"}
                else "Workspace authoritative"
            )
            + f" · {fact.entity_type}",
            f"{fact.text}\nState locator: {fact.locator}\nVersion: {fact.stable_version}",
        ),
    }[fact.source]
    payload = {
        "fact_id": fact.fact_id,
        "source": fact.source,
        "entity_type": fact.entity_type,
        "heading": heading,
        "body": body,
        "locator": fact.locator,
        "stable_version": fact.stable_version,
        "renderer_version": SOURCE_RENDERER_VERSION,
    }
    normalized = SourceRenderedEvidenceV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return SourceRenderedEvidenceV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def build_evidence_pack_v2(
    plan: MultiSourcePlanV2,
    fusion: RoleFusionResultV2,
    traversal: EvidenceTraversalTraceV2,
) -> EvidencePackV2:
    """Separate retrieval selection from a bounded comprehension evidence pack."""

    min_units, max_units = {
        MultiSourceComplexityV2.SIMPLE: (2, 5),
        MultiSourceComplexityV2.SINGLE_SOURCE: (5, 10),
        MultiSourceComplexityV2.MULTI_SOURCE: (8, 15),
        MultiSourceComplexityV2.MULTI_HOP: (12, 20),
    }[plan.complexity]
    candidates = list(fusion.selected)
    path_candidate_ids = {
        identity for path in traversal.accepted_paths for identity in path.candidate_ids
    }
    candidates.sort(
        key=lambda item: (
            not item.counter_evidence,
            item.candidate_id not in path_candidate_ids,
            -len(set(item.matched_roles) & set(plan.required_roles)),
            -item.calibrated_relevance,
            item.candidate_id,
        )
    )
    selected: list[MultiSourceCandidateV2] = []
    dropped: list[str] = []
    roots: set[str] = set()
    used = 0
    for candidate in candidates:
        if len(selected) >= max_units:
            dropped.append("evidencefact-" + candidate.candidate_id)
            continue
        if candidate.root_provenance in roots and not candidate.counter_evidence:
            dropped.append("evidencefact-" + candidate.candidate_id)
            continue
        if used + candidate.token_estimate > plan.context_budget_tokens:
            dropped.append("evidencefact-" + candidate.candidate_id)
            continue
        selected.append(candidate)
        roots.add(candidate.root_provenance)
        used += candidate.token_estimate
    facts = tuple(normalize_evidence_fact_v2(item) for item in selected)
    facts_by_status = {
        status: tuple(item for item in facts if item.status is status)
        for status in EvidenceFactStatusV2
    }
    canonical_facts = (
        *facts_by_status[EvidenceFactStatusV2.VERIFIED],
        *facts_by_status[EvidenceFactStatusV2.REPORTED],
        *facts_by_status[EvidenceFactStatusV2.OBSERVED],
        *facts_by_status[EvidenceFactStatusV2.INFERRED],
    )
    citations = tuple(
        EvidenceCitationV2(
            citation_id=f"E{index}",
            fact_id=fact.fact_id,
            source=fact.source,
            fact_type=fact.fact_type,
            entity_type=fact.entity_type,
            locator=fact.locator,
            stable_version=fact.stable_version,
            content_sha256=fact.content_sha256,
        )
        for index, fact in enumerate(canonical_facts, start=1)
    )
    source_status = tuple(
        (
            item.source,
            item.status.value,
            item.index_generation,
            item.watermark,
        )
        for item in fusion.source_status
    )
    unavailable = [
        item
        for item in fusion.source_status
        if item.status
        in {
            SourceExecutionStatusV2.TIMEOUT,
            SourceExecutionStatusV2.UNAVAILABLE,
            SourceExecutionStatusV2.NOT_INDEXED,
        }
    ]
    unauthorized = [
        item for item in fusion.source_status if item.status is SourceExecutionStatusV2.UNAUTHORIZED
    ]
    missing = tuple(dict.fromkeys((*fusion.missing_roles, *traversal.missing_roles)))
    counter_ids = tuple(item.fact_id for item in facts if item.counter_evidence)
    version_differences = tuple(
        item.candidate_id
        for item in fusion.selected
        if item.version_alignment
        not in {"exact", "compatible", "historical_target", "not_applicable"}
    )
    if unauthorized:
        decision_status = "unauthorized"
        reason = UnanswerableReasonV2.UNAUTHORIZED
    elif unavailable and missing:
        decision_status = "source_unavailable"
        source_reason = unavailable[0].status
        reason = (
            UnanswerableReasonV2.TIMEOUT
            if source_reason is SourceExecutionStatusV2.TIMEOUT
            else UnanswerableReasonV2.NOT_INDEXED
            if source_reason is SourceExecutionStatusV2.NOT_INDEXED
            else UnanswerableReasonV2.SOURCE_UNAVAILABLE
        )
    elif missing:
        decision_status = "insufficient_evidence"
        reason = UnanswerableReasonV2.MISSING_REQUIRED_ROLE
    elif counter_ids:
        decision_status = "contradicted"
        reason = None
    elif version_differences:
        decision_status = "potentially_stale"
        reason = None
    else:
        decision_status = "supported"
        reason = None
    if len(facts) < min_units and not missing and decision_status == "supported":
        decision_status = "partially_supported"
    remediation = tuple(
        sorted(
            {
                *(f"retrieve_role:{role}" for role in missing),
                *(f"restore_source:{item.source}" for item in unavailable),
                *(f"request_access:{item.source}" for item in unauthorized),
            }
        )
    )
    decision = EvidencePackDecisionV2(
        status=decision_status,  # type: ignore[arg-type]
        answer_mode=AnswerModeV2.RETRIEVAL_ONLY,
        unanswerable_reason=reason,
        missing_roles=missing,
        remediation=remediation,
    )
    payload = {
        "question_sha256": plan.question_sha256,
        "project_id": plan.scope.project_id,
        "plan_sha256": plan.content_sha256,
        "source_status": source_status,
        "verified_facts": facts_by_status[EvidenceFactStatusV2.VERIFIED],
        "reported_facts": facts_by_status[EvidenceFactStatusV2.REPORTED],
        "observed_facts": facts_by_status[EvidenceFactStatusV2.OBSERVED],
        "inferred_facts": facts_by_status[EvidenceFactStatusV2.INFERRED],
        "counter_evidence": counter_ids,
        "qualifiers": (),
        "version_differences": version_differences,
        "path_ids": tuple(item.path_id for item in traversal.accepted_paths),
        "satisfied_roles": fusion.satisfied_roles,
        "missing_roles": missing,
        "stale_roles": (),
        "unauthorized_roles": tuple(plan.required_roles) if unauthorized else (),
        "typed_relation_context": tuple(
            dict.fromkeys(
                relation for path in traversal.accepted_paths for relation in path.typed_relations
            )
        ),
        "rendered_evidence": tuple(render_source_evidence_v2(item) for item in canonical_facts),
        "citations": citations,
        "decision": decision,
        "included_fact_ids": tuple(item.fact_id for item in canonical_facts),
        "dropped_fact_ids": tuple(dropped),
        "token_budget": plan.context_budget_tokens,
        "used_tokens": used,
        "pack_version": EVIDENCE_PACK_VERSION,
    }
    normalized = EvidencePackV2.model_construct(content_sha256="pending", **payload).model_dump(
        mode="json", exclude={"content_sha256"}
    )
    return EvidencePackV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def verify_claim_citations_v2(
    pack: EvidencePackV2,
    claims: tuple[AnswerClaimV2, ...],
) -> tuple[ClaimCitationVerificationV2, ...]:
    if len({item.claim_id for item in claims}) != len(claims):
        raise ValueError("answer claim IDs must be unique")
    citations = {item.citation_id: item for item in pack.citations}
    facts = {
        item.fact_id: item
        for item in (
            *pack.verified_facts,
            *pack.reported_facts,
            *pack.observed_facts,
            *pack.inferred_facts,
        )
    }
    results: list[ClaimCitationVerificationV2] = []
    for claim in claims:
        invalid_citations = tuple(
            citation for citation in claim.citation_ids if citation not in citations
        )
        cited_ids = tuple(
            citations[citation].fact_id for citation in claim.citation_ids if citation in citations
        )
        missing = tuple(
            identity for identity in claim.required_fact_ids if identity not in cited_ids
        )
        claim_tokens = {
            token.casefold() for token in _TOKEN_RE.findall(claim.text) if len(token) >= 3
        }
        evidence_tokens = {
            token.casefold()
            for identity in cited_ids
            for token in _TOKEN_RE.findall(facts[identity].text)
            if len(token) >= 3
        }
        unsupported_terms = tuple(sorted(claim_tokens - evidence_tokens))
        supported = (
            bool(cited_ids)
            and not invalid_citations
            and not missing
            and not unsupported_terms
            and all(
                facts[identity].status
                in {EvidenceFactStatusV2.VERIFIED, EvidenceFactStatusV2.OBSERVED}
                and (
                    facts[identity].raw_or_derived == "raw_fact"
                    or facts[identity].review_status.casefold()
                    in {"accepted", "confirmed", "reviewed"}
                )
                for identity in cited_ids
            )
        )
        reason = (
            None
            if supported
            else "citation_unknown"
            if invalid_citations
            else "citation_missing"
            if not cited_ids
            else "required_fact_missing"
            if missing
            else "claim_terms_not_entailed"
            if unsupported_terms
            else "fact_not_claim_authority"
        )
        results.append(
            ClaimCitationVerificationV2(
                claim_id=claim.claim_id,
                supported=supported,
                cited_fact_ids=cited_ids,
                invalid_citation_ids=invalid_citations,
                missing_fact_ids=missing,
                unsupported_terms=unsupported_terms,
                reason=reason,
            )
        )
    return tuple(results)


def build_grounded_answer_v2(
    pack: EvidencePackV2,
    claims: tuple[AnswerClaimV2, ...],
) -> GroundedAnswerV2:
    # An unanswerable evidence pack is already a final, fail-closed retrieval
    # decision.  It must remain a refusal even when generation was intentionally
    # skipped and therefore supplied no claims.  Empty claims are a retrieval
    # hand-off only for an otherwise answerable pack.
    if pack.decision.unanswerable_reason is not None:
        authority = AnswerAuthorityV2.FINAL_ANSWER
        mode = AnswerModeV2.REFUSAL
        claims = ()
        verifications = ()
        rendered = "Insufficient evidence. " + "; ".join(
            pack.decision.remediation or ("No safe remediation available.",)
        )
        refusal_reason = pack.decision.unanswerable_reason
    elif not claims:
        authority = AnswerAuthorityV2.RETRIEVAL_STAGE
        mode = AnswerModeV2.RETRIEVAL_ONLY
        verifications = ()
        rendered = ""
        refusal_reason = None
    else:
        authority = AnswerAuthorityV2.FINAL_ANSWER
        mode = AnswerModeV2.GROUNDED
        verifications = verify_claim_citations_v2(pack, claims)
        rendered = "\n".join(
            f"{claim.text} {' '.join(f'[{citation}]' for citation in claim.citation_ids)}"
            for claim in claims
            if next(item.supported for item in verifications if item.claim_id == claim.claim_id)
        )
        if not rendered:
            mode = AnswerModeV2.RETRIEVAL_ONLY
            refusal_reason = None
        else:
            refusal_reason = None
    cited = sum(len(item.cited_fact_ids) for item in verifications)
    precise = sum(len(item.cited_fact_ids) if item.supported else 0 for item in verifications)
    metric_claims = claims if verifications else ()
    required = sum(len(item.required_fact_ids) for item in metric_claims)
    complete = sum(
        len(item.required_fact_ids) - len(verification.missing_fact_ids)
        for item, verification in zip(metric_claims, verifications, strict=True)
    )
    payload = {
        "authority": authority,
        "mode": mode,
        "rendered": rendered,
        "claims": claims,
        "verifications": verifications,
        "unsupported_claim_count": sum(not item.supported for item in verifications),
        "citation_precision_numerator": precise,
        "citation_precision_denominator": cited,
        "citation_completeness_numerator": complete,
        "citation_completeness_denominator": required,
        "refusal_reason": refusal_reason,
        "verifier_version": GROUNDING_VERIFIER_VERSION,
        "answer_version": GROUNDED_ANSWER_VERSION,
    }
    normalized = GroundedAnswerV2.model_construct(content_sha256="pending", **payload).model_dump(
        mode="json", exclude={"content_sha256"}
    )
    return GroundedAnswerV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def source_status_summary_v2(pack: EvidencePackV2) -> dict[str, int]:
    return dict(Counter(status for _, status, _, _ in pack.source_status))


__all__ = [
    "EVIDENCE_FACT_VERSION",
    "EVIDENCE_PACK_VERSION",
    "GROUNDED_ANSWER_VERSION",
    "GROUNDING_VERIFIER_VERSION",
    "SOURCE_RENDERER_VERSION",
    "AnswerClaimV2",
    "AnswerAuthorityV2",
    "AnswerModeV2",
    "ClaimCitationVerificationV2",
    "EvidenceCitationV2",
    "EvidenceFactStatusV2",
    "EvidenceFactV2",
    "EvidencePackDecisionV2",
    "EvidencePackV2",
    "GroundedAnswerV2",
    "SourceRenderedEvidenceV2",
    "UnanswerableReasonV2",
    "build_evidence_pack_v2",
    "build_grounded_answer_v2",
    "normalize_evidence_fact_v2",
    "render_source_evidence_v2",
    "source_status_summary_v2",
    "verify_claim_citations_v2",
]
