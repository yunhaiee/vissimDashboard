"""Unit #59's arithmetic: the class trio moves together and the total survives.

Everything here is pure — no workspace, no network, no store. The rule these
tests pin is the user's own (``docs/phase2/DECISIONS.md``, 2026-08-24): the
승용차/버스/트럭 trio is adjusted proportionally and its mix is preserved, and
where no proportion exists the answer is a refusal rather than an invented one.

REFUTER ROUND 1 (F1) added the second half of that sentence: the refusal must
also be the RIGHT one. ``split_proportionally`` used to answer every undefined
case with a bare ``None``, and the single refusal built on it asserted that
every member carried zero — false for a group of ``500 / None / 200``. Each
cause now travels as its own token, and the tests below pin one per cause.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from services.agent import demand_class_split


def _members(*volumes: float | None) -> list[dict[str, object]]:
    return [
        {"vehicle_input_no": str(100 + index), "current_volume": volume}
        for index, volume in enumerate(volumes)
    ]


def _rows(*volumes: float | None, total: float) -> list[dict[str, object]]:
    """The split, asserting it exists — for the tests about a DEFINED split."""
    outcome = demand_class_split.split_proportionally(_members(*volumes), total)
    assert outcome.cause == demand_class_split.SPLIT_OK, outcome.cause
    assert outcome.rows is not None
    return outcome.rows


# ---------------------------------------------------------------------------
# The total, which is the whole point
# ---------------------------------------------------------------------------


def test_the_members_sum_to_the_adjusted_total_exactly():
    """THE property. The proposal's claim is "this entrance should carry T"; a
    split whose members sum to anything else proposes a total nobody approved,
    in the one number the approval card is about."""
    rows = _rows(500.0, 300.0, 200.0, total=1000.01)
    assert sum(Decimal(str(row["proposed_volume"])) for row in rows) == Decimal("1000.01")


def test_the_naive_per_member_rounding_this_replaces_would_have_drifted():
    """The defect the largest-remainder rule exists for, made visible.

    Rounding each member's exact share independently is the obvious
    implementation and it is WRONG on the demo's own trio — pinned here so
    nobody "simplifies" the apportionment back into the bug.
    """
    members = _members(500.0, 300.0, 200.0)
    naive = sum(
        Decimal(str(round(float(member["current_volume"]) * 1000.01 / 1000.0, 2)))
        for member in members
    )
    assert naive != Decimal("1000.01"), "fixture: this is the drift being prevented"

    rows = _rows(500.0, 300.0, 200.0, total=1000.01)
    assert sum(Decimal(str(row["proposed_volume"])) for row in rows) == Decimal("1000.01")


@pytest.mark.parametrize("seed", range(25))
def test_the_sum_survives_many_random_splits(seed: int):
    """The same property over shapes nobody chose by hand — member counts,
    lopsided mixes and totals that do not divide cleanly."""
    rng = random.Random(seed)
    count = rng.randint(2, 8)
    volumes = [round(rng.uniform(0.0, 900.0), 2) for _ in range(count)]
    if not any(volume > 0 for volume in volumes):
        volumes[0] = 1.0
    total = round(rng.uniform(0.01, 5000.0), 2)

    rows = _rows(*volumes, total=total)
    assert sum(Decimal(str(row["proposed_volume"])) for row in rows) == Decimal(str(total))
    assert all(row["proposed_volume"] >= 0 for row in rows)


# ---------------------------------------------------------------------------
# The mix, which is what the user actually decided
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(15))
def test_every_member_lands_within_one_hundredth_of_its_exact_share(seed: int):
    """"Proportional" is a measurable claim, so it is measured.

    Largest-remainder gives each member its floor plus at most one hundredth,
    so no member can be further than that from the exact proportional share —
    which is what "the mix is preserved" MEANS at a two-decimal quantum.
    """
    rng = random.Random(1000 + seed)
    volumes = [round(rng.uniform(1.0, 900.0), 2) for _ in range(rng.randint(2, 6))]
    total = round(rng.uniform(1.0, 4000.0), 2)
    weight_total = sum(volumes)

    rows = _rows(*volumes, total=total)
    for volume, row in zip(volumes, rows):
        exact = volume * total / weight_total
        assert abs(row["proposed_volume"] - exact) <= 0.01 + 1e-9, row


def test_an_unchanged_total_leaves_every_class_exactly_where_it_was():
    """Scaling by 1 is the identity, to the hundredth. A rule that nudged a
    class while proposing no change at all would be moving demand for the
    rounding's own sake."""
    rows = _rows(569.4, 120.0, 60.6, total=750.0)
    assert [row["proposed_volume"] for row in rows] == [569.4, 120.0, 60.6]
    assert [row["delta"] for row in rows] == [0.0, 0.0, 0.0]


def test_the_current_share_reported_is_the_share_the_split_used():
    """The card shows a share; it has to be the one the arithmetic ran on, not
    a second computation that could disagree with the numbers beside it."""
    rows = _rows(500.0, 300.0, 200.0, total=2000.0)
    assert [row["current_share"] for row in rows] == [0.5, 0.3, 0.2]
    assert [row["proposed_volume"] for row in rows] == [1000.0, 600.0, 400.0]


# ---------------------------------------------------------------------------
# Where there is no answer, EACH cause says which one it is (refuter F1)
# ---------------------------------------------------------------------------


def test_every_member_at_zero_is_named_ALL_ZERO():
    """No mix to preserve and no proportion to apply. Nobody invents a
    division, and the cause is the one this branch actually established."""
    outcome = demand_class_split.split_proportionally(_members(0.0, 0.0, 0.0), 900.0)
    assert outcome.rows is None
    assert outcome.cause == demand_class_split.SPLIT_ALL_ZERO


def test_one_unknown_member_is_named_UNKNOWN_not_ALL_ZERO():
    """THE F1 REGRESSION, executed.

    Members 500/None/200: two of the three plainly carry traffic, so the
    all-zero cause is measurably false about them, and the real problem — one
    unstated volume — is what the reader needs told. Unknown is not zero:
    treating it as zero would drop that class out of the very mix the rule
    exists to preserve and write its volume to zero on approval.
    """
    outcome = demand_class_split.split_proportionally(_members(500.0, None, 200.0), 900.0)
    assert outcome.rows is None
    assert outcome.cause == demand_class_split.SPLIT_UNKNOWN_MEMBER_VOLUME
    assert outcome.cause != demand_class_split.SPLIT_ALL_ZERO

    assert demand_class_split.group_total(_members(500.0, None, 200.0)) is None
    assert demand_class_split.group_total(_members(500.0, 300.0, 200.0)) == 1000.0


def test_a_negative_volume_is_named_NEGATIVE_and_not_scaled():
    """A negative weight has no meaning as a share of the mix, and scaling by it
    would hand a member a negative proposed volume for the verb to reject at the
    door — an incoherent card instead of a named refusal."""
    outcome = demand_class_split.split_proportionally(_members(500.0, -100.0), 900.0)
    assert outcome.rows is None
    assert outcome.cause == demand_class_split.SPLIT_NEGATIVE_MEMBER_VOLUME


def test_an_unknown_member_outranks_a_negative_one_deterministically():
    """Two faults at once still produce ONE named cause, and always the same
    one — a refusal whose name depended on iteration order would be back to
    guessing."""
    both = demand_class_split.split_proportionally(_members(None, -5.0, 200.0), 900.0)
    assert both.cause == demand_class_split.SPLIT_UNKNOWN_MEMBER_VOLUME


def test_no_members_and_a_bad_total_have_their_own_names():
    """The remaining causes are named rather than folded into a neighbour's
    sentence — the whole lesson of F1."""
    empty = demand_class_split.split_proportionally([], 100.0)
    assert (empty.rows, empty.cause) == (None, demand_class_split.SPLIT_NO_MEMBERS)

    negative_total = demand_class_split.split_proportionally(_members(500.0, 200.0), -10.0)
    assert negative_total.cause == demand_class_split.SPLIT_INVALID_TOTAL

    nan_total = demand_class_split.split_proportionally(_members(500.0, 200.0), float("nan"))
    assert nan_total.cause == demand_class_split.SPLIT_INVALID_TOTAL


def test_a_boolean_is_not_a_volume():
    """``True`` is a float to Python and nonsense as a weight. A 1.0 conjured
    out of a flag is exactly the invented mix this module refuses to produce."""
    assert demand_class_split.group_total(_members(True, 300.0)) is None
    outcome = demand_class_split.split_proportionally(_members(True, 300.0), 900.0)
    assert outcome.cause == demand_class_split.SPLIT_UNKNOWN_MEMBER_VOLUME


def test_every_cause_is_in_the_published_tuple():
    """A cause the caller cannot enumerate is a cause somebody will forget to
    render, which is how F1 happened in the first place."""
    seen = {
        demand_class_split.split_proportionally(_members(0.0, 0.0), 5.0).cause,
        demand_class_split.split_proportionally(_members(None, 1.0), 5.0).cause,
        demand_class_split.split_proportionally(_members(-1.0, 1.0), 5.0).cause,
        demand_class_split.split_proportionally([], 5.0).cause,
        demand_class_split.split_proportionally(_members(1.0, 1.0), -5.0).cause,
        demand_class_split.split_proportionally(_members(1.0, 1.0), 5.0).cause,
    }
    assert seen == set(demand_class_split.SPLIT_CAUSES)


# ---------------------------------------------------------------------------
# The declared total vs the members' own sum (refuter F4)
# ---------------------------------------------------------------------------


def test_a_declared_total_that_contradicts_the_members_is_measurable():
    """The two numbers arrive independently on the agent path, so the product
    has to be able to SEE that they disagree before it scales one of them."""
    members = _members(500.0, 300.0, 200.0)
    assert demand_class_split.member_total_disagreement(members, 1000.0) == 0.0
    assert demand_class_split.member_total_disagreement(members, 400.0) == 600.0
    # Rounding is tolerated; a real contradiction is not.
    assert demand_class_split.member_total_disagreement(members, 1000.02) == pytest.approx(0.02)
    assert demand_class_split.member_total_tolerance(members) == pytest.approx(0.03, abs=1e-6)


def test_incomparable_is_not_the_same_finding_as_agreeing():
    """``None`` means "these cannot be compared", never "they match" — an
    unknown member or a missing total is somebody else's named refusal."""
    assert demand_class_split.member_total_disagreement(_members(500.0, None), 500.0) is None
    assert demand_class_split.member_total_disagreement(_members(500.0), None) is None
    assert demand_class_split.member_total_disagreement([], 500.0) is None


# ---------------------------------------------------------------------------
# Determinism — two runs, one answer
# ---------------------------------------------------------------------------


def test_the_leftover_hundredth_goes_somewhere_decided_not_somewhere_incidental():
    """Ties are broken by a TOTAL order, so the same trio splits the same way
    every time — which is what lets an approval card, an audit and a
    re-derivation quote one another."""
    # Three equal classes and one hundredth that cannot be divided by three:
    # exactly ONE member must come out a hundredth above the others.
    members = _members(100.0, 100.0, 100.0)
    first = demand_class_split.split_proportionally(members, 300.01).rows
    second = demand_class_split.split_proportionally(list(reversed(members)), 300.01).rows
    assert first is not None and second is not None
    assert sum(Decimal(str(row["proposed_volume"])) for row in first) == Decimal("300.01")

    winners = [row["vehicle_input_no"] for row in first if row["delta"] > 0]
    assert len(winners) == 1, first
    # Same members, opposite input order: which one receives the leftover is
    # decided by the tie-break, not by the list position it happened to sit in,
    # so the two runs agree about WHICH input got it.
    assert winners == [row["vehicle_input_no"] for row in second if row["delta"] > 0]


def test_input_numbers_sort_numerically_so_9_is_below_10():
    """A plain string sort puts ``"9"`` above ``"10"``, and this order decides
    which member receives a leftover hundredth."""
    assert demand_class_split.input_sort_key("9") < demand_class_split.input_sort_key("10")
    assert demand_class_split.input_sort_key("113") < demand_class_split.input_sort_key("bus")
