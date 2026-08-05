"""Follow-up to vehicle_record_probe.py: is ``Evaluation.VehRecSelectedAttributes``
(discovered via the first probe's dir(Evaluation) scan) a COM-editable
collection we can use to add a SPEED column (and canonical-ish LINK/LANE
columns) to the exported .fzp before a run -- or is it read-only / fixed to
whatever the source network's own .inpx <selectedAttributes> already lists?

Always disconnects its dedicated Vissim instance in a ``finally`` block.
Usage: python tools/vehicle_record_attrs_probe.py [example_dir]
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "vehicle-record-attrs-probe"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(example_dir: Path) -> Path:
    for stale in WORK_ROOT.glob(f"{example_dir.name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{example_dir.name}_{os.getpid()}"
    shutil.copytree(example_dir, work_dir)
    return work_dir


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    work_dir = _fresh_work_dir(example_dir)
    working = sorted(work_dir.glob("*.inpx"))[0]
    _log(f"Working copy: {working}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        evaluation = controller.vissim.Evaluation

        sel = evaluation.VehRecSelectedAttributes
        _log(f"\nVehRecSelectedAttributes -> {sel!r}")
        _log(f"type: {type(sel)}")
        names = sorted(n for n in dir(sel) if not n.startswith("_"))
        _log(f"dir() -> {names}")

        for prop in ("Count", "AttributesCount"):
            try:
                _log(f".{prop} -> {getattr(sel, prop)}")
            except Exception as exc:
                _log(f".{prop} -> ERROR: {exc}")

        # Enumerate current items, if it's iterable / has GetAll / GetMultipleAttributes
        for method in ("GetAll", "GetMultipleAttributes"):
            fn = getattr(sel, method, None)
            if fn is None:
                continue
            try:
                items = fn()
                _log(f".{method}() -> {items!r}")
                for i, item in enumerate(items):
                    try:
                        item_names = sorted(n for n in dir(item) if not n.startswith("_"))
                        _log(f"   item[{i}] dir() -> {item_names}")
                        for candidate_attr in ("AttributeID", "AttValue"):
                            try:
                                val = getattr(item, candidate_attr)
                                if callable(val):
                                    continue
                                _log(f"     .{candidate_attr} -> {val!r}")
                            except Exception:
                                pass
                        try:
                            _log(f"     .AttValue('AttributeID') -> {item.AttValue('AttributeID')!r}")
                        except Exception as exc:
                            _log(f"     .AttValue('AttributeID') -> ERROR: {exc}")
                    except Exception as exc:
                        _log(f"   item[{i}] introspection error: {exc}")
                    if i > 10:
                        break
            except Exception as exc:
                _log(f".{method}() -> ERROR: {exc}")

        # Try iterating directly (some Vissim collections support Python iteration
        # once early-bound via gencache, per check_network_probe.py's precedent).
        try:
            count = 0
            for item in sel:
                count += 1
                if count <= 15:
                    try:
                        aid = item.AttValue("AttributeID")
                    except Exception as exc:
                        aid = f"ERROR: {exc}"
                    _log(f"  iter item {count}: AttributeID={aid!r}")
            _log(f"Total iterable items: {count}")
        except Exception as exc:
            _log(f"Direct iteration failed: {exc}")

        # Try adding a SPEED attribute -- multiple plausible method shapes.
        _log("\n-- attempting to ADD a SPEED attribute selection --")
        for method_name, args in (
            ("AddAttribute", ("SPEED",)),
            ("Add", ("SPEED",)),
            ("AddItem", ("SPEED",)),
        ):
            fn = getattr(sel, method_name, None)
            if fn is None:
                _log(f"  [no ] .{method_name} does not exist")
                continue
            try:
                result = fn(*args)
                _log(f"  [ok ] .{method_name}{args} -> {result!r}")
            except Exception as exc:
                _log(f"  [err] .{method_name}{args} -> {type(exc).__name__}: {exc}")

        # Re-read to see if a SPEED-like attribute is now present (or was already).
        _log("\n-- re-scanning items after add attempts --")
        try:
            count = 0
            for item in sel:
                count += 1
                try:
                    aid = item.AttValue("AttributeID")
                except Exception as exc:
                    aid = f"ERROR: {exc}"
                _log(f"  item {count}: AttributeID={aid!r}")
            _log(f"Total items now: {count}")
        except Exception as exc:
            _log(f"Direct iteration failed: {exc}")

    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("\nDisconnected.")


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()
