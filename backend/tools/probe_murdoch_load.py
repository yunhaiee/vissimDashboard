"""LIVE COM PROBE: why does the real Murdoch Drive (Perth) client model fail
VissimController.load_network?

Trigger: the standing finding at the tail of docs/phase2/DECISIONS.md's Queue 3
entry -- "the real Murdoch .inpx fails COM load_network (writes preflight
fine -- shadow-based -- but COM applies need a loadable model; task chip
filed)". This blocks every write-verb apply against the real dataset. The
symptom Prism's own wrapper reports is generic ("The network file could not
be loaded.") -- this probe extracts VISSIM's ACTUAL raw exception (never
softened/summarized), tests the three standing hypotheses, and (per
00-EXECUTION-GUIDE.md rule 4) NEVER touches ``runtime/`` -- the client
dataset is read-only input; every experiment below runs against a
``tempfile.mkdtemp()`` COPY.

Prior known facts (do not re-derive -- see the task brief this probe was
written for):
  - ``runtime/uploads/current/current.inpx`` fails ``VissimController.
    load_network`` -- reproduced against both a full clone AND the original
    path. Several Vissim 2026 example networks (Roundabout Buehl.DE,
    Roundabout Billings.US, BRT Priority Texas.US) load fine in the same
    harness; ``Signalization RBC Boise.US`` ALSO fails (separately
    attributed to needing Ring Barrier Controller support Murdoch's
    all-FIXEDTIME controllers don't need).
  - The Murdoch file's header: ``<network version="1403"
    vissimVersion="2026.00-05 [304088]">``.
  - The dataset is a scenario-management export: ``current.inpx`` sits next
    to ``project.vissimpdb``, ``Scenarios/S000001...``, ``Modifications/
    *.trax`` -- none of which ``ArtifactManager.SIBLING_ASSET_EXTENSIONS``
    (services/agent/state.py) copies into a clone's work dir.

Sections:
  [1] Header/version comparison -- current.inpx vs the example networks named
      in the task brief vs the registry-reported INSTALLED Vissim version
      (tests HYPOTHESIS #1: "installed Vissim is older than the file's
      build, so LoadNet refuses it").
  [2] Sibling-asset inventory of runtime/uploads/current/ -- what
      ArtifactManager.clone() would/would not carry into a work dir (tests
      HYPOTHESIS #2: missing scenario-management sibling assets).
  [3] LIVE LoadNet attempts, each a FRESH dedicated Vissim instance
      (attach_to_running=False, matching VissimEditSession's real
      production shape): current.inpx ALONE (mirrors ArtifactManager.
      clone()'s actual current behavior) vs. current.inpx WITH its
      scenario-management siblings copied alongside it vs. both LoadNet
      "additive" flag values -- capturing the RAW win32com exception
      (excepinfo description/source/scode, not just str()) and checking for
      a *.err file each time.
  [4] LoadProject cross-check -- IVissim exposes a SEPARATE ``LoadProject``
      verb ("Reads the project specified in ProjectPath. This can be a
      project database file as well as an inpx file." -- verbatim docstring
      from the generated win32com wrapper) that ``vissim_com.py`` never
      calls. Tests whether a scenario-managed export needs THIS verb
      instead of ``LoadNet``.
  [5] Header-swap test -- copies current.inpx and rewrites ONLY the
      ``vissimVersion`` attribute string to a build that is KNOWN to load
      (no other byte changed), isolating whether Vissim gates purely on
      that version string vs. genuinely re-parsing content that differs
      between builds.
  [6] Boise cross-check, in the SAME harness -- same raw-error capture
      against ``Signalization RBC Boise.US``, the required secondary
      finding (same failure shape as Murdoch, or different).

Usage (from backend/, using the backend venv):
    .venv\\Scripts\\python.exe tools\\probe_murdoch_load.py

RESOLUTION (2026-07-31, after this probe ran): root cause is the file's own
``<netPara underScenMngm="true">`` self-declaration (a PTV Vision "Place
Under Scenario Management" export) -- confirmed the exact differentiator
against all 4 example networks above (every one declares
``underScenMngm="false"``). Section [3]'s ``additive=True`` result DID load
successfully, but a live follow-up check (comparing COM object counts
against this file's own XML ground truth) found it silently imports 11
EXTRA vehicle types from Vissim's own "New" default in-memory project (39
live vs the file's own 28) -- exactly the silent-wrong-numbers pollution
00-EXECUTION-GUIDE.md SS4 warns about -- so it was REJECTED as the fix. The
shipped fix instead flips ONLY ``underScenMngm`` to ``"false"`` on the
WORKING COPY (never ``source_inpx``) in ``ArtifactManager.clone()``/
``rollback_to(0)`` (``backend/services/agent/state.py``'s
``_disable_scenario_management_flag``), after which a normal
``LoadNet(path, additive=False)`` succeeds with object counts matching the
source XML exactly (no pollution) -- this mirrors a fix
``services/simulation_network_builder.py``'s ``_make_network_standalone``
already carried for a different (job-based "interactive" simulation)
cloning path. ``Signalization RBC Boise.US`` declares
``underScenMngm="false"`` (confirmed by this probe's own section [1] table)
-- its failure is UNRELATED, caused by 49 ``RINGBARRIERCONTROLLER`` signal
controllers requiring external ``.prbc`` Ring Barrier Controller support
files/licensing this installation does not have, not by scenario
management.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

# This app is Korean-primary; force UTF-8 so redirected output never breaks
# on the Windows console's default codepage (same discipline as every other
# live probe in this directory).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SOURCE_DIR = REPO_ROOT / "runtime" / "uploads" / "current"
SOURCE_INPX = SOURCE_DIR / "current.inpx"

EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")
KNOWN_EXAMPLES: dict[str, Path] = {
    "Roundabout Buehl.DE (PASS)": EXAMPLES_ROOT / "Roundabout Buehl.DE" / "3 Roundabouts - Conflict areas.inpx",
    "Roundabout Billings.US (PASS)": EXAMPLES_ROOT / "Roundabout Billings.US" / "Roundabout Billings.inpx",
    "BRT Priority Texas.US (PASS)": EXAMPLES_ROOT / "BRT Priority Texas.US" / "BRT Priority Texas.inpx",
    "Signalization RBC Boise.US (FAILS -- needs RBC)": EXAMPLES_ROOT / "Signalization RBC Boise.US" / "Signalization Boise.inpx",
}

# A build known (from the header survey above) to LoadNet successfully --
# used by section 5's header-swap test.
KNOWN_GOOD_VISSIM_VERSION_STRING = "2026.00-07 [305744]"


def _log(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# [1] header / installed-version comparison
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r'<network\s+version="([^"]*)"\s+vissimVersion="([^"]*)"')


def _header_version(inpx_path: Path) -> tuple[str | None, str | None]:
    try:
        with inpx_path.open("r", encoding="utf-8", errors="replace") as fh:
            chunk = fh.read(4000)
    except OSError as exc:
        return None, f"<could not read: {exc}>"
    m = _HEADER_RE.search(chunk)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _installed_vissim_version() -> dict[str, Any] | None:
    """Registry uninstall-key lookup for the installed 'PTV Vissim 2026'
    DisplayVersion -- the same string format as the .inpx header's
    vissimVersion attribute (e.g. "2026.00-09"), just without the bracketed
    internal build number (the registry does not carry that; a live COM
    connect cannot either -- no version-shaped AttValue id exists on the
    Vissim/Net/Simulation objects, matching tools_check.py's own finding
    that Net.AttValue fails OPEN for unknown ids rather than a real surface
    to probe -- this is a static, offline-checkable source of truth)."""
    import winreg

    hives = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in hives:
        try:
            key = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                try:
                    sub_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, sub_name) as sub_key:
                        name = winreg.QueryValueEx(sub_key, "DisplayName")[0]
                        if "vissim" not in name.lower() or "2026" not in name:
                            continue
                        version = winreg.QueryValueEx(sub_key, "DisplayVersion")[0]
                        install_location = None
                        try:
                            install_location = winreg.QueryValueEx(sub_key, "InstallLocation")[0]
                        except OSError:
                            pass
                        return {
                            "display_name": name,
                            "display_version": version,
                            "install_location": install_location,
                        }
                except OSError:
                    continue
        finally:
            key.Close()
    return None


def section_1_header_versions() -> None:
    _log("=" * 78)
    _log("[1] HEADER / INSTALLED-VERSION COMPARISON (HYPOTHESIS #1)")
    _log("=" * 78)

    installed = _installed_vissim_version()
    if installed:
        _log(f"  Installed Vissim (registry): {installed['display_name']!r} "
             f"DisplayVersion={installed['display_version']!r} "
             f"InstallLocation={installed['install_location']!r}")
    else:
        _log("  Installed Vissim: NOT FOUND in registry uninstall keys")

    _log()
    _log(f"  {'label':<45} {'net.ver':>8} {'vissimVersion':>28}")
    net_ver, vissim_ver = _header_version(SOURCE_INPX)
    _log(f"  {'Murdoch current.inpx (FAILS)':<45} {net_ver!s:>8} {vissim_ver!s:>28}")
    for label, path in KNOWN_EXAMPLES.items():
        if not path.exists():
            _log(f"  {label:<45} {'<missing>':>8} {'':>28}  ({path})")
            continue
        net_ver, vissim_ver = _header_version(path)
        _log(f"  {label:<45} {net_ver!s:>8} {vissim_ver!s:>28}")

    _log()
    _log("  NOTE: if pass/fail does not sort monotonically by the bracketed")
    _log("  build number, hypothesis #1 (simple 'older-installed-refuses-")
    _log("  newer-file' gate) cannot be the (whole) explanation -- verify by")
    _log("  eye against the installed DisplayVersion printed above.")


# ---------------------------------------------------------------------------
# [2] sibling-asset inventory
# ---------------------------------------------------------------------------


def section_2_sibling_inventory() -> None:
    _log()
    _log("=" * 78)
    _log("[2] SIBLING-ASSET INVENTORY of runtime/uploads/current/ (HYPOTHESIS #2)")
    _log("=" * 78)

    from services.agent.state import SIBLING_ASSET_EXTENSIONS

    _log(f"  ArtifactManager.SIBLING_ASSET_EXTENSIONS = {sorted(SIBLING_ASSET_EXTENSIONS)}")
    _log(f"  (directories are NEVER copied by ArtifactManager.clone(), regardless of extension)")
    _log()
    try:
        entries = sorted(SOURCE_DIR.iterdir())
    except OSError as exc:
        _log(f"  could not list {SOURCE_DIR}: {exc}")
        return

    for path in entries:
        if path.is_dir():
            try:
                n_files = sum(1 for _ in path.rglob("*") if _.is_file())
            except OSError:
                n_files = -1
            _log(f"  DIR   {path.name}/  ({n_files} file(s) under it)  -> NEVER copied by clone()")
            continue
        ext = path.suffix.lower()
        is_main_inpx = path.name == SOURCE_INPX.name
        copied = is_main_inpx or ext in SIBLING_ASSET_EXTENSIONS
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        _log(f"  FILE  {path.name:<30} ext={ext or '(none)':<10} {size:>10} bytes  "
             f"copied_by_clone={copied}")


# ---------------------------------------------------------------------------
# raw-exception capture helper (shared by sections 3/4/6)
# ---------------------------------------------------------------------------


def _com_error_detail(exc: Exception) -> dict[str, Any]:
    """Unpack a pywintypes.com_error's full (hresult, text, excepinfo,
    argerror) shape -- excepinfo itself is (wcode, source, description,
    helpfile, helpcontext, scode) -- rather than trusting str(exc), which
    already happens to embed the same text but is harder to grep/diff.
    Falls back gracefully for any exception shape that isn't a com_error at
    all (e.g. a plain TypeError from a wrong LoadProject arg count)."""
    detail: dict[str, Any] = {"exc_type": type(exc).__name__, "str": str(exc)}
    args = getattr(exc, "args", None)
    if not args:
        return detail
    detail["args_repr"] = repr(args)
    if len(args) >= 1:
        detail["hresult"] = args[0]
    if len(args) >= 3 and isinstance(args[2], tuple):
        excepinfo = args[2]
        detail["excepinfo_source"] = excepinfo[1] if len(excepinfo) > 1 else None
        detail["excepinfo_description"] = excepinfo[2] if len(excepinfo) > 2 else None
        detail["excepinfo_scode"] = excepinfo[5] if len(excepinfo) > 5 else None
    return detail


def _print_error_detail(detail: dict[str, Any]) -> None:
    for key, value in detail.items():
        _log(f"      {key}: {value!r}")


def _snapshot_err(directory: Path) -> set[Path]:
    try:
        return set(directory.glob("*.err"))
    except OSError:
        return set()


def _new_err_contents(directory: Path, pre_existing: set[Path]) -> dict[str, str]:
    try:
        new_paths = sorted(p for p in directory.glob("*.err") if p not in pre_existing)
    except OSError:
        return {}
    out: dict[str, str] = {}
    for p in new_paths:
        try:
            out[p.name] = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            out[p.name] = f"<could not read: {exc}>"
    return out


def _fresh_connect() -> Any:
    """A brand-new, dedicated Vissim instance -- attach_to_running=False
    per 00-EXECUTION-GUIDE.md rule 3, exactly matching VissimEditSession's
    real production connect+load shape (never reused across attempts, so
    each attempt starts from a completely clean COM/Vissim process state)."""
    from services.vissim_com import VissimController

    controller = VissimController()
    controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
    return controller


def _attempt_loadnet(label: str, inpx_path: Path, additive: bool) -> dict[str, Any]:
    _log(f"  --- {label}  (LoadNet additive={additive}) ---")
    import pythoncom

    pythoncom.CoInitialize()
    result: dict[str, Any] = {"label": label, "additive": additive, "path": str(inpx_path)}
    controller = None
    try:
        pre_err = _snapshot_err(inpx_path.parent)
        started = time.monotonic()
        try:
            controller = _fresh_connect()
        except Exception as exc:
            result["connect_raised"] = _com_error_detail(exc)
            _log(f"      CONNECT ITSELF RAISED: {exc}")
            return result
        result["prog_id"] = controller.prog_id
        try:
            controller.vissim.LoadNet(str(inpx_path), additive)
            result["load_net_raised"] = False
            result["elapsed_s"] = round(time.monotonic() - started, 2)
            _log(f"      LoadNet SUCCEEDED in {result['elapsed_s']}s")
        except Exception as exc:
            result["load_net_raised"] = True
            result["elapsed_s"] = round(time.monotonic() - started, 2)
            result["error"] = _com_error_detail(exc)
            _log(f"      LoadNet RAISED after {result['elapsed_s']}s:")
            _print_error_detail(result["error"])
        result["new_err_files"] = _new_err_contents(inpx_path.parent, pre_err)
        if result["new_err_files"]:
            for name, content in result["new_err_files"].items():
                _log(f"      NEW .err file {name!r}:")
                for line in content.splitlines()[:20]:
                    _log(f"        {line}")
        else:
            _log("      no new .err file appeared")
    finally:
        if controller is not None:
            try:
                controller.disconnect()
            except Exception:
                _log("      (disconnect raised -- ignored, instance may already be gone)")
        pythoncom.CoUninitialize()
    return result


def _attempt_loadproject(label: str, project_path: Path) -> dict[str, Any]:
    _log(f"  --- {label}  (LoadProject) ---")
    import pythoncom

    pythoncom.CoInitialize()
    result: dict[str, Any] = {"label": label, "path": str(project_path)}
    controller = None
    try:
        pre_err = _snapshot_err(project_path.parent)
        started = time.monotonic()
        try:
            controller = _fresh_connect()
        except Exception as exc:
            result["connect_raised"] = _com_error_detail(exc)
            _log(f"      CONNECT ITSELF RAISED: {exc}")
            return result
        result["prog_id"] = controller.prog_id
        try:
            controller.vissim.LoadProject(str(project_path))
            result["load_project_raised"] = False
            result["elapsed_s"] = round(time.monotonic() - started, 2)
            _log(f"      LoadProject SUCCEEDED in {result['elapsed_s']}s")
            # If it succeeded, confirm the network actually came in usable.
            try:
                sc_nos = controller.available_signal_controller_nos()
                vi_nos = controller.available_vehicle_input_nos()
                result["signal_controllers_after_load"] = sc_nos
                result["vehicle_inputs_after_load"] = vi_nos
                _log(f"      Net.SignalControllers -> {sc_nos}")
                _log(f"      Net.VehicleInputs      -> {vi_nos}")
            except Exception as exc:
                _log(f"      (post-load Net inspection raised: {exc})")
        except Exception as exc:
            result["load_project_raised"] = True
            result["elapsed_s"] = round(time.monotonic() - started, 2)
            result["error"] = _com_error_detail(exc)
            _log(f"      LoadProject RAISED after {result['elapsed_s']}s:")
            _print_error_detail(result["error"])
        result["new_err_files"] = _new_err_contents(project_path.parent, pre_err)
        if result["new_err_files"]:
            for name, content in result["new_err_files"].items():
                _log(f"      NEW .err file {name!r}:")
                for line in content.splitlines()[:20]:
                    _log(f"        {line}")
        else:
            _log("      no new .err file appeared")
    finally:
        if controller is not None:
            try:
                controller.disconnect()
            except Exception:
                _log("      (disconnect raised -- ignored)")
        pythoncom.CoUninitialize()
    return result


# ---------------------------------------------------------------------------
# work-dir preparation -- COPIES ONLY, never touches runtime/
# ---------------------------------------------------------------------------


def _prepare_work_dirs() -> dict[str, Path]:
    root = Path(tempfile.mkdtemp(prefix="murdoch_load_probe_"))
    _log(f"  work root (NOT under repo, NOT under runtime/): {root}")

    inpx_only_dir = root / "inpx_only"
    inpx_only_dir.mkdir()
    shutil.copyfile(SOURCE_INPX, inpx_only_dir / SOURCE_INPX.name)

    with_scenario_dir = root / "with_scenario_mgmt"
    shutil.copytree(SOURCE_DIR, with_scenario_dir)

    return {
        "root": root,
        "inpx_only": inpx_only_dir / SOURCE_INPX.name,
        "with_scenario_inpx": with_scenario_dir / SOURCE_INPX.name,
        "with_scenario_pdb": with_scenario_dir / "project.vissimpdb",
    }


def section_3_loadnet_attempts(paths: dict[str, Path]) -> list[dict[str, Any]]:
    _log()
    _log("=" * 78)
    _log("[3] LIVE LoadNet ATTEMPTS -- raw exception capture")
    _log("=" * 78)
    outcomes: list[dict[str, Any]] = []
    outcomes.append(_attempt_loadnet(
        "current.inpx ALONE (mirrors ArtifactManager.clone()'s actual shape)",
        paths["inpx_only"], additive=False,
    ))
    outcomes.append(_attempt_loadnet(
        "current.inpx ALONE, additive=True",
        paths["inpx_only"], additive=True,
    ))
    outcomes.append(_attempt_loadnet(
        "current.inpx WITH scenario-mgmt siblings alongside (project.vissimpdb, Scenarios/, Modifications/)",
        paths["with_scenario_inpx"], additive=False,
    ))
    return outcomes


def section_4_loadproject_crosscheck(paths: dict[str, Path]) -> list[dict[str, Any]]:
    _log()
    _log("=" * 78)
    _log("[4] LoadProject CROSS-CHECK (scenario-management's own load verb)")
    _log("=" * 78)
    _log("  IVissim.LoadProject docstring (generated win32com wrapper, verbatim):")
    _log("    'Reads the project specified in ProjectPath. This can be a project")
    _log("     database file as well as an inpx file.'")
    outcomes: list[dict[str, Any]] = []
    outcomes.append(_attempt_loadproject(
        "LoadProject(project.vissimpdb)", paths["with_scenario_pdb"],
    ))
    outcomes.append(_attempt_loadproject(
        "LoadProject(current.inpx) [same file, different verb than section 3]",
        paths["with_scenario_inpx"],
    ))
    return outcomes


def section_5_header_swap_test(paths: dict[str, Path]) -> dict[str, Any] | None:
    _log()
    _log("=" * 78)
    _log("[5] HEADER-SWAP TEST -- does spoofing vissimVersion alone change the outcome?")
    _log("=" * 78)
    original = paths["inpx_only"]
    net_ver, vissim_ver = _header_version(original)
    if vissim_ver is None:
        _log("  could not read the original header -- skipping")
        return None
    if vissim_ver == KNOWN_GOOD_VISSIM_VERSION_STRING:
        _log(f"  original vissimVersion already equals the known-good string "
             f"({vissim_ver!r}) -- test not meaningful, skipping")
        return None

    swapped_path = original.parent / "header_swapped.inpx"
    text = original.read_text(encoding="utf-8", errors="replace")
    swapped_text, n = _HEADER_RE.subn(
        lambda m: f'<network version="{m.group(1)}" vissimVersion="{KNOWN_GOOD_VISSIM_VERSION_STRING}"',
        text, count=1,
    )
    if n != 1:
        _log("  could not locate the header attribute to rewrite -- skipping")
        return None
    swapped_path.write_text(swapped_text, encoding="utf-8")
    _log(f"  wrote {swapped_path.name}: vissimVersion {vissim_ver!r} -> "
         f"{KNOWN_GOOD_VISSIM_VERSION_STRING!r} (every other byte identical)")

    return _attempt_loadnet(
        f"header-swapped current.inpx (vissimVersion spoofed to {KNOWN_GOOD_VISSIM_VERSION_STRING!r})",
        swapped_path, additive=False,
    )


def section_6_boise_crosscheck() -> dict[str, Any] | None:
    _log()
    _log("=" * 78)
    _log("[6] BOISE CROSS-CHECK -- same raw-error capture, same harness")
    _log("=" * 78)
    boise_dir = EXAMPLES_ROOT / "Signalization RBC Boise.US"
    if not boise_dir.is_dir():
        _log(f"  {boise_dir} not found -- skipping")
        return None
    work_root = Path(tempfile.mkdtemp(prefix="boise_load_probe_"))
    work_copy = work_root / "Signalization RBC Boise.US"
    shutil.copytree(boise_dir, work_copy)
    inpx_files = sorted(work_copy.glob("*.inpx"))
    if not inpx_files:
        _log(f"  no .inpx found under {work_copy} -- skipping")
        return None
    return _attempt_loadnet("Signalization RBC Boise.US (comparison)", inpx_files[0], additive=False)


def main() -> int:
    _log(f"SOURCE (read-only, never written): {SOURCE_INPX}")
    if not SOURCE_INPX.exists():
        _log("  current.inpx does not exist -- nothing to probe.")
        return 1
    _log(f"  exists=True size={SOURCE_INPX.stat().st_size} bytes")

    section_1_header_versions()
    section_2_sibling_inventory()

    _log()
    _log("=" * 78)
    _log("PREPARING WORK COPIES (tempfile.mkdtemp -- never under runtime/)")
    _log("=" * 78)
    paths = _prepare_work_dirs()

    loadnet_outcomes = section_3_loadnet_attempts(paths)
    loadproject_outcomes = section_4_loadproject_crosscheck(paths)
    header_swap_outcome = section_5_header_swap_test(paths)
    boise_outcome = section_6_boise_crosscheck()

    _log()
    _log("=" * 78)
    _log("SUMMARY")
    _log("=" * 78)
    for outcome in loadnet_outcomes:
        ok = not outcome.get("load_net_raised", True)
        _log(f"  LoadNet   {outcome['label']:<90} -> {'OK' if ok else 'RAISED'}")
    for outcome in loadproject_outcomes:
        ok = not outcome.get("load_project_raised", True)
        _log(f"  LoadProj  {outcome['label']:<90} -> {'OK' if ok else 'RAISED'}")
    if header_swap_outcome is not None:
        ok = not header_swap_outcome.get("load_net_raised", True)
        _log(f"  HdrSwap   {header_swap_outcome['label']:<90} -> {'OK' if ok else 'RAISED'}")
    if boise_outcome is not None:
        ok = not boise_outcome.get("load_net_raised", True)
        _log(f"  Boise     {boise_outcome['label']:<90} -> {'OK' if ok else 'RAISED'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
