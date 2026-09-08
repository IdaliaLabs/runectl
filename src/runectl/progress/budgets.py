"""Budgets (D8 mechanism 4): stop paying for an idea that isn't working.

Two counters, both counting *no-progress* steps rather than steps:

- **per-hypothesis** — the same specific approach, keyed on the normalised
  command. Re-running essentially the same thing after it taught you nothing.
- **per-family** — the whole tactic class (`dirfuzz`, `decode`, ...). Four
  different fuzzers that each found nothing is still one failed idea.

Exceeding either blocks the command before it runs, which is what makes this
a budget rather than a warning. Thresholds come from the category TOML, never
from this file (D8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_VOLATILE_ARG = re.compile(r"['\"][^'\"]{16,}['\"]|\b\d{4,}\b")
_WS = re.compile(r"\s+")


def hypothesis_key(command: str) -> str:
    """Collapse a command to the idea behind it.

    Long quoted literals and big numbers are the parts that change when an
    agent retries "the same thing but with a different wordlist/offset", so
    they are stripped: those retries share a hypothesis.
    """
    return _WS.sub(" ", _VOLATILE_ARG.sub("<V>", command)).strip().lower()


@dataclass(frozen=True)
class BudgetVerdict:
    blocked: bool
    family: str
    reason: str = ""


@dataclass
class BudgetLedger:
    """No-progress counters per family and per hypothesis."""

    per_hypothesis: int
    per_family: int

    _family_misses: dict[str, int] = field(default_factory=dict)
    _hypothesis_misses: dict[str, int] = field(default_factory=dict)

    def check(self, command: str, family: str) -> BudgetVerdict:
        """Called *before* execution. Blocking here is the whole point."""
        key = hypothesis_key(command)
        if self._hypothesis_misses.get(key, 0) >= self.per_hypothesis:
            return BudgetVerdict(
                blocked=True,
                family=family,
                reason=(
                    f"this approach has produced no new signal {self.per_hypothesis} "
                    f"time(s) already — it is blocked. Change the hypothesis, not the "
                    f"arguments."
                ),
            )
        if self._family_misses.get(family, 0) >= self.per_family:
            return BudgetVerdict(
                blocked=True,
                family=family,
                reason=(
                    f"the '{family}' tactic family has produced no new signal "
                    f"{self.per_family} time(s) — it is blocked. Pick a different "
                    f"class of idea."
                ),
            )
        return BudgetVerdict(blocked=False, family=family)

    def record(self, command: str, family: str, *, progressed: bool) -> None:
        """Called *after* execution, with whether the step actually taught us anything."""
        key = hypothesis_key(command)
        if progressed:
            # Success clears the family: the idea is working after all.
            self._family_misses.pop(family, None)
            self._hypothesis_misses.pop(key, None)
            return
        self._family_misses[family] = self._family_misses.get(family, 0) + 1
        self._hypothesis_misses[key] = self._hypothesis_misses.get(key, 0) + 1

    @property
    def blocked_families(self) -> list[str]:
        return [f for f, n in self._family_misses.items() if n >= self.per_family]
