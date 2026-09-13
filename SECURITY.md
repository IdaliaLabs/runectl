# Security

`runectl` executes model-authored shell commands against CTF challenge material. Both are
untrusted input. This file states the containment boundary, its limits, and the reporting
process.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/IdaliaLabs/runectl/security/advisories/new)
on this repository. Do not open a public issue for anything that would give an attacker a
working attack before a fix exists.

Email alternative: **hello@idalia.dev**, the address published at
[`idalia.dev/.well-known/security.txt`](https://idalia.dev/.well-known/security.txt)
(RFC 9116). It is not a dedicated security mailbox and reaches the same maintainer.

Acknowledgement target is a few days. This is a solo project; that is an intent, not an SLA.

## Scope

**In scope:** sandbox escape or containment bypass; secret leakage into traces or logs; any
path that executes model-authored code outside the container; any path that lets challenge
material reach the host filesystem.

**Out of scope:** the agent writing a destructive command *inside* its own container (that
is the container's purpose); the model producing a wrong flag (see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) D15); the cost of authorized API calls.

## Threat model

**Purpose of the sandbox.** Containment for two things expected to misbehave: commands an
LLM wrote, and files a CTF author wrote. A wrong command destroys a disposable container
rather than a working tree, and a challenge binary executes somewhere other than the host
filesystem.

**Limits of the sandbox.** It is Docker. Docker is not a hardened boundary against an
attacker with a kernel exploit. `runectl run` is not appropriate for material believed to be
hostile — malware samples, untrusted binaries, challenges from an unvetted source. Those
require a virtual machine between the container and the host.

**Applied per run** (`src/runectl/sandbox/docker.py`):

| Control | Detail |
|---|---|
| One container per run | Named for the run id, removed on stop; no state carries between runs |
| Non-root, verified | `USER ctf` in the image; the run refuses to start if `id -u` returns 0. The check exists because `arena ensure --from-file/--from-registry` allows an arbitrary image to become the arena |
| `no-new-privileges` | Set as a `security_opt` |
| Unprivileged | `privileged=False`; no added capabilities, no device or socket mounts |
| Resource caps | 2 GB memory, 2 CPUs, 512 pids |
| Network off where possible | `crypto` and `misc` run `network=none`. `web`, `pwn`, `rev`, `forensics`, `osint` and `network` default to `bridge` (2026-09-09) for remote targets and live lookups. Set per category, overridable with `--network` |
| No host mounts | Challenge files are copied in; the host filesystem is never bind-mounted |

**Not applied:** no seccomp or AppArmor profile beyond Docker's default; no user-namespace
remapping; no egress filtering when the network is on — a `web` run can reach anything the
host can reach. Any of these mattering means running the whole thing inside a VM.

## Secrets

- **Key precedence:** `--api-key` > environment > OS keyring > `~/.config/runectl/keys.json`.
  The keyring is the intended route (`runectl keys set`). The JSON fallback is created
  `0600` atomically, never written wide and then narrowed.
- **Keys never enter the sandbox.** The container receives `TERM` and nothing else. The
  agent cannot read credentials that are not present.
- **Traces are redacted on write.** Every event passes a redaction hook before reaching
  disk, matching common provider key shapes. It is deliberately over-eager: a false positive
  is free, a leaked key is not. It is a backstop that matches known patterns, not a
  guarantee.

## Traces are not automatically safe to publish

A trace records the challenge description, every command run, and every model response.
That is its purpose, and it means a trace can contain challenge material that is not
redistributable, plus any secret that appeared in command output without matching a
redaction pattern. Review a trace before attaching it to a public issue.
