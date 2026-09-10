# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow
[SemVer](https://semver.org/); `0.x` means the CLI contract can still change between
minor versions — see [`docs/CLI.md`](docs/CLI.md)'s Stability note.

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
