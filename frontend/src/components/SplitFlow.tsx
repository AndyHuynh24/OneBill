import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { forgetFriend, loadFriends, rememberFriends } from "../friends";
import { useState } from "react";
import { useBackLayer } from "../backButton";
import type { ChargeChoice, DocumentDetail, OneBillApi, Split } from "../api";
import type { Person } from "../split";
import { type Assignments, type Picks, SplitList } from "./SplitList";
import { PeoplePicker } from "./PeoplePicker";
import { SplitSummary } from "./SplitSummary";

type Step = "people" | "assign" | "summary";
type Charges = Record<string, ChargeChoice>;
const CHARGE_KINDS = new Set(["tax", "fee", "tip"]);

interface SplitFlowProps {
  api: OneBillApi;
  doc: DocumentDetail;
  /** A trip receipt: its people, to start a new split with. */
  tripPeople?: Person[];
  onCancel: () => void;
  onClose: () => void;
}

/** Split a bill: who's here → who had what (one list) → summary (saved). */
export function SplitFlow({ api, doc, tripPeople, onCancel, onClose }: SplitFlowProps) {
  const queryClient = useQueryClient();
  const restored = restore(doc.split);
  if (!doc.split && tripPeople?.length) restored.people = tripPeople;
  const inTrip = !!doc.tripId;
  // The steps taken, so the phone's Back retraces them (going to an earlier step trims back to it).
  const [trail, setTrail] = useState<Step[]>([doc.split ? "summary" : "people"]);
  const step = trail[trail.length - 1];
  const setStep = (next: Step) =>
    setTrail((t) => (t.includes(next) ? t.slice(0, t.indexOf(next) + 1) : [...t, next]));
  useBackLayer(trail.length > 1, () => setTrail((t) => t.slice(0, -1)), trail.length);
  const [people, setPeople] = useState<Person[]>(restored.people);
  const [picks, setPicks] = useState<Picks>(restored.picks);
  const [saved, setSaved] = useState<Split | undefined>(doc.split);
  // Who paid the bill (a seat); only asked on trips, where friends pay too.
  const [paidBy, setPaidBy] = useState(() => {
    const seat = doc.split?.members.findIndex((m) => m.id === doc.split?.paidBy) ?? -1;
    return seat >= 0 ? seat : Math.max(0, restored.people.findIndex((p) => p.self));
  });
  // The bill's version as loaded (not the split's save time): edits to the bill after a split
  // was saved (e.g. Looks right) must not make every later save look like a conflict.
  const [docVersion, setDocVersion] = useState(doc.updatedAt ?? "");

  const lines = doc.lines ?? [];
  // Tax-inclusive prices: the tax is already inside each item, so there's nothing to assign.
  const chargeLines = lines.filter((l) => CHARGE_KINDS.has(l.kind) && !(doc.taxIncluded && l.kind === "tax"));
  // Recent friends live only in this browser, per account.
  const profile = useQuery({ queryKey: ["me"], queryFn: api.getProfile });
  const userId = profile.data?.userId;
  const [, refreshFriends] = useState(0);
  const friends = userId ? loadFriends(userId) : [];
  const save = useMutation({
    mutationFn: (request: { assignments: Assignments; charges: Charges; paidBy?: number }) =>
      api.saveSplit(doc.docId, {
        members: people.map((p) => (p.self ? { name: p.name, self: true } : { name: p.name })),
        assignments: request.assignments,
        charges: request.charges,
        expectedVersion: saved?.version ?? 0,
        expectedUpdatedAt: docVersion,
        ...(inTrip ? { paidBy: request.paidBy ?? Math.min(paidBy, people.length - 1) } : {}),
      }),
    onSuccess: (split) => {
      setSaved(split);
      if (split.updatedAt) setDocVersion(split.updatedAt);
      setStep("summary");
      queryClient.invalidateQueries({ queryKey: ["document", doc.docId] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      if (inTrip) queryClient.invalidateQueries({ queryKey: ["trip"] });
      if (userId) rememberFriends(userId, people.filter((p) => !p.self).map((p) => p.name));
    },
  });

  // A tick shows at once; if it can't be saved, it's undone and the reason shown.
  const markPaid = useMutation({
    mutationFn: ({ member, paid }: { member: string; paid: boolean }) =>
      api.setSplitPaid(doc.docId, { member, paid, expectedVersion: saved?.version ?? 0 }),
    onMutate: ({ member, paid }) => {
      const before = saved;
      setSaved((s) => s && { ...s, paid: withPaid(s.paid, member, paid) });
      return { before };
    },
    onError: (_err, _vars, context) => setSaved(context?.before),
    onSuccess: (split) => {
      setSaved(split);
      queryClient.invalidateQueries({ queryKey: ["document", doc.docId] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  function choosePeople(chosen: Person[]) {
    // Only people added at the end: everyone keeps their seat, so earlier choices still hold.
    // Anything else (removed or reordered): earlier choices may point at the wrong people.
    const kept = people.every((p, i) => chosen[i]?.name === p.name);
    if (!kept) setPicks({});
    setPeople(chosen);
    setStep("assign");
  }

  /** Items become assignments, tax/tip/fee picks become charge choices. */
  function toRequest(done: Picks): { assignments: Assignments; charges: Charges } {
    const chargeRows = new Set(chargeLines.map((l) => String(l.row)));
    const assignments: Assignments = {};
    const charges: Charges = {};
    for (const [row, pick] of Object.entries(done)) {
      if (chargeRows.has(row)) charges[row] = toCharge(pick, people.length);
      else if (Array.isArray(pick)) assignments[row] = pick;
    }
    return { assignments, charges };
  }

  /** Every line chosen: save the split. */
  function allChosen(done: Picks) {
    setPicks(done);
    save.mutate(toRequest(done));
  }

  /** A trip friend paid instead: same split, saved again with the new payer. */
  function choosePaidBy(seat: number) {
    setPaidBy(seat);
    save.mutate({ ...toRequest(picks), paidBy: seat });
  }

  return (
    <div className="split-flow">
      {step === "people" && (
        <PeoplePicker
          currency={doc.currency ?? "USD"}
          friends={friends}
          people={people}
          onChange={setPeople}
          onNext={choosePeople}
          onForget={(friend) => {
            if (userId) forgetFriend(userId, friend.name);
            refreshFriends((n) => n + 1);
          }}
        />
      )}
      {step === "assign" && (
        <SplitList
          people={people}
          lines={lines}
          chargeLines={chargeLines}
          currency={doc.currency ?? "USD"}
          initial={picks}
          onDone={allChosen}
          onEditPeople={() => setStep("people")}
        />
      )}
      {step === "summary" && saved && (
        <SplitSummary
          split={saved}
          doc={doc}
          onEdit={() => setStep("assign")}
          onAddPeople={() => setStep("people")}
          onTogglePaid={inTrip ? undefined : (member, paid) => markPaid.mutate({ member, paid })}
          paidBySeat={inTrip ? paidBy : undefined}
          onPaidBy={inTrip ? choosePaidBy : undefined}
          onDone={onClose}
        />
      )}

      {save.isPending && <p className="muted">Saving…</p>}
      {(save.error ?? markPaid.error) && (
        <p role="alert" className="error">
          {(save.error ?? markPaid.error)?.message}
        </p>
      )}
      {step === "people" && (
        <button type="button" className="link-button split-cancel" onClick={onCancel}>
          Cancel split
        </button>
      )}
    </div>
  );
}

/** A saved split back into screen state: people by seat, one pick per line by seat index. */
function restore(split: Split | undefined): { people: Person[]; picks: Picks } {
  if (!split) return { people: [{ name: "Me", self: true }], picks: {} };
  const seat = new Map(split.members.map((m, i) => [m.id, i]));
  const people = split.members.map((m) => (m.self ? { name: m.name, self: true } : { name: m.name }));
  const all = split.members.map((_, i) => i);
  const picks: Picks = Object.fromEntries(
    Object.entries(split.assignments).map(([row, ids]) => [row, ids.map((id) => seat.get(id) ?? 0)]),
  );
  for (const [row, c] of Object.entries(split.charges)) {
    picks[row] =
      c.mode === "proportional"
        ? "byOrder"
        : c.mode === "even"
          ? all
          : c.mode === "person"
            ? [seat.get(c.member ?? "") ?? 0]
            : (c.members ?? []).map((id) => seat.get(id) ?? 0);
  }
  return { people, picks };
}

/** A tax/tip/fee pick as the API's charge choice. */
function toCharge(pick: Picks[string], count: number): ChargeChoice {
  if (pick === "byOrder") return { mode: "proportional" };
  if (pick.length === count) return { mode: "even" };
  if (pick.length === 1) return { mode: "person", member: pick[0] };
  return { mode: "people", members: pick };
}

function withPaid(paid: Record<string, string> | undefined, member: string, on: boolean): Record<string, string> {
  const next = { ...paid };
  if (on) next[member] ??= new Date().toISOString();
  else delete next[member];
  return next;
}
