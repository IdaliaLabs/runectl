# Status

Last updated **2026-09-13**, the day both remaining providers were pointed at a live service
for the first time — Google in the morning, OpenAI in the afternoon — and eight defects fell
out between them. Those entries are under *Live verification* and are the most consequential
part of this file.

M0–M9 are built, typed and green: 320 tests passing, `mypy --strict` clean, `ruff` clean.
This file states what that does and does not cover, so that nothing here surprises a reader
at run time.

## Verified by test

| Behavior | Verified by |
|---|---|
| The full loop solves a challenge end to end | `tests/integration/test_walking_skeleton.py`, via `StubSandbox` + `ScriptedProvider` |
| The trace survives a process restart | Same test: reopen the store fresh, manifest and events durable |
| `runectl replay --check` reproduces a run at zero spend | Same test, running the real CLI as a subprocess |
| `runectl trace show --format jsonl` emits parseable events | Same test |
| Triage cannot see challenge identity | `tests/unit/test_triage.py` — identical command sets across differing challenges |
| Provider retry/backoff on transient errors | `tests/unit/test_provider_retry.py` |
| Cassette record → replay round-trip | `tests/unit/test_providers_replay.py` |
| The progress ratio reaches `run.json`, not just the trace | `tests/integration/test_walking_skeleton.py` |
| History compaction triggers, never orphans a tool result, never re-summarizes its own summary | `tests/unit/test_context_compaction.py` |
| Category TOMLs validate | `tests/unit/test_categories.py` |
| The judge rejects a flag with no provenance | `tests/unit/test_flags_judge.py` |
| The judge rejects the agent echoing its own guess back | `tests/unit/test_flag_laundering.py` — replayed against the real trace that did it |
| All five D15 mechanisms; `--approval auto` cannot submit an invented or planted flag | `tests/unit/test_flags_judge_m6.py` |
| An uncorroborated find exits 2; `flag approve` finalizes without rewriting the judge's decision | `tests/integration/test_pending_candidate_approval.py` |
| Tactic classification, fingerprint normalization, signal scoring, budgets, forced shifts | `tests/unit/test_progress_*.py` |
| Bench scoring, false-flag/held distinction, suite cost ceiling, gate arithmetic | `tests/unit/test_bench_suite.py`, `tests/integration/test_bench_command.py` |
| Every vendored challenge loads, its files resolve, none carries its own answer | `tests/unit/test_bench_challenges.py` |
| Core code never prints | `tests/unit/test_render_boundary.py` |
| Tool dispatch, empty-command and path-traversal rejection | `tests/unit/test_dispatch.py` |
| Secrets redacted, >8 KB values spilled, torn tail tolerated, index rebuild | `tests/unit/test_trace.py` |
| Utility-summarizer tokens land in the shared cost ledger | `tests/unit/test_utility_summarizer.py` |
| Constructing a `DockerSandbox` never touches the daemon; a daemon failure surfaces as `SandboxError` | `tests/unit/test_docker_sandbox.py`, against a mocked client |
| Arena presence/staleness detection, fingerprint stamping, tarball load and re-tag | `tests/unit/test_arena.py`, against a fake client and stubbed `subprocess` |
| The arena is pinned to amd64 on build and run; an arm64 image is flagged | `tests/unit/test_arena.py`, `tests/unit/test_docker_sandbox.py` |
| Containers are named `runectl-<run_id>` so a human can attach mid-run | `tests/unit/test_docker_sandbox.py` |
| No category claims a tool the arena Dockerfile never installs | `tests/unit/test_categories.py` |
| A missing image or dead daemon exits 4 before any run directory is created | By hand, 2026-09-07, daemon stopped |
| Thinking resolution/clamping per model, round-trip of provider-native thinking blocks, `llm.thinking` truncation at 8 KB, `run.started`'s clamp record | `tests/unit/test_thinking.py` (D20) |
| A cassette recorded with thinking off is never served to a thinking-on replay | `tests/unit/test_thinking.py::test_request_hash_differs_by_thinking_level` |
| `runectl config`'s key-shaped-value rejection and round-trip | `tests/unit/test_thinking.py::test_user_config_round_trips_and_rejects_key_shaped_values` |
| The TUI mounts, its launcher modal opens/composes/dismisses, a live run's events update the run table | `tests/integration/test_tui.py`, against a stub script standing in for `runectl run` |
| Every registry row is internally coherent: provider valid, thinking flags consistent, prices positive, cache multiplier in range, a utility model resolvable per provider | `tests/unit/test_registry.py`, parametrized over all 37 rows |
| `--thinking off` resolves to `low` with the clamp recorded on every think-by-default model, and stays `off` where it can be honored | `tests/unit/test_thinking.py`, including a sweep over every registry row |
| Cached tokens leave `input_tokens` and land in `cache_read_tokens` on all three adapters; Gemini thinking tokens count as output; `cache_read_multiplier` applies per model | `tests/unit/test_cost_accounting.py` (fake usage objects, not mocks) |
| The TUI splash shows by default, dismisses on any key, is suppressed under `--replay` and `splash=False`; a `cost.updated` event moves a row before it finishes; the timeline formats to the pane's real width; model dropdowns lead with each provider's cheapest | `tests/integration/test_tui.py` |
| Run-wide `--approval` and `--max-cost` defaults resolve config → flag → built-in, in that precedence | `tests/unit/test_run_defaults.py` |
| Every advertised CLI action has a TUI equivalent | `tests/unit/test_cli_tui_parity.py` |

## Live verification

### 2026-09-13 — Google adapter, first live exercise

A live Gemini key ran `bench/practice/easy-02` end to end: 39 steps, thinking captured per
step, tool calls dispatched into the sandbox, results returned, cost accumulating correctly
to $0.1579. It did not solve the challenge (cheap model, free tier), but the adapter, the
D20 thinking round-trip, the cost ledger and the artifact spill path all work against a live
service. Four defects surfaced, none caught by any test, none catchable without a real call:

- **Three registry rows were dead.** `gemini-2.5-pro`, `gemini-2.5-flash` and
  `gemini-2.5-flash-lite` are closed to new accounts and return 404. The last was both the
  README's recommended cheap Google pick and `cheapest_model_for("google")` — the default
  utility model for any Google user. Now marked retired, excluded from defaults, flagged in
  `models list`. `models.list()` still returns all three: a listing is not an availability
  signal, only a call is.
- **Gemini rejected every second turn.** It attaches a `thought_signature` to each
  `functionCall` part and requires it back verbatim; the adapter dropped it, so any run with
  a tool call died at step 2 with a 400. D20 already required thinking state to round-trip
  per each provider's contract; this is that contract's Google shape, now carried on
  `ToolCallRequest.provider_signature`.
- **A tool output over 8 KB broke reading the trace.** Surfaced here, not a Google problem —
  see the spill entry below.
- **Rate-limit backoff ignored the provider's stated delay.** Gemini's free tier asks for
  ~35 s; four exponential retries total about seven, so a limit about to lift looked like a
  hard failure. `TransientProviderError` now carries `retry_after`.

### 2026-09-13 — OpenAI adapter, first live exercise

All 18 registry rows called for real with a function tool attached, then a full 40-step run
of `bench/practice/easy-03` on `gpt-5-nano`: exit 3 (exhausted, honest), $0.0137, 15 steps
with progress, tool calls dispatched and results returned. It did not solve the challenge.
Prompt caching was verified separately against a real cache hit: a 4,571-token prompt
returned 4,352 cached + 219 uncached, which is the split D18 prices. Four more defects:

- **No OpenAI model accepts `--thinking max`.** Eight rows claimed
  `max_thinking_level="max"`, so `resolve_thinking_level` passed it through and the call
  400'd. The real ceiling is `xhigh`, or `high` on `gpt-5.1` and the original `gpt-5` family.
- **The `gpt-5` family refuses `reasoning_effort: "none"`** — the opposite of what the
  registry comment asserted. The adapter also keyed that value on `supports_thinking` rather
  than `thinking_off_supported`, so the clamp only protected callers going through
  `resolve_thinking_level`. The utility summarizer does not: a healthy `gpt-5-nano` run died
  at step 18 on a context-compaction call. Both facts are now separate fields and both reach
  the adapter.
- **Four models cannot be used at all.** `gpt-6-astra` and the `gpt-5.6` family reject
  function tools on `v1/chat/completions` at every reasoning setting, including omitting the
  setting, since their own default effort is what collides. Every `runectl` step sends tools.
  `gpt-5.6-luna` was the README's recommended cheap OpenAI pick. Now retired with a stated
  reason; `gpt-5.4*` and `gpt-5.5` continue to work as non-thinking models.
- **Cache tokens never reached the trace.** The ledger had tracked them since D18, but
  `cost.updated` and `llm.response` carried only uncached counts, so a run that was 95% cache
  hits left no record of it and `cost_usd` could not be re-derived. Both events now carry the
  split.

Moving the adapter to `v1/responses` would lift the tools/reasoning restriction entirely and
is the obvious next call; it is also the only route to OpenAI reasoning text, which Chat
Completions never returns.

**Implication for this file.** All three adapters have now made real calls, and all three had
defects the moment they did, in code that was typed, linted and unit-tested throughout. Live
verification was the only kind that counted. Still uncovered: no OpenAI or Google run has
solved a challenge, so end-to-end prompt behavior on those providers is unmeasured, and every
bench number below is Anthropic-only.

### 2026-09-13 — a spilled tool output silently truncated the trace

The writer moves any string over 8 KB into `artifacts/` and leaves a reference behind (D3).
Nothing resolved it on the way back: `Event.payload()` raised on the reference, the live
renderer crashed mid-run, and `TraceReader` mistook the same error for a torn final line — so
`trace show`, `replay` and the TUI stopped at the first large output and silently dropped
everything after it. The reader had held an `artifacts_dir` it never read. Both halves fixed;
regression in `tests/unit/test_artifact_spill.py`.

### 2026-09-12 — an attempted re-bench

A ten-case suite was launched against `claude-sonnet-5` after the thinking fix. It produced
no score: the Anthropic account was out of credit, so all ten runs failed at step 1 for
$0.00. Three things were confirmed anyway, none of which a test could show:

- **The D20 thinking clamp engages on a real run.** Every header read
  `thinking=low (clamped from off)`, against the live API rather than
  `resolve_thinking_level` in isolation.
- **A provider failure exits cleanly,** with the API's own message surfaced and no traceback.
- **The exit code was wrong,** now fixed. Each run exited 5 ("provider failure after
  retries"), which tells a caller a retry might help. Nothing would have; the account was out
  of money. Billing failures now exit 6, the same code a bad key gets, because they are
  actionable by a person and never by a retry. Regression:
  `tests/unit/test_provider_errors.py`.

### Earlier live sessions

- **2026-09-07** — `arena build` and `arena status` on an M3 MacBook Air (amd64 image), three
  live Anthropic runs, and `replay --check`.
- **2026-09-09** — arena rebuilt for M7's toolset; a full ten-challenge `runectl bench run`
  against Anthropic ([`../bench/results/README.md`](../bench/results/README.md)).
- **2026-09-09 (M9)** — arena rebuilt again (current fingerprint), then one live
  `runectl run --thinking high --record` against Anthropic (`bench/practice/easy-02`,
  `modern-clueless-child`): solved in 6 steps, $0.0558, 5 `llm.thinking` events with
  non-empty reasoning text, confirming `display: "summarized"` works — the API default,
  `"omitted"`, returns empty thinking blocks while still billing for them. `run.json`
  recorded `thinking_level: "high"`. `replay --check` still reproduced the tool-call sequence
  at zero spend after D20's changes to `request_hash`.

Exercised for real as a result: `arena build`/`arena status`; `DockerSandbox` building and
running real containers (triage and agent commands execute inside the amd64 arena); and the
Anthropic adapter against the live API, including error handling (a rejected key exits 6
cleanly, an invalid request exits 5, verified 2026-09-09) and extended thinking end to end
(adaptive thinking + `output_config.effort`, never `budget_tokens`, which Opus 5 and Sonnet 5
reject outright). The container's security posture is applied and functional, but its
isolation is asserted by configuration, not by adversarial testing — see
[`../SECURITY.md`](../SECURITY.md).

### 2026-09-10 — a replay defect that looked like a passing check

`runectl replay`'s regenerated run finished as `candidate` (exit 2) rather than reproducing
the original's `solved` (exit 0), while `--check` confirmed an identical tool-call sequence
and reported OK.

Cause: the D15 judge re-derives a cited command by calling `self._sandbox.exec(...)`
directly, bypassing `ToolDispatcher`, so that exec was never written to the trace. Since
`ReplaySandbox` serves recorded exec results positionally, the unrecorded call consumed the
next tool call's output and shifted every result after it. Any run that auto-finalized via
re-derivation — the primary path under `gated` since the D11 amendments — hit it.

Fixed by recording the re-derivation as a `flag.rederived` event (D3 amended, 19 → 20 event
types) rather than by special-casing replay: the defect was an incomplete trace, and the
replay desync was its symptom. `runectl replay --check` now asserts the outcome as well as
the sequence, since comparing only the sequence is what let this hide for a milestone.
Regression gate: `tests/integration/test_replay_fidelity.py`, which fails on four separate
assertions if the event is removed.

## Never run for real

Each of these needs a first smoke test before it is relied on.

- **`arena ensure`'s load and pull paths** (`--from-file`, `--from-registry`). Only `build`
  has run. The `docker load` / `docker pull` argument construction is tested against a
  stubbed `subprocess` but has never moved a real image. The daemon-down and image-missing
  paths were exercised by hand.
- **Every registry row's price.** All 37 rows have been called for real (Google and OpenAI on
  2026-09-13), so ids, endpoint support and thinking capability are observed facts rather
  than documentation claims — and the observation corrected a dozen of them. Prices cannot be
  checked this way: a wrong price does not fail, it reports a wrong number. Each provider
  block in `providers/registry.py` carries the dated page it was read from. Those are the
  weakest claims in the file.
- **A completed solve on OpenAI or Google.** Both adapters drive the loop correctly end to
  end, but neither has solved a challenge; both live runs exhausted their step budget on
  cheap models. Every published bench number is Anthropic-only, and nothing here measures how
  well the prompts work elsewhere.
- **OpenAI reasoning text.** Chat Completions has no reasoning-content field, so
  `Completion.thinking_text` is always empty for that adapter even when a level was honored
  and billed server-side. Confirmed live. Rendering it requires `v1/responses`.
- **The TUI's live-run path** — launching a real `runectl run` subprocess from the launcher
  modal, watching multiple runs concurrently, approving a flag through it. The
  subprocess/NDJSON mechanism (`runner_proc.run_streaming`) is unit-tested against a stub
  script, and the app's reaction to a simulated live run is tested the same way
  (`tests/integration/test_tui.py`), but no session has driven it against a real daemon and a
  real key. `runectl tui --replay <run_id>` has been verified live against the recorded
  thinking-enabled run above.

## Stubs — wired, discoverable, no body yet

| Surface | Behavior today | Lands in |
|---|---|---|
| Evidence store | No durable finding record; findings carry forward in the conversation only. The unemitted `evidence.added` event was removed from the schema 2026-09-09 (D3 amendment) | later |

## What the judge does

Six stages in order. The order is load-bearing: cheap deterministic rejections run before
anything that costs sandbox time.

1. **Plausibility.** Placeholders (`picoCTF{flag}`), UUIDs, JSON fragments, capture-interface
   ids, multi-line strings. Rejected.
2. **Provenance (D15 §1).** The flag must appear in a tool result this run observed, that
   result must not have come from a command the agent wrote the flag into, and the agent must
   cite the observation by `seq`. Every tool result reaches the model with an
   `[observation seq=N]` header. What must appear is the flag's *payload* — the part inside
   the wrapper — since the wrapper is published in the challenge (amended 2026-09-08). A
   payload sighting counts only if the wrapper's prefix is attested by the description or
   `--flag-format` and the payload is at least 8 characters; an invented prefix still
   requires the whole string. Anti-echo runs on the payload, so an agent that types its own
   answer into a command is still caught.
3. **Decoy detection (D15 §3).** A bait-named source, a taunt next to the hit, or a token the
   author pasted into the description. Rejected.
4. **Corroboration (D15 §4).** How many observations with different commands and different
   output fingerprints produced this string. Reported, not gating, since the 2026-09-08 D11
   amendment.
5. **Flag format.** Matched against `--flag-format` when one was given.
6. **Re-derivation (D15 §2).** The cited command is re-run in the sandbox and must produce
   the same string again.
7. ~~**Disconfirmation review (D15 §2).**~~ **Removed 2026-09-10.** A call to the cheapest
   model of the same provider (~$0.002), framed to find a reason the flag was wrong, ran last
   on every candidate from 2026-09-08. It was advisory the day it shipped — recorded and
   printed, deciding nothing — and `_apply_policy` never read its verdict. Its record over
   nine live reviews: six correct clears, two wrong flags cleared, one correct flag held (it
   cannot see a challenge's attached files), zero caught. Deleted outright rather than left
   advisory, because an unread verdict printed beside every candidate read as a judgment it
   never was. See the D11/D15 amendments in [`ARCHITECTURE.md`](ARCHITECTURE.md).

Stages 1–3 reject under every `--approval` policy. Stages 5–6 decide whether a candidate can
finalize without a human: under `gated` both must pass, otherwise the run ends at exit 2 with
the candidate in the trace for `runectl flag approve`. Stage 4 decides nothing. The gating set
is named explicitly as `_GATING_CHECKS` in `flags/judge.py` so it cannot drift without
someone editing that line.

Both mechanisms removed from the gate so far — corroboration, then the review — were forms of
model judgement, and each was satisfiable or fooled by the model it judged. What holds is the
sandbox: provenance says the string came from a tool's output rather than the agent's own
command, and re-derivation says the tool produces it again.

A rejection is feedback, not failure: it returns to the model as a tool message and the run
continues. No flag with evidence beats a wrong flag (D15 §5).

## Known gaps

**Context compaction triggers on message count, not tokens.**
`ContextBuilder.maybe_compact` runs before every request and compacts once history passes
`DEFAULT_MAX_HISTORY_MESSAGES` (40). That threshold stands in for a real per-provider token
budget; a long run with large individual messages can still approach the context window
before the count trips. Accurate tokenization is a later refinement.

**`runectl bench` is a subcommand group, not a bare command.** It is `runectl bench run
--suite ...`, which diverges from [`ARCHITECTURE.md`](ARCHITECTURE.md)'s D4 sketch.

**`--dry-run` from the D4 sketch does not exist.** No flag, no code path.

**Three categories cannot be measured in the gate.** Ten challenges are vendored or authored
([`../bench/README.md`](../bench/README.md)); the gated subset covers crypto, misc, rev,
forensics and network, so a solve rate is no longer a statement about cryptography alone.
`web` needs a live service the offline sandbox cannot host; `pwn` and `osint` are present but
scored outside the gate (pwn: a local flag file the agent can read directly; osint: an answer
in rotted live-internet state).

**Bench scores do not agree with each other.** The gate was met on 2026-09-08 (3 solved / 0
false over 4 gated, older five-challenge suite) and on 2026-09-09 (7/10, 6 solved / 0 false
over 7 gated). It was missed on 2026-09-13 (6/10, 5 solved / 1 false), the first run against
current code.

The reading is not that something regressed. Repeating the case that broke the gate,
`esrever`, eight times on the same code returned five correct and one false at the default
setting, and one of each at `--thinking high`: roughly a one-in-six false flag, with a
different wrong answer each time. A gate demanding *zero* false flags therefore cannot be
established by one suite run, because a single run of a stochastic suite passes or fails
partly on luck. Earlier versions of this file and of the README asserted "the V1 gate is met"
as a property of the tool. It is a property of a sample. That was overclaiming, corrected here
rather than quietly restated.

What the gate results do support is narrower: on the cases it can fairly judge, the
false-flag subsystem catches what it was built to catch. What it does not catch is a
derivation that is internally consistent and wrong — `quick-math` and `esrever` both
re-derived cleanly while being wrong, because re-derivation proves provenance, not
correctness.

**`quick-math` is the excluded case,** excluded for a stated reason rather than for being
hard: its run performs the Håstad broadcast attack correctly and submits the recovered value
one transformation short of the flag, so provenance, decoys, re-derivation and the
disconfirmation review all agreed with it — correctly. No mechanism this subsystem could add
would catch that. It still runs, still counts in the headline numbers, and prints its
exclusion reason beside its result; only the pass/fail gate reads the narrower subset.

**The one held candidate on the third run held the correct flag,** and the reviewer said why:
it could not see the challenge's `code.py`, because the review prompt receives the
description, the cited command and its output and not the attached files. That is a blind
spot, not a defect in the answer, and it is unfixed pending a decision on the cost of
widening the review prompt.

**First live bench score, for the record: 0 solved, 1 false flag**
([`../bench/results/README.md`](../bench/results/README.md), 2026-09-08, claude-sonnet-5,
$0.38). The headline number understates it: the tool produced the correct flag in four of
five challenges and held all four for approval, while the one it finalized was wrong. Both
halves trace to D11's corroboration rule, which on this evidence cost four solves and
prevented zero false flags. That is one run, not a mandate to change a locked decision.

**Budgets remain unmeasured.** Zero steps have been blocked across any of the three suites —
runs take 4–16 steps against limits of 60–80 — so the per-family and per-hypothesis budgets
have never bound anything. Nothing here justifies moving a step limit. A forced strategy
shift has fired on a live run (`20260909-011822-4c708c`, three failed commands in a row) and
the run recovered and solved, which is the first evidence that half of D8 does anything
outside a test.

**A per-run spend cap was misread once, and the correction matters more than the reading.**
The second bench blamed `machine-fix`'s failure on the $0.15 ceiling. The third solved the
same challenge for $0.0377 after a provenance change — a quarter of that cap. The cap was
where a doomed search happened to stop, not the thing stopping it. "It ran out of budget" is a
hypothesis to check against a trace, not a conclusion.

**`easy-01` is a known-unfair gate.** It was the first vendored challenge and its description
does not contain enough to solve it without the original repo's file layout; `easy-02`
replaced it as the case real runs are measured against.

## Milestones

| Milestone | State | Contents |
|---|---|---|
| M0–M4 | done | Repo skeleton, trace/store, sandbox layer, provider layer, walking-skeleton loop |
| M5 | done | Tactic families, output fingerprinting, signal scoring, per-family and per-hypothesis budgets, forced strategy shifts — makes the D16 progress ratio mean something |
| M6 | done | Mandatory citable provenance, verification re-derivation, decoy detection, corroboration ≥2, `--approval` policy, exit code 2, `flag list` / `flag approve` |
| M8 | done | `runectl bench run`: scores a suite against `expected.json`, reports solve rate with D16's progress-waste ratio, bounds spend per run and per suite, fails outright on a finalized wrong flag. Built before M7 so remaining categories could be measured as they landed |
| M7 | done 2026-09-09 | `pwn`, `rev`, `forensics`, `osint`, `network` ship as data at equal depth; the arena grew their toolset; the bench grew from 5 to 10 cases |
| M9 | done 2026-09-09 | Extended thinking (D20) as its own trace event, rendered live; `runectl config`/`models`/`runs`; and `runectl tui`, added to D13 by dated amendment — no server, no port, every run it launches a plain non-interactive `runectl run` subprocess, and `loop/runner.py` gained no threading to support it |

Ordering note: M5 and M6 were both prerequisites for trusting a solve, and M6 addresses the
product's largest risk, since a wrong flag scores worse than no flag. Neither measured
anything: every budget number and step limit in the category TOMLs remains an unmeasured
starting value (D16), and `runectl bench` is what earns the right to change them.
