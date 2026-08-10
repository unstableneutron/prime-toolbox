"""Read-ledger-aware safe mutation helpers."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

from .ledger import DEFAULT_LEDGER, FileIdentity, ReadLedger
from .limits import DEFAULT_LIMITS, ReadLimits
from .paths import NonRegularFileError, PathResolutionError, open_verified_regular
from .types import MutationResult


class _StaleMutationError(Exception):
    pass


def _result(
    status: str,
    requested: str,
    *,
    canonical: str | None,
    message: str,
    changed: bool = False,
    created: bool = False,
    stale: bool = False,
) -> MutationResult:
    return MutationResult(
        status=status,
        requested_path=requested,
        canonical_path=canonical,
        message=message,
        changed=changed,
        created=created,
        stale=stale,
    )


def _canonical_for_write(path: Path) -> tuple[Path, bool]:
    if os.path.lexists(path):
        try:
            canonical = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathResolutionError(str(exc), category="invalid_path") from exc
        mode = canonical.stat().st_mode
        if not stat.S_ISREG(mode):
            raise NonRegularFileError(str(canonical), "non-regular target")
        return canonical, False
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise PathResolutionError(f"Parent is not a directory: {parent}", category="invalid_path")
    return parent / path.name, True


def _current_identity(path: Path) -> FileIdentity:
    return FileIdentity.from_stat(path.stat())


def _guard(
    canonical: Path,
    *,
    ledger: ReadLedger,
    require_read: bool,
    creating: bool,
) -> MutationResult | None:
    if creating:
        if ledger.get(str(canonical)) is not None:
            return _result(
                "refused",
                str(canonical),
                canonical=str(canonical),
                message="A previously read file disappeared; mutation was refused as stale.",
                stale=True,
            )
        return None
    entry = ledger.get(str(canonical))
    if require_read and entry is None:
        return _result(
            "refused",
            str(canonical),
            canonical=str(canonical),
            message="Mutation requires a prior successful read of this canonical path.",
        )
    if entry is not None:
        current = _current_identity(canonical)
        if current != entry.identity:
            return _result(
                "refused",
                str(canonical),
                canonical=str(canonical),
                message="File changed since its last successful ledger read; mutation was refused.",
                stale=True,
            )
    return None


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


def safe_write(
    path: str | os.PathLike[str],
    content: str | bytes,
    *,
    require_read: bool = False,
    ledger: ReadLedger | None = None,
    encoding: str = "utf-8",
) -> MutationResult:
    """Atomically replace a regular file, with optional read-before-write."""
    requested = os.fspath(path)
    selected_ledger = ledger or DEFAULT_LEDGER
    raw = Path(requested).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    try:
        canonical, creating = _canonical_for_write(raw)
        refusal = _guard(
            canonical,
            ledger=selected_ledger,
            require_read=require_read,
            creating=creating,
        )
        if refusal is not None:
            refusal["requested_path"] = requested
            return refusal
        data = content.encode(encoding) if isinstance(content, str) else bytes(content)
        before_write = None
        mode = None
        if not creating:
            before_stat = canonical.stat()
            before_write = FileIdentity.from_stat(before_stat)
            entry = selected_ledger.get(str(canonical))
            if entry is not None and before_write != entry.identity:
                return _result(
                    "refused",
                    requested,
                    canonical=str(canonical),
                    message=(
                        "File changed since its last successful ledger read; no write was made."
                    ),
                    stale=True,
                )
            mode = before_stat.st_mode
        identity = _atomic_write(
            canonical,
            data,
            mode,
            expected=before_write,
            creating=creating,
        )
        selected_ledger.record(str(canonical), identity, complete=True)
        return _result(
            "created" if creating else "ok",
            requested,
            canonical=str(canonical),
            message="File was written atomically and the ledger now observes the new identity.",
            changed=True,
            created=creating,
        )
    except _StaleMutationError as exc:
        return _result("refused", requested, canonical=str(canonical), message=str(exc), stale=True)
    except FileExistsError:
        return _result(
            "refused",
            requested,
            canonical=str(canonical),
            message="A file appeared at the new path before publication; no overwrite was made.",
            stale=True,
        )
    except (OSError, PathResolutionError, NonRegularFileError, UnicodeError, TypeError) as exc:
        return _result("error", requested, canonical=None, message=f"Safe write failed: {exc}")


def safe_edit(
    path: str | os.PathLike[str],
    old: str,
    new: str,
    *,
    count: int = -1,
    require_read: bool = True,
    ledger: ReadLedger | None = None,
    limits: ReadLimits | None = None,
    encoding: str = "utf-8",
) -> MutationResult:
    """Apply an exact text replacement after checking the ledger identity."""
    if not old:
        raise ValueError("old must not be empty")
    if isinstance(count, bool) or not isinstance(count, int) or count == 0 or count < -1:
        raise ValueError("count must be -1 or a positive integer")
    requested = os.fspath(path)
    selected_ledger = ledger or DEFAULT_LEDGER
    selected_limits = (limits or DEFAULT_LIMITS).validate()
    raw = Path(requested).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    try:
        canonical, creating = _canonical_for_write(raw)
        if creating:
            stale = selected_ledger.get(str(canonical)) is not None
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                message=(
                    "A previously read file disappeared; the edit was refused as stale."
                    if stale
                    else "safe_edit cannot create a missing file; use safe_write for new files."
                ),
                stale=stale,
            )
        refusal = _guard(
            canonical,
            ledger=selected_ledger,
            require_read=require_read,
            creating=False,
        )
        if refusal is not None:
            refusal["requested_path"] = requested
            return refusal
        fd, identity = open_verified_regular(canonical)
        try:
            if identity.size > selected_limits.max_document_bytes:
                return _result(
                    "refused",
                    requested,
                    canonical=str(canonical),
                    message=(
                        f"Edit source is {identity.size:,} bytes; the mutation ceiling is "
                        f"{selected_limits.max_document_bytes:,} bytes."
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
        if current_identity != identity:
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                message="File changed while safe_edit was reading it.",
                stale=True,
            )
        entry = selected_ledger.get(str(canonical))
        if entry is not None and identity != entry.identity:
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                message="File changed since its last successful ledger read; no edit was made.",
                stale=True,
            )
        text = b"".join(chunks).decode(encoding, errors="strict")
        replacements = text.count(old) if count == -1 else min(text.count(old), count)
        if replacements == 0:
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                message="The exact old text was not found; no edit was made.",
            )
        updated = text.replace(old, new, count)
        if _current_identity(canonical) != identity:
            return _result(
                "refused",
                requested,
                canonical=str(canonical),
                message="File became stale before the atomic replacement; no edit was made.",
                stale=True,
            )
        new_identity = _atomic_write(
            canonical,
            updated.encode(encoding),
            mode,
            expected=identity,
        )
        selected_ledger.record(str(canonical), new_identity, complete=True)
        result = _result(
            "ok",
            requested,
            canonical=str(canonical),
            message=f"Applied {replacements} exact replacement(s) atomically.",
            changed=True,
        )
        result["replacements"] = replacements
        return result
    except _StaleMutationError as exc:
        return _result("refused", requested, canonical=str(canonical), message=str(exc), stale=True)
    except (OSError, PathResolutionError, NonRegularFileError, UnicodeError) as exc:
        return _result("error", requested, canonical=None, message=f"Safe edit failed: {exc}")


__all__ = ["safe_edit", "safe_write"]
