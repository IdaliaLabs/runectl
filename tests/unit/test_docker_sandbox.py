"""Regression: constructing a DockerSandbox must never itself touch the Docker
daemon — only start()/exec() may, and only ever raising SandboxError, never a
raw SDK exception, so a caller's try/except around start() always catches it."""

from __future__ import annotations

import pytest

from runectl.errors import SandboxError
from runectl.sandbox.docker import DockerSandbox


def test_construction_does_not_touch_daemon() -> None:
    # No client override given; if __init__ eagerly called docker.from_env()
    # (which fails fast when no daemon socket exists) this would already raise.
    DockerSandbox(network="bridge")


def test_start_wraps_daemon_failure_as_sandbox_error() -> None:
    sandbox = DockerSandbox(network="bridge")
    with pytest.raises(SandboxError):
        sandbox.start()
