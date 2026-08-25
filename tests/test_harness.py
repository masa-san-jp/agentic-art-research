from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from harness import HarnessError, bootstrap  # noqa: E402
from harness_paths import HarnessPathError, HarnessPaths  # noqa: E402
from next_action import build_next_action  # noqa: E402


NOW = "2026-08-25T00:00:00+09:00"


def snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(path for path in root.rglob("*") if path.is_file())
    )


class HarnessBootstrapContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp())
        self.output = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)
        self.addCleanup(shutil.rmtree, self.output, True)
        self.request = REPO_ROOT / "tests/fixtures/harness/request.yaml"

    def test_bootstrap_is_isolated_schema_valid_and_does_not_touch_protocol_or_output(self) -> None:
        before = snapshot(REPO_ROOT)
        result = bootstrap(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id="HR001",
            now=NOW,
            request_path=self.request,
        )

        self.assertEqual("BOOTSTRAPPED", result["status"])
        self.assertEqual("project/harness-study", result["project_id"])
        self.assertEqual("AVAILABLE", result["dependency_preflight"]["checks"][0]["status"])
        self.assertEqual("MISSING", result["dependency_preflight"]["checks"][1]["status"])
        state = json.loads((self.work / "projects/harness-study/07_runtime/research-state.json").read_text())
        self.assertIn("task_runtime", state)
        self.assertEqual([], list(self.output.iterdir()))
        self.assertEqual(before, snapshot(REPO_ROOT))

        schema = json.loads((REPO_ROOT / "schemas/harness-run.schema.json").read_text())
        common = json.loads((REPO_ROOT / "schemas/common.schema.json").read_text())
        validator = Draft202012Validator(
            schema,
            registry=Registry().with_resource(common["$id"], Resource.from_contents(common)),
        )
        self.assertEqual([], list(validator.iter_errors(result)))

    def test_same_request_hash_and_run_id_returns_the_same_manifest_and_changed_input_is_non_destructive(self) -> None:
        first = bootstrap(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id="HR002",
            now=NOW,
            request_path=self.request,
        )
        project_before = snapshot(self.work)
        changed = self.work.parent / "changed-request.yaml"
        value = yaml.safe_load(self.request.read_text())
        value["intent"]["purpose"] = "conflicting content"
        changed.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        self.addCleanup(changed.unlink, missing_ok=True)

        repeated = bootstrap(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id="HR002",
            now="2026-08-25T01:00:00+09:00",
            request_path=self.request,
        )
        self.assertEqual(first, repeated)
        self.assertEqual(project_before, snapshot(self.work))
        with self.assertRaisesRegex(HarnessError, "HARNESS-BOOTSTRAP-CONFLICT"):
            bootstrap(
                protocol_root=REPO_ROOT,
                work_root=self.work,
                output_root=self.output,
                run_id="HR002",
                now=NOW,
                request_path=changed,
            )
        self.assertEqual(project_before, snapshot(self.work))

    def test_optional_dependency_is_available_when_supplied_and_invalid_is_blocking(self) -> None:
        profiles = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, profiles, True)
        result = bootstrap(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id="HR003",
            now=NOW,
            request_path=self.request,
            profiles_root=profiles,
        )
        checks = {item["id"]: item for item in result["dependency_preflight"]["checks"]}
        self.assertEqual("AVAILABLE", checks["profiles-root"]["status"])
        self.assertTrue(checks["profiles-root"]["required"])

        work = Path(tempfile.mkdtemp())
        output = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        self.addCleanup(shutil.rmtree, output, True)
        with self.assertRaisesRegex(HarnessError, "HARNESS-DEPENDENCY-PREFLIGHT"):
            bootstrap(
                protocol_root=REPO_ROOT,
                work_root=work,
                output_root=output,
                run_id="HR004",
                now=NOW,
                request_path=self.request,
                profiles_root=work / "missing-profiles",
            )
        self.assertEqual([], list(work.iterdir()))
        self.assertEqual([], list(output.iterdir()))

    def test_root_boundary_rejects_same_and_nested_roots(self) -> None:
        with self.assertRaisesRegex(HarnessPathError, "HARNESS-ROOT-BOUNDARY"):
            HarnessPaths.resolve(REPO_ROOT, self.work, self.work)
        with self.assertRaisesRegex(HarnessPathError, "HARNESS-ROOT-BOUNDARY"):
            HarnessPaths.resolve(REPO_ROOT, REPO_ROOT / "temporary-work", self.output)

    def test_next_action_emits_absolute_protocol_and_work_paths(self) -> None:
        bootstrap(
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
            run_id="HR005",
            now=NOW,
            request_path=self.request,
        )
        answer = build_next_action(
            REPO_ROOT,
            "project/harness-study",
            "worker-harness",
            NOW,
            dry_run=True,
            protocol_root=REPO_ROOT,
            work_root=self.work,
            output_root=self.output,
        )
        self.assertTrue(answer["acceptance"])
        self.assertTrue(all(str(REPO_ROOT / "tools") in command or "python3 -c" in command for command in answer["acceptance"]))
        self.assertTrue(all(str(self.work) in command for command in answer["acceptance"]))
        self.assertEqual(str(self.output.resolve()), answer["roots"]["output_root"])


if __name__ == "__main__":
    unittest.main()
