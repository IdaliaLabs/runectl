"""The new-run launcher modal (Phase 4).

Composes the exact `runectl run` argv a human would type and shows it before
launching — the TUI is meant to teach the flags, not hide them (D4: the CLI
is still the API). Confirming dismisses the modal with that argv list; the
caller (`app.py`) is the one that actually spawns it.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from runectl.categories.loader import available_categories
from runectl.cli.tui.models import model_options, preferred_model
from runectl.config import DEFAULT_MAX_COST_USD
from runectl.flags.judge import APPROVAL_POLICIES
from runectl.providers.registry import THINKING_LEVELS
from runectl.user_config import default_thinking


class LauncherScreen(ModalScreen[list[str] | None]):
    """Returns the composed argv (everything after `runectl run`) or None on cancel."""

    DEFAULT_CSS = """
    LauncherScreen {
        align: center middle;
    }
    #launcher-box {
        width: 76;
        height: auto;
        max-height: 90%;
        border: round $accent;
        padding: 1 2;
        background: $surface;
    }
    #launcher-preview {
        margin-top: 1;
        color: $text-muted;
    }
    #launcher-error {
        color: $error;
    }
    """

    def compose(self) -> ComposeResult:
        options = model_options()
        preferred = preferred_model(options)

        with VerticalScroll(id="launcher-box"):
            yield Static(
                "[b]New run[/b] — fill in what you're solving below. This form builds the "
                "real `runectl run` command shown at the bottom; nothing here is hidden "
                "from you, and you'll see the exact command before it launches."
            )
            yield Label("Challenge file (optional — if you have one, it fills in everything below)")
            yield Input(placeholder="bench/practice/easy-02/chal.toml", id="challenge")
            yield Label("Name — a short label for this run")
            yield Input(placeholder="quick-math", id="name")
            yield Label("Category — what kind of challenge this is")
            yield Select(
                [(c, c) for c in available_categories()], id="category", allow_blank=True
            )
            yield Label("Description — the challenge prompt, pasted as given to you")
            yield Input(placeholder="the challenge prompt", id="description")
            yield Label(
                "Model — cheapest first per provider, with its price per 1M tokens in/out. "
                "Type to search."
            )
            yield Select(options, value=preferred, id="model", allow_blank=False)
            yield Label("Thinking — how much the model reasons before each step (higher costs more)")
            yield Select(
                [(level, level) for level in THINKING_LEVELS],
                value=default_thinking("anthropic"),
                id="thinking",
                allow_blank=False,
            )
            yield Label("Approval — 'gated' holds every flag for you to approve before it's final")
            yield Select(
                [(a, a) for a in APPROVAL_POLICIES], value="gated", id="approval", allow_blank=False
            )
            yield Label("Max cost in USD — the run stops itself once it would spend more (0 disables)")
            yield Input(value=str(DEFAULT_MAX_COST_USD), id="max_cost")
            yield Static("", id="launcher-error")
            yield Static("", id="launcher-preview")
            with Horizontal():
                yield Button("Launch", id="launch", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._update_preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_preview()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._update_preview()

    def _build_argv(self) -> list[str] | str:
        """Returns the argv, or an error string if the form is incomplete."""
        challenge = self.query_one("#challenge", Input).value.strip()
        model = self.query_one("#model", Select).value
        thinking = self.query_one("#thinking", Select).value
        approval = self.query_one("#approval", Select).value
        max_cost = self.query_one("#max_cost", Input).value.strip()

        if not model:
            return "a model is required"

        argv = ["run", "--model", str(model), "--output", "jsonl"]
        if challenge:
            argv += ["--challenge", challenge]
        else:
            name = self.query_one("#name", Input).value.strip()
            category = self.query_one("#category", Select).value
            if not name or not category:
                return "either a challenge TOML, or both name and category, are required"
            description = self.query_one("#description", Input).value.strip()
            argv += ["--name", name, "--category", str(category)]
            if description:
                argv += ["--description", description]

        if thinking and thinking != "off":
            argv += ["--thinking", str(thinking)]
        if approval and approval != "gated":
            argv += ["--approval", str(approval)]
        if max_cost:
            argv += ["--max-cost", max_cost]
        return argv

    def _update_preview(self) -> None:
        result = self._build_argv()
        error = self.query_one("#launcher-error", Static)
        preview = self.query_one("#launcher-preview", Static)
        if isinstance(result, str):
            error.update(result)
            preview.update("")
        else:
            error.update("")
            preview.update("runectl " + " ".join(result))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        result = self._build_argv()
        if isinstance(result, str):
            self.query_one("#launcher-error", Static).update(result)
            return
        self.dismiss(result)
