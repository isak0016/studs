"""Headless push/pull/new-project — no GUI needed. Built for the Max for Live
device: `python3 -m studs push --live-set <path>`, etc."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from studs.config import load_config
from studs.sync import (
    add_contributor,
    create_new_project,
    find_project,
    load_projects,
    pull_archive,
    push_archive,
    remote_archive_path_for,
    save_projects,
    upsert_project,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("push", "pull"):
        sp = subparsers.add_parser(name)
        sp.add_argument("--live-set", required=True, type=Path, help="path to the .als file or its project folder")

    new_project = subparsers.add_parser("new-project")
    new_project.add_argument("--live-set", required=True, type=Path)
    new_project.add_argument("--name", default=None, help="optional display name")

    join = subparsers.add_parser("join")
    join.add_argument("--live-set", required=True, type=Path, help="folder to save the project into")
    join.add_argument("--code", required=True, help="sync code someone shared with you")
    join.add_argument("--name", default=None, help="optional display name")

    args = parser.parse_args(argv)

    # A real --live-set from Max's live.path is always an absolute path. An
    # empty string silently normalizes to Path(".") in Python, which would
    # otherwise resolve to wherever this process's cwd happens to be —
    # catch that here instead of quietly operating on the wrong folder.
    if not args.live_set.is_absolute():
        print(f"ERROR: --live-set must be an absolute path, got {args.live_set!r}")
        return 1

    username = load_config().get("username", "").strip()

    if args.command == "new-project":
        return _new_project(args.live_set, args.name, username)

    if args.command == "join":
        return _join(args.live_set, args.code.strip(), args.name)

    project = find_project(load_projects(), args.live_set)
    if project is None:
        print(f"ERROR: no tracked project matches {args.live_set} — use new-project first")
        return 1

    if args.command == "push":
        return _push(project, username)
    return _pull(project)


def _push(project: dict, username: str) -> int:
    if not username:
        print("ERROR: no username set — open studs and set one in settings")
        return 1

    add_contributor(Path(project["path"]), username)
    result = push_archive(project["path"], remote_archive_path_for(project["sync_code"]))
    if not result["ok"]:
        print(f"ERROR: {result['error'] or 'unknown error'}")
        return 1

    projects = upsert_project(
        load_projects(),
        project["path"],
        project["sync_code"],
        project["role"],
        last_pushed=datetime.now(timezone.utc).isoformat(),
        last_pushed_by=username,
        synced_remote_mtime=result["mtime"],
    )
    save_projects(projects)
    name = project.get("display_name") or Path(project["path"]).name
    print(f"OK: pushed {name}")
    return 0


def _pull(project: dict) -> int:
    local_folder = Path(project["path"])
    local_folder.mkdir(parents=True, exist_ok=True)
    result = pull_archive(remote_archive_path_for(project["sync_code"]), str(local_folder))
    if not result["ok"]:
        print(f"ERROR: {result['error'] or 'unknown error'}")
        return 1

    projects = upsert_project(
        load_projects(), project["path"], project["sync_code"], project["role"],
        synced_remote_mtime=result["mtime"],
    )
    save_projects(projects)
    name = project.get("display_name") or local_folder.name
    print(f"OK: pulled {name}")
    return 0


def _join(live_set: Path, code: str, display_name: str | None) -> int:
    if not code:
        print("ERROR: no sync code given")
        return 1

    projects = load_projects()
    existing = find_project(projects, live_set)
    if existing is not None:
        print(f"ERROR: this folder is already tracked (code: {existing['sync_code']})")
        return 1

    live_set.mkdir(parents=True, exist_ok=True)
    result = pull_archive(remote_archive_path_for(code), str(live_set))
    if not result["ok"]:
        print(f"ERROR: {result['error'] or 'unknown error'}")
        return 1

    projects = upsert_project(
        projects,
        str(live_set),
        code,
        "imported",
        display_name=display_name,
        synced_remote_mtime=result["mtime"],
    )
    save_projects(projects)
    print(f"OK: joined project {display_name or live_set.name}")
    return 0


def _new_project(live_set: Path, display_name: str | None, username: str) -> int:
    if not username:
        print("ERROR: no username set — open studs and set one in settings")
        return 1

    folder = live_set if live_set.is_dir() else live_set.parent
    projects = load_projects()

    existing = find_project(projects, folder)
    if existing is not None:
        print(f"ERROR: this project is already tracked (code: {existing['sync_code']})")
        return 1

    result = create_new_project(str(folder), username, display_name)
    if not result["ok"]:
        print(f"ERROR: {result['error'] or 'unknown error'}")
        return 1

    add_contributor(folder, username)
    projects = upsert_project(
        projects,
        str(folder),
        result["sync_code"],
        "created",
        last_pushed=datetime.now(timezone.utc).isoformat(),
        last_pushed_by=username,
        display_name=display_name,
        synced_remote_mtime=result["mtime"],
    )
    save_projects(projects)
    suffix = f" — note: {result['warning']}" if result.get("warning") else ""
    print(f"OK: created project {display_name or folder.name} (code: {result['sync_code']}){suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
