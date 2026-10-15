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
