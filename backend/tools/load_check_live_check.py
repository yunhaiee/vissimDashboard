"""Live acceptance for v2 unit (3): the REAL registered ``load_check`` tool,
dispatched through ``services.agent.toolkit.dispatch`` exactly as the
orchestrator would, against real Vissim COM, on three networks:

  (1) a clean clone of the example network -> expect load_ok=True,
      init_ok=True.
  (2) a clone with its only <signalController> deleted (referenced by every
      <signalHead> via sg="<sc_no> *") -> expect load_ok=False, load_error
      mentioning the file could not be loaded.
  (3) a clone with a <link> deleted that a <signalHead> references via
      lane="<link_no> <lane_idx>" (this also breaks Static Vehicle Routes
      through that link on the Beijing example) -> expect load_ok=True,
      init_ok=False, err_content mentioning a route "is not complete".

This is the SAME corruption logic ``backend/tools/check_network_probe.py``
used for the Phase 1 probe (duplicated here deliberately, not imported —
matches this directory's existing precedent of self-contained live-check
scripts, e.g. reduced_speed_area_live_check.py not importing from
edit_live_probe.py).

Exercises real ``tools_run.set_active_run_source`` wiring (the same public
accessor ``routers/assistant.py`` sets for every turn) so this is a true
end-to-end proof of the registered tool, not just of the private
``_load_check`` COM helper (which the Phase 1 probe already proved directly).

Usage: python tools/load_check_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo).
"""
from __future__ import annotations

import os
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "load-check-live-check"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(name: str) -> Path:
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _corrupt_delete_signal_controller(inpx_path: Path) -> None:
    tree = ET.parse(inpx_path)
    root = tree.getroot()
    sc_parent = root.find("signalControllers")
    sc = sc_parent.find("signalController[@no='1']")
    if sc is None:
        raise RuntimeError("Expected <signalController no='1'> not found")
    sc_parent.remove(sc)
    tree.write(inpx_path, encoding="UTF-8", xml_declaration=True)


def _corrupt_delete_referenced_link(inpx_path: Path) -> None:
    tree = ET.parse(inpx_path)
    root = tree.getroot()
    signal_heads = root.find("signalHeads")
    first_sh = list(signal_heads)[0]
    link_no = (first_sh.get("lane") or "").split()[0]
    links_parent = root.find("links")
    link_el = links_parent.find(f"link[@no='{link_no}']")
    if link_el is None:
        raise RuntimeError(f"Expected <link no='{link_no}'> not found")
    links_parent.remove(link_el)
    tree.write(inpx_path, encoding="UTF-8", xml_declaration=True)


def _dispatch_load_check(working_path: Path) -> dict[str, Any]:
    from services.agent import tools_run
    import services.agent.tools_check  # noqa: F401 - registers load_check
    from services.agent.toolkit import default_registry, dispatch

    tools_run.set_active_run_source(working_path)
    try:
        return dispatch("load_check", {}, registry=default_registry)
    finally:
        tools_run.clear_active_run_source()


def _run_case(example_dir: Path, case_name: str, corrupt_fn) -> dict[str, Any]:
    _log(f"\n{'=' * 78}\n[{case_name}]\n{'=' * 78}")
    from services.agent.state import ArtifactManager

    work_dir = _fresh_work_dir(case_name)
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    artifact_manager.clone()
    _log(f"Working copy: {artifact_manager.working_path}")

    if corrupt_fn is not None:
        corrupt_fn(artifact_manager.working_path)
        _log("Corrupted the working copy in place (this is a throwaway clone, not the source dataset).")

    envelope = _dispatch_load_check(artifact_manager.working_path)
    _log(f"envelope = {envelope}")
    return envelope


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    results: dict[str, bool] = {}

    clean = _run_case(example_dir, "clean_network", None)
    ok = (
        clean["status"] == "ok"
        and clean["data"]["load_check_result"]["load_ok"] is True
        and clean["data"]["load_check_result"]["init_ok"] is True
    )
    _log(f"VERDICT: {'PASS' if ok else 'FAIL'} (expected load_ok=True, init_ok=True)")
    results["clean_network"] = ok

    broken_sc = _run_case(example_dir, "delete_signal_controller", _corrupt_delete_signal_controller)
    ok = (
        broken_sc["status"] == "ok"
        and broken_sc["data"]["load_check_result"]["load_ok"] is False
        and "could not be loaded" in (broken_sc["data"]["load_check_result"]["load_error"] or "").lower()
    )
    _log(f"VERDICT: {'PASS' if ok else 'FAIL'} (expected load_ok=False, 'could not be loaded' in load_error)")
    results["delete_signal_controller"] = ok

    broken_link = _run_case(example_dir, "delete_referenced_link", _corrupt_delete_referenced_link)
    result = broken_link["data"]["load_check_result"]
    ok = (
        broken_link["status"] == "ok"
        and result["load_ok"] is True
        and result["init_ok"] is False
        and result["err_content"] is not None
        and "not complete" in result["err_content"].lower()
    )
    _log(
        f"VERDICT: {'PASS' if ok else 'FAIL'} (expected load_ok=True, init_ok=False, "
        "err_content mentioning an incomplete route)"
    )
    results["delete_referenced_link"] = ok

    _log("\n" + "#" * 78)
    _log("# SUMMARY")
    _log("#" * 78)
    for name, passed in results.items():
        _log(f"  {name}: {'PASS' if passed else 'FAIL'}")

    if not all(results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
