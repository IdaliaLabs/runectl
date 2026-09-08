# Provenance — easy-03 (rivest-shamir-adleman)

- **Source:** [`csivitu/ctf-challenges`](https://github.com/csivitu/ctf-challenges), `crypto/Rivest Shamir Adleman/`
- **Commit:** `33ef02b4ecec332c6e7d5f5511a930747645ba8f` (master, fetched 2026-09-08)
- **License:** MIT (Computer Society of India — VIT University, 2020)
- **Author credit:** [roerohan](https://github.com/roerohan), from the original challenge README
- **Original challenge:** `csictf` 2020, category "crypto" ("Rivest Shamir Adleman") —
  RSA where one of the two primes is small enough to find directly.

## What was and was not vendored

`files/enc.txt` is the upstream file byte for byte (`n`, `e`, `c`). `chal.toml`'s
`description` reproduces the README's "Sources" block plus one line naming the
file, which is what a competitor saw.

The upstream README's exploit section — which states the small prime outright
and includes a complete working solver script — is **not** vendored. Vendoring
it would put the answer in the challenge data and measure reading rather than
solving (D10).

## Why this challenge

Chosen for a technique the suite did not otherwise cover: the work is
*factoring*, not decoding. The whole challenge is noticing that `n` has a small
factor and finding it — a solver that reaches for `sympy.factorint` or trial
division gets there, one that only knows how to run `base64 -d` does not.

The flag is readable English, so a solver that has it knows it has it. That is
the same property `easy-02` was chosen for and `easy-01` lacks.

## Verified

The flag in `expected.json` was re-derived locally on 2026-09-08 rather than
copied from the README: `p = 15485863`, `q = n // p`, `d = e⁻¹ mod φ(n)`,
`m = c^d mod n`, decoded from hex. It matches what the README claims.
