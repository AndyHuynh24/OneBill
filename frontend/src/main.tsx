import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { configureAuth } from "./auth";
import { loadConfig } from "./config";
import "./index.css";

const root = createRoot(document.getElementById("root")!);

try {
  const config = loadConfig();
  configureAuth(config);
  const queryClient = new QueryClient();
  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <App config={config} />
      </QueryClientProvider>
    </StrictMode>,
  );
} catch (err) {
  root.render(
    <main className="page">
      <h1>OneTally</h1>
      <p role="alert" className="error">
        {err instanceof Error ? err.message : "Failed to start"}
      </p>
    </main>,
  );
}
