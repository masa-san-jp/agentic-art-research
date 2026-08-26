from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from _common import atomic_write_text, load_json, load_yaml, stable_json  # noqa: E402
from build_handoff import build_handoff  # noqa: E402
from canonical import canonical_sha256  # noqa: E402
from harness import bootstrap  # noqa: E402
from harness_e2e import run_request  # noqa: E402


NOW = "2026-08-25T00:00:00+09:00"


def _schema(protocol_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(protocol_root / "schemas" / f"{name}.schema.json")
    common = load_json(protocol_root / "schemas" / "common.schema.json")
    resources = [(common["$id"], Resource.from_contents(common))]
    for related in ("harness-outcome", "harness-run", "harness-checksums"):
        value = load_json(protocol_root / "schemas" / f"{related}.schema.json")
        resources.append((value["$id"], Resource.from_contents(value)))
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


def success_result(request: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "run_id": request["run_id"],
        "attempt_id": request["attempt_id"],
        "status": "SUCCEEDED",
        "summary": "Deterministic E2E worker completed.",
        "effect_key": f"attempt/{request['attempt_id']}",
        "outputs": [],
        "failure": None,
        "human_decision_request": None,
        "diagnostics": {"stderr": "", "exit_code": 0, "signal": None, "timed_out": False},
    }


class HarnessE2EContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.work.mkdir()
        self.output.mkdir()
        self.request = REPO_ROOT / "tests/fixtures/harness/request.yaml"
        self.protocol_before = self._snapshot(REPO_ROOT)
        self.addCleanup(shutil.rmtree, self.root, True)

    def _snapshot(self, root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }

    def _seed_project_after_bootstrap(self, run_id: str) -> None:
        bootstrap(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id=run_id,
            now=NOW,
            request_path=self.request,
        )
        project = self.work / "projects" / "harness-study"
        fixture = REPO_ROOT / "tests/fixtures/harmony"
        for source in sorted(fixture.rglob("*")):
            relative = source.relative_to(fixture)
            if source.is_dir() or relative in {Path("manifest.yaml"), Path("metadata.yaml"), Path("07_runtime/research-state.json")}:
                continue
            destination = project / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

        for path in sorted(project.rglob("*")):
            if path.is_file() and path.suffix in {".yaml", ".json", ".jsonl", ".md"}:
                path.write_text(path.read_text(encoding="utf-8").replace("project/harmony-study", "project/harness-study"), encoding="utf-8")

        manifest_path = project / "manifest.yaml"
        manifest = load_yaml(manifest_path) or {}
        manifest["project"]["status"] = "VALIDATING"
        manifest["project"]["updated_at"] = NOW
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")

        state_path = project / "07_runtime/research-state.json"
        state = load_json(state_path)
        state["status"] = "VALIDATING"
        state["updated_at"] = NOW
        atomic_write_text(state_path, stable_json(state))

        plan_path = project / "01_planning/research-plan.yaml"
        plan = load_yaml(plan_path) or {}
        plan["minimums"] = {
            "evidence": 0,
            "claims": 0,
            "insights": 0,
            "decisions": 0,
            "requirements": 0,
            "reason": "The deterministic harness fixture uses an explicitly bounded research scope.",
        }
        plan_path.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), encoding="utf-8")

        (project / "04_decisions/rejected-options.yaml").write_text(
            "rejected_options:\n  - id: RO001\n    title: Explanatory text\n    reason: It weakens direct discovery.\n",
            encoding="utf-8",
        )
        (project / "04_decisions/uncertainty-register.yaml").write_text(
            "uncertainties:\n  - id: UN001\n    statement: Venue lighting remains untested.\n",
            encoding="utf-8",
        )
        (project / "03_knowledge/prior-art.jsonl").write_text(
            '{"id":"PA001","work_title":"Related work","source_url":"https://example.com/related-work","difference":"The fixture uses spatial discovery."}\n',
            encoding="utf-8",
        )
        (project / "04_decisions/self-repetition-review.yaml").write_text(
            "reviews:\n  - id: SR001\n    scope: Concept\n    prior_work_refs: [PA001]\n    risk_level: LOW\n    assessment: The fixture preserves a distinct interaction.\n    mitigation: Preserve the documented interruption.\n    reviewed_at: '2026-08-25T00:00:00+09:00'\n",
            encoding="utf-8",
        )
        (project / "06_governance/rights-register.yaml").write_text(
            "rights:\n  - id: RT001\n    status: CLEARED\n",
            encoding="utf-8",
        )
        (project / "06_governance/privacy-review.yaml").write_text(
            "status: COMPLETE\nreviewed_at: '2026-08-25T00:00:00+09:00'\nprivate_raw_in_git: false\nfindings: []\n",
            encoding="utf-8",
        )

        hypothesis = load_json(REPO_ROOT / "tests/fixtures/schema-valid/production-hypothesis.json")
        hypothesis["single_hypothesis_rationale"] = None
        rejected = dict(hypothesis)
        rejected.update({"id": "PH002", "title": "Fixed cue alternative", "status": "REJECTED", "recommendation": "REJECTED", "uncertainties": []})
        (project / "04_decisions/production-hypotheses.yaml").write_text(
            yaml.safe_dump({"hypotheses": [hypothesis, rejected]}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        comparison = load_json(REPO_ROOT / "tests/fixtures/schema-valid/hypothesis-comparison.json")
        (project / "04_decisions/hypothesis-comparison.yaml").write_text(
            yaml.safe_dump({"comparisons": [comparison]}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        prototype = load_json(REPO_ROOT / "tests/fixtures/schema-valid/prototype-plan.json")
        (project / "05_production/prototype-plans.yaml").write_text(
            yaml.safe_dump({"prototype_plans": [prototype]}, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        run_log = project / "07_runtime/run-log.jsonl"
        log_lines = run_log.read_text(encoding="utf-8").splitlines()
        log_lines.pop(-2)
        last = json.loads(log_lines[-1])
        last.update({"from_status": "READY_FOR_PRODUCTION", "to_status": "VALIDATING"})
        log_lines[-1] = json.dumps(last)
        run_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        with run_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event_id": "E2E-SEARCH", "event_type": "SEARCH_ATTEMPT", "question_id": "Q001", "strategy_id": "fixture"}) + "\n")

    def worker(self, request_path: Path, *, output_path: Path, **kwargs: object) -> dict[str, object]:
        result = success_result(load_json(request_path))
        atomic_write_text(output_path, stable_json(result))
        return result

    def test_one_command_publishes_validated_handoff_and_is_idempotent(self) -> None:
        self._seed_project_after_bootstrap("HR701")
        result = run_request(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id="HR701",
            request_path=self.request,
            adapter="test",
            worker_runner=self.worker,
            now=NOW,
            max_tasks=20,
        )
        self.assertEqual("COMPLETE", result["status"])
        published = self.output / "harness-study"
        self.assertEqual({"research-project", "handoff", "run-manifest.json", "checksums.json"}, {path.name for path in published.iterdir()})
        self.assertEqual([], list(_schema(REPO_ROOT, "harness-outcome").iter_errors(result)))
        manifest = load_json(published / "run-manifest.json")
        self.assertEqual(result["outcome_sha256"], manifest["outcome_sha256"])
        self.assertEqual([], list(_schema(REPO_ROOT, "harness-run").iter_errors(manifest)))
        checksums = load_json(published / "checksums.json")
        self.assertEqual([], list(_schema(REPO_ROOT, "harness-checksums").iter_errors(checksums)))
        self.assertTrue((published / "handoff/manifest.yaml").is_file())
        self.assertEqual(self.protocol_before, self._snapshot(REPO_ROOT))

        repeated = run_request(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id="HR701",
            request_path=self.request,
            adapter="test",
            worker_runner=self.worker,
            now=NOW,
            max_tasks=20,
        )
        self.assertEqual("ALREADY_PUBLISHED", repeated["status"])
        self.assertEqual(result["artifacts"], repeated["artifacts"])
        repeated_manifest = load_json(published / "run-manifest.json")
        self.assertEqual("ALREADY_PUBLISHED", repeated_manifest["status"])
        self.assertEqual(repeated["outcome_sha256"], repeated_manifest["outcome_sha256"])
        self.assertEqual([], list(_schema(REPO_ROOT, "harness-run").iter_errors(repeated_manifest)))
        self.assertEqual(self.protocol_before, self._snapshot(REPO_ROOT))

    def test_human_pause_and_worker_failure_never_create_partial_output(self) -> None:
        paused = run_request(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id="HR702",
            request_path=self.request,
            adapter="fake",
            fixture_mode="human_required",
            now=NOW,
            max_tasks=20,
        )
        self.assertEqual("PAUSED", paused["status"])
        self.assertEqual([], list(self.output.iterdir()))
        resumed = subprocess.run(
            [
                sys.executable,
                "tools/harness.py",
                "resume",
                "--protocol-root",
                str(REPO_ROOT),
                "--work-root",
                str(self.work),
                "--output-root",
                str(self.output),
                "--run-id",
                "HR702",
                "--now",
                NOW,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual("PAUSED", json.loads(resumed.stdout)["status"])

        failure_work = self.root / "failure-work"
        failure_output = self.root / "failure-output"
        failure_work.mkdir()
        failure_output.mkdir()
        failed = run_request(
            protocol_root=REPO_ROOT,
            work_root=failure_work,
            output_root=failure_output,
            run_id="HR703",
            request_path=self.request,
            adapter="fake",
            fixture_mode="exit",
            now=NOW,
            max_tasks=20,
        )
        self.assertIn(failed["status"], {"FAILED", "BLOCKED"})
        self.assertEqual([], list(failure_output.iterdir()))

    def test_public_cli_returns_versioned_failure_json_without_hidden_roots(self) -> None:
        work = self.root / "cli-work"
        output = self.root / "cli-output"
        work.mkdir()
        output.mkdir()
        completed = subprocess.run(
            [
                sys.executable,
                "tools/harness.py",
                "run",
                "--request",
                str(self.request),
                "--adapter",
                "fake",
                "--protocol-root",
                str(REPO_ROOT),
                "--work-root",
                str(work),
                "--output-root",
                str(output),
                "--run-id",
                "HR704",
                "--now",
                NOW,
                "--max-tasks",
                "1",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        outcome = json.loads(completed.stdout)
        self.assertEqual("BLOCKED", outcome["status"])
        self.assertEqual([], list(output.iterdir()))
        self.assertEqual([], list(_schema(REPO_ROOT, "harness-outcome").iter_errors(outcome)))


if __name__ == "__main__":
    unittest.main()
