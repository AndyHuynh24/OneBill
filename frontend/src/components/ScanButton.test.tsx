import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ScanButton } from "./ScanButton";

describe("ScanButton", () => {
  it("accepts photos and PDFs, without forcing the camera", () => {
    render(<ScanButton onFile={vi.fn()} />);

    const input = screen.getByLabelText(/scan or upload/i);
    expect(input).toHaveAttribute("accept", "image/jpeg,image/png,application/pdf");
    expect(input).not.toHaveAttribute("capture"); // lets phones offer camera OR library
  });

  it("hands the chosen file to onFile", async () => {
    const onFile = vi.fn();
    render(<ScanButton onFile={onFile} />);
    const file = new File(["jpeg"], "receipt.jpg", { type: "image/jpeg" });

    await userEvent.upload(screen.getByLabelText(/scan or upload/i), file);

    expect(onFile).toHaveBeenCalledWith(file);
  });

  it("is disabled while an upload is in progress", () => {
    render(<ScanButton onFile={vi.fn()} busy />);

    expect(screen.getByRole("button", { name: /uploading/i })).toBeDisabled();
  });
});
