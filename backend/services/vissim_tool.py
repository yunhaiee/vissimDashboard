"""Read-only VISSIM COM tool used by the assistant router."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from services.agent.state import _copy_sibling_assets, _disable_scenario_management_flag
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


_TEMP_PROBE_PREFIX = "vissim-status-probe-"


def _sweep_stale_probe_dirs(max_age_s: float = 3600.0) -> None:
    """Best-effort removal of probe temp dirs earlier runs left behind.

    Vissim holds the probe dir NODE (not its contents) until its process
    fully exits, which outlasts the post-disconnect retry in
    ``get_vissim_status`` — verified live: the retry empties the dir, the
    empty dir itself lingers. Age-gated so a sweep can never race a
    concurrent probe's seconds-old dir."""
    try:
        cutoff = time.time() - max_age_s
        for stale in Path(tempfile.gettempdir()).glob(_TEMP_PROBE_PREFIX + "*"):
            try:
                if stale.stat().st_mtime < cutoff:
                    shutil.rmtree(stale, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


def get_vissim_status(
    *,
    launch_if_missing: bool | None = None,
    load_active_network: bool | None = None,
    visible: bool = False,
) -> dict[str, Any]:
    """Probe VISSIM COM with a dedicated, probe-owned instance.

    Never attaches to a running Vissim (docs/phase2/00-EXECUTION-GUIDE.md
    rule 3: GetActiveObject can grab a human's open GUI — connect() would
    flip its QuickMode and an opted-in ``load_active_network`` would LoadNet
    over their open work). Consequence: with ``launch_if_missing`` off (the
    default everywhere) there is nothing safe to probe, so the result is
    ``connected: False`` with a reason explaining how to opt into a launch.

    ``load_active_network`` never passes the live active-dataset path to
    LoadNet (rule 4 adjacency): it loads a throwaway temp copy cloned the
    same way agent edit sessions do — sibling assets alongside, PTV
    scenario-management self-declaration normalized — so the probe answers
    "can the agent pipeline load this dataset", which is the question that
    matters, and the real client export doesn't fail it.
    """
    capabilities = get_vissim_com_capabilities()
    if not capabilities["available"]:
        return {
            **capabilities,
            "executed": False,
            "connected": False,
            "reason": "VISSIM COM is available only on Windows with pywin32 installed.",
        }

    launch = assistant_com_auto_launch() if launch_if_missing is None else launch_if_missing
    load_network = assistant_com_load_active_network() if load_active_network is None else load_active_network
    if not launch:
        return {
            **capabilities,
            "executed": False,
            "connected": False,
            "reason": (
                "Attaching to a running Vissim GUI is disabled for safety (automation "
                "must own its Vissim instance), and auto-launch is off, so there is no "
                "instance to probe. Pass launch=true (or set VISSIM_ASSISTANT_COM_LAUNCH=1) "
                "to probe with a dedicated Vissim instance."
            ),
        }

    import pythoncom

    source_inpx = Path(str(capabilities["source_inpx"]))
    ctrl: VissimController | None = None
    initialized = False
    temp_probe_dir: Path | None = None

    try:
        pythoncom.CoInitialize()
        initialized = True

        ctrl = VissimController()
        ctrl.connect(visible=visible, launch_if_missing=launch, attach_to_running=False)
        loaded_active_network = False
        scenario_management_normalized = False
        if load_network and source_inpx.exists():
            _sweep_stale_probe_dirs()
            temp_probe_dir = Path(tempfile.mkdtemp(prefix=_TEMP_PROBE_PREFIX))
            probe_copy = temp_probe_dir / source_inpx.name
            shutil.copyfile(source_inpx, probe_copy)
            _copy_sibling_assets(source_inpx, temp_probe_dir)
            scenario_management_normalized = _disable_scenario_management_flag(probe_copy)
            try:
                ctrl.load_network(str(probe_copy))
            except Exception as exc:
                raise RuntimeError(
                    f"Vissim could not load the active dataset '{source_inpx.name}' "
                    f"(probed via a temporary normalized copy): {exc}"
                ) from exc
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
            "scenario_management_normalized": scenario_management_normalized,
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
        if temp_probe_dir is not None:
            # The probe owns its instance, but Vissim releases the probe
            # files asynchronously on Exit — verified live: this retry
            # empties the dir, yet Vissim holds the dir NODE itself until
            # its process fully exits, longer than a probe should block.
            # An empty leftover dir is swept by the next probe's
            # _sweep_stale_probe_dirs; never fails the probe either way.
            for _ in range(10):
                shutil.rmtree(temp_probe_dir, ignore_errors=True)
                if not temp_probe_dir.exists():
                    break
                time.sleep(0.3)


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
