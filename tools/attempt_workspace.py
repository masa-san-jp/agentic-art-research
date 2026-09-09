"""Isolate one task attempt and validate its file changes before promotion."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from _common import atomic_write_text, load_json, load_yaml, stable_json
from canonical import canonical_sha256


RUN_ID_RE = re.compile(r"^HR[0-9]{3,}$")
TASK_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]{2,}$")
ATTEMPT_ID_RE = re.compile(r"^AT[A-Za-z0-9_-]{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s\"'])(/(?:[^\s\"']+/)*[^\s\"']*)|\b[A-Za-z]:[\\/][^\s\"']*")
SKIP_NAMES = {".git", ".venv", "__pycache__", ".pytest_cache"}


def _has_unsafe_locator_symlink(path: Path) -> bool:
    """Reject locator symlinks while tolerating macOS system path aliases."""
    macos_aliases = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
        Path("/etc"): Path("/private/etc"),
    }
    for part in (path, *path.parents):
        if not part.is_symlink():
            continue
        if sys.platform == "darwin" and macos_aliases.get(part) == part.resolve(strict=False):
            continue
        return True
    return False


class AttemptWorkspaceError(ValueError):
    """A workspace or changeset cannot be accepted safely."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        self.message = message
        super().__init__(f"{rule}: {message}")


@dataclass(frozen=True)
class AttemptWorkspace:
    root: Path
    project: Path
    baseline_manifest: Path
    protected_manifest: Path
    changeset: Path
    project_id: str
    run_id: str
    task_id: str
    attempt_id: str
    role: str
    protocol_root: Path | None = None
    work_root: Path | None = None
    output_root: Path | None = None


def _validate_id(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", f"{label} is not a safe stable identifier")
    return value


def _project_path(work_root: Path, project_id: str) -> Path:
    if not isinstance(project_id, str) or not project_id.startswith("project/"):
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "project_id must be project/<slug>")
    slug = project_id.split("/", 1)[1]
    if not SLUG_RE.fullmatch(slug):
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "project_id contains an unsafe slug")
    projects = work_root.resolve() / "projects"
    project = (projects / slug).resolve()
    if project.parent != projects.resolve() or not project.is_dir() or project.is_symlink():
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "project is not a direct safe work-root project")
    return project


def _relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "write target must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", f"path escapes the project: {value!r}")
    return path.as_posix()


def _schema_validator(protocol_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(protocol_root / "schemas" / f"{name}.schema.json")
    common = load_json(protocol_root / "schemas" / "common.schema.json")
    registry = Registry().with_resources([(common["$id"], Resource.from_contents(common))])
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=registry)


def _validate_schema(protocol_root: Path, name: str, value: dict[str, Any]) -> None:
    validator = _schema_validator(protocol_root, name)
    if any(validator.iter_errors(value)):
        raise AttemptWorkspaceError("ATTEMPT-CHANGESET-SCHEMA", f"{name} contract is invalid")


def _policy(protocol_root: Path) -> tuple[dict[str, Any], list[re.Pattern[str]]]:
    try:
        value = load_yaml(protocol_root / "config" / "access-policy.yaml") or {}
    except Exception as exc:
        raise AttemptWorkspaceError("ATTEMPT-SECURITY", "access policy cannot be read") from exc
    if not isinstance(value, dict):
        raise AttemptWorkspaceError("ATTEMPT-SECURITY", "access policy is not a mapping")
    patterns: list[re.Pattern[str]] = []
    for item in value.get("secret_patterns", []):
        if not isinstance(item, dict) or not isinstance(item.get("pattern"), str):
            raise AttemptWorkspaceError("ATTEMPT-SECURITY", "access policy secret pattern is invalid")
        try:
            patterns.append(re.compile(item["pattern"]))
        except re.error as exc:
            raise AttemptWorkspaceError("ATTEMPT-SECURITY", "access policy secret pattern is invalid") from exc
    limits = value.get("attempt_workspace_limits", {})
    if not isinstance(limits, dict):
        raise AttemptWorkspaceError("ATTEMPT-SECURITY", "attempt workspace limits are invalid")
    for key in ("max_file_bytes", "max_total_bytes"):
        if isinstance(limits.get(key), bool) or not isinstance(limits.get(key), int) or limits[key] <= 0:
            raise AttemptWorkspaceError("ATTEMPT-SECURITY", f"{key} must be a positive integer")
    return value, patterns


def _safe_entries(
    root: Path,
    *,
    policy: dict[str, Any],
    prefix: str = "",
    skip: set[str] | None = None,
    reject_hardlinks: bool = False,
) -> tuple[list[dict[str, Any]], int, int]:
    root = root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "snapshot root must be a real directory")
    max_file = int(policy["attempt_workspace_limits"]["max_file_bytes"])
    max_total = int(policy["attempt_workspace_limits"]["max_total_bytes"])
    entries: list[dict[str, Any]] = []
    normalized_names: dict[str, str] = {}
    total = 0
    file_count = 0
    skipped = skip or set()

    def visit(directory: Path, relative: str) -> None:
        nonlocal total, file_count
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "snapshot directory cannot be read") from exc
        for child in children:
            child_rel = f"{relative}/{child.name}" if relative else child.name
            child_rel = child_rel.replace(os.sep, "/")
            relative_without_prefix = child_rel[len(prefix.rstrip("/")) + 1:] if prefix else child_rel
            if any(
                relative_without_prefix == item or relative_without_prefix.startswith(item.rstrip("/") + "/")
                for item in skipped
            ):
                continue
            try:
                safe_rel = _relative_path(child_rel)
                normalized = unicodedata.normalize("NFC", safe_rel).casefold()
                previous = normalized_names.get(normalized)
                if previous is not None and previous != safe_rel:
                    raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "case or Unicode-normalization collision detected")
                normalized_names[normalized] = safe_rel
                info = child.stat(follow_symlinks=False)
                mode = stat.S_IMODE(info.st_mode)
                if stat.S_ISLNK(info.st_mode):
                    raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "symlink is not allowed in an attempt tree")
                if stat.S_ISDIR(info.st_mode):
                    entries.append({"path": safe_rel, "type": "directory", "mode": mode, "size": 0, "sha256": None})
                    visit(Path(child.path), safe_rel)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "device, socket, fifo, or other special file is not allowed")
                if reject_hardlinks and info.st_nlink != 1:
                    raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "hard-linked files are not allowed")
                if info.st_size > max_file:
                    raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "file exceeds the attempt size limit")
                digest = hashlib.sha256()
                size = 0
                with Path(child.path).open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if size > max_file or total > max_total:
                            raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "attempt file set exceeds the configured size limit")
                        digest.update(chunk)
                file_count += 1
                entries.append({
                    "path": safe_rel,
                    "type": "file",
                    "mode": mode,
                    "size": size,
                    "sha256": f"sha256:{digest.hexdigest()}",
                })
            except AttemptWorkspaceError:
                raise
            except (OSError, UnicodeError) as exc:
                raise AttemptWorkspaceError("ATTEMPT-UNSAFE-FILE", "filesystem entry cannot be inspected safely") from exc

    visit(root, prefix.rstrip("/"))
    entries.sort(key=lambda item: item["path"])
    return entries, file_count, total


def _manifest(
    *,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    role: str,
    kind: str,
    entries: list[dict[str, Any]],
    file_count: int,
    total_bytes: int,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "role": role,
        "kind": kind,
        "entries": entries,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return canonical_sha256(manifest)


def snapshot_project(
    project: Path,
    *,
    project_id: str = "project/snapshot",
    run_id: str = "HR000",
    task_id: str = "SNAPSHOT",
    attempt_id: str = "AT000",
    role: str = "snapshot",
    protocol_root: Path | None = None,
    reject_hardlinks: bool = False,
) -> dict[str, Any]:
    policy, _ = _policy(protocol_root or project.parents[1])
    entries, file_count, total = _safe_entries(project, policy=policy, reject_hardlinks=reject_hardlinks)
    value = _manifest(
        project_id=project_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        role=role,
        kind="PROJECT",
        entries=entries,
        file_count=file_count,
        total_bytes=total,
    )
    if protocol_root is not None:
        _validate_schema(protocol_root, "attempt-workspace-manifest", value)
    return value


def _protected_snapshot(
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    role: str,
) -> dict[str, Any]:
    roots = [("protocol", protocol_root, set(SKIP_NAMES) | {".harness"})]
    slug = project_id.split("/", 1)[1]
    roots.append(("work", work_root, set(SKIP_NAMES) | {".harness", f"projects/{slug}"}))
    roots.append(("output", output_root, set(SKIP_NAMES) | {".harness"}))
    all_entries: list[dict[str, Any]] = []
    file_count = 0
    total = 0
    seen_roots: list[Path] = []
    for label, root, skipped in roots:
        resolved = root.resolve()
        if any(resolved == previous or resolved in previous.parents or previous in resolved.parents for previous in seen_roots):
            raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "protected roots overlap")
        seen_roots.append(resolved)
        entries, count, size = _safe_entries(resolved, policy=_policy(protocol_root)[0], prefix=label, skip=skipped)
        all_entries.extend(entries)
        file_count += count
        total += size
    all_entries.sort(key=lambda item: item["path"])
    return _manifest(
        project_id=project_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        role=role,
        kind="PROTECTED_ROOT",
        entries=all_entries,
        file_count=file_count,
        total_bytes=total,
    )


def _copy_project(source: Path, destination: Path, manifest: dict[str, Any]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for entry in manifest["entries"]:
        target = destination / entry["path"]
        if entry["type"] == "directory":
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, entry["mode"])
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / entry["path"], target)
            os.chmod(target, entry["mode"])


def _attempt_paths(
    work_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    role: str,
    *,
    protocol_root: Path | None = None,
    output_root: Path | None = None,
) -> AttemptWorkspace:
    _validate_id(run_id, RUN_ID_RE, "run_id")
    _validate_id(task_id, TASK_ID_RE, "task_id")
    _validate_id(attempt_id, ATTEMPT_ID_RE, "attempt_id")
    if not isinstance(role, str) or not role or "/" in role or "\\" in role:
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "role is not a safe identifier")
    work = work_root.resolve()
    project = _project_path(work, project_id)
    root = work / ".harness" / "attempts" / run_id / task_id / attempt_id
    if root == project or project in root.parents or root in project.parents:
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "attempt root overlaps canonical project")
    return AttemptWorkspace(
        root=root,
        project=root / "project",
        baseline_manifest=root / "baseline-manifest.json",
        protected_manifest=root / "protected-manifest.json",
        changeset=root / "changeset.json",
        project_id=project_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        role=role,
        protocol_root=protocol_root.resolve() if protocol_root else None,
        work_root=work,
        output_root=output_root.resolve() if output_root else None,
    )


def load_attempt(path: Path, *, project_id: str, run_id: str, task_id: str, attempt_id: str, role: str) -> AttemptWorkspace:
    root = path.resolve()
    return AttemptWorkspace(
        root=root,
        project=root / "project",
        baseline_manifest=root / "baseline-manifest.json",
        protected_manifest=root / "protected-manifest.json",
        changeset=root / "changeset.json",
        project_id=project_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        role=role,
    )


def load_write_targets(protocol_root: Path, role: str) -> list[dict[str, Any]]:
    try:
        config = load_yaml(protocol_root / "config" / "task-roles.yaml") or {}
    except Exception as exc:
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "task role configuration cannot be read") from exc
    role_entry = (config.get("roles") or {}).get(role) if isinstance(config, dict) else None
    if not isinstance(role_entry, dict):
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", f"role {role!r} is not configured")
    targets: list[dict[str, Any]] = []
    normalized: dict[str, dict[str, Any]] = {}
    for raw in role_entry.get("write_targets") or []:
        if not isinstance(raw, dict) or raw.get("namespace", "worker") != "worker":
            continue
        target = dict(raw)
        target["path"] = _relative_path(target.get("path"))
        if target["path"] in normalized:
            raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "duplicate normalized write target")
        normalized[target["path"]] = target
        targets.append(target)
    return targets


def create_attempt_workspace(
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    role: str,
) -> AttemptWorkspace:
    """Create or resume an isolated attempt workspace from the canonical project."""

    protocol = protocol_root.resolve()
    work = work_root.resolve()
    output = output_root.resolve()
    attempt = _attempt_paths(work, project_id, run_id, task_id, attempt_id, role, protocol_root=protocol, output_root=output)
    if not protocol.is_dir() or not output.is_dir():
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "protocol and output roots must be existing directories")
    project = _project_path(work, project_id)
    targets = load_write_targets(protocol, role)
    baseline = snapshot_project(project, project_id=project_id, run_id=run_id, task_id=task_id, attempt_id=attempt_id, role=role, protocol_root=protocol, reject_hardlinks=False)
    protected = _protected_snapshot(
        protocol_root=protocol,
        work_root=work,
        output_root=output,
        project_id=project_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        role=role,
    )
    baseline_hash = manifest_sha256(baseline)
    if attempt.root.exists():
        if not attempt.baseline_manifest.is_file() or not attempt.project.is_dir():
            raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "existing attempt workspace is incomplete")
        existing = load_json(attempt.baseline_manifest)
        if manifest_sha256(existing) != baseline_hash:
            raise AttemptWorkspaceError("ATTEMPT-BASELINE-CONFLICT", "attempt ID already has a different baseline")
        existing_protected = load_json(attempt.protected_manifest)
        if manifest_sha256(existing_protected) != manifest_sha256(protected):
            raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "protected-root baseline changed for an existing attempt")
        return attempt

    attempts_parent = attempt.root.parent
    attempts_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{attempt_id}.", dir=attempts_parent))
    try:
        staged_project = staging / "project"
        _copy_project(project, staged_project, baseline)
        atomic_write_text(staging / "baseline-manifest.json", stable_json(baseline))
        atomic_write_text(staging / "protected-manifest.json", stable_json(protected))
        atomic_write_text(staging / "metadata.json", stable_json({
            "schema_version": "1.0.0",
            "project_id": project_id,
            "run_id": run_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "role": role,
            "write_targets": targets,
        }))
        os.replace(staging, attempt.root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return attempt


prepare_attempt = create_attempt_workspace


def _entry_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(entry["path"]): entry for entry in manifest.get("entries", [])}


def build_changeset(
    baseline: dict[str, Any],
    after: dict[str, Any],
    *,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    role: str,
    protocol_root: Path | None = None,
) -> dict[str, Any]:
    before_map = _entry_map(baseline)
    after_map = _entry_map(after)
    added = sorted(set(after_map) - set(before_map))
    deleted = sorted(set(before_map) - set(after_map))
    common = sorted(set(before_map) & set(after_map))
    changes: list[dict[str, Any]] = []
    for path in common:
        before = before_map[path]
        current = after_map[path]
        if before != current:
            changes.append({
                "operation": "MODIFY",
                "path": path,
                "before_sha256": before["sha256"],
                "after_sha256": current["sha256"],
                "size": current["size"],
            })
    deleted_by_hash: dict[str, list[str]] = {}
    for path in deleted:
        digest = before_map[path].get("sha256")
        if digest:
            deleted_by_hash.setdefault(digest, []).append(path)
    renamed_deleted: set[str] = set()
    renamed_added: set[str] = set()
    for path in added:
        digest = after_map[path].get("sha256")
        candidates = deleted_by_hash.get(digest or "", [])
        if candidates:
            source = candidates.pop(0)
            renamed_deleted.add(source)
            renamed_added.add(path)
            changes.append({
                "operation": "RENAME",
                "path": path,
                "from_path": source,
                "before_sha256": before_map[source]["sha256"],
                "after_sha256": after_map[path]["sha256"],
                "size": after_map[path]["size"],
            })
    for path in added:
        if path in renamed_added:
            continue
        current = after_map[path]
        changes.append({
            "operation": "ADD",
            "path": path,
            "before_sha256": None,
            "after_sha256": current["sha256"],
            "size": current["size"],
        })
    for path in deleted:
        if path in renamed_deleted:
            continue
        before = before_map[path]
        changes.append({
            "operation": "DELETE",
            "path": path,
            "before_sha256": before["sha256"],
            "after_sha256": None,
            "size": 0,
        })
    changes.sort(key=lambda item: (item["path"], item["operation"], item.get("from_path", "")))
    result = {
        "schema_version": "1.0.0",
        "project_id": project_id,
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "role": role,
        "baseline_manifest_sha256": manifest_sha256(baseline),
        "after_manifest_sha256": manifest_sha256(after),
        "changes": changes,
    }
    if protocol_root is not None:
        _validate_schema(protocol_root, "attempt-changeset", result)
    return result


def _security_check_file(path: Path, relative: str, protocol_root: Path) -> None:
    policy, patterns = _policy(protocol_root)
    filename = PurePosixPath(relative).name
    extension = Path(filename).suffix.lower()
    forbidden_extensions = {str(item).lower() for item in policy.get("forbidden_extensions", [])}
    forbidden_names = policy.get("forbidden_filenames", [])
    if extension in forbidden_extensions or any(Path(filename).match(str(item)) for item in forbidden_names):
        raise AttemptWorkspaceError("ATTEMPT-SECURITY", "changed file uses a forbidden extension or filename")
    content = path.read_bytes()
    text = content.decode("utf-8", errors="replace")
    if any(pattern.search(text) for pattern in patterns):
        raise AttemptWorkspaceError("ATTEMPT-SECURITY", "changed file contains a configured secret pattern")
    if "PRIVATE_RAW" in text or "RESTRICTED" in text:
        raise AttemptWorkspaceError("ATTEMPT-SECURITY", "changed file contains a prohibited access classification")
    if relative == "04_decisions/cumulative-specificity-request.json":
        # This local-only input contract explicitly carries owner payload/store
        # locators. Preserve secret/classification checks above and scan every
        # other value below; never exempt prose or an entire JSON document.
        try:
            document = json.loads(text)
            _validate_schema(protocol_root, "cumulative-specificity-request", document)
            locators = [(row, "payload_path", False) for row in document["inputs"]]
            if document["memory_query"] is not None:
                locators.append((document["memory_query"], "store_root", True))
            for row, key, directory in locators:
                value = row[key]
                if not isinstance(value, str):
                    raise ValueError("locator must be a string")
                target = Path(value)
                if (not target.is_absolute() or ".." in target.parts
                        or _has_unsafe_locator_symlink(target)
                        or (not target.is_dir() if directory else not target.is_file())):
                    raise ValueError("locator is missing, relative, traversing, or symlinked")
                row[key] = "validated-owner-local-locator"
            text = json.dumps(document, ensure_ascii=False)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise AttemptWorkspaceError("ATTEMPT-SECURITY", "invalid cumulative knowledge locator contract") from exc
    if ABSOLUTE_PATH_RE.search(text):
        raise AttemptWorkspaceError("ATTEMPT-SECURITY", "changed file contains an absolute local path")


def validate_changeset(
    changeset: dict[str, Any],
    *,
    baseline_root: Path,
    after_root: Path,
    protocol_root: Path,
    role: str,
) -> dict[str, Any]:
    _validate_schema(protocol_root, "attempt-changeset", changeset)
    targets = {target["path"]: target for target in load_write_targets(protocol_root, role)}
    for change in changeset["changes"]:
        paths = [change["path"]]
        if change["operation"] == "RENAME":
            paths.append(change["from_path"])
        if any(path not in targets for path in paths):
            raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "changeset contains a path outside the worker write set")
        target = targets[change["path"]]
        mode = target.get("mode", "UPDATE")
        if mode == "CREATE" and change["operation"] != "ADD":
            raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "CREATE target was not added")
        if mode == "APPEND" and change["operation"] != "MODIFY":
            raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "APPEND target was not modified")
        if mode == "APPEND":
            before_path = baseline_root / change["path"]
            after_path = after_root / change["path"]
            if not before_path.is_file() or not after_path.is_file() or not after_path.read_bytes().startswith(before_path.read_bytes()):
                raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "APPEND target changed content before its baseline")
        if change["operation"] in {"ADD", "MODIFY", "RENAME"}:
            target_path = after_root / change["path"]
            if not target_path.is_file():
                raise AttemptWorkspaceError("ATTEMPT-CHANGESET-SCHEMA", "changeset target is not a regular file")
            _security_check_file(target_path, change["path"], protocol_root)
    return changeset


def verify_protected_roots(
    attempt: AttemptWorkspace,
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
) -> None:
    baseline = load_json(attempt.protected_manifest)
    current = _protected_snapshot(
        protocol_root=protocol_root.resolve(),
        work_root=work_root.resolve(),
        output_root=output_root.resolve(),
        project_id=attempt.project_id,
        run_id=attempt.run_id,
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        role=attempt.role,
    )
    if manifest_sha256(baseline) != manifest_sha256(current):
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "a protected protocol, work, or output root changed")


def inspect_attempt(
    attempt: AttemptWorkspace,
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    verify_protected_roots(attempt, protocol_root=protocol_root, work_root=work_root, output_root=output_root)
    baseline = load_json(attempt.baseline_manifest)
    canonical_project = _project_path(work_root.resolve(), attempt.project_id)
    canonical_before = snapshot_project(
        canonical_project,
        project_id=attempt.project_id,
        run_id=attempt.run_id,
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        role=attempt.role,
        protocol_root=protocol_root,
        reject_hardlinks=False,
    )
    if manifest_sha256(canonical_before) != manifest_sha256(baseline):
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "worker changed the canonical project instead of its attempt workspace")
    after = snapshot_project(
        attempt.project,
        project_id=attempt.project_id,
        run_id=attempt.run_id,
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        role=attempt.role,
        protocol_root=protocol_root,
        reject_hardlinks=True,
    )
    changeset = build_changeset(
        baseline,
        after,
        project_id=attempt.project_id,
        run_id=attempt.run_id,
        task_id=attempt.task_id,
        attempt_id=attempt.attempt_id,
        role=attempt.role,
        protocol_root=protocol_root,
    )
    validate_changeset(
        changeset,
        baseline_root=canonical_project,
        after_root=attempt.project,
        protocol_root=protocol_root,
        role=attempt.role,
    )
    atomic_write_text(attempt.changeset, stable_json(changeset))
    return changeset


@contextlib.contextmanager
def _project_lock(work_root: Path, project_id: str) -> Iterator[None]:
    import fcntl

    lock_dir = work_root.resolve() / ".harness" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / (hashlib.sha256(project_id.encode("utf-8")).hexdigest() + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _apply_changes(candidate: Path, source: Path, changes: list[dict[str, Any]]) -> None:
    ordered = sorted(changes, key=lambda item: (item["operation"] == "DELETE", -item["path"].count("/"), item["path"]))
    for change in ordered:
        operation = change["operation"]
        destination = candidate / change["path"]
        if operation == "DELETE":
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            elif destination.exists() or destination.is_symlink():
                destination.unlink()
            continue
        if operation == "RENAME":
            old = candidate / change["from_path"]
            if old.exists() or old.is_symlink():
                if old.is_dir() and not old.is_symlink():
                    shutil.rmtree(old)
                else:
                    old.unlink()
        if destination.exists() and destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / change["path"], destination)
        os.chmod(destination, stat.S_IMODE((source / change["path"]).stat().st_mode))


def promote_attempt(
    attempt: AttemptWorkspace,
    *,
    protocol_root: Path,
    work_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Validate and atomically swap an accepted attempt tree into the project."""

    protocol = protocol_root.resolve()
    work = work_root.resolve()
    project = _project_path(work, attempt.project_id)
    with _project_lock(work, attempt.project_id):
        verify_protected_roots(attempt, protocol_root=protocol, work_root=work, output_root=output_root)
        baseline = load_json(attempt.baseline_manifest)
        current = snapshot_project(
            project,
            project_id=attempt.project_id,
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
            role=attempt.role,
            protocol_root=protocol,
            reject_hardlinks=False,
        )
        if manifest_sha256(current) != manifest_sha256(baseline):
            raise AttemptWorkspaceError("ATTEMPT-BASELINE-CONFLICT", "canonical project changed after the attempt baseline")
        after = snapshot_project(
            attempt.project,
            project_id=attempt.project_id,
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
            role=attempt.role,
            protocol_root=protocol,
            reject_hardlinks=True,
        )
        changeset = build_changeset(
            baseline,
            after,
            project_id=attempt.project_id,
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
            role=attempt.role,
            protocol_root=protocol,
        )
        validate_changeset(
            changeset,
            baseline_root=project,
            after_root=attempt.project,
            protocol_root=protocol,
            role=attempt.role,
        )
        if not changeset["changes"]:
            atomic_write_text(attempt.changeset, stable_json(changeset))
            return changeset
        candidate = Path(tempfile.mkdtemp(prefix=f".{project.name}.candidate.", dir=project.parent))
        backup = project.parent / f".{project.name}.backup.{attempt.attempt_id}"
        try:
            shutil.rmtree(candidate)
            _copy_project(project, candidate, current)
            _apply_changes(candidate, attempt.project, changeset["changes"])
            candidate_manifest = snapshot_project(
                candidate,
                project_id=attempt.project_id,
                run_id=attempt.run_id,
                task_id=attempt.task_id,
                attempt_id=attempt.attempt_id,
                role=attempt.role,
                protocol_root=protocol,
                reject_hardlinks=True,
            )
            if manifest_sha256(candidate_manifest) != manifest_sha256(after):
                raise AttemptWorkspaceError("ATTEMPT-CHANGESET-SCHEMA", "candidate tree does not match the accepted after manifest")
            if backup.exists():
                raise AttemptWorkspaceError("ATTEMPT-BASELINE-CONFLICT", "promotion backup path already exists")
            os.replace(project, backup)
            try:
                os.replace(candidate, project)
            except Exception:
                os.replace(backup, project)
                raise
            shutil.rmtree(backup)
        finally:
            if candidate.exists():
                shutil.rmtree(candidate, ignore_errors=True)
            if backup.exists() and not project.exists():
                os.replace(backup, project)
        atomic_write_text(attempt.changeset, stable_json(changeset))
        return changeset


def quarantine_attempt(attempt: AttemptWorkspace, *, work_root: Path, reason: str = "crash") -> Path:
    if not isinstance(reason, str) or not reason or "/" in reason or "\\" in reason:
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "quarantine reason is not a safe label")
    work = work_root.resolve()
    root = attempt.root.resolve()
    attempts_root = work / ".harness" / "attempts"
    if attempts_root not in root.parents:
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "attempt is outside the work-root attempt namespace")
    target = work / ".harness" / "quarantine" / attempt.run_id / attempt.task_id / f"{attempt.attempt_id}-{reason}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise AttemptWorkspaceError("ATTEMPT-BASELINE-CONFLICT", "quarantine destination already exists")
    os.replace(root, target)
    return target


def recover_quarantined_attempt(
    quarantine: Path,
    *,
    work_root: Path,
    project_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    role: str,
) -> AttemptWorkspace:
    work = work_root.resolve()
    source = quarantine.resolve()
    quarantine_root = work / ".harness" / "quarantine"
    if quarantine_root not in source.parents:
        raise AttemptWorkspaceError("ATTEMPT-WRITE-BOUNDARY", "quarantine path is outside the work-root namespace")
    target = work / ".harness" / "attempts" / run_id / task_id / attempt_id
    if target.exists():
        raise AttemptWorkspaceError("ATTEMPT-BASELINE-CONFLICT", "attempt recovery destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    return load_attempt(target, project_id=project_id, run_id=run_id, task_id=task_id, attempt_id=attempt_id, role=role)
