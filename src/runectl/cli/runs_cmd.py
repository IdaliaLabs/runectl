"""`runectl runs` — discover, inspect, and attach to runs (Phase 3).

Read-only, on purpose: there is no `runs rm`. Deleting a run's directory
deletes the record, and the record is the whole point of D3 — this command
family only ever reads `Store`/`IndexDB`, never writes.

`runs list`/`show` read the derived SQLite index and the authoritative
manifest respectively (D3: the index is never authoritative, so a stale or
missing index.db only means an empty `runs list` — `runectl index rebuild`
regenerates it from what's actually on disk). `runs ps`/`attach` are the
multi-instance visibility this family exists for: containers are named
`runectl-<run_id>` specifically so a human can find and attach to one
(D2's amendment), and this just does the `docker ps`/`docker exec` lookup for
you instead of asking you to remember the name.
"""

from __future__ import annotations

import json
import subprocess

import docker
import docker.errors
import typer

from runectl.config import CONTAINER_NAME_PREFIX
from runectl.trace.index import IndexDB
from runectl.trace.store import Store

app = typer.Typer(add_completion=False, help="List, inspect, and attach to runs.")


@app.command("list")
def runs_list(
    limit: int = typer.Option(20, "--limit", help="Most recent N runs"),
    category: str | None = typer.Option(None, "--category"),
    outcome: str | None = typer.Option(None, "--outcome"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    """Most recent runs first. Empty output (not an error) if the index is empty or stale —
    run `runectl index rebuild` if a run you know exists doesn't show up."""
    store = Store()
    rows = IndexDB(store.home / "index.db").list_runs()
    if category:
        rows = [r for r in rows if r.get("category") == category]
    if outcome:
        rows = [r for r in rows if r.get("outcome") == outcome]
    rows = rows[:limit]

    if output_json:
        for row in rows:
            typer.echo(json.dumps(row))
        return
    for row in rows:
        cost = row.get("cost_usd") or 0.0
        typer.echo(
            f"{row['run_id']}\t{row.get('outcome') or 'running'}\t{row['category']}\t"
            f"{row['model']}\t${cost:.4f}\t{row.get('steps_used') or 0} steps"
        )


@app.command("show")
def runs_show(
    run_id: str,
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    store = Store()
    try:
        manifest = store.read_manifest(run_id)
    except FileNotFoundError as exc:
        typer.echo(f"no such run: {run_id}", err=True)
        raise typer.Exit(code=6) from exc

    if output_json:
        typer.echo(manifest.model_dump_json())
        return

    typer.echo(f"{manifest.run_id}  {manifest.challenge_name} [{manifest.category}]")
    typer.echo(f"  model: {manifest.model} ({manifest.provider})")
    typer.echo(f"  thinking: {manifest.thinking_level}")
    typer.echo(f"  outcome: {manifest.outcome or 'running'}  exit_code={manifest.exit_code}")
    typer.echo(f"  flag: {manifest.flag or '-'}")
    ratio = (manifest.progress_steps / manifest.steps_used * 100) if manifest.steps_used else 0.0
    typer.echo(
        f"  steps: {manifest.steps_used} ({manifest.progress_steps} with progress, {ratio:.0f}%) "
        f"· {manifest.blocked_steps} blocked"
    )
    typer.echo(f"  cost: ${manifest.cost_usd:.4f}")
    if manifest.approved_at:
        typer.echo("  approved by a human (runectl flag approve)")


@app.command("ps")
def runs_ps() -> None:
    """Live `runectl-<run_id>` containers — the multi-instance visibility (D2's amendment)."""
    try:
        client = docker.from_env()
        containers = client.containers.list(filters={"name": CONTAINER_NAME_PREFIX})
    except docker.errors.DockerException as exc:
        typer.echo(f"could not reach the Docker daemon: {exc}", err=True)
        raise typer.Exit(code=4) from exc

    if not containers:
        typer.echo("no live runectl containers")
        return
    for container in containers:
        name = container.name or ""
        run_id = name.removeprefix(CONTAINER_NAME_PREFIX)
        typer.echo(f"{run_id}\t{container.status}\t{name}")


@app.command("attach")
def runs_attach(
    run_id: str,
    exec_now: bool = typer.Option(
        False, "--exec", help="Run the docker exec command directly instead of only printing it"
    ),
) -> None:
    """Prints (or runs) the same attach command `runectl run` shows on start."""
    command = ["docker", "exec", "-it", f"{CONTAINER_NAME_PREFIX}{run_id}", "bash"]
    if not exec_now:
        typer.echo(" ".join(command))
        return
    result = subprocess.run(command, check=False)
    raise typer.Exit(code=result.returncode)
