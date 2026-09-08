# Categories

A category is a **data file**, never a code change ([`DECISIONS.md`](../DECISIONS.md) D9).
Dropping `src/runectl/categories/<name>.toml` into place makes `--category <name>` work.

All eight categories get the same machinery and the same depth — there is no
"tune web first, everything else later" order ([`DECISIONS.md`](../DECISIONS.md) D14).

## The standard eight

`pwn`, `web`, `crypto`, `forensics`, `rev`, `misc`, `osint`, `network`

**Shipped today:** `web`, `crypto`, `misc`. The other five land in M7. `runectl run
--category pwn` currently exits 6 with a message listing what *is* available.

## File format

```toml
brief = "One or two lines: how this category should be approached."

playbook = """
Longer prose the model actually reads. A decision order, the common traps, and
the specific things that are and aren't progress in this category.
"""

required_tools = ["curl", "ffuf", "gobuster", "sqlmap"]

signal_low  = ["HTTP/1\\.[01] 404", "Not Found", "Forbidden"]
signal_high = ["flag\\{", "FLAG\\{", "token", "admin=true"]

step_limit = 80
network = "bridge"

[tactic_families]
dirfuzz = "ffuf|gobuster|wfuzz|dirb"
sqli    = "sqlmap|UNION SELECT|OR 1=1|SLEEP\\("
lfi     = "\\.\\./|php://filter|/etc/passwd"

[budgets]
per_hypothesis = 2
per_family = 4
```

### Fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `brief` | string | ✓ | Short execution brief. Goes into the system prompt right after the base rules. |
| `playbook` | string | ✓ | The category's real prompt content. Also part of the cacheable system prefix. |
| `step_limit` | int | ✓ | Default max steps. Overridable per run with `--max-steps`. |
| `network` | `"none"` or `"bridge"` | — (default `bridge`) | Default container network mode. Overridable with `--network`. |
| `required_tools` | list[string] | — | Tools this category expects in the arena image. Documentation for now. |
| `tactic_families` | table of name → regex | — | Semantic classification of a command into a family. A command matching none of them lands in `other`. |
| `signal_low` | list[regex] | — | Output patterns that are explicitly *not* progress. |
| `signal_high` | list[regex] | — | Output patterns worth pursuing. |
| `budgets.per_hypothesis` | int | — (default 2) | No-progress steps allowed per hypothesis before a block. |
| `budgets.per_family` | int | — (default 4) | No-progress steps allowed per tactic family. |
| `budgets.no_progress_shift` | int | — (default 3) | No-progress steps before a forced strategy shift (D8). |
| `budgets.consecutive_error_shift` | int | — (default 3) | Consecutive failing steps before a forced strategy shift. |

The schema is `extra="forbid"` — a typo'd key is a load error, not a silently ignored
line. `name` is supplied by the loader from the filename; don't put it in the file.

Regexes are Python `re` syntax, and TOML basic strings need backslashes doubled. Prefer
`'single quotes'` (TOML literal strings) if you'd rather write them raw.

## How the progress fields are used

`tactic_families`, `signal_low`, `signal_high`, and `budgets` are read on every executed
step (`progress/`): the command is classified into a family, its output is normalized and
fingerprinted, the signal patterns score it, and the budgets decide whether the next call
on the same idea runs at all. A blocked call is rejected *before* execution — it costs no
sandbox time and no further tokens on a dead hypothesis.

Two consequences worth knowing when writing a category:

- A `signal_low` pattern that is too broad makes real findings score as noise, which
  spends the family budget faster and forces an early strategy shift.
- A `tactic_families` regex that matches nothing leaves every command in `other`, where
  they share one budget — the per-family mechanism effectively switches off.

## Step limits are unmeasured

Every `step_limit` currently shipped is a **starting value carried forward from the
predecessor's prompts, explicitly marked unmeasured**. They are a backstop, not a target.

The metric that matters is the *progress ratio* — `progress_steps / steps_used`, with
blocked steps tracked separately ([`DECISIONS.md`](../DECISIONS.md) D16). A run isn't
failing because it took many steps; it's failing because it took steps that produced no
new signal. `runectl bench` (M8) is what tunes these numbers, and as real data comes in
they come **down**, not up.

The starting values, for reference:

| Category | step_limit | network | shipped? |
|---|---|---|---|
| pwn | 120 | — | M7 |
| rev | 100 | — | M7 |
| web | 80 | bridge | ✓ |
| crypto | 80 | none | ✓ |
| forensics | 70 | — | M7 |
| misc | 60 | none | ✓ |
| osint | 50 | — | M7 |
| network | 60 | — | M7 |

The step limits for the unshipped five live in `DEFAULT_STEP_LIMITS` in `config.py`,
waiting on their TOML files; their network modes are an M7 decision and aren't set
anywhere yet.

## Writing a good playbook

Looking at what's shipped, the pattern that works:

1. **Give a decision order, not a tool list.** "Recon headers and robots.txt before
   fuzzing; test injection on endpoints you actually found before spraying parameters" is
   useful. "Use ffuf, sqlmap, and curl" is not.
2. **Name what is not progress.** Web's playbook says repeated 404/403 bodies are the
   single most common way the category burns steps for nothing. That sentence is worth
   more than another tool suggestion.
3. **Encode the specific known breaks.** Crypto's playbook walks RSA down a concrete
   order — small modulus, small `e`, Hastad broadcast, shared factors, Wiener. Misc's says
   to re-triage after every decode layer instead of assuming one pass.
4. **Keep it stable.** The whole system prompt is a cacheable static prefix; churn costs
   real money across a run.

## Adding a category

1. Write `src/runectl/categories/<name>.toml`.
2. Add whatever tools it needs to `arena/Dockerfile` and rebuild the arena image.
3. If the category needs deterministic pre-LLM triage commands beyond `ls -la` and
   `file *`, add them to `_CATEGORY_COMMANDS` in `loop/triage.py`, keyed **only** by
   category name. Never key on anything about a specific challenge — that's the D10
   no-answer-keys rule, and a test enforces it.
4. `uv run pytest tests/unit/test_categories.py` — it validates every shipped TOML.

That's it. No registration, no code change.
