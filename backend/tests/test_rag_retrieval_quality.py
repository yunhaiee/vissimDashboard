"""Retrieval quality, measured against the corpus that is actually on disk.

What this file used to assert, and why it was wrong
---------------------------------------------------
``assert status["chunks"] >= 5000`` — a number describing a corpus this
repository has never contained. The real corpus was 985 chunks from 29 markdown
files, so the test failed on every machine, every run, from the day it was
written. It was written off as "environmental" in CLAUDE.md and skipped over for
months, which is the expensive part: the one check pointed at the corpus was
red for a reason nobody read, so nobody noticed that
``rag_documents.list_markdown_documents()`` globbed ``docs/**/*.md`` and NOTHING
else — and that every client PDF, the Main Roads WA Operational Modelling
Guidelines above all, had never been indexed once.

What it asserts now
-------------------
Three separate things, so a failure names which one broke:

* the markdown corpus is present and chunked, against floors measured from this
  checkout rather than an aspiration;
* the PDF corpus, WHEN the source PDFs are on this machine, is page-addressable
  — every chunk carries the document page it came from, because a guideline
  citation without a page number is not a citation;
* each retrieval case retrieves what it names.

The cases are parametrised so a case whose source document is absent SKIPS with
the reason printed by ``pytest -rs``, instead of being quietly dropped or
quietly passing. Client PDFs live in gitignored ``client-data/`` and half the
section directories the retrieval code expects were never shipped in the source
drop, so "this case cannot run here" is a normal state — it just has to be a
visible one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

import pytest

from config import REPO_ROOT
from services import rag_documents
from services.rag_documents import (
    RAG_SECTIONS,
    get_rag_status,
    list_corpus_documents,
    list_markdown_documents,
    load_markdown_chunks,
    search_keyword_rag,
    search_rag,
    title_for_markdown,
)
from services.rag_vector_store import search_vector_index


CASES_PATH = Path(__file__).with_name("fixtures") / "rag_retrieval_cases.json"

#: The calibration workspace's doctrine (#50-d, user-confirmed 2026-08-20).
#:
#: Every doctrine sentence the /calibration screen used to carry lives here now,
#: and the assistant is one of the three readers that arrangement promises --
#: alongside the frontend contract suite and the /docs page. That promise is only
#: worth something if a reader who asks the assistant a doctrine question is
#: actually handed this file, so the checks below ask it.
#:
#: Committed to the repo, not gitignored, so these are FIRM assertions rather
#: than the corpus-presence skips the PDF cases take.
CALIBRATION_DOCTRINE = REPO_ROOT / "docs" / "product" / "calibration-doctrine.md"

#: A sentence a modeller would actually ask about, taken from D2 -- the honest
#: headline the rail used to carry in a tooltip.
CALIBRATION_DOCTRINE_QUERY = "증거가 없는 단계는 완료로 표시"

#: Measured 2026-08-18 on this checkout: 29 markdown documents under ``docs/``
#: producing 989 chunks. The floors sit far enough below that for ordinary
#: documentation churn, while still failing loudly if the corpus collapses — a
#: broken glob, a chunker that returns nothing, a docs/ directory that moved.
#: Unlike 5000, these are numbers this repository can actually meet.
#:
#: They are checked against the MARKDOWN half alone, never against
#: ``get_rag_status()``'s combined count. On a machine that has the client drop,
#: the PDF supplies 521 chunks — 87% of the 600 floor — so reading the floor off
#: the combined number let the markdown chunker collapse from 989 chunks to 87
#: with this test still green (measured by mutation, 2026-08-18). A floor a
#: different corpus can satisfy on your behalf is not a floor.
MARKDOWN_DOCUMENT_FLOOR = 20
MARKDOWN_CHUNK_FLOOR = 600


def load_cases() -> list[dict[str, object]]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


CASES = load_cases()


def searchable_text(hit: dict[str, object]) -> str:
    fields = [
        hit.get("kind"),
        hit.get("section"),
        hit.get("title"),
        hit.get("path"),
        hit.get("chunk_id"),
    ]
    return " ".join(str(field or "").replace("\\", "/") for field in fields).lower()


def corpus_haystack() -> str:
    """Every corpus document path and title, for asking "is this source here?"."""
    return " ".join(
        f"{str(document.path).replace(chr(92), '/')} {document.title}".lower()
        for document in list_corpus_documents()
    )


def pdfs_on_disk() -> list[Path]:
    """Source PDFs this machine has, found WITHOUT asking the ingester.

    The guard on the PDF checks below decides whether they run at all, so it
    must not be derived from ``services.rag_pdf``'s own discovery. Measured: with
    the 289-page client guideline sitting in ``client-data/``, breaking
    ``list_pdf_files()`` or ``pdf_roots()`` turned this module from 11 passed /
    11 skipped into 6 passed / 16 skipped — five PDF checks downgraded to skips
    and NOTHING red. That is the silent pass this file was rewritten to end, so
    the probe walks the roots itself, with its own case-insensitive suffix match
    (``rglob("*.pdf")`` matches ``.PDF`` on Windows and not on macOS, and both
    machines are in use here).
    """
    override = os.getenv("RAG_PDF_ROOTS", "").strip()
    roots = (
        [Path(part.strip()) for part in override.split(os.pathsep) if part.strip()]
        if override
        else [REPO_ROOT / "client-data", REPO_ROOT / "docs" / "pdf_knowledge"]
    )
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found.extend(
            path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"
        )
    return sorted(found)


def skip_without_a_source_pdf() -> None:
    if not pdfs_on_disk():
        pytest.skip(
            "no source PDF on this machine: client-data/ is gitignored, so a fresh checkout "
            "indexes markdown only. Roots probed: "
            f"{os.getenv('RAG_PDF_ROOTS') or 'client-data/, docs/pdf_knowledge/'}"
        )


def missing_requirement(case: dict[str, object], haystack: str) -> str | None:
    requires = [str(item).lower().replace("\\", "/") for item in (case.get("requires") or [])]
    if not requires:
        return None
    if any(needle in haystack for needle in requires):
        return None
    return (
        f"the corpus on this machine has none of {requires}. Markdown sources ship in the repo; "
        "PDF sources live in gitignored client-data/, so this case runs only where the client "
        "drop is present. Build the corpus with: python tools/build_rag_index.py"
    )


# ---------------------------------------------------------------------------
# The corpus itself
# ---------------------------------------------------------------------------


def test_markdown_corpus_is_present_and_chunked():
    """The markdown half, measured on its own — see the note on the floors."""
    markdown_documents = len(list_markdown_documents())
    markdown_chunks = len(load_markdown_chunks())

    assert markdown_documents >= MARKDOWN_DOCUMENT_FLOOR
    assert markdown_chunks >= MARKDOWN_CHUNK_FLOOR

    # And the reported corpus is a superset of it: markdown never goes missing
    # from the status payload the assistant and the console read.
    status = get_rag_status()
    assert status["documents"] >= markdown_documents
    assert status["chunks"] >= markdown_chunks


def test_a_pdf_on_disk_always_reaches_the_corpus():
    """A PDF that is present but uncollected is a FAILURE, never a skip.

    Every other PDF check in this module is allowed to skip when the client drop
    is absent. This one draws the line between "absent" and "present but lost",
    because those two states are indistinguishable to a guard that asks the
    collector whether the collector found anything.
    """
    skip_without_a_source_pdf()

    pdf = get_rag_status()["pdf"]
    collected = {Path(str(document["path"])).resolve() for document in pdf["documents"]}
    missed = [str(path) for path in pdfs_on_disk() if path.resolve() not in collected]

    assert not missed, (
        f"{len(missed)} PDF(s) on disk never reached the ingester: {missed}. "
        f"Roots the ingester searched: {pdf['roots']}"
    )


def test_expected_section_directories_are_reported_rather_than_assumed():
    """The five section directories in ``RAG_SECTIONS`` are not all on disk.

    ``document_for_path()`` files anything outside them under the catch-all
    ``doc`` kind, so the error-case ranking boost in ``search_keyword_rag`` can
    never fire for a corpus that has no ``error_cases/`` directory. That is
    survivable; leaving it undetectable is not. The status payload has to state
    which of the expected sections are actually populated.
    """
    status = get_rag_status()

    assert set(status["expected_sections"]) == RAG_SECTIONS
    missing = set(status["missing_sections"])
    assert missing <= RAG_SECTIONS
    populated = {section for section in RAG_SECTIONS if status["sections"].get(section)}
    assert missing == RAG_SECTIONS - populated


def test_pdf_corpus_is_page_addressable():
    """Every PDF chunk can be cited by document page, not just by filename."""
    skip_without_a_source_pdf()
    status = get_rag_status()
    pdf = status["pdf"]
    assert pdf["files"], (
        f"a PDF is on disk ({[str(path) for path in pdfs_on_disk()]}) but the ingester "
        f"collected none. Roots it searched: {pdf['roots'] or 'none'}"
    )

    # Structural invariants, not magic numbers: every page that was indexed
    # produced at least one chunk, and extraction reached almost every page.
    assert pdf["chunks"] >= pdf["indexed_pages"] > 0
    assert pdf["indexed_pages"] >= 0.9 * pdf["pages"], (
        "extraction lost more than 10% of the pages: " + repr(pdf["documents"])
    )

    for document in pdf["documents"]:
        if not document["chunks"]:
            continue
        # A page offset means the document printed its own page numbers and the
        # ingester read them; without one, citations fall back to PDF pages and
        # the report has to say so.
        if document["page_offset"] is None:
            assert any("no printed page numbers" in warning for warning in document["warnings"])


def test_a_pdf_hit_carries_the_printed_page_number():
    skip_without_a_source_pdf()
    status = get_rag_status()
    assert status["pdf"]["files"], "a PDF is on disk but the ingester collected none"

    hits = [
        hit
        for hit in search_rag("saturation flow calibration signalised intersection", 8)
        if hit.get("source_kind") == "pdf"
    ]
    if not hits:
        pytest.skip("the PDFs on this machine do not cover saturation flow")

    for hit in hits:
        assert re.fullmatch(r"(p\.[0-9IVXLCDM]+|PDF p\.\d+)", str(hit["page_range"])), hit["page_range"]
        assert str(hit["citation"]).startswith(str(hit["title"]))
        assert f"PDF p.{hit['pdf_page']}" in str(hit["citation"])


# ---------------------------------------------------------------------------
# The calibration doctrine (#50-d, 2026-08-20)
# ---------------------------------------------------------------------------


def doctrine_hits(hits: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        hit
        for hit in hits
        if Path(str(hit.get("path") or "")).name == CALIBRATION_DOCTRINE.name
    ]


def doctrine_is_in_the_vector_index() -> bool:
    """Does the built index CARRY the doctrine file? An identity question.

    Read the distinction carefully, because the first version of this probe got it
    wrong in the direction that costs the most: it asked a RANKING question and
    read the answer as an identity one.

    WHAT WENT WRONG, measured. The probe queried the index with the file's stem,
    ``"calibration-doctrine"``, and skipped the merged-path check when no doctrine
    chunk came back in the top 25. Those are two English tokens, and this corpus
    holds 521 chunks of the Main Roads WA operational-modelling PDF, which is
    English and is *about* calibration -- so the query's whole top-25 was
    ``operational-modelling-v2.pdf`` and the doctrine file scored 0 hits on a
    freshly built index that carried it perfectly well (measured 2026-08-20, index
    of 1556 chunks). The check then skipped with the reason "the index predates
    the file", which was false, while the assertion it was guarding
    (``search_rag`` returning the doctrine file at merged positions #1 and #2)
    would have passed. A guard that turns a green assertion into a skip on a false
    premise is worse than no guard: the suite reports coverage it is not
    delivering, which is the exact silent-pass failure this module was rewritten
    to end.

    WHAT IT ASKS NOW. The file's own H1 -- read from the file, so a retitle
    carries the probe with it, and it is the same string the ingester stores as
    each chunk's ``title``. It is unique to this document in this corpus, so
    ranking cannot crowd it out the way two generic English words could; measured
    on the same 1556-chunk index, it returns the doctrine file at rank #1.

    A rebuilt index answers True. An index built before the doctrine file existed
    answers False, and only then does the caller skip.
    """
    probe = title_for_markdown(CALIBRATION_DOCTRINE)
    assert probe.strip(), (
        f"{CALIBRATION_DOCTRINE.name} has no '# ' heading for the index probe to ask about; "
        "without one this probe would degrade back into a ranking question"
    )
    return bool(doctrine_hits(search_vector_index(probe, 25)))


def test_the_calibration_doctrine_is_a_corpus_document():
    """The file exists and the markdown glob actually reaches it.

    First, because the two retrieval checks below cannot tell "the ranking put it
    fifth" apart from "the retriever never saw the file at all", and those need
    different fixes. ``list_markdown_documents()`` globs ``docs/**/*.md``, so a
    doctrine file written anywhere else -- or renamed -- silently stops being
    something the assistant can cite.
    """
    assert CALIBRATION_DOCTRINE.is_file(), (
        f"{CALIBRATION_DOCTRINE} is missing. #50-d evicted the /calibration screen's doctrine "
        "into this file; without it the sentences have no home the assistant can read."
    )

    names = {document.path.name for document in list_markdown_documents()}
    assert CALIBRATION_DOCTRINE.name in names, (
        f"{CALIBRATION_DOCTRINE.name} is on disk but outside the corpus glob "
        f"(docs/**/*.md, rooted at {REPO_ROOT / 'docs'})."
    )

    # And it is chunked, not merely listed: a document that produces no chunk is
    # a document no query can ever return.
    chunks = [
        chunk
        for chunk in load_markdown_chunks()
        if Path(str(chunk.get("path") or "")).name == CALIBRATION_DOCTRINE.name
    ]
    assert chunks, "the doctrine file reached the corpus but produced no chunks"


def test_a_doctrine_query_retrieves_the_doctrine_file_without_any_index():
    """FIRM, on the path that needs nothing built.

    ``search_keyword_rag`` live-scans the markdown under ``docs/`` on every call
    -- no index, no cache, no build step -- so this check has no environmental
    dependency at all and is allowed none. It is asserted twice: directly, and
    through ``search_rag`` with the vector half stubbed out, which is the same
    "keyword-only" path the parametrised cases below exercise and is what the
    assistant falls back to on a machine where nobody has run
    ``tools/build_rag_index.py``.
    """
    assert doctrine_hits(search_keyword_rag(CALIBRATION_DOCTRINE_QUERY, 5)), (
        f"a doctrine query ({CALIBRATION_DOCTRINE_QUERY!r}) did not retrieve "
        f"{CALIBRATION_DOCTRINE.name} on the index-free keyword path"
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rag_documents, "search_vector_index", lambda query, limit: [])
        hits = search_rag(CALIBRATION_DOCTRINE_QUERY, 5)
    assert doctrine_hits(hits), (
        "search_rag() with no vector index did not surface the doctrine file: "
        f"{[str(hit.get('path')) for hit in hits]}"
    )


def test_a_doctrine_query_retrieves_the_doctrine_file_on_the_merged_path():
    """The merged path, and the ONE dependency this module is honest about.

    ``search_rag`` concatenates ``[*vector_hits, *keyword_hits]`` and truncates at
    the limit, so a ready vector index starves the keyword half completely (the
    same mechanic the ``mrwa_linsig_tables`` case above carries a note about).
    That makes this check a question about the INDEX, not about the corpus: a
    file written after the last ``python tools/build_rag_index.py`` is invisible
    to the vector half until somebody rebuilds, and the rebuild writes under
    ``runtime/``, which is not this test's business.

    So the dependency is probed rather than assumed, and the skip names the exact
    command that turns this into a pass -- never a silent one. When the index is
    absent entirely, the merged path IS the keyword path and this asserts firmly.
    """
    vector_ready = bool(get_rag_status()["vector"].get("ready"))
    if vector_ready and not doctrine_is_in_the_vector_index():
        pytest.skip(
            f"the vector index on this machine does not carry {CALIBRATION_DOCTRINE.name} "
            "(it predates the file). search_rag() fills its limit from the vector half first, "
            "so the keyword hit is truncated away until the index is rebuilt: "
            "python tools/build_rag_index.py"
        )

    hits = search_rag(CALIBRATION_DOCTRINE_QUERY, 5)
    assert doctrine_hits(hits), (
        "the merged path did not surface the doctrine file: "
        f"{[str(hit.get('path')) for hit in hits]}"
    )


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[str(case["id"]) for case in CASES])
def test_rag_retrieval_case(case: dict[str, object]):
    """Each case has to hold on BOTH retrieval paths.

    The verdict must not depend on whether ``python tools/build_rag_index.py``
    has been run on this machine. Measured 2026-08-18: with the index unbuilt
    every case passed, and building it — the command ``get_vector_status()``
    tells developers to run — turned ``mrwa_linsig_tables`` red, because the 521
    new PDF chunks filled the entire top-5. ``search_rag()`` concatenates
    ``[*vector_hits, *keyword_hits]`` and truncates at the limit, so a ready
    vector index starves the keyword half completely; a case tuned against one
    path says nothing about the other.
    """
    reason = missing_requirement(case, corpus_haystack())
    if reason:
        pytest.skip(f"{case['id']}: {reason}")

    expected = [str(item).lower().replace("\\", "/") for item in case["must_match_any"]]  # type: ignore[index]
    limit = int(case.get("top_k") or 5)

    for path_name, without_vector in (("merged", False), ("keyword-only", True)):
        with pytest.MonkeyPatch.context() as mp:
            if without_vector:
                mp.setattr(rag_documents, "search_vector_index", lambda query, limit: [])
            hits = search_rag(str(case["query"]), limit)

        haystacks = [searchable_text(hit) for hit in hits]
        matched = any(any(needle in haystack for haystack in haystacks) for needle in expected)
        assert matched, (
            f"{case['id']} on the {path_name} path: expected one of {expected}, got "
            f"{[str(hit.get('title') or hit.get('path')) for hit in hits]}"
        )
