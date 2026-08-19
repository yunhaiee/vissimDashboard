// ---------------------------------------------------------------------------
// THE ONE CONVENTION for state that comes from outside React.
//
// `react-hooks/set-state-in-effect` (eslint-config-next 16) rejects a setState
// called synchronously in an effect BODY. Almost every screen in this app used
// to open a fetch effect with three of them:
//
//     useEffect(() => {
//       setData(null);        // reset, because the selection changed
//       setError(null);
//       setLoading(true);     // "a request is in flight"
//       fetchThing(id).then(setData).catch(setError).finally(...);
//     }, [id]);
//
// The codebase carried THREE answers to that: most files simply had the error;
// three files turned the rule off wholesale in eslint.config.mjs (inherited
// from the original 2026-07-21 source import); and some screens deferred the
// setState through `Promise.resolve().then(...)`, which slips past the rule
// without changing anything the rule was pointing at. This module replaces the
// first two outright.
//
// IT DOES NOT YET REPLACE THE THIRD (refuter, 2026-08-19). The microtask
// deferral is still live in four files -- app/dashboard/page.tsx,
// app/genesis/demand/page.tsx, app/genesis/phases/page.tsx and
// components/calibration/BaseModelStation.tsx -- and an earlier draft of this
// header claimed otherwise. It matters that the claim was wrong in a
// measurable way: the deferral does not FIRE the rule, so "eslint reports zero
// set-state-in-effect" is not evidence that this is the only convention. Those
// four files are pinned as a named inventory in tests/dashboard-contract.test.ts
// ("the microtask deferral is a NAMED remainder"), which stops a fifth file
// from joining them and fails the moment one is converted, so the list cannot
// quietly go stale in either direction.
//
// THE IDEA. None of those three values is really independent state. They are
// all answers to one question -- "is what I am holding the answer to the
// request I am currently making?" -- so they are DERIVED from one piece of
// state that carries the request's own key:
//
//     const [loaded, setLoaded] = useState<Loaded<Thing> | null>(null);
//     const { data, error, loading } = resolve(loaded, id);
//     useEffect(() => {
//       let cancelled = false;
//       fetchThing(id)
//         .then((thing) => { if (!cancelled) setLoaded(ok(id, thing)); })
//         .catch((caught) => { if (!cancelled) setLoaded(failed(id, caught)); });
//       return () => { cancelled = true; };
//     }, [id]);
//
// The reset is free (a stale key stops matching the moment the selection
// changes) and the effect sets state only inside the fetch's own callback.
//
// IT IS ALSO MORE CORRECT, not merely quieter. The old shape left one painted
// frame in which `id` had already changed but `loading` was still false and
// `data` still described the PREVIOUS selection -- a scoring table briefly
// showing the last run's numbers under the new run's name. Deriving from the
// key closes that window by construction: the frame that changes `id` is the
// frame that reports `loading`.
//
// WHERE AN EFFECT STILL LEGITIMATELY SETS STATE: a value that must be read
// AFTER hydration (localStorage, matchMedia, an imperative subscription's
// current value). Reading those during render would make the server's HTML and
// the client's first render disagree. Those keep their effect and carry a
// single-line eslint-disable naming hydration as the reason -- never a
// file-wide or rule-wide switch-off.
// ---------------------------------------------------------------------------

/** A resolved (or failed) response, tagged with the request that produced it. */
export type Loaded<T> = {
  /** The request this answer describes. Compared, never parsed. */
  key: string;
  data: T | null;
  error: string | null;
};

/** A successful answer for `key`.
 *
 * `data` may be `null`: "the lookup ran and there is no such thing" is a
 * successful answer, and it must not be confused with "no answer yet", which is
 * what a null `Loaded` means. */
export function ok<T>(key: string, data: T | null): Loaded<T> {
  return { key, data, error: null };
}

/** A failed answer for `key`. Accepts whatever `catch` handed over, so no
 * caller has to repeat the `instanceof Error` dance. */
export function failed<T>(key: string, caught: unknown): Loaded<T> {
  return {
    key,
    data: null,
    error: caught instanceof Error ? caught.message : String(caught),
  };
}

/** A failed answer whose sentence the CALLER wrote — for the screens that
 * translate their own failure text rather than surfacing a raw message. */
export function failedWith<T>(key: string, message: string): Loaded<T> {
  return { key, data: null, error: message };
}

export type Resolved<T> = {
  data: T | null;
  error: string | null;
  /** True while what we hold does not answer `key`. Always false when `idle`. */
  loading: boolean;
};

/**
 * What `loaded` says about `key`, computed during render.
 *
 * `idle` is for the "nothing is selected, so nothing is being loaded" state:
 * without it an empty selection would read as perpetually loading, which is a
 * spinner that never resolves. With it the screen can say "pick something"
 * instead, which is what every caller here actually wants to show.
 *
 * `keepPrevious` is for a REFRESH of the same question, as opposed to a new
 * one. A poll that re-reads a job list every few seconds must not blank the
 * list on each tick, so it keeps showing the last answer with `loading: true`
 * beside it. It is deliberately NOT the default: when the key changed because
 * the USER picked something else, showing the previous selection's data under
 * the new selection's heading is precisely the wrong answer -- the two cases
 * look identical to this function, so each caller has to say which it is.
 */
export function resolve<T>(
  loaded: Loaded<T> | null,
  key: string,
  options?: { idle?: boolean; keepPrevious?: boolean },
): Resolved<T> {
  if (options?.idle) return { data: null, error: null, loading: false };
  if (loaded?.key !== key) {
    if (options?.keepPrevious && loaded) {
      return { data: loaded.data, error: loaded.error, loading: true };
    }
    return { data: null, error: null, loading: true };
  }
  return { data: loaded.data, error: loaded.error, loading: false };
}

/**
 * Pick a still-valid item from a list, during render.
 *
 * The other half of the same problem: `useEffect(() => { if (!list.includes(
 * active)) setActive(list[0]); }, [list, active])` is a render the user can
 * see -- one frame pointing at an item that is gone. Storing the user's INTENT
 * and resolving it against the current list removes both the effect and the
 * frame.
 *
 * Returns `fallback` (default `null`) when the list is empty.
 */
export function resolveSelection<T, K>(
  intent: K | null,
  items: readonly T[],
  identify: (item: T) => K,
  fallback: K | null = null,
): K | null {
  if (items.length === 0) return fallback;
  if (intent !== null && items.some((item) => identify(item) === intent)) return intent;
  return identify(items[0]);
}
