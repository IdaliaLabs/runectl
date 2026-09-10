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
| `--flag-format <regex>` | — | Expected flag shape. A candidate that does not match is held for approval rather than auto-finalized (D11). With no format supplied the check does not apply — it never blocks on its own absence. |
| `--utility-model <id>` | cheapest model of `--model`'s provider | Model used for internal summarization calls. Its tokens land in the same cost ledger. |
| `--api-key <str>` | — | Highest-precedence key source. Prefer `runectl keys set` or an env var. |
| `--approval <gated\|strict\|auto>` | `gated` | What a cleared candidate becomes. `gated`: auto-finalize only if re-derived in the sandbox and matching `--flag-format`; otherwise exit 2. `strict`: never auto-finalize. `auto`: finalize on plausibility and provenance alone. Provenance and decoy checks apply under all three. Corroboration and the disconfirmation review are reported on every candidate and gate nothing (D11, amended 2026-09-08). |
| `--network <none\|bridge>` | the category's value | Container network mode. `none` for offline categories. |
| `--max-steps <int>` | the category's `step_limit` | Hard step backstop for this run. |
| `--record` | off | Record provider request/response pairs to `cassette.jsonl` so the run can be replayed at zero spend. |
| `--output <jsonl\|human>` | `human` on a TTY, `jsonl` otherwise | Render mode. See "Output contract" below. |
| `--thinking <off\|low\|medium\|high\|xhigh\|max>` | the configured per-provider default (`runectl config`), or `off` | Extended thinking (D20). The *resolved* level (after any provider clamp) is written to `run.started` and `run.json`, so a run's reasoning spend is never invisible. See [`PROVIDERS.md`](PROVIDERS.md#extended-thinking-d20). |

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
`flag_format` to unset. Paths in `files` are resolved relative to **the TOML file's own
directory** (an absolute path is left alone) — not your current working directory. A
challenge directory is a unit that gets moved and vendored as a whole:
`runectl run --challenge bench/practice/easy-03/chal.toml` from the repo root finds
`easy-03/files/enc.txt` even though you ran the command from elsewhere. See
`challenge_from_file` in `src/runectl/cli/run_cmd.py`.

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
| 2 | flag candidate found, awaiting approval — not a failure | yes |
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

`timeline` (the default) replays the run through the *same* renderer a live run uses, so
what you read afterwards is exactly what you would have watched happen:

```
  ▶ modern-clueless-child [crypto]  claude-sonnet-5
    max 20 steps · network=none · approval=gated
  8 → run_command  python3 -c " parts = ['52','41','58','51','47','57','49','48',…
  8 ← ok           b'csictf{you_are_a_basic_person}' (0.5s)
  9 ✓ finalized: csictf{you_are_a_basic_person}
      flag observed verbatim in tool output at seq 43
  ■ solved — csictf{you_are_a_basic_person}
    9 steps (7 with progress, 78%) · 0 blocked
    $0.0724 · 66s · exit 0
```

Commands and outputs are clipped to one line each — an exploit script is thousands of
characters and the live view has to stay watchable. The full text is always in
`trace.jsonl`. `jsonl` prints the raw event stream, one JSON object per line.

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

`--check` then compares two things and exits **1** on either divergence: the two runs'
`tool.call` event sequences, and their **outcomes**. That's the regression test: change
the loop, replay a recorded run, and find out immediately whether the agent would have
done something different — or reached a different verdict on the same evidence.

The outcome half was added 2026-09-10, after comparing only the sequence let a real defect
hide for a milestone: an unrecorded sandbox call in the D15 judge desynchronized
`ReplaySandbox`'s positional queue, so replays issued identical tool calls while silently
ending `candidate` instead of `solved`, and `--check` reported OK throughout. See
`DECISIONS.md` D3's 2026-09-10 amendment.

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

## `runectl config`

Per-provider preferences and run-wide defaults, in `~/.config/runectl/config.toml`
(overridable with `RUNECTL_CONFIG_HOME`). This is a discovery/convenience surface only —
it does **not** relax D5. `--model` is still required on every `runectl run`; nothing
here is read by the run path to silently choose a model. What it *does* prefill: the
default level `--thinking` resolves to when omitted, and (for the TUI's launcher) a
default model per provider.

```bash
uv run runectl config set anthropic.model claude-sonnet-5
uv run runectl config set anthropic.thinking high
uv run runectl config get anthropic.thinking
uv run runectl config list
uv run runectl config path
```

An API key pasted into `SECTION.KEY` is rejected outright — `config.toml` is plain text,
not the keyring; use `runectl keys set` instead. `config get` on an unset key prints
nothing and exits 1, not an error.

---

## `runectl models list`

The model registry, joined with which providers have a key present on this machine and
each model's thinking support — the discoverability answer to `--model` always being
required (D5).

```bash
uv run runectl models list
```

```
claude-opus-5   provider=anthropic  key=yes  thinking=yes (max max)  ctx=1000000  $5.00/$25.00 per 1M
claude-sonnet-5 provider=anthropic  key=yes  thinking=yes (max max)  ctx=1000000  $2.00/$10.00 per 1M [configured default]
gpt-5           provider=openai     key=no   thinking=yes (max max) ctx=272000   $5.00/$15.00 per 1M
```

---

## `runectl runs`

Discover, inspect, and attach to runs — read-only, on purpose (there is no `runs rm`;
deleting a run's directory deletes the only record of what that run did, D3).

```bash
uv run runectl runs list [--limit N] [--category C] [--outcome O] [--json]
uv run runectl runs show <run_id> [--json]
uv run runectl runs ps                       # live runectl-<run_id> containers
uv run runectl runs attach <run_id> [--exec] # prints (or runs) docker exec -it ...
```

`runs list` reads the derived SQLite index (`runectl index rebuild` regenerates it —
D3, the index is never authoritative, so a stale or missing index just means an empty
list, not an error). `runs ps` is the multi-instance visibility a Docker-per-run design
otherwise lacks: it lists live `runectl-<run_id>` containers by filtering `docker ps` on
the name prefix, exits 4 (with the same distinct message as `arena status`) if the
daemon is unreachable.

---

## `runectl tui`

An interactive, in-terminal view over runs — `runectl`'s one screen-owning surface,
added to D13 by a dated amendment rather than by drift (see `DECISIONS.md` D13). It is a
TUI, not a GUI: no server, no port, no browser involved, and every action it takes is
composing and launching the exact non-interactive command a human would type.

```bash
uv run runectl tui                       # live: launch and watch runs, approve flags
uv run runectl tui --replay <run_id>     # demo: animate through a finished run's trace
```

**The one architectural idea**: the TUI never runs the agent loop in-process. Launching
a run from its modal spawns `runectl run --output jsonl ...` as a subprocess and reads
the same stdout-NDJSON stream any other driving agent reads (the "Output contract"
above). `loop/runner.py` gained no threading or async to make this work; several runs
watched at once are just several subprocesses, each with its own container (D2
unchanged — still one container per run). The run itself stays exactly as
non-interactive as it is when driven from a shell.

**`--replay <run_id>`** is Phase 5's demo mode: it animates straight through a finished
run's already-recorded `trace.jsonl` at a readable pace (`--playback-delay`, default
0.6s between events) — "show the thought, show the command, show the output, show the
next move" (`PLAN.md`'s stated demo). This is deliberately **not** `runectl replay`,
which re-executes the loop against `ReplayProvider`/`ReplaySandbox` to prove the
tool-call sequence still matches; playback only reads what already happened, so it needs
no sandbox, no provider, and spends nothing regardless of whether the original run did.

`make demo` seeds a scratch run store with one solved, zero-spend fixture run (via
`StubSandbox`/`ScriptedProvider` — no Docker, no key) and opens straight into its
playback, so the demo works on a clean checkout.

Arena preflight on startup only reports a missing image (pointing at `arena ensure`); it
never builds one — D17's rule that a run must never kick off a 30-minute build behind
your back applies to the TUI too.

---

## `runectl arena`

The arena image (`runectl/arena:kali`) is the sandbox every challenge runs inside. It is
the one piece of setup required before `runectl run` will do anything.

```bash
uv run runectl arena ensure      # first-time setup, interactive on a TTY
uv run runectl arena build       # build from arena/Dockerfile
uv run runectl arena status      # is it here, and is it current?
```

### `arena ensure`

The first-run helper. With no flags on a TTY it asks which route you want:

```
The arena sandbox image (runectl/arena:kali) isn't on this machine yet.
Challenges run inside it, so runectl needs one before it can do anything.

  1) Build it here from arena/Dockerfile
     ~15-40 min, several GB, pulls kalilinux/kali-rolling:latest from Docker Hub
  2) Load an image file I already have (a `docker save` tarball)
  3) Pull a prebuilt image from a registry
  4) Cancel

Which [1]:
```

Every route also has a non-interactive form, so nothing here can block a script:

| Flag | What it does |
|---|---|
| `--build` | Build from `arena/Dockerfile` |
| `--from-file <path>` | `docker load` a tarball, re-tagging it as the arena image if it carried another name |
| `--from-registry <ref>` | `docker pull` a prebuilt image and tag it |
| `--force` | Act even if a current image is already present |

With no flags **and no TTY**, `ensure` prints the remedies and exits 4 rather than
prompting. Nothing in `runectl` can ever block waiting on a human.

To move an arena image between machines, or keep one for an offline competition:

```bash
docker save runectl/arena:kali -o arena.tar            # on the machine that has it
uv run runectl arena ensure --from-file ./arena.tar    # on the machine that doesn't
```

### `arena status`

```
runectl/arena:kali -> sha256:...
  built from Dockerfile fingerprint: 4f2a9c1b7e0d3a55
  Dockerfile in this tree:           4f2a9c1b7e0d3a55
  architecture:                      amd64
```

Exits 4 if the image isn't built (printing the remedies) and 4, with a different message,
if the Docker daemon isn't reachable at all — the two are distinguishable on purpose.

If the two fingerprints differ, the image was built from an older Dockerfile and `status`
says `STALE`. That's a warning, not a failure: the image still works, its toolset is just
older than your checkout.

### `arena build`

Shells out to `docker build`, streaming Docker's output to your terminal, and returns
Docker's exit code. It stamps the Dockerfile's fingerprint onto the image as a label,
which is what makes the staleness check above possible.

### What `runectl run` does about all this

Before creating a run directory or resolving your API key, `run` checks the image:

- **Missing** → exit 4, printing every remedy. No run directory is created, nothing is
  spent.
- **Daemon unreachable** → exit 4, with a message saying so specifically.
- **Stale** → a warning on stderr, then the run proceeds.
- **Built for the wrong architecture** → a warning on stderr, then the run proceeds.

### Architecture

The arena is always built and run as `linux/amd64`, whatever your host is. CTF challenge
binaries are overwhelmingly x86-64, and an arm64 arena — what you get by default on Apple
Silicon — cannot execute them; the failure looks like a broken challenge rather than a
broken sandbox. Docker emulates, which is slower but correct. If you already have an arena
built the wrong way, `arena status` says so.

### Attaching to a live run

Containers are named after the run, so you can take over from the agent while it works:

```bash
docker exec -it runectl-<run_id> bash      # a shell in the live container
tail -f /ctf/.agent_live.log               # or just watch every command it runs
```

`runectl run` prints the exact command when it starts on a terminal.

`run` never prompts you and never silently builds the image for you. That's deliberate
([`DECISIONS.md`](../DECISIONS.md) D2, D4, D17): a run that can block on a question isn't
scriptable, and a run that quietly kicks off a 30-minute build when you asked it to solve
a challenge isn't honest.

## `runectl index rebuild`

```bash
uv run runectl index rebuild
# rebuilt index from 12 run(s)
```

Drops and regenerates `~/.local/share/runectl/index.db` purely from the run directories
on disk. The database is a convenience index for cross-run questions and is **never**
authoritative — deleting it loses nothing about any run's replayability.

---

## `runectl flag`

The human half of D11. Under the default `gated` policy, a run that finds a candidate it
cannot re-derive in the sandbox — or that does not match the `--flag-format` you supplied —
exits **2** and leaves it in the trace instead of claiming a solve. These commands are what
happens next.

### `runectl flag list <run_id>`

One JSON object per line per pending candidate — `step`, `flag`, `how_found`,
`provenance_seq`, and `held_because` (the checks that were not satisfied, verbatim):

```json
{"step": 2, "flag": "flag{...}", "how_found": "decoded chal.txt", "provenance_seq": 8,
 "held_because": "held for approval — rederivation: re-running the cited command did not produce the flag again"}
```

Exits **3** if the run held nothing.

### `runectl flag approve <run_id> [--flag <value>]`

Finalizes a pending candidate: appends a `flag.decision` event (the trace is append-only,
so the judge's original `pending` decision stays visible) and updates `run.json` to
`outcome: solved`, `exit_code: 0`, with `approved_at` set. That timestamp is what keeps an
approved solve distinguishable from one the judge cleared unattended — `runectl bench`
scores them apart.

`--flag` is required only when a run held more than one candidate. Exits **6** if the run
has no pending candidate, which also makes approving twice a no-op rather than a way to
invent a second solve.

---

## `runectl bench run`

Runs every challenge in a suite through the same path `runectl run` uses, scores each
against its `expected.json`, and reports the solve rate together with D16's progress-waste
ratio. See [`bench/README.md`](../bench/README.md) for the suite itself.

```bash
runectl bench run --suite bench/practice --model claude-sonnet-5 --max-total-cost 1.00
```

| Flag | Default | Meaning |
|---|---|---|
| `--suite <dir>` | `bench/practice` | Directory of challenge directories. |
| `--model <id>` | **required** | As for `runectl run`. |
| `--only <name>` | — | Challenge name or directory name. Repeatable. |
| `--max-cost <usd>` | `0.50` | Ceiling for **one** run. |
| `--max-total-cost <usd>` | `0` (off) | Ceiling for the **whole suite**. It stops cleanly between challenges, and never lets one run overshoot what is left. |
| `--max-steps`, `--approval`, `--utility-model`, `--api-key`, `--record` | as `runectl run` | Passed through to every run. |
| `--thinking <level>` | `off` | As for `runectl run` (D20) — **defaults to `off` here, not the configured per-provider default**: a suite runs unattended and repeatably, and a reasoning-cost surprise across ten challenges is a worse place to discover a config default than one run. |
| `--report <path>` | — | Also write the JSON report to a file. |
| `--output <human\|json>` | `human` | `json` prints the report object on stdout. |
| `--dry-run` | off | List what would run and exit. Spends nothing. |

**Exit codes.** `0` normally — including when nothing solved, because that is a result.
`1` if any run **finalized a wrong flag**: a false flag is the one outcome worse than
failing, so it fails the command. `6` for a malformed or missing suite.

A wrong flag that was *held* for approval is not a false flag and does not fail the
command — holding it is the D15 subsystem working.

The report carries per-case `status` (`solved`, `false_flag`, `candidate`, `unsolved`,
`error`), steps, progress ratio and cost, plus suite totals and `gate_met` — the V1 gate
of 2 solved with 0 false flags.

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
