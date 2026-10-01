import { describe, expect, it } from "vitest";
import { loadConfig } from "./config";

const FULL = {
  VITE_USER_POOL_ID: "us-east-1_abc",
  VITE_USER_POOL_CLIENT_ID: "client123",
  VITE_COGNITO_DOMAIN: "onetally-ah24.auth.us-east-1.amazoncognito.com",
  VITE_API_URL: "https://abc.execute-api.us-east-1.amazonaws.com/",
};

describe("loadConfig", () => {
  it("reads all values (the app URL comes from wherever the page is opened)", () => {
    expect(loadConfig(FULL)).toEqual({
      userPoolId: "us-east-1_abc",
      userPoolClientId: "client123",
      cognitoDomain: "onetally-ah24.auth.us-east-1.amazoncognito.com",
      apiUrl: "https://abc.execute-api.us-east-1.amazonaws.com/",
    });
  });

  it("names every missing value and how to fix it", () => {
    expect(() => loadConfig({ VITE_USER_POOL_ID: "x" })).toThrow(
      /userPoolClientId, cognitoDomain, apiUrl.*make frontend-env/,
    );
  });
});
