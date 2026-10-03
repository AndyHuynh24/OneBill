import { useEffect, useState } from "react";

const STEPS = [
  "Uploading your photo",
  "Reading the receipt",
  "Understanding each line",
  "Checking the math",
];

/**
 * Progress while a scan is uploaded and read. The server only says "processing", so the bar
 * and the current step follow typical timings (a few seconds each) and finish when it's done.
 */
export function ScanProgress({ stage }: { stage: "uploading" | "reading" }) {
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(startedAt);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);

  const seconds = (now - startedAt) / 1000;
  const step =
    stage === "uploading" ? 0 : seconds < 2.5 ? 1 : seconds < 6 ? 2 : 3;
  // Eases toward (never reaches) 95% until the server says it's done.
  const percent =
    stage === "uploading"
      ? 25 * (1 - Math.exp(-seconds / 1.5))
      : 25 + 70 * (1 - Math.exp(-seconds / 4));

  return (
    <div className="scan-progress">
      <div
        className="progress progress-lg"
        role="progressbar"
        aria-label="Reading your scan"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <ol className="scan-steps">
        {STEPS.map((label, i) => (
          <li
            key={label}
            className={i < step ? "done" : i === step ? "active" : ""}
          >
            <span className="scan-step-dot" aria-hidden="true">
              {i < step ? "✓" : ""}
            </span>
            {label}
            {i === step ? "…" : ""}
          </li>
        ))}
      </ol>
      <p className="muted scan-note">
        Usually 5–10 seconds. You can close this; the bill keeps reading.
      </p>
    </div>
  );
}
