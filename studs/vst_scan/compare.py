"""Compares two VST manifests and reports differences."""
from __future__ import annotations

from dataclasses import dataclass


def _key(plugin: dict) -> tuple[str, str]:
    # Prefer bundle_id (stable across renames) when available, otherwise
    # fall back to a case-insensitive name match.
    identity = plugin.get("bundle_id") or plugin["name"].lower()
    return (plugin["format"], identity)


@dataclass
class CompareResult:
    only_in_a: list[dict]
    only_in_b: list[dict]
    version_mismatches: list[tuple[dict, dict]]
    matched: list[dict]

    def is_identical(self) -> bool:
        return not (self.only_in_a or self.only_in_b or self.version_mismatches)


def compare_manifests(manifest_a: dict, manifest_b: dict) -> CompareResult:
    plugins_a = {_key(p): p for p in manifest_a["plugins"]}
    plugins_b = {_key(p): p for p in manifest_b["plugins"]}

    only_in_a = [p for k, p in plugins_a.items() if k not in plugins_b]
    only_in_b = [p for k, p in plugins_b.items() if k not in plugins_a]

    version_mismatches = []
    matched = []
    for key, plugin_a in plugins_a.items():
        if key not in plugins_b:
            continue
        plugin_b = plugins_b[key]
        if plugin_a.get("version") and plugin_b.get("version") and plugin_a["version"] != plugin_b["version"]:
            version_mismatches.append((plugin_a, plugin_b))
        else:
            matched.append(plugin_a)

    only_in_a.sort(key=lambda p: (p["format"], p["name"].lower()))
    only_in_b.sort(key=lambda p: (p["format"], p["name"].lower()))
    version_mismatches.sort(key=lambda pair: (pair[0]["format"], pair[0]["name"].lower()))

    return CompareResult(only_in_a, only_in_b, version_mismatches, matched)


def format_report(result: CompareResult, name_a: str, name_b: str) -> str:
    lines = []
    if result.is_identical():
        lines.append(f"{name_a} and {name_b} have matching VST installs.")
        return "\n".join(lines)

    if result.only_in_a:
        lines.append(f"Only on {name_a} ({len(result.only_in_a)}):")
        for p in result.only_in_a:
            lines.append(f"  - [{p['format']}] {p['name']}")

    if result.only_in_b:
        lines.append(f"Only on {name_b} ({len(result.only_in_b)}):")
        for p in result.only_in_b:
            lines.append(f"  - [{p['format']}] {p['name']}")

    if result.version_mismatches:
        lines.append(f"Version mismatches ({len(result.version_mismatches)}):")
        for a, b in result.version_mismatches:
            lines.append(f"  - [{a['format']}] {a['name']}: {name_a}={a['version']} {name_b}={b['version']}")

    return "\n".join(lines)
