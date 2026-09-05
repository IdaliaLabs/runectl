"""Load category data from TOML at runtime (D9, plan §8.2 — the loader is HANDOFF).

Adding a category is a data file, never a code change. The skeleton ships a
subset of the eight standard categories to exercise this loader; M7 fills in
the rest, with equal depth from day one (D14 — no earner-first order).
"""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

from runectl.categories.schema import Category

_CATEGORY_DIR = Path(__file__).resolve().parent

# The full standard set (`REBUILD_NOTES.md` §6). M7 fills in whichever aren't
# yet shipped as data; `load_all()` below intentionally does NOT assert this
# full set is present until then (plan §8.2 skeleton note).
STANDARD_CATEGORIES = ("pwn", "web", "crypto", "forensics", "rev", "misc", "osint", "network")


class CategoryNotFoundError(Exception):
    pass


class CategoryLoadError(Exception):
    pass


def available_categories() -> list[str]:
    return sorted(path.stem for path in _CATEGORY_DIR.glob("*.toml"))


@cache
def load(category: str) -> Category:
    path = _CATEGORY_DIR / f"{category}.toml"
    if not path.exists():
        raise CategoryNotFoundError(
            f"no category data for {category!r} at {path} "
            f"(shipped: {', '.join(available_categories()) or 'none'})"
        )
    try:
        raw = tomllib.loads(path.read_text())
        return Category.model_validate({**raw, "name": category})
    except CategoryNotFoundError:
        raise
    except Exception as exc:
        raise CategoryLoadError(f"malformed category data for {category!r}: {exc}") from exc


def load_all() -> dict[str, Category]:
    """Load every category currently shipped as data.

    M7 changes this to assert all of :data:`STANDARD_CATEGORIES` are present;
    the skeleton ships 1-2 to exercise the loader (plan §8.2).
    """
    return {name: load(name) for name in available_categories()}
