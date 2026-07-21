from __future__ import annotations

from routers.simulations import (
    SimParams,
    SimulationParameterError,
    _format_simulation_error,
    _validate_simulation_params,
)


class FakeVissimController:
    def __init__(self, vehicle_inputs: list[int], signal_controllers: list[int]) -> None:
        self._vehicle_inputs = vehicle_inputs
        self._signal_controllers = signal_controllers

    def available_vehicle_input_nos(self) -> list[int]:
        return self._vehicle_inputs

    def available_signal_controller_nos(self) -> list[int]:
        return self._signal_controllers


def test_validate_simulation_params_rejects_missing_vehicle_input():
    params = SimParams(vehicle_inputs={10: 1200})
    ctrl = FakeVissimController(vehicle_inputs=[1, 2, 3], signal_controllers=[1])

    try:
        _validate_simulation_params(ctrl, params)  # type: ignore[arg-type]
    except SimulationParameterError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected missing vehicle input to fail")

    assert "Vehicle input 10 does not exist" in message
    assert "Available vehicle inputs: 1, 2, 3" in message


def test_validate_simulation_params_rejects_missing_signal_controller():
    params = SimParams(signal_cycles={5: 90}, signal_offsets={2: 10})
    ctrl = FakeVissimController(vehicle_inputs=[1], signal_controllers=[1, 2])

    try:
        _validate_simulation_params(ctrl, params)  # type: ignore[arg-type]
    except SimulationParameterError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected missing signal controller to fail")

    assert "Signal controller 5 does not exist" in message
    assert "Available signal controllers: 1, 2" in message


def test_format_simulation_error_hides_raw_item_by_key_com_error():
    raw_error = (
        "(-2147352567, 'exception', (0, 'VISSIM.Vissim.2600', "
        "'CCOMVehicleInputContainerBase<CVehicleInputManager>::get_ItemByKey failed: object does not exist'))"
    )

    message = _format_simulation_error(RuntimeError(raw_error))

    assert "getitembykey" not in message.lower()
    assert "object number" in message
