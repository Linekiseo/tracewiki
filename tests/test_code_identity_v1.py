from __future__ import annotations

import builtins
import hashlib
import json
import pathlib
import sys
import types

import pytest

from evidence_rag.code_identity_v1 import (
    CodeIdentityError,
    portable_code_payload,
    portable_function_payload,
    portable_stdlib_object_identity,
)


def _portable_sample(value: int = 3) -> tuple[int, int]:
    return value + 1, sum(item for item in range(value))


def _payload_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_portable_function_identity_is_frozen_across_supported_python_minors() -> None:
    payload = portable_function_payload(_portable_sample)

    assert payload["schema"] == "python-source-ast-code-identity-v1"
    assert _payload_sha256(payload) == (
        "sha256:fc41add6bd5e37d03094814e58c39652df53a03e2f1a29ba36945ee52a5dc97a"
    )


def test_portable_code_payload_omits_interpreter_private_fields() -> None:
    payload = portable_code_payload(_portable_sample.__code__)

    assert payload["schema"] == "python-portable-code-identity-v1"
    assert {
        "bytecode",
        "code",
        "filename",
        "firstlineno",
        "flags",
        "linetable",
        "stacksize",
    }.isdisjoint(payload)
    assert str(sys.version_info.minor) not in payload["schema"]


def test_relocated_checkout_path_keeps_portable_function_identity() -> None:
    original = portable_function_payload(_portable_sample)
    relocated_code = _portable_sample.__code__.replace(
        co_filename="/different/checkout/tests/test_code_identity_v1.py"
    )
    relocated = types.FunctionType(
        relocated_code,
        _portable_sample.__globals__,
        _portable_sample.__name__,
        _portable_sample.__defaults__,
        _portable_sample.__closure__,
    )
    relocated.__kwdefaults__ = _portable_sample.__kwdefaults__

    assert portable_function_payload(relocated) == original


def test_canonical_in_memory_code_mutation_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _portable_sample.__code__
    tampered = original.replace(co_consts=(*original.co_consts, "authority-tamper"))

    with monkeypatch.context() as patch:
        patch.setattr(_portable_sample, "__code__", tampered)
        with pytest.raises(CodeIdentityError, match="differs from verified module source"):
            portable_function_payload(_portable_sample)


def test_stdlib_identity_uses_public_logical_name_not_python_minor_path() -> None:
    path_identity = portable_stdlib_object_identity(pathlib.Path)
    max_identity = portable_stdlib_object_identity(builtins.max)
    min_identity = portable_stdlib_object_identity(builtins.min)

    assert path_identity == {
        "module": "pathlib",
        "qualname": "Path",
        "runtime": {
            "runtime": "python-stdlib",
            "implementation": sys.implementation.name,
            "language_major": 3,
        },
    }
    assert max_identity is not None and max_identity["qualname"] == "max"
    assert min_identity is not None and min_identity["qualname"] == "min"
    assert max_identity != min_identity
