from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from websearch import websearch as implementation


class PackageContractTests(unittest.TestCase):
    def test_distribution_name_replaces_the_bundled_editable_install(self) -> None:
        pyproject = Path(__file__).parents[1] / "pyproject.toml"
        self.assertIn('name = "prime-agent-skill-websearch"', pyproject.read_text())


class ProviderSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.agent_dir.cleanup)

    def test_auto_prefers_serper_when_both_keys_exist(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PRIME_AGENT_CODING_AGENT_DIR": self.agent_dir.name,
                "SERPER_API_KEY": "serper-key",
                "PARALLEL_API_KEY": "parallel-key",
            },
            clear=True,
        ):
            self.assertEqual(
                implementation._select_provider("auto"), ("serper", "serper-key")
            )

    def test_auto_uses_parallel_when_it_is_the_only_configured_provider(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PRIME_AGENT_CODING_AGENT_DIR": self.agent_dir.name,
                "PARALLEL_API_KEY": "parallel-key",
            },
            clear=True,
        ):
            self.assertEqual(
                implementation._select_provider(None), ("parallel", "parallel-key")
            )

    def test_reads_parallel_key_from_auth_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "auth.json").write_text(
                json.dumps({"parallel": {"type": "api_key", "key": "stored-key"}})
            )
            with patch.dict(
                os.environ,
                {"PRIME_AGENT_CODING_AGENT_DIR": directory},
                clear=True,
            ):
                self.assertEqual(
                    implementation._resolve_api_key("parallel"), "stored-key"
                )

    def test_resolves_environment_reference_from_auth_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "auth.json").write_text(
                json.dumps(
                    {"parallel": {"type": "api_key", "key": "PARALLEL_SECRET_REF"}}
                )
            )
            with patch.dict(
                os.environ,
                {
                    "PRIME_AGENT_CODING_AGENT_DIR": directory,
                    "PARALLEL_SECRET_REF": "resolved-key",
                },
                clear=True,
            ):
                self.assertEqual(
                    implementation._resolve_api_key("parallel"), "resolved-key"
                )

    def test_rejects_command_references_in_python(self) -> None:
        self.assertEqual(
            implementation._resolve_config_value("!security find-generic-password"), ""
        )


class FormattingTests(unittest.TestCase):
    def test_formats_parallel_results(self) -> None:
        formatted = implementation._format_parallel_results(
            {
                "results": [
                    {
                        "title": "Example",
                        "url": "https://example.com",
                        "publish_date": "2026-08-07",
                        "excerpts": ["First excerpt", "Second excerpt"],
                    }
                ]
            },
            "example query",
            5,
        )
        self.assertIn("Result 0: Example", formatted)
        self.assertIn("URL: https://example.com", formatted)
        self.assertIn("Published: 2026-08-07", formatted)
        self.assertIn("First excerpt", formatted)

    def test_truncation_respects_budget(self) -> None:
        output = implementation._truncate("a" * 1000, 100)
        self.assertEqual(len(output), 100)
        self.assertIn("output truncated", output)
        self.assertEqual(implementation._truncate("content", 0), "")


class RequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.agent_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.agent_dir.cleanup)

    async def test_serper_preserves_bundled_request_contract(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["api_key"] = request.headers.get("x-api-key")
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "title": "Example",
                            "link": "https://example.com",
                            "snippet": "Serper snippet",
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await implementation._fetch_serper(
                "prime agent",
                "serper-key",
                timeout=45,
                num_results=5,
                client=client,
            )

        self.assertEqual(captured["url"], "https://google.serper.dev/search")
        self.assertEqual(captured["api_key"], "serper-key")
        self.assertEqual(captured["payload"], {"q": "prime agent"})
        self.assertIn("Serper snippet", result)

    async def test_parallel_uses_ga_endpoint_and_expected_payload(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["api_key"] = request.headers.get("x-api-key")
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "search_id": "search_123",
                    "session_id": "session_123",
                    "results": [
                        {
                            "url": "https://example.com",
                            "title": "Example",
                            "publish_date": None,
                            "excerpts": ["Relevant excerpt"],
                        }
                    ],
                },
            )

        with patch.dict(
            os.environ,
            {"PRIME_AGENT_CODING_AGENT_DIR": self.agent_dir.name},
            clear=True,
        ):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                result = await implementation._fetch_parallel(
                    "prime agent",
                    "parallel-key",
                    objective="Find Prime Agent documentation",
                    search_queries=["Prime Agent docs", "Prime Agent GitHub"],
                    mode="turbo",
                    timeout=45,
                    num_results=5,
                    max_chars_total=20000,
                    client=client,
                )

        self.assertEqual(captured["url"], "https://api.parallel.ai/v1/search")
        self.assertEqual(captured["api_key"], "parallel-key")
        self.assertEqual(
            captured["payload"],
            {
                "objective": "Find Prime Agent documentation",
                "search_queries": ["Prime Agent docs", "Prime Agent GitHub"],
                "mode": "turbo",
                "max_chars_total": 20000,
                "advanced_settings": {
                    "max_results": 5,
                    "excerpt_settings": {"max_chars_per_result": 4000},
                },
            },
        )
        self.assertIn("Relevant excerpt", result)

    async def test_http_errors_redact_api_keys(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="rejected parallel-secret")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(RuntimeError, "<redacted>") as caught:
                await implementation._post_json(
                    "https://example.test/search",
                    payload={"q": "example"},
                    headers={"x-api-key": "parallel-secret"},
                    timeout=45,
                    provider_name="Parallel",
                    client=client,
                )

        self.assertNotIn("parallel-secret", str(caught.exception))

    async def test_parallel_rejects_more_than_three_queries_without_network(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "at most 3"):
            await implementation._fetch_parallel(
                "query",
                "parallel-key",
                objective=None,
                search_queries=["one", "two", "three", "four"],
                mode="turbo",
                timeout=45,
                num_results=5,
                max_chars_total=20000,
            )

    async def test_run_preserves_callable_contract_and_selects_parallel(self) -> None:
        fetch = AsyncMock(return_value="Result 0: Example\nURL: https://example.com")
        with (
            patch.dict(
                os.environ,
                {
                    "PRIME_AGENT_CODING_AGENT_DIR": self.agent_dir.name,
                    "PARALLEL_API_KEY": "parallel-key",
                },
                clear=True,
            ),
            patch.object(implementation, "_fetch_parallel", fetch),
        ):
            output = await implementation.run("example query")

        self.assertIn('Results for query "example query"', output)
        fetch.assert_awaited_once()
        self.assertEqual(fetch.await_args.args[:2], ("example query", "parallel-key"))

    async def test_missing_credentials_does_not_attempt_network(self) -> None:
        with patch.dict(
            os.environ,
            {"PRIME_AGENT_CODING_AGENT_DIR": self.agent_dir.name},
            clear=True,
        ):
            output = await implementation.run("example query")
        self.assertIn("neither Serper nor Parallel", output)


if __name__ == "__main__":
    unittest.main()
