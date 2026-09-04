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

    def test_agents_contract_is_self_contained_for_a_fresh_clone(self) -> None:
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        for required in (
            "python3 -m venv .venv",
            ".venv/bin/python -m pip install -r requirements.txt",
            ".venv/bin/python -m unittest discover -s tests -v",
            ".venv/bin/python tools/validate.py --check",
            ".venv/bin/python tools/next_action.py",
            ".venv/bin/python tools/task_runtime.py",
            "write_targets",
            "lease",
            "budget_remaining.exceeded",
            "07_runtime/completion-report.json",
            "<external-output-root>/<project-id>/",
            "PRIVATE_RAW",
            "外部送信",
        ):
            self.assertIn(required, agents)

        self.assertNotIn("/Users/masa/", agents)
        self.assertTrue((REPO_ROOT / "requirements.txt").is_file())

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
