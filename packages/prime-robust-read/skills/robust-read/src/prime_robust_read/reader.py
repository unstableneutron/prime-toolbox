"""Primary format-aware reader."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .documents import DocumentConversionError, convert_anydoc, document_format
from .formatting import Page, SourceLine, iter_string_lines, paginate_lines
from .ledger import DEFAULT_LEDGER, FileIdentity, ReadLedger
from .limits import DEFAULT_LIMITS, ReadLimits
from .notebook import NotebookError, render_notebook
from .paths import (
    NonRegularFileError,
    PathResolutionError,
    ResolvedPath,
    open_verified_regular,
    resolve_path,
)
from .pdf import PdfConversionError, convert_pdf
from .text import InvalidEncodingError, image_format, iter_utf8_lines, pread_prefix
from .types import ReadResult


def _error(
    requested: str,
    message: str,
    *,
    category: str,
    canonical: str | None = None,
    format_name: str = "unknown",
    recovery: dict[str, Any] | None = None,
    suggestions: list[str] | None = None,
    warnings: list[str] | None = None,
    conversion: dict[str, Any] | None = None,
) -> ReadResult:
    return ReadResult(
        status="error",
        category=category,
        message=message,
        requested_path=requested,
        canonical_path=canonical,
        format=format_name,
        kind=None,
        content="",
        start_offset=None,
        end_offset=None,
        next_offset=None,
        total_lines=None,
        truncated=False,
        truncated_by=[],
        recovery=recovery or {"method": "none"},
        suggestions=suggestions or [],
        warnings=warnings or [],
        conversion=conversion or {"status": "not_attempted"},
        pdf=None,
        metadata={},
        repeated=False,
        unchanged=False,
        changed_since_last_read=False,
    )


def _identity_now(fd: int) -> FileIdentity:
    return FileIdentity.from_stat(os.fstat(fd))


def _base_result(
    resolved: ResolvedPath,
    *,
    format_name: str,
    page: Page,
    identity: FileIdentity | None,
    conversion: dict[str, Any],
    pdf: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    ledger: ReadLedger,
    additional_warnings: list[str] | None = None,
) -> ReadResult:
    canonical = str(resolved.canonical)
    previous = ledger.get(canonical) if identity is not None else None
    repeated = bool(previous and previous.identity == identity)
    changed = bool(previous and previous.identity != identity)
    complete = page.next_offset is None and not page.truncated
    if identity is not None:
        ledger.record(canonical, identity, complete=complete)

    if page.end_offset is None:
        status = "empty" if page.total_lines == 0 and page.start_offset == 1 else "eof"
        message = (
            "The file is empty."
            if status == "empty"
            else f"Offset {page.start_offset} is at or beyond EOF"
            + (f"; the file has {page.total_lines} lines." if page.total_lines is not None else ".")
        )
    elif repeated:
        status = "unchanged"
        message = "This canonical file is unchanged since its last successful read in this ledger."
    else:
        status = "ok"
        message = None

    warnings = list(dict.fromkeys(resolved.warnings + page.warnings + (additional_warnings or [])))
    file_metadata = metadata.copy() if metadata else {}
    if identity is not None:
        file_metadata.update(
            {
                "identity": identity.as_dict(),
                "file_size": identity.size,
                "offset_convention": "1-based source lines",
            }
        )
    return ReadResult(
        status=status,
        category=None,
        message=message,
        requested_path=resolved.requested,
        canonical_path=canonical,
        format=format_name,
        kind=resolved.kind,
        content=page.content,
        start_offset=page.start_offset,
        end_offset=page.end_offset,
        next_offset=page.next_offset,
        total_lines=page.total_lines,
        truncated=page.truncated,
        truncated_by=page.truncated_by,
        recovery=resolved.recovery,
        suggestions=[],
        warnings=warnings,
        conversion=conversion,
        pdf=pdf,
        metadata=file_metadata,
        repeated=repeated,
        unchanged=repeated,
        changed_since_last_read=changed,
    )


def _read_all_bounded(fd: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= maximum:
        chunk = os.read(fd, min(64 * 1024, maximum + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > maximum:
        raise OverflowError(f"source is {total:,}+ bytes; limit is {maximum:,} bytes")
    return b"".join(chunks)


@dataclass(slots=True)
class _DirectoryScan:
    truncated: bool = False


def _directory_lines(path: Path, limits: ReadLimits, scan: _DirectoryScan):
    number = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                number += 1
                if number > limits.max_sibling_entries:
                    scan.truncated = True
                    return
                try:
                    if entry.is_dir(follow_symlinks=False):
                        suffix = "/"
                    elif entry.is_symlink():
                        suffix = "@"
                    elif not entry.is_file(follow_symlinks=False):
                        suffix = " [special]"
                    else:
                        suffix = ""
                except OSError:
                    suffix = " [unavailable]"
                name = entry.name.replace("\r", "\\r").replace("\n", "\\n") + suffix
                yield SourceLine(
                    number,
                    name[: limits.max_line_characters],
                    len(name) > limits.max_line_characters,
                )
    except OSError as exc:
        raise PathResolutionError(str(exc), category="directory_read_failed") from exc


def _read_directory(
    resolved: ResolvedPath,
    *,
    offset: int,
    limit: int,
    limits: ReadLimits,
    ledger: ReadLedger,
) -> ReadResult:
    scan = _DirectoryScan()
    page = paginate_lines(
        _directory_lines(resolved.canonical, limits, scan),
        offset=offset,
        limit=limit,
        limits=limits,
    )
    if page.next_offset is None and scan.truncated:
        page.truncated = True
        page.truncated_by.append("directory_entries")
        page.next_offset = limits.max_sibling_entries + 1
        page.total_lines = None
        page.warnings.append("Directory listing reached the bounded scan ceiling.")
    return _base_result(
        resolved,
        format_name="directory",
        page=page,
        identity=None,
        conversion={"status": "not_applicable"},
        pdf=None,
        metadata={"offset_convention": "1-based directory entries in filesystem order"},
        ledger=ledger,
    )


def _structured_page(text: str, *, offset: int, limit: int, limits: ReadLimits) -> Page:
    return paginate_lines(
        iter_string_lines(text, limits.max_line_characters),
        offset=offset,
        limit=limit,
        limits=limits,
    )


def read(
    path: str | os.PathLike[str],
    offset: int = 1,
    limit: int | None = None,
    *,
    limits: ReadLimits | None = None,
    ledger: ReadLedger | None = None,
    use_fff: bool = True,
) -> ReadResult:
    """Read a file or directory through bounded, format-aware handling.

    ``offset`` is always a 1-based source-line (or directory-entry) number.
    Use ``result.next_offset`` to continue. Content omitted by the per-line
    character ceiling is deliberately not addressable by a later line offset.
    """
    selected_limits = (limits or DEFAULT_LIMITS).validate()
    selected_ledger = ledger or DEFAULT_LEDGER
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
        raise ValueError("offset must be a positive 1-based line number")
    selected_limit = selected_limits.max_lines if limit is None else limit
    if (
        isinstance(selected_limit, bool)
        or not isinstance(selected_limit, int)
        or selected_limit < 1
        or selected_limit > selected_limits.max_lines
    ):
        raise ValueError(f"limit must be between 1 and {selected_limits.max_lines}")
    requested = os.fspath(path)
    try:
        resolved = resolve_path(path, limits=selected_limits, use_fff=use_fff)
    except PathResolutionError as exc:
        return _error(
            requested,
            str(exc),
            category=exc.category,
            recovery=exc.recovery,
            suggestions=exc.suggestions,
        )

    if resolved.kind == "directory":
        try:
            return _read_directory(
                resolved,
                offset=offset,
                limit=selected_limit,
                limits=selected_limits,
                ledger=selected_ledger,
            )
        except PathResolutionError as exc:
            return _error(
                requested,
                str(exc),
                category=exc.category,
                canonical=str(resolved.canonical),
                recovery=resolved.recovery,
            )
    if resolved.kind != "regular file":
        return _error(
            requested,
            f"Refusing to read {resolved.kind}: {resolved.canonical}",
            category="non_regular_file",
            canonical=str(resolved.canonical),
            recovery=resolved.recovery,
        )

    try:
        fd, opened_identity = open_verified_regular(resolved.canonical)
    except (NonRegularFileError, PathResolutionError) as exc:
        category = "non_regular_file" if isinstance(exc, NonRegularFileError) else exc.category
        return _error(
            requested,
            str(exc),
            category=category,
            canonical=str(resolved.canonical),
            recovery=resolved.recovery,
        )

    suffix = resolved.canonical.suffix.casefold()
    try:
        prefix = pread_prefix(fd)
        detected_image = image_format(prefix, suffix)
        if detected_image:
            final_identity = _identity_now(fd)
            page = Page("", offset, None, None, False, [], 0, [])
            result = _base_result(
                resolved,
                format_name=f"image/{detected_image}",
                page=page,
                identity=final_identity,
                conversion={
                    "status": "not_attempted",
                    "guidance": (
                        "Use Prime's vision/image path; image bytes were not decoded as text."
                    ),
                },
                pdf=None,
                metadata={"image_format": detected_image},
                ledger=selected_ledger,
                additional_warnings=[
                    "Image content is intentionally not decoded as text; use Prime's vision path."
                ],
            )
            result["status"] = "image"
            result["message"] = "Image metadata returned; no image bytes were decoded as text."
            return result

        is_structured = (
            suffix == ".ipynb" or suffix == ".pdf" or document_format(resolved.canonical)
        )
        if is_structured:
            try:
                data = _read_all_bounded(fd, selected_limits.max_document_bytes)
            except OverflowError as exc:
                return _error(
                    requested,
                    str(exc),
                    category="resource_limited",
                    canonical=str(resolved.canonical),
                    format_name=suffix.lstrip("."),
                    recovery=resolved.recovery,
                    conversion={"status": "refused", "category": "source_size"},
                )
            final_identity = _identity_now(fd)
            if final_identity != opened_identity:
                return _error(
                    requested,
                    "File metadata changed while it was being read; no conversion was trusted.",
                    category="changed_during_read",
                    canonical=str(resolved.canonical),
                    format_name=suffix.lstrip("."),
                    recovery=resolved.recovery,
                )
            if suffix == ".ipynb":
                try:
                    text, notebook_metadata = render_notebook(data, selected_limits)
                except NotebookError as exc:
                    return _error(
                        requested,
                        str(exc),
                        category="malformed",
                        canonical=str(resolved.canonical),
                        format_name="jupyter-notebook",
                        recovery=resolved.recovery,
                        conversion={
                            "status": "failed",
                            "backend": "native",
                            "category": "malformed",
                        },
                    )
                page = _structured_page(
                    text, offset=offset, limit=selected_limit, limits=selected_limits
                )
                return _base_result(
                    resolved,
                    format_name="jupyter-notebook",
                    page=page,
                    identity=final_identity,
                    conversion={"status": "converted", "backend": "native-ipynb"},
                    pdf=None,
                    metadata={"notebook": notebook_metadata},
                    ledger=selected_ledger,
                )
            if suffix == ".pdf":
                try:
                    text, pdf_metadata, pdf_warnings = convert_pdf(data, selected_limits)
                except PdfConversionError as exc:
                    return _error(
                        requested,
                        str(exc),
                        category=exc.category,
                        canonical=str(resolved.canonical),
                        format_name="pdf",
                        recovery=resolved.recovery,
                        conversion={
                            "status": "failed",
                            "backend": "pdf-inspector",
                            "category": exc.category,
                            **exc.detail,
                        },
                    )
                page = _structured_page(
                    text, offset=offset, limit=selected_limit, limits=selected_limits
                )
                return _base_result(
                    resolved,
                    format_name="pdf",
                    page=page,
                    identity=final_identity,
                    conversion={
                        "status": "converted",
                        "backend": "pdf-inspector",
                        "backend_version": "0.2.6",
                    },
                    pdf=pdf_metadata,
                    metadata={},
                    ledger=selected_ledger,
                    additional_warnings=pdf_warnings,
                )
            try:
                text, conversion = convert_anydoc(data, resolved.canonical)
            except DocumentConversionError as exc:
                return _error(
                    requested,
                    str(exc),
                    category=exc.category,
                    canonical=str(resolved.canonical),
                    format_name=suffix.lstrip("."),
                    recovery=resolved.recovery,
                    conversion={
                        "status": "failed",
                        "backend": "firecrawl-anydoc",
                        "category": exc.category,
                        **exc.detail,
                    },
                )
            page = _structured_page(
                text, offset=offset, limit=selected_limit, limits=selected_limits
            )
            return _base_result(
                resolved,
                format_name=conversion["family"],
                page=page,
                identity=final_identity,
                conversion=conversion,
                pdf=None,
                metadata={},
                ledger=selected_ledger,
            )

        if b"\x00" in prefix:
            return _error(
                requested,
                "Binary content (NUL bytes) was detected; it was not decoded as text.",
                category="unsupported_binary",
                canonical=str(resolved.canonical),
                format_name="binary",
                recovery=resolved.recovery,
            )
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            page = paginate_lines(
                iter_utf8_lines(fd, selected_limits.max_line_characters),
                offset=offset,
                limit=selected_limit,
                limits=selected_limits,
            )
        except InvalidEncodingError as exc:
            return _error(
                requested,
                str(exc),
                category="invalid_encoding",
                canonical=str(resolved.canonical),
                format_name="text",
                recovery=resolved.recovery,
                conversion={
                    "status": "not_applicable",
                    "encoding": "utf-8",
                    "invalid_byte_offset": exc.byte_offset,
                },
            )
        final_identity = _identity_now(fd)
        if final_identity != opened_identity:
            return _error(
                requested,
                "File metadata changed while it was being read; the result was discarded.",
                category="changed_during_read",
                canonical=str(resolved.canonical),
                format_name="text",
                recovery=resolved.recovery,
            )
        return _base_result(
            resolved,
            format_name="text",
            page=page,
            identity=final_identity,
            conversion={"status": "not_applicable", "encoding": "utf-8"},
            pdf=None,
            metadata={},
            ledger=selected_ledger,
        )
    except OSError as exc:
        return _error(
            requested,
            f"File read failed: {exc}",
            category="read_failed",
            canonical=str(resolved.canonical),
            format_name=suffix.lstrip(".") or "unknown",
            recovery=resolved.recovery,
        )
    finally:
        os.close(fd)


__all__ = ["read"]
