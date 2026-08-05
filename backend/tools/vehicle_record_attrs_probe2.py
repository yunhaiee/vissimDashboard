"""Follow-up #2: VehRecSelectedAttributes has no Add/AddAttribute/AddItem, but
does have ReplaceAll / SetMultipleAttributes / SetMultiAttValues / Attributes.
Try each plausible call shape to add a SPEED column, verify by re-reading, then
(if successful) run a short RunContinuous and confirm SPEED actually appears
in the .fzp header + data.
"""
from __future__ import annotations

import os
import shutil
import sys
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "vehicle-record-attrs-probe2"


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


def _current_ids(sel) -> list[str]:
    return [item.AttValue("AttributeID") for item in sel]


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

        _log(f"Attributes property -> {getattr(sel, 'Attributes', 'N/A')!r}")
        try:
            _log(f"AttrExists('SPEED') -> {sel.AttrExists('SPEED')}")
        except Exception as exc:
            _log(f"AttrExists('SPEED') -> ERROR: {exc}")
        try:
            _log(f"AttrExists('SIMSEC') -> {sel.AttrExists('SIMSEC')}")
        except Exception as exc:
            _log(f"AttrExists('SIMSEC') -> ERROR: {exc}")

        target_ids = ["SIMSEC", "NO", "LANE\\LINK\\NO", "LANE\\INDEX", "POS", "SPEED"]

        _log("\n-- trying ReplaceAll --")
        for args in (
            (target_ids,),
            (",".join(target_ids),),
        ):
            try:
                result = sel.ReplaceAll(*args)
                _log(f"  [ok ] ReplaceAll({args!r}) -> {result!r}")
                _log(f"        now: {_current_ids(sel)}")
                break
            except Exception as exc:
                _log(f"  [err] ReplaceAll({args!r}) -> {type(exc).__name__}: {exc}")

        _log("\n-- trying SetMultipleAttributes --")
        for args in (
            (target_ids,),
            (",".join(target_ids),),
        ):
            try:
                result = sel.SetMultipleAttributes(*args)
                _log(f"  [ok ] SetMultipleAttributes({args!r}) -> {result!r}")
                _log(f"        now: {_current_ids(sel)}")
                break
            except Exception as exc:
                _log(f"  [err] SetMultipleAttributes({args!r}) -> {type(exc).__name__}: {exc}")

        _log(f"\nCurrent selection after attempts: {_current_ids(sel)}")

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
