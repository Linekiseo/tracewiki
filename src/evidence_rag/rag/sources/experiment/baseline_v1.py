"""Prepared-only Experiment E-B0 current-V1 baseline harness.

The Experiment Foundation Gate authorizes preparation of this harness only.
It does not authorize a released-45 execution.  Production execution remains
behind a separate, future, canonical Harness Gate.  All admission and
authority checks fail closed before creating a temp directory, opening
SQLite, calling production services, or writing an artifact.
"""

from __future__ import annotations

import builtins
import dis
import hashlib
import importlib
import inspect
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
import types
import typing
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic_core import PydanticUndefined

import evidence_rag.experiments.adapters as experiment_adapters_public
import evidence_rag.experiments.adapters.mlflow as mlflow_module
import evidence_rag.experiments.models as experiment_models_public
import evidence_rag.experiments.service as experiment_service_public
import evidence_rag.platform.models as platform_models_public
import evidence_rag.platform.service as platform_service_public
import evidence_rag.platform.store as platform_store_public
from evidence_rag.code_identity_v1 import (
    CodeIdentityError,
    portable_code_payload,
    portable_constant_payload,
    portable_function_payload,
    portable_stdlib_object_identity,
)
from evidence_rag.config import Settings
from evidence_rag.experiments.adapters.mlflow import MLflowAdapter
from evidence_rag.experiments.models import (
    ArtifactInput,
    ComparisonRequest,
    ExperimentCreate,
    MetricInput,
    MLflowSyncRequest,
    RunCreate,
)
from evidence_rag.experiments.service import ExperimentService
from evidence_rag.platform.models import GlobalSearchRequest
from evidence_rag.platform.service import PlatformService
from evidence_rag.platform.store import PlatformStore
from evidence_rag.runtime import create_runtime
from evidence_rag.workspace.models import ProjectCreate

from .evaluation_v1 import (
    EXPECTED_SLICE_COUNTS,
    EXPERIMENT_GOLDEN_AUTHORITY_HASH,
    EXPERIMENT_GOLDEN_CASE_COUNT,
    EXPERIMENT_GOLDEN_DATASET_ID,
    EXPERIMENT_GOLDEN_DATASET_VERSION,
    EXPERIMENT_GOLDEN_PACKAGE_HASH,
    Answerability,
    ComparabilityTruth,
    EntityKind,
    ExperimentEvaluationMetric,
    ExperimentGoldenDataset,
    MetricTruth,
    ReproductionTruth,
    ReviewedCandidate,
    ReviewedExperimentCaseRow,
    ReviewedOutcome,
    ReviewedReason,
    TypedPredicate,
    evaluate_reviewed_experiment_retrieval,
    load_experiment_golden_v1,
)
from .fixture_v1 import (
    _FIXED_CURRENT_PRODUCTION_AUTHORITY_ROWS,
    _FIXED_PRODUCTION_AUTHORITY_ROWS,
    EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST,
    EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST,
    EXPERIMENT_PRODUCTION_PARITY_HASH,
    CurrentProductionComponentAuthority,
    ExperimentFixtureRecipe,
    FixtureMetricDirection,
    FixtureRun,
    deterministic_experiment_recipe,
    materialize_mlflow_filestore,
    verify_production_component_authority,
)

EXPERIMENT_BASELINE_SCHEMA_VERSION = "experiment-e-b0-preparation-v1"
EXPERIMENT_BASELINE_RUNNER_VERSION = "experiment-e-b0-current-v1-runner-v1"
EXPERIMENT_BASELINE_ARTIFACT_VERSION = "experiment-e-b0-portable-artifact-v1"
EXPERIMENT_BASELINE_VERIFICATION_VERSION = "experiment-e-b0-verify-only-v1"
EXPERIMENT_BASELINE_PATH_POLICY_VERSION = "experiment-e-b0-temp-path-policy-v1"
EXPERIMENT_BASELINE_EXECUTION_STATUS = "NOT_AUTHORIZED"
EXPERIMENT_BASELINE_GATE_STATUS = "PREPARATION_AUTHORIZED"
EXPERIMENT_BASELINE_SMOKE_STATUS = "SMOKE_UNAVAILABLE"
EXPERIMENT_BASELINE_QUALIFICATION_STATUS = "NON_QUALIFIED"
EXPERIMENT_BASELINE_SEED = 20260729

EXPERIMENT_FOUNDATION_TASK_ID = "019f9f27-8d88-7b01-b686-3d4acbc5dcf2"
EXPERIMENT_HARNESS_GATE_TASK_ID = "019fac2b-517a-79b3-8c1a-3a0625cd7ee5"
EXPERIMENT_FOUNDATION_RELEASE_ID = "release-e7de1be4caf88af99c3b36de7e13c4a2"
EXPERIMENT_FOUNDATION_RECIPE_HASH = (
    "sha256:4ff2eff2b2261f80f775d9ba6b8cce80e6b56972de459b50c1c198cc0d2fea97"
)
EXPERIMENT_BASELINE_PRODUCTION_AUTHORITY_VERSION = (
    "experiment-e-b0-current-v1-production-authority-v2"
)
EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST = (
    "sha256:689b77964fb99fc72f7f93631eba0063dab8af4ab8e0d121364ee0df7fd5f845"
)
EXPERIMENT_BASELINE_CURRENT_PRODUCTION_AUTHORITY_VERSION = (
    "experiment-e-b0-current-production-authority-v8"
)
EXPERIMENT_BASELINE_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST = (
    "sha256:17e71e271058132fce73d1ac8bc948c2bd033d9dbe69634a0008aa7bd2b220ae"
)
EXPERIMENT_HARNESS_GATE_DECISION_DIGEST = (
    "sha256:939130c475ae005f797ccded56e36998891a55ce6b5e2574cec9180b5bf3791e"
)
EXPERIMENT_BASELINE_PREPARATION_DIGEST = (
    "sha256:5b407c7bd9e86cce4521aeacffbe6f5f2824b88ed6799792d38fb5dd59dd4179"
)
_FAILED_HARNESS_GATE_COMPONENT_SET_DIGEST = (
    "sha256:4c776f72bfa1f383a71eb64e1804077c4d659d0f0624e53b91f5be0c741bb808"
)

EXPERIMENT_BASELINE_ARTIFACT_FILES = (
    "manifest.json",
    "golden_cases.jsonl",
    "predictions.jsonl",
    "comparison_results.jsonl",
    "metrics.json",
    "slice_report.json",
    "errors.jsonl",
    "latency.json",
    "security_report.json",
    "checksums.json",
)
EXPERIMENT_BASELINE_CHECKSUM_TARGETS = EXPERIMENT_BASELINE_ARTIFACT_FILES[:-1]

EXPERIMENT_FOUNDATION_SLICE_COUNTS: Mapping[str, int] = {
    "exact": 5,
    "structured_filter": 7,
    "metric_numeric": 8,
    "comparison": 8,
    "reproduction": 6,
    "failure_status": 4,
    "aggregation_trend": 4,
    "unanswerable_incomparable_acl": 3,
}
EXPERIMENT_FOUNDATION_ELIGIBLE_DENOMINATORS: Mapping[str, int] = {
    "exact_entity_accuracy": 5,
    "predicate_accuracy": 28,
    "numeric_unit_direction_accuracy": 20,
    "comparability_accuracy": 9,
    "reproduction_accuracy": 6,
    "hard_negative_avoidance": 45,
    "unanswerable_accuracy": 10,
    "zero_result_accuracy": 2,
}
EXPERIMENT_FOUNDATION_LOGICAL_COUNTS: Mapping[str, int] = {
    "experiments": 1,
    "runs": 12,
    "metric_definitions": 3,
    "metric_series": 18,
    "metric_observations": 47,
    "artifacts": 2,
}

_FOUNDATION_GATE_RELATIVE_PATH = Path(
    "docs/rag-optimization/development/reviews/11_EXPERIMENT_E0_FOUNDATION_GATE_REVIEW.md"
)
_HARNESS_GATE_RELATIVE_PATH = Path(
    "docs/rag-optimization/development/reviews/12_EXPERIMENT_EB0_HARNESS_GATE_REVIEW.md"
)

_IDENTIFIER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_FILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_CREDENTIAL_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s\"']{8,})|"
    r"SECRET_SENTINEL|EXPERIMENT_FIXTURE_SECRET)"
)
_POSIX_ABSOLUTE_RE = re.compile(
    r"(?:^|[\s\"'(])/(?:Users|home|private|tmp|var|opt|etc|root|mnt|Volumes)(?:/|$)"
)
_GENERIC_POSIX_ABSOLUTE_RE = re.compile(r"(?:^|[\s\"'(=])/(?!/)[^\s\"']+")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?:^|[\s\"'(])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])")
_PAYMENT_RE = re.compile(r"(?<![A-Za-z0-9])(?:\d[ ()-]?){13,19}(?![A-Za-z0-9])")
_UUID7_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _validate_identifier(value: str) -> str:
    if value != value.strip() or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError("identifier is not canonical")
    return value


def _validate_sha256(value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("expected sha256:<lowercase-hex>")
    return value


def _validate_contract_text(value: str) -> str:
    if value != value.strip() or _CONTROL_RE.search(value):
        raise ValueError("contract text must be trimmed and free of control characters")
    return value


def _validate_artifact_file(value: str) -> str:
    if (
        value != value.strip()
        or "/" in value
        or "\\" in value
        or _SAFE_FILE_RE.fullmatch(value) is None
        or value.endswith((".sqlite3", "-wal", "-shm", ".pyc"))
    ):
        raise ValueError("artifact filename is not portable")
    return value


Identifier = Annotated[StrictStr, AfterValidator(_validate_identifier)]
Sha256 = Annotated[StrictStr, AfterValidator(_validate_sha256)]
ContractText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=12_000),
    AfterValidator(_validate_contract_text),
]
ArtifactFile = Annotated[StrictStr, AfterValidator(_validate_artifact_file)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
NonNegativeFiniteFloat = Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]


class ExperimentBaselineError(ValueError):
    """Base error for E-B0 preparation, runner, and artifact contracts."""


class ExperimentBaselineNotAuthorized(ExperimentBaselineError):
    """Raised before side effects when the independent Harness Gate is absent."""


class ExperimentBaselineGateError(ExperimentBaselineError):
    """Raised when a Gate document is copied, historical, forged, or incomplete."""


class ExperimentBaselineAuthorityError(ExperimentBaselineError):
    """Raised when Foundation or current production identity differs from review."""


class ExperimentBaselinePathError(ExperimentBaselineError):
    """Raised when explicit isolated paths are unsafe or reusable."""


class ExperimentBaselineArtifactError(ExperimentBaselineError):
    """Raised when the portable artifact is incomplete, unsafe, or inconsistent."""


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))

    def canonical_sha256(self) -> str:
        return _sha256(self.canonical_json_bytes())

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        del deep
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class ExperimentFoundationAuthority(_FrozenContract):
    schema_version: Literal["experiment-e-b0-foundation-authority-v1"] = (
        "experiment-e-b0-foundation-authority-v1"
    )
    dataset_id: Literal["experiment-golden-v1"] = EXPERIMENT_GOLDEN_DATASET_ID
    dataset_version: Literal["experiment-golden-v1"] = EXPERIMENT_GOLDEN_DATASET_VERSION
    release_record_id: Literal["release-e7de1be4caf88af99c3b36de7e13c4a2"] = (
        EXPERIMENT_FOUNDATION_RELEASE_ID
    )
    package_hash: Literal[
        "sha256:e7de1be4caf88af99c3b36de7e13c4a2ba3d15d4e40e5e5e0459ecf3d583a81f"
    ] = EXPERIMENT_GOLDEN_PACKAGE_HASH
    authority_hash: Literal[
        "sha256:cad053ebb0686c6a413a743fe9d0e142f2d61546cb18a2bb0d2a9b82af8d29e1"
    ] = EXPERIMENT_GOLDEN_AUTHORITY_HASH
    recipe_hash: Literal[
        "sha256:4ff2eff2b2261f80f775d9ba6b8cce80e6b56972de459b50c1c198cc0d2fea97"
    ] = EXPERIMENT_FOUNDATION_RECIPE_HASH
    component_hash: Literal[
        "sha256:0c961eb90c9a30795debc25d4d9c04eb6f38d4214a2ae3d71f03f54f812f405d"
    ] = EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST
    parity_hash: Literal[
        "sha256:df0d6a7f745bfda76bb4e68ce52063c0010ef474ee8afe584d4f20e2fd92ceb9"
    ] = EXPERIMENT_PRODUCTION_PARITY_HASH
    case_count: Literal[45] = EXPERIMENT_GOLDEN_CASE_COUNT
    membership: tuple[Identifier, ...] = tuple(
        f"experiment-v1-{ordinal:03d}" for ordinal in range(1, 46)
    )
    slice_counts: dict[Identifier, PositiveInt] = dict(EXPERIMENT_FOUNDATION_SLICE_COUNTS)
    eligible_denominators: dict[Identifier, PositiveInt] = dict(
        EXPERIMENT_FOUNDATION_ELIGIBLE_DENOMINATORS
    )
    logical_counts: dict[Identifier, PositiveInt] = dict(EXPERIMENT_FOUNDATION_LOGICAL_COUNTS)

    @model_validator(mode="after")
    def _exact_foundation(self) -> Self:
        if self.membership != tuple(f"experiment-v1-{ordinal:03d}" for ordinal in range(1, 46)):
            raise ValueError("Foundation ordered45 membership drift")
        if self.slice_counts != dict(EXPERIMENT_FOUNDATION_SLICE_COUNTS):
            raise ValueError("Foundation slice counts drift")
        if self.eligible_denominators != dict(EXPERIMENT_FOUNDATION_ELIGIBLE_DENOMINATORS):
            raise ValueError("Foundation eligible denominators drift")
        if self.logical_counts != dict(EXPERIMENT_FOUNDATION_LOGICAL_COUNTS):
            raise ValueError("Foundation logical counts drift")
        return self


class ExperimentFoundationGateDecision(_FrozenContract):
    schema_version: Literal["experiment-e0-foundation-gate-decision-v1"] = (
        "experiment-e0-foundation-gate-decision-v1"
    )
    source_task_id: Literal["019f9f27-8d88-7b01-b686-3d4acbc5dcf2"] = EXPERIMENT_FOUNDATION_TASK_ID
    conclusion: Literal["EXPERIMENT E0-01 FOUNDATION PASS"] = "EXPERIMENT E0-01 FOUNDATION PASS"
    p0_findings: Literal[0] = 0
    p1_findings: Literal[0] = 0
    preparation_authorization: Literal["E-B0 HARNESS PREPARATION AUTHORIZED"] = (
        "E-B0 HARNESS PREPARATION AUTHORIZED"
    )
    production_execution: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    e1_status: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    authority: ExperimentFoundationAuthority = Field(default_factory=ExperimentFoundationAuthority)
    decision_digest: Sha256


class ExperimentHarnessGateDecision(_FrozenContract):
    schema_version: Literal["experiment-e-b0-harness-gate-decision-v1"] = (
        "experiment-e-b0-harness-gate-decision-v1"
    )
    harness_task_id: Literal["019fac2b-517a-79b3-8c1a-3a0625cd7ee5"] = (
        EXPERIMENT_HARNESS_GATE_TASK_ID
    )
    conclusion: Literal[
        "EXPERIMENT E-B0 HARNESS PASS",
        "EXPERIMENT E-B0 HARNESS FAIL",
    ]
    p0_findings: Literal[0] = 0
    p1_findings: Literal[0, 2]
    run_authorization: Literal[
        "E-B0 ISOLATED PRODUCTION RUN AUTHORIZED",
        "E-B0 ISOLATED PRODUCTION RUN NOT AUTHORIZED",
    ]
    authorized: StrictBool
    e1_status: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    foundation_authority: ExperimentFoundationAuthority = Field(
        default_factory=ExperimentFoundationAuthority
    )
    production_component_hash: Sha256
    artifact_version: Literal["experiment-e-b0-portable-artifact-v1"] = (
        EXPERIMENT_BASELINE_ARTIFACT_VERSION
    )
    decision_digest: Sha256

    @model_validator(mode="after")
    def _exact_review_outcome(self) -> Self:
        if _UUID7_RE.fullmatch(self.harness_task_id) is None:
            raise ValueError("Harness Gate task must be the reviewed canonical UUIDv7")
        passed = (
            self.conclusion == "EXPERIMENT E-B0 HARNESS PASS"
            and self.p1_findings == 0
            and self.run_authorization == "E-B0 ISOLATED PRODUCTION RUN AUTHORIZED"
            and self.authorized is True
            and self.production_component_hash
            == EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST
        )
        failed = (
            self.conclusion == "EXPERIMENT E-B0 HARNESS FAIL"
            and self.p1_findings == 2
            and self.run_authorization == "E-B0 ISOLATED PRODUCTION RUN NOT AUTHORIZED"
            and self.authorized is False
            and self.production_component_hash == _FAILED_HARNESS_GATE_COMPONENT_SET_DIGEST
        )
        if not (passed or failed):
            raise ValueError("Harness Gate conclusion/authorization identity is inconsistent")
        return self


class ExperimentBaselineComponentIdentity(_FrozenContract):
    role: Identifier
    kind: Literal["class", "method"]
    module: ContractText
    public_module: ContractText
    export: ContractText
    qualname: ContractText
    version: Identifier
    source_sha256: Sha256
    code_sha256: Sha256
    bindings_sha256: Sha256


_FIXED_CURRENT_V1_ADDITIONAL_ROWS: tuple[dict[str, str], ...] = (
    {
        "role": "experiment-compare-model",
        "kind": "class",
        "module": "evidence_rag.experiments.models",
        "public_module": "evidence_rag.experiments.models",
        "export": "ComparisonRequest",
        "qualname": "ComparisonRequest",
        "version": "experiment-public-model-v1",
        "source_sha256": (
            "sha256:a4f248097beaee94cc05f3b822a699fb79fa2451bef4dd0e6b2734b04f8a833a"
        ),
        "code_sha256": ("sha256:a98cd0deffd1fd428d32519d43c3ed6fe83cb32ff490baf14e237569c5b68936"),
    },
    {
        "role": "experiment-compare",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService.compare",
        "qualname": "ExperimentService.compare",
        "version": "experiment-service-v1",
        "source_sha256": (
            "sha256:a43fa280066f5128625cae60649f86acf451b7fe14d03439ce85e1e3b4927ec0"
        ),
        "code_sha256": ("sha256:f2e686d8e94659dd17b9dcebabf0261393563e8eed80a31b67c599a3e2338651"),
    },
    {
        "role": "experiment-controls",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService._controls",
        "qualname": "ExperimentService._controls",
        "version": "experiment-service-v1",
        "source_sha256": (
            "sha256:ecbf06756fc932b16cedacc08572186fb048b311689f212f4bc2ad9c5e3ef320"
        ),
        "code_sha256": ("sha256:70d3fd0dc84f07ef20a870a135be6abb8079ee0b95f6036f1fefeefe4617e762"),
    },
    {
        "role": "experiment-config-differences",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService._config_differences",
        "qualname": "ExperimentService._config_differences",
        "version": "experiment-service-v1",
        "source_sha256": (
            "sha256:1d0388db10f96f7f61ea0cb7f9f28d8a4820247fd78b71bd264b3cd04bc6aafc"
        ),
        "code_sha256": ("sha256:6b41657494c093dd5c3cdca35ed5a60cbed4aa6e4020a4b87ad7b35816dd28c0"),
    },
    {
        "role": "experiment-metric-differences",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService._metric_differences",
        "qualname": "ExperimentService._metric_differences",
        "version": "experiment-service-v1",
        "source_sha256": (
            "sha256:f063c0ef655b8d887b1242e729a98a503460973f10e77b88620b637f74a3507c"
        ),
        "code_sha256": ("sha256:3b1efb1ee99e04c542a33c27df699fff5103fc83c77890969c305d5a795aa680"),
    },
    {
        "role": "platform-search-model",
        "kind": "class",
        "module": "evidence_rag.platform.models",
        "public_module": "evidence_rag.platform.models",
        "export": "GlobalSearchRequest",
        "qualname": "GlobalSearchRequest",
        "version": "platform-public-model-v1",
        "source_sha256": (
            "sha256:1ad9cdf539f12a781766c4de0befb1942acd444f3127a65b04f28ac2e38a7397"
        ),
        "code_sha256": ("sha256:bbaee1d953b6febaaae6882324efddc0d58128464599aca3f02ad61c65c2e7d9"),
    },
    {
        "role": "platform-service-class",
        "kind": "class",
        "module": "evidence_rag.platform.service",
        "public_module": "evidence_rag.platform.service",
        "export": "PlatformService",
        "qualname": "PlatformService",
        "version": "platform-service-current-v1",
        "source_sha256": (
            "sha256:57558d143770c635fe1876958ea6579b11d6d35038e17e2aa7eba90c6d31ef74"
        ),
        "code_sha256": ("sha256:bfea3ec0fa3c0af43c6674d6890a1107ad65669d75c2635afe954cf40d452846"),
    },
    {
        "role": "platform-service-search",
        "kind": "method",
        "module": "evidence_rag.platform.service",
        "public_module": "evidence_rag.platform.service",
        "export": "PlatformService.search",
        "qualname": "PlatformService.search",
        "version": "platform-service-current-v1",
        "source_sha256": (
            "sha256:e41d7274b9570dd6ad6b69a1b9701801ade209ffa659748c56aeeead3e2369f2"
        ),
        "code_sha256": ("sha256:5189995cf0db87ff1c3e0ae784ec559102b4a8ef78e81752d6083d74b65aab22"),
    },
    {
        "role": "platform-service-in-scope",
        "kind": "method",
        "module": "evidence_rag.platform.service",
        "public_module": "evidence_rag.platform.service",
        "export": "PlatformService._in_scope",
        "qualname": "PlatformService._in_scope",
        "version": "platform-service-current-v1",
        "source_sha256": (
            "sha256:9af1cfe9ef34b0001c23ec6a49690ba3f709cf047b2db8c1e4174ad79c58a5b1"
        ),
        "code_sha256": ("sha256:f2e67a334edf911a5f708aa5a2f09e684e4d299b0d26b192c08bb98989fe690f"),
    },
    {
        "role": "platform-service-deduplicate",
        "kind": "method",
        "module": "evidence_rag.platform.service",
        "public_module": "evidence_rag.platform.service",
        "export": "PlatformService._deduplicate",
        "qualname": "PlatformService._deduplicate",
        "version": "platform-service-current-v1",
        "source_sha256": (
            "sha256:a98295f240dbfb4c51b5990bc0e073d14a3db504059652cd94daf8d3980d4422"
        ),
        "code_sha256": ("sha256:77504b34b4409653a9b3d3a43467b2e48f82226ffb83c261fcaacf4b52a615df"),
    },
    {
        "role": "platform-service-diversify",
        "kind": "method",
        "module": "evidence_rag.platform.service",
        "public_module": "evidence_rag.platform.service",
        "export": "PlatformService._diversify",
        "qualname": "PlatformService._diversify",
        "version": "platform-service-current-v1",
        "source_sha256": (
            "sha256:51d244ad5c8a4c61fe77912c9b072d9ce9be3e9db4ce8fb387efedfa343ddf0d"
        ),
        "code_sha256": ("sha256:6259c4c5643e4bca96a75c78e4de95b99f35250712bd13905fa30e7292705935"),
    },
    {
        "role": "platform-store-class",
        "kind": "class",
        "module": "evidence_rag.platform.store",
        "public_module": "evidence_rag.platform.store",
        "export": "PlatformStore",
        "qualname": "PlatformStore",
        "version": "platform-store-structured-v1",
        "source_sha256": (
            "sha256:dc59b972a6193b2c4c6bc44c4025bcd367ee0b3be0326b3280c9ce1dece53cfe"
        ),
        "code_sha256": ("sha256:37f2541026cc328e68d46dabf0b5ff08f0251edb1b9ff4daa18d940e2765c305"),
    },
    {
        "role": "platform-store-structured-search",
        "kind": "method",
        "module": "evidence_rag.platform.store",
        "public_module": "evidence_rag.platform.store",
        "export": "PlatformStore.structured_search",
        "qualname": "PlatformStore.structured_search",
        "version": "platform-store-structured-v1",
        "source_sha256": (
            "sha256:40bed17071a75a85e4b0a25d70daff3d2fabf5b9de2f022dbe35870fa05d21c1"
        ),
        "code_sha256": ("sha256:fbd3605f4a773fd2acf393f103df3ef8444d035efca614f3d7f1be189414251b"),
    },
    {
        "role": "platform-store-query-terms",
        "kind": "method",
        "module": "evidence_rag.platform.store",
        "public_module": "evidence_rag.platform.store",
        "export": "PlatformStore._query_terms",
        "qualname": "PlatformStore._query_terms",
        "version": "platform-store-token-substring-v1",
        "source_sha256": (
            "sha256:1a6a1819f005da1c41b735174ae5bf9a311f7f09a95797ff816dd43ecb76340a"
        ),
        "code_sha256": ("sha256:ebdbab3d7d1406d9ef835a76992e77cbb2247cf457517fdb6cbba34c46cf298d"),
    },
)

_FIXED_CURRENT_V1_BINDING_DIGESTS: Mapping[str, str] = {
    "mlflow-adapter-class": (
        "sha256:b43de26520944607382f86c4b2c2e127fcf3e217511b318a0241cee6add435e4"
    ),
    "experiment-service-class": (
        "sha256:ce8fcf2fcb955dae757146129fc956bf1dea3832d37864342b9bd42e2af069d0"
    ),
    "experiment-sync-mlflow": (
        "sha256:75536fb304dc80c4fbc81c1be6335ac9c27e5f0ea67345830741c5fa392b80e8"
    ),
    "experiment-create-experiment": (
        "sha256:0f41210302c38a10f630fcac8ce90a655a6aa0ffe9d7e1f5643e42242756c6bb"
    ),
    "experiment-create-run": (
        "sha256:2178a1b548e12dfb019f87585b09d2f46c913fe1d068833df982420285f729a2"
    ),
    "experiment-create-model": (
        "sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9"
    ),
    "run-create-model": ("sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9"),
    "experiment-compare-model": (
        "sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9"
    ),
    "experiment-compare": (
        "sha256:5bd4500050b39e844a4de5451b45184d8f870e25648d755195155853c083b59d"
    ),
    "experiment-controls": (
        "sha256:9c232c0dcc70fc130733341ac0ae04a14815ab4e98d4907687011717f0792c5d"
    ),
    "experiment-config-differences": (
        "sha256:d4163e15525aae9b4fc8487a190294a640e5a8be507965518b544b4428f8846f"
    ),
    "experiment-metric-differences": (
        "sha256:4598341c1afba8bf36bcfb2506f09429b18adb8a679c4827b864e6b8dc3bcd2c"
    ),
    "platform-search-model": (
        "sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9"
    ),
    "platform-service-class": (
        "sha256:3035729bcf2f5648cfe87fa952c46acf80b591a5dbef1b7787799f7c452cd598"
    ),
    "platform-service-search": (
        "sha256:038d193fa4b339c9c7c82d38a788c0561e36041db7545c8a7d04d66825241710"
    ),
    "platform-service-in-scope": (
        "sha256:2b25434502839b56265a57b83f347295fac562b54b8120bb8a5fa86f5bb3e667"
    ),
    "platform-service-deduplicate": (
        "sha256:19ffd06efaaaf3d2625a8497d323e5d650e4f7cd12fed3a65335eae5c4b2d84a"
    ),
    "platform-service-diversify": (
        "sha256:689cedafa2780ce00936543bb94e3bafe20c1de682a3a7ad785a5fe6e53aef7b"
    ),
    "platform-store-class": (
        "sha256:1bc3f7bbb61ae3959d893bd589f9a875aa1383cf9f95ad40c49dc5a851a65432"
    ),
    "platform-store-structured-search": (
        "sha256:5dafcdc0082f49d71f38669d3fa9ee91ff77424f7fbe0d14236ec9130959a636"
    ),
    "platform-store-query-terms": (
        "sha256:818d8dcc27211b48a7ad0dea026776b8fe4d7f2e9aae04522172630cc611f2e7"
    ),
}


def _with_fixed_binding_digest(row: Mapping[str, str]) -> dict[str, str]:
    return {
        **row,
        "bindings_sha256": _FIXED_CURRENT_V1_BINDING_DIGESTS[row["role"]],
    }


_FIXED_CURRENT_V1_ROWS: tuple[dict[str, str], ...] = (
    *tuple(_with_fixed_binding_digest(row) for row in _FIXED_PRODUCTION_AUTHORITY_ROWS),
    *tuple(_with_fixed_binding_digest(row) for row in _FIXED_CURRENT_V1_ADDITIONAL_ROWS),
)

# The rows above are the frozen authority embedded in the historical E-B0 Run.
# Current smoke/execution uses this separate, statically reviewed generation.
_FIXED_CURRENT_V2_ADDITIONAL_DIGESTS: Mapping[str, Mapping[str, str]] = {
    "experiment-compare-model": {
        "version": "experiment-public-model-v2",
        "source_sha256": "sha256:a4f248097beaee94cc05f3b822a699fb79fa2451bef4dd0e6b2734b04f8a833a",
        "code_sha256": "sha256:07cfcdc3c531abecfdfa1d03e01575941f9254b9f8568cecb323835103ee112f",
        "bindings_sha256": "sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9",
    },
    "experiment-compare": {
        "version": "experiment-service-v2",
        "source_sha256": "sha256:a43fa280066f5128625cae60649f86acf451b7fe14d03439ce85e1e3b4927ec0",
        "code_sha256": "sha256:10d8ebbe05cfce5d1315ccd89bcd9dc390114820e0e6abda571fdef4a9c0fc76",
        "bindings_sha256": "sha256:c82da49d12a29c31c2915bfd319c37857e808beb6e7f7ce6b4b3ef25177119e9",
    },
    "experiment-controls": {
        "version": "experiment-service-v1",
        "source_sha256": "sha256:ecbf06756fc932b16cedacc08572186fb048b311689f212f4bc2ad9c5e3ef320",
        "code_sha256": "sha256:9e08f9b0ec11fdfe34e386145d6a64320b94edf4ba8bc31579fef7bc6781704d",
        "bindings_sha256": "sha256:868bac8fb160c9d23f6c54546324664fcb06342cc906c12cd93bad3b571d69ac",
    },
    "experiment-config-differences": {
        "version": "experiment-service-v1",
        "source_sha256": "sha256:1d0388db10f96f7f61ea0cb7f9f28d8a4820247fd78b71bd264b3cd04bc6aafc",
        "code_sha256": "sha256:b721f1b8491beac0c2515512645fa5def4a83e825d8905485b375ad1c2266281",
        "bindings_sha256": "sha256:9d8bb0252bb7cac021ad7d71306680cc3a14107e4e9a0fd5e3bb513137248b36",
    },
    "experiment-metric-differences": {
        "version": "experiment-service-v2",
        "source_sha256": "sha256:c96318cd7aab5f8d6e88e301fc9c99bb336d3b205bd9abc12c56d32161e27d36",
        "code_sha256": "sha256:0c2df5af211d49eebaf82e3bee6d102bd09fcf25a6681f2d4db50f8cbf762a2e",
        "bindings_sha256": "sha256:8e1e40b63610f6fa206e6c00fd04e9a0b5863e64804165b70dae9522a6b864aa",
    },
    "platform-search-model": {
        "version": "platform-search-model-v4",
        "source_sha256": "sha256:61dffdcfe33df153784a641c6a9647ffef1c5b669548dfb018371986abeea64b",
        "code_sha256": "sha256:e10e339440dcb89af6cf0e51d04b804e8e08ffbfa53546c85b01bf8f58bdcbdf",
        "bindings_sha256": "sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9",
    },
    "platform-service-class": {
        "version": "platform-service-current-v6",
        "source_sha256": "sha256:5458f6252815fa6c7d5d4697da74dc18737df036bf3b411ca9bc7bd1870cceba",
        "code_sha256": "sha256:092230756c0fb37ea19861ecf1b648b93cd2e64f99b28f6ce79d5282fd4170a0",
        "bindings_sha256": "sha256:f2428c619535c7cb189549066b44fd67d47c022a944a588fef93a6e4b73e5d5a",
    },
    "platform-service-search": {
        "version": "platform-service-current-v4",
        "source_sha256": "sha256:ecd97f59849856ec79ea14e066e297919a55db8242a742926417174fdde6487c",
        "code_sha256": "sha256:215456546ec639ff0d75a982a2e966a4400410565710cae5557b9aa69b4a68eb",
        "bindings_sha256": "sha256:63ab9ae7a9d346a646ae298206eed833ffbcff61a3110df22a56197fcd544e66",
    },
    "platform-service-in-scope": {
        "version": "platform-service-current-v2",
        "source_sha256": "sha256:377921ce59363854702ce07b66ba3146d32aff818f1b475e99a12fd82f907b05",
        "code_sha256": "sha256:581be3dbd9196807d724a47a3bff7a08a8658a432eaca5c5475f8170f86087d4",
        "bindings_sha256": "sha256:2b25434502839b56265a57b83f347295fac562b54b8120bb8a5fa86f5bb3e667",
    },
    "platform-service-deduplicate": {
        "version": "platform-service-current-v2",
        "source_sha256": "sha256:f404224d24fa0c8c3d66e3fc5990279ab246621b44e65d7f8fd26dbdc1fbaae8",
        "code_sha256": "sha256:ef6cce60bbec02d1234ffca18b603397e66a83808252f5345261bc2fb5a884d3",
        "bindings_sha256": "sha256:6160d46db84a07799e9a3d2cab300458a4a6cfedaae5085aa860dd09544c2826",
    },
    "platform-service-diversify": {
        "version": "platform-service-current-v2",
        "source_sha256": "sha256:9d690ccf12a2fa2e6c22e87798262a10ac5a5bdcc35cc5eadaeaa2a0a794674f",
        "code_sha256": "sha256:3436261dc08d652f0f3d340a9153bc6dc12c248c4e2aebc9f6e62f0bad32b4d4",
        "bindings_sha256": "sha256:7e9e0e8fe7dd329f2e38c02925f30adffbdb34fb2976a500201e332cf66caa78",
    },
    "platform-store-class": {
        "version": "platform-store-structured-v3",
        "source_sha256": "sha256:6aba4b5e8cad1f0e1d610d7b7631eeedbc6ccfd02d411a3cad711aa1cce7028b",
        "code_sha256": "sha256:7076bfa9b22a17b04344d1a7c6704cd6848a0d30cb071af4ae67acad71cb5047",
        "bindings_sha256": "sha256:7d401acdf8b25d5abbce7b7f38b4cf0830d26eb90513bdd0a3c5ce4d64cfa499",
    },
    "platform-store-structured-search": {
        "version": "platform-store-structured-v2",
        "source_sha256": "sha256:5b119d2aad36fc59ceb3c73b8357d260588d972e597135d722683aa72c30dbbb",
        "code_sha256": "sha256:56c204e3ae976010d24557822a4df214d48326dd421af96d74de543c3370aafb",
        "bindings_sha256": "sha256:855bb6d478fcdd9028722e97458c9ed9a7e751abccf4fd9d04c7b01e538d9db2",
    },
    "platform-store-query-terms": {
        "version": "platform-store-token-substring-v2",
        "source_sha256": "sha256:68247435d15d49c48ad69a94d2a02239866cf0b9cb0913ea34ebfcf3c37c1ff3",
        "code_sha256": "sha256:54f52ca297ddbabae117c542167bd1e9ba54644766c051496a734ed38e6dffa1",
        "bindings_sha256": "sha256:9c283b3b832afb2c352fcd5b73c06070de2d92c0c85a2b77d0a4053ee520896a",
    },
}


def _with_current_v2_digests(row: Mapping[str, str]) -> dict[str, str]:
    return {**row, **_FIXED_CURRENT_V2_ADDITIONAL_DIGESTS[row["role"]]}


_FIXED_CURRENT_V2_ROWS: tuple[dict[str, str], ...] = (
    *_FIXED_CURRENT_PRODUCTION_AUTHORITY_ROWS,
    *tuple(_with_current_v2_digests(row) for row in _FIXED_CURRENT_V1_ADDITIONAL_ROWS),
)


def _fixed_component_identities() -> tuple[ExperimentBaselineComponentIdentity, ...]:
    return tuple(
        ExperimentBaselineComponentIdentity.model_validate(row) for row in _FIXED_CURRENT_V1_ROWS
    )


def _fixed_current_component_identities() -> tuple[ExperimentBaselineComponentIdentity, ...]:
    return tuple(
        ExperimentBaselineComponentIdentity.model_validate(row) for row in _FIXED_CURRENT_V2_ROWS
    )


def _component_set_hash(
    components: Sequence[ExperimentBaselineComponentIdentity],
) -> str:
    return _sha256(_canonical_json([component.model_dump(mode="json") for component in components]))


class ExperimentBaselineProductionAuthority(_FrozenContract):
    schema_version: Literal["experiment-e-b0-current-v1-production-authority-v2"] = (
        EXPERIMENT_BASELINE_PRODUCTION_AUTHORITY_VERSION
    )
    components: tuple[ExperimentBaselineComponentIdentity, ...]
    component_set_digest: Literal[
        "sha256:689b77964fb99fc72f7f93631eba0063dab8af4ab8e0d121364ee0df7fd5f845"
    ] = EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST
    foundation_component_set_digest: Literal[
        "sha256:0c961eb90c9a30795debc25d4d9c04eb6f38d4214a2ae3d71f03f54f812f405d"
    ] = EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST
    default_v1: Literal[True] = True
    experiment_v2_forbidden: Literal[True] = True
    semantic_forbidden: Literal[True] = True
    reranker_forbidden: Literal[True] = True
    context_builder_forbidden: Literal[True] = True
    experiment_source_only: Literal[True] = True
    notebook_family_preserved: Literal[True] = True
    evidence_match: Literal["token-substring"] = "token-substring"
    comparison_contract: Literal["controls/config/name+split/raw-delta"] = (
        "controls/config/name+split/raw-delta"
    )

    @model_validator(mode="after")
    def _fixed_reviewed_components(self) -> Self:
        fixed = _fixed_component_identities()
        if (
            self.components != fixed
            or len(self.components) != 21
            or _component_set_hash(self.components) != self.component_set_digest
        ):
            raise ValueError("E-B0 production authority differs from reviewed current V1")
        return self


class ExperimentBaselineCurrentProductionAuthority(_FrozenContract):
    schema_version: Literal["experiment-e-b0-current-production-authority-v8"] = (
        EXPERIMENT_BASELINE_CURRENT_PRODUCTION_AUTHORITY_VERSION
    )
    components: tuple[ExperimentBaselineComponentIdentity, ...]
    component_set_digest: Sha256 = EXPERIMENT_BASELINE_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
    foundation_component_set_digest: Literal[
        "sha256:8096c26fb306d5b98e57fe4c60f33f48bb18345dd92f83b020f3ed370e35d99c"
    ] = EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
    default_v1: Literal[True] = True
    experiment_v2_forbidden: Literal[True] = True
    semantic_forbidden: Literal[True] = True
    reranker_forbidden: Literal[True] = True
    context_builder_forbidden: Literal[True] = True
    experiment_source_only: Literal[True] = True
    notebook_family_preserved: Literal[True] = True
    evidence_match: Literal["token-substring"] = "token-substring"
    comparison_contract: Literal["controls/config/name+split/raw-delta"] = (
        "controls/config/name+split/raw-delta"
    )

    @model_validator(mode="after")
    def _fixed_reviewed_current_components(self) -> Self:
        fixed = _fixed_current_component_identities()
        if (
            self.components != fixed
            or len(self.components) != 21
            or self.component_set_digest
            != EXPERIMENT_BASELINE_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
            or _component_set_hash(self.components) != self.component_set_digest
        ):
            raise ValueError("E-B0 current production authority differs from reviewed V6")
        return self


class ExperimentBaselineArtifactContract(_FrozenContract):
    artifact_version: Literal["experiment-e-b0-portable-artifact-v1"] = (
        EXPERIMENT_BASELINE_ARTIFACT_VERSION
    )
    files: tuple[ArtifactFile, ...] = EXPERIMENT_BASELINE_ARTIFACT_FILES
    checksum_targets: tuple[ArtifactFile, ...] = EXPERIMENT_BASELINE_CHECKSUM_TARGETS
    verify_only: Literal[True] = True
    portable_copy_required: Literal[True] = True
    network_access_forbidden: Literal[True] = True
    database_files_forbidden: Literal[True] = True
    absolute_paths_forbidden: Literal[True] = True
    raw_objects_forbidden: Literal[True] = True

    @model_validator(mode="after")
    def _fixed_membership(self) -> Self:
        if self.files != EXPERIMENT_BASELINE_ARTIFACT_FILES:
            raise ValueError("E-B0 artifact membership is frozen")
        if self.checksum_targets != self.files[:-1]:
            raise ValueError("E-B0 checksum coverage drift")
        return self


class ExperimentSmokeMetric(_FrozenContract):
    name: Identifier
    status: Literal["SMOKE_UNAVAILABLE"] = EXPERIMENT_BASELINE_SMOKE_STATUS
    numerator: None = None
    denominator: None = None
    value: None = None
    reason: Literal["fixture smoke is not a released45 baseline measurement"] = (
        "fixture smoke is not a released45 baseline measurement"
    )


def _smoke_metrics() -> tuple[ExperimentSmokeMetric, ...]:
    return tuple(
        ExperimentSmokeMetric(name=name)
        for name in (
            "overall",
            "exact",
            "structured_filter",
            "metric_numeric",
            "comparison",
            "reproduction",
            "failure_status",
            "aggregation_trend",
            "unanswerable_incomparable_acl",
        )
    )


class ExperimentBaselinePreparation(_FrozenContract):
    schema_version: Literal["experiment-e-b0-preparation-v1"] = EXPERIMENT_BASELINE_SCHEMA_VERSION
    status: Literal["PREPARED"] = "PREPARED"
    qualification_status: Literal["NON_QUALIFIED"] = EXPERIMENT_BASELINE_QUALIFICATION_STATUS
    execution_status: Literal["NOT_AUTHORIZED"] = EXPERIMENT_BASELINE_EXECUTION_STATUS
    foundation_gate: Literal["PREPARATION_AUTHORIZED"] = EXPERIMENT_BASELINE_GATE_STATUS
    foundation_gate_decision: ExperimentFoundationGateDecision
    foundation_authority: ExperimentFoundationAuthority
    production_authority: ExperimentBaselineCurrentProductionAuthority
    path_policy_version: Literal["experiment-e-b0-temp-path-policy-v1"] = (
        EXPERIMENT_BASELINE_PATH_POLICY_VERSION
    )
    artifact_contract: ExperimentBaselineArtifactContract = Field(
        default_factory=ExperimentBaselineArtifactContract
    )
    smoke_metrics: tuple[ExperimentSmokeMetric, ...] = Field(default_factory=_smoke_metrics)
    released_baseline_executed: Literal[False] = False
    evaluation_run_created: Literal[False] = False
    baseline_identity_created: Literal[False] = False
    treatment_identity_created: Literal[False] = False
    metrics_published: Literal[False] = False
    artifact_published: Literal[False] = False
    qualification_created: Literal[False] = False
    e1_status: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"

    @model_validator(mode="after")
    def _preparation_only(self) -> Self:
        if tuple(item.name for item in self.smoke_metrics) != (
            "overall",
            "exact",
            "structured_filter",
            "metric_numeric",
            "comparison",
            "reproduction",
            "failure_status",
            "aggregation_trend",
            "unanswerable_incomparable_acl",
        ):
            raise ValueError("smoke metric membership drift")
        return self


class ExperimentBaselinePathAdmission(_FrozenContract):
    schema_version: Literal["experiment-e-b0-path-admission-v1"] = (
        "experiment-e-b0-path-admission-v1"
    )
    status: Literal["ADMITTED_FOR_FUTURE_AUTHORIZED_EXECUTION"] = (
        "ADMITTED_FOR_FUTURE_AUTHORIZED_EXECUTION"
    )
    temp_root: StrictStr
    database_path: StrictStr
    output_directory: StrictStr
    database_must_be_new: Literal[True] = True
    output_must_be_new: Literal[True] = True
    formal_database_forbidden: Literal[True] = True
    sidecars_forbidden: Literal[True] = True
    creates_files: Literal[False] = False


class ExperimentBaselineRunPathAdmission(_FrozenContract):
    schema_version: Literal["experiment-e-b0-run-path-admission-v1"] = (
        "experiment-e-b0-run-path-admission-v1"
    )
    temp_root: StrictStr
    mlflow_tracking_root: StrictStr
    raw_database_path: StrictStr
    manual_database_path: StrictStr
    output_directory: StrictStr
    creates_files: Literal[False] = False


class ExperimentBaselineRunRequest(_FrozenContract):
    schema_version: Literal["experiment-e-b0-run-request-v1"] = "experiment-e-b0-run-request-v1"
    foundation_gate_path: StrictStr
    harness_gate_path: StrictStr
    golden_root: StrictStr
    fixture_recipe_path: StrictStr
    temp_root: StrictStr
    mlflow_tracking_root: StrictStr
    raw_database_path: StrictStr
    manual_database_path: StrictStr
    output_directory: StrictStr
    seed: Literal[20260729] = EXPERIMENT_BASELINE_SEED


class ExperimentBaselineCandidate(_FrozenContract):
    rank: PositiveInt
    entity_id: ContractText
    entity_kind: EntityKind
    logical_locator: ContractText
    acl_ref: ContractText
    matched_terms: tuple[ContractText, ...]
    raw_structured_fields: dict[StrictStr, Any]


class ExperimentBaselinePrediction(_FrozenContract):
    schema_version: Literal["experiment-e-b0-prediction-v1"] = "experiment-e-b0-prediction-v1"
    dataset_id: Literal["experiment-golden-v1"]
    dataset_version: Literal["experiment-golden-v1"]
    package_hash: Literal["sha256:e7de1be4caf88af99c3b36de7e13c4a2ba3d15d4e40e5e5e0459ecf3d583a81f"]
    case_id: Identifier
    question: ContractText
    outcome: ReviewedOutcome
    candidates: tuple[ExperimentBaselineCandidate, ...] = ()
    candidate_count: NonNegativeInt
    observed_answerability: Answerability
    observed_predicate: TypedPredicate | None = None
    observed_metric_truth: tuple[MetricTruth, ...] = ()
    observed_comparability: ComparabilityTruth | None = None
    observed_reproduction: ReproductionTruth | None = None
    comparison_result: dict[StrictStr, Any] | None = None
    unavailable_reason: ReviewedReason | None = None
    error_reason: ReviewedReason | None = None
    error: ContractText | None = None
    latency_ms: NonNegativeFiniteFloat

    @model_validator(mode="after")
    def _prediction_shape(self) -> Self:
        ranks = tuple(candidate.rank for candidate in self.candidates)
        if ranks != tuple(range(1, len(ranks) + 1)):
            raise ValueError("candidate ranks must be contiguous")
        if len({candidate.entity_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("candidate entity identities must be unique")
        if self.candidate_count < len(self.candidates):
            raise ValueError("candidate_count cannot be below recorded candidates")
        if self.outcome == ReviewedOutcome.RETURNED:
            if (
                not self.candidates
                or self.unavailable_reason is not None
                or self.error_reason is not None
                or self.error is not None
            ):
                raise ValueError("returned prediction shape is invalid")
        elif self.outcome == ReviewedOutcome.ZERO_RESULT:
            if (
                self.candidates
                or self.candidate_count
                or self.unavailable_reason is not None
                or self.error_reason is not None
                or self.error is not None
            ):
                raise ValueError("zero-result prediction shape is invalid")
        elif self.outcome == ReviewedOutcome.UNAVAILABLE:
            if (
                self.candidates
                or self.candidate_count
                or self.unavailable_reason is None
                or self.error_reason is not None
                or self.error is not None
            ):
                raise ValueError("unavailable prediction shape is invalid")
        elif self.outcome == ReviewedOutcome.ERROR and (
            self.candidates
            or self.candidate_count
            or self.unavailable_reason is not None
            or self.error_reason != ReviewedReason.SYSTEM_ERROR
            or self.error is None
        ):
            raise ValueError("error prediction shape is invalid")
        return self

    def reviewed_row(self) -> ReviewedExperimentCaseRow:
        return ReviewedExperimentCaseRow(
            dataset_id=self.dataset_id,
            dataset_version=self.dataset_version,
            package_hash=self.package_hash,
            case_id=self.case_id,
            reviewed=True,
            outcome=self.outcome,
            candidates=tuple(
                ReviewedCandidate(
                    entity_id=candidate.entity_id,
                    locator=candidate.logical_locator,
                    acl_ref=candidate.acl_ref,
                    rank=candidate.rank,
                )
                for candidate in self.candidates
            ),
            observed_answerability=self.observed_answerability,
            observed_predicate=self.observed_predicate,
            observed_metric_truth=self.observed_metric_truth,
            observed_comparability=self.observed_comparability,
            observed_reproduction=self.observed_reproduction,
            unavailable_reason=self.unavailable_reason,
            error_reason=self.error_reason,
        )


class ExperimentComparisonRecord(_FrozenContract):
    schema_version: Literal["experiment-e-b0-comparison-record-v1"] = (
        "experiment-e-b0-comparison-record-v1"
    )
    case_id: Identifier
    attempted: StrictBool
    result: dict[StrictStr, Any] | None = None
    unavailable_reason: ReviewedReason | None = None
    error: ContractText | None = None

    @model_validator(mode="after")
    def _comparison_shape(self) -> Self:
        if not self.attempted and any(
            value is not None for value in (self.result, self.unavailable_reason, self.error)
        ):
            raise ValueError("unattempted comparison cannot report an outcome")
        if self.result is not None and (
            not self.attempted or self.unavailable_reason is not None or self.error is not None
        ):
            raise ValueError("comparison success shape is invalid")
        if (
            self.attempted
            and self.result is None
            and ((self.unavailable_reason is None) == (self.error is None))
        ):
            raise ValueError("attempted comparison requires one terminal outcome")
        return self


class ExperimentBaselineArtifactManifest(_FrozenContract):
    schema_version: Literal["experiment-e-b0-artifact-manifest-v2"] = (
        "experiment-e-b0-artifact-manifest-v2"
    )
    artifact_version: Literal["experiment-e-b0-portable-artifact-v1"] = (
        EXPERIMENT_BASELINE_ARTIFACT_VERSION
    )
    runner_version: Literal["experiment-e-b0-current-v1-runner-v1"] = (
        EXPERIMENT_BASELINE_RUNNER_VERSION
    )
    run_id: Identifier
    run_uri: ContractText
    execution_mode: Literal["AUTHORIZED_RELEASED_45", "TEST_FIXTURE"]
    status: Literal["NON_QUALIFIED"] = EXPERIMENT_BASELINE_QUALIFICATION_STATUS
    qualification_created: Literal[False] = False
    harness_gate_task_id: Literal["019fac2b-517a-79b3-8c1a-3a0625cd7ee5"] = (
        EXPERIMENT_HARNESS_GATE_TASK_ID
    )
    harness_gate_decision_digest: Literal[
        "sha256:939130c475ae005f797ccded56e36998891a55ce6b5e2574cec9180b5bf3791e"
    ] = EXPERIMENT_HARNESS_GATE_DECISION_DIGEST
    preparation_digest: Literal[
        "sha256:5b407c7bd9e86cce4521aeacffbe6f5f2824b88ed6799792d38fb5dd59dd4179"
    ] = EXPERIMENT_BASELINE_PREPARATION_DIGEST
    dataset_id: Literal["experiment-golden-v1"]
    dataset_version: Literal["experiment-golden-v1"]
    membership: tuple[Identifier, ...]
    seed: Literal[20260729]
    execution_config: dict[StrictStr, Any] = Field(
        default_factory=lambda: {
            "current_v1": True,
            "mlflow_path": "MLflowAdapter/FileStore->ExperimentService.sync_mlflow",
            "manual_path": "public-models/service",
            "search_path": "PlatformService.search->PlatformStore.structured_search",
            "sources": ["experiment"],
            "evidence_match": "token-substring",
            "notebook_family_preserved": True,
            "compare_path": "ExperimentService.compare/controls+config+name+split+raw-delta",
            "experiment_v2": False,
            "semantic": False,
            "reranker": False,
            "context_builder": False,
            "network": False,
            "formal_database": False,
            "system_temp_stage": True,
            "atomic_publish": True,
        }
    )
    foundation_authority: ExperimentFoundationAuthority
    production_authority: ExperimentBaselineProductionAuthority
    files: tuple[ArtifactFile, ...] = EXPERIMENT_BASELINE_ARTIFACT_FILES
    checksum_targets: tuple[ArtifactFile, ...] = EXPERIMENT_BASELINE_CHECKSUM_TARGETS

    @model_validator(mode="after")
    def _manifest_membership(self) -> Self:
        expected_run_uri = (
            f"evaluation-run://project-experiment-eb0-v1/{self.run_id.removeprefix('eb0-')}"
        )
        if self.membership != self.foundation_authority.membership:
            raise ValueError("artifact manifest membership differs from Foundation")
        if self.run_uri != expected_run_uri:
            raise ValueError("artifact Run URI differs from bound run identity")
        if (
            self.execution_config
            != ExperimentBaselineArtifactManifest.model_fields["execution_config"].default_factory()
        ):
            raise ValueError("artifact execution config differs from reviewed current V1")
        if (
            self.files != EXPERIMENT_BASELINE_ARTIFACT_FILES
            or self.checksum_targets != EXPERIMENT_BASELINE_CHECKSUM_TARGETS
        ):
            raise ValueError("artifact manifest file contract drift")
        return self


class ExperimentBaselineVerification(_FrozenContract):
    schema_version: Literal["experiment-e-b0-verification-result-v1"] = (
        "experiment-e-b0-verification-result-v1"
    )
    verification_version: Literal["experiment-e-b0-verify-only-v1"] = (
        EXPERIMENT_BASELINE_VERIFICATION_VERSION
    )
    status: Literal["VERIFIED_NON_QUALIFIED"] = "VERIFIED_NON_QUALIFIED"
    run_id: Identifier
    case_count: Literal[45]
    files: tuple[ArtifactFile, ...] = EXPERIMENT_BASELINE_ARTIFACT_FILES
    portable: Literal[True] = True
    service_calls: Literal[0] = 0
    database_calls: Literal[0] = 0
    network_calls: Literal[0] = 0


class ExperimentBaselineResult(_FrozenContract):
    schema_version: Literal["experiment-e-b0-result-v1"] = "experiment-e-b0-result-v1"
    status: Literal["NON_QUALIFIED"] = EXPERIMENT_BASELINE_QUALIFICATION_STATUS
    run_id: Identifier
    artifact_directory: StrictStr
    case_count: Literal[45]
    qualification_created: Literal[False] = False
    e1_status: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"


class ExperimentBaselineSmokeResult(_FrozenContract):
    schema_version: Literal["experiment-e-b0-smoke-result-v1"] = "experiment-e-b0-smoke-result-v1"
    status: Literal["SMOKE_UNAVAILABLE"] = EXPERIMENT_BASELINE_SMOKE_STATUS
    qualification_status: Literal["NON_QUALIFIED"] = EXPERIMENT_BASELINE_QUALIFICATION_STATUS
    production_execution: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    released45_executed: Literal[False] = False
    evaluation_run_created: Literal[False] = False
    metrics_published: Literal[False] = False
    artifact_created: Literal[False] = False
    seed: Literal[20260729] = EXPERIMENT_BASELINE_SEED
    logical_counts: dict[Identifier, PositiveInt]
    mlflow_synced_runs: Literal[12]
    manual_created_runs: Literal[12]
    search_candidate_count: NonNegativeInt
    search_entity_kinds: tuple[ContractText, ...]
    search_matched_terms: tuple[ContractText, ...]
    comparison_controls: tuple[dict[StrictStr, Any], ...]
    comparison_config_difference_count: NonNegativeInt
    comparison_metric_rows: NonNegativeInt
    current_v1: Literal[True] = True
    experiment_source_only: Literal[True] = True
    notebook_family_preserved: Literal[True] = True
    metrics: tuple[ExperimentSmokeMetric, ...] = Field(default_factory=_smoke_metrics)


_CANONICAL_CURRENT_V1_OBJECTS: dict[str, object] = {
    "mlflow-adapter-class": MLflowAdapter,
    "experiment-service-class": ExperimentService,
    "experiment-sync-mlflow": ExperimentService.sync_mlflow,
    "experiment-create-experiment": ExperimentService.create_experiment,
    "experiment-create-run": ExperimentService.create_run,
    "experiment-create-model": ExperimentCreate,
    "run-create-model": RunCreate,
    "experiment-compare-model": ComparisonRequest,
    "experiment-compare": ExperimentService.compare,
    "experiment-controls": ExperimentService._controls,
    "experiment-config-differences": ExperimentService._config_differences,
    "experiment-metric-differences": ExperimentService._metric_differences,
    "platform-search-model": GlobalSearchRequest,
    "platform-service-class": PlatformService,
    "platform-service-search": PlatformService.search,
    "platform-service-in-scope": PlatformService._in_scope,
    "platform-service-deduplicate": PlatformService._deduplicate,
    "platform-service-diversify": PlatformService._diversify,
    "platform-store-class": PlatformStore,
    "platform-store-structured-search": PlatformStore.structured_search,
    "platform-store-query-terms": PlatformStore._query_terms,
}

_BOUND_PRODUCTION_MODULES: dict[str, object] = {
    "evidence_rag.experiments.adapters": experiment_adapters_public,
    "evidence_rag.experiments.adapters.mlflow": mlflow_module,
    "evidence_rag.experiments.models": experiment_models_public,
    "evidence_rag.experiments.service": experiment_service_public,
    "evidence_rag.platform.models": platform_models_public,
    "evidence_rag.platform.service": platform_service_public,
    "evidence_rag.platform.store": platform_store_public,
}


def _constant_code_payload(value: object) -> object:
    return portable_constant_payload(value)


def _code_object_payload(code: types.CodeType) -> dict[str, object]:
    return portable_code_payload(code)


def _function_code_digest(value: types.FunctionType) -> str:
    try:
        return _sha256(_canonical_json(portable_function_payload(value)))
    except CodeIdentityError as exc:
        raise ExperimentBaselineAuthorityError(str(exc)) from exc


def _annotation_identity(value: object) -> object:
    origin = typing.get_origin(value)
    if origin is not None:
        return {
            "origin": _annotation_identity(origin),
            "args": [_annotation_identity(item) for item in typing.get_args(value)],
        }
    if isinstance(value, type):
        return {"type": [value.__module__, value.__qualname__]}
    if value is Ellipsis:
        return {"ellipsis": True}
    return {"repr": repr(value)}


def _class_code_digest(value: type[object]) -> str:
    members: dict[str, object] = {}
    attributes: dict[str, object] = {}
    class_source_file = inspect.getsourcefile(value)
    ignored = {"__annotations__", "__dict__", "__doc__", "__module__", "__weakref__"}
    for name, member in sorted(value.__dict__.items()):
        raw = member
        kind = "function"
        if isinstance(raw, staticmethod):
            raw = raw.__func__
            kind = "staticmethod"
        elif isinstance(raw, classmethod):
            raw = raw.__func__
            kind = "classmethod"
        if isinstance(raw, types.FunctionType) and inspect.getsourcefile(raw) == class_source_file:
            members[name] = {"kind": kind, "digest": _function_code_digest(raw)}
        elif isinstance(raw, property):
            members[name] = {
                "kind": "property",
                "get": (
                    _function_code_digest(raw.fget)
                    if raw.fget and inspect.getsourcefile(raw.fget) == class_source_file
                    else None
                ),
                "set": (
                    _function_code_digest(raw.fset)
                    if raw.fset and inspect.getsourcefile(raw.fset) == class_source_file
                    else None
                ),
                "delete": (
                    _function_code_digest(raw.fdel)
                    if raw.fdel and inspect.getsourcefile(raw.fdel) == class_source_file
                    else None
                ),
            }
        elif name not in ignored and not name.startswith("__") and isinstance(
            raw, (type(None), bool, int, float, str, tuple, frozenset)
        ):
            attributes[name] = _constant_code_payload(raw)
    model_fields: dict[str, object] = {}
    if issubclass(value, BaseModel):
        for name, field in sorted(value.model_fields.items()):
            default: object = (
                "required"
                if field.default is PydanticUndefined
                else _constant_code_payload(field.default)
            )
            factory = None
            if field.default_factory is not None:
                factory = [
                    getattr(field.default_factory, "__module__", None),
                    getattr(
                        field.default_factory,
                        "__qualname__",
                        repr(field.default_factory),
                    ),
                ]
            model_fields[name] = {
                "annotation": _annotation_identity(field.annotation),
                "default": default,
                "default_factory": factory,
            }
    return _sha256(
        _canonical_json(
            {
                "bases": [(base.__module__, base.__qualname__) for base in value.__bases__],
                "members": members,
                "attrs": attributes,
                "model_fields": model_fields,
            }
        )
    )


def _source_digest(value: object) -> str:
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError) as exc:
        raise ExperimentBaselineAuthorityError(
            "production component source is unavailable"
        ) from exc
    normalized = source.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return _sha256(normalized)


def _runtime_code_digest(value: object, kind: str) -> str:
    if kind == "class" and isinstance(value, type):
        return _class_code_digest(value)
    if kind == "method" and isinstance(value, types.FunctionType):
        return _function_code_digest(value)
    raise ExperimentBaselineAuthorityError("production component kind/runtime object mismatch")


def _nested_code_objects(code: types.CodeType) -> Iterable[types.CodeType]:
    yield code
    for item in code.co_consts:
        if isinstance(item, types.CodeType):
            yield from _nested_code_objects(item)


def _runtime_export_is_exact(value: object) -> bool:
    module_name = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if not isinstance(module_name, str) or not isinstance(qualname, str) or "<locals>" in qualname:
        return False
    current: object = sys.modules.get(module_name)
    if current is None:
        return False
    try:
        for part in qualname.split("."):
            current = getattr(current, part)
    except AttributeError:
        return False
    return current is value


def _optional_source_digest(value: object) -> str | None:
    try:
        return _source_digest(value)
    except ExperimentBaselineAuthorityError:
        return None


def _portable_module_origin(value: types.ModuleType) -> str | None:
    """Describe module provenance without binding authority to a checkout root."""

    origin = getattr(getattr(value, "__spec__", None), "origin", None)
    if origin is None or origin in {"built-in", "frozen"}:
        return origin
    normalized = origin.replace("\\", "/")
    is_absolute = normalized.startswith(("/", "//")) or bool(
        re.match(r"^[A-Za-z]:/", normalized)
    )
    if not is_absolute:
        return normalized
    module_path = value.__name__.replace(".", "/")
    for suffix in (f"/{module_path}.py", f"/{module_path}/__init__.py"):
        if normalized.endswith(suffix):
            return f"<module-root>{suffix}"
    return f"<module-file>/{normalized.rsplit('/', 1)[-1]}"


def _binding_value_payload(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return {"literal": value, "type": type(value).__name__}
    if isinstance(value, float):
        return {"float": repr(value)}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, tuple):
        return {"tuple": [_binding_value_payload(item) for item in value]}
    if isinstance(value, list):
        return {"list": [_binding_value_payload(item) for item in value]}
    if isinstance(value, (set, frozenset)):
        items = [_binding_value_payload(item) for item in value]
        return {type(value).__name__: sorted(items, key=_canonical_json)}
    if isinstance(value, Mapping):
        items = [
            (_binding_value_payload(key), _binding_value_payload(item))
            for key, item in value.items()
        ]
        items.sort(key=lambda item: _canonical_json(item[0]))
        return {
            "mapping": items,
            "type": [type(value).__module__, type(value).__qualname__],
        }
    if isinstance(value, re.Pattern):
        return {"pattern": value.pattern, "flags": value.flags}
    if isinstance(value, types.ModuleType):
        return {
            "module": value.__name__,
            "origin": _portable_module_origin(value),
            "canonical": sys.modules.get(value.__name__) is value,
        }
    stdlib_identity = portable_stdlib_object_identity(value)
    if stdlib_identity is not None:
        return {
            "stdlib": stdlib_identity,
            "kind": f"{type(value).__module__}.{type(value).__qualname__}",
        }
    if isinstance(value, types.FunctionType):
        module = sys.modules.get(value.__module__)
        return {
            "function": [value.__module__, value.__qualname__],
            "canonical": _runtime_export_is_exact(value),
            "canonical_globals": module is not None and value.__globals__ is module.__dict__,
            "canonical_builtins": value.__builtins__ is builtins.__dict__,
            "source": _optional_source_digest(value),
            "code": _function_code_digest(value),
        }
    if isinstance(value, type):
        try:
            code_digest = _class_code_digest(value)
        except (TypeError, ValueError):
            code_digest = None
        return {
            "class": [value.__module__, value.__qualname__],
            "canonical": _runtime_export_is_exact(value),
            "source": _optional_source_digest(value),
            "code": code_digest,
        }
    if isinstance(value, (types.BuiltinFunctionType, types.BuiltinMethodType)):
        return {
            "builtin": [
                getattr(value, "__module__", None),
                getattr(value, "__qualname__", getattr(value, "__name__", None)),
            ]
        }
    return {
        "object_type": [type(value).__module__, type(value).__qualname__],
        "text": str(value),
    }


def _function_binding_payload(value: types.FunctionType) -> dict[str, object]:
    module = sys.modules.get(value.__module__)
    if module is None or value.__globals__ is not module.__dict__:
        raise ExperimentBaselineAuthorityError(
            f"{value.__qualname__} does not use canonical module globals"
        )
    if value.__builtins__ is not builtins.__dict__:
        raise ExperimentBaselineAuthorityError(
            f"{value.__qualname__} does not use canonical builtins"
        )
    global_names = sorted(
        {
            instruction.argval
            for code in _nested_code_objects(value.__code__)
            for instruction in dis.get_instructions(code)
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME"}
            and isinstance(instruction.argval, str)
        }
    )
    bindings: list[dict[str, object]] = []
    for name in global_names:
        if name in value.__globals__:
            bindings.append(
                {
                    "name": name,
                    "scope": "global",
                    "binding": _binding_value_payload(value.__globals__[name]),
                }
            )
        elif hasattr(builtins, name):
            binding = getattr(builtins, name)
            if binding is not builtins.__dict__[name]:
                raise ExperimentBaselineAuthorityError(
                    f"{value.__qualname__} builtin binding is not canonical: {name}"
                )
            bindings.append(
                {
                    "name": name,
                    "scope": "builtin",
                    "binding": _binding_value_payload(binding),
                }
            )
        else:
            bindings.append({"name": name, "scope": "unbound"})
    closure: list[dict[str, object]] = []
    if value.__closure__ is not None:
        for name, cell in zip(
            value.__code__.co_freevars,
            value.__closure__,
            strict=True,
        ):
            try:
                binding = _binding_value_payload(cell.cell_contents)
            except ValueError:
                binding = {"empty": True}
            closure.append({"name": name, "binding": binding})
    return {
        "module": value.__module__,
        "qualname": value.__qualname__,
        "canonical_globals": True,
        "canonical_builtins": True,
        "globals": bindings,
        "closure": closure,
    }


def _runtime_binding_digest(value: object, kind: str) -> str:
    if kind == "method" and isinstance(value, types.FunctionType):
        payload = {"functions": [_function_binding_payload(value)]}
    elif kind == "class" and isinstance(value, type):
        functions: list[dict[str, object]] = []
        for name, member in sorted(value.__dict__.items()):
            member_kind = "function"
            if isinstance(member, staticmethod):
                member = member.__func__
                member_kind = "staticmethod"
            elif isinstance(member, classmethod):
                member = member.__func__
                member_kind = "classmethod"
            if isinstance(member, types.FunctionType):
                functions.append(
                    {
                        "member": name,
                        "kind": member_kind,
                        "binding": _function_binding_payload(member),
                    }
                )
            elif isinstance(member, property):
                for operation, function in (
                    ("get", member.fget),
                    ("set", member.fset),
                    ("delete", member.fdel),
                ):
                    if function is not None:
                        functions.append(
                            {
                                "member": name,
                                "kind": f"property-{operation}",
                                "binding": _function_binding_payload(function),
                            }
                        )
        payload = {"functions": functions}
    else:
        raise ExperimentBaselineAuthorityError("production component kind/runtime binding mismatch")
    return _sha256(_canonical_json(payload))


def _resolve_export(module_name: str, export: str) -> object:
    current: object = importlib.import_module(module_name)
    for part in export.split("."):
        if not hasattr(current, part):
            raise ExperimentBaselineAuthorityError(
                f"production export unavailable: {module_name}.{export}"
            )
        current = getattr(current, part)
    return current


def fixed_experiment_baseline_production_authority_v1() -> ExperimentBaselineProductionAuthority:
    """Return the frozen authority embedded in the historical E-B0 Run."""

    try:
        return ExperimentBaselineProductionAuthority(components=_fixed_component_identities())
    except ValueError as exc:
        raise ExperimentBaselineAuthorityError(
            "fixed E-B0 production authority is internally invalid"
        ) from exc


def fixed_experiment_baseline_current_production_authority_v2() -> (
    ExperimentBaselineCurrentProductionAuthority
):
    """Return the hard-coded current authority without runtime discovery."""

    try:
        return ExperimentBaselineCurrentProductionAuthority(
            components=_fixed_current_component_identities()
        )
    except ValueError as exc:
        raise ExperimentBaselineAuthorityError(
            "fixed E-B0 current production authority is internally invalid"
        ) from exc


def verify_experiment_baseline_production_authority_v1(
    expected: ExperimentBaselineCurrentProductionAuthority | None = None,
) -> ExperimentBaselineCurrentProductionAuthority:
    """Verify exact current modules/exports/objects/source/code/bindings before I/O."""

    authority = fixed_experiment_baseline_current_production_authority_v2()
    if expected is not None and expected != authority:
        raise ExperimentBaselineAuthorityError(
            "artifact/request production authority differs from reviewed authority"
        )
    for module_name, bound in _BOUND_PRODUCTION_MODULES.items():
        if importlib.import_module(module_name) is not bound:
            raise ExperimentBaselineAuthorityError(
                f"production module binding replaced: {module_name}"
            )
    reviewed: list[tuple[ExperimentBaselineComponentIdentity, str, str, str]] = []
    for identity in authority.components:
        actual = _resolve_export(identity.module, identity.export)
        public = _resolve_export(identity.public_module, identity.export)
        canonical = _CANONICAL_CURRENT_V1_OBJECTS[identity.role]
        if actual is not public or actual is not canonical:
            raise ExperimentBaselineAuthorityError(
                f"{identity.role} public/exact object was replaced"
            )
        if (
            getattr(actual, "__module__", None) != identity.module
            or getattr(actual, "__qualname__", None) != identity.qualname
        ):
            raise ExperimentBaselineAuthorityError(f"{identity.role} module/qualname was forged")
        if identity.kind == "class":
            if not isinstance(actual, type):
                raise ExperimentBaselineAuthorityError(f"{identity.role} is not an exact class")
        elif not isinstance(actual, types.FunctionType) or inspect.unwrap(actual) is not actual:
            raise ExperimentBaselineAuthorityError(f"{identity.role} is wrapped/replaced")
        # Validate canonical globals/builtins for every component before any
        # aggregate comparison, so a precise binding attack is never obscured
        # by unrelated source drift in an earlier component.
        binding_digest = _runtime_binding_digest(actual, identity.kind)
        reviewed.append(
            (
                identity,
                _source_digest(actual),
                _runtime_code_digest(actual, identity.kind),
                binding_digest,
            )
        )
    try:
        foundation = verify_production_component_authority()
    except ValueError as exc:
        raise ExperimentBaselineAuthorityError(
            "Foundation current production component authority failed"
        ) from exc
    if (
        not isinstance(foundation, CurrentProductionComponentAuthority)
        or foundation.component_set_digest != EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
    ):
        raise ExperimentBaselineAuthorityError(
            "Foundation current component authority digest drift"
        )
    for identity, source_digest, code_digest, binding_digest in reviewed:
        if (
            source_digest != identity.source_sha256
            or code_digest != identity.code_sha256
            or binding_digest != identity.bindings_sha256
        ):
            raise ExperimentBaselineAuthorityError(
                f"{identity.role} reviewed source/runtime/binding digest mismatch"
            )
    if MLflowAdapter.adapter_version != "mlflow-tracking-v2":
        raise ExperimentBaselineAuthorityError("MLflow adapter version drift")
    return authority


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _canonical_gate_path(relative: Path) -> Path:
    return (_repository_root() / relative).resolve(strict=False)


def _require_canonical_gate_path(
    supplied: str | os.PathLike[str] | None,
    *,
    relative: Path,
    must_exist: bool,
) -> Path:
    expected = _canonical_gate_path(relative)
    path = expected if supplied is None else Path(os.fspath(supplied))
    if path.absolute() != expected or path.resolve(strict=False) != expected:
        raise ExperimentBaselineGateError("Gate copies/aliases are not authoritative")
    if path.is_symlink():
        raise ExperimentBaselineGateError("Gate symlinks are not authoritative")
    if must_exist and (
        not path.is_file()
        or stat.S_ISLNK(path.lstat().st_mode)
        or not stat.S_ISREG(path.lstat().st_mode)
    ):
        raise ExperimentBaselineGateError("canonical Gate document is unavailable")
    return path


def _token_line(line: str) -> str:
    stripped = line.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == "`":
        return stripped[1:-1]
    return stripped


def _latest_relevant_top_level_section(
    text: str,
    *,
    pass_token: str,
    fail_token: str,
) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("# ")]
    if not starts:
        raise ExperimentBaselineGateError("Gate has no top-level review section")
    headings = [lines[index][2:].strip() for index in starts]
    if len(headings) != len(set(headings)):
        raise ExperimentBaselineGateError("Gate has a duplicated top-level heading")
    starts.append(len(lines))
    relevant: list[str] = []
    for start, end in zip(starts[:-1], starts[1:], strict=True):
        section = "\n".join(lines[start:end])
        tokens = {_token_line(line) for line in lines[start:end]}
        if pass_token in tokens or fail_token in tokens:
            relevant.append(section)
    if not relevant:
        raise ExperimentBaselineGateError("Gate has no exact relevant conclusion section")
    return relevant[-1]


def _require_exact_token(section: str, token: str) -> None:
    count = sum(_token_line(line) == token for line in section.splitlines())
    if count != 1:
        raise ExperimentBaselineGateError(f"Gate exact token missing/duplicated: {token}")


def _exact_token_lines(section: str, prefix: str) -> list[str]:
    return [
        token for line in section.splitlines() if (token := _token_line(line)).startswith(prefix)
    ]


def _require_whole_section_lines(
    section: str,
    *,
    prefix: str,
    expected: Sequence[str],
) -> None:
    if _exact_token_lines(section, prefix) != list(expected):
        raise ExperimentBaselineGateError(f"Gate whole-section decision token conflict: {prefix}")


def _latest_exact_decision_block(
    section: str,
    *,
    pass_token: str,
    fail_token: str,
) -> tuple[str, int]:
    lines = section.splitlines()
    blocks: list[tuple[int, int, str]] = []
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if start is None and stripped.startswith("```"):
            start = index
        elif start is not None and stripped == "```":
            blocks.append((start, index, "\n".join(lines[start + 1 : index])))
            start = None
    if start is not None:
        raise ExperimentBaselineGateError("Gate has an unterminated fenced block")
    decisions = [
        block
        for block in blocks
        if any(_token_line(line) in {pass_token, fail_token} for line in block[2].splitlines())
    ]
    if len(decisions) != 1:
        raise ExperimentBaselineGateError("Gate final decision block is missing/duplicated")
    _, end, block = decisions[0]
    if any(line.strip() for line in lines[end + 1 :]):
        raise ExperimentBaselineGateError("Gate decision block is not final")
    return block, end


def _require_exact_decision_block(
    block: str,
    expected: Sequence[str],
    *,
    optional_tail: Sequence[str] = (),
) -> None:
    lines = tuple(_token_line(line) for line in block.splitlines() if line.strip())
    if lines not in {tuple(expected), (*expected, *optional_tail)}:
        raise ExperimentBaselineGateError("Gate final decision block is not canonical")


def _require_identity_line(section: str, label: str, value: str) -> None:
    pattern = re.compile(
        rf"^\s*{re.escape(label)}\s*:\s*(\S.*?)\s*$",
        re.MULTILINE,
    )
    matches = pattern.findall(section)
    if matches != [value]:
        raise ExperimentBaselineGateError(f"Gate identity missing/duplicated: {label}")


def parse_experiment_foundation_gate_v1(
    text: str,
) -> ExperimentFoundationGateDecision:
    """Parse only the latest exact Foundation conclusion section.

    Historical FAIL/PASS prose and token substrings never authorize preparation.
    """

    if type(text) is not str or not text or _CONTROL_RE.search(text):
        raise ExperimentBaselineGateError("Gate text is invalid")
    source_matches = re.findall(
        r"^Gate 来源任务：`([0-9a-f-]+)`\s*$",
        text,
        flags=re.MULTILINE,
    )
    if (
        source_matches != [EXPERIMENT_FOUNDATION_TASK_ID]
        or _UUID7_RE.fullmatch(source_matches[0]) is None
    ):
        raise ExperimentBaselineGateError("Foundation source task identity mismatch")
    section = _latest_relevant_top_level_section(
        text,
        pass_token="EXPERIMENT E0-01 FOUNDATION PASS",
        fail_token="EXPERIMENT E0-01 FOUNDATION FAIL",
    )
    decision_block, _ = _latest_exact_decision_block(
        section,
        pass_token="EXPERIMENT E0-01 FOUNDATION PASS",
        fail_token="EXPERIMENT E0-01 FOUNDATION FAIL",
    )
    expected_block = (
        "EXPERIMENT E0-01 FOUNDATION PASS",
        "P0 findings: 0",
        "P1 findings: 0",
        "E-B0 HARNESS PREPARATION AUTHORIZED",
        "E-B0 production execution: NOT_AUTHORIZED",
        "E-B0 Run/metrics/artifact/qualification: NONE",
        "E1: NOT_AUTHORIZED",
    )
    _require_exact_decision_block(
        decision_block,
        expected_block,
        optional_tail=("E2–E5: NOT_IMPLEMENTED / NOT_AUTHORIZED",),
    )
    _require_whole_section_lines(
        section,
        prefix="EXPERIMENT E0-01 FOUNDATION ",
        expected=("EXPERIMENT E0-01 FOUNDATION PASS",),
    )
    # The canonical reviewed Foundation section has one findings summary and
    # one final decision block. Any third copy or conflicting value is tamper.
    _require_whole_section_lines(
        section,
        prefix="P0 findings:",
        expected=("P0 findings: 0", "P0 findings: 0"),
    )
    _require_whole_section_lines(
        section,
        prefix="P1 findings:",
        expected=("P1 findings: 0", "P1 findings: 0"),
    )
    for prefix, token in (
        ("E-B0 HARNESS PREPARATION ", "E-B0 HARNESS PREPARATION AUTHORIZED"),
        ("E-B0 production execution:", "E-B0 production execution: NOT_AUTHORIZED"),
        (
            "E-B0 Run/metrics/artifact/qualification:",
            "E-B0 Run/metrics/artifact/qualification: NONE",
        ),
        ("E1:", "E1: NOT_AUTHORIZED"),
    ):
        _require_whole_section_lines(section, prefix=prefix, expected=(token,))
    _require_identity_line(section, "release", EXPERIMENT_FOUNDATION_RELEASE_ID)
    _require_identity_line(
        section,
        "package",
        EXPERIMENT_GOLDEN_PACKAGE_HASH,
    )
    _require_identity_line(
        section,
        "authority",
        EXPERIMENT_GOLDEN_AUTHORITY_HASH,
    )
    _require_identity_line(
        section,
        "component",
        EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST,
    )
    _require_identity_line(section, "parity", EXPERIMENT_PRODUCTION_PARITY_HASH)
    _require_identity_line(section, "recipe", EXPERIMENT_FOUNDATION_RECIPE_HASH)
    decision_payload = {
        "source_task_id": EXPERIMENT_FOUNDATION_TASK_ID,
        "conclusion": "EXPERIMENT E0-01 FOUNDATION PASS",
        "p0_findings": 0,
        "p1_findings": 0,
        "preparation_authorization": "E-B0 HARNESS PREPARATION AUTHORIZED",
        "production_execution": "NOT_AUTHORIZED",
        "e1_status": "NOT_AUTHORIZED",
        "authority": ExperimentFoundationAuthority().model_dump(mode="json"),
    }
    return ExperimentFoundationGateDecision(
        **decision_payload,
        decision_digest=_sha256(_canonical_json(decision_payload)),
    )


def parse_experiment_harness_gate_v1(text: str) -> ExperimentHarnessGateDecision:
    """Parse the exact reviewed Harness Gate outcome without self-authorization."""

    if type(text) is not str or not text or _CONTROL_RE.search(text):
        raise ExperimentBaselineGateError("Harness Gate text is invalid")
    section = _latest_relevant_top_level_section(
        text,
        pass_token="EXPERIMENT E-B0 HARNESS PASS",
        fail_token="EXPERIMENT E-B0 HARNESS FAIL",
    )
    task_matches = re.findall(
        r"^Harness Gate 来源任务：`([0-9a-f-]+)`\s*$",
        section,
        flags=re.MULTILINE,
    )
    if (
        task_matches != [EXPERIMENT_HARNESS_GATE_TASK_ID]
        or _UUID7_RE.fullmatch(task_matches[0]) is None
    ):
        raise ExperimentBaselineGateError("Harness Gate task identity mismatch")
    decision_block, _ = _latest_exact_decision_block(
        section,
        pass_token="EXPERIMENT E-B0 HARNESS PASS",
        fail_token="EXPERIMENT E-B0 HARNESS FAIL",
    )
    decision_lines = tuple(
        _token_line(line) for line in decision_block.splitlines() if line.strip()
    )
    passed = decision_lines[:1] == ("EXPERIMENT E-B0 HARNESS PASS",)
    expected_block = (
        "EXPERIMENT E-B0 HARNESS PASS" if passed else "EXPERIMENT E-B0 HARNESS FAIL",
        "P0 findings: 0",
        "P1 findings: 0" if passed else "P1 findings: 2",
        (
            "E-B0 ISOLATED PRODUCTION RUN AUTHORIZED"
            if passed
            else "E-B0 ISOLATED PRODUCTION RUN NOT AUTHORIZED"
        ),
        "E-B0 Run/metrics/artifact/qualification: NONE",
        "E-B0 production Run executed: false",
        "E1: NOT_AUTHORIZED",
    )
    _require_exact_decision_block(decision_block, expected_block)
    for prefix, token in (
        ("EXPERIMENT E-B0 HARNESS ", expected_block[0]),
        ("P0 findings:", expected_block[1]),
        ("P1 findings:", expected_block[2]),
        ("E-B0 ISOLATED PRODUCTION RUN ", expected_block[3]),
        ("E-B0 Run/metrics/artifact/qualification:", expected_block[4]),
        ("E-B0 production Run executed:", expected_block[5]),
        ("E1:", expected_block[6]),
    ):
        _require_whole_section_lines(section, prefix=prefix, expected=(token,))
    component_hash = (
        EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST
        if passed
        else _FAILED_HARNESS_GATE_COMPONENT_SET_DIGEST
    )
    for label, value in (
        ("release", EXPERIMENT_FOUNDATION_RELEASE_ID),
        ("package", EXPERIMENT_GOLDEN_PACKAGE_HASH),
        ("authority", EXPERIMENT_GOLDEN_AUTHORITY_HASH),
        ("recipe", EXPERIMENT_FOUNDATION_RECIPE_HASH),
        ("foundation-component", EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST),
        (
            "harness-component",
            component_hash,
        ),
        ("parity", EXPERIMENT_PRODUCTION_PARITY_HASH),
        ("artifact", EXPERIMENT_BASELINE_ARTIFACT_VERSION),
    ):
        _require_identity_line(section, label, value)
    payload = {
        "harness_task_id": EXPERIMENT_HARNESS_GATE_TASK_ID,
        "conclusion": expected_block[0],
        "p0_findings": 0,
        "p1_findings": 0 if passed else 2,
        "run_authorization": expected_block[3],
        "authorized": passed,
        "e1_status": "NOT_AUTHORIZED",
        "foundation_authority": ExperimentFoundationAuthority().model_dump(mode="json"),
        "production_component_hash": component_hash,
        "artifact_version": EXPERIMENT_BASELINE_ARTIFACT_VERSION,
    }
    try:
        return ExperimentHarnessGateDecision(
            **payload,
            decision_digest=_sha256(_canonical_json(payload)),
        )
    except ValueError as exc:
        raise ExperimentBaselineGateError(f"invalid Harness Gate: {exc}") from exc


def load_experiment_foundation_gate_v1(
    gate_path: str | os.PathLike[str] | None = None,
) -> ExperimentFoundationGateDecision:
    path = _require_canonical_gate_path(
        gate_path,
        relative=_FOUNDATION_GATE_RELATIVE_PATH,
        must_exist=True,
    )
    return parse_experiment_foundation_gate_v1(path.read_text(encoding="utf-8"))


def load_experiment_harness_gate_v1(
    gate_path: str | os.PathLike[str],
) -> ExperimentHarnessGateDecision:
    path = _require_canonical_gate_path(
        gate_path,
        relative=_HARNESS_GATE_RELATIVE_PATH,
        must_exist=True,
    )
    return parse_experiment_harness_gate_v1(path.read_text(encoding="utf-8"))


def verify_experiment_foundation_authority_v1(
    dataset: ExperimentGoldenDataset | None = None,
    *,
    golden_root: str | os.PathLike[str] | None = None,
) -> ExperimentFoundationAuthority:
    """Bind the released dataset, recipe, parity, membership, and denominators."""

    if dataset is not None and golden_root is not None:
        raise ExperimentBaselineAuthorityError(
            "provide either a loaded dataset or golden_root, not both"
        )
    authority = dataset or load_experiment_golden_v1(golden_root)
    expected = ExperimentFoundationAuthority()
    actual_slice_counts = {key.value: value for key, value in EXPECTED_SLICE_COUNTS.items()}
    actual_denominators = {
        metric.value: len(authority.manifest.eligible_metric_case_ids[metric])
        for metric in ExperimentEvaluationMetric
    }
    actual = {
        "dataset_id": authority.dataset_id,
        "dataset_version": authority.dataset_version,
        "release_record_id": authority.release_record_id,
        "package_hash": authority.package_hash,
        "authority_hash": authority.authority_hash,
        "recipe_hash": authority.recipe.canonical_sha256(),
        "component_hash": authority.recipe.production_authority.component_set_digest,
        "parity_hash": EXPERIMENT_PRODUCTION_PARITY_HASH,
        "case_count": len(authority.cases),
        "membership": authority.case_membership,
        "slice_counts": actual_slice_counts,
        "eligible_denominators": actual_denominators,
        "logical_counts": authority.recipe.logical_counts,
    }
    if actual != expected.model_dump(mode="python", exclude={"schema_version"}):
        raise ExperimentBaselineAuthorityError(
            "released Experiment Foundation identity differs from Gate authority"
        )
    return expected


def prepare_experiment_baseline_v1(
    *,
    gate_path: str | os.PathLike[str] | None = None,
    golden_root: str | os.PathLike[str] | None = None,
) -> ExperimentBaselinePreparation:
    """Prepare E-B0 only; create no Run, metric, SQLite, or artifact."""

    production_authority = verify_experiment_baseline_production_authority_v1()
    gate = load_experiment_foundation_gate_v1(gate_path)
    foundation = verify_experiment_foundation_authority_v1(golden_root=golden_root)
    if gate.authority != foundation:
        raise ExperimentBaselineAuthorityError(
            "Foundation Gate decision differs from released authority"
        )
    return ExperimentBaselinePreparation(
        foundation_gate_decision=gate,
        foundation_authority=foundation,
        production_authority=production_authority,
    )


def admit_experiment_baseline_artifact_layout(
    files: Sequence[str],
) -> ExperimentBaselineArtifactContract:
    """Validate exact portable artifact membership without filesystem I/O."""

    parsed = tuple(files)
    if parsed != EXPERIMENT_BASELINE_ARTIFACT_FILES:
        raise ExperimentBaselineArtifactError(
            "E-B0 artifact files must exactly match canonical membership/order"
        )
    return ExperimentBaselineArtifactContract(files=parsed)


def _check_existing_components_for_symlink(path: Path, stop: Path) -> None:
    current = path
    while current != stop and stop in current.parents:
        if current.exists() and current.is_symlink():
            raise ExperimentBaselinePathError("symlink path components are forbidden")
        current = current.parent


def _admit_temp_root(temp_root: str | os.PathLike[str]) -> Path:
    root = Path(os.fspath(temp_root))
    system_temp = Path(tempfile.gettempdir()).resolve()
    root_absolute = root.absolute()
    root_resolved = root.resolve(strict=False)
    if (
        root_absolute != root_resolved
        or root_resolved == system_temp
        or system_temp not in root_resolved.parents
        or not root_resolved.is_dir()
        or root.is_symlink()
    ):
        raise ExperimentBaselinePathError(
            "temp_root must be an existing, canonical, nonsymlink system-temp child"
        )
    return root_resolved


def _admit_new_child(path_value: str | os.PathLike[str], root: Path, label: str) -> Path:
    path = Path(os.fspath(path_value))
    if root in path.absolute().parents:
        _check_existing_components_for_symlink(path.absolute(), root)
    if path.is_symlink():
        raise ExperimentBaselinePathError(f"{label} cannot be a symlink")
    if path.absolute() != path or path.resolve(strict=False) != path:
        raise ExperimentBaselinePathError(f"{label} must be an explicit canonical path")
    if root not in path.parents:
        raise ExperimentBaselinePathError(f"{label} must be below temp_root")
    if path.exists():
        raise ExperimentBaselinePathError(f"{label} must be new")
    forbidden = {
        "evidence-rag.sqlite3",
        "evidence-rag.sqlite3-wal",
        "evidence-rag.sqlite3-shm",
    }
    if any(part in forbidden for part in path.parts):
        raise ExperimentBaselinePathError("formal/default database and sidecars are forbidden")
    if path.name.endswith(("-wal", "-shm", ".pyc")) or "__pycache__" in path.parts:
        raise ExperimentBaselinePathError(f"{label} sidecar/compiled path is forbidden")
    return path


def admit_experiment_baseline_paths(
    *,
    temp_root: str | os.PathLike[str],
    database_path: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
) -> ExperimentBaselinePathAdmission:
    """Read-only legacy-compatible admission for one DB plus one output."""

    root = _admit_temp_root(temp_root)
    database = _admit_new_child(database_path, root, "database")
    output = _admit_new_child(output_directory, root, "output")
    if database.suffix != ".sqlite3":
        raise ExperimentBaselinePathError("isolated database must use .sqlite3 suffix")
    if output.suffix or output.name in {"evals", "experiment"}:
        raise ExperimentBaselinePathError(
            "output must be a new dedicated extensionless run directory"
        )
    if database == output or database in output.parents or output in database.parents:
        raise ExperimentBaselinePathError("database/output paths must be separate")
    return ExperimentBaselinePathAdmission(
        temp_root=str(root),
        database_path=str(database),
        output_directory=str(output),
    )


def admit_experiment_baseline_run_paths(
    *,
    temp_root: str | os.PathLike[str],
    mlflow_tracking_root: str | os.PathLike[str],
    raw_database_path: str | os.PathLike[str],
    manual_database_path: str | os.PathLike[str],
    output_directory: str | os.PathLike[str],
) -> ExperimentBaselineRunPathAdmission:
    """Admit explicit new FileStore/raw SQLite/manual SQLite/output paths."""

    root = _admit_temp_root(temp_root)
    tracking = _admit_new_child(mlflow_tracking_root, root, "MLflow tracking root")
    raw_database = _admit_new_child(raw_database_path, root, "raw database")
    manual_database = _admit_new_child(manual_database_path, root, "manual database")
    output = _admit_new_child(output_directory, root, "output")
    if raw_database.suffix != ".sqlite3" or manual_database.suffix != ".sqlite3":
        raise ExperimentBaselinePathError("both isolated databases must use .sqlite3")
    if tracking.suffix or output.suffix:
        raise ExperimentBaselinePathError("tracking/output paths must be extensionless directories")
    if output.name in {"evals", "experiment"}:
        raise ExperimentBaselinePathError("formal output tree names are forbidden")
    if (
        raw_database.parent == root
        or manual_database.parent == root
        or raw_database.parent.exists()
        or manual_database.parent.exists()
    ):
        raise ExperimentBaselinePathError(
            "each database requires a new dedicated runtime directory"
        )
    paths = (tracking, raw_database.parent, manual_database.parent, output)
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            if path == other or path in other.parents or other in path.parents:
                raise ExperimentBaselinePathError("tracking/databases/output must be disjoint")
    return ExperimentBaselineRunPathAdmission(
        temp_root=str(root),
        mlflow_tracking_root=str(tracking),
        raw_database_path=str(raw_database),
        manual_database_path=str(manual_database),
        output_directory=str(output),
    )


def _cleanup_future_runtime_path(path: Path, root: Path) -> None:
    if path.is_symlink():
        raise ExperimentBaselinePathError("refusing symlink future runtime cleanup")
    resolved = path.resolve(strict=False)
    if root not in resolved.parents or resolved == root:
        raise ExperimentBaselinePathError("refusing unsafe future runtime cleanup")
    if not resolved.exists():
        return
    for child in sorted(
        resolved.rglob("*"),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    resolved.rmdir()


def _strict_json_loads(raw: bytes, *, label: str) -> object:
    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ExperimentBaselineArtifactError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ExperimentBaselineArtifactError(f"non-finite JSON number in {label}: {value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentBaselineArtifactError(f"invalid JSON in {label}") from exc
    _assert_finite(value, label=label)
    return value


def _assert_finite(value: object, *, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ExperimentBaselineArtifactError(f"non-finite value in {label}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(key, label=label)
            _assert_finite(item, label=label)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _assert_finite(item, label=label)


def _payment_like(text: str) -> bool:
    return any(
        13 <= len(re.sub(r"\D", "", match.group(0))) <= 19 for match in _PAYMENT_RE.finditer(text)
    )


def _security_findings(value: object, *, location: str) -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            findings.extend(_security_findings(str(key), location=f"{location}.<key>"))
            findings.extend(_security_findings(item, location=f"{location}.{key}"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            findings.extend(_security_findings(item, location=f"{location}[{index}]"))
    elif isinstance(value, str):
        if _CONTROL_RE.search(value):
            findings.append(f"{location}:control")
        if (
            _POSIX_ABSOLUTE_RE.search(value)
            or _GENERIC_POSIX_ABSOLUTE_RE.search(value)
            or _WINDOWS_ABSOLUTE_RE.search(value)
        ):
            findings.append(f"{location}:absolute_path")
        if _EMAIL_RE.search(value):
            findings.append(f"{location}:email")
        if _CREDENTIAL_RE.search(value):
            findings.append(f"{location}:credential")
        if _payment_like(value):
            findings.append(f"{location}:payment")
        lowered = value.casefold()
        if any(
            marker in lowered
            for marker in (
                ".sqlite3-wal",
                ".sqlite3-shm",
                ".sqlite3",
                ".db",
                "__pycache__",
                ".pyc",
            )
        ):
            findings.append(f"{location}:database_or_compiled")
        if value.startswith(("../", "./")) or "/../" in value or "\\..\\" in value:
            findings.append(f"{location}:relative_path_escape")
    return findings


def _safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    text = _POSIX_ABSOLUTE_RE.sub("[path]", text)
    text = _GENERIC_POSIX_ABSOLUTE_RE.sub("[path]", text)
    text = _WINDOWS_ABSOLUTE_RE.sub("[path]", text)
    text = _EMAIL_RE.sub("[email]", text)
    text = _CREDENTIAL_RE.sub("[credential]", text)
    text = _CONTROL_RE.sub(" ", text).strip()
    return text[:500] or type(exc).__name__


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_json(value) + b"\n")


def _write_jsonl(path: Path, values: Iterable[object]) -> None:
    path.write_bytes(b"".join(_canonical_json(value) + b"\n" for value in values))


def _read_json(path: Path) -> dict[str, Any]:
    value = _strict_json_loads(path.read_bytes(), label=path.name)
    if not isinstance(value, dict):
        raise ExperimentBaselineArtifactError(f"{path.name} must be a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line:
            raise ExperimentBaselineArtifactError(f"{path.name}:{line_number} is empty")
        value = _strict_json_loads(
            line,
            label=f"{path.name}:{line_number}",
        )
        if not isinstance(value, dict):
            raise ExperimentBaselineArtifactError(f"{path.name}:{line_number} must be an object")
        rows.append(value)
    return rows


def _artifact_file_records(
    directory: Path,
    names: Sequence[str],
) -> dict[str, dict[str, object]]:
    return {
        name: {
            "sha256": _sha256((directory / name).read_bytes()),
            "size": (directory / name).stat().st_size,
        }
        for name in names
    }


def _cleanup_incomplete_output(output: Path) -> None:
    if not output.exists() or output.is_symlink():
        return
    for name in EXPERIMENT_BASELINE_ARTIFACT_FILES:
        path = output / name
        if path.is_file() and not path.is_symlink():
            path.unlink()
    with suppress(OSError):
        output.rmdir()


def _artifact_metrics_payload(evaluation: Any) -> dict[str, object]:
    return {
        "schema_version": "experiment-e-b0-metrics-v1",
        "status": "NON_QUALIFIED",
        "overall": evaluation.overall.model_dump(mode="json"),
    }


def _artifact_slice_payload(evaluation: Any) -> dict[str, object]:
    return {
        "schema_version": "experiment-e-b0-slice-report-v1",
        "status": "NON_QUALIFIED",
        "case_results": [item.model_dump(mode="json") for item in evaluation.case_results],
        "slices": [item.model_dump(mode="json") for item in evaluation.slices],
    }


def _artifact_run_id(
    *,
    prediction_payloads: Sequence[Mapping[str, object]],
    comparison_payloads: Sequence[Mapping[str, object]],
    seed: int,
    component_digest: str,
) -> str:
    run_digest = _sha256(
        _canonical_json(
            {
                "predictions": prediction_payloads,
                "comparisons": comparison_payloads,
                "seed": seed,
                "component": component_digest,
            }
        )
    )
    return f"eb0-{run_digest.removeprefix('sha256:')[:32]}"


def _artifact_authority_boundary_counts(
    dataset: ExperimentGoldenDataset,
    predictions: Sequence[ExperimentBaselinePrediction],
) -> tuple[int, int]:
    acl_violations = 0
    locator_violations = 0
    for prediction in predictions:
        for candidate in prediction.candidates:
            authority = dataset.authority_entities.get(candidate.entity_id)
            if authority is None:
                acl_violations += 1
                locator_violations += 1
                continue
            if candidate.acl_ref != authority.acl_ref:
                acl_violations += 1
            if (
                candidate.entity_kind != authority.kind
                or candidate.logical_locator != authority.locator
            ):
                locator_violations += 1
    return acl_violations, locator_violations


def _build_experiment_baseline_artifact_v1(
    *,
    output_directory: Path,
    dataset: ExperimentGoldenDataset,
    predictions: Sequence[ExperimentBaselinePrediction],
    comparisons: Sequence[ExperimentComparisonRecord],
    production_authority: ExperimentBaselineProductionAuthority,
    execution_mode: Literal["AUTHORIZED_RELEASED_45", "TEST_FIXTURE"],
    seed: int,
) -> ExperimentBaselineResult:
    """Internal writer; callers must complete Gate/path admission first."""

    foundation = verify_experiment_foundation_authority_v1(dataset)
    if production_authority != fixed_experiment_baseline_production_authority_v1():
        raise ExperimentBaselineAuthorityError("artifact production authority drift")
    case_ids = dataset.case_membership
    if tuple(item.case_id for item in predictions) != case_ids:
        raise ExperimentBaselineArtifactError("predictions must exactly match released ordered45")
    if tuple(item.case_id for item in comparisons) != case_ids:
        raise ExperimentBaselineArtifactError(
            "comparison rows must exactly match released ordered45"
        )
    for case, prediction in zip(dataset.cases, predictions, strict=True):
        if prediction.question != case.question:
            raise ExperimentBaselineArtifactError(
                "prediction question differs from released case input"
            )
    reviewed_rows = tuple(prediction.reviewed_row() for prediction in predictions)
    evaluation = evaluate_reviewed_experiment_retrieval(
        reviewed_rows,
        dataset=dataset,
    )
    prediction_payloads = [prediction.model_dump(mode="json") for prediction in predictions]
    comparison_payloads = [comparison.model_dump(mode="json") for comparison in comparisons]
    run_id = _artifact_run_id(
        prediction_payloads=prediction_payloads,
        comparison_payloads=comparison_payloads,
        seed=seed,
        component_digest=production_authority.component_set_digest,
    )
    manifest = ExperimentBaselineArtifactManifest(
        run_id=run_id,
        run_uri=(f"evaluation-run://project-experiment-eb0-v1/{run_id.removeprefix('eb0-')}"),
        execution_mode=execution_mode,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        membership=case_ids,
        seed=seed,
        foundation_authority=foundation,
        production_authority=production_authority,
    )
    errors = [
        {
            "schema_version": "experiment-e-b0-error-record-v1",
            "case_id": prediction.case_id,
            "error_reason": prediction.error_reason,
            "error": prediction.error,
        }
        for prediction in predictions
        if prediction.error is not None
    ]
    latency = {
        "schema_version": "experiment-e-b0-latency-v1",
        "status": "NON_QUALIFIED",
        "cases": [
            {
                "case_id": prediction.case_id,
                "latency_ms": prediction.latency_ms,
                "candidate_count": prediction.candidate_count,
            }
            for prediction in predictions
        ],
    }
    payloads: dict[str, object] = {
        "manifest.json": manifest.model_dump(mode="json"),
        "golden_cases.jsonl": [case.model_dump(mode="json") for case in dataset.cases],
        "predictions.jsonl": prediction_payloads,
        "comparison_results.jsonl": comparison_payloads,
        "metrics.json": _artifact_metrics_payload(evaluation),
        "slice_report.json": _artifact_slice_payload(evaluation),
        "errors.jsonl": errors,
        "latency.json": latency,
    }
    findings = [
        finding
        for name, payload in payloads.items()
        for finding in _security_findings(payload, location=name)
    ]
    if findings:
        raise ExperimentBaselineArtifactError(
            f"artifact security policy failed: {sorted(findings)[:3]}"
        )
    acl_violations, locator_violations = _artifact_authority_boundary_counts(
        dataset,
        predictions,
    )
    if acl_violations or locator_violations:
        raise ExperimentBaselineArtifactError(
            "artifact candidate ACL/logical-locator authority boundary failed"
        )
    security = {
        "schema_version": "experiment-e-b0-security-report-v2",
        "status": "PASS",
        "findings": [],
        "files_scanned": list(EXPERIMENT_BASELINE_CHECKSUM_TARGETS),
        "absolute_paths": 0,
        "emails": 0,
        "credentials": 0,
        "payments": 0,
        "database_or_compiled_files": 0,
        "nonfinite_values": 0,
        "secret_leakage": 0,
        "acl_violations": acl_violations,
        "cross_run_locator_violations": locator_violations,
        "formal_database_access": 0,
        "network_access": 0,
        "production_component_hash": production_authority.component_set_digest,
        "foundation_authority_hash": foundation.authority_hash,
    }
    payloads["security_report.json"] = security
    if output_directory.exists() or output_directory.is_symlink():
        raise ExperimentBaselinePathError("artifact output must be new")
    if not output_directory.parent.is_dir():
        raise ExperimentBaselinePathError("artifact output parent must already exist")
    output_directory.mkdir()
    try:
        jsonl_names = {
            "golden_cases.jsonl",
            "predictions.jsonl",
            "comparison_results.jsonl",
            "errors.jsonl",
        }
        for name in EXPERIMENT_BASELINE_CHECKSUM_TARGETS:
            payload = payloads[name]
            if name in jsonl_names:
                if not isinstance(payload, list):
                    raise ExperimentBaselineArtifactError(
                        f"internal JSONL payload mismatch: {name}"
                    )
                _write_jsonl(output_directory / name, payload)
            else:
                _write_json(output_directory / name, payload)
        checksums = {
            "schema_version": "experiment-e-b0-checksums-v1",
            "targets": list(EXPERIMENT_BASELINE_CHECKSUM_TARGETS),
            "files": _artifact_file_records(
                output_directory,
                EXPERIMENT_BASELINE_CHECKSUM_TARGETS,
            ),
        }
        _write_json(output_directory / "checksums.json", checksums)
        verification = verify_experiment_baseline_artifact_v1(
            output_directory,
            dataset=dataset,
        )
        if verification.run_id != run_id:
            raise ExperimentBaselineArtifactError("post-write verification run identity drift")
    except Exception:
        _cleanup_incomplete_output(output_directory)
        raise
    return ExperimentBaselineResult(
        run_id=run_id,
        artifact_directory=str(output_directory),
        case_count=45,
    )


def _require_artifact_tree(directory: Path) -> Path:
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or not stat.S_ISDIR(directory.lstat().st_mode)
    ):
        raise ExperimentBaselineArtifactError(
            "artifact root must be a regular nonsymlink directory"
        )
    names = tuple(sorted(path.name for path in directory.iterdir()))
    expected = tuple(sorted(EXPERIMENT_BASELINE_ARTIFACT_FILES))
    if names != expected:
        raise ExperimentBaselineArtifactError(
            "artifact file membership differs from canonical ten files"
        )
    for name in EXPERIMENT_BASELINE_ARTIFACT_FILES:
        path = directory / name
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ExperimentBaselineArtifactError(f"artifact member is not a regular file: {name}")
        if name.endswith((".sqlite3", "-wal", "-shm", ".pyc")):
            raise ExperimentBaselineArtifactError("database/compiled artifact forbidden")
    return directory


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    *,
    label: str,
) -> None:
    if set(value) != expected:
        raise ExperimentBaselineArtifactError(f"{label} keys differ; expected={sorted(expected)}")


def verify_experiment_baseline_artifact_v1(
    directory: str | os.PathLike[str],
    *,
    dataset: ExperimentGoldenDataset | None = None,
    golden_root: str | os.PathLike[str] | None = None,
) -> ExperimentBaselineVerification:
    """Offline verify-only rebuild from released truth plus predictions.

    This function never calls the adapter, service, platform, SQLite, runtime,
    or network.  It accepts a portable copy at any filesystem location.
    """

    if dataset is not None and golden_root is not None:
        raise ExperimentBaselineArtifactError("provide either dataset or golden_root, not both")
    root = _require_artifact_tree(Path(os.fspath(directory)))
    checksums = _read_json(root / "checksums.json")
    _require_exact_keys(
        checksums,
        {"schema_version", "targets", "files"},
        label="checksums",
    )
    if (
        checksums["schema_version"] != "experiment-e-b0-checksums-v1"
        or checksums["targets"] != list(EXPERIMENT_BASELINE_CHECKSUM_TARGETS)
        or not isinstance(checksums["files"], dict)
        or set(checksums["files"]) != set(EXPERIMENT_BASELINE_CHECKSUM_TARGETS)
    ):
        raise ExperimentBaselineArtifactError("checksum contract drift")
    actual_records = _artifact_file_records(
        root,
        EXPERIMENT_BASELINE_CHECKSUM_TARGETS,
    )
    if checksums["files"] != actual_records:
        raise ExperimentBaselineArtifactError("artifact checksum/size mismatch")

    manifest_payload = _read_json(root / "manifest.json")
    try:
        manifest = ExperimentBaselineArtifactManifest.model_validate(manifest_payload)
    except ValueError as exc:
        raise ExperimentBaselineArtifactError(f"invalid manifest: {exc}") from exc
    fixed_authority = fixed_experiment_baseline_production_authority_v1()
    if manifest.production_authority != fixed_authority:
        raise ExperimentBaselineArtifactError(
            "manifest production authority differs from fixed review"
        )
    authority = dataset or load_experiment_golden_v1(golden_root)
    foundation = verify_experiment_foundation_authority_v1(authority)
    if manifest.foundation_authority != foundation:
        raise ExperimentBaselineArtifactError("manifest Foundation authority mismatch")

    golden_rows = _read_jsonl(root / "golden_cases.jsonl")
    expected_golden_rows = [case.model_dump(mode="json") for case in authority.cases]
    if golden_rows != expected_golden_rows:
        raise ExperimentBaselineArtifactError("embedded released truth differs from fixed Golden")
    try:
        predictions = tuple(
            ExperimentBaselinePrediction.model_validate(row)
            for row in _read_jsonl(root / "predictions.jsonl")
        )
        comparisons = tuple(
            ExperimentComparisonRecord.model_validate(row)
            for row in _read_jsonl(root / "comparison_results.jsonl")
        )
    except ValueError as exc:
        raise ExperimentBaselineArtifactError(
            f"invalid prediction/comparison record: {exc}"
        ) from exc
    expected_run_id = _artifact_run_id(
        prediction_payloads=[prediction.model_dump(mode="json") for prediction in predictions],
        comparison_payloads=[comparison.model_dump(mode="json") for comparison in comparisons],
        seed=manifest.seed,
        component_digest=fixed_authority.component_set_digest,
    )
    if manifest.run_id != expected_run_id:
        raise ExperimentBaselineArtifactError(
            "manifest run identity does not bind predictions/comparisons"
        )
    membership = authority.case_membership
    if (
        tuple(item.case_id for item in predictions) != membership
        or tuple(item.case_id for item in comparisons) != membership
    ):
        raise ExperimentBaselineArtifactError("cross-file ordered45 identity mismatch")
    for case, prediction, comparison in zip(
        authority.cases,
        predictions,
        comparisons,
        strict=True,
    ):
        if (
            prediction.question != case.question
            or prediction.comparison_result != comparison.result
        ):
            raise ExperimentBaselineArtifactError(
                "cross-file question/comparison identity mismatch"
            )
    evaluation = evaluate_reviewed_experiment_retrieval(
        tuple(prediction.reviewed_row() for prediction in predictions),
        dataset=authority,
    )
    if _read_json(root / "metrics.json") != _artifact_metrics_payload(evaluation):
        raise ExperimentBaselineArtifactError(
            "metrics do not recompute from released truth plus predictions"
        )
    if _read_json(root / "slice_report.json") != _artifact_slice_payload(evaluation):
        raise ExperimentBaselineArtifactError("slice report/denominators do not recompute")
    expected_errors = [
        {
            "schema_version": "experiment-e-b0-error-record-v1",
            "case_id": prediction.case_id,
            "error_reason": (
                prediction.error_reason.value if prediction.error_reason is not None else None
            ),
            "error": prediction.error,
        }
        for prediction in predictions
        if prediction.error is not None
    ]
    if _read_jsonl(root / "errors.jsonl") != expected_errors:
        raise ExperimentBaselineArtifactError("error ledger differs from predictions")
    expected_latency = {
        "schema_version": "experiment-e-b0-latency-v1",
        "status": "NON_QUALIFIED",
        "cases": [
            {
                "case_id": prediction.case_id,
                "latency_ms": prediction.latency_ms,
                "candidate_count": prediction.candidate_count,
            }
            for prediction in predictions
        ],
    }
    if _read_json(root / "latency.json") != expected_latency:
        raise ExperimentBaselineArtifactError("latency/candidate counts differ from predictions")
    security = _read_json(root / "security_report.json")
    acl_violations, locator_violations = _artifact_authority_boundary_counts(
        authority,
        predictions,
    )
    expected_security = {
        "schema_version": "experiment-e-b0-security-report-v2",
        "status": "PASS",
        "findings": [],
        "files_scanned": list(EXPERIMENT_BASELINE_CHECKSUM_TARGETS),
        "absolute_paths": 0,
        "emails": 0,
        "credentials": 0,
        "payments": 0,
        "database_or_compiled_files": 0,
        "nonfinite_values": 0,
        "secret_leakage": 0,
        "acl_violations": acl_violations,
        "cross_run_locator_violations": locator_violations,
        "formal_database_access": 0,
        "network_access": 0,
        "production_component_hash": fixed_authority.component_set_digest,
        "foundation_authority_hash": foundation.authority_hash,
    }
    if acl_violations or locator_violations or security != expected_security:
        raise ExperimentBaselineArtifactError("security report contract drift")
    parsed_values: dict[str, object] = {
        "manifest.json": manifest_payload,
        "golden_cases.jsonl": golden_rows,
        "predictions.jsonl": [prediction.model_dump(mode="json") for prediction in predictions],
        "comparison_results.jsonl": [
            comparison.model_dump(mode="json") for comparison in comparisons
        ],
        "metrics.json": _read_json(root / "metrics.json"),
        "slice_report.json": _read_json(root / "slice_report.json"),
        "errors.jsonl": expected_errors,
        "latency.json": expected_latency,
        "security_report.json": security,
        "checksums.json": checksums,
    }
    findings = [
        finding
        for name, value in parsed_values.items()
        for finding in _security_findings(value, location=name)
    ]
    if findings:
        raise ExperimentBaselineArtifactError(
            f"artifact security scan failed: {sorted(findings)[:3]}"
        )
    return ExperimentBaselineVerification(
        run_id=manifest.run_id,
        case_count=45,
    )


def _runtime_settings(root: Path, database_path: Path) -> Settings:
    return Settings(
        data_dir=root,
        database_path=database_path,
        repository_cache=root / "repositories",
        web_dir=_repository_root() / "web",
        allowed_local_roots=(root.parent,),
        project_root=root,
        embedding_dimensions=32,
        rag_code_engine="v1",
        rag_code_shadow=False,
        rag_code_unit_builder="raw-v1",
        rag_code_graph=False,
        rag_code_semantic_resolver="off",
        rag_code_reranker="off",
        rag_code_context="snippet-v1",
        rag_code_canary_percent=0,
    )


def _ensure_fixture_project(runtime: Any, recipe: ExperimentFixtureRecipe) -> None:
    runtime.workspace.create_project(
        ProjectCreate(
            id=recipe.project_id,
            name="Experiment E-B0 isolated fixture",
            acl_ref=recipe.acl_ref,
            classification="internal",
        ),
        audit=False,
    )


def _manual_run_request(experiment_id: str, run: FixtureRun) -> RunCreate:
    return RunCreate(
        experiment_id=experiment_id,
        external_id=run.external_id,
        name=run.name,
        status=run.status,
        commit_sha=run.commit_sha,
        branch=run.branch,
        dataset_id=run.dataset_id,
        dataset_version=run.dataset_version,
        config=run.config,
        environment=run.environment,
        command=run.command,
        started_at=datetime.fromtimestamp(
            run.started_at_ms / 1000,
            tz=UTC,
        ).isoformat(),
        completed_at=(
            datetime.fromtimestamp(run.completed_at_ms / 1000, tz=UTC).isoformat()
            if run.completed_at_ms is not None
            else None
        ),
        metrics=[
            MetricInput(
                name=metric.name,
                value=metric.latest.value,
                unit=metric.unit,
                split=metric.split,
                step=metric.latest.step,
                higher_is_better=metric.direction != FixtureMetricDirection.LOWER,
            )
            for metric in run.metrics
        ],
        artifacts=[
            ArtifactInput(
                name=artifact.name,
                uri=artifact.logical_uri,
                kind=artifact.kind,
                checksum=artifact.checksum,
                media_type=artifact.media_type,
                metadata={
                    "verification_state": artifact.verification_state,
                    "fixture_only": True,
                },
            )
            for artifact in run.artifacts
        ],
        tags={"fixture.group": run.group, "fixture.seed": str(run.seed)},
    )


def _populate_manual_runtime(
    runtime: Any,
    recipe: ExperimentFixtureRecipe,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    _ensure_fixture_project(runtime, recipe)
    experiment = runtime.experiments.create_experiment(
        ExperimentCreate(
            project_id=recipe.project_id,
            title=recipe.title,
            objective=recipe.objective,
            owner="Experiment E-B0 fixture",
            status="running",
            tags=["experiment-e-b0", "isolated-fixture"],
        )
    )
    runs = {
        fixture_run.logical_run_id: runtime.experiments.create_run(
            _manual_run_request(experiment["id"], fixture_run)
        )
        for fixture_run in recipe.runs
    }
    return experiment, runs


def run_experiment_baseline_smoke_v1(
    *,
    temp_root: str | os.PathLike[str],
    seed: int = EXPERIMENT_BASELINE_SEED,
) -> ExperimentBaselineSmokeResult:
    """Run a small current-V1 production smoke, never the released45 baseline."""

    if seed != EXPERIMENT_BASELINE_SEED:
        raise ExperimentBaselineError("smoke seed is frozen")
    verify_experiment_baseline_production_authority_v1()
    root = _admit_temp_root(temp_root)
    tracking = root / "smoke-mlflow"
    raw_database = root / "smoke-raw-runtime" / "raw.sqlite3"
    manual_database = root / "smoke-manual-runtime" / "manual.sqlite3"
    for path, label in (
        (tracking, "smoke tracking"),
        (raw_database, "smoke raw database"),
        (manual_database, "smoke manual database"),
    ):
        _admit_new_child(path, root, label)
    recipe = deterministic_experiment_recipe()
    materialize_mlflow_filestore(tracking, recipe)
    raw_runtime = create_runtime(_runtime_settings(raw_database.parent, raw_database))
    _ensure_fixture_project(raw_runtime, recipe)
    sync_result = raw_runtime.experiments.sync_mlflow(
        MLflowSyncRequest(
            project_id=recipe.project_id,
            tracking_uri=str(tracking),
            experiment_ids=[recipe.external_experiment_id],
            max_runs=100,
            acl_ref=recipe.acl_ref,
        )
    )
    search = raw_runtime.platform.search(
        GlobalSearchRequest(
            query="treatment seed 11 ndcg",
            project_id=recipe.project_id,
            sources=["experiment"],
            limit=8,
            include_lineage=False,
            allowed_acl_refs=[recipe.acl_ref],
            enforce_acl=True,
        ),
        record_event=False,
    )
    manual_runtime = create_runtime(_runtime_settings(manual_database.parent, manual_database))
    _, manual_runs = _populate_manual_runtime(manual_runtime, recipe)
    comparison = manual_runtime.experiments.compare(
        ComparisonRequest(
            run_ids=[
                manual_runs["run://fixture/baseline-seed-11"]["id"],
                manual_runs["run://fixture/treatment-seed-11"]["id"],
            ],
            baseline_run_id=manual_runs["run://fixture/baseline-seed-11"]["id"],
            name="E-B0 nonqualified smoke comparison",
        )
    )
    result = comparison["result"]
    return ExperimentBaselineSmokeResult(
        logical_counts=recipe.logical_counts,
        mlflow_synced_runs=sync_result["stats"]["runs"],
        manual_created_runs=len(manual_runs),
        search_candidate_count=search["total"],
        search_entity_kinds=tuple(str(item["entity_type"]) for item in search["results"]),
        search_matched_terms=tuple(
            dict.fromkeys(
                str(term) for item in search["results"] for term in item.get("matched_terms", [])
            )
        ),
        comparison_controls=tuple(result["controls"]),
        comparison_config_difference_count=len(result["config_differences"]),
        comparison_metric_rows=len(result["metrics"]),
    )


def _logical_maps(
    runtime: Any,
    recipe: ExperimentFixtureRecipe,
) -> tuple[dict[str, tuple[str, EntityKind, str]], dict[str, str]]:
    experiments = runtime.experiments.store.list_experiments(recipe.project_id)
    if len(experiments) != 1:
        raise ExperimentBaselineError("isolated runtime Experiment membership drift")
    production_experiment_id = experiments[0]["id"]
    entity_map: dict[str, tuple[str, EntityKind, str]] = {
        production_experiment_id: (
            recipe.logical_experiment_id,
            EntityKind.EXPERIMENT,
            recipe.logical_experiment_id,
        )
    }
    logical_to_production: dict[str, str] = {}
    recipe_by_external = {run.external_id: run for run in recipe.runs}
    for row in runtime.experiments.store.list_runs(recipe.project_id):
        detail = runtime.experiments.store.get_run(row["id"])
        if detail is None:
            continue
        fixture_run = recipe_by_external.get(str(detail.get("external_id")))
        if fixture_run is None:
            continue
        run_slug = fixture_run.logical_run_id.removeprefix("run://fixture/")
        locator = f"{recipe.logical_experiment_id}/runs/{run_slug}"
        entity_map[detail["id"]] = (
            fixture_run.logical_run_id,
            EntityKind.RUN,
            locator,
        )
        logical_to_production[fixture_run.logical_run_id] = detail["id"]
        fixture_metrics = {metric.name: metric for metric in fixture_run.metrics}
        for metric in detail.get("metrics", []):
            fixture_metric = fixture_metrics.get(str(metric.get("name")))
            if fixture_metric is None:
                continue
            latest = fixture_metric.latest
            entity_map[metric["id"]] = (
                latest.observation_id,
                EntityKind.METRIC_OBSERVATION,
                latest.locator,
            )
    return entity_map, logical_to_production


def _portable_raw_structured_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "entity_id",
        "source",
        "entity_type",
        "title",
        "subtitle",
        "snippet",
        "locator",
        "status",
        "updated_at",
        "version",
        "score",
        "channels",
        "matched_terms",
        "authority",
        "version_alignment",
        "relation_only",
    )
    payload = {field: item.get(field) for field in fields}
    findings = _security_findings(payload, location="raw_structured_fields")
    if findings:
        for field in ("snippet", "locator", "subtitle", "title"):
            payload[field] = "[portable-redacted]" if payload.get(field) else None
    return payload


def _logicalize_comparison_result(
    result: Mapping[str, Any],
    production_to_logical: Mapping[str, str],
) -> dict[str, Any]:
    def convert(value: object, key: str | None = None) -> object:
        if isinstance(value, Mapping):
            return {
                str(item_key): convert(item_value, str(item_key))
                for item_key, item_value in value.items()
                if item_key not in {"id", "display_key", "created_at", "updated_at"}
            }
        if isinstance(value, list):
            return [convert(item, key) for item in value]
        if key in {"run_id", "baseline_run_id"} and isinstance(value, str):
            return production_to_logical.get(value, value)
        return value

    converted = convert(result)
    if not isinstance(converted, dict):
        raise ExperimentBaselineError("comparison result is not an object")
    return converted


def _comparison_logical_ids(question: str) -> tuple[str, str] | None:
    lowered = question.casefold()
    if "baseline seed 11" not in lowered:
        return None
    aliases = {
        "treatment-seed-11": "run://fixture/treatment-seed-11",
        "treatment seed 11": "run://fixture/treatment-seed-11",
        "treatment-seed-22": "run://fixture/treatment-seed-22",
        "treatment seed 22": "run://fixture/treatment-seed-22",
        "dataset-v2-mismatch": "run://fixture/dataset-v2-mismatch",
        "percent-unit-mismatch": "run://fixture/percent-unit-mismatch",
        "failed-high-score": "run://fixture/failed-high-score",
        "running-candidate": "run://fixture/running-candidate",
        "missing-commit-environment": "run://fixture/missing-commit-environment",
        "typed-config-string-one": "run://fixture/typed-config-string-one",
    }
    for alias, logical in aliases.items():
        if alias in lowered:
            return "run://fixture/baseline-seed-11", logical
    return None


def _execute_released_cases(
    *,
    raw_runtime: Any,
    manual_runtime: Any,
    dataset: ExperimentGoldenDataset,
    recipe: ExperimentFixtureRecipe,
    raw_entity_map: Mapping[str, tuple[str, EntityKind, str]],
    manual_logical_to_production: Mapping[str, str],
) -> tuple[
    tuple[ExperimentBaselinePrediction, ...],
    tuple[ExperimentComparisonRecord, ...],
]:
    predictions: list[ExperimentBaselinePrediction] = []
    comparisons: list[ExperimentComparisonRecord] = []
    production_to_logical = {
        production: logical for logical, production in manual_logical_to_production.items()
    }
    for case in dataset.cases:
        started = time.perf_counter()
        comparison_result: dict[str, Any] | None = None
        comparison_record = ExperimentComparisonRecord(
            case_id=case.case_id,
            attempted=False,
        )
        try:
            response = raw_runtime.platform.search(
                GlobalSearchRequest(
                    query=case.question,
                    project_id=recipe.project_id,
                    sources=["experiment"],
                    limit=30,
                    include_lineage=False,
                    allowed_acl_refs=[recipe.acl_ref],
                    enforce_acl=True,
                ),
                record_event=False,
            )
            candidates: list[ExperimentBaselineCandidate] = []
            for item in response["results"]:
                mapped = raw_entity_map.get(str(item["entity_id"]))
                if mapped is None:
                    continue
                logical_id, kind, locator = mapped
                candidates.append(
                    ExperimentBaselineCandidate(
                        rank=len(candidates) + 1,
                        entity_id=logical_id,
                        entity_kind=kind,
                        logical_locator=locator,
                        acl_ref=recipe.acl_ref,
                        matched_terms=tuple(str(term) for term in item.get("matched_terms", [])),
                        raw_structured_fields=_portable_raw_structured_fields(item),
                    )
                )
            comparison_ids = _comparison_logical_ids(case.question)
            if case.task in {"run_comparison", "incomparable"}:
                if comparison_ids is None or any(
                    logical not in manual_logical_to_production for logical in comparison_ids
                ):
                    comparison_record = ExperimentComparisonRecord(
                        case_id=case.case_id,
                        attempted=True,
                        unavailable_reason=ReviewedReason.UNSUPPORTED_V1,
                    )
                else:
                    baseline_logical, candidate_logical = comparison_ids
                    comparison = manual_runtime.experiments.compare(
                        ComparisonRequest(
                            run_ids=[
                                manual_logical_to_production[baseline_logical],
                                manual_logical_to_production[candidate_logical],
                            ],
                            baseline_run_id=manual_logical_to_production[baseline_logical],
                            name=f"E-B0 {case.case_id}",
                        )
                    )
                    comparison_result = _logicalize_comparison_result(
                        comparison["result"],
                        production_to_logical,
                    )
                    comparison_record = ExperimentComparisonRecord(
                        case_id=case.case_id,
                        attempted=True,
                        result=comparison_result,
                    )
            candidate_count = int(response["total"])
            if candidates:
                outcome = ReviewedOutcome.RETURNED
                observed_answerability = Answerability.ANSWERABLE
                unavailable_reason = None
            elif candidate_count == 0:
                outcome = ReviewedOutcome.ZERO_RESULT
                observed_answerability = Answerability.UNANSWERABLE
                unavailable_reason = None
            else:
                outcome = ReviewedOutcome.UNAVAILABLE
                observed_answerability = Answerability.INSUFFICIENT_METADATA
                unavailable_reason = ReviewedReason.UNSUPPORTED_V1
                candidate_count = 0
            prediction = ExperimentBaselinePrediction(
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.dataset_version,
                package_hash=dataset.package_hash,
                case_id=case.case_id,
                question=case.question,
                outcome=outcome,
                candidates=tuple(candidates) if outcome == ReviewedOutcome.RETURNED else (),
                candidate_count=(
                    int(response["total"])
                    if outcome == ReviewedOutcome.RETURNED
                    else candidate_count
                ),
                observed_answerability=observed_answerability,
                comparison_result=comparison_result,
                unavailable_reason=unavailable_reason,
                latency_ms=float(max(0.0, (time.perf_counter() - started) * 1000)),
            )
        except Exception as exc:
            safe = _safe_error(exc)
            prediction = ExperimentBaselinePrediction(
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.dataset_version,
                package_hash=dataset.package_hash,
                case_id=case.case_id,
                question=case.question,
                outcome=ReviewedOutcome.ERROR,
                candidate_count=0,
                observed_answerability=Answerability.INSUFFICIENT_METADATA,
                error_reason=ReviewedReason.SYSTEM_ERROR,
                error=safe,
                latency_ms=float(max(0.0, (time.perf_counter() - started) * 1000)),
            )
            comparison_record = ExperimentComparisonRecord(
                case_id=case.case_id,
                attempted=True,
                error=safe,
            )
        predictions.append(prediction)
        comparisons.append(comparison_record)
    return tuple(predictions), tuple(comparisons)


def _validate_fixture_recipe_path(
    path_value: str | os.PathLike[str],
    golden_root: Path,
) -> Path:
    path = Path(os.fspath(path_value))
    expected = (golden_root / "fixture_recipe.json").resolve(strict=False)
    if (
        path.absolute() != expected
        or path.resolve(strict=False) != expected
        or path.is_symlink()
        or not path.is_file()
    ):
        raise ExperimentBaselinePathError(
            "fixture recipe must be the explicit released Golden recipe"
        )
    return path


def run_experiment_baseline_v1(
    request: ExperimentBaselineRunRequest | None = None,
    *,
    gate_token: object | None = None,
    **_: Any,
) -> ExperimentBaselineResult:
    """Run only after a future independent canonical Harness Gate.

    With today's Foundation-only authority this rejects before inspecting any
    request path or path-like bomb.  The authorized branch is fully wired and
    does not contain an unconditional terminal raise.
    """

    if request is None:
        del gate_token
        raise ExperimentBaselineNotAuthorized(
            "E-B0 production execution NOT_AUTHORIZED: independent Harness Gate missing"
        )
    production_authority = verify_experiment_baseline_production_authority_v1()
    foundation_gate = load_experiment_foundation_gate_v1(request.foundation_gate_path)
    harness_gate = load_experiment_harness_gate_v1(request.harness_gate_path)
    if (
        not harness_gate.authorized
        or foundation_gate.authority != harness_gate.foundation_authority
        or harness_gate.production_component_hash != production_authority.component_set_digest
    ):
        raise ExperimentBaselineNotAuthorized(
            "E-B0 production execution NOT_AUTHORIZED: Gate authority mismatch"
        )
    golden_root = Path(request.golden_root)
    dataset = load_experiment_golden_v1(golden_root)
    foundation = verify_experiment_foundation_authority_v1(dataset)
    if foundation != foundation_gate.authority:
        raise ExperimentBaselineAuthorityError("runtime Golden differs from authorized Foundation")
    _validate_fixture_recipe_path(request.fixture_recipe_path, golden_root)
    admission = admit_experiment_baseline_run_paths(
        temp_root=request.temp_root,
        mlflow_tracking_root=request.mlflow_tracking_root,
        raw_database_path=request.raw_database_path,
        manual_database_path=request.manual_database_path,
        output_directory=request.output_directory,
    )
    recipe = deterministic_experiment_recipe()
    if recipe.canonical_sha256() != foundation.recipe_hash or recipe != dataset.recipe:
        raise ExperimentBaselineAuthorityError("runtime fixture recipe drift")
    tracking = Path(admission.mlflow_tracking_root)
    raw_database = Path(admission.raw_database_path)
    manual_database = Path(admission.manual_database_path)
    output = Path(admission.output_directory)
    try:
        materialize_mlflow_filestore(tracking, recipe)
        raw_runtime = create_runtime(_runtime_settings(raw_database.parent, raw_database))
        _ensure_fixture_project(raw_runtime, recipe)
        raw_runtime.experiments.sync_mlflow(
            MLflowSyncRequest(
                project_id=recipe.project_id,
                tracking_uri=str(tracking),
                experiment_ids=[recipe.external_experiment_id],
                max_runs=100,
                acl_ref=recipe.acl_ref,
            )
        )
        raw_entity_map, _ = _logical_maps(raw_runtime, recipe)
        manual_runtime = create_runtime(_runtime_settings(manual_database.parent, manual_database))
        _populate_manual_runtime(manual_runtime, recipe)
        _, manual_map = _logical_maps(manual_runtime, recipe)
        predictions, comparisons = _execute_released_cases(
            raw_runtime=raw_runtime,
            manual_runtime=manual_runtime,
            dataset=dataset,
            recipe=recipe,
            raw_entity_map=raw_entity_map,
            manual_logical_to_production=manual_map,
        )
        result = _build_experiment_baseline_artifact_v1(
            output_directory=output,
            dataset=dataset,
            predictions=predictions,
            comparisons=comparisons,
            production_authority=production_authority,
            execution_mode="AUTHORIZED_RELEASED_45",
            seed=request.seed,
        )
    finally:
        for derived in (tracking, raw_database.parent, manual_database.parent):
            _cleanup_future_runtime_path(derived, Path(admission.temp_root))
    verify_experiment_baseline_artifact_v1(output, dataset=dataset)
    return result


def authorize_experiment_baseline_v1(gate_token: object | None = None) -> None:
    """Reject token-only authorization; a canonical independent Gate is required."""

    del gate_token
    raise ExperimentBaselineNotAuthorized(
        "E-B0 production execution NOT_AUTHORIZED: token-only authorization forbidden"
    )


__all__ = [
    "EXPERIMENT_BASELINE_ARTIFACT_FILES",
    "EXPERIMENT_BASELINE_ARTIFACT_VERSION",
    "EXPERIMENT_BASELINE_CHECKSUM_TARGETS",
    "EXPERIMENT_BASELINE_CURRENT_PRODUCTION_AUTHORITY_VERSION",
    "EXPERIMENT_BASELINE_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST",
    "EXPERIMENT_BASELINE_EXECUTION_STATUS",
    "EXPERIMENT_BASELINE_GATE_STATUS",
    "EXPERIMENT_BASELINE_PATH_POLICY_VERSION",
    "EXPERIMENT_BASELINE_PREPARATION_DIGEST",
    "EXPERIMENT_BASELINE_PRODUCTION_AUTHORITY_VERSION",
    "EXPERIMENT_BASELINE_PRODUCTION_COMPONENT_SET_DIGEST",
    "EXPERIMENT_BASELINE_QUALIFICATION_STATUS",
    "EXPERIMENT_BASELINE_RUNNER_VERSION",
    "EXPERIMENT_BASELINE_SCHEMA_VERSION",
    "EXPERIMENT_BASELINE_SEED",
    "EXPERIMENT_BASELINE_SMOKE_STATUS",
    "EXPERIMENT_FOUNDATION_ELIGIBLE_DENOMINATORS",
    "EXPERIMENT_FOUNDATION_LOGICAL_COUNTS",
    "EXPERIMENT_FOUNDATION_RECIPE_HASH",
    "EXPERIMENT_FOUNDATION_RELEASE_ID",
    "EXPERIMENT_FOUNDATION_SLICE_COUNTS",
    "EXPERIMENT_FOUNDATION_TASK_ID",
    "EXPERIMENT_HARNESS_GATE_TASK_ID",
    "EXPERIMENT_HARNESS_GATE_DECISION_DIGEST",
    "ExperimentBaselineArtifactContract",
    "ExperimentBaselineArtifactError",
    "ExperimentBaselineArtifactManifest",
    "ExperimentBaselineAuthorityError",
    "ExperimentBaselineCandidate",
    "ExperimentBaselineComponentIdentity",
    "ExperimentBaselineCurrentProductionAuthority",
    "ExperimentBaselineError",
    "ExperimentBaselineGateError",
    "ExperimentBaselineNotAuthorized",
    "ExperimentBaselinePathAdmission",
    "ExperimentBaselinePathError",
    "ExperimentBaselinePrediction",
    "ExperimentBaselinePreparation",
    "ExperimentBaselineProductionAuthority",
    "ExperimentBaselineResult",
    "ExperimentBaselineRunPathAdmission",
    "ExperimentBaselineRunRequest",
    "ExperimentBaselineSmokeResult",
    "ExperimentBaselineVerification",
    "ExperimentComparisonRecord",
    "ExperimentFoundationAuthority",
    "ExperimentFoundationGateDecision",
    "ExperimentHarnessGateDecision",
    "ExperimentSmokeMetric",
    "admit_experiment_baseline_artifact_layout",
    "admit_experiment_baseline_paths",
    "admit_experiment_baseline_run_paths",
    "authorize_experiment_baseline_v1",
    "fixed_experiment_baseline_current_production_authority_v2",
    "fixed_experiment_baseline_production_authority_v1",
    "load_experiment_foundation_gate_v1",
    "load_experiment_harness_gate_v1",
    "parse_experiment_foundation_gate_v1",
    "parse_experiment_harness_gate_v1",
    "prepare_experiment_baseline_v1",
    "run_experiment_baseline_smoke_v1",
    "run_experiment_baseline_v1",
    "verify_experiment_baseline_artifact_v1",
    "verify_experiment_baseline_production_authority_v1",
    "verify_experiment_foundation_authority_v1",
]
