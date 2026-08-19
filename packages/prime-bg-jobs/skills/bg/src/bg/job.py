"""The :class:`Job` handle and its bounded, non-blocking operations."""

from __future__ import annotations

import contextlib
import itertools
import os
import signal
import subprocess
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from .limits import (
    DEFAULT_ESCALATE_SECONDS,
    DEFAULT_TAIL_CHARS,
    DEFAULT_WAIT_SECONDS,
    MAX_KILL_GRACE_SECONDS,
    POLL_INTERVAL_SECONDS,
    clamp_tail_chars,
    clamp_timeout,
)
from .spool import ensure_spool_dir, pid_alive, write_meta

JobKind = Literal["process", "call"]
JobState = Literal["running", "done", "failed", "killed", "orphaned"]

TERMINAL_STATES: frozenset[str] = frozenset({"done", "failed", "killed", "orphaned"})

_ID_LOCK = threading.Lock()
_ID_COUNTER = itertools.count(1)


def next_job_id(prefix: str) -> str:
    """Return a spool-unique, human-readable job id such as ``sh3-9f2a1c``."""
    with _ID_LOCK:
        index = next(_ID_COUNTER)
    return f"{prefix}{index}-{uuid.uuid4().hex[:6]}"


def _describe(target: Callable[..., Any]) -> str:
    module = getattr(target, "__module__", None)
    name = getattr(target, "__qualname__", None) or repr(target)
    return f"{module}.{name}" if module else str(name)


def _signal_group(pid: int | None, sig: int) -> bool:
    """Signal the whole process group of ``pid``; fall back to the pid alone."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        group = os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        group = None
    if group is not None:
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            pass
        except OSError:
            group = None
        else:
            return True
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


class Job:
    """A handle to one background process or one background Python call.

    A ``Job`` is returned immediately by :func:`bg.run` and :func:`bg.call`.
    Nothing on this class waits without a bounded timeout.
    """

    def __init__(
        self,
        job_id: str,
        *,
        label: str,
        kind: JobKind,
        out_file: Path,
        meta_file: Path,
        command: str | None = None,
        cwd: str | None = None,
        pid: int | None = None,
        kernel_pid: int | None = None,
        created_at: float | None = None,
        foreign: bool = False,
    ) -> None:
        self.id = job_id
        self.label = label
        self.kind: JobKind = kind
        self.command = command
        self.cwd = cwd
        self.out_file = out_file
        self.meta_file = meta_file
        self.pid = pid
        self.kernel_pid = kernel_pid if kernel_pid is not None else os.getpid()
        self.created_at = float(created_at if created_at is not None else time.time())
        self.ended_at: float | None = None
        self.exit_code: int | None = None
        self.value: Any = None
        self.error: dict[str, str] | None = None
        self.foreign = foreign
        self._state: JobState = "running"
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._kill_requested = False

    # ------------------------------------------------------------------ spawn

    @classmethod
    def spawn_process(
        cls,
        command: str | Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        shell: bool = True,
        label: str | None = None,
    ) -> Job:
        """Start ``command`` in its own session and return at once."""
        if isinstance(command, str):
            use_shell = bool(shell)
            printable = command
        elif isinstance(command, Sequence):
            command = [str(part) for part in command]
            use_shell = False
            printable = " ".join(command)
        else:
            raise TypeError("command must be a string or a sequence of strings")
        if not printable.strip():
            raise ValueError("command must not be empty")

        directory = ensure_spool_dir()
        job_id = next_job_id("sh")
        job = cls(
            job_id,
            label=label or printable[:80],
            kind="process",
            out_file=directory / f"{job_id}.out",
            meta_file=directory / f"{job_id}.json",
            command=printable,
            cwd=str(cwd) if cwd is not None else os.getcwd(),
        )
        environment = None
        if env is not None:
            environment = {**os.environ, **{str(k): str(v) for k, v in env.items()}}

        handle = job.out_file.open("wb")
        try:
            job._process = subprocess.Popen(
                command,
                shell=use_shell,
                cwd=str(cwd) if cwd is not None else None,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            handle.close()
        job.pid = job._process.pid
        job._write_meta()
        threading.Thread(target=job._watch_process, name=f"bg-watch-{job_id}", daemon=True).start()
        return job

    @classmethod
    def spawn_call(
        cls,
        target: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        label: str | None = None,
    ) -> Job:
        """Run ``target`` on a daemon thread and return at once."""
        if not callable(target):
            raise TypeError("target must be callable")
        directory = ensure_spool_dir()
        job_id = next_job_id("py")
        job = cls(
            job_id,
            label=label or _describe(target),
            kind="call",
            out_file=directory / f"{job_id}.out",
            meta_file=directory / f"{job_id}.json",
            command=_describe(target),
            cwd=os.getcwd(),
            pid=os.getpid(),
        )
        job.out_file.touch()
        job._write_meta()
        threading.Thread(
            target=job._run_call,
            args=(target, args, kwargs),
            name=f"bg-call-{job_id}",
            daemon=True,
        ).start()
        return job

    @classmethod
    def from_meta(cls, meta: dict[str, Any], meta_file: Path) -> Job:
        """Rebuild a read-only handle for a job spooled by an earlier kernel."""
        kind: JobKind = "call" if meta.get("kind") == "call" else "process"
        out_file = meta.get("out_file")
        job = cls(
            str(meta["id"]),
            label=str(meta.get("label") or meta["id"]),
            kind=kind,
            out_file=Path(out_file) if out_file else meta_file.with_suffix(".out"),
            meta_file=meta_file,
            command=meta.get("command"),
            cwd=meta.get("cwd"),
            pid=meta.get("pid") if isinstance(meta.get("pid"), int) else None,
            kernel_pid=meta.get("kernel_pid") if isinstance(meta.get("kernel_pid"), int) else None,
            created_at=meta.get("created_at"),
            foreign=True,
        )
        job.ended_at = meta.get("ended_at")
        job.exit_code = meta.get("exit_code") if isinstance(meta.get("exit_code"), int) else None
        error = meta.get("error")
        job.error = error if isinstance(error, dict) else None
        recorded = meta.get("state")
        if recorded in TERMINAL_STATES:
            job._state = recorded  # type: ignore[assignment]
            job._done.set()
        else:
            owner = job.pid if kind == "process" else job.kernel_pid
            if pid_alive(owner):
                job._state = "running"
            else:
                job._state = "orphaned"
                job._done.set()
        return job

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> JobState:
        """Current job state, refreshed for reattached jobs."""
        with self._lock:
            if self.foreign and self._state == "running":
                owner = self.pid if self.kind == "process" else self.kernel_pid
                if not pid_alive(owner):
                    self._state = "orphaned"
                    self.ended_at = self.ended_at or time.time()
                    self._done.set()
            return self._state

    @property
    def done(self) -> bool:
        """Whether the job has reached a terminal state."""
        return self.state in TERMINAL_STATES

    @property
    def elapsed(self) -> float:
        """Seconds between start and completion, or start and now."""
        end = self.ended_at if self.ended_at is not None else time.time()
        return round(max(0.0, end - self.created_at), 3)

    @property
    def out_bytes(self) -> int:
        """Bytes currently spooled for this job."""
        try:
            return self.out_file.stat().st_size
        except OSError:
            return 0

    def _finish(
        self,
        state: JobState,
        *,
        exit_code: int | None = None,
        value: Any = None,
        error: dict[str, str] | None = None,
    ) -> None:
        with self._lock:
            if self._state in TERMINAL_STATES:
                return
            self._state = state
            self.exit_code = exit_code
            self.value = value
            self.error = error
            self.ended_at = time.time()
        self._write_meta()
        self._done.set()

    def _watch_process(self) -> None:
        process = self._process
        assert process is not None
        returncode = process.wait()
        stdin = process.stdin
        if stdin is not None and not stdin.closed:
            with contextlib.suppress(OSError):
                stdin.close()
        if self._kill_requested:
            state: JobState = "killed"
        elif returncode == 0:
            state = "done"
        else:
            state = "failed"
        self._finish(state, exit_code=returncode)

    def _run_call(
        self, target: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        try:
            value = target(*args, **kwargs)
        except BaseException as exc:
            text = traceback.format_exc()
            self._append(text)
            self._finish(
                "failed",
                exit_code=1,
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": text,
                },
            )
        else:
            self._finish("done", exit_code=0, value=value)

    def _append(self, text: str) -> None:
        try:
            with self.out_file.open("ab") as handle:
                handle.write(text.encode("utf-8", errors="replace"))
        except OSError:
            pass

    def _meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "state": self._state,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.pid,
            "kernel_pid": self.kernel_pid,
            "created_at": self.created_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "out_file": str(self.out_file),
        }
        if self.error is not None:
            meta["error"] = self.error
        if self._state == "done" and self.kind == "call":
            meta["value_repr"] = repr(self.value)[:2_000]
        return meta

    def _write_meta(self) -> None:
        if self.foreign:
            return
        write_meta(self.meta_file, self._meta())

    # ------------------------------------------------------------------- read

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-friendly view of this job, including any result."""
        state = self.state
        snapshot: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "state": state,
            "elapsed": self.elapsed,
            "exit_code": self.exit_code,
            "out_bytes": self.out_bytes,
            "pid": self.pid,
            "command": self.command,
            "out_file": str(self.out_file),
            "done": state in TERMINAL_STATES,
        }
        if self.kind == "call":
            snapshot["value"] = self.value if state == "done" else None
        if self.error is not None:
            snapshot["error"] = self.error
        return snapshot

    def summary(self) -> dict[str, Any]:
        """Return the compact row used by :func:`bg.list`."""
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "state": self.state,
            "elapsed": self.elapsed,
            "exit_code": self.exit_code,
            "out_bytes": self.out_bytes,
        }

    def tail(self, *, since: int = 0, max_chars: int | None = DEFAULT_TAIL_CHARS) -> dict[str, Any]:
        """Read spooled output from byte ``since``; see :func:`bg.tail`."""
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise ValueError("since must be a non-negative byte offset")
        limit = clamp_tail_chars(max_chars)
        size = self.out_bytes
        offset = min(since, size)
        text = ""
        if offset < size:
            try:
                with self.out_file.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read(limit)
            except OSError:
                chunk = b""
            while chunk:
                try:
                    text = chunk.decode("utf-8")
                except UnicodeDecodeError as exc:
                    if exc.end >= len(chunk) and exc.start > 0:
                        chunk = chunk[: exc.start]
                        continue
                    text = chunk.decode("utf-8", errors="replace")
                break
            offset += len(chunk)
        state = self.state
        return {
            "id": self.id,
            "text": text,
            "offset": offset,
            "eof": state in TERMINAL_STATES and offset >= self.out_bytes,
            "truncated": offset < size,
            "state": state,
        }

    # ------------------------------------------------------------------ write

    def write(self, data: str | bytes, *, close: bool = False) -> int:
        """Send ``data`` to the job's stdin; see :func:`bg.write`."""
        if self.kind != "process":
            raise ValueError(f"job {self.id} is a call() job and has no stdin")
        process = self._process
        if process is None or process.stdin is None or process.stdin.closed:
            raise ValueError(f"job {self.id} has no writable stdin (reattached or already closed)")
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        try:
            written = process.stdin.write(payload)
            process.stdin.flush()
            if close:
                process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            raise ValueError(f"job {self.id} is no longer reading stdin") from exc
        return int(written if written is not None else len(payload))

    # ------------------------------------------------------------------- wait

    def _wait_bounded(self, seconds: float) -> bool:
        if self.foreign:
            deadline = time.monotonic() + seconds
            while self.state not in TERMINAL_STATES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(POLL_INTERVAL_SECONDS, remaining))
            return True
        return self._done.wait(seconds)

    def wait(self, timeout: float | None = DEFAULT_WAIT_SECONDS) -> dict[str, Any]:
        """Wait at most ``timeout`` seconds; see :func:`bg.wait`."""
        seconds = clamp_timeout(timeout)
        started = time.monotonic()
        self._wait_bounded(seconds)
        snapshot = self.snapshot()
        snapshot["waited"] = round(time.monotonic() - started, 3)
        snapshot["timed_out"] = not snapshot["done"]
        return snapshot

    # ------------------------------------------------------------------- kill

    def kill(
        self,
        *,
        sig: int = signal.SIGTERM,
        escalate_after: float | None = DEFAULT_ESCALATE_SECONDS,
    ) -> dict[str, Any]:
        """Signal the job's process group; see :func:`bg.kill`."""
        grace = clamp_timeout(
            escalate_after,
            default=DEFAULT_ESCALATE_SECONDS,
            maximum=MAX_KILL_GRACE_SECONDS,
        )
        snapshot = self.snapshot()
        if self.kind == "call":
            snapshot["killed"] = False
            snapshot["message"] = (
                "call() jobs run on daemon threads and cannot be interrupted; "
                "use run() for work that must be cancellable"
            )
            return snapshot
        if snapshot["done"]:
            snapshot["killed"] = False
            snapshot["message"] = f"job {self.id} already finished"
            return snapshot

        self._kill_requested = True
        signalled = _signal_group(self.pid, sig)
        finished = self._wait_bounded(grace)
        escalated = False
        if not finished:
            escalated = True
            _signal_group(self.pid, signal.SIGKILL)
            finished = self._wait_bounded(min(grace, 5.0))
        snapshot = self.snapshot()
        snapshot["killed"] = signalled
        snapshot["escalated"] = escalated
        snapshot["timed_out"] = not finished
        return snapshot

    def discard_files(self) -> None:
        """Remove this job's spool files."""
        for path in (self.out_file, self.meta_file):
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)

    def __repr__(self) -> str:
        return (
            f"<Job {self.id} {self.kind} state={self.state} "
            f"elapsed={self.elapsed}s label={self.label[:40]!r}>"
        )


__all__ = ["TERMINAL_STATES", "Job", "JobKind", "JobState", "next_job_id"]
