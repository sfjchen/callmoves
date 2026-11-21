// Keyboard control of the library page (client component, renders nothing): j / k or the down and up arrows select
// the next or previous visible call card by moving focus to it, Enter then follows the focused link, and / jumps to
// the filter field. The keys are listed in KEYMAP.library (lib/keymap.ts).
"use client";

import { usePageKeys } from "@/lib/hotkeys";

/**
 * Attaches the library's key handler. A key with nothing to do (no card past the last one) is left to the browser,
 * so the arrows still scroll the page there.
 */
export default function LibraryKeys() {
  usePageKeys("library", (id) => {
    if (id === "search") {
      const q = document.querySelector<HTMLInputElement>('input[name="q"]');
      q?.focus();
      q?.select();
      return !!q;
    }
    // cards in a collapsed sector have no boxes and are skipped
    const rows = [...document.querySelectorAll<HTMLElement>("a[data-row]")].filter((r) => r.getClientRects().length);
    const i = rows.indexOf(document.activeElement as HTMLElement);
    const to = rows[i < 0 ? 0 : i + (id === "row-next" ? 1 : -1)];
    to?.focus();
    return !!to;
  });
  return null;
}
