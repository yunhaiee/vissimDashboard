"""Phase 1 empirical probe for v2 unit (3): does Vissim expose network
consistency-checking over COM at all -- and if not, does LoadNet of a
deliberately-broken clone give us something diagnosable instead?

This is EMPIRICAL-FIRST per the task brief: run this BEFORE designing
check_network. The doctrine (docs/phase2/00-EXECUTION-GUIDE.md SS4, "an
action with no possible verification read-back does not ship") applies
just as much to READ surfaces as to writes -- if there is no real COM
method to call, no design should pretend one exists.

Two independent questions, two phases:

  [Phase 1a] STATIC type-library scan (no COM instance, no licence
  consumed): generate the makepy wrapper for the installed "Vissim Object
  Library" type library and grep EVERY interface's method name for
  check/consistency/validation vocabulary. This is authoritative for "does
  a network-check verb exist anywhere in the COM surface" -- it reads the
  registered .tlb directly, listing literally every method Vissim's COM
  API exposes (600+ interfaces), not just the handful this codebase
  currently calls. Also dumps IVissim/INet/ISimulation's full method lists
  verbatim for manual inspection.

  [Phase 1b] LIVE dir()/getattr probing on a real connected instance
  (attach_to_running=False per 00-EXECUTION-GUIDE.md rule 3) -- confirms
  Phase 1a's static finding holds for the live object too (gencache
  early-binds Vissim.Net/Vissim.Simulation once Phase 1a has generated the
  wrapper in-process, so dir() here shows real method names, not opaque
  dynamic-dispatch noise), and defensively tries a battery of plausible
  method names / AttValue ids anyway, each wrapped in its own try/except
  with the exact outcome printed -- including a CONTROL probe (a
  known-bogus AttValue id) so a survives-without-raising candidate can be
  judged against Vissim's actual "unknown id" failure shape on this
  object, not assumed.

  [Phase 2] Deliberately-broken clone: two corruption modes, each a FRESH
  connect+LoadNet (never attach_to_running, never reuses a session across
  networks -- matches VissimEditSession._ensure_connected()'s real
  production shape: connect() then immediately load_network(), nothing
  reused):
    (a) delete the network's only <signalController> -- referenced by
        EVERY <signalHead> via sg="<sc_no> <group>" (52/52 on the Beijing
        example).
    (b) delete a <link> referenced by a <signalHead>'s lane="<link_no>
        <lane_idx>" attribute.
  For each: does LoadNet raise? If not, is the missing object simply gone
  from the live Net collection? Does an .err file appear immediately after
  LoadNet (before any run)? Then a short RunContinuous, to see whether the
  corruption surfaces there instead (or not at all) -- with any NEW .err
  file's content printed verbatim.

Always disconnects every dedicated Vissim instance in a ``finally`` block.
Clones only -- the original PTV Examples Demo folder is never modified
(00-EXECUTION-GUIDE.md rule 4): every corrupted network is written into a
runtime/check-network-probe/ work-dir copy.

Usage: python tools/check_network_probe.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- same
default as every other live probe/check tool in this directory.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# This app is Korean-primary; the Windows console's default codepage (often
# cp1252) cannot encode Hangul that might show up in .err content or paths.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "check-network-probe"

# Vocabulary a genuine "check this network" COM verb would plausibly use.
_CHECK_KEYWORDS = [
    "check", "consist", "valid", "verify", "diagnos", "integrity", "sanity",
    "repair",
]

_CANDIDATE_METHODS = [
    "CheckNetwork", "CheckNet", "NetworkCheck", "CheckConsistency",
    "VerifyNetwork", "ValidateNetwork", "Validate", "Verify", "CheckModel",
    "ModelCheck", "ConsistencyCheck", "RunConsistencyCheck", "CheckErrors",
    "GetErrors", "GetMessages", "GetWarnings", "ErrorList", "Diagnose",
    "NetworkDiagnostics",
]

_CANDIDATE_ATTVALUES = [
    "IsConsistent", "IsValid", "NetworkValid", "ConsistencyState",
    "CheckResult", "ErrorCount", "WarningCount", "NumErrors", "NumWarnings",
    "NetCheckOK", "NetworkCheckState", "Messages", "ErrorCode",
]

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(name: str) -> Path:
    """Clean stale siblings from a prior run of this probe (a still-open
    Vissim window keeps its working copy locked -- best-effort only), then
    return a process-unique path. Does NOT create the directory --
    callers either shutil.copytree into it or mkdir it themselves."""
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    return WORK_ROOT / f"{name}_{os.getpid()}"


# ---------------------------------------------------------------------------
# Phase 1a: static type-library scan (no COM instance required)
# ---------------------------------------------------------------------------


def probe_typelib() -> dict[str, Any]:
    import win32com.client.makepy as makepy
    import win32com.client.selecttlb as selecttlb
    from win32com.client import gencache

    _log("[1a] Locating the installed 'Vissim Object Library' type library via selecttlb...")
    candidates = [
        tlb
        for tlb in selecttlb.EnumTlbs()
        if "vissim object library" in (getattr(tlb, "desc", "") or "").lower()
    ]
    if not candidates:
        raise RuntimeError(
            "No registered 'Vissim Object Library' type library found via "
            "win32com.client.selecttlb.EnumTlbs() -- cannot run the static scan."
        )
    # selecttlb.TypelibSpec.major/minor are HEX STRINGS without a "0x" prefix
    # (verified empirically -- e.g. Vissim 2026's typelib reports
    # major='1a', minor='0', i.e. 26.0 in decimal; NOT the same vocabulary
    # as the "26.0" in its own .desc string). GenerateFromTypeLibSpec/
    # EnsureModule both require real ints. Same "verify empirically, don't
    # trust a plausible-looking string" lesson as the CycTm landmine
    # (docs/phase2/DECISIONS.md, M2.1).
    def _hex_int(value: str) -> int:
        return int(value, 16)

    # Prefer the highest major version (2026 == major 0x1a == 26) if more
    # than one Vissim version's typelib is registered on this machine.
    match = max(candidates, key=lambda t: (_hex_int(t.major), _hex_int(t.minor)))
    for tlb in candidates:
        _log(f"      registered: {tlb.desc}  clsid={tlb.clsid} major={tlb.major} minor={tlb.minor}")
    _log(f"      using: {match.desc}  clsid={match.clsid}")

    major_i, minor_i = _hex_int(match.major), _hex_int(match.minor)
    makepy.GenerateFromTypeLibSpec((match.clsid, match.lcid, major_i, minor_i))
    mod = gencache.EnsureModule(match.clsid, match.lcid, major_i, minor_i)
    src_path = Path(mod.__file__)
    _log(f"      Generated wrapper: {src_path} ({src_path.stat().st_size} bytes)")
    lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()

    class_name = None
    hits: list[str] = []
    interface_count = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            interface_count += 1
            continue
        if stripped.startswith("def ") and "(self" in stripped:
            method_name = stripped[4:].split("(")[0]
            lower = method_name.lower()
            if any(kw in lower for kw in _CHECK_KEYWORDS):
                hits.append(f"{class_name}.{method_name}")

    _log(
        f"      Scanned {len(lines)} source lines across {interface_count} COM "
        f"interfaces for method names containing any of {_CHECK_KEYWORDS}"
    )
    if hits:
        _log(f"      HITS ({len(hits)}): {hits}")
    else:
        _log(
            "      RESULT: zero interface methods anywhere in the ENTIRE type "
            "library match any check/consistency/validation keyword."
        )

    # Full, verbatim method lists for the three places a network-check verb
    # would most plausibly live: the top-level automation object, the
    # loaded-network object, and the simulation-control object.
    for iface in ("IVissim", "INet", "ISimulation"):
        _log(f"\n      -- {iface} full method list (verbatim from the type library) --")
        in_iface = False
        for line in lines:
            if line.strip() == f"class {iface}(DispatchBaseClass):":
                in_iface = True
                continue
            if in_iface and line.strip().startswith("class ") and "(DispatchBaseClass)" in line:
                break
            if in_iface and line.strip().startswith("def ") and "(self" in line:
                _log(f"        {line.strip()}")

    return {"hits": hits, "source_path": str(src_path), "interface_count": interface_count}


# ---------------------------------------------------------------------------
# Phase 1b: live COM surface probe (one Vissim instance, valid network)
# ---------------------------------------------------------------------------


def probe_live_surface(vissim: Any) -> dict[str, list[str]]:
    _log("\n[1b] Live dir()/getattr probing + targeted call attempts (valid network loaded)...")
    results: dict[str, list[str]] = {}
    for label, obj in (("Vissim", vissim), ("Net", vissim.Net), ("Simulation", vissim.Simulation)):
        try:
            names = sorted(n for n in dir(obj) if not n.startswith("_"))
        except Exception as exc:
            names = []
            _log(f"      dir({label}) raised: {type(exc).__name__}: {exc}")
        _log(f"\n      dir({label}) -> {len(names)} public name(s)")
        matches = [n for n in names if any(kw in n.lower() for kw in _CHECK_KEYWORDS)]
        _log(
            f"        keyword matches: {matches}" if matches
            else "        keyword matches: NONE"
        )
        results[label] = names

        _log(f"      -- targeted method-call attempts on {label} --")
        for method_name in _CANDIDATE_METHODS:
            fn = getattr(obj, method_name, None)
            if fn is None:
                continue
            try:
                value = fn()
                _log(f"        [FOUND]              {label}.{method_name}() -> {value!r}")
            except Exception as exc:
                _log(
                    f"        [EXISTS-BUT-RAISED]  {label}.{method_name}() -> "
                    f"{type(exc).__name__}: {exc}"
                )
        if not any(getattr(obj, m, None) is not None for m in _CANDIDATE_METHODS):
            _log(f"        (none of {len(_CANDIDATE_METHODS)} candidate method names exist on {label})")

        _log(f"      -- targeted AttValue(...) attempts on {label} --")
        for attr_id in _CANDIDATE_ATTVALUES:
            try:
                value = obj.AttValue(attr_id)
                _log(
                    f"        [RESPONDED] {label}.AttValue({attr_id!r}) -> {value!r}  "
                    "(cross-check against the CONTROL probe below before trusting this)"
                )
            except Exception as exc:
                _log(f"        [no]        {label}.AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
        try:
            bogus = obj.AttValue(_CONTROL_BOGUS_ATTR)
            _log(
                f"        [CONTROL]   {label}.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} "
                "(unexpected -- did NOT raise for a made-up id)"
            )
        except Exception as exc:
            _log(
                f"        [CONTROL]   {label}.AttValue({_CONTROL_BOGUS_ATTR!r}) -> "
                f"{type(exc).__name__}: {exc}  (this is Vissim's real 'unknown id' shape on {label})"
            )
    return results


# ---------------------------------------------------------------------------
# Phase 2: deliberately-broken clone -- what does LoadNet actually do?
# ---------------------------------------------------------------------------


def _make_corrupted_clone(mode_dir: Path) -> tuple[Path, str]:
    """Delete a referenced element from the sole .inpx in ``mode_dir`` and
    write a new, separately-named corrupted .inpx alongside it (the
    original is then removed so nothing can accidentally load the good
    file instead). Returns (corrupted_path, human-readable description of
    what referenced the deleted element)."""
    source_inpx = sorted(mode_dir.glob("*.inpx"))[0]
    tree = ET.parse(source_inpx)
    root = tree.getroot()

    if mode_dir.name.startswith("delete_signal_controller"):
        sc_parent = root.find("signalControllers")
        sc = sc_parent.find("signalController[@no='1']")
        if sc is None:
            raise RuntimeError("Expected <signalController no='1'> not found in this example network")
        sc_parent.remove(sc)
        signal_heads = root.find("signalHeads")
        referencing = [
            sh.get("no") for sh in signal_heads if (sh.get("sg") or "").split()[:1] == ["1"]
        ]
        referenced_by = (
            f"{len(referencing)}/{len(list(signal_heads))} <signalHead> element(s) via "
            f"sg='1 *' (signalHead no= {referencing[:10]}{'...' if len(referencing) > 10 else ''})"
        )
    elif mode_dir.name.startswith("delete_referenced_link"):
        signal_heads = root.find("signalHeads")
        first_sh = list(signal_heads)[0]
        link_no = (first_sh.get("lane") or "").split()[0]
        links_parent = root.find("links")
        link_el = links_parent.find(f"link[@no='{link_no}']")
        if link_el is None:
            raise RuntimeError(f"Expected <link no='{link_no}'> not found in this example network")
        links_parent.remove(link_el)
        referenced_by = (
            f"<signalHead no='{first_sh.get('no')}'> via lane='{first_sh.get('lane')}' "
            f"(link {link_no} deleted)"
        )
    else:
        raise ValueError(f"Unknown corruption mode dir name: {mode_dir.name}")

    corrupted_path = mode_dir / "broken.inpx"
    tree.write(corrupted_path, encoding="UTF-8", xml_declaration=True)
    source_inpx.unlink()  # remove the good file so nothing can load it by accident
    return corrupted_path, referenced_by


def probe_broken_network_loadnet(example_dir: Path, probe_root: Path, mode: str) -> dict[str, Any]:
    _log(f"\n{'=' * 78}\n[Phase 2] corruption mode: {mode}\n{'=' * 78}")
    mode_dir = probe_root / mode
    if mode_dir.exists():
        shutil.rmtree(mode_dir, ignore_errors=True)
    shutil.copytree(example_dir, mode_dir)

    broken_path, referenced_by = _make_corrupted_clone(mode_dir)
    _log(f"   Corrupted clone: {broken_path.name}")
    _log(f"   Deleted element was referenced by: {referenced_by}")

    pre_existing_err = set(mode_dir.rglob("*.err"))

    outcome: dict[str, Any] = {"mode": mode, "referenced_by": referenced_by}

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        _log("   Launching a FRESH, dedicated Vissim instance (attach_to_running=False)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"   Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        started = time.monotonic()
        try:
            controller.load_network(str(broken_path))
        except Exception as exc:
            elapsed = time.monotonic() - started
            outcome["load_net_raised"] = True
            outcome["load_net_error"] = f"{type(exc).__name__}: {exc}"
            _log(f"   LoadNet RAISED after {elapsed:.1f}s: {type(exc).__name__}: {exc}")
            return outcome

        elapsed = time.monotonic() - started
        outcome["load_net_raised"] = False
        outcome["load_net_elapsed_s"] = round(elapsed, 1)
        _log(f"   LoadNet did NOT raise ({elapsed:.1f}s) -- Vissim silently accepted the broken file.")

        sc_nos = controller.available_signal_controller_nos()
        vi_nos = controller.available_vehicle_input_nos()
        outcome["signal_controllers_after_load"] = sc_nos
        outcome["vehicle_inputs_after_load"] = vi_nos
        _log(f"   Post-load available_signal_controller_nos() = {sc_nos}")
        _log(f"   Post-load available_vehicle_input_nos()     = {vi_nos}")

        new_err_immediately = sorted(p for p in mode_dir.rglob("*.err") if p not in pre_existing_err)
        outcome["err_files_immediately_after_loadnet"] = [str(p.relative_to(mode_dir)) for p in new_err_immediately]
        _log(
            f"   New .err file(s) immediately after LoadNet (before any run): "
            f"{outcome['err_files_immediately_after_loadnet'] or 'NONE'}"
        )
        for p in new_err_immediately:
            content = p.read_text(encoding="utf-8", errors="replace")
            _log(f"   --- {p.name} (immediately post-LoadNet) ---\n{content}\n   --- end ---")

        _log("   Attempting a short RunContinuous (60s) to see whether the corruption surfaces at RUN time...")
        try:
            controller.set_sim_duration(60)
            controller.set_random_seed(1)
            run_started = time.monotonic()
            controller.vissim.Simulation.RunContinuous()
            outcome["run_continuous_raised"] = False
            _log(f"   RunContinuous completed without raising ({time.monotonic() - run_started:.1f}s).")
        except Exception as exc:
            outcome["run_continuous_raised"] = True
            outcome["run_continuous_error"] = f"{type(exc).__name__}: {exc}"
            _log(f"   RunContinuous RAISED: {type(exc).__name__}: {exc}")

        new_err_after_run = sorted(
            p for p in mode_dir.rglob("*.err") if p not in pre_existing_err and p not in new_err_immediately
        )
        outcome["err_files_after_run"] = [str(p.relative_to(mode_dir)) for p in new_err_after_run]
        _log(f"   New .err file(s) after RunContinuous: {outcome['err_files_after_run'] or 'NONE'}")
        outcome["err_contents_after_run"] = {}
        for p in new_err_after_run:
            content = p.read_text(encoding="utf-8", errors="replace")
            outcome["err_contents_after_run"][p.name] = content
            _log(f"   --- {p.name} (after RunContinuous) ---\n{content}\n   --- end ---")

        db_paths = sorted(mode_dir.rglob("*.db"))
        outcome["result_db_written"] = bool(db_paths)
        _log(f"   Result .db written: {outcome['result_db_written']} ({[p.name for p in db_paths]})")

    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("   Disconnected the dedicated Vissim instance.")

    return outcome


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log("#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    typelib_result = probe_typelib()

    _log("\n" + "#" * 78)
    _log("# PHASE 1b -- live COM surface probe (one dedicated Vissim instance)")
    _log("#" * 78)
    valid_work_dir = _fresh_work_dir(f"{example_dir.name}_valid")
    shutil.copytree(example_dir, valid_work_dir)
    inpx_files = sorted(valid_work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {valid_work_dir}")
    working = inpx_files[0]

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    live_result: dict[str, list[str]] = {}
    try:
        _log(f"   Launching a dedicated Vissim instance (attach_to_running=False), loading {working.name}...")
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        live_result = probe_live_surface(controller.vissim)
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("\n   Disconnected the dedicated Vissim instance (Phase 1b).")

    probe_root = _fresh_work_dir("broken-clones")
    probe_root.mkdir(parents=True, exist_ok=True)
    broken_findings: dict[str, Any] = {}
    for mode in ("delete_signal_controller", "delete_referenced_link"):
        broken_findings[mode] = probe_broken_network_loadnet(example_dir, probe_root, mode)

    _log("\n" + "#" * 78)
    _log("# SUMMARY")
    _log("#" * 78)
    _log(f"[1a] Type-library method-name scan hits: {typelib_result['hits'] or 'NONE'}")
    live_hits = {
        label: [n for n in names if any(kw in n.lower() for kw in _CHECK_KEYWORDS)]
        for label, names in live_result.items()
    }
    _log(f"[1b] Live dir() keyword matches: {live_hits}")
    for mode, outcome in broken_findings.items():
        if outcome.get("load_net_raised"):
            verdict = f"LoadNet REFUSED ({outcome.get('load_net_error')})"
        else:
            verdict = (
                f"LoadNet SILENTLY ACCEPTED; .err@load={outcome.get('err_files_immediately_after_loadnet')}; "
                f"run_raised={outcome.get('run_continuous_raised')}; "
                f".err@run={outcome.get('err_files_after_run')}"
            )
        _log(f"[2]  {mode}: {verdict}")


if __name__ == "__main__":
    main()
