"""Roadmap C1-b — 현시도 제안+확인. The tests are written around the refusals.

The unit's claim is one sentence: **a machine's reading of the 현시도 is an
artifact, and a named human's confirmation is the only fact.** So the suite is
organised the way the gate's own is — for every way a caller might get an
unconfirmed (or stale, or guessed) mapping into a Vissim network there is a test
that must fail:

* **§4 An unconfirmed proposal is INERT.** ``build_signals_document`` takes
  ``ConfirmedMapping`` values and nothing else; a ``ConfirmedMapping`` cannot be
  constructed without an attestation; and the emit signature is asserted against
  its own source, because "we always remember to confirm first" is exactly the
  convention this unit replaces.
* **§5 Attestations are provenance-bound.** A re-render does not delete a
  confirmation — it makes it *stale*, which is a different answer from "nobody
  confirmed", and the store says which in Korean (*재확인 필요*).
* **§2 An unreadable diagram refuses by name.** Never an empty result, never a
  low-confidence guess dressed as a reading.
* **§3 The cross-check flags, and never corrects.** It compares a proposal
  against facts the TEXT LAYER independently states, and a disagreement is
  handed to the human.

**No client data anywhere.** The sheet text comes from the synthetic fixtures
under ``tests/fixtures/daejeon_signal/`` that ``test_genesis_signals_daejeon.py``
already uses, the draft is that file's synthetic crossroads, and the PDFs are
built here byte by byte (:func:`synthetic_pdf`) with Helvetica text — so the
render tests exercise the real anchor-derivation logic without shipping a real
sheet. **No network access**: every vision test injects a stub provider, and the
one place a real call could happen (``propose_phase_diagram`` with
``provider=None``) is never exercised here.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import bearer_headers
from services import genesis_jobs, workspaces
from services.agent import phase_diagram_vision as vision
from services.genesis import phase_diagram as pd
from services.genesis import signals_daejeon as dj
from services.genesis.draft import DraftNetwork, DraftNode
from services.genesis.emit import emit_inpx
from services.genesis.gate import CheckState, run_gate
from services.genesis.side_inputs import compose_from_documents, parse_side_input

from tests.test_genesis_signals_daejeon import crossroads  # noqa: F401  (pytest fixture)

FIXTURES = Path(__file__).parent / "fixtures" / "daejeon_signal"

#: The reviewer's declaration the 대전 bridge refuses without. Same value and
#: same reasoning as ``test_genesis_signals_daejeon.py``'s ``DECLARED``.
DECLARED_AMBER_S = 3.0
AMBER_NOTE = "test fixture: reviewer declares 3.0 s amber"


def sheet_text(name: str = "actuated_variant.txt") -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# A PDF, built here, so the render tests never need a client sheet
# ---------------------------------------------------------------------------


def synthetic_pdf(
    items: list[tuple[str, float, float]], *, width: float = 595.0, height: float = 842.0
) -> bytes:
    """A one-page PDF placing each ``(text, x, y)`` with the base-14 Helvetica.

    Hand-assembled rather than produced by a library because the point is the
    ANCHORS: a test needs to control exactly where the two strings the crop is
    cut between land on the page, and nothing else about the file matters.
    ASCII-only, so the tests monkeypatch :data:`phase_diagram.ANCHOR_TOP` and
    ``ANCHOR_BOTTOM` to the strings used here — the derivation under test is the
    geometry, not the Korean.
    """
    stream = "\n".join(
        f"BT /F1 10 Tf {x} {y} Td ({text}) Tj ET" for text, x, y in items
    ).encode("latin-1")
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode(),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


TOP = "INTERSECTION NAME"
BOTTOM = "SIGNAL TABLE"


@pytest.fixture()
def ascii_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pd, "ANCHOR_TOP", TOP)
    monkeypatch.setattr(pd, "ANCHOR_BOTTOM", BOTTOM)


@pytest.fixture()
def sheet_pdf() -> bytes:
    """A page shaped like a sheet: the identity line, a band of leg labels and
    phase headers, then the SIGNAL TABLE marker below them."""
    return synthetic_pdf(
        [
            (TOP, 40, 740),
            ("NORTH LEG", 200, 715),
            ("WEST LEG", 60, 660),
            ("EAST LEG", 380, 660),
            ("SOUTH LEG", 200, 600),
            ("P1  P2  P3  P4", 430, 715),
            (BOTTOM, 35, 555),
            ("ROWS BELOW THE BAND", 100, 500),
        ]
    )


@pytest.fixture()
def render(sheet_pdf: bytes, ascii_anchors: None) -> tuple[bytes, pd.DiagramRender]:
    return pd.render_phase_diagram(sheet_pdf, source_file="synthetic.pdf")


# ---------------------------------------------------------------------------
# Proposal helpers — a stub provider, so no test needs the network or a key
# ---------------------------------------------------------------------------


class StubProvider:
    """Anything with ``status()`` and ``generate_structured()`` is a provider.

    ``propose_phase_diagram`` takes the provider as an argument for exactly this
    reason: the real one is ``ClaudeProvider`` and is never constructed here, so
    no test can reach the API, spend a token, or read the key.
    """

    def __init__(self, action: dict[str, Any] | None, meta: dict[str, Any] | None = None,
                 ready: bool = True) -> None:
        self.model = "stub-model"
        self._action = action
        self._meta = meta or {"mode": "claude-api", "model": "stub-model", "fallback_rung": 0}
        self._ready = ready
        self.calls: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        return {"ready": self._ready, "state": "ready" if self._ready else "missing-key",
                "model": self.model}

    def generate_structured(self, messages, tools, schema, *, system=None, max_tokens=None):
        self.calls.append(
            {"messages": messages, "tools": tools, "system": system, "max_tokens": max_tokens}
        )
        return self._action, self._meta


def report_action(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "read_intersection_no": "1",
        "read_intersection_name": "SYNTHETIC",
        "approaches": [
            {"label": "NORTH LEG", "side": "top"},
            {"label": "SOUTH LEG", "side": "bottom"},
            {"label": "EAST LEG", "side": "right"},
            {"label": "WEST LEG", "side": "left"},
        ],
        "phases": [
            {"phase_id": "Φ1", "movements": [{"approach_label": "NORTH LEG", "turn": "through"}]},
            {"phase_id": "Φ2", "movements": [{"approach_label": "SOUTH LEG", "turn": "left"}],
             "ambiguity": "화살표 두 개가 겹쳐 보입니다."},
            {"phase_id": "Φ3", "movements": [{"approach_label": "EAST LEG", "turn": "through"}]},
            {"phase_id": "Φ4", "movements": [{"approach_label": "WEST LEG", "turn": "right"}]},
        ],
    }
    params.update(overrides)
    return {"tool": vision.TOOL_REPORT, "params": params}


def proposal_for(render_record: pd.DiagramRender, png: bytes, **overrides: Any) -> pd.PhaseProposal:
    return vision.propose_phase_diagram(
        png, render=render_record, provider=StubProvider(report_action(**overrides))
    )


def attestation_for(
    render_record: pd.DiagramRender,
    *,
    approach_phases: dict[int, str] | None = None,
    **overrides: Any,
) -> pd.MappingAttestation:
    fields: dict[str, Any] = {
        "attested_by": "yunha",
        "attested_at": "2026-08-12T00:00:00+00:00",
        "source_file": render_record.source_file,
        "source_sha256": render_record.source_sha256,
        "image_sha256": render_record.image_sha256,
        "node_id": 1,
        "approach_links": {1: "NORTH LEG", 3: "SOUTH LEG", 5: "EAST LEG", 7: "WEST LEG"},
        "approach_phases": approach_phases or {1: "Φ1", 3: "Φ2", 5: "Φ3", 7: "Φ4"},
        "level_no": 1,
        "plan_id": 1,
        "declared_amber_s": DECLARED_AMBER_S,
        "amber_declaration_note": AMBER_NOTE,
    }
    fields.update(overrides)
    return pd.MappingAttestation(**fields)


def declare_amber(
    store: pd.PhaseDiagramStore, review_id: str, amber: float = DECLARED_AMBER_S
) -> None:
    """Declare the 황색시간 through the door the SCREEN uses (unit #58).

    Since #58 the amber is not a field on the confirm form any more: it is typed
    into the card's 신호 시간 table, stamped server-side, and read back off the
    stored review. ``put_attestation`` therefore refuses a confirmation whose
    amber is not the stored one (``phase_diagram_attestation_stale``), so a test
    that signs WITH an amber declares it first — exactly as the screen does.
    """
    store.amend_timing(
        review_id, field="declared_amber_s", value=amber, edited_by="yunha"
    )


# ---------------------------------------------------------------------------
# §1 Render — the crop is derived from the document, never configured
# ---------------------------------------------------------------------------


def test_the_band_is_cut_between_the_sheets_own_two_anchors(render):
    """The rectangle comes from where the anchors ARE, not from a constant.

    Asserted as an inequality chain rather than four literals: the point is the
    ordering (SIGNAL TABLE below, the identity line above, the leg labels
    between), and pinning pdfium's exact glyph metrics would make this test a
    record of the renderer's version instead of the derivation.
    """
    png, record = render
    left, bottom, right, top = record.crop_pt
    assert bottom < 600 < top, "the band must contain the leg labels"
    assert top < 842 and bottom > 555, "the band stops short of the page edge and SIGNAL TABLE"
    assert left < 60 and right > 440, "the band spans the text between the anchors"
    assert record.anchors["top"]["text"] == TOP
    assert record.anchors["bottom"]["text"] == BOTTOM
    assert png.startswith(b"\x89PNG")
    assert record.image_sha256 == pd.sha256_hex(png)
    assert record.renderer == pd.RENDERER_NAME and record.renderer_version != ""


def test_the_band_carries_the_text_the_sheet_prints_inside_it(render):
    """Label positions are MEASURED off the text layer, not recovered from pixels."""
    _png, record = render
    printed = "".join(record.label_texts())
    for leg in ("NORTH LEG", "SOUTH LEG", "EAST LEG", "WEST LEG"):
        assert leg.replace(" ", "") in printed.replace(" ", "")
    assert "ROWS BELOW THE BAND" not in printed, "text below the band is outside the crop"
    for label in record.labels:
        assert 0.0 <= label.x <= 1.0 and 0.0 <= label.y <= 1.0


def test_a_page_missing_an_anchor_is_refused_rather_than_cropped_to_a_guess(ascii_anchors):
    """An image of the wrong part of a page would produce a confident proposal
    about the wrong drawing, and nothing downstream could notice."""
    pdf = synthetic_pdf([(TOP, 40, 740), ("A DIFFERENT FORM ENTIRELY", 35, 555)])
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        pd.render_phase_diagram(pdf, source_file="wrong-form.pdf")
    assert excinfo.value.code == "phase_diagram_band_not_found"
    assert BOTTOM in " ".join(excinfo.value.reasons)


def test_anchors_in_the_wrong_order_are_refused_as_a_degenerate_band(ascii_anchors):
    pdf = synthetic_pdf([(TOP, 40, 500), (BOTTOM, 35, 740)])
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        pd.render_phase_diagram(pdf, source_file="upside-down.pdf")
    assert excinfo.value.code == "phase_diagram_band_degenerate"


def test_bytes_that_are_not_a_pdf_are_refused_by_name(ascii_anchors):
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        pd.render_phase_diagram(b"this is not a pdf", source_file="notes.txt")
    assert excinfo.value.code == "phase_diagram_source_unreadable"


def test_a_review_id_is_derived_from_the_file_name_so_a_re_render_lands_in_place():
    """Deterministic on purpose: re-rendering must reach the SAME review, so the
    existing attestation is compared against the new image rather than a second
    review quietly appearing with nobody's confirmation on it."""
    assert pd.review_id_for("218. x.pdf") == pd.review_id_for("218. x.pdf")
    assert pd.review_id_for("218. x.pdf") != pd.review_id_for("219. x.pdf")
    assert pd.review_id_for("/some/dir/218. x.pdf") == pd.review_id_for("218. x.pdf")


# ---------------------------------------------------------------------------
# §2 The proposal — a named refusal, never a guess and never an empty result
# ---------------------------------------------------------------------------


def test_the_models_reading_becomes_a_structured_proposal(render):
    png, record = render
    proposal = proposal_for(record, png)
    assert proposal.outcome == "proposed" and proposal.refusal is None
    assert proposal.phase_ids == ["Φ1", "Φ2", "Φ3", "Φ4"]
    assert proposal.read_intersection_no == "1"
    assert proposal.image_sha256 == record.image_sha256
    assert proposal.source_file == record.source_file
    assert proposal.model == "stub-model" and proposal.proposed_at
    ambiguous = [p for p in proposal.phases if p.ambiguity]
    assert ambiguous and ambiguous[0].phase_id == "Φ2"


def test_the_model_declining_becomes_a_named_refusal_not_an_empty_answer(render):
    """A refusal is a TOOL CALL, which is why it arrives with a reason attached."""
    png, record = render
    provider = StubProvider(
        {"tool": vision.TOOL_REFUSE, "params": {"code": "illegible_arrows",
                                                "reason": "화살표를 판독할 수 없습니다."}}
    )
    proposal = vision.propose_phase_diagram(png, render=record, provider=provider)
    assert proposal.outcome == "refused" and not proposal.phases
    assert proposal.refusal is not None
    assert proposal.refusal.code == "illegible_arrows"
    assert "판독" in proposal.refusal.reason


def test_an_unavailable_provider_is_a_refusal_the_batch_can_count(render):
    png, record = render
    proposal = vision.propose_phase_diagram(
        png, render=record, provider=StubProvider(None, ready=False)
    )
    assert proposal.outcome == "refused"
    assert proposal.refusal.code == vision.REFUSAL_PROVIDER_UNAVAILABLE


def test_a_provider_that_gave_up_carries_its_own_error_into_the_refusal(render):
    png, record = render
    provider = StubProvider(None, meta={"mode": "claude-api", "error": "timeout after 90s"})
    proposal = vision.propose_phase_diagram(png, render=record, provider=provider)
    assert proposal.refusal.code == vision.REFUSAL_PROVIDER_FAILED
    assert "timeout after 90s" in proposal.refusal.reason


def test_a_reading_with_no_phases_is_recorded_as_the_refusal_it_is(render):
    """``PhaseProposal`` rejects "proposed but empty"; the vision layer turns that
    rejection into a named refusal rather than letting the batch crash."""
    png, record = render
    proposal = proposal_for(record, png, phases=[])
    assert proposal.outcome == "refused"
    assert proposal.refusal.code == vision.REFUSAL_INVALID_PAYLOAD


def test_an_unknown_tool_is_refused_rather_than_interpreted(render):
    png, record = render
    provider = StubProvider({"tool": "something_else", "params": {}})
    proposal = vision.propose_phase_diagram(png, render=record, provider=provider)
    assert proposal.refusal.code == vision.REFUSAL_INVALID_PAYLOAD


def test_a_turn_outside_the_closed_set_becomes_unknown_and_stays_visible(render):
    png, record = render
    proposal = proposal_for(
        record,
        png,
        phases=[{"phase_id": "Φ1", "movements": [
            {"approach_label": "NORTH LEG", "turn": "straight-ahead"}]}],
    )
    assert proposal.phases[0].movements[0].turn == "unknown"


def test_the_image_sha_is_computed_from_the_bytes_sent_not_copied_from_the_render(render):
    """A caller that passes an image and a provenance record which disagree gets
    a proposal matching neither — so the mismatch surfaces at confirm time
    instead of becoming a confirmation of the wrong picture."""
    _png, record = render
    other = b"\x89PNG\r\n\x1a\n" + b"different bytes"
    proposal = vision.propose_phase_diagram(
        other, render=record, provider=StubProvider(report_action())
    )
    assert proposal.image_sha256 == pd.sha256_hex(other) != record.image_sha256


def test_the_proposal_shape_forbids_a_refusal_without_a_reason():
    with pytest.raises(ValueError, match="must name its reason"):
        pd.PhaseProposal(
            outcome="refused", model="m", image_sha256="0" * 64,
            source_file="s.pdf", proposed_at="now",
        )


def test_the_proposal_shape_forbids_half_a_reading_beside_a_refusal():
    with pytest.raises(ValueError, match="carries no phases"):
        pd.PhaseProposal(
            outcome="refused",
            refusal=pd.ProposalRefusal(code="c", reason="r"),
            phases=[pd.ProposedPhase(phase_id="Φ1")],
            model="m", image_sha256="0" * 64, source_file="s.pdf", proposed_at="now",
        )


def test_the_strict_json_path_never_touches_clean_generated_answer():
    """CLAUDE.md: ``clean_generated_answer()`` STRIPS JSON, so a strict-JSON path
    that went through it would be destroyed by it.

    Pinned against the module's own source rather than asserted behaviourally,
    because the failure it guards is somebody later routing this answer through
    the assistant's text pipeline — which no output assertion would catch.
    """
    source = Path(vision.__file__).read_text(encoding="utf-8")
    assert "clean_generated_answer" not in source.replace(
        "``assistant_router.clean_generated_answer()``", ""
    ).replace("clean_generated_answer()``", "")
    assert "generate_structured" in source, "the native tool-use path IS the strict-JSON path"
    assert "anthropic.Anthropic(" not in source, "the product's existing client, not a second one"


def test_the_vision_call_sends_the_image_and_offers_exactly_two_tools(render):
    png, record = render
    provider = StubProvider(report_action())
    vision.propose_phase_diagram(png, render=record, provider=provider)
    call = provider.calls[0]
    blocks = call["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert {tool["name"] for tool in call["tools"]} == {vision.TOOL_REPORT, vision.TOOL_REFUSE}
    assert "refuse_phase_diagram" in call["system"]
    assert "confidence" not in json.dumps(call["tools"]), (
        "a score invites a threshold, and a threshold promotes a guess to a reading"
    )


# ---------------------------------------------------------------------------
# §3 The cross-check — deterministic, and it never corrects
# ---------------------------------------------------------------------------


def test_phase_ids_that_disagree_with_the_parsed_plan_are_flagged_never_corrected(render):
    png, record = render
    sheet = dj.parse_sheet_text(sheet_text(), source_file="fixture.pdf")
    proposal = proposal_for(
        record, png,
        phases=[{"phase_id": f"Φ{n}", "movements": [
            {"approach_label": "NORTH LEG", "turn": "through"}]} for n in (1, 2, 3, 4, 5)],
    )
    result = pd.cross_check(proposal, sheet=sheet, render=record)
    assert pd.CHECK_PHASE_IDS in result.codes("flag")
    assert not result.agrees
    # Flagged, not repaired: the proposal on disk still says what the model said.
    assert proposal.phase_ids == ["Φ1", "Φ2", "Φ3", "Φ4", "Φ5"]


def test_matching_phase_ids_agree_with_the_text_layer(render):
    png, record = render
    sheet = dj.parse_sheet_text(sheet_text(), source_file="fixture.pdf")
    proposal = proposal_for(record, png, read_intersection_no=sheet.intersection_no)
    result = pd.cross_check(proposal, sheet=sheet, render=record)
    assert result.agrees, result.codes("flag")
    assert pd.CHECK_AGREES in result.codes("ok")


def test_an_approach_name_the_sheet_does_not_print_is_flagged(render):
    png, record = render
    sheet = dj.parse_sheet_text(sheet_text(), source_file="fixture.pdf")
    proposal = proposal_for(
        record, png,
        approaches=[{"label": "INVENTED AVENUE", "side": "top"}],
        phases=[{"phase_id": f"Φ{n}", "movements": [
            {"approach_label": "INVENTED AVENUE", "turn": "through"}]} for n in (1, 2, 3, 4)],
    )
    result = pd.cross_check(proposal, sheet=sheet, render=record)
    assert pd.CHECK_APPROACH_UNKNOWN in result.codes("flag")


def test_a_label_split_across_text_runs_is_not_falsely_flagged(render):
    """The sketch prints leg names vertically, one character per line, so the text
    layer returns them as several one-character runs. A membership test that only
    compared whole runs would flag every real sheet — and reviewers would learn
    to ignore the one check that catches an invented name."""
    _png, record = render
    printed = pd._PrintedLabels(record)
    assert pd._normalise_label("NORTH LEG") in printed
    assert pd._normalise_label("NORTHLEG") in printed, "whitespace is typography, not spelling"
    assert pd._normalise_label("NORTHERN LEG") not in printed


def test_the_intersection_number_read_off_the_image_must_match_the_sheet(render):
    """The check that catches an image paired with the WRONG sheet — which
    nothing else downstream could notice."""
    png, record = render
    sheet = dj.parse_sheet_text(sheet_text(), source_file="fixture.pdf")
    proposal = proposal_for(record, png, read_intersection_no="99999")
    result = pd.cross_check(proposal, sheet=sheet, render=record)
    assert pd.CHECK_INTERSECTION_NO in result.codes("flag")


def test_a_phase_with_no_movement_at_all_is_flagged(render):
    png, record = render
    sheet = dj.parse_sheet_text(sheet_text(), source_file="fixture.pdf")
    proposal = proposal_for(
        record, png,
        read_intersection_no=sheet.intersection_no,
        phases=[
            {"phase_id": "Φ1", "movements": [{"approach_label": "NORTH LEG", "turn": "through"}]},
            {"phase_id": "Φ2", "movements": []},
            {"phase_id": "Φ3", "movements": [{"approach_label": "EAST LEG", "turn": "through"}]},
            {"phase_id": "Φ4", "movements": [{"approach_label": "WEST LEG", "turn": "right"}]},
        ],
    )
    result = pd.cross_check(proposal, sheet=sheet, render=record)
    assert pd.CHECK_PHASE_WITHOUT_MOVEMENT in result.codes("flag")


def test_a_sheet_the_text_layer_could_not_parse_is_a_flag_not_silence(render):
    png, record = render
    proposal = proposal_for(record, png)
    result = pd.cross_check(proposal, sheet=None, render=record)
    assert pd.CHECK_NO_PARSED_PLAN in result.codes("flag")


def test_a_refused_proposal_cross_checks_to_an_observation_not_to_nothing(render):
    """"No findings" must never be the same shape as "not checked" — and the
    finding's CODE must say nothing was compared. Re-verify 2026-08-12: this
    branch wore CHECK_AGREES ("compared and matched") when a sheet was present,
    which would mislead anyone debugging by code."""
    png, record = render
    provider = StubProvider(
        {"tool": vision.TOOL_REFUSE, "params": {"code": "c", "reason": "읽을 수 없습니다."}}
    )
    proposal = vision.propose_phase_diagram(png, render=record, provider=provider)

    result = pd.cross_check(proposal, sheet=None, render=record)
    assert result.findings and not result.codes("flag")
    assert result.findings[0].severity == "observation"
    assert result.findings[0].code == pd.CHECK_NO_PARSED_PLAN

    sheet = dj.parse_sheet_text(sheet_text(), source_file="fixture.pdf")
    with_sheet = pd.cross_check(proposal, sheet=sheet, render=record)
    assert with_sheet.findings[0].severity == "observation"
    assert with_sheet.findings[0].code == pd.CHECK_NO_READING
    assert with_sheet.findings[0].code != pd.CHECK_AGREES


def test_where_a_label_is_printed_is_an_observation_and_never_a_verdict(render):
    """A draughtsman may put a label anywhere: this locates the CHARACTERS, which
    is not evidence of where the road is. Offered as corroboration, never a flag."""
    png, record = render
    sheet = dj.parse_sheet_text(sheet_text(), source_file="fixture.pdf")
    proposal = proposal_for(record, png, read_intersection_no=sheet.intersection_no)
    result = pd.cross_check(proposal, sheet=sheet, render=record)
    sides = [f for f in result.findings if f.code == pd.CHECK_LABEL_SIDE]
    assert sides, "the observation is offered"
    assert all(f.severity == "observation" for f in sides)
    assert result.agrees, "an observation never blocks"


def test_agreement_is_not_a_claim_that_the_reading_is_correct():
    """The property's own docstring is the contract, and it is asserted so that
    deleting the caveat is a test failure rather than a quiet promotion."""
    doc = " ".join((pd.CrossCheckResult.agrees.__doc__ or "").split())
    assert "NOT a claim" in doc and "wrong approach in the wrong phase" in doc


# ---------------------------------------------------------------------------
# §4 An unconfirmed proposal is INERT
# ---------------------------------------------------------------------------


def test_the_signals_document_builder_takes_confirmations_and_nothing_else():
    """No ``proposals=``, no ``allow_unconfirmed``, no ``skip_confirmation``.

    Asserted against the SIGNATURE, because the failure this guards is a later
    edit adding such a parameter — which no behavioural test would catch until
    somebody used it.
    """
    signature = inspect.signature(pd.build_signals_document)
    assert list(signature.parameters) == [
        "confirmed", "conversion_settings", "signal_settings",
        # Unit #70. Admitted here deliberately, and the two assertions below are
        # what keep admitting it from being a loophole: it may not change WHICH
        # mappings are used, only what the document says about the plans of the
        # ones already confirmed.
        "time_of_day_switching",
    ]
    source = inspect.getsource(pd.build_signals_document)
    for banned in ("proposal", "skip", "allow_unconfirmed", "force"):
        assert banned not in source.split('"""')[2], f"{banned!r} must not be on the emit path"


def test_no_builder_parameter_can_change_which_confirmations_are_used(render):
    """The guard above admits parameters by name; this one holds every admitted
    parameter to the rule the name list cannot express — the set of assignments,
    and every attested field in them, is identical whatever the flags say."""
    _png, record = render
    confirmed = [
        pd.ConfirmedMapping(attestation=attestation_for(record), sheet_text=sheet_text())
    ]

    plain = pd.build_signals_document(confirmed)
    for kwargs in (
        {"time_of_day_switching": True},
        {"time_of_day_switching": False},
        {"conversion_settings": {}, "time_of_day_switching": True},
    ):
        other = pd.build_signals_document(confirmed, **kwargs)
        assert len(other["assignments"]) == len(plain["assignments"])
        for left, right in zip(plain["assignments"], other["assignments"]):
            assert left["node_id"] == right["node_id"]
            assert left["plan"] == right["plan"]
            assert left["approach_phases"] == right["approach_phases"]


def test_a_confirmed_mapping_cannot_be_built_without_an_attestation(render):
    """A caller holding only a proposal cannot construct the value the emit path
    takes. That is the enforcement — not a flag that defaults to on."""
    _png, record = render
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        pd.ConfirmedMapping(attestation=None, sheet_text=sheet_text())  # type: ignore[arg-type]
    assert excinfo.value.code == "phase_diagram_not_confirmed"

    proposal = pd.PhaseProposal(
        outcome="proposed",
        phases=[pd.ProposedPhase(phase_id="Φ1")],
        model="m", image_sha256="0" * 64, source_file="s.pdf", proposed_at="now",
    )
    with pytest.raises(pd.PhaseDiagramRefused):
        pd.ConfirmedMapping(attestation=proposal, sheet_text=sheet_text())  # type: ignore[arg-type]


def test_an_empty_set_of_confirmations_is_refused_with_the_real_reason():
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        pd.build_signals_document([])
    assert excinfo.value.code == "phase_diagram_no_confirmations"
    assert "confirmed" in " ".join(excinfo.value.reasons)


def test_a_review_with_a_perfectly_good_proposal_and_no_confirmation_refuses(tmp_path, render):
    """The proposal's EXISTENCE is stated in the refusal, so nobody reads it as
    "nothing was produced"."""
    png, record = render
    store = pd.PhaseDiagramStore(tmp_path / "reviews")
    review_id = store.put_render(png, record, sheet_text=sheet_text())
    proposal = proposal_for(record, png)
    store.put_proposal(review_id, proposal, pd.cross_check(proposal, sheet=None, render=record))

    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        store.confirmed_mapping(review_id)
    assert excinfo.value.code == "phase_diagram_not_confirmed"
    assert "제안은 존재하지만" in " ".join(excinfo.value.reasons)


def test_two_confirmations_for_one_node_are_refused_rather_than_one_winning(render):
    _png, record = render
    entries = [
        pd.ConfirmedMapping(attestation=attestation_for(record), sheet_text=sheet_text()),
        pd.ConfirmedMapping(attestation=attestation_for(record), sheet_text=sheet_text()),
    ]
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        pd.build_signals_document(entries)
    assert excinfo.value.code == "phase_diagram_duplicate_node"


def test_an_attestation_needs_a_named_attester_and_a_non_empty_mapping(render):
    _png, record = render
    with pytest.raises(ValueError):
        attestation_for(record, attested_by="")
    # Constructed directly rather than through the helper: the helper's
    # ``approach_phases or {...}`` fallback would swallow the empty mapping and
    # the test would pass against a default it never meant to exercise.
    with pytest.raises(ValueError, match="approach_phases is empty"):
        pd.MappingAttestation(
            attested_by="yunha",
            attested_at="2026-08-12T00:00:00+00:00",
            source_file=record.source_file,
            source_sha256=record.source_sha256,
            image_sha256=record.image_sha256,
            node_id=1,
            approach_phases={},
            level_no=1,
            plan_id=1,
        )


def test_attestation_keys_are_link_ids_because_a_label_would_match_no_approach(render):
    _png, record = render
    attestation = attestation_for(record, approach_phases={"1": "Φ1"})
    assert attestation.approach_phases == {1: "Φ1"}
    with pytest.raises(ValueError, match="not a draft link id"):
        attestation_for(record, approach_phases={"north": "Φ1"})


# ---------------------------------------------------------------------------
# §5 Attestations are provenance-bound — stale, never silently reused
# ---------------------------------------------------------------------------


def test_a_re_render_makes_a_confirmation_stale_rather_than_deleting_it(tmp_path, render, ascii_anchors):
    """Deleting it would make a stale confirmation indistinguishable from one
    that was never made, and the reviewer would be shown a blank form with no
    sign that somebody had confirmed this intersection before."""
    png, record = render
    store = pd.PhaseDiagramStore(tmp_path / "reviews")
    review_id = store.put_render(png, record, sheet_text=sheet_text())
    declare_amber(store, review_id)
    store.put_attestation(review_id, attestation_for(record))
    assert store.review_payload(review_id)["confirmed"] is True

    # The same sheet, rendered wider: a different image, same review.
    png2, record2 = pd.render_phase_diagram(
        synthetic_pdf([(TOP, 40, 740), ("NORTH LEG", 200, 700), (BOTTOM, 35, 555)]),
        source_file=record.source_file,
        target_width_px=900,
    )
    assert store.put_render(png2, record2, sheet_text=sheet_text()) == review_id

    payload = store.review_payload(review_id)
    assert payload["attestation"] is not None, "the confirmation is still on disk"
    assert payload["confirmed"] is False
    assert any("재확인 필요" in reason for reason in payload["stale_reasons"])
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        store.confirmed_mapping(review_id)
    assert excinfo.value.code == "phase_diagram_attestation_stale"


def test_a_changed_sheet_file_makes_the_confirmation_stale_too(render):
    _png, record = render
    attestation = attestation_for(record)
    reasons = attestation.staleness(source_sha256="f" * 64, image_sha256=record.image_sha256)
    assert reasons and "재확인 필요" in reasons[0]
    assert attestation.staleness(
        source_sha256=record.source_sha256, image_sha256=record.image_sha256
    ) == []


def test_confirming_against_an_image_the_review_no_longer_holds_is_refused(tmp_path, render):
    png, record = render
    store = pd.PhaseDiagramStore(tmp_path / "reviews")
    review_id = store.put_render(png, record, sheet_text=sheet_text())
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        store.put_attestation(review_id, attestation_for(record, image_sha256="a" * 64))
    assert excinfo.value.code == "phase_diagram_attestation_stale"
    assert store.attestation(review_id) is None, "nothing was recorded"


def test_the_blank_form_path_is_a_first_class_confirmation(tmp_path, render):
    """Roadmap C1 option (b): a mapping typed in with no machine reading at all
    is the STRONGEST shape this module produces, not a degraded one."""
    png, record = render
    store = pd.PhaseDiagramStore(tmp_path / "reviews")
    review_id = store.put_render(png, record, sheet_text=sheet_text())
    declare_amber(store, review_id)
    store.put_attestation(review_id, attestation_for(record, proposal_outcome="none"))

    payload = store.review_payload(review_id)
    assert payload["proposal"] is None, "the key exists and is null — never absent"
    assert payload["confirmed"] is True
    assert payload["attestation"]["proposal_sha256"] is None
    assert store.confirmed_mapping(review_id).attestation.attested_by == "yunha"


def test_what_the_human_changed_against_the_proposal_is_recorded(tmp_path, render):
    png, record = render
    store = pd.PhaseDiagramStore(tmp_path / "reviews")
    review_id = store.put_render(png, record, sheet_text=sheet_text())
    declare_amber(store, review_id)
    store.put_attestation(
        review_id,
        attestation_for(
            record,
            proposal_outcome="proposed",
            proposal_sha256="b" * 64,
            changes=[pd.MappingChange(field="link 5 phase", was="Φ3", now="Φ4")],
        ),
    )
    recorded = store.attestation(review_id)
    assert recorded.changes[0].was == "Φ3" and recorded.changes[0].now == "Φ4"


# ---------------------------------------------------------------------------
# §6 Emit — exactly the Track G shape, and a draft whose V5 can run
# ---------------------------------------------------------------------------


def test_confirmed_mappings_emit_exactly_the_signals_side_input_shape(render):
    """The document is fed to ``side_inputs.parse_side_input`` — the shipped
    validator — rather than to an assertion about the shape this module thinks
    it should have."""
    _png, record = render
    document = pd.build_signals_document(
        [pd.ConfirmedMapping(attestation=attestation_for(record), sheet_text=sheet_text())]
    )
    assert document["kind"] == "genesis.signals.v1"

    parsed = parse_side_input("signals", json.dumps(document).encode("utf-8"))
    assignment = parsed.assignments[0]
    assert assignment.node_id == 1
    assert assignment.plan.source == "daejeon_sheet"
    assert assignment.plan.declared_amber_s == DECLARED_AMBER_S
    assert assignment.approach_phases == {1: "Φ1", 3: "Φ2", 5: "Φ3", 7: "Φ4"}


def test_a_confirmation_with_no_sheet_text_cannot_build_a_plan(render):
    _png, record = render
    with pytest.raises(pd.PhaseDiagramRefused) as excinfo:
        pd.ConfirmedMapping(attestation=attestation_for(record), sheet_text="   ")
    assert excinfo.value.code == "phase_diagram_no_sheet_text"


def test_end_to_end_a_confirmed_mapping_reaches_a_draft_whose_v5_runs(
    tmp_path, render, crossroads
):
    """Sheet → render → proposal → cross-check → CONFIRMATION → signals document
    → composed layers → emitted ``.inpx`` → the gate's V5 is CHECKABLE.

    This is the unit's acceptance path, offline. It proves the objects are
    self-consistent and that every interface matches; it does NOT prove PTV
    Vissim loads the result — that needs the live check, and V1 says so on the
    same verdict. What it does prove is the thing C1 was blocked on: without the
    approach→phase mapping V5 reports NOT_CHECKABLE on a geometry-only file, and
    with a confirmed one it reports a real state.
    """
    png, record = render
    store = pd.PhaseDiagramStore(tmp_path / "reviews")
    review_id = store.put_render(png, record, sheet_text=sheet_text())

    proposal = proposal_for(record, png)
    store.put_proposal(review_id, proposal, pd.cross_check(proposal, sheet=None, render=record))
    with pytest.raises(pd.PhaseDiagramRefused):
        store.confirmed_mapping(review_id)  # still inert

    declare_amber(store, review_id)
    store.put_attestation(review_id, attestation_for(record, proposal_outcome="proposed"))
    document = pd.build_signals_document([store.confirmed_mapping(review_id)])
    signals = parse_side_input("signals", json.dumps(document).encode("utf-8"))

    composed = compose_from_documents(crossroads, signals=signals)
    assert composed.refusals == (), [i.code for i in composed.refusals]
    assert composed.signals is not None and composed.signals.nodes

    path = emit_inpx(crossroads, tmp_path / "e2e.inpx", composed.signals)
    v5 = run_gate(path).check("V5")
    assert v5.state is not CheckState.NOT_CHECKABLE, [gap.code for gap in v5.gaps]


# ---------------------------------------------------------------------------
# §7 Node candidates — a ranked list with its reason, never a binding
# ---------------------------------------------------------------------------


def test_node_candidates_carry_their_approaches_and_why_they_ranked(crossroads):
    candidates = pd.node_candidates(crossroads, intersection_name="n1")
    assert candidates and candidates[0]["node_id"] == 1
    assert candidates[0]["match_reason"], "a rank with no reason is an unexplained verdict"
    links = {a["link_id"] for a in candidates[0]["approaches"]}
    assert links == {1, 3, 5, 7}
    assert all(a["from_compass"] for a in candidates[0]["approaches"])


def _osm_style_nodes(count: int, *, start_id: int = 1) -> list[DraftNode]:
    """``count`` nodes named the way an OSM draft actually names one -- "Node
    <osmid>" -- so every one of them scores 0.0 against ANY Korean 교차로명:
    `_name_overlap` has no character in common between an ASCII label and a
    Korean one to weigh."""
    return [
        DraftNode(
            id=start_id + i,
            name=f"Node {900000 + start_id + i}",
            osm_id=900000 + start_id + i,
            x=float(i),
            y=0.0,
            polygon=[],
        )
        for i in range(count)
    ]


def test_node_candidates_offers_every_node_when_the_name_signal_discriminates_nothing():
    """MEASURED DEFECT (unit #62): an OSM draft names every node "Node
    <osmid>", so every candidate scores 0.0 against the sheet's 교차로명 and the
    OLD ``ranked[:limit]`` offered only the 8 lowest-node-id nodes -- on the
    field draft this was measured against (25 nodes / 82 links), 17 nodes were
    simply never reachable, with nothing on screen naming the cut. A ranking
    that cannot discriminate has no basis for saying which 8 are "better", so
    it must not truncate at all.
    """
    nodes = _osm_style_nodes(25)
    draft = DraftNetwork(name="osm-draft", source_osm="region.osm", nodes=nodes)
    candidates = pd.node_candidates(draft, intersection_name="가상감응네거리")
    assert len(candidates) == 25, "a non-discriminating ranking must return every node, not just the top `limit`"
    assert {c["node_id"] for c in candidates} == {n.id for n in nodes}
    assert all(c["match_score"] == 0.0 for c in candidates)


def test_node_candidates_offers_every_node_when_no_intersection_name_is_known():
    """The other half of the same defect: a blank review has no 교차로명 to
    compare against at all, so every score is 0.0 for a different reason --
    and with 0 nodes discriminated, `limit` must still not silently cut."""
    nodes = _osm_style_nodes(10)
    draft = DraftNetwork(name="osm-draft", source_osm="region.osm", nodes=nodes)
    assert len(pd.node_candidates(draft, intersection_name=None)) == 10
    assert len(pd.node_candidates(draft, intersection_name="")) == 10


def test_node_candidates_still_truncates_to_the_limit_once_the_ranking_discriminates():
    """Some names DO overlap the sheet's 교차로명 -- the ranking discriminates --
    so `limit` is a real answer again and must still be obeyed. What changed
    from the old behaviour is that every discriminating node survives the cut
    (it outranks every 0.0 node by construction) and the caller can now see the
    cut happened at all -- covered by the HTTP-level disclosure test below.
    """
    # UNIT #62 FINDING 1: these three must land on a REAL signal tier (exact
    # match or substring containment) -- the fallback tier's character
    # overlap no longer counts toward `discriminates` at all, so a fixture
    # built on it (the pre-fix version of this test used "가상삼거리" etc.,
    # fallback-tier matches) would wrongly assert a truncation that could no
    # longer happen.
    discriminating = [
        DraftNode(id=1, name="가상감응네거리", osm_id=1, x=0.0, y=0.0, polygon=[]),  # exact match
        DraftNode(id=2, name="가상감응", osm_id=2, x=0.0, y=0.0, polygon=[]),  # target contains this
        DraftNode(id=3, name="가상감응네거리앞", osm_id=3, x=0.0, y=0.0, polygon=[]),  # this contains target
    ]
    flat = _osm_style_nodes(8, start_id=10)
    draft = DraftNetwork(name="mixed", source_osm="mixed.osm", nodes=discriminating + flat)
    candidates = pd.node_candidates(draft, intersection_name="가상감응네거리", limit=8)
    assert len(candidates) == 8, "a ranking that DOES discriminate still obeys the limit"
    kept_ids = {c["node_id"] for c in candidates}
    # the three discriminating nodes always survive; the cut falls on the
    # lowest-node-id zero-score nodes, node_id 10-14 of 10-17.
    assert kept_ids == {1, 2, 3, 10, 11, 12, 13, 14}
    for candidate in candidates:
        if candidate["node_id"] in {1, 2, 3}:
            assert candidate["match_score"] > 0.0


def test_name_overlap_reasons_stay_intact_and_a_shared_digit_alone_is_not_signal():
    """`_name_overlap`'s reason STRINGS are user-facing (`match_reason` on the
    wire) and must not move; this pins all five branches byte-for-byte while
    also pinning the NEW third value (`real_signal`) FINDING 1's fix added.
    The digit case is the regression itself: "3번국도교차로" and "Node
    318918018" share only the digit "3" -- before the fix that produced a
    positive score AND (via the old `score > 0.0` rule) counted as a
    discriminating signal; after the fix it is still reported as "no common
    characters", because a digit is not one for this purpose.
    """
    assert pd._name_overlap("", "아무거나") == (0.0, "이름 없음 — 이름으로는 비교할 수 없습니다.", False)
    assert pd._name_overlap("가상감응네거리", "가상감응네거리") == (1.0, "이름이 정확히 일치합니다.", True)
    score, reason, real_signal = pd._name_overlap("가상", "가상감응네거리")
    assert reason == "한쪽 이름이 다른 쪽에 포함됩니다." and real_signal is True and score > 0.0
    score, reason, real_signal = pd._name_overlap("가상감응네거리", "가상삼거리")
    assert reason == "공통 글자 4자." and real_signal is False and score > 0.0
    # THE REGRESSION: a shared digit, and nothing else, must fall through to
    # the "no common characters" branch -- both the message AND real_signal.
    assert pd._name_overlap("3번국도교차로", "node 318918018") == (
        0.0,
        "공통 글자가 없습니다.",
        False,
    )


def test_node_candidates_ignores_a_shared_digit_as_name_signal():
    """MEASURED DEFECT (refuter, unit #62 FINDING 1 -- BLOCKER): a 교차로명
    carrying a route number, e.g. "3번국도교차로", shares the digit "3" with
    an OSM draft's "Node 318918018" (and several of its 25 siblings -- OSM
    ids in the 900000s routinely contain a "3"). The character-overlap
    fallback tier scored that positive, `discriminates` flipped true on pure
    digit noise, the silent 8-node cap re-activated, and the CORRECT node --
    which legitimately scores 0.0, having no real OSM name overlap at all --
    was excluded from the top 8 by garbage matches that merely happened to
    share a digit. Reproduced here with the exact `_osm_style_nodes` fixture
    every other test in this file uses.
    """
    nodes = _osm_style_nodes(25)
    draft = DraftNetwork(name="osm-draft", source_osm="region.osm", nodes=nodes)
    candidates = pd.node_candidates(draft, intersection_name="3번국도교차로")
    assert len(candidates) == 25, "a shared digit alone must not count as a discriminating name signal"
    assert all(c["match_score"] == 0.0 for c in candidates), "digits are excluded from the fallback comparison entirely"
    assert {c["node_id"] for c in candidates} == {n.id for n in nodes}


def test_name_overlap_tier_matrix_across_digit_bearing_all_digit_and_clean_targets():
    """ROUND 3 FINDING A's own regression shape, EXTENDED in round 4 with the
    digit-sibling tier: a full matrix over {exact, containment, fallback} x
    {digit-bearing, all-digit, clean} targets, plus the new sibling rows,
    asserting exactly which cells count as REAL signal.

    THE RULE this matrix pins: digits are excluded from tiers 1.5 (removed --
    see below), 2 and 3 at EVERY combination -- an all-digit residue on
    EITHER side of a containment check blocks tier 2 outright (Python's
    `"" in anything` is always True, so an unstripped guard would make an
    all-digit string trivially "contain" everything), and the fallback tier
    is never real signal regardless of alphabet. Tier 1 (exact match) is the
    one DELIBERATE exception -- see `_name_overlap`'s own docstring for the
    recorded decision -- so it counts even when both names are bare digits.

    ROUND 4: tier 2 (containment) is back to the RAW, unstripped strings --
    round 3 ran it on stripped strings, which PROMOTED digit siblings
    ("원신흥4" vs "원신흥네거리", a containment relationship that does not
    exist on the real strings) into full real_signal=True matches. The new
    TIER 2.5 (digit-sibling) catches exactly that shape and reports it
    honestly instead: real signal is False, but the score still ranks above
    pure noise (tier 3) and the reason names what is actually true.
    """
    cases = [
        # (label, target, candidate, expect_real_signal, expect_positive_score)
        ("exact / digit-bearing", "3번국도교차로", "3번국도교차로", True, True),
        ("exact / all-digit", "18", "18", True, True),
        ("exact / clean", "가상삼거리", "가상삼거리", True, True),
        ("containment / digit-bearing", "3번국도교차로", "3번국도교차로앞", True, True),
        # THE ROUND 3 REGRESSION: an all-digit target is a literal substring
        # of an OSM draft's "node<osmid>" name for most osmids -- still
        # blocked (tier 2's residue guard), even with containment back to
        # raw strings.
        ("containment / all-digit target", "18", "node18foo", False, False),
        ("containment / clean", "가상삼거리", "가상삼거리앞", True, True),
        # ROUND 4 -- DIGIT-SIBLING, the class this round closes. None of
        # these satisfy RAW containment (asserted explicitly below, so the
        # test fixture cannot silently drift into tier 2 instead); all three
        # satisfy STRIPPED containment/equality with a real non-digit
        # residue, so they land in tier 2.5: real signal False, score
        # positive (ranked above pure noise, never a truncation trigger).
        ("digit-sibling / embedded digit", "가상3거리", "가상거리", False, True),
        ("digit-sibling / distinct suffix digits", "신흥들3", "신흥들4", False, True),
        ("digit-sibling / not a real containment", "원신흥4", "원신흥네거리", False, True),
        ("fallback / digit-bearing", "가상감응네거리", "9가상삼거리", False, True),
        ("fallback / all-digit", "18", "81", False, False),
        ("fallback / clean", "가상감응네거리", "가상삼거리", False, True),
    ]
    for label, target, candidate, expect_real_signal, expect_positive_score in cases:
        score, _reason, real_signal = pd._name_overlap(target, candidate)
        assert real_signal is expect_real_signal, f"{label}: expected real_signal={expect_real_signal}, got {real_signal}"
        assert (score > 0.0) is expect_positive_score, f"{label}: expected score>0 == {expect_positive_score}, got score={score}"

    # THE SIBLING REASON, verbatim, and NOT the containment tier's reason --
    # a mutation that left the OLD tier-2 reason on a sibling pair (the exact
    # round-3 defect, just with the label swapped) would fail here.
    for target, candidate in [("가상3거리", "가상거리"), ("신흥들3", "신흥들4"), ("원신흥4", "원신흥네거리")]:
        _score, reason, _real_signal = pd._name_overlap(target, candidate)
        assert reason == "숫자만 다른 이름입니다 — 같은 계열의 다른 번호일 수 있습니다.", (target, candidate, reason)
        # SANITY, self-documenting: none of these pairs may satisfy RAW
        # containment, or the fixture itself would be testing the wrong tier.
        assert target not in candidate and candidate not in target, (target, candidate)

    # THE ORDERING REQUIREMENT (round 4, orchestrator): digit-sibling must
    # rank ABOVE the fallback tier for the SAME pair -- stripped CONTAINMENT
    # (every character of the shorter name appears, in order, inside the
    # longer) is strictly more information than stripped SET overlap (the
    # same characters merely both appear somewhere). Verified structurally,
    # not just for one number: tier 2.5's ratio for "원신흥4"/"원신흥네거리"
    # is mathematically identical to what tier 3's ratio WOULD be for the
    # same pair (containment guarantees the shorter's whole character set is
    # a subset of the longer's), so the higher coefficient (0.6 vs 0.4) alone
    # determines the ordering -- exactly 1.5x, not a coincidence of this one
    # example.
    target, candidate = "원신흥4", "원신흥네거리"
    sibling_score, _reason, sibling_real_signal = pd._name_overlap(target, candidate)
    assert sibling_real_signal is False
    target_stripped, candidate_stripped = "원신흥", "원신흥네거리"
    would_be_fallback_common = set(target_stripped) & set(candidate_stripped)
    would_be_fallback_score = 0.4 * len(would_be_fallback_common) / max(
        len(set(target_stripped)), len(set(candidate_stripped))
    )
    assert sibling_score > would_be_fallback_score, (
        "digit-sibling must outrank the fallback tier for the same pair -- "
        "stripped containment is more information than shared characters"
    )
    assert sibling_score == pytest.approx(1.5 * would_be_fallback_score), (
        "expected the 0.6-vs-0.4 coefficient gap to be the ENTIRE reason for the ordering, exactly 1.5x"
    )


def test_node_candidates_digit_siblings_never_flip_discriminates_the_round3_repro():
    """ROUND 4 (orchestrator-decided semantics): the round-3 refuter's ONE
    surviving HIGH, reproduced directly. Round 3's stripped-containment tier
    2 scored FOUR DISTINCT physical intersections -- "신흥들1"/"2"/"4"/"5" --
    as identical 0.8 "포함됩니다" ties against target "신흥들3", erasing the
    exact digit that disambiguates separate intersections in this product's
    own Daejeon naming convention, and treated "원신흥4" as if it were
    genuinely CONTAINED in "원신흥네거리" (real_signal=True, score 0.533) when
    no such containment exists on the real strings at all.

    A digit-only difference is real information for a human reviewer -- worth
    ranking above pure noise, with its own honest reason -- but it is NOT a
    name match, so a screen full of digit siblings must never activate the
    truncation cap.
    """
    siblings = [
        DraftNode(id=1, name="신흥들1", osm_id=1, x=0.0, y=0.0, polygon=[]),
        DraftNode(id=2, name="신흥들2", osm_id=2, x=0.0, y=0.0, polygon=[]),
        DraftNode(id=3, name="신흥들4", osm_id=3, x=0.0, y=0.0, polygon=[]),
        DraftNode(id=4, name="신흥들5", osm_id=4, x=0.0, y=0.0, polygon=[]),
    ]
    flat = _osm_style_nodes(21, start_id=5)
    draft = DraftNetwork(name="siblings", source_osm="region.osm", nodes=siblings + flat)
    candidates = pd.node_candidates(draft, intersection_name="신흥들3")
    sibling_candidates = [c for c in candidates if c["node_id"] in {1, 2, 3, 4}]
    assert len(sibling_candidates) == 4, "sanity: all four siblings must still be OFFERED, just not as a name match"
    assert all(c["match_score"] > 0.0 for c in sibling_candidates), "a digit-sibling still ranks above pure noise"
    assert len(candidates) == 25, "a digit-only difference among candidates must NOT activate the truncation cap"


def test_node_candidates_ranks_real_signal_before_score_the_round4_repro():
    """ROUND 5 (refuter, MEDIUM, round 4): tier 2.5's coefficient (0.6) can
    exceed a WEAK true containment match's score -- `0.8 * shorter / longer`
    is small when the two names are very different lengths. MEASURED: target
    "가상거리" vs true containment "가상거리앞쪽" scores 0.533 (real_signal
    True) while sibling "가상3거리" scores 0.6 (real_signal False), so a
    SCORE-ONLY sort listed the wrong node first -- the truncation cap was
    never unsafe (real_signal alone still governs `discriminates`), but the
    visual scan order lied about which node was the genuine name match.
    """
    nodes = [
        DraftNode(id=1, name="가상거리앞쪽", osm_id=1, x=0.0, y=0.0, polygon=[]),  # true containment, ~0.533, real True
        DraftNode(id=2, name="가상3거리", osm_id=2, x=0.0, y=0.0, polygon=[]),  # digit-sibling, 0.6, real False
    ]
    draft = DraftNetwork(name="round5-repro", source_osm="region.osm", nodes=nodes)
    candidates = pd.node_candidates(draft, intersection_name="가상거리")
    assert candidates[0]["match_score"] < candidates[1]["match_score"], (
        "sanity: the score-only bug would have been invisible if the scores agreed"
    )
    assert [c["node_id"] for c in candidates] == [1, 2], (
        "expected the true containment match (node 1) to rank FIRST despite its lower raw score -- "
        "real name signal must outrank every hint tier regardless of either tier's own formula"
    )


def test_node_candidates_full_order_groups_by_real_signal_then_score():
    """The five-tier scan order, all at once: exact, containment, and
    digit-sibling scores do NOT sort by magnitude alone -- the order groups
    by `real_signal` FIRST (exact and containment together, both real;
    sibling, fallback and zero together, all hints), and only within each
    group does score break the tie. Node ids are deliberately scrambled and
    out of both scan order and insertion order, so a bug that fell back to
    node_id (or to source order) is caught here too.
    """
    nodes = [
        DraftNode(id=5, name="가상3거리", osm_id=5, x=0.0, y=0.0, polygon=[]),  # sibling, 0.6, hint
        DraftNode(id=1, name="가상거리앞쪽", osm_id=1, x=0.0, y=0.0, polygon=[]),  # containment, ~0.533, real
        DraftNode(id=4, name="Node 999999", osm_id=999999, x=0.0, y=0.0, polygon=[]),  # zero, 0.0, hint
        DraftNode(id=3, name="가상거리", osm_id=3, x=0.0, y=0.0, polygon=[]),  # exact, 1.0, real
        DraftNode(id=2, name="상거마을", osm_id=2, x=0.0, y=0.0, polygon=[]),  # fallback, 0.2, hint
    ]
    draft = DraftNetwork(name="round5-mixed", source_osm="region.osm", nodes=nodes)
    candidates = pd.node_candidates(draft, intersection_name="가상거리")
    assert [c["node_id"] for c in candidates] == [3, 1, 5, 2, 4], (
        "expected exact(1.0,real) > containment(0.533,real) > sibling(0.6,hint) > fallback(0.2,hint) > zero(0.0,hint) -- "
        "the two REAL entries first regardless of score, then score breaks ties WITHIN each group"
    )


def test_node_candidates_ignores_an_all_digit_target_at_the_containment_tier():
    """ROUND 3 FINDING A (refuter, HIGH -- CLASS CLOSURE): round 2's digit
    exclusion covered only the FALLBACK tier. Tier 2 (substring containment)
    was left completely UNCONDITIONAL, so an all-digit `intersection_name`
    such as "18" -- entered through the manual blank-review or excel_upload
    doors, not just read off an OSM draft's own node names -- is a literal
    substring of "node<osmid>" for MOST osmids in a real draft (this
    fixture's own node id 18, "Node 900018", contains "18" outright).
    MEASURED (refuter): a 25-node draft returned only 8.
    """
    nodes = _osm_style_nodes(25)
    draft = DraftNetwork(name="osm-draft", source_osm="region.osm", nodes=nodes)
    candidates = pd.node_candidates(draft, intersection_name="18")
    assert len(candidates) == 25, "an all-digit target must not discriminate via substring containment either"
    assert all(c["match_score"] == 0.0 for c in candidates)


def test_node_candidates_a_fallback_only_match_does_not_flip_discriminates():
    """ROUND 3 FINDING D (refuter, coverage gap): `discriminates = any(
    real_signal for ...)` was defended only by a STRING pin in the frontend
    contract suite -- mutating it back to `any(score > 0.0 for ...)` passed
    all of THIS file's tests too, because no fixture made `real_signal` and
    `score > 0` actually DIVERGE. This one does, deliberately: a fallback-tier
    -only match (shared non-digit Korean characters, no substring or exact
    hit) scores POSITIVE but is NOT a real signal, so `discriminates` must
    stay False and every node must still be returned. (Mutation-verified by
    hand per the refuter's instruction -- see the round-3 handoff notes.)
    """
    nodes = [
        # fallback-tier match against "가상감응네거리": shares 가,상,거,리,
        # but is neither equal nor a substring either way -- see the tier
        # matrix test above for the same pair, asserted at the _name_overlap
        # level.
        DraftNode(id=1, name="가상삼거리", osm_id=1, x=0.0, y=0.0, polygon=[]),
    ] + _osm_style_nodes(24, start_id=2)
    draft = DraftNetwork(name="fallback-only", source_osm="region.osm", nodes=nodes)
    candidates = pd.node_candidates(draft, intersection_name="가상감응네거리")
    fallback_candidate = next(c for c in candidates if c["node_id"] == 1)
    assert fallback_candidate["match_score"] > 0.0, "sanity: the fallback tier really did score this one positive"
    assert len(candidates) == 25, (
        "a fallback-tier-only positive score must NOT flip discriminates -- it is not a real name signal"
    )


def test_an_approach_bearing_names_the_side_the_traffic_comes_from(crossroads):
    """The link whose geometry runs from +y down to the node is the NORTH leg —
    the heading is southbound and the leg lies opposite it."""
    north = next(link for link in crossroads.links if link.id == 1)
    bearing = pd.approach_bearing(north.points)
    assert bearing is not None and bearing[1] == "N"
    assert pd.approach_bearing([(0.0, 0.0)]) is None
    assert pd.approach_bearing([(0.0, 0.0), (0.0, 0.0)]) is None


# ---------------------------------------------------------------------------
# §8 The HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield tmp_path / "workspaces"
    finally:
        workspaces.reset_current_workspace_id(token)


@pytest.fixture()
def client(workspace_root) -> TestClient:
    return TestClient(main.app)


@pytest.fixture()
def headers() -> dict[str, str]:
    return bearer_headers("phase-diagram-reviewer", "reviewer")


@pytest.fixture()
def seeded(workspace_root, render) -> str:
    """One review, rendered and proposed, in the workspace the routes read."""
    png, record = render
    store = pd.PhaseDiagramStore(
        genesis_jobs.genesis_dir() / "phase-diagrams"
    )
    review_id = store.put_render(png, record, sheet_text=sheet_text())
    proposal = proposal_for(record, png)
    store.put_proposal(review_id, proposal, pd.cross_check(proposal, sheet=None, render=record))
    return review_id


def test_the_review_routes_refuse_an_anonymous_caller(client, seeded):
    for path in (
        "/api/genesis/phase-diagrams",
        f"/api/genesis/phase-diagrams/{seeded}",
        f"/api/genesis/phase-diagrams/{seeded}/image",
    ):
        assert client.get(path).status_code == 401, path
    assert client.post(
        f"/api/genesis/phase-diagrams/{seeded}/confirmation", json={}
    ).status_code == 401


def test_a_signed_in_reviewer_reads_the_review_and_its_image(client, headers, seeded, render):
    _png, record = render
    listed = client.get("/api/genesis/phase-diagrams", headers=headers).json()["reviews"]
    assert [row["review_id"] for row in listed] == [seeded]
    assert listed[0]["confirmed"] is False and listed[0]["proposal_outcome"] == "proposed"

    payload = client.get(f"/api/genesis/phase-diagrams/{seeded}", headers=headers).json()
    assert payload["render"]["image_sha256"] == record.image_sha256
    assert payload["proposal"]["outcome"] == "proposed"
    assert payload["attestation"] is None and payload["confirmed"] is False

    image = client.get(f"/api/genesis/phase-diagrams/{seeded}/image", headers=headers)
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")


def test_an_unknown_review_is_a_404_with_a_code(client, headers):
    response = client.get("/api/genesis/phase-diagrams/" + "0" * 16, headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "phase_diagram_unknown_review"


def test_the_candidate_route_discloses_a_real_truncation(
    client, headers, seeded, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Unit #62's HTTP half: the route's disclosure fields, over the wire.

    ``genesis_jobs.artifact_path`` is monkeypatched to a hand-built census
    rather than run through the real OSM generator -- the truncation this test
    proves needs more than 8 nodes with a mix of scores, which a synthetic OSM
    fixture is the wrong tool to grow just for this. The seeded review's own
    교차로명 (``actuated_variant.txt``'s "가상감응네거리") is the discriminating
    target, unchanged from the fixture every other test in this file already
    uses.
    """
    # UNIT #62 FINDING 1: these three must land on a REAL signal tier (exact
    # match or substring containment) -- the fallback tier's character
    # overlap no longer counts toward `discriminates` at all, so a fixture
    # built on it (the pre-fix version of this test used "가상삼거리" etc.,
    # fallback-tier matches) would wrongly assert a truncation that could no
    # longer happen.
    discriminating = [
        DraftNode(id=1, name="가상감응네거리", osm_id=1, x=0.0, y=0.0, polygon=[]),  # exact match
        DraftNode(id=2, name="가상감응", osm_id=2, x=0.0, y=0.0, polygon=[]),  # target contains this
        DraftNode(id=3, name="가상감응네거리앞", osm_id=3, x=0.0, y=0.0, polygon=[]),  # this contains target
    ]
    flat = _osm_style_nodes(8, start_id=10)
    draft = DraftNetwork(name="mixed", source_osm="mixed.osm", nodes=discriminating + flat)
    census_path = tmp_path / "mixed.genesis.json"
    census_path.write_text(draft.model_dump_json(), encoding="utf-8")

    def fake_artifact_path(job_id: str, kind: str) -> Path:
        assert kind == "genesis_json"
        return census_path

    monkeypatch.setattr(genesis_jobs, "artifact_path", fake_artifact_path)

    response = client.get(
        f"/api/genesis/phase-diagrams/{seeded}/network-candidates",
        headers=headers,
        params={"job_id": "does-not-need-to-exist"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_nodes"] == 11, "the draft's own node count, not the truncated one"
    assert payload["returned_nodes"] == 8
    assert len(payload["candidates"]) == 8, "the payload's own list must agree with returned_nodes"


def test_the_candidate_route_discloses_no_truncation_when_the_signal_cannot_discriminate(
    client, headers, seeded, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """Same route, the OTHER measured shape: an OSM draft whose every node name
    is "Node <osmid>" gets total==returned -- every node offered, nothing to
    disclose as cut, matching `node_candidates`'s own zero-signal behaviour."""
    nodes = _osm_style_nodes(25)
    draft = DraftNetwork(name="osm-draft", source_osm="region.osm", nodes=nodes)
    census_path = tmp_path / "osm.genesis.json"
    census_path.write_text(draft.model_dump_json(), encoding="utf-8")

    def fake_artifact_path(job_id: str, kind: str) -> Path:
        assert kind == "genesis_json"
        return census_path

    monkeypatch.setattr(genesis_jobs, "artifact_path", fake_artifact_path)

    response = client.get(
        f"/api/genesis/phase-diagrams/{seeded}/network-candidates",
        headers=headers,
        params={"job_id": "does-not-need-to-exist"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_nodes"] == 25
    assert payload["returned_nodes"] == 25
    assert len(payload["candidates"]) == 25


def _confirmation_body(record: pd.DiagramRender, **overrides: Any) -> dict[str, Any]:
    body = {
        "node_id": 1,
        "approach_phases": {"1": "Φ1", "3": "Φ2", "5": "Φ3", "7": "Φ4"},
        "approach_links": {"1": "NORTH LEG"},
        "level_no": 1,
        "plan_id": 1,
        "image_sha256": record.image_sha256,
        "source_sha256": record.source_sha256,
        "proposal_outcome": "proposed",
        "declared_amber_s": DECLARED_AMBER_S,
        "amber_declaration_note": AMBER_NOTE,
    }
    body.update(overrides)
    return body


def declare_amber_over_http(client, headers, review_id: str, amber: float = DECLARED_AMBER_S):
    """The screen's own two-step, over HTTP: declare the 황색시간 in the 신호 시간
    table (unit #58's stamped PATCH), THEN confirm. `put_attestation` refuses a
    confirmation carrying an amber the store does not hold, so this is not test
    scaffolding — it is the order the product performs."""
    response = client.patch(
        f"/api/genesis/phase-diagrams/{review_id}/timing",
        headers=headers,
        json={"field": "declared_amber_s", "value": amber},
    )
    assert response.status_code == 200, response.text
    return response


def test_the_attester_is_the_session_never_the_request_body(client, headers, seeded, render):
    """A caller naming somebody else as the attester is the one thing an
    attestation must not permit — so there is no field to put a name in."""
    _png, record = render
    declare_amber_over_http(client, headers, seeded)
    rejected = client.post(
        f"/api/genesis/phase-diagrams/{seeded}/confirmation",
        headers=headers,
        json=_confirmation_body(record, attested_by="somebody-else"),
    )
    assert rejected.status_code == 422, "extra='forbid' — the field does not exist"

    response = client.post(
        f"/api/genesis/phase-diagrams/{seeded}/confirmation",
        headers=headers,
        json=_confirmation_body(record),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["confirmed"] is True
    assert payload["attestation"]["attested_by"] == "phase-diagram-reviewer"
    assert payload["attestation"]["attested_at"], "the server's clock, not the client's"


def test_confirming_against_a_stale_image_is_a_409_the_ui_can_translate(
    client, headers, seeded, render
):
    _png, record = render
    response = client.post(
        f"/api/genesis/phase-diagrams/{seeded}/confirmation",
        headers=headers,
        json=_confirmation_body(record, image_sha256="c" * 64),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "phase_diagram_attestation_stale"
    assert any("재확인" in reason for reason in response.json()["detail"]["reasons"])


def test_an_unconfirmed_review_cannot_be_staged_as_a_signals_input(client, headers, seeded):
    """Acceptance 2, over HTTP: the browser cannot route around the refusal,
    because the document is never built on the browser's side."""
    response = client.post(
        "/api/genesis/phase-diagrams/signals-input",
        headers=headers,
        json={"review_ids": [seeded]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "phase_diagram_not_confirmed"


def test_a_confirmed_review_stages_a_signals_side_input_through_the_shipped_door(
    client, headers, seeded, render
):
    _png, record = render
    declare_amber_over_http(client, headers, seeded)
    client.post(
        f"/api/genesis/phase-diagrams/{seeded}/confirmation",
        headers=headers,
        json=_confirmation_body(record),
    )
    response = client.post(
        "/api/genesis/phase-diagrams/signals-input",
        headers=headers,
        json={"review_ids": [seeded]},
    )
    assert response.status_code == 200, response.text
    staged = response.json()
    assert staged["kind"] == "signals" and staged["token"]
    assert staged["summary"]["doc_kind"] == "genesis.signals.v1"
    assert staged["summary"]["assignments"][0]["node_id"] == 1
    # The token resolves in the SAME staging area POST /jobs reads.
    assert genesis_jobs.staged_side_input(staged["token"])["kind"] == "signals"
