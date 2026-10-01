import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { DocumentDetail, OneTallyApi } from "../api";
import type { Person } from "../split";
import { PeoplePicker } from "./PeoplePicker";

type Step = "people" | "assign";

interface SplitFlowProps {
  api: OneTallyApi;
  doc: DocumentDetail;
  onCancel: () => void;
}

/** Split a bill: who's here → assign items → tax/fees/tip → summary. */
export function SplitFlow({ api, doc, onCancel }: SplitFlowProps) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<Step>("people");
  const [people, setPeople] = useState<Person[]>([{ name: "Me", self: true }]);
  const friends = useQuery({ queryKey: ["friends"], queryFn: api.listFriends });
  const forget = useMutation({
    mutationFn: (friendId: string) => api.deleteFriend(friendId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["friends"] }),
  });

  return (
    <div className="split-flow" data-doc={doc.docId}>
      {step === "people" && (
        <PeoplePicker
          friends={friends.data?.friends ?? []}
          people={people}
          onChange={setPeople}
          onNext={(chosen) => {
            setPeople(chosen);
            setStep("assign");
          }}
          onForget={(friend) => forget.mutate(friend.friendId)}
        />
      )}
      <div className="sheet-actions">
        <button type="button" className="link-button" onClick={onCancel}>
          Cancel split
        </button>
      </div>
    </div>
  );
}
