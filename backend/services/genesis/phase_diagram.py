"""현시도 propose+confirm — roadmap C1 option (c), and option (b) as its byproduct.

What this module is for
------------------------
``services.genesis.signals_daejeon`` reads the 대전 ``교차로 운영 DATABASE``
sheet's text layer and states, in its own docstring, the one thing it cannot
deliver: **which approach runs in which phase**. That is the 현시도 — a *drawing*
in the top-left of the sheet — and ``signals.generate_node_signals`` already
refuses without it (``approach_phases``). ``side_inputs.SignalAssignment``
carries the same refusal one layer up.

This module produces that mapping without ever guessing it, in five stages:

1. :func:`render_phase_diagram` — the diagram band of one sheet PDF, rasterised
   to a PNG, with its crop derived from the document's OWN text anchors (never a
   magic rectangle) and its provenance recorded (source sha, page, crop box,
   scale, image sha).
2. ``services.agent.phase_diagram_vision`` — a vision model *proposes* a reading
   of that image. Strict JSON through native tool-use, so nothing on that path
   touches ``clean_generated_answer()``. Too ambiguous → a NAMED refusal.
3. :func:`cross_check` — deterministic. The proposal's phase ids against the
   phases the text layer actually parsed, its approach labels against the labels
   the text layer actually prints, its read 교차로번호 against the sheet's own.
   A mismatch is FLAGGED for the human and never corrected on their behalf.
4. The human confirms or corrects, and that act is recorded as a
   :class:`MappingAttestation` — who, when, what changed against the proposal,
   and the shas of the sheet and image it was made against. Same doctrine as
   V1's operator attestation (``genesis_jobs.ATTESTATION_BASIS``): a machine's
   reading is an artifact; **a human's claim is the load-bearing fact**.
5. :func:`build_signals_document` — attestations become exactly the
   ``genesis.signals.v1`` document ``POST /api/genesis/side-inputs?kind=signals``
   already takes.

The refusal shapes, which are the point
----------------------------------------
**An unconfirmed proposal is inert.** :func:`build_signals_document` takes
:class:`ConfirmedMapping` values and nothing else, and a ``ConfirmedMapping``
cannot be constructed without a :class:`MappingAttestation` — there is no
``skip_confirmation`` flag, no ``proposal=`` parameter, and no default. A
proposal is not an input to this function in any shape; it is not "trusted less",
it is structurally not on the road.

**Attestations are provenance-bound.** :meth:`MappingAttestation.staleness`
compares the recorded sheet sha and image sha against the ones on disk now. A
re-rendered image or a replaced sheet makes the confirmation stale, and the
store reports ``phase_diagram_attestation_stale`` (UI: *재확인 필요*) rather than
carrying it forward. Silently reusing a confirmation made against a different
drawing is exactly the failure this unit exists to prevent.

**The machine never verified anything.** Nothing here — no field, no code, no
message — says a proposal was checked, validated or confirmed by the model.
:class:`PhaseProposal` carries ``outcome``/``refusal``/``ambiguity`` and the
cross-check carries ``flag``/``observation``. The only word that means "this is
right" is the attestation's ``attested_by``.

No COM, no Vissim, no network. The vision call lives in
``services/agent/phase_diagram_vision.py`` and is invoked by an offline tool or
a job, never by a request handler.

Confidentiality
---------------
The rendered images and the extracted sheet text are CLIENT material
(``client-data/`` is confidential). They are written under the workspace runtime
tree, which is git-ignored, and no test fixture in this repository carries a real
sheet: the tests build synthetic PDFs and synthetic proposals.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..dataimport.base import IssueSeverity
from .side_inputs import SIGNALS_DOC_KIND
from .signals_daejeon import DaejeonSheet, parse_sheet_text

# ---------------------------------------------------------------------------
# Document kinds. Carried IN each file, for the reason ``side_inputs.py`` gives:
# a document that is renamed, or copied into the wrong review, is caught by its
# own contents rather than by a filename nobody can trust.
# ---------------------------------------------------------------------------

RENDER_DOC_KIND = "prism.phase_diagram_render.v1"
PROPOSAL_DOC_KIND = "prism.phase_diagram_proposal.v1"
CROSS_CHECK_DOC_KIND = "prism.phase_diagram_crosscheck.v1"
ATTESTATION_DOC_KIND = "prism.phase_diagram_attestation.v1"

#: The signals side-input document kind this unit emits into is
#: ``side_inputs.SIGNALS_DOC_KIND``, imported above rather than re-spelled here,
#: so the emitted document and the validator that accepts it cannot drift apart.

#: File names inside one review directory.
RENDER_FILENAME = "render.json"
IMAGE_FILENAME = "diagram.png"
PROPOSAL_FILENAME = "proposal.json"
CROSS_CHECK_FILENAME = "crosscheck.json"
ATTESTATION_FILENAME = "attestation.json"
SHEET_TEXT_FILENAME = "sheet.txt"

#: The renderer. PDFium through ``pypdfium2`` — permissive (Apache-2.0 /
#: BSD-3-Clause), pip-installable with no external binary, and imported LAZILY
#: for the same reason ``signals_daejeon.read_sheet_text`` imports ``pypdf``
#: lazily: everything downstream of the render takes an image, so the tests and
#: the whole confirm/emit half run in an environment that has no PDF stack.
RENDERER_NAME = "pypdfium2"

#: How wide the rendered band is, in pixels. Chosen against what reads the
#: image: Anthropic's vision path downsamples anything whose long edge exceeds
#: 1568 px, so rendering wider spends tokens on detail that is thrown away, and
#: rendering much narrower loses the arrow heads that ARE the diagram.
DEFAULT_TARGET_WIDTH_PX = 1540

#: Padding around the derived band, in PDF points. Small and symmetric: the crop
#: is anchored on text this parser located exactly, and a generous pad would pull
#: the SIGNAL TABLE's first row into an image whose whole purpose is the drawing.
_BAND_PAD_TOP_PT = 6.0
_BAND_PAD_BOTTOM_PT = 3.0
_BAND_PAD_X_PT = 8.0

#: The two anchors the band is cut between. Both are printed on every one of the
#: 23 real sheets and both are read by ``signals_daejeon`` already, so a sheet
#: whose band cannot be found here is a sheet that parser would not read either.
ANCHOR_TOP = "교차로명"
ANCHOR_BOTTOM = "SIGNAL TABLE"

#: A review id: the source file name's own digest, truncated. DETERMINISTIC on
#: purpose — re-rendering the same sheet must land in the same review so the
#: existing attestation is compared against the new image (and goes stale if the
#: drawing moved), rather than quietly starting a second review nobody has
#: confirmed.
_REVIEW_ID_RE = re.compile(r"^[0-9a-f]{16}$")

#: Movement vocabulary. A closed set: an unknown turn is ``unknown`` and carries
#: its own note, never a free-text field the emit stage would have to interpret.
Turn = Literal["through", "left", "right", "u_turn", "pedestrian", "unknown"]
TURNS: tuple[str, ...] = ("through", "left", "right", "u_turn", "pedestrian", "unknown")

#: Where a leg label sits in the sketch, as the model reads it. Not a compass
#: bearing: the sheet's sketch is not oriented and nothing on it states north,
#: so calling the top leg "north" would be this module inventing a fact. The
#: human binds a side to a draft link; that binding is the attestation.
Side = Literal["top", "right", "bottom", "left", "unknown"]
SIDES: tuple[str, ...] = ("top", "right", "bottom", "left", "unknown")


class PhaseDiagramRefused(ValueError):
    """Something that cannot be done AT ALL — the same ``{code, reasons}`` shape
    as ``side_inputs.SideInputRefused`` and ``genesis_jobs.GenesisRefused``.

    Reserved for structural faults: a PDF with no diagram band, a review that
    does not exist, a mapping with no attestation. Everything a reviewer could
    act on inside a well-formed review — a proposal that disagrees with the
    parsed plan — is a :class:`CrossCheckFinding`, never an exception.
    """

    def __init__(self, code: str, reasons: list[str]) -> None:
        super().__init__(f"{code}: {'; '.join(reasons) or code}")
        self.code = code
        self.reasons = reasons


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def review_id_for(source_file: str) -> str:
    """The stable review id for one sheet file name."""
    name = Path(str(source_file or "")).name.strip()
    if not name:
        raise PhaseDiagramRefused(
            "phase_diagram_no_source_file",
            ["A review is keyed by its sheet's file name and this one has none."],
        )
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Stage 1 — the render, and what it records about itself
# ---------------------------------------------------------------------------


class LabelBox(BaseModel):
    """One text run the sheet prints inside the diagram band, and where.

    Read off the PDF's text layer, not off the image — so it is a MEASUREMENT of
    the document rather than something recovered from pixels. Two consumers:
    :func:`cross_check` uses the label set to test whether a proposed approach
    name is a name this sheet actually prints, and the confirm UI shows the
    reviewer what the document says beside what the model read.

    Coordinates are fractions of the cropped image (origin top-left), so they
    survive any later change of render scale.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(ge=0.0, le=1.0)
    height: float = Field(ge=0.0, le=1.0)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


class DiagramRender(BaseModel):
    """The provenance of ONE rendered 현시도 image.

    Every field here exists so a later reader can answer "what exactly is this
    picture of": which file (and which bytes of it — ``source_sha256``), which
    page, which rectangle in PDF points, at what scale, and what the resulting
    bytes hash to. The attestation is keyed to ``image_sha256``, so this record
    is what makes a confirmation falsifiable.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prism.phase_diagram_render.v1"] = RENDER_DOC_KIND
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    page_no: int = Field(ge=1, description="1-based, as a human counts pages.")
    page_width_pt: float
    page_height_pt: float
    #: (left, bottom, right, top) in PDF points, PDF axes (y up).
    crop_pt: tuple[float, float, float, float]
    render_scale: float = Field(gt=0)
    image_sha256: str = Field(min_length=64, max_length=64)
    image_width_px: int = Field(gt=0)
    image_height_px: int = Field(gt=0)
    renderer: str
    renderer_version: str
    rendered_at: str
    labels: list[LabelBox] = Field(default_factory=list)
    anchors: dict[str, Any] = Field(
        default_factory=dict,
        description="Which text runs the crop was cut between, and where they were found.",
    )

    def label_texts(self) -> list[str]:
        return [label.text for label in self.labels]


def _normalise_label(text: str) -> str:
    """A label reduced to what two humans would agree is "the same name".

    NFKC (the sheets mix full- and half-width parentheses), whitespace removed
    (the sketch prints vertical labels one character per line, so the text layer
    returns them with newlines inside), and case-folded for the ASCII parts.
    Deliberately NOT fuzzy: this collapses TYPOGRAPHY, never spelling. A label
    that differs by a character is a different label and gets flagged.
    """
    folded = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", "", folded).casefold()


def render_phase_diagram(
    pdf: bytes,
    *,
    source_file: str,
    target_width_px: int = DEFAULT_TARGET_WIDTH_PX,
) -> tuple[bytes, DiagramRender]:
    """One sheet PDF → (PNG bytes, :class:`DiagramRender`).

    **The crop is derived, not configured.** The band runs from just above the
    ``교차로명`` line down to just above ``SIGNAL TABLE``, and horizontally across
    the text that falls between them. Both anchors are located in the PDF's own
    text layer, so the same code cuts the right rectangle out of a sheet printed
    by any of the six controller firmwares in the client drop, and a sheet whose
    layout differs enough to move them is REFUSED
    (``phase_diagram_band_not_found``) rather than cropped to a guessed
    rectangle — an image of the wrong part of the page is a proposal about the
    wrong thing, and nothing downstream could tell.

    Raises :class:`PhaseDiagramRefused` for anything that stops a band being
    produced, including the renderer being absent.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - environment guard
        raise PhaseDiagramRefused(
            "phase_diagram_renderer_missing",
            [
                f"{RENDERER_NAME} is required to rasterise a 현시도 band "
                "(backend/requirements.txt). Everything downstream of the render takes an "
                "image and needs no PDF stack."
            ],
        ) from exc

    name = Path(str(source_file or "")).name.strip()
    if not name:
        raise PhaseDiagramRefused(
            "phase_diagram_no_source_file", ["The PDF was handed over with no file name."]
        )
    if not pdf:
        raise PhaseDiagramRefused("phase_diagram_source_empty", [f"{name!r} is empty."])

    try:
        document = pdfium.PdfDocument(pdf)
        page = document[0]
        page_width, page_height = page.get_size()
        text_page = page.get_textpage()
    except Exception as exc:  # noqa: BLE001 - any pdfium failure is an unreadable input
        raise PhaseDiagramRefused(
            "phase_diagram_source_unreadable",
            [f"{name}: the PDF could not be opened ({type(exc).__name__}: {exc})."],
        ) from exc

    top_box = _first_charbox(text_page, ANCHOR_TOP)
    bottom_box = _first_charbox(text_page, ANCHOR_BOTTOM)
    if top_box is None or bottom_box is None:
        missing = [
            anchor
            for anchor, box in ((ANCHOR_TOP, top_box), (ANCHOR_BOTTOM, bottom_box))
            if box is None
        ]
        raise PhaseDiagramRefused(
            "phase_diagram_band_not_found",
            [
                f"{name}: the 현시도 band is cut between {ANCHOR_TOP!r} and {ANCHOR_BOTTOM!r}, "
                f"and this page states no {missing}. The rectangle is derived from the "
                "document's own text and is never guessed — an image of the wrong part of a "
                "page would produce a confident proposal about the wrong drawing."
            ],
        )

    y_hi = top_box[3] + _BAND_PAD_TOP_PT
    y_lo = bottom_box[3] + _BAND_PAD_BOTTOM_PT
    if y_hi - y_lo < 20.0:
        raise PhaseDiagramRefused(
            "phase_diagram_band_degenerate",
            [
                f"{name}: the band between {ANCHOR_TOP!r} and {ANCHOR_BOTTOM!r} is "
                f"{y_hi - y_lo:.1f} pt tall. That is not a 현시도; the two anchors are in an "
                "order this layout does not have."
            ],
        )

    runs = _text_runs(text_page, y_lo=y_lo, y_hi=y_hi)
    if not runs:
        raise PhaseDiagramRefused(
            "phase_diagram_band_empty",
            [f"{name}: the derived band carries no text at all, so it is not the sheet's header."],
        )
    x_lo = max(0.0, min(run[1] for run in runs) - _BAND_PAD_X_PT)
    x_hi = min(page_width, max(run[3] for run in runs) + _BAND_PAD_X_PT)

    scale = float(target_width_px) / (x_hi - x_lo)
    crop = (x_lo, y_lo, page_width - x_hi, page_height - y_hi)
    try:
        bitmap = page.render(scale=scale, crop=crop)
        image = bitmap.to_pil().convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        png = buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 - a render failure is an unusable input
        raise PhaseDiagramRefused(
            "phase_diagram_render_failed",
            [f"{name}: the band could not be rasterised ({type(exc).__name__}: {exc})."],
        ) from exc

    band_width = x_hi - x_lo
    band_height = y_hi - y_lo
    labels = [
        LabelBox(
            text=text,
            x=max(0.0, min(1.0, (left - x_lo) / band_width)),
            # PDF y grows upward, image y grows downward.
            y=max(0.0, min(1.0, (y_hi - top) / band_height)),
            width=max(0.0, min(1.0, (right - left) / band_width)),
            height=max(0.0, min(1.0, (top - bottom) / band_height)),
        )
        for text, left, bottom, right, top in runs
    ]

    render = DiagramRender(
        source_file=name,
        source_sha256=sha256_hex(pdf),
        page_no=1,
        page_width_pt=float(page_width),
        page_height_pt=float(page_height),
        crop_pt=(x_lo, y_lo, x_hi, y_hi),
        render_scale=scale,
        image_sha256=sha256_hex(png),
        image_width_px=image.width,
        image_height_px=image.height,
        renderer=RENDERER_NAME,
        renderer_version=_renderer_version(),
        rendered_at=_now_iso(),
        labels=labels,
        anchors={
            "top": {"text": ANCHOR_TOP, "charbox": list(top_box)},
            "bottom": {"text": ANCHOR_BOTTOM, "charbox": list(bottom_box)},
        },
    )
    return png, render


def _renderer_version() -> str:
    """The renderer's OWN version string, both halves.

    Recorded on every render because a raster is a function of the rasteriser:
    a reviewer looking at a two-month-old confirmation has to be able to see
    which build drew the picture they signed against.
    """
    try:
        from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO

        return f"pypdfium2 {PYPDFIUM_INFO} / pdfium {PDFIUM_INFO}"
    except Exception:  # noqa: BLE001 - a version we cannot read is named as such
        return "unknown"


def _first_charbox(text_page: Any, needle: str) -> tuple[float, float, float, float] | None:
    """The first character box of ``needle`` on this page, or ``None``."""
    try:
        searcher = text_page.search(needle)
        hit = searcher.get_next()
    except Exception:  # noqa: BLE001 - a search failure is "not found"
        return None
    if not hit:
        return None
    try:
        return tuple(float(v) for v in text_page.get_charbox(hit[0]))  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
        return None


def _text_runs(
    text_page: Any, *, y_lo: float, y_hi: float
) -> list[tuple[str, float, float, float, float]]:
    """The band's text as runs of adjacent characters, with their boxes.

    Characters are grouped into a run while they stay on the same line and touch
    (the sketch prints leg labels one character per line, vertically, so a run is
    broken by the vertical gap and the label arrives as several one-character
    runs — which is why :func:`cross_check` compares against the CONCATENATED
    band text as well as against individual runs).
    """
    runs: list[tuple[str, float, float, float, float]] = []
    current: list[str] = []
    box: list[float] | None = None
    try:
        count = text_page.count_chars()
    except Exception:  # noqa: BLE001 - no text layer at all
        return []

    def flush() -> None:
        nonlocal current, box
        text = "".join(current).strip()
        if text and box is not None:
            runs.append((text, box[0], box[1], box[2], box[3]))
        current = []
        box = None

    for index in range(count):
        try:
            char = text_page.get_text_range(index, 1)
            left, bottom, right, top = (float(v) for v in text_page.get_charbox(index))
        except Exception:  # noqa: BLE001 - skip a character the layer cannot describe
            continue
        if not char or not char.strip():
            flush()
            continue
        if bottom < y_lo or top > y_hi:
            flush()
            continue
        if box is not None:
            same_line = abs(bottom - box[1]) <= 2.0
            adjacent = left - box[2] <= 3.0
            if not (same_line and adjacent):
                flush()
        if box is None:
            box = [left, bottom, right, top]
        else:
            box = [min(box[0], left), min(box[1], bottom), max(box[2], right), max(box[3], top)]
        current.append(char)
    flush()
    return runs


# ---------------------------------------------------------------------------
# Stage 2 — the proposal artifact (produced by services/agent, validated here)
# ---------------------------------------------------------------------------


class ProposedMovement(BaseModel):
    """One approach + one turn the model reads as green in one phase."""

    model_config = ConfigDict(extra="forbid")

    approach_label: str = Field(min_length=1, description="Verbatim as the sheet prints it.")
    turn: Turn = "unknown"
    note: str | None = None


class ProposedPhase(BaseModel):
    """One 현시 column as the model reads it.

    ``ambiguity`` is a first-class field rather than a confidence score on
    purpose: a number invites a threshold, and a threshold is how a
    low-confidence guess gets promoted to a reading. A phase the model cannot
    read says so in words, to a human, and the human decides.
    """

    model_config = ConfigDict(extra="forbid")

    phase_id: str = Field(min_length=1, description="As the sheet labels it: 'Φ1', 'Φ2', …")
    movements: list[ProposedMovement] = Field(default_factory=list)
    serves_pedestrian: bool | None = None
    ambiguity: str | None = None


class ProposedApproach(BaseModel):
    """One leg of the sketch: the name printed on it and which side it is on."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    side: Side = "unknown"
    note: str | None = None


class ProposalRefusal(BaseModel):
    """Why no reading was produced. A named reason, never an empty result."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class PhaseProposal(BaseModel):
    """A machine's READING of one 현시도. An artifact, not a fact.

    Carries its own provenance (``model``, ``image_sha256``, ``proposed_at``) so
    it can be argued with. Nothing in this class asserts correctness, and no
    consumer may render it as though it did — the only field that means "this
    mapping is right" lives on :class:`MappingAttestation`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prism.phase_diagram_proposal.v1"] = PROPOSAL_DOC_KIND
    outcome: Literal["proposed", "refused"]
    refusal: ProposalRefusal | None = None
    read_intersection_no: str | None = Field(
        default=None, description="교차로번호 as the model reads it OFF THE IMAGE."
    )
    read_intersection_name: str | None = None
    approaches: list[ProposedApproach] = Field(default_factory=list)
    phases: list[ProposedPhase] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    model: str = Field(min_length=1)
    image_sha256: str = Field(min_length=64, max_length=64)
    source_file: str = Field(min_length=1)
    proposed_at: str
    usage: dict[str, Any] = Field(
        default_factory=dict, description="Token counts the provider reported, when it did."
    )

    @model_validator(mode="after")
    def _outcome_and_content_agree(self) -> "PhaseProposal":
        if self.outcome == "refused":
            if self.refusal is None:
                raise ValueError(
                    "A refused proposal must name its reason. An empty refusal is "
                    "indistinguishable from a crash, and a reviewer cannot act on it."
                )
            if self.phases:
                raise ValueError(
                    "A refused proposal carries no phases. Half a reading presented beside a "
                    "refusal is the low-confidence guess this unit exists to not produce."
                )
        else:
            if self.refusal is not None:
                raise ValueError("A proposed reading cannot also carry a refusal.")
            if not self.phases:
                raise ValueError(
                    "A proposal with no phases is a refusal that forgot to say so. Name the "
                    "reason and set outcome='refused'."
                )
        return self

    @property
    def phase_ids(self) -> list[str]:
        return [phase.phase_id for phase in self.phases]

    def approach_labels(self) -> list[str]:
        seen: list[str] = []
        for approach in self.approaches:
            if approach.label not in seen:
                seen.append(approach.label)
        for phase in self.phases:
            for movement in phase.movements:
                if movement.approach_label not in seen:
                    seen.append(movement.approach_label)
        return seen


# ---------------------------------------------------------------------------
# Stage 3 — the deterministic cross-check
# ---------------------------------------------------------------------------

#: The proposal's phase ids against the ones the TEXT LAYER parsed. The text
#: layer is machine-read arithmetic that closes against the sheet's own cycle
#: (``signals_daejeon``'s Σ시간분할 == 주기 check); the diagram is a picture. When
#: they disagree, the picture is the suspect party — but it is FLAGGED, because
#: "correcting" a phase list would silently move a movement into a phase the
#: model never claimed.
CHECK_PHASE_IDS = "phase_diagram_phase_ids_disagree"
CHECK_APPROACH_UNKNOWN = "phase_diagram_approach_not_printed"
CHECK_INTERSECTION_NO = "phase_diagram_intersection_no_disagrees"
CHECK_LABEL_SIDE = "phase_diagram_label_side_observed"
CHECK_NO_PARSED_PLAN = "phase_diagram_no_parsed_plan"
CHECK_PHASE_WITHOUT_MOVEMENT = "phase_diagram_phase_without_movement"
CHECK_AGREES = "phase_diagram_agrees_with_text_layer"
#: A refused proposal has NO reading — nothing was compared, so the finding must
#: not wear CHECK_AGREES (that code means "compared and matched"). Its own code
#: keeps `codes("ok")`-style debugging honest on refused proposals.
CHECK_NO_READING = "phase_diagram_no_reading_to_check"

Severity = Literal["flag", "observation", "ok"]


class CrossCheckFinding(BaseModel):
    """One deterministic comparison, and what it found.

    ``flag`` = something a human must look at before confirming. ``observation``
    = a measurement offered as corroboration, which is NOT evidence of
    correctness and never blocks. ``ok`` = the comparison ran and agreed.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Severity
    message: str
    message_en: str
    context: dict[str, Any] = Field(default_factory=dict)


class CrossCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["prism.phase_diagram_crosscheck.v1"] = CROSS_CHECK_DOC_KIND
    checked_at: str = Field(default_factory=_now_iso)
    findings: list[CrossCheckFinding] = Field(default_factory=list)

    @property
    def flags(self) -> list[CrossCheckFinding]:
        return [f for f in self.findings if f.severity == "flag"]

    @property
    def agrees(self) -> bool:
        """True when nothing was flagged. NOT a claim that the reading is right —
        a proposal can agree with every text-layer fact and still put the wrong
        approach in the wrong phase, because the text layer states neither."""
        return not self.flags

    def codes(self, severity: Severity | None = None) -> list[str]:
        return [f.code for f in self.findings if severity is None or f.severity == severity]


def cross_check(
    proposal: PhaseProposal,
    *,
    sheet: DaejeonSheet | None,
    render: DiagramRender | None = None,
) -> CrossCheckResult:
    """Compare a proposal against what this repository already KNOWS, and stop.

    Three things are actually knowable without a human:

    1. **How many phases the intersection runs**, from the SIGNAL TABLE the text
       layer parsed. A proposal naming Φ5 at a four-phase intersection read a
       column the controller does not use.
    2. **Which approach names the sheet prints**, from the text runs inside the
       rendered band. A proposed label that appears nowhere on the page was
       invented or misread.
    3. **Which intersection this is**, from 교차로번호 — printed in the band, so
       the model reads it off the same image, and it must equal the number the
       text layer read. This is the check that catches an image paired with the
       wrong sheet, which nothing else downstream could notice.

    Everything else — *which* approach runs in *which* phase — is precisely what
    no text in this repository states, which is why this function cannot check it
    and why the attestation exists. Nothing here is ever corrected: a finding
    names the disagreement and the human decides.
    """
    findings: list[CrossCheckFinding] = []

    if proposal.outcome == "refused":
        # A refusal has nothing to compare. Saying so explicitly keeps the batch
        # honest: "no findings" must never be the same shape as "not checked".
        findings.append(
            CrossCheckFinding(
                code=CHECK_NO_PARSED_PLAN if sheet is None else CHECK_NO_READING,
                severity="observation",
                message="제안이 거부되어 대조할 판독 결과가 없습니다. 빈 양식으로 직접 입력하십시오.",
                message_en=(
                    "The proposal refused, so there is no reading to cross-check. The review "
                    "screen becomes a blank manual-entry form."
                ),
                context={"outcome": proposal.outcome},
            )
        )
        return CrossCheckResult(findings=findings)

    # -- 1. phase ids ------------------------------------------------------
    if sheet is None or not sheet.phases:
        findings.append(
            CrossCheckFinding(
                code=CHECK_NO_PARSED_PLAN,
                severity="flag",
                message=(
                    "이 시트의 텍스트 계층이 현시를 산출하지 못해 제안의 현시 번호를 대조할 "
                    "수 없습니다. 확인자가 직접 대조해야 합니다."
                ),
                message_en=(
                    "The text layer produced no phases for this sheet, so the proposal's phase "
                    "ids could not be cross-checked. A human must do it."
                ),
                context={"proposed_phase_ids": proposal.phase_ids},
            )
        )
    else:
        parsed_ids = [f"Φ{timing.phase_no}" for timing in sheet.phases]
        proposed_ids = proposal.phase_ids
        if proposed_ids != parsed_ids:
            findings.append(
                CrossCheckFinding(
                    code=CHECK_PHASE_IDS,
                    severity="flag",
                    message=(
                        f"제안의 현시 목록 {proposed_ids}이(가) 시트에서 파싱된 현시 "
                        f"{parsed_ids}과(와) 다릅니다. 자동으로 고치지 않습니다 — 어느 쪽이 맞는지 "
                        "확인자가 판단하십시오."
                    ),
                    message_en=(
                        f"The proposal names phases {proposed_ids}; the sheet's text layer parsed "
                        f"{parsed_ids}. Not corrected automatically — the reviewer decides."
                    ),
                    context={"proposed": proposed_ids, "parsed": parsed_ids},
                )
            )
        else:
            findings.append(
                CrossCheckFinding(
                    code=CHECK_AGREES,
                    severity="ok",
                    message=f"현시 번호가 시트 텍스트 계층과 일치합니다 ({', '.join(parsed_ids)}).",
                    message_en=(
                        f"Phase ids agree with the sheet's text layer ({', '.join(parsed_ids)})."
                    ),
                    context={"phase_ids": parsed_ids},
                )
            )

    for phase in proposal.phases:
        if not phase.movements:
            findings.append(
                CrossCheckFinding(
                    code=CHECK_PHASE_WITHOUT_MOVEMENT,
                    severity="flag",
                    message=(
                        f"{phase.phase_id}에 진행 이동류가 하나도 배정되지 않았습니다. 아무것도 "
                        "진행하지 않는 현시는 이 시트가 표시하는 내용이 아닙니다."
                    ),
                    message_en=(
                        f"{phase.phase_id} was read with no moving traffic at all, which is not "
                        "what this sheet shows."
                    ),
                    context={"phase_id": phase.phase_id},
                )
            )

    # -- 2. approach labels ------------------------------------------------
    printed = _printed_label_index(render)
    if printed is not None:
        unknown = [
            label
            for label in proposal.approach_labels()
            if _normalise_label(label) not in printed
        ]
        if unknown:
            findings.append(
                CrossCheckFinding(
                    code=CHECK_APPROACH_UNKNOWN,
                    severity="flag",
                    message=(
                        f"제안이 시트에 인쇄되지 않은 접근로 이름 {unknown}을(를) 사용했습니다. "
                        "이름이 잘못 읽혔거나 지어낸 것입니다."
                    ),
                    message_en=(
                        f"The proposal uses approach name(s) {unknown} that this sheet does not "
                        "print. Either misread or invented."
                    ),
                    context={"unknown": unknown, "printed": printed.verbatim()},
                )
            )
        else:
            findings.append(
                CrossCheckFinding(
                    code=CHECK_AGREES,
                    severity="ok",
                    message="제안이 사용한 접근로 이름이 모두 시트에 인쇄되어 있습니다.",
                    message_en="Every approach name the proposal uses is printed on the sheet.",
                    context={"labels": proposal.approach_labels()},
                )
            )
        findings.extend(_side_observations(proposal, printed_boxes=render.labels if render else []))

    # -- 3. which intersection -------------------------------------------
    if sheet is not None and sheet.intersection_no and proposal.read_intersection_no:
        if str(proposal.read_intersection_no).strip() != str(sheet.intersection_no).strip():
            findings.append(
                CrossCheckFinding(
                    code=CHECK_INTERSECTION_NO,
                    severity="flag",
                    message=(
                        f"이미지에서 읽은 교차로번호({proposal.read_intersection_no})가 시트가 "
                        f"기재한 번호({sheet.intersection_no})와 다릅니다. 이미지와 시트가 서로 "
                        "다른 교차로일 수 있습니다."
                    ),
                    message_en=(
                        f"The number read off the image ({proposal.read_intersection_no}) is not "
                        f"the one the sheet states ({sheet.intersection_no}). The image and the "
                        "sheet may be different intersections."
                    ),
                    context={
                        "read": proposal.read_intersection_no,
                        "parsed": sheet.intersection_no,
                    },
                )
            )
        else:
            findings.append(
                CrossCheckFinding(
                    code=CHECK_AGREES,
                    severity="ok",
                    message=f"이미지에서 읽은 교차로번호가 시트와 일치합니다 ({sheet.intersection_no}).",
                    message_en=(
                        f"The 교차로번호 read off the image matches the sheet "
                        f"({sheet.intersection_no})."
                    ),
                    context={"intersection_no": sheet.intersection_no},
                )
            )

    return CrossCheckResult(findings=findings)


class _PrintedLabels:
    """What text this sheet PRINTS inside the diagram band, for membership tests.

    A label counts as printed when its normalised form is one of the runs OR a
    substring of the whole band's normalised text. The second clause is not
    laxity: the sketch prints its leg names vertically, one character per line,
    so ``용전천주교(단)`` arrives from the text layer as eight separate
    one-character runs. Without it, "this name is not on the page" — the only
    check that catches an invented approach name — would fire on every sheet and
    reviewers would learn to ignore it.
    """

    def __init__(self, render: DiagramRender) -> None:
        self.runs = {_normalise_label(label.text): label.text for label in render.labels}
        self.band = _normalise_label("".join(label.text for label in render.labels))

    def __contains__(self, normalised: object) -> bool:
        if not isinstance(normalised, str) or not normalised:
            return False
        return normalised in self.runs or normalised in self.band

    def verbatim(self) -> list[str]:
        return sorted(self.runs.values())


def _printed_label_index(render: DiagramRender | None) -> _PrintedLabels | None:
    return None if render is None else _PrintedLabels(render)


def _side_observations(
    proposal: PhaseProposal, *, printed_boxes: Sequence[LabelBox]
) -> list[CrossCheckFinding]:
    """Where the sheet PRINTS each proposed leg label, as a plain measurement.

    Offered at ``observation`` tier and never as a flag, because a draughtsman
    may legitimately place a label anywhere: this says where the characters are,
    not where the road is. It is here because a reviewer confirming twenty
    intersections benefits from seeing the model's ``side`` beside the position
    the document actually prints — and because the batch's honest summary needs
    something measured to report instead of an accuracy claim nobody has earned.
    """
    findings: list[CrossCheckFinding] = []
    for approach in proposal.approaches:
        target = _normalise_label(approach.label)
        # A leg name reaches the text layer in PIECES — the layout extractor
        # breaks a run at every space, and the sketch prints some labels one
        # character per line — so a box counts as part of this label when either
        # string contains the other. Runs shorter than two characters are
        # excluded: a lone digit belongs to half the labels on the sheet and
        # averaging it in would move the reported position onto nothing.
        matches = [
            box
            for box in printed_boxes
            if target
            and len(_normalise_label(box.text)) >= 2
            and (
                _normalise_label(box.text) in target or target in _normalise_label(box.text)
            )
        ]
        if not matches:
            continue
        cx = sum(box.center[0] for box in matches) / len(matches)
        cy = sum(box.center[1] for box in matches) / len(matches)
        findings.append(
            CrossCheckFinding(
                code=CHECK_LABEL_SIDE,
                severity="observation",
                message=(
                    f"{approach.label!r} 라벨은 도면 밴드에서 가로 {cx:.2f}, 세로 {cy:.2f} "
                    f"위치에 인쇄되어 있습니다 (모델 판독: {approach.side}). 이는 글자의 위치일 "
                    "뿐이며 접근로의 방향을 증명하지 않습니다."
                ),
                message_en=(
                    f"{approach.label!r} is printed at ({cx:.2f}, {cy:.2f}) of the band "
                    f"(model read: {approach.side}). This locates the CHARACTERS, and is not "
                    "evidence of where the approach is."
                ),
                context={
                    "label": approach.label,
                    "model_side": approach.side,
                    "printed_x": round(cx, 4),
                    "printed_y": round(cy, 4),
                },
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Stage 4 — the attestation, which is the load-bearing fact
# ---------------------------------------------------------------------------


class MappingChange(BaseModel):
    """One thing the human changed against the proposal, recorded verbatim."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    was: str | None = None
    now: str | None = None


class MappingAttestation(BaseModel):
    """A named human's claim that THIS approach→phase mapping is the site's.

    The same doctrine as ``genesis_jobs.ATTESTATION_BASIS`` (V1's operator
    attestation) and as ``DaejeonSettings.declared_amber_s`` (the reviewer's
    amber): the app cannot prove the mapping from anything it holds, so it
    records an accountable claim and says whose it is — instead of pretending to
    a verification it did not perform.

    Bound to bytes on both sides. ``source_sha256`` is the sheet PDF's, and
    ``image_sha256`` is the rendered band's; :meth:`staleness` compares both, and
    a mismatch means this confirmation was made against a different drawing and
    must be made again.

    ``proposal_sha256`` is ``None`` exactly when there was no proposal to confirm
    — the blank-form path (roadmap C1 option (b)). That is a first-class case,
    not a degraded one: a mapping typed in by a human with no machine reading at
    all is the STRONGEST shape this module produces, and ``proposal_outcome``
    records which of the three it was.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prism.phase_diagram_attestation.v1"] = ATTESTATION_DOC_KIND
    attested_by: str = Field(
        min_length=1,
        description="The signed-in username. An attestation nobody signed is not one.",
    )
    attested_at: str = Field(min_length=1)
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    image_sha256: str = Field(min_length=64, max_length=64)
    proposal_sha256: str | None = Field(
        default=None, description="None = confirmed with no proposal (blank manual entry)."
    )
    proposal_outcome: Literal["proposed", "refused", "none"] = "none"
    node_id: int = Field(description="The DRAFT node this sheet's intersection is.")
    approach_links: dict[int, str] = Field(
        default_factory=dict,
        description="draft link id → the sheet approach label the human bound to it.",
    )
    approach_phases: dict[int, str] = Field(
        description="draft link id → plan phase id. The mapping itself.",
    )
    level_no: int
    plan_id: int
    declared_amber_s: float | None = Field(default=None, gt=0)
    amber_declaration_note: str | None = None
    changes: list[MappingChange] = Field(default_factory=list)
    note: str | None = None

    @field_validator("approach_phases", "approach_links", mode="before")
    @classmethod
    def _keys_are_link_ids(cls, value: Any) -> Any:
        """JSON object keys arrive as strings; a link id is an integer.

        Exactly the coercion ``side_inputs.SignalAssignment`` performs, and for
        exactly its reason: ``generate_node_signals`` keys on ``int`` and a
        string key would match no approach and be silently unused.
        """
        if not isinstance(value, Mapping):
            return value
        converted: dict[int, Any] = {}
        for key, entry in value.items():
            try:
                converted[int(str(key))] = entry
            except (TypeError, ValueError):
                raise ValueError(
                    f"key {key!r} is not a draft link id. Keys are the integer link ids the "
                    "draft assigned; a label here matches no approach and the phase would be "
                    "silently unused."
                ) from None
        return converted

    @field_validator("approach_phases")
    @classmethod
    def _mapping_is_not_empty(cls, value: dict[int, str]) -> dict[int, str]:
        if not value:
            raise ValueError(
                "approach_phases is empty. Confirming an empty mapping would record a human's "
                "name against no claim at all, and the signals document would then refuse for a "
                "reason that names the document rather than the confirmation."
            )
        for link_id, phase in value.items():
            if not str(phase).strip():
                raise ValueError(f"approach_phases[{link_id}] names an empty phase id.")
        return value

    def staleness(
        self, *, source_sha256: str, image_sha256: str
    ) -> list[str]:
        """Why this attestation no longer applies, or an empty list.

        Returned as reasons rather than a boolean because the UI has to be able
        to say WHICH provenance moved — a re-render at a new scale and a replaced
        client PDF are different problems with different answers.
        """
        reasons: list[str] = []
        if source_sha256 and self.source_sha256 != source_sha256:
            reasons.append(
                f"{self.source_file}의 시트 파일이 확인 이후 변경되었습니다 "
                f"(확인 당시 {self.source_sha256[:12]}…, 현재 {source_sha256[:12]}…). 재확인 필요."
            )
        if image_sha256 and self.image_sha256 != image_sha256:
            reasons.append(
                f"현시도 이미지가 확인 이후 다시 렌더링되었습니다 "
                f"(확인 당시 {self.image_sha256[:12]}…, 현재 {image_sha256[:12]}…). 재확인 필요."
            )
        return reasons


# ---------------------------------------------------------------------------
# Stage 5 — emit. The ONLY road from a confirmation to a signals document.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfirmedMapping:
    """One node's confirmed mapping, and the sheet text its plan is built from.

    ``attestation`` has **no default and is never optional**. That is the whole
    enforcement: :func:`build_signals_document` takes these and nothing else, so
    there is no argument anywhere on the emit path that a proposal could be
    passed as, and therefore no flag that could turn confirmation off. A caller
    holding only a proposal cannot construct this value.
    """

    attestation: MappingAttestation
    sheet_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.attestation, MappingAttestation):
            raise PhaseDiagramRefused(
                "phase_diagram_not_confirmed",
                [
                    "A confirmed mapping requires a MappingAttestation — a named human's claim, "
                    "with the shas of the sheet and image it was made against. A proposal is not "
                    "one, and there is no flag that makes it one."
                ],
            )
        if not str(self.sheet_text or "").strip():
            raise PhaseDiagramRefused(
                "phase_diagram_no_sheet_text",
                [
                    f"{self.attestation.source_file}: the sheet's extracted text is missing, so "
                    "the daejeon_sheet plan route has nothing to parse."
                ],
            )

    def to_assignment(self) -> dict[str, Any]:
        """This confirmation as one ``side_inputs.SignalAssignment`` payload."""
        attestation = self.attestation
        return {
            "node_id": attestation.node_id,
            "plan": {
                "source": "daejeon_sheet",
                "sheet_text": self.sheet_text,
                "source_file": attestation.source_file,
                "level_no": attestation.level_no,
                "plan_id": attestation.plan_id,
                "declared_amber_s": attestation.declared_amber_s,
                "amber_declaration_note": attestation.amber_declaration_note,
            },
            "approach_phases": {
                str(link_id): phase for link_id, phase in sorted(attestation.approach_phases.items())
            },
        }


def build_signals_document(
    confirmed: Sequence[ConfirmedMapping],
    *,
    conversion_settings: dict[str, Any] | None = None,
    signal_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Confirmed mappings → exactly the ``genesis.signals.v1`` document.

    The output is fed straight to ``POST /api/genesis/side-inputs?kind=signals``,
    whose ``side_inputs.parse_side_input`` is the authority on whether it is
    valid — this function does not re-implement that validation and does not
    pre-empt it. What it DOES enforce is the one thing that validator cannot see:
    that every assignment came from an attestation.

    Note the signature. There is no ``proposals=`` parameter, no
    ``allow_unconfirmed`` and no ``skip_confirmation``; adding one would be the
    edit this docstring exists to make visible in review.
    """
    if not confirmed:
        raise PhaseDiagramRefused(
            "phase_diagram_no_confirmations",
            [
                "No confirmed mapping was given. A signals document with no assignments would "
                "be refused by side_inputs for a reason that names the document; the real reason "
                "is that nobody has confirmed a 현시도 yet."
            ],
        )
    nodes = [entry.attestation.node_id for entry in confirmed]
    duplicates = sorted({node for node in nodes if nodes.count(node) > 1})
    if duplicates:
        raise PhaseDiagramRefused(
            "phase_diagram_duplicate_node",
            [
                f"Node(s) {duplicates} are confirmed twice. One node has one controller, and two "
                "assignments for it would mean one confirmation silently wins over another."
            ],
        )

    document: dict[str, Any] = {
        "kind": SIGNALS_DOC_KIND,
        "assignments": [entry.to_assignment() for entry in confirmed],
    }
    if conversion_settings is not None:
        document["conversion_settings"] = conversion_settings
    if signal_settings is not None:
        document["signal_settings"] = signal_settings
    return document


# ---------------------------------------------------------------------------
# Binding the sheet to a network — candidates, never assignments
# ---------------------------------------------------------------------------

#: Compass sectors for an approach's heading, 8-way. Used only to LABEL a
#: candidate for a human; the binding itself is the human's.
_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def approach_bearing(points: Sequence[Sequence[float]]) -> tuple[float, str] | None:
    """(heading in degrees, the compass point the approach comes FROM).

    The heading is of the final segment — the direction traffic travels as it
    reaches the stop line — so the leg itself lies in the OPPOSITE direction,
    which is what a sheet's leg label names. Returns ``None`` for a degenerate
    polyline rather than a default bearing.
    """
    if len(points) < 2:
        return None
    (x0, y0), (x1, y1) = points[-2], points[-1]
    dx, dy = float(x1) - float(x0), float(y1) - float(y0)
    if math.isclose(dx, 0.0, abs_tol=1e-9) and math.isclose(dy, 0.0, abs_tol=1e-9):
        return None
    heading = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
    from_bearing = (heading + 180.0) % 360.0
    sector = _COMPASS[int((from_bearing + 22.5) % 360.0 // 45.0)]
    return heading, sector


def node_candidates(
    draft: Any, *, intersection_name: str | None, limit: int = 8
) -> list[dict[str, Any]]:
    """Draft nodes ranked as candidates for one sheet's intersection.

    A CANDIDATE LIST, which is a different thing from a binding. The ranking is
    a plain name overlap (the OSM node name against the sheet's 교차로명, both
    normalised) and it is reported WITH its reason, so a reviewer sees why a node
    is at the top instead of being told it is the right one. Nothing here writes
    a node id anywhere: the binding happens when a human confirms it, and the
    attestation is where it lands.
    """
    from .signals import node_approaches  # local: avoids a heavy import at module load

    target = _normalise_label(intersection_name or "")
    ranked: list[tuple[float, dict[str, Any]]] = []
    for node in getattr(draft, "nodes", []):
        name = _normalise_label(getattr(node, "name", "") or "")
        score, reason = _name_overlap(target, name)
        approaches = []
        for link in node_approaches(draft, node):
            bearing = approach_bearing(link.points)
            approaches.append(
                {
                    "link_id": link.id,
                    "name": link.name,
                    "lanes": link.lanes,
                    "heading_deg": None if bearing is None else round(bearing[0], 1),
                    "from_compass": None if bearing is None else bearing[1],
                }
            )
        ranked.append(
            (
                score,
                {
                    "node_id": node.id,
                    "name": node.name,
                    "x": node.x,
                    "y": node.y,
                    "approaches": approaches,
                    "match_score": round(score, 4),
                    "match_reason": reason,
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]["node_id"]))
    return [entry for _score, entry in ranked[:limit]]


def _name_overlap(target: str, candidate: str) -> tuple[float, str]:
    """A deliberately dumb similarity, and the sentence that explains it."""
    if not target or not candidate:
        return 0.0, "이름 없음 — 이름으로는 비교할 수 없습니다."
    if target == candidate:
        return 1.0, "이름이 정확히 일치합니다."
    if target in candidate or candidate in target:
        shorter, longer = sorted((len(target), len(candidate)))
        return 0.8 * shorter / longer, "한쪽 이름이 다른 쪽에 포함됩니다."
    common = set(target) & set(candidate)
    if not common:
        return 0.0, "공통 글자가 없습니다."
    return 0.4 * len(common) / max(len(set(target)), len(set(candidate))), (
        f"공통 글자 {len(common)}자."
    )


# ---------------------------------------------------------------------------
# The store — one directory per sheet, on the workspace's own tree
# ---------------------------------------------------------------------------


class PhaseDiagramStore:
    """A directory of reviews: ``<root>/<review id>/{render,proposal,…}``.

    Constructed with an explicit root so the offline batch tool and the request
    handlers use the SAME code over different trees, and so every test runs
    against ``tmp_path``. Path containment is checked the way
    ``genesis_jobs.job_dir`` checks it — the id regex rules a separator out, and
    the resolve()+relative_to is the independent second check.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths ---------------------------------------------------------

    def review_dir(self, review_id: str) -> Path:
        if not review_id or not _REVIEW_ID_RE.match(review_id):
            raise PhaseDiagramRefused(
                "phase_diagram_unknown_review", [f"Unknown 현시도 review: {review_id!r}"]
            )
        self.root.mkdir(parents=True, exist_ok=True)
        resolved_root = self.root.resolve()
        target = (resolved_root / review_id).resolve()
        try:
            target.relative_to(resolved_root)
        except ValueError:  # pragma: no cover - the regex rules this out
            raise PhaseDiagramRefused(
                "phase_diagram_unknown_review", [f"Unknown 현시도 review: {review_id!r}"]
            ) from None
        return target

    def image_path(self, review_id: str) -> Path:
        path = self.review_dir(review_id) / IMAGE_FILENAME
        if not path.is_file():
            raise PhaseDiagramRefused(
                "phase_diagram_no_image",
                [f"Review {review_id} has no rendered 현시도 image."],
            )
        return path

    # -- writes --------------------------------------------------------

    def put_render(self, png: bytes, render: DiagramRender, *, sheet_text: str) -> str:
        """Store one render, and the sheet text its plan will be parsed from.

        A NEW image sha does not delete an existing attestation. It must not:
        deleting it would make a stale confirmation indistinguishable from one
        that was never made, and the reviewer would be shown a blank form with no
        indication that somebody had confirmed this intersection before. The
        attestation stays on disk and is reported as *재확인 필요*.
        """
        review_id = review_id_for(render.source_file)
        directory = self.review_dir(review_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / IMAGE_FILENAME).write_bytes(png)
        _write_json(directory / RENDER_FILENAME, render.model_dump(mode="json"))
        (directory / SHEET_TEXT_FILENAME).write_text(sheet_text, encoding="utf-8")
        return review_id

    def put_proposal(
        self, review_id: str, proposal: PhaseProposal, cross: CrossCheckResult
    ) -> None:
        directory = self.review_dir(review_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / PROPOSAL_FILENAME, proposal.model_dump(mode="json"))
        _write_json(directory / CROSS_CHECK_FILENAME, cross.model_dump(mode="json"))

    def put_attestation(self, review_id: str, attestation: MappingAttestation) -> None:
        """Record a confirmation, against the render that is on disk NOW.

        The image sha is checked here rather than only on read: an attestation
        minted against an image this review no longer holds is a client bug or a
        race, and storing it would create exactly the stale-but-silent state the
        module refuses.
        """
        render = self.render(review_id)
        if attestation.image_sha256 != render.image_sha256:
            raise PhaseDiagramRefused(
                "phase_diagram_attestation_stale",
                [
                    "확인하려는 현시도 이미지가 현재 저장된 이미지와 다릅니다. 화면을 새로 "
                    "고친 뒤 다시 확인하십시오 (재확인 필요).",
                    f"attested against {attestation.image_sha256[:12]}…, "
                    f"stored image is {render.image_sha256[:12]}…",
                ],
            )
        if attestation.source_sha256 != render.source_sha256:
            raise PhaseDiagramRefused(
                "phase_diagram_attestation_stale",
                [
                    "확인하려는 시트 파일이 현재 저장된 파일과 다릅니다 (재확인 필요).",
                    f"attested against {attestation.source_sha256[:12]}…, "
                    f"stored sheet is {render.source_sha256[:12]}…",
                ],
            )
        directory = self.review_dir(review_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / ATTESTATION_FILENAME, attestation.model_dump(mode="json"))

    # -- reads ---------------------------------------------------------

    def review_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            child.name
            for child in self.root.iterdir()
            if child.is_dir() and _REVIEW_ID_RE.match(child.name)
        )

    def render(self, review_id: str) -> DiagramRender:
        path = self.review_dir(review_id) / RENDER_FILENAME
        if not path.is_file():
            raise PhaseDiagramRefused(
                "phase_diagram_unknown_review",
                [f"Review {review_id} has no stored render — nothing has been rendered for it."],
            )
        return DiagramRender.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def proposal(self, review_id: str) -> PhaseProposal | None:
        path = self.review_dir(review_id) / PROPOSAL_FILENAME
        if not path.is_file():
            return None
        return PhaseProposal.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def proposal_sha256(self, review_id: str) -> str | None:
        path = self.review_dir(review_id) / PROPOSAL_FILENAME
        return sha256_hex(path.read_bytes()) if path.is_file() else None

    def cross_check(self, review_id: str) -> CrossCheckResult | None:
        path = self.review_dir(review_id) / CROSS_CHECK_FILENAME
        if not path.is_file():
            return None
        return CrossCheckResult.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def attestation(self, review_id: str) -> MappingAttestation | None:
        path = self.review_dir(review_id) / ATTESTATION_FILENAME
        if not path.is_file():
            return None
        return MappingAttestation.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def sheet_text(self, review_id: str) -> str:
        path = self.review_dir(review_id) / SHEET_TEXT_FILENAME
        if not path.is_file():
            raise PhaseDiagramRefused(
                "phase_diagram_no_sheet_text",
                [f"Review {review_id} holds no extracted sheet text."],
            )
        return path.read_text(encoding="utf-8")

    def sheet(self, review_id: str) -> DaejeonSheet:
        render = self.render(review_id)
        return parse_sheet_text(self.sheet_text(review_id), source_file=render.source_file)

    def stale_reasons(self, review_id: str) -> list[str]:
        attestation = self.attestation(review_id)
        if attestation is None:
            return []
        render = self.render(review_id)
        return attestation.staleness(
            source_sha256=render.source_sha256, image_sha256=render.image_sha256
        )

    def confirmed_mapping(self, review_id: str) -> ConfirmedMapping:
        """The confirmed mapping for one review, or a refusal naming why not.

        This is where "an unconfirmed proposal is inert" is enforced for the
        HTTP path: a review that carries a perfectly good proposal and no
        attestation refuses here, with the proposal's existence stated in the
        reason so nobody reads it as "nothing was produced".
        """
        attestation = self.attestation(review_id)
        if attestation is None:
            has_proposal = self.proposal(review_id) is not None
            raise PhaseDiagramRefused(
                "phase_diagram_not_confirmed",
                [
                    (
                        "이 교차로의 현시도 배정이 아직 확인되지 않았습니다. "
                        + (
                            "제안은 존재하지만 확인 전에는 사용되지 않습니다."
                            if has_proposal
                            else "제안도 확인도 없습니다."
                        )
                    ),
                    f"review {review_id}: no attestation. A proposal is never used unconfirmed.",
                ],
            )
        stale = self.stale_reasons(review_id)
        if stale:
            raise PhaseDiagramRefused("phase_diagram_attestation_stale", stale)
        return ConfirmedMapping(attestation=attestation, sheet_text=self.sheet_text(review_id))

    # -- the whole review, for a browser -------------------------------

    def review_payload(self, review_id: str) -> dict[str, Any]:
        """One review as the confirm screen reads it.

        Deliberately includes ``proposal: null`` and ``attestation: null`` as
        real states rather than omitting the keys: the blank-form path (option
        (b)) is the same screen with a null proposal, and a UI that had to infer
        it from a missing key would eventually infer it wrong.
        """
        render = self.render(review_id)
        proposal = self.proposal(review_id)
        attestation = self.attestation(review_id)
        cross = self.cross_check(review_id)
        sheet = self.sheet(review_id)
        stale = self.stale_reasons(review_id)
        return {
            "review_id": review_id,
            "source_file": render.source_file,
            "render": render.model_dump(mode="json"),
            "proposal": None if proposal is None else proposal.model_dump(mode="json"),
            "proposal_sha256": self.proposal_sha256(review_id),
            "cross_check": None if cross is None else cross.model_dump(mode="json"),
            "attestation": None if attestation is None else attestation.model_dump(mode="json"),
            "stale_reasons": stale,
            "confirmed": attestation is not None and not stale,
            "sheet": _sheet_summary(sheet),
        }

    def index_payload(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for review_id in self.review_ids():
            try:
                render = self.render(review_id)
            except PhaseDiagramRefused:
                continue
            proposal = self.proposal(review_id)
            attestation = self.attestation(review_id)
            cross = self.cross_check(review_id)
            stale = self.stale_reasons(review_id)
            rows.append(
                {
                    "review_id": review_id,
                    "source_file": render.source_file,
                    "image_sha256": render.image_sha256,
                    "proposal_outcome": None if proposal is None else proposal.outcome,
                    "proposal_refusal_code": (
                        None
                        if proposal is None or proposal.refusal is None
                        else proposal.refusal.code
                    ),
                    "flags": [] if cross is None else cross.codes("flag"),
                    "confirmed": attestation is not None and not stale,
                    "attested_by": None if attestation is None else attestation.attested_by,
                    "attested_at": None if attestation is None else attestation.attested_at,
                    "stale_reasons": stale,
                }
            )
        return rows


def _sheet_summary(sheet: DaejeonSheet) -> dict[str, Any]:
    """What the TEXT LAYER says about this intersection, for the confirm screen.

    The reviewer needs the phases the sheet parsed (to check the proposal's
    against them) and the scheduled (수준, ID) pairs (to pick the plan the
    mapping is confirmed for). No timing values are included: the screen's job is
    the mapping, and ``signals_daejeon`` already owns the numbers.
    """
    return {
        "source_file": sheet.source_file,
        "intersection_name": sheet.intersection_name,
        "intersection_no": sheet.intersection_no,
        "parsed": sheet.ok,
        "refusal_codes": sheet.issue_codes(IssueSeverity.REFUSE),
        "phase_ids": [f"Φ{timing.phase_no}" for timing in sheet.phases],
        "plan_choices": [
            {"level_no": level_no, "plan_id": plan_id}
            for level_no, plan_id in sorted(
                {(entry.level_no, entry.plan_id) for entry in sheet.tod_entries}
            )
        ],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )


__all__ = [
    "ATTESTATION_DOC_KIND",
    "CROSS_CHECK_DOC_KIND",
    "PROPOSAL_DOC_KIND",
    "RENDER_DOC_KIND",
    "SIDES",
    "TURNS",
    "ConfirmedMapping",
    "CrossCheckFinding",
    "CrossCheckResult",
    "DiagramRender",
    "LabelBox",
    "MappingAttestation",
    "MappingChange",
    "PhaseDiagramRefused",
    "PhaseDiagramStore",
    "PhaseProposal",
    "ProposalRefusal",
    "ProposedApproach",
    "ProposedMovement",
    "ProposedPhase",
    "approach_bearing",
    "build_signals_document",
    "cross_check",
    "node_candidates",
    "render_phase_diagram",
    "review_id_for",
    "sha256_hex",
]
