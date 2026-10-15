import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { fakeApi } from "../test/fakeApi";
import { answeredPairs } from "../ask";
import { AskBox, AskSheet } from "./Ask";

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const ANSWER = {
  answer: "You spent $61.00 at Trader Joe's in September.",
  sources: [{ docId: "d1", vendor: "TRADER JOE'S", date: "2026-09-03", totalCents: 4210, currency: "USD" }],
  questionsLeft: 9,
};

describe("AskBox", () => {
  it("opens the conversation with the question typed", async () => {
    const onAsk = vi.fn();
    render(<AskBox onAsk={onAsk} />);

    await userEvent.type(screen.getByRole("textbox", { name: /ask about your bills/i }), "Who owes me?{Enter}");

    expect(onAsk).toHaveBeenCalledWith("Who owes me?");
  });

  it("ignores an empty question", async () => {
    const onAsk = vi.fn();
    render(<AskBox onAsk={onAsk} />);

    await userEvent.type(screen.getByRole("textbox", { name: /ask about your bills/i }), "   {Enter}");

    expect(onAsk).not.toHaveBeenCalled();
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

    await userEvent.type(screen.getByRole("textbox", { name: /follow-up/i }), "And in August?{Enter}");

    expect(api.ask).toHaveBeenLastCalledWith({
      question: "And in August?",
      history: [
        { role: "user", text: "Trader Joe's in September?" },
        { role: "assistant", text: ANSWER.answer },
      ],
    });
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
