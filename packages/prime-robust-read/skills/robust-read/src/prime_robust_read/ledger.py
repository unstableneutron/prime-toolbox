"""Session-local read ledger used by guarded mutation helpers."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Metadata sufficient for practical same-session stale-read detection."""

    device: int
    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileIdentity:
        return cls(value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)

    def as_dict(self) -> dict[str, int]:
        return {
            "device": self.device,
            "inode": self.inode,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    canonical_path: str
    identity: FileIdentity
    complete: bool


class ReadLedger:
    """Thread-safe in-memory ledger.

    A ledger is scoped to its Python process or to the explicit instance a
    caller passes. It intentionally does not intercept arbitrary Python,
    shell, or IPython filesystem operations.
    """

    def __init__(self) -> None:
        self._entries: dict[str, LedgerEntry] = {}
        self._lock = threading.RLock()

    def get(self, path: str | os.PathLike[str]) -> LedgerEntry | None:
        key = str(Path(path))
        with self._lock:
            return self._entries.get(key)

    def record(
        self,
        path: str | os.PathLike[str],
        identity: FileIdentity,
        *,
        complete: bool,
    ) -> LedgerEntry:
        entry = LedgerEntry(str(Path(path)), identity, complete)
        with self._lock:
            self._entries[entry.canonical_path] = entry
        return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


DEFAULT_LEDGER = ReadLedger()

__all__ = ["DEFAULT_LEDGER", "FileIdentity", "LedgerEntry", "ReadLedger"]
