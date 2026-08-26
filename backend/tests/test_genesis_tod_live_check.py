"""Unit #70 — the S7 probe's CLASSIFIER, tested against synthetic save results.

The probe is what SETTLED S7 (2026-08-26, ``KEPT_VERBATIM`` on a live Vissim
SaveNetAs roundtrip) and what re-verifies it after a Vissim upgrade. A classifier
that says KEPT_VERBATIM too easily is worse than no probe at all: it would retire
the assumption while the emission is still wrong, and the C2/S6 precedent's whole
value — "closing S6 was a RUN, not an investigation" — depends on the run being
able to say no. These tests are what make that verdict worth believing, and they
were written and made to fail on purpose BEFORE the live run.

Three rubber-stamp scenarios have their own tests below. Each is a way Vissim
could hand the schedule back CHANGED while the container name — the one name
that was never really in doubt — stayed put.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from services.genesis.signals import (
    AlternateProgram,
    NodeSignals,
    SignalGroupPlan,
    TodPrograms,
    build_int_supply_data,
    build_vissig_document,
)
from tools.genesis_tod_live_check import (
    build_report,
    describe_schedule,
    pair_controllers,
    verdict,
)


def _group(no: int, link_id: int, green_start_s: float, green_s: float) -> SignalGroupPlan:
    return SignalGroupPlan(
        no=no,
        approach_link_id=link_id,
        approach_name=f"approach {link_id}",
        phase_id=f"Φ{no}",
        green_start_s=green_start_s,
        green_s=green_s,
        amber_s=3.0,
        all_red_s=2.0,
        red_amber_s=0.0,
        min_green_s=5.0,
        lanes=2,
    )


def _node(
    controller_no: int = 7,
    *,
    emit_schedule_element: bool = True,
    alternate_cycle_s: float = 90.0,
    switching: bool = True,
    morning_switch_s: int = 7 * 3600,
) -> NodeSignals:
    groups = (_group(1, 101, 0.0, 25.0), _group(2, 102, 30.0, 25.0))
    tod = None
    if switching:
        tod = TodPrograms(
            entries=((0, 2), (morning_switch_s, 1), (17 * 3600, 2)),
            alternates=(
                AlternateProgram(
                    prog_id=2,
                    program_key="L2-P3",
                    cycle_s=alternate_cycle_s,
                    groups=(_group(1, 101, 0.0, 40.0), _group(2, 102, 45.0, 40.0)),
                ),
            ),
            day_plan_no=1,
            emit_schedule_element=emit_schedule_element,
        )
    return NodeSignals(
        controller_no=controller_no,
        node_id=controller_no,
        node_name="원신흥",
        cycle_s=60.0,
        groups=groups,
        heads=(),
        detectors=(),
        issues=(),
        compliance=(),
        tod=tod,
    )


def _document(emit_schedule_element: bool = True) -> str:
    return build_vissig_document(_node(emit_schedule_element=emit_schedule_element))


def write_inpx(path: Path, nodes) -> Path:
    """A minimal ``.inpx`` carrying real controllers, in the ORDER given.

    Only ``<signalController>`` matters to ``decode_blobs``, and each one's
    ``intSupplyData`` is produced by the emitter itself — so these files
    exercise the real decode path rather than a hand-written approximation of
    it. The element order is the caller's, which is what makes a reordered save
    expressible.
    """
    elements = "".join(
        f'<signalController no="{node.controller_no}" '
        f'intSupplyData="{build_int_supply_data(node)}"/>'
        for node in nodes
    )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<network><signalControllers>{elements}</signalControllers></network>",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def emitted() -> dict:
    return describe_schedule(_document())


def test_the_emitted_document_is_what_the_probe_reads(emitted):
    assert emitted["parsed"] is True
    assert emitted["daily_child_tags"] == ["dailyProgList"]
    assert len(emitted["daily_entries"]) == 3
    assert len(emitted["programs"]) == 2


# ---------------------------------------------------------------------------
# The four verdicts
# ---------------------------------------------------------------------------


def test_an_identical_save_is_kept_verbatim(emitted):
    assert verdict(emitted, describe_schedule(_document()))["s7"] == "KEPT_VERBATIM"


def test_a_save_that_strips_the_schedule_is_dropped(emitted):
    finding = verdict(emitted, describe_schedule(_document(emit_schedule_element=False)))
    assert finding["s7"] == "DROPPED"
    assert finding["s7b_programs_survived"] is True


def test_an_unparseable_save_is_refused(emitted):
    finding = verdict(emitted, describe_schedule("<sc><progs>"))
    assert finding["s7"] == "REFUSED_OR_UNPARSEABLE"


def test_a_controller_absent_from_the_save_is_a_named_finding_not_a_skip(emitted):
    """A deleted controller used to fall out of the report entirely, because the
    findings were built by zipping two lists."""
    finding = verdict(emitted, None)
    assert finding["s7"] == "CONTROLLER_MISSING"
    assert finding["s7b_programs_survived"] is False


def test_a_network_with_no_schedule_says_so_rather_than_passing(emitted):
    plain = describe_schedule(_document(emit_schedule_element=False))
    assert verdict(plain, plain)["s7"] == "NOTHING_TO_JUDGE"


# ---------------------------------------------------------------------------
# The three rubber-stamp scenarios — none may report KEPT_VERBATIM
# ---------------------------------------------------------------------------


def test_an_attribute_rename_is_rewritten_not_kept(emitted):
    """``from``→``start`` and ``prog_id``→``progNo``: the container name is
    untouched, so a tags-only classifier called this "S7 correct"."""
    saved = describe_schedule(
        _document().replace(' from="', ' start="').replace(' prog_id="', ' progNo="')
    )
    finding = verdict(emitted, saved)
    assert finding["s7"] == "REWRITTEN"
    assert any("attribute names changed" in d for d in finding["differences"])


def test_an_entry_element_rename_is_rewritten_not_kept(emitted):
    """``<dailyProg>``→``<entry>``, with ``<dailyProgList>`` still in place."""
    saved = describe_schedule(_document().replace("<dailyProg ", "<entry "))
    finding = verdict(emitted, saved)
    assert finding["s7"] == "REWRITTEN"
    assert any("entry element renamed" in d for d in finding["differences"])


def test_half_the_entries_being_dropped_is_rewritten_not_kept(emitted):
    """Vissim keeping the shape but silently discarding switch points is the
    worst case of all: the file looks right and the day is wrong."""
    document = _document()
    lines = [line for line in document.split("\r\n") if "<dailyProg " not in line]
    kept = document.split("\r\n")
    first_entry = next(line for line in kept if "<dailyProg " in line)
    saved_document = "\r\n".join(
        lines[: lines.index("    </dailyProgList>")]
        + [first_entry]
        + lines[lines.index("    </dailyProgList>") :]
    )
    finding = verdict(emitted, describe_schedule(saved_document))
    assert finding["s7"] == "REWRITTEN"
    assert any("entry count changed: 3 -> 1" in d for d in finding["differences"])


def test_a_changed_switch_TIME_is_rewritten_not_kept(emitted):
    """Same names, same count — one value moved. Only a value-level comparison
    catches this, and it is the difference between a schedule that runs when the
    sheet says and one that does not."""
    saved = describe_schedule(_document().replace('from="25200000"', 'from="28800000"'))
    finding = verdict(emitted, saved)
    assert finding["s7"] == "REWRITTEN"
    assert any("values changed" in d for d in finding["differences"])


def test_a_lost_program_is_reported_even_when_the_schedule_survives(emitted):
    """S7-b is independent of S7-a: the programs are the measured half."""
    document = _document()
    saved_document = document.replace(
        '    <prog id="2" cycletime="90000"', '    <prog id="2" cycletime="45000"'
    )
    finding = verdict(emitted, describe_schedule(saved_document))
    assert finding["s7b_programs_survived"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.replace(' from="', ' start="'),
        lambda d: d.replace(' prog_id="', ' progNo="'),
        lambda d: d.replace("<dailyProg ", "<entry "),
        lambda d: d.replace('from="25200000"', 'from="28800000"'),
        lambda d: d.replace("<dailyProgList ", "<dailyProgramList "),
    ],
)
def test_no_mutation_of_the_schedule_survives_as_kept_verbatim(emitted, mutation):
    """The classifier's contract in one line: KEPT_VERBATIM means IDENTICAL."""
    assert verdict(emitted, describe_schedule(mutation(_document())))["s7"] != "KEPT_VERBATIM"


# ---------------------------------------------------------------------------
# The wiring: two files in, findings out
#
# The classifier above was well covered while the JOIN that feeds it was not,
# and the join carries the two decisions a wrong report would never reveal.
# These go through ``decode_blobs`` and real ``.inpx`` files rather than
# hand-fed dictionaries, because a hand-fed ``None`` proves the classifier
# handles a missing controller without proving anything produces one.
# ---------------------------------------------------------------------------


def test_a_reordered_save_still_pairs_each_controller_with_itself(tmp_path: Path):
    """``SaveNetAs`` may write the controllers in any order.

    Controller 7 comes back untouched; controller 9 comes back with its morning
    switch moved 07:00 → 08:00. Paired by number, 7 is KEPT_VERBATIM and 9 is
    REWRITTEN. Paired positionally the two verdicts land on the wrong
    controllers — and each is individually plausible, so the report reads as a
    clean result while telling the orchestrator to fix the wrong controller.
    """
    emitted = write_inpx(tmp_path / "emitted.inpx", [_node(7), _node(9)])
    saved = write_inpx(
        tmp_path / "saved.inpx",
        [_node(9, morning_switch_s=8 * 3600), _node(7)],
    )

    before, after, switching = pair_controllers(emitted, saved)
    assert switching == ["7", "9"]
    assert sorted(after) == ["7", "9"]

    report = build_report(emitted, saved)
    assert report["findings"]["7"]["s7"] == "KEPT_VERBATIM"
    assert report["findings"]["9"]["s7"] == "REWRITTEN"
    assert any("values changed" in d for d in report["findings"]["9"]["differences"])


def test_a_controller_deleted_by_the_save_reaches_the_report_as_missing(tmp_path: Path):
    """The worst outcome the probe can observe — Vissim dropping the controller
    outright — must survive the join. Built from real files: the ``None`` that
    reaches ``verdict`` is produced by the lookup, not handed to it."""
    emitted = write_inpx(tmp_path / "emitted.inpx", [_node(7), _node(9)])
    saved = write_inpx(tmp_path / "saved.inpx", [_node(7)])

    before, after, switching = pair_controllers(emitted, saved)
    assert switching == ["7", "9"]
    assert after.get("9") is None

    report = build_report(emitted, saved)
    assert report["controllers_missing_from_save"] == ["9"]
    assert set(report["findings"]) == {"7", "9"}
    assert report["findings"]["9"]["s7"] == "CONTROLLER_MISSING"
    assert "CONTROLLER_MISSING" in report["verdicts"]


def test_a_controller_the_save_invented_is_counted_but_not_judged(tmp_path: Path):
    emitted = write_inpx(tmp_path / "emitted.inpx", [_node(7)])
    saved = write_inpx(tmp_path / "saved.inpx", [_node(7), _node(11)])
    report = build_report(emitted, saved)
    assert report["controllers_added_by_save"] == ["11"]
    assert set(report["findings"]) == {"7"}


def test_a_controller_that_never_opted_in_is_not_judged_by_s7(tmp_path: Path):
    """``switching`` is what was EMITTED with a schedule, so a plain controller
    contributes no S7 verdict either way."""
    emitted = write_inpx(tmp_path / "emitted.inpx", [_node(7, switching=False)])
    saved = write_inpx(tmp_path / "saved.inpx", [_node(7, switching=False)])
    report = build_report(emitted, saved)
    assert report["switching_controllers"] == []
    assert report["findings"] == {}


def test_the_report_round_trips_through_json(tmp_path: Path):
    import json

    emitted = write_inpx(tmp_path / "emitted.inpx", [_node(7), _node(9)])
    saved = write_inpx(tmp_path / "saved.inpx", [_node(7)])
    assert json.loads(json.dumps(build_report(emitted, saved)))["findings"]["9"]["s7"] == (
        "CONTROLLER_MISSING"
    )


# ---------------------------------------------------------------------------
# The COM census (S7-c) — checked WITHOUT COM
# ---------------------------------------------------------------------------


def _executable_source(fn) -> str:
    """A function's source with its DOCSTRING removed.

    The guards below assert that stale COM names do not appear. The census's own
    docstring names both of them — that is the record of what was fixed and why
    — so a plain substring check over the raw source would fail on the
    explanation rather than on the code. Stripping the docstring makes the
    guards about what the function DOES.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    function = tree.body[0]
    body = function.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        function.body = body[1:]
    return ast.unparse(function)


def test_the_docstring_stripper_really_removes_the_prose():
    """Otherwise the guards below could pass vacuously."""
    from tools import genesis_tod_live_check as probe

    code = _executable_source(probe.com_attribute_census)
    assert "MEASURED 2026-08-07" not in code
    assert "controller.load_network" in code


def test_the_census_calls_only_methods_vissim_controller_actually_has():
    """The live run found ``controller.open_net(...)`` — a method that has never
    existed on ``VissimController``; the API is ``load_network``. It raised only
    on a machine with Vissim installed, so the whole S7-c leg was dead in a way
    no offline test could see.

    This guard reads the census's OWN source for ``controller.<name>`` calls and
    holds each one against the real class, so the instrument cannot rot again
    between live runs. No COM and no Vissim needed.
    """
    import re as _re

    from services.vissim_com import VissimController
    from tools import genesis_tod_live_check as probe

    source = _executable_source(probe.com_attribute_census)
    called = set(_re.findall(r"\bcontroller\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", source))
    assert called, "expected the census to call the controller at all"
    missing = sorted(name for name in called if not hasattr(VissimController, name))
    assert missing == [], f"census calls VissimController method(s) that do not exist: {missing}"


def test_the_census_loads_the_network_through_the_real_api():
    from services.vissim_com import VissimController
    from tools import genesis_tod_live_check as probe

    source = _executable_source(probe.com_attribute_census)
    assert "controller.load_network(" in source
    assert "open_net" not in source
    assert hasattr(VissimController, "load_network")


def test_the_census_never_attaches_to_a_humans_running_vissim():
    """00-EXECUTION-GUIDE: ``GetActiveObject`` can destroy a human's open work."""
    from tools import genesis_tod_live_check as probe

    source = _executable_source(probe.com_attribute_census)
    assert "attach_to_running=False" in source
    assert "attach_to_running=True" not in source


def test_the_census_reads_the_attribute_table_through_the_measured_helper():
    """An item wrapper has no ``Attributes`` (measured 2026-08-07); the table
    hangs off the container. The census must not re-derive that the wrong way —
    ``AttributesHelp`` was its second stale COM name."""
    from tools import genesis_tod_live_check as probe

    source = _executable_source(probe.com_attribute_census)
    assert "_attribute_table(" in source
    assert "AttributesHelp" not in source


def test_the_census_degrades_to_a_named_reason_without_vissim():
    """Called here with no Vissim present: it must report unavailability rather
    than raise, so ``--skip-com`` is a convenience and not a requirement."""
    from tools import genesis_tod_live_check as probe

    result = probe.com_attribute_census(Path("does-not-exist.inpx"))
    assert result["available"] is False
    assert str(result["reason"]).strip()
