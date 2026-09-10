"""Spawn `runectl run` as a subprocess and stream its NDJSON events (Phase 4).

This is the one idea the whole TUI is built on: **the TUI never runs the
agent loop in-process.** It spawns `python -m runectl run --output jsonl ...`
— the exact non-interactive command a human or another driving agent would
type — and reads the same stdout-NDJSON stream D4 already guarantees for
that purpose. That keeps `loop/runner.py` a single-process synchronous loop
(no threading/async was added there to make this work), keeps the run itself
fully non-interactive (D4's "nothing can block on a human" survives
literally, because interactivity lives in a second process, not inside the
run), and means several runs watched at once are just several subprocesses —
concurrency without touching the engine. See the D13 amendment in
`docs/ARCHITECTURE.md` for the full argument.

Per `docs/CLI.md`'s output contract: every line of stdout except the last is
one JSON-encoded trace event; the last line is the bare run id. A line this
module can't parse as an `Event` is assumed to be that trailing run id (or
stray noise) rather than an error — the caller gets it back as `run_id` on
the returned `RunHandle`.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from runectl.trace.events import Event

_STDERR_TAIL = 200  # lines kept for diagnostics if the process exits before any event


@dataclass
class RunHandle:
    """What's left once a launched `runectl run` subprocess has exited."""

    argv: list[str]
    exit_code: int
    run_id: str | None
    stderr_tail: list[str] = field(default_factory=list)


_DEFAULT_LAUNCH_ARGV: tuple[str, ...] = (sys.executable, "-m", "runectl")


async def run_streaming(
    argv: list[str],
    on_event: Callable[[Event], None],
    *,
    launch_argv: tuple[str, ...] = _DEFAULT_LAUNCH_ARGV,
) -> RunHandle:
    """Spawn `runectl <argv>`, calling `on_event` for each parsed trace event.

    `argv` is everything after `runectl` itself, e.g.
    ``["run", "--model", "claude-sonnet-5", "--challenge", "chal.toml",
    "--output", "jsonl"]`` — the caller is responsible for including
    ``--output jsonl`` (without it the process would render to stderr as
    human text instead of streaming events, which this reader can't parse).
    Runs via ``python -m runectl`` (rather than the ``runectl`` console
    script, so it works regardless of what's on PATH) unless `launch_argv`
    overrides it — the one legitimate reason to is a test exercising this
    subprocess/parsing path against a stub script instead of the real engine
    (CONTRIBUTING.md: no test may need a daemon or a key).
    """
    process = await asyncio.create_subprocess_exec(
        *launch_argv,
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    stderr_tail: list[str] = []

    async def _drain_stderr() -> None:
        async for raw in process.stderr:  # type: ignore[union-attr]
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            stderr_tail.append(line)
            if len(stderr_tail) > _STDERR_TAIL:
                stderr_tail.pop(0)

    async def _drain_stdout() -> str | None:
        last_line: str | None = None
        async for raw in process.stdout:  # type: ignore[union-attr]
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            last_line = line
            try:
                event = Event.model_validate_json(line)
            except ValueError:
                # Not parseable as an event — the trailing run id line, most
                # likely. Kept as `last_line` regardless so it's available as
                # `run_id` below even if it's the *only* line the process ever
                # wrote (e.g. it failed before emitting a single event).
                continue
            on_event(event)
        return last_line

    stdout_task = asyncio.ensure_future(_drain_stdout())
    stderr_task = asyncio.ensure_future(_drain_stderr())
    last_line = await stdout_task
    await stderr_task
    exit_code = await process.wait()

    return RunHandle(argv=argv, exit_code=exit_code, run_id=last_line, stderr_tail=stderr_tail)
