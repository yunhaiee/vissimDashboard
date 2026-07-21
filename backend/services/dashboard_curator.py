"""LLM dashboard curation: pick which charts to show + write insight cards.

Given a user question and the compare-summary data, ask the provider for a
strict-JSON curation spec. This deliberately bypasses the assistant's
``clean_generated_answer`` pipeline (which strips JSON-ish lines) — the raw
model text is parsed here instead.

The output contract (validated before returning):
    {
      "headline": str,                     # one-line answer to the question
      "charts": [chart_id, ...],           # ordered subset of the catalog
      "insight_cards": [
        {"title": str, "body": str, "tone": "info"|"success"|"warning",
         "related_chart": chart_id | None}
      ],
    }

On any provider/parsing failure the fallback spec shows the default charts
with no cards and carries the error, so the dashboard never breaks.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from services.chart_catalog import DEFAULT_VISIBLE_IDS, VALID_CHART_IDS, catalog_prompt_block

logger = logging.getLogger(__name__)

MAX_CHARTS = 5
MAX_CARDS = 4
VALID_TONES = {"info", "success", "warning"}
_CONTEXT_CHAR_LIMIT = 7000

LANGUAGE_NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese"}

AUDIENCE_RULES = {
    "plain": (
        "The reader is a non-expert (e.g. a public official), not a traffic engineer. "
        "Explain every technical term in everyday words the first time it appears. "
        "When average and total versions of a metric could be confused, explain the "
        "difference concretely (e.g. 'average delay is per vehicle; total delay adds "
        "up all vehicles, so a busier road can have lower average but higher total')."
    ),
    "expert": (
        "The reader is a traffic engineer. Use standard terminology (delay s/veh, "
        "LOS, GEH) without explanation; be precise and quantitative."
    ),
}


def build_curation_prompt(
    message: str,
    context: dict[str, Any],
    response_language: str = "ko",
    audience: str = "plain",
) -> str:
    language = LANGUAGE_NAMES.get(response_language, "Korean")
    audience_rule = AUDIENCE_RULES.get(audience, AUDIENCE_RULES["plain"])
    context_json = json.dumps(context, ensure_ascii=False, default=str)
    if len(context_json) > _CONTEXT_CHAR_LIMIT:
        context_json = context_json[:_CONTEXT_CHAR_LIMIT] + "…(truncated)"

    return (
        "You curate a traffic-simulation dashboard. Based on the user's question, "
        "choose ONLY the charts that help answer it, and write short insight cards "
        "that answer the question in plain text using the data provided.\n\n"
        f"Available charts:\n{catalog_prompt_block()}\n\n"
        f"Dashboard data (per-scenario comparison):\n{context_json}\n\n"
        f"User question: {message}\n\n"
        "Rules:\n"
        f"- Write headline and all card text in {language}.\n"
        f"- {audience_rule}\n"
        f"- Choose at most {MAX_CHARTS} charts, ordered most-relevant first. "
        "If the question asks for everything/overview, choose the default set.\n"
        f"- At most {MAX_CARDS} insight cards. Each card states a finding with its "
        "numbers, not a description of what a chart is.\n"
        "- Use only numbers present in the data; never invent values. If the data "
        "cannot answer the question, say so in the headline and choose the closest "
        "relevant charts.\n"
        "- tone: 'warning' for problems/risks, 'success' for improvements, else 'info'.\n\n"
        "Respond with ONLY a JSON object, no markdown fences, no prose, exactly this shape:\n"
        '{"headline": "...", "charts": ["chart_id"], '
        '"insight_cards": [{"title": "...", "body": "...", "tone": "info", '
        '"related_chart": "chart_id or null"}]}'
    )


def extract_json_object(text: str | None) -> dict[str, Any] | None:
    """Pull the first complete JSON object out of model text (tolerates fences)."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def validate_spec(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None

    charts: list[str] = []
    for chart_id in raw.get("charts") or []:
        normalized = str(chart_id).strip()
        if normalized in VALID_CHART_IDS and normalized not in charts:
            charts.append(normalized)
    charts = charts[:MAX_CHARTS]

    cards: list[dict[str, Any]] = []
    for card in raw.get("insight_cards") or []:
        if not isinstance(card, dict):
            continue
        title = str(card.get("title") or "").strip()
        body = str(card.get("body") or "").strip()
        if not title and not body:
            continue
        tone = str(card.get("tone") or "info").strip().lower()
        related = str(card.get("related_chart") or "").strip() or None
        cards.append(
            {
                "title": title,
                "body": body,
                "tone": tone if tone in VALID_TONES else "info",
                "related_chart": related if related in VALID_CHART_IDS else None,
            }
        )
    cards = cards[:MAX_CARDS]

    headline = str(raw.get("headline") or "").strip()
    if not charts and not cards and not headline:
        return None

    return {
        "headline": headline,
        "charts": charts or list(DEFAULT_VISIBLE_IDS),
        "insight_cards": cards,
    }


def fallback_spec(error: str) -> dict[str, Any]:
    return {
        "headline": "",
        "charts": list(DEFAULT_VISIBLE_IDS),
        "insight_cards": [],
        "fallback": True,
        "error": error,
    }


def curate_dashboard(
    provider: Any,
    message: str,
    context: dict[str, Any],
    response_language: str = "ko",
    audience: str = "plain",
) -> dict[str, Any]:
    prompt = build_curation_prompt(message, context, response_language, audience)
    try:
        generated, runtime = provider.generate(prompt, None, think=False)
    except Exception as exc:  # provider contract violation — treat as fallback
        logger.exception("Curation provider raised")
        generated, runtime = None, {"mode": "fallback", "error": str(exc)}

    spec = validate_spec(extract_json_object(generated))
    if spec is None:
        error = str((runtime or {}).get("error") or "Model returned no parsable curation JSON.")
        result = fallback_spec(error)
    else:
        result = {**spec, "fallback": False}

    result["runtime"] = runtime
    result["audience"] = audience if audience in AUDIENCE_RULES else "plain"
    return result
