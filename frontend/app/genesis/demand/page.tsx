"use client";

// Roadmap v2 §5.B — demand side-input UI: the browser-clickable path from a
// Daejeon counts workbook to a `demand` token `startGenesisJob` can take.
//
// Before this page, attaching demand to a generation meant hand-writing the
// `genesis.demand.v1` JSON (counts from `DaejeonCountAdapter` + a hand-made
// `approach_links` site→leg→link map) and staging it with PowerShell. Three
// steps, each backed by its own server route so nothing here re-implements
// validation the backend already owns:
//
//   1. Upload ONE counts workbook -- POST /api/genesis/demand-inputs/parse
//      runs DaejeonCountAdapter and returns sites, legs (verbatim), classes,
//      window and every issue the adapter reported, unsoftened.
//   2. Type the site→leg→link join -- a plain table, one row per leg. A link
//      id is never guessed here: an empty cell stays empty, and the server
//      refuses an incomplete mapping with its own reason
//      (services.genesis.demand.check_approach_link_sites, the same function
//      generate_demand itself calls).
//   3. 만들기 -- POST /api/genesis/demand-inputs/build assembles the
//      genesis.demand.v1 document server-side and stages it through the SAME
//      side_inputs.parse_side_input the raw POST /side-inputs route uses.
//
// Chrome strings use the three-argument t() form (explicit ja) so nothing here
// depends on an entry landing in the generated lib/i18n-ja.ts dictionary.
//
// U-1 POLISH (2026-08-13): the Console density pass and THE TINTED-BOX RULE
// stated in full above `statusClasses` in app/genesis/region/page.tsx — a tinted
// box carries its tint in the BACKGROUND and the BORDER, and its text in the
// neutral ink ramp, because globals.css's dark remap covers only part of each
// Tailwind family and an uncovered `text-*` shade (here it was
// `text-emerald-900`, stock #064e3b, on a `bg-emerald-50` remapped to #1e2b22)
// renders dark-on-dark. Type floor is 12px on this screen: nothing below it
// survives.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { Panel, refusalHeadline } from "@/components/genesis/ui";
import {
  GenesisError,
  buildDemandInput,
  fetchGenesisJobs,
  parseDemandCounts,
  type DemandCountsIssue,
  type GenesisJob,
  type StagedDemandCounts,
  type StagedDemandInput,
} from "@/lib/api";
import { formatTimestamp, statusLabel } from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

/** site id → leg id → the link id text the reviewer typed. A leg absent from
 * this map, or present with an empty string, is left OUT of the payload --
 * never filled with a guess. */
type MappingState = Record<string, Record<string, string>>;

/** A link id is a positive integer, and NOTHING else -- `Number.parseInt`
 * truncates at the first non-digit ("1e5" -> 1, "12.5" -> 12) while the cell
 * keeps showing the typed text, so a different, plausible link id gets built
 * with zero warning. Every cell is checked against this before 만들기 will
 * run; anything that fails it is a visible per-cell error, never a silent
 * coercion. */
const LINK_ID_PATTERN = /^\d+$/;

function isValidLinkIdCell(raw: string): boolean {
  const trimmed = raw.trim();
  return trimmed === "" || LINK_ID_PATTERN.test(trimmed);
}

/** Tinted-box rule + three-colour discipline, both stated in full above
 * `statusClasses` in app/genesis/region/page.tsx: the fill and the border carry
 * the severity, the ink ramp carries the words, and there are exactly three
 * accent hues (rose = refuse, emerald = pass, sky = selection/info). A WARN is
 * none of those, so it goes MONO — a heavier `border-slate-400` on a neutral
 * fill, with the word 경고 printed on it as before. The word is what says
 * "warning"; the colour never was, since globals.css already remapped the amber
 * family to white-alphas and the ivory ramp. */
function issueClasses(severity: DemandCountsIssue["severity"]): string {
  if (severity === "refuse") return "border-rose-300 bg-rose-50 text-slate-800";
  if (severity === "warn") return "border-slate-400 bg-slate-100 text-slate-800";
  return "border-slate-200 bg-slate-50 text-slate-600";
}

function issueWord(severity: DemandCountsIssue["severity"], t: Translate): string {
  if (severity === "refuse") return t("거부", "REFUSE", "拒否");
  if (severity === "warn") return t("경고", "WARN", "警告");
  return t("정보", "INFO", "情報");
}

// `refusalHeadline` (ko/en/ja for the backend's stable refusal codes, this
// page's `genesis_counts_*`/`genesis_demand_*` ones included) moved to
// components/genesis/ui.tsx together with the other three genesis screens'
// partial copies. Its rule is unchanged and restated there: the reasons array is
// still shown verbatim underneath, because rewriting a backend refusal
// client-side is how a UI starts lying.

export default function DemandInputPage() {
  const { t, lang } = useLanguage();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<GenesisError | Error | null>(null);
  const [staged, setStaged] = useState<StagedDemandCounts | null>(null);

  const [mapping, setMapping] = useState<MappingState>({});
  const [windowStart, setWindowStart] = useState("");
  const [windowEnd, setWindowEnd] = useState("");
  const [reduction, setReduction] = useState<"peak" | "mean" | "">("peak");

  // The generation whose link ids the reviewer is reading off -- REQUIRED
  // before 만들기, because a link id means nothing without the generation
  // that assigned it (draft.py resets the id counter on every build).
  const [jobs, setJobs] = useState<GenesisJob[]>([]);
  const [builtAgainstJob, setBuiltAgainstJob] = useState("");

  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<GenesisError | Error | null>(null);
  const [built, setBuilt] = useState<StagedDemandInput | null>(null);

  useEffect(() => {
    // Deferred for the react-hooks `set-state-in-effect` rule, the same
    // pattern the phases review page and the dashboard page use.
    void Promise.resolve().then(async () => {
      try {
        setJobs(await fetchGenesisJobs());
      } catch {
        setJobs([]);
      }
    });
  }, []);

  const upload = useCallback(async () => {
    if (!file) return;
    setParsing(true);
    setParseError(null);
    try {
      const result = await parseDemandCounts(file);
      setStaged(result);
      setBuilt(null);
      setBuildError(null);
      // Every leg starts EMPTY -- a link id is never guessed, so the mapping
      // table's initial state is the same shape the server refuses if it is
      // sent unchanged.
      const initial: MappingState = {};
      for (const site of result.summary.sites) {
        initial[site.site_id] = Object.fromEntries(site.legs.map((leg) => [leg, ""]));
      }
      setMapping(initial);
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (caught) {
      setParseError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setParsing(false);
    }
  }, [file]);

  const setCell = useCallback((siteId: string, leg: string, value: string) => {
    setMapping((current) => ({
      ...current,
      [siteId]: { ...(current[siteId] ?? {}), [leg]: value },
    }));
  }, []);

  const build = useCallback(async () => {
    if (!staged || !builtAgainstJob) return;
    // Belt-and-suspenders: the button is already disabled while an invalid
    // cell exists, but `build` refuses too rather than trusting the caller.
    const hasInvalidCell = Object.values(mapping).some((legs) =>
      Object.values(legs).some((value) => !isValidLinkIdCell(value)),
    );
    if (hasInvalidCell) return;
    setBuilding(true);
    setBuildError(null);
    try {
      const approachLinks: Record<string, Record<string, number>> = {};
      for (const [siteId, legs] of Object.entries(mapping)) {
        const entries: Record<string, number> = {};
        for (const [leg, raw] of Object.entries(legs)) {
          const trimmed = raw.trim();
          if (!trimmed) continue; // empty stays empty -- never a guessed link id
          // A link id is a positive integer and nothing else -- no
          // Number.parseInt here, since it truncates at the first
          // non-digit ("1e5" -> 1) while the cell keeps showing the typed
          // text. isValidLinkIdCell (checked above and per-cell on screen)
          // is the only gate; this is now a plain, lossless conversion.
          entries[leg] = Number(trimmed);
        }
        if (Object.keys(entries).length > 0) approachLinks[siteId] = entries;
      }

      const demandSettings: Record<string, unknown> = {};
      if (reduction) demandSettings.single_interval_reduction = reduction;

      const payload: Parameters<typeof buildDemandInput>[0] = {
        counts_token: staged.token,
        approach_links: approachLinks,
        built_against_job: builtAgainstJob,
      };
      if (windowStart && windowEnd) {
        payload.window_start = windowStart;
        payload.window_end = windowEnd;
      }
      if (Object.keys(demandSettings).length > 0) payload.demand_settings = demandSettings;

      const result = await buildDemandInput(payload);
      setBuilt(result);
    } catch (caught) {
      setBuilt(null);
      setBuildError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setBuilding(false);
    }
  }, [staged, mapping, windowStart, windowEnd, reduction, builtAgainstJob]);

  const mappedApproachCount = Object.values(mapping).reduce(
    (total, legs) => total + Object.values(legs).filter((value) => value.trim() !== "").length,
    0,
  );
  const invalidCellCount = Object.values(mapping).reduce(
    (total, legs) => total + Object.values(legs).filter((value) => !isValidLinkIdCell(value)).length,
    0,
  );

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      {/* One page-title size across the three genesis sub-screens (the region
        * page's 22px), body 13, secondary 12 muted. Before this, this h1 was
        * 18px and its subtitle 12px, so the whole screen lived in one narrow
        * band with no middle step. `text-sky-700` on the back link was the same
        * remap fall-through the tinted boxes had: sky-700 is not in the dark
        * remap, so it rendered as stock #0369a1 on the near-black page. */}
      <header className="mb-7">
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">
          {t("수요 입력 만들기", "Build a demand input", "需要入力の作成")}
        </h1>
        <p className="mt-1.5 text-[15px] leading-relaxed text-slate-600">
          {t(
            "대전 스마트교차로 카운트 워크북을 업로드하고, 접근로를 초안 링크 ID에 연결하면 생성에 붙일 수요 입력이 만들어집니다.",
            "Upload a Daejeon smart-intersection counts workbook, bind each approach to a draft link id, and a demand input this generation can attach is built.",
            "大田スマート交差点のカウントワークブックをアップロードし、流入路を草案リンクIDに対応付けると、生成に付ける需要入力が作成されます。",
          )}
        </p>
        <Link href="/genesis" className="mt-2 inline-block text-[14px] font-semibold text-slate-500 underline">
          {t("← 네트워크 생성으로", "← Back to generation", "← ネットワーク生成へ")}
        </Link>
      </header>

      <Panel
        density="console"
        title={t("1. 카운트 워크북 업로드", "1. Upload the counts workbook", "1. カウントワークブックのアップロード")}
        subtitle={t(
          "대전 스마트교차로 15분 집계 .xlsx 파일 하나. 대전 어댑터 전용이며, WA SCATS 등 다른 형식은 아직 이 화면을 지원하지 않습니다.",
          "One 대전 스마트교차로 15-minute .xlsx export. Daejeon-adapter only -- other formats (e.g. WA SCATS) are not wired into this screen yet.",
          "大田スマート交差点の15分集計 .xlsx ファイル一つ。大田アダプター専用で、WA SCATS など他形式は本画面ではまだ未対応です。",
        )}
      >
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xlsm"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="block text-[15px] text-slate-700 file:mr-3 file:rounded-md file:border file:border-slate-300 file:bg-white/[0.06] file:px-3 file:py-1.5 file:text-[14px] file:font-semibold file:text-slate-700"
          />
          <button
            type="button"
            disabled={!file || parsing}
            onClick={() => void upload()}
            className="rounded-md bg-slate-900 px-4 py-2 text-[15px] font-semibold text-ink disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {parsing ? t("읽는 중…", "Reading…", "読み込み中…") : t("업로드", "Upload", "アップロード")}
          </button>
        </div>

        {parseError && (
          <div className="mt-5 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] text-slate-800">
            <p className="font-semibold">
              {parseError instanceof GenesisError
                ? refusalHeadline(parseError.code, t)
                : t("업로드하지 못했습니다", "Upload failed", "アップロードできませんでした")}
            </p>
            {parseError instanceof GenesisError && parseError.reasons.length > 0 ? (
              <ul className="mt-1.5 space-y-0.5">
                {parseError.reasons.map((reason, index) => (
                  <li key={index} className="font-mono text-[14px] leading-relaxed text-slate-700">
                    {reason}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1.5 whitespace-pre-wrap font-mono text-[14px] leading-relaxed text-slate-700">
                {parseError.message}
              </p>
            )}
          </div>
        )}

        {staged && (
          <div className="mt-5 space-y-3">
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-[14px] leading-relaxed text-slate-700">
              <p>
                {t("어댑터", "adapter", "アダプター")}: {staged.summary.adapter} ({staged.summary.region}) ·{" "}
                {t("레코드", "records", "レコード")} {staged.summary.counts}건 ·{" "}
                {t("파일", "file", "ファイル")} {staged.filename}
              </p>
              {staged.summary.window && (
                <p className="mt-1">
                  {t("구간", "window", "区間")}: {staged.summary.window.start} → {staged.summary.window.end}
                </p>
              )}
              <p className="mt-1">
                {t("차종", "vehicle classes", "車種")}: {staged.summary.vehicle_classes.join(", ") || "—"}
              </p>
            </div>

            {staged.summary.issues.length > 0 && (
              <ul className="space-y-1.5">
                {staged.summary.issues.map((issue, index) => (
                  <li
                    key={`${issue.code}-${index}`}
                    className={`rounded-md border px-3 py-2 text-[14px] leading-relaxed ${issueClasses(issue.severity)}`}
                  >
                    <span className="mr-1 font-bold">{issueWord(issue.severity, t)}</span>
                    <span className="font-mono">{issue.code}</span> — {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Panel>

      {staged && (
        <Panel
          density="console"
          title={t("2. 접근로 → 링크 매핑", "2. Bind each approach to a link", "2. 流入路 → リンクの対応付け")}
          subtitle={t(
            "링크 ID는 추측하지 않습니다 -- 비워두면 그대로 비어 있고, 매핑되지 않은 접근로가 있는 지점은 서버가 거부합니다. 링크 ID는 지오메트리만으로 만든 이전 생성의 게이트 지도(네트워크 생성 → 게이트 판정 → 지도)에서 확인할 수 있습니다.",
            "A link id is never guessed -- an empty cell stays empty, and a site with an unmapped approach is refused by the server. Link ids are readable from a previous GEOMETRY-ONLY generation's gate map (Network Genesis → gate verdict → map).",
            "リンクIDは推測しません -- 空欄はそのまま空欄となり、対応付けのない流入路がある地点はサーバーが拒否します。リンクIDは、以前のジオメトリのみの生成のゲート地図（ネットワーク生成 → ゲート判定 → 地図）で確認できます。",
          )}
        >
          <div className="mb-6">
            <label className="text-[15px] text-slate-700">
              <span className="block font-semibold">
                {t(
                  "링크 ID를 읽은 생성 (필수)",
                  "Generation the link ids were read from (required)",
                  "リンクIDを読み取った生成（必須）",
                )}
              </span>
              <select
                value={builtAgainstJob}
                onChange={(event) => setBuiltAgainstJob(event.target.value)}
                className="mt-1.5 w-full max-w-md rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
              >
                <option value="">{t("선택", "select", "選択")}</option>
                {jobs.map((job) => (
                  <option key={job.id} value={job.id}>
                    {job.name} · {statusLabel(job.status, t)} · {formatTimestamp(job.created_at, lang)}
                  </option>
                ))}
              </select>
            </label>
            <p className="mt-2 text-[14px] leading-relaxed text-slate-500">
              {t(
                "링크 ID는 이 생성이 매긴 번호입니다. 재생성마다(bbox 변경 등) 번호가 바뀔 수 있어 서버가 이 생성의 번호와 대조합니다 -- 선택 없이는 만들 수 없습니다.",
                "Link ids are numbers THIS generation assigned. A rebuild (a bbox change, a re-extract) can renumber them, so the server checks the mapping against this generation's own numbers -- required before 만들기.",
                "リンクIDはこの生成が振った番号です。再生成（bbox変更など）で番号が変わることがあるため、サーバーはこの生成の番号と照合します -- 選択なしでは作成できません。",
              )}
            </p>
          </div>

          {staged.summary.sites.length === 0 ? (
            <p className="text-[15px] text-slate-500">
              {t(
                "워크북에서 지점을 하나도 읽지 못했습니다.",
                "No site was read from the workbook.",
                "ワークブックから地点を一つも読み取れませんでした。",
              )}
            </p>
          ) : (
            <div className="space-y-6">
              {staged.summary.sites.map((site) => (
                /* One layer, not two: the site's rows are separated by their own
                  * hairlines and a heading, so the bordered card each site used
                  * to sit in was a box inside a box inside a panel. */
                <div key={site.site_id}>
                  <p className="text-[15px] font-semibold text-slate-900">
                    {site.site_name ?? site.site_id}
                    {site.site_name && site.site_name !== site.site_id ? ` (${site.site_id})` : ""}
                  </p>
                  <table className="mt-2 w-full text-[14px]">
                    <thead>
                      <tr className="text-left text-slate-500">
                        <th className="py-1">{t("접근로 (원문)", "approach (verbatim)", "流入路（原文）")}</th>
                        <th className="py-1">{t("링크 ID", "link id", "リンクID")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {site.legs.map((leg) => {
                        const raw = mapping[site.site_id]?.[leg] ?? "";
                        const invalid = !isValidLinkIdCell(raw);
                        return (
                          <tr key={leg} className="border-t border-slate-100">
                            <td className="py-1 font-mono">{leg}</td>
                            <td className="py-1">
                              <input
                                type="number"
                                inputMode="numeric"
                                value={raw}
                                onChange={(event) => setCell(site.site_id, leg, event.target.value)}
                                placeholder={t("비워두면 매핑 안 됨", "empty = unmapped", "空欄=未対応")}
                                aria-invalid={invalid}
                                className={`w-32 rounded border px-2 py-1 text-[14px] ${
                                  invalid
                                    ? "border-rose-400 bg-rose-50 text-slate-800"
                                    : "border-slate-300"
                                }`}
                              />
                              {invalid && (
                                <p className="mt-1 max-w-[9rem] text-[14px] leading-tight text-rose-700">
                                  {t(
                                    "숫자만 입력 -- 만들기 불가",
                                    "digits only -- blocks 만들기",
                                    "数字のみ -- 作成不可",
                                  )}
                                </p>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}

      {staged && (
        <Panel
          density="console"
          title={t("3. 수요 설정 (선택)", "3. Demand settings (optional)", "3. 需要設定（任意）")}
        >
          <div className="flex flex-wrap items-end gap-5">
            <label className="text-[15px] text-slate-700">
              <span className="block font-semibold">
                {t("구간 대푯값", "single-interval reduction", "区間の代表値")}
              </span>
              <select
                value={reduction}
                onChange={(event) => setReduction(event.target.value as "peak" | "mean" | "")}
                className="mt-1.5 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
              >
                <option value="peak">
                  {t("피크 (기본값)", "peak (default)", "ピーク（既定）")}
                </option>
                <option value="mean">{t("평균", "mean", "平均")}</option>
                <option value="">
                  {t("지정 안 함", "unset", "指定なし")}
                </option>
              </select>
              <span className="mt-2 block max-w-sm text-[14px] leading-relaxed text-slate-500">
                {t(
                  "이 값은 측정치가 아니라 이 화면이 대신 내리는 모델링 선택입니다. 구간이 여러 개인 카운트를 하나의 값으로 접을 때 쓰이며, 언제든 바꾸거나 '지정 안 함'으로 비울 수 있습니다 -- 비워둔 채로 구간이 여럿이면 생성이 거부됩니다.",
                  "Not a measurement -- a modelling choice this screen pre-selects on your behalf, used only to collapse a multi-interval count onto one value. Change it or clear it to 'unset' anytime; left unset, a multi-interval count refuses at generation time.",
                  "これは測定値ではなく、本画面が代わりに選ぶモデリング上の選択です。複数区間のカウントを一つの値に畳む際にのみ使われ、いつでも変更または「指定なし」にできます -- 指定なしのまま区間が複数あると生成時に拒否されます。",
                )}
              </span>
            </label>

            <label className="text-[15px] text-slate-700">
              <span className="block font-semibold">{t("분석 구간 시작 (선택)", "window start (optional)", "分析区間開始（任意）")}</span>
              <input
                type="datetime-local"
                value={windowStart}
                onChange={(event) => setWindowStart(event.target.value)}
                className="mt-1.5 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
              />
            </label>
            <label className="text-[15px] text-slate-700">
              <span className="block font-semibold">{t("분석 구간 끝 (선택)", "window end (optional)", "分析区間終了（任意）")}</span>
              <input
                type="datetime-local"
                value={windowEnd}
                onChange={(event) => setWindowEnd(event.target.value)}
                className="mt-1.5 rounded-md border border-slate-300 px-2.5 py-1.5 text-[14px]"
              />
            </label>
          </div>
          <p className="mt-3 text-[14px] leading-relaxed text-slate-500">
            {t(
              "시작과 끝을 함께 입력해야 합니다. 하나만 입력하면 서버가 거부합니다.",
              "Both ends or neither -- entering only one is refused by the server.",
              "始点と終点は両方入力してください。片方のみでは拒否されます。",
            )}
          </p>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={building || !builtAgainstJob || invalidCellCount > 0}
              onClick={() => void build()}
              className="rounded-md bg-sky-700 px-4 py-2 text-[15px] font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {building ? t("만드는 중…", "Building…", "作成中…") : t("만들기", "Build", "作成")}
            </button>
            <span className="text-[14px] text-slate-500">
              {t(
                `현재 ${mappedApproachCount}개 접근로 매핑됨`,
                `${mappedApproachCount} approach(es) mapped so far`,
                `現在 ${mappedApproachCount} 件の流入路が対応付けられています`,
              )}
            </span>
            {!builtAgainstJob && (
              /* MONO: a precondition that is not yet met, not a fault. */
              <span className="text-[14px] font-semibold text-slate-800">
                {t(
                  "2단계에서 생성을 선택해야 만들기가 활성화됩니다",
                  "Pick step 2's generation to enable 만들기",
                  "手順2で生成を選ぶと作成が有効になります",
                )}
              </span>
            )}
            {invalidCellCount > 0 && (
              <span className="text-[14px] font-semibold text-rose-700">
                {t(
                  `잘못된 링크 ID ${invalidCellCount}건 -- 숫자만 입력하세요`,
                  `${invalidCellCount} invalid link id cell(s) -- digits only`,
                  `無効なリンクID ${invalidCellCount}件 -- 数字のみ入力してください`,
                )}
              </span>
            )}
          </div>

          {buildError && (
            <div className="mt-5 rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] text-slate-800">
              <p className="font-semibold">
                {buildError instanceof GenesisError
                  ? refusalHeadline(buildError.code, t)
                  : t("만들지 못했습니다", "Could not build the demand input", "作成できませんでした")}
              </p>
              {buildError instanceof GenesisError && buildError.reasons.length > 0 ? (
                <ul className="mt-1.5 space-y-0.5">
                  {buildError.reasons.map((reason, index) => (
                    <li key={index} className="font-mono text-[14px] leading-relaxed text-slate-700">
                      {reason}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1.5 whitespace-pre-wrap font-mono text-[14px] leading-relaxed text-slate-700">
                  {buildError.message}
                </p>
              )}
            </div>
          )}

          {built && (
            <div className="mt-5 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-[15px] leading-relaxed text-slate-800">
              <p className="font-semibold">
                {t("수요 입력이 만들어졌습니다", "Demand input built", "需要入力を作成しました")}
              </p>
              <p className="mt-1.5 break-all font-mono text-[14px]">
                {t("토큰", "token", "トークン")}: {built.token}
              </p>
              <p className="mt-1.5">
                {t("매핑된 지점", "sites mapped", "対応付けされた地点")}: {built.summary.sites_mapped.join(", ") || "—"} ·{" "}
                {t("매핑된 접근로", "approaches mapped", "対応付けされた流入路")}: {built.summary.approaches_mapped}
              </p>
              <p className="mt-1.5 text-[14px] leading-relaxed text-slate-600">
                {t(
                  "이 토큰을 네트워크 생성 화면의 '수요 입력' 선택 목록에서 고르면 이번 생성에 붙습니다.",
                  "Pick this token from the 'demand input' dropdown on the generation screen to attach it to a generation.",
                  "このトークンをネットワーク生成画面の「需要入力」選択欄で選ぶと、今回の生成に付きます。",
                )}
              </p>
            </div>
          )}
        </Panel>
      )}
    </main>
  );
}
