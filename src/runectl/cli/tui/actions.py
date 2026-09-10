"""Mutating actions, all driven as subprocesses of the existing CLI commands.

Approving a flag through the TUI runs the literal `runectl flag approve`
command rather than re-implementing D11's approval bookkeeping — the two can
never drift apart, because there is only one implementation.
"""

from __future__ import annotations

import asyncio
import sys


async def approve_flag(run_id: str, flag: str) -> tuple[int, str]:
    """Runs `runectl flag approve <run_id> --flag <flag>`. Returns (exit_code, combined output)."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "runectl",
        "flag",
        "approve",
        run_id,
        "--flag",
        flag,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output = b""
    if process.stdout is not None:
        output = await process.stdout.read()
    exit_code = await process.wait()
    return exit_code, output.decode("utf-8", errors="replace").strip()
