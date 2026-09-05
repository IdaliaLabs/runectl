"""The five tools, defined once; wire formats are derived per provider (D7, plan §3.1).

Carried from the predecessor's proven minimal surface (`PROMPT_ARCHIVE.md` §5,
`REBUILD_NOTES.md` §2 item 7): ``run_command`` is the workhorse, ``run_gdb`` is
batch-only so it can never hang the loop, ``write_file`` + ``run_command`` is the
exploit-script pattern, ``search_flag`` and ``submit_flag`` close the loop. A
sixth tool is added only if a category demonstrably can't be served by shell
(D7) — argued in `DECISIONS.md` first, not added quietly here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]  # a JSON-schema object: {"type","properties","required"}


TOOLS: tuple[ToolSchema, ...] = (
    ToolSchema(
        name="run_command",
        description="Run a bash command in the sandbox at /ctf/. The workhorse tool.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to run"},
                "reasoning": {"type": "string", "description": "Why you're running this"},
                "long_running": {
                    "type": "boolean",
                    "description": "True for slow tools (sqlmap/hashcat/gobuster/ffuf); raises the timeout",
                },
            },
            "required": ["command"],
        },
    ),
    ToolSchema(
        name="run_gdb",
        description="Run GDB in batch mode on a binary. Never hangs — no interactive sessions.",
        parameters={
            "type": "object",
            "properties": {
                "binary_path": {"type": "string", "description": "Path like /ctf/vuln"},
                "gdb_commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "GDB commands to run in sequence, batch mode",
                },
            },
            "required": ["binary_path", "gdb_commands"],
        },
    ),
    ToolSchema(
        name="write_file",
        description=(
            "Write a file directly to /ctf/ in the sandbox. Use for exploit scripts, "
            "solvers, payloads, config files. Immediately available to run_command."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename relative to /ctf/, e.g. exploit.py. No path traversal.",
                },
                "content": {"type": "string", "description": "Complete file content as a UTF-8 string"},
                "reasoning": {"type": "string", "description": "What this file does and why"},
            },
            "required": ["filename", "content"],
        },
    ),
    ToolSchema(
        name="search_flag",
        description="Recursively search /ctf/ for flag-shaped strings.",
        parameters={
            "type": "object",
            "properties": {
                "flag_pattern": {
                    "type": "string",
                    "description": "Regex or fixed prefix like picoCTF{",
                },
            },
            "required": ["flag_pattern"],
        },
    ),
    ToolSchema(
        name="submit_flag",
        description=(
            "Submit the flag once you can point to exactly where you observed it. "
            "Never guess — an unsupported guess is worse than no flag."
        ),
        parameters={
            "type": "object",
            "properties": {
                "flag": {"type": "string", "description": "The flag value, exactly as observed"},
                "how_found": {"type": "string", "description": "How you found it"},
            },
            "required": ["flag", "how_found"],
        },
    ),
)

TOOLS_BY_NAME: dict[str, ToolSchema] = {tool.name: tool for tool in TOOLS}


def to_openai(tools: Sequence[ToolSchema] = TOOLS) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]


def to_anthropic(tools: Sequence[ToolSchema] = TOOLS) -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
        for tool in tools
    ]


def to_google(tools: Sequence[ToolSchema] = TOOLS) -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
        for tool in tools
    ]
