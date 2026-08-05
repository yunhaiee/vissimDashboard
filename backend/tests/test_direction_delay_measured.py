"""Per-approach delay must be MEASURED, never invented (unit 1.5, D5).

``_estimate_direction_delays`` used to multiply the node-average delay by a
made-up ``0.75 + (dirVol / maxVol) * 0.5`` volume factor and ship the product
as if it were a per-direction number. Vissim already reports ``VEHDELAY`` per
movement, and ``_build_node_directions`` already classifies every movement to a
compass approach, so the real value was available all along.

These tests pin the two halves of the fix:
  * a VEHS-weighted mean of the real per-movement delays, and
  * NOTHING emitted when no delay was measured -- no zeros, no guesses.
"""
from __future__ import annotations

import pytest

from services import results_reader


# One node at the origin with four approach links, one per compass point.
_NODE_META = {
    "1": {
        "node_name": "Test Node",
        "center": (0.0, 0.0),
        "links": {
            "10": [(0.0, 0.0), (0.0, 100.0)],    # north
            "20": [(0.0, 0.0), (100.0, 0.0)],    # east
            "30": [(0.0, 0.0), (0.0, -100.0)],   # south
            "40": [(0.0, 0.0), (-100.0, 0.0)],   # west
        },
        "link_names": {},
        "link_lanes": {"10": 1, "20": 1, "30": 1, "40": 1},
        "connector_lane_map": {},
    }
}


@pytest.fixture
def node_meta(monkeypatch):
    monkeypatch.setattr(results_reader, "_load_movement_metadata", lambda *a, **k: _NODE_META)
    monkeypatch.setattr(results_reader, "get_active_inpx_path", lambda: "unused.inpx")
    monkeypatch.setattr(results_reader, "get_active_modifications_dir", lambda: "unused")
    monkeypatch.setattr(results_reader, "get_active_dataset", lambda: {"revision": "test"})


def _movement(from_link: str, to_link: str, vehs, delay):
    row = {"OBJECT_ID": f"1-{from_link}-0.0-{to_link}-0.0", "VEHS_MEAN": vehs}
    if delay is not None:
        row["VEHDELAY_MEAN"] = delay
    return row


def test_incoming_delay_is_the_vehs_weighted_mean_of_real_movement_delays(node_meta):
    """North approach carries two movements: 100 veh @ 10 s and 300 veh @ 30 s.
    VEHS-weighted mean = (100*10 + 300*30) / 400 = 25.0 s -- NOT the arithmetic
    mean (20.0), and nothing at all to do with the old volume factor."""
    rows = results_reader._build_node_directions([
        _movement("10", "20", 100, 10.0),
        _movement("10", "30", 300, 30.0),
        _movement("20", "40", 200, 12.0),
    ])
    delays = rows[0]["incoming_delay_sec"]
    assert delays["north"] == pytest.approx(25.0)
    assert delays["east"] == pytest.approx(12.0)
    # No movement arrives from the south or the west: no measurement, no number.
    assert delays["south"] is None
    assert delays["west"] is None


def test_a_movement_with_delay_but_zero_volume_still_counts(node_meta):
    """Weighting mirrors _build_node_movement_metrics: weight falls back to 1.0
    so a measured delay is never dropped for want of a volume."""
    rows = results_reader._build_node_directions([
        _movement("10", "20", 0, 40.0),
    ])
    assert rows[0]["incoming_delay_sec"]["north"] == pytest.approx(40.0)


def test_no_measured_delay_emits_nothing_rather_than_a_fabricated_number(node_meta):
    """The no-data path: movements exist and carry volume, but no VEHDELAY."""
    rows = results_reader._build_node_directions([
        _movement("10", "20", 100, None),
        _movement("20", "40", 200, None),
    ])
    assert rows[0]["incoming_delay_sec"] == {
        "north": None, "east": None, "south": None, "west": None
    }
    # ...and the selected-node payload emits an EMPTY map, so the UI shows a
    # "no data" state instead of drawing a bar.
    assert results_reader._measured_direction_delays(rows[0]) == {}


def test_measured_direction_delays_passes_real_values_through(node_meta):
    rows = results_reader._build_node_directions([
        _movement("10", "20", 100, 10.0),
    ])
    normalized = results_reader._normalize_node_direction_row(
        node_id="1", node_name="Test Node", row=rows[0]
    )
    assert normalized["incoming_delay_sec"] == {
        "north": 10.0, "east": None, "south": None, "west": None
    }
    assert results_reader._measured_direction_delays(normalized) == {
        "north": 10.0, "east": None, "south": None, "west": None
    }


def test_normalizer_keeps_missing_delay_as_none_not_zero():
    """A missing measurement and a measured zero-second delay are different
    facts; _normalize_direction_map's `or 0.0` must not apply here."""
    normalized = results_reader._normalize_node_direction_row(
        node_id="1", node_name="Test Node", row={"incoming_delay_sec": {"north": 0.0}}
    )
    assert normalized["incoming_delay_sec"]["north"] == 0.0
    assert normalized["incoming_delay_sec"]["east"] is None


def test_the_invented_volume_factor_is_gone():
    """``0.75 + share * 0.5`` must not survive as EXECUTABLE code anywhere in
    the results reader.

    Checked against compiled code constants rather than the source text, so the
    docstring that explains what was removed does not count as a survival (and
    so a re-introduction under a different variable name is still caught)."""
    import types

    def literals(code):
        for const in code.co_consts:
            if isinstance(const, float):
                yield const
            elif isinstance(const, types.CodeType):
                yield from literals(const)

    for name, obj in vars(results_reader).items():
        if isinstance(obj, types.FunctionType) and obj.__module__ == results_reader.__name__:
            assert 0.75 not in set(literals(obj.__code__)), f"{name} still carries the invented factor"

    assert not hasattr(results_reader, "_estimate_direction_delays")
