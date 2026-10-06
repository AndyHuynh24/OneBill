import { beforeEach, describe, expect, it, vi } from "vitest";
import { forgetFriend, loadFriends, MAX_FRIENDS, rememberFriends } from "./friends";

const T0 = new Date("2026-10-01T19:00:00Z");
const later = (days: number) => new Date(T0.getTime() + days * 86_400_000);

beforeEach(() => localStorage.clear());

describe("local recent friends", () => {
  it("ranks by how often, then how recently, you split with someone", () => {
    rememberFriends("u1", ["Bao", "Linh"], T0);
    rememberFriends("u1", ["bao", "Minh"], later(1));
    rememberFriends("u1", ["Bao"], later(2));

    const friends = loadFriends("u1");

    expect(friends.map((f) => f.name)).toEqual(["Bao", "Minh", "Linh"]);
    expect(friends[0].timesUsed).toBe(3);
  });

  it("is kept per account in this browser", () => {
    rememberFriends("u1", ["Bao"], T0);

    expect(loadFriends("u2")).toEqual([]);
  });

  it("forgets a friend in any spelling", () => {
    rememberFriends("u1", ["Bao", "Linh"], T0);

    forgetFriend("u1", "BAO");

    expect(loadFriends("u1").map((f) => f.name)).toEqual(["Linh"]);
  });

  it("keeps at most the top MAX_FRIENDS", () => {
    rememberFriends("u1", Array.from({ length: 60 }, (_, i) => `Friend ${i}`), T0);

    expect(loadFriends("u1")).toHaveLength(MAX_FRIENDS);
  });

  it("survives broken or unavailable storage", () => {
    localStorage.setItem("onebill.friends.u1", "{not json");
    expect(loadFriends("u1")).toEqual([]);

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(() => rememberFriends("u1", ["Bao"], T0)).not.toThrow();
    vi.restoreAllMocks();
  });
});
