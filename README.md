# runectl

An agentic CTF solver CLI, from [Idalia Labs](https://github.com/IdaliaLabs).

You hand it a challenge — name, category, the description you were given, any provided
files — plus your own provider API key. It runs an autonomous agent loop inside a
per-challenge Docker sandbox, works toward a flag, and writes a complete, replayable
trace of everything it tried.

It is built to be driven by another AI agent as much as by a human: machine-readable
I/O, nothing interactive, honest exit codes.

**CLI-only, permanently** — no GUI, no `serve` command, no `ui/` package, ever
([`DECISIONS.md`](DECISIONS.md) D13).

> **Status: pre-alpha.** The M0–M4 skeleton is built and green — the loop, trace,
> sandbox, provider, and replay layers all work end to end. The parts that make it
> actually *good* at CTFs (progress scoring and budgets, the false-flag defense
> subsystem, five of the eight category playbooks, the benchmark suite) are not built
> yet. See [`docs/STATUS.md`](docs/STATUS.md) for the honest line-by-line breakdown
> before you rely on anything here.

---

## What makes it different

- **Bring your own key, bring your own model.** Anthropic, OpenAI, and Google are all
  first-class. There is no default provider and no default model — `--model` is always
  required, and the provider is looked up in an explicit registry, never guessed from a
  string prefix.
- **The trace is the product.** Every run writes an append-only JSONL event stream that
  is the source of truth: every prompt, tool call, command result, cost update, and flag
  decision, flushed to disk after each event. A run killed mid-flight still leaves a
  valid, replayable prefix.
- **Replay at zero spend.** `--record` captures provider responses to a cassette;
  `runectl replay <run_id> --check` re-runs the whole loop from that cassette with no
  API calls and no Docker daemon, and asserts the tool-call sequence is identical.
- **It refuses to invent a flag.** A submitted flag is only finalized if it appears
  verbatim in tool output the run actually observed. A flag the agent cannot point to in
  its own trace is rejected and the run keeps going.
- **No answer keys, structurally.** The one code path that runs before the first paid
  call — deterministic triage — never receives the challenge name, its filenames, or its
  description, so a fast-path keyed to `logs.txt` has nowhere to live. A test enforces it.

## Requirements

- **Python 3.12** (3.13 is not supported yet — see [`DECISIONS.md`](DECISIONS.md) D1)
- **[uv](https://docs.astral.sh/uv/)** for dependency management
- **Docker** — needed to build the arena image and to run real challenges. Not needed
  for the test suite or for `runectl replay`.
- **A provider API key** for whichever model you choose. Not needed for the test suite
  or for `runectl replay`.

## Install

```bash
git clone https://github.com/IdaliaLabs/runectl.git
cd runectl
uv sync
uv run runectl --help
```

Every example below uses `uv run runectl`. If you'd rather have `runectl` on your PATH
directly, `uv tool install .` also works.

## Quickstart

**1. Set up the sandbox image.** One Kali-based image with a pinned CTF toolset, built
once. `ensure` walks you through it the first time.

```bash
uv run runectl arena ensure     # asks: build it, load a file you have, or pull one
uv run runectl arena status     # runectl/arena:kali -> sha256:...
```

Building takes a while and downloads several GB. If you already have the image as a
`docker save` tarball — from another machine, or for an offline competition — skip the
build entirely:

```bash
uv run runectl arena ensure --from-file ./arena.tar
```

`runectl run` checks for this image before it creates a run directory or touches your API
key, and tells you exactly how to fix it if it's missing. It never prompts mid-run and
never silently starts a 30-minute build.

**2. Store a provider key.** It goes into your OS keyring, falling back to
`~/.config/runectl/keys.json` at mode 0600. Keys are never written into a run's config
and are redacted from the trace by the writer itself.

```bash
uv run runectl keys set anthropic sk-ant-...
uv run runectl keys list        # presence only, never values
```

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` work too, as does `--api-key`.

**3. Solve something.**

```bash
uv run runectl run \
  --challenge bench/practice/easy-01/chal.toml \
  --model claude-sonnet-5 \
  --record
```

Or without a challenge file:

```bash
uv run runectl run \
  --name "quick-math" \
  --category crypto \
  --description "Ben encrypted the same message under three moduli with e=3..." \
  --flag-format 'csictf\{.+\}' \
  --model gpt-5 \
  --file ./capture.pcap
```

While it runs, you can take over — containers are named after the run:

```bash
docker exec -it runectl-<run_id> bash     # a shell inside the live sandbox
tail -f /ctf/.agent_live.log              # or just watch every command it runs
```

**4. Read the trace.**

```bash
uv run runectl trace show <run_id>                    # human timeline
uv run runectl trace show <run_id> --format jsonl     # raw events, one per line
```

```
run 20260907-220636-9221b7  easy-01 [misc]  model=claude-sonnet-5
  [   1] run.started        challenge_name='easy-01' category='misc' model='claude-sonnet-5' ...
  [   3] triage.result      category='misc' commands=['ls -la /ctf/', 'file /ctf/* 2>/dev/null', ...]
  [   7] tool.call          step=1 tool='run_command' arguments={'command': 'echo ZmxhZ3... | base64 -d'}
  [   8] tool.result        step=1 tool='run_command' ok=True kind='output' stdout='flag{sk3l3t0n_w4lk}' ...
  [  12] flag.candidate     step=2 flag='flag{sk3l3t0n_w4lk}' how_found='decoded the base64 blob' provenance_seq=8
  [  13] flag.decision      step=2 flag='flag{sk3l3t0n_w4lk}' decision='finalized' reason='flag observed verbatim in tool output at seq 8'
  [  14] run.finished       outcome='solved' flag='flag{sk3l3t0n_w4lk}' steps_used=2 cost_usd=0.00141 exit_code=0
outcome=solved exit_code=0 cost=$0.0014 steps=2
```

**5. Replay it, free.**

```bash
uv run runectl replay <run_id> --check
# OK: 20260907-220651-62a17a reproduces 20260907-220636-9221b7's tool-call sequence at zero spend
```

## Exit codes

`runectl run` never lies about how a run ended. This is what makes it safe to script.

| code | meaning |
|---|---|
| 0 | flag found and finalized |
| 2 | flag candidate found, awaiting approval — *not* a failure |
| 3 | run exhausted, no candidate |
| 4 | sandbox / infrastructure failure |
| 5 | provider failure after retries |
| 6 | usage / config error |

Code 2 is the default policy working as intended, not an error: under `--approval gated`
a flag the judge could not fully corroborate ends the run as a *candidate* rather than a
claimed solve. `runectl flag list` shows what was held and which check held it;
`runectl flag approve` finalizes it. See [`docs/STATUS.md`](docs/STATUS.md).

## Command surface

```
runectl run --model <id> (--challenge <file> | --name <n> --category <c>) [options]
runectl trace show <run_id> [--format timeline|jsonl]
runectl replay <run_id> [--check]
runectl keys set|list|rm <provider>
runectl arena build|status
runectl index rebuild
runectl flag list <run_id>           # pending candidates, as JSON lines
runectl flag approve <run_id> [--flag <value>]
runectl bench run [--suite <dir>]    # stub — lands in M8
```

Full reference, every flag, and the machine-readable output contract:
[`docs/CLI.md`](docs/CLI.md).

## Where things live

```
~/.local/share/runectl/runs/<run_id>/
    run.json         # manifest: challenge, model, config snapshot, outcome, cost, timings
    trace.jsonl      # append-only event stream — THE record
    artifacts/       # spilled outputs over 8 KB, referenced by digest from the trace
    cassette.jsonl   # recorded provider request/response pairs (only with --record)
~/.local/share/runectl/index.db    # derived SQLite index; `runectl index rebuild` regenerates it
~/.config/runectl/keys.json        # 0600 key file, only if the OS keyring is unavailable
```

Override with `RUNECTL_HOME` and `RUNECTL_CONFIG_HOME` (both honor `XDG_DATA_HOME` /
`XDG_CONFIG_HOME` when unset). Handy for keeping benchmark runs out of your real store.

## Documentation

| Document | What's in it |
|---|---|
| [`docs/CLI.md`](docs/CLI.md) | Every command and flag, exit codes, the NDJSON output contract |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Module map, the anatomy of one run, the seams later milestones fill |
| [`docs/TRACE.md`](docs/TRACE.md) | The trace format: envelope, all 16 event types, `run.json`, artifacts, cassettes |
| [`docs/PROVIDERS.md`](docs/PROVIDERS.md) | Model registry, key resolution, cost accounting, record/replay |
| [`docs/CATEGORIES.md`](docs/CATEGORIES.md) | How to write a category TOML — adding a category is never a code change |
| [`docs/STATUS.md`](docs/STATUS.md) | What is real, what is a stub, what is a known gap |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup, the checks, and the architectural rules a PR must not break |
| [`DECISIONS.md`](DECISIONS.md) | The locked architecture (D1–D16). Read before changing anything structural. |

## Development

```bash
uv sync
uv run mypy --strict src/     # strict typing is enforced in CI
uv run ruff check .
uv run pytest                 # no Docker daemon, no API key, no spend
```

The whole test suite runs against `StubSandbox` + `ScriptedProvider`, so it needs
neither a container runtime nor a cent of API credit. That's deliberate — see
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Undecided and deliberately parked. There is no license file yet, and nothing here is
published. Until that's settled, treat this repository as all-rights-reserved.

## Name

Idalia's Flute, from the Drizzt / *Sellswords* books: an instrument that reveals what
its player has hidden. The brand idea is *surface what is already there* — which is more
or less the job description for a CTF solver.
