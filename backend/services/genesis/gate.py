"""The R1.5 validation gate: the nine checks a generated network must survive
before anyone is allowed to call it built.

Roadmap 3.3, Genesis (d). The contract is
``docs/product/tk-requirements-2026-07-31.md`` §R1.5, which fixes the nine
checks and their tiers and leaves the thresholds to be settled by measurement
here. Refuse tier: V1 load oracle, V2 reference integrity, V3 connectivity,
V4 lane continuity, V5 signal–geometry consistency, V6 demand coverage
(structure; capacity is warn). Warn tier: V7 geometry sanity, V8 conflict
coverage, V9 measurement readiness. *"Build isn't done until every REFUSE-tier
check passes; WARN-tier findings ride on the result."*

THE RULE THIS MODULE IS BUILT AROUND
------------------------------------
**A check that cannot run reports NOT_CHECKABLE with a named reason. It never
counts as a pass, and it never lets the gate go green.**

That is not a style preference, it is this repository's two most expensive
lessons written as code:

* ``LoadNet`` returns SUCCESS on a network file stripped of its support
  containers and holds **zero links** — measured 2026-08-06, and
  ``tools_check._load_check`` passed such a file on both of its gates. A load
  oracle that only asks "did load succeed?" is an oracle that manufactures
  confidence, so :func:`_check_v1` compares the COM object census to the file's
  own element count and refuses to answer at all without one.
* A schema guard that protected only the first open of a database looked green
  for every run after it (2026-08-10). A check that quietly did not execute is
  worse than no check.

So every check resolves to exactly one of three states, and the resolution
happens in ONE place — :meth:`CheckResult.build` — with a fixed precedence:

    ``FAIL`` (a fault was proven)  >  ``NOT_CHECKABLE`` (something the check
    needed was missing)  >  ``PASS`` (everything it needed was present and
    every assertion held).

A check with a finding is FAIL even if part of it could not run: a proven fault
is a definite answer. A check with any gap can never be PASS. There is no code
path that produces PASS from an empty result set without also proving that the
population it examined was non-empty and reachable — a "vacuous pass" (zero
signal heads, therefore every signalised approach has a head) is exactly the
shape of the failures above and is reported as NOT_CHECKABLE instead.

VERDICT
-------
:class:`GateVerdict` is ``PASS`` only when every refuse-tier check is genuinely
``PASS`` — which includes V1, so an **offline run can never report an
unqualified PASS**: V1 is the only check that proves a real Vissim opens the
file, and without its evidence the verdict is ``INCOMPLETE`` with V1 named.
Any refuse-tier ``FAIL`` makes the verdict ``REFUSED``. Warn-tier results ride
along and never change the verdict.

V1 AND COM
----------
Nothing in this module imports Vissim, touches COM, or needs a licence seat —
``services.genesis`` is offline by construction and stays that way. V1 consumes
the evidence that ``backend/tools/genesis_open_live_check.py`` already produces
(``attach_to_running=False``, clone into a work dir, ``LoadNet`` → ``.err`` →
``SaveNetAs`` → disconnect, bounded child process). :meth:`LoadOracle.
from_probe_state` reads that probe's ``state.json`` and — this is the part that
matters — **proves the evidence is about the file being gated** by comparing
the sha256 of the network the probe actually loaded. A load oracle for a
different file is not weak evidence, it is no evidence, and it reports
NOT_CHECKABLE.

THRESHOLDS, AND WHERE EACH ONE COMES FROM
-----------------------------------------
Every number this module judges by is either read out of the file under test or
derived from a constant this repository already owns. None was chosen because
it made a result look better.

======================================= ============================================
:data:`ZERO_LENGTH_M` = 0.0005 m        Half of the emitter's own coordinate
                                        quantum. ``draft.COORD_DECIMALS`` = 3, so
                                        coordinates are written to 1 mm and a
                                        length below half that is not
                                        representable as non-zero in the file at
                                        all. Not an error budget — the precision
                                        limit of the artifact being judged.
:data:`OVERLAP_DECIMALS` = 3            Same quantum, used for polyline identity:
                                        two links "overlap" when their polylines
                                        are equal at the precision the file was
                                        written in. Exact, not approximate.
:data:`SHORT_LINK_FLOOR_M` = 2.0 m      ``draft.MIN_LINK_LENGTH_M`` — the
                                        generator's OWN declared floor, below
                                        which it drops a segment as a named skip.
                                        A link in the file below its own
                                        generator's floor is a contradiction
                                        between the artifact and its provenance,
                                        so the number is the generator's, not
                                        this module's.
lane-change runway                      **Read from the file**: each link's own
                                        ``lnChgDist`` attribute (200 m on every
                                        genesis emission, itself measured from a
                                        Vissim save — ``schema.LINK_COMMON_ATTRS``).
                                        V4 never invents a distance; it asks
                                        whether the network provides the runway
                                        the network itself declares it needs.
:data:`ROUGH_LANE_CAPACITY_VEH_PER_H`   1900 veh/h/lane, mirrored from
                                        ``services.agent.constraints`` — the
                                        SAME constant the ``latent_demand_over-
                                        flow`` constraint judges by, imported
                                        rather than restated so the gate and the
                                        edit-time guardrail can never disagree.
======================================= ============================================

WHAT A CHECK MAY REFUSE ON SOMEBODY ELSE'S FILE (2026-08-11, roadmap §5.A A5)
------------------------------------------------------------------------------
This gate was built against what ``services.genesis`` emits, and until it was
pointed at a real consultant's ``.inpx`` nothing distinguished "a property of
every valid Vissim network" from "a property of the networks we happen to
build". On the first foreign file it refused with 0 pass / 5 fail, and **the
overwhelming majority of those findings were about us, not about the file** —
253 fabricated reference failures, 33 lane-continuity findings on connectors
that model a lane GAIN, six capacity warnings that were six time intervals of
ordinary demand added together. A gate that manufactures faults destroys
exactly the trust it exists to create, so each clause now carries the scope its
evidence supports:

* **Universal** — proven against THE CORPUS (below): reference integrity in a
  MEASURED context, lane references naming lanes that exist,
  head→controller/group/lane integrity, reachability. These refuse any file.
* **Provenance-scoped** — true of phasing and demand THIS generator imported,
  and merely an observation about a model it did not build (R1.5's own wording:
  "임포트된 phasing의 …"). Measured on PTV's own 2026 example library (411
  files): 304 of 1,387 signal groups are displayed by no head, 45 of 292
  controllers have no head at all, and 60 of 1,116 vehicle inputs carry zero
  volume — none of which can be a universal defect. These REFUSE a network of
  ours and ride as NAMED WARNINGS only on one PROVEN to be somebody else's.
* **Not measured** — a reference in a context the table has never measured is
  named (``v2_reference_context_not_measured``), never resolved against the
  nearest plausible id space; an element type whose reference surface was never
  enumerated makes V2 NOT_CHECKABLE with the tags named.

The recurring lesson this is an instance of is recorded in ``DECISIONS.md``
(2026-08-07, ``a522ec6``): *a live check's negative generalises only to its
sampled population*. Every scope above names its population.

PROVENANCE HAS THREE STATES, AND ONLY ONE OF THEM SOFTENS A CHECK
-----------------------------------------------------------------
:class:`Provenance` is ``OURS`` / ``FOREIGN`` / ``UNKNOWN``, never a boolean.
Deciding it by the mere PRESENCE of a ``*.genesis.json`` failed OPEN in both
directions: a genesis network whose census went missing mid-pipeline scored as
"not ours" and had its refuse-tier V5/V6 findings demoted to warnings — which
turns REFUSED into INCOMPLETE, and ``genesis_jobs.gate_and_publish`` registers
everything that is not REFUSED, so the defective network became the workspace's
live dataset. So:

* ``OURS`` needs the census to be **proven to describe this artifact** — the id
  sets of its links, connectors and nodes against the file's own
  (:func:`_census_identity`), which is V1's sha256 admissibility rule applied to
  the other piece of evidence this gate is handed. It survives a Vissim
  ``SaveNetAs`` round trip (ids preserved) and rejects an earlier build of the
  same source (they are not).
* ``FOREIGN`` needs POSITIVE evidence: a top-level container the emitter has no
  code to write (:data:`GENERATOR_TOP_LEVEL_CONTAINERS`, imported from the
  emitter's own schema), and no provenance comment of ours in the file.
* ``UNKNOWN`` is everything else, and it grades exactly as strictly as ``OURS``.
  A refuse-tier check must never be softened by the ABSENCE of evidence.

The decision, its evidence and the tier each check applied are on
:attr:`GateResult.provenance`, in V5's and V6's own ``evidence``, on the report's
front page and in the job record's compact summary — an invisible tier switch
makes the same verdict word mean two different things without saying so.

WHAT IS OFFLINE-ONLY, STATED HERE RATHER THAN DISCOVERED LATER
--------------------------------------------------------------
* V1 needs the live probe's evidence. Without it: NOT_CHECKABLE.
* V7's "link length vs map distance" needs an independently-authored reference
  network; ``benchmark.py`` does that comparison and needs a second file. Without
  one: a named gap.
* V8's real question is the RIGHT-OF-WAY of the conflict areas Vissim derives
  from geometry at load time. Conflict areas have no Add/Remove over COM and are
  not written to the ``.inpx`` at all, so their Passive/priority status cannot
  be read from a file by anything, ever. V8 counts the unsignalised crossing
  points (the evidence) and reports the status question as a gap.
* V3's distinction between "a vehicle leaves the model here because this is the
  model boundary" and "a vehicle leaves the model here silently" is not in the
  ``.inpx`` — it is in the generator's own census
  (``metadata['connectivity']['dangling_approaches']``). Pass the draft's
  ``*.genesis.json`` and V3 checks it; without one, that half is a gap.

DETERMINISM
-----------
Same input, same verdict, byte-identical JSON: every collection is sorted by an
explicit key before it is emitted, ids sort numerically, and nothing that varies
between runs (time, host, path outside the inputs) is written.
"""
from __future__ import annotations

import hashlib
import heapq
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .connectivity import BENIGN_DANGLING_CAUSES
from .diff import (
    DiffFinding,
    NetworkTopology,
    _dangling_references,
    _id_sort_key,
    load_topology,
)
from .draft import COORD_DECIMALS, MIN_LINK_LENGTH_M, pick_text
# The emitter's OWN declaration of every top-level container it is able to
# write: ``emit.build_tree`` raises ``AssertionError`` if it ever builds one
# this tuple does not name, so provenance cannot drift away from the emitter by
# restating the list here.
from .schema import CONTAINER_ORDER as _EMITTED_CONTAINERS

# ``services.agent.constraints`` owns the rough capacity the edit-time
# ``latent_demand_overflow`` guardrail judges by. Mirrored (imported, not
# restated) so a network this gate passes on capacity cannot be one the agent
# would refuse to edit for the same reason, and vice versa. The import is
# read-only; nothing in this module touches the constraint registry.
from services.agent.constraints import (  # noqa: E402
    _ROUGH_LANE_CAPACITY_VEH_PER_H as ROUGH_LANE_CAPACITY_VEH_PER_H,
)

__all__ = [
    "CheckClause",
    "CheckResult",
    "CheckState",
    "CheckTier",
    "GENERATOR_PROVENANCE_MARKER",
    "GENERATOR_TOP_LEVEL_CONTAINERS",
    "GENERIC_REFERENCE_TAGS",
    "MEASURED_REFERENCE_SURFACE_TAGS",
    "GateFinding",
    "GateGap",
    "GateResult",
    "GateSettings",
    "GateVerdict",
    "LoadOracle",
    "OVERLAP_DECIMALS",
    "Provenance",
    "ProvenanceEvidence",
    "ROUGH_LANE_CAPACITY_VEH_PER_H",
    "SHORT_LINK_FLOOR_M",
    "ZERO_LENGTH_M",
    "render_report",
    "run_gate",
]


# ---------------------------------------------------------------------------
# Thresholds — every one derived; see the module docstring's table
# ---------------------------------------------------------------------------

#: Polyline identity precision: the emitter's own coordinate decimals.
OVERLAP_DECIMALS = COORD_DECIMALS

#: A length below half the emitted coordinate quantum (1 mm at
#: ``COORD_DECIMALS`` = 3) cannot be represented as non-zero in the file, so it
#: IS zero as far as the artifact is concerned.
ZERO_LENGTH_M = 0.5 * (10.0 ** -COORD_DECIMALS)

#: The generator's own declared minimum segment length. A link shorter than the
#: floor its own generator drops segments at is a contradiction worth naming.
SHORT_LINK_FLOOR_M = MIN_LINK_LENGTH_M

#: The attribute each link uses to declare how much road a vehicle needs to
#: change lanes. V4 reads it per link rather than assuming a value.
LANE_CHANGE_DISTANCE_ATTR = "lnChgDist"


class GateSettings(BaseModel):
    """Compute budgets. These are NOT modelling thresholds — exhausting one
    makes a check NOT_CHECKABLE with the budget named, never a pass."""

    model_config = ConfigDict(extra="forbid")

    lane_change_search_budget: int = Field(
        default=20000,
        gt=0,
        description=(
            "Node expansions allowed when measuring the upstream road available "
            "to one lane drop. Exhaustion is reported, never rounded away."
        ),
    )
    conflict_pair_budget: int = Field(
        default=2_000_000,
        gt=0,
        description="Connector-pair crossing tests allowed across the whole network.",
    )
    max_subjects_rendered: int = Field(
        default=12,
        gt=0,
        description="Subjects listed per finding in the human report. The data carries all.",
    )


DEFAULT_SETTINGS = GateSettings()


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class CheckState(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_CHECKABLE = "not_checkable"


class CheckTier(str, Enum):
    REFUSE = "refuse"
    WARN = "warn"


class GateVerdict(str, Enum):
    #: Every refuse-tier check genuinely passed, V1 included.
    PASS = "pass"
    #: At least one refuse-tier check proved a fault.
    REFUSED = "refused"
    #: No refuse-tier fault, but at least one refuse-tier check could not run.
    INCOMPLETE = "incomplete"


class Provenance(str, Enum):
    """Who built the network under test — in THREE states, not two.

    A handful of R1.5's clauses are about *the phasing and the demand THIS
    generator imported* ("임포트된 phasing의 …"). They are contradictions of the
    artifact's own provenance when we built it, and merely observations about
    somebody else's modelling choice when we did not — PTV's own 2026 example
    library has 304 of 1,387 signal groups displayed by no head, so neither can
    be a universal defect.

    The trap is that "we cannot prove we built it" is NOT "somebody else built
    it". Scoring an unproven file as foreign softens a refuse-tier check on the
    strength of missing evidence, and the pipeline publishes anything that is
    not REFUSED — so a genesis network whose census went missing mid-pipeline
    would reach the workspace carrying the very defect the clause exists to
    catch. **UNKNOWN therefore grades exactly as strictly as OURS.** Only
    :attr:`FOREIGN` — which needs POSITIVE evidence — softens anything.
    """

    #: The generator's own census, PROVEN to describe this artifact.
    OURS = "ours"
    #: Positive evidence to the contrary: the file declares a top-level
    #: container ``services.genesis.emit`` cannot write.
    FOREIGN = "foreign"
    #: Neither. Graded strictly, and named as unproven wherever the verdict goes.
    UNKNOWN = "unknown"


#: Every top-level container the emitter is ABLE to write. Imported from the
#: emitter's own schema rather than restated, so the two cannot drift: an
#: element type outside this set is something ``services.genesis`` has no code
#: to produce, which is why its presence is evidence and its absence is not.
GENERATOR_TOP_LEVEL_CONTAINERS: frozenset[str] = frozenset(_EMITTED_CONTAINERS)

#: The opening words of the XML comment ``emit.provenance_comment`` writes into
#: every file this generator produces. Its presence VETOES a foreign verdict; its
#: absence proves nothing, because Vissim's own ``SaveNetAs`` drops comments
#: (measured on this repository's round-tripped Murdoch).
GENERATOR_PROVENANCE_MARKER = "Generated by Prism services.genesis"

#: How far into the file the marker is looked for. ``emit.build_tree`` appends
#: the comment as the FIRST child of ``<network>``, so a bounded read is exact
#: rather than a heuristic — and a gate does not read a 7 MB file twice for it.
_PROVENANCE_MARKER_WINDOW_BYTES = 8192


class ProvenanceEvidence(BaseModel):
    """Why the gate believes what it believes about who built this file.

    Carried on :class:`GateResult`, echoed into the evidence of every check that
    changes tier because of it, printed in the report and copied into the job
    record's compact summary. An invisible tier switch makes the same verdict
    word mean two different things without saying so.
    """

    model_config = ConfigDict(frozen=True)

    state: Provenance
    #: ``absent`` | ``matches_this_network`` | ``describes_another_network`` |
    #: ``declares_no_elements``. Anything but ``matches_this_network`` means the
    #: census is INADMISSIBLE — V1's rule for its load oracle, applied to the
    #: other piece of evidence this gate is handed about a file.
    census: str
    #: Whether the emitter's own provenance comment is in the file.
    generator_marker: bool
    #: Top-level containers the emitter cannot write, in tag order. Counts and
    #: tag names only — never an id, never a value.
    foreign_containers: list[str] = Field(default_factory=list)
    #: Element counts behind the identity verdict, so a stale sidecar is legible.
    census_elements: int = 0
    network_elements: int = 0

    @property
    def census_is_admissible(self) -> bool:
        return self.census == "matches_this_network"

    @property
    def grades_strictly(self) -> bool:
        """Whether a provenance-scoped clause REFUSES on this file.

        OURS and UNKNOWN both do. Only proven-foreign softens.
        """
        return self.state is not Provenance.FOREIGN

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "census": self.census,
            "generator_marker": self.generator_marker,
            "foreign_containers": list(self.foreign_containers),
            "census_elements": self.census_elements,
            "network_elements": self.network_elements,
        }


def _census_identity(
    metadata: dict[str, Any] | None,
    *,
    network_links: set[str],
    network_nodes: set[str],
) -> tuple[str, int, int]:
    """Does this ``*.genesis.json`` describe THIS ``.inpx``?

    The same admissibility rule :class:`LoadOracle` already applies to V1's live
    evidence: a record about a different file is not weak evidence, it is none.
    A ``.genesis.json`` is the draft's whole ``model_dump``, so it names every
    link, connector and node it built — and the emitter writes each one's ``id``
    straight into ``<link no>`` / ``<node no>``. Comparing those id SETS ties the
    census to the artifact without needing the emitter to record a hash it does
    not record, and it survives the one transformation that legitimately rewrites
    our bytes: a Vissim ``SaveNetAs`` round trip preserves every id (measured on
    this repository's Murdoch: 5,082 links and 761 nodes, identical before and
    after), while an EARLIER BUILD of the same source does not (5,086 links).
    """
    if not isinstance(metadata, dict) or not metadata:
        return "absent", 0, len(network_links) + len(network_nodes)
    census_links = {
        str(entry["id"])
        for key in ("links", "connectors")
        for entry in (metadata.get(key) or [])
        if isinstance(entry, dict) and entry.get("id") is not None
    }
    census_nodes = {
        str(entry["id"])
        for entry in (metadata.get("nodes") or [])
        if isinstance(entry, dict) and entry.get("id") is not None
    }
    census_count = len(census_links) + len(census_nodes)
    network_count = len(network_links) + len(network_nodes)
    if not census_links and not census_nodes:
        return "declares_no_elements", census_count, network_count
    if census_links == network_links and census_nodes == network_nodes:
        return "matches_this_network", census_count, network_count
    return "describes_another_network", census_count, network_count


def _decide_provenance(
    root: ET.Element,
    metadata: dict[str, Any] | None,
    *,
    network_links: set[str],
    network_nodes: set[str],
    generator_marker: bool,
) -> ProvenanceEvidence:
    """Three states, and only one of them softens a check.

    ``OURS`` needs the census to be admissible. ``FOREIGN`` needs positive
    evidence — a top-level container the emitter has no code to write, and no
    provenance comment of ours in the file. Everything else is ``UNKNOWN`` and
    is graded exactly as strictly as ours.
    """
    census, census_count, network_count = _census_identity(
        metadata, network_links=network_links, network_nodes=network_nodes
    )
    foreign = sorted(
        {child.tag for child in root if child.tag not in GENERATOR_TOP_LEVEL_CONTAINERS}
    )
    if census == "matches_this_network":
        state = Provenance.OURS
    elif foreign and not generator_marker:
        state = Provenance.FOREIGN
    else:
        state = Provenance.UNKNOWN
    return ProvenanceEvidence(
        state=state,
        census=census,
        generator_marker=generator_marker,
        foreign_containers=foreign,
        census_elements=census_count,
        network_elements=network_count,
    )


def _carries_generator_marker(path: Path) -> bool:
    """Is the emitter's own provenance comment in the head of this file?"""
    try:
        with path.open("rb") as handle:
            head = handle.read(_PROVENANCE_MARKER_WINDOW_BYTES)
    except OSError:  # pragma: no cover - the caller already read the file
        return False
    return GENERATOR_PROVENANCE_MARKER.encode("utf-8") in head


class GateFinding(BaseModel):
    """One proven fault, naming the object it is about."""

    model_config = ConfigDict(frozen=True)

    code: str
    subject: str
    context: dict[str, Any] = Field(default_factory=dict)

    def message(self, language: str = "ko") -> str:
        template = _TEXT.get(self.code)
        if template is None:  # pragma: no cover - every code has text
            return f"{self.code}: {self.subject}"
        return pick_text(template, language).format(subject=self.subject, **self.context)


class GateGap(BaseModel):
    """One reason a check could not run, naming precisely what was missing."""

    model_config = ConfigDict(frozen=True)

    code: str
    context: dict[str, Any] = Field(default_factory=dict)

    def message(self, language: str = "ko") -> str:
        template = _TEXT.get(self.code)
        if template is None:  # pragma: no cover - every code has text
            return self.code
        return pick_text(template, language).format(**self.context)


class CheckClause(BaseModel):
    """One named sub-question inside a check, with its OWN state.

    A check is one line in a verdict, but most of the nine ask several
    independent questions and a reader deserves to know which of them ran. V3
    is the case that forced this: on a consultant's ``.inpx`` its reachability
    half runs in full and proves ``orphan_links=0`` /
    ``links_in_a_broken_zone=0``, while its silent-departure half needs the
    generator's own census and cannot — and reporting the whole check as one
    grey box threw away a real, earned result.

    A clause NEVER SOFTENS the check: :meth:`CheckResult.build` takes the worst
    of the check's own findings/gaps and its clauses' states, so a check with an
    unrunnable clause is never a PASS. That rule lives in exactly one place, and
    it is enforced there rather than trusted to each check — V5 and V6 both
    reported ``PASS`` over a ``NOT_CHECKABLE`` clause until 2026-08-11, because
    each had appended its "could not certify this" gap to the clause's own list
    and not to the check's.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    state: CheckState

    def message(self, language: str = "ko") -> str:
        template = _TEXT.get(self.code)
        if template is None:  # pragma: no cover - every clause code has text
            return self.code
        return pick_text(template, language)


def _clause(
    code: str,
    *,
    findings: Iterable[GateFinding] = (),
    gaps: Iterable[GateGap] = (),
) -> CheckClause:
    """A clause's state by the SAME precedence the check itself uses."""
    if any(True for _ in findings):
        state = CheckState.FAIL
    elif any(True for _ in gaps):
        state = CheckState.NOT_CHECKABLE
    else:
        state = CheckState.PASS
    return CheckClause(code=code, state=state)


class CheckResult(BaseModel):
    """One of the nine checks, in exactly one state.

    Build these with :meth:`build` and never by setting ``state`` directly: the
    precedence FAIL > NOT_CHECKABLE > PASS lives there and nowhere else, so a
    check cannot report a pass it did not earn — and that precedence is applied
    to the check's CLAUSES as well as to its own findings and gaps.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tier: CheckTier
    state: CheckState
    findings: list[GateFinding] = Field(default_factory=list)
    #: Findings from a WARN clause inside this check. R1.5 gives V6 exactly one
    #: — "input volume vs link capacity … warn(용량)" inside a refuse-tier check
    #: — so warn observations need somewhere to live that does not fail a refuse
    #: check. They ride on the result and never touch :attr:`state`.
    warnings: list[GateFinding] = Field(default_factory=list)
    gaps: list[GateGap] = Field(default_factory=list)
    #: Named sub-questions and how each one resolved. Reporting only — see
    #: :class:`CheckClause`; they never change :attr:`state`.
    clauses: list[CheckClause] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        check_id: str,
        tier: CheckTier,
        *,
        findings: Iterable[GateFinding] = (),
        warnings: Iterable[GateFinding] = (),
        gaps: Iterable[GateGap] = (),
        clauses: Iterable[CheckClause] = (),
        evidence: dict[str, Any] | None = None,
    ) -> "CheckResult":
        ordered_findings = sorted(findings, key=_finding_sort_key)
        ordered_warnings = sorted(warnings, key=_finding_sort_key)
        ordered_gaps = sorted(gaps, key=lambda g: g.code)
        ordered_clauses = sorted(clauses, key=lambda c: c.code)
        # The clauses are part of the answer, not a commentary on it. A check
        # whose own sub-question could not run has not certified what it claims
        # to certify, so it cannot be a PASS — enforced HERE, once, rather than
        # depending on every check remembering to copy its clause's gap up.
        clause_states = {clause.state for clause in ordered_clauses}
        if ordered_findings or CheckState.FAIL in clause_states:
            state = CheckState.FAIL
        elif ordered_gaps or CheckState.NOT_CHECKABLE in clause_states:
            state = CheckState.NOT_CHECKABLE
        else:
            state = CheckState.PASS
        return cls(
            id=check_id,
            tier=tier,
            state=state,
            findings=ordered_findings,
            warnings=ordered_warnings,
            gaps=ordered_gaps,
            clauses=ordered_clauses,
            evidence=dict(sorted((evidence or {}).items())),
        )

    def title(self, language: str = "ko") -> str:
        return pick_text(_CHECK_TITLE[self.id], language)

    @property
    def blocks(self) -> bool:
        """Whether this result stops the gate going green."""
        return self.tier is CheckTier.REFUSE and self.state is not CheckState.PASS


def _subject_sort_key(subject: str) -> tuple:
    """Sort a finding's subject numerically when it starts with an object id.

    Subjects read ``"630 (South Street [FWD] w406690969s0)"`` — id first, the
    generator's own name after it — so sorting the whole string would put 1064
    before 187. The leading token goes through the diff module's own
    numeric-aware key; the rest breaks ties.
    """
    head, _, tail = subject.partition(" ")
    return (_id_sort_key(head), tail)


def _finding_sort_key(finding: "GateFinding") -> tuple:
    return (finding.code, _subject_sort_key(finding.subject))


class GateResult(BaseModel):
    """The whole gate: nine checks in V1..V9 order, and one verdict."""

    model_config = ConfigDict(frozen=True)

    network: str
    network_sha256: str
    verdict: GateVerdict
    #: Who built this file, and on what evidence. Refuse-tier checks change tier
    #: on it, so it travels with the verdict rather than being reconstructible
    #: only by a reader of the full per-check clause detail.
    provenance: ProvenanceEvidence = Field(
        default_factory=lambda: ProvenanceEvidence(
            state=Provenance.UNKNOWN, census="absent", generator_marker=False
        )
    )
    checks: list[CheckResult] = Field(default_factory=list)

    def check(self, check_id: str) -> CheckResult:
        for item in self.checks:
            if item.id == check_id:
                return item
        raise KeyError(check_id)  # pragma: no cover - ids are fixed

    @property
    def refuse_tier(self) -> list[CheckResult]:
        return [c for c in self.checks if c.tier is CheckTier.REFUSE]

    @property
    def warn_tier(self) -> list[CheckResult]:
        return [c for c in self.checks if c.tier is CheckTier.WARN]

    @property
    def blocking(self) -> list[CheckResult]:
        return [c for c in self.checks if c.blocks]

    def counts(self) -> dict[str, int]:
        return {
            state.value: sum(1 for c in self.checks if c.state is state)
            for state in (CheckState.PASS, CheckState.FAIL, CheckState.NOT_CHECKABLE)
        }

    def as_dict(self) -> dict[str, Any]:
        """Deterministic, JSON-safe. Same input, byte-identical output."""
        return {
            "network": self.network,
            "network_sha256": self.network_sha256,
            "verdict": self.verdict.value,
            "provenance": self.provenance.as_dict(),
            "counts": self.counts(),
            "checks": [
                {
                    "id": c.id,
                    "tier": c.tier.value,
                    "state": c.state.value,
                    "findings": [
                        {"code": f.code, "subject": f.subject, "context": f.context}
                        for f in c.findings
                    ],
                    "warnings": [
                        {"code": f.code, "subject": f.subject, "context": f.context}
                        for f in c.warnings
                    ],
                    "gaps": [{"code": g.code, "context": g.context} for g in c.gaps],
                    "clauses": [
                        {"code": cl.code, "state": cl.state.value} for cl in c.clauses
                    ],
                    "evidence": c.evidence,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# V1's evidence: the live-check probe's own output, proven to be about THIS file
# ---------------------------------------------------------------------------


class LoadOracle(BaseModel):
    """What a real PTV Vissim did with one network file.

    Produced by ``backend/tools/genesis_open_live_check.py`` (or the full-stack
    probe, whose child writes the same shape). This module never opens COM; it
    reads that probe's record. ``network_sha256`` is what makes the record
    admissible: the same bytes, or it is evidence about a different file.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    network_path: str
    network_sha256: str | None = None
    load_ok: bool | None = None
    load_error: str | None = None
    save_ok: bool | None = None
    new_err_files: list[str] = Field(default_factory=list)
    err_text: str | None = None
    com_link_count: int | None = None
    com_connector_count: int | None = None
    com_plain_link_count: int | None = None
    com_node_count: int | None = None
    vissim_version: str | None = None

    @classmethod
    def from_probe_state(
        cls, state: dict[str, Any], *, label: str | None = None
    ) -> tuple["LoadOracle | None", str | None]:
        """Read one run out of ``genesis-open-live-check/state.json``.

        Returns ``(oracle, None)`` or ``(None, reason)``. ``label`` picks the
        run; omitted, the newest run that carries a load result wins — "newest"
        by the probe's own ``started_utc``, never by dict order.
        """
        runs = state.get("runs")
        if not isinstance(runs, dict) or not runs:
            return None, "no_runs_in_state"
        if label is not None:
            entry = runs.get(label)
            if entry is None:
                return None, f"label_absent:{label}"
            chosen = label
        else:
            candidates = [
                (str(v.get("started_utc") or ""), k)
                for k, v in runs.items()
                if isinstance(v, dict) and isinstance(v.get("run"), dict)
            ]
            if not candidates:
                return None, "no_runs_in_state"
            chosen = sorted(candidates)[-1][1]
            entry = runs[chosen]
        com = (entry or {}).get("run", {}).get("com", {})
        if not isinstance(com, dict) or "load_ok" not in com:
            return None, f"run_carries_no_load_result:{chosen}"

        network_path = str(com.get("network") or entry.get("network") or "")
        digest: str | None = None
        candidate = Path(network_path)
        if network_path and candidate.is_file():
            digest = _sha256(candidate)

        topology = com.get("com_topology") or {}
        census = topology.get("is_conn_counts") or {}
        err = com.get("err_after_load") or {}
        new_files = [str(name) for name in (err.get("new_files") or [])]
        err_text = "\n".join(
            str(f.get("text") or "")
            for f in (err.get("files") or [])
            if f.get("name") in new_files
        ) or None
        version = ((com.get("version") or {}).get("att_values") or {}).get("Version")

        return (
            cls(
                label=chosen,
                network_path=network_path,
                network_sha256=digest,
                load_ok=com.get("load_ok"),
                load_error=com.get("load_error"),
                save_ok=com.get("save_ok"),
                new_err_files=sorted(new_files),
                err_text=err_text,
                com_link_count=_as_int(topology.get("link_collection_count")),
                com_connector_count=_as_int(census.get("connectors")),
                com_plain_link_count=_as_int(census.get("plain_links")),
                com_node_count=_as_int(topology.get("nodes_count")),
                vissim_version=str(version) if version else None,
            ),
            None,
        )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Reference integrity table (V2) — MEASURED, not guessed, and CONTEXT-KEYED
#
# Every entry is keyed on ``(parent element tag, element tag, attribute)``, not
# on the element tag alone, because **the same element name means different
# things under different parents**. ``<intObjectRef key="…"/>`` is the whole
# argument: it is Vissim's generic "this set contains that object" element, and
# its referent is named by the CONTAINER, never by itself. Measured across the
# corpus below, one element name resolves against seven different id spaces:
#
#     <linkSeq>  links · <vehClasses>/<blockedVehClasses>/<blockedVehClasses-
#     DynAssign>  vehicle classes · <vehTypes>  vehicle types · <pedTypes>
#     pedestrian types · <wttFiles>  WTT files · <dataCollectionPoints>  data-
#     collection points · <ptStops>  PT stops · <ptLines>  PT lines ·
#     <actLvls>  levels · <vehTravTmMeas>  travel-time measurements
#
# Keyed on the element name alone (as this table was until 2026-08-11), every
# non-``<linkSeq>`` occurrence was resolved against the LINK table. On the first
# real consultant model that produced **253 fabricated "unresolved reference"
# findings** — and, in the other direction, silently GRADED 3 vehicle-type
# references against the link id space, where they happened to resolve. Both
# halves of that are the same bug: a reference judged against the wrong universe.
#
# THE CORPUS — ONE NUMBER, MEASURED (``backend/tools/probe_reference_contexts.py``)
# ------------------------------------------------------------------------------
# **825 real ``.inpx`` files**, every one of them readable, and this is the ONLY
# corpus size this module quotes: 411 from PTV's own Vissim 2026 "Examples Demo"
# and "Examples Training" libraries, 380 from the Vissim 2024 ones, 33 of this
# repository's own live-check networks, and 1 hand-authored fixture. (The table
# was first measured against roughly half that corpus, and several of the counts
# in this docstring were never re-run when it grew — every number below is a
# re-run against the 825 on 2026-08-11, and ``--audit`` is the tool that keeps
# them honest.) The one real consultant model is deliberately NOT in the default
# corpus: ``client-data/`` and ``runtime/workspaces/`` are confidential and are
# passed explicitly with ``--root`` when a measurement needs them.
#
# For every ``(parent, tag, attribute)`` site the probe collects the values and
# asks which of the id spaces the SAME file declares contains all of them; a
# target is admitted only when it is the unanimous answer in EVERY file the site
# occurs in, over enough distinct values that coincidence is implausible. What
# survives is READ and registered by hand — never auto-registered — exactly as
# ``inpx_structure.ID_REFERENCE_SITES`` (this repository's other, older
# reference registry, xpath-keyed for the same reason) was built.
#
# Five generic-membership sites are deliberately left UNREGISTERED, and the
# reason differs between them, so it is stated per site rather than in one
# sentence that fits none of them. Four are genuinely ambiguous — no single id
# space wins: ``<sectFilt>`` (6 files, 5 candidate id spaces), ``<evalGrps>``
# (17 files, 2), ``<sections>`` (91 files, 2), ``<mesoSectsForMicroSim>`` (2
# files, 21 — too few samples to exclude anything). The fifth,
# ``<walkPedClasses>``, DOES have a single unanimous candidate
# (``pedestrianClass``) and is left out on VOLUME instead: 4 occurrences of one
# distinct value across 4 files is below the probe's own
# ``MIN_DISTINCT_VALUES``, which is not enough to tell a reference from a
# coincidence. All five are NAMED as unmeasured rather than resolved against the
# most plausible guess.
#
# Why a table at all, rather than "resolve everything numeric": a numeric
# attribute is far more often a parameter than a reference (``latDistDrivDef``
# is a lateral distance, ``alc_v_limit`` a speed), and the probe's own output
# proves it — ``occupancyDistribution/@mean`` resolves unanimously against the
# occupancy-distribution id space across 402 files and is a MEAN, not a
# reference. Guessing would produce findings about numbers that reference
# nothing.
#
# The cost of a table is that it can fall behind. That is handled twice over:
# :data:`MEASURED_REFERENCE_SURFACE_TAGS` is the tag inventory the table's own
# reference surface was enumerated against, and any element carrying a reference
# in a context outside the table is NAMED (``v2_reference_context_not_measured``)
# instead of resolved. A gate that says "I have not measured ``<foo>/@bar``" is
# honest; one that assumes is the bug this table exists to have stopped making.
# ---------------------------------------------------------------------------

#: Elements whose ONLY purpose is to reference another object — they carry no
#: content of their own, so every attribute on one is a reference by
#: construction. An occurrence in a context the table has not measured is
#: therefore a reference we know we cannot resolve, and it is reported as a gap
#: rather than resolved against the nearest plausible id space.
GENERIC_REFERENCE_TAGS: frozenset[str] = frozenset({"intObjectRef", "objectRef"})

#: ``(parent tag, element tag, attribute) -> target element tag``. A plain id
#: reference. A TUPLE of tags is one object class Vissim has spelled more than
#: one way across schema versions — resolved against the union, which is exact
#: rather than lax because the spellings never coexist (measured; see
#: ``desSpeedDistr`` below).
_SIMPLE_REFERENCES: dict[tuple[str, str, str], str | tuple[str, ...]] = {
    ("links", "link", "displayType"): "displayType",
    ("links", "link", "linkBehavType"): "linkBehaviorType",
    ("links", "link", "level"): "level",
    ("lanes", "lane", "markingType"): "laneMarkingType",
    ("linkBehaviorTypes", "linkBehaviorType", "drivBehavDef"): "drivingBehavior",
    ("drivingBehaviors", "drivingBehavior", "w99cc1Distr"): "timeDistribution",
    ("drivingBehaviors", "drivingBehavior", "zipperOvtTmDistr"): "timeDistribution",
    ("link", "fromLinkEndPt", "link"): "link",
    ("link", "toLinkEndPt", "link"): "link",
    ("detectors", "detector", "sc"): "signalController",
    ("vehicleInputs", "vehicleInput", "link"): "link",
    ("timeIntVehVols", "timeIntervalVehVolume", "vehComp"): "vehicleComposition",
    ("vehCompRelFlows", "vehicleCompositionRelativeFlow", "vehType"): "vehicleType",
    # PTV RENAMED this object class between schema versions: Vissim 2024 writes
    # <desSpeedDistributions><desSpeedDistribution no="…">, Vissim 2026 writes
    # <speedDistributions><speedDistribution no="…">, and ``desSpeedDistr``
    # points at whichever the file declares. Measured over the 804 files of the
    # corpus that declare one: 379 carry only the 2024 spelling, 425 only the
    # 2026 spelling, and NOT ONE carries both — so the union cannot let a
    # dangling reference through, and it repairs a table entry that (measured on
    # this generator's 2026 output alone) fabricated one finding per composition
    # flow on every Vissim 2024 file, 277 of them across the example libraries.
    ("vehCompRelFlows", "vehicleCompositionRelativeFlow", "desSpeedDistr"): (
        "speedDistribution",
        "desSpeedDistribution",
    ),
    (
        "vehicleRoutingDecisionsStatic",
        "vehicleRoutingDecisionStatic",
        "link",
    ): "link",
    ("vehRoutSta", "vehicleRouteStatic", "destLink"): "link",
    # ``data/practice.inpx``'s hand-authored shape puts the route directly under
    # its decision with no ``<vehRoutSta>`` wrapper. Measured, so registered —
    # the same "a real deployment will see either shape" argument
    # ``inpx_structure.ID_REFERENCE_SITES`` makes about connector endpoints.
    ("vehicleRoutingDecisionStatic", "vehicleRouteStatic", "destLink"): "link",
    ("vehicleTypes", "vehicleType", "colorDistr1"): "colorDistribution",
    ("vehicleTypes", "vehicleType", "desAccelFunc"): "desAccelerationFunction",
    ("vehicleTypes", "vehicleType", "maxAccelFunc"): "maxAccelerationFunction",
    ("vehicleTypes", "vehicleType", "desDecelFunc"): "desDecelerationFunction",
    ("vehicleTypes", "vehicleType", "maxDecelFunc"): "maxDecelerationFunction",
    ("vehicleTypes", "vehicleType", "desCurveSpeedFunc"): "curvSpeedFunc",
    ("vehicleTypes", "vehicleType", "model2D3DDistr"): "model2D3DDistribution",
    ("vehicleTypes", "vehicleType", "occupDistr"): "occupancyDistribution",
    ("vehicleTypes", "vehicleType", "powerDistr"): "powerDistribution",
    ("vehicleTypes", "vehicleType", "weightDistr"): "weightDistribution",
    ("model2D3DDistrEl", "model2D3DDistributionElement", "model2D3D"): "model2D3D",
    # -- the generic membership element, one entry per MEASURED container -----
    ("linkSeq", "intObjectRef", "key"): "link",
    ("vehClasses", "intObjectRef", "key"): "vehicleClass",
    ("blockedVehClasses", "intObjectRef", "key"): "vehicleClass",
    ("blockedVehClassesDynAssign", "intObjectRef", "key"): "vehicleClass",
    ("vehTypes", "intObjectRef", "key"): "vehicleType",
    ("pedTypes", "intObjectRef", "key"): "pedestrianType",
    ("pedClasses", "intObjectRef", "key"): "pedestrianClass",
    ("wttFiles", "intObjectRef", "key"): "wttFile",
    ("dataCollectionPoints", "intObjectRef", "key"): "dataCollectionPoint",
    ("vehTravTmMeas", "intObjectRef", "key"): "vehicleTravelTimeMeasurement",
    ("ptStops", "intObjectRef", "key"): "ptStop",
    ("ptLines", "intObjectRef", "key"): "ptLine",
    ("actLvls", "intObjectRef", "key"): "level",
}

#: ``(parent tag, element tag, attribute) -> (owner tag, member tag)``. A
#: ``"<owner no> <member no>"`` pair, the shape Vissim uses for lane and
#: signal-group refs.
_PAIR_REFERENCES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("fromLanes", "objectRef", "key"): ("link", "lane"),
    ("toLanes", "objectRef", "key"): ("link", "lane"),
    ("signalHeads", "signalHead", "lane"): ("link", "lane"),
    ("detectors", "detector", "lane"): ("link", "lane"),
    ("signalHeads", "signalHead", "sg"): ("signalController", "signalGroup"),
    (
        "sigTmsTabConfig",
        "signalOutputConfigurationElement",
        "sg",
    ): ("signalController", "signalGroup"),
    # The same element under the detector-record configuration. Both parents
    # were measured (2,703 occurrences in 199 files under <sigTmsTabConfig>, 60
    # in 4 under <scDetRecConf>, 100 % resolving) rather than one being assumed
    # from the other. All seven pair contexts are re-checked by ``--audit``.
    (
        "scDetRecConf",
        "signalOutputConfigurationElement",
        "sg",
    ): ("signalController", "signalGroup"),
}

#: Attributes that LOOK like references and are deliberately NOT resolved, each
#: with the reason. Published verbatim in V2's evidence, so the scope of the
#: check is a statement a reader can act on rather than an omission they have to
#: notice. English-only and language-neutral, the same way
#: ``site_node_map.TOLERANCE_BASIS`` and ``connectivity.TOLERANCE_BASIS`` carry
#: their derivations: this is data about the method, not user-facing prose.
_UNRESOLVED_REFERENCES: dict[str, str] = {
    "timeIntervalVehVolume/@timeInt": (
        "a '<set> <start>' composite; the target <timeInterval> carries no 'no' attribute, "
        "so nothing in the file can resolve it"
    ),
    "signalController/@intSupplyData": (
        "a base64 blob; the fixed-time program inside it is opaque to COM and to every "
        "parser in this repository"
    ),
}

#: The element types whose reference surface :data:`_SIMPLE_REFERENCES` and
#: :data:`_PAIR_REFERENCES` were enumerated against — the tag inventory of what
#: ``services.genesis`` emits (geometry-only + the four-layer full stack,
#: 2026-08-11).
#:
#: The claim this set makes, stated exactly so nobody has to infer it: **for
#: these element types the table has looked at every attribute and recorded a
#: verdict** — reference (registered above), deliberately unresolvable
#: (:data:`_UNRESOLVED_REFERENCES`), or parameter. For an element type OUTSIDE
#: the set the table has looked at nothing, so V2 reads NOT_CHECKABLE with the
#: tags named. That is why a real consultant model still ends NOT_CHECKABLE
#: after the context fix: the fabricated findings are gone, but a file carrying
#: conflict areas, priority rules, PT lines and parking lots carries reference
#: surfaces this table has never enumerated, and "I did not look" is not "there
#: is nothing there".
#:
#: Widening this set is a measurement job, not an edit: register the element
#: type's reference attributes from ``tools/probe_reference_contexts.py``'s
#: output first, then add the tag.
MEASURED_REFERENCE_SURFACE_TAGS: frozenset[str] = frozenset(
    {
        "accelFuncDataPts", "accelerationFunctionDataPoint", "colorDistrEl",
        "colorDistribution", "colorDistributionElement", "colorDistributions",
        "curvSpeedFunc", "curvSpeedFuncDataPt", "curvSpeedFuncDataPts", "curvSpeedFuncs",
        "decelFuncDataPts", "decelerationFunctionDataPoint", "desAccelerationFunction",
        "desAccelerationFunctions", "desDecelerationFunction", "desDecelerationFunctions",
        "detector", "detectors", "displayType", "displayTypes", "distrDataPts",
        "distributionDataPoint", "door", "doors", "drivingBehavior", "drivingBehaviors",
        "durDistrDataPts", "durationDistributionDataPoint", "fromLanes", "fromLinkEndPt",
        "geometry", "intObjectRef", "lane", "laneMarkingType", "laneMarkingTypes", "lanes",
        "level", "levels", "lineStyle", "link", "linkBehaviorType", "linkBehaviorTypes",
        "linkPolyPoint", "linkPolyPts", "linkSeq", "links", "maxAccelerationFunction",
        "maxAccelerationFunctions", "maxDecelerationFunction", "maxDecelerationFunctions",
        "model2D3D", "model2D3DDistrEl", "model2D3DDistribution",
        "model2D3DDistributionElement", "model2D3DDistributions", "model2D3DSegment",
        "model2D3DSegs", "models2D3D", "netPara", "network", "node", "nodes", "objectRef",
        "occupancyDistribution", "occupancyDistributions", "parkLotSelPars",
        "parkingLotSelectionParameters", "point", "points", "posRel", "powerDistribution",
        "powerDistributions", "rampMeterPara", "sgs", "sigTmsTabConfig", "signalController",
        "signalControllers", "signalGroup", "signalHead", "signalHeads",
        "signalOutputConfigurationElement", "speedDistrDatPts", "speedDistribution",
        "speedDistributionDataPoint", "speedDistributions", "timeDistribution",
        "timeDistributions", "timeIntVehVols", "timeInterval", "timeIntervalSet",
        "timeIntervalSets", "timeIntervalVehVolume", "timeInts", "toLanes", "toLinkEndPt",
        "vehCompRelFlows", "vehRoutSta", "vehicleComposition",
        "vehicleCompositionRelativeFlow", "vehicleCompositions", "vehicleInput",
        "vehicleInputs", "vehicleRouteStatic", "vehicleRoutingDecisionStatic",
        "vehicleRoutingDecisionsStatic", "vehicleType", "vehicleTypes",
        "weightDistribution", "weightDistributions",
        # -- generic-membership CONTAINERS, added 2026-08-11 -----------------
        # Pure wrappers: measured over the whole 825-file corpus, all 13 of these
        # (and ``linkSeq`` above) carry ZERO attributes of their own in every
        # occurrence, so "the reference surface of this element type is
        # enumerated" is a claim the measurement supports outright. Four of them
        # also hold the OBJECT elements themselves (``ptLine``, ``ptStop``,
        # ``wttFile``, ``dataCollectionPoint``) beside the reference children —
        # those object types carry attributes of their own, they are a reference
        # surface nobody has enumerated, and they are deliberately NOT in this
        # set, so a file that has one still reads NOT_CHECKABLE.
        "actLvls", "blockedVehClasses", "blockedVehClassesDynAssign",
        "dataCollectionPoints", "pedClasses", "pedTypes", "ptLines", "ptStops",
        "scDetRecConf", "vehClasses", "vehTravTmMeas", "vehTypes", "wttFiles",
    }
)

#: ``(element tag, attribute)`` pairs the table resolves under AT LEAST ONE
#: parent. An element carrying one of these attributes under a DIFFERENT parent
#: is a measured reference surface in an unmeasured context — named, never
#: resolved against the context we happen to know.
_REFERENCE_SURFACE: frozenset[tuple[str, str]] = frozenset(
    {(tag, attribute) for _, tag, attribute in _SIMPLE_REFERENCES}
    | {(tag, attribute) for _, tag, attribute in _PAIR_REFERENCES}
)


# ---------------------------------------------------------------------------
# The facts one network file states about itself
# ---------------------------------------------------------------------------


class _LinkFacts:
    """One ``<link>``, plain or connector, as the gate needs it."""

    __slots__ = (
        "id", "name", "is_connector", "lanes", "points", "length_m",
        "from_link", "to_link", "from_pos", "to_pos",
        "from_lane_refs", "to_lane_refs", "lane_change_distance_m",
    )

    def __init__(self, element: ET.Element) -> None:
        self.id = element.get("no") or ""
        self.name = element.get("name") or ""
        from_el = element.find("fromLinkEndPt")
        to_el = element.find("toLinkEndPt")
        self.is_connector = from_el is not None or to_el is not None
        lane_elements = element.findall("./lanes/lane")
        self.lanes = len(lane_elements)
        self.points = [
            (_as_float(p.get("x")), _as_float(p.get("y")))
            for p in element.findall("./geometry/linkPolyPts/linkPolyPoint")
        ]
        self.length_m = _polyline_length(self.points)
        self.from_link = from_el.get("link") if from_el is not None else None
        self.to_link = to_el.get("link") if to_el is not None else None
        self.from_pos = _as_float(from_el.get("pos")) if from_el is not None else None
        self.to_pos = _as_float(to_el.get("pos")) if to_el is not None else None
        self.from_lane_refs = [
            ref.get("key") or ""
            for lane in lane_elements
            for ref in lane.findall("./fromLanes/objectRef")
        ]
        self.to_lane_refs = [
            ref.get("key") or ""
            for lane in lane_elements
            for ref in lane.findall("./toLanes/objectRef")
        ]
        self.lane_change_distance_m = _as_float_or_none(
            element.get(LANE_CHANGE_DISTANCE_ATTR)
        )


class _Facts:
    """Everything the nine checks read, parsed once, in one pass."""

    def __init__(
        self,
        root: ET.Element,
        topology: NetworkTopology | None,
        draft_metadata: dict[str, Any] | None,
        *,
        generator_marker: bool = False,
    ) -> None:
        self.root = root
        self.topology = topology
        self.offered_metadata = draft_metadata or {}

        self.links: dict[str, _LinkFacts] = {}
        self.connectors: dict[str, _LinkFacts] = {}
        container = root.find(".//links")
        for element in container.findall("link") if container is not None else []:
            facts = _LinkFacts(element)
            if not facts.id:
                continue
            (self.connectors if facts.is_connector else self.links)[facts.id] = facts

        self.out_edges: dict[str, list[str]] = defaultdict(list)
        self.in_edges: dict[str, list[str]] = defaultdict(list)
        for connector in self.connectors.values():
            if connector.from_link and connector.to_link:
                self.out_edges[connector.from_link].append(connector.to_link)
                self.in_edges[connector.to_link].append(connector.from_link)

        self.signal_heads = root.findall(".//signalHeads/signalHead")
        self.signal_controllers = root.findall(".//signalControllers/signalController")
        self.detectors = root.findall(".//detectors/detector")
        self.vehicle_inputs = root.findall(".//vehicleInputs/vehicleInput")
        self.compositions = root.findall(".//vehicleCompositions/vehicleComposition")
        self.routing_decisions = root.findall(
            ".//vehicleRoutingDecisionsStatic/vehicleRoutingDecisionStatic"
        )
        self.queue_counters = root.findall(".//queueCounters/queueCounter")
        self.travel_time_measurements = root.findall(
            ".//vehicleTravelTimeMeasurements/vehicleTravelTimeMeasurement"
        )
        self.data_collection_points = root.findall(
            ".//dataCollectionPoints/dataCollectionPoint"
        )
        self.conflict_areas = root.findall(".//conflictAreas/conflictArea")
        self.priority_rules = root.findall(".//priorityRules/priorityRule")
        self.nodes = root.findall(".//nodes/node")

        #: ``tag -> {no}`` for every element that declares one. V2's universe.
        #: The same pass records each element's PARENT tag, because a
        #: reference's referent is named by where it sits, not by what it is
        #: called (see the reference table's own comment).
        self.declared: dict[str, set[str]] = defaultdict(set)
        self.parent_tag: dict[ET.Element, str] = {}
        for element in root.iter():
            no = element.get("no")
            if no is not None:
                self.declared[element.tag].add(no)
            for child in element:
                self.parent_tag[child] = element.tag

        #: The signal groups each controller declares.
        self.controller_groups: dict[str, set[str]] = {}
        for controller in self.signal_controllers:
            self.controller_groups[controller.get("no") or ""] = {
                group.get("no") or ""
                for group in controller.findall("./sgs/signalGroup")
            }

        self.tags: set[str] = {element.tag for element in root.iter()}

        #: Who built this file, decided ONCE, on the artifact's own evidence.
        self.provenance = _decide_provenance(
            root,
            draft_metadata,
            network_links=set(self.links) | set(self.connectors),
            network_nodes={node.get("no") or "" for node in self.nodes},
            generator_marker=generator_marker,
        )
        #: The census, and ONLY when it was proven to describe this artifact. A
        #: sidecar about another build is not weak evidence about this one, so a
        #: clause that needs the census reads a gap rather than grading itself
        #: against ids that may no longer exist.
        self.draft_metadata: dict[str, Any] = (
            self.offered_metadata if self.provenance.census_is_admissible else {}
        )

    # -- derived helpers ----------------------------------------------------

    @property
    def grades_as_ours(self) -> bool:
        """Does a provenance-scoped clause REFUSE on this file?

        R1.5's coverage clauses are about **the phasing and the demand THIS
        generator imported** — "임포트된 phasing의 …". They are contradictions of
        the artifact's own provenance when we built it, and merely observations
        about somebody else's modelling choice when we did not: PTV's OWN 2026
        example library has 304 of 1,387 signal groups displayed by no head and
        60 zero-volume vehicle inputs, so neither can be a universal defect.

        The switch is :class:`Provenance`, and it is deliberately not a boolean:
        ``OURS`` and ``UNKNOWN`` both refuse, and only proven ``FOREIGN`` rides
        as a named warning. An unproven file graded leniently is a refuse-tier
        defect converted into a warning by the ABSENCE of evidence, and
        ``genesis_jobs.gate_and_publish`` publishes everything that is not
        REFUSED — so that conversion is the difference between a defect being
        stopped and it becoming the workspace's live dataset.
        """
        return self.provenance.grades_strictly

    def host(self, link_id: str | None) -> _LinkFacts | None:
        if not link_id:
            return None
        return self.links.get(link_id) or self.connectors.get(link_id)

    def sinks(self) -> list[str]:
        """Plain links no connector leaves — every place a vehicle departs."""
        return sorted(
            (lid for lid in self.links if not self.out_edges.get(lid)), key=_id_sort_key
        )

    def sources(self) -> list[str]:
        """Plain links no connector arrives on — every place a vehicle can start."""
        return sorted(
            (lid for lid in self.links if not self.in_edges.get(lid)), key=_id_sort_key
        )


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(
        ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        for a, b in zip(points, points[1:])
    )


def _label(facts: _Facts, link_id: str) -> str:
    """A link id plus the generator's own name for it, when there is one."""
    host = facts.host(link_id)
    return f"{link_id} ({host.name})" if host is not None and host.name else link_id


# ---------------------------------------------------------------------------
# V1 — load oracle (refuse)
# ---------------------------------------------------------------------------


def _check_v1(
    facts: _Facts, oracle: LoadOracle | None, digest: str, oracle_gap: str | None
) -> CheckResult:
    """Did a REAL Vissim open this file, and did it hold the network afterwards?

    The second half is the whole point. A network file stripped of the support
    containers its own links reference returns ``load_ok=True`` with
    ``Net.Links.Count == 0`` (measured 2026-08-06) — Vissim reports success for
    an empty load. So a load oracle that stops at "no exception" certifies the
    exact silent failure this gate exists to catch, and this one refuses to
    answer at all when the probe recorded no object census.
    """
    tier = CheckTier.REFUSE
    file_links = len(facts.links) + len(facts.connectors)
    evidence: dict[str, Any] = {
        "file_link_elements": file_links,
        "file_plain_links": len(facts.links),
        "file_connectors": len(facts.connectors),
        "file_nodes": len(facts.nodes),
    }

    if oracle is None:
        return CheckResult.build(
            "V1",
            tier,
            gaps=[
                GateGap(
                    code="v1_no_live_evidence",
                    context={"reason": oracle_gap or "no_load_oracle_supplied"},
                )
            ],
            evidence=evidence,
        )

    evidence.update(
        {
            "probe_label": oracle.label,
            "probe_network": Path(oracle.network_path).name,
            "vissim_version": oracle.vissim_version or "",
            "com_link_collection_count": oracle.com_link_count,
            "com_plain_links": oracle.com_plain_link_count,
            "com_connectors": oracle.com_connector_count,
            "com_nodes": oracle.com_node_count,
            "load_ok": oracle.load_ok,
            "save_ok": oracle.save_ok,
        }
    )

    # Identity first. Evidence about another file is not weak evidence.
    if oracle.network_sha256 is None:
        return CheckResult.build(
            "V1",
            tier,
            gaps=[
                GateGap(
                    code="v1_oracle_identity_unprovable",
                    context={"path": Path(oracle.network_path).name},
                )
            ],
            evidence=evidence,
        )
    evidence["oracle_sha256"] = oracle.network_sha256
    if oracle.network_sha256 != digest:
        return CheckResult.build(
            "V1",
            tier,
            gaps=[
                GateGap(
                    code="v1_oracle_names_another_network",
                    context={
                        "path": Path(oracle.network_path).name,
                        "oracle_sha256": oracle.network_sha256[:16],
                        "gated_sha256": digest[:16],
                    },
                )
            ],
            evidence=evidence,
        )

    findings: list[GateFinding] = []
    gaps: list[GateGap] = []

    if oracle.load_ok is not True:
        findings.append(
            GateFinding(
                code="v1_load_refused",
                subject=Path(oracle.network_path).name,
                context={"error": oracle.load_error or "(no error text recorded)"},
            )
        )
        # A refused load makes every downstream assertion meaningless; report the
        # refusal alone rather than a cascade of consequences.
        return CheckResult.build("V1", tier, findings=findings, evidence=evidence)

    if oracle.new_err_files:
        findings.append(
            GateFinding(
                code="v1_err_file_written",
                subject=", ".join(oracle.new_err_files),
                context={
                    "count": len(oracle.new_err_files),
                    "text": (oracle.err_text or "")[:400],
                },
            )
        )

    if oracle.com_link_count is None:
        gaps.append(GateGap(code="v1_no_object_census"))
    else:
        if oracle.com_link_count != file_links:
            findings.append(
                GateFinding(
                    code="v1_link_count_mismatch",
                    subject=Path(oracle.network_path).name,
                    context={"in_file": file_links, "in_vissim": oracle.com_link_count},
                )
            )
        if (
            oracle.com_plain_link_count is not None
            and oracle.com_plain_link_count != len(facts.links)
        ):
            findings.append(
                GateFinding(
                    code="v1_plain_link_count_mismatch",
                    subject=Path(oracle.network_path).name,
                    context={
                        "in_file": len(facts.links),
                        "in_vissim": oracle.com_plain_link_count,
                    },
                )
            )
        if (
            oracle.com_connector_count is not None
            and oracle.com_connector_count != len(facts.connectors)
        ):
            findings.append(
                GateFinding(
                    code="v1_connector_count_mismatch",
                    subject=Path(oracle.network_path).name,
                    context={
                        "in_file": len(facts.connectors),
                        "in_vissim": oracle.com_connector_count,
                    },
                )
            )
        if oracle.com_node_count is not None and oracle.com_node_count != len(facts.nodes):
            findings.append(
                GateFinding(
                    code="v1_node_count_mismatch",
                    subject=Path(oracle.network_path).name,
                    context={"in_file": len(facts.nodes), "in_vissim": oracle.com_node_count},
                )
            )

    if oracle.save_ok is None:
        gaps.append(GateGap(code="v1_no_save_round_trip"))
    elif oracle.save_ok is False:
        findings.append(
            GateFinding(
                code="v1_save_refused",
                subject=Path(oracle.network_path).name,
                context={"error": "SaveNetAs failed"},
            )
        )

    return CheckResult.build("V1", tier, findings=findings, gaps=gaps, evidence=evidence)


# ---------------------------------------------------------------------------
# V2 — reference integrity (refuse)
# ---------------------------------------------------------------------------


def _check_v2(facts: _Facts, topology: NetworkTopology | None) -> CheckResult:
    """Does every reference in the file point at something the file declares?

    The failure this exists for is not hypothetical and not loud: a
    ``drivingBehavior`` whose ``w99cc1Distr`` named an undeclared time
    distribution made Vissim drop the whole ``drivingBehaviors`` container,
    which made ``linkBehaviorType no="1"`` dangle, which made **every link in
    the network vanish** — with ``load_ok=True`` and no ``.err``
    (``schema.py``'s own comment records it). One unresolved id two hops away
    from the geometry deleted the geometry.

    Resolution is CONTEXT-AWARE: what a reference points at is decided by where
    it sits, never by the element's name alone. See the reference table's own
    comment for the measurement and for the 253 fabricated findings the
    name-only version produced on the first real consultant model.
    """
    tier = CheckTier.REFUSE
    findings: list[GateFinding] = []
    gaps: list[GateGap] = []
    checked = 0
    unmeasured_contexts: set[str] = set()

    unknown_tags = sorted(facts.tags - MEASURED_REFERENCE_SURFACE_TAGS)
    if unknown_tags:
        gaps.append(
            GateGap(
                code="v2_reference_table_not_measured_for_tag",
                context={"tags": unknown_tags[:20], "count": len(unknown_tags)},
            )
        )

    for element in facts.root.iter():
        tag = element.tag
        if not element.attrib:
            continue
        parent = facts.parent_tag.get(element, "")
        generic = tag in GENERIC_REFERENCE_TAGS
        for attribute, value in element.attrib.items():
            context = (parent, tag, attribute)
            simple_target = _SIMPLE_REFERENCES.get(context)
            pair_target = _PAIR_REFERENCES.get(context)
            if simple_target is None and pair_target is None:
                # A reference surface we HAVE measured, appearing somewhere we
                # have not — or a generic reference element, which is a
                # reference by construction. Either way it is named, not
                # guessed at. Anything else on an ordinary element stays quiet:
                # the probe's own output is the evidence that an ordinary
                # numeric attribute is far more often a parameter than a
                # reference, and naming every one of them would bury the short,
                # honest list under noise nobody can act on.
                if generic or (tag, attribute) in _REFERENCE_SURFACE:
                    unmeasured_contexts.add(f"{parent}/{tag}/@{attribute}")
                continue

            subject = f"{parent}/{tag}[{_self_id(element)}]/@{attribute}"
            checked += 1
            if simple_target is not None:
                spellings = (
                    (simple_target,) if isinstance(simple_target, str) else simple_target
                )
                if not any(value in facts.declared.get(t, ()) for t in spellings):
                    findings.append(
                        GateFinding(
                            code="v2_unresolved_reference",
                            subject=subject,
                            context={"value": value, "target": " | ".join(spellings)},
                        )
                    )
                continue

            owner_tag, member_tag = pair_target
            parts = value.split()
            if len(parts) != 2:
                findings.append(
                    GateFinding(
                        code="v2_malformed_pair_reference",
                        subject=subject,
                        context={"value": value, "expected": f"<{owner_tag} no> <{member_tag} no>"},
                    )
                )
                continue
            owner, member = parts
            if member_tag == "lane":
                host = facts.host(owner)
                if host is None:
                    findings.append(
                        GateFinding(
                            code="v2_unresolved_reference",
                            subject=subject,
                            context={"value": owner, "target": owner_tag},
                        )
                    )
                elif not member.isdigit() or not (1 <= int(member) <= host.lanes):
                    findings.append(
                        GateFinding(
                            code="v2_lane_index_out_of_range",
                            subject=subject,
                            context={"value": value, "link": owner, "lanes": host.lanes},
                        )
                    )
                continue
            groups = facts.controller_groups.get(owner)
            if groups is None:
                findings.append(
                    GateFinding(
                        code="v2_unresolved_reference",
                        subject=subject,
                        context={"value": owner, "target": owner_tag},
                    )
                )
            elif member not in groups:
                findings.append(
                    GateFinding(
                        code="v2_unresolved_reference",
                        subject=subject,
                        context={"value": value, "target": member_tag},
                    )
                )

    if unmeasured_contexts:
        named = sorted(unmeasured_contexts)
        gaps.append(
            GateGap(
                code="v2_reference_context_not_measured",
                context={"contexts": named[:20], "count": len(named)},
            )
        )

    # The connector-endpoint sweep ``genesis.diff`` already owns. Used, not
    # duplicated — the same argument NetworkTopology makes for using the
    # parser's own private node-membership helper.
    if topology is not None:
        for problem in _dangling_references(topology):
            findings.append(
                GateFinding(
                    code="v2_connector_endpoint_dangling",
                    subject=problem,
                    context={"source": "genesis.diff._dangling_references"},
                )
            )

    clauses = [
        _clause(
            "v2_references_resolve",
            findings=findings,
            gaps=[g for g in gaps if g.code == "v2_reference_context_not_measured"],
        ),
        _clause(
            "v2_surface_fully_measured",
            gaps=[g for g in gaps if g.code == "v2_reference_table_not_measured_for_tag"],
        ),
    ]
    evidence = {
        "references_checked": checked,
        "reference_contexts": len(_SIMPLE_REFERENCES) + len(_PAIR_REFERENCES),
        "reference_kinds_not_resolved": [
            f"{name} — {reason}" for name, reason in sorted(_UNRESOLVED_REFERENCES.items())
        ],
        "declared_object_tags": len(facts.declared),
        "unmeasured_reference_contexts": len(unmeasured_contexts),
    }
    return CheckResult.build(
        "V2", tier, findings=findings, gaps=gaps, clauses=clauses, evidence=evidence
    )


def _self_id(element: ET.Element) -> str:
    return element.get("no") or element.get("key") or element.get("link") or "?"


# ---------------------------------------------------------------------------
# V3 — connectivity (refuse)
# ---------------------------------------------------------------------------


def _check_v3(facts: _Facts) -> CheckResult:
    """Can traffic get in, through, and out — and does it leave only where it
    is meant to?

    Four questions, three of which the ``.inpx`` answers on its own:

    1. **Orphan links** — no connector arrives, none leaves. Nothing can reach
       them and nothing leaves them.
    2. **Unreachable zones** — links no path from any network entry reaches. A
       pocket of streets connected only to each other is invisible in a
       rendering and simulates nothing.
    3. **Trapped zones** — links from which no network exit is reachable at all.
       A vehicle that gets in circulates until the run ends.
    4. **Silent departure points** — a link end no connector leaves, where that
       end is not the model boundary. The ``.inpx`` cannot tell the two apart;
       the generator can, and did (``metadata['connectivity']``), so this half
       runs on the draft's ``*.genesis.json`` and is a named gap without it.

    Written as reachability rather than as a count, because a count is what the
    connectivity unit already publishes and a count is not the question R1.5
    asks. The measurement that made the difference: on the current Murdoch
    generation the dangling-end census reads 21 and the reachability analysis
    finds **150 links in 8 fully isolated components** — a fault no census of
    link ends can see, because every one of those links has both a connector in
    and a connector out.
    """
    tier = CheckTier.REFUSE
    findings: list[GateFinding] = []
    gaps: list[GateGap] = []

    sinks = facts.sinks()
    sources = facts.sources()

    orphans = [
        lid
        for lid in facts.links
        if not facts.out_edges.get(lid) and not facts.in_edges.get(lid)
    ]
    for lid in sorted(orphans, key=_id_sort_key):
        findings.append(
            GateFinding(code="v3_orphan_link", subject=_label(facts, lid), context={})
        )

    orphan_set = set(orphans)
    reachable_forward = _reach(sources, facts.out_edges)
    reaches_exit = _reach(sinks, facts.in_edges)
    unreachable = {
        lid
        for lid in facts.links
        if lid not in reachable_forward and lid not in orphan_set
    }
    trapped = {
        lid for lid in facts.links if lid not in reaches_exit and lid not in orphan_set
    }

    # Reported per COMPONENT, and each component ONCE. A pocket of streets that
    # is both unreachable and trapped is one fault, not two; splitting it across
    # two lists would double every count a reader takes away.
    broken = sorted(unreachable | trapped, key=_id_sort_key)
    for component in _components(broken, facts):
        members = set(component)
        if members <= unreachable and members <= trapped:
            code = "v3_isolated_zone"
        elif members <= unreachable:
            code = "v3_unreachable_zone"
        elif members <= trapped:
            code = "v3_trapped_zone"
        else:
            code = "v3_broken_zone"
        findings.append(
            GateFinding(
                code=code,
                subject=_label(facts, component[0]),
                context={"links": len(component), "members": component[:20]},
            )
        )

    # -- vehicle-input reachability ----------------------------------------
    input_links = sorted(
        {el.get("link") or "" for el in facts.vehicle_inputs} - {""}, key=_id_sort_key
    )
    if not facts.vehicle_inputs:
        gaps.append(GateGap(code="v3_no_vehicle_inputs"))
    else:
        for link_id in input_links:
            if link_id not in facts.links:
                continue  # V2 owns the dangling reference itself
            if link_id not in reaches_exit:
                findings.append(
                    GateFinding(
                        code="v3_input_cannot_reach_an_exit",
                        subject=_label(facts, link_id),
                        context={},
                    )
                )

    input_findings = [f for f in findings if f.code == "v3_input_cannot_reach_an_exit"]
    reachability_findings = [f for f in findings if f.code != "v3_input_cannot_reach_an_exit"]
    input_gaps = [g for g in gaps if g.code == "v3_no_vehicle_inputs"]

    # -- silent departure points -------------------------------------------
    departure_findings: list[GateFinding] = []
    departure_gaps: list[GateGap] = []
    connectivity = facts.draft_metadata.get("metadata", {}).get("connectivity")
    if not isinstance(connectivity, dict):
        # A census that does not describe THIS artifact is named as such rather
        # than folded into "no census": grading a departure list against link
        # ids from another build is the failure V1's sha256 gate exists to stop,
        # and the operator's repair is different in each case.
        if facts.offered_metadata and not facts.provenance.census_is_admissible:
            census_gap = GateGap(
                code="v3_census_is_not_about_this_network",
                context={
                    "reason": facts.provenance.census,
                    "census_elements": facts.provenance.census_elements,
                    "network_elements": facts.provenance.network_elements,
                },
            )
        else:
            census_gap = GateGap(code="v3_no_generator_census")
        gaps.append(census_gap)
        departure_gaps.append(census_gap)
    else:
        census = connectivity.get("dangling_approaches") or []
        for entry in sorted(census, key=lambda e: _id_sort_key(str(e.get("link_id", "")))):
            cause = str(entry.get("cause") or "")
            if cause in BENIGN_DANGLING_CAUSES:
                continue
            departure_findings.append(
                GateFinding(
                    code="v3_silent_departure_point",
                    subject=str(entry.get("link") or entry.get("link_id") or "?"),
                    context={"cause": cause, "link_id": entry.get("link_id")},
                )
            )
    findings.extend(departure_findings)

    # The reachability half of V3 runs on ANY .inpx, so on a file with no
    # generator census it still EARNS its answer — reported as a passing clause
    # rather than swallowed by the grey box the census gap puts around the
    # check. The check itself is still NOT_CHECKABLE: a clause that could not
    # run never becomes a pass, only a visible one.
    clauses = [
        _clause("v3_reachability", findings=reachability_findings),
        _clause("v3_inputs_reach_an_exit", findings=input_findings, gaps=input_gaps),
        _clause("v3_departures_are_at_the_boundary", findings=departure_findings, gaps=departure_gaps),
    ]

    evidence = {
        "plain_links": len(facts.links),
        "connectors": len(facts.connectors),
        "entry_links": len(sources),
        "exit_links": len(sinks),
        "orphan_links": len(orphans),
        "links_unreachable_from_any_entry": len(unreachable),
        "links_that_reach_no_exit": len(trapped),
        "links_in_a_broken_zone": len(broken),
        "vehicle_input_links": len(input_links),
    }
    return CheckResult.build(
        "V3", tier, findings=findings, gaps=gaps, clauses=clauses, evidence=evidence
    )


def _reach(seeds: Iterable[str], edges: dict[str, list[str]]) -> set[str]:
    seen = set(seeds)
    queue = deque(seen)
    while queue:
        current = queue.popleft()
        for nxt in edges.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _components(members: list[str], facts: _Facts) -> list[list[str]]:
    """Weakly-connected components of ``members``, each sorted, all sorted."""
    pool = set(members)
    seen: set[str] = set()
    out: list[list[str]] = []
    for start in members:
        if start in seen:
            continue
        stack = [start]
        component: list[str] = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            for neighbour in facts.out_edges.get(current, ()):
                if neighbour in pool and neighbour not in seen:
                    stack.append(neighbour)
            for neighbour in facts.in_edges.get(current, ()):
                if neighbour in pool and neighbour not in seen:
                    stack.append(neighbour)
        out.append(sorted(component, key=_id_sort_key))
    return sorted(out, key=lambda c: (-len(c), _id_sort_key(c[0])))


# ---------------------------------------------------------------------------
# V4 — lane continuity (refuse)
# ---------------------------------------------------------------------------


def _check_v4(facts: _Facts, settings: GateSettings) -> CheckResult:
    """Do the connectors map lanes that exist, and does a dropped lane give the
    vehicle in it room to get out?

    Clause 1 is exact: every ``<fromLanes>/<toLanes>`` reference names a link
    the file declares and a lane index within that link's lane count, and a
    connector never maps MORE lanes on one side than that side's link has.

    That last clause used to read "a connector is never wider than either link
    it joins", and that is false of real Vissim. A connector's lane count is the
    WIDER of its two ends when a road gains lanes across a junction: the extra
    connector lanes simply carry no ``<fromLanes>`` (they begin at the
    connector) or no ``<toLanes>`` (they end there). PTV's own Vissim 2026
    example library settles it — 50 connectors across 17 networks are wider than
    one of their links, in files named ``Inside Merge 2+2 to 3 Lanes`` and
    ``Ghost Island Merge 2+3 to 4 Lanes, Diverge 4 to 2+3 Lanes``, which is the
    modelling intent stated in the filename. On the first real consultant model
    the old clause manufactured 33 findings, every one of them a lane gain.
    What IS universal, and what this clause now asks, is that a side never maps
    more lanes than exist there: measured 0 violations across all 8,381
    connectors in that same PTV 2026 library, across this generator's own output
    and across the consultant model.

    Clause 2 needs a distance, and the distance is **the file's own**: each link
    declares ``lnChgDist`` — how much road Vissim gives a vehicle to change
    lanes before it needs to. A lane no connector leaves is a lane whose traffic
    MUST change lanes (in Vissim it does not merge, it waits, and after
    ``DiffusTm`` it is deleted and the demand vanishes with no error anywhere).
    So the question is whether the longest path of road leading into that drop
    is at least as long as the network says a lane change needs. Nothing here is
    chosen: the runway requirement is read from the link, and the runway
    available is measured along the connector graph.
    """
    tier = CheckTier.REFUSE
    findings: list[GateFinding] = []
    gaps: list[GateGap] = []

    for connector in sorted(facts.connectors.values(), key=lambda c: _id_sort_key(c.id)):
        for side, refs, host_id in (
            ("from", connector.from_lane_refs, connector.from_link),
            ("to", connector.to_lane_refs, connector.to_link),
        ):
            host = facts.host(host_id)
            if host is None:
                continue  # V2 owns the missing link itself
            if len(refs) > host.lanes:
                findings.append(
                    GateFinding(
                        code="v4_more_mapped_lanes_than_link_has",
                        subject=connector.id,
                        context={
                            "side": side,
                            "mapped_lanes": len(refs),
                            "link": host_id or "",
                            "link_lanes": host.lanes,
                        },
                    )
                )
            for ref in refs:
                parts = ref.split()
                if len(parts) != 2 or not parts[1].isdigit():
                    findings.append(
                        GateFinding(
                            code="v4_lane_reference_malformed",
                            subject=connector.id,
                            context={"side": side, "value": ref},
                        )
                    )
                    continue
                target = facts.host(parts[0])
                if target is None:
                    continue  # V2 owns it
                index = int(parts[1])
                if not 1 <= index <= target.lanes:
                    findings.append(
                        GateFinding(
                            code="v4_lane_index_outside_link",
                            subject=connector.id,
                            context={
                                "side": side,
                                "link": parts[0],
                                "lane": index,
                                "link_lanes": target.lanes,
                            },
                        )
                    )

    # -- lane drops ---------------------------------------------------------
    served: dict[str, set[int]] = defaultdict(set)
    leaving: dict[str, list[_LinkFacts]] = defaultdict(list)
    for connector in facts.connectors.values():
        if not connector.from_link:
            continue
        leaving[connector.from_link].append(connector)
        for ref in connector.from_lane_refs:
            parts = ref.split()
            if len(parts) == 2 and parts[0] == connector.from_link and parts[1].isdigit():
                served[connector.from_link].add(int(parts[1]))

    drops = 0
    budget_exhausted = 0
    for link_id in sorted(facts.links, key=_id_sort_key):
        link = facts.links[link_id]
        if not leaving.get(link_id):
            continue  # a network exit; V3's question, not V4's
        missing = sorted(set(range(1, link.lanes + 1)) - served[link_id])
        if not missing:
            continue
        drops += 1
        required = link.lane_change_distance_m
        if required is None:
            gaps.append(
                GateGap(code="v4_link_declares_no_lane_change_distance", context={"link": link_id})
            )
            continue
        available, exhausted = _upstream_runway(
            facts, link_id, required, settings.lane_change_search_budget
        )
        if exhausted:
            budget_exhausted += 1
            gaps.append(
                GateGap(
                    code="v4_runway_search_budget_exhausted",
                    context={"link": link_id, "budget": settings.lane_change_search_budget},
                )
            )
            continue
        if available + 1e-9 < required:
            findings.append(
                GateFinding(
                    code="v4_lane_drop_without_runway",
                    subject=_label(facts, link_id),
                    context={
                        "lanes": link.lanes,
                        "dropped_lanes": missing,
                        "required_m": round(required, 2),
                        "available_m": round(available, 2),
                    },
                )
            )

    evidence = {
        "connectors": len(facts.connectors),
        "lane_references": sum(
            len(c.from_lane_refs) + len(c.to_lane_refs) for c in facts.connectors.values()
        ),
        "lane_drops": drops,
        "runway_searches_exhausted": budget_exhausted,
    }
    return CheckResult.build("V4", tier, findings=findings, gaps=gaps, evidence=evidence)


def _upstream_runway(
    facts: _Facts, link_id: str, cap_m: float, budget: int
) -> tuple[float, bool]:
    """Longest simple path of road leading into ``link_id``, capped at ``cap_m``.

    Capped because the answer is only ever compared against the cap, and
    uncapped it would be a longest-path search. Simple paths only: a vehicle
    circling a roundabout twice is not lane-change runway, and allowing revisits
    would let any cycle manufacture an arbitrary distance.

    Returns ``(distance, budget_exhausted)``. An exhausted budget is reported as
    exhausted, never as the best distance found — a lower bound compared against
    a threshold would silently invent findings.
    """
    start = facts.links[link_id]
    if start.length_m >= cap_m:
        return start.length_m, False
    best = start.length_m
    stack: list[tuple[str, float, frozenset[str]]] = [
        (link_id, start.length_m, frozenset({link_id}))
    ]
    expansions = 0
    while stack:
        current, distance, path = stack.pop()
        expansions += 1
        if expansions > budget:
            return best, True
        for previous in facts.in_edges.get(current, ()):
            if previous in path:
                continue
            host = facts.host(previous)
            if host is None:
                continue
            extended = distance + host.length_m
            if extended >= cap_m:
                return cap_m, False
            if extended > best:
                best = extended
            stack.append((previous, extended, path | {previous}))
    return best, False


# ---------------------------------------------------------------------------
# V5 — signal/geometry consistency (refuse)
# ---------------------------------------------------------------------------


def _check_v5(facts: _Facts) -> CheckResult:
    """Every signalised approach has a head, every group is on a head, and no
    head hangs off a controller that does not exist.

    A network with **no** signal layer at all is not a pass here. It is either
    genuinely unsignalised or missing its signals, and the file cannot say
    which — so it is NOT_CHECKABLE with that stated, which keeps the gate from
    going green on a network whose signals were never imported. That is the
    vacuous-pass trap this module exists to refuse: "every signalised approach
    has a head" is trivially true of zero approaches.

    TWO OF THE THREE CLAUSES ARE ABOUT PHASING **WE** IMPORTED
    ----------------------------------------------------------
    R1.5 scopes V5 to "임포트된 phasing의 …". Head→controller, head→group and
    head→lane integrity are universal — a head naming a controller the file does
    not declare is broken in anybody's model, and those stay refuse-tier for
    every file. The coverage clauses are not:

    * **A signal group no head displays.** Measured across PTV's own Vissim 2026
      example library: **304 of 1,387 signal groups** (22 %, in 36 of 126
      signalised networks) are displayed by no head, and 45 of 292 controllers
      have no head at all. Pedestrian phases in a network without pedestrians,
      spare stages, imported controller rosters — all normal.
    * **A signalised approach with no head.** "Approach" is derived from node
      membership, and node footprints are drawn by whoever built the model. On
      the first real consultant model, 8 of the 12 links this derivation calls
      approaches of one node are *interior* links, and the stop lines sit on
      those interior links rather than on the entering ones. The clause is
      reformulated below to the question that IS portable — can traffic entering
      a signalised node reach a head before it leaves — see
      :func:`_node_entry_approaches` for what that costs and what it must not
      buy. The findings that survive it are partially signalised junctions (a
      bus gate, a signalised crossing on one arm) in PTV's own library and in
      the consultant model, which no ``.inpx`` can distinguish from a missing
      head.

    So both are run against every file and reported in full, and their SEVERITY
    follows provenance: a refusal when this generator built the network (the
    artifact contradicts the import it claims) OR when nothing proves who did,
    and a named warning only on a file PROVEN to be somebody else's. See
    :attr:`_Facts.grades_as_ours` and :class:`Provenance`.
    """
    tier = CheckTier.REFUSE
    findings: list[GateFinding] = []
    warnings: list[GateFinding] = []
    provenance_findings: list[GateFinding] = []
    gaps: list[GateGap] = []

    if not facts.signal_controllers and not facts.signal_heads:
        return CheckResult.build(
            "V5",
            tier,
            gaps=[GateGap(code="v5_no_signal_layer")],
            evidence={"signal_controllers": 0, "signal_heads": 0},
        )

    heads_by_link: dict[str, int] = defaultdict(int)
    groups_with_head: set[tuple[str, str]] = set()
    for head in facts.signal_heads:
        head_no = head.get("no") or "?"
        lane_ref = (head.get("lane") or "").split()
        if len(lane_ref) == 2:
            heads_by_link[lane_ref[0]] += 1
        sg_ref = (head.get("sg") or "").split()
        if len(sg_ref) != 2:
            findings.append(
                GateFinding(
                    code="v5_head_signal_group_malformed",
                    subject=head_no,
                    context={"value": head.get("sg") or ""},
                )
            )
            continue
        controller, group = sg_ref
        declared = facts.controller_groups.get(controller)
        if declared is None:
            findings.append(
                GateFinding(
                    code="v5_head_without_controller",
                    subject=head_no,
                    context={"controller": controller},
                )
            )
            continue
        if group not in declared:
            findings.append(
                GateFinding(
                    code="v5_head_without_signal_group",
                    subject=head_no,
                    context={"controller": controller, "group": group},
                )
            )
            continue
        groups_with_head.add((controller, group))

    for controller in sorted(facts.controller_groups, key=_id_sort_key):
        for group in sorted(facts.controller_groups[controller], key=_id_sort_key):
            if (controller, group) not in groups_with_head:
                provenance_findings.append(
                    GateFinding(
                        code="v5_signal_group_without_head",
                        subject=f"{controller} {group}",
                        context={"controller": controller, "group": group},
                    )
                )

    # -- traffic entering a signalised node meets a head inside it -----------
    if facts.topology is None or not facts.topology.node_members:
        gaps.append(GateGap(code="v5_no_node_membership"))
    else:
        signalised_nodes = sorted(
            {
                node_id
                for node_id, members in facts.topology.node_members.items()
                if any(
                    member in heads_by_link
                    or (
                        member in facts.connectors
                        and facts.connectors[member].from_link in heads_by_link
                    )
                    for member in members
                )
            },
            key=_id_sort_key,
        )
        if not signalised_nodes:
            gaps.append(GateGap(code="v5_heads_belong_to_no_node"))
        for node_id in signalised_nodes:
            for approach in _node_entry_approaches(facts, node_id, heads_by_link):
                if not _reaches_a_head_inside_node(facts, node_id, approach, heads_by_link):
                    provenance_findings.append(
                        GateFinding(
                            code="v5_approach_reaches_no_head_in_node",
                            subject=_label(facts, approach),
                            context={"node": node_id},
                        )
                    )

    head_findings = list(findings)
    strict = facts.grades_as_ours
    if strict:
        findings.extend(provenance_findings)
    else:
        warnings.extend(provenance_findings)

    # ``v5_no_node_membership`` / ``v5_heads_belong_to_no_node`` are what the
    # COVERAGE clause needed and did not get; head integrity ran regardless.
    # The "not our import" gap goes into the CHECK's gaps too, not the clause's
    # alone: a check that could not certify its own coverage question is
    # NOT_CHECKABLE, and a reader of the verdict is owed the reason by name.
    if not strict:
        gaps.append(
            GateGap(
                code="v5_coverage_is_not_our_import",
                context={"provenance": facts.provenance.state.value},
            )
        )
    clauses = [
        _clause("v5_heads_resolve", findings=head_findings),
        _clause(
            "v5_coverage_of_the_imported_phasing",
            findings=provenance_findings if strict else (),
            gaps=gaps,
        ),
    ]
    evidence = {
        "signal_controllers": len(facts.signal_controllers),
        "signal_heads": len(facts.signal_heads),
        "signal_groups": sum(len(v) for v in facts.controller_groups.values()),
        "links_with_a_head": len(heads_by_link),
        "detectors": len(facts.detectors),
        # The tier this check applied, IN the check, so "9 passed" can never
        # quietly mean "and two refusals were downgraded".
        "provenance": facts.provenance.state.value,
        "coverage_tier": "refuse" if strict else "warn",
    }
    return CheckResult.build(
        "V5",
        tier,
        findings=findings,
        warnings=warnings,
        gaps=gaps,
        clauses=clauses,
        evidence=evidence,
    )


def _node_members(facts: _Facts, node_id: str) -> set[str]:
    return facts.topology.node_members.get(node_id, set()) if facts.topology else set()


def _node_entry_approaches(
    facts: _Facts, node_id: str, heads_by_link: dict[str, int]
) -> list[str]:
    """Every plain link traffic enters ``node_id`` on **without having passed a
    stop line to get there**, in id order.

    Derived from the node's own connector membership, never from proximity: a
    connector belonging to the node leaves the link it belongs to. A link that
    is itself fed by a connector of the SAME node is *interior* — a leg inside
    the junction rather than an arm arriving at it — and a modeller who meshes
    the junction interior would otherwise have every internal leg counted as an
    unsignalised approach (35 of the 47 links the old, membership-only
    derivation called approaches on the first real consultant model are
    interior).

    THE EXCLUSION IS CONDITIONAL, AND THE CONDITION IS THE WHOLE POINT
    ------------------------------------------------------------------
    An interior leg is only innocent because traffic on it **already met a head
    upstream, inside this node**. Excluding every interior link unconditionally
    — as this did until 2026-08-11 — deletes the branch from the check before
    the reachability walk ever sees it, so a leg that meets NO head on any path,
    forward or back, disappears with no finding and no warning. Measured:

    ==================================== ===== ==========
    rule                                 PTV   consultant
    ==================================== ===== ==========
    membership only, head on the arm      195         30
    unconditional interior exclusion        6          5
    no exclusion at all                    30         19
    **exclusion only where a head is
    upstream (this rule)**                 15         10
    ==================================== ===== ==========

    Dropping the exclusion entirely would indeed catch the hidden cases, but it
    re-manufactures the false positives it was built for: of the 24 extra hits
    on PTV's 2026 library, **15 have a head upstream inside the node** (traffic
    there passed the stop line already) and only 9 are real. Asking the upstream
    question keeps all 9 — e.g. PTV's own ``Mainz - Static Routes``, where link 8
    feeds both a signalised movement and link 68, and everything through 68 and
    out crosses node 22 without ever meeting a head — and reinstates none of the
    15. On the consultant model the same rule recovers 5 real cases the
    unconditional exclusion hid.

    ``heads_by_link`` is a parameter rather than a lookup so this function stays
    a pure statement about one node.
    """
    members = _node_members(facts, node_id)
    connectors = [m for m in members if m in facts.connectors]
    approaches = {
        facts.connectors[member].from_link
        for member in connectors
        if facts.connectors[member].from_link in facts.links
    }
    interior = {
        facts.connectors[member].to_link
        for member in connectors
        if facts.connectors[member].to_link in facts.links
    }
    excluded = {
        link_id
        for link_id in approaches & interior
        if _passed_a_head_inside_node(facts, node_id, link_id, heads_by_link)
    }
    return sorted((a for a in approaches - excluded if a), key=_id_sort_key)


def _passed_a_head_inside_node(
    facts: _Facts, node_id: str, link_id: str, heads_by_link: dict[str, int]
) -> bool:
    """Did traffic on ``link_id`` meet a head on its way INTO it, inside the node?

    The mirror of :func:`_reaches_a_head_inside_node`, walking the node's own
    connectors backwards. It answers the only question that makes an interior
    leg innocent, and it answers it about the leg rather than about the shape of
    the junction.
    """
    members = _node_members(facts, node_id)
    connectors = [m for m in members if m in facts.connectors]
    seen = {link_id}
    queue = deque([link_id])
    while queue:
        current = queue.popleft()
        for member in connectors:
            connector = facts.connectors[member]
            if connector.to_link != current:
                continue
            for previous in (member, connector.from_link):
                if not previous or previous in seen:
                    continue
                if heads_by_link.get(previous, 0) > 0:
                    return True
                seen.add(previous)
                queue.append(previous)
    return False


def _reaches_a_head_inside_node(
    facts: _Facts, node_id: str, approach: str, heads_by_link: dict[str, int]
) -> bool:
    """Can traffic entering on ``approach`` meet a signal head before it leaves
    ``node_id``?

    The portable form of "every signalised approach has a head". Where a stop
    line sits — on the arm itself, or on a short interior leg one connector in —
    is a modelling convention, and both conventions occur in real files; where
    it does NOT sit is the defect. The walk stays inside the node's own
    membership, so a head one junction downstream never launders an arm that has
    none.
    """
    if heads_by_link.get(approach, 0) > 0:
        return True
    members = _node_members(facts, node_id)
    connectors = [m for m in members if m in facts.connectors]
    seen = {approach}
    queue = deque([approach])
    while queue:
        current = queue.popleft()
        for member in connectors:
            connector = facts.connectors[member]
            if connector.from_link != current:
                continue
            for nxt in (member, connector.to_link):
                if not nxt or nxt in seen:
                    continue
                if nxt not in members:
                    continue
                if heads_by_link.get(nxt, 0) > 0:
                    return True
                seen.add(nxt)
                queue.append(nxt)
    return False


# ---------------------------------------------------------------------------
# V6 — demand coverage (refuse on structure, warn on capacity)
# ---------------------------------------------------------------------------

#: ``(parent tag, element tag, attribute)`` sites that USE a vehicle
#: composition. A composition named at any of them is in service, whatever the
#: vehicle inputs say. Measured 2026-08-12 by
#: ``backend/tools/probe_orphaned_compositions.py`` over the A5 corpus of 825
#: real ``.inpx`` (PTV Examples Demo/Training 2024 + 2026, this repository's
#: live-check networks, ``data/``), cross-checked against
#: ``probe_reference_contexts.py --min-distinct 1``, and REGISTERED BY HAND like
#: every other reference in this module — a value-only sweep cannot decide this,
#: because composition ids are small integers and collide with ~297,000 values
#: across 454 unrelated sites in that same corpus.
#:
#: Counts are values that collide with a composition id the same file declares,
#: over the files carrying them.
#:
#: Deliberately NOT here, each with the measurement that rejected it:
#: ``ptLineStop/@alightComp`` (146 / 18) and ``partialPTLineStop/@alightComp``
#: (88 / 16) resolve to **pedestrian**Composition — alighting passengers are
#: pedestrians, and "Comp" is the whole of the resemblance;
#: ``timeIntervalPedVolume/@pedComp`` (330 / 47) is that same pedestrian id
#: space; ``signalHead/@complRate`` (2,848 / 123) is a compliance RATE;
#: ``@attributeComputation`` (45 across three sites) is an
#: attribute-computation id. Admitting any of them would silence real orphans.
_COMPOSITION_CONSUMERS: frozenset[tuple[str, str, str]] = frozenset(
    {
        # The demand layer itself — also ``_SIMPLE_REFERENCES``' one registered
        # composition reference (557 values over 300 files).
        ("timeIntVehVols", "timeIntervalVehVolume", "vehComp"),
        # 131 / 38; the reference probe resolves it unanimously to
        # ``vehicleComposition``.
        ("parkingLots", "parkingLot", "initOccupComp"),
        # 42 / 28; a dynamic assignment's demand names the composition it
        # releases.
        ("dynAssignDemands", "dynAssignDemand", "vehComp"),
    }
)

#: Composition-NAMED sites the corpus contains that are not consumers, kept
#: explicitly so that a spelling PTV adds in a future schema is NAMED as
#: unmeasured (``v6_composition_consumer_not_measured``) instead of being
#: silently treated as "nothing uses this composition". The A5 rule: an
#: unmeasured context is named, never guessed.
_NOT_COMPOSITION_CONSUMERS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("signalHeads", "signalHead", "complRate"),
        ("timeIntPedVols", "timeIntervalPedVolume", "pedComp"),
        ("lineStops", "ptLineStop", "alightComp"),
        ("partLineStops", "partialPTLineStop", "alightComp"),
        ("vehicleAttributeDecisions", "vehicleAttributeDecision", "attributeComputation"),
        (
            "pedestrianAttributeDecisions",
            "pedestrianAttributeDecision",
            "attributeComputation",
        ),
        ("attributeModifications", "attributeModification", "attributeComputation"),
    }
)

#: Gap codes V6 raises that are NOT about its structural half, so the structure
#: clause does not inherit them.
_V6_NON_STRUCTURE_GAPS: frozenset[str] = frozenset(
    {"v6_zero_volume_is_not_our_import", "v6_composition_consumer_not_measured"}
)


def _composition_use(
    facts: _Facts, declared: set[str]
) -> tuple[set[str], list[str]]:
    """Which declared compositions does something in this file USE, and did any
    site claim to name a composition in a spelling we have never measured?

    Both halves in one pass over the document. A value counts as a use only at
    a REGISTERED consumer site; a composition-named attribute that is neither
    registered nor registered-as-rejected is returned as an unmeasured site, and
    the caller turns it into a gap rather than concluding "unused" from a
    surface it has not measured.
    """
    used: set[str] = set()
    unmeasured: set[tuple[str, str, str]] = set()
    if not declared:
        return used, []
    for element in facts.root.iter():
        if element.tag == "vehicleComposition":
            continue  # its own declaration, not a use
        parent = facts.parent_tag.get(element, "")
        for attribute, value in element.attrib.items():
            if attribute == "no":
                continue
            token = (value or "").strip()
            if not token or token not in declared:
                continue
            site = (parent, element.tag, attribute)
            if site in _COMPOSITION_CONSUMERS:
                used.add(token)
            elif "comp" in attribute.lower() and site not in _NOT_COMPOSITION_CONSUMERS:
                unmeasured.add(site)
    return used, sorted(f"{p}/{tag}/@{attr}" for p, tag, attr in unmeasured)


def _check_v6(facts: _Facts) -> CheckResult:
    """Does every vehicle input carry a volume, a composition that adds up, and
    a routing decision before the first place the road branches?

    The routing half is the one worth the code. Vissim does not refuse traffic
    that arrives at a branch with no routing decision — it distributes it, and
    the split it invents is reported with the same confidence as an observed
    one. So the check walks forward from each input until it meets either a
    routing decision (covered) or a link with more than one exit (not).

    Capacity is the warn half and reuses the edit-time constraint's own
    number: ``latent_demand_overflow`` compares a link's total demand against
    ``lanes x 1900 veh/h``, and excess queues invisibly as latent demand rather
    than being refused. One constant, imported, so the gate and the guardrail
    cannot disagree about the same link.

    A ``volume`` is a RATE, in veh/h, that applies DURING one time interval, so
    the demand on a link is the largest of its per-interval totals — never the
    sum across intervals. This generator emits one interval per input, which
    made sum and max the same number and hid the difference until a real
    consultant model with six 15-minute intervals reported 11,893 veh/h on a
    three-lane road: six intervals of a perfectly ordinary ~1,980 veh/h, added
    up. Six of that model's warn-tier capacity findings were that arithmetic and
    nothing else.

    A composition NOTHING uses is the second warn observation, and it exists
    because the "no demand at all" gap above is GLOBAL: a multi-site model whose
    second site lost its whole demand still has the first site's inputs, V6
    passes, and the orphaned ``<vehicleComposition>`` is the only trace left in
    the file. It is reported and never refused, because the corpus says so —
    measured 2026-08-12 over the A5 corpus of 825 real ``.inpx``
    (``backend/tools/probe_orphaned_compositions.py``): of the 300 files where
    the pattern can appear at all (>= 1 composition AND >= 1 input), **39
    (13.0 %) declare a composition no vehicle input names**, and 32 (10.7 %)
    declare one nothing in the file names — 7 of 139 applicable PTV 2024 library
    files, 19 of 153 PTV 2026, and the consultant's own calibrated Murdoch model
    with 3 of its 6 compositions. PTV ships genuine orphans in its flagship
    examples (``Karlsruhe 3D``, ``Toll plaza``, ``BRT Priority Texas``).
    ``Mainz - Static Routes`` carries only the LOOSER shape — compositions no
    vehicle input names but ``dynAssignDemand/@vehComp`` does — which the
    consumer registry resolves as used, so this clause correctly does NOT warn
    on it (re-verified by hand 2026-08-12; it belongs to the 39, not the 32).
    Keeping a template composition around is an ordinary modelling idiom, so
    refusing on it would fabricate a defect on one real file in ten — the A5
    failure this gate exists not to repeat.
    """
    tier = CheckTier.REFUSE
    findings: list[GateFinding] = []
    provenance_findings: list[GateFinding] = []
    gaps: list[GateGap] = []

    if not facts.vehicle_inputs:
        return CheckResult.build(
            "V6",
            tier,
            gaps=[GateGap(code="v6_no_demand_layer")],
            evidence={"vehicle_inputs": 0, "compositions": 0, "routing_decisions": 0},
        )

    composition_flows: dict[str, list[ET.Element]] = {}
    for composition in facts.compositions:
        composition_flows[composition.get("no") or ""] = composition.findall(
            "./vehCompRelFlows/vehicleCompositionRelativeFlow"
        )
    for comp_no in sorted(composition_flows, key=_id_sort_key):
        flows = composition_flows[comp_no]
        if not flows:
            findings.append(
                GateFinding(code="v6_composition_empty", subject=comp_no, context={})
            )
            continue
        total = sum(_as_float(f.get("relFlow")) for f in flows)
        if total <= 0:
            findings.append(
                GateFinding(
                    code="v6_composition_relflow_sum_zero",
                    subject=comp_no,
                    context={"flows": len(flows)},
                )
            )
        for flow in flows:
            if _as_float(flow.get("relFlow")) < 0:
                findings.append(
                    GateFinding(
                        code="v6_composition_relflow_negative",
                        subject=comp_no,
                        context={"veh_type": flow.get("vehType") or "?"},
                    )
                )

    decision_links = {
        el.get("link") or "" for el in facts.routing_decisions
    } - {""}
    #: ``link -> {time interval token -> concurrent veh/h}``. Keyed by the
    #: interval so the comparison against capacity is rate against rate.
    volume_by_link_interval: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )

    for element in sorted(facts.vehicle_inputs, key=lambda e: _id_sort_key(e.get("no") or "")):
        input_no = element.get("no") or "?"
        link_id = element.get("link") or ""
        volumes = element.findall("./timeIntVehVols/timeIntervalVehVolume")
        if not volumes:
            findings.append(
                GateFinding(code="v6_input_without_volume", subject=input_no, context={})
            )
        positive = [v for v in volumes if _as_float(v.get("volume")) > 0]
        if volumes and not positive:
            provenance_findings.append(
                GateFinding(
                    code="v6_input_volume_is_zero",
                    subject=input_no,
                    context={"link": link_id},
                )
            )
        for volume in volumes:
            interval = " ".join((volume.get("timeInt") or "").split()) or "?"
            volume_by_link_interval[link_id][interval] += _as_float(volume.get("volume"))
            comp = volume.get("vehComp") or ""
            if comp not in composition_flows:
                findings.append(
                    GateFinding(
                        code="v6_input_composition_missing",
                        subject=input_no,
                        context={"composition": comp},
                    )
                )
        if link_id not in facts.links:
            continue  # V2 owns the dangling link reference
        uncovered = _first_uncovered_branch(facts, link_id, decision_links)
        if uncovered is not None:
            findings.append(
                GateFinding(
                    code="v6_input_without_routing_coverage",
                    subject=input_no,
                    context={"link": link_id, "branch": _label(facts, uncovered)},
                )
            )

    # -- capacity: R1.5's ONE warn clause inside a refuse check ---------------
    # "refuse(구조)/warn(용량)". Over-capacity demand is legal, runs, and lies —
    # it queues as latent demand — so it rides on the result and never refuses
    # a build on its own.
    warnings: list[GateFinding] = []
    for link_id, per_interval in sorted(
        volume_by_link_interval.items(), key=lambda kv: _id_sort_key(kv[0])
    ):
        link = facts.links.get(link_id)
        if link is None or link.lanes <= 0:
            continue
        capacity = link.lanes * ROUGH_LANE_CAPACITY_VEH_PER_H
        peak_interval, volume = max(
            sorted(per_interval.items()), key=lambda kv: kv[1], default=("?", 0.0)
        )
        if volume > capacity:
            warnings.append(
                GateFinding(
                    code="v6_link_demand_over_capacity",
                    subject=_label(facts, link_id),
                    context={
                        "volume_vph": round(volume, 2),
                        "capacity_vph": capacity,
                        "lanes": link.lanes,
                        "interval": peak_interval,
                        "intervals": len(per_interval),
                    },
                )
            )

    # -- orphaned compositions: the warn observation the GLOBAL demand gap
    # cannot make ----------------------------------------------------------
    # ``v6_no_demand_layer`` fires only when the file has NO input at all, so a
    # site whose demand was dropped from a multi-site model leaves nothing
    # behind but a composition nobody references. Warn tier, on the corpus
    # measurement in this function's docstring — 13.0 % of the real files where
    # the pattern can occur carry one, PTV's own library included.
    used_compositions, unmeasured_consumers = _composition_use(
        facts, {comp_no for comp_no in composition_flows if comp_no}
    )
    orphans = [
        comp_no
        for comp_no in sorted(composition_flows, key=_id_sort_key)
        if comp_no and comp_no not in used_compositions
    ]
    if unmeasured_consumers:
        # A site that NAMES a composition in a spelling this module has never
        # measured means "used" is not a question we can answer here, and an
        # unanswered question is never a pass — the clause reports it and
        # ``CheckResult.build`` carries it up to the check.
        gaps.append(
            GateGap(
                code="v6_composition_consumer_not_measured",
                context={"sites": ", ".join(unmeasured_consumers)},
            )
        )
    else:
        for comp_no in orphans:
            warnings.append(
                GateFinding(
                    code="v6_composition_unused",
                    subject=comp_no,
                    context={"inputs": len(facts.vehicle_inputs)},
                )
            )

    # A zero-volume vehicle input contributes nothing, which contradicts a
    # demand layer THIS generator built (it emits an input only where it has a
    # count) and is an ordinary modelling idiom in a file it did not: a
    # spreadsheet-driven build declares the same input on every entry link and
    # leaves the volume at 0 where the count was 0, and PTV's own 2026 example
    # library carries 60 of them among 1,116 inputs. Named either way; rides as
    # a warning only on a file PROVEN to be somebody else's — an unprovable
    # provenance refuses, because a demotion earned by MISSING evidence is how a
    # refuse-tier defect reaches the workspace as a published dataset.
    strict = facts.grades_as_ours
    if strict:
        findings.extend(provenance_findings)
    else:
        warnings.extend(provenance_findings)
        # In the CHECK's gaps, not the clause's alone — see ``_check_v5``.
        gaps.append(
            GateGap(
                code="v6_zero_volume_is_not_our_import",
                context={"provenance": facts.provenance.state.value},
            )
        )

    clauses = [
        _clause(
            "v6_structure",
            findings=[f for f in findings if f.code != "v6_input_volume_is_zero"],
            gaps=[g for g in gaps if g.code not in _V6_NON_STRUCTURE_GAPS],
        ),
        _clause(
            "v6_every_input_generates_traffic",
            findings=provenance_findings if strict else (),
            gaps=() if strict
            else (GateGap(code="v6_zero_volume_is_not_our_import"),),
        ),
        # Warn tier, so its findings never enter the clause: the clause states
        # whether the question was ANSWERED, and the answer rides in
        # ``warnings``. It can only be NOT_CHECKABLE (an unmeasured consumer
        # spelling) or PASS (asked and answered) — never FAIL, because an
        # unused composition is not a fault in a real ``.inpx``.
        _clause(
            "v6_composition_use_is_named",
            gaps=(
                (GateGap(code="v6_composition_consumer_not_measured"),)
                if unmeasured_consumers
                else ()
            ),
        ),
    ]
    evidence = {
        "provenance": facts.provenance.state.value,
        "zero_volume_tier": "refuse" if strict else "warn",
        "vehicle_inputs": len(facts.vehicle_inputs),
        "compositions": len(facts.compositions),
        "routing_decisions": len(facts.routing_decisions),
        "links_with_demand": len(volume_by_link_interval),
        "links_over_rough_capacity": len(
            [w for w in warnings if w.code == "v6_link_demand_over_capacity"]
        ),
        "demand_time_intervals": len(
            {i for per in volume_by_link_interval.values() for i in per}
        ),
        "rough_lane_capacity_veh_per_h": ROUGH_LANE_CAPACITY_VEH_PER_H,
    }
    # A count of orphans is only evidence when the "used" surface was fully
    # measured; otherwise the honest evidence is WHICH surface was not.
    if unmeasured_consumers:
        evidence["composition_consumer_sites_not_measured"] = ", ".join(
            unmeasured_consumers
        )
    else:
        evidence["compositions_unused"] = len(orphans)
    return CheckResult.build(
        "V6",
        tier,
        findings=findings,
        warnings=warnings,
        gaps=gaps,
        clauses=clauses,
        evidence=evidence,
    )


def _first_uncovered_branch(
    facts: _Facts, start: str, decision_links: set[str]
) -> str | None:
    """Walk forward from ``start`` and return the first branching link reached
    without passing a routing decision, or ``None`` when every path is covered.

    Deterministic: the frontier is drained in id order (a heap keyed by the same
    numeric-aware id key the rest of this module sorts by), so the first branch
    in id order wins regardless of which path reached it first. A heap rather
    than a re-sorted list because the walk is bounded only by the network — on a
    2,000-link draft with 50 inputs, re-sorting on every pop is the difference
    between a second and minutes.
    """
    seen = {start}
    frontier: list[tuple[tuple[int, int, str], str]] = [(_id_sort_key(start), start)]
    while frontier:
        _, current = heapq.heappop(frontier)
        if current in decision_links:
            continue
        exits = facts.out_edges.get(current, [])
        if len(exits) > 1:
            return current
        for nxt in exits:
            if nxt not in seen:
                seen.add(nxt)
                heapq.heappush(frontier, (_id_sort_key(nxt), nxt))
    return None


# ---------------------------------------------------------------------------
# V7 — geometry sanity (warn)
# ---------------------------------------------------------------------------


def _check_v7(facts: _Facts, reference: NetworkTopology | None) -> CheckResult:
    """Zero-length links, links on top of each other, and the two halves that
    need something this file does not carry.

    Zero-length is judged at the precision the file is written in
    (:data:`ZERO_LENGTH_M`); overlap is exact equality of the rounded polyline
    (:data:`OVERLAP_DECIMALS`). Neither is a tolerance to argue about — both are
    properties of the artifact rather than of the road.

    Link length vs map distance needs a network authored independently of this
    one to compare against; ``benchmark.py`` is that comparison and it needs a
    second file. Desired-speed distributions by road class are outside what the
    emitter writes at all. Both are named gaps rather than skipped lines.
    """
    tier = CheckTier.WARN
    findings: list[GateFinding] = []
    gaps: list[GateGap] = []

    everything = list(facts.links.values()) + list(facts.connectors.values())
    for item in sorted(everything, key=lambda i: _id_sort_key(i.id)):
        if len(item.points) < 2:
            findings.append(
                GateFinding(
                    code="v7_link_without_geometry",
                    subject=item.id,
                    context={"points": len(item.points)},
                )
            )
        elif item.length_m < ZERO_LENGTH_M:
            findings.append(
                GateFinding(
                    code="v7_zero_length_link",
                    subject=_label(facts, item.id),
                    context={"length_m": round(item.length_m, 6)},
                )
            )
        elif not item.is_connector and item.length_m < SHORT_LINK_FLOOR_M:
            findings.append(
                GateFinding(
                    code="v7_link_below_generator_floor",
                    subject=_label(facts, item.id),
                    context={
                        "length_m": round(item.length_m, 3),
                        "floor_m": SHORT_LINK_FLOOR_M,
                    },
                )
            )

    by_geometry: dict[tuple, list[str]] = defaultdict(list)
    for item in facts.links.values():
        if len(item.points) < 2:
            continue
        key = tuple(
            (round(x, OVERLAP_DECIMALS), round(y, OVERLAP_DECIMALS)) for x, y in item.points
        )
        by_geometry[key].append(item.id)
    for ids in sorted(by_geometry.values(), key=lambda v: _id_sort_key(v[0])):
        if len(ids) > 1:
            ordered = sorted(ids, key=_id_sort_key)
            findings.append(
                GateFinding(
                    code="v7_links_share_one_polyline",
                    subject=_label(facts, ordered[0]),
                    context={"links": ordered, "count": len(ordered)},
                )
            )

    if reference is None:
        gaps.append(GateGap(code="v7_no_reference_network"))
    else:
        # A reference was supplied, and this gate still does not grade the
        # comparison: ``benchmark.py`` owns it, with a 22 m tolerance derived
        # from the georeference's own residual, and restating its verdict here
        # would create a second answer to one question.
        gaps.append(
            GateGap(
                code="v7_reference_comparison_delegated",
                context={"reference": reference.label},
            )
        )

    if not facts.root.findall(".//desSpeedDecisions/desSpeedDecision"):
        gaps.append(GateGap(code="v7_no_desired_speed_decisions"))

    evidence = {
        "plain_links": len(facts.links),
        "connectors": len(facts.connectors),
        "zero_length_threshold_m": ZERO_LENGTH_M,
        "overlap_decimals": OVERLAP_DECIMALS,
        "generator_floor_m": SHORT_LINK_FLOOR_M,
    }
    return CheckResult.build("V7", tier, findings=findings, gaps=gaps, evidence=evidence)


# ---------------------------------------------------------------------------
# V8 — conflict coverage (warn)
# ---------------------------------------------------------------------------


def _check_v8(facts: _Facts, settings: GateSettings) -> CheckResult:
    """How many unsignalised crossing movements are there, and who has
    right-of-way at them?

    The first question is answerable from geometry and is answered here: two
    movements at one node whose polylines cross, sharing neither an approach nor
    an exit, are a conflict point. The second is not answerable from any
    ``.inpx``, by anything, ever — Vissim DERIVES conflict areas from the
    geometry at load time (it computed 5,518 over the Murdoch emission) and none
    of them is written back into the file; conflict areas also have no
    Add/Remove over COM. Their default right-of-way is **Passive**, which means
    vehicles drive through each other and every delay number downstream is a
    fiction.

    So V8 reports the count as evidence and the right-of-way as a named gap.
    This is the generalisation of the standing 111-Passive warning: the number
    of places the answer matters, computed rather than remembered.
    """
    tier = CheckTier.WARN
    findings: list[GateFinding] = []
    gaps: list[GateGap] = []

    signalised_links = {
        (head.get("lane") or " ").split()[0]
        for head in facts.signal_heads
        if (head.get("lane") or "").split()
    }

    crossings = 0
    unsignalised_nodes: set[str] = set()
    budget = settings.conflict_pair_budget
    exhausted = False
    if facts.topology is None or not facts.topology.node_members:
        gaps.append(GateGap(code="v8_no_node_membership"))
    else:
        for node_id in sorted(facts.topology.node_members, key=_id_sort_key):
            members = [
                facts.connectors[m]
                for m in sorted(facts.topology.node_members[node_id], key=_id_sort_key)
                if m in facts.connectors
            ]
            for index, first in enumerate(members):
                for second in members[index + 1 :]:
                    budget -= 1
                    if budget <= 0:
                        exhausted = True
                        break
                    if first.from_link == second.from_link:
                        continue
                    if first.to_link == second.to_link:
                        continue
                    if not _polylines_cross(first.points, second.points):
                        continue
                    crossings += 1
                    if (
                        first.from_link not in signalised_links
                        and second.from_link not in signalised_links
                    ):
                        unsignalised_nodes.add(node_id)
                if exhausted:
                    break
            if exhausted:
                break
    if exhausted:
        gaps.append(
            GateGap(
                code="v8_crossing_search_budget_exhausted",
                context={"budget": settings.conflict_pair_budget},
            )
        )

    gaps.append(
        GateGap(
            code="v8_right_of_way_not_in_file",
            context={
                "crossings": crossings,
                "unsignalised_nodes": len(unsignalised_nodes),
                "declared_conflict_areas": len(facts.conflict_areas),
                "declared_priority_rules": len(facts.priority_rules),
            },
        )
    )

    evidence = {
        "crossing_movement_pairs": crossings,
        "nodes_with_an_unsignalised_crossing": len(unsignalised_nodes),
        "declared_conflict_areas": len(facts.conflict_areas),
        "declared_priority_rules": len(facts.priority_rules),
    }
    return CheckResult.build("V8", tier, findings=findings, gaps=gaps, evidence=evidence)


def _polylines_cross(
    first: list[tuple[float, float]], second: list[tuple[float, float]]
) -> bool:
    """Do two polylines properly intersect? Shared endpoints do not count.

    Strict orientation test on both sides of both segments, so touching at an
    endpoint (which is what a merge or a diverge looks like) is not a crossing.
    """
    for a, b in zip(first, first[1:]):
        for c, d in zip(second, second[1:]):
            if (
                _orientation(a, b, c) * _orientation(a, b, d) < 0
                and _orientation(c, d, a) * _orientation(c, d, b) < 0
            ):
                return True
    return False


def _orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


# ---------------------------------------------------------------------------
# V9 — measurement readiness (warn)
# ---------------------------------------------------------------------------


def _check_v9(facts: _Facts) -> CheckResult:
    """Is anything in this network actually measured?

    A stop line is where a signal head is — that is the only stop-line
    population an ``.inpx`` defines. With no heads there is no population, and
    "every stop line has a queue counter" is vacuously true of nothing, so that
    half is NOT_CHECKABLE rather than a pass.

    Travel-time sections are asked for on "major corridors". Nothing in the
    file, and nothing in the draft, declares which corridors those are —
    ``benchmark.py``'s corridor scope comes from a consultant model, not from a
    generated one — so that half is a named gap in every case until a corridor
    definition exists to check against.
    """
    tier = CheckTier.WARN
    findings: list[GateFinding] = []
    gaps: list[GateGap] = []

    heads_by_link: dict[str, int] = defaultdict(int)
    for head in facts.signal_heads:
        ref = (head.get("lane") or "").split()
        if len(ref) == 2:
            heads_by_link[ref[0]] += 1

    counters_by_link: dict[str, int] = defaultdict(int)
    for counter in facts.queue_counters:
        ref = (counter.get("link") or counter.get("lane") or "").split()
        if ref:
            counters_by_link[ref[0]] += 1

    if not heads_by_link:
        gaps.append(GateGap(code="v9_no_stop_lines"))
    else:
        for link_id in sorted(heads_by_link, key=_id_sort_key):
            if counters_by_link.get(link_id, 0) == 0:
                findings.append(
                    GateFinding(
                        code="v9_stop_line_without_queue_counter",
                        subject=_label(facts, link_id),
                        context={"heads": heads_by_link[link_id]},
                    )
                )

    gaps.append(
        GateGap(
            code="v9_no_corridor_definition",
            context={"travel_time_measurements": len(facts.travel_time_measurements)},
        )
    )

    evidence = {
        "signal_heads": len(facts.signal_heads),
        "links_with_a_stop_line": len(heads_by_link),
        "queue_counters": len(facts.queue_counters),
        "travel_time_measurements": len(facts.travel_time_measurements),
        "data_collection_points": len(facts.data_collection_points),
    }
    return CheckResult.build("V9", tier, findings=findings, gaps=gaps, evidence=evidence)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def run_gate(
    inpx_path: str | Path,
    *,
    draft_metadata: dict[str, Any] | None = None,
    load_oracle: LoadOracle | None = None,
    load_oracle_gap: str | None = None,
    reference_inpx: str | Path | None = None,
    settings: GateSettings | None = None,
) -> GateResult:
    """Run all nine checks against one generated ``.inpx``.

    ``draft_metadata`` is the draft's ``*.genesis.json`` as a dict (V3's
    boundary attribution, and the evidence :class:`Provenance` rests on). It is
    admitted only when it is proven to describe THIS file — see
    :func:`_census_identity`; a sidecar about another build is ignored with the
    reason named, exactly as V1 treats a load oracle for another network.
    ``load_oracle`` is V1's live evidence; without it the verdict can never be
    an unqualified pass. Nothing here opens COM.

    An unreadable file is not an empty network: the whole gate refuses with V1
    and V2 naming the read failure, and no check reports a pass.
    """
    settings = settings or DEFAULT_SETTINGS
    path = Path(inpx_path)
    topology, read_findings = load_topology(path)
    digest = _sha256(path) if path.is_file() else ""

    if topology is None:
        refusal = read_findings[0] if read_findings else None
        return _unreadable_result(path, digest, refusal)

    root = ET.parse(path).getroot()
    facts = _Facts(
        root, topology, draft_metadata, generator_marker=_carries_generator_marker(path)
    )

    reference_topology: NetworkTopology | None = None
    if reference_inpx is not None:
        reference_topology, _ = load_topology(reference_inpx)

    checks = [
        _check_v1(facts, load_oracle, digest, load_oracle_gap),
        _check_v2(facts, topology),
        _check_v3(facts),
        _check_v4(facts, settings),
        _check_v5(facts),
        _check_v6(facts),
        _check_v7(facts, reference_topology),
        _check_v8(facts, settings),
        _check_v9(facts),
    ]
    return GateResult(
        network=path.name,
        network_sha256=digest,
        verdict=_verdict(checks),
        provenance=facts.provenance,
        checks=checks,
    )


def _verdict(checks: list[CheckResult]) -> GateVerdict:
    """PASS only when every refuse-tier check genuinely passed.

    The precedence is stated rather than inferred: a proven refuse-tier fault
    beats an unrunnable check, and an unrunnable refuse-tier check beats a pass.
    """
    refuse = [c for c in checks if c.tier is CheckTier.REFUSE]
    if any(c.state is CheckState.FAIL for c in refuse):
        return GateVerdict.REFUSED
    if any(c.state is not CheckState.PASS for c in refuse):
        return GateVerdict.INCOMPLETE
    return GateVerdict.PASS


def _unreadable_result(
    path: Path, digest: str, refusal: DiffFinding | None
) -> GateResult:
    """Every check NOT_CHECKABLE, for a file nothing could read.

    Deliberately NOT a FAIL: "this file is not a Vissim network" and "this
    network has a fault" are different answers, and ``load_topology`` already
    makes that distinction for a reason.
    """
    reason = refusal.code if refusal is not None else "network_unreadable"
    message = refusal.message if refusal is not None else str(path)
    gap = GateGap(code="gate_network_unreadable", context={"reason": reason, "detail": message})
    checks = [
        CheckResult.build(check_id, tier, gaps=[gap])
        for check_id, tier in _CHECK_TIERS.items()
    ]
    return GateResult(
        network=path.name,
        network_sha256=digest,
        verdict=GateVerdict.INCOMPLETE,
        # Nothing could be read, so nothing is known — and unknown never softens.
        provenance=ProvenanceEvidence(
            state=Provenance.UNKNOWN, census="absent", generator_marker=False
        ),
        checks=checks,
    )


_CHECK_TIERS: dict[str, CheckTier] = {
    "V1": CheckTier.REFUSE,
    "V2": CheckTier.REFUSE,
    "V3": CheckTier.REFUSE,
    "V4": CheckTier.REFUSE,
    "V5": CheckTier.REFUSE,
    "V6": CheckTier.REFUSE,
    "V7": CheckTier.WARN,
    "V8": CheckTier.WARN,
    "V9": CheckTier.WARN,
}


# ---------------------------------------------------------------------------
# Rendering (ko primary, en/ja through the same table)
# ---------------------------------------------------------------------------


def render_report(
    result: GateResult, *, language: str = "ko", settings: GateSettings | None = None
) -> str:
    settings = settings or DEFAULT_SETTINGS
    counts = result.counts()
    out: list[str] = [
        f"# {pick_text(_REPORT['heading'], language)}",
        "",
        f"**{result.network}** — sha256 {result.network_sha256[:16]}",
        "",
        f"## {pick_text(_REPORT['verdict'], language)}: "
        f"{pick_text(_VERDICT_LABEL[result.verdict.value], language)}",
        "",
        pick_text(_REPORT['summary'], language).format(**counts),
        "",
        # Provenance decides the TIER of V5's and V6's coverage clauses, so the
        # same verdict word means two different things without it on the page.
        pick_text(_REPORT["provenance"], language).format(
            state=pick_text(
                _PROVENANCE_LABEL[result.provenance.state.value], language
            ),
            census=pick_text(_CENSUS_LABEL[result.provenance.census], language),
        ),
        "",
    ]
    blocking = result.blocking
    if blocking:
        out.append(pick_text(_REPORT["blocked_by"], language))
        for check in blocking:
            out.append(
                f"- {check.id} [{pick_text(_STATE_LABEL[check.state.value], language)}] "
                f"{check.title(language)}"
            )
        out.append("")

    for check in result.checks:
        state = pick_text(_STATE_LABEL[check.state.value], language)
        tier = pick_text(_TIER_LABEL[check.tier.value], language)
        out.append(f"## {check.id} [{state}] ({tier}) {check.title(language)}")
        for clause in check.clauses:
            label = pick_text(_STATE_LABEL[clause.state.value], language)
            out.append(f"- [{label}] {clause.message(language)}")
        for gap in check.gaps[: settings.max_subjects_rendered]:
            out.append(f"- {pick_text(_REPORT['gap'], language)}: {gap.message(language)}")
        if len(check.gaps) > settings.max_subjects_rendered:
            out.append(f"  - (+{len(check.gaps) - settings.max_subjects_rendered})")
        for bucket, prefix in ((check.findings, ""), (check.warnings, "warn / ")):
            grouped: dict[str, list[GateFinding]] = defaultdict(list)
            for finding in bucket:
                grouped[finding.code].append(finding)
            for code in sorted(grouped):
                items = grouped[code]
                out.append(f"- {prefix}{code} x{len(items)}")
                for finding in items[: settings.max_subjects_rendered]:
                    out.append(f"  - {finding.message(language)}")
                if len(items) > settings.max_subjects_rendered:
                    out.append(f"  - (+{len(items) - settings.max_subjects_rendered})")
        if check.evidence:
            rendered = ", ".join(f"{k}={v}" for k, v in check.evidence.items())
            out.append(f"- {pick_text(_REPORT['evidence'], language)}: {rendered}")
        out.append("")

    out.append(f"> {pick_text(_REPORT['footer'], language)}")
    return "\n".join(out) + "\n"


_STATE_LABEL: dict[str, dict[str, str]] = {
    "pass": {"ko": "통과", "en": "PASS", "ja": "合格"},
    "fail": {"ko": "실패", "en": "FAIL", "ja": "不合格"},
    "not_checkable": {"ko": "확인 불가", "en": "NOT CHECKABLE", "ja": "確認不可"},
}

_TIER_LABEL: dict[str, dict[str, str]] = {
    "refuse": {"ko": "거부 등급", "en": "refuse tier", "ja": "拒否ティア"},
    "warn": {"ko": "경고 등급", "en": "warn tier", "ja": "警告ティア"},
}

_VERDICT_LABEL: dict[str, dict[str, str]] = {
    "pass": {"ko": "통과 — 출하 가능", "en": "PASS — shippable", "ja": "合格 — 出荷可"},
    "refused": {
        "ko": "거부 — 거부 등급 검사가 결함을 입증했습니다",
        "en": "REFUSED — a refuse-tier check proved a fault",
        "ja": "拒否 — 拒否ティアの検査が欠陥を証明しました",
    },
    "incomplete": {
        "ko": "미완 — 거부 등급 검사 중 실행되지 못한 것이 있습니다 (통과 아님)",
        "en": "INCOMPLETE — a refuse-tier check could not run (this is NOT a pass)",
        "ja": "未完 — 拒否ティアの検査が実行できませんでした(合格ではありません)",
    },
}

_PROVENANCE_LABEL: dict[str, dict[str, str]] = {
    "ours": {
        "ko": "이 생성기가 만든 네트워크(명부로 입증)",
        "en": "built by this generator (proven by its census)",
        "ja": "この生成器が作成(名簿で立証)",
    },
    "foreign": {
        "ko": "이 생성기가 만들지 않은 네트워크(이 생성기가 쓸 수 없는 최상위 컨테이너 존재)",
        "en": "NOT built by this generator (it declares a top-level container the "
        "emitter cannot write)",
        "ja": "この生成器の作成ではない(出力できない最上位コンテナが存在)",
    },
    "unknown": {
        "ko": "출처 미입증 — 거부 등급 그대로 채점합니다",
        "en": "provenance UNPROVEN — graded at the strict (refuse) tier",
        "ja": "出自は未証明 — 拒否ティアのまま採点します",
    },
}

_CENSUS_LABEL: dict[str, dict[str, str]] = {
    "absent": {
        "ko": "생성기 명부 없음",
        "en": "no generator census supplied",
        "ja": "生成器の名簿なし",
    },
    "matches_this_network": {
        "ko": "명부가 이 파일의 요소와 일치",
        "en": "the census matches this file's own elements",
        "ja": "名簿がこのファイルの要素と一致",
    },
    "describes_another_network": {
        "ko": "명부가 다른 빌드를 설명함 — 채택하지 않음",
        "en": "the census describes another build — not admitted",
        "ja": "名簿は別のビルドの記述 — 採用せず",
    },
    "declares_no_elements": {
        "ko": "명부가 요소를 선언하지 않아 대조 불가 — 채택하지 않음",
        "en": "the census declares no elements to match against — not admitted",
        "ja": "名簿が要素を宣言せず照合不可 — 採用せず",
    },
}

_REPORT: dict[str, dict[str, str]] = {
    "heading": {
        "ko": "R1.5 네트워크 검증 게이트",
        "en": "R1.5 network validation gate",
        "ja": "R1.5 ネットワーク検証ゲート",
    },
    "verdict": {"ko": "판정", "en": "Verdict", "ja": "判定"},
    "summary": {
        "ko": "통과 {pass}건 · 실패 {fail}건 · 확인 불가 {not_checkable}건 (9개 검사)",
        "en": "{pass} passed, {fail} failed, {not_checkable} not checkable (9 checks)",
        "ja": "合格{pass}件・不合格{fail}件・確認不可{not_checkable}件(9検査)",
    },
    "blocked_by": {
        "ko": "게이트를 막고 있는 거부 등급 검사:",
        "en": "Refuse-tier checks blocking the gate:",
        "ja": "ゲートを止めている拒否ティア検査:",
    },
    "gap": {"ko": "확인 불가 사유", "en": "not checkable", "ja": "確認不可の理由"},
    "evidence": {"ko": "근거", "en": "evidence", "ja": "根拠"},
    "provenance": {
        "ko": "출처: {state} — {census}",
        "en": "Provenance: {state} — {census}",
        "ja": "出自: {state} — {census}",
    },
    "footer": {
        "ko": "실행되지 못한 검사는 통과로 계산되지 않습니다. V1 없이는 어떤 판정도 무조건적 통과가 아닙니다.",
        "en": "A check that could not run is never counted as a pass. Without V1 no verdict is "
        "an unqualified pass.",
        "ja": "実行できなかった検査は合格に数えません。V1 なしの判定は無条件の合格ではありません。",
    },
}

_CHECK_TITLE: dict[str, dict[str, str]] = {
    "V1": {
        "ko": "로드 오라클 — 실제 Vissim이 이 파일을 열고 네트워크를 보유했는가",
        "en": "Load oracle — a real Vissim opened this file AND held the network",
        "ja": "ロードオラクル — 実際の Vissim が開き、ネットワークを保持したか",
    },
    "V2": {
        "ko": "참조 무결성 — 파일이 선언하지 않은 대상을 가리키는 참조 0건",
        "en": "Reference integrity — no reference points at something the file never declares",
        "ja": "参照整合性 — ファイルが宣言しない対象への参照 0 件",
    },
    "V3": {
        "ko": "연결성 — 진입·통과·이탈이 가능하고, 이탈은 경계에서만",
        "en": "Connectivity — traffic gets in, through and out, and leaves only at the boundary",
        "ja": "接続性 — 流入・通過・流出が可能で、流出は境界のみ",
    },
    "V4": {
        "ko": "차로 연속성 — 존재하는 차로만 매핑, 차로 소실엔 차로변경 거리 확보",
        "en": "Lane continuity — only existing lanes are mapped, and a dropped lane has its runway",
        "ja": "車線連続性 — 存在する車線のみ対応付け、車線消失には車線変更距離を確保",
    },
    "V5": {
        "ko": "신호-기하 정합 — 신호 접근로마다 head, 모든 group이 head에, 컨트롤러 없는 head 0건",
        "en": "Signal-geometry consistency — a head on every signalised approach, every group on a "
        "head, no head without a controller",
        "ja": "信号-幾何整合 — 信号化approachごとにhead、全groupがheadに、controller無しのhead 0件",
    },
    "V6": {
        "ko": "수요 커버리지 — 볼륨·구성·라우팅 결정, 그리고 링크 용량",
        "en": "Demand coverage — volumes, compositions, routing decisions, and link capacity",
        "ja": "需要カバレッジ — 交通量・構成・経路決定、そしてリンク容量",
    },
    "V7": {
        "ko": "기하 위생 — 0-길이/중첩 링크, 지도 대비 오차, 도로 등급별 희망속도",
        "en": "Geometry sanity — zero-length and overlapping links, map error, desired speeds by class",
        "ja": "幾何の健全性 — 0長・重複リンク、地図との誤差、道路等級別の希望速度",
    },
    "V8": {
        "ko": "상충 커버리지 — 비신호 상충점 수와 그 우선권",
        "en": "Conflict coverage — how many unsignalised conflict points, and who has right of way",
        "ja": "競合カバレッジ — 非信号の競合点の数とその優先権",
    },
    "V9": {
        "ko": "계측 준비 — 정지선마다 queue counter, corridor마다 travel-time section",
        "en": "Measurement readiness — a queue counter per stop line, a travel-time section per corridor",
        "ja": "計測準備 — 停止線ごとの queue counter、corridor ごとの travel-time section",
    },
}

#: Every finding and gap code, ko primary. Formatted with the finding's own
#: context, so a message can never state a number the data does not carry.
_TEXT: dict[str, dict[str, str]] = {
    # -- gate-level --------------------------------------------------------
    "gate_network_unreadable": {
        "ko": "네트워크 파일을 읽지 못해 아홉 검사 모두 실행 불가입니다 ({reason}): {detail}",
        "en": "The network file could not be read, so none of the nine checks ran ({reason}): {detail}",
        "ja": "ネットワークファイルを読めず、9 検査すべて実行不能です ({reason}): {detail}",
    },
    # -- V1 ----------------------------------------------------------------
    "v1_no_live_evidence": {
        "ko": "실제 Vissim 로드 증거가 없습니다 ({reason}). "
        "tools/genesis_open_live_check.py 로 이 파일을 열고 그 state.json 을 --live-check-json 으로 주세요.",
        "en": "No live-Vissim load evidence ({reason}). Open this file with "
        "tools/genesis_open_live_check.py and pass its state.json as --live-check-json.",
        "ja": "実 Vissim のロード証拠がありません ({reason})。tools/genesis_open_live_check.py で"
        "開き、その state.json を --live-check-json で渡してください。",
    },
    "v1_oracle_identity_unprovable": {
        "ko": "로드 증거가 가리키는 파일({path})이 더 이상 존재하지 않아 동일 파일임을 증명할 수 없습니다.",
        "en": "The file the load evidence names ({path}) no longer exists, so it cannot be proven "
        "to be the file being gated.",
        "ja": "ロード証拠が指すファイル({path})が存在せず、同一ファイルであることを証明できません。",
    },
    "v1_oracle_names_another_network": {
        "ko": "로드 증거는 다른 파일에 대한 것입니다 ({path}: {oracle_sha256}… vs 검사 대상 {gated_sha256}…).",
        "en": "The load evidence is about a different file ({path}: {oracle_sha256}… vs the gated "
        "file's {gated_sha256}…).",
        "ja": "ロード証拠は別のファイルに関するものです({path}: {oracle_sha256}… / 対象 {gated_sha256}…)。",
    },
    "v1_no_object_census": {
        "ko": "로드는 성공했으나 COM 객체 수를 기록하지 않았습니다 — Vissim은 링크 0개인 빈 로드에도 성공을 보고하므로, "
        "객체 수 없이는 로드 성공이 아무것도 증명하지 않습니다.",
        "en": "The load succeeded but no COM object census was recorded — Vissim reports success "
        "for an EMPTY load (zero links), so a load result without a census proves nothing.",
        "ja": "ロードは成功しましたが COM オブジェクト数の記録がありません — Vissim はリンク 0 の"
        "空ロードでも成功を返すため、個数なしでは何も証明されません。",
    },
    "v1_no_save_round_trip": {
        "ko": "SaveNetAs 왕복이 기록되지 않았습니다.",
        "en": "No SaveNetAs round trip was recorded.",
        "ja": "SaveNetAs の往復が記録されていません。",
    },
    "v1_load_refused": {
        "ko": "{subject}: Vissim이 로드를 거부했습니다 — {error}",
        "en": "{subject}: Vissim refused to load it — {error}",
        "ja": "{subject}: Vissim がロードを拒否しました — {error}",
    },
    "v1_save_refused": {
        "ko": "{subject}: SaveNetAs 실패 — {error}",
        "en": "{subject}: SaveNetAs failed — {error}",
        "ja": "{subject}: SaveNetAs 失敗 — {error}",
    },
    "v1_err_file_written": {
        "ko": "Vissim이 .err 파일 {count}개를 남겼습니다 ({subject}): {text}",
        "en": "Vissim wrote {count} .err file(s) ({subject}): {text}",
        "ja": "Vissim が .err ファイルを {count} 件書き出しました({subject}): {text}",
    },
    "v1_link_count_mismatch": {
        "ko": "{subject}: 파일의 link 요소는 {in_file}개인데 Vissim은 {in_vissim}개만 보유 — 조용한 부분 로드입니다.",
        "en": "{subject}: the file declares {in_file} link element(s) but Vissim held {in_vissim} "
        "— a silent partial load.",
        "ja": "{subject}: ファイルの link 要素は {in_file} 件ですが Vissim の保持は {in_vissim} 件 — 静かな部分ロードです。",
    },
    "v1_plain_link_count_mismatch": {
        "ko": "{subject}: 일반 링크가 파일 {in_file}개 vs Vissim {in_vissim}개.",
        "en": "{subject}: plain links, {in_file} in the file vs {in_vissim} in Vissim.",
        "ja": "{subject}: 通常リンクはファイル {in_file} 件、Vissim {in_vissim} 件。",
    },
    "v1_connector_count_mismatch": {
        "ko": "{subject}: 커넥터가 파일 {in_file}개 vs Vissim {in_vissim}개.",
        "en": "{subject}: connectors, {in_file} in the file vs {in_vissim} in Vissim.",
        "ja": "{subject}: コネクタはファイル {in_file} 件、Vissim {in_vissim} 件。",
    },
    "v1_node_count_mismatch": {
        "ko": "{subject}: 노드가 파일 {in_file}개 vs Vissim {in_vissim}개.",
        "en": "{subject}: nodes, {in_file} in the file vs {in_vissim} in Vissim.",
        "ja": "{subject}: ノードはファイル {in_file} 件、Vissim {in_vissim} 件。",
    },
    # -- V2 ----------------------------------------------------------------
    "v2_reference_table_not_measured_for_tag": {
        "ko": "이 파일은 참조 표가 측정된 적 없는 요소 {count}종을 담고 있어 무결성을 주장할 수 없습니다: {tags}",
        "en": "The file carries {count} element tag(s) this reference table was never measured "
        "against, so integrity cannot be claimed: {tags}",
        "ja": "参照表が測定されていない要素 {count} 種を含むため、整合性を主張できません: {tags}",
    },
    "v2_unresolved_reference": {
        "ko": "{subject} → {target} '{value}' — 파일이 선언하지 않은 대상입니다.",
        "en": "{subject} → {target} '{value}' — the file never declares that object.",
        "ja": "{subject} → {target} '{value}' — ファイルが宣言していない対象です。",
    },
    "v2_malformed_pair_reference": {
        "ko": "{subject} = '{value}' — {expected} 형식이 아닙니다.",
        "en": "{subject} = '{value}' — not of the form {expected}.",
        "ja": "{subject} = '{value}' — {expected} の形式ではありません。",
    },
    "v2_lane_index_out_of_range": {
        "ko": "{subject} = '{value}' — 링크 {link}의 차로는 {lanes}개뿐입니다.",
        "en": "{subject} = '{value}' — link {link} has only {lanes} lane(s).",
        "ja": "{subject} = '{value}' — リンク {link} の車線は {lanes} 本のみです。",
    },
    "v2_connector_endpoint_dangling": {
        "ko": "{subject} — 네트워크에 없는 링크를 가리킵니다.",
        "en": "{subject} — points at a link the network does not contain.",
        "ja": "{subject} — ネットワークに存在しないリンクを指しています。",
    },
    "v2_reference_context_not_measured": {
        "ko": "참조가 놓인 위치 {count}종을 측정한 적이 없어 그 대상을 추정하지 않고 미확인으로 둡니다: "
        "{contexts}",
        "en": "{count} reference context(s) have never been measured, so their targets are left "
        "unresolved rather than guessed: {contexts}",
        "ja": "参照の置かれた文脈 {count} 種を測定したことがないため、対象を推測せず未確認とします: "
        "{contexts}",
    },
    "v2_references_resolve": {
        "ko": "측정된 문맥의 모든 참조가 파일이 선언한 대상을 가리킨다",
        "en": "every reference in a measured context names an object the file declares",
        "ja": "測定済み文脈の全参照がファイルの宣言対象を指す",
    },
    "v2_surface_fully_measured": {
        "ko": "이 파일의 모든 요소 종류에 대해 참조 표면이 측정되어 있다",
        "en": "the reference surface of every element type in this file has been measured",
        "ja": "このファイルの全要素種について参照表面が測定済み",
    },
    # -- V3 ----------------------------------------------------------------
    "v3_no_vehicle_inputs": {
        "ko": "vehicle input이 하나도 없어 '모든 input이 출구에 도달하는가'를 검사할 대상이 없습니다.",
        "en": "The file declares no vehicle inputs, so 'every input reaches an exit' has nothing "
        "to check.",
        "ja": "vehicle input が 1 件もなく、「全 input が出口に到達するか」の検査対象がありません。",
    },
    "v3_no_generator_census": {
        "ko": ".inpx만으로는 '모델 경계에서의 정상 이탈'과 '조용한 이탈'을 구분할 수 없습니다 — "
        "생성기의 dangling 명부(*.genesis.json)를 함께 주세요.",
        "en": "An .inpx alone cannot tell a legitimate departure at the model boundary from a "
        "silent one — supply the generator's own census (*.genesis.json).",
        "ja": ".inpx だけではモデル境界での正常な流出と静かな流出を区別できません — "
        "生成器の名簿(*.genesis.json)を渡してください。",
    },
    "v3_census_is_not_about_this_network": {
        "ko": "생성기 명부가 있지만 이 파일을 설명하지 않습니다({reason}: 명부 요소 {census_elements}개 "
        "vs 파일 요소 {network_elements}개) — 다른 빌드의 명부는 약한 근거가 아니라 근거가 아닙니다.",
        "en": "A generator census was supplied but it does not describe THIS file "
        "({reason}: {census_elements} census element(s) vs {network_elements} in the network) — "
        "a census about another build is not weak evidence, it is no evidence.",
        "ja": "生成器の名簿はありますが、このファイルを説明していません({reason}: 名簿 "
        "{census_elements} 要素 vs ファイル {network_elements} 要素) — 別ビルドの名簿は根拠になりません。",
    },
    "v3_orphan_link": {
        "ko": "{subject}: 도착하는 커넥터도 떠나는 커넥터도 없는 고아 링크입니다.",
        "en": "{subject}: an orphan link — no connector arrives on it and none leaves it.",
        "ja": "{subject}: 到着も出発もコネクタがない孤立リンクです。",
    },
    "v3_isolated_zone": {
        "ko": "{subject} 외 — 링크 {links}개가 네트워크에서 완전히 고립된 구역입니다: 들어갈 수도 나올 수도 없습니다.",
        "en": "{subject} and others — a zone of {links} link(s) completely isolated from the "
        "network: nothing can get in and nothing can get out.",
        "ja": "{subject} ほか — リンク {links} 本がネットワークから完全に孤立した区域です: 出入りできません。",
    },
    "v3_broken_zone": {
        "ko": "{subject} 외 — 링크 {links}개의 구역이 진입 불가와 이탈 불가가 섞인 상태입니다.",
        "en": "{subject} and others — a zone of {links} link(s) mixing links nothing reaches with "
        "links that reach no exit.",
        "ja": "{subject} ほか — リンク {links} 本の区域が到達不能と流出不能の混在状態です。",
    },
    "v3_unreachable_zone": {
        "ko": "{subject} 외 — 네트워크 어느 진입점에서도 도달할 수 없는 링크 {links}개의 구역입니다.",
        "en": "{subject} and others — a zone of {links} link(s) no path from any network entry "
        "reaches.",
        "ja": "{subject} ほか — どの進入点からも到達できないリンク {links} 本の区域です。",
    },
    "v3_trapped_zone": {
        "ko": "{subject} 외 — 어떤 출구에도 도달할 수 없는 링크 {links}개의 구역입니다(진입한 차량은 나가지 못합니다).",
        "en": "{subject} and others — a zone of {links} link(s) from which no network exit is "
        "reachable; a vehicle that gets in never leaves.",
        "ja": "{subject} ほか — どの出口にも到達できないリンク {links} 本の区域です(入った車両は出られません)。",
    },
    "v3_input_cannot_reach_an_exit": {
        "ko": "{subject}: 이 vehicle input에서 출발한 차량은 어떤 네트워크 출구에도 도달할 수 없습니다.",
        "en": "{subject}: traffic starting at this vehicle input can reach no network exit.",
        "ja": "{subject}: この vehicle input から出発した車両はどの出口にも到達できません。",
    },
    "v3_silent_departure_point": {
        "ko": "{subject}: 모델 경계가 아닌데 떠나는 커넥터가 없습니다 — 차량이 조용히 사라지는 지점입니다 ({cause}).",
        "en": "{subject}: no connector leaves this link end and it is not the model boundary — "
        "vehicles depart the network here in silence ({cause}).",
        "ja": "{subject}: モデル境界でないのに出発するコネクタがありません — 車両が静かに消える地点です({cause})。",
    },
    "v3_reachability": {
        "ko": "고아 링크 0건, 도달 불능·이탈 불능 구역 0건 (.inpx만으로 판정 가능)",
        "en": "no orphan links and no unreachable or trapped zone (answerable from the .inpx alone)",
        "ja": "孤立リンク 0 件、到達不能・離脱不能ゾーン 0 件(.inpx 単独で判定可能)",
    },
    "v3_inputs_reach_an_exit": {
        "ko": "모든 vehicle input에서 네트워크 출구에 도달할 수 있다",
        "en": "every vehicle input can reach a network exit",
        "ja": "全 vehicle input からネットワーク出口に到達できる",
    },
    "v3_departures_are_at_the_boundary": {
        "ko": "차량이 떠나는 지점은 전부 모델 경계다 (생성기 명부 필요)",
        "en": "every point traffic departs at is the model boundary (needs the generator's census)",
        "ja": "車両が離脱する地点はすべてモデル境界である(生成器の名簿が必要)",
    },
    # -- V4 ----------------------------------------------------------------
    "v4_link_declares_no_lane_change_distance": {
        "ko": "링크 {link}가 lnChgDist를 선언하지 않아 필요한 차로변경 거리를 알 수 없습니다.",
        "en": "Link {link} declares no lnChgDist, so the lane-change distance it needs is unknown.",
        "ja": "リンク {link} が lnChgDist を宣言しておらず、必要な車線変更距離が不明です。",
    },
    "v4_runway_search_budget_exhausted": {
        "ko": "링크 {link}의 상류 거리 탐색이 예산 {budget}회를 소진했습니다 — 하한값으로 판정하지 않습니다.",
        "en": "The upstream-distance search for link {link} exhausted its budget of {budget} "
        "expansions; a lower bound is not graded.",
        "ja": "リンク {link} の上流距離探索が予算 {budget} 回を使い切りました — 下限値では判定しません。",
    },
    "v4_more_mapped_lanes_than_link_has": {
        "ko": "커넥터 {subject}: {side} 쪽 링크 {link}의 차로는 {link_lanes}개인데 {mapped_lanes}개 차로를 "
        "매핑합니다 — 존재하지 않는 차로를 잇습니다.",
        "en": "Connector {subject}: it maps {mapped_lanes} lane(s) on its {side} side, but link "
        "{link} has only {link_lanes} — it joins lanes that do not exist.",
        "ja": "コネクタ {subject}: {side} 側リンク {link} の車線は {link_lanes} 本ですが {mapped_lanes} "
        "本を対応付けています — 存在しない車線を繋いでいます。",
    },
    "v4_lane_reference_malformed": {
        "ko": "커넥터 {subject}: {side} 차로 참조 '{value}'가 '<링크> <차로>' 형식이 아닙니다.",
        "en": "Connector {subject}: {side} lane reference '{value}' is not '<link> <lane>'.",
        "ja": "コネクタ {subject}: {side} 車線参照 '{value}' が '<link> <lane>' 形式ではありません。",
    },
    "v4_lane_index_outside_link": {
        "ko": "커넥터 {subject}: {side} 차로 {lane}은(는) 링크 {link}에 없습니다(차로 {link_lanes}개).",
        "en": "Connector {subject}: {side} lane {lane} does not exist on link {link} "
        "({link_lanes} lane(s)).",
        "ja": "コネクタ {subject}: {side} 車線 {lane} はリンク {link} に存在しません(車線 {link_lanes} 本)。",
    },
    "v4_lane_drop_without_runway": {
        "ko": "{subject}: 차로 {dropped_lanes}에서 떠나는 커넥터가 없는데, 이 파일이 요구하는 차로변경 거리 "
        "{required_m} m 중 {available_m} m만 상류에 존재합니다 — 이 차로의 차량은 합류하지 못하고 삭제됩니다.",
        "en": "{subject}: no connector leaves lane(s) {dropped_lanes}, and only {available_m} m of "
        "upstream road exists against the {required_m} m this file itself declares a lane change "
        "needs — traffic in that lane cannot merge and is deleted.",
        "ja": "{subject}: 車線 {dropped_lanes} を出るコネクタがなく、本ファイルが要求する車線変更距離 "
        "{required_m} m に対し上流は {available_m} m しかありません — その車線の車両は合流できず削除されます。",
    },
    # -- V5 ----------------------------------------------------------------
    "v5_no_signal_layer": {
        "ko": "신호 컨트롤러도 signal head도 없습니다. 정말 비신호 네트워크인지, 신호 임포트가 빠진 것인지 "
        "파일만으로는 알 수 없으므로 통과로 계산하지 않습니다.",
        "en": "The file declares no signal controllers and no signal heads. Whether that is a "
        "genuinely unsignalised network or a missing signal import cannot be told from the file, "
        "so it is not counted as a pass.",
        "ja": "信号コントローラも signal head もありません。真に非信号なのか信号インポート漏れなのかは"
        "ファイルからは判断できないため、合格に数えません。",
    },
    "v5_no_node_membership": {
        "ko": "노드 소속을 읽을 수 없어 '신호화 접근로마다 head'를 검사할 수 없습니다.",
        "en": "Node membership could not be read, so 'a head on every signalised approach' cannot "
        "be checked.",
        "ja": "ノード所属が読めず、「信号化approachごとにhead」を検査できません。",
    },
    "v5_heads_belong_to_no_node": {
        "ko": "signal head가 있는 링크가 어떤 노드에도 속하지 않아 접근로 커버리지를 검사할 수 없습니다.",
        "en": "The links carrying signal heads belong to no node, so approach coverage cannot be "
        "checked.",
        "ja": "signal head を持つリンクがどのノードにも属さず、approach カバレッジを検査できません。",
    },
    "v5_head_signal_group_malformed": {
        "ko": "signal head {subject}: sg 참조 '{value}'가 '<컨트롤러> <그룹>' 형식이 아닙니다.",
        "en": "Signal head {subject}: its sg reference '{value}' is not '<controller> <group>'.",
        "ja": "signal head {subject}: sg 参照 '{value}' が '<controller> <group>' 形式ではありません。",
    },
    "v5_head_without_controller": {
        "ko": "signal head {subject}: 컨트롤러 {controller}가 파일에 없습니다.",
        "en": "Signal head {subject}: controller {controller} is not in the file.",
        "ja": "signal head {subject}: コントローラ {controller} がファイルにありません。",
    },
    "v5_head_without_signal_group": {
        "ko": "signal head {subject}: 컨트롤러 {controller}에 signal group {group}이(가) 없습니다.",
        "en": "Signal head {subject}: controller {controller} declares no signal group {group}.",
        "ja": "signal head {subject}: コントローラ {controller} に signal group {group} がありません。",
    },
    "v5_signal_group_without_head": {
        "ko": "signal group {subject}: 이 그룹을 표시하는 signal head가 없습니다 — 현시가 아무것도 제어하지 않습니다.",
        "en": "Signal group {subject}: no signal head displays it — the phase controls nothing.",
        "ja": "signal group {subject}: これを表示する signal head がありません — 現示が何も制御しません。",
    },
    "v5_approach_reaches_no_head_in_node": {
        "ko": "{subject}: 노드 {node}는 신호화되어 있는데, 이 접근로로 진입한 차량은 노드를 빠져나갈 때까지 "
        "어떤 signal head도 만나지 않습니다.",
        "en": "{subject}: node {node} is signalised, but traffic entering on this approach meets no "
        "signal head anywhere before it leaves the node.",
        "ja": "{subject}: ノード {node} は信号化されていますが、この approach から進入した車両はノードを"
        "出るまでいかなる signal head にも出会いません。",
    },
    "v5_heads_resolve": {
        "ko": "모든 signal head가 실재하는 컨트롤러·그룹·차로를 가리킨다",
        "en": "every signal head names a controller, a group and a lane that exist",
        "ja": "全 signal head が実在するコントローラ・グループ・車線を指す",
    },
    "v5_coverage_of_the_imported_phasing": {
        "ko": "임포트한 phasing의 커버리지 — 모든 group이 head에, 신호 노드의 모든 진입 접근로가 head를 만난다",
        "en": "coverage of the phasing we imported — every group on a head, every entering approach "
        "of a signalised node meeting one",
        "ja": "取り込んだ phasing のカバレッジ — 全 group が head に、信号ノードの全進入 approach が head に出会う",
    },
    "v5_coverage_is_not_our_import": {
        "ko": "이 네트워크는 이 생성기가 만든 것이 아닙니다 — phasing 커버리지가 결함인지 설계인지는 "
        "파일이 말해주지 않으므로, 발견 사항은 이름을 붙여 경고로만 보고합니다.",
        "en": "This network was not built by this generator, so whether a coverage gap in its "
        "phasing is a defect or a modelling choice is not something the file can say; the "
        "observations are named and reported as warnings only.",
        "ja": "このネットワークは本ジェネレータの生成物ではないため、phasing のカバレッジ不足が欠陥か"
        "設計かはファイルからは判断できません。所見は名前を付けて警告としてのみ報告します。",
    },
    # -- V6 ----------------------------------------------------------------
    "v6_no_demand_layer": {
        "ko": "vehicle input이 없습니다 — 수요가 없는 네트워크는 아무것도 시뮬레이션하지 않으므로 "
        "커버리지 검사가 성립하지 않습니다.",
        "en": "The file declares no vehicle inputs. A network with no demand simulates nothing, so "
        "coverage cannot be assessed.",
        "ja": "vehicle input がありません — 需要のないネットワークは何もシミュレートしないため、"
        "カバレッジ検査が成立しません。",
    },
    "v6_composition_empty": {
        "ko": "vehicle composition {subject}: 상대 유량 항목이 하나도 없습니다.",
        "en": "Vehicle composition {subject}: it carries no relative-flow entry at all.",
        "ja": "vehicle composition {subject}: 相対流量の項目が 1 件もありません。",
    },
    "v6_composition_relflow_sum_zero": {
        "ko": "vehicle composition {subject}: 상대 유량 {flows}개의 합이 0이라 어떤 차량도 생성되지 않습니다.",
        "en": "Vehicle composition {subject}: its {flows} relative flows sum to zero, so it "
        "generates no vehicles.",
        "ja": "vehicle composition {subject}: 相対流量 {flows} 件の合計が 0 で、車両が生成されません。",
    },
    "v6_composition_relflow_negative": {
        "ko": "vehicle composition {subject}: 차종 {veh_type}의 상대 유량이 음수입니다.",
        "en": "Vehicle composition {subject}: vehicle type {veh_type} carries a negative relative flow.",
        "ja": "vehicle composition {subject}: 車種 {veh_type} の相対流量が負です。",
    },
    "v6_input_without_volume": {
        "ko": "vehicle input {subject}: 시간대별 볼륨이 하나도 없습니다.",
        "en": "Vehicle input {subject}: it declares no time-interval volume at all.",
        "ja": "vehicle input {subject}: 時間帯別の交通量が 1 件もありません。",
    },
    "v6_input_volume_is_zero": {
        "ko": "vehicle input {subject}(링크 {link}): 모든 구간의 볼륨이 0입니다.",
        "en": "Vehicle input {subject} (link {link}): every interval's volume is zero.",
        "ja": "vehicle input {subject}(リンク {link}): すべての区間の交通量が 0 です。",
    },
    "v6_input_composition_missing": {
        "ko": "vehicle input {subject}: 참조하는 composition {composition}이(가) 파일에 없습니다.",
        "en": "Vehicle input {subject}: the composition it references ({composition}) is not in the file.",
        "ja": "vehicle input {subject}: 参照する composition {composition} がファイルにありません。",
    },
    "v6_input_without_routing_coverage": {
        "ko": "vehicle input {subject}(링크 {link}): 라우팅 결정을 만나기 전에 분기 {branch}에 도달합니다 — "
        "그 분기의 배분은 관측이 아니라 Vissim이 만들어낸 값이 됩니다.",
        "en": "Vehicle input {subject} (link {link}): traffic reaches branch {branch} before meeting "
        "any routing decision — the split there is one Vissim invents, not one that was observed.",
        "ja": "vehicle input {subject}(リンク {link}): 経路決定に出会う前に分岐 {branch} に到達します — "
        "その配分は観測ではなく Vissim が作り出した値になります。",
    },
    "v6_link_demand_over_capacity": {
        "ko": "{subject}: 구간 {interval}에서 차로 {lanes}개의 개략 용량 {capacity_vph} veh/h를 "
        "동시 수요 {volume_vph} veh/h가 초과합니다(전체 {intervals}개 구간 중 최대) — 초과분은 "
        "화면에 보이지 않는 잠재 수요로 대기합니다.",
        "en": "{subject}: in interval {interval} the concurrent demand {volume_vph} veh/h exceeds "
        "the rough capacity {capacity_vph} veh/h of {lanes} lane(s) (the peak of {intervals} "
        "interval(s)) — the excess queues invisibly as latent demand.",
        "ja": "{subject}: 区間 {interval} で車線 {lanes} 本の概略容量 {capacity_vph} veh/h を同時需要 "
        "{volume_vph} veh/h が超えています(全 {intervals} 区間の最大) — 超過分は不可視の潜在需要"
        "として待機します。",
    },
    "v6_composition_unused": {
        "ko": "vehicle composition {subject}: 이 파일의 vehicle input {inputs}개 중 어느 것도(주차장 "
        "초기 점유·동적 배정 수요 포함) 이 composition을 사용하지 않습니다 — 한 지점의 수요가 통째로 "
        "빠졌다면 파일에 남는 유일한 흔적이 이것입니다. 쓰지 않는 템플릿 composition은 실제 모델에서 "
        "흔하므로 거부가 아닌 관찰입니다.",
        "en": "Vehicle composition {subject}: none of this file's {inputs} vehicle input(s) uses it, "
        "nor does a parking lot's initial occupancy or a dynamic-assignment demand — if a whole "
        "site's demand was dropped, this is the only trace left in the file. Unused template "
        "compositions are common in real models, so this is an observation, not a refusal.",
        "ja": "vehicle composition {subject}: このファイルの vehicle input {inputs} 件のいずれも"
        "(駐車場の初期占有・動的配分需要を含む)これを使用していません — ある地点の需要が丸ごと"
        "抜けた場合、ファイルに残る唯一の痕跡がこれです。未使用のテンプレート composition は実際の"
        "モデルでは珍しくないため、拒否ではなく観察として報告します。",
    },
    "v6_composition_consumer_not_measured": {
        "ko": "composition을 참조한다고 이름 붙은 위치 {sites}는 측정된 적이 없습니다 — 어떤 "
        "composition이 실제로 쓰이지 않는지 이 파일에서는 판단할 수 없습니다.",
        "en": "The site(s) {sites} name a composition in a spelling that has never been measured, so "
        "which compositions are genuinely unused cannot be decided for this file.",
        "ja": "composition を参照すると名乗る位置 {sites} は測定されたことがありません — どの "
        "composition が実際に未使用かをこのファイルでは判断できません。",
    },
    "v6_composition_use_is_named": {
        "ko": "선언된 모든 vehicle composition의 사용 여부를 확인했다 — 쓰이지 않는 것은 경고로 "
        "이름을 붙인다",
        "en": "every declared vehicle composition was checked for use — the unused ones are named as "
        "warnings",
        "ja": "宣言された全 vehicle composition の使用有無を確認した — 未使用のものは警告として名前を"
        "付ける",
    },
    "v6_structure": {
        "ko": "구조 — composition 합, 참조, 그리고 모든 input이 분기 전에 라우팅 결정을 만난다",
        "en": "structure — compositions add up, references resolve, every input meets a routing "
        "decision before it branches",
        "ja": "構造 — composition の合計・参照・全 input が分岐前に経路決定に出会う",
    },
    "v6_every_input_generates_traffic": {
        "ko": "우리가 만든 수요층의 모든 vehicle input이 실제로 차량을 생성한다",
        "en": "every vehicle input in the demand layer we built actually generates traffic",
        "ja": "我々が構築した需要層の全 vehicle input が実際に車両を生成する",
    },
    "v6_zero_volume_is_not_our_import": {
        "ko": "이 수요층은 이 생성기가 만든 것이 아닙니다 — 볼륨 0인 input이 누락인지 '해당 없음'의 "
        "표기인지 파일이 말해주지 않으므로, 이름을 붙여 경고로만 보고합니다.",
        "en": "This demand layer was not built by this generator, so whether a zero-volume input is "
        "a missing count or a recorded 'none' is not something the file can say; it is named and "
        "reported as a warning only.",
        "ja": "この需要層は本ジェネレータの生成物ではないため、交通量 0 の input が欠落なのか「該当なし」"
        "の記録なのかはファイルからは判断できません。名前を付けて警告としてのみ報告します。",
    },
    # -- V7 ----------------------------------------------------------------
    "v7_no_reference_network": {
        "ko": "링크 길이 대 지도 거리 오차는 독립적으로 작성된 참조 네트워크가 있어야 측정됩니다 "
        "(services.genesis.benchmark). 이번 실행에는 없습니다.",
        "en": "Link length vs map distance needs an independently-authored reference network to "
        "measure against (services.genesis.benchmark). None was supplied.",
        "ja": "リンク長と地図距離の誤差は独立に作成された参照ネットワークが必要です"
        "(services.genesis.benchmark)。今回は与えられていません。",
    },
    "v7_reference_comparison_delegated": {
        "ko": "참조 네트워크 {reference}와의 기하 대응은 services.genesis.benchmark의 소관이며 "
        "이 게이트는 그 결과를 대신 판정하지 않습니다.",
        "en": "Geometric correspondence against reference {reference} belongs to "
        "services.genesis.benchmark; this gate does not restate its verdict.",
        "ja": "参照ネットワーク {reference} との幾何対応は services.genesis.benchmark の担当で、"
        "本ゲートはその判定を代弁しません。",
    },
    "v7_no_desired_speed_decisions": {
        "ko": "희망속도 분포 배정(desSpeedDecision)이 방출 범위 밖이라 도로 등급별 배정을 검사할 수 없습니다.",
        "en": "Desired-speed decisions are outside what the emitter writes, so the assignment of "
        "distributions by road class cannot be checked.",
        "ja": "希望速度分布の割当(desSpeedDecision)は出力範囲外のため、道路等級別の割当を検査できません。",
    },
    "v7_link_without_geometry": {
        "ko": "{subject}: 폴리라인 점이 {points}개뿐이라 형상이 없습니다.",
        "en": "{subject}: only {points} polyline point(s) — it has no shape.",
        "ja": "{subject}: ポリライン点が {points} 点のみで形状がありません。",
    },
    "v7_zero_length_link": {
        "ko": "{subject}: 길이 {length_m} m — 파일이 기록하는 정밀도에서 0입니다.",
        "en": "{subject}: {length_m} m long — zero at the precision the file is written in.",
        "ja": "{subject}: 長さ {length_m} m — ファイルの記録精度では 0 です。",
    },
    "v7_link_below_generator_floor": {
        "ko": "{subject}: 길이 {length_m} m로, 생성기 자신이 세그먼트를 버리는 하한 {floor_m} m보다 짧습니다.",
        "en": "{subject}: {length_m} m long, below the {floor_m} m floor its own generator drops "
        "segments at.",
        "ja": "{subject}: 長さ {length_m} m で、生成器自身がセグメントを捨てる下限 {floor_m} m 未満です。",
    },
    "v7_links_share_one_polyline": {
        "ko": "{subject}: 링크 {count}개가 완전히 같은 폴리라인 위에 겹쳐 있습니다 ({links}).",
        "en": "{subject}: {count} links lie on exactly the same polyline ({links}).",
        "ja": "{subject}: リンク {count} 本がまったく同じポリライン上に重なっています({links})。",
    },
    # -- V8 ----------------------------------------------------------------
    "v8_no_node_membership": {
        "ko": "노드 소속을 읽을 수 없어 상충점을 셀 수 없습니다.",
        "en": "Node membership could not be read, so conflict points cannot be counted.",
        "ja": "ノード所属が読めず、競合点を数えられません。",
    },
    "v8_crossing_search_budget_exhausted": {
        "ko": "상충 쌍 탐색이 예산 {budget}쌍을 소진해 상충점 수가 완전하지 않습니다.",
        "en": "The crossing search exhausted its budget of {budget} pairs, so the conflict-point "
        "count is not complete.",
        "ja": "競合ペア探索が予算 {budget} 組を使い切り、競合点数が完全ではありません。",
    },
    "v8_right_of_way_not_in_file": {
        "ko": "상충점 {crossings}개(비신호 노드 {unsignalised_nodes}곳)를 찾았지만, 우선권은 파일 안에 없습니다: "
        "Vissim이 로드 시 기하에서 conflict area를 직접 도출하며 파일에는 {declared_conflict_areas}개, "
        "priority rule은 {declared_priority_rules}개만 선언되어 있습니다. 기본값은 Passive이며, "
        "그 경우 차량은 서로를 통과합니다 — 이 판정은 실 Vissim에서만 가능합니다.",
        "en": "{crossings} crossing movement pair(s) found at {unsignalised_nodes} unsignalised "
        "node(s), but right-of-way is not in the file: Vissim DERIVES conflict areas from the "
        "geometry at load time, and the file declares {declared_conflict_areas} conflict area(s) "
        "and {declared_priority_rules} priority rule(s). The default is Passive, under which "
        "vehicles drive through each other — only a live Vissim can settle this.",
        "ja": "競合ペア {crossings} 件(非信号ノード {unsignalised_nodes} 箇所)を検出しましたが、"
        "優先権はファイルにありません: Vissim はロード時に幾何から conflict area を導出し、"
        "ファイルの宣言は conflict area {declared_conflict_areas} 件、priority rule "
        "{declared_priority_rules} 件のみです。既定は Passive で、車両は互いを通過します。",
    },
    # -- V9 ----------------------------------------------------------------
    "v9_no_stop_lines": {
        "ko": ".inpx가 정의하는 정지선은 signal head의 위치뿐인데 head가 하나도 없습니다 — "
        "'정지선마다 queue counter'는 검사할 대상이 없습니다.",
        "en": "The only stop-line population an .inpx defines is where the signal heads are, and "
        "there are none — 'a queue counter per stop line' has nothing to check.",
        "ja": ".inpx が定義する停止線は signal head の位置のみですが、head が 1 件もありません — "
        "「停止線ごとの queue counter」の検査対象がありません。",
    },
    "v9_no_corridor_definition": {
        "ko": "어떤 corridor가 '주요'인지 파일도 draft도 선언하지 않아 travel-time section 배치를 "
        "검사할 수 없습니다(현재 {travel_time_measurements}개).",
        "en": "Neither the file nor the draft declares which corridors are 'major', so travel-time "
        "section placement cannot be checked ({travel_time_measurements} present).",
        "ja": "どの corridor が「主要」かをファイルも draft も宣言しておらず、travel-time section の"
        "配置を検査できません(現在 {travel_time_measurements} 件)。",
    },
    "v9_stop_line_without_queue_counter": {
        "ko": "{subject}: signal head {heads}개가 있는 정지선에 queue counter가 없습니다 — 대기열은 측정되지 않습니다.",
        "en": "{subject}: a stop line with {heads} signal head(s) and no queue counter — its queue "
        "is never measured.",
        "ja": "{subject}: signal head {heads} 件の停止線に queue counter がありません — 待ち行列は計測されません。",
    },
}
