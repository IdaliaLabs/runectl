"""The M4 gate (plan "Milestones"): the CLI solves one easy challenge end to
end, the trace survives a restart, and `runectl replay --check` reproduces the
tool-call sequence at zero spend.

Uses StubSandbox + ScriptedProvider — the zero-daemon, zero-API-spend path
`POSTMORTEM.md` §2 (testability) exists for. Live Docker/API-key verification
is out of scope here; see the M4 handoff report.
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
from runectl.providers.replay import RecordingProvider
from runectl.providers.scripted import ScriptedProvider, Step
from runectl.sandbox.stub import StubSandbox, ok
from runectl.trace.store import Store

FLAG = "flag{sk3l3t0n_w4lk}"
ENCODED = "ZmxhZ3tzazNsM3Qwbl93NGxrfQ=="  # base64 of FLAG
SEARCH_COMMAND = "grep -rnoIE 'flag\\{' /ctf/ 2>/dev/null | head -200"
_OBSERVATION_SEQ = re.compile(r"\[observation seq=(\d+)\]")


def _cite_last_observation(messages: Sequence[Message]) -> str:
    """The seq the agent would read off the most recent tool result (D15 §1)."""
    for message in reversed(messages):
        match = _OBSERVATION_SEQ.search(message.content)
        if match:
            return match.group(1)
    return ""


def _submit_citing_evidence(messages: Sequence[Message]) -> Completion:
    return Completion(
        text="",
        tool_calls=(
            ToolCallRequest(
                id="call_3",
                name="submit_flag",
                arguments={
                    "flag": FLAG,
                    "how_found": "decoded the base64 blob from chal.txt, then confirmed it on disk",
                    "provenance": _cite_last_observation(messages),
                },
            ),
        ),
        usage=Usage(input_tokens=140, output_tokens=18),
        stop_reason="tool_use",
    )


def _scripted_completions() -> list[Step]:
    """Two independent routes to the same string, then a cited submission.

    That shape is not padding: under `gated` (D11) a candidate is auto-finalized
    only if two *different* tool calls with *different* output fingerprints
    produced it (D15 §4), so a one-command solve is a `pending` candidate by
    design. This is the corroborated path.
    """
    return [
        Completion(
            text="",
            tool_calls=(
                ToolCallRequest(
                    id="call_1",
                    name="run_command",
                    arguments={
                        "command": f"echo {ENCODED} | base64 -d",
                        "reasoning": "decode the base64 blob found in triage",
                    },
                ),
            ),
            usage=Usage(input_tokens=120, output_tokens=24),
            stop_reason="tool_use",
        ),
        Completion(
            text="",
            tool_calls=(
                ToolCallRequest(
                    id="call_2",
                    name="search_flag",
                    arguments={"flag_pattern": "flag\\{"},
                ),
            ),
            usage=Usage(input_tokens=130, output_tokens=20),
            stop_reason="tool_use",
        ),
        _submit_citing_evidence,
    ]


def _stub_sandbox() -> StubSandbox:
    return StubSandbox(
        script={
            "ls -la /ctf/": ok("total 8\n-rw-r--r-- 1 ctf ctf 32 Jan  1 00:00 chal.txt"),
            "file /ctf/* 2>/dev/null": ok("/ctf/chal.txt: ASCII text"),
            "strings -a -n 8 /ctf/* 2>/dev/null | head -60": ok(ENCODED),
            f"echo {ENCODED} | base64 -d": ok(FLAG),
            SEARCH_COMMAND: ok(f"/ctf/solution.txt:1:{FLAG}"),
        }
    )


@pytest.fixture
def runectl_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "runectl-home"
    monkeypatch.setenv("RUNECTL_HOME", str(home))
    return home


def test_walking_skeleton_solves_end_to_end_and_replays(runectl_home: Path) -> None:
    store = Store()
    challenge = Challenge(name="easy-01", category="misc", description="Decode this to find the flag.")
    category = load_category("misc")
    model = resolve_model("claude-sonnet-5")

    run_id, writer = store.new_run(
        challenge_name=challenge.name,
        category=challenge.category,
        model=model.id,
        provider=model.provider,
        config_snapshot={"challenge": challenge.model_dump(mode="json"), "approval_policy": "gated"},
    )
    provider = RecordingProvider(ScriptedProvider(_scripted_completions()), store.cassette_path(run_id))
    sandbox = _stub_sandbox()
    sandbox.start()
    runner = Runner(
        challenge=challenge, category=category, model=model, provider=provider, sandbox=sandbox,
        writer=writer,
        # D15 §2's disconfirmation pass, scripted: a live run sends this to the
        # cheap utility model, and no test may reach an API.
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

    assert outcome.outcome == "solved"
    assert outcome.exit_code == 0
    assert outcome.flag == FLAG

    # "Trace survives a restart": re-open the store fresh (a new Store instance,
    # no in-memory state carried over) and confirm the manifest is durable.
    reopened = Store()
    manifest = reopened.read_manifest(run_id)
    assert manifest.outcome == "solved"
    assert manifest.flag == FLAG
    # D16: the progress ratio is the primary metric, so it has to survive into
    # run.json — not live only in the run.finished event.
    assert manifest.steps_used == outcome.steps_used
    assert manifest.progress_steps == outcome.progress_steps == 2
    assert manifest.blocked_steps == outcome.blocked_steps == 0

    env = {**os.environ, "RUNECTL_HOME": str(runectl_home)}

    trace_show = subprocess.run(
        [sys.executable, "-m", "runectl", "trace", "show", run_id, "--format", "jsonl"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert trace_show.returncode == 0, trace_show.stderr
    lines = [json.loads(line) for line in trace_show.stdout.splitlines() if line.strip()]
    assert any(line["type"] == "run.finished" for line in lines)
    assert any(line["type"] == "flag.decision" for line in lines)

    replay = subprocess.run(
        [sys.executable, "-m", "runectl", "replay", run_id, "--check"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert replay.returncode == 0, replay.stderr
    assert "OK:" in replay.stdout
