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

/** How sure the reading is, from evidence (OCR clarity, amounts found, math), phase 2d. */
export interface Confidence {
  level: "confident" | "mostly" | "check" | "confirmed";
  /** suggestedCents: one digit off; this amount would make the bill add up. */
  checks: { row: number; reason: string; suggestedCents?: number }[];
}

export interface DocumentSummary {
  docId: string;
  status: DocStatus;
  confidence?: Confidence;
  filename?: string;
  contentType?: string;
  vendor?: string | null;
  documentDate?: string | null;
  totalCents?: number | null;
  currency?: string | null;
  createdAt: string;
  updatedAt?: string;
  reviewOutcome?: "accepted" | "corrected";
  /** "email": a labelled email read like a scan. */
  source?: "email";
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
  /** Item prices already include the tax (common outside the US). */
  taxIncluded?: boolean;
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
  /** The document version the split was made on; refused (409) if the lines changed since. */
  expectedUpdatedAt: string;
}

export interface SplitMember {
  id: string;
  name: string;
  self: boolean;
  color: number;
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
  /** What the shares add up to: the printed total, or the recorded items if they differ. */
  totalCents: number;
  receiptTotalCents?: number;
  matchesReceipt?: boolean;
  taxIncluded?: boolean;
  updatedAt?: string;
}

export type BillingCycle = "weekly" | "monthly" | "quarterly" | "yearly" | "other";
export type SubscriptionStatus = "charged" | "upcoming" | "trial_ending" | "payment_failed" | "cancelled";

/** One service, as read from its emails (amounts in cents). */
export interface Subscription {
  serviceKey: string;
  service: string;
  plan: string | null;
  amountCents: number | null;
  currency: string;
  cycle: BillingCycle | null;
  status: SubscriptionStatus;
  nextRenewal: string | null;
  renewalComputed: boolean;
  lastChargedAt?: string | null;
  previousAmountCents?: number;
  priceChangedAt?: string;
  history: { date: string; amountCents: number; status: string }[];
  issues: string[];
  remindersOff?: boolean;
  /** Bills (rent, utilities) are "due"; subscriptions "renew". */
  category?: "subscription" | "bill";
  source?: "manual";
  updatedAt?: string;
}

/** Added by hand: anything that doesn't email (rent, utilities, a gym billed to a card). */
export interface NewSubscription {
  service: string;
  category: "subscription" | "bill";
  amountCents?: number;
  cycle: BillingCycle;
  nextRenewal: string;
}

/** The Gmail script's connection; the key itself is never returned after creation. */
export interface GmailConnection {
  connected: boolean;
  createdAt: string | null;
  lastUsedAt: string | null;
}

export interface SubscriptionEdit {
  amountCents?: number;
  nextRenewal?: string;
  cycle?: BillingCycle;
  plan?: string | null;
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

export type ReviewRequest =
  | { action: "accept" }
  | { action: "correct"; fields: CorrectionFields }
  /** "Looks right" on flagged lines: clears just those flags. */
  | { action: "confirm"; rows: number[] };

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
  /** The API's address (shown in Connect Gmail, for the Apps Script). */
  readonly baseUrl: string;
  getProfile(): Promise<Profile>;
  listDocuments(): Promise<{ documents: DocumentSummary[]; review: ReviewStats }>;
  getDocument(docId: string): Promise<DocumentDetail>;
  reviewDocument(docId: string, request: ReviewRequest): Promise<DocumentDetail>;
  listObligations(fromDate: string, withinDays?: number): Promise<{ obligations: Obligation[] }>;
  /** Ask the API for an upload ticket, then send the file straight to S3. */
  uploadDocument(file: File): Promise<string>;
  saveSplit(docId: string, request: SplitRequest): Promise<Split>;
  listSubscriptions(): Promise<{ subscriptions: Subscription[]; connection: GmailConnection }>;
  cancelSubscription(serviceKey: string): Promise<Subscription>;
  editSubscription(serviceKey: string, fields: SubscriptionEdit): Promise<Subscription>;
  /** Shown once; only its hash is stored. */
  createIngestKey(): Promise<{ key: string }>;
  revokeIngestKey(): Promise<void>;
  /** Gone from OneTally (with its reminder); it can come back with new emails. */
  deleteSubscription(serviceKey: string): Promise<void>;
  addSubscription(subscription: NewSubscription): Promise<Subscription>;
  setSubscriptionReminders(serviceKey: string, on: boolean): Promise<Subscription>;
  /** A document's deadline (removed from the document too) or a subscription's renewal (turns its reminders off). */
  deleteObligation(oblId: string): Promise<void>;
  /** The receipt or bill, its deadlines, and its photo. */
  deleteDocument(docId: string): Promise<void>;
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
    baseUrl: apiUrl,
    getProfile: () => call<Profile>("GET", "me"),
    listDocuments: () => call("GET", "documents"),
    getDocument: (docId) => call<DocumentDetail>("GET", `documents/${encodeURIComponent(docId)}`),
    reviewDocument: (docId, request) =>
      call<DocumentDetail>("PATCH", `documents/${encodeURIComponent(docId)}`, request),
    listObligations: (fromDate, withinDays = 30) =>
      call("GET", `obligations?from=${encodeURIComponent(fromDate)}&withinDays=${withinDays}`),
    listSubscriptions: () => call("GET", "subscriptions"),
    cancelSubscription: (key) =>
      call<Subscription>("PATCH", `subscriptions/${encodeURIComponent(key)}`, { action: "cancel" }),
    editSubscription: (key, fields) =>
      call<Subscription>("PATCH", `subscriptions/${encodeURIComponent(key)}`, { action: "edit", fields }),
    createIngestKey: () => call("POST", "ingest/keys"),
    revokeIngestKey: async () => {
      await call("DELETE", "ingest/keys");
    },
    addSubscription: (subscription) => call<Subscription>("POST", "subscriptions", subscription),
    deleteSubscription: async (key) => {
      await call("DELETE", `subscriptions/${encodeURIComponent(key)}`);
    },
    setSubscriptionReminders: (key, on) =>
      call<Subscription>("PATCH", `subscriptions/${encodeURIComponent(key)}`, { action: "reminders", on }),
    deleteObligation: async (oblId) => {
      await call("DELETE", `obligations/${encodeURIComponent(oblId)}`);
    },
    deleteDocument: async (docId) => {
      await call("DELETE", `documents/${encodeURIComponent(docId)}`);
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
