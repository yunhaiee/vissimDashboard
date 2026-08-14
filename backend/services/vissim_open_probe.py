"""THE OPEN PROBE — what a real PTV Vissim does with ONE network file.

``LoadNet`` → read whatever ``.err`` Vissim wrote beside the file → read the
LOADED network's own census back over COM → ``SaveNetAs`` a copy → disconnect.
No simulation, no edits, no reconfiguration of anything. That single operation
is the whole of V1's evidence: ``services/genesis/gate.py``'s :class:`LoadOracle`
reads the record this module produces and decides, on its own sha256 identity
rule, whether the record is evidence about the file being gated.

**Extracted, not written.** Every line below was ``backend/tools/
genesis_open_live_check.py``'s own child-process body (``_com_phase`` and the
four readers it calls). It moved here so the CLI and the queued live-check job
(``services/genesis_jobs.py``'s live-check runner) run the SAME probe rather
than two implementations that agree until one of them is edited. The CLI still
owns everything around it — the bounded child process, the orphan-``vissim.exe``
sweep, the ``state.json`` merge, the offline XML round-trip analysis — and calls
:func:`probe_open` for the COM leg; :func:`probe_open`'s result dict is
byte-for-byte the dict the CLI used to build inline, so its ``state.json`` shape
did not change (``tests/test_vissim_open_probe.py`` pins that key set).

**COM discipline** (CLAUDE.md, ``docs/phase2/00-EXECUTION-GUIDE.md`` §1):

* ``connect(attach_to_running=False)`` ALWAYS — :func:`_connect_controller` is
  the only place a connection is made, and ``GetActiveObject`` (which would
  attach to a human's open GUI and load a network into it, destroying their
  unsaved work) is never reachable from here.
* This module NEVER opens a caller's dataset in place. It opens the path it is
  handed, and every caller hands it a file inside its own job/work directory —
  the CLI copies into ``runtime/genesis-open-live-check/nets/<label>/``, the
  live-check job clones into the new job's own work dir.
* No simulation and no edits: there is no ``RunContinuous``, no
  ``RunSingleStep`` and no ``SetAttValue`` anywhere below.
* Nothing here may run in a request handler. This module does not enforce that
  (it cannot see its caller); the job queue does, and the CLI runs in its own
  child process.

**Importing this module does not touch COM.** ``pythoncom`` and
``services.vissim_com`` are imported inside :func:`_connect_controller`, so
``import services.vissim_open_probe`` stays cheap and cross-platform — which is
what lets ``services/genesis_jobs.py`` reach the probe through a seam without
dragging pywin32 into every test process.

**The one deliberate difference from the CLI's old inline body:** ``pythoncom``'s
import and ``CoInitialize`` used to sit OUTSIDE the try, so a machine without
pywin32 produced an uncaught traceback and no record at all. They now sit inside
:func:`_connect_controller`, which is inside the try, so that case is recorded as
``fatal_error`` like every other infrastructure failure. No run that ever
produced evidence is affected — those all have COM available.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "probe_open",
    "read_com_topology",
    "read_err_files",
    "read_version",
    "snapshot_err_paths",
]

#: Vissim-object-level ids that might carry the version/product string. Read
#: defensively: a missing id is recorded as missing, never inferred.
VERSION_CANDIDATES = (
    "Version",
    "VersionNumber",
    "ProductName",
    "LicenseInfo",
    "LicenceInfo",
    "ServicePack",
    "Build",
)

MAX_ERR_CHARS = 20000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Readers — offline-safe helpers the probe records its evidence with
# ---------------------------------------------------------------------------


def snapshot_err_paths(directory: Path) -> set[str]:
    try:
        return {p.name for p in directory.glob("*.err")}
    except OSError:
        return set()


def read_err_files(directory: Path, pre_existing: set[str]) -> dict[str, Any]:
    """Every ``.err`` in ``directory``, flagged new-since-snapshot, verbatim."""
    out: dict[str, Any] = {"files": [], "new_files": []}
    try:
        paths = sorted(directory.glob("*.err"))
    except OSError as exc:
        out["error"] = str(exc)
        return out
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            text = f"[could not read: {exc}]"
        if len(text) > MAX_ERR_CHARS:
            text = text[:MAX_ERR_CHARS] + "\n... [truncated]"
        entry = {"name": path.name, "bytes": path.stat().st_size, "text": text}
        out["files"].append(entry)
        if path.name not in pre_existing:
            out["new_files"].append(path.name)
    return out


def read_version(vissim: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"att_values": {}, "python_attrs": {}}
    for att in VERSION_CANDIDATES:
        try:
            info["att_values"][att] = str(vissim.AttValue(att))
        except Exception as exc:  # noqa: BLE001 - a missing id is data, not a failure
            info["att_values"][att] = f"<unavailable: {type(exc).__name__}>"
    for name in ("Version", "VersionNumber", "ProductName"):
        try:
            info["python_attrs"][name] = str(getattr(vissim, name))
        except Exception as exc:  # noqa: BLE001
            info["python_attrs"][name] = f"<unavailable: {type(exc).__name__}>"
    return info


def _link_census(vissim: Any) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    """``[(link no, IsConn), …]`` plus the connector/plain split, in as few COM calls as
    possible.

    MEASURED 2026-08-06: the obvious form -- ``GetAll()`` then one ``AttValue``
    per object, three times over -- is ~15,600 marshalled round trips on the
    2,069-link Murdoch draft and blew a 900 s wall clock without reaching
    ``SaveNetAs``. ``GetMultipleAttributes`` returns the same numbers in ONE
    call per attribute set. The per-object loop is kept as the fallback so a
    Vissim build without it still answers, just slowly.
    """
    links = vissim.Net.Links
    try:
        pairs = [
            (int(row[0]), int(row[1] or 0))
            for row in links.GetMultipleAttributes(("No", "IsConn"))
        ]
        read_via = "GetMultipleAttributes"
    except Exception as bulk_exc:  # noqa: BLE001 - see the fallback note above
        pairs = [
            (int(o.AttValue("No")), int(o.AttValue("IsConn") or 0))
            for o in links.GetAll()
        ]
        read_via = (
            "per-object AttValue "
            f"(GetMultipleAttributes unavailable: {type(bulk_exc).__name__})"
        )
    pairs.sort()
    counts = {
        "connectors": sum(1 for _no, is_conn in pairs if is_conn == 1),
        "plain_links": sum(1 for _no, is_conn in pairs if is_conn == 0),
        "read_via": read_via,
    }
    return pairs, counts


def read_com_topology(controller: Any, max_connectors: int = 12) -> dict[str, Any]:
    """What COM says about the LOADED network -- counts, and for the first few
    connectors the resolved ``from_lanes``/``to_lanes`` (``get_link()``'s
    landmine-4 read of each connector lane's nested FromLanes/ToLanes).

    The counts are what V1 actually grades: a network file Vissim "opens" into
    an EMPTY model is the 2026-08-06 trap, and only a census read back OUT of
    the loaded model can catch it.
    """
    out: dict[str, Any] = {}
    vissim = controller.vissim
    try:
        pairs, census = _link_census(vissim)
    except Exception as exc:  # noqa: BLE001 - a failed read is recorded, never raised
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    out["link_collection_count"] = len(pairs)
    out["is_conn_counts"] = census

    # Sampling, not a census: ``get_link()`` is several COM round trips each.
    # The ids to sample are chosen from the census rather than by walking the
    # collection in id order -- on Murdoch the connectors all sort AFTER the
    # 2,069 plain links, so an id-order walk would call ``get_link()`` two
    # thousand times before reaching the first connector. That is what timed
    # this probe out on its first v2 Murdoch attempt.
    wanted = [no for no, is_conn in pairs if is_conn == 0][:max_connectors] + [
        no for no, is_conn in pairs if is_conn == 1
    ][:max_connectors]
    links: list[dict[str, Any]] = []
    connectors: list[dict[str, Any]] = []
    for no in wanted:
        try:
            info = controller.get_link(no)
        except Exception as exc:  # noqa: BLE001
            connectors.append({"no": no, "error": f"{type(exc).__name__}: {exc}"})
            continue
        (connectors if info.get("is_connector") else links).append(info)
    out["plain_links_sample"] = links
    out["connectors_sample"] = connectors
    for name, accessor in (("nodes", "Nodes"), ("vehicle_compositions", "VehicleCompositions")):
        try:
            out[f"{name}_count"] = int(getattr(vissim.Net, accessor).Count)
        except Exception as exc:  # noqa: BLE001
            out[f"{name}_count"] = f"<unavailable: {type(exc).__name__}>"
    return out


# ---------------------------------------------------------------------------
# THE COM SEAM — the only two functions in this package that reach Vissim
# ---------------------------------------------------------------------------


def _connect_controller() -> Any:
    """A connected :class:`services.vissim_com.VissimController`, launched fresh.

    ``attach_to_running=False`` is not a default here, it is the rule: the
    alternative (``GetActiveObject``) attaches to an ARBITRARY registered
    instance including a human's open GUI, and the very next call below loads a
    network into whatever it attached to.

    THIS is the function offline tests replace (``monkeypatch.setattr(
    vissim_open_probe, "_connect_controller", ...)``) — the same single-line
    seam discipline ``genesis_jobs._build_fix_edit_session`` uses, so everything
    around the connection runs for real in tests.
    """
    import pythoncom  # noqa: PLC0415 - Windows/pywin32 only, see the module docstring

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController  # noqa: PLC0415

    controller = VissimController()
    controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
    return controller


def _release_controller(controller: Any) -> None:
    """Disconnect and uninitialise, best-effort in both halves.

    Every failure here is swallowed on purpose: the probe's evidence is already
    recorded by the time this runs, and a teardown exception must not replace a
    measured result with a stack trace.
    """
    try:
        if controller is not None:
            controller.disconnect()
    except Exception:  # noqa: BLE001 - see the docstring
        pass
    try:
        import pythoncom  # noqa: PLC0415

        pythoncom.CoUninitialize()
    except Exception:  # noqa: BLE001 - includes ImportError off Windows
        pass


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


def probe_open(
    net_path: Path | str,
    label: str,
    *,
    on_update: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Open ``net_path`` in a real Vissim and record what happened.

    Returns the record ``LoadOracle.from_probe_state`` reads (as the ``com``
    object of one run). A refused load is a FINDING, not a probe failure: the
    exception text and any ``.err`` Vissim wrote are captured verbatim and the
    record still comes back with ``load_ok: False``. Only an infrastructure
    failure (no COM at all) sets ``fatal_error``.

    ``on_update`` is called after every step with the record as it stands, so a
    caller that persists progressively still has what was already measured if
    the process dies mid-COM — the CLI's own "written after EVERY step" rule.
    """
    net_path = Path(net_path)
    result: dict[str, Any] = {
        "label": label,
        "network": str(net_path),
        "started_utc": _now(),
        "connected": False,
        "load_ok": None,
        "load_error": None,
        "save_ok": None,
        "save_error": None,
    }

    def flush() -> None:
        if on_update is not None:
            on_update(result)

    flush()
    controller = None
    try:
        t0 = time.time()
        controller = _connect_controller()
        result["connected"] = True
        result["prog_id"] = getattr(controller, "prog_id", None)
        result["connect_seconds"] = round(time.time() - t0, 2)
        result["version"] = read_version(controller.vissim)
        flush()

        pre_err = snapshot_err_paths(net_path.parent)
        result["err_files_before_load"] = sorted(pre_err)

        t0 = time.time()
        try:
            controller.load_network(str(net_path))
            result["load_ok"] = True
        except Exception as exc:  # noqa: BLE001 - a refused load is the finding
            result["load_ok"] = False
            result["load_error"] = f"{type(exc).__name__}: {exc}"
        result["load_seconds"] = round(time.time() - t0, 2)
        result["err_after_load"] = read_err_files(net_path.parent, pre_err)
        flush()

        if result["load_ok"]:
            try:
                result["com_topology"] = read_com_topology(controller)
            except Exception as exc:  # noqa: BLE001
                result["com_topology"] = {"error": f"{type(exc).__name__}: {exc}"}
            flush()

            # SaveNetAs into the SAME directory under a NEW name -- the file
            # that was opened is never written back to.
            saved = net_path.with_name(f"{net_path.stem}_saved.inpx")
            t0 = time.time()
            try:
                controller.save_net_as(saved)
                result["save_ok"] = True
                result["saved_path"] = str(saved)
            except Exception as exc:  # noqa: BLE001
                result["save_ok"] = False
                result["save_error"] = f"{type(exc).__name__}: {exc}"
            result["save_seconds"] = round(time.time() - t0, 2)
            result["err_after_save"] = read_err_files(net_path.parent, pre_err)
        result["finished_utc"] = _now()
        flush()
        return result
    except Exception as exc:  # noqa: BLE001 - infrastructure failure (no COM, no licence)
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
        result["finished_utc"] = _now()
        flush()
        return result
    finally:
        _release_controller(controller)
        flush()
