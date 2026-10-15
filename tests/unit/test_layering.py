"""Guard the layering rule: core, media, analysis and ai never import Qt (SPEC §7.1)."""

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "easy_video_editor"
QT_FREE_PACKAGES = ["core", "media", "analysis", "ai"]
FORBIDDEN_PREFIXES = ("PySide6", "PyQt5", "PyQt6", "shiboken6", "easy_video_editor.ui")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("package", QT_FREE_PACKAGES)
def test_package_does_not_import_qt(package: str) -> None:
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT)}: {module}"
        for path in sorted((PACKAGE_ROOT / package).rglob("*.py"))
        for module in _imported_modules(path)
        if module.startswith(FORBIDDEN_PREFIXES)
    ]
    assert not offenders, "Qt imported from a Qt-free layer:\n" + "\n".join(offenders)
