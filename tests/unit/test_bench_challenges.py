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


def test_the_suite_has_five_challenges() -> None:
    """The V1 gate is 2 of 5 (plan §10.1) — it needs five to be 2 of 5."""
    assert len(CASES) == 5


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
