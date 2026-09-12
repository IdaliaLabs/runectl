"""D20 — extended thinking: resolution/clamping, round-trip, trace, and render.

Together with test_provider_retry.py's fakes (now thinking-aware), this is
the zero-spend coverage for a feature whose real adapters
(providers/anthropic.py, openai.py, google.py) can't be exercised here at
all (no daemon, no key — CONTRIBUTING.md).
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from runectl.categories.loader import load as load_category
from runectl.cli.render import event_line
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.base import Completion, Message, Usage
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.registry import resolve_thinking_level
from runectl.providers.replay import request_hash
from runectl.providers.scripted import ScriptedProvider, Step
from runectl.sandbox.stub import StubSandbox, ok
from runectl.trace.events import LlmThinking, RunStarted
from runectl.trace.writer import TraceWriter


def test_resolve_thinking_level_off_stays_off_when_the_model_can_be_stopped() -> None:
    model = resolve_model("claude-opus-4-8")  # thinking_off_supported=True (defaults off)
    assert resolve_thinking_level(model, "off") == ("off", False)


def test_resolve_thinking_level_off_is_clamped_up_when_the_model_thinks_anyway() -> None:
    """The D20 2026-09-11 amendment, and the reason it exists.

    This assertion used to read `("off", False)` for `claude-sonnet-5` — which
    was wrong, and wrong in the direction that hides spend. Sonnet 5 thinks by
    default; sending no thinking configuration does not stop it. The old
    behavior produced a run that thought, billed for it, and wrote
    `thinking_level="off"` into its own trace with no clamp recorded.
    """
    model = resolve_model("claude-sonnet-5")  # thinking_off_supported=False
    assert resolve_thinking_level(model, "off") == ("low", True)


def test_every_think_by_default_model_reports_the_clamp() -> None:
    """No registry row may quietly reintroduce the bug above."""
    from runectl.providers.registry import MODEL_REGISTRY

    for model in MODEL_REGISTRY.values():
        resolved, clamped = resolve_thinking_level(model, "off")
        if model.supports_thinking and not model.thinking_off_supported:
            assert (resolved, clamped) == ("low", True), model.id
        else:
            assert (resolved, clamped) == ("off", False), model.id


def test_resolve_thinking_level_no_support_clamps_to_off() -> None:
    model = resolve_model("claude-haiku-4-5")  # supports_thinking=False (utility model)
    assert resolve_thinking_level(model, "high") == ("off", True)


def test_resolve_thinking_level_within_ceiling_passes_through() -> None:
    model = resolve_model("claude-sonnet-5")  # max_thinking_level="max"
    assert resolve_thinking_level(model, "high") == ("high", False)


def test_resolve_thinking_level_above_ceiling_clamps_down() -> None:
    model = resolve_model("gemini-2.5-pro")  # max_thinking_level="high" (no xhigh/max)
    assert resolve_thinking_level(model, "max") == ("high", True)


def test_scripted_provider_records_the_requested_level() -> None:
    completion = Completion(
        text="ok", tool_calls=(), usage=Usage(input_tokens=1, output_tokens=1), stop_reason="end_turn"
    )
    provider = ScriptedProvider([completion])
    provider.complete(system="s", messages=[], tools=(), max_tokens=10, thinking="high")
    assert provider.thinking_requests == ["high"]


def test_request_hash_differs_by_thinking_level() -> None:
    """A cassette recorded with thinking off must not serve a thinking-on replay (D20)."""
    off_hash = request_hash("sys", [], (), 100, "off")
    high_hash = request_hash("sys", [], (), 100, "high")
    assert off_hash != high_hash


def _stub_sandbox() -> StubSandbox:
    return StubSandbox(script={"ls -la /ctf/": ok("nothing here")})


def _thinking_then_stall_completions(long_text: str) -> list[Step]:
    def second_step(messages: Sequence[Message]) -> Completion:
        # The first assistant turn's thinking blocks must round-trip back in
        # as this turn's second-to-last message's thinking_blocks (D20).
        last_assistant = next(m for m in reversed(messages) if m.role == "assistant")
        assert last_assistant.thinking_blocks == ({"type": "thinking", "thinking": "reasoned"},)
        return Completion(
            text="", tool_calls=(), usage=Usage(input_tokens=1, output_tokens=1), stop_reason="end_turn"
        )

    return [
        Completion(
            text="",
            tool_calls=(),
            usage=Usage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
            thinking_text=long_text,
            thinking_blocks=({"type": "thinking", "thinking": "reasoned"},),
        ),
        second_step,
    ]


def test_runner_emits_llm_thinking_and_round_trips_blocks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    model = resolve_model("claude-sonnet-5")
    # Capped to exactly the two scripted steps below — the loop otherwise
    # keeps nudging a tool-call-less reply until the category's real step
    # limit, which would outrun the script.
    category = load_category("misc").model_copy(update={"step_limit": 2})
    challenge = Challenge(name="t", category="misc", description="d")
    provider = ScriptedProvider(_thinking_then_stall_completions("reasoned about the flag"))
    sandbox = _stub_sandbox()
    sandbox.start()
    writer = TraceWriter(tmp_path / "trace.jsonl", "run-1", tmp_path / "artifacts")
    runner = Runner(
        challenge=challenge, category=category, model=model, provider=provider,
        sandbox=sandbox, writer=writer, thinking="high",
    )
    runner.run()
    sandbox.stop()
    writer.close()

    events = [e.payload() for e in _read_events(tmp_path / "trace.jsonl", tmp_path / "artifacts")]
    started = next(e for e in events if isinstance(e, RunStarted))
    assert started.thinking_level == "high"
    assert started.thinking_clamped_from is None

    thinking_events = [e for e in events if isinstance(e, LlmThinking)]
    assert len(thinking_events) == 1
    assert thinking_events[0].text == "reasoned about the flag"
    assert thinking_events[0].level == "high"
    assert thinking_events[0].truncated is False


def test_llm_thinking_is_truncated_past_the_display_cap(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from runectl.loop.runner import _MAX_THINKING_CHARS

    model = resolve_model("claude-sonnet-5")
    category = load_category("misc").model_copy(update={"step_limit": 2})
    challenge = Challenge(name="t", category="misc", description="d")
    huge = "x" * (_MAX_THINKING_CHARS + 500)
    provider = ScriptedProvider(_thinking_then_stall_completions(huge))
    sandbox = _stub_sandbox()
    sandbox.start()
    writer = TraceWriter(tmp_path / "trace.jsonl", "run-1", tmp_path / "artifacts")
    runner = Runner(
        challenge=challenge, category=category, model=model, provider=provider,
        sandbox=sandbox, writer=writer, thinking="high",
    )
    runner.run()
    sandbox.stop()
    writer.close()

    events = [e.payload() for e in _read_events(tmp_path / "trace.jsonl", tmp_path / "artifacts")]
    thinking_event = next(e for e in events if isinstance(e, LlmThinking))
    assert thinking_event.truncated is True
    assert len(thinking_event.text) == _MAX_THINKING_CHARS


def test_run_started_records_a_clamp_loudly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    model = resolve_model("claude-haiku-4-5")  # supports_thinking=False
    # run.started is emitted before the step loop begins, so a zero step limit
    # is enough to observe it with no scripted completions needed at all.
    category = load_category("misc").model_copy(update={"step_limit": 0})
    challenge = Challenge(name="t", category="misc", description="d")
    provider = ScriptedProvider([])
    sandbox = _stub_sandbox()
    sandbox.start()
    writer = TraceWriter(tmp_path / "trace.jsonl", "run-1", tmp_path / "artifacts")
    runner = Runner(
        challenge=challenge, category=category, model=model, provider=provider,
        sandbox=sandbox, writer=writer, thinking="high",
    )
    runner.run()
    sandbox.stop()
    writer.close()

    events = [e.payload() for e in _read_events(tmp_path / "trace.jsonl", tmp_path / "artifacts")]
    started = next(e for e in events if isinstance(e, RunStarted))
    assert started.thinking_level == "off"
    assert started.thinking_clamped_from == "high"


def _read_events(trace_path, artifacts_dir):  # type: ignore[no-untyped-def]
    from runectl.trace.reader import TraceReader

    return list(TraceReader(trace_path, artifacts_dir))


def test_render_line_for_llm_thinking_is_clipped_and_marks_truncation() -> None:
    payload = LlmThinking(step=4, text="x" * 500, level="high", truncated=True)
    line = event_line(payload, width=100)
    assert line is not None
    assert line.endswith("…")
    assert "\n" not in line


def test_render_line_for_run_started_shows_thinking_when_on() -> None:
    payload = RunStarted(
        challenge_name="c", category="misc", model="claude-sonnet-5", provider="anthropic",
        approval_policy="gated", max_steps=10, network="none",
        thinking_level="high", thinking_clamped_from="max",
    )
    line = event_line(payload, width=100)
    assert line is not None
    assert "thinking=high" in line
    assert "clamped from max" in line


def test_render_line_for_run_started_omits_thinking_when_off() -> None:
    payload = RunStarted(
        challenge_name="c", category="misc", model="claude-sonnet-5", provider="anthropic",
        approval_policy="gated", max_steps=10, network="none",
    )
    line = event_line(payload, width=100)
    assert line is not None
    assert "thinking" not in line


def test_user_config_round_trips_and_rejects_key_shaped_values(  # type: ignore[no-untyped-def]
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runectl.errors import UsageError
    from runectl.user_config import default_thinking, set_value

    monkeypatch.setenv("RUNECTL_CONFIG_HOME", str(tmp_path))
    set_value("anthropic", "thinking", "high")
    assert default_thinking("anthropic") == "high"
    assert default_thinking("google") == "off"  # unset

    with pytest.raises(UsageError):
        set_value("anthropic", "api_key", "sk-ant-abcdefghijklmnopqrstuvwxyz")
