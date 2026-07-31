"""Demand-stress sweep (Phase 0.1 / master roadmap S0.1, task #33):
``run_demand_stress_sweep``, tier ``"run"``.

**The demo this tool answers.** The user asks "차량 수요가 10% 늘면 어떻게 되나?" and
the agent answers with SIMULATED evidence, not a guess: run the current working
network (BASE) and a demand-scaled clone of it (SCALED variant) through the
SAME simulation job queue ``tools_run.py`` already drives, with the SAME seed
set on both sides (MRWA §5 p.393's run-discipline rule — a sweep that varies
the seed between scenarios is comparing noise, not demand), and report a
per-KPI delta.

**Isolation design decision: scale on a separate clone, never
snapshot-and-restore.** The task brief offered two options; this tool takes
the clone path. ``services.simulation_network_builder.
scale_vehicle_input_volumes`` opens the session's active run source
(``tools_run.get_active_run_source()``) READ-ONLY (``ET.parse`` — never an
in-place mutation) and writes the scaled result to a brand-new file under a
dedicated ``tempfile.mkdtemp()`` directory, entirely OUTSIDE both the repo's
``runtime/`` tree and the session's own work dir. The BASE variant then reuses
that SAME untouched working-copy path directly as its own job source, exactly
like ``run_simulation`` already does. Consequence: there is no "restore" step
to get right, because there is no window in which the working copy was ever
different — the isolation is trivially, structurally complete rather than
proven by an undo. This tool still VERIFIES it, defense in depth (the
guardrail doctrine's "verify, not trust"): a SHA-256 of the working copy is
taken before dispatch and re-checked identical immediately after both variants
finish, and the comparison rides in the envelope's ``verification`` key for
every test/live-check to assert on directly. A snapshot-and-restore design
(mutate the working copy in place for the SCALED run, then restore it) was
rejected: it introduces a real time window in which a concurrent reader of the
working copy (or a crash mid-sweep) could observe or persist the wrong
network, for zero benefit over the clone path here (XML-level volume
multiplication is cheap — no COM, no per-object set/get loop).

**Same seed set on both variants — test-pinned hard requirement.** ``_seed_
values`` builds the seed LIST exactly once (``[1, 2, ..., seed_count]`` —
deterministic and reproducible run to run, unlike a randomly-drawn set) and
the SAME list object is passed to both ``tools_run._run_job_and_wait`` calls
below. MRWA §5 (p.385-393): >=5 seeds per scenario (this tool's own
``seed_count`` bound, 5-10, mirrors ``run_simulation``'s own advisory
``single_seed_run_not_statistically_valid`` floor, enforced here structurally
via ``Field(ge=5, le=10)`` instead — see "Constraint-catalog scope" below for
why), and the seed VALUES must be identical across scenarios being compared,
or a rank/delta reads noise as signal.

**Reuse, not reinvention.** This tool calls ``tools_run``'s own module-level
functions directly, module-qualified (``tools_run._job_timeout_s``,
``tools_run._run_job_and_wait``, ``tools_run._summarize_kpis``) rather than
importing bare names or re-implementing them — three reasons: (1) ``tools_run.
py``'s own docstring frames ``_run_job_and_wait`` as "a thin, injectable
seam ... so tests can monkeypatch.setattr(tools_run, '_run_job_and_wait',
...)" — reuse via the module attribute is the designed extension point, not a
private-symbol reach-in; (2) calling it this way means a test that
monkeypatches ``tools_run._run_job_and_wait`` transparently fakes BOTH
``run_simulation`` and this tool with the exact same fixture shape, with zero
duplicated job-submission logic to keep in sync; (3) ``_summarize_kpis``
degrades gracefully already (a KPI-less job dir returns ``None``, never
raises) and takes any ``Path``, so it is reused UNCHANGED for both the
whole-job aggregate (every seed averaged, mirroring ``run_simulation``'s own
KPI summary) and each individual ``seed_NN`` subdirectory (one real run, no
averaging distortion) to build the ``per_seed`` breakdown the task brief asks
for. The active-run-source getter is reused directly too
(``tools_run.get_active_run_source()`` — PUBLIC, unlike the underscore-
prefixed functions above; mirrors ``tools_check.py``'s identical choice to
reuse this same accessor rather than a private resolver), but the
"require present, raise if not" WRAPPER is this module's own
(:func:`_require_active_source` / :class:`NoActiveSweepSourceError`), matching
``tools_check.py``'s own precedent of writing a small owned wrapper around a
sibling module's public getter rather than reaching into that sibling's
private ``_require_active_run_source``.

**Sequential, never concurrent, variant runs.** BASE is submitted and
awaited to completion BEFORE SCALED is ever submitted — not two simultaneous
``start_agent_simulation_job`` calls. ``docs/product/master-roadmap-2026-07.md``
§5's open question #3 ("Vissim license count — sweep parallelism = license
count... 1 → nightly serial queue is the ceiling") is still unanswered; running
two variants concurrently would silently assume >=2 licenses/instances are
available and safe, which nothing in this codebase confirms today. This is
strictly more conservative than necessary if more licenses ARE available —
correctly cheap to loosen later once that open question closes, expensive to
have gotten wrong here.

**Constraint-catalog scope (deliberately NOT touched by this unit).** Per the
task brief's hard rule, this tool implements its own semantic bounds directly
rather than adding entries to ``constraints.py`` (a follow-up unit owns "big
constraint additions"): the |scale_percent| > 100 hard refusal is a
``model_validator`` on ``RunDemandStressSweepParams`` (produces a clean
``invalid_params`` envelope, same failure shape ``marker_index: Field(ge=1)``
already establishes for a structural bound elsewhere in this codebase), and
the |scale_percent| > 50 caution is folded into this tool's own POST-DISPATCH
``message`` (never blocking). One real gap results from not touching
``constraints.py``: because no preflight WARN-tier finding exists for this
tool, the pre-approval card's warning bullet list (built from
``GuardrailEngine.preflight``'s findings, entirely code-deterministic,
independent of what the planner's own LLM-authored summary happens to say)
carries nothing for this tool. The task brief's requirement that the approval
card state scale/seed-count/run-count/untouched-note is instead pursued via
this tool's own docstring (the ``description`` the planner prompt renders,
:func:`services.agent.toolkit.render_contract`) explicitly instructing the
model what its proposal summary must say — a real, but LLM-dependent, lever;
the POST-run ``message`` (fully code-deterministic) states all four facts
regardless. See this unit's report for the drafted follow-up
``constraints.py`` entry (an always-fires, non-blocking preflight disclosure
finding, applies_to=["run_demand_stress_sweep"]) that would close this gap
properly, deferred here per the hard rule.

**Postrun guardrail reuse for free.** This tool's returned ``run_result``
(``{"tables", "deleted_vehicles"}``) uses the EXACT SAME shape
``run_simulation`` returns, so ``constraints.py``'s two ``applies_to=["*"]``
postrun checks (``results_missing_kpi_tables``, ``vehicles_deleted_in_run``)
already fire against a demand-stress sweep with ZERO changes to
``constraints.py`` — they key off ``extra["run_result"]`` alone, never the
tool name (unlike ``single_seed_run_not_statistically_valid``'s
``applies_to=["run_simulation"]``, deliberately not extended to this tool
since ``seed_count``'s own ``Field(ge=5, le=10)`` bound already makes a
single-seed sweep structurally impossible).

**Ledger shape.** ``artifacts.runs`` carries one entry per (variant, seed) —
``2 * seed_count`` children total — mirroring ``apply_edit_plan``'s own
"one parent action, per-step children in artifacts" ledger convention exactly.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent import tools_run
from services.agent.state import SIBLING_ASSET_EXTENSIONS
from services.agent.toolkit import tool
from services.simulation_network_builder import scale_vehicle_input_volumes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Active run source -- reused from tools_run.py (public accessor), own
# "require present" wrapper (see module docstring's "Reuse, not reinvention").
# ---------------------------------------------------------------------------


class NoActiveSweepSourceError(RuntimeError):
    """No active run source for this turn. Raised (never returned as a dict
    -- see tools_run.py's identical rationale in its own
    ``NoActiveRunSourceError``) so ``toolkit.dispatch`` reports a genuine
    ``status: "error"`` envelope."""


def _require_active_source() -> Path:
    source = tools_run.get_active_run_source()
    if source is None:
        raise NoActiveSweepSourceError(
            "No active network source for this turn (error_type: no_active_sweep_source). "
            "run_demand_stress_sweep only runs during an agent turn with a working copy set up."
        )
    if not source.exists():
        raise FileNotFoundError(f"Active sweep source does not exist: {source}")
    return source


# ---------------------------------------------------------------------------
# Bounds (structural, enforced by pydantic -- see module docstring's
# "Constraint-catalog scope" section for why these live here, not in
# constraints.py, for this unit).
# ---------------------------------------------------------------------------

_MIN_SEED_COUNT = 5
_MAX_SEED_COUNT = 10
_DEFAULT_SEED_COUNT = 5
_MAX_ABS_SCALE_PERCENT = 100.0
_WARN_ABS_SCALE_PERCENT = 50.0

#: Own message cap for this tool, mirroring tools_edit.py's _cap_message
#: convention (mirrored below, not imported -- see that module's own
#: private-symbol-mirroring precedent, e.g. tools_run.py's
#: _NETWORK_METRIC_KEYS mirroring tools_metrics.py's). A larger budget than
#: tools_edit.py's 240 chars: this tool's message must carry FOUR distinct
#: facts (scale, seed count, run count, untouched-note) PLUS the KPI
#: headline, per the task brief's own approval-card content requirement --
#: still a hard, renderer-survival-safe cap, just sized to this tool's own
#: content, not copied from a sibling tool with different content.
_MESSAGE_MAX_CHARS = 420


def _cap_message(text: str, max_chars: int = _MESSAGE_MAX_CHARS) -> str:
    """Truncate ``text`` to ``max_chars``, ellipsis-marked -- the final,
    unconditional backstop under every smarter per-field cap (mirrors
    ``tools_edit.py``'s identically-named helper; see this module's own
    docstring for why it is mirrored here rather than imported)."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


class RunDemandStressSweepParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: veh/h multiplier delta, e.g. +10 -> every scaled interval's volume
    #: * 1.10; negative values (demand DECREASE) are equally legal.
    scale_percent: float
    #: MRWA §5 p.385-387: >=5 seeds per scenario, this codebase's own advisory
    #: ceiling (run_simulation's single_seed_run_not_statistically_valid) is
    #: 5; 10 is this tool's own upper bound (a caller wanting more should
    #: reconsider whether a single sweep call is the right shape at all).
    seed_count: int = Field(default=_DEFAULT_SEED_COUNT, ge=_MIN_SEED_COUNT, le=_MAX_SEED_COUNT)
    #: Simulated duration per run (seconds) -- required, unbounded, exactly
    #: mirroring RunSimulationParams.duration_s (tools_run.py) for byte-
    #: identical semantics on the shared parameter.
    duration_s: int
    #: Optional vehicle-input filter -- None (default) scales every input in
    #: the network. An explicit list naming zero real inputs is refused (see
    #: run_demand_stress_sweep's own body) rather than silently scaling
    #: nothing and reporting an uninformative all-zero delta.
    input_nos: list[str] | None = None

    @model_validator(mode="after")
    def _scale_percent_within_hard_bound(self) -> "RunDemandStressSweepParams":
        if abs(self.scale_percent) > _MAX_ABS_SCALE_PERCENT:
            raise ValueError(
                f"scale_percent={self.scale_percent} exceeds the +/-{_MAX_ABS_SCALE_PERCENT:.0f}% "
                "hard limit -- refused. A multiplier beyond this range is not a stress test of "
                "the current demand, it is effectively a different network; use "
                "set_vehicle_input_volume directly if you really mean to replace demand rather "
                "than scale it."
            )
        return self


def _copy_sibling_assets(source_inpx: Path, work_dir: Path) -> None:
    """Best-effort copy of ``source_inpx``'s sibling assets
    (:data:`services.agent.state.SIBLING_ASSET_EXTENSIONS` -- the PUBLIC data
    this mirrors; the copy LOOP itself is mirrored rather than importing
    ``state._copy_sibling_assets`` directly, per this codebase's convention
    of reusing a sibling module's public accessors/data but mirroring its
    private, underscore-prefixed functions -- see ``tools_check.py``'s
    identical choice for ``tools_run._require_active_run_source``) into
    ``work_dir``, preserving each file's exact original name. One file that
    cannot be read is skipped with a logged warning; never raises."""
    try:
        entries = list(source_inpx.parent.iterdir())
    except OSError:
        logger.warning("run_demand_stress_sweep: cannot list %s for sibling assets", source_inpx.parent)
        return
    for path in entries:
        try:
            if not path.is_file() or path.name == source_inpx.name:
                continue
            if path.suffix.lower() not in SIBLING_ASSET_EXTENSIONS:
                continue
            shutil.copyfile(path, work_dir / path.name)
        except OSError:
            logger.warning(
                "run_demand_stress_sweep: could not copy sibling asset %s", path, exc_info=True
            )


def _seed_values(seed_count: int) -> list[int]:
    """The deterministic seed LIST both variants share -- see module
    docstring's "Same seed set on both variants" section. Plain ``1..N``:
    reproducible run to run (unlike drawing N random seeds), and every value
    is a valid Vissim RandSeed (positive int)."""
    return list(range(1, seed_count + 1))


# ---------------------------------------------------------------------------
# One variant's 1..seed_count sub-runs, via the EXISTING job queue
# (tools_run._run_job_and_wait -- see module docstring's "Reuse, not
# reinvention").
# ---------------------------------------------------------------------------


def _run_variant(
    *, label: str, source_inpx: Path, duration_s: int, seeds: list[int]
) -> dict[str, Any]:
    """Submit ONE variant's seeds (a single job, N sequential sub-runs --
    exactly ``run_simulation``'s own multi-seed shape) and return
    ``{"label", "job_id", "tables", "metrics", "run_count", "per_seed"}``.

    Raises (never returns an error dict -- see tools_edit.py's "Failure
    shape" convention, followed identically here) when the job does not
    reach "done" or its result carries no SIMULATIONRUN row -- the SAME
    defense-in-depth re-check ``run_simulation`` itself performs, since a
    variant that silently "succeeds" without a verified completed run would
    poison the whole sweep's delta with a phantom number.
    """
    timeout_s = tools_run._job_timeout_s(duration_s, len(seeds))
    record = tools_run._run_job_and_wait(
        source_inpx=source_inpx, duration_s=duration_s, random_seeds=seeds, timeout_s=timeout_s
    )
    job_id = record.get("id")
    if record.get("status") != "done":
        raise RuntimeError(
            f"Demand-stress sweep: {label} variant job {job_id!r} did not complete "
            f"successfully: {record.get('error') or 'unknown error'}"
        )

    tables = {str(t).upper() for t in (record.get("result_tables") or [])}
    if "SIMULATIONRUN" not in tables:
        raise RuntimeError(
            f"Demand-stress sweep: {label} variant job {job_id!r} reported done, but its "
            "result database carries no SIMULATIONRUN row -- refusing to treat this as a "
            "completed run."
        )

    from services.simulation_runs import job_dir

    work_dir = job_dir(job_id)
    aggregate = tools_run._summarize_kpis(work_dir)

    per_seed: list[dict[str, Any]] = []
    for index, seed in enumerate(seeds):
        seed_dir = work_dir / f"seed_{index:02d}"
        seed_summary = tools_run._summarize_kpis(seed_dir)
        per_seed.append(
            {"seed": seed, "metrics": seed_summary["metrics"] if seed_summary else None}
        )

    return {
        "label": label,
        "job_id": job_id,
        "tables": sorted(tables),
        "metrics": aggregate["metrics"] if aggregate else None,
        "run_count": aggregate["run_count"] if aggregate else 0,
        "per_seed": per_seed,
    }


# ---------------------------------------------------------------------------
# Delta math -- degrades gracefully (None) exactly like _summarize_kpis does
# when a metric is missing on either side, never a crash on None arithmetic.
# ---------------------------------------------------------------------------


def _delta_entry(base_value: Any, scaled_value: Any) -> dict[str, Any]:
    if base_value is None or scaled_value is None:
        return {"base": base_value, "scaled": scaled_value, "abs_delta": None, "pct_delta": None}
    abs_delta = scaled_value - base_value
    pct_delta = (abs_delta / base_value * 100.0) if base_value != 0 else None
    return {
        "base": base_value,
        "scaled": scaled_value,
        "abs_delta": abs_delta,
        "pct_delta": pct_delta,
    }


def _build_delta(base_metrics: dict[str, Any] | None, scaled_metrics: dict[str, Any] | None) -> dict[str, Any]:
    base_metrics = base_metrics or {}
    scaled_metrics = scaled_metrics or {}
    keys = sorted(set(base_metrics) | set(scaled_metrics))
    return {key: _delta_entry(base_metrics.get(key), scaled_metrics.get(key)) for key in keys}


#: Preferred headline metric for the ko message's "총 지연 X% 증가" framing --
#: falls back to reporting "insufficient KPI data" honestly (never a guess)
#: when neither variant produced this metric (e.g. both runs too short for
#: evaluation intervals to elapse -- the exact results_missing_kpi_tables
#: scenario).
_HEADLINE_METRIC = "avg_delay_s"
_HEADLINE_LABEL_KO = "총 지연"


def _headline_text(delta: dict[str, Any]) -> str:
    entry = delta.get(_HEADLINE_METRIC)
    if not entry or entry.get("pct_delta") is None:
        return "헤드라인 KPI(총 지연) 데이터가 부족합니다 (평가 구간이 짧았을 수 있음)."
    pct = entry["pct_delta"]
    direction = "증가" if pct >= 0 else "감소"
    return (
        f"{_HEADLINE_LABEL_KO} {abs(pct):.1f}% {direction} "
        f"({entry['base']:.1f}s → {entry['scaled']:.1f}s)."
    )


# ---------------------------------------------------------------------------
# run_demand_stress_sweep(scale_percent, seed_count, duration_s, input_nos)
# ---------------------------------------------------------------------------


@tool(
    name="run_demand_stress_sweep",
    tier="run",
    provider="run",
    params=RunDemandStressSweepParams,
)
def run_demand_stress_sweep(params: RunDemandStressSweepParams) -> dict[str, Any]:
    """Run the current working network (BASE) and a demand-scaled clone of it (SCALED, scale_percent applied to every vehicle-input volume interval) through the simulation job queue with the SAME seed_count seeds on both sides, and report a per-KPI delta -- answers "차량 수요가 N% 늘면 어떻게 되나?" with simulated evidence. Runs 2 * seed_count total simulations (never fewer than 10, since seed_count is >=5). The working copy is NEVER mutated: the scaled variant is built on a separate temporary clone, verified byte-identical before/after via SHA-256. When proposing this action, your summary MUST state: the scale_percent, seed_count, that 2 * seed_count runs will execute, and that the working copy will remain untouched afterward -- the user needs all four facts to approve responsibly."""
    source = _require_active_source()
    working_hash_before = hashlib.sha256(source.read_bytes()).hexdigest()

    seeds = _seed_values(params.seed_count)
    tmp_dir: str | None = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="demand_stress_sweep_")
        scaled_path = Path(tmp_dir) / source.name
        scale_summary = scale_vehicle_input_volumes(
            source,
            scaled_path,
            scale_percent=params.scale_percent,
            input_nos=params.input_nos,
        )
        if params.input_nos is not None and scale_summary["unmatched_input_nos"]:
            raise RuntimeError(
                "Demand-stress sweep: input_nos "
                f"{scale_summary['unmatched_input_nos']!r} do not exist in the active network "
                "-- refusing before running anything. Use get_vehicle_inputs to see the "
                "available vehicle-input numbers."
            )
        # Sibling assets (external .vap/.layx/... the working copy's own
        # ArtifactManager.clone() already gathered -- see state.py's
        # SIBLING_ASSET_EXTENSIONS) live next to `source`, not next to the
        # freshly-created `scaled_path`; copy them alongside the scaled
        # clone too so the scaled variant's own load behavior matches the
        # base variant's as closely as possible. Best-effort (never raises).
        _copy_sibling_assets(source, Path(tmp_dir))

        base_result = _run_variant(
            label="base", source_inpx=source, duration_s=params.duration_s, seeds=seeds
        )
        scaled_result = _run_variant(
            label="scaled", source_inpx=scaled_path, duration_s=params.duration_s, seeds=seeds
        )
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    working_hash_after = hashlib.sha256(source.read_bytes()).hexdigest()
    working_copy_untouched = working_hash_before == working_hash_after
    if not working_copy_untouched:
        # This should be structurally impossible (scale_vehicle_input_volumes
        # only ever reads `source`) -- raising rather than silently reporting
        # a false "untouched" claim is the guardrail doctrine's "verify, not
        # trust" applied to this tool's own core promise.
        raise RuntimeError(
            "Demand-stress sweep: the working copy changed during the sweep "
            f"({working_hash_before} -> {working_hash_after}) -- refusing to report results "
            "against a network that may no longer be what was analyzed."
        )

    delta = _build_delta(base_result["metrics"], scaled_result["metrics"])

    runs: list[dict[str, Any]] = []
    for variant_result in (base_result, scaled_result):
        for entry in variant_result["per_seed"]:
            runs.append(
                {
                    "index": len(runs) + 1,
                    "variant": variant_result["label"],
                    "seed": entry["seed"],
                    "job_id": variant_result["job_id"],
                    "metrics": entry["metrics"],
                }
            )

    all_tables = sorted(set(base_result["tables"]) | set(scaled_result["tables"]))
    run_result = {
        # Same shape run_simulation returns -- see module docstring's
        # "Postrun guardrail reuse for free" section: this makes
        # constraints.py's applies_to=["*"] postrun checks fire against this
        # tool with zero constraints.py changes.
        "tables": all_tables,
        "deleted_vehicles": None,
    }

    scale_note = ""
    if abs(params.scale_percent) > _WARN_ABS_SCALE_PERCENT:
        scale_note = f" 참고: {params.scale_percent:+.0f}%는 큰 폭의 변화이므로 결과 해석에 유의하세요."

    message = _cap_message(
        f"수요 스트레스 스윕 완료: scale_percent={params.scale_percent:+.0f}%, "
        f"seed_count={params.seed_count} (총 {2 * params.seed_count}회 실행). "
        f"{_headline_text(delta)} 작업 사본은 변경되지 않았습니다.{scale_note}"
    )

    return {
        "message": message,
        "verification": {
            "working_copy_sha256_before": working_hash_before,
            "working_copy_sha256_after": working_hash_after,
            "working_copy_untouched": working_copy_untouched,
        },
        "artifacts": {
            "scale_input_summary": scale_summary,
            "runs": runs,
        },
        "run_result": run_result,
        "sweep": {
            "scale_percent": params.scale_percent,
            "seed_count": params.seed_count,
            "seeds": seeds,
            "duration_s": params.duration_s,
            "run_count": 2 * params.seed_count,
            "base": {
                "job_id": base_result["job_id"],
                "run_count": base_result["run_count"],
                "metrics": base_result["metrics"],
            },
            "scaled": {
                "job_id": scaled_result["job_id"],
                "run_count": scaled_result["run_count"],
                "metrics": scaled_result["metrics"],
            },
            "delta": delta,
        },
    }


__all__ = [
    "NoActiveSweepSourceError",
    "RunDemandStressSweepParams",
    "run_demand_stress_sweep",
]
