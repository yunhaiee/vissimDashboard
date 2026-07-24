"""Tests for services.vissim_com's network-performance evaluation verb pair:
``configure_network_performance_evaluation`` / ``get_network_performance_evaluation_config``.

A fake ``self.vissim.Evaluation`` COM stub stands in for real COM (COM tasks
need the real Vissim on this machine per docs/phase2/00-EXECUTION-GUIDE.md
SS5 -- this file proves the pure setter/getter/verification LOGIC; live truth
comes from ``backend/tools/evaluation_probe.py``, which verified the exact
attribute ids against a real Vissim 2026 instance before this module was
written around them):

  vehicle_network_performance: VehNetPerfCollectData/FromTime/ToTime/Interval
  node_results:                NodeResCollectData/FromTime/ToTime/Interval
  vehicle_travel_time:         VehTravTmsCollectData/FromTime/ToTime/Interval
  queue_counters:              QueuesCollectData/FromTime/ToTime/Interval

All four are configured together because services/results_reader.py's
_read_db unconditionally queries (no fallback) the result table each one
produces, and raises -- losing even a successfully-read table -- if any ONE
is missing.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.vissim_com import VissimController, _EVAL_TYPE_ATTR_IDS


class _FakeEvaluation:
    """Minimal AttValue/SetAttValue stub mirroring Vissim's COM Evaluation
    object -- a dict of attribute id -> value, with SetAttValue optionally
    rigged to raise (refusal) or to silently write something OTHER than what
    was asked (simulating a real Vissim mismatch)."""

    def __init__(
        self,
        initial: dict[str, Any] | None = None,
        *,
        raise_on_set: str | None = None,
        raise_on_get: str | None = None,
        corrupt_field: str | None = None,
        corrupt_value: Any = -1,
    ) -> None:
        self._values: dict[str, Any] = dict(initial or {})
        self._raise_on_set = raise_on_set
        self._raise_on_get = raise_on_get
        self._corrupt_field = corrupt_field
        self._corrupt_value = corrupt_value
        self.set_calls: list[tuple[str, Any]] = []

    def SetAttValue(self, attr_id: str, value: Any) -> None:
        if self._raise_on_set is not None and attr_id == self._raise_on_set:
            raise RuntimeError(f"COM refused to set {attr_id}")
        self.set_calls.append((attr_id, value))
        if self._corrupt_field is not None and attr_id == self._corrupt_field:
            # Simulate Vissim silently clamping/ignoring the write.
            self._values[attr_id] = self._corrupt_value
        else:
            self._values[attr_id] = value

    def AttValue(self, attr_id: str) -> Any:
        if self._raise_on_get is not None and attr_id == self._raise_on_get:
            raise RuntimeError(f"COM could not find attribute {attr_id}")
        return self._values.get(attr_id, 0)


class _FakeVissim:
    def __init__(self, evaluation: _FakeEvaluation) -> None:
        self.Evaluation = evaluation


def _controller_with(evaluation: _FakeEvaluation) -> VissimController:
    controller = VissimController()
    controller.vissim = _FakeVissim(evaluation)
    return controller


def _all_default_values() -> dict[str, Any]:
    """Every attribute id in _EVAL_TYPE_ATTR_IDS defaulted to a falsy/0
    value, mirroring an untouched network before configuration."""
    values: dict[str, Any] = {}
    for fields in _EVAL_TYPE_ATTR_IDS.values():
        for attr_id in fields.values():
            values[attr_id] = 0
    return values


# ---------------------------------------------------------------------------
# get_network_performance_evaluation_config -- the paired getter, read alone
# ---------------------------------------------------------------------------


def test_get_config_reads_all_four_evaluation_types():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.get_network_performance_evaluation_config()

    assert set(config.keys()) == set(_EVAL_TYPE_ATTR_IDS.keys())
    for eval_type in _EVAL_TYPE_ATTR_IDS:
        assert config[eval_type] == {
            "collect_data": False,
            "from_time": 0,
            "to_time": 0,
            "interval": 0,
        }


def test_get_config_raises_runtime_error_when_com_read_fails():
    evaluation = _FakeEvaluation({}, raise_on_get="VehNetPerfCollectData")
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="Could not read network-performance"):
        controller.get_network_performance_evaluation_config()


def test_get_config_raises_when_only_a_sibling_evaluation_type_fails_to_read():
    """Even a field failure on a NON-vehNetPerf type (e.g. node_results) must
    surface -- this is the exact fragility class that made an earlier
    version of this fix silently under-configure the run."""
    evaluation = _FakeEvaluation(_all_default_values(), raise_on_get="NodeResCollectData")
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="Could not read network-performance"):
        controller.get_network_performance_evaluation_config()


# ---------------------------------------------------------------------------
# configure_network_performance_evaluation -- the setter + its own read-back
# ---------------------------------------------------------------------------


def test_configure_arms_all_four_evaluation_types_with_a_single_bucket():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.configure_network_performance_evaluation(duration_s=600)

    assert set(config.keys()) == set(_EVAL_TYPE_ATTR_IDS.keys())
    for eval_type in _EVAL_TYPE_ATTR_IDS:
        assert config[eval_type] == {
            "collect_data": True,
            "from_time": 0,
            "to_time": 600,
            "interval": 600,
        }
    # Every one of the 16 (4 types x 4 fields) attribute ids was actually set.
    set_ids = {attr_id for attr_id, _ in evaluation.set_calls}
    all_attr_ids = {aid for fields in _EVAL_TYPE_ATTR_IDS.values() for aid in fields.values()}
    assert set_ids == all_attr_ids


def test_configure_respects_a_nonzero_from_time():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.configure_network_performance_evaluation(duration_s=3600, from_time=600)

    for eval_type in _EVAL_TYPE_ATTR_IDS:
        assert config[eval_type]["from_time"] == 600
        assert config[eval_type]["to_time"] == 3600


def test_configure_floors_duration_at_one_second():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.configure_network_performance_evaluation(duration_s=0)

    for eval_type in _EVAL_TYPE_ATTR_IDS:
        assert config[eval_type]["to_time"] == 1
        assert config[eval_type]["interval"] == 1


def test_configure_raises_when_com_set_refuses():
    evaluation = _FakeEvaluation(_all_default_values(), raise_on_set="VehNetPerfCollectData")
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="Could not configure network-performance"):
        controller.configure_network_performance_evaluation(duration_s=600)


def test_configure_raises_when_a_sibling_evaluation_types_set_call_refuses():
    """A refusal on ANY of the four types (not just vehicle_network_performance)
    must abort configuration -- e.g. node_results' SetAttValue failing."""
    evaluation = _FakeEvaluation(_all_default_values(), raise_on_set="QueuesInterval")
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="Could not configure network-performance"):
        controller.configure_network_performance_evaluation(duration_s=600)


def test_configure_raises_on_readback_mismatch_never_returns_a_lie():
    """The M2.1 doctrine: a setter that doesn't raise but whose read-back
    doesn't match what was written must still surface as a refusal, not a
    silent false "ok" -- mirrors write_verification_failed's reasoning,
    applied here to the evaluation-arming verb itself."""
    evaluation = _FakeEvaluation(_all_default_values(), corrupt_field="VehTravTmsToTime")
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="read-back mismatch"):
        controller.configure_network_performance_evaluation(duration_s=600)


def test_configure_raises_when_collect_data_readback_is_falsy_despite_no_exception():
    """Belt-and-suspenders: even if every SetAttValue call succeeds without
    raising, a collect_data read-back that comes back falsy must still be
    treated as "not armed" -- covered generically by the full-dict compare,
    exercised here explicitly for one of the four types."""
    evaluation = _FakeEvaluation(_all_default_values(), corrupt_field="NodeResCollectData", corrupt_value=0)
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="read-back mismatch"):
        controller.configure_network_performance_evaluation(duration_s=600)
