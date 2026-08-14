"""Unit #33 — the open probe, EXTRACTED from ``tools/genesis_open_live_check.py``.

The extraction's whole risk is silent drift: ``services/vissim_open_probe.py``
now produces the record that used to be built inline inside that CLI's child
process, and TWO things read it — the CLI's ``state.json`` (whose shape a year
of live-check evidence in ``runtime/`` is written in) and
``gate.LoadOracle.from_probe_state`` (V1's only input). So this file pins the
record's KEY SET against the set the pre-extraction code produced, and proves
the CLI's own ``_com_phase`` still writes exactly that, with the same
``json.dumps`` call it always used.

No live Vissim anywhere: ``vissim_open_probe._connect_controller`` — the single
line that reaches COM — is monkeypatched to a fake controller, so LoadNet, the
``.err`` capture, the census read and SaveNetAs all run through their real code
paths against a fake that answers like Vissim does.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services import vissim_open_probe
from services.genesis.gate import LoadOracle

#: EXACTLY the keys ``tools/genesis_open_live_check.py::_com_phase`` wrote for a
#: successful run before the extraction (read off its own source at commit
#: c88fc34, the last one before unit #33). A key added here without a reason is
#: a change to the on-disk shape of every live-check record ever written; a key
#: REMOVED here is a field ``LoadOracle.from_probe_state`` may already be
#: reading. Either way the pin should fail and be argued about, not updated
#: reflexively.
_SUCCESS_KEYS = {
    "label",
    "network",
    "started_utc",
    "connected",
    "load_ok",
    "load_error",
    "save_ok",
    "save_error",
    "prog_id",
    "connect_seconds",
    "version",
    "err_files_before_load",
    "load_seconds",
    "err_after_load",
    "com_topology",
    "saved_path",
    "save_seconds",
    "err_after_save",
    "finished_utc",
}


class _FakeLinks:
    def __init__(self, pairs: list[tuple[int, int]]):
        self._pairs = pairs

    def GetMultipleAttributes(self, atts):  # noqa: N802 - COM's own casing
        assert tuple(atts) == ("No", "IsConn")
        return [(no, is_conn) for no, is_conn in self._pairs]


class _FakeCollection:
    def __init__(self, count: int):
        self.Count = count


class _FakeNet:
    def __init__(self, pairs: list[tuple[int, int]]):
        self.Links = _FakeLinks(pairs)
        self.Nodes = _FakeCollection(1)
        self.VehicleCompositions = _FakeCollection(1)


class _FakeVissim:
    def __init__(self, pairs: list[tuple[int, int]]):
        self.Net = _FakeNet(pairs)

    def AttValue(self, name: str) -> str:  # noqa: N802 - COM's own casing
        if name == "Version":
            return "PTV Vissim Fake 2026"
        raise RuntimeError(f"no such attribute: {name}")


class FakeController:
    """``VissimController``'s surface for the probe's four calls, and nothing else."""

    def __init__(self, *, pairs=((1, 0), (2, 0), (10001, 1)), load_error: str | None = None):
        self.prog_id = "VISSIM.Vissim.Fake"
        self.vissim = _FakeVissim(list(pairs))
        self._load_error = load_error
        self.loaded: str | None = None
        self.saved: Path | None = None
        self.disconnected = False

    def connect(self, **kwargs):  # pragma: no cover - the seam replaces this
        raise AssertionError("the probe must connect through _connect_controller")

    def load_network(self, path: str) -> None:
        if self._load_error:
            raise RuntimeError(self._load_error)
        self.loaded = path

    def get_link(self, no: int) -> dict[str, Any]:
        return {"no": no, "is_connector": no > 1000}

    def save_net_as(self, path) -> None:
        Path(path).write_text("<network/>", encoding="utf-8")
        self.saved = Path(path)

    def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture()
def fake_com(monkeypatch: pytest.MonkeyPatch) -> FakeController:
    controller = FakeController()
    monkeypatch.setattr(vissim_open_probe, "_connect_controller", lambda: controller)
    return controller


def _network(tmp_path: Path) -> Path:
    path = tmp_path / "probe_me.inpx"
    path.write_text("<network/>", encoding="utf-8")
    return path


def test_probe_record_keeps_the_pre_extraction_key_set(tmp_path: Path, fake_com: FakeController):
    """The CLI's ``state.json`` shape did not move when the code did."""
    record = vissim_open_probe.probe_open(_network(tmp_path), "live_a")

    assert set(record) == _SUCCESS_KEYS
    assert record["load_ok"] is True
    assert record["save_ok"] is True
    assert record["connected"] is True
    assert record["version"]["att_values"]["Version"] == "PTV Vissim Fake 2026"
    assert record["com_topology"]["link_collection_count"] == 3
    assert record["com_topology"]["is_conn_counts"] == {
        "plain_links": 2,
        "connectors": 1,
        "read_via": "GetMultipleAttributes",
    }
    assert record["com_topology"]["nodes_count"] == 1


def test_the_probe_never_writes_back_to_the_file_it_opened(
    tmp_path: Path, fake_com: FakeController
):
    """``SaveNetAs`` goes to a NEW name in the same directory. The file handed
    in is read-only input — the whole COM doctrine in one assertion."""
    net = _network(tmp_path)
    before = net.read_bytes()

    record = vissim_open_probe.probe_open(net, "live_a")

    assert net.read_bytes() == before
    assert Path(record["saved_path"]).name == "probe_me_saved.inpx"
    assert fake_com.saved is not None and fake_com.saved != net
    assert fake_com.disconnected is True


def test_a_refused_load_is_a_finding_not_a_probe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Vissim refusing a file is the answer V1 exists to record. The record
    still comes back, with the error verbatim and no ``fatal_error``."""
    controller = FakeController(load_error="The network file could not be loaded.")
    monkeypatch.setattr(vissim_open_probe, "_connect_controller", lambda: controller)

    record = vissim_open_probe.probe_open(_network(tmp_path), "live_a")

    assert record["load_ok"] is False
    assert "could not be loaded" in record["load_error"]
    assert "fatal_error" not in record
    # No census and no save were attempted on a network that never loaded.
    assert "com_topology" not in record
    assert record["save_ok"] is None


def test_no_com_at_all_is_recorded_as_a_fatal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def _no_com():
        raise ImportError("No module named 'pythoncom'")

    monkeypatch.setattr(vissim_open_probe, "_connect_controller", _no_com)

    record = vissim_open_probe.probe_open(_network(tmp_path), "live_a")

    assert record["connected"] is False
    assert "pythoncom" in record["fatal_error"]
    assert record["load_ok"] is None


def test_on_update_fires_after_every_step(tmp_path: Path, fake_com: FakeController):
    """The CLI's "written after EVERY step" rule survives the extraction: a
    probe that dies mid-COM must leave behind what it already measured."""
    snapshots: list[dict[str, Any]] = []
    vissim_open_probe.probe_open(
        _network(tmp_path), "live_a", on_update=lambda current: snapshots.append(dict(current))
    )

    assert len(snapshots) >= 5
    # The FIRST snapshot exists before anything was measured -- that is the one
    # that makes a mid-COM death legible rather than silent.
    assert snapshots[0]["connected"] is False
    assert snapshots[0]["load_ok"] is None
    assert snapshots[-1]["load_ok"] is True


def test_the_cli_writes_the_probe_record_verbatim(tmp_path: Path, fake_com: FakeController):
    """``tools/genesis_open_live_check.py``'s child phase, byte-compatible.

    Runs the CLI's OWN ``_com_phase`` (not a re-implementation) and asserts the
    JSON it writes is exactly ``json.dumps(record, ensure_ascii=False, indent=2,
    sort_keys=True)`` of the probe's record — the same call, the same shape, the
    same file. That is the compatibility claim the extraction rests on.
    """
    import importlib.util
    import sys

    tool_path = Path(vissim_open_probe.__file__).resolve().parent.parent / "tools" / "genesis_open_live_check.py"
    spec = importlib.util.spec_from_file_location("_live_check_cli_probe", tool_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)

        net = _network(tmp_path)
        out = tmp_path / "live_a_com.json"
        rc = module._com_phase(net, "live_a", out)
    finally:
        sys.modules.pop(spec.name, None)

    assert rc == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert set(written) == _SUCCESS_KEYS
    assert written["label"] == "live_a"
    assert out.read_text(encoding="utf-8") == json.dumps(
        written, ensure_ascii=False, indent=2, sort_keys=True
    )

    # And the record the CLI wrote is admissible as V1's evidence, which is the
    # only reason its shape matters at all.
    oracle, gap = LoadOracle.from_probe_state(
        {"runs": {"live_a": {"label": "live_a", "network": str(net), "run": {"com": written}}}},
        label="live_a",
    )
    assert gap is None
    assert oracle is not None
    assert oracle.load_ok is True
    assert oracle.com_link_count == 3
    assert oracle.com_plain_link_count == 2
    assert oracle.com_connector_count == 1
    assert oracle.vissim_version == "PTV Vissim Fake 2026"
