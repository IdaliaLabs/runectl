# The practice suite

Ten challenges, each a directory holding three files:

```
chal.toml        # what a run is given: name, category, description, files, flag_format
expected.json    # the known-correct flag — for scoring only
PROVENANCE.md    # where it came from, its license, and what was deliberately not vendored
files/           # the challenge's own inputs, byte for byte from upstream
```

Score it with `runectl bench run --suite bench/practice --model <id>`.

**Most of these challenges are not ours.** Nine of the ten are vendored from
[`csivitu/ctf-challenges`](https://github.com/csivitu/ctf-challenges) under the MIT
license; the required copyright and license text is in
[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) and each challenge's
`PROVENANCE.md` credits its original author. For those, only the `chal.toml`,
`expected.json` and `PROVENANCE.md` wrappers are this project's. The exception is
`easy-08` (`stream-secret`, network), which is original Idalia work under this
repo's Apache-2.0 LICENSE — there was no network challenge to vendor, and its
`PROVENANCE.md` states the honesty cost of scoring a self-authored challenge.

## The gate

V1 is **2 solved with 0 false flags** over the gated subset (`docs/ARCHITECTURE.md` D15). The report says
whether it is met. A wrong flag that was *finalized* fails the command outright with
exit 1; a wrong flag the judge *held* for approval does not, because holding it is the
false-flag subsystem doing its job.

The other number in the report is D16's progress ratio — `progress_steps / steps_used`
across the suite. It is there to keep the solve rate honest: a high solve rate bought by
brute-forcing every step is not the thing being aimed at, and the budgets in the category
TOMLs are meant to be tuned against this number rather than against a step count.

## What is in it, and what that biases

| | challenge | category | technique | gate |
|---|---|---|---|---|
| easy-01 | quick-math | crypto | Håstad's broadcast attack + CRT | outside |
| easy-02 | modern-clueless-child | crypto | repeating-key XOR with an obfuscation character | ✓ |
| easy-03 | rivest-shamir-adleman | crypto | RSA with one small prime — factor it | ✓ |
| easy-04 | machine-fix | misc | read a program that cannot finish, derive its closed form | ✓ |
| easy-05 | little-rsa | crypto | tiny-RSA recovery, then the plaintext is a zip password | ✓ |
| easy-06 | esrever | rev | invert a permutation + XOR + Caesar chain | ✓ |
| easy-07 | gradient-sky | forensics | archive appended after JPEG image data — carve it | ✓ |
| easy-08 | stream-secret | network | reassemble a TCP stream split across segments | ✓ |
| easy-09 | pwn-intended-0x1 | pwn | overflow an adjacent variable to trigger the flag print | outside |
| easy-10 | flying-places | osint | trace a photo to its source and a commenter's city | outside |

**M7 (2026-09-09) rebalanced the suite** from five challenges (four crypto) to ten,
adding one case each for `rev`, `forensics`, `network`, `pwn` and `osint`. Three
categories still cannot be measured offline: `web` needs a live service the sandbox can't
host, and `pwn`/`osint` are present but scored *outside the gate* for the structural
reasons in their `expected.json` (see below). So the gated subset now spans crypto, misc,
rev, forensics and network — a much broader statement than "cryptography and reasoning,"
but still not the full eight.

**`easy-01` is a known-unfair gate.** Two models solved its actual cryptography and then
both failed at guessing how to render the recovered integer as a flag, which is trivia
about one author's encoding habit rather than a capability. It is kept in the suite
because removing a challenge because the tool fails it is how a benchmark stops meaning
anything — but read a failure there with that in mind. `easy-02` onward were chosen for
the property it lacks: **an answer that is self-evidently correct**, so a solver that has
it knows it has it.

## Rules for adding one

1. **MIT or CC licensed, with the commit pinned** in `PROVENANCE.md` — or original
   Idalia work under this repo's Apache-2.0 license. No other licenses; this repo is
   public. An authored challenge (like `easy-08`) is a last resort for a category with
   nothing vendorable, and its `PROVENANCE.md` must state the honesty cost of scoring a
   self-authored challenge.
2. **Vendor the sources block, never the walkthrough.** Upstream READMEs usually contain a
   full exploit. Including it measures reading comprehension, not solving, and it puts an
   answer key inside challenge data — the same failure `docs/ARCHITECTURE.md` D10 forbids in
   solver code.
3. **Re-derive the flag independently** before writing `expected.json`, rather than
   copying it out of the README. Every flag in this suite was.
4. **Prefer a self-verifiable answer.** Either it is readable English, or the sandbox can
   check it (`easy-04`'s closed form can be validated against its own brute force for
   small inputs).
5. **No live services.** A challenge that needs a netcat listener, a Discord bot, or a web
   server is not runnable in the offline sandbox and does not belong here yet.

## Cases scored outside the gate

`expected.json` may carry `"gate": false` with a `gate_note`. Such a case runs
normally, appears in the report marked `[outside gate]` with its reason, and
still counts in the headline solve rate and false-flag count — only the V1
pass/fail gate (2 solved, 0 false flags) reads the narrower subset. `load_suite`
rejects an exclusion with no `gate_note`.

Three cases use it, each for a different structural reason — none of them "the
tool finds it hard":

- **`easy-01` (`quick-math`, crypto)**, since 2026-09-08. Its failure is a
  capability failure shaped like a false flag: the run does the Håstad broadcast
  attack correctly and submits the recovered value one transformation short of
  the flag, so every check the tool has agrees with it — correctly. Gating on it
  would make "0 false flags" unmeetable for a reason unrelated to false-flag
  defense.
- **`easy-09` (`pwn-intended-0x1`, pwn)**, since 2026-09-09. A real overflow, but
  in a single-container sandbox the flag file it prints is directly readable by
  the agent's own shell — there is no privilege boundary as a remote service has,
  so a "solve" can't distinguish exploitation from a plain `cat`. Faithful pwn
  scoring needs a boundary the agent does not already own (a V2 harness concern).
- **`easy-10` (`flying-places`, osint)**, since 2026-09-09. Not solvable from the
  provided file: its answer threads through live social-media state that has
  rotted. A gate case must be deterministic and reproducible; this depends on the
  past internet.

All three still run, still appear in the report marked `[outside gate]`, and
still count in the headline solve rate and false-flag count. Only the pass/fail
gate reads the narrower subset.
