import { useQueryClient } from "@tanstack/react-query";
import { Hub } from "aws-amplify/utils";
import { useEffect, useMemo, useState } from "react";
import { createApi } from "./api";
import { getAccessToken, isSignedIn, signIn, signOut } from "./auth";
import { type AuthStatus, Home } from "./components/Home";
import type { AppConfig } from "./config";

export function App({ config }: { config: AppConfig }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const queryClient = useQueryClient();
  const api = useMemo(() => createApi(config.apiUrl, getAccessToken), [config.apiUrl]);

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

  return <Home status={status} api={api} onSignIn={signIn} onSignOut={signOut} />;
}
