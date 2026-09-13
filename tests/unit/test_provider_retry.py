"""D5/M3 gate: a 429/5xx/timeout storm degrades (retry, then a clean
ProviderError) rather than aborting the process; a transient error that
clears within the retry budget succeeds and is costed exactly once."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from runectl.errors import ProviderError, UsageError
from runectl.providers.base import (
    Completion,
    Message,
    ToolCallRequest,
    TransientProviderError,
    Usage,
    api_error,
    auth_error,
    complete_with_retry,
)
from runectl.providers.cost import CostLedger
from runectl.providers.registry import ThinkingLevel
from runectl.providers.registry import resolve as resolve_model
from runectl.tools.schema import ToolSchema

_MODEL = resolve_model("claude-sonnet-5")
_OK = Completion(
    text="ok", tool_calls=(), usage=Usage(input_tokens=10, output_tokens=5), stop_reason="end_turn"
)


class _FlakyProvider:
    """Raises TransientProviderError `fail_times` times, then succeeds."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSchema], max_tokens: int,
        thinking: ThinkingLevel = "off",
    ) -> Completion:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientProviderError("simulated 429")
        return _OK


class _AlwaysFlakyProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSchema], max_tokens: int,
        thinking: ThinkingLevel = "off",
    ) -> Completion:
        self.calls += 1
        raise TransientProviderError("simulated 429 storm")


def test_transient_error_within_budget_still_succeeds() -> None:
    provider = _FlakyProvider(fail_times=2)
    ledger = CostLedger()
    result = complete_with_retry(
        provider, _MODEL, ledger,
        system="sys", messages=[], tools=(), max_tokens=10,
        attempts=4, sleep=lambda _: None,
    )
    assert result == _OK
    assert provider.calls == 3
    assert len(ledger.entries) == 1  # only the eventual success is costed


def test_persistent_429_storm_degrades_to_provider_error_not_a_crash() -> None:
    provider = _AlwaysFlakyProvider()
    ledger = CostLedger()
    with pytest.raises(ProviderError):
        complete_with_retry(
            provider, _MODEL, ledger,
            system="sys", messages=[], tools=(), max_tokens=10,
            attempts=4, sleep=lambda _: None,
        )
    assert provider.calls == 4
    assert len(ledger.entries) == 0  # no phantom cost recorded for failed calls


def test_tool_call_request_round_trips_through_scripted_provider() -> None:
    # sanity: the fixture types used across provider tests are constructible
    call = ToolCallRequest(id="c1", name="run_command", arguments={"command": "ls"})
    assert call.name == "run_command"


class _AuthFailingProvider:
    """An adapter that classified a 401 as a UsageError (an invalid key)."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSchema], max_tokens: int,
        thinking: ThinkingLevel = "off",
    ) -> Completion:
        self.calls += 1
        raise auth_error("Anthropic", RuntimeError("401 invalid x-api-key"))


def test_auth_error_is_not_retried_and_surfaces_as_usage_error() -> None:
    """A bad key must fail once, cleanly (exit 6), not be hammered four times."""
    provider = _AuthFailingProvider()
    ledger = CostLedger()
    with pytest.raises(UsageError) as caught:
        complete_with_retry(
            provider, _MODEL, ledger,
            system="sys", messages=[], tools=(), max_tokens=10,
            attempts=4, sleep=lambda _: None,
        )
    assert provider.calls == 1  # no retry on a permanent auth failure
    assert not ledger.entries
    assert caught.value.exit_code == 6
    assert "keys set anthropic" in str(caught.value)


def test_error_factories_carry_actionable_text_and_the_right_exit_codes() -> None:
    ue = auth_error("OpenAI", RuntimeError("401"))
    assert isinstance(ue, UsageError) and ue.exit_code == 6
    assert "OPENAI_API_KEY" in str(ue) and "keys set openai" in str(ue)
    pe = api_error("Google", RuntimeError("400 bad request"))
    assert isinstance(pe, ProviderError) and pe.exit_code == 5


def test_a_provider_stated_retry_delay_is_honored() -> None:
    """Found live on 2026-09-13 against Gemini's free tier.

    Exponential backoff over four attempts waits roughly seven seconds in total.
    A per-minute quota that clears in thirty-five is therefore indistinguishable
    from a hard failure — the run died after four fast retries against a limit
    that was about to lift. Free-tier users are exactly who the README points at
    cheap models, so this was their default experience.
    """
    from runectl.providers.base import MAX_RETRY_AFTER_S

    class _RateLimited:
        def __init__(self) -> None:
            self.calls = 0

        def complete(
            self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSchema],
            max_tokens: int, thinking: ThinkingLevel = "off",
        ) -> Completion:
            self.calls += 1
            if self.calls == 1:
                raise TransientProviderError("429 quota exceeded", retry_after=35.0)
            return _OK

    slept: list[float] = []
    result = complete_with_retry(
        _RateLimited(), _MODEL, CostLedger(),
        system="s", messages=[], tools=[], max_tokens=10, sleep=slept.append,
    )
    assert result.text == "ok"
    assert slept[0] >= 35.0, f"waited {slept} — less than the provider asked for"
    assert slept[0] <= MAX_RETRY_AFTER_S


def test_an_absurd_retry_delay_is_capped() -> None:
    """D19 bounds dollars; nothing bounds wall-clock, so a hostile or buggy
    delay must not park a run for a day."""
    from runectl.providers.base import MAX_RETRY_AFTER_S

    class _Hostile:
        def __init__(self) -> None:
            self.calls = 0

        def complete(
            self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSchema],
            max_tokens: int, thinking: ThinkingLevel = "off",
        ) -> Completion:
            self.calls += 1
            if self.calls == 1:
                raise TransientProviderError("429", retry_after=86_400.0)
            return _OK

    slept: list[float] = []
    complete_with_retry(
        _Hostile(), _MODEL, CostLedger(),
        system="s", messages=[], tools=[], max_tokens=10, sleep=slept.append,
    )
    assert slept[0] == MAX_RETRY_AFTER_S
