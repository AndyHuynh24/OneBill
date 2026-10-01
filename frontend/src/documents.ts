import type { DocumentSummary } from "./api";

/** How many documents are still uploading or being read. */
export function processingCount(documents: DocumentSummary[] | undefined): number {
  return documents?.filter((d) => d.status === "pending_upload" || d.status === "processing").length ?? 0;
}

/** True while any document is still uploading or being read (the list keeps polling). */
export function hasProcessing(documents: DocumentSummary[] | undefined): boolean {
  return processingCount(documents) > 0;
}
