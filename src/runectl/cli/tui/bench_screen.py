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

from runectl.cli.tui.models import model_options, preferred_model
from runectl.cli.tui.proc import run_foreground


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
        options = model_options()
        preferred = preferred_model(options)

        with VerticalScroll(id="bench-box"):
            yield Static("[b]Run the bench suite[/b] — output goes to your real terminal")
            yield Input(value="bench/practice", id="suite")
            yield Select(options, value=preferred, id="model", allow_blank=False)
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
