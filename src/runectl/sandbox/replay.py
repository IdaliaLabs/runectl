"""Serves recorded ``ExecResult``s for ``runectl replay`` — no daemon, no side
effects (D2).

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
from runectl.trace.events import FlagRederived, ToolResultEvent
from runectl.trace.reader import TraceReader

# Tools whose dispatch handler routes through Sandbox.exec (D7). write_file and
# submit_flag never touch exec(), so they're excluded here.
_EXEC_BACKED_TOOLS = frozenset({"run_command", "run_gdb", "search_flag"})


def exec_results_from_trace(trace_path: Path, artifacts_dir: Path) -> list[ExecResult]:
    """Extract, in recorded order, every ExecResult a replay must serve.

    Two kinds of event put a command into the sandbox, and both belong in this
    queue: the agent's own tool calls (``tool.result``) and the D15 judge's
    re-derivation of a cited command (``flag.rederived``), which calls
    ``Sandbox.exec`` directly rather than through the dispatcher.

    Missing the second kind is not a missing entry — it is a *misaligned* one.
    The queue is positional, so an unrecorded exec silently hands the judge the
    next tool call's output and shifts everything after it, which is what made
    a replayed run reproduce its tool-call sequence while quietly downgrading
    its outcome from `solved` to `candidate`. Fixed 2026-09-10 by recording the
    re-derivation (D3 amended, 19 -> 20 events).
    """
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
                    truncated=payload.truncated,
                )
            )
        elif isinstance(payload, FlagRederived):
            # An errored re-derivation (exec raised) is served as a plain failed
            # result rather than a re-raised exception: a different mechanism
            # reaching the judge's same "could not re-derive" verdict, which is
            # what matters, and it keeps the cursor aligned either way.
            results.append(
                ExecResult(
                    ok=not payload.errored and payload.exit_code == 0,
                    stdout=payload.stdout,
                    stderr=payload.stderr,
                    exit_code=payload.exit_code,
                    duration_s=payload.duration_s,
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

    def put_inputs(self, files: Sequence[Path]) -> list[str]:
        return [f"{SANDBOX_WORKDIR}/{f.name}" for f in files]
