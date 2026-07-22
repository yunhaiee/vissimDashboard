"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, FormEvent, KeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { usePathname } from "next/navigation";

import { useLanguage } from "@/components/LanguageProvider";
import {
  fetchAssistantStatus,
  fetchSimulationJob,
  fetchSimulationResults,
  streamAgentQuery,
  streamAssistant,
  type AgentStep,
  type AssistantQueryResponse,
  type AssistantSource,
  type AssistantStatus,
  type SimulationJob,
  type SimulationResultsResponse,
  type VissimComToolStatus,
} from "@/lib/api";

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
};

type AssistantPageContext = {
  route: string;
  label?: string;
  selectedScenarioIds?: string[];
  selectedScenarioNames?: string[];
  selectedNodeIds?: string[];
  selectedNodeNames?: string[];
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

const DEFAULT_STARTERS = [
  {
    ko: "\ud604\uc7ac VISSIM \uc2e4\ud589 \uc0c1\ud0dc\uc640 \ub370\uc774\ud130\uc14b \uc0c1\ud0dc\ub97c \uc694\uc57d\ud574\uc918.",
    en: "Summarize current VISSIM execution and dataset status.",
  },
  {
    ko: "\ucd5c\uadfc \uc2dc\ubbac\ub808\uc774\uc158 \uc624\ub958\ub97c \uc9c4\ub2e8\ud558\uace0 \ub2e4\uc74c \uc218\uc815 \ubc29\ud5a5\uc744 \uc54c\ub824\uc918.",
    en: "Diagnose the latest simulation error and tell me the next fix.",
  },
  {
    ko: "Vehicle input 107\uc744 6000\uc73c\ub85c \ubc14\uafb8\uace0 seed 42\ub85c VISSIM \uc2dc\ubbac\ub808\uc774\uc158\uc744 \uc2e4\ud589\ud574\uc918.",
    en: "Run a VISSIM simulation with vehicle input 107 set to 6000 and seed 42.",
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
  // M1.4: read-only agent mode is an explicit opt-in, default off — see
  // docs/phase2/DECISIONS.md. Off keeps every existing prompt (curation +
  // the regex simulation-command path) behaved exactly as before.
  const [agentMode, setAgentMode] = useState(false);
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
        { message: text, response_language: lang },
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
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantMessageId
            ? { ...message, content: response.answer, agentSteps: response.steps }
            : message,
        ),
      );
    } catch (caught) {
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
          aria-label={t("VISSIM \ub3c4\uc6b0\ubbf8 \ucc44\ud305", "VISSIM assistant chat")}
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
                  <h2 className="truncate text-sm font-bold text-slate-900">{t("VISSIM \ub3c4\uc6b0\ubbf8", "VISSIM Assistant")}</h2>
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
              </div>
              <label className="mt-2 flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
                <input
                  type="checkbox"
                  checked={agentMode}
                  disabled={loading}
                  onChange={(event) => setAgentMode(event.target.checked)}
                  className="h-3.5 w-3.5 rounded border-slate-300 text-accent focus:outline-none focus:ring-2 focus:ring-accent/35 disabled:cursor-not-allowed"
                />
                <span>{t("\uc5d0\uc774\uc804\ud2b8 \ubaa8\ub4dc (\ubca0\ud0c0)", "Agent mode (beta)", "\u30a8\u30fc\u30b8\u30a7\u30f3\u30c8\u30e2\u30fc\u30c9 (\u30d9\u30fc\u30bf)")}</span>
              </label>
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
}: {
  message: FloatingMessage;
  activePhase?: AssistantRequestPhase;
  onCancel?: () => void;
  declined: boolean;
  onConfirmExecute: () => void;
  onDeclineExecute: () => void;
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
          <AssistantInlineProgress phase={activePhase ?? "querying"} onCancel={onCancel} />
        ) : (
          <>
            {hasAgentSteps && <AgentStepNarration steps={message.agentSteps!} />}
            <AssistantAnswerContent content={message.content} response={message.response} />
            {isActiveAssistant && <AssistantInlineProgress phase={activePhase ?? "streaming"} onCancel={onCancel} compact />}
          </>
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
        </p>
      ))}
    </div>
  );
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

function AssistantAnswerContent({
  content,
  response,
}: {
  content: string;
  response?: AssistantQueryResponse;
}) {
  const { lang } = useLanguage();
  const sections = parseAssistantAnswer(content, response);

  if (sections.length === 1 && sections[0].heading === "답변") {
    return <AnswerLines lines={sections[0].lines} />;
  }

  return (
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
        t("Vehicle input 107\uc744 6000\uc73c\ub85c \ubc14\uafb8\uace0 \uc2dc\ubbac\ub808\uc774\uc158\ud55c \uacb0\uacfc\ub97c \ubcf4\uc5ec\uc918.", "Run a simulation by changing vehicle input 107 to 6000 and show the result."),
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

  return {
    label: t("\ud398\uc774\uc9c0 \ucee8\ud14d\uc2a4\ud2b8", "Page context"),
    starters: DEFAULT_STARTERS.map((starter) => t(starter.ko, starter.en)),
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
