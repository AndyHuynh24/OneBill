import type { DocumentSummary } from "./api";

/** True while any document is still uploading or being read (the list keeps polling). */
export function hasProcessing(documents: DocumentSummary[] | undefined): boolean {
  return !!documents?.some((d) => d.status === "pending_upload" || d.status === "processing");
}
