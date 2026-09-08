"""Hand-authored completions for free, deterministic unit tests (D5, plan §3.5).

Together with :class:`~runectl.sandbox.stub.StubSandbox`, this is what lets the
whole loop run with no daemon and no API spend (`REBUILD_NOTES.md` req 4).

A step may also be a callable taking the conversation so far. That exists for
one specific reason: D15 makes the agent cite the `seq` of the observation a
flag came from, and a seq is only knowable once the run is underway — a
scripted step that reads it out of the history is doing exactly what a real
model does with the `[observation seq=N]` header, rather than hardcoding a
number that any new trace event would silently invalidate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from runectl.providers.base import Completion, Message
from runectl.tools.schema import ToolSchema

Step = Completion | Callable[[Sequence[Message]], Completion]


class ScriptedProvider:
    """Returns completions from a fixed list, one per call, in order."""

    def __init__(self, completions: Sequence[Step]) -> None:
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
        step = self._completions[self._cursor]
        self._cursor += 1
        return step(messages) if callable(step) else step
