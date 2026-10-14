import type { DocStatus, DocumentSummary } from "../api";
import { docTitle, formatCents, formatDate } from "../format";

const STATUS: Record<DocStatus, { label: string; tone: string }> = {
  pending_upload: { label: "Uploading…", tone: "busy" },
  processing: { label: "Reading…", tone: "busy" },
  ready: { label: "Ready", tone: "ok" },
  needs_review: { label: "Check this", tone: "warn" },
  failed: { label: "Couldn't read", tone: "error" },
};

interface RecentDocumentsProps {
  documents: DocumentSummary[];
  uploading: string[];
  onOpen: (docId: string) => void;
  /** Home shows the newest few; the full page shows all. */
  limit?: number;
}

export function RecentDocuments({ documents, uploading, onOpen, limit }: RecentDocumentsProps) {
  if (!documents.length && !uploading.length) {
    return <p className="muted">Nothing yet. Tap Scan receipt to add your first one.</p>;
  }
  return (
    <ul className="doc-list">
      {uploading.map((_, i) => (
        <li key={`uploading-${i}`} className="doc-card">
          <span className="doc-title doc-title-reading">Reading…</span>
        </li>
      ))}
      {(limit ? documents.slice(0, limit) : documents).map((doc) => {
        const status = STATUS[doc.status] ?? STATUS.processing;
        const busy = status.tone === "busy";
        return (
          <li key={doc.docId}>
            <button type="button" className="doc-card" disabled={busy} onClick={() => onOpen(doc.docId)}>
              <span className={doc.vendor || !busy ? "doc-title" : "doc-title doc-title-reading"}>
                {docTitle(doc)}
                {doc.source === "email" && <span className="tag-email">Email</span>}
                {(doc.confidence?.level === "confident" || doc.confidence?.level === "confirmed") && (
                  <span className="tag-sure" title="Confident: the amounts were found and add up" aria-label="Confident">
                    ✓
                  </span>
                )}
                {doc.status === "ready" && !!doc.confidence?.checks.length && (
                  <span className="tag-check">Check</span>
                )}
              </span>
              <span className="doc-meta">
                {doc.documentDate ? formatDate(doc.documentDate) : ""}
                {doc.totalCents != null ? ` · ${formatCents(doc.totalCents, doc.currency ?? "USD")}` : ""}
                {/* On the meta line, not a chip: a "Check this" chip can share the row. */}
                {!!doc.owedCents && (
                  <>
                    {" · "}
                    <span className="owed">Owed {formatCents(doc.owedCents, doc.currency ?? "USD")}</span>
                  </>
                )}
              </span>
              {/* Ready is the normal state: only what needs attention shows (the title already
                  says "Reading…" while a scan is read). */}
              {status.tone !== "ok" && doc.status !== "processing" && <span className={`chip chip-${status.tone}`}>{status.label}</span>}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
