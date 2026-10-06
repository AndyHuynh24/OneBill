import { act, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EXIT_WINDOW_MS, resetBackButtonForTests, trapBackButton, useBackLayer, useExitHint } from "./backButton";

/** The phone's Back button: the browser pops our extra entry and fires popstate. */
const pressBack = () =>
  act(() => {
    window.dispatchEvent(new PopStateEvent("popstate"));
  });

function Screen() {
  const [sheet, setSheet] = useState(true);
  const [steps, setSteps] = useState(3); // e.g. a split three steps in
  useEffect(() => trapBackButton(), []);
  useBackLayer(sheet, () => setSheet(false));
  useBackLayer(sheet && steps > 1, () => setSteps((n) => n - 1), steps);
  const hint = useExitHint();
  return (
    <>
      {sheet && <p>sheet open, step {steps}</p>}
      {hint && <p>Press back again to exit</p>}
    </>
  );
}

let leave: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  resetBackButtonForTests();
  leave = vi.spyOn(window.history, "back").mockImplementation(() => {});
  vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
});
afterEach(() => {
  vi.useRealTimers();
  leave.mockRestore();
});

describe("the Back button", () => {
  it("steps back through what's open, newest first, then closes the sheet", () => {
    render(<Screen />);
    pressBack();
    expect(screen.getByText("sheet open, step 2")).toBeInTheDocument();
    pressBack();
    expect(screen.getByText("sheet open, step 1")).toBeInTheDocument();
    pressBack();
    expect(screen.queryByText(/sheet open/)).not.toBeInTheDocument();
    expect(leave).not.toHaveBeenCalled(); // still in the app
  });

  it("on the home screen, one press only shows a hint; it never leaves", () => {
    render(<Screen />);
    pressBack();
    pressBack();
    pressBack(); // sheet closed: now on home

    pressBack();

    expect(screen.getByText("Press back again to exit")).toBeInTheDocument();
    expect(leave).not.toHaveBeenCalled();
  });

  it("a second press within 2 seconds leaves the app", () => {
    render(<Screen />);
    for (let i = 0; i < 3; i++) pressBack(); // close everything

    pressBack();
    act(() => {
      vi.advanceTimersByTime(EXIT_WINDOW_MS - 500);
    });
    pressBack();

    expect(leave).toHaveBeenCalledOnce();
  });

  it("presses far apart don't leave: the hint just shows again", () => {
    render(<Screen />);
    for (let i = 0; i < 3; i++) pressBack();

    pressBack();
    act(() => {
      vi.advanceTimersByTime(EXIT_WINDOW_MS + 500);
    });
    expect(screen.queryByText("Press back again to exit")).not.toBeInTheDocument();
    pressBack();

    expect(leave).not.toHaveBeenCalled();
    expect(screen.getByText("Press back again to exit")).toBeInTheDocument();
  });

  it("keeps one extra history entry on top, so Back comes to the app first", () => {
    const push = vi.spyOn(window.history, "pushState");
    window.history.replaceState(null, "");
    render(<Screen />);
    expect(push).toHaveBeenCalledWith({ onebill: "back-trap" }, "");
    pressBack();
    expect(push).toHaveBeenCalledTimes(2); // put back after handling the press
    push.mockRestore();
  });
});
