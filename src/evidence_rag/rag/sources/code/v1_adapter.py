"""Strict Code Source contract adapter for the existing V1 hybrid retriever."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from ....models import EvidenceSearchRequest
from ....retrieval import (
    HybridRetriever,
    LegacyExecutionEvidenceError,
)
from .contracts import (
    CodeCandidateRole,
    CodeChannelCompleteNoMatch,
    CodeChannelCompletePruned,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeChannelRank,
    CodeChannelScore,
    CodeDerivation,
    CodeFactStatus,
    CodeQueryProfile,
    CodeRelationNode,
    CodeRelationPath,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeSourceResult,
    CodeSourceStatus,
    CodeTask,
    CodeUncalibratedScore,
    CodeVersionAlignment,
)

_LEGACY_CHANNELS = {
    "lexical": CodeRetrievalChannel.SPARSE,
    "dense": CodeRetrievalChannel.DENSE,
}
_DISABLED_CHANNEL_REASONS = {
    CodeRetrievalChannel.EXACT: "legacy v1 has no independent exact candidate channel",
    CodeRetrievalChannel.GRAPH: "legacy v1 edges do not generate candidates",
    CodeRetrievalChannel.HISTORY: "legacy v1 has no history candidate channel",
    CodeRetrievalChannel.TEST: "legacy v1 has no test candidate channel",
}


class LegacyCodeResponseError(ValueError):
    """Raised when a legacy response cannot be represented without weakening the contract."""


class CodeSourceRetrieverV1Adapter:
    """Wrap ``HybridRetriever`` and convert its result without changing V1 retrieval."""

    index_family = "raw-v1"

    def __init__(self, legacy: HybridRetriever) -> None:
        self.legacy = legacy

    def search(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> CodeSourceResult:
        """Execute V1 exactly once and adapt the resulting response."""

        legacy_response = self.legacy.search(request)
        return self.adapt_response(request, legacy_response, profile=profile)

    def profile_for_request(self, request: EvidenceSearchRequest) -> CodeQueryProfile:
        """Return the minimal truthful profile for V1 adapter validation."""

        target_ref = request.scope.commit or "current"
        return CodeQueryProfile(task=CodeTask.IMPLEMENTATION, target_ref=target_ref)

    def adapt_response(
        self,
        request: EvidenceSearchRequest,
        legacy_response: Mapping[str, Any],
        *,
        profile: CodeQueryProfile | None = None,
    ) -> CodeSourceResult:
        """Purely convert an already executed legacy response.

        ``profile`` is accepted so V1 and a future V2 can receive the same resolved
        routing input. V1 does not claim profile-driven channels that it did not run.
        """

        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        if profile is not None and not isinstance(profile, CodeQueryProfile):
            raise TypeError("profile must be a CodeQueryProfile")
        try:
            execution_evidence = self.legacy.verify_execution_evidence(
                request,
                legacy_response,
            )
        except LegacyExecutionEvidenceError as error:
            raise LegacyCodeResponseError(
                "legacy execution evidence verification failed"
            ) from error
        response = _require_mapping(legacy_response, "response")
        _validate_response_identity(request, response)

        results = _require_sequence(response.get("results"), "results")
        total = _require_int(response.get("total"), "total")
        if total != len(results):
            raise LegacyCodeResponseError("total must equal the number of legacy results")
        if total > request.limit:
            raise LegacyCodeResponseError("total must not exceed the requested limit")

        trace = _require_mapping(response.get("trace"), "trace")
        if _require_string(trace.get("fusion"), "trace.fusion") != "weighted-hybrid-v2":
            raise LegacyCodeResponseError("trace.fusion is not the supported legacy policy")
        latency_ms = _require_float(trace.get("duration_ms"), "trace.duration_ms")
        lexical_hit_count = _require_int(
            trace.get("lexical_candidates"), "trace.lexical_candidates"
        )
        dense_candidate_count = _require_int(
            trace.get("dense_candidates"), "trace.dense_candidates"
        )
        dense_hit_count = _require_int(trace.get("dense_matches"), "trace.dense_matches")
        if min(lexical_hit_count, dense_candidate_count, dense_hit_count) < 0:
            raise LegacyCodeResponseError("legacy trace counts must be non-negative")
        if dense_hit_count > dense_candidate_count:
            raise LegacyCodeResponseError("dense matches cannot exceed dense candidates")
        embedding_model = _require_string(trace.get("embedding_model"), "trace.embedding_model")

        generations = _require_unique_strings(response.get("index_generation"), "index_generation")
        if tuple(sorted(generations)) != generations:
            raise LegacyCodeResponseError("index_generation must be sorted")

        validated_items = [
            _validate_legacy_item(request, item, generations, index)
            for index, item in enumerate(results)
        ]
        entity_ids = [item["entity_id"] for item in validated_items]
        if len(entity_ids) != len(set(entity_ids)):
            raise LegacyCodeResponseError("legacy results must not repeat an entity")
        scores = [item["score"] for item in validated_items]
        if scores != sorted(scores, reverse=True):
            raise LegacyCodeResponseError("legacy results must be ordered by descending score")

        item_generations = tuple(sorted({item["generation_id"] for item in validated_items}))
        if item_generations != generations:
            raise LegacyCodeResponseError(
                "index_generation must exactly cover returned result generations"
            )

        evidence_by_channel = {channel.channel: channel for channel in execution_evidence.channels}
        if set(evidence_by_channel) != set(_LEGACY_CHANNELS):
            raise LegacyCodeResponseError("legacy execution evidence channels are incomplete")
        if lexical_hit_count != evidence_by_channel["lexical"].hit_count:
            raise LegacyCodeResponseError("lexical trace count conflicts with execution evidence")
        if dense_hit_count != evidence_by_channel["dense"].hit_count:
            raise LegacyCodeResponseError("dense trace count conflicts with execution evidence")
        raw_ranks_by_entity = {
            entity_id: {
                _LEGACY_CHANNELS[channel_name]: raw_rank
                for channel_name, evidence in evidence_by_channel.items()
                for ranked_entity_id, raw_rank in evidence.returned_ranks
                if ranked_entity_id == entity_id
            }
            for entity_id in entity_ids
        }
        returned_by_channel = {
            channel: sum(channel in item["mapped_channels"] for item in validated_items)
            for channel in (CodeRetrievalChannel.SPARSE, CodeRetrievalChannel.DENSE)
        }
        if returned_by_channel[CodeRetrievalChannel.SPARSE] > lexical_hit_count:
            raise LegacyCodeResponseError(
                "lexical hit count cannot be below returned sparse candidates"
            )
        if returned_by_channel[CodeRetrievalChannel.DENSE] > dense_hit_count:
            raise LegacyCodeResponseError(
                "dense hit count cannot be below returned dense candidates"
            )

        candidates = tuple(
            _candidate_from_item(
                request,
                item,
                source_rank=source_rank,
                raw_ranks=raw_ranks_by_entity[item["entity_id"]],
            )
            for source_rank, item in enumerate(validated_items)
        )
        channel_outcomes = _channel_outcomes(
            returned_by_channel,
            lexical_hit_count=lexical_hit_count,
            dense_hit_count=dense_hit_count,
        )
        query_id = _require_string(response.get("query_id"), "query_id")
        watermark = f"commit:{request.scope.commit}" if request.scope.commit else "current"
        return CodeSourceResult(
            query_id=query_id,
            status=CodeSourceStatus.COMPLETE,
            channel_outcomes=channel_outcomes,
            candidates=candidates,
            context_blocks=(),
            index_version=f"legacy-weighted-hybrid-v2:{embedding_model}",
            watermark=watermark,
            latency_ms=latency_ms,
            errors=(),
        )

    @classmethod
    def retrieval_unit_id(
        cls,
        *,
        entity_id: str,
        repository_id: str,
        stable_version: str,
        source_generation: str,
        view_type: str,
        locator: str,
    ) -> str:
        """Build the deterministic identity of a rebuildable raw-V1 view."""

        identity = {
            "entity_id": entity_id,
            "repository_id": repository_id,
            "stable_version": stable_version,
            "source_generation": source_generation,
            "view_type": view_type,
            "locator": locator,
        }
        if any(type(value) is not str or not value for value in identity.values()):
            raise TypeError("raw-v1 unit identity fields must be non-empty strings")
        canonical = {key: unicodedata.normalize("NFC", value) for key, value in identity.items()}
        payload = json.dumps(
            {
                "builder": cls.index_family,
                **canonical,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        return f"code-unit://raw-v1/sha256/{digest}"


def _candidate_from_item(
    request: EvidenceSearchRequest,
    item: dict[str, Any],
    *,
    source_rank: int,
    raw_ranks: dict[CodeRetrievalChannel, int],
) -> CodeRetrievalCandidate:
    raw_scores = []
    raw_rank_entries = []
    for legacy_channel in item["channels"]:
        channel = _LEGACY_CHANNELS[legacy_channel]
        score_field = "lexical_score" if legacy_channel == "lexical" else "dense_score"
        raw_scores.append(CodeChannelScore(channel=channel, score=item[score_field]))
        raw_rank_entries.append(CodeChannelRank(channel=channel, rank=raw_ranks[channel]))

    version_alignment = (
        CodeVersionAlignment.COMPATIBLE
        if request.scope.commit is None
        else (
            CodeVersionAlignment.EXACT
            if item["commit"] == request.scope.commit
            else CodeVersionAlignment.MISMATCH
        )
    )
    unit_id = CodeSourceRetrieverV1Adapter.retrieval_unit_id(
        entity_id=item["entity_id"],
        repository_id=item["repository_id"],
        stable_version=item["commit"],
        source_generation=item["generation_id"],
        view_type=item["view_type"],
        locator=item["evidence_locator"],
    )
    path = CodeRelationPath(
        nodes=(
            CodeRelationNode(
                entity_id=item["entity_id"],
                repository_id=item["repository_id"],
                stable_version=item["commit"],
                source_generation=item["generation_id"],
                locator=item["evidence_locator"],
                acl_ref=item["acl_ref"],
            ),
        ),
        edges=(),
        path_score=0.0,
    )
    return CodeRetrievalCandidate(
        entity_id=item["entity_id"],
        retrieval_unit_id=unit_id,
        repository_id=item["repository_id"],
        entity_type=item["entity_type"],
        stable_version=item["commit"],
        source_generation=item["generation_id"],
        raw_channel_scores=tuple(raw_scores),
        raw_channel_ranks=tuple(raw_rank_entries),
        within_source_rank=source_rank,
        source_fused_score=item["score"],
        calibrated_relevance=CodeUncalibratedScore(
            status="disabled",
            reason="legacy v1 has no calibrated relevance artifact",
        ),
        version_alignment=version_alignment,
        fact_status=CodeFactStatus.OBSERVED,
        derivation=CodeDerivation.RAW_SOURCE,
        review_status=CodeReviewStatus.UNREVIEWED,
        role=CodeCandidateRole.TARGET,
        relation_path=path,
        locator=item["evidence_locator"],
        token_estimate=(len(item["snippet"]) + 3) // 4,
        acl_ref=item["acl_ref"],
    )


def _channel_outcomes(
    returned_by_channel: dict[CodeRetrievalChannel, int],
    *,
    lexical_hit_count: int,
    dense_hit_count: int,
) -> tuple[
    CodeChannelDisabled
    | CodeChannelCompleteNoMatch
    | CodeChannelCompletePruned
    | CodeChannelCompleteWithHits,
    ...,
]:
    outcomes = []
    hit_counts = {
        CodeRetrievalChannel.SPARSE: lexical_hit_count,
        CodeRetrievalChannel.DENSE: dense_hit_count,
    }
    for channel in CodeRetrievalChannel:
        if channel in _DISABLED_CHANNEL_REASONS:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason=_DISABLED_CHANNEL_REASONS[channel],
                )
            )
        elif returned_by_channel[channel]:
            outcomes.append(
                CodeChannelCompleteWithHits(
                    channel=channel,
                    hit_count=hit_counts[channel],
                )
            )
        elif hit_counts[channel]:
            outcomes.append(
                CodeChannelCompletePruned(
                    channel=channel,
                    hit_count=hit_counts[channel],
                )
            )
        else:
            outcomes.append(CodeChannelCompleteNoMatch(channel=channel))
    return tuple(outcomes)


def _validate_response_identity(
    request: EvidenceSearchRequest,
    response: Mapping[str, Any],
) -> None:
    if _require_string(response.get("query"), "query") != request.query:
        raise LegacyCodeResponseError("legacy response query does not match the request")
    actual_scope = _plain_json(_require_mapping(response.get("resolved_scope"), "resolved_scope"))
    expected_scope = request.scope.model_dump(mode="python")
    if actual_scope != expected_scope:
        raise LegacyCodeResponseError("legacy resolved_scope does not match the request")


def _validate_legacy_item(
    request: EvidenceSearchRequest,
    value: object,
    generations: tuple[str, ...],
    index: int,
) -> dict[str, Any]:
    item = _require_mapping(value, f"results[{index}]")
    required_strings = (
        "entity_id",
        "repository_id",
        "generation_id",
        "entity_type",
        "view_type",
        "path",
        "commit",
        "evidence_locator",
        "acl_ref",
        "snippet",
    )
    validated = {
        field: _require_string(item.get(field), f"results[{index}].{field}")
        for field in required_strings
    }
    validated["score"] = _require_float(item.get("score"), f"results[{index}].score")
    validated["lexical_score"] = _require_float(
        item.get("lexical_score"), f"results[{index}].lexical_score"
    )
    validated["dense_score"] = _require_float(
        item.get("dense_score"), f"results[{index}].dense_score"
    )
    if (
        min(
            validated["score"],
            validated["lexical_score"],
            validated["dense_score"],
        )
        < 0.0
    ):
        raise LegacyCodeResponseError("legacy result scores must be non-negative")

    channels = _require_unique_strings(item.get("channels"), f"results[{index}].channels")
    if not channels or not set(channels) <= set(_LEGACY_CHANNELS):
        raise LegacyCodeResponseError("legacy result channels are unsupported or empty")
    if tuple(sorted(channels)) != channels:
        raise LegacyCodeResponseError("legacy result channels must be sorted")
    if ("lexical" in channels) != (validated["lexical_score"] > 0.0):
        raise LegacyCodeResponseError("legacy lexical channel and score disagree")
    if ("dense" in channels) != (validated["dense_score"] > 0.0):
        raise LegacyCodeResponseError("legacy dense channel and score disagree")
    validated["channels"] = channels
    validated["mapped_channels"] = tuple(_LEGACY_CHANNELS[channel] for channel in channels)

    if validated["generation_id"] not in generations:
        raise LegacyCodeResponseError("result generation is absent from index_generation")
    if (
        request.scope.repository_ids
        and validated["repository_id"] not in request.scope.repository_ids
    ):
        raise LegacyCodeResponseError("legacy result is outside the repository scope")
    if request.scope.enforce_acl:
        allowed_acl_refs = set(request.scope.allowed_acl_refs) | {"public"}
        if validated["acl_ref"] not in allowed_acl_refs:
            raise LegacyCodeResponseError("legacy result is outside the authorized ACL scope")
    return validated


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LegacyCodeResponseError(f"{field} must be a mapping")
    if not all(type(key) is str for key in value):
        raise LegacyCodeResponseError(f"{field} keys must be strings")
    return value


def _require_sequence(value: object, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise LegacyCodeResponseError(f"{field} must be a sequence")
    return value


def _require_unique_strings(value: object, field: str) -> tuple[str, ...]:
    values = _require_sequence(value, field)
    result = tuple(_require_string(item, f"{field}[]") for item in values)
    if len(result) != len(set(result)):
        raise LegacyCodeResponseError(f"{field} must not contain duplicates")
    return result


def _require_string(value: object, field: str) -> str:
    if type(value) is not str or not value:
        raise LegacyCodeResponseError(f"{field} must be a non-empty string")
    return value


def _require_int(value: object, field: str) -> int:
    if type(value) is not int:
        raise LegacyCodeResponseError(f"{field} must be an exact integer")
    return value


def _require_float(value: object, field: str) -> float:
    if type(value) is not float:
        raise LegacyCodeResponseError(f"{field} must be an exact float")
    if value != value or value in {float("inf"), float("-inf")}:
        raise LegacyCodeResponseError(f"{field} must be finite")
    return 0.0 if value == 0.0 else value


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain_json(item) for item in value]
    return value
