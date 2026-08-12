"""``services/genesis/side_inputs.py`` — the DATA a caller adds to a generation.

The unit's claim is that there is ONE entrance for the optional layers, that it
refuses a document it cannot use BEFORE handing out a token, and that it guesses
neither of the two joins the layer modules refuse to guess (site→leg→link, and
approach→phase). So the tests are mostly refusals — one per way a real upload
goes wrong — plus the two plan routes and the one thing that must never happen:
a key nobody recognises being silently ignored.

The format under test is deliberately not a new one: the payload is
``services.dataimport``'s neutral ``ImportResult``, verbatim, exactly as an
adapter's ``model_dump(mode="json")`` produces it. So the fixtures here build
real neutral records rather than hand-written JSON shapes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.dataimport.base import IssueSeverity
from services.dataimport.schema import (
    PhaseData,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalProgramData,
)
from services.genesis import side_inputs as si
from services.genesis.compose import compose_layers

from tools.genesis_fullstack_live_check import (
    APPROACH_PHASES,
    LEG_OF_LINK,
    SITE,
    _counts_result,
    _crossroads_draft,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "daejeon_signal"

APPROACH_LINKS = {SITE: {LEG_OF_LINK[link_id]: link_id for link_id in LEG_OF_LINK}}


# ---------------------------------------------------------------------------
# Fixtures — real neutral records, serialised the way an adapter would
# ---------------------------------------------------------------------------


def demand_document(**overrides) -> dict:
    document = {
        "kind": si.DEMAND_DOC_KIND,
        "counts": _counts_result().model_dump(mode="json"),
        "approach_links": {SITE: {leg: link for link, leg in LEG_OF_LINK.items()}},
        "demand_settings": {"single_interval_reduction": "peak"},
    }
    document.update(overrides)
    return document


def observed_history_controller(
    controller_id: str = "TCS999", source_kind: str = "observed_history"
) -> SignalControllerData:
    """The same 10-cycle A.2 history the full-stack live check converts."""
    prov = Provenance(source_file="scats-history.csv", locator="row 1")

    def phase(phase_id, seq, total_s, min_green_s, all_red_s, stretch=False):
        return PhaseData(
            provenance=prov,
            phase_id=phase_id,
            sequence_index=seq,
            amber_s=3.0,
            all_red_s=all_red_s,
            is_stretch_phase=stretch,
            observation_count=10,
            observed_total_duration_s=total_s,
            min_green_s=min_green_s,
        )

    program = SignalProgramData(
        provenance=prov,
        program_id="P1",
        source_kind=ProgramSourceKind(source_kind),
        observed_cycle_count=10,
        phases=[
            phase("A", 0, 410.0, 10.0, 3.0, stretch=True),
            phase("B", 1, 260.0, 8.0, 2.0),
            phase("C", 2, 110.0, 5.0, 2.0),
        ],
    )
    return SignalControllerData(
        provenance=prov, controller_id=controller_id, programs=[program]
    )


def signals_document(**overrides) -> dict:
    result = _counts_result()
    result.controllers.append(observed_history_controller())
    document = {
        "kind": si.SIGNALS_DOC_KIND,
        "controllers": result.model_dump(mode="json"),
        "assignments": [
            {
                "node_id": 1,
                "plan": {"source": "observed_history", "controller_id": "TCS999"},
                "approach_phases": {str(k): v for k, v in APPROACH_PHASES.items()},
            }
        ],
    }
    document.update(overrides)
    return document


def as_bytes(document: dict) -> bytes:
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def parse(kind: str, document: dict):
    return si.parse_side_input(kind, as_bytes(document))


def refusal(kind: str, document: dict) -> si.SideInputRefused:
    with pytest.raises(si.SideInputRefused) as caught:
        parse(kind, document)
    return caught.value


# ---------------------------------------------------------------------------
# §1 A document that works
# ---------------------------------------------------------------------------


def test_a_demand_document_parses_and_counts_what_it_carries():
    """The staging response is COUNTED from the document, so "the counts I sent"
    and "the counts that arrived" are comparable before a generation is spent."""
    document = parse(si.KIND_DEMAND, demand_document())
    summary = document.summary()

    assert summary["doc_kind"] == si.DEMAND_DOC_KIND
    assert summary["counts"] == len(_counts_result().counts)
    assert summary["sites_mapped"] == [SITE]
    assert summary["approaches_mapped"] == 4
    assert summary["vehicle_classes"] == sorted(
        {c.vehicle_class.code for c in _counts_result().counts}
    )
    assert summary["counts_refusals"] == []
    assert document.window() is None


def test_a_signals_document_parses_its_assignments():
    document = parse(si.KIND_SIGNALS, signals_document())
    summary = document.summary()

    assert summary["controllers_in_document"] == ["TCS999"]
    assert summary["assignments"] == [
        {"node_id": 1, "plan_source": "observed_history", "approaches": 4, "phases": ["A", "B", "C"]}
    ]
    # Keys arrived as JSON strings and are draft LINK IDS on the way out.
    assert set(document.assignments[0].approach_phases) == set(APPROACH_PHASES)


def test_a_window_is_carried_as_a_pair_not_two_loose_fields():
    document = parse(
        si.KIND_DEMAND,
        demand_document(window_start="2026-04-08T07:00:00", window_end="2026-04-08T08:00:00"),
    )
    start, end = document.window()
    assert (start.hour, end.hour) == (7, 8)
    assert document.summary()["window"]["start"].startswith("2026-04-08T07:00")


# ---------------------------------------------------------------------------
# §2 Structural refusals — every one of them before a token exists
# ---------------------------------------------------------------------------


def test_bytes_that_are_not_json_are_refused():
    with pytest.raises(si.SideInputRefused) as caught:
        si.parse_side_input(si.KIND_DEMAND, b"{not json")
    assert caught.value.code == "genesis_side_input_not_json"


def test_a_json_document_that_is_not_an_object_is_refused():
    with pytest.raises(si.SideInputRefused) as caught:
        si.parse_side_input(si.KIND_DEMAND, b"[1, 2, 3]")
    assert caught.value.code == "genesis_side_input_not_json"
    assert "list" in caught.value.reasons[0]


def test_empty_bytes_are_refused():
    with pytest.raises(si.SideInputRefused) as caught:
        si.parse_side_input(si.KIND_DEMAND, b"   \n")
    assert caught.value.code == "genesis_side_input_empty"


def test_an_unknown_kind_is_refused_and_names_the_known_ones():
    with pytest.raises(si.SideInputRefused) as caught:
        si.parse_side_input("fleet", b"{}")
    assert caught.value.code == "genesis_side_input_unknown_kind"
    assert "demand" in caught.value.reasons[0] and "signals" in caught.value.reasons[0]


def test_a_document_over_the_cap_is_refused_with_the_number():
    oversized = b'{"kind": "genesis.demand.v1", ' + b" " * si.MAX_SIDE_INPUT_BYTES + b"}"
    with pytest.raises(si.SideInputRefused) as caught:
        si.parse_side_input(si.KIND_DEMAND, oversized)
    assert caught.value.code == "genesis_side_input_too_large"
    assert str(si.MAX_SIDE_INPUT_BYTES) in caught.value.reasons[0]


def test_an_unrecognised_key_is_a_refusal_not_a_silently_ignored_field():
    """``extra="forbid"`` everywhere. A typo in a key that is quietly dropped is
    a setting the caller believes they set and did not — the silent drop in its
    smallest form."""
    caught = refusal(si.KIND_DEMAND, demand_document(aproach_links={"S": {"N": 1}}))
    assert caught.code == "genesis_side_input_invalid_demand"
    assert any("aproach_links" in reason for reason in caught.reasons)


def test_an_empty_approach_link_map_is_refused():
    caught = refusal(si.KIND_DEMAND, demand_document(approach_links={}))
    assert any("approach_links" in reason for reason in caught.reasons)


def test_a_site_that_maps_no_approach_is_refused():
    caught = refusal(si.KIND_DEMAND, demand_document(approach_links={SITE: {}}))
    assert any("no approach" in reason for reason in caught.reasons)


def test_half_a_window_is_refused():
    caught = refusal(si.KIND_DEMAND, demand_document(window_start="2026-04-08T07:00:00"))
    assert any("window_start and window_end" in reason for reason in caught.reasons)


def test_a_window_that_ends_before_it_starts_is_refused():
    caught = refusal(
        si.KIND_DEMAND,
        demand_document(window_start="2026-04-08T08:00:00", window_end="2026-04-08T07:00:00"),
    )
    assert any("not after" in reason for reason in caught.reasons)


def test_an_approach_phase_key_that_is_not_a_link_id_is_refused():
    """A label here would match no approach and the phase would be silently
    unused — ``generate_node_signals`` keys on ``int``."""
    document = signals_document()
    document["assignments"][0]["approach_phases"] = {"북측 접근": "A"}
    caught = refusal(si.KIND_SIGNALS, document)
    assert any("is not a draft link id" in reason for reason in caught.reasons)


def test_an_empty_approach_phase_mapping_is_refused():
    document = signals_document()
    document["assignments"][0]["approach_phases"] = {}
    caught = refusal(si.KIND_SIGNALS, document)
    assert any("approach_phases is empty" in reason for reason in caught.reasons)


def test_one_node_assigned_twice_is_refused():
    document = signals_document()
    document["assignments"].append(dict(document["assignments"][0]))
    caught = refusal(si.KIND_SIGNALS, document)
    assert any("assigned twice" in reason for reason in caught.reasons)


def test_an_observed_history_assignment_without_controllers_is_refused():
    document = signals_document()
    document["controllers"] = None
    caught = refusal(si.KIND_SIGNALS, document)
    assert any("no 'controllers'" in reason for reason in caught.reasons)


def test_no_assignments_at_all_is_refused():
    caught = refusal(si.KIND_SIGNALS, signals_document(assignments=[]))
    assert caught.code == "genesis_side_input_invalid_signals"


def test_a_malformed_neutral_record_is_refused_with_its_path():
    """The neutral model does the validating; this reports WHERE it failed, so a
    30,000-record document does not come back as one unreadable blob."""
    document = demand_document()
    document["counts"]["counts"][0]["volume_unit"] = "furlongs"
    caught = refusal(si.KIND_DEMAND, document)
    assert any("counts.counts.0.volume_unit" in reason for reason in caught.reasons)


def test_a_long_error_list_says_how_many_it_omitted():
    """A truncated error list that does not admit it is truncated is the silent
    drop in miniature."""
    document = demand_document()
    for row in document["counts"]["counts"]:
        row["volume_unit"] = "furlongs"
    caught = refusal(si.KIND_DEMAND, document)
    assert len(caught.reasons) == 21
    assert "further validation error" in caught.reasons[-1]


# ---------------------------------------------------------------------------
# §3 The two plan routes
# ---------------------------------------------------------------------------


def test_the_observed_history_route_builds_the_a2_plan():
    """1.6's converter is what produces the plan — cycle 78 / greens 35-21-6 come
    out of ``convert_to_fixed_time``, not out of this document."""
    build = si.build_signal_assignments(parse(si.KIND_SIGNALS, signals_document()))

    assert build.ok, build.refusal_reasons()
    plan, mapping = build.assignments[1]
    assert plan.plan_cycle_s == 78.0
    assert [row.green_s for row in plan.rows] == [35.0, 21.0, 6.0]
    assert mapping == APPROACH_PHASES


def test_a_controller_the_document_does_not_carry_refuses():
    document = signals_document()
    document["assignments"][0]["plan"]["controller_id"] = "TCS000"
    build = si.build_signal_assignments(parse(si.KIND_SIGNALS, document))

    assert not build.ok
    assert build.assignments == {}
    assert "genesis_side_input_controller_not_found" in {i.code for i in build.refusals}


def test_a_program_the_controller_does_not_carry_refuses():
    document = signals_document()
    document["assignments"][0]["plan"]["program_id"] = "P9"
    build = si.build_signal_assignments(parse(si.KIND_SIGNALS, document))

    assert not build.ok
    assert "genesis_side_input_program_not_found" in {i.code for i in build.refusals}


def test_a_declared_program_is_refused_by_1_6_not_converted_anyway():
    """``convert_to_fixed_time``'s own rule: A.2 averages OBSERVATIONS, and
    converting a declared plan would relabel a fact as an estimate."""
    result = _counts_result()
    result.controllers.append(
        observed_history_controller(source_kind="declared")
    )
    document = signals_document(controllers=result.model_dump(mode="json"))
    build = si.build_signal_assignments(parse(si.KIND_SIGNALS, document))

    assert not build.ok
    assert "fixed_time_no_observed_program" in {i.code for i in build.refusals}
    assert build.assignments == {}


def test_the_daejeon_route_refuses_without_a_declared_amber():
    """The 대전 sheet prints ``황색+전적색`` combined and never the amber alone.
    Omitting the declaration produces no plan — it never defaults to 3.0 s."""
    document = signals_document(
        controllers=None,
        assignments=[
            {
                "node_id": 1,
                "plan": {
                    "source": "daejeon_sheet",
                    "sheet_text": (FIXTURES / "clean_five_phase.txt").read_text(encoding="utf-8"),
                    "source_file": "clean_five_phase.txt",
                    "level_no": 1,
                    "plan_id": 1,
                },
                "approach_phases": {"1": "Φ1", "3": "Φ2", "5": "Φ3", "7": "Φ4"},
            }
        ],
    )
    build = si.build_signal_assignments(parse(si.KIND_SIGNALS, document))

    assert not build.ok
    assert "daejeon_intergreen_split_unstated" in {i.code for i in build.refusals}
    assert build.assignments == {}


def test_the_daejeon_route_builds_a_plan_once_a_reviewer_declares_the_amber():
    """With the declaration the sheet's own arithmetic produces the plan, and
    the declaration travels into the program's provenance."""
    document = signals_document(
        controllers=None,
        assignments=[
            {
                "node_id": 1,
                "plan": {
                    "source": "daejeon_sheet",
                    "sheet_text": (FIXTURES / "clean_five_phase.txt").read_text(encoding="utf-8"),
                    "source_file": "clean_five_phase.txt",
                    "level_no": 1,
                    "plan_id": 1,
                    "declared_amber_s": 3.0,
                    "amber_declaration_note": "test fixture declaration",
                },
                "approach_phases": {"1": "Φ1", "3": "Φ2", "5": "Φ3", "7": "Φ4"},
            }
        ],
    )
    build = si.build_signal_assignments(parse(si.KIND_SIGNALS, document))

    assert build.ok, build.refusal_reasons()
    plan, _mapping = build.assignments[1]
    assert plan.plan_cycle_s == 120.0
    assert plan.controller_id == "9001"
    assert all(
        "reviewer declaration" in phase.provenance.derived_by for phase in plan.program.phases
    )


# ---------------------------------------------------------------------------
# §4 Documents → composed layers
# ---------------------------------------------------------------------------


def test_compose_from_documents_matches_composing_from_the_raw_inputs():
    """The document seam adds a parse and nothing else: the composition it
    produces must be the one ``compose_layers`` produces from the same data."""
    draft = _crossroads_draft()
    from services.genesis.demand import DemandSettings, REDUCTION_PEAK
    from services.genesis.emit import to_xml_text

    via_documents = si.compose_from_documents(
        draft,
        demand=parse(si.KIND_DEMAND, demand_document()),
        signals=parse(si.KIND_SIGNALS, signals_document()),
    )
    plan, mapping = si.build_signal_assignments(
        parse(si.KIND_SIGNALS, signals_document())
    ).assignments[1]
    direct = compose_layers(
        draft,
        counts=_counts_result(),
        approach_links=APPROACH_LINKS,
        demand_settings=DemandSettings(single_interval_reduction=REDUCTION_PEAK),
        signal_assignments={1: (plan, mapping)},
    )

    assert via_documents.ok, via_documents.refusal_reasons()
    assert to_xml_text(
        draft, via_documents.signals, via_documents.demand, via_documents.fleet
    ) == to_xml_text(draft, direct.signals, direct.demand, direct.fleet)


def test_a_plan_that_could_not_be_built_refuses_the_whole_composition():
    """A node whose plan failed to convert is absent from the assignments — and
    the composition must refuse rather than emit a network with one
    intersection quietly unsignalised."""
    draft = _crossroads_draft()
    document = signals_document()
    document["assignments"][0]["plan"]["controller_id"] = "TCS000"

    composed = si.compose_from_documents(
        draft, signals=parse(si.KIND_SIGNALS, document)
    )

    assert not composed.ok
    codes = composed.issue_codes(IssueSeverity.REFUSE)
    assert "genesis_side_input_controller_not_found" in codes
    assert composed.signals is not None and not composed.signals.emitting


def test_load_side_input_reads_a_file_the_server_stored(tmp_path: Path):
    path = tmp_path / "demand.json"
    path.write_bytes(as_bytes(demand_document()))
    assert si.load_side_input(si.KIND_DEMAND, path).approach_links == APPROACH_LINKS
