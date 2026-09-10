"""`runectl tui` — an interactive, in-terminal view over runs (Phase 4, D13 amendment).

This package is `cli/` code under every existing rule that phrase carries: it
renders, it never contains agent logic, and it is the only thing in the
product allowed to hold a live redraw loop (the D13 amendment names this
package specifically). It never runs the agent loop in-process — every run
this UI starts is a separate `runectl run --output jsonl` subprocess, read
exactly the way any other driving agent reads it (D4's stdout-NDJSON
contract). See `runner_proc.py`'s module docstring for why that boundary is
load-bearing, not incidental.
"""

from __future__ import annotations
