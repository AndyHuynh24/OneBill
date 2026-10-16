import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useBackLayer } from "../backButton";

export const ACCEPTED_TYPES = "image/jpeg,image/png,application/pdf";

interface ScanButtonProps {
  onFile: (file: File) => void;
  busy?: boolean;
  /** The button's text (the trip card's says "Scan for trip"). */
  label?: string;
  /** Who it's for, added to the inputs' names, e.g. "for Chicago" (a second Scan on screen). */
  purpose?: string;
  /** The trip card's small inline button instead of the fixed bottom bar. */
  inline?: boolean;
  /** The bottom bar is also the app's one Add menu: a receipt typed in by hand, money someone
   *  owes you, a subscription or bill. Each shows only when given. */
  onType?: () => void;
  onOwed?: () => void;
  onSubscription?: () => void;
}

const ICON = {
  "aria-hidden": true,
  viewBox: "0 0 24 24",
  width: 26,
  height: 26,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

/**
 * The app's one primary action. Scan asks how: Take a photo opens the back camera
 * (`capture="environment"`), Upload picks a photo, screenshot, or PDF. A phone's own menu can't
 * choose the camera, so the app shows the choice itself.
 */
export function ScanButton({
  onFile,
  busy = false,
  label = "Scan or add",
  purpose,
  inline = false,
  onType,
  onOwed,
  onSubscription,
}: ScanButtonProps) {
  const suffix = purpose ? ` ${purpose}` : "";
  const camera = useRef<HTMLInputElement>(null);
  const upload = useRef<HTMLInputElement>(null);
  const [choosing, setChoosing] = useState(false);
  useBackLayer(choosing, () => setChoosing(false));

  const pick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) onFile(file);
    event.target.value = ""; // allow picking the same file again
  };
  // A form instead of a file: close the menu, then open it.
  const choose = (action: () => void) => {
    setChoosing(false);
    action();
  };
  const extra = (title: string, hint: string, path: string, action: () => void) => (
    <button type="button" className="scan-option" onClick={() => choose(action)}>
      <svg {...ICON}>
        <path d={path} />
      </svg>
      <span className="scan-option-text">
        <strong>{title}</strong>
        <span>{hint}</span>
      </span>
    </button>
  );
  // Open the picker inside the tap itself (browsers require that), then close the menu.
  const open = (input: HTMLInputElement | null) => {
    input?.click();
    setChoosing(false);
  };

  return (
    <div className={inline ? "scan-inline" : "scan-bar"}>
      <input
        ref={camera}
        id={inline ? undefined : "scan-input"}
        type="file"
        accept="image/*"
        capture="environment"
        className="visually-hidden"
        aria-label={`Scan a receipt${suffix} with the camera`}
        disabled={busy}
        onChange={pick}
      />
      <input
        ref={upload}
        type="file"
        accept={ACCEPTED_TYPES}
        className="visually-hidden"
        aria-label={`Upload a photo or PDF${suffix}`}
        disabled={busy}
        onChange={pick}
      />
      <button
        type="button"
        className={inline ? "trip-scan" : "scan-button"}
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
        <span>{busy ? "Uploading…" : label}</span>
      </button>

      {choosing &&
        createPortal(
          <div className="picker-backdrop" onClick={() => setChoosing(false)}>
            <div
              className="picker scan-choice"
              role="dialog"
              aria-modal="true"
              aria-label="Scan or add"
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
              {onType &&
                extra(
                  "Type it in",
                  "No photo? Add the items by hand, then split it",
                  "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
                  onType,
                )}
              {(onOwed || onSubscription) && <hr className="scan-divider" />}
              {onOwed &&
                extra(
                  "Someone owes me money",
                  "Keep track of it until they pay you back",
                  "M12 3v18M16.5 7.5c0-1.7-2-3-4.5-3s-4.5 1.3-4.5 3S9.5 10.5 12 11s4.5 1.3 4.5 3.5-2 3.5-4.5 3.5-4.5-1.3-4.5-3",
                  onOwed,
                )}
              {onSubscription &&
                extra(
                  "Subscription or bill",
                  "Get a reminder before it renews or is due",
                  "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0",
                  onSubscription,
                )}
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
