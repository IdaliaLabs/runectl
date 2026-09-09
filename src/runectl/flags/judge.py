"""The false-flag subsystem (D15, D11) — the judge that decides a submitted flag.

A wrong flag scores worse than no flag, so this module's bias is explicit: every
check that cannot be *satisfied* leaves a candidate `pending` rather than
finalizing it. Five mechanisms, in the order D15 names them:

1. **Provenance is mandatory** — `submit_flag(flag, how_found, provenance)`. The
   agent cites the `seq` of the observation it saw the flag in; the judge
   re-reads that observation and confirms the flag literally appears there, and
   that the flag did not appear *because the agent wrote it into the command*.
   That last part is not hypothetical: the first real run of this tool printed
   its own guess and had it accepted (run 20260908-033407-cfa0ad, 2026-09-07).
   What must appear is the flag's **payload** — the part inside the wrapper —
   because the wrapper is published in the challenge and nobody earns it
   (`_payload_is_provenance`, 2026-09-08).
2. **Verification re-derivation** — the cited command is re-run in the sandbox
   and must produce the same string again. The disconfirmation pass in
   `flags/review.py` (D15 mechanism 2's "framed to *disconfirm* rather than
   confirm") still runs on every candidate and is recorded, but is **advisory**
   as of 2026-09-08: over nine live reviews it cleared two wrong flags, held one
   correct one, and caught nothing. See the D11 amendment.
3. **Decoy detection** — `flags/decoys.py`.
4. **Independent corroboration** is still counted and reported, but **no longer
   gates** — see the 2026-09-08 D11 amendment and `bench/results/README.md`. It
   held four correct flags and finalized a wrong one, because a clean solve
   produces its answer once and a stubborn agent produces it as many times as
   the checker asks for.

What actually gates a candidate under `gated`, then, is the deterministic set:
provenance and anti-echo, decoy markers, `--flag-format`, and re-derivation in
the sandbox. That is not a retreat — it is what the bench evidence supports.
Both model-judgement mechanisms tried so far were satisfiable or fooled by the
model they were judging; the sandbox is not.
5. **No-flag-is-success** — enforced by the loop, not here: a rejection is
   feedback and the run continues; exhausting without a flag exits 3 rather
   than fabricating one. The base rules (`loop/context.py`) say so to the model.

`--approval` (D11) decides what a *cleared* candidate becomes, and nothing else:
mechanisms 1 and 3 are unconditional under every policy.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from runectl.flags import decoys, plausibility
from runectl.flags.review import Reviewer, ReviewRequest
from runectl.progress.fingerprint import fingerprint

Decision = Literal["finalized", "pending", "rejected"]

# Still counted and reported; no longer part of the auto-finalize bar (D11,
# amended 2026-09-08). Kept because "how many independent sightings" is real
# information for whoever reads a held candidate.
MIN_CORROBORATION = 2

# A re-derivation is a repeat of a command that already ran once inside this
# run's step timeout, so it does not need a generous one of its own.
REDERIVE_TIMEOUT_S = 60

# The distinctive part of a flag: whatever sits inside the outermost braces.
# `csictf{h45t4d}` -> prefix `csictf`, payload `h45t4d`. Flags without braces
# have no wrapper and are used whole.
_FLAG_CORE = re.compile(r"^(?P<prefix>[^{]*)\{(?P<core>.+)\}[^}]*$", re.DOTALL)

# Below this length a payload is too generic to be evidence of authorship —
# `flag{a}` would match nearly any command text. This threshold guards a
# *rejection*, so erring short is the safe direction.
_MIN_CORE_LEN = 4

# The threshold for letting a payload sighting stand as provenance. This one
# guards an *acceptance*, so it errs long: a short string appearing somewhere in
# a wall of output is coincidence, not evidence.
_MIN_PAYLOAD_LEN = 8

_SEQ_IN_TEXT = re.compile(r"\d+")

# The checks whose failure can hold a candidate under `gated`. Everything else
# a verdict carries — corroboration, the disconfirmation review — is recorded
# for whoever reads it and decides nothing (D11, amended 2026-09-08).
_GATING_CHECKS = frozenset({"flag_format", "rederivation"})


def _payload_of(flag: str) -> str:
    """The earned part of a flag: what sits inside the wrapper, else the whole."""
    match = _FLAG_CORE.match(flag)
    return match.group("core") if match else flag


def _prefix_of(flag: str) -> str:
    """The wrapper's name — `csictf{...}` -> `csictf`. Empty when unwrapped."""
    match = _FLAG_CORE.match(flag)
    return match.group("prefix").strip() if match else ""


class _Executor(Protocol):
    """Just the slice of `Sandbox` re-derivation needs (D6: take what you need)."""

    def exec(self, argv_or_script: str, *, timeout_s: int) -> object: ...


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
    core = _payload_of(flag)
    if core == flag:
        return False
    return len(core) >= _MIN_CORE_LEN and core in command


@dataclass(frozen=True)
class ToolObservation:
    """One past tool result the judge can search for provenance.

    ``command`` is the serialized arguments of the call that produced it — the
    judge needs it to tell evidence from echo. ``shell_command`` is the same
    call reduced to something re-runnable, and is empty for tools that have no
    such form; without it a candidate cannot be re-derived (D15 mechanism 2)
    and so cannot be auto-finalized under `gated`.
    """

    seq: int
    stdout: str
    stderr: str
    command: str = ""
    tool: str = ""
    shell_command: str = ""

    @property
    def text(self) -> str:
        return f"{self.stdout}\n{self.stderr}"

    def contains(self, flag: str) -> bool:
        return flag in self.stdout or flag in self.stderr


@dataclass(frozen=True)
class Check:
    """One named mechanism's result, kept for the trace and for `flag list`."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class JudgeVerdict:
    decision: Decision
    provenance_seq: int
    reason: str
    corroboration: int = 0
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        """Back-compatible with the M4 judge: accepted means *finalized*."""
        return self.decision == "finalized"


# Kept as the old name so the M4-era call sites and regression tests that encode
# the laundering incident keep working against the same code path.
JudgeDecision = JudgeVerdict


class FlagJudge:
    """Runs the D15 pipeline for one run. Returns verdicts; mutates nothing (D6)."""

    def __init__(
        self,
        *,
        approval_policy: str = "gated",
        flag_format: str | None = None,
        description: str = "",
        sandbox: _Executor | None = None,
        reviewer: Reviewer | None = None,
        require_provenance: bool = True,
        min_corroboration: int = MIN_CORROBORATION,
    ) -> None:
        self._policy = approval_policy
        self._flag_format = flag_format
        self._description = description
        self._sandbox = sandbox
        self._reviewer = reviewer
        self._require_provenance = require_provenance
        self._min_corroboration = min_corroboration

    def judge(
        self,
        *,
        flag: str,
        history: Sequence[ToolObservation],
        fallback_seq: int,
        provenance: str = "",
        how_found: str = "",
    ) -> JudgeVerdict:
        checks: list[Check] = []

        # 1. Plausibility (D11) — cheapest rejections first, before any sandbox work.
        verdict = plausibility.assess(flag)
        checks.append(Check("plausibility", verdict.plausible, verdict.reason))
        if not verdict.plausible:
            return self._rejected(fallback_seq, f"implausible flag: {verdict.reason}", checks)

        # 2. Provenance (D15 mechanism 1) — unconditional under every policy.
        source, error = self._resolve_provenance(flag, history, provenance)
        if source is None:
            checks.append(Check("provenance", False, error))
            return self._rejected(fallback_seq, error, checks)
        checks.append(Check("provenance", True, self._provenance_detail(flag, source)))

        # 3. Decoy detection (D15 mechanism 3) — also unconditional.
        decoy = decoys.assess(
            flag,
            source_output=source.text,
            source_command=source.command,
            description=self._description,
        )
        checks.append(Check("decoy", not decoy.is_decoy, decoy.reason))
        if decoy.is_decoy:
            return self._rejected(source.seq, f"looks planted: {decoy.reason}", checks)

        # 4. The gating checks. These never reject — they decide whether a
        #    candidate is eligible to be finalized without a human (D11).
        corroboration = self._corroboration(flag, history)
        checks.append(
            Check(
                # Always passes: reported for whoever reads a held candidate,
                # never a reason to hold one (D11, amended 2026-09-08).
                "corroboration",
                True,
                f"{corroboration} independent observation(s)",
            )
        )

        format_ok, format_detail = self._format_check(flag)
        checks.append(Check("flag_format", format_ok, format_detail))

        rederived, rederive_detail = self._rederive(flag, source)
        checks.append(Check("rederivation", rederived, rederive_detail))

        # Run last: it is the only stage that costs tokens, so everything a
        # deterministic check can settle is already settled by here. Advisory
        # since 2026-09-08 — its verdict is recorded and printed, and does not
        # decide anything.
        reviewed, review_detail = self._review(flag, how_found, source)
        checks.append(Check("review", reviewed, review_detail))

        return self._apply_policy(
            source_seq=source.seq,
            corroboration=corroboration,
            format_ok=format_ok,
            rederived=rederived,
            checks=checks,
        )

    # -- stages ---------------------------------------------------------------

    def _payload_is_provenance(self, flag: str) -> bool:
        """May a sighting of the payload alone stand as provenance?

        The wrapper is public. It is printed in the challenge description and
        passed as `--flag-format`, so nobody earns it, and no correct solver can
        be required to make it appear in output. For a flag that is a computed
        value inside a known wrapper — `csictf{785539772602034710213927792950}`,
        bench `machine-fix` — the whole string can only reach a tool's output if
        the agent types the wrapper into the command, which is exactly what the
        anti-echo rule rejects. Provenance therefore matches on the payload, and
        the wrapper has to be attested by the challenge rather than invented by
        the agent: if the agent made the prefix up, this returns False and the
        whole flag is required as before.
        """
        payload = _payload_of(flag)
        if payload == flag or len(payload) < _MIN_PAYLOAD_LEN:
            return False
        prefix = _prefix_of(flag)
        if not prefix:
            return False
        attested = f"{self._description}\n{self._flag_format or ''}".lower()
        return prefix.lower() in attested

    def _evident_in(self, text: str, flag: str) -> bool:
        """Does this text carry the flag — whole, or as its earned payload?"""
        if flag in text:
            return True
        return self._payload_is_provenance(flag) and _payload_of(flag) in text

    def _observes(self, obs: ToolObservation, flag: str) -> bool:
        return self._evident_in(obs.stdout, flag) or self._evident_in(obs.stderr, flag)

    def _provenance_detail(self, flag: str, source: ToolObservation) -> str:
        if flag in source.text:
            return f"observed at seq {source.seq}"
        return f"payload observed at seq {source.seq}, inside a wrapper the challenge states"

    def _resolve_provenance(
        self, flag: str, history: Sequence[ToolObservation], provenance: str
    ) -> tuple[ToolObservation | None, str]:
        """The cited observation, or why no observation can support this flag."""
        matches = [obs for obs in history if self._observes(obs, flag)]
        supported = [obs for obs in matches if not _authored_in(flag, obs.command)]

        if not matches:
            if self._payload_is_provenance(flag):
                return None, (
                    "neither the flag nor the value inside its wrapper appears in any "
                    "tool output this run has observed"
                )
            return None, "flag does not appear verbatim in any tool output this run has observed"
        if not supported:
            return None, (
                "the flag only appears in output produced by a command that already "
                "contained it — that is the agent echoing its own guess, not evidence. "
                "Derive the flag from the challenge data and cite the command that "
                "produced it."
            )

        cited = _parse_seq(provenance)
        if cited is None:
            if not self._require_provenance:
                return supported[-1], ""
            return None, (
                "submit_flag needs `provenance`: the seq number shown in the "
                "[observation seq=N] header of the tool result where the flag appeared."
            )

        by_seq = {obs.seq: obs for obs in supported}
        if cited in by_seq:
            return by_seq[cited], ""
        if any(obs.seq == cited for obs in matches):
            return None, (
                f"the observation you cited (seq {cited}) contains the flag only because "
                "your own command did — cite the observation that first produced it."
            )
        return None, (
            f"the flag does not appear in the observation you cited (seq {cited}). "
            "Cite the seq of the tool result you actually read it in."
        )

    def _corroboration(self, flag: str, history: Sequence[ToolObservation]) -> int:
        """How many *independent* observations produced this string (D15 §4).

        Independent means a different tool call and a different output
        fingerprint (D8): running the same solver twice, or two commands whose
        output normalizes to the same thing, is one piece of evidence seen
        twice, not two.
        """
        seen_commands: set[str] = set()
        seen_prints: set[str] = set()
        count = 0
        for obs in history:
            if not self._observes(obs, flag) or _authored_in(flag, obs.command):
                continue
            print_ = fingerprint(obs.text)
            if obs.command in seen_commands or print_ in seen_prints:
                continue
            seen_commands.add(obs.command)
            seen_prints.add(print_)
            count += 1
        return count

    def _format_check(self, flag: str) -> tuple[bool, str]:
        """Match `--flag-format` when one was supplied (D11).

        With no format supplied there is nothing to match, and treating that as
        a failure would silently turn `gated` into `strict` for every challenge
        whose format the user did not type out. It passes, and says so.
        """
        if not self._flag_format:
            return True, "no --flag-format supplied; nothing to match against"
        try:
            pattern = re.compile(self._flag_format)
        except re.error as exc:
            return False, f"--flag-format is not a valid regex: {exc}"
        if pattern.search(flag):
            return True, f"matches {self._flag_format}"
        return False, f"does not match {self._flag_format}"

    def _review(self, flag: str, how_found: str, source: ToolObservation) -> tuple[bool, str]:
        """Ask a cheap model to find a reason this flag is wrong (D15 §2)."""
        if self._reviewer is None:
            return False, "no reviewer available to check this candidate"
        verdict = self._reviewer(
            ReviewRequest(
                flag=flag,
                how_found=how_found,
                description=self._description,
                source_command=source.shell_command or source.command,
                source_output=source.text,
            )
        )
        return verdict.sound, verdict.detail

    def _rederive(self, flag: str, source: ToolObservation) -> tuple[bool, str]:
        """Re-run the cited command and require the same string back (D15 §2)."""
        if self._sandbox is None:
            return False, "no sandbox available to re-derive in"
        if not source.shell_command:
            return False, f"the cited observation (seq {source.seq}) has no re-runnable command"
        try:
            result = self._sandbox.exec(source.shell_command, timeout_s=REDERIVE_TIMEOUT_S)
        except Exception as exc:  # a failed re-derivation is a verdict, not a crash
            return False, f"re-running the cited command failed: {exc}"
        stdout = str(getattr(result, "stdout", ""))
        stderr = str(getattr(result, "stderr", ""))
        if self._evident_in(stdout, flag) or self._evident_in(stderr, flag):
            return True, "re-running the cited command produced the same flag"
        return False, "re-running the cited command did not produce the flag again"

    def _apply_policy(
        self,
        *,
        source_seq: int,
        corroboration: int,
        format_ok: bool,
        rederived: bool,
        checks: list[Check],
    ) -> JudgeVerdict:
        if self._policy == "auto":
            return JudgeVerdict(
                decision="finalized",
                provenance_seq=source_seq,
                reason="--approval auto: finalized on plausibility and provenance alone",
                corroboration=corroboration,
                checks=tuple(checks),
            )
        if self._policy == "strict":
            return JudgeVerdict(
                decision="pending",
                provenance_seq=source_seq,
                reason="--approval strict: candidates are never auto-finalized",
                corroboration=corroboration,
                checks=tuple(checks),
            )

        if format_ok and rederived:
            return JudgeVerdict(
                decision="finalized",
                provenance_seq=source_seq,
                reason="re-derived in the sandbox and matching the expected format",
                corroboration=corroboration,
                checks=tuple(checks),
            )
        unmet = "; ".join(
            f"{check.name}: {check.detail}"
            for check in checks
            if not check.passed and check.name in _GATING_CHECKS
        )
        return JudgeVerdict(
            decision="pending",
            provenance_seq=source_seq,
            reason=f"held for approval — {unmet}" if unmet else "held for approval",
            corroboration=corroboration,
            checks=tuple(checks),
        )

    @staticmethod
    def _rejected(seq: int, reason: str, checks: list[Check]) -> JudgeVerdict:
        return JudgeVerdict(
            decision="rejected", provenance_seq=seq, reason=reason, checks=tuple(checks)
        )


def _parse_seq(provenance: str) -> int | None:
    match = _SEQ_IN_TEXT.search(provenance or "")
    return int(match.group(0)) if match else None


def judge_candidate(
    *,
    flag: str,
    history: list[ToolObservation],
    fallback_seq: int,
) -> JudgeVerdict:
    """Provenance-only judging: does a tool *produce* this flag, or echo it back?

    The M4 entry point, kept because it is exactly the subset of the pipeline
    that has no policy in it — plausibility, provenance, anti-echo — and the
    regression tests for the laundering incident are written against it. New
    code uses `FlagJudge`, which adds decoys, corroboration, re-derivation and
    the `--approval` policy on top of these same stages.
    """
    verdict = FlagJudge(approval_policy="auto", require_provenance=False).judge(
        flag=flag, history=history, fallback_seq=fallback_seq
    )
    if verdict.decision == "finalized":
        return JudgeVerdict(
            decision="finalized",
            provenance_seq=verdict.provenance_seq,
            reason=f"flag observed verbatim in tool output at seq {verdict.provenance_seq}",
            corroboration=verdict.corroboration,
            checks=verdict.checks,
        )
    return verdict
