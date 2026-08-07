"""Fixed-time signals for a genesis draft — controllers, groups, heads, detectors.

Roadmap 3.1, **Genesis (b) 신호**, the SCATS half. (The Daejeon half — parsing
Daejeon signal PDFs — is blocked on client data and is not here.)

What this module is
-------------------
Unit 1.6 (``services.dataimport.fixed_time``) converts an adaptive controller's
observed history into a :class:`~services.dataimport.fixed_time.FixedTimePlan`:
per-phase start offsets, rounded phase times, greens, intergreens and a cycle.
It stops exactly there, and says so in writing — its own "Deliberately NOT
implemented" list records that **signal-group green windows** are left to
roadmap 3.1 because "Appendix A gives no rule for laying signal groups out
inside the derived phases, and the layout depends on the target network".

This module is that step. It takes a plan and a draft node, and produces the
Vissim objects a signalised network needs:

* a ``FIXEDTIME`` **signal controller** carrying a Vissig fixed-time program,
* **signal groups**, one per approach (the layout decision — argued below),
* **signal heads**, one per approach lane at the stop line (§5.4.2.2),
* **detectors**, on §5.4.2.6's no-plans fallback geometry (§5.4.2.6),
* and §5.4.2 **compliance items** in the draft's own three-state style.

It is additive by construction. An unsignalised draft never calls it, and the
emitter writes not one byte differently for one — pinned by
``test_genesis_signals.py::test_unsignalised_draft_bytes_are_unchanged``.

Nothing here imports Vissim or touches COM. Everything is offline and pure
python, like the rest of ``services.genesis``.


The signal-group layout decision — one group per APPROACH
---------------------------------------------------------
This is the decision 1.6 explicitly declined to make, so it is argued rather
than asserted.

**The rule.** One signal group per **approach** (one per link entering the
node), numbered 1..N in ascending approach-link-id order. Not one per turn, and
not one per phase.

**Why not one per turn.** A per-turn layout means each movement is separately
controlled, and asserting that requires knowing which turns are separately
signalled — i.e. which right turn is protected and which filters. The neutral
model does not carry that: ``dataimport.schema.TurnType``'s own docstring states
the enum is geometric and carries no opposed/permissive semantics, which is the
very reason 1.6 cannot check A.4's yellow-trap rule and says so on every run
(``fixed_time_yellow_trap_not_checkable``). A per-turn layout would therefore
invent control that is not in the data — arrows on turns nobody said were
arrowed.

**Why not one per phase.** A signal group is a thing heads point at; a phase is
a slice of the cycle. Collapsing them makes every approach sharing a phase
indistinguishable, and §5.4.2.1 requires group numbers to survive a comparison
against SCATS, which a phase-numbered layout cannot support.

**Why per-approach is the granularity the evidence supports.** §5.4.2.2
(:data:`CIT_S5422_SIGNAL_HEADS`) says heads go "on links or on connectors where
partially controlled turns exist" — links are the DEFAULT and connectors are the
exception for partially-controlled turns, which is exactly the property the
neutral model cannot express. So every head this module writes goes on an
approach LINK, and one group per approach is precisely the granularity that head
placement can distinguish. Layout and head placement agree instead of one
promising a resolution the other cannot deliver.

**What this layout is NOT.** It is not §5.4.2.1 compliance.
:data:`CIT_S5421_SIGNAL_GROUPS` requires groups to be "defined in accordance
with the site LM plan and SCATS information" and group numbers to "match the
signal groups defined in SCATS". A genesis draft has neither: the LM plan is a
drawing (1.6's own note: "Both are drawings; neither is machine-readable"), and
the numbering here is derived from OSM link ids. So the report carries
§5.4.2.1 as **NOT CHECKABLE** with that named reason, never as applied. The
layout is a runnable placeholder that a reviewer must reconcile against the LM
plan, and the report says so in ko/en/ja.

**The one fact this module refuses to guess.** Which approach runs in which
phase is a site fact. Nothing links a SCATS phase id to a compass bearing: the
plan's phases are named by the controller, the draft's approaches are named by
OSM way. So the approach→phase assignment is a required ARGUMENT
(``approach_phases``), and is validated exhaustively — every approach assigned,
every plan phase used, no unknown link, no unknown phase. Absent or
contradictory, the module refuses with a named code and produces nothing. This
is the house rule applied where it bites: a refused edit with a clear reason
beats a completed edit with wrong numbers.


Worked example — 1.6's own A.2 composite, all the way to red/green windows
--------------------------------------------------------------------------
Take the composite 1.6's tests use: **cycle 78 s, greens 35/21/6, offsets
0/41/67**. Those imply the phase times and intergreens, and every number below
is a subtraction a reviewer can redo on paper::

    phase   offset   green   next offset   phase time   intergreen
    A       0        35      41            41           41 - 35 = 6
    B       41       21      67            26           26 - 21 = 5
    C       67       6       78 (cycle)    11           11 - 6  = 5
                                           --
                                           78 = the plan cycle

Give the node four approaches — links 1, 3, 5, 7 — and the assignment
``{1: A, 3: A, 5: B, 7: C}`` (a standard crossroads: two opposing approaches
share the main phase). With A.3 ambers of 3 s the all-reds are ``6-3=3``,
``5-3=2``, ``5-3=2``, and the layout produces four signal groups::

    SG   approach   phase   green window    amber        red begins
    1    link 1     A       0 → 35          35 → 38      38
    2    link 3     A       0 → 35          35 → 38      38
    3    link 5     B       41 → 62         62 → 65      65
    4    link 7     C       67 → 73         73 → 76      76

which is emitted as Vissig commands in milliseconds — green is
``<cmd display="3" begin=offset*1000>`` and red is
``<cmd display="1" begin=(offset+green+amber)*1000>``, with
``<fixedstate display="4" duration=amber*1000>``::

    SG 1, 2:  cmd 3 @ 0      cmd 1 @ 38000   fixedstate 4 = 3000
    SG 3:     cmd 3 @ 41000  cmd 1 @ 65000   fixedstate 4 = 3000
    SG 4:     cmd 3 @ 67000  cmd 1 @ 76000   fixedstate 4 = 3000
    prog cycletime = 78000

Read back, ``services.signal_parser`` computes each green as
``red_begin - green_begin - amber``, which returns ``38-0-3 = 35``,
``65-41-3 = 21`` and ``76-67-3 = 6`` — **the plan's own greens, exactly** — and a
cycle of ``78000 // 1000 = 78``. That identity is not a coincidence to be hoped
for; it is why the red command is placed at ``offset + green + amber`` rather
than at the end of the phase, and ``test_a2_composite_round_trips_to_the_plans_
own_numbers`` asserts every one of those figures.


What the repo's own readers recover — two named gaps
-----------------------------------------------------
Measured against the emitted file, not assumed. Both gaps are **pre-existing
properties of the read-only parsers**, not consequences of this emission, and
both are pinned by tests so they stay visible.

``services.signal_parser`` has TWO read paths and they use the phrase "green
window" differently:

* :func:`~services.signal_parser.parse_signal_controllers` (the phase view)
  computes ``red_begin - green_begin - amber``, so it returns the plan's green
  exactly. This is the path the round-trip test asserts against.
* :func:`~services.signal_parser.parse_signal_programs` (the split view)
  reports ``green_end_s = red_begin`` **without** subtracting the amber it
  reports alongside. For the worked example it returns 38/65/76 where the greens
  end at 35/62/73. Nothing is lost — the true green is
  ``green_end_s - green_start_s - amber_s`` — but a consumer that reads
  ``green_end_s`` as "when green ends" is reading green + amber. Pinned by
  ``test_the_split_read_path_reports_green_plus_amber``.

**Minimum green does not come back at all** from a FIXEDTIME controller.
``_parse_fixedtime_blob_programs`` hard-codes ``"min_green_s": None`` with its
own stated reason ("the blob's per-SG ``defaultDurations`` semantics are
unverified — an unproven datum is omitted, never guessed"). That reader looks
only at the BLOB; this module writes ``minGreen`` on the outer
``<signalGroup>``, which is where Vissim's own save puts it (LHT writes
``minGreen="0"`` on its fixed-time groups and ``minGreen="5"`` on its VAP one)
and where the consultant network puts it too.

The field is therefore **kept, not shrunk**. Shrinking is the rule when the
emission would contain something the readers cannot confirm; here the opposite
holds — ``minGreen`` is Vissim-authored schema AND §5.4.2.1 explicitly requires
minimum greens to be "directly input from controller/SCATS information". Dropping
a guideline-mandated field because one of two internal read paths declines to
report it would trade a real compliance defect for a cosmetic round-trip win.
The gap is named here, and ``test_min_green_is_written_but_not_recovered_by_the_
split_reader`` proves both halves: the attribute IS in the file, and the reader
does NOT return it.

Where each number comes from — nothing here is invented
-------------------------------------------------------
* Every **timing** (cycle, offset, green, intergreen, min green) is read off the
  :class:`FixedTimePlan`. This module does no traffic-engineering arithmetic of
  its own: it re-slices the plan's own numbers into Vissim's red/green view and
  refuses when a slice would need a number the plan does not carry.
* Every **geometry** offset is either the draft's own (the stop line is the
  approach link's end, which is the same value ``draft.py`` already writes as the
  connector's ``from_pos_m``) or a cited §5 default (§5.4.2.6's 2.0 m / 4.5 m).
* Every **Vissim schema default** is copied from a network Vissim itself saved,
  in :mod:`services.genesis.schema`, each one naming its source file. Emitter
  v1's lesson is law here: an attribute set invented from a hand-written
  exemplar produced files Vissim refused to load, with no ``.err`` written.

ASSUMPTIONS — where this module had to choose
---------------------------------------------
S1. **The stop line is the approach link's end**, so a head's ``pos`` is the
    link's length. §5.4.2.2 prescribes NO head offset — the "2.0 m" often quoted
    at it is §5.4.2.6's DETECTOR setback, and the extract says so in bold. The
    draft's geometry puts the link end at the node boundary, which is where the
    stop line is, so no offset is applied and none is invented. See
    :data:`CIT_S5422_SIGNAL_HEADS`.
S2. **Red/amber is zero.** Vissim's sequence 3 has a red/amber display and LHT
    writes 2 s for it; Australian practice has no red/amber interval, and A.3
    defines intergreen as early cut-off + yellow + all-red with no red/amber
    term. Writing LHT's 2 s would insert 2 s of a display the plan does not
    contain. Overridable: :attr:`SignalSettings.red_amber_s`.
S3. **All-red is the intergreen remainder**, ``intergreen - amber``. When the
    source stated the intergreen directly (so it may include A.3's early
    cut-off), that early cut-off lands in the all-red bucket; the green is
    unaffected either way, and ``signals_early_cut_off_in_all_red`` says so.
S4. **``localNo`` is the head's own number.** LHT's local numbers are site
    numbers from a real installation; this draft has no site numbering, so the
    field repeats ``no`` rather than inventing a plate number.
S5. **Detectors are emitted for a FIXEDTIME controller even though §5.4.2.6's
    own opening sentence scopes them to SCATSIM/VAP.** They are inert for this
    program and are written so a later SCATSIM/VAP swap has them; every run says
    so via ``signals_detectors_not_required_for_fixed_time``.
S6. **A detector's ``pos`` anchors its UPSTREAM end — MEASURED 2026-08-07**
    (live check, ``backend/tools/genesis_signals_live_check.py``, evidence in
    ``runtime/genesis-signals-live-check/``). The emitted position
    ``stop line − setback − length`` (100 → 93.5 in the worked example) is
    therefore correct as shipped; nothing changed but this paragraph.

    §5.4.2.6 fixes the detector's *downstream* end at 2.0 m back from the head.
    Vissim stores one ``pos`` plus a ``Length``, exposes no end-anchor
    attribute (43-code COM census: no PosEnd/EndPos/StartPos/Pos2), and a
    saved file is ambiguous — so the proof came from Vissim's own validation:
    a 4.5 m detector at ``pos = L − 1`` is REFUSED ("does not fit on link …
    (271.264 m)") while ``pos = 1.0`` is accepted, i.e. Vissim checks
    ``pos + length ≤ link length`` and never ``pos − length ≥ 0`` — the body
    runs downstream FROM ``pos``. Corroborated by a census of all 826
    PTV-authored detectors in the Examples library: 0 would overrun their
    link's end under the upstream reading, 99 would precede its start under
    the downstream one.

    The inertness note stands: for a FIXEDTIME controller the detectors are
    inert (S5), and §5.4.2.6 says real site plans must replace this fallback
    before any SCATSIM/VAP swap makes them live.
"""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from ..dataimport.base import ImportIssue, IssueSeverity
from ..dataimport.fixed_time import FixedTimePlan
from . import schema
from .draft import (
    REASON_NO_SIGNAL_PLAN,
    REASON_OUTSIDE_EMITTED_SUBSET,
    REASON_REQUIRES_SITE_OBSERVATION,
    ComplianceItem,
    ComplianceState,
    DraftLink,
    DraftNetwork,
    DraftNode,
)
from .osm import EXTRACT

# ---------------------------------------------------------------------------
# Citations — §5.4.2, the signalised-intersection subsection
#
# Same contract as ``draft.py``: every constant names the extract file and
# quotes it verbatim, so the citation test can mechanically compare each
# fragment against the document. A rule with no traceable line does not get
# written. The locator names a § and a doc page and never an in-text figure
# label — §5 mislabels nine of its own Tables as Figures.
# ---------------------------------------------------------------------------

CIT_S542_SIGNAL_CONTROL_TYPE = (
    f"MRWA OMG v2.0 §5.4.2 Signalised Intersections, doc p.201 ({EXTRACT}) — "
    "'The type of signal control used for the modelling should be agreed with Main Roads. "
    "... Signal offsets should be correctly modelled using the information from SCATS "
    "(where applicable).'"
)

CIT_S5421_SIGNAL_GROUPS = (
    f"MRWA OMG v2.0 §5.4.2.1 Traffic Signal Groups, doc p.201 ({EXTRACT}) — "
    "'Signal groups in the model must be defined in accordance with the site LM plan and "
    "SCATS information for existing signals (refer to Appendix A). Signal group numbers in "
    "Vissim models must match the signal groups defined in SCATS.'"
)

CIT_S5421_TIMINGS_UNCHANGED = (
    f"MRWA OMG v2.0 §5.4.2.1 Traffic Signal Groups, doc p.201 ({EXTRACT}) — "
    "'The signal group/phase timings (minimum greens and yellow + all reds) should be "
    "directly input from controller/SCATS information and should not be changed.'"
)

CIT_S5422_SIGNAL_HEADS = (
    f"MRWA OMG v2.0 §5.4.2.2 Signal Heads, doc p.201 ({EXTRACT}) — "
    "'Signal heads should generally be set on links or on connectors where partially "
    "controlled turns exist. To ensure that signal heads are not mismanaging traffic or "
    "being ignored, care should be taken in the setting of signal heads relative to the "
    "adjacent lanes and link/connectors.'"
)

CIT_S5423_FIXED_TIME = (
    f"MRWA OMG v2.0 §5.4.2.3 Fixed Time Signals, doc p.201 ({EXTRACT}) — "
    "'Fixed time signals should generally be used where there is minimal variation of phase "
    "sequences and timing throughout the assessment period. IDM data or SCATS history files "
    "should be used to determine the average phase lengths for the intersection(s) during "
    "the modelled time periods.'"
)

CIT_S5423_PEDESTRIANS = (
    f"MRWA OMG v2.0 §5.4.2.3 Fixed Time Signals, doc p.201 ({EXTRACT}) — "
    "'Pedestrians should be modelled where there are high levels of pedestrian movements "
    "which may result in delays to the corridor due to crossing movements or signal "
    "controls.'"
)

CIT_S5426_DETECTOR_FALLBACK = (
    f"MRWA OMG v2.0 §5.4.2.6 Detectors, doc p.202 ({EXTRACT}) — "
    "'If there is no information provided to indicate the detector size and location, signal "
    "detectors for vehicle detections should be modelled 2.0m back from signal heads and "
    "4.5m in length at intersection approaches.'"
)

CIT_S5426_DETECTORS_FROM_PLANS = (
    f"MRWA OMG v2.0 §5.4.2.6 Detectors, doc p.202 ({EXTRACT}) — "
    "'Signal detectors generally need to be set when SCATSIM or VAP is adopted for signal "
    "control in the model. Signal detectors in the model should reflect existing or design "
    "information (for example, traffic signal plans or operation sheets).'"
)


# ---------------------------------------------------------------------------
# §5.4.2.6's two published numbers
#
# These are the ONLY engineering numbers this module owns; everything else is
# the plan's or the draft's. They are §5.4.2.6's, they are a FALLBACK ("If there
# is no information provided..."), and they are DETECTOR geometry — the roadmap
# text that once attached "2.0 m / 4.5 m" to signal-head placement was miscited,
# corrected 2026-08-06, and the extract carries a bold IMPORTANT note saying no
# offset appears in §5.4.2.2 at all.
# ---------------------------------------------------------------------------

#: §5.4.2.6: the detector's downstream end sits this far back from the head.
DETECTOR_SETBACK_M = 2.0
#: §5.4.2.6: detector length at an intersection approach.
DETECTOR_LENGTH_M = 4.5


# ---------------------------------------------------------------------------
# Refusal / finding codes
# ---------------------------------------------------------------------------

#: Every code that produces NO signals at all. Kept as data so the module
#: docstring's "refuses and produces nothing" claim is testable.
REFUSE_CODES: frozenset[str] = frozenset(
    {
        "signals_plan_refused",
        "signals_plan_has_no_cycle",
        "signals_node_not_in_draft",
        "signals_node_has_no_approaches",
        "signals_approach_without_phase",
        "signals_assignment_names_unknown_link",
        "signals_assignment_names_unknown_phase",
        "signals_phase_never_assigned",
        "signals_amber_unknown",
        "signals_green_unknown",
        "signals_all_red_not_positive",
        "signals_turn_from_unassigned_approach",
    }
)


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    *,
    messages: dict[str, str] | None = None,
    subject: str | None = None,
    citation: str | None = None,
    context: dict | None = None,
) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        messages=messages or {},
        subject=subject,
        citation=citation,
        context=context or {},
    )


# ---------------------------------------------------------------------------
# Settings and result types
# ---------------------------------------------------------------------------


class SignalSettings(BaseModel):
    """Every number this module judges by, in one place and overridable — the
    same shape as ``fixed_time.ConversionSettings`` and ``quality.
    QualityThresholds``."""

    model_config = ConfigDict(extra="forbid")

    red_amber_s: float = Field(
        default=0.0,
        ge=0,
        description=(
            "ASSUMPTION S2. Australian practice has no red/amber interval and A.3's "
            "intergreen has no red/amber term, so this is 0 by default even though "
            "Vissim's own left-hand example writes 2 s."
        ),
    )
    detector_setback_m: float = Field(
        default=DETECTOR_SETBACK_M,
        ge=0,
        description="§5.4.2.6 fallback: detector's downstream end back from the head.",
    )
    detector_length_m: float = Field(
        default=DETECTOR_LENGTH_M,
        gt=0,
        description="§5.4.2.6 fallback: detector length at an intersection approach.",
    )
    emit_detectors: bool = Field(
        default=True,
        description=(
            "ASSUMPTION S5. §5.4.2.6's opening sentence scopes detectors to SCATSIM/VAP, "
            "so they are inert for a FIXEDTIME program; they are still written so a later "
            "swap has them, and every run says so."
        ),
    )


@dataclass(frozen=True)
class SignalGroupPlan:
    """One signal group: one approach, one phase, one green window.

    Every field is a number a reviewer can re-derive by hand from the plan::

        green_start_s = the phase's start_offset_s
        green_end_s   = green_start_s + green_s
        red_start_s   = green_end_s + amber_s
        all_red_s     = intergreen_s - amber_s
    """

    no: int
    approach_link_id: int
    approach_name: str
    phase_id: str
    green_start_s: float
    green_s: float
    amber_s: float
    all_red_s: float
    red_amber_s: float
    min_green_s: float
    lanes: int

    @property
    def green_end_s(self) -> float:
        return self.green_start_s + self.green_s

    @property
    def red_start_s(self) -> float:
        """When the RED display begins — after the amber, not at green end.

        This is the number ``services.signal_parser`` subtracts from to recover
        the green, so it is the whole reason the round trip returns the plan's
        own figure. See the module docstring's worked example.
        """
        return self.green_end_s + self.amber_s

    @property
    def intergreen_s(self) -> float:
        return self.amber_s + self.all_red_s


@dataclass(frozen=True)
class SignalHeadPlan:
    """One head, on one lane of one approach, at the stop line."""

    no: int
    local_no: int
    link_id: int
    lane: int
    pos_m: float
    group_no: int


@dataclass(frozen=True)
class DetectorPlan:
    """One detector, on §5.4.2.6's fallback geometry.

    ``assumed`` is always ``True`` and ``citation`` always names §5.4.2.6: the
    rule applies only "If there is no information provided to indicate the
    detector size and location", which for a genesis draft — built from OSM
    geometry and a SCATS history, with no traffic signal plan or operation sheet
    anywhere in the input — is definitionally the case. The marker travels with
    the object so a consumer cannot mistake a fallback for a surveyed position.
    """

    no: int
    port_no: int
    link_id: int
    lane: int
    pos_m: float
    length_m: float
    head_no: int
    assumed: bool = True
    citation: str = CIT_S5426_DETECTOR_FALLBACK


@dataclass(frozen=True)
class NodeSignals:
    """Everything one signalised node contributes to the emission.

    ``groups`` is empty exactly when a ``REFUSE`` issue is present — the module
    produces no partial controller, the same contract ``FixedTimePlan`` keeps.
    """

    controller_no: int
    node_id: int
    node_name: str
    cycle_s: float
    groups: tuple[SignalGroupPlan, ...]
    heads: tuple[SignalHeadPlan, ...]
    detectors: tuple[DetectorPlan, ...]
    issues: tuple[ImportIssue, ...]
    compliance: tuple[ComplianceItem, ...]

    @property
    def refusals(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.REFUSE)

    @property
    def warnings(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.WARN)

    @property
    def infos(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.INFO)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [i.code for i in self.issues if severity is None or i.severity is severity]


@dataclass(frozen=True)
class DraftSignals:
    """The whole draft's signal layer — what the emitter takes.

    ``nodes`` holds one entry per signalised node, in controller-number order.
    A draft with no signalised node produces :data:`NO_SIGNALS`, which the
    emitter treats exactly like ``None``: no container is written.
    """

    nodes: tuple[NodeSignals, ...] = ()

    @property
    def ok(self) -> bool:
        return all(node.ok for node in self.nodes)

    @property
    def issues(self) -> tuple[ImportIssue, ...]:
        return tuple(issue for node in self.nodes for issue in node.issues)

    @property
    def compliance(self) -> tuple[ComplianceItem, ...]:
        return tuple(item for node in self.nodes for item in node.compliance)

    @property
    def emitting(self) -> tuple[NodeSignals, ...]:
        """The nodes that actually produced a controller. A refused node
        contributes findings and NO objects; it never half-emits."""
        return tuple(node for node in self.nodes if node.ok and node.groups)

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [i.code for i in self.issues if severity is None or i.severity is severity]


#: The empty signal layer. ``emit`` treats this and ``None`` identically.
NO_SIGNALS = DraftSignals()


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def node_approaches(draft: DraftNetwork, node: DraftNode) -> list[DraftLink]:
    """The links entering ``node``, in the draft's own order.

    Deliberately the SAME expression ``draft.build_draft`` uses to find the
    approaches it builds connectors from (``to_node_osm_id == osm_node_id``,
    sorted by link id), so a group cannot be laid out over a different set of
    approaches than the connectors were.
    """
    return sorted(
        (link for link in draft.links if link.to_node_osm_id == node.osm_id),
        key=lambda link: link.id,
    )


def _stop_line_pos_m(link: DraftLink) -> float:
    """Where the stop line is on an approach: the link's own end.

    ASSUMPTION S1. Rounded exactly as ``draft.build_draft`` rounds the connector
    ``from_pos_m`` it takes from the same length, so a head and the connector
    leaving under it agree to the last written digit rather than to within a
    millimetre.
    """
    return round(link.length_m, 3)


def generate_node_signals(
    draft: DraftNetwork,
    node: DraftNode,
    plan: FixedTimePlan,
    *,
    approach_phases: Mapping[int, str],
    settings: SignalSettings | None = None,
    head_no_start: int = 1,
    detector_no_start: int = 1,
) -> NodeSignals:
    """One node's controller, groups, heads and detectors from one plan.

    ``approach_phases`` maps **approach link id → plan phase id**. It is
    required, and validated exhaustively: see the module docstring's "The one
    fact this module refuses to guess".

    Returns a :class:`NodeSignals` **always** — the import layer's "report, do
    not raise" contract. ``result.ok`` is false and ``result.groups`` is empty
    when any check refused.
    """
    settings = settings or SignalSettings()
    subject = f"node {node.id} ({node.name}) / controller {node.id}"
    issues: list[ImportIssue] = []

    def _bail() -> NodeSignals:
        return NodeSignals(
            controller_no=node.id,
            node_id=node.id,
            node_name=node.name,
            cycle_s=0.0,
            groups=(),
            heads=(),
            detectors=(),
            issues=tuple(issues),
            compliance=(),
        )

    # -- 1. the plan must BE a plan ---------------------------------------
    if not plan.ok or plan.program is None:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_plan_refused",
                f"The fixed-time plan for controller {plan.controller_id} refused "
                f"({sorted(plan.issue_codes(IssueSeverity.REFUSE))}), so it carries no program. "
                "1.6 produces no plan at all from contradictory history, and this module will "
                "not build a signal controller out of one that does not exist.",
                messages={
                    "ko": "1.6 고정주기 변환이 거부되어 프로그램이 없습니다. 신호 제어기를 만들지 "
                    "않습니다 — 모순된 이력으로부터 계획을 추측하지 않습니다.",
                    "en": "The 1.6 fixed-time conversion refused, so there is no program. No "
                    "signal controller is built — a plan is not guessed from contradictory "
                    "history.",
                    "ja": "1.6 の固定時間変換が拒否されたためプログラムがありません。信号制御機は "
                    "作成しません — 矛盾した履歴から計画を推測しません。",
                },
                subject=subject,
                citation=CIT_S5423_FIXED_TIME,
                context={"plan_refusals": sorted(plan.issue_codes(IssueSeverity.REFUSE))},
            )
        )
        return _bail()

    cycle_s = plan.plan_cycle_s
    if cycle_s is None or cycle_s <= 0:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_plan_has_no_cycle",
                f"The plan states a cycle of {cycle_s!r}. Every green window is placed inside "
                "the cycle, so there is nothing to place them in.",
                messages={
                    "ko": f"계획의 주기가 {cycle_s!r}입니다. 녹색 구간을 배치할 주기가 없습니다.",
                    "en": f"The plan states a cycle of {cycle_s!r}; there is no cycle to place "
                    "green windows in.",
                    "ja": f"計画の周期が {cycle_s!r} です。緑区間を配置する周期がありません。",
                },
                subject=subject,
                citation=CIT_S5423_FIXED_TIME,
                context={"plan_cycle_s": cycle_s},
            )
        )
        return _bail()

    # -- 2. the node must be in this draft, and have approaches -----------
    if all(existing.id != node.id for existing in draft.nodes):
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_node_not_in_draft",
                f"Node {node.id} is not a node of draft {draft.name!r}. Heads are placed on that "
                "draft's links, so signalling a node it does not contain would write heads onto "
                "links that are not there.",
                messages={
                    "ko": f"노드 {node.id}은(는) 이 초안의 노드가 아닙니다. 존재하지 않는 링크에 "
                    "신호등을 배치할 수 없습니다.",
                    "en": f"Node {node.id} is not a node of this draft; heads cannot be placed on "
                    "links it does not contain.",
                    "ja": f"ノード {node.id} はこの草案のノードではありません。存在しないリンクに "
                    "信号機は配置できません。",
                },
                subject=subject,
                context={"node_id": node.id, "draft": draft.name},
            )
        )
        return _bail()

    approaches = node_approaches(draft, node)
    if not approaches:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_node_has_no_approaches",
                f"Node {node.id} has no approach links, so there is nothing to signal. A "
                "controller with no signal group controls nothing, and Vissim would load it "
                "without a murmur.",
                messages={
                    "ko": f"노드 {node.id}에 진입 링크가 없어 신호로 제어할 대상이 없습니다. "
                    "신호그룹이 없는 제어기는 아무것도 제어하지 않습니다.",
                    "en": f"Node {node.id} has no approach links, so there is nothing to signal.",
                    "ja": f"ノード {node.id} に流入リンクがなく、制御する対象がありません。",
                },
                subject=subject,
                citation=CIT_S5422_SIGNAL_HEADS,
                context={"node_id": node.id},
            )
        )
        return _bail()

    approach_by_id = {link.id: link for link in approaches}

    # -- 3. the plan's usable phases --------------------------------------
    # A dropped phase (1.6 ASSUMPTION A5: it never ran) is not in the derived
    # program, so it cannot carry a signal group either.
    rows_by_phase = {row.phase_id: row for row in plan.rows if not row.dropped}
    phase_data = {phase.phase_id: phase for phase in plan.program.phases}

    # -- 4. validate the assignment, exhaustively, before building anything
    unknown_links = sorted(set(approach_phases) - set(approach_by_id))
    if unknown_links:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_assignment_names_unknown_link",
                f"The approach→phase assignment names link(s) {unknown_links}, which are not "
                f"approaches of node {node.id} (its approaches are "
                f"{sorted(approach_by_id)}). The module will not silently ignore an entry the "
                "caller meant to apply somewhere.",
                messages={
                    "ko": f"배정표가 이 노드의 진입 링크가 아닌 링크 {unknown_links}을(를) "
                    "지정했습니다. 의도된 배정을 조용히 무시하지 않습니다.",
                    "en": f"The assignment names link(s) {unknown_links}, which are not approaches "
                    "of this node; an entry the caller meant to apply is never silently ignored.",
                    "ja": f"割当表がこのノードの流入リンクでないリンク {unknown_links} を指定して "
                    "います。意図された割当を黙って無視しません。",
                },
                subject=subject,
                citation=CIT_S5421_SIGNAL_GROUPS,
                context={"unknown_link_ids": unknown_links, "approaches": sorted(approach_by_id)},
            )
        )
        return _bail()

    unassigned = [link.id for link in approaches if link.id not in approach_phases]
    if unassigned:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_approach_without_phase",
                f"Approach link(s) {unassigned} of node {node.id} have no phase assigned. Which "
                "approach runs in which phase is a site fact — nothing links a SCATS phase id to "
                "a compass bearing — so it is required input, never a guess. An unsignalled "
                "approach inside a signalised intersection is the silent failure this project "
                "exists to catch.",
                messages={
                    "ko": f"진입 링크 {unassigned}에 배정된 현시가 없습니다. 어떤 접근로가 어느 "
                    "현시에 운영되는지는 현장 정보이므로 추측하지 않고 입력을 요구합니다.",
                    "en": f"Approach link(s) {unassigned} have no phase assigned. Which approach "
                    "runs in which phase is a site fact, so it is required input, never a guess.",
                    "ja": f"流入リンク {unassigned} に割り当てられた現示がありません。どの流入路が "
                    "どの現示で動くかは現地情報であり、推測せず入力を要求します。",
                },
                subject=subject,
                citation=CIT_S5421_SIGNAL_GROUPS,
                context={"unassigned_link_ids": unassigned},
            )
        )
        return _bail()

    unknown_phases = sorted({p for p in approach_phases.values() if p not in rows_by_phase})
    if unknown_phases:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_assignment_names_unknown_phase",
                f"The assignment names phase(s) {unknown_phases}, which the plan does not carry "
                f"(its phases are {sorted(rows_by_phase)}). A phase 1.6 dropped because it never "
                "ran (ASSUMPTION A5) is not in the derived program and cannot be given a signal "
                "group.",
                messages={
                    "ko": f"배정표가 계획에 없는 현시 {unknown_phases}을(를) 지정했습니다. 관측 "
                    "구간에 한 번도 운영되지 않아 제외된 현시에는 신호그룹을 만들 수 없습니다.",
                    "en": f"The assignment names phase(s) {unknown_phases}, which the plan does "
                    "not carry; a phase dropped because it never ran cannot be given a group.",
                    "ja": f"割当表が計画にない現示 {unknown_phases} を指定しています。観測期間中に "
                    "一度も動作せず除外された現示には信号グループを作成できません。",
                },
                subject=subject,
                citation=CIT_S5421_SIGNAL_GROUPS,
                context={"unknown_phase_ids": unknown_phases, "plan_phases": sorted(rows_by_phase)},
            )
        )
        return _bail()

    never_assigned = sorted(set(rows_by_phase) - set(approach_phases.values()))
    if never_assigned:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_phase_never_assigned",
                f"Plan phase(s) {never_assigned} were assigned to no approach of node {node.id}. "
                "The plan and the node disagree about how many movements this intersection "
                "serves: either the plan is another site's, or an approach is missing from the "
                "draft. Emitting the rest would produce a controller that holds green for a "
                "movement no head shows.",
                messages={
                    "ko": f"계획의 현시 {never_assigned}이(가) 어떤 접근로에도 배정되지 않았습니다. "
                    "계획과 노드가 서로 다른 교차로를 말하고 있습니다 — 나머지만 내보내면 "
                    "어떤 신호등도 표시하지 않는 현시를 가진 제어기가 됩니다.",
                    "en": f"Plan phase(s) {never_assigned} were assigned to no approach: the plan "
                    "and the node disagree about how many movements this intersection serves.",
                    "ja": f"計画の現示 {never_assigned} がどの流入路にも割り当てられていません。 "
                    "計画とノードが別の交差点を指しています。",
                },
                subject=subject,
                citation=CIT_S5421_SIGNAL_GROUPS,
                context={
                    "unassigned_phase_ids": never_assigned,
                    "approach_count": len(approaches),
                    "plan_phase_count": len(rows_by_phase),
                },
            )
        )
        return _bail()

    # Every connector leaving this node must leave from an approach that got a
    # group. By construction of ``build_draft`` it always does; a hand-assembled
    # draft is the case this guards.
    orphan_turns = sorted(
        {
            connector.from_link
            for connector in draft.connectors
            if connector.node_id == node.id and connector.from_link not in approach_by_id
        }
    )
    if orphan_turns:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "signals_turn_from_unassigned_approach",
                f"Node {node.id} has turn(s) leaving link(s) {orphan_turns}, which are not among "
                "its approaches, so those movements would run through a signalised intersection "
                "with no signal group at all.",
                messages={
                    "ko": f"이 노드의 회전 이동이 접근로가 아닌 링크 {orphan_turns}에서 출발합니다. "
                    "해당 이동은 신호그룹 없이 신호교차로를 통과하게 됩니다.",
                    "en": f"Turns leave link(s) {orphan_turns}, which are not approaches, so those "
                    "movements would cross a signalised intersection with no signal group.",
                    "ja": f"回転movementが流入路でないリンク {orphan_turns} から出発します。信号 "
                    "グループなしで信号交差点を通過することになります。",
                },
                subject=subject,
                citation=CIT_S5422_SIGNAL_HEADS,
                context={"orphan_from_link_ids": orphan_turns},
            )
        )
        return _bail()

    # -- 5. build the groups ----------------------------------------------
    groups: list[SignalGroupPlan] = []
    early_cut_off_phases: list[str] = []
    min_green_absent: list[str] = []
    for index, approach in enumerate(approaches, start=1):
        phase_id = approach_phases[approach.id]
        row = rows_by_phase[phase_id]
        data = phase_data.get(phase_id)

        if row.green_s is None or row.start_offset_s is None:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "signals_green_unknown",
                    f"Phase {phase_id} carries no green (1.6 could not split its phase time into "
                    "green and clearance — see fixed_time_green_split_unavailable). A signal "
                    "group with an unknown green is not a signal group; the module will not "
                    "back-compute one.",
                    messages={
                        "ko": f"현시 {phase_id}의 녹색시간을 알 수 없습니다 (1.6이 현시시간을 "
                        "녹색/클리어런스로 분할하지 못함). 역산하지 않습니다.",
                        "en": f"Phase {phase_id} carries no green — 1.6 could not split its phase "
                        "time into green and clearance, and it is not back-computed here.",
                        "ja": f"現示 {phase_id} の緑時間が不明です (1.6 が現示時間を緑/クリアランス "
                        "に分割できず)。逆算はしません。",
                    },
                    subject=subject,
                    citation=CIT_S5421_TIMINGS_UNCHANGED,
                    context={"phase_id": phase_id},
                )
            )
            return _bail()

        amber_s = data.amber_s if data is not None else None
        if amber_s is None:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "signals_amber_unknown",
                    f"Phase {phase_id} states no yellow. Vissim needs the amber as its OWN "
                    "display — it is not interchangeable with the rest of the intergreen — and "
                    "§5.4.2.1 requires yellow and all-red to be input from controller/SCATS "
                    "information and not changed. Writing a zero or a default amber would be "
                    "inventing the one number the guideline says must come from the controller.",
                    messages={
                        "ko": f"현시 {phase_id}에 황색시간이 없습니다. Vissim은 황색을 별도 표시로 "
                        "요구하며, §5.4.2.1은 황색·전적색을 제어기/SCATS 값 그대로 입력하도록 "
                        "합니다. 기본값을 지어내지 않습니다.",
                        "en": f"Phase {phase_id} states no yellow. Vissim needs amber as its own "
                        "display and §5.4.2.1 requires it to come from controller/SCATS data, so "
                        "no default is invented.",
                        "ja": f"現示 {phase_id} に黄色時間がありません。Vissim は黄色を独立した "
                        "表示として要求し、§5.4.2.1 は制御機/SCATS の値をそのまま入力するよう "
                        "求めます。既定値は作りません。",
                    },
                    subject=subject,
                    citation=CIT_S5421_TIMINGS_UNCHANGED,
                    context={"phase_id": phase_id},
                )
            )
            return _bail()

        intergreen_s = row.intergreen_s if row.intergreen_s is not None else 0.0
        all_red_s = round(intergreen_s - float(amber_s), 6)
        if all_red_s <= 0:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "signals_all_red_not_positive",
                    f"Phase {phase_id}: its intergreen ({intergreen_s:g} s) is not longer than "
                    f"its yellow ({amber_s:g} s), leaving an all-red of {all_red_s:g} s. A.3 puts "
                    "a 1.0 s floor under all-red, and an all-red of zero also makes the red "
                    "command land on the cycle boundary, where a reader cannot tell it from the "
                    "start of the next cycle. Either the clearance times or the history is wrong.",
                    messages={
                        "ko": f"현시 {phase_id}: 소거시간({intergreen_s:g}s)이 황색({amber_s:g}s)"
                        f"보다 길지 않아 전적색이 {all_red_s:g}s입니다. A.3의 전적색 최소 1.0s를 "
                        "위반하며, 0이면 적색 명령이 주기 경계와 구분되지 않습니다.",
                        "en": f"Phase {phase_id}: intergreen ({intergreen_s:g} s) is not longer "
                        f"than yellow ({amber_s:g} s), leaving an all-red of {all_red_s:g} s — "
                        "below A.3's 1.0 s floor, and at zero the red command is "
                        "indistinguishable from the next cycle's start.",
                        "ja": f"現示 {phase_id}: 全赤が {all_red_s:g}s となり、A.3 の下限 1.0s を "
                        "下回ります。0 の場合、赤命令が周期境界と区別できません。",
                    },
                    subject=subject,
                    citation=CIT_S5421_TIMINGS_UNCHANGED,
                    context={
                        "phase_id": phase_id,
                        "intergreen_s": intergreen_s,
                        "amber_s": amber_s,
                        "all_red_s": all_red_s,
                    },
                )
            )
            return _bail()

        # ASSUMPTION S3: when the source stated the intergreen directly it may
        # already include A.3's early cut-off, which then sits in this all-red.
        stated_all_red = data.all_red_s if data is not None else None
        if stated_all_red is not None and abs(stated_all_red - all_red_s) > 1e-6:
            early_cut_off_phases.append(phase_id)

        min_green_s = data.min_green_s if data is not None else None
        if min_green_s is None:
            min_green_absent.append(phase_id)

        groups.append(
            SignalGroupPlan(
                no=index,
                approach_link_id=approach.id,
                approach_name=approach.name,
                phase_id=phase_id,
                green_start_s=float(row.start_offset_s),
                green_s=float(row.green_s),
                amber_s=float(amber_s),
                all_red_s=all_red_s,
                red_amber_s=float(settings.red_amber_s),
                min_green_s=float(min_green_s) if min_green_s is not None else 0.0,
                lanes=max(approach.lanes, 1),
            )
        )

    if early_cut_off_phases:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "signals_early_cut_off_in_all_red",
                f"Phase(s) {sorted(set(early_cut_off_phases))}: the stated intergreen is longer "
                "than yellow + the stated all-red, so A.3's early cut-off is included in it. It "
                "is carried in the ALL-RED of the emitted group (ASSUMPTION S3) because early "
                "cut-off is a per-signal-group, site-justified value this draft cannot attribute "
                "to one group. The GREEN is unaffected either way — 1.6 already subtracted the "
                "whole intergreen from the phase time.",
                subject=subject,
                citation=CIT_S5421_TIMINGS_UNCHANGED,
                context={"phase_ids": sorted(set(early_cut_off_phases))},
            )
        )

    if min_green_absent:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "signals_min_green_absent",
                f"Phase(s) {sorted(set(min_green_absent))} state no minimum green, so the emitted "
                "signal group carries Vissim's own 0. §5.4.2.1 requires minimum greens to be "
                "input directly from controller/SCATS information; a zero here is 'not stated', "
                "not 'zero seconds', and must be filled in from the controller record before the "
                "model is used.",
                subject=subject,
                citation=CIT_S5421_TIMINGS_UNCHANGED,
                context={"phase_ids": sorted(set(min_green_absent))},
            )
        )

    # -- 6. heads (§5.4.2.2) and detectors (§5.4.2.6) ----------------------
    heads: list[SignalHeadPlan] = []
    detectors: list[DetectorPlan] = []
    head_no = head_no_start
    detector_no = detector_no_start
    port_no = 1
    detector_did_not_fit: list[str] = []

    for group in groups:
        approach = approach_by_id[group.approach_link_id]
        stop_line_m = _stop_line_pos_m(approach)
        for lane in range(1, group.lanes + 1):
            heads.append(
                SignalHeadPlan(
                    no=head_no,
                    local_no=head_no,  # ASSUMPTION S4
                    link_id=approach.id,
                    lane=lane,
                    pos_m=stop_line_m,
                    group_no=group.no,
                )
            )
            if settings.emit_detectors:
                # ASSUMPTION S6: ``pos`` anchors the UPSTREAM end, so the
                # detector body runs pos → pos + length and its DOWNSTREAM end
                # lands exactly ``setback`` before the head, which is what
                # §5.4.2.6 states. The alternative anchor reading is declared in
                # the module docstring and is a live-check follow-up.
                detector_pos = round(
                    stop_line_m - settings.detector_setback_m - settings.detector_length_m, 3
                )
                if detector_pos < 0:
                    detector_did_not_fit.append(f"link {approach.id} lane {lane}")
                else:
                    detectors.append(
                        DetectorPlan(
                            no=detector_no,
                            port_no=port_no,
                            link_id=approach.id,
                            lane=lane,
                            pos_m=detector_pos,
                            length_m=settings.detector_length_m,
                            head_no=head_no,
                        )
                    )
                    detector_no += 1
                    port_no += 1
            head_no += 1

    if detector_did_not_fit:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "signals_detector_does_not_fit",
                f"No detector was written on {detector_did_not_fit}: §5.4.2.6's fallback needs "
                f"{settings.detector_setback_m:g} + {settings.detector_length_m:g} = "
                f"{settings.detector_setback_m + settings.detector_length_m:g} m of approach "
                "upstream of the stop line and the link is shorter than that. The detector was "
                "LEFT OUT rather than shortened or pushed to position 0 — §5.4.2.6 states one "
                "length, and a detector of a different length is a different object.",
                subject=subject,
                citation=CIT_S5426_DETECTOR_FALLBACK,
                context={"subjects": detector_did_not_fit},
            )
        )

    if settings.emit_detectors and detectors:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "signals_detectors_not_required_for_fixed_time",
                f"{len(detectors)} detector(s) were written on §5.4.2.6's FALLBACK geometry "
                f"({settings.detector_setback_m:g} m back from the head, "
                f"{settings.detector_length_m:g} m long). Two things a reader must not miss. "
                "First, §5.4.2.6's own opening sentence scopes detectors to SCATSIM or VAP "
                "control, and this is a FIXEDTIME controller — they are inert for this program "
                "and exist so a later SCATSIM/VAP swap has them (ASSUMPTION S5). Second, the "
                "fallback applies only 'If there is no information provided to indicate the "
                "detector size and location'; that is definitionally true of a genesis draft, "
                "which is built from OSM geometry and a SCATS history with no traffic signal "
                "plan or operation sheet anywhere in its input. Plans win over these numbers "
                "the moment plans exist.",
                subject=subject,
                citation=CIT_S5426_DETECTORS_FROM_PLANS,
                context={
                    "detector_count": len(detectors),
                    "setback_m": settings.detector_setback_m,
                    "length_m": settings.detector_length_m,
                    "assumed": True,
                },
            )
        )

    # -- 7. always-on disclosures -----------------------------------------
    approaches_without_turns = sorted(
        link.id
        for link in approaches
        if not any(
            connector.node_id == node.id and connector.from_link == link.id
            for connector in draft.connectors
        )
    )
    if approaches_without_turns:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "signals_approach_has_no_turns",
                f"Approach link(s) {approaches_without_turns} carry a signal group and heads but "
                "no onward movement at this node — no connector leaves them. They will hold "
                "vehicles at a red that never releases anything.",
                subject=subject,
                citation=CIT_S5422_SIGNAL_HEADS,
                context={"link_ids": approaches_without_turns},
            )
        )

    # The round trip through ``services.signal_parser`` clusters green starts
    # within 5 s into ONE phase. Two plan phases that close together cannot be
    # told apart on the way back, so the verification claim would be false for
    # this plan and the module says so rather than letting the test pass on a
    # coincidence.
    starts = sorted({group.green_start_s for group in groups})
    too_close = [
        (low, high) for low, high in zip(starts, starts[1:]) if 0 < (high - low) <= 5
    ]
    if too_close:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "signals_phase_starts_within_parser_cluster",
                f"Green starts {too_close} are within 5 s of each other. The emitted program is "
                "correct, but ``services.signal_parser`` merges green starts within 5 s into one "
                "phase, so a read-back of this network will report FEWER phases than the plan "
                "has. The round-trip check cannot distinguish these phases.",
                subject=subject,
                citation=CIT_S5423_FIXED_TIME,
                context={"close_starts": [list(pair) for pair in too_close]},
            )
        )

    issues.append(
        _issue(
            IssueSeverity.INFO,
            "signals_group_layout_is_per_approach",
            f"Controller {node.id} was laid out with ONE SIGNAL GROUP PER APPROACH "
            f"({len(groups)} groups over {len(approaches)} approaches), numbered 1..N in "
            "ascending approach-link-id order. §5.4.2.1 requires groups to follow the site LM "
            "plan and to be NUMBERED AS IN SCATS; a genesis draft has neither, so this numbering "
            "is a runnable placeholder derived from OSM link ids and must be reconciled against "
            "the LM plan before the model is used. Per-approach (rather than per-turn) is the "
            "finest layout the input supports: a per-turn layout would assert which turns are "
            "separately signalled, and the neutral model carries no opposed/permissive semantics "
            "to say so.",
            subject=subject,
            citation=CIT_S5421_SIGNAL_GROUPS,
            context={
                "group_count": len(groups),
                "approach_count": len(approaches),
                "mapping": {group.no: group.approach_link_id for group in groups},
                "phase_of_group": {group.no: group.phase_id for group in groups},
            },
        )
    )

    return NodeSignals(
        controller_no=node.id,
        node_id=node.id,
        node_name=node.name,
        cycle_s=float(cycle_s),
        groups=tuple(groups),
        heads=tuple(heads),
        detectors=tuple(detectors),
        issues=tuple(issues),
        compliance=tuple(_compliance(groups, heads, detectors, settings, issues)),
    )


def generate_signals(
    draft: DraftNetwork,
    assignments: Mapping[int, tuple[FixedTimePlan, Mapping[int, str]]],
    *,
    settings: SignalSettings | None = None,
) -> DraftSignals:
    """Every signalised node of a draft, numbered so the whole file is valid.

    ``assignments`` maps **draft node id → (plan, approach→phase mapping)**.
    Nodes are processed in ascending node id, and head and detector numbers are
    threaded across them, because ``no`` is unique per CONTAINER in Vissim, not
    per controller. Deterministic given the inputs.
    """
    nodes_by_id = {node.id: node for node in draft.nodes}
    results: list[NodeSignals] = []
    head_no = 1
    detector_no = 1
    for node_id in sorted(assignments):
        node = nodes_by_id.get(node_id)
        if node is None:
            # Reported, not raised: the same "report, do not raise" contract.
            plan, _mapping = assignments[node_id]
            results.append(
                NodeSignals(
                    controller_no=node_id,
                    node_id=node_id,
                    node_name="",
                    cycle_s=0.0,
                    groups=(),
                    heads=(),
                    detectors=(),
                    issues=(
                        _issue(
                            IssueSeverity.REFUSE,
                            "signals_node_not_in_draft",
                            f"Node {node_id} is not a node of draft {draft.name!r}; its plan "
                            f"({plan.controller_id}) has nothing to attach to.",
                            messages={
                                "ko": f"노드 {node_id}은(는) 이 초안에 없습니다.",
                                "en": f"Node {node_id} is not a node of this draft.",
                                "ja": f"ノード {node_id} はこの草案にありません。",
                            },
                            subject=f"node {node_id}",
                            context={"node_id": node_id, "draft": draft.name},
                        ),
                    ),
                    compliance=(),
                )
            )
            continue
        plan, mapping = assignments[node_id]
        result = generate_node_signals(
            draft,
            node,
            plan,
            approach_phases=mapping,
            settings=settings,
            head_no_start=head_no,
            detector_no_start=detector_no,
        )
        head_no += len(result.heads)
        detector_no += len(result.detectors)
        results.append(result)
    return DraftSignals(nodes=tuple(results))


# ---------------------------------------------------------------------------
# The compliance layer — §5.4.2 in the draft's three states
# ---------------------------------------------------------------------------


def _compliance(
    groups: list[SignalGroupPlan],
    heads: list[SignalHeadPlan],
    detectors: list[DetectorPlan],
    settings: SignalSettings,
    issues: list[ImportIssue],
) -> list[ComplianceItem]:
    """The §5.4.2 rules, graded applied / violated-named / not-checkable.

    Same three states, same shape and same renderer as ``draft._compliance`` —
    these items are appended to the draft's own report, not kept in a second
    one.
    """
    items: list[ComplianceItem] = []

    # §5.4.2 — the control TYPE is a thing to agree with Main Roads, and offsets
    # come from SCATS. Fixed time was chosen by the pipeline, not by agreement.
    items.append(
        ComplianceItem(
            rule_id="5.4.2-control-type-agreed",
            section="5.4.2",
            doc_page=201,
            state=ComplianceState.NOT_CHECKABLE,
            citation=CIT_S542_SIGNAL_CONTROL_TYPE,
            reason=REASON_REQUIRES_SITE_OBSERVATION,
            instances=1,
            evidence={"emitted_control_type": schema.SIGNAL_CONTROLLER_ATTRS["type"]},
            detail={
                "ko": "제어 방식은 Main Roads와 협의해 정하는 사항입니다. 이 초안은 1.6이 만든 "
                "고정주기 계획을 FIXEDTIME으로 내보냈을 뿐, 협의를 대신하지 않습니다.",
                "en": "The control type is something to be agreed with Main Roads. This draft "
                "emitted the 1.6 fixed-time plan as FIXEDTIME; that is not the agreement.",
                "ja": "制御方式は Main Roads と協議して決める事項です。本草案は 1.6 の固定時間 "
                "計画を FIXEDTIME として出力しただけで、協議の代わりにはなりません。",
            },
        )
    )

    # §5.4.2.1 — the layout decision. NOT compliance: no LM plan, no SCATS
    # numbering. This is the honest grade of the module's central choice.
    items.append(
        ComplianceItem(
            rule_id="5.4.2.1-signal-groups-match-scats",
            section="5.4.2.1",
            doc_page=201,
            state=ComplianceState.NOT_CHECKABLE,
            citation=CIT_S5421_SIGNAL_GROUPS,
            reason=REASON_NO_SIGNAL_PLAN,
            instances=len(groups),
            subjects=[f"SG{group.no}←link {group.approach_link_id}" for group in groups],
            evidence={"layout": "one_group_per_approach", "numbering": "ascending_link_id"},
            detail={
                "ko": "신호그룹은 접근로 1개당 1개로 배치하고 진입 링크 id 순으로 번호를 매겼습니다. "
                "§5.4.2.1은 LM 도면과 SCATS 그룹번호 일치를 요구하지만 초안에는 둘 다 없으므로, "
                "이 번호는 실행 가능한 임시값이며 LM 도면과 대조해야 합니다.",
                "en": "Groups are laid out one per approach and numbered by ascending link id. "
                "§5.4.2.1 requires the LM plan and SCATS group numbers; a genesis draft has "
                "neither, so this numbering is a runnable placeholder to be reconciled with the "
                "LM plan.",
                "ja": "信号グループは流入路ごとに1つ配置し、リンクid順に採番しました。§5.4.2.1 は "
                "LM図面とSCATSのグループ番号一致を要求しますが草案には両方ないため、この番号は "
                "暫定値でありLM図面と照合が必要です。",
            },
        )
    )

    # §5.4.2.1 — the timings themselves ARE input unchanged from the plan.
    items.append(
        ComplianceItem(
            rule_id="5.4.2.1-timings-input-unchanged",
            section="5.4.2.1",
            doc_page=201,
            state=ComplianceState.APPLIED,
            citation=CIT_S5421_TIMINGS_UNCHANGED,
            instances=len(groups),
            evidence={
                "greens_s": sorted({group.green_s for group in groups}),
                "ambers_s": sorted({group.amber_s for group in groups}),
                "all_reds_s": sorted({group.all_red_s for group in groups}),
            },
            detail={
                "ko": "녹색·황색·전적색은 1.6 계획의 값을 그대로 옮겼습니다. 이 모듈은 자체 "
                "교통공학 계산을 하지 않으며, 필요한 값이 없으면 지어내지 않고 거부합니다.",
                "en": "Green, yellow and all-red are carried over from the 1.6 plan unchanged. "
                "This module does no traffic-engineering arithmetic of its own and refuses rather "
                "than inventing a missing value.",
                "ja": "緑・黄・全赤は 1.6 計画の値をそのまま移しました。本モジュールは独自の交通 "
                "工学計算を行わず、値が無ければ作らずに拒否します。",
            },
        )
    )

    # §5.4.2.2 — heads on links, which is what the subsection's own default is.
    items.append(
        ComplianceItem(
            rule_id="5.4.2.2-signal-heads-on-links",
            section="5.4.2.2",
            doc_page=201,
            state=ComplianceState.APPLIED,
            citation=CIT_S5422_SIGNAL_HEADS,
            instances=len(heads),
            evidence={"heads": len(heads), "one_per": "approach_lane"},
            detail={
                "ko": "신호등은 모두 접근 링크 위, 차로마다 1개씩 정지선(링크 끝) 위치에 배치했습니다. "
                "§5.4.2.2는 위치 오프셋을 규정하지 않으므로 오프셋을 적용하지 않았습니다 "
                "(흔히 인용되는 2.0 m는 §5.4.2.6의 검지기 값입니다).",
                "en": "Every head sits on an approach LINK, one per lane, at the stop line (the "
                "link's end). §5.4.2.2 prescribes no position offset, so none was applied — the "
                "2.0 m often quoted at it is §5.4.2.6's DETECTOR setback.",
                "ja": "信号機はすべて流入リンク上、車線ごとに1つ、停止線(リンク端)に配置しました。 "
                "§5.4.2.2 は位置オフセットを規定しないため適用していません (よく引用される 2.0 m "
                "は §5.4.2.6 の検知器の値です)。",
            },
        )
    )

    # §5.4.2.2's other half — partially controlled turns get heads on
    # CONNECTORS, and this draft cannot tell which turns those are.
    items.append(
        ComplianceItem(
            rule_id="5.4.2.2-partially-controlled-turns",
            section="5.4.2.2",
            doc_page=201,
            state=ComplianceState.NOT_CHECKABLE,
            citation=CIT_S5422_SIGNAL_HEADS,
            reason=REASON_NO_SIGNAL_PLAN,
            instances=1,
            detail={
                "ko": "부분 제어 회전(필터링 우회전 등)에는 커넥터 위 신호등이 필요하지만, 중립 "
                "모델은 대향/필터링 의미를 담지 않아 어떤 회전이 그런지 판정할 수 없습니다. "
                "따라서 커넥터 신호등은 하나도 쓰지 않았습니다.",
                "en": "Partially controlled turns need heads on CONNECTORS, but the neutral model "
                "carries no opposed/filtering semantics, so which turns those are is not "
                "determinable. No connector head was written.",
                "ja": "部分制御の回転にはコネクタ上の信号機が必要ですが、中立モデルは対向/フィル "
                "タリングの意味を持たないため判定できません。コネクタ信号機は出力していません。",
            },
        )
    )

    # §5.4.2.3 — fixed time is what 1.6 produced, from SCATS history.
    items.append(
        ComplianceItem(
            rule_id="5.4.2.3-fixed-time-from-history",
            section="5.4.2.3",
            doc_page=201,
            state=ComplianceState.APPLIED,
            citation=CIT_S5423_FIXED_TIME,
            instances=1,
            evidence={"signal_groups": len(groups)},
            detail={
                "ko": "SCATS 이력에서 평균 현시시간을 구하는 §5.4.2.3의 방법을 1.6이 그대로 "
                "수행했고, 이 모듈은 그 결과를 고정주기 프로그램으로 옮겼습니다.",
                "en": "§5.4.2.3's method — average phase lengths from SCATS history files — is "
                "what 1.6 performed, and this module carried the result into a fixed-time "
                "program.",
                "ja": "SCATS 履歴から平均現示時間を求める §5.4.2.3 の方法を 1.6 が実施し、本 "
                "モジュールはその結果を固定時間プログラムに移しました。",
            },
        )
    )

    # §5.4.2.3's pedestrian half — no ped signals are emitted.
    items.append(
        ComplianceItem(
            rule_id="5.4.2.3-pedestrian-signals",
            section="5.4.2.3",
            doc_page=201,
            state=ComplianceState.NOT_CHECKABLE,
            citation=CIT_S5423_PEDESTRIANS,
            reason=REASON_OUTSIDE_EMITTED_SUBSET,
            instances=1,
            detail={
                "ko": "보행 신호는 이 배출 범위 밖입니다. 보행 현시가 있는지 여부는 보행량 관측에 "
                "달려 있고, 초안에는 보행 링크도 횡단보도도 없습니다.",
                "en": "Pedestrian signals are outside this emission. Whether they are needed "
                "turns on observed pedestrian volumes, and the draft carries no pedestrian links "
                "or crossings.",
                "ja": "歩行者信号はこの出力範囲外です。必要性は歩行者量の観測に依存し、草案には "
                "歩行者リンクも横断歩道もありません。",
            },
        )
    )

    # §5.4.2.6 — the detectors, and the fact that they are a FALLBACK.
    if detectors:
        items.append(
            ComplianceItem(
                rule_id="5.4.2.6-detector-fallback-geometry",
                section="5.4.2.6",
                doc_page=202,
                state=ComplianceState.APPLIED,
                citation=CIT_S5426_DETECTOR_FALLBACK,
                instances=len(detectors),
                evidence={
                    "setback_m": settings.detector_setback_m,
                    "length_m": settings.detector_length_m,
                    "assumed": True,
                },
                detail={
                    "ko": "검지기는 §5.4.2.6의 대체 규정대로 신호등에서 2.0 m 뒤, 길이 4.5 m로 "
                    "배치했습니다. 이 규정은 '검지기 크기와 위치 정보가 없을 때'에만 적용되며, "
                    "신호도면이 전혀 없는 초안에는 정의상 해당합니다 — 도면이 생기면 도면이 "
                    "우선합니다.",
                    "en": "Detectors use §5.4.2.6's fallback: 2.0 m back from the head, 4.5 m "
                    "long. That rule applies only when no detector size/location information is "
                    "provided, which is definitionally true of a draft with no signal plans — "
                    "plans win the moment they exist.",
                    "ja": "検知器は §5.4.2.6 の代替規定どおり信号機の 2.0 m 手前・長さ 4.5 m で "
                    "配置しました。この規定は情報がない場合にのみ適用され、図面がない草案には "
                    "定義上該当します — 図面があれば図面が優先します。",
                },
            )
        )
        items.append(
            ComplianceItem(
                rule_id="5.4.2.6-detectors-reflect-plans",
                section="5.4.2.6",
                doc_page=202,
                state=ComplianceState.NOT_CHECKABLE,
                citation=CIT_S5426_DETECTORS_FROM_PLANS,
                reason=REASON_NO_SIGNAL_PLAN,
                instances=len(detectors),
                detail={
                    "ko": "§5.4.2.6은 검지기가 기존/설계 도면(신호도면·운영표)을 반영하도록 요구 "
                    "하지만 초안 입력에는 그런 자료가 없습니다. 또한 이 조항은 SCATSIM/VAP 제어를 "
                    "전제로 하며, 이 제어기는 FIXEDTIME이므로 검지기는 현재 프로그램에 영향이 "
                    "없습니다.",
                    "en": "§5.4.2.6 requires detectors to reflect existing or design information "
                    "(signal plans, operation sheets); none is in the draft's input. The "
                    "subsection also presumes SCATSIM/VAP control, and this controller is "
                    "FIXEDTIME, so the detectors do not affect the emitted program.",
                    "ja": "§5.4.2.6 は検知器が既存/設計図面を反映することを求めますが、草案の入力に "
                    "はありません。また本条は SCATSIM/VAP 制御を前提とし、本制御機は FIXEDTIME "
                    "のため検知器は現行プログラムに影響しません。",
                },
            )
        )

    # Anything the generation itself refused or warned about is named here too,
    # so a reader of the compliance report alone still sees it.
    warned = sorted({issue.code for issue in issues if issue.severity is IssueSeverity.WARN})
    if warned:
        items.append(
            ComplianceItem(
                rule_id="5.4.2-generation-warnings",
                section="5.4.2",
                doc_page=201,
                state=ComplianceState.VIOLATED,
                citation=CIT_S542_SIGNAL_CONTROL_TYPE,
                instances=len(warned),
                subjects=warned,
                detail={
                    "ko": "신호 생성 중 명시된 경고입니다. 각 코드는 위 issues 목록에 전체 설명과 "
                    "함께 있습니다.",
                    "en": "Warnings raised while generating the signals; each code carries its "
                    "full explanation in the issues list.",
                    "ja": "信号生成中に出た警告です。各コードの詳細は issues 一覧にあります。",
                },
            )
        )

    return items


# ---------------------------------------------------------------------------
# Vissig serialisation — the base64 <sc> blob inside intSupplyData
# ---------------------------------------------------------------------------


def _ms(seconds: float) -> str:
    """Seconds → Vissig's milliseconds, as an integer string.

    Every plan time is a whole or half second (1.6 rounds phase times up to 1 s
    and clearance times up to 0.5 s), so this is exact rather than truncating.
    """
    return str(int(round(seconds * schema.VISSIG_MS_PER_S)))


def build_vissig_document(node_signals: NodeSignals) -> str:
    """The inner ``<sc>`` XML document, exactly as Vissim writes it.

    Shape copied from the decoded ``intSupplyData`` of ``LHT``'s own FIXEDTIME
    controllers: ``<sc>`` → ``<sgs>`` (declaration, with per-group default
    durations) → ``<intergreenmatrices/>`` → ``<progs>`` → one ``<prog>`` whose
    ``<sgs>`` carry ``<cmds>`` and ``<fixedstates>`` → the three empty
    stage containers. Two-space indent and CRLF line endings, both of which are
    what Vissim itself produced.

    Deterministic: groups are written in ascending number and each group's
    commands in ascending ``begin``, which is the order LHT's own blob uses.
    """
    controller = str(node_signals.controller_no)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        f'<sc id="{controller}" name="" frequency="1" defaultIntergreenMatrix="0">'
    )

    # The signal-group DECLARATION list.
    lines.append("  <sgs>")
    for group in node_signals.groups:
        lines.append(
            f'    <sg id="{group.no}" name="" '
            f'defaultSignalSequence="{schema.SIGNAL_SEQUENCE_VEHICLE}">'
        )
        lines.append("      <defaultDurations>")
        lines.append(
            f'        <defaultDuration display="{schema.DISPLAY_RED_AMBER}" '
            f'duration="{_ms(group.red_amber_s)}" />'
        )
        lines.append(
            f'        <defaultDuration display="{schema.DISPLAY_AMBER}" '
            f'duration="{_ms(group.amber_s)}" />'
        )
        lines.append(f'        <defaultDuration display="{schema.DISPLAY_RED}" duration="0" />')
        lines.append(f'        <defaultDuration display="{schema.DISPLAY_GREEN}" duration="0" />')
        lines.append("      </defaultDurations>")
        lines.append("    </sg>")
    lines.append("  </sgs>")
    lines.append("  <intergreenmatrices />")

    # The PROGRAM: one per controller, id 1, matching SIGNAL_CONTROLLER_ATTRS'
    # progNo="1" so the parser's "the program Vissim RUNS is the one progNo
    # selects" lookup finds it.
    lines.append("  <progs>")
    lines.append(
        f'    <prog id="1" cycletime="{_ms(node_signals.cycle_s)}" switchpoint="0" '
        'offset="0" intergreens="0" name="Signal program">'
    )
    lines.append("      <sgs>")
    for group in node_signals.groups:
        lines.append(
            f'        <sg sg_id="{group.no}" '
            f'signal_sequence="{schema.SIGNAL_SEQUENCE_VEHICLE}">'
        )
        lines.append("          <cmds>")
        # Green at the phase's own offset; RED after the amber, not at green
        # end. That placement is what makes the read-back return the plan's own
        # green — see the module docstring's worked example.
        commands = sorted(
            (
                (group.green_start_s, schema.DISPLAY_GREEN),
                (group.red_start_s, schema.DISPLAY_RED),
            )
        )
        for begin_s, display in commands:
            lines.append(f'            <cmd display="{display}" begin="{_ms(begin_s)}" />')
        lines.append("          </cmds>")
        lines.append("          <fixedstates>")
        lines.append(
            f'            <fixedstate display="{schema.DISPLAY_RED_AMBER}" '
            f'duration="{_ms(group.red_amber_s)}" />'
        )
        lines.append(
            f'            <fixedstate display="{schema.DISPLAY_AMBER}" '
            f'duration="{_ms(group.amber_s)}" />'
        )
        lines.append("          </fixedstates>")
        lines.append("        </sg>")
    lines.append("      </sgs>")
    lines.append("    </prog>")
    lines.append("  </progs>")
    lines.append("  <stages />")
    lines.append("  <interstageProgs />")
    lines.append("  <stageProgs />")
    lines.append("</sc>")
    return "\r\n".join(lines)


def build_int_supply_data(node_signals: NodeSignals) -> str:
    """The ``intSupplyData`` attribute: the Vissig document, base64, one line.

    Base64 of the UTF-8 bytes, exactly as measured on LHT — whose own blob
    decodes to a CRLF, two-space-indented ``<sc>`` document with an XML
    declaration.
    """
    return base64.b64encode(build_vissig_document(node_signals).encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# XML element construction — used by ``emit.build_tree``
# ---------------------------------------------------------------------------


def _element(parent: ET.Element, tag: str, attrs: dict) -> ET.Element:
    """One ``SubElement`` with attributes in a FIXED (sorted) order — the same
    determinism rule, and the same reason, as ``emit._element``."""
    return ET.SubElement(parent, tag, {key: attrs[key] for key in sorted(attrs)})


def _num(value: float) -> str:
    """Positions in metres, 3 decimals — identical to ``emit._num`` so a head's
    ``pos`` is formatted exactly like the connector position it shares."""
    text = f"{float(value):.3f}"
    return "0.000" if text == "-0.000" else text


def _timing(value: float) -> str:
    """A signal-group timing as Vissim writes it: an integer when it is one.

    LHT writes ``amber="3"``, not ``amber="3.0"``. A.3's clearance times round
    to 0.5 s, so a half second is representable and is written as ``2.5``.
    """
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):g}"


def signal_controller_element(parent: ET.Element, node_signals: NodeSignals) -> ET.Element:
    """One ``<signalController>`` with its groups and signal-times-table rows.

    Child order is Vissim's own, measured on all three LHT controllers:
    ``rampMeterPara``, ``sgs``, ``sigTmsTabConfig``.
    """
    controller = str(node_signals.controller_no)
    element = _element(
        parent,
        "signalController",
        {**schema.SIGNAL_CONTROLLER_ATTRS, "no": controller,
         "intSupplyData": build_int_supply_data(node_signals)},
    )
    _element(element, "rampMeterPara", schema.RAMP_METER_PARA_ATTRS)

    sgs = ET.SubElement(element, "sgs")
    for group in node_signals.groups:
        _element(
            sgs,
            "signalGroup",
            {
                **schema.SIGNAL_GROUP_ATTRS,
                "no": str(group.no),
                "amber": _timing(group.amber_s),
                "redAmber": _timing(group.red_amber_s),
                "minGreen": _timing(group.min_green_s),
            },
        )

    config = ET.SubElement(element, "sigTmsTabConfig")
    for group in node_signals.groups:
        _element(
            config,
            "signalOutputConfigurationElement",
            {
                **schema.SIGNAL_OUTPUT_CONFIG_ATTRS,
                "sg": f"{controller} {group.no}",
                "varNo": str(group.no),
            },
        )
    return element


def signal_head_element(parent: ET.Element, head: SignalHeadPlan, controller_no: int) -> None:
    _element(
        parent,
        "signalHead",
        {
            **schema.SIGNAL_HEAD_ATTRS,
            "no": str(head.no),
            "localNo": str(head.local_no),
            "lane": f"{head.link_id} {head.lane}",
            "pos": _num(head.pos_m),
            "sg": f"{controller_no} {head.group_no}",
        },
    )


def detector_element(parent: ET.Element, detector: DetectorPlan, controller_no: int) -> None:
    _element(
        parent,
        "detector",
        {
            **schema.DETECTOR_ATTRS,
            "no": str(detector.no),
            "portNo": str(detector.port_no),
            "lane": f"{detector.link_id} {detector.lane}",
            "pos": _num(detector.pos_m),
            "length": _num(detector.length_m),
            "sc": str(controller_no),
        },
    )


def signal_containers(signals: DraftSignals | None) -> dict[str, ET.Element]:
    """The three signal containers, or ``{}`` when there is nothing to write.

    ``{}`` is the whole additivity guarantee: ``emit.build_tree`` merges this
    into its container map, so an unsignalised draft — ``None``,
    :data:`NO_SIGNALS`, or a draft every node of which refused — contributes no
    key and the emitted bytes are exactly what they were before this module
    existed.
    """
    if signals is None:
        return {}
    emitting = signals.emitting
    if not emitting:
        return {}

    controllers = ET.Element("signalControllers")
    heads = ET.Element("signalHeads")
    detectors = ET.Element("detectors")
    for node in sorted(emitting, key=lambda item: item.controller_no):
        signal_controller_element(controllers, node)
        for head in sorted(node.heads, key=lambda item: item.no):
            signal_head_element(heads, head, node.controller_no)
        for detector in sorted(node.detectors, key=lambda item: item.no):
            detector_element(detectors, detector, node.controller_no)

    containers = {"signalControllers": controllers, "signalHeads": heads}
    if len(detectors):
        containers["detectors"] = detectors
    return containers


def signal_provenance_note(signals: DraftSignals | None) -> str:
    """The sentence the emitter adds to its provenance comment for a signalised
    draft. Empty for an unsignalised one, so the comment — and the file — is
    unchanged."""
    if signals is None or not signals.emitting:
        return ""
    nodes = signals.emitting
    groups = sum(len(node.groups) for node in nodes)
    heads = sum(len(node.heads) for node in nodes)
    detectors = sum(len(node.detectors) for node in nodes)
    return (
        f"SIGNALS (roadmap 3.1 Genesis (b)): {len(nodes)} fixed-time controller(s), "
        f"{groups} signal group(s), {heads} signal head(s), {detectors} detector(s). "
        "Every timing is carried unchanged from the MRWA Appendix-A A.2 fixed-time plan; "
        "no timing was computed here. Signal groups are laid out ONE PER APPROACH and "
        "numbered from OSM link ids - MRWA OMG v2.0 §5.4.2.1 requires the site LM plan and "
        "SCATS group numbers, which a generated draft does not have, so the numbering is a "
        "runnable PLACEHOLDER to be reconciled before use. Detectors use §5.4.2.6's "
        "no-plans FALLBACK geometry (2.0 m setback, 4.5 m length) and are inert for a "
        "FIXEDTIME controller. "
    )


__all__ = [
    "CIT_S542_SIGNAL_CONTROL_TYPE",
    "CIT_S5421_SIGNAL_GROUPS",
    "CIT_S5421_TIMINGS_UNCHANGED",
    "CIT_S5422_SIGNAL_HEADS",
    "CIT_S5423_FIXED_TIME",
    "CIT_S5423_PEDESTRIANS",
    "CIT_S5426_DETECTORS_FROM_PLANS",
    "CIT_S5426_DETECTOR_FALLBACK",
    "DETECTOR_LENGTH_M",
    "DETECTOR_SETBACK_M",
    "EXTRACT",
    "NO_SIGNALS",
    "REFUSE_CODES",
    "DetectorPlan",
    "DraftSignals",
    "NodeSignals",
    "SignalGroupPlan",
    "SignalHeadPlan",
    "SignalSettings",
    "build_int_supply_data",
    "build_vissig_document",
    "generate_node_signals",
    "generate_signals",
    "node_approaches",
    "signal_containers",
    "signal_provenance_note",
]
