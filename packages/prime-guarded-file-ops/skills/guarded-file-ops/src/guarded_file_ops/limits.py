"""Central resource and response limits for :mod:`guarded_file_ops`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadLimits:
    """Ceilings applied before and after parsing.

    ``max_lines`` and ``max_bytes`` apply to the returned content window.
    ``max_line_characters`` is applied while a source line is being parsed, so
    a pathological single line is never accumulated in memory. Structured
    documents are allowed to buffer their source only up to
    ``max_document_bytes`` because the native converter APIs are whole-file
    APIs. Suggestions are always capped by ``max_suggestions``.
    """

    max_lines: int = 2_000
    max_bytes: int = 50 * 1024
    max_line_characters: int = 2_000
    max_document_bytes: int = 50 * 1024 * 1024
    max_suggestions: int = 5
    max_sibling_entries: int = 10_000
    max_notebook_output_characters: int = 20_000
    max_pdf_page_diagnostics: int = 2_000
    fff_timeout_seconds: float = 3.0

    def validate(self) -> ReadLimits:
        """Return this instance after validating every ceiling."""
        integer_fields = (
            "max_lines",
            "max_bytes",
            "max_line_characters",
            "max_document_bytes",
            "max_suggestions",
            "max_sibling_entries",
            "max_notebook_output_characters",
            "max_pdf_page_diagnostics",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.fff_timeout_seconds <= 0:
            raise ValueError("fff_timeout_seconds must be positive")
        return self


DEFAULT_LIMITS = ReadLimits()

__all__ = ["DEFAULT_LIMITS", "ReadLimits"]
