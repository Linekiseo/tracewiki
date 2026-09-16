"""Typed Code graph ontology and bounded unresolved-relation diagnostics.

This module is deliberately persistence- and traversal-free.  It defines the
closed relation vocabulary that a later graph retriever may consume, plus a
deterministic diagnostic summary that ingestion/resolution code can persist
through its own governed boundary.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import Field, StrictInt, field_validator, model_validator

from .contracts import (
    CodeRelationType,
    CodeTask,
    ContractText,
    _ContractModel,
)


class CodeGraphEntityType(StrEnum):
    """Closed graph endpoint types; local variables are retrieval detail, not entities."""

    REPOSITORY = "Repository"
    COMMIT = "Commit"
    GIT_COMMIT = "GitCommit"
    WORKTREE = "Worktree"
    FILE_VERSION = "FileVersion"
    CODE_SYMBOL = "CodeSymbol"
    CODE_RETRIEVAL_UNIT = "CodeRetrievalUnit"
    TYPE_ENTITY = "TypeEntity"
    PACKAGE_MODULE = "PackageModule"
    DEPENDENCY = "Dependency"
    CONFIG_KEY = "ConfigKey"
    DIFF_HUNK = "DiffHunk"
    CHANGE_SET = "ChangeSet"
    TEST_CASE = "TestCase"
    TEST_RESULT = "TestResult"
    COVERAGE = "Coverage"
    VALIDATION_TARGET = "ValidationTarget"


class CodeSymbolKind(StrEnum):
    """Entity-worthy symbol kinds.

    ``local_variable`` and parameters are intentionally absent.  They may remain
    parser/resolver details but cannot be promoted to graph entities.
    """

    UNKNOWN = "unknown"
    MODULE = "module"
    CLASS = "class"
    INTERFACE = "interface"
    TRAIT = "trait"
    ENUM = "enum"
    FUNCTION = "function"
    METHOD = "method"
    PROPERTY = "property"
    FIELD = "field"
    CONSTANT = "constant"
    TYPE_ALIAS = "type_alias"
    TEST = "test"


class CodeEdgeDirection(StrEnum):
    """Meaning of the stored source-to-target orientation."""

    FORWARD = "forward"
    BIDIRECTIONAL = "bidirectional"


class CodeEdgeInverse(StrEnum):
    """Typed display/traversal meaning when a stored edge is followed incoming."""

    DEFINED_BY = "DEFINED_BY"
    CONTAINED_BY = "CONTAINED_BY"
    IMPORTED_BY = "IMPORTED_BY"
    CALLED_BY = "CALLED_BY"
    REFERENCED_BY = "REFERENCED_BY"
    CHILD_OF = "CHILD_OF"
    HAS_TYPE = "HAS_TYPE"
    IMPLEMENTED_BY = "IMPLEMENTED_BY"
    OVERRIDDEN_BY = "OVERRIDDEN_BY"
    TESTED_BY = "TESTED_BY"
    COVERED_BY = "COVERED_BY"
    AFFECTED_BY = "AFFECTED_BY"
    VALIDATES = "VALIDATES"
    HAS_FAILED_VALIDATION = "HAS_FAILED_VALIDATION"
    SAME_SYMBOL_AS = "SAME_SYMBOL_AS"
    RENAMED_FROM = "RENAMED_FROM"
    MOVED_FROM = "MOVED_FROM"


class CodeEdgeOwner(StrEnum):
    """Endpoint that owns the stored evidence locator and deletion lifecycle."""

    SOURCE = "source"
    BOTH = "both"


class CodeEdgeDerivationLayer(StrEnum):
    """Truth layer, ordered from direct evidence to governed human assertion."""

    DETERMINISTIC = "deterministic"
    STATIC = "static"
    SEMANTIC = "semantic"
    HUMAN = "human"


class CodeEdgeVersionRequirement(StrEnum):
    """Stable-version alignment required before an edge is materialized."""

    SAME_STABLE_VERSION = "same_stable_version"
    EXACT_TARGET_VERSION = "exact_target_version"
    EXPLICIT_VERSION_TRANSITION = "explicit_version_transition"


class CodeEdgeGenerationRequirement(StrEnum):
    """Index-generation alignment required before an edge is materialized."""

    SAME_GENERATION = "same_generation"
    TARGET_ALIGNED = "target_aligned"
    EXPLICIT_GENERATIONS = "explicit_generations"


class CodeEdgeReviewRequirement(StrEnum):
    """Review gate attached to the strongest permitted derivation layer."""

    NONE = "none"
    SEMANTIC_CANDIDATES = "semantic_candidates"
    ALWAYS = "always"


_ENTITY_ORDER = {value: index for index, value in enumerate(CodeGraphEntityType)}
_DERIVATION_ORDER = {value: index for index, value in enumerate(CodeEdgeDerivationLayer)}
_TASK_ORDER = {value: index for index, value in enumerate(CodeTask)}
_RELATION_ORDER = {value: index for index, value in enumerate(CodeRelationType)}


class CodeGraphEntity(_ContractModel):
    """Minimal governed entity reference used by graph diagnostics."""

    entity_id: ContractText
    entity_type: CodeGraphEntityType
    symbol_kind: CodeSymbolKind = CodeSymbolKind.UNKNOWN

    @model_validator(mode="after")
    def validate_symbol_kind(self) -> CodeGraphEntity:
        if (
            self.entity_type is not CodeGraphEntityType.CODE_SYMBOL
            and self.symbol_kind is not CodeSymbolKind.UNKNOWN
        ):
            raise ValueError("symbol_kind is only valid for CodeSymbol entities")
        return self


class CodeEdgeSpec(_ContractModel):
    """One registered relation's complete materialization and traversal semantics."""

    edge_type: CodeRelationType
    source_types: tuple[CodeGraphEntityType, ...] = Field(min_length=1)
    target_types: tuple[CodeGraphEntityType, ...] = Field(min_length=1)
    direction: CodeEdgeDirection
    inverse: CodeEdgeInverse
    owner: CodeEdgeOwner
    derivations: tuple[CodeEdgeDerivationLayer, ...] = Field(min_length=1)
    confidence_meaning: ContractText
    version_requirement: CodeEdgeVersionRequirement
    generation_requirement: CodeEdgeGenerationRequirement
    allowed_tasks: tuple[CodeTask, ...] = Field(min_length=1)
    default_traversal_cost: Annotated[StrictInt, Field(ge=1, le=10)]
    review_requirement: CodeEdgeReviewRequirement

    @field_validator("source_types", "target_types")
    @classmethod
    def canonicalize_entity_types(
        cls,
        values: tuple[CodeGraphEntityType, ...],
    ) -> tuple[CodeGraphEntityType, ...]:
        if len(values) != len(set(values)):
            raise ValueError("edge endpoint types must not contain duplicates")
        return tuple(sorted(values, key=_ENTITY_ORDER.__getitem__))

    @field_validator("derivations")
    @classmethod
    def canonicalize_derivations(
        cls,
        values: tuple[CodeEdgeDerivationLayer, ...],
    ) -> tuple[CodeEdgeDerivationLayer, ...]:
        if len(values) != len(set(values)):
            raise ValueError("edge derivations must not contain duplicates")
        return tuple(sorted(values, key=_DERIVATION_ORDER.__getitem__))

    @field_validator("allowed_tasks")
    @classmethod
    def canonicalize_tasks(cls, values: tuple[CodeTask, ...]) -> tuple[CodeTask, ...]:
        if len(values) != len(set(values)):
            raise ValueError("edge allowed_tasks must not contain duplicates")
        return tuple(sorted(values, key=_TASK_ORDER.__getitem__))

    @model_validator(mode="after")
    def validate_layer_semantics(self) -> CodeEdgeSpec:
        semantic = CodeEdgeDerivationLayer.SEMANTIC in self.derivations
        if self.edge_type is CodeRelationType.CALLS and semantic:
            raise ValueError("semantic derivation cannot assert a CALLS edge")
        if semantic and self.review_requirement is CodeEdgeReviewRequirement.NONE:
            raise ValueError("semantic derivations require an explicit review gate")
        if (
            not semantic
            and self.review_requirement is CodeEdgeReviewRequirement.SEMANTIC_CANDIDATES
        ):
            raise ValueError("semantic-candidate review requires semantic derivation")
        if self.direction is CodeEdgeDirection.BIDIRECTIONAL and (
            self.inverse is not CodeEdgeInverse.SAME_SYMBOL_AS
        ):
            raise ValueError("bidirectional edges require a self-inverse semantic")
        return self

    def accepts_endpoints(
        self,
        source_type: CodeGraphEntityType | str,
        target_type: CodeGraphEntityType | str,
    ) -> bool:
        """Return whether endpoint types conform; unknown/local types fail closed."""

        try:
            source = CodeGraphEntityType(source_type)
            target = CodeGraphEntityType(target_type)
        except (TypeError, ValueError):
            return False
        return source in self.source_types and target in self.target_types

    def require_derivation(
        self,
        derivation: CodeEdgeDerivationLayer | str,
    ) -> CodeEdgeDerivationLayer:
        """Validate a producer layer before an edge can cross the graph boundary."""

        try:
            resolved = CodeEdgeDerivationLayer(derivation)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown edge derivation layer: {derivation!r}") from exc
        if resolved not in self.derivations:
            raise ValueError(
                f"{resolved.value} derivation is not allowed for {self.edge_type.value}"
            )
        return resolved


_ALL_TASKS = tuple(CodeTask)
_LOCATION_IMPLEMENTATION_TASKS = (
    CodeTask.EXACT_LOCATION,
    CodeTask.IMPLEMENTATION,
    CodeTask.CALL_PATH,
    CodeTask.BUG_LOCALIZATION,
    CodeTask.IMPACT_ANALYSIS,
)
_BEHAVIOR_TASKS = (
    CodeTask.IMPLEMENTATION,
    CodeTask.CALL_PATH,
    CodeTask.BUG_LOCALIZATION,
    CodeTask.IMPACT_ANALYSIS,
    CodeTask.TEST_VALIDATION,
)
_CHANGE_TASKS = (
    CodeTask.BUG_LOCALIZATION,
    CodeTask.IMPACT_ANALYSIS,
    CodeTask.CHANGE_CONTEXT,
    CodeTask.HISTORICAL,
    CodeTask.TEST_VALIDATION,
)
_LINEAGE_TASKS = (
    CodeTask.EXACT_LOCATION,
    CodeTask.IMPLEMENTATION,
    CodeTask.BUG_LOCALIZATION,
    CodeTask.IMPACT_ANALYSIS,
    CodeTask.CHANGE_CONTEXT,
    CodeTask.HISTORICAL,
)
_DETERMINISTIC_HUMAN = (
    CodeEdgeDerivationLayer.DETERMINISTIC,
    CodeEdgeDerivationLayer.HUMAN,
)
_STATIC_HUMAN = (
    CodeEdgeDerivationLayer.STATIC,
    CodeEdgeDerivationLayer.HUMAN,
)
_DETERMINISTIC_STATIC_HUMAN = (
    CodeEdgeDerivationLayer.DETERMINISTIC,
    CodeEdgeDerivationLayer.STATIC,
    CodeEdgeDerivationLayer.HUMAN,
)
_STATIC_SEMANTIC_HUMAN = (
    CodeEdgeDerivationLayer.STATIC,
    CodeEdgeDerivationLayer.SEMANTIC,
    CodeEdgeDerivationLayer.HUMAN,
)
_DETERMINISTIC_STATIC_SEMANTIC_HUMAN = (
    CodeEdgeDerivationLayer.DETERMINISTIC,
    CodeEdgeDerivationLayer.STATIC,
    CodeEdgeDerivationLayer.SEMANTIC,
    CodeEdgeDerivationLayer.HUMAN,
)


def _spec(
    edge_type: CodeRelationType,
    *,
    source_types: tuple[CodeGraphEntityType, ...],
    target_types: tuple[CodeGraphEntityType, ...],
    inverse: CodeEdgeInverse,
    derivations: tuple[CodeEdgeDerivationLayer, ...],
    confidence_meaning: str,
    allowed_tasks: tuple[CodeTask, ...],
    default_traversal_cost: int,
    owner: CodeEdgeOwner = CodeEdgeOwner.SOURCE,
    direction: CodeEdgeDirection = CodeEdgeDirection.FORWARD,
    version_requirement: CodeEdgeVersionRequirement = (
        CodeEdgeVersionRequirement.SAME_STABLE_VERSION
    ),
    generation_requirement: CodeEdgeGenerationRequirement = (
        CodeEdgeGenerationRequirement.SAME_GENERATION
    ),
    review_requirement: CodeEdgeReviewRequirement = CodeEdgeReviewRequirement.NONE,
) -> CodeEdgeSpec:
    return CodeEdgeSpec(
        edge_type=edge_type,
        source_types=source_types,
        target_types=target_types,
        direction=direction,
        inverse=inverse,
        owner=owner,
        derivations=derivations,
        confidence_meaning=confidence_meaning,
        version_requirement=version_requirement,
        generation_requirement=generation_requirement,
        allowed_tasks=allowed_tasks,
        default_traversal_cost=default_traversal_cost,
        review_requirement=review_requirement,
    )


_EDGE_SPECS = (
    _spec(
        CodeRelationType.DEFINES,
        source_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
        ),
        target_types=(
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.CODE_RETRIEVAL_UNIT,
            CodeGraphEntityType.TYPE_ENTITY,
        ),
        inverse=CodeEdgeInverse.DEFINED_BY,
        derivations=_DETERMINISTIC_HUMAN,
        confidence_meaning=(
            "Confidence is source-structure identity: 1 means a direct parser or governed "
            "human definition, not semantic similarity."
        ),
        allowed_tasks=_LOCATION_IMPLEMENTATION_TASKS,
        default_traversal_cost=1,
    ),
    _spec(
        CodeRelationType.CONTAINS,
        source_types=(
            CodeGraphEntityType.REPOSITORY,
            CodeGraphEntityType.COMMIT,
            CodeGraphEntityType.GIT_COMMIT,
            CodeGraphEntityType.WORKTREE,
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.CODE_RETRIEVAL_UNIT,
        ),
        target_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.CODE_RETRIEVAL_UNIT,
            CodeGraphEntityType.DIFF_HUNK,
            CodeGraphEntityType.TEST_RESULT,
        ),
        inverse=CodeEdgeInverse.CONTAINED_BY,
        derivations=_DETERMINISTIC_HUMAN,
        confidence_meaning=(
            "Confidence is exact structural or snapshot membership; it does not measure "
            "retrieval relevance."
        ),
        allowed_tasks=_ALL_TASKS,
        default_traversal_cost=1,
    ),
    _spec(
        CodeRelationType.IMPORTS,
        source_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.PACKAGE_MODULE,
        ),
        target_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.PACKAGE_MODULE,
            CodeGraphEntityType.DEPENDENCY,
        ),
        inverse=CodeEdgeInverse.IMPORTED_BY,
        derivations=_DETERMINISTIC_STATIC_HUMAN,
        confidence_meaning=(
            "Confidence is import resolution certainty; parsed syntax may be exact while "
            "static target resolution may be partial."
        ),
        allowed_tasks=_LOCATION_IMPLEMENTATION_TASKS,
        default_traversal_cost=2,
    ),
    _spec(
        CodeRelationType.CALLS,
        source_types=(CodeGraphEntityType.CODE_SYMBOL,),
        target_types=(CodeGraphEntityType.CODE_SYMBOL,),
        inverse=CodeEdgeInverse.CALLED_BY,
        derivations=_STATIC_HUMAN,
        confidence_meaning=(
            "Confidence is resolver certainty for a concrete caller and callee at the same "
            "version; it is never vector similarity or runtime call frequency."
        ),
        allowed_tasks=_BEHAVIOR_TASKS,
        default_traversal_cost=1,
    ),
    _spec(
        CodeRelationType.REFERENCES,
        source_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
        ),
        target_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.TYPE_ENTITY,
            CodeGraphEntityType.CONFIG_KEY,
        ),
        inverse=CodeEdgeInverse.REFERENCED_BY,
        derivations=_STATIC_HUMAN,
        confidence_meaning=(
            "Confidence is static name-to-entity resolution certainty, not a generic "
            "semantic association."
        ),
        allowed_tasks=_BEHAVIOR_TASKS,
        default_traversal_cost=2,
    ),
    _spec(
        CodeRelationType.PARENT_OF,
        source_types=(
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.CODE_RETRIEVAL_UNIT,
        ),
        target_types=(
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.CODE_RETRIEVAL_UNIT,
        ),
        inverse=CodeEdgeInverse.CHILD_OF,
        derivations=_DETERMINISTIC_HUMAN,
        confidence_meaning="Confidence is exact lexical/AST parentage within one generation.",
        allowed_tasks=_LOCATION_IMPLEMENTATION_TASKS,
        default_traversal_cost=1,
    ),
    _spec(
        CodeRelationType.TYPE_OF,
        source_types=(CodeGraphEntityType.CODE_SYMBOL,),
        target_types=(CodeGraphEntityType.TYPE_ENTITY,),
        inverse=CodeEdgeInverse.HAS_TYPE,
        derivations=_STATIC_HUMAN,
        confidence_meaning=(
            "Confidence is static type-resolution certainty for an entity-worthy symbol; "
            "locals and parameters remain resolver detail."
        ),
        allowed_tasks=_LOCATION_IMPLEMENTATION_TASKS,
        default_traversal_cost=2,
    ),
    _spec(
        CodeRelationType.IMPLEMENTS,
        source_types=(
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.TYPE_ENTITY,
        ),
        target_types=(
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.TYPE_ENTITY,
        ),
        inverse=CodeEdgeInverse.IMPLEMENTED_BY,
        derivations=_STATIC_HUMAN,
        confidence_meaning=(
            "Confidence is compiler/indexer certainty for an explicit implementation relationship."
        ),
        allowed_tasks=_LOCATION_IMPLEMENTATION_TASKS,
        default_traversal_cost=1,
    ),
    _spec(
        CodeRelationType.OVERRIDES,
        source_types=(CodeGraphEntityType.CODE_SYMBOL,),
        target_types=(CodeGraphEntityType.CODE_SYMBOL,),
        inverse=CodeEdgeInverse.OVERRIDDEN_BY,
        derivations=_STATIC_HUMAN,
        confidence_meaning=(
            "Confidence is static dispatch-hierarchy certainty for a concrete overriding "
            "and overridden symbol pair."
        ),
        allowed_tasks=_LOCATION_IMPLEMENTATION_TASKS,
        default_traversal_cost=1,
    ),
    _spec(
        CodeRelationType.TESTS,
        source_types=(
            CodeGraphEntityType.TEST_CASE,
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.VALIDATION_TARGET,
        ),
        target_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.TYPE_ENTITY,
        ),
        inverse=CodeEdgeInverse.TESTED_BY,
        derivations=_DETERMINISTIC_STATIC_HUMAN,
        confidence_meaning=(
            "Confidence is selector, framework, or static test-target alignment; it does "
            "not imply the test ran, passed, or covered the target."
        ),
        allowed_tasks=_BEHAVIOR_TASKS,
        default_traversal_cost=1,
    ),
    _spec(
        CodeRelationType.COVERS,
        source_types=(
            CodeGraphEntityType.COVERAGE,
            CodeGraphEntityType.TEST_RESULT,
        ),
        target_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
            CodeGraphEntityType.CODE_RETRIEVAL_UNIT,
        ),
        inverse=CodeEdgeInverse.COVERED_BY,
        derivations=_DETERMINISTIC_STATIC_HUMAN,
        confidence_meaning=(
            "Confidence is observed coverage-to-version alignment; it is separate from "
            "test pass status."
        ),
        allowed_tasks=(
            CodeTask.BUG_LOCALIZATION,
            CodeTask.IMPACT_ANALYSIS,
            CodeTask.TEST_VALIDATION,
        ),
        default_traversal_cost=1,
        version_requirement=CodeEdgeVersionRequirement.EXACT_TARGET_VERSION,
        generation_requirement=CodeEdgeGenerationRequirement.TARGET_ALIGNED,
    ),
    _spec(
        CodeRelationType.AFFECTS,
        source_types=(
            CodeGraphEntityType.DIFF_HUNK,
            CodeGraphEntityType.CHANGE_SET,
            CodeGraphEntityType.GIT_COMMIT,
        ),
        target_types=(
            CodeGraphEntityType.FILE_VERSION,
            CodeGraphEntityType.CODE_SYMBOL,
        ),
        inverse=CodeEdgeInverse.AFFECTED_BY,
        derivations=_DETERMINISTIC_STATIC_SEMANTIC_HUMAN,
        confidence_meaning=(
            "Confidence is change-to-target alignment. Semantic values are candidate "
            "likelihoods requiring review, never proof of impact."
        ),
        allowed_tasks=_CHANGE_TASKS,
        default_traversal_cost=2,
        version_requirement=CodeEdgeVersionRequirement.EXACT_TARGET_VERSION,
        generation_requirement=CodeEdgeGenerationRequirement.TARGET_ALIGNED,
        review_requirement=CodeEdgeReviewRequirement.SEMANTIC_CANDIDATES,
    ),
    _spec(
        CodeRelationType.VALIDATED_BY,
        source_types=(
            CodeGraphEntityType.COMMIT,
            CodeGraphEntityType.GIT_COMMIT,
            CodeGraphEntityType.WORKTREE,
            CodeGraphEntityType.CHANGE_SET,
            CodeGraphEntityType.VALIDATION_TARGET,
        ),
        target_types=(CodeGraphEntityType.TEST_RESULT,),
        inverse=CodeEdgeInverse.VALIDATES,
        derivations=_DETERMINISTIC_HUMAN,
        confidence_meaning=(
            "Confidence is exact target-version alignment for an observed passing result "
            "with exit code zero; it is not inferred freshness."
        ),
        allowed_tasks=_CHANGE_TASKS,
        default_traversal_cost=1,
        version_requirement=CodeEdgeVersionRequirement.EXACT_TARGET_VERSION,
        generation_requirement=CodeEdgeGenerationRequirement.TARGET_ALIGNED,
    ),
    _spec(
        CodeRelationType.FAILED_VALIDATION,
        source_types=(
            CodeGraphEntityType.COMMIT,
            CodeGraphEntityType.GIT_COMMIT,
            CodeGraphEntityType.WORKTREE,
            CodeGraphEntityType.CHANGE_SET,
            CodeGraphEntityType.VALIDATION_TARGET,
        ),
        target_types=(CodeGraphEntityType.TEST_RESULT,),
        inverse=CodeEdgeInverse.HAS_FAILED_VALIDATION,
        derivations=_DETERMINISTIC_HUMAN,
        confidence_meaning=(
            "Confidence is exact target-version alignment for an observed failed, error, "
            "or nonzero-exit result; it must not be treated as validation success."
        ),
        allowed_tasks=_CHANGE_TASKS,
        default_traversal_cost=1,
        version_requirement=CodeEdgeVersionRequirement.EXACT_TARGET_VERSION,
        generation_requirement=CodeEdgeGenerationRequirement.TARGET_ALIGNED,
    ),
    _spec(
        CodeRelationType.SAME_SYMBOL_AS,
        source_types=(CodeGraphEntityType.CODE_SYMBOL,),
        target_types=(CodeGraphEntityType.CODE_SYMBOL,),
        inverse=CodeEdgeInverse.SAME_SYMBOL_AS,
        derivations=_STATIC_SEMANTIC_HUMAN,
        confidence_meaning=(
            "Confidence is cross-version identity likelihood; semantic matches remain "
            "reviewable candidates until static or human confirmation."
        ),
        allowed_tasks=_LINEAGE_TASKS,
        default_traversal_cost=2,
        owner=CodeEdgeOwner.BOTH,
        direction=CodeEdgeDirection.BIDIRECTIONAL,
        version_requirement=CodeEdgeVersionRequirement.EXPLICIT_VERSION_TRANSITION,
        generation_requirement=CodeEdgeGenerationRequirement.EXPLICIT_GENERATIONS,
        review_requirement=CodeEdgeReviewRequirement.SEMANTIC_CANDIDATES,
    ),
    _spec(
        CodeRelationType.RENAMED_TO,
        source_types=(CodeGraphEntityType.CODE_SYMBOL,),
        target_types=(CodeGraphEntityType.CODE_SYMBOL,),
        inverse=CodeEdgeInverse.RENAMED_FROM,
        derivations=_DETERMINISTIC_STATIC_SEMANTIC_HUMAN,
        confidence_meaning=(
            "Confidence is forward cross-version rename identity; Git evidence can be "
            "deterministic while similarity-only matches require review."
        ),
        allowed_tasks=_LINEAGE_TASKS,
        default_traversal_cost=1,
        version_requirement=CodeEdgeVersionRequirement.EXPLICIT_VERSION_TRANSITION,
        generation_requirement=CodeEdgeGenerationRequirement.EXPLICIT_GENERATIONS,
        review_requirement=CodeEdgeReviewRequirement.SEMANTIC_CANDIDATES,
    ),
    _spec(
        CodeRelationType.MOVED_TO,
        source_types=(CodeGraphEntityType.CODE_SYMBOL,),
        target_types=(CodeGraphEntityType.CODE_SYMBOL,),
        inverse=CodeEdgeInverse.MOVED_FROM,
        derivations=_DETERMINISTIC_STATIC_SEMANTIC_HUMAN,
        confidence_meaning=(
            "Confidence is forward cross-version path-move identity; similarity-only "
            "matches require review."
        ),
        allowed_tasks=_LINEAGE_TASKS,
        default_traversal_cost=1,
        version_requirement=CodeEdgeVersionRequirement.EXPLICIT_VERSION_TRANSITION,
        generation_requirement=CodeEdgeGenerationRequirement.EXPLICIT_GENERATIONS,
        review_requirement=CodeEdgeReviewRequirement.SEMANTIC_CANDIDATES,
    ),
)


def _build_registry(
    specs: Iterable[CodeEdgeSpec],
) -> Mapping[CodeRelationType, CodeEdgeSpec]:
    registry: dict[CodeRelationType, CodeEdgeSpec] = {}
    for spec in specs:
        if spec.edge_type in registry:
            raise RuntimeError(f"duplicate Code edge specification: {spec.edge_type.value}")
        registry[spec.edge_type] = spec
    missing = set(CodeRelationType).difference(registry)
    extra = set(registry).difference(CodeRelationType)
    if missing or extra:
        missing_values = sorted(item.value for item in missing)
        extra_values = sorted(item.value for item in extra)
        raise RuntimeError(
            f"Code edge registry mismatch: missing={missing_values}, extra={extra_values}"
        )
    return MappingProxyType(
        {
            relation: registry[relation]
            for relation in sorted(registry, key=_RELATION_ORDER.__getitem__)
        }
    )


CODE_EDGE_REGISTRY = _build_registry(_EDGE_SPECS)
"""Immutable, exhaustive registry keyed only by :class:`CodeRelationType`."""

EDGE_REGISTRY = CODE_EDGE_REGISTRY
"""Concise public alias for the immutable Code edge registry."""

CODE_EDGE_SPECS = tuple(CODE_EDGE_REGISTRY.values())
"""Stable serialized-order registry snapshot."""


def registered_edge_type(value: CodeRelationType | str) -> CodeRelationType:
    """Return a registered edge enum or fail closed for free-form relation text."""

    try:
        edge_type = CodeRelationType(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unregistered Code edge type: {value!r}") from exc
    if edge_type not in CODE_EDGE_REGISTRY:
        raise ValueError(f"unregistered Code edge type: {value!r}")
    return edge_type


def get_edge_spec(value: CodeRelationType | str) -> CodeEdgeSpec:
    """Resolve only a registered relation to its immutable ontology specification."""

    return CODE_EDGE_REGISTRY[registered_edge_type(value)]


def require_registered_edge_types(
    values: Iterable[CodeRelationType | str],
) -> tuple[CodeRelationType, ...]:
    """Canonicalize a downstream traversal whitelist without accepting free edge text."""

    edge_types = tuple(registered_edge_type(value) for value in values)
    if len(edge_types) != len(set(edge_types)):
        raise ValueError("registered edge types must not contain duplicates")
    return tuple(sorted(edge_types, key=_RELATION_ORDER.__getitem__))


def validate_edge_assertion(
    edge_type: CodeRelationType | str,
    *,
    source_type: CodeGraphEntityType | str,
    target_type: CodeGraphEntityType | str,
    derivation: CodeEdgeDerivationLayer | str,
) -> CodeEdgeSpec:
    """Fail closed unless relation, endpoints, and derivation match the registry."""

    spec = get_edge_spec(edge_type)
    if not spec.accepts_endpoints(source_type, target_type):
        raise ValueError(
            f"endpoint types are not allowed for registered edge {spec.edge_type.value}"
        )
    spec.require_derivation(derivation)
    return spec


class CodeUnresolvedReason(StrEnum):
    """Exhaustive reason why a raw relation target was not materialized."""

    NO_CANDIDATE = "no_candidate"
    AMBIGUOUS = "ambiguous"
    EXTERNAL = "external"
    DYNAMIC = "dynamic"
    LIMIT = "limit"


class CodeUnresolvedDiagnostic(_ContractModel):
    """One sampled unresolved relation detail with resolver provenance."""

    source: CodeGraphEntity
    raw_target: ContractText
    relation: CodeRelationType
    reason: CodeUnresolvedReason
    candidate_ids: tuple[ContractText, ...] = ()
    parser_version: ContractText
    resolver_version: ContractText
    language: ContractText = "unknown"
    module: ContractText = "unknown"

    @field_validator("candidate_ids")
    @classmethod
    def canonicalize_candidates(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("candidate_ids must not contain duplicates")
        return tuple(sorted(values))

    def sampling_bytes(self) -> bytes:
        """Canonical detail identity used for stable bounded sampling."""

        return self.canonical_json_bytes()


class CodeUnresolvedAggregate(_ContractModel):
    """Lossless counter cell retained independently from bounded detail samples."""

    source_entity_id: ContractText
    source_entity_type: CodeGraphEntityType
    relation: CodeRelationType
    reason: CodeUnresolvedReason
    language: ContractText
    module: ContractText
    count: Annotated[StrictInt, Field(ge=1)]


class CodeUnresolvedLanguageModuleAggregate(_ContractModel):
    """Reader-facing aggregation across source entities and relation reasons."""

    language: ContractText
    module: ContractText
    count: Annotated[StrictInt, Field(ge=1)]


class CodeUnresolvedDiagnostics(_ContractModel):
    """Deterministic bounded detail sample plus uncapped aggregate counters."""

    detail_limit_per_source_relation: Annotated[StrictInt, Field(ge=0)]
    total_count: Annotated[StrictInt, Field(ge=0)]
    details: tuple[CodeUnresolvedDiagnostic, ...] = ()
    aggregates: tuple[CodeUnresolvedAggregate, ...] = ()

    @field_validator("details")
    @classmethod
    def canonicalize_details(
        cls,
        values: tuple[CodeUnresolvedDiagnostic, ...],
    ) -> tuple[CodeUnresolvedDiagnostic, ...]:
        identities = [value.sampling_bytes() for value in values]
        if len(identities) != len(set(identities)):
            raise ValueError("diagnostic details must not contain duplicates")
        return tuple(sorted(values, key=_diagnostic_sort_key))

    @field_validator("aggregates")
    @classmethod
    def canonicalize_aggregates(
        cls,
        values: tuple[CodeUnresolvedAggregate, ...],
    ) -> tuple[CodeUnresolvedAggregate, ...]:
        keys = [_aggregate_sort_key(value) for value in values]
        if len(keys) != len(set(keys)):
            raise ValueError("diagnostic aggregate keys must not contain duplicates")
        return tuple(sorted(values, key=_aggregate_sort_key))

    @model_validator(mode="after")
    def validate_summary(self) -> CodeUnresolvedDiagnostics:
        if self.total_count != sum(item.count for item in self.aggregates):
            raise ValueError("total_count must equal the uncapped aggregate count")
        bucket_counts = Counter(
            (detail.source.entity_id, detail.relation) for detail in self.details
        )
        if any(count > self.detail_limit_per_source_relation for count in bucket_counts.values()):
            raise ValueError("diagnostic detail sample exceeds its source/relation limit")
        return self

    def count(
        self,
        *,
        source_entity_id: str | None = None,
        relation: CodeRelationType | str | None = None,
        reason: CodeUnresolvedReason | str | None = None,
        language: str | None = None,
        module: str | None = None,
    ) -> int:
        """Sum permanent counters through deterministic language/module filters."""

        resolved_relation = registered_edge_type(relation) if relation is not None else None
        resolved_reason = CodeUnresolvedReason(reason) if reason is not None else None
        return sum(
            item.count
            for item in self.aggregates
            if (source_entity_id is None or item.source_entity_id == source_entity_id)
            and (resolved_relation is None or item.relation is resolved_relation)
            and (resolved_reason is None or item.reason is resolved_reason)
            and (language is None or item.language == language)
            and (module is None or item.module == module)
        )

    def by_language_module(
        self,
    ) -> tuple[CodeUnresolvedLanguageModuleAggregate, ...]:
        """Aggregate all permanent counts by language/module in canonical order."""

        counts: Counter[tuple[str, str]] = Counter()
        for item in self.aggregates:
            counts[(item.language, item.module)] += item.count
        return tuple(
            CodeUnresolvedLanguageModuleAggregate(
                language=language,
                module=module,
                count=count,
            )
            for (language, module), count in sorted(counts.items())
        )


_AggregateKey = tuple[
    str,
    CodeGraphEntityType,
    CodeRelationType,
    CodeUnresolvedReason,
    str,
    str,
]


def _diagnostic_sort_key(diagnostic: CodeUnresolvedDiagnostic) -> tuple[Any, ...]:
    return (
        diagnostic.source.entity_id,
        _RELATION_ORDER[diagnostic.relation],
        diagnostic.raw_target,
        diagnostic.reason.value,
        diagnostic.candidate_ids,
        diagnostic.parser_version,
        diagnostic.resolver_version,
        diagnostic.language,
        diagnostic.module,
    )


def _aggregate_sort_key(aggregate: CodeUnresolvedAggregate) -> tuple[Any, ...]:
    return (
        aggregate.source_entity_id,
        _ENTITY_ORDER[aggregate.source_entity_type],
        _RELATION_ORDER[aggregate.relation],
        aggregate.reason.value,
        aggregate.language,
        aggregate.module,
    )


def _sample_rank(diagnostic: CodeUnresolvedDiagnostic) -> tuple[bytes, bytes]:
    canonical = diagnostic.sampling_bytes()
    return hashlib.sha256(canonical).digest(), canonical


def summarize_unresolved_diagnostics(
    diagnostics: Iterable[CodeUnresolvedDiagnostic | Mapping[str, Any]],
    *,
    detail_limit_per_source_relation: int = 20,
) -> CodeUnresolvedDiagnostics:
    """Build an order-independent bounded sample without dropping aggregate counts."""

    if (
        isinstance(detail_limit_per_source_relation, bool)
        or not isinstance(detail_limit_per_source_relation, int)
        or detail_limit_per_source_relation < 0
    ):
        raise ValueError("detail_limit_per_source_relation must be a non-negative integer")

    aggregate_counts: Counter[_AggregateKey] = Counter()
    samples: defaultdict[
        tuple[str, CodeRelationType],
        dict[bytes, CodeUnresolvedDiagnostic],
    ] = defaultdict(dict)
    total_count = 0

    for raw_diagnostic in diagnostics:
        diagnostic = (
            raw_diagnostic
            if isinstance(raw_diagnostic, CodeUnresolvedDiagnostic)
            else CodeUnresolvedDiagnostic.model_validate(raw_diagnostic)
        )
        total_count += 1
        aggregate_key: _AggregateKey = (
            diagnostic.source.entity_id,
            diagnostic.source.entity_type,
            diagnostic.relation,
            diagnostic.reason,
            diagnostic.language,
            diagnostic.module,
        )
        aggregate_counts[aggregate_key] += 1

        if detail_limit_per_source_relation:
            bucket = samples[(diagnostic.source.entity_id, diagnostic.relation)]
            canonical = diagnostic.sampling_bytes()
            bucket[canonical] = diagnostic
            if len(bucket) > detail_limit_per_source_relation:
                retained = sorted(bucket.values(), key=_sample_rank)[
                    :detail_limit_per_source_relation
                ]
                bucket.clear()
                bucket.update((item.sampling_bytes(), item) for item in retained)

    details = tuple(diagnostic for bucket in samples.values() for diagnostic in bucket.values())
    aggregates = tuple(
        CodeUnresolvedAggregate(
            source_entity_id=source_entity_id,
            source_entity_type=source_entity_type,
            relation=relation,
            reason=reason,
            language=language,
            module=module,
            count=count,
        )
        for (
            source_entity_id,
            source_entity_type,
            relation,
            reason,
            language,
            module,
        ), count in aggregate_counts.items()
    )
    return CodeUnresolvedDiagnostics(
        detail_limit_per_source_relation=detail_limit_per_source_relation,
        total_count=total_count,
        details=details,
        aggregates=aggregates,
    )


__all__ = [
    "CODE_EDGE_REGISTRY",
    "CODE_EDGE_SPECS",
    "EDGE_REGISTRY",
    "CodeEdgeDerivationLayer",
    "CodeEdgeDirection",
    "CodeEdgeInverse",
    "CodeEdgeOwner",
    "CodeEdgeReviewRequirement",
    "CodeEdgeGenerationRequirement",
    "CodeEdgeSpec",
    "CodeEdgeVersionRequirement",
    "CodeGraphEntity",
    "CodeGraphEntityType",
    "CodeSymbolKind",
    "CodeUnresolvedAggregate",
    "CodeUnresolvedDiagnostic",
    "CodeUnresolvedDiagnostics",
    "CodeUnresolvedLanguageModuleAggregate",
    "CodeUnresolvedReason",
    "get_edge_spec",
    "registered_edge_type",
    "require_registered_edge_types",
    "summarize_unresolved_diagnostics",
    "validate_edge_assertion",
]
