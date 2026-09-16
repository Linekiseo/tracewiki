"""Portable Python code identity with same-interpreter source coherence checks.

Persisted authority manifests must remain stable across every supported Python
minor release.  CPython bytecode, stack sizes, and some compiler flags are not
such a format.  This module therefore separates two concerns:

* ``portable_*_payload`` returns a structural identity that deliberately omits
  interpreter-private bytecode details.
* ``assert_canonical_function_matches_source`` recompiles the verified module
  source with the active interpreter and compares the exact runtime bytecode.

The second check is only applied to the canonical function currently bound in
its declaring module.  Detached copies (for example a function whose checkout
path was relocated for a portability test) can still be structurally hashed,
but cannot be treated as runtime authority.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import importlib
import inspect
import math
import sys
import textwrap
import types
from collections.abc import Mapping
from functools import lru_cache


class CodeIdentityError(RuntimeError):
    """Raised when canonical runtime code differs from its module source."""


def _portable_sort_key(value: object) -> str:
    return repr(value)


def portable_constant_payload(value: object) -> object:
    """Return a Python-minor-independent payload for a code constant."""

    if isinstance(value, types.CodeType):
        return {"code": portable_code_payload(value)}
    if value is None or isinstance(value, (bool, int, str)):
        return {"literal": value, "type": type(value).__name__}
    if isinstance(value, float):
        return {
            "literal": value if math.isfinite(value) else repr(value),
            "type": type(value).__name__,
        }
    if isinstance(value, complex):
        return {"complex": [value.real, value.imag]}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, tuple):
        return {"tuple": [portable_constant_payload(item) for item in value]}
    if isinstance(value, frozenset):
        items = [portable_constant_payload(item) for item in value]
        return {"frozenset": sorted(items, key=_portable_sort_key)}
    if value is Ellipsis:
        return {"literal": "Ellipsis", "type": "ellipsis"}
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr": repr(value),
    }


def portable_code_payload(code: types.CodeType) -> dict[str, object]:
    """Return structural code identity without CPython-private bytecode."""

    return {
        "schema": "python-portable-code-identity-v1",
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "consts": [portable_constant_payload(item) for item in code.co_consts],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
    }


def portable_function_payload(value: types.FunctionType) -> dict[str, object]:
    """Return portable function code plus exact defaults."""

    assert_canonical_function_matches_source(value)
    return {
        "schema": "python-source-ast-code-identity-v1",
        "definition": _portable_function_definition(value),
        "defaults": portable_constant_payload(value.__defaults__),
        "kwdefaults": portable_constant_payload(
            tuple(sorted((value.__kwdefaults__ or {}).items()))
        ),
    }


def portable_stdlib_identity(module: types.ModuleType) -> dict[str, object] | None:
    """Return a cross-minor logical identity for builtins and stdlib modules."""

    root_name = module.__name__.split(".", 1)[0]
    if module.__name__ != "builtins" and root_name not in sys.stdlib_module_names:
        return None
    return {
        "runtime": "python-stdlib",
        "implementation": sys.implementation.name,
        "language_major": sys.version_info.major,
    }


def portable_stdlib_object_identity(value: object) -> dict[str, object] | None:
    """Return the public logical identity of a builtin or stdlib object."""

    module_name = getattr(value, "__module__", None)
    if not isinstance(module_name, str):
        return None
    module = sys.modules.get(module_name)
    if module is None:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return None
    runtime = portable_stdlib_identity(module)
    if runtime is None:
        return None
    root_name = module_name.split(".", 1)[0]
    try:
        root_module = importlib.import_module(root_name)
    except ImportError:
        root_module = module
    public_names = sorted(
        name
        for name, candidate in vars(root_module).items()
        if not name.startswith("_")
        and candidate is value
        and name == getattr(value, "__qualname__", "").rsplit(".", 1)[-1]
    )
    return {
        "module": root_name if public_names else module_name,
        "qualname": public_names[0]
        if public_names
        else getattr(value, "__qualname__", None),
        "runtime": runtime,
    }


def _unwrap_static_member(raw: object) -> tuple[types.FunctionType, ...]:
    if isinstance(raw, (staticmethod, classmethod)):
        return (raw.__func__,)
    if isinstance(raw, property):
        return tuple(item for item in (raw.fget, raw.fset, raw.fdel) if item is not None)
    if isinstance(raw, types.FunctionType):
        return (raw,)
    return ()


def _canonical_function(value: types.FunctionType) -> bool:
    module = sys.modules.get(value.__module__)
    if module is None or value.__globals__ is not vars(module):
        return False
    return any(candidate is value for candidate in _declared_function_candidates(value))


def _declared_function_candidates(
    value: types.FunctionType,
) -> tuple[types.FunctionType, ...]:
    module = sys.modules.get(value.__module__)
    if module is None:
        return ()
    parts = value.__qualname__.split(".")
    if "<locals>" in parts:
        return ()
    current: object = module
    try:
        for part in parts[:-1]:
            current = inspect.getattr_static(current, part)
        raw = inspect.getattr_static(current, parts[-1])
    except AttributeError:
        return ()
    return _unwrap_static_member(raw)


def _portable_function_definition(value: types.FunctionType) -> object:
    source_value = value
    try:
        source = inspect.getsource(source_value)
    except (OSError, TypeError):
        source = ""
        actual = _strict_code_payload(value.__code__)
        for candidate in _declared_function_candidates(value):
            if _strict_code_payload(candidate.__code__) != actual:
                continue
            try:
                source = inspect.getsource(candidate)
            except (OSError, TypeError):
                continue
            source_value = candidate
            break
    if source:
        try:
            tree = ast.parse(textwrap.dedent(source))
        except SyntaxError as exc:
            raise CodeIdentityError("canonical function source cannot be parsed") from exc
        return {
            "module": source_value.__module__,
            "qualname": source_value.__qualname__,
            "ast": _portable_ast_payload(tree),
        }
    if value.__code__.co_filename.startswith("<") or any(
        candidate is value for candidate in _declared_function_candidates(value)
    ):
        signature = inspect.signature(value)
        return {
            "generated": True,
            "module": value.__module__,
            "qualname": value.__qualname__,
            "parameters": [
                {
                    "name": parameter.name,
                    "kind": parameter.kind.name,
                    "has_default": parameter.default is not inspect.Parameter.empty,
                }
                for parameter in signature.parameters.values()
            ],
            "return_annotation": signature.return_annotation is not inspect.Signature.empty,
        }
    raise CodeIdentityError(
        "function source is unavailable for portable identity: "
        f"{value.__module__}.{value.__qualname__} ({value.__code__.co_filename})"
    )


def _portable_ast_payload(value: object) -> object:
    """Normalize AST fields whose empty-value rendering changed in Python 3.13."""

    if isinstance(value, ast.AST):
        fields: dict[str, object] = {"node": type(value).__name__}
        for name, item in ast.iter_fields(value):
            if item is None or item == [] or item == ():
                continue
            fields[name] = _portable_ast_payload(item)
        return fields
    if isinstance(value, list):
        return [_portable_ast_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_portable_ast_payload(item) for item in value]
    if value is Ellipsis or isinstance(value, (bytes, complex)):
        return portable_constant_payload(value)
    return value


def _strict_constant_payload(value: object) -> object:
    if isinstance(value, types.CodeType):
        return {"code": _strict_code_payload(value)}
    return portable_constant_payload(value)


def _strict_code_payload(code: types.CodeType) -> dict[str, object]:
    """Return exact compiler output, excluding checkout and line-table data."""

    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize,
        "flags": code.co_flags,
        "code": code.co_code.hex(),
        "consts": [_strict_constant_payload(item) for item in code.co_consts],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
    }


def _walk_code(code: types.CodeType) -> tuple[types.CodeType, ...]:
    rows = [code]
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            rows.extend(_walk_code(constant))
    return tuple(rows)


@lru_cache(maxsize=128)
def _compiled_module_index(
    module_name: str,
    source_sha256: str,
    source: str,
) -> Mapping[tuple[str, str, int], tuple[types.CodeType, ...]]:
    del module_name, source_sha256
    try:
        root = compile(source, "<authority-source>", "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        raise CodeIdentityError("canonical module source cannot be recompiled") from exc
    index: dict[tuple[str, str, int], list[types.CodeType]] = {}
    for code in _walk_code(root):
        key = (code.co_qualname, code.co_name, code.co_firstlineno)
        index.setdefault(key, []).append(code)
    return {key: tuple(rows) for key, rows in index.items()}


def assert_canonical_function_matches_source(value: types.FunctionType) -> None:
    """Reject in-memory mutation of a canonical module function.

    The comparison uses bytecode produced by the same interpreter from the
    module's current source, so it is strict at runtime without persisting a
    Python-minor-specific digest.
    """

    if not _canonical_function(value):
        return
    module = sys.modules.get(value.__module__)
    if module is None:
        raise CodeIdentityError("canonical function module is unavailable")
    try:
        source = inspect.getsource(module).replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, TypeError) as exc:
        raise CodeIdentityError("canonical module source is unavailable") from exc
    source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    index = _compiled_module_index(module.__name__, source_sha256, source)
    key = (value.__qualname__, value.__code__.co_name, value.__code__.co_firstlineno)
    candidates = index.get(key, ())
    if not candidates and value.__code__.co_filename.startswith("<"):
        # Dataclasses and similar stdlib decorators synthesize functions from
        # reviewed class declarations.  There is no module-source code object
        # to compare, so only their portable structure is persisted.
        return
    actual = _strict_code_payload(value.__code__)
    if not any(_strict_code_payload(candidate) == actual for candidate in candidates):
        raise CodeIdentityError(
            "canonical runtime code differs from verified module source: "
            f"{value.__module__}.{value.__qualname__} "
            f"(source_candidates={len(candidates)})"
        )


def exact_builtins_namespace(value: types.FunctionType) -> bool:
    """Return whether a function uses the process canonical builtins mapping."""

    return value.__builtins__ is vars(builtins)


__all__ = [
    "CodeIdentityError",
    "assert_canonical_function_matches_source",
    "exact_builtins_namespace",
    "portable_code_payload",
    "portable_constant_payload",
    "portable_function_payload",
    "portable_stdlib_identity",
    "portable_stdlib_object_identity",
]
