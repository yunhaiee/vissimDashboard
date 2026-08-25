"use client";

// Unit #58 -- the binding's map. THE USER'S ADDITION, mid-build (recorded with
// #58 in docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도 카드 단순화 —
// 도식+시간표, 짧은 확인 (#58)"): "어느 노드인지 고르는건 지도/도식이 있으면 편할 것
// 같은데 같이 놔줘 — 대시보드 페이지에서 왼쪽에 지도 오른쪽 내용 있는 것처럼".
//
// THIS FILE DRAWS NOTHING. It is a DATA path and an absence-namer around the
// map this repo already has:
//
//   * the renderer is `components/genesis/DraftLocalMap.tsx` -- unit #29's
//     local-coordinates SVG canvas, with the node layer #58 added to it. It is
//     reused rather than reimplemented, which is also why the binding map works
//     for EVERY generation: the basemap renderer needs degrees, and a draft
//     whose census recorded no projection origin cannot be placed on the earth
//     at all (`services/genesis/geojson.py` refuses to invent one). The node
//     picker must not go dark for those drafts.
//   * the data is `fetchGenesisDraftNetwork(jobId)` -- the SAME census fetch
//     `GenesisTileMap` makes for the job-detail map. No second endpoint, no
//     second shape.
//
// THE HONESTY RULES, all three from the user's own brief:
//   1. the map shows the DRAFT'S OWN nodes, from the server's census. It never
//      invents a candidate and never places one the draft does not carry.
//   2. a click is SELECTION, not confirmation. The single 확인 button in the
//      binding section remains the only act that records an attestation, and
//      the selection state itself is OWNED BY THE PAGE -- this component holds
//      none, so the map and the candidate list beside it are two views of one
//      selection rather than two selections.
//   3. a draft with no geometry degrades to a NAMED absence line, never a blank
//      box. Three different absences, three different sentences: no census at
//      all, a census with no nodes, and a census whose fetch refused.

import { useEffect, useState } from "react";

import DraftLocalMap from "@/components/genesis/DraftLocalMap";
import { buildHighlights } from "@/components/genesis/findings";
import { failed, ok, resolve, type Loaded } from "@/lib/asyncResource";
import {
  fetchGenesisDraftNetwork,
  type GenesisDraftNetwork,
  type GenesisDraftNode,
} from "@/lib/genesisArtifacts";

type Translate = (ko: string, en: string, ja: string) => string;

/** A named absence, in the one shape all three of them share. Never a blank
 * box: a map that renders nothing looks like a map that is still loading. */
function MapAbsence({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-[15px] leading-relaxed text-slate-600">
      {children}
    </p>
  );
}

export function DraftNodePickMap({
  jobId,
  candidateNodeIds,
  candidatesStatus,
  totalNodes,
  returnedNodes,
  selectedNodeId,
  onSelectNode,
  t,
}: {
  /** The 생성 작업 whose draft is drawn. The map renders ONLY when one is
   * selected -- there is no draft to show otherwise, and a map of nothing is a
   * box that invites a click it cannot answer. */
  jobId: string;
  /** Which of the draft's nodes the SERVER offered as candidates for this
   * sheet. Narrowing, never invention: every id here must exist in the census
   * or it simply does not draw. */
  candidateNodeIds: readonly number[];
  /** unit #62 -- WHETHER a candidate search has actually run, and how.
   * `"loading"` and `"idle"` both precede any server answer, so neither may
   * render the "찾지 못했습니다" sentence: that sentence is a claim about a
   * search's RESULT, and no search has produced one yet. `"failed"` is a
   * DIFFERENT fact again (FINDING 3, refuter): a search DID run and did not
   * come back -- folding that into `"idle"`'s "no search has run yet" would
   * be its own false claim, just the opposite direction. Only `"fetched"`
   * may render the found/not-found sentences. */
  candidatesStatus: "idle" | "loading" | "fetched" | "failed";
  /** The draft's own node count and how many the server actually returned,
   * disclosed together so a real truncation (the name signal discriminated
   * and `limit` cut the rest) can be named on screen instead of absorbed.
   * `null` whenever `candidatesStatus !== "fetched"`. */
  totalNodes: number | null;
  returnedNodes: number | null;
  selectedNodeId: number | null;
  onSelectNode: (nodeId: number) => void;
  t: Translate;
}) {
  const [loaded, setLoaded] = useState<Loaded<GenesisDraftNetwork> | null>(null);
  const { data, error, loading } = resolve(loaded, jobId, { idle: jobId === "" });

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    fetchGenesisDraftNetwork(jobId)
      .then((network) => {
        if (!cancelled) setLoaded(ok(jobId, network));
      })
      .catch((caught) => {
        if (!cancelled) setLoaded(failed(jobId, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (!jobId) return null;
  if (loading) {
    return (
      <MapAbsence>
        {t("초안 네트워크를 불러오는 중입니다…", "Loading the draft network…", "草案ネットワークを読み込み中…")}
      </MapAbsence>
    );
  }
  if (error || !data) {
    return (
      <MapAbsence>
        {t(
          "이 생성 작업의 초안 네트워크를 읽지 못해 지도를 그릴 수 없습니다. 아래 후보 목록으로 선택하십시오.",
          "The draft network for this generation could not be read, so no map is drawn. Pick from the candidate list below.",
          "この生成ジョブの草案ネットワークを読めなかったため、地図は描けません。下の候補一覧から選択してください。",
        )}
        {error ? ` (${error})` : ""}
      </MapAbsence>
    );
  }

  const wanted = new Set(candidateNodeIds);
  const nodes: GenesisDraftNode[] = data.nodes.filter((node) => wanted.has(node.id));

  if (data.links.length === 0) {
    return (
      <MapAbsence>
        {t(
          "이 초안에는 그릴 도형이 없습니다 — 링크가 한 개도 없는 생성입니다.",
          "This draft has nothing to draw — the generation produced no links at all.",
          "この草案には描く図形がありません — リンクが一つもない生成です。",
        )}
      </MapAbsence>
    );
  }

  return (
    <div>
      <DraftLocalMap
        network={data}
        linksById={new Map(data.links.map((link) => [link.id, link]))}
        connectorsById={new Map(data.connectors.map((connector) => [connector.id, connector]))}
        highlights={buildHighlights([], data)}
        selectedHighlight={null}
        showNormal
        showRefuse={false}
        showWarn={false}
        onSelectLink={() => undefined}
        onSelectConnector={() => undefined}
        onClearSelection={() => undefined}
        nodes={nodes}
        selectedNodeId={selectedNodeId}
        onSelectNode={onSelectNode}
        t={t}
      />
      <p className="mt-1.5 text-[14px] leading-relaxed text-slate-500">
        {/* unit #62 -- FOUR STATES, not two. A search that has not run yet
          * (or is still in flight) is a DIFFERENT fact from a search that ran
          * and found nothing, and this line used to conflate them: it fired
          * on `nodes.length === 0` alone, which was also true the instant the
          * page loaded and before anyone had ever clicked "노드 후보
          * 불러오기". That told a reader a search had failed before one had
          * even started. FINDING 3 (refuter) added the fourth: a search that
          * ran and FAILED is not "no search has run yet" either -- one did,
          * and it did not come back. Each of the four gets its own sentence,
          * and no two may share one. */}
        {candidatesStatus === "loading"
          ? t(
              "노드 후보를 찾는 중입니다…",
              "Searching for node candidates…",
              "ノード候補を検索中…",
            )
          : candidatesStatus === "failed"
            ? t(
                "노드 후보 탐색이 실패했습니다 — 사유는 위 오류를 확인하십시오.",
                "The node candidate search failed — see the error above for the reason.",
                "ノード候補の検索に失敗しました — 理由は上のエラーをご確認ください。",
              )
            : candidatesStatus === "idle"
              ? t(
                  "아직 노드 후보를 찾지 않았습니다 — 생성 작업을 고르면 자동으로 찾습니다.",
                  "No search for node candidates has run yet — picking a generation searches automatically.",
                  "まだノード候補の検索は行われていません — 生成ジョブを選ぶと自動で検索します。",
                )
              : nodes.length === 0
                ? t(
                    "이 초안에서 이 교차로의 후보 노드를 찾지 못했습니다 — 지도에 표시할 점이 없습니다.",
                    "No candidate node for this intersection was found in this draft — there is no dot to show.",
                    "この草案でこの交差点の候補ノードは見つかりませんでした — 地図に表示する点がありません。",
                  )
                : t(
                    `점 ${nodes.length}개는 이 초안이 가진 노드입니다. 점을 누르면 선택될 뿐이며, 기록되는 것은 아래 확인 단추뿐입니다.`,
                    `The ${nodes.length} dot(s) are nodes this draft holds. Clicking one only SELECTS it; the only act that records anything is the 확인 button below.`,
                    `${nodes.length}個の点はこの草案が持つノードです。点を押しても選択されるだけで、記録されるのは下の確認ボタンだけです。`,
                  )}
      </p>
      {/* unit #62 -- THE DISCLOSED CUT. `node_candidates` truncates only when
        * its name ranking actually discriminates; when it does, this is the
        * one place the truncation is nameable instead of eight dots quietly
        * standing in for twenty-five. Absent whenever the server offered
        * every node it has (`returnedNodes === totalNodes`), which is the
        * common OSM-draft case this unit was measured against. */}
      {candidatesStatus === "fetched" &&
        totalNodes != null &&
        returnedNodes != null &&
        returnedNodes < totalNodes && (
          <p className="mt-1 text-[13px] leading-relaxed text-slate-500">
            {t(
              `초안 노드 ${totalNodes}개 중 ${returnedNodes}개만 표시합니다 — 이름이 더 겹치는 노드가 우선했습니다.`,
              `Showing ${returnedNodes} of the draft's ${totalNodes} nodes — the ones with more name overlap were prioritized.`,
              `草案ノード${totalNodes}件中${returnedNodes}件のみ表示しています — 名前の重なりが多いノードを優先しました。`,
            )}
          </p>
        )}
    </div>
  );
}
