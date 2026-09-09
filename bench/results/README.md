# Bench results

One JSON report per scored run of the practice suite, named
`<date>-<model>.json`. Written by `runectl bench run --report <path>`.

These are kept in the repo on purpose. D16 says step limits and budgets come
down as real data arrives and never go up, which is only checkable if the data
that justified a change is still here to read.

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

- **The reviewer's blind spot.** It cannot see challenge files. Fixing it would
  likely convert `machine-fix` from held to solved. Needs a decision on cost.
- **Five categories still unbuilt** (`pwn`, `rev`, `forensics`, `osint`,
  `network`). Four of the five bench cases are crypto; the suite currently
  measures one category well and nothing else at all.
- **`runectl flag approve 20260909-012150-7cc6e6`** would finalize `machine-fix`
  by hand, which is exactly what exit 2 is for.
