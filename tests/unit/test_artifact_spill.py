"""A spilled tool output must not break reading the trace back (D3).

Found live on 2026-09-13, on the first run whose tool output exceeded the 8KB
spill threshold. Two failures, one loud and one silent:

- `Event.payload()` raised `ValidationError`, because the writer had replaced a
  `str` field with `{"$artifact": digest, "bytes": n}`. That crashed the live
  human renderer mid-run.
- `TraceReader` caught that same error and treated the line as a torn tail, so
  it stopped and yielded only the events *before* the first large output — and
  said nothing. `trace show`, `replay` and the TUI all read through it. The
  trace is the product; silently dropping its back half is the worst thing this
  layer can do, and it looked exactly like correct behavior.

The reader had been constructed with an `artifacts_dir` since the beginning and
never once read from it.
"""

from __future__ import annotations

from pathlib import Path

from runectl.config import ARTIFACT_SPILL_THRESHOLD_BYTES
from runectl.trace.events import RunStarted, ToolCall, ToolResultEvent
from runectl.trace.reader import TraceReader
from runectl.trace.writer import TraceWriter

_BIG = "A" * (ARTIFACT_SPILL_THRESHOLD_BYTES + 5_000)

_STARTED = RunStarted(
    challenge_name="c", category="misc", model="claude-sonnet-5", provider="anthropic",
    approval_policy="gated", max_steps=5, network="none",
)


def _write(tmp_path: Path) -> tuple[Path, Path]:
    trace, artifacts = tmp_path / "trace.jsonl", tmp_path / "artifacts"
    with TraceWriter(run_id="r", path=trace, artifacts_dir=artifacts) as w:
        w.emit(_STARTED)
        w.emit(ToolResultEvent(
            step=1, tool="run_command", ok=True, kind="output",
            stdout=_BIG, stderr="", exit_code=0, duration_s=0.1, truncated=False,
        ))
        # The event that used to be unreachable: everything after the spill.
        w.emit(ToolCall(step=2, tool="run_command", arguments={"command": "echo done"}))
    return trace, artifacts


def test_the_reader_does_not_stop_at_a_spilled_event(tmp_path: Path) -> None:
    trace, artifacts = _write(tmp_path)
    events = list(TraceReader(trace, artifacts))
    assert [e.type for e in events] == ["run.started", "tool.result", "tool.call"], (
        "the reader stopped at the spilled event and silently dropped the rest"
    )


def test_the_reader_restores_the_spilled_content(tmp_path: Path) -> None:
    trace, artifacts = _write(tmp_path)
    result = [e for e in TraceReader(trace, artifacts) if e.type == "tool.result"][0]
    payload = result.payload()
    assert isinstance(payload, ToolResultEvent)
    assert payload.stdout == _BIG, "a resolved artifact must be byte-identical to what ran"


def test_payload_survives_an_unresolved_spill(tmp_path: Path) -> None:
    """The live render path gets the event at emit time, with no artifacts dir
    to resolve against. It must get a readable placeholder, not an exception."""
    seen = []
    trace, artifacts = tmp_path / "t.jsonl", tmp_path / "a"
    with TraceWriter(run_id="r", path=trace, artifacts_dir=artifacts, on_emit=seen.append) as w:
        w.emit(_STARTED)
        w.emit(ToolResultEvent(
            step=1, tool="run_command", ok=True, kind="output",
            stdout=_BIG, stderr="", exit_code=0, duration_s=0.1, truncated=False,
        ))

    payload = seen[-1].payload()  # used to raise ValidationError
    assert isinstance(payload, ToolResultEvent)
    assert payload.stdout.startswith("<") and "bytes in artifacts/" in payload.stdout
