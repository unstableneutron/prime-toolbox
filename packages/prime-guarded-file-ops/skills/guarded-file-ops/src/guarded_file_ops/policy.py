"""Immutable policy configuration for scoped file operations."""

from __future__ import annotations

from dataclasses import dataclass

from .limits import DEFAULT_LIMITS, ReadLimits


@dataclass(frozen=True, slots=True)
class FileOpsPolicy:
    """Immutable configuration for one :class:`FileOps` instance.

    ``limits`` bounds reads and edits, ``use_fff`` enables optional fuzzy path
    recovery, and ``allow_mutation=False`` makes the instance read-only.
    """

    limits: ReadLimits = DEFAULT_LIMITS
    use_fff: bool = True
    allow_mutation: bool = True

    def validate(self) -> FileOpsPolicy:
        if not isinstance(self.limits, ReadLimits):
            raise TypeError("limits must be a ReadLimits instance")
        self.limits.validate()
        if not isinstance(self.use_fff, bool):
            raise TypeError("use_fff must be a bool")
        if not isinstance(self.allow_mutation, bool):
            raise TypeError("allow_mutation must be a bool")
        return self


__all__ = ["FileOpsPolicy"]
