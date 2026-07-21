"""Read-only VISSIM COM tool used by the assistant router."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from services.datasets import get_active_dataset, get_active_inpx_path
from services.vissim_com import VissimController


def get_vissim_com_capabilities() -> dict[str, Any]:
    pywin32_available, pywin32_error = check_pywin32()
    source_inpx = get_active_inpx_path()
    dataset = get_active_dataset()
    available = os.name == "nt" and pywin32_available
    return {
        "available": available,
        "environment": sys.platform,
        "is_windows": os.name == "nt",
        "pywin32_available": pywin32_available,
        "pywin32_error": pywin32_error,
        "source_inpx": str(source_inpx),
        "source_inpx_exists": source_inpx.exists(),
        "dataset_filename": dataset.get("filename"),
        "dataset_source": dataset.get("source"),
        "dataset_kind": dataset.get("kind"),
        "auto_launch": assistant_com_auto_launch(),
        "load_active_network": assistant_com_load_active_network(),
        "mode": "ready" if available else "unavailable",
    }


def get_vissim_status(
    *,
    launch_if_missing: bool | None = None,
    load_active_network: bool | None = None,
    visible: bool = False,
) -> dict[str, Any]:
    capabilities = get_vissim_com_capabilities()
    if not capabilities["available"]:
        return {
            **capabilities,
            "executed": False,
            "connected": False,
            "reason": "VISSIM COM is available only on Windows with pywin32 installed.",
        }

    import pythoncom

    launch = assistant_com_auto_launch() if launch_if_missing is None else launch_if_missing
    load_network = assistant_com_load_active_network() if load_active_network is None else load_active_network
    source_inpx = Path(str(capabilities["source_inpx"]))
    ctrl: VissimController | None = None
    initialized = False

    try:
        pythoncom.CoInitialize()
        initialized = True

        ctrl = VissimController()
        ctrl.connect(visible=visible, launch_if_missing=launch)
        loaded_active_network = False
        if load_network and source_inpx.exists():
            ctrl.load_network(str(source_inpx))
            loaded_active_network = True

        snapshot = collect_vissim_snapshot(ctrl.vissim)
        return {
            **capabilities,
            "executed": True,
            "connected": True,
            "reason": None,
            "prog_id": ctrl.prog_id,
            "launched": bool(ctrl._owns_instance),
            "loaded_active_network": loaded_active_network,
            **snapshot,
        }
    except Exception as exc:
        return {
            **capabilities,
            "executed": True,
            "connected": False,
            "reason": str(exc),
            "error": str(exc),
        }
    finally:
        if ctrl is not None:
            try:
                ctrl.disconnect()
            except Exception:
                pass
        if initialized:
            pythoncom.CoUninitialize()


def check_pywin32() -> tuple[bool, str | None]:
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except Exception as exc:
        return False, str(exc)
    return True, None


def assistant_com_auto_launch() -> bool:
    return os.getenv("VISSIM_ASSISTANT_COM_LAUNCH", "0").strip().lower() in {"1", "true", "yes", "on"}


def assistant_com_load_active_network() -> bool:
    return os.getenv("VISSIM_ASSISTANT_LOAD_ACTIVE_INPX", "0").strip().lower() in {"1", "true", "yes", "on"}


def collect_vissim_snapshot(vissim: Any) -> dict[str, Any]:
    net = safe_getattr(vissim, "Net")
    simulation = safe_getattr(vissim, "Simulation")
    counts = {
        "links": collection_count(safe_getattr(net, "Links")),
        "nodes": collection_count(safe_getattr(net, "Nodes")),
        "vehicle_inputs": collection_count(safe_getattr(net, "VehicleInputs")),
        "signal_controllers": collection_count(safe_getattr(net, "SignalControllers")),
        "detectors": collection_count(safe_getattr(net, "Detectors")),
        "data_collection_points": collection_count(safe_getattr(net, "DataCollectionPoints")),
    }
    return {
        "network": {
            "name": first_att_value(net, ["Name", "LongName", "ShortName"]),
            "scenario_management": first_att_value(net, ["ScenManagmt"]),
            "input_file": first_att_value(vissim, ["InputFile", "FileName"]),
        },
        "simulation": {
            "sim_sec": first_att_value(simulation, ["SimSec"]),
            "sim_period": first_att_value(simulation, ["SimPeriod"]),
            "random_seed": first_att_value(simulation, ["RandSeed"]),
            "number_of_runs": first_att_value(simulation, ["NumRuns"]),
        },
        "counts": counts,
        "signal_controllers": sample_collection(
            safe_getattr(net, "SignalControllers"),
            ["No", "Name", "CycleTime", "Offset", "ProgNo", "ProgNo(1)"],
        ),
        "vehicle_inputs": sample_collection(
            safe_getattr(net, "VehicleInputs"),
            ["No", "Name", "Volume(1)", "Volume"],
        ),
    }


def sample_collection(collection: Any, attributes: list[str], limit: int = 6) -> list[dict[str, Any]]:
    if collection is None:
        return []

    items = collection_items(collection)
    rows: list[dict[str, Any]] = []
    for item in items[:limit]:
        row: dict[str, Any] = {}
        for attribute in attributes:
            value = first_att_value(item, [attribute])
            if value is not None:
                row[attribute] = value
        if row:
            rows.append(row)
    return rows


def collection_items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    try:
        return list(collection.GetAll())
    except Exception:
        return []


def collection_count(collection: Any) -> int | None:
    if collection is None:
        return None
    try:
        value = getattr(collection, "Count")
        value = value() if callable(value) else value
        if value is not None:
            return int(value)
    except Exception:
        pass
    try:
        return len(collection_items(collection))
    except Exception:
        return None


def first_att_value(item: Any, names: list[str]) -> Any:
    if item is None:
        return None
    for name in names:
        try:
            value = item.AttValue(name)
        except Exception:
            continue
        value = json_safe(value)
        if value not in {None, ""}:
            return value
    return None


def safe_getattr(item: Any, name: str) -> Any:
    if item is None:
        return None
    try:
        return getattr(item, name)
    except Exception:
        return None


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return str(value)
    except Exception:
        return None
