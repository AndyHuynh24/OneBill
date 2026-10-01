import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { forgetFriend, loadFriends, rememberFriends } from "../friends";
import { useState } from "react";
import type { DocumentDetail, OneTallyApi, Split } from "../api";
import type { Person } from "../split";
import { AssignCircle, type Assignments } from "./AssignCircle";
import { ChargeStep, type Charges } from "./ChargeStep";
import { PeoplePicker } from "./PeoplePicker";
import { SplitSummary } from "./SplitSummary";

type Step = "people" | "assign" | "charges" | "summary";
const CHARGE_KINDS = new Set(["tax", "fee", "tip"]);

interface SplitFlowProps {
  api: OneTallyApi;
  doc: DocumentDetail;
  onCancel: () => void;
  onClose: () => void;
}

/** Split a bill: who's here → assign items → tax/fees/tip → summary (saved on the server). */
export function SplitFlow({ api, doc, onCancel, onClose }: SplitFlowProps) {
  const queryClient = useQueryClient();
  const restored = restore(doc.split);
  const [step, setStep] = useState<Step>(doc.split ? "summary" : "people");
  const [people, setPeople] = useState<Person[]>(restored.people);
  const [assignments, setAssignments] = useState<Assignments>(restored.assignments);
  const [charges, setCharges] = useState<Charges>(restored.charges);
  const [saved, setSaved] = useState<Split | undefined>(doc.split);

  const lines = doc.lines ?? [];
  const chargeLines = lines.filter((l) => CHARGE_KINDS.has(l.kind));
  // Recent friends live only in this browser, per account.
  const profile = useQuery({ queryKey: ["me"], queryFn: api.getProfile });
  const userId = profile.data?.userId;
  const [, refreshFriends] = useState(0);
  const friends = userId ? loadFriends(userId) : [];
  const save = useMutation({
    mutationFn: (request: { assignments: Assignments; charges: Charges }) =>
      api.saveSplit(doc.docId, {
        members: people.map((p) => (p.self ? { name: p.name, self: true } : { name: p.name })),
        assignments: request.assignments,
        charges: request.charges,
        expectedVersion: saved?.version ?? 0,
        expectedUpdatedAt: saved?.updatedAt ?? doc.updatedAt ?? "",
      }),
    onSuccess: (split) => {
      setSaved(split);
      setStep("summary");
      queryClient.invalidateQueries({ queryKey: ["document", doc.docId] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      if (userId) rememberFriends(userId, people.filter((p) => !p.self).map((p) => p.name));
    },
  });

  function choosePeople(chosen: Person[]) {
    // Seats changed: earlier choices may point at the wrong people.
    if (JSON.stringify(chosen) !== JSON.stringify(people)) {
      setAssignments({});
      setCharges({});
    }
    setPeople(chosen);
    setStep("assign");
  }

  function itemsDone(done: Assignments) {
    setAssignments(done);
    if (chargeLines.length) setStep("charges");
    else save.mutate({ assignments: done, charges: {} });
  }

  return (
    <div className="split-flow">
      {step === "people" && (
        <PeoplePicker
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
        <AssignCircle
          people={people}
          lines={lines}
          currency={doc.currency ?? "USD"}
          initial={assignments}
          onDone={itemsDone}
        />
      )}
      {step === "charges" && (
        <ChargeStep
          people={people}
          lines={chargeLines}
          currency={doc.currency ?? "USD"}
          initial={charges}
          onDone={(done) => {
            setCharges(done);
            save.mutate({ assignments, charges: done });
          }}
        />
      )}
      {step === "summary" && saved && (
        <SplitSummary split={saved} doc={doc} onEdit={() => setStep("assign")} onDone={onClose} />
      )}

      {save.isPending && <p className="muted">Saving…</p>}
      {save.error && (
        <p role="alert" className="error">
          {save.error.message}
        </p>
      )}
      {step !== "summary" && (
        <div className="sheet-actions">
          {step !== "people" && (
            <button type="button" onClick={() => setStep(step === "charges" ? "assign" : "people")}>
              Back
            </button>
          )}
          <button type="button" className="link-button" onClick={onCancel}>
            Cancel split
          </button>
        </div>
      )}
    </div>
  );
}

/** A saved split back into screen state: people by seat, choices by seat index. */
function restore(split: Split | undefined): { people: Person[]; assignments: Assignments; charges: Charges } {
  if (!split) return { people: [{ name: "Me", self: true }], assignments: {}, charges: {} };
  const seat = new Map(split.members.map((m, i) => [m.id, i]));
  const people = split.members.map((m) => (m.self ? { name: m.name, self: true } : { name: m.name }));
  const assignments = Object.fromEntries(
    Object.entries(split.assignments).map(([row, ids]) => [row, ids.map((id) => seat.get(id) ?? 0)]),
  );
  const charges = Object.fromEntries(
    Object.entries(split.charges).map(([row, c]) => [
      row,
      c.mode === "person" ? { mode: "person" as const, member: seat.get(c.member ?? "") ?? 0 } : { mode: c.mode as "even" | "proportional" },
    ]),
  );
  return { people, assignments, charges };
}
