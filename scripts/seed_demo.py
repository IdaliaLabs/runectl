"""Seed a fixture run for `make demo` (Phase 5).

Builds one complete, solved run — with realistic thinking text — using
`StubSandbox` + `ScriptedProvider`, the same zero-daemon, zero-spend path
`tests/integration/test_walking_skeleton.py` uses to verify the loop end to
end (`REBUILD_NOTES.md` requirement 4). No Docker daemon, no API key, and no
real provider call happens anywhere in this script.

Prints the new run id to stdout and nothing else, so a shell wrapper (the
`demo` Makefile target) can capture it directly. Reads `$RUNECTL_HOME` like
any other `runectl` invocation — the Makefile points it at a scratch
directory so this never touches a real run store.
"""

from __future__ import annotations

from collections.abc import Sequence

from runectl.categories.loader import load as load_category
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.base import Completion, Message, ToolCallRequest, Usage
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.scripted import ScriptedProvider, Step
from runectl.sandbox.stub import StubSandbox, ok
from runectl.trace.store import Store

FLAG = "flag{sk3l3t0n_w4lk}"
ENCODED = "ZmxhZ3tzazNsM3Qwbl93NGxrfQ=="  # base64 of FLAG
SEARCH_COMMAND = "grep -rnoIE 'flag\\{' /ctf/ 2>/dev/null | head -200"


def _submit(messages: Sequence[Message]) -> Completion:
    seq = 0
    for message in reversed(messages):
        if "[observation seq=" in message.content:
            seq = int(message.content.split("seq=", 1)[1].split("]", 1)[0])
            break
    return Completion(
        text="",
        thinking_text=(
            "Both the base64 decode and the grep search independently produced the "
            "same string, and I can point to the exact observation where it appeared "
            "verbatim — that's the provenance the judge needs, not just a guess."
        ),
        tool_calls=(
            ToolCallRequest(
                id="call_3",
                name="submit_flag",
                arguments={
                    "flag": FLAG,
                    "how_found": "decoded the base64 blob from chal.txt, then confirmed it on disk",
                    "provenance": str(seq),
                },
            ),
        ),
        usage=Usage(input_tokens=140, output_tokens=18),
        stop_reason="tool_use",
    )


def _completions() -> list[Step]:
    return [
        Completion(
            text="",
            thinking_text=(
                "The strings output shows what looks like a base64 blob sitting alone "
                "in chal.txt. Base64 is the cheapest hypothesis to try before anything "
                "more elaborate — decoding it costs one command."
            ),
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
            thinking_text=(
                "The decode produced a flag-shaped string. Before I submit it I want a "
                "second, independent way to see the same string — a plain search of the "
                "filesystem — so the judge sees it wasn't a one-off fluke of my decoding."
            ),
            tool_calls=(
                ToolCallRequest(id="call_2", name="search_flag", arguments={"flag_pattern": "flag\\{"}),
            ),
            usage=Usage(input_tokens=130, output_tokens=20),
            stop_reason="tool_use",
        ),
        _submit,
    ]


def _sandbox() -> StubSandbox:
    return StubSandbox(
        script={
            "ls -la /ctf/": ok("total 8\n-rw-r--r-- 1 ctf ctf 32 Jan  1 00:00 chal.txt"),
            "file /ctf/* 2>/dev/null": ok("/ctf/chal.txt: ASCII text"),
            "strings -a -n 8 /ctf/* 2>/dev/null | head -60": ok(ENCODED),
            f"echo {ENCODED} | base64 -d": ok(FLAG),
            SEARCH_COMMAND: ok(f"/ctf/solution.txt:1:{FLAG}"),
        }
    )


def main() -> None:
    store = Store()
    challenge = Challenge(
        name="easy-01", category="misc", description="Decode this to find the flag."
    )
    category = load_category("misc")
    model = resolve_model("claude-sonnet-5")

    run_id, writer = store.new_run(
        challenge_name=challenge.name,
        category=challenge.category,
        model=model.id,
        provider=model.provider,
        config_snapshot={
            "challenge": challenge.model_dump(mode="json"),
            "approval_policy": "gated",
            "thinking_requested": "high",
        },
    )
    provider = ScriptedProvider(_completions())
    sandbox = _sandbox()
    sandbox.start()
    runner = Runner(
        challenge=challenge, category=category, model=model, provider=provider,
        sandbox=sandbox, writer=writer, thinking="high",
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
        thinking_level=outcome.thinking_level,
    )
    print(run_id)


if __name__ == "__main__":
    main()
