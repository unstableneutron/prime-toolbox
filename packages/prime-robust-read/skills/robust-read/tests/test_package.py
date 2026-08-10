from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import prime_robust_read

SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


class PackageContractTests(unittest.TestCase):
    def test_distribution_import_and_skill_layout_match(self):
        project = tomllib.loads((SKILL_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        package = json.loads((PACKAGE_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["name"], "prime_robust_read")
        self.assertEqual(package["name"], "@unstableneutron/prime-robust-read")
        self.assertEqual(package["pi"]["skills"], ["./skills"])
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        self.assertTrue((SKILL_ROOT / "uv.lock").is_file())
        module_path = Path(prime_robust_read.__file__).resolve()
        self.assertEqual(module_path.parent, (SKILL_ROOT / "src/prime_robust_read").resolve())

    def test_root_package_exposes_the_skill(self):
        root = json.loads((REPOSITORY_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertIn("./packages/prime-robust-read/skills", root["pi"]["skills"])


if __name__ == "__main__":
    unittest.main()
