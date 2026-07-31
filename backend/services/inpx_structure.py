"""The v2b XML-build spine: schema-aware element construction/removal,
reparse verification, reference-integrity verification, and the COM load
oracle for STRUCTURAL .inpx edits (creating/removing objects), as opposed
to every write tool that predates this module, which only ever SETS
attributes on objects that already exist.

This is the reusable machinery docs/product/master-roadmap-2026-07.md item
1.1 calls "the longest wall" — every later network-genesis milestone (2.3
geometry, 3.1 signals, 3.2 demand) stands on it. Four components, in the
order a caller uses them:

1. **Element construction/removal helpers** (:func:`get_or_create_top_level_
   section`, :func:`next_id`) — schema-aware id allocation and child
   ordering, generalized from ``simulation_network_builder.
   inject_reduced_speed_area``'s own (RSA-specific) versions of the same
   ideas: a real Vissim export's ``<network>`` sorts its direct children
   alphabetically by tag name (verified against multiple real 2026 example
   networks), and new ids follow the existing ``max(existing) + 1``
   convention already used by every COM verb that allocates one.

2. **Reparse verification** (:func:`verify_reparse`) — after ANY structural
   write, the shadow-model parse of the result must succeed and reflect the
   intended change; builds on ``tools_network.build_shadow_model`` (never a
   second, competing parser).

3. **Reference-integrity verification** (:data:`ID_REFERENCE_SITES`,
   :data:`LANE_TOKEN_REFERENCE_SITES`, :func:`find_id_references`,
   :func:`find_lane_token_references`) — a general id-bearing-element
   registry + reference-attribute map: given the CURRENT tree and a
   proposed removal, name every element that still references the id/lane
   about to disappear. Removal helpers in this module REFUSE (never
   cascade) when references exist. Deliberately data-driven (a
   ``ReferenceSite`` tuple per reference shape) so a later genesis unit
   extends coverage by appending entries, not by writing new traversal
   code.

4. **The COM load oracle as structural postwrite** (:func:`run_load_oracle`,
   :func:`apply_structural_edit`) — reuses ``tools_check._load_check`` (the
   same LoadNet + one RunSingleStep probe ``load_check`` the tool already
   exposes) as a POST-WRITE gate for a structural edit: if the mutated
   working copy fails to load/init, the edit is rolled back to its exact
   pre-edit bytes before the caller ever sees a result. This is IN ADDITION
   to (2) — a network can reparse cleanly (well-formed XML, the shadow
   model sees the intended change) and still fail to LOAD in Vissim (e.g. a
   dangling reference this module's own registry does not yet cover) or fail
   to INITIALIZE a simulation (a corruption ``load_check``'s own docstring
   already catalogs as diagnosable) — the oracle is Vissim's own opinion,
   not a second guess at ours.

**What this unit ships as the proof pair**: :func:`remove_last_lane` /
:func:`add_lane` — a genuinely STRUCTURAL edit found directly within lane-
closure semantics while probing ``set_lane_closure`` (see
``backend/tools/probe_lane_closure.py``): physically removing/adding a
``<lane>`` child element (the "hard" close — permanently narrowing a link —
as opposed to ``set_lane_closure``'s "soft", reversible, per-vehicle-class
``BlockedVehClasses`` attribute, which is what actually shipped as the
catalog's "Lane closure / reopen" verb — see ``tools_edit.py``'s own
docstring for why the attribute route was the honest, catalog-correct
choice). Deliberately scoped to the LAST lane only (index == the link's
current lane count): removing or inserting an INTERIOR lane would shift
every later lane's 1-based ordinal identity (a lane has no identity of its
own besides that ordinal — confirmed live, see ``tools_network.
_parse_link_lanes``'s docstring), silently invalidating any reference-
integrity check keyed on an index that just changed meaning. The last-lane
restriction sidesteps that whole class of index-shift bugs while still
being a real, unrestricted structural add/remove — not a synthetic
placeholder element.

Not registered as a planner-facing ``@tool`` in this unit (a deliberate
scope cut, not an oversight — see the unit's own report): exercised
directly by tests and by
``backend/tools/lane_removal_structural_live_check.py``. A future genesis
milestone that wants a planner-reachable "remove the last lane of a link"
verb only needs a thin ``tools_edit.py`` wrapper (params model + guardrails
table entries) around :func:`remove_last_lane`/:func:`add_lane` — the hard
part (structure, verification, rollback-on-load-failure) is already done
here.
"""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Component 1: element construction/removal helpers
# ---------------------------------------------------------------------------


def get_or_create_top_level_section(root: ET.Element, tag: str) -> ET.Element:
    """Return ``root``'s existing direct-child ``<tag>`` element, or create
    and insert an empty one at the alphabetically-correct position among
    ``root``'s CURRENT direct children.

    Generalized from ``simulation_network_builder._get_or_create_top_level_
    section`` (v2 unit ②'s own, RSA-scoped copy of the same idea) — a real
    Vissim export's ``<network>`` sorts its direct children alphabetically
    by tag name (verified against two independent real 2026 example
    networks' full child-tag listings: Air Taxi Vertiport, Urban
    Intersection Beijing.CN), and Vissim OMITS a section entirely when a
    network never used that feature rather than exporting it empty — so a
    structural writer needing a section for the first time must create AND
    correctly place it, never merely append before ``</network>``, in case
    LoadNet performs order-sensitive validation. Only ever looks at DIRECT
    children of ``root`` (never ``.//tag``) — a top-level section is never
    nested.
    """
    existing = root.find(tag)
    if existing is not None:
        return existing

    insert_at = len(list(root))
    for index, child in enumerate(root):
        if child.tag > tag:
            insert_at = index
            break
    section = ET.Element(tag)
    root.insert(insert_at, section)
    return section


def next_id(container: ET.Element, child_tag: str, id_attr: str = "no") -> str:
    """``max(existing) + 1`` id allocation — the SAME convention every COM
    write verb in this codebase that allocates a new id already follows
    (mirrors ``simulation_network_builder._next_number``, generalized to
    any ``(container, child_tag, id_attr)`` triple rather than a hardcoded
    ``"no"``-only, RSA-scoped version). Returns a string (every id in this
    codebase's shadow model is string-typed, per ``guardrails.py``'s own
    string-keyed id fields)."""
    numbers: list[int] = []
    for child in container.findall(child_tag):
        value = child.get(id_attr)
        if value is not None:
            try:
                numbers.append(int(value))
            except ValueError:
                continue
    return str((max(numbers) if numbers else 0) + 1)


# ---------------------------------------------------------------------------
# Component 2: reparse verification
# ---------------------------------------------------------------------------


class ReparseVerificationError(RuntimeError):
    """A structural write's saved file either failed to reparse at all, or
    reparsed but did not reflect the intended change (``expect`` returned
    ``False``) — see :func:`verify_reparse`."""


def verify_reparse(
    inpx_path: Path | str,
    *,
    expect: Callable[[dict[str, Any]], bool] | None = None,
    expect_description: str = "",
) -> dict[str, Any]:
    """Reparse ``inpx_path`` via ``tools_network.build_shadow_model`` (never
    a second, competing parser) and return the resulting shadow model.

    Raises :class:`ReparseVerificationError` when the reparse itself raises
    (malformed XML — a structural write must never leave the working copy
    unparseable) or, when ``expect`` is given, when it returns ``False``
    against the fresh shadow model (the parse succeeded but does not show
    the intended change — e.g. a lane count that didn't actually change).
    Imports ``tools_network`` lazily to avoid a module-load-time import
    cycle (``services.agent.tools_edit`` already imports THIS module for
    the same reason a future genesis tool would).
    """
    from services.agent.tools_network import build_shadow_model

    try:
        shadow = build_shadow_model(Path(inpx_path))
    except Exception as exc:
        raise ReparseVerificationError(
            f"Reparse of {inpx_path} failed after a structural write: {exc}"
        ) from exc

    if expect is not None and not expect(shadow):
        detail = f": {expect_description}" if expect_description else ""
        raise ReparseVerificationError(
            f"Reparse of {inpx_path} succeeded but did not reflect the intended "
            f"structural change{detail}"
        )
    return shadow


# ---------------------------------------------------------------------------
# Component 3: reference-integrity registry + verifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceSite:
    """One place in the .inpx where another element can reference an
    id-bearing object (or a lane token) by a single attribute.

    ``xpath`` is evaluated from the document root (``ElementTree.findall``
    syntax) and names the REFERRING elements; ``attr`` is the attribute on
    each matched element that holds the reference value. A referrer whose
    ``attr`` is absent/empty is simply not a match (never an error) —
    mirrors every existing parser in this codebase's "absent means no
    reference" convention (e.g. ``_parse_connector_endpoint``).
    """

    description: str
    xpath: str
    attr: str
    #: ``None`` (default): the whole attribute value is compared against
    #: the target id. An int: the attribute is whitespace-split and the
    #: token at this position is compared instead -- for attributes that
    #: pack MULTIPLE tokens into one value (Vissim's own ``"<link> <lane>"``
    #: convention), so a link-level check can still match a lane-token
    #: attribute's first token without a second, near-duplicate traversal.
    token_position: int | None = None


#: General id-bearing element registry: object_class -> (xpath to every
#: such element, its id attribute). Seeded with "links" (this unit's own
#: touch point); extensible by appending entries for future genesis units
#: (nodes, connectors-as-a-distinct-class, signal controllers, ...) without
#: changing any traversal code below.
ID_BEARING_ELEMENTS: dict[str, tuple[str, str]] = {
    "links": (".//links/link", "no"),
    # Q4 unit (2): measurement-infrastructure placement. Only data-collection
    # points are ever REFERENCED by another element type (a
    # dataCollectionMeasurement's own <dataCollectionPoints><intObjectRef
    # key="..."/></dataCollectionPoints> children -- see the module-level
    # ID_REFERENCE_SITES entry below); queue counters and travel-time
    # sections are leaf objects (verified against every real Vissim 2026
    # example network that uses one, backend/tools/probe_measurement_infra.py
    # -- nothing else in the schema ever points at either by id), so they are
    # not registered here at all -- removal needs no reference-integrity
    # check, only the object's own existence (constraints.py's
    # object_not_found / _TOOL_TARGET_SPEC, wired against the shadow model's
    # own "queue_counters"/"travel_time_sections" lists, not this registry).
    "data_collection_points": (".//dataCollectionPoints/dataCollectionPoint", "no"),
}

#: Plain single-id reference sites, keyed by the object_class being
#: referenced. A connector is itself a ``<link>`` (Vissim's own convention
#: -- verified live, ``backend/tools/probe_lane_closure.py``'s connector
#: dump on the Beijing example) whose ``fromLinkEndPt``/``toLinkEndPt``
#: children carry a plain ``link="<no>"`` attribute on a REAL Vissim export.
#: This codebase's own hand-authored static fixture (``data/practice.inpx``
#: -- NOT a real export, see ``tools_network._parse_connector_endpoint``'s
#: docstring) instead uses a ``lane="<no> <laneIdx>"`` form at the SAME
#: element with no separate ``link`` attribute at all -- both are covered
#: here (``token_position=0`` reads just the link-number token out of the
#: lane form) since a real deployment will see either shape depending on
#: how the network was authored/exported.
ID_REFERENCE_SITES: dict[str, tuple[ReferenceSite, ...]] = {
    "links": (
        ReferenceSite("connector fromLinkEndPt", ".//links/link/fromLinkEndPt", "link"),
        ReferenceSite("connector toLinkEndPt", ".//links/link/toLinkEndPt", "link"),
        ReferenceSite(
            "connector fromLinkEndPt (lane form)", ".//links/link/fromLinkEndPt", "lane", token_position=0
        ),
        ReferenceSite(
            "connector toLinkEndPt (lane form)", ".//links/link/toLinkEndPt", "lane", token_position=0
        ),
        # Q4 unit (3): full link closure + turn bans (movement removal).
        # remove_connector's own referrer catalog -- every site below was
        # live-verified relevant by backend/tools/probe_link_closure_turn_bans.py
        # before being added, not guessed:
        #
        # A route's own <linkSeq><intObjectRef key="..."/></linkSeq> names
        # every link/connector it traverses (v2 unit ⑥'s own finding,
        # tools_network._parse_routing_decisions) -- a route still naming a
        # connector about to be STRUCTURALLY removed is a genuine dangling
        # reference (unlike set_turn_ban's reversible BlockedVehClasses
        # closure, which leaves the route syntactically intact -- see that
        # tool's own route-consequence WARN in constraints.py instead of a
        # refusal).
        ReferenceSite("route link sequence", ".//linkSeq/intObjectRef", "key"),
        # A priority rule / conflict marker's "intLink" whole-link reference
        # form (2026-07-31 DECISIONS entry: real Murdoch rule 143 uses
        # intLink="613" with NO lane attribute at all) -- distinct from the
        # lane-token "lane" form LANE_TOKEN_REFERENCE_SITES already covers
        # below.
        ReferenceSite("priority rule (intLink)", ".//priorityRule", "intLink"),
        ReferenceSite(
            "priority rule conflict marker (intLink)", ".//priorityRule/conflictMarker", "intLink"
        ),
        # A conflict area's linkA/linkB. Live-verified
        # (probe_link_closure_turn_bans.py [1b.9]) that Vissim's own LoadNet
        # does NOT refuse a dangling conflictArea reference -- it SILENTLY
        # drops the stale conflict area on the next load/save instead (the
        # exact "Vissim almost never refuses a bad edit" thesis this
        # codebase's validation layer exists to counter), and 6317/8494
        # (74%) of every conflict area across all 25 real example networks
        # names a CONNECTOR as linkA/linkB -- the COMMON case, not an edge
        # case. remove_connector refuses and names these rather than
        # silently letting Vissim's own recompute erase them unannounced.
        ReferenceSite("conflict area link A", ".//conflictArea", "linkA"),
        ReferenceSite("conflict area link B", ".//conflictArea", "linkB"),
    ),
    # Q4 unit (2): a dataCollectionMeasurement groups 1..N data-collection
    # points by id via one <intObjectRef key="<point_no>"/> per point --
    # verified live/statically across every real Vissim 2026 example network
    # that uses one (backend/tools/probe_measurement_infra.py, Toll Plaza
    # Camden.US measurement no=1 groups points 1/2/3): the SAME
    # <intObjectRef> convention _parse_link_lanes already found for a lane's
    # blockedVehClasses. COM itself does NOT refuse removing a still-
    # referenced point (live-verified, same probe, [followup] step -- a
    # RemoveDataCollectionPoint call on a point two measurements still
    # reference SUCCEEDS silently, leaving the measurement's own
    # DataCollectionPoints membership pointing at a now-nonexistent point),
    # so this registry entry -- and remove_data_collection_point's own
    # preflight use of find_id_references against it, in tools_edit.py -- is
    # the ONLY thing standing between a careless removal and exactly the
    # kind of dangling reference Vissim itself will not catch.
    "data_collection_points": (
        ReferenceSite(
            "data-collection measurement point ref",
            ".//dataCollectionMeasurements/dataCollectionMeasurement/dataCollectionPoints/intObjectRef",
            "key",
        ),
    ),
}

#: Lane-TOKEN reference sites -- Vissim's own "<linkNo> <laneIndex>"
#: two-token convention (space-separated), reused verbatim across several
#: unrelated object types (verified live/statically, see each site's own
#: comment). A lane has no identity of its own besides this token (see
#: ``tools_network._parse_link_lanes``'s docstring), so lane reference-
#: integrity is checked by token, not by a single id attribute the way
#: ID_REFERENCE_SITES above works.
LANE_TOKEN_REFERENCE_SITES: tuple[ReferenceSite, ...] = (
    # A connector's own <lane> child (itself a lane of the CONNECTOR link)
    # carries <fromLanes>/<toLanes><objectRef key="<linkNo> <laneIndex>"/>
    # pointing at the SOURCE/DEST link's specific lane (verified live,
    # Urban Intersection Beijing.CN: <objectRef key="9 2"/> inside a
    # connector's <fromLanes>).
    ReferenceSite("connector fromLanes", ".//links/link/lanes/lane/fromLanes/objectRef", "key"),
    ReferenceSite("connector toLanes", ".//links/link/lanes/lane/toLanes/objectRef", "key"),
    # data/practice.inpx's own simplified fixture convention (NOT a real
    # Vissim export -- see ID_REFERENCE_SITES's comment above): the
    # "<link> <lane>" token lives directly on the endpoint, no nested
    # <lanes>/<lane>/<fromLanes>/<objectRef> structure at all.
    ReferenceSite("connector fromLinkEndPt (lane form)", ".//links/link/fromLinkEndPt", "lane"),
    ReferenceSite("connector toLinkEndPt (lane form)", ".//links/link/toLinkEndPt", "lane"),
    # A reducedSpeedArea's own "lane" attribute (verified live/statically,
    # every real example network that uses one, e.g. Air Taxi Vertiport:
    # <reducedSpeedArea lane="3 1" .../>).
    ReferenceSite("reduced speed area", ".//reducedSpeedAreas/reducedSpeedArea", "lane"),
    # A priority rule's own "lane" attribute, and its conflict markers'
    # (verified statically against the real Murdoch production model's
    # rules 144/145: lane="613 1"/"613 2" -- see tools_network.
    # _parse_priority_rule_link_ref's own docstring for the "lane" vs
    # "intLink" two-form finding; only the "lane" form is a lane-token
    # reference, "intLink" references a whole LINK, not a specific lane).
    ReferenceSite("priority rule", ".//priorityRule", "lane"),
    ReferenceSite("priority rule conflict marker", ".//priorityRule/conflictMarker", "lane"),
    # Q4 unit (4): signal-head + detector placement. Both carry the SAME
    # space-separated "<link> <lane>" XML token on their own "lane" attribute
    # (verified live/statically, backend/tools/probe_signal_heads_detectors.py
    # across all 25 real Vissim 2026 example networks + live COM against
    # Urban Intersection Beijing.CN/Toll Plaza Camden.US) -- so a lane
    # structurally removed by remove_last_lane/remove_connector (v2b units
    # (1)/(3)) while a signal head or detector still references it is now
    # caught by this registry BEFORE either of those pre-existing removal
    # verbs ever touches the file, exactly like reducedSpeedArea/priorityRule
    # above. Neither object type existed in this codebase's shadow model
    # before this unit, so this entry closes a real, previously-unreachable
    # gap in those two verbs' own reference-integrity coverage -- not merely
    # future-proofing for a hypothetical.
    ReferenceSite("signal head", ".//signalHeads/signalHead", "lane"),
    ReferenceSite("detector", ".//detectors/detector", "lane"),
)


def _normalize_token(raw: str | None) -> str:
    return " ".join((raw or "").split())


def find_id_references(root: ET.Element, object_class: str, object_id: str) -> list[dict[str, Any]]:
    """Every element referencing ``object_id`` (a plain, single-token id —
    e.g. a link number) via :data:`ID_REFERENCE_SITES`'s entries for
    ``object_class``. Empty list = no referrers found (safe to remove, as
    far as this registry can see — see the module docstring for how a
    future unit extends coverage)."""
    referrers: list[dict[str, Any]] = []
    for site in ID_REFERENCE_SITES.get(object_class, ()):
        for element in root.findall(site.xpath):
            raw = element.get(site.attr)
            if raw is None:
                continue
            if site.token_position is None:
                value = raw.strip()
            else:
                tokens = raw.split()
                value = tokens[site.token_position] if len(tokens) > site.token_position else None
            if value == str(object_id):
                referrers.append(
                    {
                        "site": site.description,
                        "tag": element.tag,
                        "attribute": site.attr,
                        "value": raw,
                    }
                )
    return referrers


def find_lane_token_references(root: ET.Element, link_no: str, lane_index: int) -> list[dict[str, Any]]:
    """Every element referencing lane ``lane_index`` of link ``link_no`` (the
    ``"<link_no> <lane_index>"`` token convention) via
    :data:`LANE_TOKEN_REFERENCE_SITES`. Empty list = no referrers found."""
    token = f"{link_no} {lane_index}"
    referrers: list[dict[str, Any]] = []
    for site in LANE_TOKEN_REFERENCE_SITES:
        for element in root.findall(site.xpath):
            if _normalize_token(element.get(site.attr)) == token:
                referrers.append(
                    {
                        "site": site.description,
                        "tag": element.tag,
                        "attribute": site.attr,
                        "value": element.get(site.attr),
                    }
                )
    return referrers


class ReferenceIntegrityError(RuntimeError):
    """A removal was refused because other elements still reference the
    object/lane about to disappear. ``referrers`` names every one of them
    (never a bare count) — the guardrail doctrine's "a refused edit with a
    clear reason" applied to structural edits."""

    def __init__(self, description: str, referrers: list[dict[str, Any]]) -> None:
        self.referrers = referrers
        rendered = "; ".join(
            f"{r['site']} ({r['tag']}@{r['attribute']}={r['value']!r})" for r in referrers
        )
        super().__init__(f"{description}: still referenced by {rendered}")


def check_new_object_references(
    root: ET.Element,
    *,
    referring_xpath: str,
    ref_attr: str,
    target_object_class: str,
) -> list[dict[str, Any]]:
    """The OTHER half of reference integrity: do the newly-created elements
    matched by ``referring_xpath`` reference an id that genuinely exists in
    ``target_object_class`` (per :data:`ID_BEARING_ELEMENTS`)? Returns one
    entry per DANGLING reference found (referencing a nonexistent id) —
    empty list = every reference resolves, safe to accept the add.

    This is the "new object referencing nonexistent ids" half of the
    reference-integrity verifier the task brief asks for; :func:`find_id_
    references`/:func:`find_lane_token_references` above are the "removed
    object still referenced" half.
    """
    target_xpath, target_id_attr = ID_BEARING_ELEMENTS[target_object_class]
    existing_ids = {el.get(target_id_attr) for el in root.findall(target_xpath)}

    dangling: list[dict[str, Any]] = []
    for element in root.findall(referring_xpath):
        value = element.get(ref_attr)
        if value is None:
            continue
        if value not in existing_ids:
            dangling.append({"tag": element.tag, "attribute": ref_attr, "value": value})
    return dangling


# ---------------------------------------------------------------------------
# Component 4: the COM load oracle as structural postwrite
# ---------------------------------------------------------------------------


def run_load_oracle(inpx_path: Path | str) -> dict[str, Any]:
    """Thin wrapper around ``tools_check._load_check`` (the exact same
    LoadNet + one RunSingleStep probe the ``load_check`` tool exposes) —
    reused, not duplicated, per this codebase's own "one COM-boilerplate
    implementation" convention (mirrors how ``rollback.py``/``batch.py``
    reuse ``ArtifactManager`` rather than re-deriving file operations).

    Returns ``{"load_ok", "load_error", "init_ok", "init_error",
    "err_content"}`` — see ``tools_check._load_check``'s own docstring for
    the full shape. Costs a real, dedicated Vissim instance
    (``attach_to_running=False``) and a licence seat, same as ``load_check``
    itself — callers should not run this more than once per structural
    write.
    """
    from services.agent.tools_check import _load_check

    return _load_check(Path(inpx_path))


class StructuralEditRolledBackError(RuntimeError):
    """A structural edit reparsed cleanly but failed Vissim's own load/init
    oracle — the working copy has been restored to its exact pre-edit
    bytes before this was raised. ``load_check_result`` carries the
    oracle's own diagnostic payload (same shape ``load_check`` returns)."""

    def __init__(self, load_check_result: dict[str, Any]) -> None:
        self.load_check_result = load_check_result
        if not load_check_result.get("load_ok"):
            reason = f"LoadNet failed: {load_check_result.get('load_error')}"
        else:
            reason = f"initialization failed: {load_check_result.get('init_error')}"
        super().__init__(
            f"Structural edit rolled back -- the mutated network did not pass the COM load "
            f"oracle ({reason})."
        )


def apply_structural_edit(
    inpx_path: Path | str,
    mutate: Callable[[ET.Element], dict[str, Any]],
    *,
    reparse_expect: Callable[[dict[str, Any]], bool] | None = None,
    reparse_expect_description: str = "",
    run_load_check: bool = True,
) -> dict[str, Any]:
    """The spine's single entry point for a structural write: parse
    ``inpx_path``, let ``mutate(root)`` edit the in-memory tree IN PLACE and
    return whatever metadata it wants surfaced, serialize the result back
    to ``inpx_path`` (sibling-temp-file + ``os.replace``, mirroring
    ``inject_reduced_speed_area``'s crash-safe two-step write), then run
    components 2 and 4 in order:

    1. :func:`verify_reparse` — malformed XML or a reparse that doesn't
       show the intended change raises immediately (the working copy is
       NOT reverted here — a caller that wants pre-mutation bytes back on
       THIS failure should catch and restore itself; in practice every
       failure this stage can produce is a bug in ``mutate`` itself, not a
       recoverable runtime condition, so this module does not spend a
       backup/restore cycle guarding against it).
    2. :func:`run_load_oracle` (skippable via ``run_load_check=False`` for
       tests that stub COM out entirely) — a load/init FAILURE reverts
       ``inpx_path`` to its exact pre-mutation bytes (read into memory
       before the mutation ever touched disk) and raises
       :class:`StructuralEditRolledBackError`; every future structural
       write built on this function inherits "must pass the load oracle or
       be rolled back" for free.

    Returns ``{"metadata": <mutate's own return value>, "shadow_model": ...,
    "load_check_result": ... | None}`` on success.
    """
    inpx_path = Path(inpx_path)
    original_bytes = inpx_path.read_bytes()

    tree = ET.parse(inpx_path)
    root = tree.getroot()
    metadata = mutate(root)

    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    tmp_path = inpx_path.with_name(inpx_path.stem + "_structural_saving" + inpx_path.suffix)
    tree.write(tmp_path, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp_path, inpx_path)

    shadow_model = verify_reparse(
        inpx_path, expect=reparse_expect, expect_description=reparse_expect_description
    )

    load_check_result: dict[str, Any] | None = None
    if run_load_check:
        load_check_result = run_load_oracle(inpx_path)
        if not (load_check_result.get("load_ok") and load_check_result.get("init_ok")):
            inpx_path.write_bytes(original_bytes)
            raise StructuralEditRolledBackError(load_check_result)

    return {"metadata": metadata, "shadow_model": shadow_model, "load_check_result": load_check_result}


# ---------------------------------------------------------------------------
# The proof pair: remove_last_lane / add_lane (see module docstring)
# ---------------------------------------------------------------------------


def _find_link(root: ET.Element, link_no: str) -> ET.Element:
    for link in root.findall(".//links/link"):
        if link.get("no") == str(link_no):
            return link
    raise ValueError(f"link {link_no!r} not found")


def remove_last_lane(
    inpx_path: Path | str, link_no: str, *, run_load_check: bool = True
) -> dict[str, Any]:
    """Structurally remove the LAST (highest-index) lane of ``link_no`` --
    a "hard" lane closure: permanently narrowing the link's own lane count,
    as opposed to ``set_lane_closure``'s reversible per-class attribute
    (see the module docstring for why this is scoped to the last lane
    only). REFUSES (raises :class:`ReferenceIntegrityError`, never touches
    the file) when any connector/reduced-speed-area/priority-rule still
    references that lane by its ``"<link_no> <lane_index>"`` token — see
    :func:`find_lane_token_references`.

    Raises :class:`ValueError` if the link does not exist or has only one
    lane left (removing the LAST lane of a link would leave it with zero
    lanes -- not a valid Vissim link; refused before any write, same
    "refuse with a clear reason" discipline as every other guard here).
    """
    root = ET.parse(inpx_path).getroot()
    link = _find_link(root, link_no)
    lanes_el = link.find("lanes")
    lane_count = len(lanes_el.findall("lane")) if lanes_el is not None else 0
    if lane_count <= 1:
        raise ValueError(
            f"remove_last_lane: link {link_no} has {lane_count} lane(s) -- refusing to remove "
            "its last remaining lane (a link cannot have zero lanes)."
        )

    referrers = find_lane_token_references(root, str(link_no), lane_count)
    if referrers:
        raise ReferenceIntegrityError(
            f"Cannot remove lane {lane_count} of link {link_no}", referrers
        )

    def _mutate(root: ET.Element) -> dict[str, Any]:
        link = _find_link(root, link_no)
        lanes_el = link.find("lanes")
        lanes = lanes_el.findall("lane")
        removed = lanes[-1]
        removed_width = float(removed.get("width", "3.5") or 3.5)
        lanes_el.remove(removed)
        return {"link_no": str(link_no), "removed_lane_index": lane_count, "removed_width": removed_width}

    def _expect(shadow: dict[str, Any]) -> bool:
        target_link = next((l for l in shadow["links"] if l["id"] == str(link_no)), None)
        return target_link is not None and target_link["lanes"] == lane_count - 1

    return apply_structural_edit(
        inpx_path,
        _mutate,
        reparse_expect=_expect,
        reparse_expect_description=f"link {link_no} should have {lane_count - 1} lane(s)",
        run_load_check=run_load_check,
    )


def add_lane(
    inpx_path: Path | str,
    link_no: str,
    width: float = 3.5,
    *,
    marking_type: str | None = None,
    run_load_check: bool = True,
) -> dict[str, Any]:
    """Structurally add one lane at the END of ``link_no``'s ``<lanes>``
    (the reopen/undo half of :func:`remove_last_lane`) -- no reference-
    integrity concern on the add path (nothing can reference a lane index
    that does not exist yet), so this only runs components 1/2/4, not 3.
    """
    root = ET.parse(inpx_path).getroot()
    link = _find_link(root, link_no)
    lanes_el = link.find("lanes")
    lane_count_before = len(lanes_el.findall("lane")) if lanes_el is not None else 0

    def _mutate(root: ET.Element) -> dict[str, Any]:
        link = _find_link(root, link_no)
        lanes_el = link.find("lanes")
        if lanes_el is None:
            raise ValueError(f"add_lane: link {link_no} has no <lanes> element")
        attrib = {"width": f"{float(width):g}"}
        if marking_type is not None:
            attrib["markingType"] = str(marking_type)
        ET.SubElement(lanes_el, "lane", attrib)
        return {"link_no": str(link_no), "added_lane_index": lane_count_before + 1, "width": float(width)}

    def _expect(shadow: dict[str, Any]) -> bool:
        target_link = next((l for l in shadow["links"] if l["id"] == str(link_no)), None)
        return target_link is not None and target_link["lanes"] == lane_count_before + 1

    return apply_structural_edit(
        inpx_path,
        _mutate,
        reparse_expect=_expect,
        reparse_expect_description=f"link {link_no} should have {lane_count_before + 1} lane(s)",
        run_load_check=run_load_check,
    )


# ---------------------------------------------------------------------------
# Q4 unit (3): remove_connector -- the true structural "movement removal"
# verb (as opposed to set_turn_ban's reversible, per-class BlockedVehClasses
# closure -- see services.agent.tools_edit's own module comment for that
# pair). A connector IS a plain ``<link>`` element (Vissim's own convention,
# re-verified live by backend/tools/probe_link_closure_turn_bans.py [1b.4]:
# ``Net.Links.ItemByKey`` resolves a connector identically to a plain link,
# same ``.Lanes`` child collection) distinguished only by carrying a
# ``fromLinkEndPt``/``toLinkEndPt`` child -- so removal is exactly
# :func:`remove_last_lane`'s own "find the element, refuse if referenced,
# remove it, verify via apply_structural_edit" shape, generalized from ONE
# lane to the WHOLE link (every one of the connector's own lanes disappears
# at once, which is fine -- unlike a lane's ordinal identity inside a link
# that survives, a REMOVED connector's own lane indices reference nothing
# once the connector itself is gone, so there is no "shift meaning" hazard
# the last-lane-only restriction on :func:`remove_last_lane` was guarding
# against).
#
# **Reference-integrity is the whole point of this function.** Live-verified
# (probe_link_closure_turn_bans.py [1b.9]): Vissim's own LoadNet does NOT
# refuse when a conflict area's ``linkA``/``linkB`` still names a connector
# that no longer exists in the XML -- LoadNet succeeds, RunSingleStep
# succeeds, and Vissim SILENTLY drops the stale conflict area on its own
# next load/recompute (23 conflict areas survived where 24 existed before,
# with zero error/warning of any kind) -- exactly the "Vissim almost never
# refuses a bad edit" thesis this codebase's validation layer exists to
# counter. :data:`ID_REFERENCE_SITES`'s own ``"links"`` entry was extended
# this unit with route ``linkSeq``, priority-rule/conflict-marker
# ``intLink``, and conflict-area ``linkA``/``linkB`` sites specifically so
# :func:`find_id_references` catches every one of these BEFORE any write --
# a genuine refusal, never a silent cascade, mirroring
# ``tools_edit.remove_data_collection_point``'s identical "COM itself does
# not enforce this, so this codebase's own spine does" precedent. Every one
# of the connector's own lanes is also checked via
# :func:`find_lane_token_references` (fromLanes/toLanes on ANOTHER
# connector, a reduced-speed area, or a priority rule's lane-token form) --
# generalized from :func:`remove_last_lane`'s single-lane check to every
# lane this connector carries.
#
# **NO un-remove exists for this verb.** There is no "add a connector back"
# counterpart in this module (reconstructing one would require re-deriving
# geometry -- ``fromLinkEndPt``/``toLinkEndPt`` positions, a ``<geometry>``
# polyline -- this module never captured on removal) --
# ``services.agent.rollback.rollback_last_edit`` is the ONLY undo path for
# this specific edit; state that fact in any planner-facing wrapper's own
# docstring too (see ``tools_edit.remove_connector``).
# ---------------------------------------------------------------------------


def _find_connector(root: ET.Element, connector_no: str) -> ET.Element:
    link = _find_link(root, connector_no)
    if link.find("fromLinkEndPt") is None and link.find("toLinkEndPt") is None:
        raise ValueError(
            f"remove_connector: link {connector_no!r} is not a connector (no fromLinkEndPt/"
            "toLinkEndPt child) -- structural connector removal only; a plain link has no "
            "COM/XML remove path in this codebase (see the module docstring: conflict areas "
            "are geometry-derived, lane closure is attribute-only, and neither a plain link "
            "nor a connector has a native COM Add/Remove)."
        )
    return link


def remove_connector(
    inpx_path: Path | str, connector_no: str, *, run_load_check: bool = True
) -> dict[str, Any]:
    """Structurally remove connector ``connector_no`` -- the true "movement
    removal" verb (see this section's own module comment for the full
    reference-integrity story and why NO un-remove exists for this edit;
    ``rollback_last_edit`` is the only undo path).

    Raises :class:`ValueError` when ``connector_no`` does not exist, or
    exists but is not genuinely a connector (a plain link is refused here,
    not silently "removed" some other way -- there is no COM/XML remove
    path for a plain link in this codebase at all). Raises
    :class:`ReferenceIntegrityError` (never touches the file) naming every
    route / priority-rule / conflict-marker / conflict-area / other-connector
    still referencing this connector or one of its own lanes.
    """
    root = ET.parse(inpx_path).getroot()
    connector = _find_connector(root, connector_no)

    referrers = find_id_references(root, "links", str(connector_no))
    lanes_el = connector.find("lanes")
    lane_count = len(lanes_el.findall("lane")) if lanes_el is not None else 0
    for lane_index in range(1, lane_count + 1):
        referrers.extend(find_lane_token_references(root, str(connector_no), lane_index))
    if referrers:
        raise ReferenceIntegrityError(f"Cannot remove connector {connector_no}", referrers)

    def _mutate(root: ET.Element) -> dict[str, Any]:
        links_el = root.find(".//links")
        target = _find_connector(root, connector_no)
        links_el.remove(target)
        return {"connector_no": str(connector_no), "removed_lane_count": lane_count}

    def _expect(shadow: dict[str, Any]) -> bool:
        return not any(str(l.get("id", "")) == str(connector_no) for l in shadow["links"])

    return apply_structural_edit(
        inpx_path,
        _mutate,
        reparse_expect=_expect,
        reparse_expect_description=f"connector {connector_no} should no longer exist",
        run_load_check=run_load_check,
    )
