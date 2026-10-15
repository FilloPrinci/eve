"""Fail unless the release tag matches the package version (PEP 440 comparison).

Usage: python packaging/check_version.py <tag, e.g. v0.1.0-alpha.1>
"""

import sys

from packaging.version import Version

from easy_video_editor import __version__

tag = sys.argv[1].removeprefix("v")
if Version(tag) != Version(__version__):
    raise SystemExit(f"Tag {tag} does not match the package version {__version__}")
print(f"Version {__version__} matches tag {tag}")
