import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ScanButton } from "./ScanButton";

describe("ScanButton", () => {
  it("opens the back camera for Scan, and offers Upload for photos and PDFs", () => {
    render(<ScanButton onFile={vi.fn()} />);

    expect(screen.getByLabelText(/scan a receipt with the camera/i)).toHaveAttribute(
      "capture",
      "environment",
    );
    const upload = screen.getByLabelText(/upload a photo or pdf/i);
    expect(upload).toHaveAttribute("accept", "image/jpeg,image/png,application/pdf");
    expect(upload).not.toHaveAttribute("capture"); // library, screenshots, and files
  });

  it("asks camera or upload when Scan is tapped", async () => {
    render(<ScanButton onFile={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /scan receipt/i }));

    expect(screen.getByRole("button", { name: /take a photo/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload a photo or pdf/i })).toBeInTheDocument();
  });

  it("hands the chosen file to onFile", async () => {
    const onFile = vi.fn();
    render(<ScanButton onFile={onFile} />);
    const file = new File(["jpeg"], "receipt.jpg", { type: "image/jpeg" });

    await userEvent.upload(screen.getByLabelText(/scan a receipt with the camera/i), file);

    expect(onFile).toHaveBeenCalledWith(file);
  });

  it("is disabled while an upload is in progress", () => {
    render(<ScanButton onFile={vi.fn()} busy />);

    expect(screen.getByRole("button", { name: /uploading/i })).toBeDisabled();
  });
});
