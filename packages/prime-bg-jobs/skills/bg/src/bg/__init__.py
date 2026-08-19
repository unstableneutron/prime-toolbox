"""Background jobs for Prime Agent's single-threaded IPython kernel.

The kernel is the agent's only tool surface: a blocking cell (``time.sleep``,
``subprocess.run``, ``%%bash pnpm build``) pins the agent for the whole
duration. Hand the work to :func:`run` (subprocess) or :func:`call` (thread)
instead; the cell returns in milliseconds and the kernel goes idle while the
work continues.

Spawn, end your turn, and check back later with :func:`tail` and
:func:`result`. Never poll in a loop. Every wait here is bounded.
"""

from .api import call, clean, kill, list, result, run, tail, wait, write
from .job import Job, JobKind, JobState
from .limits import (
    DEFAULT_ESCALATE_SECONDS,
    DEFAULT_TAIL_CHARS,
    DEFAULT_WAIT_SECONDS,
    MAX_TAIL_CHARS,
    MAX_WAIT_SECONDS,
)
from .spool import SPOOL_DIR_ENV, spool_dir, spool_root

__all__ = [
    "DEFAULT_ESCALATE_SECONDS",
    "DEFAULT_TAIL_CHARS",
    "DEFAULT_WAIT_SECONDS",
    "MAX_TAIL_CHARS",
    "MAX_WAIT_SECONDS",
    "SPOOL_DIR_ENV",
    "Job",
    "JobKind",
    "JobState",
    "call",
    "clean",
    "kill",
    "list",
    "result",
    "run",
    "spool_dir",
    "spool_root",
    "tail",
    "wait",
    "write",
]
