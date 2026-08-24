"""Integrity tests for services.report.spine — Track R unit R-1.

The spine is a TRANSCRIPTION plus an AUDIT, so these tests protect exactly two
properties and nothing else:

1. **The transcription is faithful, both ways.** Every quote and page in the
   module is checked against ``docs/product/reference/mrwa-reporting-requirements.md``
   (module → file), and every quoted requirement and every Table 5-15 cell in
   that file is checked against the module (file → module). Nothing quoted here
   is typed out in this test: the fragments are read out of the module's own
   ``CIT_*`` constants and out of the extract file, so a mis-transcribed
   citation FAILS instead of being faithfully re-typed into the test. That is
   the mechanism ``tests/test_dataimport_fixed_time.py`` established for
   ``services/dataimport/*.py``; this file runs it against a third document,
   and adds the reverse direction, which that one does not have.

2. **The availability markers are honest.** Every ``AVAILABLE`` output names an
   extractor, and this file IMPORTS it and reads its source to prove both the
   symbol and the result-table/column it claims to read really exist; and no
   two outputs in one Table 5-15 row may be extracted by the identical call.
   Every ``NOT_AVAILABLE`` output names no extractor and states why. The two
   sets are enumerated, so an output cannot be promoted to AVAILABLE (or
   quietly demoted) without a deliberate edit here.

   **The limit of (2), stated rather than implied.** The evidence check is a
   token search, so it proves a column EXISTS in the cited reader, never that
   it holds the quantity the guideline asked for. An invented token fails, and
   so does one borrowed from a different module — but any real identifier in
   the cited reader passes, including a column of a different measurement
   entirely: pointing Route/"Average travel time" at ``STOPSAVG`` (average
   stops per vehicle, from the network performance query) keeps every test in
   this file green. That mutation was run, not imagined. What IS caught is the
   careless-copy-paste class, by the within-row distinctness rule: a sibling's
   column pasted over its neighbour's makes two outputs identical and fails.
   Quantity-to-column correctness stays a human-review property, and the
   per-output ``reason`` strings exist so that review can be done by reading.
   Mechanising it would mean restating what each Vissim column means — a second
   transcription, itself unverified, which is the failure this whole file
   exists to prevent.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from routers import auth as auth_router
from services.report import spine

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACT_PATH = REPO_ROOT / spine.EXTRACT

#: Proves the file the module cites is the document it thinks it is, not some
#: other extract that happens to sit at that path.
EXTRACT_MARKER = "§5.8 / §5.8.1 Model Output"

#: Discovery floor. Ten quoted requirements live in the extract (five §2.12.2
#: sentences, two §5.8 paragraphs, §5.8.1.1, §5.8.1.2, and the minimum clause);
#: finding FEWER constants than that would otherwise read as a pass.
_MINIMUM_CITATIONS = 10

_BLOCKQUOTE_RE = re.compile(r"^[ \t]*>+[ \t]?", re.MULTILINE)


def _normalise_prose(text: str) -> str:
    """Put a quotation and the document it came from into the same shape.

    The extract hard-wraps its quotes (a sentence spanning two lines is still
    the same sentence) and wears ``**bold**``/backticks in places; a quotation
    of any of it carries none of that. Same helper, same reasons, as
    ``tests/test_dataimport_fixed_time.py``'s — the extract's own honesty
    caveat asks for exactly this ("공백 비교는 정규화 후에").
    """
    text = _BLOCKQUOTE_RE.sub("", text)
    return " ".join(text.replace("**", "").replace("`", "").split())


def _citation_constants() -> dict[str, str]:
    """Every citation the module declares, found by introspection rather than
    by a list maintained here — that is what makes an eleventh constant covered
    the day it is written."""
    return {
        name: value
        for name, value in vars(spine).items()
        if name.startswith("CIT_") and isinstance(value, str)
    }


_BOILERPLATE_RE = re.compile(rf".*?\({re.escape(spine.EXTRACT)}\)\s*—\s*", re.DOTALL)
_PAGE_RE = re.compile(r"\bp\.(\d+)\b")
_PAGE_MARKER_RE = re.compile(r"\(p\.(\d+)\)")


def _split_citation(citation: str) -> tuple[str, str]:
    """``(claimed page, quoted text)`` for one ``CIT_*``.

    Returns ``("", "")`` when the citation does not have the module's declared
    shape — the caller reports that by constant name rather than skipping it,
    because a citation with no checkable quote is the failure, not an exemption.
    """
    boilerplate = _BOILERPLATE_RE.match(citation)
    if boilerplate is None:
        return ("", "")
    page = _PAGE_RE.search(citation[: boilerplate.end()])
    # Greedy first-quote-to-last-quote: a quotation may contain apostrophes of
    # its own, so a lazy match would truncate it.
    quoted = re.search(r"'(.*)'", citation[boilerplate.end() :], re.DOTALL)
    if page is None or quoted is None:
        return ("", "")
    return (page.group(1), _normalise_prose(quoted.group(1)))


@pytest.fixture(scope="module")
def extract_text() -> str:
    return EXTRACT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def normalised_extract(extract_text: str) -> str:
    return _normalise_prose(extract_text)


# ---------------------------------------------------------------------------
# 1. Transcription: module -> file
# ---------------------------------------------------------------------------

def test_the_extract_the_spine_cites_is_a_file_on_this_machine(extract_text: str):
    """Traceability is only checkable while the source ships with the repo. If
    the extract moves or empties, every citation in the module becomes
    unfalsifiable prose, and this is the test that says so."""
    assert EXTRACT_PATH.is_file(), f"{EXTRACT_PATH} is missing — spine.EXTRACT points nowhere"
    assert EXTRACT_MARKER in extract_text


def test_every_citation_quotes_the_extract_verbatim(normalised_extract: str):
    """MUTATION TARGET: reword any quote in spine.py and this dies.

    The asserted text IS the module's text — read out of its ``CIT_*``
    constants — so editing a citation away from its source, or editing the
    extract out from under a citation, stops the fragment being found and this
    test names the constant.
    """
    constants = _citation_constants()
    assert len(constants) >= _MINIMUM_CITATIONS, f"only found {sorted(constants)}"

    unquotable: list[str] = []
    missing: list[tuple[str, str]] = []
    for name, citation in sorted(constants.items()):
        assert spine.EXTRACT in citation, f"{name} does not name the file it cites"
        _page, quote = _split_citation(citation)
        if not quote:
            unquotable.append(name)
            continue
        if quote not in normalised_extract:
            missing.append((name, quote))

    assert unquotable == [], f"citations quoting nothing checkable: {unquotable}"
    assert missing == [], f"quoted in spine.py, absent from {spine.EXTRACT}: {missing}"


def test_every_citation_claims_the_page_the_extract_prints_after_its_quote(
    normalised_extract: str,
):
    """MUTATION TARGET: change any ``p.NNN`` in spine.py and this dies.

    The expected page is not typed here either. The extract prints a ``(p.NNN)``
    marker immediately after every quote it carries, so for each constant this
    finds the quote in the extract and reads the FIRST page marker that follows
    it — a page that does not sit under the text it is attached to is exactly
    the failure ``mrwa_reference``'s calibration-principle regression was about.
    """
    wrong: list[tuple[str, str, str]] = []
    for name, citation in sorted(_citation_constants().items()):
        claimed, quote = _split_citation(citation)
        assert quote, f"{name} carries no checkable quote"
        index = normalised_extract.find(quote)
        assert index >= 0, f"{name}'s quote is not in {spine.EXTRACT}"
        marker = _PAGE_MARKER_RE.search(normalised_extract[index + len(quote) :])
        assert marker is not None, f"{spine.EXTRACT} prints no page marker after {name}'s quote"
        if marker.group(1) != claimed:
            wrong.append((name, claimed, marker.group(1)))

    assert wrong == [], f"(constant, claimed page, page the extract prints): {wrong}"


# ---------------------------------------------------------------------------
# 2. Transcription: file -> module (a requirement cannot be dropped)
# ---------------------------------------------------------------------------

def test_every_quoted_requirement_in_the_extract_is_carried_by_a_citation(
    normalised_extract: str,
):
    """MUTATION TARGET: delete a ``CIT_*`` (or a whole section's basis) and this
    dies.

    The other direction of the pin. The extract wraps every verbatim
    requirement in double quotes, so they can be enumerated mechanically; each
    one must appear in some citation this module declares. A requirement absent
    from the spine reads to a client as a requirement that does not exist,
    which is the exact failure the extract's own honesty caveat warns about
    ("절을 조용히 빼먹으면 안 된다").
    """
    module_quotes = [
        quote for _page, quote in map(_split_citation, _citation_constants().values()) if quote
    ]
    file_quotes = re.findall(r'"([^"]+)"', normalised_extract)
    assert len(file_quotes) >= _MINIMUM_CITATIONS, f"only {len(file_quotes)} quotes in the extract"

    dropped = [
        quoted
        for quoted in file_quotes
        if not any(_normalise_prose(quoted) in carried for carried in module_quotes)
    ]
    assert dropped == [], f"quoted in {spine.EXTRACT}, carried by no CIT_* in spine.py: {dropped}"


def _extract_table_5_15_rows(extract_text: str) -> list[list[str]]:
    """Table 5-15's data rows, parsed out of the extract's markdown.

    Anchored on the table's own heading so the §2.12.2 derived-spine table
    (three columns) above it can never be mistaken for it.
    """
    _before, _sep, after = extract_text.partition("### Table 5-15")
    assert _sep, "the extract no longer carries a '### Table 5-15' heading"
    rows: list[list[str]] = []
    for line in after.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 4 or cells[0] in {"Type", "---"}:
            continue
        rows.append(cells)
    return rows


def test_table_5_15_is_transcribed_cell_for_cell(extract_text: str):
    """MUTATION TARGET: drop or reword ANY output, type, evaluation method or
    time period in spine.TABLE_5_15_ROWS and this dies.

    The expected values are read out of the extract's own markdown table, not
    typed here — so the module's transcription is compared against the source
    document rather than against a second copy of itself.
    """
    file_rows = _extract_table_5_15_rows(extract_text)
    module_rows = spine.TABLE_5_15_ROWS

    assert len(file_rows) == len(module_rows) == 4, (
        f"extract has {len(file_rows)} Table 5-15 rows, module has {len(module_rows)}"
    )

    for cells, row in zip(file_rows, module_rows, strict=True):
        type_cell, outputs_cell, method_cell, period_cell = cells
        assert row.type == type_cell
        assert [output.name for output in row.outputs] == outputs_cell.split(" · ")
        assert row.evaluation_method == method_cell
        assert row.time_period == period_cell


def test_table_5_15_has_four_types_and_every_output_is_reachable():
    """The counts, stated once so a silent shrink is loud: 4 types, 20 outputs
    (7 network-wide, 4 intersection, 4 route, 5 critical section)."""
    types = [row.type for row in spine.TABLE_5_15_ROWS]
    assert types == [
        "Network-wide",
        "Intersection",
        "Route",
        "Critical section (for example, bottle neck)",
    ]
    per_row = [len(row.outputs) for row in spine.TABLE_5_15_ROWS]
    assert per_row == [7, 4, 4, 5]
    assert sum(per_row) == 20

    for row in spine.TABLE_5_15_ROWS:
        assert spine.table_5_15_row(row.type) is row


def test_an_unknown_table_row_or_source_key_raises_rather_than_being_invented():
    with pytest.raises(KeyError):
        spine.table_5_15_row("Corridor")
    with pytest.raises(KeyError):
        spine.source_key("whatever_the_ui_wants")


# ---------------------------------------------------------------------------
# 3. The availability audit
# ---------------------------------------------------------------------------

def _module_source(dotted: str) -> str:
    module = importlib.import_module(dotted)
    assert module.__file__, f"{dotted} has no source file"
    return Path(module.__file__).read_text(encoding="utf-8")


def test_every_available_output_names_an_extractor_that_really_exists():
    """MUTATION TARGET: mark Heat map (or Density, or route delay, or latent
    demand) AVAILABLE and this dies — there is no extractor to name.

    Three things are proved per output, in increasing strength: the reader
    module IMPORTS, the named symbol EXISTS on it, and every evidence token
    (the Vissim result table plus the column, or the constant implementing a
    derived grade) appears in that module's SOURCE. The last one is what makes
    the audit survive a reader that keeps its function name but stops selecting
    the column.

    A fourth property — that no two outputs in one row are extracted by the
    identical call — lives in
    :func:`test_no_two_outputs_in_one_row_cite_the_same_extractor_signature`,
    which also carries the reason this test alone is not enough: existence is
    not meaning, and a real-but-wrong column passes here.
    """
    checked = 0
    for row in spine.TABLE_5_15_ROWS:
        for output in row.outputs:
            if output.availability != spine.AVAILABLE:
                continue
            extractor = output.extractor
            assert extractor is not None, f"{row.type}/{output.name} is AVAILABLE with no extractor"
            assert output.reason, f"{row.type}/{output.name} does not say how it is extracted"

            module = importlib.import_module(extractor.module)
            assert hasattr(module, extractor.symbol), (
                f"{row.type}/{output.name} cites {extractor.module}.{extractor.symbol}, "
                "which does not exist"
            )
            source = _module_source(extractor.module)
            assert extractor.evidence, f"{row.type}/{output.name} names no evidence tokens"
            for token in extractor.evidence:
                assert re.search(rf"\b{re.escape(token)}\b", source), (
                    f"{row.type}/{output.name} claims {extractor.module} reads {token!r}, "
                    "but that token is not in its source"
                )
            checked += 1

    assert checked == 15, f"expected 15 AVAILABLE outputs, audited {checked}"


def test_no_two_outputs_in_one_row_cite_the_same_extractor_signature():
    """MUTATION TARGET: paste a sibling's column over its neighbour's and this
    dies.

    REGRESSION (refuter, 2026-08-24). The test above checks that every evidence
    token EXISTS in the cited reader, which is silent about whether it is the
    right token: swapping Route/'Average travel time' from ``TRAVTM`` to
    ``DISTTRAV`` — the column its own row-mate 'Travel distance' already cites —
    left all twenty tests green, because ``DISTTRAV`` is a real column that
    reader really does select. Every count was unchanged and every token was
    real; the spine simply claimed travel time is extracted from the distance
    column.

    Quantity-to-column correctness cannot be mechanised here (see
    ``services/report/spine.py``'s "WHAT THE AUDIT PROVES, AND WHAT IT DOES
    NOT"): restating what a Vissim column MEANS would just be a second,
    unverified transcription of it. But the realistic failure — a careless
    copy-paste between neighbouring outputs — has a signature this test can
    read: two outputs in ONE row extracted by identical means. One reader call
    cannot be the honest source of two different Table 5-15 quantities.

    Deliberately WITHIN a row, not across the table: 'Volume' (Route) and
    'Traffic volume' (Critical section) are different rows asking for the same
    quantity from different measurements, and they legitimately share the column
    name ``VEHS`` — their signatures differ only by result table, which is
    correct and must keep passing.
    """
    for row in spine.TABLE_5_15_ROWS:
        seen: dict[tuple[str, str, tuple[str, ...]], str] = {}
        for output in row.outputs:
            if output.availability != spine.AVAILABLE or output.extractor is None:
                continue
            signature = (
                output.extractor.module,
                output.extractor.symbol,
                output.extractor.evidence,
            )
            twin = seen.get(signature)
            assert twin is None, (
                f"{row.type}: {output.name!r} and {twin!r} are extracted by the identical call "
                f"{signature[0]}.{signature[1]} reading {list(signature[2])} — one reader call "
                "cannot honestly be the source of two different Table 5-15 quantities, so this "
                "is a column pasted from one output onto its neighbour"
            )
            seen[signature] = output.name

    # The cross-row case this rule deliberately permits, pinned so a later
    # tightening to table-wide distinctness has to face it rather than discover
    # it: two different measurements, same column name, both correct.
    route_volume = {o.name: o for o in spine.table_5_15_row("Route").outputs}["Volume"]
    critical_volume = {
        o.name: o
        for o in spine.table_5_15_row("Critical section (for example, bottle neck)").outputs
    }["Traffic volume"]
    assert route_volume.extractor is not None and critical_volume.extractor is not None
    assert "VEHS" in route_volume.extractor.evidence
    assert "VEHS" in critical_volume.extractor.evidence
    assert route_volume.extractor.evidence != critical_volume.extractor.evidence


def test_the_outputs_this_build_cannot_extract_are_exactly_these_five():
    """MUTATION TARGET: promote or demote any output and this dies.

    The whole NOT_AVAILABLE set, enumerated, because "we never looked" must
    never be able to masquerade as "the data is unavailable" — and because a
    later unit that genuinely wires one of these up should have to delete its
    line here, deliberately.

    Density and Heat map are the extract's own named caveat. The other three
    were measured for this unit: the latent-demand column does not exist in any
    Vissim result table (unit U1's finding, in tools_run.py's docstring), no
    delay-measurement table is read anywhere in this backend, and this row's LoS
    would have to be computed from the Density above.
    """
    assert spine.unavailable_outputs() == (
        ("Network-wide", "Total latent traffic demand"),
        ("Route", "Average delay time"),
        ("Critical section (for example, bottle neck)", "Density"),
        ("Critical section (for example, bottle neck)", "LoS"),
        ("Critical section (for example, bottle neck)", "Heat map"),
    )

    for row in spine.TABLE_5_15_ROWS:
        for output in row.outputs:
            if output.availability != spine.NOT_AVAILABLE:
                continue
            assert output.extractor is None, (
                f"{row.type}/{output.name} is NOT_AVAILABLE but names an extractor"
            )
            assert output.reason.startswith(spine.NO_EXTRACTOR), (
                f"{row.type}/{output.name} does not open with the {spine.NO_EXTRACTOR!r} marker"
            )
            assert len(output.reason) > len(spine.NO_EXTRACTOR) + 40, (
                f"{row.type}/{output.name} states the marker but not the measured reason"
            )


def test_the_intersection_row_keeps_its_los_while_the_critical_section_row_does_not():
    """The one distinction a careless edit would flatten.

    ``results_evidence.los_band`` grades HCM SIGNALIZED CONTROL DELAY, which is
    what the Intersection row's 'Node' evaluation produces — so LoS there is
    AVAILABLE. The Critical section row's method is 'Link · Data collection',
    whose LoS is density-based, and Density is itself unavailable. Two cells
    with the same word in them, two different answers; a spine that gave them
    one answer would be claiming a grade computed from the wrong quantity.
    """
    intersection = {o.name: o for o in spine.table_5_15_row("Intersection").outputs}
    critical = {
        o.name: o
        for o in spine.table_5_15_row("Critical section (for example, bottle neck)").outputs
    }

    assert intersection["LoS for each turning movement and intersection"].availability == (
        spine.AVAILABLE
    )
    assert critical["LoS"].availability == spine.NOT_AVAILABLE
    assert "los_band" in critical["LoS"].reason


def test_every_source_key_names_a_module_that_exists_on_this_machine():
    """A section whose data source points at nothing is a section that can never
    be filled — that must fail loudly rather than read as a plan."""
    assert {entry.key for entry in spine.SOURCE_KEYS} == {
        "approval_ledger",
        "reality_check",
        "gate_report",
        "observed_dataset_ids",
        "holdout_split",
        "named_limitations",
        "run_results",
    }
    for entry in spine.SOURCE_KEYS:
        assert (REPO_ROOT / entry.owner).is_file(), f"{entry.key} points at {entry.owner}, missing"
        assert entry.what, f"{entry.key} does not say what it holds"
        assert spine.source_key(entry.key) is entry


# ---------------------------------------------------------------------------
# 4. The spine: order, basis, and the minimum clause
# ---------------------------------------------------------------------------

def test_the_section_order_is_the_guidelines_own_and_is_stable():
    """MUTATION TARGET: shuffle REGULATION_SECTIONS and this dies.

    The order is the source document's — §2.12.2's five requirements in the
    order the extract lists them, then §5.8, then §5.8.1.1 and §5.8.1.2 — not a
    house preference, which is why it can be pinned at all. Every consumer (the
    /report tree, the DOCX builder) renders in this order, so a shuffle is a
    change to every report this product has ever produced.
    """
    assert [section.id for section in spine.REGULATION_SECTIONS] == [
        "parameter_adjustment_record",
        "statistical_comparison",
        "calibration",
        "validation",
        "limitations",
        "model_evaluation_results",
        "network_wide_performance",
        "intersection_performance",
    ]
    assert spine.build_spine()[: len(spine.REGULATION_SECTIONS)] == spine.REGULATION_SECTIONS


def test_calibration_and_validation_are_separate_sections_because_the_guideline_says_so():
    """§2.12.2's third sentence is the only reason this spine has two sections
    where a tidier table of contents would have one, so the two are pinned to
    that sentence rather than to taste."""
    by_id = {section.id: section for section in spine.REGULATION_SECTIONS}
    for section_id in ("calibration", "validation"):
        assert spine.CIT_2_12_2_SEPARATE_SECTIONS in by_id[section_id].basis
        assert spine.CIT_2_12_2_DATASET_COMPARISON in by_id[section_id].basis
        # Which datasets were used for which process is the sentence's other
        # half, and it is what these two source keys answer.
        assert "observed_dataset_ids" in by_id[section_id].source_keys
        assert "holdout_split" in by_id[section_id].source_keys


def test_every_section_is_backed_by_a_citation_this_module_declares():
    """No section may exist on this spine without a regulatory sentence behind
    it: the spine transcribes requirements, it does not compose a table of
    contents."""
    declared = set(_citation_constants().values())
    for section in spine.REGULATION_SECTIONS:
        assert section.basis, f"{section.id} has no regulatory basis"
        for citation in section.basis:
            assert citation in declared, f"{section.id} cites a string no CIT_* constant declares"
        assert section.title_ko and section.title_en and section.title_ja
        for key in section.source_keys:
            spine.source_key(key)
        for row_type in section.required_output_types:
            spine.table_5_15_row(row_type)


def test_the_spine_declares_itself_a_minimum(normalised_extract: str):
    """MUTATION TARGET: delete SPINE_IS_A_MINIMUM (or drop its citation) and
    this dies.

    §5.8 says the outputs 'include, but are not limited to' Table 5-15. A spine
    presented as the whole of a report, when the authority called it a floor,
    misrepresents the authority — so the sentence is module-level, quotes the
    clause, carries its page, and rides in the payload.
    """
    assert "include, but are not limited to" in normalised_extract
    assert spine.CIT_5_8_MINIMUM_CLAUSE in spine.SPINE_IS_A_MINIMUM
    assert "MINIMUM" in spine.SPINE_IS_A_MINIMUM
    assert "build_spine" in spine.SPINE_IS_A_MINIMUM

    payload = spine.spine_payload()
    assert payload["minimum"]["sentence"] == spine.SPINE_IS_A_MINIMUM
    assert payload["minimum"]["citation"] == spine.CIT_5_8_MINIMUM_CLAUSE


def test_the_core_extends_without_being_edited():
    """The other half of the minimum clause: a house section is an ADDITION to
    the regulatory core, appended after it and marked as not
    regulation-derived — never an amendment of it."""
    extra = spine.ReportSection(
        id="scenario_sensitivity",
        title_ko="민감도 / 시나리오",
        title_en="Sensitivity and scenarios",
        title_ja="感度・シナリオ",
        basis=("Prism house section — no MRWA requirement claimed",),
        source_keys=("run_results",),
        required_output_types=("Network-wide",),
    )
    built = spine.build_spine([extra])

    assert built[: len(spine.REGULATION_SECTIONS)] == spine.REGULATION_SECTIONS
    assert built[-1] is extra
    assert spine.REGULATION_SECTIONS == spine.build_spine(), "the core was mutated by an extension"

    payload = spine.spine_payload([extra])
    assert payload["sections"][-1]["regulation_derived"] is False
    assert all(item["regulation_derived"] for item in payload["sections"][:-1])


def test_an_extension_may_not_shadow_the_core_or_invent_a_data_source():
    with pytest.raises(ValueError):
        spine.build_spine([spine.REGULATION_SECTIONS[0]])
    with pytest.raises(KeyError):
        spine.build_spine(
            [
                spine.ReportSection(
                    id="wishful",
                    title_ko="가상",
                    title_en="Wishful",
                    title_ja="架空",
                    basis=("none",),
                    source_keys=("emissions_model",),
                )
            ]
        )
    with pytest.raises(KeyError):
        spine.build_spine(
            [
                spine.ReportSection(
                    id="wishful",
                    title_ko="가상",
                    title_en="Wishful",
                    title_ja="架空",
                    basis=("none",),
                    source_keys=(),
                    required_output_types=("Corridor",),
                )
            ]
        )
    with pytest.raises(TypeError):
        spine.build_spine([{"id": "not_a_section"}])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# 5. The API surface
# ---------------------------------------------------------------------------

def test_the_payload_round_trips_through_json():
    """The /report UI and the DOCX builder consume this over the wire, so
    'JSON-safe' has to mean it, not nearly."""
    payload = spine.spine_payload()
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload

    section = payload["sections"][0]
    assert set(section) == {
        "id",
        "title",
        "basis",
        "source_keys",
        "required_outputs",
        "regulation_derived",
    }
    assert set(section["title"]) == {"ko", "en", "ja"}
    # Source keys are resolved to their DECLARATION, never to their contents.
    assert set(section["source_keys"][0]) == {"key", "owner", "what"}

    network = payload["table_5_15"][0]
    assert network["type"] == "Network-wide"
    assert network["outputs"][0]["extractor"]["module"] == "services.results_reader"
    assert network["outputs"][-1]["extractor"] is None


def test_the_route_returns_the_spine():
    with TestClient(main.app) as client:
        response = client.get("/api/report/spine")
    assert response.status_code == 200
    assert response.json() == spine.spine_payload()


def test_the_spine_route_is_open_like_every_other_read_on_this_router():
    """Auth parity with its neighbours, asserted here as well as in
    ``tests/test_auth_core.py``'s exact-set ledger.

    ``GET /api/report/spine`` takes no session, and this route discloses less
    than any read on this router: the regulation's own sentences, the names of
    the modules that would fill each section, and which Table 5-15 outputs
    this build cannot extract. No client data, no project data, no decision
    made by reading it.

    This is no longer "nothing on ``/api/report`` is guarded" -- Track R unit
    R-4 (``routers/report.py``'s own module docstring carries the reasoning)
    deliberately guards exactly two routes that WRITE a durable, named,
    provenance-bearing record: uploading a style profile and correcting one.
    Every READ, including the two new ``GET .../style-profiles*`` routes,
    stays open in the same parity this test has always asserted. Pinned as an
    EXACT set (not "guarded == set()") so a route added later to this router
    with an auth dependency is a deliberate edit here, exactly the discipline
    ``tests/test_auth_core.py``'s own scope guard uses for ``/api/genesis``.
    """
    #: The only two routes on this router that create or mutate a durable,
    #: named record — see routers/report.py's module docstring for why these
    #: two and not the reads beside them.
    expected_guarded = {
        "/api/report/style-profiles",
        "/api/report/style-profiles/{profile_id}",
    }

    guarded = set()
    for route in main.app.routes:
        path = getattr(route, "path", None)
        if not path or not path.startswith("/api/report"):
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        pending = [dependant]
        calls = set()
        while pending:
            node = pending.pop()
            if node.call is not None:
                calls.add(node.call)
            pending.extend(node.dependencies)
        if auth_router.current_user in calls or auth_router.optional_current_user in calls:
            guarded.add(path)

    assert guarded == expected_guarded
