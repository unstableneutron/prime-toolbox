"""On-disk spool for job output and job metadata.

Output is spooled to files rather than in-memory pipes so a chatty job can
never deadlock on a full pipe buffer, and so output survives a kernel restart.
Every job also mirrors its metadata to a sibling JSON file, which is what makes
``bg.list()`` meaningful in a fresh kernel.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SPOOL_DIR_ENV = "PRIME_BG_DIR"
SPOOL_ROOT_NAME = "prime-bg-jobs"


def spool_root() -> Path:
    """Return the directory scanned when reattaching to earlier kernels."""
    override = os.environ.get(SPOOL_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path(tempfile.gettempdir()) / SPOOL_ROOT_NAME


def spool_dir() -> Path:
    """Return this kernel's spool directory without creating it."""
    override = os.environ.get(SPOOL_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return spool_root() / str(os.getpid())


def ensure_spool_dir() -> Path:
    """Create this kernel's spool directory lazily and return it."""
    directory = spool_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_meta(path: Path, meta: dict[str, Any]) -> None:
    """Mirror job metadata to ``path`` with a same-directory atomic replace."""
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(meta, indent=2, sort_keys=True, default=repr), encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)


def read_meta(path: Path) -> dict[str, Any] | None:
    """Return parsed job metadata, or ``None`` for unreadable or partial files."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
        return None
    return payload


def iter_meta_paths() -> Iterator[Path]:
    """Yield every job metadata file under the spool root, newest last."""
    root = spool_root()
    if not root.is_dir():
        return
    seen: set[Path] = set()
    for pattern in ("*.json", "*/*.json"):
        for path in sorted(root.glob(pattern)):
            if path.name.endswith(".json.tmp") or path in seen:
                continue
            seen.add(path)
            yield path


def pid_alive(pid: int | None) -> bool:
    """Return whether ``pid`` currently exists.

    This cannot detect pid reuse; a recycled pid is reported as alive.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


__all__ = [
    "SPOOL_DIR_ENV",
    "SPOOL_ROOT_NAME",
    "ensure_spool_dir",
    "iter_meta_paths",
    "pid_alive",
    "read_meta",
    "spool_dir",
    "spool_root",
    "write_meta",
]
