"""Regression: the agent must not be able to launder its own guess into evidence.

The first real run of runectl (20260908-033407-cfa0ad) printed a guessed flag
from inside its own script, and the judge accepted it because the string did
appear "verbatim in tool output". A wrong flag scores worse than no flag, so
this is the failure the judge exists to prevent.
"""

from __future__ import annotations

from runectl.flags.judge import ToolObservation, judge_candidate

FLAG = "csictf{9f1ff1d8e8}"


def test_echoing_your_own_guess_is_not_evidence() -> None:
    observation = ToolObservation(
        seq=53,
        stdout=f"Possible flags:\n1. {FLAG}\n",
        stderr="",
        command=f'{{"command": "python3 -c \'print(\\"{FLAG}\\")\'"}}',
    )

    decision = judge_candidate(flag=FLAG, history=[observation], fallback_seq=99)

    assert not decision.accepted
    assert "echoing its own guess" in decision.reason


def test_a_genuine_derivation_is_still_accepted() -> None:
    """The flag comes out of the computation, not out of the command text."""
    observation = ToolObservation(
        seq=40,
        stdout=f"decoded message: {FLAG}\n",
        stderr="",
        command='{"command": "python3 /ctf/solve.py"}',
    )

    decision = judge_candidate(flag=FLAG, history=[observation], fallback_seq=99)

    assert decision.accepted
    assert decision.provenance_seq == 40


def test_an_earlier_real_finding_survives_a_later_echo() -> None:
    """Skipping self-authored observations must not discard genuine ones."""
    history = [
        ToolObservation(seq=40, stdout=f"decoded: {FLAG}", stderr="", command='{"c": "solve.py"}'),
        ToolObservation(seq=53, stdout=FLAG, stderr="", command=f'{{"c": "echo {FLAG}"}}'),
    ]

    decision = judge_candidate(flag=FLAG, history=history, fallback_seq=99)

    assert decision.accepted
    assert decision.provenance_seq == 40


def test_never_observed_at_all_is_still_rejected() -> None:
    decision = judge_candidate(flag=FLAG, history=[], fallback_seq=7)

    assert not decision.accepted
    assert "does not appear" in decision.reason


def test_empty_flag_is_rejected() -> None:
    assert not judge_candidate(flag="", history=[], fallback_seq=1).accepted


def test_fstring_brace_escaping_does_not_evade_the_check() -> None:
    """The real run printed its guess via an f-string, so the command contained
    doubled braces and the literal flag never appeared in it. Matching on the
    flag's payload is what closes that hole."""
    observation = ToolObservation(
        seq=53,
        stdout=f"Possible flags:\n1. {FLAG}\n",
        stderr="",
        command='{"command": "print(f\\"1. csictf{{9f1ff1d8e8}}\\")"}',
    )

    decision = judge_candidate(flag=FLAG, history=[observation], fallback_seq=99)

    assert not decision.accepted
    assert "echoing its own guess" in decision.reason


def test_a_short_core_is_not_treated_as_authorship() -> None:
    """`flag{a}` has a payload too generic to prove the agent wrote it."""
    observation = ToolObservation(
        seq=10, stdout="flag{a}", stderr="", command='{"command": "cat /ctf/a.txt"}'
    )

    assert judge_candidate(flag="flag{a}", history=[observation], fallback_seq=99).accepted


def test_a_flag_without_braces_still_works() -> None:
    observation = ToolObservation(
        seq=11, stdout="ABCD1234EFGH", stderr="", command='{"command": "strings /ctf/bin"}'
    )

    assert judge_candidate(flag="ABCD1234EFGH", history=[observation], fallback_seq=99).accepted
