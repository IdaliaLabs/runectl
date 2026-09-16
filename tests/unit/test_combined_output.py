

def test_combined_output_shows_stderr_and_exit_code() -> None:
    """The model sees stdout AND stderr and a nonzero exit note, not stdout-or-stderr."""
    from runectl.loop.runner import _combined_output
    from runectl.sandbox.base import ExecResult

    failing = ExecResult(
        ok=False, stdout="partial\n", stderr="Traceback: boom\n", exit_code=2, duration_s=0.1
    )
    combined = _combined_output(failing)
    assert "partial" in combined and "Traceback" in combined and "[exit code 2]" in combined

    clean = ExecResult(ok=True, stdout="ok", stderr="", exit_code=0, duration_s=0.1)
    assert _combined_output(clean) == "ok"
