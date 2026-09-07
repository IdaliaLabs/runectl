# Status

Last updated **2026-09-07**.

The M0–M4 skeleton is built, typed, and green: 32 tests passing, `mypy --strict` clean,
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
| Core code never prints | `tests/unit/test_render_boundary.py` |
| Tool dispatch, empty-command and path-traversal rejection | `tests/unit/test_dispatch.py` |
| Secrets redacted, >8KB values spilled, torn tail tolerated, index rebuild | `tests/unit/test_trace.py` |
| Utility-summarizer tokens land in the shared cost ledger | `tests/unit/test_utility_summarizer.py` |
| Constructing a `DockerSandbox` never touches the daemon, and a daemon failure surfaces as `SandboxError` | `tests/unit/test_docker_sandbox.py` — against a mocked client |

## Never run for real

These are the two paths the build session had no way to exercise. **Assume they need a
first smoke test before you trust them.**

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
| `runectl flag approve` | Prints an explanation, exits 6 | M6 |
| `runectl bench run` | Prints an explanation, exits 6 | M8 |
| Exit code 2 (candidate pending) | Reserved and documented; never produced | M6 |
| `--approval gated\|strict\|auto` | Recorded in the manifest and `run.started`; never branched on. Values aren't validated either. | M6 |
| `--flag-format` | Recorded in `challenge.loaded`; not enforced by the judge | M6 |
| `progress.scored`, `budget.blocked`, `evidence.added` events | Defined; not emitted by the loop | M5 |
| `tactic_families`, `signal_low`, `signal_high`, `budgets` | Typed, validated, inert | M5 |
| `pwn`, `rev`, `forensics`, `osint`, `network` categories | No TOML; `--category pwn` exits 6 | M7 |

## What the current judge actually does

One check, and it is a real one: a submitted flag is finalized only if it appears
**verbatim** in the stdout or stderr of a tool result this run actually observed.
Otherwise it is rejected, the rejection goes back to the model as a tool message, and the
run continues.

That single structural check kills invented flags. It is *not* the false-flag defense
subsystem — no plausibility filtering, no decoy detection, no independent corroboration,
no verification re-derivation, no approval policy. Those are M6, and they slot into the
same `judge_candidate()` signature and the same `flag.candidate` / `flag.decision` events.

## Known gaps

**Context compaction triggers on message count, not tokens.** `ContextBuilder.maybe_compact`
runs before every request and compacts once history passes `DEFAULT_MAX_HISTORY_MESSAGES`
(40). That threshold is a stand-in for a real per-provider token budget — a long run with
huge individual messages can still approach the context window before the count trips.
Accurate tokenization is a later refinement.

**`runectl bench` is a subcommand group, not a bare command.** It's `runectl bench run
--suite ...`, which diverges from the `DECISIONS.md` D4 sketch.

**`--dry-run` from the D4 sketch doesn't exist.** No flag, no code path.

**One bench challenge is vendored.** `bench/practice/easy-01` (MIT-licensed, from
`csivitu/ctf-challenges`, with provenance recorded). The V1 gate is 2 of 5 practice
challenges solved with **0 false flags** — four more challenges still need vendoring.

## Milestones

- **M0–M4 — done.** Repo skeleton, trace/store, sandbox layer, provider layer, and the
  walking-skeleton loop.
- **M5 — progress machinery.** Tactic families, output fingerprinting, signal scoring,
  per-family and per-hypothesis budgets, forced strategy shifts. Makes the D16 progress
  ratio mean something.
- **M6 — false-flag defense.** Mandatory provenance, verification re-derivation, decoy
  detection, corroboration ≥2, the `--approval` policy, exit code 2, `flag approve`.
- **M7 — the remaining five categories**, at equal depth.
- **M8 — bench and the capability report.** What tunes the step limits and budgets.
- **M9 — human render polish.** Compact, foldable, width-aware, over the same event
  stream.

Ordering note: M5 and M6 are both prerequisites for trusting a solve, and M6 is the one
that addresses the product's biggest risk — a wrong flag scores worse than no flag.
