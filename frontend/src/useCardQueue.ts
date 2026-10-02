import { useState } from "react";

/**
 * One card at a time, for the split screens (items, then tax/fees/tip).
 * - Choosing for the last open card finishes (calls onDone).
 * - Re-choosing a card that already had a choice (editing) just moves on; `complete` then
 *   shows a Next button instead of finishing behind the user's back.
 */
export function useCardQueue<T>(keys: string[], initial: Record<string, T>, onDone: (values: Record<string, T>) => void) {
  const [values, setValues] = useState<Record<string, T>>(initial);
  const [current, setCurrent] = useState(() => nextOpen(keys, initial, 0) ?? 0);
  const [history, setHistory] = useState<number[]>([]);
  const key = keys[current];

  function choose(value: T) {
    if (key === undefined) return;
    const wasOpen = values[key] === undefined;
    const next = { ...values, [key]: value };
    setValues(next);
    setHistory((h) => [...h, current]);
    const open = nextOpen(keys, next, current + 1);
    if (open !== undefined) setCurrent(open);
    else if (wasOpen) onDone(next);
    else setCurrent((current + 1) % keys.length);
  }

  function undo() {
    const previous = history.at(-1);
    if (previous === undefined) return;
    const rest = { ...values };
    delete rest[keys[previous]];
    setValues(rest);
    setHistory((h) => h.slice(0, -1));
    setCurrent(previous);
  }

  return {
    key,
    current,
    values,
    choose,
    undo,
    canUndo: history.length > 0,
    jump: setCurrent,
    complete: keys.every((k) => values[k] !== undefined),
    finish: () => onDone(values),
  };
}

function nextOpen<T>(keys: string[], values: Record<string, T>, from: number): number | undefined {
  for (let step = 0; step < keys.length; step++) {
    const i = (from + step) % keys.length;
    if (values[keys[i]] === undefined) return i;
  }
  return undefined;
}
