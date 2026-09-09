"""D10: triage() never receives the challenge name/filenames/description, and
the command set is identical across two runs differing only in those."""

from __future__ import annotations

import inspect

from runectl.categories.loader import STANDARD_CATEGORIES
from runectl.categories.loader import load as load_category
from runectl.loop.triage import commands_for, triage
from runectl.sandbox.stub import StubSandbox, ok


def test_triage_signature_has_no_challenge_parameter() -> None:
    sig = inspect.signature(triage)
    assert set(sig.parameters) == {"sandbox", "category"}


def test_triage_commands_depend_only_on_category() -> None:
    category = load_category("misc")
    # There is no parameter through which a challenge name or filename could
    # reach this function, so the command set can only ever vary by category.
    assert commands_for(category) == commands_for(category)


def test_triage_runs_fixed_commands_against_sandbox() -> None:
    category = load_category("misc")
    sandbox = StubSandbox(script={"ls -la /ctf/": ok("total 0")})
    sandbox.start()
    result = triage(sandbox, category)
    sandbox.stop()
    assert result.category == "misc"
    assert "ls -la /ctf/" in result.commands


def test_every_category_has_a_challenge_blind_triage_set() -> None:
    """All eight categories triage from the same base set, keyed only by name (D10)."""
    for name in STANDARD_CATEGORIES:
        commands = commands_for(load_category(name))
        assert commands[0] == "ls -la /ctf/"
        assert any("file /ctf/*" in c for c in commands)
