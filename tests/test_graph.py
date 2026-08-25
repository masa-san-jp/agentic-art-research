from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from build_graph import build_graph, node_key
from impact import downstream, impact_report, render_markdown, upstream
from new_project import create_project


class GraphContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, temporary / name)
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def write_jsonl(self, path: Path, records: list[dict[str, object]]) -> None:
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    def add_chain(self, root: Path, slug: str) -> Path:
        project = create_project(root, slug, slug.title())
        (project / "01_planning" / "question-register.yaml").write_text(
            yaml.safe_dump({"questions": [{"id": "Q001", "text": "Question", "status": "ANSWERED"}]}, sort_keys=False),
            encoding="utf-8",
        )
        self.write_jsonl(
            project / "02_evidence" / "evidence-ledger.jsonl",
            [{"id": "EV001", "related_questions": ["Q001"]}],
        )
        self.write_jsonl(
            project / "03_knowledge" / "claims.jsonl",
            [{"id": "CL001", "evidence_ids": ["EV001"], "supporting_claims": [], "opposing_claims": []}],
        )
        (project / "04_decisions" / "insight-register.yaml").write_text(
            yaml.safe_dump({"insights": [{"id": "IN001", "claim_ids": ["CL001"], "opposing_claim_ids": []}]}, sort_keys=False),
            encoding="utf-8",
        )
        (project / "04_decisions" / "decision-log.yaml").write_text(
            yaml.safe_dump({"decisions": [{"id": "DC001", "insight_ids": ["IN001"], "evidence_ids": []}]}, sort_keys=False),
            encoding="utf-8",
        )
        (project / "05_production" / "production-requirements.yaml").write_text(
            yaml.safe_dump({"requirements": [{"id": "RQ001", "source_decisions": ["DC001"]}]}, sort_keys=False),
            encoding="utf-8",
        )
        (project / "05_production" / "acceptance-tests.yaml").write_text(
            yaml.safe_dump({"acceptance_tests": [{"id": "AT001", "target_requirement": "RQ001"}]}, sort_keys=False),
            encoding="utf-8",
        )
        return project

    def test_graph_is_deterministic_and_fully_links_the_chain(self) -> None:
        root = self.make_root()
        self.add_chain(root, "trace-test")

        graph = build_graph(root)
        self.assertEqual(graph, build_graph(root))

        node_keys = {node["key"] for node in graph["nodes"]}
        self.assertEqual(len(node_keys), len(graph["nodes"]))
        self.assertTrue(all(edge["from"] in node_keys and edge["to"] in node_keys for edge in graph["edges"]))

        project_id = "project/trace-test"
        expected_edges = {
            (node_key(project_id, "Q001"), node_key(project_id, "EV001"), "answered_by"),
            (node_key(project_id, "EV001"), node_key(project_id, "CL001"), "supports"),
            (node_key(project_id, "CL001"), node_key(project_id, "IN001"), "informs"),
            (node_key(project_id, "IN001"), node_key(project_id, "DC001"), "informs"),
            (node_key(project_id, "DC001"), node_key(project_id, "RQ001"), "requires"),
            (node_key(project_id, "RQ001"), node_key(project_id, "AT001"), "verified_by"),
        }
        actual_edges = {(edge["from"], edge["to"], edge["type"]) for edge in graph["edges"]}
        self.assertTrue(expected_edges.issubset(actual_edges))

        impacted = downstream(graph, "EV001")
        self.assertEqual({"CL001", "IN001", "DC001", "RQ001", "AT001"}, {item["id"] for item in impacted})
        self.assertEqual("project/trace-test", impacted[-1]["project_id"])

    def test_same_local_ids_in_multiple_projects_remain_project_scoped(self) -> None:
        root = self.make_root()
        self.add_chain(root, "first-project")
        self.add_chain(root, "second-project")

        graph = build_graph(root)
        evidence_nodes = [node for node in graph["nodes"] if node["id"] == "EV001"]
        self.assertEqual(2, len(evidence_nodes))
        self.assertEqual(
            {"project/first-project::EV001", "project/second-project::EV001"},
            {node["key"] for node in evidence_nodes},
        )
        self.assertEqual([], downstream(graph, "EV001"), "an ambiguous local ID must not cross project boundaries")
        first_impact = downstream(graph, "project/first-project::EV001")
        self.assertTrue(first_impact)
        self.assertTrue(all(item["project_id"] == "project/first-project" for item in first_impact))

    def test_typed_decision_registries_are_linked_in_both_directions(self) -> None:
        root = self.make_root()
        project = self.add_chain(root, "decision-links")
        (project / "04_decisions" / "decision-log.yaml").write_text(
            yaml.safe_dump(
                {
                    "decisions": [
                        {
                            "id": "DC001",
                            "insight_ids": ["IN001"],
                            "evidence_ids": [],
                            "rejected_option_ids": ["RO001"],
                            "uncertainty_ids": ["U001"],
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "04_decisions" / "rejected-options.yaml").write_text(
            yaml.safe_dump(
                {
                    "rejected_options": [
                        {"id": "RO001", "title": "Option A", "reason": "Too costly", "decision_ids": ["DC001"]}
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (project / "04_decisions" / "uncertainty-register.yaml").write_text(
            yaml.safe_dump(
                {
                    "uncertainties": [
                        {"id": "U001", "statement": "Audience response is untested.", "decision_ids": ["DC001"]}
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        graph = build_graph(root)
        project_id = "project/decision-links"
        expected_edges = {
            (node_key(project_id, "DC001"), node_key(project_id, "RO001"), "rejects"),
            (node_key(project_id, "RO001"), node_key(project_id, "DC001"), "rejected_by"),
            (node_key(project_id, "DC001"), node_key(project_id, "U001"), "has_uncertainty"),
            (node_key(project_id, "U001"), node_key(project_id, "DC001"), "uncertainty_of"),
        }
        actual_edges = {(edge["from"], edge["to"], edge["type"]) for edge in graph["edges"]}
        self.assertTrue(expected_edges.issubset(actual_edges))

    def test_impact_report_emits_upstream_and_downstream_json_and_markdown(self) -> None:
        root = self.make_root()
        self.add_chain(root, "impact-test")
        graph = build_graph(root)

        report = impact_report(graph, "EV001")
        self.assertEqual({"Q001"}, {item["id"] for item in report["upstream"]})
        self.assertEqual({"CL001", "IN001", "DC001", "RQ001", "AT001"}, {item["id"] for item in report["downstream"]})
        self.assertEqual(
            {"RQ001", "DC001", "IN001", "CL001", "EV001", "Q001"},
            {item["id"] for item in upstream(graph, "AT001")},
        )

        markdown = render_markdown(report)
        self.assertIn("## Upstream", markdown)
        self.assertIn("## Downstream", markdown)
        self.assertIn("`AT001`", markdown)
        self.assertIn("`Q001`", markdown)
        self.assertEqual({"node": "missing", "found": False, "upstream": [], "downstream": []}, impact_report(graph, "missing"))


if __name__ == "__main__":
    unittest.main()
