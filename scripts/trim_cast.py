"""Cut a time window out of an asciinema cast, rebasing it to start at zero.

`make demo-video` records one generous take and cuts the README's clips out of
it. Trimming the cast rather than the rendered GIF keeps it to a single `agg`
encode: a GIF re-encode either loses colours or needs a palettegen/paletteuse
pair, and neither is worth it when the source is a text format.

A cast frame only carries what *changed*, so a window that starts mid-stream
would open on a blank terminal. Every frame before `start` is therefore
replayed into one synthetic frame at t=0, which reconstructs the screen as it
stood when the window opens.

`--frame-before-toast` is the other half of the same job: `make demo-video`
cuts the README screenshot from the same cast, and the frame worth cutting is
the one where the timeline is complete but the TUI's own "playback complete"
toast has not yet covered the exit-code line. This prints the window length
that ends there, so the Makefile need not hardcode one that drifts with every
re-recording.

Usage:
    uv run python scripts/trim_cast.py <in.cast> <out.cast> <start_s> <duration_s>
    uv run python scripts/trim_cast.py --frame-before-toast <in.cast>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def trim(src: Path, dst: Path, start: float, duration: float) -> int:
    lines = src.read_text().splitlines()
    header = lines[0]
    frames = [json.loads(line) for line in lines[1:] if line.startswith("[")]

    preamble = "".join(f[2] for f in frames if f[0] < start and f[1] == "o")
    window = [f for f in frames if start <= f[0] < start + duration]

    out = [header]
    if preamble:
        out.append(json.dumps([0.0, "o", preamble]))
    for ts, kind, data in window:
        out.append(json.dumps([round(ts - start, 6), kind, data]))
    dst.write_text("\n".join(out) + "\n")
    return len(window)


_TOAST = "playback complete"
_GAP_S = 0.05


def frame_before_toast(src: Path) -> float:
    """Window length that ends just before the playback-complete toast.

    Returned as a duration rather than a timestamp because the caller re-cuts
    the cast to it: seeking the rendered GIF instead does not work, since agg
    coalesces frames and the toast's frame spans the moment worth capturing.

    Falls back to the full cast when no toast was captured — a cast cut short
    of the run finishing has no toast to avoid.
    """
    frames = [json.loads(line) for line in src.read_text().splitlines()[1:] if line.startswith("[")]
    toast = next((f[0] for f in frames if _TOAST in f[2]), None)
    if toast is None:
        return round(frames[-1][0], 3)
    return round(max(0.0, toast - _GAP_S), 3)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--frame-before-toast":
        print(frame_before_toast(Path(sys.argv[2])))
        return
    if len(sys.argv) != 5:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    count = trim(src, dst, float(sys.argv[3]), float(sys.argv[4]))
    print(f"{dst}: {count} frames")


if __name__ == "__main__":
    main()
