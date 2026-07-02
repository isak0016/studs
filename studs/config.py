"""Shared access to the local ~/.studs/config.json file."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

CONFIG_DIR = Path.home() / ".studs"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _find_rclone() -> str:
    # A plain "rclone" only resolves via PATH, which GUI-launched processes
    # (e.g. Ableton spawning node.script -> python3 -> subprocess) don't
    # inherit from the shell — resolve to an absolute path once instead.
    found = shutil.which("rclone")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/rclone", "/usr/local/bin/rclone"):
        if Path(candidate).exists():
            return candidate
    return "rclone"  # last resort — same behavior as before this fix


RCLONE_BIN = _find_rclone()


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
