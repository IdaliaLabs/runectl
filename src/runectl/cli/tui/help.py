"""The `?` help overlay — plain-English orientation for a first-time viewer.

Exists because the TUI's own docstring is the only other place this is
explained, and a person looking at the app has no way to read that. Static
text only: nothing here reads run state, so it can never drift out of sync
with what the panes actually do (it can only go stale against the *code*,
which a reviewer can catch same as any other doc).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP_TEXT = """\
[b]What this is[/b]
runectl runs an AI agent against a CTF challenge inside a sandboxed container and
writes down everything it tries. This screen is the whole control surface — watch a
run live or replayed, and manage everything runectl can do without leaving it.

[b]The run list[/b] (left)
Every run you've started or browsed before. The two dropdowns above it filter by
category and outcome. Press [b]n[/b] to start a new one — it composes and shows you
the exact command before launching, nothing hidden.

[b]The four tabs[/b] (right)
  Timeline  — one line per step: what it ran, what came back, what it cost
  Thinking  — the model's own reasoning, in its words
  Trace     — the raw recorded event, for when you want to see everything
  Flags     — candidate answers waiting on your approval

[b]Approving a flag[/b]
runectl never submits an answer on its own by default — a candidate sits in the
Flags tab until you select it. That gate is the point: a wrong flag costs nothing
until you say yes.

[b]Everything else is one key away[/b]
  k  API keys           a  arena sandbox image     c  config defaults
  m  model registry      b  run the bench suite      x  attach to a run's container
  X  kill a live run (one this session started)
Or press [b]ctrl+p[/b] for the command palette — type a few letters of any of the
above and hit enter, faster than remembering the key.

[b]Keys[/b]
  n  start a new run        r  refresh the run list
  ?  toggle this screen      q  quit

Press [b]?[/b] or [b]Escape[/b] to close.
"""


class HelpScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-box {
        width: 78;
        height: auto;
        max-height: 90%;
        border: heavy $primary;
        padding: 1 3;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "dismiss", "Close"), ("question_mark", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-box"):
            yield Static(_HELP_TEXT)
