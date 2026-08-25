"use client";

// ---------------------------------------------------------------------------
// THE OLD WORKBOOK REPORT PATH, MOVED — not rewritten and not deleted (R-5b).
//
// This is app/report/page.tsx as it stood before the Track R rebuild, lifted
// into a component so the new spine/figures/typesetter screen can take the
// page while this surface keeps working. It still serves a real path that the
// typesetter does NOT: `POST /api/report/build`'s block-composed .docx and
// `GET /api/report/data/{block}`'s per-block CSV both read
// services/report_exports.py's workbook functions, which R-2 excluded from the
// new path by name (the stale-workbook GEH source). Two paths, two sources,
// and the new one is not a superset — so this one is DEMOTED (it renders
// inside the page's 기존 내보내기 fold), never removed.
//
// The body below is deliberately unchanged apart from three things: the
// component's name, the page-level heading and outer page padding (the fold
// that now contains it supplies both), and this header. Its own style
// profile is the browser-local one from lib/reportStyleProfile.ts and has
// nothing to do with R-4's per-workspace stored profiles — the two are
// different stores for different pipelines, which is why neither reads the
// other.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useRef, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import {
  buildReport,
  fetchActiveDataset,
  fetchReportBlockDataCsv,
  type ActiveDataset,
  type ReportBlock,
  type ReportMeta,
} from "@/lib/api";
import {
  clearStoredReportStyleProfile,
  exportReportStyleProfileToJson,
  isReportStyleProfileEmpty,
  parseReportStyleProfileFromJson,
  profileToApiStyle,
  readStoredReportStyleProfile,
  suggestedDefaultPalette,
  writeStoredReportStyleProfile,
  type ReportStyleProfile,
} from "@/lib/reportStyleProfile";

type Lang = "ko" | "en" | "ja";

type PaletteItem = { type: string; label: Record<Lang, string>; desc: Record<Lang, string> };

const PALETTE: PaletteItem[] = [
  {
    type: "cover",
    label: { ko: "표지 + 메타", en: "Cover + meta", ja: "表紙 + メタ" },
    desc: { ko: "제목·작성자·날짜 표지", en: "Title / author / date cover", ja: "タイトル・作成者・日付の表紙" },
  },
  {
    type: "overview",
    label: { ko: "개요 (자동 요약)", en: "Overview (auto)", ja: "概要 (自動要約)" },
    desc: { ko: "GEH·통행시간 결과 자동 요약문", en: "Auto GEH / travel-time summary", ja: "GEH・旅行時間結果の自動要約" },
  },
  {
    type: "geh-validation",
    label: { ko: "교통량 GEH 검증표", en: "Count GEH table", ja: "交通量GEH検証表" },
    desc: { ko: "노드별 실측·모델·GEH (AM/PM)", en: "Per-node observed/model/GEH", ja: "ノード別 実測・モデル・GEH" },
  },
  {
    type: "los-summary",
    label: { ko: "LoS 요약표", en: "LoS summary table", ja: "LoS要約表" },
    desc: { ko: "교차로·이동류 Volume/Delay/LOS/대기", en: "Volume/Delay/LOS/Queue", ja: "Volume/Delay/LOS/待ち行列" },
  },
  {
    type: "flow-geh-validation",
    label: { ko: "Flow / GEH 검증표", en: "Flow / GEH table", ja: "Flow / GEH検証表" },
    desc: { ko: "노드별 %Diff·Flow·GEH 통과율", en: "%Diff / Flow / GEH pass rate", ja: "%Diff・Flow・GEH通過率" },
  },
  {
    type: "travel-time-charts",
    label: { ko: "통행시간 차트", en: "Travel-time charts", ja: "旅行時間チャート" },
    desc: { ko: "루트×AM/PM 누적 차트", en: "Route × AM/PM cumulative charts", ja: "ルート×AM/PM 累積チャート" },
  },
  {
    type: "text",
    label: { ko: "자유 텍스트 단락", en: "Free text block", ja: "自由テキスト段落" },
    desc: { ko: "제목 + 본문 직접 작성", en: "Custom heading + body", ja: "見出し + 本文を直接入力" },
  },
];

type Block = ReportBlock & { id: string };

let _seq = 0;
function newId() {
  _seq += 1;
  return `b${_seq}`;
}

// Block types that render an actual table/figure -- and so have data behind
// them worth extracting (R4.1). "cover"/"overview"/"text" render prose, not
// a table/figure, so they intentionally get no CSV button (see report body
// for the full rationale). Keys match backend/routers/report.py's
// `_DATA_BLOCK_HEADERS` exactly.
const DATA_EXPORTABLE_TYPES = new Set(["geh-validation", "los-summary", "flow-geh-validation", "travel-time-charts"]);

const DEFAULT_BLOCKS: Block[] = [
  { id: newId(), type: "cover" },
  { id: newId(), type: "overview" },
  { id: newId(), type: "geh-validation" },
  { id: newId(), type: "los-summary" },
  { id: newId(), type: "flow-geh-validation" },
  { id: newId(), type: "travel-time-charts" },
];

export default function LegacyReportBuilder() {
  const { lang, t } = useLanguage();
  const L = (lang === "ko" ? "ko" : lang === "ja" ? "ja" : "en") as Lang;

  const [meta, setMeta] = useState<ReportMeta>({ title: "", subtitle: "Model Build Report", author: "", date: "", project: "", revision: "v1.0", reviewer: "" });
  const [filename, setFilename] = useState("vissim_report");
  const [blocks, setBlocks] = useState<Block[]>(DEFAULT_BLOCKS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);

  // R4.2 — 회사 스타일 프로필. null/empty = today's exact default appearance
  // everywhere (see profileToApiStyle in lib/reportStyleProfile.ts). Loaded
  // from localStorage after mount only (SSR has no window; this mirrors the
  // guarded-read pattern the rest of the app uses, e.g. dashboardSelectionStorage.ts).
  const [styleProfile, setStyleProfile] = useState<ReportStyleProfile | null>(null);
  const [styleLoaded, setStyleLoaded] = useState(false);
  const [styleOpen, setStyleOpen] = useState(false);
  const [dataBusyKey, setDataBusyKey] = useState<string | null>(null);
  const [dataStatus, setDataStatus] = useState<Record<string, "ok" | "err">>({});
  const importFileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchActiveDataset()
      .then((dataset) => {
        if (!cancelled) setActiveDataset(dataset);
      })
      .catch(() => {
        if (!cancelled) setActiveDataset(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // THE HYDRATION EXCEPTION (lib/asyncResource.ts): the profile is in
  // localStorage, which the server cannot read, so reading it during render
  // would desynchronise the server's HTML from the client's first render.
  // `styleLoaded` is set in the same landing because the persist effect below
  // waits for it -- the two writes are one event ("the stored profile, or the
  // absence of one, is now known"), not two.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- localStorage; must run after hydration
    setStyleProfile(readStoredReportStyleProfile());
    setStyleLoaded(true);
  }, []);

  // Persist on every change, once the initial load has happened (guards
  // against overwriting a stored profile with the pre-load `null` state).
  useEffect(() => {
    if (!styleLoaded) return;
    writeStoredReportStyleProfile(styleProfile);
  }, [styleLoaded, styleProfile]);

  const patchStyle = (p: Partial<ReportStyleProfile>) =>
    setStyleProfile((prev) => ({ ...(prev ?? {}), ...p }));

  const resetStyle = () => {
    setStyleProfile(null);
    clearStoredReportStyleProfile();
  };

  const fillDefaultPalette = () => patchStyle({ palette: suggestedDefaultPalette(6) });

  const setPaletteColor = (index: number, hex: string) =>
    setStyleProfile((prev) => {
      const palette = [...(prev?.palette ?? suggestedDefaultPalette(6))];
      palette[index] = hex;
      return { ...(prev ?? {}), palette };
    });

  const removePaletteColor = (index: number) =>
    setStyleProfile((prev) => {
      if (!prev?.palette) return prev;
      const palette = prev.palette.filter((_c, i) => i !== index);
      return { ...prev, palette };
    });

  const addPaletteColor = () =>
    setStyleProfile((prev) => {
      const palette = [...(prev?.palette ?? []), "#4c8dff"];
      return { ...(prev ?? {}), palette };
    });

  const exportStyleProfile = () => {
    const profile = styleProfile ?? {};
    const json = exportReportStyleProfileToJson(profile);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "vissim_report_style.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const importStyleProfile = async (file: File) => {
    setError(null);
    try {
      const text = await file.text();
      const profile = parseReportStyleProfileFromJson(text);
      setStyleProfile(profile);
      setStyleOpen(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const downloadBlockData = useCallback(async (blockType: string) => {
    setDataBusyKey(blockType);
    setDataStatus((prev) => ({ ...prev, [blockType]: undefined as never }));
    try {
      const blob = await fetchReportBlockDataCsv(blockType);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${blockType}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setDataStatus((prev) => ({ ...prev, [blockType]: "ok" }));
    } catch {
      setDataStatus((prev) => ({ ...prev, [blockType]: "err" }));
    } finally {
      setDataBusyKey(null);
    }
  }, []);

  const labelFor = (type: string) => PALETTE.find((p) => p.type === type)?.label[L] ?? type;

  const addBlock = (type: string) =>
    setBlocks((b) => [...b, { id: newId(), type, ...(type === "text" ? { heading: "", body: "" } : {}) }]);
  const removeBlock = (id: string) => setBlocks((b) => b.filter((x) => x.id !== id));
  const move = (id: string, dir: -1 | 1) =>
    setBlocks((b) => {
      const i = b.findIndex((x) => x.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= b.length) return b;
      const next = [...b];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  const patch = (id: string, p: Partial<Block>) =>
    setBlocks((b) => b.map((x) => (x.id === id ? { ...x, ...p } : x)));

  const generate = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      // profileToApiStyle omits `style` entirely for an empty/absent
      // profile -- that is what keeps the default report byte-for-byte
      // unchanged (see lib/reportStyleProfile.ts).
      const style = profileToApiStyle(styleProfile);
      const spec = {
        meta,
        filename,
        blocks: blocks.map(({ id: _id, ...rest }) => rest),
        ...(style ? { style } : {}),
      };
      const blob = await buildReport(spec);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = (filename || "vissim_report").replace(/\.docx$/i, "") + ".docx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [meta, filename, blocks, styleProfile]);

  const field = (key: keyof ReportMeta, label: string, placeholder?: string) => (
    <label className="block">
      <span className="mb-1 block text-[14px] font-medium text-slate-500">{label}</span>
      <input
        className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
        value={meta[key] ?? ""}
        placeholder={placeholder}
        onChange={(e) => setMeta((m) => ({ ...m, [key]: e.target.value }))}
      />
    </label>
  );

  return (
    <div className="space-y-6">
      <p className="text-[15px] text-slate-600">
        {t(
          "섹션을 추가·정렬하고 'Word 생성'을 누르면 현재 데이터셋 기준 보고서(.docx)가 만들어집니다.",
          "Add and order sections, then click 'Generate Word' to build a .docx report from the active dataset.",
          "セクションを追加・並べ替えて『Word生成』を押すと、現在のデータセットからレポート(.docx)が作成されます。",
        )}
      </p>

      {activeDataset && activeDataset.source !== "uploaded" && (
        <div className="rounded-xl border border-amber-300 bg-amber-100/70 px-4 py-3 text-[14px] font-medium text-amber-900">
          {t(
            `※ 업로드된 VISSIM 데이터셋이 없어 기본 샘플 데이터(${activeDataset.filename})로 보고서가 생성됩니다.`,
            `※ No VISSIM dataset has been uploaded — the report will be generated from the bundled sample data (${activeDataset.filename}).`,
            `※ VISSIMデータセットがアップロードされていないため、サンプルデータ（${activeDataset.filename}）でレポートが作成されます。`,
          )}
        </div>
      )}

      {/* meta */}
      <section className="rounded-xl border border-slate-200 p-5">
        <h2 className="mb-3 text-[15px] font-semibold text-white">{t("표지 정보", "Cover info", "表紙情報")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {field("title", t("제목", "Title", "タイトル"), t("예: Murdoch Drive 모델 검증 보고서", "e.g. Murdoch Drive validation report"))}
          {field("subtitle", t("부제", "Subtitle", "サブタイトル"))}
          {field("project", t("프로젝트", "Project", "プロジェクト"))}
          {field("revision", t("개정(Revision)", "Revision", "改訂"))}
          {field("author", t("작성자(Originator)", "Originator", "作成者"))}
          {field("reviewer", t("검토자(Review)", "Reviewer", "レビュー"))}
          {field("date", t("작성일", "Date", "作成日"), "2026-06-05")}
          <label className="block">
            <span className="mb-1 block text-[14px] font-medium text-slate-500">{t("파일명", "File name", "ファイル名")}</span>
            <input
              className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
            />
          </label>
        </div>
      </section>

      {/* palette */}
      <section className="rounded-xl border border-slate-200 p-5">
        <h2 className="mb-3 text-[15px] font-semibold text-white">{t("섹션 추가", "Add section", "セクション追加")}</h2>
        <div className="flex flex-wrap gap-2">
          {PALETTE.map((p) => (
            <button
              key={p.type}
              type="button"
              onClick={() => addBlock(p.type)}
              title={p.desc[L]}
              className="rounded-lg border border-slate-200 bg-slate-100 px-3 py-2 text-[14px] font-medium text-slate-700 transition hover:bg-slate-200 hover:text-white"
            >
              + {p.label[L]}
            </button>
          ))}
        </div>
      </section>

      {/* block list */}
      <section className="rounded-xl border border-slate-200 p-5">
        <h2 className="mb-3 text-[15px] font-semibold text-white">
          {t("보고서 구성", "Report outline", "レポート構成")} ({blocks.length})
        </h2>
        {blocks.length === 0 ? (
          <p className="text-[15px] text-slate-500">{t("위 버튼으로 섹션을 추가하세요.", "Add sections with the buttons above.", "上のボタンでセクションを追加してください。")}</p>
        ) : (
          <ol className="divide-y divide-slate-200">
            {blocks.map((b, i) => (
              <li key={b.id} className="py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-[13px] font-semibold text-slate-500">{i + 1}</span>
                    <span className="text-[15px] font-medium text-slate-900">{labelFor(b.type)}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    {DATA_EXPORTABLE_TYPES.has(b.type) && (
                      <button
                        type="button"
                        onClick={() => downloadBlockData(b.type)}
                        disabled={dataBusyKey === b.type}
                        title={t(
                          "이 섹션에 표시되는 데이터를 CSV로 내려받습니다.",
                          "Download the data rendered in this section as CSV.",
                          "このセクションに表示されるデータをCSVでダウンロードします。",
                        )}
                        className="rounded-md border border-slate-200 px-2 py-1 text-[13px] font-medium text-slate-600 transition hover:border-slate-300 hover:text-white disabled:cursor-wait disabled:opacity-50"
                      >
                        {dataBusyKey === b.type
                          ? t("내보내는 중…", "Exporting…", "エクスポート中…")
                          : dataStatus[b.type] === "ok"
                            ? t("✓ 완료", "✓ Done", "✓ 完了")
                            : dataStatus[b.type] === "err"
                              ? t("실패", "Failed", "失敗")
                              : t("데이터 내보내기", "Export data", "データ出力")}
                      </button>
                    )}
                    <button type="button" onClick={() => move(b.id, -1)} disabled={i === 0} className="rounded-md border border-slate-200 px-2 py-1 text-[14px] text-slate-600 transition hover:border-slate-300 hover:text-white disabled:opacity-40">↑</button>
                    <button type="button" onClick={() => move(b.id, 1)} disabled={i === blocks.length - 1} className="rounded-md border border-slate-200 px-2 py-1 text-[14px] text-slate-600 transition hover:border-slate-300 hover:text-white disabled:opacity-40">↓</button>
                    <button type="button" onClick={() => removeBlock(b.id)} className="rounded-md border border-slate-200 px-2 py-1 text-[14px] text-rose-600 transition hover:border-rose-300">✕</button>
                  </div>
                </div>
                {b.type === "text" && (
                  <div className="mt-2 grid gap-2">
                    <input
                      className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
                      placeholder={t("소제목", "Heading", "見出し")}
                      value={b.heading ?? ""}
                      onChange={(e) => patch(b.id, { heading: e.target.value })}
                    />
                    <textarea
                      className="min-h-[90px] w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
                      placeholder={t("본문 (줄바꿈은 새 단락)", "Body (newlines become paragraphs)", "本文 (改行で段落)")}
                      value={b.body ?? ""}
                      onChange={(e) => patch(b.id, { body: e.target.value })}
                    />
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* R4.2 — company style profile */}
      <section className="rounded-xl border border-slate-200 p-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-semibold text-white">{t("회사 스타일", "Company style", "会社スタイル")}</h2>
            <p className="mt-0.5 text-[14px] text-slate-500">
              {t(
                "차트 색상·폰트·그림 크기를 지정해 보고서에 회사 스타일을 적용합니다. 설정하지 않으면 기본 모양 그대로 생성됩니다.",
                "Set chart colors, fonts and figure size to apply your company's style. Leave unset to keep today's default appearance.",
                "チャートの色・フォント・図のサイズを指定してレポートに会社スタイルを適用します。未設定なら既定の外観のまま生成されます。",
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setStyleOpen((v) => !v)}
            className="flex-none rounded-lg border border-slate-200 px-3 py-1.5 text-[14px] font-semibold text-slate-700 transition hover:bg-slate-100"
          >
            {styleOpen ? t("접기", "Collapse", "折りたたむ") : t("펼치기", "Expand", "開く")}
          </button>
        </div>

        {!isReportStyleProfileEmpty(styleProfile) && (
          <p className="mt-2 text-[13px] font-semibold text-emerald-600">
            {t(
              "프로필 적용 중 — 생성되는 보고서와 데이터 내보내기에 반영됩니다.",
              "Profile active — applied to the generated report and its export.",
              "プロファイル適用中 — 生成されるレポートとエクスポートに反映されます。",
            )}
          </p>
        )}

        {styleOpen && (
          <div className="mt-4 space-y-4">
            {/* palette */}
            <div>
              <span className="mb-1 block text-[14px] font-medium text-slate-500">
                {t("차트 색상 (모델 → 관측 순)", "Chart colors (model, then observed)", "チャート色 (モデル→実測の順)")}
              </span>
              <div className="flex flex-wrap items-center gap-2">
                {(styleProfile?.palette ?? []).map((hex, i) => (
                  <div key={i} className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2 py-1">
                    <input
                      type="color"
                      value={/^#[0-9a-fA-F]{6}$/.test(hex) ? hex : "#4c8dff"}
                      onChange={(e) => setPaletteColor(i, e.target.value)}
                      className="h-6 w-6 cursor-pointer rounded border-0 bg-transparent p-0"
                      aria-label={t(`색상 ${i + 1}`, `Color ${i + 1}`, `色 ${i + 1}`)}
                    />
                    <input
                      type="text"
                      value={hex}
                      onChange={(e) => setPaletteColor(i, e.target.value)}
                      className="w-20 bg-transparent text-[14px] text-slate-700 outline-none"
                    />
                    <button type="button" onClick={() => removePaletteColor(i)} className="text-[14px] text-rose-500" aria-label={t("삭제", "Remove", "削除")}>
                      ✕
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={addPaletteColor}
                  className="rounded-lg border border-dashed border-slate-300 px-3 py-1.5 text-[14px] text-slate-600 transition hover:border-slate-400"
                >
                  + {t("색상 추가", "Add color", "色を追加")}
                </button>
                <button
                  type="button"
                  onClick={fillDefaultPalette}
                  className="rounded-lg border border-slate-200 px-3 py-1.5 text-[14px] text-slate-600 transition hover:bg-slate-100"
                >
                  {t("기본값으로 채우기", "Fill with default", "デフォルトで埋める")}
                </button>
              </div>
            </div>

            {/* font */}
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="block">
                <span className="mb-1 block text-[14px] font-medium text-slate-500">{t("폰트", "Font family", "フォント")}</span>
                <input
                  className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
                  placeholder={t("예: Malgun Gothic", "e.g. Arial", "例: メイリオ")}
                  value={styleProfile?.fontFamily ?? ""}
                  onChange={(e) => patchStyle({ fontFamily: e.target.value || undefined })}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[14px] font-medium text-slate-500">{t("제목 크기 (pt)", "Title size (pt)", "タイトルサイズ (pt)")}</span>
                <input
                  type="number"
                  min={6}
                  max={48}
                  className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
                  value={styleProfile?.fontSizeTitle ?? ""}
                  onChange={(e) => patchStyle({ fontSizeTitle: e.target.value ? Number(e.target.value) : undefined })}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[14px] font-medium text-slate-500">{t("라벨 크기 (pt)", "Label size (pt)", "ラベルサイズ (pt)")}</span>
                <input
                  type="number"
                  min={6}
                  max={32}
                  className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
                  value={styleProfile?.fontSizeLabel ?? ""}
                  onChange={(e) => patchStyle({ fontSizeLabel: e.target.value ? Number(e.target.value) : undefined })}
                />
              </label>
            </div>

            {/* figure export size */}
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-[14px] font-medium text-slate-500">{t("그림 너비 (in)", "Figure width (in)", "図の幅 (in)")}</span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  step={0.1}
                  className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
                  value={styleProfile?.figureWidthIn ?? ""}
                  onChange={(e) => patchStyle({ figureWidthIn: e.target.value ? Number(e.target.value) : undefined })}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-[14px] font-medium text-slate-500">
                  {t("그림 높이 (in, 선택)", "Figure height (in, optional)", "図の高さ (in, 任意)")}
                </span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  step={0.1}
                  className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-[15px]"
                  value={styleProfile?.figureHeightIn ?? ""}
                  onChange={(e) => patchStyle({ figureHeightIn: e.target.value ? Number(e.target.value) : undefined })}
                />
              </label>
            </div>

            {/* lightweight non-chart preview -- swatches + sample title text,
                not a fake chart; the report's only real chart is rendered
                server-side (see report body for why) */}
            <div className="rounded-lg border border-dashed border-slate-300 px-3 py-2">
              <span className="mb-1 block text-[13px] font-medium text-slate-500">{t("미리보기", "Preview", "プレビュー")}</span>
              <div className="flex flex-wrap items-center gap-2">
                {(styleProfile?.palette ?? []).map((hex, i) => (
                  <span key={i} className="h-5 w-5 flex-none rounded-full border border-slate-300" style={{ backgroundColor: hex }} />
                ))}
                <span
                  className="text-[15px] text-slate-700"
                  style={{
                    fontFamily: styleProfile?.fontFamily || undefined,
                    fontSize: styleProfile?.fontSizeTitle ? `${styleProfile.fontSizeTitle}px` : undefined,
                  }}
                >
                  {t("샘플 제목 텍스트", "Sample title text", "サンプルタイトルテキスト")}
                </span>
              </div>
            </div>

            {/* import / export / reset */}
            <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3">
              <button
                type="button"
                onClick={exportStyleProfile}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-[14px] font-semibold text-slate-700 transition hover:bg-slate-100"
              >
                {t("프로필 내보내기 (JSON)", "Export profile (JSON)", "プロファイル出力 (JSON)")}
              </button>
              <button
                type="button"
                onClick={() => importFileInputRef.current?.click()}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-[14px] font-semibold text-slate-700 transition hover:bg-slate-100"
              >
                {t("프로필 가져오기 (JSON)", "Import profile (JSON)", "プロファイル取込 (JSON)")}
              </button>
              <input
                ref={importFileInputRef}
                type="file"
                accept="application/json,.json"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) importStyleProfile(f);
                  e.target.value = "";
                }}
              />
              <button
                type="button"
                onClick={resetStyle}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-[14px] font-semibold text-rose-600 transition hover:border-rose-300"
              >
                {t("기본값으로 초기화", "Reset to default", "デフォルトに戻す")}
              </button>
            </div>
          </div>
        )}
      </section>

      {error && <div className="rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] text-rose-700">{error}</div>}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={generate}
          disabled={busy || blocks.length === 0}
          className="ui-button-primary px-5 py-2.5 text-[15px] disabled:cursor-wait disabled:opacity-50"
        >
          {busy ? t("생성 중…", "Generating…", "生成中…") : t("Word 생성", "Generate Word", "Word生成")}
        </button>
        <span className="text-[14px] text-slate-500">
          {t("현재 활성 데이터셋·검증 워크북 기준으로 만들어집니다.", "Built from the active dataset and validation workbook.", "現在のデータセット・検証ワークブックを基に作成されます。")}
        </span>
      </div>
    </div>
  );
}
