import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { GmailConnection, OneBillApi } from "../api";
import { fakeApi } from "../test/fakeApi";
import { ConnectGmail } from "./ConnectGmail";

const OFF: GmailConnection = { connected: false, createdAt: null, lastUsedAt: null };

function renderSheet(api: OneBillApi, connection = OFF) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ConnectGmail api={api} connection={connection} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("ConnectGmail", () => {
  it("creates a key and offers a ready-made script with it filled in", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderSheet(fakeApi());

    await userEvent.click(screen.getByRole("button", { name: /create key/i }));
    await userEvent.click(await screen.findByRole("button", { name: /copy script/i }));

    const script = writeText.mock.calls[0][0] as string;
    expect(script).toContain("var ONEBILL_KEY = 'obk_" + "k".repeat(43) + "';");
    expect(script).toContain("var ONEBILL_URL = 'https://api.example.com/';");
    expect(screen.getByText(/only once/i)).toBeInTheDocument();
  });

  it("copies the settings file that asks for read-only Gmail access", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderSheet(fakeApi());
    await userEvent.click(screen.getByRole("button", { name: /create key/i }));

    await userEvent.click(await screen.findByRole("button", { name: /copy settings file/i }));

    expect(writeText.mock.calls[0][0]).toContain("gmail.readonly");
  });

  it("when connected, can disconnect", async () => {
    const api = fakeApi();
    renderSheet(api, { connected: true, createdAt: "2026-10-01T10:00:00Z", lastUsedAt: null });

    expect(screen.getByText(/connected/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /disconnect/i }));

    expect(api.revokeIngestKey).toHaveBeenCalled();
  });
});
