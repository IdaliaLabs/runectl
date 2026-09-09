# Provenance — easy-08 (stream-secret)

- **Source:** Original work, authored for this repository — **not vendored**.
- **Commit:** not applicable — this challenge was authored here, not fetched from
  an upstream commit.
- **License:** Apache-2.0 (Idalia Labs), the same license as the rest of `runectl`.
  It is therefore **not** listed in `bench/THIRD_PARTY_LICENSES.md`, which covers
  only third-party MIT material.

## Why an authored challenge

The MIT source used for the rest of the suite (`csivitu/ctf-challenges`) has no
network/pcap challenge, and no `network` category at all. Rather than pull a
second upstream with its own licensing, the network category is exercised by a
small capture written here. The honesty cost is stated plainly: a challenge the
authors wrote and then solve is weaker evidence than a real competition capture —
a reader can object that it was built to be winnable. It is included because the
*skill* it tests is faithful and unshortcut-able (below), and the bench write-ups
name it as authored.

## What it is

A minimal, well-formed libpcap (Ethernet/IPv4/TCP, 8 packets: a full three-way
handshake, an HTTP `GET /secret`, and a two-segment HTTP `200` response). The
flag is deliberately **split across two TCP segments**, so a naive
`strings capture.pcap | grep flag` does **not** find it whole — only TCP stream
reassembly (`tshark -qz 'follow,tcp,ascii,0'`, or `--export-objects http`)
recovers the contiguous flag. That is precisely the network playbook's core
move (triage the protocol hierarchy, follow the conversation that carries
payload), which is why an authored capture is an honest test of it.

## Verified

Built and checked on 2026-09-09 by an independent reader (`make_pcap.py` /
`verify_pcap.py`, kept out of the vendored dir): the capture parses as libpcap
with 8 packets, the whole flag string is **absent** from the raw file bytes, and
the reassembled server→client stream contains `flag{f0ll0w_th3_tcp_str34m}`.
Live confirmation that the arena's `tshark` follows it is a Phase-8 re-bench step.
