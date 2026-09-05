"""Serves recorded ``ExecResult``s for ``runectl replay`` — no daemon, no side
effects (D2, plan §2.5).

Deliberately dumb: it plays back exec results strictly in the order they were
recorded, with no string-matching against the incoming command. Sequence
*fidelity* — did the replayed run actually issue the same tool calls in the
same order — is asserted by the caller (``runectl replay --check``) comparing
the two traces' ``tool.call`` events, not by this class.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from runectl.config import SANDBOX_WORKDIR
from runectl.errors import SandboxError
from runectl.sandbox.base import ExecResult
from runectl.trace.events import ToolResultEvent
from runectl.trace.reader import TraceReader

# Tools whose dispatch handler routes through Sandbox.exec (D7). write_file and
# submit_flag never touch exec(), so they're excluded here.
_EXEC_BACKED_TOOLS = frozenset({"run_command", "run_gdb", "search_flag"})


def exec_results_from_trace(trace_path: Path, artifacts_dir: Path) -> list[ExecResult]:
    """Extract, in recorded order, every ExecResult a replay must serve."""
    results: list[ExecResult] = []
    for event in TraceReader(trace_path, artifacts_dir):
        payload = event.payload()
        if isinstance(payload, ToolResultEvent) and payload.tool in _EXEC_BACKED_TOOLS:
            results.append(
                ExecResult(
                    ok=payload.ok,
                    stdout=payload.stdout,
                    stderr=payload.stderr,
                    exit_code=payload.exit_code,
                    duration_s=payload.duration_s,
                    timed_out=False,
                    truncated=payload.truncated,
                )
            )
    return results


class ReplaySandbox:
    def __init__(self, exec_results: Sequence[ExecResult]) -> None:
        self._queue = list(exec_results)
        self._cursor = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult:
        if self._cursor >= len(self._queue):
            raise SandboxError("ReplaySandbox: exec called past the end of the recorded trace")
        result = self._queue[self._cursor]
        self._cursor += 1
        return result

    def write_file(self, rel_path: str, content: bytes) -> None:
        pass

    def read_file(self, rel_path: str) -> bytes:
        raise SandboxError("ReplaySandbox does not support read_file")

    def put_inputs(self, files: Sequence[Path]) -> list[str]:
        return [f"{SANDBOX_WORKDIR}/{f.name}" for f in files]
