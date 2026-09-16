"""Production-flat X-B0 baseline harness contracts for the Codex source.

The harness is deliberately isolated from the application runtime.  A caller
must provide a new SQLite path, a new materialized-raw path, and a new output
directory beneath one explicit system-temporary root.  The released execution
path is authorization gated; the test path can only emit a visibly non-qualified
smoke artifact.

Retrieval is never reimplemented here.  Execution uses the public production
``CodexSessionAdapter``, ``CodexIngestionService``, publication store method,
``LocalHashEmbedding``, and exact ``CodexHybridRetriever``.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib
import inspect
import json
import math
import re
import shutil
import stat
import sys
import tempfile
import time
import types
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
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
    field_validator,
    model_validator,
)

from evidence_rag.code_identity_v1 import (
    CodeIdentityError,
    portable_code_payload,
    portable_constant_payload,
    portable_function_payload,
    portable_stdlib_identity,
)
from evidence_rag.codex_adapter import CodexSessionAdapter
from evidence_rag.codex_ingestion import CodexIngestionService
from evidence_rag.codex_retrieval import CodexHybridRetriever
from evidence_rag.codex_store import CodexStoreMixin
from evidence_rag.config import Settings
from evidence_rag.embeddings import LocalHashEmbedding
from evidence_rag.models import CodexIngestRequest, CodexSearchRequest, CodexSearchScope
from evidence_rag.sources.service import RawSourceService
from evidence_rag.sources.store import RawSourceStore
from evidence_rag.storage import SQLiteStore

from .evaluation_v1 import (
    CodexGoldenCase,
    CodexGoldenSlice,
    CodexMetric,
    GoldenDataset,
    MetricResult,
    ReviewedRetrievalRow,
    evaluate_reviewed_codex_retrieval,
    load_codex_golden_v1,
    materialize_codex_session_fixture,
)

CODEX_BASELINE_SCHEMA_VERSION = "codex-x-b0-flat-baseline-v1"
CODEX_BASELINE_RUNNER_VERSION = "codex-x-b0-runner-v1"
CODEX_BASELINE_ARTIFACT_VERSION = "codex-x-b0-portable-artifact-v1"
CODEX_BASELINE_VERIFICATION_VERSION = "codex-x-b0-verify-only-v1"
CODEX_BASELINE_PROJECT_ID = "project-codex-x-b0-v1"
CODEX_BASELINE_ACL_REF = "project:project-codex-x-b0-v1"
CODEX_BASELINE_TOP_K = 10
CODEX_BASELINE_THREAD_TOP_K = 5
CODEX_BASELINE_EMBEDDING_DIMENSIONS = 384
CODEX_BASELINE_RELEASE_AUTHORIZATION = "X-B0_RELEASED_RUN_AUTHORIZED"
CODEX_BASELINE_SMOKE_AUTHORIZATION = "SMOKE_ONLY"
CODEX_HISTORICAL_PRODUCTION_AUTHORITY_VERSION = "codex-x-b0-production-authority-v1"
CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST = (
    "sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75"
)
CODEX_PRODUCTION_AUTHORITY_VERSION = "codex-x-b0-production-authority-v5"
CODEX_PRODUCTION_AUTHORITY_DIGEST = (
    "sha256:0b8a57c8668de7a575a7d09a84a0c376e6f01aa4749863db74508cd6aa440b41"
)
CODEX_XB0_GOLDEN_PACKAGE_HASH = (
    "sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1"
)
CODEX_XB0_GOLDEN_AUTHORITY_DIGEST = (
    "sha256:b5fc258c94375480b8d76cdc2e5dda6fd86ca49ddc2a0048b5879da2b23d1fe5"
)
CODEX_XB0_GOLDEN_DATASET_DIGEST = (
    "sha256:3e6645ef64daff7d82096e376b84755138480a5640cc751fbc7148ada67b046e"
)
CODEX_XB0_GOLDEN_DATASET_ID = "codex-golden-v1"
CODEX_XB0_GOLDEN_DATASET_VERSION = "v1"
CODEX_XB0_GOLDEN_SCHEMA_VERSION = "codex-evaluation-foundation-v1"
CODEX_XB0_CASE_COUNT = 45
CODEX_XB0_CASE_MEMBERSHIP = tuple(
    f"codex-v1-{ordinal:03d}" for ordinal in range(1, CODEX_XB0_CASE_COUNT + 1)
)
CODEX_XB0_SOURCE_RUN_DIRECTORY_ID = "6971b8cb1702a9ece8a894915c6b6de7"
CODEX_XB0_SOURCE_RUN_ID = f"codex-xb0-{CODEX_XB0_SOURCE_RUN_DIRECTORY_ID}"
CODEX_XB0_SOURCE_RUN_URI = (
    "evaluation-run://project-codex-xb0-v1/" + CODEX_XB0_SOURCE_RUN_DIRECTORY_ID
)
CODEX_XB0_SOURCE_ARTIFACT_SET_HASH = (
    "sha256:85a52db4afbe4f06630600982212767d9365cbff466c7c26acf15967a988d2f6"
)
CODEX_XB0_SOURCE_RUNNER_DIGEST = (
    "sha256:06a90145a062ed556dfcbeb704dc1fdc873b2d1a25fa2b97759af6a79a334d29"
)
CODEX_CORRECTION_SCHEMA_VERSION = "codex-x-b0-offline-correction-v1"
CODEX_CORRECTION_ARTIFACT_VERSION = "codex-x-b0-portable-correction-v1"
CODEX_CORRECTION_EVALUATOR_VERSION = "codex-x-b0-complete-truth-v1"
CODEX_CORRECTION_VERIFICATION_VERSION = "codex-x-b0-correction-verify-only-v1"
CODEX_CORRECTION_AUTHORIZATION = "OFFLINE_CORRECTION_PREPARATION_ONLY"
CODEX_CORRECTION_PUBLICATION_AUTHORIZATION = "PERSISTENT_CORRECTION_ARTIFACT_AUTHORIZED"
CODEX_CORRECTION_URI_PREFIX = "evaluation-correction://project-codex-xb0-v1/"
CODEX_CORRECTION_EVALUATOR_DIGEST = (
    "sha256:f48cb5ee7a11f4455a3b0952ba269dd3e5afd367c32ecb5915b4d7a13344dbe4"
)
CODEX_CORRECTION_RUNTIME_AUTHORITY_VERSION = "codex-x-b0-complete-truth-runtime-v2"
CODEX_CORRECTION_RUNTIME_AUTHORITY_DIGEST = (
    "sha256:5fd415d75a06f66451352150e127385049b7b8ba4078dcc7c6790b5c35a549fd"
)

CANONICAL_ARTIFACT_FILES = (
    "run.json",
    "manifest.json",
    "golden_cases.jsonl",
    "predictions.jsonl",
    "metrics.json",
    "slice_report.json",
    "errors.jsonl",
    "latency.json",
    "security_report.json",
    "checksums.json",
)
CHECKSUM_TARGET_FILES = CANONICAL_ARTIFACT_FILES[:-1]
_SECURITY_INPUT_FILES = CANONICAL_ARTIFACT_FILES[:8]
CORRECTION_CANONICAL_ARTIFACT_FILES = (
    "correction.json",
    "manifest.json",
    "golden_cases.jsonl",
    "predictions.jsonl",
    "corrected_metrics.json",
    "corrected_slice_report.json",
    "hard_negative_report.json",
    "refusal_report.json",
    "security_report.json",
    "checksums.json",
)
_CORRECTION_CHECKSUM_TARGET_FILES = CORRECTION_CANONICAL_ARTIFACT_FILES[:-1]
_CORRECTION_SECURITY_INPUT_FILES = CORRECTION_CANONICAL_ARTIFACT_FILES[:8]
_CORRECTION_NEW_METRICS = (
    "hard_negative_hit_rate_at_10",
    "correct_zero_rate",
    "refusal_failure_rate",
)
_CORRECTION_METRIC_NAMES = tuple(metric.value for metric in CodexMetric) + (_CORRECTION_NEW_METRICS)

_FIXED_ITEM_TYPES = (
    "UserGoal",
    "AgentMessage",
    "CommandExecution",
    "ToolResult",
    "ToolCall",
    "Patch",
    "FileChange",
    "Plan",
    "ValidationResult",
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s\"'=:(])(?:/(?!/)[A-Za-z0-9._~+-]+(?:/[A-Za-z0-9._~+,-]+)*|"
    r"[A-Za-z]:[\\/])"
)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_PAYMENT_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_CREDENTIAL_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s\"']{8,}))"
)
_RUN_ID_RE = re.compile(r"^codex-xb0-[0-9a-f]{32}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_DECLARED_SHA256_FIELDS = frozenset(
    {
        "artifact_set_hash",
        "authority_hash",
        "authority_sha256",
        "builtins_sha256",
        "code_sha256",
        "component_set_hash",
        "dataset_authority_digest",
        "detail_sha256",
        "golden_authority_digest",
        "globals_sha256",
        "helpers_sha256",
        "implementation_digest",
        "package_hash",
        "production_authority_digest",
        "sha256",
        "source_artifact_set_hash",
        "source_sha256",
    }
)
_FORBIDDEN_ARTIFACT_TEXT_RE = re.compile(
    r"(?:evidence-rag\.sqlite3|\.sqlite3-(?:wal|shm)|(?:^|[/\\])__pycache__(?:[/\\]|$)|"
    r"\.pyc(?:\W|$))",
    re.IGNORECASE,
)
_PROJECT_ROOT_TOKEN = "${PROJECT_ROOT}"

_FROZEN_HISTORICAL_PRODUCTION_AUTHORITY_ROWS: tuple[dict[str, str], ...] = (
    {
        "role": "adapter-class",
        "kind": "class",
        "module": "evidence_rag.codex_adapter",
        "export": "CodexSessionAdapter",
        "qualname": "CodexSessionAdapter",
        "version": "codex-jsonl-v2-clean-text",
        "source_sha256": (
            "sha256:1da1bd1bdd6d7dc0a8a66b0dfe6e7125ab74b7cf5a64fe302596bda8811570ad"
        ),
        "code_sha256": ("sha256:89100b7de7187af669a9c5ccfb9a20ad4414cd4f0f8a880ed4711c462c1fbd3f"),
    },
    {
        "role": "adapter-parse",
        "kind": "method",
        "module": "evidence_rag.codex_adapter",
        "export": "CodexSessionAdapter.parse",
        "qualname": "CodexSessionAdapter.parse",
        "version": "codex-jsonl-v2-clean-text",
        "source_sha256": (
            "sha256:b45c488e61b3b43650ebc7ec8709bdda141cd7d4c8e055a3fb3fb93d3381be2e"
        ),
        "code_sha256": ("sha256:e02487616bf94d87ef6a40668e2832d47bdd8ed987c25328b28801adad090eb9"),
    },
    {
        "role": "ingestion-class",
        "kind": "class",
        "module": "evidence_rag.codex_ingestion",
        "export": "CodexIngestionService",
        "qualname": "CodexIngestionService",
        "version": "codex-ingestion-production-v1",
        "source_sha256": (
            "sha256:32c28b27ce337ef32f3908d2997fc675eecc972b2e0383c50ff4d031ca838351"
        ),
        "code_sha256": ("sha256:a0ca33b914dcf5ea296e2f1fdf954355356280125f36c885d4fb9105bb193397"),
    },
    {
        "role": "ingestion-run",
        "kind": "method",
        "module": "evidence_rag.codex_ingestion",
        "export": "CodexIngestionService.run",
        "qualname": "CodexIngestionService.run",
        "version": "codex-ingestion-production-v1",
        "source_sha256": (
            "sha256:9e594f8ae8b56784bbd5ccdd32aed5350c09f19391ec865493053ad39589fec0"
        ),
        "code_sha256": ("sha256:7970632286c6152be1b77b24c179c8e6ed0a58ac1c311b1c9351ab60e21cd2fb"),
    },
    {
        "role": "publication-class",
        "kind": "class",
        "module": "evidence_rag.codex_store",
        "export": "CodexStoreMixin",
        "qualname": "CodexStoreMixin",
        "version": "codex-publication-production-v1",
        "source_sha256": (
            "sha256:223344af85093577f01b99de38fc406127f69658e487a8d647f94258a7cd9b32"
        ),
        "code_sha256": ("sha256:45d442879d6534c0ae131437ddc020ababeb718e026e735c289726220e04ecd8"),
    },
    {
        "role": "publication-method",
        "kind": "method",
        "module": "evidence_rag.codex_store",
        "export": "CodexStoreMixin.publish_codex_generation",
        "qualname": "CodexStoreMixin.publish_codex_generation",
        "version": "codex-publication-production-v1",
        "source_sha256": (
            "sha256:b7f6accbc34251158283b5195e2639cfa4ad11ec04fb02839c96c46318f862a4"
        ),
        "code_sha256": ("sha256:34ee100322ec2e2b3ceba149c1771ee68cbc8aa2722be25f9f23ec0b9a367dcf"),
    },
    {
        "role": "embedding-class",
        "kind": "class",
        "module": "evidence_rag.embeddings",
        "export": "LocalHashEmbedding",
        "qualname": "LocalHashEmbedding",
        "version": "local-hash-v2",
        "source_sha256": (
            "sha256:5f95707639474903c92f5288c4ac15aae1ea0d6c9ba20a4d0f9d603d804ccbd8"
        ),
        "code_sha256": ("sha256:e8fc0dc010e4c3419b47c3bd3b5e0e7ea5c2fd26a18b4747e753ec00db4639b1"),
    },
    {
        "role": "embedding-embed",
        "kind": "method",
        "module": "evidence_rag.embeddings",
        "export": "LocalHashEmbedding.embed",
        "qualname": "LocalHashEmbedding.embed",
        "version": "local-hash-v2",
        "source_sha256": (
            "sha256:101429dd05d63588ced11f5320d8a764bc2c0bf1e4997c958d9f66f75066b820"
        ),
        "code_sha256": ("sha256:96dc32594b5bcc3b53c216ba4285e94d3ec552581045b734beddecf54aeeef9d"),
    },
    {
        "role": "retriever-class",
        "kind": "class",
        "module": "evidence_rag.codex_retrieval",
        "export": "CodexHybridRetriever",
        "qualname": "CodexHybridRetriever",
        "version": "codex-weighted-hybrid-v2",
        "source_sha256": (
            "sha256:8a55d00928cbdebed34d0f2a93efbeee6166a8945f31d7804a4d752a487036d6"
        ),
        "code_sha256": ("sha256:e670b58d2e6e7f909e32ab8ce8cd4459f4294934c149684f638e81dcbdfdd4ea"),
    },
    {
        "role": "retriever-search",
        "kind": "method",
        "module": "evidence_rag.codex_retrieval",
        "export": "CodexHybridRetriever.search",
        "qualname": "CodexHybridRetriever.search",
        "version": "codex-weighted-hybrid-v2",
        "source_sha256": (
            "sha256:f0c288bba3603a4d6671865977b0abde7cc00accce64a4d47fc50adf869947cf"
        ),
        "code_sha256": ("sha256:a6f65ca35d16a2664f32dbf0c4b3de71b22a052277f69fbaffa544494b32b91c"),
    },
    {
        "role": "store-class",
        "kind": "class",
        "module": "evidence_rag.storage",
        "export": "SQLiteStore",
        "qualname": "SQLiteStore",
        "version": "sqlite-production-store-v1",
        "source_sha256": (
            "sha256:d6ce442643d5b579e291e7e13799b1f817ce14ac4410a6e124e9778bb155dd3f"
        ),
        "code_sha256": ("sha256:a2270b2c3b84f5bcdc59dd486f26e574bf5aa78fa9e79ec30583cd72ddefa514"),
    },
    {
        "role": "store-connect",
        "kind": "method",
        "module": "evidence_rag.storage",
        "export": "SQLiteStore.connect",
        "qualname": "SQLiteStore.connect",
        "version": "sqlite-production-store-v1",
        "source_sha256": (
            "sha256:d519f344cd8c73ea82a1e6f56b7c50d5b3f17dc6c17d71b21d4457362bf88149"
        ),
        "code_sha256": ("sha256:badf9182c9dac3e70bc03916d9620736678f026b30594a945f6ec76f047a5a94"),
    },
    {
        "role": "evaluator",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.evaluation_v1",
        "export": "evaluate_reviewed_codex_retrieval",
        "qualname": "evaluate_reviewed_codex_retrieval",
        "version": "codex-evaluation-foundation-v1",
        "source_sha256": (
            "sha256:e0170695894e3f9874a25f1adeabd78f0d9896194e28086006a066b680648d21"
        ),
        "code_sha256": ("sha256:37b5a7b429110d5eaf8b708c83a242903fbc1dbf0179dda13e05afffd7bfa42e"),
    },
)

# Gate-reviewed current v2 rows.  These are intentionally independent of the
# frozen v1 tuple so current runtime changes cannot re-sign historical X-B0
# artifacts.
_CURRENT_PRODUCTION_AUTHORITY_ROWS: tuple[dict[str, str], ...] = (
    {
        "role": "adapter-class",
        "kind": "class",
        "module": "evidence_rag.codex_adapter",
        "export": "CodexSessionAdapter",
        "qualname": "CodexSessionAdapter",
        "version": "codex-jsonl-v2-clean-text",
        "source_sha256": "sha256:1da1bd1bdd6d7dc0a8a66b0dfe6e7125ab74b7cf5a64fe302596bda8811570ad",
        "code_sha256": "sha256:3c3005b380d8fa5d00bcadd1d58dfcbcd14a25fc744537a49cd413d52988ca4e",
        "globals_sha256": "sha256:df560008093bafda79a0cde6f4d68d0837a3134e73b0fcf90d0216084f1e01e3",
        "builtins_sha256": "sha256:01aedede6351ab79027da0cc447375f83b78f8e1c19f5ebd5e99f043c6c63304",
        "helpers_sha256": "sha256:093e695a5b071d3b0c32c2557176ea9f59b64389f7e4a7598bf8cacfdbdd1dae",
    },
    {
        "role": "adapter-parse",
        "kind": "method",
        "module": "evidence_rag.codex_adapter",
        "export": "CodexSessionAdapter.parse",
        "qualname": "CodexSessionAdapter.parse",
        "version": "codex-jsonl-v2-clean-text",
        "source_sha256": "sha256:b45c488e61b3b43650ebc7ec8709bdda141cd7d4c8e055a3fb3fb93d3381be2e",
        "code_sha256": "sha256:c7108b7e1317c0dc94da03957b6853e64540997d093d4ddd531edb81822ad824",
        "globals_sha256": "sha256:986fda0a251540620d57151328a347786363c90e8a18e5b7146c0d8058e2f3c0",
        "builtins_sha256": "sha256:c5e1b43c8f0e55b52e1d6bf2461d124e080ff921714044e069f2c0c5b5c5c9b6",
        "helpers_sha256": "sha256:0c2bf3d94477e9c5e71ef17f3c2357680b36f541b62b74eb6236d10c250c1a0e",
    },
    {
        "role": "ingestion-class",
        "kind": "class",
        "module": "evidence_rag.codex_ingestion",
        "export": "CodexIngestionService",
        "qualname": "CodexIngestionService",
        "version": "codex-ingestion-production-v1",
        "source_sha256": "sha256:e9a65a252936d178185f144936f7de4eb3d9e703b630894e68a065335ee71215",
        "code_sha256": "sha256:5133fdba80f795a7b88e036a6410256036b754bcfce08dc2d8bb2b047d8a75fe",
        "globals_sha256": "sha256:0f25d160e7f4fe3bdbc22dd6c5624b7ac104f05fa36d176bd39a3803f7bee1fd",
        "builtins_sha256": "sha256:82554f7bf13fe97027663a35ffa17d363104b0155057de532164bd8bf4b2e117",
        "helpers_sha256": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    {
        "role": "ingestion-run",
        "kind": "method",
        "module": "evidence_rag.codex_ingestion",
        "export": "CodexIngestionService.run",
        "qualname": "CodexIngestionService.run",
        "version": "codex-ingestion-production-v1",
        "source_sha256": "sha256:9e594f8ae8b56784bbd5ccdd32aed5350c09f19391ec865493053ad39589fec0",
        "code_sha256": "sha256:e55b080b7cbff238c27268b63e05e0bded9ac8154a310671f2da584c09fc4f09",
        "globals_sha256": "sha256:cf2774d5f544f463d65cc1da583b935c890991fc53ff3901aab5d848b2adfa96",
        "builtins_sha256": "sha256:6dfbfc4f290932f523cb514d700cb15ce4e99b357ab55e1e8589e5c479b602cc",
        "helpers_sha256": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    {
        "role": "publication-class",
        "kind": "class",
        "module": "evidence_rag.codex_store",
        "export": "CodexStoreMixin",
        "qualname": "CodexStoreMixin",
        "version": "codex-publication-production-v1",
        "source_sha256": "sha256:b034f82c0bb4cf863d3b0d6e649073749b8d3176e0dcf5a86c20966978db20b8",
        "code_sha256": "sha256:789703a904a6344768687c1169611ca4176060efc41cfbbd5794942cf6ebc601",
        "globals_sha256": "sha256:da629b5126ed22570ec13e98fa7b6d23cba423cba37245b42a078c3671f09eca",
        "builtins_sha256": "sha256:7a463a2e4b96bc5189fa835a8b574452b522ca52d0a286c7310edafbea2e6504",
        "helpers_sha256": "sha256:a9f61785ca15d0906669d6cf26d6122c30435efe57acb19244495378b9192f87",
    },
    {
        "role": "publication-method",
        "kind": "method",
        "module": "evidence_rag.codex_store",
        "export": "CodexStoreMixin.publish_codex_generation",
        "qualname": "CodexStoreMixin.publish_codex_generation",
        "version": "codex-publication-production-v1",
        "source_sha256": "sha256:b7f6accbc34251158283b5195e2639cfa4ad11ec04fb02839c96c46318f862a4",
        "code_sha256": "sha256:7abbb5c480b9bfcc8bd6d990d0f5b2e92c57f9b0d4eb70c06c7935e1764a3aa3",
        "globals_sha256": "sha256:fe4b60ccc4436839fb7dff2c4d763a7842c8106b97eea8425e6c3397e3972a7b",
        "builtins_sha256": "sha256:941fa00d6c75d939a8e019f122dd9b048ff77ab478e2d5f1e83de063b4362727",
        "helpers_sha256": "sha256:83b6a2fe3b2310df78cf2c4aaa272765d40541f4be4ab8d1da2360cbafd54a92",
    },
    {
        "role": "embedding-class",
        "kind": "class",
        "module": "evidence_rag.embeddings",
        "export": "LocalHashEmbedding",
        "qualname": "LocalHashEmbedding",
        "version": "local-hash-v2",
        "source_sha256": "sha256:5f95707639474903c92f5288c4ac15aae1ea0d6c9ba20a4d0f9d603d804ccbd8",
        "code_sha256": "sha256:09fa815a990a4be75f5b184492390cdac93aa86008ae419f6de56589260ae3f7",
        "globals_sha256": "sha256:78897fb66cd5a17c4559e690c2030d80d4aed6eaa3f5930f659c01744a4557e7",
        "builtins_sha256": "sha256:42c218714379dfef265023e287411cdd6cddb3f66346a9a8a24b45760555a698",
        "helpers_sha256": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    {
        "role": "embedding-embed",
        "kind": "method",
        "module": "evidence_rag.embeddings",
        "export": "LocalHashEmbedding.embed",
        "qualname": "LocalHashEmbedding.embed",
        "version": "local-hash-v2",
        "source_sha256": "sha256:101429dd05d63588ced11f5320d8a764bc2c0bf1e4997c958d9f66f75066b820",
        "code_sha256": "sha256:067dff2f5203a0510855c7ec926e672bd8e8953ee432583af0b3d04c208b0060",
        "globals_sha256": "sha256:2059fcd6c373f20347deebf60dac083087b06dc0cbef686810f7a614c3e57456",
        "builtins_sha256": "sha256:aabaf0f58eb90d2c894cd40e2be05c7cfeaeec96f1dd29fcd2fe846eb980ce27",
        "helpers_sha256": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    {
        "role": "retriever-class",
        "kind": "class",
        "module": "evidence_rag.codex_retrieval",
        "export": "CodexHybridRetriever",
        "qualname": "CodexHybridRetriever",
        "version": "codex-weighted-hybrid-v2",
        "source_sha256": "sha256:8a55d00928cbdebed34d0f2a93efbeee6166a8945f31d7804a4d752a487036d6",
        "code_sha256": "sha256:010987d13a8be9d0da73299b04357752a708ee98c4c6f0e01a0f3e6538c72022",
        "globals_sha256": "sha256:f8de004085105b58f0fcd9a73c1d0b8d5021a46fff403abd280c0ee4d07a6281",
        "builtins_sha256": "sha256:288f3caf06e9d82862053ad040a98930213cd4f82e20f55db65bdf645c7cccb0",
        "helpers_sha256": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    {
        "role": "retriever-search",
        "kind": "method",
        "module": "evidence_rag.codex_retrieval",
        "export": "CodexHybridRetriever.search",
        "qualname": "CodexHybridRetriever.search",
        "version": "codex-weighted-hybrid-v2",
        "source_sha256": "sha256:f0c288bba3603a4d6671865977b0abde7cc00accce64a4d47fc50adf869947cf",
        "code_sha256": "sha256:1aeb1cbffef6ffce5cecdea6664d3a6785240f4d409d45f1732cabcb4cbda826",
        "globals_sha256": "sha256:6be4a4bdbe690514979063caef31b75c6f316c9b2efe6e2cf175c71d6a1d28c1",
        "builtins_sha256": "sha256:cf4598d341b825570a42b962da378491833aa8ea05cf85b1d4d5ef1516bae619",
        "helpers_sha256": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    {
        "role": "store-class",
        "kind": "class",
        "module": "evidence_rag.storage",
        "export": "SQLiteStore",
        "qualname": "SQLiteStore",
        "version": "sqlite-production-store-v2",
        "source_sha256": "sha256:08fdbbea8da0ae651b6662f1aa7c79489be786776e982862c67a92e0d62c8563",
        "code_sha256": "sha256:f7821bb639f23c41e20315a75606b3fbd828f27e78cca707dc699c5b88a34d1b",
        "globals_sha256": "sha256:66dd43c654f0b46b226e5f6eecaee0d94b04dd20ad9b12bb96a5e638493fec77",
        "builtins_sha256": "sha256:ff30bde3344a15a4c6a7e5f2a6c1bec5439347b554169c820e6976849bcc5165",
        "helpers_sha256": "sha256:a1321535cbceec3f13f08b80cbf202a6cf9bc38c49accf91ba1cc2fdafabdb32",
    },
    {
        "role": "store-connect",
        "kind": "method",
        "module": "evidence_rag.storage",
        "export": "SQLiteStore.connect",
        "qualname": "SQLiteStore.connect",
        "version": "sqlite-production-store-v2",
        "source_sha256": "sha256:52f002d1225a7da95409fee9cadbc44df433df6c8fb7ab47964cc811cb2eeedb",
        "code_sha256": "sha256:aec7b3e9873745d6da025efa63d1b978b05662428fa1aef9ce0e4d4029e437a6",
        "globals_sha256": "sha256:8fffb6770279c4b5dbb06c95dfd090c9f0edb19340322a5cd47649c88ac50c00",
        "builtins_sha256": "sha256:9f15318b16bcd4bfaa3545d8ba865fb29983aa1c021a0647343679ec20cbe38a",
        "helpers_sha256": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    },
    {
        "role": "evaluator",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.evaluation_v1",
        "export": "evaluate_reviewed_codex_retrieval",
        "qualname": "evaluate_reviewed_codex_retrieval",
        "version": "codex-evaluation-foundation-v1",
        "source_sha256": "sha256:e0170695894e3f9874a25f1adeabd78f0d9896194e28086006a066b680648d21",
        "code_sha256": "sha256:250c42931cefb117e18010561e1a574b3704b965b5ab4e082e6e94fccc7e4007",
        "globals_sha256": "sha256:a05b98d60ea5d292a74dfb6c1d5f1c518df91e6a76bf6df6e2c00ef032c9ee00",
        "builtins_sha256": "sha256:00d98cbf4714673549defe803f6827cd96ea0f826dffe68b5daf854c360f886b",
        "helpers_sha256": "sha256:ee5162070af6b02317e4f18fa19be586644f95d18657757f531f8b940b87821b",
    },
)

_FIXED_SOURCE_FILE_RECORDS: dict[str, dict[str, object]] = {
    "checksums.json": {
        "sha256": "sha256:c75bde8affd713cf9ab683c05f470e66916d393e07568961fcac0e4ad1cabd9a",
        "size": 1259,
    },
    "errors.jsonl": {
        "sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "size": 0,
    },
    "golden_cases.jsonl": {
        "sha256": "sha256:0ffd297b7f4c403d607756f817307f81746ad8454cce71d5005d7d1f18fdb57c",
        "size": 194426,
    },
    "latency.json": {
        "sha256": "sha256:bf50de2b852c0a688c707148fec9ac1e9f12d750ffa95da119d08b5ae41bac9a",
        "size": 8010,
    },
    "manifest.json": {
        "sha256": "sha256:ddbbd6b673530b23228411c0e4e2dc1211ce88a07f21fed8e0eb3e90da56cd58",
        "size": 18639,
    },
    "metrics.json": {
        "sha256": "sha256:4b10afec22bb92c98f8ce2c073c49baa691232519b06aafa271eff2db3a82a6c",
        "size": 2909,
    },
    "predictions.jsonl": {
        "sha256": "sha256:1a56ab42a8d646fee31fef9748fa5f805b479a3b3c91992bd489dd88eba3b16d",
        "size": 218173,
    },
    "run.json": {
        "sha256": "sha256:5f3c6863c74d2c8a5d2c4865a7c2acdce1460748393d566b010d02a7dc9ccf47",
        "size": 2506,
    },
    "security_report.json": {
        "sha256": "sha256:67c2ea186c51517a6482f8650c1f9179370432ed8febafcad716b192727b32b6",
        "size": 529,
    },
    "slice_report.json": {
        "sha256": "sha256:05aac74a5c05e883931478dcd54d55c50041e8e65d8ec4bb73d37170d7c6e1cf",
        "size": 17628,
    },
}


def _canonical_json(value: object) -> bytes:
    _assert_finite(value)
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _assert_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numeric values are forbidden")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(key)
            _assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite(item)


def _validated_text(value: str) -> str:
    if (
        not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or _CONTROL_RE.search(value)
    ):
        raise ValueError("text must be non-empty, NFC, trimmed, and free of controls")
    return value


def _validated_identifier(value: str) -> str:
    value = _validated_text(value)
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier is not canonical")
    return value


def _validated_sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("expected sha256:<lowercase-hex>")
    return value


Identifier = Annotated[StrictStr, AfterValidator(_validated_identifier)]
ContractText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=8_000),
    AfterValidator(_validated_text),
]
Sha256 = Annotated[StrictStr, AfterValidator(_validated_sha256)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
FiniteNonNegativeFloat = Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]


class CodexBaselineError(ValueError):
    """Fail-closed request, execution, artifact, or verification failure."""


class CodexBaselineUnavailableError(CodexBaselineError):
    """A production query could not produce a contract-compatible response."""


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

    def copy(self, **_: Any) -> Self:
        raise TypeError("deprecated copy() is disabled; use validated model_copy()")


class CodexBaselineConfig(_FrozenContract):
    """Frozen X-B0 production-flat search configuration."""

    schema_version: Literal["codex-x-b0-flat-baseline-v1"] = CODEX_BASELINE_SCHEMA_VERSION
    view_granularity: Literal["item-per-view"] = "item-per-view"
    embedding_model: Literal["local-hash-v2"] = "local-hash-v2"
    embedding_dimensions: Literal[384] = CODEX_BASELINE_EMBEDDING_DIMENSIONS
    lexical_channel: Literal["fts5"] = "fts5"
    dense_channel: Literal["dense-full-candidate"] = "dense-full-candidate"
    ranked_candidate_multiplier: Literal[8] = 8
    fusion: Literal["codex-weighted-hybrid-v2"] = "codex-weighted-hybrid-v2"
    episode_retrieval: Literal[False] = False
    event_graph: Literal[False] = False
    reranker: Literal[False] = False
    context_builder: Literal["snippet-v1"] = "snippet-v1"
    top_k: Literal[10] = CODEX_BASELINE_TOP_K
    thread_top_k: Literal[5] = CODEX_BASELINE_THREAD_TOP_K
    item_types: tuple[
        Literal[
            "UserGoal",
            "AgentMessage",
            "CommandExecution",
            "ToolResult",
            "ToolCall",
            "Patch",
            "FileChange",
            "Plan",
            "ValidationResult",
        ],
        ...,
    ] = _FIXED_ITEM_TYPES

    @model_validator(mode="after")
    def _exact_item_membership(self) -> Self:
        if self.item_types != _FIXED_ITEM_TYPES:
            raise ValueError("X-B0 item type membership is fixed and ordered")
        return self


class CodexBaselineCase(_FrozenContract):
    """Minimal query/denominator contract used by released and smoke execution."""

    case_id: Identifier
    slice: CodexGoldenSlice
    query: ContractText
    eligible_metrics: tuple[CodexMetric, ...] = ()

    @field_validator("eligible_metrics")
    @classmethod
    def _unique_metrics(cls, value: tuple[CodexMetric, ...]) -> tuple[CodexMetric, ...]:
        if len(value) != len(set(value)):
            raise ValueError("eligible_metrics cannot contain duplicates")
        return value


class CodexBaselineRequest(_FrozenContract):
    """All execution paths are explicit; no environment/default path is accepted."""

    schema_version: Literal["codex-x-b0-flat-baseline-v1"] = CODEX_BASELINE_SCHEMA_VERSION
    execution_mode: Literal["released", "smoke"]
    authorization: Literal["X-B0_RELEASED_RUN_AUTHORIZED", "SMOKE_ONLY"]
    golden_path: Path
    fixture_path: Path
    isolated_root: Path
    database_path: Path
    raw_root: Path
    output_dir: Path
    seed: Annotated[StrictInt, Field(ge=0, le=9_223_372_036_854_775_807)]
    config: CodexBaselineConfig = Field(default_factory=CodexBaselineConfig)

    @model_validator(mode="after")
    def _explicit_paths_and_authorization(self) -> Self:
        for name in (
            "golden_path",
            "fixture_path",
            "isolated_root",
            "database_path",
            "raw_root",
            "output_dir",
        ):
            if not getattr(self, name).is_absolute():
                raise ValueError(f"{name} must be explicit and absolute")
        expected = (
            CODEX_BASELINE_RELEASE_AUTHORIZATION
            if self.execution_mode == "released"
            else CODEX_BASELINE_SMOKE_AUTHORIZATION
        )
        if self.authorization != expected:
            raise ValueError("execution authorization does not match execution_mode")
        return self


class CodexComponentIdentity(_FrozenContract):
    role: Identifier
    kind: Literal["class", "method", "function", "module"]
    module: ContractText
    export: ContractText
    qualname: ContractText
    version: ContractText
    source_sha256: Sha256
    code_sha256: Sha256
    globals_sha256: Sha256 | None = None
    builtins_sha256: Sha256 | None = None
    helpers_sha256: Sha256 | None = None


class CodexRankedItem(_FrozenContract):
    rank: PositiveInt
    item_id: ContractText
    thread_id: ContractText
    locator: ContractText
    item_type: ContractText
    channels: tuple[Literal["dense", "lexical"], ...]
    score: FiniteNonNegativeFloat

    @model_validator(mode="after")
    def _production_locator_and_channels(self) -> Self:
        if not self.item_id.startswith("codex://thread/"):
            raise ValueError("ranked item identity must be a Codex locator")
        if not self.locator.startswith(self.item_id):
            raise ValueError("ranked item locator must be relative to its item identity")
        if tuple(sorted(set(self.channels))) != self.channels:
            raise ValueError("channels must be unique and sorted")
        if not self.channels:
            raise ValueError("ranked items require at least one production channel")
        return self


class CodexBaselineTrace(_FrozenContract):
    """Per-query production trace retained without snippets, cwd, or raw paths."""

    case_id: Identifier
    outcome: Literal["retrieved", "zero_result", "error", "unavailable"]
    latency_ms: FiniteNonNegativeFloat
    lexical_candidates: NonNegativeInt
    dense_candidates: NonNegativeInt
    dense_matches: NonNegativeInt
    result_count: NonNegativeInt
    reviewed_result_count: NonNegativeInt
    zero_result: StrictBool
    fallback_used: Literal[False] = False
    unavailable_reason: ContractText | None = None
    error_type: ContractText | None = None
    error_detail_sha256: Sha256 | None = None
    fusion: Literal["codex-weighted-hybrid-v2"] = "codex-weighted-hybrid-v2"
    embedding_model: Literal["local-hash-v2"] = "local-hash-v2"
    top_threads: tuple[ContractText, ...] = ()

    @model_validator(mode="after")
    def _honest_outcome(self) -> Self:
        if self.reviewed_result_count > self.result_count:
            raise ValueError("reviewed_result_count cannot exceed result_count")
        if len(self.top_threads) > CODEX_BASELINE_THREAD_TOP_K:
            raise ValueError("top_threads exceeds the fixed thread cutoff")
        if self.outcome == "retrieved":
            if (
                self.result_count <= 0
                or self.zero_result
                or self.error_type
                or self.error_detail_sha256
                or self.unavailable_reason
            ):
                raise ValueError("retrieved trace cannot report failure state")
        elif self.outcome == "zero_result":
            if (
                self.result_count != 0
                or not self.zero_result
                or self.error_type
                or self.error_detail_sha256
                or self.unavailable_reason
            ):
                raise ValueError("zero-result trace is inconsistent")
        elif self.outcome == "error":
            if (
                self.result_count != 0
                or self.zero_result
                or self.error_type is None
                or self.error_detail_sha256 is None
                or self.unavailable_reason is not None
            ):
                raise ValueError("error trace requires only error_type")
        elif (
            self.result_count != 0
            or self.zero_result
            or self.unavailable_reason is None
            or self.error_detail_sha256 is None
            or self.error_type is not None
        ):
            raise ValueError("unavailable trace requires only unavailable_reason")
        return self


class CodexPredictionRecord(_FrozenContract):
    schema_version: Literal["codex-x-b0-flat-baseline-v1"] = CODEX_BASELINE_SCHEMA_VERSION
    run_id: Identifier
    dataset_id: Identifier
    dataset_version: Identifier
    package_hash: Sha256
    case_id: Identifier
    query: ContractText
    eligible_metrics: tuple[CodexMetric, ...]
    denominator_contributions: dict[CodexMetric, NonNegativeInt]
    results: tuple[CodexRankedItem, ...]
    trace: CodexBaselineTrace

    @model_validator(mode="after")
    def _rank_and_case_trace(self) -> Self:
        if self.trace.case_id != self.case_id:
            raise ValueError("prediction and trace case identities differ")
        if [item.rank for item in self.results] != list(range(1, len(self.results) + 1)):
            raise ValueError("production ranks must be contiguous from one")
        if len(self.results) > CODEX_BASELINE_TOP_K:
            raise ValueError("prediction exceeds the fixed top-k")
        if self.trace.result_count != len(self.results):
            raise ValueError("trace result_count differs from prediction")
        expected_threads = tuple(dict.fromkeys(item.thread_id for item in self.results))[
            :CODEX_BASELINE_THREAD_TOP_K
        ]
        if self.trace.top_threads != expected_threads:
            raise ValueError("trace top_threads differs from production ranking")
        return self


class CodexBaselineErrorRecord(_FrozenContract):
    schema_version: Literal["codex-x-b0-flat-baseline-v1"] = CODEX_BASELINE_SCHEMA_VERSION
    run_id: Identifier
    case_id: Identifier
    outcome: Literal["error", "unavailable"]
    error_type: ContractText
    safe_summary: ContractText
    detail_sha256: Sha256
    fallback_used: Literal[False] = False


class CodexBaselineResult(_FrozenContract):
    schema_version: Literal["codex-x-b0-flat-baseline-v1"] = CODEX_BASELINE_SCHEMA_VERSION
    run_id: Identifier
    execution_mode: Literal["released", "smoke"]
    status: Literal["COMPLETED", "SMOKE_COMPLETED_NON_QUALIFIED"]
    release_posture: Literal["NON_QUALIFIED", "SMOKE_NON_QUALIFIED"]
    artifact_path: Path
    artifact_set_hash: Sha256
    case_count: PositiveInt
    prediction_count: PositiveInt
    error_count: NonNegativeInt
    zero_result_count: NonNegativeInt
    baseline_qualified: Literal[False] = False


class CodexBaselineVerification(_FrozenContract):
    schema_version: Literal["codex-x-b0-verify-only-v1"] = CODEX_BASELINE_VERIFICATION_VERSION
    run_id: Identifier
    status: Literal["VERIFIED_NON_QUALIFIED", "VERIFIED_SMOKE_NON_QUALIFIED"]
    execution_mode: Literal["released", "smoke"]
    artifact_set_hash: Sha256
    canonical_file_count: Literal[10] = len(CANONICAL_ARTIFACT_FILES)
    case_count: PositiveInt
    retrieval_executed: Literal[False] = False
    portable: Literal[True] = True
    baseline_qualified: Literal[False] = False


class CodexBaselineCorrectionRequest(_FrozenContract):
    schema_version: Literal["codex-x-b0-offline-correction-v1"] = CODEX_CORRECTION_SCHEMA_VERSION
    authorization: Literal["OFFLINE_CORRECTION_PREPARATION_ONLY"]
    source_run_dir: Path
    isolated_root: Path
    output_dir: Path

    @model_validator(mode="after")
    def _explicit_offline_paths(self) -> Self:
        if self.authorization != CODEX_CORRECTION_AUTHORIZATION:
            raise ValueError("correction authorization mismatch")
        for name in ("source_run_dir", "isolated_root", "output_dir"):
            if not getattr(self, name).is_absolute():
                raise ValueError(f"{name} must be explicit and absolute")
        return self


class CodexBaselineCorrectionPublicationRequest(_FrozenContract):
    schema_version: Literal["codex-x-b0-offline-correction-v1"] = CODEX_CORRECTION_SCHEMA_VERSION
    authorization: Literal["PERSISTENT_CORRECTION_ARTIFACT_AUTHORIZED"]
    source_run_dir: Path
    isolated_root: Path
    corrections_root: Path

    @model_validator(mode="after")
    def _explicit_offline_paths(self) -> Self:
        if self.authorization != CODEX_CORRECTION_PUBLICATION_AUTHORIZATION:
            raise ValueError("correction publication authorization mismatch")
        for name in ("source_run_dir", "isolated_root", "corrections_root"):
            if not getattr(self, name).is_absolute():
                raise ValueError(f"{name} must be explicit and absolute")
        return self


class CodexBaselineCorrectionResult(_FrozenContract):
    schema_version: Literal["codex-x-b0-offline-correction-v1"] = CODEX_CORRECTION_SCHEMA_VERSION
    correction_directory_id: Identifier
    correction_id: Identifier
    correction_uri: str
    status: Literal["CORRECTION_PREPARED"]
    release_posture: Literal["NON_QUALIFIED"] = "NON_QUALIFIED"
    source_run_id: Literal["codex-xb0-6971b8cb1702a9ece8a894915c6b6de7"] = CODEX_XB0_SOURCE_RUN_ID
    artifact_path: Path
    artifact_set_hash: Sha256
    case_count: Literal[45] = CODEX_XB0_CASE_COUNT
    retrieval_executed: Literal[False] = False
    persistent_artifact: Literal[False] = False
    baseline_qualified: Literal[False] = False


class CodexBaselineCorrectionPublicationResult(_FrozenContract):
    schema_version: Literal["codex-x-b0-offline-correction-v1"] = CODEX_CORRECTION_SCHEMA_VERSION
    correction_directory_id: Identifier
    correction_id: Identifier
    correction_uri: str
    status: Literal["CORRECTION_PUBLISHED"]
    release_posture: Literal["NON_QUALIFIED"] = "NON_QUALIFIED"
    source_run_id: Literal["codex-xb0-6971b8cb1702a9ece8a894915c6b6de7"] = CODEX_XB0_SOURCE_RUN_ID
    artifact_path: Path
    artifact_set_hash: Sha256
    case_count: Literal[45] = CODEX_XB0_CASE_COUNT
    retrieval_executed: Literal[False] = False
    persistent_artifact: Literal[True] = True
    baseline_qualified: Literal[False] = False


class CodexBaselineCorrectionVerification(_FrozenContract):
    schema_version: Literal["codex-x-b0-correction-verify-only-v1"] = (
        CODEX_CORRECTION_VERIFICATION_VERSION
    )
    correction_directory_id: Identifier
    correction_id: Identifier
    correction_uri: str
    status: Literal["VERIFIED_CORRECTION_NON_QUALIFIED"]
    source_run_id: Literal["codex-xb0-6971b8cb1702a9ece8a894915c6b6de7"] = CODEX_XB0_SOURCE_RUN_ID
    artifact_set_hash: Sha256
    canonical_file_count: Literal[10] = len(CORRECTION_CANONICAL_ARTIFACT_FILES)
    case_count: Literal[45] = CODEX_XB0_CASE_COUNT
    retrieval_executed: Literal[False] = False
    source_run_immutable: Literal[True] = True
    persistent_artifact: bool
    portable: Literal[True] = True
    baseline_qualified: Literal[False] = False


class CodexBaselinePreparation(_FrozenContract):
    schema_version: Literal["codex-x-b0-flat-baseline-v1"] = CODEX_BASELINE_SCHEMA_VERSION
    status: Literal["PREPARED"] = "PREPARED"
    release_posture: Literal["NON_QUALIFIED"] = "NON_QUALIFIED"
    config: CodexBaselineConfig = Field(default_factory=CodexBaselineConfig)
    components: tuple[CodexComponentIdentity, ...]
    component_set_hash: Sha256
    production_authority_version: Literal["codex-x-b0-production-authority-v5"] = (
        CODEX_PRODUCTION_AUTHORITY_VERSION
    )
    production_authority_digest: Literal[
        "sha256:0b8a57c8668de7a575a7d09a84a0c376e6f01aa4749863db74508cd6aa440b41"
    ] = CODEX_PRODUCTION_AUTHORITY_DIGEST
    runner_identity: CodexComponentIdentity
    released_run_executed: Literal[False] = False
    evaluation_run_created: Literal[False] = False
    baseline_metrics_run: Literal[False] = False
    baseline_qualified: Literal[False] = False

    @model_validator(mode="after")
    def _component_hash_matches(self) -> Self:
        if (
            self.components != _fixed_production_authority()
            or self.component_set_hash != CODEX_PRODUCTION_AUTHORITY_DIGEST
            or self.runner_identity != _runner_module_identity()
        ):
            raise ValueError("preparation component_set_hash mismatch")
        return self


def _source_digest(value: object) -> str:
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError) as exc:
        raise CodexBaselineError("production callable source is unavailable") from exc
    normalized = source.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return _sha256(normalized)


def _constant_code_payload(value: object) -> object:
    return portable_constant_payload(value)


def _code_object_payload(code: types.CodeType) -> dict[str, object]:
    return portable_code_payload(code)


def _function_code_digest(value: types.FunctionType) -> str:
    try:
        payload = portable_function_payload(value)
    except CodeIdentityError as exc:
        raise CodexBaselineError(str(exc)) from exc
    return _sha256(_canonical_json(payload))


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
            raw,
            (type(None), bool, int, float, str, tuple, frozenset),
        ):
            attributes[name] = _constant_code_payload(raw)
    payload = {
        "bases": [(base.__module__, base.__qualname__) for base in value.__bases__],
        "members": members,
        "attrs": attributes,
    }
    return _sha256(_canonical_json(payload))


def _runtime_code_digest(value: object, kind: str) -> str:
    if kind == "class" and isinstance(value, type):
        return _class_code_digest(value)
    if kind in {"method", "function"} and isinstance(value, types.FunctionType):
        return _function_code_digest(value)
    raise CodexBaselineError("production authority object kind mismatch")


def _authority_function_members(
    value: object,
    kind: str,
) -> tuple[tuple[str, types.FunctionType], ...]:
    if kind in {"method", "function"} and isinstance(value, types.FunctionType):
        return ((value.__qualname__, value),)
    if kind != "class" or not isinstance(value, type):
        raise CodexBaselineError("production authority object kind mismatch")
    module = sys.modules.get(value.__module__)
    namespace = vars(module) if module is not None else None
    members: list[tuple[str, types.FunctionType]] = []
    for name, member in sorted(value.__dict__.items()):
        raw = member
        if isinstance(raw, (staticmethod, classmethod)):
            raw = raw.__func__
        if (
            isinstance(raw, types.FunctionType)
            and raw.__module__ == value.__module__
            and raw.__globals__ is namespace
        ):
            members.append((name, raw))
        elif isinstance(raw, property):
            for accessor, function in (
                ("get", raw.fget),
                ("set", raw.fset),
                ("delete", raw.fdel),
            ):
                if (
                    function is not None
                    and function.__module__ == value.__module__
                    and function.__globals__ is namespace
                ):
                    members.append((f"{name}.{accessor}", function))
    return tuple(members)


def _declared_module_version(module: types.ModuleType) -> object:
    stdlib_identity = portable_stdlib_identity(module)
    if stdlib_identity is not None:
        return stdlib_identity
    for name in ("__version__", "VERSION", "version"):
        value = vars(module).get(name)
        if value is None or isinstance(value, (bool, int, float, str)):
            if value is not None:
                return {"attribute": name, "value": value}
        elif isinstance(value, tuple) and all(
            item is None or isinstance(item, (bool, int, float, str)) for item in value
        ):
            return {"attribute": name, "value": list(value)}
    return None


def _stable_external_source_digest(value: object) -> str | None:
    module_name = getattr(value, "__module__", None)
    module = (
        value
        if isinstance(value, types.ModuleType)
        else sys.modules.get(module_name)
        if isinstance(module_name, str)
        else None
    )
    if module is not None and portable_stdlib_identity(module) is not None:
        return None
    try:
        return _source_digest(value)
    except CodexBaselineError:
        return None


def _stable_external_identity(value: object) -> dict[str, object]:
    module_name = getattr(value, "__module__", None)
    module = sys.modules.get(module_name) if isinstance(module_name, str) else None
    qualname = getattr(value, "__qualname__", None)
    if module is not None and portable_stdlib_identity(module) is not None:
        root_name = module.__name__.split(".", 1)[0]
        root_module = importlib.import_module(root_name)
        public_names = sorted(
            name
            for name, candidate in vars(root_module).items()
            if not name.startswith("_") and candidate is value
        )
        if public_names:
            module_name = root_name
            qualname = public_names[0]
    return {
        "module": module_name,
        "qualname": qualname,
        "source_sha256": _stable_external_source_digest(value),
        "module_version": _declared_module_version(module) if module is not None else None,
    }


def _binding_value_payload(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return {"literal": value, "type": type(value).__name__}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, re.Pattern):
        return {"pattern": value.pattern, "flags": value.flags}
    if isinstance(value, Path):
        return {"path_type": type(value).__name__, "parts": list(value.parts)}
    if isinstance(value, types.ModuleType):
        return {
            "kind": "module",
            "module": value.__name__,
            "source_sha256": _stable_external_source_digest(value),
            "module_version": _declared_module_version(value),
        }
    if isinstance(value, Mapping):
        return {
            "mapping": [
                [_binding_value_payload(key), _binding_value_payload(item)]
                for key, item in sorted(value.items(), key=lambda pair: repr(pair[0]))
            ]
        }
    if isinstance(value, (tuple, list)):
        return {
            type(value).__name__: [_binding_value_payload(item) for item in value],
        }
    if isinstance(value, (set, frozenset)):
        items = [_binding_value_payload(item) for item in value]
        return {type(value).__name__: sorted(items, key=_canonical_json)}
    if isinstance(value, types.FunctionType):
        return {
            "kind": "function",
            **_stable_external_identity(value),
            "code_sha256": _function_code_digest(value),
        }
    if isinstance(value, type):
        return {
            "kind": "class",
            **_stable_external_identity(value),
        }
    if isinstance(
        value,
        (
            types.BuiltinFunctionType,
            types.BuiltinMethodType,
            types.MethodDescriptorType,
        ),
    ):
        return {
            "kind": type(value).__name__,
            **_stable_external_identity(value),
        }
    return {
        "kind": "object",
        "type_module": type(value).__module__,
        "type_qualname": type(value).__qualname__,
        "module": getattr(value, "__module__", None),
        "qualname": getattr(value, "__qualname__", None),
    }


def _is_namespace_helper(value: object, namespace: Mapping[str, object]) -> bool:
    if isinstance(value, types.FunctionType):
        return value.__globals__ is namespace
    if isinstance(value, type):
        return (
            value.__module__ == namespace.get("__name__") and namespace.get(value.__name__) is value
        )
    return False


def _require_canonical_function_namespace(
    function: types.FunctionType,
    namespace: Mapping[str, object],
) -> None:
    module = sys.modules.get(function.__module__)
    if (
        module is None
        or vars(module) is not namespace
        or function.__globals__ is not namespace
        or function.__builtins__ is not vars(builtins)
    ):
        raise CodexBaselineError("production callable namespace or builtins was replaced")


def _function_binding_rows(
    function: types.FunctionType,
    *,
    namespace: Mapping[str, object],
    root_ids: frozenset[int],
    helper_queue: list[object],
) -> tuple[dict[str, object], dict[str, object]]:
    _require_canonical_function_namespace(function, namespace)
    closure = inspect.getclosurevars(function)

    def bound_value(value: object) -> object:
        if id(value) in root_ids:
            return {
                "root": getattr(value, "__qualname__", type(value).__qualname__),
            }
        if _is_namespace_helper(value, namespace):
            helper_queue.append(value)
            return {
                "helper": getattr(value, "__qualname__", type(value).__qualname__),
                "kind": "class" if isinstance(value, type) else "function",
            }
        return _binding_value_payload(value)

    globals_row = {
        "function": function.__qualname__,
        "globals": [
            {"name": name, "binding": bound_value(value)}
            for name, value in sorted(closure.globals.items())
        ],
        "nonlocals": [
            {"name": name, "binding": bound_value(value)}
            for name, value in sorted(closure.nonlocals.items())
        ],
        "unbound": sorted(closure.unbound),
    }
    builtins_row = {
        "function": function.__qualname__,
        "builtins": [
            {"name": name, "binding": _binding_value_payload(value)}
            for name, value in sorted(closure.builtins.items())
        ],
    }
    return globals_row, builtins_row


def _runtime_binding_digests(value: object, kind: str) -> tuple[str, str, str]:
    root_members = _authority_function_members(value, kind)
    if not root_members:
        raise CodexBaselineError("production authority class has no callable members")
    namespace = root_members[0][1].__globals__
    root_ids = frozenset({id(value), *(id(function) for _, function in root_members)})
    helper_queue: list[object] = []
    globals_rows: list[dict[str, object]] = []
    builtins_rows: list[dict[str, object]] = []
    for member_name, function in root_members:
        globals_row, builtins_row = _function_binding_rows(
            function,
            namespace=namespace,
            root_ids=root_ids,
            helper_queue=helper_queue,
        )
        globals_rows.append({"member": member_name, **globals_row})
        builtins_rows.append({"member": member_name, **builtins_row})

    helper_rows: list[dict[str, object]] = []
    seen_helpers: set[int] = set(root_ids)
    while helper_queue:
        helper = helper_queue.pop(0)
        if id(helper) in seen_helpers:
            continue
        seen_helpers.add(id(helper))
        helper_kind = "class" if isinstance(helper, type) else "function"
        helper_globals: list[dict[str, object]] = []
        helper_builtins: list[dict[str, object]] = []
        for member_name, function in _authority_function_members(helper, helper_kind):
            globals_row, builtins_row = _function_binding_rows(
                function,
                namespace=namespace,
                root_ids=root_ids,
                helper_queue=helper_queue,
            )
            helper_globals.append({"member": member_name, **globals_row})
            helper_builtins.append({"member": member_name, **builtins_row})
        helper_rows.append(
            {
                "kind": helper_kind,
                "qualname": helper.__qualname__,
                "source_sha256": _source_digest(helper),
                "code_sha256": _runtime_code_digest(helper, helper_kind),
                "globals": helper_globals,
                "builtins": helper_builtins,
            }
        )
    helper_rows.sort(key=lambda row: (str(row["qualname"]), str(row["kind"])))
    return (
        _sha256(_canonical_json(globals_rows)),
        _sha256(_canonical_json(builtins_rows)),
        _sha256(_canonical_json(helper_rows)),
    )


def _authority_from_frozen_rows(
    rows: Sequence[Mapping[str, str]],
    expected_digest: str,
    *,
    require_bindings: bool,
) -> tuple[CodexComponentIdentity, ...]:
    try:
        identities = tuple(CodexComponentIdentity.model_validate(row) for row in rows)
    except ValueError as exc:
        raise CodexBaselineError("fixed production authority is internally invalid") from exc
    binding_fields = ("globals_sha256", "builtins_sha256", "helpers_sha256")
    if require_bindings and any(
        getattr(identity, field) is None for identity in identities for field in binding_fields
    ):
        raise CodexBaselineError("current production authority lacks binding digests")
    if not require_bindings and any(
        getattr(identity, field) is not None for identity in identities for field in binding_fields
    ):
        raise CodexBaselineError("historical production authority was mutated")
    if _component_set_hash(identities) != expected_digest:
        raise CodexBaselineError("fixed production authority digest mismatch")
    return identities


def _fixed_production_authority() -> tuple[CodexComponentIdentity, ...]:
    return _authority_from_frozen_rows(
        _CURRENT_PRODUCTION_AUTHORITY_ROWS,
        CODEX_PRODUCTION_AUTHORITY_DIGEST,
        require_bindings=True,
    )


def _frozen_historical_production_authority() -> tuple[CodexComponentIdentity, ...]:
    return _authority_from_frozen_rows(
        _FROZEN_HISTORICAL_PRODUCTION_AUTHORITY_ROWS,
        CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST,
        require_bindings=False,
    )


def _resolve_authority_export(identity: CodexComponentIdentity) -> object:
    module = importlib.import_module(identity.module)
    current: object = module
    for part in identity.export.split("."):
        if not hasattr(current, part):
            raise CodexBaselineError(
                f"fixed production export is unavailable: {identity.module}.{identity.export}"
            )
        current = getattr(current, part)
    return current


def _runner_module_identity() -> CodexComponentIdentity:
    try:
        source = Path(__file__).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    except OSError as exc:
        raise CodexBaselineError("runner module source is unavailable") from exc
    return CodexComponentIdentity(
        role="runner-module",
        kind="module",
        module=__name__,
        export="baseline_v1",
        qualname="baseline_v1",
        version=CODEX_BASELINE_RUNNER_VERSION,
        source_sha256=_sha256(source),
        code_sha256=_sha256(source),
    )


def _fixed_source_runner_identity() -> CodexComponentIdentity:
    return CodexComponentIdentity(
        role="runner-module",
        kind="module",
        module=__name__,
        export="baseline_v1",
        qualname="baseline_v1",
        version=CODEX_BASELINE_RUNNER_VERSION,
        source_sha256=CODEX_XB0_SOURCE_RUNNER_DIGEST,
        code_sha256=CODEX_XB0_SOURCE_RUNNER_DIGEST,
    )


def component_identities_v1() -> tuple[CodexComponentIdentity, ...]:
    """Validate runtime exports against the Gate-reviewed fixed allowlist."""

    _assert_exact_production_components()
    return _fixed_production_authority()


def probe_codex_baseline_preparation_v1() -> CodexBaselinePreparation:
    """Read-only X-B0 harness probe; it never opens SQLite or runs retrieval."""

    _assert_exact_production_components()
    identities = component_identities_v1()
    return CodexBaselinePreparation(
        components=identities,
        component_set_hash=_component_set_hash(identities),
        runner_identity=_runner_module_identity(),
    )


def _component_set_hash(identities: Sequence[CodexComponentIdentity]) -> str:
    return _sha256(
        _canonical_json([item.model_dump(mode="json", exclude_none=True) for item in identities])
    )


def _assert_exact_production_components() -> None:
    authority = _fixed_production_authority()
    bound_classes = {
        "adapter-class": CodexSessionAdapter,
        "ingestion-class": CodexIngestionService,
        "publication-class": CodexStoreMixin,
        "embedding-class": LocalHashEmbedding,
        "retriever-class": CodexHybridRetriever,
        "store-class": SQLiteStore,
        "evaluator": evaluate_reviewed_codex_retrieval,
    }
    for identity in authority:
        actual = _resolve_authority_export(identity)
        if (
            getattr(actual, "__module__", None) != identity.module
            or getattr(actual, "__qualname__", None) != identity.qualname
        ):
            raise CodexBaselineError(f"{identity.role} public module/qualname was forged")
        if identity.kind == "class":
            if type(actual) is not type:
                raise CodexBaselineError(f"{identity.role} is not an exact public class")
        elif not isinstance(actual, types.FunctionType) or inspect.unwrap(actual) is not actual:
            raise CodexBaselineError(f"{identity.role} was wrapped or replaced")
        if (
            _source_digest(actual) != identity.source_sha256
            or _runtime_code_digest(actual, identity.kind) != identity.code_sha256
        ):
            raise CodexBaselineError(f"{identity.role} fixed implementation digest mismatch")
        runtime_bindings = _runtime_binding_digests(actual, identity.kind)
        expected_bindings = (
            identity.globals_sha256,
            identity.builtins_sha256,
            identity.helpers_sha256,
        )
        if runtime_bindings != expected_bindings:
            raise CodexBaselineError(f"{identity.role} fixed binding digest mismatch")
        bound = bound_classes.get(identity.role)
        if bound is not None and bound is not actual:
            raise CodexBaselineError(f"{identity.role} baseline binding was replaced")
    if (
        getattr(_resolve_authority_export(authority[0]), "adapter_version", None)
        != "codex-jsonl-v2-clean-text"
        or getattr(_resolve_authority_export(authority[6]), "model_id", None) != "local-hash-v2"
    ):
        raise CodexBaselineError("fixed production component version mismatch")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _require_existing_nonsymlink(path: Path, *, directory: bool, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise CodexBaselineError(f"{label} does not exist") from exc
    if stat.S_ISLNK(mode):
        raise CodexBaselineError(f"{label} cannot be a symlink alias")
    if directory and not stat.S_ISDIR(mode):
        raise CodexBaselineError(f"{label} must be a directory")
    if not directory and not stat.S_ISREG(mode):
        raise CodexBaselineError(f"{label} must be a regular file")
    return path.resolve(strict=True)


def _require_no_symlink_ancestors(path: Path, root: Path, *, label: str) -> None:
    current = path
    while current != root:
        if current.exists() and stat.S_ISLNK(current.lstat().st_mode):
            raise CodexBaselineError(f"{label} contains a symlink alias")
        parent = current.parent
        if parent == current or not _is_relative_to(parent, root):
            raise CodexBaselineError(f"{label} escapes isolated_root")
        current = parent


def _formal_database_path() -> Path:
    return Path(__file__).resolve().parents[5] / "var" / "evidence-rag.sqlite3"


class _ValidatedPaths(_FrozenContract):
    isolated_root: Path
    database_path: Path
    raw_root: Path
    output_dir: Path
    derived_data: Path
    project_root: Path
    staging_dir: Path


def _validate_execution_paths(request: CodexBaselineRequest) -> _ValidatedPaths:
    isolated_root = _require_existing_nonsymlink(
        request.isolated_root,
        directory=True,
        label="isolated_root",
    )
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if isolated_root == temporary_root or not _is_relative_to(isolated_root, temporary_root):
        raise CodexBaselineError("isolated_root must be a child of the system temporary root")

    database_path = request.database_path.resolve(strict=False)
    raw_root = request.raw_root.resolve(strict=False)
    output_dir = request.output_dir.resolve(strict=False)
    for label, path in (
        ("database_path", database_path),
        ("raw_root", raw_root),
        ("output_dir", output_dir),
    ):
        if path == isolated_root or not _is_relative_to(path, isolated_root):
            raise CodexBaselineError(f"{label} must be a child of isolated_root")
        _require_no_symlink_ancestors(path, isolated_root, label=label)
    if database_path == _formal_database_path().resolve(strict=False):
        raise CodexBaselineError("formal database path is forbidden")
    if database_path.suffix != ".sqlite3":
        raise CodexBaselineError("database_path must be a new .sqlite3 file")
    if database_path.exists():
        raise CodexBaselineError("database_path reuse is forbidden, including empty files")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{database_path}{suffix}").exists():
            raise CodexBaselineError("database sidecar reuse is forbidden")
    for label, path in (("raw_root", raw_root), ("output_dir", output_dir)):
        if path.exists():
            raise CodexBaselineError(f"{label} reuse is forbidden, including empty directories")
    if (
        raw_root == output_dir
        or _is_relative_to(raw_root, output_dir)
        or _is_relative_to(output_dir, raw_root)
    ):
        raise CodexBaselineError("raw_root and output_dir must be disjoint")

    derived_data = isolated_root / ".codex-xb0-derived-data"
    project_root = isolated_root / ".codex-xb0-project"
    staging_dir = output_dir.with_name(f".{output_dir.name}.codex-xb0-staging")
    for label, path in (
        ("derived_data", derived_data),
        ("project_root", project_root),
        ("staging_dir", staging_dir),
    ):
        if path.exists():
            raise CodexBaselineError(f"{label} must be new")
        _require_no_symlink_ancestors(path, isolated_root, label=label)
    reserved = (database_path, raw_root, output_dir, derived_data, project_root, staging_dir)
    if len(set(reserved)) != len(reserved):
        raise CodexBaselineError("isolated execution paths collide")
    return _ValidatedPaths(
        isolated_root=isolated_root,
        database_path=database_path,
        raw_root=raw_root,
        output_dir=output_dir,
        derived_data=derived_data,
        project_root=project_root,
        staging_dir=staging_dir,
    )


def _replace_project_root(value: object, project_root: str) -> object:
    if isinstance(value, str):
        return value.replace(_PROJECT_ROOT_TOKEN, project_root)
    if isinstance(value, list):
        return [_replace_project_root(item, project_root) for item in value]
    if isinstance(value, dict):
        return {key: _replace_project_root(item, project_root) for key, item in value.items()}
    return value


def _safe_smoke_fixture_files(source: Path, isolated_root: Path) -> tuple[Path, ...]:
    resolved = source.resolve(strict=True)
    if not _is_relative_to(resolved, isolated_root):
        raise CodexBaselineError("smoke fixture_path must be inside isolated_root")
    if stat.S_ISLNK(source.lstat().st_mode):
        raise CodexBaselineError("smoke fixture symlink aliases are forbidden")
    if source.is_file():
        candidates = (source,)
    elif source.is_dir():
        candidates = tuple(sorted(path for path in source.rglob("*") if path.is_file()))
        for path in source.rglob("*"):
            if stat.S_ISLNK(path.lstat().st_mode):
                raise CodexBaselineError("smoke fixture symlink aliases are forbidden")
    else:
        raise CodexBaselineError("smoke fixture_path must be a file or directory")
    if not candidates or any(path.suffix != ".jsonl" for path in candidates):
        raise CodexBaselineError("smoke fixture must contain only JSONL regular files")
    return candidates


def _materialize_smoke_fixture(
    source: Path,
    destination: Path,
    *,
    project_root: Path,
    isolated_root: Path,
) -> Path:
    files = _safe_smoke_fixture_files(source, isolated_root)
    destination.mkdir(parents=True)
    source_is_file = source.is_file()
    for path in files:
        relative = Path(path.name) if source_is_file else path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        rows: list[object] = []
        try:
            for raw_line in path.read_bytes().splitlines():
                if raw_line.strip():
                    rows.append(json.loads(raw_line))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexBaselineError("smoke fixture contains invalid JSONL") from exc
        target.write_bytes(
            b"".join(
                _canonical_json(_replace_project_root(row, str(project_root))) + b"\n"
                for row in rows
            )
        )
    return destination


def _load_smoke_cases(path: Path, isolated_root: Path) -> tuple[CodexBaselineCase, ...]:
    resolved = _require_existing_nonsymlink(path, directory=False, label="golden_path")
    if not _is_relative_to(resolved, isolated_root):
        raise CodexBaselineError("smoke golden_path must be inside isolated_root")
    rows = _read_jsonl(resolved)
    try:
        cases = tuple(CodexBaselineCase.model_validate(row) for row in rows)
    except ValueError as exc:
        raise CodexBaselineError("invalid smoke Golden case contract") from exc
    if not cases or len(cases) > 8:
        raise CodexBaselineError("smoke Golden must contain from one through eight cases")
    if len({case.case_id for case in cases}) != len(cases):
        raise CodexBaselineError("smoke Golden case membership contains duplicates")
    if any(case.eligible_metrics for case in cases):
        raise CodexBaselineError("smoke cases cannot claim released metric eligibility")
    return cases


def _fixture_content_hash(golden_path: Path, fixture_path: Path) -> str:
    records: list[dict[str, object]] = []
    inputs = (golden_path, fixture_path)
    for root in inputs:
        paths = (root,) if root.is_file() else tuple(sorted(path for path in root.rglob("*")))
        for path in paths:
            if not path.is_file():
                continue
            relative = path.name if root.is_file() else path.relative_to(root).as_posix()
            raw = path.read_bytes()
            records.append(
                {
                    "root": "golden" if root == golden_path else "fixture",
                    "path": relative,
                    "sha256": _sha256(raw),
                    "size": len(raw),
                }
            )
    return _sha256(_canonical_json(records))


def _released_case(case: CodexGoldenCase) -> CodexBaselineCase:
    return CodexBaselineCase(
        case_id=case.case_id,
        slice=case.slice,
        query=case.query,
        eligible_metrics=case.eligible_metrics,
    )


def _case_denominator_contributions(case: CodexGoldenCase) -> dict[CodexMetric, int]:
    values = {metric: 0 for metric in CodexMetric}
    for metric in case.eligible_metrics:
        values[metric] = 1
    values[CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10] = sum(
        int(item.old_attempt) for item in case.hard_negatives
    )
    calls_by_id: dict[str, set[str]] = defaultdict(set)
    for truth in case.event_truth:
        if truth.call_id:
            calls_by_id[truth.call_id].add(truth.kind)
    values[CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10] = sum(
        int({"tool_call", "tool_result"} <= kinds) for kinds in calls_by_id.values()
    )
    values[CodexMetric.PATCH_ACCURACY_AT_10] = sum(
        truth.kind in {"patch", "file_change"} and truth.patch_applied is not None
        for truth in case.event_truth
    )
    values[CodexMetric.VALIDATION_ACCURACY_AT_10] = sum(
        truth.kind == "validation" for truth in case.event_truth
    )
    values[CodexMetric.FALSE_VALIDATED_RATE_AT_10] = sum(
        judgment.false_validation_category is not None for judgment in case.item_judgments
    )
    values[CodexMetric.OUTCOME_ACCURACY_AT_10] = sum(
        truth.kind == "outcome" for truth in case.event_truth
    )
    return values


def _empty_denominator_contributions() -> dict[CodexMetric, int]:
    return {metric: 0 for metric in CodexMetric}


def _sum_denominators(
    contributions: Iterable[Mapping[CodexMetric, int]],
) -> dict[CodexMetric, int]:
    totals = {metric: 0 for metric in CodexMetric}
    for item in contributions:
        for metric in CodexMetric:
            totals[metric] += int(item.get(metric, 0))
    return totals


def _validate_response(
    case: CodexBaselineCase,
    response: object,
) -> tuple[tuple[CodexRankedItem, ...], Mapping[str, object]]:
    if not isinstance(response, Mapping):
        raise CodexBaselineUnavailableError("production retriever returned no response mapping")
    trace = response.get("trace")
    results = response.get("results")
    if not isinstance(trace, Mapping) or not isinstance(results, list):
        raise CodexBaselineUnavailableError("production response lacks results or trace")
    if trace.get("fusion") != "codex-weighted-hybrid-v2":
        raise CodexBaselineUnavailableError("production fusion identity is unavailable")
    if trace.get("embedding_model") != "local-hash-v2":
        raise CodexBaselineUnavailableError("production embedding identity is unavailable")
    ranked: list[CodexRankedItem] = []
    for rank, item in enumerate(results, start=1):
        if not isinstance(item, Mapping):
            raise CodexBaselineUnavailableError("production result is not a mapping")
        if item.get("item_type") not in _FIXED_ITEM_TYPES:
            raise CodexBaselineUnavailableError("flat retrieval returned an episode/non-item view")
        if item.get("edges") not in (None, []):
            raise CodexBaselineUnavailableError("event graph material leaked into flat retrieval")
        try:
            channels = tuple(sorted({str(value) for value in item.get("channels", [])}))
            ranked.append(
                CodexRankedItem(
                    rank=rank,
                    item_id=str(item["entity_id"]),
                    thread_id=str(item["thread_id"]),
                    locator=str(item["evidence_locator"]),
                    item_type=str(item["item_type"]),
                    channels=channels,
                    score=float(item["score"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CodexBaselineUnavailableError(
                f"production result contract is unavailable for {case.case_id}"
            ) from exc
    if len(ranked) > CODEX_BASELINE_TOP_K:
        raise CodexBaselineUnavailableError("production retriever exceeded fixed top-k")
    return tuple(ranked), trace


def _trace_int(trace: Mapping[str, object], key: str) -> int:
    value = trace.get(key)
    if type(value) is not int or value < 0:
        raise CodexBaselineUnavailableError(f"production trace field is invalid: {key}")
    return value


def _trace_float(trace: Mapping[str, object], key: str, fallback: float) -> float:
    value = trace.get(key, fallback)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CodexBaselineUnavailableError(f"production trace field is invalid: {key}")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise CodexBaselineUnavailableError(f"production trace field is invalid: {key}")
    return result


def _safe_error_record(
    *,
    run_id: str,
    case_id: str,
    outcome: Literal["error", "unavailable"],
    exc: Exception,
) -> CodexBaselineErrorRecord:
    detail = f"{type(exc).__module__}.{type(exc).__qualname__}:{exc}"
    return CodexBaselineErrorRecord(
        run_id=run_id,
        case_id=case_id,
        outcome=outcome,
        error_type=type(exc).__name__,
        safe_summary=(
            "production response unavailable"
            if outcome == "unavailable"
            else "production retriever raised an exception"
        ),
        detail_sha256=_sha256(detail.encode("utf-8", errors="replace")),
    )


def _execute_cases(
    *,
    run_id: str,
    dataset_id: str,
    dataset_version: str,
    package_hash: str,
    cases: Sequence[CodexBaselineCase],
    denominator_by_case: Mapping[str, Mapping[CodexMetric, int]],
    retriever: CodexHybridRetriever,
    judgments_by_case: Mapping[str, frozenset[str]],
) -> tuple[
    tuple[CodexPredictionRecord, ...],
    tuple[CodexBaselineErrorRecord, ...],
    tuple[ReviewedRetrievalRow, ...],
]:
    predictions: list[CodexPredictionRecord] = []
    errors: list[CodexBaselineErrorRecord] = []
    reviewed_rows: list[ReviewedRetrievalRow] = []
    for case in cases:
        started = time.perf_counter()
        ranked: tuple[CodexRankedItem, ...] = ()
        reviewed_count = 0
        try:
            response = retriever.search(
                CodexSearchRequest(
                    query=case.query,
                    scope=CodexSearchScope(
                        project_id=CODEX_BASELINE_PROJECT_ID,
                        item_types=list(_FIXED_ITEM_TYPES),
                    ),
                    limit=CODEX_BASELINE_TOP_K,
                    include_edges=False,
                )
            )
            ranked, production_trace = _validate_response(case, response)
            authority_ids = judgments_by_case.get(case.case_id, frozenset())
            for item in ranked:
                if item.item_id not in authority_ids:
                    break
                reviewed_count += 1
                reviewed_rows.append(
                    ReviewedRetrievalRow(
                        dataset_id=CODEX_XB0_GOLDEN_DATASET_ID,
                        dataset_version=CODEX_XB0_GOLDEN_DATASET_VERSION,
                        package_hash=package_hash,
                        case_id=case.case_id,
                        rank=item.rank,
                        thread_id=item.thread_id,
                        item_id=item.item_id,
                        locator=item.locator,
                        reviewed=True,
                    )
                )
            top_threads = tuple(dict.fromkeys(item.thread_id for item in ranked))[
                :CODEX_BASELINE_THREAD_TOP_K
            ]
            case_trace = CodexBaselineTrace(
                case_id=case.case_id,
                outcome="retrieved" if ranked else "zero_result",
                latency_ms=_trace_float(
                    production_trace,
                    "duration_ms",
                    (time.perf_counter() - started) * 1000,
                ),
                lexical_candidates=_trace_int(production_trace, "lexical_candidates"),
                dense_candidates=_trace_int(production_trace, "dense_candidates"),
                dense_matches=_trace_int(production_trace, "dense_matches"),
                result_count=len(ranked),
                reviewed_result_count=reviewed_count,
                zero_result=not ranked,
                top_threads=top_threads,
            )
        except CodexBaselineUnavailableError as exc:
            error_record = _safe_error_record(
                run_id=run_id,
                case_id=case.case_id,
                outcome="unavailable",
                exc=exc,
            )
            errors.append(error_record)
            case_trace = CodexBaselineTrace(
                case_id=case.case_id,
                outcome="unavailable",
                latency_ms=float((time.perf_counter() - started) * 1000),
                lexical_candidates=0,
                dense_candidates=0,
                dense_matches=0,
                result_count=0,
                reviewed_result_count=0,
                zero_result=False,
                unavailable_reason="production response contract unavailable",
                error_detail_sha256=error_record.detail_sha256,
            )
        except Exception as exc:
            error_record = _safe_error_record(
                run_id=run_id,
                case_id=case.case_id,
                outcome="error",
                exc=exc,
            )
            errors.append(error_record)
            case_trace = CodexBaselineTrace(
                case_id=case.case_id,
                outcome="error",
                latency_ms=float((time.perf_counter() - started) * 1000),
                lexical_candidates=0,
                dense_candidates=0,
                dense_matches=0,
                result_count=0,
                reviewed_result_count=0,
                zero_result=False,
                error_type=type(exc).__name__,
                error_detail_sha256=error_record.detail_sha256,
            )
        predictions.append(
            CodexPredictionRecord(
                run_id=run_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                package_hash=package_hash,
                case_id=case.case_id,
                query=case.query,
                eligible_metrics=case.eligible_metrics,
                denominator_contributions={
                    metric: int(value)
                    for metric, value in denominator_by_case[case.case_id].items()
                },
                results=ranked,
                trace=case_trace,
            )
        )
    return tuple(predictions), tuple(errors), tuple(reviewed_rows)


def _metric_case_contributions(
    dataset: GoldenDataset,
    reviewed_rows: Sequence[ReviewedRetrievalRow],
) -> dict[str, dict[CodexMetric, float]]:
    grouped: dict[str, list[ReviewedRetrievalRow]] = defaultdict(list)
    for row in reviewed_rows:
        grouped[row.case_id].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row.rank)
    output: dict[str, dict[CodexMetric, float]] = {}
    for case in dataset.cases:
        rows = grouped.get(case.case_id, [])
        rows10 = [row for row in rows if row.rank <= 10]
        row_ids = {row.item_id for row in rows10}
        judgments = {item.item_id: item for item in case.item_judgments}
        values = {metric: 0.0 for metric in CodexMetric}
        if CodexMetric.THREAD_RECALL_AT_5 in case.eligible_metrics:
            expected = {item.thread_id for item in case.expected_threads}
            values[CodexMetric.THREAD_RECALL_AT_5] = float(
                bool(expected & {row.thread_id for row in rows[:5]})
            )
        if CodexMetric.EPISODE_RECALL_AT_5 in case.eligible_metrics:
            retrieved = {
                judgments[row.item_id].episode_id
                for row in rows[:5]
                if judgments[row.item_id].episode_id is not None
            }
            expected = {
                item.episode_id for item in case.expected_threads if item.episode_id is not None
            }
            values[CodexMetric.EPISODE_RECALL_AT_5] = float(bool(expected & retrieved))
        expected_items = {item.item_id for item in case.expected_items}
        if CodexMetric.ITEM_RECALL_AT_10 in case.eligible_metrics:
            values[CodexMetric.ITEM_RECALL_AT_10] = float(bool(expected_items & row_ids))
        if CodexMetric.MRR_AT_10 in case.eligible_metrics:
            first = next(
                (row.rank for row in rows10 if row.item_id in expected_items),
                None,
            )
            values[CodexMetric.MRR_AT_10] = 1.0 / first if first else 0.0
        if CodexMetric.GOAL_RECALL_AT_10 in case.eligible_metrics:
            goals = {item.item_id for item in case.expected_items if item.item_type == "UserGoal"}
            values[CodexMetric.GOAL_RECALL_AT_10] = float(bool(goals & row_ids))
        if CodexMetric.DECISION_RECALL_AT_10 in case.eligible_metrics:
            values[CodexMetric.DECISION_RECALL_AT_10] = float(bool(expected_items & row_ids))
        values[CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10] = float(
            sum(item.old_attempt and item.item_id in row_ids for item in case.hard_negatives)
        )
        if CodexMetric.EVENT_ORDER_ACCURACY_AT_10 in case.eligible_metrics:
            ordered = sorted(
                (truth for truth in case.event_truth if truth.order is not None),
                key=lambda truth: truth.order or 0,
            )
            rank_by_id = {row.item_id: row.rank for row in rows10}
            ranks = [rank_by_id[item.item_id] for item in ordered if item.item_id in rank_by_id]
            values[CodexMetric.EVENT_ORDER_ACCURACY_AT_10] = float(
                len(ranks) == len(ordered) and ranks == sorted(ranks)
            )
        calls_by_id: dict[str, list[Any]] = defaultdict(list)
        for truth in case.event_truth:
            if truth.call_id:
                calls_by_id[truth.call_id].append(truth)
        values[CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10] = float(
            sum(
                all(
                    truth.item_id in row_ids
                    for truth in truths
                    if truth.kind in {"tool_call", "tool_result"}
                )
                for truths in calls_by_id.values()
                if {"tool_call", "tool_result"} <= {truth.kind for truth in truths}
            )
        )
        values[CodexMetric.PATCH_ACCURACY_AT_10] = float(
            sum(
                truth.item_id in row_ids
                and judgments[truth.item_id].patch_applied == truth.patch_applied
                for truth in case.event_truth
                if truth.kind in {"patch", "file_change"} and truth.patch_applied is not None
            )
        )
        values[CodexMetric.VALIDATION_ACCURACY_AT_10] = float(
            sum(
                truth.item_id in row_ids
                and judgments[truth.item_id].validation_status
                == ("passed" if truth.exit_code == 0 else "failed")
                for truth in case.event_truth
                if truth.kind == "validation"
            )
        )
        values[CodexMetric.FALSE_VALIDATED_RATE_AT_10] = float(
            sum(
                judgment.false_validated is True and judgment.item_id in row_ids
                for judgment in case.item_judgments
                if judgment.false_validation_category is not None
            )
        )
        values[CodexMetric.OUTCOME_ACCURACY_AT_10] = float(
            sum(
                truth.item_id in row_ids and judgments[truth.item_id].outcome_correct is True
                for truth in case.event_truth
                if truth.kind == "outcome"
            )
        )
        seen: set[tuple[str, str]] = set()
        duplicated = False
        for row in rows10:
            identity = (row.thread_id, row.item_id)
            duplicated = duplicated or identity in seen
            seen.add(identity)
        if CodexMetric.CONTEXT_DUPLICATE_RATE_AT_10 in case.eligible_metrics:
            values[CodexMetric.CONTEXT_DUPLICATE_RATE_AT_10] = float(duplicated)
        if CodexMetric.CONTEXT_NOISE_RATE_AT_10 in case.eligible_metrics:
            values[CodexMetric.CONTEXT_NOISE_RATE_AT_10] = float(
                any(judgments[row.item_id].context_noise for row in rows10)
            )
        output[case.case_id] = values
    return output


def _slice_report(
    *,
    run_id: str,
    cases: Sequence[CodexBaselineCase],
    denominator_by_case: Mapping[str, Mapping[CodexMetric, int]],
    predictions: Sequence[CodexPredictionRecord],
    contributions: Mapping[str, Mapping[CodexMetric, float]] | None,
) -> dict[str, object]:
    prediction_by_case = {item.case_id: item for item in predictions}
    rows: list[dict[str, object]] = []
    for slice_name in CodexGoldenSlice:
        members = [case for case in cases if case.slice == slice_name]
        denominators = _sum_denominators(denominator_by_case[case.case_id] for case in members)
        metrics: list[dict[str, object]] = []
        if contributions is not None:
            for metric in CodexMetric:
                denominator = denominators[metric]
                numerator = sum(contributions[case.case_id][metric] for case in members)
                metrics.append(
                    {
                        "name": metric.value,
                        "numerator": numerator if denominator else None,
                        "denominator": denominator,
                        "value": numerator / denominator if denominator else None,
                        "status": "AVAILABLE" if denominator else "UNAVAILABLE",
                    }
                )
        rows.append(
            {
                "slice": slice_name.value,
                "case_ids": [case.case_id for case in members],
                "case_count": len(members),
                "eligible_denominators": {
                    metric.value: denominators[metric] for metric in CodexMetric
                },
                "retrieval_outcomes": dict(
                    Counter(prediction_by_case[case.case_id].trace.outcome for case in members)
                ),
                "metrics": metrics,
            }
        )
    return {
        "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "slice_count": len(CodexGoldenSlice),
        "slices": rows,
    }


def _metric_payload(
    *,
    run_id: str,
    execution_mode: Literal["released", "smoke"],
    metric_results: Sequence[MetricResult],
    metric_denominators: Mapping[CodexMetric, int],
) -> dict[str, object]:
    return {
        "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "evaluator": (
            "evaluate_reviewed_codex_retrieval"
            if execution_mode == "released"
            else "NOT_RUN_SMOKE_NON_QUALIFIED"
        ),
        "status": "AVAILABLE" if execution_mode == "released" else "SMOKE_UNAVAILABLE",
        "eligible_denominators": {
            metric.value: metric_denominators[metric] for metric in CodexMetric
        },
        "metrics": [item.model_dump(mode="json") for item in metric_results],
        "baseline_qualified": False,
    }


def _latency_payload(
    run_id: str,
    predictions: Sequence[CodexPredictionRecord],
) -> dict[str, object]:
    values = sorted(item.trace.latency_ms for item in predictions)

    def percentile(fraction: float) -> float:
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, math.ceil(len(values) * fraction) - 1))
        return float(values[index])

    return {
        "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "unit": "milliseconds",
        "case_count": len(predictions),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": float(values[-1]) if values else 0.0,
        "cases": [
            {
                "case_id": item.case_id,
                "latency_ms": item.trace.latency_ms,
                "lexical_candidates": item.trace.lexical_candidates,
                "dense_candidates": item.trace.dense_candidates,
                "dense_matches": item.trace.dense_matches,
                "zero_result": item.trace.zero_result,
                "outcome": item.trace.outcome,
                "fallback_used": False,
            }
            for item in predictions
        ],
    }


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_json(value) + b"\n")


def _write_jsonl(path: Path, values: Iterable[object]) -> None:
    path.write_bytes(b"".join(_canonical_json(value) + b"\n" for value in values))


def _json_loads(raw: bytes, *, label: str) -> object:
    try:
        return json.loads(
            raw,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CodexBaselineError(f"invalid canonical JSON: {label}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CodexBaselineError(f"unable to read artifact file: {path.name}") from exc
    value = _json_loads(raw, label=path.name)
    try:
        canonical = _canonical_json(value)
    except ValueError as exc:
        raise CodexBaselineError(f"artifact JSON contains non-finite values: {path.name}") from exc
    if not isinstance(value, dict) or raw != canonical + b"\n":
        raise CodexBaselineError(f"artifact JSON is not canonical: {path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CodexBaselineError(f"unable to read JSONL: {path.name}") from exc
    rows: list[dict[str, Any]] = []
    rebuilt = b""
    for line in raw.splitlines():
        if not line:
            raise CodexBaselineError(f"blank JSONL row is forbidden: {path.name}")
        value = _json_loads(line, label=path.name)
        if not isinstance(value, dict):
            raise CodexBaselineError(f"JSONL row must be an object: {path.name}")
        rows.append(value)
        try:
            rebuilt += _canonical_json(value) + b"\n"
        except ValueError as exc:
            raise CodexBaselineError(
                f"artifact JSONL contains non-finite values: {path.name}"
            ) from exc
    if raw != rebuilt:
        raise CodexBaselineError(f"artifact JSONL is not canonical: {path.name}")
    return rows


def _is_declared_identity_string(field_name: str | None, value: str) -> bool:
    if field_name in _DECLARED_SHA256_FIELDS and _SHA256_RE.fullmatch(value):
        return True
    if field_name == "run_id" and _RUN_ID_RE.fullmatch(value):
        return True
    return bool(field_name and field_name.endswith("_id") and _UUID_RE.fullmatch(value))


def _scan_string(name: str, text: str, *, field_name: str | None) -> list[str]:
    findings: list[str] = []
    for label, pattern in (
        ("absolute_path", _ABSOLUTE_PATH_RE),
        ("credential", _CREDENTIAL_RE),
        ("email", _EMAIL_RE),
        ("temporary_or_generated", _FORBIDDEN_ARTIFACT_TEXT_RE),
    ):
        if pattern.search(text):
            findings.append(f"{name}:{label}")
    if not _is_declared_identity_string(field_name, text) and _PAYMENT_RE.search(text):
        findings.append(f"{name}:payment")
    return findings


def _scan_json_strings(
    name: str,
    value: object,
    *,
    field_name: str | None = None,
) -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            findings.extend(_scan_string(name, key, field_name=None))
            findings.extend(_scan_json_strings(name, item, field_name=key))
    elif isinstance(value, list):
        for item in value:
            findings.extend(_scan_json_strings(name, item, field_name=field_name))
    elif type(value) is str:
        findings.extend(_scan_string(name, value, field_name=field_name))
    return findings


def _scan_artifact_files(directory: Path, names: Iterable[str]) -> list[str]:
    findings: list[str] = []
    for name in names:
        path = directory / name
        value = _read_json(path) if name.endswith(".json") else _read_jsonl(path)
        findings.extend(_scan_json_strings(name, value))
        try:
            _assert_finite(value)
        except ValueError:
            findings.append(f"{name}:nonfinite")
    return sorted(set(findings))


def _file_records(directory: Path, names: Iterable[str]) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for name in names:
        raw = (directory / name).read_bytes()
        records[name] = {"sha256": _sha256(raw), "size": len(raw)}
    return records


def _artifact_set_hash(files: Mapping[str, Mapping[str, object]]) -> str:
    return _sha256(_canonical_json(files))


def _require_artifact_tree(directory: Path) -> Path:
    resolved = _require_existing_nonsymlink(
        directory,
        directory=True,
        label="artifact directory",
    )
    entries: set[str] = set()
    for path in directory.iterdir():
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise CodexBaselineError("artifact may contain only top-level regular files")
        entries.add(path.name)
    if entries != set(CANONICAL_ARTIFACT_FILES):
        missing = sorted(set(CANONICAL_ARTIFACT_FILES) - entries)
        extra = sorted(entries - set(CANONICAL_ARTIFACT_FILES))
        raise CodexBaselineError(
            f"canonical artifact set mismatch: missing={missing}, extra={extra}"
        )
    return resolved


def _golden_rows(
    *,
    run_id: str,
    dataset_id: str,
    dataset_version: str,
    package_hash: str,
    cases: Sequence[CodexBaselineCase],
    denominator_by_case: Mapping[str, Mapping[CodexMetric, int]],
    released_dataset: GoldenDataset | None,
) -> list[dict[str, object]]:
    authority_by_case = (
        {case.case_id: case.model_dump(mode="json") for case in released_dataset.cases}
        if released_dataset is not None
        else {}
    )
    return [
        {
            "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
            "run_id": run_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "package_hash": package_hash,
            "case_id": case.case_id,
            "slice": case.slice.value,
            "query": case.query,
            "eligible_metrics": [metric.value for metric in case.eligible_metrics],
            "denominator_contributions": {
                metric.value: denominator_by_case[case.case_id][metric] for metric in CodexMetric
            },
            "authority": authority_by_case.get(case.case_id),
            "authority_sha256": (
                _sha256(_canonical_json(authority_by_case[case.case_id]))
                if case.case_id in authority_by_case
                else None
            ),
        }
        for case in cases
    ]


def _cleanup_derived(paths: _ValidatedPaths) -> None:
    for suffix in ("-wal", "-shm", "-journal", ""):
        candidate = Path(f"{paths.database_path}{suffix}")
        try:
            if candidate.is_file() or candidate.is_symlink():
                candidate.unlink()
        except OSError:
            pass
    for directory in (paths.raw_root, paths.derived_data, paths.project_root, paths.staging_dir):
        try:
            if directory.exists():
                shutil.rmtree(directory)
        except OSError:
            pass


def _build_artifact(
    *,
    paths: _ValidatedPaths,
    run_id: str,
    execution_mode: Literal["released", "smoke"],
    dataset_id: str,
    dataset_version: str,
    package_hash: str,
    seed: int,
    identities: Sequence[CodexComponentIdentity],
    runner_identity: CodexComponentIdentity,
    cases: Sequence[CodexBaselineCase],
    released_dataset: GoldenDataset | None,
    denominator_by_case: Mapping[str, Mapping[CodexMetric, int]],
    predictions: Sequence[CodexPredictionRecord],
    errors: Sequence[CodexBaselineErrorRecord],
    metric_results: Sequence[MetricResult],
    slice_report: Mapping[str, object],
) -> str:
    paths.output_dir.parent.mkdir(parents=True, exist_ok=True)
    paths.staging_dir.mkdir()
    component_hash = _component_set_hash(identities)
    if component_hash != CODEX_PRODUCTION_AUTHORITY_DIGEST:
        raise CodexBaselineError("artifact production authority is not the fixed allowlist")
    golden_authority_digest = (
        CODEX_XB0_GOLDEN_AUTHORITY_DIGEST if released_dataset is not None else None
    )
    dataset_authority_digest = (
        CODEX_XB0_GOLDEN_DATASET_DIGEST if released_dataset is not None else None
    )
    metric_denominators = _sum_denominators(denominator_by_case.values())
    slice_counts = Counter(case.slice.value for case in cases)
    run_payload = {
        "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
        "artifact_version": CODEX_BASELINE_ARTIFACT_VERSION,
        "runner_version": CODEX_BASELINE_RUNNER_VERSION,
        "run_id": run_id,
        "execution_mode": execution_mode,
        "status": "COMPLETED" if execution_mode == "released" else "SMOKE_COMPLETED",
        "release_posture": (
            "NON_QUALIFIED" if execution_mode == "released" else "SMOKE_NON_QUALIFIED"
        ),
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "package_hash": package_hash,
        "seed": seed,
        "case_count": len(cases),
        "case_membership": [case.case_id for case in cases],
        "component_set_hash": component_hash,
        "production_authority_version": CODEX_PRODUCTION_AUTHORITY_VERSION,
        "production_authority_digest": CODEX_PRODUCTION_AUTHORITY_DIGEST,
        "runner_identity": runner_identity.model_dump(mode="json", exclude_none=True),
        "golden_authority_digest": golden_authority_digest,
        "config": CodexBaselineConfig().model_dump(mode="json"),
        "error_count": len(errors),
        "zero_result_count": sum(item.trace.zero_result for item in predictions),
        "fallback_count": 0,
        "baseline_qualified": False,
    }
    manifest_payload = {
        "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
        "artifact_version": CODEX_BASELINE_ARTIFACT_VERSION,
        "run_id": run_id,
        "dataset": {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "schema_version": (
                CODEX_XB0_GOLDEN_SCHEMA_VERSION
                if released_dataset is not None
                else "codex-smoke-foundation-v1"
            ),
            "package_hash": package_hash,
            "case_count": len(cases),
            "case_membership": [case.case_id for case in cases],
            "authority_hash": golden_authority_digest,
            "dataset_authority_digest": dataset_authority_digest,
            "fixture_thread_ids": (
                list(released_dataset.fixture_thread_ids) if released_dataset else []
            ),
            "fixture_item_ids": (
                list(released_dataset.fixture_item_ids) if released_dataset else []
            ),
            "fixture_item_locators": (
                list(released_dataset.fixture_item_locators) if released_dataset else []
            ),
        },
        "seed": seed,
        "config": CodexBaselineConfig().model_dump(mode="json"),
        "components": [item.model_dump(mode="json", exclude_none=True) for item in identities],
        "component_set_hash": component_hash,
        "production_authority_version": CODEX_PRODUCTION_AUTHORITY_VERSION,
        "production_authority_digest": CODEX_PRODUCTION_AUTHORITY_DIGEST,
        "runner_identity": runner_identity.model_dump(mode="json", exclude_none=True),
        "golden_authority_digest": golden_authority_digest,
        "metric_denominators": {
            metric.value: metric_denominators[metric] for metric in CodexMetric
        },
        "slice_counts": dict(sorted(slice_counts.items())),
        "canonical_files": list(CANONICAL_ARTIFACT_FILES),
        "portable_paths_only": True,
        "baseline_qualified": False,
    }
    metrics_payload = _metric_payload(
        run_id=run_id,
        execution_mode=execution_mode,
        metric_results=metric_results,
        metric_denominators=metric_denominators,
    )
    _write_json(paths.staging_dir / "run.json", run_payload)
    _write_json(paths.staging_dir / "manifest.json", manifest_payload)
    _write_jsonl(
        paths.staging_dir / "golden_cases.jsonl",
        _golden_rows(
            run_id=run_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            package_hash=package_hash,
            cases=cases,
            denominator_by_case=denominator_by_case,
            released_dataset=released_dataset,
        ),
    )
    _write_jsonl(
        paths.staging_dir / "predictions.jsonl",
        (item.model_dump(mode="json") for item in predictions),
    )
    _write_json(paths.staging_dir / "metrics.json", metrics_payload)
    _write_json(paths.staging_dir / "slice_report.json", dict(slice_report))
    _write_jsonl(
        paths.staging_dir / "errors.jsonl",
        (item.model_dump(mode="json") for item in errors),
    )
    _write_json(paths.staging_dir / "latency.json", _latency_payload(run_id, predictions))
    findings = _scan_artifact_files(paths.staging_dir, _SECURITY_INPUT_FILES)
    if findings:
        raise CodexBaselineError(f"artifact security scan failed: {findings}")
    security_payload = {
        "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "clean",
        "scanned_files": list(_SECURITY_INPUT_FILES),
        "checks": {
            "absolute_paths": "clean",
            "credentials": "clean",
            "personal_or_payment_data": "clean",
            "temporary_database_or_raw_fixture": "clean",
            "sqlite_sidecars": "clean",
            "compiled_python": "clean",
            "nonfinite_numbers": "clean",
            "undeclared_files": "clean",
        },
        "findings": [],
    }
    _write_json(paths.staging_dir / "security_report.json", security_payload)
    files = _file_records(paths.staging_dir, CHECKSUM_TARGET_FILES)
    set_hash = _artifact_set_hash(files)
    _write_json(
        paths.staging_dir / "checksums.json",
        {
            "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
            "run_id": run_id,
            "algorithm": "sha256",
            "files": files,
            "artifact_set_hash": set_hash,
        },
    )
    verify_codex_baseline_artifact_v1(paths.staging_dir)
    paths.staging_dir.rename(paths.output_dir)
    return set_hash


def run_codex_baseline_v1(request: CodexBaselineRequest) -> CodexBaselineResult:
    """Run one isolated released X-B0 or explicitly non-qualified smoke execution."""

    if type(request) is not CodexBaselineRequest:
        raise CodexBaselineError("request must be the exact frozen CodexBaselineRequest")
    paths = _validate_execution_paths(request)
    output_published = False
    try:
        _assert_exact_production_components()
        identities = component_identities_v1()
        component_hash = _component_set_hash(identities)
        runner_identity = _runner_module_identity()
        dataset: GoldenDataset | None = None
        if request.execution_mode == "released":
            dataset = load_codex_golden_v1(request.golden_path)
            fixture_dataset = load_codex_golden_v1(request.fixture_path)
            if (
                dataset.dataset_id != CODEX_XB0_GOLDEN_DATASET_ID
                or dataset.dataset_version != CODEX_XB0_GOLDEN_DATASET_VERSION
                or dataset.package_hash != CODEX_XB0_GOLDEN_PACKAGE_HASH
                or dataset.authority_hash != CODEX_XB0_GOLDEN_AUTHORITY_DIGEST
                or dataset.case_membership != CODEX_XB0_CASE_MEMBERSHIP
                or _sha256(_canonical_json(dataset.model_dump(mode="json")))
                != CODEX_XB0_GOLDEN_DATASET_DIGEST
                or fixture_dataset.package_hash != dataset.package_hash
                or fixture_dataset.case_membership != dataset.case_membership
                or fixture_dataset.authority_hash != dataset.authority_hash
                or len(dataset.cases) != CODEX_XB0_CASE_COUNT
            ):
                raise CodexBaselineError("released Golden/fixture package identity mismatch")
            cases = tuple(_released_case(case) for case in dataset.cases)
            package_hash = dataset.package_hash
            dataset_id = dataset.dataset_id
            dataset_version = dataset.dataset_version
            denominator_by_case = {
                case.case_id: _case_denominator_contributions(case) for case in dataset.cases
            }
            judgments_by_case = {
                case.case_id: frozenset(item.item_id for item in case.item_judgments)
                for case in dataset.cases
            }
        else:
            cases = _load_smoke_cases(request.golden_path, paths.isolated_root)
            package_hash = _fixture_content_hash(request.golden_path, request.fixture_path)
            dataset_id = "codex-smoke-v1"
            dataset_version = "v1"
            denominator_by_case = {
                case.case_id: _empty_denominator_contributions() for case in cases
            }
            judgments_by_case = {case.case_id: frozenset() for case in cases}

        run_id = (
            "codex-xb0-"
            + hashlib.sha256(
                _canonical_json(
                    {
                        "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
                        "execution_mode": request.execution_mode,
                        "dataset_id": dataset_id,
                        "dataset_version": dataset_version,
                        "package_hash": package_hash,
                        "seed": request.seed,
                        "case_membership": [case.case_id for case in cases],
                        "component_set_hash": component_hash,
                        "production_authority_digest": CODEX_PRODUCTION_AUTHORITY_DIGEST,
                        "runner_identity": runner_identity.model_dump(
                            mode="json", exclude_none=True
                        ),
                        "golden_authority_digest": (
                            CODEX_XB0_GOLDEN_AUTHORITY_DIGEST if dataset else None
                        ),
                        "config": request.config.model_dump(mode="json"),
                    }
                )
            ).hexdigest()[:32]
        )

        paths.project_root.mkdir()
        if request.execution_mode == "released":
            materialize_codex_session_fixture(
                paths.raw_root,
                project_root=paths.project_root,
                fixture_root=request.fixture_path,
            )
        else:
            _materialize_smoke_fixture(
                request.fixture_path,
                paths.raw_root,
                project_root=paths.project_root,
                isolated_root=paths.isolated_root,
            )

        settings = Settings(
            data_dir=paths.derived_data,
            database_path=paths.database_path,
            repository_cache=paths.derived_data / "repositories",
            web_dir=paths.project_root,
            allowed_local_roots=(paths.isolated_root,),
            codex_home=paths.raw_root,
            project_root=paths.project_root,
            embedding_dimensions=CODEX_BASELINE_EMBEDDING_DIMENSIONS,
            rag_code_engine="v1",
            rag_code_shadow=False,
            rag_code_unit_builder="raw-v1",
            rag_code_embedding_profile="local-hash-v2",
            rag_code_graph=False,
            rag_code_semantic_resolver="off",
            rag_code_reranker="off",
            rag_code_context="snippet-v1",
            rag_code_canary_percent=0,
        )
        settings.prepare()
        store = SQLiteStore(paths.database_path)
        if type(store) is not SQLiteStore:
            raise CodexBaselineError("SQLite store is not the exact production class")
        store.initialize()
        raw_store = RawSourceStore(store)
        raw_store.initialize()
        sources = RawSourceService(raw_store, settings.raw_object_dir)
        embedder = LocalHashEmbedding(CODEX_BASELINE_EMBEDDING_DIMENSIONS)
        adapter = CodexSessionAdapter(settings)
        ingestion = CodexIngestionService(store, adapter, embedder, sources)
        retriever = CodexHybridRetriever(store, embedder)
        if (
            type(adapter) is not CodexSessionAdapter
            or type(ingestion) is not CodexIngestionService
            or type(retriever) is not CodexHybridRetriever
            or type(embedder) is not LocalHashEmbedding
        ):
            raise CodexBaselineError("production component instance identity mismatch")
        ingest_request = CodexIngestRequest(
            source=str(paths.raw_root),
            project_path=str(paths.project_root),
            project_id=CODEX_BASELINE_PROJECT_ID,
            acl_ref=CODEX_BASELINE_ACL_REF,
            include_archived=True,
            max_sessions=min(2_000, max(1, CODEX_XB0_CASE_COUNT)),
        )
        workflow_id = ingestion.enqueue(ingest_request)
        ingestion.run(workflow_id, ingest_request)
        workflow = store.get_workflow(workflow_id)
        if workflow is None or workflow.get("status") != "completed":
            raise CodexBaselineError("production Codex ingestion/publication did not complete")
        if workflow.get("counters", {}).get("views", 0) <= 0:
            raise CodexBaselineError("production publication created no item views")

        predictions, errors, reviewed_rows = _execute_cases(
            run_id=run_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            package_hash=package_hash,
            cases=cases,
            denominator_by_case=denominator_by_case,
            retriever=retriever,
            judgments_by_case=judgments_by_case,
        )
        metric_results: tuple[MetricResult, ...] = ()
        contributions: dict[str, dict[CodexMetric, float]] | None = None
        if dataset is not None:
            metric_results = evaluate_reviewed_codex_retrieval(
                dataset,
                reviewed_rows,
                case_membership=dataset.case_membership,
            )
            expected_denominators = _sum_denominators(denominator_by_case.values())
            for result in metric_results:
                metric = CodexMetric(result.name)
                if result.denominator != expected_denominators[metric]:
                    raise CodexBaselineError("X0 evaluator denominator differs from harness")
            contributions = _metric_case_contributions(dataset, reviewed_rows)
            for result in metric_results:
                metric = CodexMetric(result.name)
                numerator = sum(item[metric] for item in contributions.values())
                if result.numerator is None or abs(float(result.numerator) - numerator) > 1e-12:
                    raise CodexBaselineError("X0 evaluator numerator differs from slice ledger")
        slice_report = _slice_report(
            run_id=run_id,
            cases=cases,
            denominator_by_case=denominator_by_case,
            predictions=predictions,
            contributions=contributions,
        )
        set_hash = _build_artifact(
            paths=paths,
            run_id=run_id,
            execution_mode=request.execution_mode,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            package_hash=package_hash,
            seed=request.seed,
            identities=identities,
            runner_identity=runner_identity,
            cases=cases,
            released_dataset=dataset,
            denominator_by_case=denominator_by_case,
            predictions=predictions,
            errors=errors,
            metric_results=metric_results,
            slice_report=slice_report,
        )
        output_published = True
        verification = verify_codex_baseline_artifact_v1(paths.output_dir)
        if verification.artifact_set_hash != set_hash:
            raise CodexBaselineError("published artifact verification identity changed")
        return CodexBaselineResult(
            run_id=run_id,
            execution_mode=request.execution_mode,
            status=(
                "COMPLETED"
                if request.execution_mode == "released"
                else "SMOKE_COMPLETED_NON_QUALIFIED"
            ),
            release_posture=(
                "NON_QUALIFIED" if request.execution_mode == "released" else "SMOKE_NON_QUALIFIED"
            ),
            artifact_path=paths.output_dir,
            artifact_set_hash=set_hash,
            case_count=len(cases),
            prediction_count=len(predictions),
            error_count=len(errors),
            zero_result_count=sum(item.trace.zero_result for item in predictions),
        )
    except Exception:
        if output_published and paths.output_dir.exists():
            shutil.rmtree(paths.output_dir)
        raise
    finally:
        _cleanup_derived(paths)


def _parse_components(value: object) -> tuple[CodexComponentIdentity, ...]:
    if not isinstance(value, list):
        raise CodexBaselineError("manifest components must be an ordered list")
    try:
        return tuple(CodexComponentIdentity.model_validate(item) for item in value)
    except ValueError as exc:
        raise CodexBaselineError("manifest component identity is invalid") from exc


def _string_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or not all(type(item) is str for item in value):
        raise CodexBaselineError(f"{label} must be an ordered string list")
    return value


def _integer_map(value: object, *, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or not all(
        type(key) is str and type(item) is int and item >= 0 for key, item in value.items()
    ):
        raise CodexBaselineError(f"{label} must be a non-negative integer map")
    return dict(value)


def _rebuild_released_dataset_authority(
    dataset_payload: Mapping[str, object],
    golden_rows: Sequence[Mapping[str, object]],
) -> GoldenDataset:
    if (
        dataset_payload.get("dataset_id") != CODEX_XB0_GOLDEN_DATASET_ID
        or dataset_payload.get("dataset_version") != CODEX_XB0_GOLDEN_DATASET_VERSION
        or dataset_payload.get("schema_version") != CODEX_XB0_GOLDEN_SCHEMA_VERSION
        or dataset_payload.get("package_hash") != CODEX_XB0_GOLDEN_PACKAGE_HASH
        or dataset_payload.get("authority_hash") != CODEX_XB0_GOLDEN_AUTHORITY_DIGEST
        or dataset_payload.get("dataset_authority_digest") != CODEX_XB0_GOLDEN_DATASET_DIGEST
        or tuple(dataset_payload.get("case_membership", ())) != CODEX_XB0_CASE_MEMBERSHIP
        or dataset_payload.get("case_count") != CODEX_XB0_CASE_COUNT
    ):
        raise CodexBaselineError("released Golden identity is not Gate-approved")
    authorities: list[dict[str, Any]] = []
    cases: list[CodexGoldenCase] = []
    for expected_id, row in zip(CODEX_XB0_CASE_MEMBERSHIP, golden_rows, strict=True):
        authority = row.get("authority")
        if not isinstance(authority, dict):
            raise CodexBaselineError("released Golden case lacks frozen authority payload")
        authority_sha256 = _sha256(_canonical_json(authority))
        if (
            row.get("authority_sha256") != authority_sha256
            or authority.get("case_id") != expected_id
        ):
            raise CodexBaselineError("released Golden per-case authority digest mismatch")
        try:
            case = CodexGoldenCase.model_validate(authority)
        except ValueError as exc:
            raise CodexBaselineError("released Golden case authority is invalid") from exc
        expected_denominators = {
            metric.value: value for metric, value in _case_denominator_contributions(case).items()
        }
        if (
            row.get("case_id") != case.case_id
            or row.get("slice") != case.slice.value
            or row.get("query") != case.query
            or row.get("eligible_metrics") != [metric.value for metric in case.eligible_metrics]
            or row.get("denominator_contributions") != expected_denominators
        ):
            raise CodexBaselineError("Golden summary differs from frozen case authority")
        authorities.append(authority)
        cases.append(case)
    authority_digest = _sha256(
        b"".join(_canonical_json(authority) + b"\n" for authority in authorities)
    )
    if authority_digest != CODEX_XB0_GOLDEN_AUTHORITY_DIGEST:
        raise CodexBaselineError("released Golden authority digest mismatch")
    dataset_authority = {
        "dataset_id": CODEX_XB0_GOLDEN_DATASET_ID,
        "dataset_version": CODEX_XB0_GOLDEN_DATASET_VERSION,
        "schema_version": CODEX_XB0_GOLDEN_SCHEMA_VERSION,
        "package_hash": CODEX_XB0_GOLDEN_PACKAGE_HASH,
        "authority_hash": CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
        "cases": authorities,
        "fixture_thread_ids": dataset_payload.get("fixture_thread_ids"),
        "fixture_item_ids": dataset_payload.get("fixture_item_ids"),
        "fixture_item_locators": dataset_payload.get("fixture_item_locators"),
    }
    if _sha256(_canonical_json(dataset_authority)) != CODEX_XB0_GOLDEN_DATASET_DIGEST:
        raise CodexBaselineError("released full Golden dataset authority digest mismatch")
    try:
        dataset = GoldenDataset.model_validate(dataset_authority)
    except ValueError as exc:
        raise CodexBaselineError("released Golden dataset authority cannot be rebuilt") from exc
    if dataset.cases != tuple(cases):
        raise CodexBaselineError("rebuilt Golden cases differ from frozen authority")
    return dataset


def _reviewed_rows_from_predictions(
    dataset: GoldenDataset,
    predictions: Sequence[CodexPredictionRecord],
) -> tuple[ReviewedRetrievalRow, ...]:
    cases = {case.case_id: case for case in dataset.cases}
    reviewed: list[ReviewedRetrievalRow] = []
    for prediction in predictions:
        case = cases[prediction.case_id]
        judgments = {item.item_id: item for item in case.item_judgments}
        reviewed_count = 0
        for result in prediction.results:
            judgment = judgments.get(result.item_id)
            if judgment is None:
                break
            try:
                row = ReviewedRetrievalRow(
                    dataset_id=CODEX_XB0_GOLDEN_DATASET_ID,
                    dataset_version=CODEX_XB0_GOLDEN_DATASET_VERSION,
                    package_hash=CODEX_XB0_GOLDEN_PACKAGE_HASH,
                    case_id=prediction.case_id,
                    rank=result.rank,
                    thread_id=result.thread_id,
                    item_id=result.item_id,
                    locator=result.locator,
                    reviewed=True,
                )
            except ValueError as exc:
                raise CodexBaselineError(
                    "prediction cannot rebuild a strict ReviewedRetrievalRow"
                ) from exc
            if row.thread_id != judgment.thread_id or row.locator != judgment.locator:
                raise CodexBaselineError("prediction differs from treatment judgment authority")
            reviewed.append(row)
            reviewed_count += 1
        if prediction.trace.reviewed_result_count != reviewed_count:
            raise CodexBaselineError("reviewed prediction prefix count mismatch")
    return tuple(reviewed)


def _require_exact_keys(
    value: Mapping[str, object],
    expected: Iterable[str],
    *,
    label: str,
) -> None:
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        raise CodexBaselineError(f"{label} fields mismatch: missing={missing}, extra={extra}")


def verify_codex_baseline_artifact_v1(
    directory: str | Path,
) -> CodexBaselineVerification:
    """Verify a portable artifact read-only; this function never runs retrieval."""

    artifact = Path(directory)
    _require_artifact_tree(artifact)
    checksums = _read_json(artifact / "checksums.json")
    _require_exact_keys(
        checksums,
        ("schema_version", "run_id", "algorithm", "files", "artifact_set_hash"),
        label="checksums",
    )
    if (
        checksums.get("schema_version") != CODEX_BASELINE_SCHEMA_VERSION
        or checksums.get("algorithm") != "sha256"
    ):
        raise CodexBaselineError("checksums contract identity mismatch")
    files = checksums.get("files")
    if not isinstance(files, dict) or set(files) != set(CHECKSUM_TARGET_FILES):
        raise CodexBaselineError("checksums must cover every other canonical file")
    actual = _file_records(artifact, CHECKSUM_TARGET_FILES)
    if files != actual:
        raise CodexBaselineError("canonical artifact checksum or size mismatch")
    set_hash = _artifact_set_hash(actual)
    if checksums.get("artifact_set_hash") != set_hash:
        raise CodexBaselineError("artifact set hash mismatch")

    findings = _scan_artifact_files(artifact, CHECKSUM_TARGET_FILES)
    findings.extend(_scan_json_strings("checksums.json", checksums))
    if findings:
        raise CodexBaselineError(f"portable artifact security scan failed: {sorted(findings)}")

    run = _read_json(artifact / "run.json")
    manifest = _read_json(artifact / "manifest.json")
    golden_rows = _read_jsonl(artifact / "golden_cases.jsonl")
    prediction_rows = _read_jsonl(artifact / "predictions.jsonl")
    metrics = _read_json(artifact / "metrics.json")
    slices = _read_json(artifact / "slice_report.json")
    error_rows = _read_jsonl(artifact / "errors.jsonl")
    latency = _read_json(artifact / "latency.json")
    security = _read_json(artifact / "security_report.json")
    _require_exact_keys(
        run,
        (
            "schema_version",
            "artifact_version",
            "runner_version",
            "run_id",
            "execution_mode",
            "status",
            "release_posture",
            "dataset_id",
            "dataset_version",
            "package_hash",
            "seed",
            "case_count",
            "case_membership",
            "component_set_hash",
            "production_authority_version",
            "production_authority_digest",
            "runner_identity",
            "golden_authority_digest",
            "config",
            "error_count",
            "zero_result_count",
            "fallback_count",
            "baseline_qualified",
        ),
        label="run",
    )
    _require_exact_keys(
        manifest,
        (
            "schema_version",
            "artifact_version",
            "run_id",
            "dataset",
            "seed",
            "config",
            "components",
            "component_set_hash",
            "production_authority_version",
            "production_authority_digest",
            "runner_identity",
            "golden_authority_digest",
            "metric_denominators",
            "slice_counts",
            "canonical_files",
            "portable_paths_only",
            "baseline_qualified",
        ),
        label="manifest",
    )
    for row in golden_rows:
        _require_exact_keys(
            row,
            (
                "schema_version",
                "run_id",
                "dataset_id",
                "dataset_version",
                "package_hash",
                "case_id",
                "slice",
                "query",
                "eligible_metrics",
                "denominator_contributions",
                "authority",
                "authority_sha256",
            ),
            label="Golden case",
        )
    _require_exact_keys(
        metrics,
        (
            "schema_version",
            "run_id",
            "evaluator",
            "status",
            "eligible_denominators",
            "metrics",
            "baseline_qualified",
        ),
        label="metrics",
    )
    _require_exact_keys(
        slices,
        ("schema_version", "run_id", "slice_count", "slices"),
        label="slice report",
    )
    _require_exact_keys(
        latency,
        ("schema_version", "run_id", "unit", "case_count", "p50", "p95", "max", "cases"),
        label="latency",
    )
    _require_exact_keys(
        security,
        ("schema_version", "run_id", "status", "scanned_files", "checks", "findings"),
        label="security report",
    )
    run_id = run.get("run_id")
    if type(run_id) is not str or not _IDENTIFIER_RE.fullmatch(run_id):
        raise CodexBaselineError("run identity is invalid")
    if any(
        payload.get("run_id") != run_id
        for payload in (manifest, metrics, slices, latency, security, checksums)
    ):
        raise CodexBaselineError("cross-file run identity mismatch")
    if any(row.get("run_id") != run_id for row in [*golden_rows, *prediction_rows, *error_rows]):
        raise CodexBaselineError("JSONL row run identity mismatch")
    if (
        run.get("schema_version") != CODEX_BASELINE_SCHEMA_VERSION
        or manifest.get("schema_version") != CODEX_BASELINE_SCHEMA_VERSION
        or run.get("artifact_version") != CODEX_BASELINE_ARTIFACT_VERSION
        or manifest.get("artifact_version") != CODEX_BASELINE_ARTIFACT_VERSION
        or run.get("runner_version") != CODEX_BASELINE_RUNNER_VERSION
    ):
        raise CodexBaselineError("artifact schema/runner identity mismatch")
    if any(
        payload.get("schema_version") != CODEX_BASELINE_SCHEMA_VERSION
        for payload in (metrics, slices, latency, security, checksums)
    ) or any(
        row.get("schema_version") != CODEX_BASELINE_SCHEMA_VERSION
        for row in [*golden_rows, *prediction_rows, *error_rows]
    ):
        raise CodexBaselineError("cross-file schema identity mismatch")
    execution_mode = run.get("execution_mode")
    if execution_mode not in {"released", "smoke"}:
        raise CodexBaselineError("artifact execution_mode is invalid")
    if (
        run.get("baseline_qualified") is not False
        or manifest.get("baseline_qualified") is not False
    ):
        raise CodexBaselineError("X-B0 artifact cannot self-qualify")
    expected_run_state = (
        ("COMPLETED", "NON_QUALIFIED")
        if execution_mode == "released"
        else ("SMOKE_COMPLETED", "SMOKE_NON_QUALIFIED")
    )
    if (run.get("status"), run.get("release_posture")) != expected_run_state:
        raise CodexBaselineError("run completion/release posture mismatch")
    if manifest.get("canonical_files") != list(CANONICAL_ARTIFACT_FILES):
        raise CodexBaselineError("manifest canonical file membership mismatch")
    if manifest.get("portable_paths_only") is not True:
        raise CodexBaselineError("manifest must assert portable paths only")
    try:
        config = CodexBaselineConfig.model_validate(manifest.get("config"))
        run_config = CodexBaselineConfig.model_validate(run.get("config"))
    except ValueError as exc:
        raise CodexBaselineError("artifact flat configuration is invalid") from exc
    if config != run_config or config != CodexBaselineConfig():
        raise CodexBaselineError("artifact flat configuration mismatch")
    historical_source_artifact = (
        run_id == CODEX_XB0_SOURCE_RUN_ID and set_hash == CODEX_XB0_SOURCE_ARTIFACT_SET_HASH
    )
    components = _parse_components(manifest.get("components"))
    expected_components = (
        _frozen_historical_production_authority()
        if historical_source_artifact
        else component_identities_v1()
    )
    if components != expected_components:
        raise CodexBaselineError("artifact component identities differ from exact production")
    component_hash = _component_set_hash(components)
    expected_authority_version = (
        CODEX_HISTORICAL_PRODUCTION_AUTHORITY_VERSION
        if historical_source_artifact
        else CODEX_PRODUCTION_AUTHORITY_VERSION
    )
    expected_authority_digest = (
        CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST
        if historical_source_artifact
        else CODEX_PRODUCTION_AUTHORITY_DIGEST
    )
    if (
        component_hash != expected_authority_digest
        or manifest.get("component_set_hash") != component_hash
        or run.get("component_set_hash") != component_hash
        or manifest.get("production_authority_version") != expected_authority_version
        or run.get("production_authority_version") != expected_authority_version
        or manifest.get("production_authority_digest") != expected_authority_digest
        or run.get("production_authority_digest") != expected_authority_digest
    ):
        raise CodexBaselineError("cross-file component identity hash mismatch")
    try:
        manifest_runner = CodexComponentIdentity.model_validate(manifest.get("runner_identity"))
        run_runner = CodexComponentIdentity.model_validate(run.get("runner_identity"))
    except ValueError as exc:
        raise CodexBaselineError("artifact runner identity is invalid") from exc
    expected_runner_identity = (
        _fixed_source_runner_identity() if historical_source_artifact else _runner_module_identity()
    )
    if manifest_runner != run_runner or manifest_runner != expected_runner_identity:
        raise CodexBaselineError("artifact runner implementation identity mismatch")

    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict):
        raise CodexBaselineError("manifest dataset contract is invalid")
    _require_exact_keys(
        dataset,
        (
            "dataset_id",
            "dataset_version",
            "schema_version",
            "package_hash",
            "case_count",
            "case_membership",
            "authority_hash",
            "dataset_authority_digest",
            "fixture_thread_ids",
            "fixture_item_ids",
            "fixture_item_locators",
        ),
        label="manifest dataset",
    )
    membership = _string_list(dataset.get("case_membership"), label="case_membership")
    golden_ids = [str(row.get("case_id")) for row in golden_rows]
    prediction_ids = [str(row.get("case_id")) for row in prediction_rows]
    if (
        membership != golden_ids
        or membership != prediction_ids
        or membership != run.get("case_membership")
        or len(membership) != len(set(membership))
        or dataset.get("case_count") != len(membership)
        or run.get("case_count") != len(membership)
        or not membership
    ):
        raise CodexBaselineError("case membership/denominator mismatch")
    if execution_mode == "released" and (
        dataset.get("dataset_id") != CODEX_XB0_GOLDEN_DATASET_ID
        or dataset.get("dataset_version") != CODEX_XB0_GOLDEN_DATASET_VERSION
        or dataset.get("schema_version") != CODEX_XB0_GOLDEN_SCHEMA_VERSION
        or dataset.get("package_hash") != CODEX_XB0_GOLDEN_PACKAGE_HASH
        or dataset.get("authority_hash") != CODEX_XB0_GOLDEN_AUTHORITY_DIGEST
        or dataset.get("dataset_authority_digest") != CODEX_XB0_GOLDEN_DATASET_DIGEST
        or tuple(membership) != CODEX_XB0_CASE_MEMBERSHIP
        or len(membership) != CODEX_XB0_CASE_COUNT
        or run.get("golden_authority_digest") != CODEX_XB0_GOLDEN_AUTHORITY_DIGEST
        or manifest.get("golden_authority_digest") != CODEX_XB0_GOLDEN_AUTHORITY_DIGEST
    ):
        raise CodexBaselineError("released artifact lacks exact 45-case identity")
    if execution_mode == "smoke" and (
        dataset.get("dataset_id") != "codex-smoke-v1"
        or dataset.get("schema_version") != "codex-smoke-foundation-v1"
        or dataset.get("authority_hash") is not None
        or dataset.get("dataset_authority_digest") is not None
        or dataset.get("fixture_thread_ids") != []
        or dataset.get("fixture_item_ids") != []
        or dataset.get("fixture_item_locators") != []
        or run.get("golden_authority_digest") is not None
        or manifest.get("golden_authority_digest") is not None
        or len(membership) > 8
    ):
        raise CodexBaselineError("smoke artifact identity is invalid")
    for key in ("dataset_id", "dataset_version", "package_hash"):
        expected = dataset.get(key)
        if run.get(key) != expected or any(row.get(key) != expected for row in golden_rows):
            raise CodexBaselineError(f"cross-file dataset {key} mismatch")
        if any(row.get(key) != expected for row in prediction_rows):
            raise CodexBaselineError(f"prediction dataset {key} mismatch")
    if (
        manifest.get("seed") != run.get("seed")
        or type(run.get("seed")) is not int
        or not 0 <= int(run["seed"]) <= 9_223_372_036_854_775_807
        or not isinstance(dataset.get("package_hash"), str)
        or not _SHA256_RE.fullmatch(str(dataset["package_hash"]))
    ):
        raise CodexBaselineError("cross-file fixed seed mismatch")
    expected_run_id = (
        "codex-xb0-"
        + hashlib.sha256(
            _canonical_json(
                {
                    "schema_version": CODEX_BASELINE_SCHEMA_VERSION,
                    "execution_mode": execution_mode,
                    "dataset_id": dataset["dataset_id"],
                    "dataset_version": dataset["dataset_version"],
                    "package_hash": dataset["package_hash"],
                    "seed": run["seed"],
                    "case_membership": membership,
                    "component_set_hash": component_hash,
                    "production_authority_digest": expected_authority_digest,
                    "runner_identity": manifest_runner.model_dump(mode="json", exclude_none=True),
                    "golden_authority_digest": manifest.get("golden_authority_digest"),
                    "config": config.model_dump(mode="json"),
                }
            )
        ).hexdigest()[:32]
    )
    if run_id != expected_run_id:
        raise CodexBaselineError("run identity does not bind dataset/config/seed/components")

    released_dataset = (
        _rebuild_released_dataset_authority(dataset, golden_rows)
        if execution_mode == "released"
        else None
    )
    if execution_mode == "smoke" and any(
        row.get("authority") is not None or row.get("authority_sha256") is not None
        for row in golden_rows
    ):
        raise CodexBaselineError("smoke artifact cannot carry released Golden authority")

    metric_names = {metric.value for metric in CodexMetric}
    denominator_totals = {name: 0 for name in metric_names}
    slice_counts: Counter[str] = Counter()
    for row in golden_rows:
        if (
            not isinstance(row.get("case_id"), str)
            or not _IDENTIFIER_RE.fullmatch(str(row["case_id"]))
            or not isinstance(row.get("query"), str)
        ):
            raise CodexBaselineError("Golden case identity/query is invalid")
        contributions = _integer_map(
            row.get("denominator_contributions"),
            label="denominator_contributions",
        )
        if set(contributions) != metric_names:
            raise CodexBaselineError("per-case metric denominator membership mismatch")
        for name, value in contributions.items():
            denominator_totals[name] += value
        eligible = set(_string_list(row.get("eligible_metrics"), label="eligible_metrics"))
        if not eligible <= metric_names:
            raise CodexBaselineError("unknown per-case eligible metric")
        if execution_mode == "smoke" and eligible:
            raise CodexBaselineError("smoke artifact claims released metric eligibility")
        slice_value = row.get("slice")
        if slice_value not in {item.value for item in CodexGoldenSlice}:
            raise CodexBaselineError("unknown Golden slice")
        slice_counts[str(slice_value)] += 1
    manifest_denominators = _integer_map(
        manifest.get("metric_denominators"),
        label="metric_denominators",
    )
    metrics_denominators = _integer_map(
        metrics.get("eligible_denominators"),
        label="metrics eligible_denominators",
    )
    if (
        manifest_denominators != denominator_totals
        or metrics_denominators != denominator_totals
        or manifest.get("slice_counts") != dict(sorted(slice_counts.items()))
    ):
        raise CodexBaselineError("cross-file denominator or slice mismatch")

    parsed_predictions: list[CodexPredictionRecord] = []
    try:
        parsed_predictions = [CodexPredictionRecord.model_validate(row) for row in prediction_rows]
        parsed_errors = [CodexBaselineErrorRecord.model_validate(row) for row in error_rows]
    except ValueError as exc:
        raise CodexBaselineError("prediction/error artifact contract is invalid") from exc
    error_by_case = {item.case_id: item for item in parsed_errors}
    if len(error_by_case) != len(parsed_errors):
        raise CodexBaselineError("duplicate case error records are forbidden")
    for prediction in parsed_predictions:
        contributions = {
            metric.value: prediction.denominator_contributions[metric] for metric in CodexMetric
        }
        golden = golden_rows[membership.index(prediction.case_id)]
        if (
            contributions != golden.get("denominator_contributions")
            or prediction.query != golden.get("query")
            or [metric.value for metric in prediction.eligible_metrics]
            != golden.get("eligible_metrics")
        ):
            raise CodexBaselineError("prediction denominator differs from Golden membership")
        has_error = prediction.trace.outcome in {"error", "unavailable"}
        if has_error != (prediction.case_id in error_by_case):
            raise CodexBaselineError("prediction/error cross-file outcome mismatch")
        if has_error:
            error = error_by_case[prediction.case_id]
            expected_summary = (
                "production response unavailable"
                if prediction.trace.outcome == "unavailable"
                else "production retriever raised an exception"
            )
            expected_type = (
                "CodexBaselineUnavailableError"
                if prediction.trace.outcome == "unavailable"
                else prediction.trace.error_type
            )
            if (
                error.outcome != prediction.trace.outcome
                or error.safe_summary != expected_summary
                or error.error_type != expected_type
                or error.detail_sha256 != prediction.trace.error_detail_sha256
                or error.fallback_used
            ):
                raise CodexBaselineError("error record differs from prediction trace")

    if latency.get("case_count") != len(membership):
        raise CodexBaselineError("latency denominator differs from case membership")
    latency_rows = latency.get("cases")
    if (
        not isinstance(latency_rows, list)
        or [item.get("case_id") for item in latency_rows if isinstance(item, dict)] != membership
    ):
        raise CodexBaselineError("latency case membership mismatch")
    for prediction, item in zip(parsed_predictions, latency_rows, strict=True):
        _require_exact_keys(
            item,
            (
                "case_id",
                "latency_ms",
                "lexical_candidates",
                "dense_candidates",
                "dense_matches",
                "zero_result",
                "outcome",
                "fallback_used",
            ),
            label="latency case",
        )
        if (
            not isinstance(item, dict)
            or item.get("latency_ms") != prediction.trace.latency_ms
            or item.get("lexical_candidates") != prediction.trace.lexical_candidates
            or item.get("dense_candidates") != prediction.trace.dense_candidates
            or item.get("dense_matches") != prediction.trace.dense_matches
            or item.get("zero_result") != prediction.trace.zero_result
            or item.get("outcome") != prediction.trace.outcome
            or item.get("fallback_used") is not False
        ):
            raise CodexBaselineError("latency/trace cross-file mismatch")
    latency_values = sorted(prediction.trace.latency_ms for prediction in parsed_predictions)

    def expected_percentile(fraction: float) -> float:
        index = min(
            len(latency_values) - 1,
            max(0, math.ceil(len(latency_values) * fraction) - 1),
        )
        return float(latency_values[index])

    if (
        latency.get("unit") != "milliseconds"
        or latency.get("p50") != expected_percentile(0.50)
        or latency.get("p95") != expected_percentile(0.95)
        or latency.get("max") != float(latency_values[-1])
    ):
        raise CodexBaselineError("latency aggregates differ from case traces")
    if run.get("error_count") != len(error_rows):
        raise CodexBaselineError("run error denominator mismatch")
    if run.get("zero_result_count") != sum(
        prediction.trace.zero_result for prediction in parsed_predictions
    ):
        raise CodexBaselineError("run zero-result denominator mismatch")
    if run.get("fallback_count") != 0 or any(
        prediction.trace.fallback_used for prediction in parsed_predictions
    ):
        raise CodexBaselineError("X-B0 fallback must remain disabled")

    if released_dataset is not None:
        reviewed_rows = _reviewed_rows_from_predictions(
            released_dataset,
            parsed_predictions,
        )
        recomputed_metrics = evaluate_reviewed_codex_retrieval(
            released_dataset,
            reviewed_rows,
            case_membership=CODEX_XB0_CASE_MEMBERSHIP,
        )
        released_denominator_by_case = {
            case.case_id: _case_denominator_contributions(case) for case in released_dataset.cases
        }
        recomputed_metric_payload = _metric_payload(
            run_id=run_id,
            execution_mode="released",
            metric_results=recomputed_metrics,
            metric_denominators=_sum_denominators(released_denominator_by_case.values()),
        )
        if metrics != recomputed_metric_payload:
            raise CodexBaselineError(
                "released metrics differ from exact X0 evaluator recomputation"
            )
        recomputed_contributions = _metric_case_contributions(
            released_dataset,
            reviewed_rows,
        )
        recomputed_slices = _slice_report(
            run_id=run_id,
            cases=tuple(_released_case(case) for case in released_dataset.cases),
            denominator_by_case=released_denominator_by_case,
            predictions=parsed_predictions,
            contributions=recomputed_contributions,
        )
        if slices != recomputed_slices:
            raise CodexBaselineError(
                "released slice/treatment metrics differ from authority recomputation"
            )

    metric_rows = metrics.get("metrics")
    if not isinstance(metric_rows, list):
        raise CodexBaselineError("metrics payload is invalid")
    if execution_mode == "smoke":
        if (
            metrics.get("status") != "SMOKE_UNAVAILABLE"
            or metrics.get("evaluator") != "NOT_RUN_SMOKE_NON_QUALIFIED"
            or metrics.get("baseline_qualified") is not False
            or metric_rows
        ):
            raise CodexBaselineError("smoke numbers cannot masquerade as baseline metrics")
    else:
        if (
            metrics.get("status") != "AVAILABLE"
            or metrics.get("evaluator") != "evaluate_reviewed_codex_retrieval"
            or metrics.get("baseline_qualified") is not False
        ):
            raise CodexBaselineError("released evaluator identity/status mismatch")
        try:
            parsed_metrics = [MetricResult.model_validate(item) for item in metric_rows]
        except ValueError as exc:
            raise CodexBaselineError("released metric contract is invalid") from exc
        if {item.name for item in parsed_metrics} != metric_names:
            raise CodexBaselineError("released metric membership mismatch")
        for item in parsed_metrics:
            if item.denominator != denominator_totals[item.name]:
                raise CodexBaselineError("released metric denominator mismatch")

    slice_rows = slices.get("slices")
    if (
        slices.get("slice_count") != len(CodexGoldenSlice)
        or not isinstance(slice_rows, list)
        or len(slice_rows) != len(CodexGoldenSlice)
    ):
        raise CodexBaselineError("slice report membership mismatch")
    golden_by_slice = {
        slice_name.value: [
            str(row["case_id"]) for row in golden_rows if row.get("slice") == slice_name.value
        ]
        for slice_name in CodexGoldenSlice
    }
    prediction_by_case = {item.case_id: item for item in parsed_predictions}
    aggregate_slice_numerators = {metric.value: 0.0 for metric in CodexMetric}
    aggregate_slice_denominators = {metric.value: 0 for metric in CodexMetric}
    for expected_slice, item in zip(CodexGoldenSlice, slice_rows, strict=True):
        if not isinstance(item, dict):
            raise CodexBaselineError("slice row must be an object")
        _require_exact_keys(
            item,
            (
                "slice",
                "case_ids",
                "case_count",
                "eligible_denominators",
                "retrieval_outcomes",
                "metrics",
            ),
            label="slice row",
        )
        members = golden_by_slice[expected_slice.value]
        slice_denominators = {name: 0 for name in metric_names}
        for case_id in members:
            row = golden_rows[membership.index(case_id)]
            for name, value in row["denominator_contributions"].items():
                slice_denominators[name] += value
        expected_outcomes = dict(
            Counter(prediction_by_case[case_id].trace.outcome for case_id in members)
        )
        if (
            item.get("slice") != expected_slice.value
            or item.get("case_ids") != members
            or item.get("case_count") != len(members)
            or item.get("eligible_denominators") != slice_denominators
            or item.get("retrieval_outcomes") != expected_outcomes
        ):
            raise CodexBaselineError("slice report membership/denominator mismatch")
        slice_metrics = item.get("metrics")
        if not isinstance(slice_metrics, list):
            raise CodexBaselineError("slice metrics must be a list")
        if execution_mode == "smoke":
            if slice_metrics:
                raise CodexBaselineError("smoke slice numbers cannot masquerade as metrics")
            continue
        if [row.get("name") for row in slice_metrics if isinstance(row, dict)] != [
            metric.value for metric in CodexMetric
        ]:
            raise CodexBaselineError("slice metric membership mismatch")
        for row in slice_metrics:
            if not isinstance(row, dict):
                raise CodexBaselineError("slice metric row must be an object")
            _require_exact_keys(
                row,
                ("name", "numerator", "denominator", "value", "status"),
                label="slice metric",
            )
            name = str(row["name"])
            denominator = slice_denominators[name]
            numerator = row["numerator"]
            value = row["value"]
            if denominator == 0:
                if (
                    row["status"] != "UNAVAILABLE"
                    or numerator is not None
                    or value is not None
                    or row["denominator"] != 0
                ):
                    raise CodexBaselineError("unavailable slice metric is inconsistent")
            elif (
                row["status"] != "AVAILABLE"
                or row["denominator"] != denominator
                or not isinstance(numerator, (int, float))
                or isinstance(numerator, bool)
                or not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(numerator))
                or not math.isfinite(float(value))
                or abs(float(value) - float(numerator) / denominator) > 1e-12
            ):
                raise CodexBaselineError("available slice metric is inconsistent")
            aggregate_slice_denominators[name] += denominator
            aggregate_slice_numerators[name] += float(numerator or 0.0)
    if execution_mode == "released":
        overall_by_name = {item.name: item for item in parsed_metrics}
        for name in metric_names:
            overall = overall_by_name[name]
            if (
                aggregate_slice_denominators[name] != denominator_totals[name]
                or overall.numerator is None
                or abs(float(overall.numerator) - aggregate_slice_numerators[name]) > 1e-12
            ):
                raise CodexBaselineError("slice metrics differ from X0 evaluator totals")
    expected_security_checks = {
        "absolute_paths",
        "credentials",
        "personal_or_payment_data",
        "temporary_database_or_raw_fixture",
        "sqlite_sidecars",
        "compiled_python",
        "nonfinite_numbers",
        "undeclared_files",
    }
    if (
        security.get("status") != "clean"
        or security.get("findings") != []
        or security.get("scanned_files") != list(_SECURITY_INPUT_FILES)
        or not isinstance(security.get("checks"), dict)
        or set(security["checks"]) != expected_security_checks
        or set(security["checks"].values()) != {"clean"}
    ):
        raise CodexBaselineError("security report is not clean and canonical")
    expected_status = (
        "VERIFIED_NON_QUALIFIED" if execution_mode == "released" else "VERIFIED_SMOKE_NON_QUALIFIED"
    )
    return CodexBaselineVerification(
        run_id=run_id,
        status=expected_status,
        execution_mode=execution_mode,
        artifact_set_hash=set_hash,
        case_count=len(membership),
    )


class _ValidatedCorrectionPaths(_FrozenContract):
    source_run_dir: Path
    isolated_root: Path
    output_dir: Path
    staging_dir: Path
    persistent_artifact: bool


def _validate_correction_paths(
    request: CodexBaselineCorrectionRequest | CodexBaselineCorrectionPublicationRequest,
) -> _ValidatedCorrectionPaths:
    source_run_dir = _require_existing_nonsymlink(
        request.source_run_dir,
        directory=True,
        label="source_run_dir",
    )
    if source_run_dir.name != CODEX_XB0_SOURCE_RUN_DIRECTORY_ID:
        raise CodexBaselineError("correction source directory identity mismatch")
    isolated_root = _require_existing_nonsymlink(
        request.isolated_root,
        directory=True,
        label="isolated_root",
    )
    temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if isolated_root == temporary_root or not _is_relative_to(isolated_root, temporary_root):
        raise CodexBaselineError("correction isolated_root must be below system temporary root")
    if type(request) is CodexBaselineCorrectionRequest:
        output_dir = request.output_dir.resolve(strict=False)
        if output_dir == isolated_root or not _is_relative_to(output_dir, isolated_root):
            raise CodexBaselineError("correction output_dir must be below isolated_root")
        _require_no_symlink_ancestors(output_dir, isolated_root, label="output_dir")
        staging_dir = output_dir.with_name(f".{output_dir.name}.correction-staging")
        persistent_artifact = False
    elif type(request) is CodexBaselineCorrectionPublicationRequest:
        corrections_root = request.corrections_root.resolve(strict=False)
        if (
            corrections_root.name != "corrections"
            or corrections_root.parent.name != "codex"
            or corrections_root.parent.parent.name != "evals"
        ):
            raise CodexBaselineError("corrections_root must be evals/codex/corrections")
        corrections_parent = _require_existing_nonsymlink(
            corrections_root.parent,
            directory=True,
            label="corrections_root parent",
        )
        _require_no_symlink_ancestors(
            corrections_root,
            corrections_parent,
            label="corrections_root",
        )
        if corrections_root.exists():
            _require_existing_nonsymlink(
                corrections_root,
                directory=True,
                label="corrections_root",
            )
        output_dir = corrections_root / _correction_directory_id()
        staging_dir = isolated_root / f".{output_dir.name}.correction-staging"
        persistent_artifact = True
    else:
        raise CodexBaselineError("correction request must be an exact frozen contract")
    if output_dir.exists():
        raise CodexBaselineError("correction output_dir must be new")
    if staging_dir.exists():
        raise CodexBaselineError("correction staging_dir must be new")
    _require_no_symlink_ancestors(
        staging_dir,
        isolated_root,
        label="staging_dir",
    )
    return _ValidatedCorrectionPaths(
        source_run_dir=source_run_dir,
        isolated_root=isolated_root,
        output_dir=output_dir,
        staging_dir=staging_dir,
        persistent_artifact=persistent_artifact,
    )


def _correction_id() -> str:
    return (
        "codex-xb0-correction-"
        + hashlib.sha256(
            _canonical_json(
                {
                    "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
                    "source_run_id": CODEX_XB0_SOURCE_RUN_ID,
                    "source_artifact_set_hash": CODEX_XB0_SOURCE_ARTIFACT_SET_HASH,
                    "source_files": _FIXED_SOURCE_FILE_RECORDS,
                    "golden_authority_digest": CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
                    "component_authority_digest": (CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST),
                    "evaluator_version": CODEX_CORRECTION_EVALUATOR_VERSION,
                    "evaluator_digest": CODEX_CORRECTION_EVALUATOR_DIGEST,
                    "case_membership": CODEX_XB0_CASE_MEMBERSHIP,
                }
            )
        ).hexdigest()[:32]
    )


def _correction_directory_id() -> str:
    return _correction_id().removeprefix("codex-xb0-correction-")


def _correction_uri() -> str:
    return CODEX_CORRECTION_URI_PREFIX + _correction_directory_id()


def _parse_correction_predictions(
    prediction_rows: Sequence[Mapping[str, object]],
) -> tuple[CodexPredictionRecord, ...]:
    try:
        predictions = tuple(CodexPredictionRecord.model_validate(row) for row in prediction_rows)
    except ValueError as exc:
        raise CodexBaselineError("correction source predictions are invalid") from exc
    if tuple(item.case_id for item in predictions) != CODEX_XB0_CASE_MEMBERSHIP or any(
        item.run_id != CODEX_XB0_SOURCE_RUN_ID
        or item.dataset_id != CODEX_XB0_GOLDEN_DATASET_ID
        or item.dataset_version != CODEX_XB0_GOLDEN_DATASET_VERSION
        or item.package_hash != CODEX_XB0_GOLDEN_PACKAGE_HASH
        for item in predictions
    ):
        raise CodexBaselineError("correction prediction source authority mismatch")
    return predictions


def _derive_complete_correction_evidence(
    dataset: GoldenDataset,
    predictions: Sequence[CodexPredictionRecord],
) -> tuple[
    dict[str, tuple[ReviewedRetrievalRow, ...]],
    dict[str, CodexPredictionRecord],
    int,
    int,
]:
    if (
        dataset.case_membership != CODEX_XB0_CASE_MEMBERSHIP
        or tuple(item.case_id for item in predictions) != CODEX_XB0_CASE_MEMBERSHIP
    ):
        raise CodexBaselineError("correction evidence membership mismatch")
    prediction_by_case = {item.case_id: item for item in predictions}
    fixture_locators = dataset.fixture_locator_by_item
    reviewed_by_case: dict[str, tuple[ReviewedRetrievalRow, ...]] = {}
    reviewed_item_count = 0
    rank_gap_case_count = 0
    for case in dataset.cases:
        prediction = prediction_by_case[case.case_id]
        judgments = {item.item_id: item for item in case.item_judgments}
        reviewed: list[ReviewedRetrievalRow] = []
        for result in prediction.results:
            judgment = judgments.get(result.item_id)
            if judgment is None:
                continue
            try:
                row = ReviewedRetrievalRow(
                    dataset_id=CODEX_XB0_GOLDEN_DATASET_ID,
                    dataset_version=CODEX_XB0_GOLDEN_DATASET_VERSION,
                    package_hash=CODEX_XB0_GOLDEN_PACKAGE_HASH,
                    case_id=case.case_id,
                    rank=result.rank,
                    thread_id=result.thread_id,
                    item_id=result.item_id,
                    locator=result.locator,
                    reviewed=True,
                )
            except ValueError as exc:
                raise CodexBaselineError(
                    "correction cannot reconstruct a strict reviewed result"
                ) from exc
            if (
                row.thread_id != judgment.thread_id
                or row.locator != judgment.locator
                or fixture_locators.get(row.item_id) != row.locator
            ):
                raise CodexBaselineError(
                    "correction prediction differs from frozen treatment authority"
                )
            reviewed.append(row)
        ranks = [item.rank for item in reviewed]
        if ranks and ranks != list(range(1, len(ranks) + 1)):
            rank_gap_case_count += 1
        reviewed_by_case[case.case_id] = tuple(reviewed)
        reviewed_item_count += len(reviewed)
    return (
        reviewed_by_case,
        prediction_by_case,
        reviewed_item_count,
        rank_gap_case_count,
    )


def _correction_case_ledger(
    dataset: GoldenDataset,
    predictions: Sequence[CodexPredictionRecord],
) -> tuple[
    dict[str, dict[str, int]],
    dict[str, dict[str, float]],
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, str],
    int,
    int,
]:
    (
        reviewed_by_case,
        prediction_by_case,
        reviewed_item_count,
        rank_gap_case_count,
    ) = _derive_complete_correction_evidence(dataset, predictions)
    denominators_by_case: dict[str, dict[str, int]] = {}
    numerators_by_case: dict[str, dict[str, float]] = {}
    hard_negative_cases: list[dict[str, object]] = []
    refusal_cases: list[dict[str, object]] = []
    outcomes: dict[str, str] = {}

    for case in dataset.cases:
        prediction = prediction_by_case[case.case_id]
        rows = reviewed_by_case[case.case_id]
        rows5 = tuple(row for row in rows if row.rank <= 5)
        rows10 = tuple(row for row in rows if row.rank <= 10)
        row_ids = {row.item_id for row in rows10}
        judgments = {item.item_id: item for item in case.item_judgments}
        base_denominators = _case_denominator_contributions(case)
        denominators = {metric.value: int(base_denominators[metric]) for metric in CodexMetric}
        denominators["hard_negative_hit_rate_at_10"] = len(case.hard_negatives)
        if case.unanswerable != case.refusal_expected:
            raise CodexBaselineError("correction refusal eligibility authority is inconsistent")
        refusal_eligible = case.unanswerable and case.refusal_expected
        denominators["correct_zero_rate"] = int(refusal_eligible)
        denominators["refusal_failure_rate"] = int(refusal_eligible)
        numerators = {name: 0.0 for name in _CORRECTION_METRIC_NAMES}

        expected_threads = {item.thread_id for item in case.expected_threads}
        if denominators[CodexMetric.THREAD_RECALL_AT_5.value]:
            numerators[CodexMetric.THREAD_RECALL_AT_5.value] = float(
                bool(expected_threads & {row.thread_id for row in rows5})
            )
        if denominators[CodexMetric.EPISODE_RECALL_AT_5.value]:
            retrieved_episodes = {
                judgments[row.item_id].episode_id
                for row in rows5
                if judgments[row.item_id].episode_id is not None
            }
            expected_episodes = {
                item.episode_id for item in case.expected_threads if item.episode_id is not None
            }
            numerators[CodexMetric.EPISODE_RECALL_AT_5.value] = float(
                bool(expected_episodes & retrieved_episodes)
            )
        expected_items = {item.item_id for item in case.expected_items}
        if denominators[CodexMetric.ITEM_RECALL_AT_10.value]:
            numerators[CodexMetric.ITEM_RECALL_AT_10.value] = float(bool(expected_items & row_ids))
        if denominators[CodexMetric.MRR_AT_10.value]:
            first = next(
                (row.rank for row in rows10 if row.item_id in expected_items),
                None,
            )
            numerators[CodexMetric.MRR_AT_10.value] = 1.0 / first if first else 0.0
        if denominators[CodexMetric.GOAL_RECALL_AT_10.value]:
            expected_goals = {
                item.item_id for item in case.expected_items if item.item_type == "UserGoal"
            }
            numerators[CodexMetric.GOAL_RECALL_AT_10.value] = float(bool(expected_goals & row_ids))
        if denominators[CodexMetric.DECISION_RECALL_AT_10.value]:
            numerators[CodexMetric.DECISION_RECALL_AT_10.value] = float(
                bool(expected_items & row_ids)
            )

        old_harmful = [
            item
            for item in case.hard_negatives
            if item.old_attempt
            and CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10 in case.eligible_metrics
        ]
        numerators[CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10.value] = float(
            sum(item.item_id in row_ids for item in old_harmful)
        )
        if denominators[CodexMetric.EVENT_ORDER_ACCURACY_AT_10.value]:
            ordered = sorted(
                (truth for truth in case.event_truth if truth.order is not None),
                key=lambda truth: truth.order or 0,
            )
            rank_by_id = {row.item_id: row.rank for row in rows10}
            ranks = [rank_by_id[item.item_id] for item in ordered if item.item_id in rank_by_id]
            numerators[CodexMetric.EVENT_ORDER_ACCURACY_AT_10.value] = float(
                len(ranks) == len(ordered) and ranks == sorted(ranks)
            )

        calls_by_id: dict[str, list[Any]] = defaultdict(list)
        for truth in case.event_truth:
            if truth.call_id:
                calls_by_id[truth.call_id].append(truth)
        numerators[CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10.value] = float(
            sum(
                all(
                    truth.item_id in row_ids
                    for truth in truths
                    if truth.kind in {"tool_call", "tool_result"}
                )
                for truths in calls_by_id.values()
                if {"tool_call", "tool_result"} <= {truth.kind for truth in truths}
            )
        )
        numerators[CodexMetric.PATCH_ACCURACY_AT_10.value] = float(
            sum(
                truth.item_id in row_ids
                and judgments[truth.item_id].patch_applied == truth.patch_applied
                for truth in case.event_truth
                if truth.kind in {"patch", "file_change"} and truth.patch_applied is not None
            )
        )
        numerators[CodexMetric.VALIDATION_ACCURACY_AT_10.value] = float(
            sum(
                truth.item_id in row_ids
                and judgments[truth.item_id].validation_status
                == ("passed" if truth.exit_code == 0 else "failed")
                for truth in case.event_truth
                if truth.kind == "validation"
            )
        )
        numerators[CodexMetric.FALSE_VALIDATED_RATE_AT_10.value] = float(
            sum(
                judgment.false_validated is True and judgment.item_id in row_ids
                for judgment in case.item_judgments
                if judgment.false_validation_category is not None
            )
        )
        numerators[CodexMetric.OUTCOME_ACCURACY_AT_10.value] = float(
            sum(
                truth.item_id in row_ids and judgments[truth.item_id].outcome_correct is True
                for truth in case.event_truth
                if truth.kind == "outcome"
            )
        )

        seen: set[tuple[str, str]] = set()
        duplicated = False
        for result in prediction.results:
            identity = (result.thread_id, result.item_id)
            duplicated = duplicated or identity in seen
            seen.add(identity)
        numerators[CodexMetric.CONTEXT_DUPLICATE_RATE_AT_10.value] = float(duplicated)
        numerators[CodexMetric.CONTEXT_NOISE_RATE_AT_10.value] = float(
            any(judgments[row.item_id].context_noise for row in rows10)
        )

        result_rank_by_id = {item.item_id: item.rank for item in prediction.results}
        for negative in case.hard_negatives:
            rank = result_rank_by_id.get(negative.item_id)
            hit = rank is not None and rank <= CODEX_BASELINE_TOP_K
            hard_negative_cases.append(
                {
                    "case_id": case.case_id,
                    "route": negative.reason.value,
                    "thread_id": negative.thread_id,
                    "item_id": negative.item_id,
                    "old_attempt": negative.old_attempt,
                    "hit": hit,
                    "rank": rank if hit else None,
                }
            )
        numerators["hard_negative_hit_rate_at_10"] = float(
            sum(
                result_rank_by_id.get(negative.item_id, CODEX_BASELINE_TOP_K + 1)
                <= CODEX_BASELINE_TOP_K
                for negative in case.hard_negatives
            )
        )

        actual_result_count = len(prediction.results)
        outcomes[case.case_id] = "retrieved" if actual_result_count else "zero_result"
        if refusal_eligible:
            correct_zero = actual_result_count == 0
            refusal_failure = not correct_zero
            numerators["correct_zero_rate"] = float(correct_zero)
            numerators["refusal_failure_rate"] = float(refusal_failure)
            hard_negative = case.hard_negatives[0]
            hard_rank = result_rank_by_id.get(hard_negative.item_id)
            refusal_cases.append(
                {
                    "case_id": case.case_id,
                    "result_count": actual_result_count,
                    "correct_zero": correct_zero,
                    "refusal_failure": refusal_failure,
                    "hard_negative_route": hard_negative.reason.value,
                    "hard_negative_hit": hard_rank is not None,
                    "hard_negative_rank": hard_rank,
                }
            )
        denominators_by_case[case.case_id] = denominators
        numerators_by_case[case.case_id] = numerators

    if tuple(item["case_id"] for item in refusal_cases) != CODEX_XB0_CASE_MEMBERSHIP[-5:]:
        raise CodexBaselineError("correction refusal membership is not the fixed five-case truth")
    return (
        denominators_by_case,
        numerators_by_case,
        hard_negative_cases,
        refusal_cases,
        outcomes,
        reviewed_item_count,
        rank_gap_case_count,
    )


def _correction_metric_row(
    name: str,
    numerator: float,
    denominator: int,
) -> dict[str, object]:
    unit = "mean_reciprocal_rank" if name == CodexMetric.MRR_AT_10.value else "ratio"
    if denominator == 0:
        return {
            "name": name,
            "status": "UNAVAILABLE",
            "numerator": None,
            "denominator": 0,
            "value": None,
            "unit": unit,
            "reason": "no eligible frozen labels",
        }
    normalized_numerator: int | float = numerator
    if name != CodexMetric.MRR_AT_10.value and numerator.is_integer():
        normalized_numerator = int(numerator)
    return {
        "name": name,
        "status": "AVAILABLE",
        "numerator": normalized_numerator,
        "denominator": denominator,
        "value": float(numerator / denominator),
        "unit": unit,
        "reason": None,
    }


def _compute_correction_payloads(
    dataset: GoldenDataset,
    predictions: Sequence[CodexPredictionRecord],
    *,
    correction_id: str,
) -> dict[str, object]:
    (
        denominators_by_case,
        numerators_by_case,
        hard_negative_cases,
        refusal_cases,
        outcomes,
        reviewed_item_count,
        rank_gap_case_count,
    ) = _correction_case_ledger(dataset, predictions)
    overall_denominators = {
        name: sum(items[name] for items in denominators_by_case.values())
        for name in _CORRECTION_METRIC_NAMES
    }
    overall_numerators = {
        name: sum(items[name] for items in numerators_by_case.values())
        for name in _CORRECTION_METRIC_NAMES
    }
    overall_metrics = [
        _correction_metric_row(
            name,
            overall_numerators[name],
            overall_denominators[name],
        )
        for name in _CORRECTION_METRIC_NAMES
    ]
    corrected_metrics = {
        "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
        "correction_id": correction_id,
        "evaluator_version": CODEX_CORRECTION_EVALUATOR_VERSION,
        "evaluator_digest": CODEX_CORRECTION_EVALUATOR_DIGEST,
        "status": "AVAILABLE",
        "released_case_count": CODEX_XB0_CASE_COUNT,
        "evaluated_case_count": len(predictions),
        "reviewed_evidence_count": reviewed_item_count,
        "reviewed_rank_gap_case_count": rank_gap_case_count,
        "metrics": overall_metrics,
        "baseline_qualified": False,
    }

    slice_rows: list[dict[str, object]] = []
    for slice_name in CodexGoldenSlice:
        members = [case.case_id for case in dataset.cases if case.slice == slice_name]
        denominators = {
            name: sum(denominators_by_case[case_id][name] for case_id in members)
            for name in _CORRECTION_METRIC_NAMES
        }
        numerators = {
            name: sum(numerators_by_case[case_id][name] for case_id in members)
            for name in _CORRECTION_METRIC_NAMES
        }
        metrics = [
            _correction_metric_row(name, numerators[name], denominators[name])
            for name in _CORRECTION_METRIC_NAMES
        ]
        slice_rows.append(
            {
                "slice": slice_name.value,
                "case_ids": members,
                "case_count": len(members),
                "retrieval_outcomes": dict(Counter(outcomes[case_id] for case_id in members)),
                "eligible_denominators": denominators,
                "unavailable_metric_count": sum(
                    item["status"] == "UNAVAILABLE" for item in metrics
                ),
                "metrics": metrics,
            }
        )
    corrected_slice_report = {
        "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
        "correction_id": correction_id,
        "slice_count": len(CodexGoldenSlice),
        "slices": slice_rows,
    }

    hit_rows = [item for item in hard_negative_cases if item["hit"]]
    route_rows: list[dict[str, object]] = []
    for route in sorted({str(item["route"]) for item in hard_negative_cases}):
        members = [item for item in hard_negative_cases if item["route"] == route]
        route_rows.append(
            {
                "route": route,
                "denominator": len(members),
                "hits": sum(bool(item["hit"]) for item in members),
                "case_ids": [item["case_id"] for item in members],
                "hit_cases": [
                    {"case_id": item["case_id"], "rank": item["rank"]}
                    for item in members
                    if item["hit"]
                ],
            }
        )
    metric_by_name = {str(item["name"]): item for item in overall_metrics}
    hard_negative_report = {
        "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
        "correction_id": correction_id,
        "case_count": len(hard_negative_cases),
        "general_metric": metric_by_name["hard_negative_hit_rate_at_10"],
        "harmful_old_attempt_metric": metric_by_name[
            CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10.value
        ],
        "hit_count": len(hit_rows),
        "hits": hit_rows,
        "routes": route_rows,
        "cases": hard_negative_cases,
    }
    refusal_report = {
        "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
        "correction_id": correction_id,
        "eligible_case_count": len(refusal_cases),
        "eligible_case_ids": [item["case_id"] for item in refusal_cases],
        "correct_zero_metric": metric_by_name["correct_zero_rate"],
        "refusal_failure_metric": metric_by_name["refusal_failure_rate"],
        "cases": refusal_cases,
    }
    return {
        "corrected_metrics": corrected_metrics,
        "corrected_slice_report": corrected_slice_report,
        "hard_negative_report": hard_negative_report,
        "refusal_report": refusal_report,
        "derived": {
            "reviewed_evidence_count": reviewed_item_count,
            "reviewed_rank_gap_case_count": rank_gap_case_count,
            "hard_negative_hit_count": len(hit_rows),
            "refusal_failure_count": sum(bool(item["refusal_failure"]) for item in refusal_cases),
            "correct_zero_count": sum(bool(item["correct_zero"]) for item in refusal_cases),
        },
    }


def _correction_evaluator_runtime_digest() -> str:
    functions = (
        _derive_complete_correction_evidence,
        _correction_case_ledger,
        _correction_metric_row,
        _compute_correction_payloads,
        _case_denominator_contributions,
    )
    return _sha256(
        _canonical_json(
            [
                {
                    "qualname": function.__qualname__,
                    "source_sha256": _source_digest(function),
                    "code_sha256": _function_code_digest(function),
                }
                for function in functions
            ]
        )
    )


def _require_correction_evaluator_authority() -> None:
    if _correction_evaluator_runtime_digest() != CODEX_CORRECTION_RUNTIME_AUTHORITY_DIGEST:
        raise CodexBaselineError("correction evaluator implementation digest mismatch")


def _correction_security_payload(correction_id: str) -> dict[str, object]:
    return {
        "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
        "correction_id": correction_id,
        "status": "clean",
        "scanned_files": list(_CORRECTION_SECURITY_INPUT_FILES),
        "checks": {
            "absolute_paths": "clean",
            "credentials": "clean",
            "personal_or_payment_data": "clean",
            "temporary_database_or_raw_fixture": "clean",
            "sqlite_sidecars": "clean",
            "compiled_python": "clean",
            "nonfinite_numbers": "clean",
            "undeclared_files": "clean",
            "source_byte_exact": "clean",
        },
        "findings": [],
    }


def _require_correction_tree(directory: Path) -> Path:
    resolved = _require_existing_nonsymlink(
        directory,
        directory=True,
        label="correction artifact directory",
    )
    entries: set[str] = set()
    for path in directory.iterdir():
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise CodexBaselineError("correction artifact may contain only top-level regular files")
        entries.add(path.name)
    if entries != set(CORRECTION_CANONICAL_ARTIFACT_FILES):
        raise CodexBaselineError("correction canonical artifact membership mismatch")
    return resolved


def verify_codex_baseline_correction_artifact_v1(
    directory: str | Path,
) -> CodexBaselineCorrectionVerification:
    """Verify a self-contained correction without retrieval, ingestion, or SQLite."""

    _require_correction_evaluator_authority()
    artifact = Path(directory)
    _require_correction_tree(artifact)
    checksums = _read_json(artifact / "checksums.json")
    _require_exact_keys(
        checksums,
        ("schema_version", "correction_id", "algorithm", "files", "artifact_set_hash"),
        label="correction checksums",
    )
    if (
        checksums.get("schema_version") != CODEX_CORRECTION_SCHEMA_VERSION
        or checksums.get("algorithm") != "sha256"
    ):
        raise CodexBaselineError("correction checksum identity mismatch")
    files = checksums.get("files")
    actual_files = _file_records(artifact, _CORRECTION_CHECKSUM_TARGET_FILES)
    if (
        not isinstance(files, dict)
        or files != actual_files
        or set(files) != set(_CORRECTION_CHECKSUM_TARGET_FILES)
    ):
        raise CodexBaselineError("correction file checksum mismatch")
    artifact_set_hash = _artifact_set_hash(actual_files)
    if checksums.get("artifact_set_hash") != artifact_set_hash:
        raise CodexBaselineError("correction artifact set hash mismatch")
    findings = _scan_artifact_files(artifact, _CORRECTION_CHECKSUM_TARGET_FILES)
    findings.extend(_scan_json_strings("checksums.json", checksums))
    if findings:
        raise CodexBaselineError(f"correction portable security scan failed: {sorted(findings)}")

    correction = _read_json(artifact / "correction.json")
    manifest = _read_json(artifact / "manifest.json")
    golden_rows = _read_jsonl(artifact / "golden_cases.jsonl")
    prediction_rows = _read_jsonl(artifact / "predictions.jsonl")
    corrected_metrics = _read_json(artifact / "corrected_metrics.json")
    corrected_slices = _read_json(artifact / "corrected_slice_report.json")
    hard_negative_report = _read_json(artifact / "hard_negative_report.json")
    refusal_report = _read_json(artifact / "refusal_report.json")
    security = _read_json(artifact / "security_report.json")
    _require_exact_keys(
        correction,
        (
            "schema_version",
            "artifact_version",
            "correction_directory_id",
            "correction_id",
            "correction_uri",
            "status",
            "release_posture",
            "source_run_directory_id",
            "source_run_id",
            "source_run_uri",
            "source_artifact_set_hash",
            "golden_authority_digest",
            "component_authority_digest",
            "evaluator_version",
            "evaluator_digest",
            "case_count",
            "case_membership",
            "source_run_immutable",
            "retrieval_executed",
            "persistent_artifact",
            "baseline_qualified",
        ),
        label="correction",
    )
    _require_exact_keys(
        manifest,
        (
            "schema_version",
            "artifact_version",
            "correction_directory_id",
            "correction_id",
            "correction_uri",
            "source",
            "evaluator",
            "derived",
            "canonical_files",
            "byte_exact_source_copies",
            "portable_paths_only",
            "source_run_immutable",
            "retrieval_executed",
            "persistent_artifact",
            "baseline_qualified",
        ),
        label="correction manifest",
    )
    correction_directory_id = correction.get("correction_directory_id")
    correction_id = correction.get("correction_id")
    correction_uri = correction.get("correction_uri")
    if (
        correction_directory_id != _correction_directory_id()
        or type(correction_id) is not str
        or not _IDENTIFIER_RE.fullmatch(correction_id)
        or correction_id != _correction_id()
        or correction_uri != _correction_uri()
        or manifest.get("correction_directory_id") != correction_directory_id
        or manifest.get("correction_id") != correction_id
        or manifest.get("correction_uri") != correction_uri
        or checksums.get("correction_id") != correction_id
    ):
        raise CodexBaselineError("correction identity mismatch")
    persistent_artifact = correction.get("persistent_artifact")
    if type(persistent_artifact) is not bool:
        raise CodexBaselineError("correction persistence posture is invalid")
    expected_status = "CORRECTION_PUBLISHED" if persistent_artifact else "CORRECTION_PREPARED"
    if correction != {
        "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
        "artifact_version": CODEX_CORRECTION_ARTIFACT_VERSION,
        "correction_directory_id": correction_directory_id,
        "correction_id": correction_id,
        "correction_uri": correction_uri,
        "status": expected_status,
        "release_posture": "NON_QUALIFIED",
        "source_run_directory_id": CODEX_XB0_SOURCE_RUN_DIRECTORY_ID,
        "source_run_id": CODEX_XB0_SOURCE_RUN_ID,
        "source_run_uri": CODEX_XB0_SOURCE_RUN_URI,
        "source_artifact_set_hash": CODEX_XB0_SOURCE_ARTIFACT_SET_HASH,
        "golden_authority_digest": CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
        "component_authority_digest": CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST,
        "evaluator_version": CODEX_CORRECTION_EVALUATOR_VERSION,
        "evaluator_digest": CODEX_CORRECTION_EVALUATOR_DIGEST,
        "case_count": CODEX_XB0_CASE_COUNT,
        "case_membership": list(CODEX_XB0_CASE_MEMBERSHIP),
        "source_run_immutable": True,
        "retrieval_executed": False,
        "persistent_artifact": persistent_artifact,
        "baseline_qualified": False,
    }:
        raise CodexBaselineError("correction fixed authority fields mismatch")

    source = manifest.get("source")
    evaluator = manifest.get("evaluator")
    if not isinstance(source, dict) or not isinstance(evaluator, dict):
        raise CodexBaselineError("correction manifest authority is invalid")
    _require_exact_keys(
        source,
        (
            "directory_id",
            "run_id",
            "uri",
            "artifact_set_hash",
            "files",
            "dataset",
            "runner_identity",
            "golden_authority_digest",
            "component_authority_digest",
            "case_count",
            "case_membership",
        ),
        label="correction source",
    )
    expected_source = {
        "directory_id": CODEX_XB0_SOURCE_RUN_DIRECTORY_ID,
        "run_id": CODEX_XB0_SOURCE_RUN_ID,
        "uri": CODEX_XB0_SOURCE_RUN_URI,
        "artifact_set_hash": CODEX_XB0_SOURCE_ARTIFACT_SET_HASH,
        "files": _FIXED_SOURCE_FILE_RECORDS,
        "dataset": source.get("dataset"),
        "runner_identity": _fixed_source_runner_identity().model_dump(
            mode="json", exclude_none=True
        ),
        "golden_authority_digest": CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
        "component_authority_digest": CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST,
        "case_count": CODEX_XB0_CASE_COUNT,
        "case_membership": list(CODEX_XB0_CASE_MEMBERSHIP),
    }
    if source != expected_source:
        raise CodexBaselineError("correction source authority mismatch")
    if evaluator != {
        "version": CODEX_CORRECTION_EVALUATOR_VERSION,
        "implementation_digest": CODEX_CORRECTION_EVALUATOR_DIGEST,
        "review_policy": "all-frozen-judgments-at-original-top10-ranks",
        "prefix_break": False,
        "trust_source_reviewed_result_count": False,
    }:
        raise CodexBaselineError("correction evaluator authority mismatch")
    if (
        manifest.get("canonical_files") != list(CORRECTION_CANONICAL_ARTIFACT_FILES)
        or manifest.get("byte_exact_source_copies") != ["golden_cases.jsonl", "predictions.jsonl"]
        or manifest.get("portable_paths_only") is not True
        or manifest.get("source_run_immutable") is not True
        or manifest.get("retrieval_executed") is not False
        or manifest.get("persistent_artifact") is not persistent_artifact
        or manifest.get("baseline_qualified") is not False
    ):
        raise CodexBaselineError("correction manifest posture mismatch")

    copied_records = _file_records(
        artifact,
        ("golden_cases.jsonl", "predictions.jsonl"),
    )
    if copied_records != {
        name: _FIXED_SOURCE_FILE_RECORDS[name]
        for name in ("golden_cases.jsonl", "predictions.jsonl")
    }:
        raise CodexBaselineError("correction source copies are not byte-exact")
    dataset_payload = source.get("dataset")
    if not isinstance(dataset_payload, dict):
        raise CodexBaselineError("correction source dataset is invalid")
    dataset = _rebuild_released_dataset_authority(dataset_payload, golden_rows)
    predictions = _parse_correction_predictions(prediction_rows)
    payloads = _compute_correction_payloads(
        dataset,
        predictions,
        correction_id=correction_id,
    )
    if (
        corrected_metrics != payloads["corrected_metrics"]
        or corrected_slices != payloads["corrected_slice_report"]
        or hard_negative_report != payloads["hard_negative_report"]
        or refusal_report != payloads["refusal_report"]
        or manifest.get("derived") != payloads["derived"]
    ):
        raise CodexBaselineError("correction reports differ from complete truth recomputation")
    expected_security = _correction_security_payload(correction_id)
    if security != expected_security:
        raise CodexBaselineError("correction security report mismatch")
    return CodexBaselineCorrectionVerification(
        correction_directory_id=correction_directory_id,
        correction_id=correction_id,
        correction_uri=correction_uri,
        status="VERIFIED_CORRECTION_NON_QUALIFIED",
        artifact_set_hash=artifact_set_hash,
        persistent_artifact=persistent_artifact,
    )


def build_codex_baseline_correction_v1(
    request: CodexBaselineCorrectionRequest | CodexBaselineCorrectionPublicationRequest,
) -> CodexBaselineCorrectionResult | CodexBaselineCorrectionPublicationResult:
    """Build one offline correction from the immutable released Run."""

    if type(request) not in (
        CodexBaselineCorrectionRequest,
        CodexBaselineCorrectionPublicationRequest,
    ):
        raise CodexBaselineError("correction request must be the exact frozen contract")
    paths = _validate_correction_paths(request)
    _require_correction_evaluator_authority()
    verify_codex_baseline_artifact_v1(paths.source_run_dir)
    source_records = _file_records(paths.source_run_dir, CANONICAL_ARTIFACT_FILES)
    if source_records != _FIXED_SOURCE_FILE_RECORDS:
        raise CodexBaselineError("correction source file authority mismatch")
    source_manifest = _read_json(paths.source_run_dir / "manifest.json")
    golden_rows = _read_jsonl(paths.source_run_dir / "golden_cases.jsonl")
    prediction_rows = _read_jsonl(paths.source_run_dir / "predictions.jsonl")
    dataset_payload = source_manifest.get("dataset")
    if not isinstance(dataset_payload, dict):
        raise CodexBaselineError("correction source dataset is unavailable")
    dataset = _rebuild_released_dataset_authority(dataset_payload, golden_rows)
    predictions = _parse_correction_predictions(prediction_rows)
    correction_directory_id = _correction_directory_id()
    correction_id = _correction_id()
    correction_uri = _correction_uri()
    correction_status = (
        "CORRECTION_PUBLISHED" if paths.persistent_artifact else "CORRECTION_PREPARED"
    )
    payloads = _compute_correction_payloads(
        dataset,
        predictions,
        correction_id=correction_id,
    )
    correction = {
        "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
        "artifact_version": CODEX_CORRECTION_ARTIFACT_VERSION,
        "correction_directory_id": correction_directory_id,
        "correction_id": correction_id,
        "correction_uri": correction_uri,
        "status": correction_status,
        "release_posture": "NON_QUALIFIED",
        "source_run_directory_id": CODEX_XB0_SOURCE_RUN_DIRECTORY_ID,
        "source_run_id": CODEX_XB0_SOURCE_RUN_ID,
        "source_run_uri": CODEX_XB0_SOURCE_RUN_URI,
        "source_artifact_set_hash": CODEX_XB0_SOURCE_ARTIFACT_SET_HASH,
        "golden_authority_digest": CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
        "component_authority_digest": CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST,
        "evaluator_version": CODEX_CORRECTION_EVALUATOR_VERSION,
        "evaluator_digest": CODEX_CORRECTION_EVALUATOR_DIGEST,
        "case_count": CODEX_XB0_CASE_COUNT,
        "case_membership": list(CODEX_XB0_CASE_MEMBERSHIP),
        "source_run_immutable": True,
        "retrieval_executed": False,
        "persistent_artifact": paths.persistent_artifact,
        "baseline_qualified": False,
    }
    manifest = {
        "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
        "artifact_version": CODEX_CORRECTION_ARTIFACT_VERSION,
        "correction_directory_id": correction_directory_id,
        "correction_id": correction_id,
        "correction_uri": correction_uri,
        "source": {
            "directory_id": CODEX_XB0_SOURCE_RUN_DIRECTORY_ID,
            "run_id": CODEX_XB0_SOURCE_RUN_ID,
            "uri": CODEX_XB0_SOURCE_RUN_URI,
            "artifact_set_hash": CODEX_XB0_SOURCE_ARTIFACT_SET_HASH,
            "files": _FIXED_SOURCE_FILE_RECORDS,
            "dataset": dataset_payload,
            "runner_identity": _fixed_source_runner_identity().model_dump(
                mode="json", exclude_none=True
            ),
            "golden_authority_digest": CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
            "component_authority_digest": CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST,
            "case_count": CODEX_XB0_CASE_COUNT,
            "case_membership": list(CODEX_XB0_CASE_MEMBERSHIP),
        },
        "evaluator": {
            "version": CODEX_CORRECTION_EVALUATOR_VERSION,
            "implementation_digest": CODEX_CORRECTION_EVALUATOR_DIGEST,
            "review_policy": "all-frozen-judgments-at-original-top10-ranks",
            "prefix_break": False,
            "trust_source_reviewed_result_count": False,
        },
        "derived": payloads["derived"],
        "canonical_files": list(CORRECTION_CANONICAL_ARTIFACT_FILES),
        "byte_exact_source_copies": ["golden_cases.jsonl", "predictions.jsonl"],
        "portable_paths_only": True,
        "source_run_immutable": True,
        "retrieval_executed": False,
        "persistent_artifact": paths.persistent_artifact,
        "baseline_qualified": False,
    }
    output_published = False
    try:
        paths.output_dir.parent.mkdir(parents=True, exist_ok=True)
        paths.staging_dir.mkdir()
        _write_json(paths.staging_dir / "correction.json", correction)
        _write_json(paths.staging_dir / "manifest.json", manifest)
        (paths.staging_dir / "golden_cases.jsonl").write_bytes(
            (paths.source_run_dir / "golden_cases.jsonl").read_bytes()
        )
        (paths.staging_dir / "predictions.jsonl").write_bytes(
            (paths.source_run_dir / "predictions.jsonl").read_bytes()
        )
        _write_json(
            paths.staging_dir / "corrected_metrics.json",
            payloads["corrected_metrics"],
        )
        _write_json(
            paths.staging_dir / "corrected_slice_report.json",
            payloads["corrected_slice_report"],
        )
        _write_json(
            paths.staging_dir / "hard_negative_report.json",
            payloads["hard_negative_report"],
        )
        _write_json(
            paths.staging_dir / "refusal_report.json",
            payloads["refusal_report"],
        )
        findings = _scan_artifact_files(
            paths.staging_dir,
            _CORRECTION_SECURITY_INPUT_FILES,
        )
        if findings:
            raise CodexBaselineError(f"correction artifact security scan failed: {findings}")
        _write_json(
            paths.staging_dir / "security_report.json",
            _correction_security_payload(correction_id),
        )
        files = _file_records(paths.staging_dir, _CORRECTION_CHECKSUM_TARGET_FILES)
        artifact_set_hash = _artifact_set_hash(files)
        _write_json(
            paths.staging_dir / "checksums.json",
            {
                "schema_version": CODEX_CORRECTION_SCHEMA_VERSION,
                "correction_id": correction_id,
                "algorithm": "sha256",
                "files": files,
                "artifact_set_hash": artifact_set_hash,
            },
        )
        verify_codex_baseline_correction_artifact_v1(paths.staging_dir)
        paths.staging_dir.rename(paths.output_dir)
        output_published = True
        verification = verify_codex_baseline_correction_artifact_v1(paths.output_dir)
        if verification.artifact_set_hash != artifact_set_hash:
            raise CodexBaselineError("correction verification identity changed")
        result_fields = {
            "correction_directory_id": correction_directory_id,
            "correction_id": correction_id,
            "correction_uri": correction_uri,
            "artifact_path": paths.output_dir,
            "artifact_set_hash": artifact_set_hash,
        }
        if paths.persistent_artifact:
            return CodexBaselineCorrectionPublicationResult(
                status="CORRECTION_PUBLISHED",
                **result_fields,
            )
        return CodexBaselineCorrectionResult(
            status="CORRECTION_PREPARED",
            **result_fields,
        )
    except Exception:
        if output_published and paths.output_dir.exists():
            shutil.rmtree(paths.output_dir)
        raise
    finally:
        if paths.staging_dir.exists():
            shutil.rmtree(paths.staging_dir)


_assert_exact_production_components()


def run_codex_baseline_smoke_v1(request: CodexBaselineRequest) -> CodexBaselineResult:
    """Run only the non-qualified small-fixture path."""

    if type(request) is not CodexBaselineRequest or request.execution_mode != "smoke":
        raise CodexBaselineError("smoke runner accepts only an exact smoke request")
    return run_codex_baseline_v1(request)


verify_codex_baseline_v1 = verify_codex_baseline_artifact_v1


__all__ = [
    "CANONICAL_ARTIFACT_FILES",
    "CORRECTION_CANONICAL_ARTIFACT_FILES",
    "CODEX_BASELINE_ARTIFACT_VERSION",
    "CODEX_BASELINE_RELEASE_AUTHORIZATION",
    "CODEX_BASELINE_RUNNER_VERSION",
    "CODEX_BASELINE_SCHEMA_VERSION",
    "CODEX_BASELINE_SMOKE_AUTHORIZATION",
    "CODEX_HISTORICAL_PRODUCTION_AUTHORITY_DIGEST",
    "CODEX_HISTORICAL_PRODUCTION_AUTHORITY_VERSION",
    "CODEX_PRODUCTION_AUTHORITY_DIGEST",
    "CODEX_PRODUCTION_AUTHORITY_VERSION",
    "CODEX_CORRECTION_ARTIFACT_VERSION",
    "CODEX_CORRECTION_AUTHORIZATION",
    "CODEX_CORRECTION_EVALUATOR_DIGEST",
    "CODEX_CORRECTION_EVALUATOR_VERSION",
    "CODEX_CORRECTION_RUNTIME_AUTHORITY_DIGEST",
    "CODEX_CORRECTION_RUNTIME_AUTHORITY_VERSION",
    "CODEX_CORRECTION_PUBLICATION_AUTHORIZATION",
    "CODEX_CORRECTION_SCHEMA_VERSION",
    "CODEX_CORRECTION_URI_PREFIX",
    "CODEX_XB0_CASE_COUNT",
    "CODEX_XB0_CASE_MEMBERSHIP",
    "CODEX_XB0_GOLDEN_AUTHORITY_DIGEST",
    "CODEX_XB0_GOLDEN_DATASET_DIGEST",
    "CODEX_XB0_GOLDEN_DATASET_ID",
    "CODEX_XB0_GOLDEN_DATASET_VERSION",
    "CODEX_XB0_GOLDEN_PACKAGE_HASH",
    "CODEX_XB0_GOLDEN_SCHEMA_VERSION",
    "CODEX_XB0_SOURCE_ARTIFACT_SET_HASH",
    "CODEX_XB0_SOURCE_RUN_DIRECTORY_ID",
    "CODEX_XB0_SOURCE_RUN_ID",
    "CODEX_XB0_SOURCE_RUN_URI",
    "CodexBaselineCase",
    "CodexBaselineCorrectionRequest",
    "CodexBaselineCorrectionResult",
    "CodexBaselineCorrectionPublicationRequest",
    "CodexBaselineCorrectionPublicationResult",
    "CodexBaselineCorrectionVerification",
    "CodexBaselineConfig",
    "CodexBaselineError",
    "CodexBaselineErrorRecord",
    "CodexBaselinePreparation",
    "CodexBaselineRequest",
    "CodexBaselineResult",
    "CodexBaselineTrace",
    "CodexBaselineUnavailableError",
    "CodexBaselineVerification",
    "CodexComponentIdentity",
    "CodexPredictionRecord",
    "CodexRankedItem",
    "build_codex_baseline_correction_v1",
    "component_identities_v1",
    "probe_codex_baseline_preparation_v1",
    "run_codex_baseline_smoke_v1",
    "run_codex_baseline_v1",
    "verify_codex_baseline_artifact_v1",
    "verify_codex_baseline_correction_artifact_v1",
    "verify_codex_baseline_v1",
]
