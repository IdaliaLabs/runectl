"""Structured tool results — never a string-prefixed error (D7, plan §4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    kind: Literal["output", "error", "blocked"]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_s: float = 0.0
    truncated: bool = False
    # The shell command this tool actually ran, when it ran one. `run_gdb` and
    # `search_flag` build theirs here in dispatch, so the judge cannot
    # reconstruct them from the tool arguments alone — and D15's verification
    # pass needs to re-run exactly what ran.
    shell_command: str = ""
