"""Writes and reads VST scan results as JSON manifests."""
from __future__ import annotations

import json
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path

from .scanner import Plugin, scan_installed_plugins

MANIFEST_VERSION = 1


def build_manifest(formats: list[str] | None = None) -> dict:
    plugins = scan_installed_plugins(formats)
    return {
        "manifest_version": MANIFEST_VERSION,
        "hostname": socket.gethostname(),
        "platform": platform.system(),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "plugins": [p.to_dict() for p in plugins],
    }


def write_manifest(path: Path, formats: list[str] | None = None) -> dict:
    manifest = build_manifest(formats)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=False))
    return manifest


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text())
