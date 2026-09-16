"""Deterministic six-source compiler for the agent-native Wiki."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..global_governance_v2 import inspect_untrusted_content_v2
from ..multisource_foundation_v2 import MULTISOURCE_DOMAINS, MultiSourceCandidateV2
from .contracts_v1 import (
    WIKI_CONTRACT_VERSION,
    SourceGenerationV1,
    WikiDirectoryV1,
    WikiFactAuthorityV1,
    WikiFactStatusV1,
    WikiGenerationManifestV1,
    WikiGenerationStatusV1,
    WikiLinkStatusV1,
    WikiPageFragmentV1,
    WikiPageTypeV1,
    WikiRecordIndexV1,
    WikiRecordKindV1,
    WikiScopeV1,
    WikiSourceDomainV1,
    WikiSourceRefV1,
    build_wiki_directory_v1,
    build_wiki_fact_v1,
    build_wiki_generation_manifest_v1,
    build_wiki_link_v1,
    build_wiki_page_fragment_v1,
    build_wiki_source_ref_v1,
    canonical_sha256_v1,
    visibility_partition_v1,
)
from .paths_v1 import (
    WIKI_INTENT_DIRECTORIES,
    WIKI_PATH_VERSION,
    wiki_logical_path_v1,
    wiki_record_physical_key_v1,
    wiki_slug_v1,
)

WIKI_COMPILER_PIPELINE_VERSION = "six-source-wiki-compiler-v1"
WIKI_DEPENDENCY_INDEX_VERSION = "wiki-source-page-dependency-index-v1"
WIKI_ERROR_BOOK_VERSION = "wiki-persistent-error-book-v1"
WIKI_COMPILER_AUTHORITY_SHA256 = canonical_sha256_v1(
    {
        "compiler": WIKI_COMPILER_PIPELINE_VERSION,
        "contract": WIKI_CONTRACT_VERSION,
        "path": WIKI_PATH_VERSION,
        "rules": (
            "full-content-security-before-summary",
            "single-acl-visibility-fragments",
            "source-generation-exact",
            "raw-source-ref-required",
            "bidirectional-grounded-links",
            "child-first-generation-manifest",
        ),
        "sources": MULTISOURCE_DOMAINS,
    }
)


class WikiCompilerError(ValueError):
    """Raised when the compiler authority or source batch is invalid."""


class WikiCompilerDiagnosticCodeV1(StrEnum):
    UNSAFE_CONTENT = "unsafe_content"
    INVALID_LOCATOR = "invalid_locator"
    EMPTY_EVIDENCE = "empty_evidence"
    UNSUPPORTED_ENTITY = "unsupported_entity"


class WikiErrorStatusV1(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class _FrozenCompiler(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class WikiCompilationRequestV1(_FrozenCompiler):
    request_id: str
    project_id: str
    generation_id: str
    source_generations: tuple[SourceGenerationV1, ...] = Field(min_length=6, max_length=6)
    candidates: tuple[MultiSourceCandidateV2, ...]
    observed_at: str
    compiler_authority_sha256: Literal[WIKI_COMPILER_AUTHORITY_SHA256] = (
        WIKI_COMPILER_AUTHORITY_SHA256
    )
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiCompilationRequestV1:
        if tuple(item.source.value for item in self.source_generations) != MULTISOURCE_DOMAINS:
            raise WikiCompilerError("compiler request requires the exact governed six-source order")
        candidate_ids = tuple(item.candidate_id for item in self.candidates)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise WikiCompilerError("compiler candidates must be ID-sorted and unique")
        generation_by_source = {
            item.source.value: item.generation_id for item in self.source_generations
        }
        for candidate in self.candidates:
            if generation_by_source[candidate.retrieval_domain] != candidate.source_generation:
                raise WikiCompilerError("candidate source generation differs from compiler scope")
            if candidate.acl_ref.strip() != candidate.acl_ref or not candidate.acl_ref:
                raise WikiCompilerError("candidate ACL authority is invalid")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiCompilerError("compiler request digest mismatch")
        return self


class WikiCompilerDiagnosticV1(_FrozenCompiler):
    diagnostic_id: str
    fingerprint_sha256: str
    source: WikiSourceDomainV1
    candidate_sha256: str
    code: WikiCompilerDiagnosticCodeV1
    reason_code: str
    generation_id: str
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiCompilerDiagnosticV1:
        normalized = {
            "code": self.code.value,
            "reason_code": self.reason_code,
            "source": self.source.value,
        }
        if self.fingerprint_sha256 != canonical_sha256_v1(normalized):
            raise WikiCompilerError("compiler diagnostic fingerprint mismatch")
        if self.diagnostic_id != "wiki-diagnostic-" + self.fingerprint_sha256[7:31]:
            raise WikiCompilerError("compiler diagnostic identity mismatch")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiCompilerError("compiler diagnostic digest mismatch")
        return self


class WikiErrorBookEntryV1(_FrozenCompiler):
    entry_id: str
    fingerprint_sha256: str
    source: WikiSourceDomainV1
    code: WikiCompilerDiagnosticCodeV1
    reason_code: str
    constraint: str
    first_seen_generation: str
    last_seen_generation: str
    occurrences: int = Field(ge=1)
    status: WikiErrorStatusV1
    error_book_version: Literal[WIKI_ERROR_BOOK_VERSION] = WIKI_ERROR_BOOK_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiErrorBookEntryV1:
        if self.entry_id != "wiki-error-" + self.fingerprint_sha256[7:31]:
            raise WikiCompilerError("Error Book entry identity mismatch")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiCompilerError("Error Book entry digest mismatch")
        return self


class WikiDependencyIndexV1(_FrozenCompiler):
    source_to_pages: tuple[tuple[str, tuple[str, ...]], ...]
    page_to_sources: tuple[tuple[str, tuple[str, ...]], ...]
    index_version: Literal[WIKI_DEPENDENCY_INDEX_VERSION] = WIKI_DEPENDENCY_INDEX_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiDependencyIndexV1:
        for mappings, label in (
            (self.source_to_pages, "source-to-pages"),
            (self.page_to_sources, "page-to-sources"),
        ):
            keys = tuple(item[0] for item in mappings)
            if keys != tuple(sorted(set(keys))):
                raise WikiCompilerError(f"dependency {label} keys must be sorted and unique")
            if any(values != tuple(sorted(set(values))) for _, values in mappings):
                raise WikiCompilerError(f"dependency {label} values must be sorted and unique")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiCompilerError("dependency index digest mismatch")
        return self


class WikiCompilationResultV1(_FrozenCompiler):
    request_sha256: str
    manifest: WikiGenerationManifestV1
    records: tuple[WikiDirectoryV1 | WikiPageFragmentV1, ...]
    diagnostics: tuple[WikiCompilerDiagnosticV1, ...]
    error_book: tuple[WikiErrorBookEntryV1, ...]
    dependency_index: WikiDependencyIndexV1
    input_candidate_count: int = Field(ge=0)
    compiled_candidate_count: int = Field(ge=0)
    quarantined_candidate_count: int = Field(ge=0)
    compiler_version: Literal[WIKI_COMPILER_PIPELINE_VERSION] = WIKI_COMPILER_PIPELINE_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiCompilationResultV1:
        if (
            self.input_candidate_count
            != self.compiled_candidate_count + self.quarantined_candidate_count
        ):
            raise WikiCompilerError("compiler candidate accounting mismatch")
        record_index = tuple(
            (
                item.scope.visibility_partition,
                item.logical_path,
                (
                    WikiRecordKindV1.DIRECTORY.value
                    if isinstance(item, WikiDirectoryV1)
                    else WikiRecordKindV1.PAGE_FRAGMENT.value
                ),
                item.content_sha256,
            )
            for item in self.records
        )
        manifest_index = tuple(
            (
                item.visibility_partition,
                item.logical_path,
                item.record_kind.value,
                item.record_sha256,
            )
            for item in self.manifest.records
        )
        if record_index != manifest_index:
            raise WikiCompilerError("compiler result records differ from the generation manifest")
        if tuple(item.diagnostic_id for item in self.diagnostics) != tuple(
            sorted(set(item.diagnostic_id for item in self.diagnostics))
        ):
            raise WikiCompilerError("compiler diagnostics must be sorted and unique")
        if tuple(item.entry_id for item in self.error_book) != tuple(
            sorted(set(item.entry_id for item in self.error_book))
        ):
            raise WikiCompilerError("Error Book entries must be sorted and unique")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiCompilerError("compiler result digest mismatch")
        return self


class WikiIncrementalCompilationV1(_FrozenCompiler):
    previous_result_sha256: str
    result: WikiCompilationResultV1
    changed_candidate_ids: tuple[str, ...]
    affected_page_paths: tuple[str, ...]
    reused_records: tuple[tuple[str, str], ...]
    rebuilt_records: tuple[tuple[str, str], ...]
    removed_records: tuple[tuple[str, str], ...]
    full_rebuild_equivalent: Literal[True] = True
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiIncrementalCompilationV1:
        if self.changed_candidate_ids != tuple(sorted(set(self.changed_candidate_ids))):
            raise WikiCompilerError("incremental candidate changes must be sorted and unique")
        if self.affected_page_paths != tuple(sorted(set(self.affected_page_paths))):
            raise WikiCompilerError("incremental affected pages must be sorted and unique")
        for values, label in (
            (self.reused_records, "reused"),
            (self.rebuilt_records, "rebuilt"),
            (self.removed_records, "removed"),
        ):
            if values != tuple(sorted(set(values))):
                raise WikiCompilerError(f"incremental {label} records must be sorted and unique")
        if set(self.reused_records) & set(self.rebuilt_records):
            raise WikiCompilerError("incremental records cannot be both reused and rebuilt")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiCompilerError("incremental compilation digest mismatch")
        return self


@dataclass
class _PageDraft:
    logical_path: str
    page_type: WikiPageTypeV1
    title: str
    summary: str
    scope: WikiScopeV1
    aliases: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    source_refs: dict[str, WikiSourceRefV1] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    links: dict[str, Any] = field(default_factory=dict)
    evidence_roles: set[str] = field(default_factory=set)


def build_wiki_compilation_request_v1(**payload: Any) -> WikiCompilationRequestV1:
    normalized = WikiCompilationRequestV1.model_construct(
        **payload, content_sha256="pending"
    ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
    return WikiCompilationRequestV1.model_validate(
        {**payload, "content_sha256": canonical_sha256_v1(normalized)}
    )


def _diagnostic(
    candidate: MultiSourceCandidateV2,
    *,
    code: WikiCompilerDiagnosticCodeV1,
    reason_code: str,
    generation_id: str,
) -> WikiCompilerDiagnosticV1:
    candidate_sha256 = canonical_sha256_v1(candidate.model_dump(mode="json"))
    fingerprint = canonical_sha256_v1(
        {
            "code": code.value,
            "reason_code": reason_code,
            "source": candidate.retrieval_domain,
        }
    )
    payload = {
        "diagnostic_id": "wiki-diagnostic-" + fingerprint[7:31],
        "fingerprint_sha256": fingerprint,
        "source": WikiSourceDomainV1(candidate.retrieval_domain),
        "candidate_sha256": candidate_sha256,
        "code": code,
        "reason_code": reason_code,
        "generation_id": generation_id,
    }
    return WikiCompilerDiagnosticV1(
        **payload,
        content_sha256=canonical_sha256_v1(
            {**payload, "source": candidate.retrieval_domain, "code": code.value}
        ),
    )


def _candidate_security_reason(candidate: MultiSourceCandidateV2) -> str | None:
    for label, value in (
        ("title", candidate.title),
        ("snippet", candidate.snippet),
        ("locator", candidate.locator),
    ):
        blocked, reason = inspect_untrusted_content_v2(
            value,
            requester_acl_refs=(candidate.acl_ref,),
            evidence_acl_ref=candidate.acl_ref,
        )
        if blocked:
            return f"{label}_{reason}"
    if not candidate.snippet.strip():
        return "empty_snippet"
    if not candidate.locator.startswith(f"{candidate.retrieval_domain}://"):
        return "typed_locator_mismatch"
    return None


def _source_ref(
    candidate: MultiSourceCandidateV2, *, watermark: str, observed_at: str
) -> WikiSourceRefV1:
    candidate_sha256 = canonical_sha256_v1(candidate.model_dump(mode="json"))
    source_ref_id = "wiki-source-ref-" + candidate_sha256[7:31]
    return build_wiki_source_ref_v1(
        source_ref_id=source_ref_id,
        source=WikiSourceDomainV1(candidate.retrieval_domain),
        entity_type="entity-" + canonical_sha256_v1(candidate.entity_type)[7:23],
        entity_id="entity-" + canonical_sha256_v1(candidate.entity_id)[7:31],
        locator=candidate.locator,
        generation_id=candidate.source_generation,
        watermark=watermark,
        acl_refs=(candidate.acl_ref,),
        observed_at=observed_at,
        raw_content_sha256=candidate_sha256,
    )


def _fact_status(candidate: MultiSourceCandidateV2) -> WikiFactStatusV1:
    if candidate.derivation == "live_project_inventory_v1":
        return WikiFactStatusV1.ACTIVE
    lowered = candidate.fact_status.lower()
    if "contradict" in lowered:
        return WikiFactStatusV1.CONTRADICTED
    if "supersed" in lowered:
        return WikiFactStatusV1.SUPERSEDED
    if "stale" in lowered or candidate.version_alignment == "mismatch":
        return WikiFactStatusV1.STALE
    if (
        candidate.raw_or_derived == "derived_fact"
        and "review" not in candidate.review_status.lower()
    ):
        return WikiFactStatusV1.PROPOSED
    return WikiFactStatusV1.ACTIVE


def _fact_authority(candidate: MultiSourceCandidateV2) -> WikiFactAuthorityV1:
    if candidate.derivation == "live_project_inventory_v1":
        return WikiFactAuthorityV1.DETERMINISTIC_DERIVED
    if candidate.raw_or_derived == "raw_fact":
        return WikiFactAuthorityV1.RAW_OBSERVED
    if "review" in candidate.review_status.lower() or "accept" in candidate.review_status.lower():
        return WikiFactAuthorityV1.REVIEWED_SYNTHESIS
    return WikiFactAuthorityV1.UNREVIEWED_SUGGESTION


def _fact_id(path: str, predicate: str, source_ref_id: str) -> str:
    return "wiki-fact-" + canonical_sha256_v1((path, predicate, source_ref_id))[7:31]


def _link_id(path: str, target: str, relation: str, source_ref_id: str) -> str:
    return "wiki-link-" + canonical_sha256_v1((path, target, relation, source_ref_id))[7:31]


def _get_draft(
    drafts: dict[tuple[str, str], _PageDraft],
    *,
    path: str,
    page_type: WikiPageTypeV1,
    title: str,
    summary: str,
    scope: WikiScopeV1,
) -> _PageDraft:
    key = (scope.visibility_partition, path)
    draft = drafts.get(key)
    if draft is None:
        draft = _PageDraft(path, page_type, title, summary, scope)
        drafts[key] = draft
    elif draft.page_type is not page_type or draft.scope != scope:
        raise WikiCompilerError("page identity collision across incompatible compiler drafts")
    return draft


def _add_fact(
    draft: _PageDraft,
    *,
    predicate: str,
    source_ref: WikiSourceRefV1,
    candidate: MultiSourceCandidateV2,
    object_text: str | None = None,
    object_path: str | None = None,
    deterministic: bool = False,
) -> None:
    fact_id = _fact_id(draft.logical_path, predicate, source_ref.source_ref_id)
    authority = (
        WikiFactAuthorityV1.DETERMINISTIC_DERIVED if deterministic else _fact_authority(candidate)
    )
    status = WikiFactStatusV1.ACTIVE if deterministic else _fact_status(candidate)
    draft.facts[fact_id] = build_wiki_fact_v1(
        fact_id=fact_id,
        subject_path=draft.logical_path,
        predicate=predicate,
        object_text=object_text,
        object_path=object_path,
        source_ref_ids=(source_ref.source_ref_id,),
        authority=authority,
        status=status,
        confidence=candidate.authority,
        valid_from=None,
        valid_to=None,
    )


def _add_link(
    draft: _PageDraft,
    *,
    target: str,
    relation: str,
    inverse: str,
    source_ref: WikiSourceRefV1,
    confidence: float,
) -> None:
    link_id = _link_id(draft.logical_path, target, relation, source_ref.source_ref_id)
    draft.links[link_id] = build_wiki_link_v1(
        link_id=link_id,
        source_path=draft.logical_path,
        target_path=target,
        relation=relation,
        inverse_relation=inverse,
        source_ref_ids=(source_ref.source_ref_id,),
        status=WikiLinkStatusV1.ACTIVE,
        confidence=confidence,
    )


def _compile_candidate(
    candidate: MultiSourceCandidateV2,
    *,
    scope: WikiScopeV1,
    watermark: str,
    observed_at: str,
    drafts: dict[tuple[str, str], _PageDraft],
) -> None:
    source_ref = _source_ref(candidate, watermark=watermark, observed_at=observed_at)
    source_slug = wiki_slug_v1(
        candidate.title,
        stable_identity=f"{candidate.retrieval_domain}:{candidate.entity_id}:{candidate.stable_version}",
    )
    source_path = wiki_logical_path_v1("sources", candidate.retrieval_domain, source_slug)
    source_title = candidate.title[:1_000] if candidate.title else candidate.entity_type
    source_summary = candidate.snippet[:8_000]
    source_draft = _get_draft(
        drafts,
        path=source_path,
        page_type=WikiPageTypeV1.SOURCE,
        title=source_title,
        summary=source_summary,
        scope=scope,
    )
    source_draft.source_refs[source_ref.source_ref_id] = source_ref
    source_draft.tags.update((candidate.retrieval_domain, "raw-evidence"))
    source_draft.evidence_roles.update(candidate.matched_roles or (candidate.fact_type,))
    _add_fact(
        source_draft,
        predicate="evidence.summary",
        source_ref=source_ref,
        candidate=candidate,
        object_text=source_summary,
    )

    component_slug = wiki_slug_v1(
        candidate.entity_type,
        stable_identity=f"component:{candidate.retrieval_domain}:{candidate.entity_type}",
    )
    component_path = wiki_logical_path_v1("components", component_slug)
    component_draft = _get_draft(
        drafts,
        path=component_path,
        page_type=WikiPageTypeV1.COMPONENT,
        title=f"{candidate.retrieval_domain} · {candidate.entity_type}"[:1_000],
        summary="A typed component assembled from governed raw source evidence.",
        scope=scope,
    )
    component_draft.source_refs[source_ref.source_ref_id] = source_ref
    component_draft.tags.update((candidate.retrieval_domain, "typed-component"))
    component_draft.evidence_roles.update(candidate.matched_roles)
    _add_fact(
        component_draft,
        predicate="contains.entity",
        source_ref=source_ref,
        candidate=candidate,
        object_path=source_path,
        deterministic=True,
    )
    _add_link(
        component_draft,
        target=source_path,
        relation="grounded_by",
        inverse="grounds",
        source_ref=source_ref,
        confidence=candidate.authority,
    )
    _add_link(
        source_draft,
        target=component_path,
        relation="member_of",
        inverse="has_member",
        source_ref=source_ref,
        confidence=candidate.authority,
    )

    roles = candidate.matched_roles or (candidate.task,)
    for role in roles:
        role_slug = wiki_slug_v1(role, stable_identity=f"capability:{role}")
        capability_path = wiki_logical_path_v1("capabilities", role_slug)
        capability_draft = _get_draft(
            drafts,
            path=capability_path,
            page_type=WikiPageTypeV1.CAPABILITY,
            title=f"Capability · {role}"[:1_000],
            summary="A cross-source capability page compiled from independently governed evidence.",
            scope=scope,
        )
        capability_draft.source_refs[source_ref.source_ref_id] = source_ref
        capability_draft.tags.update(("cross-source", "capability"))
        capability_draft.evidence_roles.add(role)
        _add_fact(
            capability_draft,
            predicate="supported_by.source",
            source_ref=source_ref,
            candidate=candidate,
            object_path=source_path,
            deterministic=True,
        )
        _add_link(
            capability_draft,
            target=source_path,
            relation="supported_by",
            inverse="supports",
            source_ref=source_ref,
            confidence=candidate.authority,
        )
        _add_link(
            source_draft,
            target=capability_path,
            relation="supports",
            inverse="supported_by",
            source_ref=source_ref,
            confidence=candidate.authority,
        )

    specialized: tuple[tuple[str, WikiPageTypeV1, str], ...] = ()
    task = candidate.task.casefold()
    if candidate.retrieval_domain == "experiment":
        specialized = (("experiments", WikiPageTypeV1.EXPERIMENT, "experiment.evidence"),)
    elif candidate.counter_evidence:
        specialized = (("findings", WikiPageTypeV1.FINDING, "finding.counter_evidence"),)
    elif any(token in task for token in ("issue", "failure", "error", "blocker")):
        specialized = (("issues", WikiPageTypeV1.ISSUE, "issue.evidence"),)
    elif "architecture" in task or "design" in task:
        specialized = (("architecture", WikiPageTypeV1.ARCHITECTURE, "architecture.evidence"),)
    elif "decision" in candidate.entity_type.lower() or "rationale" in task:
        specialized = (("decisions", WikiPageTypeV1.DECISION, "decision.evidence"),)
    elif any(token in task for token in ("change", "historical", "patch")):
        specialized = (("changes", WikiPageTypeV1.CHANGE, "change.evidence"),)
    elif any(token in task for token in ("procedure", "validation", "reproduction", "workflow")):
        specialized = (("procedures", WikiPageTypeV1.PROCEDURE, "procedure.evidence"),)
    elif any(token in task for token in ("requirement", "objective", "acceptance")):
        specialized = (("requirements", WikiPageTypeV1.REQUIREMENT, "requirement.evidence"),)
    for intent, page_type, predicate in specialized:
        special_path = wiki_logical_path_v1(
            intent,
            wiki_slug_v1(
                candidate.title,
                stable_identity=f"{intent}:{candidate.candidate_id}:{candidate.stable_version}",
            ),
        )
        special = _get_draft(
            drafts,
            path=special_path,
            page_type=page_type,
            title=source_title,
            summary=source_summary,
            scope=scope,
        )
        special.source_refs[source_ref.source_ref_id] = source_ref
        special.tags.update((candidate.retrieval_domain, page_type.value))
        special.evidence_roles.update(roles)
        _add_fact(
            special,
            predicate=predicate,
            source_ref=source_ref,
            candidate=candidate,
            object_path=source_path,
            deterministic=True,
        )
        _add_link(
            special,
            target=source_path,
            relation="grounded_by",
            inverse="grounds",
            source_ref=source_ref,
            confidence=candidate.authority,
        )
        _add_link(
            source_draft,
            target=special_path,
            relation="grounds",
            inverse="grounded_by",
            source_ref=source_ref,
            confidence=candidate.authority,
        )


def update_wiki_error_book_v1(
    existing: tuple[WikiErrorBookEntryV1, ...],
    diagnostics: tuple[WikiCompilerDiagnosticV1, ...],
    *,
    generation_id: str,
) -> tuple[WikiErrorBookEntryV1, ...]:
    by_fingerprint = {item.fingerprint_sha256: item for item in existing}
    for diagnostic in diagnostics:
        previous = by_fingerprint.get(diagnostic.fingerprint_sha256)
        payload = {
            "entry_id": "wiki-error-" + diagnostic.fingerprint_sha256[7:31],
            "fingerprint_sha256": diagnostic.fingerprint_sha256,
            "source": diagnostic.source,
            "code": diagnostic.code,
            "reason_code": diagnostic.reason_code,
            "constraint": f"reject-before-page:{diagnostic.code.value}:{diagnostic.reason_code}",
            "first_seen_generation": (
                previous.first_seen_generation if previous is not None else generation_id
            ),
            "last_seen_generation": generation_id,
            "occurrences": previous.occurrences + 1 if previous is not None else 1,
            "status": WikiErrorStatusV1.OPEN,
            "error_book_version": WIKI_ERROR_BOOK_VERSION,
        }
        by_fingerprint[diagnostic.fingerprint_sha256] = WikiErrorBookEntryV1(
            **payload,
            content_sha256=canonical_sha256_v1(
                {
                    **payload,
                    "source": diagnostic.source.value,
                    "code": diagnostic.code.value,
                    "status": WikiErrorStatusV1.OPEN.value,
                }
            ),
        )
    return tuple(sorted(by_fingerprint.values(), key=lambda item: item.entry_id))


def compile_wiki_v1(
    request: WikiCompilationRequestV1,
    *,
    previous_error_book: tuple[WikiErrorBookEntryV1, ...] = (),
) -> WikiCompilationResultV1:
    """Compile a complete immutable generation from governed six-source candidates."""

    generation_by_source = {item.source.value: item for item in request.source_generations}
    scopes = {
        acl_ref: WikiScopeV1(
            project_id=request.project_id,
            visibility_partition=visibility_partition_v1((acl_ref,)),
            acl_refs=(acl_ref,),
            source_generations=request.source_generations,
            as_of=request.observed_at,
        )
        for acl_ref in sorted({item.acl_ref for item in request.candidates} or {"public"})
    }
    diagnostics: list[WikiCompilerDiagnosticV1] = []
    drafts: dict[tuple[str, str], _PageDraft] = {}
    compiled_count = 0
    for candidate in request.candidates:
        reason = _candidate_security_reason(candidate)
        if reason is not None:
            code = (
                WikiCompilerDiagnosticCodeV1.EMPTY_EVIDENCE
                if reason == "empty_snippet"
                else WikiCompilerDiagnosticCodeV1.INVALID_LOCATOR
                if reason == "typed_locator_mismatch"
                else WikiCompilerDiagnosticCodeV1.UNSAFE_CONTENT
            )
            diagnostics.append(
                _diagnostic(
                    candidate,
                    code=code,
                    reason_code=reason,
                    generation_id=request.generation_id,
                )
            )
            continue
        source_generation = generation_by_source[candidate.retrieval_domain]
        _compile_candidate(
            candidate,
            scope=scopes[candidate.acl_ref],
            watermark=source_generation.watermark,
            observed_at=request.observed_at,
            drafts=drafts,
        )
        compiled_count += 1

    pages = tuple(
        build_wiki_page_fragment_v1(
            fragment_id="wiki-fragment-"
            + canonical_sha256_v1((partition, path, WIKI_COMPILER_PIPELINE_VERSION))[7:31],
            logical_path=draft.logical_path,
            page_type=draft.page_type,
            title=draft.title,
            summary=draft.summary,
            aliases=tuple(sorted(draft.aliases)),
            tags=tuple(sorted(draft.tags)),
            scope=WikiScopeV1(
                project_id=draft.scope.project_id,
                visibility_partition=draft.scope.visibility_partition,
                acl_refs=draft.scope.acl_refs,
                source_generations=tuple(
                    source_generation
                    for source_generation in request.source_generations
                    if source_generation.source
                    in {source_ref.source for source_ref in draft.source_refs.values()}
                ),
                as_of=draft.scope.as_of,
            ),
            source_refs=tuple(
                sorted(draft.source_refs.values(), key=lambda item: item.source_ref_id)
            ),
            facts=tuple(sorted(draft.facts.values(), key=lambda item: item.fact_id)),
            links=tuple(sorted(draft.links.values(), key=lambda item: item.link_id)),
            evidence_roles=tuple(sorted(draft.evidence_roles)),
        )
        for (partition, path), draft in sorted(drafts.items())
    )
    pages_by_partition: dict[str, tuple[WikiPageFragmentV1, ...]] = {}
    for partition in sorted(scopes[item].visibility_partition for item in scopes):
        pages_by_partition[partition] = tuple(
            page for page in pages if page.scope.visibility_partition == partition
        )

    directories: list[WikiDirectoryV1] = []
    for scope in sorted(scopes.values(), key=lambda item: item.visibility_partition):
        partition_pages = pages_by_partition[scope.visibility_partition]
        source_children = tuple(sorted(f"/sources/{source}" for source in MULTISOURCE_DOMAINS))
        for intent in WIKI_INTENT_DIRECTORIES:
            directories.append(
                build_wiki_directory_v1(
                    directory_id="wiki-directory-"
                    + canonical_sha256_v1(
                        (scope.visibility_partition, intent, WIKI_COMPILER_PIPELINE_VERSION)
                    )[7:31],
                    logical_path=f"/{intent}",
                    scope=scope,
                    child_paths=source_children if intent == "sources" else (),
                    page_paths=tuple(
                        sorted(
                            page.logical_path
                            for page in partition_pages
                            if page.logical_path.startswith(f"/{intent}/")
                            and not (intent == "sources" and page.logical_path.count("/") > 2)
                        )
                    ),
                )
            )
        for source in MULTISOURCE_DOMAINS:
            directories.append(
                build_wiki_directory_v1(
                    directory_id="wiki-directory-"
                    + canonical_sha256_v1(
                        (
                            scope.visibility_partition,
                            "sources",
                            source,
                            WIKI_COMPILER_PIPELINE_VERSION,
                        )
                    )[7:31],
                    logical_path=f"/sources/{source}",
                    scope=scope,
                    child_paths=(),
                    page_paths=tuple(
                        sorted(
                            page.logical_path
                            for page in partition_pages
                            if page.logical_path.startswith(f"/sources/{source}/")
                        )
                    ),
                )
            )
    records = tuple(
        sorted(
            (*directories, *pages),
            key=lambda item: (
                item.scope.visibility_partition,
                item.logical_path,
                (
                    WikiRecordKindV1.DIRECTORY.value
                    if isinstance(item, WikiDirectoryV1)
                    else WikiRecordKindV1.PAGE_FRAGMENT.value
                ),
            ),
        )
    )
    indexes = tuple(
        WikiRecordIndexV1(
            logical_path=record.logical_path,
            record_kind=(
                WikiRecordKindV1.DIRECTORY
                if isinstance(record, WikiDirectoryV1)
                else WikiRecordKindV1.PAGE_FRAGMENT
            ),
            visibility_partition=record.scope.visibility_partition,
            record_sha256=record.content_sha256,
            physical_key=wiki_record_physical_key_v1(
                project_id=request.project_id,
                generation_id=request.generation_id,
                visibility_partition=record.scope.visibility_partition,
                logical_path=record.logical_path,
                record_kind=(
                    WikiRecordKindV1.DIRECTORY.value
                    if isinstance(record, WikiDirectoryV1)
                    else WikiRecordKindV1.PAGE_FRAGMENT.value
                ),
            ),
        )
        for record in records
    )
    manifest = build_wiki_generation_manifest_v1(
        snapshot_id="wiki-snapshot-" + canonical_sha256_v1(request.content_sha256)[7:31],
        project_id=request.project_id,
        generation_id=request.generation_id,
        status=WikiGenerationStatusV1.VERIFIED,
        source_generations=request.source_generations,
        visibility_partitions=tuple(
            sorted(scope.visibility_partition for scope in scopes.values())
        ),
        records=indexes,
        root_paths=tuple(f"/{intent}" for intent in WIKI_INTENT_DIRECTORIES),
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
        created_at=request.observed_at,
    )
    source_to_pages: dict[str, set[str]] = defaultdict(set)
    page_to_sources: dict[str, set[str]] = defaultdict(set)
    for page in pages:
        for source_ref in page.source_refs:
            source_to_pages[source_ref.source_ref_id].add(page.logical_path)
            page_to_sources[page.logical_path].add(source_ref.source_ref_id)
    dependency_payload = {
        "source_to_pages": tuple(
            (key, tuple(sorted(values))) for key, values in sorted(source_to_pages.items())
        ),
        "page_to_sources": tuple(
            (key, tuple(sorted(values))) for key, values in sorted(page_to_sources.items())
        ),
        "index_version": WIKI_DEPENDENCY_INDEX_VERSION,
    }
    dependency = WikiDependencyIndexV1(
        **dependency_payload,
        content_sha256=canonical_sha256_v1(dependency_payload),
    )
    ordered_diagnostics = tuple(sorted(diagnostics, key=lambda item: item.diagnostic_id))
    error_book = update_wiki_error_book_v1(
        previous_error_book,
        ordered_diagnostics,
        generation_id=request.generation_id,
    )
    result_payload = {
        "request_sha256": request.content_sha256,
        "manifest": manifest,
        "records": records,
        "diagnostics": ordered_diagnostics,
        "error_book": error_book,
        "dependency_index": dependency,
        "input_candidate_count": len(request.candidates),
        "compiled_candidate_count": compiled_count,
        "quarantined_candidate_count": len(request.candidates) - compiled_count,
        "compiler_version": WIKI_COMPILER_PIPELINE_VERSION,
    }
    return WikiCompilationResultV1(
        **result_payload,
        content_sha256=canonical_sha256_v1(
            {
                **result_payload,
                "manifest": manifest.model_dump(mode="json"),
                "records": [item.model_dump(mode="json") for item in records],
                "diagnostics": [item.model_dump(mode="json") for item in ordered_diagnostics],
                "error_book": [item.model_dump(mode="json") for item in error_book],
                "dependency_index": dependency.model_dump(mode="json"),
            }
        ),
    )


def compile_wiki_incremental_v1(
    *,
    previous_request: WikiCompilationRequestV1,
    previous_result: WikiCompilationResultV1,
    request: WikiCompilationRequestV1,
    changed_candidate_ids: tuple[str, ...],
) -> WikiIncrementalCompilationV1:
    """Rebuild an exact generation and identify content-addressed records safe to reuse.

    The dependency closure is derived from both generations.  The final result is also
    compared to a complete deterministic compilation, so incremental materialization
    can never silently diverge from a clean rebuild.
    """

    if previous_result.request_sha256 != previous_request.content_sha256:
        raise WikiCompilerError("previous compiler request does not bind the previous result")
    previous_candidates = {item.candidate_id: item for item in previous_request.candidates}
    next_candidates = {item.candidate_id: item for item in request.candidates}
    actual_changed = tuple(
        sorted(
            candidate_id
            for candidate_id in set(previous_candidates) | set(next_candidates)
            if previous_candidates.get(candidate_id) != next_candidates.get(candidate_id)
        )
    )
    if changed_candidate_ids != actual_changed:
        raise WikiCompilerError("declared incremental changes differ from candidate authority")
    rebuilt = compile_wiki_v1(request, previous_error_book=previous_result.error_book)

    previous_records = {
        (
            item.scope.visibility_partition,
            item.logical_path,
            (
                WikiRecordKindV1.DIRECTORY.value
                if isinstance(item, WikiDirectoryV1)
                else WikiRecordKindV1.PAGE_FRAGMENT.value
            ),
        ): item
        for item in previous_result.records
    }
    next_records = {
        (
            item.scope.visibility_partition,
            item.logical_path,
            (
                WikiRecordKindV1.DIRECTORY.value
                if isinstance(item, WikiDirectoryV1)
                else WikiRecordKindV1.PAGE_FRAGMENT.value
            ),
        ): item
        for item in rebuilt.records
    }
    previous_source_pages = dict(previous_result.dependency_index.source_to_pages)
    next_source_pages = dict(rebuilt.dependency_index.source_to_pages)
    affected_paths: set[str] = set()
    for candidate_id in changed_candidate_ids:
        for candidate, source_pages in (
            (previous_candidates.get(candidate_id), previous_source_pages),
            (next_candidates.get(candidate_id), next_source_pages),
        ):
            if candidate is None:
                continue
            source_ref_id = (
                "wiki-source-ref-" + canonical_sha256_v1(candidate.model_dump(mode="json"))[7:31]
            )
            affected_paths.update(source_pages.get(source_ref_id, ()))

    reused: list[tuple[str, str]] = []
    changed: list[tuple[str, str]] = []
    for key, record in next_records.items():
        previous = previous_records.get(key)
        public_key = (key[1], key[2])
        if previous is not None and previous.content_sha256 == record.content_sha256:
            reused.append(public_key)
        else:
            changed.append(public_key)
            if key[2] == WikiRecordKindV1.PAGE_FRAGMENT.value:
                affected_paths.add(key[1])
    removed = [(key[1], key[2]) for key in previous_records.keys() - next_records.keys()]
    incremental_payload = {
        "previous_result_sha256": previous_result.content_sha256,
        "result": rebuilt,
        "changed_candidate_ids": changed_candidate_ids,
        "affected_page_paths": tuple(sorted(affected_paths)),
        "reused_records": tuple(sorted(reused)),
        "rebuilt_records": tuple(sorted(changed)),
        "removed_records": tuple(sorted(removed)),
        "full_rebuild_equivalent": True,
    }
    return WikiIncrementalCompilationV1(
        **incremental_payload,
        content_sha256=canonical_sha256_v1(
            {
                **incremental_payload,
                "result": rebuilt.model_dump(mode="json"),
            }
        ),
    )
