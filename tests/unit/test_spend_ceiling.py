"""D19: a run stops itself at the spend ceiling rather than draining a balance."""

from __future__ import annotations

from pathlib import Path

import pytest

from runectl.categories.loader import load as load_category
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.base import Completion, ToolCallRequest, Usage
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.scripted import ScriptedProvider
from runectl.sandbox.stub import StubSandbox, ok
from runectl.trace.events import BudgetExhausted
from runectl.trace.reader import TraceReader
from runectl.trace.writer import TraceWriter


def _expensive_step() -> Completion:
    """One call that costs ~$0.25 on haiku (250k input tokens)."""
    return Completion(
        text="",
        tool_calls=(
            ToolCallRequest(id="c", name="run_command", arguments={"command": "echo hi"}),
        ),
        usage=Usage(input_tokens=250_000, output_tokens=0),
        stop_reason="tool_use",
    )


def _runner(tmp_path: Path, *, max_cost: float, steps: int) -> Runner:
    sandbox = StubSandbox(default=ok("hi"))
    sandbox.start()
    writer = TraceWriter(tmp_path / "trace.jsonl", "run-1", tmp_path / "artifacts")
    # Cap the step limit to the length of the script so an uncapped run ends by
    # exhausting its steps rather than running off the end of the provider.
    category = load_category("misc").model_copy(update={"step_limit": steps})
    return Runner(
        challenge=Challenge(name="c", category="misc", description=""),
        category=category,
        model=resolve_model("claude-haiku-4-5"),
        provider=ScriptedProvider([_expensive_step() for _ in range(steps)]),
        sandbox=sandbox,
        writer=writer,
        max_cost_usd=max_cost,
    )


def test_run_stops_at_the_ceiling_and_reports_exhausted(tmp_path: Path) -> None:
    runner = _runner(tmp_path, max_cost=0.50, steps=10)

    outcome = runner.run()

    # 250k input tokens on haiku = $0.25/step, so step 2 reaches exactly $0.50.
    assert outcome.steps_used == 2
    assert outcome.outcome == "exhausted"
    assert outcome.exit_code == 3
    assert outcome.cost_usd >= 0.50


def test_the_stop_is_recorded_in_the_trace(tmp_path: Path) -> None:
    runner = _runner(tmp_path, max_cost=0.50, steps=10)
    runner.run()

    payloads = [e.payload() for e in TraceReader(tmp_path / "trace.jsonl", tmp_path / "artifacts")]
    exhausted = [p for p in payloads if isinstance(p, BudgetExhausted)]

    assert len(exhausted) == 1
    assert exhausted[0].limit_usd == 0.50


def test_zero_disables_the_ceiling(tmp_path: Path) -> None:
    runner = _runner(tmp_path, max_cost=0.0, steps=4)

    outcome = runner.run()

    # Runs the whole script instead of stopping at $0.50.
    assert outcome.steps_used == 4
    assert outcome.cost_usd == pytest.approx(1.0)
    assert outcome.cost_usd > 0.50
