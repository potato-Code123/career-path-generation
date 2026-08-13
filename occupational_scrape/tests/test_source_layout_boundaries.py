"""``dev/`` is diagnostics only and must never be on the pipeline's import path.

Required by the phase specification: nothing under ``src/occupational_scrape/``
may import from ``dev``. The rule matters because ``dev/`` scripts produce output
for humans -- sweeps, coverage reports, conflict listings -- and are free to be
slow, interactive, or half-finished. A pipeline module that imported one would
make a reproducible artifact depend on an exploratory script.

The check is on the AST rather than on text, so a mention of ``dev`` in a comment
or docstring does not trip it and a disguised import does not slip past.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "occupational_scrape"
DEV = Path(__file__).resolve().parent.parent / "dev"

SOURCE_FILES = sorted(SRC.rglob("*.py"))
TEST_FILES = sorted((Path(__file__).resolve().parent).rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, cannot reach dev/
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_there_are_source_files_to_check() -> None:
    assert SOURCE_FILES, "found no modules under src/occupational_scrape"


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda path: path.name)
def test_src_module_does_not_import_dev(path: Path) -> None:
    assert "dev" not in _imported_roots(path), f"{path.name} imports from dev/"


@pytest.mark.parametrize("path", TEST_FILES, ids=lambda path: path.name)
def test_test_module_does_not_import_dev(path: Path) -> None:
    assert "dev" not in _imported_roots(path), f"{path.name} imports from dev/"


def test_dev_scripts_exist_and_are_not_a_package() -> None:
    # No __init__.py: dev/ is a directory of scripts, not an importable package.
    assert DEV.is_dir()
    assert not (DEV / "__init__.py").exists()
    assert list(DEV.glob("*.py")), "dev/ has no diagnostic scripts"


def test_no_src_module_is_named_after_a_junk_drawer() -> None:
    banned = {"utils.py", "helpers.py", "common.py", "main.py", "misc.py"}
    offenders = sorted(path.name for path in SOURCE_FILES if path.name in banned)
    assert offenders == []
