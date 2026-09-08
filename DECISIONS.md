# DECISIONS — runectl (locked)

Written 2026-09-05, updated 2026-09-05 (name, CLI-only lock, multi-provider/no-default, equal categories, D15/D16 — see "Corrections folded in below"). This closes `REBUILD_NOTES.md` §7 and `PLAN.md` "Finish the stack decision." Everything here is **locked for V1**: build to it, don't re-litigate it mid-build. Changing a decision means editing this file with a dated reason, not quietly diverging in code.

Product, restated in one paragraph so the decisions have something to serve:

> A local, BYO-API-key CLI named **`runectl`**. The user hands it a challenge — name, category, the prompt/description they were given, and any provided files — plus their own provider key (Anthropic, OpenAI, or Google, whichever model they want). It runs an autonomous agent loop inside a per-challenge Docker sandbox, works toward a flag, and writes a complete, replayable trace of everything it tried. It is built to be driven by another AI agent (machine-readable I/O, non-interactive, honest exit codes). **It is CLI-only, permanently — there is no GUI, no `serve` command, no `ui/` package, ever.**

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

- Dependency manager: **uv**, with a committed `uv.lock`. Every dependency pinned (fixes `REBUILD_NOTES.md` §3 security hygiene).
- Layout: `src/` layout, package name `runectl`, console script `runectl`.
- Typed throughout; `mypy --strict` on `src/runectl/` in CI. This is the direct structural answer to "stringly-typed control flow."
- Runtime deps, complete V1 list: `typer`, `pydantic>=2`, `docker`, `anthropic`, `openai`, `google-genai`, `keyring`, `rich`. Category data uses stdlib `tomllib` — no YAML dep. Dev: `pytest`, `pytest-cov`, `mypy`, `ruff`.

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
- Three implementations: `DockerSandbox` (real), `StubSandbox` (in-process fake filesystem + scripted command responses; no daemon, no tokens — this is requirement 4), `ReplaySandbox` (serves recorded outputs from a trace, for replay verification).
- Every command is mirrored to `/ctf/.agent_live.log` inside the container so a human can `docker exec` and tail it mid-run (preserve item 12). Interactive tools are always driven non-interactively — `run_gdb` is batch-only, no exceptions (item 11).
- **Amended 2026-09-07** (carried back from the predecessor after re-reading `DeprecatedProject/.../routes.py:1200` and `docker_mgr.py:18`, both dropped by accident in the rewrite):
  - **The arena is pinned to `linux/amd64`**, on both `docker build` and `containers.run`, regardless of the host's own architecture. CTF challenge binaries are overwhelmingly x86-64 ELF; on an arm64 host an unpinned build yields an arm64 arena in which those binaries cannot execute, and the failure presents as a broken challenge rather than a broken arena. Docker emulates; slower is the correct trade. `arena status`/`inspect()` additionally report the built image's architecture and warn on a mismatch, so an already-built arm64 arena is diagnosed rather than silently wrong.
  - **Containers are named `runectl-<run_id>`.** The predecessor named them `ctf-agent-<cid>` specifically so a human could `docker exec -it <name> bash` and take over mid-run — a workflow actually used at competitions (`TEARDOWN.md` line 13). A stale namesake from a crashed run is force-removed before start, as the predecessor did. `runectl run` prints the attach command on the human render.
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
- Event types (closed set for V1): `run.started`, `challenge.loaded`, `triage.result`, `llm.request`, `llm.response`, `tool.call`, `tool.result`, `progress.scored`, `budget.blocked`, `strategy.shift`, `evidence.added`, `flag.candidate`, `flag.decision`, `cost.updated`, `error`, `run.finished`.
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
- One `Provider` protocol, one canonical tool schema, adapted per provider at the boundary (preserve item 8). Provider and capability selection come from an explicit **model registry** — a table of `{id, provider, context_window, supports_tools, supports_prompt_cache, price_in, price_out}` — never from string prefixes like `claude-`.
- **Non-streaming request/response in the core.** The old system streamed to feed a live browser UI; we don't have one, and non-streaming makes retries, caching, cassettes, and replay determinism straightforward. Live watchability comes from per-step events and the container live log. Streaming can return later as a display concern only.
- Retry with exponential backoff + jitter on 429/5xx/timeouts (default 4 attempts). A run degrades and reports; it does not die on a transient 429.
- **Every** LLM call goes through this path — including summarization and retry-summary generation. No hardcoded `gpt-4o-mini`, no silent no-op when a provider is absent. Utility calls use a configurable `--utility-model` that defaults to the cheapest model of the *same provider as the main model*, and their tokens land in the same cost ledger.
- Prompt caching on the stable system prefix where the provider supports it (registry flag), so category playbooks aren't re-billed every step.
- `ReplayProvider` reads `cassette.jsonl` and serves responses by request hash. `ScriptedProvider` returns hand-written responses for unit tests. Together these are requirement 4: the loop is fully testable with zero spend.

### D6 — Run state: **one explicit object, no mixins**

Locked. The old system's six mixins sharing ~30 implicit `self` attributes is the coupling failure. The rebuild has a single `RunState` dataclass owned by the loop, passed explicitly to collaborators (`ProgressTracker`, `FlagJudge`, `ContextBuilder`, `ToolDispatcher`) which are plain objects with constructor dependencies. No collaborator mutates `RunState` directly; they return values the loop applies. This is what makes the loop unit-testable.

### D7 — Tool surface: **the five, structured results, no growth without a rule**

Locked: `run_command`, `write_file`, `run_gdb`, `search_flag`, `submit_flag` (preserve item 7). Defined once as Python objects; the OpenAI and Anthropic schemas are both *derived*.

- Results are structured values — `ToolResult(ok: bool, kind: Literal["output","error","blocked"], stdout, stderr, exit_code, duration_s, truncated, artifact_ref)` — never `[error]`-prefixed strings.
- Exactly one tool call is executed per step; extras are rejected with a structured `blocked` result and a nudge (earned rule 6).
- A sixth tool is added only if a category demonstrably cannot be served by shell, argued in this file first.

### D8 — Progress machinery: **category-parameterized from day one**

Locked, and this is the crown jewel (`TEARDOWN.md` §3). Anti-loop is not "web plus seven strings of prompt text." Every category gets the same four mechanisms, tuned by data:

1. **Tactic families** — semantic classification of a command into a family (e.g. `dirfuzz`, `sqli`, `strings`, `disasm`, `decode`, `crack`, `pcap-filter`). Family regexes live in the category's data file, over a shared default set.
2. **Output fingerprinting** — normalize volatile parts (HTTP dates/headers, timestamps, PIDs, addresses, whitespace) then hash; a repeat fingerprint is not progress.
3. **Signal scoring** — per-category low-signal patterns (404/403 bodies, empty output, "not found") vs. high-value patterns (new paths, non-empty decodes, credential-shaped strings). Produces a numeric progress delta.
4. **Budgets** — per-hypothesis and per-family no-progress counters that *block* a command once exceeded, plus a forced strategy shift after N no-progress steps or M consecutive errors, injecting an evidence summary and "pick a different hypothesis class."

Shared defaults apply equally to all eight categories on day one — no earner-first tuning (D14). Web's old heuristics inform the shared defaults' *shape* since they're the only ones with real evidence behind them, but they ship as defaults every category gets, not as a special case for web alone. All thresholds live in the category TOML, not in code.

### D9 — Categories as data: **one TOML per category, loaded at runtime**

Locked. `src/runectl/categories/{pwn,web,crypto,forensics,rev,misc,osint,network}.toml`, each containing: execution brief, playbook text, required-tool list, tactic-family patterns, low/high-signal patterns, budgets, step limit, default network mode. Sourced from `PROMPT_ARCHIVE.md` **[DURABLE]**/**[EARNED]** material, re-edited — not pasted wholesale. Adding a category is a data file; it is never a code change. The eight `[STALE]` step limits are carried as *starting* values explicitly marked unmeasured, and `runectl bench` is what tunes them.

### D10 — Pre-LLM work: **deterministic triage only, and it never sees the challenge identity**

Locked, and it is the structural guarantee behind requirement 15 (no answer keys).

- Exactly one code path runs before the first paid call: `triage(sandbox, category) -> TriageResult`. It runs a fixed, category-parameterized command set (`ls -la`, `file *`, `strings` head, `exiftool` for forensics, `checksec` for pwn, …) and returns structured findings.
- **`triage()` does not receive the challenge name, filenames-as-conditions, or the description.** It cannot branch on them, so a fast-path keyed to `logs.txt` or `REChallenge1.zip` is not something you have to remember not to write — there is nowhere to put it.
- There is no plugin hook, no "helper," and no pre-LLM solver of any kind. Techniques like PNG-LSB extraction exist only as tools in the arena image that the agent chooses to invoke.
- A test asserts the triage command set is identical across two runs whose only difference is challenge name and filenames.

### D11 — Flag policy: **candidate by default, corroboration-gated auto, explicit off**

Locked. `--approval` takes three values:

- `gated` (**default**): a candidate is auto-finalized only if it matches the user-supplied `--flag-format` regex **and** is corroborated by ≥2 independent observations **and** passes plausibility filtering (no placeholders like `picoCTF{flag}`, no GUIDs, no JSON fragments, no capture-interface IDs) **and** passes the verification double-check pass (D15). Otherwise the run exits **2** with the candidate in the trace, and a human or driving agent finalizes via `runectl flag approve`.
- `strict`: never auto-finalize; always exit 2 with candidates ranked.
- `auto`: finalize the top plausible candidate. For live competition speed, at the user's own risk.

`gated` is the default because the CLI is meant to be AI-driven — pure human-in-the-loop can't be the only mode — while an uncorroborated guess still never gets submitted silently. This is revisitable once the `REBUILD_NOTES.md` §4 questionnaire is answered; it is the one default here most likely to move.

### D12 — Context management: **summarize, don't truncate; one limit**

Locked. One configurable `context.tool_output_limit` (default 6000 chars) with no second dead constant. Over the limit, the output is written to `artifacts/` and an extractive summarizer (deterministic first: head + tail + regex-salient lines; LLM summarization only if still over) produces what enters context. Identical tool output is deduplicated to a back-reference by digest (preserve item 9). History compaction runs on a token-budget trigger, not "every 15 steps," and goes through the standard provider path (D5).

### D13 — CLI-only, permanently: **no GUI, no `serve`, no `ui/`, ever**

Locked, and rewritten 2026-09-05 from an earlier draft that planned a GUI as a later consumer. There is no GUI in this product's future — the CLI is the whole product, permanently. This is a stronger statement than "no GUI yet": it forecloses `idalia serve`/`runectl serve`, an SSE tailer, and a separate `ui/` package as things to ever build.

- The event-stream/renderer split (D4) is kept anyway, but purely for testability and for the stdout-NDJSON / stderr-human duality — not as a seam for a future front end.
- The one retained boundary: only `cli/` renders. Core code (`trace/`, `sandbox/`, `providers/`, `loop/`, `tools/`, `progress/`, `flags/`, `categories/`) emits structured events and never prints — this keeps the loop testable and scriptable, which is a CLI concern in its own right, not a GUI-readiness concern.
- No import-linter GUI boundary is needed because there is nothing on the other side of it to protect against.

### D14 — Category depth: **all eight equal, no earner-first order**

Locked, rewritten 2026-09-05 from an earlier draft that fixed a deepening order (web → forensics → crypto → rev → pwn → network → misc → osint). That sequencing is dropped: **no category is tuned ahead of the others.** Shared defaults (D8) and full category data (D9) for all eight ship together at V1; per-category *improvement* beyond the shared defaults is a later, uniform effort applied to every category at once, not a queue.

Web's old heuristics remain the only evidence-backed starting point (`REBUILD_NOTES.md` §3, coverage problem), so they inform the *shape* of the shared defaults every category gets — that is a statement about where the starting numbers come from, not a license to keep tuning web first. Revisit only if the §4 questionnaire (question 7, "categories worth the tokens") gives a reason to cut a category for V1 entirely — that is a scope question, not a sequencing one.

### D15 — False-flag defense: **a first-class subsystem, not a side effect of plausibility filtering**

Locked. False flags are the #1 product risk — a wrong flag scores worse than no flag, and the V1 bench gate is 2/5 solved with **0 false flags**. Five mechanisms, all landing in M6 (post-skeleton); the skeleton's trace and `submit_flag` signature carry the fields they need so M6 slots in without an API change:

1. **Provenance is mandatory.** `submit_flag(flag, how_found, provenance)` — `provenance` names the trace `seq` and artifact the flag came from. The judge re-reads that event and confirms the flag literally appears there. A flag the agent cannot point to in its own trace is rejected. This kills invented strings structurally, not just probabilistically.
2. **Verification double-check pass.** A cleared candidate is not submitted yet: the judge re-derives it deterministically first — re-run the cited command in the sandbox and confirm the same string reappears, independently re-decode a decode chain, check `--flag-format`. LLM verification (on the standard costed provider path, D5, framed to *disconfirm* rather than confirm) is a last resort only. Under `gated` (D11), only a re-derived candidate is eligible.
3. **Decoy detection.** Reject or penalize a candidate whose source carries decoy markers: a path/file named `decoy`/`fake`/`honey`/`not_the_flag`, nearby text like "nice try" / "this is not the flag", or a token that appeared verbatim in the pasted challenge description (a planted lure the agent should not echo back as its own finding).
4. **Independent corroboration ≥2** — already in D11, restated here as part of the named subsystem: two *different* tool calls with *different* fingerprints (D8) must produce the same candidate.
5. **No-flag-is-success.** Stated plainly as a base rule the agent sees: no flag with solid, cited evidence is a correct outcome; an unsupported guess is a failure. A run exits **3** rather than fabricate. This is the explicit counterweight to "act, don't ask" (an earned rule from the predecessor, `REBUILD_NOTES.md` §2 item 4) which, alone, biases the model toward answering.

*Skeleton seam:* M4 ships a minimal `submit_flag`/judge that already takes `provenance` and emits `flag.candidate` / `flag.decision` events, but does not yet implement verification, decoy detection, or corroboration counting — those are M6.

### D16 — Budgets measure **step *waste*, not step count**

Locked. A run is not failing because it took many steps; it is failing because it took steps that produced no new signal. Step *limits* (D9 category TOML, `PROMPT_ARCHIVE.md` §6 starting values) are a backstop, not the primary metric. The primary metric is the **progress ratio**: `progress_steps / steps_used`, with `blocked_steps` (steps rejected by a budget, D8) tracked separately and reported in `run.json`. Budgets (D8) are the mechanism that acts on step waste; this decision is the framing that says *why* they exist and what `runectl bench` (M8) should optimize toward — a high solve rate with a low waste ratio, not merely "fewer steps." As real run data comes in (`REBUILD_NOTES.md` §4), limits come down; they do not go up.

*Skeleton seam:* M4's `RunState` (D6) already carries `steps_used` as a field; `progress_steps`/`blocked_steps` are real fields but computed by a no-op progress stub (M5 fills in real scoring) so `run.json` has the shape from day one even before the numbers mean anything.

### D17 — First-run arena setup: **check always, remediate explicitly, never prompt inside a run**

Added 2026-09-07. D2 established that the arena image is built once and that runs "gate on the image existing and fail with a clear message, never a silent build." That held, but it left a bad first-run experience: on a fresh machine the only remedy was a 15-40 minute network build, there was no way to hand `runectl` an image you already had, and nothing noticed when an existing image predated a Dockerfile change. D17 fills that in without weakening D2 or D4.

- **Presence and provenance are checked before every run**, in a preflight that runs *before* the run directory is created and before any paid call. A missing image exits **4** with the full list of remedies. An unreachable daemon is a distinct message from a missing image.
- **`build` stamps a fingerprint.** `docker build` applies `dev.idalia.runectl.arena-fingerprint=<sha256 of the Dockerfile, 16 hex>` as a label. `inspect()` compares it to the Dockerfile in the tree, so "your arena is older than your checkout" is detectable after an update. A **stale image warns and proceeds** — it still works, it is merely old. An image with **no** label is "unknown provenance," never "stale": an image built before stamping existed must not start nagging.
- **Three remedies, all non-interactive by flag**: `runectl arena ensure --build` (build here), `--from-file PATH` (load a `docker save` tarball — the offline/air-gapped/USB-stick path, and how a prebuilt arena moves between machines), `--from-registry REF` (pull and tag a prebuilt image). `load_archive` re-tags an archive that carried a different name rather than making the user work out `docker tag`.
- **`runectl arena ensure` with no flags on a TTY is the one interactive surface in the product.** It asks which route the user wants. With no flags and no TTY it prints the remedies and exits 4 — it can never block a script or an agent.
- **`runectl run` still never prompts and never silently builds** (D2, D4 intact). It reports and exits; the human runs `arena ensure`. An agent driving the tool reads the same message and runs the same non-interactive command.

*Not decided here:* whether Idalia publishes a prebuilt `runectl/arena:kali` to a registry. `--from-registry` accepts any reference, so publishing later is a distribution decision, not a code change.

---

## Deliberately not decided yet

- **License.** Parked (`CLAUDE.md`). `solver/` ships with no license file until it's decided; nothing goes public before then.
- **Step-limit and budget numbers.** Carried from `PROMPT_ARCHIVE.md` §6 as marked-unmeasured starting values. `runectl bench` tunes them; do not treat them as tuned.
- **Anything about hosting, accounts, or billing.** Out of scope for V1 (`REBUILD_NOTES.md` §6).

## Assumptions I made in the user's absence

Recorded so they're cheap to overturn:

1. ~~Anthropic is the primary provider and OpenAI the secondary~~ — superseded 2026-09-05: Anthropic, OpenAI, and Google all ship in V1 with no primary; the product promise is BYO-any-key, equally. A fourth provider is a registry entry plus an adapter, not a redesign.
2. Runs are single-challenge and single-threaded. Parallelism is a V2 concern and no V1 decision blocks it.
3. The target machine is the founder's laptop with Docker Desktop. No remote/cloud sandbox in V1.
4. `gated` (D11) is the right default; the §4 questionnaire may move it to `auto` for live comps.
