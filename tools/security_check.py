from __future__ import annotations

import argparse
import fnmatch
import os
import re
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from _common import ROOT, load_yaml


IGNORED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class SecurityFinding:
    path: str
    rule: str
    message: str
    remediation: str

    def render(self) -> str:
        return f"{self.path}: [{self.rule}] {self.message}; remediation: {self.remediation}"


def _ignored(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return bool(IGNORED_PARTS.intersection(relative.parts))


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _finding(root: Path, path: Path, rule: str, message: str) -> SecurityFinding:
    return SecurityFinding(
        _relative(root, path),
        rule,
        message,
        "Remove the unsafe object or keep only approved metadata, then rerun validation.",
    )


def _unsafe_member_name(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    if "\x00" in normalized:
        return "NUL byte"
    if normalized.startswith("/") or WINDOWS_DRIVE_RE.match(normalized):
        return "absolute member path"
    if any(part == ".." for part in PurePosixPath(normalized).parts):
        return "parent traversal"
    return None


def _member_boundary_issue(name: str, policy: dict[str, Any]) -> str | None:
    filename = PurePosixPath(name.replace("\\", "/")).name
    extension = Path(filename).suffix.lower()
    forbidden_extensions = set(policy.get("forbidden_extensions", [])) if isinstance(policy, dict) else set()
    forbidden_filenames = policy.get("forbidden_filenames", []) if isinstance(policy, dict) else []
    if extension in forbidden_extensions:
        return "forbidden member extension"
    if any(fnmatch.fnmatch(filename, pattern) for pattern in forbidden_filenames if isinstance(pattern, str)):
        return "forbidden member filename"
    return None


def _archive_limits(policy: dict[str, Any]) -> tuple[set[str], int, int, int]:
    extensions = policy.get("unsafe_archive_extensions", [])
    limits = policy.get("archive_limits", {})
    if not isinstance(extensions, list) or any(not isinstance(item, str) for item in extensions):
        raise ValueError("config/access-policy.yaml: unsafe_archive_extensions must be a string list")
    if not isinstance(limits, dict):
        raise ValueError("config/access-policy.yaml: archive_limits must be a mapping")
    values = tuple(limits.get(key) for key in ("max_members", "max_archive_bytes", "max_member_bytes"))
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in values):
        raise ValueError("config/access-policy.yaml: archive limits must be positive integers")
    return {item.lower() for item in extensions}, values[0], values[1], values[2]


def _is_archive(path: Path, extensions: set[str]) -> bool:
    name = path.name.lower()
    return any(name.endswith(extension) for extension in extensions)


def _scan_zip(root: Path, path: Path, policy: dict[str, Any], limits: tuple[int, int, int]) -> list[SecurityFinding]:
    max_members, _, max_member_bytes = limits
    findings: list[SecurityFinding] = []
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > max_members:
                findings.append(_finding(root, path, "ARCHIVE-LIMIT", "archive has too many members"))
            total = 0
            for member in members:
                issue = _unsafe_member_name(member.filename)
                if issue:
                    findings.append(_finding(root, path, "ARCHIVE-PATH-TRAVERSAL", f"unsafe archive member path ({issue})"))
                boundary = _member_boundary_issue(member.filename, policy)
                if boundary:
                    findings.append(_finding(root, path, "ARCHIVE-DATA-BOUNDARY", boundary))
                total += member.file_size
                if member.file_size > max_member_bytes:
                    findings.append(_finding(root, path, "ARCHIVE-LIMIT", "archive member exceeds the configured size"))
                mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
                    findings.append(_finding(root, path, "ARCHIVE-UNSAFE-OBJECT", "archive contains a link or special file"))
            if total > limits[1]:
                findings.append(_finding(root, path, "ARCHIVE-LIMIT", "archive expands beyond the configured total size"))
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
        findings.append(_finding(root, path, "ARCHIVE-INVALID", "archive cannot be inspected safely"))
    return findings


def _scan_tar(root: Path, path: Path, policy: dict[str, Any], limits: tuple[int, int, int]) -> list[SecurityFinding]:
    max_members, _, max_member_bytes = limits
    findings: list[SecurityFinding] = []
    try:
        with tarfile.open(path, mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) > max_members:
                findings.append(_finding(root, path, "ARCHIVE-LIMIT", "archive has too many members"))
            total = 0
            for member in members:
                issue = _unsafe_member_name(member.name)
                if issue:
                    findings.append(_finding(root, path, "ARCHIVE-PATH-TRAVERSAL", f"unsafe archive member path ({issue})"))
                boundary = _member_boundary_issue(member.name, policy)
                if boundary:
                    findings.append(_finding(root, path, "ARCHIVE-DATA-BOUNDARY", boundary))
                total += max(member.size, 0)
                if member.size > max_member_bytes:
                    findings.append(_finding(root, path, "ARCHIVE-LIMIT", "archive member exceeds the configured size"))
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    findings.append(_finding(root, path, "ARCHIVE-UNSAFE-OBJECT", "archive contains a link or special file"))
            if total > limits[1]:
                findings.append(_finding(root, path, "ARCHIVE-LIMIT", "archive expands beyond the configured total size"))
    except (OSError, tarfile.TarError, EOFError, ValueError):
        findings.append(_finding(root, path, "ARCHIVE-INVALID", "archive cannot be inspected safely"))
    return findings


def _scan_archives(root: Path, policy: dict[str, Any], files: list[Path]) -> list[SecurityFinding]:
    extensions, max_members, max_archive_bytes, max_member_bytes = _archive_limits(policy)
    limits = (max_members, max_archive_bytes, max_member_bytes)
    findings: list[SecurityFinding] = []
    for path in files:
        if not _is_archive(path, extensions):
            continue
        try:
            if path.stat().st_size > max_archive_bytes:
                findings.append(_finding(root, path, "ARCHIVE-LIMIT", "archive exceeds the configured input size"))
                continue
        except OSError:
            findings.append(_finding(root, path, "ARCHIVE-INVALID", "archive metadata cannot be read safely"))
            continue
        if path.name.lower().endswith(".zip"):
            findings.extend(_scan_zip(root, path, policy, limits))
        else:
            findings.extend(_scan_tar(root, path, policy, limits))
    return findings


def scan_advanced_security(root: Path, policy: dict[str, Any] | None = None) -> list[SecurityFinding]:
    root = root.resolve()
    policy = policy if policy is not None else (load_yaml(root / "config" / "access-policy.yaml") or {})
    findings: list[SecurityFinding] = []
    files: list[Path] = []
    for directory, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        directories[:] = [name for name in directories if name not in IGNORED_PARTS]
        for name in list(directories):
            candidate = directory_path / name
            if candidate.is_symlink():
                findings.append(_finding(root, candidate, "SYMLINK", "repository contains a symbolic link"))
                if root not in candidate.resolve().parents:
                    findings.append(_finding(root, candidate, "PATH-TRAVERSAL", "symbolic link resolves outside the repository"))
                directories.remove(name)
        for name in filenames:
            candidate = directory_path / name
            if _ignored(candidate, root):
                continue
            if candidate.is_symlink():
                findings.append(_finding(root, candidate, "SYMLINK", "repository contains a symbolic link"))
                if root not in candidate.resolve().parents:
                    findings.append(_finding(root, candidate, "PATH-TRAVERSAL", "symbolic link resolves outside the repository"))
                continue
            if candidate.is_file():
                files.append(candidate)
    findings.extend(_scan_archives(root, policy, files))
    return sorted(findings, key=lambda item: (item.path, item.rule, item.message))


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect symlinks, path traversal, and unsafe archive objects without extracting archives.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        findings = scan_advanced_security(args.root.resolve())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for finding in findings:
        print(finding.render())
    if findings:
        print(f"FAILED: {len(findings)} advanced security finding(s)")
        return 1
    print("OK: advanced security inspection passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
