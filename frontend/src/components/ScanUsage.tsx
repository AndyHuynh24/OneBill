import type { Usage } from "../api";

interface ScanUsageProps {
  usage: Usage;
  onRenew: () => void;
  renewing?: boolean;
}

/** One small line: the daily scans, what's left today, and Renew once they're used up. */
export function ScanUsage({ usage, onRenew, renewing = false }: ScanUsageProps) {
  const perDay = `${usage.dailyLimit} scans a day`;
  if (usage.leftToday > 0) {
    return (
      <p className="scan-usage muted">
        {perDay} · {usage.leftToday} left today
      </p>
    );
  }
  if (usage.renewalsLeft > 0) {
    return (
      <p className="scan-usage">
        <span className="muted">Out of scans today · </span>
        <button type="button" className="link-button" onClick={onRenew} disabled={renewing}>
          {renewing ? "Renewing…" : `Renew (${usage.renewalsLeft} left this month)`}
        </button>
      </p>
    );
  }
  return <p className="scan-usage muted">{perDay} · none left today, more tomorrow</p>;
}
