import type { Profile } from "../api";
import { ProfileCard } from "./ProfileCard";

export type AuthStatus = "loading" | "signedOut" | "signedIn";

interface HomeProps {
  status: AuthStatus;
  onSignIn: () => void;
  onSignOut: () => void;
  loadProfile: () => Promise<Profile>;
}

export function Home({ status, onSignIn, onSignOut, loadProfile }: HomeProps) {
  return (
    <main className="page">
      <header className="brand">
        <h1>OneTally</h1>
        <p className="tagline">One place for every bill, receipt, and dollar you owe or are owed.</p>
      </header>

      {status === "loading" && <p className="muted">Checking your session…</p>}

      {status === "signedOut" && (
        <section className="card">
          <p>Sign in to see your account.</p>
          <button type="button" className="primary" onClick={onSignIn}>
            Sign in
          </button>
        </section>
      )}

      {status === "signedIn" && <ProfileCard loadProfile={loadProfile} onSignOut={onSignOut} />}
    </main>
  );
}
