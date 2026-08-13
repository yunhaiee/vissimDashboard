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
  never declared a boundary — names a hole in the road network. No registered
  verb closes a hole without fabricating the road that would close it, so
  every V3 code is declared :data:`fixes.NOT_AUTOMATABLE` under
  :data:`CONNECTIVITY_IS_NOT_INVENTED`, and where the generator's own census
  already named a cause (``one_way_dead_end_unfixable`` and its siblings —
  ``services.genesis.connectivity``'s own vocabulary), that cause is quoted
  verbatim in the rationale rather than paraphrased.
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
  :data:`NO_TOOL_WITHOUT_FABRICATION`.

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
            f"({fixes.VERB_UNREGISTERED!r} is derived, never declared)"
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


def _no_fix(mapping: GenesisFixMapping, finding: Any) -> fixes.FixProposal:
    """The fixed-reason response for a ``NOT_AUTOMATABLE`` entry with no
    per-call builder.

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
    }
    return fixes.FixProposal(
        key=mapping.code,
        key_kind=KEY_GATE_FINDING,
        outcome=fixes.NO_AUTOMATED_FIX,
        scope=fixes.NOT_APPLICABLE,
        verb=None,
        params=None,
        evidence=evidence,
        rationale_ko=mapping.reason_ko + suffix_ko,
        rationale_en=mapping.reason_en + suffix_en,
        rationale_ja=mapping.reason_ja + suffix_ja,
        confidence=fixes.CONFIDENCE_NONE,
        reason_code=mapping.reason_code,
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
# on a NOT_AUTOMATABLE entry rather than a fixed reason.
# ---------------------------------------------------------------------------


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

    Which upstream link the runway search's shortfall traces back to is
    NETWORK-WALK knowledge (``gate._upstream_runway``'s own BFS) that this
    module does not recompute — the same "this module does not guess network
    knowledge it was not handed" rule ``fixes._build_routing_proposal`` states
    for ``decision_movements``. The caller supplies the traced link via
    ``targets['upstream_boundary_link']`` when it has walked the graph;
    absent that, the finding's own subject link (the lane-dropping link
    itself) is used, which still correctly classifies the common case where
    the dropping link IS the boundary-adjacent one.

    The census consulted is the SAME one V3's silent-departure clause reads
    (``metadata['connectivity']['dangling_approaches']``, ``gate._check_v3``)
    — an entry there whose ``cause`` is
    ``services.genesis.connectivity.DANGLING_MODEL_BOUNDARY`` means this
    generator itself synthesized a node at the requested extract's edge and
    the road correctly ends there.
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
        ko=f"링크 {link_id}의 차로 감소에 {evidence['required_m']} m의 차로변경 여유가 필요하지만 "
        f"{evidence['available_m']} m밖에 없습니다. 이 부족이 요청된 모델 경계에서 비롯된 것이라는 "
        "증거는 없습니다. 현재 등록된 도구 중 링크의 lnChgDist를 늘리거나 상류 도로를 연장하는 "
        "동사는 없으며, set_link_geometry는 기존 폴리라인 점을 재배치할 뿐 새 점(즉 새 길이)을 "
        "추가하지 못합니다. 존재하지 않는 도로를 만들어내지 않고는 이 부족을 메울 수 없으므로 "
        "자동 수정을 제안하지 않습니다.",
        en=f"Link {link_id}'s lane drop needs {evidence['required_m']} m of lane-change runway "
        f"and only has {evidence['available_m']} m. There is no evidence this shortfall traces "
        "to the requested model boundary. No registered verb lengthens a link's lnChgDist runway "
        "or extends the upstream road: set_link_geometry only REPOSITIONS a link's EXISTING "
        "polyline points, it cannot add new ones (new length). Closing this gap without "
        "fabricating road that is not there is not something this system can do, so no automated "
        "fix is proposed.",
        ja=f"リンク {link_id} の車線減少には {evidence['required_m']} m の車線変更余裕が必要ですが "
        f"{evidence['available_m']} m しかありません。この不足が要求されたモデル境界に起因すると"
        "いう証拠はありません。現在登録されているツールにリンクのlnChgDistを延ばす、または上流の"
        "道路を延長する動詞はなく、set_link_geometryは既存のポリライン点を再配置するのみで新しい"
        "点(すなわち新しい長さ)を追加できません。存在しない道路を作り出さずにこの不足を埋めること"
        "はできないため、自動修正は提案しません。",
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
    invent (e.g. the draft's own pre-corruption record for this link id, read
    by the caller from the census).

    Without it there is no census-derivable correction: this module does not
    invent coordinates to separate two overlapping links or to give a
    zero-length link a shape, so it refuses under
    :data:`NO_TOOL_WITHOUT_FABRICATION` rather than guess.
    """
    link_id = _link_id_from_subject(_finding_field(finding, "subject"))
    context = _finding_field(finding, "context", {}) or {}
    corrected = (targets or {}).get("corrected_points_xy")
    evidence: dict[str, Any] = {
        "link": link_id,
        "finding_context": dict(context) if isinstance(context, Mapping) else context,
    }
    if not corrected or len(list(corrected)) < 2:
        return _refuse(
            code=code,
            reason_code=NO_TOOL_WITHOUT_FABRICATION,
            evidence=evidence,
            ko=f"링크 {link_id}의 기하 결함을 고치려면 새 폴리라인 점 좌표가 필요하지만, census에서 "
            "유도된 보정 좌표가 제공되지 않았습니다. 좌표를 추측해서 만들어내지 않고 거부합니다.",
            en=f"Repairing link {link_id}'s geometry finding needs new polyline point "
            "coordinates, and no census-derived correction was supplied. Refused rather than "
            "inventing coordinates.",
            ja=f"リンク {link_id} の幾何学的な欠陥を修正するには新しいポリライン点座標が必要ですが、"
            "census由来の補正座標が提供されませんでした。座標を推測して作り出さずに拒否します。",
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
    _v3_doctrine = (
        " 연결성은 발명하지 않습니다(로드맵 원칙) — 현재 등록된 편집 동사 중 어느 것도 도로를 "
        "지어내지 않고 이 구멍을 메우지 못합니다. 승인 루프가 검토할 사람의 몫입니다.",
        " Connectivity is not invented (roadmap doctrine) -- no registered edit verb closes this "
        "hole in the road network without fabricating the road that would close it. This is a "
        "human reviewer's call, via the approval loop.",
        " 連結性は発明しません(ロードマップの原則) -- 現在登録されている編集動詞のいずれも、"
        "その穴を塞ぐ道路を作り出さずにこの穴を塞ぐことはできません。承認ループを通じて人が"
        "判断すべき事項です。",
    )
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
        (
            "v3_silent_departure_point",
            (
                "링크 끝에서 어떤 커넥터도 떠나지 않는데, 그 끝이 모델 경계가 아닙니다.",
                "A link end that no connector leaves, where that end is not the model boundary.",
                "リンク端からどのコネクタも出発しないのに、その端がモデル境界ではありません。",
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
