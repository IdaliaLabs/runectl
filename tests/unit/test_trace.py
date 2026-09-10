"""M1 gate (plan "Milestones"): 500-event round-trip; SIGKILL leaves a valid
prefix; `index rebuild` matches the store; secrets never land in trace.jsonl."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runectl.trace.events import ToolCall
from runectl.trace.index import IndexDB
from runectl.trace.reader import TraceReader
from runectl.trace.store import Store
from runectl.trace.writer import TraceWriter


def test_round_trip_500_events(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    artifacts_dir = tmp_path / "artifacts"
    writer = TraceWriter(trace_path, "run-1", artifacts_dir)
    for i in range(500):
        writer.emit(ToolCall(step=i, tool="run_command", arguments={"command": f"echo {i}"}))
    writer.close()

    events = list(TraceReader(trace_path, artifacts_dir))
    assert len(events) == 500
    assert [e.seq for e in events] == list(range(1, 501))
    assert all(e.type == "tool.call" for e in events)


def test_sigkill_leaves_valid_prefix(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    artifacts_dir = tmp_path / "artifacts"
    writer = TraceWriter(trace_path, "run-1", artifacts_dir)
    for i in range(5):
        writer.emit(ToolCall(step=i, tool="run_command", arguments={"command": f"echo {i}"}))
    writer.close()

    # Simulate a SIGKILL mid-write: an unflushed, unterminated final line.
    with trace_path.open("a", encoding="utf-8") as fh:
        fh.write('{"v":1,"run_id":"run-1","seq":6,"ty')

    events = list(TraceReader(trace_path, artifacts_dir))
    assert len(events) == 5


def test_secrets_are_redacted(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    artifacts_dir = tmp_path / "artifacts"
    writer = TraceWriter(trace_path, "run-1", artifacts_dir)
    writer.emit(
        ToolCall(
            step=1,
            tool="run_command",
            arguments={"command": "curl -H 'Authorization: Bearer sk-ant-abcdefghijklmnop'"},
        )
    )
    writer.close()
    raw = trace_path.read_text()
    assert "sk-ant-abcdefghijklmnop" not in raw
    assert "[REDACTED]" in raw


def test_large_value_spills_to_artifact(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    artifacts_dir = tmp_path / "artifacts"
    writer = TraceWriter(trace_path, "run-1", artifacts_dir)
    big = "x" * 9000
    writer.emit(ToolCall(step=1, tool="run_command", arguments={"command": big}))
    writer.close()
    raw = trace_path.read_text()
    assert big not in raw
    assert "$artifact" in raw
    assert any(artifacts_dir.iterdir())


def test_index_rebuild_matches_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNECTL_HOME", str(tmp_path))
    store = Store()
    run_id, writer = store.new_run(
        challenge_name="c1", category="misc", model="claude-sonnet-5",
        provider="anthropic", config_snapshot={},
    )
    writer.emit(ToolCall(step=1, tool="run_command", arguments={"command": "ls"}))
    writer.close()
    store.finish_run(run_id, outcome="solved", exit_code=0, flag="flag{x}", cost_usd=0.01, steps_used=1)

    index = IndexDB(store.home / "index.db")
    count = index.rebuild(store)
    assert count == 1
    rows = index.list_runs()
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["outcome"] == "solved"


def test_list_runs_is_empty_not_an_error_before_the_first_rebuild(tmp_path: Path) -> None:
    """D3: the index is derived, never authoritative — `runs list` before the
    first `index rebuild` is a legitimate empty state (`runectl runs list`),
    not a crash. Covers both a genuinely missing index.db and one that exists
    but was never given a schema (sqlite3.connect() alone creates an empty
    file without creating any table)."""
    missing = IndexDB(tmp_path / "does-not-exist.db")
    assert missing.list_runs() == []

    empty_path = tmp_path / "empty.db"
    sqlite3.connect(empty_path).close()  # touches the file, no schema
    assert IndexDB(empty_path).list_runs() == []
