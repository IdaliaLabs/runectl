"""History compaction (D12): it triggers, and it never orphans a tool result."""

from __future__ import annotations

from runectl.loop.context import ContextBuilder, compact_history
from runectl.providers.base import Message, ToolCallRequest


def _summarize(messages: list[Message]) -> str:
    return f"summary of {len(messages)} messages"


def test_below_the_budget_is_a_no_op() -> None:
    history = [Message(role="user", content=f"m{i}") for i in range(5)]
    assert compact_history(history, max_messages=10, summarize=_summarize) == history


def test_compaction_replaces_the_older_portion_with_one_summary() -> None:
    history = [Message(role="user", content=f"m{i}") for i in range(20)]
    compacted = compact_history(history, max_messages=5, summarize=_summarize)

    # The summary counts against the budget, so 5 means 1 summary + 4 retained.
    assert len(compacted) == 5
    assert compacted[0].role == "user"
    assert "summary of 16 messages" in compacted[0].content
    assert compacted[1:] == history[-4:]


def test_split_never_orphans_a_tool_result() -> None:
    """A retained `tool` message whose assistant turn was compacted away is an
    orphan every provider rejects — the boundary must walk past it."""
    history = [
        Message(role="user", content="start"),
        Message(role="user", content="filler"),
        Message(
            role="assistant",
            content="",
            tool_calls=(ToolCallRequest(id="c1", name="run_command", arguments={}),),
        ),
        Message(role="tool", tool_call_id="c1", tool_name="run_command", content="output"),
        Message(role="user", content="next"),
    ]
    # A naive tail of 2 would start on the orphaned tool result at index 3.
    compacted = compact_history(history, max_messages=2, summarize=_summarize)

    assert compacted[1].role != "tool"
    assert [m.role for m in compacted] == ["user", "user"]
    assert compacted[-1].content == "next"


def test_context_builder_compacts_without_a_utility_model() -> None:
    """No llm_summarize wired (tests, replay) still compacts deterministically
    rather than silently dropping the older context."""
    builder = ContextBuilder(max_messages=4)
    history = [Message(role="user", content=f"m{i}") for i in range(30)]

    compacted = builder.maybe_compact(history)

    assert len(compacted) == 4
    assert compacted[0].content.startswith("[compacted summary of 27 earlier messages]")
    # Critically: a compacted history is not still over budget, so the next step
    # does not summarize the summary.
    assert builder.maybe_compact(compacted) == compacted
