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
| `--approval <gated\|strict\|auto>` | `gated` | What a cleared candidate becomes. `gated`: auto-finalize only if corroborated by ≥2 independent observations, re-derived in the sandbox, and matching `--flag-format`; otherwise exit 2. `strict`: never auto-finalize. `auto`: finalize on plausibility and provenance alone. Provenance and decoy checks apply under all three. |
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

The human half of D11. Under the default `gated` policy, a run that finds something it
cannot fully corroborate exits **2** and leaves the candidate in the trace instead of
claiming a solve. These commands are what happens next.

### `runectl flag list <run_id>`

One JSON object per line per pending candidate — `step`, `flag`, `how_found`,
`provenance_seq`, and `held_because` (the checks that were not satisfied, verbatim):

```json
{"step": 2, "flag": "flag{...}", "how_found": "decoded chal.txt", "provenance_seq": 8,
 "held_because": "held for approval — corroboration: 1 independent observation(s), need 2"}
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

## Stubs

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
