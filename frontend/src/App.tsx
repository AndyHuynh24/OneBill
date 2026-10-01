import { useQueryClient } from "@tanstack/react-query";
import { Hub } from "aws-amplify/utils";
import { useCallback, useEffect, useState } from "react";
import { fetchProfile } from "./api";
import { getAccessToken, isSignedIn, signIn, signOut } from "./auth";
import { type AuthStatus, Home } from "./components/Home";
import type { AppConfig } from "./config";

export function App({ config }: { config: AppConfig }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const queryClient = useQueryClient();

  useEffect(() => {
    isSignedIn().then((signedIn) => setStatus(signedIn ? "signedIn" : "signedOut"));

    // Amplify announces auth changes on its Hub event bus.
    return Hub.listen("auth", ({ payload }) => {
      switch (payload.event) {
        case "signInWithRedirect":
          setStatus("signedIn");
          window.history.replaceState({}, "", "/"); // drop /callback?code=... from the address bar
          break;
        case "signInWithRedirect_failure":
          setStatus("signedOut");
          window.history.replaceState({}, "", "/");
          break;
        case "signedOut":
          queryClient.clear(); // never show the previous user's data
          setStatus("signedOut");
          break;
      }
    });
  }, [queryClient]);

  const loadProfile = useCallback(() => fetchProfile(config.apiUrl, getAccessToken), [config.apiUrl]);

  return <Home status={status} onSignIn={signIn} onSignOut={signOut} loadProfile={loadProfile} />;
}
