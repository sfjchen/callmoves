// Keyboard shortcut hooks: the page-wide key listener with its guards, and the on/off switch that WCAG 2.1.4
// (Character Key Shortcuts) asks for, saved in localStorage as cm_keys. The keys themselves are in lib/keymap.ts.
import { useEffect, useRef } from "react";
import { KEYMAP, type Page } from "@/lib/keymap";
import { setStored, useStored } from "@/lib/local";

const CHORD_MS = 1000;   // a chord's 2nd key must follow the 1st within this long
const REPEAT = new Set(["ArrowLeft", "ArrowRight", "Shift+ArrowLeft", "Shift+ArrowRight"]); // held seek keys repeat
const MODS = new Set(["Shift", "Control", "Alt", "Meta", "CapsLock"]);
const NON_TEXT = /^(range|checkbox|radio|button|submit|reset|file|color|image)$/;

/**
 * Returns whether single-key shortcuts are on (the default), false once the reader turned them off.
 */
export function useKeysOn(): boolean {
  return useStored("cm_keys") !== "0";
}

/**
 * Turns single-key shortcuts on or off for every tab.
 */
export function setKeysOn(on: boolean): void {
  setStored("cm_keys", on ? "1" : "0");
}

/**
 * Listens for key presses page-wide and passes each to `handle(e, key, prev)`: key is e.key with "Shift+" in front of
 * a shifted named key, prev is the key pressed within the last second ("" otherwise), which makes chords. `handle`
 * returns true only when the key did something, and then the browser's own action is cancelled, so a key that did
 * nothing (an arrow with no row to move to) keeps scrolling. Skips presses with Cmd, Ctrl or Alt held, while text is
 * composed, when another handler already used the key, in fields and sliders, a held key unless it seeks, a focused
 * slider's or media player's own arrows, Enter and Space on a focused button or link, while a modal dialog is open,
 * and everything while the shortcuts are switched off.
 */
export function useHotkeys(handle: (e: KeyboardEvent, key: string, prev: string) => boolean | void, on = true): void {
  const enabled = useKeysOn();
  const fn = useRef(handle);
  useEffect(() => {
    fn.current = handle;
  });
  useEffect(() => {
    if (!on || !enabled) return;
    let last = { key: "", at: 0 };
    const onKey = (e: KeyboardEvent) => {
      const t = e.target instanceof HTMLElement ? e.target : null;
      const key = e.shiftKey && e.key.length > 1 ? `Shift+${e.key}` : e.key;
      if (
        e.defaultPrevented || e.isComposing || e.keyCode === 229 || e.metaKey || e.ctrlKey || e.altKey ||
        MODS.has(e.key) || (e.repeat && !REPEAT.has(key)) || document.querySelector('[aria-modal="true"]')
      ) return;
      if (t) {
        const typing = t.isContentEditable || !!t.closest('[role="textbox"],[role="combobox"],[role="slider"]') ||
          t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement ||
          (t instanceof HTMLInputElement && !NON_TEXT.test(t.type));
        // a focused slider or player keeps its arrows, Home, End and Page keys, a control acts on Enter and Space
        const own = (/^(Arrow|Home$|End$|Page)/.test(e.key) &&
          ((t instanceof HTMLInputElement && t.type === "range") || t instanceof HTMLMediaElement)) ||
          ((e.key === "Enter" || e.key === " ") && !!t.closest("button,a[href],summary,audio,video,[role=button]"));
        if (typing || own) return;
      }
      const now = performance.now();
      const prev = now - last.at < CHORD_MS ? last.key : "";
      const used = fn.current(e, key, prev) === true;
      last = used && prev ? { key: "", at: 0 } : { key, at: now };
      if (used) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [on, enabled]);
}

/**
 * Runs `run(id, e.key)` for the shortcut of this page's key map that the pressed key or chord triggers. `run` returns
 * true when it did something.
 */
export function usePageKeys(page: Page, run: (id: string, key: string) => boolean | void, on = true): void {
  useHotkeys((e, key, prev) => {
    const find = (k: string) => KEYMAP[page].find((a) => !a.doc && a.keys.includes(k))?.id;
    const id = (prev && find(`${prev} ${key}`)) || find(key);
    return id ? run(id, e.key) : false;
  }, on);
}
