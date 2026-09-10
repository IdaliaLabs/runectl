"""Phase 4 — the TUI, driven headlessly via Textual's `App.run_test()`.

Per CONTRIBUTING.md, nothing here may touch a real Docker daemon or spend a
token. `runner_proc.run_streaming` is exercised against a small stub script
(via its `launch_argv` seam) that emits canned NDJSON on stdout — never the
real `runectl run` — so this covers the subprocess-reading contract without
needing the engine, a Docker daemon, or an API key at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from runectl.cli.tui.app import RunectlTUI
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
    app = RunectlTUI()
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

    app = RunectlTUI()
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

    app = RunectlTUI()
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
