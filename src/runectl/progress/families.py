"""Tactic families (D8 mechanism 1): what *kind* of thing is this command?

A family is the hypothesis class a command belongs to — `dirfuzz`, `decode`,
`disasm`. Budgets (D8 mechanism 4) are counted per family, so this
classification is what lets the loop say "you have tried four different
directory fuzzers and learned nothing; try a different class of idea."

Shared defaults live here and apply to every category equally (D14 — no
earner-first tuning). A category's TOML `[tactic_families]` table is merged
*over* these, so a category can add a family or sharpen one without losing the
shared set. Web's old heuristics informed the shape of these defaults; they
ship as defaults everyone gets, not as a special case for web.
"""

from __future__ import annotations

import re
from functools import lru_cache

UNCLASSIFIED = "other"

# Ordered: the first match wins, so put specific families above generic ones.
DEFAULT_FAMILIES: tuple[tuple[str, str], ...] = (
    ("dirfuzz", r"\b(ffuf|gobuster|wfuzz|dirb|dirsearch|feroxbuster)\b"),
    ("sqli", r"\b(sqlmap)\b|UNION\s+SELECT|\bOR\s+1=1\b|SLEEP\("),
    ("crack", r"\b(john|hashcat|fcrackzip|hydra|steghide|zip2john)\b"),
    ("pcap-filter", r"\b(tshark|tcpdump|capinfos|editcap)\b"),
    ("disasm", r"\b(gdb|objdump|radare2|r2|ropgadget|checksec|nm|readelf|angr)\b"),
    ("decode", r"\b(base64|base32|xxd|rot13|zlib|gzip|b64decode|unhexlify|fromhex)\b"),
    ("stego", r"\b(zsteg|steghide|exiftool|binwalk|foremost|stegsolve|zbarimg)\b"),
    ("strings", r"\b(strings|grep|rg|awk|sed)\b"),
    ("recon", r"\b(curl|wget|whatweb|nmap|dig|host)\b"),
    ("script", r"\b(python3?|perl|ruby|node|sage)\b"),
    ("inspect", r"\b(ls|file|cat|head|tail|stat|find|xxd)\b"),
)


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def classify(command: str, *, overrides: dict[str, str] | None = None) -> str:
    """Return the tactic family for a command.

    Category overrides are checked first so a category can claim a command the
    shared defaults would have classified more generically.
    """
    for name, pattern in (overrides or {}).items():
        try:
            if _compiled(pattern).search(command):
                return name
        except re.error:
            # A malformed regex in category data must not take a run down; the
            # loader validates shape, not regex compilability.
            continue
    for name, pattern in DEFAULT_FAMILIES:
        if _compiled(pattern).search(command):
            return name
    return UNCLASSIFIED
