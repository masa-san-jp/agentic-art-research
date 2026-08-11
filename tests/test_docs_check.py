from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from docs_check import check_documentation


class DocumentationCheckContractTest(unittest.TestCase):
    def test_repository_documentation_contract_passes(self) -> None:
        self.assertEqual([], check_documentation(REPO_ROOT))

    def test_missing_required_section_is_reported(self) -> None:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        shutil.copytree(REPO_ROOT / "config", temporary / "config")
        shutil.copytree(REPO_ROOT / "docs", temporary / "docs")
        operations = temporary / "docs" / "operations.md"
        operations.write_text(operations.read_text(encoding="utf-8").replace("## Incident response", "## Response"), encoding="utf-8")

        findings = check_documentation(temporary)

        self.assertTrue(any("Incident response" in finding for finding in findings), findings)


if __name__ == "__main__":
    unittest.main()
