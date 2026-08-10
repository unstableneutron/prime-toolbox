"""Lazy Firecrawl Anydoc integration for structured document families."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Literal

AnydocFormat = Literal[
    "doc",
    "docx",
    "odt",
    "pdf",
    "ppt",
    "pptx",
    "rtf",
    "epub",
    "xlsx",
    "ods",
    "odp",
    "csv",
]

ANYDOC_FORMATS: dict[str, tuple[AnydocFormat, str]] = {
    ".doc": ("doc", "word"),
    ".docx": ("docx", "word"),
    ".docm": ("docx", "word"),
    ".ppt": ("ppt", "powerpoint"),
    ".pps": ("ppt", "powerpoint"),
    ".pot": ("ppt", "powerpoint"),
    ".pptx": ("pptx", "powerpoint"),
    ".pptm": ("pptx", "powerpoint"),
    ".ppsx": ("pptx", "powerpoint"),
    ".ppsm": ("pptx", "powerpoint"),
    ".xls": ("xlsx", "excel"),
    ".xlsx": ("xlsx", "excel"),
    ".xlsm": ("xlsx", "excel"),
    ".xlsb": ("xlsx", "excel"),
    ".odt": ("odt", "opendocument-text"),
    ".ods": ("ods", "opendocument-spreadsheet"),
    ".odp": ("odp", "opendocument-presentation"),
    ".rtf": ("rtf", "rtf"),
    ".epub": ("epub", "epub"),
    ".csv": ("csv", "csv"),
}


class DocumentConversionError(Exception):
    def __init__(
        self, message: str, *, category: str, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.category = category
        self.detail = detail or {}


def document_format(path: Path) -> tuple[AnydocFormat, str] | None:
    return ANYDOC_FORMATS.get(path.suffix.casefold())


def _category(error: Exception) -> tuple[str, dict[str, Any]]:
    name = type(error).__name__
    detail: dict[str, Any] = {"exception": name}
    part = getattr(error, "part", None)
    limit = getattr(error, "limit", None)
    if part is not None:
        detail["part"] = part
    if limit is not None:
        detail["limit"] = limit
    categories = {
        "MemoryError": "resource_limited",
        "OverflowError": "resource_limited",
        "UnsupportedError": "unsupported",
        "MalformedError": "malformed",
        "EncryptedError": "encrypted",
        "ResourceLimitError": "resource_limited",
        "MissingPartError": "malformed",
    }
    return categories.get(name, "conversion_failed"), detail


def convert_anydoc(data: bytes, path: Path) -> tuple[str, dict[str, Any]]:
    selected = document_format(path)
    if selected is None:
        raise DocumentConversionError(
            f"Unsupported structured extension: {path.suffix}", category="unsupported"
        )
    native_format, family = selected
    try:
        anydoc = importlib.import_module("anydoc")
    except Exception as exc:
        raise DocumentConversionError(
            "firecrawl-anydoc is unavailable; install the 'firecrawl-anydoc==0.1.7' "
            "native package for this document family",
            category="missing_dependency",
            detail={"dependency": "firecrawl-anydoc", "exception": type(exc).__name__},
        ) from exc
    try:
        markdown = anydoc.to_markdown_bytes(data, native_format)
    except Exception as exc:
        category, detail = _category(exc)
        raise DocumentConversionError(str(exc), category=category, detail=detail) from exc
    if not isinstance(markdown, str) or not markdown.strip():
        raise DocumentConversionError(
            "The converter returned no meaningful text", category="unsupported"
        )
    return markdown, {
        "status": "converted",
        "backend": "firecrawl-anydoc",
        "backend_version": "0.1.7",
        "native_format": native_format,
        "family": family,
    }


__all__ = [
    "ANYDOC_FORMATS",
    "DocumentConversionError",
    "convert_anydoc",
    "document_format",
]
