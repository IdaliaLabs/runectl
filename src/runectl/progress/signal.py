"""Signal scoring (D8 mechanism 3): was what came back worth having?

Produces a numeric delta plus a coarse band. The bands matter more than the
number: `low` is the state the predecessor burned whole competitions in —
another 404 body, another empty decode, another "not found" — and it is what
the budgets in `budgets.py` count against.

Shared defaults apply to every category (D14); a category's TOML adds its own
`signal_low` / `signal_high` patterns on top rather than replacing them.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

SignalBand = Literal["none", "low", "high"]

# Patterns every category gets. Web's evidence shaped these; they are not
# web-only (D8, D14).
DEFAULT_LOW: tuple[str, ...] = (
    r"HTTP/\d(?:\.\d)? 40[034]",
    r"\bnot found\b",
    r"\bno such file\b",
    r"\bpermission denied\b",
    r"\bcommand not found\b",
    r"\bforbidden\b",
    r"\bconnection refused\b",
    r"\b0 results?\b",
)
DEFAULT_HIGH: tuple[str, ...] = (
    r"[A-Za-z0-9_]{2,}\{[^}]{3,}\}",          # anything flag-shaped
    r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[:=]",
    r"(?i)\bBEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY\b",
    r"(?i)\badmin\s*=\s*true\b",
    r"HTTP/\d(?:\.\d)? (?:200|301|302)",
)

# Deltas are relative weights, not currency. Tunable by bench (D16); the shape
# is what matters — repeats and low-signal are penalised, discovery is not.
DELTA_HIGH = 1.0
DELTA_NEUTRAL = 0.25
DELTA_LOW = -0.5
DELTA_REPEAT = 0.0
DELTA_ERROR = -0.25


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None  # bad category regex must not kill a run


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        compiled = _compiled(pattern)
        if compiled is not None and compiled.search(text):
            return True
    return False


def score(
    output: str,
    *,
    ok: bool,
    repeated: bool,
    low_patterns: tuple[str, ...] = (),
    high_patterns: tuple[str, ...] = (),
) -> tuple[float, SignalBand]:
    """Return ``(delta, band)`` for one tool result.

    A repeat fingerprint is never progress no matter how good it looks (D8
    mechanism 2) — you already had that information.
    """
    if repeated:
        return DELTA_REPEAT, "none"
    if _matches_any(output, DEFAULT_HIGH + high_patterns):
        return DELTA_HIGH, "high"
    if not output.strip():
        return DELTA_LOW, "low"
    if _matches_any(output, DEFAULT_LOW + low_patterns):
        return DELTA_LOW, "low"
    if not ok:
        return DELTA_ERROR, "low"
    return DELTA_NEUTRAL, "none"
