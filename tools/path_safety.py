"""Shared checks for explicit external paths.

macOS exposes the system temporary directory through the lexical ``/var``
alias. It is safe to canonicalize that OS-managed alias, while caller-created
symlinks remain rejected.
"""
from __future__ import annotations

from pathlib import Path


SYSTEM_ALIASES = frozenset({Path("/var"), Path("/tmp")})


def _has_unapproved_symlink(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink() and current not in SYSTEM_ALIASES:
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def external_path(value: object, *, require_exists: bool = False) -> Path:
    """Return a canonical absolute path while rejecting caller symlinks."""
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        raise ValueError("EXPLICIT_NONSYMLINK_PATH_REQUIRED")
    path = Path(value).expanduser()
    if not path.is_absolute() or _has_unapproved_symlink(path):
        raise ValueError("EXPLICIT_NONSYMLINK_PATH_REQUIRED")
    try:
        resolved = path.resolve(strict=require_exists)
    except (OSError, RuntimeError) as exc:
        raise ValueError("EXPLICIT_NONSYMLINK_PATH_REQUIRED") from exc
    if resolved.is_symlink():
        raise ValueError("EXPLICIT_NONSYMLINK_PATH_REQUIRED")
    return resolved
