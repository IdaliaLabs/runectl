"""Arena image lifecycle (D2, D17): presence, provenance, and the remedy paths.

All of it against a fake Docker client and a stubbed `subprocess.run` — no
daemon, per this suite's rule.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from docker.errors import ImageNotFound

from runectl.errors import SandboxError
from runectl.sandbox import arena_build


class _FakeImage:
    def __init__(self, image_id: str, labels: dict[str, str] | None) -> None:
        self.id = image_id
        self.labels = labels


class _FakeImages:
    def __init__(self, image: _FakeImage | None) -> None:
        self._image = image

    def get(self, tag: str) -> _FakeImage:
        if self._image is None:
            raise ImageNotFound(tag)
        return self._image


class _FakeClient:
    def __init__(self, image: _FakeImage | None) -> None:
        self.images = _FakeImages(image)


@pytest.fixture
def context_dir(tmp_path: Path) -> Path:
    (tmp_path / "Dockerfile").write_text("FROM kalilinux/kali-rolling:latest\n")
    return tmp_path


def test_fingerprint_tracks_the_dockerfile(context_dir: Path) -> None:
    first = arena_build.arena_fingerprint(context_dir)
    assert first == arena_build.arena_fingerprint(context_dir)

    (context_dir / "Dockerfile").write_text("FROM kalilinux/kali-rolling:latest\nRUN apt-get update\n")
    assert arena_build.arena_fingerprint(context_dir) != first


def test_missing_image_is_a_normal_answer_not_an_error(context_dir: Path) -> None:
    status = arena_build.inspect(context_dir=context_dir, client=_FakeClient(None))

    assert not status.present
    assert not status.stale
    assert status.image_id is None


def test_image_built_from_this_dockerfile_is_current(context_dir: Path) -> None:
    fingerprint = arena_build.arena_fingerprint(context_dir)
    client = _FakeClient(_FakeImage("sha256:abc", {arena_build.FINGERPRINT_LABEL: fingerprint}))

    status = arena_build.inspect(context_dir=context_dir, client=client)

    assert status.present
    assert not status.stale
    assert not status.provenance_unknown


def test_image_built_from_a_different_dockerfile_is_stale(context_dir: Path) -> None:
    client = _FakeClient(_FakeImage("sha256:abc", {arena_build.FINGERPRINT_LABEL: "0" * 16}))

    status = arena_build.inspect(context_dir=context_dir, client=client)

    assert status.present
    assert status.stale


def test_unlabelled_image_is_unknown_provenance_not_stale(context_dir: Path) -> None:
    """An image built before provenance stamping existed must not start nagging."""
    client = _FakeClient(_FakeImage("sha256:abc", {}))

    status = arena_build.inspect(context_dir=context_dir, client=client)

    assert status.present
    assert status.provenance_unknown
    assert not status.stale


def test_build_stamps_the_fingerprint_as_a_label(
    context_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(arena_build.subprocess, "run", fake_run)
    arena_build.build(context_dir=context_dir)

    argv = captured[0]
    assert "--label" in argv
    label = argv[argv.index("--label") + 1]
    assert label == f"{arena_build.FINGERPRINT_LABEL}={arena_build.arena_fingerprint(context_dir)}"


def test_load_archive_retags_when_the_tarball_carried_another_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "arena.tar"
    archive.write_bytes(b"not really a tarball")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1] == "load":
            return subprocess.CompletedProcess(argv, 0, "Loaded image: someone/arena:custom\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(arena_build.subprocess, "run", fake_run)
    arena_build.load_archive(archive, tag="runectl/arena:kali")

    assert calls[-1] == ["docker", "tag", "someone/arena:custom", "runectl/arena:kali"]


def test_load_archive_does_not_retag_when_the_name_already_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "arena.tar"
    archive.write_bytes(b"x")

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "Loaded image: runectl/arena:kali\n", "")

    monkeypatch.setattr(arena_build.subprocess, "run", fake_run)
    arena_build.load_archive(archive, tag="runectl/arena:kali")  # must not raise


def test_load_archive_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="no such image archive"):
        arena_build.load_archive(tmp_path / "nope.tar")


def test_load_archive_surfaces_a_docker_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "arena.tar"
    archive.write_bytes(b"x")

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "open arena.tar: permission denied")

    monkeypatch.setattr(arena_build.subprocess, "run", fake_run)
    with pytest.raises(SandboxError, match="permission denied"):
        arena_build.load_archive(archive)
