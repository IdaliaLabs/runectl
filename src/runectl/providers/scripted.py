"""Hand-authored completions for free, deterministic unit tests (D5).

Together with :class:`~runectl.sandbox.stub.StubSandbox`, this is what lets the
whole loop run with no daemon and no API spend.

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
from runectl.providers.registry import ThinkingLevel
from runectl.tools.schema import ToolSchema

Step = Completion | Callable[[Sequence[Message]], Completion]


class ScriptedProvider:
    """Returns completions from a fixed list, one per call, in order."""

    def __init__(self, completions: Sequence[Step]) -> None:
        self._completions = list(completions)
        self._cursor = 0
        # D20 — the thinking level each call was made with, for tests that
        # assert the loop resolved and requested the level they expected.
        self.thinking_requests: list[ThinkingLevel] = []

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        max_tokens: int,
        thinking: ThinkingLevel = "off",
    ) -> Completion:
        if self._cursor >= len(self._completions):
            raise IndexError("ScriptedProvider: complete() called past the end of the script")
        self.thinking_requests.append(thinking)
        step = self._completions[self._cursor]
        self._cursor += 1
        return step(messages) if callable(step) else step
