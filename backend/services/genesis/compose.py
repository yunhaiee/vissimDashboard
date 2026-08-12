"""The ONE order in which a draft's optional layers are built and composed.

Roadmap v2 §5.B, Track G. ``emit.py`` has taken ``signals=``/``demand=``/
``fleet=`` on one call since roadmap 3.2, and the four-layer file was proved to
open in a real PTV Vissim 2026 SP09 (``445477d``, evidence in
``backend/tools/genesis_fullstack_live_check.py``). What did NOT exist was a
place where "how the four layers are composed" lives: the ordering, the
fleet→demand dependency and the cross-check were written out inside that live
check's ``build_fullstack_network``, so the product pipeline had nothing to call
and would have had to restate them. Restating them is how the second copy drifts
from the one that was measured.

This module is that place, and it is a straight EXTRACTION — the same three
calls in the same order with the same arguments the live check made:

1. :func:`~services.genesis.fleet.build_fleet` over the counts' own observed
   classes (:func:`~services.genesis.fleet.observed_vehicle_classes`);
2. :func:`~services.genesis.demand.generate_demand`, whose ``vehicle_types`` is
   **the fleet's own map** — the dependency below;
3. :func:`~services.genesis.signals.generate_signals` over the caller's
   node → (plan, approach→phase) assignments.

``tests/test_genesis_compose.py`` pins the extraction by emitting from this
module's output and from the live check's own build and comparing the BYTES.

THE FLEET→DEMAND DEPENDENCY, ENFORCED RATHER THAN ASSUMED
---------------------------------------------------------
A demand layer's vehicle compositions reference ``vehType`` numbers, and the
only thing that DECLARES those types in the file is the fleet layer. Emit demand
without fleet and Vissim reports a successful load, drops the dangling
references silently and runs a network with no traffic — the exact silent-wrong
-answer failure this project exists to catch (``emit.py``'s v2 ladder measured
that class of drop twice).

So the dependency is a refusal here, never a warning and never a repair:

* the demand layer is built from the SAME ``ImportResult`` as the fleet, so a
  caller cannot supply one without the other by construction;
* :meth:`~services.genesis.fleet.DraftFleet.check_composition_targets` — the
  fleet module's OWN cross-check, not a restatement — is run over the composed
  demand, and any finding it returns refuses the composition;
* a demand layer that would emit while the fleet layer would not refuses with
  :data:`CODE_DEMAND_WITHOUT_FLEET`.

WHICH COMBINATIONS HAVE LIVE EVIDENCE, STATED EXACTLY
------------------------------------------------------
Only two layer sets have been opened by a real Vissim: geometry alone
(``genesis_open_live_check``, 2026-08-06) and **all four together**
(``genesis_fullstack_live_check``, ``445477d`` — ``full.inpx`` LoadNet OK, no
``.err``, containers round-tripped). The intermediate sets this module can
produce — geometry+demand+fleet, and geometry+signals — rest on ``emit.py``'s
strictly-additive contract and each layer's own pinned emission, NOT on a
LoadNet of that exact combination. The full-stack probe's bisect rungs exist for
those sets and were never run, because the four-layer file passed.

That gap is real and it is deliberately not papered over — it is also the reason
V1 exists. No layer set, certified or not, can reach ``published`` from this
code: V1 is refuse-tier, a generation job never opens COM, and the only way it
goes green is an operator attesting that a real Vissim loaded THAT artifact
(``genesis_jobs.attach_live_check``). So "we composed a set nobody has loaded"
is a state the gate already reports as INCOMPLETE rather than one this module
has to prevent by refusing.

A REQUESTED LAYER THAT PRODUCES NOTHING IS A REFUSAL
-----------------------------------------------------
Each layer module keeps an all-or-nothing contract: it either emits its objects
or it emits none and says why. This module adds the one guarantee none of them
can give on its own — that a layer the CALLER ASKED FOR actually reached the
file. A layer that returns ``ok`` with nothing in it would otherwise vanish
between "the API accepted my counts" and "the .inpx has no vehicle inputs",
which is the silent drop §2.3.3 of the roadmap forbids outright.

A MAPPED APPROACH THAT SEEDS NOTHING IS A REFUSAL, TOO
--------------------------------------------------------
The guarantee above works at the LAYER level, and a demand layer can pass it —
nonzero inputs, nonzero compositions — while one caller-mapped approach inside
it produced NEITHER a vehicle input nor a routing decision: its mapped link is
not an entry (so ``demand.py``'s own vehicle-input builder skips it, silently —
``_build_inputs`` simply never appends it) and carries no departing connector
(so no routing decision exists for it either). ``demand.py`` records this only
as an unweighted ``links_seeded_neither`` coverage entry; nothing about it is a
``REFUSE``, a ``WARN`` or even an ``INFO``. A caller who mapped that approach
asserted "traffic enters here"; if this pipeline cannot seed it, refusing beats
emitting a network where one site's several-hundred-veh/h demand is entirely
absent while ``layers.ok`` and the gate's V6 both read green — reproduced
twice: a single site mapped entirely to non-entry links (0 vehicle inputs, 1
orphaned composition) and a two-site document where one site's healthy inputs
made the other site's total drop invisible at every level above ``demand.py``
itself.

The check is PER APPROACH, not per site, and that is the strictness that
actually subsumes the per-site case rather than the reverse: a half-mapped
site's other approaches can seed correctly while one of them silently does not
(the two-site reproduction above), so refusing only when a whole site is empty
would still let it through. It is implemented HERE rather than in
``demand.py`` on purpose — this module already receives ``approach_links`` and
already reads ``DraftDemand.inputs``/``.decisions`` (each carrying its own
``site_id``/``approach_leg_id``) to render refusals, so it can tell a true drop
apart from the LEGITIMATE shape ``demand.py``'s own ``_classify_seeded_links``
names: an interior approach that gets routing but no input because "its
traffic entered upstream". Reading straight off ``inputs``/``decisions``,
rather than off ``approach_links`` alone, is what keeps that legitimate shape
from being refused by mistake.

Offline, no COM, no Vissim, no network access — like everything else in
``services.genesis``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from services.dataimport.base import ImportIssue, ImportResult, IssueSeverity
from services.dataimport.fixed_time import FixedTimePlan

from .demand import DemandSettings, DraftDemand, generate_demand
from .draft import DraftNetwork
from .fleet import DraftFleet, FleetSettings, build_fleet, observed_vehicle_classes
from .signals import DraftSignals, SignalSettings, generate_signals

#: A demand layer that would emit while its fleet would not. See the module
#: docstring: the compositions' ``vehType`` references would dangle, and Vissim
#: drops dangling references without a word.
CODE_DEMAND_WITHOUT_FLEET = "genesis_compose_demand_without_fleet"

#: A layer the caller asked for that produced no objects and no refusal.
CODE_LAYER_EMPTY = "genesis_compose_requested_layer_empty"

#: An approach the caller mapped in ``approach_links`` that the demand layer
#: seeded with NEITHER a vehicle input NOR a routing decision — its mapped link
#: is not an entry and carries no departing connector, so the caller's observed
#: demand for it never reaches a single Vissim object. See the module
#: docstring's "A MAPPED APPROACH THAT SEEDS NOTHING IS A REFUSAL, TOO".
CODE_APPROACH_NOT_SEEDED = "genesis_compose_mapped_approach_not_seeded"

#: Every code THIS module can raise at REFUSE tier. Data, so the "these produce
#: no composition at all" claim is testable rather than aspirational — the same
#: shape ``demand.REFUSE_CODES`` and ``fleet.REFUSE_CODES`` keep.
REFUSE_CODES: frozenset[str] = frozenset(
    {CODE_DEMAND_WITHOUT_FLEET, CODE_LAYER_EMPTY, CODE_APPROACH_NOT_SEEDED}
)

_LAYER_LABELS = {
    "fleet": {"ko": "차종", "en": "fleet", "ja": "車種"},
    "demand": {"ko": "수요", "en": "demand", "ja": "需要"},
    "signals": {"ko": "신호", "en": "signals", "ja": "信号"},
}


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    *,
    messages: dict[str, str] | None = None,
    subject: str | None = None,
    context: dict | None = None,
) -> ImportIssue:
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        messages=messages or {},
        subject=subject,
        context=context or {},
    )


@dataclass(frozen=True)
class ComposedLayers:
    """The optional layers of one draft, plus every finding composing them made.

    ``requested`` names the layers the CALLER asked for, which is not the same
    set as the layers that carry objects: a requested layer that produced
    nothing is a refusal (module docstring), and knowing which was asked for is
    what makes that statable.

    ``fleet``/``demand``/``signals`` are ``None`` for a layer nobody asked for.
    They are passed to :func:`~services.genesis.emit.emit_inpx` verbatim, which
    treats ``None`` and an empty layer identically — so a composition that only
    carries geometry emits the byte-for-byte file a geometry-only draft always
    did.
    """

    fleet: DraftFleet | None = None
    demand: DraftDemand | None = None
    signals: DraftSignals | None = None
    #: Findings this module made, not the layers' own.
    issues: tuple[ImportIssue, ...] = ()
    requested: tuple[str, ...] = ()

    @property
    def layer_issues(self) -> tuple[ImportIssue, ...]:
        """Every finding the three layer modules made, in layer order."""
        collected: list[ImportIssue] = []
        if self.fleet is not None:
            collected.extend(self.fleet.issues)
        if self.demand is not None:
            collected.extend(self.demand.issues)
        if self.signals is not None:
            collected.extend(self.signals.issues)
        return tuple(collected)

    @property
    def all_issues(self) -> tuple[ImportIssue, ...]:
        return self.layer_issues + tuple(self.issues)

    @property
    def refusals(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.all_issues if i.severity is IssueSeverity.REFUSE)

    @property
    def warnings(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.all_issues if i.severity is IssueSeverity.WARN)

    @property
    def ok(self) -> bool:
        """No layer, and no composition rule, refused."""
        return not self.refusals

    @property
    def emitting(self) -> bool:
        """Whether anything beyond geometry would be written."""
        return bool(
            (self.fleet is not None and self.fleet.emitting)
            or (self.demand is not None and self.demand.emitting)
            or (self.signals is not None and self.signals.emitting)
        )

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [
            i.code for i in self.all_issues if severity is None or i.severity is severity
        ]

    def refusal_reasons(self, language: str = "ko") -> list[str]:
        """One human sentence per refusal, ko primary, in the layers' own words.

        The layer modules author these messages; this module renders them and
        never writes a replacement. A refusal a caller cannot read is a refusal
        that will be reported to us as "it just failed".
        """
        return [
            (issue.messages.get(language) or issue.message)
            for issue in self.refusals
        ]

    def summary(self) -> dict[str, Any]:
        """The compact per-layer record the job carries (the full findings go to
        ``<name>.layers.json``).

        Every number here is a count of objects that reached the emission, so a
        reader can tell "the signal layer ran" from "the signal layer produced
        one controller and four groups" without opening the artifact.
        """
        out: dict[str, Any] = {"requested": list(self.requested)}
        if self.fleet is not None:
            out["fleet"] = {
                "emitted": bool(self.fleet.emitting),
                "vehicle_types": len(self.fleet.types),
                "type_nos": sorted(self.fleet.declared_type_nos()),
                "speed_distribution_no": self.fleet.speed_distr_no,
                "refusals": self.fleet.issue_codes(IssueSeverity.REFUSE),
                "warnings": self.fleet.issue_codes(IssueSeverity.WARN),
                "coverage": self.fleet.coverage.counts(),
            }
        if self.demand is not None:
            out["demand"] = {
                "emitted": bool(self.demand.emitting),
                "vehicle_inputs": len(self.demand.inputs),
                "compositions": len(self.demand.compositions),
                "routing_decisions": len(self.demand.decisions),
                "refusals": self.demand.issue_codes(IssueSeverity.REFUSE),
                "warnings": self.demand.issue_codes(IssueSeverity.WARN),
                "coverage": self.demand.coverage.counts(),
            }
        if self.signals is not None:
            emitting = self.signals.emitting
            out["signals"] = {
                "emitted": bool(emitting),
                "controllers": len(emitting),
                "signal_groups": sum(len(node.groups) for node in emitting),
                "signal_heads": sum(len(node.heads) for node in emitting),
                "detectors": sum(len(node.detectors) for node in emitting),
                "nodes": [node.node_id for node in emitting],
                "refusals": self.signals.issue_codes(IssueSeverity.REFUSE),
                "warnings": self.signals.issue_codes(IssueSeverity.WARN),
            }
        out["compose_refusals"] = [
            i.code for i in self.issues if i.severity is IssueSeverity.REFUSE
        ]
        return out

    def as_dict(self, language: str = "ko") -> dict[str, Any]:
        """Everything, for ``<name>.layers.json``: the summary plus EVERY
        finding and EVERY compliance item the three layers graded.

        Nothing is truncated here. The compact summary is what crosses the HTTP
        boundary; this file is the audit copy, and a §5 item dropped from it
        would be exactly the silent omission the compliance report exists to
        prevent.
        """
        payload: dict[str, Any] = {"summary": self.summary(), "language": language}
        payload["issues"] = [
            _issue_as_dict(issue, language) for issue in self.all_issues
        ]
        compliance: list[dict[str, Any]] = []
        for layer, items in (
            ("fleet", self.fleet.compliance if self.fleet is not None else ()),
            ("demand", self.demand.compliance if self.demand is not None else ()),
            ("signals", self.signals.compliance if self.signals is not None else ()),
        ):
            for item in items:
                compliance.append(
                    {
                        "layer": layer,
                        "state": item.state.value,
                        "rule_id": item.rule_id,
                        "section": item.section,
                        "doc_page": item.doc_page,
                        "citation": item.citation,
                        "title": item.title(language),
                        "subjects": list(item.subjects),
                        "evidence": item.evidence,
                        # The item's OWN renderer, so a §5 line reads the same
                        # here as it does in the draft's compliance report.
                        "text": item.render(language),
                    }
                )
        payload["compliance"] = compliance
        payload["coverage"] = {
            "fleet": self.fleet.coverage.sorted_entries() if self.fleet is not None else None,
            "demand": (
                self.demand.coverage.sorted_entries() if self.demand is not None else None
            ),
        }
        return payload


def _issue_as_dict(issue: ImportIssue, language: str) -> dict[str, Any]:
    return {
        "severity": issue.severity.value,
        "code": issue.code,
        "subject": issue.subject,
        "message": issue.messages.get(language) or issue.message,
        "message_en": issue.message,
        "citation": issue.citation,
        "context": issue.context,
    }


#: What an empty composition is. Handed to ``emit_inpx`` it changes no byte.
NO_LAYERS = ComposedLayers()


def _dropped_approach_issues(
    demand: DraftDemand, approach_links: Mapping[str, Mapping[str, int]]
) -> list[ImportIssue]:
    """One REFUSE issue naming every caller-mapped approach the demand layer
    seeded with NOTHING — no vehicle input, no routing decision.

    Read straight off ``demand.inputs``/``demand.decisions``, each of which
    carries its own ``site_id``/``approach_leg_id`` — never off
    ``approach_links`` alone — so a mapped link that legitimately gets ROUTING
    ONLY (an interior approach; see the module docstring) is never mistaken
    for a drop. A link with EITHER object is fine; only "neither" is the
    silent-drop shape this function exists to catch.
    """
    if not approach_links:
        return []
    seeded = {(plan.site_id, plan.approach_leg_id) for plan in demand.inputs}
    routed = {(seed.site_id, seed.approach_leg_id) for seed in demand.decisions}
    dropped: list[tuple[str, str, int]] = []
    for site_id in sorted(approach_links):
        for leg, link_id in sorted(approach_links[site_id].items()):
            key = (site_id, leg)
            if key not in seeded and key not in routed:
                dropped.append((site_id, leg, link_id))
    if not dropped:
        return []

    labels = [f"{site_id}|{leg}->link {link_id}" for site_id, leg, link_id in dropped]
    listing = ", ".join(labels)
    return [
        _issue(
            IssueSeverity.REFUSE,
            CODE_APPROACH_NOT_SEEDED,
            f"Approach(es) {listing} were mapped by the caller, but the demand layer seeded "
            "neither a vehicle input nor a routing decision for any of them: the mapped link is "
            "not an entry link (so it produces no vehicle input) and carries no departing "
            "connector (so it produces no routing decision either). The observed demand the "
            "caller supplied for that approach never reaches a single Vissim object, which a "
            "whole-layer emptiness check cannot see when other approaches seeded correctly.",
            messages={
                "ko": f"접근로 {listing}을(를) 매핑했지만 수요 레이어가 차량 입력도 회전 배정도 "
                "만들지 않았습니다. 매핑된 링크가 진입(entry) 링크가 아니고 나가는 커넥터도 없어 "
                "해당 접근로의 관측 수요가 어떤 Vissim 객체에도 반영되지 않습니다. 다른 접근로가 "
                "정상 반영되었더라도 이 누락은 레이어 전체 개수만으로는 보이지 않습니다.",
                "en": f"Approach(es) {listing} were mapped but the demand layer seeded neither a "
                "vehicle input nor a routing decision for them; the mapped link is not an entry "
                "and has no departing connector, so the observed demand never reaches the file.",
                "ja": f"流入路 {listing} を対応付けましたが、需要レイヤは車両入力も経路配分も作成 "
                "しませんでした。対応付けたリンクは進入リンクでも発信コネクタを持つリンクでもない "
                "ため、観測需要がファイルに一切反映されません。",
            },
            context={"dropped_approaches": labels},
        )
    ]


def compose_layers(
    draft: DraftNetwork,
    *,
    counts: ImportResult | None = None,
    approach_links: Mapping[str, Mapping[str, int]] | None = None,
    demand_window: tuple[datetime, datetime] | None = None,
    demand_settings: DemandSettings | None = None,
    demand_built_against_job: str | None = None,
    demand_built_against_link_table_sha256: str | None = None,
    fleet_settings: FleetSettings | None = None,
    signal_assignments: Mapping[int, tuple[FixedTimePlan, Mapping[int, str]]] | None = None,
    signal_settings: SignalSettings | None = None,
    job_id: str | None = None,
) -> ComposedLayers:
    """Build a draft's optional layers in the order that was proved to load.

    ``counts`` drives BOTH the fleet and the demand — they are two readings of
    one import, and splitting them into two arguments would let a caller pair a
    demand with a fleet built from other classes. ``approach_links`` is the
    site→leg→draft-link join ``generate_demand`` requires and refuses to guess.

    ``demand_built_against_job``/``demand_built_against_link_table_sha256`` are
    the CRITICAL link-numbering check's inputs (``side_inputs.DemandSideInput``'s
    fields of the same name, minus the ``demand_`` prefix): when the fingerprint
    is present, ``generate_demand`` refuses rather than seeding a draft whose
    link ids have since been renumbered out from under the mapping. ``job_id``
    is THIS generation's own id, used only to name it in that refusal's message.

    ``signal_assignments`` maps draft node id → ``(plan, {approach link id →
    phase id})``. The mapping is DATA the caller supplies:
    ``generate_node_signals`` treats it as a required argument for the reason
    roadmap C1 records — the phase diagram is a picture, and a mapping derived
    from geometry would be a guess wearing an import's clothes.

    Returns a :class:`ComposedLayers` ALWAYS — the import layer's report-do-not
    -raise contract. ``result.ok`` is false and the caller must not emit when
    any layer or any composition rule refused.
    """
    requested: list[str] = []
    issues: list[ImportIssue] = []

    fleet: DraftFleet | None = None
    demand: DraftDemand | None = None
    signals: DraftSignals | None = None

    # -- 1. fleet, from the counts' OWN observed classes ---------------------
    if counts is not None:
        requested.extend(("fleet", "demand"))
        fleet = build_fleet(observed_vehicle_classes(counts), settings=fleet_settings)

        # -- 2. demand, whose vehicle types ARE the fleet's map --------------
        demand = generate_demand(
            draft,
            counts,
            approach_links=approach_links or {},
            vehicle_types=dict(fleet.map),
            window=demand_window,
            settings=demand_settings,
            built_against_job=demand_built_against_job,
            built_against_link_table_sha256=demand_built_against_link_table_sha256,
            target_job_id=job_id,
        )

        # -- the per-approach guarantee: a mapping that seeded nothing -------
        # Only meaningful once the demand layer itself validated cleanly — a
        # demand that already refused (unknown site, unknown link, ...) has
        # empty ``inputs``/``decisions`` by construction (``_bail()``), which
        # would make EVERY mapped approach look dropped and bury the specific
        # reason under a redundant one.
        if demand.ok:
            issues.extend(_dropped_approach_issues(demand, approach_links or {}))

        # -- the dependency, checked by the fleet module's own cross-check ---
        if demand.emitting and not fleet.emitting:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    CODE_DEMAND_WITHOUT_FLEET,
                    "The demand layer would emit vehicle compositions while the fleet layer "
                    "emits no vehicle types, so every composition's vehType reference would "
                    "point at something the file never declares. Vissim reports a successful "
                    "load for that file and silently drops the references, which is a network "
                    "that looks seeded and carries no traffic.",
                    messages={
                        "ko": "수요 레이어는 차량 구성을 내보내는데 차종 레이어가 차종을 하나도 "
                        "내보내지 않습니다. 구성의 vehType 참조가 파일에 없는 대상을 가리키게 "
                        "되고, Vissim은 그 파일을 성공으로 로드한 뒤 참조를 조용히 버립니다 — "
                        "수요가 있는 것처럼 보이지만 통행이 없는 네트워크가 됩니다.",
                        "en": "The demand layer would emit compositions while the fleet layer "
                        "emits no vehicle types; every vehType reference would dangle and "
                        "Vissim drops dangling references without a word.",
                        "ja": "需要レイヤが車両構成を出力する一方で車種レイヤが車種を出力しません。"
                        "vehType 参照が宙に浮き、Vissim は警告なくそれを捨てます。",
                    },
                    context={
                        "fleet_refusals": fleet.issue_codes(IssueSeverity.REFUSE),
                        "compositions": len(demand.compositions),
                    },
                )
            )
        elif demand.emitting and fleet.emitting:
            issues.extend(fleet.check_composition_targets(demand))

    # -- 3. signals ---------------------------------------------------------
    if signal_assignments is not None:
        requested.append("signals")
        signals = generate_signals(draft, signal_assignments, settings=signal_settings)

    # -- 4. a layer that was asked for and produced nothing ------------------
    for name, layer in (("fleet", fleet), ("demand", demand), ("signals", signals)):
        if name not in requested or layer is None:
            continue
        if layer.ok and not layer.emitting:
            label = _LAYER_LABELS[name]
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    CODE_LAYER_EMPTY,
                    f"The {name} layer was requested, refused nothing, and produced no objects "
                    "at all. Emitting the network anyway would ship a file that silently lacks "
                    "the layer the caller supplied data for.",
                    messages={
                        "ko": f"{label['ko']} 레이어를 요청했는데 거부 사유도 없이 아무 객체도 "
                        "만들지 않았습니다. 그대로 내보내면 사용자가 준 데이터가 조용히 사라진 "
                        "파일이 됩니다.",
                        "en": f"The {label['en']} layer was requested, refused nothing and "
                        "produced no objects; emitting anyway would silently drop the caller's "
                        "data.",
                        "ja": f"{label['ja']}レイヤを要求しましたが、拒否理由もなくオブジェクトを "
                        "1 つも生成していません。そのまま出力すると入力が黙って消えます。",
                    },
                    subject=name,
                    context={"layer": name},
                )
            )

    return ComposedLayers(
        fleet=fleet,
        demand=demand,
        signals=signals,
        issues=tuple(issues),
        requested=tuple(requested),
    )


__all__ = [
    "CODE_APPROACH_NOT_SEEDED",
    "CODE_DEMAND_WITHOUT_FLEET",
    "CODE_LAYER_EMPTY",
    "NO_LAYERS",
    "REFUSE_CODES",
    "ComposedLayers",
    "compose_layers",
]
