"""Tests for unit U3's anomaly catalogue (``services/agent/anomalies.py``).

Roadmap row 2.1 names four anomalies — ghost queue, blocked connector,
starved link, diffusion/latent demand > 1% — and this file pins what the
catalogue is allowed to claim about each of them.

Two things are tested harder than the rest, because they are the two ways
this unit could quietly become a lie:

1. **Coverage is derived, not tabulated.** Removing an indexed constraint
   from the LIVE registry must change the derived coverage and flip the
   anomaly's arming to ``DETECTOR_UNREGISTERED``. A test that only asserted
   "ghost_queue lists these two ids" would pass against a hardcoded table
   and would therefore have tested nothing.
2. **``ARMED_BUT_EMPTY`` never reads as clean.** Both shapes are covered:
   the in-process ``EmptyEvidence*`` marker, and the JSON-round-tripped form
   where the marker class is gone and only
   ``run_result["evidence"][table] == "ARMED_BUT_EMPTY"`` survives.

No COM anywhere. Every ``run_result`` in this file is produced by running
unit U1's real ``tools_run._collect_run_evidence`` over a real (tiny) sqlite
result database this file writes into ``tmp_path`` — the same synthetic
fixture shape ``test_agent_tools_run.py`` uses — so "genuinely fires" means
fires on evidence read out of a database, not on a hand-written dict.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import services.agent.anomalies as anomalies
import services.agent.tools_run as tools_run
import services.results_evidence as results_evidence
from services.agent import constraints
from services.agent.guardrails import GuardrailEngine

# Real movement OBJECT_ID shapes (node-from_link-from_pos-to_link-to_pos).
_MOVEMENT_BUSY = "1-508-100.779980-510-62.109716"
_MOVEMENT_GRIDLOCKED = "1-509-88.412000-511-40.550000"


# ---------------------------------------------------------------------------
# Synthetic fixtures: a real result .db + a real <evaluation> element
# ---------------------------------------------------------------------------


def _write_results_db(
    db_path: Path,
    *,
    movements: "list[tuple[str, float | None, float | None]] | None" = None,
    queues: "list[tuple[str, float | None, float | None]] | None" = None,
    movement_tables: bool = True,
) -> None:
    """A minimal result .db.

    ``movement_tables=False`` omits the two MOVEMENT tables entirely — the
    ``TABLE_ABSENT`` shape. Passing them as ``True`` with empty row lists
    creates them and leaves them empty — the ``ARMED_BUT_EMPTY`` shape.
    Collapsing those two is exactly what unit U1's descriptor refuses to do
    and what this catalogue must not undo.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER, SIMPERIOD REAL, "
            "SIMEND REAL, VISSIMVER TEXT)"
        )
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 42, 3600, 3600, '2026')")
        conn.execute(
            "CREATE TABLE VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass ("
            "ARG_VEHICLECLASS TEXT, DELAYAVG REAL, STOPSAVG REAL, SPEEDAVG REAL, "
            "DELAYSTOPAVG REAL, DISTTOT REAL, TRAVTMTOT REAL, DELAYTOT REAL, VEHACT REAL, "
            "VEHARR REAL)"
        )
        conn.execute(
            "INSERT INTO VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass "
            "VALUES ('ALL', 32.4, 1.6, 38.2, 21.0, 6480000.0, 52000.0, 19400.0, 310.0, 5050.0)"
        )
        if movement_tables:
            conn.execute(
                "CREATE TABLE MOVEMENT_EvaluationTimeIntervalClass (OBJECT_ID TEXT, "
                "ARG_VEHICLECLASS TEXT, VEHS REAL, VEHDELAY REAL, STOPDELAY REAL, STOPS REAL)"
            )
            conn.execute(
                "CREATE TABLE MOVEMENT_EvaluationTimeInterval (OBJECT_ID TEXT, QLEN REAL, "
                "QLENMAX REAL, SUMDELAYS REAL, SUMSTOPS REAL)"
            )
            for object_id, vehs, vehdelay in movements or ():
                conn.execute(
                    "INSERT INTO MOVEMENT_EvaluationTimeIntervalClass VALUES (?, 'ALL', ?, ?, 0, 0)",
                    (object_id, vehs, vehdelay),
                )
            for object_id, qlen, qlenmax in queues or ():
                conn.execute(
                    "INSERT INTO MOVEMENT_EvaluationTimeInterval VALUES (?, ?, ?, 0, 0)",
                    (object_id, qlen, qlenmax),
                )
        conn.commit()
    finally:
        conn.close()


def _write_inpx(path: Path, *, queue_definition: bool, delay_segment_start_m: float = 100.0) -> Path:
    """The ``<evaluation>`` element U1 reads; attribute values copied from
    real networks in this checkout."""
    attrs = [f'nodesDelaySegmentStart="{delay_segment_start_m}"']
    if queue_definition:
        attrs += [
            'queueBeginSpeed="3"',
            'queueEndSpeed="6"',
            'queueMaxClear="10"',
            'queueMaxLength="500"',
            'queueConsiderAdjacentLanes="true"',
        ]
    path.write_text(
        f"<network><evaluation {' '.join(attrs)}></evaluation></network>", encoding="utf-8"
    )
    return path


def _run_result(work_dir: Path, source: Path) -> dict[str, Any]:
    """The real ``run_result`` shape, built by U1's own evidence collector.

    The four base keys are exactly what ``tools_run.run_simulation``
    constructs before it calls ``_collect_run_evidence``; the job runner and
    COM layer are simply not involved in producing them.
    """
    run_result: dict[str, Any] = {
        "tables": ["SIMULATIONRUN"],
        "deleted_vehicles": None,
        "evaluation_configured": True,
        "trajectory_recorded": False,
    }
    run_result.update(
        tools_run._collect_run_evidence(
            work_dir, source, duration_s=3600, evaluation_configured=True
        )
    )
    return run_result


@pytest.fixture
def unhealthy_run(tmp_path: Path) -> dict[str, Any]:
    """A run whose evidence trips both ARMED anomalies.

    * a zero-throughput movement Vissim graded LOS A (blocked connector);
    * queue numbers with NO defining parameters in the .inpx, and a queue
      longer than the 100 m delay-measurement segment (ghost queue).
    """
    work = tmp_path / "job-unhealthy"
    work.mkdir()
    _write_results_db(
        work / "run_001.db",
        movements=[(_MOVEMENT_BUSY, 1134.0, 44.84), (_MOVEMENT_GRIDLOCKED, 0.0, 0.0)],
        queues=[(_MOVEMENT_BUSY, 12.0, 41.5), (_MOVEMENT_GRIDLOCKED, 30.0, 137.4)],
    )
    return _run_result(work, _write_inpx(tmp_path / "unhealthy.inpx", queue_definition=False))


@pytest.fixture
def healthy_run(tmp_path: Path) -> dict[str, Any]:
    """The same evidence keys, all clean: every movement carries throughput,
    the queue definition IS attached, no queue outruns the segment."""
    work = tmp_path / "job-healthy"
    work.mkdir()
    _write_results_db(
        work / "run_001.db",
        movements=[(_MOVEMENT_BUSY, 1134.0, 44.8), (_MOVEMENT_GRIDLOCKED, 510.0, 10.28)],
        queues=[(_MOVEMENT_BUSY, 12.0, 41.5), (_MOVEMENT_GRIDLOCKED, 8.0, 22.25)],
    )
    return _run_result(work, _write_inpx(tmp_path / "healthy.inpx", queue_definition=True))


@pytest.fixture
def armed_but_empty_run(tmp_path: Path) -> dict[str, Any]:
    """Both MOVEMENT tables exist and are EMPTY — queue and movement
    collection was armed and returned nothing. The shape 48 real vendor
    result .dbs in this checkout have."""
    work = tmp_path / "job-empty"
    work.mkdir()
    _write_results_db(work / "run_001.db", movements=[], queues=[])
    return _run_result(work, _write_inpx(tmp_path / "empty.inpx", queue_definition=True))


@pytest.fixture
def table_absent_run(tmp_path: Path) -> dict[str, Any]:
    """Neither MOVEMENT table exists at all — that evaluation was never
    armed. Distinct from armed-but-empty, and the catalogue must keep them
    distinct."""
    work = tmp_path / "job-absent"
    work.mkdir()
    _write_results_db(work / "run_001.db", movement_tables=False)
    return _run_result(work, _write_inpx(tmp_path / "absent.inpx", queue_definition=True))


def _by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in report["anomalies"]}


# ---------------------------------------------------------------------------
# (a) The four roadmap-named anomalies, each with a derived status
# ---------------------------------------------------------------------------


def test_the_catalogue_indexes_exactly_the_four_roadmap_named_anomalies():
    assert sorted(a.id for a in anomalies.get_anomaly_registry()) == [
        "blocked_connector",
        "ghost_queue",
        "latent_demand_diffusion",
        "starved_link",
    ]


def test_every_indexed_constraint_id_resolves_in_the_live_registry_today():
    """The fail-fast a rename should trip.

    ``register_anomaly`` deliberately does not raise on an unknown id (a
    rename must not crash backend import, and a raise would hide the stale
    claim instead of reporting it) — so the fail-fast lives here.
    """
    coverage = anomalies.get_anomaly_coverage()
    unresolved = {
        anomaly_id: entry["unresolved_constraint_ids"]
        for anomaly_id, entry in coverage.items()
        if entry["unresolved_constraint_ids"]
    }
    assert unresolved == {}


def test_the_four_arming_statuses_are_what_the_evidence_supports():
    coverage = anomalies.get_anomaly_coverage()
    assert coverage["ghost_queue"]["arming"] == anomalies.ARMED
    assert coverage["blocked_connector"]["arming"] == anomalies.ARMED
    assert coverage["starved_link"]["arming"] == anomalies.NOT_YET_ARMED
    assert coverage["latent_demand_diffusion"]["arming"] == anomalies.NOT_MEASURABLE


def test_starved_link_is_not_yet_armed_rather_than_not_measurable():
    """The distinction is load-bearing: ``NOT_MEASURABLE`` would tell a
    future reader to stop looking, and the evidence is right there.

    Both halves of the claim are pinned against live state rather than
    prose: unit U1's descriptor already reports on the LINK table (so the
    evidence is addressable), while ``results_reader`` contains no SQL
    against it (so nothing reads it today).
    """
    entry = anomalies.get_anomaly_coverage()["starved_link"]
    assert entry["arming"] == anomalies.NOT_YET_ARMED
    assert entry["arming"] != anomalies.NOT_MEASURABLE

    link_table = "LINKEVALSEGMENT_EvaluationTimeIntervalClass"
    assert link_table in results_evidence.EVALUATION_TABLES

    reader_source = (
        Path(__file__).resolve().parents[1] / "services" / "results_reader.py"
    ).read_text(encoding="utf-8")
    assert link_table not in reader_source
    assert "LINKEVALSEGMENT" not in reader_source

    # And the "only four evaluation types are armed" half is read live, not
    # remembered — the day a link-eval type is added, this fails and the
    # status gets revisited.
    assert len(anomalies.armed_evaluation_types()) == 4
    assert not any("link" in name for name in anomalies.armed_evaluation_types())
    assert link_table in entry["reason"]["en"]


def test_latent_demand_attaches_the_measured_reason_instead_of_restating_it():
    entry = anomalies.get_anomaly_coverage()["latent_demand_diffusion"]
    assert entry["arming"] == anomalies.NOT_MEASURABLE
    assert entry["reason_source"] == (
        "services/agent/tools_run.py module docstring, "
        "paragraph 'deleted_vehicles is always None today'"
    )

    verbatim = entry["reason_verbatim"]
    assert verbatim and verbatim != anomalies._UNOBTAINABLE_FALLBACK
    # Read out of tools_run's own docstring, not typed here.
    assert verbatim in " ".join((tools_run.__doc__ or "").split())
    assert "DiffusTm" in verbatim
    assert "never in any SQLite result table" in verbatim
    assert "latent_demand_vehicles" in verbatim


def test_uncatalogued_postrun_constraints_are_named_not_implied():
    """The catalogue's honesty about its own edges: the postrun catalog is
    bigger than four anomalies, and the rest are listed by id rather than
    left to look like they do not exist."""
    uncatalogued = {c.id for c in anomalies.get_uncatalogued_postrun_constraints()}
    catalogued = anomalies.get_catalogued_constraint_ids()
    postrun = {c.id for c in constraints.get_registry() if c.phase == "postrun"}

    assert uncatalogued | catalogued == postrun
    assert not (uncatalogued & catalogued)
    # A concrete one, so this is not a tautology over empty sets.
    assert "results_missing_kpi_tables" in uncatalogued


# ---------------------------------------------------------------------------
# (b) MUTATION EVIDENCE — coverage is derived from live registry state
# ---------------------------------------------------------------------------


def test_removing_an_indexed_constraint_changes_the_derived_coverage(
    monkeypatch: pytest.MonkeyPatch,
):
    """The test a hardcoded coverage table could not pass.

    ``gridlock_reports_los_a`` is deleted from the LIVE constraint registry
    (monkeypatch restores it afterwards). Nothing in anomalies.py is
    touched. The catalogue must notice.
    """
    before = anomalies.get_anomaly_coverage()["blocked_connector"]
    assert before["arming"] == anomalies.ARMED
    assert before["constraint_ids"] == ["gridlock_reports_los_a"]
    assert before["unresolved_constraint_ids"] == []

    monkeypatch.delitem(constraints._REGISTRY, "gridlock_reports_los_a")

    after = anomalies.get_anomaly_coverage()["blocked_connector"]
    assert after["arming"] == anomalies.DETECTOR_UNREGISTERED
    assert after["declared_arming"] == anomalies.ARMED
    assert after["constraint_ids"] == []
    assert after["unresolved_constraint_ids"] == [
        {"constraint_id": "gridlock_reports_los_a", "why": "NOT_REGISTERED"}
    ]
    # And the id stops counting as covered anywhere in the catalogue.
    assert "gridlock_reports_los_a" not in anomalies.get_catalogued_constraint_ids()


def test_a_vanished_detector_makes_the_scan_report_not_checkable_not_clean(
    unhealthy_run: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Same mutation, seen through the scan: an anomaly that WAS detecting
    must not silently start reporting nothing wrong."""
    detected = _by_id(anomalies.scan_run_result(unhealthy_run))["blocked_connector"]
    assert detected["outcome"] == anomalies.DETECTED

    monkeypatch.delitem(constraints._REGISTRY, "gridlock_reports_los_a")

    entry = _by_id(anomalies.scan_run_result(unhealthy_run))["blocked_connector"]
    assert entry["outcome"] == anomalies.NOT_CHECKABLE
    assert entry["arming"] == anomalies.DETECTOR_UNREGISTERED
    assert "gridlock_reports_los_a" in entry["why"]["en"]
    for lang in ("ko", "en", "ja"):
        assert entry["why"][lang]


def test_re_registering_a_constraint_under_a_new_id_leaves_no_stale_claim(
    monkeypatch: pytest.MonkeyPatch,
):
    """The rename case specifically: the check logic is still registered,
    just under a different id. Coverage must follow the ID, because the id
    is what the catalogue indexes."""
    import dataclasses

    original = constraints._REGISTRY["queue_length_definition_not_portable"]
    monkeypatch.delitem(constraints._REGISTRY, "queue_length_definition_not_portable")
    monkeypatch.setitem(
        constraints._REGISTRY,
        "queue_definition_missing",
        dataclasses.replace(original, id="queue_definition_missing"),
    )

    entry = anomalies.get_anomaly_coverage()["ghost_queue"]
    assert entry["arming"] == anomalies.DETECTOR_UNREGISTERED
    assert entry["constraint_ids"] == ["delay_measurement_truncated_by_queue"]
    assert entry["unresolved_constraint_ids"] == [
        {"constraint_id": "queue_length_definition_not_portable", "why": "NOT_REGISTERED"}
    ]


def test_an_indexed_constraint_re_phased_away_from_postrun_stops_counting(
    monkeypatch: pytest.MonkeyPatch,
):
    """A postrun scan can never fire a preflight check, so indexing one
    would be a coverage claim with nothing behind it — derived, not
    assumed."""
    import dataclasses

    original = constraints._REGISTRY["gridlock_reports_los_a"]
    monkeypatch.setitem(
        constraints._REGISTRY,
        "gridlock_reports_los_a",
        dataclasses.replace(original, phase="preflight"),
    )

    entry = anomalies.get_anomaly_coverage()["blocked_connector"]
    assert entry["arming"] == anomalies.DETECTOR_UNREGISTERED
    assert entry["unresolved_constraint_ids"] == [
        {"constraint_id": "gridlock_reports_los_a", "why": "WRONG_PHASE", "phase": "preflight"}
    ]


# ---------------------------------------------------------------------------
# (c) The two ARMED anomalies genuinely fire — end to end, through the scan
# ---------------------------------------------------------------------------


def test_ghost_queue_and_blocked_connector_fire_on_an_unhealthy_run(
    unhealthy_run: dict[str, Any],
):
    report = anomalies.scan_run_result(unhealthy_run)
    entries = _by_id(report)

    ghost = entries["ghost_queue"]
    assert ghost["outcome"] == anomalies.DETECTED
    assert sorted(f["constraint_id"] for f in ghost["findings"]) == [
        "delay_measurement_truncated_by_queue",
        "queue_length_definition_not_portable",
    ]

    blocked = entries["blocked_connector"]
    assert blocked["outcome"] == anomalies.DETECTED
    assert [f["constraint_id"] for f in blocked["findings"]] == ["gridlock_reports_los_a"]
    # The finding names the actual zero-throughput movement, not a summary.
    assert blocked["findings"][0]["details"]["movements"] == [_MOVEMENT_GRIDLOCKED]

    assert report["summary"]["detected_ids"] == ["ghost_queue", "blocked_connector"]
    assert report["summary"]["detected_n"] == 2


def test_the_same_two_stay_silent_on_a_healthy_run(healthy_run: dict[str, Any]):
    entries = _by_id(anomalies.scan_run_result(healthy_run))

    assert entries["ghost_queue"]["outcome"] == anomalies.CHECKED_CLEAN
    assert entries["ghost_queue"]["findings"] == []
    assert entries["blocked_connector"]["outcome"] == anomalies.CHECKED_CLEAN
    assert entries["blocked_connector"]["findings"] == []

    # Checked-clean means the checks actually RAN against real evidence.
    for anomaly_id in ("ghost_queue", "blocked_connector"):
        for check in entries[anomaly_id]["checks"]:
            assert check["checkable"] is True
            assert all(e["state"] == anomalies.AVAILABLE for e in check["evidence"])


def test_findings_carry_all_three_languages(unhealthy_run: dict[str, Any]):
    for entry in _by_id(anomalies.scan_run_result(unhealthy_run)).values():
        for finding in entry["findings"]:
            assert set(finding["messages"]) == {"ko", "en", "ja"}
            assert all(finding["messages"][lang] for lang in ("ko", "en", "ja"))


# ---------------------------------------------------------------------------
# (d) Four distinguishable states, one test each
# ---------------------------------------------------------------------------


def test_state_detected(unhealthy_run: dict[str, Any]):
    assert _by_id(anomalies.scan_run_result(unhealthy_run))["blocked_connector"]["outcome"] == (
        anomalies.DETECTED
    )


def test_state_checked_clean(healthy_run: dict[str, Any]):
    assert _by_id(anomalies.scan_run_result(healthy_run))["blocked_connector"]["outcome"] == (
        anomalies.CHECKED_CLEAN
    )


def test_state_not_yet_armed(healthy_run: dict[str, Any]):
    entry = _by_id(anomalies.scan_run_result(healthy_run))["starved_link"]
    assert entry["arming"] == anomalies.NOT_YET_ARMED
    assert entry["outcome"] == anomalies.NOT_CHECKABLE
    assert "scheduled work" in entry["why"]["en"]


def test_state_not_measurable(healthy_run: dict[str, Any]):
    entry = _by_id(anomalies.scan_run_result(healthy_run))["latent_demand_diffusion"]
    assert entry["arming"] == anomalies.NOT_MEASURABLE
    assert entry["outcome"] == anomalies.NOT_CHECKABLE
    assert "Not observable at all" in entry["why"]["en"]


def test_the_four_states_are_genuinely_distinct_on_one_run(healthy_run: dict[str, Any]):
    """All four in a single report, so "distinguishable" is not four
    separate tests that happen to agree."""
    entries = _by_id(anomalies.scan_run_result(healthy_run))
    states = {
        entries["blocked_connector"]["outcome"],
        entries["starved_link"]["arming"],
        entries["latent_demand_diffusion"]["arming"],
    }
    assert states == {anomalies.CHECKED_CLEAN, anomalies.NOT_YET_ARMED, anomalies.NOT_MEASURABLE}
    assert entries["starved_link"]["why"] != entries["latent_demand_diffusion"]["why"]


# ---------------------------------------------------------------------------
# (e) THE ONE THAT MUST NOT BE SOFTENED — armed-but-empty is never clean
# ---------------------------------------------------------------------------


def test_armed_but_empty_evidence_reports_not_checkable_never_clean(
    armed_but_empty_run: dict[str, Any],
):
    """"Queue collection was requested and returned nothing" is not "there
    are no queues"."""
    # Precondition: this really is the armed-but-empty shape, not absence.
    descriptor = armed_but_empty_run["evidence"]
    assert descriptor[results_evidence.MOVEMENT_INTERVAL_TABLE] == results_evidence.ARMED_BUT_EMPTY
    assert (
        descriptor[results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE]
        == results_evidence.ARMED_BUT_EMPTY
    )

    entries = _by_id(anomalies.scan_run_result(armed_but_empty_run))
    for anomaly_id in ("ghost_queue", "blocked_connector"):
        entry = entries[anomaly_id]
        assert entry["outcome"] == anomalies.NOT_CHECKABLE
        assert entry["outcome"] != anomalies.CHECKED_CLEAN
        assert entry["findings"] == []
        for check in entry["checks"]:
            assert check["checkable"] is False
            assert all(
                e["state"] == results_evidence.ARMED_BUT_EMPTY for e in check["evidence"]
            )
            # The marker's own reason travels with it.
            assert all(results_evidence.ARMED_BUT_EMPTY_REASON == e["marker_reason"] for e in check["evidence"])
        assert "zero rows" in entry["why"]["en"]
        for lang in ("ko", "en", "ja"):
            assert entry["why"][lang]


def test_armed_but_empty_survives_a_json_round_trip(armed_but_empty_run: dict[str, Any]):
    """The ``EmptyEvidence*`` markers serialise to a bare ``{}``/``[]`` (U1's
    own docstring says so). The durable form is the evidence descriptor, and
    the catalogue must honour it — otherwise the blind-spot rule holds
    in-process and quietly fails the moment a run payload crosses a JSON
    boundary (audit trail, API response, stored session)."""
    round_tripped = json.loads(json.dumps(armed_but_empty_run))
    # The markers are gone; only the descriptor remembers.
    assert round_tripped["queue_results"] == {}
    assert round_tripped["los_by_movement"] == []

    entries = _by_id(anomalies.scan_run_result(round_tripped))
    for anomaly_id in ("ghost_queue", "blocked_connector"):
        entry = entries[anomaly_id]
        assert entry["outcome"] == anomalies.NOT_CHECKABLE
        assert "zero rows" in entry["why"]["en"]


def test_table_absent_is_reported_differently_from_armed_but_empty(
    table_absent_run: dict[str, Any], armed_but_empty_run: dict[str, Any]
):
    """Both are not-checkable, but for different reasons, and the report
    must say which — "never armed" and "armed and collected nothing" call
    for different fixes."""
    absent = _by_id(anomalies.scan_run_result(table_absent_run))["blocked_connector"]
    empty = _by_id(anomalies.scan_run_result(armed_but_empty_run))["blocked_connector"]

    assert absent["outcome"] == empty["outcome"] == anomalies.NOT_CHECKABLE
    assert absent["checks"][0]["evidence"][0]["state"] == results_evidence.TABLE_ABSENT
    assert empty["checks"][0]["evidence"][0]["state"] == results_evidence.ARMED_BUT_EMPTY
    assert absent["why"] != empty["why"]
    assert "never armed" in absent["why"]["en"]


def test_a_partially_checkable_anomaly_reports_not_checkable(
    healthy_run: dict[str, Any],
):
    """Conservative on purpose: ghost_queue indexes two checks, and one of
    them losing its evidence must not leave the headline reading "clean"."""
    partial = dict(healthy_run)
    partial.pop("delay_segments")

    entry = _by_id(anomalies.scan_run_result(partial))["ghost_queue"]
    assert entry["outcome"] == anomalies.NOT_CHECKABLE
    checkable = {c["constraint_id"]: c["checkable"] for c in entry["checks"]}
    assert checkable == {
        "queue_length_definition_not_portable": True,
        "delay_measurement_truncated_by_queue": False,
    }


def test_no_run_result_at_all_is_not_checkable_everywhere():
    report = anomalies.scan_run_result(None)
    assert report["summary"]["not_checkable_n"] == 4
    assert report["summary"]["detected_ids"] == []
    assert report["summary"]["checked_clean_ids"] == []
    blocked = _by_id(report)["blocked_connector"]
    assert "no run payload at all" in blocked["why"]["en"]


# ---------------------------------------------------------------------------
# (f) Two callers: row 3.7's self-contained scan and row 2.1's reuse
# ---------------------------------------------------------------------------


def test_index_postrun_findings_agrees_with_the_self_contained_scan(
    unhealthy_run: dict[str, Any],
):
    """Row 2.1 already holds a GuardrailReport; it must not pay to run the
    same checks twice, and must not get a different answer for doing so."""
    report = GuardrailEngine().postrun({"tool": "run_simulation"}, unhealthy_run)
    reused = anomalies.index_postrun_findings(report.findings, unhealthy_run)
    scanned = anomalies.scan_run_result(unhealthy_run)

    def _shape(payload: dict[str, Any]) -> Any:
        return [
            (e["id"], e["outcome"], sorted(f["constraint_id"] for f in e["findings"]))
            for e in payload["anomalies"]
        ]

    assert _shape(reused) == _shape(scanned)


def test_the_scan_needs_nothing_but_a_run_payload(unhealthy_run: dict[str, Any]):
    """Row 3.7 flags anomalies WITHOUT being asked — no action, no shadow
    model, no agent turn."""
    report = anomalies.scan_run_result(unhealthy_run)
    assert report["summary"]["detected_n"] == 2
    assert "3.7" in report["roadmap_rows"]


def test_a_finding_no_anomaly_indexes_is_surfaced_not_swallowed():
    """``results_missing_kpi_tables`` belongs to no catalogue anomaly. A
    scan that dropped it would imply the four names are the whole postrun
    catalog."""
    run_result = {
        "tables": ["SIMULATIONRUN"],
        "deleted_vehicles": None,
        "evaluation_configured": True,
        "trajectory_recorded": False,
    }
    report = anomalies.scan_run_result(run_result)
    uncatalogued = {f["constraint_id"] for f in report["uncatalogued_findings"]}
    assert "results_missing_kpi_tables" in uncatalogued
    assert "results_missing_kpi_tables" in report["uncatalogued_postrun_constraint_ids"]


def test_a_not_measurable_anomaly_whose_detector_fires_reports_the_finding():
    """The catalogue must never suppress a real finding to protect its own
    status claim.

    ``latent_demand_residue_after_run`` is registered and would fire if
    ``run_result["latent_demand_vehicles"]`` ever appeared — which is
    exactly what a future unit closing that gap would do. Until then the
    anomaly is catalogued ``NOT_MEASURABLE``; the day the key shows up, the
    finding wins and the stale status is reported as stale.
    """
    run_result = {
        "tables": ["SIMULATIONRUN", "MOVEMENT_EvaluationTimeIntervalClass"],
        "deleted_vehicles": None,
        "evaluation_configured": True,
        "trajectory_recorded": False,
        "latent_demand_vehicles": 42,
    }
    entry = _by_id(anomalies.scan_run_result(run_result))["latent_demand_diffusion"]

    assert entry["outcome"] == anomalies.DETECTED
    assert entry["arming"] == anomalies.NOT_MEASURABLE
    assert entry["arming_contradicted"] is True
    assert [f["constraint_id"] for f in entry["findings"]] == [
        "latent_demand_residue_after_run"
    ]
    assert "stale" in entry["why"]["en"]


def test_no_finding_is_ever_dropped_between_the_two_lists(unhealthy_run: dict[str, Any]):
    """Every postrun finding this run produced is either attached to an
    anomaly or listed as uncatalogued — never neither."""
    from services.agent import constraints as constraints_module

    raw = constraints_module.run_checks(
        "postrun", {"tool": "run_simulation"}, {}, {"run_result": unhealthy_run}
    )
    report = anomalies.scan_run_result(unhealthy_run)

    attached = [f["constraint_id"] for e in report["anomalies"] for f in e["findings"]]
    loose = [f["constraint_id"] for f in report["uncatalogued_findings"]]
    assert sorted(attached + loose) == sorted(f.constraint_id for f in raw)
    assert not (set(attached) & set(loose))


def test_the_whole_report_is_json_serialisable(unhealthy_run: dict[str, Any]):
    """Row 3.7 renders this; it crosses an API boundary."""
    assert json.loads(json.dumps(anomalies.scan_run_result(unhealthy_run)))


def test_every_anomaly_entry_has_the_same_shape(unhealthy_run: dict[str, Any]):
    """One shape for consumers, never two — a key that appears only on some
    branches makes a renderer guess."""
    expected = {
        "id",
        "label",
        "arming",
        "declared_arming",
        "reason",
        "reason_source",
        "reason_verbatim",
        "constraint_ids",
        "unresolved_constraint_ids",
        "evidence_tables",
        "roadmap_rows",
        "checks",
        "findings",
        "arming_contradicted",
        "outcome",
        "why",
    }
    for entry in anomalies.scan_run_result(unhealthy_run)["anomalies"]:
        assert set(entry) == expected, entry["id"]
        assert entry["outcome"] in anomalies.OUTCOMES
        assert entry["arming"] in anomalies.ARMING_STATUSES
        for check in entry["checks"]:
            for evidence in check["evidence"]:
                assert set(evidence) == {
                    "key",
                    "state",
                    "tables",
                    "why",
                    "marker_reason",
                    "marker_source",
                }


# ---------------------------------------------------------------------------
# (g) Registration guards and i18n
# ---------------------------------------------------------------------------


def _anomaly(**overrides: Any) -> anomalies.Anomaly:
    base: dict[str, Any] = {
        "id": "test_only_anomaly",
        "label_ko": "테스트",
        "label_en": "Test",
        "label_ja": "テスト",
        "arming": anomalies.NOT_YET_ARMED,
        "reason_ko": "사유",
        "reason_en": "reason",
        "reason_ja": "理由",
    }
    base.update(overrides)
    return anomalies.Anomaly(**base)


def test_register_anomaly_rejects_a_duplicate_id():
    with pytest.raises(ValueError, match="duplicate anomaly id"):
        anomalies.register_anomaly(_anomaly(id="ghost_queue"))


def test_register_anomaly_rejects_armed_with_no_indexed_constraint():
    with pytest.raises(ValueError, match="indexes no constraint"):
        anomalies.register_anomaly(_anomaly(arming=anomalies.ARMED))


def test_detector_unregistered_cannot_be_declared_only_derived():
    with pytest.raises(ValueError, match="derived, never declared"):
        anomalies.register_anomaly(_anomaly(arming=anomalies.DETECTOR_UNREGISTERED))


def test_register_anomaly_rejects_a_missing_translation():
    with pytest.raises(ValueError, match="ko/en/ja reason"):
        anomalies.register_anomaly(_anomaly(reason_ja=""))


def test_register_anomaly_does_not_raise_on_an_unknown_constraint_id():
    """Deliberate asymmetry with ``constraints.register_dataset_check``: an
    unresolvable id is reportable data, not an import-time crash."""
    try:
        anomalies.register_anomaly(
            _anomaly(
                id="test_only_unknown_id",
                checks=(anomalies.IndexedCheck("not_a_real_constraint_id", ("x",)),),
            )
        )
        entry = anomalies.get_anomaly_coverage()["test_only_unknown_id"]
        assert entry["unresolved_constraint_ids"] == [
            {"constraint_id": "not_a_real_constraint_id", "why": "NOT_REGISTERED"}
        ]
        assert entry["constraint_ids"] == []
    finally:
        anomalies._REGISTRY.pop("test_only_unknown_id", None)


def test_every_user_facing_string_carries_ko_en_and_ja():
    for anomaly in anomalies.get_anomaly_registry():
        for bundle in (anomaly.label(), anomaly.reason()):
            assert set(bundle) == {"ko", "en", "ja"}
            assert all(bundle[lang].strip() for lang in ("ko", "en", "ja"))
    for state, template in anomalies._WHY_MESSAGES.items():
        assert set(template) == {"ko", "en", "ja"}, state
        assert all(template[lang].strip() for lang in ("ko", "en", "ja")), state
