"""Side inputs: the DATA a caller adds to a generation, and nothing else.

Roadmap v2 §5.B, Track G. ``services/genesis_jobs.py`` could reach ``osm`` →
``draft`` → ``emit`` → ``gate`` and nothing else, so every generation started
from a browser was geometry-only — and a geometry-only draft can never reach
``published``, because V5 and V6 report NOT_CHECKABLE on a file with no signal
and no demand layer (roadmap §5.B records that as a stated limit of B1). The
three finished modules that fix it (``demand``, ``fleet``, ``signals``) all take
DATA the geometry cannot imply. This module is where that data enters.

**One format family, and it is the one the adapters already emit.** A side input
is a JSON document whose payload is ``services.dataimport``'s neutral model —
:class:`~services.dataimport.base.ImportResult`, verbatim, as
``result.model_dump(mode="json")`` produces it. Nothing here invents a schema: a
Daejeon or a WA SCATS adapter's own output IS the file, so a record's class,
its unit and its provenance survive into the network with no second mapping in
between. The wrapper around it carries exactly the joins the modules refuse to
guess, and nothing more.

WHAT EACH SIDE INPUT MUST CARRY, AND WHY IT CANNOT BE DERIVED
-------------------------------------------------------------
``demand`` (which also builds the fleet — see ``compose.py``)
    ``counts``: the ImportResult. ``approach_links``: **site id → {approach leg
    id → draft link id}**. A count site id is a survey label and a draft link id
    is a number ``draft.py`` assigned from a sorted OSM way id;
    ``generate_demand`` refuses to join them by position, ordering or name
    similarity, and this module does not do it on its behalf.

``signals``
    ``controllers``: the ImportResult carrying
    :class:`~services.dataimport.schema.SignalControllerData` (the 1.6 route),
    OR a Daejeon sheet's extracted text (the 대전 route). ``assignments``: one
    entry per signalised node giving the draft node id, which plan to use, and
    **approach link id → phase id**. Roadmap C1 states why the last one is data:
    the 대전 sheet prints its 현시도 as a PICTURE, so no parse can produce it,
    and ``generate_node_signals`` already refuses without it.

TWO PLAN SOURCES, BOTH NAMED
-----------------------------
``source: "observed_history"``
    ``convert_to_fixed_time(controller, program)`` — MRWA Appendix-A A.2
    averaging of an OBSERVED adaptive history. This is the only conversion 1.6
    performs, and it refuses a ``declared`` or ``derived`` program by design
    ("converting a declared plan would relabel a fact as an estimate").
``source: "daejeon_sheet"``
    ``signals_daejeon.parse_sheet_text`` → ``to_fixed_time_plan``. The 대전
    ``교차로 운영 DATABASE`` sheet's own route. It REFUSES unless the caller
    declares the amber (``declared_amber_s``), because the sheet prints only the
    combined ``황색+전적색`` and the split is a reviewer's declaration — never a
    number this code picks.

WHAT THIS MODULE REFUSES
------------------------
Everything structural, at STAGE time, before a token is handed out — the same
discipline ``genesis_jobs.stage_osm`` follows for the ``.osm`` itself. Bytes
that are not UTF-8 JSON, a document that is not an object, an unknown ``kind``,
an unknown key anywhere (every model is ``extra="forbid"``, so a typo is a
refusal and never a silently ignored field), a link id that is not an integer,
an empty ``approach_links``, an ``ImportResult`` the neutral model rejects. What
it does NOT check here is anything that needs the draft — whether link 7 exists,
whether the counts cover the mapped approaches — because the draft does not
exist yet and ``generate_demand``/``generate_node_signals`` already report those
in both directions.

Offline, no COM, no Vissim, no network access.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.dataimport.base import ImportIssue, ImportResult, IssueSeverity
from services.dataimport.fixed_time import ConversionSettings, FixedTimePlan, convert_to_fixed_time

from .compose import ComposedLayers, compose_layers
from .demand import DemandSettings
from .draft import DraftNetwork
from .fleet import FleetSettings
from .signals import SignalSettings
from .signals_daejeon import DaejeonSettings, parse_sheet_text, to_fixed_time_plan

#: The side-input kinds this backend accepts. A closed set, checked before a
#: token exists — an unknown kind is never staged "for later".
KIND_DEMAND = "demand"
KIND_SIGNALS = "signals"
KINDS = (KIND_DEMAND, KIND_SIGNALS)

#: Document type tags. Carried IN the file so a document that is renamed, or
#: staged under the wrong ``kind``, is caught by its own contents rather than by
#: a filename nobody can trust.
DEMAND_DOC_KIND = "genesis.demand.v1"
SIGNALS_DOC_KIND = "genesis.signals.v1"

#: The cap on ONE side input's bytes. Much smaller than a dataset upload
#: (250 MB) because these are JSON documents that are fully parsed and validated
#: inside a request handler: the WA SCATS drop's 30,208 count records serialise
#: to roughly 12 MB, so 32 MB is ~2.5x the largest real input this project has
#: measured, and a document beyond it is refused with the number rather than
#: being allowed to occupy the request thread.
MAX_SIDE_INPUT_BYTES = 32 * 1024 * 1024


class SideInputRefused(ValueError):
    """A side input that cannot be used AT ALL — the same shape, and the same
    ``{code, reasons}`` HTTP body, as ``genesis_jobs.GenesisRefused``.

    Reserved for structural faults (not JSON, unknown kind, unusable join
    table). Everything a data reviewer could act on inside a well-formed
    document — a class with no vehicle type, an approach with no counts — is an
    ``ImportIssue`` reported by the layer that found it, never an exception.
    """

    def __init__(self, code: str, reasons: list[str]) -> None:
        super().__init__(f"{code}: {'; '.join(reasons) or code}")
        self.code = code
        self.reasons = reasons


# ---------------------------------------------------------------------------
# The demand side input
# ---------------------------------------------------------------------------


class DemandSideInput(BaseModel):
    """Counts + the join, plus the two settings blocks the modules expose.

    There is no separate FLEET document, and that is deliberate rather than an
    omission: ``compose.py`` builds the fleet from ``counts``' OWN observed
    classes (``observed_vehicle_classes``), which is the composition the
    2026-08-08 live check proved. A second document listing classes would either
    duplicate the counts or state a class set the counts do not contain, and the
    only fleet input that is NOT in the counts — the §5.2.3 modelling choices —
    is :attr:`fleet_settings`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["genesis.demand.v1"] = Field(
        default=DEMAND_DOC_KIND, description="Document type tag."
    )
    counts: ImportResult = Field(
        description="An adapter's ImportResult, verbatim (model_dump(mode='json'))."
    )
    approach_links: dict[str, dict[str, int]] = Field(
        description="site id → {approach leg id → draft link id}. Required; never guessed.",
    )
    window_start: datetime | None = Field(
        default=None,
        description="Analysis window start. Both ends or neither; 1.2 records the choice.",
    )
    window_end: datetime | None = None
    demand_settings: DemandSettings | None = None
    fleet_settings: FleetSettings | None = None
    #: Which generation ``approach_links``' ids were read off, and that
    #: generation's own link-table fingerprint (:func:`~.draft.link_table_fingerprint`).
    #: Both or neither (:meth:`_built_against_is_whole`) — the same shape
    #: ``window_start``/``window_end`` use. Optional because an adapter/API
    #: producer that never looked at a specific draft (the WA SCATS path, Track
    #: G staging) legitimately has neither; ``generate_demand`` does not refuse
    #: for their absence, but names it rather than staying silent (module
    #: docstring's CRITICAL fix: draft link ids are a sequential counter reset
    #: on every build, so a bbox trim or any other rebuild renumbers them, and
    #: an unfingerprinted mapping applied to a renumbered draft can silently
    #: attach one approach's observed volumes to a different link).
    built_against_job: str | None = Field(default=None, min_length=1)
    built_against_link_table_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )

    @model_validator(mode="after")
    def _built_against_is_whole(self) -> "DemandSideInput":
        if (self.built_against_job is None) != (self.built_against_link_table_sha256 is None):
            raise ValueError(
                "built_against_job and built_against_link_table_sha256 must be given together "
                "— a job id with no fingerprint (or a fingerprint with no job id) cannot be "
                "checked against a draft's own numbering."
            )
        return self

    @field_validator("approach_links")
    @classmethod
    def _join_is_not_empty(cls, value: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        if not value:
            raise ValueError(
                "approach_links is empty. Without a site→leg→link join nothing observed can be "
                "attached to a draft link, and generate_demand refuses rather than guessing one."
            )
        for site_id, legs in value.items():
            if not legs:
                raise ValueError(
                    f"approach_links[{site_id!r}] names no approach; an empty site entry joins "
                    "nothing and hides the fact that the site was never mapped."
                )
        return value

    @model_validator(mode="after")
    def _window_is_whole(self) -> "DemandSideInput":
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError(
                "window_start and window_end must be given together — half a window is not a "
                "window, and 1.2 bounds both the profile and the routing weights with it."
            )
        if (
            self.window_start is not None
            and self.window_end is not None
            and self.window_end <= self.window_start
        ):
            raise ValueError(
                f"window_end ({self.window_end.isoformat()}) is not after window_start "
                f"({self.window_start.isoformat()})."
            )
        return self

    def window(self) -> tuple[datetime, datetime] | None:
        if self.window_start is None or self.window_end is None:
            return None
        return (self.window_start, self.window_end)

    def summary(self) -> dict[str, Any]:
        """What was staged, for the upload response. Counted, never sampled."""
        classes = sorted(
            {count.vehicle_class.code for count in self.counts.counts}
        )
        return {
            "doc_kind": self.kind,
            "adapter": self.counts.adapter.name,
            "region": self.counts.adapter.region,
            "source_files": list(self.counts.source_files),
            "counts": len(self.counts.counts),
            "sites_in_counts": sorted({count.site_id for count in self.counts.counts}),
            "sites_mapped": sorted(self.approach_links),
            "approaches_mapped": sum(len(legs) for legs in self.approach_links.values()),
            "vehicle_classes": classes,
            "counts_refusals": self.counts.issue_codes(IssueSeverity.REFUSE),
            "window": (
                None
                if self.window() is None
                else {
                    "start": self.window_start.isoformat(),  # type: ignore[union-attr]
                    "end": self.window_end.isoformat(),  # type: ignore[union-attr]
                }
            ),
        }


# ---------------------------------------------------------------------------
# The signals side input
# ---------------------------------------------------------------------------


class ObservedHistoryPlanRef(BaseModel):
    """Which controller (and optionally which program) 1.6 should convert."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["observed_history"]
    controller_id: str = Field(min_length=1)
    program_id: str | None = Field(
        default=None,
        description="Omitted = the controller's single observed-history program; "
        "zero or several is a refusal, not a guess (fixed_time.py).",
    )


class DaejeonSheetPlanRef(BaseModel):
    """One 대전 ``교차로 운영 DATABASE`` sheet's text layer + which plan to take.

    ``declared_amber_s`` is a REVIEWER'S DECLARATION and is passed straight to
    :class:`~services.genesis.signals_daejeon.DaejeonSettings`, which refuses
    without it. Omitting it here does not default to 3.0 s or any other number;
    it produces ``daejeon_intergreen_split_unstated`` and no plan.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["daejeon_sheet"]
    sheet_text: str = Field(min_length=1, description="The sheet's extracted text layer.")
    source_file: str = Field(min_length=1, description="What the text came from, for provenance.")
    level_no: int
    plan_id: int
    declared_amber_s: float | None = Field(default=None, gt=0)
    amber_declaration_note: str | None = None


class SignalAssignment(BaseModel):
    """One signalised node: which plan runs it, and which approach is in which
    phase.

    ``approach_phases`` keys are DRAFT LINK IDS. They arrive as JSON strings and
    are converted here, with a refusal for anything that is not an integer —
    ``generate_node_signals`` keys on ``int`` and a string key would silently
    match nothing, which is the failure mode this whole file exists to avoid.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: int
    plan: ObservedHistoryPlanRef | DaejeonSheetPlanRef = Field(discriminator="source")
    approach_phases: dict[int, str] = Field(
        description="draft approach link id → plan phase id. Required; never derived.",
    )

    @field_validator("approach_phases", mode="before")
    @classmethod
    def _keys_are_link_ids(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        converted: dict[int, str] = {}
        for key, phase in value.items():
            try:
                link_id = int(str(key))
            except (TypeError, ValueError):
                raise ValueError(
                    f"approach_phases key {key!r} is not a draft link id. Keys are the integer "
                    "link ids the draft assigned; a label here matches no approach and the "
                    "phase would be silently unused."
                ) from None
            converted[link_id] = phase
        return converted

    @field_validator("approach_phases")
    @classmethod
    def _mapping_is_not_empty(cls, value: dict[int, str]) -> dict[int, str]:
        if not value:
            raise ValueError(
                "approach_phases is empty. The approach→phase mapping is the one fact "
                "signals.generate_node_signals refuses to guess (roadmap C1: the 현시도 is a "
                "picture), so an empty mapping is a refusal, not a default."
            )
        for link_id, phase in value.items():
            if not str(phase).strip():
                raise ValueError(
                    f"approach_phases[{link_id}] names an empty phase id."
                )
        return value


class SignalsSideInput(BaseModel):
    """The signal plans and their node assignments."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["genesis.signals.v1"] = Field(default=SIGNALS_DOC_KIND)
    controllers: ImportResult | None = Field(
        default=None,
        description="An adapter's ImportResult carrying SignalControllerData. Required for "
        "every observed_history assignment; unused by daejeon_sheet ones.",
    )
    assignments: list[SignalAssignment] = Field(min_length=1)
    conversion_settings: ConversionSettings | None = None
    signal_settings: SignalSettings | None = None

    @model_validator(mode="after")
    def _observed_history_needs_controllers(self) -> "SignalsSideInput":
        needs = [
            a.plan.controller_id
            for a in self.assignments
            if isinstance(a.plan, ObservedHistoryPlanRef)
        ]
        if needs and self.controllers is None:
            raise ValueError(
                f"assignments name observed-history controllers {sorted(set(needs))} but the "
                "document carries no 'controllers' ImportResult to find them in."
            )
        nodes = [a.node_id for a in self.assignments]
        if len(set(nodes)) != len(nodes):
            duplicates = sorted({n for n in nodes if nodes.count(n) > 1})
            raise ValueError(
                f"node(s) {duplicates} are assigned twice. One node has one controller; two "
                "assignments for it would mean one silently wins."
            )
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "doc_kind": self.kind,
            "adapter": None if self.controllers is None else self.controllers.adapter.name,
            "controllers_in_document": (
                []
                if self.controllers is None
                else sorted(c.controller_id for c in self.controllers.controllers)
            ),
            "assignments": [
                {
                    "node_id": a.node_id,
                    "plan_source": a.plan.source,
                    "approaches": len(a.approach_phases),
                    "phases": sorted(set(a.approach_phases.values())),
                }
                for a in self.assignments
            ],
        }


# ---------------------------------------------------------------------------
# Parsing — the only entrance
# ---------------------------------------------------------------------------


def parse_side_input(kind: str, content: bytes) -> DemandSideInput | SignalsSideInput:
    """Bytes → a validated side input, or :class:`SideInputRefused`.

    Called at STAGE time so a caller learns their document is unusable while
    they are still looking at the upload, not two steps later inside a job whose
    only output is a refusal — the reason ``stage_osm`` proves the .osm is OSM
    XML before handing out a token.
    """
    if kind not in KINDS:
        raise SideInputRefused(
            "genesis_side_input_unknown_kind",
            [
                f"{kind!r} is not a side-input kind. Expected one of: {', '.join(KINDS)}."
            ],
        )
    if not content.strip():
        raise SideInputRefused(
            "genesis_side_input_empty", [f"The {kind} side input is empty."]
        )
    if len(content) > MAX_SIDE_INPUT_BYTES:
        raise SideInputRefused(
            "genesis_side_input_too_large",
            [
                f"The {kind} side input is {len(content)} bytes; the limit is "
                f"{MAX_SIDE_INPUT_BYTES} bytes ({MAX_SIDE_INPUT_BYTES // (1024 * 1024)} MB)."
            ],
        )
    try:
        payload = json.loads(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SideInputRefused(
            "genesis_side_input_not_json",
            [f"The {kind} side input is not UTF-8 text ({exc})."],
        ) from exc
    except ValueError as exc:
        raise SideInputRefused(
            "genesis_side_input_not_json",
            [f"The {kind} side input is not a JSON document ({exc})."],
        ) from exc
    if not isinstance(payload, dict):
        raise SideInputRefused(
            "genesis_side_input_not_json",
            [
                f"The {kind} side input's top level is a {type(payload).__name__}, not a JSON "
                "object."
            ],
        )

    model = DemandSideInput if kind == KIND_DEMAND else SignalsSideInput
    try:
        return model.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic's own message IS the reason
        raise SideInputRefused(
            f"genesis_side_input_invalid_{kind}",
            _validation_reasons(exc),
        ) from exc


def _validation_reasons(exc: BaseException) -> list[str]:
    """Pydantic's errors as one readable line each, with the path that failed.

    A bare ``str(exc)`` on a document with 30,000 records can run to megabytes,
    so the list is capped — and the cap says how many were omitted, because a
    truncated error list that does not admit it is truncated is the silent drop
    in miniature.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return [str(exc)]
    try:
        raw = errors()
    except Exception:  # noqa: BLE001 - fall back to the plain message
        return [str(exc)]
    lines: list[str] = []
    for entry in raw[:20]:
        location = ".".join(str(part) for part in entry.get("loc", ())) or "(document)"
        lines.append(f"{location}: {entry.get('msg', 'invalid')}")
    if len(raw) > 20:
        lines.append(f"... and {len(raw) - 20} further validation error(s) not shown.")
    return lines or [str(exc)]


def load_side_input(kind: str, path) -> DemandSideInput | SignalsSideInput:
    """:func:`parse_side_input` over a file this server itself stored."""
    from pathlib import Path

    return parse_side_input(kind, Path(path).read_bytes())


# ---------------------------------------------------------------------------
# Side input → the arguments compose_layers takes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalPlanBuild:
    """The assignments ``compose_layers`` takes, plus every finding building
    them produced.

    Separate from the composition because these findings are about the PLANS —
    1.6's conversion, the 대전 sheet's amber — not about the draft. A node whose
    plan could not be built is NOT silently dropped from the assignments: it is
    absent from :attr:`assignments` and named in :attr:`issues` at REFUSE tier,
    so the composition refuses the whole generation rather than emitting a
    network with one intersection quietly unsignalised.
    """

    assignments: dict[int, tuple[FixedTimePlan, Mapping[int, str]]]
    issues: tuple[ImportIssue, ...] = ()

    @property
    def refusals(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.REFUSE)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def refusal_reasons(self, language: str = "ko") -> list[str]:
        return [(i.messages.get(language) or i.message) for i in self.refusals]


def build_signal_assignments(document: SignalsSideInput) -> SignalPlanBuild:
    """Every assignment's ``FixedTimePlan``, built by its own named source.

    Neither route is reimplemented here: ``observed_history`` calls 1.6's
    :func:`~services.dataimport.fixed_time.convert_to_fixed_time` and
    ``daejeon_sheet`` calls
    :func:`~services.genesis.signals_daejeon.to_fixed_time_plan`. This function
    only routes, and carries each route's findings out verbatim.
    """
    issues: list[ImportIssue] = []
    assignments: dict[int, tuple[FixedTimePlan, Mapping[int, str]]] = {}

    controllers = {
        controller.controller_id: controller
        for controller in (document.controllers.controllers if document.controllers else [])
    }

    for assignment in document.assignments:
        subject = f"node {assignment.node_id}"
        plan: FixedTimePlan | None = None

        if isinstance(assignment.plan, ObservedHistoryPlanRef):
            controller = controllers.get(assignment.plan.controller_id)
            if controller is None:
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "genesis_side_input_controller_not_found",
                        f"Controller {assignment.plan.controller_id!r} is not in the document's "
                        f"controllers ({sorted(controllers)}), so node {assignment.node_id} has "
                        "no plan to convert.",
                        messages={
                            "ko": f"제어기 {assignment.plan.controller_id!r}이(가) 문서에 "
                            f"없습니다(수록: {sorted(controllers)}). 노드 {assignment.node_id}에 "
                            "변환할 계획이 없습니다.",
                            "en": f"Controller {assignment.plan.controller_id!r} is absent from "
                            f"the document ({sorted(controllers)}).",
                            "ja": f"制御機 {assignment.plan.controller_id!r} が文書にありません"
                            f"(収録: {sorted(controllers)})。",
                        },
                        subject=subject,
                        context={"controller_id": assignment.plan.controller_id},
                    )
                )
                continue
            program = (
                controller.program_by_id(assignment.plan.program_id)
                if assignment.plan.program_id
                else None
            )
            if assignment.plan.program_id and program is None:
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "genesis_side_input_program_not_found",
                        f"Controller {controller.controller_id} carries no program "
                        f"{assignment.plan.program_id!r} (has "
                        f"{[p.program_id for p in controller.programs]}).",
                        messages={
                            "ko": f"제어기 {controller.controller_id}에 프로그램 "
                            f"{assignment.plan.program_id!r}이(가) 없습니다"
                            f"(보유: {[p.program_id for p in controller.programs]}).",
                            "en": f"Controller {controller.controller_id} has no program "
                            f"{assignment.plan.program_id!r}.",
                            "ja": f"制御機 {controller.controller_id} にプログラム "
                            f"{assignment.plan.program_id!r} がありません。",
                        },
                        subject=subject,
                        context={"program_id": assignment.plan.program_id},
                    )
                )
                continue
            plan = convert_to_fixed_time(
                controller, program, settings=document.conversion_settings
            )
            issues.extend(plan.issues)
        else:
            sheet = parse_sheet_text(
                assignment.plan.sheet_text, source_file=assignment.plan.source_file
            )
            issues.extend(sheet.issues)
            if sheet.controller is None:
                continue
            settings = DaejeonSettings(
                declared_amber_s=assignment.plan.declared_amber_s,
                amber_declaration_note=assignment.plan.amber_declaration_note,
            )
            plan, plan_issues = to_fixed_time_plan(
                sheet,
                level_no=assignment.plan.level_no,
                plan_id=assignment.plan.plan_id,
                settings=settings,
            )
            issues.extend(plan_issues)

        if plan is None or not plan.ok:
            # The plan's own refusals are already in ``issues``; the node is
            # left out of the assignments and the composition will not run.
            continue
        assignments[assignment.node_id] = (plan, dict(assignment.approach_phases))

    return SignalPlanBuild(assignments=assignments, issues=tuple(issues))


def compose_from_documents(
    draft: DraftNetwork,
    *,
    demand: DemandSideInput | None = None,
    signals: SignalsSideInput | None = None,
    job_id: str | None = None,
) -> ComposedLayers:
    """Validated side-input documents + a draft → the composed layers.

    The one seam between "what the caller uploaded" and
    :func:`~services.genesis.compose.compose_layers`, which knows nothing about
    documents. The plan-build findings are carried into the composition's own
    ``issues`` rather than being returned separately, so a caller has ONE place
    to look for "why was this generation refused" and a plan that failed to
    convert cannot be lost between two result objects.

    ``job_id`` is THIS generation's own job id (``genesis_jobs._compose_side_layers``
    has it) — used only to name the target side of the CRITICAL link-numbering
    check in a mismatch's refusal message; optional because most callers (tests,
    ``tools/*``) never have one.
    """
    plan_build = (
        build_signal_assignments(signals) if signals is not None else SignalPlanBuild({})
    )
    composed = compose_layers(
        draft,
        counts=None if demand is None else demand.counts,
        approach_links=None if demand is None else demand.approach_links,
        demand_window=None if demand is None else demand.window(),
        demand_settings=None if demand is None else demand.demand_settings,
        demand_built_against_job=None if demand is None else demand.built_against_job,
        demand_built_against_link_table_sha256=(
            None if demand is None else demand.built_against_link_table_sha256
        ),
        fleet_settings=None if demand is None else demand.fleet_settings,
        signal_assignments=None if signals is None else plan_build.assignments,
        signal_settings=None if signals is None else signals.signal_settings,
        job_id=job_id,
    )
    if not plan_build.issues:
        return composed
    return replace(composed, issues=tuple(plan_build.issues) + composed.issues)


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


__all__ = [
    "DEMAND_DOC_KIND",
    "KINDS",
    "KIND_DEMAND",
    "KIND_SIGNALS",
    "MAX_SIDE_INPUT_BYTES",
    "SIGNALS_DOC_KIND",
    "DaejeonSheetPlanRef",
    "DemandSideInput",
    "ObservedHistoryPlanRef",
    "SideInputRefused",
    "SignalAssignment",
    "SignalPlanBuild",
    "SignalsSideInput",
    "build_signal_assignments",
    "compose_from_documents",
    "load_side_input",
    "parse_side_input",
]
