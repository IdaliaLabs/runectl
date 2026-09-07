# CLI reference

`runectl` is the whole product ([`DECISIONS.md`](../DECISIONS.md) D13). Everything below
is non-interactive: no command prompts, nothing that can block a run waiting on a human,
no `--yes` flag needed because nothing asks.

All examples use `uv run runectl`; drop the prefix if you installed the console script.

---

## `runectl run`

Solve one challenge end to end, writing a replayable trace.

```bash
uv run runectl run --model claude-sonnet-5 --challenge chal.toml
uv run runectl run --model gpt-5 --name "sanity" --category web --description "..."
```

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--model <id>` | **required** | Main model. Must be in the model registry — see [`PROVIDERS.md`](PROVIDERS.md). Never inferred. |
| `--challenge <path>` | — | A challenge TOML file. Supplies name/category/description/files/flag_format in one file, so an agent driving `runectl` doesn't have to shell-quote a description. |
| `--name <str>` | — | Challenge name. Required unless `--challenge` is given. |
| `--category <str>` | — | Category name; must match a shipped category TOML. Required unless `--challenge` is given. |
| `--description <str>` | `""` | The challenge prompt as you were given it. |
| `--description-file <path>` | — | Read the description from a file instead. `--description` wins if both are given. |
| `--file <path>` | — | A provided challenge file to copy into the sandbox. Repeatable. |
| `--flag-format <regex>` | — | Expected flag shape. Recorded in the trace; **not yet enforced** by the judge (M6). |
| `--utility-model <id>` | cheapest model of `--model`'s provider | Model used for internal summarization calls. Its tokens land in the same cost ledger. |
| `--api-key <str>` | — | Highest-precedence key source. Prefer `runectl keys set` or an env var. |
| `--approval <gated\|strict\|auto>` | `gated` | Flag finalization policy. Recorded and emitted; **not yet enforced** (M6). |
| `--network <none\|bridge>` | the category's value | Container network mode. `none` for offline categories. |
| `--max-steps <int>` | the category's `step_limit` | Hard step backstop for this run. |
| `--record` | off | Record provider request/response pairs to `cassette.jsonl` so the run can be replayed at zero spend. |
| `--output <jsonl\|human>` | `human` on a TTY, `jsonl` otherwise | Render mode. See "Output contract" below. |

### Challenge TOML

```toml
name = "quick-math"
category = "crypto"
flag_format = "csictf\\{[\\w!@#?$%.'\"+:>-]{3,60}\\}"
files = []

description = """
Ben has encrypted a message with the same value of 'e' for 3 public moduli...
"""
```

`name` and `category` are required; `description` defaults to empty, `files` to none,
`flag_format` to unset. Paths in `files` are resolved relative to your current working
directory, not to the TOML file.

### Output contract

This is what makes `runectl` scriptable by another agent.

- **`--output jsonl`** (the default when stdout is not a TTY): every trace event is
  written to **stdout** as a single-line JSON object, as it happens. The final line of
  stdout is the run id. Nothing else goes to stdout.
- **`--output human`** (the default on a TTY): the same event stream is rendered to
  **stderr**. Only the run id goes to stdout.

Either way, `stdout` is safe to pipe. The human renderer is a pure function of the event
stream — it can never show you something the trace doesn't contain.

```bash
# a driving agent's happy path
run_id=$(uv run runectl run --model gpt-5 --challenge chal.toml --output jsonl | tail -1)
case $? in
  0) echo "solved" ;;
  3) echo "exhausted, nothing found" ;;
  *) echo "failed: $?" ;;
esac
```

### Exit codes

| code | meaning | produced today? |
|---|---|---|
| 0 | flag found and finalized | yes |
| 2 | flag candidate found, awaiting approval — not a failure | no — reserved for M6 |
| 3 | run exhausted, no candidate | yes |
| 4 | sandbox / infrastructure failure | yes |
| 5 | provider failure after retries | yes |
| 6 | usage / config error | yes |

Codes 0, 2 and 3 are *outcomes* of a finished run — they are carried on the run's
manifest and its `run.finished` event, and are not exceptions. Codes 4, 5 and 6 mean the
run could not proceed at all, and correspond to `SandboxError`, `ProviderError` and
`UsageError` in `errors.py`.

---

## `runectl trace show`

```bash
uv run runectl trace show <run_id>
uv run runectl trace show <run_id> --format jsonl
```

`timeline` (the default) prints a one-line-per-event human review surface, followed by
the run's outcome, exit code, cost and step count. `jsonl` prints the raw event stream,
one JSON object per line — the same shape written to `trace.jsonl`.

Exits 6 if the run id doesn't exist.

If a run was killed mid-flight and the last line of its trace is torn, the reader stops
at the last valid line and shows everything before it. You get a valid prefix, never a
parse error about the tail.

---

## `runectl replay`

```bash
uv run runectl replay <run_id>
uv run runectl replay <run_id> --check
```

Re-runs the loop against `ReplayProvider` (serving recorded completions from
`cassette.jsonl` by request hash) and `ReplaySandbox` (serving the recorded `ExecResult`s
from the original trace, in order). No Docker daemon is contacted and no API call is
made.

A replay is itself a real run: it gets a **new run id** and writes its own trace, whose
`config_snapshot` records `{"replay_of": "<original run id>"}`. The new run id is printed
to stdout.

`--check` then compares the two runs' `tool.call` event sequences and exits **1** on any
divergence. That's the regression test: change the loop, replay a recorded run, and find
out immediately whether the agent would have done something different.

Requires the original run to have been recorded with `--record`; exits 6 otherwise.

---

## `runectl keys`

```bash
uv run runectl keys set anthropic sk-ant-...
uv run runectl keys list
uv run runectl keys rm openai
```

`set` writes to the OS keyring, falling back to `~/.config/runectl/keys.json` at mode
0600 if no keyring backend is available. `list` reports presence only — it never prints a
key value. `rm` removes the key from both the keyring and the file.

Valid providers: `anthropic`, `openai`, `google`.

See [`PROVIDERS.md`](PROVIDERS.md) for the full resolution order.

---

## `runectl arena`

```bash
uv run runectl arena build
uv run runectl arena status
```

`build` shells out to `docker build -t runectl/arena:kali arena/`, streaming Docker's
output to your terminal, and returns Docker's exit code.

`status` prints the image id if the image exists. It exits **4** if the image isn't built
(with a message telling you to build it) and also **4**, with a different message, if the
Docker daemon isn't reachable at all — the two failures are distinguishable on purpose.

A `runectl run` gates on the image existing and fails with a clear message. It never
silently builds it for you.

---

## `runectl index rebuild`

```bash
uv run runectl index rebuild
# rebuilt index from 12 run(s)
```

Drops and regenerates `~/.local/share/runectl/index.db` purely from the run directories
on disk. The database is a convenience index for cross-run questions and is **never**
authoritative — deleting it loses nothing about any run's replayability.

---

## Stubs

These two are wired into the command surface so they're discoverable and stable, but
their bodies land in later milestones. Both print an explanatory message to stderr and
exit **6**.

### `runectl flag approve <run_id> [--flag <value>]`

Lands in **M6**, with the false-flag defense subsystem. The current judge auto-decides
every `submit_flag` call itself, so there is never a *pending* candidate for a human or a
driving agent to approve.

### `runectl bench run [--suite bench/practice]`

Lands in **M8**. Will run the capability suite across a directory of challenges and emit
a report — solve rate and, per [`DECISIONS.md`](../DECISIONS.md) D16, the progress-waste
ratio, not merely the step count.

Note the shape: it is `runectl bench run`, a subcommand, not the bare `runectl bench` the
D4 command-surface sketch used.

---

## Environment variables

| Variable | Effect |
|---|---|
| `RUNECTL_HOME` | Overrides the run store root (default `$XDG_DATA_HOME/runectl`, else `~/.local/share/runectl`) |
| `RUNECTL_CONFIG_HOME` | Overrides the config dir (default `$XDG_CONFIG_HOME/runectl`, else `~/.config/runectl`) |
| `ANTHROPIC_API_KEY` | Anthropic key, second in the resolution order |
| `OPENAI_API_KEY` | OpenAI key, second in the resolution order |
| `GOOGLE_API_KEY` | Google key, second in the resolution order |

`RUNECTL_HOME` is the clean way to keep experimental or benchmark runs out of your real
store:

```bash
RUNECTL_HOME=/tmp/scratch-runs uv run runectl run --model gpt-5-mini --challenge chal.toml
```
