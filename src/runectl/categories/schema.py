"""Category data schema every TOML validates against (D9, plan §8.1).

The loader (§8.2, HANDOFF) validates every category file against this shape.
``tactic_families``/``signal_low``/``signal_high``/``budgets`` are real, typed
fields from day one, but in the M4 skeleton they're inert data — the progress
machinery that actually consumes them (D8) is a no-op stub until M5. Malformed
category data fails loudly at load, never silently.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CategoryBudgets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    per_hypothesis: int = 2
    per_family: int = 4


class Category(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    brief: str
    playbook: str
    required_tools: list[str] = Field(default_factory=list)
    tactic_families: dict[str, str] = Field(default_factory=dict)
    signal_low: list[str] = Field(default_factory=list)
    signal_high: list[str] = Field(default_factory=list)
    budgets: CategoryBudgets = Field(default_factory=CategoryBudgets)
    step_limit: int
    network: Literal["none", "bridge"] = "bridge"
