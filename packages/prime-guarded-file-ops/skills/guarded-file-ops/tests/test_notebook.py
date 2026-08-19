from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from guarded_file_ops import FileOps, FileOpsPolicy, ReadLimits


class NotebookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = FileOps(policy=FileOpsPolicy(use_fff=False))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def render(self, notebook, **kwargs):
        path = self.root / "test.ipynb"
        path.write_text(json.dumps(notebook), encoding="utf-8")
        return self.files.read(path, **kwargs)

    def test_markdown_code_text_error_and_language_fences(self):
        notebook = {
            "nbformat": 4,
            "metadata": {"language_info": {"name": "python```\ninjected"}},
            "cells": [
                {"cell_type": "markdown", "source": ["# Heading\n", "Body"]},
                {
                    "cell_type": "code",
                    "source": "print('```')",
                    "outputs": [
                        {"output_type": "stream", "text": "\u001b[31mred\u001b[0m\rfinal\n"},
                        {
                            "output_type": "error",
                            "ename": "ValueError",
                            "evalue": "bad",
                            "traceback": ["large traceback intentionally ignored"],
                        },
                    ],
                },
            ],
        }
        result = self.render(notebook)
        self.assertEqual(result.status, "ok")
        self.assertIn("# Heading", result.content)
        self.assertIn("````python", result.content)
        self.assertIn("final", result.content)
        self.assertNotIn("\u001b", result.content)
        self.assertIn("Error: ValueError: bad", result.content)
        self.assertNotIn("large traceback", result.content)

    def test_progress_images_widgets_and_binary_payloads_are_omitted(self):
        notebook = {
            "nbformat": 4,
            "metadata": {},
            "cells": [
                {
                    "cell_type": "code",
                    "source": "display(x)",
                    "outputs": [
                        {
                            "output_type": "stream",
                            "text": (
                                "0%|a| 0/10 [00:00<?, ?it/s]\r100%|a| 10/10 [00:01<00:00, 10it/s]"
                            ),
                        },
                        {"output_type": "display_data", "data": {"image/png": "aGVsbG8="}},
                        {
                            "output_type": "display_data",
                            "data": {"application/vnd.jupyter.widget-view+json": {"model_id": "x"}},
                        },
                        {
                            "output_type": "display_data",
                            "data": {"application/octet-stream": "blob"},
                        },
                    ],
                }
            ],
        }
        result = self.render(notebook)
        self.assertNotIn("it/s", result.content)
        self.assertIn("image/png output omitted: 5 bytes", result.content)
        self.assertIn("interactive widget omitted", result.content)
        self.assertIn("application/octet-stream output omitted", result.content)
        diagnostics = result.metadata["notebook"]
        self.assertEqual(diagnostics["omitted_assets"], 1)
        self.assertEqual(diagnostics["omitted_widgets"], 1)
        self.assertEqual(diagnostics["omitted_binary_outputs"], 1)

    def test_markdown_data_uris_and_attachments_are_never_rendered(self):
        payload = "aGVsbG8=" * 8
        notebook = {
            "nbformat": 4,
            "metadata": {},
            "cells": [
                {
                    "cell_type": "markdown",
                    "source": f"![plot](data:image/png;base64,{payload})",
                    "attachments": {"plot.png": {"image/png": payload}},
                }
            ],
        }
        result = self.render(notebook)
        self.assertNotIn(payload, result.content)
        self.assertIn("embedded data omitted", result.content)
        self.assertEqual(result.metadata["notebook"]["omitted_attachments"], 1)

    def test_oversized_textual_output_is_bounded_before_normal_pagination(self):
        notebook = {
            "nbformat": 4,
            "metadata": {},
            "cells": [
                {
                    "cell_type": "code",
                    "source": "x",
                    "outputs": [
                        {"output_type": "execute_result", "data": {"text/plain": "z" * 500}}
                    ],
                }
            ],
        }
        result = self.render(
            notebook,
            limits=ReadLimits(
                max_lines=20,
                max_bytes=400,
                max_line_characters=100,
                max_notebook_output_characters=120,
            ),
        )
        self.assertEqual(result.metadata["notebook"]["truncated_outputs"], 1)
        self.assertIn("output characters omitted", result.content)
        self.assertLessEqual(len(result.content.encode()), 400)

    def test_malformed_notebooks_fail_safely(self):
        malformed = self.root / "malformed.ipynb"
        malformed.write_text("{not json", encoding="utf-8")
        result = self.files.read(malformed)
        self.assertEqual(result.category, "malformed")
        self.assertEqual(result.conversion["backend"], "native")

        binary = self.root / "binary.ipynb"
        binary.write_bytes(b"\xff\x00")
        result = self.files.read(binary)
        self.assertEqual(result.category, "malformed")


if __name__ == "__main__":
    unittest.main()
