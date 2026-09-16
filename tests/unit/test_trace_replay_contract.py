"""Black-box tests for trace-reader tolerance, index tolerance, replay-provider
error typing, replay's exec-result extraction, challenge loading, and bench
suite loading. Written from the spec and public signatures only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runectl.bench.suite import SuiteError, load_suite
from runectl.cli.run_cmd import challenge_from_file
from runectl.errors import ProviderError, UsageError
from runectl.providers.replay import ReplayProvider
from runectl.sandbox.replay import exec_results_from_trace
from runectl.trace.events import ToolCall, ToolResultEvent
from runectl.trace.index import IndexDB
from runectl.trace.reader import TraceReader
from runectl.trace.store import Store
from runectl.trace.writer import TraceWriter

# -- Criterion 2: torn multi-byte UTF-8 tail ---------------------------------


def test_torn_multibyte_utf8_tail_is_skipped_without_raising(tmp_path: Path) -> None:
    """A valid line followed by one torn mid multi-byte char yields only the
    events before the tear, and iteration does not raise."""
    trace_path = tmp_path / "trace.jsonl"
    artifacts_dir = tmp_path / "artifacts"
    writer = TraceWriter(trace_path, "run-1", artifacts_dir)
    writer.emit(ToolCall(step=0, tool="run_command", arguments={"command": "echo hi"}))
    writer.close()

    # Append a second, unterminated line cut mid multi-byte UTF-8 character:
    # `'{"a":"caf'` followed by the lone 0xC3 lead byte of a 2-byte sequence,
    # with its continuation byte missing.
    with trace_path.open("ab") as fh:
        fh.write(b'{"a":"caf' + b"\xc3")

    events = list(TraceReader(trace_path, artifacts_dir))
    assert len(events) == 1
    assert events[0].type == "tool.call"


# -- Criterion 3: atomic manifest / tolerant index ---------------------------


def test_index_rebuild_skips_a_corrupt_manifest_but_indexes_the_good_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNECTL_HOME", str(tmp_path))
    store = Store()
    run_ids = []
    for i in range(2):
        run_id, writer = store.new_run(
            challenge_name=f"c{i}", category="misc", model="claude-sonnet-5",
            provider="anthropic", config_snapshot={},
        )
        writer.close()
        store.finish_run(run_id, outcome="solved", exit_code=0, flag="flag{x}", cost_usd=0.0, steps_used=1)
        run_ids.append(run_id)

    # Corrupt one run's manifest in place.
    store.manifest_path(run_ids[0]).write_bytes(b"not json at all")

    index = IndexDB(tmp_path / "index.db")
    count = index.rebuild(store)

    assert count >= 1  # the good run, not 0 — a corrupt sibling does not blank the index
    indexed_ids = {row["run_id"] for row in index.list_runs()}
    assert run_ids[1] in indexed_ids
    assert run_ids[0] not in indexed_ids


# -- Criterion 4: ReplayProvider raises ProviderError, not KeyError ----------


def test_replay_provider_raises_provider_error_not_key_error(tmp_path: Path) -> None:
    provider = ReplayProvider(tmp_path / "does-not-exist-cassette.jsonl")
    try:
        provider.complete(system="sys", messages=[], tools=[], max_tokens=100)
    except ProviderError:
        pass
    except KeyError:
        pytest.fail("ReplayProvider raised KeyError instead of ProviderError")
    else:
        pytest.fail("ReplayProvider did not raise for an unrecorded request")


# -- Criterion 5: exec_results_from_trace excludes a blocked tool result -----


def test_exec_results_from_trace_excludes_blocked_tool_results(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    artifacts_dir = tmp_path / "artifacts"
    writer = TraceWriter(trace_path, "run-1", artifacts_dir)
    writer.emit(
        ToolResultEvent(
            step=0, tool="run_command", ok=False, kind="blocked",
            stdout="", stderr="blocked by policy", duration_s=0.0, truncated=False,
        )
    )
    writer.emit(
        ToolResultEvent(
            step=1, tool="run_command", ok=True, kind="output",
            stdout="hello", stderr="", exit_code=0, duration_s=0.01, truncated=False,
        )
    )
    writer.close()

    results = exec_results_from_trace(trace_path, artifacts_dir)
    assert len(results) == 1
    assert results[0].stdout == "hello"


# -- Criterion 6: challenge_from_file ----------------------------------------


def _write_toml(directory: Path, body: str) -> Path:
    path = directory / "chal.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_challenge_from_file_missing_name_is_a_usage_error(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, 'category = "misc"\ndescription = "d"\n')
    with pytest.raises(UsageError):
        challenge_from_file(path)


def test_challenge_from_file_missing_category_is_a_usage_error(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, 'name = "x"\ndescription = "d"\n')
    with pytest.raises(UsageError):
        challenge_from_file(path)


def test_challenge_from_file_a_files_entry_that_escapes_its_directory_is_a_usage_error(
    tmp_path: Path,
) -> None:
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    path = _write_toml(
        tmp_path,
        'name = "x"\ncategory = "misc"\ndescription = "d"\nfiles = ["../secret.txt"]\n',
    )
    with pytest.raises(UsageError):
        challenge_from_file(path)


def test_challenge_from_file_an_absolute_files_entry_is_a_usage_error(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    path = _write_toml(
        tmp_path,
        f'name = "x"\ncategory = "misc"\ndescription = "d"\nfiles = ["{outside.as_posix()}"]\n',
    )
    with pytest.raises(UsageError):
        challenge_from_file(path)


def test_challenge_from_file_a_listed_file_that_does_not_exist_is_a_usage_error(
    tmp_path: Path,
) -> None:
    path = _write_toml(
        tmp_path, 'name = "x"\ncategory = "misc"\ndescription = "d"\nfiles = ["nope.bin"]\n'
    )
    with pytest.raises(UsageError):
        challenge_from_file(path)


def test_challenge_from_file_loads_a_well_formed_challenge(tmp_path: Path) -> None:
    (tmp_path / "real.txt").write_text("data", encoding="utf-8")
    path = _write_toml(
        tmp_path, 'name = "x"\ncategory = "misc"\ndescription = "d"\nfiles = ["real.txt"]\n'
    )
    challenge = challenge_from_file(path)
    assert challenge.name == "x"
    assert challenge.category == "misc"
    assert len(challenge.files) == 1


# -- Criterion 7: load_suite raises SuiteError for malformed case files ------


def test_load_suite_raises_suite_error_for_malformed_chal_toml(tmp_path: Path) -> None:
    case_dir = tmp_path / "broken"
    case_dir.mkdir()
    (case_dir / "chal.toml").write_text("this is not [valid toml", encoding="utf-8")
    (case_dir / "expected.json").write_text(json.dumps({"flag": "x"}), encoding="utf-8")
    with pytest.raises(SuiteError):
        load_suite(tmp_path)


def test_load_suite_raises_suite_error_for_malformed_expected_json(tmp_path: Path) -> None:
    case_dir = tmp_path / "broken"
    case_dir.mkdir()
    (case_dir / "chal.toml").write_text('name = "broken"\ncategory = "misc"\n', encoding="utf-8")
    (case_dir / "expected.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SuiteError):
        load_suite(tmp_path)
