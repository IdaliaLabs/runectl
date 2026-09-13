# Contributing

This is a solo build. This file is the set of rules the codebase holds itself to, not a
process document. Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Setup

```bash
uv sync
uv run runectl --help
```

Python 3.12 exactly (pinned in `.python-version`; `requires-python = ">=3.12,<3.13"`). 3.13
is excluded for CTF-tooling compatibility breadth.

## The checks

All three run in CI on every push and PR and must pass:

```bash
uv run mypy --strict src/
uv run ruff check .
uv run pytest
```

`mypy --strict` over `src/runectl/` is non-negotiable. It is the structural answer to the
predecessor's stringly-typed control flow, and the reason tool results and trace events are
typed models rather than dicts.

## Tests need no daemon and no key

The entire suite runs against `StubSandbox` and `ScriptedProvider`. Nothing in `tests/` may
require Docker, a provider key, or network access. A change testable only against a live
daemon or live API indicates a misplaced seam: `DockerSandbox` is tested against a mocked
client, and the adapters against their canonical `Message`/`Completion` shapes.

| Directory | Contents |
|---|---|
| `tests/unit/` | One module's behavior, no subprocess |
| `tests/integration/` | The CLI as a subprocess, `RUNECTL_HOME` pointed at `tmp_path` |

Set `RUNECTL_HOME` (and `RUNECTL_CONFIG_HOME` when keys are involved) in any test that
touches the store. Never write into a developer's real run store.

## Rules a change must not break

Each rule comes from [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and exists because its
absence caused a specific failure in the predecessor.

**Only `cli/` prints.** No `print()`, no `typer.echo()`, no `rich` console in `trace/`,
`sandbox/`, `providers/`, `loop/`, `tools/`, `flags/` or `categories/`. Core code emits
events; `cli/render.py` is the only module that turns an event into terminal output.
Enforced by `tests/unit/test_render_boundary.py`.

**A registry row carries a dated source.** Every provider block in
`providers/registry.py` names the date it was checked and the URL or probe it was checked
against. This table has been wrong three times — Anthropic pricing and context windows,
OpenAI pricing left unverified for six days, and a dozen OpenAI capability flags that were
read off documentation rather than probed. Each time the result was a confidently wrong
number rather than an obvious break.

**Capability claims come from a live probe, not a page.** Thinking ceilings, `off` support
and endpoint compatibility are observable only by calling the model with the tool schema
`runectl` actually sends. A bare probe without tools reported `xhigh` as usable on models
that reject tools entirely.

**Never assume a provider's default is "off".** Most current models think by default and
some cannot be stopped; sending no thinking configuration is not the same as requesting
none. A new row's `thinking_off_supported` must be probed, and `tests/unit/test_thinking.py`
sweeps every row to confirm the clamp is recorded either way.

**No network surface, ever.** No `serve` command, no HTTP server, no listener, no SSE
tailer, no browser UI, no top-level `ui/` package — not later, not behind a flag.

`runectl tui` is the one in-terminal exception, allowed by D13's dated 2026-09-09 amendment,
and only because it never runs the agent loop in-process: it spawns `runectl run --output
jsonl` as a subprocess and consumes the same NDJSON stream any driving agent gets. A change
that makes the TUI call `execute_run()` in a thread, or that puts agent logic under
`cli/tui/`, breaks the decision. Everything under `cli/tui/` is renderer code and obeys
every rule in this section.

**Triage never sees challenge identity.** `triage()` takes `(sandbox, category)`. Do not add
a parameter. Do not thread the name, the filenames or the description through it for
logging. A filename-gated fast path must have nowhere to live.

**No pre-LLM solver.** No plugin hook, no helper, no technique that runs before the first
model call. Solving techniques are tools in the arena image that the agent chooses to
invoke.

**Structured results, never string prefixes.** A failure is `ToolResult(ok=False,
kind="error", ...)`, not a string starting with `[error]`. Same for `ExecResult`.

**One tool call per step.** Extras get a `blocked` result and a matching tool message.

**A sixth tool needs an argument in `docs/ARCHITECTURE.md` first.** The five-tool surface is
locked. Adding to it requires demonstrating a category shell cannot serve, written down
before the code.

**Every LLM call goes through `complete_with_retry`,** including utility calls. No hardcoded
cheap model, no untracked tokens, no silent no-op when a provider is absent.

**Adding a category is a data file.** A new category needing a code change beyond
category-name-keyed triage commands indicates a schema problem; fix the schema.

**Keys never touch disk outside the keyring or the 0600 key file.** Never in a run's config
snapshot, never in a log line. A new field that could carry one needs a matching redaction
pattern in `trace/writer.py`.

**The trace stays the source of truth.** SQLite is derived and rebuildable. No code path may
require the index to exist.

## Changing a locked decision

D1–D20 in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) are locked for V1. Changing one
means editing that file with a dated reason in the same change that alters the code.
Amendments (see D13's 2026-09-09 entry, which allows `runectl tui` without reopening the
rest of the lock) are the same discipline applied to widening a decision, not an exception
to it. Diverging in code without the record is the failure mode the file prevents.

CLAUDE.md additionally requires every amendment to land in both
`docs/ARCHITECTURE.md` and the private decision log.

## Adding an event type

The event set is closed for V1 — twenty types as of `flag.rederived` (D3's 2026-09-10
amendment). `_ALL_PAYLOADS` in `trace/events.py` is the authoritative count. To add one:

1. Add the payload class in `trace/events.py` with its `event_type` ClassVar.
2. Add it to `_ALL_PAYLOADS`, which populates the type registry.
3. Document it in the event-types table in
   [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#event-types).
4. Decide whether `cli/render.py` should render it specially.
5. **If the event records a command that ran in the sandbox, add it to
   `exec_results_from_trace` in `sandbox/replay.py`.** That queue is positional: an exec the
   replay does not know about consumes the next tool call's output and shifts every result
   after it. Skipping this step broke replay for a full milestone (D3's 2026-09-10
   amendment; `tests/integration/test_replay_fidelity.py`).

Step 5 has a converse: a command must never reach `Sandbox.exec` without an event recording
it. `flag.rederived` exists because the D15 judge called `exec` directly and wrote nothing.

Payloads are frozen with `extra="ignore"`. Frozen keeps a malformed event a
construction-time error; `ignore` rather than `forbid` is deliberate, so that removing a
field from a payload class does not make every trace recorded before the removal unreadable
(fixed 2026-09-10 — see the comment on `EventPayload.model_config`).

## Style

- `ruff` with `E, F, I, UP, B, SIM`, line length 110.
- `from __future__ import annotations` at the top of every module.
- Module docstrings state what the module is for and cite the decision that shaped it. The
  code is meant to be read next to `docs/ARCHITECTURE.md`; an oddly shaped module states its
  reason.
- Comments explain the non-obvious constraint, not the obvious mechanic.

## Numbers are unmeasured until bench says otherwise

Every step limit, budget and threshold in the tree is a starting value carried from the
predecessor and marked unmeasured. They are not tuned, and they are not tuned by intuition:
`runectl bench` (M8) is what moves them, and per
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) D16 they come down over time, not up.

## Commits

Present tense, describing the change and the decision it serves. The existing history is the
model:

```
Wire utility-model calls through the shared cost ledger; test the M3 retry gate
Vendor one bench challenge and fix a DockerSandbox daemon-touch bug
```
