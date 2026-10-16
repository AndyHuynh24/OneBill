import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { AskSource, AskTurn, OneBillApi } from "../api";
import { answeredPairs } from "../ask";
import { formatCents, formatDate } from "../format";

const MAX_HISTORY = 6;

const CYCLE_MS = 6000;

/** The chat button, bottom right, with one small suggestion beside it that cycles every few
 *  seconds. Tap it to ask. Opening the chat or × puts suggestions away until the app is next
 *  opened or reloaded. While the chat is open the button is hidden (it stays mounted, so the
 *  suggestions stay put away when it comes back). */
export function AskLauncher({
  suggestions,
  chatOpen = false,
  onOpen,
}: {
  suggestions: string[];
  chatOpen?: boolean;
  onOpen: (question?: string) => void;
}) {
  const [hidden, setHidden] = useState(false);
  const [index, setIndex] = useState(0);
  const showing = !hidden && suggestions.length > 0;
  const open = (question?: string) => {
    setHidden(true);
    if (question === undefined) onOpen();
    else onOpen(question);
  };

  useEffect(() => {
    if (!showing || suggestions.length < 2) return;
    const timer = setInterval(() => setIndex((i) => i + 1), CYCLE_MS);
    return () => clearInterval(timer);
  }, [showing, suggestions.length]);

  const current = suggestions[index % Math.max(suggestions.length, 1)];
  if (chatOpen) return null;
  return (
    <div className="ask-launcher">
      {showing && (
        <div className="ask-hint" role="group" aria-label="Suggested question">
          <button key={current} type="button" className="ask-hint-question" onClick={() => open(current)}>
            {current}
          </button>
          <button type="button" className="icon-button ask-hint-hide" aria-label="Hide suggestions" onClick={() => setHidden(true)}>
            ×
          </button>
        </div>
      )}
      <button type="button" className="ask-fab" aria-label="Ask OneBill" onClick={() => open()}>
        <svg aria-hidden="true" viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-4.6A8 8 0 1 1 21 12z" />
          <path d="M8.5 11h.01M12 11h.01M15.5 11h.01" />
        </svg>
      </button>
    </div>
  );
}

interface Message extends AskTurn {
  sources?: AskSource[];
}

interface AskSheetProps {
  api: OneBillApi;
  /** A suggestion that was tapped: asked right away. Without one, the suggestions are offered. */
  firstQuestion?: string;
  suggestions?: string[];
  onClose: () => void;
  onOpenDocument: (docId: string) => void;
}

/** The conversation: answers come from your own bills, with the bills they used underneath. */
export function AskSheet({ api, firstQuestion, suggestions = [], onClose, onOpenDocument }: AskSheetProps) {
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
    if (asked.current || !firstQuestion) return; // once, even when React runs effects twice in development
    asked.current = true;
    send(firstQuestion);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // A block body: newer browsers return a Promise here, and an effect may only return cleanup.
    end.current?.scrollIntoView?.({ block: "end" });
  }, [messages, ask.isPending]);

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
          {messages.length === 0 && !ask.isPending && (
            <div className="ask-empty">
              <p className="muted">Ask anything about your own bills, splits, trips, or subscriptions.</p>
              <div className="ask-suggestions-inline">
                {suggestions.map((q) => (
                  <button key={q} type="button" className="ask-suggestion" onClick={() => send(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
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
            Ask a question
          </label>
          <input
            id="ask-follow-up"
            value={followUp}
            maxLength={500}
            placeholder={messages.length ? "Ask a follow-up…" : "e.g. How much did I spend at Trader Joe's?"}
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
