"""Cassette recording/replay: byte-identical responses at zero spend (D5, plan §3.5).

``--record`` wraps a real provider in :class:`RecordingProvider`, which hashes
each request and appends the request/response pair to ``cassette.jsonl``.
``runectl replay`` then serves the same responses back from
:class:`ReplayProvider`, keyed by the identical hash, with no network call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from runectl.providers.base import Completion, Message, Provider
from runectl.tools.schema import ToolSchema


def request_hash(
    system: str, messages: Sequence[Message], tools: Sequence[ToolSchema], max_tokens: int
) -> str:
    payload = {
        "system": system,
        "messages": [m.model_dump(mode="json") for m in messages],
        "tools": sorted(t.name for t in tools),
        "max_tokens": max_tokens,
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CassetteEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_hash: str
    response: dict[str, Any]


class RecordingProvider:
    """Wraps a real provider; appends every request/response pair to the cassette."""

    def __init__(self, inner: Provider, cassette_path: Path) -> None:
        self._inner = inner
        self._cassette_path = cassette_path
        self._cassette_path.parent.mkdir(parents=True, exist_ok=True)

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> Completion:
        completion = self._inner.complete(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        entry = CassetteEntry(
            request_hash=request_hash(system, messages, tools, max_tokens),
            response=completion.model_dump(mode="json"),
        )
        with self._cassette_path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")
        return completion


class ReplayProvider:
    """Serves recorded completions by request hash — byte-identical, zero spend."""

    def __init__(self, cassette_path: Path) -> None:
        self._by_hash: dict[str, list[Completion]] = {}
        if cassette_path.exists():
            for line in cassette_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = CassetteEntry.model_validate_json(line)
                self._by_hash.setdefault(entry.request_hash, []).append(
                    Completion.model_validate(entry.response)
                )
        self._cursor: dict[str, int] = {}

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> Completion:
        key = request_hash(system, messages, tools, max_tokens)
        candidates = self._by_hash.get(key)
        if not candidates:
            raise KeyError(f"ReplayProvider: no recorded response for request hash {key}")
        index = min(self._cursor.get(key, 0), len(candidates) - 1)
        self._cursor[key] = index + 1
        return candidates[index]
