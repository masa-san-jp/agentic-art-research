from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from bundle import AUDIENCE_PATHS, build_bundle, resolve_audience_sources
from new_project import create_project


class BundleContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        shutil.copytree(REPO_ROOT / "templates", temporary / "templates")
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_all_audiences_are_deterministic_and_include_only_declared_sources(self) -> None:
        root = self.make_root()
        create_project(root, "bundle-test", "Bundle Test")

        for audience, expected_paths in AUDIENCE_PATHS.items():
            with self.subTest(audience=audience):
                content = build_bundle(root, "project/bundle-test", audience)
                self.assertEqual(content, build_bundle(root, "project/bundle-test", audience))
                headers = re.findall(r"^## `([^`]+)`$", content, flags=re.MULTILINE)
                self.assertEqual(expected_paths, headers)
                self.assertIn("## Resolved sources", content)
                for relative in expected_paths:
                    self.assertIn(f"- `{relative}`", content)

                resolved = resolve_audience_sources(root, "project/bundle-test", audience)
                self.assertEqual(expected_paths, [relative for relative, _ in resolved])
                self.assertTrue(all(path.is_file() for _, path in resolved))

    def test_missing_source_is_a_blocking_bundle_error(self) -> None:
        root = self.make_root()
        project = create_project(root, "bundle-missing", "Bundle Missing")
        missing = project / AUDIENCE_PATHS["human"][0]
        missing.unlink()

        with self.assertRaisesRegex(FileNotFoundError, "audience source not found"):
            build_bundle(root, "project/bundle-missing", "human")

    def test_invalid_target_and_audience_fail_clearly(self) -> None:
        root = self.make_root()
        create_project(root, "bundle-errors", "Bundle Errors")

        with self.assertRaisesRegex(ValueError, "unknown audience"):
            build_bundle(root, "project/bundle-errors", "unknown")
        with self.assertRaisesRegex(ValueError, "project/<slug>"):
            build_bundle(root, "../outside", "human")
        with self.assertRaisesRegex(FileNotFoundError, "project not found"):
            build_bundle(root, "project/not-found", "human")


if __name__ == "__main__":
    unittest.main()
