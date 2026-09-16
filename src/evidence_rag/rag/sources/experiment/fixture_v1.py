"""Deterministic isolated Experiment fixture and production-lane parity.

The released recipe contains logical, portable identities only.  Materialized
MLflow paths and SQLite databases exist solely below an explicit system
temporary root.  The production-lane helper calls the current MLflow adapter
through ``ExperimentService.sync_mlflow`` and the current manual service
through its public Pydantic models; it never fabricates adapter return values.
"""

from __future__ import annotations

import builtins
import dis
import hashlib
import importlib
import inspect
import json
import math
import re
import sys
import tempfile
import types
import typing
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
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
from pydantic_core import PydanticUndefined

import evidence_rag.experiments.adapters as experiment_adapters_public
import evidence_rag.experiments.adapters.mlflow as mlflow_module
import evidence_rag.experiments.models as experiment_models_public
import evidence_rag.experiments.service as experiment_service_public
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
    ExperimentCreate,
    MetricInput,
    MLflowSyncRequest,
    RunCreate,
)
from evidence_rag.experiments.service import ExperimentService
from evidence_rag.runtime import create_runtime
from evidence_rag.workspace.models import ProjectCreate

_CANONICAL_PRODUCTION_OBJECTS: dict[str, object] = {
    "mlflow-adapter-class": MLflowAdapter,
    "experiment-service-class": ExperimentService,
    "experiment-sync-mlflow": ExperimentService.sync_mlflow,
    "experiment-create-experiment": ExperimentService.create_experiment,
    "experiment-create-run": ExperimentService.create_run,
    "experiment-create-model": ExperimentCreate,
    "run-create-model": RunCreate,
}

EXPERIMENT_FIXTURE_RECIPE_VERSION = "experiment-fixture-recipe-v2"
EXPERIMENT_FIXTURE_EXPERIMENT_ID = "experiment://fixture/experiment-v1-main"
EXPERIMENT_FIXTURE_EXTERNAL_EXPERIMENT_ID = "101"
EXPERIMENT_FIXTURE_PROJECT_ID = "project-experiment-fixture-v1"
EXPERIMENT_FIXTURE_ACL_REF = f"project:{EXPERIMENT_FIXTURE_PROJECT_ID}"
EXPERIMENT_FIXTURE_RUN_COUNT = 12
EXPERIMENT_FIXTURE_SEEDS = (11, 22, 33)
EXPERIMENT_FIXTURE_FIXED_STARTED_MS = 1_788_154_400_000
EXPERIMENT_PRODUCTION_AUTHORITY_VERSION = "experiment-production-authority-v1"
EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST = (
    "sha256:0c961eb90c9a30795debc25d4d9c04eb6f38d4214a2ae3d71f03f54f812f405d"
)
EXPERIMENT_PRODUCTION_PARITY_HASH = (
    "sha256:df0d6a7f745bfda76bb4e68ce52063c0010ef474ee8afe584d4f20e2fd92ceb9"
)
EXPERIMENT_CURRENT_PRODUCTION_AUTHORITY_VERSION = "experiment-current-production-authority-v5"
EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST = (
    "sha256:8096c26fb306d5b98e57fe4c60f33f48bb18345dd92f83b020f3ed370e35d99c"
)
EXPERIMENT_CURRENT_PRODUCTION_PARITY_HASH = (
    "sha256:2cf348a6557152d32a91dba4cb5c020ad312dc071b8e23c5f288a5f82d629ac8"
)

_SHA256_RE = __import__("re").compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = __import__("re").compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")

_FIXED_PRODUCTION_AUTHORITY_ROWS: tuple[dict[str, str], ...] = (
    {
        "role": "mlflow-adapter-class",
        "kind": "class",
        "module": "evidence_rag.experiments.adapters.mlflow",
        "public_module": "evidence_rag.experiments.adapters",
        "export": "MLflowAdapter",
        "qualname": "MLflowAdapter",
        "version": "mlflow-tracking-v1",
        "source_sha256": (
            "sha256:b07e70fa726882717a87ecdda82ce1492872e1eac0f8707b89647ba5782ec879"
        ),
        "code_sha256": ("sha256:bbf90398d0e95bcad37de343f56c35bbe3c99845303dee52eeeedde60f216609"),
    },
    {
        "role": "experiment-service-class",
        "kind": "class",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService",
        "qualname": "ExperimentService",
        "version": "experiment-service-v1",
        "source_sha256": (
            "sha256:b35a9fa17616d5530d79749a987f061c59418aa0a945a1b9f25e9ba6a58e90a6"
        ),
        "code_sha256": ("sha256:bff4453cd31a0a0bb521e3502c80b8976ad7ca21c1d3b9e765eb7672fdeab732"),
    },
    {
        "role": "experiment-sync-mlflow",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService.sync_mlflow",
        "qualname": "ExperimentService.sync_mlflow",
        "version": "experiment-service-v1",
        "source_sha256": (
            "sha256:20b4f5dab82087621a213b5678242a1ea9fb3b0b67e5a1ca28c19f14120c4ed9"
        ),
        "code_sha256": ("sha256:72593388729fc40476df3b1cf5671f32a2df088213eaf73b99c5a4916259d66d"),
    },
    {
        "role": "experiment-create-experiment",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService.create_experiment",
        "qualname": "ExperimentService.create_experiment",
        "version": "experiment-service-v1",
        "source_sha256": (
            "sha256:4460544a0484a5cecc703cd3c6b29524bd073dba87ba774f23b6e6a04e74bf8c"
        ),
        "code_sha256": ("sha256:d89ca387e5e7a21eb4766705ee7f08c588c0af832d8c0a93e296164f5ee4322d"),
    },
    {
        "role": "experiment-create-run",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService.create_run",
        "qualname": "ExperimentService.create_run",
        "version": "experiment-service-v1",
        "source_sha256": (
            "sha256:5cfbff838c0aefa32ab7179e31843ecf807844fad5aead210baf18610c8428ef"
        ),
        "code_sha256": ("sha256:8a426eb996ea4657323f3d2c51a946c354ce40953e1d081f29956beb1ae72624"),
    },
    {
        "role": "experiment-create-model",
        "kind": "class",
        "module": "evidence_rag.experiments.models",
        "public_module": "evidence_rag.experiments.models",
        "export": "ExperimentCreate",
        "qualname": "ExperimentCreate",
        "version": "experiment-public-model-v1",
        "source_sha256": (
            "sha256:90e93eeff407314e6a7c44dbe7ed07ba3b9385e2d9fae5477749f927bb08a3e8"
        ),
        "code_sha256": ("sha256:d71b28fedb63b93c505ef29301ef85596a6846c37be375234f9363bd3c823e26"),
    },
    {
        "role": "run-create-model",
        "kind": "class",
        "module": "evidence_rag.experiments.models",
        "public_module": "evidence_rag.experiments.models",
        "export": "RunCreate",
        "qualname": "RunCreate",
        "version": "experiment-public-model-v1",
        "source_sha256": (
            "sha256:c3fa6e827a3178385ea0fff25749ea769b3bc8357268050d683e0b7c9b752e93"
        ),
        "code_sha256": ("sha256:05346219020bdcd607240c64f44d3c3e08ad76f8c335c8673c5a90e74082f103"),
    },
)

# This authority is intentionally separate from the frozen V1 rows above.  The
# V1 rows remain part of the released Golden recipe identity; these reviewed
# rows authorize only current isolated production execution.
_FIXED_CURRENT_PRODUCTION_AUTHORITY_ROWS: tuple[dict[str, str], ...] = (
    {
        "role": "mlflow-adapter-class",
        "kind": "class",
        "module": "evidence_rag.experiments.adapters.mlflow",
        "public_module": "evidence_rag.experiments.adapters",
        "export": "MLflowAdapter",
        "qualname": "MLflowAdapter",
        "version": "mlflow-tracking-v2",
        "source_sha256": "sha256:20e299abfe1ade4e85637e8b6da478a9363ad5f1f8cc65732172ac352b7eb6b5",
        "code_sha256": "sha256:256632ae82d0333729aeac680b2d2afeb558db7824ebb77e4905c461723ebe36",
        "bindings_sha256": "sha256:b94d1f49edf7fa94824f88f92ce5d288811bb208a2997055f4501e2075cb5d18",
    },
    {
        "role": "experiment-service-class",
        "kind": "class",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService",
        "qualname": "ExperimentService",
        "version": "experiment-service-v3",
        "source_sha256": "sha256:13403175f2499205fc215777d55bf0807ccb54c2c7755e28355824cdb250d98c",
        "code_sha256": "sha256:7d9ae7d0cca8d49fc8faa2ffce884b26f2f8bebed1861c333391a77eb81283f9",
        "bindings_sha256": "sha256:e5b28cd35c8b416387cb2d56d84376db49fafba4c33def4833dedcc2e60e7c41",
    },
    {
        "role": "experiment-sync-mlflow",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService.sync_mlflow",
        "qualname": "ExperimentService.sync_mlflow",
        "version": "experiment-service-v2",
        "source_sha256": "sha256:5311256ac5e03531e88abd8a25ab95502edf86331ca6f09cbc73774be05ae3b2",
        "code_sha256": "sha256:22967e8c2ee853500404a777712cdbb1fa3d44fc0739a9ab339898bbf0fe28d6",
        "bindings_sha256": "sha256:09b2af1712769ef82a9ad25f9c99d161da414fe9964ecd8ac9a1ca5e5746f69d",
    },
    {
        "role": "experiment-create-experiment",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService.create_experiment",
        "qualname": "ExperimentService.create_experiment",
        "version": "experiment-service-v2",
        "source_sha256": "sha256:4460544a0484a5cecc703cd3c6b29524bd073dba87ba774f23b6e6a04e74bf8c",
        "code_sha256": "sha256:21516fe4b51ce37a1583ad25091c9e289d2fac98cc9945b18bfe5ede25f43b80",
        "bindings_sha256": "sha256:88ef6ce69edec92321b02d2ccf9d69dca2d4a0223ffc8dd523425ea669d02911",
    },
    {
        "role": "experiment-create-run",
        "kind": "method",
        "module": "evidence_rag.experiments.service",
        "public_module": "evidence_rag.experiments.service",
        "export": "ExperimentService.create_run",
        "qualname": "ExperimentService.create_run",
        "version": "experiment-service-v1",
        "source_sha256": "sha256:5cfbff838c0aefa32ab7179e31843ecf807844fad5aead210baf18610c8428ef",
        "code_sha256": "sha256:96d8cbe6f3c00f8ce877dfa7289d952f802a0d9ab613e443d971b16a1ad95b07",
        "bindings_sha256": "sha256:eed91bbf02dbb5923fe8f1d7af94046aa174786202324d4bb96aa04e2c63ea82",
    },
    {
        "role": "experiment-create-model",
        "kind": "class",
        "module": "evidence_rag.experiments.models",
        "public_module": "evidence_rag.experiments.models",
        "export": "ExperimentCreate",
        "qualname": "ExperimentCreate",
        "version": "experiment-public-model-v1",
        "source_sha256": "sha256:90e93eeff407314e6a7c44dbe7ed07ba3b9385e2d9fae5477749f927bb08a3e8",
        "code_sha256": "sha256:5b6845ce1285e346c7f339cf23d0b7190b2e3316c5929fbe7c7ec3e13652a101",
        "bindings_sha256": "sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9",
    },
    {
        "role": "run-create-model",
        "kind": "class",
        "module": "evidence_rag.experiments.models",
        "public_module": "evidence_rag.experiments.models",
        "export": "RunCreate",
        "qualname": "RunCreate",
        "version": "experiment-public-model-v2",
        "source_sha256": "sha256:c3fa6e827a3178385ea0fff25749ea769b3bc8357268050d683e0b7c9b752e93",
        "code_sha256": "sha256:9f6633f6bfb28b6bbd4b0792c4cbfc28f1ba4f74f08367c28ee140fbda3c791b",
        "bindings_sha256": "sha256:93c1e6d0f0e1deb09626434aff7cf79fd01be4a711f09ee94eb359bb9d68d6d9",
    },
)


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


Identifier = Annotated[StrictStr, AfterValidator(_validate_identifier)]
Sha256 = Annotated[StrictStr, AfterValidator(_validate_sha256)]
ContractText = Annotated[StrictStr, Field(min_length=1, max_length=8_000)]
Scalar = StrictBool | StrictInt | StrictFloat | StrictStr
PositiveInt = Annotated[StrictInt, Field(ge=1)]
FixtureRunGroup = Literal[
    "baseline",
    "treatment",
    "dataset_mismatch",
    "unit_mismatch",
    "failed_high_score",
    "running",
    "missing_metadata",
    "typed_config_hard_negative",
]


class ExperimentFixtureError(ValueError):
    """Fail-closed fixture materialization or parity error."""


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


class ProductionComponentIdentity(_FrozenContract):
    role: Identifier
    kind: Literal["class", "method"]
    module: ContractText
    public_module: ContractText
    export: ContractText
    qualname: ContractText
    version: Identifier
    source_sha256: Sha256
    code_sha256: Sha256


class CurrentProductionComponentIdentity(ProductionComponentIdentity):
    bindings_sha256: Sha256


class ProductionComponentAuthority(_FrozenContract):
    schema_version: Literal["experiment-production-authority-v1"] = (
        EXPERIMENT_PRODUCTION_AUTHORITY_VERSION
    )
    components: tuple[ProductionComponentIdentity, ...]
    component_set_digest: Sha256

    @model_validator(mode="after")
    def _fixed_reviewed_authority(self) -> Self:
        fixed = tuple(
            ProductionComponentIdentity.model_validate(row)
            for row in _FIXED_PRODUCTION_AUTHORITY_ROWS
        )
        if (
            self.components != fixed
            or self.component_set_digest != EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST
            or _component_set_hash(self.components) != self.component_set_digest
        ):
            raise ValueError("production component authority differs from reviewed fixed identity")
        return self


class CurrentProductionComponentAuthority(_FrozenContract):
    schema_version: Literal["experiment-current-production-authority-v5"] = (
        EXPERIMENT_CURRENT_PRODUCTION_AUTHORITY_VERSION
    )
    components: tuple[CurrentProductionComponentIdentity, ...]
    component_set_digest: Sha256

    @model_validator(mode="after")
    def _fixed_reviewed_current_authority(self) -> Self:
        fixed = tuple(
            CurrentProductionComponentIdentity.model_validate(row)
            for row in _FIXED_CURRENT_PRODUCTION_AUTHORITY_ROWS
        )
        if (
            self.components != fixed
            or self.component_set_digest != EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST
            or _component_set_hash(self.components) != self.component_set_digest
        ):
            raise ValueError(
                "current production component authority differs from reviewed fixed identity"
            )
        return self


def _source_digest(value: object) -> str:
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError) as exc:
        raise ExperimentFixtureError("production component source is unavailable") from exc
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
        raise ExperimentFixtureError(str(exc)) from exc
    return _sha256(_canonical_json(payload))


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
            raw,
            (type(None), bool, int, float, str, tuple, frozenset),
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
    payload = {
        "bases": [(base.__module__, base.__qualname__) for base in value.__bases__],
        "members": members,
        "attrs": attributes,
        "model_fields": model_fields,
    }
    return _sha256(_canonical_json(payload))


def _runtime_code_digest(value: object, kind: str) -> str:
    if kind == "class" and isinstance(value, type):
        return _class_code_digest(value)
    if kind == "method" and isinstance(value, types.FunctionType):
        return _function_code_digest(value)
    raise ExperimentFixtureError("production component kind/runtime object mismatch")


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
    except ExperimentFixtureError:
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
        raise ExperimentFixtureError(f"{value.__qualname__} does not use canonical module globals")
    if value.__builtins__ is not builtins.__dict__:
        raise ExperimentFixtureError(f"{value.__qualname__} does not use canonical builtins")
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
                raise ExperimentFixtureError(
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
        for name, cell in zip(value.__code__.co_freevars, value.__closure__, strict=True):
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
        raise ExperimentFixtureError("production component kind/runtime binding mismatch")
    return _sha256(_canonical_json(payload))


def _component_set_hash(
    components: Sequence[ProductionComponentIdentity | CurrentProductionComponentIdentity],
) -> str:
    return _sha256(_canonical_json([item.model_dump(mode="json") for item in components]))


def _fixed_production_authority() -> ProductionComponentAuthority:
    try:
        components = tuple(
            ProductionComponentIdentity.model_validate(row)
            for row in _FIXED_PRODUCTION_AUTHORITY_ROWS
        )
        return ProductionComponentAuthority(
            components=components,
            component_set_digest=EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST,
        )
    except ValueError as exc:
        raise ExperimentFixtureError(
            "fixed production component authority is internally invalid"
        ) from exc


def _fixed_current_production_authority() -> CurrentProductionComponentAuthority:
    try:
        components = tuple(
            CurrentProductionComponentIdentity.model_validate(row)
            for row in _FIXED_CURRENT_PRODUCTION_AUTHORITY_ROWS
        )
        return CurrentProductionComponentAuthority(
            components=components,
            component_set_digest=EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST,
        )
    except ValueError as exc:
        raise ExperimentFixtureError(
            "fixed current production component authority is internally invalid"
        ) from exc


def _resolve_export(module_name: str, export: str) -> object:
    module = importlib.import_module(module_name)
    current: object = module
    for part in export.split("."):
        if not hasattr(current, part):
            raise ExperimentFixtureError(
                f"production export is unavailable: {module_name}.{export}"
            )
        current = getattr(current, part)
    return current


def verify_production_component_authority(
    expected: CurrentProductionComponentAuthority | None = None,
) -> CurrentProductionComponentAuthority:
    """Validate exact reviewed current public objects before parity I/O."""

    authority = _fixed_current_production_authority()
    if expected is not None and expected != authority:
        raise ExperimentFixtureError(
            "requested current production authority differs from reviewed authority"
        )
    fixture_bindings: dict[str, object] = {
        "mlflow-adapter-class": MLflowAdapter,
        "experiment-service-class": ExperimentService,
        "experiment-sync-mlflow": ExperimentService.sync_mlflow,
        "experiment-create-experiment": ExperimentService.create_experiment,
        "experiment-create-run": ExperimentService.create_run,
        "experiment-create-model": ExperimentCreate,
        "run-create-model": RunCreate,
    }
    for role, canonical in _CANONICAL_PRODUCTION_OBJECTS.items():
        if fixture_bindings[role] is not canonical:
            raise ExperimentFixtureError(f"{role} fixture binding was replaced")
    imported_modules = {
        "evidence_rag.experiments.adapters.mlflow": mlflow_module,
        "evidence_rag.experiments.adapters": experiment_adapters_public,
        "evidence_rag.experiments.models": experiment_models_public,
        "evidence_rag.experiments.service": experiment_service_public,
    }
    for module_name, bound_module in imported_modules.items():
        if importlib.import_module(module_name) is not bound_module:
            raise ExperimentFixtureError(f"production module binding was replaced: {module_name}")
    for identity in authority.components:
        actual = _resolve_export(identity.module, identity.export)
        public = _resolve_export(identity.public_module, identity.export)
        if actual is not public:
            raise ExperimentFixtureError(f"{identity.role} public export is an alias/replacement")
        if _CANONICAL_PRODUCTION_OBJECTS[identity.role] is not actual:
            raise ExperimentFixtureError(f"{identity.role} exact object was replaced")
        if (
            getattr(actual, "__module__", None) != identity.module
            or getattr(actual, "__qualname__", None) != identity.qualname
        ):
            raise ExperimentFixtureError(f"{identity.role} module/qualname was forged")
        if identity.kind == "class":
            if not isinstance(actual, type):
                raise ExperimentFixtureError(f"{identity.role} is not an exact public class")
        elif not isinstance(actual, types.FunctionType) or inspect.unwrap(actual) is not actual:
            raise ExperimentFixtureError(f"{identity.role} was wrapped or replaced")
        if (
            _source_digest(actual) != identity.source_sha256
            or _runtime_code_digest(actual, identity.kind) != identity.code_sha256
            or _runtime_binding_digest(actual, identity.kind) != identity.bindings_sha256
        ):
            raise ExperimentFixtureError(
                f"{identity.role} reviewed source/runtime/binding digest mismatch"
            )
    if (
        getattr(
            _resolve_export(
                "evidence_rag.experiments.adapters.mlflow",
                "MLflowAdapter",
            ),
            "adapter_version",
            None,
        )
        != "mlflow-tracking-v2"
    ):
        raise ExperimentFixtureError("MLflowAdapter reviewed version mismatch")
    if (
        experiment_service_public.MLflowAdapter
        is not _CANONICAL_PRODUCTION_OBJECTS["mlflow-adapter-class"]
    ):
        raise ExperimentFixtureError("ExperimentService MLflowAdapter binding was replaced")
    return authority


def _verify_runtime_service_binding(
    service: object,
    authority: CurrentProductionComponentAuthority,
) -> None:
    by_role = {
        identity.role: _resolve_export(identity.module, identity.export)
        for identity in authority.components
    }
    service_class = _CANONICAL_PRODUCTION_OBJECTS["experiment-service-class"]
    if by_role["experiment-service-class"] is not service_class:
        raise ExperimentFixtureError("runtime service public class binding was replaced")
    if type(service) is not service_class:
        raise ExperimentFixtureError("runtime service is not the exact reviewed class")
    for role, name in (
        ("experiment-sync-mlflow", "sync_mlflow"),
        ("experiment-create-experiment", "create_experiment"),
        ("experiment-create-run", "create_run"),
    ):
        bound = getattr(service, name, None)
        if (
            not isinstance(bound, types.MethodType)
            or bound.__self__ is not service
            or bound.__func__ is not _CANONICAL_PRODUCTION_OBJECTS[role]
            or by_role[role] is not _CANONICAL_PRODUCTION_OBJECTS[role]
        ):
            raise ExperimentFixtureError(f"runtime bound method differs from reviewed {role}")


class FixtureRunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    RUNNING = "running"


class FixtureMetricDirection(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"
    UNKNOWN = "unknown"


class FixtureVerificationState(StrEnum):
    VERIFIED_CHECKSUM = "verified_checksum"
    LOCATED_UNVERIFIED = "located_unverified"


class FixtureMetricDefinition(_FrozenContract):
    definition_id: ContractText
    name: Identifier
    canonical_unit: Literal["ratio", "ms", "score"]
    allowed_raw_units: tuple[Literal["ratio", "percent", "ms", "score"], ...]
    direction: FixtureMetricDirection
    split: Literal["test"]
    locator: ContractText

    @model_validator(mode="after")
    def _canonical_identity(self) -> Self:
        if self.definition_id != f"metric-definition://fixture/{self.name}":
            raise ValueError("MetricDefinition identity mismatch")
        expected_locator = f"{EXPERIMENT_FIXTURE_EXPERIMENT_ID}/metric-definitions/{self.name}"
        if self.locator != expected_locator:
            raise ValueError("MetricDefinition locator mismatch")
        if (
            not self.allowed_raw_units
            or len(self.allowed_raw_units) != len(set(self.allowed_raw_units))
            or self.canonical_unit not in self.allowed_raw_units
        ):
            raise ValueError("MetricDefinition allowed units are invalid")
        return self


class FixtureMetricPoint(_FrozenContract):
    observation_id: ContractText
    run_id: ContractText
    definition_id: ContractText
    series_id: ContractText
    unit: Literal["ratio", "percent", "ms", "score"]
    direction: FixtureMetricDirection
    split: Literal["test"]
    observed_at_ms: Annotated[StrictInt, Field(ge=1)]
    value: StrictFloat
    step: PositiveInt
    locator: ContractText

    @field_validator("value")
    @classmethod
    def _finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric values must be finite")
        return value


class FixtureMetric(_FrozenContract):
    series_id: ContractText
    run_id: ContractText
    definition_id: ContractText
    name: Identifier
    unit: Literal["ratio", "percent", "ms", "score"]
    direction: FixtureMetricDirection
    split: Literal["test"]
    locator: ContractText
    points: tuple[FixtureMetricPoint, ...]

    @model_validator(mode="after")
    def _ordered_points(self) -> Self:
        if not self.points:
            raise ValueError("metric requires at least one point")
        run_slug = self.run_id.removeprefix("run://fixture/")
        if self.run_id != f"run://fixture/{run_slug}" or not run_slug:
            raise ValueError("MetricSeries run identity mismatch")
        if self.series_id != f"metric-series://fixture/{run_slug}/{self.name}/test":
            raise ValueError("MetricSeries identity mismatch")
        expected_locator = (
            f"{EXPERIMENT_FIXTURE_EXPERIMENT_ID}/runs/{run_slug}/metric-series/{self.name}/test"
        )
        if self.locator != expected_locator:
            raise ValueError("MetricSeries locator mismatch")
        steps = [point.step for point in self.points]
        times = [point.observed_at_ms for point in self.points]
        if steps != list(range(1, len(self.points) + 1)):
            raise ValueError("metric steps must be contiguous and ordered")
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("metric observations must be time ordered")
        for point in self.points:
            expected_observation_id = (
                f"metric-observation://fixture/{run_slug}/{self.name}/test/step/{point.step}"
            )
            expected_point_locator = f"{self.locator}/observations/step/{point.step}"
            if (
                point.observation_id != expected_observation_id
                or point.run_id != self.run_id
                or point.definition_id != self.definition_id
                or point.series_id != self.series_id
                or point.unit != self.unit
                or point.direction != self.direction
                or point.split != self.split
                or point.locator != expected_point_locator
            ):
                raise ValueError("MetricObservation crosses series/run/definition authority")
        return self

    @property
    def latest(self) -> FixtureMetricPoint:
        return self.points[-1]


class FixtureArtifact(_FrozenContract):
    artifact_id: ContractText
    name: ContractText
    logical_uri: ContractText
    kind: Identifier
    media_type: ContractText | None = None
    verification_state: FixtureVerificationState
    checksum: Sha256 | None = None
    content: ContractText | None = None

    @model_validator(mode="after")
    def _verification_is_honest(self) -> Self:
        if self.verification_state == FixtureVerificationState.VERIFIED_CHECKSUM:
            if self.checksum is None or self.content is None:
                raise ValueError("verified artifact requires checksum and fixture content")
            if _sha256(self.content.encode("utf-8")) != self.checksum:
                raise ValueError("verified artifact checksum mismatch")
        elif self.checksum is not None or self.content is not None:
            raise ValueError("URI-only artifact cannot carry content or checksum")
        return self


class FixtureRun(_FrozenContract):
    logical_run_id: ContractText
    external_id: Identifier
    name: Identifier
    group: FixtureRunGroup
    status: FixtureRunStatus
    seed: StrictInt
    dataset_id: ContractText
    dataset_version: ContractText
    commit_sha: ContractText | None
    branch: Literal["main"] = "main"
    config: dict[Identifier, Scalar]
    environment: dict[Identifier, Scalar]
    command: ContractText
    started_at_ms: Annotated[StrictInt, Field(ge=1)]
    completed_at_ms: Annotated[StrictInt, Field(ge=1)] | None
    metrics: tuple[FixtureMetric, ...]
    artifacts: tuple[FixtureArtifact, ...] = ()

    @model_validator(mode="after")
    def _run_truth_is_coherent(self) -> Self:
        if self.status == FixtureRunStatus.RUNNING and self.completed_at_ms is not None:
            raise ValueError("running fixture run cannot be completed")
        if self.status != FixtureRunStatus.RUNNING and self.completed_at_ms is None:
            raise ValueError("terminal fixture run requires completed_at_ms")
        if self.completed_at_ms is not None and self.completed_at_ms < self.started_at_ms:
            raise ValueError("run completion precedes start")
        metric_names = [metric.name for metric in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("fixture run contains duplicate metric definitions")
        artifact_names = [artifact.name for artifact in self.artifacts]
        if len(artifact_names) != len(set(artifact_names)):
            raise ValueError("fixture run contains duplicate artifacts")
        return self


class ExperimentFixtureRecipe(_FrozenContract):
    schema_version: Literal["experiment-fixture-recipe-v2"] = EXPERIMENT_FIXTURE_RECIPE_VERSION
    logical_experiment_id: Literal["experiment://fixture/experiment-v1-main"] = (
        EXPERIMENT_FIXTURE_EXPERIMENT_ID
    )
    external_experiment_id: Literal["101"] = EXPERIMENT_FIXTURE_EXTERNAL_EXPERIMENT_ID
    project_id: Literal["project-experiment-fixture-v1"] = EXPERIMENT_FIXTURE_PROJECT_ID
    acl_ref: Literal["project:project-experiment-fixture-v1"] = EXPERIMENT_FIXTURE_ACL_REF
    title: Literal["Experiment retrieval quality fixture"] = "Experiment retrieval quality fixture"
    objective: Literal["Evaluate deterministic structured Experiment retrieval truth."] = (
        "Evaluate deterministic structured Experiment retrieval truth."
    )
    production_authority: ProductionComponentAuthority
    metric_definitions: tuple[FixtureMetricDefinition, ...]
    runs: tuple[FixtureRun, ...]

    @model_validator(mode="after")
    def _fixed_scenario(self) -> Self:
        if len(self.runs) != EXPERIMENT_FIXTURE_RUN_COUNT:
            raise ValueError("fixture recipe requires exactly 12 runs")
        if self.production_authority != _fixed_production_authority():
            raise ValueError("fixture production authority drift")
        expected_definition_names = ("latency_ms", "mystery_score", "ndcg_at_10")
        if tuple(item.name for item in self.metric_definitions) != expected_definition_names:
            raise ValueError("fixture requires the three canonical MetricDefinitions")
        definitions = {item.definition_id: item for item in self.metric_definitions}
        if len(definitions) != 3:
            raise ValueError("fixture MetricDefinition identities must be unique")
        external_ids = [run.external_id for run in self.runs]
        logical_ids = [run.logical_run_id for run in self.runs]
        if len(external_ids) != len(set(external_ids)) or len(logical_ids) != len(set(logical_ids)):
            raise ValueError("fixture run identities must be unique")
        groups = Counter(run.group for run in self.runs)
        expected = {
            "baseline": 3,
            "treatment": 3,
            "dataset_mismatch": 1,
            "unit_mismatch": 1,
            "failed_high_score": 1,
            "running": 1,
            "missing_metadata": 1,
            "typed_config_hard_negative": 1,
        }
        if groups != Counter(expected):
            raise ValueError("fixture run groups differ from the frozen recipe")
        for group in ("baseline", "treatment"):
            seeds = tuple(run.seed for run in self.runs if run.group == group)
            if seeds != EXPERIMENT_FIXTURE_SEEDS:
                raise ValueError(f"{group} seeds must be 11/22/33")
        string_config = next(run for run in self.runs if run.group == "typed_config_hard_negative")
        if string_config.config.get("workers") != "1":
            raise ValueError("typed hard negative must preserve string '1'")
        if not any(run.config.get("workers") == 1 for run in self.runs):
            raise ValueError("fixture requires numeric config value 1")
        if not any(
            metric.direction == FixtureMetricDirection.UNKNOWN
            for run in self.runs
            for metric in run.metrics
        ):
            raise ValueError("fixture requires an unknown-direction metric")
        states = {artifact.verification_state for run in self.runs for artifact in run.artifacts}
        if states != {
            FixtureVerificationState.VERIFIED_CHECKSUM,
            FixtureVerificationState.LOCATED_UNVERIFIED,
        }:
            raise ValueError("fixture requires verified and locator-only artifacts")
        series_ids: list[str] = []
        observation_ids: list[str] = []
        for ordinal, run in enumerate(self.runs, start=1):
            expected_started = EXPERIMENT_FIXTURE_FIXED_STARTED_MS + ordinal * 60_000
            if (
                run.external_id != f"expv1-run-{ordinal:03d}"
                or run.started_at_ms != expected_started
                or (
                    run.completed_at_ms is not None
                    and run.completed_at_ms != expected_started + 50_000
                )
            ):
                raise ValueError("Run order/timestamp authority mismatch")
            for metric in run.metrics:
                definition = definitions.get(metric.definition_id)
                if (
                    definition is None
                    or metric.run_id != run.logical_run_id
                    or metric.name != definition.name
                    or metric.unit not in definition.allowed_raw_units
                    or metric.direction != definition.direction
                    or metric.split != definition.split
                ):
                    raise ValueError("MetricSeries differs from its Run/MetricDefinition authority")
                if any(
                    point.observed_at_ms != run.started_at_ms + point.step * 10_000
                    for point in metric.points
                ):
                    raise ValueError("MetricObservation timestamp authority mismatch")
                series_ids.append(metric.series_id)
                observation_ids.extend(point.observation_id for point in metric.points)
        if len(series_ids) != 18 or len(series_ids) != len(set(series_ids)):
            raise ValueError("fixture requires 18 unique MetricSeries identities")
        if len(observation_ids) != 47 or len(observation_ids) != len(set(observation_ids)):
            raise ValueError("fixture requires 47 unique MetricObservation identities")
        return self

    @property
    def logical_counts(self) -> dict[str, int]:
        metrics = [metric for run in self.runs for metric in run.metrics]
        return {
            "experiments": 1,
            "runs": len(self.runs),
            "metric_definitions": len(self.metric_definitions),
            "metric_series": len(metrics),
            "metric_observations": sum(len(metric.points) for metric in metrics),
            "artifacts": sum(len(run.artifacts) for run in self.runs),
        }


def _points(
    run_ordinal: int,
    run_slug: str,
    name: str,
    definition_id: str,
    series_id: str,
    series_locator: str,
    unit: Literal["ratio", "percent", "ms", "score"],
    direction: FixtureMetricDirection,
    values: tuple[float, ...],
) -> tuple[FixtureMetricPoint, ...]:
    base = EXPERIMENT_FIXTURE_FIXED_STARTED_MS + run_ordinal * 60_000
    run_id = f"run://fixture/{run_slug}"
    return tuple(
        FixtureMetricPoint(
            observation_id=(f"metric-observation://fixture/{run_slug}/{name}/test/step/{index}"),
            run_id=run_id,
            definition_id=definition_id,
            series_id=series_id,
            unit=unit,
            direction=direction,
            split="test",
            observed_at_ms=base + index * 10_000,
            value=value,
            step=index,
            locator=f"{series_locator}/observations/step/{index}",
        )
        for index, value in enumerate(values, start=1)
    )


def _metric(
    run_slug: str,
    ordinal: int,
    name: str,
    unit: Literal["ratio", "percent", "ms", "score"],
    direction: FixtureMetricDirection,
    values: tuple[float, ...],
) -> FixtureMetric:
    run_id = f"run://fixture/{run_slug}"
    definition_id = f"metric-definition://fixture/{name}"
    series_id = f"metric-series://fixture/{run_slug}/{name}/test"
    locator = f"{EXPERIMENT_FIXTURE_EXPERIMENT_ID}/runs/{run_slug}/metric-series/{name}/test"
    return FixtureMetric(
        series_id=series_id,
        run_id=run_id,
        definition_id=definition_id,
        name=name,
        unit=unit,
        direction=direction,
        split="test",
        locator=locator,
        points=_points(
            ordinal,
            run_slug,
            name,
            definition_id,
            series_id,
            locator,
            unit,
            direction,
            values,
        ),
    )


def _run(
    ordinal: int,
    slug: str,
    group: FixtureRunGroup,
    *,
    status: FixtureRunStatus = FixtureRunStatus.COMPLETED,
    seed: int,
    dataset_version: str = "dataset-v1",
    commit_sha: str | None = "1111111111111111111111111111111111111111",
    config: dict[str, Scalar] | None = None,
    environment: dict[str, Scalar] | None = None,
    metrics: tuple[FixtureMetric, ...],
    artifacts: tuple[FixtureArtifact, ...] = (),
) -> FixtureRun:
    started = EXPERIMENT_FIXTURE_FIXED_STARTED_MS + ordinal * 60_000
    return FixtureRun(
        logical_run_id=f"run://fixture/{slug}",
        external_id=f"expv1-run-{ordinal:03d}",
        name=slug,
        group=group,
        status=status,
        seed=seed,
        dataset_id="dataset://retrieval-benchmark",
        dataset_version=dataset_version,
        commit_sha=commit_sha,
        config=config or {"reranker": "bm25", "workers": 1},
        environment=(
            {"environment.python": "3.13", "hardware.device": "cpu"}
            if environment is None
            else environment
        ),
        command="python -m evaluation.run --dataset retrieval-benchmark",
        started_at_ms=started,
        completed_at_ms=None if status == FixtureRunStatus.RUNNING else started + 50_000,
        metrics=metrics,
        artifacts=artifacts,
    )


def deterministic_metric_definitions() -> tuple[FixtureMetricDefinition, ...]:
    definitions = (
        ("latency_ms", "ms", ("ms",), FixtureMetricDirection.LOWER),
        ("mystery_score", "score", ("score",), FixtureMetricDirection.UNKNOWN),
        (
            "ndcg_at_10",
            "ratio",
            ("ratio", "percent"),
            FixtureMetricDirection.HIGHER,
        ),
    )
    return tuple(
        FixtureMetricDefinition(
            definition_id=f"metric-definition://fixture/{name}",
            name=name,
            canonical_unit=canonical_unit,
            allowed_raw_units=allowed_units,
            direction=direction,
            split="test",
            locator=(f"{EXPERIMENT_FIXTURE_EXPERIMENT_ID}/metric-definitions/{name}"),
        )
        for name, canonical_unit, allowed_units, direction in definitions
    )


def deterministic_experiment_recipe() -> ExperimentFixtureRecipe:
    """Return the frozen one-Experiment/twelve-Run logical recipe."""

    verified_content = '{"ndcg_at_10":0.86,"split":"test"}\n'
    verified = FixtureArtifact(
        artifact_id="artifact://fixture/treatment-seed-11/evaluation-report",
        name="evaluation-report.json",
        logical_uri="artifact://fixture/verified/evaluation-report.json",
        kind="evaluation",
        media_type="application/json",
        verification_state=FixtureVerificationState.VERIFIED_CHECKSUM,
        checksum=_sha256(verified_content.encode("utf-8")),
        content=verified_content,
    )
    uri_only = FixtureArtifact(
        artifact_id="artifact://fixture/missing-metadata/artifact-root",
        name="MLflow artifact root",
        logical_uri="artifact://fixture/uri-only",
        kind="artifact_root",
        verification_state=FixtureVerificationState.LOCATED_UNVERIFIED,
    )

    runs: list[FixtureRun] = []
    baseline_scores = {11: (0.74, 0.77, 0.79), 22: (0.75, 0.78, 0.80), 33: (0.73, 0.76, 0.78)}
    treatment_scores = {
        11: (0.80, 0.84, 0.86),
        22: (0.79, 0.83, 0.85),
        33: (0.81, 0.85, 0.87),
    }
    ordinal = 1
    for seed in EXPERIMENT_FIXTURE_SEEDS:
        slug = f"baseline-seed-{seed}"
        runs.append(
            _run(
                ordinal,
                slug,
                "baseline",
                seed=seed,
                metrics=(
                    _metric(
                        slug,
                        ordinal,
                        "ndcg_at_10",
                        "ratio",
                        FixtureMetricDirection.HIGHER,
                        baseline_scores[seed],
                    ),
                    _metric(
                        slug,
                        ordinal,
                        "latency_ms",
                        "ms",
                        FixtureMetricDirection.LOWER,
                        (132.0 - seed / 10, 126.0 - seed / 10, 121.0 - seed / 10),
                    ),
                ),
            )
        )
        ordinal += 1
    for seed in EXPERIMENT_FIXTURE_SEEDS:
        slug = f"treatment-seed-{seed}"
        runs.append(
            _run(
                ordinal,
                slug,
                "treatment",
                seed=seed,
                config={"reranker": "hybrid", "workers": 1},
                metrics=(
                    _metric(
                        slug,
                        ordinal,
                        "ndcg_at_10",
                        "ratio",
                        FixtureMetricDirection.HIGHER,
                        treatment_scores[seed],
                    ),
                    _metric(
                        slug,
                        ordinal,
                        "latency_ms",
                        "ms",
                        FixtureMetricDirection.LOWER,
                        (141.0 + seed / 10, 136.0 + seed / 10, 133.0 + seed / 10),
                    ),
                ),
                artifacts=(verified,) if seed == 11 else (),
            )
        )
        ordinal += 1

    runs.extend(
        (
            _run(
                7,
                "dataset-v2-mismatch",
                "dataset_mismatch",
                seed=11,
                dataset_version="dataset-v2",
                config={"reranker": "hybrid", "workers": 1},
                metrics=(
                    _metric(
                        "dataset-v2-mismatch",
                        7,
                        "ndcg_at_10",
                        "ratio",
                        FixtureMetricDirection.HIGHER,
                        (0.82, 0.87, 0.90),
                    ),
                ),
            ),
            _run(
                8,
                "percent-unit-mismatch",
                "unit_mismatch",
                seed=11,
                config={"reranker": "hybrid", "workers": 1},
                metrics=(
                    _metric(
                        "percent-unit-mismatch",
                        8,
                        "ndcg_at_10",
                        "percent",
                        FixtureMetricDirection.HIGHER,
                        (80.0, 84.0, 86.0),
                    ),
                ),
            ),
            _run(
                9,
                "failed-high-score",
                "failed_high_score",
                status=FixtureRunStatus.FAILED,
                seed=11,
                config={"reranker": "hybrid", "workers": 1},
                metrics=(
                    _metric(
                        "failed-high-score",
                        9,
                        "ndcg_at_10",
                        "ratio",
                        FixtureMetricDirection.HIGHER,
                        (0.99,),
                    ),
                ),
            ),
            _run(
                10,
                "running-candidate",
                "running",
                status=FixtureRunStatus.RUNNING,
                seed=22,
                config={"reranker": "hybrid", "workers": 1},
                metrics=(
                    _metric(
                        "running-candidate",
                        10,
                        "ndcg_at_10",
                        "ratio",
                        FixtureMetricDirection.HIGHER,
                        (0.81, 0.83),
                    ),
                ),
            ),
            _run(
                11,
                "missing-commit-environment",
                "missing_metadata",
                seed=33,
                commit_sha=None,
                config={"reranker": "hybrid", "workers": 1},
                environment={},
                metrics=(
                    _metric(
                        "missing-commit-environment",
                        11,
                        "mystery_score",
                        "score",
                        FixtureMetricDirection.UNKNOWN,
                        (0.42,),
                    ),
                ),
                artifacts=(uri_only,),
            ),
            _run(
                12,
                "typed-config-string-one",
                "typed_config_hard_negative",
                seed=11,
                config={"reranker": "hybrid", "workers": "1"},
                metrics=(
                    _metric(
                        "typed-config-string-one",
                        12,
                        "ndcg_at_10",
                        "ratio",
                        FixtureMetricDirection.HIGHER,
                        (0.86,),
                    ),
                ),
            ),
        )
    )
    return ExperimentFixtureRecipe(
        production_authority=_fixed_production_authority(),
        metric_definitions=deterministic_metric_definitions(),
        runs=tuple(runs),
    )


class FixtureMaterialization(_FrozenContract):
    schema_version: Literal["experiment-fixture-materialization-v1"] = (
        "experiment-fixture-materialization-v1"
    )
    recipe_digest: Sha256
    logical_counts: dict[Identifier, StrictInt]
    tracking_root: StrictStr
    file_count: Annotated[StrictInt, Field(ge=1)]


def _require_system_temp_child(root: Path, *, must_not_exist: bool) -> Path:
    system_temp = Path(tempfile.gettempdir()).resolve()
    resolved = root.resolve(strict=False)
    if resolved == system_temp or system_temp not in resolved.parents:
        raise ExperimentFixtureError("fixture root must be a child of the system temp directory")
    if must_not_exist and root.exists():
        raise ExperimentFixtureError("fixture root must not already exist")
    return resolved


def _encode_param(value: Scalar) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in {int, float, str}:
        return str(value)
    raise ExperimentFixtureError(f"unsupported fixture scalar: {type(value).__name__}")


def materialize_mlflow_filestore(
    root: str | Path,
    recipe: ExperimentFixtureRecipe | None = None,
) -> FixtureMaterialization:
    """Create a deterministic MLflow FileStore below a new system-temp path."""

    frozen = recipe or deterministic_experiment_recipe()
    target = _require_system_temp_child(Path(root), must_not_exist=True)
    experiment_root = target / frozen.external_experiment_id
    experiment_root.mkdir(parents=True)
    (experiment_root / "meta.yaml").write_text(
        json.dumps(
            {
                "experiment_id": frozen.external_experiment_id,
                "name": frozen.title,
                "lifecycle_stage": "active",
                "artifact_location": "artifact://fixture/root",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    for run in frozen.runs:
        run_root = experiment_root / run.external_id
        for child in ("metrics", "params", "tags", "artifacts"):
            (run_root / child).mkdir(parents=True)
        status = {
            FixtureRunStatus.COMPLETED: "FINISHED",
            FixtureRunStatus.FAILED: "FAILED",
            FixtureRunStatus.RUNNING: "RUNNING",
        }[run.status]
        meta: dict[str, object] = {
            "run_id": run.external_id,
            "run_name": run.name,
            "status": status,
            "start_time": run.started_at_ms,
        }
        if run.completed_at_ms is not None:
            meta["end_time"] = run.completed_at_ms
        uri_only = next(
            (
                artifact
                for artifact in run.artifacts
                if artifact.verification_state == FixtureVerificationState.LOCATED_UNVERIFIED
            ),
            None,
        )
        if uri_only is not None:
            meta["artifact_uri"] = uri_only.logical_uri
        (run_root / "meta.yaml").write_text(
            json.dumps(meta, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for key, value in sorted(run.config.items()):
            (run_root / "params" / key).write_text(_encode_param(value), encoding="utf-8")
        tags: dict[str, str] = {
            "mlflow.runName": run.name,
            "dataset_id": run.dataset_id,
            "dataset_version": run.dataset_version,
            "git.branch": run.branch,
            "mlflow.project.entryPoint": run.command,
            "fixture.seed": str(run.seed),
            "fixture.group": run.group,
        }
        if run.commit_sha is not None:
            tags["mlflow.source.git.commit"] = run.commit_sha
        tags.update({key: _encode_param(value) for key, value in run.environment.items()})
        for key, value in sorted(tags.items()):
            (run_root / "tags" / key).write_text(value, encoding="utf-8")
        for metric in run.metrics:
            rows = "".join(
                f"{point.observed_at_ms} {point.value:.17g} {point.step}\n"
                for point in metric.points
            )
            (run_root / "metrics" / metric.name).write_text(rows, encoding="utf-8")
        for artifact in run.artifacts:
            if artifact.content is not None:
                path = run_root / "artifacts" / artifact.name
                path.write_text(artifact.content, encoding="utf-8")
                if _sha256(path.read_bytes()) != artifact.checksum:
                    raise ExperimentFixtureError("materialized artifact checksum mismatch")

    file_count = sum(1 for path in target.rglob("*") if path.is_file())
    return FixtureMaterialization(
        recipe_digest=frozen.canonical_sha256(),
        logical_counts=frozen.logical_counts,
        tracking_root=str(target),
        file_count=file_count,
    )


class NormalizedMetric(_FrozenContract):
    name: Identifier
    value: StrictFloat
    unit: Literal["ratio", "percent", "ms", "score"]
    direction: FixtureMetricDirection
    split: Literal["test"]
    step: StrictInt
    history_points: Annotated[StrictInt, Field(ge=1)]


class NormalizedArtifact(_FrozenContract):
    name: ContractText
    logical_uri: ContractText
    verification_state: FixtureVerificationState
    checksum: Sha256 | None


class NormalizedRun(_FrozenContract):
    logical_run_id: ContractText
    external_id: Identifier
    name: Identifier
    group: Identifier
    status: FixtureRunStatus
    seed: StrictInt
    dataset_id: ContractText
    dataset_version: ContractText
    commit_sha: ContractText | None
    branch: Literal["main"]
    config: dict[Identifier, Scalar]
    environment: dict[Identifier, Scalar]
    metrics: tuple[NormalizedMetric, ...]
    artifacts: tuple[NormalizedArtifact, ...]


class ProductionLaneSnapshot(_FrozenContract):
    lane: Literal["mlflow_filestore", "manual_service"]
    production_authority: CurrentProductionComponentAuthority
    recipe_digest: Sha256
    production_evidence_digest: Sha256
    logical_experiment_id: ContractText
    normalized_runs: tuple[NormalizedRun, ...]
    capability_gaps: tuple[Identifier, ...]

    @property
    def parity_digest(self) -> str:
        payload = {
            "production_authority": self.production_authority.model_dump(mode="json"),
            "logical_experiment_id": self.logical_experiment_id,
            "normalized_runs": [run.model_dump(mode="json") for run in self.normalized_runs],
        }
        return _sha256(_canonical_json(payload))


class ProductionLaneParity(_FrozenContract):
    schema_version: Literal["experiment-current-production-lane-parity-v3"] = (
        "experiment-current-production-lane-parity-v3"
    )
    recipe_digest: Sha256
    logical_counts: dict[Identifier, StrictInt]
    production_authority: CurrentProductionComponentAuthority
    parity_hash: Sha256
    mlflow: ProductionLaneSnapshot
    manual: ProductionLaneSnapshot
    parity: Literal[True]
    differences: tuple[ContractText, ...] = ()

    @model_validator(mode="after")
    def _digests_match(self) -> Self:
        if (
            self.mlflow.recipe_digest != self.recipe_digest
            or self.manual.recipe_digest != self.recipe_digest
            or self.mlflow.production_authority != self.production_authority
            or self.manual.production_authority != self.production_authority
            or self.mlflow.parity_digest != self.manual.parity_digest
            or self.parity_hash != self.mlflow.parity_digest
            or self.differences
        ):
            raise ValueError("production lane parity mismatch")
        if self.parity_hash != EXPERIMENT_CURRENT_PRODUCTION_PARITY_HASH:
            raise ValueError("production lane parity differs from current reviewed hash")
        return self


def _runtime_settings(root: Path) -> Settings:
    repository_root = Path(__file__).resolve().parents[5]
    data = root / "data"
    return Settings(
        data_dir=data,
        database_path=data / "fixture.sqlite3",
        repository_cache=data / "repositories",
        web_dir=repository_root / "web",
        allowed_local_roots=(root.parent,),
        project_root=root,
        embedding_dimensions=32,
    )


def _ensure_fixture_project(runtime: Any, recipe: ExperimentFixtureRecipe) -> None:
    runtime.workspace.create_project(
        ProjectCreate(
            id=recipe.project_id,
            name="Experiment E0 isolated fixture",
            acl_ref=recipe.acl_ref,
            classification="internal",
        ),
        audit=False,
    )


def _coerce_expected_scalar(raw: object, expected: Scalar, *, lane: str) -> Scalar:
    if lane == "manual_service":
        if type(raw) is not type(expected) or raw != expected:
            raise ExperimentFixtureError("manual config/environment scalar drift")
        return expected
    if type(raw) is not str or raw != _encode_param(expected):
        raise ExperimentFixtureError("MLflow config/environment scalar drift")
    return expected


def _production_evidence_digest(runs: list[dict[str, Any]]) -> str:
    safe_rows = []
    for run in sorted(runs, key=lambda item: str(item.get("external_id"))):
        safe_rows.append(
            {
                "external_id": run.get("external_id"),
                "name": run.get("name"),
                "status": run.get("status"),
                "dataset_id": run.get("dataset_id"),
                "dataset_version": run.get("dataset_version"),
                "commit_sha": run.get("commit_sha"),
                "config": run.get("config"),
                "environment": run.get("environment"),
                "metrics": [
                    {
                        "name": metric.get("name"),
                        "value": metric.get("value"),
                        "step": metric.get("step"),
                        "unit": metric.get("unit"),
                        "split": metric.get("split"),
                        "higher_is_better": metric.get("higher_is_better"),
                    }
                    for metric in run.get("metrics", [])
                ],
                "artifacts": [
                    {
                        "name": artifact.get("name"),
                        "kind": artifact.get("kind"),
                        "checksum": artifact.get("checksum"),
                        "media_type": artifact.get("media_type"),
                        "size": artifact.get("metadata", {}).get("size"),
                        "has_uri": bool(artifact.get("uri")),
                    }
                    for artifact in run.get("artifacts", [])
                ],
            }
        )
    return _sha256(_canonical_json(safe_rows))


def _normalize_runs(
    recipe: ExperimentFixtureRecipe,
    runs: list[dict[str, Any]],
    *,
    lane: Literal["mlflow_filestore", "manual_service"],
    tracking_root: Path | None,
) -> tuple[NormalizedRun, ...]:
    by_external = {str(run.get("external_id")): run for run in runs}
    if set(by_external) != {run.external_id for run in recipe.runs}:
        raise ExperimentFixtureError("production lane run membership mismatch")
    normalized: list[NormalizedRun] = []
    for expected in recipe.runs:
        actual = by_external[expected.external_id]
        for field in (
            "name",
            "status",
            "dataset_id",
            "dataset_version",
            "commit_sha",
            "branch",
        ):
            actual_value = actual.get(field)
            expected_value = getattr(expected, field)
            if actual_value != expected_value:
                raise ExperimentFixtureError(f"{lane} run {expected.external_id} {field} drift")
        config = {
            key: _coerce_expected_scalar(actual["config"].get(key), value, lane=lane)
            for key, value in expected.config.items()
        }
        if set(actual["config"]) != set(expected.config):
            raise ExperimentFixtureError(f"{lane} run config membership drift")
        environment = {
            key: _coerce_expected_scalar(actual["environment"].get(key), value, lane=lane)
            for key, value in expected.environment.items()
        }
        if set(actual["environment"]) != set(expected.environment):
            raise ExperimentFixtureError(f"{lane} run environment membership drift")
        if actual.get("tags", {}).get("fixture.seed") != str(expected.seed):
            raise ExperimentFixtureError(f"{lane} run seed tag drift")
        if actual.get("tags", {}).get("fixture.group") != expected.group:
            raise ExperimentFixtureError(f"{lane} run group tag drift")
        expected_metrics = {metric.name: metric for metric in expected.metrics}
        actual_metrics = {metric["name"]: metric for metric in actual.get("metrics", [])}
        if set(actual_metrics) != set(expected_metrics):
            raise ExperimentFixtureError(f"{lane} metric membership drift")
        metrics: list[NormalizedMetric] = []
        for name, metric in expected_metrics.items():
            observed = actual_metrics[name]
            if (
                not math.isclose(float(observed["value"]), metric.latest.value, abs_tol=1e-12)
                or observed.get("step") != metric.latest.step
            ):
                raise ExperimentFixtureError(f"{lane} latest metric projection drift")
            if lane == "manual_service":
                if (
                    observed.get("unit") != metric.unit
                    or observed.get("split") != metric.split
                    or bool(observed.get("higher_is_better"))
                    != (metric.direction != FixtureMetricDirection.LOWER)
                ):
                    raise ExperimentFixtureError("manual metric metadata drift")
            else:
                if observed.get("unit") is not None or observed.get("split") is not None:
                    raise ExperimentFixtureError("MLflow V1 metadata capability changed")
            metrics.append(
                NormalizedMetric(
                    name=name,
                    value=metric.latest.value,
                    unit=metric.unit,
                    direction=metric.direction,
                    split=metric.split,
                    step=metric.latest.step,
                    history_points=len(metric.points),
                )
            )
        expected_artifacts = {artifact.name: artifact for artifact in expected.artifacts}
        actual_artifacts = {artifact["name"]: artifact for artifact in actual.get("artifacts", [])}
        if set(actual_artifacts) != set(expected_artifacts):
            raise ExperimentFixtureError(f"{lane} artifact membership drift")
        artifacts: list[NormalizedArtifact] = []
        for name, artifact in expected_artifacts.items():
            observed = actual_artifacts[name]
            if lane == "manual_service":
                if (
                    observed.get("uri") != artifact.logical_uri
                    or observed.get("checksum") != artifact.checksum
                ):
                    raise ExperimentFixtureError("manual artifact projection drift")
            elif artifact.content is not None:
                if tracking_root is None:
                    raise ExperimentFixtureError("MLflow artifact root is unavailable")
                path = (
                    tracking_root
                    / recipe.external_experiment_id
                    / expected.external_id
                    / "artifacts"
                    / artifact.name
                )
                if (
                    not bool(observed.get("uri"))
                    or _sha256(path.read_bytes()) != artifact.checksum
                    or observed.get("checksum") is not None
                ):
                    raise ExperimentFixtureError("MLflow artifact evidence drift")
            elif (
                observed.get("uri") != artifact.logical_uri or observed.get("checksum") is not None
            ):
                raise ExperimentFixtureError("MLflow URI-only artifact drift")
            artifacts.append(
                NormalizedArtifact(
                    name=name,
                    logical_uri=artifact.logical_uri,
                    verification_state=artifact.verification_state,
                    checksum=artifact.checksum,
                )
            )
        normalized.append(
            NormalizedRun(
                logical_run_id=expected.logical_run_id,
                external_id=expected.external_id,
                name=expected.name,
                group=expected.group,
                status=expected.status,
                seed=expected.seed,
                dataset_id=expected.dataset_id,
                dataset_version=expected.dataset_version,
                commit_sha=expected.commit_sha,
                branch=expected.branch,
                config=config,
                environment=environment,
                metrics=tuple(metrics),
                artifacts=tuple(artifacts),
            )
        )
    return tuple(normalized)


def _mlflow_lane(
    root: Path,
    recipe: ExperimentFixtureRecipe,
    authority: CurrentProductionComponentAuthority,
) -> ProductionLaneSnapshot:
    verify_production_component_authority(authority)
    tracking = root / "mlflow-tracking"
    materialize_mlflow_filestore(tracking, recipe)
    runtime = create_runtime(_runtime_settings(root / "mlflow-runtime"))
    _verify_runtime_service_binding(runtime.experiments, authority)
    _ensure_fixture_project(runtime, recipe)
    adapter = MLflowAdapter(str(tracking), allowed_local_roots=(root,))
    if type(adapter) is not _resolve_export(
        "evidence_rag.experiments.adapters.mlflow",
        "MLflowAdapter",
    ):
        raise ExperimentFixtureError("runtime adapter is not the exact reviewed class")
    adapter_experiments, adapter_runs = adapter.load([recipe.external_experiment_id], 100)
    if len(adapter_experiments) != 1 or len(adapter_runs) != EXPERIMENT_FIXTURE_RUN_COUNT:
        raise ExperimentFixtureError("direct production MLflowAdapter membership mismatch")
    result = runtime.experiments.sync_mlflow(
        MLflowSyncRequest(
            project_id=recipe.project_id,
            tracking_uri=str(tracking),
            experiment_ids=[recipe.external_experiment_id],
            max_runs=100,
            acl_ref=recipe.acl_ref,
        )
    )
    if result["stats"]["runs"] != EXPERIMENT_FIXTURE_RUN_COUNT:
        raise ExperimentFixtureError("production sync run count mismatch")
    rows = runtime.experiments.store.list_runs(recipe.project_id)
    details = [runtime.experiments.store.get_run(row["id"]) for row in rows]
    production_runs = [run for run in details if run is not None]
    return ProductionLaneSnapshot(
        lane="mlflow_filestore",
        production_authority=authority,
        recipe_digest=recipe.canonical_sha256(),
        production_evidence_digest=_production_evidence_digest(production_runs),
        logical_experiment_id=recipe.logical_experiment_id,
        normalized_runs=_normalize_runs(
            recipe,
            production_runs,
            lane="mlflow_filestore",
            tracking_root=tracking,
        ),
        capability_gaps=(
            "filestore_metric_unit_split_direction_unavailable",
            "filestore_params_are_strings",
            "filestore_artifact_checksum_unavailable",
        ),
    )


def _manual_lane(
    root: Path,
    recipe: ExperimentFixtureRecipe,
    authority: CurrentProductionComponentAuthority,
) -> ProductionLaneSnapshot:
    verify_production_component_authority(authority)
    runtime = create_runtime(_runtime_settings(root / "manual-runtime"))
    _verify_runtime_service_binding(runtime.experiments, authority)
    _ensure_fixture_project(runtime, recipe)
    service = runtime.experiments
    experiment = service.create_experiment(
        ExperimentCreate(
            project_id=recipe.project_id,
            title=recipe.title,
            objective=recipe.objective,
            owner="Experiment E0 fixture",
            status="running",
            tags=["experiment-e0", "isolated-fixture"],
        )
    )
    production_runs: list[dict[str, Any]] = []
    for run in recipe.runs:
        metrics = [
            MetricInput(
                name=metric.name,
                value=metric.latest.value,
                unit=metric.unit,
                split=metric.split,
                step=metric.latest.step,
                higher_is_better=metric.direction != FixtureMetricDirection.LOWER,
            )
            for metric in run.metrics
        ]
        artifacts = [
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
        ]
        production_runs.append(
            service.create_run(
                RunCreate(
                    experiment_id=experiment["id"],
                    external_id=run.external_id,
                    name=run.name,
                    status=run.status,
                    commit_sha=run.commit_sha,
                    branch="main",
                    dataset_id=run.dataset_id,
                    dataset_version=run.dataset_version,
                    config=run.config,
                    environment=run.environment,
                    command=run.command,
                    started_at=__import__("datetime")
                    .datetime.fromtimestamp(
                        run.started_at_ms / 1000,
                        tz=__import__("datetime").UTC,
                    )
                    .isoformat(),
                    completed_at=(
                        __import__("datetime")
                        .datetime.fromtimestamp(
                            run.completed_at_ms / 1000,
                            tz=__import__("datetime").UTC,
                        )
                        .isoformat()
                        if run.completed_at_ms is not None
                        else None
                    ),
                    metrics=metrics,
                    artifacts=artifacts,
                    tags={"fixture.group": run.group, "fixture.seed": str(run.seed)},
                )
            )
        )
    return ProductionLaneSnapshot(
        lane="manual_service",
        production_authority=authority,
        recipe_digest=recipe.canonical_sha256(),
        production_evidence_digest=_production_evidence_digest(production_runs),
        logical_experiment_id=recipe.logical_experiment_id,
        normalized_runs=_normalize_runs(
            recipe,
            production_runs,
            lane="manual_service",
            tracking_root=None,
        ),
        capability_gaps=(
            "manual_service_time_series_summary_only",
            "manual_service_unknown_direction_unrepresentable_v1",
        ),
    )


def build_production_lane_parity(
    temp_root: str | Path,
    recipe: ExperimentFixtureRecipe | None = None,
) -> ProductionLaneParity:
    """Run both real production ingestion lanes in independent temp runtimes."""

    frozen = recipe or deterministic_experiment_recipe()
    authority = verify_production_component_authority()
    root = _require_system_temp_child(Path(temp_root), must_not_exist=False)
    if not root.is_dir():
        raise ExperimentFixtureError("explicit system temp root must already exist")
    reserved = (root / "mlflow-lane", root / "manual-lane")
    if any(path.exists() for path in reserved):
        raise ExperimentFixtureError("production parity lane roots must be new")
    mlflow = _mlflow_lane(reserved[0], frozen, authority)
    manual = _manual_lane(reserved[1], frozen, authority)
    differences: list[str] = []
    if mlflow.parity_digest != manual.parity_digest:
        differences.append("normalized production lane digests differ")
    if differences:
        raise ExperimentFixtureError("; ".join(differences))
    return ProductionLaneParity(
        recipe_digest=frozen.canonical_sha256(),
        logical_counts=frozen.logical_counts,
        production_authority=authority,
        parity_hash=mlflow.parity_digest,
        mlflow=mlflow,
        manual=manual,
        parity=True,
        differences=(),
    )


__all__ = [
    "EXPERIMENT_CURRENT_PRODUCTION_AUTHORITY_VERSION",
    "EXPERIMENT_CURRENT_PRODUCTION_COMPONENT_SET_DIGEST",
    "EXPERIMENT_CURRENT_PRODUCTION_PARITY_HASH",
    "EXPERIMENT_FIXTURE_ACL_REF",
    "EXPERIMENT_FIXTURE_EXPERIMENT_ID",
    "EXPERIMENT_FIXTURE_EXTERNAL_EXPERIMENT_ID",
    "EXPERIMENT_FIXTURE_PROJECT_ID",
    "EXPERIMENT_FIXTURE_RECIPE_VERSION",
    "EXPERIMENT_FIXTURE_RUN_COUNT",
    "EXPERIMENT_FIXTURE_SEEDS",
    "EXPERIMENT_PRODUCTION_AUTHORITY_VERSION",
    "EXPERIMENT_PRODUCTION_COMPONENT_SET_DIGEST",
    "EXPERIMENT_PRODUCTION_PARITY_HASH",
    "CurrentProductionComponentAuthority",
    "CurrentProductionComponentIdentity",
    "ExperimentFixtureError",
    "ExperimentFixtureRecipe",
    "FixtureArtifact",
    "FixtureMaterialization",
    "FixtureMetric",
    "FixtureMetricDefinition",
    "FixtureMetricDirection",
    "FixtureMetricPoint",
    "FixtureRun",
    "FixtureRunGroup",
    "FixtureRunStatus",
    "FixtureVerificationState",
    "NormalizedArtifact",
    "NormalizedMetric",
    "NormalizedRun",
    "ProductionLaneParity",
    "ProductionLaneSnapshot",
    "ProductionComponentAuthority",
    "ProductionComponentIdentity",
    "build_production_lane_parity",
    "deterministic_experiment_recipe",
    "deterministic_metric_definitions",
    "materialize_mlflow_filestore",
    "verify_production_component_authority",
]
