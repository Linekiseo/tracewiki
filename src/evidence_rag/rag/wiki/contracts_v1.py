"""Frozen, content-addressed contracts for the agent-native Wiki."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..global_governance_v2 import inspect_untrusted_content_v2
from ..multisource_foundation_v2 import MULTISOURCE_DOMAINS
from .paths_v1 import (
    WIKI_INTENT_DIRECTORIES,
    WIKI_PATH_VERSION,
    validate_wiki_logical_path_v1,
    wiki_record_physical_key_v1,
)

WIKI_CONTRACT_VERSION = "agent-native-wiki-contract-v1"
WIKI_COMPILER_VERSION = "evidence-compiled-wiki-v1"
WIKI_SNAPSHOT_VERSION = "wiki-generation-snapshot-v1"
WIKI_VISIBILITY_VERSION = "wiki-acl-visibility-partition-v1"

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=240)]
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=16_000)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=1_000)]

_PORTABLE_ID_RE = re.compile(r"[A-Za-z0-9._:@+-]+")
_PREDICATE_RE = re.compile(r"[a-z][a-z0-9_.-]{0,127}")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_VISIBILITY_RE = re.compile(r"vis-[0-9a-f]{24}")
_GENERATION_RE = re.compile(r"(?:sha256:[0-9a-f]{64}|[A-Za-z0-9][A-Za-z0-9._:@+-]{0,239})")
_LOCATOR_RE = re.compile(r"(?:code|codex|experiment|notebook|document|workspace)://\S+")
_SOURCE_ORDER = {source: index for index, source in enumerate(MULTISOURCE_DOMAINS)}


def canonical_json_bytes_v1(value: object) -> bytes:
    return json.dumps(
        value,
        default=lambda item: (
            item.model_dump(mode="json") if isinstance(item, BaseModel) else str(item)
        ),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256_v1(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes_v1(value)).hexdigest()


def _validate_identifier(value: str, *, label: str) -> None:
    if not _PORTABLE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} is not a portable identifier")


def _validate_sha256(value: str, *, label: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} is not a canonical SHA-256 digest")


def _validate_time(value: str | None, *, label: str) -> None:
    if value is None:
        return
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")


def _validate_safe_text(value: str, *, acl_ref: str, label: str) -> None:
    blocked, reason = inspect_untrusted_content_v2(
        value,
        requester_acl_refs=(acl_ref,),
        evidence_acl_ref=acl_ref,
    )
    if blocked:
        raise ValueError(f"{label} contains unsafe content: {reason}")


def visibility_partition_v1(acl_refs: tuple[str, ...]) -> str:
    if not acl_refs or acl_refs != tuple(sorted(set(acl_refs))):
        raise ValueError("Wiki ACL refs must be non-empty, sorted, and unique")
    for acl_ref in acl_refs:
        _validate_identifier(acl_ref, label="acl_ref")
    return (
        "vis-"
        + canonical_sha256_v1({"acl_refs": acl_refs, "version": WIKI_VISIBILITY_VERSION})[7:31]
    )


class _FrozenWiki(BaseModel):
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


class WikiSourceDomainV1(StrEnum):
    CODE = "code"
    CODEX = "codex"
    EXPERIMENT = "experiment"
    NOTEBOOK = "notebook"
    DOCUMENT = "document"
    WORKSPACE = "workspace"


class WikiRecordKindV1(StrEnum):
    DIRECTORY = "directory"
    PAGE_FRAGMENT = "page_fragment"


class WikiPageTypeV1(StrEnum):
    ARCHITECTURE = "architecture"
    CAPABILITY = "capability"
    CHANGE = "change"
    COMPONENT = "component"
    DECISION = "decision"
    EXPERIMENT = "experiment"
    FINDING = "finding"
    ISSUE = "issue"
    PROCEDURE = "procedure"
    REQUIREMENT = "requirement"
    SOURCE = "source"


class WikiFactAuthorityV1(StrEnum):
    RAW_OBSERVED = "raw_observed"
    DETERMINISTIC_DERIVED = "deterministic_derived"
    REVIEWED_SYNTHESIS = "reviewed_synthesis"
    UNREVIEWED_SUGGESTION = "unreviewed_suggestion"


class WikiFactStatusV1(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    CONTRADICTED = "contradicted"
    PROPOSED = "proposed"


class WikiLinkStatusV1(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    PROPOSED = "proposed"
    REJECTED = "rejected"


class WikiGenerationStatusV1(StrEnum):
    STAGED = "staged"
    VERIFIED = "verified"
    PUBLISHED = "published"
    RETIRED = "retired"


class SourceGenerationV1(_FrozenWiki):
    source: WikiSourceDomainV1
    generation_id: Identifier
    watermark: Identifier
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> SourceGenerationV1:
        if not _GENERATION_RE.fullmatch(self.generation_id):
            raise ValueError("source generation is not canonical")
        _validate_identifier(self.watermark, label="source watermark")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("source generation digest mismatch")
        return self


class WikiScopeV1(_FrozenWiki):
    project_id: Identifier
    visibility_partition: str
    acl_refs: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    source_generations: tuple[SourceGenerationV1, ...] = Field(min_length=1, max_length=6)
    as_of: str | None = None

    @model_validator(mode="after")
    def _scope_identity(self) -> WikiScopeV1:
        _validate_identifier(self.project_id, label="project_id")
        if self.acl_refs != tuple(sorted(set(self.acl_refs))):
            raise ValueError("scope ACL refs must be sorted and unique")
        if self.visibility_partition != visibility_partition_v1(self.acl_refs):
            raise ValueError("visibility partition does not bind the ACL set")
        sources = tuple(item.source.value for item in self.source_generations)
        if len(set(sources)) != len(sources) or tuple(
            _SOURCE_ORDER[item] for item in sources
        ) != tuple(sorted(_SOURCE_ORDER[item] for item in sources)):
            raise ValueError("source generations must follow governed source order and be unique")
        _validate_time(self.as_of, label="scope as_of")
        return self

    def permits(self, evidence_acl_refs: tuple[str, ...]) -> bool:
        return bool(evidence_acl_refs) and set(evidence_acl_refs).issubset(self.acl_refs)


class WikiSourceRefV1(_FrozenWiki):
    source_ref_id: Identifier
    source: WikiSourceDomainV1
    entity_type: Identifier
    entity_id: Identifier
    locator: str = Field(min_length=1, max_length=2_000)
    generation_id: Identifier
    watermark: Identifier
    acl_refs: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    observed_at: str
    raw_content_sha256: str
    content_sha256: str

    @model_validator(mode="after")
    def _source_identity(self) -> WikiSourceRefV1:
        for value, label in (
            (self.source_ref_id, "source_ref_id"),
            (self.entity_type, "entity_type"),
            (self.entity_id, "entity_id"),
            (self.watermark, "watermark"),
        ):
            _validate_identifier(value, label=label)
        if not _GENERATION_RE.fullmatch(self.generation_id):
            raise ValueError("source ref generation is not canonical")
        if self.acl_refs != tuple(sorted(set(self.acl_refs))):
            raise ValueError("source ref ACL refs must be sorted and unique")
        if not _LOCATOR_RE.fullmatch(self.locator) or not self.locator.startswith(
            f"{self.source.value}://"
        ):
            raise ValueError("source locator is not a portable typed locator")
        _validate_safe_text(self.locator, acl_ref=self.acl_refs[0], label="source locator")
        _validate_time(self.observed_at, label="source observed_at")
        _validate_sha256(self.raw_content_sha256, label="raw content digest")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("source ref digest mismatch")
        return self


class WikiQualifierV1(_FrozenWiki):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def _canonical(self) -> WikiQualifierV1:
        if not _PREDICATE_RE.fullmatch(self.key):
            raise ValueError("qualifier key is not canonical")
        _validate_safe_text(self.value, acl_ref="wiki-internal", label="qualifier")
        return self


class WikiFactV1(_FrozenWiki):
    fact_id: Identifier
    subject_path: str
    predicate: str
    object_text: BoundedText | None = None
    object_path: str | None = None
    qualifiers: tuple[WikiQualifierV1, ...] = ()
    source_ref_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    authority: WikiFactAuthorityV1
    status: WikiFactStatusV1
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: str | None = None
    valid_to: str | None = None
    content_sha256: str

    @model_validator(mode="after")
    def _fact_identity(self) -> WikiFactV1:
        _validate_identifier(self.fact_id, label="fact_id")
        validate_wiki_logical_path_v1(self.subject_path, allow_root=False)
        if not _PREDICATE_RE.fullmatch(self.predicate):
            raise ValueError("fact predicate is not canonical")
        if (self.object_text is None) == (self.object_path is None):
            raise ValueError("fact requires exactly one text or entity-path object")
        if self.object_path is not None:
            validate_wiki_logical_path_v1(self.object_path, allow_root=False)
        if self.object_text is not None:
            _validate_safe_text(self.object_text, acl_ref="wiki-internal", label="fact object")
        if self.qualifiers != tuple(
            sorted(self.qualifiers, key=lambda item: (item.key, item.value))
        ):
            raise ValueError("fact qualifiers must be canonically sorted")
        if self.source_ref_ids != tuple(sorted(set(self.source_ref_ids))):
            raise ValueError("fact source refs must be sorted and unique")
        _validate_time(self.valid_from, label="fact valid_from")
        _validate_time(self.valid_to, label="fact valid_to")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("fact validity interval is invalid")
        if (
            self.authority is WikiFactAuthorityV1.UNREVIEWED_SUGGESTION
            and self.status is not WikiFactStatusV1.PROPOSED
        ):
            raise ValueError("unreviewed facts cannot become active authority")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("fact digest mismatch")
        return self


class WikiLinkV1(_FrozenWiki):
    link_id: Identifier
    source_path: str
    target_path: str
    relation: str
    inverse_relation: str
    source_ref_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    status: WikiLinkStatusV1
    confidence: float = Field(ge=0.0, le=1.0)
    content_sha256: str

    @model_validator(mode="after")
    def _link_identity(self) -> WikiLinkV1:
        _validate_identifier(self.link_id, label="link_id")
        validate_wiki_logical_path_v1(self.source_path, allow_root=False)
        validate_wiki_logical_path_v1(self.target_path, allow_root=False)
        if self.source_path == self.target_path:
            raise ValueError("Wiki link cannot self-reference")
        if not _PREDICATE_RE.fullmatch(self.relation) or not _PREDICATE_RE.fullmatch(
            self.inverse_relation
        ):
            raise ValueError("Wiki link relation is not canonical")
        if self.source_ref_ids != tuple(sorted(set(self.source_ref_ids))):
            raise ValueError("link source refs must be sorted and unique")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("link digest mismatch")
        return self


class WikiPageFragmentV1(_FrozenWiki):
    fragment_id: Identifier
    logical_path: str
    page_type: WikiPageTypeV1
    title: ShortText
    summary: BoundedText
    aliases: tuple[ShortText, ...] = ()
    tags: tuple[Identifier, ...] = ()
    scope: WikiScopeV1
    source_refs: tuple[WikiSourceRefV1, ...] = Field(min_length=1, max_length=512)
    facts: tuple[WikiFactV1, ...] = ()
    links: tuple[WikiLinkV1, ...] = ()
    evidence_roles: tuple[Identifier, ...] = ()
    compiler_version: Literal[WIKI_COMPILER_VERSION] = WIKI_COMPILER_VERSION
    contract_version: Literal[WIKI_CONTRACT_VERSION] = WIKI_CONTRACT_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _fragment_identity(self) -> WikiPageFragmentV1:
        _validate_identifier(self.fragment_id, label="fragment_id")
        validate_wiki_logical_path_v1(self.logical_path, allow_root=False)
        if self.logical_path.split("/", 2)[1] != self.page_type.value + "s" and not (
            self.page_type is WikiPageTypeV1.ARCHITECTURE
            and self.logical_path.startswith("/architecture/")
        ):
            expected_intent = {
                WikiPageTypeV1.CAPABILITY: "capabilities",
                WikiPageTypeV1.CHANGE: "changes",
                WikiPageTypeV1.COMPONENT: "components",
                WikiPageTypeV1.DECISION: "decisions",
                WikiPageTypeV1.EXPERIMENT: "experiments",
                WikiPageTypeV1.FINDING: "findings",
                WikiPageTypeV1.ISSUE: "issues",
                WikiPageTypeV1.PROCEDURE: "procedures",
                WikiPageTypeV1.REQUIREMENT: "requirements",
                WikiPageTypeV1.SOURCE: "sources",
            }.get(self.page_type)
            if expected_intent is not None and not self.logical_path.startswith(
                f"/{expected_intent}/"
            ):
                raise ValueError("page type does not match its intent directory")
        _validate_safe_text(self.title, acl_ref=self.scope.acl_refs[0], label="page title")
        _validate_safe_text(self.summary, acl_ref=self.scope.acl_refs[0], label="page summary")
        if self.aliases != tuple(sorted(set(self.aliases))):
            raise ValueError("page aliases must be sorted and unique")
        if self.tags != tuple(sorted(set(self.tags))):
            raise ValueError("page tags must be sorted and unique")
        for alias in self.aliases:
            _validate_safe_text(alias, acl_ref=self.scope.acl_refs[0], label="page alias")
        for tag in self.tags:
            _validate_identifier(tag, label="page tag")
        ref_ids = tuple(item.source_ref_id for item in self.source_refs)
        if ref_ids != tuple(sorted(set(ref_ids))):
            raise ValueError("page source refs must be source-ref sorted and unique")
        generations = {(item.source, item.generation_id) for item in self.scope.source_generations}
        for source_ref in self.source_refs:
            if not self.scope.permits(source_ref.acl_refs):
                raise ValueError("page fragment contains evidence outside its visibility partition")
            if (source_ref.source, source_ref.generation_id) not in generations:
                raise ValueError("page source ref is outside the snapshot generation set")
        allowed_ref_ids = set(ref_ids)
        fact_ids = tuple(item.fact_id for item in self.facts)
        link_ids = tuple(item.link_id for item in self.links)
        if fact_ids != tuple(sorted(set(fact_ids))) or link_ids != tuple(sorted(set(link_ids))):
            raise ValueError("page facts and links must be ID-sorted and unique")
        for fact in self.facts:
            if fact.subject_path != self.logical_path or not set(fact.source_ref_ids).issubset(
                allowed_ref_ids
            ):
                raise ValueError("page fact is not grounded in this fragment")
        for link in self.links:
            if link.source_path != self.logical_path or not set(link.source_ref_ids).issubset(
                allowed_ref_ids
            ):
                raise ValueError("page link is not grounded in this fragment")
        if self.evidence_roles != tuple(sorted(set(self.evidence_roles))):
            raise ValueError("evidence roles must be sorted and unique")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("page fragment digest mismatch")
        return self


class WikiDirectoryV1(_FrozenWiki):
    directory_id: Identifier
    logical_path: str
    scope: WikiScopeV1
    child_paths: tuple[str, ...]
    page_paths: tuple[str, ...]
    path_version: Literal[WIKI_PATH_VERSION] = WIKI_PATH_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _directory_identity(self) -> WikiDirectoryV1:
        _validate_identifier(self.directory_id, label="directory_id")
        validate_wiki_logical_path_v1(self.logical_path)
        for values, label in ((self.child_paths, "children"), (self.page_paths, "pages")):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"directory {label} must be path-sorted and unique")
            for path in values:
                validate_wiki_logical_path_v1(path, allow_root=False)
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("directory digest mismatch")
        return self


class WikiRecordIndexV1(_FrozenWiki):
    logical_path: str
    record_kind: WikiRecordKindV1
    visibility_partition: str
    record_sha256: str
    physical_key: str

    @model_validator(mode="after")
    def _record_identity(self) -> WikiRecordIndexV1:
        validate_wiki_logical_path_v1(self.logical_path)
        if not _VISIBILITY_RE.fullmatch(self.visibility_partition):
            raise ValueError("record visibility partition is malformed")
        _validate_sha256(self.record_sha256, label="record digest")
        if not re.fullmatch(r"wiki-record:[0-9a-f]{64}", self.physical_key):
            raise ValueError("record physical key is malformed")
        return self


class WikiGenerationManifestV1(_FrozenWiki):
    snapshot_id: Identifier
    project_id: Identifier
    generation_id: Identifier
    status: WikiGenerationStatusV1
    source_generations: tuple[SourceGenerationV1, ...] = Field(min_length=1, max_length=6)
    visibility_partitions: tuple[str, ...] = Field(min_length=1, max_length=256)
    records: tuple[WikiRecordIndexV1, ...] = Field(min_length=len(WIKI_INTENT_DIRECTORIES))
    root_paths: tuple[str, ...]
    compiler_authority_sha256: str
    created_at: str
    snapshot_version: Literal[WIKI_SNAPSHOT_VERSION] = WIKI_SNAPSHOT_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _manifest_identity(self) -> WikiGenerationManifestV1:
        _validate_identifier(self.snapshot_id, label="snapshot_id")
        _validate_identifier(self.project_id, label="project_id")
        if not _GENERATION_RE.fullmatch(self.generation_id):
            raise ValueError("Wiki generation ID is not canonical")
        sources = tuple(item.source.value for item in self.source_generations)
        if len(set(sources)) != len(sources) or tuple(
            _SOURCE_ORDER[item] for item in sources
        ) != tuple(sorted(_SOURCE_ORDER[item] for item in sources)):
            raise ValueError(
                "manifest source generations must follow governed source order and be unique"
            )
        if self.visibility_partitions != tuple(sorted(set(self.visibility_partitions))):
            raise ValueError("manifest visibility partitions must be sorted and unique")
        if any(not _VISIBILITY_RE.fullmatch(item) for item in self.visibility_partitions):
            raise ValueError("manifest visibility partition is malformed")
        record_keys = tuple(
            (item.visibility_partition, item.logical_path, item.record_kind.value)
            for item in self.records
        )
        if record_keys != tuple(sorted(set(record_keys))):
            raise ValueError("manifest record index must be sorted and unique")
        if any(
            item.visibility_partition not in self.visibility_partitions for item in self.records
        ):
            raise ValueError("manifest record references an unknown visibility partition")
        for item in self.records:
            expected_key = wiki_record_physical_key_v1(
                project_id=self.project_id,
                generation_id=self.generation_id,
                visibility_partition=item.visibility_partition,
                logical_path=item.logical_path,
                record_kind=item.record_kind.value,
            )
            if item.physical_key != expected_key:
                raise ValueError("manifest record physical key identity mismatch")
        if self.root_paths != tuple(f"/{intent}" for intent in WIKI_INTENT_DIRECTORIES):
            raise ValueError("manifest intent roots are incomplete or reordered")
        _validate_sha256(self.compiler_authority_sha256, label="compiler authority")
        _validate_time(self.created_at, label="manifest created_at")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("generation manifest digest mismatch")
        return self


def _build_hashed(model: type[_FrozenWiki], payload: dict[str, Any]) -> _FrozenWiki:
    normalized = model.model_construct(**payload, content_sha256="pending").model_dump(
        mode="json", exclude={"content_sha256"}, warnings=False
    )
    return model.model_validate({**payload, "content_sha256": canonical_sha256_v1(normalized)})


def build_source_generation_v1(**payload: Any) -> SourceGenerationV1:
    return SourceGenerationV1.model_validate(_build_hashed(SourceGenerationV1, payload))


def build_wiki_source_ref_v1(**payload: Any) -> WikiSourceRefV1:
    return WikiSourceRefV1.model_validate(_build_hashed(WikiSourceRefV1, payload))


def build_wiki_fact_v1(**payload: Any) -> WikiFactV1:
    return WikiFactV1.model_validate(_build_hashed(WikiFactV1, payload))


def build_wiki_link_v1(**payload: Any) -> WikiLinkV1:
    return WikiLinkV1.model_validate(_build_hashed(WikiLinkV1, payload))


def build_wiki_page_fragment_v1(**payload: Any) -> WikiPageFragmentV1:
    return WikiPageFragmentV1.model_validate(_build_hashed(WikiPageFragmentV1, payload))


def build_wiki_directory_v1(**payload: Any) -> WikiDirectoryV1:
    return WikiDirectoryV1.model_validate(_build_hashed(WikiDirectoryV1, payload))


def build_wiki_generation_manifest_v1(**payload: Any) -> WikiGenerationManifestV1:
    return WikiGenerationManifestV1.model_validate(_build_hashed(WikiGenerationManifestV1, payload))


def assert_supported_sources_v1() -> None:
    if tuple(item.value for item in WikiSourceDomainV1) != MULTISOURCE_DOMAINS:
        raise RuntimeError("Wiki source vocabulary diverged from the governed six-source registry")


assert_supported_sources_v1()
