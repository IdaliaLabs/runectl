# Provenance — easy-05 (little-rsa)

- **Source:** [`csivitu/ctf-challenges`](https://github.com/csivitu/ctf-challenges), `crypto/little RSA/`
- **Commit:** `33ef02b4ecec332c6e7d5f5511a930747645ba8f` (master, fetched 2026-09-08)
- **License:** MIT (Computer Society of India — VIT University, 2020)
- **Author credit:** [pragati1610](https://github.com/pragati1610), from the original challenge README
- **Original challenge:** `csictf` 2020, category "crypto" ("little RSA") — recover
  a plaintext from tiny RSA parameters, then use it as a zip password.

## What was and was not vendored

`files/a.txt` and `files/flag.zip` are the upstream files byte for byte. The zip
is 226 bytes and stores a single 32-byte `flag.txt`; it is included because the
challenge is not solvable without it.

`chal.toml`'s `description` reproduces the README's "Sources" block and the
author's Hint 1. The exploit section — which walks the continued-fraction
convergents and states the recovered password — is **not** vendored.

## Why this challenge

It is the only multi-step challenge in the suite: recover `m` from `(c, n, e)`,
then use `m` as a password to open an archive, then read the flag out of it. A
solver that stops after the arithmetic has not solved it, which is a more honest
shape than a challenge that ends the moment a decode succeeds.

It is also the strongest test of D15's corroboration rule in the suite: the flag
comes out of a file the solver had to unlock, so a run that submits it can point
at a command that produced it — and a run that guesses cannot.

The upstream framing calls this a Wiener's attack challenge, and the hint says
so. It is worth knowing that `n = 64741` is small enough to factor directly, so
a solver may well solve it the shorter way. That is fine: the bench scores
whether the flag is right, not whether the intended method was used.

## Verified

The flag in `expected.json` was re-derived locally on 2026-09-08, not copied
from the README: `n = 64741` factored by trial division, `d = e⁻¹ mod φ(n)`,
`m = c^d mod n = 18429`, then `unzip -P 18429`, which yields the flag.
