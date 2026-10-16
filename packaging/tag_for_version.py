"""Print the release tag for the package version: 0.1.0a3 -> v0.1.0-alpha.3, 1.2.0 -> v1.2.0.

Prints nothing for development versions (e.g. 0.2.0.dev1), which are never released.
Usage: python3 packaging/tag_for_version.py
"""

import re
from pathlib import Path

INIT = Path(__file__).resolve().parents[1] / "src" / "easy_video_editor" / "__init__.py"
STAGES = {"a": "alpha", "b": "beta", "rc": "rc"}


def main() -> None:
    match = re.search(r'^__version__ = "([^"]+)"', INIT.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise SystemExit("No __version__ found")
    version = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", match.group(1))
    if version is None:
        return  # development or unusual version: not a release
    base, stage, number = version.groups()
    print(f"v{base}-{STAGES[stage]}.{number}" if stage else f"v{base}")


main()
