"""Splitting ONE demand adjustment across a link's vehicle-class inputs (#59).

THE DECISION THIS IMPLEMENTS
----------------------------
``docs/phase2/DECISIONS.md``, 2026-08-24, the user's own words: **(a) 셋을 비례로
함께 조정 — 승용차:버스:트럭 구성비 유지**. Every approach link in the real client
model carries a three-class vehicle-input trio (승용차/버스/트럭), so
``reality_check_targets`` refused network-wide with
``MULTIPLE_VEHICLE_INPUT_CANDIDATES`` — not because the LINK was ambiguous but
because the CLASS was. The user decided the class question: the trio moves
together and its mix is preserved.

This module is that rule's arithmetic, and only that. No I/O, no COM, no
network read, no opinion about WHICH link — ``reality_check_targets`` owns the
grouping and ``fixes`` owns the proposal.

WHY THE ROUNDING RULE IS NAMED RATHER THAN INCIDENTAL
-----------------------------------------------------
A per-member ``round(w_i * T / W, 2)`` does NOT sum to ``T``. On the demo's own
trio (500/300/200 scaled to 1000.01) the naive rounding drifts the total by a
hundredth, and a drifting total is not a rounding detail here — the whole
proposal is "the node's entrance total should become T". A split whose members
sum to something else proposes a total nobody approved, silently, in the one
number the approval card is about.

So the split is a LARGEST-REMAINDER (Hamilton) apportionment over integer
hundredths (:data:`SPLIT_RULE`): every member takes its floor, and the
hundredths left over go to the largest fractional remainders, one each. The
members then sum to ``T`` EXACTLY, by construction rather than by luck, and no
member is more than one hundredth from its exact proportional share.

Ties are broken deterministically and the order is TOTAL — larger remainder
first, then larger current volume, then the input number (numerically where it
is a number). Two runs over the same trio produce the same split, which is what
lets an approval card, an audit DOCX and a re-derivation quote one another.

WHAT IS UNDEFINED, AND IS SAID RATHER THAN INVENTED
----------------------------------------------------
The weights ARE the members' current volumes. Several distinct cases have no
proportional answer, and each returns its OWN named cause
(:data:`SPLIT_CAUSES`) rather than a bare ``None``:

* **A weight this system does not know**
  (:data:`SPLIT_UNKNOWN_MEMBER_VOLUME`). An input whose volume the ``.inpx``
  never stated has an UNKNOWN volume, and unknown is not zero. Treating it as
  zero would silently drop that class out of the mix the rule exists to
  preserve.
* **Every weight zero** (:data:`SPLIT_ALL_ZERO`). There is no mix to preserve
  and no proportion to apply; any split would be this module's invention
  wearing the rule's name.
* **A negative weight** (:data:`SPLIT_NEGATIVE_MEMBER_VOLUME`), which is not a
  share of any mix and would scale into a negative proposed volume.
* **Nothing to split, or a total that is not a number**
  (:data:`SPLIT_NO_MEMBERS`, :data:`SPLIT_INVALID_TOTAL`).

WHY THE CAUSE IS RETURNED AND NOT INFERRED (measured, refuter round 1)
-----------------------------------------------------------------------
This function returned a bare ``None`` for every one of those causes, and
``fixes`` mapped that single ``None`` onto ONE refusal whose text asserts "every
one of them currently carries zero". For members ``500 / None / 200`` that
sentence is measurably FALSE: two of the three carry traffic, and the actual
problem — one unstated volume — went unnamed while the reader was told something
untrue about the other two. A refusal that misnames its own cause is worse than
no refusal, because it sends the reader to fix a thing that is not broken.

So the cause travels WITH the answer. ``fixes`` maps each token to its own named
refusal; no caller has to re-derive from the inputs why the split failed, and no
cause can be silently folded into another's sentence.

None of these is ever a licence to fall back to an equal split. That is the
whole point of naming them.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, NamedTuple, Sequence

#: The rounding rule, on the wire, so an approval card and an audit can say
#: which apportionment produced these numbers rather than implying one.
SPLIT_RULE = "LARGEST_REMAINDER_BY_CURRENT_VOLUME"

# ---------------------------------------------------------------------------
# Why a split does or does not exist — one token per cause
# ---------------------------------------------------------------------------

#: The split was computed.
SPLIT_OK = "SPLIT_OK"
#: Every member's current volume is zero: no mix, no proportion.
SPLIT_ALL_ZERO = "SPLIT_ALL_ZERO"
#: At least one member's current volume is unknown. Unknown is not zero, and
#: this is the cause that spent round 1 wearing :data:`SPLIT_ALL_ZERO`'s name.
SPLIT_UNKNOWN_MEMBER_VOLUME = "SPLIT_UNKNOWN_MEMBER_VOLUME"
#: At least one member's current volume is negative — not a share of any mix.
SPLIT_NEGATIVE_MEMBER_VOLUME = "SPLIT_NEGATIVE_MEMBER_VOLUME"
#: No members were handed over at all.
SPLIT_NO_MEMBERS = "SPLIT_NO_MEMBERS"
#: The total to apportion is not a finite, non-negative number.
SPLIT_INVALID_TOTAL = "SPLIT_INVALID_TOTAL"

SPLIT_CAUSES = (
    SPLIT_OK,
    SPLIT_ALL_ZERO,
    SPLIT_UNKNOWN_MEMBER_VOLUME,
    SPLIT_NEGATIVE_MEMBER_VOLUME,
    SPLIT_NO_MEMBERS,
    SPLIT_INVALID_TOTAL,
)

#: How far the members' own sum may sit from a SEPARATELY DECLARED group total
#: before the two are treated as contradicting each other. Both sides are
#: quantised to hundredths, so a legitimate declaration can differ only by each
#: member's own rounding — never by more.
MEMBER_TOTAL_TOLERANCE_PER_MEMBER = 0.01


class SplitOutcome(NamedTuple):
    """``(rows, cause)`` — the split, or the NAMED reason there is none.

    ``rows`` is ``None`` for every cause other than :data:`SPLIT_OK`, and the
    cause is never ``SPLIT_OK`` when ``rows`` is ``None``. Callers branch on
    ``rows is None`` and then render the cause; they never re-derive it.
    """

    rows: list[dict[str, Any]] | None
    cause: str

#: Every proposed volume is quantised to hundredths, the same two decimals
#: ``fixes._round`` states every other proposed number in. The apportionment
#: runs in INTEGER hundredths so "the members sum to the total" is integer
#: equality rather than a float comparison that happens to pass.
QUANTUM_PER_UNIT = 100


def _number(value: Any) -> float | None:
    """A finite float, or ``None`` — ``fixes._number``'s rule, restated.

    Booleans are excluded deliberately: ``True`` is a perfectly good ``float``
    to Python and a nonsense volume, and a weight of 1.0 conjured out of a flag
    would be exactly the invented split this module refuses to make.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def input_sort_key(value: Any) -> tuple[int, int, str]:
    """A TOTAL order over vehicle-input numbers, numeric where they are numbers.

    ``"9"`` must sort below ``"10"`` — a plain string sort puts it above, and
    the difference decides which member receives a leftover hundredth. A
    non-numeric id still sorts, after every numeric one, so the order is total
    over anything a network can hold rather than raising on it.
    """
    text = str(value)
    try:
        return (0, int(text), text)
    except (TypeError, ValueError):
        return (1, 0, text)


def member_volumes(members: Sequence[Mapping[str, Any]]) -> list[float | None]:
    """Each member's CURRENT volume, ``None`` preserved as unknown."""
    return [_number(member.get("current_volume")) for member in members]


def group_total(members: Sequence[Mapping[str, Any]]) -> float | None:
    """The group's current total, or ``None`` if ANY member's volume is unknown.

    ``None`` propagates rather than being summed around: a total that silently
    omitted one class would be a smaller number presented as the group's whole
    demand, and every downstream ratio would inherit the error. ``fixes`` turns
    this ``None`` into its existing ``EVIDENCE_INSUFFICIENT`` refusal, which is
    already the honest answer for a volume the network never stated.
    """
    if not members:
        return None
    volumes = member_volumes(members)
    if any(volume is None for volume in volumes):
        return None
    return float(sum(volume or 0.0 for volume in volumes))


def member_total_disagreement(
    members: Sequence[Mapping[str, Any]], declared_total: Any
) -> float | None:
    """How far a SEPARATELY DECLARED group total sits from the members' own sum.

    ``None`` when the two are not comparable at all — no members, an unknown
    member volume, or a declared total that is not a number — because "we cannot
    compare them" is not the same finding as "they agree", and only the caller
    knows which of its own refusals covers the first.

    Exists because the two numbers arrive INDEPENDENTLY on the agent path: an
    LLM fills ``vehicle_input_current_volume`` (the group's total) and
    ``vehicle_input_group_members`` (the classes) as separate fields, so nothing
    structurally forces them to describe the same link. The derivation path
    computes the total FROM the members and always agrees with itself.
    """
    if not members:
        return None
    volumes = member_volumes(members)
    if any(volume is None for volume in volumes):
        return None
    declared = _number(declared_total)
    if declared is None:
        return None
    return abs(float(sum(volume or 0.0 for volume in volumes)) - declared)


def member_total_tolerance(members: Sequence[Mapping[str, Any]]) -> float:
    """The most :func:`member_total_disagreement` may be and still be rounding.

    One hundredth PER MEMBER: every member's volume is quantised to hundredths,
    so a truthfully declared total can differ from their sum by each member's
    own rounding and by nothing else.
    """
    return MEMBER_TOTAL_TOLERANCE_PER_MEMBER * max(len(members), 1) + 1e-9


def split_proportionally(
    members: Sequence[Mapping[str, Any]], adjusted_total: float
) -> SplitOutcome:
    """Apportion ``adjusted_total`` across ``members`` by current volume.

    Returns :class:`SplitOutcome`. On success ``rows`` is one row per member —
    ``vehicle_input_no``, ``current_volume``, ``proposed_volume``, ``delta`` and
    ``current_share`` — in the members' own order, so the card renders them in
    the order the picker offered them, and ``cause`` is :data:`SPLIT_OK`.

    Where the split is undefined ``rows`` is ``None`` and ``cause`` NAMES which
    of the causes in :data:`SPLIT_CAUSES` applies. It is never a substitute
    split, and never a cause the caller has to guess: see the module docstring
    for the measured reason a single anonymous ``None`` was a defect.

    The members sum to ``adjusted_total`` exactly (see :data:`SPLIT_RULE`).
    """
    if not members:
        return SplitOutcome(None, SPLIT_NO_MEMBERS)
    volumes = member_volumes(members)
    if any(volume is None for volume in volumes):
        return SplitOutcome(None, SPLIT_UNKNOWN_MEMBER_VOLUME)
    weights = [float(volume or 0.0) for volume in volumes]
    if any(weight < 0 for weight in weights):
        # A negative volume is not a mix this rule can preserve, and scaling by
        # it would hand some member a NEGATIVE proposed volume that the verb's
        # own schema would then reject at the door. Undefined, said here.
        return SplitOutcome(None, SPLIT_NEGATIVE_MEMBER_VOLUME)

    target = _number(adjusted_total)
    if target is None or target < 0:
        return SplitOutcome(None, SPLIT_INVALID_TOTAL)

    total_weight = float(sum(weights))
    if total_weight <= 0:
        # Checked AFTER the unknown/negative cases above, so "every one of them
        # carries zero" is a statement this branch has actually established
        # rather than the residue of everything else failing.
        return SplitOutcome(None, SPLIT_ALL_ZERO)

    # INTEGER HUNDREDTHS from here down. Every comparison below is between
    # integers, which is what makes "they sum to the total" a fact rather than
    # a float coincidence.
    total_hundredths = int(round(target * QUANTUM_PER_UNIT))
    exact = [weight * total_hundredths / total_weight for weight in weights]
    floors = [int(math.floor(value)) for value in exact]
    remainder = total_hundredths - sum(floors)

    if remainder > 0:
        # Largest fractional remainder first; ties by larger current volume,
        # then by input number. Total and deterministic — see the docstring.
        order = sorted(
            range(len(members)),
            key=lambda index: (
                -(exact[index] - floors[index]),
                -weights[index],
                input_sort_key(members[index].get("vehicle_input_no")),
            ),
        )
        for index in order[:remainder]:
            floors[index] += 1

    rows: list[dict[str, Any]] = []
    for index, member in enumerate(members):
        proposed = round(floors[index] / QUANTUM_PER_UNIT, 2)
        current = round(weights[index], 2)
        rows.append(
            {
                "vehicle_input_no": str(member.get("vehicle_input_no") or ""),
                "current_volume": current,
                "proposed_volume": proposed,
                "delta": round(proposed - current, 2),
                # The mix being preserved, stated as the number it is, so a
                # reader can check preservation against the proposed volumes
                # instead of taking the word "proportional" on trust.
                "current_share": round(weights[index] / total_weight, 6),
            }
        )
    return SplitOutcome(rows, SPLIT_OK)


__all__ = [
    "MEMBER_TOTAL_TOLERANCE_PER_MEMBER",
    "QUANTUM_PER_UNIT",
    "SPLIT_ALL_ZERO",
    "SPLIT_CAUSES",
    "SPLIT_INVALID_TOTAL",
    "SPLIT_NEGATIVE_MEMBER_VOLUME",
    "SPLIT_NO_MEMBERS",
    "SPLIT_OK",
    "SPLIT_RULE",
    "SPLIT_UNKNOWN_MEMBER_VOLUME",
    "SplitOutcome",
    "group_total",
    "input_sort_key",
    "member_total_disagreement",
    "member_total_tolerance",
    "member_volumes",
    "split_proportionally",
]
