# Bench results

One JSON report per scored run of the practice suite, named
`<date>-<model>.json`. Written by `runectl bench run --report <path>`.

These are kept in the repo on purpose. D16 says step limits and budgets come
down as real data arrives and never go up, which is only checkable if the data
that justified a change is still here to read.

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
