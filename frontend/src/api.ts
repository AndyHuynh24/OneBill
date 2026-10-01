/** Typed client for the OneTally HTTP API. Every call sends the Cognito access token. */

export interface Profile {
  userId: string;
  baseCurrency: string;
  createdAt: string;
}

export type DocStatus = "pending_upload" | "processing" | "ready" | "needs_review" | "failed";

export type LineKind =
  | "item"
  | "discount"
  | "fee"
  | "tax"
  | "tip"
  | "subtotal"
  | "total"
  | "payment"
  | "change"
  | "balance_due"
  | "info";

export interface DocumentSummary {
  docId: string;
  status: DocStatus;
  filename?: string;
  contentType?: string;
  vendor?: string | null;
  documentDate?: string | null;
  totalCents?: number | null;
  currency?: string | null;
  createdAt: string;
  updatedAt?: string;
  reviewOutcome?: "accepted" | "corrected";
}

export interface DocumentLine {
  row: number;
  description: string;
  kind: LineKind;
  amountCents: number;
}

export interface DocumentDeadline {
  kind: string;
  dueDate: string;
  evidence: string;
}

export interface DocumentDetail extends DocumentSummary {
  docType?: string;
  issues?: string[];
  failureReason?: string;
  lines?: DocumentLine[];
  subtotalCents?: number | null;
  taxCents?: number;
  tipCents?: number;
  feesCents?: number;
  discountsCents?: number;
  obligations?: DocumentDeadline[];
  split?: Split;
}

export interface Friend {
  friendId: string;
  name: string;
  color: number;
  timesUsed: number;
  lastUsedAt: string;
}

export type ChargeChoice =
  | { mode: "proportional" }
  | { mode: "even" }
  | { mode: "person"; member: number };

/** People go by seat (0 = first) and lines by row number; amounts always come from the server. */
export interface SplitRequest {
  members: { name: string; self?: boolean }[];
  assignments: Record<string, number[]>;
  charges: Record<string, ChargeChoice>;
  expectedVersion: number;
}

export interface SplitMember {
  id: string;
  name: string;
  self: boolean;
  color: number;
  friendId?: string;
}

export interface SplitShare {
  member: string;
  totalCents: number;
  itemsCents: number;
  extrasCents: number;
  rows: number[];
}

export interface Split {
  version: number;
  members: SplitMember[];
  assignments: Record<string, string[]>;
  charges: Record<string, { mode: string; member?: string }>;
  shares: SplitShare[];
  totalCents: number;
  updatedAt?: string;
}

export interface Obligation {
  oblId: string;
  docId: string;
  kind: string;
  dueDate: string;
  vendor?: string | null;
  amountCents?: number | null;
}

export interface ReviewStats {
  reviewed: number;
  acceptedUnchanged: number;
  acceptanceRate: number | null;
}

export interface CorrectionFields {
  vendor?: string | null;
  documentDate?: string | null;
  totalCents?: number;
  currency?: string;
  deadlines?: { kind: string; dueDate: string }[];
  /** Corrected lines, in order; the server numbers them and re-checks the bill. */
  lines?: { description: string; kind: LineKind; amountCents: number }[];
}

export type ReviewRequest = { action: "accept" } | { action: "correct"; fields: CorrectionFields };

export interface UploadTicket {
  docId: string;
  upload: { url: string; fields: Record<string, string> };
}

export class ApiError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Returns the current Cognito access token, or undefined when signed out. */
export type TokenGetter = () => Promise<string | undefined>;

export interface OneTallyApi {
  getProfile(): Promise<Profile>;
  listDocuments(): Promise<{ documents: DocumentSummary[]; review: ReviewStats }>;
  getDocument(docId: string): Promise<DocumentDetail>;
  reviewDocument(docId: string, request: ReviewRequest): Promise<DocumentDetail>;
  listObligations(fromDate: string, withinDays?: number): Promise<{ obligations: Obligation[] }>;
  /** Ask the API for an upload ticket, then send the file straight to S3. */
  uploadDocument(file: File): Promise<string>;
  listFriends(): Promise<{ friends: Friend[] }>;
  deleteFriend(friendId: string): Promise<void>;
  saveSplit(docId: string, request: SplitRequest): Promise<Split>;
}

export function createApi(apiUrl: string, getToken: TokenGetter, fetchImpl: typeof fetch = fetch): OneTallyApi {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const token = await getToken();
    if (!token) throw new ApiError("Not signed in", 401);
    const response = await fetchImpl(new URL(path, apiUrl).toString(), {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new ApiError(typeof data?.message === "string" ? data.message : "Request failed", response.status);
    }
    return data as T;
  }

  return {
    getProfile: () => call<Profile>("GET", "me"),
    listDocuments: () => call("GET", "documents"),
    getDocument: (docId) => call<DocumentDetail>("GET", `documents/${encodeURIComponent(docId)}`),
    reviewDocument: (docId, request) =>
      call<DocumentDetail>("PATCH", `documents/${encodeURIComponent(docId)}`, request),
    listObligations: (fromDate, withinDays = 30) =>
      call("GET", `obligations?from=${encodeURIComponent(fromDate)}&withinDays=${withinDays}`),
    listFriends: () => call("GET", "friends"),
    deleteFriend: async (friendId) => {
      await call("DELETE", `friends/${encodeURIComponent(friendId)}`);
    },
    saveSplit: (docId, request) => call<Split>("PUT", `documents/${encodeURIComponent(docId)}/split`, request),
    async uploadDocument(file) {
      const ticket = await call<UploadTicket>("POST", "documents", {
        filename: file.name,
        contentType: file.type,
        sizeBytes: file.size,
      });
      // S3 POST policy: the signed fields first, the file last. No auth header: the signature
      // in the fields is the permission, and S3 enforces the size/type limits it encodes.
      const form = new FormData();
      for (const [name, value] of Object.entries(ticket.upload.fields)) form.append(name, value);
      form.append("file", file);
      const response = await fetchImpl(ticket.upload.url, { method: "POST", body: form });
      if (!response.ok) throw new ApiError("Upload failed", response.status);
      return ticket.docId;
    },
  };
}
