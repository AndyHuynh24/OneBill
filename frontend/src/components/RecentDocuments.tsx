import type { DocStatus, DocumentSummary } from "../api";
import { formatCents, formatDate } from "../format";

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
}

export function RecentDocuments({ documents, uploading, onOpen }: RecentDocumentsProps) {
  if (!documents.length && !uploading.length) {
    return <p className="muted">Nothing yet. Tap Scan to add your first receipt or bill.</p>;
  }
  return (
    <ul className="doc-list">
      {uploading.map((name, i) => (
        <li key={`uploading-${i}`} className="doc-card">
          <span className="doc-title">{name}</span>
          <span className="chip chip-busy">Uploading…</span>
        </li>
      ))}
      {documents.map((doc) => {
        const status = STATUS[doc.status] ?? STATUS.processing;
        const busy = status.tone === "busy";
        return (
          <li key={doc.docId}>
            <button type="button" className="doc-card" disabled={busy} onClick={() => onOpen(doc.docId)}>
              <span className="doc-title">{doc.vendor || doc.filename || "Document"}</span>
              <span className="doc-meta">
                {doc.documentDate ? formatDate(doc.documentDate) : ""}
                {doc.totalCents != null ? ` · ${formatCents(doc.totalCents, doc.currency ?? "USD")}` : ""}
              </span>
              <span className={`chip chip-${status.tone}`}>{status.label}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
