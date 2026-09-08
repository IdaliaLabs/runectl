"""D8/D16 end to end: an agent that repeats itself gets stopped, not billed.

This is the failure the predecessor burned competitions on — the same fuzz,
wider each time, forever. Scripted provider + stub sandbox, so it costs
nothing to assert.
"""

from __future__ import annotations

from pathlib import Path

from runectl.categories.loader import load as load_category
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.base import Completion, ToolCallRequest, Usage
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.scripted import ScriptedProvider
from runectl.sandbox.stub import StubSandbox, ok
from runectl.trace.events import BudgetBlocked, ProgressScored, StrategyShift
from runectl.trace.reader import TraceReader
from runectl.trace.writer import TraceWriter


def _same_call(n: int) -> Completion:
    return Completion(
        text="",
        tool_calls=(
            ToolCallRequest(
                id=f"c{n}",
                name="run_command",
                arguments={"command": "ffuf -w /usr/share/wordlists/big.txt -u http://t/FUZZ"},
            ),
        ),
        usage=Usage(input_tokens=10, output_tokens=5),
        stop_reason="tool_use",
    )


def _run(tmp_path: Path, steps: int = 12):  # type: ignore[no-untyped-def]
    sandbox = StubSandbox(default=ok("HTTP/1.1 404 Not Found"))
    sandbox.start()
    writer = TraceWriter(tmp_path / "trace.jsonl", "loop-run", tmp_path / "artifacts")
    category = load_category("web").model_copy(update={"step_limit": steps})
    runner = Runner(
        challenge=Challenge(name="c", category="web", description=""),
        category=category,
        model=resolve_model("claude-haiku-4-5-20251001"),
        provider=ScriptedProvider([_same_call(i) for i in range(steps)]),
        sandbox=sandbox,
        writer=writer,
        max_cost_usd=0.0,
    )
    outcome = runner.run()
    writer.close()
    payloads = [e.payload() for e in TraceReader(tmp_path / "trace.jsonl", tmp_path / "artifacts")]
    return outcome, payloads


def test_the_same_useless_command_gets_budget_blocked(tmp_path: Path) -> None:
    outcome, payloads = _run(tmp_path)

    blocked = [p for p in payloads if isinstance(p, BudgetBlocked)]
    assert blocked, "a repeated no-signal command must eventually be blocked"
    assert blocked[0].family == "dirfuzz"
    assert outcome.blocked_steps > 0


def test_repeats_are_not_counted_as_progress(tmp_path: Path) -> None:
    outcome, payloads = _run(tmp_path)

    scored = [p for p in payloads if isinstance(p, ProgressScored)]
    assert scored
    # The first 404 is new information; every identical one after it is not.
    assert sum(1 for p in scored if p.delta > 0) <= 1
    assert outcome.progress_steps <= 1


def test_the_agent_is_told_to_change_approach(tmp_path: Path) -> None:
    _, payloads = _run(tmp_path)

    shifts = [p for p in payloads if isinstance(p, StrategyShift)]
    assert shifts
    assert "different hypothesis class" in shifts[0].evidence_summary


def test_the_progress_ratio_reflects_the_waste(tmp_path: Path) -> None:
    """D16: the metric is waste, not step count."""
    outcome, _ = _run(tmp_path)

    ratio = outcome.progress_steps / outcome.steps_used
    assert ratio < 0.2, f"a pure repeat loop should score near-zero progress, got {ratio:.2f}"
