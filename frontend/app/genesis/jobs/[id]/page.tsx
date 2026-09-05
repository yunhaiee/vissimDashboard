"use client";

// Unit #23 -- the genesis DRAWER becomes its own route. Roadmap v2 §5.B B1 +
// the mockup at docs/product/mockups/genesis-detail-layout-mockup-2026-08-14.html,
// option D (user decision, 2026-08-14): "지도가 크고, 판정과 지도가 동시에 보임" --
// the drawer's real workflow (read a finding, look at where it is on the map,
// approve a fix) needs the map wide, and a 46%-of-content-width column could
// never give it that. So every job now has its own URL, and this page IS the
// drawer -- transplanted, not redesigned: every doctrine paragraph, every
// gate/fix mechanic and every fetch-caching discipline app/genesis/page.tsx's
// drawer used to carry lives here verbatim, just under a new floor plan (top
// bar, then map LEFT ~65% / verdict rail RIGHT ~35%).
//
// -------------------------------------------------------------------------
// DEEP-LINK COLD LOAD, and the one behaviour a route gives the old drawer that
// a `selectedId` in page state never could
// -------------------------------------------------------------------------
// Every fetch below keys off `jobId`, read from the URL via `useParams` --
// never off anything the hub might have had in memory, because a reader can
// land here directly (a shared link, a bookmark, B4 demo notes) with no hub
// visit in this tab's history at all. An id the backend does not recognise
// (typo'd, deleted, or another project's job -- the backend answers all three
// the same way the artifact route already does, see `downloadFailureText` in
// the old drawer) renders ONE named empty state with a way back, never a
// crash and never a silently blank page.
//
// THE STALE-FLASH TRAP, and how this file avoids it. `router.push` to a
// SIBLING job's URL (the fix-apply flow below) re-renders this SAME page
// component with a new `jobId` param -- React does not remount it, so a
// `useState` holding "the job" would go on showing the OLD job's verdict
// under the NEW job's URL until the next poll tick overwrites it. The old
// drawer already had the fix for exactly this shape of bug (switching which
// row was selected without a remount): store the fetched record WITH the key
// it was fetched for, and read it back only when that key still matches.
// `selected` below keeps that exact discipline, now keyed on the URL's
// `jobId` instead of a `selectedId` state -- the moment the id changes, the
// stale record stops qualifying and the page falls back to its loading state
// on the very next render, not on the next network round trip.
//
// Every gate/fixes caching key, the polling cadence, the fix-disposition
// trap, the three-colour rule and the tri-state doctrine are UNCHANGED from
// the file this page's content was moved out of -- see that file's own
// header comment (app/genesis/page.tsx) for the parts of this doctrine that
// stayed on the hub.

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import GenesisComparisonPanel from "@/components/genesis/GenesisComparisonPanel";
import GenesisTileMap, { type GenesisMapSelectionRequest } from "@/components/genesis/GenesisTileMap";
import {
  SubjectLabel,
  findingKeyOf,
  type FindingRef,
  type SelectedItem,
} from "@/components/genesis/findings";
import { InfoTip, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  applyGenesisFix,
  confirmGenesisDeadEnd,
  fetchGenesisArtifact,
  fetchGenesisGateReport,
  fetchGenesisJob,
  fetchGenesisJobFixes,
  fetchGenesisJobs,
  getWorkspaceId,
  regenerateGenesisJob,
  startGenesisLiveCheck,
  type GenesisArtifactKind,
  type GenesisFixDisposition,
  type GenesisFixFinding,
  type GenesisGateCheck,
  type GenesisGateFinding,
  type GenesisGateReport,
  type GenesisJob,
  type GenesisJobFixes,
} from "@/lib/api";
import { messageWithoutSubject } from "@/lib/findingMessage";
import {
  BASIS_BOUNDARY_CUT,
  BASIS_RUNWAY_DEFICIT,
  PREFILL_KIND_MANUAL_REGION_EDIT,
  stashRegionPrefill,
  type RegionPrefillCoverage,
} from "@/lib/region-prefill";
import {
  POLL_INTERVAL_MS,
  TERMINAL_RECHECK_MS,
  formatTimestamp,
  isTerminal,
  lineageKindLabel,
  networkScaleLabel,
  statusClasses,
  statusLabel,
} from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

/** "No element on this job's map can be placed yet" -- one shared empty set, so
 * the render-time derivation of `placeable` (see the map wiring below) does not
 * mint a new identity on every render. */
const EMPTY_PLACEABLE: ReadonlySet<string> = new Set<string>();

/** The generation form's own unknown-code sentence -- kept here too (not only
 * on the hub) because `selected.refusal` (a job that failed before it ever
 * reached a gate report) renders through the same `refusalHeadline` fallback
 * on THIS page. Duplicated on purpose rather than imported across pages: it
 * is three lines of pure text with no state, and the alternative (importing
 * a helper from a sibling page.tsx) is not a pattern this app uses anywhere
 * else. */
function generationRefusalFallback(t: Translate): string {
  return t("생성이 거부되었습니다", "Generation was refused", "生成が拒否されました");
}

/** PASS / FAIL / NOT CHECKABLE -- three words, three treatments, never two.
 * `not_checkable` keeps its OWN NOUN, which is the part R1.5 actually requires:
 * a check that could not run may never be counted as a pass, so it may never
 * borrow the pass word. Its treatment is mono-with-a-doubled-border rather than
 * a third hue (see `checkStateTagClasses` below). */
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

/** THE STATE TAG CHIP -- the ONLY thing on this screen that carries a check's
 * colour (user decision from live review, 2026-08-13; see the original
 * comment history in app/genesis/page.tsx's own git log for the full
 * rationale, unchanged by this unit's move). */
function checkStateTagClasses(state: GenesisGateCheck["state"]): string {
  if (state === "pass") return "border-emerald-300 bg-emerald-50 text-emerald-800";
  if (state === "fail") return "border-rose-300 bg-rose-50 text-rose-800";
  return "border-2 border-slate-400 bg-slate-100 text-slate-600";
}

/** The card behind a check -- IDENTICAL for every state; selection is the
 * ONLY thing that changes it. */
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

// ---------------------------------------------------------------------------
// Unit #41 -- the five things the user hit using this screen for real
// (live pass 2026-08-17, job 2712071437d0: 1831 links, refused)
// ---------------------------------------------------------------------------
//
// 1. A FINDING WAS NOT CLICKABLE. The map has had click-to-select since U-19,
//    but only in ONE direction: map feature -> detail. Nothing in the verdict
//    rail was a control at all -- `CollapsibleMessages` printed `<li>{message}`
//    and the fix cards printed a mono code -- so "소견을 눌러 지도에서 본다"
//    was never wired, in either the gate-finding list or the fix list. It is
//    now, through `GenesisTileMap`'s selection REQUEST, and ONLY for findings
//    the census could actually place (`highlights.placeable`, published up out
//    of the map): a row that cannot highlight anything is not offered as one
//    that can, and an unplaceable finding keeps its named-list treatment.
// 2. THE ONE REAL PROPOSAL WAS NINTH. V3 raised ten findings; exactly one of
//    them (둔산로 w43011584s2, the source-testified connector) carried an
//    applicable proposal, and it sat below a six-item fold. Rows carrying a
//    proposal now sort FIRST and wear a sky 제안 badge, and the fold can never
//    close over one -- see `visibleCount` in `CollapsibleMessages`. Display
//    order only: nothing about the payload or the stored gate moves.
// 3/4. THE DOCTRINE WALL AND THE TRIPLE CARD. Every card repeated the full
//    rationale paragraph, a raw `CONNECTIVITY_IS_NOT_INVENTED`, and two
//    standing explanations, inside a card inside a card inside a card. NOTHING
//    IS DELETED -- the completeness of a refusal is this product's whole claim.
//    The long text and every machine identifier moved behind `<CardDetails>`
//    (a native <details>, so the refusal branch still contains no <button>),
//    and the nested boxes became rules: a top rule between fix rows, a left
//    rule on a treatment block.
// 6/7. NAMES BEFORE NUMBERS, SENTENCES SHORTER. `SubjectLabel` prints the
//    road first and the ids small behind it, everywhere a subject appears.

/** The sky badge on a finding that carries an applicable proposal -- sky is
 * this app's selection/suggestion accent (three-colour rule), and it is the
 * ONLY new colour this unit adds. A named helper rather than inline classes so
 * `GateCheckGrid`'s own markup keeps carrying no tint of its own, exactly as
 * the 2026-08-13 rule requires of the cells. */
function fixProposalBadgeClasses(): string {
  return "border-sky-300 bg-sky-50 text-slate-900";
}

/** Does this finding have a proposal a reader could actually approve? The same
 * `fixDispositionIsActionable` test the card itself gates its button on -- so a
 * badge can never promise a button that is not there. */
function fixFindingIsProposal(finding: GenesisFixFinding): boolean {
  return fixDispositionIsActionable(finding.disposition);
}

/**
 * THE DISCLOSURE THAT MOVED THE DOCTRINE WITHOUT DELETING IT.
 *
 * A native `<details>`, deliberately, for two reasons. It needs no state and
 * survives a re-render with its open/closed answer intact; and it is not a
 * `<button>`, which matters here more than it looks -- a refusal on this page
 * may carry no button at all (a refusal is information, not a disabled
 * control), and a disclosure that spelled itself as one would make the refusal
 * branch read as an actionable thing.
 */
function CardDetails({ summary, children }: { summary: string; children: React.ReactNode }) {
  return (
    <details className="mt-1.5 text-[14px] leading-relaxed text-slate-600">
      <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
        {summary}
      </summary>
      <div className="mt-1.5 flex flex-col gap-1.5 border-l border-slate-300 pl-3">{children}</div>
    </details>
  );
}

/** The one summary label every 자세히 toggle on this page wears. */
function moreLabel(t: Translate): string {
  return t("자세히 (근거·가정·코드)", "More (evidence, assumptions, codes)", "詳細（根拠・仮定・コード）");
}

/** THE CONSOLE STAT -- a small muted label above a large bold value. */
function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="min-w-[86px]">
      <p className="text-[14px] text-slate-500">{label}</p>
      <p className={`mt-0.5 text-[22px] font-semibold leading-none ${tone ?? "text-slate-900"}`}>
        {value}
      </p>
    </div>
  );
}

/** Everything a check records beyond its own state. */
function checkHasBody(check: GenesisGateCheck): boolean {
  return (
    check.gaps.length > 0 ||
    check.findings.length > 0 ||
    check.warnings.length > 0 ||
    check.findings_omitted > 0
  );
}

// ---------------------------------------------------------------------------
// B5-2 stage 4 -- the fix strip. Transplanted verbatim from app/genesis/
// page.tsx; see that file's own git history for the full doctrine comments
// this unit did not need to change.
// ---------------------------------------------------------------------------

function fixDispositionIsActionable(disposition: GenesisFixDisposition): boolean {
  return disposition.status === "AUTOMATABLE" && disposition.params !== null;
}

function fixConfidenceLabel(confidence: string, t: Translate): string {
  switch (confidence) {
    case "ARITHMETIC_FROM_OBSERVED_COUNTS":
      return t("산술 계산값 (관측 카운트 기반)", "Arithmetic, from observed counts", "算術計算値（観測カウント基準）");
    case "CONSTRAINT_COMPUTED_CEILING":
      return t("제약이 계산한 상한값", "A constraint's own computed ceiling", "制約が計算した上限値");
    case "DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE":
      return t("수요 추정값 (미검증 진입부)", "A demand ESTIMATE (unverified entrance)", "需要推定値（未検証の流入部）");
    // Unit #35 — two NAMED evidence levels, neither of them arithmetic. What
    // they qualify is the CONNECTION, never the geometry (see the assumption
    // token that always rides with them).
    case "SOURCE_TESTIFIED_CONNECTION":
      return t("소스가 증언한 연결", "The SOURCE testified this connection", "ソースが証言した接続");
    case "HUMAN_ATTESTED_CONNECTION":
      return t("운영자가 확인한 연결", "OPERATOR-ATTESTED connection", "運営者が確認した接続");
    case "NONE":
      return t("근거 값 없음", "No confidence value", "根拠値なし");
    default:
      return confidence;
  }
}

function fixIsDemandEstimateFraming(disposition: GenesisFixDisposition): boolean {
  return (
    disposition.confidence === "DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE" ||
    disposition.assumptions.includes("ENTRANCE_NOT_VERIFIED_MEASURED") ||
    (disposition.verb === "set_vehicle_input_volume" && disposition.reason_code === "EVIDENCE_INSUFFICIENT")
  );
}

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
    case "CONNECTOR_GEOMETRY_CONSTRUCTED":
      return t(
        "연결의 사실만 출처가 있고, 연결로의 형상·차로 대응은 기존 두 링크 끝에서 구성한 값입니다",
        "The CONNECTION has a named source; the connector's shape and lane mapping are constructed from the two existing link ends",
        "接続という事実にのみ出典があり、コネクタの形状・車線対応は既存の2つのリンク端から構成した値です",
      );
    case "ATTESTED_NOT_MEASURED":
      return t(
        "이 연결은 측정된 사실이 아니라 사람이 책임지는 진술입니다 (확인자·시각 기록됨)",
        "This connection is a person's accountable claim, not a measurement (attester and timestamp are recorded)",
        "この接続は測定された事実ではなく、人が責任を負う主張です（確認者・時刻を記録）",
      );
    default:
      return token;
  }
}

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

/** A refused genesis mutation → the headline + the reasons, verbatim.
 *
 * The FALLBACK is the caller's, not this function's (unit #33): a code the
 * shared table does not know still has to produce a sentence about the thing
 * the reader actually pressed. When this helper hard-coded the fix flow's
 * "수정을 적용하지 못했습니다", the second caller would have reported an
 * unrecognised live-check refusal as a failed fix. The reasons array is always
 * rendered underneath, unrewritten. */
function genesisRefusalText(
  caught: unknown,
  t: Translate,
  fallback: string,
): { headline: string; reasons: string[] } {
  if (caught instanceof GenesisError) {
    return { headline: refusalHeadline(caught.code, t, fallback), reasons: caught.reasons };
  }
  return { headline: caught instanceof Error ? caught.message : String(caught), reasons: [] };
}

/** Unit #42 -- the radio value that means "no, the road ENDS here", sharing the
 * one group with unit #35's candidate link ids. A draft link id is always
 * numeric, so this sentinel can never collide with one; and living in the same
 * group is what makes the two answers exclusive without a rule to enforce. */
const DEAD_END_ANSWER = "__real_world_dead_end__";

// ---------------------------------------------------------------------------
// UNIT #48-b (user decision, 2026-08-20) -- THE AUTOMATIC BOUNDARY-EXTENSION
// PROPOSALS ARE GONE FROM THIS SCREEN.
//
// What stood here: `boundaryPatchesPrefill` + `BoundaryPatchesProposal`, the one
// component that rendered unit #48's multi-corridor widened region in its two
// homes (an orphan's own card, and the job-level "cover every boundary finding
// at once" card), each with a button that carried the derived ring to the region
// page.
//
// WHY IT IS GONE, measured rather than argued: the geometry is whack-a-mole.
// Every widened boundary cuts NEW roads somewhere else, so the composite
// button was pressed repeatedly on a real network and the boundary findings
// moved instead of closing (jobs 7f263915d770 and e2f654400d21 in
// runtime/workspaces/bfc69251bd04/genesis/jobs/). The user chose to edit the
// region by hand instead, and the ONE button this screen now offers is
// `RegionEditInvitation` immediately below -- it carries THIS job's own
// requested polygon, unchanged, and nothing this app derived.
//
// The BACKEND derivation is retained and un-exposed (see the note on
// `GenesisFixBoundaryExtension` in lib/api.ts): the measurement is sound, and
// its obvious next home is a pre-flight boundary check on the region screen,
// before an extract is ever run. What died is the proposal, not the ruler.
// ---------------------------------------------------------------------------

/** UNIT #48-b -- the boundary-attributed findings of this verdict, as HINTS.
 *
 * Every value is the BACKEND's own measurement, read off the two attribution
 * payloads it already sends (unit #39's `boundary_attribution` for a V4 runway
 * shortage, unit #48's `orphan_boundary_attribution` for a `v3_orphan_link`),
 * and every entry is gated on that payload's OWN `status === "boundary_
 * attributed"`. Nothing here decides whether the boundary was the cause -- this
 * page is structurally unable to, and a hint that guessed would send a reader to
 * drag the wrong edge.
 *
 * A finding whose attribution is missing the number the hint would print is
 * SKIPPED rather than printed with a zero: "0 m" is a measurement, and this one
 * was never taken. */
function boundaryHintsOf(fixes: GenesisJobFixes | null): RegionPrefillCoverage[] {
  const hints: RegionPrefillCoverage[] = [];
  for (const finding of fixes?.findings ?? []) {
    // The orphan is asked FIRST: it is the payload that names severed ENDS, and
    // a finding never carries both.
    const orphan = finding.disposition.orphan_boundary_attribution;
    if (orphan && orphan.status === "boundary_attributed") {
      hints.push({
        finding_code: finding.code,
        subject: finding.subject,
        basis_kind: BASIS_BOUNDARY_CUT,
        basis_m: orphan.metres_cut_total,
        way_id: orphan.way_id,
        boundary_points: orphan.severed_ends.map((end) => ({
          end: end.end,
          boundary_node: end.boundary_node,
          metres_cut: end.metres_cut,
        })),
      });
      continue;
    }
    const attributed = finding.disposition.boundary_attribution;
    if (
      attributed &&
      attributed.status === "boundary_attributed" &&
      typeof attributed.deficit_m === "number"
    ) {
      hints.push({
        finding_code: finding.code,
        subject: finding.subject,
        // A SHORTAGE, not a cut. The two numbers mean different things and the
        // region page's banner branches on this very field to word them apart.
        basis_kind: BASIS_RUNWAY_DEFICIT,
        basis_m: attributed.deficit_m,
        way_id: attributed.way_id ?? null,
        boundary_points:
          typeof attributed.boundary_node === "number"
            ? [{ end: null, boundary_node: attributed.boundary_node, metres_cut: null }]
            : [],
      });
    }
  }
  return hints;
}

/** UNIT #48-b -- the ONE action on this screen that leaves it, and the whole of
 * what replaced the automatic proposals.
 *
 * It proposes NOTHING. `polygon` is the ring this generation was extracted with,
 * read off the census's own `metadata.boundary.requested_polygon_wgs84` and
 * handed over unchanged; the region page draws it, every corner is draggable,
 * and THAT page's extract button is the ask-first gate this one never crosses.
 * The hints ride along in the payload so the reader arrives knowing which edges
 * the boundary cut -- information, not a shape.
 *
 * IT IS A BUTTON AND NOTHING ELSE (user decision, 2026-08-20: "just delete
 * everything and leave 영역편집 버튼 only"). The panel that stood here carried a
 * heading ("이 생성이 요청한 영역"), an explanation sentence with a
 * boundary-finding count branch, a corner count ("점 N개") and a caption under
 * the button ("누르면 영역 화면이 이 영역으로 열립니다 …"). All four were
 * struck: they described what the reader is about to see on the very screen the
 * button opens, and that screen already says it -- the region page's own
 * pre-filled banner names the parent job, the corner count and the boundary
 * hints. Saying it twice is not honesty, it is noise, and the card chrome around
 * one button went with the prose it framed.
 *
 * NOTHING BEHIND THE BUTTON CHANGED: the render gate (no recorded ring, no
 * button), the job tagging, the stashed payload and its one-shot read are all
 * as they were -- only the words are gone. `hints` is still carried, in `covers`.
 *
 * A store that refuses the write is SAID, not navigated past: landing on an
 * empty region page with no explanation is how a user concludes the feature is
 * broken. That message is a REFUSAL, not panel prose -- it exists only when the
 * hand-off failed, and deleting it would send the reader nowhere, silently. */
function RegionEditInvitation({
  jobId,
  polygon,
  hints,
  t,
}: {
  jobId: string;
  polygon: Array<[number, number]>;
  hints: RegionPrefillCoverage[];
  t: Translate;
}) {
  const router = useRouter();
  const [handOffError, setHandOffError] = useState<string | null>(null);

  const openRegionEditor = () => {
    const stashed = stashRegionPrefill({
      polygon,
      // The 프로젝트 this region was taken from travels WITH it: the store
      // outlives a project switch (which reloads the tab), and a payload read
      // back under another project would pre-fill B's extract with A's ring.
      workspace_id: getWorkspaceId(),
      parent_job: jobId,
      kind: PREFILL_KIND_MANUAL_REGION_EDIT,
      covers: hints,
    });
    if (!stashed) {
      setHandOffError(
        t(
          "이 브라우저에서 영역을 넘길 수 없습니다 (세션 저장소 사용 불가). 영역 화면에서 직접 그려 주세요.",
          "This browser cannot carry the region across (session storage is unavailable). Draw it on the region screen instead.",
          "このブラウザでは領域を引き渡せません（セッションストレージが利用不可）。領域画面で直接描いてください。",
        ),
      );
      return;
    }
    setHandOffError(null);
    router.push("/genesis/region");
  };

  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={openRegionEditor}
        className="ui-button-primary w-fit rounded-md px-3 py-1.5 text-[15px]"
      >
        {t("영역 편집 →", "Edit region →", "領域を編集 →")}
      </button>
      {handOffError && (
        <p className="mt-2 text-[14px] leading-relaxed text-rose-700">{handOffError}</p>
      )}
    </div>
  );
}

/**
 * ONE finding's disposition -- EITHER an informational mono line (never a
 * button: a refusal is information, not a disabled control) OR an approval
 * card. Never a third shape.
 */
function FixFindingRow({
  jobId,
  finding,
  instanceKey,
  showSubject,
  t,
  lang,
  onApplied,
  onFocusElement,
  onFocusPoint,
}: {
  jobId: string;
  finding: GenesisFixFinding;
  /** Unit #41 -- this ROW's own identity, distinct from the FINDING's.
   * `(code, subject)` is the finding's identity for applying a fix (unit #30)
   * but it is NOT unique in a list: gate.py legitimately emits the same pair
   * twice (V3 raised `v3_silent_departure_point / 대덕대로 [FWD] w52496957s1`
   * for links 427 AND 429 on the live job). Two rows sharing a React key made
   * React treat them as one component -- and this unit's radio group, named
   * off the same pair, merged the two cards' candidate lists so picking a
   * target in one cleared the other. Both are keyed off this instead. */
  instanceKey: string;
  /** Unit #41-b -- does this card have to NAME its own finding, or is it
   * already sitting under the gate row that named it? The proposal is rendered
   * inline under its own 소견 now (the user's live pass, 2026-08-18: "이름 반복
   * … 이게 너무 자주돼"), so the merged case prints the subject exactly once,
   * on the row above. `true` is the standing shape and is still used by the
   * dispositions whose gate row the payload cap left out. */
  showSubject: boolean;
  t: Translate;
  lang: string;
  onApplied: (job: GenesisJob) => void;
  /** Unit #41 -- show a link on the map beside this card. Used by the candidate
   * list (hover or pick a link end and see WHERE it is) and by the derived
   * to-link's own confirmation, which asks the reader to check the map and
   * until now gave them no way to get there. */
  onFocusElement: (kind: "link" | "connector", id: number) => void;
  /** Unit #41 -- ring a MEASURED coordinate on the map. Only the source's own
   * testimony carries one; nothing else calls this. */
  onFocusPoint: (point: { lat: number; lon: number; label: string }) => void;
}) {
  const disposition = finding.disposition;
  const actionable = fixDispositionIsActionable(disposition);
  const rationale =
    lang === "ko" ? disposition.rationale.ko : lang === "ja" ? disposition.rationale.ja : disposition.rationale.en;

  const [approvalRationale, setApprovalRationale] = useState("");
  const [requiredValues, setRequiredValues] = useState<Record<string, string>>({});
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<GenesisError | Error | null>(null);
  // Unit #35 -- the two ask-first gates a connectivity treatment adds. Neither
  // is a value this page computes: `targetConfirmed` is the reader saying out
  // loud that the DERIVED to-link is the road they mean, and the attestation
  // pair is the reader's own claim. Both start empty on purpose.
  const [targetConfirmed, setTargetConfirmed] = useState(false);
  const [attestTarget, setAttestTarget] = useState("");
  const [attestBasis, setAttestBasis] = useState("");
  // Unit #38 -- the regenerate treatment's own two bits of state. Separate from
  // the apply pair above because it is a different action against a different
  // route: nothing about this artifact is edited.
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<GenesisError | Error | null>(null);
  // Unit #42 -- the dead-end confirmation's own in-flight/error pair. It shares
  // `attestTarget` (the answer) and `attestBasis` (the reason) with the
  // attestation deliberately: "the road continues to X" and "the road ends
  // here" are two answers to ONE question, so they live in one radio group and
  // cannot both be held. What they do NOT share is the route, and this pair is
  // why -- an attestation edits this artifact, a confirmation re-derives a new
  // one, and a failure of either must not report as the other.
  const [confirmingDeadEnd, setConfirmingDeadEnd] = useState(false);
  const [deadEndError, setDeadEndError] = useState<GenesisError | Error | null>(null);

  const missingRequired = disposition.requires.some((field) => !requiredValues[field]?.trim());
  // A connector's to-link is DERIVED, and a reader confirming it is the point
  // of the ask: no confirmation, no approve button, even though the payload
  // has been ready since the page loaded.
  const needsTargetConfirmation = disposition.verb === "add_connector";
  const attestation = disposition.attestation;
  // Unit #42 -- the OTHER answer to the same question. `attestChoice` resolves
  // to null whenever the sentinel is picked (no candidate is ever named that),
  // and `deadEndChosen` is false whenever a candidate is, so the two answers
  // are exclusive by CONSTRUCTION rather than by a rule some later edit could
  // forget to keep. Holding both would be a contradiction: a road cannot both
  // continue to link X and end here.
  const deadEnd = disposition.dead_end_confirmation;
  const attestChoice = attestation?.candidates.find((c) => c.link_id === attestTarget) ?? null;
  // Read off the SAME state the candidate picker writes (that is the
  // exclusivity), and gated on the offer still being there: a live check
  // replaces the payload in place (unit #41), so an answer picked against a
  // disposition that no longer offers it must fall back to no answer rather
  // than rendering a button whose action cannot run.
  const deadEndChosen = Boolean(deadEnd) && attestTarget === DEAD_END_ANSWER;
  // Unit #41-c -- IS A CONNECTOR ACTUALLY ON OFFER HERE? Named apart from
  // `worldGapAsked` because the two answer different questions and one card
  // shape can hold the second without the first: a road the source shares the
  // gap on, with no reachable link end for a connector to land on, offers ONLY
  // the dead-end answer. Doctrine about how a connector would be built has no
  // subject on that card -- see its use below.
  const connectorOffered = Boolean(attestation && attestation.candidates.length > 0);
  const worldGapAsked = connectorOffered || Boolean(deadEnd);

  const submit = useCallback(
    async (attested?: { params: Record<string, unknown>; to_link: string; basis: string }) => {
      if (!disposition.verb) return;
      if (!attested && disposition.params === null) return;
      setApplying(true);
      setApplyError(null);
      try {
        const params: Record<string, unknown> = attested
          ? { ...attested.params }
          : { ...(disposition.params as Record<string, unknown>) };
        if (!attested) {
          for (const field of disposition.requires) {
            const spec = fixRequiredFieldSpec(field, t);
            const raw = requiredValues[field] ?? "";
            params[field] = spec.numeric ? Number(raw) : raw;
          }
        }
        const job = await applyGenesisFix(jobId, {
          finding_code: finding.code,
          // The finding's own identity is (code, subject) -- unit #30. Sending
          // the subject is what makes the SECOND of two same-coded findings its
          // own apply target instead of a collision with the first.
          subject: finding.subject,
          verb: disposition.verb,
          params,
          approval_rationale: approvalRationale.trim() || undefined,
          // Unit #35: the attester's NAME and the timestamp are deliberately
          // not sent -- the route stamps both, so this client cannot attest on
          // somebody else's behalf.
          attestation: attested ? { to_link: attested.to_link, basis: attested.basis } : undefined,
        });
        onApplied(job);
      } catch (caught) {
        setApplyError(caught instanceof Error ? caught : new Error(String(caught)));
      } finally {
        setApplying(false);
      }
    },
    [disposition, requiredValues, approvalRationale, jobId, finding.code, finding.subject, onApplied, t],
  );

  // Unit #42 -- the OTHER treatment a world gap can have. Not an apply: no verb,
  // no params, nothing about this artifact edited. The way to exclude is NOT
  // sent -- the route re-derives it from the finding's own census record, so
  // this client cannot widen an exclusion past the road the reader looked at --
  // and the collateral list IS sent, because the server refuses a set that
  // differs from its own re-derivation and that refusal is the proof the reader
  // saw what they were removing.
  const confirmDeadEnd = useCallback(async () => {
    if (!deadEnd) return;
    setConfirmingDeadEnd(true);
    setDeadEndError(null);
    try {
      const job = await confirmGenesisDeadEnd(jobId, {
        finding_code: finding.code,
        subject: finding.subject,
        basis: attestBasis.trim(),
        acknowledged_collateral_way_ids: deadEnd.collateral_way_ids,
      });
      onApplied(job);
    } catch (caught) {
      setDeadEndError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setConfirmingDeadEnd(false);
    }
  }, [deadEnd, jobId, finding.code, finding.subject, attestBasis, onApplied]);

  const regenerate = useCallback(async () => {
    setRegenerating(true);
    setRegenerateError(null);
    try {
      // The requester's own sentence quotes the FINDING, so the audit trail
      // says which refusal this re-derivation was asked for. Nothing else is
      // sent: the source, the options and the pipeline are the parent's own.
      const job = await regenerateGenesisJob(jobId, `${finding.code} / ${finding.subject}`);
      onApplied(job);
    } catch (caught) {
      setRegenerateError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setRegenerating(false);
    }
  }, [jobId, finding.code, finding.subject, onApplied]);

  const error = applyError
    ? genesisRefusalText(
        applyError,
        t,
        t("수정을 적용하지 못했습니다", "The fix could not be applied", "修正を適用できませんでした"),
      )
    : null;

  // Unit #41 -- the id the fix payload itself carries for this finding
  // (`context.link_id`, written by gate.py beside every V3/V7 subject). Read
  // defensively: `context` is `Record<string, unknown>`, and a code that
  // carries no link id must print none rather than a coerced NaN.
  const contextLinkId =
    typeof finding.context?.link_id === "number" ? (finding.context.link_id as number) : null;

  return (
    <div className={showSubject ? "px-0.5 py-3 text-[15px] text-slate-800" : "px-1.5 pb-3 text-[15px] text-slate-800"}>
      {/* Unit #41 -- WHAT IT IS, first and in words. The finding code used to
        * lead this line; it is a machine identifier a bug report needs and a
        * consultant cannot read, so it moved into the 자세히 block below and
        * the road took its place.
        *
        * Unit #41-b -- and it is printed ONCE PER CARD. Merged under its own
        * gate row (the standing case now), the badge and the road are already
        * on the line above, so this header would be the second copy of both. */}
      {showSubject && (
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          {fixFindingIsProposal(finding) && (
            <span
              className={`rounded-full border px-1.5 py-0.5 text-[14px] font-bold ${fixProposalBadgeClasses()}`}
            >
              {t("제안", "Proposal", "提案")}
            </span>
          )}
          {finding.origin === "warning" && <span className="text-[14px] text-slate-500">warn /</span>}
          <SubjectLabel subject={finding.subject} id={contextLinkId} className="text-[15px]" />
        </div>
      )}

      {/* Unit #36's measurement, quoted rather than paraphrased -- the job's
        * own source .osm declaring the connection this network lost. It rides
        * above the branch because BOTH answers can carry it: a testified
        * finding whose target link the model never emitted is still a
        * refusal, and the testimony is exactly what makes that refusal
        * legible.
        *
        * Unit #41 gave it the map context it always lacked: the testimony
        * carries the shared node's own WGS84 coordinate, so the sentence that
        * names the node can now put the reader on it. Key-absent coordinates
        * render the sentence and no control -- never a guessed pin. */}
      {disposition.source_testimony && (
        <div className="mt-2">
          <p className="font-mono text-[14px] leading-relaxed text-slate-600">
            {t(
              `소스가 증언합니다 — way ${disposition.source_testimony.connected_way_ids.join(", ")}가 node ${disposition.source_testimony.node_id}를 공유`,
              `The source testifies — way(s) ${disposition.source_testimony.connected_way_ids.join(", ")} share node ${disposition.source_testimony.node_id}`,
              `ソースが証言します — way ${disposition.source_testimony.connected_way_ids.join(", ")} がノード ${disposition.source_testimony.node_id} を共有`,
            )}
          </p>
          {disposition.source_testimony.coordinates && (
            <button
              type="button"
              onClick={() =>
                onFocusPoint({
                  lat: disposition.source_testimony!.coordinates!.lat,
                  lon: disposition.source_testimony!.coordinates!.lon,
                  label: t("출발 지점", "Departure point", "出発地点"),
                })
              }
              className="ui-button-ghost mt-1.5 rounded-full px-2.5 py-0.5 text-[14px] font-medium"
            >
              {t("지도에서 이 지점 보기", "Show this point on the map", "地図でこの地点を見る")}
            </button>
          )}
        </div>
      )}

      {!actionable ? (
        <div className="mt-2">
          {/* NOT_AUTOMATABLE (or a structural refusal wearing an AUTOMATABLE
            * status -- see fixDispositionIsActionable) -- ONE mono line, the
            * rationale verbatim, and NEVER a button.
            *
            * Unit #41: the rationale is still here VERBATIM and still first --
            * it just reads inside a disclosure now, because nine copies of a
            * five-sentence paragraph is a wall a reader scrolls past rather
            * than a reason they read. The one-line answer above it is this
            * UI's own summary of a state it already computes, never a
            * re-wording of the backend's claim. */}
          {/* Unit #41-d -- THE TREATMENT THIS CARD ACTUALLY OFFERS, not the one
            * its neighbour offers. This line rode on `worldGapAsked`, which is
            * true for EITHER answer, and said "적용할 수 있습니다" -- true only
            * where a connector is on offer, because attesting one runs a real
            * `add_connector` edit against this artifact. A card whose only
            * answer is the dead end treats the finding the opposite way: the
            * confirmation mints a SIBLING generation with that road excluded
            * by name (`POST .../dead-end-confirmation` returns a new job) and
            * this artifact is never edited. Telling that reader the fix "가
            * 적용된다" describes a button they do not have.
            *
            * A MIXED card keeps "적용": both answers are open on it, and the
            * one the reader is most likely to take -- the connector -- really
            * does apply. The dead-end answer's own consequences are spelled by
            * the consent line further down, which appears once that answer is
            * actually picked; this line stays the card's opening summary and
            * does not restate it. */}
          {/* REFUTER, unit #48-b -- THE BOUNDARY BRANCH NAMES A CONTROL THAT
            * EXISTS. It read "자동 수정 없음 — 경계를 넓혀 다시 생성해야
            * 합니다." until 2026-08-20, when it was the one-line summary of the
            * "경계를 넓혀 다시 생성 →" button sitting directly beneath it on this
            * card. That button went with the rest of the automatic proposal UI
            * and the imperative was left behind: a reader was told to perform
            * the one flow this unit deleted, by a line naming nothing they could
            * press. Unit #41-d above already ruled on this shape of copy -- an
            * answer line describes the treatment THIS reader can reach.
            *
            * WHY IT MAY NAME 영역 편집 rather than trailing off into "사람이
            * 판단해야 합니다": the gate below is still `boundary_extension`, and
            * that key is key-ABSENT unless the census recorded a requested
            * POLYGON (`genesis_jobs.derive_v4_boundary_extension` refuses with
            * `requested_region_not_a_polygon` otherwise) -- the SAME field
            * `requestedPolygonOf` needs before `RegionEditInvitation` renders at
            * all. So wherever this sentence appears, the button it names is on
            * the rail with it. Keying it off unit #39's attribution instead
            * would read better and be a lie: an orphan or a bbox job can be
            * boundary-attributed with no requested polygon and therefore no
            * button. Only the key's PRESENCE is read here -- none of the derived
            * metres, no added area, no finger. */}
          <p className="text-[15px] leading-relaxed text-slate-700">
            {connectorOffered
              ? t(
                  "자동 수정 없음 — 현장을 아는 사람이 확인하면 적용할 수 있습니다.",
                  "No automated fix — someone who knows the site can attest, and then it applies.",
                  "自動修正なし — 現場をご存じの方が確認すれば適用できます。",
                )
              : deadEnd
                ? t(
                    "자동 수정 없음 — 현장을 아는 사람이 확인하면 그 도로를 제외하고 다시 생성합니다.",
                    "No automated fix — someone who knows the site can confirm it ends, and then it is generated again with that road left out.",
                    "自動修正なし — 現場をご存じの方が確認すれば、その道路を除いて再生成します。",
                  )
                : disposition.regenerate
                  ? t(
                      "자동 수정 없음 — 재생성으로 치료합니다.",
                      "No automated fix — this one is treated by regenerating.",
                      "自動修正なし — 再生成で治療します。",
                    )
                  : disposition.boundary_extension
                    ? t(
                        "자동 수정 없음 — 이 부족은 요청하신 영역의 경계가 원인입니다. '영역 편집'에서 경계를 옮기신 뒤 다시 추출해 주세요.",
                        "No automated fix — the requested region's boundary is the cause. Move it yourself with 'Edit region', then extract again.",
                        "自動修正なし — この不足は要求した領域の境界が原因です。「領域を編集」で境界を動かしたうえで、もう一度抽出してください。",
                      )
                    : t(
                        "자동 수정 없음 — 사람이 판단해야 합니다.",
                        "No automated fix — this needs a person's judgement.",
                        "自動修正なし — 人の判断が必要です。",
                      )}
          </p>
          {/* Unit #41-b -- ONE 자세히 PER CARD. The attestation block below used
            * to open a SECOND disclosure with the same summary line, so a
            * reader met "자세히 (근거·가정·코드)" twice in one card and had to
            * open both to have read the card (user, live pass 2026-08-18: "the
            * second 자세히는 없애"). The two standing attestation explanations
            * and unit #42's own dead-end doctrine moved UP into this one,
            * verbatim and still under exactly the conditions that used to
            * render them -- MOVED, never dropped, which is the same rule the
            * disclosure itself was created under. */}
          <CardDetails summary={moreLabel(t)}>
            <p className="font-mono leading-relaxed text-slate-700">{rationale}</p>
            {/* TRUE OF EITHER ANSWER: the source really does declare no
              * continuation here (that measured state is what opens this
              * question at all), only someone who knows the site can settle
              * it, and both answers are recorded with who gave them and when.
              * Stays on the dead-end-only card. */}
            {worldGapAsked && (
              <p className="leading-relaxed">
                {t(
                  "원본 데이터는 이 지점에서 이어지는 도로를 선언하지 않습니다. 현장을 아시는 분만 답할 수 있는 질문이며, 확인하시면 확인자와 시각이 이 수정과 함께 기록됩니다.",
                  "The source data declares no continuation here. Only someone who knows the site can answer, and an attestation is recorded with who made it and when.",
                  "元データはこの地点での継続を宣言していません。現場をご存じの方だけが答えられる問いであり、確認された場合は確認者と時刻がこの修正とともに記録されます。",
                )}
              </p>
            )}
            {/* Unit #41-c -- ONLY WHERE A CONNECTOR IS ON OFFER. This sentence
              * describes how the system would BUILD one and closes with "what
              * you attest is only that the road continues" -- on a card whose
              * single answer is that the road does NOT continue, that is a
              * contradiction the reader has to talk themselves out of, and it
              * rode in on `worldGapAsked`, which is true for the dead-end
              * offer alone. `connectorOffered` is the condition it was always
              * about. A card carrying both answers is unchanged. */}
            {connectorOffered && (
              <p className="leading-relaxed">
                {t(
                  "연결로의 형상과 차로 대응은 기존 두 링크 끝에서 시스템이 구성합니다 — 확인하시는 것은 도로가 이어진다는 사실뿐입니다.",
                  "The connector's shape and lane mapping are constructed by the system from the two existing link ends — what you attest is only that the road continues.",
                  "コネクタの形状と車線対応は既存の2つのリンク端からシステムが構成します — 確認いただくのは道路が続くという事実のみです。",
                )}
              </p>
            )}
            {deadEnd && (
              <p className="leading-relaxed">
                {t(
                  "여기서 끝난다고 확인하셔도 원본 .osm은 편집되지 않습니다 — 원본은 증거이며, 제외는 새 작업에 전달되는 선언적 파라미터입니다. 판정기(gate)도 그대로입니다: 그 way가 네트워크에 없으므로 V3가 더 이상 발화하지 않을 뿐, 소견이 숨겨지는 것이 아닙니다.",
                  "Confirming that it ends does NOT edit the source .osm — the source is evidence, and the exclusion is a declarative parameter carried into the new job. The judge is untouched too: V3 stops firing because the way is no longer in the network, never because a finding was suppressed.",
                  "ここで終わると確認しても元の.osmは編集されません — 元データは証拠であり、除外は新しいジョブに渡される宣言的パラメータです。判定器（gate）もそのままです: そのwayがネットワークに存在しないためV3が発火しなくなるだけで、検出が隠されるわけではありません。",
                )}
              </p>
            )}
            <p className="font-mono text-[14px] text-slate-500">
              {finding.code}
              {disposition.reason_code ? ` · ${disposition.reason_code}` : ""}
            </p>
          </CardDetails>

          {/* Unit #38 -- the THIRD disposition, in the same card vocabulary:
            * the refusal above stays verbatim (the census really cannot
            * correct this), and what follows is the treatment that CAN --
            * re-deriving the whole generation from the same source through
            * the corrected converter. Not a fix button: nothing about this
            * artifact is edited, and the new job faces the gate on its own. */}
          {disposition.regenerate && (
            <div className="mt-3 flex flex-col gap-2 border-l-2 border-slate-300 pl-3">
              <p className="text-[15px] font-semibold text-slate-800">
                {t(
                  "이 결함은 편집이 아니라 재생성으로 치료합니다",
                  "This finding is treated by regenerating, not by editing",
                  "この欠陥は編集ではなく再生成で治療します",
                )}
              </p>
              <p className="text-[14px] leading-relaxed text-slate-600">
                {lang === "ko"
                  ? disposition.regenerate.explanation.ko
                  : lang === "ja"
                    ? disposition.regenerate.explanation.ja
                    : disposition.regenerate.explanation.en}
              </p>
              {(disposition.regenerate.converter_fixes ?? []).map((fix) => (
                <p key={fix.id} className="font-mono text-[14px] leading-relaxed text-slate-500">
                  {`${fix.id} (${fix.unit}, ${fix.since}) — ${fix.summary}`}
                </p>
              ))}
              <div>
                <button
                  type="button"
                  disabled={regenerating}
                  onClick={regenerate}
                  className="ui-button-primary w-fit rounded-md px-3 py-1.5 text-[15px] disabled:cursor-not-allowed"
                >
                  {regenerating
                    ? t("재생성 중…", "Regenerating…", "再生成中…")
                    : t("재생성으로 치료", "Treat by regenerating", "再生成で治療")}
                </button>
              </div>
              {regenerateError && (
                <div className="text-[14px] leading-relaxed text-rose-700">
                  <p className="font-semibold">
                    {
                      genesisRefusalText(
                        regenerateError,
                        t,
                        t("재생성을 시작하지 못했습니다", "The regeneration could not be started", "再生成を開始できませんでした"),
                      ).headline
                    }
                  </p>
                  {genesisRefusalText(regenerateError, t, "").reasons.map((line) => (
                    <p key={line}>{line}</p>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* UNIT #48-b -- the per-finding "경계를 넓혀 다시 생성" buttons that
            * unit #40 and unit #48 rendered here (a V4 shortage's widened ring,
            * and an orphan's one-finger-per-severed-end ring) were removed by
            * user decision on 2026-08-20; see the note at the top of this file.
            * The MEASUREMENT stays exactly where it was -- the boundary
            * attribution's own sentences are in the rationale above, with the
            * way ids and the cut distances the backend measured -- and the ONE
            * remaining way to act on it is the 영역 편집 button beside the
            * verdict, which hands over this job's own region and derives
            * nothing. */}

          {/* Unit #42 -- a NAMED refusal, and deliberately not a control: the
            * dead-end disposition is unavailable here and the reader is told
            * why rather than left to wonder where the option went. The one
            * that matters is source_testifies_the_connection — the source
            * declares this road continues, so no confirmation may delete it. */}
          {disposition.dead_end_refusal && (
            <div className="mt-3 border-l-2 border-slate-300 pl-3">
              <p className="text-[14px] leading-relaxed text-slate-600">
                {t(
                  "실세계 막다른 길로 확인하는 처분은 이 소견에 쓸 수 없습니다.",
                  "The real-world dead-end disposition is not available on this finding.",
                  "実世界の行き止まりとして確認する処分はこの検出には使えません。",
                )}
              </p>
              <p className="font-mono text-[14px] leading-relaxed text-slate-500">
                {disposition.dead_end_refusal.reason_code}
                {disposition.dead_end_refusal.detail
                  ? ` — ${disposition.dead_end_refusal.detail}`
                  : ""}
              </p>
            </div>
          )}

          {/* Unit #35 -- the ask-first QUESTION, and the ONLY thing that can
            * turn this refusal into a proposal. The refusal above is
            * unchanged and stays first: what follows is a question, not a
            * disabled button, and it appears only when the backend measured
            * link ends a connector could actually reach.
            *
            * Unit #42 -- the question got its SECOND honest answer. A world gap
            * can be the world's: the ramp really ends, and every candidate
            * below is a different road. So "여기서 끝납니다" joins the same
            * radio group -- one question, one group, mutually exclusive by
            * construction -- and the block now also renders when only that
            * answer is on offer. */}
          {worldGapAsked && (
            <div className="mt-3 flex flex-col gap-2 border-l-2 border-slate-300 pl-3">
              <p className="text-[15px] font-semibold text-slate-800">
                {t("실제 도로가 이어집니까?", "Does the real road continue here?", "実際の道路はここで続いていますか？")}
              </p>
              {/* Unit #41 -- THE CANDIDATE LIST BECAME A MAP CONTROL. It was a
                * <select> of `"<id> · <name> · <m> · <turn>"` strings, which is
                * two failures at once: a native option cannot answer a pointer,
                * so "이 후보가 어디인가" had no answer at all, and it led with
                * the id a consultant cannot place. Each candidate is now a row
                * that lights its own link on the map on hover, on focus and on
                * pick, and reads 도로명 first with the measured metres and the
                * turn behind it. Nothing about WHAT is offered changed: the
                * list, its order and its measured numbers are the backend's,
                * and the submitted payload is still the candidate's own
                * server-derived `params`. */}
              <fieldset className="flex flex-col gap-1">
                <legend className="text-[14px] text-slate-700">
                  {t("이어지는 링크", "The link it continues to", "続く先のリンク")}
                </legend>
                {(attestation?.candidates ?? []).map((candidate) => {
                  const numericId = Number(candidate.link_id);
                  const focus = () => {
                    if (Number.isFinite(numericId)) onFocusElement("link", numericId);
                  };
                  return (
                    <label
                      key={candidate.link_id}
                      onMouseEnter={focus}
                      className={`flex cursor-pointer items-baseline gap-2 rounded-md px-2 py-1 text-[14px] ${
                        attestTarget === candidate.link_id ? "bg-slate-100" : ""
                      }`}
                    >
                      <input
                        type="radio"
                        name={`attest-${instanceKey}`}
                        value={candidate.link_id}
                        checked={attestTarget === candidate.link_id}
                        onFocus={focus}
                        onChange={(event) => {
                          setAttestTarget(event.target.value);
                          focus();
                        }}
                      />
                      <span className="min-w-0">
                        <SubjectLabel
                          subject={candidate.name}
                          id={Number.isFinite(numericId) ? numericId : null}
                          className="text-[14px]"
                        />
                        <span className="ml-1.5 font-mono text-[14px] text-slate-500">
                          · {candidate.distance_m} m · {candidate.turn}
                        </span>
                      </span>
                    </label>
                  );
                })}
                {/* Unit #42 -- the answer no candidate can express, in the SAME
                  * group as the candidates because it is the same question's
                  * other answer. Hovering or picking it lights the link that
                  * would DISAPPEAR, not one that would be joined. */}
                {deadEnd && (
                  <label
                    onMouseEnter={() => {
                      if (contextLinkId !== null) onFocusElement("link", contextLinkId);
                    }}
                    className={`flex cursor-pointer items-baseline gap-2 rounded-md px-2 py-1 text-[14px] ${
                      deadEndChosen ? "bg-slate-100" : ""
                    }`}
                  >
                    <input
                      type="radio"
                      name={`attest-${instanceKey}`}
                      value={DEAD_END_ANSWER}
                      checked={deadEndChosen}
                      onFocus={() => {
                        if (contextLinkId !== null) onFocusElement("link", contextLinkId);
                      }}
                      onChange={(event) => {
                        setAttestTarget(event.target.value);
                        if (contextLinkId !== null) onFocusElement("link", contextLinkId);
                      }}
                    />
                    <span className="min-w-0">
                      <span className="font-semibold text-slate-800">
                        {t(
                          "아니요 — 여기서 실제로 끝납니다",
                          "No — the road really ends here",
                          "いいえ — ここで実際に終わります",
                        )}
                      </span>
                      <span className="ml-1.5 font-mono text-[14px] text-slate-500">
                        · way {deadEnd.way_id} · {t("링크", "links", "リンク")}{" "}
                        {deadEnd.link_ids.length} · {deadEnd.excluded_length_m} m
                      </span>
                    </span>
                  </label>
                )}
              </fieldset>
              {/* When the model holds NO reachable end, "이어집니다" is not an
                * answer this screen can offer -- said out loud rather than left
                * as an empty list a reader reads as a bug. The backend's own
                * rule (derive_v3_attestation_candidates): nothing to attest TO
                * means the honest answer is a wider extract. */}
              {(!attestation || attestation.candidates.length === 0) && (
                <p className="text-[14px] leading-relaxed text-slate-500">
                  {t(
                    "이 모델에는 이어붙일 수 있는 링크 끝이 없습니다 — 도로가 이어진다고 답하시려면 더 넓은 영역으로 다시 생성해야 합니다.",
                    "This model holds no reachable link end — answering that the road continues would need a regeneration over a wider extract.",
                    "このモデルには接続可能なリンク端がありません — 道路が続くと答えるには、より広い範囲での再生成が必要です。",
                  )}
                </p>
              )}
              {/* Unit #42 -- the SCOPE, shown before the signature and never
                * after it. What is removed is ONE way (the confirmed road), and
                * what the source says comes loose with it is named here rather
                * than discovered in the sibling's census afterwards. The empty
                * case says so too: "nothing else" is a measurement, not a
                * silence. */}
              {deadEndChosen && deadEnd && (
                <div className="flex flex-col gap-1 rounded-md bg-slate-50 px-2.5 py-2">
                  <p className="text-[14px] leading-relaxed text-slate-700">
                    {t(
                      `제외되는 것: way ${deadEnd.way_id} 하나 (census 링크 ${deadEnd.link_ids.length}개, ${deadEnd.excluded_length_m} m). 연결된 다른 도로는 제외되지 않습니다.`,
                      `Excluded: the one way ${deadEnd.way_id} (${deadEnd.link_ids.length} census link(s), ${deadEnd.excluded_length_m} m). No road connected to it is excluded.`,
                      `除外されるもの: way ${deadEnd.way_id} 1本（census リンク ${deadEnd.link_ids.length} 件、${deadEnd.excluded_length_m} m）。接続する他の道路は除外されません。`,
                    )}
                  </p>
                  {deadEnd.collateral.length > 0 ? (
                    <p className="text-[14px] leading-relaxed text-rose-700">
                      {t(
                        `주의 — 이 제외로 소스 기준 way ${deadEnd.collateral_way_ids.join(", ")}도 함께 끊깁니다. 확인 전에 지도에서 보십시오.`,
                        `Careful — by the source's own adjacency this also cuts loose way(s) ${deadEnd.collateral_way_ids.join(", ")}. Look at them on the map before confirming.`,
                        `注意 — この除外により、ソースの隣接関係では way ${deadEnd.collateral_way_ids.join(", ")} も一緒に切り離されます。確認前に地図でご覧ください。`,
                      )}
                    </p>
                  ) : (
                    <p className="text-[14px] leading-relaxed text-slate-500">
                      {t(
                        "소스 기준으로 이 제외 때문에 새로 끊기는 도로는 없습니다.",
                        "By the source's own adjacency this exclusion cuts nothing else loose.",
                        "ソースの隣接関係では、この除外によって新たに切り離される道路はありません。",
                      )}
                    </p>
                  )}
                </div>
              )}
              <label className="flex flex-col gap-1 text-[14px] text-slate-700">
                <span>{t("근거 (필수)", "Basis (required)", "根拠（必須）")}</span>
                <input
                  type="text"
                  value={attestBasis}
                  onChange={(event) => setAttestBasis(event.target.value)}
                  maxLength={600}
                  placeholder={
                    deadEndChosen
                      ? t(
                          "예: 구글맵 현장 확인 — 백화점 내부 순환로로 이어짐",
                          "e.g. Google Maps site check — it runs into the store's internal loop",
                          "例: Googleマップで現地確認 — 百貨店の内部循環路に続いています",
                        )
                      : t(
                          "예: 2026-08-10 현장 답사 — 도로가 실제로 이어집니다",
                          "e.g. site visit 2026-08-10 — the road really does continue",
                          "例: 2026-08-10 現地確認 — 道路は実際に続いています",
                        )
                  }
                  className="rounded-md border border-slate-300 bg-slate-100 px-2 py-1 text-[15px] text-slate-900"
                />
              </label>
              {/* Unit #41 -- the consent line a reader must meet BEFORE the
                * button stays visible and got shorter; the two standing
                * explanations it used to run on into (what the system
                * constructs, and why only a person can answer) moved intact
                * into the disclosure. Moved, never dropped.
                *
                * Unit #42 -- the two answers have DIFFERENT consequences, so
                * they got different sentences: attesting edits this artifact
                * with a real Vissim turn; confirming a dead end never touches
                * it and starts a new generation instead. One line for both
                * would have been a lie about one of them.
                *
                * Unit #41-b -- and the ATTESTING half of that pair is DELETED
                * on the product owner's own instruction (user, live pass
                * 2026-08-18: "'승인하면 실제 Vissim 편집이 실행되고 네트워크가
                * 다시 게이트를 거칩니다.' 이것도 없애도"). The contract pin that
                * held it was removed with it, by the same authority. What made
                * the dead-end sentence necessary is untouched: it names a
                * consequence a reader cannot infer from the button (a NEW job
                * from the same source, this artifact left alone), so it stays
                * exactly as unit #42 wrote it -- now rendered only for the
                * answer it is about. */}
              {deadEndChosen && (
                <p className="text-[14px] leading-relaxed text-slate-500">
                  {t(
                    "확인하면 이 way를 명명 제외한 새 생성이 같은 원본으로 시작됩니다 — 이 산출물은 편집되지 않고, 확인자·시각·근거 원문이 새 작업의 원장에 남습니다.",
                    "Confirming starts a NEW generation from the same source with this way excluded by name — this artifact is not edited, and who confirmed it, when, and their own words land in the new job's ledger.",
                    "確認すると、このwayを名前付きで除外した新しい生成が同じ元データから開始されます — この成果物は編集されず、確認者・時刻・根拠の原文が新しいジョブの台帳に残ります。",
                  )}
                </p>
              )}
              <div>
                {deadEndChosen ? (
                  <button
                    type="button"
                    disabled={confirmingDeadEnd || !attestBasis.trim()}
                    onClick={() => void confirmDeadEnd()}
                    className="ui-button-primary w-fit rounded-md px-3 py-1.5 text-[15px] disabled:cursor-not-allowed"
                  >
                    {confirmingDeadEnd
                      ? t("다시 생성하는 중…", "Re-deriving…", "再生成中…")
                      : t(
                          "막다른 길로 확인하고 다시 생성",
                          "Confirm the dead end and re-derive",
                          "行き止まりとして確認して再生成",
                        )}
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={applying || !attestChoice || !attestBasis.trim()}
                    onClick={() =>
                      attestChoice &&
                      void submit({
                        params: attestChoice.params,
                        to_link: attestChoice.link_id,
                        basis: attestBasis.trim(),
                      })
                    }
                    className="ui-button-primary w-fit rounded-md px-3 py-1.5 text-[15px] disabled:cursor-not-allowed"
                  >
                    {applying
                      ? t("적용하는 중…", "Applying…", "適用中…")
                      : t("확인하고 적용 승인", "Attest and approve", "確認して適用を承認")}
                  </button>
                )}
              </div>
              {deadEndError && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
                  <p className="font-semibold">
                    {
                      genesisRefusalText(
                        deadEndError,
                        t,
                        t(
                          "막다른 길 확인을 기록하지 못했습니다",
                          "The dead-end confirmation could not be recorded",
                          "行き止まりの確認を記録できませんでした",
                        ),
                      ).headline
                    }
                  </p>
                  <ul className="mt-1 space-y-1">
                    {genesisRefusalText(deadEndError, t, "").reasons.map((line) => (
                      <li key={line} className="font-mono text-[14px] leading-relaxed">
                        {line}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {error && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
                  <p className="font-semibold">{error.headline}</p>
                  {error.reasons.length > 0 && (
                    <ul className="mt-1 space-y-1">
                      {error.reasons.map((reason, index) => (
                        <li key={index} className="font-mono text-[14px] leading-relaxed">
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
      ) : (
        <div className="mt-2 flex flex-col gap-2">
          {/* Unit #41 -- one line a reader can act on, then the machine detail
            * behind a toggle. The verb string, the assumption tokens and the
            * backend's full rationale paragraph are all still here, verbatim
            * and complete, inside the 자세히 block below; what leads the card
            * now is the sentence that says whether this can be approved. */}
          <p className="leading-relaxed text-slate-800">
            {t(
              "승인하면 이 수정을 자동으로 적용할 수 있습니다.",
              "This fix can be applied automatically once approved.",
              "承認すればこの修正を自動的に適用できます。",
            )}
          </p>

          <span
            className={`w-fit rounded-full border px-1.5 py-0.5 text-[14px] font-semibold ${
              fixIsDemandEstimateFraming(disposition)
                ? "border-2 border-slate-400 bg-slate-100 text-slate-900 underline decoration-dotted decoration-slate-400 underline-offset-4"
                : "border-slate-300 bg-slate-100 text-slate-600"
            }`}
          >
            {fixConfidenceLabel(disposition.confidence, t)}
          </span>

          {fixIsDemandEstimateFraming(disposition) && (
            <p className="text-[14px] leading-relaxed text-slate-600">
              {t(
                "※ 수요 추정 — 이 값은 검증된 실측이 아니라 명시된 추정치입니다. 실제 진입 교통량을 아신다면 이 제안 대신 그 값을 직접 입력해 주십시오.",
                "NB DEMAND ESTIMATE — this value is a named estimate, not a verified measurement. If you know the real entrance count, enter it directly instead of approving this.",
                "※ 需要推定 — この値は検証済みの実測ではなく、明示された推定値です。実際の流入交通量をご存知であれば、この提案の代わりに直接入力してください。",
              )}
            </p>
          )}

          <CardDetails summary={moreLabel(t)}>
            <p className="font-mono leading-relaxed text-slate-700">{rationale}</p>
            {disposition.assumptions.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-4 leading-relaxed">
                {disposition.assumptions.map((token) => (
                  <li key={token}>{fixAssumptionLabel(token, t)}</li>
                ))}
              </ul>
            )}
            <p className="font-mono text-[14px] text-slate-500">
              {finding.code} · {t("동사", "Verb", "動詞")}: {disposition.verb}
              {disposition.reason_code ? ` · ${disposition.reason_code}` : ""}
            </p>
          </CardDetails>

          {/* Unit #41-c -- THE AUTO-PUBLISH DISCLOSURE IS DELETED, on the
            * product owner's own instruction (user, 2026-08-18: "1번 승인 카드
            * 문장도 없애고"). Unit #41-b had already deleted the attestation
            * card's shorter form of the same sentence by the same authority;
            * this was the longer one it was scoped to leave behind, and the
            * refuter's report named it as the surviving half. The B5-2 pins
            * that required it are removed with it, under that authority --
            * recorded here and in the contract file rather than dropped
            * silently, because a pin deleted without a named authority is
            * indistinguishable from one lost in a rebase.
            *
            * What the button does is not hidden by this: the verb, the
            * rationale and every machine identifier stay in the 자세히 block
            * directly above, and the sibling job the apply mints announces
            * itself with its own lineage line. */}

          {disposition.requires.length > 0 && (
            <div className="flex flex-col gap-1.5">
              {disposition.requires.map((field) => {
                const spec = fixRequiredFieldSpec(field, t);
                return (
                  <label key={field} className="flex items-center gap-2 text-[14px] text-slate-700">
                    <span className="min-w-[120px]">{spec.label}</span>
                    <input
                      type={spec.numeric ? "number" : "text"}
                      value={requiredValues[field] ?? ""}
                      onChange={(event) =>
                        setRequiredValues((current) => ({ ...current, [field]: event.target.value }))
                      }
                      className="w-32 rounded-md border border-slate-300 bg-slate-100 px-2 py-1 text-[14px] text-slate-900"
                    />
                  </label>
                );
              })}
            </div>
          )}

          {/* Unit #35 -- the ask-first confirmation. The to-link was DERIVED
            * (from the source's own testimony), and the one thing a reader can
            * check that no derivation can is whether it is the road they mean.
            * Unticked, there is no approve button to press. */}
          {needsTargetConfirmation && (
            <div className="flex flex-col gap-1">
              {/* Unit #41 -- the tick asks the reader to check the map, and
                * until now the screen gave them no way to get there. The
                * control under it shows the DERIVED to-link itself, on the map
                * beside this card. */}
              {disposition.params?.to_link !== undefined &&
                Number.isFinite(Number(disposition.params?.to_link)) && (
                  <button
                    type="button"
                    onClick={() => onFocusElement("link", Number(disposition.params?.to_link))}
                    className="ui-button-ghost w-fit rounded-full px-2.5 py-0.5 text-[14px] font-medium"
                  >
                    {t("지도에서 대상 링크 보기", "Show the target link on the map", "地図で対象リンクを見る")}
                  </button>
                )}
              <label className="flex items-start gap-2 text-[14px] leading-relaxed text-slate-700">
                <input
                  type="checkbox"
                  checked={targetConfirmed}
                  onChange={(event) => setTargetConfirmed(event.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  {t(
                    `링크 ${String(disposition.params?.to_link ?? "")}로 이어지는 것이 맞는지 지도에서 확인했습니다`,
                    `I checked on the map that it continues to link ${String(disposition.params?.to_link ?? "")}`,
                    `リンク ${String(disposition.params?.to_link ?? "")} へ続くことを地図で確認しました`,
                  )}
                </span>
              </label>
            </div>
          )}

          <label className="flex flex-col gap-1 text-[14px] text-slate-600">
            <span>{t("사유 (선택)", "Rationale (optional)", "理由（任意）")}</span>
            <input
              type="text"
              value={approvalRationale}
              onChange={(event) => setApprovalRationale(event.target.value)}
              maxLength={600}
              className="rounded-md border border-slate-300 bg-slate-100 px-2 py-1 text-[15px] text-slate-900"
            />
          </label>

          <div>
            <button
              type="button"
              disabled={applying || missingRequired || (needsTargetConfirmation && !targetConfirmed)}
              onClick={() => void submit()}
              className="ui-button-primary w-fit rounded-md px-3 py-1.5 text-[15px] disabled:cursor-not-allowed"
            >
              {applying ? t("적용하는 중…", "Applying…", "適用中…") : t("적용 승인", "Approve fix", "適用を承認")}
            </button>
          </div>

          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
              <p className="font-semibold">{error.headline}</p>
              {error.reasons.length > 0 && (
                <ul className="mt-1 space-y-1">
                  {error.reasons.map((reason, index) => (
                    <li key={index} className="font-mono text-[14px] leading-relaxed">
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

// ---------------------------------------------------------------------------
// Unit #33 -- V1's own control. The gate check that needs a real Vissim gets
// the button that gets it one.
// ---------------------------------------------------------------------------

/** The job states that HAVE a gated artifact on disk -- backend
 * `genesis_jobs.GATED_STATUSES`, mirrored here for ONE purpose: deciding
 * whether to render a control at all. The server's own eligibility check is the
 * authoritative one and its refusal renders through the same
 * `refusalHeadline` table below, so a drift here shows up as a 409 with a
 * sentence, never as a button that silently does nothing. */
const GATED_STATUSES: GenesisJob["status"][] = ["refused", "provisional", "published"];

/** Why this generation cannot be live-checked, as the BACKEND's own refusal
 * code, or `null` when it can be. */
function liveCheckRefusalCode(job: GenesisJob): string | null {
  if (!GATED_STATUSES.includes(job.status)) return "genesis_live_check_parent_not_gated";
  if (!job.artifact) return "genesis_live_check_artifact_missing";
  return null;
}

/**
 * THE V1 TRIGGER -- "실 Vissim으로 확인".
 *
 * V1 is the only check that cannot be answered offline: it asks whether a REAL
 * PTV Vissim opens this file and holds the network afterwards. Until this unit
 * the only way to answer it was for somebody to run
 * `backend/tools/genesis_open_live_check.py` at a console and post its
 * state.json back, which is why every V1 that ever went green in the product
 * did so on an operator's attestation. This button queues a JOB that opens the
 * artifact itself; the COM turn runs on the job queue, never in the request.
 *
 * WHAT THIS COMPONENT MAY NOT DO, and the reason the code reads the way it
 * does: it decides nothing. It starts a job, shows that the job is running, and
 * gets out of the way -- the verdict comes back through the V1 row itself,
 * re-fetched from the STORED gate the moment the parent's `checked_at` moves.
 * A load that FAILED is not an error here either: the probe worked, the gate
 * refused the network, and the words a reader gets are gate.py's own.
 *
 * INELIGIBLE IS A SENTENCE, NEVER A GREYED BUTTON (this project's
 * refusal-is-information rule): a generation with no gated artifact renders the
 * named reason and no control at all.
 */
function LiveCheckControl({
  jobId,
  refusalCode,
  t,
  onCompleted,
}: {
  jobId: string;
  refusalCode: string | null;
  t: Translate;
  onCompleted: () => void;
}) {
  const [starting, setStarting] = useState(false);
  const [checkJob, setCheckJob] = useState<GenesisJob | null>(null);
  const [startError, setStartError] = useState<GenesisError | Error | null>(null);

  // Poll the live-check job until it stops moving. One chained timeout per
  // tick (the same shape the page's own job poll uses) rather than an
  // interval, so a slow response can never stack requests.
  useEffect(() => {
    if (!checkJob || isTerminal(checkJob.status)) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      fetchGenesisJob(checkJob.id)
        .then((next) => {
          if (cancelled) return;
          setCheckJob(next);
          // The parent's verdict has just been rewritten -- ask for it NOW
          // rather than waiting out the terminal re-check interval.
          if (isTerminal(next.status)) onCompleted();
        })
        .catch(() => {
          /* a dropped tick retries on the next one */
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [checkJob, onCompleted]);

  const start = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    try {
      setCheckJob(await startGenesisLiveCheck(jobId));
    } catch (caught) {
      setStartError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setStarting(false);
    }
  }, [jobId]);

  if (refusalCode) {
    return (
      <div className="mt-4 border-t border-slate-200 pt-3">
        <p className="font-mono text-[15px] leading-relaxed text-slate-700">
          {refusalHeadline(refusalCode, t)}
        </p>
      </div>
    );
  }

  const running = checkJob !== null && !isTerminal(checkJob.status);
  const failure = startError
    ? genesisRefusalText(
        startError,
        t,
        t(
          "확인을 시작하지 못했습니다",
          "The live check could not be started",
          "確認を開始できませんでした",
        ),
      )
    : null;

  return (
    <div className="mt-4 border-t border-slate-200 pt-3">
      <p className="text-[14px] leading-relaxed text-slate-600">
        {t(
          "이 생성의 .inpx를 실제 PTV Vissim으로 열어 보고, 그 결과로 판정을 다시 받습니다. 로딩은 작업 큐에서 실행됩니다.",
          "Opens this generation's .inpx in a real PTV Vissim and re-gates the verdict on what the load did. The load runs on the job queue.",
          "この生成の .inpx を実際の PTV Vissim で開き、その結果で判定をやり直します。読み込みはジョブキューで実行されます。",
        )}
      </p>

      <button
        type="button"
        disabled={starting || running}
        onClick={() => void start()}
        className="ui-button-primary mt-2 w-fit rounded-md px-3 py-1.5 text-[15px] disabled:cursor-not-allowed"
      >
        {starting
          ? t("확인 작업을 넣는 중…", "Queueing…", "確認ジョブを投入中…")
          : running
            ? t("실 Vissim에서 확인 중…", "Checking in a real Vissim…", "実Vissimで確認中…")
            : t("실 Vissim으로 확인", "Check in a real Vissim", "実Vissimで確認")}
      </button>

      {checkJob && (
        <div aria-live="polite" className="mt-2 text-[14px] leading-relaxed text-slate-600">
          {running && (
            <p className="font-mono">
              {checkJob.status} · {checkJob.phase}
            </p>
          )}
          {checkJob.status === "checked" && checkJob.live_check && (
            <>
              <p>
                {checkJob.live_check.load_ok
                  ? t(
                      "실 Vissim이 이 파일을 열었습니다 — 위의 판정은 그 결과로 다시 내려졌습니다.",
                      "A real Vissim opened this file — the verdict above was re-decided on that result.",
                      "実Vissimがこのファイルを開きました — 上の判定はその結果で下し直されました。",
                    )
                  : t(
                      "실 Vissim이 이 파일을 열지 못했습니다 — 판정은 게이트가 그 증거로 다시 내렸습니다.",
                      "A real Vissim could not open this file — the gate re-decided the verdict on that evidence.",
                      "実Vissimがこのファイルを開けませんでした — 判定はゲートがその証拠で下し直しました。",
                    )}
              </p>
              {checkJob.live_check.admitted === false && (
                <p className="mt-1">
                  {t(
                    "이 증거는 채택되지 않았습니다 — 검사한 파일과 판정 대상 파일이 같은 바이트가 아닙니다",
                    "The evidence was not admitted — the file checked and the file gated are not the same bytes",
                    "この証拠は採用されませんでした — 検査したファイルと判定対象のファイルが同一バイトではありません",
                  )}
                  {checkJob.live_check.gap_codes.length > 0 &&
                    ` (${checkJob.live_check.gap_codes.join(", ")})`}
                </p>
              )}
            </>
          )}
          {checkJob.status === "error" && checkJob.refusal && (
            <div className="mt-1 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
              <p className="font-semibold">
                {t(
                  "확인을 실행하지 못했습니다 — 판정은 그대로입니다",
                  "The check could not run — the verdict is unchanged",
                  "確認を実行できませんでした — 判定はそのままです",
                )}
              </p>
              <ul className="mt-1 space-y-1">
                {checkJob.refusal.reasons.map((reason, index) => (
                  <li key={index} className="font-mono text-[14px] leading-relaxed">
                    {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {failure && (
        <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
          <p className="font-semibold">{failure.headline}</p>
          {failure.reasons.length > 0 && (
            <ul className="mt-1 space-y-1">
              {failure.reasons.map((reason, index) => (
                <li key={index} className="font-mono text-[14px] leading-relaxed">
                  {reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * V1–V9 AS A GRID -- EVERY CELL STARTS CLOSED. Transplanted verbatim from
 * app/genesis/page.tsx (2026-08-13 doctrine, unchanged by this unit).
 */
function GateCheckGrid({
  checks,
  t,
  lang,
  jobId,
  liveCheckRefusal,
  onLiveCheckCompleted,
  fixes,
  fixesError,
  onFixApplied,
  placeable,
  onFocusFinding,
  onFocusElement,
}: {
  checks: GenesisGateCheck[];
  t: Translate;
  lang: string;
  jobId: string;
  liveCheckRefusal: string | null;
  onLiveCheckCompleted: () => void;
  fixes: GenesisJobFixes | null;
  fixesError: GenesisError | Error | null;
  onFixApplied: (job: GenesisJob) => void;
  /** Unit #41 -- `findingKeyOf` for every finding the map can actually place.
   * A row outside this set stays a plain line: it has no location to show, and
   * a control that looks like it does is worse than none. */
  placeable: ReadonlySet<string>;
  onFocusFinding: (ref: FindingRef | null, point?: { lat: number; lon: number; label: string }) => void;
  onFocusElement: (kind: "link" | "connector", id: number) => void;
}) {
  // NO initial selection, by the user's explicit instruction. `null` is the
  // mount state and stays until a click.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = selectedId ? checks.find((check) => check.id === selectedId) ?? null : null;

  // Unit #41 -- which of THIS check's findings carry an applicable proposal.
  // Keyed by (code, subject), the same identity the backend addresses a
  // finding by, so the gate report's rows and the fix catalogue's rows line up
  // without either side matching on message text.
  const proposalKeys = new Set(
    (fixes?.findings ?? [])
      .filter((entry) => selected && entry.check_id === selected.id && fixFindingIsProposal(entry))
      .map((entry) => findingKeyOf(entry)),
  );
  const checkFixes = (fixes?.findings ?? []).filter(
    (entry) => selected && entry.check_id === selected.id,
  );
  // Unit #41-b -- THE MERGE, AND WHY IT IS POSITIONAL.
  //
  // The 수정 제안 section is gone (user, live pass 2026-08-18: "수정 제안 칸
  // 따로 만들지 말고 위에 걍 넣어 같이. 그럼 이름 반복될 필요도 없고"), so each
  // disposition renders inside the 소견 that raised it and the subject is
  // printed once per card instead of once per list.
  //
  // WHICH catalogue entry belongs to WHICH gate row is not a (code, subject)
  // lookup, and deliberately so: gate.py legitimately emits the same pair twice
  // (`v3_silent_departure_point / 대덕대로 [FWD] w52496957s1`, for links 427 AND
  // 429), so that key cannot tell the two rows apart -- the very hole unit #41's
  // refuter closed on the row identity. Both payloads are walks of the SAME
  // stored gate.json: `gate_payload` renders `check.findings` then
  // `check.warnings` in order, and the fixes route's
  // `iter_stored_gate_findings` walks `("findings", "warnings")` in the same
  // order. So the n-th gate row of an origin is the n-th catalogue entry of that
  // origin, exactly, and the pairing needs no matching at all.
  //
  // A DISPLAY MERGE ONLY: `fixes.findings` is filtered into new arrays and never
  // sorted or mutated, the apply target is still the finding's own
  // (code, subject), and `rowKeyOf` below is still the PAYLOAD index.
  const fixesFromOrigin = (via: "finding" | "warning"): GenesisFixFinding[] =>
    checkFixes.filter((entry) => entry.origin === via);
  // A ROW'S IDENTITY IS ITS PLACE IN THE PAYLOAD, NEVER ITS PLACE ON SCREEN --
  // unit #41's refuter, unchanged by the merge and only moved out of the render
  // body. `orderedFixes` was a DISPLAY partition and the proposal-first order
  // now lives in `CollapsibleMessages`; the fixes fetch is keyed on
  // `gate.checked_at`, so a live check replaces this payload with no navigation
  // and a row can cross the partition while the reader is mid-card. Indexed by
  // display position, that reorder makes React preserve a key whose finding
  // CHANGED: `<code>::<subject>::2` was link 429's card and becomes 427's,
  // carrying 429's typed attestation basis, its picked destination and its
  // ticked map-confirmation onto a different link's finding. An attestation is a
  // recorded human claim about ONE object; it may never slide onto another. The
  // payload index is stable across every reorder. Reference-identity `indexOf`:
  // each parsed finding is its own object, so duplicates get distinct indices.
  const rowKeyOf = (entry: GenesisFixFinding): string =>
    `${entry.code}::${entry.subject}::${checkFixes.indexOf(entry)}`;

  return (
    <div>
      <div className="grid grid-cols-3 gap-3.5">
        {checks.map((check) => {
          const isSelected = check.id === selectedId;
          return (
            <button
              key={check.id}
              type="button"
              onClick={() => setSelectedId((current) => (current === check.id ? null : check.id))}
              aria-pressed={isSelected}
              title={check.title}
              className={`flex flex-col gap-1.5 rounded-lg border px-3 py-2.5 text-left transition-[background-color,box-shadow,color] duration-100 ${checkCellClasses(isSelected)}`}
            >
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[15px] font-bold">{check.id}</span>
                <span
                  className={`rounded-full border px-1.5 py-0.5 text-[14px] font-bold uppercase ${checkStateTagClasses(check.state)}`}
                >
                  {checkStateLabel(check.state, t)}
                </span>
              </span>
              {/* CORE NAME ONLY before a click (user, live pass 2026-08-14):
                * the gate titles are "이름 — 설명 한 문장" and nine clamped
                * sentences made the grid a wall of text. The cell shows the
                * name; the full sentence stays on hover (title=) and in the
                * detail below once the cell is opened. Derived by splitting
                * the payload's own string — no client-side name table. */}
              <span
                className={`line-clamp-1 text-[14px] leading-tight ${isSelected ? "font-semibold text-slate-900" : "font-medium"}`}
              >
                {check.title.split(" — ")[0]}
              </span>
            </button>
          );
        })}
      </div>

      <div aria-live="polite">
        {selected && (
          <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-[15px] text-slate-800">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-mono text-[15px] font-bold">{selected.id}</span>
              <span
                className={`rounded-full border px-1.5 py-0.5 text-[14px] font-bold uppercase ${checkStateTagClasses(selected.state)}`}
              >
                {checkStateLabel(selected.state, t)}
              </span>
              <span className="text-[14px] text-slate-600">
                {selected.tier === "refuse"
                  ? t("거부 등급", "refuse tier", "拒否ティア")
                  : t("경고 등급", "warn tier", "警告ティア")}
              </span>
            </div>
            {/* Unit #41-b -- the 수정 제안 SECTION is gone, and its ⓘ is here.
              * The sentence was never about a box: it says what the catalogue
              * answers "이 검사가 낸 각 발견에 대해", which is exactly what this
              * check's title names -- so it reads on the title, verbatim, while
              * the dispositions it describes now render inside the 소견 rows
              * below. Moved, never dropped. */}
            <p className="mt-1.5 font-semibold">
              {selected.title}
              <InfoTip
                text={t(
                  "이 검사가 낸 각 발견에 대해, 등록된 수정 카탈로그가 자동으로 고칠 수 있는지와 그 근거를 답합니다. 자동 수정이 없다는 답은 정보이지 버튼이 아닙니다 — 거부는 진단, 에이전트는 치료.",
                  "For each finding this check raised, the registered fix catalogue answers whether it can be automatically repaired and on what evidence. \"No automated fix\" is information, never a button — a refusal is a diagnosis, and the agent is the treatment.",
                  "この検査が出した各発見について、登録済みの修正カタログが自動修正可能かとその根拠を答えます。「自動修正なし」は情報であり、ボタンではありません — 拒否は診断、エージェントは治療。",
                )}
              />
            </p>

            <div className="mt-2">
              {/* The catalogue's own three states, as LINES rather than a
                * titled panel -- they are about the dispositions that render
                * inside the rows below, so they are said before them and take
                * no box of their own. Every sentence is the one the retired
                * section carried, word for word. */}
              {checkHasBody(selected) && !fixes && !fixesError && (
                <p className="mb-1.5 text-[15px] text-slate-500">
                  {t("수정 제안을 불러오는 중…", "Loading fix proposals…", "修正提案を読み込み中…")}
                </p>
              )}
              {checkHasBody(selected) && fixesError && (
                <p className="mb-1.5 text-[15px] leading-relaxed text-slate-600">
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
              {checkHasBody(selected) && fixes && checkFixes.length === 0 && (
                <p className="mb-1.5 text-[15px] text-slate-500">
                  {t(
                    "이 검사에는 개별 수정 제안이 없습니다.",
                    "No individual fix proposal for this check.",
                    "この検査には個別の修正提案がありません。",
                  )}
                </p>
              )}

              {selected.gaps.length > 0 && (
                <ul className="space-y-1">
                  {selected.gaps.map((gap) => (
                    <li key={gap.code} className="text-[15px] leading-relaxed">
                      {t("실행 불가", "could not run", "実行不可")}: {gap.message}
                    </li>
                  ))}
                </ul>
              )}

              {selected.findings.length > 0 && (
                <CollapsibleMessages
                  key={`f-${selected.id}`}
                  check={selected}
                  findings={selected.findings}
                  via="finding"
                  proposalKeys={proposalKeys}
                  placeable={placeable}
                  onFocusFinding={onFocusFinding}
                  checkFixes={fixesFromOrigin("finding")}
                  rowKeyOf={rowKeyOf}
                  jobId={jobId}
                  lang={lang}
                  onFixApplied={onFixApplied}
                  onFocusElement={onFocusElement}
                  t={t}
                />
              )}
              {selected.findings_omitted > 0 && (
                <p className="mt-1.5 text-[14px] text-slate-600">
                  {t(
                    `이 화면에 표시되지 않은 항목 ${selected.findings_omitted}건 — gate.json 전체 내려받기에 모두 들어 있습니다.`,
                    `${selected.findings_omitted} more not shown here — all of them are in the gate.json download.`,
                    `この画面に表示されていない項目 ${selected.findings_omitted} 件 — gate.json のダウンロードにすべて含まれています。`,
                  )}
                </p>
              )}

              {selected.warnings.length > 0 && (
                <CollapsibleMessages
                  key={`w-${selected.id}`}
                  check={selected}
                  findings={selected.warnings}
                  via="warning"
                  proposalKeys={proposalKeys}
                  placeable={placeable}
                  onFocusFinding={onFocusFinding}
                  checkFixes={fixesFromOrigin("warning")}
                  rowKeyOf={rowKeyOf}
                  jobId={jobId}
                  lang={lang}
                  onFixApplied={onFixApplied}
                  onFocusElement={onFocusElement}
                  t={t}
                />
              )}

              {!checkHasBody(selected) && (
                <p className="text-[15px] leading-relaxed text-slate-600">
                  {t(
                    "이 검사에 기록된 항목이 없습니다.",
                    "Nothing was recorded for this check.",
                    "この検査に記録された項目はありません。",
                  )}
                </p>
              )}

              {/* V1 -- and ONLY V1 -- carries a control, because it is the one
                * check whose evidence this app can go and get (unit #33). It
                * sits inside the opened cell, under the gate's own words, so a
                * reader has already seen WHY V1 is grey before being offered
                * the thing that answers it. */}
              {selected.id === "V1" && (
                <LiveCheckControl
                  jobId={jobId}
                  refusalCode={liveCheckRefusal}
                  t={t}
                  onCompleted={onLiveCheckCompleted}
                />
              )}

            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** The five downloadable artifacts. */
const ARTIFACT_KINDS: Array<{ kind: GenesisArtifactKind; label: (t: Translate) => string }> = [
  { kind: "inpx", label: (t) => t("네트워크 .inpx", "Network .inpx", "ネットワーク .inpx") },
  { kind: "compliance_md", label: (t) => t("§5 준수 리포트", "§5 compliance report", "§5 準拠レポート") },
  { kind: "gate_report", label: (t) => t("게이트 리포트", "Gate report", "ゲートレポート") },
  { kind: "gate_json", label: () => "gate.json" },
  { kind: "genesis_json", label: (t) => t("생성기 명부", "Generator census", "生成器の名簿") },
];

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

/** Long finding lists fold (user, live pass 2026-08-14): V7-class checks carry
 * dozens of same-shaped rows — the first few state the pattern, the rest are
 * one click away. This fold is PRESENTATION ONLY: the server-side cap's own
 * named-omission line ("표시되지 않은 N건 — gate.json") is evidence and renders
 * separately, untouched. Callers key this by the selected check's id so the
 * fold resets when the reader moves to another check. */
const FINDINGS_COLLAPSED_COUNT = 6;

/**
 * THE FINDING LIST -- unit #41 turned it from printed text into the screen's
 * primary index of the verdict. Three things changed, all of them the user's
 * own findings from the 2026-08-17 live pass:
 *
 *   * A ROW IS A CONTROL when the census could place its subject. This is the
 *     "소견을 눌러도 지도가 반응하지 않는다" bug: nothing here was ever a
 *     button, in either direction, so no click could reach the map. A row
 *     OUTSIDE `placeable` stays a plain `<li>` -- the gate carries no location
 *     reference for it, and the panel's own "지도에 표시하지 못한 발견" list is
 *     where it is named. No invented coordinates, then or now.
 *   * A ROW CARRYING A PROPOSAL SORTS FIRST and wears the sky badge.
 *   * AND THE FOLD CAN NEVER CLOSE OVER ONE. `visibleCount` is the greater of
 *     the standing cut and the number of proposals, so the case that opened
 *     this unit (one applicable proposal, ninth of ten, under a six-item fold)
 *     cannot recur even if a check ever raises more proposals than the cut.
 *
 * The fold itself is unchanged and still PRESENTATION ONLY: the server-side
 * cap's own named-omission line ("표시되지 않은 N건 — gate.json") is evidence
 * and renders separately, untouched. Callers key this by the selected check's
 * id so the fold resets when the reader moves to another check.
 *
 * UNIT #41-b MERGED THE PROPOSAL INTO THE ROW. The 수정 제안 section under this
 * list re-printed every subject a second time to say what could be done about
 * it ("이름 반복 service[FWD] w1294917882s0 이게 너무 자주돼" — user, live pass
 * 2026-08-18), so the disposition now renders inside the row that raised it and
 * the subject is printed once. Three things this does NOT change, each of them
 * a claim some earlier unit had to buy:
 *
 *   * the fix payload is only READ here — the apply target is still the
 *     finding's own (code, subject) and the catalogue array is never sorted;
 *   * a row's identity is still its PAYLOAD index (`rowKeyOf`, and `<li>`'s own
 *     key below), never its position in the proposal-first display order —
 *     which now matters MORE than it did, because the stateful card lives
 *     inside the reordered list rather than beside it;
 *   * a card whose gate row the payload cap left out is still rendered, under
 *     the fold, carrying its own subject: the cap omits a finding's WORDING,
 *     and this list may not turn that into omitting its disposition too.
 */
function CollapsibleMessages({
  check,
  findings,
  via,
  proposalKeys,
  placeable,
  onFocusFinding,
  checkFixes,
  rowKeyOf,
  jobId,
  lang,
  onFixApplied,
  onFocusElement,
  t,
}: {
  check: GenesisGateCheck;
  findings: GenesisGateFinding[];
  via: "finding" | "warning";
  proposalKeys: ReadonlySet<string>;
  placeable: ReadonlySet<string>;
  onFocusFinding: (ref: FindingRef | null, point?: { lat: number; lon: number; label: string }) => void;
  /** Unit #41-b -- THIS ORIGIN's fix-catalogue entries for this check, in the
   * payload's own order. Paired to `findings` POSITIONALLY: both arrays are
   * walks of the same stored gate.json in the same order, so the n-th entry
   * here is the n-th row there — see `fixesFromOrigin`'s own comment for why a
   * (code, subject) lookup could not do this job. */
  checkFixes: GenesisFixFinding[];
  rowKeyOf: (entry: GenesisFixFinding) => string;
  jobId: string;
  lang: string;
  onFixApplied: (job: GenesisJob) => void;
  onFocusElement: (kind: "link" | "connector", id: number) => void;
  t: Translate;
}) {
  const [expanded, setExpanded] = useState(false);

  const rows = findings.map((finding, payloadIndex) => ({
    finding,
    payloadIndex,
    key: findingKeyOf(finding),
    fix: checkFixes[payloadIndex] ?? null,
    ref: { checkId: check.id, checkTitle: check.title, tier: check.tier, finding, via } as FindingRef,
  }));
  const withProposal = rows.filter((row) => proposalKeys.has(row.key));
  const ordered = [...withProposal, ...rows.filter((row) => !proposalKeys.has(row.key))];
  const visibleCount = Math.max(FINDINGS_COLLAPSED_COUNT, withProposal.length);
  // The catalogue answers for EVERY stored finding; the gate payload carries at
  // most `MAX_FINDINGS_IN_PAYLOAD` of them. The tail has no row to sit under, so
  // it keeps the standing card shape (subject and all) rather than disappearing.
  const unpaired = checkFixes.slice(findings.length);
  const overflow = ordered.length - visibleCount;
  const visible = expanded || overflow <= 0 ? ordered : ordered.slice(0, visibleCount);

  return (
    <>
      <ul className="mt-2 space-y-1">
        {visible.map((row) => {
          // THE BADGE READS THIS ROW'S OWN DISPOSITION -- the one rendered
          // directly beneath it -- never the check-wide (code, subject) set.
          // `proposalKeys` cannot tell gate.py's duplicate pair apart
          // (`v3_silent_departure_point / 대덕대로 [FWD] w52496957s1`, raised for
          // links 427 AND 429), so when one of the two is derivable and the
          // other is not, BOTH rows used to wear 제안 -- and unit #41-b put the
          // card inside the row, which turns that into a sky 제안 badge sitting
          // one line above its own card's refusal. A badge may not promise a
          // button that is not there, and now that the row knows which
          // catalogue entry is its own, it can answer exactly. A row with no
          // paired entry has no card to approve, so it claims nothing.
          //
          // The FOLD is still driven by `proposalKeys` (`withProposal` above),
          // and safely: every genuine proposal's (code, subject) is in that
          // set, so it stays a SUPERSET -- it can only reveal a row early,
          // never hide one behind the fold.
          const isProposal = Boolean(row.fix && fixFindingIsProposal(row.fix));
          const badge = isProposal ? (
            <span
              className={`mr-1.5 rounded-full border px-1.5 py-0.5 text-[14px] font-bold ${fixProposalBadgeClasses()}`}
            >
              {t("제안", "Proposal", "提案")}
            </span>
          ) : null;
          const body = (
            <>
              {badge}
              {via === "warning" && <span className="mr-1 text-slate-500">warn /</span>}
              <SubjectLabel subject={row.finding.subject} className="text-[15px]" />
              {/* Unit #41-b -- the gate's own sentence, minus the subject the
                * line above just printed. `messageWithoutSubject` trims ONLY an
                * exact leading copy; every other message comes through
                * verbatim, and nothing here authors a replacement. */}
              <span className="mt-0.5 block leading-relaxed">
                {messageWithoutSubject(row.finding.subject, row.finding.message)}
              </span>
            </>
          );
          return (
            // KEYED ON THE PAYLOAD INDEX, not the display index: this row now
            // carries a stateful fix card, and the proposal-first order above is
            // a partition a live-check re-gate can move a row across.
            <li key={`${row.key}::${row.payloadIndex}`} className="text-[15px] leading-relaxed">
              {placeable.has(row.key) ? (
                <button
                  type="button"
                  onClick={() => onFocusFinding(row.ref)}
                  title={t("지도에서 보기", "Show on the map", "地図で見る")}
                  className="w-full rounded-md border border-transparent px-1.5 py-1 text-left hover:border-slate-300 hover:bg-slate-100"
                >
                  {body}
                </button>
              ) : (
                <span className="block px-1.5 py-1">{body}</span>
              )}
              {/* THE MERGE ITSELF -- a SIBLING of the row's control, never
                * inside it: the card carries buttons, radios and text inputs,
                * and nesting those in the row's own <button> would be both
                * invalid markup and a click the reader cannot aim. */}
              {row.fix && (
                <FixFindingRow
                  key={rowKeyOf(row.fix)}
                  instanceKey={rowKeyOf(row.fix)}
                  showSubject={false}
                  jobId={jobId}
                  finding={row.fix}
                  t={t}
                  lang={lang}
                  onApplied={onFixApplied}
                  onFocusElement={onFocusElement}
                  onFocusPoint={(point) => onFocusFinding(row.ref, point)}
                />
              )}
            </li>
          );
        })}
      </ul>
      {overflow > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((open) => !open)}
          className="ui-button-ghost mt-1.5 rounded-full px-2.5 py-0.5 text-[14px] font-medium"
        >
          {expanded
            ? t("접기", "Collapse", "折りたたむ")
            : t(`나머지 ${overflow}건 펼치기`, `Show ${overflow} more`, `残り${overflow}件を表示`)}
        </button>
      )}
      {/* Unit #41-b -- the dispositions with no row of their own. The gate
        * payload caps how many findings it CARRIES (backend
        * `MAX_FINDINGS_IN_PAYLOAD`), while the fix catalogue answers for every
        * stored one, so a check over the cap has catalogue entries whose
        * wording is only in gate.json. They were rendered before this unit and
        * they are rendered after it -- named as what they are, and carrying
        * their own subject because there is no row above to carry it. */}
      {unpaired.length > 0 && (
        <div className="mt-2 flex flex-col divide-y divide-slate-200 border-t border-slate-200">
          <p className="pt-2 text-[14px] leading-relaxed text-slate-600">
            {t(
              `위 목록에 실리지 않은 소견 ${unpaired.length}건의 처분입니다 — 문구는 gate.json 전체 내려받기에 있습니다.`,
              `Dispositions for ${unpaired.length} finding(s) this list does not carry — their wording is in the gate.json download.`,
              `上の一覧に載っていない検出 ${unpaired.length} 件の処分です — 文言は gate.json のダウンロードに含まれています。`,
            )}
          </p>
          {unpaired.map((entry) => (
            <FixFindingRow
              key={rowKeyOf(entry)}
              instanceKey={rowKeyOf(entry)}
              showSubject
              jobId={jobId}
              finding={entry}
              t={t}
              lang={lang}
              onApplied={onFixApplied}
              onFocusElement={onFocusElement}
              onFocusPoint={(point) => onFocusFinding(null, point)}
            />
          ))}
        </div>
      )}
    </>
  );
}

/** THE ARTIFACT ROW. Unchanged mechanics from app/genesis/page.tsx, only the
 * bar it sits in moved -- top bar, right side, rather than a titled section
 * under the map. Each entry wears a download glyph (user, live pass
 * 2026-08-14: the bare pills read as tags, not as things that download). */
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
            className="ui-button-ghost inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[14px] font-medium disabled:cursor-not-allowed disabled:opacity-60"
          >
            <svg
              viewBox="0 0 24 24"
              className="h-3.5 w-3.5 shrink-0"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M12 4v11" />
              <path d="m7.5 11 4.5 4.5L16.5 11" />
              <path d="M5 20h14" />
            </svg>
            {pending === artifact.kind
              ? t("내려받는 중…", "Downloading…", "ダウンロード中…")
              : artifact.label(t)}
          </button>
        ))}
      </div>
      {failure && (
        <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
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

/** One titled block, full width -- used for 생성 비교 below the map/rail split.
 * "your call, named" (this unit's own brief): the comparison table does not
 * fit a 35%-wide rail without recreating the exact cramped column this unit
 * exists to fix, so it stays full width, under the split, like the other
 * things this page cannot fit two-up. */
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

export default function GenesisJobDetailPage() {
  const { lang, t } = useLanguage();
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const jobId = typeof params?.id === "string" ? params.id : "";

  // Sibling list, for the comparison panel's own "compare against" picker
  // only -- best-effort, the same discipline the hub's side-input listing
  // uses (a failed fetch here must not block the rest of this page).
  const [jobs, setJobs] = useState<GenesisJob[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetchGenesisJobs()
      .then((entries) => {
        if (!cancelled) setJobs(entries);
      })
      .catch(() => {
        if (!cancelled) setJobs([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // THE STALE-FLASH GUARD -- see this file's header comment. `selected` is
  // read back only when the cached entry's own id still matches the URL's
  // `jobId`, exactly the discipline the old drawer used for the same reason.
  const [selectedEntry, setSelectedEntry] = useState<{ id: string; record: GenesisJob } | null>(null);
  const [notFoundId, setNotFoundId] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const selected = selectedEntry && selectedEntry.id === jobId ? selectedEntry.record : null;
  const notFound = notFoundId === jobId;

  const [reportEntry, setReportEntry] = useState<
    { jobId: string; language: string; payload: GenesisGateReport } | null
  >(null);
  const [reportError, setReportError] = useState<string | null>(null);
  // ONE answer for the fix strip, carrying the (job, verdict) it describes --
  // the shape lib/asyncResource.ts's `Loaded` names, written out by hand here
  // because this screen needs the GenesisError INSTANCE (it says something
  // different for a 401 than for anything else) and `Loaded.error` is a bare
  // string. Payload and failure share one key, so neither can be read under a
  // request it does not answer.
  const [fixesAnswer, setFixesAnswer] = useState<{
    key: string;
    payload: GenesisJobFixes | null;
    error: GenesisError | Error | null;
  } | null>(null);

  const report =
    reportEntry && selected && reportEntry.jobId === selected.id && reportEntry.language === lang
      ? reportEntry.payload
      : null;

  // Poll the job named by the URL. A tick that never once resolved for this
  // id names it not-found and stops (see header comment); once a job HAS
  // shown here, a later poll failure keeps the last-known record on screen
  // and retries, rather than flashing the page to an empty state over one
  // dropped request.
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let haveJob = false;

    const tick = async () => {
      try {
        const record = await fetchGenesisJob(jobId);
        if (cancelled) return;
        haveJob = true;
        setSelectedEntry({ id: jobId, record });
        setNotFoundId(null);
        setPollError(null);
        timer = setTimeout(
          () => void tick(),
          isTerminal(record.status) ? TERMINAL_RECHECK_MS : POLL_INTERVAL_MS,
        );
      } catch (caught) {
        if (cancelled) return;
        if (!haveJob) {
          setNotFoundId(jobId);
          return;
        }
        setPollError(caught instanceof Error ? caught.message : String(caught));
        timer = setTimeout(() => void tick(), TERMINAL_RECHECK_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  // The gate report -- read from the STORED verdict endpoint, one fetch per
  // (job, verdict, language). `checked_at` is in the key because a re-gate
  // (live-check evidence) changes the verdict without changing the job id.
  const gatedJobId = selected?.gate ? selected.id : null;
  const gateCheckedAt = selected?.gate?.checked_at ?? null;
  // The fix strip's request key, and the render-time read of it. A key that
  // does not match is "not this job's answer" -- which is also how the
  // previous job's "수정 제안을 불러오지 못했습니다" stops being on screen. It
  // used to be cleared by the fetch effect instead, one render after the new
  // job's id had already arrived, so the strip carried the old job's failure
  // for that frame.
  const fixesKey = gatedJobId ? `${gatedJobId}|${gateCheckedAt ?? ""}` : "";
  const fixesCurrent = fixesAnswer && fixesAnswer.key === fixesKey ? fixesAnswer : null;
  const fixes = gatedJobId ? fixesCurrent?.payload ?? null : null;
  const fixesError = gatedJobId ? fixesCurrent?.error ?? null : null;
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

  // The fix strip's own data -- one fetch per (job, verdict), WITHOUT
  // `language` (the backend's rationale already comes back trilingual).
  // Deliberately a separate effect: `GET .../fixes` is not open to an
  // anonymous caller the way `GET .../gate` is, so the two fail independently.
  useEffect(() => {
    if (!gatedJobId) return;
    let cancelled = false;
    fetchGenesisJobFixes(gatedJobId)
      .then((payload) => {
        if (!cancelled) setFixesAnswer({ key: fixesKey, payload, error: null });
      })
      .catch((caught) => {
        if (!cancelled) {
          setFixesAnswer({
            key: fixesKey,
            payload: null,
            error: caught instanceof Error ? caught : new Error(String(caught)),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gatedJobId, gateCheckedAt, fixesKey]);

  // A live check re-gates THIS job from another job's worker thread, so the
  // verdict on screen can change without anything on this page having asked
  // for it. `refreshJob` is the "ask now" the poll's terminal cadence (15 s)
  // would otherwise make a reader wait for; the gate report itself then
  // re-fetches on its own, because its key includes `gate.checked_at`.
  const refreshJob = useCallback(() => {
    if (!jobId) return;
    fetchGenesisJob(jobId)
      .then((record) => setSelectedEntry({ id: jobId, record }))
      .catch(() => {
        /* the poll above owns error reporting; a missed refresh is retried there */
      });
  }, [jobId]);

  // Unit #41 -- THE MISSING WIRE. The map has owned a selection since U-19,
  // but only its own clicks could set it; the verdict rail beside it had no
  // way in, which is why clicking a finding did nothing at all. These three
  // handlers are that way in. Each bumps a sequence number rather than
  // replacing a controlled value, because the map still owns the state (see
  // `GenesisMapSelectionRequest`) and because clicking the SAME row twice must
  // re-centre rather than no-op.
  //
  // A different job is a different network: both values below are therefore
  // stored WITH the job they were minted against and read back only while that
  // is still the job on screen (the map clears its own selection on the same
  // signal, see its reset effect). As an effect that cleared them, the request
  // and the placeable set belonging to the PREVIOUS job survived into the
  // first render of the next one -- a rail row highlighted as placeable
  // against a census that had already been replaced.
  const [mapRequestState, setMapRequestState] = useState<
    { jobId: string; request: GenesisMapSelectionRequest } | null
  >(null);
  const [placeableState, setPlaceableState] = useState<
    { jobId: string; ids: ReadonlySet<string> } | null
  >(null);
  const mapRequest = mapRequestState && mapRequestState.jobId === jobId ? mapRequestState.request : null;
  const placeable = placeableState && placeableState.jobId === jobId ? placeableState.ids : EMPTY_PLACEABLE;
  const requestSelection = useCallback(
    (item: SelectedItem) => {
      // The sequence number still only ever counts up WITHIN one job, which is
      // what makes clicking the same row twice re-centre instead of no-op.
      setMapRequestState((current) => ({
        jobId,
        request: { seq: (current?.jobId === jobId ? current.request.seq : 0) + 1, item },
      }));
    },
    [jobId],
  );
  // Wrapped rather than handed `setPlaceableState` directly so the set can be
  // tagged with its job -- and it keeps the identity check the raw setter got
  // for free, so re-publishing the SAME set does not re-render.
  const handlePlaceableChange = useCallback(
    (ids: ReadonlySet<string>) => {
      setPlaceableState((current) =>
        current && current.jobId === jobId && current.ids === ids ? current : { jobId, ids },
      );
    },
    [jobId],
  );
  // Unit #48-b -- the ring THIS generation was extracted with, published up out
  // of the map (the one place the census it lives in is fetched). Tagged with
  // its job for exactly the reason the placeable set is: a different job is a
  // different region, and the PREVIOUS one surviving into the first render of
  // the next would offer to edit an area this generation never asked for.
  const [requestedPolygonState, setRequestedPolygonState] = useState<
    { jobId: string; polygon: Array<[number, number]> | null } | null
  >(null);
  const requestedPolygon =
    requestedPolygonState && requestedPolygonState.jobId === jobId
      ? requestedPolygonState.polygon
      : null;
  const handleRequestedPolygonChange = useCallback(
    (polygon: Array<[number, number]> | null) => {
      setRequestedPolygonState((current) =>
        current && current.jobId === jobId && current.polygon === polygon
          ? current
          : { jobId, polygon },
      );
    },
    [jobId],
  );
  // The hints the 영역 편집 hand-off carries: derived from the SAME fixes payload
  // the cards are rendered from, so the region page can never name a finding
  // this screen does not show.
  const boundaryHints = useMemo(() => boundaryHintsOf(fixes), [fixes]);
  const focusFinding = useCallback(
    (ref: FindingRef | null, point?: { lat: number; lon: number; label: string }) =>
      requestSelection({ kind: "finding", refs: ref ? [ref] : [], point }),
    [requestSelection],
  );
  // An element asked for by id only -- the map re-resolves its name and its
  // findings from the census it already holds (see its own request effect), so
  // the blanks here are filled there rather than guessed at.
  const focusElement = useCallback(
    (kind: "link" | "connector", id: number) =>
      requestSelection({ kind: "element", elementKind: kind, id, name: "", refs: [] }),
    [requestSelection],
  );

  // On a 202 from POST .../fixes/apply: the sibling job now HAS its own URL,
  // so approving a fix navigates there instead of re-pointing an in-page
  // selection at it -- the lineage line the sibling's own top bar renders is
  // what makes that navigation legible as "the treated version of this one".
  const handleFixApplied = useCallback(
    (appliedJob: GenesisJob) => {
      router.push(`/genesis/jobs/${appliedJob.id}`);
    },
    [router],
  );

  // UNIT #20's help drawer is RETIRED (2026-08-14). 판정 게이트/잠정 등록/지도
  // have now moved a third time and stopped moving: they are verbatim in
  // lib/help-content.tsx (`jobHelpEntries`) and render at /docs, opened by the
  // book icon in the app topbar. Their journey (inline ⓘ on the hub → the hub's
  // drawer → this route's drawer → /docs) is exactly why they ended up in one
  // file: doctrine that follows a control around gets rewritten every time it
  // moves, and these sentences are the product's own claims.

  const backLink = (
    <Link
      href="/genesis"
      className="ui-button-ghost inline-flex w-fit items-center gap-1 rounded-md px-2 py-1 text-[15px]"
    >
      ← {t("생성 이력", "Generations", "生成履歴")}
    </Link>
  );

  // UNKNOWN/FOREIGN ID -- named empty state, never a crash.
  if (notFound) {
    return (
      <div className="mx-auto max-w-[1500px] px-4 py-8">
        {backLink}
        <div className="mt-6 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
          <p className="text-[15px] text-slate-600">
            {t(
              "이 생성을 찾을 수 없습니다 — 삭제되었거나 다른 프로젝트의 생성일 수 있습니다.",
              "This generation could not be found — it may have been deleted, or it belongs to a different project.",
              "この生成が見つかりません — 削除されたか、別のプロジェクトの生成である可能性があります。",
            )}
          </p>
        </div>
      </div>
    );
  }

  // COLD-LOAD LOADING STATE -- the first tick has not resolved either way yet.
  if (!selected) {
    return (
      <div className="mx-auto max-w-[1500px] px-4 py-8">
        {backLink}
        <p className="mt-6 text-[15px] text-slate-500">{t("불러오는 중…", "Loading…", "読み込み中…")}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1500px] px-4 py-8">
      {backLink}

      {/* TOP BAR -- name + verdict chip + 판정 stat counts + mono sha line +
        * lineage (fix jobs only) + artifacts, compacted to the right. Nothing
        * here re-spells the title (#13's de-duplication rule stands). */}
      <div className="mt-4 flex flex-col gap-4 border-b border-slate-200 pb-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-[20px] font-semibold text-slate-900">{selected.name}</h1>
            <span
              className={`shrink-0 whitespace-nowrap rounded-full border px-2 py-0.5 text-[14px] font-semibold ${statusClasses(selected.status)}`}
            >
              {statusLabel(selected.status, t)}
            </span>
          </div>
          {/* The date·scale meta and the sha line left the screen (user, live
            * pass 2026-08-14) -- both stay in gate.json and the compliance
            * report; the history table still carries date and scale. */}
          {/* `source_osm` is NULL on a job with no extract of its own -- a fix
            * job and a live-check job both re-use the parent's artifact bytes.
            * Reading `.filename` off it unguarded crashed this header for those
            * two job types (found while adding the second one, unit #33). */}
          {selected.source_osm && selected.source_osm.filename !== `${selected.name}.osm` && (
            <p className="mt-1 truncate font-mono text-[14px] text-slate-500">
              {selected.source_osm.filename}
            </p>
          )}
          {selected.derived_from &&
            ("kind" in selected.derived_from ? (
              <p className="mt-1 truncate font-mono text-[14px] text-slate-500">
                {/* Refuter finding F3 (2026-08-26): EVERY kind-bearing lineage
                  * used to render here as a live check -- this branch only
                  * told a fix lineage (no `kind` at all) apart from the rest,
                  * and never checked WHICH kind the rest actually was.
                  * `lineageKindLabel` narrows on the VALUE, so a regenerate,
                  * a dead-end exclusion and a signals-regeneration each say
                  * what they are. */}
                {t("파생", "Derived from", "派生")} — {selected.derived_from.job_id} ·{" "}
                {lineageKindLabel(selected.derived_from.kind, t)} ·{" "}
                {selected.derived_from.requested_by ?? "—"}
              </p>
            ) : (
              <p className="mt-1 truncate font-mono text-[14px] text-slate-500">
                {t("파생", "Derived from", "派生")} — {selected.derived_from.job_id} ·{" "}
                {selected.derived_from.finding_code} · {selected.derived_from.approved_by ?? "—"}
              </p>
            ))}
          {report && (
            <div className="mt-3 flex flex-wrap gap-x-8 gap-y-3">
              <Stat label={t("통과", "pass", "合格")} value={report.counts.pass} tone="text-emerald-800" />
              <Stat
                label={t("실패", "fail", "不合格")}
                value={report.counts.fail}
                tone={report.counts.fail > 0 ? "text-rose-800" : undefined}
              />
              <Stat
                label={t("확인 불가", "not checkable", "確認不可")}
                value={report.counts.not_checkable}
                tone={
                  report.counts.not_checkable > 0
                    ? "text-slate-900 underline decoration-dotted decoration-slate-400 underline-offset-4"
                    : undefined
                }
              />
            </div>
          )}
          {pollError && (
            <p className="mt-2 text-[14px] text-slate-500">
              {t(
                "최신 상태를 다시 불러오지 못했습니다 — 마지막으로 확인된 상태를 보여줍니다.",
                "Could not refresh this job's status — showing the last state that loaded.",
                "最新の状態を再取得できませんでした — 最後に確認できた状態を表示しています。",
              )}
            </p>
          )}
        </div>

        {selected.artifact && (
          <div className="shrink-0 lg:text-right">
            <p className="text-[14px] text-slate-500">{t("아티팩트", "Artifacts", "アーティファクト")}</p>
            <div className="mt-1 flex flex-wrap gap-2 lg:justify-end">
              <ArtifactRow jobId={selected.id} t={t} />
            </div>
          </div>
        )}
      </div>

      {/* 판정 -- the merged verdict headline, progress/provisional/refused/
        * revocation/refusal paragraphs, and the live-check attestation. The
        * V1–V9 GRID itself moves into the right rail below, beside the split. */}
      <section className="mt-6">
        <h2 className="flex items-center text-[15px] font-semibold text-slate-900">
          {t("판정", "Verdict", "判定")}
        </h2>

        <div className="mt-3">
          <p className="text-[15px] font-semibold text-slate-900">
            {report ? verdictLabel(report.verdict, t) : statusLabel(selected.status, t)}
          </p>

          {!isTerminal(selected.status) && (
            <p className="mt-2 text-[15px] text-slate-600">
              {t("진행", "Phase", "進行")}: {selected.phase} ({Math.round(selected.progress * 100)}%)
            </p>
          )}

          {selected.status === "provisional" && (
            <p className="mt-2 text-[15px] leading-relaxed text-slate-600">
              {t(
                "등록되었지만 잠정입니다 — V1은 라이브 검사 증거를 기다립니다.",
                "Registered, but provisional — V1 is waiting for live-check evidence.",
                "登録されましたが暫定です — V1 はライブ検査の証拠を待っています。",
              )}
            </p>
          )}
          {/* The refused-consequence paragraph left the screen (user, live
            * pass 2026-08-14) -- the verdict headline and chip already say
            * refused; the not-registered consequence reads in the 판정 게이트
            * help entry below. */}
          {selected.revocation && (
            <p className="mt-2 text-[15px] leading-relaxed text-slate-600">
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
            <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[15px] text-rose-800">
              <p className="font-semibold">
                {refusalHeadline(selected.refusal.code, t, generationRefusalFallback(t))}
              </p>
              <ul className="mt-1 space-y-1">
                {selected.refusal.reasons.map((reason, index) => (
                  <li key={index} className="font-mono text-[14px] leading-relaxed">
                    {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {selected.gate?.live_evidence && (
          <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3 text-[15px] text-slate-700">
            {/* TWO BASES, TWO SENTENCES (unit #33). An attestation's state.json
              * arrives over HTTP, so this app cannot establish that Vissim ran —
              * it can only record who says it did. A live-check JOB's record was
              * written by this app, from what LoadNet returned. Both are named
              * for what they are, and neither borrows the other's word. */}
            <p className="font-semibold text-slate-800">
              {selected.gate.live_evidence.basis === "app_observed"
                ? t(
                    "라이브 검사 (이 앱이 직접 실행)",
                    "Live check (run by this app)",
                    "ライブ検査（このアプリが実行）",
                  )
                : t(
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
                {selected.gate.live_evidence.basis === "app_observed"
                  ? t("실행 요청", "Requested by", "実行依頼")
                  : t("증언자", "Attested by", "申告者")}
                : <span className="font-semibold">{selected.gate.live_evidence.attested_by}</span>
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
            {selected.gate.live_evidence.basis === "app_observed" && (
              <p className="mt-2 rounded border-2 border-slate-400 bg-slate-100 px-2 py-1 text-slate-900">
                {t(
                  "이 증거는 이 앱이 작업 큐에서 직접 실행한 로딩의 기록입니다 — 사람의 진술이 아닙니다. 다만 기록 자체는 이 프로젝트 폴더 안의 파일이며, 그 이상의 증명은 아닙니다.",
                  "This evidence is this app's own record of a load it ran on its job queue — not a person's statement. The record is still a file inside this project's folder, and nothing stronger is claimed.",
                  "この証拠は、このアプリがジョブキューで自ら実行した読み込みの記録です — 人の申告ではありません。ただし記録自体はこのプロジェクトのフォルダ内のファイルであり、それ以上の証明ではありません。",
                )}
              </p>
            )}
            {selected.gate.regate?.job_id && (
              <p className="mt-2 font-mono text-[14px] text-slate-500">
                {t("재판정", "Re-gated by", "再判定")}: {selected.gate.regate.job_id}
                {selected.gate.regate.previous_gate_archived &&
                  ` · ${t("이전 판정", "previous verdict", "以前の判定")}: ${selected.gate.regate.previous_gate_archived}`}
              </p>
            )}
          </div>
        )}

        {reportError && (
          <div className="mt-4 rounded-lg border-2 border-slate-400 bg-slate-100 px-3 py-2 text-[15px] text-slate-900">
            {t("게이트 결과를 불러오지 못했습니다", "Failed to load the gate result", "ゲート結果を読み込めませんでした")}
            : {reportError}
          </div>
        )}
      </section>

      {/* THE 65/35 SPLIT (mockup option D, user decision 2026-08-14): map
        * LEFT wide, V1–V9 grid + selected finding + fix card RIGHT narrow --
        * inverted from the old drawer's map-under-the-grid stack, so a click
        * on a finding and the map location it names stay on screen together. */}
      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-[65fr_35fr] xl:items-start mb-8">
        <div className="min-w-0">
          {report ? (
            <GenesisTileMap jobId={selected.id} report={report} hasArtifact={Boolean(selected.artifact)} selectionRequest={mapRequest} onPlaceableChange={handlePlaceableChange} onRequestedPolygonChange={handleRequestedPolygonChange} />
          ) : (
            <div className="rounded-xl border border-slate-200 bg-slate-50 px-6 py-10 text-center">
              <p className="text-[15px] text-slate-500">
                {t(
                  "아직 판정이 없어 지도를 표시할 수 없습니다.",
                  "No verdict yet, so the map cannot be shown.",
                  "まだ判定がないため、地図を表示できません。",
                )}
              </p>
            </div>
          )}
        </div>

        <div className="min-w-0">
          {/* UNIT #48-b -- what stands where unit #48's composite proposal and
            * its named refusal used to (user decision, 2026-08-20).
            *
            * BOTH of those are gone: the OFFER ("전부 한 번에 넓혀 다시 생성"),
            * because widening the ring for every boundary finding at once still
            * cut new roads at the new edge and the user watched it fail on their
            * own network; and the REFUSAL prose ("경계 소견을 한 번에 덮는
            * 영역은 제안하지 않습니다"), because a named reason for withholding
            * an option nobody is offered any more is noise. The user's word for
            * the refusal was "이건 지워".
            *
            * WHAT SURVIVES IS THE MEASUREMENT: every boundary-attributed finding
            * still says so on its own card, in the backend's own sentences with
            * the way ids and the cut distances. The button below acts on it by
            * handing this job's OWN region to the screen that owns the extract.
            *
            * It sits BESIDE the V1-V9 grid rather than inside it, for the reason
            * the composite did: it is not one check's answer -- it is about
            * findings across V3 and V4 at once -- and `GateCheckGrid`'s markup is
            * pinned to carry no tint of its own.
            *
            * It renders only once the census has actually reported a requested
            * ring (published up out of the map, which is where that census is
            * fetched). A hand-uploaded .osm and a bbox fetch have no ring, and
            * there is nothing to edit for them. */}
          {requestedPolygon && (
            <RegionEditInvitation
              jobId={selected.id}
              polygon={requestedPolygon}
              hints={boundaryHints}
              t={t}
            />
          )}
          {report ? (
            <GateCheckGrid
              key={`${selected.id}-${gateCheckedAt ?? ""}`}
              checks={report.checks}
              t={t}
              lang={lang}
              jobId={selected.id}
              liveCheckRefusal={liveCheckRefusalCode(selected)}
              onLiveCheckCompleted={refreshJob}
              fixes={fixes}
              fixesError={fixesError}
              onFixApplied={handleFixApplied}
              placeable={placeable}
              onFocusFinding={focusFinding}
              onFocusElement={focusElement}
            />
          ) : (
            <p className="text-[15px] text-slate-500">
              {t("아직 판정이 없습니다.", "No verdict yet.", "まだ判定がありません。")}
            </p>
          )}
        </div>
      </div>

      {/* 생성 비교 -- full width under the split; see DrawerSection's own
        * comment for why it does not try to fit the 35% rail. */}
      <DrawerSection title={t("생성 비교", "Compare generations", "生成の比較")}>
        <GenesisComparisonPanel
          jobs={jobs}
          currentJob={selected}
          currentReport={report}
          currentReportError={reportError}
        />
      </DrawerSection>
    </div>
  );
}
