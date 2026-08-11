from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from snapshot_production_schema import SchemaSnapshotError, snapshot_schema  # noqa: E402


class ProductionSchemaSnapshotTest(unittest.TestCase):
    def _git(self, source: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def _source_repo(self, schema_text: str | None = None) -> tuple[tempfile.TemporaryDirectory, Path, str]:
        temporary = tempfile.TemporaryDirectory()
        source = Path(temporary.name) / "production"
        source.mkdir()
        self._git(source, "init", "--quiet")
        schema = source / "schemas" / "external" / "production-result.v1.schema.json"
        schema.parent.mkdir(parents=True)
        schema.write_text(
            schema_text
            if schema_text is not None
            else json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object"}) + "\n",
            encoding="utf-8",
        )
        self._git(source, "add", ".")
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return temporary, source, self._git(source, "rev-parse", "HEAD")

    def _research_root(self, temporary: tempfile.TemporaryDirectory) -> Path:
        root = Path(temporary.name) / "research"
        shutil.copytree(REPO_ROOT / "config", root / "config")
        policy_path = root / "config" / "handoff-policy.yaml"
        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        policy["production_result_schema_source"] = {}
        policy["production_result_schema_versions"] = []
        policy_path.write_text(yaml.safe_dump(policy, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (root / "schemas").mkdir()
        return root

    def test_clean_commit_snapshot_is_idempotent_and_pins_policy(self) -> None:
        source_temporary, source, commit = self._source_repo()
        research_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(source_temporary.cleanup)
        self.addCleanup(research_temporary.cleanup)
        root = self._research_root(research_temporary)
        acquired_at = "2026-08-12T00:30:00+09:00"
        kwargs = {
            "source_repository": "masa-san-jp/agentic-art-production",
            "commit": commit,
            "acquired_at": acquired_at,
            "version": "1.0.0",
        }

        first = snapshot_schema(root, source, "schemas/external/production-result.v1.schema.json", Path("schemas/external/production-result.v1.schema.json"), **kwargs)
        second = snapshot_schema(root, source, "schemas/external/production-result.v1.schema.json", Path("schemas/external/production-result.v1.schema.json"), **kwargs)
        self.assertEqual(first, second)
        policy = yaml.safe_load((root / "config" / "handoff-policy.yaml").read_text(encoding="utf-8"))
        self.assertEqual(commit, policy["production_result_schema_source"]["commit"])
        self.assertEqual(first["sha256"], policy["production_result_schema_source"]["sha256"])

    def test_dirty_source_is_rejected_before_snapshot(self) -> None:
        source_temporary, source, commit = self._source_repo()
        research_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(source_temporary.cleanup)
        self.addCleanup(research_temporary.cleanup)
        root = self._research_root(research_temporary)
        (source / "schemas" / "external" / "production-result.v1.schema.json").write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(SchemaSnapshotError, "EXTERNAL-SCHEMA-SOURCE"):
            snapshot_schema(
                root,
                source,
                "schemas/external/production-result.v1.schema.json",
                Path("schemas/external/production-result.v1.schema.json"),
                source_repository="masa-san-jp/agentic-art-production",
                commit=commit,
                acquired_at="2026-08-12T00:30:00+09:00",
                version="1.0.0",
            )
        self.assertFalse((root / "schemas" / "external" / "production-result.v1.schema.json").exists())

    def test_commit_mismatch_is_rejected_before_snapshot(self) -> None:
        source_temporary, source, commit = self._source_repo()
        research_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(source_temporary.cleanup)
        self.addCleanup(research_temporary.cleanup)
        root = self._research_root(research_temporary)

        with self.assertRaisesRegex(SchemaSnapshotError, "EXTERNAL-SCHEMA-SOURCE"):
            snapshot_schema(
                root,
                source,
                "schemas/external/production-result.v1.schema.json",
                Path("schemas/external/production-result.v1.schema.json"),
                source_repository="masa-san-jp/agentic-art-production",
                commit="0" * 40,
                acquired_at="2026-08-12T00:30:00+09:00",
                version="1.0.0",
            )
        self.assertNotEqual(commit, "0" * 40)
        self.assertFalse((root / "schemas" / "external" / "production-result.v1.schema.json").exists())

    def test_invalid_schema_is_rejected_before_snapshot(self) -> None:
        source_temporary, source, _commit = self._source_repo("[]\n")
        research_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(source_temporary.cleanup)
        self.addCleanup(research_temporary.cleanup)
        root = self._research_root(research_temporary)

        with self.assertRaisesRegex(SchemaSnapshotError, "EXTERNAL-SCHEMA-SCHEMA"):
            snapshot_schema(
                root,
                source,
                "schemas/external/production-result.v1.schema.json",
                Path("schemas/external/production-result.v1.schema.json"),
                source_repository="masa-san-jp/agentic-art-production",
                commit=self._git(source, "rev-parse", "HEAD"),
                acquired_at="2026-08-12T00:30:00+09:00",
                version="1.0.0",
            )
        self.assertFalse((root / "schemas" / "external" / "production-result.v1.schema.json").exists())

    def test_output_outside_external_schema_directory_is_rejected(self) -> None:
        source_temporary, source, commit = self._source_repo()
        research_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(source_temporary.cleanup)
        self.addCleanup(research_temporary.cleanup)
        root = self._research_root(research_temporary)

        with self.assertRaisesRegex(SchemaSnapshotError, "EXTERNAL-SCHEMA-PATH"):
            snapshot_schema(
                root,
                source,
                "schemas/external/production-result.v1.schema.json",
                Path("schemas/not-external.json"),
                source_repository="masa-san-jp/agentic-art-production",
                commit=commit,
                acquired_at="2026-08-12T00:30:00+09:00",
                version="1.0.0",
            )
        self.assertFalse((root / "schemas" / "not-external.json").exists())

    def test_different_existing_snapshot_is_never_overwritten(self) -> None:
        source_temporary, source, commit = self._source_repo()
        research_temporary = tempfile.TemporaryDirectory()
        self.addCleanup(source_temporary.cleanup)
        self.addCleanup(research_temporary.cleanup)
        root = self._research_root(research_temporary)
        output = Path("schemas/external/production-result.v1.schema.json")
        kwargs = {
            "source_repository": "masa-san-jp/agentic-art-production",
            "commit": commit,
            "acquired_at": "2026-08-12T00:30:00+09:00",
            "version": "1.0.0",
        }
        snapshot_schema(root, source, output.as_posix(), output, **kwargs)
        original = (root / output).read_bytes()

        source_schema = source / "schemas" / "external" / "production-result.v1.schema.json"
        source_schema.write_text(
            json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "array"}) + "\n",
            encoding="utf-8",
        )
        self._git(source, "add", ".")
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture-update",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        changed_commit = self._git(source, "rev-parse", "HEAD")

        with self.assertRaisesRegex(SchemaSnapshotError, "EXTERNAL-SCHEMA-OVERWRITE"):
            snapshot_schema(
                root,
                source,
                output.as_posix(),
                output,
                source_repository="masa-san-jp/agentic-art-production",
                commit=changed_commit,
                acquired_at="2026-08-12T00:30:00+09:00",
                version="1.0.0",
            )
        self.assertEqual(original, (root / output).read_bytes())


if __name__ == "__main__":
    unittest.main()
