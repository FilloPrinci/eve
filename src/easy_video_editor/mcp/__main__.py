"""``python -m easy_video_editor.mcp --project DIR [--phase PHASE]``."""

from easy_video_editor.cli.main import main

if __name__ == "__main__":
    import sys

    raise SystemExit(main(["mcp", *sys.argv[1:]]))
