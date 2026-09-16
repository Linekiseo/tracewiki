from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from tree_sitter_language_pack import get_parser

from .models import ParsedCodeUnitIR, ParsedFile, ParsedSymbol

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".sql": "sql",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "rst",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".xml": "xml",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".proto": "proto",
    ".tf": "hcl",
    ".gradle": "groovy",
    ".vue": "vue",
    ".svelte": "svelte",
}

TEXT_FILENAMES = {
    "dockerfile": "dockerfile",
    "makefile": "make",
    "jenkinsfile": "groovy",
    "gemfile": "ruby",
    "rakefile": "ruby",
}

STRUCTURED_LANGUAGES = {
    "python",
    "javascript",
    "typescript",
    "tsx",
    "java",
    "go",
    "rust",
    "c",
    "cpp",
    "c_sharp",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
}

DEFINITION_TYPES: dict[str, dict[str, str]] = {
    "python": {
        "class_definition": "class",
        "function_definition": "function",
    },
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "method_definition": "method",
        "generator_function_declaration": "function",
    },
    "typescript": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "function_declaration": "function",
        "method_definition": "method",
        "abstract_method_signature": "method",
    },
    "tsx": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "function_declaration": "function",
        "method_definition": "method",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "record",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "trait",
        "impl_item": "impl",
    },
    "c": {
        "function_definition": "function",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
    },
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "namespace_definition": "namespace",
    },
    "c_sharp": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "struct_declaration": "struct",
        "method_declaration": "method",
        "constructor_declaration": "constructor",
    },
    "ruby": {
        "class": "class",
        "module": "module",
        "method": "method",
        "singleton_method": "method",
    },
    "php": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "function_definition": "function",
        "method_declaration": "method",
    },
    "swift": {
        "class_declaration": "class",
        "protocol_declaration": "interface",
        "function_declaration": "function",
        "init_declaration": "constructor",
    },
    "kotlin": {
        "class_declaration": "class",
        "object_declaration": "object",
        "function_declaration": "function",
    },
    "scala": {
        "class_definition": "class",
        "object_definition": "object",
        "trait_definition": "trait",
        "function_definition": "function",
    },
}

CALL_TYPES = {
    "call",
    "call_expression",
    "method_invocation",
    "function_call_expression",
    "invocation_expression",
    "command",
    "call_expression_with_block",
}

# Tree-sitter grammars use a small family of node names for identifiers.  We keep
# this deliberately conservative: the linker below only publishes a REFERENCES
# edge when the name resolves unambiguously within the current file or one of its
# imports, so local variables never become graph nodes by themselves.
REFERENCE_TYPES = {
    "identifier",
    "type_identifier",
    "field_identifier",
    "constant",
    "namespace_identifier",
    "scoped_identifier",
}

IR_IDENTIFIER_TYPES = REFERENCE_TYPES | {
    "label_identifier",
    "namespace_name",
    "private_property_identifier",
    "property_identifier",
    "shorthand_property_identifier",
    "shorthand_property_identifier_pattern",
    "statement_identifier",
}

IR_DEFINITION_TYPES = {
    node_type for definitions in DEFINITION_TYPES.values() for node_type in definitions
} | {
    "anonymous_function",
    "arrow_function",
    "closure_expression",
    "function_expression",
    "function_signature",
    "generator_function",
    "lambda",
    "lambda_expression",
    "method_signature",
}

IR_COMPOUND_TYPES = {
    "block",
    "body_statement",
    "class_body",
    "code_block",
    "compound_statement",
    "declaration_list",
    "enum_body",
    "field_declaration_list",
    "interface_body",
    "statement_block",
    "switch_body",
    "template_body",
}

IR_BRANCH_TYPES = {
    "case",
    "case_clause",
    "case_statement",
    "conditional_expression",
    "elif",
    "elif_clause",
    "else_clause",
    "elsif",
    "expression_case",
    "expression_switch_statement",
    "if",
    "if_expression",
    "if_statement",
    "match_arm",
    "match_block",
    "match_expression",
    "match_statement",
    "switch_case",
    "switch_expression",
    "switch_statement",
    "type_case",
    "type_switch_statement",
    "unless",
    "when",
    "when_entry",
    "when_expression",
}

IR_LOOP_TYPES = {
    "do",
    "do_expression",
    "do_statement",
    "enhanced_for_statement",
    "for",
    "for_expression",
    "for_in_statement",
    "for_statement",
    "loop_expression",
    "repeat_while_statement",
    "until",
    "while",
    "while_expression",
    "while_statement",
}

IR_TRY_TYPES = {
    "catch_block",
    "catch_clause",
    "ensure",
    "except_clause",
    "finally_block",
    "finally_clause",
    "rescue",
    "try_expression",
    "try_statement",
    "try_with_resources_statement",
}

IR_DECLARATION_TYPES = {
    "decorated_definition",
    "export_statement",
    "field_declaration",
    "lexical_declaration",
    "property_declaration",
    "type_alias_declaration",
    "variable_declaration",
    "variable_declarator",
}

IDENTIFIER_PATTERN = re.compile(r"(?u)(?:[^\W\d]|[$_])(?:\w|[$])*")

IMPORT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"^\s*from\s+([.\w]+)\s+import\s+", re.MULTILINE),
        re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE),
    ],
    "javascript": [
        re.compile(r"\bfrom\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
        re.compile(r"\bimport\s*\(\s*['\"]([^'\"]+)['\"]\s*\)"),
    ],
    "typescript": [
        re.compile(r"\bfrom\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
    ],
    "tsx": [re.compile(r"\bfrom\s+['\"]([^'\"]+)['\"]")],
    "java": [re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.MULTILINE)],
    "go": [re.compile(r"['\"]([^'\"]+)['\"]")],
    "rust": [re.compile(r"^\s*use\s+([^;]+);", re.MULTILINE)],
    "c": [re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.MULTILINE)],
    "cpp": [re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.MULTILINE)],
    "c_sharp": [re.compile(r"^\s*using\s+([\w.]+)\s*;", re.MULTILINE)],
    "kotlin": [re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)],
    "swift": [re.compile(r"^\s*import\s+([\w.]+)", re.MULTILINE)],
}


def language_for_path(path: str | Path) -> str | None:
    item = PurePosixPath(str(path).replace("\\", "/"))
    if item.name.startswith(".env"):
        return "dotenv"
    return LANGUAGE_BY_SUFFIX.get(item.suffix.casefold()) or TEXT_FILENAMES.get(
        item.name.casefold()
    )


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _definition_name(node: Any, source: bytes) -> str | None:
    for field in ("name", "declarator", "type"):
        child = node.child_by_field_name(field)
        if child is not None:
            text = _node_text(child, source).strip()
            # C/C++ declarators may include the full signature; retain the last identifier.
            identifiers = re.findall(r"[A-Za-z_$][\w$]*", text)
            if identifiers:
                return identifiers[-1]
    return None


def _call_name(node: Any, source: bytes) -> str | None:
    child = None
    for field in ("function", "name", "method", "selector"):
        child = node.child_by_field_name(field)
        if child is not None:
            break
    if child is None and node.named_children:
        child = node.named_children[0]
    if child is None:
        return None
    text = _node_text(child, source).strip()
    identifiers = re.findall(r"[A-Za-z_$][\w$]*", text)
    if not identifiers:
        return None
    return identifiers[-1]


def _unique_identifiers(text: str) -> list[str]:
    return list(dict.fromkeys(IDENTIFIER_PATTERN.findall(text)))


def _unit_role(node_type: str, *, is_root: bool) -> str | None:
    if is_root:
        return "file"
    if node_type in IR_DEFINITION_TYPES:
        return "definition"
    if node_type in IR_BRANCH_TYPES:
        return "branch"
    if node_type in IR_LOOP_TYPES:
        return "loop"
    if node_type in IR_TRY_TYPES:
        return "try"
    if (
        node_type in IR_COMPOUND_TYPES
        or node_type.endswith("_body")
        or node_type.endswith("_block")
    ):
        return "compound"
    if (
        node_type in IR_DECLARATION_TYPES
        or node_type.endswith("_declaration")
        or node_type.endswith("_declarator")
        or node_type.endswith("_definition")
    ):
        return "declaration"
    return None


def _normalized_signature(text: str) -> str | None:
    signature = re.sub(r"\s+", " ", text).strip()
    if not signature:
        return None
    if len(signature) > 500:
        return signature[:497].rstrip() + "..."
    return signature


def _signature(
    node: Any,
    source: bytes,
    role: str,
    parent: Any | None,
) -> str | None:
    if role not in {"branch", "declaration", "definition", "loop", "try"}:
        return None

    boundary = None
    for field in ("body", "consequence"):
        boundary = node.child_by_field_name(field)
        if boundary is not None:
            break
    if boundary is None:
        for child in tuple(node.named_children):
            if child.type in IR_COMPOUND_TYPES:
                boundary = child
                break

    start_byte = node.start_byte
    if (
        node.type == "arrow_function"
        and parent is not None
        and parent.type == "variable_declarator"
    ):
        start_byte = parent.start_byte
    if boundary is not None and boundary.start_byte > start_byte:
        return _normalized_signature(
            source[start_byte : boundary.start_byte].decode("utf-8", errors="replace")
        )

    text = _node_text(node, source)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return _normalized_signature(first_line)


def _strip_doc_text(text: str) -> str | None:
    stripped = text.strip()
    string_match = re.match(r"(?is)^[rubf]*(\"\"\"|'''|\"|')", stripped)
    if string_match:
        quote = string_match.group(1)
        body = stripped[string_match.end() :]
        if body.endswith(quote):
            body = body[: -len(quote)]
        return body.strip() or None

    if stripped.startswith(("/*", "//", "#")):
        stripped = re.sub(r"^/\*+|\*/$", "", stripped).strip()
        lines = []
        for line in stripped.splitlines():
            cleaned = re.sub(r"^\s*(?://+|#+|\*)\s?", "", line).rstrip()
            lines.append(cleaned)
        return "\n".join(lines).strip() or None
    return None


def _doc(
    node: Any,
    source: bytes,
    role: str,
    previous_sibling: Any | None,
) -> str | None:
    if role not in {"definition", "file"}:
        return None

    body = node.child_by_field_name("body")
    container = body if body is not None else node
    children = tuple(container.named_children)
    if children:
        first = children[0]
        if first.type == "expression_statement":
            first_children = tuple(first.named_children)
            first = first_children[0] if first_children else first
        if first.type in {"concatenated_string", "string", "string_literal"}:
            doc = _strip_doc_text(_node_text(first, source))
            if doc:
                return doc

    if previous_sibling is not None and previous_sibling.type in {
        "block_comment",
        "comment",
        "line_comment",
    }:
        gap = source[previous_sibling.end_byte : node.start_byte]
        if gap.strip() == b"" and gap.count(b"\n") <= 2:
            return _strip_doc_text(_node_text(previous_sibling, source))
    return None


class CodeParser:
    parser_version = "tree-sitter-language-pack-1.13"

    def __init__(self) -> None:
        # Parser instances are not shared across worker threads. Keeping one parser per
        # language and thread also avoids repeatedly unloading grammar capsules between
        # files, which is unsafe in some py-tree-sitter/CPython combinations.
        self._local = threading.local()

    def _parser_for(self, language: str) -> Any:
        parsers = getattr(self._local, "parsers", None)
        if parsers is None:
            parsers = {}
            self._local.parsers = parsers
        if language not in parsers:
            parsers[language] = get_parser(language)
        return parsers[language]

    def parse(self, relative_path: str, content: str, blob_hash: str | None = None) -> ParsedFile:
        language = language_for_path(relative_path) or "text"
        source = content.encode("utf-8")
        content_hash = "sha256:" + hashlib.sha256(source).hexdigest()
        parsed = ParsedFile(
            path=relative_path,
            language=language,
            content=content,
            content_hash=content_hash,
            blob_hash=blob_hash or content_hash,
            imports=self._imports(language, content),
        )
        if language not in STRUCTURED_LANGUAGES:
            parsed.units = self._fallback_units(source)
            return parsed
        try:
            tree = self._parser_for(language).parse(source)
            root = tree.root_node
            if root.has_error:
                parsed.parse_error = "tree-sitter reported syntax errors; partial AST retained"
            module = self._module_name(relative_path)
            quality = "partial" if root.has_error else "complete"
            root_path = f"{root.type}[0]"
            self._walk(
                root,
                source,
                language,
                module,
                parsed.symbols,
                None,
                parsed.units,
                root_path,
                None,
                quality,
                (),
                None,
                None,
            )
            for symbol in parsed.symbols:
                excluded = {symbol.name, *symbol.calls}
                symbol.references = [
                    name for name in dict.fromkeys(symbol.references) if name not in excluded
                ][:120]
        except Exception as exc:  # A missing grammar must not block the whole repository.
            parsed.parse_error = f"parser unavailable: {type(exc).__name__}: {exc}"
            parsed.units = self._fallback_units(source)
        return parsed

    def _walk(
        self,
        node: Any,
        source: bytes,
        language: str,
        parent_qualified: str,
        symbols: list[ParsedSymbol],
        current_symbol: ParsedSymbol | None,
        units: list[ParsedCodeUnitIR],
        structural_path: str,
        parent_structural_path: str | None,
        quality: str,
        active_units: tuple[ParsedCodeUnitIR, ...],
        parent: Any | None,
        previous_sibling: Any | None,
    ) -> None:
        # Materialize children before requesting named fields. With the CPython 3.13
        # bindings, interleaving TreeCursor-backed ``named_children`` iteration and
        # ``child_by_field_name`` can invalidate the cursor for larger syntax trees.
        children = tuple(node.named_children)
        definitions = DEFINITION_TYPES.get(language, {})
        next_symbol = current_symbol
        if node.type in definitions:
            name = _definition_name(node, source)
            if name:
                qualified = f"{parent_qualified}.{name}" if parent_qualified else name
                next_symbol = ParsedSymbol(
                    name=name,
                    qualified_name=qualified,
                    kind=definitions[node.type],
                    start_line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    content=_node_text(node, source),
                )
                symbols.append(next_symbol)
                parent_qualified = qualified
        elif node.type in CALL_TYPES and current_symbol is not None:
            name = _call_name(node, source)
            if name and name != current_symbol.name and name not in current_symbol.calls:
                current_symbol.calls.append(name)
        elif node.type in REFERENCE_TYPES and current_symbol is not None:
            name = _node_text(node, source).strip()
            if (
                re.fullmatch(r"[A-Za-z_$][\w$]*", name)
                and name != current_symbol.name
                and name not in current_symbol.references
            ):
                current_symbol.references.append(name)

        role = _unit_role(node.type, is_root=parent is None)
        next_parent_structural_path = parent_structural_path
        next_active_units = active_units
        if role is not None:
            unit = ParsedCodeUnitIR(
                structural_path=structural_path,
                node_type=node.type,
                role=role,
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                text=_node_text(node, source),
                parent_structural_path=parent_structural_path,
                signature=_signature(node, source, role, parent),
                doc=_doc(node, source, role, previous_sibling),
                quality=quality,
                parser_version=self.parser_version,
            )
            if role == "definition" and parent is not None and parent.type == "variable_declarator":
                assigned_name = _definition_name(parent, source)
                if assigned_name:
                    unit.identifiers.append(assigned_name)
            units.append(unit)
            next_parent_structural_path = structural_path
            next_active_units = (*active_units, unit)

        if node.type in IR_IDENTIFIER_TYPES:
            for identifier in _unique_identifiers(_node_text(node, source)):
                for unit in next_active_units:
                    if len(unit.identifiers) >= 120:
                        continue
                    if identifier not in unit.identifiers:
                        unit.identifiers.append(identifier)

        sibling_ordinals: dict[str, int] = {}
        prior_child = None
        for child in children:
            ordinal = sibling_ordinals.get(child.type, 0)
            sibling_ordinals[child.type] = ordinal + 1
            child_path = f"{structural_path}/{child.type}[{ordinal}]"
            self._walk(
                child,
                source,
                language,
                parent_qualified,
                symbols,
                next_symbol,
                units,
                child_path,
                next_parent_structural_path,
                quality,
                next_active_units,
                node,
                prior_child,
            )
            prior_child = child

    def _fallback_units(self, source: bytes, max_lines: int = 200) -> list[ParsedCodeUnitIR]:
        lines = source.splitlines(keepends=True)
        if not lines:
            lines = [b""]

        units: list[ParsedCodeUnitIR] = []
        start_byte = 0
        start_line = 1
        for ordinal, offset in enumerate(range(0, len(lines), max_lines)):
            chunk = b"".join(lines[offset : offset + max_lines])
            end_byte = start_byte + len(chunk)
            end_line = start_line + chunk.count(b"\n")
            text = chunk.decode("utf-8", errors="replace")
            units.append(
                ParsedCodeUnitIR(
                    structural_path=f"text_chunk[{ordinal}]",
                    node_type="text_chunk",
                    role="fallback",
                    start_byte=start_byte,
                    end_byte=end_byte,
                    start_line=start_line,
                    end_line=end_line,
                    text=text,
                    parent_structural_path=None,
                    identifiers=_unique_identifiers(text)[:120],
                    quality="fallback",
                    parser_version=self.parser_version,
                )
            )
            start_byte = end_byte
            start_line = end_line
        return units

    def _imports(self, language: str, content: str) -> list[str]:
        imports: list[str] = []
        for pattern in IMPORT_PATTERNS.get(language, []):
            for match in pattern.finditer(content):
                value = match.group(1).strip()
                if value and value not in imports:
                    imports.append(value)
        return imports

    def _module_name(self, path: str) -> str:
        item = PurePosixPath(path)
        without_suffix = item.with_suffix("")
        parts = list(without_suffix.parts)
        if parts and parts[-1] in {"__init__", "index"}:
            parts.pop()
        return ".".join(parts)
