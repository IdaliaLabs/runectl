"""Mutating actions, all driven as subprocesses of the existing CLI commands.

Approving a flag through the TUI runs the literal `runectl flag approve`
command rather than re-implementing D11's approval bookkeeping — the two can
never drift apart, because there is only one implementation.
"""

from __future__ import annotations

from runectl.cli.tui.proc import run_once


async def approve_flag(run_id: str, flag: str) -> tuple[int, str]:
    """Runs `runectl flag approve <run_id> --flag <flag>`. Returns (exit_code, combined output)."""
    return await run_once(["flag", "approve", run_id, "--flag", flag])
