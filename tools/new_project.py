from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from _common import EMPTY_JSONL_FILES, atomic_write_text, stable_json


SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def create_project(
    root: Path,
    slug: str,
    title: str,
    creator_id: str | None = None,
    created_at: str | None = None,
    *,
    protocol_root: Path | None = None,
) -> Path:
    if not SLUG.fullmatch(slug):
        raise ValueError("slug must be lower-case kebab-case")
    target = root / "projects" / slug
    if target.exists():
        raise FileExistsError(f"project already exists: {target}")
    template_root = protocol_root.resolve() if protocol_root is not None else root
    template = template_root / "templates" / "project"
    if not template.exists():
        raise FileNotFoundError(f"project template not found: {template}")

    created_at = created_at or datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    replacements = {
        "__PROJECT_SLUG__": slug,
        "__PROJECT_TITLE__": title,
        "__CREATOR_ID__": creator_id if creator_id else "null",
        "__CREATED_AT__": created_at,
    }

    try:
        for source in sorted(template.rglob("*")):
            relative = source.relative_to(template)
            destination = target / relative
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            text = source.read_text(encoding="utf-8")
            for token, replacement in replacements.items():
                text = text.replace(token, replacement)
            atomic_write_text(destination, text)
        for relative in EMPTY_JSONL_FILES:
            path = target / relative
            if not path.exists():
                atomic_write_text(path, "")
        atomic_write_text(target / "07_runtime" / "dependency-index.json", stable_json({"nodes": [], "edges": []}))
        from executive_brief import write_executive_brief

        write_executive_brief(root, f"project/{slug}")
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        raise
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a research project from the canonical template.")
    parser.add_argument("slug")
    parser.add_argument("--title", required=True)
    parser.add_argument("--creator-id")
    parser.add_argument("--root", type=Path, help="compatibility alias for --work-root")
    parser.add_argument("--work-root", type=Path, help="temporary work root or external output staging root")
    parser.add_argument("--protocol-root", type=Path, help="read-only protocol checkout containing templates")
    args = parser.parse_args()
    work_root = args.work_root or args.root
    if work_root is None:
        parser.error("one of --work-root or --root is required")
    try:
        target = create_project(
            work_root.resolve(),
            args.slug,
            args.title,
            args.creator_id,
            protocol_root=args.protocol_root.resolve() if args.protocol_root else None,
        )
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        parser.error(str(exc))
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
