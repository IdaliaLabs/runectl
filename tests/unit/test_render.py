"""The human render must stay readable and stay a pure function of one event."""

from __future__ import annotations

from runectl.cli.render import _line, _tool_arg_summary
from runectl.trace.events import RunFinished, ToolCall, ToolResultEvent


def test_a_huge_command_is_clipped_to_one_line() -> None:
    """An exploit script is thousands of chars; the live view is one row."""
    payload = ToolCall(
        step=3,
        tool="run_command",
        arguments={"command": "python3 -c '" + "x = 1\n" * 500 + "'"},
    )

    line = _line(payload, width=100)

    assert line is not None
    assert "\n" not in line
    assert len(line) <= 100
    assert line.endswith("…")


def test_the_summary_picks_the_argument_that_says_what_happened() -> None:
    assert "gobuster" in _tool_arg_summary("run_command", {"command": "gobuster dir -u x"}, 80)
    assert "exploit.py" in _tool_arg_summary(
        "write_file", {"filename": "exploit.py", "content": "..."}, 80
    )


def test_output_is_flattened_not_dumped() -> None:
    payload = ToolResultEvent(
        step=1, tool="run_command", ok=True, kind="output",
        stdout="line one\nline two\nline three\n", stderr="",
        duration_s=0.5, truncated=False,
    )

    line = _line(payload, width=100)

    assert line is not None
    assert "\n" not in line
    assert "line one line two" in line


def test_the_final_line_reports_the_progress_ratio() -> None:
    payload = RunFinished(
        outcome="solved", flag="ctf{x}", steps_used=10, progress_steps=7,
        blocked_steps=1, cost_usd=0.0724, duration_s=66.0, exit_code=0,
    )

    line = _line(payload, width=100)

    assert line is not None
    assert "solved" in line and "ctf{x}" in line
    assert "70%" in line
    assert "$0.0724" in line
    assert "exit 0" in line


def test_uninteresting_events_render_nothing() -> None:
    """A tool-calling llm.response says less than the tool.call that follows."""
    from runectl.trace.events import LlmResponse, ToolCallSummary

    payload = LlmResponse(
        step=1, model="m", provider="anthropic", stop_reason="tool_use", text_chars=0,
        tool_call=ToolCallSummary(name="run_command", arguments={}),
        input_tokens=1, output_tokens=1, cost_usd=0.0,
    )

    assert _line(payload, width=100) is None
