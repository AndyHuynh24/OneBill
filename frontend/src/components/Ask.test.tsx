import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { fakeApi } from "../test/fakeApi";
import { answeredPairs, suggestedQuestions } from "../ask";
import { AskLauncher, AskSheet } from "./Ask";

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const ANSWER = {
  answer: "You spent $61.00 at Trader Joe's in September.",
  sources: [{ docId: "d1", vendor: "TRADER JOE'S", date: "2026-09-03", totalCents: 4210, currency: "USD" }],
  questionsLeft: 9,
};

afterEach(() => localStorage.clear());

describe("AskLauncher", () => {
  const SUGGESTIONS = ["Who still owes me?", "Summarize my Lake Tahoe trip"];

  it("is a small chat button that opens the conversation", async () => {
    const onOpen = vi.fn();
    render(<AskLauncher suggestions={SUGGESTIONS} onOpen={onOpen} />);

    await userEvent.click(screen.getByRole("button", { name: "Ask OneBill" }));

    expect(onOpen).toHaveBeenCalledWith();
  });

  it("shows one suggestion at a time beside it; tapping it asks it", async () => {
    const onOpen = vi.fn();
    render(<AskLauncher suggestions={SUGGESTIONS} onOpen={onOpen} />);

    expect(screen.queryByRole("button", { name: "Summarize my Lake Tahoe trip" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Who still owes me?" }));

    expect(onOpen).toHaveBeenCalledWith("Who still owes me?");
  });

  it("cycles to the next suggestion every few seconds", () => {
    vi.useFakeTimers();
    render(<AskLauncher suggestions={SUGGESTIONS} onOpen={vi.fn()} />);

    act(() => vi.advanceTimersByTime(6000));

    expect(screen.getByRole("button", { name: "Summarize my Lake Tahoe trip" })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it.each([
    ["the chat button", "Ask OneBill"],
    ["a suggestion", "Who still owes me?"],
  ])("opening the chat from %s puts the suggestions away", async (_, name) => {
    render(<AskLauncher suggestions={SUGGESTIONS} onOpen={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name }));

    expect(screen.queryByRole("group", { name: "Suggested question" })).not.toBeInTheDocument();
  });

  it("× hides the suggestions, but they're back the next time the app opens", async () => {
    localStorage.setItem("onebill.askSuggestionsHidden", "1"); // hidden "for good" by an older version
    const { unmount } = render(<AskLauncher suggestions={SUGGESTIONS} onOpen={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Who still owes me?" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /hide suggestions/i }));
    expect(screen.queryByRole("button", { name: "Who still owes me?" })).not.toBeInTheDocument();
    unmount(); // the app is closed or reloaded

    render(<AskLauncher suggestions={SUGGESTIONS} onOpen={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Who still owes me?" })).toBeInTheDocument();
  });
});

describe("AskSheet", () => {
  it("asks, shows the answer with the bills it used, and opens one", async () => {
    const api = fakeApi({ ask: vi.fn().mockResolvedValue(ANSWER) });
    const onOpenDocument = vi.fn();
    wrap(<AskSheet api={api} firstQuestion="Trader Joe's in September?" onClose={vi.fn()} onOpenDocument={onOpenDocument} />);

    expect(await screen.findByText(ANSWER.answer)).toBeInTheDocument();
    expect(api.ask).toHaveBeenCalledWith({ question: "Trader Joe's in September?", history: [] });
    await userEvent.click(screen.getByRole("button", { name: /trader joe's.*\$42\.10/i }));
    expect(onOpenDocument).toHaveBeenCalledWith("d1");
    expect(screen.getByText(/9 questions left today/i)).toBeInTheDocument();
  });

  it("sends the conversation so far with a follow-up", async () => {
    const api = fakeApi({ ask: vi.fn().mockResolvedValue(ANSWER) });
    wrap(<AskSheet api={api} firstQuestion="Trader Joe's in September?" onClose={vi.fn()} onOpenDocument={vi.fn()} />);
    await screen.findByText(ANSWER.answer);

    await userEvent.type(screen.getByRole("textbox", { name: /ask a question/i }), "And in August?{Enter}");

    expect(api.ask).toHaveBeenLastCalledWith({
      question: "And in August?",
      history: [
        { role: "user", text: "Trader Joe's in September?" },
        { role: "assistant", text: ANSWER.answer },
      ],
    });
  });

  it("works in browsers where scrolling returns a promise (it must not crash the sheet)", async () => {
    const scroll = vi.fn().mockResolvedValue(undefined); // newer Chrome: scrollIntoView returns a Promise
    Element.prototype.scrollIntoView = scroll;
    const api = fakeApi({ ask: vi.fn().mockResolvedValue(ANSWER) });
    wrap(<AskSheet api={api} firstQuestion="Who owes me?" onClose={vi.fn()} onOpenDocument={vi.fn()} />);

    expect(await screen.findByText(ANSWER.answer)).toBeInTheDocument();
    expect(scroll).toHaveBeenCalled();
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  });

  it("opened from the button, it offers the suggestions to start with", async () => {
    const api = fakeApi({ ask: vi.fn().mockResolvedValue(ANSWER) });
    wrap(<AskSheet api={api} suggestions={["Who still owes me?"]} onClose={vi.fn()} onOpenDocument={vi.fn()} />);

    expect(api.ask).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Who still owes me?" }));

    expect(api.ask).toHaveBeenCalledWith({ question: "Who still owes me?", history: [] });
  });

  it("shows why a question couldn't be answered", async () => {
    const api = fakeApi({
      ask: vi.fn().mockRejectedValue(new ApiError("You've asked today's 10 questions. Ask again tomorrow.", 429)),
    });
    wrap(<AskSheet api={api} firstQuestion="One more?" onClose={vi.fn()} onOpenDocument={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("today's 10 questions");
    const log = screen.getByRole("log");
    expect(within(log).getByText("One more?")).toBeInTheDocument();
  });
});

describe("suggestions", () => {
  it("are built from what you have: a trip that's on, money owed, subscriptions", () => {
    expect(suggestedQuestions({ activeTrip: "Lake Tahoe", owed: true, subscriptions: true })).toEqual([
      "Who still owes me?",
      "Summarize my Lake Tahoe trip",
      "What do my subscriptions cost a month?",
    ]);
    expect(suggestedQuestions({})).toEqual([
      "How much did I spend this month?",
      "Where did I buy my last coffee?",
    ]);
  });
});

describe("answeredPairs", () => {
  it("sends back only questions that got an answer", () => {
    expect(
      answeredPairs([
        { role: "user", text: "failed one" },
        { role: "user", text: "q1" },
        { role: "assistant", text: "a1" },
        { role: "user", text: "pending" },
      ]),
    ).toEqual([
      { role: "user", text: "q1" },
      { role: "assistant", text: "a1" },
    ]);
  });
});
