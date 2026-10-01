import { useState } from "react";
import type { Friend } from "../api";
import { MAX_PEOPLE, parseNames, type Person } from "../split";
import { Avatar } from "./Avatar";

interface PeoplePickerProps {
  friends: Friend[];
  people: Person[];
  onChange: (people: Person[]) => void;
  onNext: (people: Person[]) => void;
  onForget: (friend: Friend) => void;
}

/** "Who's here?": Me, one-tap recent friends, and one box for new names (dictation works). */
export function PeoplePicker({ friends, people, onChange, onNext, onForget }: PeoplePickerProps) {
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  const has = (name: string) => people.some((p) => p.name.toLocaleLowerCase() === name.toLocaleLowerCase());
  const full = people.length >= MAX_PEOPLE;

  function add(names: string[]) {
    onChange([...people, ...names.map((name) => ({ name }))].slice(0, MAX_PEOPLE));
  }

  function toggle(name: string) {
    if (has(name)) onChange(people.filter((p) => p.self || p.name.toLocaleLowerCase() !== name.toLocaleLowerCase()));
    else if (!full) add([name]);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    add(parseNames(text, people.map((p) => p.name)));
    setText("");
  }

  return (
    <div className="people-picker">
      <h2>Who's here?</h2>

      {friends.length > 0 && (
        <div className="sheet-section">
          <div className="section-header">
            <h3>Recent</h3>
            <button type="button" className="link-button" onClick={() => setEditing((e) => !e)}>
              {editing ? "Done" : "Edit"}
            </button>
          </div>
          <div className="chips">
            {friends.map((friend) =>
              editing ? (
                <button
                  key={friend.friendId}
                  type="button"
                  className="friend-chip forget"
                  aria-label={`Forget ${friend.name}`}
                  onClick={() => onForget(friend)}
                >
                  {friend.name} ×
                </button>
              ) : (
                <button
                  key={friend.friendId}
                  type="button"
                  className="friend-chip"
                  aria-pressed={has(friend.name)}
                  onClick={() => toggle(friend.name)}
                >
                  {friend.name}
                </button>
              ),
            )}
          </div>
        </div>
      )}

      <form className="add-names" onSubmit={submit}>
        <label htmlFor="add-names">Add names</label>
        <div className="add-names-row">
          <input
            id="add-names"
            value={text}
            placeholder='e.g. "Tam and Vy"'
            autoComplete="off"
            enterKeyHint="done"
            disabled={full}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" disabled={full || !text.trim()}>
            Add
          </button>
        </div>
      </form>

      <ul className="seats" aria-label="In this split">
        {people.map((person, seat) => (
          <li key={person.name} className="seat">
            <Avatar name={person.name} seat={seat} size="sm" />
            <span>{person.name}</span>
            {!person.self && (
              <button
                type="button"
                className="icon-button"
                aria-label={`Remove ${person.name}`}
                onClick={() => onChange(people.filter((_, i) => i !== seat))}
              >
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
      {full && (
        <p role="status" className="muted">
          A split can have up to {MAX_PEOPLE} people.
        </p>
      )}

      <div className="sheet-actions">
        <button type="button" className="primary" disabled={people.length < 2} onClick={() => onNext(people)}>
          Next
        </button>
      </div>
    </div>
  );
}
