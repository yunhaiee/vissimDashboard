"""Machine-readable registry of dashboard sections for LLM curation.

Ids mirror the anchor/section ids of frontend/app/dashboard/page.tsx exactly
(map-selection, intersection-performance, travel-time-comparison,
network-kpi-comparison, overall-results-analysis, field-count-validation) so a
curation spec can drive section visibility without any id mapping. Keep the
two lists in sync when the page gains or loses sections.
"""
from __future__ import annotations

from typing import Any

CHART_CATALOG: list[dict[str, Any]] = [
    {
        "id": "map-selection",
        "name_ko": "지도 기반 교차로 선택",
        "name_en": "Map-based intersection selection",
        "description": (
            "Network map with per-intersection popups (volume, average delay, LOS) "
            "plus the selected-intersection panel and turn-movement flows."
        ),
        "answers": "Where are the problem locations? What happens at a specific intersection?",
        "default_visible": True,
    },
    {
        "id": "intersection-performance",
        "name_ko": "교차로 성능평가",
        "name_en": "Intersection performance evaluation",
        "description": (
            "Per-selected-intersection comparison: metric bars, volume by "
            "intersection, and a delay-by-approach chart. CAUTION: the "
            "per-direction delay values are estimates scaled from intersection "
            "average delay by volume share, not measured — caveat them if cited."
        ),
        "answers": "How do the selected intersections perform against each other?",
        "default_visible": True,
    },
    {
        "id": "travel-time-comparison",
        "name_ko": "통행시간 비교분석",
        "name_en": "Travel time comparison",
        "description": (
            "Corridor travel time across the simulation period per scenario. "
            "CAUTION: the curve shape is display-smoothed; cite overall "
            "levels/differences between scenarios, not specific intermediate points."
        ),
        "answers": "How does corridor travel time differ between scenarios?",
        "default_visible": True,
    },
    {
        "id": "network-kpi-comparison",
        "name_ko": "네트워크 KPI 비교",
        "name_en": "Network KPI comparison",
        "description": (
            "Network-wide KPI cards per scenario: average delay (s/veh), average "
            "speed (km/h), stops per vehicle, total travel time and related totals. "
            "The core numeric comparison."
        ),
        "answers": "What are the network-level numbers for each scenario?",
        "default_visible": True,
    },
    {
        "id": "overall-results-analysis",
        "name_ko": "종합 결과 분석",
        "name_en": "Overall results analysis",
        "description": "Narrative interpretation of the comparison plus report (Markdown/Word) generation.",
        "answers": "Give me the overall conclusion / a report.",
        "default_visible": True,
    },
    {
        "id": "field-count-validation",
        "name_ko": "현장 계수 검증",
        "name_en": "Validation against field counts",
        "description": (
            "GEH-based validation of simulated volumes against observed field "
            "counts. Only meaningful when an observed-counts workbook is loaded."
        ),
        "answers": "Is the model calibrated / trustworthy against real counts?",
        "default_visible": False,
    },
]

VALID_CHART_IDS = {entry["id"] for entry in CHART_CATALOG}
DEFAULT_VISIBLE_IDS = [entry["id"] for entry in CHART_CATALOG if entry["default_visible"]]


def catalog_prompt_block() -> str:
    """Render the catalog as a compact prompt block for the curator LLM."""
    lines = []
    for entry in CHART_CATALOG:
        lines.append(
            f"- {entry['id']}: {entry['name_en']} / {entry['name_ko']} — "
            f"{entry['description']} Answers: {entry['answers']}"
        )
    return "\n".join(lines)
