# Provenance — easy-06 (esrever)

- **Source:** [`csivitu/ctf-challenges`](https://github.com/csivitu/ctf-challenges), `reversing/Esrever/`
- **Commit:** `33ef02b4ecec332c6e7d5f5511a930747645ba8f` (master, fetched 2026-09-09)
- **License:** MIT (Computer Society of India — VIT University, 2020)
- **Author credit:** [roerohan](https://github.com/roerohan), from the original challenge README
- **Original challenge:** `csictf` 2020, category "reversing" ("Esrever").

## What was and was not vendored

`files/esrever.py` and `files/esrever.txt` are the upstream files byte for byte.
`chal.toml`'s `description` reproduces the README's prompt only. The exploit
write-up and the vendored `solve.py` are **not** included.

## Why this challenge

The suite was crypto-heavy and had no reversing case. Esrever is a clean,
offline reversing problem: a deterministic (despite its random-looking) encoding
chain — two position permutations, an XOR against a recovered key, and a Caesar
shift — that must be inverted. There is no shortcut; the flag is not present in
either provided file (the script ships a placeholder `csictf{fake_flag}`), so a
run has to actually reverse the transform.

## Verified

The flag in `expected.json` was **re-derived locally on 2026-09-09**, not copied
from the README: a clean-room inverse (independent of the vendored `solve.py`)
recovered the XOR key `insovietrussiapikachucatchesyou` by inverting `enc4`
twice, undid the two `enc3` permutations and the `enc2` XOR, brute-forced the
net Caesar shift, and landed on the documented intermediate
`csictfaesreverisjustreverseinreverserightc` — from which the braces (mapped to
`a`/`c` by the forward `enc1`) reinsert to give the flag above.
