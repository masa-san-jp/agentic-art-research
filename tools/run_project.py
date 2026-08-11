from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from _common import ROOT, atomic_write_text, load_yaml, stable_json
from build_graph import build_graph
from new_project import create_project
from validate import validate_repository


def run_offline_fixture(root: Path, slug: str, fixture: Path) -> Path:
    if not fixture.is_dir():
        raise FileNotFoundError(f"offline fixture not found: {fixture}")
    metadata_path = fixture / "metadata.yaml"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"offline fixture metadata not found: {metadata_path}")
    metadata = load_yaml(metadata_path) or {}
    if not isinstance(metadata, dict):
        raise ValueError("offline fixture metadata must be a mapping")
    project_metadata = metadata.get("project") or {}
    if not isinstance(project_metadata, dict):
        raise ValueError("offline fixture metadata.project must be a mapping")
    fixture_slug = project_metadata.get("slug")
    if fixture_slug != slug:
        raise ValueError(f"fixture slug {fixture_slug!r} does not match target {slug!r}")
    title = project_metadata.get("title")
    if not isinstance(title, str) or not title:
        raise ValueError("offline fixture metadata.project.title must be a non-empty string")
    creator_id = project_metadata.get("creator_id")
    created_at = project_metadata.get("created_at")
    if creator_id is not None and not isinstance(creator_id, str):
        raise ValueError("offline fixture metadata.project.creator_id must be a string or null")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError("offline fixture metadata.project.created_at must be a timestamp string")

    target = create_project(root, slug, title, creator_id, created_at=created_at)
    try:
        for source in sorted(fixture.rglob("*")):
            relative = source.relative_to(fixture)
            if relative == Path("metadata.yaml") or source.is_dir():
                continue
            destination = (target / relative).resolve()
            if target.resolve() not in destination.parents:
                raise ValueError(f"offline fixture file escapes project: {relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

        findings = validate_repository(root)
        if findings:
            rendered = "\n".join(finding.render() for finding in findings)
            raise ValueError(f"offline fixture validation failed:\n{rendered}")
        graph = build_graph(root)
        output = root / "data" / "dependency-graph.json"
        atomic_write_text(output, stable_json(graph))
        return target
    except Exception:
        shutil.rmtree(target)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize and validate a deterministic offline project fixture.")
    parser.add_argument("slug")
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        target = run_offline_fixture(args.root.resolve(), args.slug, args.offline_fixture.resolve())
    except (FileNotFoundError, ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
