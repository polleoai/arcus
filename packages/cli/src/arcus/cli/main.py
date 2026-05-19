"""arcus CLI entry point."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from arcus.provider_runtime import (
    EXIT_CODES,
    Factory,
    ProviderRegistry,
    register_defaults,
)


__version__ = "0.2.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arcus",
        description="Content extraction kernel: input -> transcript on disk",
    )
    parser.add_argument("input", nargs="?", help="URL or path to extract")
    parser.add_argument("--out", default="./out", help="Output directory")
    parser.add_argument("--force", action="store_true", help="Re-extract even if cached")
    parser.add_argument("--provider", help="Force a specific provider by kind")
    parser.add_argument("--print", action="store_true", help="Emit .md to stdout (no files)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose stderr")
    parser.add_argument("--json-log", action="store_true", help="NDJSON events to stderr")
    parser.add_argument("--keep-intermediates", action="store_true")
    parser.add_argument("--notebook-tag", help="Tag in NLM notebook name")
    parser.add_argument("--check", action="store_const", const="check", dest="command")
    parser.add_argument("--probe", action="store_const", const="probe", dest="command")
    parser.add_argument(
        "--list-providers", action="store_const", const="list-providers", dest="command"
    )
    parser.add_argument("--version", action="store_const", const="version", dest="command")
    parser.set_defaults(command="extract")
    return parser


def cmd_version() -> int:
    print(f"arcus {__version__}")
    return 0


def cmd_check() -> int:
    yt = shutil.which("yt-dlp") or "(not found)"
    nlm = shutil.which("nlm") or "(not found)"
    ffmpeg = shutil.which("ffmpeg") or "(not found)"
    print(f"arcus {__version__}")
    print(f"  yt-dlp:   {yt}")
    print(f"  nlm:      {nlm}")
    print(f"  ffmpeg:   {ffmpeg}  [optional]")
    if nlm != "(not found)":
        # Per Task 10 spec-gap note: real nlm CLI uses `nlm login --check`,
        # not the obsolete `nlm auth status`. Matches nlm_fallback.check_auth().
        auth = subprocess.run(["nlm", "login", "--check"], capture_output=True)
        status = "authenticated" if auth.returncode == 0 else "run `nlm login`"
        print(f"  nlm auth: {status}")
    return 0


def cmd_list_providers() -> int:
    registry = ProviderRegistry()
    register_defaults(registry)
    for p in registry.all():
        print(p.kind)
    return 0


def cmd_probe(raw_input: str) -> int:
    registry = ProviderRegistry()
    register_defaults(registry)
    factory = Factory(registry)
    match = factory.detect(raw_input)
    if match is None:
        print(f"No provider matches: {raw_input}", file=sys.stderr)
        return EXIT_CODES["EXTRACTORS_EXHAUSTED"]
    provider, detection = match
    print(f"Provider: {provider.kind}")
    print(f"Source ID: {detection.source_id}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    if not args.input:
        print("arcus: input is required", file=sys.stderr)
        return EXIT_CODES["INVALID_ARGS"]

    registry = ProviderRegistry()
    register_defaults(registry)
    factory = Factory(registry)
    return factory.run(
        args.input,
        out_dir=Path(args.out),
        force=args.force,
        json_log=args.json_log,
        keep_intermediates=args.keep_intermediates,
        notebook_tag=args.notebook_tag,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        return cmd_version()
    if args.command == "check":
        return cmd_check()
    if args.command == "list-providers":
        return cmd_list_providers()
    if args.command == "probe":
        if not args.input:
            print("arcus --probe requires an input", file=sys.stderr)
            return EXIT_CODES["INVALID_ARGS"]
        return cmd_probe(args.input)
    if args.command == "extract":
        return cmd_extract(args)

    print("arcus: unknown command", file=sys.stderr)
    return EXIT_CODES["INVALID_ARGS"]


if __name__ == "__main__":
    sys.exit(main())
