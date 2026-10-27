import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
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
  it("is just a small chat button that opens the conversation", async () => {
    const onOpen = vi.fn();
    render(<AskLauncher onOpen={onOpen} />);

    await userEvent.click(screen.getByRole("button", { name: "Ask OneBill" }));

    expect(onOpen).toHaveBeenCalledOnce();
    expect(screen.getAllByRole("button")).toHaveLength(1); // no suggestion beside it covering the page
  });

  it("is hidden while the chat is open", () => {
    render(<AskLauncher chatOpen onOpen={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "Ask OneBill" })).not.toBeInTheDocument();
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
