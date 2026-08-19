"""Version-aware guarded mutation helpers."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from .ledger import FileIdentity, FileVersion, ReadLedger
from .limits import ReadLimits
from .paths import (
    NonRegularFileError,
    PathResolutionError,
    ensure_within_root,
    open_verified_regular,
    prepare_path,
)
from .types import MutationResult


class _StaleMutationError(Exception):
    pass


def _result(
    status: str,
    requested: str,
    *,
    canonical: str | None,
    message: str,
    category: str | None = None,
    changed: bool = False,
    created: bool = False,
    stale: bool = False,
    version: FileVersion | None = None,
) -> MutationResult:
    return MutationResult(
        status=status,
        ok=status in {"ok", "created", "unchanged"},
        category=category,
        requested_path=requested,
        canonical_path=canonical,
        message=message,
        changed=changed,
        created=created,
        stale=stale,
        version=version,
    )


def _failure_category(error: Exception, fallback: str) -> str:
    if isinstance(error, PathResolutionError):
        return error.category
    if isinstance(error, NonRegularFileError):
        return "non_regular_file"
    if isinstance(error, UnicodeError):
        return "invalid_encoding"
    return fallback


def _canonical_for_write(path: Path) -> tuple[Path, bool]:
    if os.path.lexists(path):
        try:
            canonical = path.resolve(strict=True)
        except FileNotFoundError as exc:
            category = "broken_symlink" if path.is_symlink() else "invalid_path"
            raise PathResolutionError(str(exc), category=category) from exc
        except (OSError, RuntimeError) as exc:
            raise PathResolutionError(str(exc), category="invalid_path") from exc
        mode = canonical.stat().st_mode
        if not stat.S_ISREG(mode):
            raise NonRegularFileError(str(canonical), "non-regular target")
        return canonical, False
    try:
        parent = path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathResolutionError(str(exc), category="invalid_path") from exc
    if not parent.is_dir():
        raise PathResolutionError(f"Parent is not a directory: {parent}", category="invalid_path")
    return parent / path.name, True


def _current_identity(path: Path) -> FileIdentity:
    return FileIdentity.from_stat(path.stat())


def _validate_expected(
    canonical: Path,
    *,
    expected: FileVersion | None,
    creating: bool,
    requested: str,
) -> MutationResult | None:
    if expected is not None and not isinstance(expected, FileVersion):
        raise TypeError("expected must be a FileVersion returned by read()")
    if creating:
        if expected is None:
            return None
        if expected.canonical_path != str(canonical):
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                category="version_path_mismatch",
                message=(
                    "The supplied version belongs to a different canonical path; no mutation "
                    "was attempted."
                ),
            )
        return _result(
            "refused",
            requested,
            canonical=str(canonical),
            category="stale_version",
            message="The observed file no longer exists; mutation was refused as stale.",
            stale=True,
        )
    if expected is None:
        return _result(
            "refused",
            requested,
            canonical=str(canonical),
            category="missing_version",
            message=(
                "Overwriting an existing file requires expected=result.version from a "
                "successful guarded read."
            ),
        )
    if expected.canonical_path != str(canonical):
        return _result(
            "refused",
            requested,
            canonical=str(canonical),
            category="version_path_mismatch",
            message=(
                "The supplied version belongs to a different canonical path; no mutation "
                "was attempted."
            ),
        )
    if not expected.matches(_current_identity(canonical)):
        return _result(
            "refused",
            requested,
            canonical=str(canonical),
            category="stale_version",
            message="The file changed since the supplied version was observed.",
            stale=True,
        )
    return None


def _same_content(path: Path, data: bytes, expected: FileIdentity) -> bool:
    fd, identity = open_verified_regular(path)
    try:
        if identity != expected or identity.size != len(data):
            return False
        offset = 0
        while offset < len(data):
            chunk = os.read(fd, min(64 * 1024, len(data) - offset))
            if not chunk or chunk != data[offset : offset + len(chunk)]:
                return False
            offset += len(chunk)
        return not os.read(fd, 1) and FileIdentity.from_stat(os.fstat(fd)) == expected
    finally:
        os.close(fd)


def _atomic_write(
    path: Path,
    data: bytes,
    mode: int | None,
    *,
    expected: FileIdentity | None,
    creating: bool = False,
) -> FileIdentity:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_name, stat.S_IMODE(mode))
        if creating:
            # Linking a same-directory temporary file publishes a new path
            # atomically without clobbering a file created by another writer.
            os.link(temp_name, path)
            os.unlink(temp_name)
        else:
            if expected is not None and _current_identity(path) != expected:
                raise _StaleMutationError(
                    "File became stale before the atomic replacement; no write was made."
                )
            os.replace(temp_name, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                with suppress(OSError):
                    os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return _current_identity(path)
    except Exception:
        with suppress(OSError):
            os.unlink(temp_name)
        raise


def write(
    path: str | os.PathLike[str],
    content: str | bytes,
    *,
    expected: FileVersion | None,
    ledger: ReadLedger,
    root: Path | None,
    allow_mutation: bool,
    encoding: str = "utf-8",
) -> MutationResult:
    """Create a new file or replace an observed version atomically."""
    if not isinstance(content, (str, bytes)):
        raise TypeError("content must be str or bytes")
    if expected is not None and not isinstance(expected, FileVersion):
        raise TypeError("expected must be a FileVersion returned by read()")
    if not isinstance(encoding, str):
        raise TypeError("encoding must be a str")
    requested = os.fspath(path)
    if not allow_mutation:
        return _result(
            "refused",
            requested,
            canonical=None,
            category="mutation_disabled",
            message="Mutation is disabled for this FileOps instance.",
        )
    try:
        requested, effective_path = prepare_path(path, root=root)
        canonical, creating = _canonical_for_write(effective_path)
        ensure_within_root(canonical, root)
        refusal = _validate_expected(
            canonical,
            expected=expected,
            creating=creating,
            requested=requested,
        )
        if refusal is not None:
            return refusal
        data = content.encode(encoding) if isinstance(content, str) else content
        before_write = None
        mode = None
        if not creating:
            before_stat = canonical.stat()
            before_write = FileIdentity.from_stat(before_stat)
            mode = before_stat.st_mode
            if expected is None or not expected.matches(before_write):
                return _result(
                    "refused",
                    requested,
                    canonical=str(canonical),
                    category="stale_version",
                    message="The file changed while write() was preparing the replacement.",
                    stale=True,
                )
            if _same_content(canonical, data, before_write):
                if _current_identity(canonical) != before_write:
                    raise _StaleMutationError(
                        "File became stale while checking for an unchanged write."
                    )
                ledger.record(str(canonical), before_write)
                return _result(
                    "unchanged",
                    requested,
                    canonical=str(canonical),
                    message="The requested content is already present; no write was necessary.",
                    version=expected,
                )
        identity = _atomic_write(
            canonical,
            data,
            mode,
            expected=before_write,
            creating=creating,
        )
        ledger.record(str(canonical), identity)
        version = FileVersion.from_identity(str(canonical), identity)
        return _result(
            "created" if creating else "ok",
            requested,
            canonical=str(canonical),
            message="File was written atomically.",
            changed=True,
            created=creating,
            version=version,
        )
    except _StaleMutationError as exc:
        return _result(
            "refused",
            requested,
            canonical=str(canonical),
            category="stale_version",
            message=str(exc),
            stale=True,
        )
    except FileExistsError:
        return _result(
            "refused",
            requested,
            canonical=str(canonical),
            category="destination_exists",
            message="A file appeared at the new path before publication; no overwrite was made.",
            stale=True,
        )
    except (OSError, PathResolutionError, NonRegularFileError, UnicodeError) as exc:
        category = _failure_category(exc, "write_failed")
        return _result(
            "error",
            requested,
            canonical=None,
            category=category,
            message=f"Guarded write failed: {exc}",
        )


def edit(
    path: str | os.PathLike[str],
    old: str,
    new: str,
    *,
    expected: FileVersion,
    all_matches: bool,
    ledger: ReadLedger,
    limits: ReadLimits,
    root: Path | None,
    allow_mutation: bool,
    encoding: str = "utf-8",
) -> MutationResult:
    """Apply an exact text replacement to an observed file version."""
    if not isinstance(old, str) or not isinstance(new, str):
        raise TypeError("old and new must be str")
    if not old:
        raise ValueError("old must not be empty")
    if not isinstance(expected, FileVersion):
        raise TypeError("expected must be a FileVersion returned by read()")
    if not isinstance(all_matches, bool):
        raise TypeError("all_matches must be a bool")
    if not isinstance(encoding, str):
        raise TypeError("encoding must be a str")
    requested = os.fspath(path)
    if not allow_mutation:
        return _result(
            "refused",
            requested,
            canonical=None,
            category="mutation_disabled",
            message="Mutation is disabled for this FileOps instance.",
        )
    try:
        requested, effective_path = prepare_path(path, root=root)
        canonical, creating = _canonical_for_write(effective_path)
        ensure_within_root(canonical, root)
        refusal = _validate_expected(
            canonical,
            expected=expected,
            creating=creating,
            requested=requested,
        )
        if refusal is not None:
            return refusal
        fd, identity = open_verified_regular(canonical)
        try:
            if identity.size > limits.max_document_bytes:
                return _result(
                    "refused",
                    requested,
                    canonical=str(canonical),
                    category="resource_limited",
                    message=(
                        f"Edit source is {identity.size:,} bytes; the mutation ceiling is "
                        f"{limits.max_document_bytes:,} bytes."
                    ),
                )
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            current_identity = FileIdentity.from_stat(os.fstat(fd))
            mode = os.fstat(fd).st_mode
        finally:
            os.close(fd)
        if current_identity != identity or not expected.matches(identity):
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                category="stale_version",
                message="File changed while edit() was reading it.",
                stale=True,
            )
        text = b"".join(chunks).decode(encoding, errors="strict")
        matches = text.count(old)
        if matches == 0:
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                category="old_text_not_found",
                message="The exact old text was not found; no edit was made.",
            )
        if matches > 1 and not all_matches:
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                category="multiple_matches",
                message=(
                    f"The exact old text occurs {matches} times; edit() requires one match by "
                    "default. Pass all_matches=True to replace every occurrence."
                ),
            )
        replacements = matches if all_matches else 1
        if old == new:
            if _current_identity(canonical) != identity:
                return _result(
                    "refused",
                    requested,
                    canonical=str(canonical),
                    category="stale_version",
                    message="File became stale before the no-op edit completed.",
                    stale=True,
                )
            ledger.record(str(canonical), identity)
            return _result(
                "unchanged",
                requested,
                canonical=str(canonical),
                message="Old and new text are identical; no edit was necessary.",
                version=expected,
            )
        updated = text.replace(old, new, -1 if all_matches else 1)
        if _current_identity(canonical) != identity:
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                category="stale_version",
                message="File became stale before the atomic replacement; no edit was made.",
                stale=True,
            )
        new_identity = _atomic_write(
            canonical,
            updated.encode(encoding),
            mode,
            expected=identity,
        )
        ledger.record(str(canonical), new_identity)
        version = FileVersion.from_identity(str(canonical), new_identity)
        result = _result(
            "ok",
            requested,
            canonical=str(canonical),
            message=f"Applied {replacements} exact replacement(s) atomically.",
            changed=True,
            version=version,
        )
        result["replacements"] = replacements
        return result
    except _StaleMutationError as exc:
        return _result(
            "refused",
            requested,
            canonical=str(canonical),
            category="stale_version",
            message=str(exc),
            stale=True,
        )
    except (OSError, PathResolutionError, NonRegularFileError, UnicodeError) as exc:
        category = _failure_category(exc, "edit_failed")
        return _result(
            "error",
            requested,
            canonical=None,
            category=category,
            message=f"Guarded edit failed: {exc}",
        )


__all__ = ["edit", "write"]
