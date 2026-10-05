/**
 * Installing OneBill as an app. Chrome, Edge, and Samsung Internet (Android and desktop) offer
 * a real install prompt, which a button can trigger, but only once they decide to (Android
 * Chrome waits for a tap and ~30 seconds on the site, and holds back after a dismissal).
 * Until then, Android gets the browser-menu steps. Safari on iPhone/iPad never offers it:
 * there, people add it from the Share menu. Other browsers can't install web apps.
 */

/** The browser's install prompt (Chromium only; not in TypeScript's DOM types). */
export interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export type InstallOption = "prompt" | "ios" | "android" | null;

type Listener = () => void;
let deferred: InstallPromptEvent | null = null;
let installed = false;
const listeners = new Set<Listener>();
const notify = () => listeners.forEach((l) => l());

/** Starts listening. Call once, before rendering: the prompt event can come early. */
export function listenForInstall(win: Window = window) {
  win.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault(); // keep it for our button instead of the browser's mini-bar
    deferred = event as InstallPromptEvent;
    notify();
  });
  win.addEventListener("appinstalled", () => {
    deferred = null;
    installed = true;
    notify();
  });
}

/** Opened from the home-screen icon, not in a browser tab. */
export function isStandalone(win: Window = window): boolean {
  return (
    win.matchMedia?.("(display-mode: standalone)").matches ||
    (win.navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

/** iPhone or iPad Safari (iPads report a Mac, so touch tells them apart). */
export function isIos(nav: Navigator = navigator): boolean {
  return /iPhone|iPad|iPod/.test(nav.userAgent) || (/Macintosh/.test(nav.userAgent) && nav.maxTouchPoints > 1);
}

export function isAndroid(nav: Navigator = navigator): boolean {
  return /Android/.test(nav.userAgent);
}

/** What the install button can do here, if anything. */
export function installOption(win: Window = window): InstallOption {
  if (installed || isStandalone(win)) return null;
  if (deferred) return "prompt";
  if (isIos(win.navigator)) return "ios";
  if (isAndroid(win.navigator)) return "android";
  return null;
}

/** Shows the browser's install dialog. True if the person installed it. */
export async function promptInstall(): Promise<boolean> {
  const event = deferred;
  if (!event) return false;
  deferred = null; // a prompt can only be used once
  await event.prompt();
  const { outcome } = await event.userChoice;
  if (outcome === "accepted") installed = true;
  notify();
  return outcome === "accepted";
}

export function onInstallChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Registers the service worker that makes the app installable (production builds only). */
export function registerServiceWorker(nav: Navigator = navigator) {
  if (!import.meta.env.PROD || !("serviceWorker" in nav)) return;
  window.addEventListener("load", () => {
    nav.serviceWorker.register("/sw.js").catch(() => {
      // Not installable then, but the site works the same.
    });
  });
}

/** Test helper: forget any captured prompt. */
export function resetInstallForTests() {
  deferred = null;
  installed = false;
  listeners.clear();
}
