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

    await userEvent.click(screen.getByRole("button", { name: "Scan or add" }));

    expect(screen.getByRole("button", { name: /take a photo/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /upload a photo or pdf/i })).toBeInTheDocument();
  });

  it("is also the one place to add things by hand, each with why it's worth it", async () => {
    const onType = vi.fn();
    const onOwed = vi.fn();
    const onSubscription = vi.fn();
    render(<ScanButton onFile={vi.fn()} onType={onType} onOwed={onOwed} onSubscription={onSubscription} />);

    await userEvent.click(screen.getByRole("button", { name: "Scan or add" }));
    const type = screen.getByRole("button", { name: /type it in/i });
    expect(type).toHaveTextContent(/no photo/i);
    expect(screen.getByRole("button", { name: /someone owes me money/i })).toHaveTextContent(/until they pay you back/i);
    expect(screen.getByRole("button", { name: /subscription or bill/i })).toHaveTextContent(/before it renews/i);

    await userEvent.click(type);
    expect(onType).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Scan or add" }));
    await userEvent.click(screen.getByRole("button", { name: /someone owes me money/i }));
    await userEvent.click(screen.getByRole("button", { name: "Scan or add" }));
    await userEvent.click(screen.getByRole("button", { name: /subscription or bill/i }));
    expect(onOwed).toHaveBeenCalled();
    expect(onSubscription).toHaveBeenCalled();
  });

  it("offers only photo and upload where there's nothing else to add (a trip's own button)", async () => {
    render(<ScanButton onFile={vi.fn()} label="Scan for trip" inline />);

    await userEvent.click(screen.getByRole("button", { name: "Scan for trip" }));

    expect(screen.queryByRole("button", { name: /type it in/i })).not.toBeInTheDocument();
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
