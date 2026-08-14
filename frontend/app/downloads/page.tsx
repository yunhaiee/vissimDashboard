"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import {
  apiFetch,
  downloadAgentModel,
  fetchActiveDataset,
  fetchAgentDownloads,
  type ActiveDataset,
  type AgentDownloadEntry,
} from "@/lib/api";

type ExportItem = { key: string; label: string; filename: string };

type ItemMeta = { ko: string; en: string; ja: string; desc: string; descEn: string; descJa: string; partial?: boolean };

// Korean labels + descriptions keyed by the backend export key.
const META: Record<string, ItemMeta> = {
  "daily-profile": {
    ko: "교통량 일일 프로파일",
    en: "Traffic daily profile",
    desc: "유입지점별 시간대별 수요량 (vehicleInput × 시간구간)",
    descEn: "Demand per vehicle input across time intervals",
    ja: "交通量日次プロファイル",
    descJa: "流入地点別・時間帯別の需要量（vehicleInput × 時間区間）",
  },
  "signal-timing": {
    ko: "신호 타이밍 데이터",
    en: "Signal timing data",
    desc: "교차로별 주기·오프셋·현시 계획·신호그룹·이동류",
    descEn: "Cycle, offset, phase plan, signal groups and movements per node",
    ja: "信号タイミングデータ",
    descJa: "交差点別の周期・オフセット・現示計画・信号グループ・移動流",
  },
  "travel-time": {
    ko: "통행시간 데이터",
    en: "Travel time data",
    desc: "루트별 실측 vs 모델 통행시간 (표)",
    descEn: "Observed vs modelled travel time per route",
    ja: "旅行時間データ",
    descJa: "ルート別の実測 vs モデル旅行時間（表）",
  },
  "public-transport": {
    ko: "대중교통 서비스",
    en: "Public transport services",
    desc: "버스 노선·배차간격(AM/PM)·정류장·정차시간",
    descEn: "Bus lines, headways, stops and dwell times",
    ja: "公共交通サービス",
    descJa: "バス路線・運行間隔(AM/PM)・停留所・停車時間",
  },
  "vehicle-composition": {
    ko: "교통류 / 차종 구성",
    en: "Traffic flow / vehicle composition",
    desc: "차종 구성비·차량 타입·차량 클래스",
    descEn: "Vehicle compositions, types and classes",
    ja: "交通流／車種構成",
    descJa: "車種構成比・車両タイプ・車両クラス",
  },
  "calibration": {
    ko: "보정 (Calibration)",
    en: "Calibration",
    desc: "GEH·절대오차·% 오차·R² (교통량 기준)",
    descEn: "GEH, absolute/percent error and R² from counts",
    ja: "補正 (Calibration)",
    descJa: "GEH・絶対誤差・%誤差・R²（交通量基準）",
    partial: true,
  },
  "validation": {
    ko: "검증 (Validation)",
    en: "Validation",
    desc: "통행시간 ±10% 허용범위·런 안정성(CoV)",
    descEn: "Travel-time tolerance band and run stability (CoV)",
    ja: "検証 (Validation)",
    descJa: "旅行時間 ±10% 許容範囲・実行安定性(CoV)",
    partial: true,
  },
  "model-outputs": {
    ko: "모델 산출물",
    en: "Model outputs",
    desc: "교차로 성능(지체·LOS·대기) + 네트워크 성능(VKT·VHT·평균속도·잠재수요)",
    descEn: "Intersection (delay/LOS/queue) and network (VKT/VHT/speed) performance",
    ja: "モデル成果物",
    descJa: "交差点性能(遅延・LOS・待ち行列) + ネットワーク性能(VKT・VHT・平均速度・潜在需要)",
    partial: true,
  },
  "los-summary": {
    ko: "LoS 요약 보고서 (Word)",
    en: "LoS summary report (Word)",
    desc: "교차로·이동류별 Volume/Delay/LOS/대기길이 (AM·PM, 색상표) — 보고서 양식 .docx",
    descEn: "Per-intersection & movement Volume/Delay/LOS/Queue (AM & PM, colour table) — .docx",
    ja: "LoS 要約レポート (Word)",
    descJa: "交差点・移動流別 Volume/Delay/LOS/待ち行列 (AM・PM、色付き表) — レポート様式 .docx",
    partial: true,
  },
  "flow-geh-validation": {
    ko: "Flow / GEH 검증 보고서 (Word)",
    en: "Flow / GEH validation report (Word)",
    desc: "노드별 실측·모델·%Diff·Flow·GEH 통과율 (AM·PM) — 보고서 양식 .docx",
    descEn: "Per-node observed/modelled/%diff/Flow/GEH pass rates (AM & PM) — .docx",
    ja: "Flow / GEH 検証レポート (Word)",
    descJa: "ノード別 実測・モデル・%Diff・Flow・GEH 通過率 (AM・PM) — レポート様式 .docx",
    partial: true,
  },
  "travel-time-charts": {
    ko: "통행시간 차트 (PNG ZIP)",
    en: "Travel-time charts (PNG ZIP)",
    desc: "루트×AM/PM 누적 통행시간 차트 (관측·±15%·모델) PNG 4장 .zip",
    descEn: "Cumulative travel-time charts per route × AM/PM (observed/±15%/model) — 4 PNGs .zip",
    ja: "旅行時間チャート (PNG ZIP)",
    descJa: "ルート×AM/PM 累積旅行時間チャート (実測・±15%・モデル) PNG 4枚 .zip",
    partial: true,
  },
};

const ORDER = [
  "daily-profile",
  "signal-timing",
  "travel-time",
  "public-transport",
  "vehicle-composition",
  "calibration",
  "validation",
  "model-outputs",
  "los-summary",
  "flow-geh-validation",
  "travel-time-charts",
];

function formatDownloadTimestamp(iso: string, lang: "ko" | "en" | "ja"): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const locale = lang === "ko" ? "ko-KR" : lang === "ja" ? "ja-JP" : "en-US";
  return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

export default function DownloadsPage() {
  const { lang, t } = useLanguage();
  const [items, setItems] = useState<ExportItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<Record<string, "ok" | "err">>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [busyCombined, setBusyCombined] = useState(false);
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);

  // P8 (edited-model download): the AI agent's edited working copies,
  // listed newest-first (backend/routers/agent_downloads.py's unscoped
  // GET /api/agent/downloads — the latest saved version per session that
  // has actually saved a write).
  const [agentDownloads, setAgentDownloads] = useState<AgentDownloadEntry[]>([]);
  const [agentDownloadsLoading, setAgentDownloadsLoading] = useState(true);
  const [agentDownloadsError, setAgentDownloadsError] = useState<string | null>(null);
  const [agentDownloadBusyKey, setAgentDownloadBusyKey] = useState<string | null>(null);
  const [agentDownloadStatus, setAgentDownloadStatus] = useState<Record<string, "ok" | "err">>({});

  const loadAgentDownloads = useCallback(() => {
    let cancelled = false;
    setAgentDownloadsLoading(true);
    fetchAgentDownloads({ limit: 20 })
      .then((entries) => {
        if (!cancelled) {
          setAgentDownloads(entries);
          setAgentDownloadsError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setAgentDownloadsError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setAgentDownloadsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => loadAgentDownloads(), [loadAgentDownloads]);

  // Refresh the list the moment the floating chat's agent session id
  // changes — the same "vissim:agent-session" broadcast app/dashboard/
  // page.tsx and app/network/page.tsx already listen for (docs/phase2/
  // DECISIONS.md's P2 entry) — so a write made while this page is open
  // shows up without a manual reload. A stale/unrelated id is harmless
  // here: this only ever triggers a re-fetch of the unscoped (all
  // sessions) listing, never a filtered one.
  useEffect(() => {
    function handleAgentSession() {
      loadAgentDownloads();
    }
    window.addEventListener("vissim:agent-session", handleAgentSession);
    return () => {
      window.removeEventListener("vissim:agent-session", handleAgentSession);
    };
  }, [loadAgentDownloads]);

  const downloadAgentEntry = useCallback(async (entry: AgentDownloadEntry) => {
    const key = `${entry.session_id}:${entry.version ?? "latest"}`;
    setAgentDownloadBusyKey(key);
    setAgentDownloadStatus((prev) => ({ ...prev, [key]: undefined as never }));
    try {
      const { blob, filename } = await downloadAgentModel(entry.session_id, entry.version);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setAgentDownloadStatus((prev) => ({ ...prev, [key]: "ok" }));
    } catch {
      setAgentDownloadStatus((prev) => ({ ...prev, [key]: "err" }));
    } finally {
      setAgentDownloadBusyKey(null);
    }
  }, []);

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

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch("/api/exports", { cache: "no-store" });
        if (!res.ok) throw new Error(await res.text());
        const data: ExportItem[] = await res.json();
        if (!cancelled) setItems(data);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const download = useCallback(
    async (item: ExportItem) => {
      setBusy(item.key);
      setDone((prev) => ({ ...prev, [item.key]: undefined as never }));
      try {
        const res = await apiFetch(`/api/exports/${item.key}`, { cache: "no-store" });
        if (!res.ok) throw new Error(await res.text());
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = item.filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        setDone((prev) => ({ ...prev, [item.key]: "ok" }));
      } catch {
        setDone((prev) => ({ ...prev, [item.key]: "err" }));
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const ordered = [...items].sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key));

  const selectedCount = ordered.filter((i) => selected[i.key]).length;
  const allSelected = ordered.length > 0 && selectedCount === ordered.length;
  const toggle = (key: string) => setSelected((prev) => ({ ...prev, [key]: !prev[key] }));
  const toggleAll = () =>
    setSelected(allSelected ? {} : Object.fromEntries(ordered.map((i) => [i.key, true])));

  const downloadCombined = async () => {
    const keys = ordered.filter((i) => selected[i.key]).map((i) => i.key);
    if (keys.length === 0) return;
    setBusyCombined(true);
    try {
      const res = await apiFetch(`/api/exports/combined?keys=${encodeURIComponent(keys.join(","))}`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "vissim_export.xlsx";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyCombined(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-white">
          {t("데이터 다운로드", "Data Downloads")}
        </h1>
        <p className="mt-1 text-[15px] text-slate-600">
          {t(
            "모델 산출 데이터를 항목별로 Excel(.xlsx)로 내려받습니다.",
            "Download the model deliverables item by item as Excel (.xlsx).",
          )}
        </p>
      </header>

      <section className="mb-6 rounded-xl border border-slate-200 bg-surface/90 px-5 py-4 shadow-[0_8px_18px_rgba(15,23,42,0.06)]">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-bold text-slate-900">
              {t("편집된 모델", "Edited models", "編集済みモデル")}
            </h2>
            <p className="mt-0.5 text-[14px] text-slate-500">
              {t(
                "AI 에이전트가 채팅에서 변경한 VISSIM 모델(.inpx)을 내려받습니다.",
                "Download the VISSIM model (.inpx) the AI agent edited in chat.",
                "AIエージェントがチャットで変更したVISSIMモデル(.inpx)をダウンロードします。",
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={loadAgentDownloads}
            disabled={agentDownloadsLoading}
            className="rounded-lg border border-slate-300 bg-surface px-3 py-1.5 text-[14px] font-semibold text-slate-700 transition hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60"
          >
            {agentDownloadsLoading
              ? t("새로고침 중…", "Refreshing…", "更新中…")
              : t("새로고침", "Refresh", "更新")}
          </button>
        </div>

        {agentDownloadsError && (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[14px] text-rose-700">
            {t("목록을 불러오지 못했습니다", "Failed to load the list", "リストを読み込めませんでした")}: {agentDownloadsError}
          </div>
        )}

        {!agentDownloadsLoading && !agentDownloadsError && agentDownloads.length === 0 && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-[14px] text-slate-500">
            {t(
              "아직 편집된 모델이 없습니다. AI 에이전트 채팅에서 변경을 실행하면 여기에 표시됩니다.",
              "No edited models yet. Once the AI agent executes a change in chat, it will show up here.",
              "まだ編集済みモデルはありません。AIエージェントのチャットで変更を実行するとここに表示されます。",
            )}
          </p>
        )}

        {agentDownloads.length > 0 && (
          <ul className="space-y-2">
            {agentDownloads.map((entry) => {
              const key = `${entry.session_id}:${entry.version ?? "latest"}`;
              const status = agentDownloadStatus[key];
              const versionLabel =
                entry.version === null
                  ? t("미저장(현재 작업본)", "Unsaved (current)", "未保存（現在の作業版）")
                  : t(`버전 ${entry.version}`, `Version ${entry.version}`, `バージョン ${entry.version}`);
              return (
                <li
                  key={key}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3.5 py-3"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[15px] font-semibold text-slate-900">{entry.dataset_filename}</span>
                      {entry.is_latest && (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[12px] font-semibold text-emerald-700">
                          {t("최신", "Latest", "最新")}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-[14px] text-slate-500">
                      {t("세션", "Session", "セッション")} {entry.session_id_short} · {versionLabel} ·{" "}
                      {formatDownloadTimestamp(entry.modified_at, lang)}
                    </p>
                  </div>
                  <div className="flex flex-none items-center gap-2">
                    {status === "ok" && (
                      <span className="text-[14px] font-semibold text-emerald-600">{t("완료", "Done", "完了")}</span>
                    )}
                    {status === "err" && (
                      <span className="text-[14px] font-semibold text-rose-600">{t("실패", "Failed", "失敗")}</span>
                    )}
                    <button
                      type="button"
                      onClick={() => downloadAgentEntry(entry)}
                      disabled={agentDownloadBusyKey === key}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3.5 py-2 text-[14px] font-semibold text-ink transition hover:bg-slate-950 disabled:opacity-50"
                    >
                      {agentDownloadBusyKey === key
                        ? t("생성 중…", "Building…", "生成中…")
                        : t("다운로드", "Download", "ダウンロード")}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {error && (
        <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[15px] text-rose-700">
          {t("목록을 불러오지 못했습니다", "Failed to load the list")}: {error}
        </div>
      )}

      {activeDataset && activeDataset.source !== "uploaded" && (
        <div className="mb-5 rounded-xl border border-amber-300 bg-amber-100/70 px-4 py-3 text-[14px] font-medium text-amber-900">
          {t(
            `※ 업로드된 VISSIM 데이터셋이 없어 기본 샘플 데이터(${activeDataset.filename})를 내려받게 됩니다. 실제 프로젝트 데이터를 원하면 대시보드에서 먼저 업로드하세요.`,
            `※ No VISSIM dataset has been uploaded — this will export the bundled sample data (${activeDataset.filename}). Upload your project data on the dashboard first if you want real results.`,
          )}
        </div>
      )}

      <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-[14px] text-amber-800 mb-5">
        {t(
          "※ 보정·검증·모델 산출물은 시나리오 시뮬레이션(.db) 결과에 기반합니다. 현재 시뮬레이션 런이 미완성이면 값이 부분적일 수 있습니다.",
          "Note: Calibration, Validation and Model outputs are based on the scenario simulation (.db). Values may be partial if the runs are incomplete.",
        )}
      </div>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
        <div className="flex items-center gap-3 text-[15px] text-slate-600">
          <button
            type="button"
            onClick={toggleAll}
            className="rounded-lg border border-slate-300 bg-surface px-3 py-1.5 text-[14px] font-semibold text-slate-700 transition hover:bg-slate-100"
          >
            {allSelected ? t("선택 해제", "Clear all") : t("전체 선택", "Select all")}
          </button>
          <span>
            {t("선택", "Selected")} {selectedCount}/{ordered.length}
          </span>
        </div>
        <button
          type="button"
          onClick={downloadCombined}
          disabled={selectedCount === 0 || busyCombined}
          className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2 text-[15px] font-semibold text-ink transition hover:bg-slate-950 disabled:opacity-40"
        >
          {busyCombined
            ? t("생성 중…", "Building…")
            : t("선택 항목 통합 다운로드 (시트별)", "Download selected (one file, separate sheets)")}
        </button>
      </div>

      <ol className="space-y-3">
        {ordered.map((item, index) => {
          const meta = META[item.key];
          const label = meta ? (lang === "ko" ? meta.ko : lang === "ja" ? meta.ja : meta.en) : item.label;
          const desc = meta ? (lang === "ko" ? meta.desc : lang === "ja" ? meta.descJa : meta.descEn) : "";
          const status = done[item.key];
          return (
            <li
              key={item.key}
              className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 bg-surface/90 px-5 py-4 shadow-[0_8px_18px_rgba(15,23,42,0.06)]"
            >
              <div className="flex items-start gap-4">
                <input
                  type="checkbox"
                  checked={!!selected[item.key]}
                  onChange={() => toggle(item.key)}
                  className="mt-2 h-4 w-4 flex-none accent-accent"
                  aria-label={label}
                />
                <span className="mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-full bg-slate-100 text-[15px] font-bold text-slate-600">
                  {index + 1}
                </span>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-[15px] font-bold text-slate-900">{label}</h2>
                    {meta?.partial && (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[12px] font-semibold text-amber-700">
                        {t("시뮬 의존", "sim-based")}
                      </span>
                    )}
                  </div>
                  {desc && <p className="mt-0.5 text-[14px] text-slate-500">{desc}</p>}
                  <p className="mt-1 font-mono text-[12px] text-slate-400">{item.filename}</p>
                </div>
              </div>

              <div className="flex flex-none items-center gap-2">
                {status === "ok" && (
                  <span className="text-[14px] font-semibold text-emerald-600">{t("완료", "Done")}</span>
                )}
                {status === "err" && (
                  <span className="text-[14px] font-semibold text-rose-600">{t("실패", "Failed")}</span>
                )}
                <button
                  type="button"
                  onClick={() => download(item)}
                  disabled={busy === item.key}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3.5 py-2 text-[15px] font-semibold text-ink transition hover:bg-slate-950 disabled:opacity-50"
                >
                  {busy === item.key ? t("생성 중…", "Building…") : t("다운로드", "Download")}
                </button>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
