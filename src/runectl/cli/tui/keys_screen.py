"""Manage provider API keys from inside the TUI (`k`).

Reads presence with `providers.keys.list_keys()` in-process (a pure read,
same as the app's own startup preflight). Every mutation shells out to the
real `runectl keys set|rm` — see `proc.py`'s module docstring for why.
"""

from __future__ import annotations

from typing import get_args

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from runectl.cli.tui.proc import run_once
from runectl.providers.keys import list_keys
from runectl.providers.registry import ProviderName

_PROVIDERS = get_args(ProviderName)


class KeysScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    KeysScreen {
        align: center middle;
    }
    #keys-box {
        width: 70;
        height: auto;
        border: heavy $primary;
        padding: 1 2;
        background: $surface;
    }
    .provider-row Input {
        width: 1fr;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="keys-box"):
            yield Static("[b]API keys[/b] — stored in your OS keyring, or a 0600 file as fallback")
            for provider in _PROVIDERS:
                yield Static(id=f"status-{provider}")
                with Horizontal(classes="provider-row"):
                    yield Input(placeholder=f"{provider} key", password=True, id=f"input-{provider}")
                    yield Button("Set", id=f"set-{provider}", variant="primary")
                    yield Button("Remove", id=f"rm-{provider}")
            yield Static("", id="keys-message")
            yield Button("Close", id="close")

    def on_mount(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        present = list_keys()
        for provider in _PROVIDERS:
            state = "[green]set[/green]" if present.get(provider) else "[dim]not set[/dim]"
            self.query_one(f"#status-{provider}", Static).update(f"{provider}: {state}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "close":
            self.dismiss(None)
            return
        for provider in _PROVIDERS:
            if button_id == f"set-{provider}":
                value = self.query_one(f"#input-{provider}", Input).value.strip()
                if not value:
                    self.query_one("#keys-message", Static).update("enter a key first")
                    return
                self.run_worker(self._set(provider, value), exclusive=False)
                return
            if button_id == f"rm-{provider}":
                self.run_worker(self._rm(provider), exclusive=False)
                return

    async def _set(self, provider: str, value: str) -> None:
        exit_code, output = await run_once(["keys", "set", provider, value])
        self.query_one("#keys-message", Static).update(output or ("stored" if exit_code == 0 else "failed"))
        if exit_code == 0:
            self.query_one(f"#input-{provider}", Input).value = ""
        self._refresh_status()

    async def _rm(self, provider: str) -> None:
        exit_code, output = await run_once(["keys", "rm", provider])
        self.query_one("#keys-message", Static).update(output or ("removed" if exit_code == 0 else "failed"))
        self._refresh_status()
