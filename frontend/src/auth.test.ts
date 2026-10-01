import { beforeEach, describe, expect, it, vi } from "vitest";

const configure = vi.fn();
vi.mock("aws-amplify", () => ({ Amplify: { configure } }));
vi.mock("aws-amplify/auth", () => ({}));
vi.mock("aws-amplify/auth/enable-oauth-listener", () => ({}));

const CONFIG = {
  userPoolId: "us-east-1_abc",
  userPoolClientId: "client123",
  cognitoDomain: "onetally-ah24.auth.us-east-1.amazoncognito.com",
  apiUrl: "https://abc.execute-api.us-east-1.amazonaws.com/",
};

describe("configureAuth", () => {
  beforeEach(() => configure.mockClear());

  it.each(["http://localhost:5173", "https://10.0.0.136:5173"])(
    "returns from sign-in to the origin the app was opened on (%s)",
    async (origin) => {
      const { configureAuth } = await import("./auth");

      configureAuth(CONFIG, origin);

      const oauth = configure.mock.calls[0][0].Auth.Cognito.loginWith.oauth;
      expect(oauth.redirectSignIn).toEqual([`${origin}/callback`]);
      expect(oauth.redirectSignOut).toEqual([`${origin}/`]);
      expect(oauth.responseType).toBe("code");
    },
  );
});
