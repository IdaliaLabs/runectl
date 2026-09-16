"""Run-lifecycle error handling: a run that blows up mid-loop (sandbox death,
provider failure, billing failure) still produces a well-formed trace — a
terminal ``run.finished`` event as the last line, the right exit code, and no
silently-discarded spend. Black-box: written from the spec only, no reading of
``loop/runner.py`` / ``cli/run_cmd.py`` / ``sandbox/base.py`` bodies.

Follows the StubSandbox + ScriptedProvider + `Runner` conventions in
``test_walking_skeleton.py`` / ``test_spend_ceiling.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runectl.categories.loader import load as load_category
from runectl.cli.run_cmd import resolve_max_cost
from runectl.errors import ProviderError, SandboxError, UsageError
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.base import Completion, ToolCallRequest, Usage
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.scripted import ScriptedProvider
from runectl.sandbox.base import ExecResult, sandbox_session
from runectl.sandbox.stub import StubSandbox, ok
from runectl.trace.reader import load_run
from runectl.trace.store import Store


@pytest.fixture
def runectl_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "runectl-home"
    monkeypatch.setenv("RUNECTL_HOME", str(home))
    return home


class _FlakySandbox(StubSandbox):
    """Every `exec` succeeds except one designated command, which raises
    `SandboxError`. Keyed by command rather than call count so it fails on
    the run's own (later) tool call regardless of how many `exec` calls an
    earlier recon/triage phase makes first — the first *successful* call is
    whatever recon needs; the point under test is a failure after that.
    """

    def __init__(self, *args: object, fail_command: str, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._fail_command = fail_command

    def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult:
        if argv_or_script == self._fail_command:
            raise SandboxError("sandbox connection lost")
        return super().exec(argv_or_script, timeout_s=timeout_s)


def _run_command(call_id: str, command: str) -> Completion:
    return Completion(
        text="",
        tool_calls=(
            ToolCallRequest(
                id=call_id,
                name="run_command",
                arguments={"command": command, "reasoning": "test"},
            ),
        ),
        usage=Usage(input_tokens=100, output_tokens=10),
        stop_reason="tool_use",
    )


def _run_sandbox_error_scenario(model_id: str = "claude-sonnet-5"):
    """Build+run a challenge whose sandbox dies on the second `exec` call.

    Returns (store, run_id, outcome).
    """
    store = Store()
    challenge = Challenge(name="err-01", category="misc", description="test")
    category = load_category("misc")
    model = resolve_model(model_id)

    run_id, writer = store.new_run(
        challenge_name=challenge.name,
        category=challenge.category,
        model=model.id,
        provider=model.provider,
        config_snapshot={"challenge": challenge.model_dump(mode="json"), "approval_policy": "gated"},
    )
    sandbox = _FlakySandbox(default=ok("hi"), fail_command="echo two")
    sandbox.start()
    provider = ScriptedProvider(
        [_run_command("call_1", "echo one"), _run_command("call_2", "echo two")]
    )
    runner = Runner(
        challenge=challenge,
        category=category,
        model=model,
        provider=provider,
        sandbox=sandbox,
        writer=writer,
    )
    outcome = runner.run()
    sandbox.stop()
    writer.close()
    store.finish_run(
        run_id,
        outcome=outcome.outcome,
        exit_code=outcome.exit_code,
        flag=outcome.flag,
        cost_usd=outcome.cost_usd,
        steps_used=outcome.steps_used,
        progress_steps=outcome.progress_steps,
        blocked_steps=outcome.blocked_steps,
    )
    return store, run_id, outcome


class _BillingFailsProvider:
    """A provider whose main call always fails with a billing (usage) error."""

    def complete(self, *, system, messages, tools, max_tokens, thinking="off"):  # noqa: ANN001
        raise UsageError("out of credit")


class _AlwaysFailsProvider:
    """A provider whose main call always fails with a generic provider error."""

    def complete(self, *, system, messages, tools, max_tokens, thinking="off"):  # noqa: ANN001
        raise ProviderError("upstream 500")


def _run_provider_error_scenario(provider: object):
    store = Store()
    challenge = Challenge(name="err-02", category="misc", description="test")
    category = load_category("misc")
    model = resolve_model("claude-sonnet-5")

    run_id, writer = store.new_run(
        challenge_name=challenge.name,
        category=challenge.category,
        model=model.id,
        provider=model.provider,
        config_snapshot={"challenge": challenge.model_dump(mode="json"), "approval_policy": "gated"},
    )
    sandbox = StubSandbox(default=ok("hi"))
    sandbox.start()
    runner = Runner(
        challenge=challenge,
        category=category,
        model=model,
        provider=provider,
        sandbox=sandbox,
        writer=writer,
    )
    outcome = runner.run()
    sandbox.stop()
    writer.close()
    store.finish_run(
        run_id,
        outcome=outcome.outcome,
        exit_code=outcome.exit_code,
        flag=outcome.flag,
        cost_usd=outcome.cost_usd,
        steps_used=outcome.steps_used,
        progress_steps=outcome.progress_steps,
        blocked_steps=outcome.blocked_steps,
    )
    return store, run_id, outcome


def test_sandbox_error_mid_loop_ends_with_terminal_run_finished(runectl_home: Path) -> None:
    """Criterion 1: SandboxError mid-loop -> error/exit 4, run.finished last, a sandbox error before it."""
    _store, run_id, outcome = _run_sandbox_error_scenario()

    assert outcome.outcome == "error"
    assert outcome.exit_code == 4

    manifest, events = load_run(run_id)
    events = list(events)

    assert manifest.outcome == "error"
    assert events, "expected a non-empty trace"
    assert events[-1].type == "run.finished"
    finished = events[-1].payload()
    assert finished.outcome == "error"
    assert finished.exit_code == 4

    sandbox_error_indices = [
        i for i, e in enumerate(events) if e.type == "error" and e.payload().kind == "sandbox"
    ]
    assert sandbox_error_indices, "expected an error event with kind=='sandbox'"
    assert max(sandbox_error_indices) < len(events) - 1


def test_provider_billing_error_maps_to_exit_code_6(runectl_home: Path) -> None:
    """Criterion 2: a UsageError from the provider (billing) exits 6, not 5; trace still ends run.finished."""
    _store, run_id, outcome = _run_provider_error_scenario(_BillingFailsProvider())

    assert outcome.outcome == "error"
    assert outcome.exit_code == 6

    _manifest, events = load_run(run_id)
    events = list(events)
    assert events[-1].type == "run.finished"
    assert events[-1].payload().exit_code == 6


def test_provider_generic_error_maps_to_exit_code_5(runectl_home: Path) -> None:
    """Criterion 2: a plain ProviderError from the provider exits 5, and ends the trace with run.finished."""
    _store, run_id, outcome = _run_provider_error_scenario(_AlwaysFailsProvider())

    assert outcome.outcome == "error"
    assert outcome.exit_code == 5

    _manifest, events = load_run(run_id)
    events = list(events)
    assert events[-1].type == "run.finished"
    assert events[-1].payload().exit_code == 5


def test_successful_paid_call_before_error_leaves_nonzero_cost(runectl_home: Path) -> None:
    """Criterion 3: a real (priced) completion before the error leaves cost_usd > 0, not forced to 0.0."""
    _store, run_id, outcome = _run_sandbox_error_scenario(model_id="claude-sonnet-5")

    assert outcome.cost_usd > 0

    manifest, events = load_run(run_id)
    assert manifest.cost_usd > 0

    events = list(events)
    assert events[-1].type == "run.finished"
    assert events[-1].payload().cost_usd > 0


def test_resolve_max_cost_rejects_negative_flag() -> None:
    """Criterion 4: a negative --max-cost is a usage error, not silently accepted."""
    with pytest.raises(UsageError):
        resolve_max_cost(-0.5)


def test_resolve_max_cost_allows_zero() -> None:
    """Criterion 4: 0.0 (disables the ceiling) is a valid, passed-through value."""
    assert resolve_max_cost(0.0) == 0.0


def test_resolve_max_cost_passes_through_positive_value() -> None:
    """Criterion 4: an ordinary positive value passes through unchanged."""
    assert resolve_max_cost(1.25) == 1.25


def test_sandbox_session_stops_even_if_start_raises() -> None:
    """Criterion 5: sandbox_session calls stop() even when start() raises."""
    calls: list[str] = []

    class _RecordingSandbox:
        def start(self) -> None:
            calls.append("start")
            raise SandboxError("boom")

        def stop(self) -> None:
            calls.append("stop")

        def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult:  # pragma: no cover
            raise NotImplementedError

        def write_file(self, rel_path: str, content: bytes) -> None:  # pragma: no cover
            raise NotImplementedError

        def put_inputs(self, files: object) -> list[str]:  # pragma: no cover
            raise NotImplementedError

    fake = _RecordingSandbox()

    with pytest.raises(SandboxError), sandbox_session(fake):  # type: ignore[arg-type]
        pass  # body must never run: start() raised before __enter__ returned

    assert calls == ["start", "stop"]


def test_spend_ceiling_run_keeps_every_paid_completion_matched(runectl_home: Path) -> None:
    """Criterion 6: hitting the spend ceiling still records the final paid completion in full."""
    from runectl.trace.writer import TraceWriter

    steps = 10
    store = Store()
    challenge = Challenge(name="ceiling-01", category="misc", description="test")
    category = load_category("misc").model_copy(update={"step_limit": steps})
    model = resolve_model("claude-haiku-4-5")

    run_id, writer = store.new_run(
        challenge_name=challenge.name,
        category=challenge.category,
        model=model.id,
        provider=model.provider,
        config_snapshot={"challenge": challenge.model_dump(mode="json"), "approval_policy": "gated"},
    )
    assert isinstance(writer, TraceWriter)  # sanity: same writer type as other tests use
    sandbox = StubSandbox(default=ok("hi"))
    sandbox.start()

    def _expensive_step() -> Completion:
        # ~$0.25/step on haiku (250k input tokens) — same fixture used by
        # test_spend_ceiling.py, so max_cost_usd=0.50 stops the run at step 2.
        return Completion(
            text="",
            tool_calls=(ToolCallRequest(id="c", name="run_command", arguments={"command": "echo hi"}),),
            usage=Usage(input_tokens=250_000, output_tokens=0),
            stop_reason="tool_use",
        )

    provider = ScriptedProvider([_expensive_step() for _ in range(steps)])
    runner = Runner(
        challenge=challenge,
        category=category,
        model=model,
        provider=provider,
        sandbox=sandbox,
        writer=writer,
        max_cost_usd=0.50,
    )
    outcome = runner.run()
    sandbox.stop()
    writer.close()
    store.finish_run(
        run_id,
        outcome=outcome.outcome,
        exit_code=outcome.exit_code,
        flag=outcome.flag,
        cost_usd=outcome.cost_usd,
        steps_used=outcome.steps_used,
        progress_steps=outcome.progress_steps,
        blocked_steps=outcome.blocked_steps,
    )

    assert outcome.outcome == "exhausted"
    assert outcome.exit_code == 3

    _manifest, events = load_run(run_id)
    events = list(events)

    assert any(e.type == "budget.exhausted" for e in events)

    request_steps = {e.payload().step for e in events if e.type == "llm.request"}
    response_steps = {e.payload().step for e in events if e.type == "llm.response"}
    cost_events = [e.payload() for e in events if e.type == "cost.updated"]
    cost_steps = {c.step for c in cost_events if c.step is not None}

    assert request_steps, "expected at least one llm.request"
    assert request_steps == response_steps == cost_steps
