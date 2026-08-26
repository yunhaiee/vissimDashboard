"""Tests for `routers.simulations._simulation_capabilities` (unit #68).

Measured defect: a workspace whose active dataset was uploaded as a bare
.inpx (no project.vissimpdb / Scenarios/) was refused at the simulation door
with a message claiming a full project ZIP with scenario (.vissimpdb) data
was required. It is not: `_run_simulation_locked` -> `build_simulation_network`
-> `services.scenario_project.build_scenario_tree` parses the source .inpx
directly and only reaches for project.vissimpdb/Scenarios when the request
carries a `scenario_id` (`SimParams.scenario_id` defaults to `None`), and even
then does so through `get_project_scenario`, which degrades to an honest
`ValueError: Unknown scenario_id` rather than a hard crash on a missing
project. So the honest capability gate is "an uploaded dataset (zip OR
inpx) whose resolved source .inpx exists, on Windows, with pywin32" --
never a zip-specific or .vissimpdb-specific requirement.

Mirrors the monkeypatch idiom already used for this router's guard-rail
tests in test_routers_simulations_agent_job.py: patch the names
`routers.simulations` imported directly (`get_active_dataset`,
`get_active_inpx_path`, `_check_pywin32`) rather than reaching into
`services.datasets`, so no real filesystem/pywin32 state is touched.

The "are we on Windows" leg is patched through `_is_windows()`, a dedicated
seam `_simulation_capabilities` calls (mirrors `_check_pywin32` right next to
it) -- NEVER through `os.name` directly. `os` is a process-wide singleton
module; flipping its real `.name` to `"posix"` on a live Windows dev box also
flips what `pathlib.Path(...)` builds everywhere else in the process for the
rest of the test, including inside pytest's OWN failure-report rendering --
which crashes with `NotImplementedError: cannot instantiate 'PosixPath'` and
buries the actual assertion diff. Measured 2026-08-26 (unit #68 refuter
pass) against an earlier version of this file that did exactly that.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from routers import simulations as simulations_router


def _ready_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows + pywin32 both available -- isolates each test's assertions
    to the dataset-shape question alone, unless the test overrides one of
    these itself. Uses the `_is_windows()` seam, never the real `os.name`
    (see module docstring)."""
    monkeypatch.setattr(simulations_router, "_check_pywin32", lambda: (True, None))
    monkeypatch.setattr(simulations_router, "_is_windows", lambda: True)
    monkeypatch.delenv("VISSIM_SIMULATION_INPX_PATH", raising=False)


def _set_dataset(monkeypatch: pytest.MonkeyPatch, dataset: dict) -> None:
    monkeypatch.setattr(simulations_router, "get_active_dataset", lambda: dataset)


def _set_source_inpx(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(simulations_router, "get_active_inpx_path", lambda: path)


# ---------------------------------------------------------------------------
# The matrix: uploaded zip / uploaded inpx (existing vs missing source) /
# non-uploaded (default) dataset.
# ---------------------------------------------------------------------------


def test_uploaded_zip_with_existing_source_can_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _ready_env(monkeypatch)
    source = tmp_path / "current.inpx"
    source.write_text("<net/>", encoding="utf-8")
    _set_source_inpx(monkeypatch, source)
    _set_dataset(monkeypatch, {"source": "uploaded", "kind": "zip", "filename": "project.zip"})

    capabilities = simulations_router._simulation_capabilities()

    assert capabilities["can_run"] is True
    assert capabilities["reason"] is None


def test_uploaded_inpx_with_existing_source_can_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The unit's headline case: a bare-.inpx upload is runnable exactly
    like a zip once its resolved source .inpx exists on disk."""
    _ready_env(monkeypatch)
    source = tmp_path / "current.inpx"
    source.write_text("<net/>", encoding="utf-8")
    _set_source_inpx(monkeypatch, source)
    _set_dataset(monkeypatch, {"source": "uploaded", "kind": "inpx", "filename": "current.inpx"})

    capabilities = simulations_router._simulation_capabilities()

    assert capabilities["can_run"] is True
    assert capabilities["reason"] is None


def test_uploaded_inpx_with_missing_source_is_refused_naming_the_inpx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _ready_env(monkeypatch)
    missing_source = tmp_path / "does_not_exist.inpx"
    _set_source_inpx(monkeypatch, missing_source)
    _set_dataset(monkeypatch, {"source": "uploaded", "kind": "inpx", "filename": "does_not_exist.inpx"})

    capabilities = simulations_router._simulation_capabilities()

    assert capabilities["can_run"] is False
    assert capabilities["source_inpx_exists"] is False
    assert f"Simulation INPX not found: {missing_source}" in capabilities["reason"]
    # The honest reason names the missing INPX itself -- never .vissimpdb,
    # which nothing in this path requires.
    assert "vissimpdb" not in capabilities["reason"]
    assert "zip" not in capabilities["reason"].lower()


def test_non_uploaded_default_dataset_is_refused_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The built-in 기본 프로젝트/practice dataset's semantics are UNCHANGED
    by this unit: `source != "uploaded"` must still refuse, even though its
    bundled .inpx exists on disk -- the door this unit opens is for uploaded
    inpx datasets only, not for the demo network."""
    _ready_env(monkeypatch)
    bundled_source = tmp_path / "practice.inpx"
    bundled_source.write_text("<net/>", encoding="utf-8")
    _set_source_inpx(monkeypatch, bundled_source)
    _set_dataset(monkeypatch, {"source": "default", "kind": "default", "filename": "practice.inpx"})

    capabilities = simulations_router._simulation_capabilities()

    assert capabilities["can_run"] is False
    assert capabilities["reason"] == "Upload a VISSIM dataset (INPX file or project ZIP) before running simulation."


def test_uploaded_zip_reason_pinned_verbatim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pins the exact refusal string for the "nothing uploaded yet" reason,
    across all three other reasons too, so a future edit that silently
    changes wording (or reintroduces a zip-only claim) fails here."""
    _ready_env(monkeypatch)
    monkeypatch.setattr(simulations_router, "_check_pywin32", lambda: (False, "no pywin32 in this env"))
    # NOT the real os.name (see module docstring): `_ready_env` already wired
    # `_is_windows` as an overridable seam, so "not windows" is simulated by
    # overriding THAT, never by mutating the process-wide os module.
    monkeypatch.setattr(simulations_router, "_is_windows", lambda: False)
    missing_source = tmp_path / "missing.inpx"
    _set_source_inpx(monkeypatch, missing_source)
    _set_dataset(monkeypatch, {"source": "default", "kind": "default", "filename": "practice.inpx"})

    capabilities = simulations_router._simulation_capabilities()

    assert capabilities["can_run"] is False
    assert capabilities["reason"] == (
        "Upload a VISSIM dataset (INPX file or project ZIP) before running simulation. "
        "VISSIM COM can run only on Windows with PTV Vissim installed. "
        "pywin32/pythoncom is not available in this backend environment. "
        f"Simulation INPX not found: {missing_source}"
    )


# ---------------------------------------------------------------------------
# The capability payload's field itself -- must not keep a name that lies
# once an uploaded inpx is runnable too.
# ---------------------------------------------------------------------------


def test_capability_payload_reports_a_truthful_uploaded_dataset_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _ready_env(monkeypatch)
    source = tmp_path / "current.inpx"
    source.write_text("<net/>", encoding="utf-8")
    _set_source_inpx(monkeypatch, source)
    _set_dataset(monkeypatch, {"source": "uploaded", "kind": "inpx", "filename": "current.inpx"})

    capabilities = simulations_router._simulation_capabilities()

    # The old field name (`requires_uploaded_zip`) asserted "must be a zip
    # specifically" -- no longer true, so it must not still be on the wire.
    assert "requires_uploaded_zip" not in capabilities
    assert capabilities["requires_uploaded_dataset"] is True
    assert capabilities["dataset_kind"] == "inpx"
    assert capabilities["dataset_source"] == "uploaded"
