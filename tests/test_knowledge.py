from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from build_graph import build_graph, node_key
from impact import downstream
from new_project import create_project
from validate import validate_repository


class KnowledgeContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for name in ("templates", "config", "schemas"):
            shutil.copytree(REPO_ROOT / name, root / name)
        (root / "projects").mkdir()
        (root / "data").mkdir()
        return root

    def write_jsonl(self, path: Path, records: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    def add_valid_knowledge(self, root: Path) -> tuple[Path, Path]:
        project = create_project(root, "knowledge-fixture", "Knowledge Fixture")
        evidence = [
            {"id": "EV001", "source_type": "primary_public", "source_location": "https://example.invalid/1", "creator": None, "created_at": "unknown", "acquired_at": "2026-08-11T15:00:00+09:00", "content_hash": "sha256:" + "1" * 64, "rights_status": "public-use", "sensitivity": "PUBLIC_CITABLE", "redistribution": "allowed", "related_projects": ["project/knowledge-fixture"], "related_questions": [], "extraction_status": "processed", "direct_observation": True, "observed_by": "collector"},
            {"id": "EV002", "source_type": "primary_public", "source_location": "https://example.invalid/2", "creator": None, "created_at": "unknown", "acquired_at": "2026-08-11T15:01:00+09:00", "content_hash": "sha256:" + "2" * 64, "rights_status": "public-use", "sensitivity": "PUBLIC_CITABLE", "redistribution": "allowed", "related_projects": ["project/knowledge-fixture"], "related_questions": [], "extraction_status": "processed", "direct_observation": True, "observed_by": "collector"},
        ]
        self.write_jsonl(project / "02_evidence" / "evidence-ledger.jsonl", evidence)
        self.write_jsonl(
            project / "03_knowledge" / "claims.jsonl",
            [
                {"id": "CL001", "statement": "The source uses absence as a constraint.", "type": "OBSERVATION", "evidence_ids": ["EV001"], "supporting_claims": [], "opposing_claims": [], "scope": "project/knowledge-fixture", "epistemic_status": "SUPPORTED"},
                {"id": "CL002", "statement": "The constraint remains stable across works.", "type": "INFERENCE", "evidence_ids": ["EV002"], "supporting_claims": [], "opposing_claims": [], "scope": "project/knowledge-fixture", "epistemic_status": "HYPOTHESIS"},
            ],
        )
        self.write_jsonl(
            project / "03_knowledge" / "observations.jsonl",
            [{"id": "OB001", "statement": "A recurring absence is visible.", "scope": "project/knowledge-fixture", "evidence_ids": ["EV001"]}],
        )
        self.write_jsonl(
            project / "03_knowledge" / "relationships.jsonl",
            [{"id": "RL001", "from_id": "OB001", "to_id": "CL001", "type": "influenced_by", "rationale": "The observation grounds the interpretation.", "evidence_ids": ["EV001"]}],
        )
        self.write_jsonl(
            project / "03_knowledge" / "contradictions.jsonl",
            [{"id": "CT001", "claim_ids": ["CL001", "CL002"], "description": "The claims differ on stability.", "status": "OPEN", "resolution": None}],
        )
        self.write_jsonl(
            project / "03_knowledge" / "external-references.jsonl",
            [{"id": "XR001", "title": "Reference work", "location": "https://example.invalid/reference", "reference_type": "publication", "relevance": "A comparison point.", "evidence_ids": ["EV002"]}],
        )

        profile_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, profile_root, True)
        (profile_root / "aesthetic-signals.yaml").write_text(
            "signals:\n"
            "  - id: AS001\n"
            "    creator_id: creator-fixture\n"
            "    statement: Absence is a recurring compositional constraint.\n"
            "    evidence_refs: [project/knowledge-fixture::EV001]\n"
            "    observed_period:\n"
            "      from: '2026-01-01'\n"
            "      to: '2026-06-30'\n"
            "    strength: MODERATE\n"
            "    context: LONG_TERM\n"
            "    counterexample_refs: [project/knowledge-fixture::EV002]\n"
            "    confidence_status: SUPPORTED\n"
            "    review_after: '2026-12-31'\n",
            encoding="utf-8",
        )
        return project, profile_root

    def test_all_knowledge_records_and_external_profile_validate(self) -> None:
        root = self.make_root()
        _, profile_root = self.add_valid_knowledge(root)

        self.assertEqual([], validate_repository(root, profiles_root=profile_root))

    def test_knowledge_graph_is_deterministic_and_has_expected_edges(self) -> None:
        root = self.make_root()
        _, profile_root = self.add_valid_knowledge(root)

        graph = build_graph(root, profiles_root=profile_root)
        self.assertEqual(graph, build_graph(root, profiles_root=profile_root))
        project_id = "project/knowledge-fixture"
        profile_id = "profile/creator-fixture"
        expected_edges = {
            (node_key(project_id, "OB001"), node_key(project_id, "EV001"), "based_on"),
            (node_key(project_id, "RL001"), node_key(project_id, "OB001"), "relates_from"),
            (node_key(project_id, "RL001"), node_key(project_id, "CL001"), "relates_to"),
            (node_key(project_id, "RL001"), node_key(project_id, "EV001"), "supported_by"),
            (node_key(project_id, "CT001"), node_key(project_id, "CL001"), "contradicts"),
            (node_key(project_id, "CT001"), node_key(project_id, "CL002"), "contradicts"),
            (node_key(project_id, "XR001"), node_key(project_id, "EV002"), "supported_by"),
            (node_key(profile_id, "AS001"), node_key(project_id, "EV001"), "based_on"),
            (node_key(profile_id, "AS001"), node_key(project_id, "EV002"), "counterexample"),
        }
        actual_edges = {(edge["from"], edge["to"], edge["type"]) for edge in graph["edges"]}
        self.assertTrue(expected_edges.issubset(actual_edges))
        kinds = {node["kind"] for node in graph["nodes"]}
        self.assertTrue({"observation", "relationship", "contradiction", "external_reference", "aesthetic_signal"}.issubset(kinds))

        contradiction_impact = downstream(graph, node_key(project_id, "CT001"))
        self.assertEqual({"CL001", "CL002"}, {item["id"] for item in contradiction_impact})
        signal_impact = downstream(graph, node_key(profile_id, "AS001"))
        self.assertEqual({"EV001", "EV002", "CL001", "CL002"}, {item["id"] for item in signal_impact})

    def assert_rule(self, mutator, expected_rule: str) -> None:
        root = self.make_root()
        project, profile_root = self.add_valid_knowledge(root)
        mutator(project, profile_root)
        findings = validate_repository(root, profiles_root=profile_root)
        self.assertTrue(any(item.rule == expected_rule for item in findings), [item.render() for item in findings])

    def test_unresolved_knowledge_reference_is_blocking(self) -> None:
        def mutate(project: Path, _: Path) -> None:
            self.write_jsonl(project / "03_knowledge" / "observations.jsonl", [{"id": "OB001", "statement": "A recurring absence is visible.", "scope": "project/knowledge-fixture", "evidence_ids": ["EV999"]}])

        self.assert_rule(mutate, "CROSS-REFERENCE")

    def test_duplicate_knowledge_id_is_blocking(self) -> None:
        def mutate(project: Path, _: Path) -> None:
            self.write_jsonl(project / "03_knowledge" / "observations.jsonl", [{"id": "OB001", "statement": "A", "scope": "fixture", "evidence_ids": ["EV001"]}, {"id": "OB001", "statement": "B", "scope": "fixture", "evidence_ids": ["EV002"]}])

        self.assert_rule(mutate, "DUPLICATE-ID")

    def test_self_relationship_is_blocking(self) -> None:
        def mutate(project: Path, _: Path) -> None:
            self.write_jsonl(project / "03_knowledge" / "relationships.jsonl", [{"id": "RL001", "from_id": "OB001", "to_id": "OB001", "type": "influenced_by", "rationale": "Invalid self relation.", "evidence_ids": ["EV001"]}])

        self.assert_rule(mutate, "RELATIONSHIP-SELF")

    def test_unknown_knowledge_vocabulary_is_blocking(self) -> None:
        def mutate(project: Path, _: Path) -> None:
            self.write_jsonl(project / "03_knowledge" / "relationships.jsonl", [{"id": "RL001", "from_id": "OB001", "to_id": "CL001", "type": "invented_type", "rationale": "Unknown vocabulary.", "evidence_ids": ["EV001"]}])

        self.assert_rule(mutate, "KNOWLEDGE-VOCABULARY")

    def test_resolved_contradiction_requires_resolution(self) -> None:
        def mutate(project: Path, _: Path) -> None:
            self.write_jsonl(project / "03_knowledge" / "contradictions.jsonl", [{"id": "CT001", "claim_ids": ["CL001", "CL002"], "description": "Needs resolution.", "status": "RESOLVED", "resolution": None}])

        self.assert_rule(mutate, "CONTRADICTION-RESOLUTION")

    def test_profile_period_and_reference_failures_are_named(self) -> None:
        def mutate(_: Path, profile_root: Path) -> None:
            path = profile_root / "aesthetic-signals.yaml"
            text = path.read_text(encoding="utf-8").replace("to: '2026-06-30'", "to: '2027-06-30'").replace("review_after: '2026-12-31'", "review_after: '2026-01-01'").replace("project/knowledge-fixture::EV001", "project/missing::EV999")
            path.write_text(text, encoding="utf-8")

        root = self.make_root()
        _, profile_root = self.add_valid_knowledge(root)
        mutate(root, profile_root)
        rules = {item.rule for item in validate_repository(root, profiles_root=profile_root)}
        self.assertIn("PROFILE-PERIOD", rules)
        self.assertIn("PROFILE-REFERENCE", rules)


if __name__ == "__main__":
    unittest.main()
