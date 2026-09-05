"""`runectl flag approve` — stub wired for M6 (plan §9.1, "What skeleton excludes").

The M4 skeleton's judge (`flags/judge.py`) auto-decides every `submit_flag`
call itself (accept on provenance, else reject and keep running), so there is
never a *pending* candidate for a human to approve yet. `approve` is wired
into the CLI surface now so `runectl flag approve <run_id>` is a real,
discoverable command from day one; M6 gives it a body.
"""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="Approve a pending flag candidate (lands in M6).")


@app.command("approve")
def approve(run_id: str, flag: str = typer.Option(None, "--flag")) -> None:
    typer.echo(
        "`flag approve` lands in M6 (the false-flag subsystem, D15) — the M4 skeleton's "
        "minimal judge auto-decides every submit_flag call, so there is no pending "
        "candidate to approve yet.",
        err=True,
    )
    raise typer.Exit(code=6)
