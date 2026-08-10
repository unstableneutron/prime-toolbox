"""Incremental, bounded UTF-8 text reading."""

from __future__ import annotations

import codecs
import os
from collections.abc import Iterator

from .formatting import SourceLine

_CHUNK_BYTES = 64 * 1024


class InvalidEncodingError(Exception):
    def __init__(self, byte_offset: int, reason: str) -> None:
        super().__init__(f"Invalid UTF-8 at byte {byte_offset}: {reason}")
        self.byte_offset = byte_offset
        self.reason = reason


def _append_bounded(parts: list[str], text: str, retained: int, maximum: int) -> tuple[int, bool]:
    available = max(0, maximum - retained)
    if available and text:
        parts.append(text[:available])
    return retained + min(len(text), available), len(text) > available


def iter_utf8_lines(fd: int, max_characters: int) -> Iterator[SourceLine]:
    """Yield strict UTF-8 lines while retaining at most one bounded line.

    Newline discovery and decoding are incremental. Even a multi-gigabyte
    single line retains only ``max_characters`` plus one fixed-size input
    chunk, while the rest is scanned and validated without accumulation.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    parts: list[str] = []
    retained = 0
    truncated = False
    number = 1
    fed_bytes = 0
    line_had_bytes = False

    while True:
        chunk = os.read(fd, _CHUNK_BYTES)
        if not chunk:
            break
        cursor = 0
        while cursor < len(chunk):
            newline = chunk.find(b"\n", cursor)
            segment_end = len(chunk) if newline < 0 else newline
            segment = chunk[cursor:segment_end]
            pending_before = len(decoder.getstate()[0])
            try:
                decoded = decoder.decode(segment, final=newline >= 0)
            except UnicodeDecodeError as exc:
                absolute = fed_bytes + cursor - pending_before + exc.start
                raise InvalidEncodingError(absolute, exc.reason) from exc
            retained, clipped = _append_bounded(parts, decoded, retained, max_characters)
            truncated = truncated or clipped
            line_had_bytes = line_had_bytes or bool(segment)
            if newline < 0:
                cursor = len(chunk)
                continue

            text = "".join(parts)
            if text.endswith("\r"):
                text = text[:-1]
            yield SourceLine(number, text, truncated)
            number += 1
            decoder = codecs.getincrementaldecoder("utf-8")("strict")
            parts = []
            retained = 0
            truncated = False
            line_had_bytes = False
            cursor = newline + 1
        fed_bytes += len(chunk)

    pending_before = len(decoder.getstate()[0])
    try:
        tail = decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        absolute = fed_bytes - pending_before + exc.start
        raise InvalidEncodingError(absolute, exc.reason) from exc
    retained, clipped = _append_bounded(parts, tail, retained, max_characters)
    truncated = truncated or clipped
    if line_had_bytes or parts or pending_before:
        text = "".join(parts)
        if text.endswith("\r"):
            text = text[:-1]
        yield SourceLine(number, text, truncated)


def pread_prefix(fd: int, maximum: int = 8_192) -> bytes:
    if hasattr(os, "pread"):
        return os.pread(fd, maximum, 0)
    position = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, maximum)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def image_format(data: bytes, suffix: str) -> str | None:
    signatures = (
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff", "jpeg"),
        (b"GIF87a", "gif"),
        (b"GIF89a", "gif"),
        (b"BM", "bmp"),
        (b"II*\x00", "tiff"),
        (b"MM\x00*", "tiff"),
        (b"\x00\x00\x01\x00", "x-icon"),
    )
    for signature, name in signatures:
        if data.startswith(signature):
            return name
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if (
        len(data) >= 12
        and data[4:8] == b"ftyp"
        and data[8:12]
        in {
            b"heic",
            b"heix",
            b"hevc",
            b"mif1",
            b"avif",
        }
    ):
        return data[8:12].decode("ascii")
    stripped = data.lstrip()
    if stripped.startswith(b"<svg") or (
        stripped.startswith(b"<?xml") and b"<svg" in stripped[:2_048].lower()
    ):
        return "svg+xml"
    if suffix.casefold() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".tif",
        ".tiff",
        ".heic",
        ".avif",
        ".ico",
        ".svg",
    }:
        return suffix.lstrip(".").casefold()
    return None


__all__ = ["InvalidEncodingError", "image_format", "iter_utf8_lines", "pread_prefix"]
