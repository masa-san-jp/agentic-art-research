from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from _common import load_yaml
from release_check import ReleaseCheckError, _safe_path, _spec_check, _workflow_check


class ReleaseCheckContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("config", "docs", ".github"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        return temporary

    def test_spec_and_workflow_release_contract_pass(self) -> None:
        root = REPO_ROOT
        config = load_yaml(root / "config/release.yaml")
        self.assertTrue(_spec_check(root, config)["passed"])
        self.assertTrue(_workflow_check(root, config)["passed"])

    def test_release_contract_rejects_unchecked_spec_and_path_escape(self) -> None:
        root = self.make_root()
        spec_path = root / "docs/20260811-agentic-art-research-system-design-specification.md"
        spec_path.write_text(spec_path.read_text(encoding="utf-8").replace("- [x]", "- [ ]", 1), encoding="utf-8")
        config = load_yaml(root / "config/release.yaml")
        self.assertFalse(_spec_check(root, config)["passed"])
        with self.assertRaises(ReleaseCheckError):
            _safe_path(root, "../outside")


if __name__ == "__main__":
    unittest.main()
