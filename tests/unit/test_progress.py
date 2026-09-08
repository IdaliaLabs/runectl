"""D8: the four progress mechanisms. No model, no sandbox, no spend."""

from __future__ import annotations

from runectl.categories.loader import load as load_category
from runectl.progress.budgets import BudgetLedger, hypothesis_key
from runectl.progress.families import classify
from runectl.progress.fingerprint import fingerprint, normalize
from runectl.progress.signal import score
from runectl.progress.tracker import ProgressTracker

# --- mechanism 1: tactic families -------------------------------------------

def test_commands_classify_into_hypothesis_classes() -> None:
    assert classify("ffuf -w list.txt -u http://x/FUZZ") == "dirfuzz"
    assert classify("sqlmap -u http://x?id=1") == "sqli"
    assert classify("echo aGk= | base64 -d") == "decode"
    assert classify("gdb -batch -ex 'info functions' ./vuln") == "disasm"
    assert classify("john --wordlist=rockyou hash.txt") == "crack"
    assert classify("tshark -r capture.pcap") == "pcap-filter"
    assert classify("wat") == "other"


def test_a_category_can_claim_a_command_the_defaults_would_generalise() -> None:
    """Overrides win, so a category can sharpen the shared set (D8)."""
    assert classify("python3 solve.py", overrides={"rsa": r"solve\.py"}) == "rsa"


def test_a_broken_category_regex_does_not_take_the_run_down() -> None:
    assert classify("ffuf -u x", overrides={"bad": "([unclosed"}) == "dirfuzz"


# --- mechanism 2: output fingerprinting -------------------------------------

def test_volatile_parts_are_normalised_away() -> None:
    a = "Date: Mon, 08 Sep 2026 03:34:07 GMT\nsegfault at 0x7ffd1234 pid=4821 in 0.53s"
    b = "Date: Tue, 09 Sep 2026 11:02:44 GMT\nsegfault at 0x7ffe9999 pid=9137 in 1.24s"

    assert fingerprint(a) == fingerprint(b), normalize(a)


def test_genuinely_different_output_fingerprints_differently() -> None:
    assert fingerprint("HTTP/1.1 404 Not Found") != fingerprint("HTTP/1.1 200 OK")


def test_whitespace_only_differences_collapse() -> None:
    assert fingerprint("a   b\n\nc") == fingerprint("a b c")


# --- mechanism 3: signal scoring --------------------------------------------

def test_a_repeat_is_never_progress_however_good_it_looks() -> None:
    delta, band = score("flag{real_looking}", ok=True, repeated=True)

    assert delta == 0.0
    assert band == "none"


def test_flag_shaped_output_scores_high() -> None:
    _, band = score("picoCTF{abc_def}", ok=True, repeated=False)
    assert band == "high"


def test_the_classic_time_wasters_score_low() -> None:
    for text in ("HTTP/1.1 404 Not Found", "", "bash: nope: command not found"):
        _, band = score(text, ok=True, repeated=False)
        assert band == "low", text


def test_a_category_can_add_its_own_high_signal_pattern() -> None:
    _, band = score("ADMIN CONSOLE", ok=True, repeated=False, high_patterns=(r"ADMIN CONSOLE",))
    assert band == "high"


# --- mechanism 4: budgets ---------------------------------------------------

def test_retrying_the_same_idea_gets_blocked() -> None:
    ledger = BudgetLedger(per_hypothesis=2, per_family=99)
    cmd = "ffuf -w /usr/share/wordlists/big.txt -u http://x/FUZZ"

    for _ in range(2):
        assert not ledger.check(cmd, "dirfuzz").blocked
        ledger.record(cmd, "dirfuzz", progressed=False)

    verdict = ledger.check(cmd, "dirfuzz")
    assert verdict.blocked
    assert "Change the hypothesis" in verdict.reason


def test_a_whole_tactic_family_gets_blocked_even_with_varied_commands() -> None:
    """Four different fuzzers that each find nothing is still one failed idea."""
    ledger = BudgetLedger(per_hypothesis=99, per_family=3)

    for tool in ("ffuf -u a", "gobuster dir -u b", "wfuzz -u c"):
        assert not ledger.check(tool, "dirfuzz").blocked
        ledger.record(tool, "dirfuzz", progressed=False)

    verdict = ledger.check("dirb http://d", "dirfuzz")
    assert verdict.blocked
    assert "different class of idea" in verdict.reason


def test_progress_clears_the_counters() -> None:
    ledger = BudgetLedger(per_hypothesis=1, per_family=1)
    ledger.record("ffuf -u a", "dirfuzz", progressed=False)
    ledger.record("ffuf -u a", "dirfuzz", progressed=True)

    assert not ledger.check("ffuf -u a", "dirfuzz").blocked


def test_hypothesis_key_ignores_the_arguments_people_tweak() -> None:
    a = hypothesis_key("ffuf -w '/usr/share/wordlists/directory-list-2.3-big.txt' -u http://x")
    b = hypothesis_key("ffuf -w '/usr/share/wordlists/directory-list-1.0-small.txt' -u http://x")
    assert a == b


# --- the tracker, end to end ------------------------------------------------

def test_tracker_scores_repeats_as_no_progress() -> None:
    tracker = ProgressTracker(category=load_category("web"))

    first = tracker.record("curl http://x", "HTTP/1.1 200 OK\nbody", ok=True, step=1)
    second = tracker.record("curl http://x", "HTTP/1.1 200 OK\nbody", ok=True, step=2)

    assert first.progressed
    assert not second.progressed
    assert second.repeated


def test_a_forced_strategy_shift_carries_the_evidence() -> None:
    category = load_category("web")
    tracker = ProgressTracker(category=category)

    for step in range(1, category.budgets.no_progress_shift + 1):
        tracker.record(f"curl http://x/{step}", "HTTP/1.1 404 Not Found", ok=True, step=step)

    shift = tracker.due_shift()

    assert shift is not None
    assert "different hypothesis class" in shift
    assert "404" in shift  # the evidence, not just the scolding


def test_the_shift_fires_once_per_crossing_not_every_step() -> None:
    category = load_category("web")
    tracker = ProgressTracker(category=category)
    for step in range(1, category.budgets.no_progress_shift + 1):
        tracker.record(f"curl http://x/{step}", "404 Not Found", ok=True, step=step)

    assert tracker.due_shift() is not None
    assert tracker.due_shift() is None


def test_consecutive_errors_also_force_a_shift() -> None:
    category = load_category("crypto")
    tracker = ProgressTracker(category=category)

    for step in range(1, category.budgets.consecutive_error_shift + 1):
        tracker.record(f"python3 x{step}.py", "Traceback...", ok=False, step=step)

    assert tracker.due_shift() is not None


def test_blocked_steps_count_toward_the_forced_shift() -> None:
    """Regression: without this the counter freezes exactly when budgets bite,
    so the agent could hammer a blocked idea forever without being redirected."""
    category = load_category("web")
    tracker = ProgressTracker(category=category)

    for step in range(1, category.budgets.no_progress_shift + 1):
        tracker.note_blocked("dirfuzz", "family exhausted", step=step)

    shift = tracker.due_shift()

    assert shift is not None
    assert "BLOCKED" in shift
