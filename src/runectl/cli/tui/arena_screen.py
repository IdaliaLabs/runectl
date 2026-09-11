"""Manage the arena sandbox image from inside the TUI (`a`).

Status is read in-process via `arena_build.inspect()` (a pure read, same call
the app's own startup preflight already makes). Building or loading one is
never done silently (D17) — it only happens from an explicit button press
here, and even then by suspending the TUI and running the real
`runectl arena build`/`ensure` in the foreground: the same command a human
would type, with its own real (often slow, always worth watching) output
going straight to the real terminal instead of a custom log widget.
"""

from __future__ import annotations

import sys

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from runectl.cli.tui.proc import run_foreground
from runectl.errors import SandboxError
from runectl.sandbox import arena_build


class ArenaScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ArenaScreen {
        align: center middle;
    }
    #arena-box {
        width: 76;
        height: auto;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="arena-box"):
            yield Static(id="arena-status")
            with Horizontal():
                yield Button("Build", id="build", variant="primary")
                yield Button("Load from file", id="from-file")
                yield Button("Pull from registry", id="from-registry")
            yield Input(placeholder="path or registry reference", id="arena-ref")
            yield Static("", id="arena-message")
            yield Button("Close", id="close")

    def on_mount(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        status_widget = self.query_one("#arena-status", Static)
        try:
            status = arena_build.inspect()
        except SandboxError as exc:
            status_widget.update(f"[red]{exc}[/red]")
            return
        if not status.present:
            status_widget.update(f"[yellow]{status.tag} is not built yet[/yellow]")
            return
        lines = [f"[b]{status.tag}[/b] -> {status.image_id}"]
        if status.stale:
            lines.append("[yellow]STALE — rebuild to pick up the current Dockerfile[/yellow]")
        if status.architecture_mismatch:
            lines.append(
                f"[red]built for {status.architecture}, "
                f"expected {status.expected_architecture}[/red]"
            )
        status_widget.update("\n".join(lines))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "close":
            self.dismiss(None)
        elif button_id == "build":
            self._run_ensure(["--build", "--force"])
        elif button_id == "from-file":
            self._run_from_input(["--from-file"])
        elif button_id == "from-registry":
            self._run_from_input(["--from-registry"])

    def _run_from_input(self, flag: list[str]) -> None:
        value = self.query_one("#arena-ref", Input).value.strip()
        if not value:
            self.query_one("#arena-message", Static).update("enter a path or reference first")
            return
        self._run_ensure([*flag, value])

    def _run_ensure(self, extra_argv: list[str]) -> None:
        message = self.query_one("#arena-message", Static)
        message.update("running — check your terminal")
        with self.app.suspend():
            exit_code = run_foreground(
                [sys.executable, "-m", "runectl", "arena", "ensure", *extra_argv]
            )
        message.update("done" if exit_code == 0 else f"exit {exit_code}")
        self._refresh_status()
