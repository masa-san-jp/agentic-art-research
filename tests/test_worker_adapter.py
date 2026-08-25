from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(REPO_ROOT / "tools"))

from worker_adapter import WorkerAdapterError, run_attempt  # noqa: E402


def stable_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class WorkerAdapterContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self.request = load_json(REPO_ROOT / "tests/fixtures/harness/attempt-request.json")

    def make_request(self, mode: str | None = None, *, required: list[str] | None = None) -> Path:
        request = copy.deepcopy(self.request)
        request["attempt_workspace"] = str(self.workspace)
        if mode is not None:
            request["fixture_mode"] = mode
        if required is not None:
            request["capabilities"]["required"] = required
        path = self.workspace / "request.json"
        path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_normal_result_is_schema_valid_and_byte_identical_on_replay(self) -> None:
        request_path = self.make_request()
        first = run_attempt(request_path, adapter="fake", protocol_root=REPO_ROOT)
        second = run_attempt(request_path, adapter="fake", protocol_root=REPO_ROOT)
        self.assertEqual(first, second)
        self.assertEqual(stable_bytes(first), stable_bytes(second))
        schema = load_json(REPO_ROOT / "schemas/agent-attempt-result.schema.json")
        common = load_json(REPO_ROOT / "schemas/common.schema.json")
        validator = Draft202012Validator(
            schema,
            registry=Registry().with_resource(common["$id"], Resource.from_contents(common)),
        )
        self.assertEqual([], list(validator.iter_errors(first)))

    def test_human_required_is_typed_and_runtime_is_not_touched(self) -> None:
        runtime = REPO_ROOT / "tests/fixtures/harness/attempt-runtime-sentinel.txt"
        before = sorted(path.relative_to(REPO_ROOT).as_posix() for path in REPO_ROOT.rglob("*") if path.is_file())
        self.addCleanup(runtime.unlink, missing_ok=True)
        result = run_attempt(self.make_request("human_required"), adapter="fake", protocol_root=REPO_ROOT)
        after = sorted(path.relative_to(REPO_ROOT).as_posix() for path in REPO_ROOT.rglob("*") if path.is_file())
        self.assertEqual("HUMAN_REQUIRED", result["status"])
        self.assertIsNotNone(result["human_decision_request"])
        self.assertEqual("CENTRAL_PROPOSITION_CHANGE", result["human_decision_request"]["human_decision_category"])
        self.assertEqual(before, after)

    def test_failure_classes_cover_process_and_protocol_faults(self) -> None:
        cases = {
            "timeout": ("WORKER-TIMEOUT", {"timeout_seconds": 0.05}),
            "exit": ("WORKER-EXIT", {}),
            "signal": ("WORKER-EXIT", {}),
            "invalid_json": ("WORKER-PROTOCOL", {}),
            "unknown_field": ("WORKER-PROTOCOL", {}),
            "stdout_oversize": ("WORKER-OUTPUT-LIMIT", {"max_stdout_bytes": 100}),
            "stderr_oversize": ("WORKER-OUTPUT-LIMIT", {"max_stderr_bytes": 100}),
            "secret_output": ("WORKER-SECRET-OUTPUT", {}),
        }
        for mode, (failure_class, options) in cases.items():
            with self.subTest(mode=mode):
                result = run_attempt(self.make_request(mode), adapter="fake", protocol_root=REPO_ROOT, **options)
                self.assertEqual("FAILED", result["status"])
                self.assertEqual(failure_class, result["failure"]["class"])
                serialized = json.dumps(result, ensure_ascii=False)
                self.assertNotIn("sk-" + ("x" * 24), serialized)
                self.assertNotIn("credential=" + ("y" * 24), serialized)
                self.assertNotIn(str(self.workspace), serialized)
                self.assertNotIn(self.request["lease"]["token"], serialized)
                if mode == "stdout_oversize":
                    self.assertLessEqual(result["diagnostics"]["bytes"], 101)
                if mode == "stderr_oversize":
                    self.assertLessEqual(result["diagnostics"]["bytes"], 101)

    def test_capability_and_command_safety_fail_without_starting_a_shell(self) -> None:
        result = run_attempt(self.make_request(required=["network"]), adapter="fake", protocol_root=REPO_ROOT)
        self.assertEqual("WORKER-CAPABILITY", result["failure"]["class"])
        marker = self.workspace / "shell-was-executed"
        command = ["sh", "-c", f"touch {marker}"]
        result = run_attempt(self.make_request(), adapter="fake", protocol_root=REPO_ROOT, command=command)
        self.assertEqual("WORKER-COMMAND", result["failure"]["class"])
        self.assertFalse(marker.exists())
        result = run_attempt(self.make_request(), adapter="fake", protocol_root=REPO_ROOT, command=["missing-worker-executable"])
        self.assertEqual("WORKER-COMMAND", result["failure"]["class"])

    def test_result_file_is_idempotent_and_conflicts_are_not_overwritten(self) -> None:
        request_path = self.make_request()
        output = self.workspace / "result.json"
        first = run_attempt(request_path, adapter="fake", protocol_root=REPO_ROOT, output_path=output)
        original = output.read_bytes()
        second = run_attempt(request_path, adapter="fake", protocol_root=REPO_ROOT, output_path=output)
        self.assertEqual(first, second)
        self.assertEqual(original, output.read_bytes())
        output.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkerAdapterError, "WORKER-PROTOCOL"):
            run_attempt(request_path, adapter="fake", protocol_root=REPO_ROOT, output_path=output)
        self.assertEqual(b"{}\n", output.read_bytes())

    def test_malformed_request_is_rejected_before_worker_start(self) -> None:
        request_path = self.make_request()
        value = json.loads(request_path.read_text(encoding="utf-8"))
        value["unknown"] = True
        request_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(WorkerAdapterError, "WORKER-PROTOCOL"):
            run_attempt(request_path, adapter="fake", protocol_root=REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
