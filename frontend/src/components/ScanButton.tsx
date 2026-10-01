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
        <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22">
          <path
            fill="currentColor"
            d="M9 3 7.2 5H4a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3.2L15 3H9Zm3 5a5 5 0 1 1 0 10 5 5 0 0 1 0-10Zm0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"
          />
        </svg>
        {busy ? "Uploading…" : "Scan"}
      </button>
    </div>
  );
}
