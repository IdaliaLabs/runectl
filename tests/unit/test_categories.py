"""D9: categories are data; malformed/missing data fails loudly."""

from __future__ import annotations

from pathlib import Path

import pytest

from runectl.categories.loader import CategoryNotFoundError, load, load_all


def test_loads_shipped_categories() -> None:
    misc = load("misc")
    web = load("web")
    crypto = load("crypto")
    assert misc.name == "misc"
    assert misc.step_limit == 60
    assert web.name == "web"
    assert web.step_limit == 80
    assert crypto.name == "crypto"
    assert crypto.step_limit == 80


def test_unknown_category_raises() -> None:
    with pytest.raises(CategoryNotFoundError):
        load("not-a-real-category")


def test_required_tools_are_installed_in_the_arena_image() -> None:
    """A category may not claim a tool the arena image never installs.

    Caught `zbarimg` missing from the Dockerfile while misc.toml required it.
    This is a text check, not a real container probe — the arena image cannot be
    built in this suite (no daemon) — so it catches omissions, not breakage.
    """
    dockerfile = (Path(__file__).resolve().parents[2] / "arena" / "Dockerfile").read_text()
    # shipped by base packages under a different name than the binary
    provided_by_base = {"strings": "binutils", "xxd": "xxd"}
    for name, category in load_all().items():
        for tool in category.required_tools:
            package = provided_by_base.get(tool, tool)
            assert package in dockerfile, f"{name}.toml requires {tool!r}, absent from arena/Dockerfile"
