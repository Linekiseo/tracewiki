"""Pure claim review state machine and version staleness propagation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    DocumentClaimStatus,
    DocumentEdge,
    DocumentEdgeType,
    DocumentEntity,
    DocumentEntityType,
    DocumentFactAuthority,
    DocumentPublication,
    canonical_sha256,
)

DOCUMENT_CLAIM_POLICY_VERSION = "document-claim-policy-v2"


class DocumentClaimReviewDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class DocumentEvidenceRelationship(StrEnum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    QUALIFIES = "qualifies"


class DocumentClaimReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    candidate_id: str
    decision: DocumentClaimReviewDecision
    reviewer_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
    review_nonce: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")


class DocumentClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    claim_id: str
    evidence_entity_id: str
    relationship: DocumentEvidenceRelationship
    identity_match: bool
    numeric_match: bool | None = None
    design_comparable: bool | None = None
    current: bool | None = None
    reviewed: bool


def _identifier(prefix: str, value: object) -> str:
    return f"{prefix}-{canonical_sha256(value).removeprefix('sha256:')[:32]}"


def review_claims(
    publication: DocumentPublication,
    reviews: tuple[DocumentClaimReview, ...],
) -> DocumentPublication:
    """Accept/reject candidates without allowing parser candidates to self-promote."""

    candidates = {
        item.entity_id: item
        for item in publication.entities
        if item.entity_type == DocumentEntityType.CLAIM_CANDIDATE
    }
    if len({item.candidate_id for item in reviews}) != len(reviews):
        raise ValueError("claim review membership must be unique")
    entities = list(publication.entities)
    edges = list(publication.edges)
    for review in reviews:
        candidate = candidates.get(review.candidate_id)
        if candidate is None:
            raise ValueError("claim review references unknown candidate")
        status = (
            DocumentClaimStatus.ACCEPTED
            if review.decision == DocumentClaimReviewDecision.ACCEPT
            else DocumentClaimStatus.REJECTED
        )
        claim_id = _identifier(
            "doc-claim",
            {
                "stable_id": candidate.stable_id,
                "version_id": candidate.version_id,
                "reviewer": review.reviewer_id,
                "nonce": review.review_nonce,
                "decision": review.decision,
                "policy": DOCUMENT_CLAIM_POLICY_VERSION,
            },
        )
        claim = DocumentEntity(
            entity_id=claim_id,
            stable_id=_identifier(
                "docstable-claim",
                {"family": candidate.family_id, "candidate": candidate.stable_id},
            ),
            entity_type=DocumentEntityType.CLAIM,
            family_id=candidate.family_id,
            version_id=candidate.version_id,
            scope=candidate.scope,
            parent_id=candidate.entity_id,
            ordinal=candidate.ordinal,
            page_number=candidate.page_number,
            source_text=candidate.source_text,
            derived_text="",
            content_sha256=canonical_sha256(
                {
                    "candidate": candidate.content_sha256,
                    "decision": review.decision,
                    "reviewer": review.reviewer_id,
                    "nonce": review.review_nonce,
                    "policy": DOCUMENT_CLAIM_POLICY_VERSION,
                }
            ),
            locator=f"{candidate.locator}/review/{claim_id}",
            authority=DocumentFactAuthority.REVIEWED_DERIVED,
            status=status,
            label=candidate.label,
            metadata={
                "candidate_id": candidate.entity_id,
                "reviewer_id": review.reviewer_id,
                "review_nonce": review.review_nonce,
                "policy_version": DOCUMENT_CLAIM_POLICY_VERSION,
            },
        )
        entities.append(claim)
        edge_type = (
            DocumentEdgeType.SUPPORTS
            if status == DocumentClaimStatus.ACCEPTED
            else DocumentEdgeType.REFUTES
        )
        edge_id = _identifier(
            "docedge",
            {
                "version": candidate.version_id,
                "source": candidate.entity_id,
                "target": claim.entity_id,
                "type": edge_type,
            },
        )
        edges.append(
            DocumentEdge(
                edge_id=edge_id,
                version_id=candidate.version_id,
                scope=candidate.scope,
                source_id=candidate.entity_id,
                target_id=claim.entity_id,
                edge_type=edge_type,
                authority=DocumentFactAuthority.REVIEWED_DERIVED,
                confidence=1.0,
                review_status="confirmed",
                locator=f"{claim.locator}/edge/{edge_id}",
            )
        )
    return _replace_publication(publication, tuple(entities), tuple(edges))


def validate_claim(
    claim: DocumentEntity,
    evidence: tuple[DocumentClaimEvidence, ...],
) -> tuple[DocumentClaimStatus, dict[str, Any]]:
    """Apply L0-L4 evidence policy without majority vote or model-authored state."""

    if claim.entity_type != DocumentEntityType.CLAIM:
        raise ValueError("claim validation requires a reviewed claim")
    if any(item.claim_id != claim.entity_id for item in evidence):
        raise ValueError("claim evidence references a different claim")
    reviewed = tuple(item for item in evidence if item.reviewed and item.identity_match)
    supports = tuple(
        item for item in reviewed if item.relationship == DocumentEvidenceRelationship.SUPPORTS
    )
    refutes = tuple(
        item for item in reviewed if item.relationship == DocumentEvidenceRelationship.REFUTES
    )
    qualifies = tuple(
        item for item in reviewed if item.relationship == DocumentEvidenceRelationship.QUALIFIES
    )
    checks = {
        "L0_source": claim.status == DocumentClaimStatus.ACCEPTED,
        "L1_identity": bool(reviewed),
        "L2_numeric": bool(supports) and all(item.numeric_match is not False for item in supports),
        "L3_design": bool(supports)
        and all(item.design_comparable is not False for item in supports),
        "L4_current": bool(supports) and all(item.current is not False for item in supports),
        "support_count": len(supports),
        "refute_count": len(refutes),
        "qualify_count": len(qualifies),
        "policy_version": DOCUMENT_CLAIM_POLICY_VERSION,
    }
    if not reviewed:
        return DocumentClaimStatus.INSUFFICIENT_EVIDENCE, checks
    if refutes and supports:
        return DocumentClaimStatus.CONTRADICTED, checks
    if refutes:
        return DocumentClaimStatus.CONTRADICTED, checks
    if supports and checks["L2_numeric"] and checks["L3_design"] and checks["L4_current"]:
        return (
            DocumentClaimStatus.PARTIALLY_SUPPORTED if qualifies else DocumentClaimStatus.VERIFIED
        ), checks
    return DocumentClaimStatus.PARTIALLY_SUPPORTED, checks


def _replace_publication(
    publication: DocumentPublication,
    entities: tuple[DocumentEntity, ...],
    edges: tuple[DocumentEdge, ...],
) -> DocumentPublication:
    version = publication.version.model_copy(
        update={"entity_ids": tuple(item.entity_id for item in entities)}
    )
    units = list(publication.retrieval_units)
    existing = {item.entity_id for item in units}
    for item in entities:
        if item.entity_id not in existing:
            from .adapter_v2 import DocumentAdapterV2

            units.append(DocumentAdapterV2._unit(item))
    payload = {
        "publication_id": _identifier(
            "docpub",
            {
                "base": publication.publication_id,
                "entities": [item.entity_id for item in entities],
                "policy": DOCUMENT_CLAIM_POLICY_VERSION,
            },
        ),
        "source_payload_sha256": publication.source_payload_sha256,
        "family": publication.family,
        "version": version,
        "entities": entities,
        "edges": edges,
        "retrieval_units": tuple(units),
    }
    digest = canonical_sha256(
        {
            "publication_id": payload["publication_id"],
            "source_payload_sha256": payload["source_payload_sha256"],
            "family": publication.family.model_dump(mode="json"),
            "version": version.model_dump(mode="json"),
            "entities": [item.model_dump(mode="json") for item in entities],
            "edges": [item.model_dump(mode="json") for item in edges],
            "retrieval_units": [item.model_dump(mode="json") for item in units],
        }
    )
    return DocumentPublication(**payload, publication_sha256=digest)
