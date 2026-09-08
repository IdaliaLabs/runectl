# Status

Last updated **2026-09-08**.

M0–M6 and M8 are built, typed, and green: 149 tests passing, `mypy --strict` clean,
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

## Never run for real

These are the two paths the build session had no way to exercise. **Assume they need a
first smoke test before you trust them.**

- **`arena ensure`'s build / load / pull paths.** The argument construction is tested
  against a stubbed `subprocess`, but no `docker build`, `docker load`, or `docker pull`
  has ever actually run. The daemon-down and image-missing paths *were* exercised by hand.
- **`DockerSandbox` and `arena/Dockerfile`.** No Docker daemon was available. The image
  is a best-effort pinned toolset; the sandbox is implemented to spec and type-checks.
  Neither has been built or executed. The container posture in particular — `mem_limit=2g`,
  2 CPUs, `no-new-privileges`, non-root, per-run network mode — is asserted by nothing but
  reading the code.
- **The three provider adapters.** No API keys were available. Each was written against
  its installed SDK's actual types and exception hierarchy, but none has ever talked to
  the live service.

The smoke test:

```bash
uv run runectl arena build
uv run runectl arena status
uv run runectl run --challenge bench/practice/easy-01/chal.toml --model claude-sonnet-5 --record
```

## Stubs — wired, discoverable, no body yet

| Surface | Behavior today | Lands in |
|---|---|---|
| `evidence.added` event | Defined in the schema; nothing emits it — findings carry forward in the conversation only | later |
| LLM disconfirmation pass (D15 §2's last resort) | Not implemented; deterministic re-derivation covers every case reachable at V1 | later, on bench evidence |
| `pwn`, `rev`, `forensics`, `osint`, `network` categories | No TOML; `--category pwn` exits 6 | M7 |
| `--approval` values | Branched on correctly, but an unrecognized value silently behaves as `gated` rather than exiting 6 | small fix, unscheduled |

## What the judge actually does

Six stages, in order, and the order is the point — the cheap deterministic rejections run
before anything that costs sandbox time:

1. **Plausibility.** Placeholders (`picoCTF{flag}`), UUIDs, JSON fragments,
   capture-interface ids, multi-line strings. Rejected.
2. **Provenance (D15 §1).** The flag must appear verbatim in a tool result this run
   observed, that result must not have come from a command the agent wrote the flag into,
   and the agent must cite the observation by `seq`. Every tool result reaches the model
   with an `[observation seq=N]` header so it can. Rejected otherwise.
3. **Decoy detection (D15 §3).** A bait-named source, a taunt next to the hit, or a token
   the author pasted into the description. Rejected.
4. **Corroboration (D15 §4).** How many observations with *different* commands and
   *different* output fingerprints produced this string.
5. **Flag format.** Matched against `--flag-format` when one was given.
6. **Re-derivation (D15 §2).** The cited command is re-run in the sandbox and must produce
   the same string again.

Stages 1–3 reject under **every** `--approval` policy. Stages 4–6 only decide whether a
candidate can be finalized without a human: under `gated` all three must pass, otherwise
the run ends at exit code 2 with the candidate in the trace for `runectl flag approve`.

A rejection is feedback, not failure — it goes back to the model as a tool message and the
run continues. No flag with evidence beats a wrong flag (D15 §5).

## Known gaps

**Context compaction triggers on message count, not tokens.** `ContextBuilder.maybe_compact`
runs before every request and compacts once history passes `DEFAULT_MAX_HISTORY_MESSAGES`
(40). That threshold is a stand-in for a real per-provider token budget — a long run with
huge individual messages can still approach the context window before the count trips.
Accurate tokenization is a later refinement.

**`runectl bench` is a subcommand group, not a bare command.** It's `runectl bench run
--suite ...`, which diverges from the `DECISIONS.md` D4 sketch.

**`--dry-run` from the D4 sketch doesn't exist.** No flag, no code path.

**The bench suite is crypto-heavy.** Five MIT-licensed challenges are vendored
(`bench/README.md`), but four of them are crypto — only `crypto`, `misc` and `web` ship as
categories, and a web challenge needs a live service the offline sandbox cannot host. A
solve rate measured today is a statement about cryptography and reasoning, not about
`runectl` across all eight categories. Rebalance when M7 lands.

**No live bench run has been scored yet.** `runectl bench` is tested against faked runs
and a dry run; it has never been pointed at the API. Every budget and step limit therefore
remains an unmeasured starting value.

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
- **M7 — the remaining five categories**, at equal depth.
- **M9 — human render polish.** Compact, foldable, width-aware, over the same event
  stream.

Ordering note: M5 and M6 were both prerequisites for trusting a solve, and M6 addresses
the product's biggest risk — a wrong flag scores worse than no flag. What neither of them
did is *measure* anything: every budget number and step limit in the category TOMLs is
still an unmeasured starting value (D16), and `runectl bench` is what earns the right to
change them.
