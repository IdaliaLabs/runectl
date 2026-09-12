# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
[SemVer](https://semver.org/); `0.x` means the CLI contract can still change between
minor versions — see [`docs/CLI.md`](docs/CLI.md)'s Stability note.

## [0.1.1] — 2026-09-11

The first public release. Two of the three items under Fixed are bugs found by auditing
the code before opening it up, not by anything going wrong in use — both were silent, and
both corrupted numbers this project publishes.

### Fixed
- **`--thinking off` did not turn thinking off.** The adapters implemented `off` by
  sending no thinking configuration. On most current models that does not mean no
  thinking: Anthropic documents Claude Sonnet 5 and Opus 5 as thinking by default and the
  Fable family as always on, Gemini 3.x and 2.5 think by default except `flash-lite`, and
  OpenAI's reasoning models default to `medium` effort. So the default setting produced
  runs that thought, billed for the reasoning tokens, and recorded `thinking_level="off"`
  in their own traces with no clamp — the exact failure D20 exists to prevent. `off` now
  resolves up to `low` on such models and the clamp is recorded
  (`thinking_clamped_from="off"`). Every bench result in `bench/results/` was scored under
  the old behavior; those costs are what those runs really cost, but they are not
  comparable with runs made after this change.
- **OpenAI and Google cost accounting ignored cached and thinking tokens.** Both adapters
  assigned the provider's total prompt count to `Usage.input_tokens`, which is documented
  as uncached input only — so every cache hit was billed at the full input rate,
  overstating exactly the runs prompt caching makes cheap. Google additionally dropped
  `thoughts_token_count`, which it bills as output but reports outside
  `candidates_token_count`, making reasoning spend invisible. Anthropic's adapter was
  correct and is unchanged.
- **Two registry prices were wrong**, carried since 2026-09-05 as unverified estimates:
  `gpt-5` was listed at $5.00/$15.00 and is $1.25/$10.00; `gpt-5-mini` at $0.50/$1.50 and
  is $0.25/$2.00. Every provider block now carries the date and source URL it was checked
  against.
- **The cache-read multiplier is per model again.** A cached input token costs 0.10x a
  fresh one on current models but 0.25x on `gpt-4.1*` and 0.50x on `gpt-4o*`; the single
  module constant understated the cached portion of a run on those by up to 5x.
- **TUI layout.** The run pane was too narrow for its own columns, so model, cost and
  steps were cut off; the timeline formatted every line to a hardcoded width of 100
  whatever the terminal was, which made long lines wrap mid-sentence to column 0; and a
  running run's row showed `$0.0000` and `0 steps` for its entire life because only
  `run.finished` updated those cells.

### Added
- **37 models across the three providers**, up from 7 — including the cheap tiers a
  competition actually runs on: `gpt-5-nano` ($0.05/$0.40),
  `gemini-2.5-flash-lite` ($0.10/$0.40), `gpt-5.6-luna` ($0.20/$1.20).
  `runectl models list` and every TUI model picker now order cheapest-first per provider
  and show prices inline.
- **TUI:** a run header line above the tabs (challenge, category, model, thinking, steps,
  live cost, outcome, full run id), a pending-flag count on the Flags tab, a launch splash
  screen, a spinner on running rows, and live cost/step updates while a run is in flight.
- A hero demo GIF in the README, which was previously `.gitignore`d — GitHub renders a
  repo-relative `.mp4` as a link, not a player — plus a diagram of how one run works.

### Changed
- `runectl tui`'s run list shows the tail of each run id rather than the whole thing; the
  full id is on the header line.

## [0.1.0] — 2026-09-10

First public alpha.

### Added
- All eight challenge categories (`crypto`, `misc`, `web`, `pwn`, `rev`, `forensics`,
  `osint`, `network`), shipped together at equal depth as prompt/config data over one
  shared agent+tool+trace engine.
- `runectl tui` — an in-terminal interactive view over live and historical runs: watch a
  run, approve flags, browse or replay a trace. Every run it launches is a plain
  `runectl run` subprocess; the TUI never runs the agent loop in-process.
- `runectl bench run` — the capability benchmark suite.
- Extended thinking support and a `--thinking` level per provider.
- `runectl config` / `runectl models` / `runectl runs` command groups.
- `runectl --version`.

### Bench result
Scored 2026-09-09 on the ten-case M7 suite (`claude-sonnet-5`): **7/10 solved, 1 false
flag**; **6/7 solved, 0 false flags on the 7 cases gated for V1** (`pwn` and `osint` are
scored outside the gate for structural reasons — see `bench/results/README.md`). The
write-up leads with the failures: a `rev` case cut off mid-derivation by the per-run
spend ceiling, a `crypto` case finalized one transformation short, and a trace where the
agent gamed one of the tool's own checks.

### Known limitations
This is an alpha. The CLI's flags and output shape may still change before `1.0.0`. Read
[`docs/STATUS.md`](docs/STATUS.md) for the line-by-line ledger of what is verified versus
what merely exists before relying on any surface.
