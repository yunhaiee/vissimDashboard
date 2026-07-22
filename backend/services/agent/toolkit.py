"""Tool registry core: the single source of truth for agent tools.

The reference repo's #1 bug class was hand-maintained prompt/schema/dispatch
drift — phantom tools the planner believed existed, params silently stripped
before execution. This module kills that class structurally: every tool
registers once, in one place, via the :func:`tool` decorator, and both the
planner's tool contract (:func:`render_contract`) and the execution path
(:func:`dispatch`) are generated from that same registration. There is no
second place to keep in sync.

Tools are in-process typed functions (NOT MCP subprocesses). M1.2's read
tools and M2.3's write tools register through :func:`tool`; M1.3's planner
injects :func:`render_contract` verbatim into its system prompt.

Validation is strict by design (see docs/phase2/DECISIONS.md, dated entry for
this module): unknown, missing, or mistyped params are REJECTED with a
structured error, never silently key-filtered. A planner that hallucinates a
param name must get a signal it can learn from.

This module is intentionally stdlib + pydantic only. No COM. No imports from
assistant_router.
"""

from __future__ import annotations

import difflib
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

Tier = Literal["read", "write", "run"]
_VALID_TIERS = frozenset({"read", "write", "run"})

# Envelope keys a tool's return dict may set directly; anything else in the
# tool's result is "unrecognized result data" and is swept into the
# envelope's `data` key rather than silently dropped (see module docstring).
_PASSTHROUGH_RESULT_KEYS = ("message", "artifacts", "suggested_fix", "verification")


@dataclass(frozen=True)
class ToolSpec:
    """One registered tool's identity and metadata.

    ``description`` is the first line of the tool function's docstring —
    the one-line summary :func:`render_contract` puts in the planner prompt.
    """

    name: str
    fn: Callable[[BaseModel], dict[str, Any]]
    params_model: type[BaseModel]
    tier: Tier
    provider: str
    description: str


class ToolRegistry:
    """name -> :class:`ToolSpec`, in registration order.

    Duplicate name registration raises — a second tool silently shadowing
    the first is exactly the drift this module exists to prevent.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(
                f"Tool {spec.name!r} is already registered; duplicate tool "
                "names are not allowed (registry drift guard)"
            )
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Registered tool names, in registration order."""
        return list(self._tools.keys())

    def specs(self) -> tuple[ToolSpec, ...]:
        """Registered :class:`ToolSpec` values, in registration order."""
        return tuple(self._tools.values())

    def unregister(self, name: str) -> None:
        """Remove a tool by name. No-op if not registered.

        Production code never needs this (registration is one-way by
        design); it exists so tests that register into ``default_registry``
        (by omitting ``registry=``) can clean up without reaching into
        ``ToolRegistry`` internals.
        """
        self._tools.pop(name, None)


# The single production registry. M1.2/M2.3 tools register here by default;
# tests build isolated `ToolRegistry()` instances via the `registry=` kwarg
# on `tool`/`dispatch`/`render_contract` so they don't pollute this one.
default_registry = ToolRegistry()


def _first_docstring_line(fn: Callable[..., Any]) -> str:
    doc = inspect.getdoc(fn)
    if not doc:
        return ""
    first = doc.strip().splitlines()[0].strip()
    return first


def _require_forbids_extra(params_model: type[BaseModel], tool_name: str) -> None:
    """Refuse to register a params model that doesn't forbid extra keys.

    This is the anti-drift enforcement for tool authors: a params model that
    silently accepts (or ignores) unknown keys would defeat the whole point
    of strict, non-key-filtering validation at dispatch time.
    """
    if params_model.model_config.get("extra") != "forbid":
        raise TypeError(
            f"Tool {tool_name!r} params model {params_model.__name__!r} must set "
            'model_config = ConfigDict(extra="forbid") so unknown params are '
            "rejected rather than silently ignored (anti-drift enforcement)"
        )


def tool(
    *,
    name: str,
    tier: Tier,
    provider: str,
    params: type[BaseModel],
    registry: ToolRegistry | None = None,
) -> Callable[[Callable[[BaseModel], dict[str, Any]]], Callable[[BaseModel], dict[str, Any]]]:
    """Register a function as an agent tool.

    The decorated function must accept a single validated ``params`` instance
    (a ``params`` model instance) and return a plain ``dict`` (see
    :func:`dispatch` for how that dict becomes the envelope).

    Raises:
        ValueError: unknown ``tier``, or ``name`` already registered.
        TypeError: ``params`` does not forbid extra keys.
    """
    if tier not in _VALID_TIERS:
        raise ValueError(
            f"Unknown tier {tier!r} for tool {name!r}; must be one of "
            f"{sorted(_VALID_TIERS)}"
        )
    _require_forbids_extra(params, name)
    target_registry = registry if registry is not None else default_registry

    def decorator(
        fn: Callable[[BaseModel], dict[str, Any]]
    ) -> Callable[[BaseModel], dict[str, Any]]:
        spec = ToolSpec(
            name=name,
            fn=fn,
            params_model=params,
            tier=tier,
            provider=provider,
            description=_first_docstring_line(fn),
        )
        target_registry.register(spec)
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _envelope(
    *,
    status: str,
    error_type: str | None = None,
    message: str = "",
    artifacts: Any = None,
    suggested_fix: Any = None,
    verification: Any = None,
    data: Any = None,
) -> dict[str, Any]:
    """Build the 7-key envelope. Always these keys, in this order, no others."""
    return {
        "status": status,
        "error_type": error_type,
        "message": message,
        "artifacts": artifacts,
        "suggested_fix": suggested_fix,
        "verification": verification,
        "data": data,
    }


def _format_validation_errors(exc: ValidationError) -> str:
    """Compact, planner-legible rendering of pydantic errors: which field, why."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


_MAX_EXCEPTION_SUMMARY_LEN = 300


def _sanitize_exception_text(text: str) -> str:
    """Collapse an exception's text to a single-line, length-capped summary.

    A tool's exception message is arbitrary text we don't control — it may
    span multiple lines or be arbitrarily long. This is a structural
    sanitizer (not a "keep it short" convention): it collapses every run of
    whitespace (including newlines) to a single space and truncates past
    :data:`_MAX_EXCEPTION_SUMMARY_LEN` chars, so the envelope's ``message``
    can never smuggle in a multi-line blob (traceback-shaped or otherwise).
    """
    collapsed = " ".join(text.split())
    if len(collapsed) > _MAX_EXCEPTION_SUMMARY_LEN:
        collapsed = collapsed[: _MAX_EXCEPTION_SUMMARY_LEN - 3].rstrip() + "..."
    return collapsed


def _build_success_envelope(result: dict[str, Any]) -> dict[str, Any]:
    leftover = {
        key: value for key, value in result.items() if key not in _PASSTHROUGH_RESULT_KEYS
    }
    return _envelope(
        status="ok",
        error_type=None,
        message=result.get("message", ""),
        artifacts=result.get("artifacts"),
        suggested_fix=result.get("suggested_fix"),
        verification=result.get("verification"),
        data=leftover or None,
    )


def dispatch(
    name: str,
    raw_params: dict[str, Any],
    *,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Validate ``raw_params`` against ``name``'s registered params model,
    execute the tool, and return the standard 7-key envelope.

    Validation REJECTS unknown, missing, or mistyped params (structured
    ``invalid_params`` error) rather than filtering unknown keys out — see
    docs/phase2/DECISIONS.md for why. A tool raising an exception is caught
    and reported as ``tool_execution_error`` with a one-line message (no raw
    traceback, no secrets); the exception itself is logged via ``logging``.
    """
    target_registry = registry if registry is not None else default_registry
    spec = target_registry.get(name)
    if spec is None:
        candidates = difflib.get_close_matches(name, target_registry.names(), n=5)
        return _envelope(
            status="error",
            error_type="unknown_tool",
            message=f"Unknown tool {name!r}.",
            suggested_fix=candidates or None,
        )

    try:
        validated_params = spec.params_model.model_validate(raw_params)
    except ValidationError as exc:
        return _envelope(
            status="error",
            error_type="invalid_params",
            message=f"Invalid params for tool {name!r}: {_format_validation_errors(exc)}",
        )

    try:
        result = spec.fn(validated_params)
    except Exception as exc:  # noqa: BLE001 - a tool's failure must not crash dispatch
        logger.exception("Tool %r raised during execution", name)
        summary = _sanitize_exception_text(str(exc))
        return _envelope(
            status="error",
            error_type="tool_execution_error",
            message=f"Tool {name!r} raised {type(exc).__name__}: {summary}",
        )

    if not isinstance(result, dict):
        # A non-dict return is itself the "unrecognized result data" — put it
        # straight under `data` rather than routing it through
        # _build_success_envelope (which would wrap it a second time, since
        # that helper's job is splitting a *dict* result's own keys apart).
        return _envelope(status="ok", error_type=None, data=result)

    return _build_success_envelope(result)


# ---------------------------------------------------------------------------
# Planner-prompt contract
# ---------------------------------------------------------------------------


def _prune_schema_noise(schema: Any) -> Any:
    """Recursively strip pydantic schema keys that only bloat the prompt.

    Currently just ``"title"`` (pydantic emits one per model and per field;
    none of it is needed for a planner to construct valid params) — but
    ``"title"`` is ALSO a legal field name, and a field literally named
    ``title`` shows up as a key of a ``"properties"`` mapping (i.e. as data,
    not as schema noise). So ``"properties"`` gets special handling: its
    value is a field-name -> schema map whose keys are never filtered by
    name, no matter what they're called; only the *values* (each field's own
    schema) are recursed into and pruned normally.
    """
    if isinstance(schema, dict):
        pruned: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "title":
                continue
            if key == "properties" and isinstance(value, dict):
                pruned[key] = {
                    field_name: _prune_schema_noise(field_schema)
                    for field_name, field_schema in value.items()
                }
                continue
            pruned[key] = _prune_schema_noise(value)
        return pruned
    if isinstance(schema, list):
        return [_prune_schema_noise(item) for item in schema]
    return schema


def native_tool_defs(*, registry: ToolRegistry | None = None) -> list[dict[str, Any]]:
    """Render the registry as Anthropic-native tool definitions.

    The same single source of truth as :func:`render_contract`, in the shape
    ``generate_structured`` providers consume: ``name``, the one-line
    docstring ``description``, and the pruned params JSON schema as
    ``input_schema``. Deterministic: tools appear in registration order.
    M1.3's planner builds its provider tool list from this (plus its own
    planner-terminal actions, which are not registry tools).
    """
    target_registry = registry if registry is not None else default_registry
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": _prune_schema_noise(spec.params_model.model_json_schema()),
        }
        for spec in target_registry.specs()
    ]


def render_contract(*, registry: ToolRegistry | None = None) -> str:
    """Render the registry as the planner prompt's tool contract.

    Deterministic: tools appear in registration order. This is the single
    source of truth for the planner's tool section (M1.3 injects this string
    verbatim) — edit the registry to change the contract, never this
    function's output directly. Each tool renders as a ``## name`` heading
    followed by tier/provider, its one-line docstring description, and its
    params as a pruned, compact JSON schema.
    """
    target_registry = registry if registry is not None else default_registry
    lines: list[str] = []
    for spec in target_registry.specs():
        schema = _prune_schema_noise(spec.params_model.model_json_schema())
        schema_json = json.dumps(schema, separators=(",", ":"))
        lines.append(f"## {spec.name}")
        lines.append(f"tier: {spec.tier} | provider: {spec.provider}")
        if spec.description:
            lines.append(spec.description)
        lines.append(f"params: {schema_json}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + ("\n" if lines else "")
