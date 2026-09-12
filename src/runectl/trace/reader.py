"""Lazy, ordered trace reader (D3).

Reads ``trace.jsonl`` line by line, validating each line to its typed payload
model. If a line fails to parse (a SIGKILL can leave the final line truncated),
the reader stops there and yields everything before it — a valid prefix, never
a raised exception about the tail.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from runectl.trace.events import Event
from runectl.trace.store import RunManifest, Store


class TraceReader:
    def __init__(self, path: Path, artifacts_dir: Path) -> None:
        self._path = path
        self._artifacts_dir = artifacts_dir

    def __iter__(self) -> Iterator[Event]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = Event.model_validate_json(stripped)
                    event.payload()  # validate data against its registered model too
                except (ValidationError, ValueError):
                    return
                yield event

def load_run(run_id: str, *, store: Store | None = None) -> tuple[RunManifest, Iterator[Event]]:
    """Return ``(manifest, events)`` for a run, ordered by ``seq``."""
    store = store or Store()
    manifest = store.read_manifest(run_id)
    reader = TraceReader(store.trace_path(run_id), store.artifacts_dir(run_id))
    return manifest, iter(reader)
