import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { GmailConnection, OneTallyApi } from "../api";
import { formatDate } from "../format";
import { buildGmailScript, GMAIL_MANIFEST } from "../gmailScript";

interface ConnectGmailProps {
  api: OneTallyApi;
  connection: GmailConnection;
  onClose: () => void;
}

/**
 * Connect Gmail: create the ingest key (only its hash is stored) and hand over a ready-made
 * Apps Script with the key and API address filled in, plus its settings file. Or disconnect.
 */
export function ConnectGmail({ api, connection, onClose }: ConnectGmailProps) {
  const queryClient = useQueryClient();
  const [key, setKey] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["subscriptions"] });
  const create = useMutation({
    mutationFn: api.createIngestKey,
    onSuccess: ({ key }) => {
      setKey(key);
      refresh();
    },
  });
  const revoke = useMutation({ mutationFn: api.revokeIngestKey, onSuccess: refresh });

  async function copy(label: string, value: string) {
    await navigator.clipboard?.writeText(value);
    setCopied(label);
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet" role="dialog" aria-modal="true" aria-labelledby="gmail-title" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-header">
          <h2 id="gmail-title">Connect Gmail</h2>
          <button type="button" className="icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </header>

        {key ? (
          <div className="sheet-section">
            <p className="notice notice-warn">
              Your script is ready. It contains your key, which is shown only once: if you lose it, create a new one.
            </p>
            <ol className="steps">
              <li>
                Open <strong>script.google.com</strong> → <strong>New project</strong>, then paste the script over
                everything in <code>Code.gs</code>.
              </li>
              <li>
                <strong>Project Settings</strong> → tick <strong>Show “appsscript.json”</strong>, open that file in the
                editor, and paste the settings file over it.
              </li>
              <li>
                Choose <strong>installTrigger</strong> → <strong>Run</strong>, then allow access (Advanced → Go to
                OneTally). Then label emails <strong>OneTally</strong> in Gmail (create the label first): they appear here
                within 15 minutes.
              </li>
            </ol>
            <div className="sheet-actions">
              <button type="button" className="primary" onClick={() => copy("script", buildGmailScript(api.baseUrl, key))}>
                Copy script
              </button>
              <button type="button" onClick={() => copy("settings file", GMAIL_MANIFEST)}>
                Copy settings file
              </button>
            </div>
            {copied && (
              <p role="status" className="muted">
                Copied the {copied}.
              </p>
            )}
          </div>
        ) : (
          <>
            {connection.connected ? (
              <p>
                Connected since {connection.createdAt ? formatDate(connection.createdAt.slice(0, 10)) : "—"}
                {connection.expiresOn ? `; the key works until ${formatDate(connection.expiresOn.slice(0, 10))}` : ""}. A
                new key replaces the old one.
              </p>
            ) : (
              <p>
                OneTally gives you a small script to paste into your own Google account. Every 15 minutes it sends the
                emails you label <strong>OneTally</strong> in Gmail, and nothing else, with <strong>read-only</strong>{" "}
                access. Each becomes a bill you can split, and a subscription too when it is one. About 2 minutes, once.
              </p>
            )}
            <div className="sheet-actions">
              <button type="button" className="primary" disabled={create.isPending} onClick={() => create.mutate()}>
                {connection.connected ? "Create new key" : "Create key"}
              </button>
              {connection.connected && (
                <button type="button" disabled={revoke.isPending} onClick={() => revoke.mutate()}>
                  Disconnect
                </button>
              )}
            </div>
          </>
        )}
        {(create.error || revoke.error) && (
          <p role="alert" className="error">
            {(create.error ?? revoke.error)?.message}
          </p>
        )}
      </section>
    </div>
  );
}
