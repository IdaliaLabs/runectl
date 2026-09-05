"""Hand-authored completions for free, deterministic unit tests (D5, plan §3.5).

Together with :class:`~runectl.sandbox.stub.StubSandbox`, this is what lets the
whole loop run with no daemon and no API spend (`REBUILD_NOTES.md` req 4).
"""

from __future__ import annotations

from collections.abc import Sequence

from runectl.providers.base import Completion, Message
from runectl.tools.schema import ToolSchema


class ScriptedProvider:
    """Returns completions from a fixed list, one per call, in order."""

    def __init__(self, completions: Sequence[Completion]) -> None:
        self._completions = list(completions)
        self._cursor = 0

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
    ) -> Completion:
        if self._cursor >= len(self._completions):
            raise IndexError("ScriptedProvider: complete() called past the end of the script")
        completion = self._completions[self._cursor]
        self._cursor += 1
        return completion
