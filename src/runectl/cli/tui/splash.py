"""The launch splash (`runectl tui`).

Pure decoration, and deliberately cheap: static text, one fade, one timer, no
state read from anywhere. It cannot fail in a way that costs a user a run.

Three things keep it from becoming a nuisance:

- it dismisses on *any* keypress, not just a specific one;
- it never appears in `--replay` mode, so the demo capture and the recorded
  walkthrough open straight into the run;
- `RunectlTUI(splash=False)` turns it off entirely, which is what the test
  suite and `scripts/capture_demo.py` pass.

The crescent is the same gesture as the Idalia Labs mark (a ring drawn
three-quarters and left open) rendered in the TUI's own night palette, next to
the product's own wordmark. The brand's strict black-and-white rule governs
marketing surfaces — the site, the org profile — not an interactive tool's
interior.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, Middle
from textual.screen import ModalScreen
from textual.widgets import Static

# Held just long enough to read the wordmark, short enough that someone opening
# the TUI for the tenth time today does not resent it.
SPLASH_SECONDS = 1.4

_MOON = """\
 ╭───╮
╭╯
│
╰╮
 ╰───╯"""

_WORDMARK = """\
█▀▀▄ █  █ █▄ █ █▀▀ █▀▀ ▀▀█▀▀ █
█▀▀▄ █  █ █ ▀█ █▀▀ █     █   █
▀  ▀ ▀▀▀▀ ▀  ▀ ▀▀▀ ▀▀▀   ▀   ▀▀▀"""


class SplashScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    SplashScreen {
        background: $background;
    }
    #splash-moon {
        color: $warning;
        text-align: center;
        height: auto;
    }
    #splash-wordmark {
        color: $primary;
        text-style: bold;
        height: auto;
        margin-top: 1;
    }
    #splash-tagline {
        color: $text-muted;
        text-align: center;
        height: auto;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Middle(), Center():
            yield Static(_MOON, id="splash-moon")
            yield Static(_WORDMARK, id="splash-wordmark")
            yield Static("an agentic CTF solver · press any key", id="splash-tagline")

    def on_mount(self) -> None:
        for widget_id in ("#splash-moon", "#splash-wordmark", "#splash-tagline"):
            widget = self.query_one(widget_id, Static)
            widget.styles.opacity = 0.0
            widget.styles.animate("opacity", value=1.0, duration=0.45)
        self.set_timer(SPLASH_SECONDS, self._close)

    def _close(self) -> None:
        # The timer and a keypress race each other by design; whichever wins,
        # the other must not pop a screen it doesn't own.
        if self.is_running and self.app.screen is self:
            self.dismiss(None)

    def on_key(self) -> None:
        self._close()

    def on_click(self) -> None:
        self._close()
