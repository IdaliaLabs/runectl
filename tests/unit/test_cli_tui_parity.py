"""Every command the CLI advertises is reachable from the TUI.

Added 2026-09-13. The TUI is a consumer of the CLI (D13's amendment: it shells
out, it never runs the loop in-process), so any command that exists only in the
terminal is a thing a TUI user simply cannot do. Two had drifted that way —
`index rebuild` and `replay` — and the run-wide half of `config` was reachable
in neither, because nothing read it at all.

This test is deliberately structural rather than behavioural: it asserts the
*mapping* is complete, and is the thing that fails when a new CLI command is
added without a TUI route.
"""

from __future__ import annotations

import pytest
from typer.main import get_command

from runectl.cli.app import app
from runectl.cli.tui.app import RunectlTUI
from runectl.cli.tui.commands import _ACTIONS

# Every top-level CLI command, mapped to how the TUI reaches it. A command may
# legitimately map to several routes; what may never happen is no route at all.
_TUI_ROUTE: dict[str, str] = {
    "run": "action_new_run",
    "keys": "action_keys",
    "arena": "action_arena",
    "trace": "action_refresh_runs",  # the timeline pane renders a run's trace
    "flag": "action_refresh_runs",  # the Flags tab approves candidates
    "bench": "action_bench",
    "config": "action_config",
    "models": "action_models",
    "runs": "action_refresh_runs",
    "index": "action_rebuild_index",
    "replay": "action_replay_check",
    # `tui` is the TUI; it does not need a route to itself.
    "tui": "",
}


def _cli_commands() -> set[str]:
    command = get_command(app)
    return set(getattr(command, "commands", {}))


def test_every_cli_command_has_a_tui_route() -> None:
    missing = sorted(name for name in _cli_commands() if name not in _TUI_ROUTE)
    assert not missing, f"CLI commands with no TUI route declared: {missing}"


def test_every_declared_route_actually_exists_on_the_app() -> None:
    for name, action in _TUI_ROUTE.items():
        if not action:
            continue
        assert hasattr(RunectlTUI, action), f"{name} routes to missing {action}"


def test_the_route_table_has_not_gone_stale() -> None:
    """Fails if a CLI command is removed but its route is left behind."""
    stale = sorted(set(_TUI_ROUTE) - _cli_commands())
    assert not stale, f"routes for commands that no longer exist: {stale}"


@pytest.mark.parametrize("action", sorted({a for a in _TUI_ROUTE.values() if a}))
def test_every_route_is_discoverable_without_knowing_its_keybinding(action: str) -> None:
    """A keybinding you have to memorize is not discoverability. Every routed
    action is in the `ctrl+p` palette, which is searchable by name."""
    palette_actions = {entry[1] for entry in _ACTIONS}
    assert action in palette_actions, f"{action} is key-only — add it to commands.py"
