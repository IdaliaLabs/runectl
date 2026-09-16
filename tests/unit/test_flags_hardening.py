"""Hardening the D15 false-flag subsystem: wrapper attestation, anti-echo
coverage across history, trailing-junk rejection, the `auto` policy's
relationship to `--flag-format`, the `search_flag` grep shape, and decoy
taunt detection.

These encode fixes, not current behavior — some are expected to fail until
the underlying checks are tightened.
"""

from __future__ import annotations

import shlex

import pytest

from runectl.flags.judge import FlagJudge, ToolObservation
from runectl.sandbox.base import ExecResult
from runectl.sandbox.stub import StubSandbox, ok
from runectl.tools.dispatch import ToolDispatcher


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


# -- 1. wrapper attestation is case-sensitive and structural -----------------

_INVENTED_FLAG = "flag{deadbeef1234}"
_INVENTED_PAYLOAD = "deadbeef1234"


def test_generic_flag_word_does_not_attest_an_invented_wrapper() -> None:
    # Criterion 1: the bare word "flag" in a description is not "flag{" attestation.
    history = [_observation(5, f"leaked: {_INVENTED_PAYLOAD}", shell="python3 solve.py")]
    verdict = FlagJudge(
        approval_policy="auto",
        description="Find the flag in the binary.",
    ).judge(flag=_INVENTED_FLAG, history=history, fallback_seq=99, provenance="5")
    assert verdict.decision != "finalized"


def test_wrapper_named_in_description_supports_payload_only_provenance() -> None:
    # Criterion 1: a wrapper actually spelled out (e.g. "csictf{") still attests.
    flag = "csictf{deadbeef1234}"
    payload = "deadbeef1234"
    history = [_observation(5, payload, shell="python3 solve.py")]
    verdict = FlagJudge(
        approval_policy="auto",
        description="Flags look like csictf{...}.",
        sandbox=_Executor({"python3 solve.py": payload}),
    ).judge(flag=flag, history=history, fallback_seq=99, provenance="5")
    assert verdict.decision == "finalized"


def test_wrapper_attestation_is_case_sensitive() -> None:
    # Criterion 1: "CSICTF{" in the description must not attest "csictf{".
    flag = "csictf{deadbeef1234}"
    payload = "deadbeef1234"
    history = [_observation(5, payload, shell="python3 solve.py")]
    verdict = FlagJudge(
        approval_policy="auto",
        description="Flags look like CSICTF{...}.",
        sandbox=_Executor({"python3 solve.py": payload}),
    ).judge(flag=flag, history=history, fallback_seq=99, provenance="5")
    assert verdict.decision != "finalized"


def test_flag_format_alone_can_attest_the_wrapper() -> None:
    # Criterion 1: the spec names "description or flag_format" — a wrapper
    # stated only in --flag-format (normal escaped-brace regex) must attest too.
    flag = "zctf{deadbeef1234}"
    payload = "deadbeef1234"
    history = [_observation(5, payload, shell="python3 solve.py")]
    verdict = FlagJudge(
        approval_policy="auto",
        flag_format=r"zctf\{[\w]{3,60}\}",
        sandbox=_Executor({"python3 solve.py": payload}),
    ).judge(flag=flag, history=history, fallback_seq=99, provenance="5")
    assert verdict.decision == "finalized"


# -- 2 & 3. anti-echo covers earlier history, including non-ASCII ------------

_SELF_AUTHORED_FLAG = "flag{selfauthored12}"


def test_earlier_self_authored_payload_taints_a_later_clean_looking_citation() -> None:
    # Criterion 2: anti-echo must scan every earlier observation, not just the cited one.
    history = [
        ToolObservation(
            seq=10,
            stdout="wrote guess",
            stderr="",
            command=f'{{"filename": "guess.txt", "content": "{_SELF_AUTHORED_FLAG}"}}',
            tool="write_file",
            shell_command=f"echo '{_SELF_AUTHORED_FLAG}' > guess.txt",
        ),
        ToolObservation(
            seq=20,
            stdout=f"contents: {_SELF_AUTHORED_FLAG}",
            stderr="",
            command='{"command": "cat guess.txt"}',
            tool="run_command",
            shell_command="cat guess.txt",
        ),
    ]
    verdict = FlagJudge().judge(
        flag=_SELF_AUTHORED_FLAG, history=history, fallback_seq=99, provenance="20"
    )
    assert verdict.decision != "finalized"


_UNICODE_FLAG = "flag{ünïcødé_payload}"


def test_anti_echo_catches_a_non_ascii_payload_written_earlier() -> None:
    # Criterion 3: anti-echo must survive non-ASCII / escaped payloads.
    history = [
        ToolObservation(
            seq=1,
            stdout="",
            stderr="",
            command=f'{{"command": "echo {_UNICODE_FLAG}"}}',
            tool="run_command",
            shell_command=f"echo {_UNICODE_FLAG}",
        ),
        ToolObservation(
            seq=2,
            stdout=f"result: {_UNICODE_FLAG}",
            stderr="",
            command='{"command": "cat out.txt"}',
            tool="run_command",
            shell_command="cat out.txt",
        ),
    ]
    verdict = FlagJudge().judge(flag=_UNICODE_FLAG, history=history, fallback_seq=99, provenance="2")
    assert verdict.decision != "finalized"


# -- 4. trailing junk is not accepted -----------------------------------------


@pytest.mark.parametrize("junk", [".", "\n"], ids=["trailing-dot", "trailing-newline"])
def test_trailing_junk_on_the_candidate_is_not_accepted(junk: str) -> None:
    # Criterion 4: format matching is a full match; the raw flag must be clean.
    # Wrapper attestation is via the description (flag_format text is escaped
    # regex, not literal "flag{", so it would not attest on its own — see the
    # flag_format-alone test above); this isolates the trailing-junk check.
    clean_flag = "flag{real_payload_x}"
    dirty_flag = clean_flag + junk
    history = [_observation(1, f"decoded: {clean_flag} done", shell="python3 solve.py")]
    verdict = FlagJudge(
        approval_policy="auto",
        description="Flags here look like flag{...}.",
        flag_format=r"flag\{.+\}",
        sandbox=_Executor({"python3 solve.py": f"decoded: {clean_flag} done"}),
    ).judge(flag=dirty_flag, history=history, fallback_seq=99, provenance="1")
    assert verdict.decision != "finalized"


def test_trailing_newline_does_not_slip_through_without_an_explicit_format() -> None:
    # Criterion 4: "trailing junk and whitespace are not accepted" is a general
    # rule, not one contingent on --flag-format being supplied to catch it.
    clean_flag = "flag{real_payload_x}"
    dirty_flag = clean_flag + "\n"
    history = [_observation(1, f"decoded: {clean_flag} done", shell="python3 solve.py")]
    verdict = FlagJudge(
        approval_policy="auto",
        description="Flags here look like flag{...}.",
        sandbox=_Executor({"python3 solve.py": f"decoded: {clean_flag} done"}),
    ).judge(flag=dirty_flag, history=history, fallback_seq=99, provenance="1")
    assert verdict.decision != "finalized"


# -- 5. `auto` still respects an explicit flag_format -------------------------

_FLAG = "csictf{you_are_a_basic_person}"


def test_auto_holds_a_flag_format_mismatch_instead_of_finalizing() -> None:
    # Criterion 5: `auto` does not bypass a supplied --flag-format that fails.
    history = [_observation(10, f"decoded: {_FLAG}", shell="python3 solve.py")]
    verdict = FlagJudge(
        approval_policy="auto",
        flag_format=r"picoCTF\{\w+\}",
        sandbox=_Executor({"python3 solve.py": f"decoded: {_FLAG}"}),
    ).judge(flag=_FLAG, history=history, fallback_seq=99, provenance="10")
    assert verdict.decision == "pending"


def test_auto_without_flag_format_still_finalizes_a_plausible_cited_flag() -> None:
    # Criterion 5: no --flag-format at all is not the same as a failed one.
    history = [_observation(10, f"decoded: {_FLAG}", shell="python3 solve.py")]
    verdict = FlagJudge(
        approval_policy="auto",
        sandbox=_Executor({"python3 solve.py": f"decoded: {_FLAG}"}),
    ).judge(flag=_FLAG, history=history, fallback_seq=99, provenance="10")
    assert verdict.decision == "finalized"


# -- 6. `search_flag` grep shape ----------------------------------------------


def test_search_flag_returns_full_lines_and_excludes_the_live_log() -> None:
    # Criterion 6: no standalone -o (only-matching), and .agent_live.log is excluded.
    stub = StubSandbox(default=ok(""))
    stub.start()
    result = ToolDispatcher(stub).dispatch("search_flag", {"flag_pattern": "picoCTF{"})
    tokens = shlex.split(result.shell_command)
    assert "-o" not in tokens
    assert ".agent_live.log" in result.shell_command
    assert "exclude" in result.shell_command.lower()


# -- 7. decoy detection is about the taunt, not the payload's spelling -------


def test_a_taunt_around_a_payload_is_still_caught_as_a_decoy() -> None:
    # Criterion 7: a payload sighted next to "nice try, not the flag" is a decoy.
    # Wrapper attestation is via the description (see the flag_format-alone
    # test in criterion 1) so this isolates the decoy-taunt check itself.
    flag = "csictf{legit_but_teased}"
    payload = "legit_but_teased"
    history = [
        _observation(
            1,
            f"nice try, not the flag: {payload}",
            command='{"command": "cat trap.txt"}',
            shell="cat trap.txt",
        )
    ]
    verdict = FlagJudge(
        description="Flags look like csictf{...}.",
        sandbox=_Executor({"cat trap.txt": f"nice try, not the flag: {payload}"}),
    ).judge(flag=flag, history=history, fallback_seq=99, provenance="1")
    assert verdict.decision == "rejected"


def test_a_decoy_word_inside_a_genuine_payload_is_not_rejected() -> None:
    # Criterion 7: "almost" inside the payload itself is not a taunt.
    flag = "flag{almost_there_but_real}"
    history = [_observation(1, f"decoded: {flag}", shell="python3 solve.py")]
    verdict = FlagJudge(
        sandbox=_Executor({"python3 solve.py": f"decoded: {flag}"}),
    ).judge(flag=flag, history=history, fallback_seq=99, provenance="1")
    assert verdict.decision == "finalized"
