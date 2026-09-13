"""Config defaults from inside the TUI (`c`) — mirrors `runectl config`.

Covers both halves of that command: per-provider `model`/`thinking`, and the
run-wide `[run] approval` / `[run] max_cost` wired up 2026-09-13. The run-wide
pair is here because the CLI advertises them and every advertised CLI command
should be reachable from the TUI; leaving them CLI-only would recreate, one
layer up, exactly the gap that made them a silent no-op for four days.

Loads current values in-process (`user_config` is a plain preference file,
not a secret store — the same reads the launcher already does to prefill its
model/thinking fields). Only changed fields are written, each via
`runectl config set`, so an untouched field is never rewritten to its own
current value for no reason.
"""

from __future__ import annotations

from typing import get_args

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from runectl.cli.tui.proc import run_once
from runectl.config import DEFAULT_MAX_COST_USD
from runectl.flags.judge import APPROVAL_POLICIES
from runectl.providers.registry import MODEL_REGISTRY, THINKING_LEVELS, ProviderName
from runectl.user_config import (
    default_approval,
    default_max_cost,
    default_model,
    default_thinking,
)

_PROVIDERS = get_args(ProviderName)


def _fmt_cost(value: float | None) -> str:
    """Render a stored ceiling the same way the Input shows it, so an
    untouched field compares equal and is not rewritten for no reason."""
    return "" if value is None else f"{value:g}"


class ConfigScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ConfigScreen {
        align: center middle;
    }
    #config-box {
        width: 70;
        height: auto;
        max-height: 90%;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="config-box"):
            yield Static("[b]Per-provider defaults[/b] — prefills --model/--thinking; never relaxes D5")
            for provider in _PROVIDERS:
                model_options = [
                    (f"{m.id} · ${m.price_in:g}/${m.price_out:g}", m.id)
                    for m in sorted(
                        MODEL_REGISTRY.values(), key=lambda m: m.price_in + m.price_out
                    )
                    if m.provider == provider
                ]
                yield Label(f"{provider} default model")
                yield Select(
                    model_options,
                    value=default_model(provider) or Select.NULL,
                    id=f"model-{provider}",
                    allow_blank=True,
                )
                yield Label(f"{provider} default thinking")
                yield Select(
                    [(level, level) for level in THINKING_LEVELS],
                    value=default_thinking(provider),
                    id=f"thinking-{provider}",
                    allow_blank=False,
                )
            yield Static("[b]Run-wide defaults[/b] — an explicit flag always wins over these")
            yield Label("default approval policy")
            yield Select(
                [(policy, policy) for policy in APPROVAL_POLICIES],
                value=default_approval() or Select.NULL,
                id="run-approval",
                allow_blank=True,
            )
            yield Label(f"default max cost in USD (0 = no ceiling, unset = {DEFAULT_MAX_COST_USD})")
            yield Input(
                value=_fmt_cost(default_max_cost()),
                placeholder=f"{DEFAULT_MAX_COST_USD:g}",
                id="run-max-cost",
            )
            yield Static("", id="config-message")
            yield Button("Save", id="save", variant="primary")
            yield Button("Close", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss(None)
        elif event.button.id == "save":
            self.run_worker(self._save(), exclusive=False)

    async def _save(self) -> None:
        written = 0
        for provider in _PROVIDERS:
            model_value = self.query_one(f"#model-{provider}", Select).value
            if model_value and model_value != default_model(provider):
                await run_once(["config", "set", f"{provider}.model", str(model_value)])
                written += 1
            thinking_value = self.query_one(f"#thinking-{provider}", Select).value
            if thinking_value and thinking_value != default_thinking(provider):
                await run_once(["config", "set", f"{provider}.thinking", str(thinking_value)])
                written += 1

        approval_value = self.query_one("#run-approval", Select).value
        if approval_value and approval_value != default_approval():
            await run_once(["config", "set", "run.approval", str(approval_value)])
            written += 1
        # Written through the same `runectl config set` the CLI uses, so its
        # validation (and its exit 6 on a bad value) is the only validator —
        # a second one here would be a second place to get it wrong.
        cost_value = self.query_one("#run-max-cost", Input).value.strip()
        if cost_value and cost_value != _fmt_cost(default_max_cost()):
            # Compared as text, not parsed: a non-numeric entry must reach the
            # CLI so *its* error surfaces here, rather than raising ValueError
            # inside this worker where the user would see nothing at all.
            exit_code, output = await run_once(["config", "set", "run.max_cost", cost_value])
            if exit_code != 0:
                self.query_one("#config-message", Static).update(output.strip()[:200])
                return
            written += 1

        self.query_one("#config-message", Static).update(
            f"saved {written} change(s)" if written else "nothing changed"
        )
