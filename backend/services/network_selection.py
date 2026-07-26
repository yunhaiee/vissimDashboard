"""Resolve the network selection a chat request carries (Phase 3 P6).

Clicking a link, connector or element glyph in the rendered network publishes a
structured reference to the floating assistant, which forwards it as
``selected_link_ids`` / ``selected_element``. This module is the *boundary*
between those client-supplied strings and anything that reaches a prompt:

1. ``sanitize_*`` — never trust the client. Ids are stripped, de-duplicated,
   length-capped and count-capped; the element dict is reduced to a whitelist of
   four string fields, each length-capped, with control characters removed (a
   name carrying newlines could otherwise break the prompt's JSON context block).
   These run at the FastAPI request boundary (``routers/assistant.py``), so a
   malformed payload is neutralized before any service sees it.
2. ``resolve_*`` — re-read the truth from the active dataset rather than
   believing the client's own description of what it clicked. A link id becomes
   the parsed ``.inpx``'s ``{id, name, lanes, length, is_connector, from_link,
   to_link}``; an element becomes its parsed record. An id the active network
   does not contain resolves to ``{"id": ..., "missing": True}`` rather than
   being silently dropped — the model must be able to say "that link isn't in
   this network" instead of quietly answering about nothing.

Never raises: a missing or malformed active ``.inpx`` degrades to "everything is
missing", matching this repo's existing degrade-don't-500 policy for network
reads (see ``routers/network.py`` and ``routers/trajectories.py``).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from services.datasets import get_active_inpx_path
from services.inpx_parser import parse_network

logger = logging.getLogger(__name__)

# Caps applied at the request boundary. The link cap matches the node cap the
# assistant router already applies (`compact_selected_ids(..., 6)`); the char
# caps are generous versus real Vissim ids/names but bound what a hostile client
# can push into a prompt.
MAX_SELECTED_LINKS = 6
MAX_ID_CHARS = 64
MAX_NAME_CHARS = 120

# The only keys accepted on `selected_element`; everything else is dropped.
ELEMENT_ID_FIELDS = ("type", "id")
ELEMENT_OPTIONAL_FIELDS = ("name", "link")

_MAX_FIELD_CHARS = {
    "type": MAX_ID_CHARS,
    "id": MAX_ID_CHARS,
    "name": MAX_NAME_CHARS,
    "link": MAX_ID_CHARS,
}


def _clean_text(value: Any, limit: int) -> str:
    """Strip a client string down to a single-line, length-capped token.

    Non-``str`` input yields ``""`` (the caller decides whether that is fatal) —
    numbers are deliberately NOT coerced, so the accepted shape stays exactly
    what the frontend sends and a nested object can never leak into a prompt.
    """
    if not isinstance(value, str):
        return ""
    # Control characters (newlines included) would break the prompt's JSON
    # context block into something the model reads as instructions.
    collapsed = "".join(char for char in value if char >= " " and char != "\x7f")
    return collapsed.strip()[:limit]


def sanitize_selected_link_ids(values: Any) -> list[str]:
    """Strip/de-duplicate/cap client-supplied link ids. Never raises."""
    if not isinstance(values, list):
        return []
    selected: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _clean_text(raw, MAX_ID_CHARS)
        if not value or value in seen:
            continue
        seen.add(value)
        selected.append(value)
        if len(selected) >= MAX_SELECTED_LINKS:
            break
    return selected


def sanitize_selected_element(raw: Any) -> dict[str, str | None] | None:
    """Reduce a client element reference to the whitelisted string fields.

    Returns ``None`` for anything that is not a dict carrying a non-empty
    ``type`` AND ``id`` — a reference we cannot look up is worth nothing to the
    model and is not worth forwarding.
    """
    if not isinstance(raw, dict):
        return None
    cleaned: dict[str, str | None] = {}
    for field in ELEMENT_ID_FIELDS:
        value = _clean_text(raw.get(field), _MAX_FIELD_CHARS[field])
        if not value:
            return None
        cleaned[field] = value
    for field in ELEMENT_OPTIONAL_FIELDS:
        value = _clean_text(raw.get(field), _MAX_FIELD_CHARS[field])
        cleaned[field] = value or None
    return cleaned


@lru_cache(maxsize=2)
def _parse_network_cached(inpx_path: str, mtime: float) -> dict[str, Any]:
    """Parse one ``.inpx``, keyed on (path, mtime) so an edited/replaced dataset
    invalidates itself — the same cache key shape ``routers/network.py`` uses."""
    return parse_network(inpx_path)


def _active_network() -> dict[str, Any] | None:
    """The parsed active dataset, or ``None`` when it is missing/unparseable."""
    path = get_active_inpx_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        logger.info("network selection: no active .inpx at %s", path)
        return None
    try:
        return _parse_network_cached(str(path), mtime)
    except Exception:
        logger.exception("network selection: failed to parse active .inpx at %s", path)
        return None


LINK_DETAIL_FIELDS = ("id", "name", "lanes", "length", "is_connector", "from_link", "to_link")


def resolve_selected_links(link_ids: list[str]) -> list[dict[str, Any]]:
    """Look each sanitized link id up in the active network.

    Preserves the caller's order, one entry per requested id. A link the active
    network does not contain becomes ``{"id": <id>, "missing": True}``.
    """
    if not link_ids:
        return []
    network = _active_network()
    links_by_id: dict[str, dict[str, Any]] = {}
    if network:
        for link in network.get("links") or []:
            links_by_id[str(link.get("id"))] = link

    resolved: list[dict[str, Any]] = []
    for link_id in link_ids:
        link = links_by_id.get(link_id)
        if link is None:
            resolved.append({"id": link_id, "missing": True})
            continue
        detail = {field: link.get(field) for field in LINK_DETAIL_FIELDS}
        detail["id"] = link_id
        resolved.append(detail)
    return resolved


def resolve_selected_element(element: dict[str, str | None] | None) -> dict[str, Any] | None:
    """Enrich a sanitized element reference from the active network.

    The parsed record wins over the client's own name/link when the element is
    found; the client's values survive only as a fallback. ``link_name`` is
    added when the owning link resolves. Not found in the active network ->
    the reference is returned with ``"missing": True`` rather than dropped.
    """
    if not element:
        return None

    element_type = str(element.get("type") or "")
    element_id = str(element.get("id") or "")
    out: dict[str, Any] = {
        "type": element_type,
        "id": element_id,
        "name": element.get("name"),
        "link": element.get("link"),
    }

    network = _active_network()
    if not network:
        out["missing"] = True
        return out

    records = (network.get("elements") or {}).get(element_type)
    match = None
    if isinstance(records, list):
        match = next((record for record in records if str(record.get("id")) == element_id), None)
    if match is None:
        out["missing"] = True
        return out

    out["name"] = match.get("name") or out["name"]
    # traveltime_sections_geo carries its link on start/end rather than at the
    # top level; every other class has a plain `link`.
    link_id = match.get("link")
    if link_id is None:
        start = match.get("start")
        if isinstance(start, dict):
            link_id = start.get("link")
    if link_id is not None:
        out["link"] = str(link_id)
    if match.get("lane") is not None:
        out["lane"] = match.get("lane")
    if match.get("pos") is not None:
        out["pos"] = match.get("pos")

    if out["link"]:
        link = next((item for item in (network.get("links") or []) if str(item.get("id")) == out["link"]), None)
        if link is not None:
            out["link_name"] = link.get("name")
    return out
