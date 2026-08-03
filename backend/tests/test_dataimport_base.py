"""Tests for the adapter protocol + result transport (services/dataimport/base.py).

Covers the structural half of the contract: what an adapter must declare, how
issues and results behave, and how format sniffing picks a winner without ever
guessing. The behavioural half (never invent, report don't raise, preserve
provenance) is proven against a real adapter in test_dataimport_scats_wa.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest
from pydantic import ValidationError

import services.dataimport.base as base
from services.dataimport.base import (
    AdapterError,
    AdapterInfo,
    DataAdapter,
    ImportIssue,
    ImportResult,
    IssueSeverity,
    SniffResult,
    check_adapter_conformance,
    get_adapter,
    list_adapters,
    register_adapter,
    select_adapter,
    unregister_adapter,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Every test gets the production registry back exactly as it found it."""
    snapshot = dict(base._REGISTRY)
    yield
    base._REGISTRY.clear()
    base._REGISTRY.update(snapshot)


def _info(name: str = "fake") -> AdapterInfo:
    return AdapterInfo(name=name, region="XX", source_system="Test", version="0.0.1")


class _FakeAdapter(DataAdapter):
    def __init__(self, name: str = "fake", confidence: float = 0.9):
        self.info = _info(name)
        self._confidence = confidence

    def sniff(self, files: Sequence[Path]) -> SniffResult:
        return SniffResult(confidence=self._confidence, reason=f"{self.info.name} test sniff")

    def parse(self, files: Sequence[Path]) -> ImportResult:
        return self.empty_result()


class _ExplodingAdapter(_FakeAdapter):
    def sniff(self, files: Sequence[Path]) -> SniffResult:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# ImportIssue / ImportResult
# ---------------------------------------------------------------------------


def test_an_issue_needs_a_severity_a_code_and_a_message():
    with pytest.raises(ValidationError):
        ImportIssue(severity=IssueSeverity.WARN, code="x")
    with pytest.raises(ValidationError):
        ImportIssue(severity="catastrophe", code="x", message="y")
    issue = ImportIssue(severity=IssueSeverity.REFUSE, code="x", message="y")
    assert issue.citation is None and issue.context == {} and issue.messages == {}


def test_result_partitions_issues_by_severity_and_only_refusals_block():
    result = ImportResult(adapter=_info())
    assert result.ok is True

    result.add_issues(
        [
            ImportIssue(severity=IssueSeverity.INFO, code="i", message="noted"),
            ImportIssue(severity=IssueSeverity.WARN, code="w", message="odd"),
        ]
    )
    assert result.ok is True
    assert result.issue_codes(IssueSeverity.WARN) == ["w"]
    assert [issue.code for issue in result.infos] == ["i"]

    result.add_issues([ImportIssue(severity=IssueSeverity.REFUSE, code="r", message="no")])
    assert result.ok is False
    assert [issue.code for issue in result.refusals] == ["r"]
    assert sorted(result.issue_codes()) == ["i", "r", "w"]


def test_result_starts_empty_for_every_record_type():
    result = ImportResult(adapter=_info())
    assert result.sites == []
    assert result.controllers == []
    assert result.counts == []
    assert result.detector_counts == []


# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------


def test_a_well_formed_adapter_conforms():
    assert check_adapter_conformance(_FakeAdapter()) == []


def test_conformance_rejects_something_that_is_not_an_adapter():
    problems = check_adapter_conformance(object())
    assert problems and "not a DataAdapter" in problems[0]


def test_conformance_rejects_a_bad_info_declaration():
    adapter = _FakeAdapter()
    adapter.info = "scats"
    assert any("AdapterInfo" in problem for problem in check_adapter_conformance(adapter))


def test_conformance_flags_a_protocol_method_that_was_never_overridden(monkeypatch):
    adapter = _FakeAdapter()
    monkeypatch.setattr(_FakeAdapter, "sniff", DataAdapter.sniff)
    assert any("does not override sniff" in problem for problem in check_adapter_conformance(adapter))


def test_the_abstract_base_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        DataAdapter()


def test_adapter_info_is_frozen_so_a_result_cannot_be_retroactively_relabelled():
    with pytest.raises(ValidationError):
        _info().version = "9.9.9"


# ---------------------------------------------------------------------------
# Registry + selection
# ---------------------------------------------------------------------------


def test_registration_round_trip():
    adapter = _FakeAdapter("fake_a")
    register_adapter(adapter)
    assert get_adapter("fake_a") is adapter
    assert adapter in list_adapters()
    unregister_adapter("fake_a")
    assert get_adapter("fake_a") is None


def test_registering_a_non_conforming_adapter_is_refused():
    with pytest.raises(AdapterError, match="does not conform"):
        register_adapter(object())


def test_duplicate_registration_is_refused_unless_replacement_is_explicit():
    register_adapter(_FakeAdapter("fake_b"))
    with pytest.raises(AdapterError, match="already registered"):
        register_adapter(_FakeAdapter("fake_b"))
    replacement = _FakeAdapter("fake_b")
    register_adapter(replacement, replace=True)
    assert get_adapter("fake_b") is replacement


def test_select_adapter_picks_the_most_confident_and_reports_every_score():
    base._REGISTRY.clear()
    weak = _FakeAdapter("weak", confidence=0.55)
    strong = _FakeAdapter("strong", confidence=0.95)
    register_adapter(weak)
    register_adapter(strong)

    chosen, scores = select_adapter([Path("anything.csv")])
    assert chosen is strong
    assert [name for name, _ in scores] == ["strong", "weak"]
    assert all(score.reason for _, score in scores)


def test_select_adapter_refuses_to_guess_below_the_threshold():
    base._REGISTRY.clear()
    register_adapter(_FakeAdapter("unsure", confidence=0.2))
    chosen, scores = select_adapter([Path("mystery.dat")])
    assert chosen is None
    assert scores[0][1].confidence == 0.2


def test_one_broken_sniff_cannot_break_intake():
    base._REGISTRY.clear()
    register_adapter(_ExplodingAdapter("broken", confidence=1.0))
    good = _FakeAdapter("good", confidence=0.8)
    register_adapter(good)

    chosen, scores = select_adapter([Path("x.csv")])
    assert chosen is good
    broken_score = next(score for name, score in scores if name == "broken")
    assert broken_score.confidence == 0.0
    assert "RuntimeError" in broken_score.reason


def test_sniff_confidence_is_bounded():
    with pytest.raises(ValidationError):
        SniffResult(confidence=1.4, reason="over-eager")
    with pytest.raises(ValidationError):
        SniffResult(confidence=0.5, reason="")
