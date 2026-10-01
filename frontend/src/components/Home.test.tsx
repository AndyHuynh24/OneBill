import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { Home } from "./Home";

const PROFILE = { userId: "abc-123", baseCurrency: "USD", createdAt: "2026-09-29T12:00:00+00:00" };

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function renderHome(overrides: Partial<Parameters<typeof Home>[0]> = {}) {
  const props = {
    status: "signedOut" as const,
    onSignIn: vi.fn(),
    onSignOut: vi.fn(),
    loadProfile: vi.fn().mockResolvedValue(PROFILE),
    ...overrides,
  };
  withQueryClient(<Home {...props} />);
  return props;
}

describe("Home", () => {
  it("shows a loading state while the session is being checked", () => {
    renderHome({ status: "loading" });

    expect(screen.getByText(/checking your session/i)).toBeInTheDocument();
  });

  it("offers Sign in when signed out, and calls onSignIn on click", async () => {
    const props = renderHome({ status: "signedOut" });

    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(props.onSignIn).toHaveBeenCalledOnce();
    expect(props.loadProfile).not.toHaveBeenCalled();
  });

  it("renders the profile from GET /me when signed in", async () => {
    renderHome({ status: "signedIn" });

    expect(await screen.findByText("abc-123")).toBeInTheDocument();
    expect(screen.getByText("USD")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
  });

  it("shows a readable error when the API call fails", async () => {
    renderHome({
      status: "signedIn",
      loadProfile: vi.fn().mockRejectedValue(new ApiError("Request failed", 500)),
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t load your profile/i);
  });

  it("calls onSignOut when Sign out is clicked", async () => {
    const props = renderHome({ status: "signedIn" });

    await userEvent.click(await screen.findByRole("button", { name: /sign out/i }));

    expect(props.onSignOut).toHaveBeenCalledOnce();
  });
});
