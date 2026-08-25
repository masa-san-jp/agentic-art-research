"""Path contracts shared by the agent harness entry points.

The repository is the protocol source of truth.  A harness run gets a
separate work root for materialized projects and a separate output root for
successful project artifacts.  Keeping this contract in one module prevents
individual CLIs from quietly reintroducing the old single-root assumption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


PROJECT_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class HarnessPathError(ValueError):
    """Raised when a harness root would cross a repository boundary."""

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        super().__init__(f"{rule}: {message}")


def _is_within(candidate: Path, parent: Path) -> bool:
    return candidate == parent or parent in candidate.parents


def _resolve_root(value: Path, label: str, *, must_exist: bool) -> Path:
    if not isinstance(value, Path):
        value = Path(value)
    if value.is_symlink():
        raise HarnessPathError("HARNESS-ROOT-BOUNDARY", f"{label} must not be a symbolic link: {value}")
    resolved = value.expanduser().resolve()
    if must_exist and not resolved.is_dir():
        raise HarnessPathError("HARNESS-ROOT-BOUNDARY", f"{label} must be an existing directory: {resolved}")
    if resolved.exists() and not resolved.is_dir():
        raise HarnessPathError("HARNESS-ROOT-BOUNDARY", f"{label} must be a directory: {resolved}")
    # A root at the filesystem or user-home level is almost certainly an
    # accidental broad target.  A temporary directory below /tmp or a named
    # external output directory remains valid.
    broad_roots = {
        Path(resolved.anchor),
        Path.home(),
        Path.home().parent,
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var/tmp"),
    }
    if resolved in broad_roots:
        raise HarnessPathError("HARNESS-ROOT-BROAD", f"{label} is too broad: {resolved}")
    return resolved


@dataclass(frozen=True)
class HarnessPaths:
    protocol_root: Path
    work_root: Path
    output_root: Path

    @classmethod
    def resolve(
        cls,
        protocol_root: Path,
        work_root: Path,
        output_root: Path,
    ) -> "HarnessPaths":
        protocol = _resolve_root(protocol_root, "protocol_root", must_exist=True)
        work = _resolve_root(work_root, "work_root", must_exist=False)
        output = _resolve_root(output_root, "output_root", must_exist=False)

        required = ("config", "schemas", "templates", "tools")
        missing = [name for name in required if not (protocol / name).is_dir()]
        if missing:
            raise HarnessPathError(
                "HARNESS-PROTOCOL-PROVENANCE",
                f"protocol_root is not an agentic-art-research protocol checkout; missing {', '.join(missing)}",
            )
        if work == output:
            raise HarnessPathError("HARNESS-ROOT-BOUNDARY", "work_root and output_root must be different directories")

        roots = (("protocol_root", protocol), ("work_root", work), ("output_root", output))
        for index, (left_name, left) in enumerate(roots):
            for right_name, right in roots[index + 1 :]:
                if _is_within(left, right) or _is_within(right, left):
                    raise HarnessPathError(
                        "HARNESS-ROOT-BOUNDARY",
                        f"{left_name} and {right_name} must not contain one another",
                    )
        return cls(protocol, work, output)

    def project_path(self, slug: str) -> Path:
        if not isinstance(slug, str) or not PROJECT_SLUG.fullmatch(slug):
            raise HarnessPathError("HARNESS-ROOT-BOUNDARY", f"invalid project slug: {slug!r}")
        return self.work_root / "projects" / slug

    def as_json(self) -> dict[str, str]:
        return {
            "protocol_root": str(self.protocol_root),
            "work_root": str(self.work_root),
            "output_root": str(self.output_root),
        }


def ensure_empty_directory(path: Path, label: str) -> None:
    """Create a root only when it is empty; never overwrite existing data."""

    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise HarnessPathError("HARNESS-BOOTSTRAP-CONFLICT", f"{label} is not a regular directory: {path}")
        if any(path.iterdir()):
            raise HarnessPathError("HARNESS-BOOTSTRAP-CONFLICT", f"{label} is not empty: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
