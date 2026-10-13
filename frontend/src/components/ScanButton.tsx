import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useBackLayer } from "../backButton";

export const ACCEPTED_TYPES = "image/jpeg,image/png,application/pdf";

interface ScanButtonProps {
  onFile: (file: File) => void;
  busy?: boolean;
}

/**
 * The app's one primary action. Scan asks how: Take a photo opens the back camera
 * (`capture="environment"`), Upload picks a photo, screenshot, or PDF. A phone's own menu can't
 * choose the camera, so the app shows the choice itself.
 */
export function ScanButton({ onFile, busy = false }: ScanButtonProps) {
  const camera = useRef<HTMLInputElement>(null);
  const upload = useRef<HTMLInputElement>(null);
  const [choosing, setChoosing] = useState(false);
  useBackLayer(choosing, () => setChoosing(false));

  const pick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) onFile(file);
    event.target.value = ""; // allow picking the same file again
  };
  // Open the picker inside the tap itself (browsers require that), then close the menu.
  const open = (input: HTMLInputElement | null) => {
    input?.click();
    setChoosing(false);
  };

  return (
    <div className="scan-bar">
      <input
        ref={camera}
        id="scan-input"
        type="file"
        accept="image/*"
        capture="environment"
        className="visually-hidden"
        aria-label="Scan a receipt with the camera"
        disabled={busy}
        onChange={pick}
      />
      <input
        ref={upload}
        type="file"
        accept={ACCEPTED_TYPES}
        className="visually-hidden"
        aria-label="Upload a photo or PDF"
        disabled={busy}
        onChange={pick}
      />
      <button
        type="button"
        className="scan-button"
        disabled={busy}
        aria-haspopup="dialog"
        onClick={() => setChoosing(true)}
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

      {choosing &&
        createPortal(
          <div className="picker-backdrop" onClick={() => setChoosing(false)}>
            <div
              className="picker scan-choice"
              role="dialog"
              aria-modal="true"
              aria-label="Add a receipt"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                type="button"
                className="scan-option"
                onClick={() => open(camera.current)}
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  width="26"
                  height="26"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.6l1.4-2h5l1.4 2h1.6A2.5 2.5 0 0 1 20 8.5v8a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5z" />
                  <circle cx="12" cy="12.5" r="3.5" />
                </svg>
                <span className="scan-option-text">
                  <strong>Take a photo</strong>
                  <span>Opens the back camera</span>
                </span>
              </button>
              <button
                type="button"
                className="scan-option"
                onClick={() => open(upload.current)}
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  width="26"
                  height="26"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15" />
                </svg>
                <span className="scan-option-text">
                  <strong>Upload a photo or PDF</strong>
                  <span>From your photos or files</span>
                </span>
              </button>
              <button
                type="button"
                className="scan-cancel"
                onClick={() => setChoosing(false)}
              >
                Cancel
              </button>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
