import { useEffect, useRef, useSyncExternalStore } from "react";

/**
 * The phone's Back button (and browser Back) stays inside the app. One extra history entry
 * sits on top while signed in: each Back pops it, we handle the press, and put it back.
 *
 * - Something open (a sheet, a split step, "All receipts"): Back closes the topmost one.
 * - On the home screen: the first Back shows "Press back again to exit"; a second one within
 *   2 seconds leaves. A single Back never leaves the app or reaches the sign-in pages.
 */

export const EXIT_WINDOW_MS = 2000;
const TRAP = { onebill: "back-trap" };

interface Layer {
  back: () => void;
}
let layers: Layer[] = [];
let lastBackAt = 0;
let hint = false;
const hintListeners = new Set<() => void>();
let hideTimer: ReturnType<typeof setTimeout> | undefined;

function setHint(show: boolean) {
  hint = show;
  clearTimeout(hideTimer);
  if (show) hideTimer = setTimeout(() => setHint(false), EXIT_WINDOW_MS);
  hintListeners.forEach((l) => l());
}

function onBack() {
  const top = layers.pop(); // re-registered by its owner if it's still open (e.g. a step back)
  if (top) {
    top.back();
    window.history.pushState(TRAP, "");
    return;
  }
  const now = Date.now();
  if (now - lastBackAt < EXIT_WINDOW_MS) {
    // Second press: let it through. (The extra entry is already gone, so this leaves.)
    lastBackAt = 0;
    setHint(false);
    window.removeEventListener("popstate", onBack);
    window.history.back();
    return;
  }
  lastBackAt = now;
  setHint(true);
  window.history.pushState(TRAP, "");
}

/** Catches Back while signed in. Returns the undo. */
export function trapBackButton(): () => void {
  if (window.history.state?.onebill !== TRAP.onebill) window.history.pushState(TRAP, "");
  window.addEventListener("popstate", onBack);
  return () => window.removeEventListener("popstate", onBack);
}

/**
 * While `open`, Back calls `back` (closing it) instead of leaving the screen. The newest open
 * thing closes first. `depth` re-registers it when it can go back more than once (split steps).
 */
export function useBackLayer(open: boolean, back: () => void, depth = 0) {
  const latest = useRef(back);
  useEffect(() => {
    latest.current = back;
  });
  useEffect(() => {
    if (!open) return;
    const layer: Layer = { back: () => latest.current() };
    layers.push(layer);
    return () => {
      layers = layers.filter((l) => l !== layer);
    };
  }, [open, depth]);
}

/** True for a moment after the first Back on the home screen. */
export function useExitHint(): boolean {
  return useSyncExternalStore(
    (listener) => {
      hintListeners.add(listener);
      return () => hintListeners.delete(listener);
    },
    () => hint,
  );
}

/** Test helper. */
export function resetBackButtonForTests() {
  layers = [];
  lastBackAt = 0;
  setHint(false);
  window.removeEventListener("popstate", onBack);
}
