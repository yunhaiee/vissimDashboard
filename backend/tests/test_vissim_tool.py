"""Pins get_vissim_status's two safety properties without touching COM.

Both exist because of docs/phase2/00-EXECUTION-GUIDE.md rules 3 and 4:
the probe must never attach to a running Vissim GUI (this call site was
the one that missed ``attach_to_running=False`` once), and it must never
pass the live active-dataset path to LoadNet — it probes a normalized
temp copy instead.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

import services.vissim_tool as vissim_tool


@pytest.fixture
def com_available(monkeypatch):
    monkeypatch.setattr(vissim_tool, "check_pywin32", lambda: (True, None))
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setitem(
        sys.modules,
        "pythoncom",
        types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None),
    )


class FakeController:
    """Records connect kwargs and load paths; never touches COM."""

    instances: list["FakeController"] = []

    def __init__(self) -> None:
        self.connect_kwargs: dict | None = None
        self.loaded_paths: list[str] = []
        self.loaded_contents: list[str] = []
        self.vissim = None
        self.prog_id = "FAKE.Vissim"
        self._owns_instance = True
        FakeController.instances.append(self)

    def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs

    def load_network(self, inpx_path: str) -> None:
        self.loaded_paths.append(inpx_path)
        # Read at load time: the temp copy must already be normalized.
        self.loaded_contents.append(Path(inpx_path).read_text(encoding="utf-8"))

    def disconnect(self) -> None:
        pass


@pytest.fixture
def fake_controller(monkeypatch):
    FakeController.instances = []
    monkeypatch.setattr(vissim_tool, "VissimController", FakeController)
    return FakeController


def test_default_probe_short_circuits_without_com(com_available, monkeypatch):
    class Untouchable:
        def __init__(self) -> None:
            raise AssertionError("launch off must mean no COM connection attempt at all")

    monkeypatch.setattr(vissim_tool, "VissimController", Untouchable)

    status = vissim_tool.get_vissim_status(launch_if_missing=False, load_active_network=False)

    assert status["executed"] is False
    assert status["connected"] is False
    assert "launch" in status["reason"].lower()


def test_probe_never_attaches_to_running_gui(com_available, fake_controller):
    status = vissim_tool.get_vissim_status(launch_if_missing=True, load_active_network=False)

    assert status["connected"] is True
    (ctrl,) = fake_controller.instances
    assert ctrl.connect_kwargs["attach_to_running"] is False
    assert status["loaded_active_network"] is False


def test_probe_loads_normalized_temp_copy_not_live_source(com_available, fake_controller, monkeypatch, tmp_path):
    source = tmp_path / "current.inpx"
    source.write_text('<network><netPara underScenMngm="true"/></network>', encoding="utf-8")
    monkeypatch.setattr(vissim_tool, "get_active_inpx_path", lambda: source)
    monkeypatch.setattr(vissim_tool, "get_active_dataset", lambda: {})

    status = vissim_tool.get_vissim_status(launch_if_missing=True, load_active_network=True)

    assert status["connected"] is True
    assert status["loaded_active_network"] is True
    assert status["scenario_management_normalized"] is True
    (ctrl,) = fake_controller.instances
    (loaded_path,) = ctrl.loaded_paths
    assert Path(loaded_path) != source
    assert Path(loaded_path).name == source.name
    (loaded_content,) = ctrl.loaded_contents
    assert 'underScenMngm="false"' in loaded_content
    # The live source is byte-for-byte untouched.
    assert 'underScenMngm="true"' in source.read_text(encoding="utf-8")
    # The temp probe dir was cleaned up (fake controller holds no handles).
    assert not Path(loaded_path).parent.exists()
