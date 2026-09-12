"""`runectl flag list` / `runectl flag approve` — the human half of D11.

Under the default `gated` policy a run that finds something it cannot fully
corroborate stops with exit code 2 and leaves the candidate in the trace rather
than claiming a solve. These two commands are what happens next: `list` shows
what is waiting and exactly which checks it failed, `approve` finalizes one.

Both are built for a driving agent as much as a person (D4): `list` emits JSON
lines on stdout, and `approve` is non-interactive with honest exit codes.
"""

from __future__ import annotations

import json

import typer

from runectl.trace.events import Event, FlagCandidate, FlagDecision
from runectl.trace.reader import TraceReader
from runectl.trace.store import Store
from runectl.trace.writer import TraceWriter

app = typer.Typer(add_completion=False, help="Review and finalize flag candidates held for approval.")


class PendingCandidate:
    """One candidate the judge held back, with the context to decide on it."""

    def __init__(self, *, step: int, flag: str, how_found: str, provenance_seq: int, reason: str) -> None:
        self.step = step
        self.flag = flag
        self.how_found = how_found
        self.provenance_seq = provenance_seq
        self.reason = reason

    def as_dict(self) -> dict[str, object]:
        return {
            "step": self.step,
            "flag": self.flag,
            "how_found": self.how_found,
            "provenance_seq": self.provenance_seq,
            "held_because": self.reason,
        }


def _read(run_id: str) -> tuple[Store, list[Event]]:
    store = Store()
    try:
        store.read_manifest(run_id)
    except FileNotFoundError as exc:
        typer.echo(f"no such run: {run_id}", err=True)
        raise typer.Exit(code=6) from exc
    events = list(TraceReader(store.trace_path(run_id), store.artifacts_dir(run_id)))
    return store, events


def pending_candidates(events: list[Event]) -> list[PendingCandidate]:
    """Candidates whose last recorded decision was `pending`.

    Walks the whole trace rather than stopping at the first hit: a run can hold
    more than one candidate, and an already-approved flag has a later
    `finalized` decision that must win over its earlier `pending` one.
    """
    candidates: dict[str, FlagCandidate] = {}
    decisions: dict[str, tuple[int, str, str]] = {}
    for event in events:
        payload = event.payload()
        if isinstance(payload, FlagCandidate):
            candidates[payload.flag] = payload
        elif isinstance(payload, FlagDecision):
            decisions[payload.flag] = (payload.step, payload.decision, payload.reason)

    held: list[PendingCandidate] = []
    for flag, (step, decision, reason) in decisions.items():
        if decision != "pending":
            continue
        candidate = candidates.get(flag)
        held.append(
            PendingCandidate(
                step=step,
                flag=flag,
                how_found=candidate.how_found if candidate else "",
                provenance_seq=candidate.provenance_seq if candidate else 0,
                reason=reason,
            )
        )
    return held


@app.command("list")
def list_candidates(run_id: str) -> None:
    """Print this run's pending candidates as JSON lines (exit 3 if there are none)."""
    _, events = _read(run_id)
    held = pending_candidates(events)
    for candidate in held:
        typer.echo(json.dumps(candidate.as_dict()))
    if not held:
        typer.echo(f"run {run_id} has no pending flag candidates", err=True)
        raise typer.Exit(code=3)


@app.command("approve")
def approve(
    run_id: str,
    flag: str | None = typer.Option(None, "--flag", help="Which candidate, if the run held several"),
) -> None:
    """Finalize a pending candidate, recording that a human — not the judge — did it."""
    store, events = _read(run_id)
    held = pending_candidates(events)
    if not held:
        typer.echo(
            f"run {run_id} has no pending flag candidates to approve "
            "(`runectl flag list` shows what a run held, if anything)",
            err=True,
        )
        raise typer.Exit(code=6)

    if flag is None:
        if len(held) > 1:
            typer.echo(
                f"run {run_id} held {len(held)} candidates — name one with --flag:", err=True
            )
            for candidate in held:
                typer.echo(f"  {candidate.flag}", err=True)
            raise typer.Exit(code=6)
        chosen = held[0]
    else:
        matched = [candidate for candidate in held if candidate.flag == flag]
        if not matched:
            typer.echo(f"run {run_id} has no pending candidate {flag!r}", err=True)
            raise typer.Exit(code=6)
        chosen = matched[0]

    # D3: the trace is append-only and the run is over, so the approval is a new
    # event on the end — not an edit of the decision the judge recorded.
    last_seq = events[-1].seq if events else 0
    writer = TraceWriter(
        store.trace_path(run_id), run_id, store.artifacts_dir(run_id), start_seq=last_seq
    )
    try:
        writer.emit(
            FlagDecision(
                step=chosen.step,
                flag=chosen.flag,
                decision="finalized",
                reason="approved out of band via `runectl flag approve` (D11)",
            )
        )
    finally:
        writer.close()
    store.approve_flag(run_id, flag=chosen.flag)
    typer.echo(chosen.flag)
