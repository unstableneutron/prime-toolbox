"""Lazy PDF Inspector integration with page-level OCR diagnostics."""

from __future__ import annotations

import importlib
from typing import Any

from .limits import ReadLimits


class PdfConversionError(Exception):
    def __init__(
        self, message: str, *, category: str, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.category = category
        self.detail = detail or {}


def _error_category(error: Exception) -> str:
    if isinstance(error, (MemoryError, OverflowError)):
        return "resource_limited"
    message = str(error).casefold()
    if "encrypt" in message or "password" in message:
        return "encrypted"
    if "memory" in message or "resource" in message or "limit" in message or "too large" in message:
        return "resource_limited"
    if "unsupported" in message:
        return "unsupported"
    return "malformed"


def _ranges(pages: list[int], maximum: int) -> tuple[list[dict[str, int]], bool]:
    result: list[dict[str, int]] = []
    for page in sorted(set(pages)):
        if result and page == result[-1]["end"] + 1:
            result[-1]["end"] = page
        else:
            if len(result) >= maximum:
                return result, True
            result.append({"start": page, "end": page})
    return result, False


def convert_pdf(data: bytes, limits: ReadLimits) -> tuple[str, dict[str, Any], list[str]]:
    try:
        inspector = importlib.import_module("pdf_inspector")
    except Exception as exc:
        raise PdfConversionError(
            "pdf-inspector is unavailable; install 'pdf-inspector==0.2.6' for local PDF analysis",
            category="missing_dependency",
            detail={"dependency": "pdf-inspector", "exception": type(exc).__name__},
        ) from exc
    try:
        result = inspector.detect_pdf_bytes(data)
        pages_result = inspector.extract_pages_markdown_bytes(data)
    except Exception as exc:
        raise PdfConversionError(
            str(exc),
            category=_error_category(exc),
            detail={"exception": type(exc).__name__},
        ) from exc

    # pdf-inspector 0.2.6 exposes these fields at runtime, but its bundled
    # hand-written .pyi currently omits them. ``getattr`` also makes this
    # integration degrade safely if a platform supplies older native bits.
    all_reasons = list(getattr(pages_result, "ocr_reasons_by_page", []))
    reasons = [
        {"page": item.page, "reasons": list(item.reasons)}
        for item in all_reasons[: limits.max_pdf_page_diagnostics]
    ]
    page_diagnostics: list[dict[str, Any]] = []
    rendered: list[str] = []
    for page in pages_result.pages:
        number = int(page.page) + 1
        markdown = page.markdown if isinstance(page.markdown, str) else ""
        ocr_reason = getattr(page, "ocr_reason", None)
        if len(page_diagnostics) < limits.max_pdf_page_diagnostics:
            page_diagnostics.append(
                {
                    "page": number,
                    "needs_ocr": bool(page.needs_ocr),
                    "ocr_reason": ocr_reason,
                    "extracted_characters": len(markdown),
                }
            )
        rendered.append(f"## Page {number}")
        if markdown.strip():
            rendered.append(markdown.rstrip())
            if page.needs_ocr:
                rendered.append(
                    f"[Page {number} has locally extracted text but is also flagged for OCR: "
                    f"{ocr_reason or 'unreliable text layer'}]"
                )
        else:
            reason = ocr_reason or "no reliable local text layer"
            rendered.append(
                f"[Page {number} was not extracted locally and requires "
                f"Prime's vision/OCR path: {reason}]"
            )
        rendered.append("")

    ocr_pages = list(pages_result.pages_needing_ocr)
    ocr_ranges, ranges_truncated = _ranges(ocr_pages, limits.max_pdf_page_diagnostics)
    table_pages = list(pages_result.pages_with_tables)
    column_pages = list(pages_result.pages_with_columns)
    has_encoding_issues = bool(getattr(result, "has_encoding_issues", False)) or any(
        "encoding" in reason.casefold() for item in all_reasons for reason in item.reasons
    )
    diagnostics: dict[str, Any] = {
        "classification": result.pdf_type,
        "confidence": float(result.confidence),
        "page_count": int(result.page_count),
        "title": result.title,
        "processing_time_ms": int(result.processing_time_ms),
        "is_complex_layout": bool(pages_result.is_complex),
        "pages_with_tables": table_pages[: limits.max_pdf_page_diagnostics],
        "pages_with_tables_total": len(table_pages),
        "pages_with_columns": column_pages[: limits.max_pdf_page_diagnostics],
        "pages_with_columns_total": len(column_pages),
        "has_encoding_issues": has_encoding_issues,
        "pages_needing_ocr": ocr_pages[: limits.max_pdf_page_diagnostics],
        "pages_needing_ocr_total": len(ocr_pages),
        "pages_needing_ocr_ranges": ocr_ranges,
        "ocr_reasons_by_page": reasons,
        "page_diagnostics": page_diagnostics,
        "diagnostics_truncated": (
            len(ocr_pages) > limits.max_pdf_page_diagnostics
            or len(pages_result.pages) > limits.max_pdf_page_diagnostics
            or len(all_reasons) > limits.max_pdf_page_diagnostics
            or len(table_pages) > limits.max_pdf_page_diagnostics
            or len(column_pages) > limits.max_pdf_page_diagnostics
            or ranges_truncated
        ),
    }
    warnings: list[str] = []
    if ocr_pages:
        warnings.append(
            "PDF extraction is incomplete: pages listed in pdf.pages_needing_ocr require "
            "Prime's vision/OCR path. Hosted OCR is not performed by guarded_file_ops."
        )
    if has_encoding_issues:
        warnings.append(
            "PDF Inspector detected broken or unreliable font encoding; "
            "affected pages may need OCR."
        )
    if diagnostics["diagnostics_truncated"]:
        warnings.append("PDF page diagnostics reached the configured metadata ceiling.")
    return "\n".join(rendered).rstrip() + "\n", diagnostics, warnings


__all__ = ["PdfConversionError", "convert_pdf"]
