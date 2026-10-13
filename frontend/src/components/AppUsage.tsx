import type { AppUsage as AppUsageData, UsageTotals } from "../api";

const ROWS: { label: string; key: keyof UsageTotals }[] = [
  { label: "Scans", key: "scans" },
  { label: "PDF pages", key: "pages" },
  { label: "Emails read", key: "emails" },
  { label: "Renewals used", key: "renewals" },
];

/** The owner's view of the whole app: today and this month, against the monthly limits. */
export function AppUsage({ usage }: { usage: AppUsageData }) {
  const limits: Partial<Record<keyof UsageTotals, number>> = usage.limits;
  return (
    <section aria-labelledby="app-usage-title">
      <div className="section-title">
        <h2 id="app-usage-title">App usage</h2>
      </div>
      <div className="card">
        <table className="usage-table">
          <thead>
            <tr>
              <th scope="col">
                <span className="visually-hidden">Count</span>
              </th>
              <th scope="col">Today</th>
              <th scope="col">This month</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map(({ label, key }) => (
              <tr key={key}>
                <th scope="row">{label}</th>
                <td>{usage.today[key]}</td>
                <td>
                  {usage.month[key]}
                  {limits[key] !== undefined && <span className="muted"> / {limits[key]}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted usage-note">All users. Days and months are in UTC.</p>
      </div>
    </section>
  );
}
