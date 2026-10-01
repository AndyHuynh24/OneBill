import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { OneTallyApi } from "../api";
import { pollInterval, processingCount } from "../documents";
import { todayIso } from "../format";
import { DocumentSheet } from "./DocumentSheet";
import { RecentDocuments } from "./RecentDocuments";
import { ScanButton } from "./ScanButton";
import { Upcoming } from "./Upcoming";

export type AuthStatus = "loading" | "signedOut" | "signedIn";

interface HomeProps {
  status: AuthStatus;
  api: OneTallyApi;
  onSignIn: () => void;
  onSignOut: () => void;
}

export function Home({ status, api, onSignIn, onSignOut }: HomeProps) {
  if (status !== "signedIn") {
    return (
      <main className="page">
        <Brand />
        {status === "loading" ? (
          <p className="muted">Checking your session…</p>
        ) : (
          <section className="card">
            <p>Scan receipts and bills. OneTally tracks the totals and reminds you before deadlines.</p>
            <button type="button" className="primary" onClick={onSignIn}>
              Sign in
            </button>
          </section>
        )}
      </main>
    );
  }
  return <SignedInHome api={api} onSignOut={onSignOut} />;
}

function Brand() {
  return (
    <header className="brand">
      <h1>OneTally</h1>
      <p className="tagline">One place for every bill, receipt, and dollar you owe or are owed.</p>
    </header>
  );
}

function SignedInHome({ api, onSignOut }: { api: OneTallyApi; onSignOut: () => void }) {
  const queryClient = useQueryClient();
  const [uploading, setUploading] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);

  // Creates the PROFILE item on first sign-in (GET /me); nothing to show yet.
  useQuery({ queryKey: ["me"], queryFn: api.getProfile });
  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: api.listDocuments,
    // Poll only while something is being read; stop as soon as everything is done.
    refetchInterval: (query) => pollInterval(query.state.data?.documents),
  });
  const obligations = useQuery({
    queryKey: ["obligations"],
    queryFn: () => api.listObligations(todayIso()),
  });

  // Deadlines only change when a document finishes: refresh them then, instead of polling.
  const busy = processingCount(documents.data?.documents);
  const previousBusy = useRef(busy);
  useEffect(() => {
    if (busy < previousBusy.current) queryClient.invalidateQueries({ queryKey: ["obligations"] });
    previousBusy.current = busy;
  }, [busy, queryClient]);

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadDocument(file),
    onMutate: (file) => {
      setNotice(null);
      setUploading((names) => [...names, file.name]);
    },
    onSettled: (_data, _error, file) => {
      setUploading((names) => names.filter((n) => n !== file.name));
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (error) => setNotice(error.message || "Upload failed. Please try again."),
  });

  return (
    <main className="page page-with-scan">
      <header className="topbar">
        <h1>OneTally</h1>
        <button type="button" className="link-button" onClick={onSignOut}>
          Sign out
        </button>
      </header>

      <section aria-labelledby="upcoming-title">
        <h2 id="upcoming-title">Upcoming</h2>
        {obligations.data ? (
          <Upcoming obligations={obligations.data.obligations} />
        ) : (
          <p className="muted">{obligations.error ? "Couldn't load deadlines." : "Loading…"}</p>
        )}
      </section>

      <section aria-labelledby="recent-title">
        <h2 id="recent-title">Recent</h2>
        {documents.data ? (
          <RecentDocuments documents={documents.data.documents} uploading={uploading} onOpen={setOpenDoc} />
        ) : (
          <p className="muted">{documents.error ? "Couldn't load documents." : "Loading…"}</p>
        )}
      </section>

      {notice && (
        <p role="alert" className="notice notice-error">
          {notice}
        </p>
      )}

      <ScanButton onFile={(file) => upload.mutate(file)} busy={upload.isPending} />

      {openDoc && <DocumentSheet api={api} docId={openDoc} onClose={() => setOpenDoc(null)} />}
    </main>
  );
}
