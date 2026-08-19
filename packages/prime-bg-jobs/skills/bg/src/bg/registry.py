"""Thread-safe job registry with spool reattachment."""

from __future__ import annotations

import threading

from .job import Job
from .spool import iter_meta_paths, read_meta

_LOCK = threading.RLock()
_JOBS: dict[str, Job] = {}
_REATTACHED = False


def register(job: Job) -> Job:
    """Add a freshly spawned job to the registry."""
    with _LOCK:
        _JOBS[job.id] = job
    return job


def reattach(*, force: bool = False) -> int:
    """Adopt jobs spooled by this or an earlier kernel; return how many were new."""
    global _REATTACHED
    with _LOCK:
        if _REATTACHED and not force:
            return 0
        _REATTACHED = True
    added = 0
    for path in iter_meta_paths():
        meta = read_meta(path)
        if meta is None:
            continue
        with _LOCK:
            if meta["id"] in _JOBS:
                continue
        job = Job.from_meta(meta, path)
        with _LOCK:
            if job.id not in _JOBS:
                _JOBS[job.id] = job
                added += 1
    return added


def resolve(ref: Job | str) -> Job:
    """Return the job named by a :class:`Job` handle or a job id string."""
    if isinstance(ref, Job):
        return ref
    if not isinstance(ref, str):
        raise TypeError("job must be a Job or a job id string")
    reattach()
    with _LOCK:
        job = _JOBS.get(ref)
    if job is None:
        raise LookupError(f"unknown job id {ref!r}")
    return job


def jobs() -> list[Job]:
    """Return every known job, oldest first."""
    reattach()
    with _LOCK:
        return sorted(_JOBS.values(), key=lambda job: (job.created_at, job.id))


def drop(job: Job) -> None:
    """Forget a job without touching its spool files."""
    with _LOCK:
        _JOBS.pop(job.id, None)


def reset() -> None:
    """Forget every job and re-arm reattachment. Intended for tests."""
    global _REATTACHED
    with _LOCK:
        _JOBS.clear()
        _REATTACHED = False


__all__ = ["drop", "jobs", "reattach", "register", "reset", "resolve"]
