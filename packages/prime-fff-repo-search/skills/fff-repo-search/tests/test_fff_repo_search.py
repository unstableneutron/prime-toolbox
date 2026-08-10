from __future__ import annotations

import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fff_repo_search

SKILL_ROOT = Path(__file__).resolve().parents[1]
SUBPACKAGE_ROOT = SKILL_ROOT.parents[1]
REPOSITORY_ROOT = SKILL_ROOT.parents[3]


class PackageContractTests(unittest.TestCase):
    def test_python_skill_layout_and_manifests_match(self) -> None:
        pyproject = tomllib.loads((SKILL_ROOT / "pyproject.toml").read_text())
        skill_text = (SKILL_ROOT / "SKILL.md").read_text()
        subpackage = json.loads((SUBPACKAGE_ROOT / "package.json").read_text())
        repository = json.loads((REPOSITORY_ROOT / "package.json").read_text())

        self.assertEqual(pyproject["project"]["name"], "prime-fff-repo-search")
        self.assertEqual(pyproject["project"]["license"], "MIT")
        self.assertEqual(pyproject["project"]["requires-python"], ">=3.11")
        self.assertEqual(
            set(pyproject["project"]["dependencies"]),
            {"httpx>=0.28,<1", "mcp>=1.29,<2"},
        )
        self.assertEqual(
            pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"],
            ["src/fff_repo_search"],
        )
        self.assertTrue((SKILL_ROOT / "src/fff_repo_search/__init__.py").is_file())
        self.assertRegex(skill_text, r"(?m)^name: fff-repo-search$")
        self.assertRegex(skill_text, r"(?m)^description: .+$")
        self.assertEqual(subpackage["pi"]["skills"], ["./skills"])
        self.assertIn(
            "./packages/prime-fff-repo-search/skills",
            repository["pi"]["skills"],
        )

    def test_import_resolves_to_the_packaged_source(self) -> None:
        module_path = Path(fff_repo_search.__file__).resolve()
        expected = (SKILL_ROOT / "src/fff_repo_search/__init__.py").resolve()
        self.assertEqual(module_path, expected)
        self.assertEqual(
            set(fff_repo_search.__all__),
            {"SearchResponse", "find_files", "grep", "run", "status"},
        )
        for name in fff_repo_search.__all__:
            self.assertTrue(callable(getattr(fff_repo_search, name)))


class ValidationTests(unittest.TestCase):
    def test_patterns_preserve_significant_whitespace_and_deduplicate(self) -> None:
        self.assertEqual(
            fff_repo_search._patterns([" token ", "token", "token"]),
            [" token ", "token"],
        )

    def test_patterns_reject_empty_nul_and_excessive_counts(self) -> None:
        for patterns in ([], ["   "], ["bad\x00pattern"]):
            with self.subTest(patterns=patterns), self.assertRaises(ValueError):
                fff_repo_search._patterns(patterns)
        with self.assertRaises(ValueError):
            fff_repo_search._patterns([str(index) for index in range(21)])

    def test_extensions_normalize_dots_and_reject_paths_or_globs(self) -> None:
        self.assertEqual(
            fff_repo_search._extensions([".py", "py", "pyi"]), ["py", "pyi"]
        )
        for extensions in (["*.py"], ["src/py"], [".py/"]):
            with self.subTest(extensions=extensions), self.assertRaises(ValueError):
                fff_repo_search._extensions(extensions)

    def test_bounded_integer_rejects_booleans_and_out_of_range_values(self) -> None:
        with self.assertRaises(TypeError):
            fff_repo_search._bounded_int(True, name="limit", minimum=1, maximum=50)
        for value in (0, 51):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fff_repo_search._bounded_int(value, name="limit", minimum=1, maximum=50)

    def test_wildcard_only_regexes_are_rejected(self) -> None:
        for pattern in (".*", ".+", "^.*$", " "):
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                fff_repo_search._reject_wildcard_only_regex([pattern], literal=False)
        fff_repo_search._reject_wildcard_only_regex(["token.*"], literal=False)
        fff_repo_search._reject_wildcard_only_regex([".*"], literal=True)

    def test_within_resolves_paths_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child"
            child.mkdir()
            with patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(
                    fff_repo_search._resolve_within(["child", child]),
                    [str(child.resolve())],
                )
        with self.assertRaises(ValueError):
            fff_repo_search._resolve_within([])


class RequestConstructionTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_files_builds_the_structured_router_request(self) -> None:
        expected = fff_repo_search.SearchResponse({"items": []})
        with tempfile.TemporaryDirectory() as directory:
            call = AsyncMock(return_value=expected)
            with patch.object(fff_repo_search, "_call", call):
                response = await fff_repo_search.find_files(
                    "auth service",
                    within=directory,
                    extensions=[".py", "pyi"],
                    exclude_paths=["vendor", "vendor"],
                    limit=7,
                )
        self.assertIs(response, expected)
        call.assert_awaited_once_with(
            "fff_find_files",
            {
                "query": "auth service",
                "within": str(Path(directory).resolve()),
                "limit": 7,
                "cursor": None,
                "output_mode": "json",
                "extensions": ["py", "pyi"],
                "exclude_paths": ["vendor"],
            },
        )

    async def test_grep_builds_the_structured_router_request(self) -> None:
        expected = fff_repo_search.SearchResponse({"items": []})
        with tempfile.TemporaryDirectory() as directory:
            call = AsyncMock(return_value=expected)
            with patch.object(fff_repo_search, "_call", call):
                response = await fff_repo_search.grep(
                    ["ValidateToken", "validate_token"],
                    within=directory,
                    literal=False,
                    glob="src/**/*.py",
                    extensions=[".py"],
                    exclude_paths=["tests"],
                    context_lines=2,
                    limit=9,
                )
        self.assertIs(response, expected)
        call.assert_awaited_once_with(
            "fff_grep",
            {
                "patterns": ["ValidateToken", "validate_token"],
                "literal": False,
                "within": str(Path(directory).resolve()),
                "context_lines": 2,
                "limit": 9,
                "cursor": None,
                "output_mode": "json",
                "glob": "src/**/*.py",
                "extensions": ["py"],
                "exclude_paths": ["tests"],
            },
        )

    async def test_run_rejects_unknown_operations_without_network(self) -> None:
        with self.assertRaisesRegex(ValueError, "operation must be"):
            await fff_repo_search.run("token", operation="unknown")


class ResultParsingTests(unittest.TestCase):
    def test_structured_result_is_wrapped_and_long_lines_are_bounded(self) -> None:
        payload = {
            "items": [
                {
                    "path": "service.py",
                    "text": "x" * 3_000,
                    "context_before": ["y" * 3_000],
                }
            ],
            "stats": {"total_count": 1},
        }
        result = SimpleNamespace(
            isError=False,
            structuredContent=payload,
            content=[],
        )

        response = fff_repo_search._parse_result(result)

        self.assertIsInstance(response, fff_repo_search.SearchResponse)
        self.assertEqual(len(response["items"][0]["text"]), 2_000)
        self.assertEqual(len(response["items"][0]["context_before"][0]), 2_000)
        self.assertTrue(response["stats"]["client_truncated"])

    def test_response_character_budget_drops_trailing_items(self) -> None:
        payload = {
            "items": [
                {"path": "one", "metadata": "x" * 40_000},
                {"path": "two", "metadata": "y" * 40_000},
            ]
        }

        response = fff_repo_search._search_response(payload)

        self.assertEqual([item["path"] for item in response["items"]], ["one"])
        self.assertEqual(response["stats"]["client_original_result_count"], 2)
        self.assertEqual(response["stats"]["client_returned_count"], 1)

    def test_json_text_result_keeps_non_json_messages(self) -> None:
        result = SimpleNamespace(
            isError=False,
            structuredContent=None,
            content=[
                SimpleNamespace(text="router note"),
                SimpleNamespace(text=json.dumps({"items": []})),
            ],
        )

        response = fff_repo_search._parse_result(result)

        self.assertEqual(response["items"], [])
        self.assertEqual(response["_messages"], ["router note"])

    def test_structured_tool_errors_become_runtime_errors(self) -> None:
        result = SimpleNamespace(
            isError=True,
            structuredContent=None,
            content=[
                SimpleNamespace(
                    text=json.dumps(
                        {"code": "INVALID_SCOPE", "message": "outside root"}
                    )
                )
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "INVALID_SCOPE: outside root"):
            fff_repo_search._parse_result(result)

    def test_compact_representation_is_bounded(self) -> None:
        response = fff_repo_search.SearchResponse(
            {
                "backend_used": "fff-mcp",
                "base_path": "/repo",
                "items": [
                    {"path": "one.py", "line": 1, "text": "x" * 400},
                    {"path": "two.py", "line": 2, "text": "second"},
                ],
                "stats": {"total_count": 20},
            }
        )

        compact = response.compact(max_items=1)

        self.assertIn("backend='fff-mcp'", compact)
        self.assertIn("one.py:1", compact)
        self.assertNotIn("two.py:2", compact)
        self.assertIn("... 1 more", compact)
        self.assertLess(len(compact), 600)

    def test_compact_bounds_all_user_controlled_fields(self) -> None:
        sentinel = "x" * 10_000
        response = fff_repo_search.SearchResponse(
            {
                "backend_used": sentinel,
                "base_path": sentinel,
                "items": [
                    {"path": sentinel, "line": sentinel, "text": sentinel},
                    sentinel,
                ],
            }
        )

        compact = response.compact()

        self.assertLessEqual(len(compact), 4_096)
        self.assertNotIn(sentinel, compact)
        self.assertEqual(response["items"][0]["path"], sentinel)
        self.assertEqual(response["items"][1], sentinel)


class DaemonBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def test_endpoint_override_is_trimmed_and_blank_uses_default(self) -> None:
        with patch.dict(
            os.environ,
            {"FFF_ROUTER_MCP_URL": "  http://localhost:9000/mcp  "},
        ):
            self.assertEqual(
                fff_repo_search._endpoint(),
                "http://localhost:9000/mcp",
            )
        with patch.dict(os.environ, {"FFF_ROUTER_MCP_URL": "   "}):
            self.assertEqual(
                fff_repo_search._endpoint(),
                "http://127.0.0.1:4319/mcp",
            )

    def test_loopback_detection_controls_proxy_environment_usage(self) -> None:
        for endpoint in (
            "http://localhost:4319/mcp",
            "http://127.0.0.2:4319/mcp",
            "http://[::1]:4319/mcp",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertTrue(fff_repo_search._is_loopback_endpoint(endpoint))
        self.assertFalse(
            fff_repo_search._is_loopback_endpoint("https://search.example.com/mcp")
        )

    def test_windows_daemon_spawn_uses_native_detachment_flags(self) -> None:
        options = fff_repo_search._daemon_spawn_options("nt")

        self.assertNotIn("start_new_session", options)
        self.assertNotIn("close_fds", options)
        self.assertEqual(
            options["creationflags"],
            fff_repo_search._WINDOWS_CREATE_NEW_PROCESS_GROUP
            | fff_repo_search._WINDOWS_DETACHED_PROCESS,
        )

    async def test_custom_unreachable_endpoint_does_not_start_local_daemon(
        self,
    ) -> None:
        unavailable = AsyncMock(side_effect=ConnectionError("down"))
        with (
            patch.dict(
                os.environ,
                {"FFF_ROUTER_MCP_URL": "http://127.0.0.1:9999/mcp"},
            ),
            patch.object(fff_repo_search, "_list_tools_once", unavailable),
            patch.object(fff_repo_search.shutil, "which") as which,
            self.assertRaisesRegex(RuntimeError, "custom fff-routerd endpoint"),
        ):
            await fff_repo_search._ensure_daemon()
        which.assert_not_called()

    async def test_incompatible_endpoint_fails_without_starting_daemon(self) -> None:
        incompatible = AsyncMock(return_value=["some_other_tool"])
        with (
            patch.object(fff_repo_search, "_list_tools_once", incompatible),
            patch.object(fff_repo_search.shutil, "which") as which,
            self.assertRaisesRegex(RuntimeError, "missing required tools"),
        ):
            await fff_repo_search._ensure_daemon()
        which.assert_not_called()

    async def test_missing_local_daemon_has_actionable_error(self) -> None:
        unavailable = AsyncMock(side_effect=ConnectionError("down"))
        with (
            patch.dict(os.environ, {"FFF_ROUTER_MCP_URL": ""}),
            patch.object(fff_repo_search, "_list_tools_once", unavailable),
            patch.object(fff_repo_search.shutil, "which", return_value=None),
            self.assertRaisesRegex(RuntimeError, "fff-routerd is unavailable"),
        ):
            await fff_repo_search._ensure_daemon()

    async def test_default_endpoint_starts_detached_daemon_and_polls(self) -> None:
        probes = AsyncMock(
            side_effect=[
                ConnectionError("initial"),
                ConnectionError("starting"),
                ["fff_find_files", "fff_grep"],
            ]
        )
        spawn = AsyncMock()
        sleep = AsyncMock()
        with (
            patch.dict(os.environ, {"FFF_ROUTER_MCP_URL": ""}),
            patch.object(fff_repo_search, "_list_tools_once", probes),
            patch.object(
                fff_repo_search.shutil,
                "which",
                return_value="/usr/local/bin/fff-routerd",
            ),
            patch.object(
                fff_repo_search.asyncio,
                "create_subprocess_exec",
                spawn,
            ),
            patch.object(fff_repo_search.asyncio, "sleep", sleep),
            patch.object(fff_repo_search.time, "monotonic", return_value=0.0),
        ):
            await fff_repo_search._ensure_daemon()

        spawn.assert_awaited_once_with(
            "/usr/local/bin/fff-routerd",
            **fff_repo_search._daemon_spawn_options("posix"),
        )
        self.assertEqual(probes.await_count, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_call_reconnects_once_after_transport_failure(self) -> None:
        result = SimpleNamespace(
            isError=False,
            structuredContent={"items": []},
            content=[],
        )
        call_once = AsyncMock(side_effect=[ConnectionError("stale"), result])
        ensure = AsyncMock()
        with (
            patch.object(fff_repo_search, "_call_once", call_once),
            patch.object(fff_repo_search, "_ensure_daemon", ensure),
        ):
            response = await fff_repo_search._call("fff_grep", {"patterns": ["x"]})

        self.assertEqual(response["items"], [])
        self.assertEqual(call_once.await_count, 2)
        ensure.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
