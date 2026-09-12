"""Durable run storage: files are authoritative (D3).

``~/.local/share/runectl/runs/<run_id>/{run.json,trace.jsonl,artifacts/,cassette.jsonl}``.
``run.json`` is a small manifest rewritten wholesale on ``finish_run``; everything
that happened during the run lives in ``trace.jsonl``, written incrementally by
:class:`~runectl.trace.writer.TraceWriter`. No database is required for a run to
exist or be replayed.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from runectl.config import runectl_home
from runectl.ids import new_run_id
from runectl.trace.writer import TraceWriter

RunOutcome = Literal["solved", "candidate", "exhausted", "error"]


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    challenge_name: str
    category: str
    model: str
    provider: str
    config_snapshot: dict[str, Any]
    started_at: float
    finished_at: float | None = None
    outcome: RunOutcome | None = None
    exit_code: int | None = None
    flag: str | None = None
    cost_usd: float = 0.0
    steps_used: int = 0
    progress_steps: int = 0
    blocked_steps: int = 0
    # D20 — the *resolved* thinking level (post-clamp), same rule as
    # progress_steps: a run's reasoning spend has to be discoverable from
    # run.json alone, not only by re-reading trace.jsonl's run.started event.
    thinking_level: str = "off"
    # Set when a human (or driving agent) finalized a pending candidate with
    # `runectl flag approve` (D11). It keeps an approved solve distinguishable
    # from one the judge cleared on its own — `runectl bench` scores them apart,
    # and a report that hid the difference would be flattering, not honest.
    approved_at: float | None = None


class Store:
    def __init__(self, home: Path | None = None) -> None:
        self._home = home or runectl_home()

    @property
    def home(self) -> Path:
        return self._home

    def run_dir(self, run_id: str) -> Path:
        return self._home / "runs" / run_id

    def trace_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "trace.jsonl"

    def manifest_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.json"

    def artifacts_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "artifacts"

    def cassette_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "cassette.jsonl"

    def new_run(
        self,
        *,
        challenge_name: str,
        category: str,
        model: str,
        provider: str,
        config_snapshot: dict[str, Any],
    ) -> tuple[str, TraceWriter]:
        run_id = new_run_id()
        run_dir = self.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir(run_id).mkdir(parents=True, exist_ok=True)
        manifest = RunManifest(
            run_id=run_id,
            challenge_name=challenge_name,
            category=category,
            model=model,
            provider=provider,
            config_snapshot=config_snapshot,
            started_at=time.time(),
        )
        self._write_manifest(manifest)
        writer = TraceWriter(self.trace_path(run_id), run_id, self.artifacts_dir(run_id))
        return run_id, writer

    def finish_run(
        self,
        run_id: str,
        *,
        outcome: RunOutcome,
        exit_code: int,
        flag: str | None = None,
        cost_usd: float = 0.0,
        steps_used: int = 0,
        progress_steps: int = 0,
        blocked_steps: int = 0,
        thinking_level: str = "off",
    ) -> RunManifest:
        manifest = self.read_manifest(run_id)
        manifest = manifest.model_copy(
            update={
                "finished_at": time.time(),
                "outcome": outcome,
                "exit_code": exit_code,
                "flag": flag,
                "cost_usd": cost_usd,
                "steps_used": steps_used,
                "progress_steps": progress_steps,
                "blocked_steps": blocked_steps,
                "thinking_level": thinking_level,
            }
        )
        self._write_manifest(manifest)
        return manifest

    def approve_flag(self, run_id: str, *, flag: str) -> RunManifest:
        """Finalize a pending candidate out of band (D11's `flag approve`).

        Deliberately not `finish_run`: the run already finished, and rewriting
        `finished_at` would erase when it actually ended. This records a second,
        later decision on top of a completed run.
        """
        manifest = self.read_manifest(run_id)
        manifest = manifest.model_copy(
            update={
                "outcome": "solved",
                "exit_code": 0,
                "flag": flag,
                "approved_at": time.time(),
            }
        )
        self._write_manifest(manifest)
        return manifest

    def read_manifest(self, run_id: str) -> RunManifest:
        return RunManifest.model_validate_json(self.manifest_path(run_id).read_text())

    def _write_manifest(self, manifest: RunManifest) -> None:
        self.manifest_path(manifest.run_id).write_text(manifest.model_dump_json(indent=2))

    def list_run_ids(self) -> list[str]:
        runs_root = self._home / "runs"
        if not runs_root.exists():
            return []
        return sorted(p.name for p in runs_root.iterdir() if p.is_dir())
