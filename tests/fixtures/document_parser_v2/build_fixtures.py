from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent
PAGE_WIDTH, PAGE_HEIGHT = letter


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _scanned_image(title: str, lines: tuple[str, ...]) -> Image.Image:
    image = Image.new("RGB", (1000, 1200), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=42)
    body_font = ImageFont.load_default(size=27)
    draw.rectangle((45, 55, 955, 1145), outline="#606060", width=3)
    draw.text((80, 100), title, fill="black", font=title_font)
    for index, line in enumerate(lines):
        draw.text((80, 240 + index * 100), line, fill="black", font=body_font)
    return image


def _draw_scanned_page(pdf: canvas.Canvas, image: Image.Image) -> None:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    buffer.seek(0)
    pdf.drawImage(
        ImageReader(buffer),
        54,
        72,
        width=504,
        height=648,
        preserveAspectRatio=False,
        mask="auto",
    )


def _layout_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    pdf.setTitle("Native Layout Fixture")
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(54, 730, "Native Layout Evaluation")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(54, 698, "This vector-text page verifies page identity and block geometry.")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(54, 635, "Results")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(54, 605, "Claim: Native layout Recall reaches 84.2 percent.")
    pdf.drawString(54, 580, "All text is authored in the PDF content stream.")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(558, 36, "Page 1")
    pdf.save()


def _scanned_pdf(path: Path) -> None:
    image = _scanned_image(
        "Scanned Evaluation",
        (
            "Claim: OCR Recall reaches 88.4 percent.",
            "Dataset: Local Fixture",
        ),
    )
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    pdf.setTitle("Scanned Fixture")
    _draw_scanned_page(pdf, image)
    pdf.save()


def _mixed_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    pdf.setTitle("Mixed Fixture")
    pdf.setFont("Helvetica-Bold", 21)
    pdf.drawString(54, 730, "Mixed Evidence Report")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(54, 690, "Native page evidence remains searchable without OCR.")
    pdf.drawString(54, 665, "Claim: Native precision reaches 79.5 percent.")
    pdf.showPage()
    _draw_scanned_page(
        pdf,
        _scanned_image(
            "Mixed Scan Page Two",
            (
                "Finding: Mixed OCR accuracy is 91 percent.",
                "Provider payload: deterministic local fixture",
            ),
        ),
    )
    pdf.showPage()
    _draw_scanned_page(
        pdf,
        _scanned_image(
            "Mixed Scan Page Three",
            (
                "Limit: Third page needs local OCR fallback.",
                "No network provider is configured.",
            ),
        ),
    )
    pdf.save()


def _ocr_payloads(path: Path) -> None:
    payload = {
        "scanned.pdf": {
            "1": {
                "status": "available",
                "ocr_text": (
                    "# Scanned Evaluation\n\n"
                    "Claim: OCR Recall reaches 88.4 percent.\n\n"
                    "Dataset: Local Fixture"
                ),
                "blocks": [
                    {
                        "text": "Scanned Evaluation",
                        "bbox": [94.0, 126.0, 360.0, 158.0],
                        "block_type": "header",
                        "confidence": 0.99,
                    },
                    {
                        "text": "Claim: OCR Recall reaches 88.4 percent.",
                        "bbox": [94.0, 198.0, 430.0, 220.0],
                        "block_type": "body",
                        "confidence": 0.98,
                    },
                    {
                        "text": "Dataset: Local Fixture",
                        "bbox": [94.0, 252.0, 290.0, 274.0],
                        "block_type": "body",
                        "confidence": 0.98,
                    },
                ],
            }
        },
        "mixed.pdf": {
            "2": {
                "status": "available",
                "ocr_text": (
                    "# Mixed Scan Page Two\n\n"
                    "Finding: Mixed OCR accuracy is 91 percent.\n\n"
                    "Provider payload: deterministic local fixture"
                ),
                "blocks": [
                    {
                        "text": "Mixed Scan Page Two",
                        "bbox": [94.0, 126.0, 390.0, 158.0],
                        "block_type": "header",
                        "confidence": 0.99,
                    },
                    {
                        "text": "Finding: Mixed OCR accuracy is 91 percent.",
                        "bbox": [94.0, 198.0, 440.0, 220.0],
                        "block_type": "body",
                        "confidence": 0.98,
                    },
                ],
            },
            "3": {
                "status": "available",
                "ocr_text": (
                    "# Mixed Scan Page Three\n\n"
                    "Limit: Third page needs local OCR fallback.\n\n"
                    "No network provider is configured."
                ),
                "blocks": [
                    {
                        "text": "Mixed Scan Page Three",
                        "bbox": [94.0, 126.0, 410.0, 158.0],
                        "block_type": "header",
                        "confidence": 0.99,
                    },
                    {
                        "text": "Limit: Third page needs local OCR fallback.",
                        "bbox": [94.0, 198.0, 440.0, 220.0],
                        "block_type": "body",
                        "confidence": 0.98,
                    },
                ],
            },
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    _layout_pdf(ROOT / "layout.pdf")
    _scanned_pdf(ROOT / "scanned.pdf")
    _mixed_pdf(ROOT / "mixed.pdf")
    _ocr_payloads(ROOT / "ocr_payloads.json")
    files = ("layout.pdf", "scanned.pdf", "mixed.pdf", "ocr_payloads.json")
    manifest = {
        "fixture_version": "document-parser-v2",
        "files": {name: _sha256(ROOT / name) for name in files},
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
