"""The PDF half of the RAG corpus, exercised against PDFs built here.

Every fixture in this file is assembled in-process, so the whole module runs on
a machine that has never seen the client drop. That is deliberate: ``client-data/``
is gitignored, and a test that needed it would be a test that quietly stopped
running everywhere else.
"""
from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path

import pytest

from services import rag_pdf


# ---------------------------------------------------------------------------
# A PDF, built here
# ---------------------------------------------------------------------------


def synthetic_pdf(pages: list[list[tuple[str, float, float]]], *, width: float = 595.0, height: float = 842.0) -> bytes:
    """A multi-page PDF placing each ``(text, x, y)`` with base-14 Helvetica.

    Hand-assembled for the same reason ``tests/test_genesis_phase_diagram.py``
    does it: what these tests need to control is exactly where each string lands
    on the page, because the ingester's whole job is reading position-dependent
    structure (running headers at the top, page numbers at the bottom, table
    columns in between). No PDF-authoring dependency is worth adding for that.
    """
    objects: list[bytes] = [b"", b""]  # 1 = catalog, 2 = pages, filled in below
    page_ids: list[int] = []
    font_id = 3
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for items in pages:
        stream = "\n".join(
            f"BT /F1 10 Tf {x} {y} Td ({_escape(text)}) Tj ET" for text, x, y in items
        ).encode("latin-1")
        content_id = len(objects) + 1
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = len(objects) + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        page_ids.append(page_id)

    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


HEADER = "Operational Modelling Guidelines - Version 2.0"
FRONT_PAGES = 3  # a cover with no number, then two roman-numbered contents pages


def guideline_pdf(body_pages: int = 12) -> bytes:
    """A document shaped like the MRWA guidelines: running header, a cover, roman
    front matter, then body pages that print "Page N of M"."""
    pages: list[list[tuple[str, float, float]]] = []
    pages.append([("Operational Modelling Guidelines", 60, 700), ("Version No. 2.0", 60, 660)])
    for roman in ("I", "II"):
        pages.append(
            [
                (HEADER, 60, 800),
                ("Contents", 60, 740),
                (f"Section listing {roman}: demand, calibration, validation, reporting.", 60, 700),
                ("Each entry names a numbered section of the guidelines.", 60, 680),
                (f"Page {roman}", 460, 40),
            ]
        )
    for number in range(1, body_pages + 1):
        pages.append(
            [
                (HEADER, 60, 800),
                (f"{number}.1 Section heading {number}", 60, 740),
                (f"Body prose for section {number}. " * 6, 60, 700),
                (f"Page {number} of {body_pages}", 460, 40),
            ]
        )
    return synthetic_pdf(pages)


def table_pdf() -> bytes:
    """One body page carrying a caption, a header row and six data rows, laid out
    as real columns -- the shape MRWA Table 5-6 has."""
    rows = [
        ("1,630", "2.75", "3.75"),
        ("1,780", "2.50", "3.50"),
        ("1,950", "2.25", "3.25"),
        ("2,050", "2.00", "3.00"),
        ("2,100", "1.75", "2.75"),
        ("2,200", "1.50", "2.50"),
    ]
    items: list[tuple[str, float, float]] = [
        (HEADER, 60, 800),
        ("Table 5-6: Recommended car following model parameters", 60, 700),
        ("Average saturation flow", 60, 670),
        ("Additive part of safety distance", 240, 670),
        ("Multiplic. part of safety distance", 420, 670),
    ]
    for index, (flow, additive, multiplicative) in enumerate(rows):
        y = 640 - index * 24
        items.extend([(flow, 60, y), (additive, 240, y), (multiplicative, 420, y)])
    items.append(("Page 187 of 271", 460, 40))
    return synthetic_pdf([[(HEADER, 60, 800), ("cover", 60, 700)], items])


@pytest.fixture()
def pdf_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated PDF root plus an isolated ingest cache."""
    root = tmp_path / "pdfs"
    root.mkdir()
    monkeypatch.setenv("RAG_PDF_ROOTS", str(root))
    monkeypatch.setenv("RAG_PDF_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("RAG_PDF_MANIFEST", raising=False)
    rag_pdf.reset_pdf_corpus_cache()
    yield root
    rag_pdf.reset_pdf_corpus_cache()


# ---------------------------------------------------------------------------
# Page numbering -- the property that makes a hit citable
# ---------------------------------------------------------------------------


def test_printed_page_labels_are_read_off_the_page():
    assert rag_pdf.page_label("      Page 187 of 271") == ("187", True)
    assert rag_pdf.page_label("Page XVII") == ("XVII", False)
    # Prose that merely mentions a page is not a page label.
    assert rag_pdf.page_label("See Page 12 for the calibration targets") is None
    assert rag_pdf.page_label("5.2.5 Driving Behaviours") is None


def test_page_offset_is_derived_from_the_labels_the_document_printed():
    labels = {19: ("1", True), 20: ("2", True), 205: ("187", True), 2: ("I", False)}
    assert rag_pdf.resolve_page_offset(labels, None) == 18
    # A manifest entry wins, for a document that prints nothing usable.
    assert rag_pdf.resolve_page_offset(labels, 4) == 4
    assert rag_pdf.resolve_page_offset({2: ("I", False)}, None) is None


def test_ingest_maps_pdf_pages_to_the_printed_document_pages(pdf_root: Path):
    path = pdf_root / "guidelines.pdf"
    path.write_bytes(guideline_pdf())

    chunks, report = rag_pdf.ingest_pdf(path)

    assert report.page_count == FRONT_PAGES + 12
    assert report.page_offset == FRONT_PAGES  # body page 1 sits at PDF page 4
    # The running header is what names the document; nothing is hardcoded.
    assert report.title == HEADER

    by_pdf_page = {chunk["pdf_page"]: chunk for chunk in chunks}
    body_first = by_pdf_page[FRONT_PAGES + 1]
    assert body_first["doc_page"] == "1"
    assert body_first["doc_page_inferred"] is False
    assert body_first["page_range"] == "p.1"
    assert body_first["citation"] == f"{HEADER}, p.1 (PDF p.4)"
    # Roman front matter keeps its own printed label rather than being forced
    # onto the arabic offset.
    assert by_pdf_page[2]["doc_page"] == "I"
    assert by_pdf_page[2]["page_range"] == "p.I"


def test_the_running_header_and_page_number_are_stripped_from_the_indexed_text(pdf_root: Path):
    path = pdf_root / "guidelines.pdf"
    path.write_bytes(guideline_pdf())

    chunks, _ = rag_pdf.ingest_pdf(path)
    body = [chunk for chunk in chunks if chunk["doc_page"] == "5"]
    assert body, "the body page should have produced a chunk"
    text = body[0]["text"]
    assert "Section heading 5" in text
    # Repeated on every page: as chunk text it is 289 copies of noise that drags
    # every TF-IDF score toward the same document. It lives in the title instead.
    assert HEADER not in text
    assert "Page 5 of 12" not in text


def test_a_document_that_prints_no_page_numbers_says_so_instead_of_guessing(pdf_root: Path):
    path = pdf_root / "unnumbered.pdf"
    path.write_bytes(
        synthetic_pdf(
            [
                [
                    ("A technical note on saturation flow calibration and gap acceptance.", 60, 700),
                    ("It prints no page number anywhere on the page.", 60, 680),
                ]
            ]
            * 3
        )
    )

    chunks, report = rag_pdf.ingest_pdf(path)

    assert report.page_offset is None
    assert report.labelled_pages == 0
    assert any("no printed page numbers" in warning for warning in report.warnings)
    assert chunks[0]["doc_page"] == ""
    assert chunks[0]["page_range"] == "PDF p.1"
    assert "no printed page number" in chunks[0]["citation"]


# ---------------------------------------------------------------------------
# Extraction quality
# ---------------------------------------------------------------------------


def test_a_table_keeps_its_caption_header_and_every_row_in_one_chunk(pdf_root: Path):
    """The regression this chunker was rewritten for.

    Fill-to-budget chunking split the real MRWA Table 5-6 between its header row
    and its second data row, so a hit on the lower half returned six numbers
    with nothing saying which parameter they were.
    """
    path = pdf_root / "table.pdf"
    path.write_bytes(table_pdf())

    chunks, _ = rag_pdf.ingest_pdf(path)
    table_chunks = [chunk for chunk in chunks if "Table 5-6" in chunk["text"]]
    assert len(table_chunks) == 1
    text = table_chunks[0]["text"]
    for cell in ("Average saturation flow", "1,630", "1,780", "1,950", "2,050", "2,100", "2,200"):
        assert cell in text, f"{cell!r} left the table's chunk"
    assert table_chunks[0]["page_range"] == "p.187"


def test_column_alignment_survives_extraction(pdf_root: Path):
    """Layout-mode extraction, and a normalizer that does not undo it.

    ``rag_documents.normalize_text`` collapses runs of spaces; running a table
    through it turns three columns into one unreadable line, which is why this
    module has its own normalizer.
    """
    path = pdf_root / "table.pdf"
    path.write_bytes(table_pdf())

    chunks, _ = rag_pdf.ingest_pdf(path)
    text = next(chunk["text"] for chunk in chunks if "1,630" in chunk["text"])
    row = next(line for line in text.splitlines() if "1,630" in line)
    assert "2.75" in row and "3.75" in row, "a table row must stay on one line"
    assert "  " in row, "the column gap must survive as whitespace"


def test_a_page_with_no_text_layer_is_reported_rather_than_dropped_silently(pdf_root: Path):
    path = pdf_root / "mixed.pdf"
    path.write_bytes(
        synthetic_pdf(
            [
                [("Readable prose about model calibration and validation targets. " * 3, 60, 700)],
                [(".", 60, 700)],  # a full-page figure: no usable text layer
            ]
        )
    )

    _, report = rag_pdf.ingest_pdf(path)

    assert report.unreadable_pages == [2]
    assert report.indexed_pages == 1
    assert any("no usable text layer" in warning for warning in report.warnings)


def test_a_file_that_is_not_a_pdf_is_refused_with_a_reason(pdf_root: Path):
    path = pdf_root / "notes.pdf"
    path.write_bytes(b"this is not a PDF at all")

    chunks, report = rag_pdf.ingest_pdf(path)

    assert chunks == []
    assert report.warnings and "could not open the PDF" in report.warnings[0]


def test_pypdf_missing_is_named_not_swallowed(pdf_root: Path, monkeypatch: pytest.MonkeyPatch):
    """The missing-dependency path: no chunks, and a stated reason."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("no pypdf here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    path = pdf_root / "guidelines.pdf"
    path.write_bytes(guideline_pdf())

    chunks, report = rag_pdf.ingest_pdf(path)

    assert chunks == []
    assert any("pypdf is not installed" in warning for warning in report.warnings)


# ---------------------------------------------------------------------------
# Corpus wiring
# ---------------------------------------------------------------------------


def test_no_pdf_on_this_machine_is_an_empty_corpus_not_an_error(pdf_root: Path):
    status = rag_pdf.pdf_corpus_status()

    assert status["files"] == 0
    assert status["chunks"] == 0
    assert status["warnings"] == []


def test_the_corpus_status_carries_every_documents_warnings(pdf_root: Path):
    (pdf_root / "broken.pdf").write_bytes(b"not a pdf")
    (pdf_root / "guidelines.pdf").write_bytes(guideline_pdf())
    rag_pdf.reset_pdf_corpus_cache()

    status = rag_pdf.pdf_corpus_status()

    assert status["files"] == 2
    assert status["chunks"] > 0
    assert any(warning.startswith("broken.pdf:") for warning in status["warnings"])


def test_a_manifest_can_override_the_title_and_the_offset(pdf_root: Path):
    path = pdf_root / "guidelines.pdf"
    path.write_bytes(guideline_pdf())
    (pdf_root / "rag-manifest.json").write_text(
        '{"guidelines.pdf": {"title": "MRWA Operational Modelling Guidelines v2.0"}}',
        encoding="utf-8",
    )
    rag_pdf.reset_pdf_corpus_cache()

    chunks, report = rag_pdf.ingest_pdf(path)

    assert report.title == "MRWA Operational Modelling Guidelines v2.0"
    assert chunks[0]["citation"].startswith("MRWA Operational Modelling Guidelines v2.0,")


def test_the_disk_cache_returns_the_same_chunks_and_rebuilds_when_the_pdf_changes(pdf_root: Path):
    path = pdf_root / "guidelines.pdf"
    path.write_bytes(guideline_pdf(body_pages=6))

    first, first_report = rag_pdf.ingest_pdf_cached(path)
    cached, cached_report = rag_pdf.ingest_pdf_cached(path)
    assert cached == first
    assert cached_report.chunks == first_report.chunks
    assert list(rag_pdf.cache_dir().glob("*.json")), "the cache should have been written"

    path.write_bytes(guideline_pdf(body_pages=9))
    rebuilt, rebuilt_report = rag_pdf.ingest_pdf_cached(path)
    assert rebuilt_report.page_count == FRONT_PAGES + 9
    assert len(rebuilt) > len(first)


def one_page_pdf(word: str) -> bytes:
    """A one-page document whose byte length depends only on the word's length."""
    return synthetic_pdf(
        [
            [
                (HEADER, 60, 800),
                (f"{word} paragraph on saturation flow and gap acceptance targets.", 60, 700),
                (f"{word} second paragraph naming the car following parameters.", 60, 680),
                ("Page 1 of 1", 460, 40),
            ]
        ]
    )


def test_a_same_size_replacement_is_not_served_from_the_cache(pdf_root: Path):
    """Size + mtime is not a content key, so the cache cannot be keyed on it.

    ``st_mtime_ns`` is nanosecond-typed and roughly 15 ms-resolved: two
    back-to-back writes of equal length were measured on this box sharing a
    byte-identical ``st_mtime_ns``, and archive extraction, ``cp -p`` and
    ``rsync -t`` all restore a stored timestamp deliberately. Before the content
    hash went into the key, this test read the SUPERSEDED document back out of
    the cache -- with the superseded document's page numbers attached, which is
    a citation that lies.
    """
    path = pdf_root / "guidelines.pdf"
    before, after = one_page_pdf("alpha"), one_page_pdf("omega")
    assert len(before) == len(after), "the two revisions must be the same size for this to bite"

    path.write_bytes(before)
    stamp = path.stat()
    first, _ = rag_pdf.ingest_pdf_cached(path)
    assert "alpha" in first[0]["text"]

    path.write_bytes(after)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert path.stat().st_size == stamp.st_size
    assert path.stat().st_mtime_ns == stamp.st_mtime_ns

    second, _ = rag_pdf.ingest_pdf_cached(path)
    assert "omega" in second[0]["text"], "the cache served the document that was replaced"
    assert "alpha" not in second[0]["text"]


def test_a_pdf_replaced_during_ingest_is_not_left_memoised(pdf_root: Path):
    """The in-process memo must not stamp old chunks with the new file's key.

    Extraction of the real client guideline takes ~7 s. Recording the corpus
    fingerprint AFTER that window meant a PDF replaced while it was being read
    got its new fingerprint written against the old document's chunks, and every
    later call hit the memo and returned the superseded document — for the life
    of the process, with nothing able to detect it.
    """
    path = pdf_root / "guidelines.pdf"
    path.write_bytes(guideline_pdf(body_pages=6))

    real_ingest = rag_pdf.ingest_pdf_cached

    def ingest_then_replace(target: Path):
        result = real_ingest(target)
        target.write_bytes(guideline_pdf(body_pages=11))
        return result

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rag_pdf, "ingest_pdf_cached", ingest_then_replace)
        _, during = rag_pdf.load_pdf_corpus()
    assert during[0].page_count == FRONT_PAGES + 6

    _, after = rag_pdf.load_pdf_corpus()
    assert after[0].page_count == FRONT_PAGES + 11, (
        "the memo kept the superseded document under the replacement's fingerprint"
    )


def test_the_page_number_survives_the_vector_index_round_trip(pdf_root: Path):
    """Chroma metadata takes scalars only, so the citation fields have to be
    listed explicitly. Dropping them there would leave every VECTOR hit citing a
    filename with no page, while keyword hits cited a page -- the worst possible
    version, because it looks like it works."""
    from services.rag_vector_store import metadata_for_chunk

    path = pdf_root / "table.pdf"
    path.write_bytes(table_pdf())
    chunk = next(chunk for chunk in rag_pdf.ingest_pdf(path)[0] if "Table 5-6" in chunk["text"])

    metadata = metadata_for_chunk(chunk)

    assert metadata["page_range"] == "p.187"
    assert metadata["doc_page"] == "187"
    assert metadata["source_kind"] == "pdf"
    assert metadata["citation"] == chunk["citation"]
    assert all(isinstance(value, str) for value in metadata.values())


def test_a_pdf_hit_reaches_the_assistants_source_list_with_its_page(pdf_root: Path):
    from services.assistant_router import source_from_rag_item

    path = pdf_root / "table.pdf"
    path.write_bytes(table_pdf())
    chunk = next(chunk for chunk in rag_pdf.ingest_pdf(path)[0] if "Table 5-6" in chunk["text"])

    source = source_from_rag_item(chunk)

    assert source["page_range"] == "p.187"
    assert source["citation_label"] == chunk["citation"]
    assert "PDF p.2" in source["citation_label"]


def test_a_markdown_hit_still_uses_the_filename_page_convention():
    """The legacy `pages_N_M.md` naming keeps working; PDF metadata is additive."""
    from services.assistant_router import source_from_rag_item

    source = source_from_rag_item(
        {
            "kind": "pdf_knowledge",
            "title": "Operational Modelling",
            "path": "docs/pdf_knowledge/operational_modelling/demand/pages_12_14.md",
            "chunk_id": "pages_12_14.md:0",
            "text": "growth factors",
        }
    )

    assert source["page_range"] == "pages 12-14"
    assert "pages 12-14" in source["citation_label"]


def test_the_index_builder_prints_a_glyph_the_console_cannot_draw(monkeypatch: pytest.MonkeyPatch):
    """``tools/build_rag_index.py --query ...`` exited 2 on the Windows dev box.

    The MRWA guidelines set their bullets in Symbol font, so pypdf returns
    U+F0B7; the Windows console encodes stdout as cp1252; printing a retrieved
    snippet raised UnicodeEncodeError and killed the run AFTER the index had
    been built. ``get_vector_status()["build_command"]`` advertises this script,
    so it has to survive its own corpus.
    """
    import importlib.util
    import io
    import sys

    script = Path(__file__).resolve().parent.parent / "tools" / "build_rag_index.py"
    spec = importlib.util.spec_from_file_location("build_rag_index_under_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
    monkeypatch.setattr(sys, "stdout", console)
    monkeypatch.setattr(sys, "stderr", console)
    module.make_output_lossless()

    print("MRWA bullet  in a retrieved snippet")  # raised before the fix
    console.flush()
    assert b"MRWA bullet" in console.buffer.getvalue()


def test_chunks_are_attributable_to_exactly_one_page(pdf_root: Path):
    """No chunk spans pages, which is what makes "p.187" a citation and not an
    approximation."""
    path = pdf_root / "guidelines.pdf"
    path.write_bytes(guideline_pdf())

    chunks, report = rag_pdf.ingest_pdf(path)

    assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)
    assert all(isinstance(chunk["pdf_page"], int) for chunk in chunks)
    # One chunk per page here; the invariant that generalises is that the page
    # count of the chunks never exceeds the pages that were indexed.
    assert len({chunk["pdf_page"] for chunk in chunks}) == report.indexed_pages
