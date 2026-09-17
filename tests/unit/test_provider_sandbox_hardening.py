"""Provider- and sandbox-hardening behaviors: billing-vs-transient error
classification, canonical-message translation edge cases (empty turns,
thinking-block replay order), network-flag validation, API-key resolution
precedence, the retired-model guard on `execute_run`, arena fingerprinting
for a missing context dir, and secret-shaped-string redaction.

Written from the spec/interfaces only — the modules under test were not read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import runectl.trace.writer as w
from runectl.cli.run_cmd import RunRequest, execute_run, parse_network
from runectl.errors import UsageError
from runectl.loop.state import Challenge
from runectl.providers.anthropic import _to_anthropic_messages
from runectl.providers.base import Message, ToolCallRequest, TransientProviderError, transient_or_billing
from runectl.providers.google import _to_google_contents
from runectl.providers.keys import resolve_key
from runectl.providers.openai import _to_responses_input
from runectl.providers.registry import MODEL_REGISTRY
from runectl.sandbox.arena_build import ArenaStatus, arena_fingerprint
from runectl.user_config import _KEY_SHAPED

# ---------------------------------------------------------------------------
# 1. transient_or_billing: quota/billing text -> UsageError, else transient.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "429 insufficient_quota: you exceeded your current quota",
        "you exceeded your current quota, please check your plan and billing details",
        "quota exceeded for this project",
        "billing hard limit reached",
    ],
)
def test_billing_phrasings_become_a_usage_error(message: str) -> None:
    result = transient_or_billing("OpenAI", Exception(message))
    assert isinstance(result, UsageError)


def test_an_ordinary_rate_limit_stays_transient_and_keeps_retry_after() -> None:
    result = transient_or_billing("OpenAI", Exception("429 rate limit exceeded"), 30.0)
    assert isinstance(result, TransientProviderError)
    assert result.retry_after == 30.0


def test_transient_or_billing_returns_rather_than_raises() -> None:
    # It hands back the exception; the caller decides whether to raise it.
    result = transient_or_billing("OpenAI", Exception("429 rate limit exceeded"))
    assert isinstance(result, Exception)


# ---------------------------------------------------------------------------
# 2. A wholly empty assistant turn is skipped by all three translators; one
#    with content or a tool call is emitted.
# ---------------------------------------------------------------------------

_EMPTY_ASSISTANT = Message(role="assistant", content="")
_CONTENT_ASSISTANT = Message(role="assistant", content="thinking out loud")
_TOOLCALL_ASSISTANT = Message(
    role="assistant",
    content="",
    tool_calls=(ToolCallRequest(id="call-1", name="run_command", arguments={"command": "ls"}),),
)
_USER_A = Message(role="user", content="hello")
_USER_B = Message(role="user", content="again")


def test_anthropic_skips_a_wholly_empty_assistant_turn() -> None:
    out = _to_anthropic_messages([_USER_A, _EMPTY_ASSISTANT, _USER_B])
    assert not any(m["role"] == "assistant" for m in out)


def test_anthropic_emits_an_assistant_turn_with_content() -> None:
    out = _to_anthropic_messages([_USER_A, _CONTENT_ASSISTANT, _USER_B])
    assert any(m["role"] == "assistant" for m in out)


def test_anthropic_emits_an_assistant_turn_with_a_tool_call() -> None:
    out = _to_anthropic_messages([_USER_A, _TOOLCALL_ASSISTANT, _USER_B])
    assert any(m["role"] == "assistant" for m in out)


def test_openai_skips_a_wholly_empty_assistant_turn() -> None:
    # Responses input: an empty assistant turn contributes no item at all.
    out = _to_responses_input([_USER_A, _EMPTY_ASSISTANT, _USER_B])
    assert not any(item.get("role") == "assistant" for item in out)


def test_openai_emits_an_assistant_turn_with_content() -> None:
    out = _to_responses_input([_USER_A, _CONTENT_ASSISTANT, _USER_B])
    assert any(item.get("role") == "assistant" for item in out)


def test_openai_emits_an_assistant_turn_with_a_tool_call() -> None:
    out = _to_responses_input([_USER_A, _TOOLCALL_ASSISTANT, _USER_B])
    # A tool call becomes a function_call item, not an assistant message.
    assert any(item.get("type") == "function_call" for item in out)


def test_google_skips_a_wholly_empty_assistant_turn() -> None:
    out = _to_google_contents([_USER_A, _EMPTY_ASSISTANT, _USER_B])
    assert not any(c.role == "model" for c in out)
    assert len(out) == 2


def test_google_emits_an_assistant_turn_with_content() -> None:
    out = _to_google_contents([_USER_A, _CONTENT_ASSISTANT, _USER_B])
    assert any(c.role == "model" for c in out)


def test_google_emits_an_assistant_turn_with_a_tool_call() -> None:
    out = _to_google_contents([_USER_A, _TOOLCALL_ASSISTANT, _USER_B])
    assert any(c.role == "model" for c in out)


# ---------------------------------------------------------------------------
# 3. Anthropic replays a thinking block first in the assistant content list.
# ---------------------------------------------------------------------------


def test_anthropic_replays_thinking_block_first_in_assistant_content() -> None:
    thinking_block = {"type": "redacted_thinking", "data": "abc"}
    message = Message(
        role="assistant",
        content="here is my answer",
        tool_calls=(ToolCallRequest(id="call-1", name="run_command", arguments={"command": "ls"}),),
        thinking_blocks=(thinking_block,),
    )
    out = _to_anthropic_messages([message])
    assistant_dicts = [m for m in out if m["role"] == "assistant"]
    assert len(assistant_dicts) == 1
    assert assistant_dicts[0]["content"][0] == thinking_block


# ---------------------------------------------------------------------------
# 4. parse_network: only "none"/"bridge" are accepted.
# ---------------------------------------------------------------------------


def test_parse_network_accepts_none() -> None:
    assert parse_network("none") == "none"


def test_parse_network_accepts_bridge() -> None:
    assert parse_network("bridge") == "bridge"


def test_parse_network_rejects_host() -> None:
    with pytest.raises(UsageError):
        parse_network("host")


# ---------------------------------------------------------------------------
# 5. resolve_key: GEMINI_API_KEY is a fallback alias for google, but
#    GOOGLE_API_KEY wins when both are set.
# ---------------------------------------------------------------------------


def test_gemini_api_key_is_used_as_a_google_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert resolve_key("google") == "k"


def test_google_api_key_wins_over_the_gemini_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert resolve_key("google") == "g"


# ---------------------------------------------------------------------------
# 6. execute_run rejects a retired model (main or utility) before Docker/key.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("RUNECTL_HOME", str(tmp_path))
    return tmp_path


def _challenge() -> Challenge:
    return Challenge(name="c1", category="misc", description="d")


def _retired_model_id() -> str:
    return next(m.id for m in MODEL_REGISTRY.values() if m.retired_reason)


def _working_model_id() -> str:
    return next(m.id for m in MODEL_REGISTRY.values() if not m.retired_reason)


def test_execute_run_rejects_a_retired_main_model(isolated_home: Path) -> None:
    with pytest.raises(UsageError):
        execute_run(_challenge(), RunRequest(model=_retired_model_id()))


def test_execute_run_rejects_a_retired_utility_model(isolated_home: Path) -> None:
    with pytest.raises(UsageError):
        execute_run(
            _challenge(),
            RunRequest(model=_working_model_id(), utility_model=_retired_model_id()),
        )


# ---------------------------------------------------------------------------
# 7. Arena fingerprint/status for a missing context dir and an unknown
#    expected fingerprint.
# ---------------------------------------------------------------------------


def test_arena_fingerprint_of_a_nonexistent_dir_is_none() -> None:
    assert arena_fingerprint(Path("/no/such/dir")) is None


def test_arena_status_with_unknown_expected_fingerprint_is_present_not_stale() -> None:
    status = ArenaStatus(tag="x", image_id="abc", built_fingerprint=None, expected_fingerprint=None)
    assert status.present is True
    assert status.stale is False


# ---------------------------------------------------------------------------
# 8. Key-shaped secrets (service-account/admin key prefixes) are redacted
#    from the trace and rejected from config; ordinary text is not.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["sk-svcacct-" + "A" * 24, "sk-admin-" + "B" * 24])
def test_service_and_admin_shaped_keys_match_the_trace_redaction_patterns(key: str) -> None:
    assert any(rx.search(key) for rx in w._REDACT_PATTERNS)


@pytest.mark.parametrize("key", ["sk-svcacct-" + "A" * 24, "sk-admin-" + "B" * 24])
def test_service_and_admin_shaped_keys_match_config_key_shape(key: str) -> None:
    assert _KEY_SHAPED.match(key)


def test_plain_text_is_not_redacted_or_key_shaped() -> None:
    text = "just some text"
    assert not any(rx.search(text) for rx in w._REDACT_PATTERNS)
    assert not _KEY_SHAPED.match(text)
