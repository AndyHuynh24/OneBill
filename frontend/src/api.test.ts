import { describe, expect, it, vi } from "vitest";
import { ApiError, createApi } from "./api";

const API_URL = "https://abc.execute-api.us-east-1.amazonaws.com/";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

// `null` = signed out. (Passing `undefined` would silently fall back to the default token.)
function setup(responses: Response[], token: string | null = "token-xyz") {
  const fetchMock = vi.fn();
  for (const r of responses) fetchMock.mockResolvedValueOnce(r);
  const api = createApi(API_URL, async () => token ?? undefined, fetchMock as unknown as typeof fetch);
  return { api, fetchMock };
}

describe("createApi", () => {
  it("sends the access token as a Bearer header", async () => {
    const { api, fetchMock } = setup([json({ userId: "u", baseCurrency: "USD", createdAt: "x" })]);

    await api.getProfile();

    expect(fetchMock).toHaveBeenCalledWith(`${API_URL}me`, {
      method: "GET",
      headers: { Authorization: "Bearer token-xyz" },
    });
  });

  it("refuses to call the API without a token", async () => {
    const { api, fetchMock } = setup([], null);

    await expect(api.listDocuments()).rejects.toThrow(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces the API's error message and status", async () => {
    const { api } = setup([json({ message: "Uploads are invite-only for now." }, 403)]);

    await expect(api.listDocuments()).rejects.toMatchObject({
      status: 403,
      message: "Uploads are invite-only for now.",
    });
  });

  it("asks for obligations from the browser's local date", async () => {
    const { api, fetchMock } = setup([json({ obligations: [] })]);

    await api.listObligations("2026-09-30");

    expect(fetchMock.mock.calls[0][0]).toBe(`${API_URL}obligations?from=2026-09-30&withinDays=30`);
  });

  it("sends review actions as JSON with PATCH", async () => {
    const { api, fetchMock } = setup([json({ docId: "d1", status: "ready", createdAt: "x" })]);

    await api.reviewDocument("d1", { action: "correct", fields: { vendor: "Target" } });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}documents/d1`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ action: "correct", fields: { vendor: "Target" } });
  });

  it("uploads in two steps: ticket from the API, then the file straight to S3", async () => {
    const ticket = {
      docId: "doc-1",
      upload: { url: "https://bucket.s3.amazonaws.com/", fields: { key: "uploads/u/doc-1", policy: "p" } },
    };
    const { api, fetchMock } = setup([json(ticket, 201), new Response(null, { status: 204 })]);
    const file = new File(["jpeg-bytes"], "receipt.jpg", { type: "image/jpeg" });

    const docId = await api.uploadDocument(file);

    expect(docId).toBe("doc-1");
    const [ticketUrl, ticketInit] = fetchMock.mock.calls[0];
    expect(ticketUrl).toBe(`${API_URL}documents`);
    expect(JSON.parse(ticketInit.body)).toEqual({
      filename: "receipt.jpg",
      contentType: "image/jpeg",
      sizeBytes: file.size,
    });
    const [s3Url, s3Init] = fetchMock.mock.calls[1];
    expect(s3Url).toBe("https://bucket.s3.amazonaws.com/");
    expect(s3Init.headers).toBeUndefined(); // the signed form fields are the permission
    const entries = [...(s3Init.body as FormData).keys()];
    expect(entries).toEqual(["key", "policy", "file"]); // file must come last
  });

  it("saves a split with PUT; people go by seat and lines by row, never amounts", async () => {
    const { api, fetchMock } = setup([json({ version: 1, members: [], shares: [] })]);
    const request = {
      members: [{ name: "Me", self: true }, { name: "Bao" }],
      assignments: { "1": [0], "2": [1] },
      charges: { "3": { mode: "proportional" as const } },
      expectedVersion: 0,
      expectedUpdatedAt: "2026-10-01T19:05:00+00:00",
    };

    await api.saveSplit("doc-1", request);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_URL}documents/doc-1/split`);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual(request);
  });

  it("reports a failed S3 upload", async () => {
    const ticket = { docId: "doc-1", upload: { url: "https://bucket.s3.amazonaws.com/", fields: {} } };
    const { api } = setup([json(ticket, 201), new Response("<Error/>", { status: 403 })]);

    await expect(api.uploadDocument(new File(["x"], "a.jpg", { type: "image/jpeg" }))).rejects.toMatchObject({
      status: 403,
    });
  });
});
