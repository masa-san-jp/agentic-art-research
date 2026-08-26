from __future__ import annotations

import sys
import unittest
from pathlib import Path
import json
import tempfile
import shutil

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from chaos_check import run_chaos_suite
from harness_observability import EventStream, HarnessEventError, load_event_stream, replay_event_stream


class ChaosCheckContractTest(unittest.TestCase):
    def test_all_chaos_scenarios_pass_and_are_deterministic(self) -> None:
        first = run_chaos_suite(REPO_ROOT)
        second = run_chaos_suite(REPO_ROOT)
        self.assertTrue(first["passed"], first)
        self.assertEqual(first, second)
        self.assertEqual(
            {"api-stop", "broken-jsonl", "interrupted-lease", "duplicate-effect", "harness-fault-matrix"},
            {scenario["id"] for scenario in first["scenarios"]},
        )

    def test_event_stream_replay_rejects_unknown_duplicate_hash_and_phase_faults(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        path = root / ".harness/events/HR801.jsonl"
        stream = EventStream(REPO_ROOT, path, "HR801", "project/test", lambda: "2026-08-25T00:00:00+09:00")
        stream.append(phase="PREFLIGHT", event_type="RUN_STARTED")
        stream.append(phase="RUNNING", event_type="PHASE_ENTERED")
        self.assertEqual("RUNNING", replay_event_stream(REPO_ROOT, path)["phase"])

        original = path.read_text(encoding="utf-8").splitlines()
        duplicate_path = root / ".harness/events/duplicate.jsonl"
        duplicate_path.write_text("\n".join(original + [original[0]]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(HarnessEventError, "HARNESS-EVENT-ORDER"):
            load_event_stream(REPO_ROOT, duplicate_path)

        tampered = json.loads(original[1])
        tampered["hashes"]["request_sha256"] = "sha256:" + "f" * 64
        tampered_path = root / ".harness/events/tampered.jsonl"
        tampered_path.write_text("\n".join([original[0], json.dumps(tampered, sort_keys=True, separators=(",", ":"))]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(HarnessEventError, "HARNESS-EVENT-HASH"):
            load_event_stream(REPO_ROOT, tampered_path)

        unknown = json.loads(original[1])
        unknown["private_prompt"] = "must not be accepted"
        unknown_path = root / ".harness/events/unknown.jsonl"
        unknown_path.write_text("\n".join([original[0], json.dumps(unknown, sort_keys=True, separators=(",", ":"))]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(HarnessEventError, "HARNESS-EVENT-SCHEMA"):
            load_event_stream(REPO_ROOT, unknown_path)


if __name__ == "__main__":
    unittest.main()
