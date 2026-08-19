from __future__ import annotations

import time
import unittest
from typing import Any, cast

from helpers import SpooledTestCase

import bg


def _heavy(rounds: int, *, factor: int = 2) -> dict[str, int]:
    total = sum(index * factor for index in range(rounds))
    return {"rounds": rounds, "total": total}


def _explode(message: str) -> None:
    raise KeyError(message)


class CallJobTests(SpooledTestCase):
    def test_call_returns_immediately_and_captures_the_return_value(self):
        started = time.monotonic()
        job = bg.call(_heavy, 1_000, factor=3, label="heavy sum")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(job.kind, "call")
        self.assertEqual(job.label, "heavy sum")

        snapshot = self.finished(job)
        self.assertEqual(snapshot["state"], "done")
        self.assertEqual(snapshot["exit_code"], 0)
        self.assertEqual(snapshot["value"], {"rounds": 1_000, "total": 1_498_500})
        self.assertEqual(bg.result(job)["value"], snapshot["value"])

    def test_call_captures_the_exception_and_its_traceback(self):
        job = bg.call(_explode, "missing-key")
        snapshot = self.finished(job)

        self.assertEqual(snapshot["state"], "failed")
        self.assertEqual(snapshot["exit_code"], 1)
        self.assertIsNone(snapshot["value"])
        self.assertEqual(snapshot["error"]["type"], "KeyError")
        self.assertIn("missing-key", snapshot["error"]["message"])
        self.assertIn("Traceback", snapshot["error"]["traceback"])
        self.assertIn("_explode", bg.tail(job)["text"])

    def test_call_label_defaults_to_the_qualified_target_name(self):
        job = bg.call(_heavy, 1)
        self.finished(job)
        self.assertIn("_heavy", job.label)

    def test_call_requires_a_callable(self):
        with self.assertRaises(TypeError):
            bg.call(cast(Any, "not callable"))


if __name__ == "__main__":
    unittest.main()
