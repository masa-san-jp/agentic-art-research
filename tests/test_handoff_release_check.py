from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from handoff_release_check import CHECK_SCHEMA, _feedback_schema_boundary, _production_result_schema_snapshot, check_handoff_release  # noqa: E402


class HandoffReleaseCheckContractTest(unittest.TestCase):
    def test_research_gate_passes_with_external_schema_snapshot(self) -> None:
        result = check_handoff_release(
            REPO_ROOT,
            REPO_ROOT / "tests" / "fixtures" / "harmony",
            REPO_ROOT / "tests" / "fixtures" / "harmony-handoff",
            REPO_ROOT / "execution" / "ci-evidence.json",
        )

        self.assertEqual(CHECK_SCHEMA, result["schema"])
        self.assertTrue(result["passed"])
        self.assertTrue(result["schema_snapshot_ready"])
        self.assertEqual(
            "SNAPSHOT_VALID",
            next(item for item in result["checks"] if item["id"] == "production_result_schema_snapshot")["status"],
        )
        schema = json.loads((REPO_ROOT / "schemas" / "handoff-release-check.schema.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema).iter_errors(result))
        self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_require_schema_snapshot_passes_after_clean_snapshot_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "handoff-release.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "tools" / "handoff_release_check.py"),
                    "--require-schema-snapshot",
                    "-o",
                    str(output),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            self.assertTrue(report["schema_snapshot_ready"])

    def test_schema_snapshot_provenance_tampering_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(REPO_ROOT / "config", root / "config")
            shutil.copytree(REPO_ROOT / "templates", root / "templates")
            shutil.copytree(REPO_ROOT / "schemas", root / "schemas")
            schema_path = root / "schemas" / "external" / "result.json"
            schema_path.parent.mkdir(parents=True, exist_ok=True)
            schema_path.write_text(
                json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}) + "\n",
                encoding="utf-8",
            )
            policy_path = root / "config" / "handoff-policy.yaml"
            policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            policy["production_result_schema_path"] = "schemas/external/result.json"
            policy["production_result_schema_versions"] = ["1.0.0"]
            policy["production_result_schema_source"] = {
                "repository": "masa-san-jp/agentic-art-production",
                "commit": "not-a-commit",
                "acquired_at": "2026-08-12T00:00:00+09:00",
                "sha256": f"sha256:{hashlib.sha256(schema_path.read_bytes()).hexdigest()}",
            }
            policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

            result = _production_result_schema_snapshot(root)
            self.assertFalse(result["passed"])
            self.assertEqual("INVALID_PROVENANCE", result["status"])

    def test_feedback_boundary_probes_missing_schema_after_snapshot_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(REPO_ROOT / "config", root / "config")
            shutil.copytree(REPO_ROOT / "templates", root / "templates")
            shutil.copytree(REPO_ROOT / "schemas", root / "schemas")
            schema_path = root / "schemas" / "external" / "result.json"
            schema_path.parent.mkdir(parents=True, exist_ok=True)
            schema_path.write_text(
                json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}) + "\n",
                encoding="utf-8",
            )
            policy_path = root / "config" / "handoff-policy.yaml"
            policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            policy["production_result_schema_path"] = "schemas/external/result.json"
            policy["production_result_schema_versions"] = ["1.0.0"]
            policy["production_result_schema_source"] = {
                "repository": "masa-san-jp/agentic-art-production",
                "commit": "0123456789abcdef0123456789abcdef01234567",
                "acquired_at": "2026-08-12T00:00:00+09:00",
                "sha256": f"sha256:{hashlib.sha256(schema_path.read_bytes()).hexdigest()}",
            }
            policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")

            result = _feedback_schema_boundary(root)
            self.assertTrue(result["passed"])
            self.assertEqual("FAIL_CLOSED", result["status"])


if __name__ == "__main__":
    unittest.main()
