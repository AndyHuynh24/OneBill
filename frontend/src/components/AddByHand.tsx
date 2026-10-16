import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { OneBillApi } from "../api";
import { formatCents, parseDollars, todayIso } from "../format";

const MAX_ITEMS = 40;

function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <header className="sheet-header">
          <h2>{title}</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

/** Optional amount: "" → 0, otherwise dollars → cents (null when it isn't an amount). */
const optionalCents = (text: string) => (text.trim() ? parseDollars(text) : 0);

/** A receipt without a photo: store, date, items, optional tax and tip. The server computes the
 *  total too; the one shown here is only so you can check what you typed. */
export function TypeBill({ api, onClose, onSaved }: { api: OneBillApi; onClose: () => void; onSaved: (docId: string) => void }) {
  const queryClient = useQueryClient();
  const [vendor, setVendor] = useState("");
  const [date, setDate] = useState(todayIso());
  const [items, setItems] = useState([{ description: "", price: "" }]);
  const [tax, setTax] = useState("");
  const [tip, setTip] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (bill: Parameters<OneBillApi["createManualBill"]>[0]) => api.createManualBill(bill),
    onSuccess: (doc) => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      onSaved(doc.docId);
    },
  });

  const prices = items.map((i) => (i.price.trim() ? parseDollars(i.price) : null));
  const total = [...prices, optionalCents(tax), optionalCents(tip)].reduce<number>((sum, c) => sum + (c ?? 0), 0);
  const setItem = (index: number, change: Partial<(typeof items)[number]>) =>
    setItems((all) => all.map((item, i) => (i === index ? { ...item, ...change } : item)));

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!vendor.trim()) return setProblem("Add the store, or what it was for.");
    const filled = items.filter((i) => i.description.trim() || i.price.trim());
    if (!filled.length) return setProblem("Add at least one item.");
    const parsed = [];
    for (const item of filled) {
      const cents = item.price.trim() ? parseDollars(item.price) : null;
      if (!item.description.trim()) return setProblem("Every item needs a name.");
      if (!cents) return setProblem(`Add a price for ${item.description.trim()}, like 4.99.`);
      parsed.push({ description: item.description.trim(), amountCents: cents });
    }
    const taxCents = optionalCents(tax);
    const tipCents = optionalCents(tip);
    if (taxCents === null || tipCents === null) return setProblem("Enter tax and tip like 2.50 (or leave them empty).");
    setProblem(null);
    save.mutate({ vendor: vendor.trim(), date, items: parsed, taxCents, tipCents });
  }

  return (
    <Sheet title="Type in a receipt" onClose={onClose}>
      <form className="review-form" onSubmit={submit}>
        <label>
          Store or what it was for
          <input value={vendor} maxLength={80} placeholder="Farmers market, Dinner at Tam's…" onChange={(e) => setVendor(e.target.value)} />
        </label>
        <label>
          Date
          <input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} />
        </label>
        <fieldset className="typed-items">
          <legend>Items</legend>
          {items.map((item, i) => (
            <div key={i} className="typed-item">
              <input
                aria-label={`Item ${i + 1}`}
                value={item.description}
                maxLength={80}
                placeholder="Item"
                onChange={(e) => setItem(i, { description: e.target.value })}
              />
              <input
                aria-label={`Item ${i + 1} price`}
                inputMode="decimal"
                value={item.price}
                placeholder="0.00"
                onChange={(e) => setItem(i, { price: e.target.value })}
              />
              {items.length > 1 && (
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Remove item ${i + 1}`}
                  onClick={() => setItems((all) => all.filter((_, j) => j !== i))}
                >
                  ×
                </button>
              )}
            </div>
          ))}
          {items.length < MAX_ITEMS && (
            <button type="button" className="link-button" onClick={() => setItems((all) => [...all, { description: "", price: "" }])}>
              + Add another item
            </button>
          )}
        </fieldset>
        <div className="typed-extras">
          <label>
            Tax
            <input inputMode="decimal" value={tax} placeholder="Optional" onChange={(e) => setTax(e.target.value)} />
          </label>
          <label>
            Tip
            <input inputMode="decimal" value={tip} placeholder="Optional" onChange={(e) => setTip(e.target.value)} />
          </label>
        </div>
        <p className="typed-total">
          Total <strong>{formatCents(total, "USD")}</strong>
        </p>
        {(problem || save.error) && (
          <p role="alert" className="error">
            {problem ?? save.error?.message}
          </p>
        )}
        <div className="sheet-actions">
          <button type="submit" className="primary" disabled={save.isPending}>
            Save
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Sheet>
  );
}

/** Money you lent: who, how much, what for. It's tracked like a split bill until they pay. */
export function OweMe({ api, friends, onClose }: { api: OneBillApi; friends: string[]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [date, setDate] = useState(todayIso());
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (owed: Parameters<OneBillApi["createOwed"]>[0]) => api.createOwed(owed),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      onClose();
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return setProblem("Who owes you?");
    const cents = amount.trim() ? parseDollars(amount) : null;
    if (!cents) return setProblem("How much do they owe you? Like 50 or 12.50.");
    setProblem(null);
    save.mutate({ name: name.trim(), amountCents: cents, date, ...(note.trim() ? { note: note.trim() } : {}) });
  }

  return (
    <Sheet title="Someone owes me money" onClose={onClose}>
      <form className="review-form" onSubmit={submit}>
        <label>
          Who owes you
          <input value={name} maxLength={40} placeholder="Name" onChange={(e) => setName(e.target.value)} />
        </label>
        {friends.length > 0 && (
          <div className="chips" role="group" aria-label="Recent people">
            {friends.slice(0, 8).map((friend) => (
              <button
                key={friend}
                type="button"
                className="friend-chip"
                aria-pressed={friend === name}
                onClick={() => setName(friend)}
              >
                {friend}
              </button>
            ))}
          </div>
        )}
        <label>
          How much
          <input inputMode="decimal" value={amount} placeholder="0.00" onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label>
          What for
          <input value={note} maxLength={80} placeholder="Optional: concert ticket, rent share…" onChange={(e) => setNote(e.target.value)} />
        </label>
        <label>
          Date
          <input type="date" value={date} max={todayIso()} onChange={(e) => setDate(e.target.value)} />
        </label>
        {(problem || save.error) && (
          <p role="alert" className="error">
            {problem ?? save.error?.message}
          </p>
        )}
        <div className="sheet-actions">
          <button type="submit" className="primary" disabled={save.isPending}>
            Save
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Sheet>
  );
}
