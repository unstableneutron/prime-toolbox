from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast
from unittest import mock

from guarded_file_ops import (
    FileOps,
    FileOpsPolicy,
    FileVersion,
    MutationResult,
    ReadResult,
)


def version(result: ReadResult | MutationResult) -> FileVersion:
    value = result.version
    assert value is not None
    return value


class MutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = FileOps(policy=FileOpsPolicy(use_fff=False))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_creation_and_followup_edit_return_new_versions(self):
        path = self.root / "new.txt"
        created = self.files.write(path, "alpha")
        self.assertTrue(created.ok)
        self.assertEqual(created.status, "created")
        self.assertEqual(version(created).canonical_path, str(path.resolve()))

        edited = self.files.edit(path, "alpha", "beta", expected=version(created))
        self.assertTrue(edited.ok)
        self.assertEqual(edited.replacements, 1)
        self.assertNotEqual(version(edited), version(created))
        self.assertEqual(path.read_text(), "beta")

    def test_existing_write_requires_the_observed_version(self):
        path = self.root / "existing.txt"
        path.write_text("before", encoding="utf-8")
        refused = self.files.write(path, "after")
        self.assertFalse(refused.ok)
        self.assertEqual(refused.category, "missing_version")
        self.assertEqual(path.read_text(), "before")

        observed = self.files.read(path)
        allowed = self.files.write(path, "after", expected=version(observed))
        self.assertTrue(allowed.ok)
        self.assertEqual(path.read_text(), "after")

    def test_stale_version_refuses_write_and_edit(self):
        path = self.root / "stale.txt"
        path.write_text("original", encoding="utf-8")
        observed = self.files.read(path)
        path.write_text("external change", encoding="utf-8")

        write_result = self.files.write(path, "ours", expected=version(observed))
        edit_result = self.files.edit(
            path,
            "external",
            "internal",
            expected=version(observed),
        )
        self.assertTrue(write_result.stale)
        self.assertTrue(edit_result.stale)
        self.assertEqual(write_result.category, "stale_version")
        self.assertEqual(edit_result.category, "stale_version")
        self.assertEqual(path.read_text(), "external change")

    def test_ctime_detects_same_size_write_with_restored_mtime(self):
        path = self.root / "ctime.txt"
        path.write_text("aaaa", encoding="utf-8")
        observed = self.files.read(path)
        observed_version = version(observed)
        stat_before = path.stat()
        path.write_text("bbbb", encoding="utf-8")
        os.utime(path, ns=(stat_before.st_atime_ns, observed_version.mtime_ns))

        result = self.files.write(path, "ours", expected=observed_version)
        self.assertEqual(result.category, "stale_version")
        self.assertEqual(path.read_text(), "bbbb")

    def test_deleted_observed_file_is_stale_not_a_new_creation(self):
        path = self.root / "deleted.txt"
        path.write_text("observed", encoding="utf-8")
        observed = self.files.read(path)
        path.unlink()

        write_result = self.files.write(path, "replacement", expected=version(observed))
        edit_result = self.files.edit(
            path,
            "observed",
            "replacement",
            expected=version(observed),
        )
        self.assertTrue(write_result.stale)
        self.assertTrue(edit_result.stale)
        self.assertFalse(path.exists())

    def test_edit_refuses_missing_and_multiple_matches_by_default(self):
        path = self.root / "edit.txt"
        path.write_text("one two one", encoding="utf-8")
        observed = self.files.read(path)

        missing = self.files.edit(path, "three", "3", expected=version(observed))
        self.assertEqual(missing.category, "old_text_not_found")
        multiple = self.files.edit(path, "one", "1", expected=version(observed))
        self.assertEqual(multiple.category, "multiple_matches")
        self.assertEqual(path.read_text(), "one two one")

        replaced = self.files.edit(
            path,
            "one",
            "1",
            expected=version(observed),
            all_matches=True,
        )
        self.assertTrue(replaced.ok)
        self.assertEqual(replaced.replacements, 2)
        self.assertEqual(path.read_text(), "1 two 1")

    def test_edit_does_not_create_a_missing_file(self):
        observed_path = self.root / "observed.txt"
        observed_path.write_text("a", encoding="utf-8")
        observed = self.files.read(observed_path)
        missing = self.root / "missing.txt"

        result = self.files.edit(missing, "a", "b", expected=version(observed))
        self.assertEqual(result.status, "refused")
        self.assertFalse(missing.exists())

    def test_version_is_bound_to_one_canonical_path(self):
        first = self.root / "first.txt"
        second = self.root / "second.txt"
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        observed = self.files.read(first)

        result = self.files.write(second, "changed", expected=version(observed))
        self.assertEqual(result.category, "version_path_mismatch")
        self.assertEqual(second.read_text(), "two")

    def test_hard_link_alias_does_not_bypass_path_binding(self):
        first = self.root / "hard-first.txt"
        second = self.root / "hard-second.txt"
        first.write_text("shared", encoding="utf-8")
        try:
            second.hardlink_to(first)
        except (OSError, NotImplementedError):
            self.skipTest("hard links are unavailable")
        observed = self.files.read(first)

        result = self.files.write(second, "changed", expected=version(observed))
        self.assertEqual(result.category, "version_path_mismatch")
        self.assertEqual(first.read_text(), "shared")

    def test_versions_chain_across_reads_and_mutations(self):
        path = self.root / "chain.txt"
        created = self.files.write(path, "one")
        first = self.files.read(path)
        second = self.files.read(path, offset=20)
        self.assertEqual(version(created), version(first))
        self.assertEqual(version(first), version(second))

        edited = self.files.edit(path, "one", "two", expected=version(first))
        reread = self.files.read(path)
        self.assertEqual(version(edited), version(reread))
        self.assertNotEqual(version(first), version(reread))

        stale = self.files.write(path, "three", expected=version(first))
        self.assertEqual(stale.category, "stale_version")

    def test_identical_write_and_edit_are_noops(self):
        path = self.root / "noop.txt"
        path.write_text("same", encoding="utf-8")
        observed = self.files.read(path)

        write_result = self.files.write(path, "same", expected=version(observed))
        edit_result = self.files.edit(path, "same", "same", expected=version(observed))
        self.assertEqual(write_result.status, "unchanged")
        self.assertEqual(edit_result.status, "unchanged")
        self.assertFalse(write_result.changed)
        self.assertFalse(edit_result.changed)
        self.assertEqual(version(write_result), version(observed))
        self.assertEqual(version(edit_result), version(observed))

    def test_write_rechecks_expected_after_initial_validation(self):
        from guarded_file_ops import mutation

        path = self.root / "race.txt"
        path.write_text("observed", encoding="utf-8")
        observed = self.files.read(path)
        validate = mutation._validate_expected

        def race_after_validation(*args, **kwargs):
            result = validate(*args, **kwargs)
            if result is None:
                path.write_text("raced", encoding="utf-8")
            return result

        with mock.patch(
            "guarded_file_ops.mutation._validate_expected",
            side_effect=race_after_validation,
        ):
            result = self.files.write(path, "ours", expected=version(observed))
        self.assertEqual(result.category, "stale_version")
        self.assertEqual(path.read_text(), "raced")

    def test_one_file_ops_serializes_same_version_writers(self):
        path = self.root / "concurrent.txt"
        path.write_text("before", encoding="utf-8")
        observed = self.files.read(path)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.files.write, path, value, expected=version(observed))
                for value in ("first", "second")
            ]
        results = [future.result() for future in futures]
        self.assertEqual(sum(result.ok for result in results), 1)
        self.assertEqual(sum(result.category == "stale_version" for result in results), 1)
        self.assertIn(path.read_text(), {"first", "second"})

    def test_mutation_path_and_encoding_failures_have_stable_categories(self):
        directory_result = self.files.write(self.root, "blocked")
        self.assertEqual(directory_result.category, "non_regular_file")

        missing_parent = self.files.write(self.root / "missing" / "file.txt", "blocked")
        self.assertEqual(missing_parent.category, "invalid_path")

        broken = self.root / "broken-link.txt"
        try:
            broken.symlink_to(self.root / "absent-target.txt")
        except (OSError, NotImplementedError):
            pass
        else:
            broken_result = self.files.write(broken, "blocked")
            self.assertEqual(broken_result.category, "broken_symlink")
            broken.unlink(missing_ok=True)

        encoded = self.root / "encoded.txt"
        encoded.write_text("é", encoding="utf-8")
        observed = self.files.read(encoded)
        result = self.files.edit(
            encoded,
            "é",
            "e",
            expected=version(observed),
            encoding="ascii",
        )
        self.assertEqual(result.category, "invalid_encoding")
        self.assertEqual(encoded.read_text(encoding="utf-8"), "é")

    def test_programmer_errors_raise_before_io(self):
        path = self.root / "errors.txt"
        path.write_text("one", encoding="utf-8")
        observed = self.files.read(path)
        invalid_write = cast(Any, self.files.write)
        invalid_edit = cast(Any, self.files.edit)
        with self.assertRaises(TypeError):
            invalid_write(path, 3, expected=version(observed))
        with self.assertRaises(TypeError):
            invalid_write(path, "two", expected=object())
        with self.assertRaises(ValueError):
            invalid_edit(path, "", "two", expected=version(observed))
        with self.assertRaises(TypeError):
            invalid_edit(
                path,
                "one",
                "two",
                expected=version(observed),
                all_matches=1,
            )
        self.assertEqual(path.read_text(), "one")


if __name__ == "__main__":
    unittest.main()
