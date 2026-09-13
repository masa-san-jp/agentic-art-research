from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
REPO_ROOT = TEST_ROOT.parent
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from test_inspiration_pipeline import COMMIT, candidate  # noqa: E402
from inspiration_pipeline import process_packet, verify_handoff_preservation  # noqa: E402


class InspirationHandoffTest(unittest.TestCase):
    def test_adopted_content_is_traceable_and_replacement_requires_revision(self) -> None:
        result = process_packet({
            "contract_version": "inspiration-pipeline/v1",
            "project_id": "project/inspiration-test",
            "research_commit": COMMIT,
            "candidates": [
                candidate("IC001"),
                candidate("IC002", experience="The viewer first trusts the rhythm, then returns to confirm the gap after the turn."),
            ],
        })
        production = result["production"]
        selected = next(item for item in result["candidates"] if item["id"] == result["comparison"]["recommended_candidate_id"])
        selected_id = "PH" + selected["id"][-3:]
        hypothesis = next(item for item in production["hypotheses"] if item["id"] == selected_id)
        plan = next(item for item in production["prototype_plans"] if item["hypothesis_id"] == selected_id)
        drifted = copy.deepcopy(hypothesis)
        drifted["proposition"] = "A different experiment has replaced the selected omission."
        trace = verify_handoff_preservation(selected, drifted, plan, production["creative_direction"])
        self.assertEqual("REVISION_REQUIRED", trace["status"])
        self.assertIn("proposition", trace["differences"])
        self.assertEqual("MATCH", production["handoff_trace"]["status"])


if __name__ == "__main__":
    unittest.main()
