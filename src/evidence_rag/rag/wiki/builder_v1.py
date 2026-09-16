"""Reviewed offline Wiki patches and before/after marginal-utility evaluation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .compiler_v1 import (
    WIKI_COMPILER_AUTHORITY_SHA256,
    WikiCompilationResultV1,
    WikiErrorBookEntryV1,
    WikiErrorStatusV1,
)
from .contracts_v1 import (
    WikiDirectoryV1,
    WikiGenerationManifestV1,
    WikiGenerationStatusV1,
    WikiLinkStatusV1,
    WikiPageFragmentV1,
    WikiRecordIndexV1,
    WikiRecordKindV1,
    build_wiki_directory_v1,
    build_wiki_generation_manifest_v1,
    canonical_sha256_v1,
)
from .paths_v1 import WIKI_INTENT_DIRECTORIES, wiki_record_physical_key_v1

WIKI_BUILDER_VERSION = "wiki-offline-reviewed-builder-v1"
WIKI_PATCH_VERSION = "wiki-builder-patch-contract-v1"
WIKI_PATCH_EVALUATOR_VERSION = "wiki-builder-marginal-utility-v1"
WIKI_BUILDER_AUTHORITY_SHA256 = canonical_sha256_v1(
    {
        "base_compiler_authority": WIKI_COMPILER_AUTHORITY_SHA256,
        "builder_version": WIKI_BUILDER_VERSION,
        "rules": (
            "review-before-apply",
            "immutable-base-generation",
            "before-digest-exact",
            "no-dangling-active-links",
            "affected-query-improvement",
            "guard-query-non-regression",
            "acl-and-unsupported-zero",
        ),
    }
)


class WikiBuilderError(ValueError):
    """Raised when an offline patch or evaluation fails closed."""


class WikiPatchActionV1(StrEnum):
    UPSERT_PAGE = "upsert_page"
    DELETE_PAGE = "delete_page"


class WikiPatchOriginV1(StrEnum):
    HUMAN_REVIEWED = "human_reviewed"
    AGENT_REVIEWED = "agent_reviewed"
    AGENT_UNREVIEWED = "agent_unreviewed"


class WikiBuilderDecisionStatusV1(StrEnum):
    PROMOTE_TO_STAGING = "promote_to_staging"
    HOLD = "hold"
    REJECT = "reject"


class _FrozenBuilder(BaseModel):
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


class WikiPatchOperationV1(_FrozenBuilder):
    operation_id: str
    action: WikiPatchActionV1
    visibility_partition: str
    logical_path: str
    before_sha256: str | None
    after_page: WikiPageFragmentV1 | None
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiPatchOperationV1:
        if self.action is WikiPatchActionV1.UPSERT_PAGE:
            if self.after_page is None:
                raise WikiBuilderError("upsert patch operation requires an after page")
            if (
                self.after_page.logical_path != self.logical_path
                or self.after_page.scope.visibility_partition != self.visibility_partition
            ):
                raise WikiBuilderError("upsert page does not match patch target identity")
        elif self.after_page is not None or self.before_sha256 is None:
            raise WikiBuilderError("delete patch operation requires only a before digest")
        expected_id = (
            "wiki-operation-"
            + canonical_sha256_v1(
                {
                    "action": self.action.value,
                    "after": self.after_page.content_sha256 if self.after_page else None,
                    "before": self.before_sha256,
                    "path": self.logical_path,
                    "visibility": self.visibility_partition,
                }
            )[7:31]
        )
        if self.operation_id != expected_id:
            raise WikiBuilderError("patch operation identity mismatch")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiBuilderError("patch operation digest mismatch")
        return self


class WikiBuilderPatchV1(_FrozenBuilder):
    patch_id: str
    base_manifest_sha256: str
    operations: tuple[WikiPatchOperationV1, ...] = Field(min_length=1, max_length=256)
    affected_query_ids: tuple[str, ...] = Field(min_length=1, max_length=1_000)
    guard_query_ids: tuple[str, ...] = Field(min_length=1, max_length=5_000)
    trigger_error_entry_ids: tuple[str, ...] = ()
    origin: WikiPatchOriginV1
    reviewed: bool
    reviewer_authority_sha256: str | None
    patch_version: Literal[WIKI_PATCH_VERSION] = WIKI_PATCH_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiBuilderPatchV1:
        operation_keys = tuple(
            (item.visibility_partition, item.logical_path, item.action.value)
            for item in self.operations
        )
        if operation_keys != tuple(sorted(set(operation_keys))):
            raise WikiBuilderError("patch operations must be target-sorted and unique")
        targets = tuple((item.visibility_partition, item.logical_path) for item in self.operations)
        if len(targets) != len(set(targets)):
            raise WikiBuilderError("patch cannot contain multiple operations for one target")
        for values, label in (
            (self.affected_query_ids, "affected queries"),
            (self.guard_query_ids, "guard queries"),
            (self.trigger_error_entry_ids, "Error Book entries"),
        ):
            if values != tuple(sorted(set(values))):
                raise WikiBuilderError(f"patch {label} must be sorted and unique")
        if set(self.affected_query_ids) & set(self.guard_query_ids):
            raise WikiBuilderError("affected and guard queries must be disjoint")
        if self.reviewed != (self.origin is not WikiPatchOriginV1.AGENT_UNREVIEWED):
            raise WikiBuilderError("patch review state does not match its origin")
        if self.reviewed != (self.reviewer_authority_sha256 is not None):
            raise WikiBuilderError("reviewed patch must bind reviewer authority")
        expected_id = (
            "wiki-patch-"
            + canonical_sha256_v1(
                {
                    "base": self.base_manifest_sha256,
                    "operations": [item.content_sha256 for item in self.operations],
                    "version": self.patch_version,
                }
            )[7:31]
        )
        if self.patch_id != expected_id:
            raise WikiBuilderError("patch identity mismatch")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiBuilderError("patch digest mismatch")
        return self


class WikiPatchedGenerationV1(_FrozenBuilder):
    base_manifest_sha256: str
    patch_sha256: str
    manifest: WikiGenerationManifestV1
    records: tuple[WikiDirectoryV1 | WikiPageFragmentV1, ...]
    reused_page_count: int = Field(ge=0)
    changed_page_count: int = Field(ge=0)
    deleted_page_count: int = Field(ge=0)
    builder_authority_sha256: Literal[WIKI_BUILDER_AUTHORITY_SHA256] = WIKI_BUILDER_AUTHORITY_SHA256
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiPatchedGenerationV1:
        pages = tuple(item for item in self.records if isinstance(item, WikiPageFragmentV1))
        if len(pages) != self.reused_page_count + self.changed_page_count:
            raise WikiBuilderError("patched generation page accounting mismatch")
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
            raise WikiBuilderError("patched records differ from manifest authority")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiBuilderError("patched generation digest mismatch")
        return self


class WikiBuilderGenerationBaseV1(_FrozenBuilder):
    """The complete immutable authority needed for another reviewed patch.

    Compiler results and Builder results intentionally have different product
    contracts.  This normalized base prevents the first Builder publication from
    becoming a terminal generation while preserving the root compilation, raw
    candidate set, Error Book and the ordered patch lineage.
    """

    manifest: WikiGenerationManifestV1
    records: tuple[WikiDirectoryV1 | WikiPageFragmentV1, ...]
    error_book: tuple[WikiErrorBookEntryV1, ...]
    source_candidate_sha256: tuple[str, ...]
    root_compilation_sha256: str
    parent_manifest_sha256: str | None
    applied_patch_sha256: tuple[str, ...]
    lineage_depth: int = Field(ge=0, le=1_000)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiBuilderGenerationBaseV1:
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
            raise WikiBuilderError("Builder base records differ from manifest authority")
        if tuple(item.entry_id for item in self.error_book) != tuple(
            sorted(set(item.entry_id for item in self.error_book))
        ):
            raise WikiBuilderError("Builder base Error Book membership is invalid")
        if self.source_candidate_sha256 != tuple(sorted(set(self.source_candidate_sha256))):
            raise WikiBuilderError("Builder base source authority is not canonical")
        if self.applied_patch_sha256 != tuple(dict.fromkeys(self.applied_patch_sha256)):
            raise WikiBuilderError("Builder patch lineage contains duplicates")
        if self.lineage_depth != len(self.applied_patch_sha256):
            raise WikiBuilderError("Builder lineage depth does not match its patch chain")
        if self.lineage_depth == 0:
            if self.parent_manifest_sha256 is not None:
                raise WikiBuilderError("compiler base cannot claim a Builder parent")
            if self.manifest.compiler_authority_sha256 != WIKI_COMPILER_AUTHORITY_SHA256:
                raise WikiBuilderError("root Builder base is not a compiler generation")
        elif (
            self.parent_manifest_sha256 is None
            or self.manifest.compiler_authority_sha256 != WIKI_BUILDER_AUTHORITY_SHA256
        ):
            raise WikiBuilderError("patched Builder base lacks its parent authority")
        for digest in (
            self.root_compilation_sha256,
            self.parent_manifest_sha256,
            *self.source_candidate_sha256,
            *self.applied_patch_sha256,
        ):
            if digest is not None and (not digest.startswith("sha256:") or len(digest) != 71):
                raise WikiBuilderError("Builder base contains a malformed authority digest")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiBuilderError("Builder generation base digest mismatch")
        return self


def build_wiki_builder_generation_base_v1(**payload: Any) -> WikiBuilderGenerationBaseV1:
    normalized = WikiBuilderGenerationBaseV1.model_construct(
        **payload, content_sha256="pending"
    ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
    return WikiBuilderGenerationBaseV1.model_validate(
        {**payload, "content_sha256": canonical_sha256_v1(normalized)}
    )


class WikiBuilderQueryOutcomeV1(_FrozenBuilder):
    query_id: str
    evidence_complete: bool
    obligation_numerator: int = Field(ge=0)
    obligation_denominator: int = Field(ge=1)
    raw_verify_numerator: int = Field(ge=0)
    raw_verify_denominator: int = Field(ge=1)
    acl_leakage_count: int = Field(ge=0)
    unsupported_claim_count: int = Field(ge=0)
    navigation_steps: int = Field(ge=0)
    token_count: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiBuilderQueryOutcomeV1:
        if self.obligation_numerator > self.obligation_denominator:
            raise WikiBuilderError("outcome obligation numerator is invalid")
        if self.raw_verify_numerator > self.raw_verify_denominator:
            raise WikiBuilderError("outcome raw verification numerator is invalid")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiBuilderError("query outcome digest mismatch")
        return self


class WikiBuilderTrialV1(_FrozenBuilder):
    patch_sha256: str
    initial_manifest_sha256: str
    affected_before: tuple[WikiBuilderQueryOutcomeV1, ...]
    affected_after: tuple[WikiBuilderQueryOutcomeV1, ...]
    guard_before: tuple[WikiBuilderQueryOutcomeV1, ...]
    guard_after: tuple[WikiBuilderQueryOutcomeV1, ...]
    evaluator_version: Literal[WIKI_PATCH_EVALUATOR_VERSION] = WIKI_PATCH_EVALUATOR_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiBuilderTrialV1:
        for before, after, label in (
            (self.affected_before, self.affected_after, "affected"),
            (self.guard_before, self.guard_after, "guard"),
        ):
            before_ids = tuple(item.query_id for item in before)
            after_ids = tuple(item.query_id for item in after)
            if before_ids != tuple(sorted(set(before_ids))) or before_ids != after_ids:
                raise WikiBuilderError(f"builder {label} trial membership mismatch")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiBuilderError("builder trial digest mismatch")
        return self


class WikiBuilderDecisionV1(_FrozenBuilder):
    patch_sha256: str
    trial_sha256: str
    status: WikiBuilderDecisionStatusV1
    affected_utility_delta: float
    improved_affected_queries: int = Field(ge=0)
    regressed_guard_queries: tuple[str, ...]
    hard_guard_failures: tuple[str, ...]
    production_authorized: Literal[False] = False
    evaluator_version: Literal[WIKI_PATCH_EVALUATOR_VERSION] = WIKI_PATCH_EVALUATOR_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiBuilderDecisionV1:
        if self.regressed_guard_queries != tuple(sorted(set(self.regressed_guard_queries))):
            raise WikiBuilderError("builder guard regressions must be sorted and unique")
        if self.hard_guard_failures != tuple(sorted(set(self.hard_guard_failures))):
            raise WikiBuilderError("builder hard failures must be sorted and unique")
        if self.status is WikiBuilderDecisionStatusV1.PROMOTE_TO_STAGING and (
            self.affected_utility_delta <= 0
            or self.improved_affected_queries == 0
            or self.regressed_guard_queries
            or self.hard_guard_failures
        ):
            raise WikiBuilderError("promotion decision violates Builder utility gates")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiBuilderError("builder decision digest mismatch")
        return self


def build_wiki_patch_operation_v1(**payload: Any) -> WikiPatchOperationV1:
    action = WikiPatchActionV1(payload["action"])
    operation_id = (
        "wiki-operation-"
        + canonical_sha256_v1(
            {
                "action": action.value,
                "after": payload.get("after_page").content_sha256
                if payload.get("after_page") is not None
                else None,
                "before": payload.get("before_sha256"),
                "path": payload["logical_path"],
                "visibility": payload["visibility_partition"],
            }
        )[7:31]
    )
    payload = {**payload, "action": action, "operation_id": operation_id}
    normalized = WikiPatchOperationV1.model_construct(
        **payload, content_sha256="pending"
    ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
    return WikiPatchOperationV1.model_validate(
        {**payload, "content_sha256": canonical_sha256_v1(normalized)}
    )


def build_wiki_builder_patch_v1(**payload: Any) -> WikiBuilderPatchV1:
    operation_digests = [item.content_sha256 for item in payload["operations"]]
    patch_version = payload.get("patch_version", WIKI_PATCH_VERSION)
    patch_id = (
        "wiki-patch-"
        + canonical_sha256_v1(
            {
                "base": payload["base_manifest_sha256"],
                "operations": operation_digests,
                "version": patch_version,
            }
        )[7:31]
    )
    complete = {**payload, "patch_id": patch_id, "patch_version": patch_version}
    normalized = WikiBuilderPatchV1.model_construct(
        **complete, content_sha256="pending"
    ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
    return WikiBuilderPatchV1.model_validate(
        {**complete, "content_sha256": canonical_sha256_v1(normalized)}
    )


def build_wiki_query_outcome_v1(**payload: Any) -> WikiBuilderQueryOutcomeV1:
    normalized = WikiBuilderQueryOutcomeV1.model_construct(
        **payload, content_sha256="pending"
    ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
    return WikiBuilderQueryOutcomeV1.model_validate(
        {**payload, "content_sha256": canonical_sha256_v1(normalized)}
    )


def build_wiki_builder_trial_v1(**payload: Any) -> WikiBuilderTrialV1:
    normalized = WikiBuilderTrialV1.model_construct(**payload, content_sha256="pending").model_dump(
        mode="json", exclude={"content_sha256"}, warnings=False
    )
    return WikiBuilderTrialV1.model_validate(
        {**payload, "content_sha256": canonical_sha256_v1(normalized)}
    )


def apply_reviewed_wiki_patch_v1(
    *,
    base: WikiCompilationResultV1 | WikiBuilderGenerationBaseV1,
    patch: WikiBuilderPatchV1,
    generation_id: str,
    created_at: str,
) -> WikiPatchedGenerationV1:
    if not patch.reviewed or patch.origin is WikiPatchOriginV1.AGENT_UNREVIEWED:
        raise WikiBuilderError("unreviewed Agent patch cannot modify a Wiki generation")
    if patch.base_manifest_sha256 != base.manifest.content_sha256:
        raise WikiBuilderError("patch base manifest authority mismatch")
    base_pages = {
        (item.scope.visibility_partition, item.logical_path): item
        for item in base.records
        if isinstance(item, WikiPageFragmentV1)
    }
    pages = dict(base_pages)
    deleted = 0
    changed = 0
    for operation in patch.operations:
        key = (operation.visibility_partition, operation.logical_path)
        existing = pages.get(key)
        if operation.before_sha256 is None:
            if existing is not None:
                raise WikiBuilderError("create patch target already exists")
        elif existing is None or existing.content_sha256 != operation.before_sha256:
            raise WikiBuilderError("patch before digest does not match the base page")
        if operation.action is WikiPatchActionV1.DELETE_PAGE:
            del pages[key]
            deleted += 1
        else:
            assert operation.after_page is not None
            if operation.after_page.scope.project_id != base.manifest.project_id:
                raise WikiBuilderError("patch page belongs to another project")
            manifest_sources = {item.source: item for item in base.manifest.source_generations}
            if any(
                manifest_sources.get(item.source) != item
                for item in operation.after_page.scope.source_generations
            ):
                raise WikiBuilderError("patch page mixes source generations")
            pages[key] = operation.after_page
            changed += 1
    for page in pages.values():
        for link in page.links:
            if (
                link.status is WikiLinkStatusV1.ACTIVE
                and (page.scope.visibility_partition, link.target_path) not in pages
            ):
                raise WikiBuilderError("patch leaves an active dangling Wiki link")
        for fact in page.facts:
            if (
                fact.object_path
                and (page.scope.visibility_partition, fact.object_path) not in pages
            ):
                raise WikiBuilderError("patch leaves a dangling Wiki fact object")

    base_directories = tuple(item for item in base.records if isinstance(item, WikiDirectoryV1))
    rebuilt_directories = []
    for directory in base_directories:
        partition_pages = tuple(
            page
            for (partition, _), page in pages.items()
            if partition == directory.scope.visibility_partition
        )
        if directory.logical_path == "/sources":
            page_paths = ()
        elif directory.logical_path.startswith("/sources/"):
            page_paths = tuple(
                sorted(
                    page.logical_path
                    for page in partition_pages
                    if page.logical_path.startswith(directory.logical_path + "/")
                )
            )
        else:
            page_paths = tuple(
                sorted(
                    page.logical_path
                    for page in partition_pages
                    if page.logical_path.startswith(directory.logical_path + "/")
                )
            )
        rebuilt_directories.append(
            build_wiki_directory_v1(
                directory_id=directory.directory_id,
                logical_path=directory.logical_path,
                scope=directory.scope,
                child_paths=directory.child_paths,
                page_paths=page_paths,
            )
        )
    records = tuple(
        sorted(
            (*rebuilt_directories, *pages.values()),
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
            logical_path=item.logical_path,
            record_kind=(
                WikiRecordKindV1.DIRECTORY
                if isinstance(item, WikiDirectoryV1)
                else WikiRecordKindV1.PAGE_FRAGMENT
            ),
            visibility_partition=item.scope.visibility_partition,
            record_sha256=item.content_sha256,
            physical_key=wiki_record_physical_key_v1(
                project_id=base.manifest.project_id,
                generation_id=generation_id,
                visibility_partition=item.scope.visibility_partition,
                logical_path=item.logical_path,
                record_kind=(
                    WikiRecordKindV1.DIRECTORY.value
                    if isinstance(item, WikiDirectoryV1)
                    else WikiRecordKindV1.PAGE_FRAGMENT.value
                ),
            ),
        )
        for item in records
    )
    manifest = build_wiki_generation_manifest_v1(
        snapshot_id="wiki-builder-snapshot-" + patch.content_sha256[7:31],
        project_id=base.manifest.project_id,
        generation_id=generation_id,
        status=WikiGenerationStatusV1.VERIFIED,
        source_generations=base.manifest.source_generations,
        visibility_partitions=base.manifest.visibility_partitions,
        records=indexes,
        root_paths=tuple(f"/{intent}" for intent in WIKI_INTENT_DIRECTORIES),
        compiler_authority_sha256=WIKI_BUILDER_AUTHORITY_SHA256,
        created_at=created_at,
    )
    changed_targets = {(item.visibility_partition, item.logical_path) for item in patch.operations}
    page_count = len(pages)
    result_payload = {
        "base_manifest_sha256": base.manifest.content_sha256,
        "patch_sha256": patch.content_sha256,
        "manifest": manifest,
        "records": records,
        "reused_page_count": page_count - changed,
        "changed_page_count": changed,
        "deleted_page_count": deleted,
        "builder_authority_sha256": WIKI_BUILDER_AUTHORITY_SHA256,
    }
    # This also proves every declared operation affected a real final target or deletion.
    final_targets = set(pages) | {
        (item.visibility_partition, item.logical_path)
        for item in patch.operations
        if item.action is WikiPatchActionV1.DELETE_PAGE
    }
    if not changed_targets.issubset(final_targets):
        raise WikiBuilderError("patch operation target accounting mismatch")
    return WikiPatchedGenerationV1(
        **result_payload,
        content_sha256=canonical_sha256_v1(
            {
                **result_payload,
                "manifest": manifest.model_dump(mode="json"),
                "records": [item.model_dump(mode="json") for item in records],
            }
        ),
    )


def _outcome_utility(outcome: WikiBuilderQueryOutcomeV1) -> float:
    evidence = 1.0 if outcome.evidence_complete else 0.0
    obligations = outcome.obligation_numerator / outcome.obligation_denominator
    raw = outcome.raw_verify_numerator / outcome.raw_verify_denominator
    cost = min(1.0, outcome.navigation_steps / 64.0) * 0.04
    cost += min(1.0, outcome.token_count / 32_000.0) * 0.03
    cost += min(1.0, outcome.latency_ms / 10_000.0) * 0.03
    return 0.5 * evidence + 0.25 * obligations + 0.25 * raw - cost


def evaluate_wiki_builder_patch_v1(
    patch: WikiBuilderPatchV1,
    trial: WikiBuilderTrialV1,
) -> WikiBuilderDecisionV1:
    if (
        trial.patch_sha256 != patch.content_sha256
        or trial.initial_manifest_sha256 != patch.base_manifest_sha256
    ):
        raise WikiBuilderError("Builder trial does not bind the patch and initial Wiki")
    if tuple(item.query_id for item in trial.affected_before) != patch.affected_query_ids:
        raise WikiBuilderError("Builder trial affected membership differs from patch")
    if tuple(item.query_id for item in trial.guard_before) != patch.guard_query_ids:
        raise WikiBuilderError("Builder trial guard membership differs from patch")
    affected_deltas = {
        before.query_id: _outcome_utility(after) - _outcome_utility(before)
        for before, after in zip(trial.affected_before, trial.affected_after, strict=True)
    }
    guard_regressions = tuple(
        sorted(
            before.query_id
            for before, after in zip(trial.guard_before, trial.guard_after, strict=True)
            if _outcome_utility(after) + 1e-12 < _outcome_utility(before)
            or (before.evidence_complete and not after.evidence_complete)
        )
    )
    hard_failures = []
    for group_name, outcomes in (
        ("affected", trial.affected_after),
        ("guard", trial.guard_after),
    ):
        for outcome in outcomes:
            if outcome.acl_leakage_count:
                hard_failures.append(f"{group_name}:{outcome.query_id}:acl_leakage")
            if outcome.unsupported_claim_count:
                hard_failures.append(f"{group_name}:{outcome.query_id}:unsupported_claim")
    total_delta = sum(affected_deltas.values()) / len(affected_deltas)
    improved = sum(value > 1e-12 for value in affected_deltas.values())
    if hard_failures or guard_regressions:
        status = WikiBuilderDecisionStatusV1.REJECT
    elif total_delta > 1e-12 and improved:
        status = WikiBuilderDecisionStatusV1.PROMOTE_TO_STAGING
    else:
        status = WikiBuilderDecisionStatusV1.HOLD
    payload = {
        "patch_sha256": patch.content_sha256,
        "trial_sha256": trial.content_sha256,
        "status": status,
        "affected_utility_delta": total_delta,
        "improved_affected_queries": improved,
        "regressed_guard_queries": guard_regressions,
        "hard_guard_failures": tuple(sorted(hard_failures)),
        "production_authorized": False,
        "evaluator_version": WIKI_PATCH_EVALUATOR_VERSION,
    }
    return WikiBuilderDecisionV1(
        **payload,
        content_sha256=canonical_sha256_v1({**payload, "status": status.value}),
    )


def resolve_wiki_error_book_v1(
    entries: tuple[WikiErrorBookEntryV1, ...],
    *,
    resolved_entry_ids: tuple[str, ...],
    promoted_decision: WikiBuilderDecisionV1,
    generation_id: str,
) -> tuple[WikiErrorBookEntryV1, ...]:
    if promoted_decision.status is not WikiBuilderDecisionStatusV1.PROMOTE_TO_STAGING:
        raise WikiBuilderError("Error Book entries require a promoted, guard-clean patch")
    if resolved_entry_ids != tuple(sorted(set(resolved_entry_ids))):
        raise WikiBuilderError("resolved Error Book membership must be sorted and unique")
    known = {item.entry_id for item in entries}
    if not set(resolved_entry_ids).issubset(known):
        raise WikiBuilderError("cannot resolve an unknown Error Book entry")
    result = []
    for entry in entries:
        if entry.entry_id not in resolved_entry_ids:
            result.append(entry)
            continue
        payload = entry.model_dump(mode="python", exclude={"content_sha256"})
        payload.update(
            {
                "last_seen_generation": generation_id,
                "status": WikiErrorStatusV1.RESOLVED,
            }
        )
        normalized = {
            **payload,
            "source": entry.source.value,
            "code": entry.code.value,
            "status": WikiErrorStatusV1.RESOLVED.value,
        }
        result.append(
            WikiErrorBookEntryV1(
                **payload,
                content_sha256=canonical_sha256_v1(normalized),
            )
        )
    return tuple(sorted(result, key=lambda item: item.entry_id))
