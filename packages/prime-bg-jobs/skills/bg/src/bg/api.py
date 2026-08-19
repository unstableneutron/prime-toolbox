"""The module-level :mod:`bg` API.

Every function here returns promptly. Nothing blocks the kernel unboundedly,
because the kernel is the agent's only tool surface.
"""

from __future__ import annotations

import builtins
import os
import signal
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import registry
from .job import Job
from .limits import DEFAULT_ESCALATE_SECONDS, DEFAULT_TAIL_CHARS, DEFAULT_WAIT_SECONDS


def run(
    command: str | Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    shell: bool = True,
    label: str | None = None,
) -> Job:
    """Start a shell command in the background and return immediately.

    The child is started in its own session (process group) with stdin as a
    pipe and stdout/stderr merged into a spool file, so output can neither
    deadlock on a full pipe nor be lost when the kernel restarts.

    Use this instead of ``%%bash long-command`` or ``subprocess.run(...)``:
    those pin the single-threaded kernel for the whole duration and the agent
    cannot answer anyone until they finish.

    Args:
        command: Shell string, or an argv sequence (which never uses a shell).
        cwd: Working directory for the child.
        env: Extra environment variables, merged over ``os.environ``.
        shell: Run a string command through the system shell.
        label: Short human label; defaults to the command text.

    Returns:
        A :class:`Job` handle, immediately, while the command keeps running.
    """
    return registry.register(Job.spawn_process(command, cwd=cwd, env=env, shell=shell, label=label))


def call(target: Callable[..., Any], *args: Any, label: str | None = None, **kwargs: Any) -> Job:
    """Run a Python callable on a daemon thread and return immediately.

    Use it for heavy in-kernel work (a long parse, an embedding pass, a slow
    client call). The return value and any exception with its traceback are
    captured; read them with :func:`result`.

    A callable that holds the GIL in pure Python still competes with the
    kernel; prefer :func:`run` for genuinely CPU-bound work.

    Note:
        ``label`` is consumed by ``bg``; a target keyword named ``label``
        cannot be passed through. Use ``functools.partial`` in that case.
    """
    return registry.register(Job.spawn_call(target, args, kwargs, label=label))


def list(include_done: bool = True) -> builtins.list[dict[str, Any]]:
    """List known jobs as compact rows, oldest first.

    Rows contain ``id``, ``label``, ``kind``, ``state``, ``elapsed``,
    ``exit_code`` and ``out_bytes``. Jobs spooled by an earlier kernel are
    included; those whose process is gone appear as ``state="orphaned"``.
    """
    rows = [job.summary() for job in registry.jobs()]
    if include_done:
        return rows
    return [row for row in rows if row["state"] == "running"]


def tail(
    job: Job | str, *, since: int = 0, max_chars: int | None = DEFAULT_TAIL_CHARS
) -> dict[str, Any]:
    """Read spooled output incrementally from byte offset ``since``.

    Returns ``{"text", "offset", "eof", "truncated", "state", "id"}``. Pass the
    returned ``offset`` back as ``since`` on the next turn to read only what is
    new; two tails never re-read the same bytes.
    """
    return registry.resolve(job).tail(since=since, max_chars=max_chars)


def write(job: Job | str, data: str | bytes, *, close: bool = False) -> int:
    """Write ``data`` to a :func:`run` job's stdin and return bytes written.

    Include the trailing newline the program is waiting for. Pass
    ``close=True`` to send EOF after the data. Raises ``ValueError`` for
    :func:`call` jobs, reattached jobs, and jobs no longer reading stdin.
    """
    return registry.resolve(job).write(data, close=close)


def wait(job: Job | str, timeout: float | None = DEFAULT_WAIT_SECONDS) -> dict[str, Any]:
    """Wait at most ``timeout`` seconds (capped at 60) for a job to finish.

    A returned ``state="running"`` is a normal, successful outcome, not an
    error: it means the job is still working. The correct follow-up is to end
    your turn and check again on a later turn, not to loop or re-wait until
    done. Set an ``rlm_heartbeat`` if you need to be woken up.

    Returns the :func:`result` snapshot plus ``waited`` and ``timed_out``.
    """
    return registry.resolve(job).wait(timeout)


def kill(
    job: Job | str,
    *,
    sig: int = signal.SIGTERM,
    escalate_after: float | None = DEFAULT_ESCALATE_SECONDS,
) -> dict[str, Any]:
    """Signal a :func:`run` job's whole process group, escalating to SIGKILL.

    The child was started with its own session, so the signal reaches its
    descendants too. Waits at most ``escalate_after`` seconds (capped at 10)
    for a clean exit before sending ``SIGKILL``. ``call()`` jobs cannot be
    killed; the snapshot says so instead of raising.
    """
    return registry.resolve(job).kill(sig=sig, escalate_after=escalate_after)


def result(job: Job | str) -> dict[str, Any]:
    """Return a job snapshot without waiting at all.

    Includes ``state``, ``exit_code``, ``elapsed``, ``out_bytes``, the
    ``value`` of a finished :func:`call` job, and ``error`` with a captured
    traceback when one failed.
    """
    return registry.resolve(job).snapshot()


def clean(*, keep_running: bool = True) -> int:
    """Drop finished jobs and delete their spool files; return how many.

    With ``keep_running=False``, running :func:`run` jobs are killed first;
    running :func:`call` jobs are only forgotten, since their thread cannot be
    interrupted.
    """
    removed = 0
    for job in registry.jobs():
        if job.state == "running":
            if keep_running:
                continue
            if job.kind == "process":
                job.kill()
        registry.drop(job)
        job.discard_files()
        removed += 1
    return removed


__all__ = [
    "call",
    "clean",
    "kill",
    "list",
    "result",
    "run",
    "tail",
    "wait",
    "write",
]
