/* global self */
// OneBill's service worker. It makes the app installable and nothing more: it caches nothing,
// so every visit loads the latest version straight from the site (the app needs the internet
// anyway, for sign-in and scanning).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
