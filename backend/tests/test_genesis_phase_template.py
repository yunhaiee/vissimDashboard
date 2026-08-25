"""Unit #52 — 현시도 입력: the template out, a workbook in, the draft back out.

The suite is written the way ``test_genesis_phase_diagram.py``'s is: around the
refusals, because the refusals ARE the feature. Three new doors into the review
store were opened by this unit (a downloaded template filled in by hand, an
uploaded workbook, a blank review typed on the screen) and every one of them
must land in exactly the same place the vision path lands — a PROPOSAL that is
inert until a named human clicks 확인.

So, in order:

* **§1 The template says what it is.** The file travels without the app around
  it, so the 안내 sheet states the doctrine and carries the version stamp the
  parser checks on the way back.
* **§2 A bad row never silently drops, and never half-imports.** Every named
  refusal is fired by a crafted row, and a refused row takes its whole
  intersection with it — an intersection missing one leg is a junction with one
  approach quietly unassigned.
* **§3 The bomb guard runs FIRST.** Monkeypatch-spied, because "we call the
  guard" is a convention until a test fails without it.
* **§4 An uploaded sheet is a proposal, not a fact.** No attestation is written
  by any upload; the review carries no image and never pretends to; the
  confirmation door is unchanged and still refuses to stage anything unconfirmed.
* **§5 The export is labelled 미확인 on every sheet**, carries every ambiguity
  sentence the model wrote, and re-parses when uploaded again.

No client data: every workbook here is built in the test.
"""
from __future__ import annotations

import ast
import json
import pathlib
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

import openpyxl
import pytest
from fastapi.testclient import TestClient

import main
from conftest import bearer_headers
from services import genesis_jobs, workspaces
from services.dataimport import xlsx_guard
from services.genesis import phase_diagram as pd
from services.genesis import phase_template as pt
from services.genesis import signals_declared as sd
from services.genesis.side_inputs import build_signal_assignments, parse_side_input
from services.genesis.signals_daejeon import DaejeonSettings, parse_sheet_text, to_fixed_time_plan

TEMPLATE_ROUTE = "/api/genesis/phase-diagrams/template.xlsx"
UPLOAD_ROUTE = "/api/genesis/phase-diagrams/upload"
BLANK_ROUTE = "/api/genesis/phase-diagrams/blank"
EXPORTS_ROUTE = "/api/genesis/phase-diagrams/exports.xlsx"


# ---------------------------------------------------------------------------
# Workbook builders — the shapes a client actually sends us
# ---------------------------------------------------------------------------


def workbook(
    rows: list[list[Any]],
    *,
    version: str | None = pt.TEMPLATE_KIND,
    headers: list[str] | None = None,
    sheet_title: str = pt.ROWS_SHEET,
    timing: list[list[Any]] | None = None,
    timing_headers: list[str] | None = None,
) -> bytes:
    """One workbook shaped like the template, with everything parameterised that
    a test needs to break: the stamp, the header row, the sheet name, and the
    optional 신호시간 table."""
    book = openpyxl.Workbook()
    guide = book.active
    guide.title = pt.GUIDE_SHEET
    guide.append([pt.GUIDE_HEADER])
    if version is not None:
        guide.append([f"{pt.TEMPLATE_VERSION_LABEL}: {version}"])
    sheet = book.create_sheet(sheet_title)
    sheet.append(list(headers if headers is not None else pt.COLUMNS))
    for row in rows:
        sheet.append(list(row))
    if timing is not None:
        timing_sheet = book.create_sheet(pt.TIMING_SHEET)
        timing_sheet.append(list(timing_headers if timing_headers is not None else pt.TIMING_COLUMNS))
        for row in timing:
            timing_sheet.append(list(row))
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


GOOD_ROWS: list[list[Any]] = [
    ["1234", "둔산네거리", "Φ1", "둔산로(북)", "직진, 우회전", 3.0, "설계지침 4.2", ""],
    ["1234", "둔산네거리", "Φ1", "둔산로(남)", "직진", "", "", ""],
    ["1234", "둔산네거리", "Φ2", "둔산로(북)", "좌회전", "", "", "화살표 겹침"],
    ["", "탄방역네거리", "2현시", "대덕대로(동)", "직진", "", "", ""],
]


def sheet_cells(content: bytes) -> dict[str, list[str]]:
    book = openpyxl.load_workbook(BytesIO(content))
    try:
        return {
            worksheet.title: [
                str(value)
                for row in worksheet.iter_rows(values_only=True)
                for value in row
                if value is not None
            ]
            for worksheet in book.worksheets
        }
    finally:
        book.close()


# ---------------------------------------------------------------------------
# §1 The template says what it is
# ---------------------------------------------------------------------------


def test_the_template_carries_its_columns_its_version_and_the_doctrine():
    content = pt.build_template()
    book = openpyxl.load_workbook(BytesIO(content))
    try:
        assert book.sheetnames == [pt.GUIDE_SHEET, pt.ROWS_SHEET, pt.TIMING_SHEET]
        header = [cell.value for cell in book[pt.ROWS_SHEET][1]]
        assert header == list(pt.COLUMNS), "the parser finds columns BY TITLE"
        timing_header = [cell.value for cell in book[pt.TIMING_SHEET][1]]
        assert timing_header == list(pt.TIMING_COLUMNS)
    finally:
        book.close()

    cells = sheet_cells(content)
    guide = " ".join(cells[pt.GUIDE_SHEET])
    # The timing sheet's own rule, in the file: optional as a whole, complete
    # when present, and the clearance is never assumed to be zero.
    assert "소거" in guide and "거부합니다" in guide
    # The stamp the parser looks for, in a cell, in the file.
    assert f"{pt.TEMPLATE_VERSION_LABEL}: {pt.TEMPLATE_KIND}" in guide
    # The doctrine, stated in the file itself because the file travels without
    # the app around it.
    assert "제안" in guide and "사실이 아닙니다" in guide
    assert "확인" in guide
    # A blank template is NOT a draft: nothing in it may claim a reading exists.
    assert pt.UNCONFIRMED_MARK not in " ".join(sum(cells.values(), []))
    # The example row is present and marked as one.
    assert any(value.startswith(pt.EXAMPLE_PREFIX) for value in cells[pt.ROWS_SHEET])


def test_uploading_the_untouched_template_refuses_by_its_own_name():
    """"표가 비었습니다" would send a reader hunting a broken file. What actually
    happened is that they uploaded the blank form."""
    with pytest.raises(pd.PhaseDiagramRefused) as caught:
        pt.parse_template(pt.build_template(), source_file="현시도-입력-템플릿.xlsx")
    assert caught.value.code == "phase_template_only_example_rows"


# ---------------------------------------------------------------------------
# §2 A bad row never silently drops, and never half-imports
# ---------------------------------------------------------------------------


def test_a_filled_template_becomes_one_proposal_per_intersection():
    parsed = pt.parse_template(workbook(GOOD_ROWS), source_file="filled.xlsx")
    assert [entry.name for entry in parsed.intersections] == ["둔산네거리", "탄방역네거리"]
    assert parsed.refusals == []

    dunsan = parsed.intersections[0]
    assert dunsan.intersection_no == "1234"
    assert dunsan.declared_amber_s == 3.0
    assert dunsan.amber_note == "설계지침 4.2"
    proposal = dunsan.to_proposal(source_file="filled.xlsx", uploaded_by="tester")
    assert proposal.outcome == "proposed"
    assert proposal.source == "excel_upload" and proposal.uploaded_by == "tester"
    # An uploaded sheet was read by nobody's model and shows no image.
    assert proposal.model is None and proposal.image_sha256 is None
    assert proposal.phase_ids == ["Φ1", "Φ2"]
    assert [
        (movement.approach_label, movement.turn)
        for movement in proposal.phases[0].movements
    ] == [("둔산로(북)", "through"), ("둔산로(북)", "right"), ("둔산로(남)", "through")]
    # 'Φ' is optional on the way in: '2현시' is the same phase as 'Φ2'.
    assert parsed.intersections[1].to_proposal(
        source_file="filled.xlsx", uploaded_by="tester"
    ).phase_ids == ["Φ2"]


def test_a_word_the_movement_table_does_not_know_keeps_its_own_text():
    """Never rounded to 'through': the reviewer sees the word the sheet used."""
    parsed = pt.parse_template(
        workbook([["", "가교차로", "Φ1", "북", "비보호좌회전", "", "", ""]]),
        source_file="odd.xlsx",
    )
    movement = parsed.intersections[0].to_proposal(
        source_file="odd.xlsx", uploaded_by="tester"
    ).phases[0].movements[0]
    assert movement.turn == "unknown"
    assert movement.note is not None and "비보호좌회전" in movement.note


@pytest.mark.parametrize(
    "row, code",
    [
        (["", "", "Φ1", "북", "직진", "", "", ""], "phase_template_row_no_intersection"),
        (["", "가교차로", "열일곱", "북", "직진", "", "", ""], "phase_template_row_bad_phase"),
        (["", "가교차로", "Φ99", "북", "직진", "", "", ""], "phase_template_row_bad_phase"),
        (["", "가교차로", "Φ1", "", "직진", "", "", ""], "phase_template_row_no_approach"),
        (["", "가교차로", "Φ1", "북", "직진", "삼초", "", ""], "phase_template_row_amber_invalid"),
    ],
)
def test_every_bad_row_refuses_by_name_and_names_its_row_number(row, code):
    parsed = pt.parse_template(workbook([row]), source_file="bad.xlsx")
    refusal = next(item for item in parsed.refusals if item.code == code)
    assert refusal.row == 2, "1-based, as Excel itself numbers rows"
    assert refusal.reasons and "2행" in refusal.reasons[0]
    assert parsed.intersections == [], "a refused row never half-imports its intersection"


def test_two_different_ambers_for_one_intersection_is_a_refusal_not_a_choice():
    parsed = pt.parse_template(
        workbook(
            [
                ["", "가교차로", "Φ1", "북", "직진", 3.0, "근거", ""],
                ["", "가교차로", "Φ2", "남", "직진", 4.0, "다른 근거", ""],
            ]
        ),
        source_file="conflict.xlsx",
    )
    codes = [refusal.code for refusal in parsed.refusals]
    assert "phase_template_row_amber_conflict" in codes
    assert pt.REFUSED_INTERSECTION in codes
    assert parsed.intersections == []


def test_one_bad_row_takes_only_its_own_intersection_down():
    parsed = pt.parse_template(
        workbook(GOOD_ROWS + [["", "탄방역네거리", "Φ열", "대덕대로(서)", "직진", "", "", ""]]),
        source_file="mixed.xlsx",
    )
    assert [entry.name for entry in parsed.intersections] == ["둔산네거리"]
    refused = [item for item in parsed.refusals if item.code == pt.REFUSED_INTERSECTION]
    assert [item.intersection for item in refused] == ["탄방역네거리"]


# ---------------------------------------------------------------------------
# §2b The export's own memo rows come back as notes, not refusals
# (chip task_114c3f1a, docs/phase2/DECISIONS.md 2026-08-24 "인터페이스 갭")
# ---------------------------------------------------------------------------


def test_a_note_row_with_no_phase_or_approach_becomes_an_intersection_note():
    """Empty 현시번호, empty 접근로, a non-empty 비고 — exactly the shape
    ``build_export``'s own :625/:682/:701 rows write. It is the export's own
    memo coming back, not a malformed assignment row someone mistyped, so it
    is not refused and does not poison the intersection it belongs to."""
    parsed = pt.parse_template(
        workbook(
            GOOD_ROWS
            + [["1234", "둔산네거리", "", "", "", "", "", "메모: 신호시간은 별도 표에 있습니다"]]
        ),
        source_file="notes.xlsx",
    )
    assert parsed.refusals == []
    entry = next(item for item in parsed.intersections if item.name == "둔산네거리")
    assert entry.notes == [
        "신호시간은 별도 표에 있습니다"
    ], "the 메모: prefix is stripped, the rest kept verbatim"
    proposal = entry.to_proposal(source_file="notes.xlsx", uploaded_by="tester")
    assert proposal.notes == ["신호시간은 별도 표에 있습니다"]


def test_a_note_row_with_no_memo_prefix_is_kept_verbatim():
    """Not every note row carries ``build_export``'s ``메모: `` prefix — its
    own refused/missing-proposal reason row (:682) does not — so the prefix
    must be stripped only when present, never assumed."""
    parsed = pt.parse_template(
        workbook(
            GOOD_ROWS
            + [["1234", "둔산네거리", "", "", "", "", "", "판독 없음 — 직접 입력하십시오"]]
        ),
        source_file="notes-no-prefix.xlsx",
    )
    entry = next(item for item in parsed.intersections if item.name == "둔산네거리")
    assert entry.notes == ["판독 없음 — 직접 입력하십시오"]


def test_a_bad_phase_row_with_an_approach_still_refuses_even_with_a_note():
    """A non-empty 접근로 means this really is a malformed assignment row, not
    an intersection memo — a filled 비고 does not rescue it, and it still
    takes its whole intersection down exactly as an approach-less bad row
    always has."""
    parsed = pt.parse_template(
        workbook(
            GOOD_ROWS
            + [["", "탄방역네거리", "", "대덕대로(서)", "", "", "", "메모: 뭔가 적혀 있습니다"]]
        ),
        source_file="mixed-note.xlsx",
    )
    codes = [refusal.code for refusal in parsed.refusals]
    assert "phase_template_row_bad_phase" in codes
    assert pt.REFUSED_INTERSECTION in codes
    assert [entry.name for entry in parsed.intersections] == [
        "둔산네거리"
    ], "탄방역네거리 is still poisoned"


def test_an_intersection_with_only_note_rows_refuses_instead_of_becoming_a_phantom():
    """No phase-assignment row at all — exactly ``build_export``'s :682
    reason row for an intersection whose reading was refused or missing.
    Must not silently vanish, and must not import as an empty (phantom)
    intersection either."""
    parsed = pt.parse_template(
        workbook(
            [["", "판독없음교차로", "", "", "", "", "", "메모: 판독 없음 — 직접 입력하십시오"]]
        ),
        source_file="notes-only.xlsx",
    )
    assert parsed.intersections == []
    refusal = next(item for item in parsed.refusals if item.intersection == "판독없음교차로")
    assert refusal.code == "phase_template_intersection_empty"


# -- 교차로 ID conflicts: first-non-empty-silently-wins is the same lie an
# amber conflict is (refuter finding B.2) — a memo row pasted above, below, or
# copied from another intersection's export block must not hand a real
# intersection a wrong ID with zero refusals. -------------------------------


def test_a_note_row_before_the_phase_rows_with_a_conflicting_id_is_refused():
    """Note-row-FIRST ordering: the note states 999, the real rows state
    1234. Pre-fix this silently adopted 999 (first non-empty wins); it must
    now refuse instead of importing under the wrong ID."""
    rows = [
        ["999", "둔산네거리", "", "", "", "", "", "메모: 먼저 온 메모"],
    ] + GOOD_ROWS
    parsed = pt.parse_template(workbook(rows), source_file="conflict-note-first.xlsx")
    codes = [refusal.code for refusal in parsed.refusals]
    assert "phase_template_row_intersection_no_conflict" in codes
    assert pt.REFUSED_INTERSECTION in codes
    assert [entry.name for entry in parsed.intersections] == [
        "탄방역네거리"
    ], "둔산네거리 is poisoned by the ID conflict; the untouched intersection survives"


def test_a_note_row_after_the_phase_rows_with_a_conflicting_id_is_refused():
    """Note-row-LAST ordering, the symmetric case: the real rows state 1234
    first, a memo row copied from another intersection's block states 999
    afterwards."""
    rows = list(GOOD_ROWS) + [
        ["999", "둔산네거리", "", "", "", "", "", "메모: 나중에 온 메모"],
    ]
    parsed = pt.parse_template(workbook(rows), source_file="conflict-note-last.xlsx")
    codes = [refusal.code for refusal in parsed.refusals]
    assert "phase_template_row_intersection_no_conflict" in codes
    assert pt.REFUSED_INTERSECTION in codes
    assert [entry.name for entry in parsed.intersections] == ["탄방역네거리"]


def test_agreeing_ids_across_a_note_row_and_phase_rows_import_fine():
    """The positive case: a note row and its intersection's phase rows STATE
    the same ID. No conflict, no refusal, and the note survives alongside
    it."""
    rows = [
        ["1234", "둔산네거리", "", "", "", "", "", "메모: 동일한 ID"],
    ] + GOOD_ROWS
    parsed = pt.parse_template(workbook(rows), source_file="agree.xlsx")
    assert parsed.refusals == []
    entry = next(item for item in parsed.intersections if item.name == "둔산네거리")
    assert entry.intersection_no == "1234"
    assert entry.notes == ["동일한 ID"]


def test_two_phase_rows_with_conflicting_ids_are_refused_too():
    """The class this closes is not note-rows-only: an ordinary phase row
    disagreeing with an earlier phase row's ID was ALSO a silent first-wins
    before this fix, with no note row involved anywhere."""
    rows = list(GOOD_ROWS) + [
        ["9999", "둔산네거리", "Φ2", "둔산로(남)", "우회전", "", "", ""],
    ]
    parsed = pt.parse_template(workbook(rows), source_file="phase-conflict.xlsx")
    codes = [refusal.code for refusal in parsed.refusals]
    assert "phase_template_row_intersection_no_conflict" in codes
    assert pt.REFUSED_INTERSECTION in codes
    assert [entry.name for entry in parsed.intersections] == ["탄방역네거리"]


def test_an_empty_id_cell_never_conflicts_with_a_stated_one():
    """COL_INTERSECTION_NO is 선택 (optional): rows that leave it blank must
    never be the source of a conflict, before OR after the row that actually
    states the ID."""
    rows = [
        ["", "새터네거리", "Φ1", "새터로(북)", "직진", "", "", ""],
        ["5555", "새터네거리", "Φ1", "새터로(남)", "직진", "", "", ""],
        ["", "새터네거리", "Φ2", "새터로(북)", "좌회전", "", "", ""],
    ]
    parsed = pt.parse_template(workbook(rows), source_file="empty-ids.xlsx")
    assert parsed.refusals == []
    entry = next(item for item in parsed.intersections if item.name == "새터네거리")
    assert entry.intersection_no == "5555"


def test_a_memo_only_note_cell_produces_no_note():
    """Cosmetic defect B.4: ``_cell`` right-strips every cell, so a 비고 of
    exactly ``"메모: "`` (the marker and nothing else) arrives at the parser
    as the bare ``"메모:"`` — and either form must strip down to nothing, not
    survive as a literal stub note reading '메모:'."""
    parsed = pt.parse_template(
        workbook(
            GOOD_ROWS
            + [
                ["1234", "둔산네거리", "", "", "", "", "", "메모: "],
                ["1234", "둔산네거리", "", "", "", "", "", "메모:"],
            ]
        ),
        source_file="empty-memo.xlsx",
    )
    assert parsed.refusals == []
    entry = next(item for item in parsed.intersections if item.name == "둔산네거리")
    assert entry.notes == [], "a 비고 that is only the 메모: marker records no note, in either form"


def test_a_workbook_with_no_stamp_or_the_wrong_stamp_is_refused_by_name():
    with pytest.raises(pd.PhaseDiagramRefused) as missing:
        pt.parse_template(workbook(GOOD_ROWS, version=None), source_file="nostamp.xlsx")
    assert missing.value.code == "phase_template_version_missing"

    with pytest.raises(pd.PhaseDiagramRefused) as unknown:
        pt.parse_template(
            workbook(GOOD_ROWS, version="prism.phase_template.v9"), source_file="v9.xlsx"
        )
    assert unknown.value.code == "phase_template_version_unknown"
    assert any("prism.phase_template.v9" in reason for reason in unknown.value.reasons)


def test_renamed_columns_are_refused_rather_than_read_positionally():
    content = workbook(
        GOOD_ROWS, headers=["A", "B", "C", "D", "E", "F", "G", "H"]
    )
    with pytest.raises(pd.PhaseDiagramRefused) as caught:
        pt.parse_template(content, source_file="renamed.xlsx")
    assert caught.value.code == "phase_template_columns_missing"


def test_the_rows_sheet_is_found_by_its_columns_even_when_renamed():
    """Tolerance where it is safe: the SHEET name is a label, the COLUMN names
    are the contract."""
    parsed = pt.parse_template(
        workbook(GOOD_ROWS, sheet_title="현시입력(수정본)"), source_file="renamed-sheet.xlsx"
    )
    assert [entry.name for entry in parsed.intersections] == ["둔산네거리", "탄방역네거리"]


# ---------------------------------------------------------------------------
# §3 The bomb guard runs FIRST
# ---------------------------------------------------------------------------


def test_the_bomb_guard_is_called_before_openpyxl_sees_a_byte(monkeypatch):
    """Spied, not assumed. Without this the guard is a comment."""
    calls: list[str] = []
    real_guard = pt.guard_xlsx_bytes

    def spy(content: bytes, *, label: str = "upload") -> None:
        calls.append(label)
        real_guard(content, label=label)

    monkeypatch.setattr(pt, "guard_xlsx_bytes", spy)

    opened: list[str] = []
    real_load = openpyxl.load_workbook

    def watched_load(*args, **kwargs):
        opened.append("load")
        assert calls, "the guard must run BEFORE openpyxl is handed the bytes"
        return real_load(*args, **kwargs)

    monkeypatch.setattr(openpyxl, "load_workbook", watched_load)

    pt.parse_template(workbook(GOOD_ROWS), source_file="guarded.xlsx")
    assert calls == ["guarded.xlsx"] and opened == ["load"]


def test_a_bomb_is_refused_by_name_and_never_opened(monkeypatch):
    def explode(content: bytes, *, label: str = "upload") -> None:
        raise xlsx_guard.XlsxBombError(
            "crafted", detail={"code": "xlsx_member_too_large", "member": "sharedStrings.xml"}
        )

    monkeypatch.setattr(pt, "guard_xlsx_bytes", explode)
    monkeypatch.setattr(
        openpyxl,
        "load_workbook",
        lambda *a, **k: pytest.fail("openpyxl must never see a refused upload"),
    )
    with pytest.raises(pd.PhaseDiagramRefused) as caught:
        pt.parse_template(workbook(GOOD_ROWS), source_file="bomb.xlsx")
    assert caught.value.code == "phase_template_bomb_refused"


# ---------------------------------------------------------------------------
# §4 The HTTP surface — three doors, one destination
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
    return bearer_headers("phase-uploader", "reviewer")


@pytest.fixture()
def store(workspace_root) -> pd.PhaseDiagramStore:
    return pd.PhaseDiagramStore(genesis_jobs.genesis_dir() / "phase-diagrams")


def upload(client: TestClient, headers: dict[str, str], content: bytes, name: str):
    """POST one workbook the way the browser does.

    ``quote(name, safe="")`` mirrors ``lib/api.ts``'s ``encodeURIComponent`` and
    is load-bearing rather than tidy: a RAW ``#`` in a query string is a
    FRAGMENT, so an unencoded name is truncated by the client before the server
    ever sees it — which is precisely how the first version of the delimiter
    test passed against a key that still collided.
    """
    return client.post(
        f"{UPLOAD_ROUTE}?filename={quote(name, safe='')}", headers=headers, content=content
    )


def test_every_new_route_refuses_an_anonymous_caller(client):
    assert client.get(TEMPLATE_ROUTE).status_code == 401
    assert client.get(EXPORTS_ROUTE).status_code == 401
    assert client.post(UPLOAD_ROUTE, content=b"x").status_code == 401
    assert client.post(BLANK_ROUTE, json={"intersection_name": "가"}).status_code == 401


def test_the_template_downloads_as_a_readable_workbook(client, headers):
    response = client.get(TEMPLATE_ROUTE, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(pt.XLSX_MEDIA_TYPE)
    book = openpyxl.load_workbook(BytesIO(response.content))
    try:
        assert [cell.value for cell in book[pt.ROWS_SHEET][1]] == list(pt.COLUMNS)
    finally:
        book.close()


def test_an_upload_creates_reviews_that_are_proposals_and_nothing_more(
    client, headers, store
):
    response = upload(client, headers, workbook(GOOD_ROWS), "현시도.xlsx")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["refusals"] == []
    assert [row["intersection_name"] for row in body["created"]] == ["둔산네거리", "탄방역네거리"]
    assert all(row["replaced"] is False for row in body["created"])

    review_id = body["created"][0]["review_id"]
    payload = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers).json()
    assert payload["render"]["medium"] == "excel_upload"
    # THE line: no image, and nothing invented in its place.
    assert payload["render"]["image_sha256"] is None
    assert payload["proposal"]["source"] == "excel_upload"
    assert payload["proposal"]["uploaded_by"] == "phase-uploader"
    assert payload["proposal"]["model"] is None
    # An upload confirms NOTHING.
    assert payload["attestation"] is None and payload["confirmed"] is False
    # And the cross-check says the machine compared nothing, rather than
    # flagging a text layer this medium never had.
    assert [finding["code"] for finding in payload["cross_check"]["findings"]] == [
        pd.CHECK_NO_TEXT_LAYER
    ]
    # The image route still refuses by name — there is no placeholder PNG.
    image = client.get(f"/api/genesis/phase-diagrams/{review_id}/image", headers=headers)
    assert image.status_code == 409
    assert image.json()["detail"]["code"] == "phase_diagram_no_image"


def test_two_intersections_whose_names_carry_a_slash_get_two_reviews(client, headers):
    """`review_id_for` hashes `Path(key).name`, so an unsanitised composite key
    would truncate 동서로/남북로 to its last segment — and two junctions would
    silently share one review, which is the one collision the key exists to
    prevent."""
    response = upload(
        client,
        headers,
        workbook(
            [
                ["", "동서로/남북로", "Φ1", "북", "직진", "", "", ""],
                ["", "가나로/남북로", "Φ1", "북", "직진", "", "", ""],
            ]
        ),
        "슬래시.xlsx",
    )
    assert response.status_code == 200, response.text
    created = response.json()["created"]
    assert len({row["review_id"] for row in created}) == 2
    assert sorted(row["intersection_name"] for row in created) == ["가나로/남북로", "동서로/남북로"]


@pytest.mark.parametrize(
    "left, right",
    [
        # THE REFUTER'S PAIR, verbatim: the delimiter itself, in the data.
        (("현시도.xlsx", "둔산네거리#부속"), ("현시도.xlsx#둔산네거리", "부속")),
        # ...and the same shape through the path separators, which the first
        # version folded to their full-width forms (also non-injective).
        (("a.xlsx", "가/나"), ("a.xlsx", "가／나")),
        (("a.xlsx", "가\\나"), ("a.xlsx", "가＼나")),
        # The escape character itself, which an escaping scheme must escape
        # FIRST or it re-escapes its own output.
        (("a.xlsx", "가%23나"), ("a.xlsx", "가#나")),
        # An empty part is a part: dropping it would merge these two.
        (("", "가"), ("가",)),
    ],
)
def test_composite_source_key_is_injective(left, right):
    """Direct unit coverage of the key itself — it had none, and the collision
    an adversarial pass proved over HTTP lived exactly here."""
    left_key = pd.composite_source_key(*left)
    right_key = pd.composite_source_key(*right)
    assert left_key != right_key, (left_key, right_key)
    assert pd.review_id_for(left_key) != pd.review_id_for(right_key)


def test_the_key_leaves_korean_alone_and_escapes_only_the_four():
    assert pd.composite_source_key("현시도.xlsx", "둔산네거리") == "현시도.xlsx#둔산네거리"
    assert pd.composite_source_key("a/b", "c#d") == "a%2Fb#c%23d"
    # ...and it survives review_id_for's Path(...).name, which truncates at a
    # separator: that is why '/' and '\\' are in the escape set at all.
    assert pd.review_id_for(pd.composite_source_key("a/b", "c")) == pd.review_id_for("a%2Fb#c")


def test_two_uploads_whose_names_span_the_delimiter_do_not_share_one_review(
    client, headers, store
):
    """End to end over HTTP, the way the refuter proved it: before the fix these
    two landed on ONE review id and the second silently overwrote the first's
    proposal (``replaced: true``)."""
    first = upload(
        client,
        headers,
        workbook([["", "둔산네거리#부속", "Φ1", "북", "직진", "", "", ""]]),
        "현시도.xlsx",
    ).json()
    second = upload(
        client,
        headers,
        workbook([["", "부속", "Φ1", "남", "직진", "", "", ""]]),
        "현시도.xlsx#둔산네거리",
    ).json()

    assert [row["replaced"] for row in first["created"]] == [False]
    assert [row["replaced"] for row in second["created"]] == [False], (
        "the second upload must not report replacing anything — that IS the overwrite"
    )
    ids = {first["created"][0]["review_id"], second["created"][0]["review_id"]}
    assert len(ids) == 2

    listed = client.get("/api/genesis/phase-diagrams", headers=headers).json()["reviews"]
    assert sorted(row["intersection_name"] for row in listed) == ["둔산네거리#부속", "부속"]
    # Both proposals are intact: the first review still carries ITS approach.
    payload = client.get(
        f"/api/genesis/phase-diagrams/{first['created'][0]['review_id']}", headers=headers
    ).json()
    assert payload["proposal"]["phases"][0]["movements"][0]["approach_label"] == "북"


def test_an_upload_reports_its_refused_rows_instead_of_dropping_them(client, headers):
    response = upload(
        client,
        headers,
        workbook(GOOD_ROWS + [["", "가교차로", "Φ1", "", "직진", "", "", ""]]),
        "혼합.xlsx",
    )
    assert response.status_code == 200
    body = response.json()
    assert [row["intersection_name"] for row in body["created"]] == ["둔산네거리", "탄방역네거리"]
    codes = [refusal["code"] for refusal in body["refusals"]]
    assert "phase_template_row_no_approach" in codes and pt.REFUSED_INTERSECTION in codes


def test_a_re_upload_replaces_an_unconfirmed_review_and_refuses_a_confirmed_one(
    client, headers, store
):
    first = upload(client, headers, workbook(GOOD_ROWS), "현시도.xlsx").json()
    review_id = first["created"][0]["review_id"]

    again = upload(client, headers, workbook(GOOD_ROWS), "현시도.xlsx").json()
    assert again["created"][0]["review_id"] == review_id
    assert again["created"][0]["replaced"] is True, "the same file lands in the same review"

    confirmed = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id),
    )
    assert confirmed.status_code == 200, confirmed.text

    third = upload(client, headers, workbook(GOOD_ROWS), "현시도.xlsx").json()
    assert [row["review_id"] for row in third["created"]] != [review_id]
    refusal = next(
        item for item in third["refusals"] if item["code"] == "phase_template_review_confirmed"
    )
    assert "phase-uploader" in refusal["reasons"][0]
    # And the confirmation is still there, untouched.
    payload = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers).json()
    assert payload["confirmed"] is True


def _declare_amber(client, headers, review_id: str, amber: float = 3.0):
    """The screen's own two-step (unit #58): declare the 황색시간 in the 신호
    시간 table, THEN confirm.

    ``put_attestation`` refuses a confirmation carrying an amber the store does
    not hold (``phase_diagram_attestation_stale``), because since #58 the amber
    IS the table's value and there is no second box to type it into. A review
    whose WORKBOOK already declared one needs no call — the stored value is
    already the workbook's — so this is only for the media that carry none of
    their own (대전 pdf, and a blank review).
    """
    response = client.patch(
        f"/api/genesis/phase-diagrams/{review_id}/timing",
        headers=headers,
        json={"field": "declared_amber_s", "value": amber},
    )
    assert response.status_code == 200, response.text
    return response


def _confirmation(store: pd.PhaseDiagramStore, review_id: str, **overrides: Any) -> dict[str, Any]:
    render = store.render(review_id)
    body: dict[str, Any] = {
        "node_id": 7,
        "approach_phases": {"1": "Φ1", "3": "Φ2"},
        "approach_links": {"1": "둔산로(북)"},
        "level_no": 1,
        "plan_id": 1,
        "image_sha256": render.image_sha256,
        "source_sha256": render.source_sha256,
        "proposal_sha256": store.proposal_sha256(review_id),
        "proposal_outcome": "proposed" if store.proposal(review_id) else "none",
        "declared_amber_s": 3.0,
        "amber_declaration_note": "확인자 선언",
    }
    body.update(overrides)
    return body


def test_an_excel_born_review_cannot_stage_signals_before_a_human_confirms(
    client, headers, store
):
    """The confirmation door is UNCHANGED by this unit. A new way in must not
    become a new way around."""
    created = upload(client, headers, workbook(GOOD_ROWS), "현시도.xlsx").json()["created"]
    review_id = created[0]["review_id"]

    response = client.post(
        "/api/genesis/phase-diagrams/signals-input",
        headers=headers,
        json={"review_ids": [review_id]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "phase_diagram_not_confirmed"
    # Same refusal at the store, which is where it is actually enforced.
    with pytest.raises(pd.PhaseDiagramRefused) as caught:
        store.confirmed_mapping(review_id)
    assert caught.value.code == "phase_diagram_not_confirmed"


def test_a_confirmed_excel_review_is_recorded_and_still_names_what_it_lacks(
    client, headers, store
):
    """A confirmed upload IS a recorded human claim. What it still cannot do is
    become a signals document, because a 현시 mapping carries no 주기/시간분할 —
    and that refusal names the missing PLAN, never the confirmation."""
    created = upload(client, headers, workbook(GOOD_ROWS), "현시도.xlsx").json()["created"]
    review_id = created[0]["review_id"]

    response = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["confirmed"] is True
    assert payload["attestation"]["attested_by"] == "phase-uploader"
    assert payload["attestation"]["image_sha256"] is None

    staged = client.post(
        "/api/genesis/phase-diagrams/signals-input",
        headers=headers,
        json={"review_ids": [review_id]},
    )
    assert staged.status_code == 409
    reasons = staged.json()["detail"]["reasons"]
    assert staged.json()["detail"]["code"] == "phase_diagram_no_sheet_text"
    # The refusal names the CELLS that would fix it, not just the absence.
    assert any(pt.TIMING_SHEET in reason and "녹색" in reason for reason in reasons)
    assert any("신호시간이 한 줄도 없습니다" in reason for reason in reasons)
    # The confirmation survived the refusal.
    assert store.attestation(review_id) is not None


def test_confirming_an_image_the_review_does_not_have_is_refused(client, headers, store):
    """The other half of "never a fake image": a client cannot smuggle a sha
    onto a review that has no drawing."""
    created = upload(client, headers, workbook(GOOD_ROWS), "현시도.xlsx").json()["created"]
    review_id = created[0]["review_id"]
    response = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id, image_sha256="c" * 64),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "phase_diagram_attestation_stale"
    assert store.attestation(review_id) is None


def test_a_no_image_render_may_not_carry_image_fields():
    """Enforced in the model, so no route can build the fake by accident."""
    with pytest.raises(ValueError):
        pd.DiagramRender(
            medium="excel_upload",
            source_file="x.xlsx",
            source_sha256="a" * 64,
            image_sha256="b" * 64,
            renderer=pd.NO_RENDERER,
            renderer_version=pd.NO_RENDERER,
            rendered_at="2026-08-24T00:00:00+00:00",
        )
    with pytest.raises(ValueError):
        # ...and the reverse: a band with no sha cannot be confirmed against
        # anything at all.
        pd.DiagramRender(
            medium="pdf_band",
            source_file="x.pdf",
            source_sha256="a" * 64,
            renderer="pypdfium2",
            renderer_version="test",
            rendered_at="2026-08-24T00:00:00+00:00",
        )


def test_an_uploaded_proposal_may_not_borrow_a_models_provenance():
    with pytest.raises(ValueError):
        pd.PhaseProposal(
            outcome="proposed",
            source="excel_upload",
            uploaded_by="tester",
            model="claude-sonnet",
            source_file="x.xlsx",
            proposed_at="2026-08-24T00:00:00+00:00",
            phases=[pd.ProposedPhase(phase_id="Φ1")],
        )
    with pytest.raises(ValueError):
        pd.PhaseProposal(
            outcome="proposed",
            source="excel_upload",
            uploaded_by="",
            source_file="x.xlsx",
            proposed_at="2026-08-24T00:00:00+00:00",
            phases=[pd.ProposedPhase(phase_id="Φ1")],
        )


# -- ③ the blank review -----------------------------------------------------


def test_a_blank_review_is_created_for_a_named_intersection(client, headers):
    response = client.post(
        BLANK_ROUTE,
        headers=headers,
        json={"intersection_name": "새터네거리", "origin_note": "네트워크 도식 노드 12"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["render"]["medium"] == "blank_entry"
    assert payload["render"]["image_sha256"] is None
    # The blank-form branch on the screen is keyed on exactly this.
    assert payload["proposal"] is None
    assert payload["sheet"]["intersection_name"] == "새터네거리"
    assert payload["sheet"]["plan_choices"] == []
    listed = client.get("/api/genesis/phase-diagrams", headers=headers).json()["reviews"]
    assert [row["intersection_name"] for row in listed] == ["새터네거리"]
    assert listed[0]["medium"] == "blank_entry"


@pytest.mark.parametrize("name", ["", "   "])
def test_a_blank_review_with_no_name_is_refused(client, headers, name):
    response = client.post(BLANK_ROUTE, headers=headers, json={"intersection_name": name})
    assert response.status_code in (400, 422), response.text
    if response.status_code == 400:
        assert response.json()["detail"]["code"] == "phase_diagram_blank_name_required"


def test_creating_the_same_blank_twice_refuses_instead_of_overwriting(client, headers):
    first = client.post(BLANK_ROUTE, headers=headers, json={"intersection_name": "새터네거리"})
    assert first.status_code == 201
    again = client.post(BLANK_ROUTE, headers=headers, json={"intersection_name": "새터네거리"})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "phase_diagram_review_exists"


# ---------------------------------------------------------------------------
# §5 The export — labelled, complete, and it comes back
# ---------------------------------------------------------------------------


def vision_proposal(index: int) -> pd.PhaseProposal:
    return pd.PhaseProposal(
        outcome="proposed",
        model="claude-test-vision",
        image_sha256=f"{index:064d}",
        source_file=f"sheet-{index}.pdf",
        proposed_at="2026-08-24T00:00:00+00:00",
        read_intersection_no=str(1000 + index),
        approaches=[pd.ProposedApproach(label=f"{index}로(북)", side="top")],
        phases=[
            pd.ProposedPhase(
                phase_id="Φ1",
                movements=[pd.ProposedMovement(approach_label=f"{index}로(북)", turn="through")],
                ambiguity=f"{index}번 시트: 좌회전 화살표가 겹쳐 판독하지 못했습니다",
            ),
            pd.ProposedPhase(
                phase_id="Φ2",
                movements=[pd.ProposedMovement(approach_label=f"{index}로(북)", turn="left")],
            ),
        ],
    )


def test_the_export_labels_every_sheet_and_carries_every_ambiguity_sentence():
    """The 23 vision proposals, as the drop actually is."""
    entries = [
        pt.ExportEntry(
            intersection_name=f"{index}번교차로",
            intersection_no=str(1000 + index),
            proposal=vision_proposal(index),
        )
        for index in range(23)
    ]
    content = pt.build_export(entries)
    cells = sheet_cells(content)
    assert set(cells) == {pt.GUIDE_SHEET, pt.ROWS_SHEET, pt.TIMING_SHEET}
    for title, values in cells.items():
        assert any(pt.UNCONFIRMED_MARK in value for value in values), title
    joined = " ".join(cells[pt.ROWS_SHEET])
    for index in range(23):
        assert f"{index}번교차로" in joined
        assert f"{index}번 시트: 좌회전 화살표가 겹쳐 판독하지 못했습니다" in joined
    # The 상태 column marks every DATA row, not just the header.
    rows_with_mark = [value for value in cells[pt.ROWS_SHEET] if value == pt.UNCONFIRMED_MARK]
    assert len(rows_with_mark) >= 23 * 2


def test_a_refused_proposal_is_exported_as_a_row_that_says_so():
    refused = pd.PhaseProposal(
        outcome="refused",
        refusal=pd.ProposalRefusal(code="phase_diagram_illegible", reason="스캔이 흐립니다"),
        model="claude-test-vision",
        image_sha256="d" * 64,
        source_file="sheet.pdf",
        proposed_at="2026-08-24T00:00:00+00:00",
    )
    joined = " ".join(
        sheet_cells(
            pt.build_export([pt.ExportEntry(intersection_name="가교차로", proposal=refused)])
        )[pt.ROWS_SHEET]
    )
    assert "가교차로" in joined
    assert "phase_diagram_illegible" in joined and "스캔이 흐립니다" in joined


def test_an_exported_draft_parses_when_it_is_uploaded_again():
    """The round trip is the point: a reviewer corrects the draft in Excel and
    sends it back through the same door."""
    entries = [
        pt.ExportEntry(
            intersection_name=f"{index}번교차로",
            intersection_no=str(1000 + index),
            proposal=vision_proposal(index),
        )
        for index in range(23)
    ]
    parsed = pt.parse_template(pt.build_export(entries), source_file="draft.xlsx")
    assert parsed.refusals == []
    assert len(parsed.intersections) == 23
    first = next(entry for entry in parsed.intersections if entry.name == "0번교차로")
    proposal = first.to_proposal(source_file="draft.xlsx", uploaded_by="tester")
    assert proposal.phase_ids == ["Φ1", "Φ2"]
    assert [movement.turn for phase in proposal.phases for movement in phase.movements] == [
        "through",
        "left",
    ], "the ko movement words the export writes are the ones the parser reads"
    assert "판독하지 못했습니다" in (proposal.phases[0].movements[0].note or "")


def test_an_exported_draft_with_notes_round_trips_them_without_poisoning_real_intersections():
    """The gap docs/phase2/DECISIONS.md's 2026-08-24 '인터페이스 갭' entry
    measured: a real review's export carries THREE kinds of empty-현시번호
    note row (``ExportEntry.notes``, ``proposal.notes``, and the
    refused/missing-proposal reason row), and a round trip through the same
    upload door must not turn any of them into an intersection-level poison.
    """
    entry_level_note_proposal = vision_proposal(0)
    proposal_level_note = pd.PhaseProposal(
        outcome="proposed",
        model="claude-test-vision",
        image_sha256="e" * 64,
        source_file="sheet-2.pdf",
        proposed_at="2026-08-24T00:00:00+00:00",
        read_intersection_no="2000",
        approaches=[pd.ProposedApproach(label="2로(북)", side="top")],
        phases=[
            pd.ProposedPhase(
                phase_id="Φ1",
                movements=[pd.ProposedMovement(approach_label="2로(북)", turn="through")],
            ),
        ],
        notes=["모델이 확인하지 못한 부분: 보행신호 존재 여부"],
    )
    entries = [
        pt.ExportEntry(
            intersection_name="0번교차로",
            intersection_no="1000",
            proposal=entry_level_note_proposal,
            notes=["대전 시트 4-1: 신호시간은 별도 표에 있습니다"],
        ),
        pt.ExportEntry(
            intersection_name="2번교차로", intersection_no="2000", proposal=proposal_level_note
        ),
        # No proposal at all -> build_export's own :682 reason row, with
        # nothing else naming a phase for this intersection.
        pt.ExportEntry(intersection_name="9번교차로", intersection_no="9000", proposal=None),
    ]
    parsed = pt.parse_template(pt.build_export(entries), source_file="draft.xlsx")

    imported = {item.name: item for item in parsed.intersections}
    assert set(imported) == {"0번교차로", "2번교차로"}, "9번교차로 had no phase row and must not phantom-import"

    entry_level = imported["0번교차로"]
    assert entry_level.notes == [
        "대전 시트 4-1: 신호시간은 별도 표에 있습니다"
    ], "ExportEntry.notes (:625) survives the round trip, 메모: prefix stripped"
    proposal_one = entry_level.to_proposal(source_file="draft.xlsx", uploaded_by="tester")
    assert proposal_one.phase_ids == ["Φ1", "Φ2"], "movement-set equality even with a note row mixed in"
    assert proposal_one.notes == ["대전 시트 4-1: 신호시간은 별도 표에 있습니다"]

    proposal_level = imported["2번교차로"]
    assert proposal_level.notes == [
        "모델이 확인하지 못한 부분: 보행신호 존재 여부"
    ], "proposal.notes (:701) survives the round trip too"
    proposal_two = proposal_level.to_proposal(source_file="draft.xlsx", uploaded_by="tester")
    assert proposal_two.phase_ids == ["Φ1"]
    assert [
        movement.turn for phase in proposal_two.phases for movement in phase.movements
    ] == ["through"]

    # Zero refusals for either surviving intersection — the notes did not cost
    # them anything.
    assert [
        item for item in parsed.refusals if item.intersection in ("0번교차로", "2번교차로")
    ] == []

    # The notes-only intersection (:682's reason row, and nothing else) is
    # refused by name rather than silently dropped or imported empty.
    refusal = next(item for item in parsed.refusals if item.intersection == "9번교차로")
    assert refusal.code == "phase_template_intersection_empty"


def test_the_export_routes_serve_one_review_and_all_of_them(client, headers, store):
    created = upload(client, headers, workbook(GOOD_ROWS), "현시도.xlsx").json()["created"]
    review_id = created[0]["review_id"]

    single = client.get(
        f"/api/genesis/phase-diagrams/{review_id}/export.xlsx", headers=headers
    )
    assert single.status_code == 200
    assert single.headers["content-type"].startswith(pt.XLSX_MEDIA_TYPE)
    joined = " ".join(sheet_cells(single.content)[pt.ROWS_SHEET])
    assert "둔산네거리" in joined and pt.UNCONFIRMED_MARK in joined
    assert "탄방역네거리" not in joined

    everything = client.get(EXPORTS_ROUTE, headers=headers)
    assert everything.status_code == 200
    joined_all = " ".join(sheet_cells(everything.content)[pt.ROWS_SHEET])
    assert "둔산네거리" in joined_all and "탄방역네거리" in joined_all


def test_exporting_with_nothing_to_export_refuses_by_name(client, headers):
    response = client.get(EXPORTS_ROUTE, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "phase_diagram_nothing_to_export"


# ---------------------------------------------------------------------------
# §6 신호시간 — the timing that makes an uploaded 현시도 reach a generation
# ---------------------------------------------------------------------------
#
# Optional as a whole, COMPLETE when present, and converted by the 대전 sheet
# path's own arithmetic (services/genesis/signals_declared.py). The tests below
# are about the three claims that follow from that: an incomplete set never
# becomes a plan, a typed plan never travels as a sheet, and a 대전 review
# exported and re-uploaded rebuilds the SAME program.

TIMED_ROWS: list[list[Any]] = [
    ["1234", "둔산네거리", "Φ1", "둔산로(북)", "직진", 3.0, "설계지침 4.2", ""],
    ["1234", "둔산네거리", "Φ2", "둔산로(동)", "직진", "", "", ""],
]
#: 교차로명 / 수준 / 계획ID / 현시 / 녹색 / 소거 / 최소녹색 / 최대녹색 / 주기 / 연동 / 시간대
TIMING_ROWS: list[list[Any]] = [
    ["둔산네거리", 1, 1, "Φ1", 30.0, 4.0, 25.0, 60.0, 68.0, 5.0, "07:00~09:00"],
    ["둔산네거리", 1, 1, "Φ2", 30.0, 4.0, "", "", "", "", ""],
]


def test_timing_rows_are_optional_and_absence_refuses_nothing():
    parsed = pt.parse_template(workbook(TIMED_ROWS), source_file="no-timing.xlsx")
    assert parsed.refusals == []
    assert parsed.intersections[0].plans == {}, "no 신호시간 sheet is a first-class state"


def test_a_complete_timing_set_is_read_per_plan():
    parsed = pt.parse_template(
        workbook(
            TIMED_ROWS,
            timing=TIMING_ROWS
            + [
                ["둔산네거리", 2, 5, "Φ1", 20.0, 4.0, "", "", "", "", ""],
                ["둔산네거리", 2, 5, "Φ2", 20.0, 4.0, "", "", "", "", ""],
            ],
        ),
        source_file="timed.xlsx",
    )
    assert parsed.refusals == []
    entry = parsed.intersections[0]
    assert sorted(entry.plans) == [(1, 1), (2, 5)]
    peak = entry.plans[(1, 1)]
    assert (peak.cycle_s, peak.offset_s, peak.time_of_day) == (68.0, 5.0, "07:00~09:00")
    assert peak.ordered() == [
        ("Φ1", pt.ParsedPhaseTiming(green_s=30.0, clearance_s=4.0, min_green_s=25.0, max_green_s=60.0)),
        ("Φ2", pt.ParsedPhaseTiming(green_s=30.0, clearance_s=4.0)),
    ]
    payload = entry.timing_payload()
    assert payload[0]["phases"][0] == {
        "phase_id": "Φ1",
        "green_s": 30.0,
        "clearance_s": 4.0,
        "min_green_s": 25.0,
        "max_green_s": 60.0,
    }
    # An EMPTY actuated band stays None, never 0: "the source stated none" and
    # "the minimum green is zero seconds" are different facts.
    assert payload[0]["phases"][1]["min_green_s"] is None
    assert (payload[0]["offset_s"], payload[0]["time_of_day"]) == (5.0, "07:00~09:00")
    # The amber is NOT duplicated per timing row: measured against
    # signals_daejeon.to_fixed_time_plan, which writes ONE declared amber into
    # every phase while the clearance is per phase.
    assert pt.COL_AMBER not in pt.TIMING_COLUMNS


@pytest.mark.parametrize(
    "rows, code",
    [
        (
            [["둔산네거리", 1, 1, "Φ1", 30.0, 4.0, ""]],
            "phase_template_timing_phase_without_green",
        ),
        (
            TIMING_ROWS + [["둔산네거리", 1, 1, "Φ7", 10.0, 4.0, ""]],
            "phase_template_timing_green_without_phase",
        ),
        (
            [["둔산네거리", 1, 1, "Φ1", "삼십", 4.0, ""], ["둔산네거리", 1, 1, "Φ2", 30.0, 4.0, ""]],
            "phase_template_timing_green_invalid",
        ),
        (
            [["둔산네거리", 1, 1, "Φ1", 30.0, "", ""], ["둔산네거리", 1, 1, "Φ2", 30.0, 4.0, ""]],
            "phase_template_timing_clearance_invalid",
        ),
        (
            TIMING_ROWS + [["둔산네거리", 1, 1, "Φ2", 25.0, 4.0, ""]],
            "phase_template_timing_duplicate",
        ),
        (
            [["둔산네거리", "일", 1, "Φ1", 30.0, 4.0, ""], ["둔산네거리", 1, 1, "Φ2", 30.0, 4.0, ""]],
            "phase_template_timing_bad_plan_key",
        ),
    ],
)
def test_an_incomplete_or_contradictory_timing_set_refuses_by_name(rows, code):
    parsed = pt.parse_template(workbook(TIMED_ROWS, timing=rows), source_file="broken.xlsx")
    codes = [refusal.code for refusal in parsed.refusals]
    assert code in codes, codes
    # And the intersection is not half-imported: a plan missing one phase is an
    # intersection with one movement quietly dark.
    assert [entry.name for entry in parsed.intersections] == []
    assert pt.REFUSED_INTERSECTION in codes


def test_timing_for_an_intersection_the_mapping_never_named_is_refused_alone():
    """Named, not dropped — and it does not take a DIFFERENT, complete
    intersection down with it: the refusal belongs to the row's own name."""
    parsed = pt.parse_template(
        workbook(TIMED_ROWS, timing=TIMING_ROWS + [["없는네거리", 1, 1, "Φ1", 30.0, 4.0, ""]]),
        source_file="stray.xlsx",
    )
    stray = next(
        item
        for item in parsed.refusals
        if item.code == "phase_template_timing_unknown_intersection"
    )
    assert stray.intersection == "없는네거리" and stray.row == 4
    assert [entry.name for entry in parsed.intersections] == ["둔산네거리"]
    assert [phase_id for phase_id, _timing in parsed.intersections[0].plans[(1, 1)].ordered()] == [
        "Φ1",
        "Φ2",
    ]


def test_a_stated_cycle_that_contradicts_the_phases_refuses_at_build_time():
    """Measured placement: the sum is only knowable once every phase is read, so
    this refusal belongs to the converter, beside the sheet path's own."""
    parsed = pt.parse_template(
        workbook(
            TIMED_ROWS,
            timing=[
                ["둔산네거리", 1, 1, "Φ1", 30.0, 4.0, "", "", 200.0, "", ""],
                ["둔산네거리", 1, 1, "Φ2", 30.0, 4.0, "", "", "", "", ""],
            ],
        ),
        source_file="cycle.xlsx",
    )
    assert parsed.refusals == []
    plan, issues = sd.to_fixed_time_plan(
        _declared(parsed.intersections[0], level_no=1, plan_id=1, amber=3.0)
    )
    assert plan is None
    assert [issue.code for issue in issues] == ["declared_cycle_contradicts_splits"]


def _declared(
    entry: pt.ParsedIntersection, *, level_no: int, plan_id: int, amber: float | None
) -> sd.DeclaredPlan:
    plan = entry.plans[(level_no, plan_id)]
    return sd.DeclaredPlan(
        source_file="test.xlsx",
        intersection_name=entry.name,
        intersection_no=entry.intersection_no,
        level_no=level_no,
        plan_id=plan_id,
        cycle_s=plan.cycle_s,
        offset_s=plan.offset_s,
        time_of_day=plan.time_of_day,
        declared_amber_s=amber,
        phases=tuple(
            sd.DeclaredPhaseTiming(
                phase_id=phase_id,
                green_s=timing.green_s,
                intergreen_s=timing.clearance_s,
                min_green_s=timing.min_green_s,
                max_green_s=timing.max_green_s,
            )
            for phase_id, timing in plan.ordered()
        ),
    )


def test_a_typed_plan_without_a_declared_amber_produces_no_plan():
    """This module's daejeon_intergreen_split_unstated: the 소거 column is
    황색+전적색 combined, so the amber inside it is still a declaration."""
    parsed = pt.parse_template(workbook(TIMED_ROWS, timing=TIMING_ROWS), source_file="t.xlsx")
    plan, issues = sd.to_fixed_time_plan(
        _declared(parsed.intersections[0], level_no=1, plan_id=1, amber=None)
    )
    assert plan is None
    assert [issue.code for issue in issues] == ["declared_amber_unstated"]


def test_an_amber_longer_than_the_typed_clearance_refuses_like_the_sheet_path_does():
    parsed = pt.parse_template(workbook(TIMED_ROWS, timing=TIMING_ROWS), source_file="t.xlsx")
    plan, issues = sd.to_fixed_time_plan(
        _declared(parsed.intersections[0], level_no=1, plan_id=1, amber=5.0)
    )
    assert plan is None
    assert [issue.code for issue in issues] == ["declared_all_red_negative"]

    # ...and exactly zero all-red is ACCEPTED, the same as on the sheet path.
    ok_plan, _issues = sd.to_fixed_time_plan(
        _declared(parsed.intersections[0], level_no=1, plan_id=1, amber=4.0)
    )
    assert ok_plan is not None and ok_plan.ok
    assert [phase.all_red_s for phase in ok_plan.program.phases] == [0.0, 0.0]


def test_a_typed_plan_is_its_own_source_and_never_wears_the_sheets(client, headers, store):
    """`side_inputs` discriminates on plan.source and the daejeon_sheet branch
    PARSES sheet_text — so a typed plan labelled daejeon_sheet would mean
    fabricating a sheet. It gets its own source instead."""
    created = upload(
        client, headers, workbook(TIMED_ROWS, timing=TIMING_ROWS), "시간.xlsx"
    ).json()["created"]
    review_id = created[0]["review_id"]
    assert created[0]["plans"] == [{"level_no": 1, "plan_id": 1}]

    response = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id),
    )
    assert response.status_code == 200, response.text

    assignment = store.confirmed_mapping(review_id).to_assignment()
    assert assignment["plan"]["source"] == "excel_upload"
    assert "sheet_text" not in assignment["plan"], "no fabricated sheet, ever"
    # The amber is the CONFIRMED one, not whatever was in the workbook.
    assert assignment["plan"]["declared_amber_s"] == 3.0
    document = pd.build_signals_document([store.confirmed_mapping(review_id)])
    parsed_doc = parse_side_input("signals", json.dumps(document).encode("utf-8"))
    build = build_signal_assignments(parsed_doc)
    assert build.ok, [issue.code for issue in build.refusals]
    plan, _mapping = build.assignments[7]
    assert plan.program.source_kind.value == "declared"
    assert plan.program.cycle_time_s == 68.0
    assert [phase.green_s for phase in plan.program.phases] == [30.0, 30.0]


def test_the_confirm_form_offers_exactly_the_plans_that_have_timing(client, headers):
    created = upload(
        client, headers, workbook(TIMED_ROWS, timing=TIMING_ROWS), "시간.xlsx"
    ).json()["created"]
    payload = client.get(
        f"/api/genesis/phase-diagrams/{created[0]['review_id']}", headers=headers
    ).json()
    assert payload["sheet"]["plan_choices"] == [{"level_no": 1, "plan_id": 1}]


def test_confirming_a_plan_this_review_does_not_carry_refuses_at_signing_time(
    client, headers, store
):
    """UNIT #58 MOVED THIS REFUSAL EARLIER, and the move is the point.

    Before: a confirmation naming a (수준, 계획) with no timing behind it was
    ACCEPTED, the review read 확인됨, and the refusal arrived at the staging
    button — long after the person who picked the plan had left the screen.
    Now ``put_attestation`` asks the same question at signing time and names
    the plans this intersection actually has, so the answer reaches the person
    who can act on it while they are still looking at it.
    """
    created = upload(
        client, headers, workbook(TIMED_ROWS, timing=TIMING_ROWS), "시간.xlsx"
    ).json()["created"]
    review_id = created[0]["review_id"]
    response = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id, level_no=9, plan_id=9),
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "phase_diagram_attestation_unknown_plan"
    assert any("수준 9 / 계획 9" in reason for reason in detail["reasons"])
    # ...and it names the plans that DO have timing, so the reader can pick one.
    assert any("수준 1 / 계획 1" in reason for reason in detail["reasons"])
    # NOTHING was recorded: a refused confirmation leaves the review unconfirmed
    # rather than 확인됨 against a plan that does not exist.
    assert store.attestation(review_id) is None
    payload = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers).json()
    assert payload["confirmed"] is False


def test_a_review_with_no_timing_at_all_still_refuses_at_the_staging_button(
    client, headers, store
):
    """The OTHER half, deliberately left where it was. A review that carries no
    timing at all has nothing to check a (수준, 계획) against — its two numbers
    are typed by hand precisely because nothing measured them — so signing
    succeeds and the staging build is still the honest place to say the timing
    is missing, by name."""
    created = upload(client, headers, workbook(TIMED_ROWS), "무시간.xlsx").json()["created"]
    review_id = created[0]["review_id"]
    confirmed = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id, level_no=9, plan_id=9),
    )
    assert confirmed.status_code == 200, confirmed.text
    response = client.post(
        "/api/genesis/phase-diagrams/signals-input",
        headers=headers,
        json={"review_ids": [review_id]},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "phase_diagram_no_sheet_text"
    assert any("수준 9 / 계획 9" in reason for reason in detail["reasons"])
    assert any("신호시간이 한 줄도 없습니다" in reason for reason in detail["reasons"])


# -- the round trip, all the way to a signals document ----------------------

DAEJEON_FIXTURES = Path(__file__).parent / "fixtures" / "daejeon_signal"


def daejeon_review(store: pd.PhaseDiagramStore) -> tuple[str, str]:
    """One pdf_band review carrying a real 대전 sheet's text and a 4-phase
    proposal. The PNG is a stub — nothing in this test looks at the picture, and
    the render records the sha of the bytes it was actually given."""
    text = (DAEJEON_FIXTURES / "actuated_variant.txt").read_text(encoding="utf-8")
    png = b"\x89PNG\r\n\x1a\n" + b"fixture"
    render = pd.DiagramRender(
        source_file="가상감응네거리.pdf",
        source_sha256=pd.sha256_hex(text.encode("utf-8")),
        page_no=1,
        page_width_pt=595.0,
        page_height_pt=842.0,
        crop_pt=(0.0, 0.0, 500.0, 200.0),
        render_scale=2.0,
        image_sha256=pd.sha256_hex(png),
        image_width_px=1000,
        image_height_px=400,
        renderer="pypdfium2",
        renderer_version="test",
        rendered_at="2026-08-24T00:00:00+00:00",
    )
    review_id = store.put_render(png, render, sheet_text=text)
    proposal = pd.PhaseProposal(
        outcome="proposed",
        model="claude-test-vision",
        image_sha256=render.image_sha256,
        source_file=render.source_file,
        proposed_at="2026-08-24T00:00:00+00:00",
        read_intersection_no="9005",
        approaches=[pd.ProposedApproach(label=f"{leg}로", side="unknown") for leg in "북동남서"],
        phases=[
            pd.ProposedPhase(
                phase_id=f"Φ{index}",
                movements=[
                    pd.ProposedMovement(approach_label=f"{leg}로", turn="through")
                ],
            )
            for index, leg in enumerate("북동남서", start=1)
        ],
    )
    store.put_proposal(review_id, proposal, pd.cross_check(proposal, sheet=None, render=render))
    return review_id, text


def reference_plan(text: str):
    sheet = parse_sheet_text(text, source_file="가상감응네거리.pdf")
    plan, _issues = to_fixed_time_plan(
        sheet, level_no=1, plan_id=1, settings=DaejeonSettings(declared_amber_s=3.0)
    )
    assert plan is not None and plan.ok
    return plan


def test_a_daejeon_export_carries_its_timing(client, headers, store):
    review_id, text = daejeon_review(store)
    response = client.get(
        f"/api/genesis/phase-diagrams/{review_id}/export.xlsx", headers=headers
    )
    assert response.status_code == 200
    cells = sheet_cells(response.content)
    timing = " ".join(cells[pt.TIMING_SHEET])
    assert "가상감응네거리" in timing
    reference = reference_plan(text)
    for phase in reference.program.phases:
        assert phase.phase_id in timing
        assert f"{phase.green_s:g}" in timing
        assert f"{phase.intergreen_s:g}" in timing
    assert f"{reference.program.cycle_time_s:g}" in timing


def test_the_daejeon_round_trip_rebuilds_the_same_plan(client, headers, store):
    """Export → correct in Excel → upload → confirm → signals document. The
    values that come out the far end are the ones the sheet path produces."""
    review_id, text = daejeon_review(store)
    _declare_amber(client, headers, review_id)
    exported = client.get(
        f"/api/genesis/phase-diagrams/{review_id}/export.xlsx", headers=headers
    )
    assert exported.status_code == 200

    uploaded = client.post(
        f"{UPLOAD_ROUTE}?filename=재업로드.xlsx", headers=headers, content=exported.content
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["refusals"] == [], body["refusals"]
    row = next(item for item in body["created"] if item["intersection_name"] == "가상감응네거리")
    assert {"level_no": 1, "plan_id": 1} in row["plans"]
    excel_review = row["review_id"]

    confirmed = client.post(
        f"/api/genesis/phase-diagrams/{excel_review}/confirmation",
        headers=headers,
        json=_confirmation(
            store,
            excel_review,
            approach_phases={"1": "Φ1", "2": "Φ2", "3": "Φ3", "4": "Φ4"},
            level_no=1,
            plan_id=1,
            declared_amber_s=3.0,
        ),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"] is True

    staged = client.post(
        "/api/genesis/phase-diagrams/signals-input",
        headers=headers,
        json={"review_ids": [excel_review]},
    )
    assert staged.status_code == 200, staged.text
    assert staged.json()["summary"]["assignments"][0]["plan_source"] == "excel_upload"

    document = pd.build_signals_document([store.confirmed_mapping(excel_review)])
    build = build_signal_assignments(
        parse_side_input("signals", json.dumps(document).encode("utf-8"))
    )
    assert build.ok, [issue.code for issue in build.refusals]
    rebuilt, _mapping = build.assignments[7]
    reference = reference_plan(text)

    # THE COMPLETE SET, field by field. The first version of this test compared
    # cycle + four phase numbers, and an adversarial pass walked straight
    # through the hole: the 연동 offset had become None, the actuated 최소/최대
    # 녹색 had become None, and the plan's 시간대 had gone — all silently, in a
    # program Vissim would have run without a murmur. Everything below is
    # compared BY NAME so a field added to either path shows up here as a
    # failure rather than as a silence.
    assert _plan_fields(rebuilt) == _plan_fields(reference)
    assert _program_fields(rebuilt.program) == _program_fields(reference.program)
    assert [_phase_fields(phase) for phase in rebuilt.program.phases] == [
        _phase_fields(phase) for phase in reference.program.phases
    ]
    assert [_row_fields(row) for row in rebuilt.rows] == [
        _row_fields(row) for row in reference.rows
    ]
    # The offset in particular, spelled out: the fixture states 10 s and it is
    # CARRIED (and, like the sheet path, not applied to the phase starts).
    assert rebuilt.program.offset_s == 10.0
    assert [row.start_offset_s for row in rebuilt.rows] == [0.0, 30.0, 60.0, 100.0]

    # The three fields that CANNOT be equal, named and asserted rather than
    # skipped -- each is a statement about the SOURCE, and the two sources are
    # genuinely different documents:
    #   1. provenance: where the numbers were read from,
    #   2. the issue codes: each converter names its own findings,
    #   3. nothing else.
    assert reference.program.provenance.locator.startswith("PLAN TABLE")
    assert "신호시간" in (rebuilt.program.provenance.locator or "")
    assert "신호시간" in (rebuilt.program.provenance.derived_by or "")
    assert rebuilt.program.source_kind is reference.program.source_kind
    assert reference.issue_codes() == ["daejeon_coordination_offset_not_applied"]
    assert rebuilt.issue_codes() == ["declared_coordination_offset_not_applied"], (
        "the offset is carried AND the reviewer is told it is not applied, exactly as the "
        "sheet path does it"
    )


# -- the field audit, as code -----------------------------------------------
#
# One helper per record the two paths emit, listing EVERY field of that record
# except the ones named above. Written as explicit dicts rather than
# `model_dump()` so that a new field on either model fails this file loudly
# (a KeyError-free dump would simply grow and keep passing).


def _plan_fields(plan) -> dict[str, Any]:
    return {
        "controller_id": plan.controller_id,
        "source_program_id": plan.source_program_id,
        "complete_cycles": plan.complete_cycles,
        "stretch_phase_id": plan.stretch_phase_id,
        "average_cycle_s": plan.average_cycle_s,
        "plan_cycle_s": plan.plan_cycle_s,
        "round_up_residual_s": plan.round_up_residual_s,
        "ok": plan.ok,
    }


def _program_fields(program) -> dict[str, Any]:
    return {
        "program_id": program.program_id,
        "name": program.name,
        "source_kind": program.source_kind,
        "cycle_time_s": program.cycle_time_s,
        "offset_s": program.offset_s,
        "time_of_day": program.time_of_day,
        "observed_cycle_count": program.observed_cycle_count,
        "observation_window": program.observation_window,
        "coordination": program.coordination,
        "signal_group_windows": list(program.signal_group_windows),
        "absent": dict(program.absent),
        "phase_count": len(program.phases),
    }


def _phase_fields(phase) -> dict[str, Any]:
    return {
        "phase_id": phase.phase_id,
        "name": phase.name,
        "sequence_index": phase.sequence_index,
        "green_s": phase.green_s,
        "amber_s": phase.amber_s,
        "all_red_s": phase.all_red_s,
        "intergreen_s": phase.intergreen_s,
        "min_green_s": phase.min_green_s,
        "max_green_s": phase.max_green_s,
        "is_stretch_phase": phase.is_stretch_phase,
        "frequency": phase.frequency,
        "observation_count": phase.observation_count,
        "observed_total_duration_s": phase.observed_total_duration_s,
        "absent": dict(phase.absent),
    }


def _row_fields(row) -> dict[str, Any]:
    return {
        "phase_id": row.phase_id,
        "occurrences": row.occurrences,
        "observed_total_s": row.observed_total_s,
        "scats_average_s": row.scats_average_s,
        "frequency": row.frequency,
        "average_phase_s": row.average_phase_s,
        "rounded_phase_s": row.rounded_phase_s,
        "start_offset_s": row.start_offset_s,
        "intergreen_s": row.intergreen_s,
        "green_s": row.green_s,
        "is_stretch_phase": row.is_stretch_phase,
        "dropped": row.dropped,
        "drop_reason": row.drop_reason,
    }


def test_a_blank_review_still_confirms_and_still_names_what_it_lacks(client, headers, store):
    """⑥'s evidence is unaffected by the timing pass: a review with no timing
    confirms exactly as before and refuses the signals build by name."""
    created = client.post(
        BLANK_ROUTE, headers=headers, json={"intersection_name": "새터네거리"}
    ).json()
    review_id = created["review_id"]
    _declare_amber(client, headers, review_id)
    confirmed = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id, approach_phases={"1": "Φ1"}),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"] is True

    staged = client.post(
        "/api/genesis/phase-diagrams/signals-input",
        headers=headers,
        json={"review_ids": [review_id]},
    )
    assert staged.status_code == 409
    assert staged.json()["detail"]["code"] == "phase_diagram_no_sheet_text"
    assert any(
        pt.TIMING_SHEET in reason for reason in staged.json()["detail"]["reasons"]
    )
    assert store.attestation(review_id) is not None


def test_a_review_that_LOSES_its_image_makes_its_confirmation_stale(client, headers, store):
    """The other direction of the staleness comparison, which #52 made reachable
    and left untested: the attestation names an image, and the review no longer
    has one. Forged at the filesystem level, because that is the only way this
    state occurs — a hand-edited store, a partial restore, a future writer.
    Without the check, a confirmation made against a drawing would be carried
    forward onto a review that has no drawing at all."""
    review_id, _text = daejeon_review(store)
    _declare_amber(client, headers, review_id)
    confirmed = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id, approach_phases={"1": "Φ1"}),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert store.stale_reasons(review_id) == []
    attested_image = store.attestation(review_id).image_sha256
    assert attested_image is not None

    # Swap the stored render for a no-image one, leaving the attestation alone.
    directory = store.review_dir(review_id)
    replacement = pd.manual_render(
        medium="excel_upload",
        source_file=store.render(review_id).source_file,
        source_sha256=store.render(review_id).source_sha256,
    )
    (directory / pd.RENDER_FILENAME).write_text(
        json.dumps(replacement.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    reasons = store.stale_reasons(review_id)
    assert reasons and any("지금은" in reason and "이미지가 없습니다" in reason for reason in reasons)
    assert attested_image[:12] in reasons[0], "the reason names the image that WAS confirmed"
    with pytest.raises(pd.PhaseDiagramRefused) as caught:
        store.confirmed_mapping(review_id)
    assert caught.value.code == "phase_diagram_attestation_stale"
    # ...and the browser sees it as 재확인 필요, not as a confirmation.
    payload = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers).json()
    assert payload["confirmed"] is False and payload["stale_reasons"]
    assert payload["attestation"] is not None, "stale is not the same as unconfirmed"


def test_a_confirmed_mapping_may_not_carry_two_plan_sources():
    attestation = pd.MappingAttestation(
        attested_by="tester",
        attested_at="2026-08-24T00:00:00+00:00",
        source_file="x.xlsx",
        source_sha256="a" * 64,
        node_id=1,
        approach_phases={1: "Φ1"},
        level_no=1,
        plan_id=1,
    )
    with pytest.raises(pd.PhaseDiagramRefused) as caught:
        pd.ConfirmedMapping(
            attestation=attestation,
            sheet_text="a sheet",
            declared_plan={"phases": [{"phase_id": "Φ1", "green_s": 10.0, "clearance_s": 4.0}]},
        )
    assert caught.value.code == "phase_diagram_two_plan_sources"


# ---------------------------------------------------------------------------
# §6 Unit #58 — the card's 신호 시간 table, and amending your own record
# ---------------------------------------------------------------------------
#
# THE USER'S DECISION (docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도
# 카드 단순화 — 도식+시간표, 짧은 확인 (#58)"): the Excel already carries the signal
# timing, so the card renders it as a table — and the numbers are editable,
# because "editing a timing value on screen is the uploader amending their own
# statement". The tests below are the refusals and the stamps that claim rests
# on:
#
#   * the editor is the SESSION, never the body (extra="forbid" on the stamp);
#   * the previous value is KEPT — an amendment is a ledger entry, not an
#     overwrite;
#   * a 대전 review's timing is the CLIENT's document and is refused by name;
#   * an amendment never CREATES a plan or a 현시 that was never typed;
#   * the amended 황색시간 IS the declaration the attestation records;
#   * an amended green reaches the signals document, because the card and the
#     build read the same stored rows.

TIMING_ROUTE = "/api/genesis/phase-diagrams/{}/timing"


def timed_review(client, headers) -> str:
    """One uploaded review with a complete (수준 1, 계획 1) timing set."""
    created = upload(
        client, headers, workbook(TIMED_ROWS, timing=TIMING_ROWS), "시간.xlsx"
    ).json()["created"]
    return created[0]["review_id"]


def test_the_timing_route_refuses_an_anonymous_caller(client, headers):
    review_id = timed_review(client, headers)
    response = client.patch(
        TIMING_ROUTE.format(review_id),
        json={"field": "declared_amber_s", "value": 3.0},
    )
    assert response.status_code == 401


def test_the_timing_editor_is_the_session_never_the_request_body(client, headers):
    """The one thing an amendment must not permit is a caller naming somebody
    else as the editor — so, like the confirmation, there is no field for it."""
    review_id = timed_review(client, headers)

    rejected = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={
            "field": "green_s",
            "level_no": 1,
            "plan_id": 1,
            "phase_id": "Φ1",
            "value": 31.0,
            "edited_by": "somebody-else",
        },
    )
    assert rejected.status_code == 422, "extra='forbid' — the field does not exist"

    response = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "green_s", "level_no": 1, "plan_id": 1, "phase_id": "Φ1", "value": 31.0},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    # The whole review comes back, with the STORED value in it.
    plan = payload["sheet"]["timing"][0]
    assert plan["phases"][0] == {
        "phase_id": "Φ1",
        "green_s": 31.0,
        "clearance_s": 4.0,
        "min_green_s": 25.0,
        "max_green_s": 60.0,
    }

    store = pd.PhaseDiagramStore(genesis_jobs.genesis_dir() / "phase-diagrams")
    ledger = store.amendments(review_id)
    assert [event.by for event in ledger.history] == ["phase-uploader"]
    assert ledger.history[0].at, "the server's clock, not the client's"
    assert (ledger.history[0].was, ledger.history[0].now) == ("30.0", "31.0")


def test_an_amendment_keeps_the_previous_value_instead_of_overwriting_it(client, headers):
    review_id = timed_review(client, headers)
    for value in (31.0, 32.0):
        assert (
            client.patch(
                TIMING_ROUTE.format(review_id),
                headers=headers,
                json={
                    "field": "green_s",
                    "level_no": 1,
                    "plan_id": 1,
                    "phase_id": "Φ1",
                    "value": value,
                },
            ).status_code
            == 200
        )
    store = pd.PhaseDiagramStore(genesis_jobs.genesis_dir() / "phase-diagrams")
    history = store.amendments(review_id).history
    assert [(event.was, event.now) for event in history] == [("30.0", "31.0"), ("31.0", "32.0")]
    assert all(event.field == "green_s" and event.phase_id == "Φ1" for event in history)


def test_the_amended_amber_is_the_declaration_the_confirmation_records(
    client, headers, store
):
    """#58: 'The edited 황색시간 IS the amber declaration.' The separate 선언
    field is gone from the screen, so the number the table holds is the number
    the attestation must carry — with a provenance sentence the SERVER wrote."""
    review_id = timed_review(client, headers)
    amended = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "declared_amber_s", "value": 3.5},
    )
    assert amended.status_code == 200, amended.text
    sheet = amended.json()["sheet"]
    assert sheet["declared_amber_s"] == 3.5
    assert sheet["amber_declared_by"] == "phase-uploader" and sheet["amber_declared_at"]
    assert "phase-uploader" in sheet["amber_declaration_note"]
    assert "이전 값" in sheet["amber_declaration_note"], "the note names what it replaced"

    # The screen sends exactly what it was shown; the attestation records it.
    response = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(
            store,
            review_id,
            declared_amber_s=sheet["declared_amber_s"],
            amber_declaration_note=sheet["amber_declaration_note"],
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["attestation"]["declared_amber_s"] == 3.5
    assignment = store.confirmed_mapping(review_id).to_assignment()
    assert assignment["plan"]["declared_amber_s"] == 3.5


def test_an_amended_green_reaches_the_signals_document(client, headers, store):
    """The card and the build read the SAME stored rows — otherwise the screen
    would be editing a number nothing downstream ever sees."""
    review_id = timed_review(client, headers)
    client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "green_s", "level_no": 1, "plan_id": 1, "phase_id": "Φ1", "value": 33.0},
    )
    # The stated 주기 must move with it, or signals_declared refuses the sum.
    client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "cycle_s", "level_no": 1, "plan_id": 1, "value": 71.0},
    )
    client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id),
    )
    document = pd.build_signals_document([store.confirmed_mapping(review_id)])
    parsed_doc = parse_side_input("signals", json.dumps(document).encode("utf-8"))
    build = build_signal_assignments(parsed_doc)
    assert build.ok, [issue.code for issue in build.refusals]
    plan, _mapping = build.assignments[7]
    assert [phase.green_s for phase in plan.program.phases] == [33.0, 30.0]
    assert plan.program.cycle_time_s == 71.0


def test_a_client_sheets_timing_is_read_only_and_refuses_by_name(client, headers, store):
    """A 대전 review's numbers were read out of the CLIENT's own document.
    Rewriting them on screen would be this app editing that document."""
    review_id, _text = daejeon_review(store)
    refused = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "green_s", "level_no": 1, "plan_id": 1, "phase_id": "Φ1", "value": 99.0},
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "phase_diagram_timing_not_amendable"
    assert any("엑셀로 내보내" in reason for reason in detail["reasons"]), "names the way out"

    # The 황색시간 is the exception on EVERY medium: no sheet of any kind prints
    # the amber alone, so the person typing it always was the declaration.
    declared = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "declared_amber_s", "value": 3.0},
    )
    assert declared.status_code == 200, declared.text
    assert declared.json()["sheet"]["declared_amber_s"] == 3.0
    # ...and the sheet's own timing still renders on the card, unedited.
    assert declared.json()["sheet"]["timing"], "a 대전 review shows its timing too"


@pytest.mark.parametrize(
    "body, code",
    [
        (
            {"field": "green_s", "level_no": 9, "plan_id": 9, "phase_id": "Φ1", "value": 10.0},
            "phase_diagram_timing_unknown_plan",
        ),
        (
            {"field": "green_s", "level_no": 1, "plan_id": 1, "phase_id": "Φ9", "value": 10.0},
            "phase_diagram_timing_unknown_phase",
        ),
        (
            {"field": "green_s", "level_no": 1, "plan_id": 1, "phase_id": "Φ1", "value": "삼십"},
            "phase_diagram_timing_value_invalid",
        ),
        (
            {"field": "green_s", "level_no": 1, "plan_id": 1, "phase_id": "Φ1", "value": None},
            "phase_diagram_timing_value_invalid",
        ),
        ({"field": "declared_amber_s", "value": 0}, "phase_diagram_timing_value_invalid"),
        ({"field": "declared_amber_s", "value": None}, "phase_diagram_timing_value_invalid"),
    ],
)
def test_an_amendment_never_creates_or_guesses_a_value(client, headers, body, code):
    """An amendment corrects what is on file. It does not invent a plan, a 현시
    or a number — every one of those is a named 409 that changes nothing."""
    review_id = timed_review(client, headers)
    before = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers).json()
    response = client.patch(TIMING_ROUTE.format(review_id), headers=headers, json=body)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == code
    after = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers).json()
    assert after["sheet"]["timing"] == before["sheet"]["timing"], "a refusal changes nothing"


def test_the_unknown_plan_refusal_names_the_plans_that_do_exist(client, headers):
    review_id = timed_review(client, headers)
    response = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "cycle_s", "level_no": 4, "plan_id": 4, "value": 60.0},
    )
    assert response.status_code == 409
    reasons = response.json()["detail"]["reasons"]
    assert any("수준 1 / 계획 1" in reason for reason in reasons)


def test_the_card_says_whether_the_machine_compared_anything(client, headers, store):
    """#58's other half: the 기계 대조 section renders only where a comparison
    actually happened, and the SERVER is what answers that — both directions."""
    uploaded = timed_review(client, headers)
    excel = client.get(f"/api/genesis/phase-diagrams/{uploaded}", headers=headers).json()
    assert excel["cross_check"]["compared"] is False
    # The finding itself is NOT deleted from the record — only the screen stops
    # rendering a section whose entire content is "nothing was compared".
    assert [f["code"] for f in excel["cross_check"]["findings"]] == [pd.CHECK_NO_TEXT_LAYER]

    review_id, _text = daejeon_review(store)
    vision = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers).json()
    assert vision["cross_check"]["compared"] is True
    assert vision["cross_check"]["findings"], "a vision review's cross-check is untouched"


def test_the_card_reads_the_stored_timing_verbatim(client, headers):
    review_id = timed_review(client, headers)
    payload = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers).json()
    assert payload["sheet"]["timing"] == [
        {
            "level_no": 1,
            "plan_id": 1,
            "cycle_s": 68.0,
            "offset_s": 5.0,
            "time_of_day": "07:00~09:00",
            "phases": [
                {
                    "phase_id": "Φ1",
                    "green_s": 30.0,
                    "clearance_s": 4.0,
                    "min_green_s": 25.0,
                    "max_green_s": 60.0,
                },
                {
                    "phase_id": "Φ2",
                    "green_s": 30.0,
                    "clearance_s": 4.0,
                    "min_green_s": None,
                    "max_green_s": None,
                },
            ],
        }
    ]
    # An EMPTY actuated band stays None on the wire too: the card renders a
    # min/max column only where a phase carries one.
    assert payload["sheet"]["timing"][0]["phases"][1]["min_green_s"] is None


# ---------------------------------------------------------------------------
# §7 Unit #58, second pass — the races and the two refusals the refuter found
# ---------------------------------------------------------------------------
#
# Every test below was written against a REPRODUCTION, not against a worry.


def test_two_amendments_to_one_review_cannot_lose_each_other(client, headers, monkeypatch):
    """THE LOST WRITE, reproduced and closed.

    Measured before ``_STORE_LOCK`` existed: two threads amending DIFFERENT
    fields of the same review each read ``manual-sheet.json``, each mutated
    their own copy, and the second write erased the first field's new value —
    while BOTH ledger entries were recorded with a ``was → now`` that read as if
    they had landed. An audit trail asserting a change that never happened is a
    worse failure than the lost number.

    The interleave is forced, not hoped for: the first writer is made to pause
    at exactly the read-modify-write seam (after reading the sheet, before
    writing it) and the second writer is released into that window. With the
    lock held across the whole cycle the second writer simply waits; without it,
    it reads the pre-state and one field reverts.
    """
    review_id = timed_review(client, headers)
    # Built in the MAIN thread: the workspace is a ContextVar and does not
    # travel to a new thread, so the store's root is resolved here, once.
    store = pd.PhaseDiagramStore(genesis_jobs.genesis_dir() / "phase-diagrams")

    at_the_seam = threading.Event()
    original_manual_sheet = pd.PhaseDiagramStore.manual_sheet

    def pausing_manual_sheet(self, rid):
        stored = original_manual_sheet(self, rid)
        if not at_the_seam.is_set():
            at_the_seam.set()
            time.sleep(0.3)
        return stored

    monkeypatch.setattr(pd.PhaseDiagramStore, "manual_sheet", pausing_manual_sheet)

    failures: list[BaseException] = []

    def amend(field: str, phase_id: str, value: float) -> None:
        try:
            store.amend_timing(
                review_id,
                field=field,
                value=value,
                level_no=1,
                plan_id=1,
                phase_id=phase_id,
                edited_by="racer",
            )
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)

    first = threading.Thread(target=amend, args=("green_s", "Φ1", 31.0))
    second = threading.Thread(target=amend, args=("clearance_s", "Φ2", 9.0))
    first.start()
    assert at_the_seam.wait(5), "the first writer never reached the read-modify-write seam"
    second.start()
    first.join(20)
    second.join(20)
    assert not first.is_alive() and not second.is_alive(), "a writer never finished"
    assert failures == [], failures

    phases = {row["phase_id"]: row for row in store.timing_plans(review_id)[0]["phases"]}
    assert phases["Φ1"]["green_s"] == 31.0, "the first amendment was overwritten"
    assert phases["Φ2"]["clearance_s"] == 9.0, "the second amendment was overwritten"

    # ...and the ledger describes exactly the two changes that LANDED.
    history = store.amendments(review_id).history
    assert sorted((event.field, event.now) for event in history) == [
        ("clearance_s", "9.0"),
        ("green_s", "31.0"),
    ]


def test_the_store_serialises_its_writes_the_way_every_sibling_store_does(client, headers):
    """The lock is a MODULE lock, not a per-instance one, and that is
    load-bearing: ``routers/genesis._phase_diagram_store()`` builds a fresh
    ``PhaseDiagramStore`` on every request, so a lock on the instance would be a
    lock two callers never share."""
    assert isinstance(pd._STORE_LOCK, type(threading.RLock()))
    first = pd.PhaseDiagramStore(genesis_jobs.genesis_dir() / "phase-diagrams")
    second = pd.PhaseDiagramStore(genesis_jobs.genesis_dir() / "phase-diagrams")
    assert first is not second, "the store is constructed per call, which is why the lock is not on it"


def test_a_confirmation_may_not_sign_an_amber_the_store_no_longer_holds(
    client, headers, store
):
    """#58 removed the separate 선언 황색시간 box: the number IS the 신호 시간
    table's value. So a confirmation carrying a different one was made against a
    number that is no longer there — the same species as the image-sha
    staleness, refused the same way rather than silently signing the client's
    copy."""
    review_id = timed_review(client, headers)
    _declare_amber(client, headers, review_id, amber=3.5)

    stale = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id, declared_amber_s=2.0),
    )
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail["code"] == "phase_diagram_attestation_stale"
    assert any("황색시간" in reason for reason in detail["reasons"])
    assert any("2.0" in reason and "3.5" in reason for reason in detail["reasons"])
    assert store.attestation(review_id) is None, "a refused confirmation records nothing"

    # The number that IS on the table signs.
    ok = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(store, review_id, declared_amber_s=3.5),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["attestation"]["declared_amber_s"] == 3.5


def test_signing_no_amber_when_the_store_holds_none_is_not_stale(client, headers, store):
    """Both directions. 'Nobody has declared one' is a real state — the signals
    build is what refuses it, by name — so a confirmation that honestly carries
    no amber must not be turned away here."""
    created = client.post(
        BLANK_ROUTE, headers=headers, json={"intersection_name": "무선언네거리"}
    ).json()
    review_id = created["review_id"]
    response = client.post(
        f"/api/genesis/phase-diagrams/{review_id}/confirmation",
        headers=headers,
        json=_confirmation(
            store,
            review_id,
            approach_phases={"1": "Φ1"},
            declared_amber_s=None,
            amber_declaration_note=None,
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["attestation"]["declared_amber_s"] is None


def test_a_corrupt_amendment_ledger_is_quarantined_rather_than_crashing(
    client, headers, store
):
    """A hand-edited or truncated ``amendments.json`` used to reach the browser
    as an HTTP 500 carrying a raw JSONDecodeError. It is moved aside with its
    bytes intact instead, and the review stays readable."""
    review_id = timed_review(client, headers)
    _declare_amber(client, headers, review_id, amber=3.2)
    ledger_path = store.review_dir(review_id) / pd.AMENDMENT_FILENAME
    assert ledger_path.is_file()
    corrupt = b'{"kind": "prism.phase_diagram_amendments.v1", "history": [ {"at"'
    ledger_path.write_bytes(corrupt)

    payload = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers)
    assert payload.status_code == 200, payload.text
    # The DECLARATION is gone with the ledger, and the card falls back to what
    # the workbook said rather than inventing an author for a number.
    assert payload.json()["sheet"]["amber_declared_by"] is None

    quarantined = list(store.review_dir(review_id).glob(f"{pd.AMENDMENT_FILENAME}.corrupt-*"))
    assert len(quarantined) == 1, quarantined
    assert quarantined[0].read_bytes() == corrupt, "the original bytes are preserved verbatim"

    # ...and the review is writable again: a fresh ledger starts.
    again = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "declared_amber_s", "value": 3.3},
    )
    assert again.status_code == 200, again.text
    assert again.json()["sheet"]["declared_amber_s"] == 3.3


def test_a_quarantined_ledger_is_DISCLOSED_and_not_silently_healed(client, headers, store):
    """QUARANTINING STOPPED A CRASH; SAYING NOTHING AFTERWARDS WOULD REPLACE ONE
    SILENT FAILURE WITH ANOTHER.

    Measured (refuter round 3): after the corruption the review payload carried
    no trace at all, so a reviewer read a short clean history as "nobody ever
    amended this" — against this codebase's own sibling discipline
    (``demand_target_map`` / ``calibration_attestation`` persist a quarantined
    code, path and trilingual sentence, queryable). The disclosure is now on the
    wire, in three languages, and it is STICKY.
    """
    review_id = timed_review(client, headers)
    _declare_amber(client, headers, review_id, amber=3.2)
    ledger_path = store.review_dir(review_id) / pd.AMENDMENT_FILENAME
    ledger_path.write_bytes(b'{"kind": "prism.phase_diagram_amendments.v1", "history": [ {"at"')

    payload = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers)
    assert payload.status_code == 200, payload.text
    disclosure = payload.json()["sheet"]["amendments_quarantine"]
    assert disclosure is not None, "the review payload said nothing about the lost history"
    assert disclosure["quarantined"] is True
    assert disclosure["quarantine_path"] and "corrupt-" in disclosure["quarantine_path"]
    assert disclosure["quarantined_at"]
    # Trilingual, and each one names where the bytes went.
    for language in ("ko", "en", "ja"):
        assert disclosure["message"][language], language
        assert disclosure["quarantine_path"] in disclosure["message"][language], language
    assert "이전 수정 내역은 이 화면에 없습니다" in disclosure["message"]["ko"]

    # PERSISTED, not merely computed for one response: the fresh ledger on disk
    # carries the flag, so the disclosure survives this process.
    assert store.amendments(review_id).quarantined is True
    assert json.loads(ledger_path.read_bytes().decode("utf-8"))["quarantined"] is True

    # STICKY: a later amendment appends to a history that is still missing what
    # was lost, so the disclosure must outlive the next edit.
    again = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "declared_amber_s", "value": 3.4},
    )
    assert again.status_code == 200, again.text
    assert again.json()["sheet"]["amendments_quarantine"]["quarantined"] is True

    # A HEALTHY review says null rather than an empty object nobody can test.
    # A DIFFERENT workbook name, because the review id is keyed on (file,
    # 교차로명) — re-uploading "시간.xlsx" would land back in the very review
    # this test just corrupted.
    healthy = client.post(
        f"{UPLOAD_ROUTE}?filename={quote('건강한시간.xlsx', safe='')}",
        headers=headers,
        content=workbook(TIMED_ROWS, timing=TIMING_ROWS),
    ).json()["created"][0]["review_id"]
    assert healthy != review_id
    assert (
        client.get(f"/api/genesis/phase-diagrams/{healthy}", headers=headers)
        .json()["sheet"]["amendments_quarantine"]
        is None
    )


def test_a_write_is_atomic_so_a_reader_never_sees_half_a_document(store, client, headers):
    """The other half of the lock: readers take none, so the writer has to
    become the new file in one step rather than truncate-then-fill."""
    review_id = timed_review(client, headers)
    path = store.review_dir(review_id) / pd.MANUAL_SHEET_FILENAME
    before = path.read_bytes()
    client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "green_s", "level_no": 1, "plan_id": 1, "phase_id": "Φ1", "value": 34.0},
    )
    after = path.read_bytes()
    assert before != after
    assert json.loads(after.decode("utf-8"))["timing"][0]["phases"][0]["green_s"] == 34.0
    # No temp file survives a successful write.
    assert list(store.review_dir(review_id).glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# §8 Unit #58, round 3 — the lock's guarantees, VERIFIED rather than asserted
# ---------------------------------------------------------------------------
#
# MEASURED (refuter, round 3): the lock was real but its verification was not.
# Removing ``with _STORE_LOCK:`` from ``put_proposal`` failed ZERO tests, and
# reverting ``_write_json`` to a plain ``write_text`` failed zero too — the
# single race test only ever exercised ONE path, so every other write path and
# the atomicity claim were held up by nothing but the source comment.

#: Every method of ``PhaseDiagramStore`` that must take :data:`pd._STORE_LOCK`
#: ITSELF. Measured 2026-08-25 by walking the class's AST. A write path added
#: without the lock fails the set equality below; so does a write path that
#: quietly drops it.
LOCK_ACQUIRING_WRITE_PATHS = {
    "put_render",
    "put_manual_review",
    "put_proposal",
    "put_attestation",
    "amend_timing",
    "_amend_declared_amber",
    "_append_amendment",
    "_write_amendments",
    "_quarantine_amendments",
}

#: The two bodies that write while the lock is ALREADY held by their caller.
#: Named, because "it is locked by whoever calls me" is a claim that has to be
#: written down somewhere a reader can check — their docstrings say so too.
LOCK_HELD_BY_CALLER = {"_amend_timing_locked", "_declare_amber_locked"}


def _store_class_ast() -> ast.ClassDef:
    source = pathlib.Path(pd.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PhaseDiagramStore"
    )


def _acquires_store_lock(function: ast.FunctionDef) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.With):
            for item in node.items:
                expression = item.context_expr
                if isinstance(expression, ast.Name) and expression.id == "_STORE_LOCK":
                    return True
    return False


def _writes_to_disk(function: ast.FunctionDef) -> bool:
    """Does this body actually put bytes on disk (or move them)?"""
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None
            )
            if name in {"_write_json", "write_bytes", "write_text", "replace", "unlink"}:
                return True
    return False


def test_every_write_path_in_the_store_acquires_the_module_lock():
    """STRUCTURAL, over the whole class, because one race test proves one path.

    Two directions, and both matter. A write path that DROPS the lock fails the
    set equality; a NEW write path that never takes it fails the second
    assertion and has to be added here deliberately — which is the review this
    unit's own lost-write bug never got.
    """
    store = _store_class_ast()
    methods = [node for node in store.body if isinstance(node, ast.FunctionDef)]
    assert methods, "PhaseDiagramStore has no methods — did the class move?"

    acquiring = {method.name for method in methods if _acquires_store_lock(method)}
    assert acquiring == LOCK_ACQUIRING_WRITE_PATHS, (
        "the set of methods that take _STORE_LOCK changed: "
        f"missing={sorted(LOCK_ACQUIRING_WRITE_PATHS - acquiring)}, "
        f"unexpected={sorted(acquiring - LOCK_ACQUIRING_WRITE_PATHS)}"
    )

    writers = {method.name for method in methods if _writes_to_disk(method)}
    unguarded = writers - acquiring - LOCK_HELD_BY_CALLER
    assert not unguarded, (
        f"these methods write to disk without taking _STORE_LOCK: {sorted(unguarded)}. "
        "Either take it, or name the caller that holds it in LOCK_HELD_BY_CALLER."
    )
    for name in LOCK_HELD_BY_CALLER:
        body = next(method for method in methods if method.name == name)
        assert "_STORE_LOCK" in (ast.get_docstring(body) or ""), (
            f"{name} writes under a caller's lock and its docstring must say so"
        )


def test_a_re_upload_and_an_amendment_cannot_erase_each_other(client, headers, monkeypatch):
    """THE CROSS-PATH RACE, which is the one a per-method lock would miss.

    ``put_manual_review`` (a corrected re-upload) REPLACES the very file
    ``amend_timing`` is mid-cycle on. The two race against each other, not each
    against itself — so the lock has to be one lock over the whole store, and
    this is the test that says so.
    """
    review_id = timed_review(client, headers)
    store = pd.PhaseDiagramStore(genesis_jobs.genesis_dir() / "phase-diagrams")
    render = store.render(review_id)

    at_the_seam = threading.Event()
    released = threading.Event()
    original_manual_sheet = pd.PhaseDiagramStore.manual_sheet

    def pausing_manual_sheet(self, rid):
        stored = original_manual_sheet(self, rid)
        if not at_the_seam.is_set():
            at_the_seam.set()
            released.wait(3)
        return stored

    monkeypatch.setattr(pd.PhaseDiagramStore, "manual_sheet", pausing_manual_sheet)

    failures: list[BaseException] = []

    def amend() -> None:
        try:
            store.amend_timing(
                review_id,
                field="green_s",
                value=44.0,
                level_no=1,
                plan_id=1,
                phase_id="Φ1",
                edited_by="racer",
            )
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)

    def reupload() -> None:
        try:
            store.put_manual_review(
                review_id,
                render,
                sheet={
                    "intersection_name": "둔산네거리",
                    "intersection_no": "1234",
                    "phase_ids": ["Φ1", "Φ2"],
                    "plan_choices": [],
                    "timing": [
                        {
                            "level_no": 1,
                            "plan_id": 1,
                            "cycle_s": 68.0,
                            "offset_s": 5.0,
                            "time_of_day": "07:00~09:00",
                            "phases": [
                                {"phase_id": "Φ1", "green_s": 30.0, "clearance_s": 4.0},
                                {"phase_id": "Φ2", "green_s": 30.0, "clearance_s": 4.0},
                            ],
                        }
                    ],
                    "declared_amber_s": 3.0,
                },
            )
        except BaseException as exc:  # pragma: no cover - reported by the assert
            failures.append(exc)

    amender = threading.Thread(target=amend)
    uploader = threading.Thread(target=reupload)
    amender.start()
    assert at_the_seam.wait(5), "the amendment never reached the read-modify-write seam"
    uploader.start()
    # The re-upload must NOT be able to slip in and out while the amendment is
    # paused: with one store-wide lock it blocks here, and only the release lets
    # the amendment finish first.
    uploader.join(0.6)
    assert uploader.is_alive(), (
        "the re-upload completed while an amendment held the store — one of them "
        "is about to overwrite the other's file"
    )
    released.set()
    amender.join(20)
    uploader.join(20)
    assert not amender.is_alive() and not uploader.is_alive()
    assert failures == [], failures

    # Whichever ran second is the state on disk; what must never happen is a
    # TORN state — a sheet holding neither writer's coherent document.
    phases = {row["phase_id"]: row for row in store.timing_plans(review_id)[0]["phases"]}
    assert set(phases) == {"Φ1", "Φ2"}
    assert phases["Φ1"]["green_s"] in (30.0, 44.0)
    assert phases["Φ2"]["green_s"] == 30.0
    assert store.amendments(review_id).history, "the amendment was recorded"


def test_a_failed_write_leaves_the_previous_document_intact_and_no_litter(
    client, headers, store, monkeypatch
):
    """ATOMICITY, OBSERVED rather than read off the source.

    ``_write_json`` builds the new document in a temp file and swaps it in with
    ``os.replace``. Injecting a failure at the swap proves the two things that
    shape buys: the reader-visible file still holds the PREVIOUS bytes (a plain
    ``write_text`` would have truncated it), and the temp file does not survive
    as litter.
    """
    review_id = timed_review(client, headers)
    path = store.review_dir(review_id) / pd.MANUAL_SHEET_FILENAME
    before = path.read_bytes()

    def exploding_replace(source, target):
        raise OSError("injected: the swap failed")

    # Captured and restored BY HAND rather than through `monkeypatch.undo()`:
    # undo() would also unwind the `workspace_root` fixture's own patches (same
    # function-scoped monkeypatch), and the follow-up read would then look for
    # this review in the real workspaces root.
    original_replace = pd.os.replace
    monkeypatch.setattr(pd.os, "replace", exploding_replace)
    response = client.patch(
        TIMING_ROUTE.format(review_id),
        headers=headers,
        json={"field": "green_s", "level_no": 1, "plan_id": 1, "phase_id": "Φ1", "value": 99.0},
    )
    assert response.status_code == 409, response.text

    assert path.read_bytes() == before, "a failed write truncated the document it replaced"
    assert list(store.review_dir(review_id).glob("*.tmp")) == [], "the temp file was left behind"

    # And the review still reads, with its old numbers.
    monkeypatch.setattr(pd.os, "replace", original_replace)
    response = client.get(f"/api/genesis/phase-diagrams/{review_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["sheet"]["timing"][0]["phases"][0]["green_s"] == 30.0


def test_the_writer_is_temp_file_then_fsync_then_replace():
    """The structural half of the pin above: the three steps, in this order, in
    the store's ONE writer. An implementation that fsyncs nothing, or writes in
    place, cannot give the guarantee the test above observes."""
    source = pathlib.Path(pd.__file__).read_text(encoding="utf-8")
    writer = source[source.index("def _write_json(") :]
    writer = writer[: writer.index("\n__all__")]
    temp_at = writer.index("tmp_path = path.with_name(")
    fsync_at = writer.index("os.fsync(handle.fileno())")
    replace_at = writer.index("os.replace(tmp_path, path)")
    assert temp_at < fsync_at < replace_at, "temp file -> fsync -> replace, in that order"
    assert "tmp_path.unlink(missing_ok=True)" in writer, "a failed write cleans up its own temp file"
    assert ".write_text(" not in writer, "the writer must not write the document in place"
