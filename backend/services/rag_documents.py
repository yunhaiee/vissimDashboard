"""Markdown and vector-backed retrieval for assistant RAG context."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from config import REPO_ROOT
from services.rag_vector_store import get_vector_status, search_vector_index

DOC_CONTEXT_LIMIT = 5
CHUNK_TARGET_CHARS = 1400
CHUNK_OVERLAP_CHARS = 180
RAG_SECTIONS = {"vissim_manual", "error_cases", "com_examples", "project_notes", "pdf_knowledge"}

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\uac00-\ud7a3]{2,}")
ERROR_TOKENS = {
    "error",
    "errors",
    "fail",
    "failed",
    "\uc624\ub958",
    "\uc5d0\ub7ec",
    "\uc9c4\ub2e8",
}
ERROR_PHRASES = ("no path", "not found", "cannot be found", "failed to")


@dataclass(frozen=True)
class RagDocument:
    path: Path
    title: str
    kind: str
    section: str


def search_rag(query: str, limit: int = DOC_CONTEXT_LIMIT) -> list[dict[str, Any]]:
    keyword_hits = search_keyword_rag(query, limit)
    vector_hits = search_vector_index(expanded_query_text(query), limit)
    if not vector_hits:
        return keyword_hits

    merged: list[dict[str, Any]] = []
    if is_error_query(query) and keyword_hits and keyword_hits[0].get("kind") == "error_cases":
        merged.append(keyword_hits[0])

    for hit in [*vector_hits, *keyword_hits]:
        key = (hit.get("path"), hit.get("chunk_id"))
        if any((item.get("path"), item.get("chunk_id")) == key for item in merged):
            continue
        merged.append(hit)
        if len(merged) >= limit:
            break

    return merged[:limit]


def search_keyword_rag(query: str, limit: int = DOC_CONTEXT_LIMIT) -> list[dict[str, Any]]:
    chunks = load_rag_chunks()
    if not chunks:
        return []

    query_tokens = expand_query_tokens(tokenize(query))
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, chunk in enumerate(chunks):
        text_tokens = tokenize(str(chunk.get("text") or ""))
        title_tokens = tokenize(str(chunk.get("title") or ""))
        path_tokens = tokenize(str(chunk.get("path") or ""))
        score = len(query_tokens & text_tokens)
        score += 3 * len(query_tokens & title_tokens)
        score += 2 * len(query_tokens & path_tokens)
        if chunk.get("kind") == "error_cases" and is_error_query(query):
            score += 4
        if score > 0:
            scored.append((score, -index, chunk))

    if not scored:
        return chunks[:limit]

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:limit]]


def get_rag_status() -> dict[str, Any]:
    docs = list_markdown_documents()
    by_kind: dict[str, int] = {}
    for doc in docs:
        by_kind[doc.kind] = by_kind.get(doc.kind, 0) + 1
    vector = get_vector_status()
    backend = vector.get("backend") if vector.get("ready") else "markdown"
    return {
        "documents": len(docs),
        "chunks": len(load_rag_chunks()),
        "sections": by_kind,
        "root": str(REPO_ROOT / "docs"),
        "mode": backend,
        "vector": vector,
    }


def load_rag_chunks() -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for doc in list_markdown_documents():
        try:
            text = doc.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for index, chunk in enumerate(split_text(text)):
            chunks.append(
                {
                    "kind": doc.kind,
                    "section": doc.section,
                    "title": doc.title,
                    "path": str(doc.path),
                    "chunk_id": f"{doc.path.name}:{index}",
                    "text": chunk,
                }
            )
    return chunks


def list_markdown_documents() -> list[RagDocument]:
    docs_dir = REPO_ROOT / "docs"
    if not docs_dir.exists():
        return []
    return [document_for_path(path) for path in sorted(docs_dir.rglob("*.md"))]


def document_for_path(path: Path) -> RagDocument:
    section = section_for_path(path)
    kind = section if section in RAG_SECTIONS else "doc"
    return RagDocument(path=path, title=title_for_markdown(path), kind=kind, section=section)


def section_for_path(path: Path) -> str:
    try:
        relative = path.relative_to(REPO_ROOT / "docs")
    except ValueError:
        return ""
    return relative.parts[0] if len(relative.parts) > 1 else ""


def title_for_markdown(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:30]:
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return path.stem.replace("_", " ").replace("-", " ").title()


def split_text(text: str) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > CHUNK_TARGET_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(split_long_text(paragraph))
            continue
        if current and len(current) + len(paragraph) + 2 > CHUNK_TARGET_CHARS:
            chunks.append(current)
            current = current[-CHUNK_OVERLAP_CHARS:].strip()
        current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def split_long_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_TARGET_CHARS)
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
    return [chunk for chunk in chunks if chunk]


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> set[str]:
    return {item.lower() for item in TOKEN_RE.findall(text) if len(item) > 1}


def is_error_query(query: str) -> bool:
    query_lower = query.lower()
    tokens = expand_query_tokens(tokenize(query))
    return bool(ERROR_TOKENS & tokens) or any(phrase in query_lower for phrase in ERROR_PHRASES)


def expand_query_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    synonyms = {
        "\uc624\ub958": {"error", "errors", "fail", "failed", "problem", "issue", "diagnosis", "troubleshooting"},
        "\uc5d0\ub7ec": {"error", "errors", "fail", "failed", "problem", "issue"},
        "\uc9c4\ub2e8": {"diagnosis", "troubleshooting", "error"},
        "\ubcf4\ud589\uc790": {"pedestrian", "pedestrians"},
        "\uc2e0\ud638": {"signal", "controller", "program"},
        "\uc2e0\ud638\uc81c\uc5b4": {"signal", "controller", "program"},
        "\ucc28\ub7c9": {"vehicle", "input", "demand", "volume"},
        "\uad50\ud1b5\ub7c9": {"vehicle", "input", "demand", "volume"},
        "\uacbd\ub85c": {"route", "routing", "path"},
        "\ub77c\uc6b0\ud305": {"route", "routing", "path"},
        "\ub9c1\ud06c": {"link", "connector", "route"},
        "\ucee4\ub125\ud130": {"connector", "link", "route"},
        "\uc2dc\ubbac\ub808\uc774\uc158": {"simulation", "run"},
        "\uc2e4\ud589": {"simulation", "run"},
        "\uc0c1\ud0dc": {"status", "simulation", "job"},
        "\ud3c9\uac00": {"evaluation", "result", "delay", "queue", "travel", "time"},
        "\uacb0\uacfc": {"evaluation", "result", "delay", "queue", "travel", "time"},
        "\uc81c\uc5b4": {"control", "controller"},
        "intersection": {"junction", "node"},
        "junction": {"intersection", "node"},
        "node": {"intersection", "junction"},
        "program": {"signal", "controller"},
        "pedestrian": {"route", "routing", "path"},
        "connector": {"link", "route"},
        "delay": {"evaluation", "results"},
        "path": {"route", "routing"},
    }
    for token in list(tokens):
        expanded.update(synonyms.get(token, set()))
    return expanded


def expanded_query_text(query: str) -> str:
    expanded = expand_query_tokens(tokenize(query))
    return " ".join([query, *sorted(expanded)])
