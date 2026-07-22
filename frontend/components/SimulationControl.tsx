"use client";

// ARCHIVED, currently unreferenced by any route (kept per
// docs/removed-routes-archive.md for future restoration of /simulate).
// KNOWN BUG before re-wiring this into a page: the WebSocket-only progress
// monitor below has no fallback if the socket drops or never confirms
// completion — a run that finished (or failed) can misreport as stuck on
// "running" indefinitely, and a dropped connection reads as a failure even
// when the underlying job succeeded. Give it a polling fallback (same
// pattern as FloatingAssistant's PromptSimulationStatus, which polls
// fetchSimulationJob on an interval) before wiring this back into a page.
import { useState } from "react";
import { useLanguage } from "@/components/LanguageProvider";

import {
  createSimulationWS,
  startSimulation,
  type SimulationStatusMessage,
} from "@/lib/api";

export default function SimulationControl() {
  const { t } = useLanguage();
  const [name, setName] = useState("Interactive scenario");
  const [duration, setDuration] = useState(300);
  const [seed, setSeed] = useState(42);
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [progress, setProgress] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleRun = async () => {
    setStatus("running");
    setProgress(0);
    setError(null);

    try {
      const job = await startSimulation({
        name,
        sim_duration: duration,
        random_seed: seed,
        vehicle_inputs: {},
        signal_cycles: {},
        incidents: [],
      });
      setJobId(job.id);

      const ws = createSimulationWS(job.id);
      ws.onmessage = (event) => {
        let data: SimulationStatusMessage;
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }

        const nextProgress = data.status === "done"
          ? 100
          : Math.min(Math.floor((data.progress ?? 0) * 100), 99);
        setProgress(nextProgress);
        if (data.status === "done") {
          setStatus("done");
          ws.close();
        } else if (data.status === "error") {
          setStatus("error");
          setError(data.error ?? t("시뮬레이션이 실패했습니다.", "Simulation failed"));
          ws.close();
        }
      };

      ws.onerror = () => {
        setStatus("error");
        setError(t("WebSocket 연결에 실패했습니다.", "WebSocket connection failed"));
      };
    } catch (err) {
      setStatus("error");
      setError(String(err));
    }
  };

  return (
    <div className="ui-surface-strong max-w-lg space-y-5 p-6">
      <h2 className="text-base font-semibold text-slate-900">{t("시뮬레이션 파라미터", "Simulation Parameters")}</h2>

      <div className="space-y-3">
        <label className="block">
          <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">{t("시나리오 이름", "Scenario name")}</span>
          <input
            className="ui-input mt-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">{t("지속 시간 (s)", "Duration (s)")}</span>
            <input
              type="number"
              className="ui-input mt-1"
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
            />
          </label>
          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">{t("랜덤 시드", "Random seed")}</span>
            <input
              type="number"
              className="ui-input mt-1"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value))}
            />
          </label>
        </div>
      </div>

      <button
        onClick={handleRun}
        disabled={status === "running"}
        className="ui-button-primary w-full px-4 py-2.5 text-sm disabled:cursor-not-allowed"
      >
        {status === "running" ? t("실행 중...", "Running...") : t("시뮬레이션 실행", "Run simulation")}
      </button>

      {status === "running" && (
        <div className="rounded-xl border border-slate-200/80 bg-slate-50/90 p-3">
          <div className="mb-1 flex justify-between text-xs text-slate-500">
            <span>{t("진행률", "Progress")}</span>
            <span>{progress}%</span>
          </div>
          <div className="h-2 w-full rounded-full bg-slate-200/80">
            <div
              className="h-2 rounded-full bg-blue-500 transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="mt-1 text-xs text-slate-400">
            {t("완료 후 대시보드에서 결과를 재사용할 수 있습니다.", "The simulation result can be reused by the dashboard after completion.")}
          </p>
        </div>
      )}

      {status === "done" && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-600">
          {t("시뮬레이션 완료", "Simulation completed")} (Job: {jobId})
        </div>
      )}

      {status === "error" && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-600">
          {error}
        </div>
      )}
    </div>
  );
}
