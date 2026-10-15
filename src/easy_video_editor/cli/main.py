"""Entry point of the `eve` command."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from easy_video_editor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eve",
        description="EVE — Easy Video Editor (command-line interface).",
    )
    parser.add_argument("--version", action="version", version=f"eve {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
