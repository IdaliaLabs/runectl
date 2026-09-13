"""Adapter error mapping: a real SDK auth/API error becomes a clean typed
error (D5), never a raw traceback. This is the direct regression for the
2026-09-09 incident where an invalid Anthropic key crashed a whole bench run
with an unhandled `anthropic.AuthenticationError`.
"""

from __future__ import annotations

from typing import Any

import anthropic
import httpx
import pytest

from runectl.errors import ProviderError, UsageError
from runectl.providers.anthropic import AnthropicProvider


class _RaisingMessages:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def create(self, **kwargs: Any) -> Any:
        raise self._exc


class _FakeClient:
    def __init__(self, exc: Exception) -> None:
        self.messages = _RaisingMessages(exc)


def _response(code: int) -> httpx.Response:
    return httpx.Response(code, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


def _provider(exc: Exception) -> AnthropicProvider:
    return AnthropicProvider(model_id="claude-sonnet-5", api_key="bad", client=_FakeClient(exc))  # type: ignore[arg-type]


def test_a_401_becomes_a_usage_error_not_a_traceback() -> None:
    exc = anthropic.AuthenticationError("invalid x-api-key", response=_response(401), body=None)
    with pytest.raises(UsageError) as caught:
        _provider(exc).complete(system="s", messages=[], tools=(), max_tokens=10)
    assert caught.value.exit_code == 6
    assert "keys set anthropic" in str(caught.value)


def test_a_403_becomes_a_usage_error() -> None:
    exc = anthropic.PermissionDeniedError("no access", response=_response(403), body=None)
    with pytest.raises(UsageError):
        _provider(exc).complete(system="s", messages=[], tools=(), max_tokens=10)


def test_a_400_becomes_a_clean_provider_error() -> None:
    exc = anthropic.BadRequestError("malformed request", response=_response(400), body=None)
    with pytest.raises(ProviderError) as caught:
        _provider(exc).complete(system="s", messages=[], tools=(), max_tokens=10)
    assert caught.value.exit_code == 5


def test_a_billing_400_exits_6_not_5() -> None:
    """Found live on 2026-09-12, and the reason exit codes are a feature.

    A bench suite hit Anthropic's "credit balance is too low" 400 on all ten
    challenges. Every run exited 5 — "provider failure after retries" — which
    tells a caller the far side had a problem and a retry might help. Nothing
    would have helped; the account was out of money. An agent driving runectl
    (the point of D4's contract) would retry into a wall. Billing is a config
    problem, so it gets config's exit code.
    """
    exc = anthropic.BadRequestError(
        "Your credit balance is too low to access the Anthropic API.",
        response=_response(400),
        body=None,
    )
    with pytest.raises(UsageError) as caught:
        _provider(exc).complete(system="s", messages=[], tools=(), max_tokens=10)
    assert caught.value.exit_code == 6
    assert "will not clear on retry" in str(caught.value)


def test_a_non_billing_400_still_exits_5() -> None:
    """The split must not swallow ordinary malformed-request errors."""
    exc = anthropic.BadRequestError("messages: at least one message is required",
                                    response=_response(400), body=None)
    with pytest.raises(ProviderError) as caught:
        _provider(exc).complete(system="s", messages=[], tools=(), max_tokens=10)
    assert caught.value.exit_code == 5
