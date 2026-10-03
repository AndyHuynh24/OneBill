import { useRef } from "react";

export const ACCEPTED_TYPES = "image/jpeg,image/png,application/pdf";

interface ScanButtonProps {
  onFile: (file: File) => void;
  busy?: boolean;
}

/**
 * The app's one primary action. A file input *without* `capture`, so phones offer
 * Take Photo / Photo Library / Choose File, and desktops open the file picker.
 */
export function ScanButton({ onFile, busy = false }: ScanButtonProps) {
  const input = useRef<HTMLInputElement>(null);

  return (
    <div className="scan-bar">
      <input
        ref={input}
        id="scan-input"
        type="file"
        accept={ACCEPTED_TYPES}
        className="visually-hidden"
        aria-label="Scan or upload a receipt or bill"
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
          event.target.value = ""; // allow picking the same file again
        }}
      />
      <button
        type="button"
        className="scan-button"
        disabled={busy}
        onClick={() => input.current?.click()}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          width="24"
          height="24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.6l1.4-2h5l1.4 2h1.6A2.5 2.5 0 0 1 20 8.5v8a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5z" />
          <circle cx="12" cy="12.5" r="3.5" />
        </svg>
        <span>{busy ? "Uploading…" : "Scan receipt"}</span>
      </button>
    </div>
  );
}
