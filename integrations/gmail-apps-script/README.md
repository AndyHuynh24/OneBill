# Gmail → OneBill (Apps Script)

A small script that runs **in your own Google account**. Every 15 minutes it sends the emails you
**label "OneBill"** in Gmail, and nothing else, to OneBill. Each one is read like a scanned bill
(items, total, due date; you can split it), and tracked as a subscription too when it is one
(service, price, renewal date, with reminders before each renewal).

- **Free:** it runs on Google's free Apps Script quotas.
- **Read-only:** it uses the Gmail API with the `gmail.readonly` scope. It can't send, change,
  or delete mail.
- **You choose what's shared:** only emails with the **OneBill** label are sent. Create the
  label once in Gmail, then add it to any bill, receipt, or subscription email.
- **Ready-made:** OneBill hands you this script with your key and API address already filled in.
- **Cheap to run:** about 0.6¢ per email, plus 0.4¢ if it's also a subscription; at most 10 a day
  (a bigger backlog continues the next day). Keys last a year.

## Setup (about 2 minutes, once)

1. In OneBill: **Subscriptions → Connect Gmail → Create key**. Keep that screen open.
2. Go to [script.google.com](https://script.google.com) → **New project**. In OneBill tap
   **Copy script**, then paste it over everything in `Code.gs`.
3. **Project Settings (gear) → tick "Show appsscript.json manifest file in editor"**. Open
   `appsscript.json`, tap **Copy settings file** in OneBill, and paste it over everything.
   It enables the Gmail API service and asks only for read-only mail access, outgoing requests
   (to OneBill), and the 15-minute timer.
4. Choose **`installTrigger`** → **Run**. Google shows an authorization screen. Because this is
   your own unpublished script, it says _"Google hasn't verified this app"_: click **Advanced →
   Go to OneBill (unsafe)** → **Allow**.

It sends labelled emails from about the last year. OneBill reads up to 50 a day, so a big
backlog finishes over a few days. Check **Executions** in the script's sidebar for the
log, and the **Subscriptions** section in OneBill.

**Labelled one later?** That's fine: an email is sent as soon as it has the label, at the next
15-minute run.

## Stop or disconnect

- **Pause:** Triggers (clock icon) → delete the `sendToOneBill` trigger.
- **Disconnect:** OneBill → **Connect Gmail → Disconnect**. The key stops working at once.
- **Remove access completely:** [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
  → OneBill → Remove access.

## Testing again from scratch
1. In OneBill, open the subscription and tap **Delete subscription**. This also forgets which
   of its emails were already read.
2. In the script, choose **`forgetSentEmails`** → **Run**, then **`sendToOneBill`** → **Run**.

## Troubleshooting

| Log message                                 | Fix                                                                                                 |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `Copy the ready-made script from OneBill…` | You pasted the repo copy (placeholders). Use **Copy script** in OneBill.                           |
| `OneBill rejected the key`                 | The key was replaced, revoked, or is over a year old. Create a new key in OneBill and copy the new script. |
| `Daily limit reached`                       | Nothing to do: the rest are sent tomorrow.                                                          |
| `No "OneBill" label yet`                   | In Gmail, create a label named **OneBill** and add it to an email, then run `sendToOneBill`.      |
| An authorization error mentioning a scope   | Make sure `appsscript.json` is the copied settings file, then run `installTrigger` again.           |

## For developers

`Code.gs` and `appsscript.json` here are the reviewable copies. The app embeds the same text
(`frontend/src/gmailScript.ts`), and a test fails if the two ever differ.
