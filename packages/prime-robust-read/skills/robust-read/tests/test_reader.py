from __future__ import annotations

import os
import socket
import tempfile
import tracemalloc
import types
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from prime_robust_read import ReadLedger, ReadLimits, read


class ReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = ReadLedger()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def read(self, path, **kwargs):
        return read(path, ledger=self.ledger, use_fff=False, **kwargs)

    def test_empty_file_and_offsets_at_and_beyond_eof(self):
        empty = self.root / "empty.txt"
        empty.write_bytes(b"")
        result = self.read(empty)
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.total_lines, 0)
        self.assertEqual(result.content, "")

        path = self.root / "three.txt"
        path.write_text("one\ntwo\nthree", encoding="utf-8")
        exact = self.read(path, offset=4)
        beyond = self.read(path, offset=40)
        self.assertEqual((exact.status, exact.total_lines), ("eof", 3))
        self.assertEqual((beyond.status, beyond.total_lines), ("eof", 3))
        self.assertIn("3 lines", beyond.message)

    def test_line_byte_and_per_line_ceilings_have_exact_continuation(self):
        path = self.root / "bounded.txt"
        path.write_text("abcdef\nsecond\nthird\n", encoding="utf-8")
        limits = ReadLimits(max_lines=2, max_bytes=10, max_line_characters=3)
        first = self.read(path, limits=limits)
        self.assertEqual(first.content, "abc\nsec")
        self.assertEqual((first.start_offset, first.end_offset, first.next_offset), (1, 2, 3))
        self.assertEqual(set(first.truncated_by), {"lines", "line_characters"})
        continued = self.read(path, offset=first.next_offset, limits=limits)
        self.assertEqual(continued.content, "thi")
        self.assertIsNone(continued.next_offset)

        bytes_path = self.root / "bytes.txt"
        bytes_path.write_text("abcd\nxy", encoding="utf-8")
        bytes_result = self.read(
            bytes_path,
            limits=ReadLimits(max_lines=10, max_bytes=4, max_line_characters=10),
        )
        self.assertEqual(bytes_result.content, "abcd")
        self.assertEqual(bytes_result.next_offset, 2)
        self.assertIn("bytes", bytes_result.truncated_by)

    def test_utf8_byte_ceiling_never_splits_a_code_point(self):
        path = self.root / "utf8.txt"
        path.write_text("ééé", encoding="utf-8")
        result = self.read(
            path,
            limits=ReadLimits(max_lines=2, max_bytes=5, max_line_characters=10),
        )
        self.assertEqual(result.content, "éé")
        result.content.encode("utf-8", errors="strict")

    def test_utf8_character_spans_the_internal_chunk_boundary(self):
        path = self.root / "boundary.txt"
        path.write_bytes(b"a" * 65_535 + "é\nnext".encode())
        result = self.read(
            path,
            limit=1,
            limits=ReadLimits(
                max_lines=2,
                max_bytes=100_000,
                max_line_characters=70_000,
            ),
        )
        self.assertTrue(result.content.endswith("é"))
        self.assertEqual(result.next_offset, 2)

    def test_invalid_utf8_is_explicit(self):
        path = self.root / "invalid.txt"
        path.write_bytes(b"valid\n\xffbad")
        result = self.read(path)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.category, "invalid_encoding")
        self.assertEqual(result.conversion["encoding"], "utf-8")
        self.assertIsInstance(result.conversion["invalid_byte_offset"], int)

    def test_low_level_read_failures_are_categorized(self):
        path = self.root / "unreadable.txt"
        path.write_text("content", encoding="utf-8")
        with mock.patch(
            "prime_robust_read.reader.pread_prefix", side_effect=OSError("I/O failure")
        ):
            result = self.read(path)
        self.assertEqual(result.category, "read_failed")
        self.assertIn("I/O failure", result.message)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs require POSIX")
    def test_fifo_and_symlink_to_fifo_are_rejected_without_opening(self):
        fifo = self.root / "live.pipe"
        os.mkfifo(fifo)
        direct = self.read(fifo)
        self.assertEqual(direct.category, "non_regular_file")
        self.assertIn("FIFO", direct.message)
        link = self.root / "fifo-link"
        link.symlink_to(fifo)
        linked = self.read(link)
        self.assertEqual(linked.category, "non_regular_file")
        self.assertIn("FIFO", linked.message)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets require AF_UNIX")
    def test_socket_is_rejected(self):
        path = self.root / "service.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(path))
            result = self.read(path)
        finally:
            server.close()
        self.assertEqual(result.category, "non_regular_file")
        self.assertIn("socket", result.message)

    @unittest.skipUnless(Path("/dev/null").exists(), "device path unavailable")
    def test_character_device_and_symlink_are_rejected(self):
        result = self.read("/dev/null")
        self.assertEqual(result.category, "non_regular_file")
        self.assertIn("character device", result.message)
        link = self.root / "null-link"
        link.symlink_to("/dev/null")
        linked = self.read(link)
        self.assertEqual(linked.category, "non_regular_file")

    def test_broken_symlink_and_regular_symlink_chain(self):
        broken = self.root / "broken"
        broken.symlink_to("missing")
        self.assertEqual(self.read(broken).category, "broken_symlink")

        target = self.root / "target.txt"
        target.write_text("safe", encoding="utf-8")
        middle = self.root / "middle"
        final = self.root / "final"
        middle.symlink_to(target)
        final.symlink_to(middle)
        result = self.read(final)
        self.assertEqual(result.content, "safe")
        self.assertEqual(result.canonical_path, str(target.resolve()))

    def test_nfc_nfd_and_quote_dash_recovery(self):
        nfd_name = unicodedata.normalize("NFD", "Résumé.txt")
        actual = self.root / nfd_name
        actual.write_text("unicode", encoding="utf-8")
        recovered = self.read(self.root / unicodedata.normalize("NFC", "Résumé.txt"))
        self.assertEqual(recovered.content, "unicode")

        punctuation = self.root / "Team\u2019s\u2014notes.txt"
        punctuation.write_text("punctuation", encoding="utf-8")
        recovered = self.read(self.root / "Team's-notes.txt")
        self.assertEqual(recovered.recovery["method"], "unicode_sibling")
        self.assertEqual(recovered.content, "punctuation")

    def test_ambiguous_recovery_never_selects_a_candidate(self):
        (self.root / "Team\u2018s.txt").write_text("left", encoding="utf-8")
        (self.root / "Team\u2019s.txt").write_text("right", encoding="utf-8")
        result = self.read(self.root / "Team's.txt")
        self.assertEqual(result.category, "ambiguous_path")
        self.assertEqual(len(result.suggestions), 2)
        self.assertEqual(result.content, "")

    def test_directory_listing_is_bounded_and_continuable(self):
        directory = self.root / "items"
        directory.mkdir()
        for name in ("a", "b", "c", "d"):
            (directory / name).write_text(name, encoding="utf-8")
        limits = ReadLimits(max_lines=2, max_bytes=100, max_line_characters=20)
        first = self.read(directory, limits=limits)
        self.assertEqual(first.format, "directory")
        self.assertEqual(len(first.content.splitlines()), 2)
        self.assertEqual(first.next_offset, 3)
        second = self.read(directory, offset=3, limits=limits)
        self.assertEqual(len(second.content.splitlines()), 2)

    def test_directory_scan_distinguishes_exact_ceiling_from_overflow(self):
        directory = self.root / "ceiling"
        directory.mkdir()
        for name in ("a", "b"):
            (directory / name).write_text(name, encoding="utf-8")
        limits = ReadLimits(
            max_lines=10,
            max_bytes=100,
            max_line_characters=20,
            max_sibling_entries=2,
        )
        exact = self.read(directory, limits=limits)
        self.assertFalse(exact.truncated)
        (directory / "c").write_text("c", encoding="utf-8")
        overflow = self.read(directory, limits=limits)
        self.assertTrue(overflow.truncated)
        self.assertIn("directory_entries", overflow.truncated_by)
        self.assertEqual(overflow.next_offset, 3)

    def test_image_magic_behind_text_extension_returns_metadata_only(self):
        path = self.root / "actually-text.txt"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not decoded")
        result = self.read(path)
        self.assertEqual(result.format, "image/png")
        self.assertEqual(result.content, "")
        self.assertIn("vision", result.conversion["guidance"])

        svg = self.root / "vector.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
        svg_result = self.read(svg)
        self.assertEqual(svg_result.format, "image/svg+xml")
        self.assertEqual(svg_result.content, "")

    def test_large_single_line_keeps_python_memory_bounded(self):
        path = self.root / "huge-line.txt"
        block = b"x" * (1024 * 1024)
        with path.open("wb") as handle:
            for _ in range(50):
                handle.write(block)
        tracemalloc.start()
        try:
            result = self.read(path)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(len(result.content), 2_000)
        self.assertIn("line_characters", result.truncated_by)
        self.assertLess(peak, 8 * 1024 * 1024)

    def test_repeated_and_changed_reads_are_detected(self):
        path = self.root / "ledger.txt"
        path.write_text("one", encoding="utf-8")
        first = self.read(path)
        second = self.read(path)
        self.assertFalse(first.repeated)
        self.assertTrue(second.repeated)
        self.assertEqual(second.status, "unchanged")
        path.write_text("changed", encoding="utf-8")
        changed = self.read(path)
        self.assertTrue(changed.changed_since_last_read)
        self.assertEqual(changed.content, "changed")

    def test_repr_is_bounded_while_content_remains_programmatic(self):
        path = self.root / "repr.txt"
        path.write_text(("x" * 2_000 + "\n") * 30, encoding="utf-8")
        result = self.read(path)
        self.assertGreater(len(result["content"]), 4_096)
        self.assertLessEqual(len(repr(result)), 4_096)

    def test_structured_source_limit_precedes_converter_import(self):
        path = self.root / "too-big.docx"
        path.write_bytes(b"x" * 20)
        result = self.read(
            path,
            limits=ReadLimits(
                max_lines=10,
                max_bytes=100,
                max_line_characters=20,
                max_document_bytes=10,
            ),
        )
        self.assertEqual(result.category, "resource_limited")
        self.assertEqual(result.conversion["category"], "source_size")

    def test_missing_fff_client_only_disables_broad_recovery(self):
        existing = self.root / "existing.txt"
        existing.write_text("ordinary", encoding="utf-8")
        with mock.patch.dict("sys.modules", {"fff_repo_search": None}):
            ordinary = read(existing, ledger=ReadLedger())
            missing = read(self.root / "missing.txt", ledger=ReadLedger())
        self.assertEqual(ordinary.content, "ordinary")
        self.assertEqual(missing.category, "not_found")
        self.assertEqual(missing.recovery["fff_status"], "unavailable")

    def test_fff_recovers_only_one_in_repository_candidate(self):
        candidate = self.root / "nested" / "actual-report.txt"
        candidate.parent.mkdir()
        candidate.write_text("recovered", encoding="utf-8")

        async def find_files(*args, **kwargs):
            return {
                "items": [{"absolute_path": str(candidate)}],
                "stats": {"total_count": 1},
            }

        fake = types.SimpleNamespace(find_files=find_files)
        with (
            mock.patch("prime_robust_read.paths._git_root", return_value=self.root),
            mock.patch("prime_robust_read.paths.importlib.import_module", return_value=fake),
        ):
            result = read(self.root / "report.txt", ledger=ReadLedger())
        self.assertEqual(result.content, "recovered")
        self.assertEqual(result.recovery["method"], "fff")

    def test_fff_reported_ambiguity_and_out_of_repository_paths_are_never_selected(self):
        candidate = self.root / "one.txt"
        candidate.write_text("one", encoding="utf-8")
        outside = Path(self.temp.name).parent / "outside-fff-result.txt"
        outside.write_text("outside", encoding="utf-8")

        async def ambiguous(*args, **kwargs):
            return {
                "items": [{"absolute_path": str(candidate)}],
                "stats": {"total_count": 2},
            }

        async def escaped(*args, **kwargs):
            return {
                "items": [{"absolute_path": str(outside)}],
                "stats": {"total_count": 1},
            }

        try:
            with mock.patch("prime_robust_read.paths._git_root", return_value=self.root):
                with mock.patch(
                    "prime_robust_read.paths.importlib.import_module",
                    return_value=types.SimpleNamespace(find_files=ambiguous),
                ):
                    result = read(self.root / "ambiguous.txt", ledger=ReadLedger())
                with mock.patch(
                    "prime_robust_read.paths.importlib.import_module",
                    return_value=types.SimpleNamespace(find_files=escaped),
                ):
                    escaped_result = read(self.root / "escaped.txt", ledger=ReadLedger())
        finally:
            outside.unlink(missing_ok=True)
        self.assertEqual(result.category, "ambiguous_path")
        self.assertEqual(result.suggestions, [str(candidate.resolve())])
        self.assertTrue(result.recovery["fff_has_more"])
        self.assertEqual(escaped_result.category, "not_found")
        self.assertEqual(escaped_result.suggestions, [])


if __name__ == "__main__":
    unittest.main()
