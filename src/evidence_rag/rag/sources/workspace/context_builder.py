"""Task-specific Workspace context with authority separation and stable citations."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .contracts import WorkspaceAuthority, canonical_sha256
from .retriever import WorkspaceCandidate, WorkspaceQueryTask, WorkspaceSearchResult

WORKSPACE_CONTEXT_BUILDER_VERSION = "workspace-context-builder-v2"


class _FrozenContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkspaceContextBlock(_FrozenContext):
    citation_id: str
    entity_id: str
    locator: str
    authority: WorkspaceAuthority
    roles: tuple[str, ...]
    body: str
    content_sha256: str
    truncated: bool


class WorkspaceContext(_FrozenContext):
    schema_version: str = WORKSPACE_CONTEXT_BUILDER_VERSION
    task: WorkspaceQueryTask
    availability: str
    blocks: tuple[WorkspaceContextBlock, ...]
    citation_map: dict[str, str]
    missing: tuple[str, ...]
    ambiguous_scope: bool
    used_characters: int = Field(ge=0)
    budget_characters: int = Field(ge=1)
    context_sha256: str
    reasoning_included: bool = False
    mutation_applied: bool = False


def _line_safe(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    clipped = value[: max(1, limit - 1)]
    if "\n" in clipped:
        clipped = clipped.rsplit("\n", 1)[0]
    return clipped.rstrip() + "…", True


def _body(candidate: WorkspaceCandidate, task: WorkspaceQueryTask) -> str:
    metadata = candidate.metadata
    entity_metadata = metadata.get("entity_metadata") or {}
    header = (
        f"[{candidate.authority.value} · {candidate.entity_type.value} · "
        f"{metadata.get('display_key')}]\n"
        f"Status: {candidate.status}\n"
        f"Owner: {metadata.get('owner') or 'unassigned'}\n"
        f"Effective: {metadata.get('effective_at')}"
    )
    detail = str(metadata.get("text") or candidate.label)
    if task == WorkspaceQueryTask.WORK:
        detail += (
            f"\nAssignee: {metadata.get('assignee') or 'unassigned'}"
            f"\nDue: {metadata.get('due_at') or 'none'}"
        )
    elif task == WorkspaceQueryTask.COVERAGE:
        detail += (
            f"\nRole: {entity_metadata.get('role') or 'n/a'}"
            f"\nLink status: {entity_metadata.get('link_status') or 'n/a'}"
            f"\nPinned generation: {entity_metadata.get('pinned_generation') or 'n/a'}"
        )
    elif task == WorkspaceQueryTask.INTELLIGENCE:
        detail += (
            f"\nPolicy: {entity_metadata.get('policy_version') or 'n/a'}"
            f"\nExpires: {metadata.get('expires_at') or 'n/a'}"
            "\nDerived intelligence does not mutate authoritative state."
        )
    elif task == WorkspaceQueryTask.TEMPORAL:
        detail += (
            f"\nBefore: {entity_metadata.get('before') or 'n/a'}"
            f"\nAfter: {entity_metadata.get('after') or 'n/a'}"
            f"\nActor: {entity_metadata.get('actor') or 'n/a'}"
        )
    return f"{header}\n{detail}"


def build_workspace_context(
    result: WorkspaceSearchResult,
    *,
    budget_characters: int = 12_000,
    per_block_characters: int = 1_800,
) -> WorkspaceContext:
    if budget_characters < 256 or per_block_characters < 128:
        raise ValueError("Workspace context budget is too small")
    blocks: list[WorkspaceContextBlock] = []
    citations: dict[str, str] = {}
    used = 0
    for candidate in result.candidates:
        remaining = budget_characters - used
        if remaining <= 64:
            break
        body, truncated = _line_safe(
            _body(candidate, result.profile.task),
            min(per_block_characters, remaining),
        )
        citation = (
            "W"
            + canonical_sha256(
                {
                    "entity_id": candidate.entity_id,
                    "content_sha256": candidate.content_sha256,
                    "locator": candidate.locator,
                }
            ).removeprefix("sha256:")[:10]
        )
        block = WorkspaceContextBlock(
            citation_id=citation,
            entity_id=candidate.entity_id,
            locator=candidate.locator,
            authority=candidate.authority,
            roles=candidate.roles,
            body=body,
            content_sha256=canonical_sha256(
                {
                    "entity_id": candidate.entity_id,
                    "locator": candidate.locator,
                    "body": body,
                    "authority": candidate.authority,
                    "roles": candidate.roles,
                }
            ),
            truncated=truncated,
        )
        blocks.append(block)
        citations[citation] = candidate.locator
        used += len(body)
    if not blocks:
        availability = "UNAVAILABLE"
    elif result.missing or result.ambiguous_scope or len(blocks) < len(result.candidates):
        availability = "PROVISIONAL"
    else:
        availability = "AVAILABLE"
    payload = {
        "schema_version": WORKSPACE_CONTEXT_BUILDER_VERSION,
        "task": result.profile.task,
        "availability": availability,
        "blocks": [item.model_dump(mode="json") for item in blocks],
        "citation_map": citations,
        "missing": result.missing,
        "ambiguous_scope": result.ambiguous_scope,
        "used_characters": used,
        "budget_characters": budget_characters,
        "reasoning_included": False,
        "mutation_applied": False,
    }
    return WorkspaceContext(**payload, context_sha256=canonical_sha256(payload))


__all__ = [
    "WORKSPACE_CONTEXT_BUILDER_VERSION",
    "WorkspaceContext",
    "WorkspaceContextBlock",
    "build_workspace_context",
]
