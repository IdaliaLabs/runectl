# Architecture

What `runectl` is, what each module owns, what happens during a run, the trace and
category file formats, and — in one place — the design decisions that shaped all of it
and why. This file used to be split across `ARCHITECTURE.md`, `TRACE.md`, `CATEGORIES.md`
and a separate `DECISIONS.md`; they're combined here so the *what* and the *why* sit next
to each other instead of across four files. In-code comments cite these decisions by
number (`D3`, `D13`, …) — grep this file for the number.

## What runectl is

`runectl` hands an AI agent a CTF challenge — name, category, the description you were
given, any provided files — plus your own provider API key, and it works the challenge
inside a disposable Docker sandbox, one shell command at a time, until it finds a flag or
runs out of budget. Every step is written to an append-only trace as it happens, so a run
is checkable after the fact regardless of whether it succeeded. It is a CLI, not a
service: no server, nothing that listens on a port, and (with one narrow, subprocess-only
exception — the TUI, see D13) nothing that keeps state outside the files it writes.

## File structure

```
src/runectl/
    config.py           paths + tunable defaults (all starting values, none tuned)
    errors.py           typed errors mapped 1:1 onto the D4 exit codes
    ids.py              sortable run ids: YYYYMMDD-HHMMSS-<6hex>

    cli/                the ONLY package allowed to print
        app.py          Typer root; wires subcommands; hosts `replay`, `index`, `tui`
        run_cmd.py      `runectl run` — resolves everything, runs the loop, exits honestly
        trace_cmd.py    `runectl trace show`
        keys_cmd.py     `runectl keys set|list|rm`
        arena_cmd.py    `runectl arena build|status`
        flag_cmd.py     `runectl flag list` / `flag approve`
        bench_cmd.py    `runectl bench run`
        config_cmd.py   `runectl config set|get|list|path` (D20 — preferences, never keys)
        models_cmd.py   `runectl models list`
        runs_cmd.py     `runectl runs list|show|ps|attach` — read-only, on the derived index
        render.py       two pure event renderers: NDJSON→stdout, human→stderr
        tui/            `runectl tui` — the D13-amendment interactive view (M9). Still
                        `cli/` code under every existing rule: it renders, it never
                        contains agent logic, and every run it launches is a
                        `runectl run` subprocess, never an in-process loop call

    trace/              the record
        events.py       the typed payloads (20 as of D3's 2026-09-10 amendment) + the
                        versioned envelope — `_ALL_PAYLOADS` is the live count
        writer.py       append-only writer: redact secrets, spill >8KB, flush+fsync
        reader.py       lazy ordered reader that tolerates a torn final line
        store.py        run directories and the run.json manifest
        index.py        the derived, rebuildable SQLite index

    sandbox/            where commands actually run
        base.py         the Sandbox Protocol + ExecResult. Zero Docker imports.
        docker.py       DockerSandbox — one container per run
        stub.py         StubSandbox — in-process fake, no daemon
        replay.py       ReplaySandbox — serves recorded ExecResults
        arena_build.py  build/status for runectl/arena:kali

    providers/          talking to models
        base.py         Provider Protocol, canonical Message/Completion, retry path
        registry.py     the model table — provider and capabilities, never prefix sniffing
        keys.py         key resolution and storage
        cost.py         the cost ledger every call lands in
        anthropic.py openai.py google.py    the three adapters
        replay.py       RecordingProvider / ReplayProvider (cassettes)
        scripted.py     ScriptedProvider — hand-written completions for tests

    tools/              the five tools
        schema.py       defined once; OpenAI/Anthropic/Google schemas are derived
        dispatch.py     tool call → sandbox action
        results.py      ToolResult — structured, never an "[error] ..." string

    loop/               the agent
        state.py        Challenge + the single RunState dataclass
        runner.py       decide → one tool call → observe
        triage.py       the deterministic pre-LLM pass
        context.py      system prompt, output summarization, dedupe, compaction
        nudges.py       the earned anti-failure nudges, as pure functions

    flags/
        judge.py        the D15 pipeline: plausibility -> provenance -> decoy ->
                        corroboration -> flag format -> re-derivation
        plausibility.py decoys.py

    categories/         category data, loaded at runtime
        schema.py       what every category TOML validates against
        loader.py       TOML → Category
        pwn.toml web.toml crypto.toml forensics.toml rev.toml misc.toml osint.toml network.toml
```

## How data moves

What `runectl run` does, start to finish:

1. **Resolve the challenge** — from `--challenge` TOML or the individual flags. A missing
   name/category is a `UsageError` (exit 6) before anything else happens.
2. **Load category data** — `categories/<name>.toml` → a validated `Category`. Malformed
   data fails loudly here, never silently.
3. **Resolve the model and key** — `--model` against the registry (unknown id is a hard
   error, never a guess), then the key by precedence, then the right adapter.
4. **Resolve the utility model** — `--utility-model`, or the cheapest registered model of
   the same provider. Wired to the same `CostLedger`.
5. **Open the run** — create `runs/<run_id>/`, write the initial `run.json`, open the
   trace writer, attach the renderer.
6. **Start the sandbox** — one container from `runectl/arena:kali`, with `mem_limit=2g`,
   2 CPUs, `no-new-privileges`, non-root, never `--privileged`, network from the category
   or `--network`.
7. **Upload inputs** — every `--file` is copied in and then *verified* to have landed. A
   file that didn't make it is a hard error before the first paid call, not a notice the
   agent is expected to notice.
8. **Triage** — a fixed, category-parameterized command set (`ls -la`, `file *`, plus
   per-category additions like `checksec` or `exiftool`). Results go into the trace as one
   `triage.result` event and into the model's first message.
9. **The loop**, up to the step limit:
   - emit `llm.request`, call the provider through the retry path
   - emit `cost.updated` and `llm.response`
   - no tool call? push a nudge (`act, don't ask` if the model is asking permission) and
     continue
   - exactly one tool call is executed; any extras get a structured `blocked` result and a
     matching tool message so the next request stays valid
   - `submit_flag` is intercepted by the runner and handed to the judge; everything else
     goes to the dispatcher and then to the sandbox
   - emit `tool.call` / `tool.result`, render the output into context (deduped by digest,
     summarized if oversized); history is compacted at the top of the next step if it has
     outgrown its budget
   - after two consecutive non-progress steps, emit `strategy.shift` and inject the
     "pick a genuinely different hypothesis class" nudge
10. **Finish** — emit `run.finished`, rewrite `run.json` with the outcome, print the run
    id, exit with the outcome's code.

Every one of those steps that emits something writes through the same path:
`TraceWriter.emit` redacts known key shapes from every string in the payload, spills any
string over 8KB to `artifacts/<digest>.txt` and replaces it in-line with a reference,
then appends the line to `trace.jsonl` and `fsync`s. Nothing else ever writes to that
file. `render.py` reads the same event stream that just got written — to stderr as a
human-readable line, or to stdout as the raw JSON, depending on `--output` — so the human
view can never show you something the trace doesn't contain. `index.db` (SQLite) is
rebuilt from `runs/` on disk whenever `runectl index rebuild` runs; it answers cross-run
questions and can be deleted without losing anything, because `trace.jsonl` is what's
authoritative.

**The TUI's data path is one hop removed.** `runectl tui` never touches the loop, the
sandbox, or a provider directly. Launching a run from its modal spawns
`runectl run --output jsonl ...` as a subprocess and parses the same NDJSON stream any
other driving agent would read from stdout; browsing a finished run reads
`trace.jsonl` straight off disk through the same `TraceReader` the CLI itself uses.
`runectl tui --replay <run_id>` is a third mode — no subprocess, no sandbox, no provider —
that just re-plays an already-written trace at a fixed pace for a live demo.

## The three rules that shape everything

**1. Only `cli/` prints.** Core code emits typed events into the trace and returns
values. It never writes to stdout or stderr. This is what keeps the loop testable and
scriptable — and it's why the human renderer is a pure function of the event stream: it
literally cannot show you something that isn't in the trace.

**2. Files are the source of truth.** `trace.jsonl` is the record. SQLite is a derived
index you can delete and rebuild. If the process is SIGKILLed, whatever reached disk is
still a valid, replayable prefix.

**3. Every LLM call goes through one path.** Main loop calls and internal utility calls
(summarization) both go through `complete_with_retry`, into the same cost ledger. There
is no hardcoded cheap model anywhere, and no call that silently no-ops when a provider is
absent.

## Triage cannot cheat

`triage(sandbox, category)` takes exactly those two arguments. It never receives the
challenge name, its filenames, or its description, so there is nowhere to put a fast-path
keyed on `logs.txt` or `REChallenge1.zip`. `tests/unit/test_triage.py` asserts the command
set is identical across two runs differing only in challenge name and files.

There is no plugin hook and no pre-LLM solver of any kind. Techniques like PNG-LSB
extraction exist only as tools in the arena image that the agent may choose to invoke.

## The five tools

`run_command`, `run_gdb`, `write_file`, `search_flag`, `submit_flag` — defined once in
`tools/schema.py`, with the per-provider wire formats *derived* from that one definition.

- `run_command` is the workhorse. `long_running: true` raises the timeout from 30s to
  120s for sqlmap/hashcat/gobuster-shaped work.
- `run_gdb` is batch-only, always. It builds `gdb -q -batch -ex ... <binary>`, so it can
  never open an interactive session and hang the loop.
- `write_file` writes into `/ctf/`. Absolute paths and `..` segments are rejected.
- `search_flag` runs `grep -rnoIE` under `/ctf/`, capped at 200 matches. Line numbers are
  kept because they're provenance.
- `submit_flag` never reaches the dispatcher — it touches no sandbox — so the runner
  intercepts it and hands it to the judge.

A sixth tool gets added only if a category demonstrably can't be served by shell, and the
argument for it goes in this file's design-decisions section first.

Results are always a structured `ToolResult` with `ok`, `kind` (`output` / `error` /
`blocked`), streams, exit code, duration and truncation — never an `[error]`-prefixed
string the model has to parse out of prose.

## Run state

One `RunState` dataclass, owned by the loop. Collaborators (`ToolDispatcher`,
`ContextBuilder`, the judge, the nudges) are plain objects with constructor dependencies
that take what they need and return values the loop applies. None of them mutate
`RunState`. That's what makes the loop unit-testable, and it is the direct structural
answer to the predecessor tool's six mixins sharing ~30 implicit attributes (see D6).

---

## The trace format

The trace is the run's complete record: everything else — the manifest, the SQLite index,
the human timeline, replay — is derived from it. It is what makes a solve checkable after
the fact and a failure diagnosable.

Design rules (D3, below):

- **Append-only JSONL is the source of truth.** No database is required for a run to
  exist, be read, or be replayed.
- **Flushed and `fsync`ed after every event.** A `SIGKILL` mid-run leaves a valid
  prefix, not a corrupt file.
- **Greppable.** Any single value over 8 KB is spilled to `artifacts/` and referenced by
  digest, so lines stay a readable size.
- **Secrets never land in it.** The writer redacts known key shapes before anything
  reaches disk.

### On-disk layout

```
$RUNECTL_HOME/runs/<run_id>/
    run.json        # manifest, rewritten wholesale when the run finishes
    trace.jsonl     # the event stream
    artifacts/      # <sha256>.txt for every spilled value
    cassette.jsonl  # provider request/response pairs (only with --record)
$RUNECTL_HOME/index.db
```

`run_id` is `YYYYMMDD-HHMMSS-<6 hex chars>` — lexicographically sortable, so
`ls runs/` is chronological.

### The envelope

Every line of `trace.jsonl` is one of these:

```json
{"v":1,"run_id":"20260907-220636-9221b7","seq":3,"ts":1788818796.911299,"type":"triage.result","data":{...}}
```

| Field | Meaning |
|---|---|
| `v` | Envelope version. Currently always `1`. |
| `run_id` | The owning run. |
| `seq` | Monotonic per run, starting at 1. **This is the replay ordering key**, and what `provenance_seq` points at. |
| `ts` | Unix timestamp, float seconds. |
| `type` | One of the types below. Closed set. |
| `data` | The typed payload for that type. |

A payload is a pydantic model, so a malformed event is a construction-time error in the
emitting code — it cannot slip into the trace as a loose dict. `TraceReader` reads back
with `extra="ignore"` rather than `"forbid"` (fixed 2026-09-10), so a run recorded before
an event type dropped a field still reads to its actual end instead of silently
truncating the instant it hits the removed field.

### Spilled values

When any string in a payload exceeds 8 KB, the writer replaces it with:

```json
{"$artifact": "9f86d081...b0f00a08", "bytes": 41207}
```

and writes the full bytes to `artifacts/9f86d081...b0f00a08.txt`. Identical content is
written once.

### Redaction

Before an event is written, every string in it is scanned for these patterns and replaced
with `[REDACTED]`:

- `sk-ant-…`, `sk-proj-…`, `sk-…` (20+ chars)
- `AIza…` (20+ chars)
- `Authorization: Bearer …`

Deliberately over-eager: over-redacting a false positive costs nothing, under-redacting a
real key costs a lot. This is the last line of defense — keys are already never written
into run config or logged.

### Event types

A closed set for V1 — see `CONTRIBUTING.md`'s "Adding an event type" for the process to
add one. Twenty payload types are defined; `trace/events.py`'s `_ALL_PAYLOADS` tuple is
the authoritative, always-current count. `flag.reviewed` is defined but no longer emitted
(kept read-only so the ~19 runs recorded before 2026-09-10 that contain it still read —
see D11/D15's 2026-09-10 amendments).

| Type | Payload fields |
|---|---|
| `run.started` | `challenge_name`, `category`, `model`, `provider`, `approval_policy`, `max_steps`, `network`, `thinking_level`, `thinking_clamped_from` |
| `challenge.loaded` | `name`, `category`, `description_chars`, `file_count`, `flag_format` |
| `triage.result` | `category`, `commands`, `findings` |
| `llm.request` | `step`, `model`, `provider`, `message_count`, `cached_prefix` |
| `llm.response` | `step`, `model`, `provider`, `stop_reason`, `text_chars`, `tool_call`, `input_tokens`, `output_tokens`, `cost_usd` |
| `llm.thinking` | `step`, `text`, `level`, `truncated` — added 2026-09-09 (D20). A separate event from `llm.response` so a long reasoning block doesn't bloat every response line; `text` is capped at 8,000 chars by the loop itself rather than the writer's generic spill path, because the spill path's `{"$artifact": ...}` replacement wouldn't re-validate against this event's plain-`str` field. |
| `tool.call` | `step`, `tool`, `arguments` |
| `tool.result` | `step`, `tool`, `ok`, `kind` (`output`/`error`/`blocked`), `stdout`, `stderr`, `exit_code`, `duration_s`, `truncated` |
| `progress.scored` | `step`, `family`, `fingerprint`, `delta`, `signal` — emitted after every executed tool call; a repeated fingerprint scores 0 regardless of exit code (D16: progress means new information). |
| `budget.blocked` | `step`, `family`, `reason` — a command rejected *before* it ran, so it costs no sandbox time. |
| `strategy.shift` | `step`, `reason`, `evidence_summary` — forced after a no-progress threshold. |
| `flag.candidate` | `step`, `flag`, `how_found`, `provenance_seq` — `provenance_seq` points at the `seq` of the `tool.result` where the flag was actually observed (D15 §1); the agent cites it via the `[observation seq=N]` header every tool result carries. |
| `flag.reviewed` *(legacy, no longer emitted)* | `step`, `flag`, `sound`, `reason` — the disconfirmation pass's verdict (D15 §2, removed 2026-09-10: `_apply_policy` never read it, so it cost a provider call per candidate and moved no decision). |
| `flag.rederived` | `step`, `source_seq`, `command`, `matched`, `stdout`, `stderr`, `exit_code`, `duration_s`, `truncated`, `errored` — added 2026-09-10 because D15 mechanism 2 calls `Sandbox.exec` directly rather than through the dispatcher, so a re-derivation command executed inside the container and left no trace record of having done so. Without it, `ReplaySandbox`'s positional exec queue desynchronized after a gated finalize, and a replay would silently end `candidate` instead of `solved`. `errored: true` means `exec` raised rather than returned — still recorded, since an unrecorded failure desyncs the queue the same way a success would. |
| `flag.decision` | `step`, `flag`, `decision` (`finalized`/`pending`/`rejected`), `reason` — a run can carry two decisions for the same flag (`pending` from the judge, then `finalized` from `runectl flag approve`); the last one wins. |
| `cost.updated` | `step`, `provider`, `model`, `input_tokens`, `output_tokens`, `cost_usd`, `cumulative_cost_usd` |
| `budget.exhausted` | `step`, `limit_usd`, `spent_usd` — D19's hard spend ceiling; a normal outcome (exit 3), not an error. |
| `error` | `step`, `kind`, `message`, `recoverable` |
| `run.finished` | `outcome` (`solved`/`candidate`/`exhausted`/`error`), `flag`, `steps_used`, `progress_steps`, `blocked_steps`, `cost_usd`, `duration_s`, `exit_code` — always the last event. |

### `run.json`

A small manifest, written when the run opens and rewritten wholesale when it finishes:

```json
{
  "run_id": "20260907-220636-9221b7",
  "challenge_name": "easy-01",
  "category": "misc",
  "model": "claude-sonnet-5",
  "provider": "anthropic",
  "config_snapshot": {
    "challenge": {"name": "easy-01", "category": "misc", "description": "...", "files": [], "flag_format": null},
    "approval_policy": "gated"
  },
  "started_at": 1788818796.910582,
  "finished_at": 1788818796.912478,
  "outcome": "solved",
  "exit_code": 0,
  "flag": "flag{sk3l3t0n_w4lk}",
  "cost_usd": 0.00141,
  "steps_used": 2,
  "progress_steps": 1,
  "blocked_steps": 0
}
```

`progress_steps` / `steps_used` is the D16 progress ratio; `blocked_steps` (steps a
budget rejected) is tracked separately rather than folded into it.

`config_snapshot.challenge` is what makes a run replayable — `runectl replay`
reconstructs the `Challenge` from it. A replay's snapshot instead carries
`{"replay_of": "<run id>"}`.

### `cassette.jsonl`

Written only with `--record`. One line per provider call:

```json
{"request_hash": "<sha256 of system+messages+tool names+max_tokens+thinking config>", "response": {...}}
```

`ReplayProvider` loads these into a hash → list map and serves them in order per hash.
A request whose hash isn't in the cassette raises — which is the point: it means the loop
would have asked the model something different, so the replay is no longer faithful.

### `index.db`

A derived SQLite index for cross-run questions. Two tables:

```sql
runs(run_id PK, challenge_name, category, model, provider,
     outcome, exit_code, cost_usd, steps_used, started_at, finished_at)
events_summary(run_id, type, count, PRIMARY KEY (run_id, type))
```

`runectl index rebuild` drops both and regenerates them from `runs/`. It is never
authoritative — deleting it loses nothing.

### Reading a trace programmatically

```python
from runectl.trace.reader import load_run
from runectl.trace.events import ToolCall, RunFinished

manifest, events = load_run("20260907-220636-9221b7")
for event in events:
    payload = event.payload()          # validated back into its typed model
    if isinstance(payload, ToolCall):
        print(event.seq, payload.tool, payload.arguments)
    elif isinstance(payload, RunFinished):
        print(payload.outcome, payload.cost_usd)
```

`load_run` takes an optional `store=Store(...)` if you're reading from somewhere other
than the default home. The reader is lazy and stops at the first unparseable line.

---

## Categories

A category is a **data file**, never a code change (D9). Dropping
`src/runectl/categories/<name>.toml` into place makes `--category <name>` work. All
eight get the same machinery and the same depth — there is no "tune web first, everything
else later" order (D14).

**The standard eight:** `pwn`, `web`, `crypto`, `forensics`, `rev`, `misc`, `osint`,
`network` — all shipped. `pwn`, `rev`, `forensics`, `osint` and `network` landed in M7
(2026-09-09) at equal depth with the original three. A `--category` with no TOML exits 6
with a message listing what *is* available.

### File format

```toml
brief = "One or two lines: how this category should be approached."

playbook = """
Longer prose the model actually reads. A decision order, the common traps, and
the specific things that are and aren't progress in this category.
"""

required_tools = ["curl", "ffuf", "gobuster", "sqlmap"]

signal_low  = ["HTTP/1\\.[01] 404", "Not Found", "Forbidden"]
signal_high = ["flag\\{", "FLAG\\{", "token", "admin=true"]

step_limit = 80
network = "bridge"

[tactic_families]
dirfuzz = "ffuf|gobuster|wfuzz|dirb"
sqli    = "sqlmap|UNION SELECT|OR 1=1|SLEEP\\("
lfi     = "\\.\\./|php://filter|/etc/passwd"

[budgets]
per_hypothesis = 2
per_family = 4
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `brief` | string | ✓ | Short execution brief. Goes into the system prompt right after the base rules. |
| `playbook` | string | ✓ | The category's real prompt content. Also part of the cacheable system prefix. |
| `step_limit` | int | ✓ | Default max steps. Overridable per run with `--max-steps`. |
| `network` | `"none"` or `"bridge"` | — (default `bridge`) | Default container network mode. Overridable with `--network`. |
| `required_tools` | list[string] | — | Tools this category expects in the arena image. Documentation for now. |
| `tactic_families` | table of name → regex | — | Semantic classification of a command into a family. A command matching none of them lands in `other`. |
| `signal_low` | list[regex] | — | Output patterns that are explicitly *not* progress. |
| `signal_high` | list[regex] | — | Output patterns worth pursuing. |
| `budgets.per_hypothesis` | int | — (default 2) | No-progress steps allowed per hypothesis before a block. |
| `budgets.per_family` | int | — (default 4) | No-progress steps allowed per tactic family. |
| `budgets.no_progress_shift` | int | — (default 3) | No-progress steps before a forced strategy shift (D8). |
| `budgets.consecutive_error_shift` | int | — (default 3) | Consecutive failing steps before a forced strategy shift. |

The schema is `extra="forbid"` — a typo'd key is a load error, not a silently ignored
line. `name` is supplied by the loader from the filename; don't put it in the file.
Regexes are Python `re` syntax; TOML basic strings need backslashes doubled, or prefer
`'single quotes'` (TOML literal strings) to write them raw.

### How the progress fields are used

`tactic_families`, `signal_low`, `signal_high`, and `budgets` are read on every executed
step (`progress/`): the command is classified into a family, its output is normalized and
fingerprinted, the signal patterns score it, and the budgets decide whether the next call
on the same idea runs at all. A blocked call is rejected *before* execution — it costs no
sandbox time and no further tokens on a dead hypothesis. Two consequences worth knowing
when writing a category: a `signal_low` pattern that is too broad makes real findings
score as noise, spending the family budget faster and forcing an early strategy shift; a
`tactic_families` regex that matches nothing leaves every command in `other`, where they
share one budget and the per-family mechanism effectively switches off.

### Step limits are unmeasured

Every `step_limit` currently shipped is a **starting value carried forward from the
predecessor tool's prompts, explicitly marked unmeasured.** They are a backstop, not a
target. The metric that matters is the *progress ratio* — `progress_steps / steps_used`,
with blocked steps tracked separately (D16). A run isn't failing because it took many
steps; it's failing because it took steps that produced no new signal. `runectl bench`
(M8) is what tunes these numbers, and as real data comes in they come **down**, not up.

| Category | step_limit | network |
|---|---|---|
| pwn | 120 | bridge |
| rev | 100 | bridge |
| web | 80 | bridge |
| crypto | 80 | none |
| forensics | 70 | bridge |
| misc | 60 | none |
| osint | 50 | bridge |
| network | 60 | bridge |

The five M7 categories default to `network = "bridge"` (2026-09-09, D14): remote-target
pwn, live osint lookups, and network challenges that hand over a host all want egress by
default, and `--network none` is one flag away for offline work. `crypto` and `misc` stay
`none`.

### Writing a good playbook

1. **Give a decision order, not a tool list.** "Recon headers and robots.txt before
   fuzzing; test injection on endpoints you actually found before spraying parameters" is
   useful. "Use ffuf, sqlmap, and curl" is not.
2. **Name what is not progress.** Web's playbook says repeated 404/403 bodies are the
   single most common way the category burns steps for nothing.
3. **Encode the specific known breaks.** Crypto's playbook walks RSA down a concrete
   order — small modulus, small `e`, Hastad broadcast, shared factors, Wiener.
4. **Keep it stable.** The whole system prompt is a cacheable static prefix; churn costs
   real money across a run.

### Adding a category

1. Write `src/runectl/categories/<name>.toml`.
2. Add whatever tools it needs to `arena/Dockerfile` and rebuild the arena image.
3. If the category needs deterministic pre-LLM triage commands beyond `ls -la` and
   `file *`, add them to `_CATEGORY_COMMANDS` in `loop/triage.py`, keyed **only** by
   category name — never by anything about a specific challenge (D10's no-answer-keys
   rule; a test enforces it).
4. `uv run pytest tests/unit/test_categories.py` — it validates every shipped TOML.

That's it. No registration, no code change.

---

## Design decisions (D1–D20)

Everything below is **locked for V1**: build to it, don't re-litigate it mid-build.
Several entries carry dated amendments rather than rewrites — read the whole entry, not
just its opening line.

### D1 — Language / runtime: Python 3.12

Locked. The CTF tooling ecosystem is Python (pwntools, angr, Crypto, scapy); both
provider SDKs are first-class; `src/` layout, `mypy --strict` in CI, every dependency
pinned via `uv.lock`. Runtime deps: `typer`, `pydantic>=2`, `docker`, `anthropic`,
`openai`, `google-genai`, `keyring`, `textual` (added 2026-09-09 for `runectl tui`).
`rich` was briefly considered as a declared dependency the same day, then correctly
dropped again — nothing imports it; Textual pulls it in transitively, which isn't a
reason to declare it directly. Forecloses hand-rolling what `uv` already does, and
forecloses an untyped `src/`.

### D2 — Sandbox: Docker, one container per run, behind a `Sandbox` protocol

Locked. Docker-per-challenge is the mechanism with evidence behind it (it worked at
competitions), and the insurance against being wrong is the interface, not the mechanism.
One image, `runectl/arena:kali`, built from `arena/Dockerfile`; runs gate on the image
existing and fail with a clear message, never a silent build (D17). Posture:
`mem_limit=2g`, `cpus=2`, `no-new-privileges`, non-root, never `--privileged`, bridge
network by default with `--network=none` available per run. Three implementations:
`DockerSandbox` (real), `StubSandbox` (in-process fake, no daemon), `ReplaySandbox`
(serves recorded outputs). Every command mirrors to `/ctf/.agent_live.log` so a human can
attach mid-run; `run_gdb` is batch-only, no exceptions. **Amended 2026-09-07:** the arena
is pinned to `linux/amd64` on both build and run regardless of host architecture (CTF
binaries are overwhelmingly x86-64; an unpinned arm64 build can't execute them, and the
failure looks like a broken challenge, not a broken sandbox); containers are named
`runectl-<run_id>` so a human can `docker exec -it` and take over mid-run, a workflow used
at real competitions. **Amended 2026-09-09:** `read_file` removed from the protocol and
all three implementations — written in at M0, never called once; `put_inputs` gets files
in, `exec`'s stdout is how the loop reads anything back out. An interface member with no
caller is three bodies to keep correct for nothing.

### D3 — Trace storage: append-only JSONL as the source of truth, SQLite as a rebuildable index

Locked. Files win on debuggability, greppability, zero infra, and surviving a restart
because they're files; a datastore wins on cross-run questions. Both, by making files
authoritative and the database derived — see "The trace format" above for the full
envelope, event types, and on-disk layout. **Amended 2026-09-09:** `llm.thinking` added
(D20) as its own event rather than a field on `llm.response`, so a long reasoning block
doesn't bloat every response line; `evidence.added` removed — declared at M0 for an
evidence store never built, never emitted, and kept as a schema entry only documented an
intention, not a behavior; `artifact_ref`/`provenance_artifact` removed from `tool.result`
and `flag.candidate` — both superseded before ever being set by the writer's in-place
spill (`{"$artifact": ...}` replaces the oversized string directly, rather than living in
a sibling field). **Amended 2026-09-10:** `flag.rederived` added — without it, D15's
sandbox re-derivation call left no trace record of having executed, which silently
desynchronized `ReplaySandbox`'s positional exec queue and made a replay of a gated
finalize end as `candidate` instead of `solved` while `replay --check`'s sequence-only
comparison still reported OK. `replay --check` now asserts the outcome too;
`tests/integration/test_replay_fidelity.py` is the regression gate. Also fixed the same
day: `TraceReader` was validating with `extra="forbid"`, so any run recorded before a
field was removed from a payload silently truncated the instant it hit that field — a
SIGKILL-shaped failure mode for an intentional schema edit. Changed to `extra="ignore"`.

### D4 — Interface contract: CLI is the API; stdout is NDJSON; humans get stderr

Locked. `--output=jsonl` (default off a TTY) streams trace events to stdout, one per
line, ending with the run id; `--output=human` (default on a TTY) renders the same stream
to stderr. The human renderer is a pure function of the event stream — it may never read
run state directly. Every flag has a non-interactive form; there is no prompt that can
block a run. Exit codes: `0` solved, `2` candidate awaiting approval, `3` exhausted,
`4` sandbox/infra failure, `5` provider failure after retries, `6` usage/config error.
Forecloses any command that can hang a script waiting on a human.

### D5 — Provider layer: BYO keys, multi-provider with no default, non-streaming, retried

Locked. Anthropic, OpenAI, and Google all ship with no "primary" — the product promise is
bring whatever model you want, because different models are better at different
categories. `--model` is always required; nothing is inferred. Key precedence:
`--api-key` > env var > OS keyring > `~/.config/runectl/keys.json` (mode 0600). Keys are
never written to project config, never logged, redacted from the trace by a writer-level
filter. One `Provider` protocol, one canonical tool schema adapted per provider at the
boundary; provider and capability come from an explicit model registry, never a string
prefix like `claude-`. Non-streaming in the core (no live UI to feed; makes retries,
caching, cassettes, and replay determinism straightforward — live watchability comes from
per-step trace events instead). Retry with exponential backoff + jitter on
429/5xx/timeouts, 4 attempts. Every LLM call — main loop and utility calls alike — goes
through this one path; no hardcoded cheap model, no silent no-op when a provider is
absent. **Amended 2026-09-09:** `supports_tools` dropped from the registry (`True` on
every row, read by nothing — a model that can't call tools can't run this agent at all);
`cache_write_multiplier`/`cache_read_multiplier` moved from `ModelInfo` into module
constants in `providers/cost.py` (identical on all seven rows). **Amended 2026-09-09,**
alongside the D13 TUI amendment: `runectl config`'s stored per-provider preferences never
relax this — they prefill flags in `runectl models list` and the TUI's launcher, they are
read only where the user explicitly set them, and the TUI always composes and shows an
explicit `--model` before launching.

### D6 — Run state: one explicit object, no mixins

Locked. The predecessor tool's six mixins sharing ~30 implicit `self` attributes was the
coupling failure. One `RunState` dataclass owned by the loop, passed explicitly to
collaborators (`ProgressTracker`, `FlagJudge`, `ContextBuilder`, `ToolDispatcher`) that
are plain objects with constructor dependencies. No collaborator mutates `RunState`
directly; they return values the loop applies. This is what makes the loop
unit-testable.

### D7 — Tool surface: the five, structured results, no growth without a rule

Locked: `run_command`, `write_file`, `run_gdb`, `search_flag`, `submit_flag`. Defined
once as Python objects; OpenAI/Anthropic/Google schemas are derived. Results are
structured — `ToolResult(ok, kind, stdout, stderr, exit_code, duration_s, truncated,
shell_command)` — never `[error]`-prefixed strings. Exactly one tool call executes per
step; extras get a structured `blocked` result and a nudge. A sixth tool is added only if
a category demonstrably cannot be served by shell, argued here first. **Amended
2026-09-09:** `artifact_ref` and `meta` dropped (never set by any handler); `shell_command`
earned its place instead — D15's re-derivation has to re-run exactly what ran, which the
tool arguments alone can't reconstruct for `run_gdb`/`search_flag`.

### D8 — Progress machinery: category-parameterized from day one

Locked, and the crown jewel carried forward from the predecessor's one well-instrumented
category. Anti-loop isn't "web plus seven strings of prompt text" — every category gets
the same four mechanisms, tuned by data: **tactic families** (semantic classification of
a command — `dirfuzz`, `sqli`, `disasm`, `crack`, …); **output fingerprinting**
(normalize volatile parts — dates, PIDs, whitespace — then hash; a repeat fingerprint is
not progress); **signal scoring** (per-category low-signal vs. high-value output
patterns, producing a numeric delta); **budgets** (per-hypothesis and per-family
no-progress counters that *block* a command once exceeded, plus a forced strategy shift
after N no-progress steps or M consecutive errors). All thresholds live in the category
TOML, never in code. Shared defaults apply equally to all eight categories on day one —
no earner-first tuning (D14).

### D9 — Categories as data: one TOML per category, loaded at runtime

Locked — see "Categories" above for the file format and fields. Adding a category is a
data file, never a code change. Step limits are carried as starting values explicitly
marked unmeasured; `runectl bench` is what tunes them.

### D10 — Pre-LLM work: deterministic triage only, and it never sees the challenge identity

Locked, and the structural guarantee against hardcoded answer keys. Exactly one code path
runs before the first paid call: `triage(sandbox, category) -> TriageResult`, a fixed
category-parameterized command set. `triage()` does not receive the challenge name,
filenames, or description, so a fast-path keyed to a specific file can't be written — there
is nowhere to put it. No plugin hook, no pre-LLM solver of any kind. A test asserts the
triage command set is identical across two runs differing only in name and files.

### D11 — Flag policy: candidate by default, sandbox-gated auto, explicit off

Locked. `--approval` takes three values: `gated` (**default**) auto-finalizes a candidate
only if it matches `--flag-format` (when supplied) and passes plausibility filtering and
re-derivation in the sandbox (D15) — otherwise the run exits **2** with the candidate in
the trace for `runectl flag approve`; `strict` never auto-finalizes; `auto` finalizes the
top plausible candidate at the user's own risk, for live competition speed.

This decision has the most amendment history of any in this file, because it's the one
most exposed to real bench evidence:

- **Amended 2026-09-08 — corroboration (≥2 independent sightings) dropped from the
  auto-finalize bar.** The first live bench held four *correct* flags for want of a
  second sighting and finalized the one wrong flag in the suite; a clean solve produces
  its answer once, so the rule taxed exactly the runs it should have waved through, while
  a stubborn agent — told what the checker wanted — could re-run its own script with
  different noise until the fingerprints differed. Replaced (same day) by a disconfirmation
  review: one cheap call to frame a reason the flag is *wrong*.
- **Amended 2026-09-08, later the same day — the disconfirmation review made advisory,
  not gating.** It still ran on every candidate and was recorded and printed; it no
  longer held anything. `gated` finalizes on the deterministic set alone: plausibility,
  provenance/anti-echo, decoy markers, `--flag-format`, and sandbox re-derivation.
- **Amended 2026-09-10 — the disconfirmation review removed outright, not merely
  advisory.** Its own record over nine live reviews: six correct clears, **two wrong
  flags cleared**, **one correct flag held** (it couldn't see the challenge's attached
  files), zero caught. An advisory check that never changes a decision isn't a check,
  it's a line item — `_apply_policy` never read its verdict, so every review spent
  ≈$0.002 and a round trip on a call that could not move any outcome, while printing a
  "review sound/doubtful" line that read as a judgment it never was. `flags/review.py`
  and the `Reviewer` protocol are deleted; `FlagReviewed`/`flag.reviewed` stay defined,
  read-only, so runs recorded before this date still read.

What survives, after both corroboration and the review were tried and removed: the
sandbox. Provenance says the string came from a tool's output, not the agent's own
command; re-derivation says the tool produces it again. Those are checks a model cannot
talk its way past — `_GATING_CHECKS` in `flags/judge.py` names the set explicitly so it
cannot drift by accident.

*Implementation note (M6):* when no `--flag-format` is supplied, the format condition
does not apply rather than failing — the alternative would silently behave as `strict`
for every challenge whose format the user didn't type out, a default nobody chose.
Plausibility and decoy detection apply under **all three** policies — `auto` buys speed
on re-derivation, not the right to submit an invented or planted string.

### D12 — Context management: summarize, don't truncate; one limit

Locked. One configurable `context.tool_output_limit` (default 6000 chars), no second dead
constant. Over the limit, output is written to `artifacts/` and an extractive summarizer
(deterministic first — head + tail + regex-salient lines; LLM summarization only if still
over) produces what enters context. Identical tool output deduplicates to a back-reference
by digest. History compaction runs on a token-budget trigger, through the standard
provider path (D5).

### D13 — CLI-only, permanently: no server, no browser UI, ever — an in-terminal TUI is in bounds

Locked. There is no GUI in this product's future — the CLI is the whole product,
permanently. This forecloses a `serve` command, an SSE tailer, and a separate `ui/`
package as things to ever build. The event-stream/renderer split (D4) is kept anyway, for
testability and the stdout-NDJSON/stderr-human duality, not as a seam for a future front
end. Only `cli/` renders; everything else emits structured events and never prints.

**Amended 2026-09-09 — a local, in-terminal TUI is in bounds; the lock on a server or
browser UI is unchanged.** A one-line-per-event stderr stream can't serve watching
multiple runs, seeing thinking live, and approving flags interactively — that needs
something stateful and redrawing, which is a TUI, not a GUI. What's now allowed:
`runectl tui`, at `src/runectl/cli/tui/` (Textual, D1) — `cli/` code under every existing
rule, the only thing permitted to hold a live redraw loop. What stays forbidden,
untouched by this amendment: `runectl serve`, any HTTP server or network listener, any
SSE tailer, any browser-rendered UI, any top-level `ui/` package — "in-terminal" is
load-bearing, nothing here opens a port. Why this doesn't reopen the failure D13 was
written against: the predecessor's structural failure was the agent loop calling
`socketio.emit(...)` from inside itself, constructed directly by a Flask route — no
boundary between agent and UI. The TUI never touches the loop process: it spawns
`runectl run --output jsonl` as a **subprocess** and reads the same NDJSON stream any
other driving agent reads. The loop stays synchronous, single-process, exactly as
testable as before; multiple runs in the TUI are multiple subprocesses, each with its own
container — no threading or async was added to `loop/runner.py` (D2 unchanged: one
container per run). D4's non-interactivity guarantee is unchanged for `runectl run`
itself; interactivity moved *outside* the run; it was not introduced inside it.

### D14 — Category depth: all eight equal, no earner-first order

Locked, rewritten from an earlier draft that fixed a deepening order. No category is
tuned ahead of the others. Shared defaults (D8) and full category data (D9) for all eight
ship together; per-category improvement beyond the shared defaults is a later, uniform
effort applied to every category at once. Web's old heuristics remain the only
evidence-backed starting point, so they inform the *shape* of the shared defaults every
category gets — a statement about where the starting numbers came from, not a license to
keep tuning web first. **Built 2026-09-09 (M7):** the remaining five categories
(`pwn`, `rev`, `forensics`, `osint`, `network`) shipped as data at equal depth. Three
coupled changes: the arena grew the toolset the new playbooks name (this changes the
Dockerfile fingerprint, D17 — an existing arena warns as stale until rebuilt, expected);
the five default to `network = "bridge"`, not `none` (remote-target pwn, live osint, and
network challenges that hand you a host all want egress by default; `crypto`/`misc` stay
`none`); the bench grew from 5 to 10 cases, one per new category, with `pwn`/`osint`
scored outside the V1 gate for structural reasons (pwn's local flag file is directly
readable by the agent's shell; osint's answer lives in rotted live-internet state).

### D15 — False-flag defense: a first-class subsystem, not a side effect of plausibility filtering

Locked. False flags are the #1 product risk — a wrong flag scores worse than no flag.
Mechanisms, landing in M6:

1. **Provenance is mandatory.** `submit_flag(flag, how_found, provenance)` — the judge
   re-reads the cited trace `seq` and confirms the flag literally appears there. This
   kills invented strings structurally, not probabilistically. **Refined 2026-09-08:**
   provenance matches the flag's **payload**, not always the whole string — a challenge
   whose flag is a computed number inside a wrapper the challenge itself prints (e.g.
   `csictf{answer}`) can only have its wrapper reach output by the agent typing it in,
   which the anti-echo rule (below) rejects; the wrapper is published, so nobody earns it.
   A payload sighting counts only if the wrapper's prefix is attested by the description
   or `--flag-format`, and the payload is at least 8 characters (so a short value in a
   wall of output is coincidence, not evidence). Anti-echo still runs on the payload
   itself.
2. **Verification double-check.** The judge re-derives a cleared candidate
   deterministically first: re-run the cited command in the sandbox, confirm the same
   string reappears. Under `gated` (D11), only a re-derived candidate is eligible.
3. **Decoy detection.** Reject a candidate whose source carries decoy markers: a
   path/file named `decoy`/`fake`/`honey`, nearby taunt text, or a token that appeared
   verbatim in the pasted challenge description (a planted lure).
4. **Independent corroboration** — counted and reported (D11: no longer gating as of
   2026-09-08). What the bench showed: "two observations with different fingerprints" is
   satisfiable by an agent that varies its own output; any string an agent can vary
   defeats fingerprint-based corroboration.
5. **No-flag-is-success**, stated as a base rule the agent sees: no flag with solid,
   cited evidence is a correct outcome; an unsupported guess is a failure (exit 3), the
   explicit counterweight to "act, don't ask."

*The gate's scope.* "2 of 5 solved, 0 false flags" (the V1 gate) is unchanged, but a
suite case can sit **outside** it by setting `"gate": false` with a `gate_note` in its
`expected.json` — loud, not quiet: the case still runs and still counts in the headline
solve rate, it just doesn't decide pass/fail. `load_suite` refuses an exclusion with no
stated reason. One bench case (`quick-math`) is excluded this way: its run performs the
Hastad broadcast attack correctly and submits the recovered value one transformation
short of the flag, so every mechanism here agrees with it, correctly — it's a capability
failure wearing a false flag's clothes, not something this subsystem could ever catch.

### D16 — Budgets measure step *waste*, not step count

Locked. A run is not failing because it took many steps; it is failing because it took
steps that produced no new signal. Step limits (category TOML, starting values carried
from the predecessor tool) are a backstop, not the primary metric. The primary metric is
the **progress ratio** — `progress_steps / steps_used`, with `blocked_steps` tracked
separately in `run.json`. As real run data comes in, limits come down; they do not go up.

### D17 — First-run arena setup: check always, remediate explicitly, never prompt inside a run

Added 2026-09-07, filling a bad first-run experience without weakening D2 or D4.
Presence and provenance are checked before every run, in a preflight before the run
directory is created and before any paid call — a missing image exits **4** with the
full list of remedies; an unreachable daemon is a distinct message. `build` stamps a
Dockerfile-fingerprint label so a stale image (older than the current checkout) is
detected and warns rather than silently running old tooling; an image with no label is
"unknown provenance," not "stale." Three non-interactive remedies:
`arena ensure --build`, `--from-file PATH` (load a `docker save` tarball — the
offline/air-gapped path), `--from-registry REF`. `arena ensure` with no flags on a TTY is
the one interactive surface in the product — it asks which route you want; with no TTY it
prints the remedies and exits 4 rather than prompting. `runectl run` itself still never
prompts and never silently builds.

### D18 — Prompt caching is real, and the ledger prices it

Added 2026-09-07, correcting a gap where D5 promised caching but no adapter ever sent a
cache breakpoint. The breakpoint goes at the end of the message list, not the system
prompt — top-level `cache_control: {"type": "ephemeral"}` caches the last cacheable
block, which in an agent loop is the growing conversation; caching only the system prefix
would mostly not fire, since the minimum cacheable prefix (512–4096 tokens depending on
model) is bigger than a category playbook alone. Cached tokens bill differently, so
`Usage`/`CostLedger` model `cache_read_tokens` (0.10x `price_in`) and
`cache_write_tokens` (1.25x) separately from uncached input — folding them together would
overstate a cached loop's cost by up to 10x. Registry pricing was corrected the same day
against a live `models.list()`: Opus 5 and Sonnet 5 had been recorded at roughly 3x their
actual price with a quarter of their actual context window. OpenAI and Google rows remain
unverified estimates.

### D19 — Every run has a hard spend ceiling

Added 2026-09-07. A step limit bounds actions, not dollars, and an agent loop's failure
mode is *many steps* — so `--max-cost` bounds dollars directly. Default **$0.50** per run
(`--max-cost 0` disables it). The check runs immediately after the ledger updates and
before the next paid call, so a run stops one call early rather than one call late.
Crossing the ceiling is a normal outcome (`budget.exhausted`, exit 3), not an error. This
is a ceiling, not an estimate — it can't prevent one very expensive call from
overshooting, only prevent the next one.

### D20 — Extended thinking: opt-in, explicit, and always recorded

Added 2026-09-09. `--thinking <off|low|medium|high|xhigh|max>` on `run` and `bench run`;
the model's reasoning becomes a first-class trace event (`llm.thinking`) instead of being
discarded. Default is `off` — thinking is billed as output tokens against the same D19
ceiling, so a run that didn't ask for it doesn't pay for it; `off` is also the safe
default for an unattended `bench run` suite. The *resolved* level is always written to
`run.started` and `run.json`, never only implied by a flag the user might not remember
passing. One shared six-value scale maps per provider via `ModelInfo.thinking_style`
(Anthropic: adaptive thinking + `output_config.effort`; OpenAI: `reasoning.effort`;
Google: a thinking-token budget); where a provider or model can't represent a requested
level, `runectl` clamps and **records the clamp in the trace** — the same "loud, not
silent" posture as D11's gating and D15's decoy detection. Thinking blocks that a
provider returns as structured content must round-trip unchanged on the next request, per
that provider's own API contract. `--record`'s cassette hashing includes the resolved
thinking configuration, so a cassette recorded with thinking off is never served to a
replay requesting thinking on. Not a category concern — thinking level is a per-run,
per-model choice like `--model` itself.

### License

Decided 2026-09-08: **Apache-2.0** (`LICENSE`, `NOTICE`). Permissive, with an explicit
patent grant MIT doesn't carry, which matters for a company building commercial products
next to this one; keeps an open-core layer available later, where copyleft would have
worked against the adoption the project is being open-sourced to get. The vendored
practice challenges under `bench/practice/` are **not** covered by it — third-party MIT
material, with the required text in `bench/THIRD_PARTY_LICENSES.md` and per-challenge
credit in each `PROVENANCE.md`.

---

## Seams (deliberately unfinished)

Each of these is shaped now so the milestone that fills it doesn't need an API change.

| Seam | Where | Fills in |
|---|---|---|
| Evidence store | Findings carry forward in the conversation only. The unemitted `evidence.added` schema entry was removed 2026-09-09 (D3 amendment) rather than left standing as a promise | later |

**M9 (done, 2026-09-09)** filled the human-render-polish seam and then grew past it — see
[`STATUS.md`](STATUS.md)'s Milestones section and `cli/tui/`'s module docstrings for what
actually landed (extended thinking, `config`/`models`/`runs`, and the TUI itself).

For exactly what is and isn't real today, see [`STATUS.md`](STATUS.md).

## Testing without a daemon or a key

`StubSandbox` (in-process fake filesystem plus a scripted command table) and
`ScriptedProvider` (a fixed list of completions) together let the entire loop run with no
container runtime and no API spend. That's not a testing convenience bolted on afterward —
it's the reason both types exist, and the great majority of the test suite runs on it.

`ReplaySandbox` + `ReplayProvider` are the same idea pointed at a *recorded* run, which
is what `runectl replay` uses.
