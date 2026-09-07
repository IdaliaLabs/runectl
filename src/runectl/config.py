"""Paths and tunable defaults (D3, D5, D12).

Every constant here is a *starting* value carried from `PROMPT_ARCHIVE.md` §6 or
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


def runs_dir() -> Path:
    return runectl_home() / "runs"


def index_db_path() -> Path:
    return runectl_home() / "index.db"


def keys_file_path() -> Path:
    return runectl_config_dir() / "keys.json"


# D3 — spill any event value over this to artifacts/, referenced by digest.
ARTIFACT_SPILL_THRESHOLD_BYTES = 8_000

# D12 — one consistent, configurable context limit. No second dead constant.
DEFAULT_TOOL_OUTPUT_LIMIT = 6_000

# D12 — history compaction trigger. A message-count stand-in for a real
# per-provider token budget; unmeasured, like every other number in this file.
DEFAULT_MAX_HISTORY_MESSAGES = 40

# D5 — retry with exponential backoff + jitter on 429/5xx/timeouts.
DEFAULT_RETRY_ATTEMPTS = 4

# D2 — sandbox resource posture, carried from the old system as a baseline.
SANDBOX_MEM_LIMIT = "2g"
SANDBOX_CPUS = 2.0
SANDBOX_LIVE_LOG_PATH = "/ctf/.agent_live.log"
SANDBOX_WORKDIR = "/ctf"

# PROMPT_ARCHIVE.md §6 — [STALE], unmeasured starting values. Do not treat as tuned.
DEFAULT_STEP_LIMITS: dict[str, int] = {
    "pwn": 120,
    "rev": 100,
    "web": 80,
    "crypto": 80,
    "forensics": 70,
    "misc": 60,
    "osint": 50,
    "network": 60,
}
