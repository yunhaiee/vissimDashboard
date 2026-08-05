"""Smoke-test inpx_parser and signal_parser against real PTV example networks.

Usage: python tools/smoke_parse_examples.py [root_dir]
Default root: the Vissim 2026 Examples Demo folder in Public Documents.
Parses every .inpx found (depth-limited), reporting object counts or the
failure, so parser regressions against real Vissim files surface early.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.inpx_parser import parse_network  # noqa: E402
from services.signal_parser import parse_signal_controllers  # noqa: E402

DEFAULT_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    inpx_files = sorted(root.glob("*/*.inpx"))
    if not inpx_files:
        print(f"No .inpx files found under {root}")
        return

    ok = 0
    failed = 0
    for inpx in inpx_files:
        label = f"{inpx.parent.name}/{inpx.name}"
        try:
            net = parse_network(str(inpx))
            links = net["links"]
            connectors = sum(1 for link in links if link["is_connector"])
            geo = sum(1 for link in links if link["points"])
            counts = (
                f"links={len(links)} (connectors={connectors}, with-geometry={geo}) "
                f"nodes={len(net['nodes'])} tt={len(net['traveltime_sections'])}"
            )
        except Exception as exc:
            print(f"FAIL network  {label}: {type(exc).__name__}: {exc}")
            failed += 1
            continue

        try:
            signals = parse_signal_controllers(str(inpx))
            with_cycle = sum(1 for info in signals.values() if info.get("cycle_time"))
            sig = f"signals={len(signals)} (with-cycle={with_cycle})"
        except Exception as exc:
            print(f"FAIL signals  {label}: {type(exc).__name__}: {exc}")
            failed += 1
            continue

        print(f"OK  {label}: {counts} {sig}")
        ok += 1

    print(f"\n{ok} parsed, {failed} failed of {ok + failed}")


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()
