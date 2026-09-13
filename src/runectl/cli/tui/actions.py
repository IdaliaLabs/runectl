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


async def rebuild_index() -> tuple[int, str]:
    """Runs `runectl index rebuild`.

    The TUI's own run list reads `runs/` from disk and never consults the
    SQLite index (D3: the index is derived), so this is not for the TUI's
    benefit — it is here because `runectl runs list` *does* read the index, and
    a stale one silently omits recent runs. Observed 2026-09-13: the CLI listed
    nothing newer than three days prior while the TUI showed the same runs
    fine. The fix belongs on whichever surface the user is already looking at.
    """
    return await run_once(["index", "rebuild"])


async def replay_check(run_id: str) -> tuple[int, str]:
    """Runs `runectl replay <run_id> --check` — zero spend, no Docker daemon.

    Distinct from the TUI's `--replay` playback, which re-renders a recorded
    trace for a human. This re-executes the run from its cassette and asserts
    the result matches, which is the thing that would catch a regression.
    """
    return await run_once(["replay", run_id, "--check"])
