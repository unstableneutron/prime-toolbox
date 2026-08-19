from __future__ import annotations

import os
import time
import unittest
from typing import Any, cast

from helpers import SpooledTestCase, wait_for

import bg


class ProcessJobTests(SpooledTestCase):
    def test_run_returns_immediately_and_reports_exit_code(self):
        started = time.monotonic()
        job = bg.run("sleep 0.2; echo done; exit 0")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(job.state, "running")

        snapshot = self.finished(job)
        self.assertEqual(snapshot["state"], "done")
        self.assertEqual(snapshot["exit_code"], 0)
        self.assertIn("done", bg.tail(job)["text"])

    def test_failing_command_is_failed_with_its_exit_code(self):
        job = bg.run("echo boom >&2; exit 3")
        snapshot = self.finished(job)
        self.assertEqual(snapshot["state"], "failed")
        self.assertEqual(snapshot["exit_code"], 3)
        self.assertIn("boom", bg.tail(job)["text"])

    def test_spool_files_live_under_the_configured_directory(self):
        job = bg.run("echo hello")
        self.finished(job)
        self.assertEqual(job.out_file.parent, self.spool)
        self.assertTrue(job.meta_file.is_file())
        self.assertEqual(bg.spool_dir(), self.spool)

    def test_argv_sequence_never_uses_a_shell(self):
        job = bg.run(["printf", "%s", "literal $HOME"], shell=True)
        self.finished(job)
        self.assertEqual(bg.tail(job)["text"], "literal $HOME")

    def test_environment_and_cwd_reach_the_child(self):
        job = bg.run("echo $BG_TOKEN; pwd", cwd=self.spool, env={"BG_TOKEN": "abc123"})
        self.finished(job)
        text = bg.tail(job)["text"]
        self.assertIn("abc123", text)
        self.assertIn(os.path.realpath(self.spool), os.path.realpath(text.strip().splitlines()[-1]))

    def test_empty_command_is_a_programmer_error(self):
        with self.assertRaises(ValueError):
            bg.run("   ")
        with self.assertRaises(TypeError):
            bg.run(cast(Any, 42))


class TailTests(SpooledTestCase):
    def test_two_tails_do_not_re_read_the_same_bytes(self):
        job = bg.run("echo first; sleep 0.3; echo second")
        self.assertTrue(wait_for(lambda: "first" in bg.tail(job)["text"]))

        first = bg.tail(job)
        self.assertIn("first", first["text"])
        self.assertGreater(first["offset"], 0)

        self.finished(job)
        second = bg.tail(job, since=first["offset"])
        self.assertNotIn("first", second["text"])
        self.assertIn("second", second["text"])
        self.assertTrue(second["eof"])
        self.assertGreater(second["offset"], first["offset"])

        third = bg.tail(job, since=second["offset"])
        self.assertEqual(third["text"], "")
        self.assertEqual(third["offset"], second["offset"])
        self.assertTrue(third["eof"])

    def test_max_chars_truncates_and_reports_more_to_read(self):
        job = bg.run("printf 'abcdefghij'")
        self.finished(job)
        page = bg.tail(job, max_chars=4)
        self.assertEqual(page["text"], "abcd")
        self.assertEqual(page["offset"], 4)
        self.assertTrue(page["truncated"])
        self.assertFalse(page["eof"])
        rest = bg.tail(job, since=page["offset"])
        self.assertEqual(rest["text"], "efghij")
        self.assertTrue(rest["eof"])

    def test_multibyte_output_is_never_split_into_mojibake(self):
        job = bg.run("printf 'ünïcödé'")
        self.finished(job)
        collected = ""
        offset = 0
        for _ in range(20):
            page = bg.tail(job, since=offset, max_chars=3)
            collected += page["text"]
            if page["offset"] == offset:
                break
            offset = page["offset"]
        self.assertEqual(collected, "ünïcödé")
        self.assertNotIn("\ufffd", collected)

    def test_invalid_offset_is_a_programmer_error(self):
        job = bg.run("echo x")
        self.finished(job)
        with self.assertRaises(ValueError):
            bg.tail(job, since=-1)


class StdinTests(SpooledTestCase):
    def test_write_answers_an_interactive_prompt(self):
        job = bg.run("read -r name; echo hello:$name; read -r other; echo bye:$other")
        self.assertTrue(wait_for(lambda: job.state == "running" or job.done))

        self.assertEqual(bg.write(job, "ada\n"), 4)
        self.assertTrue(wait_for(lambda: "hello:ada" in bg.tail(job)["text"]))

        bg.write(job, b"grace\n", close=True)
        snapshot = self.finished(job)
        self.assertEqual(snapshot["state"], "done")
        self.assertIn("bye:grace", bg.tail(job)["text"])

    def test_write_to_a_call_job_is_refused(self):
        job = bg.call(lambda: 1)
        self.finished(job)
        with self.assertRaises(ValueError):
            bg.write(job, "data\n")


if __name__ == "__main__":
    unittest.main()
