"""Earned nudges as pure functions (plan §5.5).

Carried from the predecessor's proven anti-failure patches (`REBUILD_NOTES.md`
§2 items 3-4, `PROMPT_ARCHIVE.md` §4), re-expressed as pure functions instead
of branches scattered through the loop, so each is unit-testable with no model
and no sandbox.
"""

from __future__ import annotations

from runectl.loop.state import RunState

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


def stuck_new_hypothesis(state: RunState, *, consecutive_no_progress: int) -> str | None:
    if consecutive_no_progress < 2:
        return None
    recent = state.tool_observations[-3:]
    evidence = "; ".join(f"seq {o.seq}: {o.stdout[:80]!r}" for o in recent) or "no prior observations"
    return (
        f"No new signal in the last {consecutive_no_progress} actions. You are stuck — "
        f"pick a genuinely different hypothesis class, not a wider version of the same "
        f"approach. Evidence so far: {evidence}"
    )


def anti_optimism() -> str:
    return (
        "No flag with solid, cited evidence beats a guess. If you cannot point to "
        "exactly where a flag appeared, say so and keep investigating — do not submit "
        "an unsupported guess."
    )
