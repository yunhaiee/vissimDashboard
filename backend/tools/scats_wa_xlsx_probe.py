"""Roadmap "real observed counts" unit — measure what the real WA SCATS
detector-volume ``.xlsx`` workbooks actually yield.

Runs ``services.dataimport.adapters.scats_wa.SCATSWAAdapter`` over a zip (or a
directory) of real SCATS detector-volume workbooks
(``LM00398.2026-02.xlsx``-shaped: a ``Site`` tab + ``Hourly Volume``/``Quarter
Hourly Volume``) and prints a coverage table, so the measurement can be
re-run by anyone instead of taken on trust.

Usage (from ``backend/``, with the venv python)::

    python tools/scats_wa_xlsx_probe.py --zip "../client-data/Traffic signal data.zip"
    python tools/scats_wa_xlsx_probe.py --dir ../some/xlsx/folder
    python tools/scats_wa_xlsx_probe.py --zip ARCHIVE --per-file

Exit codes: ``0`` every workbook parsed with no refusal, ``2`` at least one
workbook refused, ``1`` a usage error. A refusal is a normal, informative
outcome here — the exit code distinguishes it so a CI job can notice a
coverage drop, not so anyone treats it as a crash.

CONFIDENTIALITY
----------------
``client-data/`` is CONFIDENTIAL client material. **This script never prints a
client volume value** — not a detector count, not a site total, not a single
cell. What it prints by default is aggregate coverage and issue-code
histograms only. ``--per-file`` additionally prints each workbook's file name,
TCS/site id, road name and detector count next to its structural counts — those
are IDENTIFIERS (the same class of fact the roadmap notes already state:
site ids, road names, detector counts), never a volume. Even so, treat
``--per-file`` output as client material and keep it out of the repo, tickets
and chat; the default output (no ``--per-file``) is safe to paste anywhere.

WHAT THIS SCRIPT TOUCHES
-------------------------
The archive or directory it is given, read-only, plus this process's own
temp-extraction directory when ``--zip`` is used (cleaned up on exit). No
``runtime/``, no ``data/``, no workspace binding, no network, and no Vissim
COM. Reading real workbooks needs ``openpyxl`` (already a backend dependency).

WHAT IT DOES NOT PROVE
------------------------
That a detector's volume can be attributed to a Vissim movement. A SCATS
detector number is local to its site; turning "site X detector N" into
"link L, movement M" needs the LMA/LMB site drawing, which this drop does not
contain (measured — see
``services.dataimport.adapters.scats_wa.build_turning_counts_from_detector_map``'s
docstring for exactly what was checked and ruled out). This probe reports
site-level detector coverage; it does not claim movement-level coverage.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - detached stream
    pass

from services.dataimport.adapters.scats_wa import (  # noqa: E402
    XLSX_SUFFIXES,
    SCATSWAAdapter,
)
from services.dataimport.base import AdapterError, ImportResult, IssueSeverity  # noqa: E402
from services.dataimport.quality import apply_quality_gate  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2


def _out(text: str = "") -> None:
    """Print, tolerating a Windows console that cannot encode some glyphs."""
    try:
        print(text)
    except UnicodeEncodeError:  # pragma: no cover - console-dependent
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def _load_paths(args: argparse.Namespace) -> tuple[list[Path], Path | None]:
    """Returns ``(workbook paths, temp dir to clean up or None)``."""
    if args.zip:
        temp_dir = Path(tempfile.mkdtemp(prefix="scats_wa_xlsx_probe_"))
        with zipfile.ZipFile(args.zip) as archive:
            names = [
                name
                for name in archive.namelist()
                if Path(name).suffix.lower() in XLSX_SUFFIXES and not name.startswith("__MACOSX")
            ]
            for name in names:
                archive.extract(name, temp_dir)
        paths = sorted((temp_dir / name) for name in names)
        return paths, temp_dir

    root = Path(args.dir)
    paths = sorted(
        p for p in root.iterdir() if p.is_file() and p.suffix.lower() in XLSX_SUFFIXES
    )
    return paths, None


class _WorkbookMeasurement:
    """One workbook's parse, reduced to what the coverage table needs —
    identifiers and counts only, never a volume value."""

    def __init__(self, path: Path, result: ImportResult) -> None:
        self.path = path
        self.result = result
        self.refused = bool(result.refusals)
        self.site_id: str | None = result.sites[0].site_id if result.sites else None
        self.road_name: str | None = result.sites[0].road_name if result.sites else None
        self.detector_count = len(result.controllers[0].detectors) if result.controllers else 0
        self.detector_count_records = len(result.detector_counts)
        self.blank_cell_issues = [
            issue for issue in result.issues if issue.code == "scats_xlsx_volume_blank_cells"
        ]
        self.blank_cells = sum(
            int(issue.context.get("blank_cells", 0)) for issue in self.blank_cell_issues
        )
        self.gap_issues = [
            issue for issue in result.issues if issue.code == "detector_count_interval_gaps"
        ]
        self.cross_check_ok = any(
            issue.code == "scats_xlsx_hourly_corroborates_quarter_hourly"
            for issue in result.issues
        )
        self.cross_check_mismatch = any(
            issue.code == "scats_xlsx_hourly_disagrees_with_quarter_hourly"
            for issue in result.issues
        )
        self.sheets_not_parsed: list[str] = []
        for issue in result.issues:
            if issue.code == "scats_xlsx_sheet_present_not_parsed":
                self.sheets_not_parsed.extend(issue.context.get("sheets_not_parsed") or [])


def _measure(paths: list[Path]) -> list[_WorkbookMeasurement]:
    measurements: list[_WorkbookMeasurement] = []
    for path in paths:
        adapter = SCATSWAAdapter()
        try:
            result = apply_quality_gate(adapter.parse([path]))
        except AdapterError as exc:
            # Genuinely unreadable bytes are a usage-level finding, not a data
            # quality one — surfaced in the per-file table as a hard refusal
            # with the exception text, never silently skipped.
            result = adapter.empty_result()
            result.source_files = [path.name]
            from services.dataimport.base import ImportIssue

            result.issues.append(
                ImportIssue(
                    severity=IssueSeverity.REFUSE,
                    code="scats_wa_xlsx_probe_unreadable",
                    message=f"{path.name}: {exc}",
                    subject=path.name,
                )
            )
        measurements.append(_WorkbookMeasurement(path, result))
    return measurements


def _print_per_file(measurements: list[_WorkbookMeasurement]) -> None:
    _out("PER FILE — identifiers and counts only, no volume values")
    _out(
        f"  {'file':<28} {'ok':<4} {'site_id':<10} {'road_name':<16} {'detectors':<9} "
        f"{'records':<8} {'blank':<6} {'gaps':<5} findings"
    )
    for m in measurements:
        codes = sorted(
            {issue.code for issue in m.result.issues if issue.severity is IssueSeverity.REFUSE}
            | {issue.code for issue in m.result.issues if issue.severity is IssueSeverity.WARN}
        )
        _out(
            f"  {m.path.name[:28]:<28} {'NO' if m.refused else 'yes':<4} "
            f"{(m.site_id or '-'):<10} {(m.road_name or '-')[:16]:<16} "
            f"{m.detector_count:<9} {m.detector_count_records:<8} {m.blank_cells:<6} "
            f"{len(m.gap_issues):<5} {', '.join(codes) if codes else '-'}"
        )
    _out()


def _print_coverage(measurements: list[_WorkbookMeasurement]) -> None:
    total = len(measurements) or 1
    parsed = [m for m in measurements if not m.refused]
    refused = [m for m in measurements if m.refused]

    _out("COVERAGE")
    _out(f"  workbooks found                  {len(measurements)}")
    _out(
        f"  parsed (no refusal)              {len(parsed)}   "
        f"({100 * len(parsed) / total:.0f}%)"
    )
    _out(f"  refused                          {len(refused)}")
    _out(f"  sites identified                 {sum(1 for m in measurements if m.site_id)}")
    _out(f"  total detectors (sum across sites) {sum(m.detector_count for m in measurements)}")
    _out(
        f"  total detector-count records     "
        f"{sum(m.detector_count_records for m in measurements)}"
    )
    _out()

    _out("  HOURLY <-> QUARTER HOURLY CROSS-CHECK (point-of-discard check; Hourly")
    _out("  Volume is read, cross-checked, then discarded rather than also emitted)")
    _out(f"    workbooks corroborated (0 mismatches)   "
         f"{sum(1 for m in measurements if m.cross_check_ok)}")
    _out(f"    workbooks with a disagreement (REFUSE)  "
         f"{sum(1 for m in measurements if m.cross_check_mismatch)}")
    _out()

    blank_total = sum(m.blank_cells for m in measurements)
    gap_total = sum(len(m.gap_issues) for m in measurements)
    _out("  DATA QUALITY")
    _out(f"    blank/unreadable detector cells (never defaulted to 0)  {blank_total}")
    _out(f"    (site, detector) series with a gap WARN                {gap_total}")
    _out()

    sheets_seen: dict[str, int] = {}
    for m in measurements:
        for sheet in m.sheets_not_parsed:
            sheets_seen[sheet] = sheets_seen.get(sheet, 0) + 1
    if sheets_seen:
        _out("  REAL TABS PRESENT BUT NOT PARSED BY THIS UNIT (named, not silently skipped)")
        for sheet, count in sorted(sheets_seen.items(), key=lambda item: (-item[1], item[0])):
            _out(f"    {count:>3} workbook(s)  {sheet}")
        _out()

    refuse_hist: dict[str, int] = {}
    warn_hist: dict[str, int] = {}
    for m in measurements:
        for issue in m.result.issues:
            if issue.severity is IssueSeverity.REFUSE:
                refuse_hist[issue.code] = refuse_hist.get(issue.code, 0) + 1
            elif issue.severity is IssueSeverity.WARN:
                warn_hist[issue.code] = warn_hist.get(issue.code, 0) + 1
    if refuse_hist:
        _out("  REFUSAL REASONS (distinct findings, not occurrences)")
        for code, count in sorted(refuse_hist.items(), key=lambda item: (-item[1], item[0])):
            _out(f"    {count:>4}  {code}")
    if warn_hist:
        _out("  WARNING REASONS")
        for code, count in sorted(warn_hist.items(), key=lambda item: (-item[1], item[0])):
            _out(f"    {count:>4}  {code}")
    _out()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure real WA SCATS detector-volume .xlsx workbook coverage.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", help="a .zip of real SCATS .xlsx workbooks")
    source.add_argument("--dir", help="a directory of real SCATS .xlsx workbooks")
    parser.add_argument(
        "--per-file",
        action="store_true",
        help="one line per workbook: site id, road name, detector count (identifiers, "
        "never a volume) — treat this output as client material",
    )
    args = parser.parse_args(argv)

    temp_dir: Path | None = None
    try:
        try:
            paths, temp_dir = _load_paths(args)
        except (OSError, zipfile.BadZipFile) as exc:
            _out(f"USAGE ERROR: {type(exc).__name__}: {exc}")
            return EXIT_USAGE

        if not paths:
            _out("no .xlsx/.xlsm workbook found at the given source.")
            return EXIT_USAGE

        measurements = _measure(paths)

        _out("=" * 78)
        _out("WA SCATS detector-volume workbooks — real .xlsx coverage")
        _out(f"adapter scats_wa (services.dataimport.adapters.scats_wa.SCATSWAAdapter)")
        _out("=" * 78)
        _out()

        if args.per_file:
            _print_per_file(measurements)

        _print_coverage(measurements)

        _out("THE DETECTOR -> MOVEMENT MAPPING VERDICT")
        _out("  No LMA/LMB site drawing exists in this drop, so a detector's volume can be")
        _out("  compared at SITE level only (this workbook's total vs a mapped Vissim node's")
        _out("  total movement volume) — never at movement level. See")
        _out("  services.dataimport.adapters.scats_wa.build_turning_counts_from_detector_map's")
        _out("  docstring for exactly what was checked (shapefile, turning-movement survey,")
        _out("  murdoch.osm) and ruled out, and for the caller-supplied-map upgrade path.")
        _out()
        _out("WHAT THIS MEASURES, AND WHAT IT DOES NOT")
        _out("  Measured offline: each workbook's Site tab + Hourly/Quarter Hourly Volume tabs")
        _out("  were read, cross-checked against each other, and passed through the shared")
        _out("  data-quality gate (services.dataimport.quality).")
        _out("  NOT measured: that these counts join to any specific Vissim network node — that")
        _out("  join is a site_nodes map the CALLER supplies to services.observations.observed_side,")
        _out("  never guessed here.")
        return EXIT_REFUSED if any(m.refused for m in measurements) else EXIT_OK
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
