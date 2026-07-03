"""Shared, pure sync logic — no tkinter here, so both the GUI and the
headless CLI (for the Max for Live device) call the exact same code path."""
from __future__ import annotations

import json
import secrets
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from studs.config import RCLONE_BIN, load_config, save_config
from studs.drive_api import folder_id_by_name, remote_path_for_id

MAX_PROJECTS = 20
CONTRIBUTORS_FILENAME = ".studs_contributors.json"
GDRIVE_REMOTE_NAME = "studs_gdrive"
PROJECT_ARCHIVE_NAME = "project.zip"


def load_projects() -> list[dict]:
    return load_config().get("projects", [])


def save_projects(projects: list[dict]) -> None:
    config = load_config()
    config["projects"] = projects
    save_config(config)


def upsert_project(
    projects: list[dict],
    path: str,
    sync_code: str,
    role: str,
    last_pushed: str | None = None,
    last_pushed_by: str | None = None,
    display_name: str | None = None,
    synced_remote_mtime: str | None = None,
) -> list[dict]:
    existing = next((p for p in projects if p["path"] == path), None)
    if last_pushed is None and existing is not None:
        last_pushed = existing.get("last_pushed")
    if last_pushed_by is None and existing is not None:
        last_pushed_by = existing.get("last_pushed_by")
    if display_name is None and existing is not None:
        display_name = existing.get("display_name")
    if synced_remote_mtime is None and existing is not None:
        synced_remote_mtime = existing.get("synced_remote_mtime")
    projects = [p for p in projects if p["path"] != path]
    projects.insert(
        0,
        {
            "path": path,
            "sync_code": sync_code,
            "role": role,
            "last_pushed": last_pushed,
            "last_pushed_by": last_pushed_by,
            "display_name": display_name,
            "synced_remote_mtime": synced_remote_mtime,
        },
    )
    return projects[:MAX_PROJECTS]


def read_contributors(project_dir: Path) -> list[str]:
    f = project_dir / CONTRIBUTORS_FILENAME
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def add_contributor(project_dir: Path, username: str) -> None:
    if not username:
        return
    contributors = read_contributors(project_dir)
    if username not in contributors:
        contributors.append(username)
        (project_dir / CONTRIBUTORS_FILENAME).write_text(json.dumps(contributors, indent=2))


def zip_folder(folder: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in folder.rglob("*"):
            if file.is_file() and file.name != ".DS_Store":
                zf.write(file, file.relative_to(folder))


def unzip_folder(zip_path: Path, dest_folder: Path) -> None:
    dest_folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_folder)


def remote_archive_mtime(remote_folder_path: str) -> str | None:
    """The archive's Drive modification time — a single lightweight metadata
    call, used to tell whether Drive has a newer push than what's stored
    locally as `synced_remote_mtime`."""
    result = subprocess.run(
        [RCLONE_BIN, "lsjson", remote_folder_path, "--files-only"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    try:
        entries = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    for entry in entries:
        if entry.get("Name") == PROJECT_ARCHIVE_NAME:
            return entry.get("ModTime")
    return None


def remote_archive_path_for(code: str) -> str:
    return f"{remote_path_for_id(GDRIVE_REMOTE_NAME, code)}/{PROJECT_ARCHIVE_NAME}"


def push_archive(local_folder: str, remote_archive_path: str) -> dict:
    """Zips local_folder and uploads it as one file via rclone copyto — one
    Drive API call instead of one per file inside, which is what actually
    caused rate-limit slowdowns with plain `rclone sync` on many-small-file
    projects. Returns {"ok": bool, "error": str | None, "mtime": str | None}."""
    remote_folder_path = remote_archive_path.rsplit("/", 1)[0]
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / PROJECT_ARCHIVE_NAME
        zip_folder(Path(local_folder), zip_path)
        result = subprocess.run(
            [RCLONE_BIN, "copyto", str(zip_path), remote_archive_path], capture_output=True, text=True
        )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr, "mtime": None}
    return {"ok": True, "error": None, "mtime": remote_archive_mtime(remote_folder_path)}


def pull_archive(remote_archive_path: str, local_folder: str) -> dict:
    """rclone copyto the archive down to a temp file, then unzip into
    local_folder. Same return shape as push_archive."""
    remote_folder_path = remote_archive_path.rsplit("/", 1)[0]
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / PROJECT_ARCHIVE_NAME
        result = subprocess.run(
            [RCLONE_BIN, "copyto", remote_archive_path, str(zip_path)], capture_output=True, text=True
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr, "mtime": None}
        unzip_folder(zip_path, Path(local_folder))
    return {"ok": True, "error": None, "mtime": remote_archive_mtime(remote_folder_path)}


def find_project(projects: list[dict], location: Path) -> dict | None:
    """location = a .als file or its containing folder. Resolves both sides
    with .resolve() and matches against each project's stored 'path'.

    Decides file-vs-folder by suffix, not is_dir() — a pull target folder may
    not exist yet at lookup time (it gets mkdir'd after), so existence can't
    be the signal for "this is a folder, not a file"."""
    folder = (location.parent if location.suffix == ".als" else location).resolve()
    for p in projects:
        if Path(p["path"]).resolve() == folder:
            return p
    return None


def create_new_project(local_folder: str, username: str, display_name: str | None = None) -> dict:
    """Pushes local_folder into a fresh, throwaway-named remote folder, then
    resolves its real Drive folder ID — the ID becomes the permanent sync
    code. Returns {"ok": bool, "error": str | None, "sync_code": str | None,
    "mtime": str | None}. Does not persist anything (no add_contributor,
    upsert_project, save_projects) — that's the caller's job, same
    single-writer-owns-persistence discipline as push_archive/pull_archive."""
    temp_name = secrets.token_hex(8)
    remote_folder = f"{GDRIVE_REMOTE_NAME}:studs/{temp_name}"
    remote_archive = f"{remote_folder}/{PROJECT_ARCHIVE_NAME}"

    push_result = push_archive(local_folder, remote_archive)
    if not push_result["ok"]:
        return {"ok": False, "error": push_result["error"], "sync_code": None, "mtime": None}

    folder_id = folder_id_by_name(GDRIVE_REMOTE_NAME, "studs", temp_name)
    if not folder_id:
        return {
            "ok": False,
            "error": "pushed but the drive folder id couldn't be resolved — try again",
            "sync_code": None,
            "mtime": None,
        }

    # Drive's by-ID lookup (what every later push/pull uses) can lag behind
    # its by-name lookup (what we just used above) for a folder that copyto
    # auto-created as a side effect, rather than an explicit mkdir. This delay
    # is sometimes seconds, sometimes much longer (minutes+) — not something
    # worth blocking on synchronously. Take one quick look so a fast-settling
    # folder gets its mtime recorded immediately; if it's not ready yet, still
    # report success (the code is genuinely valid, push_archive did succeed)
    # but flag that push/pull may not work quite yet.
    remote_folder = remote_path_for_id(GDRIVE_REMOTE_NAME, folder_id)
    mtime = remote_archive_mtime(remote_folder)
    if mtime is None:
        time.sleep(3)
        mtime = remote_archive_mtime(remote_folder)

    warning = None if mtime is not None else "drive is still indexing the new folder — push/pull may fail for a bit"
    return {"ok": True, "error": None, "sync_code": folder_id, "mtime": mtime, "warning": warning}
