---
name: poly-bet-plan
description: Force-output a trade ticket for ONE Polymarket soccer game given a budget (default $100, ALWAYS fully deployed) by MIRRORING the top smart wallet. Crawls top holders for discovery, scores wallets by lb-api all-time PnL, pulls each candidate's ENTIRE position in the game before judging it (MM/hedge check on the full book), then scales the best clean sharp's net book to the budget. Output is a leg table (注/价/投入/份数/命中回款, medal-ranked) followed by reasoning paragraphs, after an explicit self-rebuttal. Never ends with "it depends". Outputs 2 charts - implied score distribution + ticket PnL per final score vs the sharp aggregate. Trigger on - bet plan, trade ticket, 下注计划, 交易确认单, 这场怎么下, $100 怎么分, 帮我分配仓位, how should I bet this game, allocate my budget, best play on this match, 跟单这场, mirror the sharps.
---

# poly-bet-plan

Mirror the top smart wallet in one game. Data script + self-rebuttal + ticket.

## Step 1 — run the script

```bash
python3 ~/.claude/skills/poly-bet-plan/plan.py <slug|gameId|team search> \
    [--budget 100] [--out DIR] [--json FILE] [--wallet 0x..]
```

Pipeline inside:
1. **discover** — top holders of the game's core markets = candidate pool
2. **score** — lb-api all-time PnL/volume per wallet (SHARP / win+ / FISH / MM)
3. **full book** — pulls each ranked candidate's ENTIRE position in the game
   (every market incl. exact score, with avg entry + running PnL). All
   judgements — MM/hedge structure, mirror-worthiness — run on the FULL book,
   never on the partial top-holder view.
4. **mirror** — top clean (non-inventory) sharp by all-time PnL becomes the
   target; its NET book is scaled to the budget, $-value weighted, min leg
   $10, rounded to $5, **full budget deployed** (no kelly, no reserve).
   `--wallet` overrides the target.
5. **execute** — CLOB entry band + slippage per leg, printed next to the
   target's own average entry.

The script prints the ready-to-paste leg table, the event link, per-leg
execution lines, scenario PnL, and the two charts.

## Step 2 — self-rebuttal (MANDATORY before outputting the ticket)

Argue against the mirror before finalizing. Three fixed questions, answered
with data, verdict stated explicitly:

1. **Is the target actually sharp — or MM/LP/hot-streak?** Check margin %,
   the full-book shape (inventory flags, all-No structures), and whether the
   game book tells one coherent story or is exhaust from market-making.
2. **Is the entry still there?** Compare the target's avg entry vs our fill
   price per leg. Mirroring at a much worse price than the target's entry can
   erase the whole edge — if a leg has moved hard in the target's favor, say
   so and consider dropping it or shrinking it.
3. **Can the budget actually fill inside the entry band?** Book depth,
   slippage, THIN/MODERATE/THICK per leg.

If the mirror dies here (target is MM, entries all gone, books unfillable),
pick the next clean sharp — or name the closest-to-alpha candidate, its risk,
and the precise trigger that would make it bettable. NEVER end with "depends".

## Step 3 — output the ticket

(a) The leg table, EXACTLY this format (the script prints it ready-to-paste;
legs ranked by 投入, medals 🥇🥈🥉; 份数 = 投入/价; 命中回款 = 份数 × $1;
投入 must sum to the FULL budget — no cash reserve):

| 注 | 价 | 投入 | 份数 | 命中回款 |
|---|---|---|---|---|
| 🥇 全场 Under 2.5 | 43.4¢ | **$60** | 138.3 | **$138.30** |
| 🥈 Spain -1.5 (Spain) | 48.6¢ | **$25** | 51.4 | **$51.40** |
| 🥉 Spain -3.5 (Austria +3.5) | 88.6¢ | **$15** | 16.9 | **$16.90** |

Use short Chinese-friendly leg names (全场 Under 2.5, Spain -1.5), not full
market questions. Event link (polymarket.com/event/...) right below the table.

(b) Then the reasoning as flowing PARAGRAPHS (no field table, no numbered
field list), weaving in: who the mirror target is (name, all-time PnL, margin,
their exact game book with entries), why their book is credible, entry band
per leg + "cancel above X", stop-loss / exit triggers (price OR in-play event,
e.g. "Spain 2nd goal before 60'"), the main risks (including how far our fills
sit above the target's entries), and precise pre-match abort conditions.
3-5 paragraphs total.

## Caveats

- Read-only: gamma / data-api / clob / lb-api. No execution, no keys.
- A wallet's legs the holders crawl shows are a SAMPLE; only the /positions
  full book counts as evidence about the wallet.
- Half-time / first-to-score legs can't be projected on the FT state space;
  they still mirror fine, they just don't appear in the PnL grid.
- Closed games abort with a message. Live games: prices move faster than the scan.
