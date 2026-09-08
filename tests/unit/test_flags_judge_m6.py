"""The D15 false-flag subsystem: every mechanism, and the D11 policy on top.

The bias under test is one-directional. A check that cannot be *satisfied*
leaves a candidate pending; only a check that is actively *violated* rejects.
"""

from __future__ import annotations

import pytest

from runectl.flags.judge import FlagJudge, ToolObservation
from runectl.flags.review import ReviewRequest, ReviewVerdict
from runectl.sandbox.base import ExecResult

FLAG = "csictf{you_are_a_basic_person}"


class _Executor:
    """Replays fixed output per command, and records what was re-run."""

    def __init__(self, script: dict[str, str] | None = None) -> None:
        self.script = script or {}
        self.calls: list[str] = []

    def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult:
        self.calls.append(argv_or_script)
        return ExecResult(
            ok=True,
            stdout=self.script.get(argv_or_script, ""),
            stderr="",
            exit_code=0,
            duration_s=0.01,
            timed_out=False,
        )


def _observation(seq: int, stdout: str, *, command: str = "", shell: str = "") -> ToolObservation:
    return ToolObservation(
        seq=seq,
        stdout=stdout,
        stderr="",
        command=command or '{"command": "solve.py"}',
        tool="run_command",
        shell_command=shell or "python3 solve.py",
    )


def _corroborated() -> list[ToolObservation]:
    """Two different commands, two different outputs, the same flag."""
    return [
        _observation(10, f"decoded: {FLAG}", command='{"command": "solve.py"}', shell="python3 solve.py"),
        _observation(
            20,
            f"/ctf/out.txt:1:{FLAG}",
            command='{"flag_pattern": "csictf"}',
            shell="grep -rn csictf /ctf/",
        ),
    ]


class _Reviewer:
    """A scripted disconfirmation pass. Records what it was asked to judge."""

    def __init__(self, sound: bool = True, reason: str = "") -> None:
        self.sound = sound
        self.reason = reason
        self.requests: list[ReviewRequest] = []

    def __call__(self, request: ReviewRequest) -> ReviewVerdict:
        self.requests.append(request)
        return ReviewVerdict(self.sound, self.reason)


def _judge(**kwargs: object) -> FlagJudge:
    defaults: dict[str, object] = {
        "approval_policy": "gated",
        "sandbox": _Executor({"python3 solve.py": f"decoded: {FLAG}"}),
        "reviewer": _Reviewer(),
    }
    defaults.update(kwargs)
    return FlagJudge(**defaults)  # type: ignore[arg-type]


def test_gated_finalizes_a_rederivable_reviewed_candidate() -> None:
    verdict = _judge(flag_format=r"csictf\{\w+\}").judge(
        flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10"
    )
    assert verdict.decision == "finalized"
    assert verdict.provenance_seq == 10
    assert verdict.corroboration == 2


def test_one_sighting_is_enough_when_the_review_is_clean() -> None:
    """D11, amended 2026-09-08: a clean solve finds its flag once.

    The first live bench held four *correct* flags on the old two-sightings
    rule and finalized a wrong one, so corroboration is reported and no longer
    gates (bench/results/README.md).
    """
    history = [_observation(10, f"decoded: {FLAG}")]
    verdict = _judge().judge(flag=FLAG, history=history, fallback_seq=99, provenance="10")
    assert verdict.decision == "finalized"
    assert verdict.corroboration == 1


def test_a_doubtful_review_holds_the_candidate() -> None:
    reviewer = _Reviewer(sound=False, reason="the integer is right but the encoding is not")
    verdict = _judge(reviewer=reviewer).judge(
        flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10"
    )
    assert verdict.decision == "pending"
    assert "encoding" in verdict.reason


def test_no_reviewer_available_holds_rather_than_finalizes() -> None:
    """Fail closed: a check that could not run is not a check that passed."""
    verdict = _judge(reviewer=None).judge(
        flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10"
    )
    assert verdict.decision == "pending"


def test_the_review_sees_the_evidence_and_not_the_whole_run() -> None:
    reviewer = _Reviewer()
    _judge(reviewer=reviewer, description="a crypto challenge").judge(
        flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10",
        how_found="decoded the ciphertext",
    )
    assert len(reviewer.requests) == 1
    request = reviewer.requests[0]
    assert request.flag == FLAG
    assert request.how_found == "decoded the ciphertext"
    assert request.source_command == "python3 solve.py"
    assert FLAG in request.source_output


def test_the_review_runs_last_so_a_rejection_never_costs_tokens() -> None:
    reviewer = _Reviewer()
    _judge(reviewer=reviewer).judge(
        flag=FLAG, history=[_observation(10, "nothing here")], fallback_seq=99, provenance="10"
    )
    assert reviewer.requests == []


def test_provenance_is_mandatory() -> None:
    """D15 §1: a flag the agent will not cite does not get finalized."""
    verdict = _judge().judge(flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="")
    assert verdict.decision == "rejected"
    assert "provenance" in verdict.reason


def test_citing_an_observation_that_does_not_contain_the_flag_is_rejected() -> None:
    verdict = _judge().judge(flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="seq 7")
    assert verdict.decision == "rejected"
    assert "cited" in verdict.reason


def test_citing_the_agents_own_echo_is_rejected() -> None:
    """The failure mode that actually happened, now with a citation attached."""
    history = [
        _observation(
            10,
            FLAG,
            command=f'{{"command": "echo {FLAG}"}}',
            shell=f"echo {FLAG}",
        )
    ]
    verdict = _judge().judge(flag=FLAG, history=history, fallback_seq=99, provenance="10")
    assert verdict.decision == "rejected"
    assert "echoing its own guess" in verdict.reason


def test_a_placeholder_is_rejected_before_anything_expensive() -> None:
    sandbox = _Executor()
    judge = _judge(sandbox=sandbox)
    verdict = judge.judge(
        flag="csictf{flag}",
        history=[_observation(10, "csictf{flag}")],
        fallback_seq=99,
        provenance="10",
    )
    assert verdict.decision == "rejected"
    assert sandbox.calls == []  # no sandbox work spent on an obvious placeholder


def test_a_decoy_named_source_is_rejected() -> None:
    history = [
        _observation(
            10,
            f"{FLAG}",
            command='{"command": "cat /ctf/decoy_flag.txt"}',
            shell="cat /ctf/decoy_flag.txt",
        )
    ]
    verdict = _judge().judge(flag=FLAG, history=history, fallback_seq=99, provenance="10")
    assert verdict.decision == "rejected"
    assert "planted" in verdict.reason


def test_a_token_from_the_description_is_rejected() -> None:
    """Echoing the author's own example back is not a discovery (D15 §3)."""
    verdict = _judge(description=f"The flag looks like {FLAG}").judge(
        flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10"
    )
    assert verdict.decision == "rejected"
    assert "challenge description" in verdict.reason


def test_a_candidate_that_does_not_re_derive_is_held() -> None:
    """D15 §2: re-run the cited command; no flag, no auto-finalize."""
    judge = _judge(sandbox=_Executor({"python3 solve.py": "nondeterministic output"}))
    verdict = judge.judge(flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10")
    assert verdict.decision == "pending"
    assert "rederivation" in verdict.reason


def test_re_derivation_re_runs_the_command_that_actually_ran() -> None:
    sandbox = _Executor({"python3 solve.py": f"decoded: {FLAG}"})
    _judge(sandbox=sandbox).judge(
        flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10"
    )
    assert sandbox.calls == ["python3 solve.py"]


def test_a_flag_format_mismatch_is_held_not_finalized() -> None:
    verdict = _judge(flag_format=r"picoCTF\{\w+\}").judge(
        flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10"
    )
    assert verdict.decision == "pending"
    assert "flag_format" in verdict.reason


def test_no_flag_format_supplied_does_not_block_finalizing() -> None:
    """Otherwise `gated` would silently become `strict` whenever the user
    didn't type a format out — a default nobody chose."""
    verdict = _judge().judge(flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10")
    assert verdict.decision == "finalized"


@pytest.mark.parametrize("policy,expected", [("strict", "pending"), ("auto", "finalized")])
def test_the_approval_policy_decides_only_what_a_cleared_candidate_becomes(
    policy: str, expected: str
) -> None:
    history = [_observation(10, f"decoded: {FLAG}")]  # uncorroborated on purpose
    verdict = _judge(approval_policy=policy).judge(
        flag=FLAG, history=history, fallback_seq=99, provenance="10"
    )
    assert verdict.decision == expected


@pytest.mark.parametrize("policy", ["strict", "auto", "gated"])
def test_provenance_and_decoy_checks_are_unconditional(policy: str) -> None:
    """`--approval auto` buys speed, not the right to submit an invented flag."""
    invented = _judge(approval_policy=policy).judge(
        flag=FLAG, history=[_observation(10, "nothing here")], fallback_seq=99, provenance="10"
    )
    assert invented.decision == "rejected"

    planted = _judge(approval_policy=policy, description=f"e.g. {FLAG}").judge(
        flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10"
    )
    assert planted.decision == "rejected"


def test_a_verdict_records_every_check_it_ran() -> None:
    verdict = _judge().judge(flag=FLAG, history=_corroborated(), fallback_seq=99, provenance="10")
    names = [check.name for check in verdict.checks]
    assert names == [
        "plausibility", "provenance", "decoy", "corroboration", "flag_format",
        "rederivation", "review",
    ]
