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
import logging
import math
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..dataimport.base import IssueSeverity
from .side_inputs import SIGNALS_DOC_KIND
from .signals_daejeon import DaejeonSheet, parse_sheet_text

logger = logging.getLogger(__name__)

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
#: unit #52 — what a no-image review knows about its intersection, in place of
#: the 대전 sheet text a pdf review parses. A separate file rather than a field
#: on the render for the reason the render's own kind exists: it is a different
#: document with a different author (a person typing, not a parser reading).
MANUAL_SHEET_FILENAME = "manual-sheet.json"
MANUAL_SHEET_DOC_KIND = "prism.phase_diagram_manual_sheet.v1"

#: unit #58 — the LEDGER of every timing value a person amended on screen, and
#: the single home of the DECLARED 황색시간. A separate document from the
#: manual sheet for the same reason the manual sheet is separate from the
#: render: it has a different author and a different act behind it. The uploaded
#: workbook is what somebody sent; this is what somebody later corrected, with
#: their name and the server's clock on every entry.
#:
#: THE VALUES DO NOT ALL LIVE HERE, and that is deliberate. A timing number's
#: home is the uploader's own record (``MANUAL_SHEET_FILENAME``'s ``timing``),
#: because amending it is that person amending their own statement — one number,
#: one place, no overlay to resolve at read time. What lives here is (1) the
#: declared amber, which no sheet of any medium can supply (the 대전 sheets print
#: 황색+전적색 as one number and never the amber alone), and (2) the append-only
#: history of BOTH, so nothing is ever overwritten-and-forgotten.
AMENDMENT_FILENAME = "amendments.json"
AMENDMENT_DOC_KIND = "prism.phase_diagram_amendments.v1"

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

#: What a review was made FROM (unit #52). ``pdf_band`` is the original path —
#: a rasterised band of a client sheet, which is the only medium that HAS an
#: image. The other two carry no image at all and say so by name: the review
#: screen shows a named placeholder where the drawing would be, and nothing
#: anywhere renders a stand-in picture. A fake image would make the one thing an
#: attestation is bound to — "the drawing this human actually looked at" —
#: unfalsifiable, which is the whole reason the sha is recorded.
ReviewMedium = Literal["pdf_band", "excel_upload", "blank_entry"]
MEDIA_WITHOUT_IMAGE: tuple[str, ...] = ("excel_upload", "blank_entry")

#: The ``renderer`` string a no-image review records. Not "none": the field is
#: read by a human months later and has to answer "what drew this" honestly.
NO_RENDERER = "이미지 없음 — 렌더링하지 않았습니다"


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


#: The characters :func:`composite_source_key` escapes, and what each becomes.
#: ``%`` is FIRST and that ordering is load-bearing: escaping it after the
#: others would re-escape the escapes and stop the encoding being reversible.
#: ``/`` and ``\`` are in the set because :func:`review_id_for` hashes
#: ``Path(key).name``, which silently truncates at a separator; ``#`` is in it
#: because it is the delimiter itself.
_KEY_ESCAPES: tuple[tuple[str, str], ...] = (
    ("%", "%25"),
    ("#", "%23"),
    ("/", "%2F"),
    ("\\", "%5C"),
)
_KEY_DELIMITER = "#"


def composite_source_key(*parts: str) -> str:
    """A review key made of several strings — e.g. a workbook plus 교차로명 (#52).

    One upload carries many intersections, so the key has to name both; and it
    has to stay DETERMINISTIC, so a corrected re-upload lands in the same review
    and its existing confirmation is compared against the new content rather
    than starting a second review of the same junction.

    **The encoding is injective, and that is the whole point.** The first
    version joined the parts on a raw ``#`` and folded path separators to their
    full-width forms, and an adversarial pass proved both halves collide over
    real HTTP::

        ("현시도.xlsx", "둔산네거리#부속")  ->  "현시도.xlsx#둔산네거리#부속"
        ("현시도.xlsx#둔산네거리", "부속")  ->  "현시도.xlsx#둔산네거리#부속"

    — one review id for two different intersections, and the second upload
    silently overwrote the first's proposal (``replaced: true``). The fold had
    the same shape (``가/나`` and ``가／나`` folding together).

    So each part is percent-escaped over :data:`_KEY_ESCAPES` — the RFC 3986
    device, used here for its property rather than its provenance: the delimiter
    cannot occur inside an escaped part, and every escape is reversible, so
    distinct tuples produce distinct keys. Korean text is untouched; only the
    four ASCII characters change.
    """
    encoded: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        for character, replacement in _KEY_ESCAPES:
            text = text.replace(character, replacement)
        encoded.append(text)
    # Empty parts are kept as empty segments rather than dropped: dropping one
    # would make ("", "가") and ("가",) the same key, which is the very
    # collision this function exists to rule out.
    return _KEY_DELIMITER.join(encoded)


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
    #: unit #52 — which of the three input paths made this review. Defaults to
    #: the original one, so every record written before #52 reads back unchanged.
    medium: ReviewMedium = "pdf_band"
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    page_no: int | None = Field(default=None, ge=1, description="1-based, as a human counts pages.")
    page_width_pt: float | None = None
    page_height_pt: float | None = None
    #: (left, bottom, right, top) in PDF points, PDF axes (y up).
    crop_pt: tuple[float, float, float, float] | None = None
    render_scale: float | None = Field(default=None, gt=0)
    image_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    image_width_px: int | None = Field(default=None, gt=0)
    image_height_px: int | None = Field(default=None, gt=0)
    renderer: str
    renderer_version: str
    rendered_at: str
    labels: list[LabelBox] = Field(default_factory=list)
    anchors: dict[str, Any] = Field(
        default_factory=dict,
        description="Which text runs the crop was cut between, and where they were found.",
    )

    @model_validator(mode="after")
    def _image_fields_match_the_medium(self) -> "DiagramRender":
        """A pdf band HAS an image; the other two media have none, and may not
        borrow one. Both directions are enforced, because either shape of drift
        breaks the attestation's binding: a band with no sha cannot be confirmed
        against anything, and a no-image review carrying a sha would let a
        confirmation claim a drawing that does not exist."""
        image_fields = {
            "page_no": self.page_no,
            "crop_pt": self.crop_pt,
            "render_scale": self.render_scale,
            "image_sha256": self.image_sha256,
            "image_width_px": self.image_width_px,
            "image_height_px": self.image_height_px,
        }
        if self.medium == "pdf_band":
            missing = sorted(name for name, value in image_fields.items() if value is None)
            if missing:
                raise ValueError(
                    f"a pdf_band render is missing {missing}. The band IS the image, and an "
                    "attestation is bound to its sha."
                )
        else:
            present = sorted(name for name, value in image_fields.items() if value is not None)
            if present:
                raise ValueError(
                    f"a {self.medium} review carries no image, but {present} were set. Never "
                    "fabricate one: the review screen names the absence instead."
                )
        return self

    @property
    def has_image(self) -> bool:
        return self.image_sha256 is not None

    def label_texts(self) -> list[str]:
        return [label.text for label in self.labels]


def manual_render(
    *,
    medium: ReviewMedium,
    source_file: str,
    source_sha256: str,
) -> DiagramRender:
    """The provenance record for a review that has NO diagram image (#52).

    ``source_sha256`` is the bytes the review came from — the uploaded workbook
    for ``excel_upload``, and for ``blank_entry`` the digest of the name a human
    typed, which is the only thing that review is made of. Both are real digests
    of real bytes: the staleness comparison stays falsifiable, it simply has one
    side (the image) that this medium never had.
    """
    if medium == "pdf_band":
        raise PhaseDiagramRefused(
            "phase_diagram_medium_invalid",
            ["manual_render() builds the no-image variants; a pdf band comes from the renderer."],
        )
    return DiagramRender(
        medium=medium,
        source_file=source_file,
        source_sha256=source_sha256,
        page_width_pt=None,
        page_height_pt=None,
        renderer=NO_RENDERER,
        renderer_version=NO_RENDERER,
        rendered_at=_now_iso(),
    )


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
    """A READING of one 현시도. An artifact, not a fact.

    Carries its own provenance (``model``/``uploaded_by``, ``image_sha256``,
    ``proposed_at``) so it can be argued with. Nothing in this class asserts
    correctness, and no consumer may render it as though it did — the only field
    that means "this mapping is right" lives on :class:`MappingAttestation`.

    **Two sources, one status (unit #52).** ``vision`` is a model reading a
    rendered band. ``excel_upload`` is a person's spreadsheet, and it is a
    proposal for exactly the same reason: nothing this app holds can check
    either, so both wait for a human's 확인 on the confirm form. ``uploaded_by``
    names who supplied the sheet — PROVENANCE, and the review screen must render
    it as such and never beside the word 확인.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prism.phase_diagram_proposal.v1"] = PROPOSAL_DOC_KIND
    source: Literal["vision", "excel_upload"] = "vision"
    uploaded_by: str | None = Field(
        default=None,
        description="Who uploaded the sheet this reading came from. Provenance, never attestation.",
    )
    outcome: Literal["proposed", "refused"]
    refusal: ProposalRefusal | None = None
    read_intersection_no: str | None = Field(
        default=None, description="교차로번호 as the model reads it OFF THE IMAGE."
    )
    read_intersection_name: str | None = None
    approaches: list[ProposedApproach] = Field(default_factory=list)
    phases: list[ProposedPhase] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    #: The model that read the image — ``None`` exactly when no model did.
    #: An uploaded spreadsheet must not put a person's name in this field: it
    #: would read, everywhere it is displayed, as "a model called X said so".
    model: str | None = Field(default=None, min_length=1)
    image_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    source_file: str = Field(min_length=1)
    proposed_at: str
    usage: dict[str, Any] = Field(
        default_factory=dict, description="Token counts the provider reported, when it did."
    )

    @model_validator(mode="after")
    def _provenance_matches_the_source(self) -> "PhaseProposal":
        """Each source carries its OWN provenance, and never the other's."""
        if self.source == "vision":
            if not self.model:
                raise ValueError(
                    "A vision proposal must name the model that produced it. A reading whose "
                    "author is unknown cannot be argued with."
                )
            if not self.image_sha256:
                raise ValueError(
                    "A vision proposal is bound to the image it read. Without that sha nobody "
                    "can tell which drawing it is about."
                )
            if self.uploaded_by:
                raise ValueError(
                    "uploaded_by belongs to an uploaded sheet. A model's reading has no uploader, "
                    "and putting a person there would make a machine's artifact look human-signed."
                )
        else:
            if self.model:
                raise ValueError(
                    "An uploaded sheet was not read by a model, so `model` must be empty."
                )
            if self.image_sha256:
                raise ValueError(
                    "An uploaded sheet has no diagram image. Never bind one to a sha it does not "
                    "have — that is what makes the attestation's image binding meaningful."
                )
            if not str(self.uploaded_by or "").strip():
                raise ValueError(
                    "An uploaded sheet records WHO uploaded it. An anonymous upload is a claim "
                    "with nobody behind it."
                )
        return self

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
#: unit #52 — an uploaded spreadsheet (or a blank entry) has no text layer and no
#: image, so there is NOTHING to compare it against. The review still carries a
#: cross-check document saying exactly that, because "no findings" and "not
#: checked" must never be the same shape on the screen.
CHECK_NO_TEXT_LAYER = "phase_diagram_no_text_layer_to_check"
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


def cross_check_without_text_layer(*, medium: ReviewMedium) -> CrossCheckResult:
    """The cross-check document for a review with no sheet text (unit #52).

    Running :func:`cross_check` here would be worse than useless: with
    ``sheet=None`` it FLAGS *"the text layer produced no phases"*, which on an
    uploaded spreadsheet is not a finding about the data — it is this app
    describing a document that was never supposed to exist. So the review gets
    one honest observation instead, and the reviewer is told that the machine
    compared nothing at all.
    """
    return CrossCheckResult(
        findings=[
            CrossCheckFinding(
                code=CHECK_NO_TEXT_LAYER,
                severity="observation",
                message=(
                    "이 검토에는 대조할 시트 텍스트도 도면 이미지도 없습니다. 기계가 대조한 "
                    "것은 아무것도 없으며, 표의 내용은 올린 사람의 진술입니다."
                ),
                message_en=(
                    "This review has neither sheet text nor a diagram image, so the machine "
                    "compared nothing at all. The table is the uploader's claim."
                ),
                context={"medium": medium},
            )
        ]
    )


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
    #: The rendered band the human looked at — ``None`` exactly when the review
    #: HAS no image (unit #52's excel/blank media). Which of the two it is, is
    #: not this record's word to take: ``PhaseDiagramStore.put_attestation``
    #: compares against the render on disk and refuses either mismatch, so a
    #: confirmation can neither drop an image that exists nor claim one that
    #: does not.
    image_sha256: str | None = Field(default=None, min_length=64, max_length=64)
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
        self, *, source_sha256: str, image_sha256: str | None
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
            attested = "이미지 없음" if self.image_sha256 is None else f"{self.image_sha256[:12]}…"
            reasons.append(
                f"현시도 이미지가 확인 이후 다시 렌더링되었습니다 "
                f"(확인 당시 {attested}, 현재 {image_sha256[:12]}…). 재확인 필요."
            )
        elif image_sha256 is None and self.image_sha256 is not None:
            # The other direction, which #52 made reachable: a review that used
            # to carry a band no longer does. Saying nothing here would carry a
            # confirmation of a drawing forward onto a review with no drawing.
            reasons.append(
                f"확인 당시에는 현시도 이미지({self.image_sha256[:12]}…)가 있었으나 지금은 "
                "이 검토에 이미지가 없습니다. 재확인 필요."
            )
        return reasons


# ---------------------------------------------------------------------------
# Stage 5 — emit. The ONLY road from a confirmation to a signals document.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfirmedMapping:
    """One node's confirmed mapping, and the timing its plan is built from.

    ``attestation`` has **no default and is never optional**. That is the whole
    enforcement: :func:`build_signals_document` takes these and nothing else, so
    there is no argument anywhere on the emit path that a proposal could be
    passed as, and therefore no flag that could turn confirmation off. A caller
    holding only a proposal cannot construct this value.

    **Two plan sources, one confirmation (unit #52).** Exactly one of
    ``sheet_text`` (a 대전 sheet, parsed by ``signals_daejeon``) and
    ``declared_plan`` (the timing a reviewer typed into the 신호시간 sheet,
    converted by ``signals_declared``) must be present. Neither is "the good
    one": both produce a ``ProgramSourceKind.DECLARED`` program, both take the
    amber from THIS attestation, and both are inert without it. What must never
    happen is a typed plan travelling as a sheet — the ``daejeon_sheet`` branch
    parses ``sheet_text``, so that would mean fabricating a sheet.
    """

    attestation: MappingAttestation
    sheet_text: str = ""
    declared_plan: dict[str, Any] | None = None

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
        has_sheet = bool(str(self.sheet_text or "").strip())
        has_declared = bool(self.declared_plan)
        if has_sheet and has_declared:
            raise PhaseDiagramRefused(
                "phase_diagram_two_plan_sources",
                [
                    f"{self.attestation.source_file}: this confirmation carries BOTH a sheet and "
                    "a typed plan. One node has one plan; two would mean one silently wins."
                ],
            )
        if not has_sheet and not has_declared:
            raise PhaseDiagramRefused(
                "phase_diagram_no_sheet_text",
                [
                    f"{self.attestation.source_file}: the sheet's extracted text is missing, so "
                    "the daejeon_sheet plan route has nothing to parse."
                ],
            )

    def to_assignment(self) -> dict[str, Any]:
        """This confirmation as one ``side_inputs.SignalAssignment`` payload.

        The amber on both branches is the CONFIRMED one — a reviewer's
        declaration recorded on the attestation, never the number that happened
        to be in the upload.
        """
        attestation = self.attestation
        if self.declared_plan:
            plan: dict[str, Any] = {
                "source": "excel_upload",
                "source_file": attestation.source_file,
                "intersection_name": str(
                    self.declared_plan.get("intersection_name") or attestation.source_file
                ),
                "intersection_no": self.declared_plan.get("intersection_no"),
                "level_no": attestation.level_no,
                "plan_id": attestation.plan_id,
                "cycle_s": self.declared_plan.get("cycle_s"),
                # Carried, not applied — and carried at all only because an
                # adversarial pass caught this field silently becoming None
                # between the sheet and the rebuilt program.
                "offset_s": self.declared_plan.get("offset_s"),
                "time_of_day": self.declared_plan.get("time_of_day"),
                "phases": list(self.declared_plan.get("phases") or []),
                "declared_amber_s": attestation.declared_amber_s,
                "amber_declaration_note": attestation.amber_declaration_note,
            }
        else:
            plan = {
                "source": "daejeon_sheet",
                "sheet_text": self.sheet_text,
                "source_file": attestation.source_file,
                "level_no": attestation.level_no,
                "plan_id": attestation.plan_id,
                "declared_amber_s": attestation.declared_amber_s,
                "amber_declaration_note": attestation.amber_declaration_note,
            }
        return {
            "node_id": attestation.node_id,
            "plan": plan,
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

    UNIT #62 -- ``limit`` used to cut ``ranked[:limit]`` unconditionally, which
    is silent-cap doctrine violated: an OSM draft names every node "Node
    <osmid>", so `_name_overlap` scores every single one 0.0, the sort falls
    through to node_id, and the caller offered the 8 lowest-id nodes while
    hiding the rest with no signal that anything was cut (MEASURED: a 25-node
    draft offering 8, 17 unofferable). When the name signal cannot discriminate
    at all -- an empty `intersection_name`, or every candidate landing at 0.0 --
    a ranking is not actually being applied, so truncating on it would hide
    real nodes behind a coin flip on node_id. Return everyone in that case; the
    caller (`routers/genesis.py`) still discloses `total_nodes`/`returned_nodes`
    so a real truncation, when the name signal DOES discriminate, is nameable
    on screen instead of silently absorbed.

    ROUND 5 (refuter, MEDIUM, round 4): a REAL name signal always outranks
    every hint-tier entry in the returned order, regardless of either tier's
    own score formula -- sorted by `real_signal` first, so a digit-sibling's
    0.6 can never list above a weak true containment's 0.8x1/N again.
    """
    from .signals import node_approaches  # local: avoids a heavy import at module load

    target = _normalise_label(intersection_name or "")
    ranked: list[tuple[float, bool, dict[str, Any]]] = []
    for node in getattr(draft, "nodes", []):
        name = _normalise_label(getattr(node, "name", "") or "")
        score, reason, real_signal = _name_overlap(target, name)
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
                real_signal,
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
    # ROUND 5 FIX: real_signal FIRST, score second. MEASURED (refuter): tier
    # 2.5's 0.6 can exceed a weak true containment's 0.8*shorter/longer when
    # the containment match is between very disparate lengths -- sorting on
    # score alone let a digit-sibling list ABOVE the correct node. Sorting on
    # `-real_signal` first makes this true independent of either tier's own
    # formula, current or future: no hint-tier entry can ever outrank a real
    # one, only tie-break among entries that agree on realness.
    ranked.sort(key=lambda item: (-int(item[1]), -item[0], item[2]["node_id"]))
    entries = [entry for _score, _real_signal, entry in ranked]
    # FINDING 1's fix, applied here: a tier that is not a REAL signal (the
    # fallback's coincidental character overlap) can never flip this true, no
    # matter how many nodes it happens to score above 0.0.
    discriminates = any(real_signal for _score, real_signal, _entry in ranked)
    if not discriminates or len(entries) <= limit:
        return entries
    return entries[:limit]


def _name_overlap(target: str, candidate: str) -> tuple[float, str, bool]:
    """A deliberately dumb similarity, its reason, and whether it is a REAL
    name signal -- the third value ``node_candidates`` uses to decide whether
    its ranking is allowed to truncate at all.

    UNIT #62 FINDING 1 (refuter, BLOCKER, round 2): the fallback tier's raw
    character overlap counted a shared DIGIT as signal -- a 교차로명 carrying
    a route number, e.g. "3번국도교차로", scored positive against an OSM
    draft's "Node 318918018" purely because both strings contain the digit
    "3". That flipped `node_candidates`'s `discriminates` true on pure noise,
    the silent 8-node cap re-activated, and the CORRECT node -- which
    legitimately scores 0.0, having no OSM name overlap with the 교차로명 at
    all -- was excluded from the top 8 by garbage matches that merely
    happened to share a digit.

    ROUND 3 FINDING A (refuter, HIGH -- CLASS CLOSURE): round 2's fix only
    excluded digits from the FALLBACK tier. Tier 2 (substring containment)
    was left completely unconditional, so an all-digit `intersection_name`
    such as "18" (typed through the manual blank-review or excel_upload
    doors, not just read off an OSM draft) is a literal substring of
    "node<osmid>" for MOST osmids -- MEASURED: a 25-node draft returned only
    8. Round 3's fix ran the containment check itself on DIGIT-STRIPPED
    strings, with a non-empty-residue guard on both sides.

    ROUND 4 (orchestrator-decided semantics, following the round-3 refuter's
    ONE surviving HIGH): running containment on STRIPPED strings was itself
    the wrong move -- it PROMOTES digit siblings to full name matches.
    MEASURED: "원신흥4" vs "원신흥네거리" scored real_signal=True (a
    containment relationship that does not exist on the real, unstripped
    strings at all), and against target "신흥들3" the four DISTINCT physical
    intersections "신흥들1"/"2"/"4"/"5" all scored an identical 0.8 "포함됩니다"
    tie -- erasing the exact digit that disambiguates separate intersections
    in this product's own Daejeon naming convention.

    THE FIX, three tiers now instead of two:

      1. TIER 1 (exact) and TIER 2 (containment) are back to the RAW,
         UNSTRIPPED strings -- containment must be a containment relationship
         that genuinely exists in the names as written. The round-3
         non-empty-residue guard STAYS ahead of tier 2 (not folded into the
         containment test itself): an all-digit target/candidate still blocks
         it outright, because ITS residue is empty regardless of what the raw
         strings happen to contain (round 2's hole stays closed).
      2. TIER 2.5, NEW -- DIGIT-SIBLING. Reached only once tier 1 and tier 2
         have BOTH failed on the raw strings. If the DIGIT-STRIPPED strings
         satisfy containment (or are outright equal, a special case of
         containment) with a non-empty residue on both sides, the two names
         differ ONLY in digit content -- e.g. "신흥들3" vs "신흥들4", or a
         digit embedded mid-name ("가상3거리" vs "가상거리"). That is real
         information for a HUMAN reviewer (worth ranking above pure noise,
         with its own honest reason) but it is explicitly NOT a name match:
         `real_signal` is False, so a screen full of digit siblings can never
         activate the truncation cap the way a screen full of genuine
         containment matches would.
      3. TIER 3 (fallback) is unchanged in spirit -- stripped character
         overlap, never real signal -- and MUST still rank BELOW tier 2.5 for
         the same pair: stripped CONTAINMENT (every character of the shorter
         name appears, in order, inside the longer) is strictly more
         information than stripped SET overlap (the same characters merely
         both appear somewhere). Tier 2.5's score formula is the SAME
         char-overlap-ratio shape tier 3 uses -- `common / max(distinct
         counts)` -- but at a HIGHER coefficient (0.6 vs 0.4). Because
         containment guarantees the shorter stripped string's ENTIRE
         character set is a subset of the longer's, tier 2.5's ratio for a
         given pair is EXACTLY what tier 3's ratio would be for that same
         pair -- so the higher coefficient alone is what must, and provably
         does, rank it above (1.5x, structurally, not just empirically).

    DECISION, recorded (the refuter asked for one): an ALL-DIGIT EXACT match
    (tier 1) still counts as real signal -- e.g. target "18" == candidate
    "18". Both strings committed to their FULL name and it happened to be
    entirely digits; unlike containment, digit-sibling, or the fallback's
    partial overlap, there is no shorter fragment doing the work that could
    be "just a shared osmid fragment" -- an exact string match is the
    strongest signal this function can produce regardless of alphabet, and a
    real 교차로명 that happens to be a bare route/교차로번호 is unusual but
    not invalid.

    The tier-1/2/3 reason STRINGS are unchanged -- they are user-facing
    (`match_reason` on the wire). Tier 2.5 is a genuinely NEW reason, because
    it is a genuinely new fact: not a name match, not pure noise, a sibling.
    """
    if not target or not candidate:
        return 0.0, "이름 없음 — 이름으로는 비교할 수 없습니다.", False
    if target == candidate:
        # DECISION (see docstring): an all-digit exact match still counts.
        return 1.0, "이름이 정확히 일치합니다.", True
    digits = set("0123456789")
    target_stripped = "".join(ch for ch in target if ch not in digits)
    candidate_stripped = "".join(ch for ch in candidate if ch not in digits)
    # TIER 2 -- containment on the RAW strings (round 4: reverted from round
    # 3's stripped version, which promoted digit siblings into full matches).
    # The residue guard stays: an all-digit side blocks this outright, no
    # matter what the raw strings contain -- this is what keeps round 2's
    # hole (target "18" "containing" an osmid) closed without needing to
    # strip the containment test itself.
    if target_stripped and candidate_stripped and (target in candidate or candidate in target):
        shorter, longer = sorted((len(target), len(candidate)))
        return 0.8 * shorter / longer, "한쪽 이름이 다른 쪽에 포함됩니다.", True
    # TIER 2.5 -- DIGIT-SIBLING (new, round 4). Raw containment/exact just
    # failed, but the digit-stripped strings satisfy containment (equality
    # counts: "X in X" is trivially true). The names differ ONLY in digits.
    if (
        target_stripped
        and candidate_stripped
        and (target_stripped in candidate_stripped or candidate_stripped in target_stripped)
    ):
        common = set(target_stripped) & set(candidate_stripped)
        return (
            # SAME ratio shape as tier 3 below (common / max distinct count),
            # at a higher coefficient -- see the docstring's proof that this
            # is provably, not just empirically, above tier 3 for this pair.
            0.6 * len(common) / max(len(set(target_stripped)), len(set(candidate_stripped))),
            "숫자만 다른 이름입니다 — 같은 계열의 다른 번호일 수 있습니다.",
            False,
        )
    common = set(target_stripped) & set(candidate_stripped)
    if not common:
        return 0.0, "공통 글자가 없습니다.", False
    return (
        0.4 * len(common) / max(len(set(target_stripped)), len(set(candidate_stripped))),
        f"공통 글자 {len(common)}자.",
        False,
    )


# ---------------------------------------------------------------------------
# unit #58 — amending a timing value is the uploader amending their own record
# ---------------------------------------------------------------------------
#
# THE USER'S DECISION (docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도
# 카드 단순화 — 도식+시간표, 짧은 확인 (#58)"): the card renders the review's
# stored 신호 시간 as a table, and "황색시간 및 시간 같은거가 수정가능하게".
#
# The doctrine reconciliation recorded there, restated where it is enforced:
# editing a timing value on screen is **the uploader amending their own
# statement** — the same authorship rule #55 gave the upload itself — so the
# server stamps who and when, exactly as it stamps an attestation's attester.
# Nothing here is a machine's reading, and nothing here weakens a refusal: an
# amendment writes a number a person typed, names them, and keeps every previous
# value in :attr:`TimingAmendments.history` rather than overwriting and
# forgetting.
#
# WHAT IS REFUSED, BY NAME:
#   * amending a ``pdf_band`` review's timing — those numbers were read out of a
#     CLIENT's sheet text layer, and rewriting them on screen would edit the
#     client's document rather than the person's own record;
#   * amending a (수준, 계획) or a 현시 the review does not carry — an amendment
#     never CREATES a plan or a phase, because a plan nobody typed is a plan
#     nobody can be held to;
#   * a non-positive 황색시간, and a non-numeric value in a numeric field.

#: Which scope each amendable field belongs to. A closed vocabulary: a field
#: outside these three sets cannot be amended at all, and the route's own
#: ``Literal`` is built from them so the two cannot drift.
AMENDABLE_REVIEW_FIELDS: tuple[str, ...] = ("declared_amber_s",)
AMENDABLE_PLAN_FIELDS: tuple[str, ...] = ("cycle_s", "offset_s", "time_of_day")
AMENDABLE_PHASE_FIELDS: tuple[str, ...] = (
    "green_s",
    "clearance_s",
    "min_green_s",
    "max_green_s",
)
#: The fields that may never be cleared: a 현시 with no green and no 소거 is not
#: a phase, and ``signals_declared`` would refuse it one layer down anyway.
_REQUIRED_PHASE_FIELDS: frozenset[str] = frozenset({"green_s", "clearance_s"})
#: The one amendable field that is TEXT. Everything else is a number of seconds.
_TEXT_FIELDS: frozenset[str] = frozenset({"time_of_day"})

AmendableField = Literal[
    "declared_amber_s",
    "cycle_s",
    "offset_s",
    "time_of_day",
    "green_s",
    "clearance_s",
    "min_green_s",
    "max_green_s",
]


class TimingAmendment(BaseModel):
    """One value a named person changed, kept forever.

    ``was``/``now`` are STRINGS for the same reason ``MappingChange``'s are: the
    ledger is read by a human months later, and ``"없음"`` beside ``"3.0"`` says
    what a ``null`` beside a float does not.
    """

    model_config = ConfigDict(extra="forbid")

    at: str = Field(min_length=1, description="The SERVER's clock, never the client's.")
    by: str = Field(min_length=1, description="The signed-in username.")
    field: AmendableField
    level_no: int | None = None
    plan_id: int | None = None
    phase_id: str | None = None
    was: str
    now: str


class TimingAmendments(BaseModel):
    """One review's amendment ledger, and the declared 황색시간 it is the home of.

    ``declared_amber_s`` lives here rather than on the uploaded record because it
    is not something any sheet supplies — see :data:`AMENDMENT_FILENAME`. When
    nobody has declared one, this document either does not exist or carries
    ``None``, and :meth:`PhaseDiagramStore.sheet_summary` falls back to whatever
    the WORKBOOK said, still labelled as the workbook's number.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prism.phase_diagram_amendments.v1"] = AMENDMENT_DOC_KIND
    declared_amber_s: float | None = Field(default=None, gt=0)
    declared_by: str | None = None
    declared_at: str | None = None
    history: list[TimingAmendment] = Field(default_factory=list)
    #: Set when a PREVIOUS ledger for this review did not parse and was moved
    #: aside. It is carried on the FRESH ledger, and it is STICKY: subsequent
    #: amendments append to a history that is still missing everything the
    #: quarantined file held, so the disclosure outlives the next edit. Silent
    #: healing — a reviewer shown a clean, short history with no hint that an
    #: older one was lost — is the failure this field exists to prevent, and it
    #: is the same discipline ``demand_target_map`` and
    #: ``calibration_attestation`` already keep for their own registries.
    quarantined: bool = False
    quarantine_path: str | None = None
    quarantined_at: str | None = None


def amber_declaration_sentence(*, by: str, at: str, was: str) -> str:
    """The provenance of a hand-declared 황색시간, composed HERE.

    The browser never writes this sentence. A note the client composed would be
    a claim about who declared what, authored by the party that cannot be
    trusted to make it — the same reason ``attested_by`` is taken from the
    session and not from the body.
    """
    return f"{by}님이 {at}에 화면에서 선언한 황색시간입니다 (이전 값: {was})."


def amendment_quarantine_payload(ledger: TimingAmendments) -> dict[str, Any] | None:
    """The quarantine disclosure for a browser, or ``None`` when healthy.

    Trilingual, and composed HERE rather than on the screen, for the reason
    ``demand_target_map.registry_health_status`` gives: this sentence states
    what a machine did to a file, and the party that did it is the one that
    gets to describe it. The screen prints its own short heading beside this
    text; it never rewrites it.
    """
    if not ledger.quarantined:
        return None
    where = ledger.quarantine_path or "(경로 없음)"
    return {
        "quarantined": True,
        "quarantine_path": ledger.quarantine_path,
        "quarantined_at": ledger.quarantined_at,
        "message": {
            "ko": (
                "이 검토의 수정 기록 파일이 손상되어 따로 보관했고, 기록은 빈 상태에서 다시 "
                f"시작되었습니다. 이전 수정 내역은 이 화면에 없습니다 — 원본 바이트는 {where} "
                "에 그대로 있습니다."
            ),
            "en": (
                "This review's amendment ledger was corrupt: it has been quarantined and a fresh "
                "one started. The earlier amendment history is NOT on this screen — the original "
                f"bytes are preserved at {where}."
            ),
            "ja": (
                "この検討の修正記録ファイルが破損していたため隔離し、記録は空から再開しました。"
                f"以前の修正履歴はこの画面にありません — 元のバイトは {where} にそのまま保存され"
                "ています。"
            ),
        },
    }


def _amendment_text(value: Any) -> str:
    """One amended value as the ledger prints it. ``None`` is 없음, never blank."""
    return "없음" if value is None else str(value)


def cross_check_compared(
    result: "CrossCheckResult | None", *, medium: ReviewMedium
) -> bool:
    """Did the machine actually compare anything for this review?

    THE USER'S DECISION (docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정:
    현시도 카드 단순화 … (#58)"): a section whose entire content is *"대조할 시트
    텍스트도 도면 이미지도 없습니다"* is noise — "이건 필요없지 않아? 지워".

    The reconciliation recorded there, enforced here: the answer is FALSE only
    where the machine genuinely compared nothing. ``excel_upload`` and
    ``blank_entry`` reviews have neither a sheet text layer nor a rendered band,
    so there was nothing on the other side of any comparison. A ``pdf_band``
    review keeps its cross-check unchanged, flags and all — *deleting a
    comparison that happened would be hiding evidence; deleting the sentence
    that no comparison happened is removing noise.*

    The SERVER answers this, and the screen renders the answer. A browser
    deciding for itself which sections to hide is how a UI starts editing what
    the machine said.
    """
    if result is None:
        return False
    return medium == "pdf_band"


# ---------------------------------------------------------------------------
# The store — one directory per sheet, on the workspace's own tree
# ---------------------------------------------------------------------------
#
# CONCURRENCY (unit #58, from the refuter's live reproduction). Until this
# unit, ``PhaseDiagramStore`` was the ONLY read-modify-write store in this
# codebase with no lock at all — every sibling (``demand_target_map``,
# ``calibration_attestation``, ``observations``, ``datasets``,
# ``genesis_jobs``, ``simulation_runs``, ``workspaces``) holds unit U6's module
# ``RLock`` across read → mutate → write.
#
# WHAT WAS MEASURED, and it is worse than a lost number: two threads amending
# DIFFERENT fields of the same review interleaved their read-modify-write of
# ``manual-sheet.json``, and one field's value silently reverted — while BOTH
# amendments were recorded in the ledger with a ``was → now`` that looked
# honest. An audit trail that asserts a change which never landed is the exact
# failure this whole module exists to prevent, so the lock is not a performance
# detail here; it is what makes the ledger true.
#
# SCOPE, stated honestly. The lock is held across EVERY WRITE PATH of the
# store, not only ``amend_timing``:
#
#   * ``amend_timing`` and its helpers — the measured race, two files deep
#     (``manual-sheet.json`` plus the ledger);
#   * ``put_manual_review`` — a re-upload REPLACES the very file an amendment
#     is mid-cycle on, so the two race against each other and not merely each
#     against itself;
#   * ``put_render`` / ``put_proposal`` / ``put_attestation`` — whole-file
#     writes today, but they share a review directory with the above, and
#     ``put_attestation`` reads the render, checks it and then writes, which is
#     the same read-check-write shape.
#
# The lock is MODULE level, not per-instance, because ``_phase_diagram_store()``
# builds a fresh ``PhaseDiagramStore`` on every request — a lock on the instance
# would be a lock two callers never share. ``RLock`` because these methods nest
# (``amend_timing`` → ``_append_amendment`` → ``_write_amendments``).
#
# It is a lock, not a distributed one: it serialises threads inside ONE process,
# which is what this deployment is. ``_write_json``'s temp-file + fsync +
# ``os.replace`` below is the other half — it stops a concurrent READER (which
# takes no lock, deliberately, so a browser refresh is never blocked by a write)
# seeing a half-written file.
_STORE_LOCK = threading.RLock()


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
        with _STORE_LOCK:
            directory = self.review_dir(review_id)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / IMAGE_FILENAME).write_bytes(png)
            _write_json(directory / RENDER_FILENAME, render.model_dump(mode="json"))
            (directory / SHEET_TEXT_FILENAME).write_text(
                sheet_text, encoding="utf-8", newline=""
            )
        return review_id

    def put_manual_review(
        self,
        review_id: str,
        render: DiagramRender,
        *,
        sheet: dict[str, Any],
    ) -> str:
        """Store one review that has NO diagram image (unit #52).

        Same directory, same file names, one difference: there is no PNG and no
        ``sheet.txt``, because neither exists. What takes the sheet text's place
        is :data:`MANUAL_SHEET_FILENAME` — the intersection's name, its number if
        somebody typed one, and the phase ids the upload (or the human) named. It
        is deliberately NOT called a parse: nothing was parsed.

        Refuses a ``pdf_band`` render, which belongs to :meth:`put_render` and
        carries an image this method would not write.
        """
        if render.medium == "pdf_band":
            raise PhaseDiagramRefused(
                "phase_diagram_medium_invalid",
                ["A pdf_band review is stored with its image, through put_render()."],
            )
        with _STORE_LOCK:
            directory = self.review_dir(review_id)
            directory.mkdir(parents=True, exist_ok=True)
            _write_json(directory / RENDER_FILENAME, render.model_dump(mode="json"))
            _write_json(
                directory / MANUAL_SHEET_FILENAME, {"kind": MANUAL_SHEET_DOC_KIND, **sheet}
            )
        return review_id

    def put_proposal(
        self, review_id: str, proposal: PhaseProposal, cross: CrossCheckResult
    ) -> None:
        with _STORE_LOCK:
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
        with _STORE_LOCK:
            render = self.render(review_id)
            if attestation.image_sha256 != render.image_sha256:
                attested = (
                    "이미지 없음"
                    if attestation.image_sha256 is None
                    else attestation.image_sha256[:12] + "…"
                )
                stored = (
                    "이미지 없음" if render.image_sha256 is None else render.image_sha256[:12] + "…"
                )
                raise PhaseDiagramRefused(
                    "phase_diagram_attestation_stale",
                    [
                        "확인하려는 현시도 이미지가 현재 저장된 이미지와 다릅니다. 화면을 새로 "
                        "고친 뒤 다시 확인하십시오 (재확인 필요).",
                        f"attested against {attested}, stored image is {stored}",
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
            summary = self.sheet_summary(review_id)
            self._refuse_unknown_plan(review_id, attestation)
            self._refuse_stale_amber(review_id, attestation, summary=summary)
            directory = self.review_dir(review_id)
            directory.mkdir(parents=True, exist_ok=True)
            _write_json(directory / ATTESTATION_FILENAME, attestation.model_dump(mode="json"))

    def _refuse_unknown_plan(self, review_id: str, attestation: MappingAttestation) -> None:
        """A confirmation may only name a (수준, 계획 ID) this review CARRIES.

        WHAT ALREADY FIRED, measured before this was added: a pair with no
        timing behind it was accepted here and refused later, at
        ``confirmed_mapping`` → ``phase_diagram_no_sheet_text``, which names the
        plans that do exist. That is the right refusal in the wrong PLACE — it
        arrives at the staging button, long after the person who chose the plan
        has left the screen, and until then the review reads 확인됨.

        So the same question is asked at signing time, by name. A review that
        carries NO timing at all is deliberately not checked: its 수준/계획 are
        typed by hand precisely because nothing measured them, and the staging
        refusal remains the honest place to say the timing is missing.

        The hole this does NOT close, stated so nobody reads it as closed: a
        client that submits a DIFFERENT plan that also exists. Nothing on this
        server can tell that from the intended one — there is no second source
        for what the screen was showing. That half is held by the contract pin
        on the payload's own construction (tests/dashboard-contract.test.ts,
        "unit #58: the confirmation's 수준/계획 is the one on screen").
        """
        plans = self.timing_plans(review_id)
        if not plans:
            return
        pairs = {(int(plan["level_no"]), int(plan["plan_id"])) for plan in plans}
        if (int(attestation.level_no), int(attestation.plan_id)) in pairs:
            return
        available = ", ".join(f"수준 {level} / 계획 {plan}" for level, plan in sorted(pairs))
        raise PhaseDiagramRefused(
            "phase_diagram_attestation_unknown_plan",
            [
                f"확인하려는 계획(수준 {attestation.level_no} / 계획 {attestation.plan_id})은 이 "
                "교차로에 없습니다. 화면을 새로 고친 뒤, 이 교차로가 실제로 가진 계획으로 다시 "
                "확인하십시오.",
                f"이 교차로가 가진 계획: {available}.",
                f"review {review_id}: attested L{attestation.level_no}-P{attestation.plan_id}, "
                f"stored plans {sorted(pairs)}.",
            ],
        )

    def _refuse_stale_amber(
        self, review_id: str, attestation: MappingAttestation, *, summary: Mapping[str, Any]
    ) -> None:
        """The signed 황색시간 must be the one the store held at signing time.

        Since unit #58 the card has no separate 선언 황색시간 box: the number
        lives in the 신호 시간 table, is amended through a stamped PATCH, and is
        read back off the stored review. So a confirmation whose amber differs
        from the stored one was made against a number that is no longer there —
        the same species as the image-sha staleness above (#53's idiom), and it
        is refused the same way rather than silently signing the client's copy.

        Only the NUMBER is compared. ``amber_declaration_note`` is free text on
        the un-amended path (a workbook's own 근거) and server-composed on the
        amended one, so comparing it would refuse honest callers over a sentence.
        """
        stored = summary.get("declared_amber_s")
        attested = attestation.declared_amber_s
        if (stored is None) == (attested is None):
            if stored is None:
                return
            if math.isclose(float(stored), float(attested), rel_tol=0.0, abs_tol=1e-9):
                return
        raise PhaseDiagramRefused(
            "phase_diagram_attestation_stale",
            [
                "확인하려는 황색시간이 현재 저장된 값과 다릅니다. 황색시간은 신호 시간 표에서 "
                "선언하는 값이므로, 화면을 새로 고친 뒤 표의 값으로 다시 확인하십시오 "
                "(재확인 필요).",
                f"attested {'없음' if attested is None else attested}, "
                f"stored {'없음' if stored is None else stored}",
            ],
        )

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

    def manual_sheet(self, review_id: str) -> dict[str, Any] | None:
        """What a no-image review knows about its intersection, or ``None``."""
        path = self.review_dir(review_id) / MANUAL_SHEET_FILENAME
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def amendments(self, review_id: str) -> TimingAmendments:
        """This review's amendment ledger. An absent file is an EMPTY ledger.

        Never ``None``: "nobody has amended anything" and "there is no ledger"
        are the same statement, and a caller that had to tell them apart would
        eventually get it wrong.
        """
        path = self.review_dir(review_id) / AMENDMENT_FILENAME
        if not path.is_file():
            return TimingAmendments()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise PhaseDiagramRefused(
                "phase_diagram_amendments_unreadable",
                [
                    f"검토 {review_id}의 수정 기록 파일을 읽지 못했습니다. 파일은 그대로 두었고 "
                    "아무것도 덮어쓰지 않았습니다.",
                    f"{path}: {type(exc).__name__}: {exc}",
                ],
            ) from exc
        try:
            return TimingAmendments.model_validate(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, ValueError) as exc:
            return self._quarantine_amendments(path, raw, exc)

    def _quarantine_amendments(
        self, path: Path, raw: bytes, exc: Exception
    ) -> TimingAmendments:
        """A ledger that does not parse is MOVED ASIDE, bytes intact, and a
        fresh one starts — the sibling stores' rule (``demand_target_map``,
        ``calibration_attestation``), applied to the file unit #58 added.

        Never a crash: before this, a hand-edited or truncated
        ``amendments.json`` reached the browser as an HTTP 500 with a raw
        ``JSONDecodeError``, which tells a reader nothing and leaves the whole
        review unreadable. Never a silent overwrite either — the corrupt bytes
        are kept next to the review under their own name, because they are the
        only record of what was in the ledger, and an amendment history that can
        be quietly replaced is not a ledger.

        Degrading to an EMPTY ledger is the safe direction HERE specifically:
        with no amendments, the card falls back to what the WORKBOOK said and
        shows no declaration author. The opposite failure — inventing a
        declaration nobody made — is the one that cannot be allowed, and this
        path structurally cannot produce one.
        """
        stamp = _now_iso().replace(":", "").replace("-", "")
        quarantine = path.with_name(f"{path.name}.corrupt-{stamp}-{uuid4().hex[:8]}")
        try:
            with _STORE_LOCK:
                path.replace(quarantine)
        except OSError as move_error:
            raise PhaseDiagramRefused(
                "phase_diagram_amendments_unreadable",
                [
                    "수정 기록 파일이 손상되었으나 따로 보관할 수 없어 아무것도 하지 않았습니다. "
                    "파일은 그대로 있습니다.",
                    f"{path}: {type(exc).__name__}: {exc}",
                    f"quarantine failed: {type(move_error).__name__}: {move_error}",
                ],
            ) from move_error
        logger.error(
            "phase diagram: the amendment ledger at %s did not parse (%s: %s). Its bytes were "
            "moved to %s and a fresh ledger starts. Nothing was overwritten.",
            path,
            type(exc).__name__,
            exc,
            quarantine,
        )
        # The bytes are preserved beside the review, not merely logged.
        quarantine.write_bytes(raw)
        # ...and the FRESH ledger says, on disk and on the wire, that an older
        # one was lost. Written immediately rather than only returned, so the
        # disclosure survives this process and every later read of the review.
        fresh = TimingAmendments(
            quarantined=True,
            quarantine_path=str(quarantine),
            quarantined_at=_now_iso(),
        )
        review_id = path.parent.name
        try:
            with _STORE_LOCK:
                _write_json(path, fresh.model_dump(mode="json"))
        except OSError:  # pragma: no cover - the disclosure still travels in-memory
            logger.error(
                "phase diagram: could not record the quarantine disclosure for review %s; "
                "the corrupt bytes are still at %s.",
                review_id,
                quarantine,
            )
        return fresh

    def timing_plans(self, review_id: str) -> list[dict[str, Any]]:
        """The 신호 시간 this review HOLDS, per (수준, 계획 ID), for the screen.

        ONE source per medium, and each one is the same source that already
        feeds the export and the signals build — the card cannot show a number
        the document would not carry:

        * ``pdf_band`` — derived from the 대전 sheet's own text layer by
          ``phase_template.daejeon_timing_payload`` (the import is deferred
          because ``phase_template`` imports THIS module at load time);
        * ``excel_upload`` / ``blank_entry`` — the rows somebody typed into the
          신호시간 sheet, exactly as :meth:`declared_plan` reads them.

        A refusal on the 대전 path is returned as an empty list rather than
        raised: this is a READ for a screen, and one unparseable timing table
        must not take the whole review's card down with it. The refusal is not
        swallowed silently — ``confirmed_mapping`` still raises it by name on
        the road that matters, which is the one that builds a plan.
        """
        render = self.render(review_id)
        if render.medium == "pdf_band":
            from . import phase_template  # deferred: phase_template imports this module

            try:
                plans, _notes = phase_template.daejeon_timing_payload(self.sheet(review_id))
            except (PhaseDiagramRefused, ValueError):
                return []
            return list(plans)
        return list((self.manual_sheet(review_id) or {}).get("timing") or [])

    def declared_plan(
        self, review_id: str, level_no: int, plan_id: int
    ) -> dict[str, Any] | None:
        """The typed timing for ONE (수준, 계획 ID), or ``None`` (unit #52).

        Looked up by the pair the CONFIRMATION names, not by "the first one on
        file": a reviewer confirming the 첨두 plan must not silently get the
        off-peak numbers because they happened to be typed first.
        """
        stored = self.manual_sheet(review_id) or {}
        for plan in stored.get("timing") or []:
            if int(plan.get("level_no", -1)) == int(level_no) and int(
                plan.get("plan_id", -1)
            ) == int(plan_id):
                return {
                    "intersection_name": stored.get("intersection_name"),
                    "intersection_no": stored.get("intersection_no"),
                    "level_no": int(level_no),
                    "plan_id": int(plan_id),
                    "cycle_s": plan.get("cycle_s"),
                    "offset_s": plan.get("offset_s"),
                    "time_of_day": plan.get("time_of_day"),
                    "phases": list(plan.get("phases") or []),
                }
        return None

    def sheet_summary(self, review_id: str) -> dict[str, Any]:
        """The confirm screen's 'what do we know about this intersection'.

        One shape for all three media, so the screen has one branch fewer — but
        the two halves are honestly different underneath: a pdf review's summary
        is PARSED from the sheet's text layer, and a no-image review's is what a
        person typed or uploaded. ``parsed`` says which.
        """
        render = self.render(review_id)
        if render.medium == "pdf_band":
            return {
                **_sheet_summary(self.sheet(review_id)),
                # unit #58 — the same two keys on every medium. A 대전 sheet
                # supplies no amber of its own (it prints 황색+전적색 as one
                # number), so the ONLY amber a pdf review can carry is one a
                # person declared on screen — which is exactly what this reads.
                #
                # ``timing`` is deliberately NOT added here: it is joined on in
                # :meth:`review_payload`, because a 대전 review's timing costs a
                # second parse of the sheet text and :meth:`index_payload` calls
                # this method once per review just to read 교차로명.
                **self._amber_summary(review_id, uploaded={}),
            }
        stored = self.manual_sheet(review_id) or {}
        return {
            **self._amber_summary(review_id, uploaded=stored),
            "source_file": render.source_file,
            "intersection_name": stored.get("intersection_name"),
            "intersection_no": stored.get("intersection_no"),
            # Nothing was parsed: there is no sheet to parse. Saying `true` here
            # would claim a text layer this review never had.
            "parsed": False,
            "refusal_codes": [],
            "phase_ids": list(stored.get("phase_ids") or []),
            # 수준/계획 come off the 대전 sheet's own TOD table for a pdf review.
            # For this medium they are the (수준, 계획) pairs the 신호시간 sheet
            # actually carries timing for — so the confirm form offers exactly
            # the plans that could become a signals document, and an empty list
            # (no timing typed) makes it ASK for the two numbers instead of
            # offering a choice nobody measured.
            "plan_choices": [
                {"level_no": plan["level_no"], "plan_id": plan["plan_id"]}
                for plan in (stored.get("timing") or [])
            ]
            or list(stored.get("plan_choices") or []),
        }

    def _amber_summary(
        self, review_id: str, *, uploaded: Mapping[str, Any]
    ) -> dict[str, Any]:
        """The 황색시간 this review currently declares, and whose word it is.

        TWO possible authors, and the screen must never confuse them. A person
        who typed the number into the card's 신호 시간 table (unit #58) OUTRANKS
        the value that arrived in the workbook — they amended their own record,
        after the fact, with their name on it. When nobody has amended one, the
        workbook's number stands, still carrying the workbook's own 근거.

        ``amber_declaration_note`` is composed by
        :func:`amber_declaration_sentence` on the amended path rather than
        carried from the client, because it is a statement about WHO declared
        WHAT and WHEN — and the browser is not the party that gets to make it.
        """
        ledger = self.amendments(review_id)
        if ledger.declared_amber_s is not None and ledger.declared_by and ledger.declared_at:
            previous = next(
                (
                    entry.was
                    for entry in reversed(ledger.history)
                    if entry.field == "declared_amber_s"
                ),
                _amendment_text(uploaded.get("declared_amber_s")),
            )
            return {
                "declared_amber_s": ledger.declared_amber_s,
                "amber_declaration_note": amber_declaration_sentence(
                    by=ledger.declared_by, at=ledger.declared_at, was=previous
                ),
                "amber_declared_by": ledger.declared_by,
                "amber_declared_at": ledger.declared_at,
            }
        return {
            # What the UPLOAD said the amber is. A prefill, never a default: the
            # number is still a declaration, and the card's own sentence says so.
            "declared_amber_s": uploaded.get("declared_amber_s"),
            "amber_declaration_note": uploaded.get("amber_declaration_note"),
            "amber_declared_by": None,
            "amber_declared_at": None,
        }

    def amend_timing(
        self,
        review_id: str,
        *,
        field: str,
        value: Any,
        edited_by: str,
        level_no: int | None = None,
        plan_id: int | None = None,
        phase_id: str | None = None,
    ) -> None:
        """Record ONE amended value, stamped, with the previous one kept.

        ``edited_by`` comes from the caller's SESSION and the timestamp from
        this process's clock — there is no parameter for either on the request
        body, the same shape :class:`MappingAttestation` uses and for the same
        reason. Every refusal below names what is wrong and changes nothing.
        """
        editor = (edited_by or "").strip()
        if not editor:
            raise PhaseDiagramRefused(
                "phase_diagram_amendment_unsigned",
                [
                    "수정한 사람의 이름이 없습니다. 이 표의 값은 사람의 진술이므로, 이름 없는 "
                    "수정은 기록하지 않습니다."
                ],
            )
        at = _now_iso()
        # THE WHOLE READ → MUTATE → WRITE UNDER ONE HELD LOCK. Measured by the
        # refuter before this line existed: two threads amending DIFFERENT
        # fields of the same review each read `manual-sheet.json`, each mutated
        # their own copy, and the second write erased the first field's new
        # value — while BOTH ledger entries were recorded with a `was → now`
        # that read as if they had landed. The ledger is the product here, so a
        # ledger entry for a write that was rolled back is worse than the lost
        # number itself.
        with _STORE_LOCK:
            self._amend_timing_locked(
                review_id,
                field=field,
                value=value,
                editor=editor,
                at=at,
                level_no=level_no,
                plan_id=plan_id,
                phase_id=phase_id,
            )

    def _amend_timing_locked(
        self,
        review_id: str,
        *,
        field: str,
        value: Any,
        editor: str,
        at: str,
        level_no: int | None,
        plan_id: int | None,
        phase_id: str | None,
    ) -> None:
        """:meth:`amend_timing`'s body, always called with :data:`_STORE_LOCK` held."""
        if field in AMENDABLE_REVIEW_FIELDS:
            self._amend_declared_amber(review_id, value=value, by=editor, at=at)
            return
        render = self.render(review_id)
        if render.medium == "pdf_band":
            raise PhaseDiagramRefused(
                "phase_diagram_timing_not_amendable",
                [
                    f"{render.source_file}: 이 검토의 신호 시간은 대전 시트의 텍스트에서 읽은 "
                    "값입니다. 화면에서 고치면 의뢰인의 문서를 고쳐 쓰는 것이 되므로 받지 "
                    "않습니다. 엑셀로 내보내 고친 뒤 업로드하십시오.",
                    f"review {review_id}: medium=pdf_band, field={field}.",
                ],
            )
        stored = self.manual_sheet(review_id)
        if stored is None:
            raise PhaseDiagramRefused(
                "phase_diagram_timing_unknown_plan",
                [f"검토 {review_id}에는 수정할 신호 시간 기록이 없습니다."],
            )
        plan = self._locate_plan(stored, review_id, level_no=level_no, plan_id=plan_id)
        if field in AMENDABLE_PLAN_FIELDS:
            was = _amendment_text(plan.get(field))
            plan[field] = self._amended_value(field, value)
            now = _amendment_text(plan[field])
        elif field in AMENDABLE_PHASE_FIELDS:
            row = self._locate_phase(plan, review_id, phase_id=phase_id)
            was = _amendment_text(row.get(field))
            if value is None and field in _REQUIRED_PHASE_FIELDS:
                raise PhaseDiagramRefused(
                    "phase_diagram_timing_value_invalid",
                    [
                        f"{phase_id}의 {field}은(는) 비울 수 없습니다. 녹색과 소거시간이 없는 "
                        "현시는 현시가 아닙니다."
                    ],
                )
            row[field] = self._amended_value(field, value)
            now = _amendment_text(row[field])
        else:  # pragma: no cover - the route's Literal rules this out
            raise PhaseDiagramRefused(
                "phase_diagram_timing_field_unknown",
                [f"{field!r}은(는) 수정할 수 있는 항목이 아닙니다."],
            )
        directory = self.review_dir(review_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(directory / MANUAL_SHEET_FILENAME, stored)
        self._append_amendment(
            review_id,
            TimingAmendment(
                at=at,
                by=editor,
                field=field,  # type: ignore[arg-type]
                level_no=None if level_no is None else int(level_no),
                plan_id=None if plan_id is None else int(plan_id),
                phase_id=phase_id,
                was=was,
                now=now,
            ),
        )

    def _amend_declared_amber(self, review_id: str, *, value: Any, by: str, at: str) -> None:
        """The declared 황색시간, on EVERY medium — see :data:`AMENDMENT_FILENAME`."""
        with _STORE_LOCK:
            self._declare_amber_locked(review_id, value=value, by=by, at=at)

    def _declare_amber_locked(self, review_id: str, *, value: Any, by: str, at: str) -> None:
        """:meth:`_amend_declared_amber`'s body, always called with
        :data:`_STORE_LOCK` held by its caller."""
        self.render(review_id)  # the review must exist; refuses by name if not
        amber = self._amended_value("declared_amber_s", value)
        if amber is None or float(amber) <= 0:
            raise PhaseDiagramRefused(
                "phase_diagram_timing_value_invalid",
                [
                    "황색시간은 0보다 큰 초 단위 값이어야 합니다. 이 앱은 기본값을 넣지 않으므로, "
                    "값을 비우면 선언이 없는 상태가 됩니다 — 그 상태로는 신호 입력이 만들어지지 "
                    "않습니다."
                ],
            )
        ledger = self.amendments(review_id)
        stored = self.manual_sheet(review_id) or {}
        was = _amendment_text(
            ledger.declared_amber_s
            if ledger.declared_amber_s is not None
            else stored.get("declared_amber_s")
        )
        ledger.declared_amber_s = float(amber)
        ledger.declared_by = by
        ledger.declared_at = at
        ledger.history.append(
            TimingAmendment(
                at=at,
                by=by,
                field="declared_amber_s",
                was=was,
                now=_amendment_text(float(amber)),
            )
        )
        self._write_amendments(review_id, ledger)

    def _amended_value(self, field: str, value: Any) -> Any:
        """One typed value, in the type its field is. A refusal, never a guess."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if field in _TEXT_FIELDS:
            return str(value).strip()
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            raise PhaseDiagramRefused(
                "phase_diagram_timing_value_invalid",
                [f"{field}에 숫자가 아닌 값 {value!r}이(가) 들어왔습니다. 초 단위 숫자를 넣으십시오."],
            ) from None

    def _locate_plan(
        self,
        stored: dict[str, Any],
        review_id: str,
        *,
        level_no: int | None,
        plan_id: int | None,
    ) -> dict[str, Any]:
        """The (수준, 계획 ID) row this amendment names. NEVER created."""
        available = [
            f"수준 {plan.get('level_no')} / 계획 {plan.get('plan_id')}"
            for plan in (stored.get("timing") or [])
        ]
        for plan in stored.get("timing") or []:
            if (
                level_no is not None
                and plan_id is not None
                and int(plan.get("level_no", -1)) == int(level_no)
                and int(plan.get("plan_id", -1)) == int(plan_id)
            ):
                return plan
        raise PhaseDiagramRefused(
            "phase_diagram_timing_unknown_plan",
            [
                f"검토 {review_id}에는 수준 {level_no} / 계획 {plan_id}의 신호 시간이 없습니다. "
                "수정은 이미 기록된 값을 고칠 뿐, 새 계획을 만들지 않습니다.",
                (
                    f"현재 신호시간이 있는 계획: {', '.join(available)}."
                    if available
                    else "현재 이 교차로에는 신호시간이 한 줄도 없습니다."
                ),
            ],
        )

    def _locate_phase(
        self, plan: dict[str, Any], review_id: str, *, phase_id: str | None
    ) -> dict[str, Any]:
        """The 현시 row this amendment names. NEVER created."""
        for row in plan.get("phases") or []:
            if str(row.get("phase_id")) == str(phase_id):
                return row
        listed = ", ".join(str(row.get("phase_id")) for row in plan.get("phases") or [])
        raise PhaseDiagramRefused(
            "phase_diagram_timing_unknown_phase",
            [
                f"이 계획에는 {phase_id} 현시가 없습니다. 수정은 이미 기록된 현시의 값을 고칠 "
                "뿐, 새 현시를 만들지 않습니다.",
                f"review {review_id}: 이 계획의 현시 — {listed or '없음'}.",
            ],
        )

    def _append_amendment(self, review_id: str, event: TimingAmendment) -> None:
        with _STORE_LOCK:
            ledger = self.amendments(review_id)
            ledger.history.append(event)
            self._write_amendments(review_id, ledger)

    def _write_amendments(self, review_id: str, ledger: TimingAmendments) -> None:
        with _STORE_LOCK:
            directory = self.review_dir(review_id)
            directory.mkdir(parents=True, exist_ok=True)
            _write_json(directory / AMENDMENT_FILENAME, ledger.model_dump(mode="json"))

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
        render = self.render(review_id)
        if render.medium != "pdf_band":
            # A no-image review (unit #52) builds its plan from the timing a
            # reviewer TYPED into the 신호시간 sheet, converted by
            # signals_declared with the sheet path's own arithmetic. Without
            # that timing the confirmation is still recorded and still valid —
            # what is missing is the PLAN, and the refusal names exactly the
            # cells that would supply it rather than letting a caller read it as
            # "the confirmation did not count".
            declared = self.declared_plan(review_id, attestation.level_no, attestation.plan_id)
            if declared is None:
                stored = self.manual_sheet(review_id) or {}
                name = stored.get("intersection_name") or render.source_file
                available = [
                    f"수준 {plan.get('level_no')} / 계획 {plan.get('plan_id')}"
                    for plan in (stored.get("timing") or [])
                ]
                raise PhaseDiagramRefused(
                    "phase_diagram_no_sheet_text",
                    [
                        f"{name}: 배정은 확인되었지만 이 계획(수준 {attestation.level_no} / "
                        f"계획 {attestation.plan_id})의 신호시간이 없습니다. 템플릿의 신호시간 "
                        "시트에 이 교차로의 수준·계획·현시별 녹색과 소거시간을 채워 다시 "
                        "올리시면 됩니다.",
                        (
                            f"현재 신호시간이 있는 계획: {', '.join(available)}."
                            if available
                            else "현재 이 교차로에는 신호시간이 한 줄도 없습니다."
                        ),
                        f"review {review_id}: medium={render.medium}, no declared timing for "
                        f"L{attestation.level_no}-P{attestation.plan_id}. The attestation is "
                        "intact and stays on disk.",
                    ],
                )
            return ConfirmedMapping(attestation=attestation, declared_plan=declared)
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
        summary = self.sheet_summary(review_id)
        stale = self.stale_reasons(review_id)
        return {
            "review_id": review_id,
            "source_file": render.source_file,
            "render": render.model_dump(mode="json"),
            "proposal": None if proposal is None else proposal.model_dump(mode="json"),
            "proposal_sha256": self.proposal_sha256(review_id),
            # unit #58 — ``compared`` is the SERVER saying whether the machine
            # actually held anything up against anything. See
            # :func:`cross_check_compared`: the screen hides the section only
            # where the answer is no, and a vision review's flags are untouched.
            "cross_check": (
                None
                if cross is None
                else {
                    **cross.model_dump(mode="json"),
                    "compared": cross_check_compared(cross, medium=render.medium),
                }
            ),
            "attestation": None if attestation is None else attestation.model_dump(mode="json"),
            "stale_reasons": stale,
            "confirmed": attestation is not None and not stale,
            # unit #58 — the card renders ``sheet.timing`` as its 신호 시간
            # table. Joined on HERE rather than inside ``sheet_summary`` so the
            # index (which calls that method once per review, for the name
            # alone) does not pay for a second parse of every 대전 sheet.
            "sheet": {
                **summary,
                "timing": self.timing_plans(review_id),
                # unit #58 round 3 — a lost amendment history is DISCLOSED, never
                # healed silently. `None` when the ledger is healthy.
                "amendments_quarantine": amendment_quarantine_payload(
                    self.amendments(review_id)
                ),
            },
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
            try:
                intersection_name = self.sheet_summary(review_id).get("intersection_name")
            except (PhaseDiagramRefused, OSError, ValueError):
                intersection_name = None
            rows.append(
                {
                    "review_id": review_id,
                    "source_file": render.source_file,
                    # unit #52 — the list has three kinds of row now, and a row
                    # that says which it is spares the screen from inferring it
                    # from a null image sha.
                    "medium": render.medium,
                    "intersection_name": intersection_name,
                    #: The draft node a confirmation bound, so a deep link from
                    #: the network map can FIND the review for a node instead of
                    #: creating a second one for the same intersection.
                    "node_id": None if attestation is None else attestation.node_id,
                    "image_sha256": render.image_sha256,
                    "proposal_source": None if proposal is None else proposal.source,
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
        # The 대전 sheet prints 황색+전적색 as ONE number and never the amber on
        # its own, so this path has nothing to prefill and says so with a null
        # rather than a zero. (The uploaded-sheet path fills it in; see
        # PhaseDiagramStore.sheet_summary.)
        "declared_amber_s": None,
        "amber_declaration_note": None,
        "phase_ids": [f"Φ{timing.phase_no}" for timing in sheet.phases],
        "plan_choices": [
            {"level_no": level_no, "plan_id": plan_id}
            for level_no, plan_id in sorted(
                {(entry.level_no, entry.plan_id) for entry in sheet.tod_entries}
            )
        ],
    }


def _write_json(path: Path, payload: Any) -> None:
    """The store's ONLY writer: same-directory temp file → fsync → ``os.replace``.

    Unit #58 made this atomic, and the reason is the other half of
    :data:`_STORE_LOCK`'s: readers take no lock (so a browser refresh is never
    blocked behind a write), which means a plain ``write_text`` leaves a window
    where a reader sees a TRUNCATED document. The bytes and the format are
    unchanged — same JSON, same sort, same trailing newline, same LF — only the
    moment at which the file becomes the new one.

    Exactly ``demand_target_map._write_payload``'s shape, and for its reason.
    """
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


__all__ = [
    "AMENDABLE_PHASE_FIELDS",
    "AMENDABLE_PLAN_FIELDS",
    "AMENDABLE_REVIEW_FIELDS",
    "AMENDMENT_DOC_KIND",
    "AMENDMENT_FILENAME",
    "ATTESTATION_DOC_KIND",
    "AmendableField",
    "CHECK_NO_TEXT_LAYER",
    "CROSS_CHECK_DOC_KIND",
    "MANUAL_SHEET_DOC_KIND",
    "MEDIA_WITHOUT_IMAGE",
    "NO_RENDERER",
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
    "TimingAmendment",
    "TimingAmendments",
    "amber_declaration_sentence",
    "amendment_quarantine_payload",
    "approach_bearing",
    "build_signals_document",
    "composite_source_key",
    "cross_check",
    "cross_check_compared",
    "cross_check_without_text_layer",
    "manual_render",
    "node_candidates",
    "render_phase_diagram",
    "review_id_for",
    "sha256_hex",
]
