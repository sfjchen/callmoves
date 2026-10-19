import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "CallMoves",
  description: "Earnings call market-reaction dashboard",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-white text-neutral-900">
        <header className="border-b border-neutral-200 px-6 py-3 flex items-baseline gap-4">
          <Link href="/" className="font-semibold text-lg">CallMoves</Link>
          <span className="text-sm text-neutral-500">which statements the market reacted to</span>
        </header>
        <main className="flex-1 px-6 py-5">{children}</main>
      </body>
    </html>
  );
}
