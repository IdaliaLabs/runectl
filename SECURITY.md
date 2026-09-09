# Security

`runectl` executes model-authored shell commands against CTF challenge material.
Both halves of that sentence are untrusted input. This file says what the
containment boundary actually is, what it is not, and how to report a problem.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/IdaliaLabs/runectl/security/advisories/new)
on this repository. Please do not open a public issue for anything that would
give someone else a working attack before there is a fix.

There is no dedicated security mailbox yet. When there is, it will be listed
here and at `/.well-known/security.txt` on the project's site.

Expect an acknowledgement within a few days. This is a solo project, so that is
a statement of intent rather than a service level agreement.

## Threat model

**What the sandbox is for.** The container is a *containment* boundary for two
things that are expected to misbehave: commands an LLM wrote, and files a CTF
author wrote. It exists so that a wrong command destroys a disposable container
rather than your working tree, and so that a challenge binary runs somewhere
other than your laptop's filesystem.

**What the sandbox is not.** It is Docker. Docker is not a hardened boundary
against a determined attacker with a kernel exploit, and this project does not
pretend otherwise. Do not treat `runectl run` as safe for material you actually
believe is hostile — malware samples, an untrusted binary from a stranger, a
challenge from a source you would not run code from. For those, put a virtual
machine between the container and your host.

**What is done, concretely**, per run (`src/runectl/sandbox/docker.py`):

| | |
|---|---|
| One container per run | Named for the run id, removed on stop; no state carries between runs |
| Non-root, verified | `USER ctf` in the image, and the run **refuses to start** if `id -u` returns 0 — the check exists because `arena ensure --from-file/--from-registry` let an arbitrary image become the arena |
| `no-new-privileges` | Set as a `security_opt` |
| Not privileged | `privileged=False`, no added capabilities, no device or socket mounts |
| Resource caps | 2 GB memory, 2 CPUs, 512 pids |
| Network off by default *where it can be* | `crypto` and `misc` run with `network=none`. The other six — `web` plus the five M7 categories `pwn`/`rev`/`forensics`/`osint`/`network` — default to `bridge` (2026-09-09): remote targets and live lookups want egress, and offline work is one `--network none` away. Set per category, overridable with `--network` |
| No host mounts | Challenge files are copied in; the host filesystem is not bind-mounted into the container |

**What is not done.** No seccomp or AppArmor profile beyond Docker's default.
No user namespace remapping. No egress filtering when the network is on — a
`web` run can reach anything your host can reach. If any of these matter for
your use, run the whole thing inside a VM.

## Secrets

- **API keys are never a CLI-only path.** Precedence is `--api-key` > environment
  > OS keyring > `~/.config/runectl/keys.json`. The keyring is the intended
  route (`runectl keys set`); the JSON fallback is created `0600` atomically,
  never written wide and then narrowed.
- **Keys never reach the sandbox.** The container gets `TERM` and nothing else.
  The agent inside it cannot read your credentials because they are not there.
- **Traces are redacted on write.** Every event passes through a redaction hook
  before it hits disk, matching common provider key shapes. It is deliberately
  over-eager — a false positive costs nothing, a leaked key costs a lot. It is
  a backstop, not a guarantee: it matches patterns it knows about.

## Traces are not automatically safe to publish

A trace records the challenge description, every command run, and every model
response. That is the point — it is the product's evidence trail — but it means
a trace can contain challenge material you do not have the right to redistribute,
and any secret that appeared in command output and did not match a redaction
pattern. Read one before you attach it to a public issue.

## Scope

In scope: sandbox escape or containment bypass, secret leakage into traces or
logs, a path that executes model-authored code outside the container, anything
that lets challenge material reach the host filesystem.

Out of scope: the agent writing a bad or destructive command *inside* its own
container (that is the container's job), the model producing a wrong flag (see
`DECISIONS.md` D15), and the cost of API calls you authorized.
