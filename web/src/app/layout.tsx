// Root layout: the page shell with the masthead, nav and footer shared by every route.
import type { Metadata } from "next";
import { Source_Sans_3 } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const source = Source_Sans_3({ subsets: ["latin"], variable: "--font-source" });
const NAV = "text-sm text-white/90 hover:text-white hover:underline underline-offset-2";

export const metadata: Metadata = {
  // preview image URLs are relative, so crawlers on any deploy resolve them against the production domain
  metadataBase: new URL("https://callmoves.sfjc.dev"),
  title: "CallMoves",
  description: "Which statements on an earnings call came right before the stock moved, replayed with chart, " +
    "transcript and audio",
};

/**
 * Renders the page shell: masthead with the library, guess and submit links, the page content and the sfjc.dev footer.
 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`h-full antialiased ${source.variable}`}>
      <body className="min-h-full flex flex-col font-sans">
        <header className="bg-brand text-white px-4 sm:px-6 py-2.5 flex items-baseline gap-4">
          <Link href="/" className="text-xl font-semibold tracking-tight">CallMoves</Link>
          <span className="hidden sm:inline text-sm text-white/80">what moved the stock during the earnings call</span>
          <a href="/guess" className={`ml-auto ${NAV}`}>
            Guess the move</a>
          <Link href="/submit" className={NAV}>
            Submit a call</Link>
        </header>
        <main className="flex-1 px-4 sm:px-6 py-5">{children}</main>
        <footer className="border-t border-rule bg-surface py-5 text-center text-sm text-muted">
          Made by Jonathan Chen ·{" "}
          <a href="https://sfjc.dev/about" className="text-accent hover:underline underline-offset-2">About</a>
        </footer>
      </body>
    </html>
  );
}
