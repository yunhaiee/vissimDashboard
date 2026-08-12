"""The vision call that PROPOSES a 현시도 reading. Roadmap C1 option (c), stage 2.

One function, one call, one artifact
-------------------------------------
:func:`propose_phase_diagram` takes the PNG
``services.genesis.phase_diagram.render_phase_diagram`` produced and returns a
:class:`~services.genesis.phase_diagram.PhaseProposal`. It never returns
``None``, never raises for a model that could not read the drawing, and never
returns a half-filled reading: the two outcomes are a structured proposal and a
NAMED refusal, and the second is a first-class result the batch counts.

**Strict JSON without a JSON parser.** The call uses the provider's native
tool-use path (``ClaudeProvider.generate_structured``), which forces a
``tool_use`` block via ``tool_choice: {"type": "any"}`` and hands back the tool's
own input dict. So the model's answer never becomes prose that something has to
extract JSON from, and ``assistant_router.clean_generated_answer()`` — which
STRIPS JSON, and would therefore destroy this payload — is nowhere on this path
(CLAUDE.md; ``docs/phase2/00-EXECUTION-GUIDE.md`` §3.3). Two tools are offered
and the model must pick one: ``report_phase_diagram`` or
``refuse_phase_diagram``. A refusal is a tool call, not an empty answer, which is
why "the drawing is unreadable" arrives with a reason attached.

**No client, no second ladder.** ``ClaudeProvider`` is the product's existing
Anthropic client (``services/claude_provider.py``): the bounded 90 s timeout, the
one retry, the ladder ceiling and the WARNING-per-attempt logging are all
already there and are not re-implemented here. This module builds the messages
and validates the result; it constructs no ``anthropic.Anthropic``.

**Never in a request handler.** Every caller is an offline tool
(``backend/tools/genesis_phase_diagram_batch.py``) or a job. That is the same
rule COM work follows and for a related reason: this call takes seconds to tens
of seconds and holds nothing that a request thread should hold.

What the prompt is allowed to ask for, and what it forbids
-----------------------------------------------------------
The instruction is written to make a refusal CHEAP and a guess EXPENSIVE, because
the failure this unit must not produce is a confident wrong reading that a tired
reviewer clicks through. So: the model is told the mapping will be checked by a
named human and used to build a signal controller; that a wrong phase assignment
produces a network that runs and reports plausible numbers while being wrong;
that it must copy approach names VERBATIM from the drawing rather than
translating or normalising them; that any column it cannot resolve goes in
``ambiguity`` rather than being resolved; and that if the drawing is degraded,
rotated, or is not a 현시도 at all, the correct action is
``refuse_phase_diagram``.

The model is NOT asked for a confidence score. A score invites a threshold and a
threshold is the mechanism by which a low-confidence guess gets promoted to a
reading — the ambiguity note goes to a human instead.

CONFIDENTIALITY: the image is client material. It is sent to Anthropic's API
(the product's existing provider) and nothing here writes it to a log. The API
key is read by the SDK from the environment and is never touched, printed or
logged by this module.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any

from services.genesis.phase_diagram import (
    SIDES,
    TURNS,
    DiagramRender,
    PhaseProposal,
    ProposalRefusal,
    ProposedApproach,
    ProposedMovement,
    ProposedPhase,
    sha256_hex,
)

logger = logging.getLogger(__name__)

#: Tool names. ``refuse_phase_diagram`` exists so that "I cannot read this" is a
#: structured answer with a reason rather than an empty or evasive one.
TOOL_REPORT = "report_phase_diagram"
TOOL_REFUSE = "refuse_phase_diagram"

#: Refusal codes this module itself produces when no proposal could be obtained.
#: They are proposal outcomes, not exceptions: a batch run must be able to record
#: "this sheet produced no reading, and here is exactly why" for every sheet.
REFUSAL_PROVIDER_UNAVAILABLE = "phase_diagram_provider_unavailable"
REFUSAL_PROVIDER_FAILED = "phase_diagram_provider_failed"
REFUSAL_INVALID_PAYLOAD = "phase_diagram_proposal_invalid"
#: The model's own refusal, carried through with its stated reason.
REFUSAL_MODEL_DECLINED = "phase_diagram_unreadable"

#: Room for a six-phase intersection with pedestrian notes on every phase, PLUS
#: the model's own reasoning. The second half is why the number is not tighter:
#: current Claude models think adaptively by default and ``max_tokens`` caps
#: thinking and answer TOGETHER, so a budget sized only for the JSON truncates
#: the tool input on exactly the ambiguous diagrams this unit most needs a
#: reading of. An answer that runs out of tokens is reported as an invalid
#: payload (:data:`REFUSAL_INVALID_PAYLOAD`) rather than salvaged.
MAX_TOKENS = 8000

SYSTEM_PROMPT = """You read Korean traffic-signal operation sheets (교차로 운영 DATABASE).

You are given the top band of ONE sheet. It contains, left to right:
  * the intersection header: 교차로명 (name) and 교차로번호 (number);
  * a small sketch of the intersection with the NAME of each leg printed beside
    it (for example 고속터미널(단), 용전천주교(단));
  * one column per 현시 (phase), labelled Φ1, Φ2, … Each column draws the
    movements that receive green in that phase as arrows. A solid arrow is a
    vehicle movement; a dashed arrow is usually a permitted or secondary
    movement; ①보행 with a dashed arrow marks a pedestrian phase. Unused phase
    columns are blank or carry a note.
  * below that, RING A / RING B rows (A1…A6, B1…B6) drawing the same phases
    per controller ring.

Your output is a PROPOSAL for a named human engineer to check. It is not used
unless that human confirms it. Downstream it becomes a Vissim signal controller,
and PTV Vissim does not refuse a wrong phase assignment — it runs it and reports
confident, plausible, wrong numbers. So a reading you are unsure of is worse
than no reading.

RULES
1. Copy every approach name VERBATIM from the drawing, in Korean, including
   brackets and digits. Do not translate, romanise, expand or tidy them. If a
   name is illegible, do not invent one.
2. Report only the phases the drawing actually draws movements for. Do not
   report a blank Φ column.
3. Every phase you report must list at least one movement.
4. Anything you cannot resolve goes in that phase's `ambiguity` field, in
   Korean, as a sentence a reviewer can act on. Never resolve it yourself.
5. If the band is not a 현시도, is rotated, is too degraded to read, or you
   cannot tell which arrows belong to which column, call refuse_phase_diagram
   with a specific reason. Refusing is a correct, expected answer.
6. Do not state a confidence score. Do not claim you verified anything.
"""

USER_INSTRUCTION = """이 시트의 현시도 밴드입니다. 각 현시(Φ)에서 어느 접근로의 어느 이동류가
녹색을 받는지 판독하여 제안하십시오.

- 접근로 이름은 도면에 인쇄된 그대로(한글, 괄호 포함) 사용하십시오.
- 판독한 교차로번호와 교차로명을 함께 보고하십시오.
- 확실하지 않은 열은 해당 현시의 ambiguity에 한국어로 적고, 임의로 결정하지 마십시오.
- 읽을 수 없으면 refuse_phase_diagram을 호출하십시오."""


def _tools() -> list[dict[str, Any]]:
    """The two tools, as JSON Schema.

    The schemas are written against
    ``services.genesis.phase_diagram``'s own models — ``TURNS`` and ``SIDES`` are
    imported rather than re-listed, so a vocabulary change cannot leave the
    prompt describing one closed set while the validator enforces another.
    """
    movement = {
        "type": "object",
        "properties": {
            "approach_label": {
                "type": "string",
                "description": "The leg's name, VERBATIM as printed on the drawing.",
            },
            "turn": {"type": "string", "enum": list(TURNS)},
            "note": {"type": "string", "description": "Optional. Korean."},
        },
        "required": ["approach_label", "turn"],
    }
    return [
        {
            "name": TOOL_REPORT,
            "description": (
                "Report the 현시도 you read: the intersection's identity, its approach legs, "
                "and per phase the movements that receive green."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "read_intersection_no": {
                        "type": "string",
                        "description": "교차로번호 exactly as printed in the header.",
                    },
                    "read_intersection_name": {"type": "string"},
                    "approaches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "side": {
                                    "type": "string",
                                    "enum": list(SIDES),
                                    "description": (
                                        "Where the leg is DRAWN in the sketch. Not a compass "
                                        "bearing: the sketch states no north."
                                    ),
                                },
                                "note": {"type": "string"},
                            },
                            "required": ["label", "side"],
                        },
                    },
                    "phases": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "phase_id": {
                                    "type": "string",
                                    "description": "As labelled: 'Φ1', 'Φ2', …",
                                },
                                "movements": {"type": "array", "items": movement},
                                "serves_pedestrian": {"type": "boolean"},
                                "ambiguity": {
                                    "type": "string",
                                    "description": (
                                        "Korean. Anything about this column you could not "
                                        "resolve. Leave out only if there is nothing."
                                    ),
                                },
                            },
                            "required": ["phase_id", "movements"],
                        },
                    },
                    "notes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["approaches", "phases"],
            },
        },
        {
            "name": TOOL_REFUSE,
            "description": (
                "Decline to read this band. Use whenever the drawing is degraded, rotated, not "
                "a 현시도, or ambiguous enough that a reading would be a guess."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "A short stable slug, e.g. 'illegible_arrows'.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Korean. What specifically stopped you.",
                    },
                },
                "required": ["code", "reason"],
            },
        },
    ]


def propose_phase_diagram(
    png: bytes,
    *,
    render: DiagramRender,
    provider: Any | None = None,
    model: str | None = None,
) -> PhaseProposal:
    """One image → one :class:`PhaseProposal`. Always an artifact, never a crash.

    ``provider`` is injected in tests (anything with ``generate_structured``);
    production leaves it ``None`` and gets the product's ``ClaudeProvider``.

    The returned proposal's ``image_sha256`` is computed from the bytes THIS call
    sent, not copied from ``render`` — so a caller that passes an image and a
    provenance record that disagree produces a proposal whose sha matches neither
    the render nor any attestation, and the mismatch surfaces at confirm time
    instead of becoming a confirmation of the wrong picture.
    """
    image_sha = sha256_hex(png)
    if provider is None:
        from services.claude_provider import ClaudeProvider

        provider = ClaudeProvider(model=model)

    status = provider.status() if hasattr(provider, "status") else {"ready": True}
    if not status.get("ready", False):
        return _refused(
            REFUSAL_PROVIDER_UNAVAILABLE,
            (
                "이미지 판독 모델을 사용할 수 없어 제안을 생성하지 못했습니다"
                f" (상태: {status.get('state', 'unknown')}). 빈 양식으로 직접 입력하십시오."
            ),
            render=render,
            image_sha=image_sha,
            model=str(status.get("model") or getattr(provider, "model", "unknown")),
        )

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(png).decode("ascii"),
                    },
                },
                {"type": "text", "text": USER_INSTRUCTION},
            ],
        }
    ]

    action, meta = provider.generate_structured(
        messages,
        _tools(),
        None,
        system=SYSTEM_PROMPT,
        max_tokens=MAX_TOKENS,
    )
    used_model = str(meta.get("model") or getattr(provider, "model", "unknown"))
    usage = {k: v for k, v in meta.items() if k in ("mode", "fallback_rung")}

    if action is None:
        # The provider's own ladder is exhausted. Its ``error`` is carried
        # verbatim: a refusal a reviewer cannot diagnose is a refusal they
        # cannot act on.
        return _refused(
            REFUSAL_PROVIDER_FAILED,
            f"이미지 판독 호출이 실패했습니다: {meta.get('error') or 'unknown error'}",
            render=render,
            image_sha=image_sha,
            model=used_model,
            usage=usage,
        )

    tool = action.get("tool")
    params = action.get("params") or {}

    if tool == TOOL_REFUSE:
        return _refused(
            str(params.get("code") or REFUSAL_MODEL_DECLINED),
            str(params.get("reason") or "판독 모델이 이유를 밝히지 않고 거부했습니다."),
            render=render,
            image_sha=image_sha,
            model=used_model,
            usage=usage,
        )

    if tool != TOOL_REPORT:
        return _refused(
            REFUSAL_INVALID_PAYLOAD,
            f"판독 모델이 알 수 없는 도구 {tool!r}을(를) 호출했습니다.",
            render=render,
            image_sha=image_sha,
            model=used_model,
            usage=usage,
        )

    try:
        proposal = PhaseProposal(
            outcome="proposed",
            read_intersection_no=_clean(params.get("read_intersection_no")),
            read_intersection_name=_clean(params.get("read_intersection_name")),
            approaches=[
                ProposedApproach(
                    label=str(entry.get("label", "")).strip(),
                    side=_one_of(entry.get("side"), SIDES, "unknown"),
                    note=_clean(entry.get("note")),
                )
                for entry in _as_list(params.get("approaches"))
                if str(entry.get("label", "")).strip()
            ],
            phases=[
                ProposedPhase(
                    phase_id=str(entry.get("phase_id", "")).strip(),
                    movements=[
                        ProposedMovement(
                            approach_label=str(movement.get("approach_label", "")).strip(),
                            turn=_one_of(movement.get("turn"), TURNS, "unknown"),
                            note=_clean(movement.get("note")),
                        )
                        for movement in _as_list(entry.get("movements"))
                        if str(movement.get("approach_label", "")).strip()
                    ],
                    serves_pedestrian=entry.get("serves_pedestrian")
                    if isinstance(entry.get("serves_pedestrian"), bool)
                    else None,
                    ambiguity=_clean(entry.get("ambiguity")),
                )
                for entry in _as_list(params.get("phases"))
                if str(entry.get("phase_id", "")).strip()
            ],
            notes=[str(note) for note in _as_list(params.get("notes")) if str(note).strip()],
            model=used_model,
            image_sha256=image_sha,
            source_file=render.source_file,
            proposed_at=_now_iso(),
            usage=usage,
        )
    except Exception as exc:  # noqa: BLE001 - pydantic's own message IS the reason
        # Includes the "proposed but empty" case, which PhaseProposal rejects by
        # design: a reading with no phases is a refusal that forgot to say so,
        # and it is recorded here as the refusal it is.
        logger.warning("phase-diagram proposal was structurally invalid: %s", exc)
        return _refused(
            REFUSAL_INVALID_PAYLOAD,
            f"판독 결과가 구조적으로 유효하지 않습니다: {exc}",
            render=render,
            image_sha=image_sha,
            model=used_model,
            usage=usage,
        )

    return proposal


def _refused(
    code: str,
    reason: str,
    *,
    render: DiagramRender,
    image_sha: str,
    model: str,
    usage: dict[str, Any] | None = None,
) -> PhaseProposal:
    return PhaseProposal(
        outcome="refused",
        refusal=ProposalRefusal(code=code, reason=reason),
        model=model,
        image_sha256=image_sha,
        source_file=render.source_file,
        proposed_at=_now_iso(),
        usage=usage or {},
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _one_of(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    """A closed-set value, or the fallback.

    A model that answers ``"straight"`` where the schema says ``"through"`` has
    said something usable, and mapping it to ``unknown`` keeps the ambiguity
    VISIBLE to the reviewer rather than either crashing the batch or silently
    picking the nearest member.
    """
    text = str(value).strip().lower() if value is not None else ""
    return text if text in allowed else fallback


__all__ = [
    "MAX_TOKENS",
    "REFUSAL_INVALID_PAYLOAD",
    "REFUSAL_MODEL_DECLINED",
    "REFUSAL_PROVIDER_FAILED",
    "REFUSAL_PROVIDER_UNAVAILABLE",
    "SYSTEM_PROMPT",
    "TOOL_REFUSE",
    "TOOL_REPORT",
    "USER_INSTRUCTION",
    "propose_phase_diagram",
]
