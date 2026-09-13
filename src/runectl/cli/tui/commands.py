"""The command palette (`ctrl+p`, already bound by Textual) — every action the
TUI has, searchable by name. Nothing here does anything itself; each entry
just calls the matching `RunectlTUI.action_*` method, so this can never drift
from what the keybindings already do."""

from __future__ import annotations

from textual.command import DiscoveryHit, Hit, Hits, Provider

# (name, action method name, help text)
_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("New run", "action_new_run", "Compose and launch a new challenge run"),
    ("Refresh runs", "action_refresh_runs", "Reload the run list from disk"),
    ("Manage API keys", "action_keys", "View, set, or remove a provider key"),
    ("Manage arena image", "action_arena", "Build, load, or pull the sandbox image"),
    ("Configure defaults", "action_config", "Default model, thinking, approval policy and spend ceiling"),
    ("Browse model registry", "action_models", "Every registered model, its price, and thinking support"),
    ("Run bench suite", "action_bench", "Score a challenge suite against expected.json"),
    ("Attach to run's container", "action_attach", "docker exec -it into the selected run"),
    ("Kill live run", "action_kill_run", "Terminate a run this session launched"),
    ("Rebuild index", "action_rebuild_index", "Refresh the derived SQLite index `runectl runs list` reads"),
    (
        "Replay selected run",
        "action_replay_check",
        "Re-run it from its cassette and check it matches — no spend",
    ),
    ("Help", "action_help", "What everything on this screen does"),
    ("Quit", "action_quit", "Exit runectl tui"),
)


class RunectlCommands(Provider):
    async def discover(self) -> Hits:
        for name, action, help_text in _ACTIONS:
            yield DiscoveryHit(name, getattr(self.app, action), help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, action, help_text in _ACTIONS:
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    getattr(self.app, action),
                    help=help_text,
                )
