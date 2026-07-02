"""Command-line entry point: `python -m studs.vst_scan <command>`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compare import compare_manifests, format_report
from .manifest import load_manifest, write_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vst-scan")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan installed VSTs and write a manifest")
    scan_parser.add_argument("output", type=Path, help="Path to write the manifest JSON to")
    scan_parser.add_argument(
        "--formats",
        nargs="+",
        choices=["vst2", "vst3"],
        default=["vst2", "vst3"],
        help="Plugin formats to scan (default: both)",
    )

    compare_parser = subparsers.add_parser("compare", help="Compare two manifests")
    compare_parser.add_argument("manifest_a", type=Path)
    compare_parser.add_argument("manifest_b", type=Path)

    args = parser.parse_args(argv)

    if args.command == "scan":
        manifest = write_manifest(args.output, formats=args.formats)
        print(f"Wrote {len(manifest['plugins'])} plugins to {args.output}")
        return 0

    if args.command == "compare":
        manifest_a = load_manifest(args.manifest_a)
        manifest_b = load_manifest(args.manifest_b)
        result = compare_manifests(manifest_a, manifest_b)
        print(format_report(result, manifest_a["hostname"], manifest_b["hostname"]))
        return 1 if not result.is_identical() else 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
