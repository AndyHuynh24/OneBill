import { useQuery } from "@tanstack/react-query";
import type { Profile } from "../api";

interface ProfileCardProps {
  loadProfile: () => Promise<Profile>;
  onSignOut: () => void;
}

export function ProfileCard({ loadProfile, onSignOut }: ProfileCardProps) {
  // TanStack Query handles loading/error state and caching; "me" is the cache key.
  const { data, error, isPending } = useQuery({ queryKey: ["me"], queryFn: loadProfile });

  return (
    <section className="card">
      <h2>Your profile</h2>

      {isPending && <p className="muted">Loading…</p>}

      {error && (
        <p role="alert" className="error">
          Couldn't load your profile. Try signing out and in again.
        </p>
      )}

      {data && (
        <dl className="fields">
          <dt>User ID</dt>
          <dd>
            <code>{data.userId}</code>
          </dd>
          <dt>Base currency</dt>
          <dd>{data.baseCurrency}</dd>
          <dt>Member since</dt>
          <dd>{new Date(data.createdAt).toLocaleDateString()}</dd>
        </dl>
      )}

      <button type="button" onClick={onSignOut}>
        Sign out
      </button>
    </section>
  );
}
