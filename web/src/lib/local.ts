// Browser-side settings kept in localStorage (the shortcut switch cm_keys, the playback speed cm_rate, the saved
// position cm_pos_<id>). Reads and writes never throw: where storage is blocked (private windows, site data off) a
// value lives in memory until the page reloads. useStored reads through useSyncExternalStore, so the server render
// and the first client render agree and the stored value arrives right after hydration.
import { useSyncExternalStore } from "react";

const mem = new Map<string, string>();
const subs = new Set<() => void>();

/**
 * Returns the stored string for key, or null when nothing is stored or storage is blocked.
 */
export function getStored(key: string): string | null {
  try {
    return localStorage.getItem(key) ?? mem.get(key) ?? null;
  } catch {
    return mem.get(key) ?? null;
  }
}

/**
 * Stores a string under key and tells every useStored reader in this tab. Other tabs hear it through the storage event.
 */
export function setStored(key: string, value: string): void {
  mem.set(key, value);
  try {
    localStorage.setItem(key, value);
  } catch { /* blocked storage keeps the in-memory copy */ }
  subs.forEach((f) => f());
}

/**
 * Subscribes to changes made in this tab or, through the storage event, in another one, and returns the unsubscribe.
 */
function subscribe(f: () => void): () => void {
  subs.add(f);
  window.addEventListener("storage", f);
  return () => {
    subs.delete(f);
    window.removeEventListener("storage", f);
  };
}

/**
 * Returns the stored string for key, null on the server, on the first client render and when nothing is stored.
 */
export function useStored(key: string): string | null {
  return useSyncExternalStore(subscribe, () => getStored(key), () => null);
}
