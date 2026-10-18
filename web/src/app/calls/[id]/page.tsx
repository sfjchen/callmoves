import { notFound } from "next/navigation";
import { getCall } from "@/lib/data";
import Replay from "@/components/replay";

export const dynamic = "force-dynamic";


export default async function CallPage({ params }: PageProps<"/calls/[id]">) {
  const { id } = await params;
  const call = await getCall(id);
  if (!call) notFound();
  return <Replay data={call} />;
}
