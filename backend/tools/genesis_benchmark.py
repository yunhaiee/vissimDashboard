"""Operator CLI for the Murdoch rebuild benchmark v2 (roadmap R1.6 / row 2.3).

Two ``.inpx`` networks in, one topological-correspondence report out: how much
of a consultant's hand-built model a generated draft actually contains, matched
by geometry rather than by id.

Usage (run from ``backend/``, with the venv python)::

    python tools/genesis_benchmark.py \\
        --consultant path/to/current.inpx \\
        --generated  OUT/murdoch.inpx \\
        --out-dir    OUT/benchmark

    # the origin is read from <generated>.genesis.json by default; state it
    # explicitly for a draft whose sidecar is elsewhere or absent:
    python tools/genesis_benchmark.py ... --origin-lat -32.07694175 --origin-lon 115.83820175

    # every rendering, for a ko/en/ja deliverable:
    python tools/genesis_benchmark.py ... --lang ko --lang en --lang ja

Writes into ``--out-dir`` (created if missing): ``<name>.benchmark.json`` (the
whole :class:`~services.genesis.benchmark.TopologicalBenchmark` as data) and one
``<name>.benchmark.<lang>.txt`` per requested language.

Exit codes: ``0`` a comparison was performed, ``2`` refused (a file that could
not be read, or a generated draft whose projection origin is unknown), ``1`` a
usage error.

WHY A NEW TOOL RATHER THAN A FLAG ON ``genesis_osm_draft.py``
------------------------------------------------------------
``genesis_osm_draft.py`` has a one-input contract — an ``.osm`` in, a draft and
a compliance report out — and a documented exit-code vocabulary in which ``2``
means *generation* was refused. The benchmark takes **two networks**, neither of
which need be the draft that tool just produced, and refuses for entirely
different reasons (an unreadable consultant file, a missing projection origin,
two frames that cannot be bridged). Folding it in would (a) overload exit code 2
across two unrelated refusals, which is precisely what a scripted caller cannot
recover from, and (b) make re-benchmarking an existing draft impossible without
regenerating it — the draft is deterministic, so that would be wasted work, but
worse, it would tie the benchmark to having the ``.osm`` on hand when the thing
being benchmarked is the ``.inpx``. The two jobs share a library
(``services.genesis``), which is where sharing belongs.

WHAT THIS SCRIPT TOUCHES
------------------------
The two ``.inpx`` files it is given and the optional ``.genesis.json`` sidecar
(all read-only) and the ``--out-dir`` it is given. Nothing else: no ``data/``,
no ``runtime/`` writes, no ``client-data/`` writes, no workspace binding (see
``genesis_osm_draft.py``'s note on why binding one would be actively harmful),
no database, no network access and no Vissim COM.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.genesis.benchmark import (  # noqa: E402
    MATCH_TOLERANCE_M,
    benchmark_network_files,
    pick_text,
    render_benchmark_report,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2

_TEXT: dict[str, dict[str, str]] = {
    "refused": {
        "ko": "벤치마크 거부:",
        "en": "Benchmark refused:",
        "ja": "ベンチマークを拒否:",
    },
    "wrote": {
        "ko": "작성 완료:",
        "en": "Wrote:",
        "ja": "作成完了:",
    },
    "summary": {
        "ko": "컨설턴트 객체 {total}개 — 일치 {matched} · 누락 {missing} · 모호 {ambiguous} (일치율 {rate}), 차로 일치 {lane_rate}",
        "en": "{total} consultant objects — {matched} matched, {missing} missing, {ambiguous} ambiguous ({rate} matched), lane agreement {lane_rate}",
        "ja": "コンサルタント客体 {total} 件 — 一致 {matched}・欠落 {missing}・曖昧 {ambiguous}(一致率 {rate})、車線一致 {lane_rate}",
    },
    "no_origin": {
        "ko": (
            "생성 초안의 투영 원점을 알 수 없습니다. --origin-lat/--origin-lon 을 직접 주거나, "
            "초안 옆의 .genesis.json 을 --genesis-json 으로 지정하세요. 원점을 추측하면 "
            "네트워크 전체가 조용히 잘못된 위치에 놓입니다."
        ),
        "en": (
            "The generated draft's projection origin is unknown. Pass --origin-lat/--origin-lon, "
            "or point --genesis-json at the draft's sidecar. Guessing an origin would silently "
            "mis-place the entire network."
        ),
        "ja": (
            "生成草案の投影原点が不明です。--origin-lat/--origin-lon を指定するか、"
            "--genesis-json で草案のサイドカーを指定してください。原点を推測すると"
            "ネットワーク全体が誤配置されます。"
        ),
    },
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="genesis_benchmark",
        description=(
            "Topological correspondence between a consultant .inpx and a generated draft "
            ".inpx — matched by geometry, not by id. Offline: no network access, no Vissim, "
            "no COM."
        ),
    )
    parser.add_argument("--consultant", required=True, help="path to the reference .inpx (read-only)")
    parser.add_argument("--generated", required=True, help="path to the generated draft .inpx")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="directory to write into (created if missing). Never data/ or runtime/.",
    )
    parser.add_argument("--name", default=None, help="base name for the outputs (default: murdoch)")
    parser.add_argument(
        "--genesis-json",
        default=None,
        help=(
            "the draft's .genesis.json, for its recorded projection origin "
            "(default: <generated>.genesis.json beside the draft)"
        ),
    )
    parser.add_argument("--origin-lat", type=float, default=None, help="projection origin latitude")
    parser.add_argument("--origin-lon", type=float, default=None, help="projection origin longitude")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=MATCH_TOLERANCE_M,
        help=(
            f"matching tolerance in metres (default {MATCH_TOLERANCE_M:g}; the corridor buffer "
            "and the lateral cluster gap follow it)"
        ),
    )
    parser.add_argument(
        "--lang",
        action="append",
        choices=("ko", "en", "ja"),
        default=None,
        help="render in this language; repeat for several (default: ko)",
    )
    parser.add_argument(
        "--consultant-label", default="consultant", help="label for the reference network"
    )
    parser.add_argument(
        "--generated-label", default="generated", help="label for the draft network"
    )
    return parser.parse_args(argv)


def _make_console_lossy() -> None:
    """A legacy Windows console (cp949 here) cannot encode Korean or "±".

    Degrade the console to replacement characters rather than dying with
    ``UnicodeEncodeError`` after the report files have already been written —
    the same guard, for the same reason, as ``genesis_osm_draft.py``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached stream
                pass


def _resolve_origin(args: argparse.Namespace) -> tuple[float, float] | None:
    """Explicit flags win; otherwise the draft's own sidecar; otherwise refuse."""
    if args.origin_lat is not None and args.origin_lon is not None:
        return args.origin_lat, args.origin_lon

    sidecar = (
        Path(args.genesis_json)
        if args.genesis_json
        else Path(args.generated).with_suffix(".genesis.json")
    )
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        projection = payload["metadata"]["projection"]
        return float(projection["origin_lat_deg"]), float(projection["origin_lon_deg"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _make_console_lossy()
    languages = args.lang or ["ko"]
    console_lang = languages[0]
    name = args.name or "murdoch"

    origin = _resolve_origin(args)
    if origin is None:
        print(pick_text(_TEXT["refused"], console_lang), file=sys.stderr)
        print(f"  - {pick_text(_TEXT['no_origin'], console_lang)}", file=sys.stderr)
        return EXIT_REFUSED

    benchmark = benchmark_network_files(
        args.consultant,
        args.generated,
        origin_lat_deg=origin[0],
        origin_lon_deg=origin[1],
        consultant_label=args.consultant_label,
        generated_label=args.generated_label,
        tolerance_m=args.tolerance,
    )

    if benchmark.refused:
        print(pick_text(_TEXT["refused"], console_lang), file=sys.stderr)
        for finding in benchmark.findings:
            text = finding.messages.get(console_lang) or finding.message
            print(f"  - {finding.code}: {text}", file=sys.stderr)
        return EXIT_REFUSED

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    json_path = out_dir / f"{name}.benchmark.json"
    json_path.write_text(
        json.dumps(benchmark.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="",
    )
    written.append(json_path)

    for lang in languages:
        report_path = out_dir / f"{name}.benchmark.{lang}.txt"
        report_path.write_text(
            render_benchmark_report(benchmark, lang=lang) + "\n", encoding="utf-8", newline=""
        )
        written.append(report_path)

    print(pick_text(_TEXT["wrote"], console_lang))
    for path in written:
        print(f"  - {path}")
    lanes = benchmark.lanes
    print(
        pick_text(_TEXT["summary"], console_lang).format(
            total=benchmark.consultant_object_count,
            matched=len(benchmark.matched or []),
            missing=len(benchmark.missing or []),
            ambiguous=len(benchmark.ambiguous or []),
            rate=_rate(benchmark.match_rate()),
            lane_rate=_rate(lanes.rate if lanes else None),
        )
    )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
