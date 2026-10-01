import type { DocumentSummary } from "./api";

/** How many documents are still uploading or being read. */
export function processingCount(documents: DocumentSummary[] | undefined): number {
  return documents?.filter((d) => d.status === "pending_upload" || d.status === "processing").length ?? 0;
}

/** True while any document is still uploading or being read (the list keeps polling). */
export function hasProcessing(documents: DocumentSummary[] | undefined): boolean {
  return processingCount(documents) > 0;
}

/**
 * How often the list re-checks while a scan is being read. Measured server time is ~4–9 s, so a
 * 1.5 s check adds ~0.75 s on average (5 s used to add ~2.5 s). Light: one query of ≤ 50 summaries.
 */
export const POLL_MS = 1500;

export function pollInterval(documents: DocumentSummary[] | undefined): number | false {
  return hasProcessing(documents) ? POLL_MS : false;
}
