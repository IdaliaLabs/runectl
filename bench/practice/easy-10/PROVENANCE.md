# Provenance — easy-10 (flying-places)

- **Source:** [`csivitu/ctf-challenges`](https://github.com/csivitu/ctf-challenges), `osint/Flying Places/`
- **Commit:** `33ef02b4ecec332c6e7d5f5511a930747645ba8f` (master, fetched 2026-09-09)
- **License:** MIT (Computer Society of India — VIT University, 2020)
- **Author credit:** [parthkgh24](https://github.com/parthkgh24), from the original challenge README
- **Original challenge:** `csictf` 2020, category "osint" ("Flying Places").

## What was and was not vendored

`files/Flight.jpg` is the upstream image byte for byte. `chal.toml`'s
`description` reproduces the prompt. The exploit write-up is **not** included.

## Why this is scored outside the gate

See `expected.json`'s `gate_note`. The answer is not derivable from the file:
it depends on live social-media state (a Twitter post, a commenter's listed
city) that has rotted and cannot be reproduced offline. Every osint challenge in
the MIT source shares this property, so none could be a deterministic gate case.
The category still ships as data and still runs; the bench simply does not gate
on a challenge whose ground truth lives on the past internet.

## Verified

The flag is the upstream challenge's published solution (README), format-checked
against `flag_format`. It is **not** independently reproducible offline, which is
exactly why the case sits outside the gate.
