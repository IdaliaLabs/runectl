"""Lazy, ordered trace reader (D3).

Reads ``trace.jsonl`` line by line, resolving any spilled artifact references
back to their content and validating each line to its typed payload model. If a
line fails to parse (a SIGKILL can leave the final line truncated), the reader
stops there and yields everything before it — a valid prefix, never a raised
exception about the tail.

That stop-on-bad-line rule is deliberately narrow, and used to be far too wide:
until 2026-09-13 the reader did not resolve artifacts at all (it held an
``artifacts_dir`` it never read), so the first tool output over the spill
threshold failed validation and was mistaken for a torn tail. Everything after
it was dropped without a word.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from runectl.trace.events import Event, resolve_artifact_refs
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
                    # Restore any >8KB field the writer spilled to artifacts/
                    # (D3) before validating. Without this the payload check
                    # below raised on every spilled event — a string field
                    # holding an artifact reference is not a string — and the
                    # reader treated that as a torn tail and stopped, silently
                    # truncating the run at its first large tool output. The
                    # record is the product; losing the back half of it quietly
                    # is the worst failure this file can have.
                    event = event.model_copy(
                        update={"data": resolve_artifact_refs(event.data, self._artifacts_dir)}
                    )
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
