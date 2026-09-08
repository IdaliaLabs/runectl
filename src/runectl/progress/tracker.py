"""The progress tracker (D8): the four mechanisms, wired together.

A plain collaborator in the D6 sense — the loop calls it and applies what it
returns; it never touches ``RunState``. That is what lets the whole of D8 be
tested with no model, no sandbox, and no spend.

Order of operations in the loop, which matters:

    verdict = tracker.check_budget(cmd)     # before execution — may block
    ...execute...
    assessment = tracker.record(cmd, out)   # after execution — scores it
    nudge = tracker.due_shift()             # may force a change of approach
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from runectl.categories.schema import Category
from runectl.progress.budgets import BudgetLedger, BudgetVerdict
from runectl.progress.families import classify
from runectl.progress.fingerprint import fingerprint
from runectl.progress.signal import SignalBand, score


@dataclass(frozen=True)
class ProgressAssessment:
    family: str
    fingerprint: str
    delta: float
    signal: SignalBand
    repeated: bool

    @property
    def progressed(self) -> bool:
        """Progress means new information, not merely a zero exit code."""
        return not self.repeated and self.signal != "low" and self.delta > 0


@dataclass
class ProgressTracker:
    category: Category

    _ledger: BudgetLedger = field(init=False)
    _seen: dict[str, int] = field(default_factory=dict)
    _no_progress_run: int = 0
    _consecutive_errors: int = 0
    _recent: list[str] = field(default_factory=list)
    _step: int = 0

    def __post_init__(self) -> None:
        self._ledger = BudgetLedger(
            per_hypothesis=self.category.budgets.per_hypothesis,
            per_family=self.category.budgets.per_family,
        )

    def check_budget(self, command: str) -> BudgetVerdict:
        return self._ledger.check(command, classify(command, overrides=self.category.tactic_families))

    def record(self, command: str, output: str, *, ok: bool, step: int) -> ProgressAssessment:
        self._step = step
        family = classify(command, overrides=self.category.tactic_families)
        digest = fingerprint(output)
        repeated = digest in self._seen
        delta, band = score(
            output,
            ok=ok,
            repeated=repeated,
            low_patterns=tuple(self.category.signal_low),
            high_patterns=tuple(self.category.signal_high),
        )
        assessment = ProgressAssessment(
            family=family, fingerprint=digest, delta=delta, signal=band, repeated=repeated
        )

        self._seen.setdefault(digest, step)
        self._ledger.record(command, family, progressed=assessment.progressed)
        if assessment.progressed:
            self._no_progress_run = 0
        else:
            self._no_progress_run += 1
        self._consecutive_errors = 0 if ok else self._consecutive_errors + 1

        summary = f"step {step} [{family}] {'repeat' if repeated else band}: {output.strip()[:90]}"
        self._recent.append(summary)
        del self._recent[:-6]
        return assessment

    def note_blocked(self, family: str, reason: str, *, step: int) -> None:
        """Record that a budget refused a command before it ran.

        A blocked step never reaches :meth:`record`, so without this the
        no-progress counter freezes the moment budgets start biting — and the
        forced strategy shift could never fire in exactly the situation it
        exists for: an agent hammering an idea that is already exhausted.
        """
        self._step = step
        self._no_progress_run += 1
        self._recent.append(f"step {step} [{family}] BLOCKED: {reason}")
        del self._recent[:-6]

    def due_shift(self) -> str | None:
        """A forced change of approach, with the evidence that motivated it (D8).

        Returns the text to inject, or None. Calling it resets the counters —
        the loop gets one shift per threshold crossing, not one per step
        thereafter.
        """
        budgets = self.category.budgets
        reason: str | None = None
        if self._consecutive_errors >= budgets.consecutive_error_shift:
            reason = f"{self._consecutive_errors} commands in a row failed"
        elif self._no_progress_run >= budgets.no_progress_shift:
            reason = f"{self._no_progress_run} steps in a row produced no new signal"
        if reason is None:
            return None

        self._no_progress_run = 0
        self._consecutive_errors = 0
        evidence = "\n".join(f"  - {line}" for line in self._recent) or "  - (nothing yet)"
        blocked = self._ledger.blocked_families
        blocked_note = (
            f"\nThese tactic families are exhausted and blocked: {', '.join(blocked)}."
            if blocked
            else ""
        )
        return (
            f"STOP. {reason}. Pick a different hypothesis class — not a wider or "
            f"differently-flagged version of the same idea.{blocked_note}\n"
            f"What you have actually observed so far:\n{evidence}"
        )

    @property
    def shift_reason_kind(self) -> Literal["errors", "no-progress"]:
        return "errors" if self._consecutive_errors else "no-progress"
