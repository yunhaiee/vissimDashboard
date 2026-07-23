"""Tests for M2.3's gates.py: signature canonicalization, ApprovalRequired,
ProposeWriteGate, and PreapprovedWriteGate.

Hand-rolled fixtures only (no unittest.mock), per repo precedent. ``spec`` is
never inspected by any of these gates (``PreapprovedWriteGate.authorize``
explicitly ``del``s it, ``ProposeWriteGate.authorize`` never reads it before
raising), so tests pass ``None`` for it rather than constructing a real
``ToolSpec``.
"""
from __future__ import annotations

import pytest

from services.agent.gates import (
    ApprovalRequired,
    PreapprovedWriteGate,
    ProposeWriteGate,
    signature,
)

# ---------------------------------------------------------------------------
# signature() — canonicalization
# ---------------------------------------------------------------------------


def test_signature_is_stable_across_param_key_order():
    a = signature("set_vehicle_input_volume", {"input_no": "1", "volume": 600})
    b = signature("set_vehicle_input_volume", {"volume": 600, "input_no": "1"})

    assert a == b


def test_signature_differs_by_tool_name():
    a = signature("set_vehicle_input_volume", {"input_no": "1", "volume": 600})
    b = signature("set_signal_cycle", {"input_no": "1", "volume": 600})

    assert a != b


def test_signature_differs_by_param_value():
    a = signature("set_vehicle_input_volume", {"input_no": "1", "volume": 600})
    b = signature("set_vehicle_input_volume", {"input_no": "1", "volume": 601})

    assert a != b


def test_signature_round_trips_unicode_literally_not_escaped():
    sig = signature("set_signal_cycle", {"sc_no": "1", "note": "교차로 신호"})

    assert "교차로 신호" in sig
    assert "\\u" not in sig  # ensure_ascii=False -- never \uXXXX-escaped


def test_signature_is_a_tool_newline_json_shape():
    sig = signature("set_random_seed", {"seed": 7})

    assert sig.startswith("set_random_seed\n")
    assert '"seed":7' in sig or '"seed": 7' in sig


# ---------------------------------------------------------------------------
# ApprovalRequired
# ---------------------------------------------------------------------------


def test_approval_required_carries_action_and_spec():
    action = {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 600}}
    spec = object()  # any sentinel -- gates never introspect it

    exc = ApprovalRequired(action, spec)

    assert exc.action is action
    assert exc.spec is spec
    assert "set_vehicle_input_volume" in str(exc)


# ---------------------------------------------------------------------------
# ProposeWriteGate — never returns, always raises
# ---------------------------------------------------------------------------


def test_propose_write_gate_always_raises_approval_required():
    gate = ProposeWriteGate()
    action = {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 600}}

    with pytest.raises(ApprovalRequired) as excinfo:
        gate.authorize(action, spec=None)

    assert excinfo.value.action == action


def test_propose_write_gate_raises_regardless_of_params_content():
    gate = ProposeWriteGate()

    with pytest.raises(ApprovalRequired):
        gate.authorize({"tool": "set_signal_offset", "params": {}}, spec=None)


# ---------------------------------------------------------------------------
# PreapprovedWriteGate — signature-set membership
# ---------------------------------------------------------------------------


def test_preapproved_write_gate_authorizes_exact_signature_match():
    action = {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 600}}
    gate = PreapprovedWriteGate({signature(action["tool"], action["params"])})

    assert gate.authorize(action, spec=None) is True


def test_preapproved_write_gate_authorizes_regardless_of_param_key_order():
    approved_sig = signature("set_vehicle_input_volume", {"input_no": "1", "volume": 600})
    gate = PreapprovedWriteGate({approved_sig})

    # Same action, params reconstructed in a DIFFERENT key order (e.g. the
    # planner re-emitted it) -- signature() canonicalizes this, so it still
    # matches.
    action = {"tool": "set_vehicle_input_volume", "params": {"volume": 600, "input_no": "1"}}

    assert gate.authorize(action, spec=None) is True


def test_preapproved_write_gate_refuses_a_different_action():
    approved = {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 600}}
    gate = PreapprovedWriteGate({signature(approved["tool"], approved["params"])})

    different_params = {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 601}}
    different_tool = {"tool": "set_signal_cycle", "params": {"sc_no": "1", "cycle": 90}}

    assert gate.authorize(different_params, spec=None) is False
    assert gate.authorize(different_tool, spec=None) is False


def test_preapproved_write_gate_refuses_when_signature_set_is_empty():
    gate = PreapprovedWriteGate(set())

    assert gate.authorize({"tool": "set_random_seed", "params": {"seed": 1}}, spec=None) is False


def test_preapproved_write_gate_membership_is_not_consumed_on_use():
    action = {"tool": "set_random_seed", "params": {"seed": 42}}
    gate = PreapprovedWriteGate({signature(action["tool"], action["params"])})

    assert gate.authorize(action, spec=None) is True
    assert gate.authorize(action, spec=None) is True  # still authorized -- not a one-shot token


def test_preapproved_write_gate_never_raises():
    # Unlike ProposeWriteGate, this gate always returns a bool.
    gate = PreapprovedWriteGate(set())
    result = gate.authorize({"tool": "anything", "params": {}}, spec=None)
    assert result is False
