"""The disconfirmation pass (D15 mechanism 2) — one model call, framed to doubt.

Replaces corroboration in `gated`'s auto-finalize bar as of 2026-09-08 (D11
amendment), on the evidence of the first live bench: corroboration held four
correct flags and finalized a wrong one, because a clean solve produces its
answer once and a stubborn agent produces it as many times as the checker asks.

Two things make this cheap enough to run on every candidate:

- It goes to the **utility model** (D5 — the cheapest model of the same
  provider), not the solving model. About $0.002 a call.
- It is sent one candidate and its evidence, not the run's history. A review
  that re-read the whole conversation would cost more than the steps it saves.

The framing is the mechanism. Asked "is this right?", a model agrees; asked
"find the reason this is wrong, and say SOUND only if you cannot", it has to
actually look. It is still a model judging a model, which is why it can only
*withhold* a solve — a doubtful verdict holds the candidate for a human, and a
sound one still has to clear provenance, decoys, re-derivation and the flag
format before anything is finalized.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from runectl.errors import ProviderError
from runectl.providers.base import Message, Provider, complete_with_retry
from runectl.providers.cost import CostLedger
from runectl.providers.registry import ModelInfo

# Enough evidence to judge, little enough to stay cheap.
_DESCRIPTION_CHARS = 1200
_OUTPUT_CHARS = 1500

REVIEW_SYSTEM_PROMPT = """\
You are checking whether a CTF flag another agent submitted is actually correct.

Your job is to find a reason it is WRONG. Look for: an answer that solves part of
the challenge but not the last step, a value that is the right result in the
wrong encoding or format, a string lifted from the challenge description or a
tool's own banner, or a plausible-looking guess with no derivation behind it.

Reply with exactly two lines:
VERDICT: SOUND or VERDICT: DOUBTFUL
WHY: one sentence.

Say SOUND only if you looked for a specific reason it is wrong and could not
find one. If you are unsure, say DOUBTFUL — a held flag costs one human glance,
a wrong flag costs the challenge."""


@dataclass(frozen=True)
class ReviewVerdict:
    sound: bool
    reason: str

    @property
    def detail(self) -> str:
        return self.reason or ("no reason to doubt it" if self.sound else "doubtful")


Reviewer = Callable[["ReviewRequest"], ReviewVerdict]


@dataclass(frozen=True)
class ReviewRequest:
    flag: str
    how_found: str
    description: str
    source_command: str
    source_output: str

    def as_prompt(self) -> str:
        return (
            f"CHALLENGE:\n{self.description[:_DESCRIPTION_CHARS]}\n\n"
            f"SUBMITTED FLAG: {self.flag}\n"
            f"THE AGENT SAYS IT FOUND IT BY: {self.how_found}\n\n"
            f"THE COMMAND IT CITES:\n{self.source_command}\n\n"
            f"THAT COMMAND'S OUTPUT:\n{self.source_output[:_OUTPUT_CHARS]}"
        )


def parse_verdict(text: str) -> ReviewVerdict:
    """Read the reviewer's two lines. Anything unparseable is a doubt, not a pass."""
    upper = text.upper()
    reason = ""
    for line in text.splitlines():
        if line.strip().upper().startswith("WHY:"):
            reason = line.split(":", 1)[1].strip()
            break
    if "VERDICT: DOUBTFUL" in upper or "DOUBTFUL" in upper:
        return ReviewVerdict(False, reason or "the reviewer was not convinced")
    if "VERDICT: SOUND" in upper:
        return ReviewVerdict(True, reason)
    return ReviewVerdict(False, "the reviewer's answer could not be read as a verdict")


def replay_reviewer(verdicts: Sequence[ReviewVerdict]) -> Reviewer:
    """Return recorded verdicts in order — a replay contacts no API (D5).

    Same seam as the runner's `triage_override`: the review is a model call, so
    replaying it means replaying what it *said*, not asking again. Running past
    the end of the record is a doubt, never a pass.
    """
    remaining = list(verdicts)

    def review(request: ReviewRequest) -> ReviewVerdict:
        if not remaining:
            return ReviewVerdict(False, "no recorded review for this candidate")
        return remaining.pop(0)

    return review


def make_reviewer(provider: Provider, model: ModelInfo, ledger: CostLedger) -> Reviewer:
    """Wrap a provider+model+ledger as a plain reviewer (D5: costed like any call)."""

    def review(request: ReviewRequest) -> ReviewVerdict:
        try:
            completion = complete_with_retry(
                provider,
                model,
                ledger,
                system=REVIEW_SYSTEM_PROMPT,
                messages=[Message(role="user", content=request.as_prompt())],
                tools=(),
                max_tokens=200,
            )
        except ProviderError as exc:
            # Fail closed: an unreachable reviewer holds the candidate rather
            # than waving it through.
            return ReviewVerdict(False, f"the review call failed: {exc}")
        return parse_verdict(completion.text)

    return review
