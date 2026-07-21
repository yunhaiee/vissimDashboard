"""Persistent Chroma vector index helpers for RAG."""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha1
import os
from pathlib import Path
import time
from typing import Any, Iterable

from config import REPO_ROOT

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
INDEX_DIR = Path(os.getenv("RAG_CHROMA_DIR", str(REPO_ROOT / "runtime" / "rag_chroma")))
SKLEARN_INDEX_PATH = Path(os.getenv("RAG_SKLEARN_INDEX", str(REPO_ROOT / "runtime" / "rag_tfidf.joblib")))
COLLECTION_NAME = os.getenv("RAG_CHROMA_COLLECTION", "vissim_docs")
EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
EMBEDDING_DEVICE = os.getenv("RAG_EMBEDDING_DEVICE", "cpu").strip() or "cpu"
BATCH_SIZE = int(os.getenv("RAG_EMBED_BATCH_SIZE", "32"))
VECTOR_BACKEND = os.getenv("RAG_VECTOR_BACKEND", "auto").strip().lower()


def get_vector_status() -> dict[str, Any]:
    dependencies = dependency_status()
    status: dict[str, Any] = {
        "ready": False,
        "dependencies": dependencies,
        "backend": "none",
        "preferred_backend": VECTOR_BACKEND,
        "index_dir": str(INDEX_DIR),
        "sklearn_index": str(SKLEARN_INDEX_PATH),
        "collection": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_device": EMBEDDING_DEVICE,
        "build_command": "python tools/build_rag_index.py",
    }
    if _can_use_chroma(dependencies) and VECTOR_BACKEND in {"auto", "chroma"}:
        chroma_status = _chroma_status()
        if chroma_status.get("ready") or VECTOR_BACKEND == "chroma":
            status.update(chroma_status)
            return status

    if _can_use_sklearn(dependencies) and VECTOR_BACKEND in {"auto", "sklearn", "tfidf"}:
        sklearn_status = _sklearn_status()
        status.update(sklearn_status)
        return status

    if VECTOR_BACKEND == "chroma":
        status["reason"] = "chromadb and sentence-transformers are required for Chroma+bge-m3."
    elif VECTOR_BACKEND in {"sklearn", "tfidf"}:
        status["reason"] = "scikit-learn and joblib are required for the local TF-IDF vector index."
    else:
        status["reason"] = "No vector backend dependencies are available."
    return status


def search_vector_index(query: str, limit: int) -> list[dict[str, Any]]:
    status = get_vector_status()
    if not status.get("ready"):
        return []
    if status.get("backend") == "sklearn_tfidf":
        return _search_sklearn_index(query, limit)

    try:
        model = _load_embedding_model()
        embedding = _encode_texts(model, [query])[0]
        collection = _get_collection()
        result = collection.query(
            query_embeddings=[embedding],
            n_results=max(limit, 1),
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        return []

    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    hits: list[dict[str, Any]] = []
    for text, metadata, distance in zip(documents, metadatas, distances):
        metadata = metadata or {}
        hits.append(
            {
                "kind": metadata.get("kind", "doc"),
                "section": metadata.get("section", ""),
                "title": metadata.get("title", ""),
                "path": metadata.get("path", ""),
                "chunk_id": metadata.get("chunk_id", ""),
                "text": text,
                "score": float(distance),
                "retrieval": "vector",
                "vector_backend": "chroma",
            }
        )
    return hits


def build_vector_index(chunks: list[dict[str, Any]], reset: bool = True) -> dict[str, Any]:
    dependencies = dependency_status()
    if _can_use_chroma(dependencies) and VECTOR_BACKEND in {"auto", "chroma"}:
        return _build_chroma_index(chunks, reset=reset)
    if _can_use_sklearn(dependencies) and VECTOR_BACKEND in {"auto", "sklearn", "tfidf"}:
        return _build_sklearn_index(chunks, reset=reset)

    missing = [name for name, available in dependencies.items() if not available]
    raise RuntimeError(f"Missing RAG vector dependencies: {', '.join(missing)}")


def _build_chroma_index(chunks: list[dict[str, Any]], reset: bool = True) -> dict[str, Any]:
    if reset and INDEX_DIR.exists():
        _safe_delete_index_dir()

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    collection = _get_collection(create=True)
    model = _load_embedding_model()

    started = time.monotonic()
    total = 0
    for batch in _batched(chunks, BATCH_SIZE):
        documents = [str(item.get("text") or "") for item in batch]
        embeddings = _encode_texts(model, documents)
        collection.add(
            ids=[stable_id(item) for item in batch],
            documents=documents,
            metadatas=[metadata_for_chunk(item) for item in batch],
            embeddings=embeddings,
        )
        total += len(batch)

    return {
        "backend": "chroma",
        "index_dir": str(INDEX_DIR),
        "collection": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL,
        "chunks": total,
        "seconds": round(time.monotonic() - started, 2),
    }


def _build_sklearn_index(chunks: list[dict[str, Any]], reset: bool = True) -> dict[str, Any]:
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer

    if reset and SKLEARN_INDEX_PATH.exists():
        _safe_delete_sklearn_index()

    SKLEARN_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    texts = [str(item.get("text") or "") for item in chunks]
    vectorizer = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(2, 5),
        min_df=1,
        max_features=120000,
        norm="l2",
    )
    matrix = vectorizer.fit_transform(texts)
    payload = {
        "chunks": [
            {
                **metadata_for_chunk(item),
                "text": str(item.get("text") or ""),
            }
            for item in chunks
        ],
        "matrix": matrix,
        "vectorizer": vectorizer,
        "backend": "sklearn_tfidf",
        "created_at": time.time(),
    }
    joblib.dump(payload, SKLEARN_INDEX_PATH, compress=3)
    return {
        "backend": "sklearn_tfidf",
        "index_path": str(SKLEARN_INDEX_PATH),
        "chunks": len(chunks),
        "seconds": round(time.monotonic() - started, 2),
    }


def metadata_for_chunk(chunk: dict[str, Any]) -> dict[str, str]:
    return {
        "kind": str(chunk.get("kind") or "doc"),
        "section": str(chunk.get("section") or ""),
        "title": str(chunk.get("title") or ""),
        "path": str(chunk.get("path") or ""),
        "chunk_id": str(chunk.get("chunk_id") or ""),
    }


def stable_id(chunk: dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(chunk.get("path") or ""),
            str(chunk.get("chunk_id") or ""),
            str(chunk.get("text") or "")[:256],
        ]
    )
    return sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def dependency_status() -> dict[str, bool]:
    return {
        "chromadb": _module_available("chromadb"),
        "sentence_transformers": _module_available("sentence_transformers"),
        "sklearn": _module_available("sklearn"),
        "joblib": _module_available("joblib"),
    }


def _module_available(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return False
    return True


def _get_collection(create: bool = False):
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(INDEX_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    if create:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        return client.create_collection(
            COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL},
        )
    return client.get_collection(COLLECTION_NAME)


def _chroma_status() -> dict[str, Any]:
    status: dict[str, Any] = {"backend": "chroma"}
    if not INDEX_DIR.exists():
        status["reason"] = "Chroma vector index has not been built yet."
        return status

    try:
        collection = _get_collection()
        count = collection.count()
    except Exception as exc:
        status["reason"] = str(exc)
        return status

    status["count"] = count
    status["ready"] = count > 0
    if not status["ready"]:
        status["reason"] = "Chroma collection is empty."
    return status


def _sklearn_status() -> dict[str, Any]:
    status: dict[str, Any] = {"backend": "sklearn_tfidf"}
    if not SKLEARN_INDEX_PATH.exists():
        status["reason"] = "Local TF-IDF vector index has not been built yet."
        return status

    try:
        payload = _load_sklearn_payload()
        count = len(payload.get("chunks") or [])
    except Exception as exc:
        status["reason"] = str(exc)
        return status

    status["count"] = count
    status["ready"] = count > 0
    if not status["ready"]:
        status["reason"] = "Local TF-IDF vector index is empty."
    return status


def _search_sklearn_index(query: str, limit: int) -> list[dict[str, Any]]:
    try:
        payload = _load_sklearn_payload()
        vectorizer = payload["vectorizer"]
        matrix = payload["matrix"]
        chunks = payload["chunks"]
        query_vector = vectorizer.transform([query])
        scores = (matrix @ query_vector.T).toarray().ravel()
    except Exception:
        return []

    ranked = scores.argsort()[::-1][: max(limit, 1)]
    hits: list[dict[str, Any]] = []
    for index in ranked:
        score = float(scores[index])
        if score <= 0:
            continue
        item = chunks[int(index)]
        hits.append(
            {
                "kind": item.get("kind", "doc"),
                "section": item.get("section", ""),
                "title": item.get("title", ""),
                "path": item.get("path", ""),
                "chunk_id": item.get("chunk_id", ""),
                "text": item.get("text", ""),
                "score": score,
                "retrieval": "vector",
                "vector_backend": "sklearn_tfidf",
            }
        )
    return hits


@lru_cache(maxsize=1)
def _load_sklearn_payload() -> dict[str, Any]:
    import joblib

    return joblib.load(SKLEARN_INDEX_PATH)


def _can_use_chroma(dependencies: dict[str, bool]) -> bool:
    return bool(dependencies.get("chromadb") and dependencies.get("sentence_transformers"))


def _can_use_sklearn(dependencies: dict[str, bool]) -> bool:
    return bool(dependencies.get("sklearn") and dependencies.get("joblib"))


@lru_cache(maxsize=1)
def _load_embedding_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL, device=EMBEDDING_DEVICE)


def _encode_texts(model: Any, texts: list[str]) -> list[list[float]]:
    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return embeddings.tolist()


def _batched(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _safe_delete_index_dir() -> None:
    import shutil

    resolved = INDEX_DIR.resolve()
    runtime_root = (REPO_ROOT / "runtime").resolve()
    if runtime_root not in resolved.parents and resolved != runtime_root:
        raise RuntimeError(f"Refusing to delete index outside runtime: {resolved}")
    shutil.rmtree(resolved)


def _safe_delete_sklearn_index() -> None:
    resolved = SKLEARN_INDEX_PATH.resolve()
    runtime_root = (REPO_ROOT / "runtime").resolve()
    if runtime_root not in resolved.parents:
        raise RuntimeError(f"Refusing to delete index outside runtime: {resolved}")
    resolved.unlink()
