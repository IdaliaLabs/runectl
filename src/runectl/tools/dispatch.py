"""Maps a tool call to a sandbox action (D7, plan §4.2-4.4).

Handles the four tools that touch the sandbox: ``run_command``, ``run_gdb``,
``write_file``, ``search_flag``. ``submit_flag`` never reaches here — it
doesn't touch the sandbox at all, so the runner (loop/runner.py) intercepts it
directly and hands it to ``flags.judge`` instead (see that module's docstring
for why this is a deliberate, minimal seam rather than the full D15 subsystem).

Preflight stays thin on purpose (plan §4.3): trim, reject empty, apply a
timeout. No fat auto-install logic — tooling is the arena image's job
(D2/`arena/Dockerfile`); a missing tool surfaces as a normal command failure,
not something dispatch tries to silently self-heal.
"""

from __future__ import annotations

import shlex
import time
from typing import Any

from runectl.sandbox.base import ExecResult, Sandbox
from runectl.tools.results import ToolResult

_DEFAULT_TIMEOUT_S = 30
_LONG_RUNNING_TIMEOUT_S = 120


def _blocked(reason: str) -> ToolResult:
    return ToolResult(ok=False, kind="blocked", stderr=reason)


def _from_exec(result: ExecResult) -> ToolResult:
    return ToolResult(
        ok=result.ok,
        kind="output" if result.ok else "error",
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_s=result.duration_s,
        truncated=result.truncated,
    )


class ToolDispatcher:
    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox

    def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        handler = {
            "run_command": self._run_command,
            "run_gdb": self._run_gdb,
            "write_file": self._write_file,
            "search_flag": self._search_flag,
        }.get(name)
        if handler is None:
            return _blocked(f"unknown or unsupported tool for dispatch: {name!r}")
        return handler(arguments)

    def _run_command(self, arguments: dict[str, Any]) -> ToolResult:
        command = str(arguments.get("command", "")).strip()
        if not command:
            return _blocked("run_command: empty command")
        timeout_s = _LONG_RUNNING_TIMEOUT_S if arguments.get("long_running") else _DEFAULT_TIMEOUT_S
        return _from_exec(self._sandbox.exec(command, timeout_s=timeout_s))

    def _run_gdb(self, arguments: dict[str, Any]) -> ToolResult:
        binary_path = str(arguments.get("binary_path", "")).strip()
        gdb_commands = arguments.get("gdb_commands") or []
        if not binary_path:
            return _blocked("run_gdb: missing binary_path")
        if not isinstance(gdb_commands, list) or not gdb_commands:
            return _blocked("run_gdb: missing gdb_commands")
        ex_flags = " ".join(f"-ex {shlex.quote(str(c))}" for c in gdb_commands)
        command = f"gdb -q -batch {ex_flags} {shlex.quote(binary_path)}"
        return _from_exec(self._sandbox.exec(command, timeout_s=_LONG_RUNNING_TIMEOUT_S))

    def _write_file(self, arguments: dict[str, Any]) -> ToolResult:
        filename = str(arguments.get("filename", "")).strip()
        content = arguments.get("content")
        if not filename:
            return _blocked("write_file: empty filename")
        if ".." in filename.split("/") or filename.startswith("/"):
            return _blocked(f"write_file: path traversal rejected: {filename!r}")
        if not isinstance(content, str):
            return _blocked("write_file: content must be a string")
        start = time.monotonic()
        self._sandbox.write_file(filename, content.encode("utf-8"))
        return ToolResult(
            ok=True, kind="output", stdout=f"wrote {filename}", duration_s=time.monotonic() - start
        )

    def _search_flag(self, arguments: dict[str, Any]) -> ToolResult:
        pattern = str(arguments.get("flag_pattern", "")).strip()
        if not pattern:
            return _blocked("search_flag: empty flag_pattern")
        # -r recursive, -n line numbers (provenance, plan §4.4), -o only-matching, -I skip binaries
        command = f"grep -rnoIE {shlex.quote(pattern)} /ctf/ 2>/dev/null | head -200"
        return _from_exec(self._sandbox.exec(command, timeout_s=_DEFAULT_TIMEOUT_S))
