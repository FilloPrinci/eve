"""Copy the licence files of every installed Python distribution into one folder, with an index.

Usage: python packaging/collect_licenses.py <output folder>
"""

import sys
from importlib import metadata
from pathlib import Path

LICENSE_WORDS = ("license", "licence", "copying", "notice", "authors")


def main() -> None:
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    index = ["Python packages bundled with EVE and their licences", ""]
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name, version = dist.metadata["Name"], dist.version
        licence = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        if not licence or len(licence) > 80:
            classifiers = dist.metadata.get_all("Classifier") or []
            licence = "; ".join(c.split(" :: ")[-1] for c in classifiers if c.startswith("License"))
        index.append(f"{name} {version}: {licence.strip() or 'see files'}")
        copied = 0
        for file in dist.files or []:
            if any(word in file.name.lower() for word in LICENSE_WORDS):
                source = Path(str(dist.locate_file(file)))
                if source.is_file():
                    target = output / name / file.name
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(source.read_bytes())
                    copied += 1
        if not copied:
            index[-1] += " (no licence file shipped by the package)"
    (output / "INDEX.txt").write_text("\n".join(index) + "\n", encoding="utf-8")


main()
