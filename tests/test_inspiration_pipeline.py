from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from inspiration_pipeline import (  # noqa: E402
    InspirationPipelineError,
    build_production_records,
    compare_candidates,
    critique_candidate,
    process_packet,
    revise_candidate,
    semantic_fingerprint,
    verify_handoff_preservation,
    write_project_artifacts,
)


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def candidate(candidate_id: str, *, experience: str = "Notice the missing interval while moving through the repeated structure.") -> dict[str, object]:
    return {
        "id": candidate_id,
        "revision": 1,
        "title": f"Interval study {candidate_id}",
        "core_question": "How does movement make an omission perceptible?",
        "proposition": "A repeated translucent structure makes one omission visible through viewer movement.",
        "intended_experience": [experience],
        "composition": {
            "elements": ["repeated translucent sheets", "one omitted interval", "side light"],
            "spatial_relation": "The sheets form a line with one gap at eye level.",
            "viewer_action": "The viewer walks past the line and turns toward the gap.",
            "device_name": "sheet line",
            "quantity": 7,
        },
        "material_relationship": {
            "material": "reclaimed translucent sheet",
            "behavior": "The sheet catches side light while remaining partially transparent.",
            "relation": "Its changing opacity delays recognition of the omitted interval.",
        },
        "input_transformation": {
            "source_refs": [{"kind": "observation", "id": "OB001", "source_commit": COMMIT}],
            "observation": "A gap was noticed only after the observer changed position.",
            "creative_leap": "Treat the gap as a timed interruption in the viewer's route.",
            "epistemic_status": "CREATIVE_PROPOSAL",
        },
        "source_decision_ids": ["DC001"],
        "source_insight_ids": ["IN001"],
        "differentiation": {"precedent_refs": ["XR001"], "statement": "Recognition is produced by route and parallax rather than explanatory text."},
        "feasibility": {
            "method": "Cut, hang, light, and inspect a scale arrangement from three fixed viewpoints.",
            "materials": ["reclaimed translucent sheet", "wire", "clips"],
            "tools": ["cutter", "tape measure", "lamp"],
            "skills": ["safe cutting", "basic hanging"],
            "cost_band": "LOW",
            "duration_band": "DAYS",
            "venue": "A room with a 4 m walking path and controllable side light.",
            "dimensions": "120 cm height x 80 cm width",
            "quantity": 7,
            "unit": "sheets",
            "completion_path": [
                "Resolve dimensions, material, quantity, and placement in the production plan.",
                "Build the scale arrangement and inspect the three fixed viewpoints.",
                "Render the deterministic SVG and retain the acceptance evidence reference.",
            ],
            "alternatives": ["Use paper vellum if reclaimed sheet supply is unavailable."],
            "rights_status": "CLEAR",
            "safety_status": "REVIEW_REQUIRED",
        },
        "uncertainties": [{"id": "U001" if candidate_id == "IC001" else "U002", "statement": "The gap may be missed from the entrance viewpoint.", "severity": "MAJOR", "external_validation_reason": None}],
        "acceptance_test_ids": ["AT001" if candidate_id == "IC001" else "AT002"],
        "knowledge_refs": [],
        "status": "PROPOSED",
    }


class InspirationPipelineTest(unittest.TestCase):
    def test_title_device_and_quantity_only_do_not_create_a_second_candidate(self) -> None:
        first = candidate("IC001")
        second = copy.deepcopy(first)
        second.update({"id": "IC002", "title": "Different title", "revision": 4})
        second["composition"] = copy.deepcopy(first["composition"])
        second["composition"].update({"device_name": "different device", "quantity": 99})  # type: ignore[index]
        with self.assertRaisesRegex(InspirationPipelineError, "INSP-DUPLICATE-SEMANTIC"):
            compare_candidates([first, second])

    def test_evolved_experience_is_a_distinct_candidate_and_gets_all_axes(self) -> None:
        first = candidate("IC001")
        second = candidate("IC002", experience="The viewer first trusts the rhythm, then returns to confirm the gap after the turn.")
        comparison = compare_candidates([first, second])
        self.assertEqual("COMPLETE", comparison["status"])
        self.assertEqual(3, len(comparison["axes"]))
        self.assertEqual("IC002", comparison["recommended_candidate_id"])

    def test_creative_leap_keeps_source_and_epistemic_boundary(self) -> None:
        proposal = candidate("IC001")
        proposal["input_transformation"]["epistemic_status"] = "FACT"  # type: ignore[index]
        with self.assertRaisesRegex(InspirationPipelineError, "INSP-CREATIVE-STATUS"):
            critique_candidate(proposal)  # schema validation is intentionally separate from critique
        proposal = candidate("IC001")
        proposal["input_transformation"]["source_refs"][0]["source_url"] = "https://example.com/fixture"  # type: ignore[index]
        with self.assertRaisesRegex(InspirationPipelineError, "INSP-SOURCE-URL"):
            compare_candidates([proposal, candidate("IC002", experience="The viewer returns after the turn to confirm the gap.")])

    def test_poetic_only_and_measurement_only_are_actionable_critiques(self) -> None:
        poetic = candidate("IC001")
        poetic["proposition"] = "A poetic dream of beauty and wonder."  # type: ignore[index]
        poetic["intended_experience"] = ["Feel wonder and presence."]
        poetic["composition"] = {"elements": ["presence"], "spatial_relation": "an atmosphere", "viewer_action": "be present"}
        critique = critique_candidate(poetic)
        self.assertIn("POETIC_ONLY", {item["code"] for item in critique["findings"]})

        measured = candidate("IC001")
        measured["proposition"] = "The object is 120 cm wide."  # type: ignore[index]
        measured["intended_experience"] = ["Measure 120 cm."]
        critique = critique_candidate(measured)
        self.assertIn("MEASUREMENT_ONLY", {item["code"] for item in critique["findings"]})

        experimental = candidate("IC001")
        experimental["proposition"] = "An experimental interruption is discovered through the viewer's turn."  # type: ignore[index]
        self.assertEqual("PASS", critique_candidate(experimental)["status"])

    def test_revision_changes_revision_and_process_reuses_previous_knowledge(self) -> None:
        original = candidate("IC001")
        revised = revise_candidate(original, {"proposition": "The repeated line makes the viewer return to verify one omission."})
        self.assertEqual(2, revised["revision"])
        packet = {
            "contract_version": "inspiration-pipeline/v1",
            "project_id": "project/inspiration-test",
            "research_commit": COMMIT,
            "previous_knowledge": [{"id": "ICMP0001", "kind": "comparison", "summary": "The previous run favored route-based discovery."}],
            "candidates": [
                {**revised, "id": "IC001", "knowledge_refs": ["ICMP0001"]},
                {**candidate("IC002", experience="The viewer first trusts the rhythm, then returns to confirm the gap after the turn.")},
            ],
        }
        result = process_packet(packet, require_previous_knowledge=True)
        self.assertEqual(["ICMP0001"], result["knowledge_used_ids"])
        self.assertTrue(result["handoff_ready"])
        records = build_production_records(result["candidates"], result["comparison"])
        self.assertEqual("digital-prototype-renderer", records["prototype_plans"][0]["executor_capability"])
        self.assertEqual("REPOSITORY_WRITE", records["prototype_plans"][0]["tasks"][1]["effect_type"])

    def test_handoff_drift_is_named_and_preservation_is_traceable(self) -> None:
        first = candidate("IC001")
        second = candidate("IC002", experience="The viewer first trusts the rhythm, then returns to confirm the gap after the turn.")
        result = process_packet({
            "contract_version": "inspiration-pipeline/v1",
            "project_id": "project/inspiration-test",
            "research_commit": COMMIT,
            "candidates": [first, second],
        })
        production = result["production"]
        selected = next(item for item in result["candidates"] if item["id"] == result["comparison"]["recommended_candidate_id"])
        selected_hypothesis_id = "PH" + selected["id"][-3:]
        hypothesis = next(item for item in production["hypotheses"] if item["id"] == selected_hypothesis_id)
        plan = next(item for item in production["prototype_plans"] if item["hypothesis_id"] == hypothesis["id"])
        drifted = copy.deepcopy(hypothesis)
        drifted["proposition"] = "A different light experiment replaces the omission."
        trace = verify_handoff_preservation(selected, drifted, plan, production["creative_direction"])
        self.assertEqual("REVISION_REQUIRED", trace["status"])
        self.assertIn("proposition", trace["differences"])
        self.assertEqual("MATCH", production["handoff_trace"]["status"])

    def test_project_write_is_atomic_in_shape_and_idempotent(self) -> None:
        packet = {
            "contract_version": "inspiration-pipeline/v1",
            "project_id": "project/inspiration-test",
            "research_commit": COMMIT,
            "candidates": [candidate("IC001"), candidate("IC002", experience="The viewer first trusts the rhythm, then returns to confirm the gap after the turn.")],
        }
        result = process_packet(packet)
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        project = temporary / "projects" / "inspiration-test"
        for relative in ("04_decisions", "05_production"):
            (project / relative).mkdir(parents=True)
        (project / "manifest.yaml").write_text("workflow_mode: RESEARCH_ONLY\nproject:\n  id: project/inspiration-test\n", encoding="utf-8")
        written = write_project_artifacts(project, result)
        before = {path: path.read_bytes() for path in written}
        self.assertEqual(before, {path: path.read_bytes() for path in write_project_artifacts(project, result)})
        self.assertEqual(["IC001", "IC002"], [row["id"] for row in yaml.safe_load((project / "04_decisions/inspiration-candidates.yaml").read_text())["candidates"]])
        self.assertEqual("HC001", yaml.safe_load((project / "04_decisions/hypothesis-comparison.yaml").read_text())["comparisons"][0]["id"])


if __name__ == "__main__":
    unittest.main()
