"""Build the RAG corpus and the vector index over it.

``services/rag_vector_store.get_vector_status()`` has advertised
``python tools/build_rag_index.py`` as the build command since the vector index
was added, and the file did not exist -- so the one instruction the status
endpoint gave a developer was a dead end. This is that script.

Usage (from ``backend/``, with the venv python)::

    python tools/build_rag_index.py                 # collect + build the index
    python tools/build_rag_index.py --dry-run       # collect and report only
    python tools/build_rag_index.py --json          # machine-readable summary
    python tools/build_rag_index.py --no-cache      # re-extract every PDF
    python tools/build_rag_index.py --query "..."   # collect, build, then search

The corpus is markdown under ``docs/`` plus every PDF under the configured PDF
roots (``client-data/`` and ``docs/pdf_knowledge/`` by default; override with
``RAG_PDF_ROOTS``). PDFs are gitignored client data, so a checkout without them
builds a markdown-only index -- the summary says which, rather than reporting a
number that quietly means something different on each machine.

Nothing here calls an LLM or any network service. The default vector backend in
this environment is scikit-learn TF-IDF, which is local and offline; the Chroma
+ bge-m3 backend is used when its dependencies are installed (see
``requirements-rag.txt``). Neither path needs ``ANTHROPIC_API_KEY``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.rag_documents import (  # noqa: E402
    RAG_SECTIONS,
    list_corpus_documents,
    load_markdown_chunks,
    search_rag,
)
from services.rag_pdf import load_pdf_corpus, pdf_roots  # noqa: E402
from services.rag_vector_store import build_vector_index, get_vector_status  # noqa: E402


def collect(use_cache: bool = True) -> dict[str, object]:
    started = time.monotonic()
    markdown_chunks = load_markdown_chunks()
    pdf_chunks, reports = load_pdf_corpus(use_cache=use_cache)
    documents = list_corpus_documents()

    by_kind: dict[str, int] = {}
    for document in documents:
        by_kind[document.kind] = by_kind.get(document.kind, 0) + 1

    return {
        "documents": len(documents),
        "chunks": len(markdown_chunks) + len(pdf_chunks),
        "markdown_chunks": len(markdown_chunks),
        "pdf_chunks": len(pdf_chunks),
        "sections": by_kind,
        "missing_sections": sorted(section for section in RAG_SECTIONS if not by_kind.get(section)),
        "pdf_roots": [str(root) for root in pdf_roots()],
        "pdf_documents": [report.as_dict() for report in reports],
        "seconds": round(time.monotonic() - started, 2),
        "_chunks": [*markdown_chunks, *pdf_chunks],
    }


def print_summary(summary: dict[str, object]) -> None:
    print(f"corpus: {summary['documents']} documents / {summary['chunks']} chunks "
          f"({summary['markdown_chunks']} markdown + {summary['pdf_chunks']} pdf) "
          f"in {summary['seconds']}s")
    sections = summary["sections"]
    assert isinstance(sections, dict)
    print("  by kind: " + ", ".join(f"{name}={count}" for name, count in sorted(sections.items())))

    missing = summary["missing_sections"]
    assert isinstance(missing, list)
    if missing:
        print(
            "  section directories the retrieval code expects but that are NOT on disk: "
            + ", ".join(missing)
        )
        print("    (documents in them fall back to the generic 'doc' kind; ranking that "
              "boosts error_cases cannot fire)")

    roots = summary["pdf_roots"]
    assert isinstance(roots, list)
    print("  pdf roots: " + (", ".join(roots) if roots else "none on this machine"))

    pdf_documents = summary["pdf_documents"]
    assert isinstance(pdf_documents, list)
    if not pdf_documents:
        print("  no PDF was ingested -- client PDFs are gitignored, so this is expected "
              "on a fresh checkout. The index is markdown-only.")
    for report in pdf_documents:
        print(
            f"  - {Path(str(report['path'])).name}: {report['chunks']} chunks from "
            f"{report['indexed_pages']}/{report['page_count']} pages, "
            f"page offset {report['page_offset']} "
            f"({report['labelled_pages']} pages carry a printed number), {report['seconds']}s"
        )
        print(f"    title: {report['title']}")
        for warning in report["warnings"]:  # type: ignore[index]
            print(f"    ! {warning}")


def build(summary: dict[str, object]) -> dict[str, object] | None:
    chunks = summary["_chunks"]
    assert isinstance(chunks, list)
    if not chunks:
        print("nothing to index: the corpus is empty.")
        return None
    try:
        result = build_vector_index(chunks)
    except RuntimeError as exc:
        print(f"vector index NOT built: {exc}")
        print("  keyword retrieval still works; install requirements-rag.txt or scikit-learn "
              "to add the vector half.")
        return None
    print(
        f"vector index built: backend={result.get('backend')} "
        f"chunks={result.get('chunks')} in {result.get('seconds')}s"
    )
    return result


def run_query(query: str, limit: int) -> list[dict[str, object]]:
    hits = search_rag(query, limit)
    print(f"\nquery: {query}")
    for index, hit in enumerate(hits, start=1):
        citation = hit.get("citation") or hit.get("title") or hit.get("path")
        retrieval = hit.get("retrieval") or "keyword"
        print(f"  {index}. [{retrieval}] {citation}")
        print(f"     chunk_id={hit.get('chunk_id')} page_range={hit.get('page_range') or '-'}")
        snippet = " ".join(str(hit.get("text") or "").split())[:200]
        print(f"     {snippet}")
    if not hits:
        print("  (no hits)")
    return hits


def make_output_lossless() -> None:
    """Never let a glyph the console cannot draw take the whole command down.

    Measured: ``python tools/build_rag_index.py --query "saturation flow ..."``
    exited 2 with ``UnicodeEncodeError: 'charmap' codec can't encode character
    '\\uf0b7'`` on the Windows dev box. The MRWA guidelines set their bullets in
    Symbol font, so pypdf hands back U+F0B7, and Windows encodes stdout as
    cp1252 -- so printing a retrieved snippet crashed the script AFTER it had
    already built the index. The index is this script's product; a console that
    cannot render a bullet is not a reason to fail the run.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main() -> int:
    make_output_lossless()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="collect and report, do not build the index")
    parser.add_argument("--no-cache", action="store_true", help="re-extract every PDF, ignoring the disk cache")
    parser.add_argument("--json", action="store_true", help="print a machine-readable summary")
    parser.add_argument("--query", action="append", default=[], help="run a retrieval query after building")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    summary = collect(use_cache=not args.no_cache)
    if not args.json:
        print_summary(summary)

    result = None if args.dry_run else build(summary)
    if args.dry_run and not args.json:
        print("dry run: vector index not touched.")

    queries = list(args.query)
    hits: dict[str, list[dict[str, object]]] = {}
    for query in queries:
        hits[query] = run_query(query, args.top_k)

    if args.json:
        payload = {key: value for key, value in summary.items() if not key.startswith("_")}
        payload["vector_index"] = result
        payload["vector_status"] = {
            key: value for key, value in get_vector_status().items() if key != "dependencies"
        }
        payload["queries"] = {
            query: [
                {
                    "citation": hit.get("citation") or hit.get("title"),
                    "path": hit.get("path"),
                    "chunk_id": hit.get("chunk_id"),
                    "page_range": hit.get("page_range"),
                    "retrieval": hit.get("retrieval"),
                }
                for hit in items
            ]
            for query, items in hits.items()
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
