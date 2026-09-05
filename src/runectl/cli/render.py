"""stdout=NDJSON (off-TTY default); stderr=human render (D4, D13, plan §9.2).

Each function is a pure function of one event — it may never read run state
directly (D4). This is the "skeleton ships a plain but correct renderer"
version (plan §9.3); the compact/foldable/width-aware human render is M9
polish over the same event stream.
"""

from __future__ import annotations

import sys

from runectl.trace.events import Event


def render_ndjson(event: Event) -> None:
    print(event.model_dump_json(), file=sys.stdout, flush=True)


def render_human(event: Event) -> None:
    payload = event.payload()
    print(f"[{event.seq:>4}] {event.type:<18} {payload}", file=sys.stderr)
