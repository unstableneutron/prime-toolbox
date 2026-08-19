"""Public mapping-compatible result objects."""

from __future__ import annotations

from typing import Any

from .ledger import FileVersion

_MAX_REPR_CHARACTERS = 4_096


def _short(value: Any, maximum: int = 200) -> str:
    text = str(value)
    return text if len(text) <= maximum else text[: maximum - 3] + "..."


class _Result(dict[str, Any]):
    """A normal dictionary with attribute access and a bounded representation."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class ReadResult(_Result):
    """Mapping-compatible bounded read result with attribute access."""

    @property
    def version(self) -> FileVersion | None:
        value = self.get("version")
        return value if isinstance(value, FileVersion) else None

    def compact(self, max_characters: int = _MAX_REPR_CHARACTERS) -> str:
        max_characters = max(256, min(max_characters, _MAX_REPR_CHARACTERS))
        status = self.get("status", "unknown")
        path = self.get("canonical_path") or self.get("requested_path") or "?"
        header = (
            f"ReadResult(status={status!r}, format={self.get('format')!r}, "
            f"path={_short(path, 240)!r}"
        )
        start, end = self.get("start_offset"), self.get("end_offset")
        if start is not None:
            header += f", lines={start}-{end if end is not None else start}"
        if self.get("truncated"):
            header += f", truncated_by={self.get('truncated_by', [])!r}"
        header += ")"

        lines = [header]
        message = self.get("message")
        if message:
            lines.append(_short(message, 600))
        suggestions = self.get("suggestions") or []
        if suggestions:
            lines.append("Suggestions: " + ", ".join(_short(item, 180) for item in suggestions))

        content = self.get("content") or ""
        if content:
            source_line = self.get("start_offset") or 1
            for index, line in enumerate(content.splitlines() or [content]):
                lines.append(f"{source_line + index}|{line}")
                if sum(len(item) + 1 for item in lines) >= max_characters - 240:
                    lines.append(
                        "... content preview truncated; use result['content'] "
                        "for this bounded window"
                    )
                    break

        next_offset = self.get("next_offset")
        if next_offset is not None:
            lines.append(f"Continue with offset={next_offset}.")
        warnings = self.get("warnings") or []
        if warnings:
            lines.append("Warning: " + _short(warnings[0], 500))

        result = "\n".join(lines)
        return result if len(result) <= max_characters else result[: max_characters - 3] + "..."

    def __repr__(self) -> str:
        return self.compact()

    __str__ = __repr__


class MutationResult(_Result):
    """Mapping-compatible write/edit result with a chainable version."""

    @property
    def version(self) -> FileVersion | None:
        value = self.get("version")
        return value if isinstance(value, FileVersion) else None

    def __repr__(self) -> str:
        text = (
            f"MutationResult(status={self.get('status')!r}, "
            f"path={_short(self.get('canonical_path') or self.get('requested_path'), 300)!r}, "
            f"changed={bool(self.get('changed'))})"
        )
        if self.get("message"):
            text += "\n" + _short(self["message"], 700)
        return text[:_MAX_REPR_CHARACTERS]

    __str__ = __repr__


__all__ = ["MutationResult", "ReadResult"]
