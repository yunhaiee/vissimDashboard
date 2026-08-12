"""Is a vehicle composition that no vehicle input uses a DEFECT, or an idiom?

The measurement behind ``services.genesis.gate``'s V6 orphaned-composition
clause (2026-08-12). Offline: reads ``.inpx`` XML only, opens no COM, needs no
licence seat. Same corpus and the same discipline as
``probe_reference_contexts.py`` (roadmap v2 §5.A row A5): a rule about real
``.inpx`` files is MEASURED on real ``.inpx`` files, and it generalises only to
the population it was measured on.

THE QUESTION
------------
V6 gaps out (``v6_no_demand_layer``) only when a file declares NO vehicle input
at all. A multi-site model whose second site lost its entire demand still has
inputs — the first site's — so V6 passes, and the only surviving trace of the
dropped site is the ``<vehicleComposition>`` nothing references any more. Is
"every composition is used by at least one input" a rule real files obey?

If orphaned compositions are RARE the clause can refuse; if modellers routinely
keep template compositions around, a refuse-tier clause would fabricate a defect
on ordinary files — the exact failure A5 was written to undo (253 fabricated
``v2_unresolved_reference`` findings on a consultant's own calibrated model).

THE METHOD
----------
Per file, three numbers:

* ``compositions`` — ``vehicleCompositions/vehicleComposition/@no``.
* ``inputs`` — ``vehicleInputs/vehicleInput``, and the composition ids they name
  at ``timeIntVehVols/timeIntervalVehVolume/@vehComp`` (the ONE registered
  composition reference in the gate's table).
* ``orphans`` — declared compositions no input names.

And, because "no input names it" is not the same as "nothing names it", the
probe ALSO sweeps every attribute in the document for values that are
composition ids. A composition used by a parking lot or a dynamic-assignment
demand is in use; counting it as orphaned would be a fabricated finding of
exactly the A5 kind. That sweep cannot be trusted on values alone — composition
ids are SMALL INTEGERS, and the coincidence floor it produces is ~297,000 hits
across 456 sites (``@markingType``, ``@level``, ``@share``) — so the consumer
sites are HAND-REGISTERED in :data:`CONSUMER_SITES` after reading the sweep,
exactly the way ``probe_reference_contexts.py``'s candidates are read before
anything is entered in the gate's table. The rejected candidates are recorded
there too, with the measurement that rejected them.

Only files that CAN show the pattern are counted: a file with no compositions,
or with no vehicle inputs at all (V6 already gaps out there), says nothing.

USAGE
-----
    python backend/tools/probe_orphaned_compositions.py
    python backend/tools/probe_orphaned_compositions.py --root "<dir or .inpx>"
    python backend/tools/probe_orphaned_compositions.py --root X --show-orphans

Files under the default corpus are named in the output. A file supplied with
``--root`` that lies outside it (a client model, a workspace upload) is reported
as counts only and never by name — the same confidentiality rule
``probe_reference_contexts.py`` states: no path, id or value from such a file
belongs in source, tests or a report.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET

TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:  # pragma: no cover - CLI convenience
    sys.path.insert(0, str(TOOLS_ROOT))

from probe_reference_contexts import DEFAULT_ROOTS, corpus  # noqa: E402

#: ``(parent tag, element tag, attribute)`` sites that CONSUME a vehicle
#: composition, besides the vehicle input itself. Read out of this probe's own
#: value sweep and confirmed against ``probe_reference_contexts.py
#: --min-distinct 1`` (2026-08-12, 825-file corpus):
#:
#: Counts below are THIS probe's: values that collide with a composition id the
#: same file declares, and the files carrying them.
#:
#: * ``parkingLot/@initOccupComp`` — 131 values / 38 files; the reference probe
#:   resolves it unanimously to ``vehicleComposition``.
#: * ``dynAssignDemand/@vehComp`` — 42 values / 28 files; ``vehicleComposition``
#:   is among the reference probe's candidates and the attribute is the
#:   composition a dynamic assignment's demand releases.
#:
#: DELIBERATELY REJECTED, with the measurement that rejected them:
#: ``ptLineStop/@alightComp`` (146 / 18) and ``partialPTLineStop/@alightComp``
#: (88 / 16) resolve to **pedestrian**Composition — alighting passengers are
#: pedestrians, and the shared word "Comp" is the whole of the resemblance;
#: ``timeIntervalPedVolume/@pedComp`` (330 / 47) is that same pedestrian id
#: space; ``signalHead/@complRate`` (2,848 / 123) is a compliance RATE;
#: ``@attributeComputation`` (45 across three sites) is an attribute-computation
#: id. Each would have silenced real orphans.
CONSUMER_SITES: tuple[tuple[str, str, str], ...] = (
    ("parkingLots", "parkingLot", "initOccupComp"),
    ("dynAssignDemands", "dynAssignDemand", "vehComp"),
)

#: Which sub-population a file belongs to, for the breakdown. A rule measured
#: on PTV's library alone has been measured on files PTV wrote; the repository's
#: own live-check nets and drafts are a different population again.
def population(path: Path) -> str:
    text = str(path).replace("\\", "/").lower()
    if "ptv vissim 2026" in text:
        return "PTV library 2026"
    if "ptv vissim 2024" in text:
        return "PTV library 2024"
    if "/genesis-open-live-check/" in text:
        return "repo live-check nets"
    if "/vissimdashboard/data/" in text:
        return "repo data"
    return "supplied"


def _is_default_corpus(path: Path) -> bool:
    return population(path) != "supplied"


def measure_file(root: ET.Element) -> dict:
    compositions = [
        el for el in root.findall(".//vehicleCompositions/vehicleComposition")
    ]
    ids = [el.get("no") or "" for el in compositions]
    declared = {i for i in ids if i}
    inputs = root.findall(".//vehicleInputs/vehicleInput")

    used_by_inputs: set[str] = set()
    for element in inputs:
        for volume in element.findall("./timeIntVehVols/timeIntervalVehVolume"):
            comp = volume.get("vehComp")
            if comp:
                used_by_inputs.add(comp)

    # Anything ELSE in the document that names a composition id. Sites are
    # collected so the reader can see WHAT does the naming, not just that
    # something does — and split by whether the attribute NAME claims to be a
    # composition, because a bare value match against ids like "1" and "2"
    # collides with half the schema.
    parents = {child: parent for parent in root.iter() for child in parent}
    named_sites: Counter[tuple[str, str, str]] = Counter()
    candidate_sites: Counter[tuple[str, str, str]] = Counter()
    coincidence_sites: Counter[tuple[str, str, str]] = Counter()
    used_by_named: set[str] = set()
    for element in root.iter():
        if element.tag == "vehicleComposition":
            continue  # its own declaration
        parent = parents[element].tag if element in parents else ""
        for attribute, value in element.attrib.items():
            if attribute == "no":
                continue
            if parent == "timeIntVehVols" and attribute == "vehComp":
                continue  # the input reference, counted above
            token = value.strip()
            if not token or token not in declared:
                continue
            site = (parent, element.tag, attribute)
            if site in CONSUMER_SITES:
                named_sites[site] += 1
                used_by_named.add(token)
            elif "comp" in attribute.lower():
                # Named like a composition and NOT registered: the candidate
                # list a reader has to work through. Printed separately so a
                # new spelling in a future Vissim version cannot hide inside
                # the coincidence floor.
                candidate_sites[site] += 1
            else:
                coincidence_sites[site] += 1

    return {
        "compositions": len(compositions),
        "inputs": len(inputs),
        "used_by_inputs": used_by_inputs & declared,
        "orphans_vs_inputs": sorted(declared - used_by_inputs),
        "orphans_vs_anything": sorted(declared - used_by_inputs - used_by_named),
        "named_sites": named_sites,
        "candidate_sites": candidate_sites,
        "coincidence_sites": coincidence_sites,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", action="append", default=[], help="a directory or .inpx to read")
    parser.add_argument(
        "--show-orphans",
        action="store_true",
        help="list every file that carries at least one orphaned composition",
    )
    args = parser.parse_args(argv)

    roots = [Path(r) for r in args.root] or list(DEFAULT_ROOTS)
    files = corpus(roots)
    if not files:
        print("no .inpx found — pass --root", file=sys.stderr)
        return 2

    scanned = 0
    unreadable: list[str] = []
    #: population -> [applicable, with_orphans_vs_inputs, with_orphans_vs_anything]
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    orphan_counts: Counter[int] = Counter()
    not_applicable: Counter[str] = Counter()
    sites_total: Counter[tuple[str, str, str]] = Counter()
    site_files: Counter[tuple[str, str, str]] = Counter()
    candidate_total: Counter[tuple[str, str, str]] = Counter()
    candidate_files: Counter[tuple[str, str, str]] = Counter()
    coincidence_values = 0
    coincidence_site_count: set[tuple[str, str, str]] = set()
    rows: list[tuple[str, int, int, int, int]] = []
    supplied_index = 0

    for path in files:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            unreadable.append(f"{path.name if _is_default_corpus(path) else '(supplied)'}: {exc}")
            continue
        scanned += 1
        result = measure_file(root)
        for site, count in result["named_sites"].items():
            sites_total[site] += count
            site_files[site] += 1
        for site, count in result["candidate_sites"].items():
            candidate_total[site] += count
            candidate_files[site] += 1
        for site, count in result["coincidence_sites"].items():
            coincidence_values += count
            coincidence_site_count.add(site)

        group = population(path)
        if _is_default_corpus(path):
            label = path.name
        else:
            supplied_index += 1
            label = f"(supplied #{supplied_index})"

        if result["compositions"] == 0:
            not_applicable["no composition declared"] += 1
            continue
        if result["inputs"] == 0:
            # V6 already gaps out on these (v6_no_demand_layer).
            not_applicable["no vehicle input (V6 gaps out)"] += 1
            continue

        tally[group][0] += 1
        strict = len(result["orphans_vs_inputs"])
        loose = len(result["orphans_vs_anything"])
        if strict:
            tally[group][1] += 1
        if loose:
            tally[group][2] += 1
        orphan_counts[strict] += 1
        if strict:
            rows.append((label, result["compositions"], result["inputs"], strict, loose))

    applicable = sum(v[0] for v in tally.values())
    with_orphans = sum(v[1] for v in tally.values())
    with_orphans_strict = sum(v[2] for v in tally.values())

    print(f"corpus: {len(files)} file(s) found, {scanned} parsed")
    for line in unreadable:
        print(f"  unreadable — {line}")
    for reason, count in sorted(not_applicable.items()):
        print(f"  not applicable — {reason}: {count} file(s)")
    print()
    print(
        f"applicable (>=1 composition AND >=1 vehicle input): {applicable} file(s)"
    )
    if applicable:
        print(
            f"  with >=1 composition no vehicle input references: {with_orphans} "
            f"({100.0 * with_orphans / applicable:.1f} %)"
        )
        print(
            f"  with >=1 composition NOTHING in the file references: {with_orphans_strict} "
            f"({100.0 * with_orphans_strict / applicable:.1f} %)"
        )
    print()
    print("by sub-population (applicable / orphan-vs-inputs / orphan-vs-anything):")
    for group in sorted(tally):
        total, orphan_vs_inputs_hits, orphan_vs_anything_hits = tally[group]
        share = f"{100.0 * orphan_vs_inputs_hits / total:.1f} %" if total else "-"
        print(
            f"  {group:<22} {total:>5} / {orphan_vs_inputs_hits:>5} / "
            f"{orphan_vs_anything_hits:>5}   ({share})"
        )
    print()
    print("orphan count per applicable file:")
    for count in sorted(orphan_counts):
        print(f"  {count:>3} orphan(s): {orphan_counts[count]} file(s)")
    print()
    print("registered consumer sites (a composition named here is IN USE):")
    if not sites_total:
        print("  none present in this corpus")
    for site, count in sorted(sites_total.items(), key=lambda kv: -kv[1]):
        print(
            f"  {site[0]}/{site[1]}/@{site[2]}  values={count} files={site_files[site]}"
        )
    print("composition-NAMED sites left unregistered (read these — see CONSUMER_SITES):")
    for site, count in sorted(candidate_total.items(), key=lambda kv: -kv[1]):
        print(
            f"  {site[0]}/{site[1]}/@{site[2]}  values={count} files={candidate_files[site]}"
        )
    print(
        f"coincidence floor (value matches a composition id, attribute name says otherwise): "
        f"{coincidence_values} value(s) across {len(coincidence_site_count)} site(s) — "
        f"why a value-only sweep cannot answer this question"
    )
    if args.show_orphans:
        print()
        print("files carrying at least one orphaned composition:")
        for label, comps, inputs, strict, loose in sorted(rows):
            print(
                f"  {label:<60} compositions={comps:<4} inputs={inputs:<5} "
                f"orphan(vs inputs)={strict:<3} orphan(vs anything)={loose}"
            )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
