"""The disconfirmation pass: verdict parsing, cost, and failing closed."""

from __future__ import annotations

import pytest

from runectl.errors import ProviderError
from runectl.flags.review import (
    ReviewRequest,
    ReviewVerdict,
    make_reviewer,
    parse_verdict,
    replay_reviewer,
)
from runectl.providers.base import Completion, Usage
from runectl.providers.cost import CostLedger
from runectl.providers.registry import resolve as resolve_model

REQUEST = ReviewRequest(
    flag="csictf{a_flag}",
    how_found="decoded the hex",
    description="a crypto challenge",
    source_command="python3 solve.py",
    source_output="csictf{a_flag}",
)


class _Provider:
    def __init__(self, text: str = "", error: bool = False) -> None:
        self.text = text
        self.error = error
        self.calls: list[str] = []

    def complete(self, *, system: str, messages, tools, max_tokens: int) -> Completion:  # type: ignore[no-untyped-def]
        if self.error:
            raise ProviderError("upstream is down")
        self.calls.append(system)
        return Completion(
            text=self.text, tool_calls=(),
            usage=Usage(input_tokens=300, output_tokens=20), stop_reason="end_turn",
        )


@pytest.mark.parametrize(
    "text,sound",
    [
        ("VERDICT: SOUND\nWHY: it was derived from the ciphertext.", True),
        ("VERDICT: DOUBTFUL\nWHY: right integer, wrong encoding.", False),
        ("verdict: sound\nwhy: nothing wrong with it.", True),
    ],
)
def test_it_reads_the_two_line_verdict(text: str, sound: bool) -> None:
    verdict = parse_verdict(text)
    assert verdict.sound is sound
    assert verdict.reason


@pytest.mark.parametrize("text", ["", "I think it's probably fine?", "SOUND-ISH"])
def test_an_unreadable_answer_is_a_doubt_not_a_pass(text: str) -> None:
    """A verdict that can't be parsed must never finalize a flag."""
    assert parse_verdict(text).sound is False


def test_hedging_toward_doubtful_wins() -> None:
    """'SOUND, though DOUBTFUL about the format' must not finalize."""
    assert parse_verdict("VERDICT: SOUND\nWHY: though I am DOUBTFUL about the format.").sound is False


def test_a_failed_review_call_holds_the_candidate() -> None:
    reviewer = make_reviewer(_Provider(error=True), resolve_model("claude-haiku-4-5-20251001"), CostLedger())
    verdict = reviewer(REQUEST)
    assert verdict.sound is False
    assert "review call failed" in verdict.reason


def test_the_review_is_costed_on_the_shared_ledger() -> None:
    """D5: no untracked tokens, including the cheap ones."""
    ledger = CostLedger()
    model = resolve_model("claude-haiku-4-5-20251001")
    reviewer = make_reviewer(_Provider("VERDICT: SOUND\nWHY: fine."), model, ledger)
    reviewer(REQUEST)
    assert ledger.total_usd > 0
    assert ledger.entries[0].model_id == model.id


def test_the_prompt_carries_the_evidence_and_stays_small() -> None:
    request = ReviewRequest(
        flag="csictf{x}", how_found="decoded", description="d" * 5000,
        source_command="cat /ctf/f", source_output="o" * 9000,
    )
    prompt = request.as_prompt()
    assert "csictf{x}" in prompt
    assert "cat /ctf/f" in prompt
    # Truncated hard: a review that re-read everything would cost more than the
    # steps it saves.
    assert len(prompt) < 3200


def test_a_replay_returns_recorded_verdicts_and_then_doubts() -> None:
    reviewer = replay_reviewer([ReviewVerdict(True, "recorded")])
    assert reviewer(REQUEST).sound is True
    # Past the end of the record is a doubt, never a pass.
    assert reviewer(REQUEST).sound is False
