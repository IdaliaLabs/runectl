"""D9: categories are data; malformed/missing data fails loudly."""

from __future__ import annotations

import pytest

from runectl.categories.loader import CategoryNotFoundError, load


def test_loads_shipped_categories() -> None:
    misc = load("misc")
    web = load("web")
    assert misc.name == "misc"
    assert misc.step_limit == 60
    assert web.name == "web"
    assert web.step_limit == 80


def test_unknown_category_raises() -> None:
    with pytest.raises(CategoryNotFoundError):
        load("not-a-real-category")
