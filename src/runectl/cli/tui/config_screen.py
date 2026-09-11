"""Per-provider config defaults from inside the TUI (`c`) — mirrors `runectl config`.

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
from textual.widgets import Button, Label, Select, Static

from runectl.cli.tui.proc import run_once
from runectl.providers.registry import MODEL_REGISTRY, THINKING_LEVELS, ProviderName
from runectl.user_config import default_model, default_thinking

_PROVIDERS = get_args(ProviderName)


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
                    (m.id, m.id)
                    for m in sorted(MODEL_REGISTRY.values(), key=lambda m: m.id)
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
        self.query_one("#config-message", Static).update(
            f"saved {written} change(s)" if written else "nothing changed"
        )
