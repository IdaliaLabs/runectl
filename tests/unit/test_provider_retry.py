"""D5/M3 gate: a 429/5xx/timeout storm degrades (retry, then a clean
ProviderError) rather than aborting the process; a transient error that
clears within the retry budget succeeds and is costed exactly once."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from runectl.errors import ProviderError
from runectl.providers.base import (
    Completion,
    Message,
    ToolCallRequest,
    TransientProviderError,
    Usage,
    complete_with_retry,
)
from runectl.providers.cost import CostLedger
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
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSchema], max_tokens: int
    ) -> Completion:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientProviderError("simulated 429")
        return _OK


class _AlwaysFlakyProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, *, system: str, messages: Sequence[Message], tools: Sequence[ToolSchema], max_tokens: int
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
