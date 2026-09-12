# Contributing

This is currently a solo build, so treat this file as the rules the codebase holds itself
to — not an invitation-only process document. If you're reading it because you're
contributing, welcome, and start with [`ARCHITECTURE.md`](docs/ARCHITECTURE.md).

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

These come from [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) and each one exists because its absence
caused a specific failure in the predecessor.

**Only `cli/` prints.** No `print()`, no `typer.echo()`, no `rich` console anywhere in
`trace/`, `sandbox/`, `providers/`, `loop/`, `tools/`, `flags/`, or `categories/`. Core
code emits events; `cli/render.py` is the only thing that turns an event into characters
on a terminal. `tests/unit/test_render_boundary.py` enforces this.

**A registry row carries a dated source.** Every provider block in
`providers/registry.py` names the date it was checked and the URL it was checked against.
This table has been wrong twice — once on Anthropic pricing and context windows, once on
OpenAI pricing that sat unverified for six days — and both times the result was a cost
report that was confidently wrong rather than obviously broken. Adding or editing a row
without re-checking and re-dating its block is how that happens a third time.

**Never assume a provider's default is "off".** Most current models think by default and
some cannot be stopped; sending no thinking configuration is not the same as requesting no
thinking. A new row's `thinking_off_supported` must come from that provider's own
per-model documentation, and `tests/unit/test_thinking.py` sweeps every row to make sure
the clamp is recorded either way.

**No network surface, ever.** No `serve` command, no HTTP server, no listener, no SSE
tailer, no browser UI, no top-level `ui/` package — not "later," not "behind a flag."

`runectl tui` is the one in-terminal exception, allowed by D13's dated 2026-09-09
amendment, and it earns that only by never running the agent loop in-process: it spawns
`runectl run --output jsonl` as a subprocess and consumes the same NDJSON stream any
driving agent gets. **A change that makes the TUI call `execute_run()` in a thread, or
that puts agent logic under `cli/tui/`, breaks the decision** — the subprocess boundary is
the whole reason the amendment was defensible. Everything under `cli/tui/` is renderer
code and lives by every rule in this section.

**Triage never sees challenge identity.** `triage()` takes `(sandbox, category)`. Don't
add a parameter. Don't thread the name, the filenames, or the description in "just for
logging." The whole point is that a filename-gated fast path has nowhere to live.

**No pre-LLM solver.** No plugin hook, no "helper," no technique that runs before the
first model call. Solving techniques are tools in the arena image the agent chooses to
invoke.

**Structured results, never string prefixes.** A failure is `ToolResult(ok=False,
kind="error", ...)`, not a string starting with `[error]`. Same for `ExecResult`.

**One tool call per step.** Extras get a `blocked` result and a matching tool message.

**A sixth tool needs an argument in `docs/ARCHITECTURE.md` first.** The five-tool surface is
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

D1–D20 in [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) are locked for V1. Changing one means **editing
that file with a dated reason**, in the same change that alters the code — an amendment
(see D13's 2026-09-09 entry, which allows `runectl tui` without reopening the rest of
the lock) is that same discipline applied to widening a decision, not an exception to
it. Quietly diverging in code is the failure mode the file exists to prevent.

## Adding an event type

The event set is closed for V1 — twenty types as of `flag.rederived` (D3's 2026-09-10
amendment); `_ALL_PAYLOADS` is the authoritative count. If you genuinely need one more:

1. Add the payload class in `trace/events.py` with its `event_type` ClassVar
2. Add it to `_ALL_PAYLOADS` (that's what populates the type registry)
3. Document it in the "Event types" table in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#event-types)
4. Consider whether `cli/render.py` should show it specially
5. **If the event records a command that ran in the sandbox, add it to
   `exec_results_from_trace` in `sandbox/replay.py`** — that queue is positional, so an
   exec the replay doesn't know about consumes the *next* tool call's output and shifts
   every result after it. This step is here because skipping it is exactly what broke
   replay for a milestone (D3's 2026-09-10 amendment; `tests/integration/test_replay_fidelity.py`)

Step 5 has a matching rule in the other direction: a command must never reach
`Sandbox.exec` without an event recording it. `flag.rederived` exists because the D15
judge called `exec` directly and wrote nothing.

Payloads are frozen and `extra="forbid"` — keep them that way, so a malformed event is a
construction-time error rather than a bad line in the record.

## Style

- `ruff` with `E, F, I, UP, B, SIM`, line length 110
- `from __future__ import annotations` at the top of every module
- Module docstrings say what the module is *for* and cite the decision that shaped it.
  That convention is load-bearing here — the code is meant to be readable next to
  `docs/ARCHITECTURE.md`, and a reader should never have to guess why something is shaped oddly.
- Comments explain the non-obvious constraint, not the obvious mechanic.

## Numbers are unmeasured until bench says otherwise

Every step limit, budget, and threshold currently in the tree is a starting value carried
from the predecessor and explicitly marked unmeasured. Don't treat them as tuned, and
don't tune them by intuition — `runectl bench` (M8) is what moves them, and per
[`ARCHITECTURE.md`](docs/ARCHITECTURE.md) D16 they come down over time, not up.

## Commits

Present tense, describing the change and the decision it serves — the existing history is
the model:

```
Wire utility-model calls through the shared cost ledger; test the M3 retry gate
Vendor one bench challenge (plan §10.1) and fix a DockerSandbox daemon-touch bug
```
