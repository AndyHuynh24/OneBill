import type { AskTurn } from "./api";

/** The conversation so far as question → answer pairs (text only): a question that failed has no
 *  answer, so it isn't sent back. */
export function answeredPairs(messages: AskTurn[]): AskTurn[] {
  const out: AskTurn[] = [];
  messages.forEach((m, i) => {
    const next = messages[i + 1];
    if (m.role === "user" && next?.role === "assistant") {
      out.push({ role: "user", text: m.text }, { role: "assistant", text: next.text });
    }
  });
  return out;
}

/** Suggested questions for what you have (most useful first), or a couple of general ones. */
export function suggestedQuestions(have: { activeTrip?: string; owed?: boolean; subscriptions?: boolean }): string[] {
  const out: string[] = [];
  if (have.owed) out.push("Who still owes me?");
  if (have.activeTrip) out.push(`Summarize my ${have.activeTrip} trip`);
  if (have.subscriptions) out.push("What do my subscriptions cost a month?");
  return out.length ? out.slice(0, 3) : ["How much did I spend this month?", "Where did I buy my last coffee?"];
}
