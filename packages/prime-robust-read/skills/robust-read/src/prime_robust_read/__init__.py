"""Production-quality bounded reading for Prime Agent and IPython."""

from .ledger import DEFAULT_LEDGER, FileIdentity, LedgerEntry, ReadLedger
from .limits import DEFAULT_LIMITS, ReadLimits
from .mutation import safe_edit, safe_write
from .reader import read
from .types import MutationResult, ReadResult

__all__ = [
    "DEFAULT_LEDGER",
    "DEFAULT_LIMITS",
    "FileIdentity",
    "LedgerEntry",
    "MutationResult",
    "ReadLedger",
    "ReadLimits",
    "ReadResult",
    "read",
    "safe_edit",
    "safe_write",
]
