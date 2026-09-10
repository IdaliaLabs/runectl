"""Read-only data access for the TUI: runs, traces, pending flags.

Everything here reads through the same `Store`/`TraceReader`/`IndexDB` any
other `cli/` command uses (`runs_cmd.py`, `flag_cmd.py`) — no engine coupling,
no new storage format. The TUI never mutates a run directly; approving a flag
or launching a run goes through a subprocess (`runner_proc.py`,
`actions.py`), exactly like a human typing the equivalent command would.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runectl.cli.flag_cmd import PendingCandidate, pending_candidates
from runectl.trace.events import Event
from runectl.trace.index import IndexDB
from runectl.trace.reader import TraceReader
from runectl.trace.store import RunManifest, Store


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    challenge_name: str
    category: str
    model: str
    outcome: str | None
    cost_usd: float
    steps_used: int
    thinking_level: str


def list_runs(store: Store, *, limit: int = 200) -> list[RunSummary]:
    """Via the derived SQLite index — fast, but only as fresh as the last
    `runectl index rebuild`. This is what `runectl runs list` uses."""
    rows: list[dict[str, Any]] = IndexDB(store.home / "index.db").list_runs()
    return [
        RunSummary(
            run_id=row["run_id"],
            challenge_name=row.get("challenge_name") or "",
            category=row.get("category") or "",
            model=row.get("model") or "",
            outcome=row.get("outcome"),
            cost_usd=row.get("cost_usd") or 0.0,
            steps_used=row.get("steps_used") or 0,
            thinking_level=row.get("thinking_level") or "off",
        )
        for row in rows[:limit]
    ]


def list_runs_fresh(store: Store, *, limit: int = 100) -> list[RunSummary]:
    """Straight from `runs/` on disk (D3: the index is derived, never
    authoritative) — used by the TUI so a run it just launched shows up
    immediately, without needing `index rebuild` first. Bounded to the most
    recent `limit` runs since it reads one manifest per run; fine at the
    personal, single-machine scale this tool is built for (see D2's
    "assumptions"), not something to reach for over hundreds of thousands of
    runs.
    """
    run_ids = sorted(store.list_run_ids(), reverse=True)[:limit]
    summaries: list[RunSummary] = []
    for run_id in run_ids:
        manifest = read_manifest(store, run_id)
        if manifest is None:
            continue
        summaries.append(
            RunSummary(
                run_id=manifest.run_id,
                challenge_name=manifest.challenge_name,
                category=manifest.category,
                model=manifest.model,
                outcome=manifest.outcome,
                cost_usd=manifest.cost_usd,
                steps_used=manifest.steps_used,
                thinking_level=manifest.thinking_level,
            )
        )
    return summaries


def read_manifest(store: Store, run_id: str) -> RunManifest | None:
    try:
        return store.read_manifest(run_id)
    except FileNotFoundError:
        return None


def read_events(store: Store, run_id: str) -> list[Event]:
    return list(TraceReader(store.trace_path(run_id), store.artifacts_dir(run_id)))


def read_pending(store: Store, run_id: str) -> list[PendingCandidate]:
    return pending_candidates(read_events(store, run_id))
