// Keyboard shortcuts help (client component), mounted once in the footer of the root layout: the "Keyboard shortcuts"
// button and the dialog that ? also opens. The dialog lists the keys of the page being viewed from the shared KEYMAP in
// lib/keymap.ts and holds the switch that turns single-key shortcuts off. Esc closes it and focus goes back to where
// it was.
"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { setKeysOn, useKeysOn, usePageKeys } from "@/lib/hotkeys";
import { ak, type Act, KEYMAP, type Page } from "@/lib/keymap";

const SYM: Record<string, string> = {
  ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", " ": "Space", Escape: "Esc",
};
const TITLE: Record<Page, string> = {
  all: "Everywhere", library: "Library", replay: "Call replay", label: "Labeling",
};

/**
 * Renders the keys of 1 shortcut as keycaps: alternatives split by a slash, the parts of a chord by "then", a shifted
 * key as Shift+key.
 */
function Keys({ a }: { a: Act }) {
  return (
    <>
      {(a.label ?? a.keys).map((k, i) => (
        <span key={k} className="whitespace-nowrap">
          {i > 0 && " / "}
          {(k === " " ? [k] : k.split(" ")).map((part, j) => (
            <span key={j}>
              {j > 0 && " then "}
              {(part.startsWith("Shift+") ? ["Shift", part.slice(6)] : [part]).map((t, n) => (
                <span key={n}>{n > 0 && "+"}<kbd>{SYM[t] ?? t}</kbd></span>
              ))}
            </span>
          ))}
        </span>
      ))}
    </>
  );
}

/**
 * Renders the footer button and, while open, the shortcuts dialog for the current page.
 */
export default function Shortcuts() {
  const path = usePathname();
  const page: Page | null = path === "/" ? "library" : path.startsWith("/calls/") ? "replay" :
    path === "/label" ? "label" : null;
  const on = useKeysOn();
  const [open, setOpen] = useState(false);
  const back = useRef<HTMLElement | null>(null);
  const box = useRef<HTMLDivElement>(null);

  // Opens the dialog and remembers the element that had focus, which gets it back on close.
  function show(from: HTMLElement | null) {
    back.current = from;
    setOpen(true);
  }

  // Closes the dialog and gives focus back.
  const hide = useCallback(() => {
    setOpen(false);
    back.current?.focus();
  }, []);

  usePageKeys("all", () => {
    show(document.activeElement instanceof HTMLElement ? document.activeElement : null);
    return true;
  });

  // While open: focuses the dialog, closes on Esc and keeps Tab inside it.
  useEffect(() => {
    if (!open) return;
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        hide();
      } else if (e.key === "Tab" && box.current) {
        const f = [...box.current.querySelectorAll<HTMLElement>("button,input")];
        const to = e.shiftKey && [f[0], box.current].includes(document.activeElement as HTMLElement) ? f.at(-1) :
          !e.shiftKey && document.activeElement === f.at(-1) ? f[0] : null;
        if (to) {
          e.preventDefault();
          to.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, hide]);

  return (
    <>
      <span className="hidden sm:inline">
        {" · "}
        <button
          type="button" onClick={(e) => show(e.currentTarget)} aria-haspopup="dialog" aria-keyshortcuts={ak("help")}
          className="text-accent hover:underline underline-offset-2"
        >
          Keyboard shortcuts</button>
      </span>
      {open && (
        <div className="fixed inset-0 z-[80] flex items-start justify-center overflow-y-auto bg-ink/40 px-4 py-[8vh]"
          onClick={hide}>
          <div ref={box} role="dialog" aria-modal="true" aria-labelledby="keys-title" tabIndex={-1}
            onClick={(e) => e.stopPropagation()} className="card w-full max-w-lg p-4 text-left text-sm outline-none">
            <div className="flex items-baseline justify-between">
              <h2 id="keys-title" className="text-lg">Keyboard shortcuts</h2>
              <button type="button" onClick={hide} className="tap text-accent hover:underline underline-offset-2">
                Close</button>
            </div>
            <label className="tap mt-2 flex items-center gap-2">
              <input type="checkbox" checked={on} onChange={(e) => setKeysOn(e.target.checked)} />
              Single-key shortcuts
            </label>
            <p className="mb-2 text-xs text-muted">
              {on ? "Turn them off if single keys clash with speech input or assistive technology." :
                "Off: no key does anything until you turn this on."}
            </p>
            {[...(page ? [page] : []), "all" as const].map((p) => (
              <section key={p} className="mt-3" aria-label={TITLE[p]}>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{TITLE[p]}</h3>
                <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] items-baseline gap-x-4 gap-y-1">
                  {KEYMAP[p].map((a) => (
                    <div key={a.id} className="contents">
                      <dt><Keys a={a} /></dt>
                      <dd>{a.text}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
