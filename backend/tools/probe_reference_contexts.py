"""Where does a reference in an .inpx point, and how do we know?

The measurement behind ``services.genesis.gate``'s CONTEXT-KEYED reference
table (roadmap v2 §5.A row A5, 2026-08-11). Offline: reads ``.inpx`` XML only,
opens no COM, needs no licence seat.

THE QUESTION
------------
Vissim writes ``<intObjectRef key="…"/>`` as its generic "this set contains
that object" element. The element name says nothing about what it points at —
the CONTAINER does. A reference table keyed on the element name alone therefore
resolves every occurrence against whichever id space it happened to be measured
on, which on the first real consultant model produced 253 fabricated
"unresolved reference" findings and silently graded 3 more against the wrong
universe. This probe answers "what does a reference at ``<parent>/<tag>/@attr``
point at?" by measurement instead.

THE METHOD
----------
For every ``(parent tag, element tag, attribute)`` site in the corpus, collect
its single-token values per file, and ask which of the id spaces THAT SAME FILE
declares (``tag -> {@no}``) contains all of them. A target is a candidate only
when it is the unanimous answer in EVERY file the site occurs in. Corpus
breadth is what breaks coincidences: an id space that happens to contain a
site's handful of values in one file rarely does so in forty.

WHAT THE PROBE DOES NOT DO
--------------------------
It does not register anything. Its output is READ and the real references are
entered by hand, exactly the way ``inpx_structure.ID_REFERENCE_SITES`` (this
repository's other, older, xpath-keyed reference registry) was built — because
the candidate list contains coincidences that no filter can remove without
domain knowledge. ``occupancyDistribution/@mean`` resolves unanimously against
the occupancy-distribution id space across 402 files and is a MEAN.

Five generic-membership sites are deliberately left OUT of the table so the gate
names them as unmeasured instead of resolving them against the most plausible
guess — and the reason is not the same for all five. Four are genuinely
ambiguous, with no single winning id space: ``<sectFilt>`` (6 files, 5
candidates), ``<evalGrps>`` (17 files, 2), ``<sections>`` (91 files, 2),
``<mesoSectsForMicroSim>`` (2 files, 21 — too few samples to exclude anything).
``<walkPedClasses>`` is different: it HAS one unanimous candidate
(``pedestrianClass``), and it is left out on volume — 4 occurrences of a single
distinct value across 4 files, below :data:`MIN_DISTINCT_VALUES`, which cannot
tell a reference from a coincidence.

USAGE
-----
    python backend/tools/probe_reference_contexts.py --root "<dir of .inpx>" ...
    python backend/tools/probe_reference_contexts.py --generic-only
    python backend/tools/probe_reference_contexts.py --audit

``--audit`` re-runs the table itself against the corpus: every registered
context — all 41 simple ones AND all 7 pair-shaped ones — must still resolve,
and every generic-reference context present in the corpus must be either
registered or knowingly absent.

It exits non-zero when a registered context is BROKEN, and BROKEN has a precise
meaning: the file DECLARES objects of the target class and the referenced id is
not among them, i.e. the table names the wrong id space. A value that resolves
to nothing because the file declares NO object of that class at all is reported
separately as INCONCLUSIVE, because it says nothing about the table — it is a
partial network (this repository keeps several deliberately stripped diagnostic
fragments under ``runtime/genesis-open-live-check/nets/``, dumps of intermediate
pipeline stages built to probe what Vissim does when a support container is
missing). The gate itself still flags every one of those references as
unresolved, which is correct; the AUDIT is asking a different question, and a
self-check that is red on a healthy tree is a self-check everyone learns to
ignore.

DEFAULT CORPUS
--------------
The PTV Vissim example libraries installed on this machine plus this
repository's own live-check networks. Nothing under ``client-data/`` or
``runtime/workspaces/`` is read by default: those are confidential and are
passed explicitly with ``--root`` when a measurement needs them. No path, id or
value from such a file belongs in source, tests or a report — only counts.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:  # pragma: no cover - CLI convenience
    sys.path.insert(0, str(BACKEND_ROOT))

from services.genesis.gate import (  # noqa: E402
    GENERIC_REFERENCE_TAGS,
    _PAIR_REFERENCES,
    _SIMPLE_REFERENCES,
)

REPO_ROOT = BACKEND_ROOT.parent

#: Where PTV installs its own example networks. Both major versions, because
#: the connector-endpoint shape CHANGED between them: 2024 writes
#: ``<fromLinkEndPt lane="1 1"/>`` and 2026 writes ``<fromLinkEndPt link="1"/>``
#: with the per-lane mapping in ``<fromLanes>``/``<toLanes>`` children. A
#: measurement taken on one version alone would have missed half the surface.
DEFAULT_ROOTS: tuple[Path, ...] = (
    Path(r"C:/Users/Public/Documents/PTV Vision/PTV Vissim 2026"),
    Path(r"C:/Users/Public/Documents/PTV Vision/PTV Vissim 2024"),
    REPO_ROOT / "runtime" / "genesis-open-live-check" / "nets",
    REPO_ROOT / "data",
)

#: Below this many distinct values a unanimous id space is as likely to be a
#: coincidence as a reference. Not a modelling threshold — a reading aid; the
#: output is read either way.
MIN_DISTINCT_VALUES = 3


def corpus(roots: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix.lower() == ".inpx":
            candidates = [root]
        elif root.is_dir():
            candidates = sorted(root.rglob("*.inpx"))
        else:
            continue
        for path in candidates:
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


def _declared(root: ET.Element) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for element in root.iter():
        no = element.get("no")
        if no is not None:
            out[element.tag].add(no)
    return out


def _sites(root: ET.Element, generic_only: bool) -> dict[tuple[str, str, str], set[str]]:
    parents = {child: parent for parent in root.iter() for child in parent}
    out: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for element in root.iter():
        if generic_only and element.tag not in GENERIC_REFERENCE_TAGS:
            continue
        parent = parents[element].tag if element in parents else ""
        for attribute, value in element.attrib.items():
            if attribute in {"no", "name"}:
                continue
            out[(parent, element.tag, attribute)].add(value)
    return out


def measure(files: list[Path], *, generic_only: bool) -> dict:
    candidates: dict[tuple[str, str, str], set[str] | None] = {}
    files_with: Counter[tuple[str, str, str]] = Counter()
    distinct: Counter[tuple[str, str, str]] = Counter()
    occurrences: Counter[tuple[str, str, str]] = Counter()
    unreadable: list[str] = []

    for path in files:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError) as exc:
            unreadable.append(f"{path.name}: {exc}")
            continue
        declared = _declared(root)
        for site, values in _sites(root, generic_only).items():
            single = {v for v in values if len(v.split()) == 1 and v.strip()}
            occurrences[site] += len(values)
            if not single:
                continue
            files_with[site] += 1
            distinct[site] = max(distinct[site], len(single))
            resolving = {tag for tag, ids in declared.items() if single <= ids}
            candidates[site] = (
                resolving if site not in candidates else (candidates[site] & resolving)
            )
    return {
        "candidates": candidates,
        "files_with": files_with,
        "distinct": distinct,
        "occurrences": occurrences,
        "unreadable": unreadable,
    }


def audit(files: list[Path]) -> int:
    """Does the SHIPPED table still hold on this corpus?

    Both halves of it: the 41 simple contexts and the 7 pair-shaped ones, which
    went unchecked until 2026-08-11 despite this tool promising otherwise —
    they are the backbone of V4's connector-lane checks and V5's head integrity,
    roughly 15 % of the registered table, and a silent drift in one of them
    would have printed a clean bill of health.

    A value that resolves to nothing is only evidence against the TABLE when the
    file declares objects of the target class at all; when it declares none, the
    file is missing that whole layer and the audit cannot learn anything from it.
    Those are counted as INCONCLUSIVE and do not fail the run. Measured
    2026-08-11 over the 825-file default corpus: 61 unresolved values across 7
    simple contexts, **all 61** of the inconclusive kind and confined to 5
    deliberately stripped diagnostic fragments of this repository's own; ZERO
    genuine table errors.
    """
    registered = set(_SIMPLE_REFERENCES) | set(_PAIR_REFERENCES)
    broken: Counter[tuple[str, str, str]] = Counter()
    inconclusive: Counter[tuple[str, str, str]] = Counter()
    inconclusive_files: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    seen: Counter[tuple[str, str, str]] = Counter()
    pair_seen: Counter[tuple[str, str, str]] = Counter()
    unregistered_generic: Counter[tuple[str, str, str]] = Counter()

    for path in files:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        declared = _declared(root)
        for site, values in _sites(root, generic_only=False).items():
            if site in _SIMPLE_REFERENCES:
                seen[site] += 1
                target = _SIMPLE_REFERENCES[site]
                spellings = (target,) if isinstance(target, str) else target
                target_declared = any(declared.get(t) for t in spellings)
                missing = {
                    v
                    for v in values
                    if not any(v in declared.get(t, set()) for t in spellings)
                }
                if not missing:
                    continue
                if target_declared:
                    broken[site] += len(missing)
                else:
                    inconclusive[site] += len(missing)
                    inconclusive_files[site].add(path.name)
            elif site in _PAIR_REFERENCES:
                pair_seen[site] += 1
                owner_tag, member_tag = _PAIR_REFERENCES[site]
                owner_declared = bool(declared.get(owner_tag))
                for value in values:
                    tokens = value.split()
                    if len(tokens) != 2:
                        # A malformed pair is a table/schema question no matter
                        # what the file declares: the SHAPE is what is claimed.
                        broken[site] += 1
                        continue
                    if tokens[0] in declared.get(owner_tag, set()):
                        continue
                    if owner_declared:
                        broken[site] += 1
                    else:
                        inconclusive[site] += 1
                        inconclusive_files[site].add(path.name)
            elif site[1] in GENERIC_REFERENCE_TAGS and site not in registered:
                unregistered_generic[site] += 1

    print(f"audited {len(files)} file(s)")
    print(f"registered simple contexts seen: {len(seen)} of {len(_SIMPLE_REFERENCES)}")
    print(f"registered pair contexts seen:   {len(pair_seen)} of {len(_PAIR_REFERENCES)}")
    for site, count in sorted(broken.items()):
        print(
            f"  BROKEN {site[0]}/{site[1]}/@{site[2]} — {count} value(s) name an id the "
            f"file's own target container does not declare"
        )
    for site, count in sorted(inconclusive.items()):
        names = sorted(inconclusive_files[site])
        print(
            f"  INCONCLUSIVE (target class absent — a partial network, not a table error) "
            f"{site[0]}/{site[1]}/@{site[2]} — {count} value(s) in {len(names)} file(s): "
            f"{', '.join(names)}"
        )
    for site, count in sorted(unregistered_generic.items()):
        print(
            f"  UNREGISTERED (gate reports it as unmeasured) "
            f"{site[0]}/{site[1]}/@{site[2]} — {count} file(s)"
        )
    return 1 if broken else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", action="append", default=[], help="a directory or .inpx to read")
    parser.add_argument(
        "--generic-only",
        action="store_true",
        help="only the generic reference elements (intObjectRef/objectRef)",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="re-run the SHIPPED table against the corpus instead of measuring candidates",
    )
    parser.add_argument("--min-distinct", type=int, default=MIN_DISTINCT_VALUES)
    args = parser.parse_args(argv)

    roots = [Path(r) for r in args.root] or list(DEFAULT_ROOTS)
    files = corpus(roots)
    if not files:
        print("no .inpx found — pass --root", file=sys.stderr)
        return 2

    if args.audit:
        return audit(files)

    result = measure(files, generic_only=args.generic_only)
    print(f"corpus: {len(files)} file(s)")
    for line in result["unreadable"]:
        print(f"  unreadable — {line}")
    print()
    rows = [
        (site, sorted(targets))
        for site, targets in result["candidates"].items()
        if targets and result["distinct"][site] >= args.min_distinct
    ]
    rows.sort(key=lambda row: -result["occurrences"][row[0]])
    print(
        f"{len(rows)} candidate reference site(s) — unanimous in every file they occur in, "
        f"and >= {args.min_distinct} distinct values. READ THIS LIST; it contains coincidences."
    )
    registered = set(_SIMPLE_REFERENCES) | set(_PAIR_REFERENCES)
    for site, targets in rows:
        mark = "registered" if site in registered else "NOT registered"
        print(
            f"  {site[0]}/{site[1]}/@{site[2]}  n={result['occurrences'][site]} "
            f"files={result['files_with'][site]} distinct={result['distinct'][site]} "
            f"-> {targets}  [{mark}]"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
