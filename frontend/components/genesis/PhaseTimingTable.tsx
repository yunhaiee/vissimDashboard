"use client";

// Unit #58 -- the 신호 시간 table, beside the Φ strip.
//
// THE USER'S DECISION (docs/phase2/DECISIONS.md, "2026-08-24 — 사용자 결정: 현시도
// 카드 단순화 — 도식+시간표, 짧은 확인 (#58)"): the Excel already carries the
// signal timing -- "이런것도 같이 표로 렌더해줘" -- and the numbers must be
// editable: "황색시간 및 시간 같은거가 수정가능하게".
//
// THE FOUR RULES THIS FILE EXISTS FOR:
//
//  1. **EVERY NUMBER ON SCREEN IS THE SERVER'S.** This component computes no
//     time. It does not sum greens into a cycle, it does not fill an empty cell
//     with a default, and it does not round. `timingText` is the ONLY thing
//     between a stored value and the screen, and its whole body is "null becomes
//     empty, everything else becomes its own string". While somebody is typing,
//     the input shows THEIR draft; the moment the edit lands the draft is
//     dropped and the server's answer takes the cell back.
//
//  2. **A COLUMN EXISTS ONLY WHERE THE DATA DOES.** 주기 / 옵셋 / 시간대 and the
//     min/max green columns render only for the plans that actually carry them.
//     An empty ceremony column invites somebody to fill it in, and a number
//     nobody measured is exactly what this product refuses to produce.
//
//  3. **AN EDIT IS DATA ENTRY, NEVER A CONFIRMATION.** There is no button in
//     this file and there is no 확인 wording in it. Editing a value is the
//     uploader amending their own statement (#58's doctrine reconciliation);
//     the single 확인 control on the screen belongs to the BINDING and lives in
//     the 네트워크 결박 section. The contract suite pins that separation from
//     both sides -- see "the reading column carries no confirmation affordance"
//     in tests/dashboard-contract.test.ts, and the threat-model boundary at
//     docs/phase2/DECISIONS.md, "2026-08-24 — 계약 핀의 위협 모델 경계 (#55b
//     3패스에서 명시)".
//
//  4. **A REVIEW WHOSE TIMING IS THE CLIENT'S DOCUMENT IS READ-ONLY, AND SAYS
//     SO.** A 대전 pdf review's numbers were read out of the client's own sheet;
//     the server refuses to rewrite them (`phase_diagram_timing_not_amendable`)
//     and this table renders them as text with the reason printed once. The
//     황색시간 is the exception on every medium, because no sheet of any kind
//     supplies it -- the person typing it always WAS the declaration.
//
// Chrome strings take the three-argument t() form, the convention the phases
// screen states in its own header comment.

import { useCallback, useState } from "react";

import type {
  PhaseDiagramAmendmentQuarantine,
  PhaseDiagramPlanTiming,
  PhaseDiagramTimingField,
} from "@/lib/api";

type Translate = (ko: string, en: string, ja: string) => string;

export type TimingAmendment = {
  field: PhaseDiagramTimingField;
  level_no?: number | null;
  plan_id?: number | null;
  phase_id?: string | null;
  value: number | string | null;
};

/** THE ONLY path from a stored value to a cell. No default, no fill, no
 * arithmetic -- an absent value is an EMPTY box, never a plausible number. */
function timingText(value: number | string | null | undefined): string {
  return value == null ? "" : String(value);
}

/** One cell's key in the draft map: which value, in which plan, in which 현시. */
function cellKey(field: PhaseDiagramTimingField, level: number | null, plan: number | null, phase: string | null): string {
  return `${field}|${level ?? ""}|${plan ?? ""}|${phase ?? ""}`;
}

function planLabel(plan: PhaseDiagramPlanTiming, t: Translate): string {
  return `${t("수준", "level", "水準")} ${plan.level_no} / ${t("계획", "plan", "計画")} ${plan.plan_id}`;
}

/** A value nobody recorded, printed as an absence rather than as a blank. */
const ABSENT = "—";

function TimingCell({
  id,
  value,
  drafts,
  onDraft,
  onCommit,
  editable,
  busy,
  width,
  label,
  numeric,
}: {
  id: string;
  value: number | string | null;
  drafts: Record<string, string>;
  onDraft: (key: string, next: string) => void;
  onCommit: (key: string) => void;
  editable: boolean;
  busy: boolean;
  width: string;
  label: string;
  numeric: boolean;
}) {
  if (!editable) {
    return <span className="text-slate-700">{timingText(value) || ABSENT}</span>;
  }
  return (
    <input
      value={drafts[id] ?? timingText(value)}
      onChange={(event) => onDraft(id, event.target.value)}
      onBlur={() => onCommit(id)}
      disabled={busy}
      inputMode={numeric ? "decimal" : "text"}
      aria-label={label}
      className={`${width} rounded border border-slate-300 px-1.5 py-1 text-[14px] disabled:opacity-40`}
    />
  );
}

export function PhaseTimingTable({
  timing,
  declaredAmberS,
  amberNote,
  amberDeclaredBy,
  amberDeclaredAt,
  quarantine,
  timingEditable,
  busy,
  onAmend,
  t,
}: {
  timing: readonly PhaseDiagramPlanTiming[];
  declaredAmberS: number | null;
  amberNote: string | null;
  amberDeclaredBy: string | null;
  amberDeclaredAt: string | null;
  /** A PREVIOUS amendment history that was lost, or null. Rendered, never
   * swallowed: a reviewer looking at a short clean history has no other way to
   * learn that an older one existed and did not parse. */
  quarantine: PhaseDiagramAmendmentQuarantine | null;
  /** False for a review whose timing IS the client's own document (rule 4). The
   * 황색시간 stays editable regardless -- it is a declaration, not a reading. */
  timingEditable: boolean;
  busy: boolean;
  onAmend: (amendment: TimingAmendment) => Promise<void>;
  t: Translate;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const onDraft = useCallback((key: string, next: string) => {
    setDrafts((current) => ({ ...current, [key]: next }));
  }, []);

  /** Commit one cell, then DROP the draft so the server's stored answer takes
   * the cell back -- on a refusal too, where the number snaps back and the
   * refusal is on screen above it. A draft that outlived its request would be
   * this screen showing a value nobody stored. */
  const commit = useCallback(
    async (key: string, amendment: TimingAmendment, stored: number | string | null) => {
      const typed = drafts[key];
      if (typed === undefined || typed === timingText(stored)) {
        setDrafts((current) => {
          const next = { ...current };
          delete next[key];
          return next;
        });
        return;
      }
      try {
        await onAmend({ ...amendment, value: typed.trim() === "" ? null : typed.trim() });
      } finally {
        setDrafts((current) => {
          const next = { ...current };
          delete next[key];
          return next;
        });
      }
    },
    [drafts, onAmend],
  );

  const amberKey = cellKey("declared_amber_s", null, null, null);

  return (
    <div className="mt-4 border-t border-slate-200 pt-3">
      <p className="text-[15px] font-semibold text-slate-900">
        {t("신호 시간", "Signal timing", "信号時間")}
      </p>

      {/* RULE 5 (round 3) -- A LOST HISTORY IS SAID OUT LOUD. Quarantining a
        * corrupt ledger stops a crash; saying nothing about it afterwards
        * replaces one silent failure with another, and the reviewer would read
        * a short clean history as "nobody ever amended this". MONO with the
        * heavy border, the same weight this screen gives 재확인 필요: it is
        * neither a pass nor a proven fault. The SENTENCE is the server's own
        * (it states what a machine did to a file); the heading is ours. */}
      {quarantine && (
        <div className="mt-2 rounded-md border border-slate-400 bg-slate-100 px-3 py-2 text-slate-800 ring-1 ring-slate-400">
          <p className="text-[15px] font-semibold">
            {t("수정 기록 일부 없음", "Part of the amendment history is missing", "修正記録の一部が欠落")}
          </p>
          <p className="mt-1 text-[14px] leading-relaxed">
            {t(quarantine.message.ko, quarantine.message.en, quarantine.message.ja)}
          </p>
        </div>
      )}

      {/* THE 황색시간, on every medium. No sheet of any kind prints the amber on
        * its own -- the 대전 sheets print 황색+전적색 as one number -- so the
        * person typing this box always WAS the declaration, and since #58 there
        * is no second 선언 field anywhere: what is typed here is exactly what
        * the binding's attestation records. */}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[15px] text-slate-700">
        <span className="font-semibold">{t("황색시간(초)", "amber (s)", "黄色時間(秒)")}</span>
        <TimingCell
          id={amberKey}
          value={declaredAmberS}
          drafts={drafts}
          onDraft={onDraft}
          onCommit={(key) =>
            void commit(key, { field: "declared_amber_s", value: null }, declaredAmberS)
          }
          editable
          busy={busy}
          width="w-24"
          numeric
          label={t("황색시간(초)", "amber (s)", "黄色時間(秒)")}
        />
        {amberDeclaredBy && amberDeclaredAt && (
          <span className="rounded border border-slate-300 bg-slate-50 px-2 py-0.5 text-[14px] text-slate-600">
            {t("선언", "declared", "申告")}: {amberDeclaredBy} · {amberDeclaredAt}
          </span>
        )}
      </div>
      <p className="mt-1 text-[14px] leading-relaxed text-slate-500">
        {t(
          "이 값이 아래 네트워크 결박에 기록되는 선언 황색시간입니다. 시트는 황색+전적색을 하나의 값으로만 인쇄하므로, 황색시간은 이 표에 적는 사람의 선언입니다.",
          "This is the declared amber the binding below records. The sheet prints 황색+전적색 as ONE number, so the amber is the declaration of whoever types it here.",
          "この値が下のネットワーク結縛に記録される申告黄色時間です。シートは黄色+全赤を一つの値でのみ印刷するため、黄色時間はここに記入する人の申告です。",
        )}
      </p>
      {amberNote && (
        <p className="mt-1 text-[14px] leading-relaxed text-slate-600">{amberNote}</p>
      )}

      {!timingEditable && (
        <p className="mt-2.5 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 text-[14px] leading-relaxed text-slate-600">
          {t(
            "아래 시간은 의뢰인 시트의 텍스트에서 읽은 값이라 화면에서 고칠 수 없습니다. 고치려면 엑셀로 내보내 수정한 뒤 다시 올리십시오.",
            "The times below were read out of the client's own sheet, so they cannot be edited here. Export to Excel, correct it there and upload it again.",
            "以下の時間は依頼元シートの本文から読み取った値のため、画面では修正できません。エクセルに書き出して修正のうえ再度アップロードしてください。",
          )}
        </p>
      )}

      {timing.length === 0 ? (
        <p className="mt-2 text-[15px] leading-relaxed text-slate-500">
          {t(
            "신호 시간이 기록되어 있지 않습니다 — 이 교차로로는 signals 입력을 만들 수 없습니다.",
            "No signal timing is recorded — no signals input can be built from this intersection.",
            "信号時間が記録されていません — この交差点からは signals 入力を作成できません。",
          )}
        </p>
      ) : (
        <ul className="mt-2 space-y-3">
          {timing.map((plan) => {
            const hasMinGreen = plan.phases.some((phase) => phase.min_green_s != null);
            const hasMaxGreen = plan.phases.some((phase) => phase.max_green_s != null);
            return (
              <li key={`${plan.level_no}:${plan.plan_id}`}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[14px] text-slate-700">
                  <span className="font-semibold text-slate-900">{planLabel(plan, t)}</span>
                  {plan.cycle_s != null && (
                    <span className="flex items-center gap-1">
                      {t("주기", "cycle", "周期")}
                      <TimingCell
                        id={cellKey("cycle_s", plan.level_no, plan.plan_id, null)}
                        value={plan.cycle_s}
                        drafts={drafts}
                        onDraft={onDraft}
                        onCommit={(key) =>
                          void commit(
                            key,
                            { field: "cycle_s", level_no: plan.level_no, plan_id: plan.plan_id, value: null },
                            plan.cycle_s,
                          )
                        }
                        editable={timingEditable}
                        busy={busy}
                        width="w-20"
                        numeric
                        label={`${planLabel(plan, t)} ${t("주기", "cycle", "周期")}`}
                      />
                    </span>
                  )}
                  {plan.offset_s != null && (
                    <span className="flex items-center gap-1">
                      {t("옵셋", "offset", "オフセット")}
                      <TimingCell
                        id={cellKey("offset_s", plan.level_no, plan.plan_id, null)}
                        value={plan.offset_s}
                        drafts={drafts}
                        onDraft={onDraft}
                        onCommit={(key) =>
                          void commit(
                            key,
                            { field: "offset_s", level_no: plan.level_no, plan_id: plan.plan_id, value: null },
                            plan.offset_s,
                          )
                        }
                        editable={timingEditable}
                        busy={busy}
                        width="w-20"
                        numeric
                        label={`${planLabel(plan, t)} ${t("옵셋", "offset", "オフセット")}`}
                      />
                    </span>
                  )}
                  {plan.time_of_day != null && (
                    <span className="flex items-center gap-1">
                      {t("시간대", "time of day", "時間帯")}
                      <TimingCell
                        id={cellKey("time_of_day", plan.level_no, plan.plan_id, null)}
                        value={plan.time_of_day}
                        drafts={drafts}
                        onDraft={onDraft}
                        onCommit={(key) =>
                          void commit(
                            key,
                            { field: "time_of_day", level_no: plan.level_no, plan_id: plan.plan_id, value: null },
                            plan.time_of_day,
                          )
                        }
                        editable={timingEditable}
                        busy={busy}
                        width="w-32"
                        numeric={false}
                        label={`${planLabel(plan, t)} ${t("시간대", "time of day", "時間帯")}`}
                      />
                    </span>
                  )}
                </div>
                <table className="mt-1.5 w-full text-[14px]">
                  <thead>
                    <tr className="text-left text-slate-500">
                      <th className="py-1 font-semibold">{t("현시", "phase", "現示")}</th>
                      <th className="py-1 font-semibold">{t("녹색(초)", "green (s)", "青(秒)")}</th>
                      <th className="py-1 font-semibold">{t("소거(초)", "clearance (s)", "クリアランス(秒)")}</th>
                      {hasMinGreen && (
                        <th className="py-1 font-semibold">{t("최소녹색", "min green", "最小青")}</th>
                      )}
                      {hasMaxGreen && (
                        <th className="py-1 font-semibold">{t("최대녹색", "max green", "最大青")}</th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {plan.phases.map((phase) => (
                      <tr key={phase.phase_id} className="border-t border-slate-100">
                        <td className="py-1 font-semibold text-slate-900">{phase.phase_id}</td>
                        <td className="py-1">
                          <TimingCell
                            id={cellKey("green_s", plan.level_no, plan.plan_id, phase.phase_id)}
                            value={phase.green_s}
                            drafts={drafts}
                            onDraft={onDraft}
                            onCommit={(key) =>
                              void commit(
                                key,
                                {
                                  field: "green_s",
                                  level_no: plan.level_no,
                                  plan_id: plan.plan_id,
                                  phase_id: phase.phase_id,
                                  value: null,
                                },
                                phase.green_s,
                              )
                            }
                            editable={timingEditable}
                            busy={busy}
                            width="w-20"
                            numeric
                            label={`${phase.phase_id} ${t("녹색(초)", "green (s)", "青(秒)")}`}
                          />
                        </td>
                        <td className="py-1">
                          <TimingCell
                            id={cellKey("clearance_s", plan.level_no, plan.plan_id, phase.phase_id)}
                            value={phase.clearance_s}
                            drafts={drafts}
                            onDraft={onDraft}
                            onCommit={(key) =>
                              void commit(
                                key,
                                {
                                  field: "clearance_s",
                                  level_no: plan.level_no,
                                  plan_id: plan.plan_id,
                                  phase_id: phase.phase_id,
                                  value: null,
                                },
                                phase.clearance_s,
                              )
                            }
                            editable={timingEditable}
                            busy={busy}
                            width="w-20"
                            numeric
                            label={`${phase.phase_id} ${t("소거(초)", "clearance (s)", "クリアランス(秒)")}`}
                          />
                        </td>
                        {hasMinGreen && (
                          <td className="py-1">
                            <TimingCell
                              id={cellKey("min_green_s", plan.level_no, plan.plan_id, phase.phase_id)}
                              value={phase.min_green_s}
                              drafts={drafts}
                              onDraft={onDraft}
                              onCommit={(key) =>
                                void commit(
                                  key,
                                  {
                                    field: "min_green_s",
                                    level_no: plan.level_no,
                                    plan_id: plan.plan_id,
                                    phase_id: phase.phase_id,
                                    value: null,
                                  },
                                  phase.min_green_s,
                                )
                              }
                              editable={timingEditable}
                              busy={busy}
                              width="w-20"
                              numeric
                              label={`${phase.phase_id} ${t("최소녹색", "min green", "最小青")}`}
                            />
                          </td>
                        )}
                        {hasMaxGreen && (
                          <td className="py-1">
                            <TimingCell
                              id={cellKey("max_green_s", plan.level_no, plan.plan_id, phase.phase_id)}
                              value={phase.max_green_s}
                              drafts={drafts}
                              onDraft={onDraft}
                              onCommit={(key) =>
                                void commit(
                                  key,
                                  {
                                    field: "max_green_s",
                                    level_no: plan.level_no,
                                    plan_id: plan.plan_id,
                                    phase_id: phase.phase_id,
                                    value: null,
                                  },
                                  phase.max_green_s,
                                )
                              }
                              editable={timingEditable}
                              busy={busy}
                              width="w-20"
                              numeric
                              label={`${phase.phase_id} ${t("최대녹색", "max green", "最大青")}`}
                            />
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
