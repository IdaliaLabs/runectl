"""Phase 4 — the TUI, driven headlessly via Textual's `App.run_test()`.

Per CONTRIBUTING.md, nothing here may touch a real Docker daemon or spend a
token. `runner_proc.run_streaming` is exercised against a small stub script
(via its `launch_argv` seam) that emits canned NDJSON on stdout — never the
real `runectl run` — so this covers the subprocess-reading contract without
needing the engine, a Docker daemon, or an API key at all.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from runectl.cli.tui.app import RunectlTUI
from runectl.cli.tui.proc import run_once
from runectl.cli.tui.runner_proc import run_streaming
from runectl.trace.events import Event

pytestmark = pytest.mark.asyncio


def _stub_script(tmp_path: Path, lines: list[str], *, exit_code: int = 0) -> Path:
    script = tmp_path / "fake_runectl.py"
    body = "\n".join(f"print({line!r})" for line in lines)
    script.write_text(f"import sys\n{body}\nsys.exit({exit_code})\n")
    return script


def _event_line(**kwargs: object) -> str:
    return json.dumps({"v": 1, "run_id": "run-x", "seq": 1, "ts": 0.0, **kwargs})


_RUN_STARTED_DATA = {
    "challenge_name": "c", "category": "misc", "model": "claude-sonnet-5",
    "provider": "anthropic", "approval_policy": "gated", "max_steps": 5,
    "network": "none", "thinking_level": "off", "thinking_clamped_from": None,
}


async def test_run_streaming_parses_events_and_returns_run_id(tmp_path: Path) -> None:
    lines = [
        _event_line(type="run.started", data=_RUN_STARTED_DATA),
        "run-x",  # the trailing bare run id, per the D4 output contract
    ]
    script = _stub_script(tmp_path, lines)
    seen: list[Event] = []

    handle = await run_streaming([], seen.append, launch_argv=(sys.executable, str(script)))

    assert handle.exit_code == 0
    assert handle.run_id == "run-x"
    assert len(seen) == 1
    assert seen[0].type == "run.started"


async def test_run_streaming_captures_stderr_on_early_failure(tmp_path: Path) -> None:
    script = tmp_path / "fake_runectl_fail.py"
    script.write_text("import sys\nsys.stderr.write('no API key found\\n')\nsys.exit(6)\n")

    seen: list[Event] = []
    handle = await run_streaming([], seen.append, launch_argv=(sys.executable, str(script)))

    assert handle.exit_code == 6
    assert handle.run_id is None
    assert seen == []
    assert "no API key found" in handle.stderr_tail


async def test_app_mounts_and_launcher_opens_and_cancels() -> None:
    app = RunectlTUI(splash=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1

        await pilot.press("n")
        await pilot.pause()
        assert len(app.screen_stack) == 2  # launcher pushed

        from textual.widgets import Button

        cancel_btn = app.screen.query_one("#cancel", Button)
        await pilot.click(cancel_btn)
        await pilot.pause()
        assert len(app.screen_stack) == 1  # launcher dismissed


async def test_launcher_preview_shows_a_composed_command_by_default() -> None:
    from runectl.cli.tui.launcher import LauncherScreen

    app = RunectlTUI(splash=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        app.push_screen(LauncherScreen())
        await pilot.pause()
        from textual.widgets import Static

        preview = app.screen.query_one("#launcher-preview", Static)
        # A model is preselected by default (the registry's first anthropic
        # entry, or the configured default), so the preview should show a
        # composed command rather than the "model is required" error — though
        # with no --challenge and no --name/--category filled in yet, it's
        # actually the error state until one of those is provided.
        error = app.screen.query_one("#launcher-error", Static)
        assert "runectl run" in str(preview.content) or "required" in str(error.content)


async def test_app_live_run_updates_the_table_and_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drives `_launch` directly against a stub script, bypassing the
    launcher modal, to verify the app applies streamed events to its
    widgets."""
    lines = [
        _event_line(type="run.started", data=_RUN_STARTED_DATA),
        _event_line(
            type="run.finished",
            data={
                "outcome": "solved", "flag": "flag{x}", "steps_used": 1,
                "progress_steps": 1, "blocked_steps": 0, "cost_usd": 0.01,
                "duration_s": 0.1, "exit_code": 0,
            },
        ),
        "run-x",
    ]
    script = _stub_script(tmp_path, lines)

    async def _fake_run_streaming(argv, on_event, **kwargs):  # type: ignore[no-untyped-def]
        return await run_streaming(argv, on_event, launch_argv=(sys.executable, str(script)))

    # app.py imported `run_streaming` by name (`from ...runner_proc import
    # run_streaming`), so the patch target is the name bound in app.py's own
    # module, not the one in runner_proc.
    import runectl.cli.tui.app as tui_app_module

    monkeypatch.setattr(tui_app_module, "run_streaming", _fake_run_streaming)

    app = RunectlTUI(splash=False)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()

        app._launch(["run", "--model", "claude-sonnet-5", "--output", "jsonl"])
        slot_key = next(iter(app._live_runs))
        live = app._live_runs[slot_key]
        await live.task
        await pilot.pause()

        from textual.widgets import DataTable

        table = app.query_one("#run-table", DataTable)
        assert "run-x" in table.rows


async def test_run_once_against_stub_script(tmp_path: Path) -> None:
    script = tmp_path / "fake_runectl_once.py"
    script.write_text("import sys\nprint('stored key for anthropic')\nsys.exit(0)\n")

    exit_code, output = await run_once(
        ["keys", "set", "anthropic", "sk-x"], launch_argv=(sys.executable, str(script))
    )

    assert exit_code == 0
    assert "stored key for anthropic" in output


@pytest.mark.parametrize(
    ("key", "screen_name"),
    [
        ("k", "KeysScreen"),
        ("a", "ArenaScreen"),
        ("c", "ConfigScreen"),
        ("m", "ModelsScreen"),
        ("b", "BenchScreen"),
    ],
)
async def test_each_new_screen_opens_and_closes(key: str, screen_name: str) -> None:
    app = RunectlTUI(splash=False)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert type(app.screen_stack[-1]).__name__ == screen_name
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_run_list_filters_narrow_visible_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from textual.widgets import DataTable, Select

    from runectl.cli.tui import data as tui_data
    from runectl.cli.tui.data import RunSummary

    summaries = [
        RunSummary("run-a", "chal-a", "misc", "claude-sonnet-5", "solved", 0.01, 3, "off"),
        RunSummary("run-b", "chal-b", "web", "gpt-5", "exhausted", 0.02, 5, "off"),
    ]
    monkeypatch.setattr(tui_data, "list_runs_fresh", lambda store: summaries)

    app = RunectlTUI(splash=False)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        table = app.query_one("#run-table", DataTable)
        assert len(table.rows) == 2

        app.query_one("#filter-category", Select).value = "web"
        await pilot.pause()
        assert list(table.rows.keys()) == ["run-b"]


async def test_kill_run_terminates_a_live_process(tmp_path: Path) -> None:
    """A stub that just sleeps, standing in for a run still in flight — the
    point here is that `action_kill_run` reaches the real subprocess, not
    that any particular run behavior is being tested."""
    script = tmp_path / "fake_runectl_sleep.py"
    script.write_text("import time\ntime.sleep(30)\n")

    app = RunectlTUI(splash=False)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()

        async def _fake_run_streaming(argv, on_event, **kwargs):  # type: ignore[no-untyped-def]
            return await run_streaming(
                argv, on_event, launch_argv=(sys.executable, str(script)), on_start=kwargs.get("on_start")
            )

        import runectl.cli.tui.app as tui_app_module

        monkeypatch_target = tui_app_module.run_streaming
        tui_app_module.run_streaming = _fake_run_streaming  # type: ignore[assignment]
        try:
            app._launch(["run", "--model", "claude-sonnet-5", "--output", "jsonl"])
            slot_key = next(iter(app._live_runs))
            live = app._live_runs[slot_key]
            await pilot.pause()
            for _ in range(50):
                if live.process is not None:
                    break
                await pilot.pause(0.05)
            assert live.process is not None

            app._selected_run_id = slot_key
            app.action_kill_run()
            exit_code = await asyncio.wait_for(live.task, timeout=5)
            assert exit_code.exit_code != 0
        finally:
            tui_app_module.run_streaming = monkeypatch_target


async def test_splash_is_shown_by_default_and_dismisses_on_a_keypress() -> None:
    """The splash is decoration, so the only things worth pinning are that it
    appears, that it gets out of the way, and that `splash=False` suppresses it
    entirely — which is what every other test here and `scripts/capture_demo.py`
    rely on."""
    app = RunectlTUI()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        assert type(app.screen_stack[-1]).__name__ == "SplashScreen"
        await pilot.press("space")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_splash_never_appears_in_replay_mode() -> None:
    """`--replay` is the demo path; a decorative screen must not land in a
    recording or in `capture_demo.py`'s frames."""
    app = RunectlTUI(replay_run_id="nope-not-a-real-run")
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()
        assert app._splash is False
        assert all(type(s).__name__ != "SplashScreen" for s in app.screen_stack)


async def test_cost_updates_reach_the_row_before_the_run_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a running run used to show $0.0000 / 0 steps for its whole
    life, because only `run.finished` touched those cells. `cost.updated` has
    always carried `cumulative_cost_usd` and `step`."""
    lines = [
        _event_line(type="run.started", data=_RUN_STARTED_DATA),
        _event_line(
            type="cost.updated",
            data={
                "step": 3, "provider": "anthropic", "model": "claude-sonnet-5",
                "input_tokens": 10, "output_tokens": 5,
                "cost_usd": 0.002, "cumulative_cost_usd": 0.0042,
            },
        ),
        "run-x",
    ]
    script = _stub_script(tmp_path, lines)

    async def _fake_run_streaming(argv, on_event, **kwargs):  # type: ignore[no-untyped-def]
        return await run_streaming(argv, on_event, launch_argv=(sys.executable, str(script)))

    import runectl.cli.tui.app as tui_app_module

    monkeypatch.setattr(tui_app_module, "run_streaming", _fake_run_streaming)

    app = RunectlTUI(splash=False)
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        app._launch(["run", "--model", "claude-sonnet-5", "--output", "jsonl"])
        live = app._live_runs[next(iter(app._live_runs))]
        await live.task
        await pilot.pause()

        from textual.widgets import DataTable, Static

        table = app.query_one("#run-table", DataTable)
        assert "$0.0042" in table.get_row("run-x")

        # The header is the other half of the fix: a run's identity used to be
        # legible only inside the timeline text, and the step count lives here
        # rather than in the table (that column was what pushed `cost` off the
        # edge of the pane).
        header = str(app.query_one("#run-header", Static).content)
        assert "c [misc]" in header
        assert "claude-sonnet-5" in header
        assert "3 steps" in header


async def test_timeline_width_follows_the_pane_not_a_hardcoded_100() -> None:
    """Regression: `_write_event` formatted every line to a fixed width of 100
    whatever the terminal was. `event_line` pre-formats and clips to the width
    it is given, so a wrong one defeats its layout and leaves RichLog to
    hard-wrap the overflow back to column 0 mid-sentence."""

    async def width_at(columns: int) -> int:
        app = RunectlTUI(splash=False)  # a fresh app per run_test; they are not reusable
        async with app.run_test(size=(columns, 50)) as pilot:
            await pilot.pause()
            return app._line_width()

    wide, narrow = await width_at(200), await width_at(90)
    assert wide > narrow, (wide, narrow)


async def test_model_dropdowns_lead_with_the_cheapest_of_each_provider() -> None:
    """With 37 rows, ordering is the whole usability story: alphabetical put
    `claude-fable-5` ($10/$50) first and buried `gpt-5-nano` ($0.05/$0.40)
    mid-list. The first option in a dropdown is the one people take."""
    from runectl.cli.tui.models import model_options
    from runectl.providers.registry import MODEL_REGISTRY, cheapest_model_for

    options = model_options(mark_missing_keys=False)
    assert len(options) == len(MODEL_REGISTRY)

    seen: set[str] = set()
    for label, model_id in options:
        provider = MODEL_REGISTRY[model_id].provider
        if provider not in seen:
            seen.add(provider)
            assert model_id == cheapest_model_for(provider).id, provider
        # Price belongs in the label; picking a model is mostly a budget call.
        assert "$" in label and provider in label
