"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import { buildReport, fetchActiveDataset, type ActiveDataset, type ReportBlock, type ReportMeta } from "@/lib/api";

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

const DEFAULT_BLOCKS: Block[] = [
  { id: newId(), type: "cover" },
  { id: newId(), type: "overview" },
  { id: newId(), type: "geh-validation" },
  { id: newId(), type: "los-summary" },
  { id: newId(), type: "flow-geh-validation" },
  { id: newId(), type: "travel-time-charts" },
];

export default function ReportPage() {
  const { lang, t } = useLanguage();
  const L = (lang === "ko" ? "ko" : lang === "ja" ? "ja" : "en") as Lang;

  const [meta, setMeta] = useState<ReportMeta>({ title: "", subtitle: "Model Build Report", author: "", date: "", project: "", revision: "v1.0", reviewer: "" });
  const [filename, setFilename] = useState("vissim_report");
  const [blocks, setBlocks] = useState<Block[]>(DEFAULT_BLOCKS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);

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
      const spec = {
        meta,
        filename,
        blocks: blocks.map(({ id: _id, ...rest }) => rest),
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
  }, [meta, filename, blocks]);

  const field = (key: keyof ReportMeta, label: string, placeholder?: string) => (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-500">{label}</span>
      <input
        className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-sm"
        value={meta[key] ?? ""}
        placeholder={placeholder}
        onChange={(e) => setMeta((m) => ({ ...m, [key]: e.target.value }))}
      />
    </label>
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
      <div>
        <h1 className="text-[22px] font-medium text-white">{t("보고서 작성", "Report Builder", "レポート作成")}</h1>
        <p className="mt-1 text-sm text-slate-600">
          {t(
            "섹션을 추가·정렬하고 'Word 생성'을 누르면 현재 데이터셋 기준 보고서(.docx)가 만들어집니다.",
            "Add and order sections, then click 'Generate Word' to build a .docx report from the active dataset.",
            "セクションを追加・並べ替えて『Word生成』を押すと、現在のデータセットからレポート(.docx)が作成されます。",
          )}
        </p>
      </div>

      {activeDataset && activeDataset.source !== "uploaded" && (
        <div className="rounded-xl border border-amber-300 bg-amber-100/70 px-4 py-3 text-xs font-medium text-amber-900">
          {t(
            `※ 업로드된 VISSIM 데이터셋이 없어 기본 샘플 데이터(${activeDataset.filename})로 보고서가 생성됩니다.`,
            `※ No VISSIM dataset has been uploaded — the report will be generated from the bundled sample data (${activeDataset.filename}).`,
            `※ VISSIMデータセットがアップロードされていないため、サンプルデータ（${activeDataset.filename}）でレポートが作成されます。`,
          )}
        </div>
      )}

      {/* meta */}
      <section className="rounded-xl border border-slate-200 p-5">
        <h2 className="mb-3 text-sm font-semibold text-white">{t("표지 정보", "Cover info", "表紙情報")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          {field("title", t("제목", "Title", "タイトル"), t("예: Murdoch Drive 모델 검증 보고서", "e.g. Murdoch Drive validation report"))}
          {field("subtitle", t("부제", "Subtitle", "サブタイトル"))}
          {field("project", t("프로젝트", "Project", "プロジェクト"))}
          {field("revision", t("개정(Revision)", "Revision", "改訂"))}
          {field("author", t("작성자(Originator)", "Originator", "作成者"))}
          {field("reviewer", t("검토자(Review)", "Reviewer", "レビュー"))}
          {field("date", t("작성일", "Date", "作成日"), "2026-06-05")}
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-500">{t("파일명", "File name", "ファイル名")}</span>
            <input
              className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-sm"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
            />
          </label>
        </div>
      </section>

      {/* palette */}
      <section className="rounded-xl border border-slate-200 p-5">
        <h2 className="mb-3 text-sm font-semibold text-white">{t("섹션 추가", "Add section", "セクション追加")}</h2>
        <div className="flex flex-wrap gap-2">
          {PALETTE.map((p) => (
            <button
              key={p.type}
              type="button"
              onClick={() => addBlock(p.type)}
              title={p.desc[L]}
              className="rounded-lg border border-slate-200 bg-slate-100 px-3 py-2 text-xs font-medium text-slate-700 transition hover:bg-slate-200 hover:text-white"
            >
              + {p.label[L]}
            </button>
          ))}
        </div>
      </section>

      {/* block list */}
      <section className="rounded-xl border border-slate-200 p-5">
        <h2 className="mb-3 text-sm font-semibold text-white">
          {t("보고서 구성", "Report outline", "レポート構成")} ({blocks.length})
        </h2>
        {blocks.length === 0 ? (
          <p className="text-sm text-slate-500">{t("위 버튼으로 섹션을 추가하세요.", "Add sections with the buttons above.", "上のボタンでセクションを追加してください。")}</p>
        ) : (
          <ol className="divide-y divide-slate-200">
            {blocks.map((b, i) => (
              <li key={b.id} className="py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-500">{i + 1}</span>
                    <span className="text-sm font-medium text-slate-900">{labelFor(b.type)}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <button type="button" onClick={() => move(b.id, -1)} disabled={i === 0} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 transition hover:border-slate-300 hover:text-white disabled:opacity-40">↑</button>
                    <button type="button" onClick={() => move(b.id, 1)} disabled={i === blocks.length - 1} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 transition hover:border-slate-300 hover:text-white disabled:opacity-40">↓</button>
                    <button type="button" onClick={() => removeBlock(b.id)} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-rose-600 transition hover:border-rose-300">✕</button>
                  </div>
                </div>
                {b.type === "text" && (
                  <div className="mt-2 grid gap-2">
                    <input
                      className="w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-sm"
                      placeholder={t("소제목", "Heading", "見出し")}
                      value={b.heading ?? ""}
                      onChange={(e) => patch(b.id, { heading: e.target.value })}
                    />
                    <textarea
                      className="min-h-[90px] w-full rounded-lg border border-slate-200 bg-ink/40 px-3 py-2 text-sm"
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

      {error && <div className="rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={generate}
          disabled={busy || blocks.length === 0}
          className="rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-semibold text-ink transition hover:bg-slate-950 disabled:cursor-wait disabled:opacity-50"
        >
          {busy ? t("생성 중…", "Generating…", "生成中…") : t("Word 생성", "Generate Word", "Word生成")}
        </button>
        <span className="text-xs text-slate-500">
          {t("현재 활성 데이터셋·검증 워크북 기준으로 만들어집니다.", "Built from the active dataset and validation workbook.", "現在のデータセット・検証ワークブックを基に作成されます。")}
        </span>
      </div>
    </div>
  );
}
