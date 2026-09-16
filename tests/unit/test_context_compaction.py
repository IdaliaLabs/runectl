"""History compaction (D12, simple compaction as of 2026-09-16): it triggers,
it pins the challenge message, and it never leaves a verbatim turn (hence no
orphaned tool result and no thinking block) across the boundary."""

from __future__ import annotations

from runectl.loop.context import ContextBuilder, compact_history
from runectl.providers.base import Message, ToolCallRequest


def _summarize(messages: list[Message]) -> str:
    return f"summary of {len(messages)} messages"


def test_below_the_budget_is_a_no_op() -> None:
    history = [Message(role="user", content=f"m{i}") for i in range(5)]
    assert compact_history(history, max_messages=10, summarize=_summarize) == history


def test_compaction_folds_everything_after_the_pinned_head_into_one_summary() -> None:
    history = [Message(role="user", content=f"m{i}") for i in range(20)]
    compacted = compact_history(history, max_messages=5, summarize=_summarize)

    # Simple compaction: the pinned head plus a single summary of the rest.
    assert len(compacted) == 2
    assert compacted[0] == history[0]  # the challenge message is kept verbatim
    assert "summary of 19 messages" in compacted[1].content
    assert compacted[1].content.startswith("[compacted summary of 19 earlier messages]")


def test_no_verbatim_turn_survives_the_boundary() -> None:
    """Nothing after the pinned head is replayed verbatim, so a tool result can
    never be orphaned and a provider-native thinking block is never sent against
    an edited prefix (the preserved-thinking check's failure shape)."""
    history = [
        Message(role="user", content="start"),
        Message(role="user", content="filler"),
        Message(
            role="assistant",
            content="",
            tool_calls=(ToolCallRequest(id="c1", name="run_command", arguments={}),),
            thinking_blocks=({"type": "thinking", "thinking": "…"},),
        ),
        Message(role="tool", tool_call_id="c1", tool_name="run_command", content="output"),
        Message(role="user", content="next"),
    ]
    compacted = compact_history(history, max_messages=2, summarize=_summarize)

    assert [m.role for m in compacted] == ["user", "user"]
    assert compacted[0] == history[0]
    # No tool call, tool result, or thinking block is carried across.
    assert all(not m.tool_calls and not m.thinking_blocks for m in compacted)
    assert all(m.role != "tool" for m in compacted)


def test_context_builder_compacts_without_a_utility_model() -> None:
    """No llm_summarize wired (tests, replay) still compacts deterministically
    rather than silently dropping the older context."""
    builder = ContextBuilder(max_messages=4)
    history = [Message(role="user", content=f"m{i}") for i in range(30)]

    compacted = builder.maybe_compact(history)

    assert len(compacted) == 2
    assert compacted[0].content == "m0"  # the pinned head
    assert compacted[1].content.startswith("[compacted summary of 29 earlier messages]")
    # A compacted history is far under the budget, so the next step does not
    # re-compact (and never summarizes its own summary).
    assert builder.maybe_compact(compacted) == compacted


def test_compaction_does_not_refire_every_step() -> None:
    """The keep-tail scheme trimmed to exactly the budget and re-fired on every
    subsequent step; simple compaction leaves room for many steps first."""
    builder = ContextBuilder(max_messages=6)
    history = [Message(role="user", content=f"m{i}") for i in range(20)]

    compacted = builder.maybe_compact(history)
    calls = 0
    # Two messages appended per step; count how many steps until it recompacts.
    for step in range(10):
        compacted = builder.maybe_compact(compacted)
        if len(compacted) == 2 and compacted[1].content.startswith("[compacted summary"):
            # already compact; simulate the loop appending a step's worth
            pass
        compacted.append(Message(role="assistant", content=f"a{step}"))
        compacted.append(Message(role="user", content=f"u{step}"))
        # count an actual (re)compaction: history shrinks back to head+summary
        before = len(compacted)
        compacted = builder.maybe_compact(compacted)
        if len(compacted) < before:
            calls += 1
    # Far fewer than one-per-step (keep-tail would have been ~10).
    assert calls <= 3


def test_empty_utility_summary_falls_back_to_extractive() -> None:
    """If the utility model returns nothing (its reasoning ate the token budget),
    compaction must not drop the history — it falls back to the deterministic
    head/tail/salient extraction (D12 promises never to drop)."""
    builder = ContextBuilder(max_messages=4, llm_summarize=lambda _text: "")
    history = [Message(role="user", content=f"m{i} error flag{{x{i}}}") for i in range(30)]

    compacted = builder.maybe_compact(history)

    assert len(compacted) == 2
    assert compacted[0].content == "m0 error flag{x0}"  # pinned head
    # The summary is the deterministic extraction, not an empty string.
    assert len(compacted[1].content) > len("[compacted summary of 29 earlier messages]\n")
