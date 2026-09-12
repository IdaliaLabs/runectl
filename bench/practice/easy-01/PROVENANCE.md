# Provenance — easy-01 (quick-math)

- **Source:** [`csivitu/ctf-challenges`](https://github.com/csivitu/ctf-challenges), `crypto/Quick Math/README.md`
- **Commit:** `33ef02b4ecec332c6e7d5f5511a930747645ba8f` (master, fetched 2026-09-05)
- **License:** MIT (Computer Society of India — VIT University, 2020) — see the
  repo's `LICENSE` file
- **Author credit:** [ashikka](https://github.com/ashikka), from the original
  challenge README
- **Original challenge:** `csictf` 2020, category "crypto" ("Quick Math") —
  Hastad's broadcast attack + Chinese Remainder Theorem over 3 RSA moduli/
  ciphertexts sharing `e=3`.

`chal.toml` re-states the challenge's own description text (the "Sources"
block from the original README) as `description`; `expected.json` carries the
known-correct flag for scoring only — it is never read by any
solver code path (D10's zero-answer-key rule applies to challenge data the
same as to solver logic).

Picked for the M4 skeleton gate ("one easy challenge is vendored
now") because it is fully self-contained — the entire input is the challenge
description text itself, no binary files, no live network service — so it
exercises `runectl run --challenge` without needing anything beyond the arena
image's Python stack.
