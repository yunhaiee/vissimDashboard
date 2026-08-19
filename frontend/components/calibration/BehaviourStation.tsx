"use client";

// ---------------------------------------------------------------------------
// Steps ⑦ and ⑫: does each driving behaviour sit on a published MRWA Table 5-6
// row? (unit #50, rendering unit #44)
//
// `verify_applied_row` was written for exactly this question and, until this
// unit, nothing outside its own tests ever called it. Its verdict — including
// the `why` sentence that names a PARTIALLY APPLIED set — is printed verbatim
// here. This component counts nothing and decides nothing.
//
// THE TRAP THIS TABLE IS BUILT AROUND. Vissim's stock Wiedemann-74 behaviour
// carries bx_add=2.00, bx_mult=3.00, which IS Table 5-6's 2050 pcu row. So
// "matches a published row" is TRUE on a network nobody has calibrated, and a
// green tick there would be a fabricated claim. What separates the two is ax:
// §5.2.5.1 states 2.5 m and Vissim's default is 2.0 m. So the table shows the
// standstill column as its own verdict, and the summary says out loud when a
// row match is indistinguishable from the default.
// ---------------------------------------------------------------------------

import { useLanguage } from "@/components/LanguageProvider";
import type { CalibrationAppliedRows } from "@/lib/api";

export default function BehaviourStation({
  rows,
  loading,
  error,
}: {
  rows: CalibrationAppliedRows | null;
  loading: boolean;
  error: string | null;
}) {
  const { t } = useLanguage();

  if (error) {
    return (
      <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2.5 text-[14px] leading-6 text-rose-700">
        {t("주행행태를 불러오지 못했습니다:", "Driving behaviours could not be loaded:", "走行挙動を読み込めませんでした:")}{" "}
        {error}
      </div>
    );
  }
  if (!rows) {
    return (
      <p className="text-[14px] text-slate-500">
        {loading
          ? t("주행행태를 읽는 중...", "Reading driving behaviours...", "走行挙動を読み込み中...")
          : t("주행행태 정보가 없습니다.", "No driving-behaviour information.", "走行挙動情報がありません。")}
      </p>
    );
  }
  if (!rows.available) {
    return (
      <div className="rounded-lg border border-slate-300 bg-slate-50 px-3 py-2.5 text-[14px] leading-6 text-slate-700">
        {t(
          "네트워크를 읽지 못해 Table 5-6 적용 여부를 판단할 수 없습니다 — 지적이 아니라 판독 실패입니다.",
          "The network could not be read, so Table 5-6 application cannot be judged — a missing reading, not an accusation.",
          "ネットワークを読めず、Table 5-6の適用可否を判断できません — 指摘ではなく判読失敗です。",
        )}
      </div>
    );
  }

  const { summary } = rows;
  const defaultRow = summary.default_coincident_row_pcu;

  return (
    <div>
      <p className="font-mono text-[13px] text-slate-500">
        {t("읽음", "readable", "読み取り")} {summary.readable} ·{" "}
        {t("공표 행 위", "on a published row", "公表行上")} {summary.on_published_row} ·{" "}
        {t("공표 행 밖", "off any published row", "公表行外")} {summary.off_published_row} ·{" "}
        {t("지침 정지 시 거리", "guideline standstill", "指針の停止時距離")} {summary.guideline_standstill} ·{" "}
        {rows.citation}
      </p>

      {/* The honest sentence, shown whenever the only row matches are the ones
          Vissim ships with. Neutral ink + heavier hairline is this app's WARN
          treatment — never a fourth hue. */}
      {summary.default_coincident > 0 && summary.guideline_standstill === 0 && (
        <p className="mt-1.5 rounded-md border border-slate-400 bg-slate-100 px-2 py-1.5 text-[13px] leading-5 text-slate-800">
          {t(
            `주행행태 ${summary.default_coincident}개가 공표 행과 일치하지만 정지 시 거리가 지침값(2.5 m)이 아닙니다. Vissim 기본값이 마침 ${defaultRow} pcu 행과 같으므로, 이 일치는 측정이 제출됐다는 증거가 아닙니다.`,
            `${summary.default_coincident} behaviour(s) match a published row but do not carry the guideline standstill distance (2.5 m). Vissim's default happens to equal the ${defaultRow} pcu row, so this match is not evidence that a measurement was submitted.`,
            `走行挙動${summary.default_coincident}件が公表行と一致しますが、停止時距離が指針値(2.5 m)ではありません。Vissimの既定値がちょうど${defaultRow} pcu行と同じため、この一致は測定が提出された証拠にはなりません。`,
          )}
        </p>
      )}

      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[600px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-slate-200 text-left text-slate-500">
              <th className="px-2 py-1.5 font-semibold">{t("주행행태", "Behaviour", "走行挙動")}</th>
              <th className="px-2 py-1.5 text-right font-semibold">ax</th>
              <th className="px-2 py-1.5 text-right font-semibold">bx_add</th>
              <th className="px-2 py-1.5 text-right font-semibold">bx_mult</th>
              <th className="px-2 py-1.5 font-semibold">{t("표의 행", "Table row", "表の行")}</th>
              <th className="px-2 py-1.5 font-semibold">{t("정지 시 거리", "Standstill", "停止時距離")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.behaviors.map((entry) => {
              const verdict = entry.verdict;
              const matched = verdict.status === "MATCHES_PUBLISHED_ROW";
              const unreadable = verdict.status === "PARAMETERS_UNREADABLE";
              const guideline = verdict.standstill_matches_guideline === true;
              return (
                <tr key={entry.behavior_no} className="border-b border-slate-100 align-top">
                  <td className="px-2 py-1.5">
                    <span className="font-mono text-slate-700">{entry.behavior_no}</span>
                    {entry.behavior_name && <span className="ml-1.5 text-slate-600">{entry.behavior_name}</span>}
                    {/* Driving behaviours are SHARED objects — how many things
                        an edit here would move is part of reading the row. */}
                    {typeof entry.reference_count === "number" && entry.reference_count > 0 && (
                      <span className="ml-1.5 font-mono text-[13px] text-slate-400">
                        ×{entry.reference_count}
                      </span>
                    )}
                    <details className="mt-1 text-[13px] leading-5 text-slate-600">
                      <summary className="cursor-pointer list-none text-[13px] text-slate-500 underline decoration-dotted underline-offset-4">
                        {t("자세히 (판정 근거)", "More (the verdict)", "詳細（判定の根拠）")}
                      </summary>
                      <p className="mt-1 border-l border-slate-300 pl-3">{verdict.why}</p>
                    </details>
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-600">{verdict.applied.ax ?? "-"}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-600">{verdict.applied.bx_add ?? "-"}</td>
                  <td className="px-2 py-1.5 text-right font-mono text-slate-600">{verdict.applied.bx_mult ?? "-"}</td>
                  <td className="px-2 py-1.5">
                    <span
                      className={`rounded-full border px-1.5 py-0.5 font-mono font-semibold ${
                        unreadable
                          ? "border-slate-300 bg-surface text-slate-500"
                          : matched
                            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                            : "border-rose-200 bg-rose-50 text-rose-700"
                      }`}
                    >
                      {unreadable
                        ? t("판독 불가", "unreadable", "判読不可")
                        : matched
                          ? `${verdict.row?.saturation_flow_pcu ?? "-"} pcu`
                          : t("공표 행 없음", "no published row", "公表行なし")}
                    </span>
                  </td>
                  <td className="px-2 py-1.5">
                    <span
                      className={`rounded-full border px-1.5 py-0.5 font-semibold ${
                        verdict.standstill_matches_guideline == null
                          ? "border-slate-300 bg-surface text-slate-500"
                          : guideline
                            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                            : "border-slate-400 bg-slate-100 text-slate-800"
                      }`}
                    >
                      {verdict.standstill_matches_guideline == null
                        ? "-"
                        : guideline
                          ? t("지침 2.5 m", "guideline 2.5 m", "指針 2.5 m")
                          : t("지침값 아님", "not the guideline value", "指針値ではない")}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
