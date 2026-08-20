// ---------------------------------------------------------------------------
// Server-localized text, picked — never composed.
//
// Large parts of this product's prose are decided on the BACKEND and arrive as
// `{ko, en, ja}`: a precondition refusal and what would open it, a diagnosis
// rung's next action, a search job's headline. That is deliberate — those
// sentences are verdicts about a network, and a verdict a client can rephrase is
// a verdict two surfaces can disagree about.
//
// So the client's whole job is to PICK the reader's language out of an object
// the server built. This function is that job, and it lives here rather than
// inside one component because more than one component now renders server
// verdicts (components/calibration/FlowRail.tsx, which re-exports it for the
// callers that have always imported it from there, and
// components/calibration/ParameterSearchStart.tsx).
//
// `t(ko, en, ja)` is the OTHER half and is not interchangeable with this: `t` is
// for strings this client owns (a button label, a column heading). If a sentence
// states something about the user's model, it comes from the server and goes
// through `pick`.
// ---------------------------------------------------------------------------

/** A sentence the server wrote, in the three languages this product ships. */
export type ServerText = { ko: string; en: string; ja: string };

/** The server's own localized text, picked. Never composed here.
 *
 * An absent object is the empty string rather than a placeholder: "the server
 * said nothing" must render as nothing, not as a client-authored stand-in that
 * a reader would take for a verdict. */
export function pick(text: ServerText | undefined | null, lang: string): string {
  if (!text) return "";
  if (lang === "ko") return text.ko;
  if (lang === "ja") return text.ja;
  return text.en;
}
