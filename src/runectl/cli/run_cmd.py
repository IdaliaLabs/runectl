"""`runectl run` (D4 V1 command surface, plan §9.1).

Non-interactive end to end: resolve the challenge and category, resolve the
model/provider/key, start the sandbox, run the loop, persist the trace, and
exit with one of D4's honest exit codes. Nothing here prints — `render.py`'s
callback, attached to the writer, is the only thing that does.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import typer

from runectl.categories.loader import CategoryLoadError, CategoryNotFoundError
from runectl.categories.loader import load as load_category
from runectl.cli.render import render_human, render_ndjson
from runectl.config import CONTAINER_NAME_PREFIX, DEFAULT_MAX_COST_USD
from runectl.errors import ProviderError, RunectlError, SandboxError, UsageError
from runectl.flags.review import make_reviewer
from runectl.loop.context import ContextBuilder
from runectl.loop.runner import Runner, RunOutcome
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


@dataclass(frozen=True)
class RunResult:
    run_id: str
    outcome: RunOutcome


def _build_provider(model: ModelInfo, api_key: str) -> Provider:
    if model.provider == "anthropic":
        return AnthropicProvider(
            model_id=model.id, api_key=api_key, prompt_cache=model.supports_prompt_cache
        )
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
    if status.architecture_mismatch:
        typer.echo(
            arena_build.ARCH_WARNING.format(
                tag=status.tag,
                actual=status.architecture,
                expected=status.expected_architecture,
            ),
            err=True,
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


def challenge_from_file(path: Path) -> Challenge:
    """Load a challenge TOML, resolving its `files` relative to the TOML itself.

    Not relative to the working directory: a challenge directory is a unit that
    gets moved and vendored as a whole, and `runectl run --challenge
    bench/practice/easy-03/chal.toml` from the repo root has to find
    `easy-03/files/enc.txt` — not `./files/enc.txt`. Absolute paths are left
    alone so a one-off challenge can still point anywhere.
    """
    raw = tomllib.loads(path.read_text())
    base = path.parent
    files = tuple(
        Path(f) if Path(f).is_absolute() else (base / f) for f in raw.get("files", [])
    )
    return Challenge(
        name=raw["name"],
        category=raw["category"],
        description=raw.get("description", ""),
        files=files,
        flag_format=raw.get("flag_format"),
    )


@dataclass(frozen=True)
class RunRequest:
    """Everything one run needs beyond the challenge itself.

    Exists so `runectl bench` (M8) drives real runs through exactly the same
    code path as `runectl run` — a benchmark that measured a second, parallel
    implementation of the loop would be measuring the wrong thing.
    """

    model: str
    utility_model: str | None = None
    api_key: str | None = None
    approval: str = "gated"
    network: str | None = None
    max_steps: int | None = None
    max_cost: float = DEFAULT_MAX_COST_USD
    record: bool = False
    output: str | None = None


def execute_run(challenge: Challenge, request: RunRequest, *, store: Store | None = None) -> RunResult:
    """Resolve, run, persist. Raises `UsageError`/`SandboxError`/`ProviderError`;
    a finished run — solved, held, or exhausted — is a return value, not an exception."""
    try:
        category_data = load_category(challenge.category)
    except (CategoryNotFoundError, CategoryLoadError) as exc:
        raise UsageError(str(exc)) from exc

    try:
        model_info = resolve_model(request.model)
    except UnknownModelError as exc:
        raise UsageError(str(exc)) from exc

    _preflight_arena()

    key = resolve_key(model_info.provider, cli_flag=request.api_key)
    provider: Provider = _build_provider(model_info, key)

    # D5: utility calls (context/history summarization) default to the
    # cheapest model of the same provider and share the main loop's cost
    # ledger — never a hardcoded model, never untracked tokens.
    utility_model_info = (
        resolve_model(request.utility_model)
        if request.utility_model
        else cheapest_model_for(model_info.provider)
    )
    utility_key = (
        key
        if utility_model_info.provider == model_info.provider
        else resolve_key(utility_model_info.provider)
    )
    utility_provider: Provider = _build_provider(utility_model_info, utility_key)

    store = store or Store()
    run_id, writer = store.new_run(
        challenge_name=challenge.name,
        category=challenge.category,
        model=model_info.id,
        provider=model_info.provider,
        config_snapshot={
            "challenge": challenge.model_dump(mode="json"),
            "approval_policy": request.approval,
        },
    )

    if request.record:
        provider = RecordingProvider(provider, store.cassette_path(run_id))
        utility_provider = RecordingProvider(utility_provider, store.cassette_path(run_id))

    resolved_output = request.output or ("human" if sys.stdout.isatty() else "jsonl")
    writer.set_on_emit(render_ndjson if resolved_output == "jsonl" else render_human)
    if resolved_output == "human":
        # The predecessor's take-over workflow: attach to the live container
        # while the agent is still working (TEARDOWN.md item 12).
        typer.echo(
            f"run {run_id} — attach with: "
            f"docker exec -it {CONTAINER_NAME_PREFIX}{run_id} bash",
            err=True,
        )

    effective_category = (
        category_data.model_copy(update={"step_limit": request.max_steps})
        if request.max_steps
        else category_data
    )
    sandbox = DockerSandbox(network=request.network or effective_category.network, run_id=run_id)

    ledger = CostLedger()
    summarizer = make_utility_summarizer(utility_provider, utility_model_info, ledger)
    context = ContextBuilder(llm_summarize=summarizer)
    # D15 §2's disconfirmation pass, on the cheap model and the shared ledger.
    reviewer = make_reviewer(utility_provider, utility_model_info, ledger)

    runner = Runner(
        challenge=challenge,
        category=effective_category,
        model=model_info,
        provider=provider,
        sandbox=sandbox,
        writer=writer,
        approval_policy=request.approval,
        max_cost_usd=request.max_cost,
        ledger=ledger,
        context=context,
        reviewer=reviewer,
    )
    try:
        with sandbox_session(sandbox):
            outcome = runner.run()
    except RunectlError as exc:
        # Any run-ending error (sandbox/provider failure, or a UsageError such as
        # an invalid API key surfaced mid-loop): finalize the trace as an error
        # and close the writer before re-raising, so no half-written run is left
        # behind. run_command/bench map the exit_code.
        writer.close()
        store.finish_run(run_id, outcome="error", exit_code=exc.exit_code)
        raise

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
    return RunResult(run_id=run_id, outcome=outcome)


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
    max_cost: float = typer.Option(
        DEFAULT_MAX_COST_USD,
        "--max-cost",
        help="Hard spend ceiling in USD for this run; 0 disables it",
    ),
    record: bool = typer.Option(False, "--record"),
    output: str | None = typer.Option(None, "--output", help="jsonl | human"),
) -> None:
    """Solve one challenge end-to-end, writing a replayable trace (D3, D4)."""
    try:
        chal = _resolve_challenge(challenge, name, category, description, description_file, file, flag_format)
        result = execute_run(
            chal,
            RunRequest(
                model=model,
                utility_model=utility_model,
                api_key=api_key,
                approval=approval,
                network=network,
                max_steps=max_steps,
                max_cost=max_cost,
                record=record,
                output=output,
            ),
        )
    except (SandboxError, ProviderError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    except UsageError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=6) from exc

    typer.echo(result.run_id)
    raise typer.Exit(code=result.outcome.exit_code)


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
        return challenge_from_file(Path(challenge_path))
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
