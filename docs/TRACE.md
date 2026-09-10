# The trace format

The trace is the run's complete record: everything else — the manifest, the SQLite index,
the human timeline, replay — is derived from it. It is what makes a solve checkable after
the fact and a failure diagnosable.

Design rules ([`DECISIONS.md`](../DECISIONS.md) D3):

- **Append-only JSONL is the source of truth.** No database is required for a run to
  exist, be read, or be replayed.
- **Flushed and `fsync`ed after every event.** A `SIGKILL` mid-run leaves a valid
  prefix, not a corrupt file.
- **Greppable.** Any single value over 8 KB is spilled to `artifacts/` and referenced by
  digest, so lines stay a readable size.
- **Secrets never land in it.** The writer redacts known key shapes before anything
  reaches disk.

## On-disk layout

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

## The envelope

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

A payload is a pydantic model with `extra="forbid"`, so a malformed event is a
construction-time error in the emitting code — it cannot slip into the trace as a loose
dict.

## Spilled values

When any string in a payload exceeds 8 KB, the writer replaces it with:

```json
{"$artifact": "9f86d081...b0f00a08", "bytes": 41207}
```

and writes the full bytes to `artifacts/9f86d081...b0f00a08.txt`. Identical content is
written once. `TraceReader.resolve_artifact(digest)` reads it back.

## Redaction

Before an event is written, every string in it is scanned for these patterns and replaced
with `[REDACTED]`:

- `sk-ant-…`, `sk-proj-…`, `sk-…` (20+ chars)
- `AIza…` (20+ chars)
- `Authorization: Bearer …`

Deliberately over-eager: over-redacting a false positive costs nothing, under-redacting a
real key costs a lot. This is the last line of defense — keys are already never written
into run config or logged.

## Event types

A closed set for V1 — see `CONTRIBUTING.md`'s "Adding an event type" for the four-step
process to add one. (This section's original count of sixteen is stale: `flag.reviewed`
shipped with M6, `budget.exhausted` with D19, `llm.thinking` with D20 (2026-09-09), and
`flag.rederived` with D3's 2026-09-10 amendment — twenty as of that last one.
`trace/events.py`'s `_ALL_PAYLOADS` tuple is the authoritative count.)

### `run.started`
The run's parameters as actually resolved.
`challenge_name`, `category`, `model`, `provider`, `approval_policy`, `max_steps`,
`network`, `thinking_level` (D20 — the *resolved* level, `"off"` if not requested),
`thinking_clamped_from` (D20 — set only when the requested level was clamped down for
the model; null otherwise)

### `challenge.loaded`
The challenge as accepted. Note it carries the description's *length*, not the description
itself.
`name`, `category`, `description_chars`, `file_count`, `flag_format`

### `triage.result`
The one deterministic pre-LLM pass. `commands` is the exact list run; `findings` maps each
command to its output.
`category`, `commands`, `findings`

### `llm.request`
Emitted before each provider call.
`step`, `model`, `provider`, `message_count`, `cached_prefix`

### `llm.response`
`step`, `model`, `provider`, `stop_reason`, `text_chars`, `tool_call` (name + arguments, or
null), `input_tokens`, `output_tokens`, `cost_usd`

### `llm.thinking`
Added 2026-09-09 (D20). The model's reasoning for one step, when `--thinking` was
requested and the provider actually returned readable thinking text — an empty result
(e.g. OpenAI's Chat Completions surface, which never returns reasoning content at all;
see `providers/openai.py`) emits nothing rather than an empty event. A separate event
from `llm.response` rather than a field on it, so a long thinking block doesn't bloat
every response line. `text` is capped at 8,000 characters by the loop itself
(`loop/runner.py`'s `_MAX_THINKING_CHARS`) rather than relying on the writer's generic
>8 KB artifact-spill path below — that path replaces an oversized string with an
`{"$artifact": ...}` dict, which would fail to re-validate against this event's
plain-`str` `text` field on read-back.
`step`, `text`, `level`, `truncated`

### `tool.call`
The single tool call being executed this step.
`step`, `tool`, `arguments`

### `tool.result`
The structured result. `kind` is `output`, `error`, or `blocked`.
`step`, `tool`, `ok`, `kind`, `stdout`, `stderr`, `exit_code`, `duration_s`, `truncated`,
`artifact_ref`

### `progress.scored`
Per-step progress signal, emitted after every executed tool call. `delta` is what the step
moved the progress score by; a repeated fingerprint scores 0 no matter what the command
exited with (D16 — progress means new information, not a zero exit code).
`step`, `family`, `fingerprint`, `delta`, `signal` (`none`/`low`/`high`)

### `budget.blocked`
A command rejected by a per-family or per-hypothesis budget, *before* it ran — a blocked
call costs no sandbox time and no further tokens on a dead idea.
`step`, `family`, `reason`

### `strategy.shift`
A forced change of approach after a no-progress threshold, with the evidence summary
injected into context.
`step`, `reason`, `evidence_summary`

### `evidence.added`
A durable finding worth carrying forward. **Defined but not emitted** — findings currently
carry forward in the conversation only.
`step`, `kind`, `summary`, `source_seq`

### `flag.candidate`
A `submit_flag` call. `provenance_seq` points at the `seq` of the `tool.result` where the
flag was actually observed — the anti-invention mechanism (D15 §1). The agent cites it
itself: every tool result reaches the model with an `[observation seq=N]` header, and the
judge re-reads and re-runs what that number names.
`step`, `flag`, `how_found`, `provenance_seq`, `provenance_artifact`

### `flag.reviewed`
The disconfirmation pass's verdict on one candidate (D15 §2) — its own event rather than a
line in `flag.decision`'s reason, because reading *why* a model doubted a flag is the
point of the pass. **Advisory since 2026-09-08**: this event is the whole output of the
pass, and a `sound: false` verdict no longer holds anything (D11).
`step`, `flag`, `sound`, `reason`

### `flag.rederived`
D15 mechanism 2 re-ran the cited command in the sandbox, and this is what came back.

Added 2026-09-10, and the reason is worth stating plainly: **without it the trace was
incomplete.** Re-derivation calls `Sandbox.exec` directly rather than going through the
dispatcher, so a command really executed inside the run's container and left no record of
having done so.

It also carries the full `ExecResult`, which is what makes a gated finalize replayable:
`ReplaySandbox` serves recorded exec results *positionally*, so an unrecorded exec used to
consume the next tool call's output and shift everything after it — a replay would issue
an identical tool-call sequence while quietly ending `candidate` instead of `solved`.

`errored: true` means `exec` raised rather than returned. It is still recorded, because an
unrecorded failed call desynchronizes the queue exactly as a successful one does; a replay
is served a plain failed result there instead of a re-raised exception.
`step`, `source_seq`, `command`, `matched`, `stdout`, `stderr`, `exit_code`,
`duration_s`, `truncated`, `errored`

### `flag.decision`
`finalized`, `pending`, or `rejected`, with the reason. A `rejected` decision is feedback —
the run continues and the model is told why. A `pending` one ends the run at exit code 2.
A run can carry two decisions for the same flag: `pending` from the judge, then
`finalized` appended later by `runectl flag approve`. The last one wins.
`step`, `flag`, `decision`, `reason`

### `cost.updated`
Emitted after every provider call, including utility calls.
`step`, `provider`, `model`, `input_tokens`, `output_tokens`, `cost_usd`,
`cumulative_cost_usd`

### `budget.exhausted`
D19's hard spend ceiling. A normal outcome, not an error — the run finishes as
`exhausted` and exits 3, same as running out of steps with no candidate.
`step`, `limit_usd`, `spent_usd`

### `error`
`step`, `kind`, `message`, `recoverable`

### `run.finished`
Always the last event of a complete run.
`outcome` (`solved`/`candidate`/`exhausted`/`error`), `flag`, `steps_used`,
`progress_steps`, `blocked_steps`, `cost_usd`, `duration_s`, `exit_code`

## `run.json`

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
budget rejected) is tracked separately rather than folded into it. Both come from the same
counters as the `run.finished` event.

`config_snapshot.challenge` is what makes a run replayable — `runectl replay` reconstructs
the `Challenge` from it. A replay's snapshot instead carries `{"replay_of": "<run id>"}`.

## `cassette.jsonl`

Written only with `--record`. One line per provider call:

```json
{"request_hash": "<sha256 of system+messages+tool names+max_tokens>", "response": {...}}
```

`ReplayProvider` loads these into a hash → list map and serves them in order per hash.
A request whose hash isn't in the cassette raises — which is the point: it means the loop
would have asked the model something different, so the replay is no longer faithful.

## `index.db`

A derived SQLite index for cross-run questions. Two tables:

```sql
runs(run_id PK, challenge_name, category, model, provider,
     outcome, exit_code, cost_usd, steps_used, started_at, finished_at)
events_summary(run_id, type, count, PRIMARY KEY (run_id, type))
```

`runectl index rebuild` drops both and regenerates them from `runs/`. It is never
authoritative — deleting it loses nothing.

## Reading a trace programmatically

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
