"""Tests for v2 unit (3)'s tools_check.py: the load_check tool.

Every test injects a FAKE ``_load_check`` — mirrors
``test_agent_tools_run.py``'s ``_FakeJobRunner`` injection pattern — so no
test here touches real Vissim COM, a real Vissim instance, or real
filesystem I/O beyond tiny fixture files this file writes directly.
``_snapshot_err_paths``/``_read_new_err_content`` ARE exercised for real
(pure filesystem glob/read logic, deterministic, and exercising the real
"what counts as a NEW .err file" logic is the point).

Live COM behavior (does LoadNet/RunSingleStep actually behave this way
against real Vissim) is proven separately by
``backend/tools/load_check_live_check.py`` and the Phase 1 probe
(``backend/tools/check_network_probe.py``) — not re-proven here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_check as tools_check
import services.agent.tools_run as tools_run
from services.agent.guardrails import GuardrailEngine
from services.agent.toolkit import default_registry, dispatch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_active_run_source():
    """load_check reuses tools_run's active-run-source (see tools_check.py's
    module docstring) -- every test starts clean and clears it after, same
    discipline as test_agent_tools_run.py's own fixture."""
    tools_run.clear_active_run_source()
    yield
    tools_run.clear_active_run_source()


def _source_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "working.inpx"
    path.write_text("<net/>", encoding="utf-8")
    return path


class _FakeLoadCheckRunner:
    """A canned ``_load_check`` replacement: records every call's argument
    and returns a pre-built result dict -- no real COM, no real Vissim
    instance, no real semaphore slot."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[Path] = []

    def __call__(self, inpx_path: Path) -> dict[str, Any]:
        self.calls.append(inpx_path)
        return self.result


# ---------------------------------------------------------------------------
# Active source context (reused from tools_run, not reimplemented)
# ---------------------------------------------------------------------------


def test_no_active_source_raises_no_active_check_source_error():
    with pytest.raises(tools_check.NoActiveCheckSourceError, match="no_active_check_source"):
        tools_check._require_active_source()


def test_active_source_pointing_at_a_missing_file_raises_file_not_found(tmp_path: Path):
    tools_run.set_active_run_source(tmp_path / "does_not_exist.inpx")

    with pytest.raises(FileNotFoundError):
        tools_check._require_active_source()


def test_active_source_reused_from_tools_run(tmp_path: Path):
    """Confirms the design decision stated in the module docstring: no
    separate module-global exists here -- setting tools_run's active run
    source is sufficient for load_check to see it."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    assert tools_check._require_active_source() == source


# ---------------------------------------------------------------------------
# _snapshot_err_paths / _read_new_err_content -- real filesystem logic
# ---------------------------------------------------------------------------


def test_read_new_err_content_returns_none_when_nothing_new(tmp_path: Path):
    pre_existing = (tmp_path / "old.err")
    pre_existing.write_text("old content", encoding="utf-8")
    snapshot = tools_check._snapshot_err_paths(tmp_path)

    assert tools_check._read_new_err_content(tmp_path, snapshot) is None


def test_read_new_err_content_returns_only_new_files(tmp_path: Path):
    pre_existing = tmp_path / "old.err"
    pre_existing.write_text("old content", encoding="utf-8")
    snapshot = tools_check._snapshot_err_paths(tmp_path)

    new_file = tmp_path / "working.err"
    new_file.write_text("Error\tStatic Vehicle Route \"4 - 2\" is not complete.", encoding="utf-8")

    content = tools_check._read_new_err_content(tmp_path, snapshot)

    assert content is not None
    assert "not complete" in content
    assert "old content" not in content


def test_read_new_err_content_truncates_oversized_content(tmp_path: Path):
    snapshot = tools_check._snapshot_err_paths(tmp_path)
    huge = tmp_path / "working.err"
    huge.write_text("x" * (tools_check._MAX_ERR_CONTENT_CHARS + 500), encoding="utf-8")

    content = tools_check._read_new_err_content(tmp_path, snapshot)

    assert content is not None
    assert len(content) <= tools_check._MAX_ERR_CONTENT_CHARS + len("\n... [truncated]")
    assert content.endswith("[truncated]")


def test_read_new_err_content_no_err_files_at_all_returns_none(tmp_path: Path):
    assert tools_check._read_new_err_content(tmp_path, set()) is None


# ---------------------------------------------------------------------------
# load_check -- full tool dispatch, fake COM runner
# ---------------------------------------------------------------------------


def test_load_check_success_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    fake = _FakeLoadCheckRunner(
        {"load_ok": True, "load_error": None, "init_ok": True, "init_error": None, "err_content": None}
    )
    monkeypatch.setattr(tools_check, "_run_load_check_with_slot", fake)

    envelope = dispatch("load_check", {}, registry=default_registry)

    assert envelope["status"] == "ok"
    assert envelope["error_type"] is None
    assert envelope["verification"] == {"load_ok": True, "init_ok": True}
    assert envelope["data"]["load_check_result"]["init_ok"] is True
    assert "no load-time or initialization-time errors" in envelope["message"]
    assert "NOT" in envelope["message"]  # the honesty caveat must always ride along
    assert fake.calls == [source]


def test_load_check_load_refused_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mirrors the delete_signal_controller Phase 1 finding: LoadNet itself
    raises, init_ok stays None, no .err content."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    fake = _FakeLoadCheckRunner(
        {
            "load_ok": False,
            "load_error": "com_error: The network file could not be loaded.",
            "init_ok": None,
            "init_error": None,
            "err_content": None,
        }
    )
    monkeypatch.setattr(tools_check, "_run_load_check_with_slot", fake)

    envelope = dispatch("load_check", {}, registry=default_registry)

    assert envelope["status"] == "ok"  # a refused LOAD is a successful CHECK, not a tool crash
    assert envelope["verification"] == {"load_ok": False, "init_ok": None}
    assert "could not be loaded" in envelope["message"]
    assert envelope["data"]["load_check_result"]["load_error"] == fake.result["load_error"]


def test_load_check_init_failed_case_with_err_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Mirrors the delete_referenced_link Phase 1 finding: LoadNet succeeds,
    RunSingleStep raises, .err content is captured."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    err_text = (
        'Error\tStatic Vehicle Route "4 - 2" is not complete.\n'
        "Error\tThe simulation run could not be initialized due to errors."
    )
    fake = _FakeLoadCheckRunner(
        {
            "load_ok": True,
            "load_error": None,
            "init_ok": False,
            "init_error": "com_error: The simulation could not be continued.",
            "err_content": err_text,
        }
    )
    monkeypatch.setattr(tools_check, "_run_load_check_with_slot", fake)

    envelope = dispatch("load_check", {}, registry=default_registry)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"load_ok": True, "init_ok": False}
    assert "could not initialize" in envelope["message"]
    assert envelope["data"]["load_check_result"]["err_content"] == err_text


def test_load_check_no_active_source_is_a_tool_execution_error(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeLoadCheckRunner({"load_ok": True, "load_error": None, "init_ok": True, "init_error": None, "err_content": None})
    monkeypatch.setattr(tools_check, "_run_load_check_with_slot", fake)

    envelope = dispatch("load_check", {}, registry=default_registry)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_active_check_source" in envelope["message"]
    assert fake.calls == []  # never reached the COM runner


def test_load_check_rejects_unknown_params():
    envelope = dispatch("load_check", {"bogus": 1}, registry=default_registry)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# Deliberate design pin: load_check_result must NOT be interpreted by any
# postrun constraint the way a real run_simulation run_result would be.
# ---------------------------------------------------------------------------


def test_load_check_postrun_finds_nothing_to_interpret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Regression pin for the module docstring's core design claim: because
    the envelope's data key is "load_check_result", not "run_result",
    orchestrator.py's `(envelope.get("data") or {}).get("run_result")`
    extraction sees None -- every registered postrun constraint's own
    `if run_result is None: return None` guard makes this a clean no-op,
    never a crash and never a misleading "results_missing_kpi_tables"-style
    warning about a probe that was never meant to have KPI tables."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    fake = _FakeLoadCheckRunner(
        {"load_ok": True, "load_error": None, "init_ok": True, "init_error": None, "err_content": None}
    )
    monkeypatch.setattr(tools_check, "_run_load_check_with_slot", fake)

    envelope = dispatch("load_check", {}, registry=default_registry)
    run_result = (envelope.get("data") or {}).get("run_result")
    assert run_result is None

    engine = GuardrailEngine()
    report = engine.postrun({"tool": "load_check", "params": {}}, run_result)

    assert report.findings == []
    assert report.blocking is False
