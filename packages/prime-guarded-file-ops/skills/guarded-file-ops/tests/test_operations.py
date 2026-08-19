from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import guarded_file_ops
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


class PublicOperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_module_level_functions_are_the_canonical_simple_api(self):
        path = self.root / "module.txt"
        created = guarded_file_ops.write(path, "before")
        observed = guarded_file_ops.read(path)
        edited = guarded_file_ops.edit(
            path,
            "before",
            "after",
            expected=version(observed),
        )

        self.assertTrue(created.ok)
        self.assertTrue(observed.ok)
        self.assertIsInstance(version(observed), FileVersion)
        self.assertTrue(edited.ok)
        self.assertEqual(path.read_text(), "after")

    def test_unreleased_legacy_mutation_names_are_not_exported(self):
        self.assertNotIn("safe_write", guarded_file_ops.__all__)
        self.assertNotIn("safe_edit", guarded_file_ops.__all__)
        self.assertFalse(hasattr(guarded_file_ops, "safe_write"))
        self.assertFalse(hasattr(guarded_file_ops, "safe_edit"))

    def test_file_ops_root_scopes_relative_and_absolute_paths(self):
        files = FileOps(root=self.root, policy=FileOpsPolicy(use_fff=False))
        created = files.write("inside.txt", "inside")
        self.assertTrue(created.ok)
        self.assertEqual((self.root / "inside.txt").read_text(), "inside")

        outside = self.root.parent / f"{self.root.name}-outside.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            read_result = files.read(outside)
            write_result = files.write(outside, "changed")
        finally:
            outside.unlink(missing_ok=True)
        self.assertEqual(read_result.category, "outside_root")
        self.assertEqual(write_result.category, "outside_root")

    def test_file_ops_can_disable_all_mutations(self):
        files = FileOps(root=self.root, policy=FileOpsPolicy(use_fff=False, allow_mutation=False))
        result = files.write("blocked.txt", "content")
        self.assertEqual(result.category, "mutation_disabled")
        self.assertFalse((self.root / "blocked.txt").exists())

    def test_file_ops_configuration_is_validated_and_read_only(self):
        file_path = self.root / "not-a-directory.txt"
        file_path.write_text("x", encoding="utf-8")
        with self.assertRaises(NotADirectoryError):
            FileOps(root=file_path)

        files = FileOps(root=self.root)
        with self.assertRaises(AttributeError):
            setattr(files, "root", self.root.parent)  # noqa: B010 - verify read-only API
        with self.assertRaises(AttributeError):
            setattr(files, "policy", FileOpsPolicy(allow_mutation=False))  # noqa: B010

    def test_file_ops_instances_have_isolated_observation_state(self):
        path = self.root / "state.txt"
        path.write_text("content", encoding="utf-8")
        first = FileOps(policy=FileOpsPolicy(use_fff=False))
        second = FileOps(policy=FileOpsPolicy(use_fff=False))

        self.assertFalse(first.read(path).repeated)
        self.assertTrue(first.read(path).repeated)
        self.assertFalse(second.read(path).repeated)
        first.clear_observations()
        self.assertFalse(first.read(path).repeated)

    def test_public_surface_and_signatures_are_small_and_explicit(self):
        import inspect

        self.assertEqual(
            set(guarded_file_ops.__all__),
            {
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
            },
        )
        write_parameters = inspect.signature(guarded_file_ops.write).parameters
        edit_parameters = inspect.signature(guarded_file_ops.edit).parameters
        self.assertIn("expected", write_parameters)
        self.assertIn("root", write_parameters)
        self.assertIn("expected", edit_parameters)
        self.assertIn("all_matches", edit_parameters)
        self.assertIn("limits", edit_parameters)
        self.assertIn("root", edit_parameters)
        read_parameters = inspect.signature(guarded_file_ops.read).parameters
        self.assertIn("limits", read_parameters)
        self.assertIn("use_fff", read_parameters)
        self.assertIn("root", read_parameters)
        for legacy in {"ledger", "require_read", "count"}:
            self.assertNotIn(legacy, write_parameters)
            self.assertNotIn(legacy, edit_parameters)

    def test_import_does_not_patch_python_file_apis(self):
        import subprocess
        import sys

        script = r"""
import builtins
import io
import os
from pathlib import Path
from unittest import mock
before = (builtins.open, io.open, os.open, Path.open, Path.read_text, Path.write_text)
import guarded_file_ops
files = guarded_file_ops.FileOps()
after = (builtins.open, io.open, os.open, Path.open, Path.read_text, Path.write_text)
assert before == after
assert not hasattr(Path, "guarded_read")
"""
        subprocess.run([sys.executable, "-c", script], check=True)

    def test_root_rejects_parent_and_symlink_escapes(self):
        files = FileOps(root=self.root, policy=FileOpsPolicy(use_fff=False))
        parent_escape = files.write("../escape.txt", "blocked")
        self.assertEqual(parent_escape.category, "outside_root")

        outside = self.root.parent / f"{self.root.name}-target.txt"
        outside.write_text("outside", encoding="utf-8")
        link = self.root / "outside-link.txt"
        try:
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            result = files.read(link)
            self.assertEqual(result.category, "outside_root")
            self.assertNotIn("outside", result.content)
        finally:
            link.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)

    def test_fff_recovery_is_scoped_before_candidates_are_returned(self):
        outside = self.root.parent / f"{self.root.name}-fff-outside.txt"
        outside.write_text("outside", encoding="utf-8")
        calls: list[dict[str, object]] = []

        async def find_files(*args, **kwargs):
            calls.append(kwargs)
            return {
                "items": [{"absolute_path": str(outside)}],
                "stats": {"total_count": 1},
            }

        files = FileOps(root=self.root)
        try:
            with (
                mock.patch(
                    "guarded_file_ops.paths._git_root",
                    return_value=self.root.parent,
                ),
                mock.patch(
                    "guarded_file_ops.paths.importlib.import_module",
                    return_value=types.SimpleNamespace(find_files=find_files),
                ),
            ):
                result = files.read("missing.txt")
        finally:
            outside.unlink(missing_ok=True)
        self.assertEqual(result.category, "not_found")
        self.assertEqual(result.suggestions, [])
        self.assertEqual(calls[0]["within"], str(self.root.resolve()))


if __name__ == "__main__":
    unittest.main()
