# Gmail → OneTally (Apps Script)

A small script that runs **in your own Google account**. Every 15 minutes it finds receipts,
invoices, and renewal notices in your mail and sends them to OneTally, which reads the
subscription details (service, price, renewal date) and reminds you before each renewal.

- **Free:** it runs on Google's free Apps Script quotas.
- **Read-only:** it uses the Gmail API with the `gmail.readonly` scope. It can't send, change,
  or delete mail.
- **Nothing to configure:** OneTally hands you this script with your key and API address
  already filled in. No Gmail filter or label is needed.
- **Cheap to run:** OneTally skips emails with no price or no sign of recurring billing for
  free. Only likely subscription emails are read by the AI (~0.4¢ each, at most 50 a day).

## Setup (about 2 minutes, once)

1. In OneTally: **Subscriptions → Connect Gmail → Create key**. Keep that screen open.
2. Go to [script.google.com](https://script.google.com) → **New project**. In OneTally tap
   **Copy script**, then paste it over everything in `Code.gs`.
3. **Project Settings (gear) → tick "Show appsscript.json manifest file in editor"**. Open
   `appsscript.json`, tap **Copy settings file** in OneTally, and paste it over everything.
   It enables the Gmail API service and asks only for read-only mail access, outgoing requests
   (to OneTally), and the 15-minute timer.
4. Choose **`installTrigger`** → **Run**. Google shows an authorization screen. Because this is
   your own unpublished script, it says _"Google hasn't verified this app"_: click **Advanced →
   Go to OneTally (unsafe)** → **Allow**.

The first run looks back about a year. OneTally reads up to 50 likely subscription emails a day,
so a big backlog finishes over a few days. Check **Executions** in the script's sidebar for the
log, and the **Subscriptions** section in OneTally.

**Missed one?** Label any email **OneTally** in Gmail (create the label if needed). Labelled
emails are always sent and always read.

## Stop or disconnect

- **Pause:** Triggers (clock icon) → delete the `sendToOneTally` trigger.
- **Disconnect:** OneTally → **Connect Gmail → Disconnect**. The key stops working at once.
- **Remove access completely:** [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
  → OneTally → Remove access.

## Troubleshooting

| Log message                                 | Fix                                                                                                 |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `Copy the ready-made script from OneTally…` | You pasted the repo copy (placeholders). Use **Copy script** in OneTally.                           |
| `OneTally rejected the key`                 | Create a new key in OneTally and copy the new script.                                               |
| `Daily limit reached`                       | Nothing to do: the rest are sent tomorrow.                                                          |
| `Found 0 possible subscription emails`      | Nothing matched the search. Label a known subscription email **OneTally** and run `sendToOneTally`. |
| An authorization error mentioning a scope   | Make sure `appsscript.json` is the copied settings file, then run `installTrigger` again.           |

## For developers

`Code.gs` and `appsscript.json` here are the reviewable copies. The app embeds the same text
(`frontend/src/gmailScript.ts`), and a test fails if the two ever differ.
