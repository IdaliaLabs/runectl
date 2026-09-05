# runectl

An agentic CTF solver CLI (Idalia Labs). Bring your own provider key, point it at a
challenge, and it works the challenge inside a per-challenge Docker sandbox, writing a
complete, replayable trace of everything it tried.

CLI-only, permanently — there is no GUI, no `serve` command, no `ui/` package, ever.

See `DECISIONS.md` for the locked architecture. License: undecided, parked (no license
file yet — nothing here is published).

## Development

```bash
uv sync
uv run mypy --strict src/
uv run ruff check .
uv run pytest
```
