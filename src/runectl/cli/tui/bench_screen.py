"""Run the capability benchmark from inside the TUI (`b`).

Composes and runs the real `runectl bench run` in the foreground (suspending
the TUI first) rather than reparsing its output into a widget: a bench run's
own progress report is already meant to be read as a scrolling terminal
stream, and the CLI doesn't emit it as structured events the way `run` does
— there is nothing here to parse without inventing a second output format.
"""

from __future__ import annotations

import sys

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from runectl.cli.tui.proc import run_foreground
from runectl.providers.registry import MODEL_REGISTRY
from runectl.user_config import default_model


class BenchScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    BenchScreen {
        align: center middle;
    }
    #bench-box {
        width: 76;
        height: auto;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        model_options = [
            (f"{m.id}  ({m.provider})", m.id)
            for m in sorted(MODEL_REGISTRY.values(), key=lambda m: (m.provider, m.id))
        ]
        preferred_model = default_model("anthropic") or (model_options[0][1] if model_options else None)

        with VerticalScroll(id="bench-box"):
            yield Static("[b]Run the bench suite[/b] — output goes to your real terminal")
            yield Input(value="bench/practice", id="suite")
            yield Select(model_options, value=preferred_model, id="model", allow_blank=False)
            yield Input(value="0", placeholder="max total cost, USD (0 disables)", id="max-total-cost")
            with Horizontal():
                yield Button("Run", id="run", variant="primary")
                yield Button("Close", id="close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            self.dismiss(None)
        elif event.button.id == "run":
            self._run()

    def _run(self) -> None:
        suite = self.query_one("#suite", Input).value.strip() or "bench/practice"
        model = self.query_one("#model", Select).value
        max_total_cost = self.query_one("#max-total-cost", Input).value.strip() or "0"
        if not model:
            return
        argv = [
            sys.executable, "-m", "runectl", "bench", "run",
            "--suite", suite, "--model", str(model), "--max-total-cost", max_total_cost,
        ]
        with self.app.suspend():
            run_foreground(argv)
        self.dismiss(None)
