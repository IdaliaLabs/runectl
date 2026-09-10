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
from runectl.flags.judge import APPROVAL_POLICIES, ApprovalPolicy
from runectl.loop.context import ContextBuilder
from runectl.loop.runner import Runner, RunOutcome
from runectl.loop.state import Challenge
from runectl.providers.anthropic import AnthropicProvider
from runectl.providers.base import Provider, make_utility_summarizer
from runectl.providers.cost import CostLedger
from runectl.providers.google import GoogleProvider
from runectl.providers.keys import resolve_key
from runectl.providers.openai import OpenAIProvider
from runectl.providers.registry import (
    THINKING_LEVELS,
    ModelInfo,
    ThinkingLevel,
    UnknownModelError,
    cheapest_model_for,
)
from runectl.providers.registry import resolve as resolve_model
from runectl.providers.replay import RecordingProvider
from runectl.sandbox import arena_build
from runectl.sandbox.base import sandbox_session
from runectl.sandbox.docker import DockerSandbox
from runectl.trace.store import Store
from runectl.trace.writer import TraceWriter
from runectl.user_config import default_thinking as configured_default_thinking


def parse_thinking(value: str | None) -> ThinkingLevel | None:
    """Validate a raw --thinking string against the shared vocabulary (D20).

    Returns ``None`` when nothing was passed, so callers can distinguish "not
    given, use the configured/off default" from an explicit ``off``. Raises
    ``UsageError`` (exit 6) on a bad value — D20 asks for loud degradation, and
    letting a typo pass silently would undercut that on its very first flag.
    `--approval` validates the same way as of 2026-09-10 (`parse_approval`
    below); it used to be the exception this docstring warned about.
    """
    if value is None:
        return None
    if value not in THINKING_LEVELS:
        raise UsageError(f"--thinking {value!r} is not one of {', '.join(THINKING_LEVELS)}")
    return value  # narrowed to ThinkingLevel by the membership check above


def parse_approval(value: str) -> ApprovalPolicy:
    """Validate a raw --approval string against D11's closed set.

    Raises ``UsageError`` (exit 6) on anything else. Before 2026-09-10 the
    string went through unvalidated, and `FlagJudge` branched on `auto` and
    `strict` with everything else falling through to `gated` — so `--approval
    strcit` ran the whole challenge under a policy the user did not ask for and
    was never told about.

    That direction matters more than the typo does. `gated` is the *weakest* of
    the three from the false-flag subsystem's point of view: it is the only one
    that can auto-finalize on the deterministic checks, where `strict` finalizes
    nothing. Silently substituting it for a stricter request is a safety policy
    quietly downgraded, which is the wrong way for a flag to fail.
    """
    if value not in APPROVAL_POLICIES:
        raise UsageError(f"--approval {value!r} is not one of {', '.join(APPROVAL_POLICIES)}")
    return value  # narrowed to ApprovalPolicy by the membership check above


def finish(store: Store, writer: TraceWriter, run_id: str, outcome: RunOutcome) -> None:
    """Close the trace and write the run's terminal manifest.

    One place, so a new field on `RunOutcome` cannot reach `run.json` for
    `runectl run` but not for `runectl replay` — these two had already drifted
    once by the time this was factored out.
    """
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
        thinking_level=outcome.thinking_level,
    )


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
    approval: ApprovalPolicy = "gated"
    network: str | None = None
    max_steps: int | None = None
    max_cost: float = DEFAULT_MAX_COST_USD
    record: bool = False
    output: str | None = None
    # D20 — the *requested* level, or None to mean "use the configured
    # per-provider default, or off if none is set" (resolved in execute_run,
    # once the model's provider is known). Runner then resolves the
    # requested-or-defaulted level again against the model's real thinking
    # ceiling and records any clamp — two different resolutions, for two
    # different reasons: this one picks *what the user asked for*, Runner's
    # picks *what the model can actually do*.
    thinking: ThinkingLevel | None = None


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

    # D20 — an explicit --thinking always wins; otherwise fall back to the
    # provider's configured default (runectl config, Phase 2), or "off" if
    # nothing was ever set. `--model` itself is never defaulted (D5 intact) —
    # this only resolves the separate, optional thinking preference.
    thinking = (
        request.thinking if request.thinking is not None else configured_default_thinking(model_info.provider)
    )

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
            "thinking_requested": thinking,
        },
    )

    if request.record:
        provider = RecordingProvider(provider, store.cassette_path(run_id))
        utility_provider = RecordingProvider(utility_provider, store.cassette_path(run_id))

    resolved_output = request.output or ("human" if sys.stdout.isatty() else "jsonl")
    writer.set_on_emit(render_ndjson if resolved_output == "jsonl" else render_human)
    if resolved_output == "human":
        # The predecessor's take-over workflow: attach to the live container
        # while the agent is still working.
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
        thinking=thinking,
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

    finish(store, writer, run_id, outcome)
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
    approval: str = typer.Option(
        "gated", "--approval", help="gated|strict|auto — what a cleared candidate becomes (D11)"
    ),
    network: str | None = typer.Option(None, "--network"),
    max_steps: int | None = typer.Option(None, "--max-steps"),
    max_cost: float = typer.Option(
        DEFAULT_MAX_COST_USD,
        "--max-cost",
        help="Hard spend ceiling in USD for this run; 0 disables it",
    ),
    record: bool = typer.Option(False, "--record"),
    output: str | None = typer.Option(None, "--output", help="jsonl | human"),
    thinking: str | None = typer.Option(
        None,
        "--thinking",
        help="off|low|medium|high|xhigh|max — default is the configured provider "
        "default, or off (D20)",
    ),
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
                approval=parse_approval(approval),
                network=network,
                max_steps=max_steps,
                max_cost=max_cost,
                record=record,
                output=output,
                thinking=parse_thinking(thinking),
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
