from __future__ import annotations

import os
import signal
import time
import unittest
from typing import Any, cast

from helpers import SpooledTestCase, wait_for

import bg
from bg.limits import MAX_WAIT_SECONDS, clamp_timeout


class BoundedWaitTests(SpooledTestCase):
    def test_wait_timeout_returns_running_fast(self):
        job = bg.run("sleep 5")
        started = time.monotonic()
        snapshot = bg.wait(job, timeout=0.2)
        waited = time.monotonic() - started

        self.assertEqual(snapshot["state"], "running")
        self.assertTrue(snapshot["timed_out"])
        self.assertFalse(snapshot["done"])
        self.assertLess(waited, 1.5, "wait() must return long before the job finishes")
        self.assertLess(snapshot["waited"], 1.5)
        bg.kill(job)

    def test_zero_timeout_is_a_pure_poll(self):
        job = bg.run("sleep 5")
        started = time.monotonic()
        snapshot = bg.wait(job, timeout=0)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(snapshot["state"], "running")
        bg.kill(job)

    def test_result_never_waits(self):
        job = bg.run("sleep 5")
        started = time.monotonic()
        snapshot = bg.result(job)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(snapshot["state"], "running")
        self.assertIsNone(snapshot["exit_code"])
        bg.kill(job)

    def test_timeouts_are_capped_and_validated(self):
        self.assertEqual(clamp_timeout(3_600), MAX_WAIT_SECONDS)
        self.assertEqual(clamp_timeout(None), 5.0)
        self.assertEqual(clamp_timeout(0.25), 0.25)
        with self.assertRaises(ValueError):
            clamp_timeout(-1)
        with self.assertRaises(TypeError):
            clamp_timeout(cast(Any, "30"))


class KillTests(SpooledTestCase):
    def test_kill_terminates_the_sleeper_and_its_child(self):
        job = bg.run("sleep 30 & echo child:$!; wait")
        self.assertTrue(wait_for(lambda: "child:" in bg.tail(job)["text"]))
        child = int(bg.tail(job)["text"].split("child:")[1].split()[0])
        self.assertTrue(self._alive(child))

        snapshot = bg.kill(job)
        self.assertTrue(snapshot["killed"])
        self.assertIn(snapshot["state"], {"killed", "failed"})
        self.assertFalse(snapshot["timed_out"])
        self.assertTrue(wait_for(lambda: not self._alive(child), timeout=5.0))
        self.assertFalse(self._alive(job.pid))

    def test_kill_accepts_an_explicit_signal(self):
        job = bg.run("sleep 30")
        snapshot = bg.kill(job, sig=signal.SIGKILL, escalate_after=1.0)
        self.assertTrue(snapshot["killed"])
        self.assertEqual(snapshot["state"], "killed")
        self.assertFalse(snapshot["escalated"])

    def test_kill_of_a_finished_job_is_a_no_op(self):
        job = bg.run("echo quick")
        self.finished(job)
        snapshot = bg.kill(job)
        self.assertFalse(snapshot["killed"])
        self.assertEqual(snapshot["state"], "done")

    def test_kill_of_a_call_job_explains_itself(self):
        job = bg.call(time.sleep, 0.2)
        snapshot = bg.kill(job)
        self.assertFalse(snapshot["killed"])
        self.assertIn("cannot be interrupted", snapshot["message"])
        self.finished(job)

    @staticmethod
    def _alive(pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


if __name__ == "__main__":
    unittest.main()
