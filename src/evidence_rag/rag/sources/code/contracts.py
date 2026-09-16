"""Serializable, runtime-independent contracts for Code Source retrieval."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


def _validate_contract_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    if value != value.strip():
        raise ValueError("control text must not have leading or trailing whitespace")
    for character in value:
        if character.isspace() and character != " ":
            raise ValueError("control text must not contain non-ASCII or control whitespace")
        if unicodedata.category(character).startswith("C"):
            raise ValueError("control text must not contain control characters")
    return value


def _validate_exact_float(value: object) -> float:
    if type(value) is not float:
        raise ValueError("value must be an exact float")
    if value == 0.0:
        return 0.0
    return value


def _validate_context_content(value: str) -> str:
    if not value.strip():
        raise ValueError("context content must contain a non-whitespace character")
    for character in value:
        if unicodedata.category(character).startswith("C") and character not in {"\t", "\n", "\r"}:
            raise ValueError("context content contains an unsupported control character")
    return value


NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
ExactFloat = Annotated[float, BeforeValidator(_validate_exact_float)]
NonNegativeFloat = Annotated[ExactFloat, Field(ge=0)]
UnitFloat = Annotated[ExactFloat, Field(ge=0, le=1)]
ContractText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=4_000),
    AfterValidator(_validate_contract_text),
]
ContextContent = Annotated[
    StrictStr,
    Field(min_length=1, max_length=100_000),
    AfterValidator(_validate_context_content),
]


class _ContractModel(BaseModel):
    """Validated public model.

    ``model_construct`` remains Pydantic's explicit trusted-only escape hatch. It is
    intentionally not wrapped or presented as a normal contract construction API.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def canonical_json_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json_bytes()).hexdigest()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """Return a fully revalidated copy; Pydantic's unsafe update bypass is not exposed."""

        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(self, **_: Any) -> Self:
        """Disable Pydantic's deprecated unvalidated copy/update entry point."""

        raise TypeError("deprecated copy() is disabled; use validated model_copy()")


class CodeTask(StrEnum):
    """Deterministic Code Source routing tasks."""

    EXACT_LOCATION = "exact_location"
    IMPLEMENTATION = "implementation"
    CALL_PATH = "call_path"
    BUG_LOCALIZATION = "bug_localization"
    IMPACT_ANALYSIS = "impact_analysis"
    CHANGE_CONTEXT = "change_context"
    HISTORICAL = "historical"
    TEST_VALIDATION = "test_validation"


class CodeRetrievalChannel(StrEnum):
    """Independent candidate channels whose raw values are never cross-source scores."""

    EXACT = "exact"
    SPARSE = "sparse"
    DENSE = "dense"
    GRAPH = "graph"
    HISTORY = "history"
    TEST = "test"


class CodeTraversalDirection(StrEnum):
    """Direction in which a stored directed relation is traversed."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


class CodeRelationType(StrEnum):
    """P0 registered Code relations; new relations require an explicit registry addition."""

    DEFINES = "DEFINES"
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    REFERENCES = "REFERENCES"
    PARENT_OF = "PARENT_OF"
    TYPE_OF = "TYPE_OF"
    IMPLEMENTS = "IMPLEMENTS"
    OVERRIDES = "OVERRIDES"
    TESTS = "TESTS"
    COVERS = "COVERS"
    AFFECTS = "AFFECTS"
    VALIDATED_BY = "VALIDATED_BY"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    SAME_SYMBOL_AS = "SAME_SYMBOL_AS"
    RENAMED_TO = "RENAMED_TO"
    MOVED_TO = "MOVED_TO"


class CodeCandidateRole(StrEnum):
    """Why a candidate is necessary in a Code answer or context."""

    TARGET = "target"
    DEPENDENCY = "dependency"
    TEST = "test"
    HISTORY = "history"
    BACKGROUND = "background"


class CodeDerivation(StrEnum):
    """The producer of a fact or relation, kept separate from relevance."""

    RAW_SOURCE = "raw_source"
    TREE_SITTER = "tree_sitter"
    SCIP = "scip"
    LSP = "lsp"
    COMPILER = "compiler"
    COVERAGE = "coverage"
    RULE = "rule"
    VECTOR = "vector"
    HUMAN = "human"


class CodeFactStatus(StrEnum):
    """Truth posture of evidence, never inferred from its retrieval score."""

    OBSERVED = "observed"
    MACHINE_CONFIRMED = "machine_confirmed"
    REPORTED = "reported"
    INFERRED = "inferred"
    HISTORICAL_VALIDATION = "historical_validation"
    FAILED_VALIDATION = "failed_validation"
    COUNTER_EVIDENCE = "counter_evidence"


class CodeReviewStatus(StrEnum):
    """Independent review state for a fact or relation."""

    UNREVIEWED = "unreviewed"
    MACHINE_CONFIRMED = "machine_confirmed"
    HUMAN_CONFIRMED = "human_confirmed"
    REJECTED = "rejected"


class CodeVersionAlignment(StrEnum):
    """Relationship between candidate version and the resolved query watermark."""

    EXACT = "exact"
    COMPATIBLE = "compatible"
    HISTORICAL = "historical"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class CodeCalibrationStatus(StrEnum):
    """Whether a relevance probability is backed by a calibration artifact."""

    CALIBRATED = "calibrated"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class CodeChannelOutcomeStatus(StrEnum):
    """Exhaustive source-level outcome for one registered retrieval channel."""

    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"
    COMPLETE_NO_MATCH = "complete_no_match"
    COMPLETE_PRUNED = "complete_pruned"
    COMPLETE_WITH_HITS = "complete_with_hits"


class CodeFallbackStatus(StrEnum):
    """Explicit request-level fallback outcome."""

    NOT_USED = "not_used"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CodeSourceStatus(StrEnum):
    """Overall source outcome, interpreted together with all channel outcomes."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class CodeChannelErrorKind(StrEnum):
    """Failure kinds mirrored from channel outcomes."""

    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"


_CHANNEL_ORDER = {channel: index for index, channel in enumerate(CodeRetrievalChannel)}
_DIRECTION_ORDER = {direction: index for index, direction in enumerate(CodeTraversalDirection)}
_RELATION_ORDER = {relation_type: index for index, relation_type in enumerate(CodeRelationType)}
_ERROR_KIND_ORDER = {error_kind: index for index, error_kind in enumerate(CodeChannelErrorKind)}
_LINEAGE_RELATION_TYPES = frozenset(
    {
        CodeRelationType.SAME_SYMBOL_AS,
        CodeRelationType.RENAMED_TO,
        CodeRelationType.MOVED_TO,
    }
)


class CodeRetrievalBudget(_ContractModel):
    """Independent channel and output ceilings; channel pools may overlap."""

    total_candidates: NonNegativeInt = 50
    exact_candidates: NonNegativeInt = 20
    sparse_candidates: NonNegativeInt = 40
    dense_candidates: NonNegativeInt = 40
    graph_candidates: NonNegativeInt = 0
    history_candidates: NonNegativeInt = 0
    test_candidates: NonNegativeInt = 0
    graph_node_budget: NonNegativeInt = 0
    graph_edge_budget: NonNegativeInt = 0
    context_token_budget: NonNegativeInt = 4_000

    @model_validator(mode="after")
    def validate_channel_ceilings(self) -> CodeRetrievalBudget:
        channel_budgets = (
            self.exact_candidates,
            self.sparse_candidates,
            self.dense_candidates,
            self.graph_candidates,
            self.history_candidates,
            self.test_candidates,
        )
        if any(value > self.total_candidates for value in channel_budgets):
            raise ValueError("each channel candidate budget must not exceed total_candidates")
        if self.graph_candidates == 0 and (self.graph_node_budget or self.graph_edge_budget):
            raise ValueError("graph node/edge budgets require a non-zero graph candidate budget")
        if self.graph_candidates and (not self.graph_node_budget or not self.graph_edge_budget):
            raise ValueError("graph candidates require non-zero graph node and edge budgets")
        return self


class CodeQueryProfile(_ContractModel):
    """Resolved deterministic routing profile for one Code Source query."""

    profile_version: ContractText = "code-query-profile-v1"
    task: CodeTask
    target_identifiers: tuple[ContractText, ...] = ()
    target_paths: tuple[ContractText, ...] = ()
    target_ref: ContractText = "current"
    directions: tuple[CodeTraversalDirection, ...] = ()
    edge_types: tuple[CodeRelationType, ...] = ()
    max_hops: Annotated[StrictInt, Field(ge=0, le=4)] = 0
    require_tests: StrictBool = False
    include_history: StrictBool = False
    budget: CodeRetrievalBudget = Field(default_factory=CodeRetrievalBudget)

    @field_validator("target_identifiers", "target_paths")
    @classmethod
    def canonicalize_targets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("query targets must not contain canonical duplicates")
        return tuple(sorted(values))

    @field_validator("directions")
    @classmethod
    def canonicalize_directions(
        cls, values: tuple[CodeTraversalDirection, ...]
    ) -> tuple[CodeTraversalDirection, ...]:
        if len(values) != len(set(values)):
            raise ValueError("directions must not contain duplicates")
        return tuple(sorted(values, key=_DIRECTION_ORDER.__getitem__))

    @field_validator("edge_types")
    @classmethod
    def canonicalize_edge_types(
        cls, values: tuple[CodeRelationType, ...]
    ) -> tuple[CodeRelationType, ...]:
        if len(values) != len(set(values)):
            raise ValueError("edge_types must not contain duplicates")
        return tuple(sorted(values, key=_RELATION_ORDER.__getitem__))

    @model_validator(mode="after")
    def validate_profile(self) -> CodeQueryProfile:
        graph_budget_active = any(
            (
                self.budget.graph_candidates,
                self.budget.graph_node_budget,
                self.budget.graph_edge_budget,
            )
        )
        if self.max_hops == 0:
            if self.directions or self.edge_types or graph_budget_active:
                raise ValueError(
                    "zero-hop profiles require empty graph filters and zero graph budgets"
                )
        else:
            if not self.directions or not self.edge_types:
                raise ValueError("positive-hop profiles require directions and typed edge filters")
            if not graph_budget_active:
                raise ValueError("positive-hop profiles require active graph budgets")
        if self.require_tests and self.budget.test_candidates == 0:
            raise ValueError("require_tests needs a non-zero test candidate budget")
        if self.include_history and self.budget.history_candidates == 0:
            raise ValueError("include_history needs a non-zero history candidate budget")
        return self


class CodeRelationNode(_ContractModel):
    """One node with complete repository, version, generation, locator, and ACL provenance."""

    entity_id: ContractText
    repository_id: ContractText
    stable_version: ContractText
    source_generation: ContractText
    locator: ContractText
    acl_ref: ContractText

    def semantic_identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.entity_id,
            self.repository_id,
            self.stable_version,
            self.source_generation,
            self.acl_ref,
        )


class CodeRelationLocator(_ContractModel):
    """Stored-edge locator and the governed source endpoint that owns it."""

    locator: ContractText
    owner_entity_id: ContractText
    repository_id: ContractText
    stable_version: ContractText
    source_generation: ContractText
    acl_ref: ContractText


class CodeRelationHop(_ContractModel):
    """One typed stored edge with explicit provenance for both endpoints."""

    source_entity_id: ContractText
    target_entity_id: ContractText
    source_repository_id: ContractText
    target_repository_id: ContractText
    edge_type: CodeRelationType
    direction: CodeTraversalDirection
    hop: Annotated[StrictInt, Field(ge=1, le=4)]
    confidence: UnitFloat
    source_version: ContractText
    target_version: ContractText
    source_generation: ContractText
    target_generation: ContractText
    source_acl_ref: ContractText
    target_acl_ref: ContractText
    derivation: CodeDerivation
    review_status: CodeReviewStatus
    fact_status: CodeFactStatus
    locator: CodeRelationLocator
    edge_id: ContractText | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_transition(self) -> CodeRelationHop:
        lineage = self.edge_type in _LINEAGE_RELATION_TYPES
        source_state = (
            self.source_entity_id,
            self.source_version,
            self.source_generation,
        )
        target_state = (
            self.target_entity_id,
            self.target_version,
            self.target_generation,
        )
        if source_state == target_state:
            raise ValueError("relation hop endpoint states must be different")
        if self.source_repository_id != self.target_repository_id:
            raise ValueError("relation hop endpoints must belong to the same repository")
        if self.source_acl_ref != self.target_acl_ref:
            raise ValueError("relation hop endpoints must share the authorized ACL boundary")
        locator_owner = (
            self.locator.owner_entity_id,
            self.locator.repository_id,
            self.locator.stable_version,
            self.locator.source_generation,
            self.locator.acl_ref,
        )
        stored_source = (
            self.source_entity_id,
            self.source_repository_id,
            self.source_version,
            self.source_generation,
            self.source_acl_ref,
        )
        if locator_owner != stored_source:
            raise ValueError("relation locator owner must match the stored source endpoint")
        if lineage:
            if self.source_version == self.target_version:
                raise ValueError("lineage hops require an explicit version transition")
        else:
            if self.source_version != self.target_version:
                raise ValueError("ordinary relation hops cannot cross stable versions")
            if self.source_generation != self.target_generation:
                raise ValueError("ordinary relation hops cannot cross source generations")
            if self.source_entity_id == self.target_entity_id:
                raise ValueError("ordinary relation hop endpoints must be different")
        return self


class CodeRelationPath(_ContractModel):
    """Traversal-ordered governed nodes and their typed, directed stored edges."""

    nodes: tuple[CodeRelationNode, ...] = Field(min_length=1, max_length=5)
    edges: tuple[CodeRelationHop, ...] = Field(default=(), max_length=4)
    path_score: NonNegativeFloat = 0.0

    @model_validator(mode="after")
    def validate_path(self) -> CodeRelationPath:
        if len(self.edges) != len(self.nodes) - 1:
            raise ValueError("relation path must have exactly one edge per adjacent node pair")

        edge_ids = tuple(edge.edge_id for edge in self.edges)
        identified = tuple(edge_id for edge_id in edge_ids if edge_id is not None)
        if identified and len(identified) != len(edge_ids):
            raise ValueError("relation path edges must be either all identified or all legacy")
        if len(identified) != len(set(identified)):
            raise ValueError("relation path cannot repeat a stored edge identity")

        semantic_nodes = [node.semantic_identity() for node in self.nodes]
        if len(semantic_nodes) != len(set(semantic_nodes)):
            raise ValueError("relation paths cannot repeat the same governed node state")

        for index, edge in enumerate(self.edges):
            if edge.hop != index + 1:
                raise ValueError("relation path hop numbers must be contiguous and one-based")
            left, right = self.nodes[index : index + 2]
            stored_source, stored_target = (
                (left, right)
                if edge.direction is CodeTraversalDirection.OUTGOING
                else (right, left)
            )
            expected_source = (
                stored_source.entity_id,
                stored_source.repository_id,
                stored_source.stable_version,
                stored_source.source_generation,
                stored_source.acl_ref,
            )
            expected_target = (
                stored_target.entity_id,
                stored_target.repository_id,
                stored_target.stable_version,
                stored_target.source_generation,
                stored_target.acl_ref,
            )
            actual_source = (
                edge.source_entity_id,
                edge.source_repository_id,
                edge.source_version,
                edge.source_generation,
                edge.source_acl_ref,
            )
            actual_target = (
                edge.target_entity_id,
                edge.target_repository_id,
                edge.target_version,
                edge.target_generation,
                edge.target_acl_ref,
            )
            if actual_source != expected_source or actual_target != expected_target:
                raise ValueError(
                    "relation edge provenance does not match governed nodes and direction"
                )
            path_locator_owner = (
                edge.locator.owner_entity_id,
                edge.locator.repository_id,
                edge.locator.stable_version,
                edge.locator.source_generation,
                edge.locator.acl_ref,
            )
            if path_locator_owner != expected_source:
                raise ValueError(
                    "relation locator owner does not match the path stored source node"
                )

        positions_by_entity: dict[str, list[int]] = {}
        for index, node in enumerate(self.nodes):
            positions_by_entity.setdefault(node.entity_id, []).append(index)
        for positions in positions_by_entity.values():
            for start, end in zip(positions, positions[1:], strict=False):
                if any(
                    edge.edge_type not in _LINEAGE_RELATION_TYPES for edge in self.edges[start:end]
                ):
                    raise ValueError(
                        "repeated entity IDs require an all-lineage version transition"
                    )
        return self


class CodeChannelScore(_ContractModel):
    """Raw score from exactly one Code retrieval channel."""

    channel: CodeRetrievalChannel
    score: NonNegativeFloat


class CodeChannelRank(_ContractModel):
    """Zero-based raw rank from exactly one Code retrieval channel."""

    channel: CodeRetrievalChannel
    rank: NonNegativeInt


class CodeCalibratedScore(_ContractModel):
    """A relevance probability backed by a named calibration artifact."""

    status: Literal[CodeCalibrationStatus.CALIBRATED] = CodeCalibrationStatus.CALIBRATED
    score: UnitFloat
    calibration_version: ContractText


class CodeUncalibratedScore(_ContractModel):
    """An explicit non-calibrated state; no null or sentinel score is emitted."""

    status: Literal[CodeCalibrationStatus.DISABLED, CodeCalibrationStatus.UNAVAILABLE]
    reason: ContractText


CodeCalibratedRelevance = Annotated[
    CodeCalibratedScore | CodeUncalibratedScore,
    Field(discriminator="status"),
]


class CodeRetrievalCandidate(_ContractModel):
    """One governed Code candidate before cross-source normalization."""

    entity_id: ContractText
    retrieval_unit_id: ContractText
    repository_id: ContractText
    entity_type: ContractText
    stable_version: ContractText
    source_generation: ContractText
    raw_channel_scores: tuple[CodeChannelScore, ...] = Field(min_length=1)
    raw_channel_ranks: tuple[CodeChannelRank, ...] = Field(min_length=1)
    within_source_rank: NonNegativeInt = Field(
        description="Unique contiguous zero-based source rank; ties are prohibited.",
    )
    source_fused_score: NonNegativeFloat
    calibrated_relevance: CodeCalibratedRelevance
    version_alignment: CodeVersionAlignment
    fact_status: CodeFactStatus
    derivation: CodeDerivation
    review_status: CodeReviewStatus
    role: CodeCandidateRole
    relation_path: CodeRelationPath
    locator: ContractText
    token_estimate: NonNegativeInt
    acl_ref: ContractText

    @field_validator("raw_channel_scores")
    @classmethod
    def canonicalize_raw_scores(
        cls, entries: tuple[CodeChannelScore, ...]
    ) -> tuple[CodeChannelScore, ...]:
        channels = [entry.channel for entry in entries]
        if len(channels) != len(set(channels)):
            raise ValueError("raw channel scores must not repeat a channel")
        return tuple(sorted(entries, key=lambda entry: _CHANNEL_ORDER[entry.channel]))

    @field_validator("raw_channel_ranks")
    @classmethod
    def canonicalize_raw_ranks(
        cls, entries: tuple[CodeChannelRank, ...]
    ) -> tuple[CodeChannelRank, ...]:
        channels = [entry.channel for entry in entries]
        if len(channels) != len(set(channels)):
            raise ValueError("raw channel ranks must not repeat a channel")
        return tuple(sorted(entries, key=lambda entry: _CHANNEL_ORDER[entry.channel]))

    @model_validator(mode="after")
    def validate_candidate(self) -> CodeRetrievalCandidate:
        if self.entity_id == self.retrieval_unit_id:
            raise ValueError("stable entity_id and rebuildable retrieval_unit_id must differ")
        score_channels = {entry.channel for entry in self.raw_channel_scores}
        rank_channels = {entry.channel for entry in self.raw_channel_ranks}
        if score_channels != rank_channels:
            raise ValueError("raw channel scores and ranks must cover exactly the same channels")
        terminal = self.relation_path.nodes[-1]
        candidate_provenance = (
            self.entity_id,
            self.repository_id,
            self.stable_version,
            self.source_generation,
            self.locator,
            self.acl_ref,
        )
        terminal_provenance = (
            terminal.entity_id,
            terminal.repository_id,
            terminal.stable_version,
            terminal.source_generation,
            terminal.locator,
            terminal.acl_ref,
        )
        if candidate_provenance != terminal_provenance:
            raise ValueError(
                "candidate entity/repository/version/generation/locator/ACL "
                "must match terminal node"
            )
        return self


class CodeContextBlock(_ContractModel):
    """A bounded Code rendering block with candidate-identical provenance."""

    block_id: ContractText
    entity_id: ContractText
    retrieval_unit_id: ContractText
    repository_id: ContractText
    role: CodeCandidateRole
    content: ContextContent
    stable_version: ContractText
    source_generation: ContractText
    relation_path: CodeRelationPath
    locator: ContractText
    token_estimate: NonNegativeInt
    acl_ref: ContractText

    @model_validator(mode="after")
    def validate_context_identity(self) -> CodeContextBlock:
        if self.entity_id == self.retrieval_unit_id:
            raise ValueError("context entity_id and retrieval_unit_id must differ")
        terminal = self.relation_path.nodes[-1]
        context_provenance = (
            self.entity_id,
            self.repository_id,
            self.stable_version,
            self.source_generation,
            self.locator,
            self.acl_ref,
        )
        terminal_provenance = (
            terminal.entity_id,
            terminal.repository_id,
            terminal.stable_version,
            terminal.source_generation,
            terminal.locator,
            terminal.acl_ref,
        )
        if context_provenance != terminal_provenance:
            raise ValueError(
                "context entity/repository/version/generation/locator/ACL must match terminal node"
            )
        return self

    def semantic_duplicate_key(self) -> tuple[str, ...]:
        return (
            self.entity_id,
            self.retrieval_unit_id,
            self.repository_id,
            self.role.value,
            self.stable_version,
            self.source_generation,
            self.relation_path.canonical_sha256(),
            self.locator,
            self.acl_ref,
        )


class _CodeChannelOutcomeBase(_ContractModel):
    channel: CodeRetrievalChannel


class CodeChannelDisabled(_CodeChannelOutcomeBase):
    status: Literal[CodeChannelOutcomeStatus.DISABLED] = CodeChannelOutcomeStatus.DISABLED
    reason: ContractText


class CodeChannelUnavailable(_CodeChannelOutcomeBase):
    status: Literal[CodeChannelOutcomeStatus.UNAVAILABLE] = CodeChannelOutcomeStatus.UNAVAILABLE
    error: ContractText


class CodeChannelTimeout(_CodeChannelOutcomeBase):
    status: Literal[CodeChannelOutcomeStatus.TIMEOUT] = CodeChannelOutcomeStatus.TIMEOUT
    error: ContractText


class CodeChannelError(_CodeChannelOutcomeBase):
    status: Literal[CodeChannelOutcomeStatus.ERROR] = CodeChannelOutcomeStatus.ERROR
    error: ContractText


class CodeChannelCompleteNoMatch(_CodeChannelOutcomeBase):
    status: Literal[CodeChannelOutcomeStatus.COMPLETE_NO_MATCH] = (
        CodeChannelOutcomeStatus.COMPLETE_NO_MATCH
    )


class CodeChannelCompletePruned(_CodeChannelOutcomeBase):
    """A completed channel whose positive hit set was fully pruned from the response."""

    status: Literal[CodeChannelOutcomeStatus.COMPLETE_PRUNED] = (
        CodeChannelOutcomeStatus.COMPLETE_PRUNED
    )
    hit_count: PositiveInt


class CodeChannelCompleteWithHits(_CodeChannelOutcomeBase):
    """A completed channel and its total hit count before candidate truncation."""

    status: Literal[CodeChannelOutcomeStatus.COMPLETE_WITH_HITS] = (
        CodeChannelOutcomeStatus.COMPLETE_WITH_HITS
    )
    hit_count: PositiveInt


CodeChannelOutcome = Annotated[
    CodeChannelDisabled
    | CodeChannelUnavailable
    | CodeChannelTimeout
    | CodeChannelError
    | CodeChannelCompleteNoMatch
    | CodeChannelCompletePruned
    | CodeChannelCompleteWithHits,
    Field(discriminator="status"),
]


class CodeChannelFailure(_ContractModel):
    scope: Literal["channel"] = "channel"
    channel: CodeRetrievalChannel
    kind: CodeChannelErrorKind
    message: ContractText


class CodeFallbackFailure(_ContractModel):
    scope: Literal["fallback"] = "fallback"
    engine: ContractText
    message: ContractText


CodeSourceError = Annotated[
    CodeChannelFailure | CodeFallbackFailure,
    Field(discriminator="scope"),
]


class CodeFallbackNotUsed(_ContractModel):
    status: Literal[CodeFallbackStatus.NOT_USED] = CodeFallbackStatus.NOT_USED


class CodeFallbackSucceeded(_ContractModel):
    status: Literal[CodeFallbackStatus.SUCCEEDED] = CodeFallbackStatus.SUCCEEDED
    engine: ContractText
    reason: ContractText


class CodeFallbackFailed(_ContractModel):
    status: Literal[CodeFallbackStatus.FAILED] = CodeFallbackStatus.FAILED
    engine: ContractText
    reason: ContractText


CodeFallback = Annotated[
    CodeFallbackNotUsed | CodeFallbackSucceeded | CodeFallbackFailed,
    Field(discriminator="status"),
]


def _error_sort_key(error: CodeChannelFailure | CodeFallbackFailure) -> tuple[int, int, int, str]:
    if isinstance(error, CodeChannelFailure):
        return (
            0,
            _CHANNEL_ORDER[error.channel],
            _ERROR_KIND_ORDER[error.kind],
            error.message,
        )
    return (1, 0, 0, f"{error.engine}\0{error.message}")


class CodeSourceResult(_ContractModel):
    """A governed result whose candidates are canonicalized by unique source rank."""

    query_id: ContractText
    status: CodeSourceStatus
    channel_outcomes: tuple[CodeChannelOutcome, ...] = Field(
        min_length=len(CodeRetrievalChannel),
        max_length=len(CodeRetrievalChannel),
    )
    candidates: tuple[CodeRetrievalCandidate, ...] = ()
    context_blocks: tuple[CodeContextBlock, ...] = ()
    index_version: ContractText
    watermark: ContractText
    latency_ms: NonNegativeFloat
    errors: tuple[CodeSourceError, ...] = ()
    fallback: CodeFallback = Field(default_factory=CodeFallbackNotUsed)

    @field_validator("channel_outcomes")
    @classmethod
    def canonicalize_channel_outcomes(
        cls, outcomes: tuple[CodeChannelOutcome, ...]
    ) -> tuple[CodeChannelOutcome, ...]:
        channels = [outcome.channel for outcome in outcomes]
        if len(channels) != len(set(channels)):
            raise ValueError("channel outcomes must not repeat a channel")
        if set(channels) != set(CodeRetrievalChannel):
            raise ValueError("channel outcomes must cover every registered channel exactly once")
        return tuple(sorted(outcomes, key=lambda outcome: _CHANNEL_ORDER[outcome.channel]))

    @field_validator("candidates")
    @classmethod
    def canonicalize_candidates(
        cls,
        candidates: tuple[CodeRetrievalCandidate, ...],
    ) -> tuple[CodeRetrievalCandidate, ...]:
        ranks = [candidate.within_source_rank for candidate in candidates]
        if len(ranks) != len(set(ranks)):
            raise ValueError("within_source_rank must be unique")
        if sorted(ranks) != list(range(len(candidates))):
            raise ValueError("within_source_rank must be zero-based and contiguous")
        return tuple(sorted(candidates, key=lambda candidate: candidate.within_source_rank))

    @field_validator("errors")
    @classmethod
    def canonicalize_errors(
        cls, errors: tuple[CodeSourceError, ...]
    ) -> tuple[CodeSourceError, ...]:
        canonical = tuple(sorted(errors, key=_error_sort_key))
        keys = [error.canonical_json_bytes() for error in canonical]
        if len(keys) != len(set(keys)):
            raise ValueError("source errors must not contain semantic duplicates")
        return canonical

    @model_validator(mode="after")
    def validate_result(self) -> CodeSourceResult:
        completed = tuple(
            outcome
            for outcome in self.channel_outcomes
            if isinstance(
                outcome,
                (
                    CodeChannelCompleteNoMatch,
                    CodeChannelCompletePruned,
                    CodeChannelCompleteWithHits,
                ),
            )
        )
        failures = tuple(
            outcome
            for outcome in self.channel_outcomes
            if isinstance(
                outcome,
                (CodeChannelUnavailable, CodeChannelTimeout, CodeChannelError),
            )
        )
        timeouts = tuple(
            outcome for outcome in self.channel_outcomes if isinstance(outcome, CodeChannelTimeout)
        )
        if not completed and not failures:
            raise ValueError("at least one channel must run or fail explicitly")

        expected_errors: list[CodeChannelFailure | CodeFallbackFailure] = []
        for outcome in failures:
            kind = CodeChannelErrorKind(outcome.status.value)
            expected_errors.append(
                CodeChannelFailure(
                    channel=outcome.channel,
                    kind=kind,
                    message=outcome.error,
                )
            )
        if isinstance(self.fallback, CodeFallbackFailed):
            expected_errors.append(
                CodeFallbackFailure(
                    engine=self.fallback.engine,
                    message=self.fallback.reason,
                )
            )
        if tuple(sorted(expected_errors, key=_error_sort_key)) != self.errors:
            raise ValueError("errors must exactly mirror failed channels and failed fallback")

        if isinstance(self.fallback, CodeFallbackSucceeded):
            if self.status is not CodeSourceStatus.COMPLETE:
                raise ValueError("successful fallback requires complete source status")
        elif (
            isinstance(self.fallback, CodeFallbackFailed)
            and self.status is CodeSourceStatus.COMPLETE
        ):
            raise ValueError("failed fallback cannot accompany complete source status")

        if self.status is CodeSourceStatus.COMPLETE:
            if failures or not completed:
                raise ValueError("complete status requires completed channels and no failures")
        elif self.status is CodeSourceStatus.PARTIAL:
            if not completed or not failures:
                raise ValueError("partial status requires completed and failed channels")
            if isinstance(self.fallback, CodeFallbackSucceeded):
                raise ValueError("partial status cannot accompany successful fallback")
        elif self.status is CodeSourceStatus.TIMEOUT:
            if completed or not timeouts:
                raise ValueError("timeout status requires no completed channel and a timeout")
            if self.candidates or self.context_blocks:
                raise ValueError("timeout status cannot carry candidates or context")
            if isinstance(self.fallback, CodeFallbackSucceeded):
                raise ValueError("timeout status cannot accompany successful fallback")
        elif self.status is CodeSourceStatus.UNAVAILABLE:
            if completed or not failures or timeouts:
                raise ValueError(
                    "unavailable status requires non-timeout failures and no completed channel"
                )
            if self.candidates or self.context_blocks:
                raise ValueError("unavailable status cannot carry candidates or context")
            if isinstance(self.fallback, CodeFallbackSucceeded):
                raise ValueError("unavailable status cannot accompany successful fallback")

        candidate_keys = [
            (candidate.entity_id, candidate.retrieval_unit_id) for candidate in self.candidates
        ]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise ValueError("candidate entity/unit pairs must be unique")

        outcomes_by_channel = {outcome.channel: outcome for outcome in self.channel_outcomes}
        observed_hits = {channel: 0 for channel in CodeRetrievalChannel}
        observed_ranks = {channel: set() for channel in CodeRetrievalChannel}
        for candidate in self.candidates:
            ranks_by_channel = {rank.channel: rank.rank for rank in candidate.raw_channel_ranks}
            for score in candidate.raw_channel_scores:
                channel = score.channel
                outcome = outcomes_by_channel[channel]
                if not isinstance(outcome, CodeChannelCompleteWithHits):
                    raise ValueError("candidate raw channels require complete_with_hits outcomes")
                raw_rank = ranks_by_channel[channel]
                if raw_rank >= outcome.hit_count:
                    raise ValueError("zero-based raw channel rank must be below channel hit_count")
                if raw_rank in observed_ranks[channel]:
                    raise ValueError(
                        "returned candidate raw ranks must be unique within each channel"
                    )
                observed_ranks[channel].add(raw_rank)
                observed_hits[channel] += 1
        for outcome in self.channel_outcomes:
            if isinstance(outcome, CodeChannelCompleteWithHits):
                if observed_hits[outcome.channel] == 0:
                    raise ValueError(
                        "complete_with_hits outcome requires a returned channel candidate"
                    )
                if outcome.hit_count < observed_hits[outcome.channel]:
                    raise ValueError("channel hit_count cannot be below returned candidate count")
            elif isinstance(outcome, CodeChannelCompletePruned):
                if observed_hits[outcome.channel] != 0:
                    raise ValueError("complete_pruned outcome cannot carry a returned candidate")

        candidates_by_key = {
            (candidate.entity_id, candidate.retrieval_unit_id): candidate
            for candidate in self.candidates
        }
        if not self.candidates and self.context_blocks:
            raise ValueError("results without candidates cannot carry context")
        block_ids = [block.block_id for block in self.context_blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("context block IDs must be unique")
        semantic_context_keys = [block.semantic_duplicate_key() for block in self.context_blocks]
        if len(semantic_context_keys) != len(set(semantic_context_keys)):
            raise ValueError("context blocks must not contain semantic duplicates")
        for block in self.context_blocks:
            candidate = candidates_by_key.get((block.entity_id, block.retrieval_unit_id))
            if candidate is None:
                raise ValueError("every context block must reference a returned candidate")
            candidate_provenance = (
                candidate.repository_id,
                candidate.role,
                candidate.stable_version,
                candidate.source_generation,
                candidate.relation_path,
                candidate.locator,
                candidate.acl_ref,
            )
            block_provenance = (
                block.repository_id,
                block.role,
                block.stable_version,
                block.source_generation,
                block.relation_path,
                block.locator,
                block.acl_ref,
            )
            if candidate_provenance != block_provenance:
                raise ValueError(
                    "context repository/role/version/generation/path/locator/ACL "
                    "conflicts with candidate"
                )
        return self
