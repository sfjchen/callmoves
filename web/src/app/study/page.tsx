// Study page: explains the timed task and hands each visitor 2 trials, 1 per condition, on 2 different real calls.
import StudyHome from "@/components/study-home";
import { studyCalls } from "@/lib/study";

export const dynamic = "force-dynamic";

/**
 * Renders the task description and the trial list for this browser.
 */
export default async function Study() {
  const ids = await studyCalls();
  return (
    <div className="max-w-xl text-sm">
      <h1 className="text-xl font-semibold mb-1">Time to insight study</h1>
      <p className="text-neutral-600 mb-4">
        Each trial opens an earnings call replay. Find when the stock&apos;s biggest 5-minute move during the call began
        (clock time, ET) and the statement said just before it. The timer runs from Start to Submit. 2 trials, about 5
        minutes in all.
      </p>
      {ids.length < 2 ? <p className="text-neutral-500">The study needs at least 2 calls in the library.</p>
        : <StudyHome ids={ids} />}
    </div>
  );
}
