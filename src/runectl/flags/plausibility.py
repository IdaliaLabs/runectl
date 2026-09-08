"""Plausibility filtering (D11/D15): does this even look like a real flag?

Cheap, deterministic rejections that run before anything expensive. Every
pattern here is a shape the predecessor actually submitted or nearly submitted:
the template from a challenge's own README, a UUID scraped from a page, a
fragment of JSON, a capture-interface id from tshark output.

Kept conservative on purpose. A false *rejection* costs one more step; a false
*acceptance* costs the challenge, and under competition scoring a wrong flag is
worse than no flag at all (D15 mechanism 5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Placeholder text that ships inside challenge descriptions and writeups.
_PLACEHOLDER_CORES = (
    "flag", "your_flag_here", "yourflaghere", "redacted", "example",
    "xxx", "xxxx", "todo", "changeme", "placeholder", "...",
)

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_JSON_FRAGMENT = re.compile(r'^[\s{\[]|["\']\s*:\s*|,\s*$')
_CAPTURE_IFACE = re.compile(r"(?i)^(?:capture|interface|iface|dev)[-_ ]?\d+$")
_CORE = re.compile(r"^[^{]*\{(?P<core>.*)\}[^}]*$", re.DOTALL)


@dataclass(frozen=True)
class PlausibilityVerdict:
    plausible: bool
    reason: str = ""


def flag_core(flag: str) -> str:
    """The payload inside the wrapper, or the whole string if unwrapped."""
    match = _CORE.match(flag)
    return match.group("core") if match else flag


def assess(flag: str) -> PlausibilityVerdict:
    candidate = flag.strip()
    if not candidate:
        return PlausibilityVerdict(False, "empty")
    if len(candidate) > 200:
        return PlausibilityVerdict(False, "implausibly long for a flag")
    if "\n" in candidate:
        return PlausibilityVerdict(False, "spans multiple lines")

    core = flag_core(candidate).strip()
    if not core:
        return PlausibilityVerdict(False, "empty flag body")
    if core.lower() in _PLACEHOLDER_CORES:
        return PlausibilityVerdict(False, f"placeholder body {core!r}, not a real flag")
    if _UUID.match(core):
        return PlausibilityVerdict(False, "looks like a UUID, not a flag")
    if _CAPTURE_IFACE.match(core):
        return PlausibilityVerdict(False, "looks like a capture-interface id")
    if _JSON_FRAGMENT.search(candidate):
        return PlausibilityVerdict(False, "looks like a JSON fragment")
    return PlausibilityVerdict(True)
