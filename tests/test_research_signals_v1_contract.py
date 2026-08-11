import json
import unittest
from pathlib import Path


FIXTURE_PATH = (
    Path(__file__).parent
    / "contracts"
    / "self-model-notes-research-signals-v1.json"
)
EXPECTED_SCHEMA = "urn:self-model-notes:research-signals:v1"
EXPECTED_SOURCE_REPOSITORY = "masa-san-jp/self-model-notes"
EXPECTED_BUCKETS = (
    "seeks",
    "protects",
    "avoids",
    "reacts_against",
    "drawn_toward",
    "influenced_by",
    "tensions",
    "recurring_patterns",
    "emotional_material",
    "raw_voice_refs",
)


class ResearchSignalsV1ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_pins_schema_and_upstream_commit(self):
        self.assertEqual(self.fixture["schema"], EXPECTED_SCHEMA)
        self.assertEqual(
            self.fixture["source_repository"], EXPECTED_SOURCE_REPOSITORY
        )
        self.assertRegex(self.fixture["source_commit"], r"^[0-9a-f]{40}$")

    def test_fixture_contains_only_derived_signal_boundary(self):
        self.assertEqual(
            set(self.fixture),
            {
                "schema",
                "subject",
                "as_of",
                "purpose",
                "source_repository",
                "source_commit",
                "research_signals",
            },
        )
        self.assertEqual(self.fixture["purpose"], "artistic-research")
        self.assertNotIn("entities", self.fixture)
        self.assertNotIn("claims", self.fixture)
        self.assertNotIn("events", self.fixture)
        self.assertNotIn("patterns", self.fixture)
        self.assertNotIn("sources", self.fixture)

    def test_missing_upstream_inputs_remain_explicitly_unknown(self):
        signals = self.fixture["research_signals"]
        self.assertEqual(signals["certainty"], "unknown")
        self.assertEqual(signals["evidence_refs"], [])
        for bucket in EXPECTED_BUCKETS:
            self.assertEqual(signals[bucket], [], bucket)


if __name__ == "__main__":
    unittest.main()
