"""The top-level Typer app (D4 V1 command surface, plan §9.1, §9.5).

Only this package renders (D13) — core code emits events and never prints.
Exit codes come from D4's table plus `errors.py`'s hierarchy; expected
failures are one-line stderr messages, never a raw traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import typer

from runectl import __version__
from runectl.categories.loader import CategoryLoadError, CategoryNotFoundError
from runectl.categories.loader import load as load_category
from runectl.cli import (
    arena_cmd,
    bench_cmd,
    config_cmd,
    flag_cmd,
    keys_cmd,
    models_cmd,
    run_cmd,
    runs_cmd,
    trace_cmd,
)
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.registry import ThinkingLevel, UnknownModelError
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.replay import ReplayProvider
from runectl.sandbox.base import sandbox_session
from runectl.sandbox.replay import ReplaySandbox, exec_results_from_trace
from runectl.trace.events import ToolCall, TriageResult
from runectl.trace.index import IndexDB
from runectl.trace.reader import TraceReader
from runectl.trace.store import Store


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"runectl {__version__}")
        raise typer.Exit()


app = typer.Typer(add_completion=False, help="runectl — an agentic CTF solver CLI (Idalia Labs).")


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Print the version and exit."
    ),
) -> None:
    return


app.command("run")(run_cmd.run_command)
app.add_typer(keys_cmd.app, name="keys")
app.add_typer(arena_cmd.app, name="arena")
app.add_typer(trace_cmd.app, name="trace")
app.add_typer(flag_cmd.app, name="flag")
app.add_typer(bench_cmd.app, name="bench")
app.add_typer(config_cmd.app, name="config")
app.add_typer(models_cmd.app, name="models")
app.add_typer(runs_cmd.app, name="runs")


@app.command("tui")
def tui_command(
    replay: str | None = typer.Option(
        None,
        "--replay",
        metavar="RUN_ID",
        help="Animate straight through a finished run's already-recorded trace "
        "(no sandbox, no provider, zero spend) instead of the normal live view. "
        "This is not `runectl replay` — it re-executes nothing.",
    ),
    playback_delay: float = typer.Option(
        0.6, "--playback-delay", help="Seconds between events in --replay mode"
    ),
) -> None:
    """Interactive, in-terminal view over runs (D13 amendment, 2026-09-09).

    Imported lazily so every other command's startup stays fast and doesn't
    pay Textual's import cost — this is the only command that needs it.
    """
    from runectl.cli.tui.app import RunectlTUI

    RunectlTUI(replay_run_id=replay, playback_delay_s=playback_delay).run()


index_app = typer.Typer(
    add_completion=False, help="Maintain the derived SQLite index (D3 — never authoritative)."
)


@index_app.command("rebuild")
def index_rebuild() -> None:
    store = Store()
    count = IndexDB(store.home / "index.db").rebuild(store)
    typer.echo(f"rebuilt index from {count} run(s)")


app.add_typer(index_app, name="index")


def _tool_call_sequence(trace_path: Path, artifacts_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    sequence: list[tuple[str, dict[str, Any]]] = []
    for event in TraceReader(trace_path, artifacts_dir):
        payload = event.payload()
        if isinstance(payload, ToolCall):
            sequence.append((payload.tool, payload.arguments))
    return sequence


@app.command("replay")
def replay_command(run_id: str, check: bool = typer.Option(False, "--check")) -> None:
    """Re-run a recorded run from its cassette — no daemon, no spend (D2, D5)."""
    store = Store()
    try:
        manifest = store.read_manifest(run_id)
    except FileNotFoundError as exc:
        typer.echo(f"no such run: {run_id}", err=True)
        raise typer.Exit(code=6) from exc

    cassette_path = store.cassette_path(run_id)
    if not cassette_path.exists():
        typer.echo(f"run {run_id} has no cassette — it wasn't run with --record", err=True)
        raise typer.Exit(code=6)

    challenge_data = manifest.config_snapshot.get("challenge")
    if not isinstance(challenge_data, dict):
        typer.echo(f"run {run_id}'s manifest has no recorded challenge to replay", err=True)
        raise typer.Exit(code=6)
    chal = Challenge.model_validate(challenge_data)

    try:
        category_data = load_category(manifest.category)
    except (CategoryNotFoundError, CategoryLoadError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc

    try:
        model_info = resolve_model(manifest.model)
    except UnknownModelError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc

    # A replay contacts no API, so it must not report spend. The ledger prices
    # whatever the provider returns, and ReplayProvider returns the *recorded*
    # usage — which would otherwise be re-billed into run.json and make a replay
    # look like it cost what the original run cost. Zero-priced model, zero
    # reported cost; "replay at zero spend" has to be true in the manifest too.
    model_info = model_info.model_copy(update={"price_in": 0.0, "price_out": 0.0})
    provider = ReplayProvider(cassette_path)
    exec_results = exec_results_from_trace(store.trace_path(run_id), store.artifacts_dir(run_id))
    sandbox = ReplaySandbox(exec_results)
    # Validated rather than cast: runectl itself only ever writes one of the
    # three, so a bad value here means a hand-edited or corrupt manifest, and
    # replaying it under a silently-substituted `gated` would be a replay that
    # does not reproduce the run (exit 6 instead).
    approval_policy = run_cmd.parse_approval(
        str(manifest.config_snapshot.get("approval_policy", "gated"))
    )
    # D20 — the *requested* level, read back from the config snapshot the
    # original run wrote (see run_cmd.execute_run). Runner re-resolves it
    # against `model_info` deterministically, so a replay reproduces the same
    # resolved level (and clamp, if any) the original run had — and, just as
    # important, the request hash it sends to ReplayProvider matches what
    # RecordingProvider recorded (providers/replay.py's `request_hash` now
    # includes the thinking config; a mismatch here would make every recorded
    # call look unrecognized).
    thinking_requested = cast(
        ThinkingLevel, manifest.config_snapshot.get("thinking_requested", "off")
    )

    triage_override = None
    for event in TraceReader(store.trace_path(run_id), store.artifacts_dir(run_id)):
        payload = event.payload()
        if isinstance(payload, TriageResult) and triage_override is None:
            triage_override = payload

    replay_run_id, writer = store.new_run(
        challenge_name=chal.name,
        category=chal.category,
        model=model_info.id,
        provider=model_info.provider,
        config_snapshot={"replay_of": run_id},
    )
    runner = Runner(
        challenge=chal,
        category=category_data,
        model=model_info,
        provider=provider,
        sandbox=sandbox,
        writer=writer,
        approval_policy=approval_policy,
        triage_override=triage_override,
        thinking=thinking_requested,
    )
    with sandbox_session(sandbox):
        outcome = runner.run()
    run_cmd.finish(store, writer, replay_run_id, outcome)

    typer.echo(replay_run_id)
    if check:
        original = _tool_call_sequence(store.trace_path(run_id), store.artifacts_dir(run_id))
        replayed = _tool_call_sequence(store.trace_path(replay_run_id), store.artifacts_dir(replay_run_id))
        if original != replayed:
            typer.echo(f"MISMATCH: {replay_run_id} diverged from {run_id}'s tool-call sequence", err=True)
            raise typer.Exit(code=1)
        # The outcome is checked too, and separately, because comparing only the
        # sequence is what let the judge's unrecorded re-derivation hide for a
        # milestone: a replay issued identical tool calls while silently
        # downgrading `solved` to `candidate`, and --check still said OK
        # (fixed 2026-09-10; `tests/integration/test_replay_fidelity.py`).
        recorded_outcome = store.read_manifest(run_id).outcome
        if outcome.outcome != recorded_outcome:
            typer.echo(
                f"MISMATCH: {replay_run_id} reproduced {run_id}'s tool-call sequence but "
                f"ended {outcome.outcome}, not {recorded_outcome}",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(
            f"OK: {replay_run_id} reproduces {run_id}'s tool-call sequence "
            f"and its {recorded_outcome} outcome at zero spend"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
