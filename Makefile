.PHONY: demo demo-frames check test

DEMO_HOME := $(CURDIR)/.demo-home

# Phase 5's demo (PLAN.md: "show the thought, show the command, show the
# output, show the next move"). Zero spend, no Docker daemon, no API key —
# scripts/seed_demo.py builds one solved run with StubSandbox +
# ScriptedProvider, then the TUI animates through its already-recorded trace
# via `runectl tui --replay` (never `runectl replay`, which re-executes the
# loop and is not what a demo needs). Safe to run repeatedly: the scratch
# RUNECTL_HOME is wiped first, so it never touches a real run store.
demo:
	@rm -rf $(DEMO_HOME)
	@mkdir -p $(DEMO_HOME)
	@RUNECTL_HOME=$(DEMO_HOME) uv run python scripts/seed_demo.py > $(DEMO_HOME)/.run_id
	@RUNECTL_HOME=$(DEMO_HOME) uv run runectl index rebuild >/dev/null
	RUNECTL_HOME=$(DEMO_HOME) uv run runectl tui --replay "$$(cat $(DEMO_HOME)/.run_id)"

# Phase 5's demo as still frames, captured headlessly through Textual's test
# harness — no TTY, no recording tool, no spend. SVG because it is text: it
# diffs, it scales, and it keeps binaries out of the repo.
demo-frames:
	@rm -rf $(DEMO_HOME)
	@mkdir -p $(DEMO_HOME)
	@RUNECTL_HOME=$(DEMO_HOME) uv run python scripts/seed_demo.py > $(DEMO_HOME)/.run_id
	@RUNECTL_HOME=$(DEMO_HOME) uv run runectl index rebuild >/dev/null
	RUNECTL_HOME=$(DEMO_HOME) uv run python scripts/capture_demo.py \
	  "$$(cat $(DEMO_HOME)/.run_id)" docs/demo

# The three checks CI runs (CONTRIBUTING.md), as one target for local use.
check:
	uv run mypy --strict src/
	uv run ruff check .
	uv run pytest

test:
	uv run pytest
