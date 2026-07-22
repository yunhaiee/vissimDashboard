from __future__ import annotations

import logging
import re
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from services.agent.toolkit import (
    ToolRegistry,
    default_registry,
    dispatch,
    render_contract,
    tool,
)


# ---------------------------------------------------------------------------
# Sample tools + params models used across tests. All params models forbid
# extras, as the decorator requires.
# ---------------------------------------------------------------------------


class GetLinkVolumeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    interval: int = 900


class SetSignalCycleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sc_no: str
    cycle: int


def _fresh_registry_with_tools() -> ToolRegistry:
    """An isolated registry with two sample tools registered."""
    registry = ToolRegistry()

    @tool(name="get_link_volume", tier="read", provider="vissim", params=GetLinkVolumeParams, registry=registry)
    def _get_link_volume(params: GetLinkVolumeParams) -> dict[str, Any]:
        """Fetch the current volume for a link."""
        return {"volume": 900, "link_id": params.link_id}

    @tool(name="set_signal_cycle", tier="write", provider="vissim", params=SetSignalCycleParams, registry=registry)
    def _set_signal_cycle(params: SetSignalCycleParams) -> dict[str, Any]:
        """Set a signal controller's cycle time."""
        return {"artifacts": {"version": 3}, "verification": {"readback": params.cycle}}

    return registry


ENVELOPE_KEYS = {
    "status",
    "error_type",
    "message",
    "artifacts",
    "suggested_fix",
    "verification",
    "data",
}


def _assert_well_formed_envelope(envelope: dict[str, Any]) -> None:
    assert set(envelope.keys()) == ENVELOPE_KEYS


# ---------------------------------------------------------------------------
# Registration: decorator semantics
# ---------------------------------------------------------------------------


def test_tool_decorator_registers_name_params_tier_provider_and_docstring():
    registry = _fresh_registry_with_tools()

    spec = registry.get("get_link_volume")

    assert spec is not None
    assert spec.name == "get_link_volume"
    assert spec.tier == "read"
    assert spec.provider == "vissim"
    assert spec.params_model is GetLinkVolumeParams
    assert spec.description == "Fetch the current volume for a link."


def test_duplicate_name_registration_raises():
    registry = _fresh_registry_with_tools()

    with pytest.raises(ValueError, match="already registered"):

        @tool(name="get_link_volume", tier="read", provider="vissim", params=GetLinkVolumeParams, registry=registry)
        def _dup(params: GetLinkVolumeParams) -> dict[str, Any]:
            """Duplicate."""
            return {}


def test_unknown_tier_raises_at_registration():
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="tier"):

        @tool(name="bogus_tier_tool", tier="delete", provider="vissim", params=GetLinkVolumeParams, registry=registry)  # type: ignore[arg-type]
        def _bogus(params: GetLinkVolumeParams) -> dict[str, Any]:
            """Bogus."""
            return {}


def test_decorator_refuses_params_model_that_does_not_forbid_extra():
    registry = ToolRegistry()

    class LooseParams(BaseModel):
        link_id: str

    with pytest.raises(TypeError, match="extra"):

        @tool(name="loose_tool", tier="read", provider="vissim", params=LooseParams, registry=registry)
        def _loose(params: LooseParams) -> dict[str, Any]:
            """Loose."""
            return {}

    assert registry.get("loose_tool") is None


def test_tool_without_registry_kwarg_registers_into_default_registry():
    class PingParams(BaseModel):
        model_config = ConfigDict(extra="forbid")

    before = set(default_registry.names())
    try:

        @tool(name="__test_ping__", tier="read", provider="test", params=PingParams)
        def _ping(params: PingParams) -> dict[str, Any]:
            """Ping."""
            return {"pong": True}

        assert "__test_ping__" in default_registry.names()
        assert set(default_registry.names()) - before == {"__test_ping__"}
    finally:
        default_registry._tools.pop("__test_ping__", None)


# ---------------------------------------------------------------------------
# dispatch(): validation is strict rejection, NOT key-filtering
# ---------------------------------------------------------------------------


def test_dispatch_unregistered_name_returns_unknown_tool_envelope():
    registry = _fresh_registry_with_tools()

    envelope = dispatch("get_link_volme", {"link_id": "101"}, registry=registry)  # typo

    _assert_well_formed_envelope(envelope)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "unknown_tool"
    assert "get_link_volme" in envelope["message"]
    # difflib should surface the correctly-spelled name as a candidate
    assert envelope["suggested_fix"] is not None
    assert "get_link_volume" in envelope["suggested_fix"]


def test_dispatch_missing_required_param_returns_invalid_params():
    registry = _fresh_registry_with_tools()

    envelope = dispatch("get_link_volume", {}, registry=registry)  # link_id missing

    _assert_well_formed_envelope(envelope)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "link_id" in envelope["message"]


def test_dispatch_mistyped_param_returns_invalid_params():
    registry = _fresh_registry_with_tools()

    envelope = dispatch(
        "set_signal_cycle", {"sc_no": "1", "cycle": "not-an-int"}, registry=registry
    )

    _assert_well_formed_envelope(envelope)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "cycle" in envelope["message"]


def test_dispatch_extra_key_is_rejected_not_silently_filtered():
    """The NOT-key-filtering proof: an extraneous param must cause a rejection,
    not be silently dropped before the tool ever runs (docs/phase2/DECISIONS.md)."""
    registry = _fresh_registry_with_tools()

    envelope = dispatch(
        "get_link_volume",
        {"link_id": "101", "totally_made_up_param": 42},
        registry=registry,
    )

    _assert_well_formed_envelope(envelope)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "totally_made_up_param" in envelope["message"]


def test_dispatch_valid_call_returns_ok_with_data_passed_through():
    registry = _fresh_registry_with_tools()

    envelope = dispatch("get_link_volume", {"link_id": "101"}, registry=registry)

    _assert_well_formed_envelope(envelope)
    assert envelope["status"] == "ok"
    assert envelope["error_type"] is None
    assert envelope["data"] == {"volume": 900, "link_id": "101"}


def test_dispatch_success_pulls_named_envelope_keys_out_of_data():
    registry = _fresh_registry_with_tools()

    envelope = dispatch("set_signal_cycle", {"sc_no": "1", "cycle": 90}, registry=registry)

    _assert_well_formed_envelope(envelope)
    assert envelope["status"] == "ok"
    assert envelope["artifacts"] == {"version": 3}
    assert envelope["verification"] == {"readback": 90}
    # nothing besides artifacts/verification was returned, so data is None
    assert envelope["data"] is None


def test_dispatch_tool_exception_is_caught_and_reported_without_traceback(caplog):
    registry = ToolRegistry()

    class NoopParams(BaseModel):
        model_config = ConfigDict(extra="forbid")

    @tool(name="boom", tier="run", provider="vissim", params=NoopParams, registry=registry)
    def _boom(params: NoopParams) -> dict[str, Any]:
        """Always fails."""
        raise RuntimeError("simulated failure, no secrets here")

    with caplog.at_level(logging.ERROR, logger="services.agent.toolkit"):
        envelope = dispatch("boom", {}, registry=registry)

    _assert_well_formed_envelope(envelope)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "Traceback" not in envelope["message"]
    assert "\n" not in envelope["message"]
    assert "RuntimeError" in envelope["message"]
    # the exception was logged via stdlib logging, not swallowed silently
    assert any("boom" in record.message for record in caplog.records)


def test_dispatch_raw_params_not_a_dict_returns_invalid_params():
    registry = _fresh_registry_with_tools()

    envelope = dispatch("get_link_volume", ["not", "a", "dict"], registry=registry)  # type: ignore[arg-type]

    _assert_well_formed_envelope(envelope)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# render_contract(): the anti-drift guard
# ---------------------------------------------------------------------------


def test_render_contract_lists_every_registered_tool_exactly_once_and_no_others():
    """The acceptance test's heart: render_contract's tool set must equal the
    registry's tool set exactly — no phantom tools, nothing missing. This
    must fail if a tool is ever added without going through the decorator
    (e.g. hand-appended to a prompt string elsewhere), since such a tool
    would not appear in the registry this asserts against."""
    registry = _fresh_registry_with_tools()

    contract = render_contract(registry=registry)

    headings = re.findall(r"^## (\S+)$", contract, flags=re.MULTILINE)

    # exactly once each
    assert len(headings) == len(set(headings))
    # exact set equality with the registry — no more, no less
    assert set(headings) == set(registry.names())


def test_render_contract_is_deterministic_registration_order():
    registry = _fresh_registry_with_tools()

    first = render_contract(registry=registry)
    second = render_contract(registry=registry)

    assert first == second
    headings = re.findall(r"^## (\S+)$", first, flags=re.MULTILINE)
    assert headings == list(registry.names())  # registration order preserved


def test_render_contract_contains_tier_provider_description_and_params_schema():
    registry = _fresh_registry_with_tools()

    contract = render_contract(registry=registry)

    assert "tier: read | provider: vissim" in contract
    assert "Fetch the current volume for a link." in contract
    assert '"link_id"' in contract
    # pydantic's per-field "title" noise should be pruned for prompt economy
    assert '"title"' not in contract


def test_render_contract_empty_registry_returns_empty_string():
    registry = ToolRegistry()

    assert render_contract(registry=registry) == ""
