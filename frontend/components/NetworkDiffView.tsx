"use client";

// Roadmap row 2.4 — the network diff view (두 버전 나란히, 변경 하이라이트).
// MRWA "Model Audit View" framing: a reviewer picks two snapshots of one agent
// session's model and reads what changed, structurally, with the method for
// each answer stated next to it.
//
// Three backend surfaces, each queried from the endpoint that OWNS its data
// rather than re-derived here:
//   * the session list  — GET /api/agent/downloads (unscoped): the sessions
//     that have actually saved a write, newest first;
//   * the version list  — GET /api/agent/downloads?session_id=…: that
//     session's full retained edit_NNN history (the existing enumeration —
//     lib/api.ts's fetchAgentDownloads);
//   * the diff itself   — GET /api/network/diff (backend/routers/
//     network_diff.py).
//
// Snapshot tokens are the backend's own space: "0" (the session's pristine
// source), a saved version number, "working", "active". Nothing here invents a
// token the server would not accept.
//
// ko primary; every t() call uses the three-argument form (explicit ja) so no
// string on this page depends on an entry landing in the generated
// lib/i18n-ja.ts dictionary. A contract pin enforces that.
//
// POSITION: rendered exactly as the payload states it — a count and a WARN
// band, never a distance and never a map overlay. That refusal is the
// product's own doctrine (services/genesis/diff.py), so it is presented here
// as METHOD, not as a missing feature.
//
// REHOMED (unit #27, backlog item 8, 2026-08-14): this used to be
// app/network-diff/page.tsx, its own sidebar entry. The mockup's own "+비교"
// annotation folded it into /network as a mode instead, so the body moved
// here VERBATIM — every fact above is still true of this file, just read
// "the diff view" wherever it said "this page". /network's 비교 mode is now
// this component's one consumer (app/network/page.tsx); /network-diff itself
// is a thin client redirect to /network?mode=diff, not a second consumer.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { useLanguage } from "@/components/LanguageProvider";
import {
  NetworkDiffError,
  fetchAgentDownloads,
  fetchNetworkDiff,
  type AgentDownloadEntry,
  type NetworkDiffFinding,
  type NetworkDiffResponse,
  type NetworkDiffRewire,
  type NetworkDiffToken,
} from "@/lib/api";
import { ok, resolve, type Loaded } from "@/lib/asyncResource";
import { loginHref } from "@/lib/authRedirect";

type Translate = (ko: string, en: string, ja: string) => string;

const TOKEN_PRISTINE = "0";
const TOKEN_WORKING = "working";
const TOKEN_ACTIVE = "active";

type TokenOption = { token: NetworkDiffToken; label: string; hint: string };

function formatTimestamp(iso: string, lang: "ko" | "en" | "ja"): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const locale = lang === "ko" ? "ko-KR" : lang === "ja" ? "ja-JP" : "en-US";
  return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

/** The snapshots a session can offer, in the order a reviewer reads them:
 * original → each saved edit → the live working copy → the project's current
 * dataset. Built from the download listing (the existing per-session version
 * enumeration), never guessed. */
function tokenOptions(versions: number[], hasWorking: boolean, t: Translate): TokenOption[] {
  const options: TokenOption[] = [
    {
      token: TOKEN_PRISTINE,
      label: t("원본 (v0)", "Original (v0)", "オリジナル (v0)"),
      hint: t(
        "이 세션이 복제한 원본 네트워크",
        "The network this session was cloned from",
        "このセッションが複製した元のネットワーク",
      ),
    },
  ];
  for (const version of versions) {
    options.push({
      token: String(version),
      label: t(`편집 ${version}`, `Edit ${version}`, `編集 ${version}`),
      hint: t(
        "쓰기 도구 실행 직후 저장된 스냅샷",
        "Snapshot saved right after a write tool ran",
        "書き込みツール実行直後に保存されたスナップショット",
      ),
    });
  }
  if (hasWorking) {
    options.push({
      token: TOKEN_WORKING,
      label: t("현재 작업본", "Working copy", "現在の作業版"),
      hint: t(
        "에이전트가 지금 편집 중인 사본",
        "The copy the agent is editing right now",
        "エージェントが現在編集中のコピー",
      ),
    });
  }
  options.push({
    token: TOKEN_ACTIVE,
    label: t("프로젝트 현재 데이터셋", "Project's active dataset", "プロジェクトの現在のデータセット"),
    hint: t(
      "지금 이 프로젝트에 올라와 있는 데이터셋 (세션 원본과 다를 수 있음)",
      "The dataset uploaded to this project now — may differ from the session's original",
      "現在このプロジェクトにアップロードされているデータセット（セッションの原本と異なる場合あり）",
    ),
  });
  return options;
}

/** ko/en/ja for the finding codes services/genesis/diff.py emits.
 *
 * diff.py leaves DiffFinding.messages EMPTY (measured; pinned by
 * backend/tests/test_network_diff_endpoint.py) and its `message` prose is
 * English-only, so the ko-primary layer supplies the headline here — the exact
 * arrangement diff.py's own docstring describes ("an i18n messages hook the
 * ko-primary UI layer fills in"). The payload's `message` is still shown
 * verbatim underneath as the evidence line: it carries the ids and counts, and
 * rewriting a backend finding client-side is how a UI starts lying. */
function findingHeadline(finding: NetworkDiffFinding, t: Translate, lang: string): string {
  const supplied = finding.messages[lang] || finding.messages.ko;
  if (supplied) return supplied;
  switch (finding.code) {
    case "topology_only_scope":
      return t(
        "구조만 비교했습니다 — 위치는 비교 대상이 아닙니다",
        "Structure only — position is deliberately not compared",
        "構造のみ比較しました — 位置は比較対象外です",
      );
    case "geometry_differs_not_measured":
      return t(
        "구조는 같지만 선형이 다른 링크가 있습니다 (개수만, 거리 아님)",
        "Some links are structurally identical but carry a different polyline (counted, not measured)",
        "構造は同じですが線形が異なるリンクがあります（件数のみ、距離ではありません）",
      );
    case "signalized_approach_lost":
      return t(
        "신호가 있던 접근로가 신호를 잃었습니다",
        "An approach that was signalized no longer is",
        "信号があった流入路が信号を失いました",
      );
    case "signal_heads_unresolved":
      return t(
        "일부 신호등을 링크/차로/위치로 해석하지 못해 커버리지가 과소 집계됩니다",
        "Some signal heads could not be resolved, so coverage understates the network",
        "一部の信号機を解決できず、カバレッジが過小集計されています",
      );
    case "connector_reference_dangling":
      return t(
        "존재하지 않는 링크를 가리키는 커넥터 끝점이 있습니다",
        "A connector endpoint references a link the network does not contain",
        "存在しないリンクを指すコネクタ端点があります",
      );
    case "duplicate_link_ids":
      return t(
        "같은 링크 번호가 두 번 이상 나타납니다 (마지막 것만 비교됨)",
        "A link id appears more than once; only the last occurrence was compared",
        "同じリンク番号が複数回現れます（最後のもののみ比較）",
      );
    case "network_empty":
      return t(
        "링크 컨테이너는 있으나 링크가 하나도 없습니다",
        "The links container is present but holds no links",
        "リンクコンテナはありますがリンクが一つもありません",
      );
    case "network_file_missing":
      return t("파일을 찾을 수 없습니다", "The file could not be found", "ファイルが見つかりません");
    case "network_unparseable":
      return t(
        "XML로 읽을 수 없어 네트워크를 해석하지 못했습니다",
        "Not well-formed XML, so no network could be read from it",
        "XMLとして読めず、ネットワークを解析できませんでした",
      );
    case "network_unreadable":
      return t("파일을 읽을 수 없습니다", "The file could not be read", "ファイルを読み込めません");
    case "network_not_recognised":
      return t(
        "VISSIM 네트워크가 아닙니다",
        "Parsed as XML but is not a Vissim network",
        "VISSIMネットワークではありません",
      );
    default:
      return finding.code;
  }
}

function severityClasses(severity: NetworkDiffFinding["severity"]): string {
  if (severity === "refuse") return "border-rose-300 bg-rose-50 text-rose-900";
  if (severity === "warn") return "border-amber-300 bg-amber-50 text-amber-900";
  return "border-slate-200 bg-slate-50 text-slate-700";
}

/** What one comparison came back as. A FAILURE here is not a raw message: the
 * screen says something different for 404, 401 and 422, so the status has to
 * travel with it -- and `Loaded.error` is a bare string (lib/asyncResource.ts).
 * So both outcomes are recorded as the resource's DATA, keyed by the
 * comparison that produced them, and the render below reads whichever one this
 * key actually answered with. */
type DiffOutcome =
  | { kind: "diff"; response: NetworkDiffResponse }
  | { kind: "error"; status: number | null; message: string };

/** The two facts the snapshot pickers need about a session, read from ONE
 * listing call and therefore held as one answer rather than two states that
 * could describe different sessions. */
type SessionVersions = { versions: number[]; hasWorking: boolean };

function rewireLabel(rewire: NetworkDiffRewire): string {
  const before = `${rewire.base_from_link ?? "?"}→${rewire.base_to_link ?? "?"}`;
  const after = `${rewire.target_from_link ?? "?"}→${rewire.target_to_link ?? "?"}`;
  return `${before}  ⇒  ${after}`;
}

// ---------------------------------------------------------------------------
// Small presentational pieces
// ---------------------------------------------------------------------------

function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="mb-5 rounded-xl border border-slate-200 bg-surface/90 px-5 py-4 shadow-[0_8px_18px_rgba(15,23,42,0.06)]">
      <h2 className="text-[15px] font-bold text-slate-900">{title}</h2>
      {subtitle && <p className="mt-0.5 text-[14px] text-slate-500">{subtitle}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** One side of a side-by-side change list. `tone` colours the rail so an
 * addition and a removal are distinguishable without reading the heading. */
function ChangeColumn({
  heading,
  tone,
  items,
  emptyLabel,
}: {
  heading: string;
  tone: "added" | "removed" | "changed";
  items: React.ReactNode[];
  emptyLabel: string;
}) {
  const rail =
    tone === "added"
      ? "border-l-emerald-400 bg-emerald-50/50"
      : tone === "removed"
        ? "border-l-rose-400 bg-rose-50/50"
        : "border-l-sky-400 bg-sky-50/50";
  return (
    <div className={`min-w-0 rounded-r-lg border-l-4 px-3.5 py-3 ${rail}`}>
      <div className="flex items-baseline gap-2">
        <h3 className="text-[14px] font-bold text-slate-800">{heading}</h3>
        <span className="text-[13px] font-semibold text-slate-500">{items.length}</span>
      </div>
      {items.length === 0 ? (
        <p className="mt-1.5 text-[13px] text-slate-400">{emptyLabel}</p>
      ) : (
        <ul className="mt-1.5 space-y-1">
          {items.map((item, index) => (
            <li key={index} className="font-mono text-[13px] leading-relaxed text-slate-700">
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function NetworkDiffView() {
  const { lang, t } = useLanguage();
  const pathname = usePathname();

  const [sessions, setSessions] = useState<AgentDownloadEntry[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string>("");

  // Both remote reads below are ONE keyed answer each (lib/asyncResource.ts):
  // the request key decides during render whether what we hold describes what
  // is selected now, so neither effect writes anything before its own call.
  // Neither takes `keepPrevious`: a different session, or a different pair of
  // snapshots, is a DIFFERENT question -- and holding the previous session's
  // edit numbers in the two snapshot dropdowns while the new session's listing
  // was still in flight is exactly the frame this shape removes.
  const [versionsLoaded, setVersionsLoaded] = useState<Loaded<SessionVersions> | null>(null);
  const { data: sessionVersions, loading: versionsLoading } = resolve(versionsLoaded, sessionId, {
    idle: !sessionId,
  });
  const versions = useMemo(() => sessionVersions?.versions ?? [], [sessionVersions]);
  const hasWorking = sessionVersions?.hasWorking ?? true;

  const [baseToken, setBaseToken] = useState<NetworkDiffToken>(TOKEN_PRISTINE);
  const [targetToken, setTargetToken] = useState<NetworkDiffToken>(TOKEN_WORKING);

  // One comparison is one question: which session, from which snapshot, to
  // which one.
  const diffKey = `${sessionId}|${baseToken}|${targetToken}`;
  const [diffLoaded, setDiffLoaded] = useState<Loaded<DiffOutcome> | null>(null);
  const { data: diffOutcome, loading: diffLoading } = resolve(diffLoaded, diffKey, { idle: !sessionId });
  const result = diffOutcome?.kind === "diff" ? diffOutcome.response : null;
  const diffError = diffOutcome?.kind === "error" ? diffOutcome : null;

  // Session list: the unscoped download listing already enumerates every
  // session that has saved a write, newest first, workspace-scoped.
  useEffect(() => {
    let cancelled = false;
    fetchAgentDownloads({ limit: 50 })
      .then((entries) => {
        if (cancelled) return;
        setSessions(entries);
        setSessionsError(null);
        if (entries.length > 0) setSessionId((current) => current || entries[0].session_id);
      })
      .catch((e) => {
        if (!cancelled) setSessionsError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Version list for the selected session: the same listing endpoint, scoped.
  // `version: null` is its "cloned but never saved a write" row, which means
  // there is a working copy and no numbered snapshot.
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    fetchAgentDownloads({ sessionId })
      .then((entries) => {
        if (cancelled) return;
        const numbered = entries
          .map((entry) => entry.version)
          .filter((version): version is number => typeof version === "number")
          .sort((a, b) => a - b);
        setVersionsLoaded(
          ok(sessionId, { versions: numbered, hasWorking: numbered.length === 0 ? entries.length > 0 : true }),
        );
      })
      .catch(() => {
        // Best-effort, exactly as before: a listing that failed still leaves
        // the three non-numbered snapshots (original / working copy / active
        // dataset) comparable, so the failure is recorded as an EMPTY version
        // list rather than left unanswered -- an unanswered key would read as
        // a dropdown loading forever.
        if (!cancelled) setVersionsLoaded(ok(sessionId, { versions: [], hasWorking: true }));
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    fetchNetworkDiff(sessionId, baseToken, targetToken)
      .then((payload) => {
        if (!cancelled) setDiffLoaded(ok(diffKey, { kind: "diff", response: payload }));
      })
      .catch((caught) => {
        if (!cancelled) {
          setDiffLoaded(
            ok(diffKey, {
              kind: "error",
              status: caught instanceof NetworkDiffError ? caught.status : null,
              message: caught instanceof Error ? caught.message : String(caught),
            }),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [diffKey, sessionId, baseToken, targetToken]);

  const options = useMemo(
    () => tokenOptions(versions, hasWorking, t),
    [versions, hasWorking, t],
  );

  const diff = result?.diff ?? null;

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <header className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-slate-900">
          {t("네트워크 변경 비교", "Network Diff", "ネットワーク差分比較")}
        </h1>
        <p className="mt-1 text-[15px] text-slate-600">
          {t(
            "한 세션의 두 시점을 나란히 놓고, 무엇이 바뀌었는지 구조로 확인합니다.",
            "Put two snapshots of one session side by side and read what changed, structurally.",
            "1つのセッションの2時点を並べ、何が変わったかを構造で確認します。",
          )}
        </p>
      </header>

      {/* ----------------------------------------------------------------- */}
      {/* Pickers                                                            */}
      {/* ----------------------------------------------------------------- */}
      <Panel
        title={t("비교 대상", "What to compare", "比較対象")}
        subtitle={t(
          "세션과 두 시점을 고르세요.",
          "Pick a session and two snapshots.",
          "セッションと2つの時点を選んでください。",
        )}
      >
        {sessionsError && (
          <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[14px] text-rose-700">
            {t("세션 목록을 불러오지 못했습니다", "Failed to load the session list", "セッション一覧を読み込めませんでした")}
            : {sessionsError}
          </div>
        )}

        {sessions !== null && sessions.length === 0 && !sessionsError && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center text-[14px] text-slate-500">
            {t(
              "편집된 세션이 아직 없습니다. AI 에이전트 채팅에서 변경을 실행하면 여기에 나타납니다.",
              "No edited sessions yet. Once the AI agent executes a change in chat, one will appear here.",
              "編集済みセッションはまだありません。AIエージェントのチャットで変更を実行するとここに表示されます。",
            )}
          </p>
        )}

        {sessions !== null && sessions.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block text-[14px] font-semibold text-slate-700">
              {t("세션", "Session", "セッション")}
              <select
                value={sessionId}
                onChange={(event) => {
                  setSessionId(event.target.value);
                  setBaseToken(TOKEN_PRISTINE);
                  setTargetToken(TOKEN_WORKING);
                }}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-surface px-2.5 py-2 text-[14px] font-normal text-slate-800"
              >
                {sessions.map((entry) => (
                  <option key={entry.session_id} value={entry.session_id}>
                    {entry.session_id_short} · {formatTimestamp(entry.modified_at, lang)}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-[14px] font-semibold text-slate-700">
              {t("기준 (이전)", "Base (before)", "基準（変更前）")}
              <select
                value={baseToken}
                onChange={(event) => setBaseToken(event.target.value)}
                disabled={versionsLoading}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-surface px-2.5 py-2 text-[14px] font-normal text-slate-800 disabled:opacity-60"
              >
                {options.map((option) => (
                  <option key={option.token} value={option.token}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-[14px] font-semibold text-slate-700">
              {t("대상 (이후)", "Target (after)", "対象（変更後）")}
              <select
                value={targetToken}
                onChange={(event) => setTargetToken(event.target.value)}
                disabled={versionsLoading}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-surface px-2.5 py-2 text-[14px] font-normal text-slate-800 disabled:opacity-60"
              >
                {options.map((option) => (
                  <option key={option.token} value={option.token}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}

        {sessions !== null && sessions.length > 0 && versions.length === 0 && !versionsLoading && (
          <p className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[13px] text-slate-600">
            {t(
              "이 세션에는 저장된 편집 버전이 없습니다. 원본·현재 작업본·프로젝트 데이터셋만 비교할 수 있습니다.",
              "This session has no saved edit versions. Only the original, the working copy and the project dataset can be compared.",
              "このセッションには保存された編集バージョンがありません。オリジナル・現在の作業版・プロジェクトデータセットのみ比較できます。",
            )}
          </p>
        )}
      </Panel>

      {/* ----------------------------------------------------------------- */}
      {/* Error states                                                       */}
      {/* ----------------------------------------------------------------- */}
      {diffError && (
        <div className="mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[15px] text-rose-800">
          {diffError.status === 404 && (
            <>
              <p className="font-semibold">
                {t("해당 세션을 찾을 수 없습니다", "No matching session", "該当セッションが見つかりません")}
              </p>
              <p className="mt-1 text-[14px]">
                {t(
                  "존재하지 않는 세션이거나 다른 프로젝트의 세션입니다. 서버는 이 둘을 구분해 알려주지 않습니다 — 한 프로젝트가 다른 프로젝트의 세션 존재 여부를 알아내지 못하게 하기 위한 설계입니다.",
                  "Either the session does not exist or it belongs to another project. The server deliberately does not distinguish the two, so one project cannot probe another's session ids.",
                  "存在しないセッションか、別プロジェクトのセッションです。サーバーは両者を区別しません — あるプロジェクトが他プロジェクトのセッションIDを探れないようにするための設計です。",
                )}
              </p>
            </>
          )}
          {diffError.status === 401 && (
            <>
              <p className="font-semibold">{t("로그인이 필요합니다", "Sign-in required", "ログインが必要です")}</p>
              <Link href={loginHref(pathname)} className="mt-1 inline-block text-[14px] font-semibold underline">
                {t("로그인", "Sign in", "ログイン")}
              </Link>
            </>
          )}
          {diffError.status === 422 && (
            <p className="font-semibold">
              {t("비교할 수 없는 선택입니다", "That selection cannot be compared", "比較できない選択です")}
            </p>
          )}
          {diffError.status !== 404 && (
            <p className="mt-1 font-mono text-[13px] opacity-80">{diffError.message}</p>
          )}
        </div>
      )}

      {/* A comparison in flight has no result to show. It used to keep the
          PREVIOUS pair's tables on screen while the new pair was being
          compared -- totals and change lists that belonged to two snapshots
          the pickers above no longer named. Keying the answer to the pair
          makes "not this pair's answer" and "no answer yet" the same state,
          which is what this line already said. */}
      {diffLoading && !result && (
        <p className="mb-5 text-[14px] text-slate-500">{t("비교하는 중…", "Comparing…", "比較中…")}</p>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* Result                                                             */}
      {/* ----------------------------------------------------------------- */}
      {result && diff && (
        <>
          {/* Side-by-side header: what each column IS. */}
          <div className="mb-5 grid gap-3 sm:grid-cols-2">
            {([
              ["base", result.base, t("기준 (이전)", "Base (before)", "基準（変更前）")],
              ["target", result.target, t("대상 (이후)", "Target (after)", "対象（変更後）")],
            ] as const).map(([key, side, caption]) => (
              <div key={key} className="rounded-xl border border-slate-200 bg-surface/90 px-4 py-3">
                <p className="text-[13px] font-semibold uppercase tracking-wide text-slate-500">{caption}</p>
                <p className="mt-0.5 text-[15px] font-bold text-slate-900">
                  {options.find((option) => option.token === side.token)?.label ?? side.token}
                </p>
                <p className="mt-0.5 font-mono text-[13px] text-slate-500">{side.filename}</p>
                <p className="mt-0.5 text-[13px] text-slate-500">{formatTimestamp(side.modified_at, lang)}</p>
                {side.resolved_from === "active_dataset_fallback" && (
                  <p className="mt-1 rounded bg-amber-50 px-2 py-1 text-[12px] text-amber-800">
                    {t(
                      "이 세션에는 원본 기록이 없어 현재 프로젝트 데이터셋으로 대체했습니다.",
                      "This session records no original, so the project's current dataset stood in for it.",
                      "このセッションには原本の記録がないため、現在のプロジェクトデータセットで代替しました。",
                    )}
                  </p>
                )}
              </div>
            ))}
          </div>

          {/* REFUSED — never an empty diff. */}
          {diff.refused && (
            <div className="mb-5 rounded-xl border border-rose-300 bg-rose-50 px-4 py-3 text-[15px] text-rose-900">
              <p className="font-semibold">
                {t("비교를 수행하지 못했습니다", "The comparison could not be performed", "比較を実行できませんでした")}
              </p>
              <p className="mt-1 text-[14px]">
                {t(
                  "변경 목록이 비어 있는 것이 아니라, 아예 비교되지 않았습니다. 아래 사유를 확인하세요.",
                  "This is NOT an empty diff — no comparison happened at all. See the reasons below.",
                  "変更リストが空なのではなく、比較自体が行われていません。以下の理由をご確認ください。",
                )}
              </p>
            </div>
          )}

          {/* NO CHANGES — an explicit statement, with the reason when there is one. */}
          {!diff.refused && !diff.has_changes && (
            <div className="mb-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-[15px] text-emerald-900">
              <p className="font-semibold">{t("차이 없음", "No differences", "差分なし")}</p>
              <p className="mt-1 text-[14px]">
                {result.identical_sources
                  ? t(
                      "두 선택이 같은 파일을 가리킵니다. 비교는 실제로 수행되었고 구조 변경이 0건입니다.",
                      "Both selections resolve to the same file. The comparison really ran, and it found 0 structural changes.",
                      "2つの選択が同じファイルを指しています。比較は実際に行われ、構造変更は0件です。",
                    )
                  : t(
                      "두 시점의 구조(링크·커넥터·이동류·신호 커버리지)가 동일합니다.",
                      "The two snapshots are structurally identical — links, connectors, movements and signal coverage all match.",
                      "2時点の構造（リンク・コネクタ・移動流・信号カバレッジ）が同一です。",
                    )}
              </p>
            </div>
          )}

          {!diff.refused && diff.links && diff.connectors && (
            <>
              {/* Totals, side by side. */}
              {diff.base && diff.target && (
                <Panel
                  title={t("총계", "Totals", "総計")}
                  subtitle={t(
                    `구조 변경 ${diff.change_count}건`,
                    `${diff.change_count} structural change(s)`,
                    `構造変更 ${diff.change_count} 件`,
                  )}
                >
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[420px] text-[14px]">
                      <thead>
                        <tr className="text-left text-slate-500">
                          <th className="py-1 font-semibold">{t("항목", "Item", "項目")}</th>
                          <th className="py-1 text-right font-semibold">{t("기준", "Base", "基準")}</th>
                          <th className="py-1 text-right font-semibold">{t("대상", "Target", "対象")}</th>
                          <th className="py-1 text-right font-semibold">{t("차이", "Delta", "差")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {([
                          ["plain_link_count", t("링크", "Links", "リンク")],
                          ["connector_count", t("커넥터", "Connectors", "コネクタ")],
                          ["node_count", t("노드", "Nodes", "ノード")],
                          ["movement_count", t("이동류", "Movements", "移動流")],
                          ["signal_head_count", t("신호등", "Signal heads", "信号機")],
                          ["signalized_link_count", t("신호 링크", "Signalized links", "信号リンク")],
                        ] as const).map(([field, caption]) => {
                          const before = diff.base![field];
                          const after = diff.target![field];
                          const delta = after - before;
                          return (
                            <tr key={field} className="border-t border-slate-100">
                              <td className="py-1 text-slate-700">{caption}</td>
                              <td className="py-1 text-right font-mono text-slate-600">{before}</td>
                              <td className="py-1 text-right font-mono text-slate-600">{after}</td>
                              <td
                                className={`py-1 text-right font-mono font-semibold ${
                                  delta > 0 ? "text-emerald-600" : delta < 0 ? "text-rose-600" : "text-slate-400"
                                }`}
                              >
                                {delta > 0 ? `+${delta}` : delta}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </Panel>
              )}

              {/* Links. */}
              <Panel
                title={t("링크", "Links", "リンク")}
                subtitle={t(
                  `변경 ${diff.links.change_count}건 · 변화 없음 ${diff.links.unchanged_count}건`,
                  `${diff.links.change_count} changed · ${diff.links.unchanged_count} unchanged`,
                  `変更 ${diff.links.change_count} 件 · 変化なし ${diff.links.unchanged_count} 件`,
                )}
              >
                <div className="grid gap-2 sm:grid-cols-3">
                  <ChangeColumn
                    heading={t("추가됨", "Added", "追加")}
                    tone="added"
                    items={diff.links.added.map((id) => `+ ${id}`)}
                    emptyLabel={t("없음", "None", "なし")}
                  />
                  <ChangeColumn
                    heading={t("삭제됨", "Removed", "削除")}
                    tone="removed"
                    items={diff.links.removed.map((id) => `− ${id}`)}
                    emptyLabel={t("없음", "None", "なし")}
                  />
                  <ChangeColumn
                    heading={t("차로 수 변경", "Lane count changed", "車線数変更")}
                    tone="changed"
                    items={diff.links.lane_count_changed.map(
                      (change) =>
                        `${change.link_id}: ${change.base_lanes} → ${change.target_lanes} (${
                          change.delta > 0 ? `+${change.delta}` : change.delta
                        })`,
                    )}
                    emptyLabel={t("없음", "None", "なし")}
                  />
                </div>
              </Panel>

              {/* Connectors. */}
              <Panel
                title={t("커넥터", "Connectors", "コネクタ")}
                subtitle={t(
                  `변경 ${diff.connectors.change_count}건 · 변화 없음 ${diff.connectors.unchanged_count}건`,
                  `${diff.connectors.change_count} changed · ${diff.connectors.unchanged_count} unchanged`,
                  `変更 ${diff.connectors.change_count} 件 · 変化なし ${diff.connectors.unchanged_count} 件`,
                )}
              >
                <div className="grid gap-2 sm:grid-cols-2">
                  <ChangeColumn
                    heading={t("추가됨", "Added", "追加")}
                    tone="added"
                    items={diff.connectors.added.map((id) => `+ ${id}`)}
                    emptyLabel={t("없음", "None", "なし")}
                  />
                  <ChangeColumn
                    heading={t("삭제됨", "Removed", "削除")}
                    tone="removed"
                    items={diff.connectors.removed.map((id) => `− ${id}`)}
                    emptyLabel={t("없음", "None", "なし")}
                  />
                  <ChangeColumn
                    heading={t("연결 변경", "Rewired", "接続変更")}
                    tone="changed"
                    items={diff.connectors.rewired.map(
                      (rewire) => `${rewire.connector_id}: ${rewireLabel(rewire)}`,
                    )}
                    emptyLabel={t("없음", "None", "なし")}
                  />
                  <ChangeColumn
                    heading={t("차로 수 변경", "Lane count changed", "車線数変更")}
                    tone="changed"
                    items={diff.connectors.lane_count_changed.map(
                      (change) => `${change.link_id}: ${change.base_lanes} → ${change.target_lanes}`,
                    )}
                    emptyLabel={t("없음", "None", "なし")}
                  />
                </div>
              </Panel>

              {/* Reclassified. */}
              {diff.reclassified && diff.reclassified.length > 0 && (
                <Panel
                  title={t("종류 변경", "Reclassified", "種別変更")}
                  subtitle={t(
                    "같은 번호가 링크↔커넥터로 바뀐 경우 — 추가+삭제 두 건이 아니라 한 건입니다.",
                    "An id that changed kind between plain link and connector — one change, not an add plus a remove.",
                    "同じ番号がリンク↔コネクタに変わった場合 — 追加+削除の2件ではなく1件です。",
                  )}
                >
                  <ul className="space-y-1">
                    {diff.reclassified.map((item) => (
                      <li key={item.link_id} className="font-mono text-[13px] text-slate-700">
                        {item.link_id}: {item.base_kind} → {item.target_kind}
                      </li>
                    ))}
                  </ul>
                </Panel>
              )}

              {/* Per-node movements. */}
              <Panel
                title={t("노드별 이동류 변경", "Movement changes per node", "ノード別 移動流の変更")}
                subtitle={t(
                  "이동류는 노드가 스스로 자기 구성원으로 선언한 커넥터입니다 — 근접 추정이 아닙니다.",
                  "A movement is a connector the node itself declares as a member — never inferred from proximity.",
                  "移動流はノード自身が構成員として宣言したコネクタです — 近接推定ではありません。",
                )}
              >
                {(diff.node_movements ?? []).filter((node) => node.changed).length === 0 ? (
                  <p className="text-[14px] text-slate-500">
                    {t(
                      "이동류가 바뀐 노드가 없습니다.",
                      "No node's movement set changed.",
                      "移動流が変わったノードはありません。",
                    )}
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {(diff.node_movements ?? [])
                      .filter((node) => node.changed)
                      .map((node) => (
                        <li key={node.node_id} className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
                          <div className="flex flex-wrap items-baseline gap-2">
                            <span className="text-[14px] font-bold text-slate-900">
                              {t(`노드 ${node.node_id}`, `Node ${node.node_id}`, `ノード ${node.node_id}`)}
                            </span>
                            <span className="font-mono text-[13px] text-slate-500">
                              {node.base_movement_count} → {node.target_movement_count}
                            </span>
                            {!node.present_in_base && (
                              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[12px] font-semibold text-emerald-700">
                                {t("기준에 없던 노드", "Absent from base", "基準に無いノード")}
                              </span>
                            )}
                            {!node.present_in_target && (
                              <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[12px] font-semibold text-rose-700">
                                {t("대상에서 사라진 노드", "Absent from target", "対象で消えたノード")}
                              </span>
                            )}
                          </div>
                          <div className="mt-1.5 grid gap-2 sm:grid-cols-3">
                            <ChangeColumn
                              heading={t("추가됨", "Added", "追加")}
                              tone="added"
                              items={node.added.map((ref) => `+ ${ref.label}`)}
                              emptyLabel={t("없음", "None", "なし")}
                            />
                            <ChangeColumn
                              heading={t("삭제됨", "Removed", "削除")}
                              tone="removed"
                              items={node.removed.map((ref) => `− ${ref.label}`)}
                              emptyLabel={t("없음", "None", "なし")}
                            />
                            <ChangeColumn
                              heading={t("연결 변경", "Rewired", "接続変更")}
                              tone="changed"
                              items={node.rewired.map((rewire) => `${rewire.connector_id}: ${rewireLabel(rewire)}`)}
                              emptyLabel={t("없음", "None", "なし")}
                            />
                          </div>
                        </li>
                      ))}
                  </ul>
                )}
              </Panel>

              {/* Signalized-approach coverage. */}
              <Panel
                title={t("신호 접근로 커버리지", "Signalized-approach coverage", "信号流入路カバレッジ")}
                subtitle={t(
                  "접근로는 각 이동류의 상류 링크이며, 신호등이 하나라도 있으면 신호 접근로입니다.",
                  "An approach is the upstream link of a movement; it is signalized when that link carries at least one signal head.",
                  "流入路は各移動流の上流リンクで、信号機が1つでもあれば信号流入路です。",
                )}
              >
                {(diff.signal_coverage ?? []).filter((row) => row.changed).length === 0 ? (
                  <p className="text-[14px] text-slate-500">
                    {t(
                      "신호 커버리지가 바뀐 노드가 없습니다.",
                      "No node's signal coverage changed.",
                      "信号カバレッジが変わったノードはありません。",
                    )}
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[460px] text-[14px]">
                      <thead>
                        <tr className="text-left text-slate-500">
                          <th className="py-1 font-semibold">{t("노드", "Node", "ノード")}</th>
                          <th className="py-1 font-semibold">{t("기준", "Base", "基準")}</th>
                          <th className="py-1 font-semibold">{t("대상", "Target", "対象")}</th>
                          <th className="py-1 font-semibold">{t("변경", "Change", "変更")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(diff.signal_coverage ?? [])
                          .filter((row) => row.changed)
                          .map((row) => (
                            <tr key={row.node_id} className="border-t border-slate-100 align-top">
                              <td className="py-1 font-mono text-slate-700">{row.node_id}</td>
                              <td className="py-1 font-mono text-slate-600">
                                {row.base_signalized.length}/{row.base_approaches.length}
                                {row.base_coverage !== null && ` (${Math.round(row.base_coverage * 100)}%)`}
                              </td>
                              <td className="py-1 font-mono text-slate-600">
                                {row.target_signalized.length}/{row.target_approaches.length}
                                {row.target_coverage !== null && ` (${Math.round(row.target_coverage * 100)}%)`}
                              </td>
                              <td className="py-1 font-mono text-[13px]">
                                {row.newly_signalized.length > 0 && (
                                  <span className="text-emerald-600">+{row.newly_signalized.join(", ")} </span>
                                )}
                                {row.no_longer_signalized.length > 0 && (
                                  <span className="text-rose-600">−{row.no_longer_signalized.join(", ")}</span>
                                )}
                                {row.newly_signalized.length === 0 && row.no_longer_signalized.length === 0 && (
                                  <span className="text-slate-400">
                                    {t("접근로 구성 변경", "approach set changed", "流入路構成の変更")}
                                  </span>
                                )}
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>
            </>
          )}

          {/* --------------------------------------------------------------- */}
          {/* POSITION — a count and a WARN band, exactly as the payload       */}
          {/* states it. No distance, no map overlay, no implied accuracy.     */}
          {/* --------------------------------------------------------------- */}
          <section className="mb-5 rounded-xl border border-amber-300 bg-amber-50/70 px-5 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-[15px] font-bold text-amber-900">{t("위치", "Position", "位置")}</h2>
              <span className="rounded-full bg-amber-200 px-2 py-0.5 text-[12px] font-bold uppercase tracking-wide text-amber-900">
                WARN
              </span>
              <span className="text-[14px] font-semibold text-amber-900">
                {t("비교하지 않음", "Not compared", "比較していません")}
              </span>
            </div>

            <p className="mt-2 text-[14px] leading-relaxed text-amber-900">
              {t(
                "이 프로젝트의 유일한 좌표 변환은 최대 잔차 약 14 m (차로폭 약 4개분)이고, 축척항 1.1782는 실측 축척이 아니라 Web-Mercator 위도 왜곡(1/cos 31.93°)입니다. 따라서 좌표·거리·방위 차이는 어떤 값이든 그 오차 바닥을 물려받습니다. 없는 정확도를 있는 것처럼 보여주느니 위치를 보고하지 않는 편이 낫다 — 이것이 이 비교의 방법론입니다.",
                "The only georeference this project has carries a max residual of about 14 m (~4 lane widths), and its scale term 1.1782 is Web-Mercator latitude distortion (1/cos 31.93°), not a surveyed scale. Any coordinate, distance or bearing delta would inherit that floor. Declining to report position beats implying an accuracy that does not exist — that is this comparison's method, not a gap in it.",
                "本プロジェクト唯一の座標変換は最大残差 約14 m（車線幅 約4本分）で、縮尺項 1.1782 は実測縮尺ではなく Web-Mercator の緯度歪み（1/cos 31.93°）です。したがって座標・距離・方位の差はすべてその誤差下限を引き継ぎます。存在しない精度を示すより位置を報告しない方が良い — これが本比較の方法論です。",
              )}
            </p>

            {/* The COUNT is a result, and a refusal has no results — showing
                "0 links differ" after a failed comparison would read as a
                finding. The band and the basis are method, so they stay. */}
            {diff.refused ? (
              <p className="mt-3 rounded-lg border border-amber-200 bg-surface/70 px-3 py-2 text-[14px] text-amber-900">
                {t(
                  "비교가 수행되지 않았으므로 선형 차이 개수도 집계되지 않았습니다.",
                  "No comparison was performed, so the polyline-difference count was not tallied either.",
                  "比較が行われていないため、線形差分の件数も集計されていません。",
                )}
              </p>
            ) : (
              <dl className="mt-3 grid gap-2 text-[14px] sm:grid-cols-2">
                <div className="rounded-lg border border-amber-200 bg-surface/70 px-3 py-2">
                  <dt className="text-[13px] font-semibold text-amber-900">
                    {t(
                      "구조는 같고 선형만 다른 링크",
                      "Links identical in topology, different in polyline",
                      "構造は同じで線形のみ異なるリンク",
                    )}
                  </dt>
                  <dd className="mt-0.5 font-mono text-base font-bold text-amber-900">
                    {result.diff.position.geometry_differs_count}
                    <span className="ml-1.5 font-sans text-[12px] font-semibold">
                      {t("개 (세었을 뿐, 재지 않음)", "counted, not measured", "件（数えただけ、測っていません）")}
                    </span>
                  </dd>
                </div>
                <div className="rounded-lg border border-amber-200 bg-surface/70 px-3 py-2">
                  <dt className="text-[13px] font-semibold text-amber-900">
                    {t("불확실성 구간", "Uncertainty band", "不確実性の幅")}
                  </dt>
                  <dd className="mt-0.5 font-mono text-[14px] text-amber-900">
                    ±{result.diff.position.max_residual_m} m ·{" "}
                    {t(
                      `차로폭 ${result.diff.position.residual_lane_widths}개분`,
                      `${result.diff.position.residual_lane_widths} lane widths`,
                      `車線幅 ${result.diff.position.residual_lane_widths} 本分`,
                    )}{" "}
                    · scale {result.diff.position.scale_term}
                  </dd>
                </div>
              </dl>
            )}

            <p className="mt-2 font-mono text-[12px] leading-relaxed text-amber-800/90">
              {result.diff.position.basis}
            </p>
          </section>

          {/* Findings. */}
          <Panel
            title={t("검토 메모", "Findings", "レビューメモ")}
            subtitle={t(
              "비교 자체에 대한 관찰입니다. 근거 문장은 서버가 만든 그대로 표시합니다.",
              "Observations about the comparison itself. The evidence line is shown exactly as the server wrote it.",
              "比較そのものに関する観察です。根拠の文はサーバーが作成したまま表示します。",
            )}
          >
            {diff.findings.length === 0 ? (
              <p className="text-[14px] text-slate-500">{t("없음", "None", "なし")}</p>
            ) : (
              <ul className="space-y-2">
                {diff.findings.map((finding, index) => (
                  <li
                    key={`${finding.code}-${index}`}
                    className={`rounded-lg border px-3 py-2 ${severityClasses(finding.severity)}`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-black/10 px-1.5 py-0.5 text-[12px] font-bold uppercase tracking-wide">
                        {finding.severity}
                      </span>
                      <span className="text-[14px] font-semibold">{findingHeadline(finding, t, lang)}</span>
                      {finding.subject && (
                        <span className="font-mono text-[12px] opacity-70">[{finding.subject}]</span>
                      )}
                    </div>
                    <p className="mt-1 font-mono text-[12px] leading-relaxed opacity-80">{finding.message}</p>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
