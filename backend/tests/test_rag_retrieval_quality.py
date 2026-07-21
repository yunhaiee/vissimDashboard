from __future__ import annotations

import json
from pathlib import Path

from services.rag_documents import get_rag_status, search_rag


CASES_PATH = Path(__file__).with_name("fixtures") / "rag_retrieval_cases.json"


def load_cases() -> list[dict[str, object]]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def searchable_text(hit: dict[str, object]) -> str:
    fields = [
        hit.get("kind"),
        hit.get("section"),
        hit.get("title"),
        hit.get("path"),
        hit.get("chunk_id"),
    ]
    return " ".join(str(field or "").replace("\\", "/") for field in fields).lower()


def test_rag_retrieval_quality_cases():
    status = get_rag_status()
    assert status["chunks"] >= 5000

    failures: list[str] = []
    for case in load_cases():
        top_k = int(case.get("top_k") or 5)
        hits = search_rag(str(case["query"]), top_k)
        haystacks = [searchable_text(hit) for hit in hits]
        expected = [str(item).lower().replace("\\", "/") for item in case["must_match_any"]]  # type: ignore[index]
        matched = any(any(needle in haystack for haystack in haystacks) for needle in expected)
        if not matched:
            titles = [str(hit.get("title") or hit.get("path")) for hit in hits]
            failures.append(f"{case['id']}: expected one of {expected}, got {titles}")

    assert not failures, "\n".join(failures)
