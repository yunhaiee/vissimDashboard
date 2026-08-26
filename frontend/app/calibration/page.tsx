"use client";

// ---------------------------------------------------------------------------
// The calibration workspace (unit #50, restructured by #50-b).
//
// THE PROBLEM IT EXISTS FOR, in the user's own words: calibration was
// scattered across four surfaces — the assistant drawer, the dashboard's GEH
// table, a DOCX and a terminal — and none of them let anyone FEEL the whole
// flow: what comes first, what is finished, what is left, in the order the
// work actually happens.
//
// #50 put all of it on one screen. Driving that screen, the user found the
// second half of the same problem: "스테이션이 전부 세로로 쌓여 페이지 구조·
// 작업 구조를 파악할 수 없다" — every station stacked down one column, so the
// thirteen-step rail scrolled a reader past four other stations to reach one,
// and the page's structure (and therefore the WORK's structure) was invisible.
//
// SO THIS SCREEN IS NOW THREE PARTS, and each answers one question:
//
//   * a CONTEXT BAR that never moves — which dataset, which run, how the run
//     scored in one line, and the before/after toggle. Everything global lives
//     here and nowhere else, so no step can appear to own it.
//   * the RAIL, permanent on the left — thirteen steps folded into eight
//     station rows (lib/calibrationStations.ts), each with its steps' status
//     chips and a sky badge counting the proposals waiting there. Clicking
//     SWITCHES the station; it is not a scroll any more.
//   * ONE STATION at a time on the right — that step's evidence, what to do
//     next, and that step's own proposal cards inline.
//
// The 제안 collection panel is GONE (the user's decision): a proposal read away
// from the step it acts on is a decision made without its evidence. Every
// proposal now renders at the station its edit belongs to, and the placement is
// executed by tests, so nothing waiting for a human can become unreachable.
//
// NEW CALIBRATION LOGIC HERE: none, in either unit. Every number on this page
// was produced by a service that already existed — `reality_check` (scoring +
// #43's rungs), `verify_applied_row` (#44's Table 5-6 check), the generation
// gate, the phase-diagram store — and reached this page through the read-only
// /api/calibration routes. The approve button posts to the endpoint the drawer
// has always used. This file arranges and labels; it decides nothing.
//
// WHAT IT ABSORBED. `/base-model` was its own nav destination until the user's
// 2026-08-19 decision that base-model validation IS the scoring step. The page
// moved here whole (components/calibration/BaseModelStation.tsx) rather than
// being partially reimplemented, and `/base-model` 308s to this route so
// bookmarks still land on the work. #50-b gives it a tab inside the scoring
// station rather than a place below it: it is a screen-sized body of evidence,
// and appending it to the node table reproduced the very stack this unit is
// undoing.
//
// WHAT #50-c CHANGED: NUMBERS FIRST, PROSE FOLDED. The user drove #50-b's real
// screen and reported the next thing wrong with it — "거의 모든 fine text는
// 크게/직관적 대안/없애", and specifically, of the mono provenance lines
// (`saturation_flow.verify_applied_row · op §5.2.5.1, Table 5-6, p.187` and its
// kind): "이런것도 다 지워 — 화면을 포화시켜". Measured on that screen, every
// station panel opened with two to four sentences of RULE before the first
// number, and every step box closed with a mono line naming a source file and a
// paragraph number. So the surface of each tab is now numbers, verdicts, and
// ONE next-action line; every rule, citation and doctrine sentence lives in a
// `<details>` or an ⓘ. ZERO INFORMATION LOSS is the constraint that makes this
// a restructure rather than a cull: every sentence and every citation string
// that left the surface is reachable, verbatim, one click away — the audit
// trail is the product, and a folded citation is still a citation.
//
// WHAT #50-d CHANGED: ACTION FIRST, EDUCATION EVICTED (the user, 2026-08-20).
// Driving #50-c's screen the user named what it still would not answer — "뭘
// 해야 할지 모르겠다" — and the decision behind this unit: "화면은 사용자에게
// 행동을 빚졌지 교육을 빚진 게 아니다". So:
//
//   * the FIRST line of this page is now the one thing to do next, and it is a
//     BUTTON, derived from the run selection and the server's own NEXT step;
//   * every doctrine, guard and education sentence LEFT this file. They are not
//     deleted — each one is verbatim in docs/product/calibration-doctrine.md
//     (the single source of truth), rendered at /docs from
//     lib/help-content.tsx's `calibrationHelpEntries`, and reachable to the
//     assistant through the RAG corpus rooted at docs/. The contract suite pins
//     all three copies together, and pins every one of them ABSENT from here.
//
// What stayed is what a verdict or an action is made of: the per-step basis
// line, the status chips, the numbers, the 수동 확인 영역 station's one-line
// promise, and the proposal cards. A sentence that says what this product WILL
// NOT DO is a verdict and stays; a sentence that teaches how it works is at
// /docs now.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import BaseValidationMap, { type BaseValidationMapNode } from "@/components/BaseValidationMap";
// `InfoTip` is no longer imported here, and that is the state #50-d ends in:
// this screen carried seven doctrine tooltips and now carries none. The
// primitive itself is untouched — the genesis screens still use it, and it is
// the right control for a phrase beside a control. It is the wrong control for
// a page of background, which is what these seven were.
import { useLanguage } from "@/components/LanguageProvider";
import BaseModelStation from "@/components/calibration/BaseModelStation";
import BehaviourStation from "@/components/calibration/BehaviourStation";
import FlowRail, {
  ParameterSearchTrack,
  circledStep,
  pick,
  stationLabel,
  statusChipClass,
  statusLabel,
  stepLabel,
} from "@/components/calibration/FlowRail";
import NodeScoreTable, { NodeScoreDetail, nodeGehPasses } from "@/components/calibration/NodeScoreTable";
import StationProposals, { usePendingProposals } from "@/components/calibration/StationProposals";
import {
  CALIBRATION_STATIONS,
  DEFAULT_STATION,
  groupSteps,
  stationForStep,
  type CalibrationStationId,
} from "@/lib/calibrationStations";
import {
  failed,
  ok,
  resolve,
  resolveSelection,
  type Loaded,
} from "@/lib/asyncResource";
import {
  fetchActiveDataset,
  fetchCalibrationAppliedRows,
  fetchCalibrationDemandTargets,
  fetchCalibrationRail,
  fetchCalibrationRealityCheck,
  fetchScenarios,
  postCalibrationStepAttestation,
  revokeCalibrationStepAttestation,
  type ActiveDataset,
  type CalibrationAppliedRows,
  type CalibrationDemandTargets,
  type CalibrationRail,
  type CalibrationRailStep,
  type CalibrationRealityCheck,
  type CalibrationRealityNode,
  type ScenarioMeta,
} from "@/lib/api";

type Translate = (ko: string, en: string, ja?: string) => string;

// #53's station actions reach the drawer the same way app/health/page.tsx,
// components/dashboard/CurationBar.tsx and components/calibration/NodeScoreTable.tsx
// already do -- the globally-mounted FloatingAssistant (components/AppShell.tsx)
// listens for this window event. Declared here rather than imported for the
// reason all three of those files state in their own comments: the event NAME is
// the contract between independent dispatchers, and a cross-file test
// (dashboard-contract.test.ts) is what holds the four spellings together.
const ASSISTANT_HANDOFF_EVENT = "vissim:assistant-handoff";

function requestAgentHandoff(prompt: string) {
  window.dispatchEvent(new CustomEvent(ASSISTANT_HANDOFF_EVENT, { detail: { prompt } }));
}

/** The assistant prompt ⑪'s station opens with.
 *
 * The step's NUMBER and the step's LABEL are the server's (`stepLabel` reads
 * `label_ko`/`label`/`label_ja`, which the payload marks `label_source`); the
 * wrapper sentence is the only thing added, in all three languages, so the
 * drawer opens already knowing which guideline step the user is standing on.
 * Same construction as NodeScoreTable's `buildNextActionPrompt`. */
function buildRouteSplitPrompt(step: CalibrationRailStep, lang: string, t: Translate): string {
  const label = stepLabel(step, lang);
  const number = circledStep(step.number);
  return t(
    `보정 ${number} ${label} 단계를 진행하려고 합니다. 이번 실행에서 경로 분할(route split)을 어떻게 확인하고 조정해야 하는지 알려주세요.`,
    `I am working on calibration step ${number} ${label}. Tell me how to check and adjust the route splits on this run.`,
    `補正の${number} ${label}段階を進めます。今回の実行で経路分割(route split)をどう確認し調整すべきか教えてください。`,
  );
}

/** The reads with no parameters still carry a request key — `resolve` answers
 * "is what I hold the answer to THIS request", and a mount-only read is just
 * the request that never varies. */
const APPLIED_ROWS_KEY = "applied-rows";
const DATASET_KEY = "active-dataset";
const SCENARIOS_KEY = "scenarios";

/** "The rail has no steps" — one array, so an absent rail is a stable value
 * rather than a new empty array on every render. */
const NO_STEPS: CalibrationRailStep[] = [];

/** Same reason, same shape, for #65: "no runs yet" and "haven't asked yet"
 * are different facts, and only `resolve`'s `loading`/`error`/`data` triad
 * can tell them apart -- a plain `useState<ScenarioMeta[]>([])` cannot, since
 * both start and end at `[]`. */
const NO_SCENARIOS: ScenarioMeta[] = [];

/** The report's nodes in the shape `BaseValidationMap` already speaks, so the
 * table and the map cross-highlight through one contract rather than two. */
function toMapNodes(report: CalibrationRealityCheck | null): BaseValidationMapNode[] {
  if (!report?.nodes) return [];
  return report.nodes.map((node) => {
    const observed = typeof node.observed_total === "number" ? node.observed_total : null;
    const modelled = typeof node.modelled_total === "number" ? node.modelled_total : null;
    const diff = observed != null && modelled != null ? modelled - observed : null;
    return {
      nodeId: String(node.node_id),
      nodeName: String(node.node_name ?? node.node_id),
      observedTotal: observed,
      modelTotal: modelled,
      diff,
      pctError: observed != null && observed !== 0 && diff != null ? (diff / observed) * 100 : null,
      geh: typeof node.node_total_geh === "number" ? node.node_total_geh : null,
      // This report grades node TOTALS, so one node contributes exactly one
      // graded comparison. Putting a movement count here would borrow a number
      // from a different tally — the movement counts live in the report's own
      // coverage line under the table.
      validCount: node.node_total_geh == null ? 0 : 1,
      missingCount: node.node_total_geh == null ? 1 : 0,
      issueScore: typeof node.node_total_geh === "number" ? node.node_total_geh : 0,
    };
  });
}

/**
 * The nodes a station has business showing, decided by the SERVER.
 *
 * `diagnosis.next_action.mrwa_step` is geh_ladder.py's own answer to "which
 * §2.12.1 step authorises the fix for this node" — so a station can ask for its
 * own nodes without this file classifying a single one of them. A node whose
 * next action carries no step (SUPPLY_MISSING_EVIDENCE) belongs to no step and
 * appears at none; it is still in the scoring table, which is the complete list.
 */
function nodesForSteps(
  report: CalibrationRealityCheck | null,
  steps: CalibrationRailStep[],
): CalibrationRealityNode[] {
  if (!report?.nodes || steps.length === 0) return [];
  const wanted = new Set(steps.map((step) => step.number));
  return report.nodes.filter((node) => {
    const step = node.diagnosis?.next_action?.mrwa_step;
    return typeof step === "number" && wanted.has(step);
  });
}

export default function CalibrationWorkspacePage() {
  const { t, lang } = useLanguage();

  // #65: loading / failed / "asked and there are none" are three different
  // facts about the run list, and only the `Loaded<T>` triad this file already
  // uses for the rail, the report and the dataset can keep them apart -- see
  // `NO_SCENARIOS` above for why a plain `useState([])` could not.
  const [scenariosLoaded, setScenariosLoaded] = useState<Loaded<ScenarioMeta[]> | null>(null);
  const {
    data: scenariosData,
    error: scenariosError,
    loading: scenariosLoading,
  } = resolve(scenariosLoaded, SCENARIOS_KEY);
  const scenarios = scenariosData ?? NO_SCENARIOS;
  // The one fact the banner and the picker both read: the fetch RESOLVED,
  // it did not FAIL, and what came back was empty. Loading and error are
  // both false here on purpose -- neither is evidence of "zero runs".
  const scenariosEmpty = !scenariosLoading && !scenariosError && scenarios.length === 0;
  const [scenarioId, setScenarioId] = useState<string>("");
  const [compareOn, setCompareOn] = useState(false);
  const [compareIntent, setCompareIntent] = useState<string>("");
  // Bumped when an approval lands, so the rail re-reads. A nonce rather than an
  // imperative reload call: the rail's request key is what decides whether what
  // we hold is current, so "please read again" has to be a change to that key.
  //
  // #54 widened what it drives, and had to: naming a vehicle input changes the
  // REPORT (a node's outcome, its D4 verdict and its rung are all re-derived
  // from the store on the next read), not only the rail. So the nonce is now
  // part of the report's and the demand-target list's request keys too — a
  // screen that re-read only the rail would show the mapping recorded and the
  // node still 분류 불가 beside it.
  const [railNonce, setRailNonce] = useState(0);

  // Each remote resource is ONE piece of state carrying the request key it
  // describes (see `Loaded` in lib/asyncResource.ts). `loading`, the error and
  // the reset-when-the-selection-changes all fall out of comparing that key
  // with the current one DURING RENDER, so no effect sets anything before its
  // own call -- this app's one convention for talking to the server.
  const railKey = `${scenarioId}|${railNonce}`;
  const [railLoaded, setRailLoaded] = useState<Loaded<CalibrationRail> | null>(null);
  const { data: rail, error: railError, loading: railLoading } = resolve(railLoaded, railKey);

  const reportKey = `${scenarioId}|${railNonce}`;
  const [reportLoaded, setReportLoaded] = useState<Loaded<CalibrationRealityCheck> | null>(null);
  const {
    data: report,
    error: reportError,
    loading: reportLoading,
  } = resolve(reportLoaded, reportKey, { idle: !scenarioId });

  // #54's picker data: which vehicle inputs each node's counted approaches
  // carry, and which mapping stands. Read once per nonce and threaded DOWN to
  // the node detail rather than fetched inside it — the detail renders in two
  // places on this screen, and two independent fetches would let the same node
  // offer two different candidate lists.
  const demandKey = `demand|${railNonce}`;
  const [demandLoaded, setDemandLoaded] = useState<Loaded<CalibrationDemandTargets> | null>(null);
  const { data: demandTargets } = resolve(demandLoaded, demandKey);

  const [datasetLoaded, setDatasetLoaded] = useState<Loaded<ActiveDataset> | null>(null);
  const { data: dataset, error: datasetError } = resolve(datasetLoaded, DATASET_KEY);

  const [appliedLoaded, setAppliedLoaded] = useState<Loaded<CalibrationAppliedRows> | null>(null);
  const {
    data: appliedRows,
    error: appliedError,
    loading: appliedLoading,
  } = resolve(appliedLoaded, APPLIED_ROWS_KEY);

  // The comparison run is DERIVED from the toggle and the run list, never set
  // by an effect: turning the toggle on picks the first other run, switching
  // the scored run drops a comparison that no longer exists, and neither costs
  // a frame in which the heading and the table disagree.
  const otherScenarios = useMemo(
    () => scenarios.filter((scenario) => scenario.id !== scenarioId),
    [scenarios, scenarioId],
  );
  const compareId = compareOn
    ? (resolveSelection(compareIntent || null, otherScenarios, (scenario) => scenario.id) ?? "")
    : "";

  const [compareLoaded, setCompareLoaded] = useState<Loaded<CalibrationRealityCheck> | null>(null);
  const { data: compareReport, error: compareError } = resolve(compareLoaded, compareId, {
    idle: !compareId,
  });

  // Unit #51's track, read off the rail the server already sent. Derived, never
  // fetched separately: two requests would let the behaviour station and the
  // rail show different lock states on the same screen.
  const parameterSearchTrack =
    rail?.steps.find((step) => step.number === 12)?.tracks?.[0] ?? null;

  // #51's live runner: WHICH CONVERSATION a finished search is remembered
  // against.
  //
  // MEASURED, 2026-08-21. The searched-parameter broker is keyed by agent
  // session (backend/services/agent/searched_behaviour_broker.py), and the only
  // session id a browser ever holds is the drawer's — minted by the backend on a
  // turn and returned as `done.response.session_id`. FloatingAssistant already
  // broadcasts every change of it on `vissim:agent-session`, and three pages
  // (app/dashboard, app/network, app/downloads) already mirror it exactly this
  // way. So this is that same listener, not a fourth mechanism.
  //
  // ITS ONE LIMIT, stated: the broadcast fires on CHANGE, so a page mounted
  // after the drawer's last turn starts at `null` until the next one. That is
  // not papered over here — with no session the start POST is refused BY NAME
  // (NO_AGENT_SESSION) with the sentence that says to open the drawer first,
  // which is strictly better than starting an hour of Vissim whose winner
  // nothing could ever propose.
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null);

  const [activeNodeId, setActiveNodeId] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [activeStep, setActiveStep] = useState<number | null>(null);
  const [stationIntent, setStationIntent] = useState<CalibrationStationId | null>(null);
  const [scoringView, setScoringView] = useState<"nodes" | "workbook">("nodes");

  // Which station is open is a SELECTION, resolved during render against the
  // list of stations that exist (lib/asyncResource.ts's `resolveSelection`) —
  // never an effect that corrects itself one painted frame later.
  const activeStation =
    resolveSelection(stationIntent, CALIBRATION_STATIONS, (station) => station) ?? DEFAULT_STATION;

  // The rail's steps are what resolve a proposal's station (a manual-check
  // step's card belongs in 수동 확인 영역 because the SERVER flagged that step
  // manual). The empty case is a module constant rather than a fresh `[]`, so
  // the derivations below re-run when the RAIL changes and not on every render.
  const railSteps = rail?.steps ?? NO_STEPS;

  // #50-d ①: THE ONE THING TO DO NEXT — the server's, not this file's.
  //
  // `calibration_rail.py` emits the thirteen steps in §2.12.1's own order (line
  // 996 iterates `mrwa_reference.CALIBRATION_STEPS_2_12_1`), so the FIRST step
  // carrying the server's NEXT status is the earliest outstanding one in the
  // guideline's order — found by a `find`, with no sorting, no comparison and no
  // status of this file's own. `null` when the rail has not loaded or nothing is
  // NEXT, and the banner then renders nothing at all: an invented todo is the one
  // lie a line in that position can tell.
  const nextStep = railSteps.find((step) => step.status === "NEXT") ?? null;

  // The run selector lives in the context bar below the banner. When no run is
  // chosen the banner IS the way to that control, so it focuses it — a ref
  // rather than a lookup, because this screen has no element ids to look up.
  const runSelectRef = useRef<HTMLSelectElement>(null);

  const handleProposalResolved = useCallback(() => setRailNonce((nonce) => nonce + 1), []);
  const proposals = usePendingProposals({ steps: railSteps, onResolved: handleProposalResolved });

  useEffect(() => {
    let cancelled = false;
    fetchScenarios()
      .then((list) => {
        if (!cancelled) setScenariosLoaded(ok(SCENARIOS_KEY, list));
      })
      .catch((caught) => {
        if (!cancelled) setScenariosLoaded(failed(SCENARIOS_KEY, caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Mirror the floating assistant's current agent session id -- see
  // `agentSessionId`'s own note above, and FloatingAssistant.tsx's
  // "vissim:agent-session" dispatch effect.
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

  useEffect(() => {
    let cancelled = false;
    fetchActiveDataset()
      .then((active) => {
        if (!cancelled) setDatasetLoaded(ok(DATASET_KEY, active));
      })
      .catch((caught) => {
        if (!cancelled) setDatasetLoaded(failed(DATASET_KEY, caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCalibrationAppliedRows()
      .then((rows) => {
        if (!cancelled) setAppliedLoaded(ok(APPLIED_ROWS_KEY, rows));
      })
      .catch((caught) => {
        if (!cancelled) setAppliedLoaded(failed(APPLIED_ROWS_KEY, caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCalibrationRail({ scenarioId: scenarioId || null })
      .then((payload) => {
        if (!cancelled) setRailLoaded(ok(railKey, payload));
      })
      .catch((caught) => {
        if (!cancelled) setRailLoaded(failed(railKey, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [railKey, scenarioId]);

  useEffect(() => {
    if (!scenarioId) return;
    let cancelled = false;
    fetchCalibrationRealityCheck({ scenarioId })
      .then((payload) => {
        if (!cancelled) setReportLoaded(ok(reportKey, payload));
      })
      .catch((caught) => {
        if (!cancelled) setReportLoaded(failed(reportKey, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [reportKey, scenarioId]);

  useEffect(() => {
    let cancelled = false;
    fetchCalibrationDemandTargets()
      .then((payload) => {
        if (!cancelled) setDemandLoaded(ok(demandKey, payload));
      })
      .catch((caught) => {
        if (!cancelled) setDemandLoaded(failed(demandKey, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [demandKey]);

  useEffect(() => {
    if (!compareId) return;
    let cancelled = false;
    fetchCalibrationRealityCheck({ scenarioId: compareId })
      .then((payload) => {
        if (!cancelled) setCompareLoaded(ok(compareId, payload));
      })
      .catch((caught) => {
        if (!cancelled) setCompareLoaded(failed(compareId, caught));
      });
    return () => {
      cancelled = true;
    };
  }, [compareId]);

  // #56 (a): the page context the drawer reads (dashboard/page.tsx and
  // network/page.tsx already publish theirs the same way -- see
  // components/FloatingAssistant.tsx's own docstring on the "vissim:page-
  // context" / "vissim:assistant-context-request" pair being the contract
  // between independent dispatchers). Before this unit /calibration published
  // NONE: a user who typed or pasted the SCORED run's on-screen name into the
  // drawer (the picker below shows `scenario.name`, e.g. "agent_run_…") got a
  // named, correct refusal from the tools -- they all speak `scenario.id`
  // (e.g. "SIM_…"), never the display name -- but the assistant had no way to
  // answer without asking, because nothing on this page told it which run was
  // already selected.
  //
  // `selectedScenarioIds` is exactly the picker's OWN <option value>, i.e.
  // the tool-vocabulary id (see the `<select>` below) -- NEVER the name a
  // human reads. `services/network_selection.py`'s `build_selection_context`
  // passes it straight to `services.results_reader`/the agent's own
  // `compare_scenarios` tool, and `services/agent/planner.py` renders it
  // verbatim into the prompt as "- scenarios: <id>" -- so a Table 5-6 or
  // reality-check question about "this run" is answered from the id already
  // in context, never a name the assistant would have to translate or ask
  // about.
  useEffect(() => {
    const publishAssistantContext = () => {
      const selectedScenario = scenarios.find((scenario) => scenario.id === scenarioId) ?? null;
      window.dispatchEvent(
        new CustomEvent("vissim:page-context", {
          detail: {
            route: "/calibration",
            label: "Calibration workspace context",
            selectedScenarioIds: scenarioId ? [scenarioId] : [],
            selectedScenarioNames: selectedScenario ? [selectedScenario.name] : [],
            selectedNodeIds,
            selectedNodeNames: selectedNodeIds.map(
              (nodeId) =>
                report?.nodes?.find((node) => node.node_id === nodeId)?.node_name ?? `Node ${nodeId}`,
            ),
            dataset: dataset
              ? { filename: dataset.filename, source: dataset.source, revision: dataset.revision }
              : null,
            notes: [
              scenarioId
                ? t(
                    `채점 중인 실행: ${selectedScenario?.name ?? scenarioId} (id: ${scenarioId})`,
                    `Run being scored: ${selectedScenario?.name ?? scenarioId} (id: ${scenarioId})`,
                    `採点中の実行: ${selectedScenario?.name ?? scenarioId} (id: ${scenarioId})`,
                  )
                : t(
                    "아직 채점할 실행이 선택되지 않았습니다.",
                    "No run has been selected to score yet.",
                    "採点する実行がまだ選択されていません。",
                  ),
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
  }, [scenarioId, scenarios, selectedNodeIds, report, dataset, t]);

  const handleNodeClick = useCallback((nodeId: string) => {
    setActiveNodeId((current) => (current === nodeId ? null : nodeId));
    setSelectedNodeIds((current) => (current.includes(nodeId) ? current : [...current, nodeId]));
  }, []);

  const handleSelectStation = useCallback((station: CalibrationStationId) => {
    setStationIntent(station);
    setActiveStep(null);
  }, []);

  // A step clicked inside an expanded group opens its station AND says which
  // step the reader came in on, so the panel can mark it. The station is the
  // SHARED rule's answer, not a second one written here.
  const handleSelectStep = useCallback((step: CalibrationRailStep) => {
    setActiveStep(step.number);
    setStationIntent(stationForStep(step));
  }, []);

  // #57's LADDER_NOT_EXHAUSTED chip: switch to the scoring station's node tab
  // AND select the node, so the reader lands on its diagnosis card rather than
  // an empty grid. Built from the two handlers this screen already had —
  // `handleSelectStation` (the rail's own station switch) and `handleNodeClick`
  // (the SAME toggle NodeScoreTable and BaseValidationMap call) — plus the one
  // thing neither of those covers: forcing the tab off "기준 모델 검증" back to
  // "노드 채점", since a node's diagnosis card only renders on that tab.
  const handleSelectScoringNode = useCallback(
    (nodeId: string) => {
      setScoringView("nodes");
      handleSelectStation("scoring");
      handleNodeClick(nodeId);
    },
    [handleSelectStation, handleNodeClick, setScoringView],
  );

  // ONE grouping, read by the rail's rows and by the open station alike — two
  // groupings would let a row point at a station that does not show that step.
  // Derived plainly during render: it is a fold of at most thirteen rows, and
  // a manual memo over an array the compiler cannot prove immutable buys
  // nothing but a bail-out of the whole component's optimization.
  const grouped = groupSteps(railSteps);
  const stationSteps = grouped.byStation[activeStation];

  const mapNodes = useMemo(() => toMapNodes(report), [report]);
  const activeNode: CalibrationRealityNode | null = useMemo(
    () => report?.nodes?.find((node) => node.node_id === activeNodeId) ?? null,
    [report, activeNodeId],
  );

  // The one-line verdict in the context bar. It is the SAME comparison the
  // scoring table's rows make (`nodeGehPasses`, imported rather than repeated)
  // against the report's own threshold, and it states the ungradable count
  // instead of quietly dropping those nodes out of the denominator.
  const gradedNodes = (report?.nodes ?? []).filter(
    (node) => nodeGehPasses(node, report?.geh_threshold) !== null,
  );
  const passedNodes = gradedNodes.filter((node) => nodeGehPasses(node, report?.geh_threshold) === true);
  const ungradedCount = (report?.nodes?.length ?? 0) - gradedNodes.length;

  // Before/after: the SAME node in two runs, GEH against GEH. Only nodes that
  // both reports actually graded appear — a node one run could not grade has
  // no delta, and printing a dash beside a number invites reading it as zero.
  const deltas = useMemo(() => {
    if (!report?.nodes || !compareReport?.nodes) return [];
    const before = new Map(compareReport.nodes.map((node) => [node.node_id, node]));
    return report.nodes
      .map((node) => {
        const other = before.get(node.node_id);
        if (!other) return null;
        if (typeof node.node_total_geh !== "number" || typeof other.node_total_geh !== "number") return null;
        return {
          nodeId: node.node_id,
          nodeName: String(node.node_name ?? node.node_id),
          before: other.node_total_geh,
          after: node.node_total_geh,
          delta: node.node_total_geh - other.node_total_geh,
        };
      })
      .filter((row): row is NonNullable<typeof row> => row !== null);
  }, [report, compareReport]);

  const stationProposals = proposals.grouped.byStation[activeStation];
  const otherProposalCount = proposals.grouped.total - stationProposals.length;

  // Node context for the open station: the report's own nodes whose next action
  // cites one of this station's steps. Empty is the normal case for ① and ⑨,
  // and an empty map is never drawn — see `NodeContext`.
  const stationNodes = nodesForSteps(report, stationSteps);

  // ⑫ has no per-node diagnosis of its own (geh_ladder cites steps 6, 7, 10 and
  // 11), so its node context is the search plan's OWN split — which is also the
  // server's, and only exists while the track is unlocked.
  const behaviourNodeIds = parameterSearchTrack?.card
    ? [
        ...parameterSearchTrack.card.plan.calibration_nodes,
        ...parameterSearchTrack.card.plan.holdout_nodes,
      ]
    : [];

  return (
    <div className="flex flex-col gap-3 pb-16">
      <header>
        <h1 className="text-[22px] font-bold text-slate-900">
          {/* What this screen IS was an ⓘ here until #50-d. It is background a
              reader needs once, so it is at /docs now (calibrationHelpEntries),
              and the line under this heading states what to DO instead. */}
          {t("보정 워크스페이스", "Calibration workspace", "補正ワークスペース")}
        </h1>
      </header>

      {/* ---------------------------------------------------------------- */}
      {/* #50-d ①: 지금 할 일 — ONE LINE, ABOVE EVERYTHING, AND A BUTTON.    */}
      {/*                                                                   */}
      {/* Two states and a silence. No run chosen → the action is to choose  */}
      {/* one, and pressing this puts the cursor in the selector below. A    */}
      {/* run chosen → the earliest step the SERVER marked NEXT, worded by   */}
      {/* `stepLabel` (the rail's own helper, so the two can never disagree) */}
      {/* and pressed through `handleSelectStep`, the same path a rail row   */}
      {/* takes. Rail absent or nothing NEXT → nothing renders.             */}
      {/*                                                                   */}
      {/* Sky, because on this screen sky IS "do this one" — the same accent */}
      {/* the NEXT chip and the proposal badge wear (the rail's three-colour */}
      {/* rule). `text-sky-600`, never 700: globals.css remaps the sky text  */}
      {/* ramp only to 600, and a 700 here would land dark-blue-on-navy.     */}
      {/*                                                                   */}
      {/* EXCEPTION (unit #67): the zero-runs branch below wears bg-accent,  */}
      {/* not sky. It is the one of these four states where clicking IS the */}
      {/* fix for the absence just named, not a prompt toward a choice still */}
      {/* to make -- so it earns ConfirmDialog's stronger filled idiom       */}
      {/* instead of the rail's sky "do this one" mark.                     */}
      {/* ---------------------------------------------------------------- */}
      {!scenarioId ? (
        // #65: "zero runs" is a fact only the RESOLVED, ERROR-FREE fetch gets to
        // state. While the list is still in flight, this stays the neutral
        // prompt below -- that is not evidence of absence. The fetch FAILING is
        // its own, THIRD absence (orchestrator correction, 2026-08-26): the
        // original two-branch shape let a failed fetch fall through to the same
        // neutral "choose a run" prompt the empty <select> could never satisfy --
        // the exact dead-end this unit exists to close, just moved one branch
        // over. `scenariosError` and `scenariosEmpty` (both reused on the
        // `<select>` placeholder below) are the two places truth is decided, so
        // the banner and the picker cannot disagree about which absence this is.
        scenariosError ? (
          <div className="flex w-full items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-left text-[17px] font-semibold leading-7 text-rose-700">
            {t(
              "실행 목록을 불러오지 못했습니다 — 새로고침하거나 서버 상태를 확인하세요",
              "Could not load the run list — refresh the page, or check the server",
              "実行一覧を読み込めませんでした — 再読み込みするか、サーバーの状態を確認してください",
            )}
          </div>
        ) : scenariosEmpty ? (
          // Unit #67 (2026-08-26 DECISIONS): a filled CTA, not the neutral
          // outline the "choose a run" and "do this now" branches below keep
          // -- this is the one branch where clicking is the actual fix for
          // the absence just named, so it wears the same
          // bg-accent/hover:bg-accent-deep/text-white idiom ConfirmDialog's
          // primary action and the floating assistant's launcher use. A
          // reader should not have to guess that a sentence is a button.
          <Link
            href="/simulation"
            className="flex w-full items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-left text-[17px] font-semibold leading-7 text-white shadow-sm transition hover:bg-accent-deep"
          >
            {t(
              "이 데이터셋에는 완료된 실행이 없습니다 — ③ 시뮬레이션에서 먼저 실행하세요",
              "No completed runs in this dataset — run one on ③ Simulation first",
              "このデータセットには完了した実行がありません — まず③シミュレーションで実行してください",
            )}
          </Link>
        ) : (
          <button
            type="button"
            onClick={() => runSelectRef.current?.focus()}
            className="flex w-full items-center gap-2 rounded-xl border border-sky-300 bg-sky-50 px-4 py-2.5 text-left text-[17px] font-semibold leading-7 text-sky-600 transition hover:border-sky-400"
          >
            {t("채점할 실행을 선택하세요", "Choose a run to score", "採点する実行を選択してください")}
          </button>
        )
      ) : nextStep ? (
        <button
          type="button"
          onClick={() => handleSelectStep(nextStep)}
          className="flex w-full items-center gap-2 rounded-xl border border-sky-300 bg-sky-50 px-4 py-2.5 text-left text-[17px] font-semibold leading-7 text-sky-600 transition hover:border-sky-400"
        >
          <span>{t("지금 할 일:", "Do this now:", "今やること:")}</span>
          <span className="font-mono">{circledStep(nextStep.number)}</span>
          <span>{stepLabel(nextStep, lang)}</span>
        </button>
      ) : null}

      {/* ---------------------------------------------------------------- */}
      {/* CONTEXT BAR — everything that belongs to no single step.          */}
      {/* ---------------------------------------------------------------- */}
      <section className="flex flex-wrap items-end gap-x-4 gap-y-2 rounded-xl border border-slate-200 bg-surface px-4 py-3">
        <div className="flex flex-col gap-0.5">
          <span className="whitespace-nowrap text-[14px] font-semibold text-slate-600">
            {t("데이터셋", "Dataset", "データセット")}
          </span>
          {/* The dataset's NAME stays on the surface and got bigger: it is the
              identity of everything below it, and #50-b's own pin exists so no
              step can appear to own it. Mono, because a filename is one. */}
          <span className="font-mono text-[14px] text-slate-700">
            {dataset
              ? dataset.filename
              : datasetError
                ? t("확인할 수 없음", "could not be read", "確認できません")
                : t("확인 중...", "checking...", "確認中...")}
          </span>
        </div>

        <label className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("채점할 실행", "Run to score", "採点する実行")}</span>
          <select
            ref={runSelectRef}
            value={scenarioId}
            onChange={(event) => {
              setScenarioId(event.target.value);
              setActiveNodeId(null);
              setSelectedNodeIds([]);
            }}
            className="h-9 min-w-[220px] rounded-md border border-slate-300 bg-surface px-2 text-[14px] text-slate-900"
          >
            <option value="">
              {scenariosError
                ? t("목록을 불러오지 못함", "Couldn't load list", "リスト読み込み失敗")
                : scenariosEmpty
                  ? t("완료된 실행 없음", "No completed runs", "完了した実行なし")
                  : t("선택하세요", "Choose a run", "選択してください")}
            </option>
            {scenarios.map((scenario) => (
              <option key={scenario.id} value={scenario.id}>
                {scenario.name}
              </option>
            ))}
          </select>
        </label>

        <div className="flex flex-col gap-0.5">
          <span className="text-[14px] font-semibold text-slate-600">
            {t("종합 판정", "Overall verdict", "総合判定")}
          </span>
          <span className="text-[15px] font-semibold leading-6 text-slate-800">
            {!scenarioId
              ? t("실행을 선택하세요.", "Pick a run.", "実行を選択してください。")
              : reportLoading
                ? t("채점하는 중...", "Scoring...", "採点中...")
                : !report
                  ? t("채점 결과 없음", "no scoring result", "採点結果なし")
                  : report.status !== "CHECKED"
                    ? pick(report.message, lang)
                    : gradedNodes.length === 0
                      ? t("등급이 매겨진 노드가 없습니다.", "No node could be graded.", "評価されたノードがありません。")
                      : t(
                          `${gradedNodes.length}노드 중 ${passedNodes.length} 합격` +
                            (ungradedCount > 0 ? ` · 등급 불가 ${ungradedCount}` : ""),
                          `${passedNodes.length} of ${gradedNodes.length} nodes pass` +
                            (ungradedCount > 0 ? ` · ${ungradedCount} ungradable` : ""),
                          `${gradedNodes.length}ノード中${passedNodes.length}件合格` +
                            (ungradedCount > 0 ? ` · 評価不可${ungradedCount}` : ""),
                        )}
            {report?.status === "CHECKED" && typeof report.geh_threshold === "number" && (
              <span className="ml-1.5 rounded-full border border-slate-300 bg-surface px-2 py-0.5 text-[14px] font-normal text-slate-600">
                GEH <span className="font-mono font-semibold text-slate-800">{report.geh_threshold.toFixed(1)}</span>
              </span>
            )}
          </span>
        </div>

        <div className="flex flex-col gap-1 text-[14px] text-slate-600">
          <span className="font-semibold">{t("전후 비교", "Before/after", "前後比較")}</span>
          <div className="flex items-center gap-2">
            {/* SELECTED chip: navy fill + sky TEXT. `text-sky-600`, never 700+ --
                globals.css's dark @theme remaps `bg-sky-50` to navy (#032042) but
                remaps the text ramp only up to sky-600, so a 700 here keeps
                Tailwind's stock light-mode #0369a1 and renders dark-blue-on-navy.
                Same trap the genesis pages already fixed; every selected chip on
                this screen (and FlowRail's NEXT chip and proposal badge) follows
                this one rule. The border stays sky-300 on purpose -- stock #7dd3fc
                reads as a light hairline on dark, which is the selected look. */}
            <button
              type="button"
              onClick={() => setCompareOn((on) => !on)}
              aria-pressed={compareOn}
              disabled={otherScenarios.length === 0}
              className={`inline-flex h-9 items-center rounded-md border px-3 text-[14px] transition ${
                compareOn
                  ? "border-sky-300 bg-sky-50 text-sky-600"
                  : "border-slate-300 bg-surface text-slate-700 hover:bg-slate-50"
              } ${otherScenarios.length === 0 ? "cursor-not-allowed opacity-60" : ""}`}
            >
              {compareOn ? t("비교 켜짐", "Comparing", "比較オン") : t("비교 꺼짐", "Off", "比較オフ")}
            </button>
            {compareOn && (
              <select
                value={compareId}
                onChange={(event) => setCompareIntent(event.target.value)}
                className="h-9 min-w-[200px] rounded-md border border-slate-300 bg-surface px-2 text-[14px] text-slate-900"
              >
                {otherScenarios.map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- */}
      {/* RAIL (permanent) + ONE STATION.                                   */}
      {/* ---------------------------------------------------------------- */}
      <div className="grid items-start gap-3 lg:grid-cols-[minmax(240px,300px)_minmax(0,1fr)]">
        <FlowRail
          rail={rail}
          loading={railLoading}
          error={railError}
          grouped={grouped}
          proposalCounts={proposals.grouped.counts}
          activeStation={activeStation}
          onSelectStation={handleSelectStation}
          onSelectStep={handleSelectStep}
        />

        <section className="min-w-0 rounded-xl border border-slate-200 bg-surface px-4 py-3.5">
          <header className="flex flex-wrap items-baseline justify-between gap-2">
            {/* "채점·진단 is not one of §2.12.1's thirteen" was an ⓘ here until
                #50-d. It explains the guideline rather than this run, so it is
                at /docs with the rest of the doctrine. */}
            <h2 className="text-[17px] font-bold text-slate-900">
              {stationLabel(activeStation, t)}
            </h2>
          </header>

          {/* Every step this station carries: the server's status, the server's
              basis sentence, and — since #50-c, in a fold — the artifact that
              answered. This is the half of the rail that moved here; a state
              belongs beside the evidence that produced it, not on a navigation
              row. The BASIS stays on the surface because it is the verdict's
              justification, which is what "no DONE from silence" is made of;
              the `source · citation` line beneath it was provenance, and
              provenance is what the user's 2026-08-20 decision moved one click
              away. Both are still here, whole. */}
          {stationSteps.map((step) => (
            <div
              key={step.number}
              className={`mt-2.5 rounded-lg border px-3 py-2 ${
                activeStep === step.number ? "border-sky-300 bg-sky-50/60" : "border-slate-200 bg-surface"
              }`}
            >
              <p className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono text-[14px] font-semibold text-slate-500">
                  {circledStep(step.number)}
                </span>
                <span className="text-[15px] font-semibold text-slate-800">{stepLabel(step, lang)}</span>
                <span
                  className={`rounded-full border px-1.5 py-0.5 text-[14px] font-semibold ${statusChipClass(step.status)}`}
                >
                  {statusLabel(step.status, t)}
                </span>
                {/* WHAT a manual-check area IS left this row for /docs (#50-d):
                    it was six identical tooltips on one panel, teaching the same
                    rule six times. The STATE is not hidden by that — every
                    manual_area step is routed into the 수동 확인 영역 station
                    (lib/calibrationStations.ts) whose header names it and whose
                    one-line verdict promises this screen will never show these
                    steps as done. */}
              </p>
              <p className="mt-1 text-[14px] leading-6 text-slate-700">{pick(step.basis, lang)}</p>
              <details className="mt-1 text-[13px] leading-5 text-slate-500">
                <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
                  {t("자세히 (근거 출처)", "More (which artifact answered)", "詳細（根拠の出典）")}
                </summary>
                <p className="mt-1 border-l border-slate-300 pl-3 font-mono text-[13px] text-slate-500">
                  {step.source}
                  {step.citation ? ` · ${step.citation}` : ""}
                </p>
              </details>
            </div>
          ))}

          {/* ------------------------------------------------------------ */}
          {/* Station bodies. Each one is the evidence and the next action  */}
          {/* for the steps listed above it, and nothing else.              */}
          {/* ------------------------------------------------------------ */}

          {activeStation === "scoring" && (
            <div className="mt-3">
              {/* Two bodies of evidence, one station, and a tab rather than a
                  stack: the absorbed base-model screen is screen-sized, and
                  appending it under the node table is exactly the vertical
                  stack this unit exists to undo. */}
              <div className="flex flex-wrap gap-1.5">
                {(
                  [
                    ["nodes", t("노드 채점", "Node scoring", "ノード採点")],
                    ["workbook", t("기준 모델 검증", "Base-model validation", "基準モデル検証")],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setScoringView(key)}
                    className={`inline-flex h-9 items-center rounded-md border px-3 text-[14px] transition ${
                      scoringView === key
                        ? "border-sky-300 bg-sky-50 text-sky-600"
                        : "border-slate-300 bg-surface text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {scoringView === "nodes" ? (
                <div className="mt-3">
                  {/* WHICH MODEL COLUMN THIS SCREEN REFUSES TO READ was a fold
                      here until #50-d. It is a rule about the product, not a
                      fact about this run, so it is at /docs — and this tab's
                      card is left with exactly one fold budget for the step
                      boxes above it. */}
                  {reportError && (
                    <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[14px] leading-6 text-rose-700">
                      {reportError}
                    </p>
                  )}

                  {!scenarioId ? (
                    <p className="mt-3 text-[14px] leading-6 text-slate-600">
                      {t(
                        "위에서 실행을 선택하면 노드별 GEH와 진단이 나타납니다.",
                        "Pick a run above and the per-node GEH and diagnosis appear.",
                        "上で実行を選択すると、ノード別GEHと診断が表示されます。",
                      )}
                    </p>
                  ) : reportLoading ? (
                    <p className="mt-3 text-[14px] text-slate-500">
                      {t("채점하는 중...", "Scoring...", "採点中...")}
                    </p>
                  ) : report ? (
                    <div className="mt-3 grid gap-3 xl:grid-cols-[1.1fr_1fr]">
                      <div className="min-w-0">
                        <NodeScoreTable
                          report={report}
                          activeNodeId={activeNodeId}
                          selectedNodeIds={selectedNodeIds}
                          onNodeClick={handleNodeClick}
                        />
                        {activeNode && (
                          <div className="mt-3">
                            <NodeScoreDetail
                              node={activeNode}
                              threshold={report.geh_threshold}
                              demandTarget={demandTargets?.nodes?.[activeNode.node_id] ?? null}
                              onDemandTargetChanged={handleProposalResolved}
                            />
                          </div>
                        )}
                      </div>
                      <div className="min-w-0">
                        {/* The map is not decoration here: the scoring station
                            is where a reader asks WHERE the failing nodes are,
                            so it is the one station that always draws one. */}
                        <div className="h-[420px] overflow-hidden rounded-lg border border-slate-200">
                          <BaseValidationMap
                            nodes={mapNodes}
                            selectedNodeIds={selectedNodeIds}
                            activeNodeId={activeNodeId}
                            onNodeClick={(nodeId) => handleNodeClick(nodeId)}
                          />
                        </div>
                        {/* WHERE a node's position comes from — and why a node
                            can be missing from the map while still being scored
                            in the grid beside it — was the LAST ⓘ on this screen.
                            It survived #50-d's first pass because it was not on
                            the eviction list; the knowledge file registered it as
                            D17 (it overlaps D6 without being verbatim, so it
                            could not be filed under it) and it left with the
                            rest. This label is a label now, and /calibration
                            carries no tooltip at all — a sweep pin holds it to
                            that. */}
                        <p className="mt-1.5 text-[14px] text-slate-500">
                          {t("노드 위치", "Node placement", "ノード位置")}
                        </p>
                      </div>
                    </div>
                  ) : null}

                  {/* Before/after, same dataset lineage. Toggled in the context
                      bar; shown here, where the GEH it compares is. */}
                  {compareId && (
                    <div className="mt-4 border-t border-slate-200 pt-3">
                      <h3 className="text-[15px] font-bold text-slate-800">
                        {t("전후 비교 — 노드별 GEH 변화", "Before/after — per-node GEH change", "前後比較 — ノード別GEHの変化")}
                      </h3>
                      {compareError ? (
                        <p className="mt-1.5 text-[14px] text-rose-700">{compareError}</p>
                      ) : deltas.length === 0 ? (
                        <p className="mt-1.5 text-[14px] leading-6 text-slate-600">
                          {t(
                            "두 실행 모두에서 등급이 매겨진 노드가 없어 비교할 값이 없습니다.",
                            "No node was graded in both runs, so there is nothing to compare.",
                            "両方の実行で評価されたノードがないため、比較する値がありません。",
                          )}
                        </p>
                      ) : (
                        <table className="mt-1.5 w-full border-collapse text-[14px]">
                          <thead>
                            <tr className="border-b border-slate-200 text-left text-slate-500">
                              <th className="px-2 py-1.5 font-semibold">{t("노드", "Node", "ノード")}</th>
                              <th className="px-2 py-1.5 text-right font-semibold">{t("이전", "Before", "以前")}</th>
                              <th className="px-2 py-1.5 text-right font-semibold">{t("이후", "After", "以後")}</th>
                              <th className="px-2 py-1.5 text-right font-semibold">Δ</th>
                            </tr>
                          </thead>
                          <tbody>
                            {deltas.map((row) => (
                              <tr key={row.nodeId} className="border-b border-slate-100">
                                <td className="px-2 py-1.5">
                                  <span className="font-mono text-slate-700">{row.nodeId}</span>
                                  <span className="ml-1.5 text-slate-600">{row.nodeName}</span>
                                </td>
                                <td className="px-2 py-1.5 text-right font-mono text-slate-600">{row.before.toFixed(1)}</td>
                                <td className="px-2 py-1.5 text-right font-mono text-slate-600">{row.after.toFixed(1)}</td>
                                <td
                                  className={`px-2 py-1.5 text-right font-mono font-semibold ${
                                    row.delta < 0 ? "text-emerald-700" : row.delta > 0 ? "text-rose-700" : "text-slate-500"
                                  }`}
                                >
                                  {row.delta > 0 ? "+" : ""}
                                  {row.delta.toFixed(1)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                /* The absorbed base-model screen: the workbook, the Category
                   1/2/3 profile, the flow bands, the R² scatter, the travel-time
                   analysis and the §5.6.3 criteria coverage. Same code as before
                   this unit. */
                <div className="mt-3">
                  <BaseModelStation />
                </div>
              )}
            </div>
          )}

          {activeStation === "network" && (
            <div className="mt-3 flex flex-wrap gap-2">
              <Link href="/health" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
                {t("모델 검진 열기", "Open model inspection", "モデル検診を開く")}
              </Link>
              <Link href="/genesis" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
                {t("네트워크 생성·게이트", "Network generation & gate", "ネットワーク生成・ゲート")}
              </Link>
            </div>
          )}

          {activeStation === "signals" && (
            <div className="mt-3 flex flex-col gap-2">
              <div className="flex flex-wrap gap-2">
                <Link href="/genesis" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
                  {t("현시도 검토 열기", "Open 현시도 review", "現示図レビューを開く")}
                </Link>
                <button
                  type="button"
                  onClick={() => handleSelectStation("behaviour")}
                  className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]"
                >
                  {t("Table 5-6 표 보기 (⑫)", "See the Table 5-6 table (⑫)", "Table 5-6の表を見る (⑫)")}
                </button>
              </div>
            </div>
          )}

          {activeStation === "behaviour" && (
            <div className="mt-3">
              <div className="mt-2">
                <BehaviourStation rows={appliedRows} loading={appliedLoading} error={appliedError} />
              </div>

              {/* Unit #51's slot. It renders the RAIL's own step-⑫ track — the
                  same component, from the same server payload — rather than a
                  second copy of the locked/available sentence. A second copy is
                  exactly how the two would eventually disagree about whether a
                  search may start, in two places on one screen. When the rail
                  has not loaded there is simply nothing here to say. */}
              {parameterSearchTrack && (
                <div className="mt-3">
                  <ParameterSearchTrack
                    track={parameterSearchTrack}
                    lang={lang}
                    t={t}
                    scenarioId={scenarioId}
                    sessionId={agentSessionId}
                    // A finished search changes the gate underneath its own
                    // card, so the rail is re-read the same way an approval
                    // re-reads it: by bumping the request key.
                    onSearchFinished={handleProposalResolved}
                    // #57: the locked track's SIGNAL_STEP_UNVERIFIED and
                    // LADDER_NOT_EXHAUSTED chips reuse this screen's own
                    // station/node selection rather than inventing either.
                    onSelectStation={handleSelectStation}
                    onSelectNode={handleSelectScoringNode}
                  />
                </div>
              )}
            </div>
          )}

          {/* ⑨, ⑩ and ⑪ each opened with the RULE their evidence rests on,
              folded by #50-c and EVICTED by #50-d: the rule is about the
              product, the step's own basis line above is about this run, and
              only one of those changes when a reader comes back. All three are
              verbatim at /docs (calibrationHelpEntries) and in
              docs/product/calibration-doctrine.md.

              #53 gives all three what #50-d left them without: an ACTION. Each
              carries the one control its evidence gap actually needs, plus the
              [사람 확인] row that every step with no machine evidence now has. */}

          {activeStation === "transit" && (
            <div className="mt-3 flex flex-col gap-2">
              {/* ⑨'s gap is not a missing screen, it is a missing MEASUREMENT:
                  this product reads a PT line count and nothing else about
                  transit, so the only honest links are the rule that says so
                  and the signature that answers it. */}
              <div className="flex flex-wrap gap-2">
                <Link href="/docs" className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]">
                  {t("보정 원칙 문서 열기", "Open the calibration doctrine", "補正の原則ドキュメントを開く")}
                </Link>
              </div>
              <StepSignatures steps={stationSteps} onChanged={handleProposalResolved} />
            </div>
          )}

          {activeStation === "demand" && (
            <div className="mt-3 flex flex-col gap-2">
              <div className="flex flex-wrap gap-2">
                <Link
                  href="/genesis/demand"
                  className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]"
                >
                  {t("수요 입력으로 →", "Go to demand inputs →", "需要入力へ →")}
                </Link>
              </div>
              <StepSignatures steps={stationSteps} onChanged={handleProposalResolved} />
            </div>
          )}

          {activeStation === "assignment" && (
            <div className="mt-3 flex flex-col gap-2">
              <div className="flex flex-wrap gap-2">
                {/* ⑪ has no screen of its own in this product and no proposal
                    unless a scored run raised one, so the action that fits is
                    the drawer -- prefilled with THIS step, through the same
                    window event three other surfaces already dispatch. */}
                {stationSteps.map((step) => (
                  <button
                    key={step.number}
                    type="button"
                    onClick={() => requestAgentHandoff(buildRouteSplitPrompt(step, lang, t))}
                    className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]"
                  >
                    {t("AI에게 물어보기", "Ask the AI", "AIに聞く")}
                  </button>
                ))}
              </div>
              <StepSignatures steps={stationSteps} onChanged={handleProposalResolved} />
            </div>
          )}

          {/* THE ONE THAT STAYS. 수동 확인 영역's one-line verdict is not
              background a reader may skip: it is this screen's PROMISE that it
              will never mark these six steps done, which is a verdict, not an
              education. The paragraph that used to sit folded beneath it is at
              /docs. */}
          {activeStation === "manual" && (
            <div className="mt-3 flex flex-col gap-2.5">
              <p className="text-[15px] font-semibold leading-6 text-slate-800">
                {t(
                  "이 제품은 위 단계들을 측정하지 않습니다 — 완료로 표시하지 않습니다.",
                  "This product measures nothing for the steps above — it never shows them as done.",
                  "本製品は上記の段階を測定しません — 完了と表示することはありません。",
                )}
              </p>
              {/* AND THE PROMISE STILL HOLDS AFTER #53. A signature here does
                  not make this product measure anything, and the sentence above
                  is unchanged: what a signed step reads is 확인됨(사람 증언),
                  which is a different word from 완료 and is the server's. */}
              <StepSignatures steps={stationSteps} onChanged={handleProposalResolved} />
            </div>
          )}

          {/* Node context, for the stations that HAVE one. Measured, not
              assumed: a station draws a map only when the server named nodes
              for its steps (`next_action.mrwa_step`, or ⑫'s own search split).
              An empty map on ① or ⑨ would be furniture pretending to be
              evidence. */}
          <NodeContext
            nodes={stationNodes}
            extraNodeIds={activeStation === "behaviour" ? behaviourNodeIds : []}
            report={report}
            mapNodes={mapNodes}
            activeNodeId={activeNodeId}
            activeNode={activeNode}
            demandTargets={demandTargets}
            onDemandTargetChanged={handleProposalResolved}
            onNodeClick={handleNodeClick}
            t={t}
          />

          {/* This station's own proposals — inline, where their evidence is. */}
          <div className="mt-4 border-t border-slate-200 pt-3">
            {/* "One approval path, shared with the drawer" was an ⓘ here until
                #50-d — a property of the machinery, not of any card a reviewer
                is deciding on. It is at /docs. */}
            <h3 className="text-[15px] font-bold text-slate-800">
              {t("이 단계의 제안 — 승인 대기", "This step's proposals — awaiting approval", "この段階の提案 — 承認待ち")}
            </h3>
            <div className="mt-2">
              <StationProposals
                placed={stationProposals}
                otherCount={otherProposalCount}
                loading={proposals.loading}
                error={proposals.error}
                decide={proposals.decide}
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

/**
 * The nodes this station's steps are about, with a map and the shared detail.
 *
 * Renders NOTHING when the server named no node for these steps — which is the
 * normal, correct state for ① and ⑨. That is the whole rule the user asked for
 * ("지도는 의미 있는 스테이션에서만"): a map appears where there are nodes to
 * place, and nowhere else.
 */
function NodeContext({
  nodes,
  extraNodeIds,
  report,
  mapNodes,
  activeNodeId,
  activeNode,
  demandTargets,
  onDemandTargetChanged,
  onNodeClick,
  t,
}: {
  nodes: CalibrationRealityNode[];
  extraNodeIds: string[];
  report: CalibrationRealityCheck | null;
  mapNodes: BaseValidationMapNode[];
  activeNodeId: string | null;
  activeNode: CalibrationRealityNode | null;
  demandTargets: CalibrationDemandTargets | null;
  onDemandTargetChanged: () => void;
  onNodeClick: (nodeId: string) => void;
  t: (ko: string, en: string, ja?: string) => string;
}) {
  const ids = [
    ...nodes.map((node) => node.node_id),
    ...extraNodeIds.filter((id) => !nodes.some((node) => node.node_id === id)),
  ];
  if (ids.length === 0) return null;

  const named = new Map((report?.nodes ?? []).map((node) => [node.node_id, node]));

  return (
    <div className="mt-3 border-t border-slate-200 pt-3">
      {/* How to read this map — which nodes are highlighted, and that an
          unplaced node is a missing placement rather than a missing node — was
          an ⓘ on this heading until #50-d. It is a rule about the map, read once,
          so it is at /docs with the rest of the doctrine. */}
      <h3 className="text-[15px] font-bold text-slate-800">
        {t("이 단계가 가리키는 노드", "The nodes this step points at", "この段階が指すノード")}
      </h3>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {ids.map((id) => {
          const node = named.get(id);
          return (
            <li key={id}>
              <button
                type="button"
                onClick={() => onNodeClick(id)}
                className={`rounded-full border px-2 py-0.5 text-[14px] transition ${
                  activeNodeId === id
                    ? "border-sky-300 bg-sky-50 text-sky-600"
                    : "border-slate-300 bg-surface text-slate-700 hover:bg-slate-50"
                }`}
              >
                <span className="font-mono">{id}</span>
                {node?.node_name ? <span className="ml-1.5">{node.node_name}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>

      <div className="mt-2 h-[280px] overflow-hidden rounded-lg border border-slate-200">
        <BaseValidationMap
          nodes={mapNodes}
          selectedNodeIds={ids}
          activeNodeId={activeNodeId}
          onNodeClick={(nodeId) => onNodeClick(nodeId)}
        />
      </div>

      {/* The SAME detail pane the scoring grid opens — one component, so a
          node reads identically wherever a reader reaches it, AND the same bar:
          this station holds the very report the scoring grid scored, so it
          threads that report's own `geh_threshold` rather than leaving the pane
          verdictless beside a grid that has one. `?.` because the report may
          not have loaded yet — and with no bar `nodeGehPasses` returns null, so
          the GEH prints as a number with no verdict colour, which is the honest
          rendering of "this pane has not been told what the bar is". */}
      {activeNode && ids.includes(activeNode.node_id) && (
        <div className="mt-2">
          <NodeScoreDetail
            node={activeNode}
            threshold={report?.geh_threshold}
            demandTarget={demandTargets?.nodes?.[activeNode.node_id] ?? null}
            onDemandTargetChanged={onDemandTargetChanged}
          />
        </div>
      )}
    </div>
  );
}

/**
 * Unit #53 — the [사람 확인] rows: the ACTION a step with no machine evidence owes.
 *
 * THE PROBLEM. Nine of the thirteen steps have no artifact this product can
 * read, so their station said "증거 없음" and offered nothing to do about it.
 * The modeller who HAD done the step in Vissim had no way to say so, and the
 * screen could not tell "nobody did this" from "somebody did it and this tool
 * cannot see it" — the exact shape of the user's 2026-08-20 complaint that a
 * screen owes an action, not an education.
 *
 * WHAT IT IS NOT. It is not a "mark as done" checkbox. The POST records a
 * SIGNATURE — the signed-in name, the server's clock, a mandatory reason and
 * the hash of the network it was made against — and the STATUS that comes back
 * is the rail's, re-derived server-side on the next read (`onChanged` bumps the
 * rail's request key). Nothing in this file assigns a status word, which is why
 * `attestable` is read off the step rather than from a list kept here.
 */
function StepSignatures({
  steps,
  onChanged,
}: {
  steps: CalibrationRailStep[];
  onChanged: () => void;
}) {
  const signable = steps.filter((step) => step.attestable);
  if (signable.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2">
      {signable.map((step) => (
        <li key={step.number}>
          <StepSignature step={step} onChanged={onChanged} />
        </li>
      ))}
    </ul>
  );
}

function StepSignature({
  step,
  onChanged,
}: {
  step: CalibrationRailStep;
  onChanged: () => void;
}) {
  const { t, lang } = useLanguage();
  // One form, two acts. `null` is closed; both open acts demand a reason, and
  // the button that submits says which act it is rather than a generic 확인.
  const [mode, setMode] = useState<"attest" | "revoke" | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const attestation = step.attestation ?? null;

  const close = () => {
    setMode(null);
    setReason("");
    setError(null);
  };

  // Opening either act CLEARS the box. A reason typed for a confirmation must
  // not be able to arrive on a revocation (or the other way round) — they are
  // two different statements and only one of them is being made.
  const openForm = (next: "attest" | "revoke") => {
    setMode((current) => (current === next ? null : next));
    setReason("");
    setError(null);
  };

  const submit = async () => {
    if (!mode || !reason.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "revoke") {
        if (!attestation) return;
        await revokeCalibrationStepAttestation(attestation.id, { reason });
      } else {
        await postCalibrationStepAttestation({ stepNumber: step.number, reason });
      }
      close();
      onChanged();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[14px] font-semibold text-slate-500">
          {circledStep(step.number)}
        </span>
        <span className="text-[14px] font-semibold text-slate-700">{stepLabel(step, lang)}</span>
        {attestation?.stale && (
          <span className="rounded-full border border-slate-300 bg-surface px-1.5 py-0.5 text-[14px] font-semibold text-slate-600">
            {t("재확인 필요", "Re-confirm", "再確認が必要")}
          </span>
        )}
        <span className="grow" />
        <button
          type="button"
          onClick={() => openForm("attest")}
          className="ui-button-ghost inline-flex h-9 shrink-0 items-center px-3 text-[14px]"
        >
          {attestation
            ? t("다시 확인", "Confirm again", "再確認する")
            : t("사람 확인", "Confirm as a person", "人による確認")}
        </button>
        {attestation && (
          <button
            type="button"
            onClick={() => openForm("revoke")}
            className="ui-button-ghost inline-flex h-9 shrink-0 items-center px-3 text-[14px]"
          >
            {t("확인 철회", "Revoke", "確認を撤回")}
          </button>
        )}
      </div>

      {/* THE SIGNATURE THAT STANDS — who, when, and their own words for why.
          The reason is on the surface for the same reason `step.basis` is: it is
          the justification of the state beside it, and this state's whole worth
          is that somebody accountable stated one. The hash and the event history
          are provenance and fold. */}
      {attestation && (
        <div className="mt-1.5 border-l border-slate-300 pl-2">
          <p className="text-[14px] leading-6 text-slate-700">
            <b>{attestation.attested_by}</b> · {attestation.attested_at}
          </p>
          <p className="mt-0.5 text-[14px] leading-6 text-slate-700">{attestation.reason}</p>
          <details className="mt-1 text-[13px] leading-5 text-slate-500">
            <summary className="cursor-pointer list-none text-[14px] text-slate-500 underline decoration-dotted underline-offset-4">
              {t("자세히 (서명 이력)", "More (the signing history)", "詳細（署名の履歴）")}
            </summary>
            <ul className="mt-1 flex flex-col gap-1 border-l border-slate-300 pl-2">
              {attestation.history.map((event, index) => (
                <li key={`${event.event}-${index}`}>
                  {event.event} · {event.by} · {event.at} · {event.reason}
                </li>
              ))}
              <li className="font-mono text-[13px] text-slate-500">
                inpx sha256 {attestation.inpx_sha256.slice(0, 16)}…
              </li>
            </ul>
          </details>
        </div>
      )}

      {mode && (
        <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
          <label className="block text-[14px] font-semibold text-slate-700" htmlFor={`reason-${step.number}`}>
            {mode === "revoke"
              ? t("철회 사유", "Why it no longer holds", "撤回の理由")
              : t("확인 근거", "What you are attesting to", "確認の根拠")}
          </label>
          <textarea
            id={`reason-${step.number}`}
            value={reason}
            rows={3}
            onChange={(event) => setReason(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 bg-surface px-2 py-1.5 text-[14px] leading-6 text-slate-900"
          />
          {/* WHAT SIGNING DOES, beside the control that does it — the same
              sentence the 현시도 confirm form carries, for the same reason: a
              reader is about to put their name on something and has to know
              what is recorded. It states the ACT, not the doctrine; why a
              signature is never 완료 is at /docs. */}
          <p className="mt-1 text-[14px] leading-6 text-slate-600">
            {mode === "revoke"
              ? t(
                  "철회해도 기록은 지워지지 않습니다 — 누가 언제 왜 철회했는지가 원래 서명 옆에 남습니다.",
                  "Revoking deletes nothing — who withdrew it, when, and why is kept beside the original signature.",
                  "撤回しても記録は消えません — 誰がいつなぜ撤回したかが元の署名の横に残ります。",
                )
              : t(
                  "확인하면 로그인한 사용자의 이름과 시각, 그리고 지금 네트워크 파일의 해시가 함께 기록됩니다. 네트워크가 바뀌면 이 확인은 재확인 대상이 됩니다.",
                  "Confirming records the signed-in user's name, the time, and the hash of the network file as it stands now. If the network changes, this confirmation asks to be made again.",
                  "確認すると、ログイン中の利用者名・時刻・現在のネットワークファイルのハッシュが記録されます。ネットワークが変わると、この確認は再確認の対象になります。",
                )}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={submit}
              disabled={busy || reason.trim().length === 0}
              className="inline-flex h-9 items-center rounded-md border border-sky-300 bg-sky-50 px-3 text-[14px] font-semibold text-sky-600 transition hover:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {mode === "revoke"
                ? t("이 확인을 철회합니다", "I withdraw this confirmation", "この確認を撤回します")
                : t("이 단계를 확인합니다", "I confirm this step", "この段階を確認します")}
            </button>
            <button
              type="button"
              onClick={close}
              className="ui-button-ghost inline-flex h-9 items-center px-3 text-[14px]"
            >
              {t("취소", "Cancel", "キャンセル")}
            </button>
            {/* A reason is the server's requirement, not this form's opinion:
                the POST refuses an empty one by name. The disabled button says
                so before the round trip rather than instead of it. */}
            {reason.trim().length === 0 && (
              <span className="text-[14px] leading-6 text-slate-600">
                {t("이유를 적어야 합니다.", "A reason is required.", "理由の記入が必要です。")}
              </span>
            )}
          </div>
          {error && (
            <p className="mt-1.5 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[14px] leading-6 text-rose-700">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/* `StationDoctrine` — the per-station folded rule #50-c introduced — is DELETED
 * by #50-d, component and all six call sites. Six of the eight stations opened
 * with a disclosure whose only content was a paragraph of doctrine, which is the
 * "education" the user's 2026-08-20 decision evicted. Every one of those six
 * sentences is verbatim in docs/product/calibration-doctrine.md and rendered at
 * /docs by `calibrationHelpEntries`; the contract suite pins both copies and
 * pins the sentences absent from this file. */
