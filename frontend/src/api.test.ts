import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchProfile } from "./api";

const API_URL = "https://abc.execute-api.us-east-1.amazonaws.com/";
const PROFILE = { userId: "abc-123", baseCurrency: "USD", createdAt: "2026-09-29T12:00:00+00:00" };

afterEach(() => vi.unstubAllGlobals());

describe("fetchProfile", () => {
  it("calls GET /me with the access token as a Bearer header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(PROFILE), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const profile = await fetchProfile(API_URL, async () => "token-xyz");

    expect(profile).toEqual(PROFILE);
    expect(fetchMock).toHaveBeenCalledWith(`${API_URL}me`, {
      headers: { Authorization: "Bearer token-xyz" },
    });
  });

  it("fails without calling the API when there is no token", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchProfile(API_URL, async () => undefined)).rejects.toThrow(ApiError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("throws an ApiError carrying the HTTP status on failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));

    await expect(fetchProfile(API_URL, async () => "expired")).rejects.toMatchObject({ status: 401 });
  });
});
