import { describe, expect, it } from "vitest";
import type { DocumentSummary } from "./api";
import { POLL_MS, pollInterval } from "./documents";

const doc = (status: DocumentSummary["status"]) => ({ docId: status, status }) as DocumentSummary;

describe("pollInterval", () => {
  it("checks every 1.5 s while something is being read (scan latency: avg wait 0.75 s, not 2.5 s)", () => {
    expect(POLL_MS).toBe(1500);
    expect(pollInterval([doc("ready"), doc("processing")])).toBe(1500);
    expect(pollInterval([doc("pending_upload")])).toBe(1500);
  });

  it("stops polling once everything is done", () => {
    expect(pollInterval([doc("ready"), doc("needs_review"), doc("failed")])).toBe(false);
    expect(pollInterval(undefined)).toBe(false);
  });
});
