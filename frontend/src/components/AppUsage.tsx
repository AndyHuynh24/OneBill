import type { AiTotals, AppUsage as AppUsageData, UsageTotals } from "../api";

const ROWS: { label: string; key: keyof UsageTotals }[] = [
  { label: "Scans", key: "scans" },
  { label: "PDF pages", key: "pages" },
  { label: "Emails read", key: "emails" },
  { label: "Renewals used", key: "renewals" },
];

/** Column headers for the 7-day table, short enough for a phone. */
const SHORT: Partial<Record<keyof UsageTotals, string>> = { pages: "Pages", emails: "Emails", renewals: "Renewals" };

const AI_ROWS: { label: string; value: (t: AiTotals) => string }[] = [
  { label: "Model reads", value: (t) => t.reads.toLocaleString("en-US") },
  { label: "Cost", value: (t) => dollars(t.costMicroUsd) },
  { label: "Avg per read", value: (t) => (t.reads > 0 ? dollars(t.costMicroUsd / t.reads) : "—") },
  {
    label: "Tokens in / out",
    value: (t) => `${t.inputTokens.toLocaleString("en-US")} / ${t.outputTokens.toLocaleString("en-US")}`,
  },
];

/** Micro-dollars as dollars: "$1.84", or "$0.0154" below a dollar so a single read still shows. */
function dollars(microUsd: number): string {
  const usd = microUsd / 1_000_000;
  return `$${usd.toFixed(usd >= 1 ? 2 : 4)}`;
}

/** "Sat, Oct 10" for a YYYY-MM-DD day. */
function dayLabel(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

/** The owner's view of the whole app: today, this month (against the limits), and the last 7 days. */
export function AppUsage({ usage }: { usage: AppUsageData }) {
  const limits: Partial<Record<keyof UsageTotals, number>> = usage.limits;
  const ai = usage.ai;
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
        {usage.days && usage.days.length > 0 && (
          <>
            <h3 className="usage-subtitle">Last {usage.days.length} days</h3>
            <table className="usage-table">
              <thead>
                <tr>
                  <th scope="col">Day</th>
                  {ROWS.map(({ label, key }) => (
                    <th scope="col" key={key}>
                      {SHORT[key] ?? label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {usage.days.map((day, i) => (
                  <tr key={day.date}>
                    <th scope="row">{i === 0 ? "Today" : dayLabel(day.date)}</th>
                    {ROWS.map(({ key }) => (
                      <td key={key} className={day[key] === 0 ? "muted" : undefined}>
                        {day[key]}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        {ai && (
          <>
            <h3 className="usage-subtitle">AI (estimated)</h3>
            <table className="usage-table">
              <thead>
                <tr>
                  <th scope="col">
                    <span className="visually-hidden">Measure</span>
                  </th>
                  <th scope="col">Today</th>
                  <th scope="col">This month</th>
                </tr>
              </thead>
              <tbody>
                {AI_ROWS.map(({ label, value }) => (
                  <tr key={label}>
                    <th scope="row">{label}</th>
                    <td>{value(ai.today)}</td>
                    <td>{value(ai.month)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <p className="muted usage-note">
          All users. Days and months are in UTC.{ai && " AI cost uses list prices (Textract + Bedrock)."}
        </p>
      </div>
    </section>
  );
}
