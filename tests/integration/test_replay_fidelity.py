"""A replay must reproduce the run's *outcome*, not only its tool-call sequence.

The regression this file exists for (found 2026-09-09, fixed 2026-09-10): the
D15 judge re-derives a cited command by calling ``Sandbox.exec`` directly, which
emitted no trace event. ``ReplaySandbox`` serves recorded exec results strictly
in order, so that unrecorded call silently consumed the *next* tool call's
output and shifted everything after it. The replayed run therefore issued an
identical tool-call sequence — which is all ``replay --check`` compared — while
quietly downgrading `solved` to `candidate`.

`test_walking_skeleton.py` already recorded and replayed a run that re-derives,
and passed throughout, because it asserted the sequence and never the outcome.
That is the gap these tests close.

Zero spend, no daemon: `ScriptedProvider` + `StubSandbox` throughout.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from runectl.categories.loader import load as load_category
from runectl.flags.review import ReviewVerdict
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.base import Completion, Message, ToolCallRequest, Usage
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.replay import RecordingProvider
from runectl.providers.scripted import ScriptedProvider, Step
from runectl.sandbox.replay import exec_results_from_trace
from runectl.sandbox.stub import StubSandbox, ok
from runectl.trace.events import FlagRederived, ToolResultEvent
from runectl.trace.reader import TraceReader
from runectl.trace.store import Store

FLAG = "flag{re_derived_not_guessed}"
_BLOB = "ZmxhZ3tyZV9kZXJpdmVkX25vdF9ndWVzc2VkfQ=="
_DECODE = f"echo {_BLOB} | base64 -d"
_OBSERVATION_SEQ = re.compile(r"\[observation seq=(\d+)\]")

# The command the judge re-runs is the one the agent cited, so it is already in
# the stub's table. What matters for the regression is that a *second* exec
# happens at all, after the last tool call.
_SANDBOX_COMMANDS = {
    "ls -la /ctf/": ok("total 8\n-rw-r--r-- 1 ctf ctf 40 Jan  1 00:00 chal.txt"),
    "file /ctf/* 2>/dev/null": ok("/ctf/chal.txt: ASCII text"),
    "strings -a -n 8 /ctf/* 2>/dev/null | head -60": ok(_BLOB),
    "cat /ctf/chal.txt": ok(_BLOB),
    _DECODE: ok(FLAG),
}


@pytest.fixture
def runectl_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "runectl-home"
    monkeypatch.setenv("RUNECTL_HOME", str(home))
    return home


def _stub_sandbox() -> StubSandbox:
    return StubSandbox(script=dict(_SANDBOX_COMMANDS))


def _completions() -> list[Step]:
    """Two independent routes to the same string, then a cited submission —
    the shape a `gated` run needs to auto-finalize, which is what makes the
    judge reach its re-derivation at all."""

    def _call(step: int, name: str, arguments: dict[str, object]) -> Completion:
        return Completion(
            text="",
            tool_calls=(ToolCallRequest(id=f"call_{step}", name=name, arguments=arguments),),
            usage=Usage(input_tokens=100, output_tokens=20),
            stop_reason="tool_use",
        )

    return [
        _call(1, "run_command", {"command": "cat /ctf/chal.txt"}),
        _call(2, "run_command", {"command": _DECODE}),
        _submit_citing_the_decode,
    ]


def _submit_citing_the_decode(messages: Sequence[Message]) -> Completion:
    """Provenance must be a real seq the agent read off a tool result (D15 §1),
    so this step is a callable: it cites whatever seq the decode landed at."""
    seq = ""
    for message in reversed(messages):
        match = _OBSERVATION_SEQ.search(message.content)
        if match:
            seq = match.group(1)
            break
    return Completion(
        text="",
        tool_calls=(
            ToolCallRequest(
                id="call_3",
                name="submit_flag",
                arguments={
                    "flag": FLAG,
                    "how_found": "decoded the base64 blob in chal.txt",
                    "provenance": seq,
                },
            ),
        ),
        usage=Usage(input_tokens=140, output_tokens=18),
        stop_reason="tool_use",
    )


def _solve(store: Store, *, record: bool) -> tuple[str, object]:
    challenge = Challenge(
        name="rederive-01", category="misc", description="Decode this to find the flag."
    )
    model = resolve_model("claude-sonnet-5")
    run_id, writer = store.new_run(
        challenge_name=challenge.name,
        category=challenge.category,
        model=model.id,
        provider=model.provider,
        config_snapshot={
            "challenge": challenge.model_dump(mode="json"),
            "approval_policy": "gated",
        },
    )
    scripted: object = ScriptedProvider(_completions())
    if record:
        scripted = RecordingProvider(scripted, store.cassette_path(run_id))
    sandbox = _stub_sandbox()
    sandbox.start()
    runner = Runner(
        challenge=challenge,
        category=load_category("misc"),
        model=model,
        provider=scripted,  # type: ignore[arg-type]
        sandbox=sandbox,
        writer=writer,
        reviewer=lambda request: ReviewVerdict(True, "derived from the challenge data"),
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
    return run_id, outcome


def test_the_judges_rederivation_is_recorded_in_the_trace(runectl_home: Path) -> None:
    """The trace was incomplete without this: a command ran in the sandbox and
    left no record. That is the defect; the replay desync was its symptom."""
    store = Store()
    run_id, outcome = _solve(store, record=False)
    assert outcome.outcome == "solved"

    events = list(TraceReader(store.trace_path(run_id), store.artifacts_dir(run_id)))
    rederivations = [e.payload() for e in events if e.type == "flag.rederived"]
    assert len(rederivations) == 1, "the judge re-derived but the trace does not say so"
    payload = rederivations[0]
    assert isinstance(payload, FlagRederived)
    assert payload.matched is True
    assert payload.errored is False
    assert FLAG in payload.stdout
    assert payload.command == _DECODE


def test_rederivation_takes_its_place_in_the_replay_queue(runectl_home: Path) -> None:
    """The queue is positional, so ordering is the whole point: the
    re-derivation must sit *after* the tool call it re-runs, not be absent."""
    store = Store()
    run_id, _ = _solve(store, record=False)
    trace, artifacts = store.trace_path(run_id), store.artifacts_dir(run_id)

    kinds = [
        "rederived" if isinstance(e.payload(), FlagRederived) else "tool"
        for e in TraceReader(trace, artifacts)
        if isinstance(e.payload(), FlagRederived)
        or (
            isinstance(e.payload(), ToolResultEvent)
            and getattr(e.payload(), "tool", "") in {"run_command", "run_gdb", "search_flag"}
        )
    ]
    assert kinds[-1] == "rederived", "the re-derivation must be last, after the cited call"
    assert kinds.count("rederived") == 1

    queue = exec_results_from_trace(trace, artifacts)
    assert len(queue) == len(kinds)
    # The final queue entry is the re-derivation, and it carries the flag —
    # which is what lets a replayed judge reach the same verdict.
    assert FLAG in queue[-1].stdout


def test_replay_reproduces_the_outcome_not_just_the_sequence(runectl_home: Path) -> None:
    """The regression gate. Before the fix this run replayed as `candidate`
    while `--check` still reported OK, because only the sequence was compared."""
    store = Store()
    run_id, outcome = _solve(store, record=True)
    assert outcome.outcome == "solved"

    env = {**os.environ, "RUNECTL_HOME": str(runectl_home)}
    replay = subprocess.run(
        [sys.executable, "-m", "runectl", "replay", run_id, "--check"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert replay.returncode == 0, replay.stderr
    assert "OK:" in replay.stdout

    replay_run_id = replay.stdout.splitlines()[0].strip()
    original = Store().read_manifest(run_id)
    replayed = Store().read_manifest(replay_run_id)
    assert replayed.outcome == original.outcome == "solved"
    assert replayed.flag == original.flag == FLAG
    assert replayed.steps_used == original.steps_used


@pytest.mark.parametrize("errored", [False, True])
def test_rederivation_event_survives_a_write_read_round_trip(
    runectl_home: Path, errored: bool
) -> None:
    """Registration in `_ALL_PAYLOADS` is the step whose omission is silent:
    `TraceReader`'s torn-tail tolerance swallows the ValidationError, so an
    unregistered payload simply vanishes. It ate every `llm.thinking` event
    once already (M9)."""
    store = Store()
    run_id, writer = store.new_run(
        challenge_name="c", category="misc", model="claude-sonnet-5",
        provider="anthropic", config_snapshot={},
    )
    writer.emit(
        FlagRederived(
            step=1, source_seq=7, command="echo hi", matched=not errored,
            stdout="" if errored else FLAG, stderr="boom" if errored else "",
            exit_code=-1 if errored else 0, duration_s=0.1, errored=errored,
        )
    )
    writer.close()

    events = list(TraceReader(store.trace_path(run_id), store.artifacts_dir(run_id)))
    assert [e.type for e in events] == ["flag.rederived"]
    payload = events[0].payload()
    assert isinstance(payload, FlagRederived)
    assert payload.errored is errored
    assert payload.source_seq == 7

    # An errored re-derivation still occupies a queue slot, or the cursor
    # desyncs exactly as it did before the fix.
    queue = exec_results_from_trace(store.trace_path(run_id), store.artifacts_dir(run_id))
    assert len(queue) == 1
    assert queue[0].ok is not errored
