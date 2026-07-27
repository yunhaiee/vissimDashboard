"""Tests for services.vissim_com's vehicle-record (.fzp) verb pair:
``configure_vehicle_record_evaluation`` / ``get_vehicle_record_evaluation_config``.

A fake ``self.vissim.Evaluation`` COM stub stands in for real COM, same
pattern as ``test_vissim_com_evaluation.py`` -- this file proves the pure
setter/getter/verification LOGIC; live truth comes from
``backend/tools/vehicle_record_probe.py``, which verified the exact
attribute ids (VehRecWriteFile/VehRecWriteDatabase/VehRecFromTime/
VehRecToTime) against a real Vissim 2026 instance before this module was
written around them (see docs/phase2/DECISIONS.md's dated entry).
"""
from __future__ import annotations

from typing import Any

import pytest

from services.vissim_com import VissimController, _VEHICLE_RECORD_ATTR_IDS


class _FakeEvaluation:
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
    return {attr_id: 0 for attr_id in _VEHICLE_RECORD_ATTR_IDS.values()}


# ---------------------------------------------------------------------------
# get_vehicle_record_evaluation_config -- the paired getter, read alone
# ---------------------------------------------------------------------------


def test_get_config_reads_all_five_fields():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.get_vehicle_record_evaluation_config()

    assert config == {
        "write_file": False,
        "write_database": False,
        "from_time": 0,
        "to_time": 0,
        "resolution": 0,
    }


def test_get_config_raises_runtime_error_when_com_read_fails():
    evaluation = _FakeEvaluation({}, raise_on_get="VehRecWriteFile")
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="Could not read vehicle-record"):
        controller.get_vehicle_record_evaluation_config()


# ---------------------------------------------------------------------------
# configure_vehicle_record_evaluation -- the setter + its own read-back
# ---------------------------------------------------------------------------


def test_configure_arms_write_file_and_forces_write_database_off():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.configure_vehicle_record_evaluation(duration_s=60)

    assert config == {
        "write_file": True,
        "write_database": False,
        "from_time": 0,
        "to_time": 60,
        "resolution": 5,  # DEFAULT_VEHICLE_RECORD_RESOLUTION
    }
    set_ids = {attr_id for attr_id, _ in evaluation.set_calls}
    assert set_ids == set(_VEHICLE_RECORD_ATTR_IDS.values())


def test_configure_respects_a_custom_resolution():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.configure_vehicle_record_evaluation(duration_s=60, resolution=10)

    assert config["resolution"] == 10


def test_configure_floors_resolution_at_one():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.configure_vehicle_record_evaluation(duration_s=60, resolution=0)

    assert config["resolution"] == 1


def test_configure_respects_a_nonzero_from_time():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.configure_vehicle_record_evaluation(duration_s=600, from_time=60)

    assert config["from_time"] == 60
    assert config["to_time"] == 600


def test_configure_floors_duration_at_one_second():
    evaluation = _FakeEvaluation(_all_default_values())
    controller = _controller_with(evaluation)

    config = controller.configure_vehicle_record_evaluation(duration_s=0)

    assert config["to_time"] == 1


def test_configure_raises_when_com_set_refuses():
    evaluation = _FakeEvaluation(_all_default_values(), raise_on_set="VehRecWriteFile")
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="Could not configure vehicle-record"):
        controller.configure_vehicle_record_evaluation(duration_s=60)


def test_configure_raises_on_readback_mismatch_never_returns_a_lie():
    evaluation = _FakeEvaluation(_all_default_values(), corrupt_field="VehRecToTime")
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="read-back mismatch"):
        controller.configure_vehicle_record_evaluation(duration_s=60)


def test_configure_raises_when_write_file_readback_is_falsy_despite_no_exception():
    evaluation = _FakeEvaluation(
        _all_default_values(), corrupt_field="VehRecWriteFile", corrupt_value=0
    )
    controller = _controller_with(evaluation)

    with pytest.raises(RuntimeError, match="read-back mismatch"):
        controller.configure_vehicle_record_evaluation(duration_s=60)
