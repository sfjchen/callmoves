# Attribution labeling protocol

How a flagged price event gets its label. I wrote it the way I read calls next to a price chart, as a finance
student who has watched analysts replay calls. The `agent-protocol` rater follows it step by step, and the blind
Opus rater and I (rater `user` on `/label`) answer the same question with less context.

## Unit

1 flag = 1 price event from `labels/flags.json`: flagged minutes no more than 2 minutes apart, starting during the
call, flagged against the sector ETF (z-score of the 1-minute adjusted return over the trailing 20 minutes, or 3x
volume with a 0.2% move). The set is frozen, so a later change of benchmark or scoring never moves a label.

## What the rater sees

| Rater | Price | Transcript | Other |
| --- | --- | --- | --- |
| `user` (`/label`) | stock and ETF path 15 min before to 10 min after, volume | 5 min before the event to the end of its first minute | nothing |
| `opus-blind` | same as `user`, as a table | same | nothing |
| `agent-protocol` | stock, ETF, SPY, peer index, every context symbol over the event | 10 min before | release, prior quarter, consensus, squawk headlines within 15 min |

Nobody sees CallMoves' moments, scores, novelty or ranking.

## Steps

1. **Company or market.** Compare the stock with its ETF, SPY and peers over the event minutes. If the ETF or peers
   moved the same way by at least half the stock's move, the label is `market`, unless a statement clearly broke
   the stock away from them.
2. **Timing.** Find where the move starts on the price path. A sentence spoken after the move began cannot be its
   cause. If the move began before every candidate sentence, consider `release` (the call opens minutes after a
   release the market is still pricing) or `other`.
3. **News value.** Among sentences before the move, prefer new, specific, checkable information: a guidance change,
   a number not in the release, a figure off consensus, a surprising answer in Q&A, a clear change of tone. Routine
   remarks (welcome, safe harbor, thanks, restating the release) are not causes.
4. **Headlines.** If a squawk headline repeats a sentence within 2 minutes of the event, that sentence is the label,
   with confidence 3, unless step 1 says `market`.
5. **Answer.** 1 sentence number (`statement`), or `none` (nothing stands out), `release`, `market` or `other`, with
   confidence 1 (guess), 2 (plausible) or 3 (clear) and 1 line of reason.

## Tiers, reported apart

- **gold**: a timestamped headline (squawk account on X or the CNBC live blog) posted within 2 minutes of the event
  shares at least half its content words with 1 sentence of the 5-minute window (`labels.gold_labels`).
- **silver**: `opus-blind` and `agent-protocol` give the same answer (same kind, or sentences within 30 s).
- **human**: my own `/label` answers. I use them to check the agent on a sample, not as ground truth for tuning.

These are model labels except the human tier, and the report says so. Precision at 3 and 5 is computed per tier,
Cohen's kappa per rater pair, never pooled. Any weight tuning splits by call (train/test) and reports the test
calls only.
