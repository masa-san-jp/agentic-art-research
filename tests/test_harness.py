from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from harness import ARCHIVE_PROVENANCE_FILE, HarnessError, _git_head, bootstrap  # noqa: E402
from harness_paths import HarnessPathError, HarnessPaths  # noqa: E402
from next_action import build_next_action  # noqa: E402


NOW = "2026-08-25T00:00:00+09:00"


SNAPSHOT_DIRECTORIES = ("config", "docs", "profiles", "schemas", "templates", "tools")
SNAPSHOT_FILES = (".archive-commit", ".gitattributes", ".gitignore", "AGENTS.md", "PLANS.md", "README.md")
SNAPSHOT_IGNORED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
SNAPSHOT_IGNORED_SUFFIXES = {".pyc", ".pyo"}


def snapshot(root: Path) -> tuple[tuple[str, str, int], ...]:
    candidates = [root / name for name in SNAPSHOT_FILES]
    for name in SNAPSHOT_DIRECTORIES:
        directory = root / name
        if directory.is_dir():
            candidates.extend(directory.rglob("*"))
    entries = []
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or SNAPSHOT_IGNORED_PARTS.intersection(relative.parts)
            or path.suffix.lower() in SNAPSHOT_IGNORED_SUFFIXES
        ):
            continue
        entries.append(
            (
                relative.as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mtime_ns,
            )
        )
    return tuple(entries)


class HarnessBootstrapContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp())
        self.output = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.work, True)
        self.addCleanup(shutil.rmtree, self.output, True)
        self.request = REPO_ROOT / "tests/fixtures/harness/request.yaml"

    def _archive_protocol(self, commit: str = "a" * 40) -> Path:
        archive = self.work.parent / "protocol-archive"
        shutil.copytree(
            REPO_ROOT,
            archive,
            ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc", "*.pyo"),
        )
        (archive / ARCHIVE_PROVENANCE_FILE).write_text(commit + "\n", encoding="utf-8")
        self.addCleanup(shutil.rmtree, archive, True)
        return archive

    def test_snapshot_ignores_generated_noise_but_detects_protocol_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tools").mkdir()
            (root / "tools" / "owned.txt").write_text("before\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "noise.pyc").write_bytes(b"before")
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "noise").write_bytes(b"before")
            (root / ".git" / "objects").mkdir(parents=True)
            (root / ".git" / "objects" / "noise").write_bytes(b"before")

            before = snapshot(root)
            self.assertTrue(before)
            (root / "__pycache__" / "noise.pyc").write_bytes(b"after")
            (root / ".venv" / "bin" / "noise").write_bytes(b"after")
            self.assertEqual(before, snapshot(root))

            (root / "tools" / "owned.txt").write_text("after\n", encoding="utf-8")
            self.assertNotEqual(before, snapshot(root))

    def test_immutable_archive_uses_export_subst_commit_marker(self) -> None:
        archive = self._archive_protocol()
        result = bootstrap(
            protocol_root=archive,
            work_root=self.work,
            output_root=self.output,
            run_id="HR006",
            now=NOW,
            request_path=archive / "tests/fixtures/harness/request.yaml",
        )

        self.assertEqual("a" * 40, result["protocol_commit"])
        self.assertTrue(result["protocol_tree_clean"])
        self.assertEqual([], list(self.output.iterdir()))

    def test_git_archive_embeds_the_exact_commit_in_the_provenance_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            # REPO_ROOT may itself be a git archive, where the outer archive
            # has already substituted this marker. Recreate the source
            # template explicitly so the inner archive tests its own commit.
            (source / ".archive-commit").write_text("$Format:%H$\n", encoding="utf-8")
            shutil.copy2(REPO_ROOT / ".gitattributes", source / ".gitattributes")
            for args in (
                ("init", "-q", "-b", "main"),
                ("config", "user.email", "fixture@example.invalid"),
                ("config", "user.name", "Archive Fixture"),
                ("add", "."),
                ("commit", "-q", "-m", "archive marker"),
            ):
                subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True, text=True)
            commit = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            raw = subprocess.run(
                ["git", "-C", str(source), "archive", "--format=tar", "HEAD"],
                check=True,
                capture_output=True,
            ).stdout
            archive = Path(temporary) / "archive"
            archive.mkdir()
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as bundle:
                marker = bundle.extractfile(".archive-commit")
                self.assertIsNotNone(marker)
                (archive / ARCHIVE_PROVENANCE_FILE).write_bytes(marker.read())

            self.assertEqual(commit, (archive / ARCHIVE_PROVENANCE_FILE).read_text(encoding="utf-8").strip())
            self.assertEqual((commit, True), _git_head(archive))

    def test_archive_without_valid_commit_marker_fails_closed(self) -> None:
        archive = self._archive_protocol("not-a-commit")
        with self.assertRaisesRegex(HarnessError, "HARNESS-PROTOCOL-PROVENANCE"):
            bootstrap(
                protocol_root=archive,
                work_root=self.work,
                output_root=self.output,
                run_id="HR007",
                now=NOW,
                request_path=archive / "tests/fixtures/harness/request.yaml",
            )
        self.assertEqual([], list(self.work.iterdir()))
        self.assertEqual([], list(self.output.iterdir()))

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
        self.assertTrue(all(isinstance(check, dict) for check in answer["acceptance"]))
        self.assertTrue(all(check["protocol_root"] == str(REPO_ROOT.resolve()) for check in answer["acceptance"]))
        self.assertTrue(all(check["work_root"] == str(self.work.resolve()) for check in answer["acceptance"]))
        self.assertTrue(all("command" not in check for check in answer["acceptance"]))
        self.assertEqual(str(self.output.resolve()), answer["roots"]["output_root"])


if __name__ == "__main__":
    unittest.main()
