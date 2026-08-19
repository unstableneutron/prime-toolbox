from __future__ import annotations

import base64
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from guarded_file_ops import FileOps, FileOpsPolicy, ReadLimits

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class PdfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = FileOps(policy=FileOpsPolicy(use_fff=False))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_pdf(self, name: str, page_kinds: list[str], *, encrypted: bool = False) -> Path:
        path = self.root / name
        encryption = StandardEncryption("secret") if encrypted else None
        document = canvas.Canvas(str(path), encrypt=encryption)
        image = ImageReader(io.BytesIO(PNG)) if "image" in page_kinds else None
        for kind in page_kinds:
            if kind == "text":
                document.drawString(72, 720, "Locally extractable PDF text")
            elif kind == "image":
                assert image is not None
                document.drawImage(image, 72, 400, width=400, height=300)
            document.showPage()
        document.save()
        return path

    def test_text_mixed_and_scanned_pdfs_have_page_level_coverage(self):
        text = self.files.read(self.make_pdf("text.pdf", ["text"]))
        self.assertEqual(text.status, "ok", text)
        self.assertEqual(text.pdf["page_count"], 1)
        self.assertIn("Locally extractable", text.content)
        self.assertEqual(text.conversion["backend"], "pdf-inspector")

        scanned = self.files.read(self.make_pdf("scanned.pdf", ["image"]))
        self.assertIn(scanned.pdf["classification"], {"scanned", "image_based", "mixed"})
        self.assertEqual(scanned.pdf["pages_needing_ocr"], [1])
        self.assertIn("vision/OCR", scanned.content)

        mixed = self.files.read(
            self.make_pdf("mixed.pdf", ["text", "image"]),
        )
        self.assertEqual(mixed.pdf["page_count"], 2)
        self.assertIn(2, mixed.pdf["pages_needing_ocr"])
        self.assertIn("Locally extractable", mixed.content)
        self.assertTrue(mixed.warnings)

    def test_malformed_and_encrypted_pdfs_are_categorized(self):
        malformed = self.root / "malformed.pdf"
        malformed.write_bytes(b"%PDF-1.7\nbroken")
        result = self.files.read(malformed)
        self.assertEqual(result.category, "malformed")

        encrypted = self.files.read(
            self.make_pdf("encrypted.pdf", ["text"], encrypted=True),
        )
        self.assertEqual(encrypted.category, "encrypted")

    def test_pdf_output_uses_normal_line_byte_and_character_budgets(self):
        path = self.make_pdf("bounded.pdf", ["text", "text", "text"])
        result = self.files.read(
            path,
            limits=ReadLimits(max_lines=2, max_bytes=100, max_line_characters=30),
        )
        self.assertLessEqual(len(result.content.splitlines()), 2)
        self.assertLessEqual(len(result.content.encode()), 100)
        self.assertEqual(result.next_offset, 3)

    def test_structured_pdf_diagnostics_preserve_layout_and_ocr_reasons(self):
        path = self.root / "fake.pdf"
        path.write_bytes(b"%PDF-1.7 fake")
        processed = types.SimpleNamespace(
            pdf_type="mixed",
            confidence=0.91,
            page_count=2,
            title="Report",
            processing_time_ms=7,
        )
        pages = types.SimpleNamespace(
            pages=[
                types.SimpleNamespace(
                    page=0, markdown="| A | B |", needs_ocr=False, ocr_reason=None
                ),
                types.SimpleNamespace(page=1, markdown="", needs_ocr=True, ocr_reason="empty_text"),
            ],
            pages_with_tables=[1],
            pages_with_columns=[1],
            pages_needing_ocr=[2],
            ocr_reasons_by_page=[
                types.SimpleNamespace(page=2, reasons=["empty_text", "encoding_issues"])
            ],
            is_complex=True,
        )
        fake = types.SimpleNamespace(
            detect_pdf_bytes=lambda data: processed,
            extract_pages_markdown_bytes=lambda data: pages,
        )
        with mock.patch("guarded_file_ops.pdf.importlib.import_module", return_value=fake):
            result = self.files.read(path)
        self.assertEqual(result.pdf["classification"], "mixed")
        self.assertEqual(result.pdf["confidence"], 0.91)
        self.assertEqual(result.pdf["pages_with_tables"], [1])
        self.assertEqual(result.pdf["pages_with_columns"], [1])
        self.assertEqual(
            result.pdf["ocr_reasons_by_page"][0]["reasons"],
            ["empty_text", "encoding_issues"],
        )
        self.assertIn("Page 2 was not extracted locally", result.content)

    def test_missing_pdf_dependency_does_not_affect_text(self):
        path = self.root / "missing.pdf"
        path.write_bytes(b"%PDF-1.7 fake")
        with mock.patch(
            "guarded_file_ops.pdf.importlib.import_module", side_effect=ImportError("missing")
        ):
            result = self.files.read(path)
        self.assertEqual(result.category, "missing_dependency")
        self.assertEqual(result.conversion["dependency"], "pdf-inspector")


if __name__ == "__main__":
    unittest.main()
