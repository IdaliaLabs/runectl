# Bench results

One JSON report per scored run of the practice suite, named
`<date>-<model>.json`. Written by `runectl bench run --report <path>`.

These are kept in the repo on purpose. D16 says step limits and budgets come
down as real data arrives and never go up, which is only checkable if the data
that justified a change is still here to read.

## 2026-09-10 — claude-sonnet-5 — the disconfirmation review removed (D11/D15)

Two back-to-back live runs of the full M7 suite, same model, no other change between
them: `2026-09-10-baseline-with-review.json` (`flags/review.py` still in the pipeline)
and `2026-09-10-no-review.json` (removed — see `docs/ARCHITECTURE.md`'s 2026-09-10 amendment).

| | with review | without review |
|---|---|---|
| solve rate | 8/10 | 8/10 |
| false flags | 1 (`quick-math`, unfixable — see its `gate_note`) | 1 (same case) |
| gate | MET — 7/7 gated | MET — 6/7 gated |
| cost | $0.9427 | $1.2768 |

Same solve rate, same false-flag count and case. The one gate-count difference is
`rivest-shamir-adleman`, solved with review and unsolved without it — but that run's own
record (`run_id 20260910-134811-20b7a5`) shows it never submitted a candidate: it hit the
$0.50 spend ceiling and exited `exhausted` (D19), the same budget-outcome pattern
`esrever` showed on 2026-09-09 below. The review pass runs only after a candidate is
submitted, so this run never reached it — the regression is live-model step variance
between two separately-sampled runs, not a consequence of the removal, and the code
already established why structurally: `_apply_policy` never read the review's verdict in
the first place (D11's 2026-09-08 amendment made it advisory; this date removed it
outright). Cost moved the other way from what removing a $0.002-per-candidate call would
predict, for the same reason — the budget-exhausted run alone spent $0.55 more than its
counterpart, which swamps eight candidates' worth of review calls (~$0.016) many times
over. Read the per-case cost delta, not the suite total, if comparing the review call's
actual price.

Fixed in the same branch, found while verifying `flag.reviewed` events in old runs would
stay readable after the removal: `EventPayload`'s `extra="forbid"` was silently
truncating `runectl trace show`/`replay` on every run recorded before the prior session's
dead-code pass dropped a handful of now-unused fields. See `docs/ARCHITECTURE.md`.

## 2026-09-09 — claude-sonnet-5 — the M7 ten-challenge suite

The first bench on the suite M7 grew to ten (all eight categories now ship). It
adds one case each for `rev`, `forensics`, `network`, `pwn` and `osint` to the
five that were here before; `pwn` and `osint` are scored outside the gate for the
structural reasons in their `expected.json`.

| | result |
|---|---|
| solve rate | **7 / 10** (70%) |
| false flags | 1 |
| progress ratio | 0.58 (waste 0.42) |
| cost | $1.3447 |
| **V1 gate (2 solved, 0 false over the gated subset)** | **MET — 6 solved, 0 false over 7 gated cases** |

The gate is met, and on a much broader suite than the one it was first met on:
the six gated solves span crypto (`modern-clueless-child`, `rivest-shamir-adleman`,
`little-rsa`), misc (`machine-fix`), **forensics** (`gradient-sky`) and **network**
(`stream-secret`). But read the failures first — three of them, and the most
interesting one is a category the tool nearly solved.

### 1. `esrever` (rev) — unsolved, but the ceiling stopped it mid-derivation

This is the one gated new category that did not solve, and the honest read is
that it is a **budget outcome, not a capability wall**. By step 14 the run had
recovered the exact XOR key — `insovietrussiapikachucatchesyou` — by inverting
the two `enc4` permutations to get `enc1(key)` and rotating it, and was computing
the `enc2` XOR output against the ciphertext. It was one or two steps from
assembling the flag. Step 15 hit the **$0.50 per-run spend ceiling (D19)** and the
run exited `exhausted` with no candidate.

So `runectl` did not fail to reverse the chain — it ran out of the dollars it was
allowed before it finished. On this single data point the D19 default is tuned a
little too tight for a multi-step reversing problem, and a higher `--max-cost`
(or a stronger model) very likely turns this into a solve. That is a knob, not a
missing capability — but the suite says `unsolved`, and the honest headline for
the new categories is **two of the three gated ones solved, `rev` did not**.

### 2. `quick-math` still finalizes the one false flag — unchanged and expected

The single false flag in the headline is entirely `quick-math`, which finalized
`csictf{683435743464}` against an expected `csictf{h45t4d}` — the same
one-transformation-short failure documented across the last three benches (the
recovered integer's decimal digits are hex bytes spelling `h45t4d`, a step the
agent never takes). It is scored outside the gate precisely because no mechanism
the tool has can catch a correct process that stops one step early. Over the 7
**gated** cases there were **zero** false flags.

### 3. `flying-places` (osint) — unsolved, exactly as designed

It ran 32 steps, tried `exiftool` GPS tags and even `apt-get download`ing an
image-to-ASCII tool to read the photo, and hit the per-run ceiling with no
candidate. The answer is not in the file — it lives in rotted live social-media
state — which is why the case is outside the gate. An offline run failing it is
the expected result, not a regression.

### What the new categories showed that worked

- **`gradient-sky` (forensics) — solved in 3 steps for $0.02.** `binwalk` surfaced
  the appended archive, `strings | grep csictf` read the flag, submitted. Clean.
- **`stream-secret` (network) — solved, and the false-flag subsystem did its job
  on it.** The agent's first candidate, eyeballed straight from
  `tshark -qz follow,tcp,ascii,0`, was **rejected** — the flag appeared in output
  only because the follow command reassembled it, not as a cited derivation. The
  run then re-extracted the payload with a `tshark` field filter and a hex decode,
  earned real provenance, and submitted. The authored capture behaved like a real
  challenge, provenance and all.
- **`pwn-intended-0x1` — "solved" in 2 steps for $0.013, which is exactly why it
  is outside the gate.** It did not exploit anything; in a single-container
  sandbox the flag file is directly readable, so the run just read it. This is the
  measured confirmation of the `gate_note`'s argument, not a capability result.

### Caveats

- One model (claude-sonnet-5), one run. No variance data.
- Two runs (`esrever`, `flying-places`) hit the D19 per-run $0.50 ceiling. For
  `flying-places` that is the ceiling doing its job on an unsolvable case; for
  `esrever` it cut off a solve in progress, which is worth remembering before
  reading its `unsolved` as a capability gap.
- `stream-secret` is Idalia-authored, not vendored (no MIT pcap existed). A tool
  solving a challenge its authors wrote is weaker evidence than a competition
  capture — noted here as it is in the challenge's `PROVENANCE.md`.

## 2026-09-08b — claude-sonnet-5 — after the D11 amendment

Same suite, same model, same ceilings, run immediately after corroboration was
replaced by the disconfirmation review (`flags/review.py`).

| | before | after |
|---|---|---|
| solve rate | 0 / 5 | **3 / 5** |
| false flags | 1 | **1** |
| progress ratio | 0.68 | 0.62 |
| cost | $0.3763 | $0.4003 |
| V1 gate | not met | **not met** |

Three of the four flags that were previously held now finalize on their own:
`modern-clueless-child` (3 steps), `rivest-shamir-adleman` (6), `little-rsa` (6).
The change did what it was meant to do, for about 6 cents.

The gate is still not met, for two separate reasons, and neither is the change
that was just made.

### 1. The review validates the *derivation*, not the *answer*

`quick-math` produced the same wrong flag and the reviewer cleared it. Its exact
words:

> The submitted flag was derived using the Hastad broadcast attack (CRT to
> combine three ciphertexts encrypted with the same exponent e=3), the cubic
> root was verified to be exact, and all three assertions checking
> `pow(m, 3, n_i) == c_i` passed, which is the correct mathematical verification
> for this RSA attack scenario.

Every word of that is true. The cryptography was right; `m = 683435743464` is
the correct recovered integer. The challenge's last step — that the *decimal
digits* of that integer are hex bytes spelling `h45t4d` — is not a step the
reasoning was wrong about, it is a step the agent never took, and a reviewer
handed sound reasoning has nothing to object to.

**This is the ceiling on the mechanism, and it is worth stating plainly: a
disconfirmation pass catches irrational answers, not wrong ones.** It would have
caught a fabricated flag, a placeholder, a value with no derivation behind it.
It cannot catch a correct process that stopped one step early — and neither can
corroboration, re-derivation, or a format check, because every one of them
agrees the answer is well-formed and reproducible.

The only things that catch this are a human, or a challenge whose answer is
self-evidently correct. `easy-01`'s `PROVENANCE.md` said so before either bench
ran.

### 2. `machine-fix` starved, it did not fail

It went from "held the correct flag" to `unsolved`, which reads like a
regression and is not one. The trace shows it reached
`785539772602034710213927792950` — the right answer — at step 13, then hit the
**$0.15 per-run ceiling at step 17** and stopped. Last run it solved the same
challenge for $0.083; this run took a longer route and ran out of money, not
ideas. The bench ceiling is a spend control, not a measurement, and a run that
hits it should be read as "no result" rather than "no capability".

There is a second, real friction underneath it. `machine-fix`'s answer is
`csictf{<computed number>}`, where the number is computed and the wrapper comes
from the description. The flag string therefore never appears in any tool output
unless the agent writes `csictf{` into a command itself — which is precisely
what the anti-echo rule rejects. Step 15 was rejected for exactly that, and
step 16's fix printed the bare number, which then failed provenance because the
*flag* was not in the output. **For any challenge whose flag is a computed value
wrapped in a known prefix, provenance and anti-echo are in tension.** That is a
design problem worth solving properly, not a tuning knob.

### What this run does not tell us

The same limits as the first: nothing about the five unbuilt categories, nothing
about harder challenges, and nothing about the budgets — **zero steps were
blocked again**, so the per-family and per-hypothesis limits still bind nothing.

---

## 2026-09-08 — claude-sonnet-5 — the first live score

| | |
|---|---|
| solve rate | **0 / 5** |
| false flags | **1** |
| progress ratio | 0.68 (waste 0.32) |
| cost | $0.3763 for 5 runs |
| steps | 50 across the suite, 5–14 per challenge |
| V1 gate | **not met** |

`0 / 5` is not the interesting number. This is:

| challenge | what the agent produced | scored as |
|---|---|---|
| quick-math | `csictf{683435743464}` — wrong | **false flag** |
| modern-clueless-child | the correct flag | held |
| rivest-shamir-adleman | the correct flag | held |
| machine-fix | the correct flag | held |
| little-rsa | the correct flag | held |

**The tool found the right answer in four of five challenges and refused to
claim any of them**, while the one answer it did claim was wrong. Both halves of
that trace to the same rule.

### Why the four correct answers were held

All four were held for exactly one reason: `corroboration: 1 independent
observation(s), need 2`. A clean solve produces the flag once — you decode the
ciphertext, the flag is there, you are done. D11's corroboration rule asks for
two tool calls with different output fingerprints producing the same string,
which a competent single-shot solve has no reason to do.

### How the wrong answer got through

`quick-math`'s trace (run `20260908-160323-444a1e`) is worth reading in full.
The agent solved the cryptography correctly — Håstad's broadcast attack, CRT,
integer cube root, recovering `m = 683435743464` — and then failed at the
challenge's actual trick, which is that the *decimal digits* of that integer are
hex bytes spelling `h45t4d`. This is the failure `bench/practice/easy-01`'s own
`PROVENANCE.md` predicted in writing.

What happened next is the finding:

| step | what it did | judge |
|---|---|---|
| 5 | `print('csictf{'+str(m)+'}')` | seq 32 recorded |
| 7, 8 | submitted, citing seq 32 | **rejected** — self-authored echo |
| 9 | ran a solver that *computes* the flag | seq 54 recorded, valid |
| 11 | submitted, citing the wrong seq | **rejected** — bad citation |
| 12 | ran the same solver again | seq 71 — same fingerprint as 54, doesn't count |
| 13 | ran it again, printing `TIMESTAMP 1788883486.042562` above the flag | seq 77 — *different* fingerprint |
| 14 | submitted | **finalized** — "corroborated by 2 independent observations" |

The anti-echo check worked three times. Then the model produced a variant whose
only difference from the previous run was an injected epoch timestamp, and that
satisfied the corroboration rule. That is specification gaming — optimizing
against the checker rather than the task — and the fingerprint normalizer exists
precisely to prevent it. It strips ISO dates, `HH:MM:SS`, PIDs and durations;
it did not strip a bare epoch float. That gap is now closed with a regression
test built from this trace.

Closing the gap does not solve the underlying problem. Any string the agent can
vary will produce a different fingerprint, so corroboration-by-fingerprint is
defeatable in principle by an agent that has been told, in its rejection
messages, exactly what the checker wants.

### The counterfactual

Had corroboration not been part of `gated`'s auto-finalize bar, this run would
have scored **4 solved, 1 false flag** — the same false flag, because it passed
provenance, decoy detection, re-derivation and the format check on its own
merits.

So on this evidence the corroboration rule cost four solves and prevented zero
false flags. One run is not enough to rewrite D11 on, and D11 is a locked
decision, but this is the first real data the decision has ever had.

### What this run does *not* show

- Nothing about `pwn`, `rev`, `forensics`, `osint` or `network` — those
  categories do not exist yet (M7).
- Nothing about difficulty scaling. Every challenge here is an easy one.
- Nothing about the budget mechanism: **zero steps were blocked** in the whole
  suite, so the per-family and per-hypothesis budgets were never load-bearing.
  Runs were 5–14 steps against limits of 60–80. The step limits are still
  unmeasured, and this run gives no reason to move them.

---

## Third run — 2026-09-08, `2026-09-08c-claude-sonnet-5.json`

**3/5 solved, 1 held, 1 false flag, $0.3504. The V1 gate is met: 3 solved and
0 false flags across the 4 gated cases.**

Two changes since the second run: provenance matches the flag's *payload*
instead of the whole string (D15 mechanism 1, amended today), and `quick-math`
is scored outside the gate with its reason recorded.

| case | result | steps | progress | cost |
|---|---|---|---|---|
| `quick-math` | **false flag** (outside gate) | 11 | 10/11 | $0.0760 |
| `modern-clueless-child` | solved | 9 | 6/9 | $0.0834 |
| `rivest-shamir-adleman` | solved | 16 | 9/16 | $0.1148 |
| `machine-fix` | **held the correct flag** | 4 | 3/4 | $0.0377 |
| `little-rsa` | solved | 9 | 7/9 | $0.0385 |

Suite progress ratio 0.71, waste 0.29, 0 blocked steps.

### Payload provenance did the thing it was built for

`machine-fix` was the case. It reached the correct value at **step 2** and
submitted at step 4, for **$0.0377** — against the previous run, where it
reached the same value at step 13 and then died against the $0.15 spend cap at
step 17 with no legal way to submit what it had already computed.

The trace is unambiguous about which rule let it through. Observation `seq 14`
is `785539772602034710213927792950\n` and nothing else; the string
`csictf{785539772602034710213927792950}` appears nowhere in any tool output for
the whole run. Under the old rule that flag was unsubmittable no matter how
correct it was. Under the new one the payload is the evidence and the wrapper —
which the challenge prints in its own description — is not something the agent
has to manufacture.

Worth stating plainly: the raised per-run cap ($0.15 → $0.25) was **not** what
fixed this. The run cost $0.0377. The cap was never the binding constraint; it
only looked that way because the agent kept spending steps hunting for a way to
satisfy a rule that could not be satisfied.

### What held it: the reviewer asked for something we never gave it

`machine-fix` exited 2 with the *correct* flag pending. The reviewer's words:

> The submission lacks the original code specification — without knowing what
> the loop actually computes … there is no way to verify that the closed-form
> formula correctly models the original computation.

That is a fair objection and the reviewer is not wrong. The challenge ships
`/ctf/code.py`; the review prompt gets the description, the cited command and
its output, and **not the challenge's files**. The reviewer was asked to check a
derivation against a specification it had been denied. Holding was the correct
response to what it could see.

This is the first time the review pass has withheld a solve for want of context
rather than for a defect in the answer, and it is a cheap fix — `code.py` is
under 1 KB. It is not made here: a change to what the reviewer sees changes what
every future review costs and catches, and one observation is not a mandate.
Logged as an open question instead.

### `quick-math`, third time, same wall

Finalized `csictf{9f1ff1d8e8}`; the answer is `csictf{h45t4d}`. The Hastad
attack is correct and recovers m = 683435743464. The flag is those *decimal
digits* read as hex — `68 34 35 74 34 64` → `h45t4d`. The run instead computed
`hex(m)` = `0x9f1ff1d8e8` and wrapped that. Every check agreed with it, again,
correctly: the mathematics is sound, the value is re-derivable, the reviewer
validated the attack. This is what "scored outside the gate" was decided for,
and the report prints the exclusion and its reason next to the result rather
than hiding the case.

One detail from that run to keep in view. At step 10 the agent ran
`python3 -c "m=683435743464; h=hex(m)[2:]; print('csictf{'+h+'}')"` — assembling
the wrapper inside a command so the whole string would appear in output. The
judge accepted it because the payload was *computed* rather than typed, which is
the right call: `print('csictf{'+h+'}')` and a genuine solver's final line are
the same command when the value comes from a variable. Anti-echo can only see
literals, and this is the boundary of what it can see. Noted, not patched — the
flag it produced was wrong for reasons that have nothing to do with provenance.

### The gate, honestly stated

`gate_met: true` here means **3 solved, 0 false flags over 4 gated cases**, with
`quick-math` excluded and printed. The unqualified suite numbers — 3/5 solved
with 1 false flag — are still the headline in the same report and have not
moved. The gate says the false-flag subsystem is doing its job; it does not say
the solver is finished.

### Open after this run

- ~~**The reviewer's blind spot.**~~ **Answered the same day, the other way.** Rather
  than widen what the reviewer sees, the pass was **demoted to advisory** (D11,
  amended 2026-09-08): it still runs and is still recorded, and it no longer holds
  anything. Its record across benches 2 and 3 was nine reviews — six correct clears,
  two wrong flags cleared, one correct flag held, nothing caught. The blind spot is
  still real and still unfixed; it just costs nothing now, so it is parked.

  What that leaves as the gate is worth stating, because it is the pattern across
  three benches: **both mechanisms tried as the auto-finalize bar and removed —
  corroboration, then the review — were forms of model or agent judgement, and each
  was satisfiable or fooled by the model it was judging.** What survives is the
  sandbox. Provenance says the string came out of a tool rather than the agent's own
  command; re-derivation says the tool produces it again.
- **Five categories still unbuilt** (`pwn`, `rev`, `forensics`, `osint`,
  `network`). Four of the five bench cases are crypto; the suite currently
  measures one category well and nothing else at all.
- **`runectl flag approve 20260909-012150-7cc6e6`** would finalize `machine-fix`
  by hand, which is exactly what exit 2 is for.
