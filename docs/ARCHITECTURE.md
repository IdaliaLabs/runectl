# Architecture

The authority on *why* any of this is shaped the way it is is
[`DECISIONS.md`](../DECISIONS.md) (D1–D16), which is locked for V1. This document is the
map: what each module does, what happens during a run, and where the seams are that later
milestones fill in.

## Layout

```
src/runectl/
    config.py           paths + tunable defaults (all starting values, none tuned)
    errors.py           typed errors mapped 1:1 onto the D4 exit codes
    ids.py              sortable run ids: YYYYMMDD-HHMMSS-<6hex>

    cli/                the ONLY package allowed to print
        app.py          Typer root; wires subcommands; hosts `replay` and `index`
        run_cmd.py      `runectl run` — resolves everything, runs the loop, exits honestly
        trace_cmd.py    `runectl trace show`
        keys_cmd.py     `runectl keys set|list|rm`
        arena_cmd.py    `runectl arena build|status`
        flag_cmd.py     `runectl flag list` / `flag approve`
        bench_cmd.py    `runectl bench run`
        render.py       two pure event renderers: NDJSON→stdout, human→stderr

    trace/              the record
        events.py       the closed set of 16 typed payloads + the versioned envelope
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
                        corroboration -> flag format -> re-derivation -> review
        review.py       the disconfirmation pass — advisory, decides nothing
        plausibility.py decoys.py

    categories/         category data, loaded at runtime
        schema.py       what every category TOML validates against
        loader.py       TOML → Category
        web.toml crypto.toml misc.toml
```

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

## Anatomy of a run

What `runectl run` does, in order:

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
argument for it goes in `DECISIONS.md` first.

Results are always a structured `ToolResult` with `ok`, `kind` (`output` / `error` /
`blocked`), streams, exit code, duration and truncation — never an `[error]`-prefixed
string the model has to parse out of prose.

## Run state

One `RunState` dataclass, owned by the loop. Collaborators (`ToolDispatcher`,
`ContextBuilder`, the judge, the nudges) are plain objects with constructor dependencies
that take what they need and return values the loop applies. None of them mutate
`RunState`. That's what makes the loop unit-testable, and it is the direct structural
answer to the predecessor's six mixins sharing ~30 implicit attributes.

## Testing without a daemon or a key

`StubSandbox` (in-process fake filesystem plus a scripted command table) and
`ScriptedProvider` (a fixed list of completions) together let the entire loop run with no
container runtime and no API spend. That's not a testing convenience bolted on afterward —
it's the reason both types exist, and the whole 28-test suite runs on it.

`ReplaySandbox` + `ReplayProvider` are the same idea pointed at a *recorded* run, which
is what `runectl replay` uses.

## Seams (deliberately unfinished)

Each of these is shaped now so the milestone that fills it doesn't need an API change.

| Seam | Where | Fills in |
|---|---|---|
| Evidence store | `evidence.added` is defined in the trace schema but nothing emits it — findings carry forward in the conversation only | later |
| The other five categories | `pwn`, `rev`, `forensics`, `osint`, `network` TOMLs aren't written yet; the loader ships whatever is present | **M7** |
| Suite breadth | Five MIT-licensed challenges are vendored, but four of five are crypto — only `crypto`/`misc`/`web` ship as categories, and web needs a live service | **M7** rebalance |
| Human render polish | `render.py` is plain but correct — one line per event | **M9** |

For exactly what is and isn't real today, see [`STATUS.md`](STATUS.md).
