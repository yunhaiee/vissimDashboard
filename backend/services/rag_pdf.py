"""PDF ingestion for the RAG corpus: page-true chunks the assistant can cite.

Why this module exists
----------------------
``rag_documents.list_markdown_documents()`` globbed ``docs/**/*.md`` and nothing
else, so every PDF the client handed us was invisible to retrieval. The most
expensive consequence was concrete: asked about Main Roads WA's Operational
Modelling Guidelines -- TK's actual authority document -- the assistant could
only reach the handful of paragraphs someone had retyped into
``docs/product/reference/mrwa-*-extract.md``. The guidelines themselves, 289
pages of them, had never been indexed once.

The design commitments, each of which is a measurement rather than a taste:

* **Layout-mode extraction.** ``extract_text(extraction_mode="layout")`` is the
  same choice ``services/genesis/signals_daejeon.read_sheet_text()`` made, for
  the same reason: these documents are mostly tables. Measured on the MRWA
  guidelines, Table 5-6 (average saturation flow -> car-following parameters)
  comes out of layout mode with its header cells sitting above their columns and
  each row on one line; plain reading-order extraction returns the same numbers
  with the three column headers stacked into six orphan lines, so a reader
  cannot tell which value belongs to which parameter. Layout mode costs ~40%
  more characters (alignment padding) and ~40% more time. Both are worth it.

* **A chunk never spans a page.** Every chunk is attributable to exactly one
  printed page, which is what makes "p.187" a citation instead of an estimate.
  Pages longer than the budget split at paragraph boundaries with overlap;
  consecutive short pages are NOT merged.

* **The page mapping is measured, not declared.** These documents print their
  own page numbers ("Page 187 of 271", "Page XVII"), so the ingester reads the
  printed label off the page and derives the PDF-page -> document-page offset
  from the labels it found. For the MRWA guidelines that yields offset 18 across
  all 271 numbered pages -- the same number a human would have hardcoded, but
  now verified per document and reported when it disagrees with itself.

* **Quality loss is named, never swallowed.** Rotated text (pypdf drops it),
  pages with no usable text layer (scans that would need OCR), and pages with no
  printed label are counted and surfaced in the ingest report and in
  ``get_rag_status()``.

Source PDFs live outside git on purpose: ``client-data/`` is gitignored because
it is the client's confidential data. A checkout without those files therefore
indexes markdown only -- that is the normal, supported state, and the ingest
report says so rather than pretending the corpus is complete.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha1
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any

from config import REPO_ROOT

logger = logging.getLogger(__name__)

PDF_SECTION = "pdf_knowledge"

# Layout-mode text carries alignment padding, so a PDF chunk holds less prose
# per character than a markdown chunk. 1800 is chosen against the measured page
# size distribution of the MRWA guidelines (median 2.4k chars/page, p75 3.2k):
# it splits a typical page into two paragraph-aligned chunks and leaves short
# pages whole, while keeping every chunk inside a single page.
PDF_CHUNK_TARGET_CHARS = 1800
PDF_CHUNK_OVERLAP_CHARS = 200

# A page shorter than this is emitted whole rather than split to the target.
# Chosen against the same distribution: it leaves the median page (2.4k) and
# roughly two thirds of all pages in one piece, which is what keeps a table and
# its header row in the same chunk. See :func:`split_page_text`.
PDF_CHUNK_MAX_CHARS = 2800

# A page with less text than this has no usable text layer -- a full-page
# figure, a scan, a divider. It is counted and reported, not indexed.
MIN_PAGE_CHARS = 60

# A chunk shorter than this is a sliver left over from a split, not a passage.
# It gets folded back into its neighbour rather than competing for a retrieval
# slot with no context attached.
MIN_CHUNK_CHARS = 160

# Bump when the extraction or chunking changes shape, so stale disk caches are
# rebuilt instead of silently serving the old chunking.
CACHE_VERSION = 2

# "Page 187 of 271" (body) and "Page XVII" (front matter). Both are matched
# against a stripped whole line: a footer is a line of its own, and anchoring
# keeps prose that happens to contain the word "page" from being mistaken for a
# page label. Documents that print a bare number with no "Page" prefix are NOT
# handled -- see the module docstring's note on named limitations.
ARABIC_PAGE_LABEL = re.compile(r"^Page\s+(\d+)\s+of\s+\d+$", re.IGNORECASE)
ROMAN_PAGE_LABEL = re.compile(r"^Page\s+([IVXLCDM]+)$")

# A running header/footer is a line that repeats on most pages AND sits at the
# very top or very bottom of its page. Both halves matter: repetition alone
# would delete a table cell that legitimately recurs, and position alone would
# delete the first line of every section.
RUNNING_LINE_PAGE_RATIO = 0.6
RUNNING_LINE_MAX_CHARS = 120
RUNNING_LINE_EDGE = 3


@dataclass(frozen=True)
class PdfPage:
    """One page's cleaned text plus what the page says its own number is."""

    pdf_page: int
    doc_page: str | None
    doc_page_inferred: bool
    text: str


@dataclass
class PdfIngestReport:
    """What the ingester saw, including everything it could not read."""

    path: str
    title: str
    page_count: int = 0
    indexed_pages: int = 0
    chunks: int = 0
    page_offset: int | None = None
    labelled_pages: int = 0
    rotated_pages: list[int] = field(default_factory=list)
    unreadable_pages: list[int] = field(default_factory=list)
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "page_count": self.page_count,
            "indexed_pages": self.indexed_pages,
            "chunks": self.chunks,
            "page_offset": self.page_offset,
            "labelled_pages": self.labelled_pages,
            "rotated_pages": list(self.rotated_pages),
            "unreadable_pages": list(self.unreadable_pages),
            "seconds": self.seconds,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Where the PDFs and their optional manifests live
# ---------------------------------------------------------------------------


def pdf_roots() -> list[Path]:
    """Directories scanned for PDFs, in order.

    ``client-data/`` is the client drop (gitignored). ``docs/pdf_knowledge/`` is
    the section directory the retrieval code has always advertised; it is
    scanned when it exists so a checkout can carry its own PDFs without touching
    the client drop.
    """
    override = os.getenv("RAG_PDF_ROOTS", "").strip()
    if override:
        candidates = [Path(part.strip()) for part in override.split(os.pathsep) if part.strip()]
    else:
        candidates = [REPO_ROOT / "client-data", REPO_ROOT / "docs" / PDF_SECTION]

    roots: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def list_pdf_files() -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for root in pdf_roots():
        for path in sorted(root.rglob("*.pdf")):
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files


def load_manifest(root: Path) -> dict[str, dict[str, Any]]:
    """Optional per-directory overrides, keyed by PDF filename.

    ``{"operational-modelling-v2.pdf": {"title": ..., "page_offset": 18}}``.
    Everything in it is optional; with no manifest at all the title and the page
    offset are read off the document itself.
    """
    override = os.getenv("RAG_PDF_MANIFEST", "").strip()
    path = Path(override) if override else root / "rag-manifest.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("RAG PDF manifest %s is unreadable: %s", path, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key).lower(): value for key, value in payload.items() if isinstance(value, dict)}


def manifest_entry_for(path: Path) -> dict[str, Any]:
    for root in pdf_roots():
        try:
            path.relative_to(root)
        except ValueError:
            continue
        entry = load_manifest(root).get(path.name.lower())
        if entry:
            return entry
    return {}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class _RotationWatcher(logging.Handler):
    """Catches pypdf's "Rotated text discovered. Output will be incomplete."

    pypdf reports rotated text through the logging module and then silently
    returns a partial page. Left alone that is exactly the kind of quiet data
    loss this codebase refuses to ship, so the notice is captured per page and
    reported as a named limitation.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.hits = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken log record must not break ingest
            return
        if "rotated" in message.lower():
            self.hits += 1


def extract_pages(path: Path, report: PdfIngestReport) -> list[tuple[int, str]]:
    """Raw layout-mode text per page, 1-based, with rotation losses recorded."""
    try:
        from pypdf import PdfReader
    except ImportError:
        report.warnings.append(
            "pypdf is not installed, so no PDF was indexed (backend/requirements.txt pins pypdf)."
        )
        return []

    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - any reader failure is an unreadable input
        report.warnings.append(f"could not open the PDF: {type(exc).__name__}: {exc}")
        return []

    watcher = _RotationWatcher()
    pypdf_logger = logging.getLogger("pypdf")
    previous_level = pypdf_logger.level
    pypdf_logger.addHandler(watcher)
    if previous_level > logging.WARNING or previous_level == logging.NOTSET:
        pypdf_logger.setLevel(logging.WARNING)

    pages: list[tuple[int, str]] = []
    try:
        report.page_count = len(reader.pages)
        for index, page in enumerate(reader.pages, start=1):
            before = watcher.hits
            try:
                text = page.extract_text(extraction_mode="layout") or ""
            except Exception as exc:  # noqa: BLE001 - one bad page must not lose the document
                report.warnings.append(f"page {index}: {type(exc).__name__}: {exc}")
                text = ""
            if watcher.hits > before:
                report.rotated_pages.append(index)
            pages.append((index, text))
    finally:
        pypdf_logger.removeHandler(watcher)
        pypdf_logger.setLevel(previous_level)

    if report.rotated_pages:
        report.warnings.append(
            "rotated text on PDF page(s) "
            + ", ".join(str(page) for page in report.rotated_pages)
            + " is dropped by pypdf; those pages are indexed incomplete."
        )
    return pages


def normalize_page_text(text: str) -> str:
    """Trim without disturbing the horizontal alignment layout mode bought us.

    Deliberately unlike ``rag_documents.normalize_text``, which collapses runs of
    spaces: doing that here would flatten every table back into the unreadable
    reading-order form that layout mode exists to avoid.
    """
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def page_label(line: str) -> tuple[str, bool] | None:
    """``("187", True)`` for an arabic label, ``("XVII", False)`` for roman."""
    stripped = line.strip()
    match = ARABIC_PAGE_LABEL.match(stripped)
    if match:
        return match.group(1), True
    match = ROMAN_PAGE_LABEL.match(stripped)
    if match:
        return match.group(1), False
    return None


def find_running_lines(pages: list[tuple[int, str]]) -> set[str]:
    """Lines that repeat at the top or bottom of most pages."""
    if len(pages) < 8:
        return set()

    counts: dict[str, int] = {}
    for _, text in pages:
        edges = _edge_lines(text)
        for line in {line for line in edges if 0 < len(line) <= RUNNING_LINE_MAX_CHARS}:
            counts[line] = counts.get(line, 0) + 1

    threshold = max(2, int(len(pages) * RUNNING_LINE_PAGE_RATIO))
    return {line for line, count in counts.items() if count >= threshold}


def _edge_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.split("\n")]
    filled = [line for line in lines if line]
    return [*filled[:RUNNING_LINE_EDGE], *filled[-RUNNING_LINE_EDGE:]]


def clean_page(text: str, running: set[str]) -> tuple[str, tuple[str, bool] | None]:
    """Strip the running header/footer and pull the printed page label out."""
    lines = text.split("\n")
    filled_positions = [index for index, line in enumerate(lines) if line.strip()]
    head = set(filled_positions[:RUNNING_LINE_EDGE])
    tail = set(filled_positions[-RUNNING_LINE_EDGE:])
    edge = head | tail

    label: tuple[str, bool] | None = None
    kept: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        found = page_label(line) if index in edge else None
        if found and label is None:
            label = found
            continue
        if found:
            continue
        if index in edge and stripped in running:
            continue
        kept.append(line)
    return normalize_page_text("\n".join(kept)), label


def resolve_page_offset(labels: dict[int, tuple[str, bool]], override: int | None) -> int | None:
    """PDF page minus printed page, taken from the labels the document printed.

    Returns the offset the pages agree on. Disagreement means the document
    restarts its numbering somewhere; the majority offset is returned and the
    caller records a warning rather than pretending the mapping is exact.
    """
    if override is not None:
        return override
    deltas: dict[int, int] = {}
    for pdf_page, (value, is_arabic) in labels.items():
        if not is_arabic:
            continue
        deltas[pdf_page - int(value)] = deltas.get(pdf_page - int(value), 0) + 1
    if not deltas:
        return None
    return max(deltas.items(), key=lambda item: (item[1], -abs(item[0])))[0]


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def split_page_text(text: str) -> list[str]:
    """Split one page into chunks, keeping tables whole wherever possible.

    Two rules, both of which exist because of a measured failure. Greedy
    fill-to-budget split MRWA Table 5-6 (average saturation flow -> car
    following parameters) between its header row and its second data row, so a
    hit on the lower half returned six numbers with nothing saying what they
    were. Layout-mode extraction puts a blank line between table rows, so a
    table is not one paragraph the splitter can keep together -- it is a run of
    them, and any budget can land inside it.

    So: a page under :data:`PDF_CHUNK_MAX_CHARS` is never split at all (the
    measured median page is 2.4k chars, which puts most pages -- and most tables
    -- in one piece), and a page over it is split into BALANCED parts rather
    than one full chunk plus a runt.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= PDF_CHUNK_MAX_CHARS:
        return [stripped]

    blocks = [block for block in re.split(r"\n\s*\n", stripped) if block.strip()]
    if not blocks:
        return []

    parts = max(2, -(-len(stripped) // PDF_CHUNK_TARGET_CHARS))
    budget = max(PDF_CHUNK_TARGET_CHARS // 2, -(-len(stripped) // parts))

    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > budget:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_block(block, budget))
            continue
        if current and len(current) + len(block) + 2 > budget:
            chunks.append(current.strip())
            current = current[-PDF_CHUNK_OVERLAP_CHARS:].strip()
        current = f"{current}\n\n{block}".strip() if current else block
    if current.strip():
        chunks.append(current.strip())
    return _merge_runts([chunk for chunk in chunks if chunk])


def _merge_runts(chunks: list[str]) -> list[str]:
    """Fold a sliver back into its neighbour.

    A balanced split can still leave a trailing fragment of a few dozen
    characters -- a stray figure caption, the last line of a table. On its own
    that is a chunk with no context that can win a retrieval slot from a real
    one, so it is folded into the neighbour it came from.
    """
    if len(chunks) < 2:
        return chunks
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < MIN_CHUNK_CHARS:
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
            continue
        merged.append(chunk)
    while len(merged) > 1 and len(merged[0]) < MIN_CHUNK_CHARS:
        merged[1] = f"{merged[0]}\n\n{merged[1]}"
        merged.pop(0)
    return merged


def _split_long_block(text: str, budget: int = PDF_CHUNK_TARGET_CHARS) -> list[str]:
    """Line-aligned windows over a block bigger than the budget.

    Splitting on lines rather than characters matters for a wide table: a
    character window cuts a row in half and leaves two chunks that each hold
    part of a number.
    """
    lines = text.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if size and size + len(line) + 1 > budget:
            chunks.append("\n".join(current).strip())
            overlap: list[str] = []
            overlap_size = 0
            for previous in reversed(current):
                if overlap_size + len(previous) + 1 > PDF_CHUNK_OVERLAP_CHARS:
                    break
                overlap.insert(0, previous)
                overlap_size += len(previous) + 1
            current = overlap
            size = overlap_size
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def format_page_range(doc_page: str | None, pdf_page: int) -> str:
    return f"p.{doc_page}" if doc_page else f"PDF p.{pdf_page}"


def format_citation(title: str, doc_page: str | None, pdf_page: int, inferred: bool) -> str:
    if not doc_page:
        return f"{title}, PDF p.{pdf_page} (no printed page number)"
    suffix = " inferred" if inferred else ""
    return f"{title}, p.{doc_page} (PDF p.{pdf_page}{suffix})"


def build_chunks(path: Path, title: str, section: str, pages: list[PdfPage]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for page in pages:
        for ordinal, text in enumerate(split_page_text(page.text)):
            chunks.append(
                {
                    "kind": section,
                    "section": section,
                    "title": title,
                    "path": str(path),
                    "chunk_id": f"{path.name}:pdf{page.pdf_page}:{ordinal}",
                    "text": text,
                    "source_kind": "pdf",
                    "pdf_page": page.pdf_page,
                    "doc_page": page.doc_page or "",
                    "doc_page_inferred": page.doc_page_inferred,
                    "page_range": format_page_range(page.doc_page, page.pdf_page),
                    "citation": format_citation(
                        title, page.doc_page, page.pdf_page, page.doc_page_inferred
                    ),
                }
            )
    return chunks


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def title_for_pdf(path: Path, running: set[str], override: str | None) -> str:
    if override:
        return override
    candidates = [line for line in running if len(line) >= 8 and not page_label(line)]
    if candidates:
        return max(candidates, key=len)
    return path.stem.replace("_", " ").replace("-", " ").strip()


def ingest_pdf(path: Path) -> tuple[list[dict[str, Any]], PdfIngestReport]:
    """Extract, clean, page-number and chunk one PDF. Never raises."""
    started = time.monotonic()
    entry = manifest_entry_for(path)
    report = PdfIngestReport(path=str(path), title=path.stem)

    raw_pages = extract_pages(path, report)
    if not raw_pages:
        report.seconds = round(time.monotonic() - started, 2)
        return [], report

    running = find_running_lines(raw_pages)
    title = title_for_pdf(path, running, str(entry.get("title") or "").strip() or None)
    report.title = title
    section = str(entry.get("section") or PDF_SECTION).strip() or PDF_SECTION

    cleaned: list[tuple[int, str]] = []
    labels: dict[int, tuple[str, bool]] = {}
    for pdf_page, text in raw_pages:
        body, label = clean_page(normalize_page_text(text), running)
        if label:
            labels[pdf_page] = label
        cleaned.append((pdf_page, body))
    report.labelled_pages = len(labels)

    override_offset = entry.get("page_offset")
    offset = resolve_page_offset(labels, int(override_offset) if isinstance(override_offset, int) else None)
    report.page_offset = offset

    arabic_offsets = {
        pdf_page - int(value) for pdf_page, (value, is_arabic) in labels.items() if is_arabic
    }
    if len(arabic_offsets) > 1:
        report.warnings.append(
            "the printed page numbers imply more than one PDF-to-document offset "
            f"({sorted(arabic_offsets)}); citations use the majority offset {offset}."
        )

    pages: list[PdfPage] = []
    for pdf_page, body in cleaned:
        if len(body.strip()) < MIN_PAGE_CHARS:
            report.unreadable_pages.append(pdf_page)
            continue
        label = labels.get(pdf_page)
        if label:
            doc_page, inferred = label[0], False
        elif offset is not None and pdf_page - offset >= 1:
            doc_page, inferred = str(pdf_page - offset), True
        else:
            doc_page, inferred = None, False
        pages.append(PdfPage(pdf_page=pdf_page, doc_page=doc_page, doc_page_inferred=inferred, text=body))

    if report.unreadable_pages:
        report.warnings.append(
            f"{len(report.unreadable_pages)} page(s) carry no usable text layer and were not "
            "indexed (full-page figures or scans that would need OCR): "
            + _compact_pages(report.unreadable_pages)
        )
    if report.page_count and not labels:
        report.warnings.append(
            "no printed page numbers were found, so citations quote PDF page numbers only."
        )

    chunks = build_chunks(path, title, section, pages)
    report.indexed_pages = len(pages)
    report.chunks = len(chunks)
    report.seconds = round(time.monotonic() - started, 2)
    return chunks, report


def _compact_pages(pages: list[int], limit: int = 12) -> str:
    head = ", ".join(str(page) for page in pages[:limit])
    return head if len(pages) <= limit else f"{head}, ... (+{len(pages) - limit})"


# ---------------------------------------------------------------------------
# Cache -- extraction is seconds per document, retrieval is per request
# ---------------------------------------------------------------------------


def cache_dir() -> Path:
    return Path(os.getenv("RAG_PDF_CACHE_DIR", str(REPO_ROOT / "runtime" / "rag_pdf_cache")))


def _file_digest(path: Path) -> str:
    """Content hash of the source PDF, read in 1 MiB blocks.

    Size+mtime is not a content key. ``st_mtime_ns`` is nanosecond-typed but not
    nanosecond-resolved: on the Windows dev box two back-to-back writes of the
    same length were measured landing on a byte-identical ``st_mtime_ns``, and
    archive extraction and ``cp -p``/``rsync -t`` restore a stored timestamp on
    purpose. Either way the disk cache then served the OLD document's chunks --
    carrying the OLD document's page numbers -- for a file that had been
    replaced, which is a citation that lies. ~11 ms for the 15 MB client
    guideline, paid once per process rather than per query.
    """
    digest = sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_key(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "version": CACHE_VERSION,
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha1": _file_digest(path),
        "target": PDF_CHUNK_TARGET_CHARS,
        "overlap": PDF_CHUNK_OVERLAP_CHARS,
        "max": PDF_CHUNK_MAX_CHARS,
        "manifest": manifest_entry_for(path),
    }


def _cache_path(path: Path) -> Path:
    digest = sha1(str(path.resolve()).encode("utf-8", errors="replace")).hexdigest()[:16]
    return cache_dir() / f"{path.stem}-{digest}.json"


def ingest_pdf_cached(path: Path) -> tuple[list[dict[str, Any]], PdfIngestReport]:
    try:
        key = _cache_key(path)
    except OSError:
        return ingest_pdf(path)

    cache_file = _cache_path(path)
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if payload.get("key") == key:
            report = PdfIngestReport(**payload["report"])
            return list(payload["chunks"]), report
    except (OSError, ValueError, TypeError, KeyError):
        pass

    chunks, report = ingest_pdf(path)
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"key": key, "chunks": chunks, "report": report.as_dict()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:  # a read-only checkout still works, just slower
        logger.warning("Could not write the RAG PDF cache %s: %s", cache_file, exc)
    return chunks, report


# ---------------------------------------------------------------------------
# Corpus entry points
# ---------------------------------------------------------------------------


_MEMO: dict[str, Any] = {"key": None, "value": None}


def _corpus_fingerprint(paths: list[Path]) -> tuple[Any, ...]:
    entries: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((str(path), stat.st_size, stat.st_mtime_ns))
    return (CACHE_VERSION, tuple(entries))


def load_pdf_corpus(use_cache: bool = True) -> tuple[list[dict[str, Any]], list[PdfIngestReport]]:
    """Chunks and reports for every configured PDF.

    Retrieval hits this on every keyword query and every status call, while
    extraction costs seconds per document, so results are memoised in-process on
    the source files' size+mtime and persisted to ``ingest_pdf_cached``'s disk
    cache across restarts. Touching a PDF invalidates both.
    """
    paths = list_pdf_files()
    if use_cache:
        key = _corpus_fingerprint(paths)
        if _MEMO["key"] == key and _MEMO["value"] is not None:
            chunks, reports = _MEMO["value"]
            return list(chunks), list(reports)

    chunks: list[dict[str, Any]] = []
    reports: list[PdfIngestReport] = []
    for path in paths:
        document_chunks, report = ingest_pdf_cached(path) if use_cache else ingest_pdf(path)
        chunks.extend(document_chunks)
        reports.append(report)

    if use_cache:
        # The key from BEFORE the extraction, deliberately. Re-reading the
        # fingerprint here would stamp chunks that describe the old file with
        # the new file's fingerprint whenever a source PDF is replaced during
        # ingest -- a ~7 s window for the client guideline -- and the memo would
        # then serve the superseded document, with its page numbers, for the
        # rest of the process with nothing able to notice.
        _MEMO["key"] = key
        _MEMO["value"] = (chunks, reports)
    return chunks, reports


def reset_pdf_corpus_cache() -> None:
    """Drop the in-process memo. Tests that swap PDF roots need this."""
    _MEMO["key"] = None
    _MEMO["value"] = None


def pdf_corpus_status() -> dict[str, Any]:
    """What the PDF half of the corpus contains, including what it could not read."""
    _, reports = load_pdf_corpus()
    return {
        "roots": [str(root) for root in pdf_roots()],
        "files": len(reports),
        "pages": sum(report.page_count for report in reports),
        "indexed_pages": sum(report.indexed_pages for report in reports),
        "chunks": sum(report.chunks for report in reports),
        "documents": [report.as_dict() for report in reports],
        "warnings": [
            f"{Path(report.path).name}: {warning}"
            for report in reports
            for warning in report.warnings
        ],
    }


__all__ = [
    "MIN_CHUNK_CHARS",
    "MIN_PAGE_CHARS",
    "PDF_CHUNK_MAX_CHARS",
    "PDF_CHUNK_OVERLAP_CHARS",
    "PDF_CHUNK_TARGET_CHARS",
    "PDF_SECTION",
    "PdfIngestReport",
    "PdfPage",
    "build_chunks",
    "clean_page",
    "find_running_lines",
    "format_citation",
    "format_page_range",
    "ingest_pdf",
    "ingest_pdf_cached",
    "list_pdf_files",
    "load_pdf_corpus",
    "normalize_page_text",
    "page_label",
    "pdf_corpus_status",
    "pdf_roots",
    "reset_pdf_corpus_cache",
    "resolve_page_offset",
    "split_page_text",
    "title_for_pdf",
]
