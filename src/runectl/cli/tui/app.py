"""`runectl tui` — the interactive Textual app (Phase 4, D13 amendment).

Layout: a run list on the left (live runs plus history), detail tabs on the
right (Timeline / Thinking / Trace / Flags). Every run is a subprocess
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

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, ListItem, ListView, RichLog, Static

from runectl.cli.tui import data as tui_data
from runectl.cli.tui.actions import approve_flag
from runectl.cli.tui.launcher import LauncherScreen
from runectl.cli.tui.runner_proc import RunHandle, run_streaming
from runectl.errors import SandboxError
from runectl.sandbox import arena_build
from runectl.trace.events import (
    Event,
    FlagCandidate,
    FlagDecision,
    LlmThinking,
    RunFinished,
    RunStarted,
)
from runectl.trace.store import Store

# (label, key) pairs — the key is what `update_cell`/`get_row_index` address a
# cell by, kept stable regardless of display label wording.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("run", "run"),
    ("outcome", "outcome"),
    ("category", "category"),
    ("model", "model"),
    ("cost", "cost"),
    ("steps", "steps"),
)


def _timeline_line(event: Event) -> str | None:
    """A thin, TUI-local rendering, deliberately not reusing `cli/render.py`'s
    `_line` (that one renders one `EventPayload`, not raw `Event.data`, and
    coupling this live view to its exact signature would make the two harder
    to evolve independently — this is a display concern, not a shared one)."""
    payload = event.payload()
    if isinstance(payload, RunStarted):
        return f"▶ {payload.challenge_name} [{payload.category}]  {payload.model}"
    if isinstance(payload, RunFinished):
        flag = f" — {payload.flag}" if payload.flag else ""
        return f"■ {payload.outcome}{flag}  (exit {payload.exit_code})"
    from runectl.cli.render import _line

    return _line(payload, width=100)


@dataclass
class LiveRun:
    """One in-flight `runectl run` subprocess this session started."""

    argv: list[str]
    task: asyncio.Task[RunHandle]
    events: list[Event] = field(default_factory=list)
    run_id: str | None = None


class RunectlTUI(App[None]):
    TITLE = "runectl"
    BINDINGS = [
        ("n", "new_run", "New run"),
        ("q", "quit", "Quit"),
        ("r", "refresh_runs", "Refresh"),
    ]

    CSS = """
    #run-list-pane {
        width: 42;
        border-right: solid $accent;
    }
    #detail-pane {
        width: 1fr;
    }
    #timeline-log, #thinking-log, #trace-log {
        height: 1fr;
    }
    """

    def __init__(self, *, replay_run_id: str | None = None, playback_delay_s: float = 0.6) -> None:
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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="run-list-pane"):
                yield DataTable(id="run-table")
            with Vertical(id="detail-pane"):
                yield RichLog(id="timeline-log", wrap=True, markup=False)
                yield RichLog(id="thinking-log", wrap=True, markup=False)
                yield RichLog(id="trace-log", wrap=True, markup=False)
                yield ListView(id="flag-list")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#run-table", DataTable)
        table.add_columns(*_COLUMNS)
        self.action_refresh_runs()
        if self._replay_run_id is not None:
            # Demo/playback mode: skip the arena warning (this path never
            # touches Docker) and start animating immediately instead of
            # waiting on a selection.
            self.run_worker(self._playback_run(self._replay_run_id), exclusive=True)
        else:
            self._preflight_arena_warning()

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

    def action_refresh_runs(self) -> None:
        table = self.query_one("#run-table", DataTable)
        table.clear()
        for summary in tui_data.list_runs_fresh(self._store):
            table.add_row(
                summary.run_id,
                summary.outcome or "running",
                summary.category,
                summary.model,
                f"${summary.cost_usd:.4f}",
                str(summary.steps_used),
                key=summary.run_id,
            )

    def action_new_run(self) -> None:
        def _on_dismiss(argv: list[str] | None) -> None:
            if argv is not None:
                self._launch(argv)

        self.push_screen(LauncherScreen(), _on_dismiss)

    def _launch(self, argv: list[str]) -> None:
        slot_id = f"__slot_{self._next_slot}"
        self._next_slot += 1
        live = LiveRun(argv=argv, task=asyncio.ensure_future(self._drive(slot_id, argv)))
        self._live_runs[slot_id] = live
        table = self.query_one("#run-table", DataTable)
        table.add_row("(starting…)", "running", "-", "-", "$0.0000", "0", key=slot_id)

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
                self._append_live_event(event)
            self._update_row(event.run_id, event)

        handle = await run_streaming(argv, on_event)
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
        table.add_row("(running…)", "running", "-", "-", "$0.0000", "0", key=new_key)
        if self._selected_run_id == old_key:
            self._selected_run_id = new_key

    def _update_row(self, run_id: str | None, event: Event) -> None:
        if run_id is None:
            return
        table = self.query_one("#run-table", DataTable)
        if run_id not in table.rows:
            return
        payload = event.payload()
        if isinstance(payload, RunStarted):
            table.update_cell(run_id, "run", run_id)
            table.update_cell(run_id, "category", payload.category)
            table.update_cell(run_id, "model", payload.model)
        if isinstance(payload, RunFinished):
            table.update_cell(run_id, "outcome", payload.outcome)
            table.update_cell(run_id, "cost", f"${payload.cost_usd:.4f}")
            table.update_cell(run_id, "steps", str(payload.steps_used))

    def _append_live_event(self, event: Event) -> None:
        payload = event.payload()
        timeline = self.query_one("#timeline-log", RichLog)
        line = _timeline_line(event)
        if line:
            timeline.write(line)
        if isinstance(payload, LlmThinking):
            thinking_log = self.query_one("#thinking-log", RichLog)
            thinking_log.write(f"[step {payload.step}] {payload.text}")
        trace_log = self.query_one("#trace-log", RichLog)
        trace_log.write(event.model_dump_json())
        if isinstance(payload, (FlagCandidate, FlagDecision)):
            self._refresh_flag_list(event.run_id)

    def _refresh_flag_list(self, run_id: str) -> None:
        flag_list = self.query_one("#flag-list", ListView)
        flag_list.clear()
        for candidate in tui_data.read_pending(self._store, run_id):
            flag_list.append(
                ListItem(Static(f"{candidate.flag}  ({candidate.how_found})"), name=candidate.flag)
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        run_id = str(event.row_key.value)
        self._select_run(run_id)

    def _write_event_to_logs(self, event: Event) -> None:
        payload = event.payload()
        timeline = self.query_one("#timeline-log", RichLog)
        thinking_log = self.query_one("#thinking-log", RichLog)
        trace_log = self.query_one("#trace-log", RichLog)
        line = _timeline_line(event)
        if line:
            timeline.write(line)
        if isinstance(payload, LlmThinking):
            thinking_log.write(f"[step {payload.step}] {payload.text}")
        trace_log.write(event.model_dump_json())

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
            self._write_event_to_logs(event)
        self._refresh_flag_list(run_id)

    async def _playback_run(self, run_id: str) -> None:
        """Phase 5's demo: step through a finished run's trace at a readable
        pace, one event per `_playback_delay_s` seconds — "show the thought,
        show the command, show the output, show the next move"
        (`PLAN.md`'s stated demo). Reads only the already-recorded
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
        for event in events:
            self._write_event_to_logs(event)
            payload = event.payload()
            if isinstance(payload, (FlagCandidate, FlagDecision)):
                self._refresh_flag_list(run_id)
            await asyncio.sleep(self._playback_delay_s)
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


def main() -> None:
    RunectlTUI().run()


if __name__ == "__main__":
    main()
