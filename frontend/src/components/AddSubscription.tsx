import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { BillingCycle, NewSubscription, OneTallyApi } from "../api";
import { parseDollars } from "../format";

const CYCLES: [BillingCycle, string][] = [
  ["monthly", "Monthly"],
  ["weekly", "Weekly"],
  ["quarterly", "Every 3 months"],
  ["yearly", "Yearly"],
];

/** + Add: a subscription or bill that doesn't email (rent, utilities, a gym billed to a card). */
export function AddSubscription({ api, onClose }: { api: OneTallyApi; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<"subscription" | "bill">("subscription");
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [cycle, setCycle] = useState<BillingCycle>("monthly");
  const [due, setDue] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const add = useMutation({
    mutationFn: (subscription: NewSubscription) => api.addSubscription(subscription),
    onSuccess: () => {
      for (const key of [["subscriptions"], ["obligations"]]) queryClient.invalidateQueries({ queryKey: key });
      onClose();
    },
  });
  const bill = category === "bill";

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return setProblem("Give it a name, like Rent or Electricity.");
    let amountCents: number | undefined;
    if (amount.trim()) {
      const cents = parseDollars(amount);
      if (cents === null) return setProblem("Enter the amount like 2100 or 89.50 (or leave it empty).");
      amountCents = cents;
    }
    if (!due) return setProblem(bill ? "Choose the next due date." : "Choose the next renewal date.");
    setProblem(null);
    add.mutate({
      service: name.trim(),
      category,
      cycle,
      nextRenewal: due,
      ...(amountCents !== undefined ? { amountCents } : {}),
    });
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet" role="dialog" aria-modal="true" aria-labelledby="add-title" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-header">
          <h2 id="add-title">Add a subscription or bill</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <form className="review-form" onSubmit={submit}>
          <fieldset className="segmented">
            <legend className="visually-hidden">Type</legend>
            {(["subscription", "bill"] as const).map((value) => (
              <label key={value}>
                <input type="radio" name="category" checked={category === value} onChange={() => setCategory(value)} />
                {value === "bill" ? "Bill" : "Subscription"}
              </label>
            ))}
          </fieldset>
          <label>
            Name
            <input value={name} maxLength={80} placeholder={bill ? "Rent, Electricity…" : "Gym, Costco…"} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Amount {bill ? "(an estimate is fine)" : ""}
            <input inputMode="decimal" value={amount} placeholder="Optional" onChange={(e) => setAmount(e.target.value)} />
          </label>
          <label>
            How often
            <select value={cycle} onChange={(e) => setCycle(e.target.value as BillingCycle)}>
              {CYCLES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            {bill ? "Next due date" : "Next renewal"}
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
          {(problem || add.error) && (
            <p role="alert" className="error">
              {problem ?? add.error?.message}
            </p>
          )}
          <div className="sheet-actions">
            <button type="submit" className="primary" disabled={add.isPending}>
              Add
            </button>
            <button type="button" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
