"""Grants per-project Drive folder access via the Drive REST API directly.

Reuses the OAuth token rclone already obtained when the user ran GDrive
Setup, instead of running a second Google auth flow.
"""
from __future__ import annotations

import configparser
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def _rclone_config_path() -> Path:
    result = subprocess.run(["rclone", "config", "file"], capture_output=True, text=True, check=True)
    return Path(result.stdout.strip().splitlines()[-1])


def _access_token(remote: str) -> str:
    # Touch the remote so rclone refreshes a near-expiry token before we read it.
    subprocess.run(["rclone", "lsd", f"{remote}:", "--max-depth", "1"], capture_output=True, text=True)
    cp = configparser.ConfigParser()
    cp.read(_rclone_config_path())
    token = json.loads(cp[remote]["token"])
    return token["access_token"]


def folder_id_by_name(remote: str, parent_path: str, name: str) -> str | None:
    """Look up a subfolder's real Drive ID by listing its parent and matching by name.

    Only needed once, right after a folder is first created — from then on
    the ID itself is used directly to address it (see `remote_path_for_id`).
    """
    result = subprocess.run(
        ["rclone", "lsjson", f"{remote}:{parent_path}", "--dirs-only"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for entry in json.loads(result.stdout or "[]"):
        if entry.get("Name") == name:
            return entry.get("ID")
    return None


def remote_path_for_id(remote: str, folder_id: str) -> str:
    """rclone's special {ID} syntax addresses a Drive object directly, independent
    of its name or location — the same ID resolves for every account it's shared with."""
    return f"{remote}:{{{folder_id}}}"


def share_project_folder(remote: str, folder_id: str, email: str, role: str = "writer") -> None:
    """Grant one Google account access to just this one project's Drive folder."""
    token = _access_token(remote)
    body = json.dumps({"role": role, "type": "user", "emailAddress": email}).encode()
    request = urllib.request.Request(
        f"https://www.googleapis.com/drive/v3/files/{folder_id}/permissions?sendNotificationEmail=true",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Drive API error {e.code}: {e.read().decode()}") from e
