# Provenance — easy-02 (modern-clueless-child)

- **Source:** [`csivitu/ctf-challenges`](https://github.com/csivitu/ctf-challenges), `crypto/Modern Clueless Child/README.md`
- **Commit:** `33ef02b4ecec332c6e7d5f5511a930747645ba8f` (master, fetched 2026-09-07)
- **License:** MIT (Computer Society of India — VIT University, 2020)
- **Author credit:** [SrishtiGohain](https://github.com/SrishtiGohain), from the original challenge README
- **Original challenge:** `csictf` 2020, category "crypto" ("Modern Clueless Child") —
  repeating-key XOR over a hex ciphertext, with a decoy character inserted at
  regular intervals that must be stripped first.

## What was and was not vendored

`chal.toml`'s `description` reproduces **only** the README's "Sources" block —
the flavour text, the ciphertext, and the key — which is what a competitor saw.

The upstream README also contains a full exploit walkthrough that names the
obfuscation character and explains the byte-level derivation. That is
deliberately **not** vendored. Including it would put an answer key inside the
challenge data, which is the same failure D10 forbids in solver code: the run
would measure reading comprehension rather than solving ability.

`expected.json` carries the known-correct flag for scoring only and is never
read by any solver code path.

## Why this challenge

Added 2026-09-07 after `easy-01` proved to be a poor V1 gate. Two models solved
`easy-01`'s actual cryptography correctly — Hastad's broadcast attack, CRT, the
integer cube root — and then both failed, differently, at guessing how to render
the recovered integer `683435743464` as a flag. The intended answer depends on
noticing that the integer's *decimal digits* are hex bytes spelling `h45t4d`.
That is trivia about one author's encoding habit, not a crypto capability, and
worse, a wrong answer there is indistinguishable from a right one without the
answer key.

This challenge has the property `easy-01` lacks: **the correct answer is
self-evidently correct.** `csictf{you_are_a_basic_person}` is readable English,
so a solver that gets it knows it, and a solver that hasn't can tell it hasn't.
That makes it a fair gate and a much weaker invitation to guess.
