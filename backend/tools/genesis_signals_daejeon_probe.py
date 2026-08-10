"""Roadmap 3.1 (Korean half) — measure what the 대전 signal sheets actually yield.

Runs ``services.genesis.signals_daejeon`` over a zip (or a directory) of
``교차로 운영 DATABASE`` PDFs and prints the coverage table, so the measurement
can be re-run by anyone instead of taken on trust.

Usage (from ``backend/``, with the venv python)::

    python tools/genesis_signals_daejeon_probe.py --zip "../client-data/218. 대전신호.zip"
    python tools/genesis_signals_daejeon_probe.py --dir ../some/pdf/folder
    python tools/genesis_signals_daejeon_probe.py --zip ARCHIVE --per-file
    python tools/genesis_signals_daejeon_probe.py --zip ARCHIVE --declare-amber 3.0 \
        --amber-note "client email 2026-xx-xx: YELLOW(RED) is 황색+전적색"

Exit codes: ``0`` every sheet parsed, ``2`` at least one sheet refused, ``1`` a
usage error. A refusal is a normal, informative outcome here — the exit code
distinguishes it so a CI job can notice a drop in coverage, not so anyone treats
it as a crash.

CONFIDENTIALITY
---------------
``client-data/`` is confidential client material. **This script never prints a
client timing value** — not a cycle, not a split, not a clearance. What it
prints is: aggregate coverage, issue-code histograms, and — only with
``--sample`` — one sheet's SHAPE with every digit replaced by ``#`` and every
name withheld.

``--per-file`` additionally prints each file's name and its 교차로번호 next to
its structural counts, because a coverage table nobody can act on is not worth
printing; those are identifiers, still never values. Treat ``--per-file`` output
as client material and keep it out of the repo, tickets and chat. The default
output (no ``--per-file``, no ``--sample``) is safe to paste anywhere, and
adding a value-printing flag would destroy that property; do not.

WHAT THIS SCRIPT TOUCHES
------------------------
The archive or directory it is given, read-only. No ``runtime/``, no
``data/``, no workspace binding, no network, and no Vissim COM — the parser is
pure python and this probe stays that way. Reading the PDFs needs ``pypdf``;
``--from-text`` takes already-extracted text and needs nothing at all.

WHAT IT DOES NOT PROVE
----------------------
That Vissim loads anything. ``bridge_ready`` counts sheets whose numbers survive
every offline check and reach a ``FixedTimePlan``; turning one into a loaded
network needs the approach→phase assignment (a drawing, not text) and a live
Vissim. This repository has been bitten before by treating an offline round trip
as proof of loading, and the table below is worded so it cannot be misread that
way.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.dataimport.base import AdapterError, IssueSeverity  # noqa: E402
from services.genesis import signals_daejeon as dj  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2

#: Everything that looks like a number becomes ``#`` in the redacted sample.
_DIGITS = re.compile(r"\d")


def _out(text: str = "") -> None:
    """Print, tolerating a Windows console that cannot encode Korean."""
    try:
        print(text)
    except UnicodeEncodeError:  # pragma: no cover - console-dependent
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def _load(args: argparse.Namespace) -> list[dj.DaejeonSheet]:
    if args.from_text:
        root = Path(args.from_text)
        return [
            dj.parse_sheet_text(p.read_text(encoding="utf-8"), source_file=p.name)
            for p in sorted(root.glob("*.txt"))
        ]
    if args.zip:
        return dj.parse_zip(args.zip)
    root = Path(args.dir)
    return [dj.parse_pdf(p, source_file=p.name) for p in sorted(root.glob("*.pdf"))]


def _print_per_file(sheets: list[dj.DaejeonSheet]) -> None:
    _out("PER FILE — structural counts only, no timing values")
    _out(
        f"  {'file':<34} {'ok':<4} {'id':<7} {'Φ':<3} {'lvl':<4} {'plan':<5} "
        f"{'tod':<4} {'prog':<5} findings"
    )
    for sheet in sheets:
        codes = sorted(
            set(sheet.issue_codes(IssueSeverity.REFUSE))
            | set(sheet.issue_codes(IssueSeverity.WARN))
        )
        programs = len(sheet.controller.programs) if sheet.controller else 0
        _out(
            f"  {sheet.source_file[:34]:<34} {'yes' if sheet.ok else 'NO':<4} "
            f"{(sheet.intersection_no or '-'):<7} {sheet.phase_count:<3} "
            f"{len(sheet.levels):<4} {len(sheet.plans):<5} {len(sheet.tod_entries):<4} "
            f"{programs:<5} {', '.join(codes) if codes else '-'}"
        )
    _out()


def _print_coverage(measured: dj.Coverage, declared: bool) -> None:
    total = measured.sheets or 1
    _out("COVERAGE")
    _out(f"  sheets found                     {measured.sheets}")
    _out(
        f"  parsed (no refusal)              {measured.parsed}"
        f"   ({100 * measured.parsed / total:.0f}%)"
    )
    _out(f"    …fully parsed, nothing missing  {measured.fully_parsed}")
    _out(f"    …partially parsed, gaps named   {measured.partially_parsed}")
    _out(f"  refused                          {measured.refused}")
    _out(f"  neutral controllers produced     {measured.controllers}")
    _out(f"  declared signal programs         {measured.programs}")
    _out()
    _out("  BRIDGE TO A VISSIM FIXED-TIME PROGRAM")
    _out(f"    plans produced                 {measured.bridge_ready}")
    _out(
        f"    blocked ONLY by the unstated   {measured.bridge_blocked_only_by_amber}"
    )
    _out("      yellow/all-red split")
    if not declared:
        _out(
            "    (0 plans is the CORRECT number without --declare-amber: the sheet has "
            "one\n     YELLOW(RED) row and no 전적색 row, and no default is substituted.)"
        )
    _out()
    if measured.phase_count_histogram:
        hist = ", ".join(f"{n}φ×{c}" for n, c in measured.phase_count_histogram)
        _out(f"  phase counts                     {hist}")
    if measured.min_green_checked:
        pct = 100 * measured.min_green_corroborations / measured.min_green_checked
        _out(
            f"  MIN. GREEN == 시간분할 − YELLOW(RED)   "
            f"{measured.min_green_corroborations}/{measured.min_green_checked} "
            f"(sheet, phase) pairs ({pct:.0f}%)"
        )
        _out(
            "    — evidence about WHICH reading of YELLOW(RED) is right (MIN. GREEN is a"
        )
        _out(
            "      green, so a match says the subtracted quantity is the whole clearance)."
        )
        # Which sentence is true depends on whether a reviewer has declared an
        # amber. Printing "ask the client" once the client HAS answered — and
        # once every plan below is being built on that answer — would be the
        # report contradicting what the run just did.
        if not declared:
            _out("      Counted and reported; never acted on. Ask the client.")
        else:
            _out(
                "      The client answered (황색+전적색), which this evidence agrees with,"
            )
            _out(
                "      and the plans below are built on a DECLARED amber — a reviewer's"
            )
            _out("      engineering judgement, not a value any sheet states.")
    _out()
    if measured.refusal_reasons:
        _out("  REFUSAL REASONS (distinct findings, not occurrences)")
        for code, count in measured.refusal_reasons:
            _out(f"    {count:>4}  {code}")
    if measured.warning_reasons:
        _out("  WARNING REASONS")
        for code, count in measured.warning_reasons:
            _out(f"    {count:>4}  {code}")
    _out()


def _print_sample(sheets: list[dj.DaejeonSheet]) -> None:
    """One sheet's SHAPE, every digit masked. Layout evidence, not data."""
    usable = next((s for s in sheets if s.ok and s.controller), None)
    if usable is None:
        _out("SAMPLE: no parsed sheet to show.")
        return
    controller = usable.controller
    program = controller.programs[0]
    _out("REDACTED SAMPLE — digits masked, names withheld")
    _out(f"  file                <withheld> ({Path(usable.source_file).suffix})")
    _out(f"  controller_id       {_DIGITS.sub('#', controller.controller_id)}")
    _out("  name                <withheld>")
    _out(f"  region              {controller.region}")
    _out(f"  programs            {len(controller.programs)}")
    _out(f"  program_id          {_DIGITS.sub('#', program.program_id)}")
    _out(f"  source_kind         {program.source_kind.value}")
    _out(
        f"  cycle_time_s        {_DIGITS.sub('#', f'{program.cycle_time_s:g}')} "
        "(digits masked)"
    )
    _out(f"  phases              {len(program.phases)}")
    first = program.phases[0]
    _out(f"  phase[0].phase_id   {first.phase_id}")
    _out(f"  phase[0].green_s    {first.green_s!r}   <- unstated by construction")
    _out(f"  phase[0].amber_s    {first.amber_s!r}   <- unstated by construction")
    _out(f"  phase[0].all_red_s  {first.all_red_s!r}   <- unstated by construction")
    _out(f"  phase[0].absent     {sorted(first.absent)}")
    _out(f"  controller.absent   {sorted(controller.absent)}")
    _out()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure 대전 교차로 운영 DATABASE signal-sheet coverage.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", help="a .zip of sheet PDFs")
    source.add_argument("--dir", help="a directory of sheet PDFs")
    source.add_argument(
        "--from-text",
        help="a directory of already-extracted .txt sheets (needs no pypdf)",
    )
    parser.add_argument(
        "--per-file", action="store_true", help="one structural line per sheet"
    )
    parser.add_argument(
        "--sample", action="store_true", help="print one redacted sheet's shape"
    )
    parser.add_argument(
        "--declare-amber",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "A REVIEWER DECLARATION of the yellow display, once someone has established "
            "whether the sheet's YELLOW(RED) column is 황색 alone or 황색+전적색. Without "
            "it the bridge refuses, which is the intended default."
        ),
    )
    parser.add_argument(
        "--amber-note",
        default=None,
        help="why --declare-amber is what it is; travels into the plan's provenance.",
    )
    args = parser.parse_args(argv)

    if args.amber_note and args.declare_amber is None:
        parser.error("--amber-note only means something with --declare-amber")

    settings = dj.DaejeonSettings(
        declared_amber_s=args.declare_amber, amber_declaration_note=args.amber_note
    )

    try:
        sheets = _load(args)
    except AdapterError as exc:
        _out(f"UNREADABLE INPUT: {exc}")
        return EXIT_USAGE
    except (OSError, ValueError) as exc:
        _out(f"USAGE ERROR: {type(exc).__name__}: {exc}")
        return EXIT_USAGE

    if not sheets:
        _out("no sheets found at the given source.")
        return EXIT_USAGE

    _out("=" * 78)
    _out("대전 교차로 운영 DATABASE — signal-sheet coverage")
    _out(f"adapter {dj.DAEJEON_SIGNAL_ADAPTER.name} v{dj.DAEJEON_SIGNAL_ADAPTER.version}")
    _out("=" * 78)
    _out()

    if args.per_file:
        _print_per_file(sheets)

    _print_coverage(
        dj.coverage(sheets, settings=settings), declared=args.declare_amber is not None
    )

    if args.sample:
        _print_sample(sheets)

    _out("WHAT THIS MEASURES, AND WHAT IT DOES NOT")
    _out("  Measured offline: the sheets were read and their arithmetic checked against")
    _out("  itself (Σ시간분할 == 주기 for every scheduled 수준/ID pair).")
    _out("  NOT measured: that PTV Vissim loads any of it. Two things are still missing")
    _out("  from the documents themselves —")
    _out("    1. the 현시도 (which approach runs in which phase) is a drawing, and")
    _out("       generate_node_signals requires it as caller input;")
    _out("    2. the yellow/all-red split, which the sheet never states.")
    return EXIT_OK if all(sheet.ok for sheet in sheets) else EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
