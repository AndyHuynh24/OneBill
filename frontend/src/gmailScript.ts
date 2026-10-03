/**
 * The Gmail Apps Script, ready to paste: Connect Gmail fills in the user's API address and key.
 * The template mirrors integrations/gmail-apps-script/Code.gs exactly (a test checks it), so the
 * repo file stays the readable, reviewable copy.
 */

export const GMAIL_SCRIPT_TEMPLATE = String.raw`/**
 * OneTally: send your subscription emails from Gmail to OneTally.
 *
 * Get this script from OneTally → Subscriptions → Connect Gmail → Copy script: the copy there
 * has your API address and key filled in (this file in the repo has placeholders).
 *
 * It runs inside your own Google account, every 15 minutes. It searches your mail for
 * receipts, invoices, and renewal notices (plus anything you label "OneTally" by hand) and
 * sends each one to OneTally once. It uses the Gmail API with read-only access: it can't send,
 * change, or delete mail. OneTally skips anything that isn't about a subscription.
 */

var ONETALLY_URL = '__ONETALLY_URL__';
var ONETALLY_KEY = '__ONETALLY_KEY__';

var LABEL = 'OneTally'; // optional: label any email by hand to make sure it's read
var QUERY =
  'newer_than:400d -in:spam -in:trash (label:' + LABEL + ' OR subject:(receipt OR invoice OR ' +
  'renewal OR renews OR subscription OR "your plan" OR trial OR membership OR billing OR ' +
  '"payment confirmation"))';
var MAX_BODY_CHARS = 250000;
var SENT_CHUNKS = 8; // remembers up to 8 × 500 sent message IDs in Script properties
var IDS_PER_CHUNK = 500;

/** Run once to start: checks the script, then schedules sendToOneTally every 15 minutes. */
function installTrigger() {
  settings_(); // fails early with a clear message if this isn't the ready-made copy
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'sendToOneTally') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('sendToOneTally').timeBased().everyMinutes(15).create();
  console.log('Scheduled every 15 minutes. Running once now…');
  sendToOneTally();
}

/** Send every matching email (from about the last year) that hasn't been sent yet. */
function sendToOneTally() {
  var s = settings_();
  var sent = loadSent_();
  var labelId = labelId_();
  var ids = listMessageIds_(QUERY);
  var fresh = ids.filter(function (id) {
    return !sent[id];
  });
  console.log('Found ' + ids.length + ' possible subscription emails; ' + fresh.length + ' not sent yet.');
  var counts = {};

  for (var i = 0; i < fresh.length; i++) {
    var message = Gmail.Users.Messages.get('me', fresh[i], { format: 'full' });
    var response = UrlFetchApp.fetch(s.url, {
      method: 'post',
      contentType: 'application/json',
      headers: { Authorization: 'Bearer ' + s.key },
      payload: JSON.stringify(toPayload_(message, labelId)),
      muteHttpExceptions: true,
    });
    var code = response.getResponseCode();
    if (code === 401) throw new Error('OneTally rejected the key. Copy a new script from OneTally → Connect Gmail.');
    if (code === 429 || code >= 500) {
      console.log(code === 429 ? 'Daily limit reached; the rest are sent tomorrow.' : 'OneTally is busy (' + code + '); will retry next run.');
      break; // unsent emails stay unsent and are retried later
    }
    var status = code === 200 ? JSON.parse(response.getContentText()).status : 'rejected (' + code + ')';
    counts[status] = (counts[status] || 0) + 1;
    sent[fresh[i]] = true;
  }
  saveSent_(sent, ids);
  console.log('Done: ' + JSON.stringify(counts));
}

function settings_() {
  if (ONETALLY_URL.indexOf('__') === 0 || ONETALLY_KEY.indexOf('__') === 0) {
    throw new Error('Copy the ready-made script from OneTally → Subscriptions → Connect Gmail.');
  }
  return { url: ONETALLY_URL.replace(/\/+$/, '') + '/ingest/email', key: ONETALLY_KEY };
}

/** The ID of the "OneTally" label, if you created one (emails you label are always read). */
function labelId_() {
  var labels = Gmail.Users.Labels.list('me').labels || [];
  for (var i = 0; i < labels.length; i++) {
    if (labels[i].name.toLowerCase() === LABEL.toLowerCase()) return labels[i].id;
  }
  return null;
}

/**
 * For testing: forget which emails were sent, so the next run sends them all again. (After
 * deleting a subscription in OneTally, its emails are read again when they're re-sent.)
 */
function forgetSentEmails() {
  var props = PropertiesService.getScriptProperties();
  for (var c = 0; c < SENT_CHUNKS; c++) props.deleteProperty('SENT_' + c);
  console.log('Forgot sent emails. Run sendToOneTally to send them again.');
}

/** Sent message IDs, kept in a few Script properties (each property holds at most 9 KB). */
function loadSent_() {
  var props = PropertiesService.getScriptProperties();
  var sent = {};
  for (var c = 0; c < SENT_CHUNKS; c++) {
    (props.getProperty('SENT_' + c) || '').split(',').forEach(function (id) {
      if (id) sent[id] = true;
    });
  }
  return sent;
}

/** Keep only IDs still in the search window (newest first), so the list can't grow forever. */
function saveSent_(sent, idsInWindow) {
  var props = PropertiesService.getScriptProperties();
  var keep = idsInWindow.filter(function (id) {
    return sent[id];
  });
  for (var c = 0; c < SENT_CHUNKS; c++) {
    var chunk = keep.slice(c * IDS_PER_CHUNK, (c + 1) * IDS_PER_CHUNK).join(',');
    if (chunk) props.setProperty('SENT_' + c, chunk);
    else props.deleteProperty('SENT_' + c);
  }
}

function listMessageIds_(query) {
  var ids = [];
  var pageToken;
  do {
    var page = Gmail.Users.Messages.list('me', { q: query, maxResults: 100, pageToken: pageToken });
    (page.messages || []).forEach(function (m) {
      ids.push(m.id);
    });
    pageToken = page.nextPageToken;
  } while (pageToken && ids.length < SENT_CHUNKS * IDS_PER_CHUNK);
  return ids;
}

function toPayload_(message, labelId) {
  var headers = {};
  (message.payload.headers || []).forEach(function (h) {
    headers[h.name.toLowerCase()] = h.value;
  });
  // Send both: many emails have a stub plain part ("view in browser") with the prices only in
  // the HTML. OneTally reads whichever version shows more.
  var text = findPart_(message.payload, 'text/plain');
  var html = findPart_(message.payload, 'text/html');
  return {
    messageId: message.id,
    subject: headers['subject'] || '',
    from: headers['from'] || '',
    date: new Date(Number(message.internalDate)).toISOString(),
    labelled: !!labelId && (message.labelIds || []).indexOf(labelId) >= 0,
    text: text ? text.slice(0, MAX_BODY_CHARS) : null,
    html: html ? html.slice(0, MAX_BODY_CHARS) : null,
  };
}

/** The first body part of this MIME type, searching nested multipart sections. */
function findPart_(part, mimeType) {
  if (part.mimeType === mimeType && part.body && part.body.data) return decode_(part.body.data);
  var parts = part.parts || [];
  for (var i = 0; i < parts.length; i++) {
    var found = findPart_(parts[i], mimeType);
    if (found) return found;
  }
  return null;
}

/** Body data arrives base64url-encoded (or, in some runtimes, already as bytes). */
function decode_(data) {
  var bytes = typeof data === 'string' ? Utilities.base64DecodeWebSafe(data) : data;
  return Utilities.newBlob(bytes).getDataAsString('UTF-8');
}`;

export const GMAIL_MANIFEST = `{
  "timeZone": "America/Los_Angeles",
  "runtimeVersion": "V8",
  "exceptionLogging": "STACKDRIVER",
  "dependencies": {
    "enabledAdvancedServices": [
      {
        "userSymbol": "Gmail",
        "serviceId": "gmail",
        "version": "v1"
      }
    ]
  },
  "oauthScopes": [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/script.external_request",
    "https://www.googleapis.com/auth/script.scriptapp"
  ]
}
`;

/** A single-quoted JavaScript string literal's contents. */
const quoted = (value: string) => value.replace(/\\/g, "\\\\").replace(/'/g, "\\'");

export function buildGmailScript(apiUrl: string, key: string): string {
  // Replacer functions: "$&" or "$'" in a value must never be read as a replacement pattern.
  return GMAIL_SCRIPT_TEMPLATE.replace("'__ONETALLY_URL__'", () => `'${quoted(apiUrl)}'`).replace(
    "'__ONETALLY_KEY__'",
    () => `'${quoted(key)}'`,
  );
}
