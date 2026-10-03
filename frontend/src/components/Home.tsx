import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { OneTallyApi } from "../api";
import { pollInterval, processingCount } from "../documents";
import { AddSubscription } from "./AddSubscription";
import { ConnectGmail } from "./ConnectGmail";
import { ScanProgress } from "./ScanProgress";
import { SubscriptionSheet } from "./SubscriptionSheet";
import { Subscriptions } from "./Subscriptions";
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

const HOME_DOCUMENTS = 5;
const HOME_SUBSCRIPTIONS = 3;

function SignedInHome({ api, onSignOut }: { api: OneTallyApi; onSignOut: () => void }) {
  const queryClient = useQueryClient();
  const [uploading, setUploading] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  // A scan opens its bill right away: first the upload, then live progress, then the bill.
  const [scanning, setScanning] = useState(false);

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
  const subscriptions = useQuery({ queryKey: ["subscriptions"], queryFn: api.listSubscriptions });
  const removeReminder = useMutation({
    mutationFn: (oblId: string) => api.deleteObligation(oblId),
    onSettled: () => {
      for (const key of [["obligations"], ["subscriptions"], ["documents"]]) {
        queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });
  const [openSub, setOpenSub] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [view, setView] = useState<"home" | "documents" | "subscriptions">("home");
  const shownSub = subscriptions.data?.subscriptions.find((s) => s.serviceKey === openSub);

  // Deadlines only change when a document finishes: refresh them then, instead of polling.
  const busy = processingCount(documents.data?.documents);
  const previousBusy = useRef(busy);
  useEffect(() => {
    if (busy < previousBusy.current) queryClient.invalidateQueries({ queryKey: ["obligations"] });
    previousBusy.current = busy;
  }, [busy, queryClient]);

  const shownDoc = openDoc;
  const closeSheet = () => setOpenDoc(null);

  const upload = useMutation({
    mutationFn: (file: File) => api.uploadDocument(file),
    onSuccess: (docId) => setOpenDoc(docId), // the uploading sheet becomes the bill's screen
    onMutate: (file) => {
      setNotice(null);
      setOpenDoc(null);
      setScanning(true);
      setUploading((names) => [...names, file.name]);
    },
    onSettled: (_data, _error, file) => {
      setScanning(false);
      setUploading((names) => names.filter((n) => n !== file.name));
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (error) => setNotice(error.message || "Upload failed. Please try again."),
  });

  const subscriptionsSection = (limit?: number) =>
    subscriptions.data ? (
      <Subscriptions
        subscriptions={subscriptions.data.subscriptions}
        connection={subscriptions.data.connection}
        today={todayIso()}
        limit={limit}
        onOpen={setOpenSub}
        onConnect={() => setConnecting(true)}
        onAdd={() => setAdding(true)}
        onViewAll={limit ? () => setView("subscriptions") : undefined}
      />
    ) : (
      <p className="muted">{subscriptions.error ? "Couldn't load subscriptions." : "Loading…"}</p>
    );
  const allDocuments = documents.data?.documents ?? [];

  return (
    <main className="page page-with-scan">
      {view === "home" ? (
        <>
          <header className="topbar">
            <h1>
              One<span>Tally</span>
            </h1>
            <button type="button" className="link-button" onClick={onSignOut}>
              Sign out
            </button>
          </header>

          <section aria-labelledby="upcoming-title">
            <div className="section-title">
              <h2 id="upcoming-title">Coming up</h2>
            </div>
            <p className="section-desc">Due dates, renewals and return windows</p>
            {obligations.data ? (
              <Upcoming obligations={obligations.data.obligations} onRemove={(o) => removeReminder.mutate(o.oblId)} />
            ) : (
              <p className="muted">{obligations.error ? "Couldn't load deadlines." : "Loading…"}</p>
            )}
          </section>

          <section aria-labelledby="subscriptions-title">
            <div className="section-title">
              <h2 id="subscriptions-title">Subscriptions &amp; bills</h2>
            </div>
            <p className="section-desc">What renews and what's due each month</p>
            {subscriptionsSection(HOME_SUBSCRIPTIONS)}
          </section>

          <section aria-labelledby="recent-title">
            <div className="section-title">
              <h2 id="recent-title">Receipts</h2>
            </div>
            <p className="section-desc">Scan a receipt to check it and split it with friends</p>
            {documents.data ? (
              <>
                <RecentDocuments
                  documents={allDocuments}
                  uploading={uploading}
                  onOpen={setOpenDoc}
                  limit={HOME_DOCUMENTS}
                />
                {allDocuments.length > HOME_DOCUMENTS && (
                  <button
                    type="button"
                    className="link-button view-all"
                    aria-label="View all receipts"
                    onClick={() => setView("documents")}
                  >
                    View all ({allDocuments.length})
                  </button>
                )}
              </>
            ) : (
              <p className="muted">{documents.error ? "Couldn't load documents." : "Loading…"}</p>
            )}
          </section>
        </>
      ) : (
        <>
          <header className="page-header">
            <button type="button" className="icon-button back-button" aria-label="Back" onClick={() => setView("home")}>
              ←
            </button>
            <h1>{view === "documents" ? "All receipts" : "Subscriptions & bills"}</h1>
          </header>
          {view === "documents" ? (
            <RecentDocuments documents={allDocuments} uploading={uploading} onOpen={setOpenDoc} />
          ) : (
            subscriptionsSection()
          )}
        </>
      )}

      {notice && (
        <p role="alert" className="notice notice-error">
          {notice}
        </p>
      )}

      <ScanButton onFile={(file) => upload.mutate(file)} busy={upload.isPending} />

      {scanning && !shownDoc && <UploadingSheet onClose={() => setScanning(false)} />}
      {shownDoc && <DocumentSheet api={api} docId={shownDoc} onClose={closeSheet} />}
      {shownSub && <SubscriptionSheet api={api} subscription={shownSub} onClose={() => setOpenSub(null)} />}
      {adding && <AddSubscription api={api} onClose={() => setAdding(false)} />}
      {connecting && subscriptions.data && (
        <ConnectGmail api={api} connection={subscriptions.data.connection} onClose={() => setConnecting(false)} />
      )}
    </main>
  );
}

/** Shown from the moment you pick a photo until the upload finishes (then the bill opens). */
function UploadingSheet({ onClose }: { onClose: () => void }) {
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet" role="dialog" aria-modal="true" aria-labelledby="uploading-title" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-header">
          <h2 id="uploading-title">Reading your bill</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>
        <ScanProgress stage="uploading" />
      </section>
    </div>
  );
}
