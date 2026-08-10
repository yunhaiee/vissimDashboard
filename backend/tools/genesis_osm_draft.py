"""Operator CLI for roadmap 2.3 Genesis (a): a local ``.osm`` in, a draft
``.inpx`` + an MRWA §5 compliance report out.

Usage (run from ``backend/``, with the venv python)::

    python tools/genesis_osm_draft.py --osm murdoch.osm --out-dir ../out/murdoch
    python tools/genesis_osm_draft.py --osm murdoch.osm --out-dir OUT --name murdoch_v1
    python tools/genesis_osm_draft.py --osm murdoch.osm --out-dir OUT --lang en
    python tools/genesis_osm_draft.py --osm murdoch.osm --out-dir OUT --exclude-service

Measure whether the result is DRIVABLE (dangling link ends, and why)::

    python tools/genesis_connectivity_probe.py --inpx OUT/murdoch.inpx --osm murdoch.osm

Writes three files into ``--out-dir`` (created if missing):

* ``<name>.inpx`` — geometry only: links, connectors, node polygons.
* ``<name>.compliance.md`` — the three-state §5 report, ko primary
  (``--lang en|ja`` for the other two).
* ``<name>.genesis.json`` — the whole draft as data: metadata, projection,
  per-link lane provenance, every skipped way, every compliance item.

Exit codes: ``0`` generated, ``2`` refused (unreadable/undrivable ``.osm``, or a
bounding box outside the projection's validity radius — the refusal message
carries the numbers), ``1`` a usage error.

WHAT THIS SCRIPT TOUCHES
------------------------
The ``.osm`` it is given (read-only) and the ``--out-dir`` it is given. Nothing
else: no ``data/``, no ``runtime/``, no ``client-data/``, no database, no
network access and no Vissim COM.

**No workspace binding, deliberately.** The other ``backend/tools`` scripts open
with ``with bind_workspace(default_workspace_id())``; ``manage_users.py``
already refuses to, and the same argument applies here, doubled. First,
``default_workspace_id()`` is not the read-only "say which project I mean" it
looks like — it goes through ``ensure_default_workspace()``, which creates
``runtime/workspaces/<id>/``, writes the workspace registry and runs the
one-time legacy migration that MOVES ``runtime/uploads/...`` elsewhere on disk.
Turning an ``.osm`` into a draft must not relocate a consultant's dataset as a
side effect. Second, it is unnecessary: every path this script resolves comes
from an argument, and nothing in its call graph imports ``services.datasets``,
``services.workspaces`` or anything that does — ``services.genesis`` depends
only on ``services.inpx_parser`` and pydantic.

The output is a DRAFT. It has been round-tripped through this repository's own
parser and structural diff; it has **not** been opened by a real Vissim. The
compliance report repeats that at the bottom of every render.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.genesis.draft import build_draft, pick_text  # noqa: E402
from services.genesis.emit import emit_inpx  # noqa: E402
from services.genesis.osm import parse_osm_file  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2

_TEXT: dict[str, dict[str, str]] = {
    "refused": {
        "ko": "생성 거부:",
        "en": "Generation refused:",
        "ja": "生成を拒否:",
    },
    "wrote": {
        "ko": "생성 완료:",
        "en": "Wrote:",
        "ja": "生成完了:",
    },
    "summary": {
        "ko": "링크 {links}개 / 커넥터 {connectors}개 / 노드 {nodes}개 — §5 적용 {applied} · 위반 {violated} · 확인 불가 {not_checkable}",
        "en": "{links} links / {connectors} connectors / {nodes} nodes — §5 {applied} applied, {violated} violated, {not_checkable} NOT CHECKABLE",
        "ja": "リンク{links} / コネクタ{connectors} / ノード{nodes} — §5 適用{applied}・違反{violated}・確認不可{not_checkable}",
    },
    "violated_head": {
        "ko": "명시된 위반:",
        "en": "Named violations:",
        "ja": "明示された違反:",
    },
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="genesis_osm_draft",
        description=(
            "Local .osm -> draft Vissim .inpx (geometry only) + MRWA OMG v2.0 §5 "
            "compliance report. Offline: no network access, no Vissim, no COM."
        ),
    )
    parser.add_argument("--osm", required=True, help="path to a LOCAL .osm XML file")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="directory to write into (created if missing). Never data/ or runtime/.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="base name for the outputs (default: the .osm file's stem)",
    )
    parser.add_argument(
        "--lang",
        default="ko",
        choices=("ko", "en", "ja"),
        help="language of the compliance report and console output (ko primary)",
    )
    parser.add_argument(
        "--exclude-service",
        action="store_true",
        help=(
            "drop every highway=service way (car-park aisles, driveways, back lanes) "
            "as a NAMED skip. Default: service ways are modelled and connected like "
            "any other carriageway — see services.genesis.osm.SERVICE_HIGHWAY for the "
            "policy and why the default is what it is."
        ),
    )
    return parser.parse_args(argv)


def _make_console_lossy() -> None:
    """A legacy Windows console (cp949 here) cannot encode "§" or Korean text.

    Without this the script would die with ``UnicodeEncodeError`` *after* having
    written all three output files — the worst possible failure mode, since the
    exit code would say "refused" about work that succeeded. Degrade the console
    to replacement characters instead; the files themselves are always UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached stream
                pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _make_console_lossy()
    lang = args.lang
    osm_path = Path(args.osm)
    out_dir = Path(args.out_dir)
    name = args.name or osm_path.stem or "genesis_draft"

    extract = parse_osm_file(osm_path, exclude_service=args.exclude_service)
    draft = build_draft(extract, name=name)

    if draft.refused:
        print(pick_text(_TEXT["refused"], lang), file=sys.stderr)
        for reason in draft.refusals:
            print(f"  - {reason}", file=sys.stderr)
        return EXIT_REFUSED

    out_dir.mkdir(parents=True, exist_ok=True)
    inpx_path = emit_inpx(draft, out_dir / f"{name}.inpx")
    report_path = out_dir / f"{name}.compliance.md"
    report_path.write_text(draft.render_compliance(lang), encoding="utf-8", newline="")
    json_path = out_dir / f"{name}.genesis.json"
    json_path.write_text(
        json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="",
    )

    counts = draft.compliance.counts()
    print(pick_text(_TEXT["wrote"], lang))
    for path in (inpx_path, report_path, json_path):
        print(f"  - {path}")
    print(
        pick_text(_TEXT["summary"], lang).format(
            links=len(draft.links),
            connectors=len(draft.connectors),
            nodes=len(draft.nodes),
            **counts,
        )
    )
    violated = draft.compliance.violated
    if violated:
        print(pick_text(_TEXT["violated_head"], lang))
        for item in violated:
            print(f"  - {item.title(lang)}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
