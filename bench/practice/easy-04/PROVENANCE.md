# Provenance — easy-04 (machine-fix)

- **Source:** [`csivitu/ctf-challenges`](https://github.com/csivitu/ctf-challenges), `miscellaneous/Machine Fix/`
- **Commit:** `33ef02b4ecec332c6e7d5f5511a930747645ba8f` (master, fetched 2026-09-08)
- **License:** MIT (Computer Society of India — VIT University, 2020)
- **Author credit:** [harsoh](https://github.com/harsoh), from the original challenge README
- **Original challenge:** `csictf` 2020, category "miscellaneous" ("Machine Fix") —
  a brute-force loop that would take years to finish, whose result has a closed
  form once you see what it is counting.

## What was and was not vendored

`files/code.py` is the upstream file byte for byte. `chal.toml`'s `description`
reproduces the README's "Sources" block, including the author's own Hint 1 and
the flag format line — a competitor had both.

The exploit section, which explains the base-3 digit-contribution argument and
gives the closed form, is **not** vendored.

## Why this challenge

Two reasons.

**Technique coverage.** The suite was otherwise all cryptography. This one is
read-the-code-and-reason: the input is a Python program that cannot be run to
completion, so the solver has to work out what it computes rather than execute
it. A solver that just runs the file gets nothing but a hung process — which
also makes it an honest test of the step budget (D8), since waiting on it is
exactly the kind of no-progress step budgets exist to stop.

**Self-verifiability.** The flag is a 30-digit number, so it is not
self-evidently correct in the way `easy-02`'s English flag is. But it is
*checkable inside the sandbox*: the vendored brute force can be run against
small `n` and compared to any closed form the solver derives. A correct solver
can prove itself right without the answer key, which is the property that
matters for D15 — and this repo did exactly that check before writing
`expected.json`.

## Verified

The flag in `expected.json` was re-derived locally on 2026-09-08, not copied
from the README: the closed form `sum(n // 3**k)` was checked against the
vendored `code.py`'s own brute force for n ∈ {1, 2, 5, 27, 100, 1000, 5000}
(exact agreement) before being evaluated at the challenge's n.
