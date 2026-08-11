"""Roadmap 3.3 — run the R1.5 validation gate (V1–V9) over a generated ``.inpx``.

Genesis's shipping gate. Nine checks, three states each
(``PASS``/``FAIL``/``NOT_CHECKABLE``), one verdict. A check that could not run
is never counted as a pass, so **an offline run can never report an unqualified
pass**: V1 is the only check that proves a real Vissim opens the file.

Usage (run from ``backend/``, with the venv python)::

    python tools/genesis_validation_gate.py --inpx OUT/murdoch.inpx
    python tools/genesis_validation_gate.py --inpx OUT/murdoch.inpx --lang en
    python tools/genesis_validation_gate.py --inpx OUT/murdoch.inpx --json > gate.json
    python tools/genesis_validation_gate.py --inpx OUT/murdoch.inpx \
        --live-check-json ../runtime/genesis-open-live-check/state.json \
        --live-check-label murdoch_connectivity_2026_08_11

V1's evidence, and why this script does not produce it
------------------------------------------------------
Nothing here touches COM. ``backend/tools/genesis_open_live_check.py`` already
owns the one COM path this project has for opening a generated network
(``attach_to_running=False``, a clone in a work dir, ``LoadNet`` → ``.err`` →
``SaveNetAs`` → disconnect, in a bounded child process), and a second COM path
would be a second place to get those rules wrong. So V1 CONSUMES that probe's
``state.json``:

    python tools/genesis_open_live_check.py --only murdoch --murdoch OUT/murdoch.inpx
    python tools/genesis_validation_gate.py --inpx OUT/murdoch.inpx \
        --live-check-json ../runtime/genesis-open-live-check/state.json

The evidence is admitted only if the sha256 of the file the probe actually
loaded equals the sha256 of the file being gated. Evidence about a different
build is not weak evidence; V1 reports NOT_CHECKABLE and says so.

The generator's own census
--------------------------
V3 distinguishes "a vehicle leaves the model here because this is the model
boundary" from "a vehicle leaves the model here silently". That distinction is
not in the ``.inpx`` — it is in the draft's ``*.genesis.json``. This script
looks for ``<inpx stem>.genesis.json`` beside the network and says whether it
found one; ``--genesis-json`` points at it explicitly.

Exit codes
----------
``0`` PASS, ``2`` REFUSED (a refuse-tier check proved a fault), ``3``
INCOMPLETE (no refuse-tier fault, but a refuse-tier check could not run — this
is NOT a pass), ``1`` a usage error. The three outcomes are separated because a
CI job needs to tell "the build is broken" from "we have not proven the build is
good", and collapsing them is how a gate starts manufacturing confidence.

WHAT THIS SCRIPT TOUCHES
------------------------
The files it is given, read-only, plus ``--out`` when asked. No ``data/``, no
``runtime/`` writes, no workspace binding, no network access, no Vissim COM.
Offline, and safe to run in CI.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.genesis.draft import pick_text  # noqa: E402
from services.genesis.gate import (  # noqa: E402
    GateVerdict,
    LoadOracle,
    render_report,
    run_gate,
)

EXIT_PASS = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2
EXIT_INCOMPLETE = 3

_EXIT_FOR_VERDICT = {
    GateVerdict.PASS: EXIT_PASS,
    GateVerdict.REFUSED: EXIT_REFUSED,
    GateVerdict.INCOMPLETE: EXIT_INCOMPLETE,
}

_TEXT: dict[str, dict[str, str]] = {
    "census_found": {
        "ko": "생성기 명부 사용:",
        "en": "Using the generator's own census:",
        "ja": "生成器の名簿を使用:",
    },
    "census_absent": {
        "ko": "생성기 명부(*.genesis.json)가 없어 V3의 '조용한 이탈' 절반은 확인 불가로 처리됩니다.",
        "en": "No generator census (*.genesis.json) found, so V3's 'silent departure' half is "
        "reported NOT CHECKABLE.",
        "ja": "生成器の名簿(*.genesis.json)がなく、V3 の「静かな流出」半分は確認不可になります。",
    },
    "oracle_used": {
        "ko": "V1 라이브 증거:",
        "en": "V1 live evidence:",
        "ja": "V1 ライブ証拠:",
    },
    "oracle_absent": {
        "ko": "V1 라이브 증거 없음 — 이 실행은 어떤 경우에도 무조건적 통과를 보고하지 않습니다.",
        "en": "No V1 live evidence — this run cannot report an unqualified pass under any "
        "circumstances.",
        "ja": "V1 のライブ証拠なし — この実行はいかなる場合も無条件の合格を報告しません。",
    },
    "wrote": {"ko": "기록:", "en": "Wrote:", "ja": "書き出し:"},
}


def _make_console_utf8() -> None:
    """UTF-8 explicitly, not just ``errors="replace"``.

    Two failure modes, and only the first is obvious. A legacy Windows console
    (cp949 on this project's dev box) cannot encode Korean or "§", and without
    ``errors="replace"`` this script would die with ``UnicodeEncodeError``
    *after* running the whole gate — reporting a usage error about work that
    succeeded.

    The second was measured on this project on 2026-08-10 and cost a report:
    every em dash in this gate's messages IS cp1252-encodable, so
    ``errors="replace"`` never fires, and ``--json > gate.json`` on Windows
    silently writes valid JSON in cp1252 that ``json.load(open(path,
    encoding="utf-8"))`` cannot read. A gate result that only parses on the
    machine that produced it is not a gate result.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached stream
                pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="genesis_validation_gate",
        description=(
            "R1.5 validation gate (V1-V9) over a generated .inpx. Offline: no network "
            "access, no Vissim, no COM. V1 consumes genesis_open_live_check's evidence."
        ),
    )
    parser.add_argument("--inpx", required=True, help="the generated .inpx to gate")
    parser.add_argument(
        "--genesis-json",
        default=None,
        help=(
            "the draft's *.genesis.json (V3's model-boundary attribution). Default: "
            "<inpx stem>.genesis.json beside the network, when it exists."
        ),
    )
    parser.add_argument(
        "--live-check-json",
        default=None,
        help="genesis_open_live_check.py's state.json — V1's evidence",
    )
    parser.add_argument(
        "--live-check-label",
        default=None,
        help="which run inside that state.json (default: the most recent one with a load result)",
    )
    parser.add_argument(
        "--reference",
        default=None,
        help="an independently-authored reference .inpx for V7's map-error half",
    )
    parser.add_argument("--lang", default="ko", choices=("ko", "en", "ja"))
    parser.add_argument(
        "--json", action="store_true", help="print the result as JSON instead of a report"
    )
    parser.add_argument("--out", default=None, help="also write the report (or JSON) here")
    return parser.parse_args(argv)


def _load_oracle(args: argparse.Namespace) -> tuple[LoadOracle | None, str | None]:
    if not args.live_check_json:
        return None, "no_live_check_json_supplied"
    path = Path(args.live_check_json)
    if not path.is_file():
        return None, f"live_check_json_missing:{path.name}"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"live_check_json_unreadable:{type(exc).__name__}"
    return LoadOracle.from_probe_state(state, label=args.live_check_label)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _make_console_utf8()
    lang = args.lang
    inpx = Path(args.inpx)
    if not inpx.is_file():
        print(f"--inpx: no such file — {inpx}", file=sys.stderr)
        return EXIT_USAGE

    census_path: Path | None = None
    if args.genesis_json:
        census_path = Path(args.genesis_json)
        if not census_path.is_file():
            print(f"--genesis-json: no such file — {census_path}", file=sys.stderr)
            return EXIT_USAGE
    else:
        sibling = inpx.with_name(f"{inpx.stem}.genesis.json")
        census_path = sibling if sibling.is_file() else None

    metadata = None
    if census_path is not None:
        try:
            metadata = json.loads(census_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"--genesis-json: could not be read — {exc}", file=sys.stderr)
            return EXIT_USAGE

    oracle, oracle_gap = _load_oracle(args)

    result = run_gate(
        inpx,
        draft_metadata=metadata,
        load_oracle=oracle,
        load_oracle_gap=oracle_gap,
        reference_inpx=args.reference,
    )

    if args.json:
        body = json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        body = render_report(result, language=lang)

    # The provenance lines go to stderr so --json stdout stays machine-clean.
    if census_path is not None:
        print(f"{pick_text(_TEXT['census_found'], lang)} {census_path}", file=sys.stderr)
    else:
        print(pick_text(_TEXT["census_absent"], lang), file=sys.stderr)
    if oracle is not None:
        print(
            f"{pick_text(_TEXT['oracle_used'], lang)} {oracle.label} "
            f"({oracle.vissim_version or 'version not recorded'})",
            file=sys.stderr,
        )
    else:
        print(f"{pick_text(_TEXT['oracle_absent'], lang)} ({oracle_gap})", file=sys.stderr)

    sys.stdout.write(body)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body, encoding="utf-8", newline="")
        print(f"{pick_text(_TEXT['wrote'], lang)} {out_path}", file=sys.stderr)

    return _EXIT_FOR_VERDICT[result.verdict]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
