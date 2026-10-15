import { useState } from "react";
import { formatCents } from "../format";
import type { Friend } from "../friends";
import { MAX_PEOPLE, parseNames, type Person } from "../split";
import { Avatar } from "./Avatar";

interface PeoplePickerProps {
  /** What's being split, shown big at the top. */
  totalCents?: number | null;
  currency?: string;
  vendor?: string | null;
  friends: Friend[];
  people: Person[];
  onChange: (people: Person[]) => void;
  onNext: (people: Person[]) => void;
  onForget: (friend: Friend) => void;
  /** The question at the top (a trip asks "Who's going?"). */
  title?: string;
}

/** "Who's here?": Me, one-tap recent friends, and one box for names separated by spaces. */
export function PeoplePicker({
  totalCents,
  currency = "USD",
  vendor,
  friends,
  people,
  onChange,
  onNext,
  onForget,
  title = "Who's here?",
}: PeoplePickerProps) {
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

  // Names typed but not added yet still count: Continue adds them.
  const pending = parseNames(text, people.map((p) => p.name));
  const everyone = [...people, ...pending.map((name) => ({ name }))].slice(0, MAX_PEOPLE);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    add(pending);
    setText("");
  }

  function next() {
    onChange(everyone);
    setText("");
    onNext(everyone);
  }

  return (
    <div className="people-picker">
      {totalCents != null && (
        <div className="split-total">
          <span className="split-total-label">Splitting</span>
          <strong className="split-total-amount">{formatCents(totalCents, currency)}</strong>
          {vendor && <span className="split-total-vendor">{vendor}</span>}
        </div>
      )}

      <h2>{title}</h2>

      <ul className="seats" aria-label="In this split">
        {people.map((person, seat) => (
          <li key={person.name} className="seat">
            <Avatar name={person.name} seat={seat} size="sm" />
            <span>{person.name}</span>
            {!person.self && (
              <button
                type="button"
                className="icon-button seat-remove"
                aria-label={`Remove ${person.name}`}
                onClick={() => onChange(people.filter((_, i) => i !== seat))}
              >
                ×
              </button>
            )}
          </li>
        ))}
      </ul>

      <form className="add-names" onSubmit={submit}>
        <label htmlFor="add-names" className="visually-hidden">
          Add names
        </label>
        <div className="add-names-row">
          <input
            id="add-names"
            value={text}
            placeholder="Type names, e.g. Sam, Jack, Maria"
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

      {friends.length > 0 && (
        <div className="recent-friends">
          <div className="recent-friends-head">
            <span className="muted">Recent</span>
            <button type="button" className="link-button" onClick={() => setEditing((e) => !e)}>
              {editing ? "Done" : "Edit"}
            </button>
          </div>
          <div className="chips">
            {friends.map((friend) =>
              editing ? (
                <button
                  key={friend.name}
                  type="button"
                  className="friend-chip forget"
                  aria-label={`Forget ${friend.name}`}
                  onClick={() => onForget(friend)}
                >
                  {friend.name} ×
                </button>
              ) : (
                <button
                  key={friend.name}
                  type="button"
                  className="friend-chip"
                  aria-label={friend.name}
                  aria-pressed={has(friend.name)}
                  onClick={() => toggle(friend.name)}
                >
                  <span aria-hidden="true">{has(friend.name) ? "✓ " : "+ "}</span>
                  {friend.name}
                </button>
              ),
            )}
          </div>
        </div>
      )}

      {full && (
        <p role="status" className="muted">
          A split can have up to {MAX_PEOPLE} people.
        </p>
      )}

      <button type="button" className="btn-block btn-split" disabled={everyone.length < 2} onClick={next}>
        {everyone.length < 2 ? "Continue" : `Continue with ${everyone.length} people`}
      </button>
    </div>
  );
}
