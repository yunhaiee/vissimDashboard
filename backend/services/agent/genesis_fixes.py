"""The genesis fix-mapping registry (roadmap B5-2 stage 1) — from "which R1.5
gate finding fired" to "which parameter, which verb, or an honest no".

``services.genesis.gate`` (V1-V9) REFUSES a generated network and names each
fault with a finding CODE (``v3_orphan_link``, ``v6_input_without_volume``,
...). This module is the same practitioner-diagnosis step ``fixes.py`` is for
edit-time constraint/anomaly findings, aimed at gate findings instead: given
one gate finding, name the SPECIFIC registered verb that repairs it and the
SPECIFIC params derived from the evidence in hand, or say — with a named
reason — why no automated fix exists.

This is a SIBLING of ``fixes.py``, not a fork of it. Every generic piece of
that unit's machinery is imported and reused directly rather than
reimplemented: the outcome vocabulary (:data:`fixes.PROPOSAL` /
:data:`fixes.REFUSAL` / :data:`fixes.NO_AUTOMATED_FIX`), the one-shape
:class:`fixes.FixProposal` payload, live verb resolution
(:func:`fixes.resolve_verb`) and params validation against the verb's REAL
pydantic model (:func:`fixes.validate_params`), and — for the one code family
that is genuinely a demand question (V6) — the D4 doctrine itself
(:func:`fixes.entrance_measurement`, :data:`fixes.INPUT_VOLUME_IS_OBSERVED_DATA`,
:data:`fixes.CONFIDENCE_DEMAND_ESTIMATE`,
:data:`fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED`). Nothing here reimplements
that join.

The doctrine
------------
Same as ``fixes.py``'s: **the mapping PROPOSES, the approval loop DISPOSES**,
and a proposal that cannot state its evidence must not be emitted. Two more
rules are specific to what a genesis gate finding IS:

* **Connectivity is not invented** (roadmap: "연결성 발명 금지"). Every V3
  code — a link nothing connects to, a zone the network cannot reach or
  leave, a vehicle input that cannot get out, a departure point the census
  never declared a boundary — names a hole in the road network, and no
  registered verb closes a hole without fabricating the road that would close
  it. Six of the seven codes are therefore declared
  :data:`fixes.NOT_AUTOMATABLE` under :data:`CONNECTIVITY_IS_NOT_INVENTED`,
  and where the generator's own census already named a cause
  (``one_way_dead_end_unfixable`` and its siblings —
  ``services.genesis.connectivity``'s own vocabulary), that cause is quoted
  verbatim in the rationale rather than paraphrased. Since unit #36 the
  refusal also STATES, measured from the job's own source ``.osm`` when the
  caller derived it (``genesis_jobs.derive_v3_source_testimony``, threaded
  through ``targets['source_testimony']``), whether the source itself shares
  the gap or declares the very connection the network lost.

  **Unit #35 makes the seventh code treatable WITHOUT weakening that rule,
  because the rule is about the CONNECTION, not about the connector.**
  ``v3_silent_departure_point`` — the one V3 code that names a single link
  END, which is exactly the shape ``add_connector``'s from-side needs — maps
  to ``add_connector``, and its builder emits a proposal only when the
  connection FACT has a NAMED SOURCE: either the job's own ``.osm`` testifies
  it (unit #36's testimony, way and node ids quoted verbatim, confidence
  :data:`CONFIDENCE_SOURCE_TESTIMONY`) or a human attests it and takes
  responsibility for it (``targets['human_attestation']`` naming the target
  link, a basis sentence, and who attested it when — confidence
  :data:`CONFIDENCE_HUMAN_ATTESTATION`, assumption
  :data:`ASSUME_ATTESTED_NOT_MEASURED`). With neither, the refusal is exactly
  the one unit #36 left, down to the quoted cause. What the machine
  contributes in both branches is MECHANICAL CONSTRUCTION from two link ends
  that already exist — the params are derived by
  ``genesis_jobs.derive_v3_connector_plan`` (the to-link from the testified
  way or the attested id, the positions from the two ends, the lane mapping
  from the generator's OWN ``connector_lane_pairs`` rule) and never invented
  here, which is why every proposal carries
  :data:`ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED`: the source testified the
  CONNECTION, not the geometry. The gate is untouched and stays the judge —
  a treated departure point either clears on the sibling job's re-gate or it
  does not.
* **A road ends where the user's requested boundary ends, and that is not a
  defect to fabricate past.** V4's ``v4_lane_drop_without_runway`` is the one
  finding whose correct answer depends on WHY the runway is short: when the
  shortfall traces to a node this generator itself synthesized on the
  requested extract boundary (``services.genesis.connectivity``'s
  ``DANGLING_MODEL_BOUNDARY`` census entries), lengthening the link would
  extend the network past the area the user asked for — :data:`fixes.NOT_AUTOMATABLE`
  under :data:`RUNWAY_ENDS_AT_REQUESTED_BOUNDARY`. Anywhere else, there is
  simply no registered verb that lengthens a link's runway without inventing
  new road (``set_link_geometry`` REPOSITIONS a link's existing points, it
  cannot add the point count a genuinely longer runway would need) —
  :data:`NO_TOOL_WITHOUT_FABRICATION`. Since unit #39 that WHY is MEASURED
  when the caller derived it (``genesis_jobs.derive_v4_boundary_attribution``,
  threaded through ``targets['boundary_attribution']``): the judge's own
  runway walk re-traced to its upstream terminus, the terminus classified by
  the census's own boundary markers, and the source asked what road it
  declares beyond — the refusal then states the measured attribution instead
  of "no evidence", under the same reason codes and the same tier.

Registry shape — DECLARED status, DERIVED liveness
---------------------------------------------------
Same split as ``fixes.py``'s ``FixMapping``/``register_fix``. A
:class:`GenesisFixMapping` DECLARES :data:`fixes.AUTOMATABLE` (names a verb
and a builder) or :data:`fixes.NOT_AUTOMATABLE` (names a reason code, no
verb). Whether an ``AUTOMATABLE`` entry's verb is actually still live is
DERIVED, never declared — :func:`effective_status` asks
:func:`fixes.resolve_verb` at call time, so a verb renamed or re-tiered in
``tools_edit.py`` degrades the entry to :data:`fixes.VERB_UNREGISTERED`
without anybody having to remember to edit this catalogue.

**A third disposition, also derived** (unit #38): :data:`REGENERATE_WITH_FIX`
— the treatment is not an artifact edit at all, but a NEW genesis job
re-derived from the SAME source through a corrected converter. Its doctrine is
this catalogue's own, stated once more because a whole re-generation sounds
like a bigger claim than an edit and is not: **nothing is invented — the
artifact is re-derived from the same source**, byte-for-byte the ``.osm`` the
parent was built from, through the same pipeline and the same unmodified gate.
It applies where the census DEMONSTRABLY cannot correct a finding because the
draft already shares the defect (:func:`regeneration_disposition`, keyed by
``genesis_jobs.derive_v7_corrected_polyline``'s measured ``reason_code``) —
never as a fallback for "no fix found", which would turn an honest refusal into
a re-roll of the same dice.

One extension beyond ``fixes.py``'s own rule: a ``NOT_AUTOMATABLE`` entry MAY
also carry a ``builder``, used when the reason code itself depends on
evidence rather than being fixed for the key (``v4_lane_drop_without_runway``
is the only entry that needs this — boundary-traced and non-boundary
shortfalls are both "no automated fix", but under two DIFFERENT named
reasons). :func:`register_genesis_fix` still forbids naming a verb on a
``NOT_AUTOMATABLE`` entry, and :func:`propose_genesis_fix` asserts such a
builder never actually emits a :data:`fixes.PROPOSAL` — a declared "there is
no fix" that quietly proposed one anyway would be exactly the kind of
catalogue drift ``fixes.py``'s own derived-liveness machinery exists to catch
elsewhere.

The mirror check
-----------------
:data:`GATE_FINDING_CODES` is the hand-maintained list of every code
``services.genesis.gate`` can emit as a ``GateFinding`` (both refuse-tier
findings and warn-tier warnings — both are ``GateFinding`` instances there;
gap codes and clause codes are a different vocabulary and are not this
module's concern). Gate finding codes are string literals scattered through
``gate.py``'s nine check functions, not exported constants, so this list
cannot be built by import — ``backend/tests/test_genesis_fixes.py`` keeps it
honest the way the spec asks: a test walks ``gate.py``'s own AST and
re-derives the code set from source, so a new finding code added to a check
function shows up as a test failure here rather than as a silent gap.
:func:`get_unmapped_finding_codes` is the runtime mirror of the same fact —
every code in :data:`GATE_FINDING_CODES` must resolve in :func:`get_genesis_fix_registry`
or be findable there as an explicit "no automated fix" entry; nothing may
fall between the two.

No COM, no I/O, no Vissim. Every builder here operates on in-memory gate
findings and caller-supplied evidence (the draft's own ``*.genesis.json``
census, an observed-counts payload in ``fixes.node_evidence_from_real_counts``
shape, or explicit derived values the caller already has network knowledge
for) — exactly ``fixes.py``'s own "this module does not guess network
knowledge it was not handed" rule, applied to genesis evidence instead of
edit-time run evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from services.agent import fixes
from services.agent.toolkit import ToolRegistry
from services.genesis.connectivity import DANGLING_MODEL_BOUNDARY

# ---------------------------------------------------------------------------
# Key kind — a gate finding code, never a constraint/anomaly/validation key
# ---------------------------------------------------------------------------

#: This module's own ``key_kind`` for :class:`fixes.FixProposal.key_kind` —
#: distinct from ``fixes.py``'s ``KEY_CONSTRAINT``/``KEY_ANOMALY``/``KEY_VALIDATION``
#: because a gate finding code is none of those three things.
KEY_GATE_FINDING = "gate_finding"

_MISSING = object()


# ---------------------------------------------------------------------------
# Reason codes new to THIS catalogue. Generic ones (NO_REGISTERED_VERB,
# EVIDENCE_INSUFFICIENT, VERB_NOT_IN_REGISTRY, PARAMS_REJECTED_BY_SCHEMA,
# INPUT_VOLUME_IS_OBSERVED_DATA, NO_MAPPING_REGISTERED) are reused directly
# from ``fixes`` rather than respelled here.
# ---------------------------------------------------------------------------

#: V3's whole family (roadmap: "연결성 발명 금지" — connectivity is not
#: invented). No registered verb closes a hole in the road network without
#: fabricating the road that would close it.
CONNECTIVITY_IS_NOT_INVENTED = "CONNECTIVITY_IS_NOT_INVENTED"
#: V4's ``v4_lane_drop_without_runway``, boundary-traced case: the runway is
#: short because the road ends where the user's requested extract boundary
#: ends. Lengthening it would extend the network past the requested area.
RUNWAY_ENDS_AT_REQUESTED_BOUNDARY = "RUNWAY_ENDS_AT_REQUESTED_BOUNDARY"
#: No registered verb performs the edit this fix would need WITHOUT
#: fabricating data the evidence does not supply (new road length, an
#: invented vehicle-composition mix, a guessed capacity addition, ...). This
#: is deliberately distinct from ``fixes.NO_REGISTERED_VERB``: that code means
#: "nothing reaches this parameter at all"; this one means "a verb that
#: reaches the parameter exists, but using it here would mean inventing the
#: value rather than deriving it."
NO_TOOL_WITHOUT_FABRICATION = "NO_TOOL_WITHOUT_FABRICATION"
#: V1's whole family. V1 exists to prove a REAL Vissim opened this file and
#: that what it holds matches the file; "fixing" a V1 finding would mean
#: editing that evidence into existence, which defeats the reason the check
#: exists.
EVIDENCE_CANNOT_BE_EDITED_INTO_EXISTENCE = "EVIDENCE_CANNOT_BE_EDITED_INTO_EXISTENCE"
#: V2's whole family. A genesis draft with a broken internal reference is a
#: bug in the EMITTER that produced the file, not a finding an approval loop
#: patches after the fact — the repair belongs in ``services.genesis.emit``,
#: with a regression test, not in an edit proposed here.
EMITTER_BUG_NOT_APPROVAL_PATCH = "EMITTER_BUG_NOT_APPROVAL_PATCH"
#: V5's provenance-scoped family. Which signal groups/approaches a head
#: covers is exactly what roadmap track C1-b's 현시(phase) unit is for; this
#: catalogue does not reach ahead of that unit's own data.
SIGNAL_COVERAGE_IS_C1B_TERRITORY = "SIGNAL_COVERAGE_IS_C1B_TERRITORY"
#: Unit #35. An attestation arrived, but it is not one: no target link, no
#: basis sentence, or no attributable attester. Deliberately distinct from
#: :data:`CONNECTIVITY_IS_NOT_INVENTED` (which means "nobody named this
#: connection at all") — here somebody tried to, and the claim was incomplete.
#: Completing it with a default would make this system the attester.
ATTESTATION_INCOMPLETE = "ATTESTATION_INCOMPLETE"


# ---------------------------------------------------------------------------
# The third disposition (unit #38): treat it by REGENERATING, not by editing
# ---------------------------------------------------------------------------

#: A disposition beside :data:`fixes.AUTOMATABLE` and
#: :data:`fixes.NOT_AUTOMATABLE`, and the one that is neither an edit nor a
#: shrug: **the treatment is a NEW generation from the SAME source, through the
#: corrected converter.**
#:
#: The doctrine, unchanged from every other entry in this catalogue: *nothing is
#: invented — the artifact is re-derived from the same source.* No coordinate is
#: guessed, no link is deleted, no edit is applied to the refused artifact. The
#: same ``.osm`` bytes (sha-verified against the parent's own recorded digest)
#: go through the same pipeline and the same unmodified gate, and the
#: regenerated job stands or falls on its own verdict. The parent stays
#: byte-untouched, exactly as B5-2 requires of every sibling job.
#:
#: DERIVED, never declared. :func:`register_genesis_fix` refuses it as a
#: declared status for the same reason ``fixes.py`` refuses
#: :data:`fixes.VERB_UNREGISTERED`: whether regeneration is the answer for a
#: given finding depends on what the EVIDENCE measured about that finding, not
#: on what a catalogue author guessed when they typed the entry. The measurement
#: is :func:`regeneration_disposition` below.
REGENERATE_WITH_FIX = "REGENERATE_WITH_FIX"

#: The measured reasons — ``genesis_jobs.derive_v7_corrected_polyline``'s own
#: ``reason_code`` vocabulary — that mean *the census cannot correct this, and a
#: converter fix can*. Exactly one today: unit #34 measured that all 164 real
#: ``v7_links_share_one_polyline`` findings have draft twins byte-identical at
#: full precision, i.e. the corruption predates the draft. Unit #38 then found
#: what upstream did it (a source declaring one way id twice) and fixed the
#: converter, which is what makes a re-derivation a TREATMENT rather than a
#: re-roll of the same dice.
#:
#: Deliberately a SET keyed by measured reason, not by finding code: a v7
#: finding whose census CAN correct it keeps unit #34's ``set_link_geometry``
#: path, and only the uncorrectable ones are offered a regeneration.
REGENERABLE_DERIVATION_REASONS: frozenset[str] = frozenset({"draft_shares_the_corruption"})

#: The localized sentence a regenerable disposition adds — what the button
#: actually does, in the vocabulary of the existing cards. Deliberately states
#: the two facts a reviewer needs before pressing it: the same source, and a
#: verdict of its own.
_REGENERATE_CLAUSE: tuple[str, str, str] = (
    " 치료 경로: 이 산출물을 편집하는 대신, 같은 원본(.osm)을 수정된 변환기로 다시 생성합니다."
    " 원본 바이트는 부모 작업의 기록된 sha와 대조해 확인하며, 부모는 한 바이트도 변경되지 않고,"
    " 재생성된 작업은 동일한 게이트에서 자기 자신의 판정을 받습니다.",
    " Treatment path: instead of editing this artifact, the SAME source .osm is re-derived"
    " through the corrected converter. The source bytes are verified against the parent's"
    " recorded sha, the parent is left byte-untouched, and the regenerated job faces the same"
    " gate on its own merits.",
    " 治療の道筋: この成果物を編集する代わりに、同じ元データ(.osm)を修正済みコンバータで再生成します。"
    " 元データのバイト列は親ジョブの記録済みshaと照合し、親は1バイトも変更されず、"
    " 再生成されたジョブは同じゲートで自身の判定を受けます。",
)


def regeneration_disposition(
    code: str, proposal: Any, *, converter_fixes: Any = None
) -> dict[str, Any] | None:
    """``REGENERATE_WITH_FIX`` for this finding, or ``None``.

    ONE measurement, in one place, so the fixes LISTING and
    ``genesis_jobs.create_regenerate_job``'s own eligibility gate can never
    disagree about which findings the regeneration path is for (the same
    shared-derivation rule ``routers/genesis.py``'s ``get_job_fixes`` docstring
    already states for evidence).

    Eligible iff the proposal's own evidence carries a census derivation whose
    measured ``reason_code`` is in :data:`REGENERABLE_DERIVATION_REASONS` — i.e.
    the draft ITSELF already carries the defect, so no edit derived from the
    census can clear it and no coordinate may be invented to. A finding the
    census CAN correct is not eligible: it already has unit #34's honest
    ``set_link_geometry`` path, and offering a whole regeneration instead would
    be the bigger hammer for the smaller nail.

    ``converter_fixes`` is the caller's own ledger of what the converter now
    does differently (``services.genesis.osm.CONVERTER_FIXES``), threaded in
    rather than imported so this module keeps its no-I/O, no-genesis-pipeline
    import surface. Absent, the disposition still names the path — a
    regeneration whose fix ledger could not be read is still a regeneration, and
    saying so is better than pretending the finding has no treatment.
    """
    evidence = getattr(proposal, "evidence", None) or {}
    derivation = evidence.get("census_derivation") if isinstance(evidence, Mapping) else None
    if not isinstance(derivation, Mapping):
        return None
    reason_code = str(derivation.get("reason_code") or "")
    if reason_code not in REGENERABLE_DERIVATION_REASONS:
        return None
    disposition: dict[str, Any] = {
        "status": REGENERATE_WITH_FIX,
        "finding_code": code,
        "derivation_reason_code": reason_code,
        "explanation": {
            "ko": _REGENERATE_CLAUSE[0].strip(),
            "en": _REGENERATE_CLAUSE[1].strip(),
            "ja": _REGENERATE_CLAUSE[2].strip(),
        },
    }
    if converter_fixes is not None:
        disposition["converter_fixes"] = [dict(entry) for entry in converter_fixes]
    return disposition


# ---------------------------------------------------------------------------
# Confidence + assumption vocabulary new to THIS catalogue (unit #35). Kept
# here rather than in ``fixes.py`` for the same reason the reason codes above
# are: these name evidence a GENESIS gate finding can have and an edit-time
# constraint finding cannot. Both values are deliberately NOT arithmetic —
# neither branch computes a number, and ranking either alongside
# ``CONFIDENCE_ARITHMETIC`` would be exactly the confusion ``fixes.py``'s own
# CONFIDENCE_DEMAND_ESTIMATE note warns about.
# ---------------------------------------------------------------------------

#: The connection this fix builds is declared by the job's OWN source ``.osm``
#: (unit #36's testimony: ways sharing a node, with a drivable departure), and
#: the way/node ids are quoted verbatim in the rationale. Strong evidence
#: about the CONNECTION; it says nothing about the connector's geometry, which
#: is why every such proposal also carries
#: :data:`ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED`.
CONFIDENCE_SOURCE_TESTIMONY = "SOURCE_TESTIFIED_CONNECTION"
#: The connection this fix builds is a HUMAN's claim — they know the site, the
#: source does not declare it, and they named the target link and their basis
#: under their own name. Accountable, and not a measurement: the fix records
#: who attested it and when, everywhere it surfaces.
CONFIDENCE_HUMAN_ATTESTATION = "HUMAN_ATTESTED_CONNECTION"

#: What the machine contributed, stated as the caveat it is: the connector's
#: geometry, position and lane mapping are CONSTRUCTED from the two existing
#: link ends (the generator's own ``connector_lane_pairs`` rule over its own
#: ``classify_turn``) — the named source testified that the movement EXISTS,
#: never what shape it has on the ground.
ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED = "CONNECTOR_GEOMETRY_CONSTRUCTED"
#: The connection rests on a person's declaration, not on a measurement of any
#: kind — the machine half of the same statement the rationale makes in prose
#: (and the reason the attester's name and timestamp ride in the evidence).
ASSUME_ATTESTED_NOT_MEASURED = "ATTESTED_NOT_MEASURED"


# ---------------------------------------------------------------------------
# The canonical code list — SOURCE-DERIVED, kept honest by
# backend/tests/test_genesis_fixes.py (walks gate.py's AST and re-derives
# this set; see that test for the extraction method). Hand-maintained here
# only because gate.py's codes are string literals, not exported constants —
# never edit this list without re-running that test.
# ---------------------------------------------------------------------------

GATE_FINDING_CODES: frozenset[str] = frozenset(
    {
        # V1 — load oracle (refuse)
        "v1_load_refused",
        "v1_err_file_written",
        "v1_link_count_mismatch",
        "v1_plain_link_count_mismatch",
        "v1_connector_count_mismatch",
        "v1_node_count_mismatch",
        "v1_save_refused",
        # V2 — reference integrity (refuse)
        "v2_unresolved_reference",
        "v2_malformed_pair_reference",
        "v2_lane_index_out_of_range",
        "v2_connector_endpoint_dangling",
        # V3 — connectivity (refuse)
        "v3_orphan_link",
        "v3_isolated_zone",
        "v3_unreachable_zone",
        "v3_trapped_zone",
        "v3_broken_zone",
        "v3_input_cannot_reach_an_exit",
        "v3_silent_departure_point",
        # V4 — lane continuity (refuse)
        "v4_more_mapped_lanes_than_link_has",
        "v4_lane_reference_malformed",
        "v4_lane_index_outside_link",
        "v4_lane_drop_without_runway",
        # V5 — signal-geometry consistency (refuse; two of its five codes are
        # provenance-scoped, see the module docstring)
        "v5_head_signal_group_malformed",
        "v5_head_without_controller",
        "v5_head_without_signal_group",
        "v5_signal_group_without_head",
        "v5_approach_reaches_no_head_in_node",
        # V6 — demand coverage (refuse for structure; v6_link_demand_over_capacity
        # and v6_composition_unused ride as WARN-tier GateFinding "warnings")
        "v6_composition_empty",
        "v6_composition_relflow_sum_zero",
        "v6_composition_relflow_negative",
        "v6_input_without_volume",
        "v6_input_volume_is_zero",
        "v6_input_composition_missing",
        "v6_input_without_routing_coverage",
        "v6_link_demand_over_capacity",
        "v6_composition_unused",
        # V7 — geometry sanity (warn)
        "v7_link_without_geometry",
        "v7_zero_length_link",
        "v7_link_below_generator_floor",
        "v7_links_share_one_polyline",
        # V8 — conflict coverage (warn): emits GateGap only, never a
        # GateFinding — the right-of-way question cannot be read from an
        # .inpx at all (module docstring), so there is nothing to name here.
        # V9 — measurement readiness (warn)
        "v9_stop_line_without_queue_counter",
    }
)


# ---------------------------------------------------------------------------
# The catalogue entry
# ---------------------------------------------------------------------------

_BuilderFn = Callable[..., "fixes.FixProposal"]


@dataclass(frozen=True)
class GenesisFixMapping:
    """One gate finding code -> the parameter/verb that repairs it, or an
    honest declaration that none exists.

    ``code`` is one of :data:`GATE_FINDING_CODES` — never prose. ``builder``
    is required when ``status`` is :data:`fixes.AUTOMATABLE`; it MAY also be
    present when ``status`` is :data:`fixes.NOT_AUTOMATABLE`, for the one
    case (``v4_lane_drop_without_runway``) where WHICH "no automated fix"
    reason applies depends on evidence rather than being fixed for the key —
    see the module docstring. A ``NOT_AUTOMATABLE`` entry never names a verb,
    builder or not.
    """

    code: str
    status: str
    check_id: str
    label_ko: str
    label_en: str
    label_ja: str
    reason_ko: str
    reason_en: str
    reason_ja: str
    verb: str | None = None
    builder: _BuilderFn | None = None
    reason_code: str | None = None
    param_fields: tuple[str, ...] = ()
    roadmap_rows: tuple[str, ...] = ("B5-2",)

    def label(self) -> dict[str, str]:
        return {"ko": self.label_ko, "en": self.label_en, "ja": self.label_ja}

    def reason(self) -> dict[str, str]:
        return {"ko": self.reason_ko, "en": self.reason_en, "ja": self.reason_ja}


_REGISTRY: dict[str, GenesisFixMapping] = {}


def register_genesis_fix(mapping: GenesisFixMapping) -> GenesisFixMapping:
    """Add a mapping to the module-level catalogue, keyed by its gate code.

    Validates only what is knowable WITHOUT the live tool registry — the same
    split ``fixes.register_fix`` makes, for the same reason (whether a named
    verb still resolves is exactly what :func:`effective_status` derives live,
    and raising here on that would turn a reportable drift into an import-time
    crash of the whole backend).
    """
    if mapping.code in _REGISTRY:
        raise ValueError(f"register_genesis_fix: duplicate code {mapping.code!r}")
    if mapping.code not in GATE_FINDING_CODES:
        raise ValueError(
            f"register_genesis_fix: {mapping.code!r} is not one of gate.py's own finding "
            "codes (see GATE_FINDING_CODES)"
        )
    if mapping.status not in (fixes.AUTOMATABLE, fixes.NOT_AUTOMATABLE):
        raise ValueError(
            f"register_genesis_fix: {mapping.code!r} declared status {mapping.status!r}; "
            f"declarable values are {fixes.AUTOMATABLE!r} and {fixes.NOT_AUTOMATABLE!r} "
            f"({fixes.VERB_UNREGISTERED!r} and {REGENERATE_WITH_FIX!r} are derived, "
            "never declared)"
        )
    if mapping.status == fixes.AUTOMATABLE:
        if not mapping.verb:
            raise ValueError(f"register_genesis_fix: {mapping.code!r} is AUTOMATABLE but names no verb")
        if mapping.builder is None:
            raise ValueError(
                f"register_genesis_fix: {mapping.code!r} is AUTOMATABLE but has no builder — a "
                "verb name without a way to fill its params is not a fix"
            )
    else:
        if mapping.verb:
            raise ValueError(
                f"register_genesis_fix: {mapping.code!r} is NOT_AUTOMATABLE but names verb "
                f"{mapping.verb!r}"
            )
        if mapping.builder is None and not mapping.reason_code:
            raise ValueError(
                f"register_genesis_fix: {mapping.code!r} is NOT_AUTOMATABLE, has no per-call "
                "builder, and carries no reason_code saying WHY there is no automated fix"
            )
    if not (mapping.reason_ko and mapping.reason_en and mapping.reason_ja):
        raise ValueError(f"register_genesis_fix: {mapping.code!r} needs a ko/en/ja reason")
    _REGISTRY[mapping.code] = mapping
    return mapping


def get_genesis_fix_registry() -> tuple[GenesisFixMapping, ...]:
    """Every registered mapping, in registration order."""
    return tuple(_REGISTRY.values())


def get_genesis_fix(code: str) -> GenesisFixMapping | None:
    return _REGISTRY.get(code)


# ---------------------------------------------------------------------------
# Derived liveness — computed from the live tool registry on every call
# ---------------------------------------------------------------------------


def effective_status(
    mapping: GenesisFixMapping, *, registry: ToolRegistry | None = None
) -> tuple[str, dict[str, Any]]:
    """``(status, detail)`` from the LIVE tool registry.

    An ``AUTOMATABLE`` entry whose verb no longer resolves (renamed, removed,
    re-tiered in ``tools_edit.py``) degrades to :data:`fixes.VERB_UNREGISTERED`
    — delegated straight to ``fixes.resolve_verb``, so this catalogue and
    ``fixes.py``'s own can never disagree about what "live" means.
    """
    if mapping.status == fixes.AUTOMATABLE:
        resolution = fixes.resolve_verb(mapping.verb, registry=registry)
        detail = {"verb": mapping.verb, "verb_resolution": resolution}
        if not resolution["resolved"]:
            return fixes.VERB_UNREGISTERED, detail
        return fixes.AUTOMATABLE, detail
    return fixes.NOT_AUTOMATABLE, {"verb": None}


def get_genesis_fix_coverage(*, registry: ToolRegistry | None = None) -> dict[str, dict[str, Any]]:
    """``{code: coverage}`` derived from live registry state, mirroring
    ``fixes.get_fix_coverage``."""
    coverage: dict[str, dict[str, Any]] = {}
    for mapping in _REGISTRY.values():
        status, detail = effective_status(mapping, registry=registry)
        coverage[mapping.code] = {
            "code": mapping.code,
            "check_id": mapping.check_id,
            "label": mapping.label(),
            "declared_status": mapping.status,
            "status": status,
            "verb": mapping.verb,
            "param_fields": list(mapping.param_fields),
            "reason": mapping.reason(),
            "reason_code": mapping.reason_code,
            "has_evidence_dependent_reason": mapping.status == fixes.NOT_AUTOMATABLE
            and mapping.builder is not None,
            "roadmap_rows": list(mapping.roadmap_rows),
            "detail": detail,
        }
    return coverage


def get_unmapped_finding_codes() -> tuple[str, ...]:
    """Every code the gate can emit with NO entry in this catalogue at all.

    The mirror of ``fixes.get_unmapped_anomalies``: a gate finding code must
    have either a fix entry or an explicit no-fix entry, and nothing may fall
    between the two. Non-empty only when this catalogue has fallen behind
    ``gate.py`` — see ``backend/tests/test_genesis_fixes.py`` for the
    source-derived check that keeps :data:`GATE_FINDING_CODES` itself honest.
    """
    return tuple(sorted(code for code in GATE_FINDING_CODES if code not in _REGISTRY))


# ---------------------------------------------------------------------------
# Finding field access — a gate.GateFinding pydantic instance OR a plain
# Mapping (dict), the same duck-typing fixes.propose_for_finding uses for
# constraints.Finding.
# ---------------------------------------------------------------------------


def _finding_field(finding: Any, name: str, default: Any = None) -> Any:
    if finding is None:
        return default
    value = getattr(finding, name, _MISSING)
    if value is not _MISSING:
        return value
    if isinstance(finding, Mapping):
        return finding.get(name, default)
    return default


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """``value`` when it is a mapping, an empty one otherwise — so a builder
    reading a caller-supplied derivation record never has to guard each
    ``.get`` individually, and a malformed one degrades to "no record" rather
    than to an exception inside a rationale."""
    return value if isinstance(value, Mapping) else {}


def _link_id_from_subject(subject: Any) -> str:
    """The bare object id out of a gate subject string.

    ``gate._label`` writes subjects as ``"<id> (<generator's own name>)"``
    when a name is known, or the bare id otherwise. Splitting on the first
    ``" ("`` recovers the id in both cases — this re-parses an id already
    embedded in the evidence, it does not guess one.
    """
    text = str(subject if subject is not None else "").strip()
    return text.split(" (", 1)[0].strip()


# ---------------------------------------------------------------------------
# Proposal / refusal construction — same two-step ``fixes._emit``/``_refuse``
# shape, built on ``fixes.FixProposal`` directly (one shape, shared with
# every other consumer of that dataclass).
# ---------------------------------------------------------------------------


def _refuse(
    *,
    code: str,
    reason_code: str,
    evidence: dict[str, Any],
    ko: str,
    en: str,
    ja: str,
    outcome: str = fixes.NO_AUTOMATED_FIX,
) -> fixes.FixProposal:
    return fixes.FixProposal(
        key=code,
        key_kind=KEY_GATE_FINDING,
        outcome=outcome,
        scope=fixes.NOT_APPLICABLE,
        verb=None,
        params=None,
        evidence=evidence,
        rationale_ko=ko,
        rationale_en=en,
        rationale_ja=ja,
        confidence=fixes.CONFIDENCE_NONE,
        reason_code=reason_code,
    )


def _emit(
    *,
    code: str,
    verb: str,
    params: dict[str, Any],
    evidence: dict[str, Any],
    ko: str,
    en: str,
    ja: str,
    confidence: str,
    assumptions: tuple[str, ...] = (),
    scope: str = fixes.NOT_APPLICABLE,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """Build a PROPOSAL, but only after the LIVE registry accepts it.

    Delegates verb resolution and params validation straight to
    ``fixes.resolve_verb``/``fixes.validate_params`` — the exact checks
    ``toolkit.dispatch`` would apply — so a payload this catalogue THINKS is
    valid but the live schema rejects is downgraded to a refusal here rather
    than shipped to an approval card that would fail at dispatch.
    """
    resolution = fixes.resolve_verb(verb, registry=registry)
    if not resolution["resolved"]:
        return _refuse(
            code=code,
            reason_code=fixes.VERB_NOT_IN_REGISTRY,
            evidence={**evidence, "verb": verb, "verb_resolution": resolution},
            ko=f"이 수정에 필요한 편집 동사 '{verb}'이(가) 현재 도구 레지스트리에 없습니다"
            f"({resolution['why']}). 존재하지 않는 동사를 제안하지 않고 거부합니다.",
            en=f"The edit verb '{verb}' this fix needs is not in the live tool registry "
            f"({resolution['why']}). Refused rather than proposing a verb that does not exist.",
            ja=f"この修正に必要な編集動詞 '{verb}' が現在のツールレジストリにありません"
            f"({resolution['why']})。存在しない動詞を提案せず拒否します。",
        )
    ok, error = fixes.validate_params(verb, params, registry=registry)
    if not ok:
        return _refuse(
            code=code,
            reason_code=fixes.PARAMS_REJECTED_BY_SCHEMA,
            evidence={**evidence, "verb": verb, "rejected_params": dict(params), "error": error},
            ko=f"'{verb}'의 실제 파라미터 스키마가 이 제안값을 거부했습니다: {error}. "
            "승인 카드로 넘기지 않고 거부합니다.",
            en=f"'{verb}''s own live params schema rejected this payload: {error}. Refused "
            "rather than sending it to an approval card.",
            ja=f"'{verb}' の実際のパラメータスキーマがこの提案値を拒否しました: {error}。"
            "承認カードに渡さず拒否します。",
        )
    return fixes.FixProposal(
        key=code,
        key_kind=KEY_GATE_FINDING,
        outcome=fixes.PROPOSAL,
        scope=scope,
        verb=verb,
        params=params,
        evidence=evidence,
        rationale_ko=ko,
        rationale_en=en,
        rationale_ja=ja,
        confidence=confidence,
        reason_code=None,
        assumptions=assumptions,
    )


def _no_fix(
    mapping: GenesisFixMapping,
    finding: Any,
    *,
    suffixes: tuple[str, str, str] = ("", "", ""),
    extra_evidence: Mapping[str, Any] | None = None,
) -> fixes.FixProposal:
    """The fixed-reason response for a ``NOT_AUTOMATABLE`` entry with no
    per-call builder — and the shared base a ``NOT_AUTOMATABLE`` builder
    (V3's) appends its measured clause to via ``suffixes``/``extra_evidence``
    rather than restating this construction.

    Where the finding's own ``context`` names a ``cause`` (V3's census-sourced
    findings all do — ``services.genesis.connectivity``'s own vocabulary, e.g.
    ``one_way_dead_end_unfixable``), it is quoted verbatim rather than
    paraphrased, the same "attach, don't restate" convention
    ``fixes._no_automated_fix`` uses for U3's arming-drift text.
    """
    context = _finding_field(finding, "context", {}) or {}
    cause = context.get("cause") if isinstance(context, Mapping) else None
    suffix_ko = suffix_en = suffix_ja = ""
    if cause:
        suffix_ko = f" 이 사례가 명명한 원인: {cause!r}."
        suffix_en = f" The cause this instance names: {cause!r}."
        suffix_ja = f" この事例が明示する原因: {cause!r}。"
    evidence = {
        "code": mapping.code,
        "check_id": mapping.check_id,
        "reason_code": mapping.reason_code,
        "finding_subject": _finding_field(finding, "subject"),
        "finding_context": dict(context) if isinstance(context, Mapping) else context,
        **dict(extra_evidence or {}),
    }
    return fixes.FixProposal(
        key=mapping.code,
        key_kind=KEY_GATE_FINDING,
        outcome=fixes.NO_AUTOMATED_FIX,
        scope=fixes.NOT_APPLICABLE,
        verb=None,
        params=None,
        evidence=evidence,
        rationale_ko=mapping.reason_ko + suffix_ko + suffixes[0],
        rationale_en=mapping.reason_en + suffix_en + suffixes[1],
        rationale_ja=mapping.reason_ja + suffix_ja + suffixes[2],
        confidence=fixes.CONFIDENCE_NONE,
        reason_code=mapping.reason_code,
    )


# ---------------------------------------------------------------------------
# Builder (V3): connectivity -- NEVER a proposal, and never a different tier.
# Unit #36: the refusal now STATES, measured from the job's own source .osm,
# whether the source itself shares the gap (the world ends there) or declares
# the very connection the network lost (the gap is the pipeline's). The
# evidence is derived by ``genesis_jobs.derive_v3_source_testimony`` and
# threaded through ``targets['source_testimony']`` /
# ``targets['source_testimony_provenance']``; this builder only folds it in.
# ---------------------------------------------------------------------------


#: Unit #36: the measured reason a V3 source-testimony derivation yielded no
#: testimony, localized. Keys mirror
#: ``genesis_jobs.derive_v3_source_testimony``'s own ``reason_code`` values
#: (that function's docstring defines each); values are ``str.format``
#: templates over ``way_id``/``node_id`` so the measured ids appear verbatim.
#: An unknown code falls back to quoting the code itself so a new reason can
#: never silently vanish from the rationale -- the same discipline
#: :data:`_V7_DERIVATION_REASONS` established.
_V3_TESTIMONY_REASONS: dict[str, tuple[str, str, str]] = {
    "no_draft_census": (
        " 측정 결과: 이 작업의 census(*.genesis.json)를 읽을 수 없어 발견 지점을 원본 OSM으로 "
        "되짚지 못했습니다.",
        " Measured: this job's census (*.genesis.json) could not be read, so the finding's"
        " location could not be traced back to the source OSM.",
        " 測定結果: このジョブのcensus(*.genesis.json)を読めず、検出地点を元のOSMまで遡れません"
        "でした。",
    ),
    "no_draft_record": (
        " 측정 결과: 이 발견을 원본 OSM의 way로 되짚을 수 없습니다(주소 지정 가능한 링크 id가 "
        "없거나 census에 해당 링크 기록이 없습니다).",
        " Measured: this finding could not be mapped back to a source way (no addressable"
        " link id, or no census record for it).",
        " 測定結果: この検出を元のOSMのwayまで遡れません(アドレス指定可能なリンクidがないか、"
        "censusに該当リンクの記録がありません)。",
    ),
    "source_artifact_missing": (
        " 측정 결과: 이 작업의 원본 .osm을 읽을 수 없어 소스 자신의 선언을 확인하지 못했습니다.",
        " Measured: this job's source .osm is absent or unreadable, so the source's own"
        " declaration could not be consulted.",
        " 測定結果: このジョブの元の.osmを読めず、ソース自身の宣言を確認できませんでした。",
    ),
    "way_not_in_source": (
        " 측정 결과: census는 이 링크를 way {way_id}로 지목하지만 원본 OSM에는 그 id의 주행 "
        "가능한 way가 없습니다.",
        " Measured: the census names way {way_id} for this link, but the source OSM holds"
        " no drivable way with that id.",
        " 測定結果: censusはこのリンクを way {way_id} と示していますが、元のOSMにはそのidの"
        "走行可能なwayがありません。",
    ),
    "node_not_in_source": (
        " 측정 결과: 노드 {node_id}는 원본 OSM의 어떤 주행 가능한 way에도 실려 있지 않습니다.",
        " Measured: node {node_id} is on no drivable way in the source OSM.",
        " 測定結果: ノード {node_id} は元のOSMのどの走行可能なwayにも載っていません。",
    ),
    "source_shares_the_gap": (
        " 원본 OSM 실측 결과: 노드 {node_id}에서 떠날 수 있는 주행 가능한 way가 소스에 없습니다 "
        "— 소스 자체가 같은 끊김을 담고 있으며, 도로는 소스가 선언한 대로 여기서 끝납니다.",
        " Measured against the job's own source OSM: no drivable way departs node {node_id}"
        " there — the source itself shares the gap; the road ends where the source declares"
        " it ends.",
        " 元のOSMの測定結果: ノード {node_id} から離れられる走行可能なwayがソースにありません "
        "— ソース自体が同じ断絶を含んでおり、道路はソースが宣言したとおりここで終わっています。",
    ),
}


def _v3_testimony_clause(testimony: Any, provenance: Any) -> tuple[str, str, str]:
    """The localized measured sentence for a V3 source-testimony derivation
    -- the affirmative testimony when the source declares the connection, the
    named measured reason when it does not, or three empty strings when no
    derivation record was supplied at all (a caller with no service layer,
    e.g. the bare-registry tests)."""
    if isinstance(testimony, Mapping) and testimony.get("connected_way_ids"):
        ways_text = ", ".join(str(w) for w in testimony["connected_way_ids"])
        node_id = testimony.get("node_id")
        return (
            f" 원본 OSM 실측 결과: 소스는 이 지점에서 way {ways_text}이(가) 노드 {node_id}를 "
            "공유하며 그 노드에서 떠날 수 있다고 선언합니다 — 이 끊김은 세계가 아니라 "
            "파이프라인의 것입니다.",
            f" Measured against the job's own source OSM: the source declares ways {ways_text} "
            f"share node {node_id} here and can be departed on — the gap is the pipeline's, "
            "not the world's.",
            f" 元のOSMの測定結果: ソースはこの地点で way {ways_text} がノード {node_id} を共有し、"
            "そのノードから離れられると宣言しています — この断絶は世界ではなくパイプライン側の"
            "ものです。",
        )
    if not isinstance(provenance, Mapping):
        return "", "", ""
    reason_code = provenance.get("reason_code")
    if not reason_code:
        return "", "", ""
    clause = _V3_TESTIMONY_REASONS.get(str(reason_code))
    if clause is None:
        fallback = f" (source testimony: {reason_code})"
        return fallback, fallback, fallback
    fields = {"way_id": provenance.get("way_id"), "node_id": provenance.get("node_id")}
    return tuple(text.format(**fields) for text in clause)  # type: ignore[return-value]


def _build_v3_connectivity_no_fix(
    *,
    code: str,
    finding: Any = None,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """Every V3 code -> always :data:`fixes.NO_AUTOMATED_FIX` under
    :data:`CONNECTIVITY_IS_NOT_INVENTED` -- exactly ``_no_fix``'s own answer
    (cause quoted verbatim and all), plus, when the caller derived it, the
    SOURCE'S OWN TESTIMONY: ``targets['source_testimony']`` (the source
    declares the connection -- the gap is the pipeline's) or the derivation's
    named measured reason (``targets['source_testimony_provenance']`` -- the
    source shares the gap, or why it could not be asked). The tier NEVER
    changes with the answer: whether a registered verb may one day close a
    pipeline-lost gap is a re-tier decision this catalogue does not make from
    evidence alone.
    """
    mapping = _REGISTRY[code]
    caller_targets = targets or {}
    testimony = caller_targets.get("source_testimony")
    provenance = caller_targets.get("source_testimony_provenance")
    extra: dict[str, Any] = {}
    if isinstance(testimony, Mapping):
        extra["source_testimony"] = dict(testimony)
    if isinstance(provenance, Mapping):
        extra["source_testimony_provenance"] = dict(provenance)
    return _no_fix(
        mapping,
        finding,
        suffixes=_v3_testimony_clause(testimony, provenance),
        extra_evidence=extra,
    )


# ---------------------------------------------------------------------------
# Builder (V3, unit #35): the ONE V3 code that can become a TREATMENT.
# ``v3_silent_departure_point`` -> ``add_connector``, but ONLY when the
# connection FACT has a named source. The doctrine paragraph in this module's
# own docstring is the contract; this builder is its implementation.
# ---------------------------------------------------------------------------

#: V3's shared doctrine sentence -- module level (rather than local to
#: :func:`_register_catalogue`) since unit #35, because the treatable code's
#: own builder composes its refusal out of the SAME words its six siblings
#: carry, and two copies of a doctrine sentence is one copy too many.
_V3_DOCTRINE = (
    " 연결성은 발명하지 않습니다(로드맵 원칙) — 현재 등록된 편집 동사 중 어느 것도 도로를 "
    "지어내지 않고 이 구멍을 메우지 못합니다. 승인 루프가 검토할 사람의 몫입니다.",
    " Connectivity is not invented (roadmap doctrine) -- no registered edit verb closes this "
    "hole in the road network without fabricating the road that would close it. This is a "
    "human reviewer's call, via the approval loop.",
    " 連結性は発明しません(ロードマップの原則) -- 現在登録されている編集動詞のいずれも、"
    "その穴を塞ぐ道路を作り出さずにこの穴を塞ぐことはできません。承認ループを通じて人が"
    "判断すべき事項です。",
)

#: ``v3_silent_departure_point``'s own label, hoisted for the same reason.
_V3_SILENT_LABEL = (
    "링크 끝에서 어떤 커넥터도 떠나지 않는데, 그 끝이 모델 경계가 아닙니다.",
    "A link end that no connector leaves, where that end is not the model boundary.",
    "リンク端からどのコネクタも出発しないのに、その端がモデル境界ではありません。",
)

#: Unit #35: the measured reason a connector-plan derivation yielded nothing,
#: localized. Keys mirror ``genesis_jobs.derive_v3_connector_plan``'s own
#: ``reason_code`` values (that function's docstring defines each); values are
#: ``str.format`` templates over the provenance fields, so the measured ids and
#: distances appear verbatim. ``no_named_source`` is deliberately ABSENT here:
#: it is the bare refusal unit #36 already writes, and adding a clause for it
#: would restate what the doctrine sentence already says. An unknown code falls
#: back to quoting the code itself, the same discipline
#: :data:`_V7_DERIVATION_REASONS` and :data:`_V3_TESTIMONY_REASONS` established.
_V3_CONNECTOR_REASONS: dict[str, tuple[str, str, str]] = {
    "no_draft_census": (
        " 측정 결과: 이 작업의 census(*.genesis.json)를 읽을 수 없어 연결로를 구성할 두 링크 끝을 "
        "확인하지 못했습니다.",
        " Measured: this job's census (*.genesis.json) could not be read, so the two link ends a"
        " connector would be built from could not be consulted.",
        " 測定結果: このジョブのcensus(*.genesis.json)を読めず、コネクタを構成する2つのリンク端を"
        "確認できませんでした。",
    ),
    "no_draft_record": (
        " 측정 결과: 이 발견을 census의 링크 기록으로 되짚을 수 없어 출발단을 특정하지 못했습니다.",
        " Measured: this finding could not be mapped to a census links[] record, so its departure"
        " end could not be addressed.",
        " 測定結果: この検出をcensusのリンク記録まで遡れず、出発端を特定できませんでした。",
    ),
    "no_draft_link_departs_the_node": (
        " 측정 결과: 소스는 연결을 증언하지만, 그 way를 실은 링크 중 노드 {node_id}에서 시작하는 "
        "것이 이 네트워크의 census에 없습니다 — 이어붙일 링크 끝 자체가 모델에 없으므로 커넥터를 "
        "만들 수 없습니다(더 넓은 영역으로 재생성하는 것이 답입니다).",
        " Measured: the source testifies the connection, but this network's census holds no link"
        " carrying one of those ways that BEGINS at node {node_id} -- there is no link end to"
        " connect to, so no connector can be built (a regeneration over a larger extract is the"
        " answer).",
        " 測定結果: ソースは接続を証言していますが、そのwayを担うリンクのうちノード {node_id} から"
        "始まるものがこのネットワークのcensusにありません — 接続先のリンク端自体がモデルに存在せず、"
        "コネクタを作成できません(より広い範囲での再生成が答えです)。",
    ),
    "span_exceeds_node_scale": (
        " 측정 결과: 두 링크 끝 사이가 {span_m} m로, 이 생성기 자신의 노드 규모({max_span_m} m)를 "
        "넘습니다 — 그 간격을 잇는 것은 교차로 이동류가 아니라 도로를 지어내는 일입니다.",
        " Measured: the two link ends are {span_m} m apart, past this generator's own node scale"
        " ({max_span_m} m) -- bridging that gap would be fabricating road, not building a"
        " junction movement.",
        " 測定結果: 2つのリンク端の間隔は {span_m} m で、この生成器自身のノード規模"
        "({max_span_m} m)を超えます — その間隔を繋ぐことは交差点の移動ではなく道路を作り出す"
        "ことになります。",
    ),
    "attested_link_not_in_census": (
        " 확인된 대상 링크 {to_link}가 이 네트워크의 census에 없습니다.",
        " The attested target link {to_link} is not a link this network's census declares.",
        " 確認された対象リンク {to_link} はこのネットワークのcensusに存在しません。",
    ),
    "attested_link_is_the_finding_link": (
        " 확인된 대상 링크가 발견 자신의 링크입니다 — 링크를 자기 자신에 이을 수는 없습니다.",
        " The attested target link IS the finding's own link -- a connector cannot join a link to"
        " itself.",
        " 確認された対象リンクは検出自身のリンクです — リンクを自分自身に接続することはできません。",
    ),
    "attestation_without_target": (
        " 운영자 확인에 대상 링크가 지정되지 않았습니다 — 도로가 이어진다면 어디로 이어지는지를 "
        "지정해야 합니다.",
        " The attestation names no target link -- attesting that the road continues means naming"
        " WHERE it continues to.",
        " 運営者確認に対象リンクが指定されていません — 道路が続くのであれば、どこへ続くのかを"
        "指定する必要があります。",
    ),
    "attestation_without_basis": (
        " 운영자 확인에 근거 문장이 없습니다 — 근거 없는 확인은 클릭일 뿐 책임지는 주장이 아닙니다.",
        " The attestation carries no basis sentence -- an attestation without a stated basis is a"
        " click, not a claim somebody is accountable for.",
        " 運営者確認に根拠の記述がありません — 根拠のない確認はクリックであって、責任を伴う主張では"
        "ありません。",
    ),
    "attestation_without_attribution": (
        " 운영자 확인에 확인자 또는 시각이 없습니다 — 귀속되지 않은 확인은 기록할 수 없습니다.",
        " The attestation carries no attester and/or no timestamp -- an unattributed attestation"
        " cannot be recorded as one.",
        " 運営者確認に確認者または時刻がありません — 帰属のない確認は記録できません。",
    ),
    "degenerate_link_geometry": (
        " 측정 결과: 관련 링크의 census 기록이 점 2개 미만이라 이을 끝이 없습니다.",
        " Measured: the census record for one of the links has fewer than 2 points, so there is"
        " no end to connect.",
        " 測定結果: 関連リンクのcensus記録が2点未満のため、接続する端がありません。",
    ),
    "lane_mapping_not_expressible": (
        " 측정 결과: 이 생성기 자신의 차로 대응 규칙이 이 이동류에 대해 차로 쌍을 산출하지 "
        "못했습니다.",
        " Measured: this generator's own lane-pairing rule produced no lane pair for this"
        " movement.",
        " 測定結果: この生成器自身の車線対応規則がこの移動に対する車線ペアを算出できませんでした。",
    ),
}


def _v3_connector_clause(provenance: Any) -> tuple[str, str, str]:
    """The localized measured-reason sentence for a failed connector-plan
    derivation, or three empty strings when there is nothing to speak for (no
    derivation record, or the ``no_named_source`` case the doctrine sentence
    already covers)."""
    if not isinstance(provenance, Mapping):
        return "", "", ""
    reason_code = provenance.get("reason_code")
    if not reason_code or reason_code == "no_named_source":
        return "", "", ""
    clause = _V3_CONNECTOR_REASONS.get(str(reason_code))
    if clause is None:
        fallback = f" (connector derivation: {reason_code})"
        return fallback, fallback, fallback
    fields = {
        "node_id": provenance.get("node_id"),
        "to_link": provenance.get("to_link"),
        "span_m": provenance.get("span_m"),
        "max_span_m": provenance.get("max_span_m"),
        "link_id": provenance.get("link_id"),
    }
    return tuple(text.format(**fields) for text in clause)  # type: ignore[return-value]


def _v3_attestation_invitation(candidates: Any) -> tuple[str, str, str]:
    """The ask-first QUESTION a world-gap refusal carries when the model does
    hold link ends a connector could reach: the road may really continue, and
    the only thing that can say so is a human who knows the site. Three empty
    strings when there is nothing to attest TO — inviting an attestation with
    no possible target would be an empty question."""
    if not isinstance(candidates, list) or not candidates:
        return "", "", ""
    return (
        f" 실제 도로가 여기서 이어집니까? 이어진다면 운영자가 대상 링크를 지정하고 근거를 적어 "
        f"확인해 주십시오(도달 가능한 링크 끝 {len(candidates)}개). 확인 없이는 제안하지 않습니다 "
        "— 확인된 연결은 확인자와 시각이 함께 기록됩니다.",
        f" Does the real road continue here? If it does, an operator can attest it by naming the "
        f"target link and stating the basis ({len(candidates)} reachable link end(s) in this "
        "model). No attestation, no proposal -- and an attested connection is recorded with who "
        "attested it and when.",
        f" 実際の道路はここで続いていますか? 続いているのであれば、運営者が対象リンクを指定し根拠を"
        f"記して確認してください(到達可能なリンク端 {len(candidates)} 件)。確認がなければ提案は"
        "しません — 確認された接続は確認者と時刻とともに記録されます。",
    )


def _build_v3_silent_departure_connector(
    *,
    code: str,
    finding: Any = None,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """``v3_silent_departure_point`` -> ``add_connector``, but ONLY on a
    connection somebody NAMED.

    Two proposal branches, one refusal, and no fourth shape:

    * ``targets['connector_params']`` derived on the SOURCE's testimony
      (``targets['connector_derivation']['basis'] == 'source_testimony'``) ->
      a proposal at :data:`CONFIDENCE_SOURCE_TESTIMONY`, its rationale quoting
      the testified way and node ids verbatim, its evidence carrying the FULL
      testimony and the full derivation provenance;
    * derived on a HUMAN's attestation (``basis == 'human_attestation'``) -> a
      proposal at :data:`CONFIDENCE_HUMAN_ATTESTATION` with
      :data:`ASSUME_ATTESTED_NOT_MEASURED`, its rationale carrying "운영자
      확인" with the attester's name, the timestamp and their basis sentence
      verbatim, so the attribution travels with the reason wherever it is
      rendered;
    * anything else -> the refusal, which is the pre-#35 refusal exactly
      (label + doctrine + the census's own cause quoted verbatim + unit #36's
      measured testimony clause) plus, when there was a derivation to speak
      for, its own measured reason, plus the ask-first question when the model
      holds link ends an attestation could name.

    BOTH proposals carry :data:`ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED`. The
    params are never invented here — this builder does not construct a single
    coordinate, lane index or position; ``genesis_jobs.derive_v3_connector_plan``
    derives every field from the two link ends the census already declares,
    and ``genesis_jobs.create_genesis_fix_job`` re-derives them at apply time
    and refuses a submitted payload that differs.
    """
    mapping = _REGISTRY[code]
    caller_targets = targets or {}
    testimony = caller_targets.get("source_testimony")
    testimony_provenance = caller_targets.get("source_testimony_provenance")
    plan = caller_targets.get("connector_params")
    # Every derivation record is read through this: a caller may hand any
    # object in, and a rationale that crashes on a malformed one would take
    # the whole fixes listing down with it.
    plan_provenance = _as_mapping(caller_targets.get("connector_derivation"))
    attestation = caller_targets.get("human_attestation")
    candidates = caller_targets.get("attestation_candidates")

    context = _finding_field(finding, "context", {}) or {}
    cause = context.get("cause") if isinstance(context, Mapping) else None
    evidence: dict[str, Any] = {
        "code": code,
        "check_id": mapping.check_id,
        "finding_subject": _finding_field(finding, "subject"),
        "finding_context": dict(context) if isinstance(context, Mapping) else context,
    }
    if isinstance(testimony, Mapping):
        evidence["source_testimony"] = dict(testimony)
    if isinstance(testimony_provenance, Mapping):
        evidence["source_testimony_provenance"] = dict(testimony_provenance)
    if plan_provenance:
        evidence["connector_derivation"] = dict(plan_provenance)
    if isinstance(attestation, Mapping):
        evidence["human_attestation"] = dict(attestation)

    basis = plan_provenance.get("basis")
    # The census's own cause, quoted verbatim -- on the PROPOSAL too, not only
    # on the refusal. Measured on the real Daejeon jobs (2026-08-15): a
    # testified proposal can land on a junction whose census cause is
    # ``junction_where_every_movement_is_a_u_turn``, and an approver deciding
    # whether to build that movement needs to read the generator's own reason
    # for declining it, in the same sentence as the proposal.
    cause_ko = f" 이 사례가 명명한 원인: {cause!r}." if cause else ""
    cause_en = f" The cause this instance names: {cause!r}." if cause else ""
    cause_ja = f" この事例が明示する原因: {cause!r}。" if cause else ""

    if isinstance(plan, Mapping) and plan:
        params = dict(plan)
        span = plan_provenance.get("span_m")
        turn = plan_provenance.get("turn")
        constructed_ko = (
            f"연결로의 기하·차로 대응은 이 시스템이 기존 두 링크 끝에서 기계적으로 구성했습니다"
            f"(링크 {params['from_link']} 끝 {params['from_pos_m']} m → 링크 {params['to_link']} "
            f"시작 0 m, 간격 {span} m, 이동류 {turn}, 차로 {params['from_lanes']}→"
            f"{params['to_lanes']} — 이 생성기 자신의 차로 대응 규칙). 연결의 사실은 아래 근거가 "
            "말하고, 형상은 시스템이 구성합니다."
        )
        constructed_en = (
            f"The connector's geometry and lane mapping are CONSTRUCTED mechanically from the two "
            f"existing link ends (link {params['from_link']} at {params['from_pos_m']} m -> link "
            f"{params['to_link']} at 0 m, {span} m apart, {turn} movement, lanes "
            f"{params['from_lanes']}->{params['to_lanes']} by this generator's own lane-pairing "
            "rule). The named source below states the CONNECTION; the system constructs its shape."
        )
        constructed_ja = (
            f"コネクタの幾何と車線対応は、既存の2つのリンク端からこのシステムが機械的に構成しました"
            f"(リンク {params['from_link']} の {params['from_pos_m']} m → リンク "
            f"{params['to_link']} の 0 m、間隔 {span} m、移動 {turn}、車線 {params['from_lanes']}→"
            f"{params['to_lanes']} — この生成器自身の車線対応規則)。接続という事実は下記の根拠が"
            "述べ、形状はシステムが構成します。"
        )
        constructed_ko += cause_ko
        constructed_en += cause_en
        constructed_ja += cause_ja

        if basis == "source_testimony" and isinstance(testimony, Mapping):
            ways_text = ", ".join(str(w) for w in testimony.get("connected_way_ids") or [])
            node_id = testimony.get("node_id")
            way_id = testimony.get("way_id")
            return _emit(
                code=code,
                verb="add_connector",
                params=params,
                evidence=evidence,
                ko=f"소스가 증언합니다 — 이 작업 자신의 원본 OSM에서 way {ways_text}이(가) 노드 "
                f"{node_id}를 공유하며 그 노드에서 떠날 수 있다고 선언합니다(발견 링크의 way는 "
                f"{way_id}). 즉 이 끊김은 세계가 아니라 파이프라인의 것이며, 연결의 사실은 "
                f"소스가 이미 선언한 것입니다 — 이 시스템이 지어낸 것이 아닙니다. "
                + constructed_ko,
                en=f"The source testifies -- this job's own source OSM declares that way(s) "
                f"{ways_text} share node {node_id} and can be departed on there (the finding's "
                f"own link carries way {way_id}). The gap is the pipeline's, not the world's, and "
                "the CONNECTION is a fact the source already declared -- nothing here invented "
                "it. " + constructed_en,
                ja=f"ソースが証言します — このジョブ自身の元のOSMが way {ways_text} がノード "
                f"{node_id} を共有し、そのノードから離れられると宣言しています(検出リンクのwayは "
                f"{way_id})。この断絶は世界ではなくパイプライン側のものであり、接続という事実は"
                "ソースが既に宣言したものです — このシステムが作り出したものではありません。"
                + constructed_ja,
                confidence=CONFIDENCE_SOURCE_TESTIMONY,
                assumptions=(ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED,),
                registry=registry,
            )

        if basis == "human_attestation" and isinstance(attestation, Mapping):
            who = attestation.get("attested_by")
            when = attestation.get("attested_at")
            basis_text = attestation.get("basis")
            return _emit(
                code=code,
                verb="add_connector",
                params=params,
                evidence=evidence,
                ko=f"운영자 확인 — 원본 OSM은 이 지점에서 이어지는 도로를 선언하지 않지만, "
                f"{who}이(가) {when}에 실제 도로가 링크 {params['to_link']}로 이어진다고 "
                f"확인했습니다. 근거(확인자 진술, 원문): \"{basis_text}\". 이 연결은 측정된 "
                "사실이 아니라 사람이 책임지는 진술이며, 확인자와 시각이 이 수정과 함께 "
                "기록됩니다. " + constructed_ko,
                en=f"OPERATOR-ATTESTED -- the source OSM declares no continuation here, but "
                f"{who} attested at {when} that the real road continues to link "
                f"{params['to_link']}. Basis (the attester's own words, verbatim): "
                f"\"{basis_text}\". This connection is a person's accountable claim, not a "
                "measurement, and who attested it and when are recorded with this fix. "
                + constructed_en,
                ja=f"運営者確認 — 元のOSMはこの地点での継続を宣言していませんが、{who} が {when} "
                f"に実際の道路がリンク {params['to_link']} へ続くと確認しました。根拠(確認者の"
                f"原文): 「{basis_text}」。この接続は測定された事実ではなく人が責任を負う主張で"
                "あり、確認者と時刻がこの修正とともに記録されます。" + constructed_ja,
                confidence=CONFIDENCE_HUMAN_ATTESTATION,
                assumptions=(
                    ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED,
                    ASSUME_ATTESTED_NOT_MEASURED,
                ),
                registry=registry,
            )

        # A plan with no recognised basis is not a plan this catalogue will
        # sign: the whole unit rests on the connection having a NAMED source,
        # so an unnamed one is refused rather than defaulted to either branch.
        return _refuse(
            code=code,
            reason_code=CONNECTIVITY_IS_NOT_INVENTED,
            evidence=evidence,
            ko="연결로 파라미터가 유도되었지만 그 연결을 누가/무엇이 증언했는지가 기록되어 있지 "
            "않습니다 — 근거 없는 연결은 제안하지 않습니다.",
            en="Connector params were derived, but the derivation records no named source for the "
            "CONNECTION itself -- a connection nobody named is not proposed.",
            ja="コネクタのパラメータは導出されましたが、その接続を誰が/何が証言したのかが記録されて"
            "いません — 根拠のない接続は提案しません。",
        )

    # ---- the refusal: exactly what unit #36 left, plus what was measured ----
    incomplete = str(plan_provenance.get("reason_code") or "").startswith("attestation_")
    testimony_clause = _v3_testimony_clause(testimony, testimony_provenance)
    connector_clause = _v3_connector_clause(plan_provenance)
    invitation = ("", "", "") if incomplete else _v3_attestation_invitation(candidates)
    if isinstance(candidates, list) and candidates and not incomplete:
        evidence["attestation_invitation"] = {
            "from_link": plan_provenance.get("link_id"),
            "node_id": _as_mapping(testimony).get("node_id")
            or _as_mapping(testimony_provenance).get("node_id"),
            "candidates": list(candidates),
        }
    return _refuse(
        code=code,
        reason_code=ATTESTATION_INCOMPLETE if incomplete else CONNECTIVITY_IS_NOT_INVENTED,
        evidence=evidence,
        ko=_V3_SILENT_LABEL[0] + _V3_DOCTRINE[0] + cause_ko + testimony_clause[0]
        + connector_clause[0] + invitation[0],
        en=_V3_SILENT_LABEL[1] + _V3_DOCTRINE[1] + cause_en + testimony_clause[1]
        + connector_clause[1] + invitation[1],
        ja=_V3_SILENT_LABEL[2] + _V3_DOCTRINE[2] + cause_ja + testimony_clause[2]
        + connector_clause[2] + invitation[2],
    )


# ---------------------------------------------------------------------------
# Builder (V4a): a connector maps more lanes than its link has
# ---------------------------------------------------------------------------


def _build_v4_more_mapped_lanes(
    *,
    code: str,
    finding: Any = None,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """``v4_more_mapped_lanes_than_link_has`` -> widen the link to match what
    is already connected to it.

    The correct target lane count is not guessed: it is the finding's OWN
    ``context['mapped_lanes']``, which by construction (``gate._check_v4``
    only emits this finding when ``mapped_lanes > link_lanes``) is exactly the
    count the link needs to stop contradicting its own connector.
    """
    context = _finding_field(finding, "context", {}) or {}
    link_id = context.get("link")
    mapped_lanes = context.get("mapped_lanes")
    link_lanes = context.get("link_lanes")
    evidence: dict[str, Any] = {
        "connector": _finding_field(finding, "subject"),
        "side": context.get("side"),
        "link": link_id,
        "mapped_lanes": mapped_lanes,
        "link_lanes": link_lanes,
    }
    if not link_id or mapped_lanes is None:
        return _refuse(
            code=code,
            reason_code=fixes.EVIDENCE_INSUFFICIENT,
            evidence=evidence,
            ko="연결로가 지목하는 링크 또는 매핑된 차로 수가 발견에 실려 있지 않아 목표 차로 수를 "
            "산출할 수 없습니다.",
            en="The finding does not name the target link or the mapped lane count, so no "
            "target lane count can be derived.",
            ja="発見に対象リンクまたはマッピングされた車線数が記載されておらず、目標車線数を"
            "算出できません。",
        )
    return _emit(
        code=code,
        verb="set_link_lane_count",
        params={"link_id": str(link_id), "lane_count": int(mapped_lanes)},
        evidence=evidence,
        ko=f"연결로 {evidence['connector']}가 링크 {link_id}의 {evidence['side']}측에 "
        f"{mapped_lanes}개 차로를 매핑하지만 그 링크는 {link_lanes}개 차로뿐입니다. 링크의 "
        f"차로 수를 {link_lanes} → {mapped_lanes}로 넓혀 연결로가 이미 매핑한 차로 수와 "
        "일치시킬 것을 제안합니다. 목표값은 발견 자체가 실은 매핑된 차로 수이며, 추정이 아닙니다.",
        en=f"Connector {evidence['connector']} maps {mapped_lanes} lane(s) on link {link_id}'s "
        f"{evidence['side']} side, but that link only has {link_lanes}. Proposing to widen the "
        f"link's lane count {link_lanes} -> {mapped_lanes} so it matches what the connector "
        "already maps. The target value is the finding's own mapped-lane count, not an estimate.",
        ja=f"コネクタ {evidence['connector']} はリンク {link_id} の {evidence['side']} 側に "
        f"{mapped_lanes} 車線をマッピングしていますが、そのリンクは {link_lanes} 車線しかありません。"
        f"リンクの車線数を {link_lanes} → {mapped_lanes} に広げ、コネクタが既にマッピングしている"
        "車線数と一致させることを提案します。目標値は発見自体が示すマッピング済み車線数であり、"
        "推定ではありません。",
        confidence=fixes.CONFIDENCE_ARITHMETIC,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Builder (V4b): a lane drop with no runway -- NEVER a real proposal. Which
# NO_AUTOMATED_FIX reason applies depends on evidence, so this is a builder
# on a NOT_AUTOMATABLE entry rather than a fixed reason. Unit #39: when the
# caller derived a boundary attribution
# (``genesis_jobs.derive_v4_boundary_attribution``, threaded through
# ``targets['boundary_attribution']`` / ``targets['boundary_attribution_provenance']``),
# the refusal's rationale STATES the measured answer -- the corridor ends at
# the requested model boundary (and what the source declares beyond it), or
# it ends in the model's interior -- instead of "there is no evidence".
# ---------------------------------------------------------------------------


#: Unit #39: the measured reason a V4 boundary-attribution derivation yielded
#: no attribution, localized. Keys mirror
#: ``genesis_jobs.derive_v4_boundary_attribution``'s own ``reason_code``
#: values (that function's docstring defines each); values are ``str.format``
#: templates over the provenance fields so the measured numbers appear
#: verbatim. An unknown code falls back to quoting the code itself, the same
#: discipline :data:`_V7_DERIVATION_REASONS` / :data:`_V3_TESTIMONY_REASONS`
#: established.
_V4_ATTRIBUTION_REASONS: dict[str, tuple[str, str, str]] = {
    "judge_measurement_missing": (
        " 측정 결과: 발견 자체에 사용할 수 있는 required_m/available_m 쌍이 없어 부족분을 "
        "귀속시킬 기준이 없습니다.",
        " Measured: the finding itself carries no usable required_m/available_m pair, so"
        " there is no deficit to attribute.",
        " 測定結果: 検出自体に使用可能な required_m/available_m の組がなく、不足分を帰属させる"
        "基準がありません。",
    ),
    "no_draft_census": (
        " 측정 결과: 이 작업의 census(*.genesis.json)를 읽을 수 없어 여유 회랑을 되짚지 "
        "못했습니다.",
        " Measured: this job's census (*.genesis.json) could not be read, so the runway"
        " corridor could not be re-traced.",
        " 測定結果: このジョブのcensus(*.genesis.json)を読めず、余裕回廊を遡れませんでした。",
    ),
    "no_draft_record": (
        " 측정 결과: 이 발견을 census의 링크 기록으로 되짚을 수 없어 회랑의 끝을 특정하지 "
        "못했습니다.",
        " Measured: this finding could not be mapped to a census links[] record, so the"
        " corridor's end could not be located.",
        " 測定結果: この検出をcensusのリンク記録まで遡れず、回廊の端を特定できませんでした。",
    ),
    "trace_budget_exhausted": (
        " 측정 결과: 상류 재추적이 판정기의 탐색 예산을 소진해 회랑의 끝을 확정하지 못했습니다.",
        " Measured: the upstream re-trace exhausted the judge's own search budget before the"
        " corridor's end could be established.",
        " 測定結果: 上流の再トレースが判定器の探索予算を使い切り、回廊の端を確定できませんでした。",
    ),
    "trace_disagrees_with_judge": (
        " 측정 결과: census 재추적은 상류 여유를 {traced_available_m} m로 측정했지만 판정 기록은 "
        "{available_m} m입니다 — 두 측정이 어긋나므로 어느 쪽도 신뢰하지 않고 귀속을 도출하지 "
        "않습니다.",
        " Measured: the census re-trace found {traced_available_m} m of upstream runway where"
        " the judge's verdict recorded {available_m} m -- the two measurements disagree, so"
        " neither is trusted and no attribution is derived.",
        " 測定結果: censusの再トレースは上流余裕を {traced_available_m} m と測定しましたが、判定"
        "記録は {available_m} m です — 2つの測定が食い違うため、どちらも信頼せず帰属を導出しません。",
    ),
    "trim_way_mismatch": (
        " 측정 결과: 회랑 끝 노드의 절단 기록이 회랑의 way와 다른 way를 지목합니다 — census 증거가 "
        "서로 어긋나므로 판단하지 않고 보고만 합니다.",
        " Measured: the trim record at the corridor's end names a different way than the"
        " corridor carries -- conflicting census evidence is reported, not adjudicated.",
        " 測定結果: 回廊端ノードの切断記録が回廊のwayとは別のwayを指しています — census証拠が"
        "食い違うため、判断せず報告のみ行います。",
    ),
    "way_not_in_source": (
        " 측정 결과: census는 회랑 끝을 way {way_id}로 지목하지만 원본 OSM에는 그 id의 주행 "
        "가능한 way가 없습니다.",
        " Measured: the census names way {way_id} at the corridor's end, but the source OSM"
        " holds no drivable way with that id.",
        " 測定結果: censusは回廊端を way {way_id} と示していますが、元のOSMにはそのidの走行可能な"
        "wayがありません。",
    ),
    "source_artifact_missing": (
        " 측정 결과: 회랑은 모델 경계 노드에서 끝나지만, 원본 .osm을 읽을 수 없고 census에 절단 "
        "측정 기록도 없어 경계 너머의 도로를 진술할 수 없습니다.",
        " Measured: the corridor ends at a model-boundary node, but the job's source .osm is"
        " absent or unreadable and the census records no trim measurement there, so nothing"
        " can be stated about the road beyond.",
        " 測定結果: 回廊はモデル境界ノードで終わりますが、元の.osmを読めず、censusに切断測定の"
        "記録もないため、境界の先の道路について述べることができません。",
    ),
}


def _v4_attribution_reason_clause(provenance: Any, judge_available_m: Any) -> tuple[str, str, str]:
    """The localized measured sentence for a V4 attribution derivation that
    yielded NO attribution -- or three empty strings when no derivation
    record was supplied at all (a caller with no service layer, e.g. the
    bare-registry tests, keeps the exact pre-#39 answer)."""
    if not isinstance(provenance, Mapping):
        return "", "", ""
    reason_code = provenance.get("reason_code")
    if not reason_code:
        return "", "", ""
    clause = _V4_ATTRIBUTION_REASONS.get(str(reason_code))
    if clause is None:
        fallback = f" (boundary attribution: {reason_code})"
        return fallback, fallback, fallback
    fields = {
        "way_id": provenance.get("way_id"),
        "traced_available_m": provenance.get("traced_available_m"),
        "available_m": judge_available_m,
    }
    return tuple(text.format(**fields) for text in clause)  # type: ignore[return-value]


def _v4_boundary_clause(attribution: Mapping[str, Any]) -> tuple[str, str, str]:
    """The localized measured sentence for a BOUNDARY-ATTRIBUTED runway
    shortage -- which of its three shapes depends on what the source could
    testify about the road beyond the boundary node: it continues with at
    least the deficit, the extract itself is cut at the same lasso and
    cannot testify past its own edge, or the source declares less road than
    the deficit needs (the boundary explains only part of the shortage)."""
    continuation = attribution.get("continuation_evidence")
    continuation = continuation if isinstance(continuation, Mapping) else {}
    deficit = attribution.get("deficit_m")
    node = attribution.get("boundary_node")
    way = attribution.get("way_id")
    length = continuation.get("source_length_available_m")
    if continuation.get("meets_deficit"):
        return (
            f" 측정됨: 부족분 {deficit} m의 여유 회랑은 이 생성기가 요청된 모델 경계 위에 만든 "
            f"노드 {node}에서 끝나며, 원본 OSM은 way {way}의 도로가 그 너머로 {length} m 이상 "
            "계속됨을 증언합니다 — 부족은 도로가 아니라 모델 경계의 것입니다.",
            f" Measured: the {deficit} m-deficit runway corridor ends at node {node}, a node"
            f" this generator itself placed ON the requested model boundary, and the source OSM"
            f" testifies that way {way}'s road continues at least {length} m beyond it -- the"
            " shortage belongs to the model boundary, not to the road.",
            f" 測定結果: 不足分 {deficit} m の余裕回廊は、この生成器が要求されたモデル境界上に"
            f"作ったノード {node} で終わり、元のOSMは way {way} の道路がその先へ {length} m 以上"
            "続くことを証言しています — 不足は道路ではなくモデル境界のものです。",
        )
    if continuation.get("continuation_beyond_extract"):
        return (
            f" 측정됨: 부족분 {deficit} m의 여유 회랑은 요청된 모델 경계의 노드 {node}에서 끝나고, "
            f"원본은 way {way}의 도로를 경계 너머로 {length} m까지 선언한 뒤 추출 자체가 같은 "
            "경계에서 잘려 그 너머를 증언할 수 없습니다 — 여기서 끝나는 것은 도로가 아니라 "
            "모델(추출)의 가장자리입니다.",
            f" Measured: the {deficit} m-deficit runway corridor ends at node {node} on the"
            f" requested model boundary; the source declares way {way}'s road for {length} m"
            " beyond it and then the extract itself is cut at that same boundary -- an extract"
            " cut at the lasso cannot testify past its own edge, so what ends here is the"
            " MODEL's edge, not the road.",
            f" 測定結果: 不足分 {deficit} m の余裕回廊は要求されたモデル境界のノード {node} で"
            f"終わり、ソースは way {way} の道路を境界の先 {length} m まで宣言した後、抽出自体が"
            "同じ境界で切られておりその先を証言できません — ここで終わっているのは道路ではなく"
            "モデル(抽出)の縁です。",
        )
    return (
        f" 측정됨: 여유 회랑은 요청된 모델 경계의 노드 {node}에서 끝나지만, 원본이 경계 너머로 "
        f"선언하는 도로는 {length} m뿐입니다(부족분 {deficit} m 미달) — 경계가 부족의 일부만 "
        "설명합니다.",
        f" Measured: the runway corridor ends at node {node} on the requested model boundary,"
        f" but the source declares only {length} m of road beyond it (short of the {deficit} m"
        " deficit) -- the boundary explains only part of the shortage.",
        f" 測定結果: 余裕回廊は要求されたモデル境界のノード {node} で終わりますが、ソースが境界の"
        f"先に宣言する道路は {length} m のみです(不足分 {deficit} m に満たない) — 境界は不足の"
        "一部しか説明しません。",
    )


def _v4_interior_clause(attribution: Mapping[str, Any]) -> tuple[str, str, str]:
    """The localized measured sentence for an INTERIOR runway shortage --
    the honest opposite of :func:`_v4_boundary_clause`, keyed on whether the
    source could confirm the dead end, declares upstream ways the draft
    lost, or could not be asked."""
    node = attribution.get("terminus_node")
    confirms = attribution.get("source_confirms")
    if confirms is True:
        return (
            f" 측정됨: 여유 회랑은 모델 경계가 아니라 내부(노드 {node})에서 끝나며, 원본 OSM도 그 "
            "지점에 도착하는 주행 가능한 way가 없음을 확인합니다 — 부족은 모델 경계의 것이 아니라 "
            "도로 자체의 것입니다.",
            f" Measured: the runway corridor ends in the model's INTERIOR (node {node}), not at"
            " the requested boundary, and the source OSM confirms no drivable way arrives there"
            " either -- the deficit is the road's, not the model boundary's.",
            f" 測定結果: 余裕回廊はモデル境界ではなく内部(ノード {node})で終わり、元のOSMもその"
            "地点に到着する走行可能なwayがないことを確認しています — 不足はモデル境界のものでは"
            "なく、道路自体のものです。",
        )
    if confirms is False:
        ways_text = ", ".join(str(w) for w in attribution.get("source_upstream_way_ids") or [])
        return (
            f" 측정됨: 여유 회랑은 모델 경계가 아니라 내부(노드 {node})에서 끝납니다. 원본은 way "
            f"{ways_text}이(가) 그 지점에 도달함을 선언하므로 초안이 잃어버린 상류 연속이 있을 수 "
            "있지만, 어느 쪽이든 부족은 요청된 경계의 것이 아닙니다.",
            f" Measured: the runway corridor ends in the model's INTERIOR (node {node}), not at"
            f" the requested boundary. The source declares way(s) {ways_text} arriving there, so"
            " the draft may have lost an upstream continuation -- either way, the shortage is"
            " not the requested boundary's.",
            f" 測定結果: 余裕回廊はモデル境界ではなく内部(ノード {node})で終わります。ソースは "
            f"way {ways_text} がその地点に到達することを宣言しているため、ドラフトが失った上流の"
            "連続がある可能性がありますが、いずれにせよ不足は要求された境界のものではありません。",
        )
    return (
        f" 측정됨: 여유 회랑은 모델 경계 표식이 없는 내부 노드 {node}에서 끝납니다. 원본을 그 "
        "지점에 대해 물을 수 없어 도로가 실제로 이어지는지는 확인하지 못했습니다.",
        f" Measured: the runway corridor ends at interior node {node}, which carries no"
        " model-boundary marker in the census. The source could not be asked about that point,"
        " so whether the road really continues there is unconfirmed.",
        f" 測定結果: 余裕回廊は、censusにモデル境界の標識がない内部ノード {node} で終わります。"
        "その地点についてソースに問い合わせられず、道路が実際に続くかは未確認です。",
    )


# ---------------------------------------------------------------------------
# Unit #40 -- the TREATMENT clause on a boundary-attributed refusal. The
# refusal itself does not move: no registered verb lengthens a runway, and this
# artifact is still not editable into a compliant one. What the clause adds is
# that a WIDER REGION covering the measured shortage was derived
# (``genesis_jobs.derive_v4_boundary_extension``) and is waiting on the region
# page's own extract button -- a NEW GENERATION, never unit #38's same-source
# regeneration, because the stored source is clipped at this very boundary.
# ---------------------------------------------------------------------------


def _v4_extension_clause(extension: Mapping[str, Any]) -> tuple[str, str, str]:
    """The localized sentence for a DERIVED region extension: how far the
    proposed finger reaches, how much area it adds, and that a human still has
    to look at it on the map and press the button.

    **The extrapolation is named in the SENTENCE, not only in the payload.**
    ``length_m`` is what the finger asks for; ``measured_source_m`` is how much
    of it the source itself declared. On the real link-90 case those differ by
    50 m -- the extract is clipped at the very boundary being extended, so the
    source could only declare 127 of the 177 metres and the rest is drawn on
    the last MEASURED bearing. A reader given only the 177 would read a
    measurement where there is an extrapolation, so the split rides in the
    prose the same way it rides in ``corridor_patch``, together with the one
    distinction that makes an extrapolated finger honest at all: widening the
    lasso widens the EXTRACT REQUEST, and is never a claim that the road is
    there. (The card in ``app/genesis/jobs/[id]/page.tsx`` and the region
    page's arrival banner state the same split for the same reason -- this is
    the localized answer-builder's copy of it, for every reader who meets the
    rationale rather than the card.)
    """
    provenance = extension.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    patch = extension.get("corridor_patch")
    patch = patch if isinstance(patch, Mapping) else {}
    deficit = provenance.get("deficit_m")
    margin = provenance.get("margin_m")
    length = patch.get("length_m")
    added = extension.get("added_area_km2")
    measured = patch.get("measured_source_m")
    try:
        extrapolated = float(patch.get("extrapolated_m") or 0.0)
    except (TypeError, ValueError):  # pragma: no cover - a payload we did not build
        extrapolated = 0.0
    extrapolated_ko = extrapolated_en = extrapolated_ja = ""
    if extrapolated > 0.0:
        extrapolated_ko = (
            f" 이 중 원본이 도로라고 선언한 길이는 {measured} m뿐이고, 나머지 "
            f"{patch.get('extrapolated_m')} m는 마지막으로 측정된 방위로 그은 것입니다 — 도로를 "
            "지어낸 것이 아니라 그만큼 더 넓게 추출을 요청하는 것이므로, 지도에서 확인해 주세요."
        )
        extrapolated_en = (
            f" Only {measured} m of that is road the source itself declared; the remaining"
            f" {patch.get('extrapolated_m')} m is drawn on the last MEASURED bearing. It widens"
            " the EXTRACT REQUEST by that much and claims nothing about the road being there,"
            " so check it on the map."
        )
        extrapolated_ja = (
            f" そのうちソースが道路と宣言しているのは {measured} m のみで、残り "
            f"{patch.get('extrapolated_m')} m は最後に測定された方位へ引いたものです — 道路を"
            "作り出したのではなく、その分だけ広く抽出を要求するものですので、地図でご確認ください。"
        )
    return (
        f" 치료: 요청하셨던 영역에 이 회랑 방향으로 {length} m(부족분 {deficit} m + 여유 "
        f"{margin} m)의 손가락을 덧댄 영역을 도출했습니다 — 넓이 +{added} km²."
        + extrapolated_ko
        + " 그리려던 모양은 그대로 두고 이 한 변에만 끼워 넣은 것이며, 지도에서 직접 확인·수정한 "
        "뒤 추출 버튼을 누르셔야 새 생성이 시작됩니다.",
        f" Treatment: a widened version of the region you requested has been derived -- a"
        f" {length} m finger along this corridor (the {deficit} m deficit plus a {margin} m"
        f" margin), adding {added} km²."
        + extrapolated_en
        + " It is your own shape with the finger spliced into the one edge the corridor"
        " crosses; nothing is fetched until you have looked at it on the map and pressed the"
        " extract button yourself.",
        f" 治療: 要求された領域に、この回廊方向へ {length} m(不足分 {deficit} m + 余裕 "
        f"{margin} m)の指を継ぎ足した領域を導出しました — 面積 +{added} km²。"
        + extrapolated_ja
        + "描かれた形はそのままに、この一辺だけに差し込んだものです。地図で確認・修正のうえ、"
        "抽出ボタンを押されるまで何も取得しません。",
    )


#: Unit #40: the measured reason no region extension could be derived for a
#: boundary-attributed shortage, localized. Keys mirror
#: ``genesis_jobs.derive_v4_boundary_extension``'s own ``reason_code`` values;
#: an unknown code falls back to quoting the code itself, the same discipline
#: :data:`_V4_ATTRIBUTION_REASONS` established.
_V4_EXTENSION_REASONS: dict[str, tuple[str, str, str]] = {
    "requested_region_not_a_polygon": (
        " 이 작업의 census에는 요청 폴리곤이 기록되어 있지 않아(사각형 영역), 넓힌 라쏘를 "
        "제안하지 않습니다.",
        " No widened lasso is proposed: this job's census records no requested POLYGON (it was"
        " a rectangle region), and drawing one would show corners nobody drew.",
        " このジョブのcensusには要求ポリゴンが記録されておらず(矩形領域)、広げたラッソは提案"
        "しません。",
    ),
    "no_measured_continuation_direction": (
        " 원본이 경계 너머의 노드를 하나도 선언하지 않아 어느 방향으로 넓힐지 측정할 수 없어 "
        "영역을 제안하지 않습니다.",
        " No region is proposed: the source declares no node beyond the boundary on this"
        " corridor, so the direction to widen in could only be guessed.",
        " ソースが境界の先のノードを一つも宣言しておらず、どの方向へ広げるかを測定できないため"
        "領域を提案しません。",
    ),
    "extension_exceeds_region_cap": (
        " 넓힌 영역이 추출 화면과 동일한 넓이 한계를 넘어 제안하지 않습니다.",
        " No region is proposed: the widened shape is over the very area cap the extract screen"
        " itself enforces.",
        " 広げた領域が抽出画面と同じ面積上限を超えるため提案しません。",
    ),
    "extension_polygon_self_intersects": (
        " 넓힌 고리가 스스로 교차해, 깨진 모양을 지도에 내보내는 대신 제안을 거부합니다.",
        " No region is proposed: the spliced ring crosses itself, and a broken shape is refused"
        " rather than handed to the map.",
        " 広げた輪が自己交差するため、壊れた形を地図に出す代わりに提案を拒否します。",
    ),
}


def _v4_extension_refusal_clause(refusal: Any) -> tuple[str, str, str]:
    """The localized measured sentence for an extension derivation that
    produced nothing -- or three empty strings when no derivation record was
    supplied at all (a caller with no service layer keeps the exact pre-#40
    answer)."""
    if not isinstance(refusal, Mapping):
        return "", "", ""
    reason_code = refusal.get("reason_code")
    if not reason_code:
        return "", "", ""
    clause = _V4_EXTENSION_REASONS.get(str(reason_code))
    if clause is None:
        fallback = f" (boundary extension: {reason_code})"
        return fallback, fallback, fallback
    return clause


def _build_v4_lane_drop_without_runway(
    *,
    code: str,
    finding: Any = None,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """``v4_lane_drop_without_runway`` -> always NO_AUTOMATED_FIX; WHICH
    reason depends on whether the shortfall traces to the model's own
    requested boundary.

    **Unit #39: the attribution is now MEASURED when the caller derived it.**
    ``genesis_jobs.derive_v4_boundary_attribution`` re-traces the judge's own
    upstream runway walk over the draft census, classifies the corridor's
    upstream terminus against the census's own boundary markers, and asks the
    job's stored source ``.osm`` what it declares beyond -- threaded through
    ``targets['boundary_attribution']`` /
    ``targets['boundary_attribution_provenance']``, which this builder only
    folds in (the same division of labour as V3's source testimony). A
    boundary-attributed corridor refuses under
    :data:`RUNWAY_ENDS_AT_REQUESTED_BOUNDARY` with the measured clause; an
    interior one refuses under :data:`NO_TOOL_WITHOUT_FABRICATION` with the
    honest opposite; a derivation that could not answer appends its NAMED
    measured reason to the pre-#39 "no evidence" refusal. The tier NEVER
    changes with the answer.

    **Unit #40: the boundary-attributed refusal now names its TREATMENT.**
    ``genesis_jobs.derive_v4_boundary_extension`` turns that attribution into
    the user's own requested lasso with a finger spliced in at the edge the
    corridor crosses, long enough to cover the measured deficit plus a stated
    margin -- threaded through ``targets['boundary_extension']`` (or
    ``targets['boundary_extension_refusal']`` with its named reason), which
    this builder only folds in, exactly as it folds the attribution. The tier
    still does not move, and deliberately: the treatment is a NEW GENERATION
    from a wider extract that a human asks for on the region screen, not an
    edit this catalogue could apply to this artifact.

    Without a derivation record, the pre-#39 behaviour is kept exactly:
    which upstream link the shortfall traces back to is NETWORK-WALK
    knowledge (``gate._upstream_runway``'s own BFS) that this module does not
    recompute — the same "this module does not guess network knowledge it was
    not handed" rule ``fixes._build_routing_proposal`` states for
    ``decision_movements``. The caller supplies the traced link via
    ``targets['upstream_boundary_link']`` when it has walked the graph;
    absent that, the finding's own subject link (the lane-dropping link
    itself) is used, which still correctly classifies the common case where
    the dropping link IS the boundary-adjacent one. The census consulted for
    that legacy path is the SAME one V3's silent-departure clause reads
    (``metadata['connectivity']['dangling_approaches']``, ``gate._check_v3``)
    — an entry there whose ``cause`` is
    ``services.genesis.connectivity.DANGLING_MODEL_BOUNDARY`` means this
    generator itself synthesized a node at the requested extract's edge and
    the road correctly ends there. (That census records only DEPARTURE ends,
    which is exactly why it could never see a runway corridor's upstream
    ENTRY end -- the measured gap unit #39 closes.)
    """
    context = _finding_field(finding, "context", {}) or {}
    subject = _finding_field(finding, "subject")
    link_id = _link_id_from_subject(subject)
    trace_link = str((targets or {}).get("upstream_boundary_link") or link_id)

    dangling: list[Any] = []
    if isinstance(census, Mapping):
        connectivity = census.get("connectivity")
        if isinstance(connectivity, Mapping):
            raw = connectivity.get("dangling_approaches")
            if isinstance(raw, list):
                dangling = raw

    boundary_entry = None
    for entry in dangling:
        if not isinstance(entry, Mapping):
            continue
        entry_link = str(entry.get("link_id") if entry.get("link_id") is not None else entry.get("link") or "")
        if entry_link == trace_link and entry.get("cause") == DANGLING_MODEL_BOUNDARY:
            boundary_entry = dict(entry)
            break

    evidence: dict[str, Any] = {
        "link": link_id,
        "traced_link": trace_link,
        "traced_link_supplied": "upstream_boundary_link" in (targets or {}),
        "required_m": context.get("required_m"),
        "available_m": context.get("available_m"),
        "dropped_lanes": context.get("dropped_lanes"),
        "boundary_entry": boundary_entry,
    }

    # ---- unit #39: a derived attribution record trumps the legacy census
    # shortcut -- it is the deeper measurement of the same question. Shared
    # sentence components so the legacy path below stays byte-identical.
    intro_ko = (
        f"링크 {link_id}의 차로 감소에 {evidence['required_m']} m의 차로변경 여유가 필요하지만 "
        f"{evidence['available_m']} m밖에 없습니다."
    )
    intro_en = (
        f"Link {link_id}'s lane drop needs {evidence['required_m']} m of lane-change runway "
        f"and only has {evidence['available_m']} m."
    )
    intro_ja = (
        f"リンク {link_id} の車線減少には {evidence['required_m']} m の車線変更余裕が必要ですが "
        f"{evidence['available_m']} m しかありません。"
    )
    no_evidence_ko = " 이 부족이 요청된 모델 경계에서 비롯된 것이라는 증거는 없습니다."
    no_evidence_en = " There is no evidence this shortfall traces to the requested model boundary."
    no_evidence_ja = "この不足が要求されたモデル境界に起因するという証拠はありません。"
    core_ko = (
        " 현재 등록된 도구 중 링크의 lnChgDist를 늘리거나 상류 도로를 연장하는 "
        "동사는 없으며, set_link_geometry는 기존 폴리라인 점을 재배치할 뿐 새 점(즉 새 길이)을 "
        "추가하지 못합니다. 존재하지 않는 도로를 만들어내지 않고는 이 부족을 메울 수 없으므로 "
        "자동 수정을 제안하지 않습니다."
    )
    core_en = (
        " No registered verb lengthens a link's lnChgDist runway "
        "or extends the upstream road: set_link_geometry only REPOSITIONS a link's EXISTING "
        "polyline points, it cannot add new ones (new length). Closing this gap without "
        "fabricating road that is not there is not something this system can do, so no automated "
        "fix is proposed."
    )
    core_ja = (
        "現在登録されているツールにリンクのlnChgDistを延ばす、または上流の"
        "道路を延長する動詞はなく、set_link_geometryは既存のポリライン点を再配置するのみで新しい"
        "点(すなわち新しい長さ)を追加できません。存在しない道路を作り出さずにこの不足を埋めること"
        "はできないため、自動修正は提案しません。"
    )

    attribution = _as_mapping((targets or {}).get("boundary_attribution"))
    attribution_prov = _as_mapping((targets or {}).get("boundary_attribution_provenance"))
    # Unit #40's treatment, folded in the same way and with the same rule: it
    # rides on the evidence and adds a sentence, and it NEVER moves the tier.
    # Widening the region is a new generation a human asks for on another
    # screen, not a fix applied to this artifact.
    extension = _as_mapping((targets or {}).get("boundary_extension"))
    extension_refusal = _as_mapping((targets or {}).get("boundary_extension_refusal"))
    if extension:
        evidence["boundary_extension"] = dict(extension)
    if extension_refusal:
        evidence["boundary_extension_refusal"] = dict(extension_refusal)
    if extension:
        treat_ko, treat_en, treat_ja = _v4_extension_clause(extension)
    else:
        treat_ko, treat_en, treat_ja = _v4_extension_refusal_clause(extension_refusal)
    if attribution or attribution_prov:
        if attribution:
            evidence["boundary_attribution"] = dict(attribution)
        if attribution_prov:
            evidence["boundary_attribution_provenance"] = dict(attribution_prov)
        if attribution.get("status") == "boundary_attributed":
            clause_ko, clause_en, clause_ja = _v4_boundary_clause(attribution)
            return _refuse(
                code=code,
                reason_code=RUNWAY_ENDS_AT_REQUESTED_BOUNDARY,
                evidence=evidence,
                ko=intro_ko + clause_ko
                + " 그 지점을 넘겨 도로를 늘리는 것은 사용자가 요청하지 않은 구역으로 네트워크를 "
                "확장하는 것과 같으므로 자동 수정을 제안하지 않습니다." + treat_ko,
                en=intro_en + clause_en
                + " Lengthening it past that point would extend the network beyond the requested "
                "area, so no automated fix is proposed." + treat_en,
                ja=intro_ja + clause_ja
                + " その地点を越えて延長することは要求されていない区域までネットワークを拡張する"
                "ことになるため、自動修正は提案しません。" + treat_ja,
            )
        if attribution.get("status") == "interior":
            clause_ko, clause_en, clause_ja = _v4_interior_clause(attribution)
            return _refuse(
                code=code,
                reason_code=NO_TOOL_WITHOUT_FABRICATION,
                evidence=evidence,
                ko=intro_ko + clause_ko + core_ko,
                en=intro_en + clause_en + core_en,
                ja=intro_ja + clause_ja + core_ja,
            )
        # A derivation was attempted and could not answer: the pre-#39 "no
        # evidence" refusal, now carrying the derivation's NAMED measured
        # reason instead of silence about why nothing was measured.
        clause_ko, clause_en, clause_ja = _v4_attribution_reason_clause(
            attribution_prov, evidence["available_m"]
        )
        return _refuse(
            code=code,
            reason_code=NO_TOOL_WITHOUT_FABRICATION,
            evidence=evidence,
            ko=intro_ko + no_evidence_ko + core_ko + clause_ko,
            en=intro_en + no_evidence_en + core_en + clause_en,
            ja=intro_ja + no_evidence_ja + core_ja + clause_ja,
        )

    if boundary_entry is not None:
        return _refuse(
            code=code,
            reason_code=RUNWAY_ENDS_AT_REQUESTED_BOUNDARY,
            evidence=evidence,
            ko=f"링크 {link_id}의 차로 감소(전방 {evidence['dropped_lanes']}차로)에 필요한 "
            f"{evidence['required_m']} m의 차로변경 여유가 {evidence['available_m']} m밖에 "
            f"없습니다. 그러나 그 여유가 끊기는 지점(링크 {trace_link})은 이 생성기가 요청받은 "
            "모델 경계에서 직접 합성한 노드입니다 — 도로는 사용자가 요청한 경계에서 정확히 "
            "끝나는 것이며, 결함이 아닙니다. 그 지점을 넘겨 도로를 늘리는 것은 사용자가 요청하지 "
            "않은 구역으로 네트워크를 확장하는 것과 같으므로 자동 수정을 제안하지 않습니다.",
            en=f"Link {link_id}'s lane drop (lanes {evidence['dropped_lanes']}) needs "
            f"{evidence['required_m']} m of lane-change runway and only has "
            f"{evidence['available_m']} m. But the point where that runway runs out (link "
            f"{trace_link}) is a node THIS GENERATOR ITSELF synthesized on the requested model "
            "boundary — the road correctly ends where the user's requested area ends; this is "
            "not a defect. Lengthening it past that point would extend the network beyond the "
            "requested area, so no automated fix is proposed.",
            ja=f"リンク {link_id} の車線減少(前方 {evidence['dropped_lanes']} 車線)には "
            f"{evidence['required_m']} m の車線変更余裕が必要ですが、{evidence['available_m']} m "
            f"しかありません。しかし、その余裕が途切れる地点(リンク {trace_link})は、この生成器"
            "自身が要求されたモデル境界上に合成したノードです — 道路はユーザーが要求した境界で"
            "正しく終わっており、これは欠陥ではありません。その地点を越えて延長することは要求され"
            "ていない区域までネットワークを拡張することになるため、自動修正は提案しません。",
        )

    return _refuse(
        code=code,
        reason_code=NO_TOOL_WITHOUT_FABRICATION,
        evidence=evidence,
        ko=intro_ko + no_evidence_ko + core_ko,
        en=intro_en + no_evidence_en + core_en,
        ja=intro_ja + no_evidence_ja + core_ja,
    )


# ---------------------------------------------------------------------------
# Builder (V6): missing/zero vehicle-input volume -- D4's exact shape, via
# fixes.entrance_measurement. NEVER reimplement the join.
# ---------------------------------------------------------------------------


def _build_v6_entrance_volume(
    *,
    code: str,
    finding: Any = None,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """``v6_input_without_volume`` / ``v6_input_volume_is_zero`` -> unit D4,
    unchanged.

    ``targets`` carries the observed-counts context this module does not
    derive itself: ``targets['node']`` in ``fixes.node_evidence_from_real_counts``
    shape (or ``None`` if no observed-counts workbook covers this entrance),
    ``targets['link_no']`` the vehicle input's own link, and
    ``targets['proposed_volume']`` the demand ESTIMATE the caller has already
    derived (e.g. from a sibling entrance or a design-standard default) for
    the case where the entrance is NOT measured. This builder does not invent
    that number — only D4's gate and its labelling are this module's job.
    """
    input_no = _finding_field(finding, "subject")
    context = _finding_field(finding, "context", {}) or {}
    caller_targets = dict(targets or {})
    node = caller_targets.get("node")
    target: dict[str, Any] = {
        "input_no": caller_targets.get("input_no", input_no),
        "link_no": caller_targets.get("link_no", context.get("link")),
        "interval_no": caller_targets.get("interval_no", 1),
    }
    proposed_volume = caller_targets.get("proposed_volume")

    entrance = fixes.entrance_measurement(node, target)
    evidence: dict[str, Any] = {
        "input_no": target["input_no"],
        "code": code,
        "target": target,
        "entrance_measurement": entrance,
    }

    if entrance["status"] == fixes.ENTRANCE_MEASURED:
        row = entrance.get("observed_row") or {}
        counted = entrance.get("observed_on_input_link")
        return _refuse(
            code=code,
            reason_code=fixes.INPUT_VOLUME_IS_OBSERVED_DATA,
            evidence=evidence,
            ko=f"차량 입력 {target['input_no']}은(는) 볼륨이 비어 있거나 0이지만, 그 입력이 "
            f"먹이는 링크 {entrance['link_no']}는 실측이 이루어진 진입부입니다: 실측 이동류 "
            f"{row.get('movement_id')}이(가) 그 링크에서 진입하며 {counted}대로 계측되었습니다. "
            "실측된 진입 교통량은 보정의 목표값이지 이 시스템이 채워 넣을 손잡이가 아니므로, 이 "
            "빈 값을 자동으로 채우지 않고 거부합니다 — 그 값은 사람이 실측치를 확인해 직접 "
            "입력해야 합니다.",
            en=f"Vehicle input {target['input_no']}'s volume is missing or zero, but the link it "
            f"feeds ({entrance['link_no']}) is a MEASURED entrance: observed movement row "
            f"{row.get('movement_id')} enters from that link carrying {counted} counted vehicles. "
            "An observed entrance volume is the calibration TARGET, not a value this system fills "
            "in on its own — refused; a human should confirm the measured count and enter it "
            "directly.",
            ja=f"車両インプット {target['input_no']} のボリュームは空またはゼロですが、そのインプット"
            f"が供給するリンク({entrance['link_no']})は実測が行われた流入部です: 実測移動 "
            f"{row.get('movement_id')} がそのリンクから流入し {counted} 台として計測されています。"
            "実測された流入交通量はキャリブレーションの目標値であり、このシステムが自動的に埋める"
            "値ではないため拒否します — その値は人が実測値を確認して直接入力すべきです。",
        )

    if proposed_volume is None or target["input_no"] in (None, ""):
        return _refuse(
            code=code,
            reason_code=fixes.EVIDENCE_INSUFFICIENT,
            evidence=evidence,
            ko="이 진입부가 실측 지점인지 확인할 수 없었고(미검증), 채워 넣을 수요 추정치도 "
            "제공되지 않아 볼륨을 제안할 수 없습니다.",
            en="This entrance could not be verified as measured (unverified), and no demand "
            "estimate was supplied to fill it with, so no volume can be proposed.",
            ja="この流入部が実測地点かを確認できず(未検証)、埋めるべき需要推定値も提供されな"
            "かったため、ボリュームを提案できません。",
        )

    return _emit(
        code=code,
        verb="set_vehicle_input_volume",
        params={
            "input_no": str(target["input_no"]),
            "volume": float(proposed_volume),
            "interval_no": int(target.get("interval_no") or 1),
        },
        evidence={**evidence, "proposed_volume": float(proposed_volume)},
        ko=f"차량 입력 {target['input_no']}의 볼륨이 비어 있거나 0입니다. 이 진입부는 실측 지점인지 "
        f"확인하지 못했습니다({entrance.get('why')}). ※ 수요 추정(demand estimation): 제안된 "
        f"{proposed_volume} veh/h는 명시적 추정치입니다 — 실제 진입 교통량을 아시면 이 제안을 "
        "거절하고 그 값을 직접 입력해 주십시오.",
        en=f"Vehicle input {target['input_no']}'s volume is missing or zero. This entrance could "
        f"not be verified as measured ({entrance.get('why')}). NB DEMAND ESTIMATE: the proposed "
        f"{proposed_volume} veh/h is a named estimate — if you know the real entrance count, "
        "REJECT this and enter it directly.",
        ja=f"車両インプット {target['input_no']} のボリュームが空またはゼロです。この流入部は実測"
        f"地点かを確認できませんでした({entrance.get('why')})。※ 需要推定: 提案された "
        f"{proposed_volume} veh/h は明示的な推定値です — 実際の流入交通量をご存知であれば、この"
        "提案を拒否して直接入力してください。",
        confidence=fixes.CONFIDENCE_DEMAND_ESTIMATE,
        assumptions=(fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED,),
        scope=fixes.DEMAND_LEVEL,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Builder (V7): geometry findings -- AUTOMATABLE only when the caller
# supplies a corrected, census-derived polyline; otherwise NO_AUTOMATED_FIX.
# ---------------------------------------------------------------------------


#: Unit #34: the measured reason a V7 derivation yielded nothing, localized.
#: Keys mirror ``genesis_jobs.derive_v7_corrected_polyline``'s own
#: ``reason_code`` values (that function's docstring defines each); an
#: unknown code falls back to quoting the code itself so a new reason can
#: never silently vanish from the rationale.
_V7_DERIVATION_REASONS: dict[str, tuple[str, str, str]] = {
    "no_draft_census": (
        " 측정 결과: 이 작업의 census(*.genesis.json)를 읽을 수 없어 초안이 선언한 폴리라인을 확인하지 못했습니다.",
        " Measured: this job's census (*.genesis.json) could not be read, so the draft's own"
        " declared polyline could not be consulted.",
        " 測定結果: このジョブのcensus(*.genesis.json)を読めず、ドラフト自身が宣言したポリラインを確認できませんでした。",
    ),
    "no_draft_record": (
        " 측정 결과: 초안 census에 이 링크의 폴리라인 기록이 없습니다.",
        " Measured: the draft census declares no polyline record for this link.",
        " 測定結果: ドラフトのcensusにこのリンクのポリライン記録がありません。",
    ),
    "draft_record_unusable": (
        " 측정 결과: 초안 자신의 기록도 이 결함을 해소할 수 없는 형태입니다.",
        " Measured: the draft's own record could not clear this finding either.",
        " 測定結果: ドラフト自身の記録もこの欠陥を解消できない形状です。",
    ),
    "draft_shares_the_corruption": (
        " 측정 결과: 초안 자신의 기록에서도 이 링크들이 이미 같은 폴리라인을 공유합니다 — 손상이 "
        "초안 이전 단계에서 일어나 census로는 보정할 수 없습니다.",
        " Measured: the draft's OWN records already share one polyline across these links --"
        " the corruption happened upstream of the draft, so the census cannot correct it.",
        " 測定結果: ドラフト自身の記録でもこれらのリンクは既に同じポリラインを共有しています — "
        "破損はドラフトより上流で起きており、censusでは補正できません。",
    ),
    "emitted_geometry_unreadable": (
        " 측정 결과: 산출물의 현재 폴리라인을 읽을 수 없어 보정을 검증하지 못했습니다.",
        " Measured: the artifact's current polyline could not be read, so the correction"
        " could not be verified against it.",
        " 測定結果: 成果物の現在のポリラインを読めず、補正を検証できませんでした。",
    ),
    "point_count_differs": (
        " 측정 결과: 초안 기록과 산출물의 점 개수가 달라 set_link_geometry로는 적용할 수 없습니다"
        "(COM은 폴리라인 점을 추가/삭제할 수 없습니다).",
        " Measured: the draft record and the artifact carry different point counts, so"
        " set_link_geometry cannot apply it (COM cannot add or remove a polyline point).",
        " 測定結果: ドラフト記録と成果物の点数が異なるため、set_link_geometryでは適用できません"
        "(COMはポリライン点を追加・削除できません)。",
    ),
    "draft_equals_emitted": (
        " 측정 결과: 초안의 기록이 산출물이 이미 담고 있는 폴리라인과 동일합니다 — 다시 적용해도 "
        "아무것도 바뀌지 않으므로 수정이 아닙니다.",
        " Measured: the draft's record IS the polyline the artifact already carries --"
        " re-applying it would change nothing, which is not a fix.",
        " 測定結果: ドラフトの記録は成果物が既に持つポリラインと同一です — 再適用しても何も変わらず、"
        "修正にはなりません。",
    ),
}


def _v7_derivation_clause(provenance: Any) -> tuple[str, str, str]:
    """The localized measured-reason sentence for a failed V7 derivation, or
    three empty strings when there is no derivation record to speak for."""
    if not isinstance(provenance, Mapping):
        return "", "", ""
    reason_code = provenance.get("reason_code")
    if not reason_code:
        return "", "", ""
    clause = _V7_DERIVATION_REASONS.get(str(reason_code))
    if clause is None:
        fallback = f" (census derivation: {reason_code})"
        return fallback, fallback, fallback
    return clause


def _build_v7_geometry_fix(
    *,
    code: str,
    finding: Any = None,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """``v7_link_without_geometry`` / ``v7_zero_length_link`` /
    ``v7_link_below_generator_floor`` / ``v7_links_share_one_polyline`` ->
    ``set_link_geometry``, only when the caller supplies
    ``targets['corrected_points_xy']`` -- a polyline this module did not
    invent: the draft's own pre-corruption record for this link id, read from
    the census. Since unit #34 that caller EXISTS --
    ``services.genesis_jobs.derive_v7_corrected_polyline`` (via
    ``derive_fix_targets``) derives it from the job's own ``*.genesis.json``
    and threads its provenance through
    ``targets['corrected_points_provenance']``, which this builder records in
    the proposal's evidence (``census_derivation``) and, on a refusal, folds
    into the rationale as the measured reason.

    Without a derived correction there is no census-derivable one: this
    module does not invent coordinates to separate two overlapping links or
    to give a zero-length link a shape, so it refuses under
    :data:`NO_TOOL_WITHOUT_FABRICATION` rather than guess.
    """
    link_id = _link_id_from_subject(_finding_field(finding, "subject"))
    context = _finding_field(finding, "context", {}) or {}
    corrected = (targets or {}).get("corrected_points_xy")
    provenance = (targets or {}).get("corrected_points_provenance")
    evidence: dict[str, Any] = {
        "link": link_id,
        "finding_context": dict(context) if isinstance(context, Mapping) else context,
    }
    if isinstance(provenance, Mapping):
        evidence["census_derivation"] = dict(provenance)
    if not corrected or len(list(corrected)) < 2:
        measured_ko, measured_en, measured_ja = _v7_derivation_clause(provenance)
        return _refuse(
            code=code,
            reason_code=NO_TOOL_WITHOUT_FABRICATION,
            evidence=evidence,
            ko=f"링크 {link_id}의 기하 결함을 고치려면 새 폴리라인 점 좌표가 필요하지만, census에서 "
            "유도된 보정 좌표가 제공되지 않았습니다. 좌표를 추측해서 만들어내지 않고 거부합니다."
            + measured_ko,
            en=f"Repairing link {link_id}'s geometry finding needs new polyline point "
            "coordinates, and no census-derived correction was supplied. Refused rather than "
            "inventing coordinates." + measured_en,
            ja=f"リンク {link_id} の幾何学的な欠陥を修正するには新しいポリライン点座標が必要ですが、"
            "census由来の補正座標が提供されませんでした。座標を推測して作り出さずに拒否します。"
            + measured_ja,
        )
    points = [[float(x), float(y)] for x, y in corrected]
    return _emit(
        code=code,
        verb="set_link_geometry",
        params={"link_id": link_id, "points_xy": points},
        evidence={**evidence, "points_xy": points},
        ko=f"링크 {link_id}의 기하를 census가 기록한 보정 좌표({len(points)}점)로 재설정할 것을 "
        "제안합니다. 좌표는 이 모듈이 지어낸 것이 아니라 호출자가 census에서 읽어 전달한 값입니다.",
        en=f"Proposing to reset link {link_id}'s geometry to the {len(points)}-point correction "
        "the census recorded. The coordinates are not invented here — the caller read them out "
        "of the census and supplied them.",
        ja=f"リンク {link_id} の幾何をcensusが記録した補正座標({len(points)}点)にリセットする"
        "ことを提案します。座標はこのモジュールが作り出したものではなく、呼び出し側がcensusから"
        "読み取って渡した値です。",
        confidence=fixes.CONFIDENCE_ARITHMETIC,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Builder (V9): a stop line with no queue counter -- warn-tier, additive.
# ---------------------------------------------------------------------------


def _build_v9_queue_counter(
    *,
    code: str,
    finding: Any = None,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """``v9_stop_line_without_queue_counter`` -> ``add_queue_counter`` at the
    link's own end (the stop line, per the check's own definition: "a stop
    line is where a signal head is", and heads sit at a link's downstream
    end).

    The gate finding names only the link and its head count -- not the
    link's length, which ``add_queue_counter`` needs as ``pos_m``. The caller
    supplies it as ``targets['link_length_m']`` (read from the network the
    gate already parsed); this module does not invent a position.
    """
    link_id = _link_id_from_subject(_finding_field(finding, "subject"))
    context = _finding_field(finding, "context", {}) or {}
    link_length_m = (targets or {}).get("link_length_m")
    evidence: dict[str, Any] = {"link": link_id, "heads": context.get("heads")}
    if link_length_m is None:
        return _refuse(
            code=code,
            reason_code=fixes.EVIDENCE_INSUFFICIENT,
            evidence=evidence,
            outcome=fixes.REFUSAL,
            ko=f"링크 {link_id}에 대기열 카운터를 추가하려면 카운터 위치(pos_m)가 필요하지만 링크 "
            "길이가 제공되지 않아 위치를 정할 수 없습니다.",
            en=f"Adding a queue counter to link {link_id} needs a position (pos_m), and the "
            "link's length was not supplied, so a position cannot be set.",
            ja=f"リンク {link_id} に待ち行列カウンタを追加するには位置(pos_m)が必要ですが、リンク"
            "長が提供されなかったため位置を決められません。",
        )
    pos_m = round(float(link_length_m), 3)
    return _emit(
        code=code,
        verb="add_queue_counter",
        params={"link_no": link_id, "pos_m": pos_m, "name": f"V9 stop line {link_id}"},
        evidence={**evidence, "pos_m": pos_m},
        ko=f"링크 {link_id}에 정지선(신호 헤드 {evidence['heads']}개)이 있지만 대기열 카운터가 "
        f"없습니다. 링크 끝({pos_m} m)에 대기열 카운터 추가를 제안합니다 — 기존 네트워크에 "
        "카운터를 더할 뿐(additive) 다른 파라미터를 바꾸지 않는 warn 등급 수정입니다.",
        en=f"Link {link_id} has a stop line ({evidence['heads']} signal head(s)) but no queue "
        f"counter. Proposing to add one at the link's end ({pos_m} m) -- a warn-tier, additive "
        "fix that adds a counter without changing any other parameter.",
        ja=f"リンク {link_id} には停止線(信号ヘッド {evidence['heads']} 個)がありますが、待ち"
        f"行列カウンタがありません。リンクの終端({pos_m} m)にカウンタを追加することを提案します"
        " -- 他のパラメータを変更せずカウンタを追加するだけの、warn等級の追加的な修正です。",
        confidence=fixes.CONFIDENCE_ARITHMETIC,
        registry=registry,
    )


# ---------------------------------------------------------------------------
# The single addressable entry point
# ---------------------------------------------------------------------------


def propose_genesis_fix(
    code: str,
    *,
    finding: Any = None,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """One gate finding code in, one :class:`fixes.FixProposal` out.

    An unregistered code does not raise and does not return ``None``: it
    comes back as :data:`fixes.NO_AUTOMATED_FIX` / :data:`fixes.NO_MAPPING_REGISTERED`,
    mirroring ``fixes.propose_fix``'s own handling of an unmapped key.
    """
    mapping = _REGISTRY.get(code)
    if mapping is None:
        return _refuse(
            code=code,
            reason_code=fixes.NO_MAPPING_REGISTERED,
            evidence={"code": code},
            ko=f"'{code}'에 대해 등록된 수정 매핑이 없습니다 — 이 발견은 아직 자동 수정 대상이 "
            "아닙니다.",
            en=f"No fix mapping is registered for '{code}' — this finding is not yet something "
            "this system knows how to fix automatically.",
            ja=f"'{code}' に対して登録された修正マッピングがありません — この検出はまだ自動修正の"
            "対象ではありません。",
        )

    status, detail = effective_status(mapping, registry=registry)
    if status == fixes.VERB_UNREGISTERED:
        return _refuse(
            code=code,
            reason_code=fixes.VERB_NOT_IN_REGISTRY,
            evidence={"code": code, **detail},
            ko=f"'{code}'의 수정 동사 '{mapping.verb}'이(가) 현재 도구 레지스트리에 없습니다 — "
            "매핑이 실제 도구와 어긋났습니다.",
            en=f"'{code}''s fix verb '{mapping.verb}' is not in the live tool registry — the "
            "mapping has drifted from the tools.",
            ja=f"'{code}' の修正動詞 '{mapping.verb}' が現在のツールレジストリにありません — "
            "マッピングが実際のツールと乖離しています。",
        )

    if mapping.status == fixes.NOT_AUTOMATABLE:
        if mapping.builder is None:
            return _no_fix(mapping, finding)
        proposal = mapping.builder(
            code=mapping.code, finding=finding, census=census, targets=targets, registry=registry
        )
        assert proposal.outcome != fixes.PROPOSAL, (
            f"genesis_fixes: {mapping.code!r} is declared NOT_AUTOMATABLE but its builder "
            "emitted a PROPOSAL -- that is a catalogue bug, not a valid outcome"
        )
        return proposal

    assert mapping.builder is not None  # register_genesis_fix guarantees this
    return mapping.builder(
        code=mapping.code, finding=finding, census=census, targets=targets, registry=registry
    )


def propose_for_gate_finding(
    finding: Any,
    *,
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> fixes.FixProposal:
    """``propose_genesis_fix`` keyed off a gate finding's own ``code``."""
    code = str(_finding_field(finding, "code") or "")
    return propose_genesis_fix(code, finding=finding, census=census, targets=targets, registry=registry)


# ---------------------------------------------------------------------------
# Subject <-> params addressing (B5-2 stage-3 fix round, exploit #2). A
# caller must not be able to apply a verb whose params address a DIFFERENT
# object than the one the finding actually diagnosed (e.g. a v6 finding on
# vehicle input 1 answered with params naming input 2). Declared HERE,
# beside the registry entries themselves, so it cannot drift the way a
# second copy maintained by a caller (a router, a job runner) inevitably
# would -- ``test_genesis_fixes.py`` walks ``get_genesis_fix_registry()`` and
# fails loudly if any AUTOMATABLE verb is missing an entry.
# ---------------------------------------------------------------------------

#: verb -> the params FIELD NAME that addresses the object a fix touches.
#: Not derivable from a mapping's own ``param_fields`` (which names EVERY
#: field a verb takes, not which ONE of them is the addressing key), so it is
#: its own table rather than a slice of an existing one.
VERB_ADDRESSING_FIELD: dict[str, str] = {
    "set_link_lane_count": "link_id",
    "set_vehicle_input_volume": "input_no",
    "set_link_geometry": "link_id",
    "add_queue_counter": "link_no",
    # Unit #35. ``add_connector`` touches TWO links, and the one it must be
    # checked against is ``from_link``: that is the link the finding
    # diagnosed (its departure end is the fault). ``to_link`` is not an
    # addressing key at all -- it is part of the DERIVED payload, guarded by
    # ``genesis_jobs.create_genesis_fix_job``'s own
    # ``genesis_fix_connector_not_derived`` check rather than by this table.
    "add_connector": "from_link",
}


def expected_fix_target(code: str, finding: Any) -> str | None:
    """The object id THIS finding's own subject/context names as what a fix
    for it must address -- the vehicle input number for a V6 finding
    (``subject`` IS the input number), the link id for V4a/V7/V9 (parsed out
    of the ``"<id> (<name>)"`` subject the SAME way :func:`propose_genesis_fix`
    itself does, or read straight off V4a's own ``context['link']``).

    Read from the finding ITSELF, never guessed or supplied by a caller —
    this is precisely the value a submitted params payload is checked
    against, so it must be as trustworthy as the finding record is.

    ``None`` when ``code`` has no addressing convention at all (every
    NOT_AUTOMATABLE code, and any code this catalogue does not recognise) --
    callers must treat ``None`` as "nothing to cross-check", never as a
    licence to skip the check for a code that DOES have a convention.
    """
    if code in ("v6_input_without_volume", "v6_input_volume_is_zero"):
        subject = _finding_field(finding, "subject")
        return None if subject in (None, "") else str(subject)
    if code in (
        "v7_link_without_geometry",
        "v7_zero_length_link",
        "v7_link_below_generator_floor",
        "v7_links_share_one_polyline",
        "v9_stop_line_without_queue_counter",
    ):
        subject = _finding_field(finding, "subject")
        if subject in (None, ""):
            return None
        return _link_id_from_subject(str(subject))
    if code == "v4_more_mapped_lanes_than_link_has":
        context = _finding_field(finding, "context", {}) or {}
        link_id = context.get("link")
        return None if link_id is None else str(link_id)
    if code == "v3_silent_departure_point":
        # Unit #35. This finding's SUBJECT is the census's own link NAME
        # (``gate._check_v3`` writes ``entry['link']``, e.g. "도안동로 [FWD]
        # w45473814s1"), never an id -- so the addressable id comes from its
        # ``context['link_id']`` and from nowhere else. ``None`` when the
        # context does not carry one: the connector derivation then reaches no
        # plan either, so the fix is refused rather than aimed by guesswork.
        context = _finding_field(finding, "context", {}) or {}
        link_id = context.get("link_id") if isinstance(context, Mapping) else None
        return None if link_id in (None, "") else str(link_id)
    return None


# ---------------------------------------------------------------------------
# The catalogue itself
# ---------------------------------------------------------------------------


def _register_catalogue() -> None:
    """Idempotent (guarded by ``_registered``), called at import time,
    mirroring ``fixes._register_catalogue``'s own pattern."""

    # ==================== V1 -- load oracle ================================
    # Every V1 finding is a fact about whether/how a REAL Vissim opened this
    # file. "Fixing" any of them would mean editing that PROOF into existence
    # rather than the network -- there is nothing here for an edit verb to
    # reach at all.
    _v1_doctrine = (
        " V1은 실제 Vissim이 이 파일을 열었는지, 열고 나서도 파일이 담긴 내용과 일치하는지를 "
        "증명하기 위해 존재합니다. 이 결핍을 자동으로 '고친다'는 것은 네트워크가 아니라 그 증거 "
        "자체를 편집해 만들어내는 일이 되어 이 검사가 존재하는 이유를 무효화합니다.",
        " V1 exists to prove a REAL Vissim opened this file and that what it holds still matches "
        "it afterwards. An automated 'fix' here would mean editing that EVIDENCE into existence "
        "rather than the network, which defeats the reason this check exists.",
        " V1は実際のVissimがこのファイルを開いたこと、開いた後もファイルの内容と一致することを"
        "証明するために存在します。この欠落を自動的に「修正」することは、ネットワークではなく"
        "その証拠自体を編集して作り出すことになり、この検査が存在する理由を無効にします。",
    )
    for v1_code, v1_specific in (
        (
            "v1_load_refused",
            (
                "실제 Vissim이 이 파일을 로드하는 것 자체를 거부했습니다.",
                "A real Vissim itself refused to load this file.",
                "実際のVissimがこのファイルの読み込み自体を拒否しました。",
            ),
        ),
        (
            "v1_err_file_written",
            (
                "로드 과정에서 Vissim이 새 .err 파일을 남겼습니다.",
                "Vissim wrote a new .err file during the load.",
                "読み込み中にVissimが新しい.errファイルを残しました。",
            ),
        ),
        (
            "v1_link_count_mismatch",
            (
                "파일이 선언한 링크(일반+커넥터) 수와 실제 Vissim이 로드한 수가 다릅니다.",
                "The link (plain + connector) count the file declares does not match what a "
                "real Vissim loaded.",
                "ファイルが宣言するリンク(通常+コネクタ)数と実際のVissimが読み込んだ数が"
                "異なります。",
            ),
        ),
        (
            "v1_plain_link_count_mismatch",
            (
                "파일이 선언한 일반 링크 수와 실제 Vissim이 로드한 수가 다릅니다.",
                "The plain-link count the file declares does not match what a real Vissim "
                "loaded.",
                "ファイルが宣言する通常リンク数と実際のVissimが読み込んだ数が異なります。",
            ),
        ),
        (
            "v1_connector_count_mismatch",
            (
                "파일이 선언한 커넥터 수와 실제 Vissim이 로드한 수가 다릅니다.",
                "The connector count the file declares does not match what a real Vissim "
                "loaded.",
                "ファイルが宣言するコネクタ数と実際のVissimが読み込んだ数が異なります。",
            ),
        ),
        (
            "v1_node_count_mismatch",
            (
                "파일이 선언한 노드 수와 실제 Vissim이 로드한 수가 다릅니다.",
                "The node count the file declares does not match what a real Vissim loaded.",
                "ファイルが宣言するノード数と実際のVissimが読み込んだ数が異なります。",
            ),
        ),
        (
            "v1_save_refused",
            (
                "로드 후 SaveNetAs 왕복 저장 자체를 Vissim이 거부했습니다.",
                "Vissim itself refused the post-load SaveNetAs round trip.",
                "読み込み後のSaveNetAs往復保存自体をVissimが拒否しました。",
            ),
        ),
    ):
        register_genesis_fix(
            GenesisFixMapping(
                code=v1_code,
                status=fixes.NOT_AUTOMATABLE,
                check_id="V1",
                label_ko=v1_specific[0],
                label_en=v1_specific[1],
                label_ja=v1_specific[2],
                reason_ko=v1_specific[0] + _v1_doctrine[0],
                reason_en=v1_specific[1] + _v1_doctrine[1],
                reason_ja=v1_specific[2] + _v1_doctrine[2],
                reason_code=EVIDENCE_CANNOT_BE_EDITED_INTO_EXISTENCE,
            )
        )

    # ==================== V2 -- reference integrity =========================
    _v2_doctrine = (
        " 생성된 draft 안의 참조가 끊어져 있다는 것은 이 생성기(services.genesis.emit) 자체의 "
        "버그이지, 승인 루프가 사후에 땜질할 대상이 아닙니다 — 수정은 회귀 테스트와 함께 emit "
        "코드에 있어야 합니다.",
        " A broken reference inside a generated draft is a bug in THIS generator "
        "(services.genesis.emit), not something an approval loop patches after the fact -- the "
        "repair belongs in the emit code, with a regression test.",
        " 生成されたdraft内の参照が切れているのは、この生成器(services.genesis.emit)自体のバグ"
        "であり、承認ループが事後に応急処置する対象ではありません -- 修正は回帰テストとともに"
        "emitコードにあるべきです。",
    )
    for v2_code, v2_specific in (
        (
            "v2_unresolved_reference",
            (
                "한 요소가 파일이 선언하지 않은 대상을 참조합니다.",
                "An element references a target the file never declares.",
                "ある要素がファイルが宣言していない対象を参照しています。",
            ),
        ),
        (
            "v2_malformed_pair_reference",
            (
                "'<소유자 no> <구성원 no>' 쌍 참조가 정확히 두 토큰이 아닙니다.",
                "A '<owner no> <member no>' pair reference is not exactly two tokens.",
                "「<所有者no> <構成員no>」ペア参照がちょうど2トークンではありません。",
            ),
        ),
        (
            "v2_lane_index_out_of_range",
            (
                "차로 참조가 대상 링크의 실제 차로 범위를 벗어납니다.",
                "A lane reference falls outside the target link's own lane range.",
                "車線参照が対象リンクの実際の車線範囲を外れています。",
            ),
        ),
        (
            "v2_connector_endpoint_dangling",
            (
                "커넥터의 끝점이 파일이 선언하지 않은 링크를 가리킵니다.",
                "A connector's endpoint points at a link the file never declares.",
                "コネクタの端点がファイルが宣言していないリンクを指しています。",
            ),
        ),
    ):
        register_genesis_fix(
            GenesisFixMapping(
                code=v2_code,
                status=fixes.NOT_AUTOMATABLE,
                check_id="V2",
                label_ko=v2_specific[0],
                label_en=v2_specific[1],
                label_ja=v2_specific[2],
                reason_ko=v2_specific[0] + _v2_doctrine[0],
                reason_en=v2_specific[1] + _v2_doctrine[1],
                reason_ja=v2_specific[2] + _v2_doctrine[2],
                reason_code=EMITTER_BUG_NOT_APPROVAL_PATCH,
            )
        )

    # ==================== V3 -- connectivity ================================
    # Six pure refusals (the doctrine sentence is module-level _V3_DOCTRINE
    # since unit #35, because the seventh code's builder composes its own
    # refusal out of the same words), then the ONE treatable code.
    _v3_doctrine = _V3_DOCTRINE
    for v3_code, v3_specific in (
        (
            "v3_orphan_link",
            (
                "어느 커넥터도 들어오거나 나가지 않는 링크입니다.",
                "A link with no connector entering or leaving it at all.",
                "コネクタが一切出入りしないリンクです。",
            ),
        ),
        (
            "v3_isolated_zone",
            (
                "네트워크의 나머지 부분에서 도달할 수도, 빠져나갈 수도 없는 링크 구역입니다.",
                "A zone of links neither reachable from, nor able to exit to, the rest of the "
                "network.",
                "ネットワークの他の部分から到達もできず、抜け出すこともできないリンク区域です。",
            ),
        ),
        (
            "v3_unreachable_zone",
            (
                "네트워크의 어느 소스에서도 도달할 수 없는 링크 구역입니다.",
                "A zone of links no source in the network can reach.",
                "ネットワーク内のどのソースからも到達できないリンク区域です。",
            ),
        ),
        (
            "v3_trapped_zone",
            (
                "빠져나갈 출구가 없어 차량이 실행이 끝날 때까지 갇히는 링크 구역입니다.",
                "A zone of links with no exit -- a vehicle that enters circulates until the run "
                "ends.",
                "出口がなく、車両が実行終了まで閉じ込められるリンク区域です。",
            ),
        ),
        (
            "v3_broken_zone",
            (
                "도달할 수도 없고 빠져나갈 수도 없는, 두 결함이 겹친 링크 구역입니다.",
                "A zone of links that is both unreachable and trapped.",
                "到達不可能かつ脱出不可能という二つの欠陥が重なったリンク区域です。",
            ),
        ),
        (
            "v3_input_cannot_reach_an_exit",
            (
                "차량 입력이 먹이는 링크에서 네트워크 출구에 도달하는 경로가 없습니다.",
                "There is no path from the link a vehicle input feeds to any network exit.",
                "車両インプットが供給するリンクからネットワークの出口へ到達する経路が"
                "ありません。",
            ),
        ),
    ):
        register_genesis_fix(
            GenesisFixMapping(
                code=v3_code,
                status=fixes.NOT_AUTOMATABLE,
                check_id="V3",
                label_ko=v3_specific[0],
                label_en=v3_specific[1],
                label_ja=v3_specific[2],
                reason_ko=v3_specific[0] + _v3_doctrine[0],
                reason_en=v3_specific[1] + _v3_doctrine[1],
                reason_ja=v3_specific[2] + _v3_doctrine[2],
                reason_code=CONNECTIVITY_IS_NOT_INVENTED,
                # Unit #36: a builder on a NOT_AUTOMATABLE entry, the same
                # licence v4_lane_drop_without_runway holds -- not because the
                # REASON varies (it is always CONNECTIVITY_IS_NOT_INVENTED),
                # but because the refusal's rationale now carries the measured
                # source testimony when the caller derived one. The builder
                # never emits a PROPOSAL; propose_genesis_fix asserts that.
                builder=_build_v3_connectivity_no_fix,
            )
        )
    # Unit #35: the ONE V3 code that names a single link END, and therefore
    # the one a connector can answer -- AUTOMATABLE, but its builder proposes
    # ONLY on a connection the source testified or a human attested. With
    # neither, it refuses exactly as its six siblings do. See this module's
    # docstring for why that leaves CONNECTIVITY_IS_NOT_INVENTED intact.
    register_genesis_fix(
        GenesisFixMapping(
            code="v3_silent_departure_point",
            status=fixes.AUTOMATABLE,
            check_id="V3",
            label_ko=_V3_SILENT_LABEL[0],
            label_en=_V3_SILENT_LABEL[1],
            label_ja=_V3_SILENT_LABEL[2],
            reason_ko=_V3_SILENT_LABEL[0] + " 연결의 사실에 이름 붙은 출처가 있을 때에만 "
            "add_connector로 치료합니다 — 작업 자신의 원본 OSM이 그 연결을 증언하거나, 현장을 "
            "아는 사람이 대상 링크와 근거를 밝혀 확인한 경우입니다. 둘 다 없으면 거부가 그대로 "
            "남습니다. 연결로의 형상은 기존 두 링크 끝에서 기계적으로 구성할 뿐 지어내지 "
            "않습니다.",
            reason_en=_V3_SILENT_LABEL[1] + " Treated with add_connector ONLY when the CONNECTION "
            "has a named source -- the job's own source OSM testifying it, or a human who knows "
            "the site attesting it with a target link and a basis. With neither, the refusal "
            "stands. The connector's shape is constructed from the two existing link ends, never "
            "invented.",
            reason_ja=_V3_SILENT_LABEL[2] + " 接続という事実に名前のある出典がある場合に限り "
            "add_connector で治療します — ジョブ自身の元のOSMがその接続を証言している場合か、"
            "現場を知る人が対象リンクと根拠を示して確認した場合です。どちらもなければ拒否のまま"
            "です。コネクタの形状は既存の2つのリンク端から機械的に構成するのみで、作り出しません。",
            verb="add_connector",
            builder=_build_v3_silent_departure_connector,
            param_fields=(
                "from_link",
                "from_lanes",
                "from_pos_m",
                "to_link",
                "to_lanes",
                "to_pos_m",
                "name",
            ),
            roadmap_rows=("B5-2", "B5-3"),
        )
    )

    # ==================== V4 -- lane continuity ==============================
    register_genesis_fix(
        GenesisFixMapping(
            code="v4_more_mapped_lanes_than_link_has",
            status=fixes.AUTOMATABLE,
            check_id="V4",
            label_ko="연결로가 링크보다 많은 차로를 매핑",
            label_en="Connector maps more lanes than the link has",
            label_ja="コネクタがリンクより多くの車線をマッピング",
            reason_ko="목표 차로 수는 발견 자체의 mapped_lanes 값에서 그대로 나오므로 추측이 "
            "필요 없습니다 -- set_link_lane_count로 링크를 넓혀 연결로가 이미 매핑한 차로 수와 "
            "맞춥니다.",
            reason_en="The target lane count comes straight from the finding's own mapped_lanes "
            "value, no guessing needed -- set_link_lane_count widens the link to match what the "
            "connector already maps.",
            reason_ja="目標車線数は発見自体のmapped_lanes値からそのまま得られ、推測は不要です -- "
            "set_link_lane_countでリンクを広げ、コネクタが既にマッピングしている車線数に合わせ"
            "ます。",
            verb="set_link_lane_count",
            builder=_build_v4_more_mapped_lanes,
            param_fields=("link_id", "lane_count"),
        )
    )
    for v4_code, v4_specific in (
        (
            "v4_lane_reference_malformed",
            (
                "연결로의 차로 참조 문자열이 '<링크 no> <차로 no>' 형태가 아닙니다.",
                "A connector's own lane reference string is not the '<link no> <lane no>' shape.",
                "コネクタ自身の車線参照文字列が「<リンクno> <車線no>」形式ではありません。",
            ),
        ),
        (
            "v4_lane_index_outside_link",
            (
                "연결로의 차로 참조가 대상 링크의 차로 범위 밖을 가리킵니다.",
                "A connector's lane reference points outside the target link's own lane range.",
                "コネクタの車線参照が対象リンクの車線範囲外を指しています。",
            ),
        ),
    ):
        register_genesis_fix(
            GenesisFixMapping(
                code=v4_code,
                status=fixes.NOT_AUTOMATABLE,
                check_id="V4",
                label_ko=v4_specific[0],
                label_en=v4_specific[1],
                label_ja=v4_specific[2],
                reason_ko=v4_specific[0] + " 현재 등록된 도구 중 커넥터 자신의 차로 매핑 문자열을 "
                "직접 고치는 동사는 없습니다 -- remove_connector+add_connector로 재구성하려면 "
                "커넥터 전체의 기하를 지어내야 합니다.",
                reason_en=v4_specific[1] + " No registered verb edits a connector's own lane-"
                "mapping string directly -- rebuilding via remove_connector+add_connector would "
                "require fabricating the connector's whole geometry.",
                reason_ja=v4_specific[2] + " 現在登録されているツールの中に、コネクタ自身の車線"
                "マッピング文字列を直接修正する動詞はありません -- remove_connector+add_connector"
                "で再構築するにはコネクタ全体の幾何を作り出す必要があります。",
                reason_code=fixes.NO_REGISTERED_VERB,
            )
        )
    register_genesis_fix(
        GenesisFixMapping(
            code="v4_lane_drop_without_runway",
            status=fixes.NOT_AUTOMATABLE,
            check_id="V4",
            label_ko="차로 감소 앞에 차로변경 여유 부족",
            label_en="Lane drop with no lane-change runway",
            label_ja="車線減少の手前に車線変更余裕が不足",
            reason_ko="경계 추적 여부에 따라 사유가 갈립니다 -- 아래 개별 판정 참조.",
            reason_en="The reason depends on whether the shortfall traces to the model boundary "
            "-- see the per-call verdict below.",
            reason_ja="不足がモデル境界に起因するかどうかで理由が分かれます -- 下記の個別判定を"
            "参照してください。",
            builder=_build_v4_lane_drop_without_runway,
        )
    )

    # ==================== V5 -- signal-geometry consistency =================
    _v5_doctrine = (
        " 신호 헤드가 어느 신호그룹/접근로를 담당하는지는 로드맵 트랙 C1-b의 현시(phase) 단위가 "
        "다룰 데이터 영역입니다 -- 이 카탈로그는 그 단위보다 앞서 나가지 않습니다.",
        " Which signal group/approach a head covers is exactly the data roadmap track C1-b's "
        "phase unit is for -- this catalogue does not reach ahead of that unit's own data.",
        " どの信号ヘッドがどの信号グループ/流入部を担当するかは、ロードマップのトラックC1-bの"
        "現示(phase)ユニットが扱うべきデータ領域です -- このカタログはそのユニットより先には"
        "進みません。",
    )
    for v5_code, v5_specific in (
        (
            "v5_head_signal_group_malformed",
            (
                "신호 헤드의 sg 참조가 '<컨트롤러 no> <그룹 no>' 형태가 아닙니다.",
                "A signal head's own sg reference is not the '<controller no> <group no>' shape.",
                "信号ヘッド自身のsg参照が「<コントローラno> <グループno>」形式ではありません。",
            ),
        ),
        (
            "v5_head_without_controller",
            (
                "신호 헤드가 파일이 선언하지 않은 컨트롤러를 참조합니다.",
                "A signal head references a controller the file never declares.",
                "信号ヘッドがファイルが宣言していないコントローラを参照しています。",
            ),
        ),
        (
            "v5_head_without_signal_group",
            (
                "신호 헤드가 그 컨트롤러에 없는 신호그룹을 참조합니다.",
                "A signal head references a signal group its controller does not declare.",
                "信号ヘッドがそのコントローラにない信号グループを参照しています。",
            ),
        ),
        (
            "v5_signal_group_without_head",
            (
                "어떤 신호 헤드도 표시하지 않는 신호그룹입니다.",
                "A signal group no head displays.",
                "どの信号ヘッドも表示しない信号グループです。",
            ),
        ),
        (
            "v5_approach_reaches_no_head_in_node",
            (
                "신호화된 노드로 진입하는 접근로가 그 노드 안에서 어떤 헤드에도 도달하지 못합니다.",
                "An approach entering a signalised node reaches no head inside that node.",
                "信号化されたノードへ進入する流入部が、そのノード内でどのヘッドにも到達しません。",
            ),
        ),
    ):
        register_genesis_fix(
            GenesisFixMapping(
                code=v5_code,
                status=fixes.NOT_AUTOMATABLE,
                check_id="V5",
                label_ko=v5_specific[0],
                label_en=v5_specific[1],
                label_ja=v5_specific[2],
                reason_ko=v5_specific[0] + _v5_doctrine[0],
                reason_en=v5_specific[1] + _v5_doctrine[1],
                reason_ja=v5_specific[2] + _v5_doctrine[2],
                reason_code=SIGNAL_COVERAGE_IS_C1B_TERRITORY,
            )
        )

    # ==================== V6 -- demand coverage ==============================
    register_genesis_fix(
        GenesisFixMapping(
            code="v6_input_without_volume",
            status=fixes.AUTOMATABLE,
            check_id="V6",
            label_ko="차량 입력에 볼륨이 전혀 없음",
            label_en="Vehicle input carries no volume at all",
            label_ja="車両インプットにボリュームが全くない",
            reason_ko="unit D4 그대로 적용 -- fixes.entrance_measurement()로 이 진입부가 실측"
            "지점인지 먼저 묻고, 실측이면 구조적으로 거부, 미검증이면 명시적 수요 추정으로 제안합"
            "니다.",
            reason_en="Unit D4 applied unchanged -- fixes.entrance_measurement() asks first "
            "whether this entrance is measured; MEASURED refuses structurally, UNVERIFIED "
            "proposes a named demand estimate.",
            reason_ja="unit D4をそのまま適用 -- fixes.entrance_measurement()でこの流入部が実測"
            "地点かをまず問い、実測なら構造的に拒否、未検証なら明示的な需要推定として提案します。",
            verb="set_vehicle_input_volume",
            builder=_build_v6_entrance_volume,
            param_fields=("input_no", "volume", "interval_no"),
        )
    )
    register_genesis_fix(
        GenesisFixMapping(
            code="v6_input_volume_is_zero",
            status=fixes.AUTOMATABLE,
            check_id="V6",
            label_ko="차량 입력의 모든 구간 볼륨이 0",
            label_en="Vehicle input's every interval carries zero volume",
            label_ja="車両インプットの全区間ボリュームがゼロ",
            reason_ko="v6_input_without_volume과 동일한 D4 경로 -- fixes.entrance_measurement()"
            "를 그대로 재사용합니다.",
            reason_en="The same D4 path as v6_input_without_volume -- reuses "
            "fixes.entrance_measurement() unchanged.",
            reason_ja="v6_input_without_volumeと同じD4経路 -- fixes.entrance_measurement()を"
            "そのまま再利用します。",
            verb="set_vehicle_input_volume",
            builder=_build_v6_entrance_volume,
            param_fields=("input_no", "volume", "interval_no"),
        )
    )
    for v6_code, v6_specific, v6_reason_code, v6_extra in (
        (
            "v6_composition_empty",
            (
                "차량 구성에 상대 흐름 항목이 하나도 없습니다.",
                "A vehicle composition carries no relative-flow entries at all.",
                "車両構成に相対流量エントリが一つもありません。",
            ),
            fixes.NO_REGISTERED_VERB,
            (
                " set_vehicle_composition_share는 기존 항목의 값만 바꾸며, 빈 구성에 새 차종 "
                "항목을 추가하는 동사는 없습니다.",
                " set_vehicle_composition_share only changes an EXISTING entry's value; no verb "
                "adds a new vehicle-type entry to an empty composition.",
                " set_vehicle_composition_shareは既存エントリの値を変更するのみで、空の構成に"
                "新しい車種エントリを追加する動詞はありません。",
            ),
        ),
        (
            "v6_composition_relflow_sum_zero",
            (
                "차량 구성의 상대 흐름 합이 0입니다.",
                "A vehicle composition's relative-flow entries sum to zero.",
                "車両構成の相対流量の合計がゼロです。",
            ),
            NO_TOOL_WITHOUT_FABRICATION,
            (
                " set_vehicle_composition_share로 어느 한 항목을 0 초과로 올릴 수는 있지만, 어느 "
                "차종을 얼마로 할지는 증거에서 도출되지 않아 지어내는 셈이 됩니다.",
                " set_vehicle_composition_share COULD raise one entry above zero, but WHICH "
                "vehicle type and what weight is not derivable from the evidence -- doing so "
                "would fabricate a composition mix.",
                " set_vehicle_composition_shareでいずれか一つのエントリをゼロ超に引き上げること"
                "はできますが、どの車種をどれだけにするかは証拠から導出できず、行えば構成比率を"
                "作り出すことになります。",
            ),
        ),
        (
            "v6_composition_relflow_negative",
            (
                "차량 구성의 한 상대 흐름 값이 음수입니다.",
                "A vehicle composition's relative-flow value is negative.",
                "車両構成の相対流量の値が負です。",
            ),
            NO_TOOL_WITHOUT_FABRICATION,
            (
                " 음수가 유효하지 않다는 것은 알 수 있지만, 올바른 음수 아닌 값은 증거에서 도출되지 "
                "않습니다 -- 0으로 고정하는 것도 그 자체로 지어낸 모델링 결정입니다.",
                " That the value is invalid is clear, but the correct non-negative value is not "
                "derivable from the evidence -- even clamping it to 0 would itself be an invented "
                "modelling decision.",
                " 値が無効であることは明らかですが、正しい非負の値は証拠から導出できません -- "
                "ゼロに固定することもそれ自体が作り出したモデリング上の判断です。",
            ),
        ),
        (
            "v6_input_composition_missing",
            (
                "차량 입력의 한 구간 볼륨이 파일에 없는 차량 구성을 참조합니다.",
                "A vehicle input's interval volume references a vehicle composition the file "
                "does not declare.",
                "車両インプットの区間ボリュームがファイルに存在しない車両構成を参照しています。",
            ),
            fixes.NO_REGISTERED_VERB,
            (
                " 새 차량 구성을 만드는 동사는 없습니다 -- 있다 해도 그 구성의 차종 배합은 지어낸 "
                "값이 됩니다.",
                " No verb creates a new vehicle composition -- even if one did, that "
                "composition's vehicle-type mix would be an invented value.",
                " 新しい車両構成を作成する動詞はありません -- あったとしても、その構成の車種"
                "配合は作り出した値になります。",
            ),
        ),
        (
            "v6_input_without_routing_coverage",
            (
                "차량 입력의 접근로가 분기점에 도달하지만 어떤 경로 결정도 커버하지 않습니다.",
                "A vehicle input's approach reaches a branching link with no routing decision "
                "covering it.",
                "車両インプットの流入部が分岐点に到達しますが、どの経路決定もカバーしていません。",
            ),
            fixes.NO_REGISTERED_VERB,
            (
                " set_route_relative_flow는 기존 경로 결정의 relFlow만 바꾸며, 새 경로 결정/경로를"
                " 만들어 배분을 지어내는 동사는 없습니다.",
                " set_route_relative_flow only changes an EXISTING routing decision's relFlow; "
                "no verb creates a new routing decision/route to invent a split from scratch.",
                " set_route_relative_flowは既存の経路決定のrelFlowを変更するのみで、新しい経路"
                "決定・経路を作成して配分を作り出す動詞はありません。",
            ),
        ),
        (
            "v6_link_demand_over_capacity",
            (
                "링크의 첨두 구간 수요가 대략적인 차로 용량을 초과합니다(warn).",
                "A link's peak-interval demand exceeds its rough lane capacity (warn).",
                "リンクのピーク区間需要がおおよその車線容量を超えています(warn)。",
            ),
            NO_TOOL_WITHOUT_FABRICATION,
            (
                " 이 정확한 질문은 이미 fixes.py에 등록된 latent_demand_overflow 제약이 실행 결과"
                "(shadow model)를 갖고 다룹니다 -- genesis 게이트 시점에는 그 증거가 없고, 볼륨을 "
                "낮추거나(측정된 진입부일 수 있음) 차로를 늘리는(몇 개가 맞는지 모름) 두 선택 모두 "
                "지어내는 셈이 됩니다.",
                " This exact question already belongs to fixes.py's registered "
                "latent_demand_overflow constraint, which has a real run's shadow model to work "
                "from -- genesis gate time has no such evidence, and both candidate fixes "
                "(lowering the input, which may be a measured entrance, or adding lane capacity, "
                "whose correct amount is unknown) would fabricate a value.",
                " この問いはfixes.pyに既に登録されているlatent_demand_overflow制約が実行結果"
                "(shadow model)を用いて扱う領域です -- genesisゲート時点ではその証拠がなく、"
                "ボリュームを下げる(実測された流入部である可能性)か車線を増やす(正しい数が"
                "不明)かのどちらも値を作り出すことになります。",
            ),
        ),
        (
            "v6_composition_unused",
            (
                "어떤 차량 입력도 참조하지 않는 차량 구성입니다(warn).",
                "A vehicle composition no vehicle input references (warn).",
                "どの車両インプットも参照していない車両構成です(warn)。",
            ),
            fixes.NO_REGISTERED_VERB,
            (
                " 차량 구성을 제거하는 동사가 등록되어 있지 않습니다.",
                " No verb removes a vehicle composition.",
                " 車両構成を削除する動詞は登録されていません。",
            ),
        ),
    ):
        register_genesis_fix(
            GenesisFixMapping(
                code=v6_code,
                status=fixes.NOT_AUTOMATABLE,
                check_id="V6",
                label_ko=v6_specific[0],
                label_en=v6_specific[1],
                label_ja=v6_specific[2],
                reason_ko=v6_specific[0] + v6_extra[0],
                reason_en=v6_specific[1] + v6_extra[1],
                reason_ja=v6_specific[2] + v6_extra[2],
                reason_code=v6_reason_code,
            )
        )

    # ==================== V7 -- geometry sanity ==============================
    for v7_code, v7_specific in (
        (
            "v7_link_without_geometry",
            (
                "링크가 폴리라인 점을 2개 미만 가집니다.",
                "A link carries fewer than 2 polyline points.",
                "リンクのポリライン点が2点未満です。",
            ),
        ),
        (
            "v7_zero_length_link",
            (
                "링크의 길이가 파일이 표현할 수 있는 최소 단위보다 짧습니다.",
                "A link's length is below the smallest length the file can represent as "
                "non-zero.",
                "リンクの長さがファイルが非ゼロとして表現できる最小単位より短いです。",
            ),
        ),
        (
            "v7_link_below_generator_floor",
            (
                "링크 길이가 이 생성기 자신이 선언한 최소 세그먼트 길이보다 짧습니다.",
                "A link's length is below this generator's own declared minimum segment length.",
                "リンク長がこの生成器自身が宣言した最小セグメント長より短いです。",
            ),
        ),
        (
            "v7_links_share_one_polyline",
            (
                "두 개 이상의 링크가 같은 폴리라인을 공유합니다.",
                "Two or more links share the exact same polyline.",
                "2つ以上のリンクが全く同じポリラインを共有しています。",
            ),
        ),
    ):
        register_genesis_fix(
            GenesisFixMapping(
                code=v7_code,
                status=fixes.AUTOMATABLE,
                check_id="V7",
                label_ko=v7_specific[0],
                label_en=v7_specific[1],
                label_ja=v7_specific[2],
                reason_ko=v7_specific[0] + " set_link_geometry로 고칠 수 있지만, 오직 census가 "
                "기록한 보정 좌표가 있을 때만 -- 없으면 좌표를 지어내지 않고 거부합니다.",
                reason_en=v7_specific[1] + " Repairable via set_link_geometry, but only when the "
                "census recorded a correction -- absent that, refused rather than inventing "
                "coordinates.",
                reason_ja=v7_specific[2] + " set_link_geometryで修正可能ですが、census が補正"
                "座標を記録している場合に限られます -- なければ座標を作り出さずに拒否します。",
                verb="set_link_geometry",
                builder=_build_v7_geometry_fix,
                param_fields=("link_id", "points_xy"),
            )
        )

    # ==================== V9 -- measurement readiness =========================
    register_genesis_fix(
        GenesisFixMapping(
            code="v9_stop_line_without_queue_counter",
            status=fixes.AUTOMATABLE,
            check_id="V9",
            label_ko="정지선에 대기열 카운터 없음",
            label_en="Stop line with no queue counter",
            label_ja="停止線に待ち行列カウンタがない",
            reason_ko="add_queue_counter로 링크 끝(정지선)에 카운터를 더하기만 하는 warn 등급, "
            "additive 수정입니다.",
            reason_en="A warn-tier, additive fix: add_queue_counter simply adds a counter at the "
            "link's end (the stop line).",
            reason_ja="add_queue_counterでリンクの終端(停止線)にカウンタを追加するだけの、warn"
            "等級・追加的な修正です。",
            verb="add_queue_counter",
            builder=_build_v9_queue_counter,
            param_fields=("link_no", "pos_m", "name"),
        )
    )


_registered = False


def _ensure_registered() -> None:
    global _registered
    if not _registered:
        _register_catalogue()
        _registered = True


_ensure_registered()


# ---------------------------------------------------------------------------
# Report helper
# ---------------------------------------------------------------------------


def build_genesis_fix_report(
    *,
    findings: list[Any] | tuple[Any, ...] = (),
    census: Mapping[str, Any] | None = None,
    targets: Mapping[str, Any] | None = None,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Everything this unit can say about one gate result, in one payload --
    mirrors ``fixes.build_fix_report``."""
    per_finding = [
        propose_for_gate_finding(finding, census=census, targets=targets, registry=registry).to_dict()
        for finding in findings
    ]
    return {
        "roadmap_rows": ["B5-2"],
        "finding_outcomes": per_finding,
        "coverage": get_genesis_fix_coverage(registry=registry),
        "unmapped_finding_codes": list(get_unmapped_finding_codes()),
    }


__all__ = [
    "ASSUME_ATTESTED_NOT_MEASURED",
    "ASSUME_CONNECTOR_GEOMETRY_CONSTRUCTED",
    "ATTESTATION_INCOMPLETE",
    "CONFIDENCE_HUMAN_ATTESTATION",
    "CONFIDENCE_SOURCE_TESTIMONY",
    "CONNECTIVITY_IS_NOT_INVENTED",
    "EMITTER_BUG_NOT_APPROVAL_PATCH",
    "EVIDENCE_CANNOT_BE_EDITED_INTO_EXISTENCE",
    "GATE_FINDING_CODES",
    "GenesisFixMapping",
    "KEY_GATE_FINDING",
    "NO_TOOL_WITHOUT_FABRICATION",
    "RUNWAY_ENDS_AT_REQUESTED_BOUNDARY",
    "SIGNAL_COVERAGE_IS_C1B_TERRITORY",
    "VERB_ADDRESSING_FIELD",
    "build_genesis_fix_report",
    "effective_status",
    "expected_fix_target",
    "get_genesis_fix",
    "get_genesis_fix_coverage",
    "get_genesis_fix_registry",
    "get_unmapped_finding_codes",
    "propose_for_gate_finding",
    "propose_genesis_fix",
    "register_genesis_fix",
]
