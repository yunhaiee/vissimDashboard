from __future__ import annotations

from pathlib import Path

from routers.simulations import (
    MAX_ERR_LINES_IN_JOB_ERROR,
    SimParams,
    SimulationParameterError,
    _err_context_for_job,
    _err_error_lines,
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


def test_format_simulation_error_with_no_network_path_is_unchanged():
    """The default (``network_path=None``) — every OTHER caller of
    ``_format_simulation_error`` (e.g. the agent-run job path) — must see
    exactly the same message as before this fix: no trailing blank .err
    section, nothing appended."""
    message = _format_simulation_error(RuntimeError("boom"))
    assert message == "Simulation failed: boom"


# ---------------------------------------------------------------------------
# #74 fix (2) — a failed run's .err content, discarded before this, now
# reaches the job error field the UI already renders. Fabricated .err files in
# a temp dir; no COM anywhere in this section.
# ---------------------------------------------------------------------------


def _fabricated_err_text(error_count: int) -> str:
    """Shaped like the REAL .err from the live failure this fix was measured
    against (runtime/simulations/bba7f4eb/current.bba7f4eb.interactive_001.err,
    2026-08-27): severity, a tab, then the message, right-padded whitespace
    before the severity column. One non-fatal ``Warning`` and the trailing
    ``critical error`` summary line are always appended, mirroring that file's
    own shape (59 ``Error`` lines + 1 ``critical error`` line)."""
    lines = [
        f"                    Error\tNodes {i}: Node {1000 + i} and {i + 1}: "
        f"Node {2000 + i} are overlapping.\tNetwork object type: Node"
        for i in range(1, error_count + 1)
    ]
    lines.append("                    Warning\tNon-fatal: unrelated informational row.")
    lines.append("           critical error\tSimulation run 1 cannot be started because of errors.")
    return "\n".join(lines) + "\n"


def test_err_error_lines_keeps_error_and_critical_error_excludes_warning():
    lines = _err_error_lines(_fabricated_err_text(3))

    assert len(lines) == 4  # 3 "Error" rows + 1 "critical error" summary row
    assert lines[0].startswith("Error\tNodes 1:")
    assert lines[-1].startswith("critical error\tSimulation run 1")
    assert not any("Warning" in line for line in lines)


def test_err_error_lines_returns_nothing_for_a_clean_err_file():
    """An .err can exist for a run that never named a failure (informational
    rows only) -- only a file that actually marks an Error is worth attaching
    anywhere."""
    assert _err_error_lines("                    Info\tNothing wrong here.\n") == []


def test_err_context_for_job_attaches_capped_error_lines_and_names_the_path(
    tmp_path: Path,
):
    network_path = tmp_path / "current.job123.interactive.inpx"
    network_path.write_text("<network/>", encoding="utf-8")
    err_path = tmp_path / "current.job123.interactive_001.err"
    total_error_rows = MAX_ERR_LINES_IN_JOB_ERROR + 6  # 26 at the current cap of 20
    err_path.write_text(_fabricated_err_text(total_error_rows - 1), encoding="utf-8")

    context = _err_context_for_job(network_path)

    assert str(err_path) in context
    assert err_path.name in context
    assert "Nodes 1: Node 1001" in context
    # The MAX_ERR_LINES_IN_JOB_ERROR'th Error line is the last one shown...
    assert f"Nodes {MAX_ERR_LINES_IN_JOB_ERROR}: " in context
    # ...and the next one is not -- it is only counted, never silently dropped.
    assert f"Nodes {MAX_ERR_LINES_IN_JOB_ERROR + 1}: " not in context
    assert "6 more line(s)" in context
    assert "Warning" not in context


def test_err_context_for_job_is_empty_with_no_err_file_beside_the_network(
    tmp_path: Path,
):
    network_path = tmp_path / "current.job456.interactive.inpx"
    network_path.write_text("<network/>", encoding="utf-8")

    assert _err_context_for_job(network_path) == ""
    # And _format_simulation_error, given the same path, is unchanged too.
    message = _format_simulation_error(RuntimeError("boom"), network_path)
    assert message == "Simulation failed: boom"


def test_err_context_for_job_ignores_an_err_file_from_a_different_run(tmp_path: Path):
    """A job work dir holds exactly one run's network today, but the match is
    by stem anyway rather than "any .err in this directory" -- an unrelated
    .err left by a different attempt must never be attributed to this one."""
    network_path = tmp_path / "current.job789.interactive.inpx"
    network_path.write_text("<network/>", encoding="utf-8")
    other_err = tmp_path / "current.OTHERJOB.interactive_001.err"
    other_err.write_text(_fabricated_err_text(5), encoding="utf-8")

    assert _err_context_for_job(network_path) == ""


def test_err_context_for_job_is_empty_when_network_path_is_none():
    assert _err_context_for_job(None) == ""


def test_format_simulation_error_attaches_the_live_measured_overlap_message(
    tmp_path: Path,
):
    """End-to-end shape of the fix: the raw COM message the UI showed before
    (2026-08-27 live run SIM_bba7f4eb) plus the node-overlap reason that was
    previously discarded, both now reach the job error field."""
    network_path = tmp_path / "current.bba7f4eb.interactive.inpx"
    network_path.write_text("<network/>", encoding="utf-8")
    err_path = tmp_path / "current.bba7f4eb.interactive_001.err"
    err_path.write_text(
        "                    Error\tNodes 1: Node 318917839 and 65: "
        "Node 11994626502 are overlapping.\tNetwork object type: Node\n"
        "           critical error\tSimulation run 1 cannot be started because of errors.\n",
        encoding="utf-8",
    )

    message = _format_simulation_error(
        RuntimeError("The simulation could not be continued"), network_path
    )

    assert message.startswith("Simulation failed: The simulation could not be continued")
    assert "Nodes 1: Node 318917839 and 65: Node 11994626502 are overlapping." in message
    assert "critical error\tSimulation run 1 cannot be started" in message
    assert str(err_path) in message
