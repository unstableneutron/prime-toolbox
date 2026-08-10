"""Bounded pagination shared by text and converted documents."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from .limits import ReadLimits


@dataclass(slots=True)
class SourceLine:
    number: int
    text: str
    character_truncated: bool = False


@dataclass(slots=True)
class Page:
    content: str
    start_offset: int
    end_offset: int | None
    next_offset: int | None
    truncated: bool
    truncated_by: list[str]
    total_lines: int | None
    warnings: list[str]


def _utf8_prefix(text: str, maximum: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= maximum:
        return text
    return data[:maximum].decode("utf-8", errors="ignore")


def paginate_lines(
    lines: Iterator[SourceLine],
    *,
    offset: int,
    limit: int,
    limits: ReadLimits,
) -> Page:
    emitted: list[str] = []
    end_offset: int | None = None
    next_offset: int | None = None
    used_bytes = 0
    last_seen = 0
    truncated_by: list[str] = []
    warnings: list[str] = []
    exhausted = True

    for line in lines:
        last_seen = line.number
        if line.number < offset:
            continue
        if len(emitted) >= limit:
            next_offset = line.number
            truncated_by.append("lines")
            exhausted = False
            break

        encoded_bytes = len(line.text.encode("utf-8"))
        addition = encoded_bytes + (1 if emitted else 0)
        if used_bytes + addition > limits.max_bytes:
            if emitted:
                next_offset = line.number
                truncated_by.append("bytes")
                exhausted = False
                break
            prefix = _utf8_prefix(line.text, limits.max_bytes)
            emitted.append(prefix)
            end_offset = line.number
            used_bytes = len(prefix.encode("utf-8"))
            truncated_by.extend(("bytes", "line_characters"))
            warnings.append(
                "The first source line exceeded the response-byte ceiling; its omitted "
                "remainder is not addressable by the 1-based line offset."
            )
            continue

        emitted.append(line.text)
        end_offset = line.number
        used_bytes += addition
        if line.character_truncated:
            if "line_characters" not in truncated_by:
                truncated_by.append("line_characters")
            warnings.append(
                f"Source line {line.number} exceeded the per-line character ceiling; "
                "the omitted remainder is intentionally unavailable through line offsets."
            )

    total_lines = last_seen if exhausted else None

    return Page(
        content="\n".join(emitted),
        start_offset=offset,
        end_offset=end_offset,
        next_offset=next_offset,
        truncated=bool(truncated_by),
        truncated_by=list(dict.fromkeys(truncated_by)),
        total_lines=total_lines,
        warnings=list(dict.fromkeys(warnings)),
    )


def iter_string_lines(text: str, max_characters: int) -> Iterator[SourceLine]:
    """Yield bounded lines without materializing ``text.splitlines()``."""
    start = 0
    number = 1
    length = len(text)
    while start < length:
        end = text.find("\n", start)
        if end < 0:
            end = length
        raw_end = end - 1 if end > start and text[end - 1] == "\r" else end
        clipped_end = min(raw_end, start + max_characters)
        yield SourceLine(number, text[start:clipped_end], raw_end - start > max_characters)
        number += 1
        if end == length:
            break
        start = end + 1


__all__ = ["Page", "SourceLine", "iter_string_lines", "paginate_lines"]
