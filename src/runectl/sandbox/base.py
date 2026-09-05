"""The sandbox boundary (D2, plan §2.1). Zero Docker imports here — the loop only
ever knows about this Protocol and :class:`ExecResult`."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class ExecResult(BaseModel):
    """Structured result of a command execution. Never a string-prefixed error."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    duration_s: float
    timed_out: bool
    truncated: bool = False


@runtime_checkable
class Sandbox(Protocol):
    def start(self) -> None: ...

    def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult: ...

    def write_file(self, rel_path: str, content: bytes) -> None: ...

    def read_file(self, rel_path: str) -> bytes: ...

    def put_inputs(self, files: Sequence[Path]) -> list[str]:
        """Copy files into the sandbox; return their verified in-sandbox paths."""
        ...

    def stop(self) -> None: ...


@contextmanager
def sandbox_session(sandbox: Sandbox) -> Iterator[Sandbox]:
    """Guarantee ``stop()`` runs even if the caller raises."""
    sandbox.start()
    try:
        yield sandbox
    finally:
        sandbox.stop()
