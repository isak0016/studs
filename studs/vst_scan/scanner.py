"""Discovers VST2/VST3 plugins installed on the current machine."""
from __future__ import annotations

import platform
import plistlib
from dataclasses import dataclass
from pathlib import Path

from studs.config import load_config

# Standard plugin install locations per OS. Windows paths use environment
# variables so they resolve correctly regardless of drive/locale.
_MAC_VST3_DIRS = [
    Path.home() / "Library/Audio/Plug-Ins/VST3",
    Path("/Library/Audio/Plug-Ins/VST3"),
]
_MAC_VST2_DIRS = [
    Path.home() / "Library/Audio/Plug-Ins/VST",
    Path("/Library/Audio/Plug-Ins/VST"),
]
_WIN_VST3_DIRS = [
    Path(r"C:\Program Files\Common Files\VST3"),
]
_WIN_VST2_DIRS = [
    Path(r"C:\Program Files\Common Files\VST2"),
    Path(r"C:\Program Files\VstPlugins"),
    Path(r"C:\Program Files\Steinberg\VstPlugins"),
    Path(r"C:\Program Files (x86)\Common Files\VST2"),
    Path(r"C:\Program Files (x86)\VstPlugins"),
]


@dataclass
class Plugin:
    name: str
    format: str  # "vst2" | "vst3"
    path: str
    bundle_id: str | None = None
    version: str | None = None
    size_bytes: int = 0
    mtime: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "format": self.format,
            "path": self.path,
            "bundle_id": self.bundle_id,
            "version": self.version,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
        }


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _read_mac_bundle_info(bundle: Path) -> tuple[str | None, str | None]:
    info_plist = bundle / "Contents" / "Info.plist"
    if not info_plist.exists():
        return None, None
    try:
        with info_plist.open("rb") as f:
            data = plistlib.load(f)
    except (plistlib.InvalidFileException, OSError):
        return None, None
    return data.get("CFBundleIdentifier"), data.get("CFBundleShortVersionString")


def _scan_mac_dir(directory: Path, fmt: str) -> list[Plugin]:
    if not directory.is_dir():
        return []
    plugins = []
    suffix = ".vst3" if fmt == "vst3" else ".vst"
    # Recursive: some vendors (e.g. MeldaProduction) install into vendor/category
    # subfolders rather than dropping the bundle directly in the VST/VST3 root.
    for entry in directory.rglob(f"*{suffix}"):
        if entry.suffix != suffix:
            continue
        bundle_id, version = _read_mac_bundle_info(entry)
        stat = entry.stat()
        plugins.append(
            Plugin(
                name=entry.stem,
                format=fmt,
                path=str(entry),
                bundle_id=bundle_id,
                version=version,
                size_bytes=_dir_size(entry),
                mtime=stat.st_mtime,
            )
        )
    return plugins


def _scan_windows_dir(directory: Path, fmt: str) -> list[Plugin]:
    if not directory.is_dir():
        return []
    plugins = []
    suffix = ".vst3" if fmt == "vst3" else ".dll"
    for entry in directory.rglob(f"*{suffix}"):
        if not entry.is_file():
            continue
        stat = entry.stat()
        plugins.append(
            Plugin(
                name=entry.stem,
                format=fmt,
                path=str(entry),
                bundle_id=None,
                version=None,
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    return plugins


def _custom_dirs(fmt: str) -> list[Path]:
    custom = load_config().get("custom_vst_paths", {})
    return [Path(p) for p in custom.get(fmt, [])]


def scan_installed_plugins(formats: list[str] | None = None) -> list[Plugin]:
    """Scan this machine's standard plugin directories, plus any extra
    folders the user added in Settings (~/.studs/config.json -> custom_vst_paths).

    `formats` restricts the scan to a subset of {"vst2", "vst3"};
    defaults to both.
    """
    formats = formats or ["vst2", "vst3"]
    system = platform.system()
    plugins: list[Plugin] = []

    if system == "Darwin":
        if "vst3" in formats:
            for d in _MAC_VST3_DIRS + _custom_dirs("vst3"):
                plugins.extend(_scan_mac_dir(d, "vst3"))
        if "vst2" in formats:
            for d in _MAC_VST2_DIRS + _custom_dirs("vst2"):
                plugins.extend(_scan_mac_dir(d, "vst2"))
    elif system == "Windows":
        if "vst3" in formats:
            for d in _WIN_VST3_DIRS + _custom_dirs("vst3"):
                plugins.extend(_scan_windows_dir(d, "vst3"))
        if "vst2" in formats:
            for d in _WIN_VST2_DIRS + _custom_dirs("vst2"):
                plugins.extend(_scan_windows_dir(d, "vst2"))
    else:
        raise RuntimeError(f"Unsupported platform: {system}")

    plugins.sort(key=lambda p: (p.format, p.name.lower()))
    return plugins
