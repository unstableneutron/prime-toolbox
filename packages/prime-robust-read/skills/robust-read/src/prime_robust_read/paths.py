"""Path resolution, recovery, and verified opening."""

from __future__ import annotations

import asyncio
import importlib
import os
import queue
import stat
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ledger import FileIdentity
from .limits import ReadLimits

_PUNCTUATION_EQUIVALENTS = str.maketrans(
    {
        "\u00a0": " ",
        "\u202f": " ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)


class PathResolutionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        category: str,
        suggestions: list[str] | None = None,
        recovery: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.suggestions = suggestions or []
        self.recovery = recovery or {}


class NonRegularFileError(Exception):
    def __init__(self, path: str, kind: str) -> None:
        super().__init__(f"{path!r} resolves to {kind}, not a regular file")
        self.path = path
        self.kind = kind


@dataclass(slots=True)
class ResolvedPath:
    requested: str
    canonical: Path
    kind: str
    recovery: dict[str, Any] = field(default_factory=lambda: {"method": "exact"})
    warnings: list[str] = field(default_factory=list)


def _equivalence_key(value: str) -> str:
    return unicodedata.normalize("NFC", value.translate(_PUNCTUATION_EQUIVALENTS)).casefold()


def _kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISFIFO(mode):
        return "FIFO (named pipe)"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character device"
    if stat.S_ISBLK(mode):
        return "block device"
    return "special file"


def _safe_candidate(path: Path) -> tuple[Path, str] | None:
    try:
        canonical = path.resolve(strict=True)
        mode = canonical.stat().st_mode
    except (OSError, RuntimeError):
        return None
    kind = _kind(mode)
    return (canonical, kind) if kind in {"regular file", "directory"} else None


def _sibling_candidates(path: Path, limits: ReadLimits) -> tuple[list[Path], bool]:
    parent = path.parent
    try:
        if not parent.is_dir():
            return [], False
        requested_key = _equivalence_key(path.name)
        matches: list[Path] = []
        scanned = 0
        with os.scandir(parent) as entries:
            for entry in entries:
                scanned += 1
                if scanned > limits.max_sibling_entries:
                    return matches, True
                if _equivalence_key(entry.name) == requested_key:
                    candidate = _safe_candidate(parent / entry.name)
                    if candidate is not None:
                        matches.append(parent / entry.name)
                        if len(matches) > limits.max_suggestions:
                            return matches, False
        return matches, False
    except OSError:
        return [], False


def _git_root(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _fff_candidates(path: Path, limits: ReadLimits) -> tuple[list[Path], str, bool]:
    """Return safe FFF path matches without making FFF a hard dependency."""
    try:
        fff_repo_search = importlib.import_module("fff_repo_search")
    except Exception:
        return [], "unavailable", False

    root = _git_root(path)
    if root is None:
        return [], "outside_git_repository", False
    root = root.resolve()

    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            response = asyncio.run(
                fff_repo_search.find_files(
                    path.name,
                    within=str(root),
                    limit=min(50, limits.max_suggestions + 1),
                )
            )
            result_queue.put(("ok", response))
        except Exception as exc:  # FFF transport/native failures are optional.
            result_queue.put(("error", exc))

    thread = threading.Thread(target=worker, name="prime-robust-read-fff", daemon=True)
    thread.start()
    thread.join(limits.fff_timeout_seconds)
    if thread.is_alive():
        return [], "timeout", False
    try:
        state, value = result_queue.get_nowait()
    except queue.Empty:
        return [], "failed", False
    if state != "ok":
        return [], f"unavailable: {type(value).__name__}", False

    items = value.get("items", []) if isinstance(value, dict) else []
    stats = value.get("stats", {}) if isinstance(value, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    reported_total = stats.get("total_count", stats.get("result_count", len(items)))
    has_more = isinstance(reported_total, int) and reported_total > len(items)
    candidates: list[Path] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = item.get("absolute_path")
        if not isinstance(raw, str):
            relative = item.get("path")
            raw = str(root / relative) if isinstance(relative, str) else None
        if raw is None:
            continue
        safe = _safe_candidate(Path(raw))
        if safe is None:
            continue
        canonical, _ = safe
        if not canonical.is_relative_to(root):
            continue
        key = str(canonical)
        if key not in seen:
            seen.add(key)
            candidates.append(canonical)
    return candidates, "available", has_more


def resolve_path(
    path: str | os.PathLike[str],
    *,
    limits: ReadLimits,
    use_fff: bool,
) -> ResolvedPath:
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise TypeError("path must be a string or path-like object")
    if not raw or "\x00" in raw:
        raise ValueError("path must be non-empty and contain no NUL bytes")
    requested = Path(raw).expanduser()
    if not requested.is_absolute():
        requested = Path.cwd() / requested

    try:
        canonical = requested.resolve(strict=True)
    except FileNotFoundError as exact_error:
        if os.path.lexists(requested) and requested.is_symlink():
            raise PathResolutionError(
                f"Broken symlink: {requested}", category="broken_symlink"
            ) from exact_error
        siblings, scan_truncated = _sibling_candidates(requested, limits)
        if len(siblings) == 1 and not scan_truncated:
            canonical, kind = _safe_candidate(siblings[0]) or (siblings[0], "unknown")
            return ResolvedPath(
                raw,
                canonical,
                kind,
                recovery={"method": "unicode_sibling", "requested": str(requested)},
            )
        if siblings:
            suggestions = [str(item) for item in siblings[: limits.max_suggestions]]
            raise PathResolutionError(
                f"Path is ambiguous after Unicode/punctuation normalization: {requested}",
                category="ambiguous_path",
                suggestions=suggestions,
                recovery={
                    "method": "unicode_sibling",
                    "ambiguous": True,
                    "scan_truncated": scan_truncated,
                },
            ) from exact_error

        fff_status = "disabled"
        fff_matches: list[Path] = []
        fff_has_more = False
        if use_fff:
            fff_matches, fff_status, fff_has_more = _fff_candidates(requested, limits)
        if len(fff_matches) == 1 and not fff_has_more:
            safe = _safe_candidate(fff_matches[0])
            if safe is not None:
                canonical, kind = safe
                return ResolvedPath(
                    raw,
                    canonical,
                    kind,
                    recovery={
                        "method": "fff",
                        "requested": str(requested),
                        "fff_status": fff_status,
                    },
                )
        suggestions = [str(item) for item in fff_matches[: limits.max_suggestions]]
        category = "ambiguous_path" if len(fff_matches) > 1 or fff_has_more else "not_found"
        message = (
            f"FFF found multiple possible paths for {requested}"
            if category == "ambiguous_path"
            else f"Path does not exist: {requested}"
        )
        raise PathResolutionError(
            message,
            category=category,
            suggestions=suggestions,
            recovery={
                "method": "none",
                "fff_status": fff_status,
                "fff_has_more": fff_has_more,
            },
        ) from exact_error
    except RuntimeError as exc:
        raise PathResolutionError(
            f"Cannot resolve path (possible symlink loop): {requested}",
            category="invalid_path",
        ) from exc
    except OSError as exc:
        raise PathResolutionError(str(exc), category="invalid_path") from exc

    try:
        mode = canonical.stat().st_mode
    except OSError as exc:
        raise PathResolutionError(str(exc), category="stat_failed") from exc
    return ResolvedPath(raw, canonical, _kind(mode))


def open_verified_regular(path: Path) -> tuple[int, FileIdentity]:
    """Open a canonical path and verify the opened object is the statted file.

    ``O_NONBLOCK`` prevents an accidental FIFO open from blocking if the path
    changes during the check. ``O_NOFOLLOW`` protects the final component when
    available. The post-open ``fstat`` is authoritative and rejects every
    non-regular target before any read or converter receives bytes.
    """
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise NonRegularFileError(str(path), _kind(before.st_mode))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PathResolutionError(str(exc), category="open_failed") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise NonRegularFileError(str(path), _kind(opened.st_mode))
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise PathResolutionError(
                f"File changed identity while opening: {path}", category="changed_during_open"
            )
        return fd, FileIdentity.from_stat(opened)
    except Exception:
        os.close(fd)
        raise


__all__ = [
    "NonRegularFileError",
    "PathResolutionError",
    "ResolvedPath",
    "open_verified_regular",
    "resolve_path",
]
