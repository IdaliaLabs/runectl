#!/usr/bin/env bash
# Drives the seeded fixture run's TUI playback so `asciinema rec` has
# something to capture (called by `make demo-video`, never run standalone).
#
# Expects RUNECTL_HOME to already point at a scratch store `demo-seed` just
# populated (a run built by scripts/seed_demo.py with StubSandbox +
# ScriptedProvider — zero spend, no Docker, no API key). `runectl tui
# --replay` then animates that already-solved run's trace at a fixed pace
# (D13 amendment); it re-executes nothing. There is no "finish and quit"
# signal from the TUI itself once playback ends, so this just lets it run
# long enough to settle on the final state, then sends SIGINT the same way
# a viewer would Ctrl+C out.
#
# Pace and length are parameters because the README needs two takes from the
# same fixture: a fast one that fits the whole 25-event run into a five-second
# hero loop, and a slow one whose individual phases are legible enough to cut
# into per-phase clips. Defaults reproduce the fast take.
#
#   DEMO_DELAY     seconds between trace events (default 0.16)
#   DEMO_DURATION  seconds to record before SIGINT (default 5.4)
#   DEMO_TITLE     seconds to hold the title card; 0 skips it (default 0)
set -euo pipefail

run_id="$(cat "$RUNECTL_HOME/.run_id")"
delay="${DEMO_DELAY:-0.16}"
duration="${DEMO_DURATION:-5.4}"
title="${DEMO_TITLE:-0}"

clear
if [ "$title" != "0" ]; then
  echo "runectl — an AI agent solving a CTF challenge, live"
  sleep "$title"
  clear
fi

# `uv run` execs a child process for the actual `runectl` entry point, so a
# SIGINT to $! (uv's own pid) does not reach it — pkill by command name hits
# the real TUI process regardless of where it sits in that tree.
uv run runectl tui --replay "$run_id" --playback-delay "$delay" &
pid=$!
sleep "$duration"
pkill -INT -f "runectl.*tui.*--replay" 2>/dev/null || true
sleep 0.5
kill -9 "$pid" 2>/dev/null || true
wait "$pid" 2>/dev/null || true
