"""Read tools over the network shadow model: overview, lexical object
resolution, vehicle inputs, signal controllers, and conflict areas.

These are the first real tools registered through the M0.4 registry
(``services.agent.toolkit`` — see docs/phase2/02-ROADMAP.md M1.2); importing
this module registers them onto ``default_registry`` at decoration time.

``build_shadow_model`` assembles the EXTENDED shadow model M0.3's
constraints.py expects (see that module's docstring): links/nodes from
``services.inpx_parser.parse_network`` plus ``vehicle_inputs`` (parsed here
directly from the .inpx — no/name/link/volume, volume ``None`` when the demo
file's ``<vehicleInput>`` carries no ``timeIntervalVehVolume`` child),
``signals`` (from ``services.signal_parser.parse_signal_controllers``, mapped
down to the contract shape — ``controller_type`` is now populated for real
(v2 unit ①, 2026-07-24: ``signal_parser.py`` extracts the verbatim
``<signalController type="...">`` attribute, e.g. ``"FIXEDTIME"``/
``"FIXEDTIMESIMPLE"``/``"unknown"``). ``min_green_intergreen_sum`` remains
deliberately OMITTED, not set to ``None``: the parser still doesn't expose
it; see docs/phase2/DECISIONS.md's #5 gap entry. ``conflict_areas`` (v2 unit
④, 2026-07-24: parsed here directly from the .inpx — no/link_a/link_b/
link_pair/status/name — closing the M3.1 CATALOG-GAP so
``conflict_area_data_unavailable`` finally fires for real; see
:func:`_parse_conflict_areas`'s own docstring for the live-verified findings).

``resolve_object`` is the "network-RAG" pattern (research-digest #10): a
small, deterministic lexical top-k retrieval over the shadow model's objects.
There is no morphological analysis (Korean tokens split on whitespace, same
as English) and no embeddings — scoring is exact match > substring > shared
lowercase tokens, in that tier order, ties broken by object id ascending.

Every tool here is planner-facing and takes NO file paths — the LLM must
never see or choose a path. ``set_active_inpx`` is the module-level session
context the M1.3 orchestrator swaps per session (defaulting to
``config.SIMULATION_INPX_PATH``, the demo network); tests use the same hook.
"""

from __future__ import annotations

import difflib
import os
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

import config
from services.agent.toolkit import tool
from services.inpx_parser import parse_network
from services.signal_parser import parse_signal_controllers

ObjectType = Literal["link", "node", "vehicle_input", "signal", "conflict_area"]

# {object_type: {shadow_key, id_field, name_field}} — the one place that maps
# a planner-facing object_type onto the shadow model's (differently-named)
# id/name fields. Order here is the order get_network_overview renders in.
_OBJECT_TYPE_SPECS: dict[str, dict[str, str]] = {
    "link": {"shadow_key": "links", "id_field": "id", "name_field": "name"},
    "node": {"shadow_key": "nodes", "id_field": "id", "name_field": "name"},
    "vehicle_input": {"shadow_key": "vehicle_inputs", "id_field": "no", "name_field": "name"},
    "signal": {"shadow_key": "signals", "id_field": "sc_no", "name_field": "sc_name"},
    "conflict_area": {"shadow_key": "conflict_areas", "id_field": "no", "name_field": "name"},
}

_TOP_K = 5
_OVERVIEW_LIST_LIMIT = 30

# Exact > substring > token-overlap tiers. Token overlap is a fraction of the
# query's own tokens matched (0, 1], so it can never reach or exceed the
# substring tier's constant score.
_SCORE_EXACT = 3.0
_SCORE_SUBSTRING = 2.0


# ---------------------------------------------------------------------------
# Active-network session context (no paths ever reach the planner)
# ---------------------------------------------------------------------------

_active_inpx_override: Path | None = None


def set_active_inpx(path: str | Path | None) -> None:
    """Override the .inpx every tool in this module reads.

    ``None`` reverts to ``config.SIMULATION_INPX_PATH`` (the demo default) —
    which is what every session uses today, since neither the M1.3
    orchestrator nor the M1.4 router calls this. It exists as a test/ops
    override (tests use it to point at fixtures) until per-session dataset
    selection lands for the agent.
    """
    global _active_inpx_override
    _active_inpx_override = Path(path) if path is not None else None


def _active_inpx_path() -> Path:
    if _active_inpx_override is not None:
        return _active_inpx_override
    return Path(config.SIMULATION_INPX_PATH)


# ---------------------------------------------------------------------------
# Shadow model builder — links/nodes + vehicle_inputs + signals, mtime-cached
# ---------------------------------------------------------------------------


def _parse_vehicle_inputs(root: ET.Element) -> list[dict[str, Any]]:
    """{no, name, link, volume} per <vehicleInput> — volume None when the
    element carries no <timeIntervalVehVolume> child (the demo file's case)."""
    result: list[dict[str, Any]] = []
    for vi in root.findall(".//vehicleInput"):
        volume: float | None = None
        vol_el = vi.find(".//timeIntervalVehVolume")
        if vol_el is not None:
            try:
                volume = float(vol_el.get("volume"))
            except (TypeError, ValueError):
                volume = None
        result.append(
            {
                "no": vi.get("no", ""),
                "name": vi.get("name", ""),
                "link": vi.get("link", ""),
                "volume": volume,
            }
        )
    return result


def _parse_conflict_areas(root: ET.Element) -> list[dict[str, Any]]:
    """{no, link_a, link_b, link_pair, status, name} per ``<conflictArea>``.

    v2 unit (4): closes the M3.1 CATALOG-GAP on ``shadow_model["conflict_areas"]``
    (docs/phase2/DECISIONS.md, ``conflict_area_data_unavailable``). Verified
    live against Vissim 2026 (``backend/tools/conflict_area_probe.py``, Urban
    Intersection Beijing.CN, 224 real conflict areas): the .inpx XML DOES
    serialize conflict areas -- ``no``/``linkA``/``linkB``/``status`` --
    matching the COM ``AttValue`` ids of the same names exactly (unlike the
    CycTm/NodeRes landmines). Conflict area NUMBERS are NOT stable identity
    across geometry edits (docs/phase2/00-EXECUTION-GUIDE.md SS3.2), so every
    entry also carries ``link_pair`` -- the unordered-in-spirit (but here
    always ``(link_a, link_b)`` ordered) pair callers should snapshot/key on
    instead of the number when comparing across edits, per the catalog's own
    "Do" guidance. ``name`` is a synthetic, display-only "Link A vs Link B"
    label (conflict areas have no name attribute of their own) so
    ``resolve_object``/``get_network_overview`` can list them usefully.

    The demo network's ``.inpx`` has no ``<conflictArea>`` elements at all
    (verified: zero matches), so this returns ``[]`` there -- not absent,
    not ``None`` -- which is exactly the "no passive areas" shape
    ``conflict_area_data_unavailable`` already treats as silent (``not
    conflict_areas`` is ``True`` for an empty list too).
    """
    result: list[dict[str, Any]] = []
    for ca in root.findall(".//conflictArea"):
        link_a = ca.get("linkA", "")
        link_b = ca.get("linkB", "")
        result.append(
            {
                "no": ca.get("no", ""),
                "link_a": link_a,
                "link_b": link_b,
                "link_pair": (link_a, link_b),
                "status": ca.get("status", ""),
                "name": f"Link {link_a} vs Link {link_b}",
            }
        )
    return result


def _signal_to_contract_shape(info: dict[str, Any]) -> dict[str, Any]:
    """Map a signal_parser SignalInfo down to the M0.3 contract shape.

    ``controller_type`` is now populated for real (v2 unit ①): signal_parser
    extracts the verbatim ``<signalController type="...">`` attribute, so
    constraints.py #4 (``signal_controller_type_unsupported``) can finally
    distinguish COM-writable fixed-time controllers from RBC/VAP/Econolite
    ones. ``min_green_intergreen_sum`` remains OMITTED (not set to None) —
    signal_parser still doesn't expose it (DECISIONS.md #5 gap entry).
    """
    return {
        "sc_no": info["sc_no"],
        "sc_name": info["sc_name"],
        "cycle_time": info["cycle_time"],
        "controller_type": info.get("controller_type", "unknown"),
        "phases": info["phases"],
    }


def _build_shadow_model_uncached(inpx_path: Path) -> dict[str, Any]:
    net = parse_network(str(inpx_path))
    root = ET.parse(inpx_path).getroot()
    signals_by_sc_no = parse_signal_controllers(inpx_path)
    return {
        "links": net["links"],
        "nodes": net["nodes"],
        "vehicle_inputs": _parse_vehicle_inputs(root),
        "signals": [_signal_to_contract_shape(info) for info in signals_by_sc_no.values()],
        "conflict_areas": _parse_conflict_areas(root),
    }


@lru_cache(maxsize=8)
def _build_shadow_model_cached(inpx_path_str: str, mtime: float) -> dict[str, Any]:
    del mtime  # cache-busting arg only, mirrors assistant_router.py's precedent
    return _build_shadow_model_uncached(Path(inpx_path_str))


def build_shadow_model(inpx_path: Path) -> dict[str, Any]:
    """Assemble the extended agent shadow model for ``inpx_path``, mtime-cached."""
    inpx_path = Path(inpx_path)
    mtime = os.path.getmtime(inpx_path)
    return _build_shadow_model_cached(str(inpx_path), mtime)


def _current_shadow_model() -> dict[str, Any]:
    return build_shadow_model(_active_inpx_path())


# ---------------------------------------------------------------------------
# get_network_overview
# ---------------------------------------------------------------------------


def _compact_object_list(
    objects: list[dict[str, Any]], id_field: str, name_field: str, *, limit: int = _OVERVIEW_LIST_LIMIT
) -> dict[str, Any]:
    items = [{"id": str(obj.get(id_field, "")), "name": str(obj.get(name_field, ""))} for obj in objects]
    return {
        "count": len(items),
        "items": items[:limit],
        "truncated": len(items) > limit,
    }


class GetNetworkOverviewParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="get_network_overview", tier="read", provider="inspect", params=GetNetworkOverviewParams)
def get_network_overview(params: GetNetworkOverviewParams) -> dict[str, Any]:
    """Return object counts and compact id/name lists for links, nodes, vehicle inputs, and signals."""
    del params  # no fields
    shadow = _current_shadow_model()
    return {
        spec["shadow_key"]: _compact_object_list(
            shadow.get(spec["shadow_key"], []), spec["id_field"], spec["name_field"]
        )
        for spec in _OBJECT_TYPE_SPECS.values()
    }


# ---------------------------------------------------------------------------
# resolve_object
# ---------------------------------------------------------------------------


def _score(query: str, obj_id: str, obj_name: str) -> float:
    """Deterministic lexical score: exact > substring > shared-token fraction.

    Korean and English are treated identically — tokens split on whitespace,
    no morphological analysis (per the roadmap's network-RAG pattern note).
    """
    query_norm = (query or "").strip().lower()
    if not query_norm:
        return 0.0

    id_norm = (obj_id or "").strip().lower()
    name_norm = (obj_name or "").strip().lower()

    if query_norm == id_norm or query_norm == name_norm:
        return _SCORE_EXACT
    if query_norm in id_norm or query_norm in name_norm:
        return _SCORE_SUBSTRING

    query_tokens = set(query_norm.split())
    if not query_tokens:
        return 0.0
    field_tokens = set(name_norm.split()) | set(id_norm.split())
    overlap = query_tokens & field_tokens
    if not overlap:
        return 0.0
    return round(len(overlap) / len(query_tokens), 4)


def _iter_candidate_objects(shadow: dict[str, Any], object_type: str | None) -> list[tuple[str, str, str]]:
    """(id, name, object_type) tuples for every object of the requested
    type(s) — every type when ``object_type`` is None."""
    object_types = [object_type] if object_type else list(_OBJECT_TYPE_SPECS.keys())
    pool: list[tuple[str, str, str]] = []
    for otype in object_types:
        spec = _OBJECT_TYPE_SPECS[otype]
        for obj in shadow.get(spec["shadow_key"], []):
            pool.append((str(obj.get(spec["id_field"], "")), str(obj.get(spec["name_field"], "")), otype))
    return pool


def _difflib_candidates(query: str, pool: list[tuple[str, str, str]]) -> list[str]:
    """Fuzzy-match ``query`` against every object's id and name, always
    resolving a hit back to the owning object's id.

    Mirrors constraints.py's ``_check_object_not_found`` id-resolution
    behavior (that helper is private and tool-specific, so this is a
    deliberate mirror rather than an import — see the M1.2 brief).
    """
    owner_id_by_string: dict[str, str] = {}
    match_pool: list[str] = []
    for obj_id, name, _otype in pool:
        if obj_id and obj_id not in owner_id_by_string:
            match_pool.append(obj_id)
            owner_id_by_string[obj_id] = obj_id
        if name and name not in owner_id_by_string:
            match_pool.append(name)
            owner_id_by_string[name] = obj_id

    candidates: list[str] = []
    seen_ids: set[str] = set()
    for match in difflib.get_close_matches(query, match_pool, n=_TOP_K):
        owner_id = owner_id_by_string[match]
        if owner_id in seen_ids:
            continue
        seen_ids.add(owner_id)
        candidates.append(owner_id)
    return candidates


class ResolveObjectParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    object_type: ObjectType | None = None


@tool(name="resolve_object", tier="read", provider="inspect", params=ResolveObjectParams)
def resolve_object(params: ResolveObjectParams) -> dict[str, Any]:
    """Lexically resolve a name/id query to ranked candidate network objects (top-5)."""
    shadow = _current_shadow_model()
    pool = _iter_candidate_objects(shadow, params.object_type)

    scored = [
        {"id": obj_id, "name": name, "object_type": otype, "score": score}
        for obj_id, name, otype in pool
        if (score := _score(params.query, obj_id, name)) > 0
    ]
    scored.sort(key=lambda candidate: (-candidate["score"], candidate["id"]))
    top = scored[:_TOP_K]

    if not top:
        return {
            "found": False,
            "error_type": "object_not_found",
            "candidates": _difflib_candidates(params.query, pool),
        }
    return {"found": True, "candidates": top}


# ---------------------------------------------------------------------------
# get_vehicle_inputs / get_signal_controllers
# ---------------------------------------------------------------------------


class GetVehicleInputsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="get_vehicle_inputs", tier="read", provider="inspect", params=GetVehicleInputsParams)
def get_vehicle_inputs(params: GetVehicleInputsParams) -> dict[str, Any]:
    """Return the network's vehicle input objects (id, name, link, volume)."""
    del params  # no fields
    vehicle_inputs = _current_shadow_model()["vehicle_inputs"]
    return {"count": len(vehicle_inputs), "vehicle_inputs": vehicle_inputs}


class GetSignalControllersParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="get_signal_controllers", tier="read", provider="inspect", params=GetSignalControllersParams)
def get_signal_controllers(params: GetSignalControllersParams) -> dict[str, Any]:
    """Return the network's signal controller objects (id, name, cycle time, phases)."""
    del params  # no fields
    signals = _current_shadow_model()["signals"]
    return {"count": len(signals), "signals": signals}


# ---------------------------------------------------------------------------
# get_conflict_areas — v2 unit (4)
# ---------------------------------------------------------------------------


class GetConflictAreasParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@tool(name="get_conflict_areas", tier="read", provider="inspect", params=GetConflictAreasParams)
def get_conflict_areas(params: GetConflictAreasParams) -> dict[str, Any]:
    """Return the network's conflict areas (id, link pair, right-of-way status) -- conflict areas default to Passive (no right-of-way control, vehicles drive through each other) and this is how the agent sees which ones are Passive before deciding whether to fix any with set_conflict_area_status."""
    del params  # no fields
    conflict_areas = _current_shadow_model()["conflict_areas"]
    return {"count": len(conflict_areas), "conflict_areas": conflict_areas}
