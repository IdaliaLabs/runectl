# POSTMORTEM — the deprecated solver, and what the rebuild did about it

**Status: closed, 2026-09-09.** This file replaces `TEARDOWN.md`, `REBUILD_NOTES.md`
and `PROMPT_ARCHIVE.md`, which were written 2026-09-05 to get the rebuild started and
have now done their job — `runectl` ships all eight categories at M9 and every structural
lesson below is either implemented or explicitly closed. The three originals were deleted
on consolidation; the old source they described is still on disk, read-only, at
`DeprecatedProject/VMI_CyberFusion_26-main/`, so any detail here can be re-derived.

The one thing that is **not** re-derivable is §5 — the questions only the user's memory can
answer, because the old system never recorded its own runs.

---

## 1. What the old system was

"CTF Copilot," built for VMI CyberFusion 26: a Flask app with Docker-backed challenge
containers and an OpenAI/Anthropic-driven solve agent. Vibe-coded in roughly six hours and
patched under competition pressure. **It won real competitions** — that is why it earned
specific lessons worth keeping, and why the default verdict on its *code* was still "do
not carry it over."

The agent was six mixins sharing ~30 implicit `self` attributes, constructed directly by a
Flask route, emitting `socketio.emit(...)` from inside its own loop. Run state lived
across a JSON DB, module-level in-memory dicts, and the container filesystem.

---

## 2. What went wrong, and where the rebuild answers it

This is the part worth keeping. Format: the failure, then the decision or code that closes it.

### Honesty

| Failure | Answered by |
|---|---|
| **Two hardcoded challenge answer keys.** `_run_forensics_fastpath` gated on the literal filename `logs.txt`; `_run_rev_qna_fastpath` gated on `REChallenge1.zip` plus description text. Each was the worked solution to one specific past challenge — a hand-written PNG-LSB extractor, an x86 `MOV`-immediate domain reconstructor. Any capability number the old tool produced was inflated. | **The one rule with no exceptions.** `triage(sandbox, category)` takes exactly two arguments and never receives the challenge name, its filenames, or its description, so a filename-gated fast path has nowhere to live. `tests/unit/test_triage.py` asserts the command set is identical across two runs differing only in name and files. There is no plugin hook and no pre-LLM solver of any kind (D7). |

The techniques inside those fast-paths (PNG LSB extraction, UPX unpack, string/IP/domain
scanning) are legitimate, and exist in the rebuild only as tools in the arena image that
the agent may choose to invoke. The filename gates, the embedded answers, and the pre-LLM
auto-execution do not come back in any form.

### Structural

| Failure | Answered by |
|---|---|
| **No boundary between agent and UI.** The agent emitted socket events from inside its loop and could not run headless, be scripted, or be tested without the whole server. | Only `cli/` prints; core code emits typed events. `tests/unit/test_render_boundary.py` enforces it with an AST check. D13 forbids a network surface permanently; the 2026-09-09 amendment allows `runectl tui` **only** as a subprocess client of `runectl run --output jsonl`, which is what keeps this failure structurally impossible rather than merely avoided. |
| **State scattered and implicit.** ~30 attributes, six mixins, implicit coupling. | One `RunState` dataclass owned by the loop. Collaborators take constructor dependencies and return values; none of them mutate `RunState`. |
| **Ephemeral run history.** `challenges.json` was `[]`. In-memory logs died on restart. No run was ever replayable, so no honest capability data existed. | D3: an append-only JSONL trace is the source of truth, flushed after every event, with a derived and rebuildable SQLite index. A SIGKILLed run still leaves a valid replayable prefix. This is the single largest change between the two systems. |

### Reliability

| Failure | Answered by |
|---|---|
| **No retry/backoff** — one provider exception aborted a run that had cost real tokens. | Every LLM call goes through `complete_with_retry`, including utility calls. |
| **Silent provider no-ops** — summarization and retry summaries were hardcoded to `gpt-4o-mini` and silently did nothing on an Anthropic-only key, with untracked cost. | No hardcoded cheap model anywhere. The utility model resolves to the cheapest registered model of the same provider and lands in the same `CostLedger`. |
| **Stringly-typed control flow** — errors signalled by `[error]` prefixes and later regex-matched; provider routing by string prefix. | Structured `ToolResult(ok, kind, ...)` and `ExecResult`; the provider comes from an explicit registry, never prefix sniffing. `mypy --strict` over `src/` is the standing enforcement. |

### Cost

| Failure | Answered by |
|---|---|
| **No prompt caching** — multi-KB static playbooks re-sent every turn. | Cached stable prefix; ~85% hit rate measured on the 2026-09-07 live session. |
| **Blunt truncation** at an arbitrary char offset, with two inconsistent limits. | Summarize/extract oversized output; one configurable limit; >8KB spills to an artifact referenced by digest. |

### Testability

| Failure | Answered by |
|---|---|
| **No offline path** — nothing could exercise the loop without a live server, a Docker daemon, and real spend, which is *why* so many fixes were one-off hacks. | `StubSandbox` + `ScriptedProvider` run the entire loop with no daemon and no spend; 223 tests as of M9. `CONTRIBUTING.md` makes it absolute: nothing in `tests/` may require Docker, a key, or network. |

### Coverage — the one that shaped the whole rebuild

Only **web** was actually instrumented. Web had a tactic-family classifier,
header-normalized fingerprinting, signal scoring, tighter budgets and a mandatory decision
tree; the other seven categories had prompt text and nothing else. Web kept failing at
competitions, so web got iterated.

Answered by **D14**: all eight categories ship together as prompt/config data over one
shared agent+tool+trace engine, at equal depth, none tuned ahead of the others. The
anti-loop and progress machinery is category-parameterized from day one (M5). Web is the
worked example of the pattern, not a special case.

### Security hygiene

Plaintext API keys in `config.json`, a hardcoded Flask `SECRET_KEY`, unpinned
dependencies, no `.gitignore`, committed `__pycache__`. Answered by: OS keyring or a 0600
key file, keys redacted by the trace writer itself and never written into a run's config
snapshot, pinned dependencies, and `SECURITY.md` stating what the container boundary is
and — more importantly — what it is not.

---

## 3. What went right, and where it lives now

Each of these was a scar, not a feature — a response to an observed failure. All are
carried into `runectl`, restated as behaviors rather than ported as code:

- **Semantic anti-loop budgets** — detect repeated *tactics*, not repeated strings, and
  stop spending on a tactic producing no new signal → M5, D8.
- **Output fingerprinting** — "did this action produce new information," robust to
  timestamps and cosmetic diffs → M5.
- **Forced strategy shift on stall** → `strategy.shift` after two non-progress steps.
- **Suppress approval-seeking** — the agent must never stall asking permission → the
  `act, don't ask` nudge.
- **Flags are candidates, not answers** → D11 / D15, exit code 2, `runectl flag approve`.
- **Flag plausibility filtering** → `flags/plausibility.py`, `flags/decoys.py`.
- **A deliberately small tool surface** — five tools; `run_command` is the workhorse → D7,
  and a sixth now requires an argument in `DECISIONS.md` first.
- **One canonical tool schema, adapted per provider** → `tools/schema.py`.
- **Deduplicate repeated tool output in context** → digest-keyed back-references.
- **Deterministic triage before the first paid call** → `loop/triage.py`.
- **Non-hanging batch gdb** → `run_gdb` builds `gdb -q -batch` and can never open a session.
- **Human-tailable live log and attach** → `/ctf/.agent_live.log`, containers named
  `runectl-<run_id>`, and `runectl runs ps` / `runs attach` since M9.
- **Per-model cost tracking**, including internal utility calls → `providers/cost.py`.
- **Prompt size is a cost lever** — kept as the *lesson* only. The old two-profile
  compact/full mechanism was explicitly not carried; caching gets the same win without
  maintaining two hand-tuned prompt sets.

One item was dropped rather than carried: the **persisted retry summary** (a failed run
handing the next run what it had ruled out). It remains a reasonable idea and is not
implemented; the trace makes it cheap to add later.

---

## 4. What the old code could never tell us

The repo could not answer the questions that mattered most, because it did not durably
record its own behavior: `challenges.json` was `[]`, run traces were in-memory and died on
restart, and that copy had no `.git` directory, so the prompts read like a layered
lessons-learned journal with no way to tell an original line from a competition patch.

What the code *could* show is **where it hurt** — the patches, budgets and heuristics
cluster tightly around web exploitation and around two specific past challenges. That
asymmetry, not a solve rate, was the real signal, and it is what §2's coverage entry acts on.

---

## 5. Still open — only the user can answer these

Non-blocking. These are historical facts about past competitions, useful for tuning step
limits and budgets (D16 says those come down over time, not up), and unavailable from any
other source. Q7 was closed as moot by D14; the policy questions the original §7 raised
were all answered by `solver/DECISIONS.md` D1–D20.

1. **Per-category performance.** Which categories did it actually solve well, and which
   rarely or never? (The code suggests web was hard-won and the others less tested.)
2. **Prompt profile in practice.** Did competitions run `compact` or `full`? Was one better?
3. **Failure mode split.** When it failed: stuck in a loop, genuinely too hard, crashed, or
   out of steps?
4. **Cost reality.** Roughly what did a solve cost, which model was the workhorse, and was
   cost ever the limiting factor mid-competition?
5. **Human takeover.** How often did a human attach and take over mid-run, and for what?
6. **Step limits.** Did the old per-category limits (pwn 120, rev 100, web 80, …) feel
   right, or were they guesses never tuned? Those numbers are carried into `runectl` as
   explicitly unmeasured starting values until this is answered.
