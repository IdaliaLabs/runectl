"""Arena image lifecycle: build/status (D2, plan §2.4).

Shells out to ``docker build`` rather than the Python SDK so build output
streams naturally to the terminal; the SDK is used only for the cheap
image-exists check in ``status``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import docker
from docker.errors import APIError, DockerException, ImageNotFound

from runectl.errors import SandboxError
from runectl.sandbox.docker import ARENA_IMAGE

_ARENA_DIR = Path(__file__).resolve().parents[3] / "arena"


def build(*, tag: str = ARENA_IMAGE, context_dir: Path | None = None) -> int:
    """Run `docker build`, streaming to the caller's terminal. Returns the exit code."""
    context = context_dir or _ARENA_DIR
    result = subprocess.run(["docker", "build", "-t", tag, str(context)], check=False)
    return result.returncode


def status(*, tag: str = ARENA_IMAGE, client: docker.DockerClient | None = None) -> str | None:
    """Return the image id if the arena image is built, else None.

    Raises SandboxError (never a raw SDK traceback) if the Docker daemon itself
    isn't reachable — distinct from "image not built yet".
    """
    try:
        active_client = client or docker.from_env()
        image = active_client.images.get(tag)
    except ImageNotFound:
        return None
    except (APIError, DockerException) as exc:
        raise SandboxError(f"could not reach the Docker daemon: {exc}") from exc
    return str(image.id)
