import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { MoneyInput } from "./MoneyInput";

function Field({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return <MoneyInput aria-label="Amount" value={value} onChange={setValue} />;
}

describe("MoneyInput", () => {
  it("asks phones for the digits-only keypad", () => {
    render(<Field />);
    expect(screen.getByLabelText("Amount")).toHaveAttribute(
      "inputmode",
      "numeric",
    );
  });

  it("fills digits from the right, so no decimal point is typed", async () => {
    render(<Field />);
    const input = screen.getByLabelText("Amount");
    await userEvent.type(input, "1250");
    expect(input).toHaveValue("12.50");
  });

  it("backspace drops the last digit", async () => {
    render(<Field initial="12.50" />);
    const input = screen.getByLabelText("Amount");
    await userEvent.type(input, "{Backspace}");
    expect(input).toHaveValue("1.25");
  });

  it("ignores a typed point or comma", async () => {
    render(<Field />);
    const input = screen.getByLabelText("Amount");
    await userEvent.type(input, "26.81");
    expect(input).toHaveValue("26.81");
  });

  it("reads a pasted amount as written", async () => {
    render(<Field />);
    const input = screen.getByLabelText("Amount");
    await userEvent.click(input);
    await userEvent.paste("$1,234.5");
    expect(input).toHaveValue("1234.50");
  });

  it("falls back to digits for a paste it can't read", async () => {
    render(<Field />);
    const input = screen.getByLabelText("Amount");
    await userEvent.click(input);
    await userEvent.paste("about 12");
    expect(input).toHaveValue("0.12");
  });
});
