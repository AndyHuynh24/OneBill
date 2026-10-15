import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { trapBackButton, useBackLayer, useExitHint } from "../backButton";
import { InstallButton } from "./InstallButton";
import { useEffect, useRef, useState } from "react";
import type { OneBillApi } from "../api";
import { pollInterval, processingCount } from "../documents";
import { AddSubscription } from "./AddSubscription";
import { AskBox, AskSheet } from "./Ask";
import { AppUsage } from "./AppUsage";
import { ConnectGmail } from "./ConnectGmail";
import { ScanProgress } from "./ScanProgress";
import { SubscriptionSheet } from "./SubscriptionSheet";
import { ComingUp } from "./ComingUp";
import { billReminders, upcomingItems } from "../upcoming";
import { Subscriptions } from "./Subscriptions";
import { todayIso } from "../format";
import { DocumentSheet } from "./DocumentSheet";
import { RecentDocuments } from "./RecentDocuments";
import { ScanButton } from "./ScanButton";
import { ScanUsage } from "./ScanUsage";
import { StartTrip } from "./StartTrip";
import { TripCard } from "./TripCard";
import { TripSheet } from "./TripSheet";
import { Upcoming } from "./Upcoming";
import { Logo, type SignInMethod, Welcome } from "./Welcome";

export type AuthStatus = "loading" | "signedOut" | "signedIn";

interface HomeProps {
  status: AuthStatus;
  api: OneBillApi;
  onSignIn: (method: SignInMethod) => void;
  onSignOut: () => void;
}

export function Home({ status, api, onSignIn, onSignOut }: HomeProps) {
  if (status !== "signedIn") {
    return <Welcome loading={status === "loading"} onSignIn={onSignIn} />;
  }
  return <SignedInHome api={api} onSignOut={onSignOut} />;
}

const HOME_DOCUMENTS = 5;

function SignedInHome({ api, onSignOut }: { api: OneBillApi; onSignOut: () => void }) {
  const queryClient = useQueryClient();
  const [uploading, setUploading] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  // A scan opens its bill right away: first the upload, then live progress, then the bill.
  const [scanning, setScanning] = useState(false);

  // Creates the PROFILE item on first sign-in (GET /me); nothing to show yet.
  const profile = useQuery({ queryKey: ["me"], queryFn: api.getProfile });
  const reminders = useMutation({
    mutationFn: (on: boolean) => api.setEmailReminders(on),
    onSuccess: (updated) => queryClient.setQueryData(["me"], updated),
  });
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
  const trips = useQuery({ queryKey: ["trips"], queryFn: api.listTrips });
  const [openTrip, setOpenTrip] = useState<string | null>(null);
  const [startingTrip, setStartingTrip] = useState(false);
  const [question, setQuestion] = useState<string | null>(null);
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

  // Back closes what's open (newest first) and never leaves from a single press on home.
  useEffect(() => trapBackButton(), []); // this screen only shows while signed in
  useBackLayer(view !== "home", () => setView("home"));
  useBackLayer(openTrip !== null, () => setOpenTrip(null));
  useBackLayer(startingTrip, () => setStartingTrip(false));
  useBackLayer(question !== null, () => setQuestion(null));
  useBackLayer(openDoc !== null, closeSheet);
  useBackLayer(scanning && openDoc === null, () => setScanning(false));
  useBackLayer(openSub !== null, () => setOpenSub(null));
  useBackLayer(adding, () => setAdding(false));
  useBackLayer(connecting, () => setConnecting(false));
  const exitHint = useExitHint();

  const upload = useMutation({
    // The main Scan button sends no trip; the trip card's own Scan sends its trip.
    mutationFn: ({ file, tripId }: { file: File; tripId?: string }) =>
      tripId ? api.uploadDocument(file, tripId) : api.uploadDocument(file),
    onSuccess: (docId) => setOpenDoc(docId), // the uploading sheet becomes the bill's screen
    onMutate: ({ file }) => {
      setNotice(null);
      setOpenDoc(null);
      setScanning(true);
      setUploading((names) => [...names, file.name]);
    },
    onSettled: (_data, _error, { file }) => {
      setScanning(false);
      setUploading((names) => names.filter((n) => n !== file.name));
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["trips"] }); // a scan joins the trip that's on
      queryClient.invalidateQueries({ queryKey: ["trip"] });
    },
    onError: (error) => setNotice(error.message || "Upload failed. Please try again."),
  });

  const renew = useMutation({
    mutationFn: () => api.renewUsage(),
    onMutate: () => setNotice(null),
    onSuccess: (usage) => {
      queryClient.setQueryData(["documents"], (old: typeof documents.data) => old && { ...old, usage });
    },
    onError: (error) => setNotice(error.message || "Couldn't renew. Please try again."),
  });

  const subscriptionsSection = () =>
    subscriptions.data ? (
      <Subscriptions
        subscriptions={subscriptions.data.subscriptions}
        connection={subscriptions.data.connection}
        today={todayIso()}
        onOpen={setOpenSub}
        onConnect={() => setConnecting(true)}
        onAdd={() => setAdding(true)}
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
            <h1 className="topbar-logo">
              <Logo />
            </h1>
            <div className="topbar-actions">
              <InstallButton className="install-topbar" />
              <button type="button" className="link-button" onClick={onSignOut}>
                Sign out
              </button>
            </div>
          </header>

          <AskBox onAsk={setQuestion} />

          {/* One list for everything with a date: subscriptions and bills, soonest first. */}
          <section aria-labelledby="coming-title">
            <div className="section-title">
              <h2 id="coming-title">Subscriptions &amp; bills</h2>
            </div>
            {subscriptions.data && obligations.data ? (
              <ComingUp
                items={upcomingItems(subscriptions.data.subscriptions, obligations.data.obligations, todayIso())}
                subscriptions={subscriptions.data.subscriptions}
                connection={subscriptions.data.connection}
                today={todayIso()}
                onOpen={(item) => ("subscription" in item.open ? setOpenSub(item.open.subscription) : setOpenDoc(item.open.document))}
                onViewAll={() => setView("subscriptions")}
                onAdd={() => setAdding(true)}
                onConnect={() => setConnecting(true)}
                emailReminders={profile.data ? (reminders.isPending ? reminders.variables : profile.data.emailReminders !== false) : undefined}
                onEmailReminders={(on) => reminders.mutate(on)}
              />
            ) : (
              <p className="muted">{subscriptions.error || obligations.error ? "Couldn't load this." : "Loading…"}</p>
            )}
          </section>

          <section aria-labelledby="recent-title">
            <div className="section-title">
              <h2 id="recent-title">Receipts</h2>
              {allDocuments.length > HOME_DOCUMENTS && (
                <button
                  type="button"
                  className="link-button"
                  aria-label="View all receipts"
                  onClick={() => setView("documents")}
                >
                  View all ({allDocuments.length})
                </button>
              )}
            </div>
            {trips.data && (
              <TripCard
                active={trips.data.active}
                past={trips.data.past}
                onOpen={setOpenTrip}
                onStart={() => setStartingTrip(true)}
                onScan={(file, tripId) => upload.mutate({ file, tripId })}
                scanning={upload.isPending}
              />
            )}
            {documents.data ? (
              <>
                <RecentDocuments
                  documents={allDocuments}
                  uploading={uploading}
                  onOpen={setOpenDoc}
                  limit={HOME_DOCUMENTS}
                />
              </>
            ) : (
              <p className="muted">{documents.error ? "Couldn't load documents." : "Loading…"}</p>
            )}
            {documents.data?.usage && (
              <ScanUsage usage={documents.data.usage} onRenew={() => renew.mutate()} renewing={renew.isPending} />
            )}
          </section>

          {/* Only the owner's API response has it (decided by the server from the sign-in). */}
          {documents.data?.appUsage && <AppUsage usage={documents.data.appUsage} />}
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
            <>
              {subscriptionsSection()}
              {/* Due dates from scanned or emailed bills (subscriptions remind on their own). */}
              {obligations.data && billReminders(obligations.data.obligations).length > 0 && (
                <section aria-labelledby="due-title" className="due-section">
                  <h2 id="due-title" className="due-title">
                    Bill due dates
                  </h2>
                  <Upcoming
                    obligations={billReminders(obligations.data.obligations)}
                    onRemove={(o) => removeReminder.mutate(o.oblId)}
                  />
                </section>
              )}
            </>
          )}
        </>
      )}

      {notice && (
        <p role="alert" className="notice notice-error">
          {notice}
        </p>
      )}

      <ScanButton onFile={(file) => upload.mutate({ file })} busy={upload.isPending} />

      {exitHint && (
        <p className="exit-hint" role="status">
          Press back again to exit
        </p>
      )}

      {scanning && !shownDoc && <UploadingSheet onClose={() => setScanning(false)} />}
      {openTrip && (
        <TripSheet api={api} tripId={openTrip} onClose={() => setOpenTrip(null)} onOpenDocument={setOpenDoc} />
      )}
      {startingTrip && <StartTrip api={api} onClose={() => setStartingTrip(false)} />}
      {question !== null && (
        <AskSheet api={api} firstQuestion={question} onClose={() => setQuestion(null)} onOpenDocument={setOpenDoc} />
      )}
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
