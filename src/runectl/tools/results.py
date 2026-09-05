"""Structured tool results — never a string-prefixed error (D7, plan §4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    kind: Literal["output", "error", "blocked"]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_s: float = 0.0
    truncated: bool = False
    artifact_ref: str | None = None
    meta: dict[str, str] = Field(default_factory=dict)
