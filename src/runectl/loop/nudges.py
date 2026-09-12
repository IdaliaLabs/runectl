"""Earned nudges as pure functions.

Carried from the predecessor tool's proven anti-failure patches, re-expressed
as pure functions instead of branches scattered through the
loop, so each is unit-testable with no model
and no sandbox.
"""

from __future__ import annotations

_APPROVAL_PHRASES = (
    "should i proceed",
    "would you like me to",
    "let me know if",
    "shall i",
    "do you want me to",
    "can i go ahead",
)


def looks_like_approval_seeking(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _APPROVAL_PHRASES)


def act_dont_ask() -> str:
    return "Do not ask for approval. Act now: issue the next concrete tool call."


def no_tool_call() -> str:
    return "Respond with exactly one tool call. Do not respond with text alone during execution."
