"""Progress-machinery fixes (D8/D16): rev signal anchoring, the strategy-shift
reason, write_file exemption, and category-schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from runectl.categories.loader import load
from runectl.categories.schema import Category
from runectl.progress.tracker import ProgressTracker


def test_rev_incorrect_scores_low_not_high() -> None:
    # F1: `correct` was unanchored, so "Incorrect!" (a failed guess) scored HIGH.
    t = ProgressTracker(category=load("rev"))
    a = t.record("./crackme guess", "Incorrect! Try again.", ok=True, step=1)
    assert a.signal == "low"
    assert not a.progressed


def test_rev_correct_still_scores_high() -> None:
    # F1: a genuine "Correct!" (the right input) is still a high signal.
    t = ProgressTracker(category=load("rev"))
    a = t.record("./crackme right", "Correct! Access granted.", ok=True, step=1)
    assert a.signal == "high"
    assert a.progressed


def test_strategy_shift_reports_errors_when_errors_fired() -> None:
    # F4: the reason kind was read after due_shift() reset the counter, so it
    # always said "no-progress"; now it reflects the threshold that fired.
    t = ProgressTracker(category=load("misc"))
    for i in range(3):
        t.record(f"cmd{i}", "boom", ok=False, step=i + 1)
    assert t.due_shift() is not None
    assert t.shift_reason_kind == "errors"


def test_repeated_write_file_is_never_budget_blocked() -> None:
    # F3: write_file is setup, not a hypothesis — the same filename must not
    # exhaust the per-hypothesis budget and freeze the write-then-run loop.
    from runectl.loop.state import Challenge
    from runectl.providers.base import Completion, ToolCallRequest, Usage
    from runectl.providers.registry import resolve as resolve_model
    from runectl.providers.scripted import ScriptedProvider
    from runectl.sandbox.stub import StubSandbox, ok
    from runectl.trace.store import Store

    def write(step: int) -> Completion:
        return Completion(
            text="",
            tool_calls=(ToolCallRequest(id=f"c{step}", name="write_file",
                                        arguments={"filename": "exploit.py", "content": f"# v{step}"}),),
            usage=Usage(input_tokens=1, output_tokens=1), stop_reason="tool_use",
        )

    import os
    import tempfile
    os.environ["RUNECTL_HOME"] = tempfile.mkdtemp()
    store = Store()
    chal = Challenge(name="t", category="pwn", description="d")
    rid, writer = store.new_run(
        challenge_name="t", category="pwn", model="claude-sonnet-5", provider="anthropic",
        config_snapshot={"challenge": chal.model_dump(mode="json"), "approval_policy": "gated"},
    )
    from runectl.loop.runner import Runner
    sb = StubSandbox(default=ok("wrote"))
    r = Runner(challenge=chal, category=load("pwn"), model=resolve_model("claude-sonnet-5"),
               provider=ScriptedProvider([write(i) for i in range(6)]), sandbox=sb, writer=writer)
    sb.start()
    r.run()
    writer.close()
    # No write was budget-blocked (the run exhausts the scripted provider instead).
    _, events = __import__("runectl.trace.reader", fromlist=["load_run"]).load_run(rid)
    blocked = [e for e in events if e.type == "budget.blocked"]
    assert blocked == []


@pytest.mark.parametrize("kwargs", [
    {"step_limit": 0},
    {"step_limit": 5, "budgets": {"per_hypothesis": 0}},
    {"step_limit": 5, "signal_high": ["(unclosed"]},
    {"step_limit": 5, "tactic_families": {"bad": "(also unclosed"}},
])
def test_schema_rejects_bad_categories(kwargs: dict) -> None:
    # F5: a zero budget/step limit or an uncompilable regex fails at load.
    with pytest.raises(ValidationError):
        Category(name="x", brief="b", playbook="p", **kwargs)
