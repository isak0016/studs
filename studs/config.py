"""Shared access to the local ~/.studs/config.json file."""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".studs"
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
