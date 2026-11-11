// Root layout: the page shell with the header links, shared by every route.
import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "CallMoves",
  description: "Earnings call market-reaction dashboard",
};

/**
 * Renders the page shell: header with the library, study and submit links around the page content.
 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-white text-neutral-900">
        <header className="border-b border-neutral-200 px-6 py-3 flex items-baseline gap-4">
          <Link href="/" className="font-semibold text-lg">CallMoves</Link>
          <Link href="/study" className="ml-auto text-xs text-neutral-400 hover:text-neutral-600">Study</Link>
          <Link href="/submit" className="text-sm text-blue-700">Submit a call</Link>
        </header>
        <main className="flex-1 px-6 py-5">{children}</main>
      </body>
    </html>
  );
}
