# Status

Last updated **2026-09-10** (the replay-fidelity fix below, and `--approval`
validation). The M9 entries — thinking capture, `config`/`models`/`runs`, and the TUI —
landed 2026-09-09, the same day as the M7/M8 entries this file already described.

M0–M9 are built, typed, and green: 240 tests passing, `mypy --strict` clean,
`ruff` clean. What that means precisely — and what it does *not* mean — is below. The
point of this file is that nothing here should surprise you at run time.

## Verified

| Thing | How it was verified |
|---|---|
| The full loop solves a challenge end to end | `tests/integration/test_walking_skeleton.py`, via `StubSandbox` + `ScriptedProvider` |
| The trace survives a process restart | Same test: reopen the store fresh, manifest and events are durable |
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
| All five D15 mechanisms, and that `--approval auto` still can't submit an invented or planted flag | `tests/unit/test_flags_judge_m6.py` |
| An uncorroborated find exits 2, and `flag approve` finalizes it without rewriting the judge's decision | `tests/integration/test_pending_candidate_approval.py` |
| Tactic classification, fingerprint normalization, signal scoring, budgets, forced shifts | `tests/unit/test_progress_*.py` |
| Bench scoring, the false-flag/held distinction, suite cost ceiling, gate arithmetic | `tests/unit/test_bench_suite.py`, `tests/integration/test_bench_command.py` |
| Every vendored challenge loads, its files resolve, and none carries its own answer | `tests/unit/test_bench_challenges.py` |
| Core code never prints | `tests/unit/test_render_boundary.py` |
| Tool dispatch, empty-command and path-traversal rejection | `tests/unit/test_dispatch.py` |
| Secrets redacted, >8KB values spilled, torn tail tolerated, index rebuild | `tests/unit/test_trace.py` |
| Utility-summarizer tokens land in the shared cost ledger | `tests/unit/test_utility_summarizer.py` |
| Constructing a `DockerSandbox` never touches the daemon, and a daemon failure surfaces as `SandboxError` | `tests/unit/test_docker_sandbox.py` — against a mocked client |
| Arena presence/staleness detection, fingerprint stamping, tarball load and re-tag | `tests/unit/test_arena.py` — against a fake client and stubbed `subprocess` |
| The arena is pinned to amd64 on both build and run, and an arm64 image is flagged | `tests/unit/test_arena.py`, `tests/unit/test_docker_sandbox.py` |
| Containers are named `runectl-<run_id>` so a human can attach mid-run | `tests/unit/test_docker_sandbox.py` |
| No category claims a tool the arena Dockerfile never installs | `tests/unit/test_categories.py` |
| A missing image or dead daemon exits 4 before any run directory is created | Verified by hand on 2026-09-07 with the daemon stopped |
| Thinking resolution/clamping per model, round-trip of provider-native thinking blocks, `llm.thinking` truncation at 8KB, `run.started`'s clamp record | `tests/unit/test_thinking.py` (D20) |
| A cassette recorded with thinking off is never served to a thinking-on replay | `tests/unit/test_thinking.py::test_request_hash_differs_by_thinking_level` |
| `runectl config`'s key-shaped-value rejection and round-trip | `tests/unit/test_thinking.py::test_user_config_round_trips_and_rejects_key_shaped_values` |
| The TUI mounts, its launcher modal opens/composes/dismisses, and a live run's events update the run table | `tests/integration/test_tui.py`, against a stub script standing in for `runectl run` — no real subprocess of the engine, no daemon, no key |
| Every registry row is internally coherent: provider valid, thinking flags consistent, prices positive, cache multiplier in range, a utility model resolvable per provider | `tests/unit/test_registry.py`, parametrized over all 37 rows |
| `--thinking off` resolves to `low` **with the clamp recorded** on every think-by-default model, and stays `off` on models that can actually be stopped | `tests/unit/test_thinking.py` — including a sweep that holds every registry row to the rule, so a new row cannot silently reintroduce the bug |
| Cached tokens leave `input_tokens` and land in `cache_read_tokens` on all three adapters; Gemini's thinking tokens are counted as output; `cache_read_multiplier` is applied per model | `tests/unit/test_cost_accounting.py` (fake usage objects, not mocks) |
| The TUI's splash is shown by default, dismissed by any key, and suppressed under `--replay` and `splash=False`; a `cost.updated` event moves the run's row before it finishes; the timeline formats to the pane's real width rather than a fixed 100; model dropdowns lead with the cheapest model of each provider | `tests/integration/test_tui.py` |

## Live-verified since the skeleton

The build session had no Docker daemon and no API keys, so several paths shipped
unexercised. Two live sessions have since closed most of that gap:

- **2026-09-07** — `arena build` + `arena status` on an M3 MacBook Air (amd64 image),
  three live `runectl run`s against Anthropic, and `replay --check`.
- **2026-09-09** — the arena rebuilt for M7's toolset, and a full ten-challenge
  `runectl bench run` against Anthropic (`bench/results/README.md`).
- **2026-09-09 (M9)** — the arena rebuilt again (current fingerprint), then one live
  `runectl run --thinking high --record` against Anthropic
  (`bench/practice/easy-02`, `modern-clueless-child`): solved in 6 steps, $0.0558, 5
  `llm.thinking` events with real non-empty reasoning text (confirming `display:
  "summarized"` is doing its job — the API's own default, `"omitted"`, would have
  returned empty thinking blocks despite paying for them). `run.json` correctly recorded
  `thinking_level: "high"`. `replay --check` still reproduced the tool-call sequence at
  zero spend after D20's changes to `request_hash`.

Exercised for real as a result: `arena build`/`arena status`; `DockerSandbox` building
and running real containers (triage and agent commands execute inside the amd64 arena);
and the **Anthropic** adapter against the live API — including its error handling (a
rejected key exits 6 cleanly, an invalid request exits 5, verified 2026-09-09) and, as of
the same day, **extended thinking** end to end (adaptive thinking + `output_config.effort`,
never `budget_tokens`, which is rejected outright on Opus 5/Sonnet 5). The container's
security *posture* is applied and functional, but its isolation is asserted by
configuration, not adversarial testing (see [`SECURITY.md`](../SECURITY.md)).

**A real, pre-existing gap this session found — since fixed (2026-09-10).** For the
record, because it is a good example of a check that looked like it was working:
`runectl replay`'s regenerated run finished as `candidate` (exit 2) rather than
reproducing the original's `solved` (exit 0), while `--check` confirmed an identical
tool-call sequence and reported OK.

The cause was that the D15 judge re-derives a cited command by calling
`self._sandbox.exec(...)` directly, bypassing the `ToolDispatcher` path — so that exec was
never written to the trace at all. Since `ReplaySandbox` serves recorded exec results
*positionally*, the unrecorded call consumed the next tool call's output and shifted every
result after it. Any run that auto-finalized via re-derivation — the primary path under
`gated` since the D11 amendments — hit it.

Fixed by recording the re-derivation as a `flag.rederived` event (D3 amended, 19 -> 20
events) rather than by special-casing replay: the defect was that the trace was
incomplete, and the replay desync was its symptom. `runectl replay --check` now asserts
the **outcome** as well as the sequence, since comparing only the sequence is what let
this hide for a milestone. Regression gate: `tests/integration/test_replay_fidelity.py`,
which fails on four separate assertions if the event is removed.

## Still never run for real

**Assume each needs a first smoke test before you trust it.**

- **`arena ensure`'s load / pull paths** (`--from-file`, `--from-registry`). Only `build`
  has actually run; the `docker load` / `docker pull` argument construction is tested
  against a stubbed `subprocess` but has never moved a real image. The daemon-down and
  image-missing paths *were* exercised by hand.
- **The OpenAI and Google adapters.** Each was written against its installed SDK's actual
  types and exception hierarchy, but no OpenAI or Google key has ever talked to the live
  service. Treat the first real run on each as its smoke test. This is not a formality:
  on 2026-09-11 both were found to be dropping cached tokens on the floor — assigning the
  provider's total prompt count to `Usage.input_tokens`, whose contract is uncached input
  only, so every cache hit billed at the full rate — and Google was additionally ignoring
  `thoughts_token_count`, which it bills as output but reports separately. Both are fixed
  and unit-tested against fake usage objects (`tests/unit/test_cost_accounting.py`), and
  **both fixes are themselves unverified against a live service**, same caveat one level
  deeper. Anthropic's adapter was correct throughout, which is exactly why the gap
  survived a milestone: the only provider anyone had run live was the one that worked.

- **35 of the 37 registry rows.** Only `claude-sonnet-5` and `claude-opus-5` have ever
  been used for a real run. Every other row's id, pricing and capability flags come from
  the provider's published documentation on 2026-09-11 (sources and dates are in
  `providers/registry.py` and [`CLI.md`](CLI.md)), not from a call that succeeded. An id
  that has been renamed or retired since, or a model that turns out not to be served by
  `v1/chat/completions`, will fail at the first request with a clean provider error — but
  it will fail. Prices are the more insidious risk, because a wrong one does not fail at
  all; it just reports the wrong number.

- **`--thinking off` on OpenAI and Google.** The 2026-09-11 D20 amendment maps `off` onto
  `reasoning_effort: "none"` for OpenAI reasoning models and clamps to `low` where a model
  thinks regardless. The clamp arithmetic is unit-tested across every registry row; what
  the providers actually do with those requests has not been observed.
- **Thinking on OpenAI and Google (D20).** Written against the installed SDKs'
  documented shapes (`reasoning_effort` on OpenAI's Chat Completions; `ThinkingConfig`
  with `include_thoughts=True` on `google-genai`) and covered by the same
  fakes-over-mocks unit tests as everything else in `providers/`, but neither has run
  against a live service — same caveat as the adapters themselves, one level deeper.
  Two things worth knowing before the first real run: OpenAI's Chat Completions surface
  has no reasoning-content field at all, so `Completion.thinking_text` is always empty
  for that adapter even when a level was honored server-side (a Responses API migration
  would be needed to render it — out of scope here); Google's `ThinkingLevel` enum tops
  out at `HIGH` (no `xhigh`/`max`), which the registry's `max_thinking_level="high"` for
  both Gemini models already reflects.
- **The TUI's live-run path** (launching a real `runectl run` subprocess from the
  launcher modal, watching multiple runs concurrently, approving a flag through it). The
  subprocess/NDJSON-parsing mechanism (`runner_proc.run_streaming`) is unit-tested
  against a stub script standing in for `runectl run`, and the app's reaction to a
  simulated live run is tested the same way (`tests/integration/test_tui.py`) — but no
  session has driven it against a real Docker daemon and a real key. `runectl tui
  --replay <run_id>` (playback over an already-recorded trace) *has* been verified live,
  against the real thinking-enabled run recorded above.

## Stubs — wired, discoverable, no body yet

| Surface | Behavior today | Lands in |
|---|---|---|
| Evidence store | No durable finding record; findings carry forward in the conversation only. The unemitted `evidence.added` event was removed from the schema 2026-09-09 (D3 amendment) | later |

## What the judge actually does

Six stages, in order, and the order is the point — the cheap deterministic rejections run
before anything that costs sandbox time:

1. **Plausibility.** Placeholders (`picoCTF{flag}`), UUIDs, JSON fragments,
   capture-interface ids, multi-line strings. Rejected.
2. **Provenance (D15 §1).** The flag must appear in a tool result this run observed, that
   result must not have come from a command the agent wrote the flag into, and the agent
   must cite the observation by `seq`. Every tool result reaches the model with an
   `[observation seq=N]` header so it can. Rejected otherwise. **What must appear is the
   flag's payload** — the part inside the wrapper — since the wrapper is published in the
   challenge and nobody earns it (amended 2026-09-08). A payload sighting counts only if
   the wrapper's prefix is attested by the description or `--flag-format` and the payload
   is at least 8 characters; an invented prefix still requires the whole string. Anti-echo
   is unchanged and runs on the payload, so an agent that types its own answer into a
   command is still caught.
3. **Decoy detection (D15 §3).** A bait-named source, a taunt next to the hit, or a token
   the author pasted into the description. Rejected.
4. **Corroboration (D15 §4).** How many observations with *different* commands and
   *different* output fingerprints produced this string. **Reported, not gating** since
   the 2026-09-08 D11 amendment.
5. **Flag format.** Matched against `--flag-format` when one was given.
6. **Re-derivation (D15 §2).** The cited command is re-run in the sandbox and must produce
   the same string again.
7. ~~**Disconfirmation review (D15 §2).**~~ **Removed 2026-09-10.** A call to the cheapest
   model of the same provider (~$0.002), framed to find a reason the flag is *wrong*, ran
   last on every candidate from 2026-09-08. It was advisory the same day it shipped —
   recorded in the trace and printed, decided nothing — and stayed that way for its whole
   life: `_apply_policy` never read its verdict. Its record over nine live reviews was six
   correct clears, two wrong flags cleared, one correct flag held (it cannot see a
   challenge's attached files, which is why it held that one), and zero caught. Deleted
   outright rather than left advisory: an unread verdict printed next to every candidate
   read as a judgment call it never was. See the D11/D15 amendments in [`ARCHITECTURE.md`](ARCHITECTURE.md).

Stages 1–3 reject under **every** `--approval` policy. Stages 5–6 — flag format and
re-derivation — are what decide whether a candidate can be finalized without a human:
under `gated` both must pass, otherwise the run ends at exit code 2 with the candidate in
the trace for `runectl flag approve`. Stage 4 is reported and decides nothing. The
gating set is named explicitly as `_GATING_CHECKS` in `flags/judge.py` so it cannot drift
without someone editing that line.

Both mechanisms removed from the gate so far — corroboration, then the review — were forms
of model judgement, and each was satisfiable or fooled by the model it was judging. What
holds is the sandbox: provenance says the string came from a tool's output rather than the
agent's own command, and re-derivation says the tool produces it again.

A rejection is feedback, not failure — it goes back to the model as a tool message and the
run continues. No flag with evidence beats a wrong flag (D15 §5).

## Known gaps

**Context compaction triggers on message count, not tokens.** `ContextBuilder.maybe_compact`
runs before every request and compacts once history passes `DEFAULT_MAX_HISTORY_MESSAGES`
(40). That threshold is a stand-in for a real per-provider token budget — a long run with
huge individual messages can still approach the context window before the count trips.
Accurate tokenization is a later refinement.

**`runectl bench` is a subcommand group, not a bare command.** It's `runectl bench run
--suite ...`, which diverges from [`ARCHITECTURE.md`](ARCHITECTURE.md)'s D4 sketch.

**`--dry-run` from the D4 sketch doesn't exist.** No flag, no code path.

**The bench suite spans five categories as of M7 (2026-09-09).** Ten challenges are
vendored/authored (`bench/README.md`): the gated subset now covers crypto, misc, rev,
forensics and network, so a solve rate is no longer only a statement about cryptography.
Three categories still can't be measured in the gate — `web` needs a live service the
offline sandbox can't host, and `pwn`/`osint` are present but scored *outside the gate*
(pwn: a local flag file the agent can read directly; osint: an answer in rotted
live-internet state). The ten-challenge suite was scored on 2026-09-09 (claude-sonnet-5,
`bench/results/README.md`): **7/10 solved, 1 false flag, and the V1 gate MET — 6 solved,
0 false over the 7 gated cases.** Of the new categories, forensics and network solved;
rev was cut off mid-derivation by the per-run spend ceiling (a budget outcome, not a
capability wall).

**Four live bench scores. The gate is met on the third and again on the fourth — read
what that means.** The latest run (2026-09-09, `bench/results/README.md`, the M7
ten-challenge suite) scored **7 of 10 solved with 1 false flag**, with the gate **met — 6
solved, 0 false over 7 gated cases** spanning crypto, misc, forensics and network. The
earlier third run (2026-09-08) met it at 3 solved / 0 false over 4 gated cases on the
five-challenge, four-fifths-crypto suite. The gate says the false-flag subsystem is doing
its job on the cases it can fairly judge. It does not say the solver is finished — `rev`
went unsolved (cut off by the per-run spend ceiling mid-derivation), and the one false
flag is still `quick-math`, below.

`quick-math` is the excluded case, and it is excluded for a stated reason rather than for
being hard: its run does the Hastad broadcast attack correctly and submits the recovered
value one transformation short of the flag, so provenance, decoys, re-derivation and the
disconfirmation review all agree with it — correctly. No mechanism this subsystem could
add would catch that. It still runs, still counts in the headline numbers, and prints its
exclusion reason beside its result; only the pass/fail gate reads the narrower subset.

**The one held candidate on the third run was holding the *correct* flag**, and the
reviewer said why: it could not see the challenge's `code.py`, because the review prompt
gets the description, the cited command and its output and **not** the attached files. That
is a real blind spot, not a defect in the answer, and it is unfixed pending a decision on
what widening the review prompt costs.

**The first live bench score, for the record: 0 solved, 1 false flag**
(`bench/results/README.md`, 2026-09-08, claude-sonnet-5, $0.38). Read the write-up rather
than the headline number — the tool produced the *correct* flag in four of five challenges
and held all four for approval, while the one it finalized was wrong. Both halves trace to
D11's corroboration rule, which on this evidence cost four solves and prevented zero false
flags. That is one run, not a mandate to change a locked decision.

**The budgets are still unmeasured.** Zero steps have been blocked across any of the three
suites — runs take 4–16 steps against limits of 60–80 — so the per-family and
per-hypothesis budgets have never actually bound anything. Nothing here justifies moving a
step limit. A forced strategy shift *has* now fired on a live run
(`20260909-011822-4c708c`, three failed commands in a row) and the run recovered and
solved, which is the first evidence that half of D8 does anything outside a test.

**A per-run spend cap was misread once, and the correction matters more than the reading.**
The second bench blamed `machine-fix`'s failure on the $0.15 ceiling. The third solved the
same challenge for **$0.0377** after a provenance change — a quarter of that cap. The cap
was where a doomed search happened to stop, not the thing stopping it. Treat "it ran out of
budget" as a hypothesis to check against a trace, not a conclusion.

**`easy-01` is a known-unfair gate.** It was the first vendored challenge and its
description does not contain enough to solve it without the original repo's file layout;
`easy-02` replaced it as the one real runs are measured against.

## Milestones

- **M0–M4 — done.** Repo skeleton, trace/store, sandbox layer, provider layer, and the
  walking-skeleton loop.
- **M5 — done.** Tactic families, output fingerprinting, signal scoring, per-family and
  per-hypothesis budgets, forced strategy shifts. Makes the D16 progress ratio mean
  something.
- **M6 — done.** Mandatory citable provenance, verification re-derivation, decoy
  detection, corroboration ≥2, the `--approval` policy, exit code 2, `flag list` /
  `flag approve`.
- **M8 — done.** `runectl bench run`: scores a suite against `expected.json`, reports
  solve rate with D16's progress-waste ratio, bounds spend per run and per suite, and
  fails outright on a finalized wrong flag. Built before M7 so the remaining categories
  can be measured as they land rather than after.
- **M7 — done (2026-09-09).** The remaining five categories (`pwn`, `rev`, `forensics`,
  `osint`, `network`) ship as data at equal depth, the arena grew the toolset they name,
  and the bench grew from 5 to 10 (one case per new category). Re-benched the same day:
  7/10 solved, 1 false flag, V1 gate met over the 7 gated cases.
- **M9 — done (2026-09-09).** Grew beyond its original "human render polish" scope into
  the operator surface as a whole: extended thinking (D20) captured as its own trace
  event and rendered live; `runectl config`/`models`/`runs` (discovery and preference
  commands, all read-only or preference-only); and `runectl tui`, an interactive
  in-terminal view added to D13 by a dated amendment (a TUI, not a GUI — no server, no
  port; every run it launches is still a plain non-interactive `runectl run`
  subprocess, and `loop/runner.py` gained no threading to support it). `runectl tui
  --replay <run_id>` and `make demo` are the demo `PLAN.md` asks for — a real, recorded
  run's reasoning and actions, animated at a readable pace, at zero replay-time spend.

Ordering note: M5 and M6 were both prerequisites for trusting a solve, and M6 addresses
the product's biggest risk — a wrong flag scores worse than no flag. What neither of them
did is *measure* anything: every budget number and step limit in the category TOMLs is
still an unmeasured starting value (D16), and `runectl bench` is what earns the right to
change them.
