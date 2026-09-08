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

import re
from dataclasses import dataclass

# The distinctive part of a flag: whatever sits inside the outermost braces.
# `csictf{h45t4d}` -> `h45t4d`. Flags without braces are used whole.
_FLAG_CORE = re.compile(r"^[^{]*\{(?P<core>.+)\}[^}]*$", re.DOTALL)

# Below this length a "core" is too generic to be evidence of authorship —
# `flag{a}` would match nearly any command text.
_MIN_CORE_LEN = 4


def _authored_in(flag: str, command: str) -> bool:
    """Did the agent write this flag's payload into the command itself?

    Matching the whole flag string is not enough: the run that motivated this
    check printed its guess from an f-string (`print(f"csictf{{{{...}}}}")`), so
    the command contained doubled braces and the literal flag never appeared in
    it. The payload inside the braces is what the agent actually had to type,
    and it survives that kind of quoting.
    """
    if not command:
        return False
    if flag in command:
        return True
    match = _FLAG_CORE.match(flag)
    if match is None:
        return False
    core = match.group("core")
    return len(core) >= _MIN_CORE_LEN and core in command


@dataclass(frozen=True)
class ToolObservation:
    """One past tool result the judge can search for provenance.

    ``command`` is the text of the tool call that produced it. The judge needs
    it to tell evidence from echo: a flag that appears in output *because the
    agent wrote it into the command* is not a discovery.
    """

    seq: int
    stdout: str
    stderr: str
    command: str = ""


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
    """Accept a flag only if a tool *produced* it — not merely printed it back.

    Two checks, both structural:

    1. The flag appears verbatim in some prior tool result. This kills flags
       invented in the assistant's prose.
    2. That result did not come from a command the agent authored the flag into
       (matched on the flag's payload, so f-string/quoting tricks don't evade).
       Without this, check 1 is trivially defeated: the agent writes
       ``print("ctf{guess}")``, the string duly appears in stdout, and its own
       guess is laundered into evidence. This is not hypothetical — the first
       real run of this tool did exactly that and had a wrong flag accepted
       (run 20260908-033407-cfa0ad, 2026-09-07), which is what prompted the
       check.

    Self-authored observations are skipped rather than fatal, so a genuine
    earlier discovery still counts even if the agent later echoed the flag.
    """
    if not flag:
        return JudgeDecision(
            accepted=False, provenance_seq=fallback_seq, reason="no flag value submitted"
        )

    echoed_only = False
    for observation in reversed(history):
        if flag not in observation.stdout and flag not in observation.stderr:
            continue
        if _authored_in(flag, observation.command):
            # The agent put the flag into the command that produced this output;
            # the output is an echo of its own guess, not a finding.
            echoed_only = True
            continue
        return JudgeDecision(
            accepted=True,
            provenance_seq=observation.seq,
            reason=f"flag observed verbatim in tool output at seq {observation.seq}",
        )

    if echoed_only:
        return JudgeDecision(
            accepted=False,
            provenance_seq=fallback_seq,
            reason=(
                "the flag only appears in output produced by a command that already "
                "contained it — that is the agent echoing its own guess, not evidence. "
                "Derive the flag from the challenge data and cite the command that "
                "produced it."
            ),
        )
    return JudgeDecision(
        accepted=False,
        provenance_seq=fallback_seq,
        reason="flag does not appear verbatim in any tool output this run has observed",
    )
