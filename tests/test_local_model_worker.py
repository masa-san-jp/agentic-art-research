from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = REPO_ROOT / "tools/workers/local_model_worker.py"


class StubModel:
    """A local endpoint that answers like the model, so the worker is testable without a GPU."""

    def __init__(self, answer: dict[str, object]) -> None:
        self.prompts: list[str] = []
        answer_text = json.dumps(answer)
        prompts = self.prompts

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server contract
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                prompts.append(body["messages"][0]["content"])
                payload = json.dumps({"message": {"content": answer_text}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/api/chat"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class LocalModelWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.workspace, True)
        (self.workspace / "01_planning").mkdir(parents=True)
        (self.workspace / "01_planning/question-register.yaml").write_text("questions: []\n", encoding="utf-8")

    def request(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "run_id": "HR001",
            "attempt_id": "AT001",
            "project_id": "project/worker-test",
            "task_id": "TASK001",
            "role": "planner",
            "lease": {"token": "TASK001:attempt:1:lease:0", "expires_at": "2026-09-17T01:00:00+09:00"},
            "context": {"summary": "Task context.", "source_refs": [], "constraints": []},
            "instructions": ["Fix the questions."],
            "write_targets": [{"path": "01_planning/question-register.yaml", "mode": "UPDATE"}],
            "acceptance_ids": ["AT001"],
            "attempt_workspace": str(self.workspace),
            "deadline": "2026-09-17T01:00:00+09:00",
            "capabilities": {"declared": ["structured-output"], "required": ["structured-output"], "environment_allowlist": []},
        }

    def run_worker(self, stub: StubModel, request: dict[str, object]) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(WORKER), "stub-model", stub.url],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return json.loads(completed.stdout)

    def assert_matches_result_schema(self, result: dict[str, object]) -> None:
        schema = json.loads((REPO_ROOT / "schemas/agent-attempt-result.schema.json").read_text(encoding="utf-8"))
        common = json.loads((REPO_ROOT / "schemas/common.schema.json").read_text(encoding="utf-8"))
        registry = Registry().with_resources(
            [(common["$id"], Resource.from_contents(common)), (schema["$id"], Resource.from_contents(schema))]
        )
        errors = [error.message for error in Draft202012Validator(schema, registry=registry).iter_errors(result)]
        self.assertEqual([], errors)

    def test_it_writes_the_declared_target_and_reports_a_schema_valid_result(self) -> None:
        stub = StubModel({"files": {"01_planning/question-register.yaml": {"questions": [{"id": "Q001", "question": "What changes?", "status": "OPEN"}]}}, "summary": "Wrote one question."})
        self.addCleanup(stub.stop)

        result = self.run_worker(stub, self.request())

        self.assertEqual("SUCCEEDED", result["status"])
        self.assert_matches_result_schema(result)
        written = yaml.safe_load((self.workspace / "01_planning/question-register.yaml").read_text(encoding="utf-8"))
        self.assertEqual(1, len(written["questions"]))

    def test_it_accepts_the_repository_relative_spelling_the_findings_use(self) -> None:
        """Validator findings name the file as projects/<slug>/…; answering in that spelling is the same file."""
        stub = StubModel({"files": {"projects/worker-test/01_planning/question-register.yaml": {"questions": [{"id": "Q001", "text": "What changes?", "status": "OPEN"}]}}, "summary": "Fixed the status."})
        self.addCleanup(stub.stop)

        result = self.run_worker(stub, self.request())

        self.assertEqual("SUCCEEDED", result["status"])
        written = yaml.safe_load((self.workspace / "01_planning/question-register.yaml").read_text(encoding="utf-8"))
        self.assertEqual("OPEN", written["questions"][0]["status"])

    def test_it_decodes_a_record_the_model_handed_back_as_a_string(self) -> None:
        """Written as-is, the quoted record made the gate itself error out instead of counting."""
        (self.workspace / "02_evidence").mkdir()
        (self.workspace / "02_evidence/evidence-ledger.jsonl").write_text("", encoding="utf-8")
        request = self.request()
        request["write_targets"] = [{"path": "02_evidence/evidence-ledger.jsonl", "mode": "UPDATE"}]
        stub = StubModel({"files": {"02_evidence/evidence-ledger.jsonl": "{\"id\": \"E001\", \"statement\": \"One record.\"}"}, "summary": "Wrote one record."})
        self.addCleanup(stub.stop)

        result = self.run_worker(stub, request)

        self.assertEqual("SUCCEEDED", result["status"])
        line = (self.workspace / "02_evidence/evidence-ledger.jsonl").read_text(encoding="utf-8").strip()
        self.assertEqual({"id": "E001", "statement": "One record."}, json.loads(line))

    def test_it_refuses_a_path_the_attempt_may_not_touch(self) -> None:
        stub = StubModel({"files": {"07_runtime/research-state.json": {"tampered": True}}, "summary": "Wrong target."})
        self.addCleanup(stub.stop)

        result = self.run_worker(stub, self.request())

        self.assertEqual("FAILED", result["status"])
        self.assert_matches_result_schema(result)
        self.assertFalse((self.workspace / "07_runtime/research-state.json").exists())

    def test_it_carries_the_previous_gate_failure_into_the_next_prompt(self) -> None:
        """Retrying with the same prompt repeats the same miss; the gate already said what was missing."""
        attempts_root = Path(tempfile.mkdtemp()) / "HR001/TASK001"
        self.addCleanup(shutil.rmtree, attempts_root.parents[1], True)
        first = attempts_root / "AT001"
        first.mkdir(parents=True)
        (first / "acceptance-report.json").write_text(
            json.dumps(
                {
                    "gates": [
                        {
                            "id": "AG-PLANNER-QUESTIONS",
                            "kind": "collection_minimum",
                            "path": "01_planning/question-register.yaml",
                            "expected": 1,
                            "actual": 0,
                            "status": "FAIL",
                        },
                        {
                            "id": "AG-PLANNER-VALIDATE",
                            "kind": "project_validate",
                            "path": None,
                            "expected": 0,
                            "actual": 1,
                            "status": "FAIL",
                            "findings": ["projects/probe/01_planning/question-register.yaml#questions.0.status: [VOCAB] invalid question status 'NOT_A_STATUS'"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        second_workspace = attempts_root / "AT002" / "project"
        shutil.copytree(self.workspace, second_workspace)
        request = self.request()
        request["attempt_workspace"] = str(second_workspace)
        stub = StubModel({"files": {"01_planning/question-register.yaml": {"questions": [{"id": "Q001", "question": "What changes?", "status": "OPEN"}]}}, "summary": "Wrote one question."})
        self.addCleanup(stub.stop)

        self.run_worker(stub, request)

        self.assertIn("AG-PLANNER-QUESTIONS", stub.prompts[0])
        self.assertIn("NOT_A_STATUS", stub.prompts[0])


if __name__ == "__main__":
    unittest.main()
