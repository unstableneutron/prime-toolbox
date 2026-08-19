"""Public functional facade and scoped FileOps API."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from .ledger import FileVersion, ReadLedger
from .limits import DEFAULT_LIMITS, ReadLimits
from .mutation import edit as _edit
from .mutation import write as _write
from .policy import FileOpsPolicy
from .reader import read as _read
from .types import MutationResult, ReadResult


def _normalize_root(root: str | os.PathLike[str] | None) -> Path | None:
    if root is None:
        return None
    selected = Path(root).expanduser().resolve(strict=True)
    if not selected.is_dir():
        raise NotADirectoryError(f"FileOps root is not a directory: {selected}")
    return selected


class FileOps:
    """Guarded operations with an optional root and isolated observation state.

    Relative paths resolve beneath ``root``. ``policy`` controls resource
    limits, fuzzy FFF recovery, and whether mutation is allowed. Prefer the
    module-level functions unless a task needs this scoping or isolation.
    """

    __slots__ = ("_ledger", "_mutation_lock", "_policy", "_root")

    def __init__(
        self,
        *,
        root: str | os.PathLike[str] | None = None,
        policy: FileOpsPolicy | None = None,
    ) -> None:
        if policy is not None and not isinstance(policy, FileOpsPolicy):
            raise TypeError("policy must be a FileOpsPolicy instance")
        self._root = _normalize_root(root)
        self._policy = (policy or FileOpsPolicy()).validate()
        self._ledger = ReadLedger()
        self._mutation_lock = threading.RLock()

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def policy(self) -> FileOpsPolicy:
        return self._policy

    def clear_observations(self) -> None:
        """Forget repeated/change observations without changing files."""
        self._ledger.clear()

    def read(
        self,
        path: str | os.PathLike[str],
        offset: int = 1,
        limit: int | None = None,
        *,
        limits: ReadLimits | None = None,
    ) -> ReadResult:
        """Boundedly read, list, or extract ``path`` under this instance's policy.

        ``offset`` is a 1-based source line or directory-entry index. Continue
        pagination only with the returned ``result.next_offset``.
        """
        return _read(
            path,
            offset=offset,
            limit=limit,
            limits=(limits or self.policy.limits),
            ledger=self._ledger,
            use_fff=self.policy.use_fff,
            root=self.root,
        )

    def write(
        self,
        path: str | os.PathLike[str],
        content: str | bytes,
        *,
        expected: FileVersion | None = None,
        encoding: str = "utf-8",
    ) -> MutationResult:
        """Create a missing file or replace exactly ``expected``.

        Existing targets require a current ``FileVersion`` returned by
        :meth:`read`. The operation refuses stale or wrong-path versions and
        returns a new chainable version after success.
        """
        with self._mutation_lock:
            return _write(
                path,
                content,
                expected=expected,
                ledger=self._ledger,
                root=self.root,
                allow_mutation=self.policy.allow_mutation,
                encoding=encoding,
            )

    def edit(
        self,
        path: str | os.PathLike[str],
        old: str,
        new: str,
        *,
        expected: FileVersion,
        all_matches: bool = False,
        encoding: str = "utf-8",
    ) -> MutationResult:
        """Replace one exact literal match in ``expected`` by default.

        Zero or multiple matches are refused. Set ``all_matches=True`` only
        when every literal occurrence should be replaced.
        """
        with self._mutation_lock:
            return _edit(
                path,
                old,
                new,
                expected=expected,
                all_matches=all_matches,
                ledger=self._ledger,
                limits=self.policy.limits,
                root=self.root,
                allow_mutation=self.policy.allow_mutation,
                encoding=encoding,
            )


_DEFAULT_OPS = FileOps()


def read(
    path: str | os.PathLike[str],
    offset: int = 1,
    limit: int | None = None,
    *,
    limits: ReadLimits | None = None,
    use_fff: bool = True,
    root: str | os.PathLike[str] | None = None,
) -> ReadResult:
    """Boundedly read, list, or extract a local path.

    Handles text, directories, notebooks, PDFs, supported structured
    documents, and image metadata. ``offset`` is 1-based; use only the returned
    ``next_offset`` to continue. ``root`` restricts resolution and recovery.
    Returns a structured ``ReadResult``; operational failures do not raise.
    """
    if not isinstance(use_fff, bool):
        raise TypeError("use_fff must be a bool")
    return _read(
        path,
        offset=offset,
        limit=limit,
        limits=(limits or DEFAULT_LIMITS),
        ledger=_DEFAULT_OPS._ledger,
        use_fff=use_fff,
        root=_normalize_root(root),
    )


def write(
    path: str | os.PathLike[str],
    content: str | bytes,
    *,
    expected: FileVersion | None = None,
    root: str | os.PathLike[str] | None = None,
    encoding: str = "utf-8",
) -> MutationResult:
    """Create a missing file or replace an existing observed version.

    Omit ``expected`` only for creation. Existing targets require
    ``expected=result.version`` from :func:`read`; stale, deleted, and
    wrong-path versions are refused. Returns the post-write version.
    """
    with _DEFAULT_OPS._mutation_lock:
        return _write(
            path,
            content,
            expected=expected,
            ledger=_DEFAULT_OPS._ledger,
            root=_normalize_root(root),
            allow_mutation=True,
            encoding=encoding,
        )


def edit(
    path: str | os.PathLike[str],
    old: str,
    new: str,
    *,
    expected: FileVersion,
    all_matches: bool = False,
    limits: ReadLimits | None = None,
    root: str | os.PathLike[str] | None = None,
    encoding: str = "utf-8",
) -> MutationResult:
    """Version-check and replace one exact literal text match.

    Pass ``expected=result.version`` from :func:`read`. Zero or multiple
    matches are refused unless ``all_matches=True``. The successful result
    contains the post-edit version for a subsequent mutation.
    """
    with _DEFAULT_OPS._mutation_lock:
        return _edit(
            path,
            old,
            new,
            expected=expected,
            all_matches=all_matches,
            ledger=_DEFAULT_OPS._ledger,
            limits=(limits or DEFAULT_LIMITS).validate(),
            root=_normalize_root(root),
            allow_mutation=True,
            encoding=encoding,
        )


__all__ = ["FileOps", "edit", "read", "write"]
