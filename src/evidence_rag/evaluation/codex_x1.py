"""Codex X-T1 event-normalization treatment evaluation harness.

The module deliberately separates read-only preparation from future production
execution.  Preparation verifies immutable Golden/X-B0 authorities, derives
state-eligible membership from the Golden case bodies, validates the exact
Gate-reviewed CX1-01 and CX1-02 implementations, and returns no Run or metrics.

The production runner remains inert unless its exact frozen request sets
``execute=True``.  An executing request reads the released fixture with the
production adapter, constructs bounded observable inputs, calls the exact
reviewed CX1-01/CX1-02 components, uses only a temporary SQLite fact store, and
publishes one portable eleven-file artifact.  Verification is offline and never
calls the adapter, normalizer, facts builder, retrieval, network, or SQLite.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import math
import re
import shlex
import shutil
import stat
import time
import tracemalloc
import types
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self
from urllib.parse import unquote

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
)
from evidence_rag.rag.sources.codex.baseline_v1 import (
    CODEX_CORRECTION_EVALUATOR_DIGEST,
    CODEX_CORRECTION_EVALUATOR_VERSION,
    CODEX_PRODUCTION_AUTHORITY_DIGEST,
    CODEX_XB0_CASE_COUNT,
    CODEX_XB0_CASE_MEMBERSHIP,
    CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
    CODEX_XB0_GOLDEN_DATASET_ID,
    CODEX_XB0_GOLDEN_DATASET_VERSION,
    CODEX_XB0_GOLDEN_PACKAGE_HASH,
    CODEX_XB0_SOURCE_ARTIFACT_SET_HASH,
    CODEX_XB0_SOURCE_RUN_ID,
    CODEX_XB0_SOURCE_RUN_URI,
    verify_codex_baseline_correction_artifact_v1,
)
from evidence_rag.rag.sources.codex.evaluation_v1 import (
    CODEX_GOLDEN_CASE_COUNT,
    CODEX_GOLDEN_DATASET_ID,
    CODEX_GOLDEN_DATASET_VERSION,
    CODEX_GOLDEN_PACKAGE_HASH,
    CodexGoldenCase,
    CodexMetric,
    GoldenDataset,
    load_codex_golden_v1,
)

CODEX_X1_SCHEMA_VERSION = "codex-x-t1-event-treatment-v1"
CODEX_X1_ARTIFACT_VERSION = "codex-x-t1-portable-artifact-v1"
CODEX_X1_RUNNER_VERSION = "codex-x-t1-runner-v1"
CODEX_X1_PRODUCTION_RUN_VERSION = "codex-x-t1-isolated-production-run-v1"
CODEX_X1_PREPARATION_AUTHORIZATION = "X-T1 HARNESS PREPARATION PASS"
CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION = "X-T1 ISOLATED PRODUCTION RUN AUTHORIZED"
CODEX_X1_STATUS = "PREPARED"
CODEX_X1_RELEASE_POSTURE = "NON_QUALIFIED"
CODEX_X1_PRODUCTION_STATUS = "NOT_AUTHORIZED"
CODEX_X1_SEED = 1729

CODEX_X1_GATE_REVIEW_CANONICAL_PATH = (
    "docs/rag-optimization/development/reviews/10_CODEX_X1_GATE_REVIEW.md"
)
CODEX_X1_GATE_SOURCE_TASK = "019f9f27-8d88-7b01-b686-3d4acbc5dcf2"
CODEX_X1_CX1_01_TASK = "019fab06-1ff3-7440-9434-4c122d6ff86c"
CODEX_X1_CX1_02_TASK = "019fab58-b0d0-7c52-8526-dd268c877a84"
CODEX_X1_HARNESS_TASK = "019fab59-24d7-7a20-8395-b2568dbb192a"

CODEX_X1_OUTPUT_WINDOW_CONTRACT_VERSION = "codex-output-window-contract-v1"
CODEX_X1_DERIVED_FACT_CONTRACT_VERSION = "codex-derived-fact-contract-v1"
CODEX_X1_DERIVED_FACT_BUILDER_VERSION = "codex-derived-fact-builder-v1"
CODEX_X1_DERIVED_FACT_SCHEMA_VERSION = "codex-derived-fact-sqlite-schema-v1"
CODEX_X1_SOURCE_AUTHORITY_VERSION = "codex-source-authority-v2"
CODEX_X1_PRODUCTION_GATE_DECISION_SHA256 = (
    "sha256:573facfc03a99587c628c8ad3b5b7dad56ed9bbe96a583528d398b97df192431"
)
CODEX_X1_PRODUCTION_PREPARATION_SHA256 = (
    "sha256:c4cc777bd24271c7a57401811145f9cea3d6139f3d8cb39b1d8d8c70a4bc4792"
)
CODEX_X1_MEMBERSHIP_SHA256 = (
    "sha256:65fec3b1a3afc74709a3502b03f9b22893a63ce440d056cc787473d8622c29b6"
)
CODEX_X1_BASELINE_CONTROL_SHA256 = (
    "sha256:e6af1344fd622abc2a977e148709a10c3495302c15ac7d0c603d955c031bb36a"
)
CODEX_X1_CX1_NORMALIZER_MODULE_SOURCE_SHA256 = (
    "sha256:f934cd24260b365f07398c49ea8a262ed227ea8b133bc4d5f3c6ea45787920c5"
)
CODEX_X1_CX1_NORMALIZER_CALLABLE_CODE_SHA256 = (
    "sha256:b8f548753a287c45019ae7a2f2e4c410a1234a65f4a79357f6fdae3f5410a931"
)

CODEX_XB0_CORRECTION_DIRECTORY_ID = "1ccecfa1c9667d0f716c709d1175d570"
CODEX_XB0_CORRECTION_ID = "codex-xb0-correction-" + CODEX_XB0_CORRECTION_DIRECTORY_ID
CODEX_XB0_CORRECTION_URI = (
    "evaluation-correction://project-codex-xb0-v1/" + CODEX_XB0_CORRECTION_DIRECTORY_ID
)
CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH = (
    "sha256:f5da3d0b0d9d29bed3daff0a230cd1aa131d19385c20e9784b9b7a44a089ca6d"
)

CODEX_X1_CANONICAL_ARTIFACT_FILES = (
    "run.json",
    "manifest.json",
    "golden_cases.jsonl",
    "baseline_control.jsonl",
    "treatment_records.jsonl",
    "metrics.json",
    "slices.json",
    "errors.jsonl",
    "latency.json",
    "security.json",
    "checksums.json",
)
_CHECKSUM_TARGET_FILES = CODEX_X1_CANONICAL_ARTIFACT_FILES[:-1]
_SECURITY_INPUT_FILES = CODEX_X1_CANONICAL_ARTIFACT_FILES[:10]

_STATE_METRICS = (
    CodexMetric.EVENT_ORDER_ACCURACY_AT_10,
    CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10,
    CodexMetric.PATCH_ACCURACY_AT_10,
    CodexMetric.VALIDATION_ACCURACY_AT_10,
    CodexMetric.FALSE_VALIDATED_RATE_AT_10,
)
_EXPECTED_AUTHORITY_DENOMINATORS = {
    "event_order": 3,
    "call_result": 2,
    "patch": 4,
    "validation": 10,
    "false_validation": 5,
    "output_window": 1,
    "error_tail": 2,
}
_EXPECTED_BASELINE_ANCHORS = {
    "event_order": (1, 3),
    "call_result": (1, 2),
    "patch": (3, 4),
    "validation": (7, 10),
    "false_validation": (1, 5),
}

_FIXED_CX1_01_AUTHORITY_ROWS: tuple[Mapping[str, str], ...] = (
    {
        "role": "normalizer-module",
        "kind": "module",
        "module": "evidence_rag.rag.sources.codex.event_normalizer",
        "export": "event_normalizer",
        "qualname": "event_normalizer",
        "version": "codex-observable-event-normalizer-v1",
        "source_sha256": "sha256:f934cd24260b365f07398c49ea8a262ed227ea8b133bc4d5f3c6ea45787920c5",
        "code_sha256": "sha256:f934cd24260b365f07398c49ea8a262ed227ea8b133bc4d5f3c6ea45787920c5",
    },
    {
        "role": "normalizer-function",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.event_normalizer",
        "export": "normalize_codex_events_v1",
        "qualname": "normalize_codex_events_v1",
        "version": "codex-observable-event-normalizer-v1",
        "source_sha256": "sha256:44cf42b61cebf1c39f2fb636cbd998a048ca0810323428ad5cc47163b44df4b0",
        "code_sha256": "sha256:b8f548753a287c45019ae7a2f2e4c410a1234a65f4a79357f6fdae3f5410a931",
    },
    {
        "role": "state-machine-module",
        "kind": "module",
        "module": "evidence_rag.rag.sources.codex.state_machine",
        "export": "state_machine",
        "qualname": "state_machine",
        "version": "codex-observable-event-state-machine-v1",
        "source_sha256": "sha256:edd617ebc9990bd04d4812ada39bbbd1099cf257d2a9858ffa1aec7387899a99",
        "code_sha256": "sha256:edd617ebc9990bd04d4812ada39bbbd1099cf257d2a9858ffa1aec7387899a99",
    },
    {
        "role": "action-state-function",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.state_machine",
        "export": "action_state_history",
        "qualname": "action_state_history",
        "version": "codex-observable-event-state-machine-v1",
        "source_sha256": "sha256:c6489209b53a272c5fdb8680ba9fe96474d105c2e0349375c0b0d1d5d7f136ef",
        "code_sha256": "sha256:7a39038c063acbe4dac345b8471fbfc31331901cb62dbda80a99c0a0354c4911",
    },
    {
        "role": "patch-state-function",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.state_machine",
        "export": "patch_state_history",
        "qualname": "patch_state_history",
        "version": "codex-observable-event-state-machine-v1",
        "source_sha256": "sha256:4aad7aa7e51d1ce4d1aefcdf1c41674c62f697ceaa22ad3790a96b27bab428df",
        "code_sha256": "sha256:7131f76ebe4055907297e282b2959922453f8c6f795d3e84255f6fd5a0aaa218",
    },
    {
        "role": "validation-state-function",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.state_machine",
        "export": "validation_state_history",
        "qualname": "validation_state_history",
        "version": "codex-observable-event-state-machine-v1",
        "source_sha256": "sha256:17be515f22ea774de104bfd5a257de3888ccfd1b78ea88603c3c45966d686608",
        "code_sha256": "sha256:561151ccad269be535004db9f13bc212099f81eb8b485639c1c941fe548b7960",
    },
    {
        "role": "contracts-module",
        "kind": "module",
        "module": "evidence_rag.rag.sources.codex.contracts",
        "export": "contracts",
        "qualname": "contracts",
        "version": "codex-observable-event-contract-v1",
        "source_sha256": "sha256:0181ef70c3351c9ed4d597bf98690f094325e890346c7645d72ae3dbb40bf28d",
        "code_sha256": "sha256:0181ef70c3351c9ed4d597bf98690f094325e890346c7645d72ae3dbb40bf28d",
    },
    {
        "role": "observable-contract-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.contracts",
        "export": "ObservableCodexItem",
        "qualname": "ObservableCodexItem",
        "version": "codex-observable-event-contract-v1",
        "source_sha256": "sha256:501b3d8674124cf28e7f84824c9feba0481948ff9f135d3bce9f6a3acbc03cfb",
        "code_sha256": "sha256:1bed9b4b56cb302ca2ee4e711895d4db5e6dcfc9ac1d677abf758c5963618b33",
    },
    {
        "role": "normalization-result-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.contracts",
        "export": "CodexEventNormalizationResult",
        "qualname": "CodexEventNormalizationResult",
        "version": "codex-observable-event-contract-v1",
        "source_sha256": "sha256:471008c5fe5d93ef3ef02a618181530f315d1a62b4036d403e56673f8406ae64",
        "code_sha256": "sha256:8cb0ae763c276953cf5e89cb0352fb32ff37e347aa390b4d2dece0226f37122e",
    },
)
CODEX_X1_CX1_01_COMPONENT_SET_HASH = (
    "sha256:751a57cd162b217325657f78544635c9c553a08d847894ca91567a60e6954b5c"
)

_FIXED_CX1_02_AUTHORITY_ROWS: tuple[Mapping[str, str], ...] = (
    {
        "role": "facts-module",
        "kind": "module",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "facts_v1",
        "qualname": "facts_v1",
        "version": "codex-derived-fact-builder-v1",
        "source_sha256": "sha256:09f95e2083c2d6d32dabb0b33407c167a0dfe45bd3b99b82b72813928eb43c24",
        "code_sha256": "sha256:09f95e2083c2d6d32dabb0b33407c167a0dfe45bd3b99b82b72813928eb43c24",
    },
    {
        "role": "output-window-contract-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexOutputWindow",
        "qualname": "CodexOutputWindow",
        "version": "codex-output-window-contract-v1",
        "source_sha256": "sha256:2dc4adb309302f3af6222c9601eebcbdc6f2edc23e8cfcada6072818a6349455",
        "code_sha256": "sha256:fa569d309c85083254a8aa4ef9b1cf93659c1d1b22e7b56aa1c6af0d1161660b",
    },
    {
        "role": "located-output-window-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexLocatedOutputWindow",
        "qualname": "CodexLocatedOutputWindow",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:a73a339e207282d88684a17f82fae9a034ab821583aaff8f33a50b9ff4f67920",
        "code_sha256": "sha256:2f636dd6d98e0d3e27b6df4a026c60677bb6c6c48f3282421dadcfca55f45309",
    },
    {
        "role": "output-role-enum",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexOutputRole",
        "qualname": "CodexOutputRole",
        "version": "codex-output-window-contract-v1",
        "source_sha256": "sha256:e52d347284b21360ef5be42e18fecabc1c3e307dbd296b10eec933458e1b6357",
        "code_sha256": "sha256:a21a256056bd73cf8997a5f7d5b510753b2dfc84371ef9e56e81b66777115689",
    },
    {
        "role": "endpoint-kind-enum",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexEndpointKind",
        "qualname": "CodexEndpointKind",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:9ee046bf99e3e047ddbc8f1d885f8a9398d93ac0991370f9f6d5f7edfa291c0a",
        "code_sha256": "sha256:2d3b3d22b8ec59e6bf3077a110c023c7bcfc0a6a22a46644d82fa36aec69d63b",
    },
    {
        "role": "event-endpoint-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexEventEndpoint",
        "qualname": "CodexEventEndpoint",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:aa36d0aa5b0dca0c758c291296e21b01b82d7f30cff3929e7e26d850d52b8bd8",
        "code_sha256": "sha256:c105e7e5d12cd0ca444e85e58d1ea4a4084e635a1367596fff58879ac5a4e84d",
    },
    {
        "role": "event-predicate-enum",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexEventPredicate",
        "qualname": "CodexEventPredicate",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:57486087392792fa840bb98efabdafe14cfe781282ba689daf1e43743aed8fd5",
        "code_sha256": "sha256:a5a608e4f488eba24650437cc9461a947cfde9186be5b1a086d3f7011b7002b5",
    },
    {
        "role": "publication-diagnostics-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactPublicationDiagnostics",
        "qualname": "CodexFactPublicationDiagnostics",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:6e79a233b1837fc36b7165db77802538dce9446cd372902f64c277090f23da08",
        "code_sha256": "sha256:b882cf34d140628a4de92daef18fb54b43dd9204dd1e81f8bce96e4d5734a99e",
    },
    {
        "role": "publication-disposition-enum",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactPublicationDisposition",
        "qualname": "CodexFactPublicationDisposition",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:764fe54a2b7dd85d53bb7b102637d28b0faf3279c3f340fee002e54b4c08ce32",
        "code_sha256": "sha256:0b81923692554a121f5ca9c57d8ae4a5d8b18d6c2bdd0ad1a982cc562ea5d7dd",
    },
    {
        "role": "fact-error-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactError",
        "qualname": "CodexFactError",
        "version": "codex-derived-fact-builder-v1",
        "source_sha256": "sha256:9320bd3824de59c8aa7950b54b51810d1ee391a2382be8bac4285e8682c521d8",
        "code_sha256": "sha256:4764b0d6a1c47677593d9bf3505f436634603159f2506921b25d8d74e1e12416",
    },
    {
        "role": "publication-error-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactPublicationError",
        "qualname": "CodexFactPublicationError",
        "version": "codex-derived-fact-builder-v1",
        "source_sha256": "sha256:d438886d32fd8b38322f7e3c77f8b98943431dd5c1e6c92a15312d97c06f18a2",
        "code_sha256": "sha256:4d3c41dcaa8c62266359699fda659e1314fffcaa6bab6db30ed106a89d985ef1",
    },
    {
        "role": "scope-error-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactScopeError",
        "qualname": "CodexFactScopeError",
        "version": "codex-derived-fact-builder-v1",
        "source_sha256": "sha256:52fd3cd73ffd924fd45f5a19290b584fcc33401be253b6444cef0162fa413531",
        "code_sha256": "sha256:4d3c41dcaa8c62266359699fda659e1314fffcaa6bab6db30ed106a89d985ef1",
    },
    {
        "role": "validation-error-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactValidationError",
        "qualname": "CodexFactValidationError",
        "version": "codex-derived-fact-builder-v1",
        "source_sha256": "sha256:c518698bbaf3e106704f14bcb32de8289a7eb9a4932d5207424895dca01580fa",
        "code_sha256": "sha256:4d3c41dcaa8c62266359699fda659e1314fffcaa6bab6db30ed106a89d985ef1",
    },
    {
        "role": "normalizer-identity-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexNormalizerIdentity",
        "qualname": "CodexNormalizerIdentity",
        "version": "codex-source-authority-v2",
        "source_sha256": "sha256:8f5adbea0bb432284e048dd8415471e0731f6aa0ad96196cd7a16d5e8f4d89e4",
        "code_sha256": "sha256:b882cf34d140628a4de92daef18fb54b43dd9204dd1e81f8bce96e4d5734a99e",
    },
    {
        "role": "raw-output-authority-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexRawOutputAuthority",
        "qualname": "CodexRawOutputAuthority",
        "version": "codex-source-authority-v2",
        "source_sha256": "sha256:f5c3ab4afc10b8ebf216321f5e4bfae80ec8f6f25c4a4444b9766584463d3617",
        "code_sha256": "sha256:b882cf34d140628a4de92daef18fb54b43dd9204dd1e81f8bce96e4d5734a99e",
    },
    {
        "role": "source-item-authority-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexSourceItemAuthority",
        "qualname": "CodexSourceItemAuthority",
        "version": "codex-source-authority-v2",
        "source_sha256": "sha256:0ca3bc42adbccc49d227d8a3e7ab68f50be9ebd4a737ab40c9f03c5704f16aac",
        "code_sha256": "sha256:67eaf6f5fb516bf62bba2849139aefbb1cb4317573a1cf3ffb8c6d8dd67b0699",
    },
    {
        "role": "source-authority-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexSourceAuthority",
        "qualname": "CodexSourceAuthority",
        "version": "codex-source-authority-v2",
        "source_sha256": "sha256:bf2a39e42702f8e5b5859f13ddd1c2b509a74a3827b3cf35e21870c62ea76494",
        "code_sha256": "sha256:4ef32a96bfbbd8bac3607f83f84ce9ad49dcfa128473c445830a2fef5fc183f3",
    },
    {
        "role": "publication-scope-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactPublicationScope",
        "qualname": "CodexFactPublicationScope",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:a9e01234d418277f274a132981f5f76ce2744b5d08399946b0f5940f9330a71c",
        "code_sha256": "sha256:b882cf34d140628a4de92daef18fb54b43dd9204dd1e81f8bce96e4d5734a99e",
    },
    {
        "role": "derived-fact-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexDerivedFact",
        "qualname": "CodexDerivedFact",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:6411c6f45a197856613b29364ed5bf1ddd2c2b954b16adc569b23227d50a8f33",
        "code_sha256": "sha256:0f1cb15308f87f3644c5c261fbb023a6f9b066ecdaffd0533a4d2b4801dfb1cd",
    },
    {
        "role": "derived-event-link-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexDerivedEventLink",
        "qualname": "CodexDerivedEventLink",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:86008cbad7240a140c33c68728dca5b784dde2f01ca2b040edc31037fd8cc230",
        "code_sha256": "sha256:af597d2300706457b21b87eb6519dc446ef9fed54aa676f541b096a130518946",
    },
    {
        "role": "fact-publication-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactPublication",
        "qualname": "CodexFactPublication",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:296865758cb5c75c37754e515a8e9515e7935739de48ebbcb937525ea080ff2c",
        "code_sha256": "sha256:0574d6aee52fbc278d0ee6493699e526e808069804da7871cbf8a0c39b0bcc27",
    },
    {
        "role": "publication-result-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "CodexFactPublicationResult",
        "qualname": "CodexFactPublicationResult",
        "version": "codex-derived-fact-contract-v1",
        "source_sha256": "sha256:7f5b00756d178bc62d894db20d267820e23121d22ea65921181871ac3e712d47",
        "code_sha256": "sha256:b882cf34d140628a4de92daef18fb54b43dd9204dd1e81f8bce96e4d5734a99e",
    },
    {
        "role": "sqlite-fact-store-class",
        "kind": "class",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "SQLiteCodexFactStore",
        "qualname": "SQLiteCodexFactStore",
        "version": "codex-derived-fact-sqlite-schema-v1",
        "source_sha256": "sha256:c4773a85da087d8e522621ff9005beae8330691abde02b848b8346001c99e4c3",
        "code_sha256": "sha256:7d3732a31f91cb344a2003eb48b8ebdeca463f83c74a1110cded26af960fcff1",
    },
    {
        "role": "output-window-builder-function",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "build_codex_output_window_v1",
        "qualname": "build_codex_output_window_v1",
        "version": "codex-output-window-contract-v1",
        "source_sha256": "sha256:13355b9401f9ae776ecc7a2176050458cdd40528d67c0eeca6601ee382e5de1e",
        "code_sha256": "sha256:ef6e7703b1d9ce4230eed17ca503b640c7e1ca8a66c1d90b6541ac287e2ed106",
    },
    {
        "role": "output-window-public-alias",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "build_output_window_v1",
        "qualname": "build_codex_output_window_v1",
        "version": "codex-output-window-contract-v1",
        "source_sha256": "sha256:13355b9401f9ae776ecc7a2176050458cdd40528d67c0eeca6601ee382e5de1e",
        "code_sha256": "sha256:ef6e7703b1d9ce4230eed17ca503b640c7e1ca8a66c1d90b6541ac287e2ed106",
    },
    {
        "role": "fact-publication-builder-function",
        "kind": "function",
        "module": "evidence_rag.rag.sources.codex.facts_v1",
        "export": "build_codex_fact_publication_v1",
        "qualname": "build_codex_fact_publication_v1",
        "version": "codex-derived-fact-builder-v1",
        "source_sha256": "sha256:46d6d56c7790ea6d555d0db7b9fa0dc329312523b15fe14241f244ba4cbf049c",
        "code_sha256": "sha256:560124fced46bfbdade5c67583e151cf2fddad3604db7db642ef444bfda850e8",
    },
)
CODEX_X1_CX1_02_COMPONENT_SET_HASH = (
    "sha256:1cdabf3393221ffc25c5a38f8b8e126dabc9bf9d330529a2d6b2b8bc61245368"
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CREDENTIAL_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s\"']{8,}))"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s\"'=:(])(?:/(?!/)[A-Za-z0-9._~+-]+"
    r"(?:/[A-Za-z0-9._~+,%:-]+)*|[A-Za-z]:[\\/])"
)
_DB_NAME_RE = re.compile(
    r"(?:^|/)(?:[^/]*\.(?:db|sqlite|sqlite3)|[^/]*(?:-wal|-shm))$",
    re.IGNORECASE,
)
_FORBIDDEN_ARTIFACT_SUFFIXES = (".pyc", "-wal", "-shm", ".db", ".sqlite", ".sqlite3")
_MARKDOWN_HEADING_RE = re.compile(
    r"^(?P<marks>#{1,6})[ \t]+(?P<body>[^\r\n]+?)[ \t]*$",
    re.MULTILINE,
)
_JOINT_GATE_HEADING_RE = re.compile(
    r"^(?P<number>[0-9]+(?:\.[0-9]+)*)\.?\s+.*(?:联合\s+)?Gate 结论\s*$"
)
_JOINT_GATE_PARENT_RE = re.compile(r"^[0-9]+\.?\s+CX1-02 \+ X-T1 harness 联合 Engineering Gate$")
_JOINT_ENGINEERING_RE = re.compile(r"^`CODEX X1 CX1-02 ENGINEERING (?P<decision>PASS|FAIL)`$")
_HARNESS_PREPARATION_RE = re.compile(r"^`X-T1 HARNESS PREPARATION (?P<decision>PASS|FAIL)`$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_json(value))


def _validate_identifier(value: str) -> str:
    if value != value.strip() or _CONTROL_RE.search(value) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier is not canonical")
    return value


def _validate_sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("expected a canonical sha256 identity")
    return value


Identifier = Annotated[StrictStr, AfterValidator(_validate_identifier)]
Sha256 = Annotated[StrictStr, AfterValidator(_validate_sha256)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]


class CodexX1Error(ValueError):
    """Base fail-closed X-T1 harness error."""


class CodexX1NotAuthorizedError(CodexX1Error):
    """A caller attempted production execution before exact authorization."""


class CodexX1VerificationError(CodexX1Error):
    """An authority or portable artifact failed verification."""


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
        return _sha256_bytes(self.canonical_json_bytes())

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


class Availability(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    AVAILABLE = "AVAILABLE"


class CodexX1Arm(StrEnum):
    BASELINE_CONTROL = "baseline_v1_immutable_control"
    CX1_NORMALIZED = "cx1_normalized_treatment"
    CX1_DERIVED_FACTS = "cx1_plus_cx1_02_derived_facts_window_treatment"


class CodexX1MetricName(StrEnum):
    CALL_RESULT_PAIRING_PRECISION = "call_result_pairing_precision"
    CALL_RESULT_PAIRING_RECALL = "call_result_pairing_recall"
    PATCH_STATUS_ACCURACY = "patch_status_accuracy"
    VALIDATION_PRECISION = "validation_precision"
    VALIDATION_RECALL = "validation_recall"
    VALIDATION_F1 = "validation_f1"
    FALSE_VALIDATED_RATE = "false_validated_rate"
    EVENT_ORDER_ACCURACY = "event_order_accuracy"
    UNLINKED_RESULT_COUNT = "unlinked_result_count"
    UNKNOWN_STATE_COUNT = "unknown_state_count"
    OUTPUT_WINDOW_COVERAGE = "output_window_coverage"
    ERROR_TAIL_COVERAGE = "error_tail_coverage"
    REASONING_LEAKAGE_COUNT = "reasoning_leakage_count"
    SECRET_LEAKAGE_COUNT = "secret_leakage_count"
    ACL_LEAKAGE_COUNT = "acl_leakage_count"
    LATENCY_P50_MS = "latency_p50_ms"
    LATENCY_P95_MS = "latency_p95_ms"
    MEMORY_PEAK_BYTES = "memory_peak_bytes"


class CodexX1ComponentIdentity(_FrozenContract):
    role: Identifier
    kind: Literal["module", "class", "function"]
    module: StrictStr
    export: StrictStr
    qualname: StrictStr
    version: StrictStr
    source_sha256: Sha256
    code_sha256: Sha256


class CodexX1GateDecision(_FrozenContract):
    review_canonical_path: Literal[
        "docs/rag-optimization/development/reviews/10_CODEX_X1_GATE_REVIEW.md"
    ] = CODEX_X1_GATE_REVIEW_CANONICAL_PATH
    gate_source_task_id: Literal["019f9f27-8d88-7b01-b686-3d4acbc5dcf2"] = CODEX_X1_GATE_SOURCE_TASK
    cx1_02_task_id: Literal["019fab58-b0d0-7c52-8526-dd268c877a84"] = CODEX_X1_CX1_02_TASK
    harness_task_id: Literal["019fab59-24d7-7a20-8395-b2568dbb192a"] = CODEX_X1_HARNESS_TASK
    conclusion_number: StrictStr
    section_heading: StrictStr
    decision_block: StrictStr
    decision_block_sha256: Sha256
    engineering_decision: Literal["PASS", "FAIL"]
    harness_preparation_decision: Literal["PASS", "FAIL"]
    p0_findings: NonNegativeInt
    p1_findings: NonNegativeInt
    cx1_02_authorization: Literal["AUTHORIZED", "NOT_AUTHORIZED"]
    isolated_production_run_authorization: Literal["AUTHORIZED", "NOT_AUTHORIZED"]

    @model_validator(mode="after")
    def _exact_decision_digest(self) -> Self:
        if _sha256_bytes(self.decision_block.encode("utf-8")) != self.decision_block_sha256:
            raise ValueError("joint Gate decision block digest mismatch")
        expected_block = (
            "\n".join(
                (
                    self.section_heading,
                    f"`CODEX X1 CX1-02 ENGINEERING {self.engineering_decision}`",
                    f"`X-T1 HARNESS PREPARATION {self.harness_preparation_decision}`",
                    f"- `P0 findings: {self.p0_findings}`",
                    f"- `P1 findings: {self.p1_findings}`",
                    f"- `CX1-02 {self.cx1_02_authorization}`",
                    "- `X-T1 ISOLATED PRODUCTION RUN "
                    f"{self.isolated_production_run_authorization}`",
                )
            )
            + "\n"
        )
        if self.decision_block != expected_block:
            raise ValueError("joint Gate fields are not bound to the exact decision block")
        return self

    @property
    def production_authorized(self) -> bool:
        return (
            self.engineering_decision == "PASS"
            and self.harness_preparation_decision == "PASS"
            and self.p0_findings == 0
            and self.p1_findings == 0
            and self.cx1_02_authorization == "AUTHORIZED"
            and self.isolated_production_run_authorization == "AUTHORIZED"
        )


CodexX1ProductionGateDecision = CodexX1GateDecision


class CodexX1FactsVersions(_FrozenContract):
    output_window_contract_version: Literal["codex-output-window-contract-v1"] = (
        CODEX_X1_OUTPUT_WINDOW_CONTRACT_VERSION
    )
    derived_fact_contract_version: Literal["codex-derived-fact-contract-v1"] = (
        CODEX_X1_DERIVED_FACT_CONTRACT_VERSION
    )
    derived_fact_builder_version: Literal["codex-derived-fact-builder-v1"] = (
        CODEX_X1_DERIVED_FACT_BUILDER_VERSION
    )
    derived_fact_schema_version: Literal["codex-derived-fact-sqlite-schema-v1"] = (
        CODEX_X1_DERIVED_FACT_SCHEMA_VERSION
    )
    source_authority_version: Literal["codex-source-authority-v2"] = (
        CODEX_X1_SOURCE_AUTHORITY_VERSION
    )


class CodexX1AuthorityUnit(_FrozenContract):
    unit_id: Identifier
    case_id: Identifier
    item_ids: tuple[StrictStr, ...]
    expected: StrictStr
    authority_sha256: Sha256

    @field_validator("item_ids")
    @classmethod
    def _ordered_unique_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("authority unit item IDs must be non-empty and unique")
        return value


class CodexX1CaseAuthority(_FrozenContract):
    case_id: Identifier
    slice: StrictStr
    case_authority_sha256: Sha256
    event_truth_sha256: Sha256
    judgments_sha256: Sha256
    unit_kinds: tuple[Identifier, ...]


class CodexX1Membership(_FrozenContract):
    dataset_id: Literal["codex-golden-v1"] = CODEX_GOLDEN_DATASET_ID
    dataset_version: Literal["v1"] = CODEX_GOLDEN_DATASET_VERSION
    package_hash: Literal[
        "sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1"
    ] = CODEX_GOLDEN_PACKAGE_HASH
    released_case_count: Literal[45] = CODEX_GOLDEN_CASE_COUNT
    eligible_case_ids: tuple[Identifier, ...]
    denominators: dict[Identifier, PositiveInt]
    units: dict[Identifier, tuple[CodexX1AuthorityUnit, ...]]
    cases: tuple[CodexX1CaseAuthority, ...]
    content_sha256: Sha256

    @model_validator(mode="after")
    def _coherent_membership(self) -> Self:
        if self.denominators != _EXPECTED_AUTHORITY_DENOMINATORS:
            raise ValueError("X-T1 denominators differ from Golden authority")
        if tuple(self.units) != tuple(_EXPECTED_AUTHORITY_DENOMINATORS):
            raise ValueError("X-T1 unit slices are incomplete or reordered")
        if {key: len(value) for key, value in self.units.items()} != self.denominators:
            raise ValueError("X-T1 unit membership does not match denominators")
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids != self.eligible_case_ids or case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("X-T1 eligible case membership is not canonical")
        expected_hash = _canonical_sha256(
            {
                "dataset_id": self.dataset_id,
                "dataset_version": self.dataset_version,
                "package_hash": self.package_hash,
                "eligible_case_ids": list(self.eligible_case_ids),
                "denominators": self.denominators,
                "units": {
                    key: [unit.model_dump(mode="json") for unit in value]
                    for key, value in self.units.items()
                },
                "cases": [case.model_dump(mode="json") for case in self.cases],
            }
        )
        if expected_hash != self.content_sha256:
            raise ValueError("X-T1 membership content hash mismatch")
        return self


class CodexX1BaselineAnchor(_FrozenContract):
    name: Identifier
    numerator: NonNegativeInt
    denominator: PositiveInt

    @property
    def value(self) -> float:
        return self.numerator / self.denominator


class CodexX1ArmReadiness(_FrozenContract):
    arm: CodexX1Arm
    availability: Availability
    eligible_case_ids: tuple[Identifier, ...]
    membership_sha256: Sha256
    component_set_hash: Sha256 | None = None
    reason: StrictStr | None = None

    @model_validator(mode="after")
    def _honest_availability(self) -> Self:
        if self.availability == Availability.UNAVAILABLE and self.reason is None:
            raise ValueError("UNAVAILABLE arms require a reason")
        if self.availability == Availability.AVAILABLE and self.reason is not None:
            raise ValueError("ready arms cannot carry an unavailability reason")
        if self.availability == Availability.PROVISIONAL and self.reason is None:
            raise ValueError("PROVISIONAL arms require a reason")
        if (
            self.arm == CodexX1Arm.CX1_DERIVED_FACTS
            and self.component_set_hash != CODEX_X1_CX1_02_COMPONENT_SET_HASH
        ):
            raise ValueError("CX1-02 arm must bind the exact facts/window component set")
        return self


class CodexX1Preparation(_FrozenContract):
    schema_version: Literal["codex-x-t1-event-treatment-v1"] = CODEX_X1_SCHEMA_VERSION
    status: Literal["PREPARED"] = "PREPARED"
    qualification_status: Literal["NON_QUALIFIED"] = "NON_QUALIFIED"
    production_execution: Literal["AUTHORIZED", "NOT_AUTHORIZED"]
    run_created: Literal[False] = False
    metrics_created: Literal[False] = False
    released_treatment_executed: Literal[False] = False
    formal_database_accessed: Literal[False] = False
    network_accessed: Literal[False] = False
    golden_package_hash: Literal[
        "sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1"
    ] = CODEX_GOLDEN_PACKAGE_HASH
    golden_authority_digest: Literal[
        "sha256:b5fc258c94375480b8d76cdc2e5dda6fd86ca49ddc2a0048b5879da2b23d1fe5"
    ] = CODEX_XB0_GOLDEN_AUTHORITY_DIGEST
    correction_uri: Literal[
        "evaluation-correction://project-codex-xb0-v1/1ccecfa1c9667d0f716c709d1175d570"
    ] = CODEX_XB0_CORRECTION_URI
    correction_artifact_set_hash: Literal[
        "sha256:f5da3d0b0d9d29bed3daff0a230cd1aa131d19385c20e9784b9b7a44a089ca6d"
    ] = CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH
    gate: CodexX1GateDecision
    membership: CodexX1Membership
    cx1_01_components: tuple[CodexX1ComponentIdentity, ...]
    cx1_01_component_set_hash: Literal[
        "sha256:751a57cd162b217325657f78544635c9c553a08d847894ca91567a60e6954b5c"
    ] = CODEX_X1_CX1_01_COMPONENT_SET_HASH
    cx1_02_components: tuple[CodexX1ComponentIdentity, ...]
    cx1_02_component_set_hash: Literal[
        "sha256:1cdabf3393221ffc25c5a38f8b8e126dabc9bf9d330529a2d6b2b8bc61245368"
    ] = CODEX_X1_CX1_02_COMPONENT_SET_HASH
    cx1_02_identity_status: Literal["MATCH", "MISMATCH"]
    cx1_02_versions: CodexX1FactsVersions
    baseline_anchors: tuple[CodexX1BaselineAnchor, ...]
    arms: tuple[CodexX1ArmReadiness, ...]
    seed: Literal[1729] = CODEX_X1_SEED

    @model_validator(mode="after")
    def _prepared_only_truth(self) -> Self:
        if (
            _canonical_sha256([item.model_dump(mode="json") for item in self.cx1_01_components])
            != self.cx1_01_component_set_hash
        ):
            raise ValueError("CX1-01 preparation component set mismatch")
        if (
            _canonical_sha256([item.model_dump(mode="json") for item in self.cx1_02_components])
            != self.cx1_02_component_set_hash
        ):
            raise ValueError("CX1-02 preparation component set mismatch")
        anchors = {item.name: (item.numerator, item.denominator) for item in self.baseline_anchors}
        if anchors != _EXPECTED_BASELINE_ANCHORS:
            raise ValueError("X-B0 baseline anchors differ from immutable truth recomputation")
        if tuple(arm.arm for arm in self.arms) != tuple(CodexX1Arm):
            raise ValueError("three X-T1 arms must be present in canonical order")
        for arm in self.arms:
            if (
                arm.eligible_case_ids != self.membership.eligible_case_ids
                or arm.membership_sha256 != self.membership.content_sha256
            ):
                raise ValueError("all X-T1 arms must use the same eligible cases")
        facts_arm = self.arms[-1]
        exact_ready = self.gate.production_authorized and self.cx1_02_identity_status == "MATCH"
        expected_authorization = "AUTHORIZED" if exact_ready else "NOT_AUTHORIZED"
        expected_availability = Availability.AVAILABLE if exact_ready else Availability.UNAVAILABLE
        if (
            self.production_execution != expected_authorization
            or facts_arm.availability != expected_availability
            or facts_arm.component_set_hash != self.cx1_02_component_set_hash
        ):
            raise ValueError("joint Gate and exact CX1-02 readiness posture differ")
        return self


class CodexX1LinkPrediction(_FrozenContract):
    call_id: Identifier
    call_item_id: StrictStr
    result_item_id: StrictStr


class CodexX1StatePrediction(_FrozenContract):
    item_id: StrictStr
    status: Literal[
        "applied",
        "failed",
        "passed",
        "target_unknown",
        "mentioned",
        "invoked",
        "unknown",
    ]


class CodexX1SecurityFinding(_FrozenContract):
    kind: Literal["reasoning", "secret", "acl"]
    evidence_sha256: Sha256


class CodexX1SourceEvidence(_FrozenContract):
    """Portable binding from one adapter item to one Observable source item."""

    source_item_id: StrictStr
    source_locator: StrictStr
    source_file: StrictStr
    observable_item_id: StrictStr
    observable_locator: StrictStr
    observable_sha256: Sha256

    @field_validator("source_file")
    @classmethod
    def _relative_source_file(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("source evidence file must be canonical and relative")
        return value


class CodexX1EventEvidence(_FrozenContract):
    """Bounded normalized/fact event evidence retained for audit."""

    event_id: Sha256
    kind: Identifier
    role: Identifier
    state: Identifier | None = None
    state_history: tuple[Identifier, ...] = ()
    reason_code: Identifier
    source_item_ids: tuple[StrictStr, ...]
    source_locators: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def _coherent_evidence(self) -> Self:
        if (
            not self.source_item_ids
            or len(self.source_item_ids) != len(self.source_locators)
            or len(self.source_item_ids)
            != len(set(zip(self.source_item_ids, self.source_locators, strict=True)))
        ):
            raise ValueError("event evidence must be non-empty, unique, and paired")
        if self.state_history and self.state_history[-1] != self.state:
            raise ValueError("event evidence state history must end at its final state")
        return self


class CodexX1WindowEvidence(_FrozenContract):
    """Content-free output-window metadata bound to one adapter result."""

    source_item_id: StrictStr
    source_locator: StrictStr
    role: Identifier
    content_sha256: Sha256
    total_chars: NonNegativeInt
    total_utf8_bytes: NonNegativeInt
    retained_chars: NonNegativeInt
    omitted_chars: NonNegativeInt
    truncated: StrictBool
    error_tail: StrictBool

    @model_validator(mode="after")
    def _window_counts(self) -> Self:
        if self.retained_chars + self.omitted_chars != self.total_chars:
            raise ValueError("output-window character accounting mismatch")
        return self


class CodexX1TreatmentRecord(_FrozenContract):
    schema_version: Literal["codex-x-t1-event-treatment-v1"] = CODEX_X1_SCHEMA_VERSION
    arm: Literal[
        CodexX1Arm.CX1_NORMALIZED,
        CodexX1Arm.CX1_DERIVED_FACTS,
    ]
    case_id: Identifier
    membership_sha256: Sha256
    ordered_item_ids: tuple[StrictStr, ...] = ()
    links: tuple[CodexX1LinkPrediction, ...] = ()
    patches: tuple[CodexX1StatePrediction, ...] = ()
    validations: tuple[CodexX1StatePrediction, ...] = ()
    observed_result_item_ids: tuple[StrictStr, ...] = ()
    output_window_item_ids: tuple[StrictStr, ...] = ()
    error_tail_item_ids: tuple[StrictStr, ...] = ()
    security_findings: tuple[CodexX1SecurityFinding, ...] = ()
    source_evidence: tuple[CodexX1SourceEvidence, ...] = ()
    event_evidence: tuple[CodexX1EventEvidence, ...] = ()
    window_evidence: tuple[CodexX1WindowEvidence, ...] = ()
    normalization_content_sha256: Sha256 | None = None
    source_set_sha256: Sha256 | None = None
    publication_id: Sha256 | None = None
    diagnostic_codes: tuple[Identifier, ...] = ()
    latency_ms: Annotated[StrictFloat, Field(ge=0)]
    memory_peak_bytes: NonNegativeInt
    source_record_sha256: Sha256

    @field_validator(
        "ordered_item_ids",
        "links",
        "patches",
        "validations",
        "observed_result_item_ids",
        "output_window_item_ids",
        "error_tail_item_ids",
        "security_findings",
        "source_evidence",
        "event_evidence",
        "window_evidence",
        "diagnostic_codes",
    )
    @classmethod
    def _no_duplicate_predictions(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        serialized = [_canonical_json(_jsonable(item)) for item in value]
        if len(serialized) != len(set(serialized)):
            raise ValueError("treatment records cannot contain duplicate predictions")
        return value


class CodexX1MetricResult(_FrozenContract):
    name: CodexX1MetricName
    status: Availability
    numerator: StrictFloat | StrictInt | None
    denominator: StrictFloat | StrictInt | None
    value: StrictFloat | StrictInt | None
    unit: Literal["ratio", "count", "milliseconds", "bytes"]
    reason: StrictStr | None = None

    @model_validator(mode="after")
    def _honest_status(self) -> Self:
        if self.status == Availability.UNAVAILABLE:
            if (
                self.value is not None
                or self.numerator is not None
                or self.denominator not in {None, 0}
                or self.reason is None
            ):
                raise ValueError("UNAVAILABLE metric posture is inconsistent")
            return self
        if self.value is None or self.numerator is None:
            raise ValueError("scored metrics require a numerator and value")
        if self.unit == "ratio":
            if self.denominator is None or float(self.denominator) <= 0:
                raise ValueError("ratio metrics require a positive denominator")
            if abs(float(self.value) - float(self.numerator) / float(self.denominator)) > 1e-12:
                raise ValueError("ratio metric value must be recomputed from counts")
        elif self.denominator is not None and float(self.denominator) < 0:
            raise ValueError("metric denominator cannot be negative")
        if self.status == Availability.PROVISIONAL and self.reason is None:
            raise ValueError("PROVISIONAL metrics require an explicit reason")
        return self


class CodexX1Evaluation(_FrozenContract):
    schema_version: Literal["codex-x-t1-event-treatment-v1"] = CODEX_X1_SCHEMA_VERSION
    arm: Literal[
        CodexX1Arm.CX1_NORMALIZED,
        CodexX1Arm.CX1_DERIVED_FACTS,
    ]
    status: Availability
    qualification_status: Literal["QUALIFIED", "NON_QUALIFIED"]
    membership_sha256: Sha256
    evaluated_case_ids: tuple[Identifier, ...]
    metrics: tuple[CodexX1MetricResult, ...]
    hard_gate_failures: tuple[Identifier, ...]

    @model_validator(mode="after")
    def _hard_gates_are_recomputed(self) -> Self:
        values = {metric.name: metric for metric in self.metrics}
        failures: list[str] = []
        for name in (
            CodexX1MetricName.FALSE_VALIDATED_RATE,
            CodexX1MetricName.REASONING_LEAKAGE_COUNT,
            CodexX1MetricName.SECRET_LEAKAGE_COUNT,
            CodexX1MetricName.ACL_LEAKAGE_COUNT,
        ):
            metric = values.get(name)
            if metric is not None and metric.numerator is not None and float(metric.numerator) != 0:
                failures.append(name.value)
        if self.hard_gate_failures != tuple(failures):
            raise ValueError("hard Gate failures differ from recomputed treatment metrics")
        expected = "NON_QUALIFIED" if failures else "QUALIFIED"
        if self.qualification_status != expected:
            raise ValueError("treatment qualification differs from recomputed hard Gates")
        return self


class CodexX1RunRequest(_FrozenContract):
    """Frozen inputs for isolated admission or one explicit production execution."""

    preparation: CodexX1Preparation
    fixture_root: Path
    golden_root: Path
    correction_root: Path
    source_root: Path
    isolated_root: Path
    output_root: Path
    raw_sqlite_path: Path
    gate_review: Path
    seed: Literal[1729] = CODEX_X1_SEED
    network_allowed: Literal[False] = False
    reuse_output: Literal[False] = False
    execute: StrictBool = False
    production_authorization: Literal["X-T1 ISOLATED PRODUCTION RUN AUTHORIZED"] = (
        CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION
    )

    @model_validator(mode="after")
    def _isolated_paths_only(self) -> Self:
        roots = {
            "fixture_root": self.fixture_root,
            "golden_root": self.golden_root,
            "correction_root": self.correction_root,
            "source_root": self.source_root,
            "isolated_root": self.isolated_root,
        }
        resolved_roots: dict[str, Path] = {}
        for label, path in roots.items():
            if not path.is_absolute():
                raise ValueError(f"{label} must be explicit and absolute")
            try:
                mode = path.lstat().st_mode
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"{label} must already exist") from exc
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ValueError(f"{label} must be a real directory")
            resolved_roots[label] = resolved
        isolated = resolved_roots["isolated_root"]
        for label, path in {"gate_review": self.gate_review}.items():
            if not path.is_absolute():
                raise ValueError(f"{label} must be explicit and absolute")
            try:
                mode = path.lstat().st_mode
                path.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"{label} must already exist") from exc
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ValueError(f"{label} must be a real regular file")
        for label, path in {
            "output_root": self.output_root,
            "raw_sqlite_path": self.raw_sqlite_path,
        }.items():
            if not path.is_absolute():
                raise ValueError(f"{label} must be explicit and absolute")
            if path.exists() or path.is_symlink():
                raise ValueError(f"{label} must be fresh and non-reused")
            try:
                resolved_parent = path.parent.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"{label} parent must already exist") from exc
            production_output = (
                label == "output_root"
                and resolved_parent
                == (_project_root() / "evals" / "codex" / "runs").resolve(strict=True)
                and bool(re.fullmatch(r"[0-9a-f]{32}", path.name))
            )
            if not resolved_parent.is_relative_to(isolated) and not production_output:
                raise ValueError(
                    f"{label} must remain inside isolated_root or be one exact new Run"
                )
            resolved_roots[label] = resolved_parent / path.name
            if not production_output:
                current = path.parent
                while True:
                    if current.is_symlink():
                        raise ValueError(f"{label} cannot traverse a symlink")
                    if current.resolve(strict=True) == isolated:
                        break
                    if current.parent == current:
                        raise ValueError(f"{label} cannot be proven inside isolated_root")
                    current = current.parent
        if resolved_roots["output_root"] == resolved_roots["raw_sqlite_path"]:
            raise ValueError("output_root and raw_sqlite_path must be distinct")
        if self.raw_sqlite_path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            raise ValueError("raw_sqlite_path must name one temporary SQLite file")
        project_root = _project_root().resolve(strict=True)
        if isolated == project_root or isolated.is_relative_to(project_root):
            raise ValueError("isolated_root cannot be the repository or a repository child")
        if resolved_roots["source_root"] != project_root:
            raise ValueError("source_root must be the exact reviewed repository root")
        if not resolved_roots["golden_root"].is_relative_to(resolved_roots["fixture_root"]):
            raise ValueError("golden_root must remain inside fixture_root")
        return self


class CodexX1RunAdmission(_FrozenContract):
    """Authorization result only; it never creates a Run, DB, metrics, or artifact."""

    status: Literal["AUTHORIZED_READY"] = "AUTHORIZED_READY"
    preparation_sha256: Sha256
    gate_decision_block_sha256: Sha256
    membership_sha256: Sha256
    cx1_01_component_set_hash: Literal[
        "sha256:751a57cd162b217325657f78544635c9c553a08d847894ca91567a60e6954b5c"
    ] = CODEX_X1_CX1_01_COMPONENT_SET_HASH
    cx1_02_component_set_hash: Literal[
        "sha256:1cdabf3393221ffc25c5a38f8b8e126dabc9bf9d330529a2d6b2b8bc61245368"
    ] = CODEX_X1_CX1_02_COMPONENT_SET_HASH
    seed: Literal[1729] = CODEX_X1_SEED
    run_created: Literal[False] = False
    metrics_created: Literal[False] = False
    artifact_created: Literal[False] = False
    output_created: Literal[False] = False
    database_opened: Literal[False] = False
    network_accessed: Literal[False] = False


class CodexX1RunResult(_FrozenContract):
    """One completed, verified, isolated production treatment Run."""

    status: Literal["COMPLETED"]
    run_id: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{32}$")]
    run_uri: StrictStr
    artifact_path: Path
    artifact_set_hash: Sha256
    preparation_sha256: Sha256
    gate_decision_block_sha256: Sha256
    membership_sha256: Sha256
    qualification_status: Literal["QUALIFIED", "NON_QUALIFIED"]
    x2_authorization: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    canonical_file_count: Literal[11] = len(CODEX_X1_CANONICAL_ARTIFACT_FILES)
    fixture_thread_count: Literal[8] = 8
    adapter_item_count: Literal[54] = 54
    eligible_case_count: Literal[23] = 23
    treatment_record_count: Literal[46] = 46
    original_verified: Literal[True] = True
    portable_copy_verified: Literal[True] = True
    temporary_database_removed: Literal[True] = True
    temporary_sidecars_removed: Literal[True] = True
    network_accessed: Literal[False] = False


class CodexX1SmokeResult(_FrozenContract):
    artifact_path: Path
    artifact_set_hash: Sha256
    status: Literal["SMOKE_ONLY"]
    run_created: Literal[False] = False
    metrics_created: Literal[False] = False
    production_execution: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    portable: Literal[True] = True
    canonical_file_count: Literal[11] = len(CODEX_X1_CANONICAL_ARTIFACT_FILES)


class CodexX1ArtifactVerification(_FrozenContract):
    artifact_path: Path
    artifact_set_hash: Sha256
    status: Literal["VERIFIED_SMOKE_ONLY", "VERIFIED_PRODUCTION"]
    portable: Literal[True] = True
    canonical_file_count: Literal[11] = len(CODEX_X1_CANONICAL_ARTIFACT_FILES)
    run_created: StrictBool
    metrics_created: StrictBool
    production_execution: Literal["NOT_AUTHORIZED", "EXECUTED"]


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_golden_root() -> Path:
    return _project_root() / "tests" / "fixtures" / "codex_golden_v1"


def _default_correction_root() -> Path:
    return _project_root() / "evals" / "codex" / "corrections" / CODEX_XB0_CORRECTION_DIRECTORY_ID


def _default_gate_review() -> Path:
    return _project_root().joinpath(*PurePosixPath(CODEX_X1_GATE_REVIEW_CANONICAL_PATH).parts)


def _strict_json_loads(raw: bytes, *, label: str) -> object:
    def _reject_constant(value: str) -> None:
        raise CodexX1VerificationError(f"nonfinite number is forbidden in {label}: {value}")

    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexX1VerificationError(f"invalid JSON in {label}") from exc
    _assert_finite(value)
    return value


def _assert_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CodexX1VerificationError("nonfinite numbers are forbidden")
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_finite(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_finite(item)


def _read_canonical_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CodexX1VerificationError(f"artifact file is unavailable: {path.name}") from exc
    value = _strict_json_loads(raw, label=path.name)
    if not isinstance(value, dict) or raw != _canonical_json(value) + b"\n":
        raise CodexX1VerificationError(f"JSON is not canonical: {path.name}")
    return value


def _read_canonical_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CodexX1VerificationError(f"artifact file is unavailable: {path.name}") from exc
    if raw and not raw.endswith(b"\n"):
        raise CodexX1VerificationError(f"JSONL lacks final newline: {path.name}")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(raw.splitlines(), start=1):
        value = _strict_json_loads(line, label=f"{path.name}:{index}")
        if not isinstance(value, dict) or line != _canonical_json(value):
            raise CodexX1VerificationError(f"JSONL row is not canonical: {path.name}:{index}")
        rows.append(value)
    return rows


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_json(value) + b"\n")


def _write_jsonl(path: Path, values: Iterable[object]) -> None:
    path.write_bytes(b"".join(_canonical_json(value) + b"\n" for value in values))


def _exact_gate_identity(text: str, label: str, expected: str) -> str:
    matches = re.findall(
        rf"^{re.escape(label)}：`([^`\r\n]+)`[ \t]*$",
        text,
        re.MULTILINE,
    )
    if matches != [expected]:
        raise CodexX1VerificationError(f"Gate review {label} identity mismatch")
    return expected


def _one_exact_gate_line(
    lines: Sequence[str],
    pattern: re.Pattern[str],
    *,
    label: str,
) -> tuple[int, re.Match[str]]:
    matches = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := pattern.fullmatch(line)) is not None
    ]
    if len(matches) != 1:
        raise CodexX1VerificationError(f"joint Gate decision has missing or conflicting {label}")
    return matches[0]


def parse_codex_x1_gate_review_v1(text: str) -> CodexX1GateDecision:
    """Bind the last exact CX1-02/X-T1 joint Gate decision.

    Only exact full-line decision tokens are accepted. Historical conclusions
    remain inert, and unrelated text outside the selected decision block does
    not change its content digest.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    _exact_gate_identity(
        normalized,
        "Gate 来源任务",
        CODEX_X1_GATE_SOURCE_TASK,
    )
    headings = list(_MARKDOWN_HEADING_RE.finditer(normalized))
    candidates: list[tuple[re.Match[str], int, int]] = []
    for index, heading in enumerate(headings):
        heading_match = _JOINT_GATE_HEADING_RE.fullmatch(heading.group("body"))
        if heading_match is None:
            continue
        level = len(heading.group("marks"))
        end = len(normalized)
        for following in headings[index + 1 :]:
            if len(following.group("marks")) <= level:
                end = following.start()
                break
        body = normalized[heading.end() : end]
        if any(_JOINT_ENGINEERING_RE.fullmatch(line) for line in body.splitlines()):
            candidates.append((heading, index, end))
    if not candidates:
        raise CodexX1VerificationError("Gate review has no exact CX1-02 joint conclusion")
    heading, heading_index, section_end = candidates[-1]

    parent: re.Match[str] | None = None
    parent_end = len(normalized)
    for index in range(heading_index - 1, -1, -1):
        candidate = headings[index]
        if len(candidate.group("marks")) == 2:
            parent = candidate
            for following in headings[index + 1 :]:
                if len(following.group("marks")) <= 2:
                    parent_end = following.start()
                    break
            break
    if parent is None or not (
        parent.start() <= heading.start() < parent_end
        and _JOINT_GATE_PARENT_RE.fullmatch(parent.group("body")) is not None
    ):
        raise CodexX1VerificationError(
            "joint Gate conclusion is not inside the canonical CX1-02/X-T1 section"
        )
    parent_text = normalized[parent.start() : parent_end]
    _exact_gate_identity(
        parent_text,
        "CX1-02 实施任务",
        CODEX_X1_CX1_02_TASK,
    )
    _exact_gate_identity(
        parent_text,
        "X-T1 harness 实施任务",
        CODEX_X1_HARNESS_TASK,
    )

    section_lines = normalized[heading.end() : section_end].splitlines()
    engineering_index, engineering = _one_exact_gate_line(
        section_lines,
        _JOINT_ENGINEERING_RE,
        label="CX1-02 Engineering decision",
    )
    preparation_index, preparation = _one_exact_gate_line(
        section_lines,
        _HARNESS_PREPARATION_RE,
        label="X-T1 harness preparation decision",
    )
    p0_index, p0 = _one_exact_gate_line(
        section_lines,
        re.compile(r"^- `P0 findings: (?P<count>[0-9]+)`$"),
        label="P0 findings",
    )
    p1_index, p1 = _one_exact_gate_line(
        section_lines,
        re.compile(r"^- `P1 findings: (?P<count>[0-9]+)`$"),
        label="P1 findings",
    )
    cx1_index, cx1_authorization = _one_exact_gate_line(
        section_lines,
        re.compile(r"^- `CX1-02 (?P<decision>AUTHORIZED|NOT_AUTHORIZED)`$"),
        label="CX1-02 authorization",
    )
    run_index, run_authorization = _one_exact_gate_line(
        section_lines,
        re.compile(
            r"^- `X-T1 ISOLATED PRODUCTION RUN "
            r"(?P<decision>AUTHORIZED|NOT_AUTHORIZED)`$"
        ),
        label="isolated production Run authorization",
    )
    positions = (
        engineering_index,
        preparation_index,
        p0_index,
        p1_index,
        cx1_index,
        run_index,
    )
    if positions != tuple(sorted(positions)) or len(set(positions)) != len(positions):
        raise CodexX1VerificationError("joint Gate decision tokens are not in canonical order")
    selected_lines = (
        heading.group(0),
        section_lines[engineering_index],
        section_lines[preparation_index],
        section_lines[p0_index],
        section_lines[p1_index],
        section_lines[cx1_index],
        section_lines[run_index],
    )
    decision_block = "\n".join(selected_lines) + "\n"
    heading_match = _JOINT_GATE_HEADING_RE.fullmatch(heading.group("body"))
    assert heading_match is not None
    return CodexX1GateDecision(
        conclusion_number=heading_match.group("number"),
        section_heading=heading.group(0),
        decision_block=decision_block,
        decision_block_sha256=_sha256_bytes(decision_block.encode("utf-8")),
        engineering_decision=engineering.group("decision"),
        harness_preparation_decision=preparation.group("decision"),
        p0_findings=int(p0.group("count")),
        p1_findings=int(p1.group("count")),
        cx1_02_authorization=cx1_authorization.group("decision"),
        isolated_production_run_authorization=run_authorization.group("decision"),
    )


def parse_codex_x1_production_gate_review_v1(
    text: str,
) -> CodexX1ProductionGateDecision:
    """Compatibility entry point for the same exact joint Gate parser."""

    return parse_codex_x1_gate_review_v1(text)


def _constant_code_payload(value: object) -> object:
    return portable_constant_payload(value)


def _code_object_payload(code: types.CodeType) -> dict[str, object]:
    return portable_code_payload(code)


def _function_code_digest(value: types.FunctionType) -> str:
    try:
        return _canonical_sha256(portable_function_payload(value))
    except CodeIdentityError as exc:
        raise CodexX1VerificationError(str(exc)) from exc


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
    return _canonical_sha256(
        {
            "bases": [(base.__module__, base.__qualname__) for base in value.__bases__],
            "members": members,
            "attrs": attributes,
        }
    )


def _source_digest(value: object) -> str:
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError) as exc:
        raise CodexX1VerificationError("production component source is unavailable") from exc
    return _sha256_bytes(source.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _module_source_digest(value: types.ModuleType) -> str:
    source_path = getattr(value, "__file__", None)
    if not isinstance(source_path, str):
        raise CodexX1VerificationError("production module source is unavailable")
    try:
        source = Path(source_path).read_bytes()
    except OSError as exc:
        raise CodexX1VerificationError("production module source is unavailable") from exc
    return _sha256_bytes(source.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))


def _resolve_export(identity: CodexX1ComponentIdentity) -> object:
    module = importlib.import_module(identity.module)
    if identity.kind == "module":
        if identity.export != identity.module.rsplit(".", 1)[-1]:
            raise CodexX1VerificationError("reviewed module export identity is invalid")
        return module
    current: object = module
    for part in identity.export.split("."):
        if not hasattr(current, part):
            raise CodexX1VerificationError(
                f"reviewed public export is unavailable: {identity.module}.{identity.export}"
            )
        current = getattr(current, part)
    return current


def _component_identities_v1(
    rows: Sequence[Mapping[str, str]],
    expected_set_hash: str,
    *,
    label: str,
) -> tuple[CodexX1ComponentIdentity, ...]:
    identities = tuple(CodexX1ComponentIdentity.model_validate(row) for row in rows)
    if _canonical_sha256([item.model_dump(mode="json") for item in identities]) != (
        expected_set_hash
    ):
        raise CodexX1VerificationError(f"{label} fixed identity set is internally invalid")
    for identity in identities:
        actual = _resolve_export(identity)
        if identity.kind == "module":
            expected_source = (
                _project_root() / "src" / Path(*identity.module.split("."))
            ).with_suffix(".py")
            spec = getattr(actual, "__spec__", None)
            source_path = getattr(actual, "__file__", None)
            if (
                type(actual) is not types.ModuleType
                or actual.__name__ != identity.module
                or identity.qualname != identity.export
                or spec is None
                or spec.name != identity.module
                or not isinstance(spec.origin, str)
                or not isinstance(source_path, str)
            ):
                raise CodexX1VerificationError(f"{identity.role} public identity was forged")
            try:
                resolved_source = Path(source_path).resolve(strict=True)
                resolved_origin = Path(spec.origin).resolve(strict=True)
                resolved_expected = expected_source.resolve(strict=True)
            except OSError as exc:
                raise CodexX1VerificationError(
                    f"{identity.role} canonical source path is unavailable"
                ) from exc
            if resolved_source != resolved_expected or resolved_origin != resolved_expected:
                raise CodexX1VerificationError(
                    f"{identity.role} canonical module path was replaced"
                )
            parent_name, export = identity.module.rsplit(".", 1)
            parent = importlib.import_module(parent_name)
            if getattr(parent, export, None) is not actual:
                raise CodexX1VerificationError(
                    f"{identity.role} parent-package module binding was replaced"
                )
            source_digest = _module_source_digest(actual)
            code_digest = source_digest
        elif (
            getattr(actual, "__module__", None) != identity.module
            or getattr(actual, "__qualname__", None) != identity.qualname
        ):
            raise CodexX1VerificationError(f"{identity.role} public identity was forged")
        elif identity.kind == "class":
            if not isinstance(actual, type) or not inspect.isclass(actual):
                raise CodexX1VerificationError(f"{identity.role} is not an exact public class")
            source_digest = _source_digest(actual)
            code_digest = _class_code_digest(actual)
        elif not isinstance(actual, types.FunctionType) or inspect.unwrap(actual) is not actual:
            raise CodexX1VerificationError(f"{identity.role} was wrapped or replaced")
        else:
            source_digest = _source_digest(actual)
            code_digest = _function_code_digest(actual)
        if source_digest != identity.source_sha256 or code_digest != identity.code_sha256:
            raise CodexX1VerificationError(
                f"{identity.role} reviewed implementation digest mismatch"
            )
    return identities


def cx1_01_component_identities_v1() -> tuple[CodexX1ComponentIdentity, ...]:
    """Return and verify the compile-time Gate-reviewed CX1-01 allowlist."""

    identities = _component_identities_v1(
        _FIXED_CX1_01_AUTHORITY_ROWS,
        CODEX_X1_CX1_01_COMPONENT_SET_HASH,
        label="CX1-01",
    )
    normalizer_package = importlib.import_module("evidence_rag.rag.sources.codex")
    normalizer = next(identity for identity in identities if identity.role == "normalizer-function")
    if getattr(normalizer_package, "normalize_codex_events_v1", None) is not _resolve_export(
        normalizer
    ):
        raise CodexX1VerificationError("CX1-01 canonical package export was replaced")
    return identities


def cx1_02_component_identities_v1() -> tuple[CodexX1ComponentIdentity, ...]:
    """Return and verify the compile-time Gate-reviewed facts/window allowlist."""

    identities = _component_identities_v1(
        _FIXED_CX1_02_AUTHORITY_ROWS,
        CODEX_X1_CX1_02_COMPONENT_SET_HASH,
        label="CX1-02",
    )
    facts = importlib.import_module("evidence_rag.rag.sources.codex.facts_v1")
    package = importlib.import_module("evidence_rag.rag.sources.codex")
    facts_public = getattr(facts, "__all__", ())
    package_public = getattr(package, "__all__", ())
    if not isinstance(facts_public, list) or not isinstance(package_public, list):
        raise CodexX1VerificationError("CX1-02 public export manifests were replaced")
    module_identity = next(identity for identity in identities if identity.role == "facts-module")
    if getattr(package, "facts_v1", None) is not _resolve_export(module_identity):
        raise CodexX1VerificationError("CX1-02 canonical package module was replaced")
    for identity in identities:
        if identity.kind == "module":
            continue
        if (
            identity.export not in facts_public
            or identity.export not in package_public
            or getattr(package, identity.export, None) is not _resolve_export(identity)
        ):
            raise CodexX1VerificationError(
                f"CX1-02 canonical package export was replaced: {identity.export}"
            )
    versions = {
        "CODEX_OUTPUT_WINDOW_CONTRACT_VERSION": CODEX_X1_OUTPUT_WINDOW_CONTRACT_VERSION,
        "CODEX_DERIVED_FACT_CONTRACT_VERSION": CODEX_X1_DERIVED_FACT_CONTRACT_VERSION,
        "CODEX_DERIVED_FACT_BUILDER_VERSION": CODEX_X1_DERIVED_FACT_BUILDER_VERSION,
        "CODEX_DERIVED_FACT_SCHEMA_VERSION": CODEX_X1_DERIVED_FACT_SCHEMA_VERSION,
        "CODEX_SOURCE_AUTHORITY_VERSION": CODEX_X1_SOURCE_AUTHORITY_VERSION,
    }
    for name, expected in versions.items():
        if getattr(facts, name, None) != expected or name not in facts_public:
            raise CodexX1VerificationError(f"CX1-02 version was replaced: {name}")
        if getattr(package, name, None) != expected or name not in package_public:
            raise CodexX1VerificationError(f"CX1-02 public package version was replaced: {name}")
    normalizer = next(
        identity
        for identity in cx1_01_component_identities_v1()
        if identity.role == "normalizer-function"
    )
    exact_normalizer = _resolve_export(normalizer)
    if (
        getattr(facts, "_BOUND_CX1_NORMALIZER", None) is not exact_normalizer
        or getattr(facts, "_cx1_normalizer_module", None)
        is not importlib.import_module(normalizer.module)
        or getattr(facts, "CODEX_NORMALIZER_MODULE", None) != normalizer.module
        or getattr(facts, "CODEX_NORMALIZER_EXPORT", None) != normalizer.export
        or getattr(facts, "CODEX_NORMALIZER_SOURCE_SHA256", None)
        != CODEX_X1_CX1_NORMALIZER_MODULE_SOURCE_SHA256
        or getattr(facts, "CODEX_NORMALIZER_CODE_SHA256", None)
        != CODEX_X1_CX1_NORMALIZER_CALLABLE_CODE_SHA256
    ):
        raise CodexX1VerificationError("CX1-02 source-authority normalizer binding was replaced")
    return identities


def _unit(
    *,
    kind: str,
    case_id: str,
    ordinal: int,
    item_ids: Sequence[str],
    expected: str,
    authority: object,
) -> CodexX1AuthorityUnit:
    payload = {
        "kind": kind,
        "case_id": case_id,
        "ordinal": ordinal,
        "item_ids": list(item_ids),
        "expected": expected,
        "authority": authority,
    }
    return CodexX1AuthorityUnit(
        unit_id=f"{kind}-{case_id.removeprefix('codex-v1-')}-{ordinal:02d}",
        case_id=case_id,
        item_ids=tuple(item_ids),
        expected=expected,
        authority_sha256=_canonical_sha256(payload),
    )


def derive_codex_x1_membership_v1(dataset: GoldenDataset) -> CodexX1Membership:
    """Derive all X-T1 membership and denominators from Golden case bodies."""

    if type(dataset) is not GoldenDataset:
        raise CodexX1VerificationError("membership requires the exact GoldenDataset contract")
    if (
        dataset.dataset_id != CODEX_GOLDEN_DATASET_ID
        or dataset.dataset_version != CODEX_GOLDEN_DATASET_VERSION
        or dataset.package_hash != CODEX_GOLDEN_PACKAGE_HASH
        or dataset.case_membership != CODEX_XB0_CASE_MEMBERSHIP
    ):
        raise CodexX1VerificationError("membership source is not released ordered Golden-v1")

    units: dict[str, list[CodexX1AuthorityUnit]] = {
        key: [] for key in _EXPECTED_AUTHORITY_DENOMINATORS
    }
    case_kinds: dict[str, set[str]] = defaultdict(set)
    by_id = {case.case_id: case for case in dataset.cases}
    for case in dataset.cases:
        ordered_truth = sorted(
            (truth for truth in case.event_truth if truth.order is not None),
            key=lambda truth: truth.order or 0,
        )
        if CodexMetric.EVENT_ORDER_ACCURACY_AT_10 in case.eligible_metrics:
            if len(ordered_truth) < 2:
                raise CodexX1VerificationError("event-order case lacks ordered Golden truth")
            units["event_order"].append(
                _unit(
                    kind="event-order",
                    case_id=case.case_id,
                    ordinal=1,
                    item_ids=[truth.item_id for truth in ordered_truth],
                    expected=">".join(truth.item_id for truth in ordered_truth),
                    authority=[truth.model_dump(mode="json") for truth in ordered_truth],
                )
            )
            case_kinds[case.case_id].add("event_order")

        if CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10 in case.eligible_metrics:
            calls: dict[str, dict[str, Any]] = defaultdict(dict)
            for truth in case.event_truth:
                if truth.call_id is not None and truth.kind in {"tool_call", "tool_result"}:
                    calls[truth.call_id][truth.kind] = truth
            complete = [
                (call_id, values)
                for call_id, values in sorted(calls.items())
                if set(values) == {"tool_call", "tool_result"}
            ]
            if not complete:
                raise CodexX1VerificationError("call-result case lacks a complete Golden pair")
            for ordinal, (call_id, values) in enumerate(complete, start=1):
                call = values["tool_call"]
                result = values["tool_result"]
                units["call_result"].append(
                    _unit(
                        kind="call-result",
                        case_id=case.case_id,
                        ordinal=ordinal,
                        item_ids=(call.item_id, result.item_id),
                        expected=call_id,
                        authority=[
                            call.model_dump(mode="json"),
                            result.model_dump(mode="json"),
                        ],
                    )
                )
            case_kinds[case.case_id].add("call_result")

        patch_judgments = [
            judgment for judgment in case.item_judgments if judgment.patch_applied is not None
        ]
        if CodexMetric.PATCH_ACCURACY_AT_10 in case.eligible_metrics:
            if not patch_judgments:
                raise CodexX1VerificationError("patch case lacks a Golden patch judgment")
            for ordinal, judgment in enumerate(patch_judgments, start=1):
                units["patch"].append(
                    _unit(
                        kind="patch",
                        case_id=case.case_id,
                        ordinal=ordinal,
                        item_ids=(judgment.item_id,),
                        expected="applied" if judgment.patch_applied else "failed",
                        authority=judgment.model_dump(mode="json"),
                    )
                )
            case_kinds[case.case_id].add("patch")

        validation_judgments = [
            judgment for judgment in case.item_judgments if judgment.validation_status is not None
        ]
        if CodexMetric.VALIDATION_ACCURACY_AT_10 in case.eligible_metrics:
            if not validation_judgments:
                raise CodexX1VerificationError("validation case lacks a Golden validation judgment")
            for ordinal, judgment in enumerate(validation_judgments, start=1):
                units["validation"].append(
                    _unit(
                        kind="validation",
                        case_id=case.case_id,
                        ordinal=ordinal,
                        item_ids=(judgment.item_id,),
                        expected=judgment.validation_status or "unknown",
                        authority=judgment.model_dump(mode="json"),
                    )
                )
            case_kinds[case.case_id].add("validation")

        false_validation_judgments = [
            judgment
            for judgment in case.item_judgments
            if judgment.false_validation_category is not None
        ]
        if CodexMetric.FALSE_VALIDATED_RATE_AT_10 in case.eligible_metrics:
            if len(false_validation_judgments) != 1:
                raise CodexX1VerificationError(
                    "false-validation case requires exactly one Golden judgment"
                )
            judgment = false_validation_judgments[0]
            units["false_validation"].append(
                _unit(
                    kind="false-validation",
                    case_id=case.case_id,
                    ordinal=1,
                    item_ids=(judgment.item_id,),
                    expected=f"not_passed:{judgment.false_validation_category.value}",
                    authority=judgment.model_dump(mode="json"),
                )
            )
            case_kinds[case.case_id].add("false_validation")

        output_truth = [
            truth for truth in case.event_truth if truth.truncated or truth.warning is not None
        ]
        for ordinal, truth in enumerate(output_truth, start=1):
            units["output_window"].append(
                _unit(
                    kind="output-window",
                    case_id=case.case_id,
                    ordinal=ordinal,
                    item_ids=(truth.item_id,),
                    expected="bounded_observed_prefix_only",
                    authority=truth.model_dump(mode="json"),
                )
            )
            case_kinds[case.case_id].add("output_window")

        error_truth = [
            truth
            for truth in case.event_truth
            if truth.kind == "tool_result" and truth.status == "failed"
        ]
        for ordinal, truth in enumerate(error_truth, start=1):
            units["error_tail"].append(
                _unit(
                    kind="error-tail",
                    case_id=case.case_id,
                    ordinal=ordinal,
                    item_ids=(truth.item_id,),
                    expected="failed_result_tail_identity",
                    authority=truth.model_dump(mode="json"),
                )
            )
            case_kinds[case.case_id].add("error_tail")

    denominators = {key: len(value) for key, value in units.items()}
    if denominators != _EXPECTED_AUTHORITY_DENOMINATORS:
        raise CodexX1VerificationError(f"Golden-derived X-T1 denominators changed: {denominators}")
    eligible_case_ids = tuple(sorted(case_kinds))
    cases = tuple(
        CodexX1CaseAuthority(
            case_id=case_id,
            slice=by_id[case_id].slice.value,
            case_authority_sha256=by_id[case_id].canonical_sha256(),
            event_truth_sha256=_canonical_sha256(
                [truth.model_dump(mode="json") for truth in by_id[case_id].event_truth]
            ),
            judgments_sha256=_canonical_sha256(
                [judgment.model_dump(mode="json") for judgment in by_id[case_id].item_judgments]
            ),
            unit_kinds=tuple(sorted(case_kinds[case_id])),
        )
        for case_id in eligible_case_ids
    )
    payload = {
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "package_hash": dataset.package_hash,
        "eligible_case_ids": list(eligible_case_ids),
        "denominators": denominators,
        "units": {
            key: [unit.model_dump(mode="json") for unit in value] for key, value in units.items()
        },
        "cases": [case.model_dump(mode="json") for case in cases],
    }
    return CodexX1Membership(
        eligible_case_ids=eligible_case_ids,
        denominators=denominators,
        units={key: tuple(value) for key, value in units.items()},
        cases=cases,
        content_sha256=_canonical_sha256(payload),
    )


def _correction_prediction_rows(correction_root: Path) -> tuple[dict[str, Any], ...]:
    verification = verify_codex_baseline_correction_artifact_v1(correction_root)
    if (
        verification.correction_id != CODEX_XB0_CORRECTION_ID
        or verification.correction_uri != CODEX_XB0_CORRECTION_URI
        or verification.artifact_set_hash != CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH
        or verification.status != "VERIFIED_CORRECTION_NON_QUALIFIED"
    ):
        raise CodexX1VerificationError("X-B0 correction identity or posture mismatch")
    rows = tuple(_read_canonical_jsonl(correction_root / "predictions.jsonl"))
    if (
        len(rows) != CODEX_XB0_CASE_COUNT
        or tuple(row.get("case_id") for row in rows) != CODEX_XB0_CASE_MEMBERSHIP
    ):
        raise CodexX1VerificationError("X-B0 prediction membership/order mismatch")
    for row in rows:
        if (
            row.get("dataset_id") != CODEX_XB0_GOLDEN_DATASET_ID
            or row.get("dataset_version") != CODEX_XB0_GOLDEN_DATASET_VERSION
            or row.get("package_hash") != CODEX_XB0_GOLDEN_PACKAGE_HASH
            or row.get("run_id") != CODEX_XB0_SOURCE_RUN_ID
        ):
            raise CodexX1VerificationError("X-B0 prediction authority mismatch")
    return rows


def recompute_xb0_state_truth_v1(
    dataset: GoldenDataset,
    membership: CodexX1Membership,
    correction_root: str | Path,
) -> tuple[CodexX1BaselineAnchor, ...]:
    """Recompute baseline state truth from immutable predictions and judgments."""

    rows = _correction_prediction_rows(Path(correction_root))
    predictions = {row["case_id"]: row for row in rows}
    cases = {case.case_id: case for case in dataset.cases}
    counts = Counter()
    denominators = Counter()

    for unit in membership.units["event_order"]:
        ranked = {item["item_id"]: item["rank"] for item in predictions[unit.case_id]["results"]}
        expected_ranks = [ranked.get(item_id) for item_id in unit.item_ids]
        denominators["event_order"] += 1
        if all(rank is not None for rank in expected_ranks) and expected_ranks == sorted(
            expected_ranks
        ):
            counts["event_order"] += 1

    for unit in membership.units["call_result"]:
        ranked_ids = {item["item_id"] for item in predictions[unit.case_id]["results"]}
        denominators["call_result"] += 1
        if set(unit.item_ids) <= ranked_ids:
            counts["call_result"] += 1

    for kind in ("patch", "validation", "false_validation"):
        for unit in membership.units[kind]:
            ranked_ids = {item["item_id"] for item in predictions[unit.case_id]["results"]}
            denominators[kind] += 1
            if unit.item_ids[0] in ranked_ids:
                counts[kind] += 1

    anchors = tuple(
        CodexX1BaselineAnchor(
            name=name,
            numerator=counts[name],
            denominator=denominators[name],
        )
        for name in _EXPECTED_BASELINE_ANCHORS
    )
    if {
        item.name: (item.numerator, item.denominator) for item in anchors
    } != _EXPECTED_BASELINE_ANCHORS:
        raise CodexX1VerificationError(
            "X-B0 complete truth differs from fixed 1/3,1/2,3/4,7/10,1/5 anchors"
        )
    if set(cases) != set(CODEX_XB0_CASE_MEMBERSHIP):
        raise CodexX1VerificationError("Golden cases changed during truth recomputation")
    return anchors


def prepare_codex_x1_v1(
    *,
    golden_root: str | Path | None = None,
    correction_root: str | Path | None = None,
    gate_review: str | Path | None = None,
) -> CodexX1Preparation:
    """Perform the read-only PREPARED probe; never create a Run or metrics."""

    gate_path = Path(gate_review) if gate_review is not None else _default_gate_review()
    try:
        mode = gate_path.lstat().st_mode
        gate_path.resolve(strict=True)
        gate_text = gate_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CodexX1VerificationError("Gate 10 review is unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise CodexX1VerificationError("Gate 10 review must be a real regular file")
    gate = parse_codex_x1_gate_review_v1(gate_text)
    dataset = load_codex_golden_v1(golden_root or _default_golden_root())
    membership = derive_codex_x1_membership_v1(dataset)
    correction = (
        Path(correction_root) if correction_root is not None else _default_correction_root()
    )
    components = cx1_01_component_identities_v1()
    try:
        facts_components = cx1_02_component_identities_v1()
    except CodexX1VerificationError:
        facts_components = tuple(
            CodexX1ComponentIdentity.model_validate(row) for row in _FIXED_CX1_02_AUTHORITY_ROWS
        )
        if (
            _canonical_sha256([item.model_dump(mode="json") for item in facts_components])
            != CODEX_X1_CX1_02_COMPONENT_SET_HASH
        ):
            raise
        facts_identity_matches = False
    else:
        facts_identity_matches = True
    anchors = recompute_xb0_state_truth_v1(dataset, membership, correction)
    production_authorized = gate.production_authorized and facts_identity_matches
    arms = (
        CodexX1ArmReadiness(
            arm=CodexX1Arm.BASELINE_CONTROL,
            availability=Availability.AVAILABLE,
            eligible_case_ids=membership.eligible_case_ids,
            membership_sha256=membership.content_sha256,
            component_set_hash=CODEX_PRODUCTION_AUTHORITY_DIGEST,
        ),
        CodexX1ArmReadiness(
            arm=CodexX1Arm.CX1_NORMALIZED,
            availability=Availability.PROVISIONAL,
            eligible_case_ids=membership.eligible_case_ids,
            membership_sha256=membership.content_sha256,
            component_set_hash=CODEX_X1_CX1_01_COMPONENT_SET_HASH,
            reason="Engineering identity is ready; released treatment has not executed.",
        ),
        CodexX1ArmReadiness(
            arm=CodexX1Arm.CX1_DERIVED_FACTS,
            availability=(
                Availability.AVAILABLE if production_authorized else Availability.UNAVAILABLE
            ),
            eligible_case_ids=membership.eligible_case_ids,
            membership_sha256=membership.content_sha256,
            component_set_hash=CODEX_X1_CX1_02_COMPONENT_SET_HASH,
            reason=(
                None
                if production_authorized
                else (
                    ("Latest CX1-02 joint Gate is not an exact P0=0/P1=0 isolated-production PASS.")
                    if not gate.production_authorized
                    else "CX1-02 production component identity does not match authority."
                )
            ),
        ),
    )
    return CodexX1Preparation(
        production_execution=("AUTHORIZED" if production_authorized else "NOT_AUTHORIZED"),
        gate=gate,
        membership=membership,
        cx1_01_components=components,
        cx1_02_components=facts_components,
        cx1_02_identity_status=("MATCH" if facts_identity_matches else "MISMATCH"),
        cx1_02_versions=CodexX1FactsVersions(),
        baseline_anchors=anchors,
        arms=arms,
    )


def _prediction_maps(
    records: Sequence[CodexX1TreatmentRecord],
) -> dict[str, CodexX1TreatmentRecord]:
    mapped: dict[str, CodexX1TreatmentRecord] = {}
    for record in records:
        if record.case_id in mapped:
            raise CodexX1VerificationError("duplicate treatment case record")
        mapped[record.case_id] = record
    return mapped


def _ratio_metric(
    name: CodexX1MetricName,
    numerator: int,
    denominator: int,
    *,
    status: Availability = Availability.AVAILABLE,
    reason: str | None = None,
) -> CodexX1MetricResult:
    if denominator == 0:
        return CodexX1MetricResult(
            name=name,
            status=Availability.UNAVAILABLE,
            numerator=None,
            denominator=0,
            value=None,
            unit="ratio",
            reason=reason or "no scored denominator",
        )
    return CodexX1MetricResult(
        name=name,
        status=status,
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator,
        unit="ratio",
        reason=reason,
    )


def _count_metric(
    name: CodexX1MetricName,
    value: int,
    *,
    denominator: int,
) -> CodexX1MetricResult:
    return CodexX1MetricResult(
        name=name,
        status=Availability.AVAILABLE,
        numerator=value,
        denominator=denominator,
        value=value,
        unit="count",
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate_codex_x1_records_v1(
    *,
    membership: CodexX1Membership,
    records: Sequence[CodexX1TreatmentRecord],
    arm: Literal[
        CodexX1Arm.CX1_NORMALIZED,
        CodexX1Arm.CX1_DERIVED_FACTS,
    ] = CodexX1Arm.CX1_NORMALIZED,
    require_complete_membership: bool = True,
) -> CodexX1Evaluation:
    """Recompute every treatment metric from canonical per-case records."""

    if any(type(record) is not CodexX1TreatmentRecord for record in records):
        raise CodexX1VerificationError("treatment records require exact frozen contracts")
    mapped = _prediction_maps(records)
    expected_ids = set(membership.eligible_case_ids)
    if require_complete_membership and set(mapped) != expected_ids:
        raise CodexX1VerificationError("treatment case membership differs from frozen X-T1")
    if not set(mapped) <= expected_ids:
        raise CodexX1VerificationError("treatment contains an ineligible or selected case")
    for record in records:
        if record.arm != arm or record.membership_sha256 != membership.content_sha256:
            raise CodexX1VerificationError("treatment arm or membership identity mismatch")

    golden_links = {
        (
            unit.case_id,
            unit.expected,
            unit.item_ids[0],
            unit.item_ids[1],
        )
        for unit in membership.units["call_result"]
        if unit.case_id in mapped
    }
    predicted_links = {
        (record.case_id, link.call_id, link.call_item_id, link.result_item_id)
        for record in records
        for link in record.links
    }
    link_tp = len(golden_links & predicted_links)

    expected_patches = {
        (unit.case_id, unit.item_ids[0]): unit.expected
        for unit in membership.units["patch"]
        if unit.case_id in mapped
    }
    predicted_patches = {
        (record.case_id, state.item_id): state.status
        for record in records
        for state in record.patches
    }
    patch_correct = sum(
        predicted_patches.get(identity) == expected
        for identity, expected in expected_patches.items()
    )

    expected_validations = {
        (unit.case_id, unit.item_ids[0]): unit.expected
        for unit in membership.units["validation"]
        if unit.case_id in mapped
    }
    predicted_validations = {
        (record.case_id, state.item_id): state.status
        for record in records
        for state in record.validations
        if state.status in {"passed", "failed"}
    }
    validation_tp = sum(
        predicted_validations.get(identity) == expected
        for identity, expected in expected_validations.items()
    )
    validation_recall_denominator = len(expected_validations)

    false_validation_units = [
        unit for unit in membership.units["false_validation"] if unit.case_id in mapped
    ]
    false_validated = sum(
        predicted_validations.get((unit.case_id, unit.item_ids[0])) == "passed"
        for unit in false_validation_units
    )
    validation_precision_denominator = (
        sum(identity in expected_validations for identity in predicted_validations)
        + false_validated
    )

    event_units = [unit for unit in membership.units["event_order"] if unit.case_id in mapped]
    event_correct = sum(
        tuple(item for item in mapped[unit.case_id].ordered_item_ids if item in set(unit.item_ids))
        == unit.item_ids
        for unit in event_units
    )

    all_result_ids = {
        (record.case_id, link.result_item_id) for record in records for link in record.links
    }
    observed_result_ids = {
        (record.case_id, item_id)
        for record in records
        for item_id in record.observed_result_item_ids
    }
    unlinked_count = len(observed_result_ids - all_result_ids)
    unknown_count = sum(
        state.status in {"unknown", "target_unknown", "mentioned", "invoked"}
        for record in records
        for state in (*record.patches, *record.validations)
    )

    output_units = [unit for unit in membership.units["output_window"] if unit.case_id in mapped]
    output_covered = sum(
        unit.item_ids[0] in mapped[unit.case_id].output_window_item_ids for unit in output_units
    )
    error_units = [unit for unit in membership.units["error_tail"] if unit.case_id in mapped]
    error_covered = sum(
        unit.item_ids[0] in mapped[unit.case_id].error_tail_item_ids for unit in error_units
    )
    security_counts = Counter(
        finding.kind for record in records for finding in record.security_findings
    )
    latencies = [record.latency_ms for record in records]
    memory_peaks = [record.memory_peak_bytes for record in records]

    metrics: list[CodexX1MetricResult] = [
        _ratio_metric(
            CodexX1MetricName.CALL_RESULT_PAIRING_PRECISION,
            link_tp,
            len(predicted_links),
            reason="no predicted call-result links" if not predicted_links else None,
        ),
        _ratio_metric(
            CodexX1MetricName.CALL_RESULT_PAIRING_RECALL,
            link_tp,
            len(golden_links),
        ),
        _ratio_metric(
            CodexX1MetricName.PATCH_STATUS_ACCURACY,
            patch_correct,
            len(expected_patches),
        ),
        _ratio_metric(
            CodexX1MetricName.VALIDATION_PRECISION,
            validation_tp,
            validation_precision_denominator,
            reason=(
                "no predicted terminal validation states"
                if validation_precision_denominator == 0
                else None
            ),
        ),
        _ratio_metric(
            CodexX1MetricName.VALIDATION_RECALL,
            validation_tp,
            validation_recall_denominator,
        ),
    ]
    if validation_precision_denominator and validation_recall_denominator:
        precision = validation_tp / validation_precision_denominator
        recall = validation_tp / validation_recall_denominator
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        metrics.append(
            CodexX1MetricResult(
                name=CodexX1MetricName.VALIDATION_F1,
                status=Availability.AVAILABLE,
                numerator=f1,
                denominator=1,
                value=f1,
                unit="ratio",
            )
        )
    else:
        metrics.append(
            CodexX1MetricResult(
                name=CodexX1MetricName.VALIDATION_F1,
                status=Availability.UNAVAILABLE,
                numerator=None,
                denominator=0,
                value=None,
                unit="ratio",
                reason="precision or recall has no scored denominator",
            )
        )
    metrics.extend(
        [
            _ratio_metric(
                CodexX1MetricName.FALSE_VALIDATED_RATE,
                false_validated,
                len(false_validation_units),
            ),
            _ratio_metric(
                CodexX1MetricName.EVENT_ORDER_ACCURACY,
                event_correct,
                len(event_units),
            ),
            _count_metric(
                CodexX1MetricName.UNLINKED_RESULT_COUNT,
                unlinked_count,
                denominator=len(records),
            ),
            _count_metric(
                CodexX1MetricName.UNKNOWN_STATE_COUNT,
                unknown_count,
                denominator=len(records),
            ),
            _ratio_metric(
                CodexX1MetricName.OUTPUT_WINDOW_COVERAGE,
                output_covered,
                len(output_units),
            ),
            _ratio_metric(
                CodexX1MetricName.ERROR_TAIL_COVERAGE,
                error_covered,
                len(error_units),
                status=Availability.PROVISIONAL,
                reason=(
                    "Golden identifies failed result tails but not byte-complete tail content."
                ),
            ),
            _count_metric(
                CodexX1MetricName.REASONING_LEAKAGE_COUNT,
                security_counts["reasoning"],
                denominator=len(records),
            ),
            _count_metric(
                CodexX1MetricName.SECRET_LEAKAGE_COUNT,
                security_counts["secret"],
                denominator=len(records),
            ),
            _count_metric(
                CodexX1MetricName.ACL_LEAKAGE_COUNT,
                security_counts["acl"],
                denominator=len(records),
            ),
        ]
    )
    for name, value, unit in (
        (
            CodexX1MetricName.LATENCY_P50_MS,
            _percentile(latencies, 0.50) if latencies else None,
            "milliseconds",
        ),
        (
            CodexX1MetricName.LATENCY_P95_MS,
            _percentile(latencies, 0.95) if latencies else None,
            "milliseconds",
        ),
        (
            CodexX1MetricName.MEMORY_PEAK_BYTES,
            max(memory_peaks) if memory_peaks else None,
            "bytes",
        ),
    ):
        if value is None:
            metrics.append(
                CodexX1MetricResult(
                    name=name,
                    status=Availability.UNAVAILABLE,
                    numerator=None,
                    denominator=0,
                    value=None,
                    unit=unit,
                    reason="no treatment measurements",
                )
            )
        else:
            metrics.append(
                CodexX1MetricResult(
                    name=name,
                    status=Availability.AVAILABLE,
                    numerator=value,
                    denominator=len(records),
                    value=value,
                    unit=unit,
                )
            )
    hard_gate_names = (
        CodexX1MetricName.FALSE_VALIDATED_RATE,
        CodexX1MetricName.REASONING_LEAKAGE_COUNT,
        CodexX1MetricName.SECRET_LEAKAGE_COUNT,
        CodexX1MetricName.ACL_LEAKAGE_COUNT,
    )
    by_name = {metric.name: metric for metric in metrics}
    failures = tuple(
        name.value
        for name in hard_gate_names
        if by_name[name].numerator is not None and float(by_name[name].numerator) != 0
    )
    return CodexX1Evaluation(
        arm=arm,
        status=(
            Availability.AVAILABLE if require_complete_membership else Availability.PROVISIONAL
        ),
        qualification_status=("NON_QUALIFIED" if failures else "QUALIFIED"),
        membership_sha256=membership.content_sha256,
        evaluated_case_ids=tuple(sorted(mapped)),
        metrics=tuple(metrics),
        hard_gate_failures=failures,
    )


def _scan_text(name: str, text: str) -> list[str]:
    findings: list[str] = []
    if _CREDENTIAL_RE.search(text):
        findings.append(f"{name}:credential")
    if _ABSOLUTE_PATH_RE.search(text):
        findings.append(f"{name}:absolute_path")
    lowered = text.lower()
    if any(
        token in lowered
        for token in (
            '"reasoning_content"',
            '"chain_of_thought"',
            '"agent_reasoning"',
        )
    ):
        findings.append(f"{name}:reasoning")
    return findings


def _scan_artifact(directory: Path) -> list[str]:
    findings: list[str] = []
    for name in _SECURITY_INPUT_FILES:
        path = directory / name
        if name.lower().endswith(_FORBIDDEN_ARTIFACT_SUFFIXES) or _DB_NAME_RE.search(name):
            findings.append(f"{name}:database_or_compiled")
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            findings.append(f"{name}:unreadable")
            continue
        if raw.startswith(b"SQLite format 3\x00"):
            findings.append(f"{name}:sqlite_signature")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(f"{name}:non_utf8")
            continue
        findings.extend(_scan_text(name, text))
        try:
            _assert_finite(_strict_json_loads(raw if name.endswith(".json") else b"[]", label=name))
        except CodexX1VerificationError as exc:
            if name.endswith(".json"):
                findings.append(f"{name}:nonfinite_or_invalid:{exc}")
    return findings


def _file_records(directory: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for name in _CHECKSUM_TARGET_FILES:
        raw = (directory / name).read_bytes()
        records[name] = {"sha256": _sha256_bytes(raw), "size": len(raw)}
    return records


def _artifact_set_hash(records: Mapping[str, Mapping[str, object]]) -> str:
    return _canonical_sha256(records)


def _require_artifact_tree(directory: Path) -> Path:
    try:
        mode = directory.lstat().st_mode
    except OSError as exc:
        raise CodexX1VerificationError("artifact directory is unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise CodexX1VerificationError("artifact root must be a real directory")
    try:
        resolved = directory.resolve(strict=True)
    except OSError as exc:
        raise CodexX1VerificationError("artifact root cannot be resolved") from exc
    entries: set[str] = set()
    for path in directory.iterdir():
        item_mode = path.lstat().st_mode
        if stat.S_ISLNK(item_mode) or not stat.S_ISREG(item_mode):
            raise CodexX1VerificationError("artifact may contain only top-level regular files")
        entries.add(path.name)
    if entries != set(CODEX_X1_CANONICAL_ARTIFACT_FILES):
        raise CodexX1VerificationError("artifact canonical file membership mismatch")
    return resolved


def _timed_call(callable_: Any) -> tuple[Any, float, int]:
    """Measure one bounded production component call without retaining samples."""

    tracemalloc.start()
    started = time.perf_counter()
    try:
        value = callable_()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1_000.0
        tracemalloc.stop()
    return value, elapsed_ms, peak


def _adapter_event_number(locator: str) -> int:
    match = re.search(r"#event=([1-9][0-9]*)$", locator)
    if match is None:
        raise CodexX1VerificationError("adapter item lacks an observable event locator")
    return int(match.group(1))


def _observable_item_id(adapter_item_id: str) -> str:
    marker = "/item/"
    if marker not in adapter_item_id:
        raise CodexX1VerificationError("adapter item identity is not observable")
    item_id = unquote(adapter_item_id.rsplit(marker, 1)[1])
    if not item_id or "/" in item_id:
        raise CodexX1VerificationError("adapter item identity is not a safe source segment")
    return item_id


def _command_argv(content: str) -> tuple[str, ...]:
    command = content.splitlines()[0].removeprefix("$ ").strip()
    try:
        argv = tuple(shlex.split(command, posix=True))
    except ValueError:
        return ()
    return argv[:64]


def _copy_released_adapter_fixture(
    request: CodexX1RunRequest,
) -> tuple[Path, dict[str, Any]]:
    """Copy only immutable session/index bytes into the isolated system root."""

    manifest = _read_canonical_json(request.golden_root / "manifest.json")
    if (
        manifest.get("dataset_id") != CODEX_GOLDEN_DATASET_ID
        or manifest.get("dataset_version") != CODEX_GOLDEN_DATASET_VERSION
        or manifest.get("package_hash") != CODEX_GOLDEN_PACKAGE_HASH
        or manifest.get("case_count") != CODEX_GOLDEN_CASE_COUNT
    ):
        raise CodexX1VerificationError("released adapter fixture identity mismatch")
    target = request.isolated_root / "released-fixture"
    if target.exists() or target.is_symlink():
        raise CodexX1VerificationError("isolated released fixture path is not fresh")
    target.mkdir()
    copied = 0
    try:
        files = manifest.get("files")
        if not isinstance(files, list):
            raise CodexX1VerificationError("released manifest file list is invalid")
        for entry in files:
            if not isinstance(entry, dict) or entry.get("kind") not in {
                "session",
                "session_index",
            }:
                continue
            relative = PurePosixPath(str(entry.get("path", "")))
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                raise CodexX1VerificationError("released fixture path is not portable")
            source = request.golden_root.joinpath(*relative.parts)
            raw = source.read_bytes()
            if (
                len(raw) != entry.get("size")
                or _sha256_bytes(raw) != entry.get("sha256")
                or source.is_symlink()
            ):
                raise CodexX1VerificationError("released fixture byte authority mismatch")
            destination = target.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            copied += 1
        if copied != 9:
            raise CodexX1VerificationError("isolated fixture must contain eight sessions and index")
    except Exception:
        shutil.rmtree(target)
        raise
    return target, manifest


def _parse_released_adapter_items(
    fixture: Path,
    manifest: Mapping[str, Any],
    raw_sqlite_path: Path,
) -> tuple[tuple[Any, ...], dict[str, str], dict[str, int]]:
    """Read the isolated fixture with the exact production Codex adapter."""

    from evidence_rag.codex_adapter import CodexSessionAdapter
    from evidence_rag.config import Settings

    adapter = CodexSessionAdapter(
        Settings(
            data_dir=fixture,
            database_path=raw_sqlite_path,
            repository_cache=fixture,
            web_dir=fixture,
            allowed_local_roots=(fixture,),
            max_codex_item_chars=16_000,
        )
    )
    titles = adapter.read_titles(fixture)
    parsed_items: list[Any] = []
    source_files: dict[str, str] = {}
    counters = Counter()
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise CodexX1VerificationError("released session manifest is invalid")
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("kind") != "session":
            continue
        relative = PurePosixPath(str(entry["path"]))
        parsed = adapter.parse(
            fixture.joinpath(*relative.parts),
            source_root=fixture,
            source_id="codex-source://golden-v1",
            generation_id="codex-generation://golden-v1",
            project_id="project-rag",
            project_path=None,
            acl_ref="project:project-rag",
            titles=titles,
        )
        if parsed is None:
            raise CodexX1VerificationError("production adapter rejected a released session")
        actual_ids = tuple(item.id for item in parsed.items)
        expected_ids = tuple(entry.get("item_ids", ()))
        expected_threads = tuple(entry.get("thread_ids", ()))
        if set(actual_ids) != set(expected_ids) or expected_threads != (parsed.thread.thread_id,):
            raise CodexX1VerificationError("adapter output differs from released manifest")
        for item in parsed.items:
            if item.id in source_files:
                raise CodexX1VerificationError("production adapter emitted duplicate item identity")
            source_files[item.id] = relative.as_posix()
            parsed_items.append(item)
        counters.update(parsed.counters)
    if counters["sessions"] != 8 or counters["items"] != 54 or len(parsed_items) != 54:
        raise CodexX1VerificationError("released adapter inventory must be exactly 8/54")
    return tuple(parsed_items), source_files, dict(counters)


def _observable_sources_from_adapter(
    adapter_items: Sequence[Any],
) -> tuple[
    dict[str, tuple[Any, ...]],
    dict[tuple[str, str, str], str],
    dict[str, tuple[Any, ...]],
    dict[tuple[str, str], str],
]:
    """Construct exact Observable inputs without consulting Golden labels."""

    from evidence_rag.rag.sources.codex.contracts import (
        EventScope,
        ObservableCodexItem,
        canonical_codex_locator_v1,
    )

    call_types: dict[tuple[str, str, str], str] = {}
    for item in adapter_items:
        call_id = item.metadata.get("call_id")
        if not call_id:
            continue
        if item.item_type == "Patch":
            call_types[(item.thread_id, item.turn_id, str(call_id))] = "patch_apply"
        elif item.item_type == "CommandExecution":
            call_types[(item.thread_id, item.turn_id, str(call_id))] = "command"
        elif item.item_type == "ToolCall":
            call_types[(item.thread_id, item.turn_id, str(call_id))] = str(
                item.metadata.get("tool_name") or "tool"
            ).lower()

    by_thread: dict[str, list[Any]] = defaultdict(list)
    source_identity: dict[tuple[str, str, str], str] = {}
    observable_by_adapter: dict[str, list[Any]] = defaultdict(list)
    derived_validations: dict[tuple[str, str], str] = {}
    for item in adapter_items:
        if item.item_type == "DevelopmentEpisode":
            continue
        if item.item_type == "ValidationResult":
            derived_from = item.metadata.get("derived_from_item")
            if isinstance(derived_from, str) and derived_from:
                derived_validations[(item.thread_id, derived_from)] = item.id
        scope = EventScope(
            project_id="project-rag",
            source_id=item.source_id,
            generation_id=item.generation_id,
            thread_id=item.thread_id,
            turn_id=item.turn_id,
            acl_ref=item.acl_ref,
        )
        raw_item_id = _observable_item_id(item.id)
        event_number = _adapter_event_number(item.source_locator)
        observable_order = (event_number - 1) * 2
        metadata_call_id = item.metadata.get("call_id")
        call_id = str(metadata_call_id) if metadata_call_id else None
        synthetic_direct_result = (
            item.item_type == "CommandExecution"
            and item.metadata.get("source_event_type") == "command_execution"
            and type(item.metadata.get("exit_code")) is int
        )
        if synthetic_direct_result and call_id is None:
            call_id = f"adapter-command-{item.item_id}"
        values: dict[str, Any] = {
            "item_id": raw_item_id,
            "source_locator": canonical_codex_locator_v1(
                scope,
                raw_item_id,
                observable_order,
            ),
            "scope": scope,
            "observable_order": observable_order,
            "item_type": item.item_type,
            "role": item.role,
            "call_id": call_id,
            "targets": tuple(str(value) for value in item.metadata.get("paths", ()) if value),
        }
        if item.item_type == "CommandExecution":
            values["call_type"] = "command"
            values["command_argv"] = _command_argv(item.content)
        elif item.item_type == "Patch":
            values["call_type"] = "patch_apply"
            values["tool_name"] = "apply_patch"
        elif item.item_type == "ToolCall":
            values["call_type"] = str(item.metadata.get("tool_name") or "tool").lower()
            values["tool_name"] = values["call_type"]
        elif item.item_type in {"ToolResult", "CommandResult", "PatchResult"}:
            exact_call_type = call_types.get((item.thread_id, item.turn_id, call_id or ""))
            if exact_call_type is not None:
                values["call_type"] = exact_call_type
            payload: dict[str, Any] = {"output": item.content}
            exit_code = item.metadata.get("exit_code")
            if type(exit_code) is int:
                payload["exit_code"] = exit_code
            values["payload"] = payload
        elif item.item_type == "FileChange":
            success = item.metadata.get("success")
            if type(success) is bool:
                values["success"] = success
        observable = ObservableCodexItem(**values)
        by_thread[item.thread_id].append(observable)
        source_identity[(item.thread_id, item.turn_id, raw_item_id)] = item.id
        observable_by_adapter[item.id].append(observable)

        if synthetic_direct_result:
            result_id = f"CommandResult:{item.item_id}"
            output = "\n".join(item.content.splitlines()[1:])
            result = ObservableCodexItem(
                item_id=result_id,
                source_locator=canonical_codex_locator_v1(
                    scope,
                    result_id,
                    observable_order + 1,
                ),
                scope=scope,
                observable_order=observable_order + 1,
                item_type="CommandResult",
                call_id=call_id,
                call_type="command",
                payload={
                    "exit_code": item.metadata["exit_code"],
                    "output": output,
                },
            )
            by_thread[item.thread_id].append(result)
            source_identity[(item.thread_id, item.turn_id, result_id)] = item.id
            observable_by_adapter[item.id].append(result)

    return (
        {key: tuple(value) for key, value in by_thread.items()},
        source_identity,
        {key: tuple(value) for key, value in observable_by_adapter.items()},
        derived_validations,
    )


def _translate_source_item(
    source_identity: Mapping[tuple[str, str, str], str],
    scope: Any,
    source_item_id: str,
) -> str:
    try:
        return source_identity[(scope.thread_id, scope.turn_id, source_item_id)]
    except KeyError as exc:
        raise CodexX1VerificationError("normalized evidence is outside adapter authority") from exc


def _treatment_source_record_content(record: CodexX1TreatmentRecord) -> dict[str, Any]:
    payload = record.model_dump(mode="json")
    return {
        key: payload[key]
        for key in (
            "arm",
            "case_id",
            "membership_sha256",
            "ordered_item_ids",
            "links",
            "patches",
            "validations",
            "observed_result_item_ids",
            "output_window_item_ids",
            "error_tail_item_ids",
            "source_evidence",
            "event_evidence",
            "window_evidence",
            "normalization_content_sha256",
            "source_set_sha256",
            "publication_id",
            "diagnostic_codes",
        )
    }


def _treatment_records_from_results(
    *,
    preparation: CodexX1Preparation,
    arm: Literal[CodexX1Arm.CX1_NORMALIZED, CodexX1Arm.CX1_DERIVED_FACTS],
    results: Mapping[str, Any],
    source_items: Mapping[str, tuple[Any, ...]],
    source_identity: Mapping[tuple[str, str, str], str],
    observable_by_adapter: Mapping[str, tuple[Any, ...]],
    derived_validations: Mapping[tuple[str, str], str],
    adapter_by_id: Mapping[str, Any],
    source_files: Mapping[str, str],
    measurements: Mapping[str, tuple[float, int]],
    publications: Mapping[str, Any] | None = None,
) -> tuple[CodexX1TreatmentRecord, ...]:
    """Slice global production outputs into canonical per-case predictions."""

    from evidence_rag.rag.sources.codex.contracts import (
        NormalizedPatchEvent,
        NormalizedValidationEvent,
    )

    membership = preparation.membership
    authority_ids: dict[str, set[str]] = defaultdict(set)
    kinds_by_case: dict[str, dict[str, tuple[CodexX1AuthorityUnit, ...]]] = defaultdict(dict)
    for kind, units in membership.units.items():
        grouped: dict[str, list[CodexX1AuthorityUnit]] = defaultdict(list)
        for unit in units:
            grouped[unit.case_id].append(unit)
            authority_ids[unit.case_id].update(unit.item_ids)
        for case_id, case_units in grouped.items():
            kinds_by_case[case_id][kind] = tuple(case_units)

    translated_events: list[tuple[Any, tuple[str, ...], CodexX1EventEvidence]] = []
    patch_predictions: dict[str, str] = {}
    validation_predictions: dict[str, str] = {}
    for thread_id, result in results.items():
        for event in result.events:
            evidence_pairs = tuple(
                dict.fromkeys(
                    (
                        _translate_source_item(
                            source_identity,
                            event.scope,
                            evidence.source_item_id,
                        ),
                        evidence.source_locator,
                    )
                    for evidence in event.evidence
                )
            )
            translated_ids = tuple(pair[0] for pair in evidence_pairs)
            state = getattr(event, "state", None)
            state_value = state.value if state is not None else None
            history = tuple(value.value for value in getattr(event, "state_history", ()))
            audit = CodexX1EventEvidence(
                event_id=event.event_id,
                kind=event.kind.value,
                role=event.role.value,
                state=state_value,
                state_history=history,
                reason_code=event.reason_code.value,
                source_item_ids=translated_ids,
                source_locators=tuple(pair[1] for pair in evidence_pairs),
            )
            translated_events.append((event, translated_ids, audit))
            if isinstance(event, NormalizedPatchEvent):
                predicted = {
                    "applied": "applied",
                    "failed": "failed",
                }.get(event.state.value, "unknown")
                for item_id in translated_ids:
                    if adapter_by_id[item_id].item_type == "Patch":
                        patch_predictions[item_id] = predicted
            elif getattr(event, "observation", None) is not None and (
                event.observation.value == "file_change"
            ):
                for item_id in translated_ids:
                    patch_predictions[item_id] = "applied"
            if isinstance(event, NormalizedValidationEvent):
                status = {
                    "passed": "passed",
                    "failed": "failed",
                    "target_unknown": "target_unknown",
                    "mentioned": "mentioned",
                }.get(event.state.value, "invoked")
                command_sources = [
                    adapter_by_id[item_id]
                    for item_id in translated_ids
                    if adapter_by_id[item_id].item_type == "CommandExecution"
                ]
                for command in command_sources:
                    target = derived_validations.get((thread_id, command.item_id))
                    if target is not None:
                        validation_predictions[target] = status

    translated_links: list[CodexX1LinkPrediction] = []
    for result in results.values():
        for link in result.links:
            translated_links.append(
                CodexX1LinkPrediction(
                    call_id=link.call_id,
                    call_item_id=_translate_source_item(
                        source_identity,
                        link.scope,
                        link.call_item_id,
                    ),
                    result_item_id=_translate_source_item(
                        source_identity,
                        link.scope,
                        link.result_item_id,
                    ),
                )
            )

    windows_by_source: dict[str, list[CodexX1WindowEvidence]] = defaultdict(list)
    if publications is not None:
        for publication in publications.values():
            for located in publication.output_windows:
                adapter_id = _translate_source_item(
                    source_identity,
                    located.source_scope,
                    located.source_item_id,
                )
                window = located.window
                windows_by_source[adapter_id].append(
                    CodexX1WindowEvidence(
                        source_item_id=adapter_id,
                        source_locator=located.source_locator,
                        role=window.role.value,
                        content_sha256=window.content_sha256,
                        total_chars=window.total_chars,
                        total_utf8_bytes=window.total_utf8_bytes,
                        retained_chars=window.retained_chars,
                        omitted_chars=window.omitted_chars,
                        truncated=window.truncated,
                        error_tail=window.error_tail,
                    )
                )

    records: list[CodexX1TreatmentRecord] = []
    for case_id in membership.eligible_case_ids:
        exact_ids = authority_ids[case_id]
        relevant_events: list[CodexX1EventEvidence] = []
        relevant_adapter_ids = set(exact_ids)
        for event, translated_ids, audit in translated_events:
            include = bool(exact_ids.intersection(translated_ids))
            if isinstance(event, NormalizedValidationEvent):
                for source_id in translated_ids:
                    source = adapter_by_id[source_id]
                    target = derived_validations.get((source.thread_id, source.item_id))
                    if target in exact_ids:
                        include = True
            if include:
                relevant_events.append(audit)
                relevant_adapter_ids.update(translated_ids)

        ordered_ids: list[str] = []
        for unit in kinds_by_case[case_id].get("event_order", ()):
            ordered_ids.extend(
                sorted(
                    unit.item_ids,
                    key=lambda item_id: (
                        _adapter_event_number(adapter_by_id[item_id].source_locator),
                        adapter_by_id[item_id].sequence,
                        item_id,
                    ),
                )
            )
        links = tuple(
            link
            for link in translated_links
            if link.call_item_id in exact_ids or link.result_item_id in exact_ids
        )
        patches = tuple(
            CodexX1StatePrediction(
                item_id=unit.item_ids[0],
                status=patch_predictions.get(unit.item_ids[0], "unknown"),
            )
            for unit in kinds_by_case[case_id].get("patch", ())
        )
        validation_units = (
            *kinds_by_case[case_id].get("validation", ()),
            *kinds_by_case[case_id].get("false_validation", ()),
        )
        validations: list[CodexX1StatePrediction] = []
        for unit in validation_units:
            item_id = unit.item_ids[0]
            status = validation_predictions.get(item_id)
            if status is None:
                item_type = adapter_by_id[item_id].item_type
                if item_type == "CommandExecution":
                    status = "invoked"
                elif item_type in {"AgentMessage", "Plan"}:
                    status = "mentioned"
                else:
                    status = "target_unknown"
            validations.append(CodexX1StatePrediction(item_id=item_id, status=status))

        window_evidence = tuple(
            window for item_id in sorted(exact_ids) for window in windows_by_source.get(item_id, ())
        )
        output_ids = tuple(
            unit.item_ids[0]
            for unit in kinds_by_case[case_id].get("output_window", ())
            if windows_by_source.get(unit.item_ids[0])
        )
        error_ids = tuple(
            unit.item_ids[0]
            for unit in kinds_by_case[case_id].get("error_tail", ())
            if any(window.error_tail for window in windows_by_source.get(unit.item_ids[0], ()))
        )
        observed_results = tuple(
            sorted(
                item_id
                for item_id in exact_ids
                if adapter_by_id[item_id].item_type
                in {"ToolResult", "CommandResult", "PatchResult"}
            )
        )
        case_threads = {adapter_by_id[item_id].thread_id for item_id in relevant_adapter_ids}
        if len(case_threads) != 1:
            raise CodexX1VerificationError("X-T1 state case crosses production thread scope")
        thread_id = next(iter(case_threads))
        source_evidence = tuple(
            CodexX1SourceEvidence(
                source_item_id=adapter_id,
                source_locator=adapter_by_id[adapter_id].source_locator,
                source_file=source_files[adapter_id],
                observable_item_id=observable.item_id,
                observable_locator=observable.source_locator,
                observable_sha256=observable.canonical_sha256(),
            )
            for adapter_id in sorted(relevant_adapter_ids)
            for observable in observable_by_adapter.get(adapter_id, ())
        )
        normalization_sha = results[thread_id].summary.content_sha256
        publication = publications.get(thread_id) if publications is not None else None
        source_set_sha = (
            publication.source_set_sha256
            if publication is not None
            else _canonical_sha256(
                [item.model_dump(mode="json") for item in source_items[thread_id]]
            )
        )
        diagnostics = tuple(
            sorted({diagnostic.code.value for diagnostic in results[thread_id].diagnostics})
        )
        latency_ms, memory_peak = measurements[thread_id]
        record_payload = {
            "arm": arm.value,
            "case_id": case_id,
            "membership_sha256": membership.content_sha256,
            "ordered_item_ids": ordered_ids,
            "links": [item.model_dump(mode="json") for item in links],
            "patches": [item.model_dump(mode="json") for item in patches],
            "validations": [item.model_dump(mode="json") for item in validations],
            "observed_result_item_ids": observed_results,
            "output_window_item_ids": output_ids,
            "error_tail_item_ids": error_ids,
            "source_evidence": [item.model_dump(mode="json") for item in source_evidence],
            "event_evidence": [item.model_dump(mode="json") for item in relevant_events],
            "window_evidence": [item.model_dump(mode="json") for item in window_evidence],
            "normalization_content_sha256": normalization_sha,
            "source_set_sha256": source_set_sha,
            "publication_id": publication.publication_id if publication is not None else None,
            "diagnostic_codes": diagnostics,
        }
        records.append(
            CodexX1TreatmentRecord(
                arm=arm,
                case_id=case_id,
                membership_sha256=membership.content_sha256,
                ordered_item_ids=tuple(ordered_ids),
                links=links,
                patches=patches,
                validations=tuple(validations),
                observed_result_item_ids=observed_results,
                output_window_item_ids=output_ids,
                error_tail_item_ids=error_ids,
                source_evidence=source_evidence,
                event_evidence=tuple(relevant_events),
                window_evidence=window_evidence,
                normalization_content_sha256=normalization_sha,
                source_set_sha256=source_set_sha,
                publication_id=(publication.publication_id if publication is not None else None),
                diagnostic_codes=diagnostics,
                latency_ms=float(latency_ms),
                memory_peak_bytes=memory_peak,
                source_record_sha256=_canonical_sha256(record_payload),
            )
        )
    return tuple(records)


def _baseline_metric_results(
    preparation: CodexX1Preparation,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[CodexX1MetricResult, ...]:
    """Recompute the fixed baseline control contract from embedded predictions."""

    anchors = {item.name: item for item in preparation.baseline_anchors}
    eligible = set(preparation.membership.eligible_case_ids)
    latencies = [
        float(row["trace"]["latency_ms"])
        for row in rows
        if row.get("case_id") in eligible
        and isinstance(row.get("trace"), Mapping)
        and type(row["trace"].get("latency_ms")) in {int, float}
    ]
    proxy_reason = (
        "immutable X-B0 retrieval@10 is a state-presence proxy, not an explicit prediction"
    )

    def unavailable(name: CodexX1MetricName, unit: str, reason: str) -> CodexX1MetricResult:
        return CodexX1MetricResult(
            name=name,
            status=Availability.UNAVAILABLE,
            numerator=None,
            denominator=0,
            value=None,
            unit=unit,
            reason=reason,
        )

    def anchor(name: CodexX1MetricName, key: str) -> CodexX1MetricResult:
        value = anchors[key]
        return CodexX1MetricResult(
            name=name,
            status=Availability.PROVISIONAL,
            numerator=value.numerator,
            denominator=value.denominator,
            value=value.value,
            unit="ratio",
            reason=proxy_reason,
        )

    results = [
        unavailable(
            CodexX1MetricName.CALL_RESULT_PAIRING_PRECISION,
            "ratio",
            "X-B0 does not emit explicit call-result link predictions",
        ),
        anchor(CodexX1MetricName.CALL_RESULT_PAIRING_RECALL, "call_result"),
        anchor(CodexX1MetricName.PATCH_STATUS_ACCURACY, "patch"),
        unavailable(
            CodexX1MetricName.VALIDATION_PRECISION,
            "ratio",
            "X-B0 does not emit terminal validation predictions",
        ),
        anchor(CodexX1MetricName.VALIDATION_RECALL, "validation"),
        unavailable(
            CodexX1MetricName.VALIDATION_F1,
            "ratio",
            "baseline validation precision is unavailable",
        ),
        anchor(CodexX1MetricName.FALSE_VALIDATED_RATE, "false_validation"),
        anchor(CodexX1MetricName.EVENT_ORDER_ACCURACY, "event_order"),
        unavailable(
            CodexX1MetricName.UNLINKED_RESULT_COUNT,
            "count",
            "X-B0 retrieval rows do not encode link diagnostics",
        ),
        unavailable(
            CodexX1MetricName.UNKNOWN_STATE_COUNT,
            "count",
            "X-B0 retrieval rows do not encode normalized states",
        ),
        unavailable(
            CodexX1MetricName.OUTPUT_WINDOW_COVERAGE,
            "ratio",
            "X-B0 predates the CX1-02 output-window contract",
        ),
        unavailable(
            CodexX1MetricName.ERROR_TAIL_COVERAGE,
            "ratio",
            "X-B0 predates the CX1-02 error-tail contract",
        ),
        _count_metric(
            CodexX1MetricName.REASONING_LEAKAGE_COUNT,
            0,
            denominator=len(eligible),
        ),
        _count_metric(
            CodexX1MetricName.SECRET_LEAKAGE_COUNT,
            0,
            denominator=len(eligible),
        ),
        _count_metric(
            CodexX1MetricName.ACL_LEAKAGE_COUNT,
            0,
            denominator=len(eligible),
        ),
    ]
    for name, percentile in (
        (CodexX1MetricName.LATENCY_P50_MS, 0.50),
        (CodexX1MetricName.LATENCY_P95_MS, 0.95),
    ):
        value = _percentile(latencies, percentile) if latencies else None
        if value is None:
            results.append(
                unavailable(name, "milliseconds", "baseline trace latency is unavailable")
            )
        else:
            results.append(
                CodexX1MetricResult(
                    name=name,
                    status=Availability.AVAILABLE,
                    numerator=value,
                    denominator=len(latencies),
                    value=value,
                    unit="milliseconds",
                )
            )
    results.append(
        unavailable(
            CodexX1MetricName.MEMORY_PEAK_BYTES,
            "bytes",
            "immutable X-B0 did not record memory peaks",
        )
    )
    return tuple(results)


def _smoke_records(
    preparation: CodexX1Preparation,
) -> tuple[CodexX1TreatmentRecord, ...]:
    membership = preparation.membership
    smoke_case_ids = (
        membership.units["call_result"][0].case_id,
        membership.units["false_validation"][0].case_id,
    )
    call_unit = membership.units["call_result"][0]
    false_unit = membership.units["false_validation"][0]
    return (
        CodexX1TreatmentRecord(
            arm=CodexX1Arm.CX1_NORMALIZED,
            case_id=smoke_case_ids[0],
            membership_sha256=membership.content_sha256,
            ordered_item_ids=call_unit.item_ids,
            links=(
                CodexX1LinkPrediction(
                    call_id=call_unit.expected,
                    call_item_id=call_unit.item_ids[0],
                    result_item_id=call_unit.item_ids[1],
                ),
            ),
            observed_result_item_ids=(call_unit.item_ids[1],),
            latency_ms=0.25,
            memory_peak_bytes=4096,
            source_record_sha256=call_unit.authority_sha256,
        ),
        CodexX1TreatmentRecord(
            arm=CodexX1Arm.CX1_NORMALIZED,
            case_id=smoke_case_ids[1],
            membership_sha256=membership.content_sha256,
            validations=(
                CodexX1StatePrediction(
                    item_id=false_unit.item_ids[0],
                    status="target_unknown",
                ),
            ),
            latency_ms=0.5,
            memory_peak_bytes=6144,
            source_record_sha256=false_unit.authority_sha256,
        ),
    )


def build_codex_x1_smoke_artifact_v1(
    output_directory: str | Path,
    *,
    preparation: CodexX1Preparation | None = None,
) -> CodexX1SmokeResult:
    """Build a two-case programmatic SMOKE_ONLY artifact in a fresh directory."""

    prepared = preparation or prepare_codex_x1_v1()
    if type(prepared) is not CodexX1Preparation:
        raise CodexX1VerificationError("smoke requires the exact preparation contract")
    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        raise CodexX1VerificationError("smoke output must be a fresh non-reused path")
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise CodexX1VerificationError("smoke output parent cannot be a symlink")
    staging = parent / f".{output.name}.staging"
    if staging.exists() or staging.is_symlink():
        raise CodexX1VerificationError("smoke staging path is not fresh")
    records = _smoke_records(prepared)
    smoke_case_ids = tuple(record.case_id for record in records)
    dataset = load_codex_golden_v1(_default_golden_root())
    cases = {case.case_id: case for case in dataset.cases}
    baseline_rows = {
        row["case_id"]: row for row in _correction_prediction_rows(_default_correction_root())
    }
    artifact_id = _canonical_sha256(
        {
            "kind": "programmatic-smoke",
            "membership_sha256": prepared.membership.content_sha256,
            "case_ids": smoke_case_ids,
            "seed": CODEX_X1_SEED,
        }
    ).removeprefix("sha256:")[:32]
    artifact_uri = f"evaluation-smoke://project-codex-x-t1/{artifact_id}"
    common = {
        "schema_version": CODEX_X1_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_uri": artifact_uri,
        "status": "SMOKE_ONLY",
        "run_created": False,
        "metrics_created": False,
        "production_execution": "NOT_AUTHORIZED",
        "membership_sha256": prepared.membership.content_sha256,
        "seed": CODEX_X1_SEED,
    }
    run_payload = {
        **common,
        "qualification_status": "NON_QUALIFIED",
        "released_treatment_executed": False,
        "case_count": len(smoke_case_ids),
        "case_ids": list(smoke_case_ids),
    }
    manifest_payload = {
        **common,
        "artifact_version": CODEX_X1_ARTIFACT_VERSION,
        "canonical_files": list(CODEX_X1_CANONICAL_ARTIFACT_FILES),
        "portable_paths_only": True,
        "golden": {
            "dataset_id": CODEX_GOLDEN_DATASET_ID,
            "dataset_version": CODEX_GOLDEN_DATASET_VERSION,
            "package_hash": CODEX_GOLDEN_PACKAGE_HASH,
            "authority_digest": CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
            "case_count": CODEX_XB0_CASE_COUNT,
        },
        "correction": {
            "uri": CODEX_XB0_CORRECTION_URI,
            "artifact_set_hash": CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH,
            "source_run_uri": CODEX_XB0_SOURCE_RUN_URI,
            "source_artifact_set_hash": CODEX_XB0_SOURCE_ARTIFACT_SET_HASH,
            "evaluator_version": CODEX_CORRECTION_EVALUATOR_VERSION,
            "evaluator_digest": CODEX_CORRECTION_EVALUATOR_DIGEST,
        },
        "gate": prepared.gate.model_dump(mode="json"),
        "cx1_01_component_set_hash": CODEX_X1_CX1_01_COMPONENT_SET_HASH,
        "cx1_02_component_set_hash": CODEX_X1_CX1_02_COMPONENT_SET_HASH,
        "cx1_02_components": [item.model_dump(mode="json") for item in prepared.cx1_02_components],
        "cx1_02_identity_status": prepared.cx1_02_identity_status,
        "cx1_02_versions": prepared.cx1_02_versions.model_dump(mode="json"),
        "released_eligible_case_ids": list(prepared.membership.eligible_case_ids),
        "smoke_case_ids": list(smoke_case_ids),
        "arms": [arm.model_dump(mode="json") for arm in prepared.arms],
    }
    golden_rows = [
        {
            **common,
            "case_id": case_id,
            "case_authority_sha256": cases[case_id].canonical_sha256(),
            "authority": cases[case_id].model_dump(mode="json"),
        }
        for case_id in smoke_case_ids
    ]
    control_rows = [
        {
            **common,
            "case_id": case_id,
            "source_prediction_sha256": _canonical_sha256(baseline_rows[case_id]),
            "prediction": baseline_rows[case_id],
        }
        for case_id in smoke_case_ids
    ]
    treatment_rows = [{**common, **record.model_dump(mode="json")} for record in records]
    empty_report = {
        **common,
        "status": "NOT_CREATED",
        "qualification_status": "NON_QUALIFIED",
        "metrics": [],
    }
    slices_payload = {
        **common,
        "status": "NOT_CREATED",
        "slices": [],
        "authority_denominators": prepared.membership.denominators,
    }
    latency_payload = {
        **common,
        "status": "SMOKE_MEASUREMENTS_ONLY",
        "measurements": [
            {
                "case_id": record.case_id,
                "latency_ms": record.latency_ms,
                "memory_peak_bytes": record.memory_peak_bytes,
            }
            for record in records
        ],
    }
    security_payload = {
        **common,
        "status": "clean",
        "findings": [],
        "checks": {
            "absolute_paths": "clean",
            "credentials": "clean",
            "reasoning": "clean",
            "acl": "clean",
            "nonfinite_numbers": "clean",
            "database_or_sidecars": "clean",
            "undeclared_files": "clean",
        },
    }
    try:
        staging.mkdir()
        _write_json(staging / "run.json", run_payload)
        _write_json(staging / "manifest.json", manifest_payload)
        _write_jsonl(staging / "golden_cases.jsonl", golden_rows)
        _write_jsonl(staging / "baseline_control.jsonl", control_rows)
        _write_jsonl(staging / "treatment_records.jsonl", treatment_rows)
        _write_json(staging / "metrics.json", empty_report)
        _write_json(staging / "slices.json", slices_payload)
        _write_jsonl(staging / "errors.jsonl", ())
        _write_json(staging / "latency.json", latency_payload)
        _write_json(staging / "security.json", security_payload)
        findings = _scan_artifact(staging)
        if findings:
            raise CodexX1VerificationError(
                "programmatic smoke artifact failed security scan: " + ",".join(findings)
            )
        files = _file_records(staging)
        artifact_set_hash = _artifact_set_hash(files)
        _write_json(
            staging / "checksums.json",
            {
                "schema_version": CODEX_X1_SCHEMA_VERSION,
                "artifact_id": artifact_id,
                "algorithm": "sha256",
                "files": files,
                "artifact_set_hash": artifact_set_hash,
            },
        )
        staging.rename(output)
    except Exception:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise
    verification = verify_codex_x1_artifact_v1(output)
    return CodexX1SmokeResult(
        artifact_path=output,
        artifact_set_hash=verification.artifact_set_hash,
        status="SMOKE_ONLY",
    )


def _unit_slice_rows(
    *,
    membership: CodexX1Membership,
    baseline_rows: Sequence[Mapping[str, Any]],
    evaluations: Sequence[tuple[CodexX1Arm, Sequence[CodexX1TreatmentRecord]]],
) -> list[dict[str, Any]]:
    baseline = {str(row["case_id"]): row for row in baseline_rows}
    treatment = {
        arm: {record.case_id: record for record in records} for arm, records in evaluations
    }
    rows: list[dict[str, Any]] = []
    for kind, units in membership.units.items():
        for unit in units:
            ranked = {
                str(item["item_id"]): int(item["rank"])
                for item in baseline[unit.case_id].get("results", ())
                if isinstance(item, Mapping)
            }
            baseline_prediction: object
            baseline_correct: bool | None
            if kind == "event_order":
                observed = tuple(
                    sorted(
                        (item_id for item_id in unit.item_ids if item_id in ranked),
                        key=ranked.__getitem__,
                    )
                )
                baseline_prediction = list(observed)
                baseline_correct = observed == unit.item_ids
            elif kind == "call_result":
                observed = tuple(item_id for item_id in unit.item_ids if item_id in ranked)
                baseline_prediction = list(observed)
                baseline_correct = len(observed) == len(unit.item_ids)
            elif kind in {"patch", "validation", "false_validation"}:
                baseline_prediction = unit.item_ids[0] in ranked
                baseline_correct = bool(baseline_prediction)
            else:
                baseline_prediction = None
                baseline_correct = None
            rows.append(
                {
                    "arm": CodexX1Arm.BASELINE_CONTROL.value,
                    "kind": kind,
                    "unit_id": unit.unit_id,
                    "case_id": unit.case_id,
                    "item_ids": list(unit.item_ids),
                    "expected": unit.expected,
                    "prediction": baseline_prediction,
                    "correct": baseline_correct,
                    "status": (
                        Availability.PROVISIONAL.value
                        if baseline_correct is not None
                        else Availability.UNAVAILABLE.value
                    ),
                }
            )
            for arm, mapped in treatment.items():
                record = mapped[unit.case_id]
                if kind == "event_order":
                    selected = tuple(
                        item_id for item_id in record.ordered_item_ids if item_id in unit.item_ids
                    )
                    prediction = list(selected)
                    correct = selected == unit.item_ids
                elif kind == "call_result":
                    selected_links = [
                        link
                        for link in record.links
                        if (link.call_item_id, link.result_item_id) == unit.item_ids
                    ]
                    prediction = [
                        {
                            "call_id": link.call_id,
                            "call_item_id": link.call_item_id,
                            "result_item_id": link.result_item_id,
                        }
                        for link in selected_links
                    ]
                    correct = any(link.call_id == unit.expected for link in selected_links)
                elif kind == "patch":
                    state = next(
                        (
                            value.status
                            for value in record.patches
                            if value.item_id == unit.item_ids[0]
                        ),
                        None,
                    )
                    prediction = state
                    correct = state == unit.expected
                elif kind in {"validation", "false_validation"}:
                    state = next(
                        (
                            value.status
                            for value in record.validations
                            if value.item_id == unit.item_ids[0]
                        ),
                        None,
                    )
                    prediction = state
                    correct = state == unit.expected if kind == "validation" else state != "passed"
                elif kind == "output_window":
                    prediction = unit.item_ids[0] in record.output_window_item_ids
                    correct = bool(prediction)
                else:
                    prediction = unit.item_ids[0] in record.error_tail_item_ids
                    correct = bool(prediction)
                rows.append(
                    {
                        "arm": arm.value,
                        "kind": kind,
                        "unit_id": unit.unit_id,
                        "case_id": unit.case_id,
                        "item_ids": list(unit.item_ids),
                        "expected": unit.expected,
                        "prediction": prediction,
                        "correct": correct,
                        "status": Availability.AVAILABLE.value,
                    }
                )
    return rows


def _production_diagnostic_rows(
    *,
    common: Mapping[str, Any],
    arm: CodexX1Arm,
    results: Mapping[str, Any],
    source_identity: Mapping[tuple[str, str, str], str],
    allowed_source_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for thread_id, result in sorted(results.items()):
        for diagnostic in result.diagnostics:
            item_ids = [
                _translate_source_item(source_identity, diagnostic.scope, item_id)
                for item_id in diagnostic.source_item_ids
            ]
            item_ids = [item_id for item_id in item_ids if item_id in allowed_source_ids]
            if diagnostic.source_item_ids and not item_ids:
                continue
            rows.append(
                {
                    **common,
                    "arm": arm.value,
                    "thread_id": thread_id,
                    "code": diagnostic.code.value,
                    "severity": diagnostic.severity.value,
                    "call_id": diagnostic.call_id,
                    "source_item_ids": item_ids,
                    "occurrences": diagnostic.occurrences,
                }
            )
    return rows


def _build_codex_x1_production_artifact(
    output: Path,
    *,
    preparation: CodexX1Preparation,
    dataset: GoldenDataset,
    baseline_rows: Sequence[Mapping[str, Any]],
    normalized_records: Sequence[CodexX1TreatmentRecord],
    facts_records: Sequence[CodexX1TreatmentRecord],
    normalized_results: Mapping[str, Any],
    facts_results: Mapping[str, Any],
    source_identity: Mapping[tuple[str, str, str], str],
    adapter_counters: Mapping[str, int],
    fixture_files: Sequence[Mapping[str, Any]],
) -> CodexX1ArtifactVerification:
    """Write one complete production artifact into a fresh system-temp directory."""

    if output.exists() or output.is_symlink():
        raise CodexX1VerificationError("production artifact staging output is not fresh")
    if preparation.canonical_sha256() != CODEX_X1_PRODUCTION_PREPARATION_SHA256:
        raise CodexX1VerificationError("production preparation digest is not authorized")
    run_id = output.name
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise CodexX1VerificationError("production artifact ID must be UUID32 hex")
    artifact_uri = f"evaluation-run://project-codex-xt1-v1/{run_id}"
    common = {
        "schema_version": CODEX_X1_SCHEMA_VERSION,
        "artifact_id": run_id,
        "artifact_uri": artifact_uri,
        "status": "COMPLETED",
        "run_created": True,
        "metrics_created": True,
        "production_execution": "EXECUTED",
        "membership_sha256": preparation.membership.content_sha256,
        "seed": CODEX_X1_SEED,
    }
    normalized_evaluation = evaluate_codex_x1_records_v1(
        membership=preparation.membership,
        records=normalized_records,
        arm=CodexX1Arm.CX1_NORMALIZED,
    )
    facts_evaluation = evaluate_codex_x1_records_v1(
        membership=preparation.membership,
        records=facts_records,
        arm=CodexX1Arm.CX1_DERIVED_FACTS,
    )
    baseline_metrics = _baseline_metric_results(preparation, baseline_rows)
    baseline_hard_failures = tuple(
        metric.name.value
        for metric in baseline_metrics
        if metric.name
        in {
            CodexX1MetricName.FALSE_VALIDATED_RATE,
            CodexX1MetricName.REASONING_LEAKAGE_COUNT,
            CodexX1MetricName.SECRET_LEAKAGE_COUNT,
            CodexX1MetricName.ACL_LEAKAGE_COUNT,
        }
        and metric.numerator not in {None, 0}
    )
    qualification = (
        "QUALIFIED"
        if not normalized_evaluation.hard_gate_failures and not facts_evaluation.hard_gate_failures
        else "NON_QUALIFIED"
    )
    cases = {case.case_id: case for case in dataset.cases}
    controls = {str(row["case_id"]): row for row in baseline_rows}
    case_ids = preparation.membership.eligible_case_ids
    if tuple(sorted(controls)) != CODEX_XB0_CASE_MEMBERSHIP:
        raise CodexX1VerificationError("baseline control is not the immutable ordered 45")

    run_payload = {
        **common,
        "run_version": CODEX_X1_PRODUCTION_RUN_VERSION,
        "runner_version": CODEX_X1_RUNNER_VERSION,
        "preparation_sha256": preparation.canonical_sha256(),
        "gate_decision_block_sha256": preparation.gate.decision_block_sha256,
        "qualification_status": qualification,
        "x2_authorization": "NOT_AUTHORIZED",
        "x2_block_reason": "independent X-T1 Run Audit has not authorized X2",
        "released_treatment_executed": True,
        "case_count": len(case_ids),
        "case_ids": list(case_ids),
        "treatment_record_count": len(normalized_records) + len(facts_records),
        "fixture_thread_count": adapter_counters.get("sessions"),
        "adapter_item_count": adapter_counters.get("items"),
    }
    manifest_payload = {
        **common,
        "artifact_version": CODEX_X1_ARTIFACT_VERSION,
        "canonical_files": list(CODEX_X1_CANONICAL_ARTIFACT_FILES),
        "portable_paths_only": True,
        "preparation_sha256": preparation.canonical_sha256(),
        "runner": {
            "version": CODEX_X1_RUNNER_VERSION,
            "network_allowed": False,
            "temporary_sqlite_only": True,
            "formal_database_accessed": False,
            "retrieval_executed": False,
        },
        "adapter": {
            "module": "evidence_rag.codex_adapter",
            "class": "CodexSessionAdapter",
            "version": "codex-jsonl-v2-clean-text",
            "thread_count": adapter_counters.get("sessions"),
            "item_count": adapter_counters.get("items"),
            "excluded_reasoning_count": adapter_counters.get("excluded_reasoning"),
            "redacted_item_count": adapter_counters.get("redacted_items"),
            "fixture_files": list(fixture_files),
        },
        "golden": {
            "dataset_id": CODEX_GOLDEN_DATASET_ID,
            "dataset_version": CODEX_GOLDEN_DATASET_VERSION,
            "package_hash": CODEX_GOLDEN_PACKAGE_HASH,
            "authority_digest": CODEX_XB0_GOLDEN_AUTHORITY_DIGEST,
            "released_case_count": CODEX_GOLDEN_CASE_COUNT,
        },
        "correction": {
            "uri": CODEX_XB0_CORRECTION_URI,
            "artifact_set_hash": CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH,
            "source_run_uri": CODEX_XB0_SOURCE_RUN_URI,
            "source_artifact_set_hash": CODEX_XB0_SOURCE_ARTIFACT_SET_HASH,
            "evaluator_version": CODEX_CORRECTION_EVALUATOR_VERSION,
            "evaluator_digest": CODEX_CORRECTION_EVALUATOR_DIGEST,
        },
        "gate": preparation.gate.model_dump(mode="json"),
        "membership": preparation.membership.model_dump(mode="json"),
        "cx1_01_component_set_hash": CODEX_X1_CX1_01_COMPONENT_SET_HASH,
        "cx1_01_components": [
            item.model_dump(mode="json") for item in preparation.cx1_01_components
        ],
        "cx1_02_component_set_hash": CODEX_X1_CX1_02_COMPONENT_SET_HASH,
        "cx1_02_components": [
            item.model_dump(mode="json") for item in preparation.cx1_02_components
        ],
        "cx1_02_identity_status": preparation.cx1_02_identity_status,
        "cx1_02_versions": preparation.cx1_02_versions.model_dump(mode="json"),
        "arms": [
            {
                **arm.model_dump(mode="json"),
                "execution_status": (
                    "CONTROL_IMMUTABLE" if arm.arm == CodexX1Arm.BASELINE_CONTROL else "EXECUTED"
                ),
            }
            for arm in preparation.arms
        ],
        "config": {
            "seed": CODEX_X1_SEED,
            "max_codex_item_chars": 16_000,
            "fixture_mode": "isolated_byte_exact_copy",
            "sqlite_mode": "isolated_temporary",
            "network": "disabled",
        },
    }
    golden_rows = [
        {
            **common,
            "case_id": case_id,
            "case_authority_sha256": cases[case_id].canonical_sha256(),
            "authority": cases[case_id].model_dump(mode="json"),
        }
        for case_id in case_ids
    ]
    control_rows = [
        {
            **common,
            "case_id": case_id,
            "source_prediction_sha256": _canonical_sha256(controls[case_id]),
            "prediction": controls[case_id],
        }
        for case_id in case_ids
    ]
    treatment_rows = [
        {**common, **record.model_dump(mode="json")}
        for record in (*normalized_records, *facts_records)
    ]
    baseline_evaluation = {
        "arm": CodexX1Arm.BASELINE_CONTROL.value,
        "status": Availability.PROVISIONAL.value,
        "qualification_status": "NON_QUALIFIED",
        "membership_sha256": preparation.membership.content_sha256,
        "evaluated_case_ids": list(case_ids),
        "metrics": [metric.model_dump(mode="json") for metric in baseline_metrics],
        "hard_gate_failures": list(baseline_hard_failures),
    }
    metrics_payload = {
        **common,
        "qualification_status": qualification,
        "x2_authorization": "NOT_AUTHORIZED",
        "evaluations": [
            baseline_evaluation,
            normalized_evaluation.model_dump(mode="json"),
            facts_evaluation.model_dump(mode="json"),
        ],
    }
    slice_rows = _unit_slice_rows(
        membership=preparation.membership,
        baseline_rows=baseline_rows,
        evaluations=(
            (CodexX1Arm.CX1_NORMALIZED, normalized_records),
            (CodexX1Arm.CX1_DERIVED_FACTS, facts_records),
        ),
    )
    slices_payload = {
        **common,
        "authority_denominators": preparation.membership.denominators,
        "rows": slice_rows,
    }
    diagnostic_rows = [
        *_production_diagnostic_rows(
            common=common,
            arm=CodexX1Arm.CX1_NORMALIZED,
            results=normalized_results,
            source_identity=source_identity,
            allowed_source_ids={
                evidence.source_item_id
                for record in (*normalized_records, *facts_records)
                for evidence in record.source_evidence
            },
        ),
        *_production_diagnostic_rows(
            common=common,
            arm=CodexX1Arm.CX1_DERIVED_FACTS,
            results=facts_results,
            source_identity=source_identity,
            allowed_source_ids={
                evidence.source_item_id
                for record in (*normalized_records, *facts_records)
                for evidence in record.source_evidence
            },
        ),
    ]
    baseline_latency = [
        {
            "arm": CodexX1Arm.BASELINE_CONTROL.value,
            "case_id": case_id,
            "latency_ms": float(controls[case_id]["trace"]["latency_ms"]),
            "memory_peak_bytes": None,
            "source": "immutable_xb0_trace",
        }
        for case_id in case_ids
    ]
    latency_payload = {
        **common,
        "measurements": [
            *baseline_latency,
            *[
                {
                    "arm": record.arm.value,
                    "case_id": record.case_id,
                    "latency_ms": record.latency_ms,
                    "memory_peak_bytes": record.memory_peak_bytes,
                    "source": "production_component_measurement",
                }
                for record in (*normalized_records, *facts_records)
            ],
        ],
    }
    treatment_hard_failures = {
        normalized_evaluation.arm.value: list(normalized_evaluation.hard_gate_failures),
        facts_evaluation.arm.value: list(facts_evaluation.hard_gate_failures),
    }
    security_payload = {
        **common,
        "status": "clean",
        "findings": [],
        "hard_gate_failures": treatment_hard_failures,
        "excluded_reasoning_count": adapter_counters.get("excluded_reasoning"),
        "redacted_item_count": adapter_counters.get("redacted_items"),
        "checks": {
            "absolute_paths": "clean",
            "credentials": "clean",
            "reasoning": "clean",
            "acl": "clean",
            "nonfinite_numbers": "clean",
            "database_or_sidecars": "clean",
            "undeclared_files": "clean",
            "network": "not_used",
            "formal_database": "not_accessed",
            "retrieval": "not_executed",
        },
    }

    output.mkdir()
    try:
        _write_json(output / "run.json", run_payload)
        _write_json(output / "manifest.json", manifest_payload)
        _write_jsonl(output / "golden_cases.jsonl", golden_rows)
        _write_jsonl(output / "baseline_control.jsonl", control_rows)
        _write_jsonl(output / "treatment_records.jsonl", treatment_rows)
        _write_json(output / "metrics.json", metrics_payload)
        _write_json(output / "slices.json", slices_payload)
        _write_jsonl(output / "errors.jsonl", diagnostic_rows)
        _write_json(output / "latency.json", latency_payload)
        _write_json(output / "security.json", security_payload)
        findings = _scan_artifact(output)
        if findings:
            raise CodexX1VerificationError(
                "production artifact failed security scan: " + ",".join(findings)
            )
        files = _file_records(output)
        _write_json(
            output / "checksums.json",
            {
                "schema_version": CODEX_X1_SCHEMA_VERSION,
                "artifact_id": run_id,
                "algorithm": "sha256",
                "files": files,
                "artifact_set_hash": _artifact_set_hash(files),
            },
        )
        return verify_codex_x1_artifact_v1(output)
    except Exception:
        shutil.rmtree(output)
        raise


def verify_codex_x1_artifact_v1(
    directory: str | Path,
) -> CodexX1ArtifactVerification:
    """Offline, verify-only portable artifact recomputation.

    The production branch consumes only the eleven copied files.  It never
    calls preparation, the adapter, normalizer, facts builder, retrieval, or
    SQLite.
    """

    artifact = Path(directory)
    _require_artifact_tree(artifact)
    checksums = _read_canonical_json(artifact / "checksums.json")
    files = _file_records(artifact)
    expected_set_hash = _artifact_set_hash(files)
    if (
        checksums.get("schema_version") != CODEX_X1_SCHEMA_VERSION
        or checksums.get("algorithm") != "sha256"
        or checksums.get("files") != files
        or checksums.get("artifact_set_hash") != expected_set_hash
    ):
        raise CodexX1VerificationError("artifact checksum set mismatch")
    run = _read_canonical_json(artifact / "run.json")
    manifest = _read_canonical_json(artifact / "manifest.json")
    golden_rows = _read_canonical_jsonl(artifact / "golden_cases.jsonl")
    control_rows = _read_canonical_jsonl(artifact / "baseline_control.jsonl")
    treatment_rows = _read_canonical_jsonl(artifact / "treatment_records.jsonl")
    metrics = _read_canonical_json(artifact / "metrics.json")
    slices = _read_canonical_json(artifact / "slices.json")
    errors = _read_canonical_jsonl(artifact / "errors.jsonl")
    latency = _read_canonical_json(artifact / "latency.json")
    security = _read_canonical_json(artifact / "security.json")
    findings = _scan_artifact(artifact)
    if findings:
        raise CodexX1VerificationError("artifact security verification failed")

    shared_fields = (
        "schema_version",
        "artifact_id",
        "artifact_uri",
        "membership_sha256",
        "seed",
    )
    documents: list[Mapping[str, Any]] = [
        manifest,
        metrics,
        slices,
        latency,
        security,
        *golden_rows,
        *control_rows,
        *treatment_rows,
        *errors,
    ]
    for document in documents:
        if any(document.get(field) != run.get(field) for field in shared_fields):
            raise CodexX1VerificationError("artifact cross-file identity mismatch")
    if manifest.get("canonical_files") != list(CODEX_X1_CANONICAL_ARTIFACT_FILES) or (
        manifest.get("portable_paths_only") is not True
    ):
        raise CodexX1VerificationError("artifact manifest contract mismatch")

    common_exclusions = {
        "artifact_id",
        "artifact_uri",
        "status",
        "run_created",
        "metrics_created",
        "production_execution",
        "seed",
    }

    if run.get("status") == "SMOKE_ONLY":
        for document in documents:
            if (
                document.get("production_execution") != "NOT_AUTHORIZED"
                or document.get("run_created") is not False
                or document.get("metrics_created") is not False
            ):
                raise CodexX1VerificationError("artifact overclaims Run, metrics, or authorization")
        preparation = prepare_codex_x1_v1()
        if (
            run.get("qualification_status") != "NON_QUALIFIED"
            or run.get("released_treatment_executed") is not False
            or manifest.get("gate") != preparation.gate.model_dump(mode="json")
            or manifest.get("cx1_02_component_set_hash") != CODEX_X1_CX1_02_COMPONENT_SET_HASH
            or manifest.get("cx1_02_components")
            != [item.model_dump(mode="json") for item in preparation.cx1_02_components]
            or manifest.get("cx1_02_identity_status") != preparation.cx1_02_identity_status
            or manifest.get("cx1_02_versions")
            != preparation.cx1_02_versions.model_dump(mode="json")
            or manifest.get("cx1_01_component_set_hash") != CODEX_X1_CX1_01_COMPONENT_SET_HASH
            or manifest.get("golden", {}).get("package_hash") != CODEX_GOLDEN_PACKAGE_HASH
            or manifest.get("correction", {}).get("uri") != CODEX_XB0_CORRECTION_URI
            or manifest.get("correction", {}).get("artifact_set_hash")
            != CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH
            or metrics.get("status") != "NOT_CREATED"
            or metrics.get("metrics") != []
            or slices.get("status") != "NOT_CREATED"
            or slices.get("slices") != []
            or errors
            or security.get("status") != "clean"
            or security.get("findings") != []
        ):
            raise CodexX1VerificationError("artifact PREPARED-only posture mismatch")
        smoke_case_ids = tuple(manifest.get("smoke_case_ids", ()))
        row_memberships = (
            tuple(row.get("case_id") for row in golden_rows),
            tuple(row.get("case_id") for row in control_rows),
            tuple(row.get("case_id") for row in treatment_rows),
        )
        if (
            not smoke_case_ids
            or any(membership != smoke_case_ids for membership in row_memberships)
            or tuple(run.get("case_ids", ())) != smoke_case_ids
            or run.get("case_count") != len(smoke_case_ids)
        ):
            raise CodexX1VerificationError("artifact smoke case membership mismatch")
        if (
            manifest.get("released_eligible_case_ids")
            != list(preparation.membership.eligible_case_ids)
            or run.get("membership_sha256") != preparation.membership.content_sha256
            or slices.get("authority_denominators") != preparation.membership.denominators
        ):
            raise CodexX1VerificationError("artifact released membership authority mismatch")
        dataset = load_codex_golden_v1(_default_golden_root())
        cases = {case.case_id: case for case in dataset.cases}
        baseline_rows = {
            row["case_id"]: row for row in _correction_prediction_rows(_default_correction_root())
        }
        for golden, control, treatment in zip(
            golden_rows,
            control_rows,
            treatment_rows,
            strict=True,
        ):
            case_id = golden["case_id"]
            if (
                case_id not in cases
                or golden.get("authority") != cases[case_id].model_dump(mode="json")
                or golden.get("case_authority_sha256") != cases[case_id].canonical_sha256()
                or control.get("prediction") != baseline_rows[case_id]
                or control.get("source_prediction_sha256")
                != _canonical_sha256(baseline_rows[case_id])
            ):
                raise CodexX1VerificationError("artifact Golden/baseline source copy mismatch")
            record_payload = {
                key: value for key, value in treatment.items() if key not in common_exclusions
            }
            try:
                record = CodexX1TreatmentRecord.model_validate(record_payload)
            except ValueError as exc:
                raise CodexX1VerificationError("artifact treatment record is invalid") from exc
            if record.security_findings:
                raise CodexX1VerificationError(
                    "SMOKE_ONLY treatment record contains a security finding"
                )
        return CodexX1ArtifactVerification(
            artifact_path=artifact,
            artifact_set_hash=expected_set_hash,
            status="VERIFIED_SMOKE_ONLY",
            run_created=False,
            metrics_created=False,
            production_execution="NOT_AUTHORIZED",
        )

    for document in documents:
        if (
            document.get("production_execution") != "EXECUTED"
            or document.get("run_created") is not True
            or document.get("metrics_created") is not True
            or document.get("status") != "COMPLETED"
        ):
            raise CodexX1VerificationError("production cross-file execution posture mismatch")
    if (
        not re.fullmatch(r"[0-9a-f]{32}", str(run.get("artifact_id", "")))
        or run.get("artifact_uri")
        != f"evaluation-run://project-codex-xt1-v1/{run.get('artifact_id')}"
        or run.get("run_version") != CODEX_X1_PRODUCTION_RUN_VERSION
        or run.get("runner_version") != CODEX_X1_RUNNER_VERSION
        or run.get("preparation_sha256") != CODEX_X1_PRODUCTION_PREPARATION_SHA256
        or manifest.get("preparation_sha256") != CODEX_X1_PRODUCTION_PREPARATION_SHA256
        or run.get("gate_decision_block_sha256") != CODEX_X1_PRODUCTION_GATE_DECISION_SHA256
        or run.get("released_treatment_executed") is not True
        or run.get("x2_authorization") != "NOT_AUTHORIZED"
        or run.get("fixture_thread_count") != 8
        or run.get("adapter_item_count") != 54
        or run.get("case_count") != 23
        or run.get("treatment_record_count") != 46
    ):
        raise CodexX1VerificationError("production Run identity or authority mismatch")

    try:
        gate = CodexX1GateDecision.model_validate(manifest.get("gate"))
        membership = CodexX1Membership.model_validate(manifest.get("membership"))
        cx1_components = tuple(
            CodexX1ComponentIdentity.model_validate(value)
            for value in manifest.get("cx1_01_components", ())
        )
        facts_components = tuple(
            CodexX1ComponentIdentity.model_validate(value)
            for value in manifest.get("cx1_02_components", ())
        )
        versions = CodexX1FactsVersions.model_validate(manifest.get("cx1_02_versions"))
        arms = tuple(
            CodexX1ArmReadiness.model_validate(
                {key: value for key, value in arm.items() if key != "execution_status"}
            )
            for arm in manifest.get("arms", ())
        )
    except (TypeError, ValueError) as exc:
        raise CodexX1VerificationError("production embedded authority is invalid") from exc
    if (
        not gate.production_authorized
        or gate.conclusion_number != "11.1"
        or gate.decision_block_sha256 != CODEX_X1_PRODUCTION_GATE_DECISION_SHA256
        or membership.content_sha256 != CODEX_X1_MEMBERSHIP_SHA256
        or run.get("membership_sha256") != membership.content_sha256
        or tuple(run.get("case_ids", ())) != membership.eligible_case_ids
        or slices.get("authority_denominators") != membership.denominators
        or manifest.get("cx1_01_component_set_hash") != CODEX_X1_CX1_01_COMPONENT_SET_HASH
        or _canonical_sha256([item.model_dump(mode="json") for item in cx1_components])
        != CODEX_X1_CX1_01_COMPONENT_SET_HASH
        or manifest.get("cx1_02_component_set_hash") != CODEX_X1_CX1_02_COMPONENT_SET_HASH
        or _canonical_sha256([item.model_dump(mode="json") for item in facts_components])
        != CODEX_X1_CX1_02_COMPONENT_SET_HASH
        or len(facts_components) != 26
        or manifest.get("cx1_02_identity_status") != "MATCH"
        or manifest.get("golden", {}).get("package_hash") != CODEX_GOLDEN_PACKAGE_HASH
        or manifest.get("correction", {}).get("uri") != CODEX_XB0_CORRECTION_URI
        or manifest.get("correction", {}).get("artifact_set_hash")
        != CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH
        or manifest.get("runner", {}).get("retrieval_executed") is not False
        or manifest.get("runner", {}).get("formal_database_accessed") is not False
        or manifest.get("runner", {}).get("network_allowed") is not False
    ):
        raise CodexX1VerificationError("production embedded identity binding mismatch")
    preparation = CodexX1Preparation(
        production_execution="AUTHORIZED",
        gate=gate,
        membership=membership,
        cx1_01_components=cx1_components,
        cx1_02_components=facts_components,
        cx1_02_identity_status="MATCH",
        cx1_02_versions=versions,
        baseline_anchors=tuple(
            CodexX1BaselineAnchor(name=name, numerator=value[0], denominator=value[1])
            for name, value in _EXPECTED_BASELINE_ANCHORS.items()
        ),
        arms=arms,
    )
    if preparation.canonical_sha256() != CODEX_X1_PRODUCTION_PREPARATION_SHA256:
        raise CodexX1VerificationError("production preparation cannot be reconstructed")

    case_ids = membership.eligible_case_ids
    if (
        tuple(row.get("case_id") for row in golden_rows) != case_ids
        or tuple(row.get("case_id") for row in control_rows) != case_ids
        or tuple(row.get("case_id") for row in treatment_rows[:23]) != case_ids
        or tuple(row.get("case_id") for row in treatment_rows[23:]) != case_ids
    ):
        raise CodexX1VerificationError("production three-arm case membership mismatch")
    embedded_cases: dict[str, CodexGoldenCase] = {}
    membership_cases = {case.case_id: case for case in membership.cases}
    for row in golden_rows:
        try:
            case = CodexGoldenCase.model_validate(row.get("authority"))
        except ValueError as exc:
            raise CodexX1VerificationError("embedded Golden case is invalid") from exc
        bound = membership_cases.get(case.case_id)
        if (
            bound is None
            or row.get("case_authority_sha256") != case.canonical_sha256()
            or bound.case_authority_sha256 != case.canonical_sha256()
            or bound.event_truth_sha256
            != _canonical_sha256([truth.model_dump(mode="json") for truth in case.event_truth])
            or bound.judgments_sha256
            != _canonical_sha256(
                [judgment.model_dump(mode="json") for judgment in case.item_judgments]
            )
        ):
            raise CodexX1VerificationError("embedded Golden authority cross-binding mismatch")
        embedded_cases[case.case_id] = case
    controls: list[Mapping[str, Any]] = []
    for row in control_rows:
        prediction = row.get("prediction")
        if (
            not isinstance(prediction, Mapping)
            or row.get("source_prediction_sha256") != _canonical_sha256(prediction)
            or prediction.get("case_id") != row.get("case_id")
            or prediction.get("dataset_id") != CODEX_GOLDEN_DATASET_ID
            or prediction.get("dataset_version") != CODEX_GOLDEN_DATASET_VERSION
            or prediction.get("package_hash") != CODEX_GOLDEN_PACKAGE_HASH
            or prediction.get("run_id") != CODEX_XB0_SOURCE_RUN_ID
        ):
            raise CodexX1VerificationError("embedded X-B0 control row is invalid")
        controls.append(prediction)
    if _canonical_sha256(controls) != CODEX_X1_BASELINE_CONTROL_SHA256:
        raise CodexX1VerificationError("embedded X-B0 control membership digest mismatch")

    treatment_records: list[CodexX1TreatmentRecord] = []
    for row in treatment_rows:
        try:
            record = CodexX1TreatmentRecord.model_validate(
                {key: value for key, value in row.items() if key not in common_exclusions}
            )
        except ValueError as exc:
            raise CodexX1VerificationError("production treatment record is invalid") from exc
        if record.security_findings:
            raise CodexX1VerificationError("production treatment contains security leakage")
        if not record.source_evidence or not record.event_evidence:
            raise CodexX1VerificationError("production treatment lacks observable audit evidence")
        if record.source_record_sha256 != _canonical_sha256(
            _treatment_source_record_content(record)
        ):
            raise CodexX1VerificationError("production treatment source digest mismatch")
        source_ids = {evidence.source_item_id for evidence in record.source_evidence}
        if any(not set(event.source_item_ids) <= source_ids for event in record.event_evidence):
            raise CodexX1VerificationError("treatment event evidence is outside source authority")
        treatment_records.append(record)
    normalized_records = tuple(treatment_records[:23])
    facts_records = tuple(treatment_records[23:])
    normalized_evaluation = evaluate_codex_x1_records_v1(
        membership=membership,
        records=normalized_records,
        arm=CodexX1Arm.CX1_NORMALIZED,
    )
    facts_evaluation = evaluate_codex_x1_records_v1(
        membership=membership,
        records=facts_records,
        arm=CodexX1Arm.CX1_DERIVED_FACTS,
    )
    baseline_metrics = _baseline_metric_results(preparation, controls)
    baseline_failures = tuple(
        metric.name.value
        for metric in baseline_metrics
        if metric.name
        in {
            CodexX1MetricName.FALSE_VALIDATED_RATE,
            CodexX1MetricName.REASONING_LEAKAGE_COUNT,
            CodexX1MetricName.SECRET_LEAKAGE_COUNT,
            CodexX1MetricName.ACL_LEAKAGE_COUNT,
        }
        and metric.numerator not in {None, 0}
    )
    expected_baseline = {
        "arm": CodexX1Arm.BASELINE_CONTROL.value,
        "status": Availability.PROVISIONAL.value,
        "qualification_status": "NON_QUALIFIED",
        "membership_sha256": membership.content_sha256,
        "evaluated_case_ids": list(case_ids),
        "metrics": [metric.model_dump(mode="json") for metric in baseline_metrics],
        "hard_gate_failures": list(baseline_failures),
    }
    expected_qualification = (
        "QUALIFIED"
        if not normalized_evaluation.hard_gate_failures and not facts_evaluation.hard_gate_failures
        else "NON_QUALIFIED"
    )
    expected_evaluations = [
        expected_baseline,
        normalized_evaluation.model_dump(mode="json"),
        facts_evaluation.model_dump(mode="json"),
    ]
    if (
        metrics.get("evaluations") != expected_evaluations
        or metrics.get("qualification_status") != expected_qualification
        or metrics.get("x2_authorization") != "NOT_AUTHORIZED"
        or run.get("qualification_status") != expected_qualification
        or slices.get("rows")
        != _unit_slice_rows(
            membership=membership,
            baseline_rows=controls,
            evaluations=(
                (CodexX1Arm.CX1_NORMALIZED, normalized_records),
                (CodexX1Arm.CX1_DERIVED_FACTS, facts_records),
            ),
        )
    ):
        raise CodexX1VerificationError("production metrics or slices do not recompute")

    expected_latency = [
        *[
            {
                "arm": CodexX1Arm.BASELINE_CONTROL.value,
                "case_id": case_id,
                "latency_ms": float(control["trace"]["latency_ms"]),
                "memory_peak_bytes": None,
                "source": "immutable_xb0_trace",
            }
            for case_id, control in zip(case_ids, controls, strict=True)
        ],
        *[
            {
                "arm": record.arm.value,
                "case_id": record.case_id,
                "latency_ms": record.latency_ms,
                "memory_peak_bytes": record.memory_peak_bytes,
                "source": "production_component_measurement",
            }
            for record in (*normalized_records, *facts_records)
        ],
    ]
    if latency.get("measurements") != expected_latency:
        raise CodexX1VerificationError("production latency records cross-file mismatch")
    expected_security_failures = {
        normalized_evaluation.arm.value: list(normalized_evaluation.hard_gate_failures),
        facts_evaluation.arm.value: list(facts_evaluation.hard_gate_failures),
    }
    if (
        security.get("status") != "clean"
        or security.get("findings") != []
        or security.get("hard_gate_failures") != expected_security_failures
        or security.get("checks", {}).get("network") != "not_used"
        or security.get("checks", {}).get("formal_database") != "not_accessed"
        or security.get("checks", {}).get("retrieval") != "not_executed"
    ):
        raise CodexX1VerificationError("production security contract mismatch")
    allowed_error_sources = {
        evidence.source_item_id
        for record in treatment_records
        for evidence in record.source_evidence
    }
    for row in errors:
        if (
            row.get("arm")
            not in {CodexX1Arm.CX1_NORMALIZED.value, CodexX1Arm.CX1_DERIVED_FACTS.value}
            or not isinstance(row.get("source_item_ids"), list)
            or not set(row["source_item_ids"]) <= allowed_error_sources
        ):
            raise CodexX1VerificationError("production diagnostic record is invalid")
    return CodexX1ArtifactVerification(
        artifact_path=artifact,
        artifact_set_hash=expected_set_hash,
        status="VERIFIED_PRODUCTION",
        run_created=True,
        metrics_created=True,
        production_execution="EXECUTED",
    )


def _verify_production_copy_with_bombs(path: Path) -> CodexX1ArtifactVerification:
    """Prove production verification cannot execute components or open SQLite."""

    import sqlite3
    from unittest.mock import patch

    from evidence_rag.codex_adapter import CodexSessionAdapter
    from evidence_rag.rag.sources.codex import baseline_v1, event_normalizer, facts_v1

    def bomb(*_: object, **__: object) -> None:
        raise AssertionError("verify-only attempted execution")

    patches = [
        patch.object(sqlite3, "connect", bomb),
        patch.object(CodexSessionAdapter, "parse", bomb),
        patch.object(event_normalizer, "normalize_codex_events_v1", bomb),
        patch.object(facts_v1, "build_codex_fact_publication_v1", bomb),
    ]
    baseline_runner = getattr(baseline_v1, "run_codex_baseline_v1", None)
    if baseline_runner is not None:
        patches.append(patch.object(baseline_v1, "run_codex_baseline_v1", bomb))
    for active in patches:
        active.start()
    try:
        return verify_codex_x1_artifact_v1(path)
    finally:
        for active in reversed(patches):
            active.stop()


def run_codex_x1_v1(
    request: CodexX1RunRequest,
) -> CodexX1RunAdmission | CodexX1RunResult:
    """Admit an exact request or execute its one explicit isolated Run."""

    if type(request) is not CodexX1RunRequest:
        raise CodexX1NotAuthorizedError("production request must be the exact frozen contract")
    if request.output_root.exists() or request.output_root.is_symlink():
        raise CodexX1NotAuthorizedError("isolated output is no longer fresh")
    if request.raw_sqlite_path.exists() or request.raw_sqlite_path.is_symlink():
        raise CodexX1NotAuthorizedError("temporary raw SQLite path is no longer fresh")
    try:
        prepared = prepare_codex_x1_v1(
            golden_root=request.golden_root,
            correction_root=request.correction_root,
            gate_review=request.gate_review,
        )
    except (CodexX1Error, ValueError) as exc:
        raise CodexX1NotAuthorizedError(
            "production X-T1 admission could not reproduce exact preparation"
        ) from exc
    if (
        request.preparation.canonical_sha256() != prepared.canonical_sha256()
        or prepared.production_execution != "AUTHORIZED"
        or not prepared.gate.production_authorized
        or prepared.arms[-1].availability != Availability.AVAILABLE
        or prepared.cx1_02_identity_status != "MATCH"
        or prepared.cx1_02_component_set_hash != CODEX_X1_CX1_02_COMPONENT_SET_HASH
        or request.production_authorization != CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION
    ):
        raise CodexX1NotAuthorizedError(
            "production X-T1 admission requires exact ready preparation and joint Gate"
        )
    if request.output_root.exists() or request.raw_sqlite_path.exists():
        raise CodexX1NotAuthorizedError("isolated output changed during admission")
    admission = CodexX1RunAdmission(
        preparation_sha256=prepared.canonical_sha256(),
        gate_decision_block_sha256=prepared.gate.decision_block_sha256,
        membership_sha256=prepared.membership.content_sha256,
    )
    if not request.execute:
        return admission
    production_runs = (_project_root() / "evals" / "codex" / "runs").resolve(strict=True)
    if (
        request.output_root.parent.resolve(strict=True) != production_runs
        or not re.fullmatch(r"[0-9a-f]{32}", request.output_root.name)
        or prepared.canonical_sha256() != CODEX_X1_PRODUCTION_PREPARATION_SHA256
        or prepared.gate.conclusion_number != "11.1"
        or prepared.gate.decision_block_sha256 != CODEX_X1_PRODUCTION_GATE_DECISION_SHA256
        or prepared.membership.content_sha256 != CODEX_X1_MEMBERSHIP_SHA256
    ):
        raise CodexX1NotAuthorizedError(
            "execution requires the exact final Gate 11.1 preparation and one new Run path"
        )

    import socket
    import sqlite3

    from evidence_rag.rag.sources.codex.event_normalizer import (
        normalize_codex_events_v1,
    )
    from evidence_rag.rag.sources.codex.facts_v1 import (
        CodexFactPublicationDisposition,
        CodexFactPublicationScope,
        SQLiteCodexFactStore,
        build_codex_fact_publication_v1,
    )

    run_id = request.output_root.name
    fixture_copy = request.isolated_root / "released-fixture"
    stage = request.isolated_root / run_id
    portable_copy = request.isolated_root / f"verify-copy-{run_id}"
    sidecars = tuple(
        Path(str(request.raw_sqlite_path) + suffix) for suffix in ("-wal", "-shm", "-journal")
    )
    persistent_started = False
    original_connect = sqlite3.connect
    original_socket = socket.socket

    def forbidden_socket(*_: object, **__: object) -> None:
        raise CodexX1VerificationError("network access is forbidden during X-T1")

    def isolated_connect(database: object, *args: object, **kwargs: object) -> Any:
        if str(database) == ":memory:":
            return original_connect(database, *args, **kwargs)
        try:
            resolved = Path(str(database)).resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise CodexX1VerificationError("SQLite target cannot be resolved") from exc
        if resolved != request.raw_sqlite_path.resolve(strict=False):
            raise CodexX1VerificationError("formal/default SQLite access is forbidden")
        return original_connect(str(resolved), *args, **kwargs)

    try:
        socket.socket = forbidden_socket  # type: ignore[assignment]
        sqlite3.connect = isolated_connect  # type: ignore[assignment]
        isolated_fixture, released_manifest = _copy_released_adapter_fixture(request)
        adapter_items, source_files, adapter_counters = _parse_released_adapter_items(
            isolated_fixture,
            released_manifest,
            request.raw_sqlite_path,
        )
        adapter_by_id = {item.id: item for item in adapter_items}
        if (
            len(adapter_by_id) != 54
            or any(item.acl_ref != "project:project-rag" for item in adapter_items)
            or any(
                item.item_type in {"reasoning", "agent_reasoning", "token_count"}
                for item in adapter_items
            )
        ):
            raise CodexX1VerificationError("adapter observable security boundary failed")
        (
            source_items,
            source_identity,
            observable_by_adapter,
            derived_validations,
        ) = _observable_sources_from_adapter(adapter_items)
        if len(source_items) != 8:
            raise CodexX1VerificationError("Observable sources must preserve eight threads")

        normalized_results: dict[str, Any] = {}
        normalized_measurements: dict[str, tuple[float, int]] = {}
        for thread_id, items in sorted(source_items.items()):
            result, elapsed_ms, peak = _timed_call(
                lambda exact_items=items: normalize_codex_events_v1(exact_items)
            )
            normalized_results[thread_id] = result
            normalized_measurements[thread_id] = (elapsed_ms, peak)

        publications: dict[str, Any] = {}
        facts_results: dict[str, Any] = {}
        facts_measurements: dict[str, tuple[float, int]] = {}
        with SQLiteCodexFactStore(request.raw_sqlite_path) as store:
            for thread_id, items in sorted(source_items.items()):
                first = items[0]
                publication_scope = CodexFactPublicationScope(
                    project_id="project-rag",
                    repository_id="project-rag-repository",
                    source_id=first.scope.source_id,
                    source_version="codex-golden-v1",
                    generation_id=first.scope.generation_id,
                    thread_id=thread_id,
                    acl_ref=first.scope.acl_ref,
                )

                def publish_exact(
                    *,
                    exact_scope: Any = publication_scope,
                    exact_items: tuple[Any, ...] = items,
                    expected: Any = normalized_results[thread_id],
                ) -> Any:
                    publication = build_codex_fact_publication_v1(
                        exact_scope,
                        exact_items,
                        expected_normalization=expected,
                    )
                    result = store.publish(publication, source_items=exact_items)
                    if (
                        result.disposition is not CodexFactPublicationDisposition.PUBLISHED
                        or result.active_publication_id != publication.publication_id
                    ):
                        raise CodexX1VerificationError(
                            "CX1-02 isolated publication did not activate exactly once"
                        )
                    return publication

                publication, elapsed_ms, peak = _timed_call(publish_exact)
                publications[thread_id] = publication
                facts_results[thread_id] = publication.normalization
                facts_measurements[thread_id] = (elapsed_ms, peak)
            if store.publication_count() != 8:
                raise CodexX1VerificationError("CX1-02 publication count differs from threads")

        normalized_records = _treatment_records_from_results(
            preparation=prepared,
            arm=CodexX1Arm.CX1_NORMALIZED,
            results=normalized_results,
            source_items=source_items,
            source_identity=source_identity,
            observable_by_adapter=observable_by_adapter,
            derived_validations=derived_validations,
            adapter_by_id=adapter_by_id,
            source_files=source_files,
            measurements=normalized_measurements,
        )
        facts_records = _treatment_records_from_results(
            preparation=prepared,
            arm=CodexX1Arm.CX1_DERIVED_FACTS,
            results=facts_results,
            source_items=source_items,
            source_identity=source_identity,
            observable_by_adapter=observable_by_adapter,
            derived_validations=derived_validations,
            adapter_by_id=adapter_by_id,
            source_files=source_files,
            measurements=facts_measurements,
            publications=publications,
        )
        dataset = load_codex_golden_v1(request.golden_root)
        baseline_rows = _correction_prediction_rows(request.correction_root)
        fixture_files = [
            {
                "path": entry["path"],
                "sha256": entry["sha256"],
                "size": entry["size"],
            }
            for entry in released_manifest["files"]
            if entry["kind"] == "session"
        ]
        stage_verification = _build_codex_x1_production_artifact(
            stage,
            preparation=prepared,
            dataset=dataset,
            baseline_rows=baseline_rows,
            normalized_records=normalized_records,
            facts_records=facts_records,
            normalized_results=normalized_results,
            facts_results=facts_results,
            source_identity=source_identity,
            adapter_counters=adapter_counters,
            fixture_files=fixture_files,
        )
        shutil.copytree(stage, portable_copy, copy_function=shutil.copyfile)
        copy_verification = _verify_production_copy_with_bombs(portable_copy)
        if copy_verification.artifact_set_hash != stage_verification.artifact_set_hash:
            raise CodexX1VerificationError("portable copy differs from original artifact")

        sqlite3.connect = original_connect  # type: ignore[assignment]
        socket.socket = original_socket  # type: ignore[assignment]
        if request.raw_sqlite_path.exists():
            request.raw_sqlite_path.unlink()
        for sidecar in sidecars:
            if sidecar.exists():
                sidecar.unlink()
        if request.raw_sqlite_path.exists() or any(sidecar.exists() for sidecar in sidecars):
            raise CodexX1VerificationError("temporary SQLite or sidecar cleanup failed")
        if fixture_copy.exists():
            shutil.rmtree(fixture_copy)

        if request.output_root.exists() or request.output_root.is_symlink():
            raise CodexX1VerificationError("persistent Run output changed before publication")
        persistent_started = True
        shutil.copytree(stage, request.output_root, copy_function=shutil.copyfile)
        final_verification = _verify_production_copy_with_bombs(request.output_root)
        if final_verification.artifact_set_hash != stage_verification.artifact_set_hash:
            raise CodexX1VerificationError("published Run differs from verified original")
        run_payload = _read_canonical_json(request.output_root / "run.json")
        return CodexX1RunResult(
            status="COMPLETED",
            run_id=run_id,
            run_uri=str(run_payload["artifact_uri"]),
            artifact_path=request.output_root,
            artifact_set_hash=final_verification.artifact_set_hash,
            preparation_sha256=prepared.canonical_sha256(),
            gate_decision_block_sha256=prepared.gate.decision_block_sha256,
            membership_sha256=prepared.membership.content_sha256,
            qualification_status=str(run_payload["qualification_status"]),
        )
    except Exception:
        if (
            persistent_started
            and request.output_root.exists()
            and not request.output_root.is_symlink()
        ):
            shutil.rmtree(request.output_root)
        raise
    finally:
        sqlite3.connect = original_connect  # type: ignore[assignment]
        socket.socket = original_socket  # type: ignore[assignment]
        for path in (stage, portable_copy, fixture_copy):
            if path.exists() and not path.is_symlink():
                shutil.rmtree(path)
        if request.raw_sqlite_path.exists() and not request.raw_sqlite_path.is_symlink():
            request.raw_sqlite_path.unlink()
        for sidecar in sidecars:
            if sidecar.exists() and not sidecar.is_symlink():
                sidecar.unlink()


__all__ = [
    "CODEX_X1_ARTIFACT_VERSION",
    "CODEX_X1_CANONICAL_ARTIFACT_FILES",
    "CODEX_X1_CX1_01_COMPONENT_SET_HASH",
    "CODEX_X1_CX1_02_COMPONENT_SET_HASH",
    "CODEX_X1_GATE_REVIEW_CANONICAL_PATH",
    "CODEX_X1_MEMBERSHIP_SHA256",
    "CODEX_X1_PREPARATION_AUTHORIZATION",
    "CODEX_X1_PRODUCTION_GATE_DECISION_SHA256",
    "CODEX_X1_PRODUCTION_PREPARATION_SHA256",
    "CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION",
    "CODEX_X1_PRODUCTION_RUN_VERSION",
    "CODEX_X1_RUNNER_VERSION",
    "CODEX_X1_SCHEMA_VERSION",
    "CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH",
    "CODEX_XB0_CORRECTION_URI",
    "Availability",
    "CodexX1Arm",
    "CodexX1ArtifactVerification",
    "CodexX1AuthorityUnit",
    "CodexX1BaselineAnchor",
    "CodexX1CaseAuthority",
    "CodexX1ComponentIdentity",
    "CodexX1Error",
    "CodexX1Evaluation",
    "CodexX1EventEvidence",
    "CodexX1FactsVersions",
    "CodexX1GateDecision",
    "CodexX1LinkPrediction",
    "CodexX1Membership",
    "CodexX1MetricName",
    "CodexX1MetricResult",
    "CodexX1NotAuthorizedError",
    "CodexX1Preparation",
    "CodexX1ProductionGateDecision",
    "CodexX1RunAdmission",
    "CodexX1RunRequest",
    "CodexX1RunResult",
    "CodexX1SecurityFinding",
    "CodexX1SourceEvidence",
    "CodexX1SmokeResult",
    "CodexX1StatePrediction",
    "CodexX1TreatmentRecord",
    "CodexX1VerificationError",
    "CodexX1WindowEvidence",
    "build_codex_x1_smoke_artifact_v1",
    "cx1_01_component_identities_v1",
    "cx1_02_component_identities_v1",
    "derive_codex_x1_membership_v1",
    "evaluate_codex_x1_records_v1",
    "parse_codex_x1_gate_review_v1",
    "parse_codex_x1_production_gate_review_v1",
    "prepare_codex_x1_v1",
    "recompute_xb0_state_truth_v1",
    "run_codex_x1_v1",
    "verify_codex_x1_artifact_v1",
]
