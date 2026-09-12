"""`runectl tui` — the interactive Textual app (Phase 4, D13 amendment).

Layout: a run list on the left (live runs plus history), labeled detail tabs
on the right (Timeline / Thinking / Trace / Flags). Every run is a subprocess
(`runner_proc.run_streaming`); this module owns none of the agent loop, only
the display of its event stream and the composing of the commands that start
or act on one.

Arena preflight on startup mirrors `run_cmd._preflight_arena`'s message but
never builds anything itself — D17 is explicit that a run must never kick off
a 30-minute build behind the user's back, and the TUI is not a loophole for
that rule.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.theme import Theme
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from runectl.cli.render import event_line
from runectl.cli.tui import data as tui_data
from runectl.cli.tui.actions import approve_flag
from runectl.cli.tui.arena_screen import ArenaScreen
from runectl.cli.tui.bench_screen import BenchScreen
from runectl.cli.tui.commands import RunectlCommands
from runectl.cli.tui.config_screen import ConfigScreen
from runectl.cli.tui.help import HelpScreen
from runectl.cli.tui.keys_screen import KeysScreen
from runectl.cli.tui.launcher import LauncherScreen
from runectl.cli.tui.models_screen import ModelsScreen
from runectl.cli.tui.proc import run_foreground
from runectl.cli.tui.runner_proc import RunHandle, run_streaming
from runectl.cli.tui.splash import SplashScreen
from runectl.config import CONTAINER_NAME_PREFIX
from runectl.errors import SandboxError
from runectl.providers.keys import list_keys
from runectl.sandbox import arena_build
from runectl.trace.events import (
    CostUpdated,
    Event,
    EventPayload,
    FlagCandidate,
    FlagDecision,
    LlmThinking,
    RunFinished,
    RunStarted,
    ToolCall,
    ToolResultEvent,
)
from runectl.trace.store import Store

# The Fork mark (brand/svg/mark-*.svg: a stem splitting in two), in block
# characters, since the TUI has no image support — same geometry, different
# medium. Box-drawing rather than block glyphs: both the half-blocks (▟ ▘ ▝) and
# solid █ render as scattered squares in the fonts these terminals fall back to,
# where ╱ ╲ │ draw a continuous line. Taller than before and with no leading
# blank line — it used to be four sparse strokes floating in eight rows of dead
# space.
_MARK = """\
    │
    │
    │
   ╱ ╲
  ╱   ╲
 ╱     ╲"""

# Frames for the running-run indicator. Braille cycles smoothly at 8 frames and
# occupies one cell, so a row's width never changes as it spins.
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧"
_SPINNER_INTERVAL_S = 0.12

# (label, key) pairs — Textual's DataTable.add_columns() unpacks each tuple
# into (label, key) itself (see its docstring), so this renders as plain text
# headers while keeping the key stable for `update_cell`/`get_row_index`.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run", "run"),
    ("outcome", "outcome"),
    ("category", "category"),
    ("model", "model"),
    ("cost", "cost"),
)
# `steps` used to be a seventh column and was the one that pushed `cost` off the
# edge of the pane. It lives on the run header instead, which has a whole line.

# "Rune ore": a dark-fantasy night palette for the TUI, distinct from the
# Idalia Labs brand's strict black/white (that rule governs marketing
# surfaces — site, GitHub org, LinkedIn — not an interactive tool's own UI).
_RUNEORE_THEME = Theme(
    name="runeore",
    primary="#8B6FF5",  # rune-ore violet — borders, headers
    secondary="#4FA8E8",  # arcane blue — tool calls
    accent="#B79CFF",  # ore highlight
    foreground="#DDE3F5",  # moonlight
    background="#0A0B14",  # night sky
    surface="#12142A",  # panes
    panel="#1B1E3D",  # raised chrome
    success="#6EE7A8",  # solved / flag accepted
    warning="#E8C16B",  # moon amber — budget, stalls
    error="#F0637C",  # failures, rejected flags
    dark=True,
)

_HEADER_EMPTY = "no run selected"
# Falls back to this when the timeline pane hasn't been laid out yet (nothing
# has a real size until the first refresh). Matches `render._width()`'s floor.
_FALLBACK_LINE_WIDTH = 60
_TIMELINE_EMPTY = "select a run on the left, or press n to start one"
_THINKING_EMPTY = "the model's reasoning will appear here once a run is selected"
_TRACE_EMPTY = "raw trace events will appear here once a run is selected"


def _short_id(run_id: str) -> str:
    """The trailing random suffix of a run id, for the list column.

    A full id (`20260911-233703-dbf4a0`) is 22 columns of a ~48-column pane,
    which is most of why the table's model/cost/steps columns were being cut off
    entirely. The suffix alone distinguishes runs, and the full id is on the
    header line above the tabs and in every command the TUI composes.
    """
    return run_id.rsplit("-", 1)[-1]


@dataclass
class RunHeader:
    """What the line above the detail tabs says about the selected run.

    Exists because a run's identity was previously only legible *inside* the
    timeline text — scroll down and you no longer knew what you were looking at.
    No single event carries all of these (the name and model arrive with
    `run.started`, cost with each `cost.updated`, the outcome only at the end),
    so they accumulate here.
    """

    run_id: str
    challenge_name: str = "-"
    category: str = "-"
    model: str = "-"
    thinking: str = "off"
    outcome: str = "running"
    cost_usd: float = 0.0
    steps: int = 0

    def render(self) -> str:
        thinking = f" · thinking={self.thinking}" if self.thinking != "off" else ""
        # The full run id lives here because the list column now shows only its
        # suffix — this is where you copy it from for a `trace show`.
        return (
            f"{self.challenge_name} [{self.category}] · {self.model}{thinking}"
            f" · {self.steps} steps · ${self.cost_usd:.4f} · {self.outcome}"
            f"  ·  {self.run_id}"
        )


@dataclass
class LiveRun:
    """One in-flight `runectl run` subprocess this session started."""

    argv: list[str]
    task: asyncio.Task[RunHandle]
    events: list[Event] = field(default_factory=list)
    run_id: str | None = None
    process: asyncio.subprocess.Process | None = None


def _style_for(payload: EventPayload, theme: Theme) -> str:
    """Maps an event to a color straight from the active theme — reads
    `theme.primary`/etc rather than hand-duplicating the palette, so a future
    theme swap recolors the event stream along with everything else."""
    if isinstance(payload, (RunStarted, RunFinished)):
        return f"bold {theme.primary}"
    if isinstance(payload, ToolCall):
        return theme.secondary or theme.primary
    if isinstance(payload, ToolResultEvent):
        color = theme.success if payload.ok else theme.error
        return color or theme.foreground or ""
    if isinstance(payload, LlmThinking):
        return theme.accent or theme.primary
    if isinstance(payload, FlagDecision):
        if payload.decision == "finalized":
            return f"bold {theme.success}" if theme.success else "bold"
        if payload.decision == "rejected":
            return f"bold {theme.error}" if theme.error else "bold"
        return theme.warning or theme.foreground or ""
    if isinstance(payload, FlagCandidate):
        return theme.warning or theme.foreground or ""
    return theme.foreground or ""


class RunectlTUI(App[None]):
    TITLE = "runectl"
    COMMANDS = App.COMMANDS | {RunectlCommands}
    BINDINGS = [
        ("n", "new_run", "New run"),
        ("q", "quit", "Quit"),
        ("r", "refresh_runs", "Refresh"),
        ("k", "keys", "Keys"),
        ("a", "arena", "Arena"),
        ("c", "config", "Config"),
        ("m", "models", "Models"),
        ("b", "bench", "Bench"),
        ("x", "attach", "Attach"),
        ("X", "kill_run", "Kill"),
        ("question_mark", "help", "Help"),
    ]

    CSS = """
    #run-list-pane {
        /* Percentage rather than a fixed 46: at 46 columns the table's own
           model/cost/steps columns were simply cut off, which is most of what
           the list is for. */
        width: 40%;
        min-width: 54;
        border-right: heavy $primary;
    }
    #mark {
        color: $primary;
        text-align: center;
        height: auto;
    }
    #run-filters {
        height: auto;
    }
    #run-filters Select {
        width: 1fr;
    }
    #detail-pane {
        width: 1fr;
    }
    #run-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        color: $accent;
    }
    #timeline-log, #thinking-log, #trace-log {
        height: 1fr;
    }
    """

    def __init__(
        self,
        *,
        replay_run_id: str | None = None,
        playback_delay_s: float = 0.6,
        splash: bool = True,
    ) -> None:
        """`replay_run_id` is Phase 5's demo hook: on mount, instead of waiting
        for a selection, animate straight through that run's already-recorded
        `trace.jsonl` at a readable pace — real thinking, zero spend,
        reproducible. Deliberately does not go through `runectl replay`: that
        command re-executes the loop (to prove the tool-call sequence still
        matches), which is the wrong tool for "show what already happened"
        and would re-invoke the D15 judge's re-derivation sandbox call, which
        `ReplaySandbox` cannot serve (a separate, pre-existing gap — see
        `runectl replay`'s own docs — unrelated to this playback path, which
        never touches the judge or a sandbox at all).
        """
        super().__init__()
        self._store = Store()
        self._live_runs: dict[str, LiveRun] = {}  # keyed by a synthetic slot id until run_id is known
        self._selected_run_id: str | None = None
        self._next_slot = 0
        self._replay_run_id = replay_run_id
        self._playback_delay_s = playback_delay_s
        # Off for tests and for `scripts/capture_demo.py`, so a decorative
        # screen can never change what either of them sees.
        self._splash = splash and replay_run_id is None
        self._spinner_frame = 0
        self._last_line_width = 0
        self._playing_back = False
        # run_id -> header facts, accumulated as that run's events arrive (no
        # single event carries all of them) so the header survives switching
        # away from a run and back.
        self._headers: dict[str, RunHeader] = {}

    def compose(self) -> ComposeResult:
        from runectl.categories.loader import available_categories

        yield Header()
        with Horizontal():
            with Vertical(id="run-list-pane"):
                yield Static(_MARK, id="mark")
                with Horizontal(id="run-filters"):
                    yield Select(
                        [(c, c) for c in available_categories()],
                        prompt="category",
                        id="filter-category",
                        allow_blank=True,
                    )
                    yield Select(
                        [(o, o) for o in ("solved", "candidate", "exhausted", "error", "running")],
                        prompt="outcome",
                        id="filter-outcome",
                        allow_blank=True,
                    )
                yield DataTable(id="run-table", cursor_type="row")
            with Vertical(id="detail-pane"):
                yield Static(_HEADER_EMPTY, id="run-header")
                with TabbedContent(id="detail-tabs"):
                    with TabPane("Timeline", id="tab-timeline"):
                        # min_width=0: the default (78) makes a narrow pane
                        # scroll horizontally instead of wrapping to its own
                        # width, which is half of why long lines used to break
                        # to column 0 mid-sentence.
                        yield RichLog(id="timeline-log", wrap=True, markup=False, min_width=0)
                    with TabPane("Thinking", id="tab-thinking"):
                        yield RichLog(id="thinking-log", wrap=True, markup=False, min_width=0)
                    with TabPane("Trace", id="tab-trace"):
                        yield RichLog(id="trace-log", wrap=True, markup=False, min_width=0)
                    with TabPane("Flags", id="tab-flags"):
                        yield ListView(id="flag-list")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(_RUNEORE_THEME)
        self.theme = "runeore"

        table = self.query_one("#run-table", DataTable)
        table.add_columns(*_COLUMNS)
        self.action_refresh_runs()
        self.query_one("#timeline-log", RichLog).write(_TIMELINE_EMPTY)
        self.query_one("#thinking-log", RichLog).write(_THINKING_EMPTY)
        self.query_one("#trace-log", RichLog).write(_TRACE_EMPTY)
        self._write_flags_empty("select a run to see its pending flags")

        self.set_interval(_SPINNER_INTERVAL_S, self._tick_spinner)
        mark = self.query_one("#mark", Static)
        mark.styles.opacity = 0.0
        mark.styles.animate("opacity", value=1.0, duration=0.6)

        if self._splash:
            self.push_screen(SplashScreen())

        if self._replay_run_id is not None:
            # Demo/playback mode: skip the arena/key warnings (this path never
            # touches Docker or a provider) and start animating immediately
            # instead of waiting on a selection.
            self.run_worker(self._playback_run(self._replay_run_id), exclusive=True)
        else:
            self._preflight_arena_warning()
            self._preflight_key_warning()

    def _preflight_arena_warning(self) -> None:
        """Reports, never builds (D2, D17, D13 amendment) — see module docstring."""
        try:
            status = arena_build.inspect()
        except SandboxError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        if not status.present:
            self.notify(
                f"arena image {status.tag} is not built — run `runectl arena ensure` first",
                severity="warning",
                timeout=10,
            )

    def _preflight_key_warning(self) -> None:
        """A missing key otherwise surfaces as a launched run crashing a few
        seconds later — this says so up front instead."""
        if not any(list_keys().values()):
            self.notify(
                "no provider API key configured — run `runectl keys set <provider>` first",
                severity="warning",
                timeout=10,
            )

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_refresh_runs(self) -> None:
        table = self.query_one("#run-table", DataTable)
        table.clear()
        category = self.query_one("#filter-category", Select).value
        outcome = self.query_one("#filter-outcome", Select).value
        # Select.NULL (unselected) is a truthy sentinel object, not falsy like
        # None — `if category` alone would treat "nothing chosen" as a filter
        # matching nothing, and every row would vanish.
        if category is Select.NULL:
            category = None
        if outcome is Select.NULL:
            outcome = None
        for summary in tui_data.list_runs_fresh(self._store):
            if category and summary.category != category:
                continue
            if outcome and (summary.outcome or "running") != outcome:
                continue
            table.add_row(
                _short_id(summary.run_id),
                summary.outcome or "running",
                summary.category,
                summary.model,
                f"${summary.cost_usd:.4f}",
                key=summary.run_id,
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in ("filter-category", "filter-outcome"):
            self.action_refresh_runs()

    def action_new_run(self) -> None:
        def _on_dismiss(argv: list[str] | None) -> None:
            if argv is not None:
                self._launch(argv)

        self.push_screen(LauncherScreen(), _on_dismiss)

    def action_keys(self) -> None:
        self.push_screen(KeysScreen())

    def action_arena(self) -> None:
        self.push_screen(ArenaScreen())

    def action_config(self) -> None:
        self.push_screen(ConfigScreen())

    def action_models(self) -> None:
        self.push_screen(ModelsScreen())

    def action_bench(self) -> None:
        self.push_screen(BenchScreen())

    def action_attach(self) -> None:
        run_id = self._selected_run_id
        if run_id is None:
            self.notify("select a run first", severity="warning")
            return
        with self.suspend():
            run_foreground(["docker", "exec", "-it", f"{CONTAINER_NAME_PREFIX}{run_id}", "bash"])

    def action_kill_run(self) -> None:
        run_id = self._selected_run_id
        live = self._live_runs.get(run_id) if run_id else None
        if live is None or live.process is None or live.task.done():
            self.notify(
                "no live run selected — this only kills a run started from this session",
                severity="warning",
            )
            return
        live.process.terminate()
        self.notify(f"sent SIGTERM to {run_id}")

    def _launch(self, argv: list[str]) -> None:
        slot_id = f"__slot_{self._next_slot}"
        self._next_slot += 1
        live = LiveRun(argv=argv, task=asyncio.ensure_future(self._drive(slot_id, argv)))
        self._live_runs[slot_id] = live
        table = self.query_one("#run-table", DataTable)
        table.add_row("(starting…)", "running", "-", "-", "$0.0000", key=slot_id)
        # Watch what you just launched. Without this the new run streamed into
        # nothing until you noticed it in the list and clicked it.
        self._selected_run_id = slot_id
        for log_id in ("#timeline-log", "#thinking-log", "#trace-log"):
            self.query_one(log_id, RichLog).clear()

    async def _drive(self, slot_id: str, argv: list[str]) -> RunHandle:
        live = self._live_runs[slot_id]

        def on_event(event: Event) -> None:
            live.events.append(event)
            if live.run_id is None:
                # The run id isn't known until run.started's own trace lines
                # have a run_id on the envelope — Event.run_id, set by the
                # writer at construction, works from the very first event.
                live.run_id = event.run_id
                self._live_runs[event.run_id] = live
                self._rekey_row(slot_id, event.run_id)
            if live.run_id == self._selected_run_id or slot_id == self._selected_run_id:
                self._write_event(event)
            self._update_row(event.run_id, event)

        def on_start(process: asyncio.subprocess.Process) -> None:
            live.process = process

        handle = await run_streaming(argv, on_event, on_start=on_start)
        if handle.stderr_tail and handle.run_id is None:
            self.notify(
                "run failed before writing any event:\n" + "\n".join(handle.stderr_tail[-5:]),
                severity="error",
                timeout=15,
            )
        return handle

    def _rekey_row(self, old_key: str, new_key: str) -> None:
        # DataTable has no rename-key primitive; simplest correct approach is
        # to remove and re-add the row under the real run_id.
        table = self.query_one("#run-table", DataTable)
        if old_key in table.rows:
            table.remove_row(old_key)
        table.add_row("(running…)", "running", "-", "-", "$0.0000", key=new_key)
        if self._selected_run_id == old_key:
            self._selected_run_id = new_key

    def _update_row(self, run_id: str | None, event: Event) -> None:
        """Keep the run's row and header current as its events stream in.

        `cost.updated` is handled here as well as the two lifecycle events —
        without it a running run sat at `$0.0000` and `0 steps` for its entire
        life and only snapped to the truth once it finished, which is the
        opposite of what watching a live run is for. The event has carried
        `cumulative_cost_usd` and `step` all along.
        """
        if run_id is None:
            return
        header = self._headers.setdefault(run_id, RunHeader(run_id=run_id))
        payload = event.payload()
        if isinstance(payload, RunStarted):
            header.challenge_name = payload.challenge_name
            header.category = payload.category
            header.model = payload.model
            header.thinking = payload.thinking_level
        elif isinstance(payload, CostUpdated):
            header.cost_usd = payload.cumulative_cost_usd
            if payload.step is not None:
                header.steps = payload.step
        elif isinstance(payload, RunFinished):
            header.outcome = payload.outcome
            header.cost_usd = payload.cost_usd
            header.steps = payload.steps_used

        table = self.query_one("#run-table", DataTable)
        if run_id in table.rows:
            if isinstance(payload, RunStarted):
                table.update_cell(run_id, "run", _short_id(run_id))
                table.update_cell(run_id, "category", header.category)
                table.update_cell(run_id, "model", header.model)
            if isinstance(payload, (CostUpdated, RunFinished)):
                table.update_cell(run_id, "cost", f"${header.cost_usd:.4f}")
            if isinstance(payload, RunFinished):
                table.update_cell(run_id, "outcome", header.outcome)

        if run_id == self._selected_run_id:
            self.query_one("#run-header", Static).update(header.render())

    def _set_flag_badge(self, pending: int) -> None:
        """Put the pending count on the Flags tab itself.

        A candidate waiting on approval is the one thing in this app that needs
        the user to act; before this it was invisible unless the Flags tab
        happened to be the one in front."""
        self.query_one("#detail-tabs", TabbedContent).get_tab("tab-flags").update(
            f"Flags ({pending})" if pending else "Flags"
        )

    def _write_flags_empty(self, message: str) -> None:
        flag_list = self.query_one("#flag-list", ListView)
        flag_list.clear()
        # name=None so on_list_view_selected's `if not flag: return` guard
        # treats this row as inert rather than an approvable candidate.
        flag_list.append(ListItem(Static(message)))
        self._set_flag_badge(0)

    def _refresh_flag_list(self, run_id: str) -> None:
        candidates = tui_data.read_pending(self._store, run_id)
        if not candidates:
            self._write_flags_empty("no pending flags for this run")
            return
        flag_list = self.query_one("#flag-list", ListView)
        flag_list.clear()
        for candidate in candidates:
            flag_list.append(
                ListItem(Static(f"{candidate.flag}  ({candidate.how_found})"), name=candidate.flag)
            )
        self._set_flag_badge(len(candidates))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        run_id = str(event.row_key.value)
        self._select_run(run_id)

    def _line_width(self) -> int:
        """The width `event_line` should format to: the timeline pane's own.

        This was hardcoded at 100 regardless of the pane's real size, which is
        why long lines wrapped mid-sentence back to column 0 — `event_line`
        pre-formats and clips to the width it is given, so a wrong width defeats
        its layout and leaves `RichLog` to hard-wrap the overflow. `trace_cmd.py`
        has always done this correctly via `render._width()`; the TUI just has a
        different source for the number.
        """
        try:
            width = self.query_one("#timeline-log", RichLog).content_size.width
        except NoMatches:
            return _FALLBACK_LINE_WIDTH
        return max(_FALLBACK_LINE_WIDTH, width)

    def on_resize(self) -> None:
        """Re-render the selected run at the new width.

        Two guards, both learned the hard way. Textual fires `Resize` on the
        *initial* layout too, not only on a real terminal resize, so re-rendering
        unconditionally meant reloading the trace from disk while `_playback_run`
        was still writing it — the whole event stream appeared twice. And a
        resize that doesn't change the wrapping width has nothing to redo.
        """
        width = self._line_width()
        if width == self._last_line_width or self._playing_back:
            return
        self._last_line_width = width
        if self._selected_run_id is not None:
            self._select_run(self._selected_run_id)

    def _tick_spinner(self) -> None:
        """Advance the running-run indicator. Touches only rows that are still
        running, so an idle screen full of finished runs does no work."""
        live_ids = [
            run_id
            for run_id, live in self._live_runs.items()
            if live.run_id == run_id and not live.task.done()
        ]
        if not live_ids:
            return
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        frame = _SPINNER_FRAMES[self._spinner_frame]
        table = self.query_one("#run-table", DataTable)
        for run_id in live_ids:
            if run_id in table.rows:
                table.update_cell(run_id, "outcome", f"{frame} running")

    def _write_event(self, event: Event) -> None:
        """The one path an event takes to the panes — live, browsed, or replayed."""
        payload = event.payload()
        line = event_line(event.payload(), width=self._line_width())
        if line:
            style = _style_for(payload, self.current_theme)
            self.query_one("#timeline-log", RichLog).write(Text(line, style=style))
        if isinstance(payload, LlmThinking):
            self.query_one("#thinking-log", RichLog).write(f"[step {payload.step}] {payload.text}")
        self.query_one("#trace-log", RichLog).write(event.model_dump_json())
        if isinstance(payload, (FlagCandidate, FlagDecision)):
            self._refresh_flag_list(event.run_id)

    def _select_run(self, run_id: str) -> None:
        """Instant load — the whole trace, all at once. Used for browsing a
        finished run from the list (as opposed to `_playback_run`'s paced
        animation, which is for the demo)."""
        self._selected_run_id = run_id
        self.query_one("#timeline-log", RichLog).clear()
        self.query_one("#thinking-log", RichLog).clear()
        self.query_one("#trace-log", RichLog).clear()

        live = self._live_runs.get(run_id)
        events = live.events if live is not None else tui_data.read_events(self._store, run_id)
        for event in events:
            self._write_event(event)
            self._update_row(event.run_id, event)
        self._refresh_flag_list(run_id)
        header = self._headers.get(run_id)
        self.query_one("#run-header", Static).update(
            header.render() if header is not None else _HEADER_EMPTY
        )

    async def _playback_run(self, run_id: str) -> None:
        """Phase 5's demo: step through a finished run's trace at a readable
        pace, one event per `_playback_delay_s` seconds — "show the thought,
        show the command, show the output, show the next move"
        — the demo this project set out to build. Reads only the already-recorded
        `trace.jsonl` — no sandbox, no provider, no spend."""
        self._selected_run_id = run_id
        self.query_one("#timeline-log", RichLog).clear()
        self.query_one("#thinking-log", RichLog).clear()
        self.query_one("#trace-log", RichLog).clear()

        events = tui_data.read_events(self._store, run_id)
        manifest = tui_data.read_manifest(self._store, run_id)
        table = self.query_one("#run-table", DataTable)
        if run_id in table.rows:
            table.move_cursor(row=table.get_row_index(run_id))
        # Holds off `on_resize`'s re-render: the initial layout fires a Resize
        # while this loop is mid-stream, and reloading the trace underneath it
        # printed the whole run twice.
        self._playing_back = True
        try:
            for event in events:
                self._write_event(event)
                self._update_row(event.run_id, event)
                await asyncio.sleep(self._playback_delay_s)
        finally:
            self._playing_back = False
        if manifest is not None:
            self.notify(
                f"playback complete: {manifest.outcome} — {manifest.flag or 'no flag'}",
                timeout=10,
            )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "flag-list" or self._selected_run_id is None:
            return
        flag = event.item.name
        if not flag:
            return
        run_id = self._selected_run_id
        self.run_worker(self._approve(run_id, flag), exclusive=False)

    async def _approve(self, run_id: str, flag: str) -> None:
        exit_code, output = await approve_flag(run_id, flag)
        if exit_code == 0:
            self.notify(output or "approved")
        else:
            self.notify(output or f"exit {exit_code}", severity="error")
        self._refresh_flag_list(run_id)
        self.action_refresh_runs()
