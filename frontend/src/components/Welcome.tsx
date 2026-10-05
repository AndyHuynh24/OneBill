export type SignInMethod = "google" | "email";
import { InstallButton } from "./InstallButton";

/** The signed-out front door: what OneBill does, and two ways in. */
export function Welcome({
  loading,
  onSignIn,
}: {
  loading: boolean;
  onSignIn: (method: SignInMethod) => void;
}) {
  return (
    <main className="welcome">
      <header className="welcome-top">
        <Logo />
      </header>

      <section className="welcome-hero">
        <div className="welcome-copy">
          <h1>
            Every bill, <span className="accent-text">sorted.</span>
          </h1>
          <ul className="welcome-points">
            <li>
              <span className="point-icon" aria-hidden="true">
                <PeopleIcon />
              </span>
              <span>
                <strong>Scan and split.</strong> Snap a receipt and split it with friends to the cent.
              </span>
            </li>
            <li>
              <span className="point-icon" aria-hidden="true">
                <BellIcon />
              </span>
              <span>
                <strong>Bills and subscriptions.</strong> Track what renews and get a heads-up before anything is due.
              </span>
            </li>
            <li>
              <span className="point-icon" aria-hidden="true">
                <ChatIcon />
              </span>
              <span>
                <strong>Ask your money anything.</strong> A chat assistant that remembers your bills and answers
                questions about your spending. <span className="soon-tag">Coming soon</span>
              </span>
            </li>
          </ul>

          {loading ? (
            <p className="welcome-loading" role="status">
              <span className="spinner" aria-hidden="true" /> Checking your
              session…
            </p>
          ) : (
            <div className="welcome-actions">
              <button
                type="button"
                className="btn-google"
                onClick={() => onSignIn("google")}
              >
                <GoogleMark />
                Continue with Google
              </button>
              <button
                type="button"
                className="btn-email"
                onClick={() => onSignIn("email")}
              >
                Sign in with email
              </button>
              <InstallButton className="install-welcome" />
            </div>
          )}
          <p className="welcome-private">
            <LockIcon /> Private by design: names, addresses, and card numbers
            are masked before AI reads anything.
          </p>
        </div>

        <ReceiptPreview />
      </section>

      <section className="welcome-features" aria-label="What OneBill does">
        <Feature icon={<CameraIcon />} title="Scan and read">
          Snap a receipt or forward a bill. OneBill reads every line and checks
          that the totals add up.
        </Feature>
        <Feature icon={<PeopleIcon />} title="Split to the cent">
          Drag items to friends, share tax and tip fairly, and send everyone
          their share in one message.
        </Feature>
        <Feature icon={<BellIcon />} title="Never miss a renewal">
          Subscriptions and bills from your inbox, with reminders before they
          renew or fall due.
        </Feature>
      </section>

      <footer className="welcome-footer">
        © {new Date().getFullYear()} OneBill
      </footer>
    </main>
  );
}

export function Logo() {
  return (
    <span className="logo">
      <svg className="logo-mark" viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="9" fill="var(--accent)" />
        <path
          d="M10 8.5h12v15l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4z"
          fill="none"
          stroke="#fff"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
        <path
          d="M13 13h6M13 16.5h6"
          stroke="#fff"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
      </svg>
      <span className="logo-word">OneBill</span>
    </span>
  );
}

function Feature({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <article className="feature">
      <span className="feature-icon" aria-hidden="true">
        {icon}
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
    </article>
  );
}

/** Decorative: a bill (wavy lines stand in for text) and a subscription card with a repeat icon. */
function ReceiptPreview() {
  // One "line of text": a gentle wave from x to x + width.
  const wave = (x: number, y: number, width: number) => {
    const step = 10;
    let d = `M${x} ${y}`;
    for (let i = 0; i < width / step; i++) d += ` q${step / 4} -2.2 ${step / 2} 0 t${step / 2} 0`;
    return d;
  };
  const rows: [number, number][] = [
    [76, 52],
    [64, 40],
    [84, 46],
    [58, 52],
  ];
  return (
    <div className="preview" aria-hidden="true">
      <svg viewBox="0 0 320 300" className="preview-art">
        {/* The bill: wavy lines stand in for text, zigzag torn bottom edge */}
        <g transform="translate(28 18) rotate(-3)">
          <path
            d="M0 16a16 16 0 0 1 16-16h168a16 16 0 0 1 16 16v196l-10 8-10-8-10 8-10-8-10 8-10-8-10 8-10-8-10 8-10-8-10 8-10-8-10 8-10-8-10 8-10-8-10 8-10-8-10 8-10-8z"
            fill="var(--surface)"
            className="preview-paper"
          />
          <path d={wave(24, 34, 92)} className="wave wave-strong" />
          <path d={wave(24, 50, 56)} className="wave wave-soft" />
          {rows.map(([width, price], i) => {
            const y = 84 + i * 24;
            return (
              <g key={y}>
                <path d={wave(24, y, width)} className="wave" />
                <path d={wave(176 - price / 1.6, y, price / 1.6)} className="wave" />
              </g>
            );
          })}
          <line x1="24" y1="178" x2="176" y2="178" stroke="var(--border)" strokeDasharray="4 4" />
          <path d={wave(24, 196, 40)} className="wave wave-strong" />
          <path d={wave(132, 196, 44)} className="wave wave-strong" />
          <circle cx="176" cy="34" r="11" fill="var(--success-soft)" />
          <path d="M171 34l3.5 3.5 6-7" fill="none" stroke="var(--success)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
        </g>
        {/* A subscription card in front: the repeat icon hints at renewals */}
        <g transform="translate(150 178) rotate(5)">
          <rect width="150" height="92" rx="16" fill="var(--surface)" stroke="var(--border)" className="preview-paper" />
          <circle cx="30" cy="30" r="16" fill="var(--accent-soft)" />
          <path
            d="M23.5 27.5a7 7 0 0 1 12.2-3.2l1.8 1.8M36.5 32.5a7 7 0 0 1-12.2 3.2l-1.8-1.8M37.5 22.5v3.6h-3.6M22.5 37.5v-3.6h3.6"
            fill="none"
            stroke="var(--accent-strong)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path d={wave(58, 25, 70)} className="wave wave-strong" />
          <path d={wave(58, 38, 44)} className="wave wave-soft" />
          <rect x="18" y="60" width="44" height="16" rx="8" fill="var(--accent-soft)" />
          <circle cx="122" cy="68" r="5" fill="var(--accent)" />
          <path d={wave(76, 68, 34)} className="wave wave-soft" />
        </g>
      </svg>
    </div>
  );
}

function GoogleMark() {
  return (
    <svg className="google-mark" viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#FFC107"
        d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"
      />
      <path
        fill="#FF3D00"
        d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"
      />
      <path
        fill="#4CAF50"
        d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"
      />
      <path
        fill="#1976D2"
        d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"
      />
    </svg>
  );
}

const icon = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

function CameraIcon() {
  return (
    <svg viewBox="0 0 24 24" {...icon}>
      <path d="M4 8h3l1.5-2h7L17 8h3v11H4z" />
      <circle cx="12" cy="13" r="3.5" />
    </svg>
  );
}

function PeopleIcon() {
  return (
    <svg viewBox="0 0 24 24" {...icon}>
      <circle cx="9" cy="9" r="3" />
      <circle cx="17" cy="10" r="2.5" />
      <path d="M3.5 19c.6-3 2.9-5 5.5-5s4.9 2 5.5 5M14.5 15.2c.8-.7 1.6-1 2.5-1 2 0 3.5 1.6 4 4" />
    </svg>
  );
}

function BellIcon() {
  return (
    <svg viewBox="0 0 24 24" {...icon}>
      <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" />
      <path d="M10 20.5a2 2 0 0 0 4 0" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" {...icon}>
      <path d="M5 5h14v10H10l-4 3.5V15H5z" />
      <path d="M9 9.5h6M9 12h4" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg className="lock-icon" viewBox="0 0 24 24" {...icon} aria-hidden="true">
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}
