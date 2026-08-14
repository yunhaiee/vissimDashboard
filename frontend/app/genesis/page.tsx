"use client";

// Roadmap v2 §5.B row B1 (genesis becomes a product feature) + §5.A row A4
// (the gate is its exit door) + the minimal half of row B3 (the gate's verdict
// becomes visible to a human).
//
// Three backend surfaces, each read from the endpoint that OWNS its data
// rather than re-derived here:
//   * POST /api/genesis/uploads  -- stage the .osm (raw body + X-Filename, the
//     house two-step because this backend has no python-multipart);
//   * POST /api/genesis/jobs     -- start one generation, then poll
//     GET /api/genesis/jobs/{id} exactly as the browser would poll a run;
//   * GET  /api/genesis/jobs/{id}/gate -- the STORED verdict, check by check.
//
// -------------------------------------------------------------------------
// THE LAYOUT, AND WHY IT IS A SPLIT (U-1, unit #13)
// -------------------------------------------------------------------------
// docs/ux-backlog.md, the SECOND ELABORATION (2026-08-13) items 9-14. The user
// tested the previous single-column version live and reported: cards nested
// inside cards with the same title repeated three times, two refusal banners
// saying the same thing, and a history list carrying only a name and a date.
// The reference design is the Claude Console dashboard, and its list/detail
// pattern is what this page now is:
//
//   LEFT   the two things you DO — start a generation, and pick one from the
//          history TABLE (muted column headers, monospace ids and coordinates,
//          borderless rows whose height carries the separation, the verdict as
//          a small right-aligned tag chip, pagination once the list grows).
//   RIGHT  everything about the ONE row you picked, as a detail drawer:
//          verdict, map, artifacts, comparison. Sticky, so scrolling the
//          history does not scroll the thing you are reading.
//
// Nothing selected shows ONE quiet line, not an empty scaffold.
//
// -------------------------------------------------------------------------
// WHAT THIS PAGE MUST NOT DO, and the reason it is written this way
// -------------------------------------------------------------------------
// The R1.5 gate is a TRI-state per check (pass / fail / not-checkable) and a
// tri-state verdict, and "a check that could not run is never a pass" is the
// design rule the whole gate hangs on (docs/phase2/DECISIONS.md, 2026-08-11).
// So a not-checkable check is rendered in its own colour with its own word and
// its gap sentence, never as a grey "pending" or folded into a green tick, and a
// `provisional` publication says so on the card. Collapsing the states into
// ok/not-ok here would manufacture exactly the confidence the gate exists to
// refuse.
//
// Every gate sentence on this page comes from the backend: check titles,
// findings and gaps are rendered by services/genesis/gate.py's own ko/en/ja
// table and shipped in the payload. This file supplies the CHROME's ko/en/ja
// only, through the three-argument t() form (explicit ja) so no string here
// depends on an entry landing in the generated lib/i18n-ja.ts dictionary.

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import GenesisComparisonPanel from "@/components/genesis/GenesisComparisonPanel";
import GenesisTileMap from "@/components/genesis/GenesisTileMap";
import { InfoTip, PageHelp, Panel, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  applyGenesisFix,
  fetchGenesisArtifact,
  fetchGenesisGateReport,
  fetchGenesisJob,
  fetchGenesisJobFixes,
  fetchGenesisJobs,
  fetchStagedSideInputs,
  startGenesisJob,
  uploadGenesisOsm,
  type GenesisArtifactKind,
  type GenesisFixDisposition,
  type GenesisFixFinding,
  type GenesisGateCheck,
  type GenesisGateReport,
  type GenesisJob,
  type GenesisJobFixes,
  type GenesisJobStatus,
  type StagedSideInputEntry,
} from "@/lib/api";
import {
  formatTimestamp,
  networkScaleLabel,
  requestedRegionLabel,
  statusLabel,
} from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

/** How often a running job is re-read. Generation is seconds-to-a-minute of
 * pure Python (no COM, no Vissim), so a one-second poll is both cheap and
 * fast enough to feel live. */
const POLL_INTERVAL_MS = 1000;
//: A terminal job is still re-gatable (attach_live_check), so it keeps being
//: observed -- just slowly. 15 s is chosen against what it is watching for: a
//: human on another machine running the live probe and posting its state.json,
//: an action measured in minutes. Fast enough that nobody stares at a verdict
//: that has already been overturned, slow enough to be invisible in the logs.
const TERMINAL_RECHECK_MS = 15000;

/** Rows per page in the history table. The LIST REFERENCE (docs/ux-backlog.md)
 * asks for pagination arrows "when the list grows"; ten is the point at which
 * the table stops fitting beside the drawer without scrolling the page. */
const HISTORY_PAGE_SIZE = 10;

const TERMINAL: GenesisJobStatus[] = ["refused", "provisional", "published", "error"];

function isTerminal(status: GenesisJobStatus): boolean {
  return TERMINAL.includes(status);
}

// `statusLabel` (the four terminal states, each said in its own words --
// `provisional` deliberately does NOT read as success) and `formatTimestamp`
// now live in lib/genesisArtifacts.ts -- `GenesisComparisonPanel`'s job picker
// needs the exact same wording for the exact same fields, and a second,
// locally-drifting copy is how a picker option ends up showing a raw
// `job.status`/ISO timestamp next to this page's already-translated ones.

/**
 * THREE COLOURS, AND THE FOURTH STATE IS MONO. (User decision, 2026-08-13.)
 *
 * This screen may use exactly three accents and no others:
 *   rose    — fail / refused
 *   emerald — pass / published
 *   sky     — selection, info, links
 *
 * Everything that used to be AMBER — `not_checkable`, `provisional`, warn-tier
 * findings, a verdict resting on an attestation — is now MONO: ink-strength
 * text inside a heavier (2px) neutral border, or a filled neutral chip. It is
 * told apart by WEIGHT and SHAPE, never by a fourth hue.
 *
 * WHY THAT IS SAFE, and it is the only question that matters here. R1.5's rule
 * is that a check which could not RUN must never read as fine. What made the
 * old amber unsafe was that globals.css remaps the whole amber family to
 * NEUTRAL for this theme — so the state that most needed to stand out was
 * already rendering grey, at the SAME weight as ordinary chrome. Mono with a
 * doubled border, beside a green pass chip, is louder than that was, and the
 * word (확인 불가 / 잠정) is printed either way. A hue was dropped; no claim was.
 *
 * TINTED BOX = TINT IN THE BACKGROUND AND THE BORDER, INK IN THE TEXT — the
 * other half of the rule, and the contrast bug it fixes (U-1 second
 * elaboration item 11, plus the region page's blue-on-blue box reported the
 * same day). globals.css remaps the Tailwind ramps for dark, but only the
 * shades this app actually used: `bg-emerald-50` became a DARK green (#1e2b22)
 * while the emerald 900 foreground was never remapped and stayed Tailwind's
 * default #064e3b — dark green on dark green. Same trap for sky's 700/800/900
 * over `bg-sky-50` (#032042). So a tinted box takes its foreground from the
 * remapped LIGHT end of its own family: emerald-800 (#b0d5b6), rose-800
 * (#ecac9c), sky-600 (#6da7ec — literally the Console badge pairing the
 * palette lock names for this navy). Borders come from shades the remap covers
 * (`*-200`/`*-300`), never from `*-400+` of a tinted family.
 */
function statusClasses(status: GenesisJobStatus): string {
  switch (status) {
    case "published":
      return "border-emerald-300 bg-emerald-50 text-emerald-800";
    case "provisional":
      return "border-slate-400 bg-slate-100 text-slate-900";
    case "refused":
    case "error":
      return "border-rose-300 bg-rose-50 text-rose-800";
    default:
      return "border-sky-200 bg-sky-50 text-sky-600";
  }
}

/** PASS / FAIL / NOT CHECKABLE — three words, three treatments, never two.
 * `not_checkable` keeps its OWN NOUN, which is the part R1.5 actually requires:
 * a check that could not run may never be counted as a pass, so it may never
 * borrow the pass word. Its treatment is mono-with-a-doubled-border rather than
 * a third hue (see `statusClasses` above for the user's 2026-08-13 decision and
 * why greying it out is not what that amounts to). */
function checkStateLabel(state: GenesisGateCheck["state"], t: Translate): string {
  switch (state) {
    case "pass":
      return t("통과", "PASS", "合格");
    case "fail":
      return t("실패", "FAIL", "不合格");
    case "not_checkable":
      return t("확인 불가", "NOT CHECKABLE", "確認不可");
    default:
      return state;
  }
}

/**
 * THE STATE TAG CHIP — the ONLY thing on this screen that carries a check's
 * colour. (User decision from live review, 2026-08-13.)
 *
 * The nine cells used to be tinted CARDS: a whole emerald panel for a pass, a
 * whole rose one for a fail. Nine of them at once read as a block of colour to
 * decode rather than nine verdicts to scan, and the tint competed with the two
 * things that actually carry meaning — the check id and its state word. So the
 * card is mono for every state now (`checkCellClasses`) and the colour moved
 * onto the small chip that already prints the word.
 *
 * The doctrine is untouched and this is where to check that claim: `pass` and
 * `fail` keep their hues, `not_checkable` stays mono-with-a-doubled-border per
 * the three-colour rule, and the WORD is printed on every chip regardless. The
 * CHIP is the whole of the pre-reading signal now — the user's second live pass
 * (2026-08-13) removed the cells' state ring too, so every card wears the same
 * hairline and "something here is not fine" is the chip's weight and hue, one
 * glance away from WHICH not-fine it is.
 */
function checkStateTagClasses(state: GenesisGateCheck["state"]): string {
  if (state === "pass") return "border-emerald-300 bg-emerald-50 text-emerald-800";
  if (state === "fail") return "border-rose-300 bg-rose-50 text-rose-800";
  // Grey ink, not full-strength (user, live pass 2026-08-13): six 확인 불가
  // chips at ink strength outshouted the two verdicts that carry hue. The
  // doubled border stays — weight, not brightness, is what keeps this state
  // from reading as fine.
  return "border-2 border-slate-400 bg-slate-100 text-slate-600";
}

/** The card behind a check — IDENTICAL for every state, so the grid reads as a
 * field of nine equal cells and the eye goes to the chips. The user's second
 * live pass (2026-08-13) levelled the last state-driven difference: the
 * non-pass cells' heavier ring made their borders read as a second, thicker
 * kind of white line, so every card now wears the pass card's hairline and the
 * chip alone says what state lives inside. Selection is the ONLY thing that
 * changes the card — fill one step, plus the 2px ring. */
function checkCellClasses(isSelected: boolean): string {
  return isSelected
    ? "border-slate-300 bg-slate-100 text-slate-900 ring-2 ring-slate-500"
    : "border-slate-200 bg-slate-50 text-slate-800";
}

function verdictLabel(verdict: GenesisGateReport["verdict"], t: Translate): string {
  switch (verdict) {
    case "pass":
      return t("통과 — 출하 가능", "PASS — shippable", "合格 — 出荷可");
    case "refused":
      return t("거부 — 결함이 입증되었습니다", "REFUSED — a fault was proven", "拒否 — 欠陥が証明されました");
    case "incomplete":
      return t(
        "미완 — 실행되지 못한 거부 등급 검사가 있습니다 (통과 아님)",
        "INCOMPLETE — a refuse-tier check could not run (not a pass)",
        "未完 — 実行できなかった拒否ティア検査があります（合格ではありません）",
      );
    default:
      return verdict;
  }
}

/** The generation form's own unknown-code sentence, handed to the shared
 * `refusalHeadline` as its fallback: on THIS screen the refused request is a
 * generation, so the generic "요청이 거부되었습니다" would be a step vaguer than
 * the page can afford. Every known code lives in components/genesis/ui.tsx. */
function generationRefusalFallback(t: Translate): string {
  return t("생성이 거부되었습니다", "Generation was refused", "生成が拒否されました");
}

/** One dropdown line for a staged side input -- filename plus whatever its
 * OWN summary already states (never re-derived here, so the picker cannot say
 * something the staging response did not). */
function sideInputLabel(entry: StagedSideInputEntry): string {
  const summary = entry.summary ?? {};
  if (entry.kind === "demand") {
    const sites = Array.isArray(summary.sites_mapped) ? summary.sites_mapped.length : 0;
    const approaches = typeof summary.approaches_mapped === "number" ? summary.approaches_mapped : 0;
    return `${entry.filename} · ${sites}지점/${approaches}접근로`;
  }
  if (entry.kind === "signals") {
    const assignments = Array.isArray(summary.assignments) ? summary.assignments.length : 0;
    return `${entry.filename} · ${assignments}개 교차로`;
  }
  return entry.filename;
}

/** The cross-navigation chip. Pill-shaped, from the app's own
 * `.ui-button-ghost` primitive (U-1 second elaboration: "pill-style filter
 * chips from the existing .ui-button-ghost vocabulary") rather than a fourth
 * hand-rolled border/background triple. */
const CROSS_CHIP = "ui-button-ghost rounded-full px-3 py-1 text-[12px] font-medium";

/**
 * THE CONSOLE STAT — a small muted label above a large bold value.
 *
 * docs/ux-backlog.md, the SECOND ELABORATION's style transfer: this is the one
 * pattern the reference design uses wherever a number matters, and it is what
 * replaces the run-on "통과 7 · 실패 1 · 확인 불가 1" sentence the verdict
 * banner used to carry. Three stats side by side read as three FACTS; the
 * same three in a sentence read as prose to skim past.
 */
function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="min-w-[86px]">
      <p className="text-[12px] text-slate-500">{label}</p>
      <p className={`mt-0.5 text-[22px] font-semibold leading-none ${tone ?? "text-slate-900"}`}>
        {value}
      </p>
    </div>
  );
}

/** Everything a check records beyond its own state. Named once because the
 * detail area reads it to decide whether to render the "nothing recorded"
 * sentence instead of a blank. */
function checkHasBody(check: GenesisGateCheck): boolean {
  return (
    check.gaps.length > 0 ||
    check.findings.length > 0 ||
    check.warnings.length > 0 ||
    check.findings_omitted > 0
  );
}

// ---------------------------------------------------------------------------
// B5-2 stage 4 — the fix strip: "거부는 진단, 에이전트는 치료" made clickable.
// One GET (`fetchGenesisJobFixes`, fetched once per (job, verdict) exactly
// like the gate report is) hands back the registry's own disposition for
// EVERY finding in the job's STORED verdict; `GateCheckGrid`'s own detail
// area renders it under the SELECTED check's findings, never a second door
// into the registry.
// ---------------------------------------------------------------------------

/**
 * WHETHER A DISPOSITION IS ACTUALLY SOMETHING TO APPLY — and the trap this
 * function exists to close.
 *
 * `disposition.status === "AUTOMATABLE"` looks like the safe test, and it is
 * NOT: `routers/genesis.py`'s own `_render_fix_disposition` docstring names
 * "three shapes, never a fourth", and the third one is exactly this landmine
 * — an AUTOMATABLE-DECLARED mapping whose builder refused for a STRUCTURAL
 * reason (most importantly V6's MEASURED entrance,
 * `INPUT_VOLUME_IS_OBSERVED_DATA` — an observed count is the calibration
 * TARGET, never a knob to turn) reports `status: "AUTOMATABLE"`, a real
 * `verb`, AND `params: null` / `requires: []`, because the backend only ever
 * fills `params` when it holds a real proposal or a partially-filled
 * ask-first one. A button gated on `status` alone would render an enabled,
 * submittable "적용 승인" over a refusal — precisely the misrepresentation this
 * whole unit exists to prevent. `params !== null` is the one field the
 * backend itself treats as "there is something to apply".
 */
function fixDispositionIsActionable(disposition: GenesisFixDisposition): boolean {
  return disposition.status === "AUTOMATABLE" && disposition.params !== null;
}

/** ko/en/ja for `disposition.confidence` — mono wording, never a fourth
 * colour (the palette lock's own rule: rose/emerald/sky are the only
 * accents this app has). `NONE` is the ask-first card's own value (no
 * proposal exists yet to have a confidence about). */
function fixConfidenceLabel(confidence: string, t: Translate): string {
  switch (confidence) {
    case "ARITHMETIC_FROM_OBSERVED_COUNTS":
      return t("산술 계산값 (관측 카운트 기반)", "Arithmetic, from observed counts", "算術計算値（観測カウント基準）");
    case "CONSTRAINT_COMPUTED_CEILING":
      return t("제약이 계산한 상한값", "A constraint's own computed ceiling", "制約が計算した上限値");
    case "DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE":
      return t("수요 추정값 (미검증 진입부)", "A demand ESTIMATE (unverified entrance)", "需要推定値（未検証の流入部）");
    case "NONE":
      return t("근거 값 없음", "No confidence value", "根拠値なし");
    default:
      return confidence;
  }
}

/**
 * Unit D4's "수요 추정" (demand-estimate) framing — a distinct claim from the
 * literal `confidence` string. On a GET this route NEVER receives a
 * caller-supplied volume (`derive_fix_targets`'s own docstring: "threaded
 * from params ONLY"), so V6's ask-first shape reports `confidence: "NONE"`
 * with `reason_code: "EVIDENCE_INSUFFICIENT"` — the
 * `"DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE"` value only appears once a
 * human has supplied the volume and the SERVER re-derives the real proposal
 * at apply time. So this checks every shape D4's doctrine actually produces
 * on a GET, not only the literal confidence string that a future full
 * proposal would carry — it is what triggers the "수요 추정" sentence on the
 * ask-first card today, and would keep triggering it on a real one.
 */
function fixIsDemandEstimateFraming(disposition: GenesisFixDisposition): boolean {
  return (
    disposition.confidence === "DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE" ||
    disposition.assumptions.includes("ENTRANCE_NOT_VERIFIED_MEASURED") ||
    (disposition.verb === "set_vehicle_input_volume" && disposition.reason_code === "EVIDENCE_INSUFFICIENT")
  );
}

/** ko/en/ja for one `fixes.py` assumption token — the machine half of a
 * sentence the rationale already makes in prose; this is the short gloss
 * beside the bullet. An unrecognised token (catalogue drift) still shows the
 * raw token rather than nothing. */
function fixAssumptionLabel(token: string, t: Translate): string {
  switch (token) {
    case "INPUT_DRIVES_NODE_TOTAL":
      return t(
        "이 입력이 교차로 총량을 좌우한다고 가정합니다",
        "Assumes this input drives the node's total",
        "このインプットが交差点合計を左右すると仮定します",
      );
    case "SIBLING_ROUTES_UNCHANGED":
      return t(
        "다른 경로들의 상대 비율은 그대로 둔다고 가정합니다",
        "Assumes the other routes keep their current relative flow",
        "他の経路の相対比率はそのまま維持されると仮定します",
      );
    case "DECISION_MOVEMENTS_COMPLETE":
      return t(
        "제시된 이동류 목록이 이 결정의 전체 범위라고 가정합니다",
        "Assumes the listed movements are this decision's complete scope",
        "提示された移動流のリストがこの決定の全範囲であると仮定します",
      );
    case "INTERVAL_MATCHES_COUNT_PERIOD":
      return t(
        "선택한 구간이 실측 기간과 일치한다고 가정합니다",
        "Assumes the chosen interval matches the count's own period",
        "選択した区間が実測期間と一致すると仮定します",
      );
    case "ENTRANCE_NOT_VERIFIED_MEASURED":
      return t(
        "이 진입부가 실측 지점인지 확인되지 않았다는 전제입니다 (수요 추정)",
        "Assumes this entrance could not be verified as measured (a named demand estimate)",
        "この流入部が実測地点かは確認されていないという前提です（需要推定）",
      );
    default:
      return token;
  }
}

/** Label + input kind for one `disposition.requires` field name. The three
 * known fields are every one `_partial_fix_params` (`routers/genesis.py`)
 * can actually leave required today; a field this table has never seen still
 * gets an honest generic label (its own raw name) rather than a blank one,
 * and is treated as free text rather than assumed numeric. */
function fixRequiredFieldSpec(field: string, t: Translate): { label: string; numeric: boolean } {
  switch (field) {
    case "volume":
      return { label: t("교통량 (대/시)", "Volume (veh/h)", "交通量（台/時）"), numeric: true };
    case "lane_count":
      return { label: t("차로 수", "Lane count", "車線数"), numeric: true };
    case "pos_m":
      return { label: t("링크상 위치 (m)", "Position on link (m)", "リンク上の位置（m）"), numeric: true };
    default:
      return { label: field, numeric: false };
  }
}

/** `refusalHeadline` for an `applyGenesisFix` failure, plus its `reasons`
 * verbatim -- the same two-part shape `selected.refusal` already renders
 * elsewhere on this page, reused rather than re-invented for this box. */
function applyFixErrorText(caught: unknown, t: Translate): { headline: string; reasons: string[] } {
  if (caught instanceof GenesisError) {
    return {
      headline: refusalHeadline(
        caught.code,
        t,
        t("수정을 적용하지 못했습니다", "The fix could not be applied", "修正を適用できませんでした"),
      ),
      reasons: caught.reasons,
    };
  }
  return { headline: caught instanceof Error ? caught.message : String(caught), reasons: [] };
}

/**
 * ONE finding's disposition — EITHER an informational mono line (never a
 * button: a refusal is information, not a disabled control) OR an approval
 * card. Never a third shape.
 *
 * `isFirstForCode` guards a real backend trap, not a client-invented rule.
 * `POST .../fixes/apply` addresses a finding by CODE ALONE (its body carries
 * no `subject`), and `create_genesis_fix_job` resolves that to the FIRST
 * finding carrying that code in the stored verdict
 * (`genesis_jobs._finding_in_stored_gate`'s own docstring: "first mirrors
 * gate.py's own deterministic ordering"). When the SAME code names two
 * different objects in one verdict — two vehicle inputs both missing volume
 * is the real case, both carrying `v6_input_without_volume` — a click on the
 * SECOND card would submit params addressing the second object under a
 * finding_code that resolves to the first: `genesis_fix_params_target_mismatch`,
 * every time, no matter how correct the card's own proposal looked. Rather
 * than let a reader discover that from a 409, the second (and any later) card
 * never offers the button at all.
 */
function FixFindingRow({
  jobId,
  finding,
  isFirstForCode,
  t,
  lang,
  onApplied,
}: {
  jobId: string;
  finding: GenesisFixFinding;
  isFirstForCode: boolean;
  t: Translate;
  lang: string;
  onApplied: (job: GenesisJob) => void;
}) {
  const disposition = finding.disposition;
  const actionable = isFirstForCode && fixDispositionIsActionable(disposition);
  const rationale =
    lang === "ko" ? disposition.rationale.ko : lang === "ja" ? disposition.rationale.ja : disposition.rationale.en;

  const [approvalRationale, setApprovalRationale] = useState("");
  const [requiredValues, setRequiredValues] = useState<Record<string, string>>({});
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<GenesisError | Error | null>(null);

  const missingRequired = disposition.requires.some((field) => !requiredValues[field]?.trim());

  const submit = useCallback(async () => {
    if (!disposition.verb || disposition.params === null) return;
    setApplying(true);
    setApplyError(null);
    try {
      const params: Record<string, unknown> = { ...disposition.params };
      for (const field of disposition.requires) {
        const spec = fixRequiredFieldSpec(field, t);
        const raw = requiredValues[field] ?? "";
        params[field] = spec.numeric ? Number(raw) : raw;
      }
      const job = await applyGenesisFix(jobId, {
        finding_code: finding.code,
        verb: disposition.verb,
        params,
        approval_rationale: approvalRationale.trim() || undefined,
      });
      onApplied(job);
    } catch (caught) {
      setApplyError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setApplying(false);
    }
  }, [disposition, requiredValues, approvalRationale, jobId, finding.code, onApplied, t]);

  const error = applyError ? applyFixErrorText(applyError, t) : null;

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3 text-[13px] text-slate-800">
      <div className="flex flex-wrap items-baseline gap-2">
        {finding.origin === "warning" && <span className="text-[12px] text-slate-500">warn /</span>}
        <span className="font-mono text-[12px] font-semibold text-slate-700">{finding.code}</span>
        <span className="font-mono text-[12px] text-slate-500">{finding.subject}</span>
      </div>

      {!actionable ? (
        <div className="mt-2">
          {/* NOT_AUTOMATABLE (or a structural refusal wearing an AUTOMATABLE
            * status -- see fixDispositionIsActionable) -- ONE mono line, the
            * rationale verbatim, and NEVER a button. */}
          <p className="font-mono text-[13px] leading-relaxed text-slate-700">{rationale}</p>
          {!isFirstForCode && fixDispositionIsActionable(disposition) && (
            <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500">
              {t(
                "같은 코드의 발견이 이 판정에 여러 건입니다 — 자동 적용은 첫 번째 발견에만 연결됩니다. 그 발견을 먼저 적용해 재게이트한 뒤, 남은 발견이 새 판정의 첫 자리에 나타나면 그때 적용하십시오.",
                "This code appears more than once in this verdict — an apply only ever resolves to the FIRST finding carrying it. Apply that one first; once the re-gate runs, the remaining finding moves to the front of the new verdict's own list and can be applied then.",
                "同じコードの発見がこの判定に複数あります — 自動適用は最初の発見にのみ結び付きます。まずそれを適用して再ゲートし、残りの発見が新しい判定の先頭に現れたら、その時に適用してください。",
              )}
            </p>
          )}
          {disposition.reason_code && (
            <p className="mt-1 font-mono text-[12px] text-slate-500">{disposition.reason_code}</p>
          )}
        </div>
      ) : (
        <div className="mt-2 flex flex-col gap-2">
          <p className="font-mono text-[12px] text-slate-600">
            {t("동사", "Verb", "動詞")}: {disposition.verb}
          </p>
          <p className="leading-relaxed text-slate-700">{rationale}</p>

          <span
            className={`w-fit rounded-full border px-1.5 py-0.5 text-[12px] font-semibold ${
              fixIsDemandEstimateFraming(disposition)
                ? "border-2 border-slate-400 bg-slate-100 text-slate-900 underline decoration-dotted decoration-slate-400 underline-offset-4"
                : "border-slate-300 bg-white/[0.06] text-slate-600"
            }`}
          >
            {fixConfidenceLabel(disposition.confidence, t)}
          </span>

          {fixIsDemandEstimateFraming(disposition) && (
            <p className="text-[12px] leading-relaxed text-slate-600">
              {t(
                "※ 수요 추정 — 이 값은 검증된 실측이 아니라 명시된 추정치입니다. 실제 진입 교통량을 아신다면 이 제안 대신 그 값을 직접 입력해 주십시오.",
                "NB DEMAND ESTIMATE — this value is a named estimate, not a verified measurement. If you know the real entrance count, enter it directly instead of approving this.",
                "※ 需要推定 — この値は検証済みの実測ではなく、明示された推定値です。実際の流入交通量をご存知であれば、この提案の代わりに直接入力してください。",
              )}
            </p>
          )}

          {disposition.assumptions.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-4 text-[12px] leading-relaxed text-slate-600">
              {disposition.assumptions.map((token) => (
                <li key={token}>{fixAssumptionLabel(token, t)}</li>
              ))}
            </ul>
          )}

          {/* The inherited-auto-publish disclosure -- one sentence, not a
            * modal, on every approval card regardless of confidence. */}
          <p className="text-[12px] leading-relaxed text-slate-500">
            {t(
              "승인하면 실제 Vissim 편집이 실행되고 네트워크가 다시 게이트를 거칩니다 — 통과하면 그 결과가 자동으로 이 프로젝트의 활성 데이터셋으로 등록됩니다.",
              "Approving runs a real Vissim edit and re-gates the network — a pass automatically registers the result as this project's active dataset.",
              "承認すると実際のVissim編集が実行され、ネットワークは再度ゲートを通過します — 合格すればその結果は自動的にこのプロジェクトのアクティブなデータセットとして登録されます。",
            )}
          </p>

          {disposition.requires.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {disposition.requires.map((field) => {
                const spec = fixRequiredFieldSpec(field, t);
                return (
                  <label key={field} className="flex items-center gap-2 text-[12px] text-slate-700">
                    <span className="min-w-[120px]">{spec.label}</span>
                    <input
                      type={spec.numeric ? "number" : "text"}
                      value={requiredValues[field] ?? ""}
                      onChange={(event) =>
                        setRequiredValues((current) => ({ ...current, [field]: event.target.value }))
                      }
                      className="w-32 rounded-md border border-slate-300 bg-white/[0.06] px-2 py-1 text-[12px] text-slate-900"
                    />
                  </label>
                );
              })}
            </div>
          )}

          <label className="flex flex-col gap-1 text-[12px] text-slate-600">
            <span>{t("사유 (선택)", "Rationale (optional)", "理由（任意）")}</span>
            <input
              type="text"
              value={approvalRationale}
              onChange={(event) => setApprovalRationale(event.target.value)}
              maxLength={600}
              className="rounded-md border border-slate-300 bg-white/[0.06] px-2 py-1 text-[13px] text-slate-900"
            />
          </label>

          <div>
            <button
              type="button"
              disabled={applying || missingRequired}
              onClick={() => void submit()}
              className="ui-button-primary w-fit rounded-md px-3 py-1.5 text-[13px] disabled:cursor-not-allowed"
            >
              {applying ? t("적용하는 중…", "Applying…", "適用中…") : t("적용 승인", "Approve fix", "適用を承認")}
            </button>
          </div>

          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-800">
              <p className="font-semibold">{error.headline}</p>
              {error.reasons.length > 0 && (
                <ul className="mt-1 space-y-1">
                  {error.reasons.map((reason, index) => (
                    <li key={index} className="font-mono text-[12px] leading-relaxed">
                      {reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * V1–V9 AS A GRID — EVERY CELL STARTS CLOSED.
 *
 * REWRITTEN 2026-08-13 (docs/ux-backlog.md, SECOND ELABORATION item 12, the
 * user's own refinement after testing the auto-selecting version live): NO
 * microcopy on the cells, NO checkmark, and NO auto-select. The detail area
 * below renders NOTHING until the reader clicks a cell.
 *
 * WHY THAT DOES NOT WEAKEN THE DOCTRINE, which is the only question that
 * matters here. The rule this screen has always had to keep is "nothing reads
 * as fine unless it is fine" — a check that FAILED or could not RUN must be
 * visible without reading and without clicking. The previous version bought
 * that with an auto-expanded panel seeded by a severity RANK (`checkRank`,
 * `worstCheckId`), which is why those two functions and their pins existed.
 * They are gone, and what carries the doctrine now is on the CELLS themselves:
 *
 *   * every cell states its id and its tri-state WORD, with no click at all —
 *     `실패` and `확인 불가` are printed, never implied by a colour;
 *   * the state CHIP is visibly not the pass chip whether or not anything is
 *     selected: rose for a fail, a doubled mono border for 확인 불가 (see
 *     `checkStateTagClasses`). The cards themselves are identical for every
 *     state — the user's second live pass removed the non-pass cells' heavier
 *     ring, because two thicknesses of white card border read as two kinds of
 *     card — so the chip now carries the whole of "something here is not a
 *     pass", and it says which in the same glance.
 *
 * So severity is still readable at a glance; what it no longer does is decide
 * what the reader is looking at. A panel that opened itself was, in the user's
 * words from live use, a screen that answered a question nobody had asked yet.
 *
 * The selected cell is one step brighter (a fill step, a 2px ring, a brighter
 * title) over a ~100ms transition, so the click has an answer.
 */
function GateCheckGrid({
  checks,
  t,
  lang,
  jobId,
  fixes,
  fixesError,
  onFixApplied,
}: {
  checks: GenesisGateCheck[];
  t: Translate;
  lang: string;
  /** The job these findings belong to -- needed only to address
   * `POST .../fixes/apply`, never read for anything else here. */
  jobId: string;
  /** `null` while the (job, verdict)-keyed fetch is still in flight or has
   * not started; the page owns the fetch and its caching, this component
   * only renders what it is handed. */
  fixes: GenesisJobFixes | null;
  fixesError: GenesisError | Error | null;
  onFixApplied: (job: GenesisJob) => void;
}) {
  // NO initial selection, by the user's explicit instruction. `null` is the
  // mount state and stays until a click.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = selectedId ? checks.find((check) => check.id === selectedId) ?? null : null;

  // Which finding, for a given CODE, is the first one in this verdict's own
  // findings list -- job-wide, not per-check, because `POST .../fixes/apply`
  // resolves a `finding_code` against the PARENT's WHOLE stored verdict, not
  // against one check's own slice of it (see FixFindingRow's doc comment).
  const firstOccurrenceOfCode = useMemo(() => {
    const seen = new Set<string>();
    const map = new Map<string, boolean>();
    for (const finding of fixes?.findings ?? []) {
      const key = `${finding.code}::${finding.subject}`;
      map.set(key, !seen.has(finding.code));
      seen.add(finding.code);
    }
    return map;
  }, [fixes]);

  return (
    <div>
      {/* ~14px gaps (gap-3.5), the user's own measurement: the 6px grid read as
        * one block of colour rather than nine separate verdicts. */}
      <div className="grid grid-cols-3 gap-3.5">
        {checks.map((check) => {
          const isSelected = check.id === selectedId;
          return (
            <button
              key={check.id}
              type="button"
              onClick={() => setSelectedId((current) => (current === check.id ? null : check.id))}
              aria-pressed={isSelected}
              // The full title is on the cell as a hover string as well as in
              // the detail below: the cell clamps it to two lines, and a
              // clamped sentence a reader cannot recover is a dropped sentence.
              title={check.title}
              className={`flex flex-col gap-1.5 rounded-lg border px-3 py-2.5 text-left transition-[background-color,box-shadow,color] duration-100 ${checkCellClasses(isSelected)}`}
            >
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[13px] font-bold">{check.id}</span>
                <span
                  className={`rounded-full border px-1.5 py-0.5 text-[12px] font-bold uppercase ${checkStateTagClasses(check.state)}`}
                >
                  {checkStateLabel(check.state, t)}
                </span>
              </span>
              <span
                className={`line-clamp-2 text-[12px] leading-tight ${isSelected ? "font-semibold text-slate-900" : "font-medium"}`}
              >
                {check.title}
              </span>
            </button>
          );
        })}
      </div>

      {/* ONE detail region, mounted always and EMPTY until a click.
        * Mounted-but-empty rather than conditionally rendered, on purpose: an
        * aria-live region that only appears together with its content is a
        * region a screen reader was not watching when the content arrived, so
        * the selection would be announced to nobody. */}
      {/* The detail takes the SAME rule as the cells above it — mono card,
        * colour on the chip. Two vocabularies for one state ("the cell is grey
        * but its detail is green") is exactly the kind of drift the shared
        * helper exists to prevent. */}
      <div aria-live="polite">
        {selected && (
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-[13px] text-slate-800">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-mono text-[13px] font-bold">{selected.id}</span>
              <span
                className={`rounded-full border px-1.5 py-0.5 text-[12px] font-bold uppercase ${checkStateTagClasses(selected.state)}`}
              >
                {checkStateLabel(selected.state, t)}
              </span>
              <span className="text-[12px] text-slate-600">
                {selected.tier === "refuse"
                  ? t("거부 등급", "refuse tier", "拒否ティア")
                  : t("경고 등급", "warn tier", "警告ティア")}
              </span>
            </div>
            {/* The check's full title on its own line (user, live pass
              * 2026-08-13): inline after id+chip+tier it read as a fourth
              * fragment of the same phrase rather than the sentence the three
              * fragments describe. */}
            <p className="mt-1.5 font-semibold">{selected.title}</p>

            <div className="mt-2">
              {selected.gaps.length > 0 && (
                <ul className="space-y-1">
                  {selected.gaps.map((gap) => (
                    <li key={gap.code} className="text-[13px] leading-relaxed">
                      {t("실행 불가", "could not run", "実行不可")}: {gap.message}
                    </li>
                  ))}
                </ul>
              )}

              {selected.findings.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {selected.findings.map((finding, index) => (
                    <li key={`${finding.code}-${index}`} className="text-[13px] leading-relaxed">
                      {finding.message}
                    </li>
                  ))}
                </ul>
              )}
              {selected.findings_omitted > 0 && (
                <p className="mt-1.5 text-[12px] text-slate-600">
                  {t(
                    `이 화면에 표시되지 않은 항목 ${selected.findings_omitted}건 — gate.json 전체 내려받기에 모두 들어 있습니다.`,
                    `${selected.findings_omitted} more not shown here — all of them are in the gate.json download.`,
                    `この画面に表示されていない項目 ${selected.findings_omitted} 件 — gate.json のダウンロードにすべて含まれています。`,
                  )}
                </p>
              )}

              {selected.warnings.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {selected.warnings.map((warning, index) => (
                    <li key={`w-${warning.code}-${index}`} className="text-[13px] leading-relaxed">
                      warn / {warning.message}
                    </li>
                  ))}
                </ul>
              )}

              {!checkHasBody(selected) && (
                <p className="text-[13px] leading-relaxed text-slate-600">
                  {t(
                    "이 검사에 기록된 항목이 없습니다.",
                    "Nothing was recorded for this check.",
                    "この検査に記録された項目はありません。",
                  )}
                </p>
              )}

              {/* THE FIX STRIP -- roadmap B5-2 stage 4. Only for a check that
                * actually recorded something: an empty check has no finding
                * for the registry to have an opinion about. `fixes` is
                * fetched ONCE per (job, verdict) by the page, exactly like
                * the gate report above it -- this component only renders
                * what it is handed, never fetches on its own. */}
              {checkHasBody(selected) && (
                <div className="mt-4 border-t border-slate-200 pt-3">
                  <p className="text-[13px] font-semibold text-slate-800">
                    {t("수정 제안", "Fix proposals", "修正提案")}
                    <InfoTip
                      text={t(
                        "이 검사가 낸 각 발견에 대해, 등록된 수정 카탈로그가 자동으로 고칠 수 있는지와 그 근거를 답합니다. 자동 수정이 없다는 답은 정보이지 버튼이 아닙니다 — 거부는 진단, 에이전트는 치료.",
                        "For each finding this check raised, the registered fix catalogue answers whether it can be automatically repaired and on what evidence. \"No automated fix\" is information, never a button — a refusal is a diagnosis, and the agent is the treatment.",
                        "この検査が出した各発見について、登録済みの修正カタログが自動修正可能かとその根拠を答えます。「自動修正なし」は情報であり、ボタンではありません — 拒否は診断、エージェントは治療。",
                      )}
                    />
                  </p>

                  {!fixes && !fixesError && (
                    <p className="mt-2 text-[13px] text-slate-500">
                      {t("수정 제안을 불러오는 중…", "Loading fix proposals…", "修正提案を読み込み中…")}
                    </p>
                  )}

                  {fixesError && (
                    <p className="mt-2 text-[13px] leading-relaxed text-slate-600">
                      {fixesError instanceof GenesisError && fixesError.status === 401
                        ? t(
                            "수정 제안을 보려면 로그인이 필요합니다 — 로그인 후 다시 시도하세요.",
                            "Viewing fix proposals requires a signed-in user — sign in and try again.",
                            "修正提案の閲覧にはログインが必要です — ログイン後に再試行してください。",
                          )
                        : t(
                            "수정 제안을 불러오지 못했습니다.",
                            "Could not load fix proposals.",
                            "修正提案を読み込めませんでした。",
                          )}
                    </p>
                  )}

                  {fixes && (
                    <div className="mt-2 flex flex-col gap-3">
                      {fixes.findings
                        .filter((finding) => finding.check_id === selected.id)
                        .map((finding) => (
                          <FixFindingRow
                            key={`${finding.code}::${finding.subject}`}
                            jobId={jobId}
                            finding={finding}
                            isFirstForCode={
                              firstOccurrenceOfCode.get(`${finding.code}::${finding.subject}`) ?? true
                            }
                            t={t}
                            lang={lang}
                            onApplied={onFixApplied}
                          />
                        ))}
                      {fixes.findings.filter((finding) => finding.check_id === selected.id).length === 0 && (
                        <p className="text-[13px] text-slate-500">
                          {t(
                            "이 검사에는 개별 수정 제안이 없습니다.",
                            "No individual fix proposal for this check.",
                            "この検査には個別の修正提案がありません。",
                          )}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** The five downloadable artifacts, declared once so the row cannot grow a
 * sixth entry in its own shape. `kind` is the backend's own artifact kind
 * (`routers/genesis.py`'s `_ARTIFACT_MEDIA_TYPES`), never a filename. */
const ARTIFACT_KINDS: Array<{ kind: GenesisArtifactKind; label: (t: Translate) => string }> = [
  { kind: "inpx", label: (t) => t("네트워크 .inpx", "Network .inpx", "ネットワーク .inpx") },
  { kind: "compliance_md", label: (t) => t("§5 준수 리포트", "§5 compliance report", "§5 準拠レポート") },
  { kind: "gate_report", label: (t) => t("게이트 리포트", "Gate report", "ゲートレポート") },
  { kind: "gate_json", label: () => "gate.json" },
  { kind: "genesis_json", label: (t) => t("생성기 명부", "Generator census", "生成器の名簿") },
];

/** Hand the fetched bytes to the browser's own download. The object URL is
 * revoked on a timer rather than immediately after `click()`: several browsers
 * have not yet started reading the blob when the synchronous click returns, and
 * revoking under them cancels the download that just started. */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** Why a download did not happen, in the user's language and NEVER as silence.
 * The three statuses are named apart because the actions they call for are
 * different: sign in, switch 프로젝트, or nothing (the file was never
 * produced). Anything else keeps the thrown message verbatim. */
function downloadFailureText(caught: unknown, t: Translate): string {
  if (caught instanceof GenesisError) {
    if (caught.status === 401) {
      return t(
        "내려받기에는 로그인이 필요합니다 — 로그인 후 다시 시도하세요.",
        "Downloading an artifact requires a signed-in user — sign in and try again.",
        "ダウンロードにはログインが必要です — ログイン後に再試行してください。",
      );
    }
    if (caught.status === 403) {
      return t(
        "이 프로젝트의 파일을 받을 권한이 없습니다.",
        "You are not allowed to read this project's files.",
        "このプロジェクトのファイルを取得する権限がありません。",
      );
    }
    if (caught.status === 404) {
      return t(
        "이 생성에는 그 파일이 없습니다 — 또는 지금 선택된 프로젝트의 생성이 아닙니다.",
        "This generation has no such file — or it does not belong to the project selected right now.",
        "この生成にはそのファイルがありません — もしくは現在選択中のプロジェクトの生成ではありません。",
      );
    }
    return caught.message;
  }
  return caught instanceof Error ? caught.message : String(caught);
}

/**
 * THE ARTIFACT ROW, AND THE BUG IT REPLACES.
 *
 * Reported from live use (docs/ux-backlog.md, U-1-quick item #2): every one of
 * these five buttons did nothing. They were plain `<a href>` anchors built from
 * `genesisArtifactUrl()`, and that broke three ways at once — no session cookie
 * on the cross-origin URL `apiUrl()` produces, no `X-Workspace-Id` header an
 * anchor is capable of sending, and no branch anywhere to report either. The
 * evidence and the three mechanisms are written out at `fetchGenesisArtifact`
 * in lib/api.ts; that function is now the only way this route is addressed, and
 * `genesisArtifactUrl` is deleted rather than left lying next to it.
 *
 * The state here is per-ROW, not per-button, on purpose: only one download runs
 * at a time and only the most recent failure is on screen, so five simultaneous
 * error boxes can never stack up under a five-button row.
 */
function ArtifactRow({ jobId, t }: { jobId: string; t: Translate }) {
  const [pending, setPending] = useState<GenesisArtifactKind | null>(null);
  const [failure, setFailure] = useState<{ kind: GenesisArtifactKind; message: string } | null>(null);

  const download = useCallback(
    async (kind: GenesisArtifactKind) => {
      setPending(kind);
      setFailure(null);
      try {
        const artifact = await fetchGenesisArtifact(jobId, kind);
        saveBlob(artifact.blob, artifact.filename);
      } catch (caught) {
        setFailure({ kind, message: downloadFailureText(caught, t) });
      } finally {
        setPending(null);
      }
    },
    [jobId, t],
  );

  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {ARTIFACT_KINDS.map((artifact) => (
          <button
            key={artifact.kind}
            type="button"
            disabled={pending !== null}
            onClick={() => void download(artifact.kind)}
            className="ui-button-ghost rounded-full px-3 py-1 text-[12px] font-medium disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pending === artifact.kind
              ? t("내려받는 중…", "Downloading…", "ダウンロード中…")
              : artifact.label(t)}
          </button>
        ))}
      </div>
      {failure && (
        <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-800">
          <p className="font-semibold">
            {t("내려받지 못했습니다", "The download did not happen", "ダウンロードできませんでした")} (
            {failure.kind})
          </p>
          <p className="mt-1 leading-relaxed">{failure.message}</p>
        </div>
      )}
    </div>
  );
}

/** One titled block inside the detail drawer. Plain title + body, no card
 * around it: the drawer IS the card, and the disease the user named (item 9)
 * was a card inside a card inside a card, each repeating the same title. */
function DrawerSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-slate-200 pt-6">
      <h3 className="text-[15px] font-semibold text-slate-900">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function GenesisPage() {
  const { lang, t } = useLanguage();

  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [excludeService, setExcludeService] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<GenesisError | Error | null>(null);

  // The optional layers' pickers -- staged through /genesis/demand or
  // /genesis/phases (or the raw POST /side-inputs), listed here by KIND only:
  // nothing on this page assembles a document, it only names a token
  // startGenesisJob already accepts. Both default to "" (none), the
  // geometry-only path this form has always taken.
  const [sideInputs, setSideInputs] = useState<StagedSideInputEntry[]>([]);
  const [demandToken, setDemandToken] = useState("");
  const [signalsToken, setSignalsToken] = useState("");

  const [jobs, setJobs] = useState<GenesisJob[]>([]);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  // NOTHING IS SELECTED ON ARRIVAL. The previous version auto-selected the
  // newest generation, which meant the whole right-hand column was already
  // full of one job's verdict before the reader had chosen anything to look
  // at — the same "answers a question nobody asked" the V1-V9 grid had.
  const [selectedId, setSelectedId] = useState<string>("");
  // Both fetched payloads are stored WITH the key they were fetched for, and
  // read back only when that key still matches. Clearing them from inside the
  // effect body instead would mean a synchronous setState on every selection
  // change -- a cascading render, and (worse) a frame in which the previous
  // job's verdict is on screen under the new job's heading.
  const [selectedEntry, setSelectedEntry] = useState<{ id: string; record: GenesisJob } | null>(null);
  const [reportEntry, setReportEntry] = useState<
    { jobId: string; language: string; payload: GenesisGateReport } | null
  >(null);
  const [reportError, setReportError] = useState<string | null>(null);
  // Roadmap B5-2 stage 4 -- the fix strip's own fetch, keyed the SAME way the
  // gate report above it is (job id + the moment the verdict was decided),
  // but WITHOUT `language` in the key: `GET .../fixes` takes no `language`
  // query param at all -- `disposition.rationale` already comes back
  // trilingual (`FixProposal.rationale()`), so a language toggle needs no
  // re-fetch here the way the gate report's own per-language render does.
  // `fixesError` keeps the thrown error itself (not a stringified message)
  // so the render side can distinguish a 401 (unauthenticated -- the named
  // degradation path) from any other failure, the same distinction
  // `downloadFailureText` already draws for artifact downloads.
  const [fixesEntry, setFixesEntry] = useState<
    { jobId: string; checkedAt: string; payload: GenesisJobFixes } | null
  >(null);
  const [fixesError, setFixesError] = useState<GenesisError | Error | null>(null);

  const selected = selectedEntry && selectedEntry.id === selectedId ? selectedEntry.record : null;
  const report =
    reportEntry && selected && reportEntry.jobId === selected.id && reportEntry.language === lang
      ? reportEntry.payload
      : null;
  const fixes =
    fixesEntry && selected && fixesEntry.jobId === selected.id && fixesEntry.checkedAt === (selected.gate?.checked_at ?? "")
      ? fixesEntry.payload
      : null;

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadJobs = useCallback(async () => {
    try {
      const entries = await fetchGenesisJobs();
      setJobs(entries);
      setJobsError(null);
    } catch (caught) {
      setJobsError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  const loadSideInputs = useCallback(async () => {
    try {
      setSideInputs(await fetchStagedSideInputs());
    } catch {
      // Best-effort: a picker with nothing in it still lets the geometry-only
      // path work, so a failed listing must not block the form.
      setSideInputs([]);
    }
  }, []);

  useEffect(() => {
    void loadSideInputs();
  }, [loadSideInputs]);

  // Poll the SELECTED job while it is still running. The job record is the only
  // source of truth for progress; nothing here guesses a percentage the backend
  // did not state.
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const record = await fetchGenesisJob(selectedId);
        if (cancelled) return;
        setSelectedEntry({ id: selectedId, record });
        if (!isTerminal(record.status)) {
          timer = setTimeout(() => void tick(), POLL_INTERVAL_MS);
        } else {
          void loadJobs();
          // Terminal is NOT final. `attach_live_check` re-gates an already
          // terminal job: a provisional network can become published, or —
          // if the evidence records load_ok: false — refused, with its
          // publication revoked. Whoever attaches that evidence is usually not
          // the person watching this page (the probe runs on the COM box), so
          // stopping the poll here left an operator reading a verdict that had
          // already been overturned. Keep observing, slowly: the gate-report
          // effect below is keyed on `checked_at`, which only changes when the
          // record is re-fetched, so this is what makes that key mean anything.
          timer = setTimeout(() => void tick(), TERMINAL_RECHECK_MS);
        }
      } catch (caught) {
        if (!cancelled) setJobsError(caught instanceof Error ? caught.message : String(caught));
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedId, loadJobs]);

  // The gate report, once the selected job has one. Read from the STORED
  // verdict endpoint, so what is on screen is the verdict that actually decided
  // whether the artifact was published -- never a fresh one that could differ.
  // `selected` is replaced on every poll tick, so the effect is keyed on the
  // job id plus the moment the verdict was decided plus the language: one gate
  // fetch per (job, verdict, language) rather than one per second. `checked_at`
  // is in the key rather than omitted because a re-gate (live-check evidence)
  // changes the verdict WITHOUT changing the job id.
  const gatedJobId = selected?.gate ? selected.id : null;
  const gateCheckedAt = selected?.gate?.checked_at ?? null;
  useEffect(() => {
    if (!gatedJobId) return;
    let cancelled = false;
    fetchGenesisGateReport(gatedJobId, lang)
      .then((payload) => {
        if (!cancelled) {
          setReportEntry({ jobId: gatedJobId, language: lang, payload });
          setReportError(null);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setReportError(caught instanceof Error ? caught.message : String(caught));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gatedJobId, gateCheckedAt, lang]);

  // The fix strip's own data, fetched once per (job, verdict) -- same
  // discipline as the gate report effect immediately above, deliberately a
  // SEPARATE effect (not folded into it) because the two can fail
  // independently: `GET .../gate` is open to an anonymous caller and
  // `GET .../fixes` is not (routers/genesis.py's own docstring names the
  // same laundering argument `download_artifact` does), so an anonymous
  // visitor sees a full verdict AND a named 401 degradation here rather than
  // either request's failure taking the other down with it.
  useEffect(() => {
    if (!gatedJobId) return;
    let cancelled = false;
    setFixesError(null);
    fetchGenesisJobFixes(gatedJobId)
      .then((payload) => {
        if (!cancelled) {
          setFixesEntry({ jobId: gatedJobId, checkedAt: gateCheckedAt ?? "", payload });
          setFixesError(null);
        }
      })
      .catch((caught) => {
        if (!cancelled) {
          setFixesError(caught instanceof Error ? caught : new Error(String(caught)));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gatedJobId, gateCheckedAt]);

  // On a 202 from POST .../fixes/apply: the sibling job record IS the
  // selection -- point the drawer at it through the SAME mechanics `submit`
  // (below) uses for a freshly-started ordinary generation, so the reader
  // watches the re-gate verdict arrive through the existing polling effect
  // rather than through anything fix-specific. Refresh the history list so
  // the sibling appears there too (a new row, same name as its parent --
  // the drawer's own 파생 line is what tells the two apart, per B5-2 stage 4).
  const handleFixApplied = useCallback(
    (job: GenesisJob) => {
      setSelectedId(job.id);
      setSelectedEntry({ id: job.id, record: job });
      setPage(0);
      void loadJobs();
    },
    [loadJobs],
  );

  const submit = useCallback(async () => {
    if (!file) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const staged = await uploadGenesisOsm(file);
      const started = await startGenesisJob({
        upload_token: staged.token,
        name: name.trim() || undefined,
        exclude_service: excludeService,
        demand_token: demandToken || undefined,
        signals_token: signalsToken || undefined,
      });
      setSelectedId(started.id);
      setSelectedEntry({ id: started.id, record: started });
      setPage(0);
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setDemandToken("");
      setSignalsToken("");
      void loadJobs();
      // The chosen tokens are discarded server-side once the job owns its
      // frozen copies (keep_side_inputs defaults to false), so the pickers'
      // list has to be re-read -- otherwise a used token would keep showing as
      // available until the next full page load.
      void loadSideInputs();
    } catch (caught) {
      setSubmitError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setSubmitting(false);
    }
  }, [file, name, excludeService, demandToken, signalsToken, loadJobs, loadSideInputs]);

  // The three cross-navigations, each label paired with the sentence that says
  // WHY that screen exists. Declared once and read twice (the chip's `title`
  // and the disclosure list below it) so the hover text and the disclosed text
  // can never say two different things.
  const regionHint = {
    label: t("영역에서 추출하기 →", "Extract a region →", "領域から抽出 →"),
    note: t(
      ".osm 파일이 없으신가요? 지도 영역을 직접 지정해 OpenStreetMap에서 받아올 수 있습니다 — 받은 추출은 여기 업로드한 파일과 똑같은 경로를 탑니다.",
      "No .osm file? Name a map region and it is fetched from OpenStreetMap for you — the extract then takes exactly the same road as a file uploaded here.",
      ".osm ファイルがありませんか？地図の領域を指定して OpenStreetMap から取得できます — 取得した抽出はここでアップロードしたファイルとまったく同じ経路を通ります。",
    ),
  };
  const phasesHint = {
    label: t("현시도 확인하기 →", "Review the 현시도 →", "現示図を確認 →"),
    note: t(
      "대전 신호 시트를 쓰시나요? 현시도는 그림이라 어떤 파싱도 접근로–현시 배정을 산출하지 못합니다. 검토 화면에서 사람이 확인한 배정만 신호 입력이 됩니다.",
      "Using the 대전 signal sheets? The 현시도 is a drawing, so no parse yields the approach→phase mapping. Only a mapping a human confirmed on the review screen becomes signal input.",
      "大田の信号シートをお使いですか？現示図は図面のため、解析では流入路と現示の対応を得られません。確認画面で人が確認した対応のみが信号入力になります。",
    ),
  };
  const demandHint = {
    label: t("수요 입력 만들기 →", "Build a demand input →", "需要入力を作成 →"),
    note: t(
      "수요(교통량) 카운트가 있으신가요? 대전 스마트교차로 워크북을 올리고 접근로를 링크에 연결하면 아래 목록에서 고를 수 있는 수요 입력이 만들어집니다.",
      "Have traffic counts? Upload a Daejeon smart-intersection workbook and bind each approach to a link, and a demand input the dropdown below can pick up is built.",
      "交通量カウントをお持ちですか？大田スマート交差点のワークブックをアップロードし、流入路をリンクに対応付けると、下の一覧で選べる需要入力が作成されます。",
    ),
  };

  const pageCount = Math.max(1, Math.ceil(jobs.length / HISTORY_PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const visibleJobs = useMemo(
    () => jobs.slice(safePage * HISTORY_PAGE_SIZE, safePage * HISTORY_PAGE_SIZE + HISTORY_PAGE_SIZE),
    [jobs, safePage],
  );

  // UNIT #20 -- the page help drawer's content. 제외 도로/판정 게이트/잠정 등록
  // are the three doctrine sentences MOVED here verbatim from the inline ⓘs
  // they used to sit in (see the removal comments at each of their old
  // positions above); 생성 과정/수요·신호 입력/지도 are new entries covering
  // ground that used to be either always-visible prose (purged 2026-08-14) or
  // a component-local ⓘ this page's own file does not own (`GenesisTileMap`'s
  // map ⓘ, kept inline there -- see this unit's report for why).
  const genesisHelpEntries = [
    {
      title: t("생성 과정", "How generation runs", "生成の流れ"),
      body: t(
        "생성은 항상 잡 큐를 통해 실행됩니다 — 이 화면이 Vissim COM을 직접 여는 일은 없습니다. 생성을 시작하려면 로그인해야 합니다.",
        "A generation always runs through the job queue — this screen never opens Vissim COM directly. Starting a generation requires a signed-in user.",
        "生成は常にジョブキューを通じて実行されます — この画面が Vissim COM を直接開くことはありません。生成の開始にはログインが必要です。",
      ),
    },
    {
      title: t("수요·신호 입력", "Demand & signals input", "需要・信号入力"),
      body: t(
        "두 목록은 이미 스테이징된 입력 중에서 고르기만 합니다 — 이 화면에서 만들지 않습니다. 옆의 '만들기 →' 링크가 그 입력을 실제로 만드는 화면으로 이동합니다.",
        "Both dropdowns only PICK from an already-staged input — nothing is built here. The '만들기 →' link beside each one goes to the screen that actually builds it.",
        "両方の一覧は、すでにステージング済みの入力から選ぶだけです — この画面では作成しません。隣の「作成 →」リンクが、実際に作成する画面へ移動します。",
      ),
    },
    {
      title: t("제외 도로", "Excluded roads", "除外道路"),
      body: t(
        "제외된 도로는 버려지지 않고 이름이 남습니다 — 준수 리포트에 사유와 함께 기록됩니다.",
        "Excluded ways are never dropped silently — each is named with its reason in the compliance report.",
        "除外された道路は黙って捨てられず、準拠レポートに理由とともに記録されます。",
      ),
    },
    {
      title: t("판정 게이트", "The validation gate", "判定ゲート"),
      body: t(
        "R1.5 검증 게이트 V1–V9. 실행되지 못한 검사는 절대 통과가 아닙니다.",
        "The R1.5 validation gate, V1–V9. A check that could not run is never a pass.",
        "R1.5検証ゲート V1–V9。実行できなかった検査は決して合格ではありません。",
      ),
    },
    {
      title: t("잠정 등록", "Provisional registration", "暫定登録"),
      body: t(
        "프로젝트에 등록되었습니다. 다만 실행되지 못한 거부 등급 검사가 있어 잠정 상태입니다 — 생성 잡은 Vissim COM을 열 수 없으므로, 실제 Vissim이 이 파일을 여는 것을 증명하는 V1은 라이브 검사 증거를 붙여야 확정됩니다.",
        "Registered in the project, but provisional: a refuse-tier check could not run. A generation job may not open Vissim COM, so V1 — the only check that proves a real Vissim opens this file — stays open until live-check evidence is attached.",
        "プロジェクトに登録されましたが、実行できなかった拒否ティア検査があるため暫定です。生成ジョブは Vissim COM を開けないため、実際の Vissim がこのファイルを開くことを証明する V1 はライブ検査の証拠を添付するまで確定しません。",
      ),
    },
    {
      title: t("지도", "The map", "地図"),
      body: t(
        "지도는 이 생성이 기록한 좌표를 그립니다. 초안이 OpenStreetMap 좌표에서 만들어졌다면 서버가 역변환한 실제 WGS84 좌표를 배경지도 위에 그리고, 그렇지 않다면 배경지도 없이 이 생성만의 로컬 좌표로 그립니다 — 어느 쪽인지는 지도 제목 옆 ⓘ에 그때그때 표시됩니다.",
        "The map draws the coordinates this generation recorded. If the draft was built from OpenStreetMap degrees, the server's own inverse projection places it on a real WGS84 basemap; otherwise it is drawn with no basemap, in this generation's own local coordinates — which one applies is stated by the ⓘ beside the map's own title.",
        "地図はこの生成が記録した座標を描きます。下書きが OpenStreetMap の座標から作られていれば、サーバーが逆変換した実際の WGS84 座標を背景地図の上に描き、そうでなければ背景地図なしでこの生成専用のローカル座標で描きます — どちらであるかは地図タイトル横の ⓘ にその都度表示されます。",
      ),
    },
  ];

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-8">
      <header className="mb-8">
        <h1 className="flex items-center text-[22px] font-semibold tracking-tight text-white">
          {t("네트워크 생성 (Genesis)", "Network Genesis", "ネットワーク生成 (Genesis)")}
          <PageHelp entries={genesisHelpEntries} />
        </h1>
        <p className="mt-1 text-[13px] text-slate-500">
          {t(
            "OpenStreetMap 추출(.osm)에서 Vissim 네트워크 초안을 만들고, R1.5 검증 게이트를 통과한 것만 프로젝트에 등록합니다.",
            "Build a draft Vissim network from an OpenStreetMap extract (.osm); only what the R1.5 gate lets through is registered in the project.",
            "OpenStreetMap抽出(.osm)からVissimネットワークの下書きを作成し、R1.5検証ゲートを通過したものだけをプロジェクトに登録します。",
          )}
        </p>
      </header>

      {/* THE SPLIT, and its PROPORTION (user decision, 2026-08-13). Left =
        * controls + history; right = the selected row's detail drawer, sticky
        * on a tall screen so scrolling the table does not scroll what you are
        * reading off it.
        *
        * The drawer takes ~46% of the content width, not a fixed narrow column:
        * it has to hold a nine-cell verdict grid, a real map and an artifact
        * row without any of them wrapping into a stack of one-item lines. A
        * CSS grid rather than flex, because `minmax(480px, 46%)` states both
        * halves of that in one place — never thinner than the verdict grid
        * needs, never wider than half the page — and lets the LEFT column
        * compress gracefully (`minmax(0, 1fr)`) instead of forcing a horizontal
        * scrollbar on the whole page. */}
      <div className="flex flex-col gap-8 xl:grid xl:grid-cols-[minmax(0,1fr)_minmax(480px,46%)] xl:items-start xl:gap-8">
        <div className="min-w-0">
          {/* ------------------------------------------------------------- */}
          {/* Start a generation                                            */}
          {/* ------------------------------------------------------------- */}
          <Panel
            density="console"
            title={t("새 생성", "New generation", "新規生成")}
            subtitle={t(
              "로컬 .osm 파일을 선택하세요.",
              "Pick a local .osm file.",
              "ローカルの .osm ファイルを選んでください。",
            )}
            /* ONE primary action, top-right of the section — the Console list
             * pattern. It is the page's only primary button; everything else
             * (cross-navigation, artifacts, pagination) is a ghost pill. */
            action={
              <button
                type="button"
                disabled={!file || submitting}
                onClick={() => void submit()}
                className="ui-button-primary shrink-0 px-3.5 py-1.5 text-[13px] disabled:cursor-not-allowed"
              >
                {submitting
                  ? t("시작하는 중…", "Starting…", "開始中…")
                  : t("생성 시작", "Start generation", "生成を開始")}
              </button>
            }
          >
            <div className="flex flex-col gap-4">
              {/* Step 1 is a BINARY — a local file or a drawn region — so its
                * two halves sit on one row (backlog item 6, finished in #18).
                * The extract link used to live in a chip row at the bottom of
                * the form, three visual steps away from the input it is the
                * alternative to. */}
              <div className="flex flex-wrap items-center gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".osm,.xml"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                  className="block min-w-[240px] flex-1 text-[13px] text-slate-700 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white/[0.06] file:px-3 file:py-1.5 file:text-[13px] file:font-semibold file:text-slate-700"
                />
                {/* "또는" and its link wrap as ONE unit: measured 1280–1335px
                  * (just past the xl split), the link alone dropped to the next
                  * line, orphaned from the word that introduces it. */}
                <span className="flex items-center gap-2 whitespace-nowrap">
                  <span className="text-[12px] text-slate-500">{t("또는", "or", "または")}</span>
                  <span title={regionHint.note}>
                    <Link href="/genesis/region" className={CROSS_CHIP}>
                      {regionHint.label}
                    </Link>
                  </span>
                </span>
              </div>

              <label className="flex flex-col gap-1 text-[13px] text-slate-600">
                {t("네트워크 이름 (선택)", "Network name (optional)", "ネットワーク名（任意）")}
                <input
                  type="text"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={file ? file.name.replace(/\.(osm|xml)$/i, "") : "murdoch"}
                  className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[13px] text-slate-800"
                />
              </label>

              <label className="flex items-start gap-2 text-[13px] text-slate-600">
                <input
                  type="checkbox"
                  checked={excludeService}
                  onChange={(event) => setExcludeService(event.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  {t(
                    "highway=service 도로 제외 (주차장 통로·진입로·뒷골목)",
                    "Exclude highway=service ways (car-park aisles, driveways, back lanes)",
                    "highway=service の道路を除外（駐車場通路・進入路・裏路地）",
                  )}
                  {/* Unit #20: the disclosure that excluding is not deleting is
                    * doctrine (why-designed-this-way, not a validation-state
                    * meaning), so it moved out of this inline ⓘ and into the
                    * page's own help drawer (`?` beside the h1) under 제외 도로
                    * -- same sentence, verbatim, one level up. */}
                </span>
              </label>

              {/* The optional layers' pickers. Both default to the empty option
                * (none), which is the geometry-only path this form has always
                * taken -- picking one only NAMES a token already staged and
                * validated; nothing here builds or edits a document. */}
              <div className="flex flex-wrap gap-3">
                {/* Each dropdown carries ITS maker as a small link on the label
                  * row (backlog item 6): the 만들기 pages and these selects used
                  * to read as duplicate ways to "add demand/signals", when the
                  * pages PRODUCE what the dropdowns merely pick. Putting the
                  * link where its product lands is the sentence-free way to say
                  * that. (Clicks on a link inside a label navigate; they do not
                  * activate the labelled control.) */}
                <label className="flex flex-1 flex-col gap-1 text-[13px] text-slate-600">
                  <span className="flex items-baseline justify-between gap-2">
                    {t("수요 입력 (선택)", "Demand input (optional)", "需要入力（任意）")}
                    <span title={demandHint.note}>
                      <Link href="/genesis/demand" className={CROSS_CHIP}>
                        {t("만들기 →", "Build →", "作成 →")}
                      </Link>
                    </span>
                  </span>
                  <select
                    value={demandToken}
                    onChange={(event) => setDemandToken(event.target.value)}
                    className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[13px] text-slate-800"
                  >
                    <option value="">{t("없음", "none", "なし")}</option>
                    {sideInputs
                      .filter((entry) => entry.kind === "demand")
                      .map((entry) => (
                        <option key={entry.token} value={entry.token}>
                          {sideInputLabel(entry)}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="flex flex-1 flex-col gap-1 text-[13px] text-slate-600">
                  <span className="flex items-baseline justify-between gap-2">
                    {t("신호 입력 (선택)", "Signals input (optional)", "信号入力（任意）")}
                    <span title={phasesHint.note}>
                      <Link href="/genesis/phases" className={CROSS_CHIP}>
                        {t("현시도 확인 →", "Confirm 현시도 →", "現示図確認 →")}
                      </Link>
                    </span>
                  </span>
                  <select
                    value={signalsToken}
                    onChange={(event) => setSignalsToken(event.target.value)}
                    className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[13px] text-slate-800"
                  >
                    <option value="">{t("없음", "none", "なし")}</option>
                    {sideInputs
                      .filter((entry) => entry.kind === "signals")
                      .map((entry) => (
                        <option key={entry.token} value={entry.token}>
                          {sideInputLabel(entry)}
                        </option>
                      ))}
                  </select>
                </label>
              </div>

              {/* (User text-purge 2026-08-14: the dropdowns-only-pick sentence
                * and the login note left the screen — the label-row links say
                * the first, and an unauthenticated start answers with its own
                * named refusal for the second.) */}

              {submitError && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-800">
                  <p className="font-semibold">
                    {submitError instanceof GenesisError
                      ? refusalHeadline(submitError.code, t, generationRefusalFallback(t))
                      : t("생성을 시작하지 못했습니다", "Could not start the generation", "生成を開始できませんでした")}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap font-mono text-[12px] leading-relaxed">
                    {submitError.message}
                  </p>
                </div>
              )}
            </div>

            {/* Backlog item 6, finished in unit #18. The three cross-links were
              * one flat chip row down here, which read as three equal choices;
              * they now sit WHERE THEY ACT — 영역 추출 beside the file input
              * (step 1's binary), the two 만들기 links beside the dropdowns
              * they feed (step 2). Every `note` sentence is kept: as the hover
              * title on each relocated link (`title` on the WRAPPER span, never
              * on the anchor — measured 2026-08-13, an anchor's title becomes
              * its accessible NAME, WCAG 2.5.3) and verbatim in this disclosure,
              * both from the same constants so they can never drift. */}
            <div className="mt-6 border-t border-slate-200 pt-4">
              <details>
                <summary className="cursor-pointer text-[12px] text-slate-500">
                  {t(
                    "추출·현시도·수요 입력이 무엇인가요?",
                    "What are extract, 현시도 and demand input?",
                    "抽出・現示図・需要入力とは？",
                  )}
                </summary>
                <ul className="mt-2 space-y-1.5">
                  {[regionHint, phasesHint, demandHint].map((entry) => (
                    <li key={entry.label} className="text-[12px] leading-relaxed text-slate-600">
                      <span className="font-semibold text-slate-700">{entry.label}</span> — {entry.note}
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          </Panel>

          {/* ------------------------------------------------------------- */}
          {/* Generation history — the Console list                          */}
          {/* ------------------------------------------------------------- */}
          <Panel
            density="console"
            title={t("생성 이력", "Generations", "生成履歴")}
            subtitle={t(
              "이 프로젝트의 생성만 보입니다. 행을 누르면 오른쪽에 그 생성의 판정이 열립니다.",
              "Only this project's generations are listed. Click a row and its verdict opens on the right.",
              "このプロジェクトの生成のみ表示されます。行を押すと右にその生成の判定が開きます。",
            )}
            action={
              jobs.length > HISTORY_PAGE_SIZE ? (
                <div className="flex shrink-0 items-center gap-1.5 text-[12px] text-slate-500">
                  <span className="font-mono">
                    {safePage * HISTORY_PAGE_SIZE + 1}–
                    {Math.min(jobs.length, (safePage + 1) * HISTORY_PAGE_SIZE)} / {jobs.length}
                  </span>
                  <button
                    type="button"
                    disabled={safePage === 0}
                    onClick={() => setPage((current) => Math.max(0, current - 1))}
                    aria-label={t("이전 쪽", "Previous page", "前のページ")}
                    className="ui-button-ghost rounded-md px-2 py-0.5 disabled:opacity-40"
                  >
                    ‹
                  </button>
                  <button
                    type="button"
                    disabled={safePage >= pageCount - 1}
                    onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
                    aria-label={t("다음 쪽", "Next page", "次のページ")}
                    className="ui-button-ghost rounded-md px-2 py-0.5 disabled:opacity-40"
                  >
                    ›
                  </button>
                </div>
              ) : undefined
            }
          >
            {jobsError && (
              <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-800">
                {t("목록을 불러오지 못했습니다", "Failed to load the list", "一覧を読み込めませんでした")}: {jobsError}
              </div>
            )}
            {jobs.length === 0 ? (
              <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-[13px] text-slate-500">
                {t(
                  "아직 생성한 네트워크가 없습니다.",
                  "No network has been generated yet.",
                  "まだ生成したネットワークはありません。",
                )}
              </p>
            ) : (
              /* THE TABLE (docs/ux-backlog.md, the LIST REFERENCE): muted
                * column headers, monospace ids and coordinates, NO row borders
                * — the row height carries the separation — and the verdict as a
                * small right-aligned tag chip. U-1-quick #5's three facts are
                * unchanged; only their arrangement is. */
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="text-[12px] font-normal text-slate-500">
                      <th scope="col" className="py-2 pr-3 font-normal">
                        {t("이름", "Name", "名前")}
                      </th>
                      <th scope="col" className="py-2 pr-3 font-normal">
                        {t("생성일", "Created", "作成日")}
                      </th>
                      <th scope="col" className="py-2 pr-3 font-normal">
                        {t("영역", "Region", "領域")}
                      </th>
                      <th scope="col" className="py-2 pr-3 font-normal">
                        {t("규모", "Scale", "規模")}
                      </th>
                      <th scope="col" className="py-2 text-right font-normal">
                        {t("판정", "Verdict", "判定")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleJobs.map((job) => (
                      <tr
                        key={job.id}
                        onClick={() => setSelectedId(job.id)}
                        tabIndex={0}
                        aria-selected={job.id === selectedId}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setSelectedId(job.id);
                          }
                        }}
                        className={`cursor-pointer align-top text-[13px] ${
                          job.id === selectedId ? "bg-slate-100" : "hover:bg-slate-50"
                        }`}
                      >
                        <td className="max-w-[150px] py-2.5 pr-3">
                          <span className="block truncate font-medium text-slate-800">{job.name}</span>
                          {/* A derived filename that only re-spells the name above it is
                            * repetition, not information (user, live review 2026-08-13):
                            * it prints only when it actually differs — i.e. an upload
                            * whose file was not named after the job. Same rule in the
                            * drawer header and the verdict sha line. */}
                          {job.source_osm.filename !== `${job.name}.osm` && (
                            <span className="block truncate font-mono text-[12px] text-slate-500">
                              {job.source_osm.filename}
                            </span>
                          )}
                        </td>
                        <td className="whitespace-nowrap py-2.5 pr-3 font-mono text-[12px] text-slate-500">
                          {formatTimestamp(job.created_at, lang)}
                        </td>
                        <td className="max-w-[160px] py-2.5 pr-3 font-mono text-[12px] text-slate-500">
                          <span className="block truncate" title={requestedRegionLabel(job.requested_region, t)}>
                            {requestedRegionLabel(job.requested_region, t)}
                          </span>
                        </td>
                        <td className="max-w-[150px] py-2.5 pr-3 font-mono text-[12px] text-slate-500">
                          <span className="block truncate" title={networkScaleLabel(job.network, t)}>
                            {networkScaleLabel(job.network, t)}
                          </span>
                        </td>
                        <td className="py-2.5 text-right">
                          <span
                            className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-[12px] font-semibold ${statusClasses(job.status)}`}
                          >
                            {statusLabel(job.status, t)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </div>

        {/* --------------------------------------------------------------- */}
        {/* The detail drawer — everything about the ONE selected row        */}
        {/* --------------------------------------------------------------- */}
        <div className="min-w-0 flex-1 xl:sticky xl:top-6">
          {!selected ? (
            <div className="rounded-xl border border-slate-200 bg-white/[0.03] px-6 py-5">
              <p className="text-[13px] text-slate-500">
                {t(
                  "왼쪽 생성 이력에서 한 줄을 선택하면 그 생성의 판정·지도·아티팩트가 여기에 열립니다.",
                  "Pick a row from the generation history on the left and its verdict, map and artifacts open here.",
                  "左の生成履歴から一行を選ぶと、その生成の判定・地図・アーティファクトがここに開きます。",
                )}
              </p>
            </div>
          ) : (
            <div className="rounded-xl border border-slate-200 bg-white/[0.03] px-6 py-5">
              {/* DRAWER HEADER: name + verdict chip + ONE muted meta line +
                * close X. The master-detail reference's exact shape. */}
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-[15px] font-semibold text-slate-900">{selected.name}</h2>
                    <span
                      className={`shrink-0 whitespace-nowrap rounded-full border px-2 py-0.5 text-[12px] font-semibold ${statusClasses(selected.status)}`}
                    >
                      {statusLabel(selected.status, t)}
                    </span>
                  </div>
                  <p className="mt-1 truncate font-mono text-[12px] text-slate-500">
                    {selected.source_osm.filename !== `${selected.name}.osm`
                      ? `${selected.source_osm.filename} · `
                      : ""}
                    {formatTimestamp(selected.created_at, lang)} · {networkScaleLabel(selected.network, t)}
                  </p>
                  {/* LINEAGE VISIBILITY -- roadmap B5-2 stage 4. A fix job's
                    * name is identical to its parent's (create_genesis_fix_job
                    * never renames it), so without this line the history table
                    * and the drawer header alone cannot tell a fix job apart
                    * from the generation it was cloned from. key-ABSENT on an
                    * ordinary generation (`derived_from` is never `null`), so
                    * this renders for exactly the jobs that are fix jobs. */}
                  {selected.derived_from && (
                    <p className="mt-1 truncate font-mono text-[12px] text-slate-500">
                      {t("파생", "Derived from", "派生")} — {selected.derived_from.job_id} ·{" "}
                      {selected.derived_from.finding_code} · {selected.derived_from.approved_by ?? "—"}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedId("")}
                  aria-label={t("상세 닫기", "Close the detail", "詳細を閉じる")}
                  className="ui-button-ghost shrink-0 rounded-md px-2 py-0.5 text-[13px]"
                >
                  ✕
                </button>
              </div>

              <div className="mt-6 flex flex-col gap-6">
                {/* ----------------------------------------------------- */}
                {/* 판정 — the ONE merged verdict header, then the grid    */}
                {/* ----------------------------------------------------- */}
                <section className="border-t border-slate-200 pt-6">
                  <h3 className="text-[15px] font-semibold text-slate-900">
                    {t("판정", "Verdict", "判定")}
                    {/* Unit #20: this doctrine sentence (what V1-V9 means, and
                      * that a check which could not run is never a pass) moved
                      * out of this inline ⓘ into the page's help drawer, under
                      * 판정 게이트 -- it explains WHY the gate exists, not what a
                      * specific check's own state means (that stays inline on
                      * the check chips themselves, in `checkStateLabel`). */}
                  </h3>

                  {/* ONE HEADER, WHERE THERE WERE TWO BANNERS.
                    * docs/ux-backlog.md, SECOND ELABORATION item 10: the job
                    * status banner ("거부됨 — 등록하지 않음") and the gate
                    * verdict banner ("거부 — 결함이 입증되었습니다") said the
                    * same thing in two boxes, in two different words, one above
                    * the other. They are now one block: the status phrase, the
                    * three counts as Console stats, the sha in monospace. The
                    * status wording itself is unchanged and still comes from
                    * the ONE shared `statusLabel`. */}
                  <div className="mt-3">
                    <p className="text-[15px] font-semibold text-slate-900">
                      {report ? verdictLabel(report.verdict, t) : statusLabel(selected.status, t)}
                    </p>

                    {report && (
                      <div className="mt-3 flex flex-wrap gap-x-8 gap-y-3">
                        <Stat
                          label={t("통과", "pass", "合格")}
                          value={report.counts.pass}
                          tone="text-emerald-800"
                        />
                        <Stat
                          label={t("실패", "fail", "不合格")}
                          value={report.counts.fail}
                          tone={report.counts.fail > 0 ? "text-rose-800" : undefined}
                        />
                        <Stat
                          label={t("확인 불가", "not checkable", "確認不可")}
                          value={report.counts.not_checkable}
                          /* MONO BUT FLAGGED. This is the count that must never
                           * read as fine, and it sits between a green one and a
                           * red one -- rendered as a plain number it would be
                           * the quietest of the three. The dotted underline is
                           * the same mono flag `GenesisComparisonPanel` gives
                           * the same state, so the app has one vocabulary for
                           * it rather than a fourth colour. */
                          tone={
                            report.counts.not_checkable > 0
                              ? "text-slate-900 underline decoration-dotted decoration-slate-400 underline-offset-4"
                              : undefined
                          }
                        />
                      </div>
                    )}

                    {report && (
                      <p className="mt-3 break-all font-mono text-[12px] text-slate-500">
                        {report.network !== selected.name && report.network !== `${selected.name}.inpx`
                          ? `${report.network} · `
                          : ""}
                        sha256 {report.network_sha256.slice(0, 16)}
                      </p>
                    )}

                    {!isTerminal(selected.status) && (
                      <p className="mt-2 text-[13px] text-slate-600">
                        {t("진행", "Phase", "進行")}: {selected.phase} (
                        {Math.round(selected.progress * 100)}%)
                      </p>
                    )}

                    {selected.status === "provisional" && (
                      /* U-1-quick #3. The visible line is the STATUS -- registered, and
                       * one check still open -- and the paragraph explaining WHY a
                       * generation job can never close V1 by itself is the ⓘ, verbatim. */
                      <p className="mt-2 text-[13px] leading-relaxed text-slate-600">
                        {t(
                          "등록되었지만 잠정입니다 — V1은 라이브 검사 증거를 기다립니다.",
                          "Registered, but provisional — V1 is waiting for live-check evidence.",
                          "登録されましたが暫定です — V1 はライブ検査の証拠を待っています。",
                        )}
                        {/* Unit #20: the WHY (a generation job cannot open
                          * Vissim COM, so V1 stays open until live-check
                          * evidence attaches) moved out of this inline ⓘ into
                          * the page's help drawer, under 잠정 등록. The
                          * visible line above stays -- it is the STATUS, which
                          * answers "what just happened" and belongs where it
                          * already was. */}
                      </p>
                    )}
                    {selected.status === "refused" && (
                      <p className="mt-2 text-[13px] leading-relaxed text-slate-600">
                        {t(
                          "거부 등급 검사가 결함을 입증했으므로 이 네트워크는 프로젝트에 등록되지 않았습니다. 파일과 판정은 아래에서 그대로 받아볼 수 있습니다.",
                          "A refuse-tier check proved a fault, so this network was NOT registered in the project. The file and the verdict are both downloadable below.",
                          "拒否ティアの検査が欠陥を証明したため、このネットワークはプロジェクトに登録されていません。ファイルと判定は下からそのまま取得できます。",
                        )}
                      </p>
                    )}
                    {selected.revocation && (
                      <p className="mt-2 text-[13px] leading-relaxed text-slate-600">
                        {selected.revocation.removed_from_workspace
                          ? t(
                              "라이브 증거가 들어온 뒤 게이트가 이 네트워크를 거부하여, 프로젝트에서 등록을 해제했습니다.",
                              "After live evidence arrived the gate refused this network, so its registration was removed from the project.",
                              "ライブ証拠が届いた後にゲートがこのネットワークを拒否したため、プロジェクトから登録を解除しました。",
                            )
                          : t(
                              "게이트가 이 네트워크를 거부했습니다. 프로젝트의 현재 데이터셋은 이미 다른 파일이므로 아무것도 삭제하지 않았습니다.",
                              "The gate refused this network. The project's active dataset is no longer this artifact, so nothing was deleted.",
                              "ゲートがこのネットワークを拒否しました。プロジェクトの現在のデータセットは既に別のファイルのため、何も削除していません。",
                            )}
                      </p>
                    )}
                    {selected.refusal && (
                      <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[13px] text-rose-800">
                        <p className="font-semibold">
                          {refusalHeadline(selected.refusal.code, t, generationRefusalFallback(t))}
                        </p>
                        <ul className="mt-1 space-y-1">
                          {selected.refusal.reasons.map((reason, index) => (
                            <li key={index} className="font-mono text-[12px] leading-relaxed">
                              {reason}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>

                  {selected.gate?.live_evidence && (
                    <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3 text-[13px] text-slate-700">
                      {/* "Attestation", never "proof". The state.json arrives over
                        * HTTP, so this app cannot establish that Vissim ran — it can
                        * only record who says it did. */}
                      <p className="font-semibold text-slate-800">
                        {t(
                          "라이브 검사 증언 (운영자 진술)",
                          "Live-check attestation (operator's statement)",
                          "ライブ検査の証言（運用者の申告）",
                        )}
                      </p>
                      <p className="mt-1">
                        {selected.gate.live_evidence.label} · {selected.gate.live_evidence.network} ·{" "}
                        {selected.gate.live_evidence.vissim_version ??
                          t("버전 미기록", "version not recorded", "バージョン未記録")}
                      </p>
                      <p className="mt-1">
                        {selected.gate.live_evidence.admitted
                          ? t("채택됨 (동일 파일 확인)", "Admitted (same file proven)", "採用（同一ファイル確認）")
                          : t(
                              "채택되지 않음 — 이 증거는 다른 파일에 대한 것입니다",
                              "Not admitted — this evidence is about a different file",
                              "採用されず — この証拠は別のファイルに関するものです",
                            )}
                        {selected.gate.live_evidence.gap_codes.length > 0 &&
                          ` (${selected.gate.live_evidence.gap_codes.join(", ")})`}
                      </p>
                      {selected.gate.live_evidence.attested_by && (
                        <p className="mt-1">
                          {t("증언자", "Attested by", "申告者")}:{" "}
                          <span className="font-semibold">{selected.gate.live_evidence.attested_by}</span>
                          {selected.gate.live_evidence.attested_at &&
                            ` · ${formatTimestamp(selected.gate.live_evidence.attested_at, lang)}`}
                        </p>
                      )}
                      {selected.gate.rests_on_attestation && (
                        <p className="mt-2 rounded border-2 border-slate-400 bg-slate-100 px-2 py-1 text-slate-900">
                          {t(
                            "이 판정의 V1은 운영자 진술에 근거합니다 — 이 앱이 Vissim 로딩을 직접 관측한 것이 아닙니다.",
                            "V1 in this verdict rests on an operator's statement — this app did not observe the Vissim load itself.",
                            "この判定のV1は運用者の申告に基づきます — このアプリがVissimの読み込みを直接観測したわけではありません。",
                          )}
                        </p>
                      )}
                    </div>
                  )}

                  {reportError && (
                    <div className="mt-4 rounded-lg border-2 border-slate-400 bg-slate-100 px-3 py-2 text-[13px] text-slate-900">
                      {t(
                        "게이트 결과를 불러오지 못했습니다",
                        "Failed to load the gate result",
                        "ゲート結果を読み込めませんでした",
                      )}
                      : {reportError}
                    </div>
                  )}

                  {report && (
                    <div className="mt-4">
                      {/* Keyed on the VERDICT, not only the job id: a re-gate
                        * (which changes `checked_at` while the job id stays
                        * put) must clear whichever cell the reader had opened
                        * on the verdict it just replaced. */}
                      <GateCheckGrid
                        key={`${selected.id}-${gateCheckedAt ?? ""}`}
                        checks={report.checks}
                        t={t}
                        lang={lang}
                        jobId={selected.id}
                        fixes={fixes}
                        fixesError={fixesError}
                        onFixApplied={handleFixApplied}
                      />
                    </div>
                  )}
                </section>

                {/* ----------------------------------------------------- */}
                {/* 지도                                                   */}
                {/* ----------------------------------------------------- */}
                {report && (
                  <section className="border-t border-slate-200 pt-6">
                    <GenesisTileMap
                      jobId={selected.id}
                      report={report}
                      hasArtifact={Boolean(selected.artifact)}
                    />
                  </section>
                )}

                {/* ----------------------------------------------------- */}
                {/* 아티팩트                                               */}
                {/* ----------------------------------------------------- */}
                {selected.artifact && (
                  <DrawerSection title={t("아티팩트", "Artifacts", "アーティファクト")}>
                    <ArtifactRow jobId={selected.id} t={t} />
                  </DrawerSection>
                )}

                {/* ----------------------------------------------------- */}
                {/* 비교                                                   */}
                {/* ----------------------------------------------------- */}
                {/* ONE TITLE, not three. docs/ux-backlog.md item 9: the page
                  * wrapped `GenesisComparisonPanel` in a Panel titled 생성 비교,
                  * the panel drew its own inner card with the same title again,
                  * and the picker carried a third label of the same meaning.
                  * The drawer supplies the single title; the panel keeps its
                  * own scope sentence (it is pinned there, and the page must
                  * not restate it). */}
                <DrawerSection title={t("생성 비교", "Compare generations", "生成の比較")}>
                  <GenesisComparisonPanel
                    jobs={jobs}
                    currentJob={selected}
                    currentReport={report}
                    currentReportError={reportError}
                  />
                </DrawerSection>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
