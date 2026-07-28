"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, FormEvent, KeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import ApprovalFocusMap from "@/components/ApprovalFocusMap";
import { useLanguage } from "@/components/LanguageProvider";
import {
  AgentApprovalError,
  AgentStreamDisconnectedError,
  approveAgentAction,
  downloadAgentModel,
  fetchAssistantStatus,
  fetchSimulationJob,
  fetchSimulationResults,
  streamAgentQuery,
  streamAssistant,
  type AgentAction,
  type AgentApprovalFocus,
  type AgentStep,
  type AgentWarning,
  type AssistantQueryResponse,
  type AssistantSource,
  type AssistantStatus,
  type SimulationJob,
  type SimulationResultsResponse,
  type VissimComToolStatus,
} from "@/lib/api";
import { elementTypeLabel, linkKindLabel, type NetworkElementRef, type NetworkLinkRef } from "@/lib/networkElements";

// M2.3: lifecycle of a write-approval card attached to an agent-mode
// message. "pending" -> user has not acted yet (Run/Cancel both enabled);
// "approving"/"declining" -> the /agent/approve call is streaming (buttons
// disabled, busy label); "approved"/"declined" -> resolved, single-use,
// buttons replaced by a small label; "error" -> the resolve call failed
// (stale/consumed approval, or a network error) and is also terminal, since
// a consumed approval_id would just 404 again on retry.
type AgentApprovalResolution = "pending" | "approving" | "declining" | "approved" | "declined" | "error";

type AgentApprovalState = {
  approvalId: string;
  sessionId: string;
  actions: AgentAction[];
  warnings: AgentWarning[];
  assumptions: string[];
  resolution: AgentApprovalResolution;
  errorMessage?: string;
  /** Approval-card mini-map (v2 UX unit): the map-focus hint from the SAME
   * wire event actions/warnings/assumptions come from -- see
   * AgentApprovalFocus's own doc comment in lib/api.ts. Undefined only for
   * an approval reconciled from a pre-this-unit event shape; ApprovalFocusMap
   * already treats a missing/null bbox as "render nothing", so no separate
   * guard is needed here. */
  focus?: AgentApprovalFocus;
};

type FloatingMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: AssistantQueryResponse;
  /** The exact user text that produced this assistant message — needed to
   * re-send with allow_tool_execution=true if the user approves a proposed
   * simulation run, without asking them to retype the request. */
  sourcePrompt?: string;
  /** M1.4 agent-mode step narration (the agent's read-only work log for this
   * turn). Present only for messages sent while agent mode was on; stays
   * populated after completion so the log remains visible. */
  agentSteps?: AgentStep[];
  /** M2.3: present once this agent-mode message pauses for a write approval
   * (set from the approval_request stream event, reconciled from the
   * terminal done.response.approval as a safety net). Stays on the message
   * after resolution — single-use, inert once acted on. */
  agentApproval?: AgentApprovalState;
  /** P8 (edited-model download): true iff THIS agent-mode turn persisted a
   * write (response.wrote_model — see lib/api.ts's AgentQueryResponse).
   * Drives a small "download the edited model" link under the answer.
   * `writeSessionId` is the session that write landed in — always set
   * alongside `wroteModel`, kept as its own field rather than reusing the
   * panel's live `agentSessionId` state so the affordance stays correct on
   * an OLDER message even after a later "New conversation" reset. */
  wroteModel?: boolean;
  writeSessionId?: string;
  /** RUN-PROGRESS UI (2026-07-28): set from the run_started stream event
   * (lib/api.ts's AgentStreamEvent) the moment a run_simulation dispatch
   * submits a job -- well before the turn's own stream resumes (it stays
   * blocked on the same tool call until the run finishes). Drives
   * AgentRunProgressCard, polled independently of this stream via
   * fetchSimulationJob. Never cleared once set -- a message's own run is a
   * fixed fact of that turn, and the card itself stops polling/rendering
   * progress once the job reaches a terminal status. */
  runJobId?: string;
};

type AssistantPageContext = {
  route: string;
  label?: string;
  selectedScenarioIds?: string[];
  selectedScenarioNames?: string[];
  selectedNodeIds?: string[];
  selectedNodeNames?: string[];
  // P6: what the user clicked in the rendered network. Both are published by
  // /network and /dashboard and forwarded to the backend as
  // selected_link_ids/selected_element, so "이 도로" / "this signal" resolves to
  // a real object instead of the user having to type an id. `selectedLinks`
  // carries 0-or-1 entries today (single-select) but is an array so a future
  // multi-select needs no context-shape change.
  selectedLinks?: NetworkLinkRef[];
  selectedElement?: NetworkElementRef | null;
  dataset?: {
    filename?: string;
    source?: string;
    revision?: string;
  } | null;
  notes?: string[];
  clear?: boolean;
  updatedAt?: number;
};

type ParsedAnswerSection = {
  heading: string;
  lines: string[];
};

type PromptSimulationToolResult = {
  recognized?: boolean;
  executed?: boolean;
  requires_confirmation?: boolean;
  reason?: string | null;
  changes?: string[];
  job?: {
    id?: string;
    status?: string;
    scenario_result_id?: string;
  } | null;
};

type RouteAssistantContext = {
  label: string;
  starters: string[];
};

type Translate = (ko: string, en: string, ja?: string) => string;

type AssistantPanelSize = {
  width: number;
  height: number;
};

type AssistantPanelResizeState = {
  startX: number;
  startY: number;
  startWidth: number;
  startHeight: number;
};

type AssistantRequestPhase = "idle" | "context" | "querying" | "streaming" | "cancelled";

// Refreshed for agent-mode-default (docs/phase2/DECISIONS.md UX-reorg
// entry): the prior triplet's third starter was tuned for the legacy
// regex path (a single sentence carrying both an edit and a run, in the
// exact word order the regex matcher needed). Agent mode plans/approves
// each action on its own, so the three starters now each showcase one
// distinct agent verb -- a read, an edit, a run -- rather than one
// combined command.
const DEFAULT_STARTERS = [
  {
    ko: "\ud604\uc7ac VISSIM \uc2e4\ud589 \uc0c1\ud0dc\uc640 \ub370\uc774\ud130\uc14b \uc0c1\ud0dc\ub97c \uc694\uc57d\ud574\uc918.",
    en: "Summarize current VISSIM execution and dataset status.",
    ja: "\u73fe\u5728\u306eVISSIM\u5b9f\u884c\u72b6\u6cc1\u3068\u30c7\u30fc\u30bf\u30bb\u30c3\u30c8\u306e\u72b6\u614b\u3092\u8981\u7d04\u3057\u3066\u3002",
  },
  {
    ko: "\ucc28\ub7c9 \uc785\ub825 107\ubc88\uc744 6000\uc73c\ub85c \ubcc0\uacbd\ud574\uc918.",
    en: "Change vehicle input 107 to 6000.",
    ja: "\u8eca\u4e21\u5165\u529b107\u30926000\u306b\u5909\u66f4\u3057\u3066\u3002",
  },
  {
    ko: "5\uac1c \uc2dc\ub4dc\ub85c VISSIM \uc2dc\ubbac\ub808\uc774\uc158\uc744 \uc2e4\ud589\ud574\uc918.",
    en: "Run a VISSIM simulation with 5 seeds.",
    ja: "5\u3064\u306e\u30b7\u30fc\u30c9\u3067VISSIM\u30b7\u30df\u30e5\u30ec\u30fc\u30b7\u30e7\u30f3\u3092\u5b9f\u884c\u3057\u3066\u3002",
  },
];
const PANEL_SIZE_STORAGE_KEY = "vissim-assistant-panel-size";
const DEFAULT_PANEL_SIZE: AssistantPanelSize = { width: 410, height: 580 };
const PANEL_MIN_SIZE: AssistantPanelSize = { width: 340, height: 430 };
const PANEL_MAX_SIZE: AssistantPanelSize = { width: 860, height: 1000 };
const PANEL_VIEWPORT_MARGIN: AssistantPanelSize = { width: 24, height: 116 };
const COMMON_ANSWER_HEADINGS = [
  "답변",
  "요약",
  "상태 요약",
  "진단 요약",
  "근거",
  "다음 조치",
  "확인할 점",
  "확인 필요",
  "가능 여부",
  "데이터셋",
  "할 수 있는 일",
  "필요한 입력",
  "제한",
];

// Render-time fallback: the small local model occasionally emits the Korean
// section headings even when answering in another language. Map them so the
// displayed heading matches the active UI language. Keys are the Korean labels.
const ANSWER_HEADING_I18N: Record<string, { en: string; ja: string }> = {
  "답변": { en: "Answer", ja: "回答" },
  "요약": { en: "Summary", ja: "要約" },
  "상태 요약": { en: "Status summary", ja: "状態の要約" },
  "진단 요약": { en: "Diagnostic summary", ja: "診断の要約" },
  "근거": { en: "Evidence", ja: "根拠" },
  "다음 조치": { en: "Next steps", ja: "次のアクション" },
  "확인할 점": { en: "Checks", ja: "確認事項" },
  "확인 필요": { en: "Needs confirmation", ja: "確認が必要" },
  "가능 여부": { en: "Feasibility", ja: "可否" },
  "데이터셋": { en: "Dataset", ja: "データセット" },
  "할 수 있는 일": { en: "Capabilities", ja: "できること" },
  "필요한 입력": { en: "Required input", ja: "必要な入力" },
  "제한": { en: "Limits", ja: "制限" },
};

function localizeHeading(heading: string, lang: string): string {
  if (lang === "ko") return heading;
  const entry = ANSWER_HEADING_I18N[heading.trim()];
  if (!entry) return heading;
  return lang === "ja" ? entry.ja : entry.en;
}

// M2.3: builds/reconciles agentApproval state from either wire source --
// the early approval_request stream event (no session_id: the backend
// mints it before that event, see AgentStreamEvent's "approval_request"
// variant) or the terminal done.response.approval (session_id always
// known). `resolution` always starts "pending" here; only
// runAgentApproval below ever advances it.
function approvalStateFromEvent(
  event: {
    approval_id?: string;
    actions?: AgentAction[];
    warnings?: AgentWarning[];
    assumptions?: string[];
    focus?: AgentApprovalFocus;
  },
  sessionId: string,
): AgentApprovalState {
  return {
    approvalId: event.approval_id ?? "",
    sessionId,
    actions: event.actions ?? [],
    warnings: event.warnings ?? [],
    assumptions: event.assumptions ?? [],
    resolution: "pending",
    focus: event.focus,
  };
}

// Non-UI escape hatch to the legacy regex pipeline (docs/phase2/DECISIONS.md
// UX-reorg entry). Agent mode is now the default and only user-facing chat
// mode -- the checkbox that used to let anyone flip back to the M1.4 regex
// path is gone. The regex pipeline itself stays fully intact server-side
// (it's the paper's eval baseline; see runPrompt/streamAssistant below,
// unchanged), so it still needs SOME way to be reached for evaluation and
// debugging -- just never a visible control again. Two ways to trigger it,
// checked once per page load:
//   1. Load the page with ?assistant=legacy in the URL (session-only, this
//      load only -- does not persist).
//   2. Run `localStorage.setItem("vissim-assistant-legacy", "1")` in the
//      browser console (persists across reloads until cleared).
// Anything else (unset, any other value) leaves agent mode on.
function legacyPipelineRequested(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (new URLSearchParams(window.location.search).get("assistant") === "legacy") return true;
  } catch {
    // Malformed location.search -- fall through to the localStorage check.
  }
  try {
    return window.localStorage.getItem("vissim-assistant-legacy") === "1";
  } catch {
    return false;
  }
}

export default function FloatingAssistant() {
  const pathname = usePathname();
  const { lang, t } = useLanguage();
  const context = useMemo(() => buildRouteAssistantContext(pathname, t), [pathname, t]);
  const [pageContext, setPageContext] = useState<AssistantPageContext | null>(null);
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<FloatingMessage[]>([]);
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [statusChecking, setStatusChecking] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [requestPhase, setRequestPhase] = useState<AssistantRequestPhase>("idle");
  const [activeAssistantMessageId, setActiveAssistantMessageId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [panelSize, setPanelSize] = useState<AssistantPanelSize>(() => readStoredPanelSize());
  // Assistant message ids where the user explicitly declined a proposed
  // simulation run, so the confirm card doesn't keep reappearing.
  const [declinedExecutionIds, setDeclinedExecutionIds] = useState<Set<string>>(new Set());
  // Agent mode is now the default chat experience -- this flips the M1.4
  // toggle exactly as that decision anticipated ("expected to flip to
  // on-by-default ... once M2/M3 give the agent write capability behind the
  // same approval flow", docs/phase2/DECISIONS.md), now that M2/M3 have
  // shipped write capability behind that same propose->approve flow. There
  // is no UI control for this anymore; the only way off is the non-UI
  // escape hatch above (legacyPipelineRequested), kept for the paper's eval
  // baseline and debugging, never for a real end user.
  const [agentMode] = useState(() => !legacyPipelineRequested());
  // M4: the current open conversation's agent session id (M3.3's backend
  // capability, wired into the UI here for the first time — see
  // docs/phase2/DECISIONS.md's 2026-07-24 M3.3 entry, "Frontend threading
  // of session_id ... is deliberately left to M4"). `null` means "no agent
  // turn has completed yet in this conversation" -- the next agent-mode
  // send omits `session_id`, which mints a fresh session exactly like every
  // pre-M4 client. Set from `done.response.session_id` after each agent
  // turn (both a fresh proposal and an approval resolution); reset to
  // `null` on an explicit conversation reset (resetConversation below) or
  // an agent-mode off→on toggle (see the effect below) — a stale session id
  // surviving either of those would silently resume a conversation the user
  // just asked to abandon.
  const [agentSessionId, setAgentSessionId] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const resizeRef = useRef<AssistantPanelResizeState | null>(null);
  const requestAbortRef = useRef<AbortController | null>(null);
  const activePageContext = pageContext && pathname.startsWith(pageContext.route) ? pageContext : null;
  const panelStyle = {
    "--assistant-panel-width": `${panelSize.width}px`,
    "--assistant-panel-height": `${panelSize.height}px`,
  } as CSSProperties;

  useEffect(() => {
    let active = true;
    setStatusChecking(true);
    setStatusError(null);
    fetchAssistantStatus()
      .then((nextStatus) => {
        if (!active) return;
        setStatus(nextStatus);
        setStatusError(null);
      })
      .catch((caught) => {
        if (!active) return;
        setStatus(null);
        setStatusError(formatAssistantError(caught, t));
      })
      .finally(() => {
        if (active) setStatusChecking(false);
      });
    return () => {
      active = false;
    };
  }, [open, t]);

  useEffect(() => {
    return () => {
      requestAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    function handlePageContext(event: Event) {
      const detail = (event as CustomEvent<AssistantPageContext>).detail;
      if (!detail?.route) return;
      if (detail.clear) {
        setPageContext((current) => (current?.route === detail.route ? null : current));
        return;
      }
      if (!pathname.startsWith(detail.route)) return;
      setPageContext(detail);
    }

    window.addEventListener("vissim:page-context", handlePageContext);
    window.setTimeout(() => requestPageContext(pathname), 0);
    return () => {
      window.removeEventListener("vissim:page-context", handlePageContext);
    };
  }, [pathname]);

  // Deliverable 3 (handoff affordance): CurationBar's insight cards dispatch
  // this event to hand a request off to the real agent -- same
  // custom-window-event seam as "vissim:page-context" just above (that
  // pattern is the established precedent this follows, per the task brief).
  // Opens the panel if it was closed and prefills the composer with the
  // prompt CurationBar derived from the card's title, in the active UI
  // language (ko/en/ja); the user still has to press send, so this never
  // sends anything on the user's behalf.
  useEffect(() => {
    function handleAssistantHandoff(event: Event) {
      const detail = (event as CustomEvent<{ prompt?: string }>).detail;
      if (!detail?.prompt) return;
      setPrompt(detail.prompt);
      setOpen(true);
      window.setTimeout(() => inputRef.current?.focus(), 80);
    }

    window.addEventListener("vissim:assistant-handoff", handleAssistantHandoff);
    return () => {
      window.removeEventListener("vissim:assistant-handoff", handleAssistantHandoff);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    window.setTimeout(() => inputRef.current?.focus(), 80);
    requestPageContext(pathname);
  }, [open, pathname]);

  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  }, [messages, loading, open]);

  useEffect(() => {
    function handlePointerMove(event: PointerEvent) {
      const resize = resizeRef.current;
      if (!resize) return;
      const nextSize = clampPanelSize({
        width: resize.startWidth - (event.clientX - resize.startX),
        height: resize.startHeight - (event.clientY - resize.startY),
      });
      storePanelSize(nextSize);
      setPanelSize(nextSize);
    }

    function stopResize() {
      resizeRef.current = null;
    }

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
    };
  }, []);

  useEffect(() => {
    function handleWindowResize() {
      setPanelSize((current) => {
        const nextSize = clampPanelSize(current);
        storePanelSize(nextSize);
        return nextSize;
      });
    }

    window.addEventListener("resize", handleWindowResize);
    return () => {
      window.removeEventListener("resize", handleWindowResize);
    };
  }, []);

  // M4: an agent-mode off→on toggle starts a fresh agent session, never
  // resuming whatever session id a PRIOR agent-mode stretch of this same
  // open conversation left behind (see agentSessionId's own comment). A
  // plain re-render or an on→off→(still off) toggle must not reset it --
  // only the off→on edge does, tracked via this ref rather than effect
  // deps so mounting doesn't itself count as a transition.
  const previousAgentModeRef = useRef(agentMode);
  useEffect(() => {
    if (!previousAgentModeRef.current && agentMode) {
      setAgentSessionId(null);
    }
    previousAgentModeRef.current = agentMode;
  }, [agentMode]);

  // P2 (edited-network view + refresh): broadcast the current agent session
  // id to the rest of the app whenever it changes -- a fresh session minted
  // from `done.response.session_id`, an explicit reset via resetConversation
  // (대화 초기화), or the agent-mode off->on toggle above all already call
  // `setAgentSessionId`, so watching the state here is the one place that
  // needs to notify listeners rather than duplicating a dispatch at every
  // call site. Network-viewing pages (app/dashboard/page.tsx,
  // app/network/page.tsx) listen for this to know which session's *working
  // copy* of the network (if any) to show on refresh -- see
  // docs/phase2/DECISIONS.md's P2 entry.
  useEffect(() => {
    window.dispatchEvent(new CustomEvent("vissim:agent-session", { detail: { sessionId: agentSessionId } }));
  }, [agentSessionId]);

  // M4: explicit "start over" control -- the "panel's conversation is
  // cleared" reset trigger session continuity depends on. Aborts any
  // in-flight request first so a stale stream can never repopulate the
  // message list right after it was cleared.
  function resetConversation() {
    requestAbortRef.current?.abort();
    setMessages([]);
    setDeclinedExecutionIds(new Set());
    setAgentSessionId(null);
    setError(null);
  }

  // Core send/re-send path. `targetMessageId` set means this is a confirmed
  // re-run of an existing proposed action: update that message in place
  // instead of appending a new user/assistant bubble pair.
  async function runPrompt(text: string, options?: { allowExecution?: boolean; targetMessageId?: string }) {
    if (!text || loading) return;
    const allowExecution = options?.allowExecution ?? false;
    const targetMessageId = options?.targetMessageId;

    let assistantMessageId: string;
    let streamedContent = "";
    let priorContentOnFailure = "";

    if (targetMessageId) {
      assistantMessageId = targetMessageId;
      setMessages((prev) => {
        const existing = prev.find((message) => message.id === targetMessageId);
        priorContentOnFailure = existing?.content ?? "";
        return prev;
      });
    } else {
      const createdAt = Date.now();
      assistantMessageId = `${createdAt}-assistant`;
      setMessages((prev) => [
        ...prev,
        {
          id: `${createdAt}-user`,
          role: "user",
          content: text,
        },
        {
          id: assistantMessageId,
          role: "assistant",
          content: "",
          sourcePrompt: text,
        },
      ]);
      setPrompt("");
    }

    setError(null);
    setLoading(true);
    setActiveAssistantMessageId(assistantMessageId);
    setRequestPhase("context");
    requestPageContext(pathname);

    const controller = new AbortController();
    requestAbortRef.current = controller;
    try {
      setRequestPhase("querying");
      const response = await streamAssistant(
        {
          message: text,
          selected_scenario_ids: activePageContext?.selectedScenarioIds ?? [],
          selected_node_ids: activePageContext?.selectedNodeIds ?? [],
          // P6: whatever the user clicked in the rendered network travels with
          // the message, so "이 도로 차로 수 알려줘" needs no id typed by hand.
          // The backend re-resolves these against the parsed .inpx and caps/
          // sanitizes them at its own boundary — nothing here is trusted.
          selected_link_ids: activePageContext?.selectedLinks?.map((link) => link.id) ?? [],
          selected_element: activePageContext?.selectedElement ?? null,
          // Consent to run a real VISSIM change is never inferred from the
          // wording of the message (that used to be a client-side keyword
          // match a suggested starter chip could trigger by accident). The
          // first pass always asks; execution only happens when the user
          // explicitly confirms the proposed diff, which resends this same
          // text with allowExecution=true via targetMessageId above.
          allow_tool_execution: allowExecution,
          response_language: lang,
        },
        {
          onStatus: (event) => {
            if (event.phase === "preparing") setRequestPhase("querying");
          },
          onMetadata: (nextResponse) => {
            setStatus(nextResponse.model_status);
            setStatusError(null);
          },
          onChunk: (chunk) => {
            streamedContent += chunk;
            setRequestPhase("streaming");
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId
                  ? {
                      ...message,
                      content: streamedContent,
                    }
                  : message,
              ),
            );
          },
        },
        controller.signal,
      );
      streamedContent = response.answer;
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                content: response.answer,
                response,
              }
            : message,
        ),
      );
      setStatus(response.model_status);
      setStatusError(null);
    } catch (caught) {
      if (!targetMessageId && !streamedContent.trim()) {
        setMessages((prev) => prev.filter((message) => message.id !== assistantMessageId));
      } else if (targetMessageId && !streamedContent.trim()) {
        // A failed confirm re-run must not erase the proposal that was
        // already on screen \u2014 restore it so the user can retry or cancel.
        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantMessageId ? { ...message, content: priorContentOnFailure } : message,
          ),
        );
      }
      if (isAbortError(caught)) {
        setRequestPhase("cancelled");
        setError(t("\uc694\uccad\uc744 \ucde8\uc18c\ud588\uc2b5\ub2c8\ub2e4.", "Request cancelled."));
      } else {
        setError(formatAssistantError(caught, t));
      }
    } finally {
      if (requestAbortRef.current === controller) {
        requestAbortRef.current = null;
      }
      setLoading(false);
      setActiveAssistantMessageId(null);
      setRequestPhase("idle");
      inputRef.current?.focus();
    }
  }

  // M1.4 agent-mode path: read-only, no propose→confirm→execute machinery
  // (that flow is simulation-specific and agent mode never proposes a run),
  // so this stays a separate function rather than another branch inside
  // runPrompt's confirm/resend bookkeeping.
  async function runAgentPrompt(text: string) {
    if (!text || loading) return;
    const createdAt = Date.now();
    const assistantMessageId = `${createdAt}-assistant`;
    setMessages((prev) => [
      ...prev,
      { id: `${createdAt}-user`, role: "user", content: text },
      { id: assistantMessageId, role: "assistant", content: "", agentSteps: [] },
    ]);
    setPrompt("");

    setError(null);
    setLoading(true);
    setActiveAssistantMessageId(assistantMessageId);
    setRequestPhase("querying");

    const controller = new AbortController();
    requestAbortRef.current = controller;
    let streamedContent = "";
    let hasAgentSteps = false;
    try {
      const response = await streamAgentQuery(
        // M4: resume the same backend session as the previous agent-mode
        // turn in this open conversation, if any (see agentSessionId's own
        // comment) -- this is what makes a multi-turn clarification actually
        // work in the UI instead of every send starting a fresh session.
        {
          message: text,
          response_language: lang,
          session_id: agentSessionId ?? undefined,
          // P7: the same on-screen selection the plain assistant path sends
          // (P6), now also reaching the tool-executing agent -- so "이거 고쳐줘"
          // has a referent. Re-sent on every turn (the backend replaces, never
          // merges, so deselecting really deselects), and re-resolved server-
          // side against this session's working copy when it has one. Context
          // only: it names objects, it authorizes nothing -- every edit still
          // goes through the same propose->approve card.
          selected_scenario_ids: activePageContext?.selectedScenarioIds ?? [],
          selected_node_ids: activePageContext?.selectedNodeIds ?? [],
          selected_link_ids: activePageContext?.selectedLinks?.map((link) => link.id) ?? [],
          selected_element: activePageContext?.selectedElement ?? null,
        },
        {
          onStep: (event) => {
            hasAgentSteps = true;
            setRequestPhase("streaming");
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId
                  ? {
                      ...message,
                      agentSteps: [
                        ...(message.agentSteps ?? []),
                        { summary: event.summary ?? "", tool: event.tool ?? "", status: event.status ?? "" },
                      ],
                    }
                  : message,
              ),
            );
          },
          // M2.3: shows the approval card as soon as the turn pauses,
          // ahead of the (short) proposal-line chunks and done — session_id
          // is not on this event (see approvalStateFromEvent), so it starts
          // empty and is reconciled from `response.session_id` below once
          // the stream completes; the card's own buttons stay disabled
          // until then (AgentApprovalCard checks approval.sessionId).
          onApprovalRequest: (event) => {
            setRequestPhase("streaming");
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, agentApproval: approvalStateFromEvent(event, message.agentApproval?.sessionId ?? "") }
                  : message,
              ),
            );
          },
          // RUN-PROGRESS UI: the job now exists, well before the run (and
          // this stream) finishes -- start showing live progress instead of
          // the bare "Preparing answer..." spinner.
          onRunStarted: (event) => {
            if (!event.job_id) return;
            setMessages((prev) =>
              prev.map((message) => (message.id === assistantMessageId ? { ...message, runJobId: event.job_id } : message)),
            );
          },
          onChunk: (chunk) => {
            streamedContent += chunk;
            setRequestPhase("streaming");
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId ? { ...message, content: streamedContent } : message,
              ),
            );
          },
        },
        controller.signal,
      );
      // M4: carry this turn's session id forward to the next agent-mode
      // send in this conversation (see agentSessionId's own comment) --
      // set unconditionally on every completed turn, including a
      // clarification, so the very next message resumes it too.
      setAgentSessionId(response.session_id);
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantMessageId
            ? {
                ...message,
                content: response.answer,
                agentSteps: response.steps,
                agentApproval: response.approval
                  ? approvalStateFromEvent(response.approval, response.session_id)
                  : message.agentApproval,
                wroteModel: response.wrote_model ?? false,
                writeSessionId: response.wrote_model ? response.session_id : undefined,
              }
            : message,
        ),
      );
    } catch (caught) {
      if (caught instanceof AgentStreamDisconnectedError) {
        // Live-bug fix (docs/phase2/DECISIONS.md, 2026-07-24 heartbeat
        // entry): the stream ended (or a mid-read error fired) without ever
        // delivering a `done` event -- e.g. an idle-timeout intermediary
        // killing the connection during a long tool call. Resolve THIS
        // message into a clear, localized error state instead of leaving it
        // stuck on "Preparing answer..." forever (the reported symptom) or
        // silently vanishing -- any step narration/partial answer text that
        // DID arrive before the drop is kept, not discarded.
        const notice = agentStreamDisconnectedMessage(t);
        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantMessageId
              ? { ...message, content: streamedContent.trim() ? `${streamedContent}\n\n${notice}` : notice }
              : message,
          ),
        );
        setError(notice);
      } else {
        // A stream that dies after emitting step narration but before any
        // answer content must keep the message — the step log the user
        // already watched is real work, not noise, so only drop the bubble
        // when there's neither content nor steps to show for it.
        if (!streamedContent.trim() && !hasAgentSteps) {
          setMessages((prev) => prev.filter((message) => message.id !== assistantMessageId));
        }
        if (isAbortError(caught)) {
          setRequestPhase("cancelled");
          setError(t("요청을 취소했습니다.", "Request cancelled."));
        } else {
          setError(formatAssistantError(caught, t));
        }
      }
    } finally {
      if (requestAbortRef.current === controller) {
        requestAbortRef.current = null;
      }
      setLoading(false);
      setActiveAssistantMessageId(null);
      setRequestPhase("idle");
      inputRef.current?.focus();
    }
  }

  // M2.3: resolves the write-approval card on `sourceMessageId` (Run ->
  // approved=true, Cancel -> approved=false) by streaming
  // POST /agent/approve INTO A NEW assistant message appended after it —
  // same step-narration + chunk-accumulation handling as runAgentPrompt,
  // so an approved write's postwrite verification narrates identically to
  // a fresh turn. The card's own message only gets its resolution label
  // updated in place; it is never removed and never re-offered (single-use
  // — a consumed approval_id 404s on any retry, see AgentApprovalError).
  async function runAgentApproval(sourceMessageId: string, approval: AgentApprovalState, approved: boolean) {
    if (loading || approval.resolution !== "pending" || !approval.sessionId) return;

    const createdAt = Date.now();
    const assistantMessageId = `${createdAt}-assistant-approval`;
    const busyResolution: AgentApprovalResolution = approved ? "approving" : "declining";
    setMessages((prev) => [
      ...prev.map((message) =>
        message.id === sourceMessageId && message.agentApproval
          ? { ...message, agentApproval: { ...message.agentApproval, resolution: busyResolution } }
          : message,
      ),
      { id: assistantMessageId, role: "assistant", content: "", agentSteps: [] },
    ]);

    setError(null);
    setLoading(true);
    setActiveAssistantMessageId(assistantMessageId);
    setRequestPhase("querying");

    const controller = new AbortController();
    requestAbortRef.current = controller;
    let streamedContent = "";
    let hasAgentSteps = false;
    try {
      const response = await approveAgentAction(
        { session_id: approval.sessionId, approval_id: approval.approvalId, approved },
        {
          onStep: (event) => {
            hasAgentSteps = true;
            setRequestPhase("streaming");
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId
                  ? {
                      ...message,
                      agentSteps: [
                        ...(message.agentSteps ?? []),
                        { summary: event.summary ?? "", tool: event.tool ?? "", status: event.status ?? "" },
                      ],
                    }
                  : message,
              ),
            );
          },
          // A plan can in principle chain into a second write needing its
          // own approval (a different action, outside this approval's
          // signature) — handled generically here exactly like the first
          // card, on this NEW message, so it can itself be run/cancelled.
          onApprovalRequest: (event) => {
            setRequestPhase("streaming");
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId
                  ? { ...message, agentApproval: approvalStateFromEvent(event, message.agentApproval?.sessionId ?? "") }
                  : message,
              ),
            );
          },
          // RUN-PROGRESS UI: a run-tier action is gated, so its actual job
          // submission happens on THIS approve turn (not the original
          // proposal) -- this is the call site that matters in practice.
          onRunStarted: (event) => {
            if (!event.job_id) return;
            setMessages((prev) =>
              prev.map((message) => (message.id === assistantMessageId ? { ...message, runJobId: event.job_id } : message)),
            );
          },
          onChunk: (chunk) => {
            streamedContent += chunk;
            setRequestPhase("streaming");
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantMessageId ? { ...message, content: streamedContent } : message,
              ),
            );
          },
        },
        controller.signal,
      );
      // M4: keep the threaded session id in sync (it should already equal
      // approval.sessionId — the approve call targets that exact session —
      // but this stays the single source of truth for the NEXT agent-mode
      // send rather than assuming the two can never diverge).
      setAgentSessionId(response.session_id);
      setMessages((prev) =>
        prev.map((message) => {
          if (message.id === assistantMessageId) {
            return {
              ...message,
              content: response.answer,
              agentSteps: response.steps,
              agentApproval: response.approval
                ? approvalStateFromEvent(response.approval, response.session_id)
                : message.agentApproval,
              wroteModel: response.wrote_model ?? false,
              writeSessionId: response.wrote_model ? response.session_id : undefined,
            };
          }
          if (message.id === sourceMessageId && message.agentApproval) {
            return {
              ...message,
              agentApproval: { ...message.agentApproval, resolution: approved ? "approved" : "declined" },
            };
          }
          return message;
        }),
      );
    } catch (caught) {
      // Live-bug fix (docs/phase2/DECISIONS.md, 2026-07-24 heartbeat entry):
      // a stream that ends (or errors mid-read) without ever delivering a
      // `done` event -- e.g. an idle-timeout intermediary killing the
      // connection during a long postwrite run. Computed once so both the
      // narration message below and the approval card's own errorMessage
      // (via resolutionMessage) show identical, localized copy.
      const disconnected = caught instanceof AgentStreamDisconnectedError;
      const disconnectNotice = disconnected ? agentStreamDisconnectedMessage(t) : null;
      if (disconnectNotice) {
        // Keep this message (the approve turn's own narration) and resolve
        // it into the drop notice instead of vanishing it -- mirrors
        // runAgentPrompt's identical fix; any partial narration that DID
        // arrive is kept, not discarded.
        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantMessageId
              ? { ...message, content: streamedContent.trim() ? `${streamedContent}\n\n${disconnectNotice}` : disconnectNotice }
              : message,
          ),
        );
      } else if (!streamedContent.trim() && !hasAgentSteps) {
        setMessages((prev) => prev.filter((message) => message.id !== assistantMessageId));
      }
      const is404 = caught instanceof AgentApprovalError && caught.status === 404;
      const resolutionMessage = is404
        ? t(
            "이 승인 요청은 만료되었거나 이미 처리되었습니다. 다시 요청해 주세요.",
            "This approval request has expired or was already handled. Please ask again.",
            "この承認リクエストは期限切れか、既に処理済みです。もう一度お試しください。",
          )
        : disconnectNotice
          ? disconnectNotice
          : isAbortError(caught)
            ? t("요청을 취소했습니다.", "Request cancelled.")
            : formatAssistantError(caught, t);
      // Settles the source message's approval card busy state
      // ("approving"/"declining") to its error resolution on EVERY caught
      // path, including the dead-stream case above -- a busy card must
      // never stay stuck any more than the message bubble itself.
      setMessages((prev) =>
        prev.map((message) =>
          message.id === sourceMessageId && message.agentApproval
            ? { ...message, agentApproval: { ...message.agentApproval, resolution: "error", errorMessage: resolutionMessage } }
            : message,
        ),
      );
      if (isAbortError(caught)) {
        setRequestPhase("cancelled");
      }
      setError(resolutionMessage);
    } finally {
      if (requestAbortRef.current === controller) {
        requestAbortRef.current = null;
      }
      setLoading(false);
      setActiveAssistantMessageId(null);
      setRequestPhase("idle");
      inputRef.current?.focus();
    }
  }

  async function submitPrompt(nextPrompt?: string) {
    const text = (nextPrompt ?? prompt).trim();
    if (agentMode) {
      await runAgentPrompt(text);
    } else {
      await runPrompt(text);
    }
  }

  function confirmPromptExecution(message: FloatingMessage) {
    if (!message.sourcePrompt || loading) return;
    void runPrompt(message.sourcePrompt, { allowExecution: true, targetMessageId: message.id });
  }

  function declinePromptExecution(messageId: string) {
    setDeclinedExecutionIds((prev) => new Set(prev).add(messageId));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitPrompt();
  }

  function refreshAssistantStatus() {
    setStatusChecking(true);
    setStatusError(null);
    fetchAssistantStatus()
      .then((nextStatus) => {
        setStatus(nextStatus);
        setStatusError(null);
      })
      .catch((caught) => {
        setStatus(null);
        setStatusError(formatAssistantError(caught, t));
      })
      .finally(() => setStatusChecking(false));
  }

  function cancelAssistantRequest() {
    setRequestPhase("cancelled");
    requestAbortRef.current?.abort();
  }

  function beginPanelResize(event: ReactPointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    resizeRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      startWidth: panelSize.width,
      startHeight: panelSize.height,
    };
  }

  return (
    <>
      {open && (
        <section
          className="fixed bottom-[5.75rem] left-3 right-3 z-[2700] flex h-[min(var(--assistant-panel-height),calc(100vh-7.25rem))] min-h-[430px] flex-col overflow-hidden rounded-xl border border-slate-300/80 bg-surface/95 shadow-[0_22px_60px_rgba(15,23,42,0.26)] backdrop-blur md:left-auto md:right-5 md:w-[var(--assistant-panel-width)]"
          style={panelStyle}
          aria-label={t("VISSIM \uc5d0\uc774\uc804\ud2b8 \ucc44\ud305", "VISSIM agent chat")}
        >
          <button
            type="button"
            onPointerDown={beginPanelResize}
            className="absolute left-2 top-2 hidden h-7 w-7 cursor-nwse-resize items-center justify-center rounded-md border border-slate-200 bg-surface/95 text-slate-400 shadow-sm transition hover:border-slate-300 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-accent/35 md:flex"
            aria-label={t("\ub3c4\uc6b0\ubbf8 \ud06c\uae30 \uc870\uc808", "Resize assistant")}
            title={t("\ub3c4\uc6b0\ubbf8 \ud06c\uae30 \uc870\uc808", "Resize assistant")}
          >
            <span className="h-3 w-3 border-l-2 border-t-2 border-current" aria-hidden="true" />
          </button>

          <header className="flex items-start justify-between gap-3 border-b border-slate-200/80 px-4 py-3 md:pl-11">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-bold text-white">
                  AI
                </span>
                <div className="min-w-0">
                  {/* Header identity is now explicitly THE chat -- agent
                      mode is the default and only user-facing mode, so this
                      names the thing it actually is (docs/phase2/DECISIONS.md
                      UX-reorg entry) rather than a generic "assistant". */}
                  <h2 className="truncate text-sm font-bold text-slate-900">{t("VISSIM \uc5d0\uc774\uc804\ud2b8", "VISSIM Agent", "VISSIM \u30a8\u30fc\u30b8\u30a7\u30f3\u30c8")}</h2>
                  <p className="truncate text-xs font-medium text-slate-500">{context.label}</p>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <AssistantStatusPill status={status} checking={statusChecking} />
                <ComStatusPill status={status?.tools?.vissim_com ?? null} />
                <button
                  type="button"
                  onClick={refreshAssistantStatus}
                  disabled={statusChecking}
                  className="rounded-full border border-slate-200 bg-surface px-2 py-1 text-[10px] font-semibold text-slate-500 transition hover:border-slate-300 hover:text-slate-800 disabled:cursor-wait disabled:opacity-70"
                >
                  {statusChecking ? t("\ud655\uc778 \uc911", "Checking") : t("\uc0c1\ud0dc \uc0c8\ub85c\uace0\uce68", "Refresh status")}
                </button>
                <button
                  type="button"
                  onClick={resetConversation}
                  disabled={loading || messages.length === 0}
                  className="rounded-full border border-slate-200 bg-surface px-2 py-1 text-[10px] font-semibold text-slate-500 transition hover:border-slate-300 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                  title={t(
                    "\ub300\ud654\uc640 \uc5d0\uc774\uc804\ud2b8 \uc138\uc158\uc744 \ucd08\uae30\ud654\ud569\ub2c8\ub2e4",
                    "Resets the conversation and the agent session",
                    "\u4f1a\u8a71\u3068\u30a8\u30fc\u30b8\u30a7\u30f3\u30c8\u30bb\u30c3\u30b7\u30e7\u30f3\u3092\u30ea\u30bb\u30c3\u30c8\u3057\u307e\u3059",
                  )}
                >
                  {t("\ub300\ud654 \ucd08\uae30\ud654", "New conversation", "\u4f1a\u8a71\u3092\u30ea\u30bb\u30c3\u30c8")}
                </button>
              </div>
              <PageContextPills context={activePageContext} />
              <AssistantRuntimeNote status={status} statusError={statusError} checking={statusChecking} />
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-surface text-sm font-bold text-slate-500 transition hover:border-slate-300 hover:text-slate-900"
              aria-label={t("\ub3c4\uc6b0\ubbf8 \ub2eb\uae30", "Close assistant")}
            >
              x
            </button>
          </header>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            {messages.length === 0 ? (
              <div className="grid gap-2">
                <PageContextSummary context={activePageContext} routeLabel={context.label} />
                {context.starters.map((starter) => (
                  <button
                    key={starter}
                    type="button"
                    onClick={() => setPrompt(starter)}
                    className="rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2 text-left text-xs font-medium leading-5 text-slate-600 transition hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700"
                  >
                    {starter}
                  </button>
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                {messages.map((message) => (
                  <FloatingChatMessage
                    key={message.id}
                    message={message}
                    activePhase={message.id === activeAssistantMessageId && loading ? requestPhase : undefined}
                    onCancel={message.id === activeAssistantMessageId ? cancelAssistantRequest : undefined}
                    declined={declinedExecutionIds.has(message.id)}
                    onConfirmExecute={() => confirmPromptExecution(message)}
                    onDeclineExecute={() => declinePromptExecution(message.id)}
                    onResolveApproval={(approved) => {
                      if (message.agentApproval) void runAgentApproval(message.id, message.agentApproval, approved);
                    }}
                  />
                ))}
              </div>
            )}

            {error && (
              <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700">
                {error}
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="border-t border-slate-200/80 bg-slate-50/70 p-3">
            <div className="flex items-end gap-2 rounded-xl border border-slate-300/80 bg-surface px-3 py-2 shadow-sm">
              <textarea
                ref={inputRef}
                rows={2}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={(event) => handleComposerKeyDown(event, submitPrompt)}
                placeholder={t("VISSIM\uc5d0 \ubb3b\uac70\ub098, \uc9c4\ub2e8\ud558\uac70\ub098, \ubcc0\uacbd\uc744 \uc2e4\ud589\ud574\ubcf4\uc138\uc694...", "Ask, diagnose, or run a VISSIM change...")}
                className="max-h-28 min-h-10 flex-1 resize-none bg-transparent text-sm leading-5 text-slate-900 outline-none placeholder:text-slate-400"
              />
              <button
                type="submit"
                disabled={!prompt.trim() || loading}
                className="ui-button-primary flex h-10 w-10 shrink-0 items-center justify-center text-sm font-bold disabled:cursor-not-allowed"
                aria-label={t("\uba54\uc2dc\uc9c0 \ubcf4\ub0b4\uae30", "Send prompt")}
              >
                &gt;
              </button>
            </div>
          </form>
        </section>
      )}

      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="fixed bottom-5 right-5 z-[2690] flex h-14 w-14 items-center justify-center rounded-full border border-white/70 bg-accent text-sm font-bold text-white shadow-[0_16px_34px_rgba(15,23,42,0.28)] transition hover:-translate-y-0.5 hover:bg-accent-deep focus:outline-none focus:ring-4 focus:ring-accent/35"
        aria-label={open ? t("\ub3c4\uc6b0\ubbf8 \ub2eb\uae30", "Close assistant") : t("\ub3c4\uc6b0\ubbf8 \uc5f4\uae30", "Open assistant")}
      >
        {open ? "x" : "AI"}
      </button>
    </>
  );
}

function FloatingChatMessage({
  message,
  activePhase,
  onCancel,
  declined,
  onConfirmExecute,
  onDeclineExecute,
  onResolveApproval,
}: {
  message: FloatingMessage;
  activePhase?: AssistantRequestPhase;
  onCancel?: () => void;
  declined: boolean;
  onConfirmExecute: () => void;
  onDeclineExecute: () => void;
  /** M2.3: resolves this message's write-approval card, if it has one. */
  onResolveApproval: (approved: boolean) => void;
}) {
  const isUser = message.role === "user";
  const promptSimulation = getPromptSimulationResult(message.response?.tool_results);
  const { t } = useLanguage();
  // True both for the very first (empty) render of this message and for a
  // confirmed re-run (message.response already holds the prior proposal).
  const isActiveAssistant = !isUser && Boolean(activePhase);
  // M1.4 agent-mode step narration — stays visible after completion (it's
  // the agent's read-only work log for this turn), so this only checks
  // presence, not activePhase.
  const hasAgentSteps = !isUser && Boolean(message.agentSteps && message.agentSteps.length > 0);
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={
          isUser
            ? "max-w-[86%] rounded-lg bg-accent px-3 py-2 text-sm leading-6 text-white shadow-sm selection:bg-surface selection:text-blue-700"
            : "max-w-[94%] rounded-lg border border-slate-200 bg-surface px-3 py-2 text-sm leading-6 text-slate-800 shadow-sm"
        }
      >
        {isUser ? (
          <div className="whitespace-pre-wrap">{message.content}</div>
        ) : !message.content.trim() && !message.response && !hasAgentSteps ? (
          // RUN-PROGRESS UI: while a run_simulation dispatch is in flight for
          // THIS message, message.runJobId is set (from the run_started
          // stream event) well before any content/step narration arrives --
          // show honest seed-by-seed progress instead of the bare
          // "Preparing answer..." spinner that used to sit here for the
          // run's entire duration (the reported live-demo anxiety).
          message.runJobId && isActiveAssistant ? (
            <AgentRunProgressCard jobId={message.runJobId} onCancel={onCancel} />
          ) : (
            <AssistantInlineProgress phase={activePhase ?? "querying"} onCancel={onCancel} />
          )
        ) : (
          <>
            {hasAgentSteps && <AgentStepNarration steps={message.agentSteps!} />}
            <AssistantAnswerContent content={message.content} response={message.response} />
            {hasAgentSteps && <AgentRunDashboardHint steps={message.agentSteps} />}
            {isActiveAssistant &&
              (message.runJobId ? (
                <AgentRunProgressCard jobId={message.runJobId} onCancel={onCancel} compact />
              ) : (
                <AssistantInlineProgress phase={activePhase ?? "streaming"} onCancel={onCancel} compact />
              ))}
          </>
        )}
        {!isUser && message.agentApproval && (
          <AgentApprovalCard approval={message.agentApproval} onRun={() => onResolveApproval(true)} onCancelApproval={() => onResolveApproval(false)} />
        )}
        {!isUser && message.wroteModel && message.writeSessionId && (
          <AgentModelDownloadLink sessionId={message.writeSessionId} />
        )}
        {!isUser && message.response && <AssistantRuntimeBadge response={message.response} />}
        {promptSimulation && (
          <PromptSimulationStatus
            result={promptSimulation}
            declined={declined}
            confirming={isActiveAssistant}
            onConfirmExecute={onConfirmExecute}
            onDeclineExecute={onDeclineExecute}
          />
        )}
        {message.response && <CompactSources sources={message.response.sources} />}
      </div>
    </div>
  );
}

// M1.4 agent-mode narration: a compact, text-led log of the read-only
// agent's steps — small muted lines, no icons or per-step spinners. Status
// only renders when a step failed; a successful step is just its summary.
// M2.3 adds "pending_approval": the step that paused for a write approval —
// rendered with the same neutral tone as a plain step (not the red error
// style), since it is expected/normal, not a failure.
function AgentStepNarration({ steps }: { steps: AgentStep[] }) {
  const { t } = useLanguage();
  return (
    <div className="mb-2 space-y-0.5 border-b border-slate-100 pb-2">
      {steps.map((step, index) => (
        <p key={index} className="text-[11px] leading-5 text-slate-500">
          {index + 1}. {step.summary}
          {step.status === "error" && (
            <span className="ml-1 font-semibold text-rose-600">({t("오류", "error", "エラー")})</span>
          )}
          {step.status === "pending_approval" && (
            <span className="ml-1 font-semibold text-slate-500">({t("승인 대기", "awaiting approval", "承認待ち")})</span>
          )}
        </p>
      ))}
    </div>
  );
}

// M4 roadmap bullet ("Post-run: link into the existing dashboard compare
// surfaces"). Investigated against the backend rather than built blind: a
// successful `run_simulation` dispatch submits to the SAME job queue/registry
// `POST /api/simulations` already uses (backend/services/agent/tools_run.py
// -> routers/simulations.py's `start_agent_simulation_job` ->
// services/simulation_runs.py's `create_job_record`, which stamps a
// `scenario_result_id` ("SIM_<job_id>") and the ACTIVE dataset's revision at
// submit time exactly like every other job). Once that job reaches "done",
// `list_completed_runtime_scenarios()` (filtered only by a matching dataset
// revision) already feeds `services/results_reader.list_scenarios()`, which
// is exactly what `GET /api/scenarios` -- the same endpoint the dashboard's
// scenario picker calls via `fetchScenarios()` -- returns. So the agent's run
// lands a real, comparable scenario with ZERO backend wiring, as long as the
// active dataset was not swapped between the agent run and viewing the
// dashboard. This hint is therefore frontend-only: it fires whenever this
// turn's step log contains a successfully dispatched `run_simulation` step
// (`AgentStep.status === "ok"` -- toolkit.dispatch's own success marker) and
// links into `/dashboard`, where that new scenario is already selectable.
// The exact scenario id is NOT threaded through (AgentStep only carries
// summary/tool/status on the wire today, per api.ts) -- a generic pointer to
// the picker satisfies the roadmap bullet without inventing a new wire
// field; see the M4 report for the "zero/minimal wiring" finding in full.
function AgentRunDashboardHint({ steps }: { steps?: AgentStep[] }) {
  const pathname = usePathname();
  const { t } = useLanguage();
  const ranStep = (steps ?? []).find((step) => step.tool === "run_simulation" && step.status === "ok");
  if (!ranStep) return null;

  const message = t(
    "결과가 대시보드 시나리오 목록에 추가되었습니다.",
    "The result was added to the dashboard's scenario list.",
    "結果はダッシュボードのシナリオ一覧に追加されました。",
  );

  // trajectory_recorded (backend/services/agent/orchestrator.py's
  // step-emission choke point, read off run_simulation's own returned
  // data) -- a real completed run's vehicle-by-vehicle replay, extending
  // Phase 3's playback beyond the demo/synthetic source. Smallest
  // reasonable wiring: a second, independent note pointing at /network, not
  // a redesign of the existing dashboard hint above it.
  const replayMessage = ranStep.trajectory_recorded
    ? t(
        "차량 궤적도 기록되었습니다 — 네트워크 프리뷰에서 재생할 수 있습니다.",
        "Vehicle trajectories were also recorded — replay is available in the network preview.",
        "車両の軌跡も記録されました — ネットワークプレビューで再生できます。",
      )
    : null;

  if (pathname.startsWith("/dashboard")) {
    return (
      <div className="mt-2 border-t border-slate-100 pt-2 text-[11px] leading-5 text-slate-500">
        <p>{message}</p>
        {replayMessage && (
          <p className="mt-1">
            {replayMessage}{" "}
            <Link href="/network" className="font-semibold text-blue-600 underline underline-offset-2 hover:text-blue-700">
              {t("네트워크 프리뷰로 이동", "Go to network preview", "ネットワークプレビューへ")}
            </Link>
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="mt-2 border-t border-slate-100 pt-2 text-[11px] leading-5 text-slate-500">
      <p>
        {message}{" "}
        <Link href="/dashboard" className="font-semibold text-blue-600 underline underline-offset-2 hover:text-blue-700">
          {t("대시보드에서 보기", "View in dashboard", "ダッシュボードで見る")}
        </Link>
      </p>
      {replayMessage && (
        <p className="mt-1">
          {replayMessage}{" "}
          {!pathname.startsWith("/network") && (
            <Link href="/network" className="font-semibold text-blue-600 underline underline-offset-2 hover:text-blue-700">
              {t("네트워크 프리뷰로 이동", "Go to network preview", "ネットワークプレビューへ")}
            </Link>
          )}
        </p>
      )}
    </div>
  );
}

// M4 roadmap bullet ("per-action preflight status chips (pass/warn/refuse)"):
// 통과 (pass, neutral gray) when this action's approval carries zero
// preflight warnings, 주의 N (warn, amber) with the fired count otherwise.
// No "refuse" tone exists here BY DESIGN, not omission: verified against
// backend/services/agent/orchestrator.py -- a preflight report with any
// BLOCKING finding returns a "refusal" turn (kind: "answer", a structured
// refusal envelope) BEFORE `pending_approval` is ever constructed (see the
// `if preflight_report.blocking:` branch, which short-circuits ahead of the
// `pending_approval = {...}` assignment a few lines below it). A refused
// action can therefore never reach an approval_request event or this card
// at all -- only WARN-tier findings (or none) ever ride along here.
//
// `warningCount` is `approval.warnings.length`, not a per-action slice:
// backend/services/agent/orchestrator.py's `pending_approval` is always
// built from a SINGLE proposed action (`action_entry`/`pending_approval`
// there are singular objects, never a list) and `warnings` is that one
// action's own preflight findings -- so every action row getting the same
// count is exact for the only shape the backend emits today, not an
// approximation. If a future milestone teaches the backend to propose
// multiple actions in one approval, this chip would need a real per-action
// warning mapping from the wire contract; tracked here so that gap is not
// silently reintroduced.
function PreflightStatusChip({ warningCount }: { warningCount: number }) {
  const { t } = useLanguage();
  if (warningCount <= 0) {
    return (
      <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 ring-1 ring-emerald-200">
        {t("통과", "Pass", "通過")}
      </span>
    );
  }
  return (
    <span className="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700 ring-1 ring-amber-200">
      {t(`주의 ${warningCount}`, `Warn ${warningCount}`, `注意 ${warningCount}`)}
    </span>
  );
}

// M2.3 write-approval card: the action list + warnings for an agent-mode
// write turn paused via approval_request. Console-inspired card language
// (bg-surface, slate-200 hairline, rounded-xl, no colored left border, no
// shadow) — distinct on purpose from the regex-flow's inline
// PromptSimulationStatus confirm strip below, since this one carries a
// multi-action list, per-action params, and warnings rather than a single
// yes/no prompt.
// P8 (edited-model download): a small, non-intrusive affordance shown under
// an agent-mode answer the moment `wroteModel` is true (see FloatingMessage's
// own comment) -- streams the LATEST saved version of this session's edited
// .inpx (zip with sibling assets when any exist, else bare .inpx --
// backend/routers/agent_downloads.py) via the same blob + anchor-click
// pattern app/downloads/page.tsx already uses for every other deliverable,
// so it behaves identically to every other "download" button in this app.
function AgentModelDownloadLink({ sessionId }: { sessionId: string }) {
  const { t } = useLanguage();
  const [status, setStatus] = useState<"idle" | "busy" | "error">("idle");

  async function handleClick() {
    if (status === "busy") return;
    setStatus("busy");
    try {
      const { blob, filename } = await downloadAgentModel(sessionId, null);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setStatus("idle");
    } catch {
      setStatus("error");
    }
  }

  return (
    <div className="mt-2 flex items-center gap-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={status === "busy"}
        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-surface px-2.5 py-1.5 text-[11px] font-semibold text-slate-700 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 disabled:cursor-wait disabled:opacity-60"
      >
        {status === "busy"
          ? t("모델 준비 중…", "Preparing model…", "モデル準備中…")
          : t("편집된 모델 다운로드", "Download edited model", "編集済みモデルをダウンロード")}
      </button>
      {status === "error" && (
        <span className="text-[11px] font-medium text-rose-600">
          {t("다운로드에 실패했습니다", "Download failed", "ダウンロードに失敗しました")}
        </span>
      )}
    </div>
  );
}

function AgentApprovalCard({
  approval,
  onRun,
  onCancelApproval,
}: {
  approval: AgentApprovalState;
  onRun: () => void;
  onCancelApproval: () => void;
}) {
  const { t } = useLanguage();
  const busy = approval.resolution === "approving" || approval.resolution === "declining";
  const actionable = approval.resolution === "pending" || busy;
  const runDisabled = busy || !approval.sessionId;

  return (
    <div className="mt-2 rounded-xl border border-slate-200 bg-surface px-3 py-2.5">
      <p className="text-xs font-bold text-slate-800">
        {t("실행 전 확인이 필요합니다", "Approval needed before executing", "実行前に確認が必要です")}
      </p>

      <div className="mt-2 space-y-1.5">
        {approval.actions.map((action, index) => (
          <div key={`${action.tool}-${index}`} className="rounded-lg bg-slate-50 px-2.5 py-2">
            <div className="flex items-start justify-between gap-2">
              <p className="text-[11px] font-semibold text-slate-700">{agentActionLabel(action.tool, t)}</p>
              <PreflightStatusChip warningCount={approval.warnings.length} />
            </div>
            <p className="mt-0.5 text-[11px] text-slate-600">{agentActionDetail(action, t)}</p>
          </div>
        ))}
      </div>

      {approval.focus?.bbox && (
        <ApprovalFocusMap focus={approval.focus} sessionId={approval.sessionId} action={approval.actions[0]} />
      )}

      {approval.warnings.length > 0 && (
        <div className="mt-2 space-y-1">
          {approval.warnings.map((warning, index) => (
            <p
              key={`${warning.constraint_id || warning.message}-${index}`}
              className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] leading-5 text-amber-800"
            >
              {warning.message}
            </p>
          ))}
        </div>
      )}

      {approval.assumptions.length > 0 && (
        <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5">
          <p className="text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">{t("가정", "Assumptions", "前提")}</p>
          <ul className="mt-1 space-y-0.5">
            {approval.assumptions.map((assumption, index) => (
              <li key={index} className="text-[11px] leading-5 text-slate-600">
                {assumption}
              </li>
            ))}
          </ul>
        </div>
      )}

      {actionable ? (
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            onClick={onRun}
            disabled={runDisabled}
            className="ui-button-primary inline-flex h-9 items-center px-4 text-xs disabled:cursor-not-allowed disabled:opacity-50"
          >
            {approval.resolution === "approving" ? t("실행 중...", "Running...", "実行中...") : t("실행", "Run", "実行")}
          </button>
          <button
            type="button"
            onClick={onCancelApproval}
            disabled={runDisabled}
            className="ui-button-ghost inline-flex h-9 items-center px-4 text-xs disabled:cursor-not-allowed disabled:opacity-50"
          >
            {approval.resolution === "declining" ? t("취소 중...", "Cancelling...", "キャンセル中...") : t("취소", "Cancel", "キャンセル")}
          </button>
        </div>
      ) : approval.resolution === "error" ? (
        <p className="mt-2.5 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[11px] leading-5 text-rose-700">
          {approval.errorMessage}
        </p>
      ) : (
        <p className="mt-2.5 text-[11px] font-semibold text-slate-500">
          {approval.resolution === "approved" ? t("승인됨", "Approved", "承認済み") : t("취소됨", "Cancelled", "キャンセル済み")}
        </p>
      )}
    </div>
  );
}

// M2.3 tool -> human label map for the approval card (docs/phase2/02-ROADMAP.md
// M4's "plan-approval card" bullet). Falls back to the raw tool name for
// anything not in this table (e.g. a future write verb landing ahead of a
// frontend update).
const AGENT_ACTION_LABELS: Record<string, [string, string, string]> = {
  set_vehicle_input_volume: ["차량 입력 교통량 변경", "Change vehicle input volume", "車両入力交通量の変更"],
  set_signal_cycle: ["신호 주기 변경", "Change signal cycle", "信号周期の変更"],
  set_signal_offset: ["신호 옵셋 변경", "Change signal offset", "信号オフセットの変更"],
  set_sim_duration: ["시뮬레이션 시간 변경", "Change simulation duration", "シミュレーション時間の変更"],
  set_random_seed: ["랜덤 시드 변경", "Change random seed", "ランダムシードの変更"],
  run_simulation: ["시뮬레이션 실행", "Run simulation", "シミュレーション実行"],
};

function agentActionLabel(tool: string, t: Translate): string {
  const entry = AGENT_ACTION_LABELS[tool];
  return entry ? t(entry[0], entry[1], entry[2]) : tool;
}

// Readable "field -> new value" line per action, param names matching
// backend/services/agent/tools_edit.py's pydantic params models exactly
// (input_no/volume, sc_no/cycle, sc_no/offset, seconds, seed). Falls back to
// a plain key=value dump for an unrecognized tool.
function agentActionDetail(action: AgentAction, t: Translate): string {
  const params = action.params ?? {};
  switch (action.tool) {
    case "set_vehicle_input_volume":
      return t(
        `차량 입력 ${params.input_no} → ${params.volume} veh/h`,
        `Vehicle input ${params.input_no} → ${params.volume} veh/h`,
        `車両入力 ${params.input_no} → ${params.volume} veh/h`,
      );
    case "set_signal_cycle":
      return t(
        `신호제어기 ${params.sc_no} 주기 → ${params.cycle}초`,
        `Signal controller ${params.sc_no} cycle → ${params.cycle}s`,
        `信号制御機 ${params.sc_no} 周期 → ${params.cycle}秒`,
      );
    case "set_signal_offset":
      return t(
        `신호제어기 ${params.sc_no} 옵셋 → ${params.offset}초`,
        `Signal controller ${params.sc_no} offset → ${params.offset}s`,
        `信号制御機 ${params.sc_no} オフセット → ${params.offset}秒`,
      );
    case "set_sim_duration":
      return t(
        `시뮬레이션 시간 → ${params.seconds}초`,
        `Simulation duration → ${params.seconds}s`,
        `シミュレーション時間 → ${params.seconds}秒`,
      );
    case "set_random_seed":
      return t(`랜덤 시드 → ${params.seed}`, `Random seed → ${params.seed}`, `ランダムシード → ${params.seed}`);
    case "run_simulation": {
      const seeds = params.random_seeds as number[] | null | undefined;
      const seedsLabel =
        seeds && seeds.length > 0
          ? t(`시드 ${seeds.join(", ")}`, `seeds ${seeds.join(", ")}`, `シード ${seeds.join(", ")}`)
          : t("기본 시드", "default seed", "デフォルトシード");
      return t(
        `${params.duration_s}초 실행 · ${seedsLabel}`,
        `Run for ${params.duration_s}s · ${seedsLabel}`,
        `${params.duration_s}秒実行 · ${seedsLabel}`,
      );
    }
    default:
      return Object.entries(params)
        .map(([key, value]) => `${key}=${String(value)}`)
        .join(", ");
  }
}

function PageContextPills({ context }: { context: AssistantPageContext | null }) {
  const { t } = useLanguage();
  const chips = buildContextChips(context, t);
  if (!chips.length) return null;

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {chips.slice(0, 4).map((chip) => (
        <span key={chip} className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
          {chip}
        </span>
      ))}
    </div>
  );
}

function PageContextSummary({
  context,
  routeLabel,
}: {
  context: AssistantPageContext | null;
  routeLabel: string;
}) {
  const { t } = useLanguage();
  const chips = buildContextChips(context, t);
  return (
    <div className="rounded-lg border border-slate-200 bg-surface px-3 py-2">
      <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-slate-400">{routeLabel}</div>
      {chips.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <span key={chip} className="rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-600">
              {chip}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-1 text-xs leading-5 text-slate-500">{t("\uc544\uc9c1 \ud398\uc774\uc9c0 \uc120\ud0dd \ucee8\ud14d\uc2a4\ud2b8\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.", "No page selection is active yet.")}</p>
      )}
      {context?.notes && context.notes.length > 0 && (
        <p className="mt-2 text-[11px] leading-5 text-slate-500">{context.notes.slice(0, 2).join(" ")}</p>
      )}
    </div>
  );
}

// M4 roadmap bullet ("refusals rendered as explanatory blocks ... verification
// evidence in the final answer" + the deterministic-suffix polish item): the
// literal markers backend/services/agent/orchestrator.py's
// `_append_warning_block` prepends to its two deterministic notice headers
// (_AGENTIC_WARNING_NOTICE_HEADER, _CARRIED_FORWARD_WARNINGS_HEADER) --
// "참고:"/"Note:"/"注意:" -- checked at the START of a trimmed line only, so
// an ordinary sentence that happens to use the word "note" mid-paragraph is
// never misdetected.
const DETERMINISTIC_NOTICE_MARKERS = ["참고:", "Note:", "注意:"];

// Splits a deterministic warning-suffix block (header line + "- message"
// bullets, always appended after a blank line -- see `_append_warning_block`
// in backend/services/agent/orchestrator.py) off the end of an answer, so it
// can render as a visually distinct block instead of run-on paragraph text
// inside the normal section flow. Finds the FIRST line starting with a known
// marker and treats everything from there to the end of the content as the
// notice (both of orchestrator.py's notice callers only ever APPEND, never
// prepend, so "marker line to end" is exactly the suffix). Robust to
// absence: no marker line anywhere returns the content unchanged and
// `notice: null`.
function splitDeterministicNotice(content: string): { main: string; notice: string | null } {
  const lines = content.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const trimmed = lines[index].trimStart();
    if (DETERMINISTIC_NOTICE_MARKERS.some((marker) => trimmed.startsWith(marker))) {
      const notice = lines.slice(index).join("\n").trim();
      if (!notice) continue;
      return { main: lines.slice(0, index).join("\n").trimEnd(), notice };
    }
  }
  return { main: content, notice: null };
}

function AssistantAnswerContent({
  content,
  response,
}: {
  content: string;
  response?: AssistantQueryResponse;
}) {
  const { lang } = useLanguage();
  const { main, notice } = splitDeterministicNotice(content);
  const sections = parseAssistantAnswer(main, response);

  const mainContent =
    sections.length === 1 && sections[0].heading === "답변" ? (
      <AnswerLines lines={sections[0].lines} />
    ) : (
      <div className="space-y-2">
        {sections.map((section) => (
          <section key={`${section.heading}-${section.lines.join("").slice(0, 16)}`} className="border-l-2 border-blue-200 pl-3">
            <div className="text-[11px] font-bold uppercase tracking-[0.08em] text-blue-600">{localizeHeading(section.heading, lang)}</div>
            <div className="mt-1">
              <AnswerLines lines={section.lines} />
            </div>
          </section>
        ))}
      </div>
    );

  if (!notice) return mainContent;

  return (
    <div className="space-y-2">
      {mainContent}
      <AssistantNoticeBlock text={notice} />
    </div>
  );
}

// M4: the deterministic warning-suffix block, styled as a muted callout with
// a top divider -- distinct from ordinary paragraph text and from the
// approval card's amber warning strips (this can appear on ANY answer, not
// just a write approval), so a presenter/user's eye separates "the model's
// own answer" from "a guardrail system note attached underneath it". The
// header line (the "참고:"/"Note:"/"注意:" line itself) renders slightly
// bolder than its bullets, same visual grammar AnswerLines already uses for
// label lines elsewhere in this component.
function AssistantNoticeBlock({ text }: { text: string }) {
  const lines = text.split(/\r?\n/);
  const [headerLine, ...restLines] = lines;
  return (
    <div className="mt-1 border-t border-dashed border-slate-200 pt-2">
      <div className="rounded-md bg-slate-50 px-2.5 py-2 text-slate-600">
        <p className="text-[11px] font-semibold leading-5 text-slate-700">{headerLine}</p>
        <AnswerLines lines={restLines} />
      </div>
    </div>
  );
}

function AnswerLines({ lines }: { lines: string[] }) {
  const visibleLines = lines.map((line) => line.trim()).filter(Boolean);
  if (!visibleLines.length) return null;

  // Build a small hierarchy: numbered items (1.) are group headers, lines that
  // are just a label ending in a colon (e.g. "해결책:") are sub-headings, and the
  // bullets/text under them get indented so the levels are visually distinct.
  type Node = { type: "num" | "label" | "bullet" | "text"; text: string; pad: string };
  const nodes: Node[] = [];
  let underNumber = false;
  let underLabel = false;

  for (const line of visibleLines) {
    const numbered = line.match(/^(\d+[.)]\s+.+)$/);
    if (numbered) {
      underNumber = true;
      underLabel = false;
      nodes.push({ type: "num", text: numbered[1], pad: "" });
      continue;
    }
    const labelOnly = line.replace(/^[-•]\s*/, "").match(/^([^:：]{1,24})[:：]$/);
    if (labelOnly) {
      underLabel = true;
      nodes.push({ type: "label", text: `${labelOnly[1].trim()}:`, pad: underNumber ? "pl-4" : "" });
      continue;
    }
    const pad = underLabel ? (underNumber ? "pl-8" : "pl-4") : underNumber ? "pl-4" : "";
    const bullet = line.match(/^[-•]\s+(.+)$/);
    nodes.push(bullet ? { type: "bullet", text: bullet[1], pad } : { type: "text", text: line, pad });
  }

  return (
    <div className="space-y-1">
      {nodes.map((node, index) => {
        if (node.type === "num") {
          return (
            <div key={index} className="mt-1.5 text-sm font-semibold leading-6 text-slate-800">
              {node.text}
            </div>
          );
        }
        if (node.type === "label") {
          return (
            <div key={index} className={`mt-0.5 text-sm font-semibold leading-6 text-slate-600 ${node.pad}`}>
              {node.text}
            </div>
          );
        }
        if (node.type === "bullet") {
          return (
            <div key={index} className={`flex gap-2 text-sm leading-6 ${node.pad}`}>
              <span className="mt-[0.62rem] h-1 w-1 shrink-0 rounded-full bg-slate-400" />
              <span>{node.text}</span>
            </div>
          );
        }
        return (
          <p key={index} className={`whitespace-pre-wrap text-sm leading-6 ${node.pad}`}>
            {node.text}
          </p>
        );
      })}
    </div>
  );
}

function PromptSimulationStatus({
  result,
  declined,
  confirming,
  onConfirmExecute,
  onDeclineExecute,
}: {
  result: PromptSimulationToolResult;
  declined: boolean;
  confirming: boolean;
  onConfirmExecute: () => void;
  onDeclineExecute: () => void;
}) {
  const { t } = useLanguage();
  const jobId = result.job?.id;
  const [job, setJob] = useState<SimulationJob | null>(null);
  const [results, setResults] = useState<SimulationResultsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dispatchedRef = useRef(false);

  useEffect(() => {
    if (!jobId || !result.executed) return;
    const activeJobId = jobId;
    let active = true;

    async function refresh() {
      try {
        const nextJob = await fetchSimulationJob(activeJobId);
        if (!active) return;
        setJob(nextJob);

        if (nextJob.status === "done") {
          const scenarioResultId = nextJob.scenario_result_id ?? result.job?.scenario_result_id ?? null;
          if (!dispatchedRef.current) {
            dispatchedRef.current = true;
            window.dispatchEvent(
              new CustomEvent("vissim:assistant-simulation-complete", {
                detail: {
                  jobId: activeJobId,
                  scenarioResultId,
                },
              }),
            );
          }

          try {
            const nextResults = await fetchSimulationResults(activeJobId);
            if (active) setResults(nextResults);
          } catch (caught) {
            if (active) setError(caught instanceof Error ? caught.message : String(caught));
          }
        }
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : String(caught));
      }
    }

    void refresh();
    const interval = window.setInterval(() => {
      void refresh();
    }, 1500);

    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [jobId, result.executed, result.job?.scenario_result_id]);

  const currentStatus = job?.status ?? result.job?.status ?? (result.executed ? "queued" : "ready");
  const progress = Math.round(Math.max(0, Math.min(1, job?.progress ?? 0)) * 100);

  return (
    <div className="mt-2 border-t border-slate-100 pt-2 text-xs text-slate-600">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-semibold text-slate-800">{t("\uc2dc\ubbac\ub808\uc774\uc158", "Simulation")}</span>
        {jobId && <span className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-500">{jobId}</span>}
        <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-600">{currentStatus}</span>
      </div>

      {result.changes && result.changes.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {result.changes.slice(0, 4).map((change) => (
            <span key={change} className="rounded-full bg-slate-50 px-2 py-0.5 text-[10px] font-semibold text-slate-500 ring-1 ring-slate-200">
              {change}
            </span>
          ))}
        </div>
      )}

      {result.executed && currentStatus !== "done" && currentStatus !== "error" && (
        <div className="mt-2">
          <div className="mb-1 flex justify-between text-[10px] text-slate-400">
            <span>{t("\uc9c4\ud589\ub960", "Progress")}</span>
            <span>{progress}%</span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-slate-100">
            <div className="h-1.5 rounded-full bg-blue-500 transition-all duration-500" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      {currentStatus === "error" && (
        <p className="mt-2 rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] text-rose-700">
          {job?.error ?? result.reason ?? t("\uc2dc\ubbac\ub808\uc774\uc158\uc774 \uc2e4\ud328\ud588\uc2b5\ub2c8\ub2e4.", "Simulation failed.")}
        </p>
      )}

      {results && <PromptSimulationMetrics results={results} />}

      {!result.executed && result.requires_confirmation && !declined && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2">
          <p className="text-[11px] font-semibold text-amber-800">
            {t(
              "이 변경을 실제 VISSIM 시뮬레이션에 적용할까요? 위 변경 내용을 확인 후 실행하세요.",
              "Apply this change to a real VISSIM simulation? Review the changes above before running.",
            )}
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={onConfirmExecute}
              disabled={confirming}
              className="ui-button-primary rounded-md px-3 py-1.5 text-[11px] font-bold disabled:cursor-not-allowed disabled:opacity-60"
            >
              {confirming ? t("실행 중...", "Running...") : t("시뮬레이션 실행", "Run simulation")}
            </button>
            <button
              type="button"
              onClick={onDeclineExecute}
              disabled={confirming}
              className="rounded-md border border-slate-300 bg-surface px-3 py-1.5 text-[11px] font-semibold text-slate-700 transition hover:border-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {t("취소", "Cancel")}
            </button>
          </div>
        </div>
      )}
      {!result.executed && result.requires_confirmation && declined && (
        <p className="mt-2 text-[11px] font-semibold text-slate-500">
          {t("시뮬레이션 실행을 취소했습니다.", "Simulation run was cancelled.")}
        </p>
      )}
      {!result.executed && !result.requires_confirmation && result.reason && (
        <p className="mt-2 text-[11px] text-slate-500">{result.reason}</p>
      )}
      {error && <p className="mt-2 text-[11px] text-rose-600">{error}</p>}
    </div>
  );
}

function PromptSimulationMetrics({ results }: { results: SimulationResultsResponse }) {
  const { t } = useLanguage();
  const network = results.results.network ?? {};
  const metrics = [
    { label: t("\uc18d\ub3c4", "Speed"), value: network.SPEEDAVG, unit: "km/h" },
    { label: t("\uc9c0\uccb4", "Delay"), value: network.DELAYAVG, unit: "s" },
    { label: t("\uc815\uc9c0", "Stops"), value: network.STOPSAVG, unit: "" },
    { label: t("\ub3c4\ucc29", "Arrivals"), value: network.VEHARR, unit: "veh" },
  ].filter((metric) => typeof metric.value === "number");

  if (!metrics.length) {
    return <p className="mt-2 text-[11px] text-slate-500">{t("\uc0ac\uc6a9 \uac00\ub2a5\ud55c \ub124\ud2b8\uc6cc\ud06c \uc694\uc57d \uc9c0\ud45c\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.", "No network summary metrics were available.")}</p>;
  }

  return (
    <div className="mt-2 grid grid-cols-2 gap-2">
      {metrics.map((metric) => (
        <div key={metric.label} className="border-l border-slate-200 pl-2">
          <div className="text-[10px] font-semibold uppercase text-slate-400">{metric.label}</div>
          <div className="text-sm font-bold text-slate-800">
            {formatMetricValue(metric.value)}
            {metric.unit && <span className="ml-1 text-[10px] font-semibold text-slate-400">{metric.unit}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

function CompactSources({ sources }: { sources: AssistantSource[] }) {
  const { t } = useLanguage();
  const visibleSources = sources.filter((source) => source.kind !== "dashboard" && source.kind !== "tool").slice(0, 4);
  if (!visibleSources.length) return null;

  return (
    <details className="mt-2 border-t border-slate-100 pt-2">
      <summary className="cursor-pointer text-[11px] font-bold uppercase text-slate-500">
        {t("\ucd9c\ucc98", "Sources")} ({visibleSources.length})
      </summary>
      <div className="mt-2 grid gap-1.5">
        {visibleSources.map((source, index) => (
          <div key={`${source.path ?? source.title}-${source.chunk_id ?? index}`} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5">
            <div className="truncate text-[11px] font-semibold text-slate-700">{source.citation_label || source.title}</div>
            <div className="mt-0.5 truncate text-[10px] text-slate-500">{source.page_range ?? source.kind}</div>
          </div>
        ))}
      </div>
    </details>
  );
}

// RUN-PROGRESS UI (2026-07-28): honest per-seed progress + ETA for an
// in-flight run_simulation dispatch, replacing the bare "Preparing
// answer..." spinner that used to sit here for the run's ENTIRE duration
// (10+ minutes for a multi-seed batch, with zero indication -- the
// repeated live-demo "is it still running?" question this milestone
// fixes). Polls GET /api/simulations/{jobId} (backend/routers/
// simulations.py's job-status endpoint, SimulationJob's seed-boundary
// fields in lib/api.ts) on a fixed interval, independently of the agent
// NDJSON stream -- the stream stays blocked on this same turn until the
// run reaches a terminal state, so polling (which survives a stream
// hiccup) is the only channel that can move on its own. Mirrors
// PromptSimulationStatus's identical polling shape (the legacy regex
// pipeline's own simulation-progress widget below) rather than inventing a
// new pattern, just against the richer M2.4 seed-boundary job fields.
function AgentRunProgressCard({
  jobId,
  onCancel,
  compact = false,
}: {
  jobId: string;
  onCancel?: () => void;
  compact?: boolean;
}) {
  const { t } = useLanguage();
  const [job, setJob] = useState<SimulationJob | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  // Purely cosmetic: keeps the elapsed-time readout ticking between polls.
  // Always derived from the server's own `started_at` below, never a
  // locally guessed duration -- this doesn't affect eta_s honesty at all.
  const [nowTick, setNowTick] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let active = true;
    let intervalId: number | undefined;

    async function refresh() {
      try {
        const nextJob = await fetchSimulationJob(jobId);
        if (!active) return;
        setJob(nextJob);
        setPollError(null);
        if ((nextJob.status === "done" || nextJob.status === "error") && intervalId !== undefined) {
          window.clearInterval(intervalId);
          intervalId = undefined;
        }
      } catch (caught) {
        if (active) setPollError(caught instanceof Error ? caught.message : String(caught));
      }
    }

    void refresh();
    intervalId = window.setInterval(() => void refresh(), 2500);
    return () => {
      active = false;
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
  }, [jobId]);

  const status = job?.status ?? "queued";
  const phase = job?.phase ?? "queued";
  const totalSeeds = job?.total_seeds ?? null;
  const completedSeeds = job?.completed_seeds ?? 0;
  const etaS = job?.eta_s ?? null;
  const startedAtMs = job?.started_at ? Date.parse(job.started_at) : null;
  const elapsedS = startedAtMs !== null && !Number.isNaN(startedAtMs) ? Math.max(0, Math.round((nowTick - startedAtMs) / 1000)) : null;

  const fractionFromSeeds = totalSeeds && totalSeeds > 0 ? completedSeeds / totalSeeds : null;
  const progressFraction = fractionFromSeeds ?? Math.max(0, Math.min(1, job?.progress ?? 0));
  const progressPercent = Math.round(progressFraction * 100);

  const phaseLabel = agentRunPhaseLabel(phase, t);
  const seedText =
    totalSeeds && totalSeeds > 1
      ? t(
          `시드 ${completedSeeds}/${totalSeeds} 완료`,
          `Seed ${completedSeeds}/${totalSeeds} complete`,
          `シード ${completedSeeds}/${totalSeeds} 完了`,
        )
      : null;
  // eta_s is null until the backend has at least one completed seed's real
  // duration to work from -- this project's "refused edit beats wrong
  // numbers" honesty doctrine, applied to progress UI. Never interpolate a
  // guess here; show the honest "still figuring it out" copy instead.
  const etaText =
    etaS === null
      ? t("예상 시간 계산 중", "Estimating time remaining", "所要時間を計算中")
      : t(
          `약 ${formatMinutesCeil(etaS)}분 남음`,
          `About ${formatMinutesCeil(etaS)} min remaining`,
          `残り約${formatMinutesCeil(etaS)}分`,
        );

  return (
    <div
      className={
        compact
          ? "mt-2 rounded-lg border border-slate-100 bg-slate-50/70 px-2.5 py-2"
          : "rounded-lg border border-slate-100 bg-slate-50/70 px-2.5 py-2"
      }
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-xs font-medium text-slate-600">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
              status === "error" ? "bg-rose-500" : status === "done" ? "bg-emerald-500" : "animate-pulse bg-blue-500"
            }`}
          />
          <span className="truncate font-semibold text-slate-700">{phaseLabel}</span>
          {seedText && <span className="shrink-0 text-slate-400">· {seedText}</span>}
        </div>
        {onCancel && status !== "done" && status !== "error" && (
          <button
            type="button"
            onClick={onCancel}
            className="shrink-0 rounded-md border border-slate-200 px-2 py-1 text-[10px] font-bold leading-none text-slate-500 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700"
          >
            {t("취소", "Cancel", "キャンセル")}
          </button>
        )}
      </div>

      <div className="mt-1.5 h-1.5 w-full rounded-full bg-slate-200">
        <div
          className={`h-1.5 rounded-full transition-all duration-500 ${status === "error" ? "bg-rose-400" : "bg-blue-500"}`}
          style={{ width: `${progressPercent}%` }}
        />
      </div>

      <div className="mt-1 flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-[10px] text-slate-400">
        <span>
          {elapsedS !== null
            ? t(`경과 ${formatElapsedClock(elapsedS)}`, `Elapsed ${formatElapsedClock(elapsedS)}`, `経過 ${formatElapsedClock(elapsedS)}`)
            : t("시작 중...", "Starting...", "開始中...")}
        </span>
        {status !== "error" && status !== "done" && <span>{etaText}</span>}
      </div>

      {status === "error" && (
        <p className="mt-1.5 text-[11px] leading-5 text-rose-600">
          {job?.error ?? pollError ?? t("시뮬레이션이 실패했습니다.", "The simulation failed.", "シミュレーションに失敗しました。")}
        </p>
      )}
      {!job && pollError && (
        <p className="mt-1.5 text-[11px] leading-5 text-slate-400">
          {t(
            "진행 상황을 불러오지 못했습니다. 계속 재시도합니다.",
            "Couldn't load progress yet — retrying.",
            "進行状況を取得できませんでした。再試行しています。",
          )}
        </p>
      )}
    </div>
  );
}

function agentRunPhaseLabel(phase: string, t: Translate): string {
  switch (phase) {
    case "arming":
      return t("시뮬레이션 준비 중", "Preparing simulation", "シミュレーション準備中");
    case "running":
      return t("시뮬레이션 실행 중", "Running simulation", "シミュレーション実行中");
    case "collecting":
      return t("결과 수집 중", "Collecting results", "結果を収集中");
    case "done":
      return t("시뮬레이션 완료", "Simulation complete", "シミュレーション完了");
    case "error":
      return t("시뮬레이션 오류", "Simulation error", "シミュレーションエラー");
    case "queued":
    default:
      return t("대기 중", "Queued", "待機中");
  }
}

function formatMinutesCeil(seconds: number): number {
  return Math.max(1, Math.ceil(seconds / 60));
}

function formatElapsedClock(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function AssistantInlineProgress({
  phase,
  onCancel,
  compact = false,
}: {
  phase: AssistantRequestPhase;
  onCancel?: () => void;
  compact?: boolean;
}) {
  const { t } = useLanguage();
  const label =
    phase === "context"
      ? t("\ud654\uba74 \ucee8\ud14d\uc2a4\ud2b8 \uc815\ub9ac \uc911...", "Preparing page context...")
      : phase === "streaming"
        ? t("\uc2e4\uc2dc\uac04 \uc218\uc2e0 \uc911...", "Receiving stream...")
        : phase === "cancelled"
          ? t("\uc694\uccad \ucde8\uc18c \uc911...", "Cancelling request...")
          : t("\ub2f5\ubcc0 \uc900\ube44 \uc911...", "Preparing answer...");

  return (
    <div className={compact ? "mt-2 flex items-center justify-between gap-2 border-t border-slate-100 pt-2" : "flex items-center justify-between gap-3"}>
      <div className="flex min-w-0 items-center gap-2 text-xs font-medium text-slate-500">
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-blue-500" />
        <span className="truncate">{label}</span>
      </div>
      {onCancel && (
        <button
          type="button"
          onClick={onCancel}
          className="shrink-0 rounded-md border border-slate-200 px-2 py-1 text-[10px] font-bold leading-none text-slate-500 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700"
        >
          {t("\ucde8\uc18c", "Cancel")}
        </button>
      )}
    </div>
  );
}

function AssistantRuntimeBadge({ response }: { response: AssistantQueryResponse }) {
  const { t } = useLanguage();
  const mode = response.runtime?.mode ?? "";
  const attempts = response.runtime?.attempts ?? 1;

  if (mode === "error-fallback") {
    return (
      <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] leading-5 text-amber-800">
        {t(
          "AI \uc11c\ubc84 \uc624\ub958\ub97c \ud3f4\ubc31 \ub2f5\ubcc0\uc73c\ub85c \ubcf5\uad6c\ud588\uc2b5\ub2c8\ub2e4.",
          "Recovered an AI server error with a fallback answer.",
        )}
        {response.runtime.error_id ? ` ID: ${response.runtime.error_id}` : ""}
      </p>
    );
  }

  if (mode === "fallback") {
    return (
      <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] leading-5 text-amber-800">
        {t(
          "\ubaa8\ub378 \uc0dd\uc131\uc774 \ubd88\uc548\uc815\ud574 \ub300\uc2dc\ubcf4\ub4dc \uae30\ubc18 \ud3f4\ubc31 \ub2f5\ubcc0\uc744 \uc0ac\uc6a9\ud588\uc2b5\ub2c8\ub2e4.",
          "Model generation was unstable, so this used a dashboard fallback answer.",
        )}
      </p>
    );
  }

  if (attempts > 1) {
    return (
      <p className="mt-2 text-[11px] leading-5 text-slate-400">
        {t("\uc790\ub3d9 \uc7ac\uc2dc\ub3c4 \ud6c4 \uc751\ub2f5\ud588\uc2b5\ub2c8\ub2e4.", "Answered after an automatic retry.")}
      </p>
    );
  }

  return null;
}

function AssistantRuntimeNote({
  status,
  statusError,
  checking,
}: {
  status: AssistantStatus | null;
  statusError: string | null;
  checking: boolean;
}) {
  const { t } = useLanguage();

  if (statusError) {
    return (
      <p className="mt-2 rounded-md border border-rose-200 bg-rose-50 px-2 py-1.5 text-[11px] leading-5 text-rose-700">
        {statusError}
      </p>
    );
  }

  if (!status) {
    if (!checking) return null;
    return <p className="mt-2 text-[11px] leading-5 text-slate-500">{t("AI \uc0c1\ud0dc \ud655\uc778 \uc911...", "Checking AI status...")}</p>;
  }

  if (status.ready && status.state !== "generating") return null;

  const detail =
    status.state === "loading"
      ? t("AI \ubaa8\ub378\uc744 \ub85c\ub529 \uc911\uc785\ub2c8\ub2e4. \uccab \uc694\uccad\uc740 \uc880 \ub354 \uac78\ub9b4 \uc218 \uc788\uc2b5\ub2c8\ub2e4.", "The AI model is loading. The first request can take longer.")
      : status.state === "generating"
        ? t("AI worker\uac00 \ud604\uc7ac \ub2f5\ubcc0\uc744 \uc0dd\uc131 \uc911\uc785\ub2c8\ub2e4.", "The AI worker is currently generating an answer.")
        : status.model_path_exists
          ? status.worker_available && !status.worker_running
            ? t("\ubaa8\ub378\uc740 \ucc3e\uc558\uace0 worker \uc900\ube44\ub97c \uae30\ub2e4\ub9ac\ub294 \uc911\uc785\ub2c8\ub2e4.", "The model exists, but the worker is not ready yet.")
            : t("AI worker \uc0c1\ud0dc\ub97c \ud655\uc778\ud574\uc57c \ud569\ub2c8\ub2e4.", "The AI worker status needs attention.")
          : t("Qwen \ubaa8\ub378 \uacbd\ub85c\ub97c \ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.", "The Qwen model path was not found.");
  const workerError = compactWorkerError(status.worker_error);

  return (
    <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] leading-5 text-amber-800">
      {workerError ? `${detail} ${workerError}` : detail}
    </p>
  );
}

function AssistantStatusPill({ status, checking }: { status: AssistantStatus | null; checking: boolean }) {
  const { t } = useLanguage();
  const ready = status?.ready;
  const found = status?.model_path_exists;
  const state = status?.state;
  const color = ready
    ? state === "generating"
      ? "bg-sky-500"
      : "bg-emerald-500"
    : checking
      ? "bg-sky-400"
      : state === "failed"
        ? "bg-rose-500"
        : state === "loading" || state === "standby" || found
        ? "bg-amber-400"
        : "bg-slate-400";
  const label = checking
    ? t("AI \uc0c1\ud0dc \ud655\uc778 \uc911", "Checking AI status")
    : state === "loading"
      ? t("AI \ub85c\ub529 \uc911", "AI loading")
      : state === "generating"
        ? t("AI \uc0dd\uc131 \uc911", "AI generating")
        : ready
          ? t("AI \uc5d4\uc9c4 \uc900\ube44\ub428", "AI engine ready")
          : state === "failed"
            ? t("AI \ud655\uc778 \ud544\uc694", "AI needs attention")
          : state === "standby" || found
            ? t("AI \uc5d4\uc9c4 \ub300\uae30", "AI engine standby")
            : t("AI \uc5d4\uc9c4 \uc624\ud504\ub77c\uc778", "AI engine offline");
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-surface px-2 py-1 text-[10px] font-semibold text-slate-600">
      <span className={`h-1.5 w-1.5 rounded-full ${color}`} />
      {label}
    </span>
  );
}

function compactWorkerError(value?: string | null): string {
  if (!value) return "";
  const errorIdMatch = value.match(/error_id=([a-z0-9]+)/i);
  if (errorIdMatch) return `ID: ${errorIdMatch[1]}`;
  const cleaned = value.replace(/\s+/g, " ").trim();
  return cleaned.length > 120 ? `${cleaned.slice(0, 117)}...` : cleaned;
}

function ComStatusPill({ status }: { status: VissimComToolStatus | null }) {
  const { t } = useLanguage();
  const connected = status?.connected;
  const available = status?.available;
  const label = connected ? t("COM \uc5f0\uacb0\ub428", "COM connected") : available ? t("COM \uc900\ube44\ub428", "COM ready") : t("COM \uc624\ud504\ub77c\uc778", "COM offline");
  const color = connected ? "bg-emerald-500" : available ? "bg-amber-400" : "bg-slate-400";

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-surface px-2 py-1 text-[10px] font-semibold text-slate-600"
      title={status?.reason ?? status?.error ?? undefined}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${color}`} />
      {label}
    </span>
  );
}

function buildRouteAssistantContext(pathname: string, t: Translate): RouteAssistantContext {
  if (pathname.startsWith("/dashboard")) {
    return {
      label: t("\ub300\uc2dc\ubcf4\ub4dc \ucee8\ud14d\uc2a4\ud2b8", "Dashboard context"),
      starters: [
        t("\ud604\uc7ac \ub300\uc2dc\ubcf4\ub4dc \uc2dc\ub098\ub9ac\uc624\uc640 \ucd5c\uadfc \uc2dc\ubbac\ub808\uc774\uc158 \uc624\ub958\ub97c \uc694\uc57d\ud574\uc918.", "Summarize the current dashboard scenarios and recent simulation errors."),
        t("\ucd5c\uadfc VISSIM \uc2e4\ud589\uc774 \uc65c \uc2e4\ud328\ud588\ub294\uc9c0, \uc5b4\ub5a4 \uac1d\uccb4 \ubc88\ud638\ub97c \uc368\uc57c \ud558\ub294\uc9c0 \uc54c\ub824\uc918.", "Why did the latest VISSIM run fail, and which object numbers should I use?"),
        // Replaces the old single-sentence edit+run combo tuned for the
        // legacy regex matcher (see DEFAULT_STARTERS' comment above) with a
        // clean run example that also ties into AgentRunDashboardHint below
        // -- a completed agent run lands right back in this page's scenario
        // list.
        t(
          "5\uac1c \uc2dc\ub4dc\ub85c VISSIM \uc2dc\ubbac\ub808\uc774\uc158\uc744 \uc2e4\ud589\ud574\uc918.",
          "Run a VISSIM simulation with 5 seeds.",
          "5\u3064\u306e\u30b7\u30fc\u30c9\u3067VISSIM\u30b7\u30df\u30e5\u30ec\u30fc\u30b7\u30e7\u30f3\u3092\u5b9f\u884c\u3057\u3066\u3002",
        ),
      ],
    };
  }

  if (pathname.startsWith("/base-model")) {
    return {
      label: t("\uae30\uc900 \ubaa8\ub378 \uac80\uc99d \ucee8\ud14d\uc2a4\ud2b8", "Base model validation context"),
      starters: [
        t("\uc5c5\ub85c\ub4dc\ub41c \ub370\uc774\ud130\uc14b\uacfc \uac80\uc99d \uc6cc\ud06c\ubd81 \uc0c1\ud0dc\ub97c \uc694\uc57d\ud574\uc918.", "Summarize the uploaded dataset and validation workbook status."),
        t("\uae30\uc900 \ubaa8\ub378\uc758 GEH\uc640 \ud1b5\ud589\uc2dc\uac04 \uac80\uc99d \ub9ac\uc2a4\ud06c\ub97c \ud655\uc778\ud574\uc918.", "Check GEH and travel-time validation risks for the base model."),
        t("\uc774 \ud398\uc774\uc9c0\uac00 \uacb0\uacfc\ub97c \uacc4\uc0b0\ud558\uae30 \uc804\uc5d0 \uc5b4\ub5a4 \ud30c\uc77c\uc744 \uc5c5\ub85c\ub4dc\ud574\uc57c \ud558\ub294\uc9c0 \uc54c\ub824\uc918.", "Tell me what file I need to upload before this page can calculate results."),
      ],
    };
  }

  if (pathname.startsWith("/simulation")) {
    return {
      label: t("\uc2dc\ubbac\ub808\uc774\uc158 \ucee8\ud14d\uc2a4\ud2b8", "Simulation context"),
      starters: [
        t("\uc774 \uae30\uae30\uc5d0\uc11c COM\uc73c\ub85c VISSIM\uc744 \uc2e4\ud589\ud560 \uc218 \uc788\ub294\uc9c0 \ud655\uc778\ud574\uc918.", "Check whether this machine can run VISSIM through COM."),
        t("seed 42\ub85c \uc9e7\uc740 \uc2dc\ubbac\ub808\uc774\uc158\uc744 \uc2e4\ud589\ud558\uace0 \uac01 \ud30c\ub77c\ubbf8\ud130\ub97c \uc124\uba85\ud574\uc918.", "Run a short simulation with seed 42 and explain each parameter."),
        t("\ucd5c\uadfc \uc2dc\ubbac\ub808\uc774\uc158 \uc2e4\ud328\ub97c \uc9c4\ub2e8\ud558\uace0 \uc720\ud6a8\ud55c vehicle input\uc744 \uc81c\uc548\ud574\uc918.", "Diagnose the latest simulation failure and suggest a valid vehicle input."),
      ],
    };
  }

  if (pathname.startsWith("/network")) {
    return {
      label: t("\ub124\ud2b8\uc6cc\ud06c \ud504\ub9ac\ubdf0 \ucee8\ud14d\uc2a4\ud2b8", "Network preview context"),
      starters: [
        t("\ud604\uc7ac \uc120\ud0dd\ub41c \uad50\ucc28\ub85c\uc640 \ub9c1\ud06c\uac00 \ubb34\uc5c7\uc778\uc9c0 \uc54c\ub824\uc918.", "Tell me which intersections and links are currently selected."),
        t("\uc774 \ub370\uc774\ud130\uc14b\uc758 \ub124\ud2b8\uc6cc\ud06c \uad6c\uc870\ub97c \uc694\uc57d\ud574\uc918.", "Summarize the network structure of this dataset."),
        t("\ub3c4\uc2dd\uc5d0\uc11c \uc5b4\ub5a4 \ub178\ub4dc\ub97c \ud074\ub9ad\ud574\uc57c \uad50\ucc28\ub85c \uc131\ub2a5\uc744 \ube44\uad50\ud560 \uc218 \uc788\ub294\uc9c0 \uc54c\ub824\uc918.", "Tell me which nodes I should click to compare intersection performance."),
      ],
    };
  }

  return {
    label: t("\ud398\uc774\uc9c0 \ucee8\ud14d\uc2a4\ud2b8", "Page context"),
    starters: DEFAULT_STARTERS.map((starter) => t(starter.ko, starter.en, starter.ja)),
  };
}

function requestPageContext(pathname: string) {
  window.dispatchEvent(
    new CustomEvent("vissim:assistant-context-request", {
      detail: { pathname },
    }),
  );
}

function buildContextChips(context: AssistantPageContext | null, t: Translate): string[] {
  if (!context) return [];
  const chips: string[] = [];
  if (context.dataset?.filename) {
    chips.push(context.dataset.source === "uploaded" ? context.dataset.filename : `${t("\ub370\uc774\ud130\uc14b", "dataset")}: ${context.dataset.source}`);
  }
  if (context.selectedScenarioIds?.length) {
    chips.push(t(`${context.selectedScenarioIds.length}\uac1c \uc2dc\ub098\ub9ac\uc624`, `${context.selectedScenarioIds.length} scenarios`, `${context.selectedScenarioIds.length} \u30b7\u30ca\u30ea\u30aa`));
  }
  if (context.selectedNodeIds?.length) {
    chips.push(t(`${context.selectedNodeIds.length}\uac1c \ub178\ub4dc`, `${context.selectedNodeIds.length} nodes`, `${context.selectedNodeIds.length} \u30ce\u30fc\u30c9`));
  }
  // P6: one chip per clicked diagram object, so the user can see exactly what
  // the next message will carry. Labels reuse the viewer's own type vocabulary
  // (lib/networkElements) — 연결로/링크 and the 11 element-class labels.
  for (const link of context.selectedLinks ?? []) {
    chips.push(`${linkKindLabel(link.isConnector, t)} ${link.name || link.id}`);
  }
  if (context.selectedElement) {
    const element = context.selectedElement;
    chips.push(`${elementTypeLabel(element.type, t)} ${element.name || element.id}`);
  }
  if (context.selectedScenarioNames?.length === 1) {
    chips.push(context.selectedScenarioNames[0]);
  }
  return chips;
}

function readStoredPanelSize(): AssistantPanelSize {
  if (typeof window === "undefined") return DEFAULT_PANEL_SIZE;

  try {
    const stored = window.localStorage.getItem(PANEL_SIZE_STORAGE_KEY);
    if (!stored) return clampPanelSize(DEFAULT_PANEL_SIZE);
    const parsed = JSON.parse(stored) as Partial<AssistantPanelSize>;
    return clampPanelSize({
      width: toPanelDimension(parsed.width, DEFAULT_PANEL_SIZE.width),
      height: toPanelDimension(parsed.height, DEFAULT_PANEL_SIZE.height),
    });
  } catch {
    return clampPanelSize(DEFAULT_PANEL_SIZE);
  }
}

function storePanelSize(size: AssistantPanelSize) {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(PANEL_SIZE_STORAGE_KEY, JSON.stringify(size));
  } catch {
    // Ignore storage failures so private browsing modes do not break resizing.
  }
}

function clampPanelSize(size: AssistantPanelSize): AssistantPanelSize {
  const maxWidth =
    typeof window === "undefined"
      ? PANEL_MAX_SIZE.width
      : Math.min(PANEL_MAX_SIZE.width, Math.max(PANEL_MIN_SIZE.width, window.innerWidth - PANEL_VIEWPORT_MARGIN.width));
  const maxHeight =
    typeof window === "undefined"
      ? PANEL_MAX_SIZE.height
      : Math.min(PANEL_MAX_SIZE.height, Math.max(PANEL_MIN_SIZE.height, window.innerHeight - PANEL_VIEWPORT_MARGIN.height));

  return {
    width: clampNumber(toPanelDimension(size.width, DEFAULT_PANEL_SIZE.width), PANEL_MIN_SIZE.width, maxWidth),
    height: clampNumber(toPanelDimension(size.height, DEFAULT_PANEL_SIZE.height), PANEL_MIN_SIZE.height, maxHeight),
  };
}

function toPanelDimension(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function parseAssistantAnswer(content: string, response?: AssistantQueryResponse): ParsedAnswerSection[] {
  const normalized = normalizeAssistantContent(content);
  const knownHeadings = new Set(
    [...(response?.answer_format?.map((item) => item.heading) ?? []), ...COMMON_ANSWER_HEADINGS]
      .map(normalizeHeadingKey)
      .filter(Boolean),
  );
  const lines = normalized.split(/\r?\n/);
  const sections: ParsedAnswerSection[] = [];
  let current: ParsedAnswerSection | null = null;

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const heading = matchSectionHeading(line, knownHeadings);
    if (heading) {
      if (current && current.lines.some((item) => item.trim())) {
        sections.push(current);
      }
      current = { heading: heading.heading, lines: [] };
      if (heading.rest) current.lines.push(heading.rest);
      continue;
    }

    if (!current) current = { heading: "답변", lines: [] };
    current.lines.push(line);
  }

  if (current && current.lines.some((item) => item.trim())) {
    sections.push(current);
  }

  return sections.length > 0 ? sections : [{ heading: "답변", lines: [normalized] }];
}

function matchSectionHeading(
  line: string,
  knownHeadings: Set<string>,
): { heading: string; rest: string } | null {
  const match = line.trim().match(/^([^:：]{1,28})[:：]\s*(.*)$/);
  if (!match) return null;
  const heading = match[1].trim();
  if (!knownHeadings.has(normalizeHeadingKey(heading))) return null;
  return { heading, rest: match[2].trim() };
}

function normalizeHeadingKey(value: string): string {
  return value.replace(/\s+/g, "").replace(/[.。]/g, "").toLowerCase();
}

function getPromptSimulationResult(toolResults?: Record<string, unknown>): PromptSimulationToolResult | null {
  const value = toolResults?.prompt_simulation;
  if (!value || typeof value !== "object") return null;
  const result = value as PromptSimulationToolResult;
  return result.recognized ? result : null;
}

function normalizeAssistantContent(content: string): string {
  return content
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/\*\*/g, "");
}

function formatMetricValue(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return Math.abs(value) >= 1000 ? value.toLocaleString(undefined, { maximumFractionDigits: 0 }) : value.toFixed(1);
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && String((error as { name?: unknown }).name) === "AbortError";
}

// Live-bug fix (docs/phase2/DECISIONS.md, 2026-07-24 heartbeat entry): the
// dedicated copy for a dropped agent stream (api.ts's
// AgentStreamDisconnectedError) -- distinct from formatAssistantError's
// generic fallback because it can make a stronger, true promise: every
// terminal agent-turn path persists state.json before returning
// (orchestrator.py's _finalize_turn), so telling the user their progress
// was saved is accurate, not just reassuring copy.
function agentStreamDisconnectedMessage(t: Translate): string {
  return t(
    "연결이 끊어졌습니다. 결과는 안전하게 저장되었으니 다시 질문해 주세요.",
    "The connection was lost. Your progress was saved safely — please ask again.",
    "接続が切断されました。結果は安全に保存されているので、もう一度お尋ねください。",
  );
}

function formatAssistantError(error: unknown, t: Translate): string {
  if (isAbortError(error)) {
    return t("\uc694\uccad\uc744 \ucde8\uc18c\ud588\uc2b5\ub2c8\ub2e4.", "Request cancelled.");
  }

  // Lead with a plain sentence the user can actually act on ("try again",
  // "wait", "shorten your question") instead of infrastructure names (port
  // numbers, proxy paths, "worker") a non-technical user cannot do anything
  // with. The raw backend message is no longer appended to the primary text.
  const raw = error instanceof Error ? error.message : String(error);
  const lowered = raw.toLowerCase();

  if (
    lowered.includes("failed to reach the api") ||
    lowered.includes("failed to fetch") ||
    lowered.includes("networkerror") ||
    lowered.includes("remote server")
  ) {
    return t(
      "AI \uc11c\ubc84\uc5d0 \uc5f0\uacb0\ud558\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4. \uc7a0\uc2dc \ud6c4 \ub2e4\uc2dc \uc2dc\ub3c4\ud574\uc8fc\uc138\uc694. \uacc4\uc18d\ub418\uba74 \uad00\ub9ac\uc790\uc5d0\uac8c \ubb38\uc758\ud558\uc138\uc694.",
      "Could not reach the AI server. Please try again in a moment. Contact an administrator if this continues.",
    );
  }

  if (lowered.includes("timed out") || lowered.includes("timeout")) {
    return t(
      "AI \uc751\ub2f5 \uc2dc\uac04\uc774 \ub108\ubb34 \uc624\ub798 \uac78\ub9ac\uace0 \uc788\uc2b5\ub2c8\ub2e4. \uc694\uccad\uc744 \ucde8\uc18c\ud558\uace0 \ub354 \uc9e7\uac8c \ub2e4\uc2dc \ubb3c\uc5b4\ubcf4\uc138\uc694.",
      "The AI is taking too long to respond. Cancel and try a shorter question.",
    );
  }

  if (lowered.includes("assistant query failed")) {
    return t(
      "\uc9c8\ubb38\uc744 \ucc98\ub9ac\ud558\ub294 \uc911 \uc624\ub958\uac00 \ubc1c\uc0dd\ud588\uc2b5\ub2c8\ub2e4. \ub2e4\uc2dc \uc2dc\ub3c4\ud574\uc8fc\uc138\uc694.",
      "Something went wrong while processing the question. Please try again.",
    );
  }

  if (lowered.includes("internal server error")) {
    return t(
      "\uc11c\ubc84 \uc624\ub958\uac00 \ubc1c\uc0dd\ud588\uc2b5\ub2c8\ub2e4. \uc7a0\uc2dc \ud6c4 \ub2e4\uc2dc \uc2dc\ub3c4\ud574\uc8fc\uc138\uc694.",
      "A server error occurred. Please try again in a moment.",
    );
  }

  return t("\ubb38\uc81c\uac00 \ubc1c\uc0dd\ud588\uc2b5\ub2c8\ub2e4. \ub2e4\uc2dc \uc2dc\ub3c4\ud574\uc8fc\uc138\uc694.", "Something went wrong. Please try again.");
}

function handleComposerKeyDown(
  event: KeyboardEvent<HTMLTextAreaElement>,
  submitPrompt: (nextPrompt?: string) => Promise<void>,
) {
  if (event.key !== "Enter" || event.shiftKey) return;
  event.preventDefault();
  void submitPrompt();
}
