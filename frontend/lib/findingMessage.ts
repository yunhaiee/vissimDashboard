/**
 * Unit #41-b -- THE SENTENCE A ROW PRINTS BESIDE THE SUBJECT IT ALREADY PRINTED.
 *
 * `describeGenesisSubject`'s twin (lib/genesisArtifacts.ts): that one decides
 * what a subject SHOWS, this one decides what a message must DROP so the two do
 * not say it twice.
 *
 * WHY ITS OWN MODULE rather than living beside its twin. `genesisArtifacts.ts`
 * imports through the `@/` path alias, which the contract suite's runner
 * (`node --test --experimental-strip-types`) cannot resolve -- so a helper
 * defined there can only ever be regex-pinned, never RUN. The rule below is a
 * boundary rule about string prefixes, and a boundary rule that is never
 * executed against `링크 4` vs `링크 42` is not really pinned at all. Alias-free
 * and dependency-free, exactly like lib/roadWidth.ts and lib/region-prefill.ts,
 * so the suite can call it directly.
 *
 * WHAT IT IS FOR. gate.py words a number of findings as
 * `"<subject>: <what is wrong>"`. Every surface leads with `SubjectLabel` since
 * unit #41, so those rows printed the road twice running -- the user's own live
 * render, 2026-08-18:
 * `service[FWD] w1294917882s0service [FWD] w1294917882s0: 모델 경계가 아닌데…`.
 *
 * This trims the DUPLICATE and nothing else. It is a DISPLAY trim, never a
 * rewrite: the backend still owns every word, and a message that does not open
 * with its own subject comes back byte for byte. The prefix test is deliberately
 * conservative -- the subject must match from the very first character AND the
 * character after it must be a colon, whitespace, or the end of the string -- so
 * a subject that is merely the head of a longer token (`링크 4` against
 * `링크 42는 …`) can never lose its own digits, which is the one way a trim like
 * this turns into a lie about a different object. A message that is ONLY its
 * subject is left whole rather than blanked: an empty row says less than a
 * repeated one.
 */
export function messageWithoutSubject(subject: string, message: string): string {
  const lead = (subject ?? "").trim();
  const text = message ?? "";
  if (!lead || !text.startsWith(lead)) return text;
  const rest = text.slice(lead.length);
  if (rest && !/^[\s:]/.test(rest)) return text;
  const trimmed = rest.replace(/^\s*:?\s*/, "");
  return trimmed || text;
}
