"""``services/genesis/compose.py`` — the ONE order the optional layers compose in.

This module was EXTRACTED, not written: the ordering (fleet → demand off the
fleet's own map → signals) is the one
``tools/genesis_fullstack_live_check.py`` used for the four-layer file a real
PTV Vissim 2026 SP09 opened (``445477d``). So the first test here is not about
behaviour at all — it is about the extraction being an extraction: the same
draft and the same inputs must produce the same BYTES through
:func:`compose_layers` as through the inline sequence the live check ran. If
that ever stops being true, the thing that was measured live and the thing the
product ships have quietly become two different networks.

The rest of the file is the three guarantees no individual layer module can
give, because each of them only knows about itself:

* the fleet→demand dependency (a composition whose ``vehType`` references would
  dangle is refused, never emitted — Vissim drops dangling references silently
  and reports a successful load);
* a layer the CALLER ASKED FOR that produced nothing is a refusal, not an
  omission;
* a mapped APPROACH that seeded nothing is a refusal too — the sub-layer case
  a whole-layer emptiness check cannot see, because a healthy site's nonzero
  inputs keep the layer looking full while one caller-mapped approach on
  another site never reached a single Vissim object.

No COM, no Vissim, no network access — everything here runs on the synthetic
crossroads the three layer suites already use.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.dataimport.base import ImportResult, IssueSeverity
from services.dataimport.schema import (
    ApproachRef,
    MovementRef,
    Provenance,
    TurningCountData,
    TurnType,
    VolumeUnit,
)
from services.genesis.compose import (
    CODE_APPROACH_NOT_SEEDED,
    CODE_DEMAND_WITHOUT_FLEET,
    CODE_LAYER_EMPTY,
    ComposedLayers,
    compose_layers,
)
from services.genesis.demand import DemandSettings, REDUCTION_PEAK, generate_demand
from services.genesis.draft import DraftNetwork, DraftNode
from services.genesis.emit import emit_inpx, to_xml_text
from services.genesis.fleet import DraftFleet, build_fleet, observed_vehicle_classes
from services.genesis.signals import generate_signals

from tests.test_genesis_demand import (
    ADAPTER as TEE_ADAPTER,
    APPROACH_LENGTH_M as TEE_APPROACH_LENGTH_M,
    LEG_NORTH,
    SITE as TEE_SITE,
    WEDNESDAY,
    _connector as _tee_connector,
    _count as _tee_count,
    _link as _tee_link,
)
from tools.genesis_fullstack_live_check import (
    APPROACH_PHASES,
    LEG_OF_LINK,
    SITE,
    _counts_result,
    _crossroads_draft,
    _observed_classes,
    build_a2_plan,
)

APPROACH_LINKS = {SITE: {LEG_OF_LINK[link_id]: link_id for link_id in LEG_OF_LINK}}
PEAK = DemandSettings(single_interval_reduction=REDUCTION_PEAK)

#: ``_crossroads_draft()``'s own exit for each of its four entries — connector
#: 1->2, 3->4, 5->6, 7->8 in ``_crossroads_draft``. Each exit link is NOT an
#: entry (a connector arrives on it) and has NO departing connector of its own
#: (the draft is one hop deep), so mapping an approach to one is exactly the
#: "neither input nor routing" shape the CRITICAL fix refuses.
EXIT_OF_ENTRY = {1: 2, 3: 4, 5: 6, 7: 8}


@pytest.fixture()
def draft():
    built = _crossroads_draft()
    assert not built.refused, built.refusals
    return built


def _compose(draft, **overrides) -> ComposedLayers:
    kwargs: dict = {
        "counts": _counts_result(),
        "approach_links": APPROACH_LINKS,
        "demand_settings": PEAK,
        "signal_assignments": {draft.nodes[0].id: (build_a2_plan(), APPROACH_PHASES)},
    }
    kwargs.update(overrides)
    return compose_layers(draft, **kwargs)


# ---------------------------------------------------------------------------
# §1 The extraction IS an extraction
# ---------------------------------------------------------------------------


def test_composed_layers_emit_the_same_bytes_as_the_certified_inline_sequence(draft):
    """The proof that this module moved code rather than rewrote it.

    The right-hand side is the sequence ``genesis_fullstack_live_check.
    build_fullstack_network`` ran when the four-layer file was opened live:
    ``build_fleet`` over the counts' observed classes, ``generate_demand`` with
    ``vehicle_types=dict(fleet.map)``, ``generate_signals`` over the node's
    (plan, approach→phase) pair. Written out here in full — not imported —
    because a test that called the extracted function twice would prove
    nothing.
    """
    counts = _counts_result()
    plan = build_a2_plan()

    # -- the inline sequence, verbatim ------------------------------------
    inline_fleet = build_fleet(observed_vehicle_classes(counts))
    inline_demand = generate_demand(
        draft,
        counts,
        approach_links=APPROACH_LINKS,
        vehicle_types=dict(inline_fleet.map),
        settings=PEAK,
    )
    inline_signals = generate_signals(draft, {draft.nodes[0].id: (plan, APPROACH_PHASES)})
    inline_xml = to_xml_text(draft, inline_signals, inline_demand, inline_fleet)

    # -- the extracted one -------------------------------------------------
    layers = _compose(draft)
    composed_xml = to_xml_text(draft, layers.signals, layers.demand, layers.fleet)

    assert layers.ok, layers.issue_codes(IssueSeverity.REFUSE)
    assert composed_xml == inline_xml
    # …and the file really does carry all four layers, so the comparison above
    # is not two identical geometry-only files agreeing about nothing.
    for container in (
        "<vehicleTypes",
        "<vehicleInputs",
        "<vehicleCompositions",
        "<signalControllers",
        "<signalHeads",
    ):
        assert container in composed_xml, container


def test_a_composition_nobody_asked_for_changes_no_byte(draft, tmp_path: Path):
    """``compose_layers`` with no inputs must be indistinguishable from never
    having called it — that is what lets the job runner hand its result to the
    emitter unconditionally."""
    empty = compose_layers(draft)

    assert empty.ok
    assert (empty.fleet, empty.demand, empty.signals) == (None, None, None)
    assert empty.requested == ()
    assert not empty.emitting

    with_layers = emit_inpx(draft, tmp_path / "a.inpx", empty.signals, empty.demand, empty.fleet)
    without = emit_inpx(draft, tmp_path / "b.inpx")
    assert with_layers.read_bytes() == without.read_bytes()


def test_the_summary_counts_what_reached_the_file(draft):
    """A reader of a job record must be able to tell "the demand layer ran" from
    "the demand layer produced four inputs" without opening the artifact."""
    summary = _compose(draft).summary()

    assert sorted(summary["requested"]) == ["demand", "fleet", "signals"]
    assert summary["fleet"]["emitted"] is True
    assert summary["fleet"]["type_nos"] == [1, 3, 30]
    assert summary["demand"]["vehicle_inputs"] == 4
    assert summary["demand"]["compositions"] == 1
    assert summary["signals"]["controllers"] == 1
    assert summary["signals"]["signal_groups"] == 4
    assert summary["compose_refusals"] == []


def test_the_full_report_carries_every_finding_and_every_compliance_item(draft):
    """``layers.json``'s contract: nothing truncated. The compact summary is
    what crosses HTTP; this is the audit copy, and a §5 item missing from it is
    the silent omission the compliance report exists to prevent."""
    layers = _compose(draft)
    payload = layers.as_dict("ko")

    assert len(payload["issues"]) == len(layers.all_issues)
    assert {item["layer"] for item in payload["compliance"]} == {"fleet", "demand", "signals"}
    assert all(item["rule_id"] for item in payload["compliance"])
    assert payload["coverage"]["demand"]["sites_joined"] == [SITE]
    # Warnings ride along rather than being dropped for looking untidy: the
    # inverted Table 5-2 envelope is a real MRWA document property.
    assert "genesis_fleet_table_5_2_envelope_inverted" in {
        issue["code"] for issue in payload["issues"]
    }


# ---------------------------------------------------------------------------
# §2 The fleet→demand dependency
# ---------------------------------------------------------------------------


def test_a_demand_that_would_dangle_its_vehicle_types_is_refused(
    draft, monkeypatch: pytest.MonkeyPatch
):
    """The guard against emitting compositions with no types to point at.

    Today's ``build_fleet`` cannot produce this state — it fills ``types`` and
    ``map`` together — so the fleet is replaced with one that maps classes and
    declares nothing. That is deliberate: the rule exists to survive a future
    change to ``build_fleet``, or a caller composing by hand, and a rule that
    is only true by accident of another module's current behaviour is not a
    rule. Nothing else is stubbed; the demand this refuses is the real one.
    """
    real_build_fleet = build_fleet

    def typeless_fleet(classes, *, settings=None):
        built = real_build_fleet(classes, settings=settings)
        return DraftFleet(
            types=(),
            map=built.map,
            canonical_map=built.canonical_map,
            speed_distr_no=built.speed_distr_no,
            issues=(),
            compliance=built.compliance,
            coverage=built.coverage,
            settings=built.settings,
        )

    monkeypatch.setattr("services.genesis.compose.build_fleet", typeless_fleet)
    layers = _compose(draft, signal_assignments=None)

    assert not layers.ok
    assert CODE_DEMAND_WITHOUT_FLEET in layers.issue_codes(IssueSeverity.REFUSE)
    assert layers.demand is not None and layers.demand.emitting
    reason = layers.refusal_reasons("ko")[0]
    assert "vehType" in reason or "차종" in reason


def test_the_cross_check_is_the_fleet_modules_own(draft, monkeypatch: pytest.MonkeyPatch):
    """A composition target the fleet does not declare refuses through
    ``DraftFleet.check_composition_targets`` — the fleet module's function, not
    a second copy of the rule living here."""
    calls: list[str] = []
    real_build_fleet = build_fleet

    def spying_fleet(classes, *, settings=None):
        built = real_build_fleet(classes, settings=settings)
        original = built.check_composition_targets

        def spy(demand):
            calls.append("checked")
            return original(demand)

        object.__setattr__(built, "check_composition_targets", spy)
        return built

    monkeypatch.setattr("services.genesis.compose.build_fleet", spying_fleet)
    layers = _compose(draft, signal_assignments=None)

    assert layers.ok
    assert calls == ["checked"], "the fleet's own cross-check was not run over the demand"


# ---------------------------------------------------------------------------
# §3 A requested layer that produced nothing
# ---------------------------------------------------------------------------


def test_a_requested_signal_layer_with_no_assignments_refuses(draft):
    """``{}`` is not ``None``. A caller who supplied a signals document that
    produced no assignment asked for signals and got none, and the emission
    must not proceed as though they had asked for geometry."""
    layers = compose_layers(draft, signal_assignments={})

    assert not layers.ok
    assert CODE_LAYER_EMPTY in layers.issue_codes(IssueSeverity.REFUSE)
    assert "signals" in layers.requested
    assert "신호" in layers.refusal_reasons("ko")[0]


def test_a_plan_assigned_to_a_node_outside_the_draft_refuses(draft):
    """The signals module reports it (``signals_node_not_in_draft``); this
    module's job is that the report BLOCKS rather than riding along."""
    layers = compose_layers(
        draft, signal_assignments={99999: (build_a2_plan(), APPROACH_PHASES)}
    )

    assert not layers.ok
    assert "signals_node_not_in_draft" in layers.issue_codes(IssueSeverity.REFUSE)


def test_demand_without_the_approach_join_refuses_and_names_the_sites(draft):
    """``generate_demand``'s own refusal, carried out through the composition.
    A count site id and a draft link id are different id systems, and neither
    module guesses the join."""
    layers = compose_layers(draft, counts=_counts_result(), approach_links=None)

    assert not layers.ok
    assert "genesis_demand_no_approach_link_map" in layers.issue_codes(IssueSeverity.REFUSE)
    assert SITE in layers.refusal_reasons("ko")[0]


def test_refusal_reasons_are_korean_and_come_from_the_layers_themselves(draft):
    """ko primary. The layer modules author these sentences; ``compose.py``
    renders them and never writes a replacement."""
    layers = compose_layers(draft, counts=_counts_result(), approach_links=None)
    ko = layers.refusal_reasons("ko")
    en = layers.refusal_reasons("en")

    assert ko and en and ko != en
    assert any("가" <= ch <= "힣" for ch in ko[0]), ko[0]


def test_layers_that_refused_never_report_as_emitting(draft):
    """A refused composition must be unusable, not merely flagged: the emitter
    treats a refused layer as empty, so a caller that ignored ``ok`` would ship
    a network silently missing what it asked for."""
    layers = compose_layers(draft, counts=_counts_result(), approach_links=None)

    assert not layers.ok
    assert layers.demand is not None and not layers.demand.emitting
    assert layers.summary()["demand"]["emitted"] is False


# ---------------------------------------------------------------------------
# §4 A mapped approach that seeds nothing is a refusal, too — the CRITICAL fix
# ---------------------------------------------------------------------------
#
# Reproduced (pre-fix, verified by hand against the shipped code before this
# guard existed): a single site mapped entirely to non-entry links passed with
# ``layers.ok: True``, 0 vehicle inputs, 1 orphaned composition; a two-site
# document with one healthy and one orphaned site passed with
# ``compose_refusals: []`` and the REAL gate reporting V6 PASS. Both are the
# same failure ``genesis_jobs.py``'s own docstring names above every other one:
# a caller who supplied counts got back a network with their demand silently
# dropped.


def _broken_approach_links() -> dict:
    """Every leg of ``SITE`` mapped to its ENTRY'S OWN EXIT, not the entry
    itself — a link that genuinely exists in the draft (so none of the
    pre-existing unknown-site/-approach/-link checks fire and ``demand.ok``
    stays True) but is neither an entry (no vehicle input) nor has a departing
    connector of its own (no routing decision either)."""
    return {SITE: {leg: EXIT_OF_ENTRY[entry] for entry, leg in LEG_OF_LINK.items()}}


def test_a_site_mapped_entirely_to_non_entry_links_refuses_and_names_it(draft):
    """CRITICAL repro (1). Pre-fix this produced ``layers.ok: True`` with 0
    vehicle inputs and 1 composition — a network that emits and gates as if
    seeded while carrying none of the site's demand."""
    broken = _broken_approach_links()
    layers = compose_layers(
        draft,
        counts=_counts_result(),
        approach_links=broken,
        demand_settings=PEAK,
        signal_assignments=None,
    )

    assert not layers.ok
    assert CODE_APPROACH_NOT_SEEDED in layers.issue_codes(IssueSeverity.REFUSE)
    # the composition still exists (it does not depend on the join) — the
    # refusal is specifically about the join producing no input, not about
    # the layer being empty outright (that is CODE_LAYER_EMPTY, a different
    # code, and would mask this one's more specific reason).
    assert layers.demand is not None
    assert len(layers.demand.inputs) == 0
    assert len(layers.demand.compositions) == 1

    ko = " ".join(layers.refusal_reasons("ko"))
    assert SITE in ko
    for leg, link_id in broken[SITE].items():
        assert leg in ko, ko
        assert f"link {link_id}" in ko, ko


def test_a_healthy_site_does_not_mask_an_orphaned_sites_drop(draft):
    """CRITICAL repro (2). A second site whose every approach lands on a
    non-entry, non-departing link must refuse the WHOLE composition even
    though the first site's inputs are all present and correct — a per-site
    emptiness check would have missed this because ``compose_refusals`` and
    ``layers.ok`` both read clean pre-fix, and the real gate's V6 reported
    PASS on the emitted file."""
    site_b = "다른네거리"
    legs_b = {2: "leg2", 4: "leg4", 6: "leg6", 8: "leg8"}
    car, truck, bus = _observed_classes()
    prov = Provenance(source_file="counts.xlsx", locator="Sheet1!row 4")
    base = datetime(2026, 4, 8, 7, 0)
    rows = []
    for link_id, leg in legs_b.items():
        for index in range(4):
            start = base + timedelta(minutes=15 * index)
            for vehicle_class, volume in ((car, 30), (truck, 10), (bus, 5)):
                rows.append(
                    TurningCountData(
                        provenance=prov,
                        site_id=site_b,
                        approach=ApproachRef(leg_id=leg),
                        movement=MovementRef(turn=TurnType.THROUGH),
                        vehicle_class=vehicle_class,
                        interval_start=start,
                        interval_end=start + timedelta(minutes=15),
                        volume=volume,
                        volume_unit=VolumeUnit.VEHICLES,
                    )
                )
    healthy = _counts_result()
    combined = ImportResult(adapter=healthy.adapter, counts=list(healthy.counts) + rows)
    approach_links = {**APPROACH_LINKS, site_b: {leg: link_id for link_id, leg in legs_b.items()}}

    layers = compose_layers(
        draft,
        counts=combined,
        approach_links=approach_links,
        demand_settings=PEAK,
        signal_assignments=None,
    )

    assert not layers.ok
    assert CODE_APPROACH_NOT_SEEDED in layers.issue_codes(IssueSeverity.REFUSE)
    # the healthy site's inputs are NOT masked by the refusal -- they are
    # still exactly what a correct mapping would have produced.
    assert layers.demand is not None
    assert {plan.site_id for plan in layers.demand.inputs} == {SITE}
    assert len(layers.demand.inputs) == 4
    # and the refusal names the ORPHANED site, not the healthy one, hiding
    # nothing behind the other site's success.
    ko = " ".join(layers.refusal_reasons("ko"))
    assert site_b in ko


def test_a_correctly_mapped_composition_never_trips_the_new_guard(draft):
    """Required guarantee #3: a correct mapping is unaffected. Every approach
    of ``SITE`` maps to its own entry link, so every one of them is seeded
    either by an input, a routing decision, or (here) both — the guard from
    §4 must find nothing to refuse."""
    layers = _compose(draft)

    assert layers.ok, layers.refusal_reasons("ko")
    assert CODE_APPROACH_NOT_SEEDED not in layers.issue_codes(IssueSeverity.REFUSE)
    assert layers.demand is not None
    assert len(layers.demand.inputs) == 4


def test_an_already_refused_demand_is_not_double_refused_by_the_seeding_guard(draft):
    """The ``if demand.ok:`` gate in ``compose_layers`` (re-verify residual):
    a demand document that already refused for its own, more specific reason —
    here a mapping that names a link id the draft does not contain — must
    surface THAT refusal alone. Piling ``CODE_APPROACH_NOT_SEEDED`` on top
    would bury the actual cause under a second, derived symptom of the same
    mistake."""
    broken = {SITE: {leg: 9999 for leg in LEG_OF_LINK.values()}}
    layers = compose_layers(
        draft,
        counts=_counts_result(),
        approach_links=broken,
        demand_settings=PEAK,
        signal_assignments=None,
    )

    assert not layers.ok
    refuse = layers.issue_codes(IssueSeverity.REFUSE)
    assert CODE_APPROACH_NOT_SEEDED not in refuse, refuse
    assert layers.demand is not None
    assert not layers.demand.ok
    # the demand layer's own, more specific refusal is what the caller reads
    assert any(code.startswith("genesis_demand_") for code in refuse), refuse


def test_an_interior_approach_with_routing_only_is_not_mistaken_for_a_drop():
    """The guard's necessary discrimination, proven through ``compose_layers``
    itself rather than only through ``demand.py``'s own suite: a mapped link
    that is NOT an entry but DOES have a departing connector gets a routing
    decision and no vehicle input — by design, because its traffic entered
    upstream (``demand.py``'s own ``_classify_seeded_links``). If the new
    guard used "no vehicle input" as its test instead of "neither input nor
    routing", THIS shape — already asserted legitimate by
    ``test_genesis_demand.py::test_an_interior_approach_gets_routing_but_no_vehicle_input``
    — would be refused by mistake. Extends the same T-junction fixture the
    same way that test does, and calls ``compose_layers`` instead of
    ``generate_demand`` directly so the guard itself is what is exercised.
    """
    links = [
        _tee_link(1, "North approach", [(0.0, TEE_APPROACH_LENGTH_M), (0.0, 0.0)], 101, 1),
        _tee_link(3, "South exit", [(0.0, 0.0), (0.0, -TEE_APPROACH_LENGTH_M)], 1, 103),
        _tee_link(5, "East exit", [(0.0, 0.0), (TEE_APPROACH_LENGTH_M, 0.0)], 1, 105),
    ]
    connectors = [
        _tee_connector(10001, 1, 3, "through", 0.0),
        _tee_connector(10002, 1, 5, "left", 90.0),
    ]
    node = DraftNode(
        id=1,
        name="n1",
        osm_id=1,
        x=0.0,
        y=0.0,
        polygon=[(-15.0, -15.0), (15.0, -15.0), (15.0, 15.0), (-15.0, 15.0)],
    )
    tee_draft = DraftNetwork(
        name="tee", source_osm="tee.osm", links=links, nodes=[node], connectors=connectors
    )
    extended = tee_draft.model_copy(
        update={
            "links": tee_draft.links
            + [_tee_link(7, "Far exit", [(0.0, -TEE_APPROACH_LENGTH_M), (0.0, -300.0)], 103, 107)],
            "connectors": tee_draft.connectors + [_tee_connector(10003, 3, 7, "through", 0.0)],
        }
    )

    rows = []
    for index in range(4):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        rows.append(_tee_count(start, turn=TurnType.THROUGH, volume=40))
        rows.append(_tee_count(start, turn=TurnType.LEFT, volume=50))
        rows.append(_tee_count(start, turn=TurnType.THROUGH, volume=40, leg="남측"))

    layers = compose_layers(
        extended,
        counts=ImportResult(adapter=TEE_ADAPTER, counts=rows),
        approach_links={TEE_SITE: {LEG_NORTH: 1, "남측": 3}},
        demand_settings=DemandSettings(single_interval_reduction=REDUCTION_PEAK),
        signal_assignments=None,
    )

    assert layers.ok, layers.refusal_reasons("ko")
    assert CODE_APPROACH_NOT_SEEDED not in layers.issue_codes(IssueSeverity.REFUSE)
    assert layers.demand is not None
    assert [plan.link_id for plan in layers.demand.inputs] == [1]
    assert sorted(seed.link_id for seed in layers.demand.decisions) == [1, 3]
