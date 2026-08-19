from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import bg

SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parents[3]


class PackageContractTests(unittest.TestCase):
    def test_distribution_import_and_skill_layout_match(self):
        project = tomllib.loads((SKILL_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package = json.loads((PACKAGE_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["name"], "bg")
        self.assertEqual(project["project"]["dependencies"], [])
        self.assertEqual(package["name"], "@unstableneutron/prime-bg-jobs")
        self.assertEqual(package["pi"]["skills"], ["./skills"])
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        self.assertTrue((SKILL_ROOT / "src/bg/py.typed").is_file())
        module_path = Path(bg.__file__).resolve()
        self.assertEqual(module_path.parent, (SKILL_ROOT / "src/bg").resolve())

    def test_public_api_is_the_documented_surface(self):
        for name in ("run", "call", "list", "tail", "write", "wait", "kill", "result", "clean"):
            self.assertTrue(callable(getattr(bg, name)), name)
            self.assertIn(name, bg.__all__)

    def test_skill_routes_agents_away_from_blocking_and_polling(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        _, frontmatter, body = skill.split("---", maxsplit=2)
        fields = dict(
            line.split(":", maxsplit=1) for line in frontmatter.strip().splitlines() if ":" in line
        )
        self.assertEqual(fields["name"].strip(), "bg")
        description = fields["description"].strip()
        self.assertTrue(description)
        self.assertLessEqual(len(description), 1_024)
        for symbol in ("bg.run", "bg.call", "bg.tail", "bg.write", "bg.wait", "bg.kill"):
            self.assertIn(symbol, body)
        self.assertIn("Do NOT poll in a loop", body)
        self.assertIn("rlm_heartbeat.create", body)


if __name__ == "__main__":
    unittest.main()
