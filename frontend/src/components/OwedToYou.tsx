import { useState } from "react";
import type { DocumentSummary } from "../api";
import { useBackLayer } from "../backButton";
import { formatCents, formatDate } from "../format";

interface Entry {
  docId: string;
  title: string;
  date?: string | null;
  cents: number;
}

interface Person {
  name: string;
  cents: number;
  entries: Entry[];
}

/** Who owes you what, across split bills and money you lent (trips settle inside the trip).
 *  USD only: amounts in other currencies can't be added to a USD total. */
function owedByPerson(documents: DocumentSummary[]): Person[] {
  const people = new Map<string, Person>();
  for (const doc of documents) {
    if (doc.currency && doc.currency !== "USD") continue;
    for (const owed of doc.owedBy ?? []) {
      const key = owed.name.toLocaleLowerCase();
      const person = people.get(key) ?? { name: owed.name, cents: 0, entries: [] };
      person.cents += owed.cents;
      person.entries.push({ docId: doc.docId, title: doc.vendor ?? "Bill", date: doc.documentDate, cents: owed.cents });
      people.set(key, person);
    }
  }
  return [...people.values()].sort((a, b) => b.cents - a.cents);
}

function reminderText(person: Person): string {
  const parts = person.entries.map((e) => `${e.title} ${formatCents(e.cents, "USD")}`).join(", ");
  return `Hi ${person.name}! Just a reminder: you owe me ${formatCents(person.cents, "USD")} (${parts}). Thanks!`;
}

/** One line on Home, only when someone owes you; it opens a list by person with one Remind
 *  each. Ticking someone as paid happens on the bill itself (tap an entry). */
export function OwedToYou({ documents, onOpenDocument }: { documents: DocumentSummary[]; onOpenDocument: (docId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  useBackLayer(open, () => setOpen(false));
  const people = owedByPerson(documents);
  if (!people.length) return null;
  const total = people.reduce((sum, p) => sum + p.cents, 0);
  const names = people.map((p) => p.name);

  async function remind(person: Person) {
    const text = reminderText(person);
    if (typeof navigator.share === "function") {
      await navigator.share({ text }).catch(() => undefined); // closing the share sheet isn't an error
    } else {
      await navigator.clipboard?.writeText(text);
      setCopied(person.name);
    }
  }

  return (
    <>
      <button type="button" className="owed-line" onClick={() => setOpen(true)}>
        <span className="owed-line-label">Owed to you</span>
        <strong>{formatCents(total, "USD")}</strong>
        <span className="owed-line-names">
          {names.slice(0, 3).join(", ")}
          {names.length > 3 ? ` +${names.length - 3}` : ""}
        </span>
        <span aria-hidden="true">›</span>
      </button>
      {open && (
        <div className="sheet-backdrop" onClick={() => setOpen(false)}>
          <section className="sheet" role="dialog" aria-modal="true" aria-label="Owed to you" onClick={(e) => e.stopPropagation()}>
            <header className="sheet-header">
              <h2>Owed to you · {formatCents(total, "USD")}</h2>
              <button type="button" className="icon-button" aria-label="Close" onClick={() => setOpen(false)}>
                ×
              </button>
            </header>
            <ul className="owed-people">
              {people.map((person) => (
                <li key={person.name} aria-label={person.name} className="owed-person">
                  <div className="owed-person-head">
                    <strong>{person.name}</strong>
                    <strong>{formatCents(person.cents, "USD")}</strong>
                  </div>
                  {person.entries.map((entry) => (
                    <button
                      key={entry.docId}
                      type="button"
                      className="owed-entry"
                      onClick={() => {
                        setOpen(false);
                        onOpenDocument(entry.docId);
                      }}
                    >
                      <span>
                        {entry.title}
                        {entry.date ? <span className="muted"> · {formatDate(entry.date)}</span> : null}
                      </span>
                      <span>{formatCents(entry.cents, "USD")}</span>
                    </button>
                  ))}
                  <button type="button" className="link-button remind" onClick={() => remind(person)}>
                    {copied === person.name ? "Copied ✓" : `Remind ${person.name}`}
                  </button>
                </li>
              ))}
            </ul>
            <p className="muted owed-hint">Tap a bill to mark someone as paid.</p>
          </section>
        </div>
      )}
    </>
  );
}
