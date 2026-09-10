"""Derived SQLite index over the run store (D3, plan §1.5).

The database is never authoritative — ``runectl index rebuild`` regenerates it
purely from ``runs/`` on disk. If ``index.db`` were deleted right now, nothing
about a run's replayability would be lost.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from runectl.trace.reader import TraceReader
from runectl.trace.store import Store

_SCHEMA = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    challenge_name TEXT,
    category TEXT,
    model TEXT,
    provider TEXT,
    outcome TEXT,
    exit_code INTEGER,
    cost_usd REAL,
    steps_used INTEGER,
    started_at REAL,
    finished_at REAL,
    flag TEXT,
    progress_steps INTEGER,
    blocked_steps INTEGER,
    approved_at REAL,
    thinking_level TEXT
);
CREATE TABLE events_summary (
    run_id TEXT,
    type TEXT,
    count INTEGER,
    PRIMARY KEY (run_id, type)
);
"""


class IndexDB:
    def __init__(self, path: Path) -> None:
        self._path = path

    def rebuild(self, store: Store) -> int:
        """Drop and regenerate every table from ``store``. Returns the run count."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        try:
            conn.executescript("DROP TABLE IF EXISTS runs; DROP TABLE IF EXISTS events_summary;")
            conn.executescript(_SCHEMA)
            count = 0
            for run_id in store.list_run_ids():
                try:
                    manifest = store.read_manifest(run_id)
                except FileNotFoundError:
                    continue
                conn.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        manifest.run_id,
                        manifest.challenge_name,
                        manifest.category,
                        manifest.model,
                        manifest.provider,
                        manifest.outcome,
                        manifest.exit_code,
                        manifest.cost_usd,
                        manifest.steps_used,
                        manifest.started_at,
                        manifest.finished_at,
                        manifest.flag,
                        manifest.progress_steps,
                        manifest.blocked_steps,
                        manifest.approved_at,
                        manifest.thinking_level,
                    ),
                )
                type_counts: dict[str, int] = {}
                for event in TraceReader(store.trace_path(run_id), store.artifacts_dir(run_id)):
                    type_counts[event.type] = type_counts.get(event.type, 0) + 1
                for event_type, event_count in type_counts.items():
                    conn.execute(
                        "INSERT INTO events_summary VALUES (?,?,?)",
                        (run_id, event_type, event_count),
                    )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()

    def list_runs(self) -> list[dict[str, Any]]:
        """Empty, not an error, if the index has never been built (D3: the
        index is derived and never authoritative — asking a cross-run
        question before the first `runectl index rebuild` is a legitimate
        state, not a bug)."""
        if not self._path.exists():
            return []
        conn = sqlite3.connect(self._path)
        try:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC").fetchall()
            except sqlite3.OperationalError:
                return []
            return [dict(row) for row in rows]
        finally:
            conn.close()
