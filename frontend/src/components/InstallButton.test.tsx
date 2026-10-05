import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { listenForInstall, resetInstallForTests } from "../install";
import { InstallButton } from "./InstallButton";

const UA = navigator.userAgent;
function setUserAgent(ua: string, touchPoints = 0) {
  Object.defineProperty(navigator, "userAgent", { value: ua, configurable: true });
  Object.defineProperty(navigator, "maxTouchPoints", { value: touchPoints, configurable: true });
}

/** What Chrome fires when the app can be installed. */
function offerInstall(outcome: "accepted" | "dismissed" = "accepted") {
  const event = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), {
    prompt: vi.fn().mockResolvedValue(undefined),
    userChoice: Promise.resolve({ outcome }),
  });
  act(() => {
    window.dispatchEvent(event);
  });
  return event;
}

beforeEach(() => {
  resetInstallForTests();
  listenForInstall(window);
});
afterEach(() => setUserAgent(UA));

describe("InstallButton", () => {
  it("is hidden where the browser can't install apps", () => {
    render(<InstallButton />);
    expect(screen.queryByRole("button", { name: /install app/i })).not.toBeInTheDocument();
  });

  it("one click opens the browser's install dialog when it's offered", async () => {
    render(<InstallButton />);
    const event = offerInstall();

    expect(event.defaultPrevented).toBe(true); // kept for our button
    await userEvent.click(screen.getByRole("button", { name: /install app/i }));

    expect(event.prompt).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /install app/i })).not.toBeInTheDocument(); // installed
  });

  it("disappears once the app is installed", () => {
    render(<InstallButton />);
    offerInstall();
    expect(screen.getByRole("button", { name: /install app/i })).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new Event("appinstalled"));
    });

    expect(screen.queryByRole("button", { name: /install app/i })).not.toBeInTheDocument();
  });

  it("on iPhone, explains Share → Add to Home Screen", async () => {
    setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1");
    render(<InstallButton />);

    await userEvent.click(screen.getByRole("button", { name: /install app/i }));

    expect(screen.getByRole("status")).toHaveTextContent(/share.*add to home screen/i);
  });

  it("recognizes an iPad, which reports itself as a Mac", () => {
    setUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15", 5);
    render(<InstallButton />);
    expect(screen.getByRole("button", { name: /install app/i })).toBeInTheDocument();
  });

  it("on Android before Chrome offers installing, shows the browser-menu steps", async () => {
    setUserAgent("Mozilla/5.0 (Linux; Android 14; SM-S921U) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36");
    render(<InstallButton />);

    await userEvent.click(screen.getByRole("button", { name: /install app/i }));

    expect(screen.getByRole("status")).toHaveTextContent(/menu.*install app.*add to home screen/i);
  });

  it("on Android once Chrome offers installing, it's one tap", async () => {
    setUserAgent("Mozilla/5.0 (Linux; Android 14; SM-S921U) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36");
    render(<InstallButton />);
    const event = offerInstall();

    await userEvent.click(screen.getByRole("button", { name: /install app/i }));

    expect(event.prompt).toHaveBeenCalledOnce();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
