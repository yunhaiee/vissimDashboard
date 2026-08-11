"""Tests for ``tools/probe_reference_contexts.py`` — the measurement behind V2's
context-keyed reference table, and the self-check that keeps it honest.

The tool had no tests at all until 2026-08-11, which is how two defects survived
in the thing whose entire job is catching drift:

* ``--audit`` promised "every registered context must still resolve" and only
  ever looked at the 41 SIMPLE contexts. The 7 pair-shaped ones — the backbone
  of V4's connector-lane checks and V5's head integrity, ~15 % of the table —
  were never validated, so one going stale would still have printed a clean bill
  of health.
* It exited non-zero on a completely default invocation, reporting 7 of 41
  contexts BROKEN. Every one of those was a file with the whole target LAYER
  stripped (this repository's own diagnostic fragments), which is evidence about
  the file and none at all about the table. A self-check that is red on a
  healthy tree is a self-check everybody learns to ignore.

So the distinction the audit now draws — BROKEN (the file declares the target
class and the id is not among its members: the table names the wrong id space)
versus INCONCLUSIVE (the file declares no such object at all) — is what these
tests pin, in both directions, for both halves of the table.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT / "tools") not in sys.path:  # pragma: no cover - import path
    sys.path.insert(0, str(BACKEND_ROOT / "tools"))

from services.genesis import gate as gate_module
from tools import probe_reference_contexts as probe


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<network version="1403" vissimVersion="2026.00-09">{body}</network>\n',
        encoding="utf-8",
        newline="",
    )
    return path


_LINK = '<links><link displayType="{display}" no="1"><lanes><lane/></lanes></link></links>'
_DISPLAY_TYPES = '<displayTypes><displayType name="Road" no="1"/></displayTypes>'


# ---------------------------------------------------------------------------
# A simple context: wrong id space vs. a stripped layer
# ---------------------------------------------------------------------------


def test_a_value_missing_from_a_declared_target_class_is_broken(tmp_path, capsys):
    """The only shape that is evidence AGAINST the table: the file has the
    container the table points at, and the id is not in it."""
    path = _write(tmp_path, "wrong.inpx", _DISPLAY_TYPES + _LINK.format(display=9))
    exit_code = probe.audit([path])
    out = capsys.readouterr().out
    assert "BROKEN links/link/@displayType" in out
    assert exit_code == 1


def test_a_stripped_target_layer_is_inconclusive_and_does_not_fail_the_audit(
    tmp_path, capsys
):
    """The same unresolved value in a file that declares NO display type at all.

    The gate still reports it as an unresolved reference — correctly, it is one.
    The AUDIT is asking a different question ("does the table still name the
    right id space?") and this file cannot answer it either way.
    """
    path = _write(tmp_path, "partial.inpx", _LINK.format(display=1))
    exit_code = probe.audit([path])
    out = capsys.readouterr().out
    assert "BROKEN" not in out
    assert "INCONCLUSIVE" in out and "links/link/@displayType" in out
    assert "partial.inpx" in out, "an inconclusive row must name the file it came from"
    assert exit_code == 0


def test_the_default_corpus_audit_is_green_or_says_exactly_why(tmp_path, capsys):
    """A healthy tree audits clean. Guarded so the test runs anywhere: the
    default corpus needs PTV's example libraries installed, and without them
    this asserts the same property over the repository's own networks."""
    files = probe.corpus(list(probe.DEFAULT_ROOTS))
    if not files:  # pragma: no cover - depends on the machine
        return
    exit_code = probe.audit(files)
    out = capsys.readouterr().out
    assert exit_code == 0, out
    assert "BROKEN" not in out


# ---------------------------------------------------------------------------
# The pair contexts --audit never looked at
# ---------------------------------------------------------------------------


_SIGNAL_HEAD = (
    '<signalControllers><signalController no="1"><sgs>'
    '<signalGroup no="1"/></sgs></signalController></signalControllers>'
    '<signalHeads><signalHead lane="1 1" no="1" sg="{sg}"/></signalHeads>'
)


def test_audit_now_validates_the_pair_contexts_it_only_ever_promised_to(
    tmp_path, capsys
):
    """``signalHeads/signalHead/@sg`` names controller 9 in a file that declares
    controller 1. Before this, ``--audit`` printed '41 of 41' and exited 0."""
    path = _write(tmp_path, "pair.inpx", _SIGNAL_HEAD.format(sg="9 1"))
    exit_code = probe.audit([path])
    out = capsys.readouterr().out
    assert "BROKEN signalHeads/signalHead/@sg" in out
    assert exit_code == 1


def test_a_malformed_pair_is_broken_whatever_the_file_declares(tmp_path, capsys):
    """A pair reference's SHAPE is the table's claim, so a single-token value is
    a table question even in a file with no controllers to resolve against."""
    path = _write(
        tmp_path,
        "malformed.inpx",
        '<signalHeads><signalHead lane="1 1" no="1" sg="9"/></signalHeads>',
    )
    exit_code = probe.audit([path])
    assert "BROKEN signalHeads/signalHead/@sg" in capsys.readouterr().out
    assert exit_code == 1


def test_a_pair_whose_owner_class_is_absent_is_inconclusive(tmp_path, capsys):
    path = _write(
        tmp_path,
        "no-controllers.inpx",
        '<signalHeads><signalHead lane="1 1" no="1" sg="9 1"/></signalHeads>',
    )
    exit_code = probe.audit([path])
    out = capsys.readouterr().out
    assert "BROKEN" not in out
    assert "INCONCLUSIVE" in out
    assert exit_code == 0


def test_the_audit_reports_coverage_of_both_halves_of_the_table(tmp_path, capsys):
    path = _write(
        tmp_path,
        "both.inpx",
        _DISPLAY_TYPES + _LINK.format(display=1) + _SIGNAL_HEAD.format(sg="1 1"),
    )
    probe.audit([path])
    out = capsys.readouterr().out
    assert f"of {len(gate_module._SIMPLE_REFERENCES)}" in out
    assert f"of {len(gate_module._PAIR_REFERENCES)}" in out


# ---------------------------------------------------------------------------
# The numbers the table's own documentation quotes
# ---------------------------------------------------------------------------


def _gate_table_comment() -> str:
    """The block comment above ``_SIMPLE_REFERENCES`` — not a docstring, so it is
    read off the source the same way the frontend contract tests pin theirs."""
    source = Path(gate_module.__file__).read_text(encoding="utf-8")
    start = source.index("# THE CORPUS")
    end = source.index("_SIMPLE_REFERENCES: dict")
    return source[start:end]


def test_the_documented_corpus_is_one_number_and_its_parts_add_up():
    """The table's own comment gave FIVE different sizes for one corpus (791,
    404, 804, 825 and 188 all named the same measurement), so a maintainer had
    no way to tell which claim was current. It now names one total and its four
    parts, and this pins that they still agree — the arithmetic is what makes a
    half-updated number impossible to miss."""
    comment = _gate_table_comment()
    total = int(re.search(r"\*\*(\d+) real ``\.inpx`` files\*\*", comment).group(1))
    parts = [
        int(re.search(pattern, comment).group(1))
        for pattern in (
            r"(\d+) from PTV's own Vissim 2026",
            r"(\d+) from the Vissim 2024",
            r"(\d+) of this",
            r"(\d+) hand-authored",
        )
    ]
    assert sum(parts) == total, f"{parts} do not add up to {total}"
    assert total == 825
    for stale in ("404 real", "791 files", "188 files", "1,351"):
        assert stale not in comment


def test_each_unregistered_site_states_the_reason_it_was_actually_left_out():
    """``sectFilt``/``evalGrps`` were justified as "three files each" in both
    ``gate.py`` and the probe; measured on the current corpus they occur in 6 and
    17. And ``walkPedClasses`` was left out for LOW VOLUME, not for the stated
    ambiguity — it has exactly one unanimous candidate. A wrong reason is a
    decision nobody can re-check, and both files have to tell the same story."""
    for source in (_gate_table_comment(), probe.__doc__ or ""):
        assert "three files each" not in source
        assert "walkPedClasses" in source
        assert "sectFilt" in source and "evalGrps" in source
        # the reason it was excluded, in whichever words each file uses
        assert "MIN_DISTINCT_VALUES" in source or "distinct value" in source
