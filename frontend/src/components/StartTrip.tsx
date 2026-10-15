import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { OneBillApi } from "../api";
import { forgetFriend, loadFriends } from "../friends";
import type { Person } from "../split";
import { PeoplePicker } from "./PeoplePicker";

interface StartTripProps {
  api: OneBillApi;
  onClose: () => void;
}

/** Name the trip and who's going (the same picker as splitting); every scan then joins it. */
export function StartTrip({ api, onClose }: StartTripProps) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [people, setPeople] = useState<Person[]>([{ name: "Me", self: true }]);
  const [missingName, setMissingName] = useState(false);
  const profile = useQuery({ queryKey: ["me"], queryFn: api.getProfile });
  const userId = profile.data?.userId;
  const [, refreshFriends] = useState(0);
  const friends = userId ? loadFriends(userId) : [];

  const start = useMutation({
    mutationFn: (members: Person[]) =>
      api.startTrip({
        name: name.trim(),
        members: members.map((p) => (p.self ? { name: p.name, self: true } : { name: p.name })),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["trips"] });
      onClose();
    },
  });

  function next(members: Person[]) {
    if (!name.trim()) {
      setMissingName(true);
      return;
    }
    start.mutate(members);
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet" role="dialog" aria-modal="true" aria-labelledby="start-trip-title" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-header">
          <h2 id="start-trip-title">Start a trip</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="review-form">
          <label>
            Trip name
            <input
              value={name}
              maxLength={60}
              placeholder="e.g. Chicago"
              onChange={(e) => {
                setName(e.target.value);
                setMissingName(false);
              }}
            />
          </label>
        </div>
        <PeoplePicker
          title="Who's going?"
          friends={friends}
          people={people}
          onChange={setPeople}
          onNext={next}
          onForget={(friend) => {
            if (userId) forgetFriend(userId, friend.name);
            refreshFriends((n) => n + 1);
          }}
        />
        {(missingName || start.error) && (
          <p role="alert" className="error">
            {missingName ? "Give the trip a name first." : start.error?.message}
          </p>
        )}
      </section>
    </div>
  );
}
