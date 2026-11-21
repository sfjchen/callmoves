// The keyboard shortcut map shared by the key handlers (lib/hotkeys.ts) and the help dialog (components/shortcuts.tsx),
// so the list a reader sees is the list the code runs. Plain data and pure functions, safe to import anywhere.

/** One shortcut: its id, the e.key values that trigger it ("Shift+ArrowLeft", "g l" for a chord) and what it does. */
export type Act = { id: string; keys: string[]; text: string; label?: string[]; doc?: boolean };
export type Page = "all" | "library" | "replay" | "label";

// `label` replaces the shown keys, `doc` marks a key an element handles itself (listed in the help, never dispatched).
export const KEYMAP: Record<Page, Act[]> = {
  all: [
    { id: "help", keys: ["?"], text: "Show or hide this list" },
    { id: "close", keys: ["Escape"], text: "Close this list", doc: true },
  ],
  library: [
    { id: "row-next", keys: ["j", "ArrowDown"], text: "Select the next call" },
    { id: "row-prev", keys: ["k", "ArrowUp"], text: "Select the previous call" },
    { id: "open", keys: ["Enter"], text: "Open the selected call", doc: true },
    { id: "search", keys: ["/"], text: "Filter the calls" },
  ],
  replay: [
    { id: "moment-next", keys: ["j"], text: "Next moment" },
    { id: "moment-prev", keys: ["k"], text: "Previous moment" },
    { id: "rank", keys: ["1", "2", "3", "4", "5"], label: ["1-5"], text: "Jump to the moment with that rank" },
    { id: "turn-prev", keys: ["["], text: "Previous turn" },
    { id: "turn-next", keys: ["]"], text: "Next turn" },
    { id: "play", keys: [" ", "p"], text: "Play or pause" },
    { id: "back", keys: ["ArrowLeft"], text: "Back 5 s" },
    { id: "fwd", keys: ["ArrowRight"], text: "Forward 5 s" },
    { id: "back30", keys: ["Shift+ArrowLeft"], text: "Back 30 s" },
    { id: "fwd30", keys: ["Shift+ArrowRight"], text: "Forward 30 s" },
    { id: "slower", keys: ["<"], text: "Slower, down to 0.5x" },
    { id: "faster", keys: [">"], text: "Faster, up to 2x" },
    { id: "search", keys: ["/"], text: "Search the transcript" },
    { id: "search-esc", keys: ["Escape"], text: "In the search box: clear it, then leave it", doc: true },
    { id: "hit-next", keys: ["n"], text: "Next search match" },
    { id: "hit-prev", keys: ["N"], text: "Previous search match" },
    { id: "mute", keys: ["m"], text: "Mute or unmute" },
    { id: "follow", keys: ["f"], text: "Make the transcript follow the audio, or stop" },
    { id: "copy", keys: ["c"], text: "Copy the link to the current moment" },
    { id: "library", keys: ["g l"], text: "Go to the library" },
  ],
  label: [
    { id: "none", keys: ["n"], text: "None stands out" },
    { id: "release", keys: ["r"], text: "Still digesting the release" },
    { id: "market", keys: ["m"], text: "Market or sector moved too" },
    { id: "other", keys: ["o"], text: "Other" },
    { id: "conf", keys: ["1", "2", "3"], label: ["1-3"], text: "Confidence: guess, plausible, clear" },
  ],
};

/**
 * Returns the aria-keyshortcuts value for the shortcut with this id, undefined when it has none a screen reader
 * could use (a chord). Letters typed with Shift read as Shift+letter.
 */
export function ak(id: string): string | undefined {
  const a = Object.values(KEYMAP).flat().find((x) => x.id === id);
  const ks = a?.keys.filter((k) => k === " " || !k.includes(" "))
    .map((k) => (k === " " ? "Space" : /^[A-Z]$/.test(k) ? `Shift+${k}` : k));
  return ks?.length ? ks.join(" ") : undefined;
}
