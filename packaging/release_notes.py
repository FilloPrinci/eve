"""Print the CHANGELOG.md section of a version (the release notes).

Usage: python packaging/release_notes.py <version, e.g. 0.1.0-alpha.1>
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def main() -> None:
    version = sys.argv[1].removeprefix("v")
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.S | re.M)
    if match is None:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    print(match.group(1).strip())


main()
