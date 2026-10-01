import { describe, expect, it } from "vitest";
import { appOrigins, parseDevOrigins } from "../lib/origins";

describe("appOrigins", () => {
  it("is just the frontend URL by default", () => {
    expect(appOrigins("http://localhost:5173")).toEqual(["http://localhost:5173"]);
  });

  it("adds HTTPS dev origins (another device on the same network)", () => {
    expect(appOrigins("http://localhost:5173", ["https://10.0.0.136:5173"])).toEqual([
      "http://localhost:5173",
      "https://10.0.0.136:5173",
    ]);
  });

  it("rejects plain-HTTP or malformed dev origins (Cognito requires HTTPS off localhost)", () => {
    expect(() => appOrigins("http://localhost:5173", ["http://10.0.0.136:5173"])).toThrow(/https/i);
    expect(() => appOrigins("http://localhost:5173", ["https://10.0.0.136:5173/path"])).toThrow(/origin/i);
    expect(() => appOrigins("http://localhost:5173", ["*"])).toThrow();
  });
});

describe("parseDevOrigins (cdk.json array or -c comma list)", () => {
  it.each([
    [undefined, []],
    ["", []],
    [["https://a.local:5173"], ["https://a.local:5173"]],
    ["https://a.local:5173, https://b.local:5173", ["https://a.local:5173", "https://b.local:5173"]],
  ])("%j → %j", (raw, expected) => {
    expect(parseDevOrigins(raw)).toEqual(expected);
  });
});
