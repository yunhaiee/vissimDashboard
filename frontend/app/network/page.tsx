"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import VissimDatasetBanner from "@/components/VissimDatasetBanner";
import { fetchNetwork, type ActiveDataset, type NetworkData } from "@/lib/api";

// NetworkViewer draws with d3 straight against the DOM, so it must stay
// client-only (same reason DashboardMap/Leaflet is dynamically imported).
const NetworkViewer = dynamic(() => import("@/components/NetworkViewer"), { ssr: false });

const MAX_SELECTED_NODES = 6;

export default function NetworkPage() {
  const { t } = useLanguage();
  const [network, setNetwork] = useState<NetworkData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [datasetRevision, setDatasetRevision] = useState("default");
  const [reloadNonce, setReloadNonce] = useState(0);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedLinkId, setSelectedLinkId] = useState<string | null>(null);

  // Load the raw-coordinate network on mount, and again whenever the active
  // dataset changes or a retry is requested.
  useEffect(() => {
    let cancelled = false;
    setNetwork(null);
    setError(null);
    setLoading(true);
    fetchNetwork()
      .then((data) => {
        if (!cancelled) setNetwork(data);
      })
      .catch(() => {
        if (!cancelled) setError(t("네트워크 도식을 불러오지 못했습니다.", "Failed to load the network diagram."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetRevision, reloadNonce, t]);

  const handleDatasetChange = useCallback((dataset?: ActiveDataset) => {
    setSelectedNodeIds([]);
    setSelectedLinkId(null);
    setDatasetRevision(dataset?.revision ?? String(Date.now()));
  }, []);

  // Mirror the dashboard's handleNodeClick semantics: clicking toggles the node
  // in the comparison set, capped at six with the oldest dropped first.
  const handleNodeClick = useCallback((nodeId: string) => {
    setSelectedNodeIds((prev) => {
      if (prev.includes(nodeId)) {
        return prev.filter((id) => id !== nodeId);
      }
      const next = [...prev, nodeId];
      return next.length <= MAX_SELECTED_NODES ? next : next.slice(next.length - MAX_SELECTED_NODES);
    });
  }, []);

  const handleLinkClick = useCallback((linkId: string) => {
    setSelectedLinkId((prev) => (prev === linkId ? null : linkId));
  }, []);

  return (
    <div className="mx-auto w-full max-w-[96rem] space-y-6 pb-16">
      <section className="px-1">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight text-white">
              {t("네트워크 프리뷰", "Network Preview")}
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              {t(
                "활성 VISSIM 데이터셋(.inpx)의 원좌표를 그대로 렌더링합니다.",
                "Renders the active VISSIM dataset (.inpx) straight from its raw coordinates.",
              )}
            </p>
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <div className="w-full lg:max-w-[320px]">
            <VissimDatasetBanner onDatasetChange={handleDatasetChange} fullWidth className="h-full" />
          </div>
        </div>
      </section>

      <div className="relative h-[70vh] min-h-[420px] overflow-hidden rounded-xl border border-slate-300/70 bg-ink shadow-[0_18px_34px_rgba(15,23,42,0.16)]">
        {error ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
            <p className="text-sm text-slate-400">{error}</p>
            <button
              type="button"
              onClick={() => setReloadNonce((nonce) => nonce + 1)}
              className="rounded-lg border border-slate-300/70 bg-surface/90 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-surface"
            >
              {t("다시 시도", "Retry")}
            </button>
          </div>
        ) : loading || !network ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-slate-400">{t("네트워크 도식을 불러오는 중…", "Loading network diagram…")}</p>
          </div>
        ) : (
          <NetworkViewer
            network={network}
            selectedNodeIds={selectedNodeIds}
            selectedLinkId={selectedLinkId}
            onNodeClick={handleNodeClick}
            onLinkClick={handleLinkClick}
          />
        )}
      </div>

      {selectedNodeIds.length > 0 && (
        <p className="px-1 text-xs text-slate-500">
          {t(`선택된 교차로 ${selectedNodeIds.length}개`, `${selectedNodeIds.length} intersection${selectedNodeIds.length > 1 ? "s" : ""} selected`)}
        </p>
      )}
    </div>
  );
}
