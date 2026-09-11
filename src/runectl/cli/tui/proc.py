"""Shared subprocess helpers every mutating TUI screen goes through.

The one rule the whole TUI is built on (see `app.py`'s module docstring, and
D13): it never reimplements CLI behavior, it only composes and runs it. Two
shapes cover every mutating action:

- `run_once` — capture output, for a quick command whose result the TUI shows
  itself (`keys set`, `config set`). Async; runs alongside the event loop.
- `run_foreground` — inherit the real terminal, for a command whose natural
  output is a live human-facing stream (`arena build`, `bench run`,
  `docker exec -it`). Sync; call it only inside `with app.suspend():`, which
  hands the terminal to the child and restores the TUI when the block exits.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

_RUNECTL_ARGV: tuple[str, ...] = (sys.executable, "-m", "runectl")


async def run_once(
    argv: list[str], *, launch_argv: tuple[str, ...] = _RUNECTL_ARGV
) -> tuple[int, str]:
    """Runs `runectl <argv>`. Returns (exit_code, combined stdout+stderr).

    `launch_argv` overrides the `python -m runectl` prefix — the one
    legitimate reason to is a test pointing this at a stub script instead of
    the real CLI (CONTRIBUTING.md: no test may need a daemon or a key), the
    same seam `runner_proc.run_streaming` already exposes.
    """
    process = await asyncio.create_subprocess_exec(
        *launch_argv,
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output = b""
    if process.stdout is not None:
        output = await process.stdout.read()
    exit_code = await process.wait()
    return exit_code, output.decode("utf-8", errors="replace").strip()


def run_foreground(argv: list[str]) -> int:
    """Runs `argv` verbatim (not prefixed with `runectl` — the caller passes
    a full command, e.g. `["docker", "exec", "-it", ...]` or
    `[sys.executable, "-m", "runectl", "arena", "build"]`) with inherited
    stdio. Blocking — only call this inside `with app.suspend():`."""
    return subprocess.run(argv, check=False).returncode
