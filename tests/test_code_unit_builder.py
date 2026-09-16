from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from evidence_rag.parser import CodeParser
from evidence_rag.rag.sources.code.unit_builder import (
    BUILDER_VERSION,
    CodeSourceLineage,
    CodeUnitBuildContext,
    CodeUnitBuilder,
    CodeUnitBuildRequest,
)


def _request(
    path: str,
    source: str,
    *,
    ref: str = "refs/heads/main",
    generation_id: str = "generation://repo/main/001",
    acl_ref: str = "acl://engineering",
) -> CodeUnitBuildRequest:
    parsed = CodeParser().parse(path, source, blob_hash="git-blob:abc123")
    return CodeUnitBuildRequest.from_parsed_file(
        parsed,
        project_id="project://rag",
        repository_id="repository://shop",
        generation_id=generation_id,
        entity_id=f"entity://file/{path}",
        ref=ref,
        acl_ref=acl_ref,
        qualified_name=path.replace("/", "."),
        source_uri=f"github://shop/{path}@{ref}",
        lineage_attributes={"checkout": "worktree-17", "provider": "git"},
    )


def test_python_nested_ir_maps_one_to_one_with_stable_fields_and_relations() -> None:
    source = '''\
class Checkout:
    """Checkout operations."""

    def total(self, values: list[int]) -> int:
        """Compute a total."""
        def normalize(value: int) -> int:
            return max(0, value)

        if values:
            return sum(normalize(value) for value in values)
        return 0
'''
    request = _request("src/checkout.py", source)
    result = CodeUnitBuilder().build(request)

    assert len(result.records) == len(request.units)
    assert len(result.relations) == len(result.records)
    normalize = next(
        record
        for record in result.records
        if record.signature == "def normalize(value: int) -> int:"
    )
    assert normalize.structural_path.endswith("/function_definition[0]")
    assert normalize.span.start_line == 6
    assert normalize.span.start_byte < normalize.span.end_byte
    assert normalize.docstring is None
    assert {"normalize", "value", "max"} <= set(normalize.identifiers)
    assert normalize.parent_unit_id is not None
    assert normalize.quality_status == "complete"
    assert normalize.parser_version == CodeParser.parser_version
    assert normalize.builder_version == BUILDER_VERSION
    assert normalize.unit_id.startswith("unit://sha256/")

    relation = next(item for item in result.relations if item.target_unit_id == normalize.unit_id)
    assert relation.relation_type == "CONTAINS"
    assert relation.source_id == normalize.parent_unit_id
    assert normalize.context_ref.direct_relation_ids == (relation.relation_id,)

    stored = normalize.as_store_record()
    assert stored["id"] == normalize.unit_id
    assert stored["path"] == "src/checkout.py"
    assert stored["start_line"] == normalize.span.start_line
    assert stored["metadata"]["structural_path"] == normalize.structural_path
    assert stored["metadata"]["source_lineage"]["source_uri"].startswith("github://")


def test_typescript_definitions_and_neighbors_are_deterministic() -> None:
    source = """\
export class Money {
  format(value: number): string { return String(value); }
}
export const addTax = (value: number): number => value * 1.2;
"""
    request = _request("web/money.ts", source)
    builder = CodeUnitBuilder()

    first = builder.build(request)
    second = builder.build(request)

    assert first == second
    definitions = [record for record in first.records if record.unit_type == "symbol.ast_block"]
    assert {record.ast_node_type for record in definitions} >= {
        "class_declaration",
        "method_definition",
        "arrow_function",
    }
    arrow = next(record for record in definitions if record.ast_node_type == "arrow_function")
    assert arrow.signature is not None and arrow.signature.startswith("addTax =")
    assert "addTax" in arrow.identifiers
    assert tuple(record.unit_id for record in first.records) == tuple(
        record.unit_id for record in second.records
    )
    assert all(
        neighbor_id != record.unit_id
        for record in first.records
        for neighbor_id in record.context_ref.neighbor_unit_ids
    )


def test_partial_and_line_aware_fallback_quality_and_spans_are_preserved() -> None:
    partial = _request(
        "src/broken.py",
        "def broken(:\n    if ready:\n        return value\n",
    )
    fallback_source = "".join(f"line {index}\n" for index in range(205))
    fallback = _request("notes.md", fallback_source)

    partial_result = CodeUnitBuilder().build(partial)
    fallback_result = CodeUnitBuilder().build(fallback)

    assert partial.parse_error is not None
    assert all(record.quality_status == "partial" for record in partial_result.records)
    assert all(record.parse_error == partial.parse_error for record in partial_result.records)
    assert [record.unit_type for record in fallback_result.records] == [
        "fallback.line_block",
        "fallback.line_block",
    ]
    assert fallback_result.records[0].span.start_line == 1
    assert fallback_result.records[1].span.start_line == 201
    assert fallback_result.records[0].span.end_byte == fallback_result.records[1].span.start_byte
    assert all(record.quality_status == "fallback" for record in fallback_result.records)


def test_identity_is_stable_and_content_or_builder_changes_it() -> None:
    source = "def total(value: int) -> int:\n    return value\n"
    changed_source = "def total(value: int) -> int:\n    return value + 1\n"
    request = _request("src/totals.py", source)
    rebuilt = _request("src/totals.py", source)
    changed = _request("src/totals.py", changed_source)

    first = CodeUnitBuilder().build(request)
    second = CodeUnitBuilder().build(rebuilt)
    changed_result = CodeUnitBuilder().build(changed)
    versioned_builder = CodeUnitBuilder("c2-code-retrieval-unit-builder-v2").build(request)
    function = next(record for record in first.records if record.signature)
    repeated_function = next(record for record in second.records if record.signature)
    changed_function = next(record for record in changed_result.records if record.signature)
    versioned_function = next(record for record in versioned_builder.records if record.signature)

    assert function == repeated_function
    assert function.unit_id == repeated_function.unit_id
    assert function.content_hash == repeated_function.content_hash
    assert function.unit_id != changed_function.unit_id
    assert function.content_hash != changed_function.content_hash
    assert function.unit_id != versioned_function.unit_id


def test_ref_generation_acl_and_source_lineage_are_retained_without_false_rekeying() -> None:
    source = "export function checkout(): boolean { return true; }\n"
    main = _request("web/checkout.ts", source)
    historical = _request(
        "web/checkout.ts",
        source,
        ref="commit:0123456789abcdef",
        generation_id="generation://repo/commit/012345",
        acl_ref="acl://restricted",
    )

    current_result = CodeUnitBuilder().build(main)
    historical_result = CodeUnitBuilder().build(historical)
    current = next(record for record in current_result.records if record.signature)
    old = next(record for record in historical_result.records if record.signature)

    # Ref/generation are occurrence provenance. The design identity stays content based.
    assert current.unit_id == old.unit_id
    assert current.ref == "refs/heads/main"
    assert old.ref == "commit:0123456789abcdef"
    assert current.generation_id != old.generation_id
    assert old.acl_ref == "acl://restricted"
    assert old.source_lineage.ref == old.ref
    assert old.source_lineage.blob_hash == "git-blob:abc123"
    assert old.source_lineage.file_content_hash.startswith("sha256:")
    assert dict(old.source_lineage.attributes) == {
        "checkout": "worktree-17",
        "provider": "git",
    }
    assert old.as_store_record()["metadata"]["ref"] == old.ref
    with pytest.raises(FrozenInstanceError):
        old.acl_ref = "acl://public"  # type: ignore[misc]


def test_builder_has_no_database_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = CodeParser().parse("src/pure.py", "def pure():\n    return True\n")
    context = CodeUnitBuildContext(
        project_id="project://rag",
        repository_id="repository://shop",
        generation_id="generation://pure",
        entity_id="entity://pure",
        ref="refs/heads/main",
        language=parsed.language,
        file_path=parsed.path,
        acl_ref="acl://engineering",
        lineage=CodeSourceLineage(
            source_uri="memory://src/pure.py",
            ref="refs/heads/main",
        ),
    )

    def fail_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unit builder must not open SQLite")

    monkeypatch.setattr(sqlite3, "connect", fail_connect)
    result = CodeUnitBuilder().build(parsed, context=context)

    assert result.records
    assert result.store_records()[0]["generation_id"] == "generation://pure"
