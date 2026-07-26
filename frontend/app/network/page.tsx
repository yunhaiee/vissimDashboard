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
  const [activeDataset, setActiveDataset] = useState<ActiveDataset | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedLinkInfo, setSelectedLinkInfo] = useState<{ id: string; name: string; isConnector: boolean } | null>(null);
  // P2: the floating assistant's current agent session id, mirrored here via
  // the "vissim:agent-session" event so this page's refresh button can show
  // that session's in-progress edited working copy on demand -- see
  // docs/phase2/DECISIONS.md's P2 entry.
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null);

  // Load the raw-coordinate network on mount, and again whenever the active
  // dataset changes or a retry is requested.
  useEffect(() => {
    let cancelled = false;
    setNetwork(null);
    setError(null);
    setLoading(true);
    fetchNetwork(agentSessionId ?? undefined)
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
  }, [datasetRevision, reloadNonce, agentSessionId, t]);

  // P2: mirror the floating assistant's current agent session id -- see
  // FloatingAssistant.tsx's own "vissim:agent-session" dispatch effect and
  // app/dashboard/page.tsx's matching listener.
  useEffect(() => {
    function handleAgentSession(event: Event) {
      const detail = (event as CustomEvent<{ sessionId: string | null }>).detail;
      setAgentSessionId(detail?.sessionId ?? null);
    }

    window.addEventListener("vissim:agent-session", handleAgentSession);
    return () => {
      window.removeEventListener("vissim:agent-session", handleAgentSession);
    };
  }, []);

  const handleDatasetChange = useCallback((dataset?: ActiveDataset) => {
    setActiveDataset(dataset ?? null);
    setSelectedNodeIds([]);
    setSelectedLinkInfo(null);
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

  // Link/connector clicks are UI-only context for the floating assistant (a
  // chip/note) — unlike node clicks they never flow into the backend request
  // shape (selected_scenario_ids/selected_node_ids).
  const handleLinkClick = useCallback((linkId: string, linkName: string, isConnector: boolean) => {
    setSelectedLinkInfo((prev) => (prev && prev.id === linkId ? null : { id: linkId, name: linkName, isConnector }));
  }, []);

  // Mirrors the dashboard/base-model pages' assistant page-context dispatch:
  // publish on mount/change and again whenever the floating assistant asks
  // (vissim:assistant-context-request), matching FloatingAssistant's listener
  // in components/FloatingAssistant.tsx. Neither of those pages dispatches a
  // `clear: true` event on unmount — FloatingAssistant already treats stale
  // context as inert once the pathname no longer starts with its route (see
  // the `pathname.startsWith(pageContext.route)` guard before the assistant
  // reads it), so this page matches that existing convention rather than
  // adding a new one.
  useEffect(() => {
    const publishAssistantContext = () => {
      window.dispatchEvent(
        new CustomEvent("vissim:page-context", {
          detail: {
            route: "/network",
            label: t("네트워크 프리뷰 컨텍스트", "Network preview context"),
            selectedNodeIds,
            selectedNodeNames: selectedNodeIds.map((nodeId) => {
              const node = network?.nodes.find((candidate) => candidate.id === nodeId);
              return node?.name || `Node ${nodeId}`;
            }),
            dataset: activeDataset
              ? {
                  filename: activeDataset.filename,
                  source: activeDataset.source,
                  revision: activeDataset.revision,
                }
              : null,
            notes: [
              selectedNodeIds.length > 0
                ? t(
                    `선택된 교차로 ${selectedNodeIds.length}개`,
                    `${selectedNodeIds.length} intersection${selectedNodeIds.length > 1 ? "s" : ""} selected`,
                  )
                : t("선택된 교차로 없음", "No intersections selected"),
              // Link/connector selection is UI-only context (never sent as
              // selected_scenario_ids/selected_node_ids) — surfaced here only
              // so the assistant can describe what is currently highlighted.
              ...(selectedLinkInfo
                ? [
                    [
                      t("도식에서", "In the diagram,"),
                      t(selectedLinkInfo.isConnector ? "연결로" : "링크", selectedLinkInfo.isConnector ? "connector" : "link"),
                      selectedLinkInfo.name,
                      t("선택됨", "selected"),
                    ].join(" "),
                  ]
                : []),
            ],
            updatedAt: Date.now(),
          },
        }),
      );
    };

    publishAssistantContext();
    window.addEventListener("vissim:assistant-context-request", publishAssistantContext);
    return () => {
      window.removeEventListener("vissim:assistant-context-request", publishAssistantContext);
    };
  }, [activeDataset, network, selectedLinkInfo, selectedNodeIds, t]);

  return (
    <div className="mx-auto w-full max-w-[96rem] space-y-6 pb-16">
      <section className="px-1">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-[22px] font-semibold tracking-tight text-white">
                {t("네트워크 프리뷰", "Network Preview")}
              </h1>
              {network?.source?.view === "working" && (
                <span className="inline-flex items-center rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-[11px] font-semibold text-amber-600">
                  {t("편집본 표시 중", "Showing edited copy")}
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-slate-600">
              {t(
                "활성 VISSIM 데이터셋(.inpx)의 원좌표를 그대로 렌더링합니다.",
                "Renders the active VISSIM dataset (.inpx) straight from its raw coordinates.",
              )}
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setReloadNonce((nonce) => nonce + 1)}
            className="flex h-9 shrink-0 items-center rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600 transition hover:text-slate-900"
          >
            {t("새로고침", "Refresh")}
          </button>
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
            selectedLinkId={selectedLinkInfo?.id ?? null}
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
