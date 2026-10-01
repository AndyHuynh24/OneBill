import { Amplify } from "aws-amplify";
import {
  fetchAuthSession,
  getCurrentUser,
  signInWithRedirect,
  signOut as amplifySignOut,
} from "aws-amplify/auth";
// Completes the hosted-login redirect (exchanges ?code= for tokens) when the app loads on /callback.
import "aws-amplify/auth/enable-oauth-listener";
import type { AppConfig } from "./config";

/**
 * Amplify Auth talks to Cognito only; there is no Amplify backend in this project.
 * Sign-in returns to the origin the app was opened on; Cognito only accepts origins registered
 * in the Auth stack.
 */
export function configureAuth(config: AppConfig, origin: string = window.location.origin): void {
  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId: config.userPoolId,
        userPoolClientId: config.userPoolClientId,
        loginWith: {
          oauth: {
            domain: config.cognitoDomain,
            scopes: ["openid", "email", "profile"],
            redirectSignIn: [`${origin}/callback`],
            redirectSignOut: [`${origin}/`],
            responseType: "code", // authorization code + PKCE, never the implicit "token" flow
          },
        },
      },
    },
  });
}

export async function isSignedIn(): Promise<boolean> {
  try {
    await getCurrentUser();
    return true;
  } catch {
    return false;
  }
}

/** The access token is what the API's JWT authorizer verifies. Amplify refreshes it when expired. */
export async function getAccessToken(): Promise<string | undefined> {
  const session = await fetchAuthSession();
  return session.tokens?.accessToken?.toString();
}

export const signIn = () => signInWithRedirect();
export const signOut = () => amplifySignOut();
