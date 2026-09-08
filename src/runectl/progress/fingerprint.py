"""Output fingerprinting (D8 mechanism 2): has this actually changed anything?

Two runs of the same failing request differ only in a timestamp, a PID, a
heap address, or a `Date:` header. Hashing raw output would call those
different and score noise as progress. Normalising the volatile parts first
means a repeat is recognisable as a repeat — which is the whole basis for
"a repeat fingerprint is not progress".
"""

from __future__ import annotations

import hashlib
import re

# Each pattern replaces a volatile span with a fixed token. Deliberately
# aggressive: over-normalising makes two genuinely different outputs collide
# and costs one wasted "no progress" call, while under-normalising makes noise
# look like discovery on every single step.
_VOLATILE: tuple[tuple[re.Pattern[str], str], ...] = (
    # HTTP and log dates: "Date: Mon, 08 Sep 2026 03:34:07 GMT"
    (re.compile(r"(?i)^date:.*$", re.MULTILINE), "date:<D>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b"), "<TS>"),
    (re.compile(r"\b\w{3},\s+\d{1,2}\s+\w{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+\w+\b"), "<TS>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "<TIME>"),
    # Hex addresses and pointers
    (re.compile(r"\b0x[0-9a-fA-F]{4,}\b"), "<ADDR>"),
    # PIDs in common shapes
    (re.compile(r"(?i)\bpid[=: ]\s*\d+"), "pid=<PID>"),
    (re.compile(r"(?i)\bprocess \d+"), "process <PID>"),
    # Durations and elapsed times
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds)\b"), "<DUR>"),
    # Byte/size counters that move between otherwise identical runs
    (re.compile(r"(?i)\b\d+\s*bytes?\b"), "<BYTES>"),
    # Ephemeral ports and temp paths
    (re.compile(r"\b(?:127\.0\.0\.1|localhost):\d{2,5}\b"), "localhost:<PORT>"),
    (re.compile(r"/tmp/[\w.\-]+"), "/tmp/<TMP>"),
)

_WHITESPACE = re.compile(r"\s+")


def normalize(output: str) -> str:
    """Strip the parts that change between two identical attempts."""
    text = output
    for pattern, replacement in _VOLATILE:
        text = pattern.sub(replacement, text)
    return _WHITESPACE.sub(" ", text).strip()


def fingerprint(output: str) -> str:
    """A short stable digest of what a command actually produced."""
    return hashlib.sha256(normalize(output).encode("utf-8")).hexdigest()[:16]
