"""The vendored suite itself: loadable, complete, and free of its own answers."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from runectl.bench.suite import BenchCase, load_suite
from runectl.categories.loader import load as load_category
from runectl.cli.run_cmd import challenge_from_file

SUITE = Path(__file__).resolve().parents[2] / "bench" / "practice"
CASES = load_suite(SUITE)


def test_the_suite_covers_the_expected_categories() -> None:
    """M7 (2026-09-09) grew the suite from 5 to 10 so every category the tool
    can measure offline is represented. Two of the additions (pwn, osint) sit
    outside the gate for structural reasons recorded in their expected.json."""
    assert len(CASES) == 10
    categories = {case.category for case in CASES}
    assert {"crypto", "misc", "rev", "forensics", "network", "pwn", "osint"} <= categories
    # Gate composition: quick-math (crypto), pwn and osint are advisory-only.
    outside = {case.name for case in CASES if not case.in_gate}
    assert outside == {"quick-math", "pwn-intended-0x1", "flying-places"}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_every_challenge_loads_and_its_files_exist(case: BenchCase) -> None:
    challenge = challenge_from_file(case.challenge_path)
    assert challenge.name and challenge.description.strip()
    load_category(challenge.category)  # raises if the category isn't shipped
    for file in challenge.files:
        # Resolved relative to the TOML, so this passes from any working directory.
        assert file.exists(), file


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_a_challenge_never_carries_its_own_answer(case: BenchCase) -> None:
    """D10: the answer key lives in expected.json and nowhere a run can reach it.

    `easy-05`'s flag *is* inside a vendored file — encrypted in a password-
    protected zip, which is the challenge — so this checks the text a run is
    handed, not every byte on disk.
    """
    raw = tomllib.loads(case.challenge_path.read_text())
    assert case.expected_flag not in raw.get("description", "")
    assert case.expected_flag not in case.challenge_path.read_text()


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_every_challenge_records_where_it_came_from(case: BenchCase) -> None:
    provenance = (case.directory / "PROVENANCE.md").read_text()
    assert "License" in provenance
    assert "Commit" in provenance


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_the_flag_format_matches_the_expected_flag(case: BenchCase) -> None:
    """A format that rejects the real answer would hold every correct solve (D11)."""
    import re

    raw = tomllib.loads(case.challenge_path.read_text())
    flag_format = raw.get("flag_format")
    if flag_format:
        assert re.search(flag_format, case.expected_flag), case.name


def test_pointing_challenge_at_the_directory_is_a_usage_error_not_a_traceback() -> None:
    """D4: honest exit codes, no tracebacks at a user for a mistyped path.

    Hit for real on 2026-09-13 — `--challenge bench/practice/easy-03` is the
    natural thing to type, since the directory is the unit a challenge is
    vendored as, and it raised a raw IsADirectoryError through the whole Typer
    stack. A driving agent (D4's premise) cannot act on a traceback.
    """
    from runectl.errors import UsageError

    directory = Path(__file__).resolve().parents[2] / "bench" / "practice" / "easy-03"
    assert directory.is_dir()

    with pytest.raises(UsageError) as caught:
        challenge_from_file(directory)
    assert caught.value.exit_code == 6
    assert "chal.toml" in str(caught.value)  # names the file it wanted

    with pytest.raises(UsageError) as missing:
        challenge_from_file(directory / "does-not-exist.toml")
    assert missing.value.exit_code == 6
