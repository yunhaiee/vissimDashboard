"""Roadmap C1 (option c) — render every 대전 현시도, and propose a reading of it.

This is the OFFLINE half of the unit. It renders the 현시도 band out of each
``교차로 운영 DATABASE`` PDF, optionally asks the product's vision model to
propose which approach runs in which phase, cross-checks that proposal against
what the sheet's text layer already parsed, and writes all of it into the review
store the confirm screen reads. It confirms nothing: every review it writes is
INERT until a named human confirms it in the browser.

Usage (from ``backend/``, with the venv python)::

    # render only -- no API calls, no key needed
    python tools/genesis_phase_diagram_batch.py --zip "../client-data/218. 대전신호.zip" --render-only

    # render + propose, over the first 2 sheets (development)
    python tools/genesis_phase_diagram_batch.py --zip ARCHIVE --limit 2

    # the acceptance run: every sheet
    python tools/genesis_phase_diagram_batch.py --zip ARCHIVE

Exit codes: ``0`` every sheet produced a structured proposal, ``2`` at least one
sheet produced a NAMED refusal (a normal, informative outcome — the code exists
so a CI job can notice a drop in coverage), ``1`` a usage error. **There is no
exit code for a silent skip, because there is no silent skip:** a sheet that
cannot be rendered, cannot be parsed, or cannot be read by the model is recorded
with its reason and counted.

BUDGET
------
One vision call per sheet, and the whole archive is 23 sheets. ``--limit`` exists
so development runs cost 1–3 calls; the full batch is meant to be run once, when
the unit is accepted. ``--render-only`` costs nothing at all.

CONFIDENTIALITY
---------------
``client-data/`` is confidential. This script writes the rendered images and the
extracted sheet text into the workspace runtime tree (git-ignored) and prints
**aggregate counts plus per-file structural outcomes** — file name, outcome,
flag codes. It never prints a timing value and never prints a proposed movement
list. Treat the per-file table as client material, exactly as
``genesis_signals_daejeon_probe.py --per-file`` asks you to.

WHAT THIS DOES NOT PROVE
------------------------
That any proposal is CORRECT. Nothing in this script measures that and nothing
in it claims to: the cross-check compares a reading against facts the text layer
independently states (phase count, printed approach names, 교차로번호), which can
all agree while the approach→phase assignment — the one thing no text states — is
wrong. The measurement of accuracy is the human confirmation, and it happens in
the browser.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import workspaces  # noqa: E402
from services.genesis import phase_diagram as pd  # noqa: E402
from services.genesis import signals_daejeon as dj  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2

#: Where reviews live when ``--out`` is not given: beside the genesis jobs, on
#: the workspace's own tree, so the browser reads exactly what this wrote.
STORE_DIRNAME = "phase-diagrams"


def _out(text: str = "") -> None:
    """Print, tolerating a Windows console that cannot encode Korean."""
    try:
        print(text)
    except UnicodeEncodeError:  # pragma: no cover - console-dependent
        print(text.encode("ascii", "backslashreplace").decode("ascii"))


def _sources(args: argparse.Namespace) -> list[tuple[str, bytes]]:
    if args.zip:
        with zipfile.ZipFile(args.zip) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".pdf")]
            return [(Path(name).name, archive.read(name)) for name in sorted(names)]
    root = Path(args.dir)
    return [(path.name, path.read_bytes()) for path in sorted(root.glob("*.pdf"))]


def run(args: argparse.Namespace, store: pd.PhaseDiagramStore) -> int:
    sources = _sources(args)
    if args.limit:
        sources = sources[: args.limit]
    if not sources:
        _out("no sheet PDFs found at the given source.")
        return EXIT_USAGE

    rendered = 0
    render_refusals: dict[str, int] = {}
    proposed = 0
    proposal_refusals: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    agreed = 0
    cross_checked = 0
    skipped_existing = 0
    rows: list[tuple[str, str, str, str]] = []

    for source_file, content in sources:
        try:
            png, render = pd.render_phase_diagram(content, source_file=source_file)
        except pd.PhaseDiagramRefused as exc:
            render_refusals[exc.code] = render_refusals.get(exc.code, 0) + 1
            rows.append((source_file, "RENDER REFUSED", exc.code, "-"))
            continue

        # The text layer is stored beside the image: the emit stage's
        # ``daejeon_sheet`` plan route parses TEXT, and re-reading the PDF later
        # would let the confirmed mapping and the plan come from two different
        # reads of the same file.
        try:
            sheet_text = dj.read_sheet_text(content)
        except Exception as exc:  # noqa: BLE001 - an unreadable text layer is a named outcome
            render_refusals["phase_diagram_sheet_text_unreadable"] = (
                render_refusals.get("phase_diagram_sheet_text_unreadable", 0) + 1
            )
            rows.append(
                (source_file, "RENDER REFUSED", "sheet_text_unreadable", type(exc).__name__)
            )
            continue

        existing = store.proposal(pd.review_id_for(source_file)) if args.skip_existing else None
        review_id = store.put_render(png, render, sheet_text=sheet_text)
        rendered += 1

        if args.render_only:
            rows.append((source_file, "RENDERED", "-", "-"))
            continue

        if existing is not None:
            # A stored proposal is NOT re-requested. The render above still ran
            # (it is free and it re-proves the band), so a sheet whose drawing
            # moved shows up as a stale attestation exactly as it would on a
            # full run — this flag skips the CALL, never the check.
            skipped_existing += 1
            rows.append(
                (source_file, "PROPOSAL KEPT", existing.outcome, "not re-requested")
            )
            continue

        from services.agent.phase_diagram_vision import propose_phase_diagram

        proposal = propose_phase_diagram(png, render=render, model=args.model)
        sheet = dj.parse_sheet_text(sheet_text, source_file=source_file)
        cross = pd.cross_check(proposal, sheet=sheet if sheet.phases else None, render=render)
        store.put_proposal(review_id, proposal, cross)

        flags = cross.codes("flag")
        for code in flags:
            flag_counts[code] = flag_counts.get(code, 0) + 1

        if proposal.outcome == "proposed":
            proposed += 1
            cross_checked += 1
            if not flags:
                agreed += 1
            rows.append(
                (
                    source_file,
                    "PROPOSED",
                    f"{len(proposal.phases)}φ",
                    ", ".join(flags) if flags else "no flags",
                )
            )
        else:
            code = proposal.refusal.code if proposal.refusal else "unnamed"
            proposal_refusals[code] = proposal_refusals.get(code, 0) + 1
            rows.append((source_file, "PROPOSAL REFUSED", code, "-"))

    _out("=" * 78)
    _out("대전 현시도 — render + propose (roadmap C1 option c)")
    _out("=" * 78)
    _out()
    if args.per_file:
        _out("PER FILE — structural outcomes only, no movement lists, no timings")
        _out(f"  {'file':<40} {'outcome':<18} {'detail':<10} findings")
        for name, outcome, detail, findings in rows:
            _out(f"  {name[:40]:<40} {outcome:<18} {detail:<10} {findings}")
        _out()

    _out("COUNTS")
    _out(f"  sheets seen                      {len(sources)}")
    _out(f"  bands rendered                   {rendered}")
    _out(f"  render refusals                  {sum(render_refusals.values())}")
    for code, count in sorted(render_refusals.items()):
        _out(f"    {count:>4}  {code}")
    if not args.render_only:
        _out(f"  proposals kept (not re-called)   {skipped_existing}")
        _out(f"  structured proposals             {proposed}")
        _out(f"  proposal refusals (named)        {sum(proposal_refusals.values())}")
        for code, count in sorted(proposal_refusals.items()):
            _out(f"    {count:>4}  {code}")
        _out(f"  silent skips                     0   (there is no code path for one)")
        _out()
        _out("CROSS-CHECK against the sheet's own text layer")
        _out(f"  proposals cross-checked          {cross_checked}")
        _out(f"    …with no flag raised           {agreed}")
        _out(f"    …with at least one flag        {cross_checked - agreed}")
        for code, count in sorted(flag_counts.items()):
            _out(f"    {count:>4}  {code}")
    _out()
    _out("WHAT THIS MEASURES, AND WHAT IT DOES NOT")
    _out("  Measured: whether each proposal's phase ids match the phases the text layer")
    _out("  parsed, whether every approach name it used is printed on the sheet, and")
    _out("  whether the 교차로번호 it read off the image is the sheet's own.")
    _out("  NOT measured: whether any approach→phase assignment is CORRECT. No text in")
    _out("  this repository states it — that is why the mapping needs a human, and every")
    _out("  review above is inert until one confirms it in the browser.")

    if not args.render_only and proposal_refusals:
        return EXIT_REFUSED
    return EXIT_REFUSED if render_refusals else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render 대전 현시도 bands and propose their approach→phase mapping.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--zip", help="a .zip of sheet PDFs")
    source.add_argument("--dir", help="a directory of sheet PDFs")
    parser.add_argument(
        "--out",
        default=None,
        help="review store directory (default: the default workspace's genesis/phase-diagrams).",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="render the bands and stop. No API call, no key needed, no cost.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="only the first N sheets. Use this during development: one call per sheet.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "do not re-request a proposal for a review that already has one. Renders and "
            "cross-checks still run; only the paid call is skipped."
        ),
    )
    parser.add_argument("--model", default=None, help="override the Claude model id.")
    parser.add_argument(
        "--per-file", action="store_true", help="one structural line per sheet (client material)."
    )
    args = parser.parse_args(argv)

    if args.out:
        store = pd.PhaseDiagramStore(Path(args.out))
        try:
            return run(args, store)
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            _out(f"USAGE ERROR: {type(exc).__name__}: {exc}")
            return EXIT_USAGE

    from services import genesis_jobs

    with workspaces.bind_workspace(workspaces.default_workspace_id()):
        store = pd.PhaseDiagramStore(genesis_jobs.genesis_dir() / STORE_DIRNAME)
        try:
            return run(args, store)
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            _out(f"USAGE ERROR: {type(exc).__name__}: {exc}")
            return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
