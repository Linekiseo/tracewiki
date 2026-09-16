from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest

from evidence_rag.parser import CodeParser


def _units(parsed: Any, role: str) -> list[Any]:
    return [unit for unit in parsed.units if unit.role == role]


def _assert_locators(source: str, parsed: Any) -> None:
    encoded = source.encode("utf-8")
    for unit in parsed.units:
        assert encoded[unit.start_byte : unit.end_byte].decode("utf-8") == unit.text
        assert unit.start_line == encoded[: unit.start_byte].count(b"\n") + 1
        assert unit.end_line == encoded[: unit.end_byte].count(b"\n") + 1


def test_python_ir_covers_nested_async_decorator_branch_loop_and_try() -> None:
    source = '''\
@registered
async def outer(limit: int):
    """Load values."""
    def nested(value):
        return value

    if limit:
        for value in range(limit):
            try:
                await send(value)
            except ValueError:
                continue
    return nested
'''
    parsed = CodeParser().parse("src/example.py", source)

    assert parsed.parse_error is None
    assert {unit.role for unit in parsed.units} >= {
        "file",
        "declaration",
        "definition",
        "compound",
        "branch",
        "loop",
        "try",
    }
    definitions = _units(parsed, "definition")
    outer = next(unit for unit in definitions if unit.signature == "async def outer(limit: int):")
    nested = next(unit for unit in definitions if unit.signature == "def nested(value):")
    assert outer.doc == "Load values."
    assert nested.parent_structural_path is not None
    assert nested.structural_path.startswith(outer.structural_path + "/")
    assert {"outer", "limit", "nested", "send", "ValueError"} <= set(outer.identifiers)
    assert all(unit.quality == "complete" for unit in parsed.units)
    assert all(unit.parser_version == CodeParser.parser_version for unit in parsed.units)
    _assert_locators(source, parsed)


@pytest.mark.parametrize(
    ("path", "source", "expected_types"),
    [
        (
            "src/example.js",
            """\
/** Service docs. */
class Service {
  get value() { return 1; }
  set value(next) {}
  run(input) { return input; }
}
function create() { return new Service(); }
const transform = (value) => value + 1;
""",
            {"class_declaration", "method_definition", "function_declaration", "arrow_function"},
        ),
        (
            "src/example.ts",
            """\
class Service {
  run(input: string): string;
  run(input: string): string { return input; }
}
function create(): Service { return new Service(); }
const transform = (value: number): number => value + 1;
""",
            {
                "class_declaration",
                "method_definition",
                "method_signature",
                "function_declaration",
                "arrow_function",
            },
        ),
    ],
)
def test_javascript_and_typescript_definition_ir(
    path: str,
    source: str,
    expected_types: set[str],
) -> None:
    parsed = CodeParser().parse(path, source)

    assert parsed.parse_error is None
    definition_types = {unit.node_type for unit in _units(parsed, "definition")}
    assert expected_types <= definition_types
    arrow = next(unit for unit in parsed.units if unit.node_type == "arrow_function")
    assert arrow.signature is not None and arrow.signature.startswith("transform =")
    assert "transform" in arrow.identifiers
    assert any(unit.signature and "create" in unit.signature for unit in parsed.units)
    _assert_locators(source, parsed)


def test_tsx_component_and_hook_have_stable_definition_ir() -> None:
    source = """\
export const Card = ({title}: Props) => <section>{title}</section>;
function useCounter() {
  const [count, setCount] = useState(0);
  return count;
}
"""
    parser = CodeParser()
    first = parser.parse("src/Card.tsx", source)
    second = parser.parse("src/Card.tsx", source)

    definitions = _units(first, "definition")
    assert any(unit.signature and unit.signature.startswith("Card =") for unit in definitions)
    assert any(unit.signature == "function useCounter()" for unit in definitions)
    assert (
        "Card"
        in next(unit for unit in definitions if unit.node_type == "arrow_function").identifiers
    )
    assert [asdict(unit) for unit in first.units] == [asdict(unit) for unit in second.units]


def test_unicode_crlf_byte_and_line_locators_are_exact() -> None:
    source = 'def greet(name):\r\n    message = "你好, " + name\r\n    return message\r\n'
    parsed = CodeParser().parse("src/greeting.py", source)

    function = next(unit for unit in parsed.units if unit.node_type == "function_definition")
    assert function.start_line == 1
    assert function.end_line == 3
    assert "你好" in function.text
    assert function.end_byte == len(source.rstrip("\r\n").encode("utf-8"))
    _assert_locators(source, parsed)


def test_syntax_error_retains_partial_ast_units() -> None:
    source = """\
def broken(:
    if ready:
        return value
"""
    parsed = CodeParser().parse("src/broken.py", source)

    assert parsed.parse_error == "tree-sitter reported syntax errors; partial AST retained"
    assert parsed.units
    assert all(unit.quality == "partial" for unit in parsed.units)
    assert any(unit.role == "branch" for unit in parsed.units)
    _assert_locators(source, parsed)


def test_text_fallback_is_line_aware_and_empty_files_remain_parseable() -> None:
    source = "".join(f"line {index}\n" for index in range(205))
    parsed = CodeParser().parse("notes.md", source)
    empty_text = CodeParser().parse("empty.md", "")
    empty_python = CodeParser().parse("empty.py", "")

    assert parsed.symbols == []
    assert len(parsed.units) == 2
    assert all(unit.role == "fallback" and unit.quality == "fallback" for unit in parsed.units)
    assert parsed.units[0].structural_path == "text_chunk[0]"
    assert parsed.units[1].start_byte == parsed.units[0].end_byte
    assert empty_text.units[0].text == ""
    assert empty_text.units[0].start_line == empty_text.units[0].end_line == 1
    assert [(unit.role, unit.node_type, unit.text) for unit in empty_python.units] == [
        ("file", "module", "")
    ]
    _assert_locators(source, parsed)


def test_parser_exception_returns_fallback_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "def still_searchable():\n    return True\n"
    parser = CodeParser()

    def unavailable(_language: str) -> Any:
        raise RuntimeError("grammar unavailable")

    monkeypatch.setattr(parser, "_parser_for", unavailable)
    parsed = parser.parse("src/unavailable.py", source)

    assert parsed.parse_error == "parser unavailable: RuntimeError: grammar unavailable"
    assert parsed.symbols == []
    assert len(parsed.units) == 1
    assert parsed.units[0].quality == "fallback"
    assert parsed.units[0].text == source
    _assert_locators(source, parsed)


def test_generated_and_minified_inputs_are_deterministic() -> None:
    generated = "# generated\n" + "\n".join(
        f"def generated_{index}(): return {index}" for index in range(150)
    )
    minified = "const run=(items)=>{for(const item of items){if(item){use(item)}}};"
    parser = CodeParser()

    generated_parsed = parser.parse("src/generated.py", generated)
    minified_first = parser.parse("src/minified.js", minified)
    minified_second = parser.parse("src/minified.js", minified)

    assert generated_parsed.parse_error is None
    assert len(_units(generated_parsed, "definition")) == 150
    assert minified_first.parse_error is None
    assert {"definition", "compound", "loop", "branch"} <= {
        unit.role for unit in minified_first.units
    }
    assert [asdict(unit) for unit in minified_first.units] == [
        asdict(unit) for unit in minified_second.units
    ]


def test_structural_paths_ignore_absolute_lines_and_parse_tree_is_built_once() -> None:
    source = """\
def first():
    return 1

def second():
    return 2
"""
    shifted_source = "\n\n" + source
    parser = CodeParser()
    tree_parser = parser._parser_for("python")

    class CountingParser:
        def __init__(self) -> None:
            self.calls = 0

        def parse(self, encoded: bytes) -> Any:
            self.calls += 1
            return tree_parser.parse(encoded)

    counting_parser = CountingParser()
    parser._local.parsers["python"] = counting_parser
    parsed = parser.parse("src/example.py", source)
    shifted = CodeParser().parse("src/example.py", shifted_source)

    paths = [
        unit.structural_path for unit in parsed.units if unit.node_type == "function_definition"
    ]
    shifted_paths = [
        unit.structural_path for unit in shifted.units if unit.node_type == "function_definition"
    ]
    assert counting_parser.calls == 1
    assert (
        paths
        == shifted_paths
        == [
            "module[0]/function_definition[0]",
            "module[0]/function_definition[1]",
        ]
    )
    assert all(not hasattr(unit, "node") for unit in parsed.units)
