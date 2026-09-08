# The practice suite

Five challenges, each a directory holding three files:

```
chal.toml        # what a run is given: name, category, description, files, flag_format
expected.json    # the known-correct flag — for scoring only
PROVENANCE.md    # where it came from, its license, and what was deliberately not vendored
files/           # the challenge's own inputs, byte for byte from upstream
```

Score it with `runectl bench run --suite bench/practice --model <id>`.

## The gate

V1 is **2 of 5 solved with 0 false flags** (`DECISIONS.md` D15). The report says
whether it is met. A wrong flag that was *finalized* fails the command outright with
exit 1; a wrong flag the judge *held* for approval does not, because holding it is the
false-flag subsystem doing its job.

The other number in the report is D16's progress ratio — `progress_steps / steps_used`
across the suite. It is there to keep the solve rate honest: a high solve rate bought by
brute-forcing every step is not the thing being aimed at, and the budgets in the category
TOMLs are meant to be tuned against this number rather than against a step count.

## What is in it, and what that biases

| | challenge | category | technique |
|---|---|---|---|
| easy-01 | quick-math | crypto | Håstad's broadcast attack + CRT |
| easy-02 | modern-clueless-child | crypto | repeating-key XOR with an obfuscation character |
| easy-03 | rivest-shamir-adleman | crypto | RSA with one small prime — factor it |
| easy-04 | machine-fix | misc | read a program that cannot finish, derive its closed form |
| easy-05 | little-rsa | crypto | tiny-RSA recovery, then the plaintext is a zip password |

**It is crypto-heavy, and that is a limitation, not a design.** Only `crypto`, `misc` and
`web` ship as categories at V1, and a web challenge needs a live service the sandbox has
no way to host offline. When M7 lands the other five categories, the suite needs
rebalancing — a solve rate measured here is a statement about cryptography and reasoning,
not about `runectl` across all eight categories.

**`easy-01` is a known-unfair gate.** Two models solved its actual cryptography and then
both failed at guessing how to render the recovered integer as a flag, which is trivia
about one author's encoding habit rather than a capability. It is kept in the suite
because removing a challenge because the tool fails it is how a benchmark stops meaning
anything — but read a failure there with that in mind. `easy-02` onward were chosen for
the property it lacks: **an answer that is self-evidently correct**, so a solver that has
it knows it has it.

## Rules for adding one

1. **MIT or CC licensed, with the commit pinned** in `PROVENANCE.md`. No exceptions —
   this repo is public.
2. **Vendor the sources block, never the walkthrough.** Upstream READMEs usually contain a
   full exploit. Including it measures reading comprehension, not solving, and it puts an
   answer key inside challenge data — the same failure `DECISIONS.md` D10 forbids in
   solver code.
3. **Re-derive the flag yourself** before writing `expected.json`, rather than copying it
   out of the README. Every flag in this suite was.
4. **Prefer a self-verifiable answer.** Either it is readable English, or the sandbox can
   check it (`easy-04`'s closed form can be validated against its own brute force for
   small inputs).
5. **No live services.** A challenge that needs a netcat listener, a Discord bot, or a web
   server is not runnable in the offline sandbox and does not belong here yet.
