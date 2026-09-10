"""Load category data from TOML at runtime (D9, plan §8.2 — the loader is HANDOFF).

Adding a category is a data file, never a code change. As of M7 (2026-09-08) all
eight standard categories ship as data, at equal depth from day one (D14 — no
earner-first order); `load_all()` asserts the full set is present.
"""

from __future__ import annotations

import tomllib
from functools import cache
from pathlib import Path

from runectl.categories.schema import Category

_CATEGORY_DIR = Path(__file__).resolve().parent

# The full standard set (`docs/ARCHITECTURE.md` D14). All eight ship as data as of M7;
# `load_all()` asserts this set is present.
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
    """Load every shipped category, asserting the full standard set is present (D14).

    A missing standard category is a packaging error, not a silent partial load:
    all eight ship together as of M7, so a build that dropped one should fail
    loudly here rather than quietly offer seven.
    """
    loaded = {name: load(name) for name in available_categories()}
    missing = [name for name in STANDARD_CATEGORIES if name not in loaded]
    if missing:
        raise CategoryLoadError(
            f"missing standard category data: {', '.join(missing)} "
            f"(shipped: {', '.join(sorted(loaded)) or 'none'})"
        )
    return loaded
