"""D13: only cli/ renders. Core code emits events and never prints."""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "runectl"


def _uses_print(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
        for node in ast.walk(tree)
    )


def test_only_cli_package_prints() -> None:
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in SRC_ROOT.rglob("*.py")
        if "cli" not in path.relative_to(SRC_ROOT).parts and _uses_print(path)
    ]
    assert not offenders, f"non-cli modules calling print() directly: {offenders}"
