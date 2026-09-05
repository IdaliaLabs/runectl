"""The M4 minimal flag path (D15 seam, plan §7.5): reject a flag with no
provenance in observed tool output, accept one that literally appeared."""

from __future__ import annotations

from runectl.flags.judge import ToolObservation, judge_candidate


def test_accepts_flag_seen_in_tool_output() -> None:
    history = [ToolObservation(seq=3, stdout="flag{abc}", stderr="")]
    decision = judge_candidate(flag="flag{abc}", history=history, fallback_seq=99)
    assert decision.accepted
    assert decision.provenance_seq == 3


def test_rejects_flag_never_observed() -> None:
    history = [ToolObservation(seq=3, stdout="nothing here", stderr="")]
    decision = judge_candidate(flag="flag{invented}", history=history, fallback_seq=99)
    assert not decision.accepted
    assert decision.provenance_seq == 99


def test_prefers_most_recent_matching_observation() -> None:
    history = [
        ToolObservation(seq=1, stdout="flag{old}", stderr=""),
        ToolObservation(seq=5, stdout="flag{old}", stderr=""),
    ]
    decision = judge_candidate(flag="flag{old}", history=history, fallback_seq=99)
    assert decision.accepted
    assert decision.provenance_seq == 5
