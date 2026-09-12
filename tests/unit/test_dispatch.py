"""D7 tool dispatch: structured results, path-traversal rejection, empty-command
rejection."""

from __future__ import annotations

from runectl.sandbox.stub import StubSandbox, ok
from runectl.tools.dispatch import ToolDispatcher


def _dispatcher() -> ToolDispatcher:
    sandbox = StubSandbox(script={"echo hi": ok("hi")})
    sandbox.start()
    return ToolDispatcher(sandbox)


def test_run_command_empty_is_blocked() -> None:
    result = _dispatcher().dispatch("run_command", {"command": "   "})
    assert result.kind == "blocked"
    assert not result.ok


def test_run_command_dispatches_to_sandbox() -> None:
    result = _dispatcher().dispatch("run_command", {"command": "echo hi"})
    assert result.ok
    assert result.kind == "output"
    assert result.stdout == "hi"


def test_write_file_rejects_path_traversal() -> None:
    result = _dispatcher().dispatch("write_file", {"filename": "../../etc/passwd", "content": "x"})
    assert result.kind == "blocked"


def test_write_file_rejects_absolute_path() -> None:
    result = _dispatcher().dispatch("write_file", {"filename": "/etc/passwd", "content": "x"})
    assert result.kind == "blocked"


def test_unknown_tool_is_blocked() -> None:
    result = _dispatcher().dispatch("not_a_real_tool", {})
    assert result.kind == "blocked"
