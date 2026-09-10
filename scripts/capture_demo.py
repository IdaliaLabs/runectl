"""Capture the demo as SVG frames, headlessly (Phase 5's "record the demo").

Drives `RunectlTUI` through a finished run's playback under Textual's headless
test harness and exports one SVG per frame. No terminal, no TTY, no recording
tool, and — like everything else in `tests/` and `scripts/seed_demo.py` — no
Docker daemon, no API key and no spend.

SVG rather than GIF on purpose: it is text, so it diffs, it stays sharp at any
width, and it needs no binary in the repo. Run `scripts/seed_demo.py` first (or
just `make demo-frames`, which does both).

Usage:
    RUNECTL_HOME=... uv run python scripts/capture_demo.py <run_id> <out_dir>
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from runectl.cli.tui.app import RunectlTUI

# Wide enough that the timeline's command lines are not clipped, tall enough
# that all four detail panes carry content in the same frame.
_SIZE = (140, 46)

# Frames are captured on a fixed cadence rather than on specific events: the
# playback worker owns the pacing, and reaching into it to hook events would
# couple this script to the app's internals for no gain in the picture.
_FRAME_COUNT = 8
_SETTLE_S = 0.35


async def _capture(run_id: str, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    app = RunectlTUI(replay_run_id=run_id, playback_delay_s=0.05)
    written: list[Path] = []
    async with app.run_test(size=_SIZE) as pilot:
        for frame in range(_FRAME_COUNT):
            await pilot.pause()
            await asyncio.sleep(_SETTLE_S)
            await pilot.pause()
            path = out_dir / f"frame-{frame:02d}.svg"
            path.write_text(app.export_screenshot())
            written.append(path)
    return written


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    run_id, out_dir = sys.argv[1], Path(sys.argv[2])
    for path in asyncio.run(_capture(run_id, out_dir)):
        print(path)


if __name__ == "__main__":
    main()
