"""Append-only JSONL trace writer (D3, plan §1.2).

Two hooks run on every event before it hits disk: secret redaction (so a leaked
key can never land in ``trace.jsonl``) and >8KB spill to ``artifacts/`` (so the
trace file stays greppable). The writer flushes and ``fsync``s after every
event, so a SIGKILL leaves a valid, replayable prefix rather than a torn file.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runectl.config import ARTIFACT_SPILL_THRESHOLD_BYTES
from runectl.trace.events import Event, EventPayload

OnEmit = Callable[[Event], None]

# Secret patterns redacted from every event before it is written. Conservative
# on purpose: over-redacting a false positive is free, under-redacting a real
# key is not.
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)authorization:\s*bearer\s+\S+"),
)
_REDACTED = "[REDACTED]"


def _redact_str(value: str) -> str:
    for pattern in _REDACT_PATTERNS:
        value = pattern.sub(_REDACTED, value)
    return value


def _walk(value: Any, transform: Callable[[str], Any]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {k: _walk(v, transform) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk(v, transform) for v in value]
    return value


def _spill_str(value: str, artifacts_dir: Path) -> Any:
    encoded = value.encode("utf-8")
    if len(encoded) <= ARTIFACT_SPILL_THRESHOLD_BYTES:
        return value
    digest = hashlib.sha256(encoded).hexdigest()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts_dir / f"{digest}.txt"
    if not artifact_path.exists():
        artifact_path.write_bytes(encoded)
    return {"$artifact": digest, "bytes": len(encoded)}


class TraceWriter:
    """Appends typed events to ``trace.jsonl``, stamping a monotonic ``seq``."""

    def __init__(
        self,
        path: Path,
        run_id: str,
        artifacts_dir: Path,
        *,
        start_seq: int = 0,
        on_emit: OnEmit | None = None,
    ) -> None:
        self._path = path
        self._run_id = run_id
        self._artifacts_dir = artifacts_dir
        self._seq = start_seq
        self._on_emit = on_emit
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    @property
    def seq(self) -> int:
        return self._seq

    def set_on_emit(self, on_emit: OnEmit | None) -> None:
        """Attach a render callback (D13: only cli/ code may supply one — this
        writer never interprets or prints an event itself, only forwards it)."""
        self._on_emit = on_emit

    def emit(self, payload: EventPayload) -> Event:
        self._seq += 1
        data = payload.model_dump(mode="json")
        data = _walk(data, lambda s: _spill_str(_redact_str(s), self._artifacts_dir))
        event = Event(
            run_id=self._run_id,
            seq=self._seq,
            ts=time.time(),
            type=type(payload).event_type,
            data=data,
        )
        self._fh.write(event.model_dump_json() + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())
        if self._on_emit is not None:
            self._on_emit(event)
        return event

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> TraceWriter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
