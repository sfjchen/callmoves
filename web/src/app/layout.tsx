// Root layout: the page shell in the sfjc.dev notebook style, with the masthead, nav and footer shared by every
// route.
import type { Metadata } from "next";
import { Inter, Patrick_Hand } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const patrick = Patrick_Hand({ weight: "400", subsets: ["latin"], variable: "--font-patrick" });
const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "CallMoves",
  description: "Which statements on an earnings call came right before the stock moved, replayed with chart, " +
    "transcript and audio",
};

/**
 * Renders the page shell: masthead with the library, guess, themes and submit links, the page content and the
 * sfjc.dev footer.
 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`h-full antialiased ${patrick.variable} ${inter.variable}`}>
      <body className="min-h-full flex flex-col font-sans">
        <header className="border-b border-nb-border px-4 sm:px-6 py-2.5 flex items-baseline gap-4">
          <Link href="/" className="hand text-2xl text-nb-text">CallMoves</Link>
          <span className="hidden sm:inline text-xs text-nb-muted">what moved the stock during the earnings call</span>
          <nav className="ml-auto flex flex-wrap justify-end gap-x-3 gap-y-0.5 text-sm">
            {[["/guess", "Guess the move"], ["/themes", "Themes"], ["/submit", "Submit a call"]].map(([href, label]) => (
              <Link key={href} href={href} className="text-nb-accent hover:underline underline-offset-2">{label}</Link>
            ))}
          </nav>
        </header>
        <main className="flex-1 px-4 sm:px-6 py-5">{children}</main>
        <footer className="py-6 text-center text-sm text-nb-muted">
          Made by Jonathan Chen ·{" "}
          <a href="https://sfjc.dev/about" className="text-nb-accent hover:underline underline-offset-2">About</a>
        </footer>
      </body>
    </html>
  );
}
