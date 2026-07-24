"""M5.4: aggregate one or more failure-injection ``results.jsonl`` files
(``eval.injection.write_injection_results_jsonl``'s output -- one JSON
object per line, one line per trial) into TR-C-ready markdown tables:
per-injector AND pooled detection/refusal rates, each with a Wilson 95%
confidence interval.

**Why Wilson, not the normal (Wald) approximation** (docs/phase2/
04-EVAL-PLAN.md §3: "report all with N and 95% CIs; both papers omitted
them"; docs/phase2/03-RESEARCH-DIGEST.md: "[A] omitted [N and CIs], which we
add"): this suite's per-injector trial counts are SMALL (tens, not
thousands), and the Wald interval (``p_hat ± z*sqrt(p_hat(1-p_hat)/n)``)
degenerates to a zero-width ``[0, 0]``/``[1, 1]`` at ``p_hat`` of exactly 0
or 1 -- exactly the case a well-behaved injector against a solid guardrail
(or a confirmed blind spot) produces routinely. Wilson's score interval
does not have that failure mode and is the standard small-N recommendation.

Rendering is intentionally deterministic: rows are grouped and then sorted
by ``(injector, arm)`` / ``arm`` (str sort), so re-running against the same
inputs always emits byte-identical markdown -- the FABLE-PLANNED note this
task's brief asks for ("tables render deterministically ... so paper diffs
are stable").

This module only reads already-written JSONL files (plain ``dict`` records)
-- it does not import ``eval.injection`` and has no dependency on
``services.agent`` or the live orchestrator, so it stays usable standalone
against archived results from a prior run (including a real-provider /
real-COM run this repo's CI never produces).
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Two-sided 95% Wilson z (= scipy.stats.norm.ppf(0.975); hardcoded so this
# module has no scipy/numpy dependency).
Z_95 = 1.959963984540054


def wilson_ci(successes: int, n: int, *, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion ``successes / n``.

    Correctly bounded to ``[0, 1]`` at the ``0/n`` and ``n/n`` edges (unlike
    the normal/Wald approximation -- see module docstring) without any
    special-casing: the closed-form score interval is well-defined for any
    ``0 <= successes <= n`` with ``n >= 1``.

    Raises :class:`ValueError` for ``n <= 0`` or ``successes`` outside
    ``[0, n]`` -- both are caller bugs (an empty group should never reach
    this function; see :class:`RateSummary.from_counts`, which is the
    caller-facing wrapper that handles ``n == 0`` explicitly instead).
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n!r}")
    if successes < 0 or successes > n:
        raise ValueError(f"successes ({successes!r}) must be within [0, n={n!r}]")

    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) / n) + (z2 / (4 * n * n))) / denom
    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return lower, upper


@dataclass(frozen=True)
class RateSummary:
    """One group's rate + Wilson 95% CI, or the explicit "no data" state
    (``n == 0``) -- callers must not divide by zero themselves."""

    n: int
    successes: int
    rate: float | None
    ci_low: float | None
    ci_high: float | None

    @classmethod
    def from_counts(cls, successes: int, n: int) -> "RateSummary":
        if n == 0:
            return cls(n=0, successes=0, rate=None, ci_low=None, ci_high=None)
        low, high = wilson_ci(successes, n)
        return cls(n=n, successes=successes, rate=successes / n, ci_low=low, ci_high=high)

    def render(self) -> str:
        if self.n == 0 or self.rate is None:
            return "n/a (N=0)"
        return (
            f"{self.rate:.1%} ({self.successes}/{self.n}) "
            f"[{self.ci_low:.1%}, {self.ci_high:.1%}]"
        )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_injection_records(paths: Iterable[Path | str]) -> list[dict[str, Any]]:
    """Read every JSON line from every path in ``paths``, concatenated in
    the given path order (then line order within each file) -- the "one or
    more results JSONL files across arms" this task's brief asks for. Blank
    lines are skipped; each non-blank line must be one JSON object."""
    records: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path)
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _rate_summary(records: list[dict[str, Any]], field: str) -> RateSummary:
    n = len(records)
    successes = sum(1 for r in records if r.get(field))
    return RateSummary.from_counts(successes, n)


def per_injector_arm_table(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per ``(injector, arm)``, sorted by that key -- deterministic
    row order regardless of the input records' original order."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(str(record.get("injector", "")), str(record.get("arm", "")))].append(record)

    rows: list[dict[str, Any]] = []
    for injector, arm in sorted(groups):
        group = groups[(injector, arm)]
        rows.append(
            {
                "injector": injector,
                "arm": arm,
                "n": len(group),
                "detection": _rate_summary(group, "detected"),
                "refusal": _rate_summary(group, "refused"),
            }
        )
    return rows


def pooled_arm_table(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per ``arm``, pooled across every injector present in
    ``records`` -- sorted by ``arm``. This is the ablation-comparison view:
    "how often does the validation layer catch an injected fault overall,
    full stack vs. guardrails-disabled."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record.get("arm", ""))].append(record)

    rows: list[dict[str, Any]] = []
    for arm in sorted(groups):
        group = groups[arm]
        rows.append(
            {
                "arm": arm,
                "n": len(group),
                "detection": _rate_summary(group, "detected"),
                "refusal": _rate_summary(group, "refused"),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_per_injector_table(records: list[dict[str, Any]]) -> str:
    rows = per_injector_arm_table(records)
    lines = [
        "| injector | arm | N | detection rate [95% CI] | refusal rate [95% CI] |",
        "|---|---|---|---|---|",
    ]
    if not rows:
        lines.append("| _(no records)_ | | | | |")
    for row in rows:
        lines.append(
            f"| {row['injector']} | {row['arm']} | {row['n']} | "
            f"{row['detection'].render()} | {row['refusal'].render()} |"
        )
    return "\n".join(lines) + "\n"


def render_pooled_table(records: list[dict[str, Any]]) -> str:
    rows = pooled_arm_table(records)
    lines = [
        "| arm | N | detection rate [95% CI] | refusal rate [95% CI] |",
        "|---|---|---|---|",
    ]
    if not rows:
        lines.append("| _(no records)_ | | | |")
    for row in rows:
        lines.append(
            f"| {row['arm']} | {row['n']} | {row['detection'].render()} | {row['refusal'].render()} |"
        )
    return "\n".join(lines) + "\n"


def render_full_report(records: list[dict[str, Any]]) -> str:
    """The complete M5.4 markdown report: header + per-injector table +
    pooled table, in that fixed order."""
    lines = [
        "# M5.4 failure-injection results",
        "",
        f"- total trials: {len(records)}",
        "- CI method: Wilson score interval, 95% (not the normal/Wald approximation -- see "
        "this module's docstring)",
        "",
        "## Per-injector, per-arm",
        "",
        render_per_injector_table(records),
        "## Pooled (across every injector), per-arm",
        "",
        render_pooled_table(records),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M5.4 injection-results report renderer")
    parser.add_argument(
        "results", nargs="+", type=Path, help="One or more results.jsonl files (one arm each, typically)"
    )
    parser.add_argument("--out", type=Path, default=None, help="Write markdown here instead of stdout only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    records = load_injection_records(args.results)
    report = render_full_report(records)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "Z_95",
    "RateSummary",
    "load_injection_records",
    "main",
    "per_injector_arm_table",
    "pooled_arm_table",
    "render_full_report",
    "render_per_injector_table",
    "render_pooled_table",
    "wilson_ci",
]
