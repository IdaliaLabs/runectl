"""D11 end to end: a doubted find is a candidate, and a human closes it.

The run stops at exit code 2 with the candidate in the trace rather than
claiming a solve, `runectl flag list` shows what was held and why, and
`runectl flag approve` finalizes it — recording that a person, not the judge,
made that call. All at zero spend: StubSandbox + ScriptedProvider.

What holds the candidate here is the D15 §2 disconfirmation review saying it
doubts the flag. Before 2026-09-08 this test held on corroboration instead; the
first live bench showed that rule holding four *correct* flags, so the review is
what the gate hangs on now (bench/results/README.md).
"""

from __future__ import annotations

import json
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
from runectl.providers.scripted import ScriptedProvider, Step
from runectl.sandbox.stub import StubSandbox, ok
from runectl.trace.store import Store

FLAG = "flag{h3ld_f0r_4ppr0v4l}"
_OBSERVATION_SEQ = re.compile(r"\[observation seq=(\d+)\]")


def _submit(messages: Sequence[Message]) -> Completion:
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
                id="call_2",
                name="submit_flag",
                arguments={"flag": FLAG, "how_found": "read it out of chal.txt", "provenance": seq},
            ),
        ),
        usage=Usage(input_tokens=100, output_tokens=12),
        stop_reason="tool_use",
    )


def _script() -> list[Step]:
    return [
        Completion(
            text="",
            tool_calls=(
                ToolCallRequest(
                    id="call_1",
                    name="run_command",
                    arguments={"command": "cat /ctf/chal.txt", "reasoning": "read the file"},
                ),
            ),
            usage=Usage(input_tokens=100, output_tokens=12),
            stop_reason="tool_use",
        ),
        _submit,
    ]


@pytest.fixture
def runectl_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "runectl-home"
    monkeypatch.setenv("RUNECTL_HOME", str(home))
    return home


def _run(store: Store) -> str:
    challenge = Challenge(name="held-01", category="misc", description="Find the flag.")
    model = resolve_model("claude-sonnet-5")
    run_id, writer = store.new_run(
        challenge_name=challenge.name,
        category=challenge.category,
        model=model.id,
        provider=model.provider,
        config_snapshot={"challenge": challenge.model_dump(mode="json"), "approval_policy": "gated"},
    )
    sandbox = StubSandbox(script={"cat /ctf/chal.txt": ok(FLAG)})
    sandbox.start()
    runner = Runner(
        challenge=challenge,
        category=load_category("misc"),
        model=model,
        provider=ScriptedProvider(_script()),
        sandbox=sandbox,
        writer=writer,
        reviewer=lambda request: ReviewVerdict(False, "no derivation shown, just a file read"),
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
    assert outcome.outcome == "candidate"
    assert outcome.exit_code == 2
    assert outcome.flag == FLAG
    return run_id


def test_a_doubted_flag_is_held_then_approved(runectl_home: Path) -> None:
    store = Store()
    run_id = _run(store)

    manifest = Store().read_manifest(run_id)
    assert manifest.outcome == "candidate"
    assert manifest.exit_code == 2
    assert manifest.approved_at is None

    env = {**os.environ, "RUNECTL_HOME": str(runectl_home)}
    listed = subprocess.run(
        [sys.executable, "-m", "runectl", "flag", "list", run_id],
        capture_output=True, text=True, env=env,
    )
    assert listed.returncode == 0, listed.stderr
    held = [json.loads(line) for line in listed.stdout.splitlines() if line.strip()]
    assert len(held) == 1
    assert held[0]["flag"] == FLAG
    # The reason has to be actionable: it carries the reviewer's own words.
    assert "no derivation shown" in held[0]["held_because"]

    approved = subprocess.run(
        [sys.executable, "-m", "runectl", "flag", "approve", run_id],
        capture_output=True, text=True, env=env,
    )
    assert approved.returncode == 0, approved.stderr
    assert FLAG in approved.stdout

    after = Store().read_manifest(run_id)
    assert after.outcome == "solved"
    assert after.exit_code == 0
    assert after.flag == FLAG
    # An approved solve stays distinguishable from one the judge cleared alone.
    assert after.approved_at is not None
    assert after.finished_at == manifest.finished_at

    # D3: approval appends to the trace, it does not rewrite the judge's decision.
    trace = (runectl_home / "runs" / run_id / "trace.jsonl").read_text().splitlines()
    decisions = [json.loads(line) for line in trace if '"flag.decision"' in line]
    assert [d["data"]["decision"] for d in decisions] == ["pending", "finalized"]
    assert [d["seq"] for d in decisions] == sorted(d["seq"] for d in decisions)

    # Approving twice is not a way to invent a second solve.
    again = subprocess.run(
        [sys.executable, "-m", "runectl", "flag", "approve", run_id],
        capture_output=True, text=True, env=env,
    )
    assert again.returncode == 6
    assert "no pending flag candidates" in again.stderr
