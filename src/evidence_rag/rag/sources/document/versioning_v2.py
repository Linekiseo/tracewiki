"""Deterministic cross-version alignment and staleness derivation."""

from __future__ import annotations

import re

from .contracts import (
    DocumentComparison,
    DocumentEntity,
    DocumentEntityType,
    DocumentPublication,
    canonical_sha256,
)

DOCUMENT_VERSION_ALIGNER_VERSION = "document-version-aligner-v2"
_TOKEN_RE = re.compile(r"[\w.+%-]+", re.UNICODE)


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in _TOKEN_RE.findall(value) if len(item) > 1}


def _similarity(left: DocumentEntity, right: DocumentEntity) -> float:
    if left.entity_type != right.entity_type:
        return 0.0
    score = 0.0
    if left.label and right.label and left.label.casefold() == right.label.casefold():
        score += 0.45
    left_tokens = _tokens(f"{left.source_text} {left.derived_text}")
    right_tokens = _tokens(f"{right.source_text} {right.derived_text}")
    if left_tokens or right_tokens:
        score += 0.55 * len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    return score


def compare_document_versions(
    baseline: DocumentPublication,
    candidate: DocumentPublication,
) -> DocumentComparison:
    if baseline.family.family_id != candidate.family.family_id:
        raise ValueError("document comparison requires the same family")
    if baseline.family.scope != candidate.family.scope:
        raise ValueError("document comparison requires exact governed scope")
    if baseline.version.version_id == candidate.version.version_id:
        raise ValueError("document comparison requires distinct versions")
    old = tuple(
        item
        for item in baseline.entities
        if item.entity_type
        not in {
            DocumentEntityType.PAGE,
            DocumentEntityType.LAYOUT_BLOCK,
            DocumentEntityType.SECTION_SUMMARY,
            DocumentEntityType.CLAIM_CANDIDATE,
        }
    )
    new = tuple(
        item
        for item in candidate.entities
        if item.entity_type
        not in {
            DocumentEntityType.PAGE,
            DocumentEntityType.LAYOUT_BLOCK,
            DocumentEntityType.SECTION_SUMMARY,
            DocumentEntityType.CLAIM_CANDIDATE,
        }
    )
    unmatched_new = {item.entity_id: item for item in new}
    aligned: list[tuple[DocumentEntity, DocumentEntity, float]] = []
    removed: list[str] = []
    for left in old:
        scored = sorted(
            (
                (_similarity(left, right), right.entity_id, right)
                for right in unmatched_new.values()
            ),
            key=lambda item: (-item[0], item[1]),
        )
        if not scored or scored[0][0] < 0.45:
            removed.append(left.entity_id)
            continue
        score, _, right = scored[0]
        aligned.append((left, right, score))
        unmatched_new.pop(right.entity_id)
    added = sorted(unmatched_new)
    modified: list[str] = []
    moved: list[str] = []
    stale: list[str] = []
    for left, right, _score in aligned:
        if left.content_sha256 != right.content_sha256:
            modified.extend((left.entity_id, right.entity_id))
            if left.entity_type == DocumentEntityType.CLAIM:
                stale.append(left.entity_id)
        if left.parent_id != right.parent_id or left.ordinal != right.ordinal:
            moved.extend((left.entity_id, right.entity_id))
    payload = {
        "scope": baseline.family.scope.model_dump(mode="json"),
        "family_id": baseline.family.family_id,
        "baseline_version_id": baseline.version.version_id,
        "candidate_version_id": candidate.version.version_id,
        "added_ids": tuple(added),
        "removed_ids": tuple(sorted(removed)),
        "modified_ids": tuple(sorted(set(modified))),
        "moved_ids": tuple(sorted(set(moved))),
        "stale_claim_ids": tuple(sorted(set(stale))),
        "aligner_version": DOCUMENT_VERSION_ALIGNER_VERSION,
    }
    comparison_id = "doccompare-" + canonical_sha256(payload).removeprefix("sha256:")[:32]
    return DocumentComparison(
        comparison_id=comparison_id,
        scope=baseline.family.scope,
        family_id=baseline.family.family_id,
        baseline_version_id=baseline.version.version_id,
        candidate_version_id=candidate.version.version_id,
        added_ids=payload["added_ids"],
        removed_ids=payload["removed_ids"],
        modified_ids=payload["modified_ids"],
        moved_ids=payload["moved_ids"],
        stale_claim_ids=payload["stale_claim_ids"],
        comparison_sha256=canonical_sha256({"comparison_id": comparison_id, **payload}),
    )
