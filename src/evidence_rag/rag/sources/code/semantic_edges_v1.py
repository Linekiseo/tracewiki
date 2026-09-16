"""Frozen Python semantic-edge treatment over SCIP and conservative parser edges.

This module is deliberately a pure transformation boundary.  It does not read
or write a database, publish a graph, run an indexer, or route retrieval.  The
output is suitable for a later governed C4 registry/store adapter after that
adapter has repeated its own scope checks.

SCIP's standard occurrence roles do not define a call or override bit.  Two
explicit extension bits are therefore reserved here.  Ordinary definitions,
reads, writes, tests, and relationship references are never interpreted as
runtime calls or overrides.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import IntFlag, StrEnum
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from .contracts import (
    CodeDerivation,
    CodeFactStatus,
    CodeRelationType,
    CodeReviewStatus,
)
from .graph_v2 import (
    CodeEdgeDerivationLayer,
    CodeGraphEntityType,
    get_edge_spec,
    validate_edge_assertion,
)
from .scip_v1 import (
    ScipConsumeResult,
    ScipLink,
    ScipLinkedOccurrence,
    ScipLinkedRelationship,
    ScipLinkStatus,
    ScipOccurrenceKind,
    ScipStatus,
)

SEMANTIC_EDGE_TREATMENT_VERSION = "c5-python-semantic-edges-v1"
SEMANTIC_EDGE_LABEL_PROTOCOL_VERSION = "c5-semantic-edge-labels-v1"
DEFAULT_SEMANTIC_EDGE_SAMPLE_SIZE = 200
DEFAULT_MIN_SEMANTIC_EDGE_CONFIDENCE = 0.8


class ScipSemanticRole(IntFlag):
    """Non-standard roles accepted only when an indexer explicitly emits them."""

    OVERRIDE = 1 << 29
    CALL = 1 << 30


SCIP_EXPLICIT_CALL_ROLE = int(ScipSemanticRole.CALL)
SCIP_EXPLICIT_OVERRIDE_ROLE = int(ScipSemanticRole.OVERRIDE)


class SemanticEdgeSupportStatus(StrEnum):
    """Whether this treatment version supports a requested language slice."""

    SUPPORTED = "supported"
    UNAVAILABLE = "unavailable"


class SemanticEdgeTreatmentStatus(StrEnum):
    """Overall transformation outcome without implying graph publication."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SemanticScipStatus(StrEnum):
    """Truthful SCIP availability retained in the fallback trace."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SemanticEdgeDiagnosticCode(StrEnum):
    """Closed diagnostic reasons used by treatment and evaluation."""

    LANGUAGE_UNAVAILABLE = "language_unavailable"
    SCIP_UNAVAILABLE = "scip_unavailable"
    SCIP_PARTIAL = "scip_partial"
    SCIP_PROVENANCE_MISSING = "scip_provenance_missing"
    LINK_UNRESOLVED = "link_unresolved"
    LINK_AMBIGUOUS = "link_ambiguous"
    LINK_EXTERNAL = "link_external"
    LINK_LOCAL = "link_local"
    LINK_SCOPE_MISMATCH = "link_scope_mismatch"
    LINK_RESOLVER_UNAVAILABLE = "link_resolver_unavailable"
    ENDPOINT_TYPE_REJECTED = "endpoint_type_rejected"
    ENDPOINT_SCOPE_MISMATCH = "endpoint_scope_mismatch"
    ENDPOINT_VERSION_MISMATCH = "endpoint_version_mismatch"
    ENDPOINT_LOCATOR_REJECTED = "endpoint_locator_rejected"
    EDGE_REGISTRY_REJECTED = "edge_registry_rejected"
    EDGE_SELF_REFERENCE_REJECTED = "edge_self_reference_rejected"
    LOW_CONFIDENCE = "low_confidence"
    CALL_EVIDENCE_MISSING = "call_evidence_missing"
    CALL_EVIDENCE_AMBIGUOUS = "call_evidence_ambiguous"
    RELATIONSHIP_EVIDENCE_MISSING = "relationship_evidence_missing"
    RELATIONSHIP_EVIDENCE_AMBIGUOUS = "relationship_evidence_ambiguous"
    OVERRIDE_EVIDENCE_MISSING = "override_evidence_missing"
    TREE_SITTER_SCIP_CONFLICT = "tree_sitter_scip_conflict"


class SemanticEdgeDiagnosticSeverity(StrEnum):
    """Diagnostic severity; errors are fail-closed for the affected edge only."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class SemanticEdgePrecisionVerdict(StrEnum):
    """Manual precision labels; ``uncertain`` never counts as a correct edge."""

    CORRECT = "correct"
    INCORRECT = "incorrect"
    GRAPH_NOISE = "graph_noise"
    UNCERTAIN = "uncertain"


class SemanticEdgeEvaluationStatus(StrEnum):
    """Whether the precision labeling gate has enough real human evidence."""

    AVAILABLE = "available"
    PROVISIONAL = "provisional"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SemanticEdgeScope:
    """Exact scope required for every endpoint and evidence transformation."""

    project_id: str
    repository_id: str
    generation_id: str
    stable_version: str
    acl_ref: str

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "generation_id",
            "stable_version",
            "acl_ref",
        ):
            _require_text(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class SemanticEdgeEndpoint:
    """One fully governed graph endpoint; local/external symbols are inexpressible."""

    entity_id: str
    entity_type: CodeGraphEntityType
    project_id: str
    repository_id: str
    generation_id: str
    stable_version: str
    acl_ref: str
    locator: str

    def __post_init__(self) -> None:
        for name in (
            "entity_id",
            "project_id",
            "repository_id",
            "generation_id",
            "stable_version",
            "acl_ref",
        ):
            _require_text(name, getattr(self, name))
        try:
            entity_type = CodeGraphEntityType(self.entity_type)
        except (TypeError, ValueError) as error:
            raise ValueError("endpoint entity_type is not registered") from error
        object.__setattr__(self, "entity_type", entity_type)
        _require_locator("locator", self.locator)

    def scope_identity(self) -> tuple[str, str, str, str, str]:
        return (
            self.project_id,
            self.repository_id,
            self.generation_id,
            self.stable_version,
            self.acl_ref,
        )


@dataclass(frozen=True, slots=True)
class SemanticEdgeProvenance:
    """One independent resolver/parser lineage retained without confidence inflation."""

    derivation: CodeDerivation
    producer_version: str
    artifact_id: str = ""
    content_sha256: str = ""

    def __post_init__(self) -> None:
        try:
            derivation = CodeDerivation(self.derivation)
        except (TypeError, ValueError) as error:
            raise ValueError("semantic edge provenance has an unknown derivation") from error
        if derivation not in {CodeDerivation.SCIP, CodeDerivation.TREE_SITTER}:
            raise ValueError("semantic edge provenance must be SCIP or Tree-sitter")
        object.__setattr__(self, "derivation", derivation)
        _require_text("producer_version", self.producer_version)
        if self.artifact_id:
            _require_text("artifact_id", self.artifact_id)
        if self.content_sha256 and not _is_sha256(self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class TreeSitterConservativeEdge:
    """Existing conservative parser edge accepted by the pure merge boundary."""

    edge_type: CodeRelationType
    source: SemanticEdgeEndpoint
    target: SemanticEdgeEndpoint
    confidence: float
    evidence_locators: tuple[str, ...]
    parser_version: str
    resolver_version: str
    review_status: CodeReviewStatus = CodeReviewStatus.MACHINE_CONFIRMED
    fact_status: CodeFactStatus = CodeFactStatus.MACHINE_CONFIRMED

    def __post_init__(self) -> None:
        try:
            edge_type = CodeRelationType(self.edge_type)
            review_status = CodeReviewStatus(self.review_status)
            fact_status = CodeFactStatus(self.fact_status)
        except (TypeError, ValueError) as error:
            raise ValueError("conservative edge contains an unknown typed value") from error
        object.__setattr__(self, "edge_type", edge_type)
        object.__setattr__(self, "review_status", review_status)
        object.__setattr__(self, "fact_status", fact_status)
        object.__setattr__(
            self,
            "confidence",
            _unit_float("confidence", self.confidence),
        )
        _require_text("parser_version", self.parser_version)
        _require_text("resolver_version", self.resolver_version)
        object.__setattr__(
            self,
            "evidence_locators",
            _canonical_locators(self.evidence_locators),
        )


ConservativeSemanticEdge = TreeSitterConservativeEdge


@dataclass(frozen=True, slots=True)
class SemanticEdge:
    """Canonical edge ready for a later C4 persistence/traversal adapter."""

    edge_id: str
    canonical_hash: str
    edge_type: CodeRelationType
    source: SemanticEdgeEndpoint
    target: SemanticEdgeEndpoint
    confidence: float
    confidence_semantics: str
    derivation_layer: CodeEdgeDerivationLayer
    provenances: tuple[SemanticEdgeProvenance, ...]
    evidence_locators: tuple[str, ...]
    review_status: CodeReviewStatus
    fact_status: CodeFactStatus

    def __post_init__(self) -> None:
        try:
            edge_type = CodeRelationType(self.edge_type)
            layer = CodeEdgeDerivationLayer(self.derivation_layer)
            review_status = CodeReviewStatus(self.review_status)
            fact_status = CodeFactStatus(self.fact_status)
        except (TypeError, ValueError) as error:
            raise ValueError("semantic edge contains an unknown typed value") from error
        object.__setattr__(self, "edge_type", edge_type)
        object.__setattr__(self, "derivation_layer", layer)
        object.__setattr__(self, "review_status", review_status)
        object.__setattr__(self, "fact_status", fact_status)
        object.__setattr__(
            self,
            "confidence",
            _unit_float("confidence", self.confidence),
        )
        if layer is not CodeEdgeDerivationLayer.STATIC:
            raise ValueError("C5 semantic edges must use the C4 static derivation layer")
        if review_status not in {
            CodeReviewStatus.MACHINE_CONFIRMED,
            CodeReviewStatus.HUMAN_CONFIRMED,
        }:
            raise ValueError("semantic edge must be machine- or human-confirmed")
        if fact_status not in {
            CodeFactStatus.MACHINE_CONFIRMED,
            CodeFactStatus.OBSERVED,
        }:
            raise ValueError("semantic edge fact_status is not materializable")
        if self.source.scope_identity() != self.target.scope_identity():
            raise ValueError("semantic edge endpoints must share exact governed scope")
        if _endpoint_state(self.source) == _endpoint_state(self.target):
            raise ValueError("semantic edge endpoint states must differ")
        spec = validate_edge_assertion(
            edge_type,
            source_type=self.source.entity_type,
            target_type=self.target.entity_type,
            derivation=layer,
        )
        if self.confidence_semantics != spec.confidence_meaning:
            raise ValueError("confidence_semantics must match the C4 edge registry")
        provenances = tuple(sorted(set(self.provenances), key=_provenance_key))
        if not provenances:
            raise ValueError("semantic edge requires provenance")
        if len(provenances) != len(self.provenances):
            raise ValueError("semantic edge provenances must be canonical and unique")
        if provenances != self.provenances:
            raise ValueError("semantic edge provenances are not canonically ordered")
        object.__setattr__(
            self,
            "evidence_locators",
            _canonical_locators(self.evidence_locators),
        )
        expected_hash = _canonical_edge_hash(edge_type, self.source, self.target)
        if self.canonical_hash != expected_hash:
            raise ValueError("semantic edge canonical_hash does not match its endpoints")
        if self.edge_id != f"code-edge-v1:{expected_hash}":
            raise ValueError("semantic edge edge_id does not match its canonical hash")

    @property
    def derivations(self) -> tuple[CodeDerivation, ...]:
        """Canonical producer set retained for downstream merge-aware adapters."""

        return tuple(item.derivation for item in self.provenances)


@dataclass(frozen=True, slots=True)
class SemanticEdgeDiagnostic:
    """One bounded fail-closed treatment diagnostic."""

    code: SemanticEdgeDiagnosticCode
    severity: SemanticEdgeDiagnosticSeverity
    message: str
    edge_type: CodeRelationType | None = None
    source_entity_id: str = ""
    target_entity_id: str = ""
    relative_path: str = ""
    symbol: str = ""
    candidate_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            code = SemanticEdgeDiagnosticCode(self.code)
            severity = SemanticEdgeDiagnosticSeverity(self.severity)
            edge_type = CodeRelationType(self.edge_type) if self.edge_type is not None else None
        except (TypeError, ValueError) as error:
            raise ValueError("semantic edge diagnostic contains an unknown typed value") from error
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "edge_type", edge_type)
        _require_text("message", self.message)
        for name in (
            "source_entity_id",
            "target_entity_id",
            "relative_path",
            "symbol",
        ):
            value = getattr(self, name)
            if value:
                _require_text(name, value)
        candidates = tuple(sorted(set(self.candidate_ids)))
        if candidates != self.candidate_ids:
            raise ValueError("diagnostic candidate_ids must be canonical and unique")
        for candidate in candidates:
            _require_text("candidate_id", candidate)


@dataclass(frozen=True, slots=True)
class SemanticEdgeDiagnosticCount:
    """Exact count retained even when detail diagnostics are sampled."""

    code: SemanticEdgeDiagnosticCode
    count: int

    def __post_init__(self) -> None:
        try:
            code = SemanticEdgeDiagnosticCode(self.code)
        except (TypeError, ValueError) as error:
            raise ValueError("unknown diagnostic count code") from error
        object.__setattr__(self, "code", code)
        _non_negative_int("count", self.count)
        if self.count == 0:
            raise ValueError("diagnostic count must be positive")


@dataclass(frozen=True, slots=True)
class SemanticEdgeConflict:
    """Frozen conflict that preserves Tree-sitter and suppresses the SCIP challenger."""

    conflict_id: str
    source_entity_id: str
    scip_target_entity_id: str
    scip_edge_type: CodeRelationType
    retained_tree_edge_ids: tuple[str, ...]
    retained_tree_edge_types: tuple[CodeRelationType, ...]
    evidence_locators: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        _require_text("conflict_id", self.conflict_id)
        _require_text("source_entity_id", self.source_entity_id)
        _require_text("scip_target_entity_id", self.scip_target_entity_id)
        _require_text("reason", self.reason)
        try:
            scip_edge_type = CodeRelationType(self.scip_edge_type)
            tree_types = tuple(CodeRelationType(value) for value in self.retained_tree_edge_types)
        except (TypeError, ValueError) as error:
            raise ValueError("conflict contains an unregistered edge type") from error
        object.__setattr__(self, "scip_edge_type", scip_edge_type)
        if tuple(sorted(set(self.retained_tree_edge_ids))) != self.retained_tree_edge_ids:
            raise ValueError("retained_tree_edge_ids must be canonical and unique")
        canonical_types = tuple(sorted(set(tree_types), key=lambda value: value.value))
        if canonical_types != tree_types:
            raise ValueError("retained_tree_edge_types must be canonical and unique")
        object.__setattr__(self, "retained_tree_edge_types", tree_types)
        object.__setattr__(
            self,
            "evidence_locators",
            _canonical_locators(self.evidence_locators),
        )


@dataclass(frozen=True, slots=True)
class SemanticEdgeFallbackTrace:
    """SCIP/fallback truth that never claims an unavailable semantic index succeeded."""

    scip_status: SemanticScipStatus
    scip_diagnostic_codes: tuple[str, ...]
    tree_sitter_input_count: int
    tree_sitter_retained_count: int
    scip_retained_count: int
    fallback_used: bool
    reason: str

    def __post_init__(self) -> None:
        try:
            scip_status = SemanticScipStatus(self.scip_status)
        except (TypeError, ValueError) as error:
            raise ValueError("fallback trace has an unknown SCIP status") from error
        object.__setattr__(self, "scip_status", scip_status)
        if tuple(sorted(set(self.scip_diagnostic_codes))) != self.scip_diagnostic_codes:
            raise ValueError("scip_diagnostic_codes must be canonical and unique")
        for code in self.scip_diagnostic_codes:
            _require_text("scip_diagnostic_code", code)
        for name in (
            "tree_sitter_input_count",
            "tree_sitter_retained_count",
            "scip_retained_count",
        ):
            _non_negative_int(name, getattr(self, name))
        if not isinstance(self.fallback_used, bool):
            raise TypeError("fallback_used must be a bool")
        _require_text("reason", self.reason)


@dataclass(frozen=True, slots=True)
class SemanticEdgeTreatmentResult:
    """Immutable, deterministic result of one language-scoped treatment."""

    status: SemanticEdgeTreatmentStatus
    support_status: SemanticEdgeSupportStatus
    language: str
    semantic_ready: bool
    edges: tuple[SemanticEdge, ...]
    diagnostics: tuple[SemanticEdgeDiagnostic, ...]
    diagnostic_counts: tuple[SemanticEdgeDiagnosticCount, ...]
    diagnostics_truncated: int
    conflicts: tuple[SemanticEdgeConflict, ...]
    trace: SemanticEdgeFallbackTrace
    treatment_version: str
    result_hash: str

    def __post_init__(self) -> None:
        try:
            status = SemanticEdgeTreatmentStatus(self.status)
            support = SemanticEdgeSupportStatus(self.support_status)
        except (TypeError, ValueError) as error:
            raise ValueError("treatment result contains an unknown status") from error
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "support_status", support)
        _require_text("language", self.language)
        _require_text("treatment_version", self.treatment_version)
        if not isinstance(self.semantic_ready, bool):
            raise TypeError("semantic_ready must be a bool")
        if tuple(sorted(self.edges, key=_edge_sort_key)) != self.edges:
            raise ValueError("semantic edges must be canonically ordered")
        if len({edge.edge_id for edge in self.edges}) != len(self.edges):
            raise ValueError("semantic edge ids must be unique")
        if tuple(sorted(self.diagnostics, key=_diagnostic_key)) != self.diagnostics:
            raise ValueError("semantic diagnostics must be canonically ordered")
        if tuple(sorted(self.conflicts, key=_conflict_key)) != self.conflicts:
            raise ValueError("semantic conflicts must be canonically ordered")
        if tuple(sorted(self.diagnostic_counts, key=lambda item: item.code.value)) != (
            self.diagnostic_counts
        ):
            raise ValueError("diagnostic counts must be canonically ordered")
        if len({item.code for item in self.diagnostic_counts}) != len(self.diagnostic_counts):
            raise ValueError("diagnostic count codes must be unique")
        _non_negative_int("diagnostics_truncated", self.diagnostics_truncated)
        if sum(item.count for item in self.diagnostic_counts) != (
            len(self.diagnostics) + self.diagnostics_truncated
        ):
            raise ValueError("diagnostic counts must include retained and truncated details")
        retained_scip = sum(
            any(item.derivation is CodeDerivation.SCIP for item in edge.provenances)
            for edge in self.edges
        )
        if self.semantic_ready != (retained_scip > 0):
            raise ValueError("semantic_ready requires at least one retained legal SCIP edge")
        if self.trace.scip_retained_count != retained_scip:
            raise ValueError("trace SCIP count must match retained SCIP edges")
        retained_tree = sum(
            any(item.derivation is CodeDerivation.TREE_SITTER for item in edge.provenances)
            for edge in self.edges
        )
        if self.trace.tree_sitter_retained_count != retained_tree:
            raise ValueError("trace Tree-sitter count must match retained Tree-sitter edges")
        if self.trace.tree_sitter_retained_count > self.trace.tree_sitter_input_count:
            raise ValueError("retained Tree-sitter edges cannot exceed the input count")
        if support is SemanticEdgeSupportStatus.UNAVAILABLE and self.edges:
            raise ValueError("unsupported language slices cannot emit semantic edges")
        if (status is SemanticEdgeTreatmentStatus.UNAVAILABLE) != (
            support is SemanticEdgeSupportStatus.UNAVAILABLE
        ):
            raise ValueError("language support and treatment unavailable status must agree")
        expected_hash = _result_hash(
            status=status,
            support=support,
            language=self.language,
            semantic_ready=self.semantic_ready,
            edges=self.edges,
            diagnostics=self.diagnostics,
            diagnostic_counts=self.diagnostic_counts,
            diagnostics_truncated=self.diagnostics_truncated,
            conflicts=self.conflicts,
            trace=self.trace,
            treatment_version=self.treatment_version,
        )
        if self.result_hash != expected_hash:
            raise ValueError("result_hash does not match the canonical treatment result")


@dataclass(frozen=True, slots=True)
class SemanticEdgePrecisionLabel:
    """One real reviewer label; the utility never manufactures these records."""

    edge_id: str
    verdict: SemanticEdgePrecisionVerdict
    reviewer: str
    protocol_version: str = SEMANTIC_EDGE_LABEL_PROTOCOL_VERSION
    note: str = ""

    def __post_init__(self) -> None:
        _require_text("edge_id", self.edge_id)
        _require_text("reviewer", self.reviewer)
        if self.protocol_version != SEMANTIC_EDGE_LABEL_PROTOCOL_VERSION:
            raise ValueError("precision label protocol_version is not supported")
        try:
            verdict = SemanticEdgePrecisionVerdict(self.verdict)
        except (TypeError, ValueError) as error:
            raise ValueError("precision label has an unknown verdict") from error
        object.__setattr__(self, "verdict", verdict)
        if self.note:
            _require_text("note", self.note)


@dataclass(frozen=True, slots=True)
class SemanticEdgeStructuralCounts:
    """Non-quality treatment counts retained even when no labels exist."""

    legal_edge_count: int
    conflict_count: int
    diagnostic_count: int
    eligible_edge_count: int | None
    unresolved_before: int | None
    unresolved_after: int | None
    classification: str = "non_quality_diagnostics"

    def __post_init__(self) -> None:
        for name in ("legal_edge_count", "conflict_count", "diagnostic_count"):
            _non_negative_int(name, getattr(self, name))
        if self.eligible_edge_count is not None:
            _non_negative_int("eligible_edge_count", self.eligible_edge_count)
            if self.eligible_edge_count < self.legal_edge_count:
                raise ValueError("eligible_edge_count cannot be smaller than legal_edge_count")
        if (self.unresolved_before is None) != (self.unresolved_after is None):
            raise ValueError("unresolved_before and unresolved_after must be provided together")
        if self.unresolved_before is not None and self.unresolved_after is not None:
            _non_negative_int("unresolved_before", self.unresolved_before)
            _non_negative_int("unresolved_after", self.unresolved_after)
        if self.classification != "non_quality_diagnostics":
            raise ValueError("structural counts cannot be classified as quality metrics")

    @property
    def unresolved_delta(self) -> int | None:
        """Raw after-minus-before count; negative means fewer unresolved items."""

        if self.unresolved_before is None or self.unresolved_after is None:
            return None
        return self.unresolved_after - self.unresolved_before


@dataclass(frozen=True, slots=True)
class SemanticEdgeQualityMetrics:
    """Label-dependent quality metrics; every value is absent without usable labels."""

    availability: SemanticEdgeEvaluationStatus
    edge_precision: float | None
    coverage: float | None
    unresolved_reduction_count: int | None
    unresolved_reduction_rate: float | None
    graph_noise_count: int | None
    graph_noise_rate: float | None
    harmful_count: int | None
    harmful_rate: float | None

    def __post_init__(self) -> None:
        try:
            availability = SemanticEdgeEvaluationStatus(self.availability)
        except (TypeError, ValueError) as error:
            raise ValueError("quality metrics have an unknown availability") from error
        object.__setattr__(self, "availability", availability)
        values = (
            self.edge_precision,
            self.coverage,
            self.unresolved_reduction_count,
            self.unresolved_reduction_rate,
            self.graph_noise_count,
            self.graph_noise_rate,
            self.harmful_count,
            self.harmful_rate,
        )
        if availability is SemanticEdgeEvaluationStatus.UNAVAILABLE:
            if any(value is not None for value in values):
                raise ValueError("unavailable quality metrics cannot publish values")
            return
        if self.edge_precision is None:
            raise ValueError("usable label evidence requires edge_precision")
        for value in (
            self.edge_precision,
            self.coverage,
            self.unresolved_reduction_rate,
            self.graph_noise_rate,
            self.harmful_rate,
        ):
            if value is not None and (not math.isfinite(value) or not 0.0 <= value <= 1.0):
                raise ValueError("quality rates must be finite unit floats")
        for name in (
            "unresolved_reduction_count",
            "graph_noise_count",
            "harmful_count",
        ):
            value = getattr(self, name)
            if value is not None:
                _non_negative_int(name, value)


@dataclass(frozen=True, slots=True)
class SemanticEdgeEvaluation:
    """Label protocol plus non-quality counts and gated quality metrics."""

    status: SemanticEdgeEvaluationStatus
    reason: str
    protocol_version: str
    structural_counts: SemanticEdgeStructuralCounts
    quality_metrics: SemanticEdgeQualityMetrics
    sample_size_target: int
    sample_size_required: int
    labeled_sample_count: int
    correct_label_count: int
    incorrect_label_count: int
    uncertain_label_count: int
    graph_noise_label_count: int
    sampled_edge_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            status = SemanticEdgeEvaluationStatus(self.status)
        except (TypeError, ValueError) as error:
            raise ValueError("evaluation has an unknown status") from error
        object.__setattr__(self, "status", status)
        _require_text("reason", self.reason)
        if self.protocol_version != SEMANTIC_EDGE_LABEL_PROTOCOL_VERSION:
            raise ValueError("evaluation protocol version is not supported")
        for name in (
            "sample_size_target",
            "sample_size_required",
            "labeled_sample_count",
            "correct_label_count",
            "incorrect_label_count",
            "uncertain_label_count",
            "graph_noise_label_count",
        ):
            _non_negative_int(name, getattr(self, name))
        if (
            self.correct_label_count
            + self.incorrect_label_count
            + self.uncertain_label_count
            + self.graph_noise_label_count
            != self.labeled_sample_count
        ):
            raise ValueError("evaluation verdict counts must equal labeled_sample_count")
        if self.sample_size_required > self.structural_counts.legal_edge_count:
            raise ValueError("required sample size cannot exceed edge_count")
        if self.labeled_sample_count > self.sample_size_required:
            raise ValueError("labeled sample count cannot exceed the required sample")
        if tuple(sorted(set(self.sampled_edge_ids))) != tuple(sorted(self.sampled_edge_ids)):
            raise ValueError("sampled_edge_ids must be unique")
        if len(self.sampled_edge_ids) != self.sample_size_required:
            raise ValueError("sampled_edge_ids must identify the required sample")
        if status is not self.quality_metrics.availability:
            raise ValueError("evaluation and quality metric availability must agree")
        if (
            status is SemanticEdgeEvaluationStatus.UNAVAILABLE
            and self.labeled_sample_count == 0
            and self.reason != "quality_labels_unavailable"
        ):
            raise ValueError("zero-label evaluation must explicitly report unavailable")

    @property
    def sample_count(self) -> int:
        return self.labeled_sample_count

    @property
    def edge_count(self) -> int:
        return self.structural_counts.legal_edge_count

    @property
    def eligible_edge_count(self) -> int | None:
        return self.structural_counts.eligible_edge_count

    @property
    def unresolved_before(self) -> int | None:
        return self.structural_counts.unresolved_before

    @property
    def unresolved_after(self) -> int | None:
        return self.structural_counts.unresolved_after

    @property
    def precision(self) -> float | None:
        return self.quality_metrics.edge_precision

    @property
    def edge_precision(self) -> float | None:
        return self.quality_metrics.edge_precision

    @property
    def coverage(self) -> float | None:
        return self.quality_metrics.coverage

    @property
    def unresolved_reduction_count(self) -> int | None:
        return self.quality_metrics.unresolved_reduction_count

    @property
    def unresolved_reduction_rate(self) -> float | None:
        return self.quality_metrics.unresolved_reduction_rate

    @property
    def unresolved_reduction(self) -> float | None:
        return self.quality_metrics.unresolved_reduction_rate

    @property
    def graph_noise_count(self) -> int | None:
        return self.quality_metrics.graph_noise_count

    @property
    def graph_noise_rate(self) -> float | None:
        return self.quality_metrics.graph_noise_rate

    @property
    def harmful_count(self) -> int | None:
        return self.quality_metrics.harmful_count

    @property
    def harmful_rate(self) -> float | None:
        return self.quality_metrics.harmful_rate

    @property
    def correct_count(self) -> int:
        return self.correct_label_count

    @property
    def incorrect_count(self) -> int:
        return self.incorrect_label_count

    @property
    def uncertain_count(self) -> int:
        return self.uncertain_label_count


@dataclass(frozen=True, slots=True)
class _EdgeCandidate:
    edge_type: CodeRelationType
    source: SemanticEdgeEndpoint
    target: SemanticEdgeEndpoint
    confidence: float
    provenances: tuple[SemanticEdgeProvenance, ...]
    evidence_locators: tuple[str, ...]
    review_status: CodeReviewStatus
    fact_status: CodeFactStatus


@dataclass(frozen=True, slots=True)
class _ResolvedRelationship:
    linked: ScipLinkedRelationship
    source: SemanticEdgeEndpoint
    target: SemanticEdgeEndpoint
    locator: str


class SemanticEdgeTreatment:
    """Production conversion/merge policy for the Python C5-02 slice."""

    def __init__(
        self,
        *,
        min_confidence: float = DEFAULT_MIN_SEMANTIC_EDGE_CONFIDENCE,
        diagnostic_limit: int = 1_000,
    ) -> None:
        self.min_confidence = _unit_float("min_confidence", min_confidence)
        _non_negative_int("diagnostic_limit", diagnostic_limit)
        self.diagnostic_limit = diagnostic_limit

    def treat(
        self,
        *,
        scip_result: ScipConsumeResult | None,
        tree_sitter_edges: Iterable[TreeSitterConservativeEdge],
        scope: SemanticEdgeScope,
        language: str = "python",
    ) -> SemanticEdgeTreatmentResult:
        """Validate, convert, merge, diagnose, and deterministically freeze edges."""

        if not isinstance(scope, SemanticEdgeScope):
            raise TypeError("scope must be a SemanticEdgeScope")
        tree_edges = tuple(tree_sitter_edges)
        if any(not isinstance(edge, TreeSitterConservativeEdge) for edge in tree_edges):
            raise TypeError("tree_sitter_edges must contain TreeSitterConservativeEdge values")
        normalized_language = _normalized_language(language, scip_result)
        scip_status = _scip_status(scip_result)
        scip_codes = _scip_diagnostic_codes(scip_result)
        raw_diagnostics: list[SemanticEdgeDiagnostic] = []
        if normalized_language != "python":
            raw_diagnostics.append(
                _diagnostic(
                    SemanticEdgeDiagnosticCode.LANGUAGE_UNAVAILABLE,
                    SemanticEdgeDiagnosticSeverity.WARNING,
                    "semantic edge treatment is unavailable outside the Python slice",
                )
            )
            return self._result(
                status=SemanticEdgeTreatmentStatus.UNAVAILABLE,
                support=SemanticEdgeSupportStatus.UNAVAILABLE,
                language=normalized_language,
                edges=(),
                raw_diagnostics=raw_diagnostics,
                conflicts=(),
                scip_status=scip_status,
                scip_codes=scip_codes,
                tree_input_count=len(tree_edges),
                fallback_used=False,
                fallback_reason="language_unavailable",
            )

        if scip_result is None:
            raw_diagnostics.append(
                _diagnostic(
                    SemanticEdgeDiagnosticCode.SCIP_UNAVAILABLE,
                    SemanticEdgeDiagnosticSeverity.WARNING,
                    "SCIP input is unavailable; conservative Tree-sitter edges remain eligible",
                )
            )
        elif scip_result.status is ScipStatus.PARTIAL:
            raw_diagnostics.append(
                _diagnostic(
                    SemanticEdgeDiagnosticCode.SCIP_PARTIAL,
                    SemanticEdgeDiagnosticSeverity.WARNING,
                    "SCIP input is partial; only independently valid linked evidence is eligible",
                )
            )

        tree_candidates = self._tree_candidates(
            tree_edges,
            scope=scope,
            diagnostics=raw_diagnostics,
        )
        scip_candidates = self._scip_candidates(
            scip_result,
            scope=scope,
            diagnostics=raw_diagnostics,
        )
        edges, conflicts = self._merge(
            tree_candidates,
            scip_candidates,
            diagnostics=raw_diagnostics,
        )
        partial = (
            scip_status is not SemanticScipStatus.COMPLETE
            or bool(conflicts)
            or any(
                diagnostic.severity is SemanticEdgeDiagnosticSeverity.ERROR
                for diagnostic in raw_diagnostics
            )
        )
        fallback_used = scip_status is not SemanticScipStatus.COMPLETE and any(
            any(
                provenance.derivation is CodeDerivation.TREE_SITTER
                for provenance in edge.provenances
            )
            for edge in edges
        )
        if fallback_used:
            fallback_reason = (
                "scip_unavailable_tree_sitter_preserved"
                if scip_status is SemanticScipStatus.UNAVAILABLE
                else "scip_partial_tree_sitter_preserved"
            )
        elif scip_status is SemanticScipStatus.COMPLETE:
            fallback_reason = "valid_scip_input_treated"
        elif edges:
            fallback_reason = "partial_semantic_evidence_without_tree_sitter_fallback"
        else:
            fallback_reason = "no_materializable_semantic_edge"
        return self._result(
            status=(
                SemanticEdgeTreatmentStatus.PARTIAL
                if partial
                else SemanticEdgeTreatmentStatus.COMPLETE
            ),
            support=SemanticEdgeSupportStatus.SUPPORTED,
            language=normalized_language,
            edges=edges,
            raw_diagnostics=raw_diagnostics,
            conflicts=conflicts,
            scip_status=scip_status,
            scip_codes=scip_codes,
            tree_input_count=len(tree_edges),
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )

    def _tree_candidates(
        self,
        edges: tuple[TreeSitterConservativeEdge, ...],
        *,
        scope: SemanticEdgeScope,
        diagnostics: list[SemanticEdgeDiagnostic],
    ) -> tuple[_EdgeCandidate, ...]:
        candidates: list[_EdgeCandidate] = []
        for edge in edges:
            violation = _candidate_violation(
                edge.edge_type,
                edge.source,
                edge.target,
                scope=scope,
            )
            if violation is not None:
                code, message = violation
                diagnostics.append(
                    _diagnostic(
                        code,
                        SemanticEdgeDiagnosticSeverity.ERROR,
                        message,
                        edge_type=edge.edge_type,
                        source_entity_id=edge.source.entity_id,
                        target_entity_id=edge.target.entity_id,
                    )
                )
                continue
            if edge.review_status not in {
                CodeReviewStatus.MACHINE_CONFIRMED,
                CodeReviewStatus.HUMAN_CONFIRMED,
            } or edge.fact_status not in {
                CodeFactStatus.MACHINE_CONFIRMED,
                CodeFactStatus.OBSERVED,
            }:
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.EDGE_REGISTRY_REJECTED,
                        SemanticEdgeDiagnosticSeverity.ERROR,
                        "Tree-sitter edge review/fact semantics are not materializable",
                        edge_type=edge.edge_type,
                        source_entity_id=edge.source.entity_id,
                        target_entity_id=edge.target.entity_id,
                    )
                )
                continue
            if edge.confidence < self.min_confidence:
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.LOW_CONFIDENCE,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "Tree-sitter edge is below the precision-first confidence floor",
                        edge_type=edge.edge_type,
                        source_entity_id=edge.source.entity_id,
                        target_entity_id=edge.target.entity_id,
                    )
                )
                continue
            candidates.append(
                _EdgeCandidate(
                    edge_type=edge.edge_type,
                    source=edge.source,
                    target=edge.target,
                    confidence=edge.confidence,
                    provenances=(
                        SemanticEdgeProvenance(
                            derivation=CodeDerivation.TREE_SITTER,
                            producer_version=(
                                f"{edge.parser_version}+resolver:{edge.resolver_version}"
                            ),
                            artifact_id=(
                                f"tree-sitter:{edge.parser_version}:{edge.resolver_version}"
                            ),
                        ),
                    ),
                    evidence_locators=edge.evidence_locators,
                    review_status=edge.review_status,
                    fact_status=edge.fact_status,
                )
            )
        return tuple(sorted(candidates, key=_candidate_sort_key))

    def _scip_candidates(
        self,
        result: ScipConsumeResult | None,
        *,
        scope: SemanticEdgeScope,
        diagnostics: list[SemanticEdgeDiagnostic],
    ) -> tuple[_EdgeCandidate, ...]:
        if result is None:
            return ()
        if result.provenance is None:
            if result.occurrences or result.relationships:
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.SCIP_PROVENANCE_MISSING,
                        SemanticEdgeDiagnosticSeverity.ERROR,
                        "SCIP linked evidence without artifact provenance was rejected",
                    )
                )
            return ()
        provenance = SemanticEdgeProvenance(
            derivation=CodeDerivation.SCIP,
            producer_version=(
                f"{result.provenance.consumer_version}+"
                f"{result.provenance.indexer_name}:{result.provenance.indexer_version}"
            ),
            artifact_id=result.provenance.raw_object.raw_object_id,
            content_sha256=result.provenance.raw_object.content_sha256,
        )
        external_symbols = {item.symbol for item in result.external_symbols}
        documents: dict[str, SemanticEdgeEndpoint] = {}
        for linked_document in result.documents:
            path = linked_document.document.relative_path
            endpoint = _linked_endpoint(
                linked_document.file_link,
                expected_type=CodeGraphEntityType.FILE_VERSION,
                scope=scope,
                stable_version=scope.stable_version,
                diagnostics=diagnostics,
                relative_path=path,
            )
            if endpoint is not None:
                documents[path] = endpoint

        candidates: list[_EdgeCandidate] = []
        relationships: list[_ResolvedRelationship] = []
        for linked in result.relationships:
            relative_path = _relationship_path(linked)
            if linked.source_symbol.startswith("local "):
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.LINK_LOCAL,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "local SCIP relationship source was not promoted to a graph edge",
                        relative_path=relative_path,
                        symbol=linked.source_symbol,
                    )
                )
                continue
            if linked.target_symbol.startswith("local "):
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.LINK_LOCAL,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "local SCIP relationship target was not promoted to a graph edge",
                        relative_path=relative_path,
                        symbol=linked.target_symbol,
                    )
                )
                continue
            if linked.source_symbol in external_symbols or linked.target_symbol in external_symbols:
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.LINK_EXTERNAL,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "external SCIP relationship endpoint was not promoted to a graph edge",
                        relative_path=relative_path,
                        symbol=(
                            linked.source_symbol
                            if linked.source_symbol in external_symbols
                            else linked.target_symbol
                        ),
                    )
                )
                continue
            source = _linked_endpoint(
                linked.source_link,
                expected_type=CodeGraphEntityType.CODE_SYMBOL,
                scope=scope,
                stable_version=scope.stable_version,
                diagnostics=diagnostics,
                relative_path=relative_path,
                symbol=linked.source_symbol,
            )
            target = _linked_endpoint(
                linked.target_link,
                expected_type=CodeGraphEntityType.CODE_SYMBOL,
                scope=scope,
                stable_version=scope.stable_version,
                diagnostics=diagnostics,
                relative_path=relative_path,
                symbol=linked.target_symbol,
            )
            if source is None or target is None:
                continue
            locator = _relationship_locator(scope, linked, relative_path)
            resolved = _ResolvedRelationship(
                linked=linked,
                source=source,
                target=target,
                locator=locator,
            )
            relationships.append(resolved)

        relationship_index: defaultdict[tuple[str, str, str], list[_ResolvedRelationship]] = (
            defaultdict(list)
        )
        for relationship in relationships:
            flags = relationship.linked.relationship
            if not (flags.is_reference or flags.is_implementation):
                continue
            key = (
                relationship.source.locator.rsplit("#", 1)[0],
                relationship.linked.target_symbol,
                relationship.target.entity_id,
            )
            relationship_index[key].append(relationship)

        consumed_relationships: set[_ResolvedRelationship] = set()
        for linked_occurrence in result.occurrences:
            occurrence = linked_occurrence.occurrence
            if occurrence.is_local or occurrence.symbol.startswith("local "):
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.LINK_LOCAL,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "local SCIP occurrence was not promoted to a graph edge",
                        relative_path=occurrence.relative_path,
                        symbol=occurrence.symbol,
                    )
                )
                continue
            if (
                occurrence.kind is ScipOccurrenceKind.EXTERNAL
                or occurrence.symbol in external_symbols
            ):
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.LINK_EXTERNAL,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "external SCIP occurrence was not promoted to a graph edge",
                        relative_path=occurrence.relative_path,
                        symbol=occurrence.symbol,
                    )
                )
                continue
            if occurrence.kind is ScipOccurrenceKind.DEFINITION:
                continue
            target = _linked_endpoint(
                linked_occurrence.symbol_link,
                expected_type=CodeGraphEntityType.CODE_SYMBOL,
                scope=scope,
                stable_version=scope.stable_version,
                diagnostics=diagnostics,
                relative_path=occurrence.relative_path,
                symbol=occurrence.symbol,
            )
            if target is None:
                continue
            source_file = documents.get(occurrence.relative_path)
            locator = _occurrence_locator(scope, occurrence)
            path_locator = _entity_locator_base(
                scope.repository_id,
                scope.stable_version,
                occurrence.relative_path,
            )
            matches = tuple(
                relationship_index.get(
                    (path_locator, occurrence.symbol, target.entity_id),
                    (),
                )
            )
            distinct_sources = {
                relationship.source.entity_id: relationship.source for relationship in matches
            }
            call_emitted = False
            if len(distinct_sources) > 1:
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.RELATIONSHIP_EVIDENCE_AMBIGUOUS,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "resolved occurrence maps to multiple enclosing CodeSymbols",
                        target_entity_id=target.entity_id,
                        relative_path=occurrence.relative_path,
                        symbol=occurrence.symbol,
                        candidate_ids=tuple(sorted(distinct_sources)),
                    )
                )
            elif len(distinct_sources) == 1:
                for relationship in matches:
                    consumed_relationships.add(relationship)
                    flags = relationship.linked.relationship
                    evidence_locators = (locator, relationship.locator)
                    if flags.is_implementation:
                        implementation = _scip_candidate(
                            CodeRelationType.IMPLEMENTS,
                            relationship.source,
                            target,
                            provenance=provenance,
                            locators=evidence_locators,
                        )
                        if _append_if_valid(implementation, scope, diagnostics):
                            candidates.append(implementation)
                        if _has_role(linked_occurrence, ScipSemanticRole.OVERRIDE):
                            override = _scip_candidate(
                                CodeRelationType.OVERRIDES,
                                relationship.source,
                                target,
                                provenance=provenance,
                                locators=evidence_locators,
                            )
                            if _append_if_valid(override, scope, diagnostics):
                                candidates.append(override)
                    if flags.is_reference:
                        relation_type = (
                            CodeRelationType.CALLS
                            if _has_role(linked_occurrence, ScipSemanticRole.CALL)
                            else CodeRelationType.REFERENCES
                        )
                        reference_or_call = _scip_candidate(
                            relation_type,
                            relationship.source,
                            target,
                            provenance=provenance,
                            locators=evidence_locators,
                        )
                        if _append_if_valid(reference_or_call, scope, diagnostics):
                            candidates.append(reference_or_call)
                            call_emitted = relation_type is CodeRelationType.CALLS
            if _has_role(linked_occurrence, ScipSemanticRole.CALL) and not call_emitted:
                diagnostics.append(
                    _diagnostic(
                        (
                            SemanticEdgeDiagnosticCode.CALL_EVIDENCE_AMBIGUOUS
                            if len(distinct_sources) > 1
                            else SemanticEdgeDiagnosticCode.CALL_EVIDENCE_MISSING
                        ),
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        (
                            "explicit call role lacks one resolved enclosing CodeSymbol"
                            if len(distinct_sources) <= 1
                            else "explicit call role resolves to multiple enclosing CodeSymbols"
                        ),
                        edge_type=CodeRelationType.CALLS,
                        target_entity_id=target.entity_id,
                        relative_path=occurrence.relative_path,
                        symbol=occurrence.symbol,
                        candidate_ids=tuple(sorted(distinct_sources)),
                    )
                )
            if _has_role(linked_occurrence, ScipSemanticRole.OVERRIDE) and not any(
                relationship.linked.relationship.is_implementation for relationship in matches
            ):
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.OVERRIDE_EVIDENCE_MISSING,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "explicit override hint lacks one resolved implementation relationship",
                        edge_type=CodeRelationType.OVERRIDES,
                        target_entity_id=target.entity_id,
                        relative_path=occurrence.relative_path,
                        symbol=occurrence.symbol,
                        candidate_ids=tuple(sorted(distinct_sources)),
                    )
                )
            if call_emitted:
                continue
            if source_file is None:
                continue
            reference = _scip_candidate(
                CodeRelationType.REFERENCES,
                source_file,
                target,
                provenance=provenance,
                locator=locator,
            )
            if _append_if_valid(reference, scope, diagnostics):
                candidates.append(reference)

        for relationship in relationships:
            flags = relationship.linked.relationship
            if (
                flags.is_reference or flags.is_implementation
            ) and relationship not in consumed_relationships:
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.RELATIONSHIP_EVIDENCE_MISSING,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "SCIP relationship lacks a matching resolved internal occurrence",
                        source_entity_id=relationship.source.entity_id,
                        target_entity_id=relationship.target.entity_id,
                        relative_path=_relationship_path(relationship.linked),
                        symbol=relationship.linked.target_symbol,
                    )
                )
        return tuple(sorted(candidates, key=_candidate_sort_key))

    def _merge(
        self,
        tree_candidates: tuple[_EdgeCandidate, ...],
        scip_candidates: tuple[_EdgeCandidate, ...],
        *,
        diagnostics: list[SemanticEdgeDiagnostic],
    ) -> tuple[tuple[SemanticEdge, ...], tuple[SemanticEdgeConflict, ...]]:
        tree_by_key = _group_candidates(tree_candidates)
        retained: dict[tuple[Any, ...], SemanticEdge] = {
            key: _freeze_candidates(values) for key, values in tree_by_key.items()
        }
        tree_edges = tuple(sorted(retained.values(), key=_edge_sort_key))
        conflicts: list[SemanticEdgeConflict] = []
        for key, values in _group_candidates(scip_candidates).items():
            scip_edge = _freeze_candidates(values)
            existing = retained.get(key)
            if existing is not None:
                retained[key] = _merge_edges(existing, scip_edge)
                continue
            conflicting = tuple(edge for edge in tree_edges if _edges_conflict(edge, scip_edge))
            if conflicting:
                conflict = _conflict(scip_edge, conflicting)
                conflicts.append(conflict)
                diagnostics.append(
                    _diagnostic(
                        SemanticEdgeDiagnosticCode.TREE_SITTER_SCIP_CONFLICT,
                        SemanticEdgeDiagnosticSeverity.WARNING,
                        "SCIP challenger was suppressed; existing Tree-sitter edge was preserved",
                        edge_type=scip_edge.edge_type,
                        source_entity_id=scip_edge.source.entity_id,
                        target_entity_id=scip_edge.target.entity_id,
                        candidate_ids=conflict.retained_tree_edge_ids,
                    )
                )
                continue
            retained[key] = scip_edge
        return (
            tuple(sorted(retained.values(), key=_edge_sort_key)),
            tuple(sorted(conflicts, key=_conflict_key)),
        )

    def _result(
        self,
        *,
        status: SemanticEdgeTreatmentStatus,
        support: SemanticEdgeSupportStatus,
        language: str,
        edges: tuple[SemanticEdge, ...],
        raw_diagnostics: list[SemanticEdgeDiagnostic],
        conflicts: tuple[SemanticEdgeConflict, ...],
        scip_status: SemanticScipStatus,
        scip_codes: tuple[str, ...],
        tree_input_count: int,
        fallback_used: bool,
        fallback_reason: str,
    ) -> SemanticEdgeTreatmentResult:
        diagnostics, counts, truncated = _bounded_diagnostics(
            raw_diagnostics,
            limit=self.diagnostic_limit,
        )
        tree_retained = sum(
            any(
                provenance.derivation is CodeDerivation.TREE_SITTER
                for provenance in edge.provenances
            )
            for edge in edges
        )
        scip_retained = sum(
            any(provenance.derivation is CodeDerivation.SCIP for provenance in edge.provenances)
            for edge in edges
        )
        trace = SemanticEdgeFallbackTrace(
            scip_status=scip_status,
            scip_diagnostic_codes=scip_codes,
            tree_sitter_input_count=tree_input_count,
            tree_sitter_retained_count=tree_retained,
            scip_retained_count=scip_retained,
            fallback_used=fallback_used,
            reason=fallback_reason,
        )
        canonical_edges = tuple(sorted(edges, key=_edge_sort_key))
        result_hash = _result_hash(
            status=status,
            support=support,
            language=language,
            semantic_ready=scip_retained > 0,
            edges=canonical_edges,
            diagnostics=diagnostics,
            diagnostic_counts=counts,
            diagnostics_truncated=truncated,
            conflicts=conflicts,
            trace=trace,
            treatment_version=SEMANTIC_EDGE_TREATMENT_VERSION,
        )
        return SemanticEdgeTreatmentResult(
            status=status,
            support_status=support,
            language=language,
            semantic_ready=scip_retained > 0,
            edges=canonical_edges,
            diagnostics=diagnostics,
            diagnostic_counts=counts,
            diagnostics_truncated=truncated,
            conflicts=conflicts,
            trace=trace,
            treatment_version=SEMANTIC_EDGE_TREATMENT_VERSION,
            result_hash=result_hash,
        )


def treat_python_semantic_edges(
    *,
    scip_result: ScipConsumeResult | None,
    tree_sitter_edges: Iterable[TreeSitterConservativeEdge],
    scope: SemanticEdgeScope,
    language: str = "python",
    min_confidence: float = DEFAULT_MIN_SEMANTIC_EDGE_CONFIDENCE,
    diagnostic_limit: int = 1_000,
) -> SemanticEdgeTreatmentResult:
    """Functional entry point for the production transformation."""

    return SemanticEdgeTreatment(
        min_confidence=min_confidence,
        diagnostic_limit=diagnostic_limit,
    ).treat(
        scip_result=scip_result,
        tree_sitter_edges=tree_sitter_edges,
        scope=scope,
        language=language,
    )


def sample_semantic_edges(
    value: SemanticEdgeTreatmentResult | Iterable[SemanticEdge],
    *,
    limit: int = DEFAULT_SEMANTIC_EDGE_SAMPLE_SIZE,
) -> tuple[SemanticEdge, ...]:
    """Return an order-independent deterministic review sample without labels."""

    _non_negative_int("limit", limit)
    edges = value.edges if isinstance(value, SemanticEdgeTreatmentResult) else tuple(value)
    if any(not isinstance(edge, SemanticEdge) for edge in edges):
        raise TypeError("sample input must contain SemanticEdge values")
    ranked = sorted(
        edges,
        key=lambda edge: (
            hashlib.sha256(edge.edge_id.encode("utf-8")).digest(),
            edge.edge_id,
        ),
    )
    return tuple(ranked[:limit])


def evaluate_semantic_edges(
    result: SemanticEdgeTreatmentResult,
    *,
    labels: Iterable[SemanticEdgePrecisionLabel] = (),
    eligible_edge_count: int | None = None,
    unresolved_before: int | None = None,
    unresolved_after: int | None = None,
    sample_size: int = DEFAULT_SEMANTIC_EDGE_SAMPLE_SIZE,
) -> SemanticEdgeEvaluation:
    """Evaluate only supplied labels; never invent the 200-edge manual sample."""

    if not isinstance(result, SemanticEdgeTreatmentResult):
        raise TypeError("result must be a SemanticEdgeTreatmentResult")
    _non_negative_int("sample_size", sample_size)
    if sample_size == 0:
        raise ValueError("sample_size must be positive")
    if eligible_edge_count is not None:
        _non_negative_int("eligible_edge_count", eligible_edge_count)
        if eligible_edge_count < len(result.edges):
            raise ValueError("eligible_edge_count cannot be smaller than emitted edge_count")
    if (unresolved_before is None) != (unresolved_after is None):
        raise ValueError("unresolved_before and unresolved_after must be provided together")
    if unresolved_before is not None and unresolved_after is not None:
        _non_negative_int("unresolved_before", unresolved_before)
        _non_negative_int("unresolved_after", unresolved_after)
    label_values = tuple(labels)
    if any(not isinstance(label, SemanticEdgePrecisionLabel) for label in label_values):
        raise TypeError("labels must contain SemanticEdgePrecisionLabel values")
    label_by_edge: dict[str, SemanticEdgePrecisionLabel] = {}
    edge_ids = {edge.edge_id for edge in result.edges}
    for label in label_values:
        if label.edge_id not in edge_ids:
            raise ValueError(f"precision label references an unknown edge: {label.edge_id}")
        if label.edge_id in label_by_edge:
            raise ValueError(f"duplicate precision label for edge: {label.edge_id}")
        label_by_edge[label.edge_id] = label

    sampled = sample_semantic_edges(result, limit=sample_size)
    sampled_ids = tuple(edge.edge_id for edge in sampled)
    sampled_labels = tuple(
        label_by_edge[edge_id] for edge_id in sampled_ids if edge_id in label_by_edge
    )
    verdicts = Counter(label.verdict for label in sampled_labels)
    correct = verdicts[SemanticEdgePrecisionVerdict.CORRECT]
    incorrect = verdicts[SemanticEdgePrecisionVerdict.INCORRECT]
    graph_noise = verdicts[SemanticEdgePrecisionVerdict.GRAPH_NOISE]
    uncertain = verdicts[SemanticEdgePrecisionVerdict.UNCERTAIN]
    determinate = correct + incorrect + graph_noise
    required = min(len(result.edges), sample_size)
    structural_counts = SemanticEdgeStructuralCounts(
        legal_edge_count=len(result.edges),
        conflict_count=len(result.conflicts),
        diagnostic_count=sum(item.count for item in result.diagnostic_counts),
        eligible_edge_count=eligible_edge_count,
        unresolved_before=unresolved_before,
        unresolved_after=unresolved_after,
    )
    if not sampled_labels:
        status = SemanticEdgeEvaluationStatus.UNAVAILABLE
        reason = "quality_labels_unavailable"
        quality_metrics = SemanticEdgeQualityMetrics(
            availability=status,
            edge_precision=None,
            coverage=None,
            unresolved_reduction_count=None,
            unresolved_reduction_rate=None,
            graph_noise_count=None,
            graph_noise_rate=None,
            harmful_count=None,
            harmful_rate=None,
        )
    elif determinate == 0:
        status = SemanticEdgeEvaluationStatus.UNAVAILABLE
        reason = "quality_labels_have_no_determinate_verdict"
        quality_metrics = SemanticEdgeQualityMetrics(
            availability=status,
            edge_precision=None,
            coverage=None,
            unresolved_reduction_count=None,
            unresolved_reduction_rate=None,
            graph_noise_count=None,
            graph_noise_rate=None,
            harmful_count=None,
            harmful_rate=None,
        )
    else:
        if determinate >= DEFAULT_SEMANTIC_EDGE_SAMPLE_SIZE and uncertain == 0:
            status = SemanticEdgeEvaluationStatus.AVAILABLE
            reason = "minimum_200_label_quality_sample_complete"
        elif (
            len(result.edges) < DEFAULT_SEMANTIC_EDGE_SAMPLE_SIZE
            and len(sampled_labels) == len(result.edges)
            and uncertain == 0
        ):
            status = SemanticEdgeEvaluationStatus.PROVISIONAL
            reason = "all_fixture_edges_labeled_below_200_provisional"
        else:
            status = SemanticEdgeEvaluationStatus.PROVISIONAL
            reason = "partial_real_label_sample_provisional"
        coverage = correct / eligible_edge_count if eligible_edge_count not in {None, 0} else None
        if unresolved_before is None or unresolved_after is None:
            reduction_count = None
            reduction_rate = None
        else:
            reduction_count = max(0, unresolved_before - unresolved_after)
            reduction_rate = reduction_count / unresolved_before if unresolved_before else 0.0
        harmful = incorrect + graph_noise
        quality_metrics = SemanticEdgeQualityMetrics(
            availability=status,
            edge_precision=correct / determinate,
            coverage=coverage,
            unresolved_reduction_count=reduction_count,
            unresolved_reduction_rate=reduction_rate,
            graph_noise_count=graph_noise,
            graph_noise_rate=graph_noise / determinate,
            harmful_count=harmful,
            harmful_rate=harmful / determinate,
        )
    return SemanticEdgeEvaluation(
        status=status,
        reason=reason,
        protocol_version=SEMANTIC_EDGE_LABEL_PROTOCOL_VERSION,
        structural_counts=structural_counts,
        quality_metrics=quality_metrics,
        sample_size_target=DEFAULT_SEMANTIC_EDGE_SAMPLE_SIZE,
        sample_size_required=required,
        labeled_sample_count=len(sampled_labels),
        correct_label_count=correct,
        incorrect_label_count=incorrect,
        uncertain_label_count=uncertain,
        graph_noise_label_count=graph_noise,
        sampled_edge_ids=sampled_ids,
    )


def _scip_candidate(
    edge_type: CodeRelationType,
    source: SemanticEdgeEndpoint,
    target: SemanticEdgeEndpoint,
    *,
    provenance: SemanticEdgeProvenance,
    locator: str | None = None,
    locators: tuple[str, ...] = (),
) -> _EdgeCandidate:
    if (locator is None) == (not locators):
        raise ValueError("SCIP edge requires exactly one locator input form")
    evidence_locators = (locator,) if locator is not None else locators
    return _EdgeCandidate(
        edge_type=edge_type,
        source=source,
        target=target,
        confidence=1.0,
        provenances=(provenance,),
        evidence_locators=_canonical_locators(evidence_locators),
        review_status=CodeReviewStatus.MACHINE_CONFIRMED,
        fact_status=CodeFactStatus.MACHINE_CONFIRMED,
    )


def _append_if_valid(
    candidate: _EdgeCandidate,
    scope: SemanticEdgeScope,
    diagnostics: list[SemanticEdgeDiagnostic],
) -> bool:
    violation = _candidate_violation(
        candidate.edge_type,
        candidate.source,
        candidate.target,
        scope=scope,
    )
    if violation is None:
        return True
    code, message = violation
    diagnostics.append(
        _diagnostic(
            code,
            SemanticEdgeDiagnosticSeverity.ERROR,
            message,
            edge_type=candidate.edge_type,
            source_entity_id=candidate.source.entity_id,
            target_entity_id=candidate.target.entity_id,
        )
    )
    return False


def _candidate_violation(
    edge_type: CodeRelationType,
    source: SemanticEdgeEndpoint,
    target: SemanticEdgeEndpoint,
    *,
    scope: SemanticEdgeScope,
) -> tuple[SemanticEdgeDiagnosticCode, str] | None:
    expected_scope = (
        scope.project_id,
        scope.repository_id,
        scope.generation_id,
        scope.stable_version,
        scope.acl_ref,
    )
    for endpoint in (source, target):
        actual_scope = endpoint.scope_identity()
        if actual_scope[3] != scope.stable_version:
            return (
                SemanticEdgeDiagnosticCode.ENDPOINT_VERSION_MISMATCH,
                "edge endpoint stable version does not match the strict treatment scope",
            )
        if actual_scope != expected_scope:
            return (
                SemanticEdgeDiagnosticCode.ENDPOINT_SCOPE_MISMATCH,
                "edge endpoint project/repository/generation/ACL scope does not match",
            )
        try:
            _require_locator("endpoint locator", endpoint.locator)
        except (TypeError, ValueError):
            return (
                SemanticEdgeDiagnosticCode.ENDPOINT_LOCATOR_REJECTED,
                "edge endpoint locator is missing or unsafe",
            )
    if _endpoint_state(source) == _endpoint_state(target):
        return (
            SemanticEdgeDiagnosticCode.EDGE_SELF_REFERENCE_REJECTED,
            "self edge endpoint states are not materializable",
        )
    try:
        validate_edge_assertion(
            edge_type,
            source_type=source.entity_type,
            target_type=target.entity_type,
            derivation=CodeEdgeDerivationLayer.STATIC,
        )
    except ValueError:
        return (
            SemanticEdgeDiagnosticCode.EDGE_REGISTRY_REJECTED,
            "edge type, endpoint types, or derivation are rejected by the C4 registry",
        )
    return None


def _linked_endpoint(
    link: ScipLink,
    *,
    expected_type: CodeGraphEntityType,
    scope: SemanticEdgeScope,
    stable_version: str,
    diagnostics: list[SemanticEdgeDiagnostic],
    relative_path: str,
    symbol: str = "",
) -> SemanticEdgeEndpoint | None:
    if not isinstance(link, ScipLink):
        raise TypeError("SCIP linked evidence must contain ScipLink values")
    if link.status is not ScipLinkStatus.RESOLVED or link.entity is None:
        code = {
            ScipLinkStatus.UNRESOLVED: SemanticEdgeDiagnosticCode.LINK_UNRESOLVED,
            ScipLinkStatus.AMBIGUOUS: SemanticEdgeDiagnosticCode.LINK_AMBIGUOUS,
            ScipLinkStatus.EXTERNAL: SemanticEdgeDiagnosticCode.LINK_EXTERNAL,
            ScipLinkStatus.LOCAL: SemanticEdgeDiagnosticCode.LINK_LOCAL,
            ScipLinkStatus.SCOPE_MISMATCH: SemanticEdgeDiagnosticCode.LINK_SCOPE_MISMATCH,
            ScipLinkStatus.RESOLVER_UNAVAILABLE: (
                SemanticEdgeDiagnosticCode.LINK_RESOLVER_UNAVAILABLE
            ),
        }.get(link.status, SemanticEdgeDiagnosticCode.LINK_UNRESOLVED)
        diagnostics.append(
            _diagnostic(
                code,
                SemanticEdgeDiagnosticSeverity.WARNING,
                "non-resolved SCIP endpoint was not promoted to an internal graph edge",
                relative_path=relative_path,
                symbol=symbol,
                candidate_ids=tuple(sorted(set(link.candidate_ids))),
            )
        )
        return None
    entity = link.entity
    if entity.entity_type != expected_type.value:
        diagnostics.append(
            _diagnostic(
                SemanticEdgeDiagnosticCode.ENDPOINT_TYPE_REJECTED,
                SemanticEdgeDiagnosticSeverity.ERROR,
                "SCIP endpoint entity type does not match the required C4 endpoint type",
                source_entity_id=entity.entity_id,
                relative_path=relative_path,
                symbol=symbol,
            )
        )
        return None
    expected = (
        scope.project_id,
        scope.repository_id,
        scope.generation_id,
        scope.acl_ref,
    )
    actual = (
        entity.project_id,
        entity.repository_id,
        entity.generation_id,
        entity.acl_ref,
    )
    if actual != expected:
        diagnostics.append(
            _diagnostic(
                SemanticEdgeDiagnosticCode.ENDPOINT_SCOPE_MISMATCH,
                SemanticEdgeDiagnosticSeverity.ERROR,
                "SCIP endpoint project/repository/generation/ACL scope does not match",
                source_entity_id=entity.entity_id,
                relative_path=relative_path,
                symbol=symbol,
            )
        )
        return None
    try:
        path = _relative_path(entity.relative_path)
        locator = _entity_locator(
            scope.repository_id,
            stable_version,
            path,
            entity.entity_id,
        )
    except (TypeError, ValueError):
        diagnostics.append(
            _diagnostic(
                SemanticEdgeDiagnosticCode.ENDPOINT_LOCATOR_REJECTED,
                SemanticEdgeDiagnosticSeverity.ERROR,
                "SCIP endpoint path could not produce a governed locator",
                source_entity_id=entity.entity_id,
                relative_path=relative_path,
                symbol=symbol,
            )
        )
        return None
    return SemanticEdgeEndpoint(
        entity_id=entity.entity_id,
        entity_type=expected_type,
        project_id=scope.project_id,
        repository_id=scope.repository_id,
        generation_id=scope.generation_id,
        stable_version=stable_version,
        acl_ref=scope.acl_ref,
        locator=locator,
    )


def _relationship_path(linked: ScipLinkedRelationship) -> str:
    for link in (linked.source_link, linked.target_link):
        if link.entity is not None and link.entity.relative_path:
            return _relative_path(link.entity.relative_path)
    return "unknown.py"


def _relationship_locator(
    scope: SemanticEdgeScope,
    linked: ScipLinkedRelationship,
    relative_path: str,
) -> str:
    base = _entity_locator_base(scope.repository_id, scope.stable_version, relative_path)
    source = quote(linked.source_symbol, safe="")
    target = quote(linked.target_symbol, safe="")
    return f"{base}#relationship={source}->{target}"


def _occurrence_locator(scope: SemanticEdgeScope, occurrence: Any) -> str:
    source_range = occurrence.source_range
    base = _entity_locator_base(
        scope.repository_id,
        scope.stable_version,
        occurrence.relative_path,
    )
    return (
        f"{base}#L{source_range.start.line}C{source_range.start.character}"
        f"-L{source_range.end.line}C{source_range.end.character}"
    )


def _entity_locator(
    repository_id: str,
    stable_version: str,
    relative_path: str,
    entity_id: str,
) -> str:
    return (
        f"{_entity_locator_base(repository_id, stable_version, relative_path)}"
        f"#entity={quote(entity_id, safe='')}"
    )


def _entity_locator_base(
    repository_id: str,
    stable_version: str,
    relative_path: str,
) -> str:
    path = _relative_path(relative_path)
    return (
        f"code://{quote(repository_id, safe='._-')}@"
        f"{quote(stable_version, safe='._-')}/{quote(path, safe='/._-')}"
    )


def _has_role(
    occurrence: ScipLinkedOccurrence,
    role: ScipSemanticRole,
) -> bool:
    return bool(occurrence.occurrence.symbol_roles & int(role))


def _group_candidates(
    candidates: Iterable[_EdgeCandidate],
) -> Mapping[tuple[Any, ...], tuple[_EdgeCandidate, ...]]:
    grouped: defaultdict[tuple[Any, ...], list[_EdgeCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[_candidate_identity(candidate)].append(candidate)
    return {
        key: tuple(sorted(values, key=_candidate_sort_key))
        for key, values in sorted(grouped.items(), key=lambda item: item[0])
    }


def _freeze_candidates(candidates: tuple[_EdgeCandidate, ...]) -> SemanticEdge:
    if not candidates:
        raise ValueError("cannot freeze an empty candidate group")
    first = candidates[0]
    if any(_candidate_identity(item) != _candidate_identity(first) for item in candidates):
        raise ValueError("candidate group contains multiple canonical edges")
    provenances = tuple(
        sorted(
            {provenance for item in candidates for provenance in item.provenances},
            key=_provenance_key,
        )
    )
    locators = tuple(sorted({locator for item in candidates for locator in item.evidence_locators}))
    confidence = max(item.confidence for item in candidates)
    review_status = (
        CodeReviewStatus.HUMAN_CONFIRMED
        if any(item.review_status is CodeReviewStatus.HUMAN_CONFIRMED for item in candidates)
        else CodeReviewStatus.MACHINE_CONFIRMED
    )
    fact_status = (
        CodeFactStatus.OBSERVED
        if all(item.fact_status is CodeFactStatus.OBSERVED for item in candidates)
        else CodeFactStatus.MACHINE_CONFIRMED
    )
    canonical_hash = _canonical_edge_hash(first.edge_type, first.source, first.target)
    return SemanticEdge(
        edge_id=f"code-edge-v1:{canonical_hash}",
        canonical_hash=canonical_hash,
        edge_type=first.edge_type,
        source=first.source,
        target=first.target,
        confidence=confidence,
        confidence_semantics=get_edge_spec(first.edge_type).confidence_meaning,
        derivation_layer=CodeEdgeDerivationLayer.STATIC,
        provenances=provenances,
        evidence_locators=locators,
        review_status=review_status,
        fact_status=fact_status,
    )


def _merge_edges(existing: SemanticEdge, challenger: SemanticEdge) -> SemanticEdge:
    if _edge_identity(existing) != _edge_identity(challenger):
        raise ValueError("only the same canonical edge can merge provenance")
    provenances = tuple(
        sorted(set((*existing.provenances, *challenger.provenances)), key=_provenance_key)
    )
    locators = tuple(sorted(set((*existing.evidence_locators, *challenger.evidence_locators))))
    review_status = (
        CodeReviewStatus.HUMAN_CONFIRMED
        if CodeReviewStatus.HUMAN_CONFIRMED in {existing.review_status, challenger.review_status}
        else CodeReviewStatus.MACHINE_CONFIRMED
    )
    fact_status = (
        CodeFactStatus.OBSERVED
        if existing.fact_status is challenger.fact_status is CodeFactStatus.OBSERVED
        else CodeFactStatus.MACHINE_CONFIRMED
    )
    return SemanticEdge(
        edge_id=existing.edge_id,
        canonical_hash=existing.canonical_hash,
        edge_type=existing.edge_type,
        source=existing.source,
        target=existing.target,
        confidence=max(existing.confidence, challenger.confidence),
        confidence_semantics=existing.confidence_semantics,
        derivation_layer=CodeEdgeDerivationLayer.STATIC,
        provenances=provenances,
        evidence_locators=locators,
        review_status=review_status,
        fact_status=fact_status,
    )


def _edges_conflict(tree_edge: SemanticEdge, scip_edge: SemanticEdge) -> bool:
    if _edge_identity(tree_edge) == _edge_identity(scip_edge):
        return False
    same_source = _endpoint_state(tree_edge.source) == _endpoint_state(scip_edge.source)
    same_target = _endpoint_state(tree_edge.target) == _endpoint_state(scip_edge.target)
    call_reference_conflict = (
        same_source
        and same_target
        and {tree_edge.edge_type, scip_edge.edge_type}
        == {CodeRelationType.CALLS, CodeRelationType.REFERENCES}
    )
    target_resolution_conflict = (
        same_source
        and tree_edge.edge_type is scip_edge.edge_type
        and not same_target
        and bool(set(tree_edge.evidence_locators) & set(scip_edge.evidence_locators))
    )
    return call_reference_conflict or target_resolution_conflict


def _conflict(
    scip_edge: SemanticEdge,
    tree_edges: tuple[SemanticEdge, ...],
) -> SemanticEdgeConflict:
    retained_ids = tuple(sorted(edge.edge_id for edge in tree_edges))
    retained_types = tuple(sorted({edge.edge_type for edge in tree_edges}, key=lambda x: x.value))
    locators = tuple(
        sorted(
            {
                *scip_edge.evidence_locators,
                *(locator for edge in tree_edges for locator in edge.evidence_locators),
            }
        )
    )
    payload = {
        "source": scip_edge.source.entity_id,
        "target": scip_edge.target.entity_id,
        "scip_type": scip_edge.edge_type.value,
        "retained": retained_ids,
        "locators": locators,
    }
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return SemanticEdgeConflict(
        conflict_id=f"code-edge-conflict-v1:{digest}",
        source_entity_id=scip_edge.source.entity_id,
        scip_target_entity_id=scip_edge.target.entity_id,
        scip_edge_type=scip_edge.edge_type,
        retained_tree_edge_ids=retained_ids,
        retained_tree_edge_types=retained_types,
        evidence_locators=locators,
        reason="tree_sitter_baseline_preserved_over_conflicting_scip_challenger",
    )


def _bounded_diagnostics(
    diagnostics: Iterable[SemanticEdgeDiagnostic],
    *,
    limit: int,
) -> tuple[
    tuple[SemanticEdgeDiagnostic, ...],
    tuple[SemanticEdgeDiagnosticCount, ...],
    int,
]:
    values = tuple(diagnostics)
    counts = Counter(item.code for item in values)
    unique = {(_diagnostic_key(item), item) for item in values}
    ranked = sorted(
        (item for _, item in unique),
        key=lambda item: (
            hashlib.sha256(_diagnostic_bytes(item)).digest(),
            _diagnostic_key(item),
        ),
    )
    retained = tuple(sorted(ranked[:limit], key=_diagnostic_key))
    count_values = tuple(
        SemanticEdgeDiagnosticCount(code=code, count=count)
        for code, count in sorted(counts.items(), key=lambda item: item[0].value)
    )
    return retained, count_values, max(0, len(values) - len(retained))


def _diagnostic(
    code: SemanticEdgeDiagnosticCode,
    severity: SemanticEdgeDiagnosticSeverity,
    message: str,
    *,
    edge_type: CodeRelationType | None = None,
    source_entity_id: str = "",
    target_entity_id: str = "",
    relative_path: str = "",
    symbol: str = "",
    candidate_ids: tuple[str, ...] = (),
) -> SemanticEdgeDiagnostic:
    return SemanticEdgeDiagnostic(
        code=code,
        severity=severity,
        message=message,
        edge_type=edge_type,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relative_path=relative_path,
        symbol=symbol,
        candidate_ids=tuple(sorted(set(candidate_ids))),
    )


def _normalized_language(
    language: str,
    scip_result: ScipConsumeResult | None,
) -> str:
    raw = _require_text("language", language).casefold()
    aliases = {
        "py": "python",
        "python3": "python",
        "js": "javascript",
        "jsx": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
    }
    normalized = aliases.get(raw, raw)
    document_languages = {
        aliases.get(item.document.language.casefold(), item.document.language.casefold())
        for item in (scip_result.documents if scip_result is not None else ())
        if item.document.language
    }
    if document_languages and document_languages != {"python"}:
        return sorted(document_languages)[0] if len(document_languages) == 1 else "mixed"
    return normalized


def _scip_status(result: ScipConsumeResult | None) -> SemanticScipStatus:
    if result is None:
        return SemanticScipStatus.UNAVAILABLE
    return (
        SemanticScipStatus.COMPLETE
        if result.status is ScipStatus.COMPLETE
        else SemanticScipStatus.PARTIAL
    )


def _scip_diagnostic_codes(result: ScipConsumeResult | None) -> tuple[str, ...]:
    if result is None:
        return ()
    return tuple(sorted({item.code for item in result.diagnostics}))


def _relative_path(value: str) -> str:
    text = _require_text("relative_path", value)
    if "\\" in text or text.startswith("/"):
        raise ValueError("relative_path must be canonical POSIX-relative text")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("relative_path must not escape the repository")
    return path.as_posix()


def _canonical_locators(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError("evidence_locators must be a tuple")
    canonical = tuple(sorted(set(values)))
    if not canonical:
        raise ValueError("at least one evidence locator is required")
    if len(canonical) != len(values):
        raise ValueError("evidence locators must be unique")
    for locator in canonical:
        _require_locator("evidence locator", locator)
    return canonical


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")
    if len(value) > 8_192:
        raise ValueError(f"{name} exceeds its text limit")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{name} contains control characters")
    return value


def _require_locator(name: str, value: object) -> str:
    text = _require_text(name, value)
    if len(text) > 4_096:
        raise ValueError(f"{name} exceeds its locator limit")
    return text


def _unit_float(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    resolved = float(value)
    if not math.isfinite(resolved) or not 0.0 <= resolved <= 1.0:
        raise ValueError(f"{name} must be a finite unit float")
    return resolved


def _non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _endpoint_state(endpoint: SemanticEdgeEndpoint) -> tuple[Any, ...]:
    return (
        endpoint.entity_id,
        endpoint.entity_type.value,
        endpoint.project_id,
        endpoint.repository_id,
        endpoint.generation_id,
        endpoint.stable_version,
        endpoint.acl_ref,
    )


def _candidate_identity(candidate: _EdgeCandidate) -> tuple[Any, ...]:
    return (
        candidate.edge_type.value,
        *_endpoint_state(candidate.source),
        *_endpoint_state(candidate.target),
    )


def _edge_identity(edge: SemanticEdge) -> tuple[Any, ...]:
    return (
        edge.edge_type.value,
        *_endpoint_state(edge.source),
        *_endpoint_state(edge.target),
    )


def _canonical_edge_hash(
    edge_type: CodeRelationType,
    source: SemanticEdgeEndpoint,
    target: SemanticEdgeEndpoint,
) -> str:
    payload = {
        "edge_type": edge_type.value,
        "source": _endpoint_state(source),
        "target": _endpoint_state(target),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _provenance_key(item: SemanticEdgeProvenance) -> tuple[str, str, str, str]:
    return (
        item.derivation.value,
        item.producer_version,
        item.artifact_id,
        item.content_sha256,
    )


def _candidate_sort_key(item: _EdgeCandidate) -> tuple[Any, ...]:
    return (
        _candidate_identity(item),
        tuple(_provenance_key(value) for value in item.provenances),
        item.evidence_locators,
        item.confidence,
    )


def _edge_sort_key(item: SemanticEdge) -> tuple[Any, ...]:
    return (_edge_identity(item), item.edge_id)


def _diagnostic_key(item: SemanticEdgeDiagnostic) -> tuple[Any, ...]:
    return (
        item.code.value,
        item.severity.value,
        item.edge_type.value if item.edge_type is not None else "",
        item.source_entity_id,
        item.target_entity_id,
        item.relative_path,
        item.symbol,
        item.candidate_ids,
        item.message,
    )


def _diagnostic_bytes(item: SemanticEdgeDiagnostic) -> bytes:
    return _canonical_json(_diagnostic_key(item))


def _conflict_key(item: SemanticEdgeConflict) -> tuple[Any, ...]:
    return (
        item.source_entity_id,
        item.scip_target_entity_id,
        item.scip_edge_type.value,
        item.retained_tree_edge_ids,
        item.conflict_id,
    )


def _result_hash(
    *,
    status: SemanticEdgeTreatmentStatus,
    support: SemanticEdgeSupportStatus,
    language: str,
    semantic_ready: bool,
    edges: tuple[SemanticEdge, ...],
    diagnostics: tuple[SemanticEdgeDiagnostic, ...],
    diagnostic_counts: tuple[SemanticEdgeDiagnosticCount, ...],
    diagnostics_truncated: int,
    conflicts: tuple[SemanticEdgeConflict, ...],
    trace: SemanticEdgeFallbackTrace,
    treatment_version: str,
) -> str:
    payload = {
        "status": status.value,
        "support": support.value,
        "language": language,
        "semantic_ready": semantic_ready,
        "edges": [
            {
                "id": edge.edge_id,
                "type": edge.edge_type.value,
                "source": _endpoint_state(edge.source),
                "target": _endpoint_state(edge.target),
                "confidence": edge.confidence,
                "provenances": [_provenance_key(item) for item in edge.provenances],
                "locators": edge.evidence_locators,
                "review": edge.review_status.value,
                "fact": edge.fact_status.value,
            }
            for edge in edges
        ],
        "diagnostics": [_diagnostic_key(item) for item in diagnostics],
        "diagnostic_counts": [(item.code.value, item.count) for item in diagnostic_counts],
        "diagnostics_truncated": diagnostics_truncated,
        "conflicts": [
            (
                item.conflict_id,
                item.source_entity_id,
                item.scip_target_entity_id,
                item.scip_edge_type.value,
                item.retained_tree_edge_ids,
                tuple(value.value for value in item.retained_tree_edge_types),
                item.evidence_locators,
                item.reason,
            )
            for item in conflicts
        ],
        "trace": (
            trace.scip_status.value,
            trace.scip_diagnostic_codes,
            trace.tree_sitter_input_count,
            trace.tree_sitter_retained_count,
            trace.scip_retained_count,
            trace.fallback_used,
            trace.reason,
        ),
        "treatment_version": treatment_version,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


__all__ = [
    "DEFAULT_MIN_SEMANTIC_EDGE_CONFIDENCE",
    "DEFAULT_SEMANTIC_EDGE_SAMPLE_SIZE",
    "SCIP_EXPLICIT_CALL_ROLE",
    "SCIP_EXPLICIT_OVERRIDE_ROLE",
    "SEMANTIC_EDGE_LABEL_PROTOCOL_VERSION",
    "SEMANTIC_EDGE_TREATMENT_VERSION",
    "ConservativeSemanticEdge",
    "ScipSemanticRole",
    "SemanticEdge",
    "SemanticEdgeConflict",
    "SemanticEdgeDiagnostic",
    "SemanticEdgeDiagnosticCode",
    "SemanticEdgeDiagnosticCount",
    "SemanticEdgeDiagnosticSeverity",
    "SemanticEdgeEndpoint",
    "SemanticEdgeEvaluation",
    "SemanticEdgeEvaluationStatus",
    "SemanticEdgeFallbackTrace",
    "SemanticEdgePrecisionLabel",
    "SemanticEdgePrecisionVerdict",
    "SemanticEdgeProvenance",
    "SemanticEdgeQualityMetrics",
    "SemanticEdgeScope",
    "SemanticEdgeStructuralCounts",
    "SemanticEdgeSupportStatus",
    "SemanticEdgeTreatment",
    "SemanticEdgeTreatmentResult",
    "SemanticEdgeTreatmentStatus",
    "SemanticScipStatus",
    "TreeSitterConservativeEdge",
    "evaluate_semantic_edges",
    "sample_semantic_edges",
    "treat_python_semantic_edges",
]
