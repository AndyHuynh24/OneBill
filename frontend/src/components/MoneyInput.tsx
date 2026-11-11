import type { InputHTMLAttributes } from "react";
import { centsToInput, parseDollars, typedAmount } from "../format";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type" | "inputMode"> & {
  /** "12.50", or "" when empty. */
  value: string;
  onChange: (value: string) => void;
};

/**
 * An amount field typed like a bank app: digits only, filling from the right (1 → 0.01,
 * 1250 → 12.50). Phones show the plain 0–9 keypad, so the region's decimal key never matters.
 */
export function MoneyInput({ value, onChange, ...rest }: Props) {
  return (
    <input
      {...rest}
      type="text"
      inputMode="numeric"
      autoComplete="off"
      value={value}
      onChange={(e) => onChange(typedAmount(e.target.value, value))}
      onPaste={(e) => {
        // A pasted "$26.81" means $26.81, not digit-by-digit entry.
        const cents = parseDollars(e.clipboardData.getData("text"));
        if (cents === null) return; // let onChange read the digits
        e.preventDefault();
        onChange(cents ? centsToInput(cents) : "");
      }}
    />
  );
}
