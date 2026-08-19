"""Guarded local file operations for LLM agents.

Use :func:`read` for bounded text, directory, notebook, PDF, and structured
document extraction. Use :func:`write` to create a file or replace an observed
version, and :func:`edit` for version-checked exact text replacement. Construct
:class:`FileOps` only when a task needs root scoping or custom policy.
"""

from .ledger import FileVersion
from .limits import DEFAULT_LIMITS, ReadLimits
from .operations import FileOps, edit, read, write
from .policy import FileOpsPolicy
from .types import MutationResult, ReadResult

__all__ = [
    "DEFAULT_LIMITS",
    "FileOps",
    "FileOpsPolicy",
    "FileVersion",
    "MutationResult",
    "ReadLimits",
    "ReadResult",
    "edit",
    "read",
    "write",
]
