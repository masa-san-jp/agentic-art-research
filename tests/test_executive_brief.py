from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from executive_brief import build_executive_brief, write_executive_brief
from new_project import create_project


class ExecutiveBriefContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_brief_is_deterministic_and_derived_from_typed_registries(self) -> None:
        root = self.make_root()
        project = create_project(root, "brief-test", "Brief Test")
        (project / "04_decisions" / "decision-log.yaml").write_text(
            yaml.safe_dump(
                {
                    "decisions": [
                        {
                            "id": "DC001",
                            "question": "Which option should be adopted?",
                            "selected_option": "Use the restrained option.",
                            "rejected_options": [],
                            "rejected_option_ids": ["RO001"],
                            "insight_ids": [],
                            "evidence_ids": [],
                            "reason": "It preserves the intended absence.",
                            "uncertainty": None,
                            "uncertainty_ids": ["U001"],
                            "review_trigger": "Prototype review fails.",
                            "authority": "agent-recommended",
                            "status": "ADOPTED",
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "04_decisions" / "rejected-options.yaml").write_text(
            "rejected_options:\n  - id: RO001\n    title: Explicit option\n    reason: It over-explains the subject.\n    decision_ids: [DC001]\n",
            encoding="utf-8",
        )
        (project / "04_decisions" / "uncertainty-register.yaml").write_text(
            "uncertainties:\n  - id: U001\n    statement: Audience response remains untested.\n    severity: MAJOR\n    decision_ids: [DC001]\n    review_trigger: Prototype review fails.\n",
            encoding="utf-8",
        )

        first = build_executive_brief(root, "project/brief-test")
        second = build_executive_brief(root, "project/brief-test")
        self.assertEqual(first, second)
        self.assertIn("RO001: Explicit option", first)
        self.assertIn("U001: Audience response remains untested.", first)
        output = write_executive_brief(root, "project/brief-test")
        self.assertEqual(first, output.read_text(encoding="utf-8"))

        decision = yaml.safe_load((project / "04_decisions" / "decision-log.yaml").read_text(encoding="utf-8"))
        decision["decisions"][0]["selected_option"] = "Use the explicit option."
        (project / "04_decisions" / "decision-log.yaml").write_text(yaml.safe_dump(decision, sort_keys=False), encoding="utf-8")
        self.assertNotEqual(first, build_executive_brief(root, "project/brief-test"))


if __name__ == "__main__":
    unittest.main()
