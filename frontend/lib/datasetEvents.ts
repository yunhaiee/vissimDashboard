import type { ActiveDataset } from "@/lib/api";

/**
 * The one place the "the active dataset just changed" announcement is defined.
 *
 * Unit #18 moved the 프로젝트 picker out of VissimDatasetBanner and into the
 * sidebar, which split a publisher from its readers for the first time: before,
 * the component that switched projects was the same component that refetched
 * the dataset and called every page's `onDatasetChange`. The window event was
 * already being dispatched from TWO hand-written copies (the banner and
 * app/simulation/page.tsx) and listened to by nobody, so a third copy in the
 * sidebar would have been the third spelling of a contract no one could read.
 *
 * `publishDatasetChange` keeps both halves the copies had -- the localStorage
 * revision stamp and the CustomEvent -- and the event's `detail` is still the
 * dataset itself, so nothing that reads this event has to learn a new shape.
 */
export const DATASET_CHANGE_EVENT = "vissim-dataset-change";
export const DATASET_REVISION_STORAGE_KEY = "vissim-dataset-revision";

export function publishDatasetChange(dataset: ActiveDataset): void {
  window.localStorage.setItem(DATASET_REVISION_STORAGE_KEY, dataset.revision ?? String(Date.now()));
  window.dispatchEvent(new CustomEvent(DATASET_CHANGE_EVENT, { detail: dataset }));
}

/** Returns its own unsubscribe so a caller can `useEffect(() => subscribe(...))`
 * without writing the removeEventListener pair at every call site. */
export function subscribeToDatasetChange(handler: (dataset: ActiveDataset) => void): () => void {
  const listener = (event: Event) => {
    const dataset = (event as CustomEvent<ActiveDataset>).detail;
    if (dataset) handler(dataset);
  };
  window.addEventListener(DATASET_CHANGE_EVENT, listener);
  return () => window.removeEventListener(DATASET_CHANGE_EVENT, listener);
}

/**
 * Whether a dataset upload/reset is in flight anywhere in the app.
 *
 * This exists to preserve ONE property the picker had while it lived inside the
 * banner and would otherwise have been lost in the move: the 프로젝트 <select>
 * was `disabled={uploading || workspaceBusy}`, because the upload request has
 * already sent its `X-Workspace-Id` and switching projects underneath it leaves
 * the user watching a completed upload land in a project the screen is no
 * longer showing. The banner and the sidebar are now different components, so
 * that one boolean has to cross between them.
 *
 * A COUNT rather than a flag: two banners can be mounted at once (a page that
 * renders one while another route's is still unmounting), and the first one to
 * finish would otherwise clear the second one's guard.
 */
export const DATASET_BUSY_EVENT = "vissim-dataset-busy";

let busyCount = 0;

export function setDatasetBusy(busy: boolean): void {
  busyCount = Math.max(0, busyCount + (busy ? 1 : -1));
  window.dispatchEvent(new CustomEvent(DATASET_BUSY_EVENT, { detail: busyCount > 0 }));
}

export function isDatasetBusy(): boolean {
  return busyCount > 0;
}

export function subscribeToDatasetBusy(handler: (busy: boolean) => void): () => void {
  const listener = (event: Event) => handler(Boolean((event as CustomEvent<boolean>).detail));
  window.addEventListener(DATASET_BUSY_EVENT, listener);
  return () => window.removeEventListener(DATASET_BUSY_EVENT, listener);
}
