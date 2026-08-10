from __future__ import annotations

import os
import unittest
from pathlib import Path

import fff_repo_search

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


@unittest.skipUnless(
    os.environ.get("FFF_REPO_SEARCH_LIVE_TEST") == "1",
    "set FFF_REPO_SEARCH_LIVE_TEST=1 to test a running fff-routerd",
)
class LiveRouterContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_find_files_and_grep_contract(self) -> None:
        status = await fff_repo_search.status()
        self.assertEqual(
            set(status["tools"]),
            {"fff_find_files", "fff_grep"},
        )

        files = await fff_repo_search.find_files(
            "fff repo search",
            within=REPOSITORY_ROOT,
            extensions=["md"],
            limit=10,
        )
        self.assertTrue(files["items"])

        hits = await fff_repo_search.grep(
            "prime-fff-repo-search",
            within=REPOSITORY_ROOT,
            extensions=["md", "json"],
            limit=10,
        )
        self.assertTrue(hits["items"])


if __name__ == "__main__":
    unittest.main()
