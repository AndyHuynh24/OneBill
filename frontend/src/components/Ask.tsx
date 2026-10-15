import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { AskSource, AskTurn, OneBillApi } from "../api";
import { answeredPairs } from "../ask";
import { formatCents, formatDate } from "../format";

const MAX_HISTORY = 6;

/** "Ask about your bills…" on Home: one line that opens the conversation. */
export function AskBox({ onAsk }: { onAsk: (question: string) => void }) {
  const [text, setText] = useState("");
  return (
    <form
      className="ask-box"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) {
          onAsk(text.trim());
          setText("");
        }
      }}
    >
      <label htmlFor="ask-input" className="visually-hidden">
        Ask about your bills
      </label>
      <input
        id="ask-input"
        value={text}
        maxLength={500}
        placeholder="Ask about your bills…  e.g. Who still owes me?"
        enterKeyHint="send"
        autoComplete="off"
        onChange={(e) => setText(e.target.value)}
      />
    </form>
  );
}

interface Message extends AskTurn {
  sources?: AskSource[];
}

interface AskSheetProps {
  api: OneBillApi;
  firstQuestion: string;
  onClose: () => void;
  onOpenDocument: (docId: string) => void;
}

/** The conversation: answers come from your own bills, with the bills they used underneath. */
export function AskSheet({ api, firstQuestion, onClose, onOpenDocument }: AskSheetProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [followUp, setFollowUp] = useState("");
  const [left, setLeft] = useState<number | null>(null);
  const asked = useRef(false);
  const end = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: ({ question, history }: { question: string; history: AskTurn[] }) => api.ask({ question, history }),
    onMutate: ({ question }) => setMessages((m) => [...m, { role: "user", text: question }]),
    onSuccess: (response) => {
      setMessages((m) => [...m, { role: "assistant", text: response.answer, sources: response.sources }]);
      setLeft(response.questionsLeft);
    },
  });

  function send(question: string) {
    ask.mutate({ question, history: answeredPairs(messages).slice(-MAX_HISTORY) });
  }

  useEffect(() => {
    if (asked.current) return; // once, even when React runs effects twice in development
    asked.current = true;
    send(firstQuestion);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => end.current?.scrollIntoView?.({ block: "end" }), [messages, ask.isPending]);

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet ask-sheet" role="dialog" aria-modal="true" aria-labelledby="ask-title" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-header">
          <h2 id="ask-title">Ask OneBill</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="ask-log" role="log" aria-live="polite">
          {messages.map((m, i) => (
            <div key={i} className={`ask-message ask-${m.role}`}>
              <p>{m.text}</p>
              {!!m.sources?.length && (
                <ul className="plain-list ask-sources" aria-label="Bills this answer used">
                  {m.sources.map((s) => (
                    <li key={s.docId}>
                      <button type="button" className="link-button" onClick={() => onOpenDocument(s.docId)}>
                        {[s.vendor ?? "Receipt", s.date && formatDate(s.date), s.totalCents != null && formatCents(s.totalCents, s.currency ?? "USD")]
                          .filter(Boolean)
                          .join(" · ")}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          {ask.isPending && <p className="muted ask-thinking">Looking through your bills…</p>}
          <div ref={end} />
        </div>
        {ask.error && (
          <p role="alert" className="error">
            {ask.error.message}
          </p>
        )}
        <form
          className="ask-box ask-follow-up"
          onSubmit={(e) => {
            e.preventDefault();
            if (followUp.trim() && !ask.isPending) {
              send(followUp.trim());
              setFollowUp("");
            }
          }}
        >
          <label htmlFor="ask-follow-up" className="visually-hidden">
            Follow-up question
          </label>
          <input
            id="ask-follow-up"
            value={followUp}
            maxLength={500}
            placeholder="Ask a follow-up…"
            enterKeyHint="send"
            autoComplete="off"
            onChange={(e) => setFollowUp(e.target.value)}
          />
        </form>
        <p className="muted ask-note">
          Answers come only from your own bills{left != null ? ` · ${left} questions left today` : ""}.
        </p>
      </section>
    </div>
  );
}
