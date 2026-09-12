"""In-process fake sandbox: no daemon, no tokens (D2).

Built before :class:`~runectl.sandbox.docker.DockerSandbox` so the loop is
testable from day one. Commands are resolved from a scripted lookup table
supplied by the caller (a test fixture, or the walking-skeleton demo) — there
is no real shell underneath.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from runectl.config import SANDBOX_WORKDIR
from runectl.sandbox.base import ExecResult


def ok(stdout: str = "", *, stderr: str = "", exit_code: int = 0, duration_s: float = 0.01) -> ExecResult:
    """Convenience constructor for a successful scripted :class:`ExecResult`."""
    return ExecResult(
        ok=True, stdout=stdout, stderr=stderr, exit_code=exit_code,
        duration_s=duration_s,
    )


def failed(stderr: str = "", *, exit_code: int = 1, duration_s: float = 0.01) -> ExecResult:
    return ExecResult(
        ok=False, stdout="", stderr=stderr, exit_code=exit_code,
        duration_s=duration_s,
    )


class StubSandbox:
    """Implements the :class:`~runectl.sandbox.base.Sandbox` protocol structurally."""

    def __init__(
        self,
        *,
        script: Mapping[str, ExecResult] | None = None,
        default: ExecResult | None = None,
    ) -> None:
        self._fs: dict[str, bytes] = {}
        self._script: dict[str, ExecResult] = dict(script or {})
        self._default = default or ok()
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("StubSandbox: exec/write/read before start()")

    def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult:
        self._require_started()
        return self._script.get(argv_or_script, self._default)

    def write_file(self, rel_path: str, content: bytes) -> None:
        self._require_started()
        self._fs[rel_path] = content

    def put_inputs(self, files: Sequence[Path]) -> list[str]:
        self._require_started()
        landed: list[str] = []
        for file in files:
            content = file.read_bytes()
            self._fs[file.name] = content
            if file.name not in self._fs:  # pragma: no cover - defensive, mirrors D2's verify step
                raise RuntimeError(f"input did not land in sandbox: {file.name}")
            landed.append(f"{SANDBOX_WORKDIR}/{file.name}")
        return landed
