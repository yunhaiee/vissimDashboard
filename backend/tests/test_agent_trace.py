from __future__ import annotations

from pathlib import Path

import pytest

from services.agent.trace import (
    ALLOWED_KINDS,
    SESSION_ID_RE,
    TraceWriter,
    read_trace,
    validate_session_id,
)


def test_write_then_read_round_trips_records(tmp_path: Path):
    writer = TraceWriter(tmp_path, session_id="sess-1")

    writer.write("action_start", {"action": "read_scenario"})
    writer.write("action_end", {"action": "read_scenario", "ok": True})

    records = read_trace(writer.path)

    assert len(records) == 2
    assert records[0]["kind"] == "action_start"
    assert records[0]["session_id"] == "sess-1"
    assert records[0]["payload"] == {"action": "read_scenario"}
    assert records[1]["kind"] == "action_end"
    assert records[1]["payload"] == {"action": "read_scenario", "ok": True}
    assert set(records[0].keys()) == {"ts", "session_id", "kind", "payload"}


def test_timestamps_are_monotonic_across_records(tmp_path: Path):
    writer = TraceWriter(tmp_path, session_id="sess-2")

    for _ in range(5):
        writer.write("plan", {"step": "noop"})

    records = read_trace(writer.path)
    timestamps = [record["ts"] for record in records]

    assert timestamps == sorted(timestamps)


def test_file_survives_process_restart_via_append_mode(tmp_path: Path):
    session_id = "sess-restart"

    first_writer = TraceWriter(tmp_path, session_id=session_id)
    first_writer.write("action_start", {"action": "one"})
    first_writer.write("action_end", {"action": "one"})
    del first_writer  # simulate process exit

    second_writer = TraceWriter(tmp_path, session_id=session_id)
    second_writer.write("preflight", {"action": "two"})

    records = read_trace(second_writer.path)

    assert [record["kind"] for record in records] == [
        "action_start",
        "action_end",
        "preflight",
    ]
    assert second_writer.path == tmp_path / f"trace_{session_id}.jsonl"


def test_one_file_per_session_under_work_dir(tmp_path: Path):
    writer_a = TraceWriter(tmp_path, session_id="alpha")
    writer_b = TraceWriter(tmp_path, session_id="beta")

    writer_a.write("plan", {"who": "alpha"})
    writer_b.write("plan", {"who": "beta"})

    assert writer_a.path != writer_b.path
    assert read_trace(writer_a.path)[0]["payload"] == {"who": "alpha"}
    assert read_trace(writer_b.path)[0]["payload"] == {"who": "beta"}


def test_unknown_kind_raises_value_error(tmp_path: Path):
    writer = TraceWriter(tmp_path, session_id="sess-3")

    with pytest.raises(ValueError):
        writer.write("not_a_real_kind", {})


@pytest.mark.parametrize("kind", sorted(ALLOWED_KINDS))
def test_every_allowed_kind_is_accepted(tmp_path: Path, kind: str):
    writer = TraceWriter(tmp_path, session_id="sess-4")

    record = writer.write(kind, {"k": kind})

    assert record["kind"] == kind


def test_non_serializable_payload_raises(tmp_path: Path):
    writer = TraceWriter(tmp_path, session_id="sess-5")

    with pytest.raises(TypeError):
        writer.write("warning", {"bad": {1, 2, 3}})


def test_read_trace_ignores_blank_lines(tmp_path: Path):
    path = tmp_path / "trace_manual.jsonl"
    path.write_text('{"ts": 1.0, "session_id": "x", "kind": "plan", "payload": {}}\n\n')

    records = read_trace(path)

    assert len(records) == 1
    assert records[0]["kind"] == "plan"


# ---------------------------------------------------------------------------
# M3.3 blocking finding: session_id sanitization at the TraceWriter.__init__
# boundary (docs/phase2/DECISIONS.md). session_store.py's own side of the
# same check is covered in test_agent_session_store.py.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_session_id",
    [
        "../../../etc/passwd",
        "../evil",
        "a/b",
        "a\\b",
        "",
        "..",
        "session id with spaces",
        "C:\\Windows",
    ],
)
def test_tracewriter_rejects_a_path_traversal_shaped_session_id(
    tmp_path: Path, bad_session_id: str
):
    with pytest.raises(ValueError):
        TraceWriter(tmp_path, session_id=bad_session_id)

    # Never path-traverses: nothing was created anywhere, not even inside
    # work_dir, let alone escaping it.
    assert list(tmp_path.rglob("*")) == []


def test_tracewriter_rejects_a_bad_session_id_before_creating_work_dir(tmp_path: Path):
    work_dir = tmp_path / "does-not-exist-yet"

    with pytest.raises(ValueError):
        TraceWriter(work_dir, session_id="../escape")

    assert not work_dir.exists()


@pytest.mark.parametrize(
    "good_session_id",
    ["sess-1", "alpha", "s-tier-simple", "0123456789ab", "eval-T001_network_overview_read"],
)
def test_tracewriter_accepts_every_session_id_shape_used_elsewhere_in_this_repo(
    tmp_path: Path, good_session_id: str
):
    # Deliberately broader than routers/assistant.py's own stricter
    # hex-only _SESSION_ID_RE (that one gates the HTTP client-input
    # boundary specifically) -- see SESSION_ID_RE's module docstring for
    # why. This is a regression guard: a literal copy of the hex-only
    # pattern here would break every orchestrator test and the eval
    # harness's "eval-<task id>" session ids.
    writer = TraceWriter(tmp_path, session_id=good_session_id)
    assert writer.path.exists() is False  # created on first write, not on construction
    writer.write("plan", {})
    assert writer.path.exists()


def test_validate_session_id_returns_the_input_unchanged_on_success():
    assert validate_session_id("sess-1") == "sess-1"


def test_validate_session_id_raises_value_error_on_a_non_string():
    with pytest.raises(ValueError):
        validate_session_id(None)  # type: ignore[arg-type]


def test_session_id_re_is_fully_anchored_not_a_prefix_match():
    # A regex without leading ^/trailing $ would let "safe/../../etc" match
    # on its safe-looking prefix -- assert the compiled pattern rejects any
    # traversal-shaped suffix, not just a traversal-shaped whole string.
    assert SESSION_ID_RE.match("safe/../etc") is None
