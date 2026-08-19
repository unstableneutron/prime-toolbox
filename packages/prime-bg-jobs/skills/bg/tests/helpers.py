from __future__ import annotations

import os
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path

import bg
from bg import registry, spool


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Poll ``predicate`` in a test only; production code never polls."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class SpooledTestCase(unittest.TestCase):
    """Isolate every test in its own spool directory and registry."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.spool = Path(self.temp.name) / "spool"
        self._previous = os.environ.get(spool.SPOOL_DIR_ENV)
        os.environ[spool.SPOOL_DIR_ENV] = str(self.spool)
        registry.reset()

    def tearDown(self) -> None:
        for job in registry.jobs():
            if job.state == "running" and job.kind == "process":
                job.kill(escalate_after=1.0)
        registry.reset()
        if self._previous is None:
            os.environ.pop(spool.SPOOL_DIR_ENV, None)
        else:
            os.environ[spool.SPOOL_DIR_ENV] = self._previous
        self.temp.cleanup()

    def finished(self, job: bg.Job, timeout: float = 10.0) -> dict:
        """Wait for a short job with repeated bounded waits."""
        deadline = time.monotonic() + timeout
        snapshot = job.wait(1.0)
        while snapshot["state"] == "running" and time.monotonic() < deadline:
            snapshot = job.wait(1.0)
        return snapshot


__all__ = ["SpooledTestCase", "wait_for"]
