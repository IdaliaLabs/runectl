"""Real Docker sandbox: one container per run (D2).

Exercised against a live daemon since 2026-09-07 (STATUS "Live verification"):
real containers build and run, triage and agent commands execute inside the
amd64 arena, and the security posture below is applied and functional. Its
isolation is asserted by configuration, not by adversarial testing (SECURITY.md).
"""

from __future__ import annotations

import contextlib
import io
import shlex
import tarfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import docker
import requests
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

from runectl.config import (
    CONTAINER_NAME_PREFIX,
    SANDBOX_CPUS,
    SANDBOX_ENV,
    SANDBOX_LIVE_LOG_PATH,
    SANDBOX_MEM_LIMIT,
    SANDBOX_OUTPUT_CAP_BYTES,
    SANDBOX_PIDS_LIMIT,
    SANDBOX_PLATFORM,
    SANDBOX_WORKDIR,
)
from runectl.errors import SandboxError
from runectl.sandbox.base import ExecResult

ARENA_IMAGE = "runectl/arena:kali"


class DockerSandbox:
    """Implements the :class:`~runectl.sandbox.base.Sandbox` protocol structurally."""

    def __init__(
        self,
        *,
        image: str = ARENA_IMAGE,
        network: str = "bridge",
        run_id: str | None = None,
        platform: str = SANDBOX_PLATFORM,
        client: docker.DockerClient | None = None,
    ) -> None:
        self._image = image
        self._network = network
        self._platform = platform
        # Named after the run so a human can attach to it mid-run — the
        # predecessor's `docker exec -it ctf-agent-<id> bash` workflow, which was
        # actually used at competitions to take over from the agent.
        self._name = f"{CONTAINER_NAME_PREFIX}{run_id}" if run_id else None
        self._client_override = client
        self._client: docker.DockerClient | None = None
        # The container user's uid/gid, learned in _verify_non_root and applied
        # to files copied in so the agent (`ctf`) can read and chmod them rather
        # than finding them root-owned. 1000 is the arena's `ctf` uid by default.
        self._uid = 1000
        self._gid = 1000
        self._container: Container | None = None

    @property
    def container_name(self) -> str | None:
        return self._name

    def start(self) -> None:
        # Deferred to start() rather than __init__: constructing a Sandbox must
        # never itself touch the daemon, so daemon-unreachable is always a
        # SandboxError a caller's try/except around start()/run() can catch —
        # never a raw SDK exception from plain object construction.
        try:
            self._client = self._client_override or docker.from_env()
            self._client.images.get(self._image)
        except ImageNotFound as exc:
            raise SandboxError(
                f"arena image {self._image!r} not found — run `runectl arena build` first"
            ) from exc
        except (APIError, DockerException) as exc:
            raise SandboxError(f"could not reach the Docker daemon: {exc}") from exc
        self._remove_stale_namesake()
        try:
            self._container = self._client.containers.run(
                self._image,
                name=self._name,
                command="sleep infinity",
                detach=True,
                platform=self._platform,
                mem_limit=SANDBOX_MEM_LIMIT,
                nano_cpus=int(SANDBOX_CPUS * 1_000_000_000),
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],  # non-root already; dropping caps costs nothing here
                privileged=False,
                pids_limit=SANDBOX_PIDS_LIMIT,
                environment=dict(SANDBOX_ENV),
                network_mode=self._network,
                working_dir=SANDBOX_WORKDIR,
            )
        except APIError as exc:
            raise SandboxError(f"failed to start arena container: {exc}") from exc
        setup = self._raw_exec(f"mkdir -p {SANDBOX_WORKDIR} && touch {SANDBOX_LIVE_LOG_PATH}", timeout_s=10)
        if not setup.ok:
            raise SandboxError(f"arena container failed workdir setup: {setup.stderr}")
        self._verify_non_root()

    def _verify_non_root(self) -> None:
        """Refuse to run the agent as root inside the sandbox (D2 posture).

        The posture used to rest entirely on the Dockerfile's `USER ctf`, which
        stopped being a guarantee once `arena ensure --from-file` and
        `--from-registry` let an arbitrary image become the arena. Root inside
        the container plus a kernel escape is a materially worse position than
        an unprivileged user, so this is checked rather than assumed.
        """
        probe = self._raw_exec("id -u; id -g", timeout_s=10)
        if not probe.ok:
            raise SandboxError(f"could not determine the sandbox user: {probe.stderr}")
        ids = probe.stdout.split()
        if ids and ids[0].isdigit():
            self._uid = int(ids[0])
        if len(ids) > 1 and ids[1].isdigit():
            self._gid = int(ids[1])
        if ids and ids[0] == "0":
            raise SandboxError(
                f"the arena image {self._image!r} runs as root. runectl executes "
                "model-authored code in this container and will not do so as root. "
                "Add a non-root USER to the image (see arena/Dockerfile) and rebuild."
            )

    def _remove_stale_namesake(self) -> None:
        """Clear a leftover container of the same name (a previous crashed run).

        Named containers make attaching possible, but they also make a name
        collision possible; the predecessor removed the namesake before starting
        for exactly this reason.
        """
        if self._name is None or self._client is None:
            return
        try:
            existing = self._client.containers.get(self._name)
        except NotFound:
            return
        except (APIError, DockerException):
            return
        with contextlib.suppress(APIError):
            existing.remove(force=True)

    def _require_container(self) -> Container:
        if self._container is None:
            raise SandboxError("DockerSandbox: exec/write/read called before start()")
        return self._container

    def _raw_exec(self, script: str, *, timeout_s: int) -> ExecResult:
        """Unwrapped exec, no live-log tee — used for internal setup/verification only."""
        container = self._require_container()
        wrapped = f"timeout {timeout_s} bash -lc {shlex.quote(script)}"
        start = time.monotonic()
        try:
            result = container.exec_run(["bash", "-lc", wrapped], workdir=SANDBOX_WORKDIR, demux=True)
        except APIError as exc:
            raise SandboxError(f"exec failed: {exc}") from exc
        duration = time.monotonic() - start
        # The installed stub doesn't model demux=True's (bytes|None, bytes|None) return;
        # this is the documented shape for that mode (docker-py's exec_run docstring).
        output = cast("tuple[bytes | None, bytes | None]", result.output)
        stdout_raw, stderr_raw = output if output else (None, None)
        exit_code = result.exit_code
        return ExecResult(
            ok=exit_code == 0,
            stdout=(stdout_raw or b"").decode("utf-8", "replace"),
            stderr=(stderr_raw or b"").decode("utf-8", "replace"),
            exit_code=exit_code,
            duration_s=duration,
        )

    def exec(self, argv_or_script: str, *, timeout_s: int) -> ExecResult:
        """Mirrors combined output to the live log via process substitution (D2 item 12)
        while still returning stdout/stderr separately for the structured result."""
        # `head -c CAP` bounds each stream before it reaches both the live log and
        # the exec socket, so a runaway command can neither OOM the host reader nor
        # fill the container disk; when head closes, the command takes SIGPIPE and
        # stops. `timeout -k 5` escalates to SIGKILL if the command ignores the
        # SIGTERM at the deadline.
        cap = SANDBOX_OUTPUT_CAP_BYTES
        inner = (
            f"{{ {argv_or_script}\n }} "
            f"1> >(head -c {cap} | tee -a {SANDBOX_LIVE_LOG_PATH}) "
            f"2> >(head -c {cap} | tee -a {SANDBOX_LIVE_LOG_PATH} >&2)"
        )
        # ponytail: `timeout` signals only its direct child, so a command that
        # backgrounds a process (`nc -lvnp 4444 &`) and exits can still leave the
        # child holding the exec stream open. Upgrade path if that bites: run under
        # `setsid` and kill the session on a Python-side deadline.
        wrapped = f"timeout -k 5 {timeout_s} bash -c {shlex.quote(inner)}"
        container = self._require_container()
        start = time.monotonic()
        try:
            result = container.exec_run(["bash", "-lc", wrapped], workdir=SANDBOX_WORKDIR, demux=True)
        except (DockerException, OSError, requests.RequestException) as exc:
            raise SandboxError(f"exec failed: {exc}") from exc
        duration = time.monotonic() - start
        # The installed stub doesn't model demux=True's (bytes|None, bytes|None) return;
        # this is the documented shape for that mode (docker-py's exec_run docstring).
        output = cast("tuple[bytes | None, bytes | None]", result.output)
        stdout_raw, stderr_raw = output if output else (None, None)
        stdout_bytes = stdout_raw or b""
        stderr_bytes = stderr_raw or b""
        exit_code = result.exit_code
        return ExecResult(
            ok=exit_code == 0,
            stdout=stdout_bytes.decode("utf-8", "replace"),
            stderr=stderr_bytes.decode("utf-8", "replace"),
            exit_code=exit_code,
            duration_s=duration,
            truncated=len(stdout_bytes) >= cap or len(stderr_bytes) >= cap,
        )

    def _own(self, info: tarfile.TarInfo) -> tarfile.TarInfo:
        """Copy files in owned by the container user, not root/the host uid, so
        the agent can read, chmod, and append to what it is given (D2)."""
        info.uid = self._uid
        info.gid = self._gid
        info.uname = ""
        info.gname = ""
        return info

    def write_file(self, rel_path: str, content: bytes) -> None:
        container = self._require_container()
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            info = tarfile.TarInfo(name=rel_path)
            info.size = len(content)
            info.mtime = int(time.time())
            info.mode = 0o755  # agent-authored: runnable, and writable by its owner
            self._own(info)
            tar.addfile(info, io.BytesIO(content))
        try:
            ok = container.put_archive(SANDBOX_WORKDIR, tar_buf.getvalue())
        except (DockerException, OSError, requests.RequestException) as exc:
            raise SandboxError(f"failed to write file into sandbox: {rel_path}: {exc}") from exc
        if not ok:
            raise SandboxError(f"failed to write file into sandbox: {rel_path}")

    def put_inputs(self, files: Sequence[Path]) -> list[str]:
        container = self._require_container()
        tar_buf = io.BytesIO()
        try:
            with tarfile.open(fileobj=tar_buf, mode="w") as tar:
                for file in files:

                    def _own_input(info: tarfile.TarInfo) -> tarfile.TarInfo:
                        info.mode |= 0o600  # the owner can always read/write it
                        return self._own(info)

                    tar.add(file, arcname=file.name, filter=_own_input)
        except OSError as exc:
            # A missing or unreadable --file / challenge `files` entry: a usage
            # problem surfaced as a clean SandboxError, not a raw traceback after
            # the container is already up.
            raise SandboxError(f"could not read an input file to upload: {exc}") from exc
        try:
            ok = container.put_archive(SANDBOX_WORKDIR, tar_buf.getvalue())
        except (DockerException, requests.RequestException) as exc:
            raise SandboxError(f"failed to upload input files into sandbox: {exc}") from exc
        if not ok:
            raise SandboxError("failed to upload input files into sandbox")
        landed: list[str] = []
        for file in files:
            in_path = f"{SANDBOX_WORKDIR}/{file.name}"
            check = self._raw_exec(f"test -e {shlex.quote(in_path)}", timeout_s=10)
            if not check.ok:
                raise SandboxError(f"input file did not land in sandbox: {file.name}")
            landed.append(in_path)
        return landed

    def stop(self) -> None:
        if self._container is not None:
            # Best-effort: a remove() that raises (e.g. the daemon was restarted
            # mid-run) must not replace the SandboxError the run is already
            # unwinding with a raw docker/requests exception.
            with contextlib.suppress(Exception):
                self._container.remove(force=True)
            self._container = None
