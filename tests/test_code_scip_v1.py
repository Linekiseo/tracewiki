from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evidence_rag.rag.sources.code import scip_v1 as scip_module
from evidence_rag.rag.sources.code.scip_v1 import (
    SafeScipRunner,
    ScipCommandPin,
    ScipConsumer,
    ScipDecodeError,
    ScipDecodeLimits,
    ScipEntityRef,
    ScipExecutionOutcome,
    ScipExecutionPolicy,
    ScipIngestionResolver,
    ScipLinkStatus,
    ScipNetworkIsolation,
    ScipOccurrenceKind,
    ScipProtobufDecoder,
    ScipResolverFailure,
    ScipScope,
    ScipStatus,
)

MAIN = "scip-python python example 1.0.0 src/app.py/main()."
HELPER = "scip-python python example 1.0.0 src/app.py/helper()."
EXTERNAL = "scip-python python requests 2.32.0 requests/api.py/get()."
LOCAL = "local 0"
CONTAINER_DIGEST = "sha256:" + ("a" * 64)
CONTAINER_IMAGE = f"ghcr.io/scip-code/scip-python@{CONTAINER_DIGEST}"


def _varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("test protobuf only supports unsigned values")
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _key(number: int, wire_type: int) -> bytes:
    return _varint((number << 3) | wire_type)


def _uint(number: int, value: int) -> bytes:
    return _key(number, 0) + _varint(value)


def _bytes(number: int, value: bytes) -> bytes:
    return _key(number, 2) + _varint(len(value)) + value


def _text(number: int, value: str) -> bytes:
    return _bytes(number, value.encode("utf-8"))


def _packed(number: int, values: list[int]) -> bytes:
    return _bytes(number, b"".join(_varint(value) for value in values))


def _relationship(
    target: str,
    *,
    reference: bool = False,
    implementation: bool = False,
    type_definition: bool = False,
    definition: bool = False,
) -> bytes:
    return b"".join(
        (
            _text(1, target),
            _uint(2, int(reference)),
            _uint(3, int(implementation)),
            _uint(4, int(type_definition)),
            _uint(5, int(definition)),
        )
    )


def _symbol(
    symbol: str,
    *,
    display_name: str = "",
    relationships: tuple[bytes, ...] = (),
) -> bytes:
    return b"".join(
        (
            _text(1, symbol),
            *(_bytes(4, relationship) for relationship in relationships),
            _uint(5, 17),
            _text(6, display_name),
            _text(8, MAIN if symbol == LOCAL else ""),
        )
    )


def _legacy_occurrence(values: list[int], symbol: str, roles: int) -> bytes:
    return _packed(1, values) + _text(2, symbol) + _uint(3, roles)


def _typed_occurrence(
    field_number: int,
    values: list[int],
    symbol: str,
    roles: int,
) -> bytes:
    typed = b"".join(_uint(index, value) for index, value in enumerate(values, 1))
    return _bytes(field_number, typed) + _text(2, symbol) + _uint(3, roles)


def _metadata(tool_name: str = "scip-python", tool_version: str = "0.6.8") -> bytes:
    tool = _text(1, tool_name) + _text(2, tool_version) + _text(3, "index")
    return _uint(1, 1) + _bytes(2, tool) + _text(3, "file:///repo") + _uint(4, 1)


def _document(
    *,
    path: str = "src/app.py",
    occurrences: tuple[bytes, ...] | None = None,
) -> bytes:
    if occurrences is None:
        definition = _legacy_occurrence([0, 0, 3], MAIN, 1)
        occurrences = (
            definition,
            definition,
            _typed_occurrence(8, [1, 0, 4], HELPER, 0),
            _typed_occurrence(9, [2, 0, 3, 2], EXTERNAL, 0),
            _legacy_occurrence([4, 1, 3], LOCAL, 0),
        )
    main_relationship = _relationship(
        HELPER,
        implementation=True,
        definition=True,
    )
    return b"".join(
        (
            _text(1, path),
            *(_bytes(2, occurrence) for occurrence in occurrences),
            _bytes(
                3,
                _symbol(
                    MAIN,
                    display_name="main",
                    relationships=(main_relationship,),
                ),
            ),
            _bytes(3, _symbol(HELPER, display_name="helper")),
            _bytes(3, _symbol(LOCAL, display_name="value")),
            _text(4, "python"),
            _uint(6, 1),
        )
    )


def _index(
    *,
    document: bytes | None = None,
    tool_name: str = "scip-python",
    tool_version: str = "0.6.8",
    unknown: bool = True,
) -> bytes:
    document = document if document is not None else _document()
    payload = (
        _bytes(1, _metadata(tool_name, tool_version))
        + _bytes(2, document)
        + _bytes(3, _symbol(EXTERNAL, display_name="get"))
    )
    if unknown:
        payload += _uint(31, 123) + _key(32, 1) + b"12345678"
    return payload


def _scope(**updates: str) -> ScipScope:
    values = {
        "project_id": "project",
        "repository_id": "repo",
        "generation_id": "generation",
        "acl_ref": "project:project",
    }
    values.update(updates)
    return ScipScope(**values)


def test_ingestion_resolver_is_exact_path_scope_and_descriptor_only() -> None:
    scope = _scope()
    file_entity = SimpleNamespace(
        id="file://src/app.py",
        entity_type="FileVersion",
        project_id=scope.project_id,
        repository_id=scope.repository_id,
        generation_id=scope.generation_id,
        acl_ref=scope.acl_ref,
        path="src/app.py",
        name="app.py",
        qualified_name=None,
    )
    symbol_entity = SimpleNamespace(
        id="symbol://src/app.py/main",
        entity_type="CodeSymbol",
        project_id=scope.project_id,
        repository_id=scope.repository_id,
        generation_id=scope.generation_id,
        acl_ref=scope.acl_ref,
        path="src/app.py",
        name="main",
        qualified_name="app.main",
    )
    cross_file = SimpleNamespace(
        id="file://src/caller.py",
        entity_type="FileVersion",
        project_id=scope.project_id,
        repository_id=scope.repository_id,
        generation_id=scope.generation_id,
        acl_ref=scope.acl_ref,
        path="src/caller.py",
        name="caller.py",
        qualified_name=None,
    )
    resolver = ScipIngestionResolver(
        (file_entity, symbol_entity, cross_file),
        scope=scope,
    )

    assert [
        item.entity_id
        for item in resolver.resolve_file(
            scope=scope,
            relative_path="src/app.py",
        )
    ] == [file_entity.id]
    assert [
        item.entity_id
        for item in resolver.resolve_symbol(
            scope=scope,
            symbol=MAIN,
            relative_path="src/caller.py",
        )
    ] == [symbol_entity.id]
    with pytest.raises(ScipResolverFailure, match="escaped"):
        resolver.resolve_file(
            scope=_scope(generation_id="generation://other"),
            relative_path="src/app.py",
        )


def _entity(
    entity_id: str,
    entity_type: str,
    *,
    scope: ScipScope,
    path: str = "src/app.py",
    symbol: str = "",
) -> ScipEntityRef:
    return ScipEntityRef(
        entity_id=entity_id,
        entity_type=entity_type,  # type: ignore[arg-type]
        project_id=scope.project_id,
        repository_id=scope.repository_id,
        generation_id=scope.generation_id,
        acl_ref=scope.acl_ref,
        relative_path=path,
        scip_symbol=symbol,
    )


class _Resolver:
    def __init__(
        self,
        scope: ScipScope,
        *,
        entity_scope: ScipScope | None = None,
        ambiguous_symbol: str = "",
        unresolved_symbol: str = "",
    ) -> None:
        self.scope = scope
        self.entity_scope = entity_scope or scope
        self.ambiguous_symbol = ambiguous_symbol
        self.unresolved_symbol = unresolved_symbol
        self.symbol_calls: list[str] = []

    def resolve_file(
        self,
        *,
        scope: ScipScope,
        relative_path: str,
    ) -> tuple[ScipEntityRef, ...]:
        assert scope == self.scope
        return (
            _entity(
                "file:app",
                "FileVersion",
                scope=self.entity_scope,
                path=relative_path,
            ),
        )

    def resolve_symbol(
        self,
        *,
        scope: ScipScope,
        symbol: str,
        relative_path: str | None,
    ) -> tuple[ScipEntityRef, ...]:
        assert scope == self.scope
        self.symbol_calls.append(symbol)
        if symbol == self.unresolved_symbol:
            return ()
        if symbol not in {MAIN, HELPER}:
            return ()
        first = _entity(
            f"symbol:{'main' if symbol == MAIN else 'helper'}",
            "CodeSymbol",
            scope=self.entity_scope,
            path=relative_path or "src/app.py",
            symbol=symbol,
        )
        if symbol != self.ambiguous_symbol:
            return (first,)
        second = _entity(
            "symbol:ambiguous",
            "CodeSymbol",
            scope=self.entity_scope,
            path=relative_path or "src/app.py",
            symbol=symbol,
        )
        return (second, first)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/app.py").write_text("def main():\n    helper()\n", encoding="utf-8")
    return root


def _runtime(tmp_path: Path, *, name: str = "docker", mode: int = 0o755) -> Path:
    runtime_root = tmp_path / "runtime-bin"
    runtime_root.mkdir(exist_ok=True)
    executable = runtime_root / name
    executable.write_bytes(b"pinned fake runtime for injection only\n")
    executable.chmod(mode)
    return executable.resolve()


def _container_template(
    executable: Path,
    *,
    command_tail: tuple[str, ...] = (),
) -> tuple[str, ...]:
    return (
        str(executable),
        "run",
        "--rm",
        "--name",
        "{container_name}",
        "--network",
        "none",
        "--read-only",
        "--cpus",
        "{cpus}",
        "--memory",
        "{memory}",
        "--pids-limit",
        "{pids}",
        "--security-opt",
        "no-new-privileges",
        "--mount",
        "type=bind,src={repo},dst=/workspace,readonly",
        "--mount",
        "type=bind,src={output},dst=/output",
        CONTAINER_IMAGE,
        "scip-python",
        "index",
        "--output",
        "/output/index.scip",
        *command_tail,
        "/workspace",
    )


def _pin(
    tmp_path: Path,
    *,
    executable: Path | None = None,
    command_tail: tuple[str, ...] = (),
) -> ScipCommandPin:
    executable = executable or _runtime(tmp_path)
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    return ScipCommandPin(
        argv_template=_container_template(executable, command_tail=command_tail),
        tool_name="scip-python",
        tool_version="0.6.8",
        network_isolation=ScipNetworkIsolation.CONTAINER_NONE,
        executable_sha256=digest,
        container_digest=CONTAINER_DIGEST,
    )


def test_decoder_and_consumer_parse_real_subset_and_link_governed_entities(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    payload = _index()
    scope = _scope()
    resolver = _Resolver(scope)

    decoded = ScipProtobufDecoder().decode_bytes(payload)
    assert decoded.metadata.protocol_version == 1
    assert decoded.metadata.tool_name == "scip-python"
    assert decoded.metadata.tool_version == "0.6.8"
    assert decoded.metadata.tool_arguments == ("index",)
    assert decoded.metadata.project_root == "file:///repo"
    assert decoded.metadata.text_document_encoding == 1
    assert decoded.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert decoded.byte_size == len(payload)
    assert len(decoded.documents) == 1
    assert len(decoded.external_symbols) == 1

    result = ScipConsumer().consume_bytes(
        payload,
        repo_root=root,
        scope=scope,
        resolver=resolver,
        raw_object_id="raw:scip",
    )

    assert result.status is ScipStatus.COMPLETE
    assert result.semantic_ready is False
    assert result.fallback.outcome == "not_used"
    assert result.provenance is not None
    assert result.provenance.derivation == "scip"
    assert result.provenance.raw_object.raw_object_id == "raw:scip"
    assert result.provenance.raw_object.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert result.documents[0].file_link.status is ScipLinkStatus.RESOLVED
    assert result.documents[0].document.relative_path == "src/app.py"
    assert result.documents[0].document.position_encoding == 1

    assert len(result.occurrences) == 4
    kinds = {item.occurrence.symbol: item.occurrence.kind for item in result.occurrences}
    assert kinds[MAIN] is ScipOccurrenceKind.DEFINITION
    assert kinds[HELPER] is ScipOccurrenceKind.REFERENCE
    assert kinds[EXTERNAL] is ScipOccurrenceKind.EXTERNAL
    assert result.occurrences[0].occurrence.source_range.start.line == 1
    assert result.occurrences[0].occurrence.source_range.start.character == 1
    helper = next(item for item in result.occurrences if item.occurrence.symbol == HELPER)
    assert helper.occurrence.source_range.start.line == 2
    assert helper.occurrence.source_range.end.character == 5
    external = next(item for item in result.occurrences if item.occurrence.symbol == EXTERNAL)
    assert external.occurrence.source_range.end.line == 4
    assert external.occurrence.source_range.end.character == 3
    local = next(item for item in result.occurrences if item.occurrence.symbol == LOCAL)
    assert local.symbol_link.status is ScipLinkStatus.LOCAL
    assert LOCAL not in resolver.symbol_calls

    assert result.external_symbols[0].symbol == EXTERNAL
    relationship = result.relationships[0]
    assert relationship.source_symbol == MAIN
    assert relationship.target_symbol == HELPER
    assert relationship.relationship.is_implementation is True
    assert relationship.relationship.is_definition is True
    assert relationship.source_link.status is ScipLinkStatus.RESOLVED
    assert relationship.target_link.status is ScipLinkStatus.RESOLVED
    assert {item.code for item in result.diagnostics} == {
        "duplicate_occurrence",
        "external_symbol",
    }


def test_decoder_accepts_matching_legacy_and_typed_range() -> None:
    typed = _uint(1, 0) + _uint(2, 1) + _uint(3, 4)
    occurrence = _packed(1, [0, 1, 4]) + _bytes(8, typed) + _text(2, MAIN) + _uint(3, 1)
    decoded = ScipProtobufDecoder().decode_bytes(
        _index(document=_document(occurrences=(occurrence,)))
    )
    assert decoded.documents[0].occurrences[0].range_values == (0, 1, 4)


@pytest.mark.parametrize(
    "payload",
    [
        b"\x12\x05ab",
        _key(1, 7),
        _bytes(
            2,
            _bytes(1, "src/\udcff.py".encode("utf-8", "surrogatepass")),
        ),
    ],
)
def test_invalid_or_truncated_protobuf_fails_closed(
    payload: bytes,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    with pytest.raises(ScipDecodeError):
        ScipProtobufDecoder().decode_bytes(payload)

    result = ScipConsumer().consume_bytes(
        payload,
        repo_root=root,
        scope=_scope(),
    )
    assert result.status is ScipStatus.PARTIAL
    assert not result.documents
    assert not result.occurrences
    assert result.provenance is None
    assert result.fallback.outcome == "delegated"


def test_oversized_index_and_nested_message_fail_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    payload = _index()
    decoder = ScipProtobufDecoder(
        ScipDecodeLimits(
            max_file_bytes=len(payload) - 1,
            max_message_bytes=8 * 1024 * 1024,
        )
    )
    with pytest.raises(ScipDecodeError, match="file byte limit"):
        decoder.decode_bytes(payload)

    result = ScipConsumer(decoder=decoder).consume_bytes(
        payload,
        repo_root=root,
        scope=_scope(),
    )
    assert result.status is ScipStatus.PARTIAL
    assert result.diagnostics[0].code == "invalid_or_oversized_index"

    nested_decoder = ScipProtobufDecoder(
        ScipDecodeLimits(
            max_file_bytes=1024,
            max_message_bytes=4,
        )
    )
    with pytest.raises(ScipDecodeError, match="message byte limit"):
        nested_decoder.decode_bytes(_bytes(1, b"12345"))


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../outside.py",
        "src/../../outside.py",
        "C:/outside.py",
        "src\\outside.py",
    ],
)
def test_bad_paths_are_rejected_with_partial_fallback(
    path: str,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    payload = _index(document=_document(path=path))
    result = ScipConsumer().consume_bytes(
        payload,
        repo_root=root,
        scope=_scope(),
        resolver=_Resolver(_scope()),
    )
    assert result.status is ScipStatus.PARTIAL
    assert not result.documents
    assert "bad_path" in {item.code for item in result.diagnostics}
    assert result.fallback.outcome == "delegated"


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    result = ScipConsumer().consume_bytes(
        _index(document=_document(path="escape/secret.py")),
        repo_root=root,
        scope=_scope(),
        resolver=_Resolver(_scope()),
    )
    assert result.status is ScipStatus.PARTIAL
    assert {item.code for item in result.diagnostics} >= {"bad_path"}


def test_bad_range_is_rejected_without_exposing_the_occurrence(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    bad = _legacy_occurrence([5, 8, 4, 1], MAIN, 1)
    result = ScipConsumer().consume_bytes(
        _index(document=_document(occurrences=(bad,))),
        repo_root=root,
        scope=_scope(),
        resolver=_Resolver(_scope()),
    )
    assert result.status is ScipStatus.PARTIAL
    assert not result.occurrences
    assert "bad_range" in {item.code for item in result.diagnostics}


@pytest.mark.parametrize(
    "changed_scope",
    [
        _scope(generation_id="other-generation"),
        _scope(acl_ref="project:other"),
        _scope(repository_id="other-repository"),
        _scope(project_id="other-project"),
    ],
)
def test_resolver_scope_generation_and_acl_mismatches_fail_closed(
    changed_scope: ScipScope,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    scope = _scope()
    result = ScipConsumer().consume_bytes(
        _index(),
        repo_root=root,
        scope=scope,
        resolver=_Resolver(scope, entity_scope=changed_scope),
    )
    assert result.status is ScipStatus.PARTIAL
    assert not result.documents
    assert not result.occurrences
    assert not result.external_symbols
    assert not result.relationships
    assert [item.code for item in result.diagnostics] == ["resolver_failure"]
    assert result.fallback.reason == "resolver_failure"
    assert result.provenance is not None
    assert result.provenance.raw_object.content_sha256 == hashlib.sha256(_index()).hexdigest()


@pytest.mark.parametrize("failure", [RuntimeError("backend"), TimeoutError("deadline")])
def test_resolver_midstream_failure_discards_all_prior_semantic_output(
    failure: Exception,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    scope = _scope()
    payload = _index()

    class _MidstreamFailureResolver(_Resolver):
        def resolve_symbol(
            self,
            *,
            scope: ScipScope,
            symbol: str,
            relative_path: str | None,
        ) -> tuple[ScipEntityRef, ...]:
            if len(self.symbol_calls) == 1:
                raise failure
            return super().resolve_symbol(
                scope=scope,
                symbol=symbol,
                relative_path=relative_path,
            )

    resolver = _MidstreamFailureResolver(scope)
    result = ScipConsumer().consume_bytes(
        payload,
        repo_root=root,
        scope=scope,
        resolver=resolver,
        raw_object_id="raw:resolver-failure",
    )

    assert resolver.symbol_calls == [MAIN]
    assert result.status is ScipStatus.PARTIAL
    assert result.semantic_ready is False
    assert not result.documents
    assert not result.occurrences
    assert not result.external_symbols
    assert not result.relationships
    assert result.diagnostics == (result.diagnostics[0],)
    assert result.diagnostics[0].code == "resolver_failure"
    assert "backend" not in result.diagnostics[0].message
    assert "deadline" not in result.diagnostics[0].message
    assert result.fallback.reason == "resolver_failure"
    assert result.fallback.outcome == "delegated"
    assert result.provenance is not None
    assert result.provenance.raw_object.raw_object_id == "raw:resolver-failure"
    assert result.provenance.raw_object.content_sha256 == hashlib.sha256(payload).hexdigest()


def test_resolver_return_contract_error_is_not_reported_as_unresolved(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    scope = _scope()

    class _InvalidResolver(_Resolver):
        def resolve_file(  # type: ignore[override]
            self,
            *,
            scope: ScipScope,
            relative_path: str,
        ) -> object:
            return iter(())

    result = ScipConsumer().consume_bytes(
        _index(),
        repo_root=root,
        scope=scope,
        resolver=_InvalidResolver(scope),
    )
    assert [item.code for item in result.diagnostics] == ["resolver_failure"]
    assert "unresolved" not in {item.code for item in result.diagnostics}
    assert not result.documents
    assert not result.occurrences


@pytest.mark.parametrize(
    ("resolver_kwargs", "expected_status", "expected_code"),
    [
        ({"unresolved_symbol": HELPER}, ScipLinkStatus.UNRESOLVED, "unresolved"),
        ({"ambiguous_symbol": HELPER}, ScipLinkStatus.AMBIGUOUS, "ambiguous"),
    ],
)
def test_unresolved_and_ambiguous_symbols_are_diagnostic_not_guesses(
    resolver_kwargs: dict[str, str],
    expected_status: ScipLinkStatus,
    expected_code: str,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    scope = _scope()
    result = ScipConsumer().consume_bytes(
        _index(),
        repo_root=root,
        scope=scope,
        resolver=_Resolver(scope, **resolver_kwargs),
    )
    helper = next(item for item in result.occurrences if item.occurrence.symbol == HELPER)
    assert result.status is ScipStatus.PARTIAL
    assert helper.symbol_link.status is expected_status
    assert helper.symbol_link.entity is None
    assert expected_code in {item.code for item in result.diagnostics}


def test_missing_index_and_missing_resolver_are_explicit_partial(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    missing = ScipConsumer().consume(repo_root=root, scope=_scope())
    assert missing.status is ScipStatus.PARTIAL
    assert missing.fallback.reason == "index_unavailable"
    assert missing.fallback.outcome == "delegated"

    no_resolver = ScipConsumer().consume_bytes(
        _index(),
        repo_root=root,
        scope=_scope(),
    )
    assert no_resolver.status is ScipStatus.PARTIAL
    assert no_resolver.provenance is not None
    assert [item.code for item in no_resolver.diagnostics] == ["resolver_failure"]
    assert no_resolver.fallback.reason == "resolver_failure"
    assert not no_resolver.documents
    assert not no_resolver.occurrences
    assert not no_resolver.external_symbols
    assert not no_resolver.relationships
    assert no_resolver.semantic_ready is False


def test_external_execution_defaults_off_and_requires_a_digest_pin(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    result = SafeScipRunner().run(repo_root=root)
    assert result.status is ScipStatus.PARTIAL
    assert result.reason == "external_execution_disabled"
    assert result.index_bytes is None
    assert result.cleanup_status == "not_started"

    executable = _runtime(tmp_path)
    with pytest.raises(ValueError, match="executable_sha256"):
        ScipCommandPin(
            argv_template=_container_template(executable),
            tool_name="scip-python",
            tool_version="0.0.0",
            network_isolation=ScipNetworkIsolation.CONTAINER_NONE,
            container_digest=CONTAINER_DIGEST,
        )
    with pytest.raises(ValueError, match="pinned version"):
        ScipCommandPin(
            argv_template=_container_template(executable),
            tool_name="scip-python",
            tool_version="latest",
            network_isolation=ScipNetworkIsolation.CONTAINER_NONE,
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            container_digest=CONTAINER_DIGEST,
        )
    with pytest.raises(ValueError, match="only container_none"):
        ScipCommandPin(
            argv_template=_container_template(executable),
            tool_name="scip-python",
            tool_version="0.6.8",
            network_isolation=ScipNetworkIsolation.HOST_SANDBOX,
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            container_digest=CONTAINER_DIGEST,
        )


@pytest.mark.parametrize(
    "name",
    [
        "sh",
        "bash",
        "zsh",
        "fish",
        "cmd",
        "powershell",
        "python",
        "node",
        "pip",
        "uv",
        "npm",
        "yarn",
        "pnpm",
    ],
)
def test_allowlist_cannot_bypass_hard_denied_host_executables(
    name: str,
    tmp_path: Path,
) -> None:
    executable = _runtime(tmp_path, name=name)
    with pytest.raises(ValueError, match="hard deny"):
        _pin(tmp_path, executable=executable)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda argv: (*argv[:-1], "-c", "repo.py", argv[-1]),
        lambda argv: (*argv[:-1], "install", argv[-1]),
        lambda argv: (*argv[:-1], "@repo.args", argv[-1]),
        lambda argv: (
            *argv[:-1],
            "--project-name",
            "unsafe;touch",
            argv[-1],
        ),
        lambda argv: tuple(value for value in argv if value not in {"--read-only"}),
        lambda argv: tuple("bridge" if value == "none" else value for value in argv),
    ],
)
def test_container_allowlist_cannot_bypass_fixed_scip_and_isolation_grammar(
    mutate: Any,
    tmp_path: Path,
) -> None:
    executable = _runtime(tmp_path)
    template = mutate(_container_template(executable))
    with pytest.raises(ValueError):
        ScipCommandPin(
            argv_template=template,
            tool_name="scip-python",
            tool_version="0.6.8",
            network_isolation=ScipNetworkIsolation.CONTAINER_NONE,
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
            container_digest=CONTAINER_DIGEST,
        )


def test_container_runtime_path_metacharacters_are_hard_denied(tmp_path: Path) -> None:
    runtime_root = tmp_path / "unsafe;runtime"
    runtime_root.mkdir()
    executable = runtime_root / "docker"
    executable.write_bytes(b"fake\n")
    executable.chmod(0o755)
    with pytest.raises(ValueError, match="unsafe characters"):
        _pin(tmp_path, executable=executable)


def test_safe_runner_uses_exact_argv_clean_env_repo_cwd_and_bounded_artifacts(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    executable = _runtime(tmp_path)
    pin = _pin(
        tmp_path,
        executable=executable,
        command_tail=(
            "--project-name",
            "example",
            "--project-version",
            "1.0.0",
            "--environment",
            "production",
        ),
    )
    payload = _index()
    captured: dict[str, Any] = {}
    cleanup_calls = 0

    def executor(
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        output_path: Path,
        policy: ScipExecutionPolicy,
    ) -> ScipExecutionOutcome:
        captured.update(
            argv=argv,
            cwd=cwd,
            env=env,
            output_path=output_path,
            policy=policy,
        )
        return ScipExecutionOutcome(
            returncode=0,
            stdout=b"created index",
            stderr=b"",
            index_bytes=payload,
        )

    def cleanup_executor(*args: Any, **kwargs: Any) -> ScipExecutionOutcome:
        nonlocal cleanup_calls
        cleanup_calls += 1
        return ScipExecutionOutcome(returncode=0)

    policy = ScipExecutionPolicy(
        enabled=True,
        allowed_pins=(pin,),
        timeout_seconds=2.5,
        output_limit_bytes=64,
        cpu_seconds=2,
        memory_limit_bytes=128 * 1024 * 1024,
        container_cpus=0.5,
        container_pids_limit=32,
    )
    result = SafeScipRunner(
        policy,
        executor=executor,
        cleanup_executor=cleanup_executor,
    ).run(
        repo_root=root,
        pin=pin,
    )

    assert result.status is ScipStatus.COMPLETE
    assert result.index_bytes == payload
    assert captured["cwd"] == root.resolve()
    argv = captured["argv"]
    assert argv[0] == str(executable)
    assert argv[1:8] == (
        "run",
        "--rm",
        "--name",
        argv[4],
        "--network",
        "none",
        "--read-only",
    )
    assert argv[4].startswith("evidence-rag-scip-")
    assert len(argv[4]) == len("evidence-rag-scip-") + 16
    assert (argv[8], argv[9]) == ("--cpus", "0.5")
    assert (argv[10], argv[11]) == ("--memory", str(128 * 1024 * 1024))
    assert (argv[12], argv[13]) == ("--pids-limit", "32")
    assert (argv[14], argv[15]) == ("--security-opt", "no-new-privileges")
    assert argv[17] == f"type=bind,src={root.resolve()},dst=/workspace,readonly"
    output_root = captured["output_path"].parent
    assert argv[19] == f"type=bind,src={output_root},dst=/output"
    assert argv[20] == CONTAINER_IMAGE
    assert argv[21:] == (
        "scip-python",
        "index",
        "--output",
        "/output/index.scip",
        "--project-name",
        "example",
        "--project-version",
        "1.0.0",
        "--environment",
        "production",
        "/workspace",
    )
    assert set(captured["env"]) == {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "NO_PROXY",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "PIP_NO_INDEX",
        "SCIP_NETWORK",
    }
    assert captured["env"]["SCIP_NETWORK"] == "off"
    assert result.network_isolation == "container_none"
    assert result.cleanup_status == "runtime_auto_remove"
    assert cleanup_calls == 0
    assert "container-cpus=0.5" in result.resource_policy
    assert len(result.stdout.content) <= policy.output_limit_bytes


def test_default_backend_timeout_starts_session_and_kills_process_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    captured: dict[str, Any] = {}

    class _FakeProcess:
        pid = 4242
        returncode: int | None = None
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            assert timeout == 1
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    process = _FakeProcess()

    def popen(argv: list[str], **kwargs: Any) -> _FakeProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    def killpg(pid: int, sig: int) -> None:
        captured["killpg"] = (pid, sig)
        process.returncode = -9

    times = iter((0.0, 1.0))
    monkeypatch.setattr(scip_module.subprocess, "Popen", popen)
    monkeypatch.setattr(scip_module.os, "killpg", killpg)
    monkeypatch.setattr(scip_module.time, "monotonic", lambda: next(times))

    outcome = scip_module._bounded_subprocess(
        ("docker", "run"),
        cwd=root,
        env={},
        output_path=tmp_path / "missing-index.scip",
        policy=ScipExecutionPolicy(timeout_seconds=0.01),
    )

    assert outcome.timed_out is True
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["start_new_session"] is True
    assert captured["kwargs"]["close_fds"] is True
    assert captured["killpg"] == (4242, scip_module.signal.SIGKILL)


def test_default_backend_monitors_the_bounded_output_mount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    index_path = tmp_path / "output" / "index.scip"
    index_path.parent.mkdir()
    index_path.write_bytes(b"oversized")

    class _FakeProcess:
        pid = 4343
        returncode: int | None = None
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            assert self.returncode is not None
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

    process = _FakeProcess()

    def killpg(pid: int, sig: int) -> None:
        process.returncode = -9

    monkeypatch.setattr(scip_module.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(scip_module.os, "killpg", killpg)
    outcome = scip_module._bounded_subprocess(
        ("docker", "run"),
        cwd=root,
        env={},
        output_path=index_path,
        policy=ScipExecutionPolicy(index_limit_bytes=4),
    )
    assert outcome.output_limited is True
    assert outcome.returncode == -9


@pytest.mark.parametrize(
    ("outcome", "reason", "cleanup_status"),
    [
        (
            ScipExecutionOutcome(returncode=None, timed_out=True),
            "indexer_timeout",
            "succeeded",
        ),
        (
            ScipExecutionOutcome(returncode=0, stdout=b"x" * 65, index_bytes=b"index"),
            "indexer_output_limit",
            "succeeded",
        ),
        (
            ScipExecutionOutcome(returncode=7, stderr=b"failed"),
            "indexer_failed",
            "succeeded",
        ),
        (
            ScipExecutionOutcome(returncode=0, index_bytes=b""),
            "index_artifact_empty",
            "runtime_auto_remove",
        ),
    ],
)
def test_safe_runner_failures_are_partial_and_non_blocking(
    outcome: ScipExecutionOutcome,
    reason: str,
    cleanup_status: str,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    pin = _pin(tmp_path)
    captured: dict[str, Any] = {}

    def executor(*args: Any, **kwargs: Any) -> ScipExecutionOutcome:
        captured["main_argv"] = args[0]
        return outcome

    def cleanup_executor(
        argv: tuple[str, ...],
        **kwargs: Any,
    ) -> ScipExecutionOutcome:
        captured["cleanup_argv"] = argv
        return ScipExecutionOutcome(returncode=0)

    runner = SafeScipRunner(
        ScipExecutionPolicy(
            enabled=True,
            allowed_pins=(pin,),
            output_limit_bytes=64,
        ),
        executor=executor,
        cleanup_executor=cleanup_executor,
    )
    result = runner.run(repo_root=root, pin=pin)
    assert result.status is ScipStatus.PARTIAL
    assert result.reason == reason
    assert result.index_bytes is None
    assert result.cleanup_status == cleanup_status
    if cleanup_status == "succeeded":
        assert captured["cleanup_argv"] == (
            captured["main_argv"][0],
            "rm",
            "-f",
            captured["main_argv"][4],
        )
    else:
        assert "cleanup_argv" not in captured
    assert len(result.stdout.content) + len(result.stderr.content) <= 64


def test_container_cleanup_failure_is_recorded_and_remains_partial(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    pin = _pin(tmp_path)

    def executor(*args: Any, **kwargs: Any) -> ScipExecutionOutcome:
        return ScipExecutionOutcome(returncode=None, timed_out=True)

    def cleanup_executor(*args: Any, **kwargs: Any) -> ScipExecutionOutcome:
        return ScipExecutionOutcome(returncode=9, stderr=b"daemon unavailable")

    result = SafeScipRunner(
        ScipExecutionPolicy(enabled=True, allowed_pins=(pin,)),
        executor=executor,
        cleanup_executor=cleanup_executor,
    ).run(repo_root=root, pin=pin)
    assert result.status is ScipStatus.PARTIAL
    assert result.reason == "indexer_timeout"
    assert result.cleanup_status == "failed"
    assert result.index_bytes is None


def test_container_executor_exception_still_triggers_independent_cleanup(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    pin = _pin(tmp_path)
    cleanup_argv: tuple[str, ...] = ()

    def executor(*args: Any, **kwargs: Any) -> ScipExecutionOutcome:
        raise OSError("client transport failed after start")

    def cleanup_executor(
        argv: tuple[str, ...],
        **kwargs: Any,
    ) -> ScipExecutionOutcome:
        nonlocal cleanup_argv
        cleanup_argv = argv
        return ScipExecutionOutcome(returncode=0)

    result = SafeScipRunner(
        ScipExecutionPolicy(enabled=True, allowed_pins=(pin,)),
        executor=executor,
        cleanup_executor=cleanup_executor,
    ).run(repo_root=root, pin=pin)
    assert result.status is ScipStatus.PARTIAL
    assert result.reason == "indexer_unavailable"
    assert result.cleanup_status == "succeeded"
    assert cleanup_argv[1:3] == ("rm", "-f")
    assert cleanup_argv[3].startswith("evidence-rag-scip-")


def test_unavailable_and_non_allowlisted_commands_never_execute(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    unavailable_path = Path("/definitely/missing/docker")
    unavailable = ScipCommandPin(
        argv_template=_container_template(unavailable_path),
        tool_name="scip-python",
        tool_version="0.6.8",
        network_isolation=ScipNetworkIsolation.CONTAINER_NONE,
        executable_sha256="0" * 64,
        container_digest=CONTAINER_DIGEST,
    )
    other = _pin(tmp_path)
    calls = 0

    def executor(*args: Any, **kwargs: Any) -> ScipExecutionOutcome:
        nonlocal calls
        calls += 1
        return ScipExecutionOutcome(returncode=0, index_bytes=_index())

    runner = SafeScipRunner(
        ScipExecutionPolicy(enabled=True, allowed_pins=(unavailable,)),
        executor=executor,
        cleanup_executor=executor,
    )
    assert runner.run(repo_root=root, pin=unavailable).reason == "indexer_unavailable"
    assert runner.run(repo_root=root, pin=other).reason == "command_not_allowlisted"
    assert calls == 0


@pytest.mark.parametrize(
    ("runtime_kind", "expected_reason"),
    [
        ("world_writable", "runtime_file_unsafe"),
        ("symlink", "runtime_file_unsafe"),
        ("inside_repo", "runtime_inside_repository"),
        ("digest_mismatch", "executable_digest_mismatch"),
    ],
)
def test_container_runtime_file_must_be_pinned_external_regular_and_safe(
    runtime_kind: str,
    expected_reason: str,
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    if runtime_kind == "inside_repo":
        runtime_root = root / "tools"
        runtime_root.mkdir()
        executable = runtime_root / "docker"
        executable.write_bytes(b"inside repository\n")
        executable.chmod(0o755)
    elif runtime_kind == "symlink":
        target = _runtime(tmp_path, name="podman")
        executable = tmp_path / "docker"
        executable.symlink_to(target)
    else:
        executable = _runtime(
            tmp_path,
            mode=0o777 if runtime_kind == "world_writable" else 0o755,
        )
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    if runtime_kind == "digest_mismatch":
        digest = "0" * 64
    pin = ScipCommandPin(
        argv_template=_container_template(executable),
        tool_name="scip-python",
        tool_version="0.6.8",
        network_isolation=ScipNetworkIsolation.CONTAINER_NONE,
        executable_sha256=digest,
        container_digest=CONTAINER_DIGEST,
    )
    calls = 0

    def executor(*args: Any, **kwargs: Any) -> ScipExecutionOutcome:
        nonlocal calls
        calls += 1
        return ScipExecutionOutcome(returncode=0, index_bytes=_index())

    result = SafeScipRunner(
        ScipExecutionPolicy(enabled=True, allowed_pins=(pin,)),
        executor=executor,
        cleanup_executor=executor,
    ).run(repo_root=root, pin=pin)
    assert result.status is ScipStatus.PARTIAL
    assert result.reason == expected_reason
    assert result.cleanup_status == "not_started"
    assert calls == 0


def test_consumer_prefers_supplied_index_and_can_use_injected_runner(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    scope = _scope()
    supplied_payload = _index()
    (root / "index.scip").write_bytes(supplied_payload)
    pin = _pin(tmp_path)
    runner_calls = 0

    def executor(*args: Any, **kwargs: Any) -> ScipExecutionOutcome:
        nonlocal runner_calls
        runner_calls += 1
        return ScipExecutionOutcome(
            returncode=0,
            index_bytes=_index(tool_version="unexpected"),
        )

    runner = SafeScipRunner(
        ScipExecutionPolicy(enabled=True, allowed_pins=(pin,)),
        executor=executor,
        cleanup_executor=lambda *args, **kwargs: ScipExecutionOutcome(returncode=0),
    )
    preferred = ScipConsumer(runner=runner).consume(
        repo_root=root,
        scope=scope,
        resolver=_Resolver(scope),
        runner_pin=pin,
    )
    assert preferred.status is ScipStatus.COMPLETE
    assert runner_calls == 0
    assert preferred.provenance is not None
    assert (
        preferred.provenance.raw_object.content_sha256
        == hashlib.sha256(supplied_payload).hexdigest()
    )

    (root / "index.scip").unlink()
    generated = ScipConsumer(runner=runner).consume(
        repo_root=root,
        scope=scope,
        resolver=_Resolver(scope),
        runner_pin=pin,
    )
    assert runner_calls == 1
    assert generated.status is ScipStatus.PARTIAL
    assert generated.fallback.reason == "indexer_metadata_mismatch"


def test_typed_range_conflict_and_field_budget_fail_closed() -> None:
    conflict = (
        _packed(1, [0, 0, 3]) + _bytes(8, _uint(1, 1) + _uint(2, 0) + _uint(3, 3)) + _text(2, MAIN)
    )
    with pytest.raises(ScipDecodeError, match="conflict"):
        ScipProtobufDecoder().decode_bytes(_index(document=_document(occurrences=(conflict,))))

    decoder = ScipProtobufDecoder(
        ScipDecodeLimits(
            max_file_bytes=1024,
            max_message_bytes=1024,
            max_fields_per_message=1,
        )
    )
    with pytest.raises(ScipDecodeError, match="field count limit"):
        decoder.decode_bytes(_uint(31, 1) + _uint(32, 2))
