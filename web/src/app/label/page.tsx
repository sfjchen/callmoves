// Hidden blind labeling page (/label?k=<LABEL_KEY>, not in the nav): 1 flagged price event at a time with its price
// path and the 5 minutes of transcript before it, without anything CallMoves ranked or scored.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import LabelView from "@/components/label-view";
import { FLAGS, keyOk, labeledIds, queueOrder } from "@/lib/labels";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Label · CallMoves", robots: { index: false, follow: false } };

/**
 * Renders the next unlabeled flag in queue order with the user's progress, or a done note, and 404s without the key.
 */
export default async function LabelPage({ searchParams }: PageProps<"/label">) {
  const k = (await searchParams).k;
  if (!keyOk(k)) notFound();
  const done = await labeledIds();
  const todo = FLAGS.filter((f) => !done.has(f.id)).sort((a, b) => queueOrder(a.id) - queueOrder(b.id));
  if (todo.length === 0) {
    return <p className="mx-auto max-w-3xl">All {FLAGS.length} flags are labeled. Thank you.</p>;
  }
  return <LabelView key={todo[0].id} flag={todo[0]} k={String(k)} done={done.size} total={FLAGS.length} />;
}
