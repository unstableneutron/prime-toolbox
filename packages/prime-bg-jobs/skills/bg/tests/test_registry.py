from __future__ import annotations

import json
import os
import unittest
from typing import Any, cast

from helpers import SpooledTestCase, wait_for

import bg
from bg import registry


class ListAndCleanTests(SpooledTestCase):
    def test_list_reports_every_job_and_can_hide_finished_ones(self):
        quick = bg.run("echo quick")
        self.finished(quick)
        slow = bg.run("sleep 5")

        rows = bg.list()
        self.assertEqual(
            sorted(rows[0]),
            ["elapsed", "exit_code", "id", "kind", "label", "out_bytes", "state"],
        )
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id[quick.id]["state"], "done")
        self.assertEqual(by_id[quick.id]["exit_code"], 0)
        self.assertGreater(by_id[quick.id]["out_bytes"], 0)
        self.assertEqual(by_id[slow.id]["state"], "running")

        running = bg.list(include_done=False)
        self.assertEqual([row["id"] for row in running], [slow.id])
        bg.kill(slow)

    def test_clean_drops_finished_jobs_and_their_spool_files(self):
        quick = bg.run("echo quick")
        self.finished(quick)
        slow = bg.run("sleep 5")

        removed = bg.clean()
        self.assertEqual(removed, 1)
        self.assertFalse(quick.out_file.exists())
        self.assertFalse(quick.meta_file.exists())
        self.assertEqual([row["id"] for row in bg.list()], [slow.id])

        self.assertEqual(bg.clean(keep_running=False), 1)
        self.assertEqual(bg.list(), [])
        self.assertTrue(wait_for(lambda: slow.done))

    def test_labels_are_kept_for_routing_output_back_to_intent(self):
        job = bg.run("echo x", label="docs build")
        self.finished(job)
        self.assertEqual(bg.list()[0]["label"], "docs build")


class JobReferenceTests(SpooledTestCase):
    def test_every_function_accepts_a_job_id_string(self):
        job = bg.run("read -r line; echo got:$line")
        job_id = job.id
        self.assertIsInstance(job_id, str)

        self.assertEqual(bg.write(job_id, "value\n"), 6)
        self.assertTrue(wait_for(lambda: "got:value" in bg.tail(job_id)["text"]))
        self.assertEqual(bg.wait(job_id, 2.0)["id"], job_id)
        self.assertEqual(bg.result(job_id)["state"], "done")
        self.assertTrue(bg.tail(job_id, since=0)["eof"])
        self.assertFalse(bg.kill(job_id)["killed"])

    def test_unknown_and_mistyped_references_are_rejected(self):
        with self.assertRaises(LookupError):
            bg.result("no-such-job")
        with self.assertRaises(TypeError):
            bg.result(cast(Any, 7))


class ReattachTests(SpooledTestCase):
    def test_metadata_survives_a_kernel_restart(self):
        job = bg.run("echo persisted")
        self.finished(job)
        meta = json.loads(job.meta_file.read_text(encoding="utf-8"))
        self.assertEqual(meta["id"], job.id)
        self.assertEqual(meta["state"], "done")

        registry.reset()
        rows = {row["id"]: row for row in bg.list()}
        self.assertIn(job.id, rows)
        self.assertEqual(rows[job.id]["state"], "done")
        self.assertEqual(rows[job.id]["exit_code"], 0)
        self.assertIn("persisted", bg.tail(job.id)["text"])

    def test_a_job_whose_process_is_gone_is_orphaned(self):
        self.spool.mkdir(parents=True, exist_ok=True)
        dead_pid = self._dead_pid()
        (self.spool / "sh99-deadbe.out").write_text("partial output\n", encoding="utf-8")
        (self.spool / "sh99-deadbe.json").write_text(
            json.dumps(
                {
                    "id": "sh99-deadbe",
                    "label": "stale build",
                    "kind": "process",
                    "state": "running",
                    "command": "pnpm build",
                    "pid": dead_pid,
                    "kernel_pid": dead_pid,
                    "created_at": 0.0,
                    "out_file": str(self.spool / "sh99-deadbe.out"),
                }
            ),
            encoding="utf-8",
        )
        registry.reset()

        rows = {row["id"]: row for row in bg.list()}
        self.assertEqual(rows["sh99-deadbe"]["state"], "orphaned")
        self.assertIn("partial output", bg.tail("sh99-deadbe")["text"])
        self.assertEqual(bg.wait("sh99-deadbe", 0.1)["state"], "orphaned")
        self.assertEqual(bg.clean(), 1)

    @staticmethod
    def _dead_pid() -> int:
        for candidate in range(99_000, 99_500):
            try:
                os.kill(candidate, 0)
            except ProcessLookupError:
                return candidate
            except OSError:
                continue
        raise unittest.SkipTest("no free pid found")


if __name__ == "__main__":
    unittest.main()
