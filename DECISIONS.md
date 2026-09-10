# DECISIONS — runectl (locked)

Written 2026-09-05, updated 2026-09-05 (name, CLI-only lock, multi-provider/no-default, equal categories, D15/D16 — see "Corrections folded in below"). This closes the predecessor post-mortem's open-decisions list (`POSTMORTEM.md`) and `PLAN.md` "Finish the stack decision." Everything here is **locked for V1**: build to it, don't re-litigate it mid-build. Changing a decision means editing this file with a dated reason, not quietly diverging in code.

Product, restated in one paragraph so the decisions have something to serve:

> A local, BYO-API-key CLI named **`runectl`**. The user hands it a challenge — name, category, the prompt/description they were given, and any provided files — plus their own provider key (Anthropic, OpenAI, or Google, whichever model they want). It runs an autonomous agent loop inside a per-challenge Docker sandbox, works toward a flag, and writes a complete, replayable trace of everything it tried. It is built to be driven by another AI agent (machine-readable I/O, non-interactive, honest exit codes). **It is CLI-only, permanently — there is no server, no HTTP surface, no browser UI, ever** (an in-terminal TUI is allowed as of the 2026-09-09 amendment to D13 — see that section).

## Corrections folded in 2026-09-05

This file originally used the working name `idalia` and left the GUI as a later, planned consumer. Both are superseded:

- **Name:** the product and package are **`runectl`** (not `idalia`). Console script `runectl`, package `src/runectl/`, paths under `~/.local/share/runectl/`, image `runectl/arena:kali`.
- **CLI-only, permanently.** D13 below is rewritten: no GUI is planned, ever, not even later. The event-stream/renderer split stays only for testability and the NDJSON/human duality.
- **Multi-provider, no default.** D5 adds Google (Gemini) as a third first-class provider alongside Anthropic and OpenAI. There is no "primary" provider — `--model` is required and every provider is equally supported.
- **Equal categories.** D14 is rewritten from a fixed deepening order to: no earner-first tuning, all eight categories get the same machinery and depth on day one.
- **D15 and D16 are new**: the false-flag defense subsystem as a first-class, named set of mechanisms, and the step-waste framing for budgets.

---

## The decisions

### D1 — Language / runtime: **Python 3.12**

Locked. Reasons: the CTF tooling ecosystem is Python (pwntools, angr, Crypto, scapy, binwalk wrappers); both provider SDKs are first-class; the earned heuristics we're porting as *ideas* (tactic families, fingerprinting, flag plausibility) are cheapest to re-express in the language they were reasoned in. 3.12 not 3.13 for tooling compatibility breadth.

- Dependency manager: **uv**, with a committed `uv.lock`. Every dependency pinned (fixes `POSTMORTEM.md` §2 security hygiene).
- Layout: `src/` layout, package name `runectl`, console script `runectl`.
- Typed throughout; `mypy --strict` on `src/runectl/` in CI. This is the direct structural answer to "stringly-typed control flow."
- Runtime deps, complete V1 list: `typer`, `pydantic>=2`, `docker`, `anthropic`, `openai`, `google-genai`, `keyring`, `rich`. Category data uses stdlib `tomllib` — no YAML dep. Dev: `pytest`, `pytest-cov`, `mypy`, `ruff`.
- **Amended 2026-09-09:** add `textual` for the `runectl tui` command (D13 amendment). `rich` has been a declared-but-unimported dependency since M0 — the human renderer in `cli/render.py` is hand-rolled string formatting, not `rich`'s console API — and Textual is built on `rich`, so this is the point that dependency actually gets used. Nothing else about the runtime dependency list changes.
- **Amended 2026-09-09 (same day, correcting the line above):** `rich` is **removed** from the declared runtime deps, and `pytest-cov` from the dev group. The reasoning above was half right and half wrong. Right: nothing imports `rich`. Wrong: "Textual is built on `rich`, so this is the point that dependency actually gets used" — that makes `rich` a *transitive* dependency of `textual`, which is exactly the thing a direct declaration should not be used for. We declare what we import. `pytest-cov` goes for the plainer reason that no target, CI job, or contributor instruction has ever invoked it. Runtime list is now: `typer`, `pydantic>=2`, `docker`, `anthropic`, `openai`, `google-genai`, `keyring`, `textual`. Dev: `pytest`, `pytest-asyncio`, `mypy`, `ruff`, `types-docker`.

### D2 — Sandbox: **Docker, one container per run, behind a `Sandbox` protocol**

Locked. Docker-per-challenge is the mechanism we have evidence for (it worked at competitions), it is what a student laptop already has, and microVMs/gVisor buy isolation we don't need against challenge binaries we chose to run. The insurance against being wrong is the interface, not the mechanism.

- One image, `runectl/arena:kali`, built from `solver/arena/Dockerfile` — Kali base plus a pinned CTF toolset. Built once via `runectl arena build`; runs gate on the image existing and fail with a clear message, never a silent build.
- Posture (baseline carried from the old system, requirement 5): `mem_limit=2g`, `cpus=2`, `no-new-privileges`, non-root user inside, no `--privileged`, bridge network by default and `--network=none` available per run for offline categories.
- Interface, the only thing the loop knows about:
  ```python
  class Sandbox(Protocol):
      def start(self) -> None: ...
      def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult: ...   # structured, never string-prefixed errors
      def write_file(self, rel_path: str, content: bytes) -> None: ...
      def read_file(self, rel_path: str) -> bytes: ...
      def put_inputs(self, files: Sequence[Path]) -> list[str]: ...   # returns verified in-container paths
      def stop(self) -> None: ...
  ```
- **Amended 2026-09-09: `read_file` removed from the protocol** and from all three implementations. It was written into the interface at M0 and never called once — `put_inputs` gets challenge files *in*, and everything the loop reads back comes out through `exec`'s stdout. `ReplaySandbox`'s implementation existed only to raise "does not support read_file". An interface member with no caller is not insurance, it is three bodies to keep correct for nothing. It goes back in the day something needs it.
- Three implementations: `DockerSandbox` (real), `StubSandbox` (in-process fake filesystem + scripted command responses; no daemon, no tokens — this is requirement 4), `ReplaySandbox` (serves recorded outputs from a trace, for replay verification).
- Every command is mirrored to `/ctf/.agent_live.log` inside the container so a human can `docker exec` and tail it mid-run (preserve item 12). Interactive tools are always driven non-interactively — `run_gdb` is batch-only, no exceptions (item 11).
- **Amended 2026-09-07** (carried back from the predecessor after re-reading `DeprecatedProject/.../routes.py:1200` and `docker_mgr.py:18`, both dropped by accident in the rewrite):
  - **The arena is pinned to `linux/amd64`**, on both `docker build` and `containers.run`, regardless of the host's own architecture. CTF challenge binaries are overwhelmingly x86-64 ELF; on an arm64 host an unpinned build yields an arm64 arena in which those binaries cannot execute, and the failure presents as a broken challenge rather than a broken arena. Docker emulates; slower is the correct trade. `arena status`/`inspect()` additionally report the built image's architecture and warn on a mismatch, so an already-built arm64 arena is diagnosed rather than silently wrong.
  - **Containers are named `runectl-<run_id>`.** The predecessor named them `ctf-agent-<cid>` specifically so a human could `docker exec -it <name> bash` and take over mid-run — a workflow actually used at competitions (`POSTMORTEM.md` §3). A stale namesake from a crashed run is force-removed before start, as the predecessor did. `runectl run` prints the attach command on the human render.
- `put_inputs` verifies what actually landed and returns real paths; a file the user passed that didn't make it into `/ctf/` is a hard error before the first paid call, not an `[uploads verify]` notice the agent has to notice.

### D3 — Trace storage: **append-only JSONL as the source of truth, SQLite as a rebuildable index**

Locked. Files win on debuggability, greppability, zero infra, and "it survives a restart because it's a file." A datastore wins on cross-run capability questions. We get both by making the files authoritative and the database derived.

```
~/.local/share/runectl/runs/<run_id>/
    run.json        # manifest: challenge, model, config snapshot, outcome, cost, timings
    trace.jsonl     # append-only event stream, one JSON object per line — THE record
    artifacts/      # files the agent wrote, captured outputs over the inline cap
    cassette.jsonl  # recorded provider request/response pairs (only when --record)
~/.local/share/runectl/index.db   # SQLite, derived; `runectl index rebuild` regenerates it from runs/
```

- Event envelope, version 1: `{"v":1,"run_id":str,"seq":int,"ts":float,"type":str,"data":{...}}`. `seq` is monotonic per run and is the replay ordering key.
- Event types (closed set for V1): `run.started`, `challenge.loaded`, `triage.result`, `llm.request`, `llm.response`, `tool.call`, `tool.result`, `progress.scored`, `budget.blocked`, `strategy.shift`, `evidence.added`, `flag.candidate`, `flag.decision`, `cost.updated`, `error`, `run.finished`. (`flag.reviewed` shipped with M6 and `budget.exhausted` with D19 — the shipped set is 18, not 16; `docs/TRACE.md` is the current inventory.)
- **Amended 2026-09-09: `llm.thinking` added**, following the four-step process in `CONTRIBUTING.md` ("Adding an event type"). Carries `step`, `text`, `level`, `truncated`. A separate event rather than a field on `llm.response` because thinking text is long and unbounded — keeping it as its own event lets the writer's 8 KB artifact-spill rule (below) apply to it independently, so a long thinking block doesn't bloat every `llm.response` line in `trace.jsonl`. See D20.
- **Amended 2026-09-09: `evidence.added` removed** (the shipped set is **19**). It was declared in the schema at M0 for an evidence store that was never built, and nothing has ever emitted it — `docs/ARCHITECTURE.md` and `docs/STATUS.md` both carried it as a known gap. Findings carry forward in the conversation instead. Removing it is not a decision that findings shouldn't be recorded; it is a refusal to keep a schema entry that documents an intention rather than a behavior. If the evidence store is built, this goes back through `CONTRIBUTING.md`'s four-step "Adding an event type" like any other event.
- **Amended 2026-09-09: `artifact_ref` / `provenance_artifact` removed** from `tool.result` and `flag.candidate`. Both were superseded before they were ever set: the writer's spill rule replaces an oversized string *in place* with `{"$artifact": <digest>, "bytes": n}` rather than moving it to a sibling reference field. Two ways to say "this value is in artifacts/", one of them dead.
- **Amended 2026-09-10: `flag.rederived` added** (same four-step process; the shipped set is now **20**). Carries `step`, `source_seq`, `command`, `matched`, the full `ExecResult` fields, and `errored`.

  The reason is not a feature — **the trace was incomplete.** D15 mechanism 2 re-runs the cited command by calling `Sandbox.exec` directly rather than through the dispatcher, so a command genuinely executed inside the run's container and left no record of having done so. For a decision whose first line is "append-only JSONL as the source of truth," that is a defect in this decision, not in D15.

  What surfaced it was a symptom two layers away: `ReplaySandbox` serves recorded exec results **strictly positionally**, so the unrecorded exec silently consumed the *next* tool call's output and shifted every result after it. A replayed run therefore issued an identical tool-call sequence while quietly ending `candidate` instead of `solved` — and `runectl replay --check`, which compared only the sequence, reported OK. `--check` now asserts the outcome as well; `tests/integration/test_replay_fidelity.py` is the regression gate, and it fails on all four assertions if this event is removed.

  Recorded even when `exec` *raises* (`errored: true`), because an unrecorded failed call desynchronizes the queue exactly as an unrecorded successful one does. A replay is served a plain `ok=False` result there rather than a re-raised exception: a different mechanism reaching the judge's identical "could not re-derive" verdict.
- The writer flushes after every event. A run killed with SIGKILL still leaves a valid, replayable prefix.
- Any value over 8 KB is written to `artifacts/` and referenced by digest in the event, so `trace.jsonl` stays greppable.
- **The trace is written before any agent logic exists** (build order M1). Everything else emits into it.

### D4 — Interface contract: **CLI is the API; stdout is NDJSON; humans get stderr**

Locked. This is what makes requirement 2 mechanical rather than aspirational.

- `--output=jsonl` (default when stdout is not a TTY) streams trace events to stdout as they happen, one per line, plus a final summary object. `--output=human` (default on a TTY) renders the same event stream through `rich` to **stderr**. The human renderer is a pure function of the event stream — it may never read run state directly.
- Every flag has a non-interactive form. There is no prompt that can block a run. `--yes` is not needed because nothing asks.
- Exit codes, honest and stable:
  | code | meaning |
  |---|---|
  | 0 | flag found and finalized |
  | 2 | flag candidate found, awaiting approval (not a failure) |
  | 3 | run exhausted, no candidate |
  | 4 | sandbox/infrastructure failure |
  | 5 | provider failure after retries |
  | 6 | usage/config error |
- V1 command surface:
  ```
  runectl run --name --category --description|--description-file --file ... [--flag-format] [--model] [--max-steps] [--approval] [--network none|bridge] [--record] [--dry-run]
  runectl flag approve <run_id> [--flag ...]        # finalize a pending candidate
  runectl trace show <run_id> [--format timeline|jsonl]
  runectl replay <run_id> [--check]                 # re-run from cassette; --check asserts identical tool-call sequence
  runectl bench [--suite bench/practice]            # capability report over a challenge suite
  runectl keys set|list|rm <provider>
  runectl arena build|status
  runectl index rebuild
  ```
- A challenge may also be given as a single file: `runectl run --challenge chal.toml`. Same fields, so an agent driving the tool can write one file instead of shell-quoting a description.

### D5 — Provider layer: **BYO keys, multi-provider with no default, non-streaming, retried, metadata-driven**

Locked. Updated 2026-09-05: Google (Gemini) is a first-class third provider, not a later addition, and there is no "primary" — the product promise is bring whatever model you want because different models are better at different tasks. `--model` is required; nothing is inferred by default.

- **Key handling** (this is a headline product feature — the user brings whatever model they want): precedence is `--api-key` > env (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`) > OS keyring (`runectl keys set <provider>`) > `~/.config/runectl/keys.json` at mode 0600. Keys are **never** written to project config, never logged, and are redacted from the trace by a writer-level filter. This closes the plaintext-`config.json` hole directly.
- One `Provider` protocol, one canonical tool schema, adapted per provider at the boundary (preserve item 8). Provider and capability selection come from an explicit **model registry** — a table of `{id, provider, context_window, supports_prompt_cache, price_in, price_out}` — never from string prefixes like `claude-`.
- **Amended 2026-09-09:** `supports_tools` dropped from the registry table. It was `True` on every row and read by nothing: a model that cannot call tools cannot run this agent at all, so the flag had no branch to guard. `cache_write_multiplier`/`cache_read_multiplier` likewise moved out of `ModelInfo` and into constants in `providers/cost.py` — identical on all seven rows, and this file's own D18 note already described them as provider-standard rather than per-model. They become fields again the day a provider actually differs.
- **Non-streaming request/response in the core.** The old system streamed to feed a live browser UI; we don't have one, and non-streaming makes retries, caching, cassettes, and replay determinism straightforward. Live watchability comes from per-step events and the container live log. Streaming can return later as a display concern only.
- Retry with exponential backoff + jitter on 429/5xx/timeouts (default 4 attempts). A run degrades and reports; it does not die on a transient 429.
- **Every** LLM call goes through this path — including summarization and retry-summary generation. No hardcoded `gpt-4o-mini`, no silent no-op when a provider is absent. Utility calls use a configurable `--utility-model` that defaults to the cheapest model of the *same provider as the main model*, and their tokens land in the same cost ledger.
- Prompt caching on the stable system prefix where the provider supports it (registry flag), so category playbooks aren't re-billed every step.
- `ReplayProvider` reads `cassette.jsonl` and serves responses by request hash. `ScriptedProvider` returns hand-written responses for unit tests. Together these are requirement 4: the loop is fully testable with zero spend.
- **Amended 2026-09-09 — explicitly not changed by the `runectl config`/`runectl tui` work.** `--model` stays required on `runectl run`; nothing is inferred by default. `runectl config` (below, alongside the D13 amendment) stores per-provider *preferences* — a default model and thinking level — that prefill flags in `runectl models list` and the TUI's launcher. They are read only where the user explicitly set them, they never silently choose a model for a `run` invocation, and the TUI always composes and shows an explicit `--model` before launching. See D20 for the parallel statement about thinking defaults.

### D6 — Run state: **one explicit object, no mixins**

Locked. The old system's six mixins sharing ~30 implicit `self` attributes is the coupling failure. The rebuild has a single `RunState` dataclass owned by the loop, passed explicitly to collaborators (`ProgressTracker`, `FlagJudge`, `ContextBuilder`, `ToolDispatcher`) which are plain objects with constructor dependencies. No collaborator mutates `RunState` directly; they return values the loop applies. This is what makes the loop unit-testable.

### D7 — Tool surface: **the five, structured results, no growth without a rule**

Locked: `run_command`, `write_file`, `run_gdb`, `search_flag`, `submit_flag` (preserve item 7). Defined once as Python objects; the OpenAI and Anthropic schemas are both *derived*.

- Results are structured values — `ToolResult(ok: bool, kind: Literal["output","error","blocked"], stdout, stderr, exit_code, duration_s, truncated, shell_command)` — never `[error]`-prefixed strings.
- **Amended 2026-09-09:** `artifact_ref` and `meta` dropped (never set by any handler — see the D3 amendment for why `artifact_ref` was already superseded by the writer's in-place spill), and `shell_command` recorded here as the field that did earn its place: D15's re-derivation has to re-run exactly what ran, which the tool arguments alone cannot reconstruct for `run_gdb` and `search_flag`.
- Exactly one tool call is executed per step; extras are rejected with a structured `blocked` result and a nudge (earned rule 6).
- A sixth tool is added only if a category demonstrably cannot be served by shell, argued in this file first.

### D8 — Progress machinery: **category-parameterized from day one**

Locked, and this is the crown jewel (`POSTMORTEM.md` §2, coverage). Anti-loop is not "web plus seven strings of prompt text." Every category gets the same four mechanisms, tuned by data:

1. **Tactic families** — semantic classification of a command into a family (e.g. `dirfuzz`, `sqli`, `strings`, `disasm`, `decode`, `crack`, `pcap-filter`). Family regexes live in the category's data file, over a shared default set.
2. **Output fingerprinting** — normalize volatile parts (HTTP dates/headers, timestamps, PIDs, addresses, whitespace) then hash; a repeat fingerprint is not progress.
3. **Signal scoring** — per-category low-signal patterns (404/403 bodies, empty output, "not found") vs. high-value patterns (new paths, non-empty decodes, credential-shaped strings). Produces a numeric progress delta.
4. **Budgets** — per-hypothesis and per-family no-progress counters that *block* a command once exceeded, plus a forced strategy shift after N no-progress steps or M consecutive errors, injecting an evidence summary and "pick a different hypothesis class."

Shared defaults apply equally to all eight categories on day one — no earner-first tuning (D14). Web's old heuristics inform the shared defaults' *shape* since they're the only ones with real evidence behind them, but they ship as defaults every category gets, not as a special case for web alone. All thresholds live in the category TOML, not in code.

### D9 — Categories as data: **one TOML per category, loaded at runtime**

Locked. `src/runectl/categories/{pwn,web,crypto,forensics,rev,misc,osint,network}.toml`, each containing: execution brief, playbook text, required-tool list, tactic-family patterns, low/high-signal patterns, budgets, step limit, default network mode. Sourced from the predecessor's prompt archive (**[DURABLE]**/**[EARNED]** material), re-edited — not pasted wholesale. Adding a category is a data file; it is never a code change. The eight `[STALE]` step limits are carried as *starting* values explicitly marked unmeasured, and `runectl bench` is what tunes them.

### D10 — Pre-LLM work: **deterministic triage only, and it never sees the challenge identity**

Locked, and it is the structural guarantee behind requirement 15 (no answer keys).

- Exactly one code path runs before the first paid call: `triage(sandbox, category) -> TriageResult`. It runs a fixed, category-parameterized command set (`ls -la`, `file *`, `strings` head, `exiftool` for forensics, `checksec` for pwn, …) and returns structured findings.
- **`triage()` does not receive the challenge name, filenames-as-conditions, or the description.** It cannot branch on them, so a fast-path keyed to `logs.txt` or `REChallenge1.zip` is not something you have to remember not to write — there is nowhere to put it.
- There is no plugin hook, no "helper," and no pre-LLM solver of any kind. Techniques like PNG-LSB extraction exist only as tools in the arena image that the agent chooses to invoke.
- A test asserts the triage command set is identical across two runs whose only difference is challenge name and filenames.

### D11 — Flag policy: **candidate by default, corroboration-gated auto, explicit off**

Locked. `--approval` takes three values:

- `gated` (**default**): a candidate is auto-finalized only if it matches the user-supplied `--flag-format` regex **and** passes plausibility filtering (no placeholders like `picoCTF{flag}`, no GUIDs, no JSON fragments, no capture-interface IDs) **and** passes the verification double-check pass (D15) — which is re-derivation in the sandbox *plus* the disconfirmation review. Otherwise the run exits **2** with the candidate in the trace, and a human or driving agent finalizes via `runectl flag approve`.

  **Amended 2026-09-08 — corroboration ≥2 is no longer part of this bar**, replaced by the D15 §2 disconfirmation pass. It is still counted and reported on every candidate; it just does not hold one. The reason is measurement, not preference: the first live bench (`solver/bench/results/README.md`, claude-sonnet-5, $0.38) held four *correct* flags for want of a second sighting and finalized the one wrong flag in the suite. A clean solve produces its answer once, so the rule taxed exactly the runs it should have waved through, while a stubborn agent — told in its own rejection messages what the checker wanted — re-ran its solver with a timestamp printed above the flag until the fingerprints differed. On that evidence corroboration cost four solves and prevented zero false flags. The replacement is one call to the *cheapest* model of the same provider (≈$0.002), framed to disconfirm, and it can catch the class of error corroboration structurally cannot: a right answer in the wrong encoding.
- `strict`: never auto-finalize; always exit 2 with candidates ranked.
- `auto`: finalize the top plausible candidate. For live competition speed, at the user's own risk.

`gated` is the default because the CLI is meant to be AI-driven — pure human-in-the-loop can't be the only mode — while an uncorroborated guess still never gets submitted silently. This is revisitable once the `POSTMORTEM.md` §5 questionnaire is answered; it is the one default here most likely to move.

**Amended again 2026-09-08 (same day, later) — the disconfirmation review is *advisory*, not a gate.** It still runs on every candidate, its verdict is still written to the trace and printed, and it still costs about $0.002. It no longer holds anything. Under `gated` a candidate is finalized on the deterministic set alone: plausibility, provenance and anti-echo, decoy markers, `--flag-format`, and re-derivation in the sandbox.

The reason is the review's own record over nine live reviews across benches 2 and 3 (`bench/results/README.md`): six correct clears, **two wrong flags cleared** (`quick-math`, both times), and **one correct flag held** (`machine-fix`, because the reviewer could not see the challenge's `code.py` — the review prompt gets the description, the cited command and its output, not the attached files). Caught: zero. Cost: one solve. That is a measured negative, not a neutral result.

There is also a structural reason, and it is the one that will still be true after more data. In a competition the scoreboard is a free and perfect oracle, and `runectl` exits **2** with the candidate in the trace precisely so a human or a driving agent can consult it. A $0.002 model *guessing* at correctness is competing with something free and definitive that runs seconds later. Its defensible job is the narrow one — unattended runs with nobody reading the trace, or a scoreboard that penalizes wrong submissions — not gating the default path.

*Recorded as of 2026-09-10.* Re-derivation runs a command in the sandbox, and that command is now written to the trace as `flag.rederived` (D3's amendment). Nothing about the mechanism or the gate changed — only that the check now leaves the same evidence trail everything else does, which is also what lets `runectl replay` reproduce a gated finalize.

Note what this leaves standing. Both mechanisms that have been tried as the gate and removed — corroboration, then the review — were forms of model or agent judgement, and each was satisfiable or fooled by the model it was judging. What survives is the sandbox: provenance says the string came from a tool's output rather than the agent's own command, and re-derivation says the tool produces it again. Those are the checks a model cannot talk its way past. `_GATING_CHECKS` in `flags/judge.py` names the set explicitly so this cannot drift by accident.

*Not decided here:* whether to widen what the reviewer sees (the `code.py` blind spot). That question is now much less urgent — an advisory doubt costs nothing — and is parked rather than answered.

*Implementation clarification, 2026-09-08 (M6).* The policy above is unchanged; one case it does not name had to be resolved to write the judge. **When no `--flag-format` is supplied, the format condition does not apply rather than failing.** Read the other way — an absent format counts as an unmet condition — `gated` would silently behave as `strict` for every challenge whose format the user did not type out, which is a default nobody chose. The other three `gated` conditions (plausibility, corroboration ≥2, re-derivation) are unaffected, and a *supplied* format that does not match still holds the candidate. Separately: mechanisms 1 and 3 of D15 (provenance, decoy detection) are enforced under **all three** policies — `--approval auto` buys speed on corroboration and re-derivation, not the right to submit an invented or planted string.

### D12 — Context management: **summarize, don't truncate; one limit**

Locked. One configurable `context.tool_output_limit` (default 6000 chars) with no second dead constant. Over the limit, the output is written to `artifacts/` and an extractive summarizer (deterministic first: head + tail + regex-salient lines; LLM summarization only if still over) produces what enters context. Identical tool output is deduplicated to a back-reference by digest (preserve item 9). History compaction runs on a token-budget trigger, not "every 15 steps," and goes through the standard provider path (D5).

### D13 — CLI-only, permanently: **no server, no browser UI, ever — an in-terminal TUI is in bounds**

Locked, and rewritten 2026-09-05 from an earlier draft that planned a GUI as a later consumer. There is no GUI in this product's future — the CLI is the whole product, permanently. This is a stronger statement than "no GUI yet": it forecloses `idalia serve`/`runectl serve`, an SSE tailer, and a separate `ui/` package as things to ever build.

- The event-stream/renderer split (D4) is kept anyway, but purely for testability and for the stdout-NDJSON / stderr-human duality — not as a seam for a future front end.
- The one retained boundary: only `cli/` renders. Core code (`trace/`, `sandbox/`, `providers/`, `loop/`, `tools/`, `progress/`, `flags/`, `categories/`) emits structured events and never prints — this keeps the loop testable and scriptable, which is a CLI concern in its own right, not a GUI-readiness concern.
- No import-linter GUI boundary is needed because there is nothing on the other side of it to protect against.

**Amended 2026-09-09 — a local, in-terminal TUI is in bounds; the lock on a server or a browser UI is unchanged and is not what this amendment relaxes.** The user asked for an interactive way to watch and manage runs — multiple in flight, thinking visible, flags approvable — and a one-line-per-event stderr stream (however well polished by M9) cannot serve that; something stateful, navigable, and redrawing has to own the terminal. That is a TUI, not a GUI, and this amendment says so explicitly rather than letting it happen by drift.

- **What's now allowed:** `runectl tui`, an interactive, full-screen, in-terminal application living at `src/runectl/cli/tui/` (Textual, D1). It is `cli/` code under every rule that phrase already carries — it renders, it never contains agent logic, and it is the only new thing permitted to hold a live redraw loop.
- **What stays forbidden, permanently, and this amendment does not touch it:** `runectl serve`, any HTTP server, any network listener, any SSE tailer, any browser-rendered UI, any top-level `ui/` package. "In-terminal" is load-bearing — nothing here opens a port.
- **Why this doesn't reopen the failure D13 was written against.** The old system's structural failure (`POSTMORTEM.md` §2) was the agent loop calling `socketio.emit(...)` from inside itself and being constructed directly by a Flask route — no boundary between agent and UI, so the agent couldn't run headless or be tested without the whole server. The TUI does not touch the loop process at all: it spawns `runectl run --output jsonl` as a **subprocess** and reads the same NDJSON event stream any other driving agent reads (D4's existing output contract). The loop stays synchronous, single-process, and exactly as testable as before; the TUI is just another consumer of the stream, running in a second process. Multiple runs in the TUI are multiple subprocesses, each with its own container — no threading or async was added to `loop/runner.py` to get there (D2 unchanged: one container per run).
- **D4's non-interactivity guarantee is unchanged for the thing it was written to protect: `runectl run` itself.** Every flag on `run` still has a non-interactive form and nothing about running a challenge can block on a human. The TUI is interactive *as a separate process that composes and launches non-interactive commands* — interactivity moved outside the run, it was not introduced inside it. The one pre-existing exception, `runectl arena ensure` with no flags on a TTY (D17), is untouched; the TUI itself must not use it as a loophole to auto-build the arena — see the TUI section of `docs/CLI.md`.
- Adding `runectl tui` does not change what any other command does or how `run --output jsonl`/`--output human` behave. A user who never runs `runectl tui` sees no difference.

### D14 — Category depth: **all eight equal, no earner-first order**

Locked, rewritten 2026-09-05 from an earlier draft that fixed a deepening order (web → forensics → crypto → rev → pwn → network → misc → osint). That sequencing is dropped: **no category is tuned ahead of the others.** Shared defaults (D8) and full category data (D9) for all eight ship together at V1; per-category *improvement* beyond the shared defaults is a later, uniform effort applied to every category at once, not a queue.

Web's old heuristics remain the only evidence-backed starting point (`POSTMORTEM.md` §2, coverage), so they inform the *shape* of the shared defaults every category gets — that is a statement about where the starting numbers come from, not a license to keep tuning web first. Revisit only if the `POSTMORTEM.md` §5 questionnaire gives a reason to cut a category for V1 entirely — that is a scope question, not a sequencing one.

*Built 2026-09-09 (M7) — the remaining five categories ship as data.* `pwn`, `rev`, `forensics`, `osint` and `network` now have TOMLs alongside `web`/`crypto`/`misc`, re-edited from the predecessor's prompt archive into decision-order playbooks (not the old command walls, per D9), at equal depth from day one. `load_all()` now asserts the full `STANDARD_CATEGORIES` set is present rather than loading whatever globs. Three coupled changes landed with them:

- **The arena grew a toolset (`arena/Dockerfile`).** The new playbooks name tools the image lacked, and `test_categories.py` enforces that every `required_tools` entry is installed. Added: `rizin`, `ltrace`, `strace`, `upx-ucl`, `one_gadget` (pwn/rev); `sleuthkit`, `poppler-utils`, `volatility3`, `sox`/`ffmpeg`/`multimon-ng`, `unrar` (forensics — `outguess` was dropped, absent from the Kali repo; `steghide` covers JPEG stego); `whois`, `dnsutils` (osint); `nmap`, `aircrack-ng`, `wireshark-common` for `capinfos` (network). This changes the Dockerfile fingerprint (D17), so an existing arena warns as stale until rebuilt — expected.
- **The five default to `network = "bridge"`, not `none`.** A deliberate choice (the user's, 2026-09-09): remote-target pwn, a live osint lookup, and a network challenge that hands you a host all want egress by default, and `--network none` is one flag away for offline work. `crypto`/`misc` stay `none`; `SECURITY.md` notes the split. The trade is that these categories start with egress on — acceptable because the sandbox is containment for LLM-authored commands, not a hardened boundary (`SECURITY.md`), and the offline categories that most want isolation keep it.
- **The bench grew from 5 to 10 (`bench/practice/`).** One case per new category, so the suite measures more than crypto+reasoning. `rev`/`forensics`/`network` are gated offline-honest cases (flags re-derived locally); `pwn` and `osint` are scored **outside the gate** with a `gate_note`, for structural reasons (pwn: a local flag file the agent's shell can read directly, so a solve can't prove exploitation; osint: an answer that lives in rotted live-internet state). `network`'s case (`stream-secret`) is **authored by Idalia** (Apache-2.0), not vendored — the MIT source had no pcap challenge — and its `PROVENANCE.md` states the honesty cost of scoring a challenge you wrote. See `bench/README.md`.

### D15 — False-flag defense: **a first-class subsystem, not a side effect of plausibility filtering**

Locked. False flags are the #1 product risk — a wrong flag scores worse than no flag, and the V1 bench gate is 2/5 solved with **0 false flags**. Five mechanisms, all landing in M6 (post-skeleton); the skeleton's trace and `submit_flag` signature carry the fields they need so M6 slots in without an API change:

1. **Provenance is mandatory.** `submit_flag(flag, how_found, provenance)` — `provenance` names the trace `seq` and artifact the flag came from. The judge re-reads that event and confirms the flag literally appears there. A flag the agent cannot point to in its own trace is rejected. This kills invented strings structurally, not just probabilistically.
2. **Verification double-check pass.** A cleared candidate is not submitted yet: the judge re-derives it deterministically first — re-run the cited command in the sandbox and confirm the same string reappears, independently re-decode a decode chain, check `--flag-format`. LLM verification (on the standard costed provider path, D5, framed to *disconfirm* rather than confirm) is a last resort only. Under `gated` (D11), only a re-derived candidate is eligible.
3. **Decoy detection.** Reject or penalize a candidate whose source carries decoy markers: a path/file named `decoy`/`fake`/`honey`/`not_the_flag`, nearby text like "nice try" / "this is not the flag", or a token that appeared verbatim in the pasted challenge description (a planted lure the agent should not echo back as its own finding).
4. **Independent corroboration ≥2** — already in D11, restated here as part of the named subsystem: two *different* tool calls with *different* fingerprints (D8) must produce the same candidate.
5. **No-flag-is-success.** Stated plainly as a base rule the agent sees: no flag with solid, cited evidence is a correct outcome; an unsupported guess is a failure. A run exits **3** rather than fabricate. This is the explicit counterweight to "act, don't ask" (an earned rule from the predecessor, `POSTMORTEM.md` §3) which, alone, biases the model toward answering.

*Skeleton seam:* M4 ships a minimal `submit_flag`/judge that already takes `provenance` and emits `flag.candidate` / `flag.decision` events, but does not yet implement verification, decoy detection, or corroboration counting — those are M6.

*Built 2026-09-08 (M6), with two notes.* All five mechanisms are implemented in `flags/` (`judge.py`, `plausibility.py`, `decoys.py`, `review.py`). (a) **Mechanism 1 needed a vocabulary.** The agent can only cite a `seq` if it can see one, so every tool result now reaches the model with an `[observation seq=N]` header, and `submit_flag`'s `provenance` argument is that number. (b) **The LLM disconfirmation pass shipped the same day, on bench evidence** — the note that previously stood here said it had no caller and should not get one until real data justified it. It got one: `flags/review.py` runs a single call on the cheapest model of the same provider, framed to find a reason the flag is *wrong*, and it now carries the weight corroboration used to (see the D11 amendment above). It runs last, after every deterministic check, so a flag that fails on provenance or a decoy marker never costs a token. A doubtful or unreadable verdict, or a review that could not run at all, holds the candidate — the pass can only ever withhold a solve, never grant one on its own.

*The gate's scope, 2026-09-08.* "2 of 5 solved with 0 false flags" is unchanged, but a suite case may now sit **outside** it by setting `"gate": false` and a `gate_note` in its `expected.json`. One case does: bench `quick-math`. Its run performs the Hastad broadcast attack correctly, verifies the cube root is exact, and submits the recovered value one transformation short of the flag — so provenance, decoys, re-derivation and the disconfirmation review all agree with it, correctly, because the mathematics is sound. No mechanism this subsystem could add would catch it; it is a capability failure wearing a false flag's clothes, and leaving it in makes "0 false flags" unmeetable for a reason that has nothing to do with false-flag defense. Excluding a challenge you fail is also how benchmarks stop meaning anything, so the exclusion is loud rather than quiet: the case still runs, still counts in the headline solve rate and false-flag count, and prints its reason in the report next to the result. Only the pass/fail gate reads the narrower subset, and `load_suite` refuses an exclusion that gives no reason.

*Mechanism 1 refined 2026-09-08 — provenance matches the flag's **payload**, not the whole string.* The rule as written required the flag to appear literally in tool output. The second live bench found the case where that is impossible for a *correct* solver: `machine-fix`'s flag is a computed number inside a wrapper the challenge itself prints (`csictf{answer_you_get_from_above}`). The number can be derived and printed; the full string can only reach output if the agent types the wrapper into a command — which is precisely what the anti-echo rule rejects. So provenance and anti-echo were in direct contradiction for a whole class of challenge. The resolution follows from what each half of a flag is worth: the wrapper is *published*, so nobody earns it, and the payload is the only part that has to be derived. Provenance now accepts a payload sighting, on two conditions — the wrapper must be attested by the challenge (its prefix appears in the description or `--flag-format`, so an invented prefix falls back to requiring the whole string), and the payload must be at least 8 characters, because a short value appearing somewhere in a wall of output is coincidence rather than evidence. Anti-echo is untouched and still runs on the payload, so the original laundering incident is still caught: there the agent typed the payload itself. The tempting alternative — "treat all-digit payloads as suspicious" — was rejected outright, because `machine-fix`'s *correct* answer is thirty digits.

*Mechanism 4's status, same date.* Corroboration is still computed and reported; it is no longer a gate. What the bench showed is that "two observations with different fingerprints" is satisfiable by an agent that varies its own output — the fingerprint normalizer now strips bare epoch timestamps, but that is a patched instance, not a fixed class. Any string an agent can vary defeats fingerprint-based corroboration.

### D16 — Budgets measure **step *waste*, not step count**

Locked. A run is not failing because it took many steps; it is failing because it took steps that produced no new signal. Step *limits* (D9 category TOML; starting values carried from the predecessor, `POSTMORTEM.md` §5 Q6) are a backstop, not the primary metric. The primary metric is the **progress ratio**: `progress_steps / steps_used`, with `blocked_steps` (steps rejected by a budget, D8) tracked separately and reported in `run.json`. Budgets (D8) are the mechanism that acts on step waste; this decision is the framing that says *why* they exist and what `runectl bench` (M8) should optimize toward — a high solve rate with a low waste ratio, not merely "fewer steps." As real run data comes in (`POSTMORTEM.md` §5), limits come down; they do not go up.

*Skeleton seam:* M4's `RunState` (D6) already carries `steps_used` as a field; `progress_steps`/`blocked_steps` are real fields but computed by a no-op progress stub (M5 fills in real scoring) so `run.json` has the shape from day one even before the numbers mean anything.

### D17 — First-run arena setup: **check always, remediate explicitly, never prompt inside a run**

Added 2026-09-07. D2 established that the arena image is built once and that runs "gate on the image existing and fail with a clear message, never a silent build." That held, but it left a bad first-run experience: on a fresh machine the only remedy was a 15-40 minute network build, there was no way to hand `runectl` an image you already had, and nothing noticed when an existing image predated a Dockerfile change. D17 fills that in without weakening D2 or D4.

- **Presence and provenance are checked before every run**, in a preflight that runs *before* the run directory is created and before any paid call. A missing image exits **4** with the full list of remedies. An unreachable daemon is a distinct message from a missing image.
- **`build` stamps a fingerprint.** `docker build` applies `dev.idalia.runectl.arena-fingerprint=<sha256 of the Dockerfile, 16 hex>` as a label. `inspect()` compares it to the Dockerfile in the tree, so "your arena is older than your checkout" is detectable after an update. A **stale image warns and proceeds** — it still works, it is merely old. An image with **no** label is "unknown provenance," never "stale": an image built before stamping existed must not start nagging.
- **Three remedies, all non-interactive by flag**: `runectl arena ensure --build` (build here), `--from-file PATH` (load a `docker save` tarball — the offline/air-gapped/USB-stick path, and how a prebuilt arena moves between machines), `--from-registry REF` (pull and tag a prebuilt image). `load_archive` re-tags an archive that carried a different name rather than making the user work out `docker tag`.
- **`runectl arena ensure` with no flags on a TTY is the one interactive surface in the product.** It asks which route the user wants. With no flags and no TTY it prints the remedies and exits 4 — it can never block a script or an agent.
- **`runectl run` still never prompts and never silently builds** (D2, D4 intact). It reports and exits; the human runs `arena ensure`. An agent driving the tool reads the same message and runs the same non-interactive command.

*Not decided here:* whether Idalia publishes a prebuilt `runectl/arena:kali` to a registry. `--from-registry` accepts any reference, so publishing later is a distribution decision, not a code change.

### D18 — Prompt caching is real, and the ledger prices it

Added 2026-09-07. D5 promised prompt caching "on the stable system prefix where the provider supports it," and the registry advertised `supports_prompt_cache=True`, but no adapter ever sent a cache breakpoint — the feature existed only in this file. Two corrections:

- **The breakpoint goes at the end of the message list, not on the system prompt.** Top-level `cache_control: {"type": "ephemeral"}` caches the last cacheable block, which in an agent loop is the growing conversation. Each step then reads tools + system + every earlier turn from cache at 0.10x instead of paying full input rate to re-send it. Caching only the system prefix would mostly *not fire*: the minimum cacheable prefix is 512-4096 tokens depending on model, and a category playbook alone is under it. This supersedes D5's "on the stable system prefix" wording.
- **Cached tokens are billed differently, so `Usage` and `CostLedger` model them separately.** `input_tokens` now means *uncached* input; `cache_read_tokens` bills at 0.10x `price_in` and `cache_write_tokens` at 1.25x. Folding them together would have overstated a cached loop's cost by up to 10x. Providers without caching leave both at zero.

Registry pricing was corrected the same day against a live `models.list()` and the published rates: Opus 5 was recorded at $15/$75 (actually $5/$25), Sonnet 5 at $3/$15 (actually $2/$10), and both at a 200K context window (actually 1M). The OpenAI and Google rows remain unverified estimates.

### D19 — Every run has a hard spend ceiling

Added 2026-09-07. `runectl` spends the user's own prepaid balance, and an agent loop's failure mode is *many steps*, so "it stopped because it ran out of steps" is not a sufficient guarantee — a step limit bounds actions, not dollars. `--max-cost` bounds dollars directly.

- Default **$0.50** per run. Chosen so a runaway loop on a small prepaid balance is an annoyance, not a disaster. `--max-cost 0` disables the ceiling.
- The check runs immediately after the ledger updates and **before** the next paid call, so the run stops one call early rather than one call late.
- Crossing the ceiling is a normal outcome, not an error: the run emits `budget.exhausted`, finishes as `exhausted`, and exits **3**. The trace records the limit and the actual spend.
- This is a *ceiling*, not an estimate. It cannot prevent a single very expensive call from overshooting it; it prevents the *next* one.

### D20 — Extended thinking: **opt-in, explicit, and always recorded**

Added 2026-09-09. `runectl` gains a `--thinking <off|low|medium|high|xhigh|max>` flag on `run` and `bench run`, and the model's reasoning becomes a first-class part of the trace (`llm.thinking`, D3) instead of being discarded after only its length was recorded. This is what makes `PLAN.md`'s stated demo — "show the thought, show the command, show the output, show the next move" — buildable at all; before this decision the thought was the one thing no trace contained.

- **Default is `off`.** Thinking is not free — on Anthropic it is billed as output tokens against the same ledger and the same D19 spend ceiling — so a run that didn't ask for it doesn't pay for it. `off` is also the safe default for `bench run`, where a suite is run unattended and repeatably.
- **The resolved level is always written to `run.started` and `run.json`**, never only implied by a flag the user might not remember passing. A run's reasoning spend is never invisible after the fact, matching the spirit of D19 ("every run has a hard spend ceiling" — the ceiling means nothing if what fed it isn't visible).
- **One shared CLI vocabulary across three different provider APIs.** Anthropic exposes adaptive thinking plus an `output_config.effort` level; OpenAI exposes `reasoning.effort`; Google exposes a thinking-token budget. `runectl` presents one 6-value scale (`off` through `max`) and maps it per provider in the model registry (`ModelInfo.thinking_style`). Where a provider or model can't represent a requested level (e.g. a model with no thinking support, or a narrower effort range), **the CLI clamps and records the clamp in the trace — it never silently substitutes a different level without saying so.** This is the same "loud, not silent" posture as D11's approval gating and D15's decoy detection.
- **Thinking blocks round-trip.** A provider that returns thinking content as structured blocks (not just prose) must have those blocks replayed back unchanged on the next request in the same run, per that provider's own API contract — dropping them or reordering them relative to text/tool-call content is a protocol violation for that provider, not a rendering choice. `ScriptedProvider` and `ReplayProvider` (D5) are extended to carry thinking blocks so this is testable without spend.
- **`--record`'s cassette hashing (D5's `ReplayProvider`) must include the resolved thinking configuration.** A cassette recorded with thinking off must not be served to a replay requesting thinking on, or `replay --check` would compare a thinking-augmented run against a cassette that never asked the model to think, which is not the regression test D4's replay contract promises.
- **Not a category concern.** Thinking level is a per-run, per-model choice like `--model` itself, not something a category TOML declares (D9 untouched) — a category's playbook doesn't know what the user is willing to spend on reasoning for their own challenge.

---

## Deliberately not decided yet

- **License.** **Decided 2026-09-08: Apache-2.0** (`LICENSE`, `NOTICE`, declared in `pyproject.toml`). Permissive, standard for infrastructure tooling, and unlike MIT it carries an explicit patent grant — which matters for a company that intends to build commercial products next to this one. It keeps an open-core layer available later; copyleft would have blocked the adoption the project is being open-sourced to get. The practice challenges under `bench/practice/` are **not** covered by it: they are third-party MIT material, with the required copyright and license text in `bench/THIRD_PARTY_LICENSES.md` and per-challenge author credit in each `PROVENANCE.md`.
- **Step-limit and budget numbers.** Carried from the predecessor as marked-unmeasured starting values (`POSTMORTEM.md` §5 Q6). `runectl bench` tunes them; do not treat them as tuned.
- **Anything about hosting, accounts, or billing.** Out of scope for V1.

## Assumptions I made in the user's absence

Recorded so they're cheap to overturn:

1. ~~Anthropic is the primary provider and OpenAI the secondary~~ — superseded 2026-09-05: Anthropic, OpenAI, and Google all ship in V1 with no primary; the product promise is BYO-any-key, equally. A fourth provider is a registry entry plus an adapter, not a redesign.
2. Runs are single-challenge and single-threaded. Parallelism is a V2 concern and no V1 decision blocks it. **Note 2026-09-09:** the `runectl tui` amendment to D13 introduces *process-level* concurrency (multiple `runectl run` subprocesses watched at once) without touching this assumption — `Runner.run()` itself is still a single synchronous loop per process; nothing in `loop/` gained a thread or an event loop.
3. The target machine is the founder's laptop with Docker Desktop. No remote/cloud sandbox in V1.
4. `gated` (D11) is the right default; the §4 questionnaire may move it to `auto` for live comps.
