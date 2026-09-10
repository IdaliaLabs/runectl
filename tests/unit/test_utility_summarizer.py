"""D5/D12: utility calls (context summarization) are provider-agnostic and
land in the same cost ledger as the main loop's calls — never untracked."""

from __future__ import annotations

from runectl.loop.context import ContextBuilder
from runectl.providers.base import Completion, Usage, make_utility_summarizer
from runectl.providers.cost import CostLedger
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.scripted import ScriptedProvider


def test_utility_summarizer_costs_land_in_shared_ledger() -> None:
    ledger = CostLedger()
    model = resolve_model("claude-haiku-4-5")
    utility_provider = ScriptedProvider(
        [
            Completion(
                text="short summary",
                tool_calls=(),
                usage=Usage(input_tokens=500, output_tokens=50),
                stop_reason="end_turn",
            )
        ]
    )
    summarizer = make_utility_summarizer(utility_provider, model, ledger)

    result = summarizer("x" * 20000)

    assert result == "short summary"
    assert ledger.total_usd > 0
    assert ledger.entries[0].model_id == model.id
    assert ledger.entries[0].input_tokens == 500


def test_oversized_output_triggers_the_summarizer_hook() -> None:
    calls: list[str] = []

    def fake_summarize(text: str) -> str:
        calls.append(text)
        return "SUMMARY"

    builder = ContextBuilder(limit=100, llm_summarize=fake_summarize)
    rendered = builder.render_tool_output("x" * 5000, step=1)

    assert calls, "extractive summarization alone should still exceed the 100-char limit"
    assert rendered == "SUMMARY"
