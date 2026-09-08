"""Decoy detection (D15 mechanism 3): was this planted to be found?

Challenge authors plant fake flags. Two shapes matter:

- The **source** looks like bait — a path or filename saying `decoy`/`fake`/
  `honey`, or text near the hit saying "nice try" or "this is not the flag".
- The candidate **came from the challenge description**. A token the author
  pasted into the prompt is a lure or a format example; echoing it back as a
  discovery is not solving anything. This is the same class of error as the
  provenance laundering seen in the first real run — the flag is real text the
  agent read somewhere, just not evidence of a solve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Not `\b`: the shapes that actually occur are `decoy_flag.txt`, `fake_flag`,
# `honeypot_2` — and `\b` does not fire before an underscore, because `_` is a
# word character. Alphanumeric lookarounds treat `_`, `.` and `-` as the
# separators they are here, while still refusing to match inside `fakeroot`.
_DECOY_PATH = re.compile(
    r"(?i)(?<![a-z0-9])(?:decoy|fake|honey(?:pot)?|not_?the_?flag|dummy|bait|red_?herring)(?![a-z0-9])"
)
_DECOY_TEXT = re.compile(
    r"(?i)(nice try|this is not the flag|not the real flag|keep looking|"
    r"wrong flag|try again|almost|so close)"
)
# How much text around the hit to inspect for a taunt.
_WINDOW = 200


@dataclass(frozen=True)
class DecoyVerdict:
    is_decoy: bool
    reason: str = ""


def assess(flag: str, *, source_output: str, source_command: str, description: str) -> DecoyVerdict:
    if _DECOY_PATH.search(source_command):
        return DecoyVerdict(True, "the command that produced it names a decoy path")

    index = source_output.find(flag)
    if index != -1:
        window = source_output[max(0, index - _WINDOW) : index + len(flag) + _WINDOW]
        if _DECOY_PATH.search(window):
            return DecoyVerdict(True, "found next to a decoy-named file or path")
        taunt = _DECOY_TEXT.search(window)
        if taunt:
            return DecoyVerdict(True, f"found next to decoy text {taunt.group(0)!r}")

    if flag and description and flag in description:
        return DecoyVerdict(
            True,
            "this string appears verbatim in the challenge description — it is the "
            "author's example or a planted lure, not something you discovered",
        )
    return DecoyVerdict(False)
