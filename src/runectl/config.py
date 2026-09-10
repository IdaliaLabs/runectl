"""Paths and tunable defaults (D3, D5, D12).

Every constant here is a *starting* value carried from the predecessor tool's practice or
chosen for the skeleton, explicitly marked unmeasured (D16) — `runectl bench` (M8)
is what tunes them, not this file.
"""

from __future__ import annotations

import os
from pathlib import Path

# D3 — trace/store home. Overridable for tests and multi-instance use.
RUNECTL_HOME_ENV = "RUNECTL_HOME"
RUNECTL_CONFIG_ENV = "RUNECTL_CONFIG_HOME"


def runectl_home() -> Path:
    """~/.local/share/runectl, or $RUNECTL_HOME (XDG_DATA_HOME-relative)."""
    override = os.environ.get(RUNECTL_HOME_ENV)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "runectl"


def runectl_config_dir() -> Path:
    """~/.config/runectl, or $RUNECTL_CONFIG_HOME (XDG_CONFIG_HOME-relative)."""
    override = os.environ.get(RUNECTL_CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "runectl"


def keys_file_path() -> Path:
    return runectl_config_dir() / "keys.json"


# D3 — spill any event value over this to artifacts/, referenced by digest.
ARTIFACT_SPILL_THRESHOLD_BYTES = 8_000

# D12 — one consistent, configurable context limit. No second dead constant.
DEFAULT_TOOL_OUTPUT_LIMIT = 6_000

# D12 — history compaction trigger. A message-count stand-in for a real
# per-provider token budget; unmeasured, like every other number in this file.
DEFAULT_MAX_HISTORY_MESSAGES = 40

# D19 — default hard spend ceiling for one run, in USD. A run stops cleanly the
# moment the ledger crosses it. Chosen to be small enough that a runaway loop on
# a prepaid balance is an annoyance rather than a disaster; override per run with
# --max-cost, and set it to 0 to disable the ceiling entirely.
DEFAULT_MAX_COST_USD = 0.50

# D5 — retry with exponential backoff + jitter on 429/5xx/timeouts.
DEFAULT_RETRY_ATTEMPTS = 4

# D2 — sandbox resource posture, carried from the old system as a baseline.
SANDBOX_MEM_LIMIT = "2g"
SANDBOX_CPUS = 2.0
SANDBOX_LIVE_LOG_PATH = "/ctf/.agent_live.log"
SANDBOX_WORKDIR = "/ctf"

# The arena is pinned to x86-64 regardless of the host's own architecture.
# CTF challenge binaries are overwhelmingly x86-64 ELF; on an arm64 host an
# unpinned build produces an arm64 arena in which those binaries simply cannot
# execute, and the failure looks like a broken challenge rather than a broken
# arena. Docker emulates, which is slower but correct. Carried from the
# predecessor, which passed platform="linux/amd64" explicitly.
SANDBOX_PLATFORM = "linux/amd64"

# The agent writes and runs its own code in here. mem/cpu caps bound resource
# use but not process count, and a fork bomb in a challenge exploit script is a
# realistic accident. Docker's default is unlimited.
SANDBOX_PIDS_LIMIT = 512

# Docker defaults to TERM=dumb, which makes pwntools emit a curses warning on
# every import. That noise lands in tool output, gets summarized into context,
# and is billed as tokens on every pwn step. Set at run time rather than in the
# image so it also covers an arena loaded via `arena ensure --from-file`.
SANDBOX_ENV: dict[str, str] = {"TERM": "xterm-256color"}

# Container naming: predictable, so a human can attach to a live run
# (`docker exec -it runectl-<run_id> bash`) the way the predecessor allowed.
CONTAINER_NAME_PREFIX = "runectl-"
