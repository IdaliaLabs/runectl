# Contributing

This is currently a solo build, so treat this file as the rules the codebase holds itself
to — not an invitation-only process document. If you're reading it because you're
contributing, welcome, and start with [`DECISIONS.md`](DECISIONS.md).

## Setup

```bash
uv sync
uv run runectl --help
```

Python 3.12 exactly (pinned in `.python-version`; `requires-python = ">=3.12,<3.13"`).
3.13 is excluded for CTF-tooling compatibility breadth, not for lack of interest.

## The checks

These three run in CI on every push and PR, and they must all pass:

```bash
uv run mypy --strict src/
uv run ruff check .
uv run pytest
```

`mypy --strict` over `src/runectl/` is not negotiable — it's the direct structural answer
to the predecessor's stringly-typed control flow, and it's why tool results and trace
events are typed models rather than dicts.

## Tests need no daemon and no key

The entire suite runs against `StubSandbox` and `ScriptedProvider`. Nothing in `tests/`
may require Docker, a provider key, or network access. If your change can only be tested
with a live daemon or a live API, that's a signal the seam is in the wrong place —
`DockerSandbox` is tested against a mocked client, and the adapters are tested against
their canonical `Message`/`Completion` shapes.

Where to put a test:

- `tests/unit/` — one module's behavior, no subprocess
- `tests/integration/` — the CLI as a subprocess, `RUNECTL_HOME` pointed at `tmp_path`

Set `RUNECTL_HOME` (and `RUNECTL_CONFIG_HOME` if keys are involved) in any test that
touches the store. Never write into the developer's real run store.

## Rules a change must not break

These come from [`DECISIONS.md`](DECISIONS.md) and each one exists because its absence
caused a specific failure in the predecessor.

**Only `cli/` prints.** No `print()`, no `typer.echo()`, no `rich` console anywhere in
`trace/`, `sandbox/`, `providers/`, `loop/`, `tools/`, `flags/`, or `categories/`. Core
code emits events; `cli/render.py` is the only thing that turns an event into characters
on a terminal. `tests/unit/test_render_boundary.py` enforces this.

**No GUI, ever.** No `serve` command, no SSE tailer, no `ui/` package — not "later," not
"behind a flag." The event-stream/renderer split exists for testability and the
stdout-NDJSON / stderr-human duality, not as a seam for a future front end.

**Triage never sees challenge identity.** `triage()` takes `(sandbox, category)`. Don't
add a parameter. Don't thread the name, the filenames, or the description in "just for
logging." The whole point is that a filename-gated fast path has nowhere to live.

**No pre-LLM solver.** No plugin hook, no "helper," no technique that runs before the
first model call. Solving techniques are tools in the arena image the agent chooses to
invoke.

**Structured results, never string prefixes.** A failure is `ToolResult(ok=False,
kind="error", ...)`, not a string starting with `[error]`. Same for `ExecResult`.

**One tool call per step.** Extras get a `blocked` result and a matching tool message.

**A sixth tool needs an argument in `DECISIONS.md` first.** The five-tool surface is
locked; adding to it means demonstrating a category that shell genuinely cannot serve, and
writing that down before writing the code.

**Every LLM call goes through `complete_with_retry`.** Including utility calls. No
hardcoded cheap model, no untracked tokens, no silent no-op when a provider is absent.

**Adding a category is a data file.** If your new category needs a code change beyond
category-name-keyed triage commands, the schema is probably wrong — fix the schema.

**Keys never touch disk outside the keyring or the 0600 key file.** Never in a run's
config snapshot, never in a log line. If you add a field that could carry one, add a
redaction pattern to `trace/writer.py`.

**The trace stays the source of truth.** SQLite is derived and rebuildable. Don't add a
code path that requires the index to exist.

## Changing a locked decision

D1–D20 in [`DECISIONS.md`](DECISIONS.md) are locked for V1. Changing one means **editing
that file with a dated reason**, in the same change that alters the code — an amendment
(see D13's 2026-09-09 entry, which allows `runectl tui` without reopening the rest of
the lock) is that same discipline applied to widening a decision, not an exception to
it. Quietly diverging in code is the failure mode the file exists to prevent.

## Adding an event type

The event set is closed for V1. If you genuinely need a seventeenth:

1. Add the payload class in `trace/events.py` with its `event_type` ClassVar
2. Add it to `_ALL_PAYLOADS` (that's what populates the type registry)
3. Document it in [`docs/TRACE.md`](docs/TRACE.md)
4. Consider whether `cli/render.py` should show it specially

Payloads are frozen and `extra="forbid"` — keep them that way, so a malformed event is a
construction-time error rather than a bad line in the record.

## Style

- `ruff` with `E, F, I, UP, B, SIM`, line length 110
- `from __future__ import annotations` at the top of every module
- Module docstrings say what the module is *for* and cite the decision that shaped it.
  That convention is load-bearing here — the code is meant to be readable next to
  `DECISIONS.md`, and a reader should never have to guess why something is shaped oddly.
- Comments explain the non-obvious constraint, not the obvious mechanic.

## Numbers are unmeasured until bench says otherwise

Every step limit, budget, and threshold currently in the tree is a starting value carried
from the predecessor and explicitly marked unmeasured. Don't treat them as tuned, and
don't tune them by intuition — `runectl bench` (M8) is what moves them, and per
[`DECISIONS.md`](DECISIONS.md) D16 they come down over time, not up.

## Commits

Present tense, describing the change and the decision it serves — the existing history is
the model:

```
Wire utility-model calls through the shared cost ledger; test the M3 retry gate
Vendor one bench challenge (plan §10.1) and fix a DockerSandbox daemon-touch bug
```
