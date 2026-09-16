import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from evidence_rag.documents.adapters.structured import (
    DeterministicPDFParserProvider,
    DocumentSecurityError,
    PDFDeterministicPage,
    PDFLayoutBlock,
    PDFParseRequest,
    PDFProviderStatus,
    StructuredDocumentAdapter,
)
from evidence_rag.parser import CodeParser

PDF_FIXTURES = Path(__file__).parent / "fixtures" / "document_parser_v2"


def _fixture_pages(name: str, *page_numbers: int) -> dict[int, PDFDeterministicPage]:
    payload = json.loads((PDF_FIXTURES / "ocr_payloads.json").read_text(encoding="utf-8"))[name]
    selected = set(page_numbers) or {int(value) for value in payload}
    return {
        int(page_number): PDFDeterministicPage(
            ocr_text=item["ocr_text"],
            status=PDFProviderStatus(item["status"]),
            blocks=tuple(
                PDFLayoutBlock(
                    text=block["text"],
                    bbox=tuple(block["bbox"]),
                    block_type=block["block_type"],
                    confidence=block["confidence"],
                )
                for block in item["blocks"]
            ),
        )
        for page_number, item in payload.items()
        if int(page_number) in selected
    }


def test_python_parser_extracts_symbols_and_calls() -> None:
    content = """
def clamp(value):
    return max(0, value)

class Ranker:
    def rank(self, value):
        return clamp(value)
""".strip()
    parsed = CodeParser().parse("src/ranking.py", content)

    names = {symbol.qualified_name for symbol in parsed.symbols}
    assert "src.ranking.clamp" in names
    assert "src.ranking.Ranker" in names
    assert "src.ranking.Ranker.rank" in names
    rank = next(symbol for symbol in parsed.symbols if symbol.name == "rank")
    assert rank.start_line == 5
    assert "clamp" in rank.calls
    assert parsed.parse_error is None


def test_python_parser_separates_calls_from_non_call_references() -> None:
    content = """
class SearchConfig:
    pass

def build_default():
    return SearchConfig()

def describe(config_type=SearchConfig):
    return config_type.__name__
""".strip()
    parsed = CodeParser().parse("src/config.py", content)
    build = next(symbol for symbol in parsed.symbols if symbol.name == "build_default")
    describe = next(symbol for symbol in parsed.symbols if symbol.name == "describe")

    assert "SearchConfig" in build.calls
    assert "SearchConfig" not in build.references
    assert "SearchConfig" in describe.references
    assert "SearchConfig" not in describe.calls


def test_text_file_remains_a_searchable_file_without_symbols() -> None:
    parsed = CodeParser().parse("README.md", "# Architecture\nEvidence first.")
    assert parsed.language == "markdown"
    assert parsed.symbols == []
    assert parsed.content_hash.startswith("sha256:")


def test_large_nested_module_does_not_invalidate_tree_cursor() -> None:
    module = Path(__file__).resolve().parents[1] / "src" / "evidence_rag" / "ingestion.py"
    parsed = CodeParser().parse("src/evidence_rag/ingestion.py", module.read_text())
    assert parsed.parse_error is None
    assert any(symbol.qualified_name.endswith("IngestionService.run") for symbol in parsed.symbols)


def test_parser_reuses_grammar_safely_across_many_files() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "evidence_rag"
    parser = CodeParser()
    parsed = [
        parser.parse(path.name, path.read_text()) for path in sorted(source_root.glob("*.py"))
    ]
    assert len(parsed) >= 10
    assert all(item.parse_error is None for item in parsed)


def test_pdf_parser_v2_fixtures_are_fixed_and_native_layout_has_real_bboxes() -> None:
    manifest = json.loads((PDF_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        observed = "sha256:" + hashlib.sha256((PDF_FIXTURES / name).read_bytes()).hexdigest()
        assert observed == expected

    parsed = StructuredDocumentAdapter().parse(PDF_FIXTURES / "layout.pdf")
    page = parsed.pages[0]
    metadata = page["metadata"]
    assert parsed.source_uri.startswith("document-source://sha256/")
    assert str(PDF_FIXTURES.resolve()) not in json.dumps(
        {
            "source_uri": parsed.source_uri,
            "pages": parsed.pages,
            "sections": parsed.sections,
            "metadata": parsed.metadata,
        },
        default=str,
    )
    assert metadata["page_identity"].startswith("sha256:")
    assert metadata["scan_detected"] is False
    assert metadata["ocr_status"] == "not_required"
    assert len(metadata["blocks"]) >= 4
    headers = [item for item in metadata["blocks"] if item["block_type"] == "header"]
    assert {item["text"] for item in headers} >= {"Native Layout Evaluation", "Results"}
    assert all(
        0 <= item["bbox"][0] < item["bbox"][2] <= metadata["width"]
        and 0 <= item["bbox"][1] < item["bbox"][3] <= metadata["height"]
        for item in metadata["blocks"]
    )
    assert all(
        item["bbox"] != [0.0, 0.0, metadata["width"], metadata["height"]] for item in headers
    )


def test_pdf_parser_v2_default_is_unavailable_without_fabricated_ocr() -> None:
    parsed = StructuredDocumentAdapter().parse(PDF_FIXTURES / "scanned.pdf")
    page = parsed.pages[0]
    assert parsed.content == ""
    assert page["content"] == ""
    assert page["metadata"]["scan_detected"] is True
    assert page["metadata"]["ocr_status"] == "unavailable"
    assert page["metadata"]["blocks"] == []
    assert page["metadata"]["parser_provenance"]["provider_status"] == "unavailable"


def test_pdf_parser_v2_injected_provider_is_verifiable_and_partial_is_page_honest() -> None:
    scanned = StructuredDocumentAdapter(
        DeterministicPDFParserProvider(_fixture_pages("scanned.pdf"))
    ).parse(PDF_FIXTURES / "scanned.pdf")
    page = scanned.pages[0]
    assert "OCR Recall reaches 88.4 percent" in scanned.content
    assert page["metadata"]["ocr_status"] == "available"
    assert page["metadata"]["text_source"] == "ocr"
    assert page["metadata"]["layout_payload_sha256"].startswith("sha256:")
    assert page["metadata"]["parser_provenance"]["input_sha256"].startswith("sha256:")
    assert page["metadata"]["parser_provenance"]["output_sha256"].startswith("sha256:")
    assert all(item["source"] == "ocr" for item in page["metadata"]["blocks"])

    mixed = StructuredDocumentAdapter(
        DeterministicPDFParserProvider(_fixture_pages("mixed.pdf", 2))
    ).parse(PDF_FIXTURES / "mixed.pdf")
    assert [item["metadata"]["ocr_status"] for item in mixed.pages] == [
        "not_required",
        "available",
        "unavailable",
    ]
    assert mixed.pages[1]["metadata"]["parser_provenance"]["provider_status"] == "partial"
    assert mixed.pages[2]["metadata"]["parser_provenance"]["provider_status"] == "partial"
    assert "Mixed OCR accuracy is 91 percent" in mixed.content
    assert "Third page needs local OCR fallback" not in mixed.content


def test_pdf_parser_v2_provider_error_falls_back_without_leaking_exception() -> None:
    class ErrorProvider:
        provider_name = "local-error"
        provider_version = "error-v1"

        def parse(self, request: PDFParseRequest) -> object:
            raise RuntimeError(f"private failure for {request.pdf_bytes[:8]!r}")

    parsed = StructuredDocumentAdapter(ErrorProvider()).parse(PDF_FIXTURES / "scanned.pdf")
    metadata = parsed.pages[0]["metadata"]
    assert parsed.content == ""
    assert metadata["ocr_status"] == "error"
    assert metadata["parser_provenance"]["provider_status"] == "error"
    assert "private failure" not in json.dumps(metadata)


def test_pdf_parser_v2_rejects_active_binary_protected_and_invalid_inputs(
    tmp_path: Path,
) -> None:
    for kind in ("script", "attachment"):
        writer = PdfWriter()
        writer.append(PDF_FIXTURES / "layout.pdf")
        if kind == "script":
            writer.add_js("app.alert('not allowed')")
        else:
            writer.add_attachment("payload.bin", b"\x00\x01\x02")
        unsafe = tmp_path / f"{kind}.pdf"
        with unsafe.open("wb") as stream:
            writer.write(stream)
        with pytest.raises(DocumentSecurityError):
            StructuredDocumentAdapter().parse(unsafe)

    protected = _fixture_pages("scanned.pdf")
    protected[1] = PDFDeterministicPage(
        ocr_text="/Users/example/private/report.pdf",
        blocks=(
            PDFLayoutBlock(
                text="/Users/example/private/report.pdf",
                bbox=(94.0, 126.0, 360.0, 158.0),
            ),
        ),
    )
    with pytest.raises(DocumentSecurityError):
        StructuredDocumentAdapter(DeterministicPDFParserProvider(protected)).parse(
            PDF_FIXTURES / "scanned.pdf"
        )

    invalid = tmp_path / "binary.pdf"
    invalid.write_bytes(b"\x7fELF\x00not-a-pdf")
    with pytest.raises(ValueError, match="not a valid PDF"):
        StructuredDocumentAdapter().parse(invalid)
