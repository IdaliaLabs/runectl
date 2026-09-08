"""`runectl run` (D4 V1 command surface, plan §9.1).

Non-interactive end to end: resolve the challenge and category, resolve the
model/provider/key, start the sandbox, run the loop, persist the trace, and
exit with one of D4's honest exit codes. Nothing here prints — `render.py`'s
callback, attached to the writer, is the only thing that does.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import typer

from runectl.categories.loader import CategoryLoadError, CategoryNotFoundError
from runectl.categories.loader import load as load_category
from runectl.cli.render import render_human, render_ndjson
from runectl.errors import ProviderError, SandboxError, UsageError
from runectl.loop.context import ContextBuilder
from runectl.loop.runner import Runner
from runectl.loop.state import Challenge
from runectl.providers.anthropic import AnthropicProvider
from runectl.providers.base import Provider, make_utility_summarizer
from runectl.providers.cost import CostLedger
from runectl.providers.google import GoogleProvider
from runectl.providers.keys import resolve_key
from runectl.providers.openai import OpenAIProvider
from runectl.providers.registry import ModelInfo, UnknownModelError, cheapest_model_for
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.replay import RecordingProvider
from runectl.sandbox import arena_build
from runectl.sandbox.base import sandbox_session
from runectl.sandbox.docker import DockerSandbox
from runectl.trace.store import Store


def _build_provider(model: ModelInfo, api_key: str) -> Provider:
    if model.provider == "anthropic":
        return AnthropicProvider(model_id=model.id, api_key=api_key)
    if model.provider == "openai":
        return OpenAIProvider(model_id=model.id, api_key=api_key)
    return GoogleProvider(model_id=model.id, api_key=api_key)


def _preflight_arena() -> None:
    """Fail fast, with instructions, if the sandbox image isn't usable (D2, D17).

    Deliberately not interactive and deliberately not a silent build: D2 says a
    run gates on the image existing and never builds one behind your back, and
    D4 says nothing may prompt mid-run. So this reports the remedies — including
    the interactive `runectl arena ensure` — and exits 4. Running it here, before
    the run directory is created, means a machine with no arena image costs the
    user nothing and leaves no half-run behind.
    """
    status = arena_build.inspect()  # SandboxError (exit 4) if the daemon is down
    if not status.present:
        raise SandboxError(
            f"arena image {status.tag} is not built.\n\n{arena_build.REMEDY_MESSAGE}"
        )
    if status.stale:
        # A warning, not a failure: the image still works, it is just older than
        # the Dockerfile in the tree. Only cli/ prints (D13), which is why this
        # lives here rather than in the sandbox layer.
        typer.echo(
            f"warning: {status.tag} was built from a different Dockerfile than the one in "
            "this tree — its toolset may be out of date. Run `runectl arena build` to refresh.",
            err=True,
        )


def _challenge_from_file(path: Path) -> Challenge:
    raw = tomllib.loads(path.read_text())
    files = tuple(Path(f) for f in raw.get("files", []))
    return Challenge(
        name=raw["name"],
        category=raw["category"],
        description=raw.get("description", ""),
        files=files,
        flag_format=raw.get("flag_format"),
    )


def run_command(
    challenge: str | None = typer.Option(None, "--challenge", help="Path to a challenge TOML file"),
    name: str | None = typer.Option(None, "--name"),
    category: str | None = typer.Option(None, "--category"),
    description: str | None = typer.Option(None, "--description"),
    description_file: str | None = typer.Option(None, "--description-file"),
    file: list[str] = typer.Option([], "--file"),
    flag_format: str | None = typer.Option(None, "--flag-format"),
    model: str = typer.Option(..., "--model"),
    utility_model: str | None = typer.Option(None, "--utility-model"),
    api_key: str | None = typer.Option(None, "--api-key"),
    approval: str = typer.Option("gated", "--approval"),
    network: str | None = typer.Option(None, "--network"),
    max_steps: int | None = typer.Option(None, "--max-steps"),
    record: bool = typer.Option(False, "--record"),
    output: str | None = typer.Option(None, "--output", help="jsonl | human"),
) -> None:
    """Solve one challenge end-to-end, writing a replayable trace (D3, D4)."""
    try:
        chal = _resolve_challenge(challenge, name, category, description, description_file, file, flag_format)

        try:
            category_data = load_category(chal.category)
        except (CategoryNotFoundError, CategoryLoadError) as exc:
            raise UsageError(str(exc)) from exc

        try:
            model_info = resolve_model(model)
        except UnknownModelError as exc:
            raise UsageError(str(exc)) from exc

        _preflight_arena()

        key = resolve_key(model_info.provider, cli_flag=api_key)
        provider: Provider = _build_provider(model_info, key)

        # D5: utility calls (context/history summarization) default to the
        # cheapest model of the same provider and share the main loop's cost
        # ledger — never a hardcoded model, never untracked tokens.
        utility_model_info = (
            resolve_model(utility_model) if utility_model else cheapest_model_for(model_info.provider)
        )
        utility_key = key if utility_model_info.provider == model_info.provider else resolve_key(
            utility_model_info.provider
        )
        utility_provider: Provider = _build_provider(utility_model_info, utility_key)

        store = Store()
        run_id, writer = store.new_run(
            challenge_name=chal.name,
            category=chal.category,
            model=model_info.id,
            provider=model_info.provider,
            config_snapshot={"challenge": chal.model_dump(mode="json"), "approval_policy": approval},
        )

        if record:
            provider = RecordingProvider(provider, store.cassette_path(run_id))
            utility_provider = RecordingProvider(utility_provider, store.cassette_path(run_id))

        resolved_output = output or ("human" if sys.stdout.isatty() else "jsonl")
        writer.set_on_emit(render_ndjson if resolved_output == "jsonl" else render_human)

        effective_category = (
            category_data.model_copy(update={"step_limit": max_steps}) if max_steps else category_data
        )
        sandbox = DockerSandbox(network=network or effective_category.network)

        ledger = CostLedger()
        summarizer = make_utility_summarizer(utility_provider, utility_model_info, ledger)
        context = ContextBuilder(llm_summarize=summarizer)

        runner = Runner(
            challenge=chal,
            category=effective_category,
            model=model_info,
            provider=provider,
            sandbox=sandbox,
            writer=writer,
            approval_policy=approval,
            ledger=ledger,
            context=context,
        )
        try:
            with sandbox_session(sandbox):
                outcome = runner.run()
        except (SandboxError, ProviderError) as exc:
            writer.close()
            store.finish_run(run_id, outcome="error", exit_code=exc.exit_code)
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=exc.exit_code) from exc

        writer.close()
        store.finish_run(
            run_id,
            outcome=outcome.outcome,
            exit_code=outcome.exit_code,
            flag=outcome.flag,
            cost_usd=outcome.cost_usd,
            steps_used=outcome.steps_used,
            progress_steps=outcome.progress_steps,
            blocked_steps=outcome.blocked_steps,
        )
        typer.echo(run_id)
        raise typer.Exit(code=outcome.exit_code)
    except SandboxError as exc:
        # Raised by the arena preflight, before any run directory exists.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    except UsageError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc


def _resolve_challenge(
    challenge_path: str | None,
    name: str | None,
    category: str | None,
    description: str | None,
    description_file: str | None,
    files: list[str],
    flag_format: str | None,
) -> Challenge:
    if challenge_path:
        return _challenge_from_file(Path(challenge_path))
    if not name or not category:
        raise UsageError("either --challenge or both --name and --category are required")
    desc = description or (Path(description_file).read_text() if description_file else "")
    return Challenge(
        name=name,
        category=category,
        description=desc,
        files=tuple(Path(f) for f in files),
        flag_format=flag_format,
    )
