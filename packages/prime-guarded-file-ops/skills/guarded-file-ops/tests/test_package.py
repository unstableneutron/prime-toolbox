from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import guarded_file_ops

SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


class PackageContractTests(unittest.TestCase):
    def test_distribution_import_and_skill_layout_match(self):
        project = tomllib.loads((SKILL_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package = json.loads((PACKAGE_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["name"], "guarded-file-ops")
        self.assertEqual(package["name"], "@unstableneutron/prime-guarded-file-ops")
        self.assertEqual(package["pi"]["skills"], ["./skills"])
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        self.assertTrue((SKILL_ROOT / "uv.lock").is_file())
        module_path = Path(guarded_file_ops.__file__).resolve()
        self.assertEqual(module_path.parent, (SKILL_ROOT / "src/guarded_file_ops").resolve())

    def test_skill_routes_agents_to_the_canonical_api(self):
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        _, frontmatter, body = skill.split("---", maxsplit=2)
        fields = dict(
            line.split(":", maxsplit=1) for line in frontmatter.strip().splitlines() if ":" in line
        )
        self.assertEqual(fields["name"].strip(), "guarded-file-ops")
        description = fields["description"].strip()
        self.assertTrue(description)
        self.assertLessEqual(len(description), 1_024)
        for symbol in ("guarded_file_ops.read", "guarded_file_ops.write", "guarded_file_ops.edit"):
            self.assertIn(symbol, skill)
        self.assertIn("help(guarded_file_ops.<function>)", body)

    def test_root_package_exposes_the_skill(self):
        root = json.loads((REPOSITORY_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertIn("./packages/prime-guarded-file-ops/skills", root["pi"]["skills"])


if __name__ == "__main__":
    unittest.main()
