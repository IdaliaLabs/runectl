"""Minimal M4 submit_flag path — the seam D15/M6's full judge replaces (plan §7.5).

Deliberately NOT the false-flag subsystem (D15): no plausibility filtering, no
decoy detection, no independent corroboration, no verification double-check,
no `--approval` policy. It does exactly one thing, structurally, for free:
refuse to finalize a flag the agent cannot point to in its own trace. That one
check answers `REBUILD_NOTES.md` §3's honesty problem directly — a flag that
never appeared in any tool output this run actually observed is rejected, not
finalized, regardless of how confidently the model asserts it.

M6 replaces `judge_candidate`'s body with the full D11/D15 policy (plausibility,
decoy detection, corroboration >= 2, re-derivation) without changing this
signature or the `flag.candidate` / `flag.decision` events built around it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolObservation:
    """One past tool result the judge can search for provenance."""

    seq: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class JudgeDecision:
    accepted: bool
    provenance_seq: int
    reason: str


def judge_candidate(
    *,
    flag: str,
    history: list[ToolObservation],
    fallback_seq: int,
) -> JudgeDecision:
    """Accept a flag only if it appears verbatim in a prior observed tool output.

    Searches most-recent-first so provenance points at the observation that
    actually produced the flag, not an earlier coincidental appearance.
    """
    if flag:
        for observation in reversed(history):
            if flag in observation.stdout or flag in observation.stderr:
                return JudgeDecision(
                    accepted=True,
                    provenance_seq=observation.seq,
                    reason=f"flag observed verbatim in tool output at seq {observation.seq}",
                )
    return JudgeDecision(
        accepted=False,
        provenance_seq=fallback_seq,
        reason="flag does not appear verbatim in any tool output this run has observed",
    )
