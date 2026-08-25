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
    ]
    source = inspect.getsource(pd.build_signals_document)
    for banned in ("proposal", "skip", "allow_unconfirmed", "force"):
        assert banned not in source.split('"""')[2], f"{banned!r} must not be on the emit path"


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
