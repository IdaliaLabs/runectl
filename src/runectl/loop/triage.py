"""Deterministic pre-LLM triage (D10, plan §5.3).

``triage()`` takes only a sandbox and a category — never the challenge name,
its filenames, or its description. That is the structural guarantee behind
the honesty rule (`POSTMORTEM.md` §2): a filename- or description-gated
fast-path has nowhere to live, because triage cannot see any of that. A unit
test asserts the command set is identical across two runs differing only in
challenge name/files (see tests/unit/test_triage.py).
"""

from __future__ import annotations

from runectl.categories.schema import Category
from runectl.sandbox.base import Sandbox
from runectl.trace.events import TriageResult

_BASE_COMMANDS: tuple[str, ...] = (
    "ls -la /ctf/",
    "file /ctf/* 2>/dev/null",
)

# Fixed, category-parameterized additions — keyed only by category name, never
# by anything about the specific challenge.
_CATEGORY_COMMANDS: dict[str, tuple[str, ...]] = {
    "pwn": ("checksec --file=/ctf/* 2>/dev/null",),
    "rev": ("checksec --file=/ctf/* 2>/dev/null",),
    "forensics": ("exiftool -a -u -g1 /ctf/* 2>/dev/null",),
    "misc": ("strings -a -n 8 /ctf/* 2>/dev/null | head -60",),
    "network": ("capinfos /ctf/* 2>/dev/null",),
}


def commands_for(category: Category) -> tuple[str, ...]:
    return _BASE_COMMANDS + _CATEGORY_COMMANDS.get(category.name, ())


def triage(sandbox: Sandbox, category: Category) -> TriageResult:
    commands = commands_for(category)
    findings: dict[str, str] = {}
    for command in commands:
        result = sandbox.exec(command, timeout_s=15)
        findings[command] = result.stdout if result.ok else result.stderr
    return TriageResult(category=category.name, commands=list(commands), findings=findings)
