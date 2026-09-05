"""Typed error hierarchy mapped to the D4 exit codes.

Exit codes:
    0  flag found and finalized
    2  flag candidate found, awaiting approval (not a failure — not an exception here)
    3  run exhausted, no candidate (not a failure — not an exception here)
    4  sandbox/infrastructure failure
    5  provider failure after retries
    6  usage/config error

0/2/3 are outcomes carried on ``RunOutcome`` (see loop/state.py), not exceptions —
they are expected shapes of a finished run. 4/5/6 are raised as exceptions because
they mean the run could not proceed at all.
"""

from __future__ import annotations


class RunectlError(Exception):
    """Base for all errors that map to a non-zero, non-outcome exit code."""

    exit_code: int = 1


class SandboxError(RunectlError):
    """Sandbox/infrastructure failure (missing arena image, Docker daemon down, exec failure)."""

    exit_code = 4


class ProviderError(RunectlError):
    """Provider call failed after exhausting retries."""

    exit_code = 5


class UsageError(RunectlError):
    """Usage/config error: bad flags, missing key, malformed challenge file."""

    exit_code = 6
