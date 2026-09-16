"""Category data schema every TOML validates against (D9).

The loader validates every category file against this shape; the progress
machinery (D8) consumes ``tactic_families``/``signal_low``/``signal_high``/
``budgets`` on every step. Malformed category data fails loudly at load — a bad
regex or a zero budget is a load error, never a silently-dropped pattern or a
category that blocks every command from step one.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CategoryBudgets(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # No-progress steps allowed before a specific approach / a whole tactic
    # family is blocked (D8 mechanism 4).
    per_hypothesis: int = Field(default=2, ge=1)
    per_family: int = Field(default=4, ge=1)

    # D8: "a forced strategy shift after N no-progress steps or M consecutive
    # errors". Thresholds live here in data, never in code.
    no_progress_shift: int = Field(default=3, ge=1)
    consecutive_error_shift: int = Field(default=3, ge=1)


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
    step_limit: int = Field(ge=1)
    network: Literal["none", "bridge"] = "bridge"

    @field_validator("tactic_families")
    @classmethod
    def _families_compile(cls, value: dict[str, str]) -> dict[str, str]:
        for name, pattern in value.items():
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"tactic_families[{name!r}] is not a valid regex: {exc}") from exc
        return value

    @field_validator("signal_low", "signal_high")
    @classmethod
    def _signals_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"signal pattern {pattern!r} is not a valid regex: {exc}") from exc
        return value
