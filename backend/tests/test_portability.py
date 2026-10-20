"""Guards against code that only works on the newest Python.

Python 3.14 evaluates annotations lazily, so a name imported only under
`TYPE_CHECKING` works there but raises NameError on 3.12/3.13 unless the module
defers annotations with `from __future__ import annotations`. Development
happens on 3.14, so the regression would go unnoticed without this check.
"""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"
MODULES = sorted(APP_DIR.rglob("*.py"))


def _uses_type_checking_imports(tree):
    return any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
        for node in ast.walk(tree)
    )


def _defers_annotations(tree):
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def test_modules_were_found():
    assert MODULES, f"no modules found under {APP_DIR}"


@pytest.mark.parametrize("module", MODULES, ids=lambda path: path.relative_to(APP_DIR).as_posix())
def test_type_checking_imports_require_deferred_annotations(module):
    tree = ast.parse(module.read_text(encoding="utf-8"))
    if _uses_type_checking_imports(tree):
        assert _defers_annotations(tree), (
            f"{module.name} imports names under TYPE_CHECKING; add "
            "`from __future__ import annotations` or it breaks on Python < 3.14"
        )
