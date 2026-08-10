from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prime_robust_read import ReadLedger, read, safe_edit, safe_write


class MutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = ReadLedger()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_safe_creation_and_followup_edit(self):
        path = self.root / "new.txt"
        created = safe_write(path, "alpha", require_read=True, ledger=self.ledger)
        self.assertEqual(created.status, "created")
        self.assertEqual(path.read_text(), "alpha")
        edited = safe_edit(path, "alpha", "beta", ledger=self.ledger)
        self.assertEqual(edited.status, "ok")
        self.assertEqual(edited.replacements, 1)
        self.assertEqual(path.read_text(), "beta")

    def test_read_before_write_is_optional_but_enforced_when_requested(self):
        path = self.root / "existing.txt"
        path.write_text("before", encoding="utf-8")
        refused = safe_write(path, "after", require_read=True, ledger=self.ledger)
        self.assertEqual(refused.status, "refused")
        self.assertEqual(path.read_text(), "before")
        allowed = safe_write(path, "after", require_read=False, ledger=self.ledger)
        self.assertEqual(allowed.status, "ok")

    def test_stale_read_refuses_write_and_edit(self):
        path = self.root / "stale.txt"
        path.write_text("original", encoding="utf-8")
        read(path, ledger=self.ledger, use_fff=False)
        path.write_text("external change", encoding="utf-8")
        write_result = safe_write(path, "ours", ledger=self.ledger)
        edit_result = safe_edit(path, "external", "internal", ledger=self.ledger)
        self.assertTrue(write_result.stale)
        self.assertTrue(edit_result.stale)
        self.assertEqual(path.read_text(), "external change")

    def test_deleted_observed_file_is_stale_not_a_new_creation(self):
        path = self.root / "deleted.txt"
        path.write_text("observed", encoding="utf-8")
        read(path, ledger=self.ledger, use_fff=False)
        path.unlink()
        write_result = safe_write(path, "replacement", ledger=self.ledger)
        edit_result = safe_edit(path, "observed", "replacement", ledger=self.ledger)
        self.assertTrue(write_result.stale)
        self.assertTrue(edit_result.stale)
        self.assertFalse(path.exists())

    def test_safe_edit_requires_a_read_by_default_and_exact_old_text(self):
        path = self.root / "edit.txt"
        path.write_text("one two", encoding="utf-8")
        unread = safe_edit(path, "one", "1", ledger=self.ledger)
        self.assertEqual(unread.status, "refused")
        read(path, ledger=self.ledger, use_fff=False)
        missing = safe_edit(path, "three", "3", ledger=self.ledger)
        self.assertEqual(missing.status, "refused")
        self.assertEqual(path.read_text(), "one two")

    def test_safe_edit_does_not_create_missing_file(self):
        path = self.root / "missing.txt"
        result = safe_edit(path, "a", "b", require_read=False, ledger=self.ledger)
        self.assertEqual(result.status, "refused")
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
