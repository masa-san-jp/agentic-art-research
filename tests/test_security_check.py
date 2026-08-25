from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from security_check import scan_advanced_security
from validate import validate_repository


class AdvancedSecurityContractTest(unittest.TestCase):
    def make_root(self) -> Path:
        temporary = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temporary, True)
        shutil.copytree(REPO_ROOT / "config", temporary / "config")
        (temporary / "projects").mkdir()
        (temporary / "data").mkdir()
        return temporary

    def test_clean_root_passes_advanced_security_scan(self) -> None:
        root = self.make_root()
        self.assertEqual([], scan_advanced_security(root))

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_symlink_and_external_target_are_rejected_by_scanner_and_validator(self) -> None:
        root = self.make_root()
        link = root / "data" / "outside-link"
        link.symlink_to(Path(tempfile.gettempdir()), target_is_directory=True)
        findings = scan_advanced_security(root)
        self.assertTrue(any(item.rule == "SYMLINK" for item in findings))
        self.assertTrue(any(item.rule == "PATH-TRAVERSAL" for item in findings))
        repository_findings = validate_repository(root)
        self.assertTrue(any(item.rule == "SYMLINK" for item in repository_findings))

    def test_archive_traversal_link_and_boundary_are_rejected_without_extraction(self) -> None:
        root = self.make_root()
        archive_path = root / "data" / "unsafe.zip"
        symlink_mode = (0o120777 << 16) | 0xA000
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escape.txt", "synthetic")
            archive.writestr("credentials.json", "synthetic")
            link = zipfile.ZipInfo("linked")
            link.create_system = 3
            link.external_attr = symlink_mode
            archive.writestr(link, "../escape")
        findings = scan_advanced_security(root)
        rules = {item.rule for item in findings}
        self.assertIn("ARCHIVE-PATH-TRAVERSAL", rules)
        self.assertIn("ARCHIVE-DATA-BOUNDARY", rules)
        self.assertIn("ARCHIVE-UNSAFE-OBJECT", rules)
        self.assertTrue(any(item.rule == "ARCHIVE-PATH-TRAVERSAL" for item in validate_repository(root)))

    def test_malformed_archive_is_rejected(self) -> None:
        root = self.make_root()
        (root / "data" / "broken.zip").write_bytes(b"not a zip archive")
        findings = scan_advanced_security(root)
        self.assertTrue(any(item.rule == "ARCHIVE-INVALID" for item in findings))

    def test_canonical_project_output_and_nonempty_graph_are_rejected(self) -> None:
        root = self.make_root()
        project = root / "projects" / "actual-project"
        project.mkdir()
        (project / "manifest.yaml").write_text("project: {}\n", encoding="utf-8")
        (root / "data" / "dependency-graph.json").write_text(
            '{"edges": [], "nodes": [{"key": "project/actual-project::EV001"}], "projects": []}\n',
            encoding="utf-8",
        )

        with patch("security_check.ROOT", root):
            findings = scan_advanced_security(root)
            repository_findings = validate_repository(root)

        self.assertTrue(any(item.rule == "PROTOCOL-OUTPUT-BOUNDARY" for item in findings))
        self.assertTrue(any(item.rule == "PROTOCOL-OUTPUT-BOUNDARY" for item in repository_findings))

    def test_temporary_work_root_may_materialize_a_project(self) -> None:
        root = self.make_root()
        project = root / "projects" / "temporary-project"
        project.mkdir()
        (project / "manifest.yaml").write_text("project: {}\n", encoding="utf-8")
        self.assertEqual([], scan_advanced_security(root))


if __name__ == "__main__":
    unittest.main()
